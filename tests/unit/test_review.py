# 振り返り（core/review.py）: 出力 → プレイ → 次の取得で、伸びた / 初クリア / 伸びなかった / まだ を分けて記録する。
# 初クリア（出力の時に自己ベストが無かった曲）は「伸びた」と別に数え、推定との差も分ける。今ある記録は DB の移行で初クリアに直す。
# 作り物の BeatLeader を使わず、出力の記録と取得の後の状態を DB に直接入れて作る（fakebl を使う通しの確かめは test_estimate.py）。
# ホームの表の値（今回の結果の精度・曲の PP・総PP への寄与、出した時の見込み）も確かめる
import json, sqlite3, time
from types import SimpleNamespace

import pytest

from core import review
from core.store import db
from core.store.db import Store

PID = 'me'
T = int(time.time()) - 3 * 86400   # 出力の時刻
EST = {'L2': 0.92, 'L3': 0.90, 'L4': 0.84, 'L5': 0.80, 'L8': 0.95}


def _item(lid, kind):
    return {'leaderboard_id': lid, 'kind': kind, 'est_acc': EST[lid], 'est_pp': 200.0, 'gain': 1.0, 'rank_up': 0, 'confidence': 1.0}


@pytest.fixture
def st(tmp_path):
    """出力した5曲: L2・L3・L8 はプレイ済み（自己ベストあり）、L4・L5 は未プレイ。出力の後に
    L2 は自己ベストを伸ばし、L4 は初めてクリアし、L3 はクリアしたが伸びず、L5 は失敗だけ、L8 は遊んでいない"""
    s = Store(str(tmp_path / 'db.sqlite'))
    old = T - 10 * 86400
    with s.tx():
        for i, lid in enumerate(EST):
            s.upsert('leaderboards', {'leaderboard_id': lid, 'song_name': 'Song ' + lid, 'difficulty': 'Expert', 'stars': 6 + i * 0.1,
                                      'status': 3}, ['leaderboard_id'])
        for lid, acc in (('L2', 0.90), ('L3', 0.89), ('L8', 0.94)):
            s.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': 200, 'timeset': old, 'play_count': 3,
                                'last_try_time': old}, ['player_id', 'leaderboard_id'])
    model = SimpleNamespace(my_scores={r['leaderboard_id']: r for r in s.q('SELECT * FROM scores WHERE player_id=?', (PID,))})
    items = [_item('L2', 'played'), _item('L3', 'played'), _item('L8', 'played'), _item('L4', 'unplayed'), _item('L5', 'unplayed')]
    review.record_export(s, model, items, [], {'playlist_size': 5}, 'x.bplist', T)
    with s.tx():
        s.upsert('scores', {'player_id': PID, 'leaderboard_id': 'L2', 'acc': 0.93, 'timeset': T + 3600, 'play_count': 4,
                            'last_try_time': T + 3600}, ['player_id', 'leaderboard_id'])
        s.upsert('scores', {'player_id': PID, 'leaderboard_id': 'L4', 'acc': 0.80, 'pp': 150, 'timeset': T + 7200, 'play_count': 1,
                            'last_try_time': T + 7200}, ['player_id', 'leaderboard_id'])
        s.upsert('scores', {'player_id': PID, 'leaderboard_id': 'L3', 'last_try_time': T + 1000, 'play_count': 4},
                 ['player_id', 'leaderboard_id'])
        for lid, ts, acc, end in (('L2', T + 3600, 0.93, 1), ('L4', T + 7000, 0.70, 2), ('L4', T + 7200, 0.80, 1),
                                  ('L3', T + 1000, 0.86, 1), ('L5', T + 2000, 0.60, 2)):
            s.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)', (lid, ts, acc, end, ''))
    review.update(s, PID, T + 9000)
    yield s
    s.close()


def test_first_clear_is_not_improved(st):
    rows = {r['leaderboard_id']: r for r in st.q('SELECT * FROM reviews')}
    assert {k: r['status'] for k, r in rows.items()} == {'L2': 'improved', 'L4': 'firstClear', 'L3': 'played', 'L5': 'played',
                                                         'L8': 'notPlayed'}
    assert rows['L4']['best_before'] is None and rows['L4']['best_after'] == 0.80   # 初クリアも新しい自己ベストを残す
    assert rows['L2']['best_before'] == 0.90 and rows['L2']['best_after'] == 0.93
    assert rows['L3']['best_after'] is None and rows['L3']['attempt_max'] == 0.86
    assert rows['L5']['attempt_max'] is None                                        # 失敗だけなら実際の精度は分からない


