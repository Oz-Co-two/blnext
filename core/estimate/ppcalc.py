"""BeatLeader の PP 計算式の Python 移植（標準ライブラリのみ・Python 3.14）。

移植元（GPLv3 のアプリに組み込む前提で移植）: PPv4.1（2026-11-06 に本番に入った。https://beatleader.wiki/en/ranking/update4-1 ）
  BeatLeader/RatingAPI @ 48cc07c61dd57a99d872c20b3df9085ffe9ba494 (2026-11-05)
    Pp/PpFormula.cs .................. pass / acc / tech の式・★・★の低い譜面の上乗せ・★の中ほどの譜面の上乗せ（mid star top）・pass の精度の倍率
    Pp/PpCalculator.cs ............... GetPp / ToStars / ScorePp / CurveValue
    Controllers/Curve.cs ............. Curve.GetCurve（譜面ごとの精度の曲線）・BaseTable・CurveSettings
  同じ式を BeatLeader の wiki が公開している: https://beatleader.wiki/data/ppv41-formula.js
    （サイトの src/utils/beatleader/rebalanced-pp.js の写し。**beatleader-server の rebalanced-pp ブランチは 10/26 のまま更新されていないので、そこを見ても分からない**）
  モディファイア・総PP（PPv4 から変わっていない）: BeatLeader/beatleader-server rebalanced-pp @ 52a50edfd172c37bab4c5d278b10aae47ccbc8f6
    Utils/ReplayUtils.cs
      GetCurveInputs / SpeedCurveInputs（SS/FS/SF は自分のレーティング・予測精度と、長さを 0.85/1.2/1.5 で割った曲線）
      EffectiveStarRating / PpFromScore / GetTotalMultiplier（モディファイアの倍率）
    Extensions/ObjectToDictionaryHelper.cs
      ToDictionary<T> ................ float 型のプロパティだけを辞書にする
    Utils/PlayerUtils.cs
      RecalculatePPAndRankFastGeneral  総PP = Σ pp_i * 0.965^i、pp の降順
  BeatLeader/beatleader-server-models @ 3aa8d54f4df1cd0d13d3f26e97f9486414fbc4f8（サブモジュール）
    Models/Modifiers.cs .............. ModifiersRating（*SliderPercentage が増えた）・ModifiersMap の既定値・RankedMap()

上流の調整が入ったら（wiki の ppv41-formula.js・RatingAPI を見る）: 定数（FORMULA・CURVE_*）と表を合わせ、UPSTREAM_COMMIT を変えて tools/verify_pp.py で照合する。
UPSTREAM_COMMIT が変わると、次の取得で譜面・自分のスコア・比較対象のスコアを全部取り直す（core/fetch/refresh.py。古い式・古い★のデータを混ぜないため）。

上流は曲線を作る所を倍精度、式の所を単精度（float）で計算している。PP はここでは倍精度だけで計算する。
モディファイアの倍率と総PP は前の版から式が変わっていないので、単精度の再現（f32=True）を残している。
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache

UPSTREAM_REPO = "BeatLeader/RatingAPI"
UPSTREAM_BRANCH = "master"
UPSTREAM_COMMIT = "48cc07c61dd57a99d872c20b3df9085ffe9ba494"

# ---------------------------------------------------------------------------
# 単精度（C# float）の再現（モディファイアの倍率・総PP 用）
# ---------------------------------------------------------------------------
_F32 = struct.Struct("<f")


def _f32(x: float) -> float:
    """倍精度の値を単精度に丸める（C# の (float) キャスト・float 演算の結果に相当）。"""
    if math.isnan(x) or math.isinf(x):
        return x
    try:
        return _F32.unpack(_F32.pack(x))[0]
    except OverflowError:  # 単精度の範囲外は C# では ±Infinity になる
        return math.inf if x > 0 else -math.inf


def _same(x: float) -> float:
    return x


def _pow(x: float, y: float) -> float:
    """MathF.Pow 相当。定義域外は NaN、あふれは Infinity（Python の例外・複素数を避ける）。"""
    try:
        r = math.pow(x, y)
    except ValueError:
        return math.nan
    except OverflowError:
        return math.inf
    return r


def _exp(x: float) -> float:
    """MathF.Exp 相当。あふれは Infinity。"""
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf


def _bad(x: float) -> bool:
    return math.isnan(x) or math.isinf(x)


# ---------------------------------------------------------------------------
# 基本の曲線（Curve.BaseTable）: (精度, 倍率)。精度の降順。PPv4.1 で 100% が下がり、95% より下が下がった
# ---------------------------------------------------------------------------
BASE_TABLE: tuple[tuple[float, float], ...] = (
    (1.0, 7.2),
    (0.999, 6.241),
    (0.9975, 5.158),
    (0.995, 4.010),
    (0.9925, 3.241),
    (0.99, 2.700),
    (0.9875, 2.303),
    (0.985, 2.007),
    (0.9825, 1.786),
    (0.98, 1.618),
    (0.9775, 1.490),
    (0.975, 1.392),
    (0.9725, 1.315),
    (0.97, 1.256),
    (0.965, 1.167),
    (0.96, 1.094),
    (0.955, 1.039),
    (0.95, 1.000),
    (0.94, 0.926),
    (0.93, 0.861),
    (0.92, 0.804),
    (0.91, 0.760),
    (0.9, 0.727),
    (0.875, 0.675),
    (0.85, 0.627),
    (0.825, 0.584),
    (0.8, 0.549),
    (0.75, 0.469),
    (0.7, 0.400),
    (0.65, 0.343),
    (0.6, 0.297),
    (0.0, 0.000),
)

