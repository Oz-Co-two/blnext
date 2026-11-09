# 統計が非公開の人への警告（プレイリストを作る・出す操作が成功した後の小さな画面。web/js/stats.js の warnPrivate・recommend.js の warnStats）:
# 操作ごとに出る・出さない操作・失敗した時は出さない・設定でオフ（その場で効く）・公開を確かめた後は出ない・小さな画面とホームの知らせにオフにするボタンが無い。
# 本物の BSManager・ブラウザは起動しない（launch・open_browser を差し替える）
import json

import pytest

from conftest import FakeRefresher, fake_players
from fakebs import fake_installs

DLG = "document.querySelector('.statsDlg')"
SHOWN = "!!" + DLG
TOAST = "document.querySelector('#toast')"
IDS = "[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.dataset.id)"
DLG_BUTTONS = ['閉じる', 'BeatLeader の設定を開く', 'オンにした（確かめる）']
LABEL = '統計が非公開の時に警告する'
PENDING = "!!document.querySelector('.pendBar')"
RBCHOICE = "!!document.querySelector('.modal .rbChoice')"


def private(ui):
    ui.serve.STORE.set_meta('stats_public', 0)


def make_pending(ui):
    """保存したリストの1曲目を「今後出さない」にする（DB を直接。作り直すとリストが変わる＝知らせの欄に「作り直す」が出る）"""
    first = ui.serve._api_recommend({'preset': 'p1'})['items'][0]['leaderboard_id']
    with ui.serve.STORE.tx():
        ui.serve.STORE.conn.execute("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', 0)", (first,))


def fake_oneclick(ui, mp, launch=lambda url: None):
    """ワンクリックを受けるアプリがあるふり（本物の BSManager を起動しない）"""
    mp.setattr(ui.serve.oneclick, 'handler_app', lambda: 'BSManager')
    mp.setattr(ui.serve.oneclick, 'launch', launch)
    mp.setattr(ui.serve, 'ONECLICK_WAIT', 0)


def click_write(ui):
    ui.click_sel('.outBox button', text='Beat Saber に書き込む')


def toast_has(ui, text, kind='ok'):
    ui.until(f"{TOAST}.classList.contains('show') && {TOAST}.classList.contains({json.dumps(kind)}) && {TOAST}.innerText.includes({json.dumps(text)})",
             timeout=15, what=f'知らせ: {text}')


def quiet(ui):
    """警告はサーバーに聞いてから出すので、少し待ってから出ていないことを確かめる"""
    ui.wait(0.4)
    assert not ui.js(SHOWN), '警告が出ている'


def close_dialog(ui):
    ui.click_sel('.statsDlg .modalFoot button', text='閉じる')
    ui.until("!" + DLG, what='閉じる')


def nav(ui, screen):
    """開き直さずに、上の帯のボタンで画面を移る"""
    ui.click_sel(f'#nav button[data-screen="{screen}"]')
    ui.until(f"document.body.dataset.screen==={json.dumps(screen)} && !document.body.dataset.busy", what=f'{screen} の画面へ')


# ---- 操作（画面を開いて押す。返り値は成功の知らせに含まれる文） ----
def op_rebuild(ui, mp):
    make_pending(ui)
    ui.open('recommend')
    ui.until(PENDING, what='作り直すの知らせ')
    ui.click_sel('.pendBar > button', text='作り直す')
    return '今のデータでリストを作り直しました'


def group_op(choice, text):
    def run(ui, mp):
        ui.serve._api_presets_difficulty_set({'copyFrom': 'p1', 'levels': ['steady', 'challenge'], 'size': 5})
        ui.open('recommend')
        ui.click_sel('.groupBtn')
        ui.until(RBCHOICE, what='作り直すの小さな画面')
        ui.click_sel('.modal .rbChoice', text=choice)
        return text
    return run


def op_difficulty_set(ui, mp):
    ui.open('recommend')
    ui.click_sel('.diffSetBtn')
    ui.until("!!document.querySelector('.modal .dsLevel')", what='難しさ違いのセットを作る小さな画面')
    ui.click_sel('.modal .modalFoot .primary', text='作る')
    return '難しさ違いのセットを'


def op_write(ui, mp):
    ui.open('recommend')
    click_write(ui)
    return '曲を書き込みました'


def op_oneclick(ui, mp):
    fake_oneclick(ui, mp)
    ui.open('recommend')
    ui.click_sel('.outBox button', text='BSManager で入れる')
    return 'BSManager を起動しました'


