# 外のプログラム（ブラウザ・BSManager など）を起動する口。外を起動する時は必ずここを通す。
# exe 版（PyInstaller）は、起動の時に DLL の探し先へ自分の _internal を足している（SetDllDirectoryW）。これは起動した子と、その先の
# プログラムにまで引き継がれ、外のアプリが Windows の物より先に blnext の DLL を読み込んでつかみ続ける（2026-10-17: blnext が開いた
# Chrome の拡張の補助プロセスが _internal の VCRUNTIME140.dll をつかみ、exe の差し替えが止まった。自動更新でも同じことが起きる。
# docs/investigation.md の「使用中のファイルと自動更新」）。そこで、起動する直前だけ探し先を戻し、起動したらすぐ元に戻す
# （起動した時に戻しっぱなしにすると、画面の部品が名前だけで読む DLL を探せなくなるおそれがあるため）。
# 作業フォルダもアプリの外にする（引き継がれた作業フォルダは、アプリのフォルダの名前の変更・削除を止める）。
import contextlib, ctypes, os, subprocess, sys, threading

_lock = threading.Lock()   # 戻している間に、別のスレッドが外を起動して元の値を覚え違えないように


def _kernel32():
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetDllDirectoryW.argtypes = [w.DWORD, w.LPWSTR]
    k.GetDllDirectoryW.restype = w.DWORD
    k.SetDllDirectoryW.argtypes = [w.LPCWSTR]
    k.SetDllDirectoryW.restype = w.BOOL
    return k


def dll_directory():
    """今の DLL の探し先（SetDllDirectoryW で足した所）。無ければ ''"""
    if sys.platform != 'win32':
        return ''
    buf = ctypes.create_unicode_buffer(32768)
    n = _kernel32().GetDllDirectoryW(len(buf), buf)
    return buf.value if 0 < n < len(buf) else ''


def outside_dir():
    """外のプログラムの作業フォルダ（アプリのフォルダの外）"""
    for d in (os.path.expanduser('~'), os.environ.get('SystemRoot')):
        if d and os.path.isdir(d):
            return d
    return None


@contextlib.contextmanager
def _no_dll_directory():
    """この中で起動した子へ DLL の探し先を引き継がせない（出る時に元に戻す）"""
    with _lock:
        old = dll_directory()
        k = _kernel32() if old else None
        if old:
            k.SetDllDirectoryW(None)
        try:
            yield
        finally:
            if old:
                k.SetDllDirectoryW(old)


def start(target, show_cmd=1):
    """target（URL・bsplaylist:// のリンク・ファイル）を、関連付けられたプログラムで開く（os.startfile）。
    DLL の探し先と作業フォルダを引き継がせない。失敗したら OSError。show_cmd は ShellExecute の表示の仕方（テストで 0＝隠す）"""
    if sys.platform != 'win32':   # 開発用（Windows 以外）。探し先の問題は無い
        import webbrowser
        if not webbrowser.open(target):
            raise OSError('no browser')
        return
    with _no_dll_directory():
        os.startfile(target, cwd=outside_dir(), show_cmd=show_cmd)


def spawn(args, cwd, env=None):
    """別の exe を、このアプリが終わっても動き続けるように切り離して起動する（更新の差し替え役・更新の後の起動し直し。core/app_update.py）。
    DLL の探し先を引き継がせない（作業フォルダは呼ぶ側が決める）。失敗したら OSError"""
    flags = 0x00000008 | 0x00000200 if sys.platform == 'win32' else 0   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with _no_dll_directory():
        subprocess.Popen(args, cwd=cwd, env=env, close_fds=True, creationflags=flags)