STAR_ACCURACY = 0.96   # ★は精度 96% の時の PP から決める（PpCalculator.StarAccuracy）


def curve_value(acc: float, curve: tuple[tuple[float, float], ...] = BASE_TABLE) -> float:
    """PpCalculator.CurveValue / BaseValue: 精度の降順の点の並びを線形に補間する。

    範囲の外は端の区間を延ばした値（上流どおり。PP の計算では 0 以下の精度は先に弾く）。
    """
    n = len(curve)
    lo, hi = 0, n
    while lo < hi:   # 精度が acc 以下になる最初の点（並びは降順）
        mid = (lo + hi) >> 1
        if curve[mid][0] <= acc:
            hi = mid
        else:
            lo = mid + 1
    i = min(max(lo, 1), n - 1)
    x0, y0 = curve[i - 1]
    x1, y1 = curve[i]
    return y0 + (acc - x0) / (x1 - x0) * (y1 - y0)


def _smooth_table() -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Curve.Smooth: 基本の表を通る単調な3次エルミート補間の傾き（精度の昇順）。"""
    pts = sorted(BASE_TABLE)
    xs = tuple(p[0] for p in pts)
    ys = tuple(p[1] for p in pts)
    n = len(pts)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    slopes = [0.0] * n
    slopes[0] = d[0]
    slopes[n - 1] = d[n - 2]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            continue
        w1 = 2 * h[i] + h[i - 1]
        w2 = h[i] + 2 * h[i - 1]
        slopes[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    return xs, ys, tuple(slopes)


_SMOOTH = _smooth_table()


def smooth_base_value(acc: float) -> float:
    """Curve.SmoothBaseValue: 基本の表をなめらかにつないだ値。"""
    xs, ys, sl = _SMOOTH
    if acc <= xs[0]:
        return ys[0]
    if acc >= xs[-1]:
        return ys[-1]
    i = 0
    while xs[i + 1] < acc:
        i += 1
    h = xs[i + 1] - xs[i]
    t = (acc - xs[i]) / h
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * sl[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * sl[i + 1])


class ModifiableCurve:
    """Curve.ModifiableCurve: 形を変えられる曲線（60%〜95% は F1、95%〜A は F2、A より上は L）。"""

    def __init__(self, a: float = 1.0, c: float = 1.0, k: float = 4.86, w: float = 0.6,
                 k2: float = 2.88, p2: float = 2.46, y0: float = 0.256):
        self.a, self.c, self.k, self.w, self.k2, self.p2, self.y0 = a, c, k, w, k2, p2, y0

    def value(self, x: float) -> float:
        if x <= 0.60:
            return (self.y0 / 0.60) * x
        if x <= 0.95:
            return self.f1(x)
        if x <= self.a:
            return self.f2(x)
        return self.l(x)

    def m(self) -> float:
        t1 = (1.0 - self.y0) / 0.35
        t2 = (1.0 - self.w) + self.w * (self.k * math.exp(self.k)) / (math.exp(self.k) - 1.0)
        return t1 * t2

    def f1(self, x: float) -> float:
        u = (x - 0.6) / 0.35
        blend = (1.0 - self.w) * u + self.w * (math.exp(self.k * u) - 1.0) / (math.exp(self.k) - 1.0)
        return self.y0 + (1.0 - self.y0) * blend

    def f2(self, x: float) -> float:
        m = self.m()
        u2 = (x - 0.95) / 0.05
        e = (math.exp(self.k2 * u2 ** self.p2) - 1.0) / (math.exp(self.k2) - 1.0)
        return 1.0 + m * (x - 0.95) + (6.424 - 0.05 * m) * e

    def s(self) -> float:
        m = self.m()
        ua = (self.a - 0.95) / 0.05
        up = ua ** self.p2 if ua > 0 else 0.0
        up1 = ua ** (self.p2 - 1.0) if ua > 0 else 0.0
        e = math.exp(self.k2 * up) / (math.exp(self.k2) - 1.0)
        return m + (6.424 - 0.05 * m) * e * (self.k2 * self.p2 / 0.05) * up1

    def l(self, x: float) -> float:
        s = self.s()
        s1 = s * (1.0 - self.c)
        f2a = self.f2(self.a)
        if abs(self.a - 1.0) < 1e-9:
            return f2a + s1 * (x - self.a)
        e = 1.0 - math.exp(-(x - self.a) / (1.0 - self.a))
        return f2a + s1 * (x - self.a) + (s - s1) * (1.0 - self.a) * e


# CurveSettings.Table: 予測精度ごとの曲線の形 (PredictedAcc, A, C, K, W, K2, P2)。間は線形に補間する
CURVE_TABLE: tuple[tuple[float, float, float, float, float, float, float], ...] = (
    (0.955, 1.0, -5.30, 4.86, 0.60, 2.88, 2.46),
    (0.960, 0.99, -3.00, 4.30, 0.66, 4.80, 1.85),
    (0.965, 0.988, -2.40, 4.50, 0.64, 4.50, 1.95),
    (0.970, 0.986, -2.20, 4.70, 0.62, 4.20, 2.05),
    (0.975, 0.983, 0.0, 4.86, 0.60, 3.80, 2.15),
    (0.980, 0.983, 0.0, 4.86, 0.60, 3.35, 2.30),
    (0.985, 0.985, -0.60, 4.86, 0.60, 2.88, 2.46),
    (0.990, 0.99, 0.0, 4.86, 0.60, 2.88, 2.46),
    (0.995, 0.99, 0.0, 4.86, 0.60, 2.88, 2.46),
)
CURVE_ENABLED = True
CURVE_MAX_C = 1.0
CURVE_SLIDER_TILT = 0.2          # スライダーの割合の多い譜面は、予測精度より上を上げ・下を下げる
CURVE_SLIDER_TILT_RANGE = 0.015
CURVE_LOW_NOTE = (115, 20, 0.8031, 0.8031)   # ノーツが少ない譜面: (始まり, 基準のノーツ数, 基準の倍率, 下限の倍率)
CURVE_SHORT = (75, 12, 0.9, 0.9)         # 短い譜面（秒）: (始まり, 基準の長さ, 基準の倍率, 下限の倍率)


def _log_nerf(value: float, start: float, ref: float, ref_mult: float, min_mult: float) -> float:
    if start <= 0 or value <= 0 or value >= start or ref >= start:
        return 1.0
    nerf = (1 - ref_mult) * math.log(start / value) / math.log(start / ref)
    return min(max(1 - nerf, min_mult), 1.0)


def low_note_multiplier(notes: float, span: float = 0) -> float:
    """CurveSettings.LowNoteMultiplier: ノーツが少ない（最大 ×0.8031）・短い（最大 ×0.90）譜面の曲線全体の下げ。"""
    return _log_nerf(notes, *CURVE_LOW_NOTE) * _log_nerf(span, *CURVE_SHORT)


def _table_factor(standard: ModifiableCurve, x: float) -> float:
    """Curve の TableFactor: なめらかな基本の曲線 ÷ 標準の曲線。"""
    ref = standard.value(x)
    return smooth_base_value(x) / ref if ref > 0 else 0.0


def _top_table_factor(standard: ModifiableCurve, a: float, x: float) -> float:
    """Curve の TopTableFactor: A より上は、A での傾きで TableFactor を延ばす（PPv4.1 で 100% の手前の急な跳ねをなくした）。"""
    h = 1e-5
    at_a = _table_factor(standard, a)
    return at_a + (at_a - _table_factor(standard, a - h)) / h * (x - a)


def _per_map_value(modified: ModifiableCurve, standard: ModifiableCurve, x: float) -> float:
    """Curve.PerMapValue: 形を変えた曲線 × TableFactor。なめらかな基本の曲線より上にはしない。"""
    base = smooth_base_value(x)
    factor = _top_table_factor(standard, modified.a, x) if modified.a < 0.9995 and x > modified.a else _table_factor(standard, x)
    v = modified.value(x) * factor if factor > 0 else base
    return min(base, v)


def _params_at(predicted_acc: float) -> tuple[float, float, float, float, float, float]:
    """CurveSettings.ParamsAt: 予測精度での (A, C, K, W, K2, P2)。表の外は端の行。C は CURVE_MAX_C まで。"""
    rows = CURVE_TABLE
    if predicted_acc <= rows[0][0]:
        r = rows[0][1:]
    else:
        r = rows[-1][1:]
        for i in range(1, len(rows)):
            if predicted_acc <= rows[i][0]:
                p, n = rows[i - 1], rows[i]
                t = (predicted_acc - p[0]) / (n[0] - p[0])
                r = tuple(p[k] + t * (n[k] - p[k]) for k in range(1, 7))
                break
    a, c, k, w, k2, p2 = r
    return a, min(c, CURVE_MAX_C), k, w, k2, p2


@lru_cache(maxsize=8192)
def get_curve(predicted_acc: float, slider_percentage: float = 0.0, notes: int = 0,
              note_span: float = 0.0) -> tuple[tuple[float, float], ...]:
    """Curve.GetCurve: 譜面ごとの精度の曲線（精度の降順の点の並び）。予測精度が 60% 以下・99.95% 以上なら基本の表。"""
    nerf = low_note_multiplier(notes, note_span)
    curves = None
    if CURVE_ENABLED and 0.6 < predicted_acc < 0.9995:
        a, c, k, w, k2, p2 = _params_at(predicted_acc)
        if not (a >= 0.9995 and k == 4.86 and w == 0.6 and k2 == 2.88 and p2 == 2.46):   # CurveRow.IsDefault
            curves = (ModifiableCurve(a=min(max(a, 0.951), 1.0), c=c, k=k, w=w, k2=k2, p2=p2), ModifiableCurve())
    tilt = 0.0
    if CURVE_ENABLED and CURVE_SLIDER_TILT != 0 and 0.6 < predicted_acc < 0.9995:
        tilt = CURVE_SLIDER_TILT * slider_percentage
    if curves is None and tilt == 0:
        return tuple((x, y * nerf) for x, y in BASE_TABLE)
    top = curves[0].a if curves else 1.0
    xs = ([x for x, _ in BASE_TABLE] + [0.6 + i * 0.005 for i in range(71)]
          + [0.95 + i * 0.0005 for i in range(101)] + [top])
    xs = sorted({round(x, 6) for x in xs if 0 <= round(x, 6) <= 1}, reverse=True)

    def raw(x: float) -> float:
        if curves is None:
            v = curve_value(x)
        else:
            v = _per_map_value(curves[0], curves[1], x)
        if tilt != 0:
            dist = min(max(x - predicted_acc, -CURVE_SLIDER_TILT_RANGE), CURVE_SLIDER_TILT_RANGE)
            v *= math.exp(tilt * dist * 100)
        return v

    return tuple((x, raw(x) * nerf) for x in xs)


def _curve_of(predicted_acc, slider_percentage, notes, note_span) -> tuple[tuple[float, float], ...]:
    """欠けた値（None）は 0 として曲線を作る（上流の GetCurveInputs と同じ）。"""
    return get_curve(float(predicted_acc or 0.0), float(slider_percentage or 0.0), int(notes or 0), float(note_span or 0.0))


# ---------------------------------------------------------------------------
# 式（PpFormula）
# ---------------------------------------------------------------------------
FORMULA: dict[str, float] = {
    "PassScale": 12.3, "PassExponent": 2.62, "PassOffset": 30, "PassInner": 0.85966,
    "AccWeight": 34.519, "AccCurvePower": 0.9,
    "LowStarCurvePower": 1.0, "LowStarCurveStart": 7.1529, "LowStarCurveEnd": 9.6894,
    "VeryLowStarCurvePower": 1.08, "VeryLowStarCurveStart": 3.478, "VeryLowStarCurveEnd": 5.3718,
    "TechWeight": 5.7887, "TechAccExponent": 0,
    "StarDivisor": 52, "PpMultiplier": 1.0128,
    "LowStarBonus": 0.007593, "LowStarBonusEnd": 8.8584, "LowStarBonusPower": 1.98,
    "MidStarTopBonus": 0.05, "MidStarTopStart": 4.2508, "MidStarTopFull": 6.3762, "MidStarTopFullEnd": 8.0, "MidStarTopEnd": 10.5,
    "MidStarTopAccBelow": 0.03, "MidStarTopAccAbove": 0,
    "PassTopKnee": 13, "PassTopSlope": 0.5, "TechTopKnee": 6.2754, "TechTopSlope": 0.7,
    "PassAccExponent": 6, "PassAccMaxMultiplier": 3, "PassAccMinMultiplier": 0.8, "PassAccErrorOffset": 1,
    "GolfAccWeight": 38,
}


def pass_pp(pass_rating: float) -> float:
    """PpFormula.PassPp: 12.3·e^(0.85966·p^(1/2.62)) − 30（13 より上は傾きを半分に。負・NaN・∞ は 0）。"""
    f = FORMULA
    p = pass_rating
    if f["PassTopSlope"] != 1 and p > f["PassTopKnee"]:
        p = f["PassTopKnee"] + (p - f["PassTopKnee"]) * f["PassTopSlope"]
    v = f["PassScale"] * _exp(f["PassInner"] * _pow(p, 1 / f["PassExponent"])) - f["PassOffset"]
    return 0.0 if _bad(v) or v < 0 else v


def pass_acc_multiplier(accuracy: float, predicted_acc: float) -> float:
    """PpFormula.PassAccMultiplier: pass の PP に掛ける倍率。予測精度 − (1 − 予測精度) で 1 倍、e^(6·差)、0.8〜3 倍（PPv4.1）。予測精度が無ければ 1。"""
    f = FORMULA
    if f["PassAccExponent"] == 0 or not predicted_acc > 0:
        return 1.0
    gap = accuracy - (predicted_acc - f["PassAccErrorOffset"] * (1 - predicted_acc))
    lo = f["PassAccMinMultiplier"] if f["PassAccMinMultiplier"] > 0 else 1 / f["PassAccMaxMultiplier"]
    return min(max(_exp(f["PassAccExponent"] * gap), lo), f["PassAccMaxMultiplier"])


def _ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _mid_star_top_bump(stars: float) -> float:
    f = FORMULA
    if not f["MidStarTopStart"] < stars < f["MidStarTopEnd"]:
        return 0.0
    if stars < f["MidStarTopFull"]:
        return _ease((stars - f["MidStarTopStart"]) / (f["MidStarTopFull"] - f["MidStarTopStart"]))
    if stars > f["MidStarTopFullEnd"]:
        return _ease((f["MidStarTopEnd"] - stars) / (f["MidStarTopEnd"] - f["MidStarTopFullEnd"]))
    return 1.0


def mid_star_top_multiplier(stars: float, accuracy: float, predicted_acc: float) -> float:
    """PpFormula.MidStarTopMultiplier: ★の中ほどの譜面の acc の PP の上乗せ（最大 +5%。予測精度 − 3% から予測精度にかけて。PPv4.1）。★には入らない。"""
    f = FORMULA
    if f["MidStarTopBonus"] == 0 or not predicted_acc > 0:
        return 1.0
    start = predicted_acc - f["MidStarTopAccBelow"]
    if not accuracy > start:
        return 1.0
    bump = _mid_star_top_bump(stars)
    if bump == 0:
        return 1.0
    return 1 + f["MidStarTopBonus"] * bump * _ease((accuracy - start) / (f["MidStarTopAccBelow"] + f["MidStarTopAccAbove"]))


def curve_power_at(stars: float) -> float:
    """PpFormula.CurvePowerAt: ★に応じた曲線の乗数（★の低い譜面ほど曲線を急に）。"""
    f = FORMULA
    if f["VeryLowStarCurvePower"] != f["LowStarCurvePower"] and stars < f["VeryLowStarCurveEnd"]:
        if stars <= f["VeryLowStarCurveStart"]:
            return f["VeryLowStarCurvePower"]
        return f["VeryLowStarCurvePower"] + (f["LowStarCurvePower"] - f["VeryLowStarCurvePower"]) * \
            (stars - f["VeryLowStarCurveStart"]) / (f["VeryLowStarCurveEnd"] - f["VeryLowStarCurveStart"])
    if f["LowStarCurvePower"] == f["AccCurvePower"] or not stars < f["LowStarCurveEnd"]:
        return f["AccCurvePower"]
    if stars <= f["LowStarCurveStart"]:
        return f["LowStarCurvePower"]
    return f["LowStarCurvePower"] + (f["AccCurvePower"] - f["LowStarCurvePower"]) * \
        (stars - f["LowStarCurveStart"]) / (f["LowStarCurveEnd"] - f["LowStarCurveStart"])


def acc_curve(value: float, at96: float, power: float) -> float:
    """PpFormula.AccCurve: 曲線の値を 96% の値を基準に power 乗する。"""
    if power == 1 or not value > 0 or not at96 > 0:
        return value
    return at96 * (value / at96) ** power


def acc_pp(value: float, acc_rating: float) -> float:
    """PpFormula.AccPp: 曲線の値 × 34.519 × accRating（PPv4.1 で accRating に比例）。"""
    return value * FORMULA["AccWeight"] * acc_rating if acc_rating > 0 else 0.0


def tech_pp(accuracy: float, tech_rating: float) -> float:
    """PpFormula.TechPp: 5.7887 × tech（6.2754 より上は傾きを 0.7 に）。PPv4.1 で精度に依らない（TechAccExponent 0）。"""
    f = FORMULA
    if not tech_rating > 0:
        return 0.0
    t = tech_rating
    if t > f["TechTopKnee"]:
        t = f["TechTopKnee"] + (t - f["TechTopKnee"]) * f["TechTopSlope"]
    return _exp(f["TechAccExponent"] * accuracy) * f["TechWeight"] * t


def _stars(pp96: float) -> float:
    return pp96 / FORMULA["StarDivisor"]


def low_star_multiplier(stars: float) -> float:
    """PpFormula.LowStarMultiplier: ★8.86 より下の譜面の PP の上乗せ。"""
    f = FORMULA
    if f["LowStarBonus"] <= 0 or math.isnan(stars) or stars >= f["LowStarBonusEnd"]:
        return 1.0
    return 1 + f["LowStarBonus"] * (f["LowStarBonusEnd"] - max(stars, 0)) ** f["LowStarBonusPower"]


def stars_from_pp(pp96: float) -> float:
    """PpFormula.StarsFromPp: 96% の PP（上乗せ前）→ ★。★の低い所は上乗せ込みで二分法で解く。"""
    f = FORMULA
    s = _stars(pp96)
    if not pp96 > 0 or f["LowStarBonus"] <= 0 or s >= f["LowStarBonusEnd"]:
        return s
    lo, hi = s, f["LowStarBonusEnd"]
    while hi - lo > 1e-6:
        mid = (lo + hi) / 2
        if mid < _stars(pp96 * low_star_multiplier(mid)):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def get_pp(accuracy: float, acc_rating: float, pass_rating: float, tech_rating: float,
           curve: tuple[tuple[float, float], ...] = BASE_TABLE, power: float | None = None,
           at96: float | None = None, golf: bool = False, predicted_acc: float = 0.0) -> tuple[float, float, float]:
    """PpCalculator.GetPp。戻り値は (passPP, accPP, techPP)。★の中ほどの上乗せ、合計に ×1.0128 と★の低い譜面の上乗せを掛ける前の値。

    power は曲線の乗数（省くと AccCurvePower）、at96 は曲線の 96% の値（省くとその場で求める）、predicted_acc は pass の倍率に使う予測精度（0 で効かない）。
    """
    if power is None:
        power = FORMULA["AccCurvePower"]
    if golf:
        a = accuracy * acc_rating * FORMULA["GolfAccWeight"]
    else:
        v = curve_value(accuracy, curve)
        if power != 1:
            v = acc_curve(v, curve_value(STAR_ACCURACY, curve) if at96 is None else at96, power)
        a = acc_pp(v, acc_rating)
    return pass_pp(pass_rating) * pass_acc_multiplier(accuracy, predicted_acc), a, tech_pp(accuracy, tech_rating)


def to_stars(acc_rating: float, pass_rating: float, tech_rating: float,
             curve: tuple[tuple[float, float], ...] = BASE_TABLE, predicted_acc: float = 0.0) -> float:
    """PpCalculator.ToStars: 精度 96% の PP から★（NaN・∞ は 0）。曲線は譜面ごとの物を渡す。PPv4.1 から pass の倍率のため予測精度も使う。"""
    p, a, t = get_pp(STAR_ACCURACY, acc_rating, pass_rating, tech_rating, curve, predicted_acc=predicted_acc)
    r = stars_from_pp(p + a + t)
    return 0.0 if _bad(r) else r


def map_stars(acc_rating: float, pass_rating: float, tech_rating: float, *, predicted_acc=0.0,
              slider_percentage=0.0, notes=0, note_span=0.0) -> float:
    """譜面のレーティングと曲線の材料から、その譜面の★（API の difficulty.stars と同じ値）。"""
    return to_stars(acc_rating, pass_rating, tech_rating, _curve_of(predicted_acc, slider_percentage, notes, note_span), float(predicted_acc or 0.0))


# ---------------------------------------------------------------------------
# モディファイア（Models/Modifiers.cs。リウェイトでも変わっていない）
# ---------------------------------------------------------------------------
# ModifiersMap の float プロパティ（宣言順）。ToDictionary<float> はこの順で回る。
# ModifierId は int なので ToDictionary<float> に入らない。EZ/HD/SMC/OHP は [NotMapped] だがプロパティなので入る。
MODIFIER_MAP_KEYS: tuple[str, ...] = (
    "DA", "FS", "SF", "SS", "GN", "NA", "NB", "NF", "NO", "PM", "SC", "SA", "OP",
    "EZ", "HD", "SMC", "OHP",
)

# new ModifiersMap() の既定値（Models/Modifiers.cs）
DEFAULT_MODIFIER_VALUES: dict[str, float] = {
    "DA": 0.0, "FS": 0.20, "SF": 0.36, "SS": -0.3, "GN": 0.04, "NA": -0.3, "NB": -0.2,
    "NF": -0.5, "NO": -0.2, "PM": 0.0, "SC": -0.5, "SA": 0.0, "OP": -0.5,
    "EZ": -0.4, "HD": 0.05, "SMC": 0.07, "OHP": 0.0,
}

# ModifiersMap.RankedMap()。ランク譜面の modifierValues は通常これ（EZ/HD/SMC/OHP はクラスの既定値のまま）
RANKED_MODIFIER_VALUES: dict[str, float] = {
    **DEFAULT_MODIFIER_VALUES,
    "DA": 0.0, "FS": 0.40, "SF": 0.72, "SS": -0.3, "GN": 0.0, "NA": -0.3, "NB": -0.2,
    "NF": -1.0, "NO": -0.2, "PM": 0.0, "SC": -0.5, "SA": 0.0, "OP": -0.5,
}

# ModifiersRating で差し替えができる速度系モディファイア
SPEED_RATING_MODIFIERS: tuple[str, ...] = ("SS", "FS", "SF", "BFS", "BSF")

# ReplayUtils.SpeedCurveInputs: 速度系モディファイアで曲線の長さ（noteSpan）を割る値。BFS/BSF は譜面の曲線のまま
SPEED_SPAN_DIVISORS: dict[str, float] = {"SS": 0.85, "FS": 1.2, "SF": 1.5}


def normalize_modifier_values(
    values: Mapping[str, object] | None,
    base: Mapping[str, float] = DEFAULT_MODIFIER_VALUES,
) -> dict[str, float]:
    """API の modifierValues（{"fs": 0.4, ...}）や C# 風のキーを {"FS": 0.4, ...} にそろえる。

    値が無いキーは base（既定は new ModifiersMap() の既定値）で埋める。values が None の時は base そのもの
    （上流の EffectiveStarRating / グラフ API の `?? new ModifiersMap()` に相当）。
    """
    out = dict(base)
    if values:
        upper = {str(k).upper(): v for k, v in values.items()}
        for k in MODIFIER_MAP_KEYS:
            v = upper.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = float(v)
    return out


def _rating_lookup(modifiers_rating: Mapping[str, object] | None) -> dict[str, float]:
    """ModifiersRating を {"FSACCRATING": 値, ...}（大文字キー）にそろえる。API の "fsAccRating" も C# の "FSAccRating" も可。"""
    if not modifiers_rating:
        return {}
    out: dict[str, float] = {}
    for k, v in modifiers_rating.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[str(k).upper()] = float(v)
    return out


