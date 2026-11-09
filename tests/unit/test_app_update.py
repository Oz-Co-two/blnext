# アプリ本体の更新（core/app_update.py）と配布の道具（tools/make_release.py）。ネットには出ず、ローカルの偽の配布元で通しで確かめる。
# NLM の tools/update_test.py を流用し、使用中のファイルへの対策（退避の名前・古い退避の片付け・使っているアプリの案内）を足した
import hashlib, http.server, io, json, os, sys, threading, time, zipfile
from pathlib import Path

import pytest

import serve
from core import app_update as au, launch
from test_serve_data import call, server   # noqa: F401（server は fixture）

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import make_release  # noqa: E402


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def make_app(d, ver, exe=b'EXE'):
    """配布フォルダの形（exe・_internal・案内文・ライセンス）を作る"""
    d.mkdir(parents=True, exist_ok=True)
    (d / au.EXE_NAME).write_bytes(exe + ver.encode())
    write(d / '_internal' / 'web' / 'js' / 'version.js', f"export const APP_VERSION = '{ver}';\n")
    write(d / '_internal' / 'lib' / 'x.dll', ver)
    for n in au.REPLACE_ITEMS[2:]:
        write(d / n, ver)


def make_zip(tmp_path, ver, extra=None, top=au.ZIP_TOP):
    d = tmp_path / ('app-' + ver)
    make_app(d, ver)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for p in sorted(d.rglob('*')):
            if p.is_file():
                zf.write(p, f'{top}/' + p.relative_to(d).as_posix())
        for name in extra or []:
            zf.writestr(name, 'x')
    return buf.getvalue()


def manifest(ver, data, **kw):
    return {'name': au.APP_NAME, 'format': au.FORMAT, 'version': ver,
            'zip': {'name': f'{au.ZIP_TOP}-{ver}.zip', 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)},
            'notes': [{'version': 'v0.3.0', 'ja': ['新しい'], 'en': []}, {'version': 'v0.2.0', 'ja': ['その前'], 'en': []},
                      {'version': 'v0.1.0', 'ja': ['今の版'], 'en': []}], **kw}


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """偽の配布元。files に {パス: バイト列} を入れる"""
    files = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                for i in range(0, len(body), 64 * 1024):
                    self.wfile.write(body[i:i + 64 * 1024])
            except OSError:
                pass

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % httpd.server_address[1]
    monkeypatch.setattr(au, 'UPDATE_URL', base + '/latest/update.json')
    monkeypatch.setattr(au, 'ZIP_URL', base + '/download/{tag}/{name}')
    yield files
    httpd.shutdown()
    httpd.server_close()


def publish(files, tmp_path, ver='v0.3.0', data=None, **kw):
    data = make_zip(tmp_path, ver) if data is None else data
    files['/latest/update.json'] = json.dumps(manifest(ver, data, **kw)).encode()
    files[f'/download/{ver}/{au.ZIP_TOP}-{ver}.zip'] = data
    return data


@pytest.fixture
def boxes(monkeypatch):
    out = []
    monkeypatch.setattr(au, '_msgbox', out.append)   # テスト中にメッセージボックスを出さない
    monkeypatch.setattr(au, '_busy_apps', lambda paths: [])
    return out


@pytest.fixture
def app_dir(tmp_path):
    d = tmp_path / 'app'
    make_app(d, 'v0.1.0')
    for sub in ('config', 'data', 'logs', 'exports'):
        write(d / sub / 'mine.txt', sub)
    return d


def updater(d, frozen=True):
    return au.Updater(str(d / '_internal'), str(d), frozen)


