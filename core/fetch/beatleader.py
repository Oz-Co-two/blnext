# BeatLeader の API の呼び出しと、応答を保存の層で使う形（dict）にそろえる処理。
# エンドポイントと項目名は docs/investigation.md。想定外の応答は項目単位で読み飛ばしてログに残す（アプリは止めない）
import json, logging, math
from urllib.parse import quote

from .replay import is_replay_url

log = logging.getLogger('bln.bl')

SCORES_PAGE = 100      # /player/{id}/scores の1ページ（上限100）
PLAYERS_PAGE = 100     # /players の1ページ（上限100）
MAPS_PAGE = 500        # /maps の1ページ（上限のチェックが無いので500にしている）
ATTEMPTS_PAGE = 100    # /player/{id}/scoresstats の1ページ（上限100）
SPEED_MODIFIERS = ('SS', 'FS', 'SF', 'BFS', 'BSF')
SITE = 'https://beatleader.com'            # BeatLeader のサイト（ブラウザで開くページ）
REPLAY_SITE = 'https://replay.beatleader.com'   # リプレイを見るページ（?scoreId=スコアのID）
SETTINGS_SCORES_URL = SITE + '/settings#scores'   # 統計（Score history）を公開にする設定のページ（ログインが要る）


def leaderboard_url(lb_id):
    """譜面（1難易度）のランキングのページ"""
    return '%s/leaderboard/global/%s/1' % (SITE, quote(str(lb_id), safe=''))


def replay_url(score_id):
    """スコアのリプレイを見るページ"""
    return '%s/?scoreId=%d' % (REPLAY_SITE, int(score_id))


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):   # 無限大（"Infinity"・1e400）も値なし
        return None


def _s(v):
    return v if isinstance(v, str) else ('' if v is None else str(v))