def op_file_native(ui, mp):
    ui.open('recommend')   # exe 版（pywebview）の「名前を付けて保存」のふり
    ui.js("window.pywebview={api:{save_playlist:p=>Promise.resolve({ok:true, path:'C:\\\\x\\\\a.bplist'})}}; true")
    ui.click_sel('.outBox button', text='ファイルに保存')
    return 'に保存しました'


def op_file_browser(ui, mp):
    ui.call('Browser.setDownloadBehavior', behavior='deny')
    ui.open('recommend')
    ui.click_sel('.outBox button', text='ファイルに保存')
    return 'ダウンロードしました'


def op_write_all(ui, mp):
    ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    ui.open('recommend')
    ui.click_sel('.presetExtra button', text='全部のセットを書き込む')
    return '2個のセットのプレイリストを書き込みました'


def op_oneclick_all(ui, mp):
    fake_oneclick(ui, mp)
    ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    ui.open('recommend')
    ui.click_sel('.presetExtra button', text='全部のセットを BSManager で入れる')
    return 'BSManager に渡しました'


OPS = [op_rebuild, group_op('組のセット全部', '交互に選び直しました'), group_op('このセットだけ', '今のデータでリストを作り直しました'), op_difficulty_set,
       op_write, op_oneclick, op_file_native, op_file_browser, op_write_all, op_oneclick_all]
OP_IDS = ['rebuild', 'groupAll', 'groupOne', 'difficultySet', 'write', 'oneclick', 'fileNative', 'fileBrowser', 'writeAll', 'oneclickAll']


# ---- 出す ----
@pytest.mark.parametrize('op', OPS, ids=OP_IDS)
def test_warns_after_operation(ui, monkeypatch, op):
    private(ui)
    text = op(ui, monkeypatch)
    ui.until(SHOWN, timeout=15, what='警告')
    toast_has(ui, text)   # 操作の結果の知らせも出たまま（小さな画面より手前）
    assert ui.js(f"+getComputedStyle({TOAST}).zIndex > +getComputedStyle(document.querySelector('.modalBack')).zIndex")
    assert ui.js("document.querySelectorAll('.statsDlg').length") == 1
    assert ui.js("[...document.querySelectorAll('.statsDlg button')].map(b=>b.innerText)") == DLG_BUTTONS
    assert 'プレイリストの操作は終わっています' in ui.js(DLG + ".innerText")
    assert ui.layout_issues() == []
    close_dialog(ui)
    assert ui.serve.SETTINGS.get('stats_notice') is True      # 閉じても設定は変わらない


def test_warns_every_time_and_closes_with_keys(ui):
    private(ui)
    ui.open('recommend')
    click_write(ui)
    ui.until(SHOWN, what='1回目')
    n = ui.serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n']
    # 閉じるボタンに当たっている（押した書き込みのボタンに当たったままだと、スペースでもう一度書き込んでしまう）
    assert ui.js("document.activeElement.innerText") == '閉じる'
    ui.key(' ')
    ui.until("!" + DLG, what='スペースで閉じる')
    assert ui.serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n'] == n
    click_write(ui)
    ui.until(SHOWN, what='2回目')
    ui.key('Escape')
    ui.until("!" + DLG, what='Esc で閉じる')
    click_write(ui)
    ui.until(SHOWN, what='3回目')
    ui.click(5, 5)                                             # 小さな画面の外を押す
    ui.until("!" + DLG, what='外を押して閉じる')
    click_write(ui)
    ui.until(SHOWN, what='4回目')
    assert ui.js("document.querySelectorAll('.statsDlg').length") == 1


def test_warns_after_choosing_the_output_folder(ui, tmp_path, monkeypatch):
    # 出力先が未設定のまま書き込む → 選ぶ画面 → 選ぶと書き込んで、その後に警告。選ぶ画面を閉じただけ・出力先を変えただけでは出ない
    s = ui.serve
    private(ui)
    fake_installs(s, tmp_path, monkeypatch)
    keep = s.SETTINGS.get('beat_saber_dir')
    s.SETTINGS.update({'beat_saber_dir': ''}, trusted=True)
    try:
        ui.open('recommend')
        click_write(ui)
        ui.until("document.querySelectorAll('.bsItem').length===2", what='候補')
        ui.key('Escape')
        ui.until("!document.querySelector('.modal')", what='閉じる')
        quiet(ui)
        click_write(ui)
        ui.until("document.querySelectorAll('.bsItem').length===2", what='候補')
        ui.click_sel('.bsItem button', text='これにする', index=0)
        ui.until(SHOWN, what='書き込んだ後の警告')
        toast_has(ui, '曲を書き込みました')
        close_dialog(ui)
        ui.click_sel('button', text='変える')
        ui.until("document.querySelectorAll('.bsItem button').length===1", what='候補')
        ui.click_sel('.bsItem button', text='これにする')
        toast_has(ui, 'にしました')
        quiet(ui)
    finally:
        s.SETTINGS.update({'beat_saber_dir': keep}, trusted=True)


