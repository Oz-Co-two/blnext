# ステップ3〜5: 曲ごとの「今やったら取れそうな精度」、PP、総PPの増加量、上がる国内順位、並び順の値（docs/spec.md の「おすすめロジック」）
import math
from statistics import NormalDist

import numpy as np

from . import clear as C
from .ppcalc import pp_func_of_row, WEIGHT_BASE

FC_GAP_MIN = 0.005      # FC していた場合の精度との差のうち、その pass で普通の量より多い分がこれ以上なら「ミスで落とした曲」
RECENT_DAYS = 30        # 伸び悩みの判定で「最近挑戦している」と見なす日数
CONF_W0 = 0.6           # 確からしさ（自分の結果が無い曲）: 似ている人の重みの合計がこの値で約63%（1 - e^-1）
CONF_FLOOR = 0.3        # 似ている人が足りない人（model.FEW_SIMILAR）の、自分の結果が無い曲の確からしさの下限（0 だと並び順の値が全部 0 になる）
ATTEMPT_MIN = 3         # 伸び悩みの判定に使う、直近の挑戦（クリア）の最低数
ACC_MAX = 0.9999

# 2026-10-18 の調査（作者の調べ「精度の推定とpass」・docs/investigation.md の「推定の確かめ」）で足した直し:
#  R1 FC との差の上乗せは、その pass で普通の量（model.fc_norm）より多いミスの分だけにする。ミスの量は pass とともに増える
#     （偶然のミスではなく、次に遊んでも出る分）ので、差の全部を上乗せすると pass の高いプレイ済みの曲を甘く見積もる
#  R2 自分の結果が無い曲（未プレイ・NF のみ）は、pass が基準（自分がクリアしてきた曲の pass の75%点）を超える分だけ下げる
#     （設定 unplayed_pass_drop）。調べた自分の記録では、★が同じなら自己ベストは pass が高いほど低く（pass 1 あたり約 −1%）、
#     似ている人にはこの傾向がほとんど無い（その人に特有の弱さ）ので、似ている人を混ぜても入らない。プレイ済みの曲は自分の残差に
#     譜面の難しさが入っているので下げない
#  R3 プレイ済みの曲は、その譜面をクリアした日の数で直す（下の CLEAR_DAY_*）
UNPLAYED_P0 = 3.0           # R2 の基準が分からない時（クリアしてきた曲が少ない）の pass
UNPLAYED_BEYOND_MUL = 2.0   # R2: 自分の★の範囲（★の95%点）より上の曲は、下げ幅をこの倍にする（範囲の外では −3.4%/pass。ただし26件と少ない）
# R3: 推定は「次に遊んだ日の一番（クリアした時）」の見込みにしたいが、曲線も残差も自己ベスト（何度も遊んだ中の一番）から作るので、
# クリアした日が多い曲ほど次の日の一番は自己ベストに届きにくい。自分の記録（自己ベストのある曲をクリアした日 785 件）で、その日の
# 一番 − 自己ベストは、それまでにクリアした日が 1日: +1.88%（70% が伸びた）・2日: +0.07%（51%）・3日以上: −1.86%（29%）。
# 時間で前後に分けても同じ向きだったので、控えめに丸めた。3日以上の −1% は伸び悩みの割引（_stalled）と少し重なる（今は両方入れる）。
# 2026-10-28 から、直しはミスの割合（1 − 精度）に掛ける倍率で持つ（推定の幅と同じ尺度。作者の調べ「推定の幅」）。
# 精度 89%（自己ベストのある曲をクリアした日の推定の中央）の曲で ±1% になる値で、100% に近い曲ほど小さい（96% の曲の +1% は
# ミスを 25% 減らすのと同じで、Easy の曲を大きく上げすぎていた）。画面の説明（web/js/screens/recommend.js の FLAGS）にも書いている
CLEAR_DAY_ONE = 0.91        # クリアした日が1日だけ（初めてクリアした日の自己ベスト）: ミスを 9% 減らす
CLEAR_DAY_MANY = 1.09       # CLEAR_DAY_MANY_N 日以上（2日は直さない）: ミスを 9% 増やす
CLEAR_DAY_MANY_N = 3

