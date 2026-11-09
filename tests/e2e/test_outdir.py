# 出力先（Beat Saber のフォルダ）を選ぶ画面（web/js/bsdir.js）: 自動で探した候補から選ぶ・未設定のまま出力した時・
# 選んでいる間に消えた候補・exe 版のフォルダ選択（偽の窓口）・設定画面から選ぶ／解除する。
# 自動で探す結果は作り物にする（このPCに入っている本物の Beat Saber に左右されないように）
import json, os
from types import SimpleNamespace

import pytest
from fakebs import fake_installs

ITEMS = "[...document.querySelectorAll('.bsItem')].map(e=>e.innerText)"
MSG = "document.querySelector('.modal .err').textContent"
TOAST = "document.querySelector('#toast').innerText"
# exe 版（pywebview）の窓口のふり。答えを順に返す
FAKE_NATIVE = "window.__ans=%s; window.pywebview={api:{pick_beat_saber_dir:()=>Promise.resolve(window.__ans.shift())}}; true"


@pytest.fixture
def installs(ui, tmp_path, monkeypatch):
    """自動で探すと、BSManager（共通コンテンツ・最後に起動した版）と Steam が見つかる。出力先の設定は後で戻す"""
    inst, shared, steam = fake_installs(ui.serve, tmp_path, monkeypatch)
    keep = ui.serve.SETTINGS.get('beat_saber_dir')
    yield SimpleNamespace(bsm=str(inst), shared=str(shared), steam=str(steam))
    ui.serve.SETTINGS.update({'beat_saber_dir': keep}, trusted=True)
    ui.js("delete window.pywebview; delete window.__ans; true")


def open_dialog(ui, button, n=2):
    ui.click_sel('button', text=button)
    ui.until("document.querySelectorAll('.bsItem').length===%d" % n, what=f'候補が{n}つ出る')


def test_export_without_dir_asks_then_writes_to_shared(ui, installs):
    # 出力先が未設定のまま出力 → 選ぶ画面 → BSManager の版を選ぶと、そのまま出力して SharedContent に入る
    ui.serve.SETTINGS.update({'beat_saber_dir': ''}, trusted=True)
    ui.open('recommend')
    assert '出力先が未設定' in ui.js("document.querySelector('.recSum').innerText")
    open_dialog(ui, 'Beat Saber に書き込む')
    items = ui.js(ITEMS)
    assert 'BSManager · 最後に起動した版' in items[0] and os.path.realpath(installs.shared) in items[0]
    assert items[1].startswith(installs.steam) and '共通のフォルダ' not in items[1] and '最後に起動した版' not in items[1]
    ui.click_sel('.bsItem button', text='これにする', index=0)
    ui.until(TOAST + ".includes('書き込みました')", what='選んだ後にそのまま出力する')
    assert ui.serve.SETTINGS.get('beat_saber_dir') == installs.bsm
    assert installs.bsm in ui.js("document.querySelector('.recSum').innerText")
    with open(os.path.join(installs.shared, 'blnext-next.bplist'), encoding='utf-8') as f:
        assert json.load(f)['songs']


def test_change_dir_marks_current(ui, installs):
    ui.serve.SETTINGS.update({'beat_saber_dir': installs.bsm}, trusted=True)
    ui.open('recommend')
    open_dialog(ui, '変える')
    cur = ui.js("document.querySelector('.bsItem.cur').innerText")
    assert cur.startswith(installs.bsm) and '今の出力先' in cur
    assert ui.js("document.querySelectorAll('.bsItem button').length") == 1   # 今の出力先には「これにする」が無い
    ui.click_sel('.bsItem button', text='これにする')
    ui.until("!document.querySelector('.modal')", what='選ぶと閉じる')
    assert ui.serve.SETTINGS.get('beat_saber_dir') == installs.steam
    assert installs.steam in ui.js(TOAST) and installs.steam in ui.js("document.querySelector('.recSum').innerText")


