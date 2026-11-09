# 振り返り（docs/spec.md の「利用の流れ」6・「実装の進め方」7）。取得のたびに、最近出力したプレイリストの曲がどうなったかを記録する。
#  - 伸びた（improved。自己ベストの日時が出力の時から変わった）/ 初クリア（firstClear。出力の時に自己ベストが無かった曲の自己ベストが
#    できた）/ プレイしたが伸びなかった（played）/ まだプレイしていない（notPlayed）
#  - 初クリアを「伸びた」と分けるのは、初めてクリアした精度は推定より大きく下に出やすく（2026-10-18 の4曲で平均 −8.1%）、総PP にも
#    ほとんど効かない（自分の上位の曲に入らない）ため。混ぜると「伸びた曲の数」と推定との差が読めなくなる
#    （作者の調べ「精度の推定とpass」 の「初クリアの扱い」）
#  - プレイしたかは、最終挑戦日時・プレイ回数・出力の後の挑戦の記録で判定する
#  - 推定精度と実際の精度の差を残す（数値の調整に使う）。クールダウン（assemble.cooldown_ids）は reviews を見ない（遊んだ日で決める）
#  - 出力はセットごとに記録する。出力の方法（書き込み・BSManager・ファイルに保存）は問わず、どれも「そのリストで遊ぶつもり」として数える
#  - ホームの表（last）は、曲ごとに「今回の結果」（出力から今までの一番の精度・その PP・総PP への寄与）と「出した時の狙い」（推定精度・
#    増える総PP の見込み）を並べる。総PP への寄与の求め方は下の「総PP への寄与」（tools/play_report.py と同じ）
import json

from .estimate.ppcalc import WEIGHT_BASE, pp_from_row

RECENT_EXPORTS = 5   # 振り返る出力の数（セットごと）
STATUSES = ('improved', 'firstClear', 'played', 'notPlayed')
SCORED = ('improved', 'firstClear')   # 新しい自己ベストができた（best_after がある）状態


# ---- 総PP への寄与（ホームの振り返り・tools/play_report.py） ----
# 「総PP への寄与」は、出力の時点の自分のランク譜面の自己ベストの PP の並び（score_history から作る）に新しい自己ベストを入れた時の、
# 重み付きの合計（pp の降順に 0.965^i。ppcalc.WEIGHT_BASE）の差。初クリアの PP は自分の上位の曲に入らないことが多く、曲の PP より
# ずっと小さい。conn は sqlite3 の接続（row_factory=sqlite3.Row。Store.conn も、play_report の読むだけの接続も同じ形）

def fixer(conn, pid):
    """自己ベストの記録（譜面・時刻・精度・記録した時の PP）→ 今の式の PP。score_history の PP は記録した時の式の値で、
    リウェイトより前の記録は古い式のまま（2026-10-28 の振り返りで、出力の時点の並びが古い式・新しい自己ベストが新しい式に
    なり、寄与が大きく出た）。今の自己ベストと同じ記録は scores の PP（BeatLeader の値）、それより前の記録は精度から
    ppcalc で求め直す（モディファイア無しとして）。譜面の評価が無い（作り物のデータ）時は記録の PP のまま"""
    cur = {r[0]: (r[1], r[2] or 0.0) for r in conn.execute('SELECT leaderboard_id, timeset, pp FROM scores WHERE player_id=?', (pid,))}

    def fix(lid, ts, acc, pp):
        if lid in cur and cur[lid][0] == ts:
            return cur[lid][1]
        v = acc_pp(conn, lid, acc)
        return (pp or 0.0) if v is None else v
    return fix


def acc_pp(conn, lid, acc):
    """その譜面でその精度の PP（今の式・モディファイア無し）。譜面の評価が無い・精度が無ければ None"""
    if not acc:
        return None
    lb = conn.execute('SELECT * FROM leaderboards WHERE leaderboard_id=?', (lid,)).fetchone()
    if lb is None or not lb['acc_rating']:
        return None
    return pp_from_row(acc, lb)['pp']


def pp_at(conn, pid, t, fix=None):
    """時刻 t の直前の、自分のランク譜面の自己ベストの PP（譜面 → 今の式の PP）。score_history の t より前で一番新しい記録。
    記録の無い譜面は scores の自己ベスト（t より前の物）で補う。fix は fixer（何度も呼ぶ時に使い回す）"""
    out = {}
    for q, args in (('SELECT h.leaderboard_id, h.timeset, h.acc, h.pp FROM score_history h JOIN leaderboards l USING(leaderboard_id) '
                     'WHERE l.status=3 AND h.timeset<?', (t,)),
                    ('SELECT s.leaderboard_id, s.timeset, s.acc, s.pp FROM scores s JOIN leaderboards l USING(leaderboard_id) '
                     'WHERE s.player_id=? AND l.status=3 AND s.timeset<?', (pid, t))):
        for lid, ts, acc, pp in conn.execute(q, args):
            if lid not in out or ts > out[lid][0]:
                out[lid] = (ts, acc, pp)
    fix = fix or fixer(conn, pid)
    return {k: fix(k, *v) for k, v in out.items()}


