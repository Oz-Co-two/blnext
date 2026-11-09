# 難しさのプリセット: 未プレイ曲の pass の上限（unplayed_pass_cap）・ほかのセットのリストの曲を外す（exclude_other_sets。serve.py が
# 外す曲の hash を渡す）・難しさの段階（core/settings.py の DIFFICULTY）とその判定・組み立てた曲の並び（基本の並び）
import json
from types import SimpleNamespace

import pytest

from core.estimate.assemble import assemble
from core.settings import (DIFFICULTY, DIFFICULTY_SET_DEFAULT, DIFFICULTY_SETS, PRESET_KEYS, SPEC, Settings, SettingsError, check,
                           difficulty_of)
from core.store.db import Store

RANGE = {'pass': {'p75': 3.0, 'p95': 4.0, 'max': 5.0}}


def cand(i, kind, gain, pass_rating=2.0, song=None):
    return {'leaderboard_id': 'L%02d' % i, 'kind': kind, 'hash': (song or 'h%02d' % i).ljust(40, '0')[:40], 'song': 'S%d' % i,
            'mapper': 'M', 'difficulty': 'Expert', 'mode': 'Standard', 'stars': 6.0, 'upload_time': None, 'base_acc': 0.9,
            'est_acc': 0.93, 'est_pp': 200 + gain, 'best_acc': 0.9 if kind == 'played' else None,
            'best_pp': 150 if kind == 'played' else None, 'best_time': None, 'gain': gain, 'rank_up': 1, 'confidence': 1.0,
            'score': gain, 'flags': [], 'similar_n': 3, 'refs': [], 'pass_rating': pass_rating, 'nps': 3.0}


@pytest.fixture
def env(tmp_path):
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    # 挑戦枠（pass が95%点より上）では絞らない。未プレイの枠だけを見る
    s.update({'player_id': '1', 'playlist_size': 4, 'played_ratio': 0, 'challenge_max': 50})
    with st.tx():
        st.upsert('players', {'player_id': '1', 'country': 'jp', 'pp': 1000, 'country_rank': 10, 'seen_at': 1, 'is_self': 1}, ['player_id'])
        st.set_meta('ranking_at', 1)
    model = SimpleNamespace(me=SimpleNamespace(player_id='1', pp=1000.0, country_rank=10), my_scores={}, range=dict(RANGE), few_similar=False)
    yield st, s, model
    st.close()


def ids(res):
    return sorted(c['leaderboard_id'] for c in res['items'])


def pool():
    """未プレイ: pass 2.5・3.5・4.5・pass 不明 / プレイ済み: pass 4.8（プレイ済みには上限をかけない）"""
    est = [cand(0, 'unplayed', 50, 2.5), cand(1, 'unplayed', 49, 3.5), cand(2, 'unplayed', 48, 4.5), cand(3, 'unplayed', 47, None),
           cand(4, 'played', 46, 4.8)]
    return est


@pytest.mark.parametrize('cap,expect,cap_val,n_out', [
    ('none', ['L00', 'L01', 'L02', 'L03'], None, 0),
    ('p95', ['L00', 'L01', 'L03', 'L04'], 4.0, 1),            # 4.5 を外し、空いた所はプレイ済みで埋める
    ('p75', ['L00', 'L03', 'L04'], 3.0, 2),                   # 3.5・4.5 を外す（pass の分からない曲は外さない）
])
def test_unplayed_pass_cap(env, cap, expect, cap_val, n_out):
    st, s, model = env
    s.update({'unplayed_pass_cap': cap})
    res = assemble(pool(), model, s, st)
    assert ids(res) == expect
    assert res['filters']['passCap'] == cap_val and res['filters']['passOut'] == n_out
    assert not set(expect) & {c['leaderboard_id'] for c in res['runnersUp']}
    assert all(c['pass_rating'] is None or c['kind'] == 'played' or cap_val is None or c['pass_rating'] <= cap_val
               for c in res['runnersUp'])   # 外した曲は次点にも出ない


