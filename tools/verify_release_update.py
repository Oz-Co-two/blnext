"""公開した後の確かめ: GitHub の最新リリースへ、前の版からアプリと同じ仕組みで更新できるか（2026-11-09 に v1.0.0 → v1.0.1 で初めて確かめた）。

  .venv-build/Scripts/python.exe tools/verify_release_update.py               前の版＝public の1つ前のタグ
  .venv-build/Scripts/python.exe tools/verify_release_update.py --from v1.0.0

- 一時フォルダに前の版の配布 zip（GitHub のリリースから取る）を展開し、そこから check → ダウンロード・照合・展開 → 新しい版の exe の
  --apply-update（起動し直しだけ --no-launch で止める＝窓を出さない）→ startup の片付け、まで本物で通す。普段使いのフォルダには触らない。
- 公開した直後の1分ほどは、GitHub の latest がまだ前の版を指す（待てば直る）ので、新しい版になるまで待つ。
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
os.environ.pop('BLN_NO_NET', None)
sys.path.insert(0, str(ROOT))
from core import app_update as au  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='prev')
    a = ap.parse_args(argv)
    new = au.read_app_version(str(ROOT))
    prev = a.prev or subprocess.run(['git', '-C', str(ROOT), 'describe', '--tags', '--abbrev=0', 'public^'],
                                    capture_output=True, text=True).stdout.strip()
    if not au.vtuple(prev) or not au.vtuple(new) or au.vtuple(prev) >= au.vtuple(new):
        print(f'前の版（{prev!r}）と今の版（{new!r}）が読めないか、順が逆です', file=sys.stderr)
        return 1
    ok = True

    def say(cond, text):
        nonlocal ok
        ok = ok and bool(cond)
        print(('  OK  ' if cond else '  NG  ') + text)

    for _ in range(20):   # 公開した直後は latest が前の版を指す
        m = au.check_manifest(au._get(au.UPDATE_URL, 1024 * 1024))
        if m['version'] == new:
            break
        print(f'GitHub の latest はまだ {m["version"]}（待つ）')
        time.sleep(15)
    say(m['version'] == new and not m['manual'], f'latest の update.json は {m["version"]}・自動更新できる形')
    data = au._get(au.ZIP_URL.format(tag=new, name=m['zip']['name']), au.MAX_ZIP)
    say(hashlib.sha256(data).hexdigest() == m['zip']['sha256'], 'zip のハッシュが update.json と一致')
    local = ROOT / 'dist' / 'release' / m['zip']['name']
    if local.is_file():
        say(local.read_bytes() == data, '手元で作った zip と同じ中身')

    with tempfile.TemporaryDirectory(prefix='bln-update-check-') as td:
        zpath = Path(td) / f'{au.ZIP_TOP}-{prev}.zip'
        zpath.write_bytes(au._get(au.ZIP_URL.format(tag=prev, name=zpath.name), au.MAX_ZIP))
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(Path(td) / 'x')
        app = Path(td) / 'x' / au.ZIP_TOP
        (app / 'data').mkdir()
        (app / 'data' / 'mine.txt').write_text('利用者のデータ', encoding='utf-8')
        u = au.Updater(str(app / '_internal'), str(app), True)
        c = u.check()
        say(c['current'] == prev and c['newer'] and c['auto'] == 'ok', f'{prev} から見て {c["latest"]} を新しい版として知らせる（自動更新できる）')
        u.start_download()
        end = time.time() + 300
        while u.job['state'] not in ('ready', 'error', 'cancel') and time.time() < end:
            time.sleep(0.5)
        say(u.job['state'] == 'ready', 'ダウンロード・照合・展開（%s）' % (u.job.get('error') or u.job['state']))
        if u.job['state'] == 'ready':
            exe = app / au.UPDATE_DIR / 'new' / au.ZIP_TOP / au.EXE_NAME
            p = subprocess.run([str(exe), '--apply-update', '--target', str(app), '--pid', '0', '--lang', 'ja', '--no-launch'],
                               cwd=str(exe.parent), timeout=180)
            say(p.returncode == 0 and au.read_app_version(str(app / '_internal')) == new, f'新しい版の exe が差し替えた（今の版 {au.read_app_version(str(app / "_internal"))}）')
            say((app / f'{au.PREV_PREFIX}{prev}').is_dir(), '前の版を退避した')
            say((app / 'data' / 'mine.txt').read_text(encoding='utf-8') == '利用者のデータ', '利用者のデータはそのまま')
            u2 = au.Updater(str(app / '_internal'), str(app), True)
            au.startup(u2)
            for _ in range(20):
                if not (app / au.UPDATE_DIR).exists():
                    break
                time.sleep(0.5)
            say(u2.just_updated == {'from': prev, 'to': new}, '起動し直した後に「更新しました」を出せる')
            say(not (app / au.UPDATE_DIR).exists(), '作業フォルダ（_update）を片付けた')
        time.sleep(1)   # 差し替えた exe が一時フォルダを放すのを待つ
    print('すべて通りました' if ok else '通らない所があります（上の NG）')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
