# 公開の直前だけ流すテスト（tools/run_tests.py release。普段の全部のテストには入らない）。アプリの中の更新（core/app_update.py）の、
# 失敗した時の分岐を確かめる。普段のテスト（tests/unit/test_app_update.py）が通る道はそちらで確かめているので、ここはそこで通っていない所
# （2026-11-09 のカバレッジで 51行）を押さえる。更新は公開した後に一番困る所（旧版の利用者が更新できなくなると、手で入れ直してもらうしか
# ない）なので、GitHub に公開するたびに、その前にこれが通ることを確かめる。落ちたら公開しないで直す（CLAUDE.md の「配布の手順」・
# tools/make_release.py も最初にこれを流す）。本物のメッセージボックス・本物の Restart Manager の失敗は起こせないので差し替える
import ctypes, json, shutil, sys, threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'unit'))
from core import app_update as au  # noqa: E402
from test_app_update import (app_dir, boxes, dist, make_zip, manifest, publish, stage, updater,  # noqa: E402,F401（fixture も使う）
                             wait_job, write)

WIN = pytest.mark.skipif(sys.platform != 'win32', reason='Windows の API')


def join_clean():
    """startup が裏で始める片付けの終わりを待つ"""
    for t in threading.enumerate():
        if t.name == 'update-clean':
            t.join(10)


# ---- update.json・zip の取得と検査 ----

def test_get_refuses_too_large(dist):
    dist['/big'] = b'x' * 100
    base = au.UPDATE_URL.rsplit('/latest/', 1)[0]
    with pytest.raises(au.UpdateError) as e:
        au._get(base + '/big', 10)
    assert e.value.code == 'tooLarge'


def test_manifest_drops_bad_notes():
    # 更新内容の形の崩れた版（dict でない・版の形でない）は捨て、文字でない行も捨てる（画面に出すので）
    m = manifest('v0.3.0', b'zip', notes=[123, {'version': 'v1'}, {'version': 'v0.2.0', 'ja': ['ok', 5], 'en': 'x'}])
    assert au.check_manifest(json.dumps(m))['notes'] == [{'version': 'v0.2.0', 'ja': ['ok'], 'en': []}]


def test_manifest_refuses_bad_zip_size():
    m = manifest('v0.3.0', b'zip')
    m['zip']['size'] = 'abc'
    with pytest.raises(au.UpdateError) as e:
        au.check_manifest(json.dumps(m))
    assert e.value.code == 'badManifest'


# ---- ① ダウンロード ----

def test_start_download_twice_does_not_start_again(app_dir):
    # ダウンロード中にもう一度押しても、2本目を始めない（配布元にも問い合わせない）
    u = updater(app_dir)
    u.job = {'state': 'download', 'version': 'v0.3.0', 'done': 5, 'total': 10}
    assert u.start_download() == u.job


def test_download_zip_missing_is_network_error(dist, tmp_path, app_dir):
    # update.json はあるのに zip が取れない（リリースに添付し忘れた等）
    publish(dist, tmp_path)
    del dist['/download/v0.3.0/%s-v0.3.0.zip' % au.ZIP_TOP]
    u = updater(app_dir)
    u.start_download()
    job = wait_job(u)
    assert job['state'] == 'error' and job['error'] == 'network'
    assert not (app_dir / au.UPDATE_DIR / 'new').exists()
    assert not list((app_dir / au.UPDATE_DIR).glob('*.part'))


def test_download_blocked_before_file_is_made(dist, tmp_path, app_dir, monkeypatch):
    # zip を取りに行く前に止まった（途中のファイルがまだ無い）時も、片付けで落ちない
    publish(dist, tmp_path)
    monkeypatch.setattr(au, 'ZIP_URL', 'https://example.invalid/{tag}/{name}')   # BLN_NO_NET=1 で https へは出ない
    u = updater(app_dir)
    u.start_download()
    job = wait_job(u)
    assert job['state'] == 'error' and job['error'] == 'network'


def test_download_unexpected_error_is_reported(dist, tmp_path, app_dir, monkeypatch):
    # 照合の後の思わぬ失敗も、画面に失敗として返す（固まらない）。途中のファイルと展開しかけのフォルダは残さない
    publish(dist, tmp_path)

    def boom(*a):
        raise ValueError('boom')
    monkeypatch.setattr(au, 'extract_release', boom)
    u = updater(app_dir)
    u.start_download()
    job = wait_job(u)
    assert job == {'state': 'error', 'error': 'extract', 'detail': 'boom'}
    assert not list((app_dir / au.UPDATE_DIR).glob('*.part')) and not (app_dir / au.UPDATE_DIR / 'new').exists()