def get_total_multiplier(
    modifier_values: Mapping[str, float],
    modifiers: str,
    speed_modifiers: bool,
    context: str = "general",
    *,
    f32: bool = True,
) -> float:
    """ReplayUtils.GetTotalMultiplier。

    1 + Σ(該当モディファイアの値)。判定は文字列の部分一致（modifiers.Contains(key)）。
    speed_modifiers=False（= 譜面に modifiersRating がある）なら SF/SS/FS は倍率に入れない
    （代わりに PpFromScore でレーティングと曲線ごと差し替える）。SCPM コンテキストでは SC/PM を無視。
    """
    r = _f32 if f32 else _same
    multiplier = 1.0
    ctx = context.lower()
    for key in MODIFIER_MAP_KEYS:
        if key not in modifier_values:
            continue
        if not speed_modifiers and key in ("SF", "SS", "FS"):
            continue
        if ctx == "scpm" and key in ("SC", "PM"):
            continue
        if key in modifiers:  # C# の string.Contains（部分一致・大文字小文字を区別）
            multiplier = r(multiplier + r(modifier_values[key]))
    return multiplier


def effective_star_rating(
    modifiers: str,
    base_stars: float,
    modifier_values: Mapping[str, object] | None = None,
    modifiers_rating: Mapping[str, object] | None = None,
    *,
    f32: bool = True,
) -> float:
    """ReplayUtils.EffectiveStarRating: モディファイア込みの★（score.ModifiedStars）。"""
    r = _f32 if f32 else _same
    mv = normalize_modifier_values(modifier_values)
    mp = get_total_multiplier(mv, modifiers, modifiers_rating is None, "general", f32=f32)
    if modifiers_rating is not None:
        mr = _rating_lookup(modifiers_rating)
        # 上流は SS → FS → SF の順に部分一致で判定（BFS は "FS"、BSF は "SF" に当たる点も上流どおり）
        for key in ("SS", "FS", "SF"):
            if key in modifiers:
                base_stars = mr.get(key + "STARS", 0.0)
                break
    return r(base_stars * mp)


