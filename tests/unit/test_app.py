# app.py（デスクトップ版の起動）のうち、窓を開かずに確かめられる所: 補助関数・ネイティブのダイアログの窓口（偽の webview）・
# 起動前の準備・--selftest・main() の流れ（偽の webview モジュール。本物の窓は開かない）・二重起動の防止の入口
import inspect, json, os, runpy, socket, sys, tempfile, threading, types, urllib.request

import pytest

import app
import serve
from core.settings import Settings


class FakeWebview:
    """ダイアログの答えを決めておき、開いた時の引数を覚える偽の webview"""
    class FileDialog:
        FOLDER, OPEN, SAVE = 'folder', 'open', 'save'

    def __init__(self, answer):
        self.answer, self.calls = answer, []
        self.windows = [types.SimpleNamespace(create_file_dialog=self._dialog)]

    def _dialog(self, kind, **kw):
        self.calls.append((kind, kw))
        return self.answer


# ---- 画面（JS）へ見せる窓口 ----

def test_api_has_no_way_to_pass_a_path():
    # pywebview は '_' で始まらないメソッドを全部画面へ見せる。場所を JS から渡せる口が無いこと（仕様書の「プレイリスト出力」）
    # 「ファイルに保存」が受けるのはセットの id だけ（保存先はネイティブのダイアログで選ぶ）
    api = app._make_api(Settings(tempfile.mkdtemp()), FakeWebview(None))
    public = {n for n in dir(api) if not n.startswith('_')}
    assert public == {'pick_beat_saber_dir', 'pick_playerdata', 'save_playlist', 'quit_app'}
    for n in public:
        assert inspect.getfullargspec(getattr(api, n)).args == (['self', 'preset'] if n == 'save_playlist' else ['self'])


def test_pick_playerdata_saves_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings(str(tmp_path / 'config'))
    wv = FakeWebview(['PlayerData.dat'])   # 相対で返っても絶対パスにして入れる
    r = app._make_api(s, wv).pick_playerdata()
    assert r == {'ok': True, 'path': str(tmp_path / 'PlayerData.dat')}
    assert s.get('playerdata_path') == str(tmp_path / 'PlayerData.dat')
    kind, kw = wv.calls[0]
    assert kind == 'open'
    assert kw['file_types'] == ('Beat Saber のプレイ記録 (PlayerData.dat)', 'すべてのファイル (*.*)')


def test_pick_playerdata_english_labels(tmp_path):
    s = Settings(str(tmp_path / 'config'))
    s.update({'lang': 'en'})
    wv = FakeWebview([str(tmp_path / 'x.dat')])
    app._make_api(s, wv).pick_playerdata()
    assert wv.calls[0][1]['file_types'] == ('Beat Saber play data (PlayerData.dat)', 'All files (*.*)')


@pytest.mark.parametrize('answer', [None, [], ()])
def test_pick_playerdata_cancel_keeps_setting(tmp_path, answer):
    s = Settings(str(tmp_path / 'config'))
    s.update({'playerdata_path': str(tmp_path / 'old.dat')}, trusted=True)
    r = app._make_api(s, FakeWebview(answer)).pick_playerdata()
    assert r == {'ok': False, 'error': 'cancelled'}
    assert s.get('playerdata_path') == str(tmp_path / 'old.dat')


def test_pick_playerdata_start_folder(tmp_path, monkeypatch):
    # 設定が無ければ既定の場所（あれば）から開く。設定があればそのフォルダから。フォルダが無ければ指定しない
    s = Settings(str(tmp_path / 'config'))
    (tmp_path / 'LocalLow').mkdir()
    monkeypatch.setattr(app, 'DEFAULT_PLAYERDATA', str(tmp_path / 'LocalLow' / 'PlayerData.dat'))
    wv = FakeWebview(None)
    api = app._make_api(s, wv)
    api.pick_playerdata()
    assert wv.calls[-1][1]['directory'] == str(tmp_path / 'LocalLow')
    (tmp_path / 'mine').mkdir()
    s.update({'playerdata_path': str(tmp_path / 'mine' / 'PlayerData.dat')}, trusted=True)
    api.pick_playerdata()
    assert wv.calls[-1][1]['directory'] == str(tmp_path / 'mine')
    s.update({'playerdata_path': str(tmp_path / 'gone' / 'PlayerData.dat')}, trusted=True)
    api.pick_playerdata()
    assert 'directory' not in wv.calls[-1][1]


