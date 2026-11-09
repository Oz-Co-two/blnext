# 画面に出す内容を DB から組み立てる（画面の都合の集計。計算＝推定は core/estimate）
from . import review
from .estimate.ppcalc import total_pp


# 「前回からの変化」で、変化の無い取得が続いた時にさかのぼる回数の上限
CHANGE_LOOKBACK = 30


def _improved_since(store, t, player_id):
    """取得の回 t より後に記録した自己ベストの更新（新しい順）と、それで増えた総PP（重み付きの合計）。t の時点で記録の無かった曲
    （初めて遊んだ曲）は数えない。返り値は (曲の並び, 全部を一度に入れた総PP の差)。
    曲の PP（old_pp・pp）は今の式にそろえる（review.fixer。score_history の PP は記録した時の式の値で、リウェイトの前の記録は古い式のまま）。
    gain はその曲の新しい自己ベストだけを t の時点の並び（review.pp_at に、伸ばした曲の前の自己ベストを重ねた物）に入れた時の総PP の差で、
    振り返りの表の「総PP」と同じ求め方。曲の PP が増えても、自分の上位の曲に入らなければ総PP はほとんど増えない（2026-11-09 の利用者の要望で、
    曲の PP の増えと並べて出す）。ランクでない譜面は 0"""
    with store.lock:   # 接続を直に使う（review.pp_at など）間、取得のスレッドの書き込みと混ざらないように
        conn = store.conn
        rows = []
        for r in conn.execute('SELECT h.leaderboard_id AS lid, h.acc, h.pp, MAX(h.timeset) AS timeset, l.status '
                              'FROM score_history h LEFT JOIN leaderboards l ON l.leaderboard_id=h.leaderboard_id '
                              'WHERE h.recorded_at > ? GROUP BY h.leaderboard_id ORDER BY timeset DESC', (t,)).fetchall():
            old = conn.execute('SELECT acc, pp, timeset FROM score_history WHERE leaderboard_id=? AND recorded_at <= ? '
                               'ORDER BY timeset DESC LIMIT 1', (r['lid'], t)).fetchone()
            if old:
                rows.append((r, old))
        if not rows:
            return [], 0.0
        fix = review.fixer(conn, player_id)
        out, before, after = [], {}, {}
        for r, old in rows:
            a, b = fix(r['lid'], old['timeset'], old['acc'], old['pp']), fix(r['lid'], r['timeset'], r['acc'], r['pp'])
            if r['status'] == 3:
                before[r['lid']], after[r['lid']] = a, b
            out.append({'leaderboard_id': r['lid'], 'old_acc': old['acc'], 'old_pp': a, 'acc': r['acc'], 'pp': b})
        base = {**review.pp_at(conn, player_id, t, fix), **before}
    for x in out:
        lid = x['leaderboard_id']
        x['gain'] = review.gain(base, {lid: after[lid]}) if lid in after else 0.0
    return out, review.gain(base, after)


def _compare(store, me, players, t):
    """取得の回 t と今の比較（伸ばした曲・抜いた人・抜かれた人）。players は今回のランキングの人"""
    snap = {x['player_id']: x for x in store.q('SELECT player_id, pp, country_rank FROM player_snapshots WHERE run_at=?', (t,))}
    prev = {k: v['country_rank'] for k, v in snap.items()}
    mine = snap.get(me['player_id'])
    my_prev = mine['country_rank'] if mine else None
    rank = me['country_rank']
    passed, passed_by = [], []
    if my_prev and rank:
        for r in players:
            p = prev.get(r['player_id'])
            if not p or r['player_id'] == me['player_id'] or not r['country_rank']:
                continue
            if p < my_prev and r['country_rank'] > rank:
                passed.append({'name': r['name'], 'countryRank': r['country_rank'], 'pp': r['pp']})
            elif p > my_prev and r['country_rank'] < rank:
                passed_by.append({'name': r['name'], 'countryRank': r['country_rank'], 'pp': r['pp']})
    passed.sort(key=lambda x: x['countryRank'])
    passed_by.sort(key=lambda x: x['countryRank'])
    improved, gain = _improved_since(store, t, me['player_id'])
    moved = bool(mine) and (my_prev != rank or abs((mine['pp'] or 0) - (me['pp'] or 0)) >= 0.005)
    return {'prev': prev, 'prevAt': t, 'myPrev': my_prev, 'improved': improved, 'improvedGain': gain, 'passed': passed,
            'passedBy': passed_by, 'changed': bool(improved or passed or passed_by or moved)}


