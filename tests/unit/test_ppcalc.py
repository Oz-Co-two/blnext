# PP 計算の移植（core/estimate/ppcalc.py。PPv4.1）が BeatLeader の値と合うか。
# 素材 fixtures/pp_cases.json: 実際のランクスコア（2026-11-06 に API から。★のばらけた 48件と、12 種類のモディファイアの公開のスコア 96件）から、
# 精度・譜面のレーティングと曲線の材料・モディファイア・API の PP と★だけを抜いたもの（プレイヤーは入れていない）。
# 全件の照合は tools/verify_pp.py（実際の応答を使う）
import json, math, os, runpy
import pytest
from core.estimate import ppcalc as P
from core.estimate.ppcalc import pp_from_acc, total_pp, total_pp_parts, to_stars

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/
with open(os.path.join(HERE, 'fixtures', 'pp_cases.json'), encoding='utf-8') as _f:
    CASES = json.load(_f)


def kw(c):
    """素材の1件 → pp_from_acc の譜面の引数"""
    return dict(predicted_acc=c['predictedAcc'], slider_percentage=c['sliderPercentage'], notes=c['notes'],
                note_span=c['noteSpan'], modifiers=c['modifiers'], modifiers_rating=c['modifiersRating'],
                modifier_values=c['modifierValues'], timing=(c['mode'] or '').lower() == 'rhythmgamestandard')


@pytest.mark.parametrize('c', CASES, ids=[f"{c['modifiers'] or '-'}/{c['mode']}/{i}" for i, c in enumerate(CASES)])
def test_pp_matches_api(c):
    r = pp_from_acc(c['acc'], c['accRating'], c['passRating'], c['techRating'], **kw(c))
    assert abs(r['pp'] - c['pp']) < 0.01
    for mine, api in (('acc_pp', 'accPP'), ('pass_pp', 'passPP'), ('tech_pp', 'techPP')):
        if c.get(api):
            assert abs(r[mine] - c[api]) < 0.01


@pytest.mark.parametrize('c', CASES[::7])
def test_map_stars_match_api(c):
    s = P.map_stars(c['accRating'], c['passRating'], c['techRating'], predicted_acc=c['predictedAcc'],
                    slider_percentage=c['sliderPercentage'], notes=c['notes'], note_span=c['noteSpan'])
    assert s == pytest.approx(c['stars'], abs=1e-4)


# ---- 譜面ごとの曲線（値は上の API との照合で確かめている） ----

@pytest.mark.parametrize('pa', [0, 0.5, 0.59, 0.6, 0.9995, 1])
def test_curve_outside_per_map_range_is_base_table(pa):
    assert P.get_curve(pa) == P.BASE_TABLE
    nerfed = P.get_curve(pa, 0.4, 20, 0)   # スライダーの割合は予測精度が範囲外なら効かない。ノーツの少ない下げだけ
    assert [x for x, _ in nerfed] == [x for x, _ in P.BASE_TABLE]
    assert [y for _, y in nerfed] == pytest.approx([y * 0.8031 for _, y in P.BASE_TABLE])


def test_curve_default_shape_below_955():
    # 予測精度 95.5% 以下は表の先頭の行＝標準の形（スライダーが無ければ基本の表のまま）
    assert P.get_curve(0.95) == P.BASE_TABLE
    assert P.get_curve(0.95, 0.3) != P.BASE_TABLE   # スライダーの傾きは効く


def test_per_map_curve_is_not_above_base_and_monotonic():
    c = P.get_curve(0.985, 0, 800, 150)
    assert c[0][0] == 1.0 and c[-1] == (0.0, 0.0)
    ys = [y for _, y in c]
    assert all(a >= b for a, b in zip(ys, ys[1:]))   # 精度の降順に並ぶ＝値も下がっていく
    assert all(y <= P.smooth_base_value(x) + 1e-12 for x, y in c if x >= 0.6)   # なめらかな基本の曲線より上にしない
    assert P.curve_value(0.95, c) == pytest.approx(1.0)   # 95% は 1.0 のまま
    assert P.curve_value(0.995, c) < P.curve_value(0.995)   # 予測精度の高い譜面は上の方が基本より低い