def test_pick_beat_saber_dir_start_folder(tmp_path):
    s = Settings(str(tmp_path / 'config'))
    wv = FakeWebview(None)
    api = app._make_api(s, wv)
    api.pick_beat_saber_dir()
    assert wv.calls[-1] == ('folder', {})
    s.update({'beat_saber_dir': str(tmp_path)}, trusted=True)
    api.pick_beat_saber_dir()
    assert wv.calls[-1] == ('folder', {'directory': str(tmp_path)})


def test_save_playlist(tmp_path):
    # 「ファイルに保存」: 中身は serve が作り、保存先は「名前を付けて保存」で選んだ所。保存できたら記録する
    done = []

    def exporter(preset):
        assert preset == 'p2'
        return '{"songs": []}', 'blnext-p2.bplist', done.append
    s = Settings(str(tmp_path / 'config'))
    wv = FakeWebview((str(tmp_path / 'out'),))                 # 拡張子なしで答えても .bplist を付ける
    r = app._make_api(s, wv, exporter=exporter).save_playlist('p2')
    p = str(tmp_path / 'out.bplist')
    assert r == {'ok': True, 'path': p} and open(p, encoding='utf-8').read() == '{"songs": []}' and done == [p]
    kind, kw = wv.calls[0]
    assert kind == 'save' and kw['save_filename'] == 'blnext-p2.bplist' and '*.bplist' in kw['file_types'][0]
    # やめた・場所に書けない・中身が作れない・おかしな引数
    assert app._make_api(s, FakeWebview(None), exporter=exporter).save_playlist('p2') == {'ok': False, 'error': 'cancelled'}
    bad = FakeWebview((str(tmp_path / 'no' / 'such' / 'dir' / 'x.bplist'),))
    assert app._make_api(s, bad, exporter=exporter).save_playlist('p2')['error'] == 'write-failed'

    def nothing(preset):
        raise serve.ApiError('nothing-to-export')
    assert app._make_api(s, wv, exporter=nothing).save_playlist(None) == {'ok': False, 'error': 'nothing-to-export'}
    assert app._make_api(s, wv, exporter=exporter).save_playlist(['p2']) == {'ok': False, 'error': 'bad-request'}
    assert app._make_api(s, wv).save_playlist('p1') == {'ok': False, 'error': 'bad-request'}
    assert len(done) == 1


# ---- 起動前の準備 ----

def test_pythonnet_runtime_is_netfx(monkeypatch):
    # webview（pythonnet）を読む前に .NET Framework に固定する。利用者が自分で決めていればそれを使う
    monkeypatch.delenv('PYTHONNET_RUNTIME', raising=False)
    runpy.run_path(app.__file__, run_name='app_import_check')
    assert os.environ['PYTHONNET_RUNTIME'] == 'netfx'
    monkeypatch.setenv('PYTHONNET_RUNTIME', 'coreclr')
    runpy.run_path(app.__file__, run_name='app_import_check')
    assert os.environ['PYTHONNET_RUNTIME'] == 'coreclr'


def test_app_root(monkeypatch, tmp_path):
    assert app._app_root() == os.path.dirname(os.path.abspath(app.__file__))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    assert app._app_root() == str(tmp_path)


def test_data_root_dev_and_frozen(monkeypatch, tmp_path):
    # 開発中は渡したフォルダ、exe 版は exe の隣（_internal の中ではない）。中に4つのフォルダを作る
    dev = tmp_path / 'dev'
    assert app._data_root(str(dev)) == str(dev)
    assert sorted(os.listdir(dev)) == ['config', 'data', 'exports', 'logs']
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'dist' / 'blnext.exe'))
    assert app._data_root(str(tmp_path / 'dist' / '_internal')) == str(tmp_path / 'dist')
    assert sorted(os.listdir(tmp_path / 'dist')) == ['config', 'data', 'exports', 'logs']


def test_free_port_can_be_bound():
    p = app._free_port()
    assert 0 < p < 65536
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', p))
    finally:
        s.close()


