# 推定（core/estimate）と組み立て・出力: 小さな作り物のデータで結果を確かめる（仕様書の「ログとテスト」）
import json, math, os, time
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from core.estimate import curve as C, clear as CL, recommend as R
from core.estimate.recommend import Gain
from core.estimate.assemble import assemble
from core.estimate.engine import Engine, EST_KEYS, MODEL_KEYS
from core.output import bplist
from core.settings import Settings
from core.store.db import Store


# ---- 曲線 ----

def test_curve_is_decreasing_and_robust():
    rng = np.random.default_rng(1)
    x = rng.uniform(2, 10, 400)
    y = 0.98 - 0.012 * x + rng.normal(0, 0.008, 400)
    y[:15] = 0.5   # 外れ値（大きく失敗したプレイ）
    cv = C.fit(x, y)
    g = cv(np.linspace(2, 10, 50))
    assert np.all(np.diff(g) <= 1e-12)                      # 右下がり
    assert abs(float(cv(6)) - (0.98 - 0.072)) < 0.01       # 外れ値に引っ張られない
    assert 0 <= float(cv(30)) <= float(cv(10))              # 範囲の外も 0〜1・右下がり


def test_curve_needs_points():
    assert C.fit([5, 6], [0.9, 0.89]) is None


def test_time_weights_half_life():
    now = 1_000_000_000
    w = C.time_weights([now, now - 180 * 86400, now - 360 * 86400], now, 180)
    assert np.allclose(w, [1, 0.5, 0.25])


# ---- 総PPの増加量 ----

def _brute(pps):
    s = sorted([p for p in pps if p > 0], reverse=True)
    return sum(p * 0.965 ** i for i, p in enumerate(s))


@pytest.mark.parametrize('new,old', [(500, 0), (250, 200), (90, 80), (5, 0), (150, 150), (100, 300), (301, 300)])
def test_gain_matches_recompute(new, old):
    pps = [400, 300, 200, 150, 120, 100, 80, 50]
    g = Gain(pps)
    after = list(pps)
    if old:
        after.remove(old)
    after.append(max(new, old))
    assert abs(g.after(new, old) - _brute(after)) < 1e-6


def test_gain_old_not_in_list():
    g = Gain([400, 300])
    assert abs(g.after(350, 123) - _brute([400, 300, 350])) < 1e-6


# ---- 組み立て ----

def cand(i, kind, gain, stars=6.0, song=None, mode='Standard', upload=None, conf=1.0):
    return {'leaderboard_id': 'L%02d' % i, 'kind': kind, 'hash': (song or 'h%02d' % i).ljust(40, '0')[:40], 'song': 'S%d' % i,
            'mapper': 'M', 'difficulty': 'Expert', 'mode': mode, 'stars': stars, 'upload_time': upload, 'base_acc': 0.9,
            'est_acc': 0.93, 'est_pp': 200 + gain, 'best_acc': 0.9 if kind == 'played' else None,
            'best_pp': 150 if kind == 'played' else None, 'best_time': None, 'gain': gain, 'rank_up': 1, 'confidence': conf,
            'score': gain * conf, 'flags': [], 'similar_n': 3, 'refs': []}


@pytest.fixture
def env(tmp_path):
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': '1', 'playlist_size': 5, 'played_ratio': 60})
    with st.tx():
        st.upsert('players', {'player_id': '1', 'country': 'jp', 'pp': 1000, 'country_rank': 10, 'seen_at': 1, 'is_self': 1}, ['player_id'])
        for i, pp in enumerate([1100, 1050, 1010, 990]):
            st.upsert('players', {'player_id': str(100 + i), 'country': 'jp', 'pp': pp, 'country_rank': 7 + i, 'seen_at': 1}, ['player_id'])
        st.set_meta('ranking_at', 1)
    me = SimpleNamespace(player_id='1', pp=1000.0, country_rank=10, curve=None)
    model = SimpleNamespace(me=me, my_scores={}, range={}, few_similar=False, eff_stars=lambda stars, mode: stars)
    return st, s, model


def ids(res):
    return [c['leaderboard_id'] for c in res['items']]


def test_slots_and_order(env):
    st, s, model = env
    est = [cand(i, 'played', 50 - i, stars=9 - i * 0.1) for i in range(6)] + [cand(10 + i, 'unplayed', 40 - i, stars=5 + i) for i in range(4)]
    est.sort(key=lambda c: -c['score'])
    res = assemble(est, model, s, st)
    assert res['slots'] == {'played': 3, 'unplayed': 2}      # 5 × 60% = 3
    # 基本の並び（core/estimate/order.py）: プレイ済み → 新曲。クリアの見込みが無いので区分の中は並び順の値の高い順（★の低い順ではない）
    assert ids(res) == ['L00', 'L01', 'L02', 'L10', 'L11']
    assert len(res['runnersUp']) > 0 and res['runnersUp'][0]['leaderboard_id'] == 'L03'


def test_fill_shortage_and_short(env):
    st, s, model = env
    est = [cand(i, 'played', 50 - i) for i in range(4)] + [cand(10, 'unplayed', 30)]
    res = assemble(sorted(est, key=lambda c: -c['score']), model, s, st)
    assert len(res['items']) == 5 and res['short'] == 0     # 未プレイが1曲しかないのでプレイ済みで埋める
    res = assemble([cand(1, 'played', 5)], model, s, st)
    assert res['short'] == 4


def test_one_diff_per_song(env):
    st, s, model = env
    est = [cand(1, 'played', 50, song='same'), cand(2, 'played', 49, song='same'), cand(3, 'played', 10), cand(4, 'played', 9),
           cand(5, 'unplayed', 5), cand(6, 'unplayed', 4)]
    assert 'L02' not in ids(assemble(est, model, s, st))
    s.update({'one_diff_per_song': False})
    assert 'L02' in ids(assemble(est, model, s, st))


def test_filters(env):
    st, s, model = env
    now = int(time.time())
    old = now - 5 * 365 * 86400
    est = [cand(1, 'played', 50, stars=8.0), cand(2, 'unplayed', 49, stars=8.4), cand(3, 'unplayed', 48, stars=8.6),
           cand(4, 'unplayed', 47, upload=old), cand(5, 'played', 46, upload=old), cand(6, 'unplayed', 45, mode='OneSaber'),
           cand(7, 'played', 0)]
    res = assemble(est, model, s, st, now=now)
    got = ids(res)
    assert 'L03' in got              # ★の範囲は初期値（0）では効かない
    assert 'L02' in got
    assert 'L04' not in got          # 古い譜面（未プレイにはかける）
    assert 'L05' in got              # プレイ済みにはかけない（初期値）
    assert 'L06' not in got          # モード
    assert 'L07' not in got          # 増えない曲
    assert res['filters']['starMin'] == 0 and res['filters']['starMax'] == 0 and res['filters']['starOut'] == 0
    assert res['filters']['starMinAuto'] == {'Standard': None}   # 下限は自動だが、gain_up の無い候補では決められず下限なし


def test_star_range(env):
    # ★の範囲（star_min・star_max）はプレイ済みにも未プレイにもかける（端の値は入る）。上限は 0 なら効かない。下限の 0 は自動で、
    # この候補には gain_up が無いので決められず下限なし（test_auto_star_min）。
    # 固定した曲は範囲の外でも入り、外した曲の数（starOut）には数えない。次点も範囲の中の曲だけ
    st, s, model = env
    est = [cand(1, 'played', 50, stars=9.5), cand(2, 'played', 49, stars=7.0), cand(3, 'played', 48, stars=5.0),
           cand(4, 'played', 47, stars=7.5), cand(5, 'unplayed', 46, stars=9.0), cand(6, 'unplayed', 45, stars=4.0),
           cand(7, 'unplayed', 44, stars=7.2), cand(8, 'unplayed', 43, stars=8.0), cand(9, 'played', 42, stars=6.0)]
    res = assemble(est, model, s, st)
    assert set(ids(res)) == {'L01', 'L02', 'L03', 'L05', 'L06'} and res['filters']['starOut'] == 0   # 0 なら効かない
    s.update({'star_min': 6, 'star_max': 8})
    res = assemble(est, model, s, st)
    assert set(ids(res)) == {'L02', 'L04', 'L09', 'L07', 'L08'}        # ★6.0 と ★8.0（端）は入る
    assert res['filters']['starMin'] == 6 and res['filters']['starMax'] == 8 and res['filters']['starOut'] == 4
    assert all(6 <= c['stars'] <= 8 for c in res['items'] + res['runnersUp'])
    s.update({'star_max': 0})                                          # 下限だけ
    res = assemble(est, model, s, st)
    assert 'L03' not in ids(res) and 'L06' not in ids(res) and res['filters']['starOut'] == 2
    assert 'L01' in ids(res) and 'L05' in ids(res)
    s.update({'star_min': 0, 'star_max': 7.2})                         # 上限だけ
    res = assemble(est, model, s, st)
    assert {'L01', 'L04', 'L05', 'L08'}.isdisjoint(ids(res)) and res['filters']['starOut'] == 4
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L01','pin','p1',1)")   # 範囲の外の曲を固定
    res = assemble(est, model, s, st)
    assert 'L01' in ids(res) and next(c for c in res['items'] if c['leaderboard_id'] == 'L01')['pick'] == 'pin'
    assert res['filters']['starOut'] == 3                              # 固定して入れた曲は数えない


def test_auto_star_min():
    # ★の下限の自動（assemble.auto_star_min）: そのモードのプレイ済みの曲で、更新した時の増え（gain_up）が 0.5PP 以上の曲が半分以上になる
    # 一番低い★（幅 0.5 の窓を 0.1 ずつ上げる・窓の曲が5曲より少なければ飛ばす）。上限より 0.5 は下。未プレイの曲は数えない
    from core.estimate.assemble import auto_star_min

    def c(stars, gain_up, kind='played', mode='Standard'):
        return {'kind': kind, 'mode': mode, 'stars': stars, 'gain_up': gain_up}
    # ★4.0〜5.9 に 0.1 ずつ 20曲。★5.0 より上だけ増える
    est = [c(round(4 + i / 10, 1), 1.0 if 4 + i / 10 >= 5.0 - 1e-9 else 0.1) for i in range(20)]
    assert auto_star_min(est, ['Standard']) == {'Standard': 4.8}         # 窓 ★4.8〜5.3 の5曲のうち3曲（★5.0・5.1・5.2）が 0.5PP 以上
    assert auto_star_min(est + [c(4.5, 9, kind='unplayed')] * 10, ['Standard']) == {'Standard': 4.8}   # 未プレイは数えない
    assert auto_star_min(est, ['Standard'], star_max=5.0) == {'Standard': 4.5}   # 上限より 0.5 は下
    assert auto_star_min(est, ['Standard'], star_max=0.3) == {'Standard': None}  # 上限が低すぎれば下限なし
    assert auto_star_min(est, ['Standard', 'OneSaber']) == {'Standard': 4.8, 'OneSaber': None}   # 曲の無いモードは下限なし
    assert auto_star_min([x for x in est if x['stars'] < 5], ['Standard']) == {'Standard': None}  # どの★でも増えなければ下限なし
    assert auto_star_min(est[:4], ['Standard']) == {'Standard': None}    # 曲が少なければ決めない
    # 曲が少ない人（全部の曲で増える）は一番下から
    assert auto_star_min([c(6 + i / 10, 3.0) for i in range(6)], ['Standard']) == {'Standard': 6.0}


def test_auto_star_min_in_assemble(env):
    # 下限が 0 のセットは自動の下限より低い曲を入れない（プレイ済みにも未プレイにも。モードごと）。数字を入れたセットはその値で、自動は出さない
    st, s, model = env
    s.update({'modes': ['Standard', 'OneSaber']})
    est = []
    for i in range(10):   # 両手のプレイ済み ★5.0〜5.9。★5.5 より上だけ増える
        x = cand(i + 1, 'played', 40 - i, stars=round(5 + i / 10, 1))
        x['gain_up'] = 1.0 if i >= 5 else 0.1
        est.append(x)
    est += [cand(11, 'unplayed', 50, stars=5.0), cand(12, 'unplayed', 49, stars=5.6), cand(13, 'unplayed', 48, stars=4.0, mode='OneSaber')]
    res = assemble(est, model, s, st)
    assert res['filters']['starMinAuto'] == {'Standard': 5.3, 'OneSaber': None}
    assert all(c['stars'] >= 5.3 for c in res['items'] if c['mode'] == 'Standard') and 'L11' not in ids(res)
    assert 'L13' in ids(res)                                           # ワンセイバーは決められず下限なし
    assert res['filters']['starOut'] == 4                              # ★5.0・5.1・5.2 のプレイ済みと ★5.0 の未プレイ
    s.update({'star_min': 5.1})
    res = assemble(est, model, s, st)
    assert res['filters']['starMinAuto'] is None and res['filters']['starMin'] == 5.1 and 'L13' not in ids(res)