def test_low_note_multiplier():
    # wiki の「115 ノーツから始まり、20 ノーツで 19.7% 下がる」（PPv4.1）
    L = P.low_note_multiplier
    assert L(0) == 1 and L(115) == 1
    assert L(20) == pytest.approx(0.8031) and L(1) == pytest.approx(0.8031)
    assert L(200, 75) == 1
    assert L(200, 12) == pytest.approx(0.9) and L(200, 5) == pytest.approx(0.9)
    assert L(20, 12) == pytest.approx(0.8031 * 0.9)


def test_curve_value_table_points_and_ends():
    for x, y in P.BASE_TABLE:
        assert P.curve_value(x) == pytest.approx(y)
    assert P.curve_value(0.925) == pytest.approx((0.861 + 0.804) / 2)
    assert P.curve_value(-0.1) == pytest.approx(-0.297 / 6)   # 範囲の外は端の区間を延ばす（上流どおり）
    vals = [P.curve_value(i / 2000) for i in range(2001)]
    assert all(a <= b for a, b in zip(vals, vals[1:]))


def test_smooth_base_value_passes_table_points():
    for x, y in P.BASE_TABLE:
        assert P.smooth_base_value(x) == pytest.approx(y)
    assert P.smooth_base_value(-1) == 0 and P.smooth_base_value(2) == pytest.approx(7.2)


# ---- 式（PpFormula） ----

def test_pass_pp():
    assert P.pass_pp(0) == 0   # 12.3·e^0 − 30 < 0 → 0
    assert P.pass_pp(5) == pytest.approx(12.3 * math.exp(0.85966 * 5 ** (1 / 2.62)) - 30)
    assert P.pass_pp(15) == pytest.approx(12.3 * math.exp(0.85966 * 14 ** (1 / 2.62)) - 30)   # 13 より上は傾きを半分に（13 + 2·0.5）
    assert P.pass_pp(-1) == 0 and P.pass_pp(math.nan) == 0


def test_tech_pp():
    assert P.tech_pp(0.95, 0) == 0
    assert P.tech_pp(0.95, 4) == pytest.approx(5.7887 * 4) == P.tech_pp(0.5, 4)   # 精度に依らない（PPv4.1）
    assert P.tech_pp(0.95, 15) == pytest.approx(5.7887 * (6.2754 + (15 - 6.2754) * 0.7))   # 6.2754 より上は傾き 0.7
    assert P.tech_pp(0.95, 8) * 1.0128 == pytest.approx(44, abs=0.5) and P.tech_pp(0.95, 4) == pytest.approx(23, abs=0.5)   # wiki の例（8★ は 44PP・4★ は 23PP）


def test_pass_acc_multiplier():
    # wiki の例: 予測精度 97% の譜面は 94% で 1 倍・97% で 1.20 倍・100% で 1.43 倍、90% あたりで下限の 0.8 倍。予測精度が無ければ 1 倍
    M = P.pass_acc_multiplier
    assert M(0.94, 0.97) == pytest.approx(1.0) and M(0.97, 0.97) == pytest.approx(1.197, abs=0.001) and M(1.0, 0.97) == pytest.approx(1.433, abs=0.001)
    assert M(0.5, 0.97) == 0.8 and M(0.97, 0) == 1


def test_mid_star_top_multiplier():
    # ★6.38〜8 で最大 +5%。予測精度 − 3% から予測精度にかけて増える。★4.25 より下・★10.5 より上は無し。予測精度が無ければ無し
    M = P.mid_star_top_multiplier
    assert M(7, 0.97, 0.97) == pytest.approx(1.05) and M(7, 1.0, 0.97) == pytest.approx(1.05)
    assert M(7, 0.94, 0.97) == 1 and M(7, 0.955, 0.97) == pytest.approx(1.025)
    assert M(4, 0.97, 0.97) == 1 and M(10.5, 0.97, 0.97) == 1 and M(7, 0.97, 0) == 1
    assert 1 < M(5, 0.97, 0.97) < M(6, 0.97, 0.97) < 1.05 and 1.05 > M(9, 0.97, 0.97) > M(10, 0.97, 0.97) > 1


def test_curve_power_at():
    assert P.curve_power_at(2) == 1.08 and P.curve_power_at(5.3718) == 1.0
    assert P.curve_power_at(6) == 1.0 and P.curve_power_at(9.6894) == 0.9 and P.curve_power_at(12) == 0.9
    assert 0.9 < P.curve_power_at(8) < 1.0


