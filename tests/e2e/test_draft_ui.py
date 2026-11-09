# 画面の操作: 組のセット（ほかのセットと同じ曲を入れないセットどうし）の「作り直す」と「組を交互に選び直す」
# （小さな画面で「組のセット全部を交互に選び直す」か「このセットだけ作り直す」を選ぶ。presets.js の rebuildDialog・serve.py の _draft）
MODAL = "!!document.querySelector('.modal .rbChoice')"
TOAST = "document.querySelector('#toast').innerText"


def diff_sets(ui):
    """難しさ違いのセット（着実・チャレンジング・5曲ずつ）を作る。着実のセットが開く"""
    return ui.serve._api_presets_difficulty_set({'copyFrom': 'p1', 'levels': ['steady', 'challenge'], 'size': 5})['ids']


def age(ui):
    """保存したリストの作った時刻を昔（1）にする（作り直したかを made_at で見る）"""
    with ui.serve.STORE.tx():
        ui.serve.STORE.conn.execute('UPDATE rec_lists SET made_at=1')


def made_at(ui, pid):
    return ui.serve.STORE.q1('SELECT made_at FROM rec_lists WHERE preset=?', (pid,))['made_at']


def test_group_button_and_dialog(ui):
    steady, hard = diff_sets(ui)
    ui.open('recommend')
    assert ui.js("document.querySelector('.presetTab.on')?.innerText") == '着実'
    assert ui.js("document.querySelector('.presetExtra .groupBtn').innerText") == '組を交互に選び直す'
    ui.click_sel('.groupBtn')
    ui.until(MODAL, what='作り直すの小さな画面')
    assert ui.js("[...document.querySelectorAll('.modal .rbChoice b')].map(b=>b.innerText)") == [
        '組のセット全部を交互に選び直す', 'このセットだけ作り直す']
    assert '組: 着実・チャレンジング' in ui.js("document.querySelector('.modal p').innerText")
    assert ui.js("document.activeElement.dataset.group") == 'all'   # 組全部の方に印
    # Esc・やめるは何もしない
    age(ui)
    ui.key('Escape')
    ui.until("!document.querySelector('.modal')", what='Esc で閉じる')
    ui.click_sel('.groupBtn')
    ui.until(MODAL)
    ui.click_sel('.modal .modalFoot button', text='やめる')
    ui.until("!document.querySelector('.modal')", what='やめるで閉じる')
    assert made_at(ui, steady) == made_at(ui, hard) == 1
    # 組のセット全部: どちらのリストも作り直す
    ui.click_sel('.groupBtn')
    ui.until(MODAL)
    ui.click_sel('.modal .rbChoice', text='組のセット全部')
    ui.until(f"!document.querySelector('.modal') && {TOAST}.includes('交互に選び直しました')", what='組全部を選び直した')
    assert '組のセット（着実・チャレンジング）' in ui.js(TOAST)
    assert made_at(ui, steady) > 1 and made_at(ui, hard) > 1
    # このセットだけ: ほかのセットのリストはそのまま
    age(ui)
    ui.click_sel('.groupBtn')
    ui.until(MODAL)
    ui.click_sel('.modal .rbChoice', text='このセットだけ')
    ui.until(f"!document.querySelector('.modal') && {TOAST}.includes('今のデータでリストを作り直しました')", what='このセットだけ作り直した')
    assert made_at(ui, steady) > 1 and made_at(ui, hard) == 1


def test_no_group_button_outside_group(ui):
    diff_sets(ui)
    # 組でないセット（元のセット）を開くとボタンは出ない
    ui.serve.SETTINGS.update({'active_preset': 'p1'})
    ui.open('recommend')
    assert ui.js("document.querySelector('.presetTab.on')?.innerText") == 'スタンダード'
    assert ui.js("!document.querySelector('.groupBtn')")
    # 組がこのセットだけ（ほかにオンのセットが無い）の時も出ない
    pid = ui.serve.SETTINGS.add_preset('ひとつだけ', copy_from='p1')
    ui.serve.SETTINGS.update({'exclude_other_sets': True}, preset=pid)
    for p in ui.serve.SETTINGS.preset_ids():
        if p not in ('p1', pid):
            ui.serve.SETTINGS.delete_preset(p)
    ui.serve.SETTINGS.update({'active_preset': pid})
    ui.open('recommend')
    assert ui.js("!document.querySelector('.groupBtn')")


def pending(ui, pid):
    """保存したリストの曲のうち、まだ「今後出さない」でない最初の曲をそうする（DB を直接。作り直すと入れ替わる状態）"""
    r = ui.serve._api_recommend({'preset': pid})
    never = {x['leaderboard_id'] for x in ui.serve.STORE.q("SELECT leaderboard_id FROM user_marks WHERE mark='never'")}
    with ui.serve.STORE.tx():
        ui.serve.STORE.conn.execute("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', 0)",
                                    (next(c['leaderboard_id'] for c in r['items'] if c['leaderboard_id'] not in never),))


def test_rebuild_in_pending_bar(ui):
    steady, hard = diff_sets(ui)
    # 組のセットで知らせの「作り直す」を押すと、選ぶ小さな画面
    pending(ui, steady)
    age(ui)
    ui.open('recommend')
    ui.until("!!document.querySelector('.pendBar')", what='作り直すと変わる知らせ')
    ui.click_sel('.pendBar > button', text='作り直す')
    ui.until(MODAL, what='作り直すの小さな画面')
    ui.click_sel('.modal .rbChoice', text='組のセット全部')
    ui.until(f"!document.querySelector('.pendBar') && {TOAST}.includes('交互に選び直しました')", what='組全部を選び直した')
    assert made_at(ui, steady) > 1 and made_at(ui, hard) > 1
    # 組でないセットは今までどおり、押すとすぐそのセットだけ作り直す
    ui.serve.SETTINGS.update({'active_preset': 'p1'})
    pending(ui, 'p1')
    age(ui)
    ui.open('recommend')
    ui.until("!!document.querySelector('.pendBar')")
    ui.click_sel('.pendBar > button', text='作り直す')
    ui.until(f"!document.querySelector('.pendBar') && {TOAST}.includes('今のデータでリストを作り直しました')", what='すぐ作り直す')
    assert ui.js("!document.querySelector('.modal')")
    assert made_at(ui, 'p1') > 1 and made_at(ui, steady) == made_at(ui, hard) == 1
