# tools/play_report.py: 出力したプレイリストの結果の表（遊んだ後の振り返りの記録）。作り物のデータ（出力の後に1曲伸ばしてある）で作る。
# 初クリア・総PP への寄与・難しさ違いのセットを並べた比べ（--session）は、出力の記録と遊んだ後の状態を DB に直接入れて確かめる
import json, os, sqlite3, subprocess, sys, time
from datetime import datetime
from types import SimpleNamespace

import pytest

import fixture_data

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import play_report   # noqa: E402


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp('built')
    info = fixture_data.build(str(d))
    return d, info


def db(d):
    c = sqlite3.connect('file:%s?mode=ro' % (d / 'data' / 'blnext.db').as_posix(), uri=True)
    c.row_factory = sqlite3.Row
    return c


def rows_of(text):
    return [l for l in text.splitlines() if l.startswith('| ') and not l.startswith('| 区分') and not l.startswith('| ---')]


def test_report_of_last_export(built):
    d, info = built
    c = db(d)
    run = c.execute('SELECT run_id FROM exports ORDER BY exported_at DESC LIMIT 1').fetchone()[0]
    n = c.execute('SELECT COUNT(*) FROM recommendation_items WHERE run_id=? AND exported=1', (run,)).fetchone()[0]
    text = play_report.report(c, info['self_id'], run)
    assert text.startswith('## ') and len(rows_of(text)) == n
    assert '前の自己ベストを更新 1・初クリア 0' in text and '推定との差（クリアした 1 曲' in text   # 出力の後に1曲伸ばしてある
    # 作り物のデータは出力の時刻が最初の取得より前なので、出力の前の PP が無い＝ BeatLeader の PP の行は出さない
    assert not any(l.startswith('- PP ') for l in text.splitlines())


