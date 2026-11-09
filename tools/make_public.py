"""公開版（GitHub に出す物）のコミットを、手元の public ブランチに作る。NLM と同じ分け方: NAS の master は全部の開発の履歴、
public ブランチは公開した版ごとに1コミットの積み重ね（GitHub の master には public を push する）。

  .venv-build/Scripts/python.exe tools/make_public.py           「<版> 公開」のコミットとタグ <版> を public に作る
  .venv-build/Scripts/python.exe tools/make_public.py --check   作らずに、外す物と個人の情報の検査だけ

- 中身は master のコミットの中身から EXCLUDE（notes/＝作者の調べ・プレイの記録、CLAUDE.local.md＝その場所と作者の数字）を外した物。
  作業中のフォルダ・ブランチ・index には触らない（別の index で組み立てる）。
- 個人の情報の検査: 公開版の中に、この PC のユーザーのフォルダ・NAS の場所（remote origin のホストと利用者名）・git のメールアドレス・
  設定のプレイヤー ID（config/ と blnext-app/config/ の settings.json）があれば止まる。調べる値はこの PC から読む（ここには書かない）。
- 版は web/js/version.js の APP_VERSION。public の先頭が同じ版の「公開」のコミットなら、作り直す（まだ push していない時だけ使うこと。
  push した後に同じ版を作り直すと、GitHub と食い違う）。
- push はしない（GitHub への公開は利用者の指示を得てから）。最後に送るコマンドを出す。
- 作者・コミッタは GitHub の noreply のアドレス（NLM の公開版と同じ）。公開の手順は CLAUDE.md の「配布の手順」。
"""
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE = ('notes', 'CLAUDE.local.md')
BRANCH = 'public'
SOURCE = 'master'
AUTHOR = ('Oz-Co-two', '332386196+Oz-Co-two@users.noreply.github.com')


class PublicError(Exception):
    pass


def git(repo, *args, env=None, input=None):
    # core.quotepath=false: 日本語のファイル名（notes/プレイ記録.md など）を引用符と ã… の形にせず、そのまま出させる
    p = subprocess.run(['git', '-C', str(repo), '-c', 'core.quotepath=false', *args], capture_output=True, text=True, encoding='utf-8',
                       env={**os.environ, **(env or {})}, input=input)
    if p.returncode != 0:
        raise PublicError('git %s: %s' % (' '.join(args[:3]), (p.stderr or p.stdout).strip()))
    return p.stdout.strip()


def ref(repo, name):
    """ref のコミット（無ければ None）"""
    p = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', name + '^{commit}'],
                       capture_output=True, text=True, encoding='utf-8')
    return p.stdout.strip() or None


def build_tree(repo, source=SOURCE, exclude=EXCLUDE):
    """source の中身から exclude を外した tree"""
    with tempfile.TemporaryDirectory() as td:
        env = {'GIT_INDEX_FILE': str(Path(td) / 'index')}
        git(repo, 'read-tree', source, env=env)
        git(repo, 'rm', '-r', '--cached', '--quiet', '--ignore-unmatch', '--', *exclude, env=env)
        return git(repo, 'write-tree', env=env)


def secrets(repo):
    """公開版に入っていてはいけない、この PC・作者に固有の文字の並び（名前, 値）"""
    name = Path.home().name   # C:\Users\<名前>。JSON などでは \\ で書かれていることもある
    out = [('この PC のユーザーのフォルダ', 'Users' + sep + name) for sep in ('\\', '/', '\\\\')]
    origin = subprocess.run(['git', '-C', str(repo), 'remote', 'get-url', 'origin'], capture_output=True, text=True).stdout.strip()
    m = re.match(r'(?:\w+://)?(?:([^@/]+)@)?([^/:]+)', origin)
    if m and origin:
        if m.group(1):
            out.append(('NAS の利用者名', m.group(1)))
        out.append(('NAS の場所', m.group(2)))
    email = subprocess.run(['git', '-C', str(repo), 'config', 'user.email'], capture_output=True, text=True).stdout.strip()
    if email and not email.endswith('noreply.github.com'):
        out.append(('git のメールアドレス', email))
    for p in (Path(repo) / 'config' / 'settings.json', Path(repo) / 'blnext-app' / 'config' / 'settings.json'):
        try:
            pid = str(json.loads(p.read_text(encoding='utf-8')).get('player_id') or '')
        except (OSError, ValueError, AttributeError):
            continue
        if len(pid) >= 5:
            out.append(('設定のプレイヤー ID', pid))
    return [(k, v) for k, v in out if v and len(v) >= 4]


