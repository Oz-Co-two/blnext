# 画面の比較: screens.py の画面ごとに撮って、基準の写真と比べる（vislib.py）。
# 変化した画面は失敗にせず「要確認」にする（tools/run_tests.py の要約に出る。まとめは test-out/visual/review.png・report.html）。
# 意図した変化なら `tools/run_tests.py visual --accept` で基準を置き換える。崩れ（重なり・はみ出し）は e2e の test_screens.py が確かめる
import pytest

import vislib
from screens import SHOTS

MAX_H = 6000   # 縦に長い画面を全体で撮る時の高さの上限


def settle(a):
    """撮る前に: 動き・点滅を止めて時刻などを隠し、フォントと画像を読み終えるまで待つ。マウスは何も無い所へ"""
    a.wait(0.3)   # 画面が後から読む物（セットの画像など）を待つ
    a.js("(()=>{if(document.getElementById('visFreeze')) return true; const s=document.createElement('style'); s.id='visFreeze';"
         " s.textContent=%r; document.head.append(s); return true;})()" % vislib.FREEZE_CSS)
    # 隠した時刻も幅は残るので、数字の並びを 00 にそろえる（月日は桁を詰めて書くので、日付で文字数が変わり、同じ行の後ろの文が
    # ずれた。2026-11-01 に「10/29」→「11/1」で）
    a.js("document.querySelectorAll('#dataAt,.when').forEach(e=>{const w=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);"
         " for(let n;(n=w.nextNode());) n.nodeValue=n.nodeValue.replace(/[0-9]+/g,'00');}); true")
    # 曲のカバー画像は見える所に近づくまで読まない（data-src に置いてある）。撮る全体を同じに写すため、すぐ読ませてから待つ
    a.js("document.querySelectorAll('img.songCover[data-src]').forEach(i=>{if(!i.getAttribute('src')) i.src=i.dataset.src;}); true")
    a.js("Promise.all([document.fonts.ready, ...[...document.images].map(i=>i.complete?1:new Promise(r=>{i.onload=i.onerror=r;}))]).then(()=>true)")
    a.call('Input.dispatchMouseEvent', type='mouseMoved', x=1, y=1)
    a.wait(0.1)


def expand(a):
    """本文（#main）が縦にスクロールしなくなるまで、画面の高さを伸ばす（縦に長い画面の全体を撮るため）"""
    for _ in range(5):
        over = a.js("(()=>{const m=document.getElementById('main'); const z=parseFloat(document.documentElement.style.zoom||'1')||1;"
                    " return Math.ceil((m.scrollHeight-m.clientHeight)*z);})()")
        if over <= 1 or a.height >= MAX_H:
            return
        a.resize(a.width, min(MAX_H, a.height + over + 2))
        a.wait(0.15)


@pytest.mark.parametrize('shot', SHOTS, ids=[s.name for s in SHOTS])
def test_look(vis, shot, monkeypatch, record_property):
    if shot.scale != 100:
        vis.serve.SETTINGS.update({'ui_scale': shot.scale})
    vis.resize(shot.width, 900)
    vis.open(shot.screen)
    if shot.prep:
        shot.prep(vis, monkeypatch)
    settle(vis)
    if shot.full:
        expand(vis)
        settle(vis)
    vis.shot(vislib.current_path(shot.name))
    r = vislib.compare(shot.name, shot.title)
    if r['status'] == 'changed':
        record_property('review', '%s: %s（まとめ: test-out/visual/report.html）' % (shot.title or shot.name, r['note']))
    elif r['status'] in ('new', 'accepted'):
        record_property('info', '基準を%s' % ('作った' if r['status'] == 'new' else '置き換えた'))