def test_auto_star_ranges():
    import types
    from core.estimate.assemble import auto_star_ranges
    now = 2000000000
    day = 86400

    def rows(stars, age=1, mods='', mode='Standard', start=0):
        return [({'leaderboard_id': 'L%d' % (start + i), 'kind': 'played', 'mode': mode, 'stars': x},
                 {'timeset': now - age * day, 'modifiers': mods}) for i, x in enumerate(stars)]

    def ranges(*groups):
        pairs = [p for g in groups for p in g]
        model = types.SimpleNamespace(my_scores={c['leaderboard_id']: r for c, r in pairs})
        return auto_star_ranges([c for c, _ in pairs], model, ['Standard'], now)
    stars = [round(1 + i * 0.1, 1) for i in range(101)]   # ★1.0〜11.0
    assert ranges(rows(stars)) == {'steady': (0, 8.5), 'standard': (6.0, 10.0), 'challenge': (8.5, 11.0)}
    assert ranges(rows(stars[:4])) is None                                     # 少なければ入れない（曲線が作れる最低の数より少ない）
    assert ranges(rows(stars[:5])) is not None
    assert ranges(rows(stars, age=400)) == ranges(rows(stars))                 # 最近の曲が足りなければ全部で決める
    # 最近の曲が 30曲より少なければ、全部の自己ベストで決める（6曲の易しい曲で決めない）
    assert ranges(rows(stars[:6]), rows(stars[6:], age=400, start=200)) == ranges(rows(stars))
    # 最近の曲が足りていれば最近の曲だけ（古い易しい曲は数えない）
    assert ranges(rows(stars[50:90]), rows(stars[:50], age=400, start=200))['steady'][1] == ranges(rows(stars[50:90]))['steady'][1]
    # モディファイアの付いた自己ベストと、別のモードの曲は数えない
    assert ranges(rows(stars), rows([15.0] * 50, mods='NF', start=300)) == ranges(rows(stars))
    assert ranges(rows(stars), rows([15.0] * 50, mods='NA', start=600)) == ranges(rows(stars))
    assert ranges(rows(stars, mods='NA')) is None                              # 全部 NA の人には入れない
    assert ranges(rows(stars), rows([15.0] * 50, mods='SF,SS', start=400)) == ranges(rows(stars))
    assert ranges(rows(stars), rows([15.0] * 50, mode='OneSaber', start=500)) == ranges(rows(stars))


def test_recent_stars(env):
    # ★の範囲を選ぶ参考: このセットのモードのプレイ済みの曲のうち、自己ベストが90日以内で NF・SS の付いていない曲の★
    st, s, model = env
    now, day = 1_800_000_000, 86400
    est = [cand(1, 'played', 50, stars=6.0), cand(2, 'played', 49, stars=6.5), cand(3, 'played', 48, stars=7.0),
           cand(4, 'played', 47, stars=7.5), cand(5, 'played', 46, stars=8.0),
           cand(6, 'played', 45, stars=9.9), cand(7, 'played', 44, stars=9.8), cand(8, 'played', 43, stars=9.7),
           cand(9, 'played', 42, stars=9.6, mode='OneSaber'), cand(10, 'unplayed', 41, stars=9.5)]
    mods = {'L03': 'FS,GN', 'L06': 'NF', 'L07': 'FS,SS'}
    model.my_scores = {c['leaderboard_id']: {'leaderboard_id': c['leaderboard_id'], 'pp': 100, 'modifiers': mods.get(c['leaderboard_id'], ''),
                                             'timeset': now - (100 if c['leaderboard_id'] == 'L08' else 10) * day}
                       for c in est if c['kind'] == 'played'}
    rs = assemble(est, model, s, st, now=now)['filters']['recentStars']
    # NF（L06）・SS（L07）・90日より前（L08）・ほかのモード（L09）・未プレイ（L10）は入らない。GN などほかのモディファイアは入る
    assert rs['n'] == 5 and rs['max'] == 8.0 and abs(rs['p95'] - float(np.percentile([6, 6.5, 7, 7.5, 8], 95))) < 1e-9
    del model.my_scores['L05']                                          # 曲が少ない時は95%点を出さない
    rs = assemble(est, model, s, st, now=now)['filters']['recentStars']
    assert rs == {'max': 7.5, 'p95': None, 'n': 4}
    s.update({'modes': ['OneSaber']})
    assert assemble(est, model, s, st, now=now)['filters']['recentStars'] == {'max': 9.6, 'p95': None, 'n': 1}
    s.update({'modes': ['Lawless']})
    assert assemble(est, model, s, st, now=now)['filters']['recentStars'] is None


def test_pin_never_and_session(env):
    st, s, model = env
    est = [cand(i, 'played', 50 - i) for i in range(8)] + [cand(10 + i, 'unplayed', 30 - i) for i in range(4)]
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L07','pin','p1',1)")    # 下位の曲を固定
        st.conn.execute("INSERT INTO user_marks VALUES('L00','never','',1)")
    res = assemble(est, model, s, st)
    got = ids(res)
    assert 'L07' in got and 'L00' not in got
    assert sum(1 for c in res['items'] if c['kind'] == 'played') == 3   # 固定もプレイ済みの枠を使う
    res = assemble(est, model, s, st, session={'removed': {'L01'}, 'added': {'L13'}})
    got = ids(res)
    assert 'L01' not in got and 'L13' in got


def test_cooldown(env):
    # クールダウン（2026-11-01 から。遊んだ日で数える・伸びたかは問わない）。初期値は 再プレイ: 遊んだ日から3日あける・
    # 3週間に3日遊んだら4週休み、初プレイ: 3週間にクリアできない日が3日で4週休み
    from core.estimate.assemble import cooldown_ids
    st, s, model = env
    now = int(datetime(2026, 11, 10, 12).timestamp())
    ago = lambda n: now - n * 86400
    tries = {
        'L00': [(1, 1)],                          # 再プレイ: 昨日遊んだ → 間をあける
        'L01': [(3, 1)],                          # 3日前 → 出してよい
        'L02': [(20, 1), (10, 2), (5, 1)],        # 3週間に3日（失敗の日も数える） → 最後の日から4週休み
        'L03': [(60, 1), (50, 1), (40, 1)],       # 3日続けたが、最後から4週たった → 出してよい
        'L04': [(30, 1), (8, 1), (4, 1)],         # 最後の日までの3週間には2日だけ → 出してよい
        'L05': [(12, 2), (10, 3), (6, 1)],        # クリアできなかった日は初めてクリアした日（6日前）より前 → 再プレイは1日目
        'L06': [(9, 1, 'NF'), (6, 2), (2, 4)],    # 自己ベストは NF だけ＝初プレイ。NF のクリアはクリアに数えない → 3日で休み
        'L10': [(9, 2), (6, 4), (2, 3)],          # 初プレイ: クリアできない日が3日 → 4週休み
        'L11': [(2, 2), (1, 2)],                  # 初プレイは間をあけない・2日だけ → 出してよい
        'L12': [(1, 5)],                          # 練習は数えない
    }
    with st.tx():
        for lid, xs in tries.items():
            for x in xs:
                st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)',
                                (lid, ago(x[0]), 0.9, x[1], x[2] if len(x) > 2 else ''))
    model.my_scores = {lid: {'leaderboard_id': lid, 'timeset': ago(400), 'modifiers': ''} for lid in ('L00', 'L01', 'L02', 'L03', 'L04')}
    model.my_scores['L05'] = {'leaderboard_id': 'L05', 'timeset': ago(6), 'modifiers': ''}
    model.my_scores['L06'] = {'leaderboard_id': 'L06', 'timeset': ago(9), 'modifiers': 'NF'}
    assert cooldown_ids(st, model, s.values(), now) == {'L00': 'gap', 'L02': 'rest', 'L06': 'failRest', 'L10': 'failRest'}
    # 組み立て: 外した曲と、その数の内訳（固定した曲はクールダウンでも入れる）
    est = [cand(i, 'played', 50 - i) for i in range(7)] + [cand(10 + i, 'unplayed', 30 - i) for i in range(3)]
    s.update({'playlist_size': 10, 'played_ratio': 70})
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L02','pin','p1',1)")
    res = assemble(est, model, s, st, now=now)
    assert set(ids(res)) == {'L01', 'L02', 'L03', 'L04', 'L05', 'L11', 'L12'}
    assert res['filters']['cooldown'] == 3 and res['filters']['cooldownBy'] == {'gap': 1, 'rest': 0, 'failRest': 2}
    # 日数を変える: 間を 1日にすると昨日の曲も出る。続けて遊ぶ日数を 4 にすると L02 は休まない。0 で効かない
    s.update({'replay_gap_days': 1, 'replay_streak_days': 4})
    assert cooldown_ids(st, model, s.values(), now) == {'L06': 'failRest', 'L10': 'failRest'}
    s.update({'unplayed_fail_days': 2})
    assert cooldown_ids(st, model, s.values(), now) == {'L06': 'failRest', 'L10': 'failRest', 'L11': 'failRest'}
    s.update({'replay_gap_days': 0, 'replay_streak_days': 0, 'unplayed_fail_days': 0})
    assert cooldown_ids(st, model, s.values(), now) == {}
    # 休みの週を短くすると早く戻る（L02 の最後は5日前・L10 は2日前）
    s.update({'replay_streak_days': 3, 'replay_rest_weeks': 0, 'unplayed_fail_days': 3, 'unplayed_rest_weeks': 1})
    assert cooldown_ids(st, model, s.values(), now) == {'L06': 'failRest', 'L10': 'failRest'}


def test_challenge_cap(env):
    # 範囲の外（pass か1秒のノーツが95%点より上）の曲は、1リストに challenge_max 曲まで。固定した曲は数えるが必ず入れる
    st, s, model = env
    model.range = {'pass': {'p95': 3.0, 'max': 4.0}, 'nps': {'p95': 5.0, 'max': 6.0}}
    est = [cand(i, 'unplayed', 50 - i) for i in range(6)]
    for i, c in enumerate(est):
        c['pass_rating'], c['nps'] = (3.5, 4.0) if i in (0, 1) else (2.0, 6.5 if i == 2 else 4.0)   # 0・1・2 が範囲の外
    s.update({'playlist_size': 4, 'played_ratio': 0, 'challenge_max': 1})
    res = assemble(est, model, s, st)
    # 挑戦枠の曲は基本の並び（core/estimate/order.py）で最後
    assert ids(res) == ['L03', 'L04', 'L05', 'L00'] and [c['challenge'] for c in res['items']] == [False, False, False, True]
    assert res['filters']['challenge'] == 1 and res['filters']['challengeSkipped'] == 2
    assert [c['leaderboard_id'] for c in res['runnersUp'] if c['challenge']] == ['L01', 'L02']
    s.update({'challenge_max': 0})
    assert ids(assemble(est, model, s, st)) == ['L03', 'L04', 'L05']        # 足りない分は足りないまま（枠を守る）
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L02', 'pin', 'p1', 1)")
    res = assemble(est, model, s, st)
    assert 'L02' in ids(res) and res['filters']['challenge'] == 1         # 固定は枠が 0 でも入る
    model.range = {}                                                       # 範囲が分からなければ絞らない
    s.update({'challenge_max': 1})
    assert len(assemble(est, model, s, st)['items']) == 4


# ---- クリアできるか（core/estimate/clear.py） ----

def test_fit_clear_from_days():
    # pass が上がるほどクリアできる日が減るデータ → 見込みは pass とともに下がる
    rng = np.random.default_rng(1)
    days = []
    for _ in range(400):
        p = float(rng.uniform(1, 7))
        days.append((p, bool(rng.random() < 1 / (1 + np.exp(-(6 - 1.3 * p))))))
    m = CL.fit_clear(days)
    assert m is not None and m.b < 0 and m.days == 400 and m.fails == sum(1 for _, ok in days if not ok)
    assert m(2.0) > 0.9 > m(4.0) > 0.5 > m(6.0)
    assert abs(m(6.0, cleared_before=True) - (m(6.0) + (1 - m(6.0)) * CL.CLEARED_BONUS)) < 1e-12
    assert m(1000.0) == 0.0
    # 足りない・失敗が少ない・全部失敗・難しい方がクリアできている、は求めない
    assert CL.fit_clear(days[:20]) is None
    assert CL.fit_clear([(p, True) for p, _ in days[:60]] + [(5.0, False)] * 3) is None
    assert CL.fit_clear([(p, False) for p, _ in days]) is None
    assert CL.fit_clear([(p, p > 4) for p, _ in days]) is None
    # クリアできなかった日の記録が無ければ、下げ方は控えめな決まった値（自分の記録から求めない）
    assert m.c == CL.FAIL_DEFAULT and not m.fail_fitted and m.fail_days == 0


def _attempts(st, passes, rows):
    """譜面（{id: pass}）と挑戦の記録 [(id, 日時, end_type, モディファイア)] を入れる"""
    with st.tx():
        for lid, pr in passes.items():
            st.conn.execute('INSERT INTO leaderboards(leaderboard_id, pass_rating) VALUES(?,?)', (lid, pr))
        for lid, ts, et, mods in rows:
            st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)',
                            (lid, ts, 0.9, et, mods))