def test_stars_and_low_star_bonus():
    assert P.low_star_multiplier(9) == 1 and P.low_star_multiplier(5) > 1
    assert P.stars_from_pp(0) == 0 and P.stars_from_pp(-5) < 0
    hi = P.stars_from_pp(2000)   # ★の高い所は上乗せなし
    assert hi == pytest.approx(2000 / 52)
    lo = P.stars_from_pp(200)    # ★の低い所は上乗せ込みで解く: s = stars(pp·mult(s))
    assert lo == pytest.approx(P._stars(200 * P.low_star_multiplier(lo)), abs=1e-5)
    assert to_stars(math.inf, 5, 3) == 0.0 and to_stars(11, 5, 3) > to_stars(10, 5, 3)
    assert to_stars(math.nan, 5, 3) == to_stars(0, 5, 3)   # NaN の accRating は「0 より大きくない」＝accPP 0（上流の C# と同じ）


def test_get_pp_golf_and_power():
    p, a, t = P.get_pp(0.9, 10, 0, 0, golf=True)
    assert (p, a, t) == (0.0, pytest.approx(0.9 * 10 * 38), 0.0)
    # 96% では曲線の乗数に関わらず同じ（★はここで決める）
    assert P.get_pp(0.96, 8, 3, 4, power=0.9)[1] == pytest.approx(P.get_pp(0.96, 8, 3, 4, power=1.08)[1])
    assert P.get_pp(0.99, 8, 3, 4, power=0.9)[1] < P.get_pp(0.99, 8, 3, 4, power=1.0)[1]


# ---- モディファイア ----

def test_normalize_modifier_values():
    d = P.normalize_modifier_values(None)
    assert d == P.DEFAULT_MODIFIER_VALUES and d is not P.DEFAULT_MODIFIER_VALUES
    d = P.normalize_modifier_values({'fs': 0.5, 'Sf': 1, 'gn': True, 'nf': 'x', 'zz': 3}, P.RANKED_MODIFIER_VALUES)
    assert (d['FS'], d['SF'], d['GN'], d['NF']) == (0.5, 1.0, 0.0, -1.0) and 'ZZ' not in d   # 真偽値・文字は無視


def test_total_multiplier():
    R, D = P.RANKED_MODIFIER_VALUES, P.DEFAULT_MODIFIER_VALUES
    assert P.get_total_multiplier(R, 'FS,GN', True, f32=False) == pytest.approx(1.4)
    assert P.get_total_multiplier(R, 'FS,GN', True) == pytest.approx(1.4, abs=1e-6)
    assert P.get_total_multiplier(R, 'FS,GN', False, f32=False) == 1.0   # 速度系はレーティングの差し替えで扱う
    assert P.get_total_multiplier(D, 'BFS', True, f32=False) == pytest.approx(1.2)   # 部分一致（上流どおり）
    assert P.get_total_multiplier(D, 'fs', True, f32=False) == 1.0   # 大文字小文字は区別
    assert P.get_total_multiplier(D, 'SC,PM', True, f32=False) == pytest.approx(0.5)
    assert P.get_total_multiplier(D, 'SC,PM', True, 'SCPM', f32=False) == 1.0
    assert P.get_total_multiplier({'FS': 0.4}, 'FS,NF', True, f32=False) == pytest.approx(1.4)   # 値の無いキーは数えない


def test_rating_lookup():
    assert P._rating_lookup(None) == {} and P._rating_lookup({}) == {}
    assert P._rating_lookup({'fsAccRating': 1, 'SSStars': 2.5, 'x': 'y', 'b': True}) == {'FSACCRATING': 1.0, 'SSSTARS': 2.5}


def test_effective_star_rating():
    E = P.effective_star_rating
    assert E('', 7.0) == 7.0
    assert E('FS', 7.0, f32=False) == pytest.approx(7 * 1.2)   # 値が無ければ new ModifiersMap() の既定値
    assert E('FS', 7.0, {'fs': 0.4}, f32=False) == pytest.approx(7 * 1.4)
    assert E('NF', 7.0, f32=False) == pytest.approx(3.5)
    # 速度系はレーティングの★に差し替え、倍率には入れない
    assert E('SS,GN', 7.0, None, {'ssStars': 6.0}, f32=False) == pytest.approx(6.0 * 1.04)
    assert E('BFS', 7.0, None, {'fsStars': 8.0, 'bfsStars': 9.0}) == 8.0   # 部分一致で FS（上流どおり）


# ---- PP 本体の境界（上流の PpFromScoreTests と同じ確かめ方を含む） ----