def test_extract_refuses_too_large(tmp_path, monkeypatch):
    # 展開した後の大きさが上限を超える zip は展開しない
    z = tmp_path / 'r.zip'
    z.write_bytes(make_zip(tmp_path, 'v0.3.0'))
    monkeypatch.setattr(au, 'MAX_UNZIP', 10)
    with pytest.raises(au.UpdateError) as e:
        au.extract_release(str(z), str(tmp_path / 'out'), 'v0.3.0')
    assert e.value.code == 'tooLarge' and not (tmp_path / 'out').exists() and not (tmp_path / 'out.tmp').exists()


# ---- ② 新しい版の exe に任せる ----

def test_apply_spawn_failure(app_dir, monkeypatch):
    u = updater(app_dir)
    u.job = {'state': 'ready'}

    def fail(args, cwd):
        raise OSError('no exe')
    monkeypatch.setattr(au, '_spawn', fail)
    with pytest.raises(au.UpdateError) as e:
        u.apply('ja')
    assert e.value.code == 'spawn' and 'no exe' in e.value.detail


# ---- ③ 差し替え ----

@WIN
def test_msgbox_shows_and_never_raises(monkeypatch):
    # 差し替え役は画面を持たないので、失敗はメッセージボックスで知らせる。出せなくても差し替え役は止まらない。
    # 本物の MessageBoxW は呼ばない（テストが止まる）
    user32 = ctypes.windll.user32
    calls = []

    def fake(*a):
        calls.append(a)
        return 1
    monkeypatch.setattr(user32, 'MessageBoxW', fake)
    assert ctypes.windll.user32.MessageBoxW is fake   # 差し替えが効いていなければ、ここで止める（本物の窓を出さない）
    au._msgbox('テストの知らせ')
    assert len(calls) == 1 and calls[0][1] == 'テストの知らせ' and calls[0][2] == au.APP_NAME

    def broken(*a):
        raise OSError('no user32')
    monkeypatch.setattr(user32, 'MessageBoxW', broken)
    au._msgbox('x')


@WIN
def test_wait_pid_for_process_already_gone():
    assert au._wait_pid(0x7FFFFFF0, 1) is True   # 居ないプロセス（もう終わった旧版）は待たない
    assert au._wait_pid(0, 1) is True


@WIN
def test_wait_pid_waits_for_old_app():
    # 旧版が終わるのを待つ。決めた時間で終わらなければ False（差し替え役は「終了しない」と知らせて止まる）
    import subprocess
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        assert au._wait_pid(p.pid, 0.2) is False
    finally:
        p.kill()
    p.wait(10)
    q = subprocess.Popen([sys.executable, '-c', 'pass'])
    assert au._wait_pid(q.pid, 20) is True
    q.wait(10)


def test_retry_waits_then_gives_up():
    n = []

    def flaky():
        n.append(1)
        if len(n) < 3:
            raise PermissionError('busy')
        return 'ok'
    assert au._retry(flaky, tries=5, wait=0) == 'ok' and len(n) == 3

    def always():
        raise PermissionError('busy')
    with pytest.raises(PermissionError):
        au._retry(always, tries=3, wait=0)


class FakeRm:
    """Restart Manager（rstrtmgr.dll）の代わり。各関数の戻り値を決められる"""
    def __init__(self, start=0, register=0, first=234, need=1, second=0):
        self.start, self.register, self.first, self.need, self.second = start, register, first, need, second
        self.ended = False

    def RmStartSession(self, *a):
        return self.start

    def RmRegisterResources(self, *a):
        return self.register

    def RmGetList(self, session, need, n, infos, reasons):
        if infos is None:
            need._obj.value = self.need
            return self.first
        return self.second

    def RmEndSession(self, *a):
        self.ended = True
        return 0


@WIN
@pytest.mark.parametrize('rm', [FakeRm(start=1), FakeRm(register=1), FakeRm(second=5)], ids=['start', 'register', 'list'])
def test_busy_apps_gives_up_quietly(monkeypatch, tmp_path, rm):
    # 使っているアプリを調べられない時は「分からない」（[]）にする。調べ始めた時は必ず終わらせる
    p = tmp_path / 'a.dll'
    p.write_bytes(b'x')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name: rm)
    assert au._busy_apps([str(p)]) == []
    assert rm.ended == (rm.start == 0)


