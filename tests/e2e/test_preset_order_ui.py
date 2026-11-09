# 画面の操作: セット（設定のパターン）のタブの並べ替え。おすすめの画面のタブをドラッグする・Alt+←→（Alt+↑↓ も）で動かす。
# 並びは設定の presets に残り（開き直しても同じ）、ホームの「前回のプレイリストの結果」のタブも同じ並びになる
import json, time

TABS = "[...document.querySelectorAll('.presetTabs:not(.revTabs) .presetTab:not(.add)')].map(b=>b.dataset.preset)"
REV_TABS = "[...document.querySelectorAll('.revTabs .presetTab')].map(b=>b.dataset.preset)"


def three_sets(ui):
    """セットを3つ（スタンダード・B・C）にし、B と C も出力しておく（ホームの振り返りにタブが3つ。スタンダードは作り物のデータの出力）"""
    b = ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    c = ui.serve.SETTINGS.add_preset('C', copy_from='p1')
    for pid in (b, c):
        ui.serve._api_export({'preset': pid})
    return b, c


def until_tabs(ui, want):
    ui.until(f"JSON.stringify({TABS})===JSON.stringify({json.dumps(want)})", what='タブの並び %s' % want)


def until_saved(ui, want, timeout=10.0):
    """設定に残った並びが want になるまで待つ（画面は先に並びを変え、保存は後から届く）"""
    end = time.time() + timeout
    while time.time() < end and ui.serve.SETTINGS.preset_ids() != want:
        ui.wait(0.05)
    assert ui.serve.SETTINGS.preset_ids() == want


def alt_key(ui, key):
    """Alt を押しながら矢印のキー（ブラウザの本物の入力として送る）"""
    vk = {'ArrowLeft': 37, 'ArrowUp': 38, 'ArrowRight': 39, 'ArrowDown': 40}[key]
    base = dict(key=key, code=key, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=1)
    ui.call('Input.dispatchKeyEvent', type='rawKeyDown', **base)
    ui.call('Input.dispatchKeyEvent', type='keyUp', **base)
    ui.wait(0.1)


def drag_tab(ui, pid, target, after):
    """pid のタブをつかみ、target のタブの右半分（after）か左半分へ落とす。ブラウザの本物のドラッグ（CDP の
    Input.setInterceptDrags でドラッグの中身を受け取り、Input.dispatchDragEvent で落とす）"""
    sel = ".presetTabs:not(.revTabs) .presetTab[data-preset=\"%s\"]"
    p = ui.js("(()=>{const r=document.querySelector('%s').getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2};})()"
              % (sel % pid))
    q = ui.js("(()=>{const r=document.querySelector('%s').getBoundingClientRect(); return {x:%s, y:r.top+r.height/2};})()"
              % (sel % target, 'r.right-4' if after else 'r.left+4'))
    ui._events.clear()
    ui.call('Input.setInterceptDrags', enabled=True)
    try:
        ui.call('Input.dispatchMouseEvent', type='mouseMoved', x=p['x'], y=p['y'])
        ui.call('Input.dispatchMouseEvent', type='mousePressed', x=p['x'], y=p['y'], button='left', buttons=1, clickCount=1)
        ui.call('Input.dispatchMouseEvent', type='mouseMoved', x=p['x'] + 8, y=p['y'], button='left', buttons=1)
        ui.call('Input.dispatchMouseEvent', type='mouseMoved', x=q['x'], y=q['y'], button='left', buttons=1)
        end, data = time.time() + 5, None
        while data is None and time.time() < end:
            data = next((e['params']['data'] for e in ui._events if e.get('method') == 'Input.dragIntercepted'), None)
            ui.js('0')
        assert data is not None, 'ドラッグが始まらない'
        for typ in ('dragEnter', 'dragOver'):
            ui.call('Input.dispatchDragEvent', type=typ, x=q['x'], y=q['y'], data=data)
        assert ui.js(f"document.querySelector('{sel % pid}').classList.contains('dragging')")
        cls = 'dropAfter' if after else 'dropBefore'
        ui.until(f"document.querySelector('{sel % target}').classList.contains('{cls}')", what='落とす位置の線')
        ui.call('Input.dispatchDragEvent', type='drop', x=q['x'], y=q['y'], data=data)
        ui.call('Input.dispatchMouseEvent', type='mouseReleased', x=q['x'], y=q['y'], button='left', buttons=0, clickCount=1)
    finally:
        ui.call('Input.setInterceptDrags', enabled=False)
    ui.wait(0.1)


