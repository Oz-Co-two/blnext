# ステップ1〜2: 曲線・残差・似ている人（docs/spec.md の「おすすめロジック」）。取得の直後と、計算の設定を変えた時に作り直す。
# DB から読むだけで、画面にも pywebview にも依存しない
import math
from dataclasses import dataclass, field

import numpy as np

from . import clear as C
from .curve import fit, time_weights

# 「その pass で普通のミスの量」（FC との差の上乗せの基準。recommend.py の R1）を求める pass の幅（±）と、その幅に要る自己ベストの数
FC_NORM_WINDOW = 0.5
FC_NORM_MIN = 10
# モードごとの★の換算（2026-10-29）: ワンセイバーの★は自分には高めに付いていて、モードを混ぜた1本の曲線より自分の記録が平均 +3.5%
# （★が高いほど開く）上にあった。一番スコアの多いモードを基準に、ほかのモードの★に掛ける倍率を、基準のモードの曲線に一番合う
# （曲線と同じ重みでの二乗の和が一番小さい）値に決め、換算した★で自分の曲線・残差・★の範囲の上端を求める（推定もその★で読む）。
# 比較対象の人は換算しない（ほとんどの人はワンセイバーのスコアが無い）。根拠は 作者の調べ「ワンセイバーの★」
MODE_MIN_SCORES = 10                                     # 倍率を求めるのに要る、そのモードのスコアの数（足りなければ換算しない）
MODE_SCALES = np.round(np.arange(0.5, 1.5001, 0.01), 2)   # 倍率の候補
# 精度の上限（2026-11-01。利用者の要望「★5台の曲で 95% 出たら、そこから上に上がるのはなかなか難しい」。recommend.py）:
# 近い★（同じモードで★ ±CEIL_WINDOW）の、ほかの曲の自分の一番の精度を、その曲で取れる精度の上限にする。★の低い曲ほど上限が高い。
# 窓のほかの曲が CEIL_MIN より少なければ上限を付けない（一番が数曲の中の一番になり、低く出すぎる。ワンセイバーは★の帯ごとに
# 数曲しか無い）。★はモードの換算をしない（同じモードの中で比べる。全モードを換算した★でまとめると、ワンセイバーで飛び抜けた曲が
# 両手の上限を引き上げる）
CEIL_WINDOW = 0.5
CEIL_MIN = 8   # 2026-11-01 に 5 → 8（ワンセイバーは窓が 5〜13 曲で、5 だと見込みが約4割減った。両手は変わらない）
# ±CEIL_WINDOW のほかの曲が CEIL_MIN より少なければ、CEIL_WINDOW ずつここまで広げる（2026-11-12）。遊んだ曲の少ない★の帯（上位の人の易しい★・
# 誰でも一番難しい★）で上限が付かず、似ている人の値で 99% 台に押し上げられた易しい未プレイの曲が、100% 近くの PP の急な所で 1曲だけで
# 総PP を大きく増やす見込みになっていた（その★の近くで自分が出した一番より 1% 近く上）。同じモードの一番で切る形は、易しい曲ほど精度が
# 高いのでゆるすぎた。国内上位の人と作者で確かめると、広げた上限を超えていた自己ベストは余裕 +0.5% 以上で 0件、隠して当てた外れは変わった曲で
# 小さくなり、リストが変わったのはその人の着実だけだった。±1.5 まで広げても同じだった（作者の調べ「世界ランクの比較対象」の追記）
CEIL_WINDOW_MAX = 1.0
# 似ている人が足りない人（2026-11-06。作者の調べ「ほかの人のデータ」）: 共通の曲が min_overlap 曲に届く人がいないと似ている人が 0 人に
# なり、未プレイの曲の確からしさが全部 0 で、並び順の値も 0（データベースの順に選ばれ、誰にでも同じ曲が入る）。自分のスコアが 30曲未満の人と、
# 順位の近い人のスコアが少ない人（国内 1600位台の周りは中央 15曲）に起きる。この人数より少ない人だけ、確からしさの下限（recommend.CONF_FLOOR）・
# 精度の上限の代わり（AccCeiling.overall）・自分の★より上を入れない（assemble.py）を効かせる。似ている人がいる人のリストは変えない
FEW_SIMILAR = 5