# 2026-10-25 の調査（作者の調べ「曲の特徴と似ている人」）とプレイ記録で足した直し:
#  R5 自分の結果が無い曲（未プレイ・NF のみ）は、tech が基準（自分がクリアしてきた曲の tech の中央）を超える分だけ下げる
#     （設定 unplayed_tech_drop）。★が同じでも、この人の精度は tech の高い曲で低く（1標準偏差あたり −0.84%）、比較対象の人には
#     ほとんど無い傾向（中央 −0.16%）なので、似ている人を混ぜても入らない。初クリア 270曲の当たりは MAE 3.04 → 2.83・RMSE 4.50 → 4.05。
#     何度も遊んだ曲の自己ベストでは tech はほとんど効かない（下げると悪くなる）ので、プレイ済みの曲は下げない。
#     ★の範囲より上で強めること（R2 の2倍）は確かめていないのでしない。基準の分布が無い時（クリアしてきた曲が少ない）は下げない
#  R1 の制限: 前の自己ベストが曲線より大きく下の曲（自分の残差 < FC_SKIP_BELOW）には R1 を足さない。2026-10-18〜10-25 に遊んだ
#     再プレイで、推定が前の自己ベストより5%以上上だった8曲は推定より平均 −6.0% で、そのうち R1 の大きさが分かる3曲には
#     +2.1〜3.1% が付いていた。過去の再プレイを R1 なしで作り直すと、前の一番が曲線より5%以上下の 113（曲, 日）の偏りは −0.53% だけ
#     だった（R1 を足すとその分だけ上に外れる）。前の一番が曲線に近い曲の R1 は合っていたので、そちらは今までどおり
FC_SKIP_BELOW = -0.05
# 2026-11-01 に足した直し（作者のプレイの記録 の 2026-10-30・11-01 の R4 の段落）:
#  R4 自分の結果がある曲は、ほかの直し（R1・R3）まで足した推定を、前の自己ベストとの間に寄せる（推定 + R4_PB_WEIGHT ×
#     (自己ベスト − 推定)）。推定が自己ベストより低くなるのは mix（自分の残差を似ている人と混ぜて縮める）のため、高すぎるのは
#     R1・R3 の上乗せと縮めのため。10/28 以降の再プレイ 17曲（★の換算の前のワンセイバーを除く）で、推定との差の絶対値の平均が
#     1.41%（偏り −0.11%）→ 1.15%（+0.01%）。一番外れた1曲を除いた16曲でも 1.26 → 1.13%。
#     確かめた推定は出力した時の値（全部の直しの後）なので、直しの一番最後（精度の上限・0〜ACC_MAX で切る前）に入れる
R4_PB_WEIGHT = 0.5
PB_FLAG_MIN = 0.005   # R4 で動かした量がこの値以上の時だけ印 pb を付ける（小さい時まで毎回付くとうるさい）
# 精度の上限（2026-11-01。model.AccCeiling）: 近い★の、ほかの曲の自分の一番より上の精度は取れないものとして見込みを出す。
# 推定は上限で頭打ちにし、推定の幅（AccDist）の上限より上の部分は上限で取れたものとみなす（expected_gain）。自己ベストが上限以上の
# 曲は伸びしろ 0（組み立ての nogain で外れる）。未プレイの曲にもかける（推定がもともと上限より低く、削られるのは幅の上の端だけ）。
# 印 cap は、自己ベストが上限以上の曲と、推定の幅のうち CAP_FLAG_SHARE 以上が上限より上にあった曲（見込みが目に見えて削られた曲）
CAP_FLAG_SHARE = 0.1
# 推定の内訳（parts。出力の記録に残して、後で直しごとの当たりを確かめる）の丸めの桁
PARTS_DIGITS = 6