def test_cli_writes_file_and_unknown_run(built, tmp_path):
    d, _ = built
    out = tmp_path / 'r.md'
    py = sys.executable
    r = subprocess.run([py, '-X', 'utf8', os.path.join(ROOT, 'tools', 'play_report.py'), '--data', str(d), '--out', str(out)],
                       capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0 and out.read_text(encoding='utf-8').startswith('## ')
    r = subprocess.run([py, '-X', 'utf8', os.path.join(ROOT, 'tools', 'play_report.py'), '--data', str(d), '--run', '9999'],
                       capture_output=True, text=True, encoding='utf-8')
    assert r.returncode != 0 and '9999' in (r.stderr + r.stdout)


# ---- 初クリア・総PP への寄与・セットの比べ（DB に直接入れた作り物） ----

PID = 'me'
T = int(time.time()) - 5 * 86400   # 標準（p1）の出力。着実（p2）は10分後に出力した
W = 0.965


def brute(pps):
    """総PP（確かめ用に式どおり計算する）"""
    s = sorted([p for p in pps if p > 0], reverse=True)
    return sum(p * W ** i for i, p in enumerate(s))


@pytest.fixture
def sess(tmp_path):
    """自己ベスト L1 300PP・L2 200PP・L3 100PP。2日前に標準（p1）で L3 を出力（遊ばず）。T に標準で L2・L4、T+10分に着実（p2）で
    L3・L5・L6 を出力し、出力の後に L2 を伸ばし（200→250PP）、L4 を初クリア（50PP。着実の出力より後）、L3 はクリアしたが伸ばせず、
    L5 は失敗だけ、L6 を初クリア（40PP）。リストの外の L7 も初めての自己ベスト（120PP）"""
    from core import review
    from core.store.db import Store
    data = tmp_path / 'data'
    st = Store(str(data / 'data' / 'blnext.db'))
    old = T - 10 * 86400
    est = {'L1': 0.95, 'L2': 0.92, 'L3': 0.90, 'L4': 0.84, 'L5': 0.80, 'L6': 0.88, 'L7': 0.9}
    with st.tx():
        for i, lid in enumerate(est):
            st.upsert('leaderboards', {'leaderboard_id': lid, 'song_name': 'Song ' + lid, 'difficulty': 'ExpertPlus', 'stars': 6 + i * 0.2,
                                       'pass_rating': 2.5 + i * 0.3, 'status': 3}, ['leaderboard_id'])
        for lid, acc, pp in (('L1', 0.95, 300), ('L2', 0.90, 200), ('L3', 0.89, 100)):
            st.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': pp, 'timeset': old, 'play_count': 3},
                      ['player_id', 'leaderboard_id'])
            st.conn.execute('INSERT INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) VALUES(?,?,?,?,?)',
                            (lid, acc, pp, old, old))
        st.conn.execute('INSERT INTO player_snapshots(run_at, player_id, pp, country_rank) VALUES(?,?,?,?)', (T - 100, PID, 410.0, 50))
    model = SimpleNamespace(my_scores={r['leaderboard_id']: r for r in st.q('SELECT * FROM scores WHERE player_id=?', (PID,))})
    item = lambda lid: {'leaderboard_id': lid, 'kind': 'played' if lid in model.my_scores else 'unplayed', 'est_acc': est[lid],
                        'est_pp': 200.0, 'gain': 1.0, 'rank_up': 0, 'confidence': 1.0}
    runs = {}
    for key, preset, ids, at in (('old', 'p1', ['L3'], T - 2 * 86400), ('std', 'p1', ['L2', 'L4'], T),
                                 ('easy', 'p2', ['L3', 'L5', 'L6'], T + 600)):
        runs[key] = review.record_export(st, model, [item(x) for x in ids], [], {'playlist_size': len(ids)}, 'x.bplist', at,
                                         preset=preset, method='oneclick')
    with st.tx():
        for lid, acc, pp, ts in (('L2', 0.93, 250, T + 3600), ('L4', 0.80, 50, T + 7200), ('L6', 0.85, 40, T + 4000),
                                 ('L7', 0.97, 120, T + 5000)):
            st.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': pp, 'timeset': ts},
                      ['player_id', 'leaderboard_id'])
            st.conn.execute('INSERT INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) VALUES(?,?,?,?,?)',
                            (lid, acc, pp, ts, T + 9000))
        for lid, ts, acc, end in (('L2', T + 3600, 0.93, 1), ('L4', T + 7200, 0.80, 1), ('L6', T + 4000, 0.85, 1),
                                  ('L3', T + 1000, 0.86, 1), ('L5', T + 2000, 0.60, 2)):
            st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, end_type, modifiers) VALUES(?,?,?,?,?)', (lid, ts, acc, end, ''))
        st.conn.execute('INSERT INTO player_snapshots(run_at, player_id, pp, country_rank) VALUES(?,?,?,?)', (T + 9000, PID, 600.0, 40))
    st.close()
    os.makedirs(data / 'config')
    (data / 'config' / 'settings.json').write_text(json.dumps({'player_id': PID, 'presets': [
        {'id': 'p2', 'name': '着実', 'values': {}}, {'id': 'p1', 'name': '標準', 'values': {}}]}, ensure_ascii=False), encoding='utf-8')
    c = db(data)
    yield data, c, runs
    c.close()


def line(text, head):
    return next(l for l in text.splitlines() if l.startswith(head))


def song_row(text, lid):
    return next(r for r in rows_of(text) if '| Song %s |' % lid in r).split(' | ')


def test_first_clear_and_total_pp_contribution(sess):
    _, c, runs = sess
    text = play_report.report(c, PID, runs['std'], {'p1': '標準'})
    assert text.startswith('## ') and 'セット 標準' in text
    # 着実（別のセット）の出力では期間を切らない＝その後の L4 の初クリアも標準の結果に入る
    assert '遊んだ曲 2 / 2・クリアした曲 2（前の自己ベストを更新 1・初クリア 1・伸ばせなかった 0）' in text
    base = [300, 200, 100]
    up, first = brute([300, 250, 100]) - brute(base), brute(base + [50]) - brute(base)
    assert song_row(text, 'L2')[9:11] == ['+3.00', '%+.2f' % up]          # 自己ベストの伸び（%）・総PP への寄与
    assert song_row(text, 'L4')[9:11] == ['初クリア', '%+.2f' % first]     # 曲の PP（50）ではなく、並びに入れた時の差
    assert line(text, '- 総PP への寄与') == '- 総PP への寄与（リストの曲）: %+.2f（更新 1曲 %+.2f・初クリア 1曲 %+.2f）' % (
        brute([300, 250, 100, 50]) - brute(base), up, first)
    # 推定との差は初クリア / 更新 / 伸ばせなかった に分ける（L2 +1.00・L4 −4.00）
    assert line(text, '- 推定との差（') == '- 推定との差（クリアした 2 曲の平均）: -1.50%（初クリア -4.00%（1曲）・更新 +1.00%（1曲）・伸ばせなかった —）'
    # BeatLeader の PP と、自己ベストの記録から計算し直した差（リストの外の L7・着実の L6 も入る）
    after = brute([300, 250, 100, 50, 40, 120])
    assert line(text, '- PP ') == '- PP 410.00 → 600.00（+190.00。自己ベストの記録から計算すると %+.2f）・国内 #50 → #40' % (after - brute(base))
    assert '- リストの外で自己ベストを更新' in text and 'Song L6' in line(text, '- リストの外') and 'Song L7' in line(text, '- リストの外')