def pick_pool(rows, n):
    """比較対象の候補（順位の近い上下の人。players の行）から、ランクのスコアの多い順に n 人を選ぶ。取得（fetch/refresh.py）と推定（build）で同じ人にする。
    共通の曲が多い人を選ぶと、順位の近い順に減らすより当たりが落ちない（作者の調べ「比較対象の人数」）。スコアの数が分からない人は前に置く（今までどおり取る）"""
    return sorted(rows, key=lambda p: (p['ranked_play_count'] is not None, -(p['ranked_play_count'] or 0), p['player_id']))[:n]


class FcNorm:
    """pass → 自分の自己ベスト（ランク・NF を除く）の「FC した場合の精度との差」（fc_acc − acc。FC は 0）の、pass ±FC_NORM_WINDOW
    の中央。その幅の自己ベストが FC_NORM_MIN より少なければ 0（普通の量が分からない時は、今までどおり差の全部を上乗せの元にする）。
    自分の記録では、この差は pass とともに増える（pass 2 で 1.4%・3 で 2.8%・4 で 4.4%）＝偶然のミスではなく、次に遊んでも出る分"""
    def __init__(self, pairs):
        pairs = sorted(pairs)
        self.p = np.array([p for p, _ in pairs], dtype=float)
        self.g = np.array([g for _, g in pairs], dtype=float)

    def __call__(self, pass_rating):
        if pass_rating is None:
            return 0.0
        i = int(np.searchsorted(self.p, pass_rating - FC_NORM_WINDOW, side='left'))
        j = int(np.searchsorted(self.p, pass_rating + FC_NORM_WINDOW, side='right'))
        return float(np.median(self.g[i:j])) if j - i >= FC_NORM_MIN else 0.0


def fc_norm(store, player_id):
    """FcNorm を DB の自分のスコアから作る（fc_acc の無いスコアは使わない）"""
    rows = store.q('SELECT s.acc, s.fc_acc, l.pass_rating FROM scores s JOIN leaderboards l ON l.leaderboard_id=s.leaderboard_id '
                   "WHERE s.player_id=? AND l.status=3 AND instr(COALESCE(s.modifiers, ''), 'NF')=0 AND s.acc > 0 "
                   'AND s.fc_acc IS NOT NULL AND l.pass_rating IS NOT NULL', (player_id,))
    return FcNorm([(r['pass_rating'], max(0.0, r['fc_acc'] - r['acc'])) for r in rows])


class AccCeiling:
    """(★, モード) → 精度の上限: 同じモードで★ ±CEIL_WINDOW（曲が足りなければ CEIL_WINDOW_MAX まで広げる）の、ほかの曲の自分の一番の精度。
    rows は (モード, ★, 精度, 譜面) の並び"""
    def __init__(self, rows):
        by = {}
        for mode, stars, acc, lid in rows:
            by.setdefault(mode or '', []).append((stars, acc, lid))
        self.by = {}
        for mode, xs in by.items():
            xs.sort(key=lambda x: x[0])
            self.by[mode] = (np.array([x[0] for x in xs], dtype=float), [x[1] for x in xs], [x[2] for x in xs])

    def __call__(self, stars, mode, exclude=None):
        """(上限, その精度の譜面, 窓のほかの曲の数, 使った窓の幅 ±★)。exclude はその曲自身（窓から除く）。±CEIL_WINDOW のほかの曲が
        CEIL_MIN より少なければ CEIL_WINDOW ずつ CEIL_WINDOW_MAX まで広げ、それでも少なければ上限・譜面・幅は None（数は一番広い窓の数）"""
        d = self.by.get(mode or '')
        if d is None or not stars:
            return None, None, 0, None
        s, acc, ids = d
        w = CEIL_WINDOW
        while True:
            i = int(np.searchsorted(s, stars - w, side='left'))
            j = int(np.searchsorted(s, stars + w, side='right'))
            xs = [(acc[k], ids[k]) for k in range(i, j) if ids[k] != exclude]
            if len(xs) >= CEIL_MIN:
                top = max(xs, key=lambda x: x[0])
                return float(top[0]), top[1], len(xs), w
            if w >= CEIL_WINDOW_MAX - 1e-9:
                return None, None, len(xs), None
            w = min(CEIL_WINDOW_MAX, w + CEIL_WINDOW)

    def overall(self, mode, exclude=None):
        """(同じモードのほかの曲の自分の一番, その譜面)。窓の曲が足りない時の上限の代わり（似ている人が足りない人だけ。recommend.py）。
        ほかの曲が無ければ (None, None)"""
        d = self.by.get(mode or '')
        xs = [(a, i) for a, i in zip(d[1], d[2]) if i != exclude] if d else []
        if not xs:
            return None, None
        top = max(xs, key=lambda x: x[0])
        return float(top[0]), top[1]