def _changes(store, me, ranking_at, run_at):
    """「前回からの変化」。比べる回は、変化（伸ばした曲・抜いた／抜かれた・自分の順位や PP）が見つかる所まで前の取得へさかのぼる。
    続けて取得しても、変化の無い回で前の変化が消えないように。どこまでさかのぼっても変化が無ければ直前の回と比べる"""
    runs = [r['run_at'] for r in store.q('SELECT DISTINCT run_at FROM player_snapshots WHERE player_id=? AND run_at < ? '
                                         'ORDER BY run_at DESC LIMIT ?', (me['player_id'], run_at, CHANGE_LOOKBACK))] if run_at else []
    if not runs:
        return {'prev': {}, 'prevAt': None, 'myPrev': None, 'improved': [], 'improvedGain': 0.0, 'passed': [], 'passedBy': []}
    players = store.q('SELECT player_id, name, pp, country_rank FROM players WHERE country=? AND seen_at=?', (me['country'], ranking_at))
    first = None
    for t in runs:
        c = _compare(store, me, players, t)
        if c['changed']:
            return c
        if first is None:
            first = c
    return first


def _at_export(store, me):
    """「前回の出力から」の比べる元: 一番新しい出力（どのセットでも）の時刻と、その時刻以前で一番新しい取得の回の各人の PP・順位。
    返り値 ({exportAt, at}, {player_id: {pp, rank, country_rank}})。出力が無ければ (None, {})、出力の前に取得していなければ at が None。
    上下の人の表とタイルに出す（1週間に何回も遊ぶので「先週から」だけでは今回どうだったかが分からない。2026-11-01 の利用者の要望）"""
    e = store.q1('SELECT MAX(exported_at) AS t FROM exports')
    if not e or not e['t']:
        return None, {}
    r = store.q1('SELECT MAX(run_at) AS t FROM player_snapshots WHERE player_id=? AND run_at<=?', (me['player_id'], e['t']))
    if not r or not r['t']:
        return {'exportAt': e['t'], 'at': None}, {}
    snap = {x['player_id']: x for x in store.q('SELECT player_id, pp, rank, country_rank FROM player_snapshots WHERE run_at=?', (r['t'],))}
    return {'exportAt': e['t'], 'at': r['t']}, snap


def _review(store, settings, player_id):
    """前回のプレイリストの結果（review.last）。セットの並び（設定の presets の順＝おすすめの画面のタブの順）に並べ、セットの名前を
    付ける（消したセットの分は出さない）"""
    ps = settings.presets(brief=True)
    rv = review.last(store, player_id, [p['id'] for p in ps])
    if not rv:
        return None
    names = {p['id']: p['name'] for p in ps}
    return {**rv, 'runs': [{**r, 'presetName': names[r['preset']]} for r in rv['runs']]}


