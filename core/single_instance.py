# 二重起動の防止（app.py の main から、画面を出す普通の起動の時だけ使う）。同じ書き込み先（exe の隣の config/ data/ …）の
# blnext がもう動いていたら、そちらの窓を前に出し（最小化されていれば元に戻す）、自分はサーバーも窓も作らずに終わる。
# 2つ開くと、同じ DB を両方が書き換え、前回の取得から時間がたっていれば両方が同時に BeatLeader から取り始め、窓の位置の記録も取り合う。
# 書き込み先ごとにするのは、開発中のリポジトリ直下と配布フォルダのように、データが別の物は同時に開けてよいため。
#  - 「動いている」の印は Windows の名前付きミューテックス（名前は書き込み先の場所から決める）。持っているプロセスが終われば
#    （強制終了・落ちた時も）OS が放すので、印が残って開けなくなることは無い。持つのは main() を呼んだスレッド（メインのスレッド。
#    webview.start() が窓を閉じるまでそこで止まる）で、自分では放さずプロセスが終わるまで持つ（終わりぎわの書き込みと次の起動を重ねない）。
#    ミューテックスは同じスレッドなら重ねて取れる（単体テストが同じプロセスで main() を何度も呼べるのはこのため）。
#  - 前の窓は、印を持つプロセスが書き込み先に残した pid（OWNER_FILE。作られた時刻も残して、pid の使い回しと見分ける）の、
#    持ち主（owner）の無い見える窓。
#  - 前のアプリが起動の途中（まだ窓が無い）・閉じている途中の時は、窓が出るか印が放されるまで WAIT 秒まで待つ（放されたら自分が開く）。
#    それでも窓が無い時（止まったままのプロセスが印を持っている）は、そう知らせて終わる（黙って終わると、開けない理由が分からない）。
#  - 自動更新の起動し直し（core/app_update.py の apply_main）は、旧版のプロセスが終わるのを待ってから起動するので、印は放されている。
#  - 調べられない時（ミューテックスを作れない等）は止めない（開けなくなるより、2つ開く方がまし）。Windows 以外（開発用）は何もしない。
#  - 外のプログラムは起動しない（窓を前に出すのは user32 の呼び出しだけ）。
import ctypes, hashlib, json, os, sys, time

from .fsutil import write_atomic

WAIT = 30.0          # 前のアプリの窓が出る（か終わる）のを待つ秒数。起動の遅い PC（ウイルス対策ソフトの初めての検査など）のため長め
POLL = 0.25          # 待つ間に窓を探し直す間隔（秒）
OWNER_FILE = os.path.join('data', 'instance.json')   # 書き込み先の中。印を持つプロセスの pid と作られた時刻
NAME_PREFIX = 'Local\\blnext-'                        # ミューテックスの名前（ログインしている人ごと）

_WAIT_OBJECT_0, _WAIT_ABANDONED, _WAIT_TIMEOUT = 0, 0x80, 0x102
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GW_OWNER, _SW_RESTORE = 4, 9
_FLASHW_TRAY, _FLASHW_TIMERNOFG = 0x2, 0xC
_MB_ICONWARNING, _MB_TOPMOST = 0x30, 0x40000

_MSG = {
    'ja': 'blnext はもう動いていますが、窓が見つかりませんでした。\n\n'
          '閉じている途中なら、少し待ってからもう一度開いてください。\n'
          'それでも開けない時は、タスクマネージャーで blnext を終了してから開いてください。',
    'en': 'blnext is already running, but its window was not found.\n\n'
          'If it is closing, wait a moment and open it again.\n'
          'If it still does not open, end blnext in Task Manager and open it again.',
}

_libs = {}


