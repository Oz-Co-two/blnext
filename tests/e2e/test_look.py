# 見やすさの調整: 文字の大きさのスライダー（上の帯）・上の帯のメニューと更新ボタン・設定画面の目次
import time

ZOOM = "document.documentElement.style.zoom"
SLIDER = "document.getElementById('fontScale')"


def saved(ui, value, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if ui.serve.SETTINGS.get('ui_scale') == value:
            return True
        time.sleep(0.05)
    raise AssertionError('文字の大きさ %s が保存されない（%s）' % (value, ui.serve.SETTINGS.get('ui_scale')))


def test_font_scale_slider(ui):
    ui.open('home')
    assert ui.js(ZOOM) == '1' and ui.js(SLIDER + ".value") == '0'
    # マウスでスライダーの右端を押すと最大（145%）。画面全体が大きくなり、保存される
    r = ui.js("(()=>{const r=%s.getBoundingClientRect(); return {x:r.right-2, y:r.top+r.height/2};})()" % SLIDER)
    ui.click(r['x'], r['y'])
    ui.until(ZOOM + "==='1.45'", what='最大に拡大する')
    saved(ui, 145)
    assert not ui.layout_issues()
    # 開き直しても同じ大きさ（設定に残る）
    ui.open('recommend')
    assert ui.js(ZOOM) == '1.45' and ui.js(SLIDER + ".value") == '3'
    assert not ui.layout_issues()
    # キーボードで1段ずつ下げる
    ui.js(SLIDER + ".focus(); true")
    ui.key('ArrowLeft')
    ui.until(ZOOM + "==='1.3'", what='130% に下がる')
    saved(ui, 130)
    assert '130%' in ui.js(SLIDER + ".closest('.fontCtl').title")
    ui.key('ArrowRight')
    ui.until(ZOOM + "==='1.45'", what='145% に戻る')
    saved(ui, 145)
    # 狭いウィンドウでは、崩れないよう自動で控えめに（説明にも出す）
    ui.resize(960)
    ui.open('home')
    ui.until(ZOOM + "==='1.33'", what='狭いので 133% に抑える')
    assert 'ウィンドウが狭い' in ui.js(SLIDER + ".closest('.fontCtl').title")
    assert not ui.layout_issues()
    # 設定画面の選択欄と上のスライダーはつながっている
    ui.resize(1280)
    ui.open('settings')
    assert ui.js("document.getElementById('set_ui_scale').value") == '145'
    ui.js("(()=>{const e=document.getElementById('set_ui_scale'); e.value='100'; e.dispatchEvent(new Event('change'));})()")
    ui.until(ZOOM + "==='1' && %s.value==='0'" % SLIDER, what='設定画面で戻すと、上のスライダーも戻る')
    saved(ui, 100)


def test_topbar_looks_like_a_menu(ui):
    ui.open('home')
    # 上の帯は本文と違う地の色
    assert ui.js("getComputedStyle(document.getElementById('topbar')).backgroundColor") != ui.js("getComputedStyle(document.body).backgroundColor")
    # 「更新」は枠のあるボタンで、メニューのボタンと同じくらいの大きさ
    rb = ui.js("(()=>{const r=document.getElementById('refreshBtn').getBoundingClientRect(); return [r.width, r.height];})()")
    nb = ui.js("(()=>{const r=document.querySelector('#nav button').getBoundingClientRect(); return [r.width, r.height];})()")
    assert abs(rb[1] - nb[1]) <= 2 and rb[0] >= nb[0] * 0.9
    assert ui.js("getComputedStyle(document.getElementById('refreshBtn')).borderTopWidth") != '0px'


def test_settings_jump_menu(ui):
    ui.open('settings')
    top = "(id=>document.getElementById(id).getBoundingClientRect().top-document.getElementById('main').getBoundingClientRect().top)"
    on = "document.querySelector('.setNav .navItem.on').innerText"
    assert ui.js(on) == '基本'
    ui.click_sel('.setNav .navItem', text='リストの条件')   # セットごとの部分の項目へ飛ぶ
    ui.until(f"Math.abs({top}('grp_list'))<30", what='リストの条件へ飛ぶ')
    assert ui.js(on) == 'リストの条件'
    # 目次は付いてくる（スクロールしても見えている）
    assert ui.js("document.querySelector('.setNav').getBoundingClientRect().top>=document.getElementById('main').getBoundingClientRect().top-1")
    ui.click_sel('.setNav .navItem', text='通信')
    ui.until(f"Math.abs({top}('grp_net'))<30", what='通信へ飛ぶ')
    # 手でスクロールすると、見ている所に印が移る
    ui.wait(0.5)
    ui.js("(()=>{const m=document.getElementById('main'); m.scrollTop += document.getElementById('grp_presetCard').getBoundingClientRect().top - m.getBoundingClientRect().top; m.dispatchEvent(new Event('scroll'));})()")
    ui.until(on + "==='セットの名前・画像'", what='スクロールで印が移る')
    # 「全部のセットで共通」「セットごと」の見出しも押せて、その部分の見出し（基本・セットの名前より上）へ飛ぶ
    ui.click_sel('.setNav .navHead', text='全部のセットで共通')
    ui.until(f"Math.abs({top}('setCommon'))<30", what='共通の部分の見出しへ飛ぶ')
    assert ui.js(on) == '基本' and ui.js(f"{top}('setCommon')<{top}('grp_basic')")
    ui.click_sel('.setNav .navHead', text='セットごと')
    ui.until(f"Math.abs({top}('setPreset'))<30", what='セットごとの部分の見出しへ飛ぶ')
    assert ui.js(on) == 'セットの名前・画像' and ui.js(f"{top}('setPreset')<{top}('grp_presetCard')")
    assert ui.js("document.querySelector('#setPreset .partHead').getBoundingClientRect().top>=document.getElementById('main').getBoundingClientRect().top")


COND = ("[...document.querySelectorAll('.condItem')].map(e=>{const l=e.querySelector('.condLbl').getBoundingClientRect(),"
        "b=e.querySelector('.condBody').getBoundingClientRect(); return {lt:l.left, top:l.top, lb:l.bottom, bl:b.left, bt:b.top};})")
POS = "(s=>{const r=document.querySelector(s).getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right};})"


def test_conditions_layout(ui):
    # 狭い時（条件が一覧の上に移る）は、各項目を「左に名前・右に入力」の1行にして縦に並べる（格子に並べるとばらばらに見えた）
    ui.resize(960)
    ui.open('recommend')
    assert ui.js(POS + "('.recCond')")['top'] < ui.js(POS + "('.recList')")['top']
    r = ui.js(COND)
    assert len(r) == 9   # 難しさ・ほかのセットと同じ曲（2026-10-18）を含む。プレイリスト内の並び（order）は 2026-10-18 になくした
    assert all(x['bl'] > x['lt'] + 100 and x['bt'] < x['lb'] for x in r)          # 入力は名前の右・同じ行
    tops = [x['top'] for x in r]
    assert all(b > a + 10 for a, b in zip(tops, tops[1:]))                          # 項目は上から順に1つずつ
    assert max(x['bl'] for x in r) - min(x['bl'] for x in r) < 2                    # 入力の列がそろっている
    assert_separated(ui)
    # 広い時は一覧の右。名前の下に入力
    ui.resize(1600)
    ui.open('recommend')
    assert ui.js(POS + "('.recCond')")['left'] > ui.js(POS + "('.recList')")['right']
    r = ui.js(COND)
    assert all(x['bt'] >= x['lb'] - 1 for x in r)
    assert_separated(ui)


def assert_separated(ui):
    """条件の項目の間に区切りの線がある（どこまでが1つの項目かが分かる）。最初の項目の上には無い"""
    b = ui.js("[...document.querySelectorAll('.condItem')].map(e=>getComputedStyle(e).borderTopWidth)")
    assert b[0] == '0px' and all(x != '0px' for x in b[1:]), b