def total(pps):
    """総PP（pp の降順に WEIGHT_BASE^i の重みを掛けた合計。ppcalc.total_pp と同じ式を倍精度で）"""
    s = sorted((p for p in pps.values() if p and p > 0), reverse=True)
    return sum(p * WEIGHT_BASE ** i for i, p in enumerate(s))


def gain(base, new):
    """base（譜面 → PP）の並びに new（譜面 → 新しい自己ベストの PP）を入れた時の総PP の差"""
    return total({**base, **new}) - total(base) if new else 0.0


def record_export(store, model, items, runners, settings_values, path, now, preset='p1', method='write'):
    """出力の記録（recommendation_runs・recommendation_items・exports）。出力した時点の自己ベスト・プレイ回数も残す
    （次回の振り返りとクールダウンで「プレイしたか・伸びたか」を判定するため）。返り値は run_id。
    そのセットの前回の出力と同じ曲で、その後に取得していなければ（書き込んだ後にファイルにも保存した等）、前回の記録に
    出力を1つ足すだけにする（振り返り・クールダウンで同じリストを2回に数えないため）"""
    ids = sorted(c['leaderboard_id'] for c in items)
    last_run = int(store.meta('last_run_at') or 0)
    with store.tx():
        prev = store.conn.execute('SELECT run_id, created_at FROM recommendation_runs WHERE preset=? ORDER BY run_id DESC LIMIT 1',
                                  (preset,)).fetchone()
        if prev and (prev['created_at'] or 0) >= last_run:
            got = sorted(r[0] for r in store.conn.execute(
                'SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1', (prev['run_id'],)))
            if got == ids:
                store.conn.execute('INSERT INTO exports(run_id, exported_at, path, method) VALUES(?,?,?,?)',
                                   (prev['run_id'], now, path, method))
                return prev['run_id']
        cur = store.conn.execute('INSERT INTO recommendation_runs(created_at, settings, preset) VALUES(?, ?, ?)',
                                 (now, json.dumps(settings_values, ensure_ascii=False), preset))
        run_id = cur.lastrowid
        for exported, rows in ((1, items), (0, runners)):
            for c in rows:
                sc = model.my_scores.get(c['leaderboard_id']) or {}
                # parts: 推定の内訳（recommend.estimate。リストを作った時の物。この内訳を持たない版で作ったリストの曲は None）。
                # 直しごと（R1〜R5）の当たりを後で確かめるため。fcAcc は出力した時点の自己ベストの「FC していた場合の精度」（R1 の元）。
                # pUp・gainUp は推定の幅で見た自己ベストを超える見込みと超えた時の増え（見込みの当たりを後で確かめるため。無い版は None）。
                # clear はクリアの見込み（clear.py。求めない曲は None）。ホームの振り返りの「出した時の見込み」（Σ クリアの見込み × gain。
                # おすすめのまとめと同じ）に使う。このキーが無い記録（2026-11-01 より前）は Σ gain で出す。
                # accCap は精度の上限（recommend.py。近い★のほかの曲の自分の一番。付かない曲は None）。上限を超えた更新がどれだけあるかを後で確かめるため
                reason = {'refs': c.get('refs'), 'flags': c.get('flags'), 'bestAcc': c.get('best_acc'), 'baseAcc': c.get('base_acc'),
                          'parts': c.get('parts'), 'pUp': c.get('p_up'), 'gainUp': c.get('gain_up'), 'clear': c.get('clear'),
                          'accCap': (c.get('acc_cap') or {}).get('acc'),
                          'atExport': {'bestTime': sc.get('timeset'), 'playCount': sc.get('play_count'),
                                       'lastTry': sc.get('last_try_time'), 'bestAcc': sc.get('acc'), 'fcAcc': sc.get('fc_acc')}}
                store.conn.execute(
                    'INSERT OR REPLACE INTO recommendation_items(run_id, leaderboard_id, kind, est_acc, est_pp, gain, rank_up, '
                    'confidence, reason, pinned, excluded, exported) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (run_id, c['leaderboard_id'], c['kind'], c['est_acc'], c['est_pp'], c['gain'], c['rank_up'], c['confidence'],
                     json.dumps(reason, ensure_ascii=False), 1 if c.get('pinned') else 0, 0, exported))
        store.conn.execute('INSERT INTO exports(run_id, exported_at, path, method) VALUES(?,?,?,?)', (run_id, now, path, method))
    return run_id


