# 二重起動の防止（core/single_instance.py）。名前付きミューテックスは本物を使い、別のスレッド・別のプロセス（子の python）が
# 持っている時を確かめる。本物の窓を前に出す処理（activate）・知らせ（MessageBox）は呼ばない（差し替える）
import json, os, signal, subprocess, sys, threading, time, types

import pytest

from core import app_update, single_instance as si

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows の仕組み')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 子のプロセス: 書き込み先 argv[2] の印を取り、取れたかと自分の pid を出してから、親が標準入力を閉じるまで持つ。
# 閉じたら印を放さずに終わる（アプリと同じ＝OS が放す）
CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from core import single_instance as si
lock = si.claim(sys.argv[2], wait=0, find=lambda pid: None, notify=lambda root: None)
print('held' if lock is not None and lock.held else 'busy', os.getpid(), flush=True)
sys.stdin.readline()
'''


def never(*a):
    raise AssertionError('呼ばれないはず: %r' % (a,))


@pytest.fixture
def child():
    """子のプロセスに印を持たせる。start(書き込み先) → 子の pid（venv の python は起動役で、中の python が別の pid になるので
    子が名乗った pid を使う）。end(pid) で普通に終わらせる。テストの後は残さない"""
    procs = []

    def start(root):
        p = subprocess.Popen([sys.executable, '-c', CHILD, ROOT, str(root)], cwd=str(root), text=True,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=0x08000000)   # CREATE_NO_WINDOW
        procs.append(p)
        state, pid = p.stdout.readline().split()
        assert state == 'held'
        p.real_pid = int(pid)
        return int(pid)

    def end(pid):
        p = next(p for p in procs if p.real_pid == pid)
        p.stdin.close()
        p.wait(10)
    start.end = end
    yield start
    for p in procs:
        if p.poll() is None:
            try:
                os.kill(p.real_pid, signal.SIGTERM)
            except OSError:
                pass
            p.wait(10)


class ThreadHolder:
    """別のスレッドで get() の印を取って持つ。end() で放す（release=False なら放さずにスレッドを終える＝持ち主が落ちた時と同じ）"""
    def __init__(self, get, release=True):
        self.lock, got, self._stop = None, threading.Event(), threading.Event()

        def run():
            self.lock = get()
            got.set()
            self._stop.wait(10)
            if release and self.lock is not None:
                self.lock.close()
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        assert got.wait(10)

    def end(self):
        self._stop.set()
        self._t.join(10)


def claim(root, **kw):
    """本物の窓・知らせに触らない claim"""
    kw.setdefault('find', lambda pid: None)
    kw.setdefault('front', never)
    kw.setdefault('notify', never)
    return si.claim(str(root), **kw)


def owner_pid(root):
    return json.loads((root / 'data' / 'instance.json').read_text(encoding='utf-8'))['pid']


# ---- 名前（書き込み先ごと） ----

def test_mutex_name_is_per_data_root(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    n = si.mutex_name(str(a))
    assert n.startswith('Local\\blnext-') and '\\' not in n[len('Local\\'):]   # 名前に '\' は使えない（場所はハッシュ）
    for same in (str(a).upper(), str(a).replace('\\', '/'), str(a) + '\\', str(b / '..' / 'a')):
        assert si.mutex_name(same) == n, same   # 同じフォルダなら書き方が違っても同じ
    assert si.mutex_name(str(b)) != n


# ---- 取る・放す（別のスレッド） ----

def test_second_claim_is_busy_until_released(tmp_path):
    first = ThreadHolder(lambda: claim(tmp_path))
    assert first.lock is not None and first.lock.held
    assert owner_pid(tmp_path) == os.getpid()   # 前の窓を探すための pid
    t0 = time.monotonic()
    assert claim(tmp_path, wait=0, notify=lambda root: None) is None   # 2回目は「動いている」
    assert time.monotonic() - t0 < 1
    other = si.Lock(si.mutex_name(str(tmp_path)))
    try:
        assert other.acquire() is False and other.acquire(0.1) is False
        first.end()   # 放した後は取れる
        assert other.acquire() is True
    finally:
        other.close()


def test_other_data_root_is_separate(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    first = ThreadHolder(lambda: claim(a))
    try:
        lock = claim(b, wait=0)   # 別の書き込み先（例: 開発中のリポジトリ直下と配布フォルダ）は同時に開ける
        assert lock is not None and lock.held
        lock.close()
    finally:
        first.end()


def test_abandoned_lock_can_be_taken(tmp_path):
    # 持ち主が放さずに終わった（落ちた・強制終了）＝OS が放す。次の起動は開ける
    first = ThreadHolder(lambda: claim(tmp_path), release=False)
    first.end()
    lock = claim(tmp_path, wait=0)
    assert lock is not None and lock.held
    lock.close()


def free_elsewhere(root):
    """別のスレッドから取れるか（取れたらすぐ放す）"""
    res = []

    def run():
        lock = si.Lock(si.mutex_name(str(root)))
        res.append(lock.acquire())
        lock.close()
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    return res[0]


def test_same_thread_can_claim_again(tmp_path):
    # 同じスレッドなら重ねて取れる（単体テストが同じプロセスで main() を何度も呼べる）。全部閉じるまで、ほかは取れない
    a, b = claim(tmp_path, wait=0), claim(tmp_path, wait=0)
    assert a.held and b.held
    b.close()
    assert free_elsewhere(tmp_path) is False
    a.close()
    assert free_elsewhere(tmp_path) is True


# ---- 前の窓を前に出す・待つ・知らせる ----

def test_brings_existing_window_to_front(tmp_path):
    first = ThreadHolder(lambda: claim(tmp_path))
    fronted = []
    try:
        t0 = time.monotonic()
        r = claim(tmp_path, find=lambda pid: 777 if pid == os.getpid() else None, front=fronted.append)
        assert r is None and fronted == [777] and time.monotonic() - t0 < 1   # 見つかればすぐ前に出して終わる（待たない）
    finally:
        first.end()


def test_waits_for_window_while_previous_is_starting(tmp_path):
    first = ThreadHolder(lambda: claim(tmp_path))
    calls, fronted = [], []

    def find(pid):
        calls.append(pid)
        return 555 if len(calls) >= 4 else None   # 前のアプリが起動の途中で、窓は少し後に出る
    try:
        assert claim(tmp_path, wait=10, find=find, front=fronted.append) is None
        assert fronted == [555] and len(calls) == 4
    finally:
        first.end()


def test_notifies_when_no_window_appears(tmp_path):
    first = ThreadHolder(lambda: claim(tmp_path))
    told = []
    try:
        t0 = time.monotonic()
        assert claim(tmp_path, wait=0.6, notify=told.append) is None
        assert told == [str(tmp_path)] and 0.5 < time.monotonic() - t0 < 3
    finally:
        first.end()


def test_stale_owner_file_is_ignored(tmp_path):
    (tmp_path / 'data').mkdir()
    f = tmp_path / 'data' / 'instance.json'
    assert si.read_owner(str(tmp_path)) is None   # 無い
    me = os.getpid()
    for bad in ('{', '[]', '{"pid": "x"}', json.dumps({'pid': me})):
        f.write_text(bad, encoding='utf-8')
        assert si.read_owner(str(tmp_path)) is None, bad
    created = si.process_created(me)
    assert created
    f.write_text(json.dumps({'pid': me, 'created': created + 1}), encoding='utf-8')
    assert si.read_owner(str(tmp_path)) is None   # 作られた時刻が違う＝pid が使い回された別のプロセス
    f.write_text(json.dumps({'pid': 0, 'created': created}), encoding='utf-8')
    assert si.read_owner(str(tmp_path)) is None
    f.write_text(json.dumps({'pid': me, 'created': created}), encoding='utf-8')
    assert si.read_owner(str(tmp_path)) == me


# ---- 別のプロセスが持っている時（子の python） ----

def test_other_process_holds(tmp_path, child):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    pid = child(a)
    assert pid != os.getpid() and si.read_owner(str(a)) == pid
    assert si.find_window(pid) is None   # 窓の無いプロセス（EnumWindows で探すだけ。窓には触らない）
    told = []
    assert claim(a, wait=0.3, notify=told.append) is None and told == [str(a)]
    lock = claim(b, wait=0)   # 書き込み先が違えば開ける
    assert lock is not None and lock.held
    lock.close()
    child.end(pid)   # 終わった後は開ける
    lock = claim(a, wait=0)
    assert lock is not None and lock.held and owner_pid(a) == os.getpid()
    lock.close()


def test_killed_process_does_not_leave_the_lock(tmp_path, child):
    pid = child(tmp_path)
    assert claim(tmp_path, wait=0, notify=lambda root: None) is None
    os.kill(pid, signal.SIGTERM)   # TerminateProcess＝強制終了・落ちた時と同じ
    assert app_update._wait_pid(pid, 10)
    lock = claim(tmp_path, wait=0)
    assert lock is not None and lock.held
    lock.close()


def test_takes_over_when_previous_exits_while_waiting(tmp_path, child):
    # 前のアプリが閉じている途中（窓はもう無いがプロセスが残っている）: 終わるのを待って自分が開く
    pid = child(tmp_path)
    threading.Timer(0.5, child.end, args=(pid,)).start()
    t0 = time.monotonic()
    lock = claim(tmp_path, wait=10)
    assert lock is not None and lock.held and time.monotonic() - t0 < 8
    lock.close()


def test_update_relaunch_is_not_blocked(tmp_path, child):
    # 自動更新: 差し替え役（apply_main）は旧版のプロセスの終了を _wait_pid で待ってから、配布フォルダの exe を普通に起動し直す。
    # プロセスの終わりが知らされた時には OS が印を放しているので、起動し直した版は待たずに開ける
    pid = child(tmp_path)
    threading.Timer(0.3, child.end, args=(pid,)).start()
    assert app_update._wait_pid(pid, 10)
    lock = claim(tmp_path, wait=0)
    assert lock is not None and lock.held
    lock.close()


# ---- 窓を前に出す処理（本物の user32 の代わりに偽物） ----

class FakeUser32:
    def __init__(self, iconic, foreground):
        self.iconic, self.foreground, self.calls = iconic, foreground, []

    def IsIconic(self, h):
        return self.iconic

    def ShowWindowAsync(self, h, cmd):
        self.calls.append(('show', h, cmd))
        return True

    def SetForegroundWindow(self, h):
        self.calls.append(('front', h))
        return self.foreground

    def FlashWindowEx(self, p):
        self.calls.append(('flash', p._obj.hwnd, p._obj.dwFlags))
        return True


def test_activate_restores_and_flashes_when_refused(monkeypatch):
    si._u32()   # 本物を読み込むだけ（FLASHWINFO の形を作る）
    u = FakeUser32(iconic=True, foreground=True)
    monkeypatch.setattr(si, '_u32', lambda: u)
    assert si.activate(42) is True
    assert u.calls == [('show', 42, 9), ('front', 42)]   # 最小化されていれば元に戻す（SW_RESTORE）
    u = FakeUser32(iconic=False, foreground=False)
    monkeypatch.setattr(si, '_u32', lambda: u)
    assert si.activate(42) is False
    assert u.calls == [('front', 42), ('flash', 42, 0x2 | 0xC)]   # 前に出せなければタスクバーで点滅（前に出るまで）


def test_notify_uses_language_setting(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(si, '_u32', lambda: types.SimpleNamespace(MessageBoxW=lambda h, text, title, flags: shown.append((text, title))))
    si.notify_no_window(str(tmp_path))   # 設定が無い＝日本語
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'settings.json').write_text(json.dumps({'lang': 'en', 'presets': []}), encoding='utf-8')
    si.notify_no_window(str(tmp_path))
    (tmp_path / 'config' / 'settings.json').write_text('[]', encoding='utf-8')   # 壊れていれば日本語
    si.notify_no_window(str(tmp_path))
    assert [s[0] for s in shown] == [si._MSG['ja'], si._MSG['en'], si._MSG['ja']] and {s[1] for s in shown} == {'blnext'}


# ---- 調べられない時・Windows 以外は止めない ----

def test_does_not_block_when_check_fails(tmp_path, monkeypatch):
    def boom(name):
        raise OSError(5, 'CreateMutexW')
    monkeypatch.setattr(si, 'Lock', boom)
    assert claim(tmp_path) is si.NOT_CHECKED


def test_nothing_on_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(si, 'Lock', never)
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert claim(tmp_path) is si.NOT_CHECKED