def _mark(path):
    """「インターネットからのファイル」の印（NTFS の代替データストリーム）を付ける。付けられない所ではテストしない"""
    path.write_bytes(b'x')
    try:
        with open(str(path) + ':Zone.Identifier', 'w') as f:
            f.write('[ZoneTransfer]\nZoneId=3\n')
    except OSError:
        pytest.skip('このフォルダでは代替データストリームが使えない')


def _marked(path):
    return os.path.exists(str(path) + ':Zone.Identifier')


def test_unblock_only_inside_bundle(monkeypatch, tmp_path):
    exe_dir = tmp_path / 'blnext-app'
    internal = exe_dir / '_internal'
    (internal / 'sub').mkdir(parents=True)
    a, b, outside = internal / 'Python.Runtime.dll', internal / 'sub' / 'x.dll', exe_dir / 'other.zip'
    for p in (a, b, outside):
        _mark(p)
    (internal / 'plain.txt').write_bytes(b'')   # 印の無いファイルがあっても止まらない
    # 開発中（frozen でない）は何もしない
    monkeypatch.setattr(sys, '_MEIPASS', str(internal), raising=False)
    app._unblock_dist_folder()
    assert _marked(a)
    # exe 版: 同梱物の中の印だけを消す（ファイルそのものは残す・exe の隣の無関係なファイルは触らない）
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe_dir / 'blnext.exe'))
    app._unblock_dist_folder()
    assert not _marked(a) and not _marked(b) and a.exists() and b.exists()
    assert _marked(outside)


def test_unblock_skips_when_bundle_is_exe_folder(monkeypatch, tmp_path):
    # 同梱物の場所が exe の隣そのもの（古い形）なら、無関係なダウンロードの印を消さないよう何もしない
    f = tmp_path / 'downloaded.zip'
    _mark(f)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'blnext.exe'))
    app._unblock_dist_folder()
    assert _marked(f)


# ---- --selftest（ビルドした exe の動作確認） ----

@pytest.fixture
def keep_env(monkeypatch, tmp_path):
    """selftest・main が書き換える環境変数と sys.path を、テストの後に戻す。一時フォルダは tmp_path の中に作らせる"""
    for k in ('BLN_BASE_DIR', 'BLN_DATA_DIR', 'BLN_NO_NET'):
        monkeypatch.setenv(k, os.environ.get(k, ''))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    made = []

    def mkdtemp(prefix=''):
        d = tmp_path / ('%s%d' % (prefix, len(made)))
        d.mkdir()
        made.append(str(d))
        return str(d)
    monkeypatch.setattr(tempfile, 'mkdtemp', mkdtemp)
    return made


def test_selftest_ok(tmp_path, keep_env):
    out = tmp_path / 'selftest.json'
    app.selftest(str(out))
    res = json.loads(out.read_text(encoding='utf-8'))
    assert res['ok'] is True and res['version'] == serve.APP_VERSION and res['ssl']
    # 書き込み先は一時フォルダ・ネットに出ない（利用者のデータに触らない）
    assert keep_env and os.environ['BLN_DATA_DIR'] == keep_env[0] and os.path.basename(keep_env[0]).startswith('bln-selftest-')
    assert os.environ['BLN_NO_NET'] == '1'


@pytest.fixture
def made_servers(monkeypatch):
    """selftest が作ったサーバーを覚える"""
    made, real = [], serve.make_server
    monkeypatch.setattr(serve, 'make_server', lambda **kw: made.append(real(**kw)) or made[-1])
    return made


def test_selftest_closes_server(tmp_path, keep_env, made_servers):
    app.selftest(str(tmp_path / 'selftest.json'))
    assert len(made_servers) == 1 and closed(made_servers[0])


def test_selftest_closes_server_when_check_fails(tmp_path, keep_env, made_servers, monkeypatch):
    # サーバーを立てた後の確かめで失敗しても、失敗を書いてサーバーは止めて閉じる
    def boom(*a, **kw):
        raise OSError('refused')
    monkeypatch.setattr(urllib.request, 'urlopen', boom)
    out = tmp_path / 'selftest.json'
    app.selftest(str(out))
    assert json.loads(out.read_text(encoding='utf-8')) == {'ok': False, 'error': 'OSError: refused'}
    assert closed(made_servers[0])


