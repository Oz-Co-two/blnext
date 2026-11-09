"""出力したプレイリストの結果を Markdown の表にする開発用の道具（遊んだ後の振り返りを記録して、数値の調整に使う）。

使い方: .venv-build/Scripts/python.exe -X utf8 tools/play_report.py [--data <書き込み先>] [--run <出力の回>] [--session [時間]]
        [--since <日時>] [--out <ファイル>]
  --data の data/blnext.db を読むだけ（書き換えない）。--run を省くと最後に出力した回。結果は、その出力から同じセットの次の出力
  （無ければ今）までの挑戦の記録（アテンプト）・自己ベストの更新・自分の PP と国内順位で見る（別のセットの出力では切らない。
  難しさ違いのセットを続けて出力して、同じ日に遊ぶため）。
  --session: 難しさ違いのセットを同じ日に並べて遊んだ時の比べ。最後の出力（--run があればその回）からさかのぼって 時間（省くと 6）
  以内の、セットごとの最後の出力をまとめ、セットを並べた比べの表とセットごとの表を出す。セットの名前は config/settings.json から。
  --since: 結果をその時刻（"2026-11-01 12:00"・日付だけなら0時）から数える。同じリストを出し直さずに別の日にもう一度遊んだ時、
  その日の分だけを見る。「前の自己ベスト」と総PP への寄与の元の並びもその時刻の物になる（区分・推定は出力した時のまま）。
  「推定との差」は、その回に一番よかったクリアの精度と推定精度の差（クリアした曲だけ）。初クリア（出力の時に自己ベストが無かった曲）・
  更新（前の自己ベストを伸ばした）・伸ばせなかった（クリアしたが自己ベストを超えなかった）に分けても出す。初めてのクリアは推定より
  大きく下に出やすいので、混ぜると推定の外れが読めなくなる（作者の調べ「精度の推定とpass」 の「初クリアの扱い」）。
  「総PP への寄与」は、出力の時点の自分のランクのスコアの PP の並び（score_history から作る）に新しい自己ベストを入れた時の、
  重み付きの合計（pp の降順に 0.965^i。ppcalc.WEIGHT_BASE）の差。初クリアの PP は自分の上位の曲に入らないことが多く、曲の PP よりずっと小さい。
  PP は今の式の値にそろえる（score_history の PP は記録した時の式の値のため。fixer）。求め方はホームの振り返りと同じ（core/review.py）。
"""
import argparse, json, os, sqlite3, sys, time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# 総PP への寄与（ホームの振り返りと同じ求め方）
from core.review import fixer, gain, pp_at, total   # noqa: E402,F401

END = {1: 'クリア', 2: '失敗', 3: 'やり直し', 4: 'やめた', 5: '練習'}
DIFF = {'ExpertPlus': 'Expert+'}
PASS_EASY = 3.5      # 推定との差を pass で分けて見る境目
SESSION_HOURS = 6    # --session の初期値（最後の出力からさかのぼる時間）
# クリアした曲の分け方（推定との差・数）
KINDS = (('firstClear', '初クリア'), ('improved', '更新'), ('flat', '伸ばせなかった'))
KIND_NAME = dict(KINDS)
SCORED = ('firstClear', 'improved')   # 新しい自己ベストができた（総PP に効く）


def when(t, fmt='%m/%d %H:%M'):
    return datetime.fromtimestamp(int(t)).strftime(fmt) if t else '—'


def pct(v):
    return '—' if v is None else '%.2f' % (100 * v)


def signed(v):
    return '—' if v is None else '%+.2f' % v


def avg(xs):
    return sum(xs) / len(xs) if xs else None


def gap_text(xs):
    """推定との差の平均（%）と曲数。無ければ —"""
    return '%+.2f%%（%d曲）' % (avg(xs), len(xs)) if xs else '—'


# ---- 総PP への寄与（fixer・pp_at・total・gain は core/review.py） ----

def new_pps(songs):
    return {x['id']: x['new_pp'] for x in songs if x['result'] in SCORED}


# ---- 1回の出力の結果 ----