MAP = dict(predicted_acc=0.98, slider_percentage=0.0, notes=800, note_span=150)


def test_no_fail_and_bad_acc_give_zero():
    assert pp_from_acc(0.98, 8, 3, 4, modifiers='NF', **MAP)['pp'] == 0
    for acc in (0, -0.1, 1.01, math.nan):
        assert pp_from_acc(acc, 8, 3, 4, **MAP)['pp'] == 0


def test_pp_grows_with_acc_and_full_acc_is_finite():
    vals = [pp_from_acc(a / 1000, 8, 3, 4, **MAP)['pp'] for a in range(600, 1001, 5)]
    assert all(a < b for a, b in zip(vals, vals[1:])) and math.isfinite(vals[-1])


def test_pp_zero_ratings_and_broken_rating():
    r = pp_from_acc(0.95, 0, 0, 0)
    assert r['pp'] == 0 and r['bonus_pp'] == 0
    r = pp_from_acc(0.95, math.inf, 5, 3)
    assert (r['pp'], r['raw_pp'], r['bonus_pp']) == (0.0, 0.0, 0.0)
    # NaN の accRating は accPP 0 として残りを数える（上流の C# の `accRating > 0 ? … : 0` と同じ）
    assert pp_from_acc(0.95, math.nan, 5, 3)['pp'] == pytest.approx(pp_from_acc(0.95, 0, 5, 3)['pp'])


def test_pp_timing():
    # RhythmGameStandard: pp = acc * passRating * 55
    r = pp_from_acc(0.9, 10, 5, 3, timing=True)
    assert r['pp'] == pytest.approx(247.5) and r['raw_pp'] == r['pp'] and r['bonus_pp'] == 0
    assert (r['pass_pp'], r['acc_pp'], r['tech_pp']) == (0, 0, 0)
    assert pp_from_acc(0.9, 10, math.inf, 3, timing=True)['pp'] == 0


def test_parts_add_up_to_pp():
    r = pp_from_acc(0.97, 8, 3, 4, modifiers='FS,GN', **MAP)
    assert r['pass_pp'] + r['acc_pp'] + r['tech_pp'] == pytest.approx(r['pp'])


def test_speed_mods_without_rating_use_multiplier():
    # 譜面に modifiersRating が無い時は、速度系もそのまま倍率に入る（既定は RankedMap: FS 0.40, SS -0.3）
    base = pp_from_acc(0.95, 8, 3, 4, **MAP)
    fs = pp_from_acc(0.95, 8, 3, 4, modifiers='FS', **MAP)
    assert fs['multiplier'] == pytest.approx(1.4)
    assert fs['raw_pp'] == pytest.approx(base['pp']) and fs['pp'] > base['pp'] and fs['bonus_pp'] > 0
    ss = pp_from_acc(0.95, 8, 3, 4, modifiers='SS', **MAP)
    assert ss['multiplier'] == pytest.approx(0.7) and ss['pp'] < base['pp'] and ss['bonus_pp'] < 0


MR = {'fsAccRating': 9.0, 'fsPassRating': 4.0, 'fsTechRating': 5.0, 'fsPredictedAcc': 0.975, 'fsSliderPercentage': 0.1,
      'bfsAccRating': 9.5, 'bfsPassRating': 4.5, 'bfsTechRating': 5.5, 'bfsPredictedAcc': 0.97}


def test_speed_mod_uses_its_own_ratings_and_curve():
    # 上流の SpeedModifier_UsesItsOwnRatingsAndCurve_NotTheMultiplier: FS は FS のレーティングと、
    # FS の予測精度・スライダーの割合・長さ ÷ 1.2 の曲線
    r = pp_from_acc(0.97, 8, 3, 4, modifiers='FS', modifiers_rating=MR, **MAP)
    assert r['multiplier'] == 1.0
    curve = P.get_curve(0.98, 0.0, 800, 150)
    fs_curve = P.get_curve(0.975, 0.1, 800, 150 / 1.2)
    exp = P._score_pp(0.97, (8, 3, 4, curve, 0.98), 1.0, (9.0, 4.0, 5.0, fs_curve, 0.975), False)
    assert r['pp'] == pytest.approx(exp['pp']) and r['stars'] == pytest.approx(exp['stars'])
    assert r['pp'] > pp_from_acc(0.97, 8, 3, 4, **MAP)['pp']


