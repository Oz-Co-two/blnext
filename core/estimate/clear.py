# クリアできるか（2026-10-16 に足した調整の材料。docs/investigation.md の「クリアの見込みと高い★」）。
# 推定の曲線は「クリアできた曲のスコア」だけで作るので、難しい曲ほど甘く見積もる。それを補う:
#  - 自分がクリアしてきた譜面の範囲（95%点と最大）: 一覧の色と、範囲の外の曲を何曲まで入れるか（挑戦枠。assemble.py）
#  - その日のうちにクリアできる見込み: 挑戦の記録を（譜面, 日）ごとにまとめ、pass からロジスティック回帰（recommend.py）。
#    その譜面でクリアできなかった日が続いている曲は下げる（2026-10-18。下の FAIL_*）
#  - 比較対象の人の曲線の★の上の方での下がり方: 自分の★の範囲より上の推定を下げる（recommend.py）
# どれも DB のデータだけで決まるので、曲線と一緒に model.build で求める（engine.py がためておく）
import math, time
from typing import NamedTuple

import numpy as np

# 範囲に使う特徴（画面の名前 → leaderboards の列）。少なすぎる時は出さない
RANGE_COLS = (('pass', 'pass_rating'), ('tech', 'tech_rating'), ('acc', 'acc_rating'), ('nps', 'nps'), ('njs', 'njs'))
RANGE_Q = 0.95
# 2026-11-06 に 20 → 5（曲線が作れる最低の数）。範囲が無いと、未プレイの pass の上限と挑戦枠が効かず、14曲の人（自分の pass は最高 7.0）の
# 着実に pass 7.7、チャレンジングに pass 12.6 の未プレイの曲が入った
RANGE_MIN_N = 5
# 95%点を求めるのに要る曲の数。少なければ範囲の 95%点の代わりに最大を使い、star_hi は出さない（14曲の 95%点は上から2番目の曲で、
# 本人がクリアした曲より下で切ってしまう。★8〜9.8 の未プレイの 95% が NPS で「範囲の外」になり、チャレンジングが 10曲埋まらなかった）
RANGE_P95_MIN_N = 20
# 挑戦枠に数える特徴（一覧の行に出していて、クリアと疲れに効く物）
CHALLENGE_KEYS = ('pass', 'nps')

# 挑戦の記録は直近1年分だけを取り（fetch/refresh.py）、推定もその分だけを読む（2026-11-06 に利用者が決めた。古い記録は今の実力に合わない。公開している人には
# 何年分もあるが、作者の記録は約1年分で、それより長い形は確かめていない）。DB の古い行は消さない（振り返りと調査に使う）。自己ベストは総PP に要るので全部取る
ATTEMPT_DAYS = 365
CLEAR_MIN_DAYS = 30     # 見込みを求めるのに要る（譜面, 日）の数
CLEAR_MIN_FAILS = 5     # そのうちクリアできなかった日の数
CLEAR_RIDGE = 1e-3      # 回帰を安定させる小さな重み
CLEARED_BONUS = 0.5     # 前にクリアしたことのある曲は、見込みと 100% の間のこの割合だけ上げる

SLOPE_MIN_SCORES = 80   # ★の上の方の下がり方を求める人の、ランクのスコアの最低数
SLOPE_MIN_PLAYERS = 10  # 下がり方の中央値を出すのに要る人数

# その譜面でクリアできなかった日が続いている曲は、見込みを下げる（docs/investigation.md の「クリアの見込みと高い★」の 2026-10-18）。
# k ＝ その譜面で、最後にクリアした日（挑戦の記録のクリアか、自己ベストの日）の後の、クリアできなかった日の数として
#   見込み ＝ 1 / (1 + e^-(今の見込みの対数オッズ + c·log(1+k)))   （今の見込み＝pass の回帰＋CLEARED_BONUS。k=0 なら今までと同じ）
# 自分の記録 1854 日では、前にクリアできなかった日が1日ある日のクリアは 55%（pass だけの見込みは 73%）、2日で 48%（69%）と
# はっきり低く、c を求めると −2.0（交差検証でも当てはまりが良くなった）。日がたっても弱まらなかったので、日数では弱めない
SOFT_DAY_WEIGHT = 0.5   # やり直し・途中でやめただけの日（失敗＝end_type 2 の無い日）は半分に数える（失敗した日の後は 40%、
                        # やり直し・やめただけの日の後は 74% がクリア。半分に数えた方が当てはまりが少し良かった）