def collect(c, pid, run, since=None):
    """出力の回 run の結果。曲ごとの result: firstClear / improved / flat（クリアしたが自己ベストを超えなかった）/
    tried（遊んだがクリアできなかった）/ None（遊んでいない）。since（時刻）があれば結果をその時刻から数え、前の自己ベストと
    総PP への寄与の元の並びもその時点の物にする（同じリストを別の日にもう一度遊んだ時に、その日の分だけを見る）"""
    exp = c.execute('SELECT MIN(exported_at) AS a, group_concat(DISTINCT method) AS m FROM exports WHERE run_id=?', (run,)).fetchone()
    if exp['a'] is None:
        raise SystemExit('出力の回 %s が見つかりません' % run)
    r = c.execute('SELECT created_at, settings, preset FROM recommendation_runs WHERE run_id=?', (run,)).fetchone()
    preset = r['preset'] or 'p1'
    exported = exp['a']
    t1 = c.execute("SELECT MIN(e.exported_at) FROM exports e JOIN recommendation_runs r USING(run_id) "
                   "WHERE e.exported_at>? AND e.run_id<>? AND COALESCE(r.preset, 'p1')=?", (exported, run, preset)).fetchone()[0] or time.time()
    t0 = max(exported, since) if since else exported
    base = pp_at(c, pid, t0)
    fix = fixer(c, pid)
    songs = []
    for it in c.execute('SELECT ri.*, l.song_name, l.difficulty, l.stars, l.pass_rating FROM recommendation_items ri '
                        'JOIN leaderboards l USING(leaderboard_id) WHERE run_id=? AND exported=1 ORDER BY l.stars', (run,)).fetchall():
        lid = it['leaderboard_id']
        before = (json.loads(it['reason'] or '{}').get('atExport') or {}).get('bestAcc')
        if t0 > exported:   # 出力から since までに伸ばした自己ベスト
            pb0 = c.execute('SELECT acc FROM score_history WHERE leaderboard_id=? AND timeset>=? AND timeset<? ORDER BY timeset DESC LIMIT 1',
                            (lid, exported, t0)).fetchone()
            before = pb0['acc'] if pb0 else before
        tries = c.execute('SELECT acc, end_type FROM attempts WHERE leaderboard_id=? AND timeset>=? AND timeset<? ORDER BY timeset',
                          (lid, t0, t1)).fetchall()
        clear = max((a['acc'] for a in tries if a['end_type'] == 1 and a['acc']), default=None)
        pb = c.execute('SELECT timeset, acc, pp FROM score_history WHERE leaderboard_id=? AND timeset>=? AND timeset<? ORDER BY timeset DESC LIMIT 1',
                       (lid, t0, t1)).fetchone()   # 期間の終わりの自己ベスト
        got = pb['acc'] if pb else None
        if got and before is None:
            result = 'firstClear'
        elif got and got > before:
            result = 'improved'
        elif clear is not None or got:
            result = 'flat'
        else:
            result = 'tried' if tries else None
        best = max((x for x in (clear, got) if x is not None), default=None)
        new_pp = fix(lid, pb['timeset'], pb['acc'], pb['pp']) if result in SCORED else None
        songs.append({'id': lid, 'kind': it['kind'], 'stars': it['stars'], 'pass': it['pass_rating'] or 0, 'song': it['song_name'],
                      'difficulty': DIFF.get(it['difficulty'], it['difficulty']), 'est': it['est_acc'], 'before': before, 'best': best,
                      'got': got, 'result': result, 'tries': tries, 'new_pp': new_pp,
                      'gap': 100 * (best - it['est_acc']) if best is not None and it['est_acc'] else None,
                      'contrib': gain(base, {lid: new_pp}) if new_pp is not None else None})
    return {'run': run, 'preset': preset, 'method': exp['m'], 'settings': json.loads(r['settings'] or '{}'),
            'exported': exported, 't0': t0, 't1': t1, 'since': since, 'base': base, 'songs': songs}


def others(c, pid, t0, t1, skip):
    """[t0, t1) にリストの外（skip に無い譜面）で記録した自己ベスト（譜面ごとに期間の終わりの物。初めに記録した順。PP は今の式）"""
    out = {}
    fix = fixer(c, pid)
    for h in c.execute('SELECT h.leaderboard_id, h.acc, h.pp, h.timeset, l.song_name, l.difficulty, l.stars FROM score_history h '
                       'JOIN leaderboards l USING(leaderboard_id) WHERE h.timeset>=? AND h.timeset<? ORDER BY h.timeset', (t0, t1)):
        if h['leaderboard_id'] not in skip:
            out[h['leaderboard_id']] = {**dict(h), 'pp': fix(h['leaderboard_id'], h['timeset'], h['acc'], h['pp'])}
    return list(out.values())