def acc_ceiling(store, player_id):
    """AccCeiling を DB の自分のスコア（ランクの譜面・NF を除く）から作る。★は曲線と同じスコアの★（モディファイアで補正した★）"""
    rows = store.q('SELECT s.leaderboard_id, s.acc, s.stars, l.mode FROM scores s JOIN leaderboards l ON l.leaderboard_id=s.leaderboard_id '
                   "WHERE s.player_id=? AND l.status=3 AND instr(COALESCE(s.modifiers, ''), 'NF')=0 AND s.acc > 0 AND s.stars > 0",
                   (player_id,))
    return AccCeiling([(r['mode'], r['stars'], r['acc'], r['leaderboard_id']) for r in rows])


@dataclass
class Player:
    player_id: str
    name: str
    country_rank: int
    pp: float
    style: tuple            # (accPp, passPp, techPp)
    n_scores: int
    curve: object = None
    residuals: dict = field(default_factory=dict)   # leaderboard_id → 残差
    acc: dict = field(default_factory=dict)         # leaderboard_id → 精度
    mods: dict = field(default_factory=dict)        # leaderboard_id → モディファイア
    sim: float = None       # 類似度（共通曲が足りなければ None）
    overlap: int = 0
    corr: float = None
    style_sim: float = None


@dataclass
class Model:
    me: Player
    my_scores: dict          # leaderboard_id → 自分のスコアの行（DB の scores）
    pool: list               # 比較対象（Player）
    similar: list            # 似ている人（類似度の高い順・上位 top_similar 人）
    built_at: int
    pool_size: int = 0            # 選んだ人（pick_pool）のうちスコアを取った人＋範囲の中のスコアが少ないので取らなかった人（pool に入らない人も含む）
    skipped_few_scores: int = 0   # そのうち、スコアが min_scores より少なくて外した人（取らなかった人も含む）
    # クリアできるか（clear.py）: 自分がクリアしてきた譜面の範囲・その日にクリアできる見込み（ClearModel）・
    # 自分の★の範囲の上端・比較対象の人の曲線の★の上の方での下がり方（★1 あたり。ふつうは負）・
    # 譜面ごとの今のクリアできなかった日（最後にクリアした日の後の、失敗した日・やり直しかやめただけの日。clear.clear_history）
    range: dict = field(default_factory=dict)
    clear: object = None
    star_hi: float = None
    pop_slope: float = None
    fails: dict = field(default_factory=dict)
    # その pass で普通のミスの量（FcNorm。FC との差の上乗せは、これより多い分だけにする）
    fc_norm: object = None
    # モード → 自分の曲線を読む時に★に掛ける倍率（star_scales。無いモードは 1）
    star_scale: dict = field(default_factory=dict)
    # 精度の上限（AccCeiling。近い★の、ほかの曲の自分の一番。None なら上限を付けない）
    ceiling: object = None
    # 似ている人が足りない人か（FEW_SIMILAR）
    few_similar: bool = False

    def eff_stars(self, stars, mode):
        """自分の曲線を読む★（モードの換算をした★）"""
        return stars * self.star_scale.get(mode or '', 1.0) if stars else stars