def test_choice_gone_while_open(ui, installs):
    # 画面を開いている間に Beat Saber.exe が無くなった候補は選べない（Python がもう一度確かめる）
    ui.open('recommend')
    keep = ui.serve.SETTINGS.get('beat_saber_dir')
    open_dialog(ui, '変える')
    os.remove(os.path.join(installs.steam, 'Beat Saber.exe'))
    ui.click_sel('.bsItem button', text='これにする', index=1)
    ui.until(MSG + ".includes('選べませんでした')", what='選べなかった知らせ')
    assert ui.js("!!document.querySelector('.modal')") and ui.serve.SETTINGS.get('beat_saber_dir') == keep
    ui.click_sel('.modalFoot button', text='閉じる')
    ui.until("!document.querySelector('.modal')", what='「閉じる」で閉じる')


def test_none_found_and_browser_cannot_pick(ui, monkeypatch):
    monkeypatch.setattr(ui.serve.bsfind, 'find_installs', lambda home=None, appdata=None: [])
    ui.open('recommend')
    ui.click_sel('button', text='変える')
    ui.until("document.querySelector('.bsList').innerText.includes('自動では見つかりませんでした')", what='見つからない知らせ')
    ui.click_sel('button', text='フォルダを選ぶ…')   # ブラウザで開いている時は、ダイアログを出せない
    ui.until(MSG + ".includes('exe版')", what='exe 版でだけ選べる知らせ')
    ui.click(5, 5)   # 外側を押すと閉じる
    ui.until("!document.querySelector('.modal')", what='外側を押すと閉じる')


def test_pick_with_native_dialog(ui, installs):
    # exe 版のフォルダ選択: やめた・Beat Saber でなかった・中に複数あった（選ぶ）・決まった
    ui.open('recommend')
    open_dialog(ui, '変える')
    offered = ui.serve.bs_offer([installs.bsm, installs.steam], 'picked')   # app.py が選んだフォルダの中に複数見つけた時と同じ
    ui.js(FAKE_NATIVE % json.dumps([{'ok': False, 'error': 'cancelled'}, {'ok': False, 'error': 'not-beat-saber'},
                                    {'ok': False, 'error': 'choose', 'candidates': offered}]))
    ui.click_sel('button', text='フォルダを選ぶ…')
    assert ui.js(MSG) == '' and ui.js("!!document.querySelector('.modal')")
    ui.click_sel('button', text='フォルダを選ぶ…')
    ui.until(MSG + ".includes('Beat Saber.exe が見つかりませんでした')", what='Beat Saber でなかった知らせ')
    ui.click_sel('button', text='フォルダを選ぶ…')
    ui.until("document.querySelector('.bsList').innerText.includes('複数ありました')", what='中の候補から選ぶ')
    assert all('選んだフォルダの中' in s for s in ui.js(ITEMS))
    ui.click_sel('.bsItem button', text='これにする', index=1)
    ui.until("!document.querySelector('.modal')")
    assert ui.serve.SETTINGS.get('beat_saber_dir') == installs.steam
    open_dialog(ui, '変える')
    ui.js(FAKE_NATIVE % json.dumps([{'ok': True, 'path': installs.bsm}]))   # 本物の窓口は、決めたら設定にも保存する
    ui.click_sel('button', text='フォルダを選ぶ…')
    ui.until("!document.querySelector('.modal')", what='決まると閉じる')
    assert installs.bsm in ui.js("document.querySelector('.recSum').innerText")


def test_settings_path_controls(ui, installs):
    ui.open('settings')
    paths = "[...document.querySelectorAll('.pathBox .path')].map(e=>e.textContent)"
    open_dialog(ui, '探す・選ぶ…')
    ui.click_sel('.bsItem button', text='これにする', index=0)
    ui.until("!document.querySelector('.modal')")
    assert ui.js(paths) == [installs.bsm] and ui.serve.SETTINGS.get('beat_saber_dir') == installs.bsm   # 場所を選ぶ欄はこの1つだけ
    ui.click_sel('.pathBox button', text='解除', index=0)
    ui.until(paths + "[0]==='（なし）'", what='解除すると（なし）')
    assert ui.serve.SETTINGS.get('beat_saber_dir') == ''