def others_line(base, rows):
    """リストの外の自己ベストの行（総PP への寄与は合わせた値と曲ごと）"""
    if not rows:
        return []
    joint = gain(base, {h['leaderboard_id']: h['pp'] or 0.0 for h in rows})
    return ['- リストの外で自己ベストを更新（総PP への寄与 %s）: ' % signed(joint) + '、'.join(
        '%s %s（★%.2f）%s%%（%s）' % (h['song_name'], DIFF.get(h['difficulty'], h['difficulty']), h['stars'], pct(h['acc']),
                                    signed(gain(base, {h['leaderboard_id']: h['pp'] or 0.0}))) for h in rows)]


def snapshot_line(c, pid, t0, t1, computed):
    """BeatLeader の PP・国内順位の出力の前 → 結果の時点（取得の記録が無ければ出さない）。computed は自己ベストの記録から計算した差"""
    snap = lambda a: c.execute('SELECT pp, country_rank FROM player_snapshots WHERE player_id=? AND run_at<=? ORDER BY run_at DESC LIMIT 1',
                               (pid, a)).fetchone()
    p0, p1 = snap(t0), snap(t1)
    if not (p0 and p1):
        return []
    return ['- PP %.2f → %.2f（%+.2f。自己ベストの記録から計算すると %s）・国内 #%s → #%s' % (
        p0['pp'], p1['pp'], p1['pp'] - p0['pp'], signed(computed), p0['country_rank'], p1['country_rank'])]


def counts(songs):
    k = {key: [x for x in songs if x['result'] == key] for key in KIND_NAME}
    return {'n': len(songs), 'played': sum(1 for x in songs if x['tries'] or x['got']),
            'cleared': sum(len(v) for v in k.values()), **{key: len(v) for key, v in k.items()}}


def summary(info):
    """曲の数・総PP への寄与・推定との差の行"""
    songs, base = info['songs'], info['base']
    n = counts(songs)
    out = ['- 遊んだ曲 %d / %d・クリアした曲 %d（前の自己ベストを更新 %d・初クリア %d・伸ばせなかった %d）' % (
        n['played'], n['n'], n['cleared'], n['improved'], n['firstClear'], n['flat'])]
    parts = ['%s %d曲 %s' % (KIND_NAME[k], n[k], signed(gain(base, new_pps([x for x in songs if x['result'] == k]))))
             for k in ('improved', 'firstClear') if n[k]]
    out.append('- 総PP への寄与（リストの曲）: %s%s' % (signed(gain(base, new_pps(songs))), '（%s）' % '・'.join(parts) if parts else ''))
    gaps = [x for x in songs if x['gap'] is not None]
    if gaps:
        out.append('- 推定との差（クリアした %d 曲の平均）: %+.2f%%（%s）' % (
            len(gaps), avg([x['gap'] for x in gaps]),
            '・'.join('%s %s' % (name, gap_text([x['gap'] for x in gaps if x['result'] == k])) for k, name in KINDS)))
        out.append('- 推定との差を pass で分けると: pass %.1f 以下 %s・より上 %s' % (
            PASS_EASY, gap_text([x['gap'] for x in gaps if x['pass'] <= PASS_EASY]),
            gap_text([x['gap'] for x in gaps if x['pass'] > PASS_EASY])))
    return out


def table(info):
    out = ['| 区分 | ★ | pass | 曲 | 難易度 | 推定 | 前の自己ベスト | 今回一番のクリア | 推定との差 | 自己ベスト | 総PPへの寄与 | 挑戦 |',
           '| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |']
    for x in info['songs']:
        mark = '初クリア' if x['result'] == 'firstClear' else ('%+.2f' % (100 * (x['got'] - x['before'])) if x['result'] == 'improved' else '—')
        out.append('| %s | %.2f | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            'プレイ済み' if x['kind'] == 'played' else '未プレイ', x['stars'], x['pass'], x['song'], x['difficulty'], pct(x['est']),
            pct(x['before']), pct(x['best']), signed(x['gap']), mark, signed(x['contrib']),
            ' '.join('%s %.1f' % (END.get(a['end_type'], a['end_type']), 100 * (a['acc'] or 0)) for a in x['tries']) or 'なし'))
    return out