def test_flat_and_failed(sess):
    _, c, runs = sess
    text = play_report.report(c, PID, runs['easy'])
    assert 'セット p2' in text   # 名前が分からなければセットの id
    assert '遊んだ曲 3 / 3・クリアした曲 2（前の自己ベストを更新 0・初クリア 1・伸ばせなかった 1）' in text
    assert song_row(text, 'L3')[8:11] == ['-4.00', '—', '—']           # クリアしたが前の自己ベスト（89%）に届かない
    assert song_row(text, 'L5')[8:12] == ['—', '—', '—', '失敗 60.0 |']
    # 2日前の標準の出力は、次の標準の出力（T）までで切る（その後に遊んだ L3 は数えない）
    assert '遊んだ曲 0 / 1' in play_report.report(c, PID, runs['old'])


def test_session_compares_presets(sess):
    data, c, runs = sess
    names = {'p2': '着実', 'p1': '標準'}
    text = play_report.session(c, PID, 6, names)
    table = [l for l in text.split('\n\n')[1].splitlines()[2:]]
    assert [r.split(' | ')[0] for r in table] == ['| 着実', '| 標準', '| 計']   # 設定のセットの並び。2日前の出力は入れない
    easy, std, both = (r.split(' | ') for r in table)
    assert easy[2:10] == ['3', '6.73', '3.60', '3', '2', '0', '1', '1'] and std[2:10] == ['2', '6.40', '3.10', '2', '2', '1', '1', '0']
    assert both[2] == '5' and both[5:10] == ['5', '4', '1', '2', '1']
    base = [300, 200, 100]
    assert easy[-1] == '%+.2f |' % (brute(base + [40]) - brute(base))
    assert both[-1] == '%+.2f |' % (brute([300, 250, 100, 50, 40]) - brute(base))      # 最初の出力の時点の並びに全部入れる
    assert easy[10:12] == ['-3.50%（2曲）', '-3.00 / — / -4.00'] and both[10] == '-2.50%（4曲）'
    other = line(text, '- リストの外')
    assert 'Song L7' in other and 'Song L6' not in other         # 比べたセットの曲はリストの外に入れない
    assert '### セット 着実' in text and '### セット 標準' in text and text.index('### セット 着実') < text.index('### セット 標準')
    # --run と一緒に使うと、その回までさかのぼる（2日前の出力だけ）
    assert [r.split(' | ')[0] for r in play_report.session(c, PID, 6, names, runs['old']).split('\n\n')[1].splitlines()[2:]] == ['| 標準']
    out = data / 'session.md'
    r = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(ROOT, 'tools', 'play_report.py'), '--data', str(data), '--session',
                        '--out', str(out)], capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding='utf-8') == text


