# DB の形の変更（core/store/db.py の MIGRATIONS）。前の版の DB を開くと、中身を残したまま新しい形になること
import sqlite3

from core.store import db
from core.store.db import Store


def make_v2(path):
    """版 2（設定のセットを作る前）の DB を作る"""
    c = sqlite3.connect(path, isolation_level=None)
    for i in range(2):
        for stmt in db._split_sql(db.MIGRATIONS[i]):
            c.execute(stmt)
    c.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
    c.execute("INSERT INTO user_marks VALUES('L1', 'pin', 10)")
    c.execute("INSERT INTO user_marks VALUES('L2', 'never', 11)")
    c.execute("INSERT INTO recommendation_runs(run_id, created_at, settings) VALUES(1, 100, '{}')")
    c.execute("INSERT INTO exports VALUES(1, 100, 'x.bplist')")
    c.close()


def test_v2_to_v3_keeps_marks_and_exports(tmp_path):
    p = str(tmp_path / 'old.db')
    make_v2(p)
    st = Store(p)
    assert st.meta('schema_version') == str(len(db.MIGRATIONS))
    # 固定はそれまでのセット（p1）の物、今後出さないは全セット共通（preset=''）
    assert {(r['leaderboard_id'], r['mark'], r['preset'], r['updated_at']) for r in st.q('SELECT * FROM user_marks')} == {
        ('L1', 'pin', 'p1', 10), ('L2', 'never', '', 11)}
    assert st.q('SELECT run_id, preset FROM recommendation_runs') == [{'run_id': 1, 'preset': 'p1'}]
    assert st.q('SELECT run_id, path, method FROM exports') == [{'run_id': 1, 'path': 'x.bplist', 'method': 'write'}]
    # 同じ曲を別のセットで固定できる
    with st.tx():
        st.conn.execute("INSERT INTO user_marks VALUES('L1', 'pin', 'p2', 12)")
    assert st.q1("SELECT COUNT(*) AS n FROM user_marks WHERE leaderboard_id='L1'")['n'] == 2
    st.close()
    Store(p).close()   # 開き直しても当て直さない


def test_v6_to_v7_adds_curve_inputs(tmp_path):
    # 譜面ごとの精度の曲線の材料（2026-10 のリウェイト）。今ある譜面は空のまま（PP の式の版が変わった取得で全部取り直して入る）
    p = str(tmp_path / 'v6.db')
    c = sqlite3.connect(p, isolation_level=None)
    for i in range(6):
        for stmt in db._split_sql(db.MIGRATIONS[i]):
            c.execute(stmt)
    c.execute("INSERT INTO meta(key, value) VALUES('schema_version', '6')")
    c.execute("INSERT INTO leaderboards(leaderboard_id, stars, predicted_acc) VALUES('L1', 5.0, 0.98)")
    c.close()
    st = Store(p)
    assert st.q('SELECT stars, predicted_acc, slider_percentage, note_span FROM leaderboards') == [
        {'stars': 5.0, 'predicted_acc': 0.98, 'slider_percentage': None, 'note_span': None}]
    assert st.meta('pp_formula') is None   # 次の取得が全部取り直す（core/fetch/refresh.py）
    st.close()


def test_v3_to_v4_refetches_maps_and_my_scores(tmp_path):
    # 譜面の特徴とスコアの ID を足した版。今ある分にも入れるため、次の取得でランク譜面の一覧と自分のスコアを全部取り直す
    p = str(tmp_path / 'v3.db')
    c = sqlite3.connect(p, isolation_level=None)
    for i in range(3):
        for stmt in db._split_sql(db.MIGRATIONS[i]):
            c.execute(stmt)
    c.executemany('INSERT INTO meta(key, value) VALUES(?, ?)', [('schema_version', '3'), ('maps_fetched_at', '100'),
                                                                 ('self_scores_synced_at', '100'), ('last_run_at', '100')])
    c.execute("INSERT INTO leaderboards(leaderboard_id, stars) VALUES('L1', 5.0)")
    c.execute("INSERT INTO scores(player_id, leaderboard_id, acc) VALUES('me', 'L1', 0.9)")
    c.close()
    st = Store(p)
    assert st.meta('maps_fetched_at') is None and st.meta('self_scores_synced_at') is None and st.meta('last_run_at') == '100'
    assert st.q('SELECT stars, njs, bpm, nps, tags FROM leaderboards') == [{'stars': 5.0, 'njs': None, 'bpm': None, 'nps': None, 'tags': None}]
    assert st.q('SELECT acc, score_id FROM scores') == [{'acc': 0.9, 'score_id': None}]
    st.close()