# ---------------------------------------------------------------------------
# PP 本体
# ---------------------------------------------------------------------------
def _score_pp(accuracy: float, ratings: tuple, mp: float, speed: tuple | None, golf: bool) -> dict[str, float]:
    """PpCalculator.ScorePp（NF・精度の範囲は呼ぶ側で見る）。ratings / speed は (acc, pass, tech, 曲線, 予測精度)。"""
    f = FORMULA
    ar, pr, tr, curve, pa = ratings
    stars = to_stars(ar, pr, tr, curve, pa)
    at96 = curve_value(STAR_ACCURACY, curve)
    p, a, t = get_pp(accuracy, ar, pr, tr, curve, curve_power_at(stars), at96, golf, pa)
    a *= mid_star_top_multiplier(stars, accuracy, pa)
    raw = f["PpMultiplier"] * (p + a + t) * low_star_multiplier(stars)

    if speed is not None:
        ar, pr, tr, curve, pa = speed
        stars = to_stars(ar, pr, tr, curve, pa)
        at96 = curve_value(STAR_ACCURACY, curve)
    p, a, t = get_pp(accuracy, ar * mp, pr * mp, tr * mp, curve, curve_power_at(stars), at96, golf, pa)
    a *= mid_star_top_multiplier(stars, accuracy, pa)
    s = p + a + t
    full = f["PpMultiplier"] * s * low_star_multiplier(stars)
    increase = full / s if s > 0 else 0.0
    if _bad(raw):
        raw = 0.0
    if _bad(full):
        full = 0.0
    return {"pp": full, "bonus_pp": full - raw, "pass_pp": p * increase, "acc_pp": a * increase,
            "tech_pp": t * increase, "raw_pp": raw, "stars": stars}


