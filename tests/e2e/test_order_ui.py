# 画面の操作: おすすめの一覧の並び（一覧の順＝書き出すプレイリストの順）。見出しで並べ替える・基本の並びに戻す・
# 行の左の持ち手でドラッグする・Alt+↑↓ で動かす。並べた順は保存したリスト（serve.py の rec_lists）に残り、開き直しても同じ
import json, time

from core.estimate.order import GROUPS, group_of

IDS ="[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.dataset.id)"
RESET = 'button.orderReset'


def saved(app, preset='p1'):
    row = app.serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (preset,))
    return [c['leaderboard_id'] for c in json.loads(row['payload'])['items']]


def until_saved(app, want, timeout=10.0):
    """保存したリストの並びが want になるまで待つ（画面は先に並びを変え、保存は後から届く）"""
    end = time.time() + timeout
    while time.time() < end:
        if saved(app) == want:
            return
        app.wait(0.05)
    assert saved(app) == want


def until_ids(app, want):
    app.until(f"JSON.stringify({IDS})===JSON.stringify({json.dumps(want)})", what='一覧の並び %s' % want)


def col(app, i):
    vals = app.js("[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.children[%d].innerText)" % i)
    return [float(v.split('\n')[0].rstrip('%').replace(',', '')) if v.strip() not in ('—', '') else None for v in vals]


def alt_key(app, key):
    """Alt を押しながら矢印のキー（ブラウザの本物の入力として送る）"""
    vk = {'ArrowUp': 38, 'ArrowDown': 40}[key]
    base = dict(key=key, code=key, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=1)
    app.call('Input.dispatchKeyEvent', type='rawKeyDown', **base)
    app.call('Input.dispatchKeyEvent', type='keyUp', **base)
    app.wait(0.1)


def drag(app, lid, target, after):
    """lid の行の持ち手をつかみ、target の行の下半分（after）か上半分へ落とす。ブラウザの本物のドラッグ（CDP の
    Input.setInterceptDrags でドラッグの中身を受け取り、Input.dispatchDragEvent で落とす）"""
    p = app.js("(()=>{const g=document.querySelector('.recItem[data-item=\"%s\"] .grip'); g.scrollIntoView({block:'center'});"
               "const r=g.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+Math.min(r.height/2, 16)};})()" % lid)
    q = app.js("(()=>{const r=document.querySelector('.recItem[data-item=\"%s\"] .recRow').getBoundingClientRect();"
               "return {x:r.left+r.width/2, y:%s};})()" % (target, 'r.bottom-4' if after else 'r.top+4'))
    app._events.clear()
    app.call('Input.setInterceptDrags', enabled=True)
    try:
        app.call('Input.dispatchMouseEvent', type='mouseMoved', x=p['x'], y=p['y'])
        app.call('Input.dispatchMouseEvent', type='mousePressed', x=p['x'], y=p['y'], button='left', buttons=1, clickCount=1)
        app.call('Input.dispatchMouseEvent', type='mouseMoved', x=p['x'], y=p['y'] + 8, button='left', buttons=1)
        app.call('Input.dispatchMouseEvent', type='mouseMoved', x=q['x'], y=q['y'], button='left', buttons=1)
        end, data = time.time() + 5, None
        while data is None and time.time() < end:
            data = next((e['params']['data'] for e in app._events if e.get('method') == 'Input.dragIntercepted'), None)
            app.js('0')
        assert data is not None, 'ドラッグが始まらない'
        for typ in ('dragEnter', 'dragOver'):
            app.call('Input.dispatchDragEvent', type=typ, x=q['x'], y=q['y'], data=data)
        assert app.js(f"document.querySelector('.recItem[data-item=\"{lid}\"]').classList.contains('dragging')")
        if target != lid:   # 落とす位置の線（自分の上では出さない）
            cls = 'dropAfter' if after else 'dropBefore'
            app.until(f"document.querySelector('.recItem[data-item=\"{target}\"]').classList.contains('{cls}')", what='落とす位置の線')
            assert app.js("document.querySelectorAll('.dropAfter,.dropBefore').length") == 1
        app.call('Input.dispatchDragEvent', type='drop', x=q['x'], y=q['y'], data=data)
        app.call('Input.dispatchMouseEvent', type='mouseReleased', x=q['x'], y=q['y'], button='left', buttons=0, clickCount=1)
    finally:
        app.call('Input.setInterceptDrags', enabled=False)
    app.wait(0.1)