def test_bfs_uses_its_ratings_with_the_map_curve():
    r = pp_from_acc(0.97, 8, 3, 4, modifiers='BFS', modifiers_rating=MR, **MAP)
    curve = P.get_curve(0.98, 0.0, 800, 150)
    exp = P._score_pp(0.97, (8, 3, 4, curve, 0.98), 1.0, (9.5, 4.5, 5.5, curve, 0.97), False)
    assert r['pp'] == pytest.approx(exp['pp'])


def test_scpm_context_and_golf():
    plain = pp_from_acc(0.97, 8, 3, 4, **MAP)['pp']
    assert pp_from_acc(0.97, 8, 3, 4, modifiers='SC,PM', context='scpm', **MAP)['pp'] == plain   # SC/PM を無視
    assert pp_from_acc(0.97, 8, 3, 4, modifiers='SC', **MAP)['pp'] < plain
    a1 = pp_from_acc(0.2, 8, 3, 4, context='Golf', **MAP)
    a2 = pp_from_acc(0.4, 8, 3, 4, context='Golf', **MAP)
    assert a1['acc_pp'] > 0   # Golf の accPP は精度に比例（内訳は合計に合わせて按分した値なので、按分前の値で比べる）
    assert P.get_pp(0.4, 8, 3, 4, golf=True)[1] == pytest.approx(2 * P.get_pp(0.2, 8, 3, 4, golf=True)[1])


def test_missing_curve_inputs_use_base_table():
    # 曲線の材料が無い（前の版で保存した譜面など）時は基本の表の曲線
    a = pp_from_acc(0.95, 8, 3, 4, predicted_acc=None, slider_percentage=None, notes=None, note_span=None)['pp']
    b = P._score_pp(0.95, (8, 3, 4, P.BASE_TABLE, 0.0), 1.0, None, False)['pp']
    assert a == pytest.approx(b) and a > 0


# ---- API のスコア JSON・保存の層の行から ----

def score_json(c, mode=None):
    return {'accuracy': c['acc'], 'modifiers': c['modifiers'],
            'leaderboard': {'difficulty': {'accRating': c['accRating'], 'passRating': c['passRating'], 'techRating': c['techRating'],
                                           'predictedAcc': c['predictedAcc'], 'sliderPercentage': c['sliderPercentage'],
                                           'notes': c['notes'], 'noteSpan': c['noteSpan'],
                                           'modifiersRating': c['modifiersRating'], 'modifierValues': c['modifierValues'],
                                           'modeName': mode or c['mode']}}}


def test_pp_from_score_matches_api():
    for c in CASES:
        assert abs(P.pp_from_score(score_json(c))['pp'] - c['pp']) < 0.01


def test_pp_from_score_timing_and_missing():
    c = CASES[0]
    r = P.pp_from_score(score_json(c, 'RhythmGameStandard'))
    assert r['pp'] == pytest.approx(c['acc'] * c['passRating'] * 55, rel=1e-6)
    empty = {'accuracy': None, 'modifiers': None, 'leaderboard': {'difficulty': {}}}
    assert P.pp_from_score(empty)['pp'] == 0


def test_pp_from_row_matches_api():
    # 保存の層の leaderboards の行（core/fetch/beatleader.py の norm_difficulty の列。モディファイアは JSON の文字列）
    for c in CASES[::5]:
        row = {'acc_rating': c['accRating'], 'pass_rating': c['passRating'], 'tech_rating': c['techRating'],
               'predicted_acc': c['predictedAcc'], 'slider_percentage': c['sliderPercentage'], 'notes': c['notes'],
               'note_span': c['noteSpan'], 'mode': c['mode'],
               'modifiers_rating': json.dumps(c['modifiersRating']) if c['modifiersRating'] else None,
               'modifier_values': json.dumps(c['modifierValues']) if c['modifierValues'] else None}
        assert abs(P.pp_from_row(c['acc'], row, c['modifiers'])['pp'] - c['pp']) < 0.01
    broken = {'acc_rating': 8, 'pass_rating': 3, 'tech_rating': 4, 'predicted_acc': None, 'slider_percentage': None,
              'notes': None, 'note_span': None, 'mode': None, 'modifiers_rating': '{bad', 'modifier_values': ''}
    assert P.pp_from_row(0.95, broken)['pp'] > 0   # 壊れた JSON は無いものとして扱う