def _usable(row):
    """曲線・残差に使うスコアか（NF は PP が0で、精度も比べられないので使わない）"""
    return row['acc'] and row['acc'] > 0 and row['stars'] and row['stars'] > 0 and 'NF' not in (row['modifiers'] or '')


def _residuals(curve, rows, stars=None):
    """譜面 → 残差（精度 − 曲線の値）。stars があれば曲線をその★（譜面 → ★）で読む"""
    rows = [r for r in rows if _usable(r)]
    if not rows or curve is None:
        return {}
    e = curve(np.array([stars[r['leaderboard_id']] if stars else r['stars'] for r in rows]))
    return {r['leaderboard_id']: float(r['acc'] - v) for r, v in zip(rows, e)}


def star_scales(rows, modes, weights):
    """モード → ★に掛ける倍率（MODE_MIN_SCORES 以上のスコアがある、基準でないモードだけ）。rows は曲線に使うスコア、
    modes は譜面 → モード、weights は rows と同じ並びの重み。基準は一番スコアの多いモードで、その曲線に一番合う倍率を選ぶ"""
    by = {}
    for r, w in zip(rows, weights):
        by.setdefault(modes.get(r['leaderboard_id']) or '', []).append((r['stars'], r['acc'], w))
    if len(by) < 2:
        return {}
    ref = max(by, key=lambda m: len(by[m]))
    a = np.array(by[ref], dtype=float)
    cv = fit(a[:, 0], a[:, 1], a[:, 2])
    if cv is None:
        return {}
    out = {}
    for m, v in by.items():
        if m == ref or len(v) < MODE_MIN_SCORES:
            continue
        a = np.array(v, dtype=float)
        err = [float(np.sum(a[:, 2] * (a[:, 1] - cv(k * a[:, 0])) ** 2)) for k in MODE_SCALES]
        out[m] = float(MODE_SCALES[int(np.argmin(err))])
    return out