def conditions(s):
    """その回の設定の条件の行。★は、★の範囲（star_min・star_max。2026-10-25 から）を使った回なら「★の範囲 ★a〜★b」、
    前の回の記録（未プレイの★の上限＝プレイ済みの最高★＋unplayed_star_margin）なら今までの書き方"""
    out = '条件: %s曲・プレイ済みの比率 %s%%・挑戦枠 %s' % (s.get('playlist_size'), s.get('played_ratio'), s.get('challenge_max'))
    if 'cooldown_runs' in s:   # 2026-11-01 より前の回（伸びなかった曲を直近何回分の出力について外すか）
        out += '・クールダウン %s回' % s['cooldown_runs']
    elif 'replay_gap_days' in s:
        out += '・クールダウン 再プレイ %s日あける／3週間に%s日で%s週休み・初プレイ クリアできない日%s日で%s週休み' % (
            s['replay_gap_days'], s.get('replay_streak_days'), s.get('replay_rest_weeks'), s.get('unplayed_fail_days'),
            s.get('unplayed_rest_weeks'))
    lo, hi = s.get('star_min') or 0, s.get('star_max') or 0
    auto = s.get('star_min_auto')   # 下限を自動で決めた回（2026-11-08 から。{モード: ★ か None}）
    if auto is not None and lo <= 0:
        vals = [v for v in auto.values() if v]
        lo_text = '自動（%s）' % '・'.join('★%g' % v for v in vals) if vals else '自動（決められず下限なし）'
        out += '・★の範囲 %s〜%s' % (lo_text, '★%g' % hi if hi > 0 else '上限なし')
    elif lo > 0 or hi > 0:
        out += '・★の範囲 %s〜%s' % ('★%g' % lo if lo > 0 else '下限なし', '★%g' % hi if hi > 0 else '上限なし')
    elif 'unplayed_star_margin' in s:
        out += '・未プレイの★の上限 最高★＋%s' % s['unplayed_star_margin']
    return out


def since_text(since):
    return '・%s からの結果' % when(since) if since else ''


def report(c, pid, run, names=None, since=None):
    """1回の出力の表（--run・既定）"""
    info = collect(c, pid, run, since)
    t0, t1 = info['t0'], info['t1']
    name = (names or {}).get(info['preset']) or info['preset']
    out = ['## %s のプレイ（出力 %s・%s・セット %s%s）' % (when(t0, '%Y-%m-%d'), when(info['exported']), info['method'], name,
                                                    since_text(since)), '',
           conditions(info['settings']), ''] + table(info) + [''] + summary(info)
    out += snapshot_line(c, pid, t0, t1, total(pp_at(c, pid, t1)) - total(info['base']))
    out += others_line(info['base'], others(c, pid, t0, t1, {x['id'] for x in info['songs']}))
    return '\n'.join(out) + '\n'


# ---- 同じ日に並べて遊んだセットの比べ（--session） ----

def session_runs(c, hours, anchor=None, order=()):
    """最後の出力（anchor があればその回）からさかのぼって hours 時間以内の、セットごとの最後の出力（設定のセットの並び順）"""
    rows = c.execute("SELECT e.run_id, COALESCE(r.preset, 'p1') AS preset, MIN(e.exported_at) AS at FROM exports e "
                     'JOIN recommendation_runs r USING(run_id) GROUP BY e.run_id').fetchall()
    if not rows:
        raise SystemExit('出力の記録がありません')
    end = max(r['at'] for r in rows) if anchor is None else next((r['at'] for r in rows if r['run_id'] == anchor), None)
    if end is None:
        raise SystemExit('出力の回 %s が見つかりません' % anchor)
    last = {}
    for r in rows:
        if end - hours * 3600 <= r['at'] <= end and (r['preset'] not in last or r['at'] > last[r['preset']]['at']):
            last[r['preset']] = r
    pos = {p: i for i, p in enumerate(order)}
    return [r['run_id'] for r in sorted(last.values(), key=lambda r: (pos.get(r['preset'], len(pos)), r['at']))]