def test_counts_and_est_error_split(st):
    last = review.last(st)
    r = last['runs'][0]
    assert r['counts'] == {'improved': 1, 'firstClear': 1, 'played': 2, 'notPlayed': 1}
    assert {x['song']: x['status'] for x in r['items']}['Song L4'] == 'firstClear'
    for e in (r['estError'], last['estError']):       # この回の分と、これまでの分（記録は1回だけなので同じ）
        assert e['improved']['n'] == 1 and abs(e['improved']['mean'] - 0.01) < 1e-9       # 0.93 − 0.92
        assert e['firstClear']['n'] == 1 and abs(e['firstClear']['mean'] + 0.04) < 1e-9   # 0.80 − 0.84
        assert e['played']['n'] == 1 and abs(e['played']['mean'] + 0.04) < 1e-9           # 0.86 − 0.90（L5 は失敗だけ）
        assert e['n'] == 2 and abs(e['mean'] + 0.015) < 1e-9     # 上の平均は初クリアを混ぜない（伸びた・伸びなかった）


def test_first_clear_stays_on_next_refresh(st):
    # 次の取得でも同じ判定（作り直しても初クリアは初クリアのまま。その後に伸ばしても、出力の時に自己ベストが無かったことは変わらない）
    with st.tx():
        st.upsert('scores', {'player_id': PID, 'leaderboard_id': 'L4', 'acc': 0.83, 'timeset': T + 80000},
                  ['player_id', 'leaderboard_id'])
    review.update(st, PID, T + 90000)
    row = st.q1("SELECT status, best_after FROM reviews WHERE leaderboard_id='L4'")
    assert row == {'status': 'firstClear', 'best_after': 0.83}


def test_export_keeps_estimate_parts_and_fc_acc(tmp_path):
    # 出力の記録の reason に、推定の内訳（parts）と、出力した時点の自己ベストの「FC していた場合の精度」（atExport.fcAcc）を残す
    # （直しごとの当たりを後で確かめるため）。内訳を持たない曲（内訳を足す前の版で作ったリスト）は None
    s = Store(str(tmp_path / 'db.sqlite'))
    with s.tx():
        for lid in ('L2', 'L4'):
            s.upsert('leaderboards', {'leaderboard_id': lid, 'stars': 6.0, 'status': 3}, ['leaderboard_id'])
        s.upsert('scores', {'player_id': PID, 'leaderboard_id': 'L2', 'acc': 0.90, 'fc_acc': 0.93, 'pp': 200, 'timeset': T - 86400},
                 ['player_id', 'leaderboard_id'])
    model = SimpleNamespace(my_scores={r['leaderboard_id']: r for r in s.q('SELECT * FROM scores WHERE player_id=?', (PID,))})
    parts = {'base': 0.9, 'mix': 0.005, 'r1': 0.01, 'r3': 0.005}
    run = review.record_export(s, model, [{**_item('L2', 'played'), 'parts': parts, 'clear': 0.7}], [_item('L4', 'unplayed')],
                               {'playlist_size': 1}, 'x.bplist', T)
    got = {r['leaderboard_id']: json.loads(r['reason']) for r in s.q('SELECT leaderboard_id, reason FROM recommendation_items WHERE run_id=?',
                                                                     (run,))}
    assert got['L2']['parts'] == parts and got['L2']['atExport']['fcAcc'] == 0.93 and got['L2']['atExport']['bestAcc'] == 0.90
    assert got['L4']['parts'] is None and got['L4']['atExport']['fcAcc'] is None
    # クリアの見込み（ホームの振り返りの「出した時の見込み」に使う）。求めない曲は None（キーはある＝残す版の記録）
    assert got['L2']['clear'] == 0.7 and 'clear' in got['L4'] and got['L4']['clear'] is None
    s.close()


# ---- ホームの表: 今回の結果（精度・曲の PP・総PP への寄与）と出した時の狙い（推定精度・見込み） ----

W = 0.965
RATED = {'status': 3, 'mode': 'Standard', 'acc_rating': 8.0, 'pass_rating': 4.0, 'tech_rating': 3.0, 'predicted_acc': 0.95,
         'notes': 1000, 'note_span': 120}


def brute(pps):
    """総PP（確かめ用に式どおり計算する）"""
    return sum(p * W ** i for i, p in enumerate(sorted([p for p in pps if p > 0], reverse=True)))