def _cos(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return None
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else None


def build(store, settings, now):
    """自分と比較対象の曲線・残差と、似ている人を求める。データが無ければ None"""
    s = settings.values()
    me_row = store.q1('SELECT * FROM players WHERE player_id=? AND is_self=1', (s['player_id'],))
    if not me_row or not me_row['country_rank']:
        return None
    my_rows = store.q('SELECT * FROM scores WHERE player_id=?', (me_row['player_id'],))
    if not my_rows:
        return None
    use = [r for r in my_rows if _usable(r)]
    w = time_weights([r['timeset'] or now for r in use], now, s['self_half_life_days'])
    modes = {r['leaderboard_id']: r['mode'] for r in store.q(
        'SELECT l.leaderboard_id, l.mode FROM leaderboards l JOIN scores s ON s.leaderboard_id=l.leaderboard_id WHERE s.player_id=?',
        (me_row['player_id'],))}
    scale = star_scales(use, modes, w)
    eff = {r['leaderboard_id']: r['stars'] * scale.get(modes.get(r['leaderboard_id']) or '', 1.0) for r in use}
    me_curve = fit([eff[r['leaderboard_id']] for r in use], [r['acc'] for r in use], w)
    me = Player(me_row['player_id'], me_row['name'], me_row['country_rank'], me_row['pp'],
                (me_row['acc_pp'], me_row['pass_pp'], me_row['tech_pp']), len(use), me_curve)
    me.residuals = _residuals(me_curve, use, eff)
    me.acc = {r['leaderboard_id']: r['acc'] for r in my_rows}

    ranking_at = int(store.meta('ranking_at') or 0)
    rank = me_row['country_rank']
    in_range = (me_row['country'], rank - s['pool_above'], rank + s['pool_below'], ranking_at, me_row['player_id'])
    cands = store.q('SELECT * FROM players WHERE country=? AND country_rank BETWEEN ? AND ? AND seen_at=? AND player_id<>?', in_range)
    prow = sorted((p for p in pick_pool(cands, s['pool_max']) if p['scores_fetched_at'] is not None), key=lambda p: p['country_rank'])
    # 順位表のランクのスコアの数で足りないと分かって取らなかった人（refresh.py の _fetch_pool）も、スコアが少なくて外した人に数える
    not_fetched = store.q1('SELECT COUNT(*) AS n FROM players WHERE country=? AND country_rank BETWEEN ? AND ? AND seen_at=? AND player_id<>? '
                           'AND scores_fetched_at IS NULL AND ranked_play_count<?', in_range + (s['min_scores'],))['n']
    ids = [p['player_id'] for p in prow]
    by = {i: [] for i in ids}
    if ids:
        for r in store.q('SELECT player_id, leaderboard_id, acc, stars, modifiers FROM scores WHERE player_id IN (%s)'
                         % ','.join('?' * len(ids)), ids):
            by[r['player_id']].append(r)
    pool, few, slopes = [], 0, []
    mine = me.residuals
    for p in prow:
        rows = [r for r in by[p['player_id']] if _usable(r)]
        if len(rows) < s['min_scores']:
            few += 1
            continue
        pl = Player(p['player_id'], p['name'], p['country_rank'], p['pp'], (p['acc_pp'], p['pass_pp'], p['tech_pp']), len(rows))
        pl.curve = fit([r['stars'] for r in rows], [r['acc'] for r in rows])
        slopes.append(C.upper_slope(pl.curve, [r['stars'] for r in rows]))
        pl.residuals = _residuals(pl.curve, rows)
        pl.acc = {r['leaderboard_id']: r['acc'] for r in rows}
        pl.mods = {r['leaderboard_id']: r['modifiers'] for r in rows if r['modifiers']}
        common = [k for k in pl.residuals if k in mine]
        pl.overlap = len(common)
        pl.style_sim = _cos(me.style, pl.style)
        if pl.overlap >= s['min_overlap']:
            a = np.array([mine[k] for k in common]); b = np.array([pl.residuals[k] for k in common])
            if a.std() > 0 and b.std() > 0:
                pl.corr = float(np.corrcoef(a, b)[0, 1])
                # 共通曲が少ないほど 0 に近づける（sim = corr × n / (n + overlap_shrink)）
                pl.sim = pl.corr * pl.overlap / (pl.overlap + s['overlap_shrink'])
        pool.append(pl)
    similar = sorted([p for p in pool if p.sim is not None and p.sim > 0], key=lambda p: -p.sim)[: s['top_similar']]
    # クリアできるか: 自己ベストのある譜面（NF を除く）は、その日にクリアしたとみなす（挑戦の記録より前のクリアもあるため）
    cleared_at = {r['leaderboard_id']: r['timeset'] for r in my_rows if r['timeset'] and 'NF' not in (r['modifiers'] or '')}
    days, fails = C.clear_history(store, cleared_at, now - C.ATTEMPT_DAYS * 86400)
    return Model(me, {r['leaderboard_id']: r for r in my_rows}, pool, similar, now, len(prow) + not_fetched, few + not_fetched,
                 range=C.map_range(store, me.player_id), clear=C.fit_clear(days),
                 star_hi=C.star_hi(list(eff.values())), pop_slope=C.pop_slope(slopes), fails=fails,
                 fc_norm=fc_norm(store, me.player_id), star_scale=scale, ceiling=acc_ceiling(store, me.player_id),
                 few_similar=len(similar) < min(FEW_SIMILAR, s['top_similar']))


def save_similarities(store, model):
    """似ている人の計算結果を残す（画面の表示と振り返り用）"""
    with store.tx():
        for p in model.pool:
            store.conn.execute('INSERT OR REPLACE INTO similarities(computed_at, player_id, sim, overlap, style_sim) VALUES(?,?,?,?,?)',
                               (model.built_at, p.player_id, p.sim, p.overlap, p.style_sim))