def _recent_runs(store, per_preset):
    """セットごとに新しい順で per_preset 回分の出力 [{run_id, preset, at}]（at は最初に出力した時刻）"""
    rows = store.q('SELECT e.run_id, r.preset, MIN(e.exported_at) AS at FROM exports e JOIN recommendation_runs r ON r.run_id=e.run_id '
                   'GROUP BY e.run_id ORDER BY at DESC')
    out, n = [], {}
    for r in rows:
        k = r['preset'] or 'p1'
        if n.get(k, 0) < per_preset:
            n[k] = n.get(k, 0) + 1
            out.append({**r, 'preset': k})
    return out


def update(store, player_id, now):
    exps = _recent_runs(store, RECENT_EXPORTS)
    if not exps:
        return
    with store.tx():
        for e in exps:
            items = store.q('SELECT leaderboard_id, est_acc, reason FROM recommendation_items WHERE run_id=? AND exported=1', (e['run_id'],))
            for it in items:
                lid = it['leaderboard_id']
                try:
                    was = json.loads(it['reason'] or '{}').get('atExport') or {}
                except ValueError:
                    was = {}
                row = store.conn.execute('SELECT acc, timeset, play_count, last_try_time FROM scores WHERE player_id=? AND leaderboard_id=?',
                                         (player_id, lid)).fetchone()
                at = store.conn.execute('SELECT MAX(acc) FROM attempts WHERE leaderboard_id=? AND timeset>? AND end_type=1',
                                        (lid, e['at'])).fetchone()[0]
                tried = store.conn.execute('SELECT COUNT(*) FROM attempts WHERE leaderboard_id=? AND timeset>?', (lid, e['at'])).fetchone()[0]
                improved = bool(row) and (row['timeset'] or 0) > e['at'] and row['timeset'] != was.get('bestTime')
                played = improved or tried > 0 or (bool(row) and ((row['last_try_time'] or 0) > e['at']
                                                                   or (row['play_count'] or 0) > (was.get('playCount') or 0)))
                # 出力の時に自己ベストが無かった（best_before が NULL になる）曲の自己ベストは初クリア。DB の移行 6 と同じ判定
                first = improved and was.get('bestAcc') is None
                status = 'firstClear' if first else 'improved' if improved else ('played' if played else 'notPlayed')
                store.conn.execute('INSERT OR REPLACE INTO reviews(run_id, leaderboard_id, status, est_acc, best_before, best_after, '
                                   'attempt_max, checked_at) VALUES(?,?,?,?,?,?,?,?)',
                                   (e['run_id'], lid, status, it['est_acc'], was.get('bestAcc'),
                                    row['acc'] if (row and improved) else None, at, now))


def est_error(rows):
    """推定と実際の差（実際 − 推定）を結果ごとに {'improved'|'firstClear'|'played': {n, mean}}。実際は、伸びた・初クリアの曲は
    新しい自己ベスト、伸びなかった曲は出力の後の挑戦（クリア）の一番よい精度（挑戦の記録が無ければ数えない）。
    上の n・mean は初クリアを除いた全体（出力の時に自己ベストがあった曲）。初クリアは推定より低く出やすいので混ぜない"""
    by = {k: [] for k in ('improved', 'firstClear', 'played')}
    for r in rows:
        if r['status'] not in by or r['est_acc'] is None:
            continue
        act = r['best_after'] if r['status'] in SCORED else r['attempt_max']
        if act is not None:
            by[r['status']].append(act - r['est_acc'])

    def mean(xs):
        return {'n': len(xs), 'mean': (sum(xs) / len(xs)) if xs else None}
    return {**mean(by['improved'] + by['played']), **{k: mean(v) for k, v in by.items()}}