def _k32():
    if 'k' not in _libs:
        from ctypes import wintypes as w
        k = ctypes.WinDLL('kernel32', use_last_error=True)   # 自分用に読む（argtypes をほかのモジュールの windll と混ぜない）
        k.CreateMutexW.argtypes, k.CreateMutexW.restype = [w.LPVOID, w.BOOL, w.LPCWSTR], w.HANDLE
        k.WaitForSingleObject.argtypes, k.WaitForSingleObject.restype = [w.HANDLE, w.DWORD], w.DWORD
        k.ReleaseMutex.argtypes, k.ReleaseMutex.restype = [w.HANDLE], w.BOOL
        k.CloseHandle.argtypes, k.CloseHandle.restype = [w.HANDLE], w.BOOL
        k.OpenProcess.argtypes, k.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        ft = ctypes.POINTER(w.FILETIME)
        k.GetProcessTimes.argtypes, k.GetProcessTimes.restype = [w.HANDLE, ft, ft, ft, ft], w.BOOL
        _libs['k'] = k
    return _libs['k']


def _u32():
    if 'u' not in _libs:
        from ctypes import wintypes as w

        class FlashInfo(ctypes.Structure):   # FLASHWINFO
            _fields_ = [('cbSize', w.UINT), ('hwnd', w.HWND), ('dwFlags', w.DWORD), ('uCount', w.UINT), ('dwTimeout', w.DWORD)]
        _libs['flash'] = FlashInfo
        u = ctypes.WinDLL('user32', use_last_error=True)
        _libs['enum'] = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        u.EnumWindows.argtypes, u.EnumWindows.restype = [_libs['enum'], w.LPARAM], w.BOOL
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [w.HWND], w.BOOL
        u.GetWindow.argtypes, u.GetWindow.restype = [w.HWND, w.UINT], w.HWND
        u.IsIconic.argtypes, u.IsIconic.restype = [w.HWND], w.BOOL
        u.ShowWindowAsync.argtypes, u.ShowWindowAsync.restype = [w.HWND, ctypes.c_int], w.BOOL
        u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = [w.HWND], w.BOOL
        u.FlashWindowEx.argtypes, u.FlashWindowEx.restype = [ctypes.POINTER(FlashInfo)], w.BOOL
        u.MessageBoxW.argtypes, u.MessageBoxW.restype = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT], ctypes.c_int
        _libs['u'] = u
    return _libs['u']


def mutex_name(data_root):
    """書き込み先ごとのミューテックスの名前（同じフォルダなら書き方が違っても同じ名前。名前に '\\' は使えないので場所の SHA-256）"""
    key = os.path.normcase(os.path.realpath(os.path.abspath(data_root)))
    return NAME_PREFIX + hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]


class Lock:
    """名前付きミューテックス。acquire で取れたら True（持ち主が放さずに終わった物＝WAIT_ABANDONED も取れたとする）。
    作れない・待てない時は OSError"""
    def __init__(self, name):
        self.name, self.held = name, False
        self._h = _k32().CreateMutexW(None, False, name)
        if not self._h:
            raise OSError(ctypes.get_last_error(), 'CreateMutexW')

    def acquire(self, timeout=0.0):
        r = _k32().WaitForSingleObject(self._h, max(0, int(timeout * 1000)))
        if r in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            self.held = True
            return True
        if r == _WAIT_TIMEOUT:
            return False
        raise OSError(ctypes.get_last_error(), 'WaitForSingleObject')

    def close(self):
        """（持っていれば放して）閉じる。アプリは呼ばない（プロセスが終わるまで持ち、OS が放す）。テストと、持てなかった時の後始末用"""
        if self._h:
            if self.held:
                _k32().ReleaseMutex(self._h)
            _k32().CloseHandle(self._h)
        self._h, self.held = None, False


class _NotChecked:
    """Windows 以外・調べられなかった時の代わり（印は持たない。起動は止めない）"""
    held = False

    def close(self):
        pass


NOT_CHECKED = _NotChecked()