def test_min_clear(env):
    # クリアの見込みの下限（着実は 80%）: 見込みがそれより低い曲は、プレイ済み・未プレイとも入れない。見込みの無い曲は外さない。
    # 固定した曲は入れる（外した数には数えない）
    st, s, model = env
    est = pool()
    for c, p in zip(est, (0.95, 0.5, 0.79, None, 0.6)):
        c['clear'] = p
    s.update({'min_clear': 80, 'played_ratio': 50})
    res = assemble(est, model, s, st)
    assert ids(res) == ['L00', 'L03'] and res['filters']['minClear'] == 80 and res['filters']['clearOut'] == 3
    assert not {'L01', 'L02', 'L04'} & {c['leaderboard_id'] for c in res['runnersUp']}
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L01', 'pin', 'p1', 1)")
    res = assemble(est, model, s, st)
    assert 'L01' in ids(res) and res['filters']['clearOut'] == 2
    s.update({'min_clear': 0})
    assert assemble(est, model, s, st)['filters']['clearOut'] == 0


def test_pass_cap_pinned_and_no_range(env):
    st, s, model = env
    s.update({'unplayed_pass_cap': 'p75'})
    with st.tx():   # 固定した曲は上限を超えても入れる（フィルタより優先）。外した数には数えない
        st.conn.execute("INSERT INTO user_marks VALUES('L02', 'pin', 'p1', 1)")
    res = assemble(pool(), model, s, st)
    assert 'L02' in ids(res) and 'L01' not in ids(res) and res['filters']['passOut'] == 1
    # クリアしてきた曲が少なく pass の分布が無ければ絞らない
    model.range = {}
    res = assemble(pool(), model, s, st)
    assert res['filters']['passCap'] is None and res['filters']['passOut'] == 0 and 'L01' in ids(res)


def test_other_songs_by_hash(env):
    st, s, model = env
    s.update({'playlist_size': 3})
    est = [cand(0, 'unplayed', 50, song='same'), cand(1, 'unplayed', 49, song='same'),   # 同じ曲の別難易度
           cand(2, 'unplayed', 48), cand(3, 'unplayed', 47), cand(4, 'unplayed', 46)]
    other = {est[0]['hash'], est[2]['hash']}
    assert ids(assemble(est, model, s, st)) == ['L00', 'L02', 'L03']
    res = assemble(est, model, s, st, other_songs=other)
    assert ids(res) == ['L03', 'L04'] and res['short'] == 1         # 別難易度（L01）も外れる。足りない分は足りないまま
    assert res['filters']['otherOut'] == 3 and not {'L00', 'L01', 'L02'} & {c['leaderboard_id'] for c in res['runnersUp']}
    # 固定した曲・入れ替えで選んだ曲は、ほかのセットのリストにあっても入れる
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L00', 'pin', 'p1', 1)")
    res = assemble(est, model, s, st, session={'removed': set(), 'added': {'L02'}}, other_songs=other)
    assert ids(res) == ['L00', 'L02', 'L03'] and res['filters']['otherOut'] == 1
    assert assemble(est, model, s, st, other_songs=set())['filters']['otherOut'] == 0


# ---- プレイリスト内の並び（core/estimate/order.py の基本の並び） ----