def test_clear_history_groups_by_map_and_day(tmp_path):
    # 同じ譜面・同じ日の挑戦は1つにまとめ、1回でもクリアすればその日はクリア。練習と NF は数えない
    st = Store(str(tmp_path / 'db.sqlite'))
    t = int(time.mktime((2026, 10, 16, 12, 0, 0, 0, 0, -1)))
    _attempts(st, {'A': 2.0, 'B': 5.0, 'U': None},
              [('A', t, 2, ''), ('A', t + 60, 1, ''), ('A', t + 86400, 2, ''),     # 1日目はクリア、2日目は失敗
               ('B', t, 2, ''), ('B', t + 60, 1, 'NF'), ('B', t + 120, 5, ''),      # NF のクリア・練習は数えない
               ('U', t, 1, '')])                                                     # pass が無い譜面は数えない
    days, now = CL.clear_history(st)
    assert sorted(tuple(x) for x in days) == [(2.0, False, 0.0, True), (2.0, True, 0.0, False), (5.0, False, 0.0, False)]
    assert now == {'A': (1, 0), 'B': (1, 0)}   # 今のクリアできなかった日（A は最後のクリアの後の1日）
    st.close()


def test_clear_history_counts_fail_days(tmp_path):
    # その日より前の、最後にクリアした日の後のクリアできなかった日の数。失敗（2）の無い日（やり直し・途中でやめただけ）は
    # SOFT_DAY_WEIGHT に数える。クリアした日（挑戦の記録のクリアか、自己ベストの日）で 0 に戻る
    D = 86400
    t = int(time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1)))
    rows = {
        'A': [(t, 2, ''), (t + D, 3, ''), (t + D + 60, 4, ''), (t + 2 * D, 2, ''),   # 失敗・やり直しとやめただけ・失敗
              (t + 3 * D, 1, ''),                                                     # クリア → 0 に戻る
              (t + 4 * D, 2, ''), (t + 5 * D, 2, '')],
        'B': [(t, 2, ''), (t + 2 * D, 2, ''), (t + 5 * D, 4, '')],                    # 自己ベストの日（t+D）の前後
        'C': [(t, 2, ''), (t + D, 2, '')],                                            # 自己ベストが最後の挑戦より後
        'N': [(t, 2, ''), (t + D, 1, 'NF'), (t + 2 * D, 2, '')],                      # NF のクリアは数えない
    }
    w = CL.SOFT_DAY_WEIGHT
    want = {   # 譜面ごとの日の並び (pass, クリアできたか, その日より前のクリアできなかった日, その日より前にクリアしたか)
        'A': [(3.0, False, 0.0, False), (3.0, False, 1.0, False), (3.0, False, 1 + w, False), (3.0, True, 2 + w, False),
              (3.0, False, 0.0, True), (3.0, False, 1.0, True)],
        'B': [(3.0, False, 0.0, False), (3.0, False, 0.0, True), (3.0, False, 1.0, True)],
        'C': [(3.0, False, 0.0, False), (3.0, False, 1.0, False)],
        'N': [(3.0, False, 0.0, False), (3.0, False, 1.0, False)],
    }
    best = {'B': t + D, 'C': t + 3 * D}
    for lid in rows:   # 譜面を1つずつ入れて、日の並びを確かめる
        st = Store(str(tmp_path / ('%s.sqlite' % lid)))
        _attempts(st, {lid: 3.0}, [(lid, *r) for r in rows[lid]])
        days, now = CL.clear_history(st, {lid: best[lid]} if lid in best else None)
        assert [tuple(d) for d in days] == want[lid], lid
        assert now.get(lid) == {'A': (2, 0), 'B': (1, 1), 'C': None, 'N': (2, 0)}[lid], lid   # C は最後に自己ベスト＝クリアした
        st.close()
    assert CL.fail_weight(1, 1) == 1 + w


def test_clear_history_soft_only_player(tmp_path):
    # 失敗（2）がほとんど記録されない人（クリアとやり直し・途中でやめただけ）は、やり直し・やめただけの日を数えない。
    # 見込みの回帰に使う日はクリアした日だけになり、見込みは求めない（2026-11-12: 着実が 0曲になった国内上位の人）
    D = 86400
    t = int(time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1)))
    passes = {'M%02d' % i: 2.0 + i * 0.2 for i in range(20)}

    def rows(fail_maps):
        out = []
        for i, lid in enumerate(passes):
            out += [(lid, t, 3, ''), (lid, t + 60, 4, ''), (lid, t + D, 1, ''), (lid, t + 2 * D, 3, '')]   # やり直し・クリア・やり直し
            if i < fail_maps:
                out.append((lid, t + 3 * D, 2, ''))
        return out

    st = Store(str(tmp_path / 'soft.sqlite'))
    _attempts(st, passes, rows(0))
    days, now = CL.clear_history(st)
    assert len(days) == 20 and all(d.ok and d.fails == 0 for d in days) and now == {}
    assert CL.fit_clear(days) is None
    st.close()
    # クリアできなかった日のうち失敗した日が HARD_MIN_SHARE 以上なら、今までどおりやり直し・やめただけの日も数える（失敗 5 日 ÷ 45 日）
    st = Store(str(tmp_path / 'mixed.sqlite'))
    _attempts(st, passes, rows(5))
    days, now = CL.clear_history(st)
    assert len(days) == 65 and sum(1 for d in days if not d.ok) == 45
    assert now['M00'] == (1, 1) and now['M19'] == (0, 1)
    st.close()
    # 1割より少なければ数えない（失敗 4 日 ÷ 44 日。失敗した日だけが残る）
    st = Store(str(tmp_path / 'few.sqlite'))
    _attempts(st, passes, rows(4))
    days, now = CL.clear_history(st)
    assert len(days) == 24 and sum(1 for d in days if not d.ok) == 4 and now == {'M%02d' % i: (1, 0) for i in range(4)}
    assert CL.soft_only([]) is False and CL.soft_only([{'ok': True, 'hard': False}]) is False
    st.close()


def test_fit_clear_fail_term():
    # クリアできなかった日が続く日はクリアしにくいデータ → c は負で、元にした値の近く。k=0 の見込みは今までと同じ
    rng = np.random.default_rng(2)
    days = []
    for _ in range(3000):
        p, k, cb = float(rng.uniform(1, 7)), float(rng.choice([0, 0, 0, 0.5, 1, 2, 3])), bool(rng.random() < 0.5)
        q = 1 / (1 + np.exp(-(6 - 1.3 * p)))
        q = q + (1 - q) * CL.CLEARED_BONUS if cb else q
        q = 1 / (1 + np.exp(-(np.log(q / (1 - q)) - 1.5 * np.log1p(k))))
        days.append(CL.Day(p, bool(rng.random() < q), k, cb))
    m = CL.fit_clear(days)
    assert m.fail_fitted and m.fail_days == sum(1 for d in days if d.fails > 0) and abs(m.c - (-1.5)) < 0.3
    base = CL.ClearModel(m.a, m.b, 0, 0)
    for p, cb in ((2.0, False), (4.0, True), (6.0, False)):
        assert m(p, cb) == base(p, cb) == m(p, cb, 0)                  # 下げるのはクリアできなかった日がある時だけ
        z = np.log(base(p, cb) / (1 - base(p, cb))) + m.c * np.log1p(2)
        assert abs(m(p, cb, 2) - 1 / (1 + np.exp(-z))) < 1e-12 and m(p, cb, 2) < m(p, cb, 1) < m(p, cb)
    # クリアできなかった日の方がクリアしやすいデータは下げない（0 にする）・大きすぎる値は FAIL_MAX まで
    up = [CL.Day(d.pass_rating, d.ok or (d.fails > 0 and rng.random() < 0.9), d.fails, d.cleared) for d in days]
    assert CL.fit_clear(up).c == 0.0
    down = [CL.Day(d.pass_rating, d.ok and d.fails == 0, d.fails, d.cleared) for d in days]
    down += [CL.Day(1.0, True, 1.0, False)] * CL.CLEAR_MIN_FAILS   # クリアした日も少しある（無いと求めない）
    assert CL.fit_clear(down).c == -CL.FAIL_MAX
    # 求めるのに足りない時は決まった値
    few = [d for d in days if d.fails == 0] + [d for d in days if d.fails > 0][:CL.FAIL_MIN_DAYS - 1]
    m3 = CL.fit_clear(few)
    assert not m3.fail_fitted and m3.c == CL.FAIL_DEFAULT


def test_beyond_and_slopes():
    rng = {'pass': {'p95': 3.0, 'max': 4.0}, 'nps': {'p95': 5.0, 'max': 6.0}}
    assert CL.beyond(rng, {'pass_rating': 3.1, 'nps': 1}) and CL.beyond(rng, {'pass_rating': 1, 'nps': 5.5})
    assert not CL.beyond(rng, {'pass_rating': 3.0, 'nps': None}) and not CL.beyond({}, {'pass_rating': 9, 'nps': 9})
    xs = np.linspace(2, 8, 200)
    curve = C.fit(xs, 0.98 - 0.02 * xs)
    stars = list(xs)
    assert abs(CL.upper_slope(curve, stars) - (-0.02)) < 0.002
    assert CL.upper_slope(curve, stars[:50]) is None and CL.upper_slope(None, stars) is None
    assert CL.upper_slope(curve, [5.0] * 100) is None           # ★の幅が狭すぎる
    assert CL.pop_slope([-0.01] * 5 + [None] * 10) is None and CL.pop_slope([-0.01, -0.03, -0.02] * 4) == -0.02
    assert CL.star_hi(list(range(1, 101))) == 95 and CL.star_hi([5.0] * 10) is None


def test_summary_rank(env):
    st, s, model = env
    model.my_scores = {}
    est = [cand(1, 'unplayed', 0)]
    est[0]['gain'], est[0]['est_pp'], est[0]['score'] = 15, 15, 15
    s.update({'playlist_size': 1, 'played_ratio': 0})
    res = assemble(est, model, s, st)
    assert abs(res['summary']['gain'] - 15) < 1e-9
    assert res['summary']['passed'] == 1 and res['summary']['newRank'] == 9   # 1010 PP の人だけ抜く


def test_summary_is_expected_with_clear(env):
    # このリストで増える総PP の見込み＝曲ごとの「クリアできる見込み × 増える総PP（期待値）」の合計（見込みの無い曲は 1）
    st, s, model = env
    model.my_scores = {}
    est = [cand(1, 'unplayed', 10), cand(2, 'unplayed', 6), cand(3, 'unplayed', 4)]
    est[0]['clear'], est[1]['clear'] = 0.5, 0.25
    s.update({'playlist_size': 3, 'played_ratio': 0, 'min_clear': 0})
    res = assemble(est, model, s, st)
    assert abs(res['summary']['gain'] - (10 * 0.5 + 6 * 0.25 + 4)) < 1e-9


# ---- 出力 ----

def test_bplist_build():
    items = [cand(1, 'played', 5, song='AB' * 20), cand(2, 'played', 4, song='AB' * 20), cand(3, 'unplayed', 3, song='cd' * 20)]
    items[1]['difficulty'] = 'ExpertPlus'
    items[2]['hash'] = 'not-a-hash'
    d = bplist.build(items, '次回おすすめ', 'desc', b'\x89PNG')
    assert d['playlistTitle'] == '次回おすすめ' and d['image'].startswith('data:image/png;base64,')
    assert len(d['songs']) == 1                     # 不正なハッシュは捨て、同じ曲はまとめる
    song = d['songs'][0]
    assert song['hash'] == ('ab' * 20)
    assert song['difficulties'] == [{'characteristic': 'Standard', 'name': 'expert'}, {'characteristic': 'Standard', 'name': 'expertPlus'}]


def test_bplist_write(tmp_path):
    s = Settings(str(tmp_path / 'config'))
    with pytest.raises(bplist.OutputError) as e:
        bplist.write(s, str(tmp_path), {})
    assert e.value.code == 'no-beat-saber-dir'
    s.update({'beat_saber_dir': str(tmp_path / 'nope')}, trusted=True)
    with pytest.raises(bplist.OutputError) as e:
        bplist.write(s, str(tmp_path), {})
    assert e.value.code == 'beat-saber-dir-missing'
    bs = tmp_path / 'Beat Saber'
    (bs / 'Playlists').mkdir(parents=True)
    s.update({'beat_saber_dir': str(bs)}, trusted=True)
    path, copy = bplist.write(s, str(tmp_path), {'playlistTitle': 'x', 'songs': []})
    assert path == str(bs / 'Playlists' / 'blnext-next.bplist')
    assert json.loads(open(path, encoding='utf-8').read())['playlistTitle'] == 'x'
    assert copy and os.path.isfile(copy) and os.path.dirname(copy) == str(tmp_path / 'exports')
    bplist.write(s, str(tmp_path), {'playlistTitle': 'y', 'songs': []})   # 同じファイルを上書き
    assert json.loads(open(path, encoding='utf-8').read())['playlistTitle'] == 'y'
    assert os.listdir(bs / 'Playlists') == ['blnext-next.bplist']


