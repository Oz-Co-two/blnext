# アプリの更新の小さな画面（web/js/update.js）。確認・ダウンロード・差し替えのふりをする（core/app_update.py は tests/unit/test_app_update.py）。
# 設定の「このアプリについて」（版・ソースコード・不具合の報告）も
import os, time

import pytest

from conftest import FakeUpdater
from core.app_update import UpdateError

DLG = "document.querySelector('.updDlg')"
TEXT = "(" + DLG + "?.innerText||'')"
TOAST = "document.querySelector('#toast').innerText"


@pytest.fixture
def upd(ui, monkeypatch):
    fake = FakeUpdater()
    monkeypatch.setattr(ui.serve, 'UPDATER', fake)
    return fake


def manual(ui):
    ui.open('settings')
    ui.click_sel('.updCheck', text='更新を確認')


def test_up_to_date(ui, upd):
    upd.newer = False
    manual(ui)
    ui.until(TEXT + ".includes('最新の版です（v0.1.0）')", what='最新の知らせ')
    ui.click_sel('.updDlg button', text='閉じる')
    ui.until("!" + DLG)


def test_check_error(ui, upd):
    upd.check_error = 'network'
    manual(ui)
    ui.until(TEXT + ".includes('GitHub に接続できませんでした')", what='失敗の知らせ')


def test_prompt_shows_notes_as_text(ui, upd):
    manual(ui)
    ui.until(TEXT + ".includes('新しい版 v0.3.0 があります')", what='知らせ')
    text = ui.js(TEXT)
    assert 'v0.2.0' in text and 'その前の機能' in text and '今の版: v0.1.0' in text and '12.0MB' in text
    assert ui.js("!!document.querySelector('.updDlg ul li') && !document.querySelector('.updDlg ul img')")   # 外から来た文字は要素にしない
    assert '<img' in text
    assert ui.js('window.__xss') is None


def test_skip_is_remembered(ui, upd):
    manual(ui)
    ui.until("!!" + DLG, what='知らせ')
    ui.click_sel('.updDlg button', text='更新しない')
    ui.until("!" + DLG, what='閉じる')
    try:
        assert ui.serve.SETTINGS.get('update_skip') == 'v0.3.0'
        # 設定の「更新を確認」からは、更新しないを選んだ版も出す
        ui.click_sel('.updCheck', text='更新を確認')
        ui.until(TEXT + ".includes('新しい版 v0.3.0')", what='もう一度出る')
    finally:
        ui.serve.SETTINGS.reset(['update_skip'])


@pytest.mark.parametrize('mode,why', [('notExe', '開発用の起動方法'), ('readonly', '書き込めない'), ('manual', '自動では更新できません')])
def test_manual_update_opens_releases(ui, upd, monkeypatch, mode, why):
    opened = []
    monkeypatch.setattr(ui.serve, 'open_browser', opened.append)
    upd.auto = mode
    manual(ui)
    ui.until(TEXT + ".includes(%r)" % why, what='理由')
    ui.click_sel('.updDlg button', text='リリースページを開く')
    ui.until("!" + DLG, what='閉じる')
    assert opened == [ui.serve.app_update.RELEASES_URL]


def test_update_runs_and_quits(ui, upd):
    manual(ui)
    ui.until("!!" + DLG, what='知らせ')
    ui.js("window.__quit=0; window.pywebview={api:{quit_app:()=>{window.__quit++;}}}; true")
    ui.click_sel('.updDlg button', text='更新する')
    ui.until(TEXT + ".includes('再起動します')", timeout=15, what='差し替えへ')
    ui.until("window.__quit===1", what='アプリを閉じる')
    assert upd.applied == ['ja'] and upd.started == 1


def test_update_failure_is_shown(ui, upd):
    upd.final = {'state': 'error', 'error': 'hash', 'detail': ''}
    manual(ui)
    ui.until("!!" + DLG, what='知らせ')
    ui.js("window.__quit=0; window.pywebview={api:{quit_app:()=>{window.__quit++;}}}; true")
    ui.click_sel('.updDlg button', text='更新する')
    ui.until(TEXT + ".includes('一致しません')", timeout=15, what='失敗の知らせ')
    assert ui.js('window.__quit') == 0 and upd.applied == []