def test_items_in_default_order(env):
    # 組み立てた曲は、プレイ済み → 新曲 → 未クリア → 挑戦（挑戦枠の曲はプレイ済みでも最後）の順。区分の中はクリアの見込みの高い順。
    # ★の低い順・並び順の値の順ではない（設定 order は 2026-10-18 になくした）
    st, s, model = env
    s.update({'playlist_size': 6, 'played_ratio': 50})
    spec = [  # (番号, 区分, 並び順の値, pass, クリアの見込み, ★, 挑戦の記録)
        (0, 'unplayed', 50, 2.0, 0.70, 5.0, None),       # 新曲
        (1, 'played', 49, 2.0, 0.80, 8.0, None),          # プレイ済み
        (2, 'unplayed', 48, 4.5, 0.30, 8.2, None),        # 挑戦（pass が95%点より上。★は未プレイの上限 8.5 まで）
        (3, 'played', 47, 2.0, 0.95, 7.0, None),          # プレイ済み
        (4, 'unplayed', 46, 2.0, 0.60, 4.0, {'n': 2}),    # 未クリア（挑戦したがクリアしていない）
        (5, 'played', 45, 4.6, 0.90, 3.0, None),          # 挑戦（プレイ済みでも最後の区分）
    ]
    est = []
    for i, kind, gain, ps, clear, stars, tried in spec:
        c = cand(i, kind, gain, ps)
        c.update({'clear': clear, 'stars': stars, 'tried': tried})
        est.append(c)
    res = assemble(est, model, s, st)
    order = [c['leaderboard_id'] for c in res['items']]
    assert order == ['L03', 'L01', 'L00', 'L04', 'L05', 'L02']
    assert [c['challenge'] for c in res['items']] == [False, False, False, False, True, True]
    assert [c['stars'] for c in res['items']] != sorted(c['stars'] for c in res['items'])
    assert 'order' not in s.values()
    # 固定した曲も同じ並びの中に入る（先頭にはしない）
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L02', 'pin', 'p1', 1)")
    assert [c['leaderboard_id'] for c in assemble(est, model, s, st)['items']] == order


# ---- 難しさの段階（core/settings.py） ----

def test_difficulty_levels_are_valid_settings():
    assert list(DIFFICULTY) == ['steady', 'standard', 'challenge'] and list(DIFFICULTY_SETS) == list(DIFFICULTY)
    for vals in DIFFICULTY.values():
        assert all(k in PRESET_KEYS and check(k, v) == v for k, v in vals.items())
    assert DIFFICULTY['steady'] == {'unplayed_pass_cap': 'p75', 'challenge_max': 0, 'min_clear': 80, 'acc_cap_margin': 0.0}
    assert DIFFICULTY['standard'] == {'unplayed_pass_cap': 'none', 'challenge_max': 1, 'min_clear': 0, 'acc_cap_margin': 0.5}
    assert DIFFICULTY['challenge'] == {'unplayed_pass_cap': 'none', 'challenge_max': 2, 'min_clear': 0, 'acc_cap_margin': 1.0}
    assert DIFFICULTY['standard']['acc_cap_margin'] == SPEC['acc_cap_margin']['default']   # 初期値のセットは標準
    assert set(DIFFICULTY_SET_DEFAULT) <= set(DIFFICULTY)


def test_difficulty_of(tmp_path):
    s = Settings(str(tmp_path / 'config'))
    assert difficulty_of(s.values()) == 'standard'          # 初期値は標準
    for level, vals in DIFFICULTY.items():
        assert difficulty_of(s.update(vals)) == level
    assert difficulty_of(s.update({'challenge_max': 3})) is None              # 個別に変えた＝カスタム
    assert difficulty_of(s.update({'unplayed_pass_cap': 'p95', 'challenge_max': 1})) is None
    assert difficulty_of({}) is None


def test_cap_margin_of_older_presets(tmp_path):
    # 精度の上限の余裕（acc_cap_margin。2026-11-01 に足した）を持たない前からのセットは、ほかの難しさの項目が合う段階の余裕で始める
    # （初期値の 0.5 のままだと、着実・チャレンジングのセットがカスタムになる）。カスタムのセットは初期値。次に書く時にファイルに残る
    (tmp_path / 'settings.json').write_text(json.dumps({'player_id': '7', 'presets': [
        {'id': 'p1', 'name': 'A', 'values': {'unplayed_pass_cap': 'p75', 'challenge_max': 0, 'min_clear': 80}},
        {'id': 'p2', 'name': 'B', 'values': {'challenge_max': 2}},
        {'id': 'p3', 'name': 'C', 'values': {}},
        {'id': 'p4', 'name': 'D', 'values': {'challenge_max': 3}},
        {'id': 'p5', 'name': 'E', 'values': {'challenge_max': 2, 'acc_cap_margin': 0.2}}]}), encoding='utf-8')
    s = Settings(str(tmp_path))
    got = {p: s.values(p)['acc_cap_margin'] for p in ('p1', 'p2', 'p3', 'p4', 'p5')}
    assert got == {'p1': 0.0, 'p2': 1.0, 'p3': 0.5, 'p4': 0.5, 'p5': 0.2}
    assert [difficulty_of(s.values(p)) for p in ('p1', 'p2', 'p3', 'p4', 'p5')] == ['steady', 'challenge', 'standard', None, None]
    s.update({'playlist_size': 9}, preset='p3')
    raw = json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))
    assert [p['values'].get('acc_cap_margin') for p in raw['presets']] == [0.0, 1.0, None, None, 0.2]
    s.update({'challenge_max': 3}, preset='p2')      # 書いた後は、ほかの項目を変えても余裕はそのまま
    assert s.values('p2')['acc_cap_margin'] == 1.0