# ---- 取得から組み立てまで（作り物の BeatLeader） ----

def _fake_engine(tmp_path, monkeypatch, **settings):
    from core.fetch import http as bhttp
    from core.fetch.http import BLClient
    from core.fetch.refresh import Refresher
    from fakebl import FakeBL
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)
    fake = FakeBL(n_players=40, self_rank=20, n_maps=80)
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 10, 'pool_below': 10, 'min_scores': 10, 'min_overlap': 5,
              'max_map_age_years': 0, **settings})
    Refresher(st, s, lambda: BLClient('t', opener=fake)).run_sync()
    return st, s, Engine(st, s)


def test_estimate_lowers_above_my_star_range(tmp_path, monkeypatch):
    # 自分の★の範囲より上は、比較対象の人の下がり方 × 倍率で下げる（範囲の中は同じ）。倍率 0 なら今までどおり
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    model.star_hi, model.pop_slope = 5.0, -0.03
    now = model.built_at
    s.update({'beyond_star_factor': 0})
    off = {c['leaderboard_id']: c for c in R.estimate(st, model, s, now)}
    s.update({'beyond_star_factor': 1})
    on = {c['leaderboard_id']: c for c in R.estimate(st, model, s, now)}
    hi_val = float(model.me.curve(5.0))
    for lid, c in on.items():
        if c['stars'] <= 5.0:
            assert abs(c['est_acc'] - off[lid]['est_acc']) < 1e-12 and 'beyond' not in c['flags']
        else:
            want = min(float(model.me.curve(c['stars'])), hi_val - 0.03 * (c['stars'] - 5.0))
            assert abs(c['base_acc'] - max(0.0, want)) < 1e-12 and 'beyond' in c['flags']
            assert c['est_acc'] <= off[lid]['est_acc'] + 1e-12
    assert any(c['stars'] > 6 and c['est_acc'] < off[lid]['est_acc'] - 0.01 for lid, c in on.items())
    model.pop_slope = None                                     # 下がり方が分からなければ下げない
    again = {c['leaderboard_id']: c for c in R.estimate(st, model, s, now)}
    assert all(abs(again[k]['est_acc'] - off[k]['est_acc']) < 1e-12 for k in off)
    st.close()


def test_estimate_multiplies_clear_chance(tmp_path, monkeypatch):
    # 並び順の値 ＝ 総PPの増加 × 確からしさ × クリアの見込み（効き方 clear_factor）。増える総PP の値はそのまま
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, est0 = eng.get()
    assert model.clear is None and all(c['clear'] is None for c in est0)   # 作り物の挑戦の記録は少ないので求めない
    model.clear = CL.ClearModel(6.0, -1.5, 100, 20)
    for cf in (1.0, 0.5, 0.0):
        s.update({'clear_factor': cf})
        for c in R.estimate(st, model, s, model.built_at):
            p = model.clear(c['pass_rating'], c['kind'] == 'played')
            assert abs(c['clear'] - p) < 1e-12
            assert abs(c['score'] - c['gain'] * c['confidence'] * (1 - cf * (1 - p))) < 1e-9
    st.close()


def test_estimate_tried_and_fail_days(tmp_path, monkeypatch):
    # 自己ベストの無い曲の挑戦の記録（tried。練習は数えない）と、クリアできなかった日が続いている曲の見込みを下げる（fails）
    from core.estimate import model as M
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, est = eng.get()
    unpl = next(c['leaderboard_id'] for c in est if c['kind'] == 'unplayed')
    played = next(c['leaderboard_id'] for c in est if c['kind'] == 'played' and c['leaderboard_id'] != 'LB000')
    t = int(time.mktime(time.localtime(model.built_at - 10 * 86400)[:3] + (12, 0, 0, 0, 0, -1)))
    with st.tx():
        for lid, ts, et, acc in ((unpl, t, 4, 0.66), (unpl, t + 600, 5, 0.7), (unpl, t + 8 * 86400, 3, 0.74),
                                 (unpl, t + 8 * 86400 + 120, 2, 0.781),
                                 (played, t + 86400, 2, 0.8)):   # プレイ済みの曲（自己ベストは 2023 年）の後の失敗
            st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)',
                            (lid, ts, acc, et, ''))
    model = M.build(st, s, model.built_at)
    assert model.clear is None and model.fails == {unpl: (1, 1), played: (1, 0)}   # 作り物の LB000 は失敗の後にクリアした
    by = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    assert by[unpl]['tried'] == {'n': 3, 'clears': 0, 'at': t + 8 * 86400 + 120, 'acc': 0.781, 'end': 2}
    assert by[unpl]['kind'] == 'unplayed' and by[played]['tried'] is None
    assert all(c['fails'] is None for c in by.values())     # 見込みが無ければ下げない
    model.clear = CL.ClearModel(6.0, -1.5, 100, 20, c=-2.0)
    by = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    for lid, cb, k in ((unpl, False, 1 + CL.SOFT_DAY_WEIGHT), (played, True, 1.0)):
        c = by[lid]
        assert abs(c['clear'] - model.clear(c['pass_rating'], cb, k)) < 1e-12 and c['clear'] < model.clear(c['pass_rating'], cb)
        assert c['fails']['days'] == (2 if lid == unpl else 1) and c['fails']['soft'] == (1 if lid == unpl else 0)
        assert abs(c['fails']['base'] - model.clear(c['pass_rating'], cb)) < 1e-12
        assert abs(c['score'] - c['gain'] * c['confidence'] * c['clear']) < 1e-9
    other = next(c for c in by.values() if c['leaderboard_id'] not in (unpl, played))
    assert other['fails'] is None and abs(other['clear'] - model.clear(other['pass_rating'], other['kind'] == 'played')) < 1e-12
    st.close()


# ---- モードごとの★の換算（2026-10-29。作者の調べ「ワンセイバーの★」） ----

def test_star_scales_fits_ratio():
    """一番スコアの多いモードの曲線に合うよう、ほかのモードの★に掛ける倍率を選ぶ。スコアの少ないモード・モードが1つなら換算しない"""
    from core.estimate import model as M
    rng = np.random.default_rng(3)
    rows, modes = [], {}

    def add(mode, n, k):
        for i in range(n):
            lid = '%s%d' % (mode, i)
            st_ = float(rng.uniform(2, 8))
            rows.append({'leaderboard_id': lid, 'stars': st_, 'acc': 0.98 - 0.02 * k * st_ + float(rng.normal(0, 0.004))})
            modes[lid] = mode
    add('Standard', 200, 1.0)
    add('OneSaber', 30, 0.8)        # 同じ★でも精度が出やすい＝★×0.8 で両手の曲線に乗る
    add('Lawless', 5, 0.6)          # 少なすぎる
    sc = M.star_scales(rows, modes, np.ones(len(rows)))
    assert set(sc) == {'OneSaber'} and abs(sc['OneSaber'] - 0.8) <= 0.03
    std = [r for r in rows if modes[r['leaderboard_id']] == 'Standard']
    assert M.star_scales(std, modes, np.ones(len(std))) == {}


def test_build_and_estimate_use_scaled_stars(tmp_path, monkeypatch):
    """自分のスコアに別のモードが MODE_MIN_SCORES 以上あれば、換算した★で曲線・残差・★の範囲の上端を求め、推定もその★で曲線を読む"""
    from core.estimate import model as M
    st, s, eng = _fake_engine(tmp_path, monkeypatch, beyond_star_factor=0)
    model, _ = eng.get()
    assert model.star_scale == {} and model.eff_stars(6.0, 'Standard') == 6.0    # 作り物は全部両手のモード
    monkeypatch.setattr(M, 'MODE_MIN_SCORES', 5)                                   # 作り物の自分のスコアは20件
    one = sorted(model.me.residuals)[:6]
    with st.tx():
        st.conn.execute('UPDATE leaderboards SET mode=? WHERE leaderboard_id IN (%s)' % ','.join('?' * len(one)), ['OneSaber'] + one)
    model = M.build(st, s, model.built_at)
    k = model.star_scale['OneSaber']
    assert set(model.star_scale) == {'OneSaber'} and model.eff_stars(6.0, 'OneSaber') == pytest.approx(6.0 * k)
    use = [r for r in model.my_scores.values() if M._usable(r)]
    eff = [r['stars'] * (k if r['leaderboard_id'] in one else 1.0) for r in use]
    assert model.star_hi == pytest.approx(CL.star_hi(eff))
    r = model.my_scores[one[0]]
    assert model.me.residuals[one[0]] == pytest.approx(r['acc'] - float(model.me.curve(r['stars'] * k)))
    by = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    for lid in (one[0], sorted(set(model.me.residuals) - set(one))[0]):
        c = by[lid]
        x = c['stars'] * (k if lid in one else 1.0)
        assert c['base_acc'] == pytest.approx(float(model.me.curve(x)))
    st.close()


# ---- 推定の直し（2026-10-18 の調査の R1〜R3。作者の調べ「精度の推定とpass」） ----

def test_fc_norm_window_and_db(tmp_path):
    # R1 の「その pass で普通のミスの量」: 自分の自己ベストの (fc_acc − acc) の、pass ±0.5 の中央。幅の中が10件より少なければ 0
    from core.estimate import model as M
    pairs = [(2.0 + 0.05 * i, 0.01 * i) for i in range(20)]      # pass 2.0〜2.95
    g = M.FcNorm(pairs)
    near = [d for p, d in pairs if abs(p - 2.5) <= 0.5]
    assert len(near) >= M.FC_NORM_MIN and abs(g(2.5) - float(np.median(near))) < 1e-12
    near = [d for p, d in pairs if abs(p - 1.8) <= 0.5]          # 2.0〜2.3 の7件
    assert len(near) < M.FC_NORM_MIN and g(1.8) == 0.0
    assert g(None) == 0.0 and M.FcNorm([])(3.0) == 0.0
    # DB から: ランクの譜面・NF を除く・fc_acc のあるスコアだけ（FC は差 0）。ほかの人のスコアも使わない
    st = Store(str(tmp_path / 'db.sqlite'))
    with st.tx():
        for i in range(16):
            st.conn.execute('INSERT INTO leaderboards(leaderboard_id, status, pass_rating) VALUES(?,?,?)',
                            ('L%02d' % i, 0 if i == 15 else 3, 3.0))
        rows = [('1', 'L%02d' % i, 0.9, 0.9 + 0.01 * (i + 1), '') for i in range(11)]     # 差 0.01〜0.11
        rows += [('1', 'L11', 0.9, 0.9, ''),                                               # FC（差 0）
                 ('1', 'L12', 0.9, 1.4, 'NF'), ('1', 'L13', 0.9, None, ''),               # NF・fc_acc が無い → 使わない
                 ('2', 'L14', 0.9, 1.4, ''), ('1', 'L15', 0.9, 1.4, '')]                  # ほかの人・ランクでない → 使わない
        for r in rows:
            st.conn.execute('INSERT INTO scores(player_id, leaderboard_id, acc, fc_acc, modifiers) VALUES(?,?,?,?,?)', r)
    assert abs(M.fc_norm(st, '1')(3.0) - 0.055) < 1e-9       # [0, 0.01〜0.11] の中央
    st.close()