def test_update_cancel(ui, upd):
    upd.steps = 100000
    manual(ui)
    ui.until("!!" + DLG, what='知らせ')
    ui.click_sel('.updDlg button', text='更新する')
    ui.until(TEXT + ".includes('ダウンロードしています')", what='ダウンロード中')
    ui.click_sel('.updDlg button', text='やめる')
    ui.until("!" + DLG, timeout=5, what='やめると閉じる')
    assert upd.cancelled


def test_apply_error_is_shown(ui, upd):
    upd.apply_error = 'spawn'
    manual(ui)
    ui.until("!!" + DLG, what='知らせ')
    ui.click_sel('.updDlg button', text='更新する')
    ui.until(TEXT + ".includes('更新を始められませんでした')", timeout=15, what='失敗の知らせ')


def test_auto_check_only_for_exe(ui, upd):
    ui.open('home')     # 開発用の起動（exe でない）では確かめない
    ui.wait(0.2)
    assert upd.checked == 0


def test_auto_check_prompts(ui, upd, monkeypatch):
    monkeypatch.setattr(ui.serve, 'FROZEN', True)
    ui.open('home')
    ui.until(TEXT + ".includes('新しい版 v0.3.0')", timeout=8, what='起動時の知らせ')


def test_auto_check_respects_skip_and_setting(ui, upd, monkeypatch):
    monkeypatch.setattr(ui.serve, 'FROZEN', True)
    ui.serve.SETTINGS.update({'update_skip': 'v0.3.0'})
    try:
        ui.open('home')
        ui.until("window.__chk===undefined", timeout=1)
        ui.wait(3.5)
        assert upd.checked == 1 and not ui.js("!!" + DLG)    # 確かめるが、更新しないを選んだ版は出さない
        ui.serve.SETTINGS.update({'update_skip': '', 'update_check': False})
        ui.open('home')
        ui.wait(3.5)
        assert upd.checked == 1 and not ui.js("!!" + DLG)    # 設定がオフなら確かめない
    finally:
        ui.serve.SETTINGS.reset(['update_skip', 'update_check'])


def test_just_updated_toast(ui, upd):
    upd.just_updated = {'from': 'v0.1.0', 'to': 'v0.3.0'}
    ui.open('home')
    ui.until(TOAST + ".includes('v0.3.0 に更新しました')", what='更新の知らせ')


def test_about_card(ui, upd, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.serve, 'open_browser', opened.append)
    ui.open('settings')
    text = ui.js("document.getElementById('grp_about').innerText")
    assert ui.serve.APP_VERSION in text and 'GNU GPL v3' in text
    ui.click_sel('#grp_about button', text='ソースコードを開く')
    ui.until("true")
    ui.wait(0.2)
    assert opened == [ui.serve.app_update.REPO_URL]


def _wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError('呼ばれなかった')


def test_about_report(ui, monkeypatch):
    # 不具合の報告: Issues の新規作成のページ（版入り）とログのフォルダを開く。本物のブラウザ・エクスプローラーは開かない
    opened, started = [], []
    monkeypatch.setattr(ui.serve, 'open_browser', opened.append)
    monkeypatch.setattr(ui.serve.launch, 'start', started.append)
    ui.open('settings')
    row = "document.querySelector('#grp_about .row[data-key=about_report]')"
    assert ui.js(row + ".querySelector('.lbl').innerText") == '不具合の報告'
    desc = ui.js(row + ".querySelector('.desc').innerText")
    assert 'GitHub の Issues' in desc and 'アカウント' in desc and 'Windows の版' in desc and 'プレイヤー ID' in desc
    ui.click_sel('#grp_about .reportIssue', text='不具合を報告')
    _wait_for(lambda: opened)
    assert opened == [ui.serve.issue_url()] and opened[0].startswith(ui.serve.ISSUES_URL + '?')
    ui.click_sel('#grp_about .openLogs', text='ログのフォルダを開く')
    _wait_for(lambda: started)
    assert started == [ui.serve.logs.log_dir(ui.serve.DATA_DIR)] and os.path.isdir(started[0])
    # 開けなかった時は知らせる
    def fail(target):
        raise OSError('no explorer')
    monkeypatch.setattr(ui.serve.launch, 'start', fail)
    ui.click_sel('#grp_about .openLogs', text='ログのフォルダを開く')
    ui.until(TOAST + ".includes('開けませんでした')", what='開けなかった知らせ')


def test_update_error_class_is_known():
    # 画面の文言の元（web/js/update.js の ERR）と、app_update の失敗の種類がそろっている
    assert UpdateError('network').code == 'network'