def _row(c, mode=None):
    return {'acc_rating': c['accRating'], 'pass_rating': c['passRating'], 'tech_rating': c['techRating'],
            'predicted_acc': c['predictedAcc'], 'slider_percentage': c['sliderPercentage'], 'notes': c['notes'],
            'note_span': c['noteSpan'], 'mode': mode or c['mode'],
            'modifiers_rating': json.dumps(c['modifiersRating']) if c['modifiersRating'] else None,
            'modifier_values': json.dumps(c['modifierValues']) if c['modifierValues'] else None}


@pytest.mark.parametrize('acc', [0.0, -0.1, 0.5, 0.8, 0.9, 0.95, 0.96, 0.98, 0.99, 0.9999, 1.0, 1.01, float('nan')])
def test_pp_func_of_row_matches_pp_from_row(acc):
    # 「精度 → PP」の関数（精度に依らない所を先に求める。recommend.py の推定の幅で何度も呼ぶ）は、モディファイア無しの
    # pp_from_row と同じ値（低い★の上乗せ・曲線の乗数・曲線の材料が無い譜面・リズムゲームの譜面も）
    rows = [_row(c) for c in CASES[::3]] + [_row(CASES[0], 'RhythmGameStandard')]
    rows.append({'acc_rating': 8, 'pass_rating': 3, 'tech_rating': 4, 'predicted_acc': None, 'slider_percentage': None,
                 'notes': None, 'note_span': None, 'mode': None, 'modifiers_rating': None, 'modifier_values': None})
    rows.append({'acc_rating': None, 'pass_rating': None, 'tech_rating': None, 'predicted_acc': 0.98, 'slider_percentage': 0.1,
                 'notes': 300, 'note_span': 120, 'mode': 'Standard', 'modifiers_rating': None, 'modifier_values': None})
    for row in rows:
        assert P.pp_func_of_row(row)(acc) == P.pp_from_row(acc, row)['pp']


# ---- 総 PP ----

def test_total_pp_weights():
    assert total_pp([]) == 0
    assert abs(total_pp([100, 200, 0]) - (200 + 100 * 0.965)) < 1e-3
    parts = total_pp_parts([{'pp': 100, 'acc_pp': 60, 'pass_pp': 30, 'tech_pp': 10},
                            {'pp': 200, 'acc_pp': 150, 'pass_pp': 30, 'tech_pp': 20}])
    assert abs(parts['acc_pp'] - (150 + 60 * 0.965)) < 1e-3


def test_weight():
    assert P.weight(0) == 1.0 and P.weight(1) == pytest.approx(0.965, abs=1e-7)
    assert P.weight(3, f32=False) == 0.965 ** 3


def test_total_pp_order_and_zeros():
    exp = 300 + 200 * 0.965 + 100 * 0.965 ** 2
    assert total_pp([100, None, 300, 0, 200], f32=False) == pytest.approx(exp)
    assert total_pp([300, 200, 100]) == pytest.approx(exp, abs=1e-3)


def test_total_pp_parts_weights_by_pp():
    # 重みは pp の降順で決める（内訳の大きさの順ではない）。pp が 0・無い行は数えない。内訳が無ければ 0
    rows = [{'pp': 100, 'acc_pp': 90}, {'pp': 200, 'acc_pp': 10, 'pass_pp': 5}, {'pp': 0, 'acc_pp': 999}, {'acc_pp': 999}]
    out = total_pp_parts(rows, f32=False)
    assert out['pp'] == pytest.approx(200 + 100 * 0.965)
    assert out['acc_pp'] == pytest.approx(10 + 90 * 0.965)
    assert out['pass_pp'] == pytest.approx(5) and out['tech_pp'] == 0


# ---- 単精度の再現・計算の補助（モディファイアの倍率・総PP 用） ----

def test_f32_edges():
    assert P._f32(1e39) == math.inf and P._f32(-1e39) == -math.inf   # 単精度の範囲外は ±Infinity
    assert P._f32(math.inf) == math.inf and math.isnan(P._f32(math.nan))
    assert P._f32(0.1) != 0.1 and abs(P._f32(0.1) - 0.1) < 1e-8


def test_pow_exp_edges():
    assert math.isnan(P._pow(-8, 1 / 3))   # 定義域外は NaN（例外・複素数にしない）
    assert P._pow(10, 400) == math.inf and P._exp(1000) == math.inf
    assert P._pow(2, 10) == 1024


def test_module_demo_runs(capsys):
    runpy.run_path(P.__file__, run_name='__main__')
    assert 'total_pp([300, 250, 200])' in capsys.readouterr().out
