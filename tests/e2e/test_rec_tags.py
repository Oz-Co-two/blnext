# おすすめの一覧の印（挑戦・前回なし）は曲名の上の行（.tagLine）に並べる。曲名の行（.line1）には曲名・難易度・モードだけ。
# 2026-11-09 までは曲名の後ろに続けていて、長い曲名だと印が曲名と一緒に「…」で切られて見えなかった
import json

import pytest

from conftest import OUT

LONG = 'とても長い曲の名前のテスト_' + 'ABCDEFGHIJ' * 6 + '_'

# 一覧（次点を除く）の行ごとに: 印の行の中身・曲名の行に印があるか・印の行が曲名の行より上か・印が欄の中に全部見えているか・行の高さ
ROWS = r"""(()=>[...document.querySelectorAll('.recList:has(.listHead) .recRow:not(.head)')].map(r=>{
  const tl=r.querySelector('.tagLine'), l1=r.querySelector('.line1'), box=r.querySelector('.songText').getBoundingClientRect();
  const tags=tl?[...tl.children]:[];
  return {id:r.dataset.id, tags:tags.map(x=>x.className), texts:tags.map(x=>x.innerText),
          inLine1:!!l1.querySelector('.chTag,.prevTag'), lines:[...r.querySelector('.songText').children].map(x=>x.className),
          above:!tl||tl.getBoundingClientRect().bottom<=l1.getBoundingClientRect().top+0.5,
          shown:tags.every(x=>{const b=x.getBoundingClientRect(); return b.left>=box.left-0.5&&b.right<=box.right+0.5&&b.bottom<=box.bottom+0.5&&x.scrollWidth<=x.clientWidth+1;}),
          clipped:l1.scrollWidth>l1.clientWidth+1, h:Math.round(r.getBoundingClientRect().height*100)/100,
          cover:Math.round(r.querySelector('.songCover').getBoundingClientRect().height*100)/100};
}))()"""


def tagged(ui):
    """挑戦の印の曲・前回なしの印の曲・印の無い曲が並ぶ一覧にして、どの曲も長い名前にする。
    今のリストを書き込み（前回の出力）、挑戦枠でない1曲を外す（代わりに入った曲が「前回なし」）。曲名は保存したリストを直接書き換える"""
    s = ui.serve
    s.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50}, preset='p1')   # 未プレイ（挑戦枠）の曲も出る条件
    s._api_recommend_rebuild({'preset': 'p1'})
    s._api_export({'preset': 'p1'})
    items = s._api_recommend({'preset': 'p1'})['items']
    out = next(c['leaderboard_id'] for c in items if not c['challenge'])
    s._api_recommend_action({'action': 'remove', 'id': out, 'preset': 'p1'})
    row = s.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")
    lst = json.loads(row['payload'])
    for c in lst['items']:
        c['song'] = LONG + (c.get('song') or '')
    with s.STORE.tx():
        s.STORE.conn.execute("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(lst, ensure_ascii=False),))
    return {c['leaderboard_id']: c for c in s._api_recommend({'preset': 'p1'})['items']}


@pytest.mark.parametrize('width,scale', ((1280, None), (1600, None), (960, None), (960, 145), (1280, 145)))
def test_tags_above_song_name(ui, width, scale):
    if scale:
        ui.serve.SETTINGS.update({'ui_scale': scale})
    want = tagged(ui)
    ui.resize(width)
    ui.open('recommend')
    rows = ui.js(ROWS)
    assert len(rows) == len(want) >= 5
    exp = lambda c: [x for x, on in (('chTag', c['challenge']), ('prevTag', c.get('in_export') is False)) if on]
    assert {r['id']: r['tags'] for r in rows} == {lid: exp(c) for lid, c in want.items()}
    assert any('chTag' in r['tags'] for r in rows) and any('prevTag' in r['tags'] for r in rows) and any(not r['tags'] for r in rows)
    for r in rows:
        assert not r['inLine1'], r                       # 曲名の行に印は無い
        # 印のある曲だけ印の行があり、曲名の上（印の無い曲は空の行で高さを取らない）
        assert r['lines'] == (['tagLine'] if r['tags'] else []) + ['line1', 'line2 mute'], r
        assert r['above'], r
        assert r['shown'], r                             # 長い曲名でも印は切れない
        assert r['clipped'], r                           # 曲名のほうは「…」で切れている（長い名前になっている）
        assert all(t in ('挑戦', '前回なし') for t in r['texts']), r
    wide = ui.js("getComputedStyle(document.querySelector('.recRow .songCover')).width") == '64px'
    if wide:   # 広い時は、3行の文字もカバー画像の高さに収まり、印のある行も無い行も同じ高さ
        assert len({r['h'] for r in rows}) == 1, sorted({r['h'] for r in rows})
    ui.js("document.querySelector('.recList .recRow:has(.prevTag)').scrollIntoView({block:'center'}); true")   # 撮る画像に印の行が写るように
    ui.shot(OUT / 'screens' / f'recommend_tags_{width}{"_x%d" % scale if scale else ""}.png')
    issues = ui.layout_issues()
    assert not issues, '崩れ:\n' + '\n'.join(f"[{i['kind']}] {i['where']} {i['text']}" for i in issues)
