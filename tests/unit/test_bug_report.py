# 不具合の報告（設定の「このアプリについて」）: GitHub の Issues の新規作成のページと、ログのフォルダを開く口（serve.py）。
# 本物のブラウザ・エクスプローラーは開かない（serve.open_browser・launch.start を差し替える）
import os, platform, sys
from urllib.parse import parse_qs, urlsplit

import pytest

import serve
from core import app_update as au, launch
from core.settings import Settings
from test_serve_data import call, server   # noqa: F401（server は fixture）

PID = '76561198012345678'   # 作り物のプレイヤー ID（報告に入らないことを確かめる）


def query(url):
    u = urlsplit(url)
    return u, {k: v[0] for k, v in parse_qs(u.query, strict_parsing=True).items()}


@pytest.fixture
def player(tmp_path, monkeypatch):
    """プレイヤーを決めた設定（ID・国）を serve に使わせる"""
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': PID, 'country': 'jp'}, trusted=True)
    monkeypatch.setattr(serve, 'SETTINGS', s)
    return s


def test_issue_url_has_versions_and_no_personal_info(player):
    url = serve.issue_url()
    u, q = query(url)
    assert url.startswith(au.REPO_URL + '/issues/new?')
    assert (u.scheme, u.netloc, u.path) == ('https', 'github.com', '/%s/issues/new' % au.REPO)
    assert set(q) == {'title', 'body'} and q['title'].startswith('[不具合]')
    body = q['body']
    for head in ('起きたこと', 'その前にした操作', '期待したこと'):
        assert '### ' + head in body
    assert serve.APP_VERSION and serve.APP_VERSION in body and '開発用の起動' in body   # テストは exe 版でない
    assert 'Windows: ' + serve.windows_version() in body
    # 個人の情報（プレイヤーの ID・PC の中の場所・利用者の名前）は入れない
    assert PID not in url and PID not in body
    for path in (serve.DATA_DIR, serve.BASE_DIR, os.path.expanduser('~')):
        assert path not in body and os.path.normcase(path) not in body.lower()
    user = os.environ.get('USERNAME') or ''
    if len(user) >= 4:
        assert user.lower() not in body.lower()
    assert len(url) < 4000   # URL の長さは常識の範囲に（GitHub は長すぎると開けない）


def test_issue_url_frozen(monkeypatch):
    monkeypatch.setattr(serve, 'FROZEN', True)
    assert '%s（exe 版）' % serve.APP_VERSION in query(serve.issue_url())[1]['body']


def test_windows_version(monkeypatch):
    v = serve.windows_version()
    if sys.platform == 'win32':
        assert v.startswith('Windows ') and platform.win32_ver()[1] in v
    assert os.path.expanduser('~') not in v

    def broken():
        raise OSError('no wmi')
    monkeypatch.setattr(platform, 'win32_ver', broken)
    monkeypatch.setattr(platform, 'platform', broken)
    assert serve.windows_version() == sys.platform   # 読めなくても報告のページは開ける


def test_api_open_issues(server, player, monkeypatch):
    opened = []
    monkeypatch.setattr(serve, 'open_browser', opened.append)
    assert call(server, 'update/open', {'what': 'issues'})['ok']
    assert call(server, 'update/open', {'what': 'issues', 'url': 'https://evil.example/', 'body': 'x'})['ok']   # ほかのキーは見ない
    assert opened == [serve.issue_url()] * 2
    for bad in ('https://evil.example/', 'Issues', ['issues'], {'issues': 1}, None):
        assert call(server, 'update/open', {'what': bad})['error'] == 'bad-request'
    assert len(opened) == 2

    def fail(url):
        raise OSError('no browser')
    monkeypatch.setattr(serve, 'open_browser', fail)
    assert call(server, 'update/open', {'what': 'issues'})['error'] == 'open-failed'


@pytest.fixture
def started(tmp_path, monkeypatch):
    """書き込み先を一時フォルダにし、外の起動（launch.start）を記録だけにする"""
    monkeypatch.setattr(serve, 'DATA_DIR', str(tmp_path / 'app'))
    got = []
    monkeypatch.setattr(launch, 'start', got.append)
    return got


def test_api_open_logs_makes_and_opens_only_log_dir(server, started, tmp_path):
    logs_dir = tmp_path / 'app' / 'logs'
    assert not logs_dir.exists()
    assert call(server, 'open/logs')['ok']
    assert logs_dir.is_dir() and started == [str(logs_dir)]   # 無ければ作ってから開く
    # 本体に何を渡しても、ほかの場所は開かない
    for body in ({'path': 'C:\\Windows'}, {'dir': '..'}, {'what': 'config'}, {'path': str(tmp_path)}, {'url': 'https://evil.example/'}):
        assert call(server, 'open/logs', body)['ok']
    assert started == [str(logs_dir)] * 6


def test_api_open_logs_failures(server, started, tmp_path, monkeypatch):
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app' / 'logs').write_text('x', encoding='utf-8')   # 同じ名前のファイルは開かない
    assert call(server, 'open/logs')['error'] == 'open-failed' and started == []
    os.remove(tmp_path / 'app' / 'logs')

    def fail(target):
        raise OSError('no explorer')
    monkeypatch.setattr(launch, 'start', fail)
    assert call(server, 'open/logs')['error'] == 'open-failed'


def test_log_dir_is_where_logs_are_written():
    # 開くのは、ログを書いているフォルダ（serve を読み込んだ時に logs.setup が作った所）
    from core import logs
    d = logs.log_dir(serve.DATA_DIR)
    assert d == os.path.join(serve.DATA_DIR, 'logs') and os.path.isdir(d)
