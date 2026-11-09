# ホームの画面: 前回のプレイリストの結果（一番上・2段の見出しの表・このリストの総PP と出した時の見込み）と、上下の人の表・タイルの
# 「前回の出力から」。値は API（views.home）の値と同じものが出ているかを見る（作り物のデータは前のテストの取得で変わることがある）
from conftest import since_export

CARD_TITLES = "[...document.querySelectorAll('#main > section.card, #main > .grid2 > section.card')].map(c=>c.querySelector('h2').textContent)"


def pct(v):
    return '—' if v is None else '%.2f%%' % (100 * v)


def plus(v):
    return '+%.2f' % max(0.0, v)


def signed_delta(v, digits):
    """home.js の delta() の文字（▲ / ▼ と絶対値。0 は ±0）"""
    if v is None:
        return '—'
    if abs(v) < 10 ** -digits / 2:
        return '±0'
    return ('▲ ' if v > 0 else '▼ ') + '{:,.{}f}'.format(abs(v), digits)


def test_review_table_on_top(ui):
    ui.open('home')
    titles = ui.js(CARD_TITLES)
    assert titles[0] == '前回のプレイリストの結果' and titles.index('上下 10 人') > 0   # 結果は一番上（数字のタイルより前）
    assert ui.js("document.querySelector('#main .card').nextElementSibling.classList.contains('tiles')")
    # 2段の見出し: 狙い（推定精度・総PP見込み）・前の自己ベスト・今回の結果（精度・曲のPP・総PP）
    assert ui.js("[...document.querySelectorAll('.revTable .grpHead')].map(e=>e.textContent)") == ['出した時の狙い', '今回の結果']
    assert ui.js("[...document.querySelectorAll('.revTable thead .grpVals')].map(e=>[...e.children].map(x=>x.textContent))") == [
        ['推定精度', '総PP見込み'], ['精度', '曲のPP', '総PP']]
    assert '前の自己ベスト' in ui.js("document.querySelector('.revTable thead').textContent")
    # 値は API と同じ（作り物のデータは出力の後に1曲伸ばしてある）
    run = ui.serve.views.home(ui.serve.STORE, ui.serve.SETTINGS)['review']['runs'][0]
    rows = ui.js("[...document.querySelectorAll('.revTable tbody tr')].map(r=>({id:r.dataset.id, status:r.dataset.status,"
                 "vals:[...r.querySelectorAll('.rvV')].map(v=>v.textContent), before:r.children[5].textContent}))")
    assert [r['id'] for r in rows] == [x['id'] for x in run['items']]
    for r, x in zip(rows, run['items']):
        assert r['status'] == x['status'] and r['before'] == pct(x['before'])
        est, exp, acc, pp, gain = r['vals']
        assert est == pct(x['est']) and exp == plus(x['expGain'])
        if x['status'] in ('improved', 'firstClear'):
            assert (acc, pp, gain) == (pct(x['acc']), '%.1f' % x['pp'], plus(x['contrib']))
        elif x['status'] == 'notPlayed':
            assert (acc, pp, gain) == ('—', '', '')
    assert any(r['status'] == 'improved' for r in rows)
    head = ui.js("document.querySelector('.revSum').textContent")
    assert head.startswith('このリスト総PP %s（出した時の見込み %s' % (plus(run['gain']), plus(run['expected'])))
    assert '遊んだ %d / %d曲' % (run['played'], len(run['items'])) in head
    assert ui.layout_issues() == []


def test_since_last_export(ui):
    # 出力が無い（作り物のデータの出力は最初の取得より前＝比べる元が無い）時は、上下の人の表に2列を出さない
    ui.open('home')
    assert ui.js("document.querySelectorAll('.nbTable thead tr').length") == 1
    assert ui.js("[...document.querySelectorAll('.tileSub')].some(e=>e.textContent.startsWith('前回の出力から'))") is False
    since_export(ui.serve)
    ui.open('home')
    d = ui.serve.views.home(ui.serve.STORE, ui.serve.SETTINGS)
    assert d['sinceExport']['at']
    assert ui.js("[...document.querySelectorAll('.nbTable thead th')].map(e=>e.textContent)") == [
        '国内', '名前', 'PP', '自分との差', '前回の出力から', '順位', 'PP']
    assert ui.js("document.querySelector('.nbTable').closest('.card').querySelector('.cardHead .when').textContent").startswith('前回の出力 ')
    cells = ui.js("[...document.querySelectorAll('.nbTable tbody tr')].map(r=>[...r.children].slice(-2).map(c=>c.textContent))")
    assert cells == [[signed_delta(n['rankMove'], 0), signed_delta(n['ppMove'], 2)] for n in d['neighbors']]
    me = next(n for n in d['neighbors'] if n['self'])
    assert me['ppMove'] > 0                                   # 作り物のデータは2回目の取得の前に自分の曲を1つ伸ばした
    assert ui.js("document.querySelector('.nbTable tr.me').children[5].textContent") == signed_delta(me['ppMove'], 2)
    # タイル: 先週からの下に、前回の出力から
    subs = ui.js("[...document.querySelectorAll('.tile')].slice(0,3).map(t=>[...t.querySelectorAll('.tileSub')].map(e=>e.textContent))")
    assert [s[0].split(' ')[0] for s in subs] == ['先週から'] * 3
    assert subs[1][1] == '前回の出力から ' + signed_delta(d['me']['pp'] - d['me']['exportPp'], 2)
    assert ui.layout_issues() == []
    # 狭い時（表の値を縦に積む）・文字を最大にした時も崩れない
    for w in (960, 700):
        ui.resize(w)
        ui.open('home')
        assert ui.layout_issues() == [], w
    assert ui.js("getComputedStyle(document.querySelector('.revTable tbody .grpVals')).flexDirection") == 'column'
    ui.serve.SETTINGS.update({'ui_scale': 145})
    ui.resize(960)
    ui.open('home')
    assert ui.layout_issues() == []