def session(c, pid, hours=SESSION_HOURS, names=None, anchor=None, since=None):
    names = names or {}
    infos = [collect(c, pid, run, since) for run in session_runs(c, hours, anchor, list(names))]
    t0, t1 = min(i['t0'] for i in infos), max(i['t1'] for i in infos)
    base = pp_at(c, pid, t0)
    name = lambda i: names.get(i['preset']) or i['preset']
    e0, e1 = min(i['exported'] for i in infos), max(i['exported'] for i in infos)
    out = ['## %s のセットの比べ（出力 %s%s・%dセット・最後の出力から %g 時間以内%s）' % (
               when(t0, '%Y-%m-%d'), when(e0), '〜' + when(e1, '%H:%M') if e1 != e0 else '', len(infos), hours, since_text(since)), '',
           '| セット | 出力 | 曲 | ★平均 | pass平均 | 遊んだ | クリア | 更新 | 初クリア | 伸ばせなかった | 推定との差 | '
           '初クリア / 更新 / 伸ばせなかった | 総PPへの寄与 |',
           '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |']

    def row(label, at, songs, contrib):
        n = counts(songs)
        gaps = [x for x in songs if x['gap'] is not None]
        by = ' / '.join(signed(avg([x['gap'] for x in gaps if x['result'] == k])) for k, _ in KINDS)
        return '| %s | %s | %d | %s | %s | %d | %d | %d | %d | %d | %s | %s | %s |' % (
            label, at, n['n'], '%.2f' % avg([x['stars'] for x in songs]) if songs else '—',
            '%.2f' % avg([x['pass'] for x in songs]) if songs else '—', n['played'], n['cleared'], n['improved'], n['firstClear'],
            n['flat'], gap_text([x['gap'] for x in gaps]), by, signed(contrib))
    every, seen = [], set()
    for i in infos:
        out.append(row(name(i), when(i['exported']), i['songs'], gain(i['base'], new_pps(i['songs']))))
        every += [x for x in i['songs'] if x['id'] not in seen]   # 独立のセットどうしで同じ曲がある時は、計では1回だけ数える
        seen |= {x['id'] for x in i['songs']}
    if len(infos) > 1:   # 合わせた総PP への寄与は、最初の出力の時点の並びに全部のセットの新しい自己ベストを入れる
        out.append(row('計', '', every, gain(base, new_pps(every))))
    out += ['', '- 推定との差は、その回に一番よかったクリアの精度 − 推定精度（クリアした曲の平均。初クリア / 更新 / 伸ばせなかった は %）']
    out += snapshot_line(c, pid, t0, t1, total(pp_at(c, pid, t1)) - total(base))
    out += others_line(base, others(c, pid, t0, t1, {x['id'] for x in every}))
    for i in infos:
        out += ['', '### セット %s（出力 %s・%s）' % (name(i), when(i['exported']), i['method']), '', conditions(i['settings']), '']
        out += table(i) + [''] + summary(i)
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=ROOT)
    ap.add_argument('--run', type=int, default=None)
    ap.add_argument('--session', type=float, nargs='?', const=SESSION_HOURS, default=None, metavar='時間')
    ap.add_argument('--since', default=None, metavar='日時', help='結果をこの時刻から数える（例 "2026-11-01 12:00"・日付だけなら0時）')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    since = int(datetime.fromisoformat(a.since).timestamp()) if a.since else None
    data = os.path.abspath(a.data)
    with open(os.path.join(data, 'config', 'settings.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    pid = cfg['player_id']
    names = {p['id']: p.get('name') or p['id'] for p in cfg.get('presets') or [] if isinstance(p, dict) and p.get('id')}
    uri = 'file:%s?mode=ro' % os.path.join(data, 'data', 'blnext.db').replace('\\', '/')
    c = sqlite3.connect(uri, uri=True)
    c.row_factory = sqlite3.Row
    if a.session is not None:
        text = session(c, pid, a.session, names, a.run, since)
    else:
        run = a.run or c.execute('SELECT run_id FROM exports ORDER BY exported_at DESC LIMIT 1').fetchone()[0]
        text = report(c, pid, run, names, since)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