def test_add_difficulty_presets(tmp_path):
    s = Settings(str(tmp_path / 'config'))
    s.update({'playlist_size': 12, 'modes': ['OneSaber']})
    pids = s.add_difficulty_presets('p1', ['challenge', 'steady'], 6)   # 並びは 着実 → チャレンジング にそろえる
    assert [p['name'] for p in s.presets(brief=True)] == ['スタンダード', '着実', 'チャレンジング']
    a, b = (s.values(p) for p in pids)
    assert a['output_filename'] == 'blnext-steady.bplist' and a['playlist_title'] == '次回 着実'
    assert b['output_filename'] == 'blnext-challenge.bplist' and b['playlist_title'] == '次回 チャレンジング'
    assert difficulty_of(a) == 'steady' and difficulty_of(b) == 'challenge'
    assert a['playlist_size'] == b['playlist_size'] == 6 and a['exclude_other_sets'] and b['exclude_other_sets']
    assert a['modes'] == ['OneSaber']                       # ほかは写した元のまま
    assert not s.values('p1')['exclude_other_sets'] and s.values('p1')['playlist_size'] == 12   # 元のセットは変えない
    # 同じ名前のセットがあれば何も作らない。名前を変えたセットがファイル名を使っていれば番号をずらす
    with pytest.raises(SettingsError) as e:
        s.add_difficulty_presets('p1', ['steady', 'standard'], 7)
    assert e.value.code == 'preset-exists' and e.value.key == '着実' and len(s.preset_ids()) == 3
    s.rename_preset(pids[0], '昔の着実')
    pid = s.add_difficulty_presets('p1', ['steady'], 7)[0]
    assert s.values(pid)['output_filename'] == 'blnext-steady-2.bplist'
    # 段階・曲数・元のセットの間違い
    for levels in ([], ['steady', 'steady'], ['hard'], 'steady', [1], None):
        with pytest.raises(SettingsError) as e:
            s.add_difficulty_presets('p1', levels, 7)
        assert e.value.code == 'bad-levels'
    for size in (0, 51, 2.5, '7', True):
        with pytest.raises(SettingsError) as e:
            s.add_difficulty_presets('p1', ['standard'], size)
        assert e.value.code == 'bad-value'
    with pytest.raises(SettingsError) as e:
        s.add_difficulty_presets('p9', ['standard'], 7)
    assert e.value.code == 'no-preset' and len(s.preset_ids()) == 4


def test_pins_never_exceed_playlist_size(env):
    # 固定した曲が片方の区分の枠を超えていても、もう片方を空き枠ぶん足して曲数を超えることはない（固定だけで超える時は固定を全部入れる）
    st, s, model = env
    s.update({'playlist_size': 4, 'played_ratio': 0})
    est = [cand(i, 'played', 50 - i) for i in range(3)] + [cand(10 + i, 'unplayed', 40 - i) for i in range(5)]
    with st.tx():
        for i in range(3):
            st.conn.execute("INSERT INTO user_marks VALUES(?, 'pin', 'p1', 1)", ('L%02d' % i,))
    res = assemble(est, model, s, st)
    assert len(res['items']) == 4 and {'L00', 'L01', 'L02'} <= set(ids(res))
    s.update({'playlist_size': 2})
    assert len(assemble(est, model, s, st)['items']) == 3   # 固定だけで曲数を超える時は固定を全部