# ---- 出さない ----
def test_no_warning_on_list_operations(ui):
    # 開いただけ・条件の変更での自動の作り直し・一覧の操作・セットの切り替えでは出さない（条件を変えるのは足したセット。p1 の値を残さない）
    s = ui.serve
    private(ui)
    s.SETTINGS.update({'active_preset': s.SETTINGS.add_preset('B', copy_from='p1')})
    ui.open('recommend')
    quiet(ui)
    ui.js("(()=>{const e=document.getElementById('cond_playlist_size'); e.value='7'; e.dispatchEvent(new Event('change'));})()")
    ui.until(f"{IDS}.length===7", what='曲数を変えると作り直す')
    quiet(ui)
    ui.click_sel('.recCond .diffStop[data-level="challenge"]')
    ui.until("!!document.querySelector('.recCond .diffStop.on[data-level=\"challenge\"]')", what='難しさのスライダーで作り直す')
    quiet(ui)
    ids = ui.js(IDS)
    ui.click_sel(f'.recRow[data-id="{ids[0]}"] button.icon', index=0)   # 📌 固定
    ui.until(f"document.querySelector('.recRow[data-id=\"{ids[0]}\"]').classList.contains('pinned')", what='固定')
    ui.click_sel(f'.recRow[data-id="{ids[1]}"] button.icon', index=1)   # ✕ 今回は外す
    ui.until(f"!{IDS}.includes('{ids[1]}')", what='外す')
    ui.click_sel(f'.recRow[data-id="{ids[2]}"] button.icon', index=2)   # 🚫 今後出さない
    ui.until(f"!{IDS}.includes('{ids[2]}')", what='今後出さない')
    ui.click_sel('.recRow.head .th', text='推定精度')                   # 見出しで並べ替え
    ui.wait(0.2)
    ui.click_sel('button.orderReset')                                    # 基本の並びに戻す
    ui.wait(0.2)
    quiet(ui)
    ui.click_sel('button', text='入れ替える', index=0)                   # 次点と入れ替える
    ui.wait(0.2)
    quiet(ui)
    ui.click_sel('.presetTab', text='スタンダード')                       # セットの切り替え
    ui.until("document.querySelector('.presetTab.on')?.innerText==='スタンダード' && !!document.querySelector('.recSum')", what='セットの切り替え')
    quiet(ui)
    nav(ui, 'home')
    nav(ui, 'recommend')
    quiet(ui)


def test_no_warning_when_rebuild_is_cancelled(ui):
    private(ui)
    ui.serve._api_presets_difficulty_set({'copyFrom': 'p1', 'levels': ['steady', 'challenge'], 'size': 5})
    ui.open('recommend')
    ui.click_sel('.groupBtn')
    ui.until(RBCHOICE, what='作り直すの小さな画面')
    ui.click_sel('.modal .modalFoot button', text='やめる')
    ui.until("!document.querySelector('.modal')", what='やめる')
    quiet(ui)