def test_estimate_fc_gap_above_usual(tmp_path, monkeypatch):
    # R1: FC との差の上乗せは、その pass で普通の量より多い分 × fc_gap_factor。多い分が FC_GAP_MIN より少なければ上乗せも印も無し。
    # 推定に効くのは、R4（前の自己ベストとの間に寄せる）の後なので (1 − R4_PB_WEIGHT) 倍（精度の上限は外して確かめる）
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    model.ceiling = None
    k = 1 - R.R4_PB_WEIGHT
    assert abs(model.fc_norm(2.0) - 0.01) < 1e-9     # 作り物の自己ベストは、どれも FC との差が 0.01（＝普通の量）
    lid = next(l for l in model.my_scores if l in model.me.residuals)
    row = model.my_scores[lid]
    row.update({'fc_acc': row['acc'] + 0.04, 'missed_notes': 2, 'bad_cuts': 0, 'modifiers': ''})

    def run(norm):
        model.fc_norm = norm
        return {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    full, part, small = run(None), run(lambda p: 0.03), run(lambda p: 0.037)
    f = s.values()['fc_gap_factor']
    assert 'fc' in full[lid]['flags'] and 'fc' in part[lid]['flags'] and 'fc' not in small[lid]['flags']
    assert abs((full[lid]['est_acc'] - small[lid]['est_acc']) - k * f * 0.04) < 1e-9     # 普通の量が分からない時は今までどおり全部
    assert abs((part[lid]['est_acc'] - small[lid]['est_acc']) - k * f * 0.01) < 1e-9
    assert abs(full[lid]['parts']['r1'] - f * 0.04) < 1e-6 and abs(part[lid]['parts']['r1'] - f * 0.01) < 1e-6
    st.close()


def test_estimate_unplayed_pass_drop(tmp_path, monkeypatch):
    # R2: 自分の結果が無い曲（未プレイ・NF のみ）だけ、pass が基準（クリアしてきた曲の pass の75%点）を超える分 × u だけ下げる。
    # 自分の★の範囲より上の曲は2倍。設定 0 なら下げない
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    p0, hi = 3.0, 8.5       # 作り物の未プレイの曲は ★5〜10.9・pass ＝ ★の半分
    model.range, model.star_hi = {'pass': {'p75': p0, 'p95': 4.5, 'max': 5.0}}, hi

    def run(u):
        s.update({'unplayed_pass_drop': u})
        return {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    off, on, on2 = run(0), run(1.0), run(2.0)
    seen = set()
    for lid, c in on.items():
        d = off[lid]['est_acc'] - c['est_acc']
        if c['kind'] == 'played' or c['pass_rating'] <= p0:
            assert abs(d) < 1e-12 and 'pass' not in c['flags']
            seen.add('played' if c['kind'] == 'played' else 'low')
            continue
        want = 0.01 * (c['pass_rating'] - p0) * (2 if c['stars'] > hi else 1)
        assert 'pass' in c['flags'] and 'pass' not in off[lid]['flags']
        if 0 < on2[lid]['est_acc'] and off[lid]['est_acc'] < R.ACC_MAX:   # 0〜上限で切られていない曲
            assert abs(d - want) < 1e-9 and abs((off[lid]['est_acc'] - on2[lid]['est_acc']) - 2 * want) < 1e-9
            seen.add('beyond' if c['stars'] > hi else 'in')
    assert seen == {'played', 'low', 'in', 'beyond'}
    # NF のスコアしか無い曲も「自分の結果が無い曲」として下げる。基準が分からない時は pass 3.0
    nf = next(l for l in model.my_scores if l in model.me.residuals)
    model.my_scores[nf] = dict(model.my_scores[nf], modifiers='NF')
    model.range = {'pass': {'p75': 1.0, 'p95': 2.0, 'max': 3.0}}
    off, on = run(0), run(1.0)
    want = 0.01 * (on[nf]['pass_rating'] - 1.0)
    assert 'pass' in on[nf]['flags'] and abs((off[nf]['est_acc'] - on[nf]['est_acc']) - want) < 1e-9
    assert all(abs(on[l]['est_acc'] - off[l]['est_acc']) < 1e-12 for l in on if on[l]['kind'] == 'played' and l != nf)
    model.range = {}
    assert R.pass_base(model) == R.UNPLAYED_P0 == 3.0
    st.close()


# ---- 推定の直し（2026-10-25 の調査の R5 と R1 の制限。作者の調べ「曲の特徴と似ている人」） ----

def test_estimate_unplayed_tech_drop(tmp_path, monkeypatch):
    # R5: 自分の結果が無い曲（未プレイ・NF のみ）だけ、tech が基準（クリアしてきた曲の tech の中央）を超える分 × u だけ下げる。
    # ★の範囲より上でも2倍にしない。設定 0・基準が分からない時は下げない
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    t0, hi = 2.4, 8.5       # 作り物の譜面は tech ＝ ★ × 0.3（未プレイは★5〜10.9 → tech 1.5〜3.27）
    model.range, model.star_hi = {'tech': {'p50': t0, 'p75': 2.8, 'p95': 3.0, 'max': 3.3}}, hi

    def run(u):
        s.update({'unplayed_tech_drop': u, 'unplayed_pass_drop': 0})
        return {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    off, on, on2 = run(0), run(1.0), run(2.0)
    seen = set()
    for lid, c in on.items():
        d = off[lid]['est_acc'] - c['est_acc']
        if c['kind'] == 'played' or c['tech_rating'] <= t0:
            assert abs(d) < 1e-12 and 'tech' not in c['flags'] and 'r5' not in c['parts']
            seen.add('played' if c['kind'] == 'played' else 'low')
            continue
        want = 0.01 * (c['tech_rating'] - t0)          # ★の範囲より上でも同じ（2倍にしない）
        assert 'tech' in c['flags'] and 'tech' not in off[lid]['flags'] and abs(c['parts']['r5'] + want) < 1e-6
        if 0 < on2[lid]['est_acc'] and off[lid]['est_acc'] < R.ACC_MAX:   # 0〜上限で切られていない曲
            assert abs(d - want) < 1e-9 and abs((off[lid]['est_acc'] - on2[lid]['est_acc']) - 2 * want) < 1e-9
            seen.add('beyond' if c['stars'] > hi else 'in')
    assert seen == {'played', 'low', 'in', 'beyond'}
    # NF のスコアしか無い曲も「自分の結果が無い曲」として下げる
    nf = next(l for l in model.my_scores if l in model.me.residuals)
    model.my_scores[nf] = dict(model.my_scores[nf], modifiers='NF')
    model.range = {'tech': {'p50': 0.5, 'p75': 1.0, 'p95': 2.0, 'max': 3.0}}
    off, on = run(0), run(1.0)
    assert 'tech' in on[nf]['flags'] and abs((off[nf]['est_acc'] - on[nf]['est_acc']) - 0.01 * (on[nf]['tech_rating'] - 0.5)) < 1e-9
    assert all(abs(on[l]['est_acc'] - off[l]['est_acc']) < 1e-12 for l in on if on[l]['kind'] == 'played' and l != nf)
    # 基準（クリアしてきた曲の tech の分布）が無ければ下げない
    model.range = {'pass': {'p50': 2.0, 'p75': 3.0, 'p95': 4.0, 'max': 5.0}}
    assert R.tech_base(model) is None
    assert all(abs(c['est_acc'] - off[l]['est_acc']) < 1e-12 and 'tech' not in c['flags'] for l, c in run(1.0).items())
    assert R.unplayed_tech_drop(3.0, None, 0.01) == 0.0 and R.unplayed_tech_drop(None, 2.0, 0.01) == 0.0
    assert R.unplayed_tech_drop(2.0, 2.0, 0.01) == 0.0 and abs(R.unplayed_tech_drop(3.5, 2.0, 0.01) - 0.015) < 1e-12
    st.close()


def test_unplayed_tech_drop_is_an_estimate_setting(tmp_path, monkeypatch):
    # 計算の設定なので Engine の EST_KEYS に入っている: 値を変えると推定を作り直し、未プレイで tech の高い曲が下がる
    # （曲線・類似度は作り直さない）。初期値は 0.5%
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    assert 'unplayed_tech_drop' in EST_KEYS and s.values()['unplayed_tech_drop'] == 0.5
    m0, e0 = eng.get()
    assert m0.range['tech']['p50'] is not None
    s.update({'unplayed_tech_drop': 3.0})
    m1, e1 = eng.get()
    assert m1 is m0 and e1 is not e0
    a, b = {c['leaderboard_id']: c for c in e0}, {c['leaderboard_id']: c for c in e1}
    t0 = m0.range['tech']['p50']
    low = [l for l in a if a[l]['kind'] == 'unplayed' and a[l]['tech_rating'] > t0 and 0 < b[l]['est_acc'] < R.ACC_MAX]
    assert low and all(abs((a[l]['est_acc'] - b[l]['est_acc']) - 0.025 * (a[l]['tech_rating'] - t0)) < 1e-9 for l in low)
    assert all(abs(a[l]['est_acc'] - b[l]['est_acc']) < 1e-12 for l in a if a[l]['kind'] == 'played')
    st.close()


def test_estimate_fc_gap_skipped_far_below_curve(tmp_path, monkeypatch):
    # R1 の制限: 自分の残差（前の自己ベスト − 曲線）が FC_SKIP_BELOW（−5%）より小さい曲には R1 を足さず、足すはずだった量を
    # 内訳の r1Skipped に残す（印 fc も付けない）。残差がそれ以上なら今までどおり。残差が無い曲（曲線に使えないスコア）も今までどおり
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    model.pop_slope, model.fc_norm = None, None     # ★の範囲より上の下げ無し（残差＝曲線との差のまま）・普通のミスの量は 0
    model.ceiling = None                            # 精度の上限は外す（推定に効く R1 は R4 の後で (1 − R4_PB_WEIGHT) 倍）
    k = 1 - R.R4_PB_WEIGHT
    lid = next(l for l in model.my_scores if l in model.me.residuals)
    row = model.my_scores[lid]
    row.update({'fc_acc': row['acc'] + 0.04, 'missed_notes': 2, 'bad_cuts': 0, 'modifiers': ''})
    f = s.values()['fc_gap_factor']
    assert R.FC_SKIP_BELOW == -0.05

    def run(res, miss=2):
        model.me.residuals[lid] = res
        row['missed_notes'] = miss
        return next(c for c in R.estimate(st, model, s, model.built_at) if c['leaderboard_id'] == lid)
    for res, skip in ((-0.08, True), (-0.0501, True), (-0.05, False), (-0.02, False), (0.03, False)):
        c, plain = run(res), run(res, miss=0)       # miss=0 は R1 の条件に入らない（FC）
        if skip:
            assert 'fc' not in c['flags'] and 'r1' not in c['parts'] and abs(c['parts']['r1Skipped'] - f * 0.04) < 1e-6
            assert abs(c['est_acc'] - plain['est_acc']) < 1e-12, res
        else:
            assert 'fc' in c['flags'] and 'r1Skipped' not in c['parts'] and abs(c['parts']['r1'] - f * 0.04) < 1e-6
            assert abs((c['est_acc'] - plain['est_acc']) - k * f * 0.04) < 1e-9, res
        assert 'r1Skipped' not in plain['parts'] and 'r1' not in plain['parts']
    # 残差が無い（曲線に使えないスコア）プレイ済みの曲は、今までどおり足す
    del model.me.residuals[lid]
    row['missed_notes'] = 2
    c = next(c for c in R.estimate(st, model, s, model.built_at) if c['leaderboard_id'] == lid)
    assert c['kind'] == 'played' and 'fc' in c['flags'] and abs(c['parts']['r1'] - f * 0.04) < 1e-6
    st.close()


PART_KEYS = {'base', 'mix', 'r1', 'r2', 'r3', 'r4', 'r5', 'cap', 'clip', 'r1Skipped'}


def _parts_sum(p):
    return sum(v for k, v in p.items() if k != 'r1Skipped')


def test_estimate_parts_add_up(tmp_path, monkeypatch):
    # 推定の内訳: base（曲線）＋ mix ＋ 付いた直し（r1・r2・r3・r5・r4）＋ cap（精度の上限で切った量）＋ clip（0〜上限で切った量）
    # ＝ est_acc。付かなかった直しは入れない。印と内訳がそろう（pb は R4 で動かした量が PB_FLAG_MIN 以上の時だけ）
    from core.estimate import model as M
    monkeypatch.setattr(M, 'CEIL_MIN', 5)  # 作り物の曲は★の窓の曲が少ないので、上限が付くように
    monkeypatch.setattr(CL, 'ATTEMPT_DAYS', 100000)   # 作り物の日時は古いので、挑戦の記録を読む期間の制限を外す
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    lid, lid2 = [l for l in model.my_scores if l in model.me.residuals][:2]
    model.me.residuals[lid] = 0.6          # 上限を超える推定（精度の上限で切った量 cap、上限が無ければ clip が出る）
    model.me.residuals[lid2] = 0.0         # R1 が付く曲（普通のミスの量 0.01 より 0.04 多い）
    model.my_scores[lid2] = dict(model.my_scores[lid2], fc_acc=model.my_scores[lid2]['acc'] + 0.05)
    with st.tx():                          # R3 も付くように、挑戦の記録を自己ベストより前から始める
        st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)',
                        ('LB079', 1600000000, 0.5, 2, ''))
    seen = set()
    for ceiling in (model.ceiling, None):
        model.ceiling = ceiling
        est = R.estimate(st, model, s, model.built_at)
        for c in est:
            p = c['parts']
            assert set(p) <= PART_KEYS and {'base', 'mix'} <= set(p)
            assert abs(_parts_sum(p) - c['est_acc']) < 1e-5 and abs(p['base'] - c['base_acc']) < 1e-6, c['leaderboard_id']
            for flag, key in (('pass', 'r2'), ('tech', 'r5'), ('fc', 'r1')):
                assert (flag in c['flags']) == (key in p)
            assert (('cleared1' in c['flags']) or ('cleared3' in c['flags'])) == ('r3' in p)
            assert ('pb' in c['flags']) == ('r4' in p and abs(p['r4']) >= R.PB_FLAG_MIN - 1e-6)
            assert ('r4' in p) <= (c['kind'] == 'played') and ('cap' not in p or 'cap' in c['flags'])
            assert all(p[k] != 0 for k in p if k not in ('base', 'mix'))
            seen |= set(p)
        top = next(c for c in est if c['leaderboard_id'] == lid)
        if ceiling is not None:
            assert top['parts']['cap'] < 0 and 'clip' not in top['parts'] and top['est_acc'] == top['acc_cap']['acc'] < R.ACC_MAX
        else:
            assert top['est_acc'] == R.ACC_MAX and top['parts']['clip'] < 0 and 'cap' not in top['parts']
    assert {'r1', 'r2', 'r3', 'r4', 'r5', 'cap', 'clip'} <= seen
    st.close()


def test_clear_days_and_adjust():
    # R3: その譜面をクリアした日の数（挑戦の記録のクリア＝end_type 1 と自己ベストの日。NF・失敗・やり直し・練習は数えない。
    # 同じ日は1日。日の区切りは挑戦の記録のまとめ方と同じ clear.day_key＝0 時）と、日の数で足す値 s(k)
    day = lambda d, hr: int(time.mktime((2026, 9, d, hr, 0, 0, 0, 0, -1)))
    a = lambda ts, et=1, mods='': {'timeset': ts, 'end_type': et, 'modifiers': mods}
    atts = [a(day(1, 10)), a(day(1, 20)),                     # 同じ日の2回 → 1日
            a(day(2, 23)), a(day(3, 1), mods='SS'),           # 0 時をまたぐ → 2日（NF 以外のモディファイアは数える）
            a(day(4, 12), mods='NF'), a(day(5, 12), 2), a(day(6, 12), 5), a(day(7, 12), 3)]   # 数えない
    first = day(1, 10)
    assert CL.day_key(day(2, 23)) != CL.day_key(day(3, 1))
    assert R.clear_days(atts, None, first) == (3, False)
    assert R.clear_days(atts, {'timeset': day(3, 5)}, first) == (3, False)    # 自己ベストの日がクリアした日と同じ
    assert R.clear_days(atts, {'timeset': day(8, 12)}, first) == (4, False)
    assert R.clear_days([], {'timeset': day(8, 12)}, first) == (1, False)
    assert R.clear_days([], {'timeset': first - 86400}, first) == (1, True)   # 挑戦の記録より前の自己ベスト → 数えた日は少なめ
    # 直しはミスの割合（1 − 精度）に掛ける倍率（推定の幅と同じ尺度）。精度 89% の曲で ±1%、100% に近い曲ほど小さい
    assert (R.CLEAR_DAY_ONE, R.CLEAR_DAY_MANY, R.CLEAR_DAY_MANY_N) == (0.91, 1.09, 3)
    adj = lambda *a: round(R.clear_days_adj(*a), 6)
    assert [adj(k, False, 0.89) for k in range(6)] == [0.0, 0.0099, 0.0, -0.0099, -0.0099, -0.0099]
    assert [adj(k, True, 0.89) for k in range(5)] == [0.0, 0.0, 0.0, -0.0099, -0.0099]  # 少なめの時は3日以上と分かった時だけ
    assert adj(1, False, 0.98) == 0.0018 and adj(3, False, 0.98) == -0.0018 and adj(1, False, 1.0) == 0.0
    assert adj(1, False, 1.2) == 0.0   # 1 を超える推定（後で切る）でもミスの割合は負にしない


def test_estimate_clear_days(tmp_path, monkeypatch):
    # R3 を推定に: プレイ済みの曲（自分の結果がある曲）だけ、クリアした日の数で直す。挑戦の記録が無い人は直さない
    monkeypatch.setattr(CL, 'ATTEMPT_DAYS', 100000)   # 作り物の日時は古いので、挑戦の記録を読む期間の制限を外す
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    model.ceiling = None    # 精度の上限は外す（推定に効く R3 は R4 の後で (1 − R4_PB_WEIGHT) 倍）
    k = 1 - R.R4_PB_WEIGHT
    mine = [l for l in model.my_scores if l in model.me.residuals]
    by = lambda: {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    ins = lambda lid, ts, et: st.conn.execute(
        'INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)', (lid, ts, 0.85, et, ''))
    with st.tx():
        st.conn.execute('DELETE FROM attempts')
    base = by()
    assert not any({'cleared1', 'cleared3'} & set(c['flags']) for c in base.values())
    many = mine[1]
    t = int(time.mktime(time.localtime(model.built_at - 30 * 86400)[:3] + (12, 0, 0, 0, 0, -1)))
    with st.tx():
        for d in range(3):
            ins(many, t + d * 86400, 1)
    got = by()

    def moved(l, ratio):
        """R3 で動いた量が、曲線と混ぜた値（base＋mix）のミスの割合に (1 − 倍率) を掛けた分か（内訳は6桁に丸めてある。
        推定は R4 の後なので k 倍だけ動く）"""
        p = got[l]['parts']
        want = (1 - ratio) * (1 - (p['base'] + p['mix']))
        return abs((got[l]['est_acc'] - base[l]['est_acc']) - k * want) < 2e-6 and abs(p['r3'] - want) < 2e-6
    # 作り物の自己ベスト（2023 年）は挑戦の記録の始まりより前なので、数えた日は少なめ → 3日以上の曲だけ直す
    assert 'cleared3' in got[many]['flags'] and moved(many, R.CLEAR_DAY_MANY) and got[many]['parts']['r3'] < 0
    assert all(abs(got[l]['est_acc'] - base[l]['est_acc']) < 1e-12 and 'cleared1' not in got[l]['flags'] for l in got if l != many)
    # 挑戦の記録が自己ベストより前から始まっていれば、自己ベストの日だけ（1日）の曲は上げる（失敗の日は数えない）
    with st.tx():
        ins(mine[2], 1600000000, 2)
    got = by()
    assert 'cleared3' in got[many]['flags'] and moved(many, R.CLEAR_DAY_MANY)
    for l in mine:
        if l != many:
            assert 'cleared1' in got[l]['flags'] and moved(l, R.CLEAR_DAY_ONE) and got[l]['parts']['r3'] > 0, l
    assert all(abs(got[l]['est_acc'] - base[l]['est_acc']) < 1e-12 for l in got if got[l]['kind'] == 'unplayed')
    st.close()


# ---- 推定の幅（2026-10-28。recommend.py の SPREAD・AccDist・expected_gain） ----

@pytest.mark.parametrize('has_self', [True, False])
@pytest.mark.parametrize('est', [0.3, 0.85, 0.93, 0.98, R.ACC_MAX, 1.2])
def test_acc_dist_quantiles(est, has_self):
    # 分位点と累積の確率は逆の関係・右上がり・精度は 1 を超えない。中央（z = m）より悪くなる確率は「悪い側の幅 ÷ 両側の幅の和」
    d = R.AccDist(est, has_self)
    m, su, sd = R.SPREAD[has_self]
    assert abs(d.lo - sd / (su + sd)) < 1e-12
    for q in (0.001, 0.05, 0.3, d.lo, 0.8, 0.999):
        x = d.ppf(q)
        assert x < 1 and abs(d.cdf(x) - q) < 1e-9
    assert abs(d.ppf(d.lo) - (1 - d.miss * math.exp(-m))) < 1e-12
    assert d.cdf(1.0) == 1.0 and d.cdf(1.5) == 1.0
    xs = [d.ppf(q) for q in np.linspace(0.01, 0.99, 50)]
    assert all(a < b for a, b in zip(xs, xs[1:]))


def test_acc_dist_narrows_near_100():
    # 幅はミスの割合の比で持つので、100% に近い推定ほど精度の幅は狭い（伸びる余地はミスの割合に比例する）
    up = lambda est: R.AccDist(est, True).ppf(0.9) - est
    assert up(0.85) > up(0.93) > up(0.98) > 0
    assert abs(up(0.98) / up(0.85) - 0.02 / 0.15) < 1e-9
    # 自分の結果が無い曲（初クリア）は悪い側に広い
    assert R.AccDist(0.9, False).ppf(0.05) < R.AccDist(0.9, True).ppf(0.05)


def test_expected_gain():
    d = R.AccDist(0.90, True)
    gain_at = lambda x: max(0.0, x - 0.91) * 1000   # 自己ベスト 91% を超えた分 1% につき +10
    p, g = R.expected_gain(d, 0.91, gain_at)
    # 超える見込みは 1 − cdf(自己ベスト)。期待値（超える側の確率を等分した点の平均）は、細かく足した値とほぼ同じ
    assert abs(p - (1 - d.cdf(0.91))) < 1e-12 and 0.1 < p < 0.5
    qs = (np.arange(20000) + 0.5) / 20000
    fine = float(np.mean([gain_at(d.ppf(q)) for q in qs]))
    assert abs(g - fine) / fine < 0.03
    # 自己ベストが無い曲は分布の全体（見込み 1）。超える見込みがほぼ無ければ 0
    assert R.expected_gain(d, None, lambda x: 2.0) == (1.0, 2.0)
    p0, g0 = R.expected_gain(d, 0.9995, gain_at)
    assert p0 < R.UP_MIN and g0 == 0.0
    # 推定が高いほど、超える見込みも期待値も大きい。上の端は ACC_MAX で切る
    p2, g2 = R.expected_gain(R.AccDist(0.92, True), 0.91, gain_at)
    assert p2 > p and g2 > g
    seen = []
    R.expected_gain(R.AccDist(R.ACC_MAX, True), None, lambda x: seen.append(x) or 0.0)
    assert len(seen) == R.SPREAD_NODES and max(seen) <= R.ACC_MAX


def test_estimate_gain_is_expected_value(tmp_path, monkeypatch):
    # 増える総PP は推定の幅で見た期待値: 推定が自己ベストより低い曲も、超える見込みの分だけ入る（推定どおりの時の増えは 0）。
    # gain = p_up × gain_up、並び順の値もこの gain から。未プレイの曲は p_up が無い
    from core.estimate.ppcalc import pp_func_of_row
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    first = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    lid = next(l for l, c in first.items() if c['kind'] == 'played' and 'nf' not in c['flags'] and c['est_acc'] < 0.97)
    lb = st.q1('SELECT * FROM leaderboards WHERE leaderboard_id=?', (lid,))
    pb = first[lid]['est_acc'] + 0.02             # 自己ベストを推定より 2% 上にする
    model.my_scores[lid] = dict(model.my_scores[lid], acc=pb, pp=pp_func_of_row(lb)(pb), modifiers='')
    got = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    c = got[lid]
    assert c['est_pp'] < c['best_pp'] and 0.02 < c['p_up'] < 0.5
    assert c['gain'] > 0 and abs(c['gain'] - c['p_up'] * c['gain_up']) < 1e-9 and c['gain_up'] > c['gain']
    clear_mul = 1.0 if c['clear'] is None else 1 - float(s.values()['clear_factor']) * (1 - c['clear'])
    assert abs(c['score'] - c['gain'] * c['confidence'] * clear_mul) < 1e-9
    for x in got.values():
        if x['kind'] == 'unplayed':
            assert x['p_up'] is None and abs(x['gain_up'] - x['gain']) < 1e-9
    st.close()


# ---- R4 と精度の上限（2026-11-01。作者のプレイの記録 の 2026-11-01） ----

def test_estimate_r4_pulls_toward_best(tmp_path, monkeypatch):
    # R4: 自分の結果がある曲は、ほかの直しの後の推定を前の自己ベストとの間に寄せる（+ R4_PB_WEIGHT × (自己ベスト − 推定)）。
    # 内訳 r4・印 pb（動かした量が PB_FLAG_MIN 以上の時だけ）。未プレイ・NF だけの曲は寄せない。超える見込み・増える総PP は
    # 推定の幅から出すので、上に寄せた曲は上がり、下に寄せた曲は下がる
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    model.ceiling = None
    assert R.R4_PB_WEIGHT == 0.5 and R.PB_FLAG_MIN == 0.005
    mine = [l for l in model.my_scores if l in model.me.residuals]
    model.me.residuals[mine[0]] += 0.05      # 推定が自己ベストより大きく上の曲
    model.me.residuals[mine[1]] -= 0.05      # 大きく下の曲
    nf = mine[2]
    model.my_scores[nf] = dict(model.my_scores[nf], modifiers='NF')
    monkeypatch.setattr(R, 'R4_PB_WEIGHT', 0.0)
    off = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    monkeypatch.setattr(R, 'R4_PB_WEIGHT', 0.5)
    on = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    signs = set()
    for lid, c in on.items():
        a = off[lid]
        if c['kind'] != 'played' or lid == nf:
            assert c['est_acc'] == a['est_acc'] and 'r4' not in c['parts'] and 'pb' not in c['flags'] and c['gain'] == a['gain']
            continue
        d = 0.5 * (c['best_acc'] - a['est_acc'])
        assert abs(c['est_acc'] - (a['est_acc'] + d)) < 1e-12 and abs(c['parts']['r4'] - d) < 1e-6, lid
        assert ('pb' in c['flags']) == (abs(d) >= 0.005) and 'pb' not in a['flags'] and 'r4' not in a['parts']
        if abs(d) >= 0.005:
            assert (c['p_up'] > a['p_up'] and c['gain'] > a['gain']) if d > 0 else (c['p_up'] < a['p_up'] and c['gain'] < a['gain'])
            signs.add(d > 0)
    assert signs == {True, False}
    assert on[mine[0]]['est_acc'] < off[mine[0]]['est_acc'] and on[mine[1]]['est_acc'] > off[mine[1]]['est_acc']
    st.close()


def test_acc_ceiling_window(monkeypatch):
    # 精度の上限: 同じモードで★ ±CEIL_WINDOW の、ほかの曲の自分の一番。窓の端は入る。その曲自身は除く。窓のほかの曲が CEIL_MIN より
    # 少なければ上限なし。返り値は (上限, その精度の譜面, 窓のほかの曲の数)。CEIL_MIN は 2026-11-01 に 5 → 8（ワンセイバーの窓は
    # 5〜13 曲で、5 だときつすぎた）。窓の数え方はこの表で、CEIL_MIN を 5 にして確かめる
    from core.estimate import model as M
    assert (M.CEIL_WINDOW, M.CEIL_MIN) == (0.5, 8)
    monkeypatch.setattr(M, 'CEIL_MIN', 5)
    monkeypatch.setattr(M, 'CEIL_WINDOW_MAX', M.CEIL_WINDOW)   # 広げない時の窓の数え方（広げるのは test_acc_ceiling_widens）
    ce = M.AccCeiling(_ceiling_rows())

    def got(*a):
        v, lid, n, w = ce(*a)
        assert w == (None if v is None else M.CEIL_WINDOW)
        return (None if v is None else round(v, 6)), lid, n
    assert got(5.5, 'Standard') == (0.908, 'S8', 9)                     # 窓 5.0〜6.0 の全部（両端も入る・★4.0 の LOW は外）
    assert got(5.5, 'Standard', 'S8') == (0.907, 'S7', 8)               # その曲自身は除く
    assert got(5.0, 'Standard') == (0.904, 'S4', 5)                     # 窓 4.5〜5.5 の S0〜S4（ちょうど CEIL_MIN 曲）
    assert got(4.875, 'Standard') == (None, None, 4)                    # 窓 4.375〜5.375 の S0〜S3 だけ → 上限なし
    assert got(4.5, 'Standard') == (None, None, 2)                      # 窓 4.0〜5.0 は LOW と S0 だけ
    assert got(5.5, 'OneSaber') == (None, None, 4)                      # モードごと（ワンセイバーは4曲なので付けない）
    assert got(5.5, 'Lawless') == (None, None, 0) and got(None, 'Standard') == (None, None, 0)
    assert got(6.6, 'Standard') == (None, None, 0)


def _ceiling_rows():
    rows = [('Standard', 5.0 + 0.125 * i, 0.90 + 0.001 * i, 'S%d' % i) for i in range(9)]   # ★5.0〜6.0
    rows += [('Standard', 4.0, 0.97, 'LOW'), ('OneSaber', 5.5, 0.99, 'ONE')]                 # 窓の外・ほかのモード
    rows += [('OneSaber', 5.4 + 0.01 * i, 0.9, 'O%d' % i) for i in range(3)]
    return rows


def test_acc_ceiling_widens(monkeypatch):
    # ±CEIL_WINDOW のほかの曲が CEIL_MIN より少なければ、CEIL_WINDOW ずつ CEIL_WINDOW_MAX まで広げる（2026-11-12。遊んだ曲の少ない★の帯で
    # 上限が付かず、易しい未プレイの曲が 99% 台に押し上げられていた）。返り値の 4つ目は使った窓の幅。広げても足りなければ上限なし
    from core.estimate import model as M
    assert M.CEIL_WINDOW_MAX == 1.0
    monkeypatch.setattr(M, 'CEIL_MIN', 5)
    ce = M.AccCeiling(_ceiling_rows())
    r = lambda *a: (lambda v, lid, n, w: ((None if v is None else round(v, 6)), lid, n, w))(*ce(*a))
    assert r(5.5, 'Standard') == (0.908, 'S8', 9, 0.5)          # 足りていれば広げない
    assert r(4.875, 'Standard') == (0.97, 'LOW', 9, 1.0)        # ±0.5 は 4曲 → ±1.0（★3.875〜5.875）で LOW と S0〜S7
    assert r(4.5, 'Standard') == (0.97, 'LOW', 6, 1.0)          # ±0.5 は 2曲 → ±1.0（★3.5〜5.5）で LOW と S0〜S4
    assert r(6.6, 'Standard') == (None, None, 4, None)          # ±1.0（★5.6〜7.6）でも S5〜S8 の 4曲
    assert r(5.5, 'OneSaber') == (None, None, 4, None)          # 広げても増えない
    monkeypatch.setattr(M, 'CEIL_MIN', 8)
    assert r(4.875, 'Standard') == (0.97, 'LOW', 9, 1.0) and r(4.5, 'Standard') == (None, None, 6, None)


def test_acc_ceiling_from_db(tmp_path, monkeypatch):
    # DB から: 自分のランクの譜面のスコア（NF を除く）だけ。★はスコアの★（モディファイアで補正した★）
    from core.estimate import model as M
    monkeypatch.setattr(M, 'CEIL_MIN', 5)
    monkeypatch.setattr(M, 'CEIL_WINDOW_MAX', M.CEIL_WINDOW)   # 読む行の確かめ（窓は広げない）
    st = Store(str(tmp_path / 'db.sqlite'))
    with st.tx():
        for i in range(10):
            st.conn.execute('INSERT INTO leaderboards(leaderboard_id, status, stars, mode) VALUES(?,?,?,?)',
                            ('L%d' % i, 0 if i == 9 else 3, 5.0, 'Standard'))
        rows = [('1', 'L%d' % i, 0.90 + 0.01 * i, 5.0, '') for i in range(6)]
        rows += [('1', 'L6', 0.99, 5.0, 'NF'), ('2', 'L7', 0.99, 5.0, ''), ('1', 'L8', 0.98, 6.0, 'SS'),   # NF・ほかの人・★が窓の外
                 ('1', 'L9', 0.99, 5.0, '')]                                                               # ランクでない
        for r in rows:
            st.conn.execute('INSERT INTO scores(player_id, leaderboard_id, acc, stars, modifiers) VALUES(?,?,?,?,?)', r)
    ce = M.acc_ceiling(st, '1')
    v, lid, n, _ = ce(5.0, 'Standard')            # L8 は譜面の★は 5.0 だが、スコアの★（SS で補正）は 6.0 なので窓の外
    assert v == pytest.approx(0.95) and (lid, n) == ('L5', 6)
    v, lid, n, _ = ce(5.0, 'Standard', 'L5')
    assert v == pytest.approx(0.94) and (lid, n) == ('L4', 5)
    assert ce(5.6, 'Standard') == (None, None, 1, None)   # 窓 5.1〜6.1 は L8 だけ
    st.close()


def test_expected_gain_with_cap():
    # 精度の上限: 上限より上の精度は上限で取れたものとみなす。自己ベストが上限より下なら超える見込みはそのままで、期待値は小さくなる。
    # 自己ベストが上限以上なら見込みも期待値も 0。上限が分布より十分上なら上限なしと同じ
    d = R.AccDist(0.92, True)
    gain_at = lambda x: max(0.0, x - 0.91) * 1000
    p, g = R.expected_gain(d, 0.91, gain_at)
    pc, gc = R.expected_gain(d, 0.91, gain_at, cap=0.925)
    assert pc == p and 0 < gc < g
    qs = (np.arange(20000) + 0.5) / 20000
    fine = float(np.mean([gain_at(min(0.925, d.ppf(q))) for q in qs]))
    assert abs(gc - fine) / fine < 0.03
    assert R.expected_gain(d, 0.925, gain_at, cap=0.925) == (0.0, 0.0) and R.expected_gain(d, 0.93, gain_at, cap=0.925) == (0.0, 0.0)
    assert R.expected_gain(d, 0.91, gain_at, cap=0.9999) == (p, g)
    seen = []
    pu, gu = R.expected_gain(d, None, lambda x: seen.append(x) or 1.0, cap=0.9)   # 自己ベストが無い曲も上限で切る
    assert pu == 1.0 and gu == 1.0 and max(seen) == 0.9 and min(seen) < 0.9


def test_estimate_caps_at_ceiling(tmp_path, monkeypatch):
    # 推定は上限で頭打ち（内訳 cap）、推定の幅は上限で切る前の推定を中心にして上限より上を上限に寄せる。自己ベストが上限以上の曲は
    # 超える見込みも増える総PP も 0（組み立てで nogain）。未プレイの曲にもかける。上限が無い曲（窓の曲が少ない）は今までどおり。
    # 印 cap: 自己ベストが上限以上か、推定の幅の CAP_FLAG_SHARE 以上が上限より上だった曲
    st, s, eng = _fake_engine(tmp_path, monkeypatch, beyond_star_factor=0, acc_cap_margin=0)   # 余裕は最後に確かめる
    model, _ = eng.get()
    model.ceiling = None
    off = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    assert all(c['acc_cap'] is None and 'cap' not in c['flags'] for c in off.values())
    played = sorted(l for l, c in off.items() if c['kind'] == 'played' and c['gain'] > 0)
    unpl = sorted(l for l, c in off.items() if c['kind'] == 'unplayed' and c['gain'] > 0)
    a0, a1, u0 = off[played[0]], off[played[1]], off[unpl[0]]
    capped = {played[0]: max(a0['best_acc'], a0['est_acc']) + 0.004,   # 自己ベストと推定より少し上（幅の上の端だけ削る）
              played[1]: a1['best_acc'] - 0.005,                       # 自己ベストより下（伸びしろ 0）
              unpl[0]: u0['est_acc'] - 0.01}                           # 未プレイの曲の推定より下（推定を頭打ち）
    # 4つ目は使った窓の幅（未プレイの曲は広げた窓で付いたことにする。画面の「近い★（★〜★）」に使う）
    model.ceiling = lambda stars, mode, exclude=None: ((capped[exclude], 'LB005', 7, 1.0 if exclude == unpl[0] else 0.5)
                                                      if exclude in capped else (None, None, 3, None))
    on = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    for lid, c in on.items():
        a = off[lid]
        if lid not in capped:
            assert c['acc_cap'] is None and c['est_acc'] == a['est_acc'] and c['gain'] == a['gain'] and 'cap' not in c['flags']
            continue
        cap = capped[lid]
        x = c['acc_cap']
        assert x['acc'] == cap and x['song'] == 'Song LB005' and x['n'] == 7 and x['stars'] == pytest.approx(3.5)
        assert x['window'] == (1.0 if lid == unpl[0] else 0.5)
        center = min(R.ACC_MAX, max(0.0, a['est_acc']))
        dist = R.AccDist(center, c['kind'] == 'played')
        assert abs(x['over'] - (1 - dist.cdf(cap))) < 1e-12
        assert c['est_acc'] == min(a['est_acc'], cap) and (('cap' in c['parts']) == (a['est_acc'] > cap))
        best = c['best_acc']
        assert ('cap' in c['flags']) == bool((best and best >= cap) or x['over'] >= R.CAP_FLAG_SHARE)
        if best and best >= cap:
            assert c['p_up'] == 0 and c['gain'] == 0 and c['gain_up'] == 0 and c['score'] == 0
        else:
            assert c['p_up'] == a['p_up'] and 0 < c['gain'] < a['gain']   # 超える見込みは同じで、増えの期待値だけ小さい
    assert 'cap' not in on[played[0]]['parts'] and on[played[0]]['est_acc'] == a0['est_acc']
    assert on[played[1]]['gain'] == 0 and 'cap' in on[played[1]]['flags']
    assert on[unpl[0]]['est_acc'] == capped[unpl[0]] and 'cap' in on[unpl[0]]['flags'] and on[unpl[0]]['acc_cap']['over'] > 0.5
    # 組み立て: 自己ベストが上限以上の曲は増える総PP が 0 なので外れる
    res = assemble(list(on.values()), model, s, st)
    assert played[1] not in [c['leaderboard_id'] for c in res['items'] + res['runnersUp']]
    # 難しさの段階の余裕（acc_cap_margin・%）は上限に足す: 自己ベストが一番より 0.5% 下の曲も、余裕 1.0% なら伸びしろが戻る
    s.update({'acc_cap_margin': 1.0})
    wide = {c['leaderboard_id']: c for c in R.estimate(st, model, s, model.built_at)}
    x = wide[played[1]]['acc_cap']
    assert x['top'] == capped[played[1]] and x['margin'] == pytest.approx(0.01) and x['acc'] == pytest.approx(capped[played[1]] + 0.01)
    assert wide[played[1]]['gain'] > 0 and wide[played[1]]['p_up'] > 0
    st.close()


def test_build_has_ceiling(tmp_path, monkeypatch):
    # model.build が自分のスコアから精度の上限を作り、推定の曲に acc_cap を付ける（作り物の自己ベストは 0.90〜0.94 を繰り返すので、
    # 窓の曲が足りる曲の上限は 0.94。自己ベストが 0.94 の曲は伸びしろ 0）
    from core.estimate import model as M
    monkeypatch.setattr(M, 'CEIL_MIN', 5)   # 作り物の曲は★の窓の曲が少ない
    st, s, eng = _fake_engine(tmp_path, monkeypatch, acc_cap_margin=0)
    model, est = eng.get()
    assert model.ceiling is not None
    mine = [c for c in est if c['kind'] == 'played']
    withcap = [c for c in mine if c['acc_cap']]
    assert withcap and all(c['acc_cap']['acc'] == pytest.approx(0.94) for c in withcap)
    top = [c for c in withcap if c['best_acc'] >= 0.94 - 1e-9]
    assert top and all(c['gain'] == 0 and c['p_up'] == 0 and 'cap' in c['flags'] for c in top)
    st.close()


def test_confidence_with_self_result_ignores_similar(tmp_path, monkeypatch):
    # 確からしさ: 自分の結果がある曲は似ている人の数に依らず 1（伸び悩みだけ半分）。無い曲は似ている人の重みの合計で決め、
    # 3人（min_similar_on_map）未満なら半分にして few の印
    st, s, eng = _fake_engine(tmp_path, monkeypatch, min_similar_on_map=50)
    model, _ = eng.get()
    for similar in (model.similar, []):
        model.similar = similar
        got = R.estimate(st, model, s, model.built_at)
        mine = [c for c in got if c['kind'] == 'played' and 'nf' not in c['flags']]
        assert mine and all(c['confidence'] == (0.5 if 'stall' in c['flags'] else 1.0) and 'few' not in c['flags'] for c in mine)
        others = [c for c in got if c['kind'] == 'unplayed']
        assert others and all('few' in c['flags'] and c['confidence'] < 0.5 for c in others)
    st.close()


def test_end_to_end(tmp_path, monkeypatch):
    from core.fetch import http as bhttp
    from core.fetch.http import BLClient
    from core.fetch.refresh import Refresher
    from fakebl import FakeBL
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)
    fake = FakeBL(n_players=40, self_rank=20, n_maps=80)
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 10, 'pool_below': 10, 'min_scores': 10, 'min_overlap': 5,
              'max_map_age_years': 0, 'challenge_max': 50})
    Refresher(st, s, lambda: BLClient('t', opener=fake)).run_sync()
    eng = Engine(st, s)
    model, est = eng.get()
    assert model is not None and len(model.pool) == 20 and len(est) == 80
    assert model.me.curve is not None
    res = assemble(est, model, s, st)
    assert len(res['items']) == 15 and res['summary']['gain'] > 0
    # 挑戦枠: 作り物の未プレイの曲は自分の範囲（pass の95%点）の外なので、1曲までにすると残りはプレイ済みで埋める
    # （それでも足りなければ、枠は守って曲数の方が足りなくなる）
    s.update({'challenge_max': 1})
    r1 = assemble(est, model, s, st)
    assert sum(c['challenge'] for c in r1['items']) == 1 and r1['short'] == 15 - len(r1['items'])
    assert all(c['challenge'] for c in r1['runnersUp'] if c['kind'] == 'played')   # 余ったプレイ済みは挑戦枠の曲だけ
    assert r1['filters']['challenge'] == 1 and r1['filters']['challengeSkipped'] > 0
    assert any(c['challenge'] for c in r1['runnersUp'])          # 外した曲は次点から入れ替えられる
    m2, e2 = eng.get()
    assert m2 is model and e2 is est           # 何も変わらなければ作り直さない
    s.update({'self_weight': 1})
    m3, e3 = eng.get()
    assert m3 is model and e3 is not est       # 推定の設定だけ変えたら曲線・類似度はそのまま