@pytest.fixture
def res(tmp_path):
    """自己ベスト L1 300PP・L2 200PP・L3 100PP（score_history にも）。T に L2・L3・L4・L5・L6 を出力（クリアの見込みも残す）し、
    出力の後に L2 を伸ばし（200→250PP）、L4 を初クリア（50PP）、L3 はクリアしたが伸ばせず（86%）、L5 は失敗だけ、L6 は遊んでいない。
    L3 は譜面の評価があるので、伸ばせなかった精度の PP（参考）を今の式で求められる"""
    s = Store(str(tmp_path / 'db.sqlite'))
    old = T - 10 * 86400
    with s.tx():
        for i, lid in enumerate(('L1', 'L2', 'L3', 'L4', 'L5', 'L6')):
            s.upsert('leaderboards', {'leaderboard_id': lid, 'song_name': 'Song ' + lid, 'difficulty': 'Expert', 'stars': 6 + i * 0.1,
                                      'status': 3, **(RATED if lid == 'L3' else {})}, ['leaderboard_id'])
        for lid, acc, pp in (('L1', 0.95, 300.0), ('L2', 0.90, 200.0), ('L3', 0.89, 100.0)):
            s.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': pp, 'timeset': old, 'play_count': 3,
                                'last_try_time': old}, ['player_id', 'leaderboard_id'])
            s.conn.execute('INSERT INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) VALUES(?,?,?,?,?)', (lid, acc, pp, old, old))
    model = SimpleNamespace(my_scores={r['leaderboard_id']: r for r in s.q('SELECT * FROM scores WHERE player_id=?', (PID,))})
    est = {'L2': (0.92, 2.0, 0.8), 'L3': (0.90, 1.0, 0.5), 'L4': (0.84, 0.5, None), 'L5': (0.80, 0.3, 0.9), 'L6': (0.95, 0.2, 1.0)}
    items = [{'leaderboard_id': lid, 'kind': 'played' if lid in model.my_scores else 'unplayed', 'est_acc': e, 'est_pp': 200.0, 'gain': g,
              'clear': c, 'rank_up': 0, 'confidence': 1.0} for lid, (e, g, c) in est.items()]
    run = review.record_export(s, model, items, [], {'playlist_size': 5}, 'x.bplist', T, preset='p1')
    with s.tx():
        for lid, acc, pp, ts in (('L2', 0.93, 250.0, T + 3600), ('L4', 0.80, 50.0, T + 7200)):
            s.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': pp, 'timeset': ts, 'last_try_time': ts},
                     ['player_id', 'leaderboard_id'])
            s.conn.execute('INSERT INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) VALUES(?,?,?,?,?)', (lid, acc, pp, ts, ts))
        for lid, ts, acc, end in (('L2', T + 3600, 0.93, 1), ('L4', T + 7200, 0.80, 1), ('L3', T + 1000, 0.86, 1), ('L3', T + 1200, 0.84, 1),
                                  ('L5', T + 2000, 0.60, 2)):
            s.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)', (lid, ts, acc, end, ''))
    review.update(s, PID, T + 9000)
    yield s, run
    s.close()


def test_result_columns_and_total_pp(res):
    from core.estimate import ppcalc
    s, run = res
    r = review.last(s, PID)['runs'][0]
    by = {x['id']: x for x in r['items']}
    assert [x['id'] for x in r['items']] == ['L2', 'L3', 'L4', 'L5', 'L6']        # ★の低い順
    base = [300, 200, 100]
    # 伸びた: 新しい自己ベストの精度・PP と、出力の時点の並びに入れた時の総PP の差
    assert by['L2']['status'] == 'improved' and by['L2']['acc'] == 0.93 and by['L2']['pp'] == 250.0 and by['L2']['before'] == 0.90
    assert by['L2']['contrib'] == pytest.approx(brute([300, 250, 100]) - brute(base))
    # 初クリア: 曲の PP（50）ではなく、並びに入れた時の差（自分の上位に入らないので小さい）
    assert by['L4']['status'] == 'firstClear' and by['L4']['before'] is None and by['L4']['pp'] == 50.0
    assert by['L4']['contrib'] == pytest.approx(brute(base + [50]) - brute(base)) and by['L4']['contrib'] < 50
    # 伸びなかった: 出力の後のクリアの一番（86%）と、その精度の PP（参考。今の式）。総PP には足されない
    lb = s.q1("SELECT * FROM leaderboards WHERE leaderboard_id='L3'")
    assert by['L3']['status'] == 'played' and by['L3']['acc'] == 0.86 and by['L3']['contrib'] is None
    assert by['L3']['pp'] == pytest.approx(ppcalc.pp_from_row(0.86, lb)['pp'])
    assert by['L5']['status'] == 'played' and by['L5']['acc'] is None and by['L5']['pp'] is None   # 失敗だけ（クリアなし）
    assert by['L6']['status'] == 'notPlayed' and by['L6']['acc'] is None
    # 出した時の狙い: 推定精度・見込み（recommendation_items の gain）・クリアの見込み
    assert (by['L2']['est'], by['L2']['expGain'], by['L2']['clear']) == (0.92, 2.0, 0.8)
    # まとめ: 全部の新しい自己ベストを一度に入れた差・出した時の見込み（Σ クリアの見込み × 見込み。見込みが無い曲は 1）・遊んだ曲
    assert r['gain'] == pytest.approx(brute([300, 250, 100, 50]) - brute(base))
    assert r['expected'] == pytest.approx(2.0 * 0.8 + 1.0 * 0.5 + 0.5 * 1 + 0.3 * 0.9 + 0.2 * 1.0) and r['expectedClear'] is True
    assert r['played'] == 4 and r['checked'] is True and r['counts'] == {'improved': 1, 'firstClear': 1, 'played': 2, 'notPlayed': 1}