FAIL_MIN_DAYS = 30      # c を自分の記録から求めるのに要る「前にクリアできなかった日がある（譜面, 日）」の数（少なければ FAIL_DEFAULT）
FAIL_DEFAULT = -1.0     # 足りない時の c（自分の記録で求めた −2.0 より控えめに）
FAIL_MAX = 3.0          # c の大きさの上限（−3.0 より下げない。上がる向き＝正の値は 0 にする）
# 失敗（end_type 2）がほとんど記録されない人（2026-11-12）: 挑戦の記録が「クリア」と「やり直し・途中でやめた」だけの人がいる
# （統計を公開している国内上位の人の多くが、直近1年に失敗した日が 0日。失敗する前にやり直す・精度が伸びないと分かった所で
# やめる遊び方か、失敗が記録されない環境）。この人のやり直し・やめただけの日は「クリアできなかった日」とは言えない（そう数えると、
# 一番易しい曲でも見込みが 8割に届かず、着実（クリアの見込みの下限 80%）が 0曲になった）。クリアできなかった日のうち失敗した日が
# この割合より少なければ、やり直し・やめただけの日を数えない（見込みの回帰にも、クリアできなかった日の数にも）。失敗した日も
# 少なければ見込みは求めない（統計が非公開の人と同じ扱い）。失敗の記録がある人（作者など）は半分以上が失敗した日なので変わらない
HARD_MIN_SHARE = 0.1


class Day(NamedTuple):
    """（譜面, 日）1つ分。fails はその日より前のクリアできなかった日の数（fail_weight）、cleared はその日より前にクリアしたことがあるか"""
    pass_rating: float
    ok: bool
    fails: float = 0.0
    cleared: bool = False


def day_key(ts):
    """日時（秒）→ その日（'YYYY-MM-DD'。この PC の時刻で 0 時に区切る）。挑戦の記録を（譜面, 日）にまとめる時と、
    クリアした日を数える時（recommend.py の R3）で同じ区切りにする"""
    return time.strftime('%Y-%m-%d', time.localtime(ts))


def fail_weight(hard, soft):
    """クリアできなかった日の数（失敗した日・やり直しかやめただけの日）→ 見込みの式の k"""
    return hard + SOFT_DAY_WEIGHT * soft


def _quantile(sorted_vals, q):
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def map_range(store, player_id):
    """自分のランクのスコア（NF は除く）の譜面の特徴の範囲 {名前: {p50, p75, p95, max}}。pass の p75 は難しさの「着実」の pass の
    上限と、未プレイの曲の pass による下げ（unplayed_pass_drop）の基準に、tech の p50（中央）は未プレイの曲の tech による下げ
    （unplayed_tech_drop）の基準に使う"""
    rows = store.q('SELECT %s FROM scores s JOIN leaderboards l ON l.leaderboard_id=s.leaderboard_id '
                   "WHERE s.player_id=? AND l.status=3 AND instr(COALESCE(s.modifiers, ''), 'NF')=0"
                   % ', '.join('l.' + c for _, c in RANGE_COLS), (player_id,))
    out = {}
    for name, col in RANGE_COLS:
        v = sorted(r[col] for r in rows if r[col] is not None)
        if len(v) >= RANGE_MIN_N:
            out[name] = {'p50': _quantile(v, 0.5), 'p75': _quantile(v, 0.75),
                         'p95': _quantile(v, RANGE_Q) if len(v) >= RANGE_P95_MIN_N else v[-1], 'max': v[-1]}
    return out


def beyond(rng, c):
    """範囲の外（挑戦枠に数える）か。c は推定の候補（pass_rating・nps を持つ）"""
    vals = {'pass': c.get('pass_rating'), 'nps': c.get('nps')}
    return any(k in rng and vals[k] is not None and vals[k] > rng[k]['p95'] for k in CHALLENGE_KEYS)