# ---- 振り返り（出力 → プレイ → 次の取得） ----

def test_review_and_cooldown(tmp_path, monkeypatch):
    from core import review
    from core.fetch import http as bhttp
    from core.fetch.http import BLClient
    from core.fetch.refresh import Refresher
    from fakebl import FakeBL
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)
    fake = FakeBL(n_players=40, self_rank=20, n_maps=80)
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 10, 'pool_below': 10, 'min_scores': 10, 'min_overlap': 5,
              'max_map_age_years': 0, 'playlist_size': 6, 'played_ratio': 50})
    ref = Refresher(st, s, lambda: BLClient('t', opener=fake))
    ref.run_sync()
    eng = Engine(st, s)
    model, est = eng.get()
    res = assemble(est, model, s, st)
    played = [c['leaderboard_id'] for c in res['items'] if c['kind'] == 'played']
    assert len(played) >= 2
    t_export = int(time.time()) - 100
    review.record_export(st, model, res['items'], res['runnersUp'], s.values(), 'x.bplist', t_export)
    # 1曲は伸びた、1曲はプレイしたが伸びなかった（最終挑戦日時だけ進む）、残りはまだ
    up, flat = played[0], played[1]
    fake.my_scores[up] = {'acc': 0.999, 'time': t_export + 50}
    orig_score = fake._score

    def score(lb, sc):
        d = orig_score(lb, sc)
        if lb == flat:
            d['lastTryTime'] = t_export + 60
            d['playCount'] = 99
        return d
    fake._score = score
    ref.run_sync()
    last = review.last(st)
    r = last['runs'][0]
    assert r['preset'] == 'p1' and len(last['runs']) == 1
    st_by = {it['song']: it['status'] for it in r['items']}
    assert st_by['Song ' + up] == 'improved' and st_by['Song ' + flat] == 'played'
    assert r['counts']['improved'] == 1 and r['counts']['played'] == 1
    assert last['estError']['n'] == 1      # 伸びた曲だけ実際の精度が分かる