# 2026-10-28 に足した: 推定の幅（作者の調べ「推定の幅」）。推定は1つの値だが、次に遊んだ日の一番（クリアした時）はぶれる。
# 自分の記録（自己ベストのある曲をクリアした日 825件・初クリア 752件。推定はその日より前の自己ベストで作り直した）では、
# ミスの割合の比の対数 z = log((1 − 推定) / (1 − その日の一番))（正＝推定より良い）が、推定の高さにも pass にもほとんど依らず、
# 左右で幅の違う正規分布（良い側はぶれが小さく、悪い側は大きい＝ミス・調子の悪い日）によく当てはまった（精度の差で見ると、
# 幅は推定 85% 未満の 4% から 95% 以上の 1% まで縮む＝100% に近い曲ほど伸びる余地が小さい）。
# 増える総PP（gain）は、この分布で取れた時の増えの期待値（＝自己ベストを超える見込み p_up × 超えた時の平均の増え gain_up）にする。
# 推定どおりの時の増えだと、推定が自己ベストより低い曲（PP の高い自己ベストは上振れしていることが多い）はいつも 0 で、上位の曲の
# 再プレイを選ばなかった。再プレイの「自己ベストを超えた割合」は、それまでにクリアした日が 1日・2日・3日以上で
# 予測 73・53・31% / 実際 71・49・29%、超えた分の平均は 予測 1.37% / 実際 1.41%。値はこの利用者の記録から求めたので、公開前に見直す
SPREAD = {   # 自分の結果がある曲か → z の (中央, 良い側の幅, 悪い側の幅)
    True: (0.04, 0.24, 0.31),     # 再プレイ（中央はミスが 4% 少ない所。良い側 1σ でミスが 21% 減る＝推定 90% の曲で +2.1%）
    False: (-0.04, 0.26, 0.37),   # 初クリア（自分の結果が無い曲）
}
SPREAD_NODES = 12    # 期待値を求める点の数（自己ベストより上の確率を等分した真ん中の精度で増えを求めて平均する）
UP_MIN = 1e-4        # 超える見込みがこれより小さければ増えを 0 とみなす
_N = NormalDist()


class Gain:
    """総PPの増加量を速く求める。自分のランクのスコアの PP を高い順に並べ、Σ pp_i·0.965^i の部分和を持っておく"""
    def __init__(self, pps):
        self.S = np.sort(np.asarray([p for p in pps if p and p > 0], dtype=float))[::-1]
        self.w = np.power(WEIGHT_BASE, np.arange(len(self.S) + 1))
        self.A = np.concatenate([[0.0], np.cumsum(self.S * self.w[:len(self.S)])])   # A[x] = Σ_{i<x} S_i w_i
        self.total = float(self.A[-1])

    def _index_desc(self, v):
        """高い順の並びで v より大きい物の数＝v が入る位置"""
        return int(np.searchsorted(-self.S, -v, side='left'))

    def after(self, new, old=0.0):
        """ある譜面の PP を old から new に置き換えた時の総PP（new <= old なら今のまま）"""
        if new <= (old or 0):
            return self.total
        k = self._index_desc(new)
        if old and old > 0:
            j = self._index_desc(old)
            if j < len(self.S) and abs(self.S[j] - old) < 1e-9:
                # k..j-1 が1つ下がり、j を抜き、j より後はそのまま
                return float(self.A[k] + new * self.w[k] + WEIGHT_BASE * (self.A[j] - self.A[k]) + (self.total - self.A[j + 1]))
        # 足すだけ: k 以降が1つ下がる
        return float(self.A[k] + new * self.w[k] + WEIGHT_BASE * (self.total - self.A[k]))