def home(store, settings):
    s = settings.values()
    me = store.q1('SELECT * FROM players WHERE player_id=? AND is_self=1', (s['player_id'],))
    if not me or me['pp'] is None:
        return {'hasData': False}
    country = me['country']
    rank = me['country_rank']
    ranking_at = int(store.meta('ranking_at') or 0)
    snap = store.q1('SELECT MAX(run_at) AS t FROM player_snapshots WHERE player_id=?', (me['player_id'],))
    run_at = snap['t'] if snap else None
    chg = _changes(store, me, ranking_at, run_at)
    since, then = _at_export(store, me)
    mine = then.get(me['player_id']) or {}

    # 目標順位の人と、そこまでの差（その人を超えるのに要る PP）
    target = s['target_country_rank']
    trow = store.q1('SELECT name, pp, country_rank FROM players WHERE country=? AND country_rank=? AND seen_at=?',
                    (country, target, ranking_at))
    target_out = {'rank': target}
    if trow and trow['pp'] is not None:
        target_out.update(name=trow['name'], pp=trow['pp'], gap=max(0.0, trow['pp'] - me['pp']))

    # 上下の人。rankMove・ppMove は前回の出力の時点（_at_export）からの国内順位の上がり・PP の増え（その時に取得の範囲にいなかった人は None）
    n = s['neighbor_display']
    near = []
    if rank:
        # 自分はいつも入れる（ランキングが取れなかった回は、他の人は前回の値・自分は今回の値）
        rows = store.q('SELECT player_id, name, pp, country_rank, rank FROM players WHERE country=? AND (seen_at=? OR player_id=?) '
                       'AND country_rank BETWEEN ? AND ? ORDER BY country_rank, player_id=? DESC',
                       (country, ranking_at, me['player_id'], rank - n, rank + n, me['player_id']))
        for r in rows:
            o = then.get(r['player_id']) or {}
            near.append({'id': r['player_id'], 'name': r['name'], 'pp': r['pp'], 'countryRank': r['country_rank'],
                         'diff': (r['pp'] - me['pp']) if r['pp'] is not None else None,
                         'rankMove': (o['country_rank'] - r['country_rank']) if (o.get('country_rank') and r['country_rank']) else None,
                         'ppMove': (r['pp'] - o['pp']) if (o.get('pp') is not None and r['pp'] is not None) else None,
                         'self': r['player_id'] == me['player_id']})

    # 国内の PP の分布（取得した範囲の人）
    pool_pp = [r['pp'] for r in store.q('SELECT pp FROM players WHERE country=? AND seen_at=? AND pp IS NOT NULL',
                                        (country, ranking_at))]

    # 前回からの変化: 自分が伸ばした曲（曲の情報を足す。hash は曲のカバー画像）
    improved = []
    for x in chg['improved']:
        lb = store.q1('SELECT hash, song_name, difficulty, mode, stars FROM leaderboards WHERE leaderboard_id=?', (x['leaderboard_id'],)) or {}
        improved.append({**x, 'hash': lb.get('hash'), 'song': lb.get('song_name'), 'difficulty': lb.get('difficulty'), 'mode': lb.get('mode'),
                         'stars': lb.get('stars')})

    # 総PPの確かめ（移植した計算の重み付けで、保存しているランクのスコアから計算し直す）
    pps = [r['pp'] for r in store.q('SELECT s.pp FROM scores s JOIN leaderboards l ON l.leaderboard_id=s.leaderboard_id '
                                    'WHERE s.player_id=? AND l.status=3', (me['player_id'],))]
    calc = total_pp(pps) if pps else None

    return {
        'hasData': True,
        'me': {'name': me['name'], 'pp': me['pp'], 'rank': me['rank'], 'countryRank': rank, 'country': country,
               'accPp': me['acc_pp'], 'passPp': me['pass_pp'], 'techPp': me['tech_pp'],
               'lastWeekPp': me['last_week_pp'], 'lastWeekRank': me['last_week_rank'],
               'lastWeekCountryRank': me['last_week_country_rank'], 'prevCountryRank': chg['myPrev'],
               # 前回の出力の時点（_at_export）の自分の PP・世界順位・国内順位（タイルの「前回の出力から」）
               'exportPp': mine.get('pp'), 'exportRank': mine.get('rank'), 'exportCountryRank': mine.get('country_rank')},
        'target': target_out, 'neighbors': near, 'poolPp': pool_pp,
        'improved': improved, 'improvedGain': chg['improvedGain'], 'passed': chg['passed'], 'passedBy': chg['passedBy'],
        'dataAt': run_at, 'prevAt': chg['prevAt'], 'sinceExport': since,
        'ppCheck': {'api': me['pp'], 'calc': calc, 'ok': calc is None or abs(calc - me['pp']) < 0.5},
        'history': store.q('SELECT date, pp, country_rank AS countryRank FROM self_history ORDER BY date DESC LIMIT 60'),
        'review': _review(store, settings, me['player_id']),
    }