# ---- 似ている人が足りない人（model.FEW_SIMILAR）----

def test_few_similar_from_build(tmp_path, monkeypatch):
    # 共通の曲が足りず似ている人が FEW_SIMILAR 人より少ないと few_similar。top_similar を小さくしただけでは足りない扱いにしない
    from core.estimate import model as M
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model = M.build(st, s, int(time.time()))
    assert len(model.similar) >= M.FEW_SIMILAR and not model.few_similar
    s.update({'top_similar': 3})
    model = M.build(st, s, int(time.time()))
    assert len(model.similar) == 3 and not model.few_similar
    s.update({'top_similar': 20, 'min_overlap': 500})
    model = M.build(st, s, int(time.time()))
    assert model.similar == [] and model.few_similar
    st.close()


def test_few_similar_confidence_floor(tmp_path, monkeypatch):
    # 似ている人が足りない人だけ、自分の結果が無い曲の確からしさに下限を置く（0 だと並び順の値が全部 0 で、データベースの順に選ばれる）。
    # 自分の結果がある曲と、似ている人がいる人は変わらない
    st, s, eng = _fake_engine(tmp_path, monkeypatch, min_overlap=500)
    model, _ = eng.get()
    assert model.few_similar
    now = model.built_at
    on = R.estimate(st, model, s, now)
    un = [c for c in on if c['kind'] == 'unplayed']
    assert un and all(c['confidence'] == R.CONF_FLOOR and c['score'] == pytest.approx(c['gain'] * R.CONF_FLOOR) for c in un)
    assert [c['score'] for c in on] == sorted((c['score'] for c in on), reverse=True)
    assert all(c['confidence'] == 1 for c in on if c['kind'] == 'played')
    model.few_similar = False
    off = R.estimate(st, model, s, now)
    assert all(c['confidence'] == 0 and c['score'] == 0 for c in off if c['kind'] == 'unplayed')
    st.close()