def _logit(p):
    p = min(1 - 1e-12, max(1e-12, p))
    return math.log(p / (1 - p))


class ClearModel:
    """pass → その日のうちにクリアできる見込み（0〜1）。p = 1 / (1 + e^-(a + b·pass))。前にクリアした曲は CLEARED_BONUS で上げ、
    クリアできなかった日が続いている曲は c·log(1+k) で下げる（k は fail_weight）。
    days・fails は元にした（譜面, 日）とそのうちクリアできなかった日の数、fail_days は c を求めるのに使えた日の数、fail_fitted は
    c を自分の記録から求めたか（False なら FAIL_DEFAULT）"""
    def __init__(self, a, b, days, fails, c=0.0, fail_days=0, fail_fitted=False):
        self.a, self.b, self.days, self.fails = a, b, days, fails
        self.c, self.fail_days, self.fail_fitted = c, fail_days, fail_fitted

    def __call__(self, pass_rating, cleared_before=False, fails=0.0):
        z = self.a + self.b * pass_rating
        p = 1 / (1 + math.exp(-z)) if z > -40 else 0.0
        if cleared_before:
            p += (1 - p) * CLEARED_BONUS
        if fails > 0 and self.c and p > 0:
            z = _logit(p) + self.c * math.log1p(fails)
            p = 1 / (1 + math.exp(-z)) if z > -40 else 0.0
        return p


def clear_history(store, cleared_at=None, since=None):
    """挑戦の記録を（譜面, その日）ごとにまとめ、譜面ごとに日の順にたどる。練習・NF の挑戦は数えない。
    since を渡すと、その日時より前の挑戦は読まない（ATTEMPT_DAYS）。
    cleared_at は自分のスコア（NF を除く）の {leaderboard_id: 自己ベストの日時}。その日もクリアした日とみなす（挑戦の記録が取れる
    より前のクリアもあるため。見込みの回帰の「その日にクリアできたか」は挑戦の記録だけで決める）。
    返り値は (日の並び [Day], 譜面ごとの今のクリアできなかった日 {leaderboard_id: (失敗した日, やり直しかやめただけの日)})。
    今のクリアできなかった日は、最後にクリアした日の後の分（無い譜面は入らない）。
    失敗がほとんど記録されない人（HARD_MIN_SHARE）は、やり直し・やめただけの日を読まなかったことにする"""
    cleared_at = cleared_at or {}
    day_of = day_key
    days = {}
    for r in store.q('SELECT a.leaderboard_id, a.timeset, a.end_type, a.modifiers, l.pass_rating FROM attempts a '
                     'JOIN leaderboards l ON l.leaderboard_id=a.leaderboard_id '
                     'WHERE l.pass_rating IS NOT NULL AND a.end_type IN (1, 2, 3, 4) AND a.timeset IS NOT NULL AND a.timeset>=?', (since or 0,)):
        if 'NF' in (r['modifiers'] or ''):
            continue
        d = days.setdefault((r['leaderboard_id'], day_of(r['timeset'])), {'pass': r['pass_rating'], 'ok': False, 'hard': False})
        d['ok'] = d['ok'] or r['end_type'] == 1
        d['hard'] = d['hard'] or r['end_type'] == 2
    if soft_only(days.values()):
        days = {k: d for k, d in days.items() if d['ok'] or d['hard']}
    by_map = {}
    for (lid, day), d in days.items():
        by_map.setdefault(lid, []).append((day, d))
    out, now = [], {}
    for lid, ds in by_map.items():
        ds.sort(key=lambda x: x[0])
        best = day_of(cleared_at[lid]) if cleared_at.get(lid) else None
        hard = soft = 0
        cleared = False
        for day, d in ds:
            if best is not None and best < day:   # 自己ベストの日を過ぎた（その日はクリアした日）
                hard = soft = 0
                cleared, best = True, None
            out.append(Day(d['pass'], d['ok'], fail_weight(hard, soft), cleared))
            if d['ok'] or best == day:
                hard = soft = 0
                cleared = True
                if best == day:
                    best = None
            elif d['hard']:
                hard += 1
            else:
                soft += 1
        if best is not None:   # 自己ベストの日が挑戦の記録の最後の日より後
            hard = soft = 0
        if hard or soft:
            now[lid] = (hard, soft)
    return out, now