def test_selftest_failure_is_written(tmp_path, keep_env, monkeypatch):
    def boom(**kw):
        raise OSError('port busy')
    monkeypatch.setattr(serve, 'make_server', boom)
    out = tmp_path / 'selftest.json'
    app.selftest(str(out))
    assert json.loads(out.read_text(encoding='utf-8')) == {'ok': False, 'error': 'OSError: port busy'}


def test_run_as_script_with_selftest(tmp_path, keep_env, monkeypatch):
    # `blnext.exe --selftest <出力先>` と同じ（tools/build_and_deploy.py が配る前に使う）
    out = tmp_path / 'r.json'
    monkeypatch.setattr(sys, 'argv', ['app.py', '--selftest', str(out)])
    runpy.run_path(app.__file__, run_name='__main__')
    assert json.loads(out.read_text(encoding='utf-8'))['ok'] is True


# ---- main()（偽の webview モジュール。窓は開かない） ----

class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


def _fake_webview_module(on_start):
    m = types.ModuleType('webview')
    m.created = []

    def create_window(title, url, **kw):
        win = types.SimpleNamespace(title=title, url=url, kw=kw, events=types.SimpleNamespace(closing=_Event()))
        m.created.append(win)
        return win
    m.create_window = create_window
    m.start = lambda: on_start(m.created[-1])
    return m


def test_main_selftest_branch(monkeypatch):
    got = []
    monkeypatch.setattr(app, 'selftest', got.append)
    monkeypatch.setitem(sys.modules, 'webview', None)   # 窓の部品には触れない（触れば ImportError）
    monkeypatch.setattr(sys, 'argv', ['blnext.exe', '--selftest', 'C:\\out.json'])
    app.main()
    monkeypatch.setattr(sys, 'argv', ['blnext.exe', '--selftest'])
    app.main()
    assert got == ['C:\\out.json', 'selftest.json']


@pytest.fixture
def main_env(tmp_path, keep_env, monkeypatch):
    """main() を窓なしで動かす準備。書き込み先は tmp_path。作ったサーバーと、止めたかを覚える"""
    monkeypatch.setattr(sys, 'argv', ['blnext.exe'])
    monkeypatch.setattr(app, '_data_root', lambda app_root: str(tmp_path))
    servers = []
    real = serve.make_server

    def make_server(**kw):
        httpd = real(**kw)
        rec = types.SimpleNamespace(kw=kw, httpd=httpd, stopped=False)
        stop = httpd.shutdown

        def shutdown():
            rec.stopped = True
            stop()
        httpd.shutdown = shutdown
        servers.append(rec)
        return httpd
    monkeypatch.setattr(serve, 'make_server', make_server)
    yield servers
    for rec in servers:
        rec.httpd.server_close()


def closed(httpd):
    """待ち受けのソケットを閉じたか"""
    return httpd.socket.fileno() == -1


def test_main_opens_window_with_token_url(tmp_path, main_env, monkeypatch):
    seen = {}

    def on_start(win):
        # 窓が開いている間: 合言葉付きの URL で画面と API が使える
        req = urllib.request.Request(win.url.split('#')[0])
        with urllib.request.urlopen(req, timeout=10) as r:
            seen['page'] = r.status
        token = win.url.split('#k=', 1)[1]
        req = urllib.request.Request(win.url.split('/index.html')[0] + '/__api/status', data=b'{}', method='POST',
                                     headers={'X-BLN-Request': '1', 'X-BLN-Token': token})
        with urllib.request.urlopen(req, timeout=10) as r:
            seen['status'] = json.loads(r.read())
    wv = _fake_webview_module(on_start)
    monkeypatch.setitem(sys.modules, 'webview', wv)
    app.main()
    win = wv.created[0]
    assert win.title == ('blnext ' + serve.APP_VERSION).strip()
    assert win.url == serve.access_url(main_env[0].httpd.server_address[1])
    assert main_env[0].kw['bind'] == '127.0.0.1'   # 外から繋げない
    assert win.kw['min_size'] == (960, 640)
    assert {n for n in dir(win.kw['js_api']) if not n.startswith('_')} == {'pick_beat_saber_dir', 'pick_playerdata', 'save_playlist', 'quit_app'}
    assert seen['page'] == 200 and seen['status']['ok']
    assert os.environ['BLN_DATA_DIR'] == str(tmp_path)   # serve.py が読む書き込み先
    assert main_env[0].stopped and closed(main_env[0].httpd)   # 窓を閉じたらサーバーを止めて、待ち受けも閉じる


