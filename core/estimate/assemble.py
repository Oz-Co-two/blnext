# プレイリストの組み立て（docs/spec.md の「プレイリストの組み立て」）。推定の結果（recommend.estimate）から、条件に合わせて
# 曲を選ぶ。条件を変えたらここだけをやり直す（速い）。
#  1. フィルタ（古い譜面・★の範囲・未プレイ曲の pass の上限・モード・今後出さない・クールダウン・外した曲・
#     ほかのセットのリストの曲）
#     ★の範囲（star_min・star_max。セットごとの数字。上限は 0 で効かない・下限は 0 で自動＝auto_star_min）は、プレイ済みにも未プレイにも
#     かける。★は譜面の素の★（c['stars']）。
#     2026-10-25 まではプレイ済みの最高★＋余裕（unplayed_star_margin）を未プレイにだけかけていたが、最高★を全モードの曲で数えていた
#     （スタンダードのセットでもワンセイバーの★が基準になった）のと、たまたまクリアできた曲が上限を引き上げていたのでやめた。
#     値を選ぶ参考に、このセットのモードで最近クリアした曲の★（recent_stars）を filters に付ける
#     範囲の外の曲（clear.beyond。自分がクリアしてきた曲より難しい・ノーツが多い）は、1リストに challenge_max 曲まで（挑戦枠）
#     pass の上限（unplayed_pass_cap）と挑戦枠は、難しさの段階（core/settings.py の DIFFICULTY）がまとめて変える
#  2. 枠: 曲数 N とプレイ済みの比率から、プレイ済みの枠（四捨五入）と未プレイの枠
#  3. 固定した曲を先に入れる（その区分の枠を使う）
#  4. 枠ごとに並び順の値の高い順に取る（同じ曲の別難易度は1つまで）
#  5. 足りない枠はもう片方で埋める（比率より曲数を優先）。候補全体が足りなければあるだけ
#  6. プレイリスト内の並び: 基本の並び（order.default_order。プレイ済み → 新曲 → 未クリア → 挑戦、区分の中はクリアの見込みの高い順）
import time
from datetime import date

import numpy as np

from . import clear as C
from .order import default_order

RUNNERS_UP = 10      # 次点として見せる数
YEAR = 365.25 * 86400
RECENT_DAYS = 90     # ★の範囲の参考（recent_stars）にする「最近」
RECENT_P95_MIN = 5   # これより少ない曲数なら95%点は出さない（最高だけ）
# 初回の準備で入れる★の範囲（auto_star_ranges）。この日数の中の自己ベストが AUTO_STAR_RECENT_MIN 曲あればそれで決め、足りなければ全部の自己ベストで。
# それでも AUTO_STAR_MIN 曲（曲線が作れる最低の数）より少なければ入れない。少ない数でも入れるのは、入れないと★の差が付かないまま、
# 3つのリストの未プレイの枠が全部、自分がクリアしてきた★の一番上の曲になるため（国内 1800〜2100 位の人は中央 10曲ほど）
AUTO_STAR_DAYS = 180
AUTO_STAR_RECENT_MIN = 30
AUTO_STAR_MIN = 5
STREAK_DAYS = 21     # クールダウン（cooldown_ids）で「続けて遊んだ」と数える期間（最後に遊んだ日までの日数）
# ★の範囲の下限の自動（auto_star_min。star_min が 0 のセット。2026-11-08 に利用者と決めた）: そのモードのプレイ済みの曲で、自己ベストを
# 超えた時に増える総PP（gain_up）が AUTO_MIN_GAIN 以上の曲が AUTO_MIN_SHARE 以上になる一番低い★。★の幅 AUTO_WIDTH の窓を AUTO_STEP ずつ
# 上げて見る（窓の曲が AUTO_MIN_N より少なければ飛ばす）
AUTO_MIN_GAIN = 0.5
AUTO_MIN_SHARE = 0.5
AUTO_WIDTH = 0.5
AUTO_STEP = 0.1
AUTO_MIN_N = 5