def wait_job(u, until=('ready', 'error', 'cancel'), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if u.job['state'] in until:
            return u.job
        time.sleep(0.02)
    raise AssertionError(u.job)


def test_vtuple():
    assert au.vtuple('v0.10.2') == (0, 10, 2) and au.vtuple('0.1.0') is None and au.vtuple('v1.2') is None and au.vtuple(None) is None


# ---- update.json の検査 ----

def test_check_manifest():
    m = manifest('v0.3.0', b'x')
    r = au.check_manifest(json.dumps(m))
    assert not r['manual'] and r['zip']['size'] == 1 and r['version'] == 'v0.3.0'
    assert au.check_manifest(json.dumps({**m, 'format': au.FORMAT + 1, 'zip': None}))['manual']   # 新しすぎる形式は手で更新


@pytest.mark.parametrize('bad', [
    'not json', '[]', {'name': 'other'},
    lambda m: {**m, 'name': 'other'},
    lambda m: {**m, 'version': '1.0'},
    lambda m: {**m, 'zip': {**m['zip'], 'name': 'evil.zip'}},
    lambda m: {**m, 'zip': {**m['zip'], 'sha256': 'XYZ'}},
    lambda m: {**m, 'zip': {**m['zip'], 'size': 0}},
    lambda m: {**m, 'zip': {**m['zip'], 'size': au.MAX_ZIP + 1}},
    lambda m: {**m, 'zip': None},
])
def test_check_manifest_rejects(bad):
    if callable(bad):
        bad = bad(manifest('v0.3.0', b'x'))
    with pytest.raises(au.UpdateError) as e:
        au.check_manifest(bad if isinstance(bad, str) else json.dumps(bad))
    assert e.value.code == 'badManifest'


# ---- 確認 ----

def test_check_newer_and_notes(dist, tmp_path, app_dir):
    publish(dist, tmp_path)
    r = updater(app_dir).check()
    assert r['newer'] and r['current'] == 'v0.1.0' and r['latest'] == 'v0.3.0' and r['auto'] == 'ok'
    assert [n['version'] for n in r['notes']] == ['v0.3.0', 'v0.2.0']   # 今の版より新しい分だけ・新しい順


def test_check_same_version(dist, tmp_path, app_dir):
    publish(dist, tmp_path, 'v0.1.0')
    r = updater(app_dir).check()
    assert not r['newer'] and r['notes'] == []


def test_check_not_found_and_offline(dist, app_dir, monkeypatch):
    with pytest.raises(au.UpdateError) as e:
        updater(app_dir).check()
    assert e.value.code == 'notFound'
    monkeypatch.setattr(au, 'UPDATE_URL', 'http://127.0.0.1:9/x')
    with pytest.raises(au.UpdateError) as e:
        updater(app_dir).check()
    assert e.value.code == 'network'


def test_real_url_is_blocked_in_tests(monkeypatch):
    monkeypatch.setenv('BLN_NO_NET', '1')
    assert au.UPDATE_URL.startswith('https://github.com/')
    with pytest.raises(au.UpdateError) as e:
        au._get(au.UPDATE_URL, 10)
    assert e.value.detail == 'BLN_NO_NET'


def test_auto_mode(app_dir, monkeypatch):
    assert updater(app_dir, frozen=False).auto_mode() == 'notExe'
    assert updater(app_dir).auto_mode() == 'ok'
    assert not (app_dir / '.bln-write-test').exists()
    monkeypatch.setattr('builtins.open', lambda *a, **k: (_ for _ in ()).throw(OSError('readonly')))
    assert updater(app_dir).auto_mode() == 'readonly'


# ---- ダウンロード・照合・展開 ----

def test_download_ok(dist, tmp_path, app_dir):
    publish(dist, tmp_path)
    u = updater(app_dir)
    assert u.start_download()['state'] in ('download', 'extract', 'ready')
    assert wait_job(u)['state'] == 'ready'
    new = app_dir / '_update' / 'new' / au.ZIP_TOP
    assert (new / au.EXE_NAME).is_file() and au.read_app_version(str(new / '_internal')) == 'v0.3.0'
    assert not list((app_dir / '_update').glob('*.part'))
    assert (app_dir / au.EXE_NAME).read_bytes() == b'EXEv0.1.0'   # まだ差し替えない


def test_download_rejects_tampered(dist, tmp_path, app_dir):
    data = publish(dist, tmp_path)
    dist[f'/download/v0.3.0/{au.ZIP_TOP}-v0.3.0.zip'] = data[:-1] + bytes([data[-1] ^ 1])
    u = updater(app_dir)
    u.start_download()
    assert wait_job(u) == {'state': 'error', 'error': 'hash', 'detail': ''}
    assert not (app_dir / '_update' / 'new').exists()


def test_download_rejects_too_long(dist, tmp_path, app_dir):
    data = publish(dist, tmp_path)
    dist[f'/download/v0.3.0/{au.ZIP_TOP}-v0.3.0.zip'] = data + b'more'
    u = updater(app_dir)
    u.start_download()
    assert wait_job(u)['error'] == 'hash'


def test_download_cancel(dist, tmp_path, app_dir):
    publish(dist, tmp_path)
    u = updater(app_dir)
    real = u._download
    u._download = lambda m: (u.cancel(), real(m))   # 読み始める時には取り消してある
    u.start_download()
    assert wait_job(u)['state'] == 'cancel'
    assert not (app_dir / '_update' / 'new').exists() and not list((app_dir / '_update').glob('*.part'))


def test_download_needs_exe_and_newer(dist, tmp_path, app_dir):
    publish(dist, tmp_path, 'v0.1.0')
    with pytest.raises(au.UpdateError) as e:
        updater(app_dir, frozen=False).start_download()
    assert e.value.code == 'notAuto'
    with pytest.raises(au.UpdateError) as e:
        updater(app_dir).start_download()
    assert e.value.code == 'noUpdate'


@pytest.mark.parametrize('extra', [['../evil.txt'], ['blnext/../../evil.txt'], ['/abs.txt'], ['C:/x.txt'], ['other/x.txt']])
def test_extract_rejects_zip_slip(tmp_path, extra):
    z = tmp_path / 'r.zip'
    z.write_bytes(make_zip(tmp_path, 'v0.3.0', extra=extra))
    with pytest.raises(au.UpdateError) as e:
        au.extract_release(str(z), str(tmp_path / 'new'), 'v0.3.0')
    assert e.value.code == 'badZip'
    assert not (tmp_path / 'new').exists() and not (tmp_path / 'evil.txt').exists()


def test_extract_rejects_other_version(tmp_path):
    z = tmp_path / 'r.zip'
    z.write_bytes(make_zip(tmp_path, 'v0.2.0'))
    with pytest.raises(au.UpdateError) as e:
        au.extract_release(str(z), str(tmp_path / 'new'), 'v0.3.0')
    assert e.value.code == 'badZip' and e.value.detail == 'version'


# ---- 差し替え役の起動 ----

def test_apply_spawns_new_exe(dist, tmp_path, app_dir, monkeypatch):
    publish(dist, tmp_path)
    u = updater(app_dir)
    with pytest.raises(au.UpdateError) as e:
        u.apply('ja')
    assert e.value.code == 'notReady'
    u.start_download()
    wait_job(u)
    got = []
    monkeypatch.setattr(au, '_spawn', lambda args, cwd: got.append((args, cwd)))
    u.apply('en')
    new = app_dir / '_update' / 'new' / au.ZIP_TOP
    assert got == [([str(new / au.EXE_NAME), '--apply-update', '--target', str(app_dir), '--pid', str(os.getpid()), '--lang', 'en'], str(new))]


def test_env_for_child(monkeypatch):
    for k in ('_PYI_APPLICATION_HOME_DIR', '_MEIPASS2', 'BLN_BASE_DIR', 'BLN_DATA_DIR'):
        monkeypatch.setenv(k, 'x')
    monkeypatch.setenv('KEEP_ME', '1')
    e = au._env_for_child()
    assert e['PYINSTALLER_RESET_ENVIRONMENT'] == '1' and e['KEEP_ME'] == '1'
    assert not any(k in e for k in ('_PYI_APPLICATION_HOME_DIR', '_MEIPASS2', 'BLN_BASE_DIR', 'BLN_DATA_DIR'))


def test_spawn_goes_through_launch(monkeypatch):
    # DLL の探し先を引き継がせない口（core/launch.py）を通す
    got = []
    monkeypatch.setattr(launch, 'spawn', lambda args, cwd, env=None: got.append((args, cwd, env['PYINSTALLER_RESET_ENVIRONMENT'])))
    au._spawn(['x.exe'], 'd')
    assert got == [(['x.exe'], 'd', '1')]


# ---- 差し替え ----

def stage(app_dir, tmp_path, ver='v0.3.0'):
    """_update/new/blnext/ に新しい版を置いて、その場所と引数を返す"""
    src = app_dir / au.UPDATE_DIR / 'new' / au.ZIP_TOP
    make_app(src, ver)
    return src, ['x', '--apply-update', '--target', str(app_dir), '--pid', '0', '--lang', 'ja']


def test_apply_main_swaps(app_dir, tmp_path, boxes):
    src, argv = stage(app_dir, tmp_path)
    assert au.apply_main(argv, src=str(src), launch_after=False)
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.3.0' and (app_dir / au.EXE_NAME).read_bytes() == b'EXEv0.3.0'
    assert (app_dir / 'LICENSE').read_text() == 'v0.3.0'
    bk = app_dir / '_previous_v0.1.0'     # 前の版は1世代だけ残す
    assert (bk / au.EXE_NAME).read_bytes() == b'EXEv0.1.0' and au.read_app_version(str(bk / '_internal')) == 'v0.1.0'
    for sub in ('config', 'data', 'logs', 'exports'):     # 利用者のデータには触らない
        assert (app_dir / sub / 'mine.txt').read_text() == sub
    assert json.loads((app_dir / au.UPDATE_DIR / 'applied.json').read_text()) == {'from': 'v0.1.0', 'to': 'v0.3.0'}
    assert boxes == []


def test_apply_main_keeps_one_previous(app_dir, tmp_path, boxes):
    write(app_dir / '_previous_v0.0.9' / 'old.txt', 'x')
    src, argv = stage(app_dir, tmp_path)
    assert au.apply_main(argv, src=str(src), launch_after=False)
    assert not (app_dir / '_previous_v0.0.9').exists() and (app_dir / '_previous_v0.1.0').is_dir()


def test_apply_main_previous_stuck(app_dir, tmp_path, boxes, monkeypatch):
    # 同じ版の前の退避が消せずに残っていても（外のアプリがつかんでいる）、混ぜずに別の名前へ退避して更新できる
    write(app_dir / '_previous_v0.1.0' / '_internal' / 'stuck.dll', 'x')
    monkeypatch.setattr(au, '_clean_previous', lambda target, keep=None: None)
    src, argv = stage(app_dir, tmp_path)
    assert au.apply_main(argv, src=str(src), launch_after=False)
    assert (app_dir / '_previous_v0.1.0' / '_internal' / 'stuck.dll').is_file()
    assert (app_dir / '_previous_v0.1.0-2' / au.EXE_NAME).read_bytes() == b'EXEv0.1.0'


def test_apply_main_refuses_other_place(app_dir, tmp_path, boxes):
    # 自分が target の _update/new/blnext にいないと動かない（任意のフォルダを書き換えさせない）
    other = tmp_path / 'other'
    make_app(other, 'v0.3.0')
    argv = ['x', '--apply-update', '--target', str(app_dir), '--pid', '0']
    assert not au.apply_main(argv, src=str(other), launch_after=False)
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.1.0' and len(boxes) == 1


def test_apply_main_refuses_bad_package(app_dir, tmp_path, boxes):
    src, argv = stage(app_dir, tmp_path)
    (src / au.EXE_NAME).unlink()
    assert not au.apply_main(argv, src=str(src), launch_after=False)
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.1.0' and 'bad package' in boxes[0]


def test_apply_main_rolls_back(app_dir, tmp_path, boxes, monkeypatch):
    # 途中で失敗したら元の版に戻し、何が使っているかを案内する
    src, argv = stage(app_dir, tmp_path)
    monkeypatch.setattr(au, '_retry', lambda fn, tries=40, wait=0.25: fn())
    monkeypatch.setattr(au, '_busy_apps', lambda paths: ['chrome-native-host.exe'] if paths else [])

    def broken(s, d):   # 途中まで写して失敗する
        os.makedirs(d)
        raise OSError('disk full')
    monkeypatch.setattr(au.shutil, 'copytree', broken)
    launched = []
    monkeypatch.setattr(au, '_spawn', lambda args, cwd: launched.append(args))
    assert not au.apply_main(argv, src=str(src))
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.1.0' and (app_dir / au.EXE_NAME).read_bytes() == b'EXEv0.1.0'
    assert (app_dir / 'LICENSE').read_text() == 'v0.1.0'
    assert not (app_dir / '_previous_v0.1.0').exists() and not (app_dir / au.UPDATE_DIR / 'applied.json').exists()
    assert 'chrome-native-host.exe' in boxes[0] and 'disk full' in boxes[0]
    assert launched == [[str(app_dir / au.EXE_NAME)]]     # 元の版を起動し直す


def test_apply_main_in_use_unknown_app(app_dir, tmp_path, boxes, monkeypatch):
    src, argv = stage(app_dir, tmp_path)
    monkeypatch.setattr(au, '_retry', lambda fn, tries=40, wait=0.25: (_ for _ in ()).throw(PermissionError('busy')))
    assert not au.apply_main(argv, src=str(src), launch_after=False)
    assert 'エクスプローラー' in boxes[0]


def test_apply_main_waits_for_old_app(app_dir, tmp_path, boxes, monkeypatch):
    src, argv = stage(app_dir, tmp_path)
    monkeypatch.setattr(au, '_wait_pid', lambda pid, timeout: False)
    assert not au.apply_main(argv, src=str(src), launch_after=False)
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.1.0' and '終了しない' in boxes[0]


@pytest.mark.skipif(sys.platform != 'win32', reason='Restart Manager')
def test_busy_apps_names_the_holder(tmp_path):
    p = tmp_path / 'held.dll'
    p.write_bytes(b'x')
    assert au._busy_apps([str(p)]) == []    # だれも使っていない
    with open(p, 'rb'):
        assert au._busy_apps([str(p)])      # このプロセス（python）の名前が出る
    assert au._busy_apps([]) == []


# ---- 起動し直した後の片付け ----

def wait_gone(p, timeout=10):
    end = time.time() + timeout
    while time.time() < end and p.exists():
        time.sleep(0.05)
    return not p.exists()


def test_startup_after_update(app_dir):
    write(app_dir / au.UPDATE_DIR / 'applied.json', json.dumps({'from': 'v0.1.0', 'to': 'v0.3.0'}))
    u = updater(app_dir)
    au.startup(u)
    assert u.just_updated == {'from': 'v0.1.0', 'to': 'v0.3.0'}
    assert wait_gone(app_dir / au.UPDATE_DIR)


def test_startup_cleans_old_previous(app_dir):
    # 前の差し替えで消せなかった古い退避を消し直す。一番新しい退避は残す
    write(app_dir / '_previous_v0.0.1' / 'a.txt', 'x')
    write(app_dir / '_previous_v0.0.2' / 'a.txt', 'x')
    os.utime(app_dir / '_previous_v0.0.1', (1000, 1000))
    au.startup(updater(app_dir))
    assert wait_gone(app_dir / '_previous_v0.0.1')
    assert (app_dir / '_previous_v0.0.2').is_dir()


def test_startup_does_nothing_in_dev(app_dir):
    write(app_dir / au.UPDATE_DIR / 'applied.json', '{"to": "v0.3.0"}')
    u = updater(app_dir, frozen=False)
    au.startup(u)
    time.sleep(0.05)
    assert u.just_updated is None and (app_dir / au.UPDATE_DIR).is_dir()


def test_startup_ignores_broken_record(app_dir):
    write(app_dir / au.UPDATE_DIR / 'applied.json', 'not json')
    u = updater(app_dir)
    au.startup(u)
    assert u.just_updated is None and wait_gone(app_dir / au.UPDATE_DIR)


# ---- サーバーの口 ----

class FakeUpdater:
    frozen, just_updated = False, None

    def __init__(self, latest='v0.3.0'):
        self.latest = latest

    def check(self):
        return {'current': 'v0.1.0', 'latest': self.latest, 'newer': True, 'notes': [], 'size': 1, 'auto': 'ok'}


def test_api_check_skipped(server, monkeypatch, tmp_path):
    from core.settings import Settings
    s = Settings(str(tmp_path))
    monkeypatch.setattr(serve, 'SETTINGS', s)
    monkeypatch.setattr(serve, 'UPDATER', FakeUpdater())
    assert call(server, 'update/check')['skipped'] is False
    assert call(server, 'settings/save', {'changes': {'update_skip': 'v0.3.0'}})['ok']
    assert call(server, 'update/check')['skipped'] is True          # 「更新しない」を選んだ版は起動時に出さない
    monkeypatch.setattr(serve, 'UPDATER', FakeUpdater('v0.3.1'))
    assert call(server, 'update/check')['skipped'] is False         # それより新しい版は出す
    assert call(server, 'settings/save', {'changes': {'update_skip': '../x'}})['error'] == 'bad-value'


def test_api_check_error_is_passed(server):
    r = call(server, 'update/check')    # BLN_NO_NET なので本物の配布元へ出ない
    assert r == {'ok': False, 'error': 'network', 'detail': 'BLN_NO_NET'}


def test_api_open_only_fixed_pages(server, monkeypatch):
    opened = []
    monkeypatch.setattr(serve, 'open_browser', opened.append)
    assert call(server, 'update/open', {'what': 'releases'})['ok'] and call(server, 'update/open', {'what': 'repo'})['ok']
    assert opened == [au.RELEASES_URL, au.REPO_URL]
    for bad in ('https://evil.example/', None, 5):
        assert call(server, 'update/open', {'what': bad})['error'] == 'bad-request'
    assert len(opened) == 2


def test_update_skip_does_not_rebuild_lists():
    # 「更新しない」を選んでも、保存したリストは作り直さない
    assert 'update_skip' in serve._NOT_LIST_KEYS


# ---- 配布の道具 ----

@pytest.fixture
def release(tmp_path, monkeypatch):
    """作り物のビルド結果と CHANGELOG（版は v0.3.0）"""
    d = tmp_path / 'dist' / 'blnext'
    make_app(d, 'v0.3.0')
    write(d / 'config' / 'settings.json', 'PRIVATE')   # dist の exe を起動してできた個人のデータ
    log = tmp_path / 'CHANGELOG.md'
    log.write_text('# 更新履歴\n\n## v0.3.0\n\n- **新しい機能**（補足）\n- 細かいこと\n- **`コード` と [リンク](http://x) の見出し**\n\n## v0.2.0\n\n- **前の機能**\n', encoding='utf-8')
    monkeypatch.setattr(make_release, 'DIST', d)
    monkeypatch.setattr(make_release, 'OUT', tmp_path / 'dist' / 'release')
    monkeypatch.setattr(make_release, 'CHANGELOG', log)
    monkeypatch.setattr(make_release, 'ROOT', tmp_path)
    monkeypatch.setattr(au, 'read_app_version', lambda root: 'v0.3.0')
    return tmp_path


def test_make_release(release, monkeypatch):
    make_release.main()
    out = release / 'dist' / 'release'
    m = json.loads((out / 'update.json').read_text(encoding='utf-8'))
    data = (out / 'blnext-v0.3.0.zip').read_bytes()
    assert m['zip'] == {'name': 'blnext-v0.3.0.zip', 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    assert m['notes'] == [{'version': 'v0.3.0', 'ja': ['新しい機能', 'コード と リンク の見出し'], 'en': []},
                          {'version': 'v0.2.0', 'ja': ['前の機能'], 'en': []}]
    assert not au.check_manifest(json.dumps(m))['manual']
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert 'blnext/blnext.exe' in names and 'blnext/はじめにお読みください.txt' in names and 'blnext/_internal/lib/x.dll' in names
    assert not any('config' in n or 'settings.json' in n for n in names)   # 個人のデータを入れない


def test_make_release_stops_on_version_mismatch(release):
    (release / 'CHANGELOG.md').write_text('## v0.2.9\n\n- **別の版**\n', encoding='utf-8')
    with pytest.raises(SystemExit):
        make_release.main()


def test_make_release_stops_on_missing_item(release):
    (make_release.DIST / 'LICENSE').unlink()
    with pytest.raises(SystemExit):
        make_release.main()


def test_replace_items_ship_with_the_repo():
    # 案内文とライセンスは blnext.spec がビルドの最後に写す。無いとビルドが止まる
    spec = (ROOT / 'blnext.spec').read_text(encoding='utf-8')
    for n in au.REPLACE_ITEMS[2:]:
        assert n in spec and (ROOT / n).is_file()


def test_real_changelog_is_valid():
    ver = au.read_app_version(str(ROOT))
    notes = make_release.build_notes(ver)
    assert notes[0]['version'] == ver and notes[0]['ja']
