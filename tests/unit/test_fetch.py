# 取得（core/fetch）: 差分更新・自己ベストの更新の検出・失敗のやり直し・429 の待ち。作り物の BeatLeader（fakebl.py）を使う
import pytest
from core.fetch import http as bhttp
from core.fetch.http import BLClient, NetError
from core.fetch.refresh import Refresher, pool_pages
from core.settings import Settings
from core.store.db import Store
from core import views
from core.estimate import clear
from fakebl import FakeBL


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    # 間隔の待ちを無くしてテストを速くする（待つべき秒数は sleeps に記録）
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)
    sleeps = []
    monkeypatch.setattr(BLClient, '_sleep', lambda self, s: sleeps.append(s))
    return sleeps


def make(tmp_path, fake, **settings):
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 5, 'pool_below': 5, 'target_country_rank': 3, **settings})
    return st, s, Refresher(st, s, lambda: BLClient('test/0', opener=fake))


def test_pool_pages():
    assert pool_pages(566, 100, 100, 500) == [5, 6, 7]
    assert pool_pages(566, 100, 100, 100) == [1, 5, 6, 7]
    assert pool_pages(30, 100, 100) == [1, 2]
    assert pool_pages(None, 100, 100) == []


def test_first_and_second_run(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['error'] is None and p['warnings'] == []
    assert st.q1('SELECT COUNT(*) AS n FROM leaderboards WHERE status=3')['n'] == 40    # 未ランクの難易度は入れない
    assert st.q1("SELECT upload_time FROM leaderboards WHERE leaderboard_id='LB001'")['upload_time'] == 1600001000
    assert st.q1('SELECT COUNT(*) AS n FROM scores WHERE player_id=?', (fake.self_id,))['n'] == 20
    pool = st.q1('SELECT COUNT(DISTINCT player_id) AS n FROM scores WHERE player_id<>?', (fake.self_id,))['n']
    assert pool == 10   # 上5人・下5人
    assert st.q1('SELECT COUNT(*) AS n FROM attempts')['n'] == 2

    # 2回目: 誰も変わっていなければ比較対象は取り直さない。ランク譜面の一覧も1日1回なので取らない
    fake.calls.clear()
    p = r.run_sync()
    assert p['skipped'] == 10
    paths = [c[0] for c in fake.calls]
    assert not any(x.endswith('/accgraph') and fake.self_id not in x for x in paths)
    assert '/maps' not in paths

    # 1人が新しいランクスコアを出したら、その人だけ取り直す
    fake.players[11]['lastRanked'] += 50     # 12位（自分の3つ下）
    fake.calls.clear()
    p = r.run_sync()
    got = [c[0] for c in fake.calls if c[0].endswith('/accgraph')]
    assert sorted(got) == sorted(['/player/%s/accgraph' % fake.self_id, '/player/9012/accgraph'])


def test_improvement_is_recorded(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    assert views.home(st, s)['improved'] == []          # 初回の取り込みは「伸ばした」に数えない
    fake.my_scores['LB003'] = {'acc': 0.99, 'time': 1695000000}
    r.run_sync()
    imp = views.home(st, s)['improved']
    assert len(imp) == 1 and imp[0]['leaderboard_id'] == 'LB003' and imp[0]['acc'] == 0.99
    assert st.q1("SELECT COUNT(*) AS n FROM score_history WHERE leaderboard_id='LB003'")['n'] == 2


def ranked_pps(st, pid):
    return {r['leaderboard_id']: r['pp'] for r in st.q('SELECT s.leaderboard_id, s.pp FROM scores s JOIN leaderboards l '
                                                       'USING(leaderboard_id) WHERE s.player_id=? AND l.status=3', (pid,))}


def test_improved_total_pp_gain(tmp_path):
    # 伸ばした曲の総PP（重み付きの合計）の増え: 曲ごと（その曲だけを前の並びに入れた差）と全部（前後の自己ベストの並びの総PP の差）。
    # 総PP の増えは曲の PP の増えを超えない。並びの下の方の曲は、曲の PP が増えても総PP の増えは小さい
    from core import review
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    before = ranked_pps(st, fake.self_id)
    low = min(before, key=before.get)                     # 自分の並びで一番下の曲
    fake.my_scores[low] = {'acc': fake.my_scores[low]['acc'] + 0.01, 'time': 1695000000}
    r.run_sync()
    h = views.home(st, s)
    (x,) = h['improved']
    raw = x['pp'] - x['old_pp']
    assert x['leaderboard_id'] == low and raw > 0
    assert x['old_pp'] == pytest.approx(before[low], abs=1e-6)
    assert 0 < x['gain'] < raw * 0.9                     # 下の方の曲なので重みの分だけ小さい
    assert h['improvedGain'] == pytest.approx(x['gain'])
    assert h['improvedGain'] == pytest.approx(review.total(ranked_pps(st, fake.self_id)) - review.total(before), abs=1e-6)

    # 2曲を同時に伸ばした時: 全部の増えは前後の並びの差で、曲ごとの増えはその曲だけを入れた差
    before = ranked_pps(st, fake.self_id)
    top = max(before, key=before.get)
    for lid in (low, top):
        fake.my_scores[lid] = {'acc': fake.my_scores[lid]['acc'] + 0.005, 'time': 1695000100}
    r.run_sync()
    h = views.home(st, s)
    by = {x['leaderboard_id']: x for x in h['improved']}
    assert set(by) == {low, top}
    assert by[top]['gain'] == pytest.approx(by[top]['pp'] - by[top]['old_pp'], abs=1e-6)   # 一番上の曲は重み 1
    assert h['improvedGain'] == pytest.approx(review.total(ranked_pps(st, fake.self_id)) - review.total(before), abs=1e-6)
    assert views.home(st, s)['improvedGain'] == h['improvedGain']


def test_changes_survive_refresh_without_changes(tmp_path):
    # 変化の無い取得を続けても、前の変化（伸ばした曲・抜いた人）は消えず、比べる時点は変化の前の取得のまま
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    first = views.home(st, s)['dataAt']
    assert views.home(st, s)['prevAt'] is None
    fake.my_scores['LB003'] = {'acc': 0.99, 'time': 1695000000}
    me, a = fake.players[14], fake.players[13]
    fake.players[13:15] = [me, a]
    me['pp'], a['pp'] = 9871, 9870
    r.run_sync()
    r.run_sync()                                         # 何も変わっていない取得
    h = views.home(st, s)
    assert h['prevAt'] == first and h['dataAt'] > first + 1
    assert [x['leaderboard_id'] for x in h['improved']] == ['LB003'] and h['improved'][0]['acc'] == 0.99
    assert [x['name'] for x in h['passed']] == [a['name']] and h['me']['prevCountryRank'] == 15

    # 次に変化があれば、比べるのはその前の取得（前の変化は出さない）
    before = h['dataAt']
    fake.my_scores['LB004'] = {'acc': 0.98, 'time': 1695000100}
    r.run_sync()
    h = views.home(st, s)
    assert h['prevAt'] == before
    assert [x['leaderboard_id'] for x in h['improved']] == ['LB004'] and h['passed'] == []


def test_changes_without_any_change(tmp_path):
    # どこまでさかのぼっても変化が無ければ、直前の取得と比べる（「ありません」）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    for _ in range(3):
        r.run_sync()
    runs = [x['run_at'] for x in st.q('SELECT DISTINCT run_at FROM player_snapshots ORDER BY run_at')]
    h = views.home(st, s)
    assert h['prevAt'] == runs[-2] and h['improved'] == [] and h['passed'] == [] and h['passedBy'] == []


def test_new_play_is_not_improved_but_counts_as_change(tmp_path):
    # 初めて遊んだ曲は「伸ばした曲」に出さないが、自分の PP が変わったので比べる時点はさかのぼらない
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    first = views.home(st, s)['dataAt']
    new = next(lb for lb, _, _ in fake.maps if lb not in fake.my_scores)
    fake.my_scores[new] = {'acc': 0.95, 'time': 1695000000}
    fake.players[14]['pp'] += 5
    r.run_sync()
    r.run_sync()
    h = views.home(st, s)
    assert h['prevAt'] == first and h['improved'] == []


def test_map_range(tmp_path):
    # 自分のランクのスコアの譜面の特徴の範囲（中央・75%点・95%点と最大）。NF のスコアは数えない。少なすぎる時は出さない
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    rg = clear.map_range(st, fake.self_id)
    mine = lambda col: sorted(x[col] for x in st.q('SELECT l.%s FROM scores s JOIN leaderboards l USING(leaderboard_id) '
                                                   'WHERE s.player_id=?' % col, (fake.self_id,)))
    passes, techs = mine('pass_rating'), mine('tech_rating')
    assert len(passes) == 20 and rg['pass'] == {'p50': passes[9], 'p75': passes[14], 'p95': passes[18], 'max': passes[-1]}
    assert rg['tech']['p50'] == techs[9]    # R5（未プレイの曲の tech による下げ）の基準
    assert rg['njs']['max'] == max(x['njs'] for x in st.q('SELECT njs FROM leaderboards WHERE status=3 AND leaderboard_id IN '
                                                          '(SELECT leaderboard_id FROM scores WHERE player_id=?)', (fake.self_id,)))
    with st.tx():
        st.conn.execute("UPDATE scores SET modifiers='NF,FS' WHERE player_id=? AND leaderboard_id='LB019'", (fake.self_id,))
    few = clear.map_range(st, fake.self_id)             # NF を除くと19曲（20曲より少ない）: 95%点の代わりに最大
    assert few['pass']['p95'] == few['pass']['max'] and few.keys() == rg.keys()
    with st.tx():
        st.conn.execute("UPDATE scores SET modifiers='NF,FS' WHERE player_id=? AND leaderboard_id NOT IN ('LB000','LB001','LB002','LB003')", (fake.self_id,))
    assert clear.map_range(st, fake.self_id) == {}      # NF を除くと4曲（5曲より少ない）


def test_passed_and_passed_by(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    me = fake.players[14]
    # 自分が2人を抜き、1人に抜かれる（並びを入れ替えて pp もそれらしく）
    a, b, c = fake.players[12], fake.players[13], fake.players[15]
    fake.players[12:16] = [me, a, c, b]
    fake.players[12]['pp'], fake.players[13]['pp'], fake.players[14]['pp'], fake.players[15]['pp'] = 9871, 9870, 9869, 9868
    r.run_sync()
    h = views.home(st, s)
    assert h['me']['countryRank'] == 13
    assert {x['name'] for x in h['passed']} == {a['name'], b['name']}
    assert h['passedBy'] == []


def test_home_target_and_neighbors(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, neighbor_display=2)
    r.run_sync()
    h = views.home(st, s)
    assert h['hasData'] and h['me']['countryRank'] == 15
    assert h['target']['rank'] == 3 and abs(h['target']['gap'] - 120) < 1e-6
    assert [n['countryRank'] for n in h['neighbors']] == [13, 14, 15, 16, 17]


def test_home_since_last_export(tmp_path):
    # 上下の人とタイルの「前回の出力から」: 一番新しい出力（どのセットでも）の時刻以前で一番新しい取得の回の PP・順位と、今の差。
    # 出力が無い時は出さない（None）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, neighbor_display=2)
    r.run_sync()
    h = views.home(st, s)
    assert h['sinceExport'] is None and h['me']['exportPp'] is None
    assert all(n['rankMove'] is None and n['ppMove'] is None for n in h['neighbors'])
    first = h['dataAt']
    export = lambda t: st.conn.execute("INSERT INTO exports(run_id, exported_at, path, method) VALUES(1, ?, 'x', 'write')", (t,))
    with st.tx():
        export(first)                                    # 1回目の取得の直後に出力（別のセットの古い出力も混ぜる）
        export(first - 5000)
    me, a = fake.players[14], fake.players[13]           # 自分（15位・9850PP）が 14位（9860PP）の人を抜く
    fake.players[13:15] = [me, a]
    me['pp'], a['pp'] = 9871, 9870
    r.run_sync()
    h = views.home(st, s)
    assert h['sinceExport'] == {'exportAt': first, 'at': first}
    assert (h['me']['exportCountryRank'], h['me']['exportPp'], h['me']['exportRank']) == (15, 9850, 150)
    assert h['me']['countryRank'] == 14
    by = {n['id']: n for n in h['neighbors']}
    assert by[me['id']]['self'] and by[me['id']]['rankMove'] == 1 and by[me['id']]['ppMove'] == pytest.approx(21)
    assert by[a['id']]['rankMove'] == -1 and by[a['id']]['ppMove'] == pytest.approx(10)
    assert all(n['rankMove'] == 0 and n['ppMove'] == 0 for n in h['neighbors'] if n['id'] not in (me['id'], a['id']))
    # 取得の後にもう一度出力した（まだ遊んでいない）: 比べる元は今の取得の回＝差は全部 0
    with st.tx():
        export(h['dataAt'] + 10)
    h = views.home(st, s)
    assert h['sinceExport']['at'] == h['dataAt'] and all(n['rankMove'] == 0 and n['ppMove'] == 0 for n in h['neighbors'])


def test_player_not_found(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    s.update({'player_id': '999'})
    assert r.run_sync()['error'] == 'player-not-found'


def test_offline_keeps_old_data(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.fail['/player/%s' % fake.self_id] = 99
    p = r.run_sync()
    assert p['error'] == 'offline'
    assert views.home(st, s)['hasData']


def test_retry_then_success(no_wait):
    fake = FakeBL()
    fake.fail['/player/9015'] = 2
    c = BLClient('t', opener=fake)
    assert c.get('/player/9015')['id'] == '9015'
    assert no_wait.count(1) == 1 and no_wait.count(2) == 1   # 1秒・2秒待ってやり直した


def test_retry_gives_up():
    fake = FakeBL()
    fake.fail['/player/9015'] = 99
    with pytest.raises(NetError) as e:
        BLClient('t', opener=fake).get('/player/9015')
    assert e.value.code == 'offline'
    assert len(fake.calls) == 1 + bhttp.RETRIES


def test_429_waits_and_gives_up(no_wait):
    fake = FakeBL()
    fake.status_for['/player/9015'] = 429
    with pytest.raises(NetError) as e:
        BLClient('t', opener=fake).get('/player/9015')
    assert e.value.code == 'http-429'
    assert len(fake.calls) == 1 + bhttp.RETRY_429
    assert 10.0 in no_wait   # 区切りの時刻が分からない時は10秒待つ


def test_4xx_is_not_retried():
    fake = FakeBL()
    with pytest.raises(NetError) as e:
        BLClient('t', opener=fake).get('/nope')
    assert e.value.code == 'http-404' and len(fake.calls) == 1


def test_user_agent_is_sent():
    seen = []
    fake = FakeBL()

    def opener(req, timeout):
        seen.append(req.get_header('User-agent'))
        return fake(req, timeout)
    BLClient('blnext/9.9', opener=opener).get('/player/9015')
    assert seen == ['blnext/9.9']


def test_cancel(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    c = BLClient('t', opener=fake)
    c.cancel_event.set()
    r.client_factory = lambda: c
    p = r.run_sync()
    assert p['cancelled'] and p['error'] is None