def test_main_closing_handler(main_env, monkeypatch):
    wv = _fake_webview_module(lambda win: None)
    monkeypatch.setitem(sys.modules, 'webview', wv)
    app.main()
    (handler,) = wv.created[0].events.closing.handlers
    calls = []
    monkeypatch.setattr(serve, 'on_closing', lambda: calls.append(1))
    assert handler() is True and calls == [1]

    def boom():
        raise RuntimeError('x')
    monkeypatch.setattr(serve, 'on_closing', boom)
    assert handler() is True   # 失敗しても閉じるのは止めない


# ---- 二重起動の防止（core/single_instance.py。本物の窓は探すだけで、前に出す処理は呼ばない） ----

def test_main_skips_instance_check_for_selftest_and_apply_update(monkeypatch):
    # 画面を出さない起動は調べない（ビルドの確かめは別の一時フォルダのデータ、差し替え役は旧版の終了を自分で待つ）
    from core import app_update, single_instance
    monkeypatch.setattr(single_instance, 'claim', lambda *a, **kw: pytest.fail('調べないはず'))
    got = []
    monkeypatch.setattr(app, 'selftest', got.append)
    monkeypatch.setattr(app_update, 'apply_main', lambda argv: got.append(list(argv)))
    monkeypatch.setitem(sys.modules, 'webview', None)   # 窓の部品には触れない（触れば ImportError）
    monkeypatch.setattr(sys, 'argv', ['blnext.exe', '--selftest', 'C:\\out.json'])
    app.main()
    argv = ['blnext.exe', '--apply-update', '--target', 'C:\\blnext', '--pid', '123', '--lang', 'ja']
    monkeypatch.setattr(sys, 'argv', argv)
    app.main()
    assert got == ['C:\\out.json', argv]


def test_main_ends_when_already_running(tmp_path, main_env, monkeypatch):
    # 同じ書き込み先の blnext がもう動いている（claim がその窓を前に出して None）: サーバーも窓も作らずに終わる
    from core import single_instance
    seen = []
    monkeypatch.setattr(single_instance, 'claim', lambda root: seen.append(root))
    monkeypatch.setattr(app, '_unblock_dist_folder', lambda: seen.append('unblock'))
    monkeypatch.setitem(sys.modules, 'webview', None)
    assert app.main() is None
    assert seen == [str(tmp_path)] and main_env == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows の仕組み')
def test_main_holds_instance_while_open(tmp_path, main_env, monkeypatch):
    # 普通の起動は書き込み先の印を取る。窓が開いている間、2つ目（ここでは別のスレッド）は開けない
    from core import single_instance
    seen = {}

    def on_start(win):
        res = []
        second = lambda: res.append(single_instance.claim(str(tmp_path), wait=0, find=lambda pid: None, notify=lambda root: None))
        t = threading.Thread(target=second)
        t.start()
        t.join(10)
        seen['second'], seen['owner'] = res, single_instance.read_owner(str(tmp_path))
    monkeypatch.setitem(sys.modules, 'webview', _fake_webview_module(on_start))
    app.main()
    assert seen == {'second': [None], 'owner': os.getpid()}


# ---- 窓の位置・大きさ（閉じた時の状態で次も開く） ----

SCREEN = types.SimpleNamespace(x=0, y=0, width=1920, height=1080)


def write_window(d, **w):
    (d / app.WINDOW_FILE).write_text(json.dumps(w), encoding='utf-8')