def test_no_warning_when_operation_fails(ui, monkeypatch, tmp_path):
    s = ui.serve
    private(ui)

    def fail(code):
        def run(body):
            raise s.ApiError(code)
        return run

    # 作り直す
    make_pending(ui)
    ui.open('recommend')
    ui.until(PENDING, what='作り直すの知らせ')
    with monkeypatch.context() as m:
        m.setitem(s.API, 'recommend/rebuild', fail('internal'))
        ui.click_sel('.pendBar > button', text='作り直す')
        toast_has(ui, '操作に失敗しました', 'err')
    quiet(ui)
    # 難しさ違いのセットを作る
    with monkeypatch.context() as m:
        m.setitem(s.API, 'presets/difficulty-set', fail('too-many-presets'))
        ui.click_sel('.diffSetBtn')
        ui.until("!!document.querySelector('.modal .dsLevel')", what='難しさ違いのセットを作る小さな画面')
        ui.click_sel('.modal .modalFoot .primary', text='作る')
        ui.until("document.querySelector('.modal .err').innerText.includes('10個まで')", what='作れない知らせ')
    quiet(ui)
    ui.click_sel('.modal .modalFoot button', text='やめる')
    # 書き込む・全部のセットを書き込む（出力先が消えた）
    keep = s.SETTINGS.get('beat_saber_dir')
    s.SETTINGS.update({'beat_saber_dir': str(tmp_path / 'gone')}, trusted=True)
    s.SETTINGS.add_preset('B', copy_from='p1')
    try:
        ui.open('recommend')
        click_write(ui)
        toast_has(ui, '見つかりません', 'err')
        quiet(ui)
        ui.click_sel('.presetExtra button', text='全部のセットを書き込む')
        ui.wait(0.2)
        ui.until(f"{TOAST}.classList.contains('err') && {TOAST}.innerText.includes('スタンダード:')", what='全部失敗の知らせ')
        quiet(ui)
    finally:
        s.SETTINGS.update({'beat_saber_dir': keep}, trusted=True)
    # 全部のセットで1つだけ失敗（エラーを出す時は出さない）
    with monkeypatch.context() as m:
        m.setitem(s.API, 'export/all', lambda body: {'results': [{'preset': 'p1', 'name': 'スタンダード', 'ok': True}, {'preset': 'p2', 'name': 'B', 'ok': False, 'error': 'write-failed'}]})
        ui.click_sel('.presetExtra button', text='全部のセットを書き込む')
        ui.until(f"{TOAST}.classList.contains('err') && {TOAST}.innerText.startsWith('B:')", what='1つ失敗の知らせ')
    quiet(ui)
    # ワンクリック（起動に失敗）・全部のセットをワンクリック
    def no_app(url):
        raise OSError('no app')
    fake_oneclick(ui, monkeypatch, no_app)
    ui.open('recommend')
    ui.click_sel('.outBox button', text='BSManager で入れる')
    toast_has(ui, '起動できませんでした', 'err')
    quiet(ui)
    ui.click_sel('.presetExtra button', text='全部のセットを BSManager で入れる')
    ui.until(f"{TOAST}.classList.contains('err') && {TOAST}.innerText.includes('スタンダード:')", what='全部失敗の知らせ')
    quiet(ui)
    # ファイルに保存（exe 版の失敗・やめた時）・ブラウザで開いている時の失敗
    ui.js("window.pywebview={api:{save_playlist:p=>Promise.resolve({ok:false, error:'write-failed'})}}; true")
    ui.click_sel('.outBox button', text='ファイルに保存')
    toast_has(ui, '書き込めませんでした', 'err')
    quiet(ui)
    ui.js("window.pywebview.api.save_playlist=()=>Promise.resolve({ok:false, error:'cancelled'}); true")
    ui.click_sel('.outBox button', text='ファイルに保存')
    quiet(ui)
    ui.js("delete window.pywebview; true")
    with monkeypatch.context() as m:
        m.setitem(s.API, 'export/file', fail('internal'))
        ui.click_sel('.outBox button', text='ファイルに保存')
        toast_has(ui, '出力に失敗しました', 'err')
    quiet(ui)


@pytest.mark.parametrize('op', [op_write, op_rebuild], ids=['write', 'rebuild'])
@pytest.mark.parametrize('public', [1, None], ids=['public', 'unknown'])
def test_no_warning_unless_private(ui, monkeypatch, public, op):
    ui.serve.STORE.set_meta('stats_public', public)
    toast_has(ui, op(ui, monkeypatch))
    quiet(ui)


@pytest.mark.parametrize('op', [op_write, op_rebuild], ids=['write', 'rebuild'])
def test_no_warning_when_setting_is_off(ui, monkeypatch, op):
    private(ui)
    ui.serve.SETTINGS.update({'stats_notice': False})
    toast_has(ui, op(ui, monkeypatch))
    quiet(ui)