@WIN
def test_busy_apps_without_restart_manager(monkeypatch, tmp_path):
    def missing(name):
        raise OSError('no rstrtmgr')
    monkeypatch.setattr(ctypes, 'WinDLL', missing)
    assert au._busy_apps([str(tmp_path / 'a.dll')]) == []


def test_clean_previous_missing_folder(tmp_path):
    au._clean_previous(str(tmp_path / 'none'))   # 落ちない


def test_apply_main_bad_pid_argument(app_dir, tmp_path, boxes):
    # --pid が数でない（旧版の渡し方が崩れた）時は待たずに進む
    src, argv = stage(app_dir, tmp_path)
    argv[argv.index('--pid') + 1] = 'abc'
    assert au.apply_main(argv, src=str(src), launch_after=False)
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.3.0' and boxes == []


def test_apply_main_keeps_old_version_when_rollback_is_stuck(app_dir, tmp_path, boxes, monkeypatch):
    # 写した後の確かめに落ち、元に戻すのも途中で失敗した（外のアプリがつかんでいる）時: 古い版は退避ごと残し、消さない
    src, argv = stage(app_dir, tmp_path)
    monkeypatch.setattr(au.shutil, 'copytree', lambda s, d: Path(d).mkdir())   # _internal を空で写した＝版が合わない
    real_rmtree = shutil.rmtree

    def rmtree(p, ignore_errors=False, *a, **k):
        if ignore_errors:
            return real_rmtree(p, ignore_errors=True)
        raise PermissionError('locked')   # 入れかけの新しい版を消せない
    monkeypatch.setattr(au.shutil, 'rmtree', rmtree)
    real_retry = au._retry

    def retry(fn, tries=40, wait=0.25):
        if tries == 8:   # 元に戻す方だけ失敗させる
            raise PermissionError('locked')
        return real_retry(fn, tries, 0)
    monkeypatch.setattr(au, '_retry', retry)
    assert not au.apply_main(argv, src=str(src), launch_after=False)
    bk = app_dir / '_previous_v0.1.0'
    assert (bk / au.EXE_NAME).read_bytes() == b'EXEv0.1.0' and au.read_app_version(str(bk / '_internal')) == 'v0.1.0'
    assert len(boxes) == 1 and 'copy check failed' in boxes[0] and 'エクスプローラー' in boxes[0]
    assert not (app_dir / au.UPDATE_DIR / 'applied.json').exists()


def test_apply_main_survives_unwritable_record_and_relaunch_failure(app_dir, tmp_path, boxes, monkeypatch):
    # 差し替えは済んだが、「更新しました」用の記録が書けず、起動し直しにも失敗した: 差し替えは成功のまま、落ちない
    src, argv = stage(app_dir, tmp_path)
    (app_dir / au.UPDATE_DIR / 'applied.json').mkdir()   # 同じ名前のフォルダがあって書けない

    def fail(args, cwd):
        raise OSError('cannot start')
    monkeypatch.setattr(au, '_spawn', fail)
    assert au.apply_main(argv, src=str(src))
    assert au.read_app_version(str(app_dir / '_internal')) == 'v0.3.0' and boxes == []


# ---- ④ 起動し直した後の片付け ----

def test_startup_with_unreadable_folder(tmp_path):
    d = tmp_path / 'none'
    u = au.Updater(str(d / '_internal'), str(d), True)
    au.startup(u)
    join_clean()
    assert u.just_updated is None


def test_startup_keeps_work_when_update_started(app_dir):
    # このセッションで更新を始めていたら、作業フォルダを消さない
    write(app_dir / au.UPDATE_DIR / 'x.part', 'x')
    u = updater(app_dir)
    u.job = {'state': 'download'}
    au.startup(u)
    join_clean()
    assert (app_dir / au.UPDATE_DIR / 'x.part').exists()


def test_startup_retries_while_work_is_locked(app_dir, monkeypatch):
    # 差し替えた exe の終わりぎわで作業フォルダが消せない間は、1秒ずつ 30回まで消し直す（それでも残ったら次に起動した時）
    write(app_dir / au.UPDATE_DIR / 'x.part', 'x')
    monkeypatch.setattr(au.shutil, 'rmtree', lambda *a, **k: None)
    sleeps = []
    monkeypatch.setattr(au.time, 'sleep', sleeps.append)
    u = updater(app_dir)
    au.startup(u)
    join_clean()
    assert sleeps == [1] * 30 and (app_dir / au.UPDATE_DIR).exists()