def soft_only(days):
    """（譜面, 日）のまとめ（{ok, hard}）の並びから、失敗がほとんど記録されない人か（HARD_MIN_SHARE）。クリアできなかった日が無ければ False"""
    miss = [d for d in days if not d['ok']]
    return bool(miss) and sum(1 for d in miss if d['hard']) < HARD_MIN_SHARE * len(miss)


def _fit_fail(days, a, b):
    """クリアできなかった日の項の c（今の見込みの対数オッズを足しておく 1 変数のロジスティック回帰）。求められなければ None"""
    use = [d for d in days if d.fails > 0]
    ok = sum(1 for d in use if d.ok)
    if len(use) < FAIL_MIN_DAYS or ok < CLEAR_MIN_FAILS or len(use) - ok < CLEAR_MIN_FAILS:
        return None, len(use)
    base = ClearModel(a, b, 0, 0)
    off = np.array([_logit(base(d.pass_rating, d.cleared)) for d in use])
    x = np.log1p(np.array([d.fails for d in use], dtype=float))
    y = np.array([1.0 if d.ok else 0.0 for d in use])
    c = 0.0
    for _ in range(50):   # ニュートン法
        p = 1 / (1 + np.exp(-np.clip(off + c * x, -40, 40)))
        g = float(x @ (p - y)) + CLEAR_RIDGE * c
        h = float((x * x) @ (p * (1 - p))) + CLEAR_RIDGE
        step = g / h
        c -= step
        if abs(step) < 1e-8:
            break
    return (c if math.isfinite(c) else None), len(use)


def fit_clear(days):
    """（譜面, 日）の並び（Day。(pass, クリアできたか) の組でもよい）からロジスティック回帰。
    データが足りない・pass が上がっても見込みが下がらない時は None"""
    days = [d if isinstance(d, Day) else Day(*d) for d in days]
    n = len(days)
    fails = sum(1 for d in days if not d.ok)
    if n < CLEAR_MIN_DAYS or fails < CLEAR_MIN_FAILS or fails == n:
        return None
    X = np.column_stack([np.ones(n), np.array([d.pass_rating for d in days], dtype=float)])
    y = np.array([1.0 if d.ok else 0.0 for d in days])
    w = np.zeros(2)
    for _ in range(50):   # ニュートン法
        p = 1 / (1 + np.exp(-np.clip(X @ w, -40, 40)))
        g = X.T @ (p - y) + CLEAR_RIDGE * w
        H = (X * (p * (1 - p))[:, None]).T @ X + CLEAR_RIDGE * np.eye(2)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    if not np.all(np.isfinite(w)) or w[1] >= 0:
        return None
    a, b = float(w[0]), float(w[1])
    c, used = _fit_fail(days, a, b)
    fitted = c is not None
    c = min(0.0, max(-FAIL_MAX, c)) if fitted else FAIL_DEFAULT
    return ClearModel(a, b, n, fails, c, used, fitted)


def upper_slope(curve, stars):
    """曲線の★の上の方（その人の★の中央 → 95%点）での、★1 あたりの精度の変わり方（ふつうは負）。点が少なければ None"""
    s = sorted(x for x in stars if x)
    if curve is None or len(s) < SLOPE_MIN_SCORES:
        return None
    lo, hi = _quantile(s, 0.5), _quantile(s, RANGE_Q)
    if hi - lo < 0.5:
        return None
    return (float(curve(hi)) - float(curve(lo))) / (hi - lo)


def pop_slope(slopes):
    """比較対象の人の下がり方の中央値（人数が足りなければ None）"""
    v = [x for x in slopes if x is not None]
    return float(np.median(v)) if len(v) >= SLOPE_MIN_PLAYERS else None


def star_hi(stars):
    """自分の★の範囲の上端（ランクのスコアの★の95%点）。点が少なければ None"""
    s = sorted(x for x in stars if x)
    return _quantile(s, RANGE_Q) if len(s) >= RANGE_P95_MIN_N else None