def _json(s):
    try:
        d = json.loads(s or '{}')
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _one(store, run_id, at, preset, player_id, fix):
    """1回の出力の結果（ホームの表）。曲は出力した曲（振り返りの記録がまだ無い曲＝出力の後に取得していない曲は「まだ」）。
    曲ごとに 出した時の狙い（est＝推定精度・expGain＝増える総PP の見込み＝おすすめの「総PP」）・出力の時点の自己ベスト（before）と、
    今回の結果（出力から今まで。同じリストを何日かに分けて遊んだ時は全部の中の一番）:
      acc     伸びた・初クリアは新しい自己ベスト、伸びなかった曲は出力の後のクリアの一番（無ければ None）
      pp      その精度の PP（今の式）。伸びた・初クリアは自己ベストの PP（scores）、伸びなかった曲は精度から求めた参考の値
      contrib 総PP への寄与（伸びた・初クリアだけ。出力の時点の並びにその曲の新しい自己ベストを入れた差）
    まとめ: gain（全部の新しい自己ベストを一度に入れた差）・expected（出した時の見込み。Σ クリアの見込み × 見込み。クリアの見込みを
    残していない前の記録は Σ 見込みで、expectedClear が False）・played（遊んだ曲の数）・checked（出力の後に取得して確かめたか）"""
    conn = store.conn
    rows = conn.execute(
        'SELECT ri.leaderboard_id AS lid, ri.est_acc, ri.gain AS exp_gain, ri.reason, r.status, r.best_before, r.best_after, '
        'r.attempt_max, l.hash, l.song_name, l.difficulty, l.stars, s.acc AS cur_acc, s.pp AS cur_pp '
        'FROM recommendation_items ri LEFT JOIN reviews r ON r.run_id=ri.run_id AND r.leaderboard_id=ri.leaderboard_id '
        'LEFT JOIN leaderboards l ON l.leaderboard_id=ri.leaderboard_id '
        'LEFT JOIN scores s ON s.player_id=? AND s.leaderboard_id=ri.leaderboard_id '
        'WHERE ri.run_id=? AND ri.exported=1 ORDER BY l.stars, ri.leaderboard_id', (player_id, run_id)).fetchall()
    base = pp_at(conn, player_id, at, fix)
    counts = dict.fromkeys(STATUSES, 0)
    items, errs, new, with_clear = [], [], {}, bool(rows)
    for r in rows:
        lid, status = r['lid'], r['status'] or 'notPlayed'
        reason = _json(r['reason'])
        with_clear = with_clear and 'clear' in reason
        counts[status] += 1
        acc = r['best_after'] if status in SCORED else r['attempt_max'] if status == 'played' else None
        pp = None
        if acc is not None:
            same = status in SCORED and r['cur_acc'] is not None and abs(r['cur_acc'] - acc) < 1e-9
            pp = r['cur_pp'] if same else acc_pp(conn, lid, acc)
        if status in SCORED and pp is not None:
            new[lid] = pp
        before = r['best_before'] if r['status'] else (reason.get('atExport') or {}).get('bestAcc')
        items.append({'id': lid, 'hash': r['hash'], 'song': r['song_name'], 'difficulty': r['difficulty'], 'stars': r['stars'],
                      'status': status, 'est': r['est_acc'], 'expGain': r['exp_gain'], 'clear': reason.get('clear'), 'before': before,
                      'acc': acc, 'pp': pp, 'contrib': gain(base, {lid: pp}) if lid in new else None})
        errs.append({'status': status, 'est_acc': r['est_acc'], 'best_after': r['best_after'], 'attempt_max': r['attempt_max']})
    clear = lambda x: 1.0 if not with_clear or x['clear'] is None else x['clear']
    return {
        'preset': preset, 'exportedAt': at, 'counts': counts, 'estError': est_error(errs), 'items': items,
        'gain': gain(base, new), 'expected': sum((x['expGain'] or 0.0) * clear(x) for x in items), 'expectedClear': with_clear,
        'played': len(items) - counts['notPlayed'], 'checked': any(r['status'] for r in rows),
    }


def last(store, player_id=None, presets=None):
    """セットごとの一番新しい出力の振り返り（ホーム画面用。その回の推定と実際の差も）と、これまでの推定と実際の差のまとめ。
    presets（セットの id の並び）を渡すと、そのセットの分だけをその順に（ホームはおすすめのタブの並び）。省くと全部を新しい順"""
    runs = _recent_runs(store, 1)
    if presets is not None:
        by = {r['preset']: r for r in runs}
        runs = [by[p] for p in presets if p in by]
    if not runs:
        return None
    with store.lock:   # 接続を直に使う（pp_at など）間、取得のスレッドの書き込みと混ざらないように
        fix = fixer(store.conn, player_id)
        out = [_one(store, r['run_id'], r['at'], r['preset'], player_id, fix) for r in runs]
    return {
        'runs': out,
        'estError': est_error(store.q("SELECT status, est_acc, best_after, attempt_max FROM reviews WHERE status<>'notPlayed'")),
    }