# ---- 設定でオフ（その場で効く） ----
def test_setting_takes_effect_without_reopening(ui):
    s = ui.serve
    private(ui)
    ui.open('settings')
    ui.js("window.__same=1; true")   # 開き直していないことの目印
    assert ui.js("document.querySelector('#grp_net .row[data-key=\"stats_notice\"] .lbl').innerText") == LABEL
    desc = ui.js("document.querySelector('#grp_net .row[data-key=\"stats_notice\"] .desc').innerText")
    assert 'プレイリストを作る・出すたびに' in desc and 'ホームにも' in desc
    assert ui.js("document.getElementById('set_stats_notice').checked") is True
    # オフにする → 警告もホームの知らせも出ない
    ui.click_sel('#set_stats_notice')
    ui.wait(0.3)
    assert s.SETTINGS.get('stats_notice') is False
    nav(ui, 'recommend')
    click_write(ui)
    toast_has(ui, '曲を書き込みました')
    quiet(ui)
    nav(ui, 'home')
    assert not ui.js("!!document.querySelector('.statsNotice')")
    # オンに戻す → どちらも出る
    nav(ui, 'settings')
    assert ui.js("document.getElementById('set_stats_notice').checked") is False
    ui.click_sel('#set_stats_notice')
    ui.wait(0.3)
    assert s.SETTINGS.get('stats_notice') is True
    nav(ui, 'recommend')
    click_write(ui)
    ui.until(SHOWN, what='オンに戻すと警告')
    close_dialog(ui)
    nav(ui, 'home')
    assert ui.js("!!document.querySelector('.statsNotice')")
    # オフにしてから「初期値に戻す」（通信のまとまり）でも、その場でオンに戻る
    nav(ui, 'settings')
    ui.click_sel('#set_stats_notice')
    ui.wait(0.3)
    assert s.SETTINGS.get('stats_notice') is False
    ui.click_sel('#grp_net .cardHead button', text='初期値に戻す')
    ui.until("document.getElementById('set_stats_notice')?.checked===true", what='初期値に戻る')
    nav(ui, 'home')
    assert ui.js("!!document.querySelector('.statsNotice')")
    assert ui.js("window.__same") == 1


# ---- 公開を確かめた後 ----
def test_confirming_public_stops_warning(ui, monkeypatch):
    s = ui.serve
    private(ui)
    row = fake_players(s, monkeypatch, s.SETTINGS.get('player_id'), public=False)
    fake = FakeRefresher(total=2)
    monkeypatch.setattr(s, 'REFRESHER', fake)
    opened = []
    monkeypatch.setattr(s, 'open_browser', opened.append)
    ui.open('recommend')
    click_write(ui)
    ui.until(SHOWN, what='警告')
    ui.click_sel('.statsDlg .statsOpen', text='BeatLeader の設定を開く')
    ui.wait(0.2)
    assert opened == ['https://beatleader.com/settings#scores'] and ui.js(SHOWN)   # 開いても閉じない
    ui.click_sel('.statsDlg .statsCheck')
    ui.until(DLG + ".innerText.includes('まだ非公開')", what='まだの知らせ')
    assert fake.started == 0
    row['stats_public'] = True
    ui.click_sel('.statsDlg .statsCheck')
    ui.until("!" + DLG, what='公開を確かめて閉じる')
    ui.wait(0.3)
    assert fake.started == 1 and s.STORE.meta('stats_public') == '1'   # 公開になったらすぐ取り直す
    ui.until("!document.getElementById('refreshBtn').disabled && !document.body.dataset.busy", what='取り直しの終わり')
    # 以後は出ない
    click_write(ui)
    toast_has(ui, '曲を書き込みました')
    quiet(ui)
    nav(ui, 'home')
    assert not ui.js("!!document.querySelector('.statsNotice')")


# ---- 小さな画面・知らせからはオフにできない ----
def test_no_off_button_in_dialog_or_home_notice(ui):
    s = ui.serve
    private(ui)
    ui.open('home')
    hint = f'設定の「通信」にある「{LABEL}」で止められます'
    assert ui.js("[...document.querySelectorAll('.statsNotice button')].map(b=>b.innerText)") == ['公開にする方法']
    assert ui.js("document.querySelectorAll('.statsNotice input, .statsOff').length") == 0
    assert hint in ui.js("document.querySelector('.statsNotice').innerText")
    ui.click_sel('.statsNotice .statsHow', text='公開にする方法')
    ui.until(SHOWN, what='案内の小さな画面')
    assert ui.js("[...document.querySelectorAll('.statsDlg button')].map(b=>b.innerText)") == DLG_BUTTONS
    assert ui.js("document.querySelectorAll('.statsDlg input, .statsDlg select').length") == 0
    assert hint in ui.js(DLG + ".innerText")
    close_dialog(ui)
    assert s.SETTINGS.get('stats_notice') is True and ui.js("!!document.querySelector('.statsNotice')")   # 閉じても消えない
    # 文面の場所と項目の名前は、設定画面の本物と同じ
    ui.open('settings')
    assert ui.js("document.querySelector('#grp_net .cardHead h2').innerText") == '通信'
    assert ui.js("document.querySelector('#grp_net .row[data-key=\"stats_notice\"] .lbl').innerText") == LABEL