def marks(store, preset):
    """利用者の印 {leaderboard_id: 'pin' | 'never'}。固定はそのセットの物、今後出さないは全セット共通（preset=''）"""
    out = {r['leaderboard_id']: r['mark'] for r in store.q("SELECT leaderboard_id, mark FROM user_marks WHERE preset=? AND mark='pin'",
                                                             (preset,))}
    out.update({r['leaderboard_id']: 'never' for r in store.q("SELECT leaderboard_id FROM user_marks WHERE mark='never'")})
    return out


def _played_stars(est, model, modes, now=None, days=None, no_mods=False):
    """このモードのプレイ済みの曲のうち、NF も SS も付いていない自己ベストの曲の★（譜面の素の★）。days を渡すと、自己ベストがその日数以内の曲だけ。
    no_mods なら、モディファイアが何か付いた自己ベストも数えない"""
    xs = []
    for c in est:
        if c['kind'] != 'played' or c['mode'] not in modes or not c.get('stars'):
            continue
        row = model.my_scores.get(c['leaderboard_id'])
        if not row or (days is not None and (not row.get('timeset') or now - row['timeset'] > days * 86400)):
            continue
        mods = set(filter(None, (row.get('modifiers') or '').split(',')))
        if mods if no_mods else mods & {'NF', 'SS'}:
            continue
        xs.append(float(c['stars']))
    return xs


def auto_star_ranges(est, model, modes, now=None):
    """初回の準備で難しさ違いのセットに入れる★の範囲 {段階: (下限, 上限)}（0 は効かない）。自分の自己ベストの★の分布から決める。
    入れないと、3つのリストの難しさの差が★に出ない（2026-11-05 の作者のデータ: 着実に未プレイの★8.16 が入り、チャレンジングの★の
    中央が標準より低かった。入れると着実 → 標準 → チャレンジングの順に★が並び、見込みは1割ほど下がるだけ）。
    元にするのはモディファイア無しの自己ベストだけ（範囲は素の★にかかるので、NA などで易しくして遊んだ曲を数えると高く出すぎる。
    全部 NA の人で着実が〜★7.6 になった）。少なければ None（入れない）。
    着実の下限の 0 は自動（auto_star_min）。標準・チャレンジングの下限まで自動にすると難しさの順が崩れる（2026-11-08 の作者のデータで、
    3つとも自動の下限 ★5.5 だと★の中央が 着実 6.29・標準 6.58・チャレンジング 6.26。今の形は 6.29・6.53・6.65）ので、その2つは入れる"""
    now = now or int(time.time())
    xs = _played_stars(est, model, modes, now, AUTO_STAR_DAYS, no_mods=True)
    if len(xs) < AUTO_STAR_RECENT_MIN:
        xs = _played_stars(est, model, modes, no_mods=True)
    if len(xs) < AUTO_STAR_MIN:
        return None
    p50, p75, p90, p95 = (float(x) for x in np.percentile(xs, [50, 75, 90, 95]))
    return {'steady': (0, round(p75, 1)), 'standard': (round(p50, 1), round(p90, 1)), 'challenge': (round(p75, 1), round(p95 + 0.5, 1))}