def pp_from_acc(
    acc: float,
    acc_rating: float,
    pass_rating: float,
    tech_rating: float,
    *,
    predicted_acc: float | None = 0.0,
    slider_percentage: float | None = 0.0,
    notes: int | None = 0,
    note_span: float | None = 0.0,
    modifiers: str = "",
    modifiers_rating: Mapping[str, object] | None = None,
    modifier_values: Mapping[str, object] | None = None,
    context: str = "general",
    timing: bool = False,
) -> dict[str, float]:
    """ReplayUtils.PpFromScore の移植。

    引数:
      acc               精度（0〜1。score.accuracy = baseScore / maxScore）
      acc_rating 等     譜面のレーティング（difficulty.accRating / passRating / techRating）
      predicted_acc 等  曲線の材料（difficulty.predictedAcc / sliderPercentage / notes / noteSpan）。
                        無い（0・None）と予測精度 60% 以下と同じ＝基本の表の曲線になる
      modifiers         "FS,GN" のようなカンマ区切り（score.modifiers）
      modifiers_rating  difficulty.modifiersRating（無い譜面は None）。あると速度系はレーティングと曲線を差し替え
      modifier_values   difficulty.modifierValues。None なら RankedMap()（FS 0.40, SF 0.72, GN 0, NF -1.0 ...）
      context           "general"（通常）/ "nomods" / "nopause" / "golf" / "scpm"
      timing            譜面のモードが "RhythmGameStandard" の時 True（pp = acc * passRating * 55）

    戻り値: pp（= fullPP）, bonus_pp（= fullPP - rawPP: モディファイアで増減した分）,
            pass_pp / acc_pp / tech_pp（内訳。合計が pp になるよう按分した値）, raw_pp（モディファイア無しの PP）,
            multiplier（倍率）, stars（使った★。速度系はそのモディファイアの★）。
    """
    zero = {"pp": 0.0, "bonus_pp": 0.0, "pass_pp": 0.0, "acc_pp": 0.0, "tech_pp": 0.0,
            "raw_pp": 0.0, "multiplier": 1.0, "stars": 0.0}
    if not (0 < acc <= 1):   # NaN もここで弾く
        return zero
    if timing:
        v = acc * pass_rating * 55.0
        v = 0.0 if _bad(v) else v
        return {**zero, "pp": v, "raw_pp": v}

    mv = normalize_modifier_values(modifier_values, RANKED_MODIFIER_VALUES)
    mp = get_total_multiplier(mv, modifiers, modifiers_rating is None, context, f32=False)
    if "NF" in modifiers:
        return {**zero, "multiplier": mp}
    curve = _curve_of(predicted_acc, slider_percentage, notes, note_span)
    speed = None
    if modifiers_rating is not None:
        mr = _rating_lookup(modifiers_rating)
        # 最初に見つかった速度系モディファイアのレーティングと曲線に差し替え（完全一致・1つだけ）
        for m in modifiers.upper().split(","):
            if (m + "ACCRATING") in mr:
                div = SPEED_SPAN_DIVISORS.get(m)
                sc = (_curve_of(mr.get(m + "PREDICTEDACC"), mr.get(m + "SLIDERPERCENTAGE"), notes,
                                float(note_span or 0.0) / div) if div else curve)
                speed = (mr[m + "ACCRATING"], mr.get(m + "PASSRATING", 0.0), mr.get(m + "TECHRATING", 0.0), sc, mr.get(m + "PREDICTEDACC", 0.0))
                break
    out = _score_pp(acc, (acc_rating, pass_rating, tech_rating, curve, float(predicted_acc or 0.0)), mp, speed, context.lower() == "golf")
    out["multiplier"] = mp
    return out


