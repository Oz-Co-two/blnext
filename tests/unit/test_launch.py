# core/launch.py: 外のプログラムを起動する時に、DLL の探し先（SetDllDirectoryW）と作業フォルダを引き継がせない。
# exe 版（PyInstaller）は探し先に _internal を足していて、起動した子とその先にまで引き継がれる（docs/investigation.md の
# 「使用中のファイルと自動更新」）。ここでは exe 版の代わりにこのプロセスで探し先を足し、本物の子プロセス（.cmd → python）が
# 名前だけでその DLL を読めるかで確かめる。.cmd は隠して起動する（窓は出ない）。ネットには出ない
import ctypes, json, os, shutil, sys, time

import pytest

from core import launch

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows の仕組み')

PROBE = 'blnprobe_launch_test.dll'   # 足した探し先にだけ置く DLL（ほかに同じ名前は無い）
CHILD = r'''
import ctypes, json, os, sys
buf = ctypes.create_unicode_buffer(32768)
ctypes.windll.kernel32.GetDllDirectoryW(len(buf), buf)
try:
    ctypes.WinDLL(%r)
    probe = True
except OSError:
    probe = False
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    json.dump({'dll_dir': buf.value, 'probe': probe, 'cwd': os.getcwd()}, f)
''' % PROBE


@pytest.fixture
def child(tmp_path):
    """子を起動する .cmd と、子が結果を書くファイル。探し先にする所（dlldir）に PROBE を置く"""
    dlldir = tmp_path / 'dlldir'
    dlldir.mkdir()
    shutil.copy(os.path.join(os.environ['SystemRoot'], 'System32', 'version.dll'), dlldir / PROBE)
    script = tmp_path / 'child.py'
    script.write_text(CHILD, encoding='utf-8')
    out = tmp_path / 'out.json'
    cmd = tmp_path / 'run.cmd'
    cmd.write_text('@"%s" "%s" "%s"\r\n' % (sys.executable, script, out), encoding='mbcs')
    return {'dlldir': str(dlldir), 'cmd': str(cmd), 'out': out}


def _set_dll_dir(path):
    ctypes.windll.kernel32.SetDllDirectoryW(path)


def _wait(out):
    end = time.time() + 20
    while time.time() < end:
        if out.exists():
            try:
                return json.loads(out.read_text(encoding='utf-8'))
            except ValueError:
                pass   # 書いている途中
        time.sleep(0.05)
    raise AssertionError('子が結果を書かなかった')


@pytest.fixture
def leaky(child):
    """このプロセスの探し先に dlldir を足す（exe 版のまね）。終わったら元に戻す"""
    old = launch.dll_directory()
    _set_dll_dir(child['dlldir'])
    yield child
    _set_dll_dir(old or None)


def test_plain_startfile_leaks(leaky):
    # 前提の確かめ: そのまま os.startfile で起動すると、子は親の探し先を引き継ぎ、そこの DLL を名前だけで読める（今回の不具合）
    os.startfile(leaky['cmd'], show_cmd=0)
    r = _wait(leaky['out'])
    assert r['probe'] is True and os.path.normcase(r['dll_dir']) == os.path.normcase(leaky['dlldir'])


def test_start_does_not_leak(leaky):
    # launch.start で起動すると、子には探し先も作業フォルダも引き継がれない。親の探し先は元に戻る
    back = os.getcwd()
    os.chdir(leaky['dlldir'])   # 親の作業フォルダ（アプリのフォルダのつもり）
    try:
        launch.start(leaky['cmd'], show_cmd=0)
    finally:
        os.chdir(back)
    r = _wait(leaky['out'])
    assert r['probe'] is False and r['dll_dir'] == ''
    assert os.path.normcase(r['cwd']) == os.path.normcase(launch.outside_dir())
    assert os.path.normcase(launch.dll_directory()) == os.path.normcase(leaky['dlldir'])


def test_start_without_dll_dir_and_failure(child, tmp_path):
    # 探し先を足していない時（開発中の python serve.py）はそのまま起動する。開けなければ OSError（探し先は変わらない）
    assert launch.dll_directory() == ''
    launch.start(child['cmd'], show_cmd=0)
    assert _wait(child['out'])['probe'] is False
    with pytest.raises(OSError):
        launch.start(str(tmp_path / 'no-such-file.cmd'), show_cmd=0)
    assert launch.dll_directory() == ''


def test_dll_dir_restored_even_if_start_fails(leaky, tmp_path):
    with pytest.raises(OSError):
        launch.start(str(tmp_path / 'no-such-file.cmd'), show_cmd=0)
    assert os.path.normcase(launch.dll_directory()) == os.path.normcase(leaky['dlldir'])
