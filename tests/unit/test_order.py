# core/estimate/order.py: プレイリストの中の並び（基本の並び・手で並べた順を保つ）
from core.estimate.order import default_order, group_of, is_default, merge_order


def c(lid, kind='played', clear=0.9, score=1.0, tried=None, challenge=False):
    return {'leaderboard_id': lid, 'kind': kind, 'clear': clear, 'score': score, 'tried': tried, 'challenge': challenge}


ITEMS = [
    c('ch1', 'unplayed', 0.3, challenge=True),          # 挑戦枠（未プレイ）
    c('n1', 'unplayed', 0.80),
    c('p1', 'played', 0.85),
    c('t1', 'unplayed', 0.5, tried={'n': 3}),            # 未クリア
    c('p2', 'played', 0.95),
    c('ch2', 'played', 0.9, challenge=True),             # 挑戦枠（プレイ済みでも最後の区分）
    c('n2', 'unplayed', None, score=5.0),                # 見込みが無い＝区分の一番上の扱い
    c('n3', 'unplayed', 0.80, score=3.0),                # 見込みが同じなら並び順の値の高い順
]


def ids(xs):
    return [x['leaderboard_id'] for x in xs]


def test_groups():
    assert [group_of(x) for x in ITEMS] == ['challenge', 'new', 'played', 'tried', 'played', 'challenge', 'new', 'new']


def test_default_order():
    # プレイ済み → 新曲 → 未クリア → 挑戦。区分の中はクリアの見込みの高い順
    assert ids(default_order(ITEMS)) == ['p2', 'p1', 'n2', 'n3', 'n1', 't1', 'ch2', 'ch1']
    assert ids(ITEMS)[0] == 'ch1'   # 元のリストは変えない
    assert is_default(default_order(ITEMS)) and not is_default(ITEMS)


def test_merge_keeps_manual_order_and_inserts_new_by_default_position():
    manual = ['n1', 'p2', 'ch1', 'p1']                                       # 手で並べた順
    items = [x for x in ITEMS if x['leaderboard_id'] in ('n1', 'p2', 'ch1', 't1', 'n3')]   # p1 が外れ、t1・n3 が入った
    out = ids(merge_order(manual, items))
    # 残った曲は手の順のまま。n3 は基本の並びで直前の p2 の後ろ、t1 は直前の n1 の後ろ（n3 の前にある n1）
    assert [i for i in out if i in manual] == ['n1', 'p2', 'ch1']
    assert out == ['n1', 't1', 'p2', 'n3', 'ch1']


def test_merge_edge_cases():
    assert ids(merge_order([], ITEMS)) == ids(default_order(ITEMS))           # 手の順が無い＝基本の並び
    assert ids(merge_order(['zz', 'p1', 'p1'], [ITEMS[2]])) == ['p1']        # 無い曲・重複は落とす
    first = c('p0', 'played', 0.99)                                         # 基本の並びで一番前の新しい曲は先頭へ
    assert ids(merge_order(['n1', 'p1'], [ITEMS[1], ITEMS[2], first])) == ['p0', 'n1', 'p1']