def pp_from_score(score: Mapping[str, object]) -> dict[str, float]:
    """BeatLeader API のスコア JSON（/player/{id}/scores の data[i]）から PP を計算する。

    ReplayUtils.PpFromScore(Score, DifficultyDescription) と同じ材料を使う: score.accuracy / score.modifiers /
    leaderboard.difficulty の accRating・passRating・techRating・predictedAcc・sliderPercentage・notes・noteSpan・
    modifierValues・modifiersRating・modeName（"rhythmgamestandard" なら timing）。
    """
    diff = score["leaderboard"]["difficulty"]  # type: ignore[index]
    return pp_from_difficulty(float(score.get("accuracy") or 0.0), diff, str(score.get("modifiers") or ""))


def _json_or_none(v: object) -> Mapping[str, object] | None:
    try:
        return json.loads(v) if isinstance(v, str) and v else None
    except ValueError:
        return None


def pp_from_row(acc: float, lb: Mapping[str, object], modifiers: str = "") -> dict[str, float]:
    """保存の層の leaderboards の1行（列の名前。modifiers_rating・modifier_values は JSON の文字列）と精度から PP。"""
    return pp_from_acc(
        acc,
        float(lb["acc_rating"] or 0.0),  # type: ignore[arg-type]
        float(lb["pass_rating"] or 0.0),  # type: ignore[arg-type]
        float(lb["tech_rating"] or 0.0),  # type: ignore[arg-type]
        predicted_acc=lb["predicted_acc"],  # type: ignore[arg-type]
        slider_percentage=lb["slider_percentage"],  # type: ignore[arg-type]
        notes=lb["notes"],  # type: ignore[arg-type]
        note_span=lb["note_span"],  # type: ignore[arg-type]
        modifiers=modifiers,
        modifiers_rating=_json_or_none(lb["modifiers_rating"]),
        modifier_values=_json_or_none(lb["modifier_values"]),
        timing=str(lb["mode"] or "").lower() == "rhythmgamestandard",
    )