class AccDist:
    """次に遊んだ日の一番（クリアした時）の精度の分布（SPREAD）。精度 = 1 − (1 − est)·e^−z で、z は中央 m・良い側の幅 su・
    悪い側の幅 sd の左右で幅の違う正規分布"""
    def __init__(self, est, has_self):
        self.m, self.su, self.sd = SPREAD[bool(has_self)]
        self.miss = max(1 - ACC_MAX, 1 - est)
        self.lo = self.sd / (self.su + self.sd)   # 中央より悪くなる確率

    def cdf(self, x):
        """精度が x 以下になる確率"""
        if x >= 1:
            return 1.0
        z = math.log(self.miss / (1 - x))
        if z < self.m:
            return 2 * self.lo * _N.cdf((z - self.m) / self.sd)
        return self.lo + 2 * (1 - self.lo) * (_N.cdf((z - self.m) / self.su) - 0.5)

    def ppf(self, q):
        """精度の q 分位点（0 < q < 1）"""
        if q < self.lo:
            z = self.m + self.sd * _N.inv_cdf(q / (2 * self.lo))
        else:
            z = self.m + self.su * _N.inv_cdf((q - self.lo) / (2 * (1 - self.lo)) + 0.5)
        return 1 - self.miss * math.exp(-z)


def expected_gain(dist, best, gain_at, cap=None):
    """自己ベストの精度 best（無ければ None）を超える見込みと、増える総PP の期待値。gain_at(精度) はその精度で取れた時の増え。
    超える側の確率を SPREAD_NODES 等分し、それぞれの真ん中の精度の増えを平均する。返り値は (見込み, 期待値)。
    cap は精度の上限（無ければ None）: 上限より上の精度は上限で取れたものとみなす。自己ベストが上限以上なら見込みも期待値も 0"""
    if cap is not None and best and best >= cap:
        return 0.0, 0.0
    q0 = dist.cdf(best) if best else 0.0
    up = 1.0 - q0
    if up < UP_MIN:
        return up, 0.0
    top = ACC_MAX if cap is None else min(ACC_MAX, cap)
    tot = sum(gain_at(min(top, dist.ppf(q0 + up * (i + 0.5) / SPREAD_NODES))) for i in range(SPREAD_NODES))
    return up, up * tot / SPREAD_NODES


def _stalled(row, attempts, now, s):
    """伸び悩み: 挑戦の記録があればその精度で、無ければプレイ回数と更新の無い日数で判定する"""
    if not row:
        return False
    best = row['acc'] or 0
    recent = [a for a in attempts if a['end_type'] == 1 and a['timeset'] and now - a['timeset'] <= s['stall_days'] * 86400
              and a['timeset'] > (row['timeset'] or 0)]
    if len(recent) >= ATTEMPT_MIN:
        return max(a['acc'] or 0 for a in recent) < best   # 最近何度もクリアしているのに自己ベストに届いていない
    pc, last_try, ts = row['play_count'] or 0, row['last_try_time'] or 0, row['timeset'] or 0
    return (pc >= s['stall_play_count'] and now - last_try <= RECENT_DAYS * 86400
            and now - ts >= s['stall_days'] * 86400)


def clear_days(atts, row, first_ts):
    """R3: その譜面をクリアした日の数。挑戦の記録のクリア（end_type 1。NF は除く。練習は end_type 5 なので入らない）の日と、
    自己ベストの日（日の区切りは clear.day_key）。返り値は (日の数, 少なめか)。自己ベストが挑戦の記録の始まり（first_ts）より前なら、
    その前にクリアした日は数えられないので「少なめ」"""
    days = {C.day_key(a['timeset']) for a in atts if a['end_type'] == 1 and a['timeset'] and 'NF' not in (a['modifiers'] or '')}
    ts = row['timeset'] if row else None
    if ts:
        days.add(C.day_key(ts))
    return len(days), bool(ts and first_ts and ts < first_ts)


def clear_days_adj(k, partial, acc):
    """R3 の直し（精度 acc に足す値）。ミスの割合（1 − acc）に CLEAR_DAY_* を掛けた分だけ動かす。
    日の数が少なめ（partial）の時は、CLEAR_DAY_MANY_N 日以上と分かった時だけ直す"""
    if k >= CLEAR_DAY_MANY_N:
        ratio = CLEAR_DAY_MANY
    elif k == 1 and not partial:
        ratio = CLEAR_DAY_ONE
    else:
        return 0.0
    return (1 - ratio) * max(0.0, 1 - acc)