def _ratings_json(mr):
    """modifiersRating（SS/FS/SF/BFS/BSF ごとの★・レーティング）を保存用の JSON 文字列に。id は捨てる"""
    if not isinstance(mr, dict):
        return None
    d = {k: v for k, v in mr.items() if k != 'id' and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return json.dumps(d, separators=(',', ':')) if d else None


def _values_json(mv):
    """modifierValues（モディファイアの倍率）を保存用の JSON 文字列に。modifierId は捨てる"""
    if not isinstance(mv, dict):
        return None
    d = {k: v for k, v in mv.items() if k != 'modifierId' and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return json.dumps(d, separators=(',', ':')) if d else None


def is_country_code(country):
    """国のコード（小文字2文字）か。BeatLeader で国を決めていない人は 'not set' などが来ることがある"""
    return isinstance(country, str) and len(country) == 2 and country.isascii() and country.isalpha() and country.islower()


# ---- そろえる ----

def norm_player(p):
    """/player/{id} と /players の1行 → players の1行"""
    st = p.get('scoreStats') if isinstance(p.get('scoreStats'), dict) else {}
    pid = _s(p.get('id'))
    if not pid:
        raise ValueError('player without id')
    return {
        'player_id': pid, 'name': _s(p.get('name')), 'country': _s(p.get('country')).lower(),
        'pp': _f(p.get('pp')), 'rank': _i(p.get('rank')), 'country_rank': _i(p.get('countryRank')),
        'acc_pp': _f(p.get('accPp')), 'pass_pp': _f(p.get('passPp')), 'tech_pp': _f(p.get('techPp')),
        'last_ranked_score_time': _i(st.get('lastRankedScoreTime')), 'ranked_play_count': _i(st.get('rankedPlayCount')),
        'last_week_pp': _f(p.get('lastWeekPp')), 'last_week_rank': _i(p.get('lastWeekRank')),
        'last_week_country_rank': _i(p.get('lastWeekCountryRank')),
        'avatar': _s(p.get('avatar')),
    }


def norm_difficulty(lb_id, song, d):
    """譜面（1難易度）→ leaderboards の1行。song は曲の情報（/maps の1行、またはスコアの leaderboard.song）"""
    return {
        'leaderboard_id': _s(lb_id), 'hash': _s(song.get('hash')).lower(), 'song_name': _s(song.get('name')),
        'sub_name': _s(song.get('subName')), 'song_author': _s(song.get('author')), 'mapper': _s(song.get('mapper')),
        'difficulty': _s(d.get('difficultyName')), 'mode': _s(d.get('modeName')), 'status': _i(d.get('status')),
        'stars': _f(d.get('stars')), 'acc_rating': _f(d.get('accRating')), 'pass_rating': _f(d.get('passRating')),
        'tech_rating': _f(d.get('techRating')), 'predicted_acc': _f(d.get('predictedAcc')), 'map_type': _i(d.get('type')),
        # 譜面ごとの精度の曲線の材料（2026-10 のリウェイトから。core/estimate/ppcalc.py の get_curve）
        'slider_percentage': _f(d.get('sliderPercentage')), 'note_span': _f(d.get('noteSpan')),
        'modifiers_rating': _ratings_json(d.get('modifiersRating')), 'modifier_values': _values_json(d.get('modifierValues')),
        'upload_time': _i(song.get('uploadTime')) or None, 'ranked_time': _i(d.get('rankedTime')) or None,
        'notes': _i(d.get('notes')), 'duration': _i(d.get('duration') or song.get('duration')),
        'bpm': _f(song.get('bpm')), 'njs': _f(d.get('njs')), 'nps': _f(d.get('nps')),
        'bombs': _i(d.get('bombs')), 'walls': _i(d.get('walls')), 'peak_ebpm': _f(d.get('peakSustainedEBPM')),
        'tags': _tags(song.get('tags')),
    }


def _tags(v):
    """BeatSaver のタグ（"tech,speed,electronic"）→ 小文字のカンマ区切り。無ければ None"""
    if not isinstance(v, str):
        return None
    t = [x.strip().lower() for x in v.split(',') if x.strip()]
    return ','.join(t) or None


def norm_map(m):
    """/maps の1行（1曲）→ ランクの難易度ごとの leaderboards の行の並び"""
    out = []
    for d in m.get('difficulties') or []:
        try:
            if _i(d.get('status')) != 3 or not d.get('leaderboardId'):
                continue
            out.append(norm_difficulty(d['leaderboardId'], m, d))
        except (AttributeError, TypeError, ValueError) as e:
            log.warning('skip map diff %s: %s', m.get('id'), e)
    return out


def norm_score(s):
    """/player/{id}/scores の1件 → (scores の行, leaderboards の行)"""
    lb = s.get('leaderboard') or {}
    lb_id = _s(lb.get('id') or s.get('leaderboardId'))
    if not lb_id:
        raise ValueError('score without leaderboard')
    row = {
        'leaderboard_id': lb_id, 'score_id': _i(s.get('id')), 'acc': _f(s.get('accuracy')), 'pp': _f(s.get('pp')),
        'fc_acc': _f(s.get('fcAccuracy')), 'fc_pp': _f(s.get('fcPp')), 'weight': _f(s.get('weight')),
        'acc_pp': _f(s.get('accPP')), 'pass_pp': _f(s.get('passPP')), 'tech_pp': _f(s.get('techPP')),
        'modifiers': _s(s.get('modifiers')), 'stars': None,
        'timeset': _i(s.get('timepost')) or _i(s.get('timeset')),
        'play_count': _i(s.get('playCount')), 'last_try_time': _i(s.get('lastTryTime')),
        'missed_notes': _i(s.get('missedNotes')), 'bad_cuts': _i(s.get('badCuts')), 'bomb_cuts': _i(s.get('bombCuts')),
        'walls_hit': _i(s.get('wallsHit')), 'full_combo': 1 if s.get('fullCombo') else 0,
        'replay_url': s.get('replay') if is_replay_url(s.get('replay')) else None,   # 頭だけ読んで JD を出す（replay.py）
    }
    lbrow = norm_difficulty(lb_id, lb.get('song') or {}, lb.get('difficulty') or {}) if lb.get('difficulty') else None
    return row, lbrow


def norm_accgraph(p):
    """/player/{id}/accgraph の1点 → scores の行（stars はモディファイアで補正済み）。細かい項目は無い"""
    lb_id = _s(p.get('leaderboardId'))
    if not lb_id:
        raise ValueError('point without leaderboard')
    return {
        'leaderboard_id': lb_id, 'acc': _f(p.get('acc')), 'pp': _f(p.get('pp')), 'modifiers': _s(p.get('modifiers')),
        'stars': _f(p.get('stars')), 'timeset': _i(p.get('timeset')),
    }


def norm_attempt(a):
    lb_id = _s(a.get('leaderboardId'))
    if not lb_id:
        raise ValueError('attempt without leaderboard')
    t = _i(a.get('timeset')) or _i(a.get('timepost')) or _i(a.get('startTime'))
    return {'leaderboard_id': lb_id, 'acc': _f(a.get('accuracy')), 'pp': _f(a.get('pp')), 'end_type': _i(a.get('endType')),
            'modifiers': _s(a.get('modifiers')), 'timeset': t}


def norm_history(h):
    ts = _i(h.get('timestamp'))
    if not ts:
        raise ValueError('history without timestamp')
    return {'timestamp': ts, 'pp': _f(h.get('pp')), 'rank': _i(h.get('rank')), 'country_rank': _i(h.get('countryRank'))}


def each(rows, fn, what):
    """rows の各要素を fn でそろえる。だめな要素は読み飛ばしてログに残す"""
    out = []
    for r in rows or []:
        try:
            out.append(fn(r))
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            log.warning('skip %s: %s', what, e)
    return out


def _data(res):
    """{"metadata":…, "data":[…]} と配列のどちらでも data の並びを返す"""
    if isinstance(res, dict):
        return res.get('data') or []
    return res if isinstance(res, list) else []


def _total(res):
    try:
        return int(res['metadata']['total'])
    except (TypeError, KeyError, ValueError):
        return None


# ---- 呼び出し ----

def stats_public(p):
    """統計（Score history）を公開しているか。分からなければ None。非公開だと挑戦の記録（scoresstats）は 401、スコアの playCount・lastTryTime は 0 で返る。
    /player/{id} は stats=true の時だけ本当の値が入る（stats=false は公開の人にも false を返す。2026-11-06 に確かめた）"""
    ps = p.get('profileSettings') if isinstance(p, dict) else None
    v = ps.get('showStatsPublic') if isinstance(ps, dict) else None
    return v if isinstance(v, bool) else None


def player(c, pid):
    """自分のプロフィール（players の1行＋stats_public。保存する時は stats_public を外す）"""
    res = c.get('/player/%s' % pid, stats='true')
    return {**norm_player(res), 'stats_public': stats_public(res)}


def search_players(c, name, count=10):
    res = c.get('/players', search=name, count=count, sortBy='pp')
    return each(_data(res), norm_player, 'player')


def country_page(c, country, page):
    res = c.get('/players', countries=country, sortBy='pp', count=PLAYERS_PAGE, page=page)
    return each(_data(res), norm_player, 'player'), _total(res)


def scores_page(c, pid, page, sort='date'):
    """自分のランクのスコア。sort='lastTryTime' で最近挑戦した順（プレイ回数・最終挑戦日時の更新を拾う）"""
    res = c.get('/player/%s/scores' % pid, type='ranked', sortBy=sort, order='desc', count=SCORES_PAGE, page=page)
    return each(_data(res), norm_score, 'score'), _total(res)


def accgraph(c, pid):
    return each(_data(c.get('/player/%s/accgraph' % pid, type='acc')), norm_accgraph, 'accgraph')


def history(c, pid, count):
    return each(_data(c.get('/player/%s/history/compact' % pid, count=count)), norm_history, 'history')


def ranked_maps_page(c, page):
    res = c.get('/maps', type='ranked', count=MAPS_PAGE, page=page)
    rows = []
    for m in _data(res):
        if isinstance(m, dict):
            rows.extend(norm_map(m))
    return rows, len(_data(res)), _total(res)


def attempts_page(c, pid, page, time_from=None):
    """挑戦ごとの記録（統計を公開しているプレイヤーだけ）。sortBy=date でないと PP の付いた挑戦だけに絞られる"""
    res = c.get('/player/%s/scoresstats' % pid, type='ranked', sortBy='date', order='desc', count=ATTEMPTS_PAGE,
                page=page, time_from=time_from)
    return each(_data(res), norm_attempt, 'attempt'), _total(res)