def test_load_window(tmp_path):
    first = {'width': 1280, 'height': 860}
    assert app._load_window(str(tmp_path), [SCREEN]) == first   # 記録が無ければ初めての大きさ・真ん中
    write_window(tmp_path, x=100, y=50, width=1500, height=900, maximized=True)
    assert app._load_window(str(tmp_path), [SCREEN]) == {'x': 100, 'y': 50, 'width': 1500, 'height': 900, 'maximized': True}
    write_window(tmp_path, x=100, y=50, width=300, height=200, maximized=False)
    r = app._load_window(str(tmp_path), [SCREEN])
    assert (r['width'], r['height'], r['maximized']) == (960, 640, False)   # 最小より小さくしない
    # 外したモニターの上だった（タイトルバーが今の画面に見えない）: 位置は使わない（真ん中に開く）。大きさは使う
    for x, y in ((2500, 100), (100, 1070), (-1500, 100), (100, -500)):
        write_window(tmp_path, x=x, y=y, width=1500, height=900)
        r = app._load_window(str(tmp_path), [SCREEN])
        assert 'x' not in r and r['width'] == 1500, (x, y)
    write_window(tmp_path, x=-1500, y=100, width=1500, height=900)   # 左のモニターがつながっていれば使う
    assert app._load_window(str(tmp_path), [SCREEN, types.SimpleNamespace(x=-1920, y=0, width=1920, height=1080)])['x'] == -1500
    assert 'x' not in app._load_window(str(tmp_path), [])   # 画面が分からない時も真ん中
    for bad in ('{', '[]', '{"x": 1}', '{"x": "a", "y": 0, "width": 1, "height": 1}'):
        (tmp_path / app.WINDOW_FILE).write_text(bad, encoding='utf-8')
        assert app._load_window(str(tmp_path), [SCREEN]) == first, bad


def fake_form(state, bounds, restore=None):
    rect = lambda r: types.SimpleNamespace(X=r[0], Y=r[1], Width=r[2], Height=r[3])
    return types.SimpleNamespace(WindowState=state, Bounds=rect(bounds), RestoreBounds=rect(restore or bounds))


def test_window_geometry():
    # 値は create_window と同じ論理の値（DPI の倍率で割る。125% なら 120dpi）
    g = app._window_geometry(fake_form('Normal', (125, 250, 1875, 1250)), dpi=120)
    assert g == {'x': 100, 'y': 200, 'width': 1500, 'height': 1000, 'maximized': False}
    # 最大化・最小化している時は、元に戻した時の位置・大きさ
    g = app._window_geometry(fake_form('Maximized', (-10, -10, 2420, 1310), (125, 250, 1875, 1250)), dpi=120)
    assert g == {'x': 100, 'y': 200, 'width': 1500, 'height': 1000, 'maximized': True}
    g = app._window_geometry(fake_form('Minimized', (-32000, -32000, 200, 40), (10, 20, 1300, 900)), dpi=96)
    assert g == {'x': 10, 'y': 20, 'width': 1300, 'height': 900, 'maximized': False}


def test_main_remembers_window(tmp_path, main_env, monkeypatch):
    # 閉じる時に位置・大きさを config/window.json に残し、次に開く時に使う
    wv = _fake_webview_module(lambda win: None)
    wv.screens = [SCREEN]
    monkeypatch.setitem(sys.modules, 'webview', wv)
    real = app._window_geometry
    monkeypatch.setattr(app, '_window_geometry', lambda form: real(form, dpi=96))
    app.main()
    win = wv.created[0]
    assert (win.kw['width'], win.kw['height'], win.kw['min_size']) == (1280, 860, (960, 640)) and 'x' not in win.kw
    (handler,) = win.events.closing.handlers
    assert handler() is True   # 窓の状態を読めなくても閉じるのは止めない（偽の窓には native が無い）
    win.native = fake_form('Maximized', (-8, -8, 1936, 1056), (100, 50, 1500, 900))
    assert handler() is True
    assert json.loads((tmp_path / 'config' / app.WINDOW_FILE).read_text(encoding='utf-8'))['maximized'] is True
    app.main()
    kw = wv.created[1].kw
    assert {k: kw[k] for k in ('x', 'y', 'width', 'height', 'maximized')} == {'x': 100, 'y': 50, 'width': 1500, 'height': 900, 'maximized': True}
    def on_start(win):
        raise RuntimeError('WebView2 が無い')
    monkeypatch.setitem(sys.modules, 'webview', _fake_webview_module(on_start))
    with pytest.raises(RuntimeError):
        app.main()
    assert main_env[0].stopped and closed(main_env[0].httpd)


def test_stop_server_closes_even_if_shutdown_fails():
    httpd = serve.make_server(port=0)

    def boom():
        raise OSError('x')
    httpd.shutdown = boom   # serve_forever を動かしていない等で止められなくても
    app._stop_server(httpd)
    assert closed(httpd)
