"""GitHub のリリースに添付する 2 つのファイルを作る: 配布 zip と update.json（アプリ内の更新が読む）。NLM の make_release.py を流用した。

使い方:  .venv-build/Scripts/python.exe tools/make_release.py        （先に tools/build_and_deploy.py でビルドしておく）
出力:    dist/release/blnext-<版>.zip と dist/release/update.json

- 版は web/js/version.js の APP_VERSION。ビルド結果（dist/blnext）の版・CHANGELOG.md の先頭の版と一致しないと止まる。
- zip に入れるのは、更新で差し替える物（core/app_update.py の REPLACE_ITEMS）だけ。ビルド結果に揃っていないと止まる
  （dist/blnext の exe を起動してできた config/ data/ などの個人のデータを入れないように、フォルダごとは入れない）。
- update.json の更新内容は CHANGELOG.md の「## v〇.〇.〇」ごとの、行頭の太字（- **〜**）。
- できた zip をアプリと同じ検査（形式・展開・版の確認）に通してから終わる。
- 最初に、公開の直前だけ流す特別なテスト（tools/run_tests.py release＝tests/release。アプリの中の更新の失敗の分岐）を流し、
  落ちたら zip を作らずに止まる（公開しないで直す。2026-11-09 に利用者と決めた）。単体テストから main() を呼ぶ時は流さない。
- 公開はこの後に手で行う（最後に出すコマンド。GitHub への公開は利用者の指示を得てから）。update.json を添付し忘れると、
  旧版のアプリは「最新リリースに update.json が無い」ので更新を知らせられない。
"""
import hashlib, json, re, sys, tempfile, zipfile
from pathlib import Path

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import app_update  # noqa: E402

DIST = ROOT / 'dist' / 'blnext'
OUT = ROOT / 'dist' / 'release'
CHANGELOG = ROOT / 'CHANGELOG.md'


def fail(msg):
    print(f'エラー: {msg}', file=sys.stderr)
    sys.exit(1)


def headline(line):
    """'- **見出し**（補足）' → '見出し'。リンク・コードの記号は外す"""
    m = re.match(r'- \*\*(.+?)\*\*', line)
    if not m:
        return None
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', m.group(1))
    return s.replace('`', '').strip()


def parse_changelog(path):
    """[(版, [見出し…]), …]（書かれた順＝新しい順）"""
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        m = re.fullmatch(r'## (v\d+\.\d+\.\d+)\s*', line)
        if m:
            out.append((m.group(1), []))
        elif out and (h := headline(line)):
            out[-1][1].append(h)
    if not out:
        fail(f'{path.name} に「## v〇.〇.〇」の見出しがありません')
    return out


def build_notes(ver):
    log = parse_changelog(CHANGELOG)
    if log[0][0] != ver:
        fail(f'CHANGELOG の先頭の版（{log[0][0]}）が APP_VERSION（{ver}）と違います')
    for v, heads in log:
        if not heads:
            fail(f'{v}: 太字の見出し（- **〜**）がありません')
    return [{'version': v, 'ja': heads, 'en': []} for v, heads in log]   # 英語の訳を足す時は en も埋める


def make_zip(ver):
    zpath = OUT / f'{app_update.ZIP_TOP}-{ver}.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in app_update.REPLACE_ITEMS:
            p = DIST / name
            for f in sorted(p.rglob('*')) if p.is_dir() else [p]:
                if f.is_file():
                    zf.write(f, f'{app_update.ZIP_TOP}/' + f.relative_to(DIST).as_posix())
    return zpath


def main():
    ver = app_update.read_app_version(str(ROOT))
    if not app_update.vtuple(ver):
        fail(f'APP_VERSION（{ver!r}）が v〇.〇.〇 の形ではありません')
    if not (DIST / app_update.EXE_NAME).is_file():
        fail(f'{DIST} にビルド結果がありません（先に tools/build_and_deploy.py）')
    built = app_update.read_app_version(str(DIST / '_internal'))
    if built != ver:
        fail(f'ビルド結果の版（{built}）が APP_VERSION（{ver}）と違います。ビルドし直してください')
    missing = [n for n in app_update.REPLACE_ITEMS if not (DIST / n).exists()]
    if missing:   # 案内文・ライセンスは blnext.spec がビルドの最後に写す
        fail(f'{DIST} に {", ".join(missing)} がありません。blnext.spec でビルドし直してください')
    notes = build_notes(ver)
    OUT.mkdir(parents=True, exist_ok=True)
    for p in [*OUT.glob('*.zip'), OUT / 'update.json']:   # 前回の分だけ消す（同じフォルダに置いたリリースノートは残す）
        p.unlink(missing_ok=True)
    zpath = make_zip(ver)
    data = zpath.read_bytes()
    manifest = {'name': app_update.APP_NAME, 'format': app_update.FORMAT, 'version': ver,
                'zip': {'name': zpath.name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)},
                'notes': notes}
    upath = OUT / 'update.json'
    upath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    # アプリと同じ検査に通す
    m = app_update.check_manifest(upath.read_bytes())
    if m['manual'] or m['zip']['size'] != len(data):
        fail('作った update.json をアプリが自動更新に使えません')
    with tempfile.TemporaryDirectory() as td:
        try:
            app_update.extract_release(str(zpath), str(Path(td) / 'new'), ver)
        except app_update.UpdateError as e:
            fail(f'作った zip をアプリが展開できません（{e.code} {e.detail}）')
    print(f'作成しました（{ver}）:')
    print(f'  {zpath.relative_to(ROOT)}  {len(data) / 1048576:.1f}MB  sha256={manifest["zip"]["sha256"]}')
    print(f'  {upath.relative_to(ROOT)}  更新内容 {len(notes)} 版分')
    print('公開する時は、zip と update.json の2つをリリースに添付する:')
    files = ' '.join(f'"{p.relative_to(ROOT).as_posix()}"' for p in [zpath, upath])
    print(f'  gh release create {ver} {files} --verify-tag --latest --notes-file <ノート>')


def release_tests():
    """公開の直前だけ流す特別なテスト。落ちたら止まる"""
    import subprocess
    print('公開の前のテスト（tools/run_tests.py release）を流しています…', flush=True)
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'run_tests.py'), 'release'], cwd=ROOT)
    if r.returncode != 0:
        fail('公開の前のテストが通りません。公開しないで直してください（詳細は test-out/last.txt）')


if __name__ == '__main__':
    release_tests()
    main()