def test_expected_without_clear_record(res):
    # クリアの見込みを残す前の版の記録（reason に clear が無い）は、出した時の見込みを Σ 見込みで出し、expectedClear が False
    s, run = res
    with s.tx():
        for row in s.q('SELECT leaderboard_id, reason FROM recommendation_items WHERE run_id=?', (run,)):
            reason = json.loads(row['reason'])
            reason.pop('clear')
            s.conn.execute('UPDATE recommendation_items SET reason=? WHERE run_id=? AND leaderboard_id=?',
                           (json.dumps(reason), run, row['leaderboard_id']))
    r = review.last(s, PID)['runs'][0]
    assert r['expected'] == pytest.approx(2.0 + 1.0 + 0.5 + 0.3 + 0.2) and r['expectedClear'] is False


def test_result_before_refresh_and_preset_order(res):
    # 出力の後にまだ取得していない回は、出力した曲が全部「まだ」（checked が False）。presets を渡すと、そのセットだけをその順に
    s, run = res
    model = SimpleNamespace(my_scores={})
    review.record_export(s, model, [{'leaderboard_id': 'L6', 'kind': 'unplayed', 'est_acc': 0.9, 'est_pp': 100.0, 'gain': 1.5, 'clear': 0.5,
                                     'rank_up': 0, 'confidence': 1.0}], [], {}, 'y.bplist', T + 20000, preset='p2')
    last = review.last(s, PID)
    assert [x['preset'] for x in last['runs']] == ['p2', 'p1']                      # 省くと新しい順
    p2 = last['runs'][0]
    assert p2['checked'] is False and p2['played'] == 0 and [x['status'] for x in p2['items']] == ['notPlayed']
    assert p2['expected'] == pytest.approx(0.75) and p2['gain'] == 0.0
    assert [x['preset'] for x in review.last(s, PID, ['p1', 'p2'])['runs']] == ['p1', 'p2']   # セットの並びの順
    assert [x['preset'] for x in review.last(s, PID, ['p3', 'p2'])['runs']] == ['p2']        # 無いセット（消した）は出さない
    assert review.last(s, PID, ['p9']) is None


def test_no_review_without_export(tmp_path):
    s = Store(str(tmp_path / 'db.sqlite'))
    review.update(s, PID, T)
    assert review.last(s) is None
    s.close()


def test_migration_marks_old_first_clears(tmp_path):
    # 初クリアを分ける前の DB（improved だけ）: 出力の時の自己ベストが無い行を初クリアに直す。ほかの状態はそのまま
    i = next(k for k, m in enumerate(db.MIGRATIONS) if "status='firstClear'" in m)
    path = str(tmp_path / 'old.sqlite')
    c = sqlite3.connect(path, isolation_level=None)
    for m in db.MIGRATIONS[:i]:
        for stmt in db._split_sql(m):
            c.execute(stmt)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(i),))
    for lid, status, before, after in (('A', 'improved', None, 0.8), ('B', 'improved', 0.9, 0.93), ('C', 'played', None, None),
                                       ('D', 'notPlayed', None, None)):
        c.execute('INSERT INTO reviews(run_id, leaderboard_id, status, est_acc, best_before, best_after) VALUES(1,?,?,0.9,?,?)',
                  (lid, status, before, after))
    c.close()
    s = Store(path)
    assert s.meta('schema_version') == str(len(db.MIGRATIONS))
    assert {r['leaderboard_id']: r['status'] for r in s.q('SELECT * FROM reviews')} == {
        'A': 'firstClear', 'B': 'improved', 'C': 'played', 'D': 'notPlayed'}
    s.close()