def test_drag_tab_and_home_follows(ui):
    b, c = three_sets(ui)
    ui.open('home')
    assert ui.js(REV_TABS) == ['p1', b, c]                       # ホームの振り返りのタブはセットの並び（出力の新しい順ではない）
    ui.open('recommend')
    assert ui.js(TABS) == ['p1', b, c]
    drag_tab(ui, c, 'p1', after=False)                          # C をスタンダードの前へ
    until_tabs(ui, [c, 'p1', b])
    assert ui.js("document.querySelector('.presetTab.on').dataset.preset") == 'p1'   # 開いているセットは変わらない
    assert ui.js("document.querySelectorAll('.dropBefore,.dropAfter,.dragging').length") == 0
    until_saved(ui, [c, 'p1', b])
    drag_tab(ui, 'p1', b, after=True)                           # スタンダードを B の後ろへ
    until_tabs(ui, [c, b, 'p1'])
    until_saved(ui, [c, b, 'p1'])
    ui.open('recommend')                                        # 開き直しても同じ並び
    assert ui.js(TABS) == [c, b, 'p1']
    # ホームの振り返りのタブも同じ並び。最初に開くのは一番前のセット
    ui.open('home')
    assert ui.js(REV_TABS) == [c, b, 'p1']
    assert ui.js("document.querySelector('.revTabs .presetTab.on').dataset.preset") == c
    assert ui.js("document.querySelector('.revTabs').closest('.card').querySelector('.when').textContent").endswith('に出力')
    # 設定の画面・似ている人の画面のタブも同じ並び
    for screen in ('settings', 'similar'):
        ui.open(screen)
        assert ui.js(TABS) == [c, b, 'p1'], screen


def test_alt_arrow_moves_tab(ui):
    b, c = three_sets(ui)
    ui.open('recommend')
    focus = lambda pid: ui.js(f"document.querySelector('.presetTabs .presetTab[data-preset=\"{pid}\"]').focus(); true")
    focused = "document.activeElement.dataset.preset"
    focus(b)
    alt_key(ui, 'ArrowRight')                                   # B を1つ右へ（フォーカスは B のまま＝続けて動かせる）
    until_tabs(ui, ['p1', c, b])
    assert ui.js(focused) == b
    alt_key(ui, 'ArrowRight')                                   # 一番右より右へは動かない
    alt_key(ui, 'ArrowLeft')
    alt_key(ui, 'ArrowLeft')                                    # 続けて2つ左へ（保存を待たずに動かせる）
    until_tabs(ui, [b, 'p1', c])
    assert ui.js("document.body.dataset.screen") == 'recommend'   # Alt+← でブラウザが「戻る」をしない
    until_saved(ui, [b, 'p1', c])
    alt_key(ui, 'ArrowDown')                                    # Alt+↑↓ でも動く（おすすめの一覧の曲と同じ作法）
    until_tabs(ui, ['p1', b, c])
    until_saved(ui, ['p1', b, c])
    ui.key('ArrowRight')                                        # Alt なしは動かさない
    assert ui.js(TABS) == ['p1', b, c]
    # タブを1つにすると、並べ替えの印（ドラッグ・説明）は出さない
    ui.serve.SETTINGS.delete_preset(b)
    ui.serve.SETTINGS.delete_preset(c)
    ui.open('recommend')
    assert ui.js("document.querySelector('.presetTabs .presetTab[data-preset=p1]').getAttribute('draggable')") is None
    assert ui.layout_issues() == []
