# ステップ1: 各プレイヤーの「いつもの実力」の曲線 E_p(★)（docs/spec.md の「おすすめロジック」）
#  - ★（モディファイアで補正済み）と精度の関係を、外れ値に強い局所回帰（LOWESS）で近似し、右下がりにそろえる（単調減少の回帰）
#  - 自分の曲線は直近のスコアを重く扱う（半減期の重み）
#  - 曲ごとの上振れ・下振れ（残差）は、実際の精度と曲線の値の差
# 速さのため、曲線は★の格子の上で求め、間は直線でつなぐ（比較対象の全員分を取得の直後に計算する）
import numpy as np

GRID_STEP = 0.05        # ★の格子の幅
SPAN = 0.4              # LOWESS で1点の推定に使う近くの点の割合
MIN_SPAN_POINTS = 12    # 点が少ない時でも最低これだけは使う
ROBUST_ITERS = 2        # 外れ値の重みを下げるやり直しの回数


class Curve:
    """★ → 精度。格子（xs, ys）を直線でつなぎ、範囲の外は端の傾きで延ばす（0〜1に収める）"""
    def __init__(self, xs, ys, lo, hi, n):
        self.xs, self.ys = xs, ys
        self.lo, self.hi, self.n = lo, hi, n   # 元の点の★の範囲と点の数

    def __call__(self, stars):
        s = np.asarray(stars, dtype=float)
        y = np.interp(s, self.xs, self.ys)
        if len(self.xs) >= 2:
            k = max(4, len(self.xs) // 10)   # 端の傾きは端から1割の範囲の平均（局所の揺れに引っ張られないように）
            left = min(0.0, (self.ys[k] - self.ys[0]) / (self.xs[k] - self.xs[0]))
            right = min(0.0, (self.ys[-1] - self.ys[-1 - k]) / (self.xs[-1] - self.xs[-1 - k]))
            y = np.where(s < self.xs[0], self.ys[0] + left * (s - self.xs[0]), y)
            y = np.where(s > self.xs[-1], self.ys[-1] + right * (s - self.xs[-1]), y)
        return np.clip(y, 0.0, 1.0)


def _pava_decreasing(y, w):
    """重み付きの単調減少の回帰（Pool Adjacent Violators）"""
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi); wts.append(wi); cnt.append(1)
        while len(vals) > 1 and vals[-2] < vals[-1]:   # 減少でない所を平均でならす
            tw = wts[-2] + wts[-1]
            v = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / tw if tw > 0 else (vals[-2] + vals[-1]) / 2
            c = cnt[-2] + cnt[-1]
            vals[-2:] = [v]; wts[-2:] = [tw]; cnt[-2:] = [c]
    return np.repeat(vals, cnt)


def fit(stars, acc, weights=None):
    """点（★, 精度）に曲線を当てはめる。weights は点ごとの重み（半減期など）。点が少なすぎれば None"""
    x = np.asarray(stars, dtype=float)
    y = np.asarray(acc, dtype=float)
    w0 = np.ones_like(x) if weights is None else np.asarray(weights, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y) & (w0 > 0) & (x > 0) & (y > 0)
    x, y, w0 = x[ok], y[ok], w0[ok]
    n = len(x)
    if n < 5:
        return None
    lo, hi = float(x.min()), float(x.max())
    xs = np.arange(np.floor(lo / GRID_STEP) * GRID_STEP, hi + GRID_STEP, GRID_STEP)
    k = min(n, max(MIN_SPAN_POINTS, int(np.ceil(SPAN * n))))
    robust = np.ones(n)
    d = np.abs(xs[:, None] - x[None, :])                      # 格子 × 点 の距離
    h = np.partition(d, k - 1, axis=1)[:, k - 1]              # 格子ごとに k 番目に近い点までの距離＝窓の幅
    h = np.maximum(h, 1e-6)
    kern = np.clip(1 - (d / h[:, None]) ** 3, 0, None) ** 3   # 三重立方の窓
    ys = None
    for it in range(ROBUST_ITERS + 1):
        W = kern * (w0 * robust)[None, :]
        sw = W.sum(1)
        sx = (W * x).sum(1); sy = (W * y).sum(1)
        sxx = (W * x * x).sum(1); sxy = (W * x * y).sum(1)
        den = sw * sxx - sx * sx
        with np.errstate(invalid='ignore', divide='ignore'):
            b = np.where(np.abs(den) > 1e-12, (sw * sxy - sx * sy) / den, 0.0)
            a = np.where(sw > 0, (sy - b * sx) / sw, np.nan)
        ys = a + b * xs
        if np.isnan(ys).any():                                 # 窓に点が無い格子は近くの値で埋める
            good = ~np.isnan(ys)
            ys = np.interp(xs, xs[good], ys[good])
        if it < ROBUST_ITERS:
            res = y - np.interp(x, xs, ys)
            s = np.median(np.abs(res))
            if s <= 1e-9:
                break
            u = res / (6 * s)
            robust = np.clip(1 - u * u, 0, None) ** 2          # 二重平方の重み（外れ値ほど軽く）
    dens = kern.sum(1) + 1e-6                                  # 格子の近くの点の多さ（ならす時の重み）
    ys = _pava_decreasing(ys, dens)
    return Curve(xs, np.clip(ys, 0.0, 1.0), lo, hi, n)


def time_weights(timeset, now, half_life_days):
    """直近のスコアほど重い重み 0.5^(経過日数/半減期)"""
    t = np.asarray(timeset, dtype=float)
    age = np.clip((now - t) / 86400.0, 0, None)
    return np.power(0.5, age / max(1e-6, half_life_days))