def unplayed_pass_drop(pass_rating, stars, p0, star_hi, u):
    """R2 の下げ幅（精度。正の数）: u × (pass − p0)。自分の★の範囲より上の曲は UNPLAYED_BEYOND_MUL 倍。u は設定の値（% → 割合）"""
    if not u or pass_rating is None or pass_rating <= p0:
        return 0.0
    return u * (pass_rating - p0) * (UNPLAYED_BEYOND_MUL if star_hi is not None and stars and stars > star_hi else 1.0)


def pass_base(model):
    """R2 の基準の pass（自分がクリアしてきた曲の pass の75%点。分からなければ UNPLAYED_P0）"""
    p = ((model.range or {}).get('pass') or {}).get('p75')
    return UNPLAYED_P0 if p is None else float(p)


def unplayed_tech_drop(tech_rating, t0, u):
    """R5 の下げ幅（精度。正の数）: u × (tech − t0)。基準 t0 が分からない（None）・tech が基準以下なら 0。u は設定の値（% → 割合）"""
    if not u or t0 is None or tech_rating is None or tech_rating <= t0:
        return 0.0
    return u * (tech_rating - t0)


def tech_base(model):
    """R5 の基準の tech（自分がクリアしてきた曲の tech の中央。分からなければ None＝下げない）"""
    t = ((model.range or {}).get('tech') or {}).get('p50')
    return None if t is None else float(t)


def beyond_drop(model, s):
    """自分の★の範囲より上で、★1 あたり下げる精度（正の数。比較対象の人の下がり方が分からなければ 0）"""
    return max(0.0, -(model.pop_slope or 0.0)) * float(s['beyond_star_factor'])