def test_since_counts_from_that_time(sess):
    """--since: 同じリストを別の日にもう一度遊んだ時、その時刻からの結果だけを見る。前の自己ベスト・総PP への寄与の元の並びも
    その時点の物（L2 は出力の後の T+1時間に 250PP に伸ばしてある）。出力の時刻はそのまま出す"""
    data, c, runs = sess
    since = T + 3700
    text = play_report.report(c, PID, runs['std'], {'p1': '標準'}, since)
    assert line(text, '## ').endswith('（出力 %s・oneclick・セット 標準・%s からの結果）' % (play_report.when(T), play_report.when(since)))
    assert '遊んだ曲 1 / 2・クリアした曲 1（前の自己ベストを更新 0・初クリア 1・伸ばせなかった 0）' in text
    assert song_row(text, 'L2')[6:8] == ['93.00', '—']                    # 前の自己ベストは since の時点（伸ばした後）
    base = [300, 250, 100]
    assert song_row(text, 'L4')[9:11] == ['初クリア', '%+.2f' % (brute(base + [50]) - brute(base))]
    other = line(text, '- リストの外')
    assert 'Song L6' in other and 'Song L7' in other
    # セットの比べ: since より前の挑戦（着実の L3・L5）は数えない。表の「出力」は出力の時刻
    table = play_report.session(c, PID, 6, {'p2': '着実', 'p1': '標準'}, since=since).split('\n\n')[1].splitlines()[2:]
    easy, std = table[0].split(' | '), table[1].split(' | ')
    assert easy[1] == play_report.when(T + 600) and easy[5:10] == ['1', '1', '0', '1', '0']
    assert std[1] == play_report.when(T) and std[5:10] == ['1', '1', '0', '1', '0']
    # コマンドラインの --since（ISO の日時）も同じ
    r = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(ROOT, 'tools', 'play_report.py'), '--data', str(data), '--run',
                        str(runs['std']), '--since', datetime.fromtimestamp(since).isoformat()], capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr
    assert r.stdout == play_report.report(c, PID, runs['std'], {'p2': '着実', 'p1': '標準'}, since)


def test_session_counts_shared_song_once(sess):
    """独立のセット（ほかのセットと同じ曲を入れないがオフ）で同じ曲がある時、計では1回だけ数える"""
    from core import review
    from core.store.db import Store
    data, c, runs = sess
    st = Store(str(data / 'data' / 'blnext.db'))
    model = SimpleNamespace(my_scores={'L2': {'acc': 0.90, 'timeset': T - 10 * 86400, 'play_count': 3}})
    review.record_export(st, model, [{'leaderboard_id': 'L2', 'kind': 'played', 'est_acc': 0.92, 'est_pp': 200.0, 'gain': 1.0,
                                      'rank_up': 0, 'confidence': 1.0}], [], {'playlist_size': 1}, 'x.bplist', T + 1200,
                         preset='p3', method='oneclick')
    st.close()
    table = play_report.session(c, PID, 6, {'p2': '着実', 'p1': '標準', 'p3': '再プレイ'}).split('\n\n')[1].splitlines()[2:]
    assert [r.split(' | ')[0] for r in table] == ['| 着実', '| 標準', '| 再プレイ', '| 計']
    again, both = table[2].split(' | '), table[3].split(' | ')
    assert again[2] == '1' and again[5:8] == ['1', '1', '1']          # セットの行では数える
    assert both[2] == '5' and both[5:10] == ['5', '4', '1', '2', '1']   # 計は L2 を2回数えない（重ならない時と同じ）
    assert both[10] == '-2.50%（4曲）'