def auto_star_min(est, modes, star_max=0):
    """★の範囲の下限を自動で決める（star_min が 0 のセット）。{モード: 下限の★（決められなければ None＝下限なし）}。★は譜面の素の★。
    2026-11-08 に利用者の要望で入れた（「リストにあるから遊んで更新したのに点がほとんど伸びない」のを避けたい。上限は自分で決め、下限は
    点の入りやすい所を自動で）。自分の上位の曲の PP に届かない易しい曲は、更新しても総PP がほとんど増えない（作者の 11/08 のデータで、
    両手は★5 より下を更新しても 0PP・★5.5〜6 で半分の曲が +0.5PP 以上。ワンセイバーは曲が少ないので★4.5 から）。取得のたびに
    推定と一緒に決め直すので、上位の PP が上がると下限も上がる。上限（star_max。0 より大きい時）より AUTO_WIDTH は下にする"""
    out = {}
    for mode in modes:
        xs = sorted((float(c['stars']), (c.get('gain_up') or 0) >= AUTO_MIN_GAIN) for c in est
                    if c['kind'] == 'played' and c['mode'] == mode and c.get('stars'))
        line = None
        if xs:
            stars = [x for x, _ in xs]
            good = np.concatenate([[0], np.cumsum([g for _, g in xs])])
            k, k_end = int(np.floor(stars[0] / AUTO_STEP)), int(np.ceil(stars[-1] / AUTO_STEP))
            while k <= k_end:   # 窓 [k·STEP, k·STEP＋WIDTH)。★を 0.1 の整数で数えて、足し算のずれで窓がずれないように
                lo = k * AUTO_STEP
                i, j = int(np.searchsorted(stars, lo - 1e-9)), int(np.searchsorted(stars, lo + AUTO_WIDTH - 1e-9))
                if j - i >= AUTO_MIN_N and (good[j] - good[i]) >= AUTO_MIN_SHARE * (j - i):
                    line = round(lo, 1)
                    break
                k += 1
        if line is not None and star_max > 0:
            line = max(0.0, min(line, round(star_max - AUTO_WIDTH, 1))) or None
        out[mode] = line
    return out


def recent_stars(est, model, modes, now):
    """★の範囲を選ぶ参考: このセットのモードのプレイ済みの曲のうち、自己ベストが最近（RECENT_DAYS 日以内）で NF も SS も
    付いていない曲の★（譜面の素の★）の {max, p95, n}。曲数が少なければ p95 は None、1曲も無ければ None"""
    xs = _played_stars(est, model, modes, now, RECENT_DAYS)
    if not xs:
        return None
    return {'max': max(xs), 'p95': float(np.percentile(xs, 95)) if len(xs) >= RECENT_P95_MIN else None, 'n': len(xs)}


def _day(ts):
    return date.fromisoformat(C.day_key(ts))


def cooldown_ids(store, model, s, now):
    """クールダウンで今は出さない曲 {leaderboard_id: 'gap' | 'rest' | 'failRest'}。遊んだ日＝その譜面に挑戦の記録（練習を除く）が
    ある日（0時区切り。clear.day_key。リストの外で遊んだ日も、どのセットで出した曲でも数える）。伸びた・伸びないは問わない。
    2026-11-01 に利用者と決めた（それまでは「このセットで出力して遊んだのに伸びなかった曲を、直近 cooldown_runs 回の出力について
    外す」。同じ曲が何度も出て面白くない・3回ほど遊ぶとそれ以上伸びない、と利用者が感じていた）:
     - 再プレイ（NF でない自己ベストがある曲。初めてクリアした日から数える）: 最後に遊んだ日から replay_gap_days 日は出さない（gap。
       同じ曲が続けて出ないように）。最後に遊んだ日までの STREAK_DAYS 日に遊んだ日が replay_streak_days 日になったら、最後に
       遊んだ日から replay_rest_weeks 週は出さない（rest）
     - 初プレイ（まだクリアしていない曲）: 最後に挑戦した日までの STREAK_DAYS 日にクリアできなかった日が unplayed_fail_days 日に
       なったら、最後に挑戦した日から unplayed_rest_weeks 週は出さない（failRest）。間はあけない（覚えながらクリアを狙うため）
    根拠（自分の挑戦の記録・約1年。自己ベストは挑戦の記録のクリアから日ごとに組み立て直した。作者のプレイの記録 の 2026-11-01）:
    再プレイでクリアした日に自己ベストを超えた割合は、その前の3週間に遊んだ日が 0日 57%・1日 43%・2日 27%・3日以上 25%。前に
    遊んでからの間が 1〜13日では 31〜34% でほとんど変わらず、14〜29日 42%・30〜89日 53%（間をあけるのは点より飽きないため）。
    初プレイは 1日目 82%・2日目 40%・3日目 42%・4日目以降 0〜29% がその日にクリアした"""
    gap = s['replay_gap_days']
    streak, rest = s['replay_streak_days'], s['replay_rest_weeks'] * 7
    fails, fail_rest = s['unplayed_fail_days'], s['unplayed_rest_weeks'] * 7
    if not (gap or (streak and rest) or (fails and fail_rest)):
        return {}
    today = _day(now)
    days = {}   # 譜面 → {日: その日に NF でないクリアがあったか}
    for a in store.q('SELECT leaderboard_id, timeset, end_type, modifiers FROM attempts WHERE end_type IN (1, 2, 3, 4) AND timeset>0'):
        d = days.setdefault(a['leaderboard_id'], {})
        k = _day(a['timeset'])
        d[k] = d.get(k, False) or (a['end_type'] == 1 and 'NF' not in (a['modifiers'] or '').split(','))
    out = {}
    for lid, dd in days.items():
        row = model.my_scores.get(lid)
        if row and 'NF' not in (row.get('modifiers') or '').split(','):
            # 初めてクリアした日（挑戦の記録の始まりより前の自己ベストなら、記録の全部の日が再プレイ）から後の日
            first = min([k for k in dd if dd[k]] + ([_day(row['timeset'])] if row.get('timeset') else []), default=None)
            ds = sorted(k for k in dd if first is None or k >= first)
            if not ds:
                continue
            last = ds[-1]
            near = sum(1 for k in ds if (last - k).days < STREAK_DAYS)
            if streak and near >= streak and (today - last).days < rest:
                out[lid] = 'rest'
            elif (today - last).days < gap:
                out[lid] = 'gap'
        else:
            ds = sorted(k for k in dd if not dd[k])
            if not ds:
                continue
            last = ds[-1]
            near = sum(1 for k in ds if (last - k).days < STREAK_DAYS)
            if fails and near >= fails and (today - last).days < fail_rest:
                out[lid] = 'failRest'
    return out