def estimate(store, model, settings, now):
    """ランク譜面ごとの推定。返り値は候補の dict の並び（並び順の値 score の高い順）"""
    s = settings.values()
    me = model.me
    lbs = store.q('SELECT * FROM leaderboards WHERE status=3')   # モードの絞り込みは組み立て（assemble.py）で行う
    lb_by = {l['leaderboard_id']: l for l in lbs}
    attempts = {}
    for a in store.q('SELECT * FROM attempts WHERE timeset>=?', (now - C.ATTEMPT_DAYS * 86400,)):
        attempts.setdefault(a['leaderboard_id'], []).append(a)
    ranked = {l['leaderboard_id'] for l in lbs}
    gain = Gain([r['pp'] for r in model.my_scores.values() if r['leaderboard_id'] in ranked])
    ranking_at = int(store.meta('ranking_at') or 0)
    others_pp = np.sort(np.array([r['pp'] for r in store.q(
        'SELECT pp FROM players WHERE country=(SELECT country FROM players WHERE player_id=?) AND seen_at=? AND player_id<>? '
        'AND pp IS NOT NULL', (me.player_id, ranking_at, me.player_id))], dtype=float))
    beta = float(s['self_weight'])
    prior = float(s['similar_prior'])
    # 自分の★の範囲より上の下げ方（★1 あたりの精度。比較対象の人の曲線の下がり方 × 設定の倍率。0 なら下げない）。
    # ★の範囲の上端・曲線を読む★は、モードの換算をした★（model.eff_stars）
    hi = model.star_hi
    drop = beyond_drop(model, s)
    hi_val = float(me.curve(hi)) if (me.curve is not None and hi is not None) else None
    cf = float(s['clear_factor'])
    u = float(s['unplayed_pass_drop']) / 100   # R2（設定は % 単位）
    p0 = pass_base(model)
    ut = float(s['unplayed_tech_drop']) / 100  # R5（設定は % 単位）
    cap_margin = float(s['acc_cap_margin']) / 100   # 精度の上限に足す余裕（設定は % 単位。難しさの段階で変わる）
    t0 = tech_base(model)
    # R3: 挑戦の記録の始まり（無い人は直さない）
    first_ts = min((a['timeset'] for xs in attempts.values() for a in xs if a['timeset']), default=None)

    def base_at(x):
        """いつもの実力の曲線の値。自分の★の範囲より上は、比較対象の人の下がり方でも下げる（低い方を使う）。
        曲線はクリアできた曲のスコアだけで作るので、★の上の方では平らになりやすく、難しい曲を甘く見積もるため。
        自分のスコアの一番低い★より下は、端の傾きで延ばさず端の値で止める（53曲の人の★1.31 が推定 100%・+520PP になった）"""
        b = float(me.curve(max(x, me.curve.lo)))
        if drop > 0 and hi_val is not None and x > hi:
            b = min(b, hi_val - drop * (x - hi))
        return max(0.0, b)

    out = []
    for lb in lbs:
        if not lb['stars'] or lb['acc_rating'] is None:
            continue
        lid = lb['leaderboard_id']
        if me.curve is None:
            continue
        x = model.eff_stars(lb['stars'], lb['mode'])   # 自分の曲線を読む★（モードの換算をした★。model.star_scales）
        base = base_at(x)
        row = model.my_scores.get(lid)
        played = row is not None
        nf = played and 'NF' in (row['modifiers'] or '')
        contrib = [(p, p.sim, p.residuals[lid]) for p in model.similar if lid in p.residuals]
        W = sum(w for _, w, _ in contrib)
        r_me = None
        if played and not nf and lid in me.residuals:   # 残差も下げた曲線から測り直す（★の範囲の中なら同じ値）
            rs = model.eff_stars(row['stars'], lb['mode'])
            r_me = me.residuals[lid] + ((float(me.curve(rs)) - base_at(rs)) if rs else 0.0)
        b = beta if r_me is not None else 0.0
        # 混ぜた上振れ = (β·自分 + Σ w·似ている人) / (β + Σw + 控えめにする重み)。控えめにする重みは「上振れ0」の仮の1票
        den = b + W + prior
        mix = ((b * r_me if b else 0.0) + sum(w * r for _, w, r in contrib)) / den if den > 0 else 0.0
        est = base + mix
        # 推定の内訳（精度の割合）: base（曲線）＋ mix（混ぜた上振れ）＋ 直しごとに足した量（r1・r2・r3・r5・r4。付いた物だけ）＋
        # cap（精度の上限で切った量）＋ clip（0〜ACC_MAX で切った量。どちらも切った時だけ）＝ est_acc。
        # r1Skipped は R1 の制限（FC_SKIP_BELOW）で足さなかった量（合計には入らない）
        parts = {'base': base, 'mix': mix}
        flags = []
        if drop > 0 and hi is not None and x > hi:
            flags.append('beyond')
        if nf:
            flags.append('nf')
        if r_me is None:
            # R2: 自分の結果が無い曲は、pass が基準を超える分だけ下げる
            d = unplayed_pass_drop(lb['pass_rating'], x, p0, hi, u)
            if d > 0:
                est -= d
                parts['r2'] = -d
                flags.append('pass')
            # R5: 同じく、tech が基準（クリアしてきた曲の tech の中央）を超える分だけ下げる
            d = unplayed_tech_drop(lb['tech_rating'], t0, ut)
            if d > 0:
                est -= d
                parts['r5'] = -d
                flags.append('tech')
        elif first_ts is not None:
            # R3: その譜面をクリアした日の数で直す
            adj = clear_days_adj(*clear_days(attempts.get(lid, []), row, first_ts), est)
            if adj:
                est += adj
                parts['r3'] = adj
                flags.append('cleared1' if adj > 0 else 'cleared3')
        if played and not nf and row['fc_acc'] and row['acc'] and ((row['missed_notes'] or 0) + (row['bad_cuts'] or 0)) > 0:
            # R1: FC との差の上乗せは、その pass で普通の量より多いミスの分だけ。前の自己ベストが曲線より大きく下の曲には足さない
            extra = (row['fc_acc'] - row['acc']) - (model.fc_norm(lb['pass_rating']) if model.fc_norm else 0.0)
            if extra >= FC_GAP_MIN:
                add = s['fc_gap_factor'] * extra
                if r_me is not None and r_me < FC_SKIP_BELOW:
                    parts['r1Skipped'] = add
                else:
                    est += add
                    parts['r1'] = add
                    flags.append('fc')
        if r_me is not None and R4_PB_WEIGHT:
            # R4: 前の自己ベストとの間に寄せる（ほかの直しの後）
            d = R4_PB_WEIGHT * (row['acc'] - est)
            if d:
                est += d
                parts['r4'] = d
                if abs(d) >= PB_FLAG_MIN:
                    flags.append('pb')
        # 精度の上限（近い★の、ほかの曲の自分の一番＋難しさの段階の余裕 acc_cap_margin）。推定の幅は上限で切る前の推定を中心にし、
        # 上限より上を上限に寄せる
        top, cap_lid, cap_n, cap_w = model.ceiling(lb['stars'], lb['mode'], lid) if model.ceiling else (None, None, 0, None)
        overall = False
        if top is None and model.few_similar and model.ceiling:
            # 似ている人が足りない人はスコアが少なく窓の曲が足りないことが多いので、同じモードのほかの曲の自分の一番で切る
            top, cap_lid = model.ceiling.overall(lb['mode'], lid)
            overall = top is not None
        cap = min(ACC_MAX, top + cap_margin) if top is not None else None
        center = min(ACC_MAX, max(0.0, est))
        if cap is not None and est > cap:
            parts['cap'] = cap - est
            est = cap
        raw = est
        est = min(ACC_MAX, max(0.0, est))
        if est != raw:
            parts['clip'] = est - raw
        parts = {k: round(float(v), PARTS_DIGITS) for k, v in parts.items()}
        # 確からしさ: 自分の結果がある曲は 1（推定は自分の結果が中心で、自分の記録では、似ている人が3人未満の曲も
        # 自己ベストを超える割合の見込みが同じように当たっていた）。無い曲は似ている人の重みの合計で決め、3人未満なら半分
        if r_me is not None:
            conf = 1.0
        else:
            conf = 1 - math.exp(-W / CONF_W0)
            if len(contrib) < s['min_similar_on_map']:
                conf *= 0.5
                flags.append('few')
            if model.few_similar:
                conf = max(conf, CONF_FLOOR)
        if played and _stalled(row, attempts.get(lid, []), now, s):
            conf *= 0.5
            flags.append('stall')
        pp_at = pp_func_of_row(lb)
        est_pp = pp_at(est)
        cur_pp = (row['pp'] or 0.0) if played else 0.0
        # その日のうちにクリアできる見込み（前にクリアした曲は高め・最後にクリアした後にクリアできなかった日が続いている曲は低め）。
        # 並び順の値に掛ける（増える総PPの表示はそのまま）
        p_clear, fails = None, None
        if model.clear and lb['pass_rating'] is not None:
            hard, soft = (model.fails or {}).get(lid, (0, 0))
            k = C.fail_weight(hard, soft)
            p_clear = model.clear(lb['pass_rating'], played and not nf, k)
            if k > 0 and model.clear.c:   # 下げる前の見込みも出す（開いた欄の説明）
                fails = {'days': hard + soft, 'soft': soft, 'base': model.clear(lb['pass_rating'], played and not nf)}
        clear_mul = 1 - cf * (1 - p_clear) if p_clear is not None else 1.0
        # 自己ベストが無い曲の挑戦の記録（BeatLeader にはクリアしたプレイしかスコアが残らない。挑戦したがクリアしていない曲を
        # 画面で「未クリア」と分けるため）。練習は数えない
        tried = None
        tries = [a for a in attempts.get(lid, []) if a['end_type'] in (1, 2, 3, 4) and a['timeset']] if not played else []
        if tries:
            last = max(tries, key=lambda a: a['timeset'])
            tried = {'n': len(tries), 'clears': sum(1 for a in tries if a['end_type'] == 1 and 'NF' not in (a['modifiers'] or '')),
                     'at': last['timeset'], 'acc': last['acc'], 'end': last['end_type']}
        # 増える総PP: 次に遊んだ日の一番のぶれ（AccDist）で取れた時の増えの期待値。p_up は自己ベストの精度を超える見込み
        # （NF だけの曲・未プレイの曲は None）、gain_up は超えた時の増えの平均
        best = row['acc'] if played and not nf else None
        dist = AccDist(center, r_me is not None)
        p_up, g = expected_gain(dist, best, lambda x: max(0.0, gain.after(pp_at(x), cur_pp) - gain.total), cap)
        gain_up = g / p_up if g > 0 else 0.0
        # 精度の上限: 画面の説明用に、上限・その精度の譜面・窓のほかの曲の数・推定の幅のうち上限より上だった割合（over）を出す
        acc_cap = None
        if cap is not None:
            over = 1.0 - dist.cdf(cap)
            h = lb_by.get(cap_lid) or {}
            acc_cap = {'acc': cap, 'top': top, 'margin': cap_margin, 'song': h.get('song_name'), 'difficulty': h.get('difficulty'), 'stars': h.get('stars'),
                       'n': cap_n, 'window': None if overall else cap_w, 'over': over}   # window は使った窓の幅（±★）。None なら同じモードの全部の曲の一番
            if (best and best >= cap) or over >= CAP_FLAG_SHARE:
                flags.append('cap')
        # 上がる国内順位: 今の自分の PP より上で、増えた後の PP（期待値の分）以下の人の数
        up = 0
        if g > 0 and len(others_pp):
            up = int(np.searchsorted(others_pp, me.pp + g, side='right') - np.searchsorted(others_pp, me.pp, side='right'))
        top = sorted(contrib, key=lambda c: -c[1])[:3]
        out.append({
            'leaderboard_id': lid, 'kind': 'played' if played else 'unplayed', 'hash': lb['hash'], 'song': lb['song_name'],
            'mapper': lb['mapper'], 'difficulty': lb['difficulty'], 'mode': lb['mode'], 'stars': lb['stars'], 'duration': lb['duration'],
            'upload_time': lb['upload_time'], 'base_acc': base, 'est_acc': est, 'est_pp': est_pp, 'parts': parts,
            # 譜面の特徴（一覧で「これは無理」が分かるように出す。pass はクリアの見込みにも使う）
            'notes': lb['notes'], 'nps': lb['nps'], 'njs': lb['njs'], 'bpm': lb['bpm'], 'bombs': lb['bombs'], 'walls': lb['walls'],
            'peak_ebpm': lb['peak_ebpm'], 'tags': lb['tags'],
            'acc_rating': lb['acc_rating'], 'pass_rating': lb['pass_rating'], 'tech_rating': lb['tech_rating'],
            'best_acc': row['acc'] if played else None, 'best_pp': cur_pp if played else None,
            'best_time': row['timeset'] if played else None, 'replay': bool(played and row.get('score_id')),
            # 自己ベストのプレイの詳しい情報（開いた欄に出す。JD はリプレイの頭から読む＝serve.py が足す）
            'score_id': row.get('score_id') if played else None, 'replay_url': row.get('replay_url') if played else None,
            'best_mods': row['modifiers'] if played else None, 'best_fc': bool(row['full_combo']) if played else None,
            'best_miss': ((row['missed_notes'] or 0) + (row['bad_cuts'] or 0)
                          if played and (row['missed_notes'] is not None or row['bad_cuts'] is not None) else None),
            'play_count': row['play_count'] if played else None, 'last_try': row['last_try_time'] if played else None,
            'tried': tried, 'fails': fails,
            'gain': g, 'p_up': p_up if best else None, 'gain_up': gain_up, 'acc_cap': acc_cap,
            'rank_up': up, 'confidence': conf, 'clear': p_clear,
            'score': g * conf * clear_mul, 'flags': flags, 'similar_n': len(contrib),
            'refs': [{'id': p.player_id, 'name': p.name, 'acc': p.acc.get(lid), 'residual': r, 'sim': w,
                      'mods': p.mods.get(lid, '')} for p, w, r in top],
        })
    out.sort(key=lambda c: -c['score'])
    return out