def test_conditions_star_range():
    """条件の行: ★の範囲（2026-10-25 から）を使った回は「★の範囲 ★a〜★b」（0 の側は「下限なし」「上限なし」・両方 0 なら出さない）。
    前の回の記録（未プレイの★の上限＝最高★＋unplayed_star_margin）は今までの書き方"""
    base = {'playlist_size': 10, 'played_ratio': 50, 'challenge_max': 1, 'cooldown_runs': 5}
    head = '条件: 10曲・プレイ済みの比率 50%・挑戦枠 1・クールダウン 5回'
    assert play_report.conditions({**base, 'star_min': 7.5, 'star_max': 9}) == head + '・★の範囲 ★7.5〜★9'
    assert play_report.conditions({**base, 'star_min': 0, 'star_max': 9.5}) == head + '・★の範囲 下限なし〜★9.5'
    assert play_report.conditions({**base, 'star_min': 8, 'star_max': 0}) == head + '・★の範囲 ★8〜上限なし'
    assert play_report.conditions({**base, 'star_min': 0, 'star_max': 0}) == head
    # 下限を自動で決めた回（2026-11-08 から。その時の下限を star_min_auto に残す）
    assert play_report.conditions({**base, 'star_min': 0, 'star_max': 6.5, 'star_min_auto': {'Standard': 5.5}}) == head + '・★の範囲 自動（★5.5）〜★6.5'
    assert play_report.conditions({**base, 'star_min': 0, 'star_max': 0, 'star_min_auto': {'Standard': None}}) == head + '・★の範囲 自動（決められず下限なし）〜上限なし'
    assert play_report.conditions({**base, 'star_min': 6, 'star_max': 7, 'star_min_auto': None}) == head + '・★の範囲 ★6〜★7'
    assert play_report.conditions({**base, 'unplayed_star_margin': 0.5}) == head + '・未プレイの★の上限 最高★＋0.5'
    # 2026-11-01 からのクールダウン（遊んだ日で数える）
    new = {k: v for k, v in base.items() if k != 'cooldown_runs'}
    assert play_report.conditions({**new, 'replay_gap_days': 3, 'replay_streak_days': 3, 'replay_rest_weeks': 4, 'unplayed_fail_days': 3,
                                   'unplayed_rest_weeks': 4}) == (
        '条件: 10曲・プレイ済みの比率 50%・挑戦枠 1・クールダウン 再プレイ 3日あける／3週間に3日で4週休み・初プレイ クリアできない日3日で4週休み')


def test_pp_uses_current_formula(tmp_path):
    """score_history の PP は記録した時の式の値（リウェイトの前の記録は古い式のまま。混ぜると寄与が大きく出た）。
    並びの PP は今の式にそろえる: 今の自己ベストと同じ記録は scores の PP、前の記録は精度から ppcalc で求め直す
    （譜面の評価が無ければ記録の PP のまま）。リストの外の自己ベストの寄与も同じ"""
    from core.estimate import ppcalc
    from core.store.db import Store
    t0, t1 = T - 86400, T + 3600
    st = Store(str(tmp_path / 'blnext.db'))
    rated = {'status': 3, 'mode': 'Standard', 'stars': 6.0, 'acc_rating': 8.0, 'pass_rating': 4.0, 'tech_rating': 3.0,
             'predicted_acc': 0.95, 'notes': 1000, 'note_span': 120}
    with st.tx():
        for lid, extra in (('R1', rated), ('R2', rated), ('F1', {'status': 3})):
            st.upsert('leaderboards', {'leaderboard_id': lid, 'song_name': 'Song ' + lid, 'difficulty': 'Expert', **extra}, ['leaderboard_id'])
        for lid, acc, pp, ts in (('R1', 0.93, 250.0, t1), ('R2', 0.90, 210.0, t0), ('F1', 0.95, 99.0, t1)):
            st.upsert('scores', {'player_id': PID, 'leaderboard_id': lid, 'acc': acc, 'pp': pp, 'timeset': ts}, ['player_id', 'leaderboard_id'])
        for lid, acc, pp, ts in (('R1', 0.91, 180.0, t0), ('R1', 0.93, 200.0, t1), ('R2', 0.90, 150.0, t0), ('F1', 0.90, 77.0, t0)):
            st.conn.execute('INSERT INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) VALUES(?,?,?,?,?)', (lid, acc, pp, ts, ts))
    lb = dict(st.q("SELECT * FROM leaderboards WHERE leaderboard_id='R1'")[0])
    st.close()
    c = sqlite3.connect('file:%s?mode=ro' % (tmp_path / 'blnext.db').as_posix(), uri=True)
    c.row_factory = sqlite3.Row
    old = ppcalc.pp_from_row(0.91, lb)['pp']
    assert 0 < old and abs(old - 180.0) > 1
    assert play_report.pp_at(c, PID, t0 + 1) == pytest.approx({'R1': old, 'R2': 210.0, 'F1': 77.0})
    assert play_report.pp_at(c, PID, t1 + 1) == pytest.approx({'R1': 250.0, 'R2': 210.0, 'F1': 99.0})
    rows = play_report.others(c, PID, t0, t1 + 1, {'R2'})   # 期間の終わりの記録（R1 は今の自己ベスト＝ scores の PP）
    assert {h['leaderboard_id']: h['pp'] for h in rows} == {'R1': 250.0, 'F1': 77.0}
    c.close()