def test_header_sort_is_saved_and_reset(ui):
    ui.open('recommend')
    base = ui.js(IDS)
    assert base == saved(ui) and ui.js(f"document.querySelector('{RESET}').disabled")   # 基本の並び＝戻すボタンは押せない
    assert ui.js("[...document.querySelectorAll('.recSect')].length") >= 1               # 区分の見出し
    ui.click_sel('.recRow.head .th', text='推定精度')
    ui.until(f"JSON.stringify({IDS})!=={json.dumps(json.dumps(base))}", what='推定精度の高い順')
    est = col(ui, 5)
    assert est == sorted(est, reverse=True)
    assert ui.js("document.querySelector('.recRow.head .th.on').innerText") == '推定精度▼'
    until_saved(ui, ui.js(IDS))
    # もう一度押すと逆の順
    ui.click_sel('.recRow.head .th', text='推定精度')
    ui.until("document.querySelector('.recRow.head .th.on').innerText==='推定精度▲'", what='向きが変わる')
    asc = ui.js(IDS)
    assert col(ui, 5) == sorted(est)
    until_saved(ui, asc)
    # 開き直しても同じ順（手で並べた時は区分の見出しを出さず、戻すボタンを押せる）
    ui.open('recommend')
    assert ui.js(IDS) == asc and ui.js("document.querySelectorAll('.recSect').length") == 0
    assert not ui.js(f"document.querySelector('{RESET}').disabled")
    # 基本の並びに戻す
    ui.click_sel(RESET)
    until_ids(ui, base)
    until_saved(ui, base)
    ui.until(f"document.querySelector('{RESET}').disabled", what='戻すボタンが押せなくなる')
    assert ui.js("document.querySelectorAll('.recSect').length") >= 1
    # 区分の見出し（区分の順）でも並べ替えられる
    ui.click_sel('.recRow.head .th', text='★')
    ui.until("document.querySelector('.recRow.head .th.on')?.innerText==='★▼'", what='★の高い順')
    st = col(ui, 2)
    assert st == sorted(st, reverse=True)
    ui.click_sel('.recRow.head .th', text='区分')
    ui.until("document.querySelector('.recRow.head .th.on')?.innerText==='区分▲'", what='区分の順')
    until_saved(ui, ui.js(IDS))
    row = ui.serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")
    groups = [GROUPS.index(group_of(c)) for c in json.loads(row['payload'])['items']]
    assert groups == sorted(groups) and len(set(groups)) > 1   # プレイ済み → 新曲 → 未クリア → 挑戦（区分の中は★の順のまま）


def test_drag_moves_row_with_its_reasons(ui):
    ui.open('recommend')
    ids = ui.js(IDS)
    # 下へ: 1曲目を3曲目の下半分へ落とす → 3曲目の後ろ
    drag(ui, ids[0], ids[2], after=True)
    want = [ids[1], ids[2], ids[0]] + ids[3:]
    until_ids(ui, want)
    until_saved(ui, want)
    assert not ui.js("document.querySelectorAll('.dropAfter,.dropBefore,.dragging').length")   # 線・ドラッグ中の印は消える
    # 理由を開いた行も、開いたまま行ごと動く（上へ: 4曲目の上半分へ）
    lid = want[1]
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    ui.until(f"!!document.querySelector('.recItem[data-item=\"{lid}\"] .reasons')", what='理由を開く')
    drag(ui, lid, want[4], after=False)
    want2 = [want[0], want[2], want[3], lid] + want[4:]
    until_ids(ui, want2)
    until_saved(ui, want2)
    assert ui.js(f"!!document.querySelector('.recItem[data-item=\"{lid}\"].recGroup .reasons')")
    # 自分の上に落としても変わらない
    drag(ui, want2[0], want2[0], after=True)
    ui.wait(0.3)
    assert ui.js(IDS) == want2 and saved(ui) == want2
    ui.open('recommend')
    assert ui.js(IDS) == want2


def test_alt_arrow_moves_selected_row(ui):
    ui.open('recommend')
    ids = ui.js(IDS)
    ui.js("document.querySelector('.recList').focus()")
    ui.key('ArrowDown')                       # 1曲目を選ぶ
    alt_key(ui, 'ArrowDown')                  # 1つ下へ
    alt_key(ui, 'ArrowDown')                  # 続けてもう1つ下へ（保存を待たずに動かせる）
    want = [ids[1], ids[2], ids[0]] + ids[3:]
    until_ids(ui, want)
    until_saved(ui, want)
    assert ui.js("document.querySelector('.recRow.sel').dataset.id") == ids[0]   # 選んだまま
    alt_key(ui, 'ArrowUp')
    want = [ids[1], ids[0], ids[2]] + ids[3:]
    until_ids(ui, want)
    until_saved(ui, want)
    ui.key('ArrowUp')                         # Alt なしは選ぶだけ（並びは変えない）
    assert ui.js("document.querySelector('.recRow.sel').dataset.id") == ids[1] and ui.js(IDS) == want
    alt_key(ui, 'ArrowUp')                    # 一番上より上へは動かない
    ui.wait(0.2)
    assert ui.js(IDS) == want and saved(ui) == want


def test_remove_keeps_manual_order(ui):
    # 外す・固定などの操作では手で並べた順を保つ（次点から入った曲は基本の並びの位置）
    ui.open('recommend')
    rev = ui.js(IDS)[::-1]
    ui.serve._api_recommend_order({'preset': 'p1', 'ids': rev})
    ui.open('recommend')
    assert ui.js(IDS) == rev
    gone = rev[1]
    ui.click_sel(f'.recRow[data-id="{gone}"] button.icon', index=1)   # ✕
    ui.until(f"!{IDS}.includes('{gone}')", what='外した曲が消える')
    now = ui.js(IDS)
    assert [i for i in now if i in rev] == [i for i in rev if i != gone] and len(now) == len(rev)
    assert not ui.js(f"document.querySelector('{RESET}').disabled")