def process_created(pid):
    """プロセスの作られた時刻（FILETIME の数）。居ない・分からなければ None"""
    from ctypes import wintypes as w
    k = _k32()
    h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid) if pid and pid > 0 else None
    if not h:
        return None
    try:
        c, e, kt, ut = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None
        return (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        k.CloseHandle(h)


def _write_owner(data_root):
    try:
        path = os.path.join(data_root, OWNER_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, json.dumps({'pid': os.getpid(), 'created': process_created(os.getpid())}))
    except OSError:
        pass   # 書けなくても動く（2つ目が前の窓を見つけられず、待ってから知らせるだけ）


def read_owner(data_root):
    """印を持つプロセスの pid。記録が無い・壊れている・前の起動の残り（その pid のプロセスが居ない・作られた時刻が違う）なら None"""
    try:
        with open(os.path.join(data_root, OWNER_FILE), encoding='utf-8') as f:
            d = json.load(f)
        pid, created = int(d['pid']), int(d['created'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid if pid > 0 and process_created(pid) == created else None


def find_window(pid):
    """pid のプロセスの主の窓（持ち主の無い見える窓。ファイルのダイアログ・メッセージは主の窓が持ち主なので外れる）。無ければ None。
    最小化している窓も見える窓に入る"""
    from ctypes import wintypes as w
    u = _u32()
    found = []

    def each(hwnd, _):
        p = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, _GW_OWNER):
            found.append(hwnd)
            return False   # 見つけたらやめる
        return True
    u.EnumWindows(_libs['enum'](each), 0)
    return found[0] if found else None


def activate(hwnd):
    """窓を前に出す（最小化されていれば元に戻す）。前に出せたら True。
    SetForegroundWindow は前にいるプロセス（2つ目を起動したエクスプローラー）から起動された時などにしか効かないので、
    断られた時はタスクバーのボタンを点滅させる（その窓が前に出るまで）。止まった窓でも待たされないよう、元に戻すのは Async"""
    u = _u32()
    if u.IsIconic(hwnd):
        u.ShowWindowAsync(hwnd, _SW_RESTORE)
    if u.SetForegroundWindow(hwnd):
        return True
    fi = _libs['flash']
    u.FlashWindowEx(ctypes.byref(fi(ctypes.sizeof(fi), hwnd, _FLASHW_TRAY | _FLASHW_TIMERNOFG, 0, 0)))
    return False


def _lang(data_root):
    try:
        with open(os.path.join(data_root, 'config', 'settings.json'), encoding='utf-8') as f:
            return 'en' if json.load(f).get('lang') == 'en' else 'ja'
    except Exception:
        return 'ja'


def notify_no_window(data_root):
    """印を持つプロセスはいるのに、待っても窓が出なかった時の知らせ"""
    try:
        _u32().MessageBoxW(None, _MSG[_lang(data_root)], 'blnext', _MB_ICONWARNING | _MB_TOPMOST)
    except Exception:
        pass


def claim(data_root, wait=WAIT, find=find_window, front=activate, notify=notify_no_window):
    """この書き込み先のアプリとして動いてよければ、持ち続ける印（Lock。閉じないこと）を返す。
    ほかに動いていれば、その窓を前に出して None（呼ぶ側はサーバーも窓も作らずに終わる）。
    窓がまだ・もう無い時は wait 秒まで、窓が出るか印が放されるのを待つ。find・front・notify はテスト用の差し替え"""
    if sys.platform != 'win32':
        return NOT_CHECKED
    try:
        lock = Lock(mutex_name(data_root))
    except OSError:
        return NOT_CHECKED
    try:
        deadline, timeout = time.monotonic() + wait, 0.0
        while True:
            if lock.acquire(timeout):   # 待つ間の休みも兼ねる（前のアプリが終われば、すぐ取れる）
                _write_owner(data_root)
                return lock
            pid = read_owner(data_root)
            hwnd = find(pid) if pid else None
            if hwnd:
                front(hwnd)
                lock.close()
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                lock.close()
                notify(data_root)
                return None
            timeout = min(POLL, left)
    except OSError:
        lock.close()
        return NOT_CHECKED