def pp_func_of_row(lb: Mapping[str, object]) -> Callable[[float], float]:
    """leaderboards の1行 → 「精度 → PP」の関数（モディファイア無しの pp_from_row(acc, lb)["pp"] と同じ値）。
    精度に依らない所（曲線・★・曲線の乗数・倍率の前の passPP・★の低い譜面の上乗せ）を先に求めておく。pp_from_row は1回ごとに★を
    二分法で解くので、同じ譜面で精度を変えて何度も求める時（recommend.py の推定の幅）はこちらを使う"""
    ar = float(lb["acc_rating"] or 0.0)  # type: ignore[arg-type]
    pr = float(lb["pass_rating"] or 0.0)  # type: ignore[arg-type]
    tr = float(lb["tech_rating"] or 0.0)  # type: ignore[arg-type]
    if str(lb["mode"] or "").lower() == "rhythmgamestandard":
        def timing(acc: float) -> float:
            v = acc * pr * 55.0 if 0 < acc <= 1 else 0.0
            return 0.0 if _bad(v) else v
        return timing
    pa = float(lb["predicted_acc"] or 0.0)  # type: ignore[arg-type]
    curve = _curve_of(lb["predicted_acc"], lb["slider_percentage"], lb["notes"], lb["note_span"])
    stars = to_stars(ar, pr, tr, curve, pa)
    at96 = curve_value(STAR_ACCURACY, curve)
    power = curve_power_at(stars)
    p = pass_pp(pr)
    lsm = low_star_multiplier(stars)
    mul = FORMULA["PpMultiplier"]

    def pp(acc: float) -> float:
        if not (0 < acc <= 1):
            return 0.0
        v = curve_value(acc, curve)
        if power != 1:
            v = acc_curve(v, at96, power)
        r = mul * (p * pass_acc_multiplier(acc, pa) + acc_pp(v, ar) * mid_star_top_multiplier(stars, acc, pa) + tech_pp(acc, tr)) * lsm
        return 0.0 if _bad(r) else r
    return pp