def test_few_similar_cap_is_my_best(tmp_path, monkeypatch):
    # 窓の曲が足りない時の上限: 似ている人が足りない人だけ、同じモードのほかの曲の自分の一番＋難しさの余裕（window は None）
    st, s, eng = _fake_engine(tmp_path, monkeypatch, min_overlap=500)
    model, _ = eng.get()
    now = model.built_at
    margin = s.values()['acc_cap_margin'] / 100
    on = R.estimate(st, model, s, now)
    alone = [c for c in on if model.ceiling(c['stars'], c['mode'], c['leaderboard_id'])[0] is None]
    assert alone
    for c in alone:
        top, lid = model.ceiling.overall(c['mode'], c['leaderboard_id'])
        assert c['acc_cap']['window'] is None and c['acc_cap']['acc'] == pytest.approx(top + margin) and c['est_acc'] <= c['acc_cap']['acc'] + 1e-9
    model.few_similar = False
    off = {c['leaderboard_id']: c for c in R.estimate(st, model, s, now)}
    assert all(off[c['leaderboard_id']]['acc_cap'] is None for c in alone)
    st.close()


def test_acc_ceiling_overall():
    from core.estimate import model as M
    ce = M.AccCeiling([('Standard', 5.0, 0.90, 'A'), ('Standard', 8.0, 0.80, 'B'), ('OneSaber', 3.0, 0.99, 'C')])
    assert ce.overall('Standard') == (0.90, 'A')
    assert ce.overall('Standard', 'A') == (0.80, 'B')       # その曲自身は除く
    assert ce.overall('OneSaber', 'C') == (None, None)      # ほかの曲が無い
    assert ce.overall('Lawless') == (None, None)


def test_estimate_stops_below_my_stars(tmp_path, monkeypatch):
    # 自分のスコアの一番低い★より下は、曲線を端の傾きで延ばさず、端の値で止める（誰でも）
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    xs = np.linspace(6, 9, 30)
    model.me.curve = C.fit(xs, 0.95 - 0.02 * (xs - 6))      # ★6〜9 の点だけの、右下がりの曲線
    model.star_hi = None
    lo = model.me.curve.lo
    edge = float(model.me.curve(lo))
    assert float(model.me.curve(3.0)) > edge + 0.01         # 曲線そのものは★の低い方へ延びる
    for c in R.estimate(st, model, s, model.built_at):
        want = edge if c['stars'] < lo else float(model.me.curve(c['stars']))
        assert c['base_acc'] == pytest.approx(want)
    st.close()


def test_few_similar_star_top(env):
    # 似ている人が足りない人は、未プレイの曲を自分がクリアしてきた★の一番上までにする（端は入る）。プレイ済みの曲・固定した曲は外さない
    st, s, model = env
    s.update({'played_ratio': 40})
    est = [cand(1, 'unplayed', 50, stars=9.0), cand(2, 'unplayed', 49, stars=7.0), cand(3, 'unplayed', 48, stars=6.0),
           cand(4, 'unplayed', 47, stars=5.0), cand(5, 'played', 46, stars=8.0), cand(6, 'played', 45, stars=6.0)]
    res = assemble(est, model, s, st)
    assert 'L01' in ids(res) and res['filters']['starTop'] is None and res['filters']['starTopOut'] == 0   # 似ている人がいる人は変わらない
    model.few_similar = True
    model.me.curve = SimpleNamespace(hi=7.0)
    res = assemble(est, model, s, st)
    assert 'L01' not in ids(res) and {'L02', 'L05'} <= set(ids(res))
    assert res['filters']['starTop'] == 7.0 and res['filters']['starTopOut'] == 1
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L01','pin','p1',1)")
    res = assemble(est, model, s, st)
    assert 'L01' in ids(res) and res['filters']['starTopOut'] == 0


def test_attempts_older_than_a_year_are_not_read(tmp_path, monkeypatch):
    # 推定が読む挑戦の記録は直近1年分だけ（clear.ATTEMPT_DAYS）。古い失敗で見込みを下げず、未クリアの回数にも数えず、R3 の日の数にも入れない。DB の行は消さない
    st, s, eng = _fake_engine(tmp_path, monkeypatch)
    model, _ = eng.get()
    now = model.built_at
    year = CL.ATTEMPT_DAYS * 86400
    lid = next(c['leaderboard_id'] for c in R.estimate(st, model, s, now) if c['kind'] == 'unplayed')
    with st.tx():
        st.conn.execute('DELETE FROM attempts')
        for ts, end in ((now - year - 86400, 2), (now - year + 86400, 2)):   # 1年の少し前と少し後の失敗
            st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)', (lid, ts, 0.5, end, ''))
    c = next(x for x in R.estimate(st, model, s, now) if x['leaderboard_id'] == lid)
    assert c['tried']['n'] == 1                                         # 1年より古い1回は数えない
    assert st.q1('SELECT COUNT(*) AS n FROM attempts')['n'] == 2        # 行は消さない
    days, fails = CL.clear_history(st, None, now - year)
    assert len(days) == 1 and fails[lid] == (1, 0)
    days, fails = CL.clear_history(st)                                  # 渡さなければ全部
    assert len(days) == 2 and fails[lid] == (2, 0)
    model = __import__('core.estimate.model', fromlist=['build']).build(st, s, now)
    assert model.fails[lid] == (1, 0)                                   # 曲線などを作る時も1年分だけ
    st.close()


def test_pool_max_rebuilds_model():
    assert 'pool_max' in MODEL_KEYS      # 変えたら曲線・似ている人を作り直す
