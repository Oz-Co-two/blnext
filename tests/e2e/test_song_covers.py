# 曲のカバー画像（util.js の songCover・serve.py の /cover/<hash>.jpg）: 曲名を出す行（おすすめの一覧と次点・ホームの伸ばした曲と
# 前回のリストの結果・似ている人の共通曲）の曲名の左に画像があり、読み込めていて（テストはネットに出ないので代わりの絵）、崩れていない
import pytest

from conftest import OUT

# 画面の画像を全部すぐ読ませて（見える所に近づくまで data-src に置いてある物も src にする）、読み終えるまで待つ
LOAD_ALL = ("(()=>{const im=[...document.querySelectorAll('img.songCover')];"
            " im.forEach(i=>{if(!i.getAttribute('src')) i.src=i.dataset.src;});"
            " return Promise.all(im.map(i=>i.complete?1:new Promise(r=>{i.onload=i.onerror=r;}))).then(()=>true);})()")
# 曲の行ごとに: 画像があるか・src の形・読めたか（naturalWidth）・曲名より左か
ROWS = r"""((rowSel, nameSel)=>[...document.querySelectorAll(rowSel)].map(r=>{
  const im=r.querySelector('img.songCover'), nm=r.querySelector(nameSel);
  if(!im) return {img:false};
  const p=new URL(im.src).pathname;
  return {img:true, path:p, cover:/^[/]cover[/][0-9a-f]{40}[.]jpg$/.test(p), loaded:im.complete&&im.naturalWidth>0,
          w:Math.round(parseFloat(getComputedStyle(im).width)), left:!nm||im.getBoundingClientRect().right<=nm.getBoundingClientRect().left+1};
}))(%s, %s)"""


def rows(ui, row_sel, name_sel):
    import json
    ui.js(LOAD_ALL)
    return ui.js(ROWS % (json.dumps(row_sel), json.dumps(name_sel)))


def ok(rs, n_min=1):
    assert len(rs) >= n_min, '曲の行がありません'
    for r in rs:
        assert r['img'], '画像の無い行があります'
        assert r['cover'], r['path']          # 作り物のデータの譜面は全部 hash がある → /cover/<hash>.jpg
        assert r['loaded'], r['path']         # 代わりの絵（BLN_NO_NET）が 200 で返って読めた
        assert r['left'], '画像が曲名の左にありません'


def check_layout(ui, name):
    ui.shot(OUT / 'screens' / f'cover_{name}.png', full=True)
    issues = ui.layout_issues()
    assert not issues, '崩れ:\n' + '\n'.join(f"[{i['kind']}] {i['where']} {i['text']}" for i in issues)


@pytest.mark.parametrize('width', (960, 1600))
def test_recommend_rows_have_covers(ui, width):
    ui.resize(width)
    ui.open('recommend')
    rs = rows(ui, '.recList:has(.listHead) .recRow:not(.head)', '.songName')
    ok(rs, 5)
    assert all(r['w'] == 64 for r in rs)     # 大きめ（64px の正方形）
    ok(rows(ui, '.recList:not(:has(.listHead)) .recRow:not(.head)', '.songName'))   # 次点
    check_layout(ui, f'recommend_{width}')


def test_home_rows_have_covers(ui):
    # 前のテストの出力（書き込み）で、一番新しい出力の振り返りがまだ無いことがある。今のデータで振り返りを作っておく
    import time
    from core import review
    review.update(ui.serve.STORE, ui.serve.SETTINGS.values()['player_id'], int(time.time()))
    ui.open('home')
    ok(rows(ui, 'ul.chg li.withCover', '.song'))                       # 自分が伸ばした曲
    ok(rows(ui, 'table.list:has(td.coverCell) tbody tr', 'td.name'), 3)   # 前回のプレイリストの結果
    check_layout(ui, 'home')


@pytest.mark.parametrize('width', (1280, 1600))
def test_similar_rows_have_covers(ui, width):
    ui.resize(width)
    ui.open('similar')
    ui.click_sel('table.list tbody tr', index=0)
    ui.until("document.querySelectorAll('.scrollBox tbody tr').length>1")
    rs = rows(ui, '.scrollBox tbody tr', 'td.name')
    ok(rs, 2)
    assert all(r['w'] == 40 for r in rs)     # この表だけ小さめ
    # 共通曲の表が箱に収まり、右の欄（その人の精度・上振れ）まで横にスクロールせずに見える
    fit = ui.js("(()=>{const b=document.querySelector('.scrollBox'); return [b.scrollWidth, b.clientWidth];})()")
    assert fit[0] <= fit[1] + 1, fit
    check_layout(ui, f'similar_{width}')


def test_covers_load_only_near_view(ui):
    """見える所に近づいた画像だけを読む（長い一覧で、見ない曲までアプリに取りに行かせない）。スクロールすると読む"""
    ui.resize(1280, 600)
    ui.open('recommend')
    # 見えている＝本文（#main。上の帯の下からスクロールする箱）の見える範囲に入っている
    seen = ("const v=document.getElementById('main').getBoundingClientRect();"
            " const shown=i=>{const r=i.getBoundingClientRect(); return r.bottom>v.top && r.top<v.bottom;};")
    state = ("(()=>{" + seen + " const im=[...document.querySelectorAll('img.songCover')];"
             " return {all:im.length, waiting:im.filter(i=>!i.getAttribute('src')).length,"
             " shownLate:im.filter(i=>!i.getAttribute('src') && shown(i)).length};})()")
    ui.until("document.querySelectorAll('img.songCover[src]').length>0")
    s = ui.js(state)
    assert s['all'] >= 10 and s['waiting'] > 0, s     # 下の方の画像はまだ読んでいない
    assert s['shownLate'] == 0, s                      # 見えている画像は読んである
    ui.js("(()=>{const m=document.getElementById('main'); m.scrollTop=m.scrollHeight; return true;})()")
    ui.until("(()=>{" + seen + " return ![...document.querySelectorAll('img.songCover')].some(i=>!i.getAttribute('src') && shown(i));})()")
    assert ui.js(state)['waiting'] < s['waiting']      # スクロールした先も読む（上に隠れた分は読まないまま）


def test_covers_shrink_when_narrow(ui):
    """文字を最大にした狭いウィンドウでは小さくして、曲名の文字の幅を残す（@container main）"""
    ui.serve.SETTINGS.update({'ui_scale': 145})
    ui.resize(960)
    ui.open('recommend')
    rs = rows(ui, '.recList .recRow:not(.head)', '.songName')
    ok(rs, 5)
    assert all(r['w'] == 44 for r in rs)
    check_layout(ui, 'recommend_x145_960')