def pp_from_difficulty(acc: float, diff: Mapping[str, object], modifiers: str = "") -> dict[str, float]:
    """API の difficulty（譜面の1難易度）と精度から PP。"""
    return pp_from_acc(
        acc,
        float(diff.get("accRating") or 0.0),
        float(diff.get("passRating") or 0.0),
        float(diff.get("techRating") or 0.0),
        predicted_acc=diff.get("predictedAcc"),
        slider_percentage=diff.get("sliderPercentage"),
        notes=diff.get("notes"),
        note_span=diff.get("noteSpan"),
        modifiers=modifiers,
        modifiers_rating=diff.get("modifiersRating"),  # type: ignore[arg-type]
        modifier_values=diff.get("modifierValues"),  # type: ignore[arg-type]
        timing=str(diff.get("modeName") or "").lower() == "rhythmgamestandard",
    )


# ---------------------------------------------------------------------------
# 総 PP（リウェイトでも変わっていない）
# ---------------------------------------------------------------------------
WEIGHT_BASE = 0.965


def weight(i: int, *, f32: bool = True) -> float:
    """i 番目（0 始まり）の重み MathF.Pow(0.965f, i)。"""
    if f32:
        return _f32(math.pow(_f32(WEIGHT_BASE), i))
    return math.pow(WEIGHT_BASE, i)


def total_pp(pp_list: Iterable[float], *, f32: bool = True) -> float:
    """PlayerUtils.RecalculatePPAndRankFastGeneral の総PP。

    pp が 0 でないスコアを pp の降順に並べ、Σ pp_i * 0.965^i（i は 0 始まり）。
    上流は Banned・Qualification（認定前の譜面）・General コンテキスト外のスコアを除いている。
    呼び出し側で「ランク譜面の、そのプレイヤーの各譜面のベストスコアの pp」を渡すこと。
    """
    r = _f32 if f32 else _same
    pps = sorted((float(p) for p in pp_list if p), reverse=True)
    total = 0.0
    for i, p in enumerate(pps):
        total = r(total + r(r(p) * weight(i, f32=f32)))
    return total


def total_pp_parts(rows: Iterable[Mapping[str, float]], *, f32: bool = True) -> dict[str, float]:
    """総PPとその内訳（accPp / passPp / techPp）。rows は {"pp", "acc_pp", "pass_pp", "tech_pp"} の並び。

    重みは pp の降順で決め、内訳にも同じ重みを掛ける（上流と同じ）。
    """
    r = _f32 if f32 else _same
    items = sorted((x for x in rows if x.get("pp")), key=lambda x: x["pp"], reverse=True)
    out = {"pp": 0.0, "acc_pp": 0.0, "pass_pp": 0.0, "tech_pp": 0.0}
    for i, x in enumerate(items):
        w = weight(i, f32=f32)
        for k in out:
            out[k] = r(out[k] + r(r(float(x.get(k, 0.0))) * w))
    return out


if __name__ == "__main__":
    # 動作確認: 精度 96%・予測精度 98% の譜面
    res = pp_from_acc(0.96, 8.0, 3.0, 4.0, predicted_acc=0.98, notes=800, note_span=150)
    print({k: round(v, 4) for k, v in res.items()})
    print("total_pp([300, 250, 200]) =", round(total_pp([300, 250, 200]), 4))
