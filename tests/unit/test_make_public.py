# 公開版を作る道具（tools/make_public.py）。一時的な git のリポジトリで、外す物（notes/・CLAUDE.local.md）・個人の情報の検査・
# public の積み重ね方（版ごとに1コミット・同じ版は作り直す）を確かめる
import subprocess, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import make_public as mp  # noqa: E402


def run(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'core.quotepath=false', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout.strip()


def commit(repo, files, msg='c'):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    run(repo, 'add', '-A')
    run(repo, 'commit', '-q', '-m', msg)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / 'repo'
    r.mkdir()
    run(r, 'init', '-q', '-b', 'master')
    run(r, 'config', 'user.name', 'dev')
    run(r, 'config', 'user.email', 'dev@example.com')
    commit(r, {'app.py': 'print(1)\n', 'notes/プレイ記録.md': '作者の記録\n', 'CLAUDE.local.md': 'ローカル\n', 'CLAUDE.md': '公開\n'})
    monkeypatch.setattr(mp, 'secrets', lambda repo: [('テストの秘密', 'SECRET-123')])
    return r


def files(repo, rev):
    return sorted(run(repo, 'ls-tree', '-r', '--name-only', rev).splitlines())


def test_public_leaves_out_notes_and_local(repo):
    new, made = mp.make(repo, 'v1.0.0')
    assert made and files(repo, 'public') == ['CLAUDE.md', 'app.py']
    assert files(repo, 'master') == ['CLAUDE.local.md', 'CLAUDE.md', 'app.py', 'notes/プレイ記録.md']   # master は変えない
    assert run(repo, 'log', '-1', '--format=%s|%an|%ae', 'public') == 'v1.0.0 公開|%s|%s' % mp.AUTHOR
    assert run(repo, 'rev-parse', 'v1.0.0^{commit}') == new and run(repo, 'cat-file', '-t', 'v1.0.0') == 'tag'   # 注釈付きのタグ
    assert run(repo, 'rev-list', '--count', 'public') == '1'          # 開発の履歴を持たない
    assert run(repo, 'status', '--porcelain') == ''                    # 作業中のフォルダ・index は触らない


def test_versions_stack_and_same_version_is_redone(repo):
    first, _ = mp.make(repo, 'v1.0.0')
    commit(repo, {'app.py': 'print(2)\n'})
    again, _ = mp.make(repo, 'v1.0.0')                     # まだ push していない同じ版は作り直す（積まない）
    assert run(repo, 'rev-list', '--count', 'public') == '1' and again != first
    assert run(repo, 'rev-parse', 'v1.0.0^{commit}') == again
    commit(repo, {'app.py': 'print(3)\n', 'notes/x.md': 'x\n'})
    nxt, _ = mp.make(repo, 'v1.0.1')
    assert run(repo, 'rev-list', '--count', 'public') == '2' and run(repo, 'rev-parse', nxt + '^') == again
    assert files(repo, 'public') == ['CLAUDE.md', 'app.py']


def test_same_content_is_refused(repo):
    mp.make(repo, 'v1.0.0')
    commit(repo, {'notes/y.md': 'メモだけ足した\n'})        # 公開版の中身は変わらない
    with pytest.raises(mp.PublicError, match='同じです'):
        mp.make(repo, 'v1.0.1')


def test_personal_info_stops(repo):
    commit(repo, {'core/x.py': '# 場所は secret-123 の下\n'})
    with pytest.raises(mp.PublicError) as e:
        mp.make(repo, 'v1.0.0')
    assert 'テストの秘密が入っている: core/x.py' in str(e.value)
    assert mp.ref(repo, 'public') is None                   # 何も作らない
    commit(repo, {'core/x.py': '# 直した\n', 'notes/z.md': 'SECRET-123 はメモの中なら良い\n'})
    mp.make(repo, 'v1.0.0')


def test_check_only_makes_nothing(repo):
    assert mp.make(repo, 'v1.0.0', check_only=True) == (None, False)
    assert mp.ref(repo, 'public') is None


def test_secrets_read_from_this_pc(tmp_path, monkeypatch):
    # 調べる値はこの PC から読む: ユーザーのフォルダ・remote origin のホストと利用者名・メールアドレス・設定のプレイヤー ID
    r = tmp_path / 'r'
    r.mkdir()
    run(r, 'init', '-q', '-b', 'master')
    run(r, 'remote', 'add', 'origin', 'ssh://someone@10.0.0.5/vol/git/x.git')
    run(r, 'config', 'user.email', 'me@example.com')
    (r / 'config').mkdir()
    (r / 'config' / 'settings.json').write_text('{"player_id": "76561190000000000"}', encoding='utf-8')
    monkeypatch.setattr(mp.Path, 'home', staticmethod(lambda: Path('C:/Users/someone')))
    values = dict((v, k) for k, v in mp.secrets(r))
    assert values['Users\\someone'] == values['Users/someone'] == 'この PC のユーザーのフォルダ'
    assert values['someone'] == 'NAS の利用者名' and values['10.0.0.5'] == 'NAS の場所'
    assert values['me@example.com'] == 'git のメールアドレス' and values['76561190000000000'] == '設定のプレイヤー ID'


def test_check_finds_left_files(repo):
    # 外すはずの物（日本語の名前も）が入っていれば見つける
    bad = mp.check_tree(repo, run(repo, 'rev-parse', 'master^{tree}'))
    assert '外すはずの物が入っている: notes/プレイ記録.md' in bad and '外すはずの物が入っている: CLAUDE.local.md' in bad