def assemble(est, model, settings, store, session=None, now=None, other_songs=None):
    """組み立てた結果の dict。settings はセットから見た設定（Settings.view。Settings なら画面で開いているセット）。
    session は画面での一時的な操作 {removed: set, added: set}（そのセットの物。アプリを閉じるまで）。
    other_songs は候補から外す曲の hash の集合（exclude_other_sets がオンの時の、ほかのセットの保存したリストの曲。serve.py が渡す。
    hash で比べるので同じ曲の別難易度も外す）。固定した曲・入れ替えで選んだ曲は、どのフィルタよりも優先して入れる"""
    s = settings.values()
    preset = getattr(settings, 'preset', None) or s['active_preset']
    now = now or int(time.time())
    session = session or {'removed': set(), 'added': set()}
    other_songs = other_songs or set()
    mk = marks(store, preset)
    cool = cooldown_ids(store, model, s, now)
    modes = set(s['modes'])
    by_id = {c['leaderboard_id']: c for c in est}
    star_min, star_max = s['star_min'], s['star_max']   # ★の範囲（上限は 0 で効かない。下限は 0 で自動＝auto_star_min）
    auto_min = auto_star_min(est, modes, star_max) if star_min <= 0 else None   # {モード: 下限}（下限を数字で決めたセットは None）
    lower = (lambda c: star_min) if auto_min is None else (lambda c: auto_min.get(c['mode']) or 0)
    age_lim = now - s['max_map_age_years'] * YEAR if s['max_map_age_years'] > 0 else None
    rng = model.range or {}
    # 未プレイの曲の pass の上限（自分がクリアしてきた曲の pass の75%点・95%点）。クリアしてきた曲が少なくて分布が無ければ絞らない
    pass_cap = (rng.get('pass') or {}).get(s['unplayed_pass_cap']) if s['unplayed_pass_cap'] != 'none' else None
    min_clear = s['min_clear'] / 100   # クリアの見込みの下限（0 で絞らない）
    # 似ている人が足りない人（model.FEW_SIMILAR）は、未プレイの曲を自分がクリアしてきた★の一番上までにする（増える総PP は★が高いほど大きく、
    # 確からしさで下げられないので、14曲の人の着実に★10 の未プレイが入った）。曲線と同じ換算した★で比べる
    star_top = model.me.curve.hi if model.few_similar and model.me.curve is not None else None

    def why_out(c):
        """候補から外す理由（無ければ None）"""
        lid = c['leaderboard_id']
        if mk.get(lid) == 'never':
            return 'never'
        if lid in session['removed']:
            return 'removed'
        if c.get('hash') in other_songs:
            return 'other'
        if c['mode'] not in modes:
            return 'mode'
        if lid in cool:
            return 'cooldown'
        if age_lim and c['upload_time'] and c['upload_time'] < age_lim and (
                (c['kind'] == 'unplayed' and s['age_filter_unplayed']) or (c['kind'] == 'played' and s['age_filter_played'])):
            return 'age'
        if c.get('stars') is not None and ((star_max > 0 and c['stars'] > star_max) or c['stars'] < lower(c)):
            return 'stars'
        if star_top is not None and c['kind'] == 'unplayed' and c.get('stars') and model.eff_stars(c['stars'], c['mode']) > star_top:
            return 'starTop'
        if c['kind'] == 'unplayed' and pass_cap is not None and c.get('pass_rating') is not None and c['pass_rating'] > pass_cap:
            return 'pass'
        if min_clear > 0 and c.get('clear') is not None and c['clear'] < min_clear:
            return 'clear'
        if c['gain'] <= 0:
            return 'nogain'
        return None

    why = {c['leaderboard_id']: why_out(c) for c in est}
    n = s['playlist_size']
    slots = {'played': int(np.floor(n * s['played_ratio'] / 100 + 0.5))}
    slots['unplayed'] = n - slots['played']
    hard = {c['leaderboard_id'] for c in est if C.beyond(rng, c)}   # 範囲の外（挑戦枠に数える）
    fixed = [l for l, m in mk.items() if m == 'pin'] + sorted(session['added'])

    def pick(block):
        """3〜5 の選び方。block の曲は自動では選ばない。(選んだ曲, その id, その曲の hash, 候補)"""
        chosen, chosen_ids, songs = [], set(), set()

        def take(c, reason):
            chosen.append({**c, 'pinned': mk.get(c['leaderboard_id']) == 'pin', 'pick': reason,
                           'challenge': c['leaderboard_id'] in hard})
            chosen_ids.add(c['leaderboard_id'])
            songs.add(c['hash'])

        # 3. 固定した曲・入れ替えで選んだ曲（フィルタ・挑戦枠より優先。「今後出さない」と外した曲だけは入れない）
        for lid in fixed:
            c = by_id.get(lid)
            if c and lid not in chosen_ids and mk.get(lid) != 'never' and lid not in session['removed']:
                take(c, 'pin' if mk.get(lid) == 'pin' else 'added')
        used = {k: sum(1 for c in chosen if c['kind'] == k) for k in ('played', 'unplayed')}
        pool = [c for c in est if why[c['leaderboard_id']] is None and c['leaderboard_id'] not in chosen_ids]

        def fill(kind, room):
            for c in pool:
                if room <= 0 or len(chosen) >= n:   # 固定した曲が片方の枠を超えていても、合わせて曲数より多くしない
                    break
                if c['kind'] != kind or c['leaderboard_id'] in chosen_ids or c['leaderboard_id'] in block:
                    continue
                if s['one_diff_per_song'] and c['hash'] in songs:
                    continue
                take(c, 'auto')
                room -= 1
            return room

        # 4. 枠ごとに上から
        left = {k: fill(k, max(0, slots[k] - used[k])) for k in ('played', 'unplayed')}
        # 5. 足りない枠をもう片方で埋める
        if left['unplayed'] > 0:
            fill('played', left['unplayed'])
        if left['played'] > 0:
            fill('unplayed', left['played'])
        return chosen, chosen_ids, songs, pool

    # 挑戦枠: 枠が無い時に選ばれる範囲の外の曲のうち、並び順の値の高い方から残す（固定・入れ替えの曲も数に入れる）。
    # 残さない範囲の外の曲は自動では選ばずに選び直す（空いた所は範囲の中の曲で埋まる。外した曲は次点に出る）
    chosen, _, _, _ = pick(set())
    auto_hard = sorted((c for c in chosen if c['challenge'] and c['pick'] == 'auto'), key=lambda c: -c['score'])
    keep = max(0, s['challenge_max'] - sum(1 for c in chosen if c['challenge'] and c['pick'] != 'auto'))
    block = hard - {c['leaderboard_id'] for c in auto_hard[:keep]}
    skipped = len(auto_hard) - min(keep, len(auto_hard))
    chosen, chosen_ids, songs, pool = pick(block)
    counts = {k: sum(1 for c in pool if c['kind'] == k) for k in ('played', 'unplayed')}
    n_hard = sum(1 for c in chosen if c['challenge'])
    short = max(0, n - len(chosen))
    # その理由で外した曲の数（固定して入れた曲は数えない）
    n_out = lambda reason: sum(1 for lid, r in why.items() if r == reason and lid not in chosen_ids)

    # 次点（入れ替えの候補）
    runners = []
    for c in pool:
        if len(runners) >= RUNNERS_UP:
            break
        if c['leaderboard_id'] in chosen_ids or (s['one_diff_per_song'] and c['hash'] in songs):
            continue
        runners.append({**c, 'pinned': False, 'challenge': c['leaderboard_id'] in hard})

    # 6. 並び（基本の並び。手で並べた順は serve.py が保存したリストで保つ）
    chosen = default_order(chosen)

    # このリストで増える総PP の見込み: 曲ごとの「クリアできる見込み × 増える総PP（推定の幅で見た期待値。recommend.py）」の合計。
    # 2026-10-28 までは全部が推定どおり取れた場合の値だったが、推定が自己ベストより低い曲は 0 になるので、曲ごとの値と合わなかった
    total_gain = sum((c['gain'] or 0.0) * (1.0 if c.get('clear') is None else c['clear']) for c in chosen)
    me_pp = model.me.pp or 0
    others = store.q('SELECT pp FROM players WHERE country=(SELECT country FROM players WHERE player_id=?) AND seen_at=? '
                     'AND player_id<>? AND pp IS NOT NULL', (model.me.player_id, int(store.meta('ranking_at') or 0), model.me.player_id))
    others = np.array([r['pp'] for r in others], dtype=float)
    passed = int(((others > me_pp) & (others <= me_pp + total_gain)).sum()) if len(others) else 0
    rank = model.me.country_rank
    return {
        'items': chosen, 'runnersUp': runners, 'slots': slots, 'short': short, 'counts': counts,
        'summary': {'gain': total_gain, 'newPp': me_pp + total_gain, 'rank': rank, 'newRank': rank - passed if rank else None,
                    'passed': passed},
        # ★の範囲（上限の 0＝効かない・下限の 0＝自動）とそれで外した曲の数、値を選ぶ参考（recent_stars）。starMinAuto は自動で決めた
        # 下限 {モード: ★ か None（決められず下限なし）}（下限を数字で決めたセットは None）
        'filters': {'starMin': star_min, 'starMax': star_max, 'starMinAuto': auto_min, 'starOut': n_out('stars'),
                    # 似ている人が足りない人の、★の一番上（None＝絞っていない）とそれより上で外した未プレイの曲の数
                    'starTop': star_top, 'starTopOut': n_out('starTop'),
                    'recentStars': recent_stars(est, model, modes, now), 'cooldown': n_out('cooldown'),
                    # クールダウンで外した曲の数の内訳（gap: 間をあける・rest: 続けて遊んだので休み・failRest: クリアできない日が続いたので休み）
                    'cooldownBy': {k: sum(1 for lid, r in why.items() if r == 'cooldown' and cool.get(lid) == k and lid not in chosen_ids)
                                   for k in ('gap', 'rest', 'failRest')},
                    'ageLimit': age_lim, 'challenge': n_hard, 'challengeSkipped': skipped,
                    # 未プレイの pass の上限（None＝絞っていない）とそれで外した曲の数、ほかのセットのリストにあって外した曲の数
                    'passCap': pass_cap, 'passOut': n_out('pass'), 'otherOut': n_out('other'),
                    # クリアの見込みの下限（%。0＝絞っていない）とそれで外した曲の数
                    'minClear': s['min_clear'], 'clearOut': n_out('clear')},
    }