def check_tree(repo, tree, exclude=EXCLUDE, extra=()):
    """公開版の tree の検査。問題の一覧（無ければ []）"""
    bad = []
    names = git(repo, 'ls-tree', '-r', '--name-only', tree).splitlines()
    for n in names:
        if any(n == e or n.startswith(e + '/') for e in exclude):
            bad.append('外すはずの物が入っている: ' + n)
    for label, value in [*secrets(repo), *extra]:
        p = subprocess.run(['git', '-C', str(repo), '-c', 'core.quotepath=false', 'grep', '-I', '-l', '-i', '-F', '-e', value, tree],
                           capture_output=True, text=True, encoding='utf-8')
        for line in p.stdout.splitlines():
            bad.append('%sが入っている: %s' % (label, line.split(':', 1)[-1]))
    return bad


def make(repo, version, check_only=False, extra=()):
    """public に「<版> 公開」のコミットとタグを作る。返り値は (コミット, 作ったか)"""
    if not ref(repo, SOURCE):
        raise PublicError(f'{SOURCE} がありません')
    tree = build_tree(repo)
    bad = check_tree(repo, tree, extra=extra)
    if bad:
        raise PublicError('公開版に入れてはいけない物があります:\n  ' + '\n  '.join(bad))
    if check_only:
        return None, False
    msg = f'{version} 公開'
    old = ref(repo, BRANCH)
    parent = old
    if old and git(repo, 'log', '-1', '--format=%s', old) == msg:   # まだ push していない同じ版を作り直す
        parent = ref(repo, old + '^')
    if parent and git(repo, 'rev-parse', parent + '^{tree}') == tree:
        raise PublicError(f'公開版の中身が前の公開（{BRANCH}）と同じです。版を上げてから作ってください')
    env = {'GIT_AUTHOR_NAME': AUTHOR[0], 'GIT_AUTHOR_EMAIL': AUTHOR[1], 'GIT_COMMITTER_NAME': AUTHOR[0], 'GIT_COMMITTER_EMAIL': AUTHOR[1]}
    args = ['commit-tree', tree, *(['-p', parent] if parent else []), '-F', '-']
    new = git(repo, *args, env=env, input=msg + '\n')
    git(repo, 'update-ref', 'refs/heads/' + BRANCH, new, *([old] if old else []))
    if ref(repo, 'refs/tags/' + version):
        git(repo, 'tag', '-d', version)
    git(repo, 'tag', '-a', version, new, '-m', version, env=env)
    return new, True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='作らずに検査だけ')
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from core.version import app_version
    ver = app_version(str(ROOT))
    if not re.fullmatch(r'v\d+\.\d+\.\d+', ver or ''):
        print(f'APP_VERSION（{ver!r}）が読めません', file=sys.stderr)
        return 1
    try:
        new, made = make(ROOT, ver, check_only=a.check)
    except PublicError as e:
        print('止めました: ' + str(e), file=sys.stderr)
        return 1
    if not made:
        print(f'検査は通りました（{SOURCE} から {", ".join(EXCLUDE)} を外した中身に、個人の情報はありません）')
        return 0
    print(f'{BRANCH} に「{ver} 公開」（{new[:7]}）とタグ {ver} を作りました（{", ".join(EXCLUDE)} は外した）。')
    print('GitHub に出す時（利用者の指示を得てから）:')
    print(f'  git push github {BRANCH}:master && git push github {ver}')
    print(f'  git push origin {BRANCH} {ver}   （NAS にも残す）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
