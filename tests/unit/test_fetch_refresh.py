# 取得の流れ（core/fetch/refresh.py）: 途中の失敗・中止で取得済みの分が残ること、差分更新の分岐、進み具合。
# 作り物の BeatLeader（fakebl.py）の前に hook を置き、決めた要求だけ別の応答・失敗にする
import io, threading, time, urllib.error
from urllib.parse import urlsplit, parse_qs
import pytest
from core.fetch import beatleader as bl, http as bhttp, refresh
from core.fetch.http import BLClient, Cancelled
from core.fetch.refresh import Refresher
from core.estimate import model as M, recommend as R
from core.settings import Settings
from core.store.db import Store
from core import views
from fakebl import FakeBL, Resp

T0 = 1_760_000_000   # 作り物の「今」（UTC の昼ごろ。前後1分は同じ日）


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    # 待たない。中止されていれば本物の _sleep と同じく Cancelled
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)

    def _sleep(self, s):
        if self.cancel_event.is_set():
            raise Cancelled()
    monkeypatch.setattr(BLClient, '_sleep', _sleep)


@pytest.fixture
def at(monkeypatch):
    """取得の時刻を決める。at(T) の後の取得は run_at=T になる（最初は T0）"""
    t = [T0]
    monkeypatch.setattr(refresh, 'now', lambda: t[0])

    def set_(v):
        t[0] = v
    return set_


def make(tmp_path, fake, hook=None, **settings):
    """hook(path, query) が None 以外（応答か例外）を返したら、FakeBL の代わりにそれを使う"""
    st = Store(str(tmp_path / 'db.sqlite'))
    s = Settings(str(tmp_path / 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 5, 'pool_below': 5, 'target_country_rank': 3, **settings})

    def opener(req, timeout):
        if hook:
            u = urlsplit(req.full_url)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            a = hook(u.path, q)
            if a is not None:
                fake.calls.append((u.path, q))
                if isinstance(a, BaseException):
                    raise a
                return a
        return fake(req, timeout)
    return st, s, Refresher(st, s, lambda: BLClient('test/0', opener=opener))


def http_error(code):
    return urllib.error.HTTPError('http://x', code, 'x', {}, io.BytesIO(b''))


def n(st, sql, args=()):
    return st.q1(sql, args)['n']


def pool_with_scores(st, fake):
    return {r['player_id'] for r in st.q('SELECT DISTINCT player_id FROM scores WHERE player_id<>?', (fake.self_id,))}


POOL = {'9%03d' % k for k in range(10, 21) if k != 15}   # 自分（15位）の上5人・下5人


# ---- 始める前・自分のプロフィール ----

def test_no_player_does_nothing(tmp_path):
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, player_id='')
    p = r.run_sync()
    assert p['error'] == 'no-player' and fake.calls == [] and not p['running']


def test_broken_profile_is_internal_error(tmp_path):
    # 自分のプロフィールが想定外の形（id が無い）でもアプリは止めず error=internal。前回までのデータは残る
    fake = FakeBL()
    broken = [False]
    st, s, r = make(tmp_path, fake, lambda p, q: Resp({'name': '?'}) if broken[0] and p == '/player/' + fake.self_id else None)
    r.run_sync()
    broken[0] = True
    p = r.run_sync()
    assert p['error'] == 'internal' and p['warnings'][0]['code'] == 'internal' and 'ValueError' in p['warnings'][0]['detail']
    assert not p['running'] and p['finishedAt']
    assert views.home(st, s)['hasData']


def test_runs_in_same_second_get_distinct_run_at(tmp_path, at):
    # 同じ秒に2回取得しても前の回を上書きしない（run_at を前回＋1にする）。「前回からの変化」は前の回と比べる
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    r.run_sync()
    runs = [x['run_at'] for x in st.q('SELECT run_at FROM player_snapshots WHERE player_id=? ORDER BY run_at', (fake.self_id,))]
    assert runs == [T0, T0 + 1] and st.meta('prev_run_at') == str(T0)


def test_country_from_profile_wins(tmp_path):
    # 設定の国と BeatLeader の国が違えば警告し、ランキングは BeatLeader の国で取る
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, country='us')
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'country-mismatch', 'detail': 'jp'}]
    assert {q['countries'] for path, q in fake.calls if path == '/players'} == {'jp'}


def test_player_without_country_rank(tmp_path):
    # 国内順位の無い（ランク外の）人: ランキング・比較対象は取らず、自分の分だけ取って止まらない
    fake = FakeBL()

    def hook(p, q):
        if p == '/player/' + fake.self_id:
            return Resp({**fake._player(fake.players[14], 15), 'countryRank': None})
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    assert p['error'] is None and p['warnings'] == []
    paths = [c[0] for c in fake.calls]
    assert '/players' not in paths
    assert [x for x in paths if x.endswith('/accgraph')] == ['/player/%s/accgraph' % fake.self_id]
    assert n(st, 'SELECT COUNT(*) AS n FROM scores WHERE player_id=?', (fake.self_id,)) == 20


# ---- 国内ランキング ----

def test_ranking_page_failure_keeps_others(tmp_path):
    # 国内ランキングの1ページが取れなくても他のページで続ける。取れなかったページの人の前回のスコアは消さない
    fake = FakeBL(n_players=120, self_rank=100)
    down = [False]
    st, s, r = make(tmp_path, fake, lambda p, q: http_error(503) if down[0] and p == '/players' and q['page'] == '2' else None,
                    pool_above=10, pool_below=10)
    r.run_sync()
    assert len(pool_with_scores(st, fake)) == 20
    down[0] = True
    fake.calls.clear()
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'ranking-page', 'detail': '2 http-503'}] and p['error'] is None
    assert st.meta('last_run_at')                                   # 最後まで進んだ
    assert len(pool_with_scores(st, fake)) == 20                    # 101〜110位の人のスコアも残る
    assert p['skipped'] == 10                                       # 比較対象は取れたページの10人（変わっていないので取り直さない）


def test_ranking_all_failed_keeps_previous_view(tmp_path):
    # ランキングが1ページも取れなかった回は ranking_at を進めず、前回の目標順位の人・周りの人をそのまま見せる（自分は今回の値で入る）
    fake = FakeBL()
    down = [False]
    st, s, r = make(tmp_path, fake, lambda p, q: http_error(503) if down[0] and p == '/players' else None, neighbor_display=2)
    r.run_sync()
    assert views.home(st, s)['target'].get('name')
    down[0] = True
    p = r.run_sync()
    assert p['warnings'][0]['code'] == 'ranking-page'
    h = views.home(st, s)
    assert h['target'].get('name')                                  # 目標順位の人が見えている
    assert len([x for x in h['neighbors'] if not x['self']]) == 4   # 上下2人ずつが見えている
    assert len([x for x in h['neighbors'] if x['self']]) == 1       # 自分も1人だけ入っている


# ---- ランク譜面の一覧 ----

def test_maps_partial_failure_does_not_unrank(tmp_path, monkeypatch, at):
    # 途中のページで失敗したら入れ替えない（一覧に無い＝ランク外、と決めつけない）。全部取れた時だけ入れ替える
    monkeypatch.setattr(bl, 'MAPS_PAGE', 15)
    monkeypatch.setattr(refresh, 'MAPS_INTERVAL', 0)    # 毎回確かめる
    fake = FakeBL()
    fake.publish_song_details(T0, 'e1')
    down = [False]
    st, s, r = make(tmp_path, fake, lambda p, q: http_error(503) if down[0] and p == '/maps' and q['page'] == '2' else None)
    r.run_sync()
    assert [q['page'] for p, q in fake.calls if p == '/maps'] == ['1', '2', '3']

    def status():
        return st.q1("SELECT status FROM leaderboards WHERE leaderboard_id='LB030'")['status']
    assert status() == 3
    fake.maps = [m for m in fake.maps if m[0] != 'LB030']   # LB030 がランクから外れた
    fake.publish_song_details(T0 + 50, 'e2')
    down[0] = True
    at(T0 + 100)
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'maps', 'detail': 'http-503'}] and p['error'] is None
    assert status() == 3 and st.meta('maps_fetched_at') == str(T0)
    down[0] = False
    at(T0 + 200)
    r.run_sync()
    assert status() is None and st.meta('maps_fetched_at') == str(T0 + 200)
    assert n(st, 'SELECT COUNT(*) AS n FROM leaderboards WHERE status=3') == 39


# ---- 自分のスコア ----

def test_self_scores_second_run_stops_at_old_tries(tmp_path, at):
    # 初回は投稿日の新しい順に全ページ。2回目からは最近挑戦した順に、前回より前の挑戦が出たページで止める
    fake = FakeBL(n_maps=250)
    fake.my_scores = {lb: {'acc': 0.9, 'time': 1690000000 + i} for i, (lb, _, _) in enumerate(fake.maps)}
    st, s, r = make(tmp_path, fake)

    def pages():
        return [(q['page'], q['sortBy']) for p, q in fake.calls if p.endswith('/scores')]
    r.run_sync()
    assert pages() == [('1', 'date'), ('2', 'date'), ('3', 'date')]
    assert n(st, 'SELECT COUNT(*) AS n FROM scores WHERE player_id=?', (fake.self_id,)) == 250
    for lb, _, _ in fake.maps[:150]:                 # 前回の取得の後に150曲を遊んで伸ばした
        fake.my_scores[lb] = {'acc': 0.95, 'time': T0 + 100}
    fake.calls.clear()
    at(T0 + 86400)
    r.run_sync()
    assert pages() == [('1', 'lastTryTime'), ('2', 'lastTryTime')]   # 3ページ目（古い挑戦だけ）は取らない
    assert len(views.home(st, s)['improved']) == 150


def test_self_scores_failure_stops_but_keeps_earlier_stages(tmp_path):
    # 自分のスコアが取れなければ error で止める。それまでに取ったランキング・譜面は残り、次の取得で全部そろう
    fake = FakeBL()
    sp = '/player/%s/scores' % fake.self_id
    fake.status_for[sp] = 502
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['error'] == 'http-502' and not p['cancelled'] and p['phase'] == 'selfScores'
    assert n(st, 'SELECT COUNT(*) AS n FROM players') == 30
    assert n(st, 'SELECT COUNT(*) AS n FROM leaderboards WHERE status=3') == 40
    assert st.meta('ranking_at') and st.meta('last_run_at') is None and st.meta('self_scores_synced_at') is None
    del fake.status_for[sp]
    p = r.run_sync()
    assert p['error'] is None and pool_with_scores(st, fake) == POOL and st.meta('last_run_at')


def test_self_accgraph_failure_warns_and_continues(tmp_path):
    # 自分の accgraph（★の補正）が取れなくても警告にして続ける（比較対象まで取る）
    fake = FakeBL()
    fake.status_for['/player/%s/accgraph' % fake.self_id] = 500
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'self-accgraph', 'detail': 'http-500'}] and p['error'] is None
    assert n(st, 'SELECT COUNT(*) AS n FROM scores WHERE player_id=?', (fake.self_id,)) == 20
    assert pool_with_scores(st, fake) == POOL and st.meta('last_run_at')


def test_self_accgraph_failure_keeps_previous_stars(tmp_path):
    # 自分の accgraph が失敗した回も、取り直した自分のスコアの★は前回の値を残す（★の無いスコアは推定の曲線に使われない）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    q = 'SELECT COUNT(*) AS n FROM scores WHERE player_id=? AND stars IS NOT NULL'
    assert n(st, q, (fake.self_id,)) == 20
    fake.status_for['/player/%s/accgraph' % fake.self_id] = 500
    r.run_sync()
    assert n(st, q, (fake.self_id,)) == 20


# ---- アテンプト ----

def _private_run(tmp_path, fake=None):
    """統計を非公開にしている人（プロフィールの印で分かる）の取得の準備"""
    fake = fake or FakeBL()
    next(p for p in fake.players if p['id'] == fake.self_id)['statsPublic'] = False
    st = Store(str(tmp_path / 'p.db'))
    s = Settings(str(tmp_path / 'c'))
    s.update({'player_id': fake.self_id})
    return fake, st, Refresher(st, s, lambda: BLClient('test', opener=fake))


def _self_calls(fake, suffix):
    return [q for path, q in fake.calls if path == '/player/%s/%s' % (fake.self_id, suffix)]


def test_private_player_skips_attempts_without_401(tmp_path):
    # プロフィールの印で非公開と分かれば 401 を待たない（通信を増やさない・ログに失敗を残さない）
    fake, st, ref = _private_run(tmp_path)
    p = ref.run_sync()
    assert p['error'] is None and p['warnings'] == []
    assert _self_calls(fake, 'scoresstats') == []
    assert st.meta('stats_public') == '0' and st.meta('attempts_private') == '1'


def test_private_player_second_fetch_is_by_date(tmp_path):
    # 非公開の人は lastTryTime が 0 で返るので、2回目も投稿日時の順で取る（最近挑戦した順の止まる条件だと、100譜面より多く遊ぶと取りこぼす）
    fake, st, ref = _private_run(tmp_path)
    ref.run_sync()
    fake.calls.clear()
    ref.run_sync()
    assert [q['sortBy'] for q in _self_calls(fake, 'scores')] == ['date']


def test_turning_stats_public_refetches_at_once(tmp_path):
    # 公開に変えたら、7日待たずに挑戦の記録を取り、プレイ回数を埋めるため自分のスコアも全部取り直す
    fake, st, ref = _private_run(tmp_path)
    ref.run_sync()
    next(p for p in fake.players if p['id'] == fake.self_id)['statsPublic'] = True
    fake.calls.clear()
    p = ref.run_sync()
    assert p['error'] is None and st.meta('stats_public') == '1' and st.meta('attempts_private') == '0'
    assert _self_calls(fake, 'scoresstats')
    assert [q['sortBy'] for q in _self_calls(fake, 'scores')][0] == 'date'    # 全部取り直し（新しい順）
    fake.calls.clear()
    ref.run_sync()
    assert [q['sortBy'] for q in _self_calls(fake, 'scores')][0] == 'lastTryTime'   # 次からはいつもの取り方


def test_attempts_private_is_rechecked_weekly(tmp_path, at):
    # 統計が非公開（403）なら警告にせず続け、1週間は確かめ直さない。1週間たったら確かめ直す
    fake = FakeBL()
    ap = '/player/%s/scoresstats' % fake.self_id
    fake.status_for[ap] = 403
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['error'] is None and p['warnings'] == [] and st.meta('attempts_private') == '1'
    del fake.status_for[ap]                     # 公開に変えた
    fake.calls.clear()
    at(T0 + 86400)
    r.run_sync()
    assert ap not in [c[0] for c in fake.calls]
    at(T0 + 8 * 86400)
    r.run_sync()
    assert st.meta('attempts_private') == '0' and n(st, 'SELECT COUNT(*) AS n FROM attempts') == 2


def test_attempts_resume_from_last_success(tmp_path, monkeypatch, at):
    # 2回目からは「前回の時刻−1時間」以降を取る。途中のページで失敗したら時刻を進めず、次回も前回成功した時刻から取り直す
    monkeypatch.setattr(bl, 'ATTEMPTS_PAGE', 2)
    fake = FakeBL()
    ap = '/player/%s/scoresstats' % fake.self_id
    rows = [{'leaderboardId': 'LB00%d' % i, 'accuracy': 0.9, 'pp': 90, 'endType': 1, 'modifiers': '', 'timeset': 1690000000 + i}
            for i in range(3)]
    down = [False]

    def hook(p, q):
        if p != ap:
            return None
        page = int(q['page'])
        if down[0] and page == 2:
            return http_error(500)
        return Resp({'metadata': {'total': 3}, 'data': rows[(page - 1) * 2: page * 2]})
    st, s, r = make(tmp_path, fake, hook)

    def froms():
        return [(q['page'], q.get('time_from')) for p, q in fake.calls if p == ap]
    r.run_sync()
    # 初めて取る時は直近1年分だけ
    assert froms() == [('1', str(T0 - 365 * 86400)), ('2', str(T0 - 365 * 86400))] and n(st, 'SELECT COUNT(*) AS n FROM attempts') == 3
    down[0] = True
    fake.calls.clear()
    at(T0 + 86400)
    p = r.run_sync()
    assert {'code': 'attempts', 'detail': 'http-500'} in p['warnings']
    assert st.meta('attempts_synced_at') == str(T0) and st.meta('attempts_private') == '0'
    down[0] = False
    fake.calls.clear()
    at(T0 + 2 * 86400)
    r.run_sync()
    assert froms() == [('1', str(T0 - 3600)), ('2', str(T0 - 3600))]
    assert st.meta('attempts_synced_at') == str(T0 + 2 * 86400)
    fake.calls.clear()                          # 1年より長く開かなかった時も、直近1年分だけ
    later = T0 + 500 * 86400
    at(later)
    r.run_sync()
    assert froms()[0] == ('1', str(later - 365 * 86400))


# ---- 自分の履歴 ----

def test_history_today_keeps_run_value(tmp_path, at):
    # 今日の分は起動時に取った値（source='run'）のほうが新しいので、API の値で上書きしない
    fake = FakeBL()
    hist = [{'timestamp': T0 - 86400, 'pp': 1.0, 'rank': 1, 'countryRank': 1},
            {'timestamp': T0 - 60, 'pp': 2.0, 'rank': 2, 'countryRank': 2}]
    st, s, r = make(tmp_path, fake, lambda p, q: Resp(hist) if p.endswith('/history/compact') else None)
    r.run_sync()
    rows = {x['date']: x for x in st.q('SELECT * FROM self_history')}
    today, yesterday = refresh._day(T0), refresh._day(T0 - 86400)
    assert rows[today]['source'] == 'run' and rows[today]['pp'] == 9850
    assert rows[yesterday]['source'] == 'api' and rows[yesterday]['pp'] == 1.0


def test_history_failure_and_count(tmp_path):
    # 失敗は警告にして続ける。API の履歴がまだ無ければ次も遡れるだけ（1000件）、あれば最近の分（14件）だけ取る
    fake = FakeBL()
    hp = '/player/%s/history/compact' % fake.self_id
    fake.status_for[hp] = 500
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'history', 'detail': 'http-500'}] and p['error'] is None
    assert pool_with_scores(st, fake) == POOL and st.meta('last_run_at')

    def counts():
        return [q['count'] for x, q in fake.calls if x == hp]
    del fake.status_for[hp]
    fake.calls.clear()
    r.run_sync()
    assert counts() == ['1000']
    fake.calls.clear()
    r.run_sync()
    assert counts() == ['14']


# ---- 比較対象 ----

def test_pool_player_failure_is_retried_next_time(tmp_path):
    # 比較対象の1人が取れなくても他の人は保存し、次回はその人だけ取り直す
    fake = FakeBL()
    bad = '/player/9012/accgraph'
    fake.fail[bad] = 99
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['warnings'] == [{'code': 'pool', 'detail': 'offline'}] and p['error'] is None
    assert p['done'] == p['total'] == 10            # 失敗した人も「済み」に数える（進み具合が止まらない）
    assert pool_with_scores(st, fake) == POOL - {'9012'}
    fake.fail.clear()
    fake.calls.clear()
    p = r.run_sync()
    assert [c[0] for c in fake.calls if c[0].endswith('/accgraph') and fake.self_id not in c[0]] == [bad]
    assert p['skipped'] == 9 and pool_with_scores(st, fake) == POOL


# ---- ランクのスコアが min_scores より少ない比較対象は取らない ----

def pool_calls(fake):
    """取得で accgraph を取った比較対象の人"""
    return {p.split('/')[2] for p, _ in fake.calls if p.endswith('/accgraph')} - {fake.self_id}


def ranked(fake, **counts):
    """順位表のランクのスコアの数を決める（ranked(fake, p9012=29)）。テストで増やす時に lastRanked も変える"""
    for k, v in counts.items():
        next(p for p in fake.players if p['id'] == k[1:])['rankedPlayCount'] = v


def test_pool_skips_few_ranked_scores(tmp_path):
    # min_scores（初期値 30）より少ない人は取らない。ちょうどの人と、分からない（None）人は取る。進み具合の分母は取る人だけ
    fake = FakeBL()
    ranked(fake, p9012=29, p9013=0, p9014=30, p9016=None)
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['error'] is None and p['warnings'] == [] and st.meta('last_run_at')
    assert pool_calls(fake) == POOL - {'9012', '9013'} == pool_with_scores(st, fake)
    assert p['done'] == p['total'] == 8 and p['skipped'] == 2
    fake.calls.clear()
    p = r.run_sync()                                # 2回目: 誰も変わっていないので、誰も取らない
    assert pool_calls(fake) == set() and p['total'] == 0 and p['skipped'] == 10


def test_pool_player_reaching_min_scores_is_fetched_next_time(tmp_path):
    # スコアが増えて届いた人は次の取得で取る。取った後はいつもの差分（変わった時だけ）
    fake = FakeBL()
    ranked(fake, p9012=28)
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.players[11]['lastRanked'] += 50            # 1曲増えたがまだ足りない: 取らない
    ranked(fake, p9012=29)
    fake.calls.clear()
    assert r.run_sync()['total'] == 0 and pool_calls(fake) == set()
    fake.players[11]['lastRanked'] += 50
    ranked(fake, p9012=30)
    fake.calls.clear()
    p = r.run_sync()
    assert pool_calls(fake) == {'9012'} and p['total'] == 1 and pool_with_scores(st, fake) == POOL
    fake.calls.clear()
    r.run_sync()
    assert pool_calls(fake) == set()


def test_lowering_min_scores_fetches_on_next_run(tmp_path):
    # min_scores を下げたら、順位表の数が新しい値に届く人を次の取得で取る（その人のスコアは変わっていなくても）
    fake = FakeBL()
    ranked(fake, p9012=25, p9013=15)
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    assert pool_with_scores(st, fake) == POOL - {'9012', '9013'}
    s.update({'min_scores': 20})
    fake.calls.clear()
    r.run_sync()
    assert pool_calls(fake) == {'9012'} and pool_with_scores(st, fake) == POOL - {'9013'}


def test_min_scores_of_any_preset_keeps_player(tmp_path):
    # min_scores はセットごと: どれか1つのセットが使う人は取る。使う人の数はセットごと（開いているセットの値で決めない）
    fake = FakeBL()
    ranked(fake, p9012=25, p9013=15)
    st, s, r = make(tmp_path, fake, min_scores=5)   # rows（10＋順位）は全員 5 以上
    pid = s.add_preset('もう1つ')
    s.update({'min_scores': 20}, preset=pid)
    s.update({'active_preset': pid})
    assert s.values()['min_scores'] == 20
    r.run_sync()
    assert pool_calls(fake) == POOL == pool_with_scores(st, fake)
    low, high = M.build(st, s.view('p1'), T0), M.build(st, s.view(pid), T0)
    assert low.pool_size == high.pool_size == 10
    assert (low.skipped_few_scores, len(low.pool)) == (0, 10)
    assert high.skipped_few_scores + len(high.pool) == 10


def test_fetched_player_below_min_scores_is_not_refetched(tmp_path):
    # 前に取った人のスコアが変わって足りなくなった（譜面がランクから外れた等）: 取り直しても外されるので取らず、古いスコアも使わない。また届いたら取る
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, min_scores=10)
    r.run_sync()
    assert any(p.player_id == '9012' for p in M.build(st, s, T0).pool)
    fake.players[11]['lastRanked'] += 50
    ranked(fake, p9012=9)
    fake.calls.clear()
    assert r.run_sync()['total'] == 0 and pool_calls(fake) == set()
    m = M.build(st, s, T0)
    assert not any(p.player_id == '9012' for p in m.pool) and m.pool_size == 10 and m.skipped_few_scores == 1
    fake.players[11]['lastRanked'] += 50
    ranked(fake, p9012=10)
    fake.calls.clear()
    r.run_sync()
    assert pool_calls(fake) == {'9012'} and any(p.player_id == '9012' for p in M.build(st, s, T0).pool)


def test_refetch_all_skips_few_ranked_scores(tmp_path, at):
    # 全部の取り直しでも、足りない人は取らない。その人の古いスコアは使わない（scores_fetched_at が空のまま）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake, min_scores=10)
    r.run_sync()
    ranked(fake, p9012=9)
    st.set_meta('pp_formula', 'old')
    fake.calls.clear()
    at(T0 + 3600)
    r.run_sync()
    assert own_calls(fake)[2] == POOL - {'9012'}
    assert st.q1("SELECT scores_fetched_at FROM players WHERE player_id='9012'")['scores_fetched_at'] is None


def test_skipped_players_still_on_home(tmp_path):
    # ホームの順位・上下の人・抜いた人は順位表から来るので、スコアを取らなかった人も出る
    fake = FakeBL()
    ranked(fake, **{'p%s' % p['id']: 1 for p in fake.players if p['id'] != fake.self_id})
    st, s, r = make(tmp_path, fake, neighbor_display=2)
    p = r.run_sync()
    assert p['total'] == 0 and pool_calls(fake) == set()
    h = views.home(st, s)
    assert h['target'].get('name') == 'Player 3'
    assert [x['name'] for x in h['neighbors']] == ['Player 13', 'Player 14', 'Player 15', 'Player 16', 'Player 17']
    me, a = fake.players[14], fake.players[13]      # 自分が1人抜く
    fake.players[13:15] = [me, a]
    me['pp'], a['pp'] = a['pp'] + 1, me['pp']
    r.run_sync()
    h = views.home(st, s)
    assert h['me']['countryRank'] == 14 and [x['name'] for x in h['passed']] == [a['name']]


def test_skipping_few_scores_keeps_estimate(tmp_path):
    # 順位表の数が実際の行の数と同じなら（作者とほかの人の控えの 454人で 451人が同じ・3人は順位表が1曲多い。少ない人はいない）、
    # 取らなくても曲線・似ている人・推定・数え方は全部取った時と同じ
    def run(name, accurate):
        fake = FakeBL(n_players=40, self_rank=20, n_maps=80)
        if accurate:
            for k, p in enumerate(fake.players, 1):
                p['rankedPlayCount'] = 10 + k           # fakebl の accgraph の行の数
        (tmp_path / name).mkdir()
        st, s, ref = make(tmp_path / name, fake, pool_above=10, pool_below=10, min_scores=25, min_overlap=5, max_map_age_years=0)
        assert ref.run_sync()['warnings'] == []
        m = M.build(st, s, T0)
        return pool_calls(fake), m, R.estimate(st, m, s, T0)
    (got_all, m_all, e_all), (got, m, e) = run('all', False), run('skip', True)
    assert len(got_all) == 20 and got == got_all - {'9010', '9011', '9012', '9013', '9014'}      # 20〜24 行の5人は取らない
    assert (m.pool_size, m.skipped_few_scores, len(m.pool)) == (m_all.pool_size, m_all.skipped_few_scores, len(m_all.pool)) == (20, 5, 15)
    assert m.similar and [(p.player_id, p.sim) for p in m.similar] == [(p.player_id, p.sim) for p in m_all.similar]
    assert m.pop_slope == m_all.pop_slope
    assert e and [(c['leaderboard_id'], c['est_acc'], c['gain'], c['confidence'], c['score']) for c in e] == \
        [(c['leaderboard_id'], c['est_acc'], c['gain'], c['confidence'], c['score']) for c in e_all]


# ---- PP の式・★が変わった時の全部の取り直し・式のずれの見張り ----

def own_calls(fake):
    """取得で呼んだ口: 譜面の一覧の回数・自分のスコアの並べ方・比較対象の accgraph の人"""
    return (sum(1 for p, _ in fake.calls if p == '/maps'),
            [q['sortBy'] for p, q in fake.calls if p.endswith('/scores')],
            {p.split('/')[2] for p, _ in fake.calls if p.endswith('/accgraph')} - {fake.self_id})


def test_formula_version_change_refetches_everything(tmp_path, at):
    # アプリの PP の式の版（ppcalc.UPSTREAM_COMMIT）が前回の取得と違えば、譜面・自分・比較対象を全部取り直す（知らせは出さない）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    assert st.meta('pp_formula') == refresh.ppcalc.UPSTREAM_COMMIT
    fake.calls.clear()
    at(T0 + 3600)
    r.run_sync()
    assert own_calls(fake) == (0, ['lastTryTime'], set())   # ふだん: 譜面は1日1回・自分は差分・比較対象は変わった人だけ
    st.set_meta('pp_formula', 'old')
    fake.calls.clear()
    at(T0 + 7200)
    p = r.run_sync()
    assert own_calls(fake) == (1, ['date'], POOL) and p['notices'] == []
    assert st.meta('pp_formula') == refresh.ppcalc.UPSTREAM_COMMIT


def test_reweight_is_detected_and_refetches_everything(tmp_path, at):
    # BeatLeader 側で★・PP が計算し直されたら（自己ベストは同じ）、知らせて、この回で譜面と自分のスコアを取り直し、比較対象も全員取り直す
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.maps = [(lb, stars * 0.96, up) for lb, stars, up in fake.maps]
    fake.calls.clear()
    at(T0 + 3600)
    p = r.run_sync()
    assert p['notices'] == [{'code': 'reweight', 'detail': '20'}]
    assert own_calls(fake) == (1, ['lastTryTime', 'date'], POOL)
    lb, stars, _ = fake.maps[0]
    row = st.q1('SELECT s.stars, s.pp, l.stars AS lstars FROM scores s JOIN leaderboards l USING(leaderboard_id) '
                'WHERE s.player_id=? AND s.leaderboard_id=?', (fake.self_id, lb))
    assert row['stars'] == pytest.approx(stars) and row['lstars'] == pytest.approx(stars)
    assert row['pp'] == pytest.approx(fake.pp_of(lb, fake.my_scores[lb]['acc']))
    other = st.q1("SELECT stars FROM scores WHERE player_id='9010' AND leaderboard_id=?", (lb,))
    assert other['stars'] == pytest.approx(stars)   # 比較対象も新しい★
    fake.calls.clear()
    at(T0 + 7200)
    p = r.run_sync()   # 次の回はふだんどおり
    assert p['notices'] == [] and own_calls(fake) == (0, ['lastTryTime'], set())


def test_few_changed_maps_are_not_a_reweight(tmp_path, at):
    # 譜面1つずつの評価のやり直し（数曲）では全部は取り直さない
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.maps = [(lb, stars + (0.5 if i < 3 else 0), up) for i, (lb, stars, up) in enumerate(fake.maps)]
    fake.calls.clear()
    at(T0 + 3600)
    p = r.run_sync()
    assert p['notices'] == [] and own_calls(fake) == (0, ['lastTryTime'], set())


def test_pp_formula_mismatch_notice(tmp_path):
    # BeatLeader の PP がアプリの式と合わなければ知らせる（上流の式が変わった＝ppcalc.py を直すまで推定の PP がずれる）
    fake = FakeBL()
    fake.pp_scale = 1.1
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert p['notices'] == [{'code': 'pp-formula', 'detail': '20/20'}] and p['error'] is None and p['warnings'] == []
    fake.pp_scale = 1.0   # BeatLeader の PP が全部変わった＝リウェイトとして取り直し、式は合うので pp-formula は出ない
    assert [x['code'] for x in r.run_sync()['notices']] == ['reweight']


def test_refetch_all_is_resumed_after_failure(tmp_path, at):
    # 全部の取り直しの途中で取れなかった比較対象は、次の回に取る。それまでは古い★のまま曲線に使わない（scores_fetched_at が空）
    fake = FakeBL()
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    st.set_meta('pp_formula', 'old')
    fake.fail['/player/9012/accgraph'] = 99
    at(T0 + 3600)
    r.run_sync()
    assert st.q1("SELECT scores_fetched_at FROM players WHERE player_id='9012'")['scores_fetched_at'] is None
    fake.fail.clear()
    fake.calls.clear()
    at(T0 + 7200)
    r.run_sync()
    assert own_calls(fake)[2] == {'9012'}


# ---- 中止 ----

def test_cancel_keeps_finished_stages(tmp_path):
    # 自分のスコアの取得中に中止: それまでのランキング・譜面と、届いていたスコアは残る。次の取得で全部そろう
    fake = FakeBL()
    armed = [True]

    def hook(p, q):
        if armed[0] and p.endswith('/scores'):
            armed[0] = False
            r.cancel()
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    assert p['cancelled'] and p['error'] is None and not p['running'] and p['phase'] == 'selfScores'
    assert n(st, 'SELECT COUNT(*) AS n FROM players') == 30
    assert n(st, 'SELECT COUNT(*) AS n FROM leaderboards WHERE status=3') == 40
    assert n(st, 'SELECT COUNT(*) AS n FROM scores WHERE player_id=?', (fake.self_id,)) == 20
    assert st.meta('self_scores_synced_at') is None and st.meta('last_run_at') is None
    p = r.run_sync()
    assert not p['cancelled'] and pool_with_scores(st, fake) == POOL and st.meta('last_run_at')


@pytest.mark.parametrize('phase, hit', [
    ('ranking', lambda p, me: p == '/players'),
    ('maps', lambda p, me: p == '/maps'),
    ('selfScores', lambda p, me: p == '/player/%s/accgraph' % me),
    ('attempts', lambda p, me: p.endswith('/scoresstats')),
    ('history', lambda p, me: p.endswith('/history/compact')),
    ('pool', lambda p, me: p.endswith('/accgraph') and me not in p),
])
def test_cancel_in_any_phase_is_not_a_warning(tmp_path, phase, hit):
    # どの段階でも、失敗のやり直しを待つ間に中止されたら警告に化けずに「中止」で止まる（続きの段階へ進まない）
    fake = FakeBL()

    def hook(p, q):
        if hit(p, fake.self_id):
            r.cancel()
            return http_error(503)
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    assert p['cancelled'] and p['error'] is None and p['warnings'] == [] and p['phase'] == phase
    assert st.meta('last_run_at') is None


def test_cancel_during_pool_keeps_fetched_players(tmp_path):
    # 比較対象の取得中に中止: 取れた人は残り（残りの人は取りに行かない）、次の取得では残りの人だけ取る
    fake = FakeBL()
    seen, armed = [], [True]

    def hook(p, q):
        if armed[0] and p.endswith('/accgraph') and fake.self_id not in p:
            seen.append(p)
            if len(seen) >= 3:      # 3人目を取っている間に中止
                r.cancel()
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    assert p['cancelled'] and p['error'] is None and p['phase'] == 'pool'
    done = pool_with_scores(st, fake)
    assert 1 <= len(done) < 10 and len(seen) < 10
    armed[0] = False
    fake.calls.clear()
    p = r.run_sync()
    again = {c[0].split('/')[2] for c in fake.calls if c[0].endswith('/accgraph') and fake.self_id not in c[0]}
    assert not p['cancelled'] and again == POOL - done and pool_with_scores(st, fake) == POOL


# ---- 進み具合・別のスレッド ----

def test_progress_phases(tmp_path):
    # 段階は決まった順に進む。終わると running=False、比較対象は done=total、通信の回数も出る。
    # アテンプトと履歴はランク譜面の前（その間に全曲データを別のスレッドで取る）。全曲データの要求は別のスレッドから来るので、段階の順は _phase で見る
    fake = FakeBL()
    snaps, order = [], []
    st, s, r = make(tmp_path, fake, lambda p, q: snaps.append(r.progress()))
    phase = r._phase

    def record(name, total=0):
        if not order or order[-1] != name:
            order.append(name)
        phase(name, total)
    r._phase = record
    p = r.run_sync()
    assert order == ['self', 'ranking', 'attempts', 'history', 'mapsCheck', 'maps', 'selfScores', 'pool']
    assert all(x['running'] for x in snaps)
    assert not p['running'] and p['finishedAt'] >= p['startedAt']
    assert p['phase'] == 'pool' and p['done'] == p['total'] == 10 and p['requests'] == len(fake.calls)
    p['warnings'].append('x')
    assert r.progress()['warnings'] == []      # 返した進み具合を書き換えても中身は変わらない


def test_start_in_background_and_cancel(tmp_path):
    # 画面からの取得は別のスレッドで動く。動いている間の2回目の開始は無視し、中止すると取得済みの分を残して止まる
    fake = FakeBL()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def hook(p, q):
        if p == '/players':
            entered.set()
            release.wait(10)
    st, s, r = make(tmp_path, fake, hook)
    made, factory = [], r.client_factory
    r.client_factory = lambda: made.append(1) or factory()
    run = r._run
    r._run = lambda: (run(), finished.set())
    assert r.cancel()['running'] is False           # 始める前の中止は何もしない
    assert r.start()['running']
    assert entered.wait(10)
    assert r.start()['running'] and len(made) == 1
    r.cancel()
    release.set()
    assert finished.wait(10)
    p = r.progress()
    assert p['cancelled'] and not p['running'] and p['error'] is None
    assert n(st, 'SELECT COUNT(*) AS n FROM players') == 30     # 中止の時に届いていたランキングは残す


def test_pool_max_picks_most_scores(tmp_path):
    # 上下の範囲の中から、ランクのスコアの多い順に pool_max 人だけ取り、推定も同じ人を使う。同じ数なら ID の順で決まる
    fake = FakeBL()
    ranked(fake, p9010=100, p9011=90, p9012=500, p9013=80, p9014=70, p9016=400, p9017=60, p9018=300, p9019=50, p9020=40)
    st, s, r = make(tmp_path, fake, pool_max=3, min_scores=5)
    p = r.run_sync()
    assert p['error'] is None and pool_calls(fake) == {'9012', '9016', '9018'} == pool_with_scores(st, fake)
    assert p['done'] == p['total'] == 3 and p['skipped'] == 7
    assert [x.player_id for x in M.build(st, s, T0).pool] == ['9012', '9016', '9018']     # 順位の順
    fake.calls.clear()
    s.update({'pool_max': 5})                       # 増やすと、足りない分だけ次の取得で取る
    r.run_sync()
    assert pool_calls(fake) == {'9010', '9011'}
    assert {x.player_id for x in M.build(st, s, T0).pool} == {'9012', '9016', '9018', '9010', '9011'}
    fake.calls.clear()
    s.update({'pool_max': 3})                       # 減らしても前のスコアは消さず、推定には使わない。戻せば取り直さない
    r.run_sync()
    assert pool_calls(fake) == set() and {'9010', '9011'} <= pool_with_scores(st, fake)
    assert {x.player_id for x in M.build(st, s, T0).pool} == {'9012', '9016', '9018'}
    s.update({'pool_max': 5})
    r.run_sync()
    assert pool_calls(fake) == set()


def test_pool_max_follows_changes(tmp_path):
    # スコアが増えて上位に入った人は次の取得で取る。外れた人は変わっていても取らない。また入った時に、変わっていれば取り直す
    fake = FakeBL()
    ranked(fake, p9010=100, p9011=90, p9012=500, p9013=80, p9014=70, p9016=400, p9017=60, p9018=300, p9019=50, p9020=40)
    st, s, r = make(tmp_path, fake, pool_max=3, min_scores=5)
    r.run_sync()
    row = lambda pid: next(p for p in fake.players if p['id'] == pid)
    ranked(fake, p9010=600)                         # 9010 が上位に入り、9018 が外れる
    row('9010')['lastRanked'] += 50
    row('9018')['lastRanked'] += 50
    fake.calls.clear()
    r.run_sync()
    assert pool_calls(fake) == {'9010'}
    assert {x.player_id for x in M.build(st, s, T0).pool} == {'9010', '9012', '9016'}
    ranked(fake, p9018=700)                         # 9018 が戻る（外れている間に変わった分を取り直す）
    fake.calls.clear()
    r.run_sync()
    assert pool_calls(fake) == {'9018'}


def test_pool_max_with_few_scores_and_unknown(tmp_path):
    # スコアが少ない人は人数に入っても取らない。数が分からない人は前に置く（今までどおり取る）
    fake = FakeBL()
    ranked(fake, p9010=10, p9011=10, p9012=10, p9013=10, p9014=10, p9016=None, p9017=10, p9018=500, p9019=400, p9020=10)
    st, s, r = make(tmp_path, fake, pool_max=4)     # min_scores は初期値 30
    r.run_sync()
    assert pool_calls(fake) == {'9016', '9018', '9019'}


def test_pick_pool():
    rows = [{'player_id': a, 'ranked_play_count': n} for a, n in (('a', 5), ('b', 9), ('c', None), ('d', 9), ('e', 1))]
    assert [p['player_id'] for p in M.pick_pool(rows, 3)] == ['c', 'b', 'd']
    assert [p['player_id'] for p in M.pick_pool(rows, 10)] == ['c', 'b', 'd', 'a', 'e']


# ---- ランク譜面の一覧: 全曲データで変化を確かめて、変わった時だけ取る ----

def maps_calls(fake):
    return sum(1 for p, _ in fake.calls if p == '/maps')


UNRANKED = ('ab' * 20, 2, [(1, 3, 500)])       # ランクの曲の、ランクでない難易度に★が付いている（本物に 23譜面ある。手元の一覧には入らない）


def test_maps_only_when_song_details_change(tmp_path, at):
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1', extra=[UNRANKED])
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    assert maps_calls(fake) == 1 and fake.file_requests == [None] and st.meta('song_details_etag') == 'e1'
    fake.calls.clear(); fake.file_requests.clear()
    at(T0 + 3600)                                   # 1日に2回は確かめない
    r.run_sync()
    assert fake.file_requests == [] and maps_calls(fake) == 0
    at(T0 + 86400)                                  # 次の日: ファイルが同じ（304）なら取らない
    r.run_sync()
    assert fake.file_requests == ['e1'] and maps_calls(fake) == 0
    fake.publish_song_details(T0 + 86400, 'e2', extra=[UNRANKED])     # 作り直されたが中身が同じ: ランクでない難易度の★があっても取らない
    at(T0 + 2 * 86400)
    p = r.run_sync()
    assert maps_calls(fake) == 0 and st.meta('song_details_etag') == 'e2' and p['warnings'] == [] and p['notices'] == []
    fake.maps.append(('LB900', 7.0, 1700000000))    # 新しいランク譜面
    fake.publish_song_details(T0 + 2 * 86400, 'e3', extra=[UNRANKED])
    at(T0 + 3 * 86400)
    p = r.run_sync()
    assert maps_calls(fake) == 1 and p['notices'] == []
    assert st.q1("SELECT status FROM leaderboards WHERE leaderboard_id='LB900'")['status'] == 3
    fake.calls.clear()
    fake.publish_song_details(T0 + 3 * 86400, 'e4', extra=[UNRANKED])
    at(T0 + 4 * 86400)
    r.run_sync()
    assert maps_calls(fake) == 0                    # 取り直した後は違いが無い


def test_same_difference_is_not_fetched_again(tmp_path, at):
    # 全曲データと一覧が食い違ったままでも（どちらかの更新が遅れている）、同じ違いなら毎日は取らない
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.calls.clear()
    fake.publish_song_details(T0, 'e2', stars={'LB001': 9.9})
    at(T0 + 86400)
    r.run_sync()
    assert maps_calls(fake) == 1
    fake.calls.clear()
    fake.publish_song_details(T0 + 86400, 'e3', stars={'LB001': 9.9})
    at(T0 + 2 * 86400)
    r.run_sync()
    assert maps_calls(fake) == 0


def test_mass_star_change_is_reweight(tmp_path, at):
    # 多くの譜面の★が変わった（2026-11-06 に実際に起きた形）: リウェイトとして、譜面・自分のスコア・比較対象を全部取り直して知らせる
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.calls.clear()
    fake.maps = [(lb, round(stars * 1.15, 2), up) for lb, stars, up in fake.maps]
    fake.publish_song_details(T0, 'e2')
    at(T0 + 86400)
    p = r.run_sync()
    assert [n_['code'] for n_ in p['notices']] == ['reweight'] and p['notices'][0]['detail'] == 'stars 40'
    assert maps_calls(fake) == 1 and pool_calls(fake) == POOL
    assert st.q1("SELECT stars FROM leaderboards WHERE leaderboard_id='LB010'")['stars'] == pytest.approx(round(4.0 * 1.15, 2))
    fake.calls.clear()
    at(T0 + 2 * 86400)
    p = r.run_sync()
    assert p['notices'] == [] and maps_calls(fake) == 0 and pool_calls(fake) == set()


@pytest.mark.parametrize('broken', [None, (b'not a gzip', 'x')])
def test_maps_fall_back_when_song_details_unavailable(tmp_path, at, broken):
    # 全曲データが取れない・読めない間も取得は止まらず（知らせも出さない）、ランク譜面の一覧は週1回取る
    fake = FakeBL()
    fake.song_details = broken
    st, s, r = make(tmp_path, fake)
    p = r.run_sync()
    assert maps_calls(fake) == 1 and p['error'] is None and p['warnings'] == []
    fake.calls.clear()
    at(T0 + 6 * 86400)
    p = r.run_sync()
    assert maps_calls(fake) == 0 and p['warnings'] == []
    at(T0 + 7 * 86400)
    r.run_sync()
    assert maps_calls(fake) == 1


def test_maps_fall_back_when_song_details_are_old(tmp_path, at):
    # 全曲データの更新が止まっている（作成が1週間より前）: 変化を確かめられないものとして週1回取る
    fake = FakeBL()
    fake.publish_song_details(T0 - 8 * 86400, 'e1')
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.calls.clear()
    at(T0 + 86400)
    r.run_sync()
    assert maps_calls(fake) == 0
    at(T0 + 7 * 86400)
    r.run_sync()
    assert maps_calls(fake) == 1


def test_maps_refetched_monthly_without_change(tmp_path, at):
    # 全曲データでは分からない変化のために、変化が無くても30日に1回は取る
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.calls.clear()
    for d in (6, 12, 18, 24):
        fake.publish_song_details(T0 + d * 86400, 'e%d' % d)
        at(T0 + d * 86400)
        r.run_sync()
    assert maps_calls(fake) == 0
    fake.publish_song_details(T0 + 30 * 86400, 'e30')
    at(T0 + 30 * 86400)
    r.run_sync()
    assert maps_calls(fake) == 1


def test_button_mark_always_fetches_maps(tmp_path, at):
    # 設定のボタン（maps_fetched_at を空にする。serve.py の maps/refetch）: 同じ日でも必ず取り、比べる元も読み直す（ETag を送らない）
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    st, s, r = make(tmp_path, fake)
    r.run_sync()
    fake.calls.clear(); fake.file_requests.clear()
    st.set_meta('maps_fetched_at', None)
    at(T0 + 60)
    r.run_sync()
    assert maps_calls(fake) == 1 and fake.file_requests == [None] and st.meta('maps_fetched_at') == str(T0 + 60)


# ---- 全曲データを BeatLeader の取得と並べて取る ----

def test_song_details_fetched_alongside_beatleader(tmp_path):
    # 全曲データ（jsDelivr。BeatLeader とは別のサーバー）は自分のプロフィールの後に別のスレッドで取り始め、国内ランキング・アテンプト・履歴の
    # 取得と重ねる（取得の時間を短くするため。BeatLeader への要求の数・間隔は変えない）
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    reached, waited = threading.Event(), []

    def hook(p, q):
        if p.endswith('/history/compact'):
            reached.set()
        elif p.startswith('/gh/'):
            waited.append(reached.wait(10))   # 全曲データを使う前の最後の BeatLeader の要求（履歴）が届くまで返さない
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    assert waited == [True] and p['error'] is None and p['warnings'] == []
    assert fake.file_requests == [None] and maps_calls(fake) == 1 and st.meta('song_details_etag') == 'e1'


def test_cancel_while_waiting_for_song_details(tmp_path):
    # 全曲データがまだ届かないうちに中止: 届くのを待たずに「中止」で止まる（警告にしない。確かめた印も付けない）
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    release = threading.Event()

    def hook(p, q):
        if p.startswith('/gh/'):
            for _ in range(1000):   # 取得のスレッドが全曲データを待ち始めるまで
                if r.progress()['phase'] == 'mapsCheck':
                    break
                time.sleep(0.01)
            r.cancel()
            release.wait(10)
    st, s, r = make(tmp_path, fake, hook)
    p = r.run_sync()
    before = not release.is_set()
    release.set()
    assert before and p['cancelled'] and p['error'] is None and p['warnings'] == [] and p['phase'] == 'mapsCheck'
    assert st.meta('maps_checked_at') is None and st.meta('last_run_at') is None


def test_button_during_refresh_rereads_song_details(tmp_path, at, monkeypatch):
    # 全曲データを ETag 付きで取り始めた後に設定のボタンが押された: 比べる元が要るので、ETag を送らずに読み直してランク譜面を取る
    made = []

    class Rec(refresh._SongDetailsPrefetch):
        def __init__(self, *a):
            super().__init__(*a)
            made.append(self)
    monkeypatch.setattr(refresh, '_SongDetailsPrefetch', Rec)
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    armed = []

    def hook(p, q):
        if armed and p.endswith('/history/compact'):
            armed.clear()
            st.set_meta('maps_fetched_at', None)   # serve.py の maps/refetch と同じ
    st, s, r = make(tmp_path, fake, hook)
    r.run_sync()
    made.clear(); fake.calls.clear(); fake.file_requests.clear()
    armed.append(1)
    at(T0 + 86400)
    p = r.run_sync()
    assert len(made) == 1 and made[0].etag == 'e1' and made[0]._done.wait(10)   # 取り始めた物（ETag 付き）は使わない
    assert not armed and len(fake.file_requests) == 2 and set(fake.file_requests) == {'e1', None}
    assert p['error'] is None and maps_calls(fake) == 1 and st.meta('maps_fetched_at') == str(T0 + 86400)
    assert st.meta('song_details_etag') == 'e1'


def test_song_details_stopped_when_refresh_stops_early(tmp_path, monkeypatch):
    # 全曲データを使う前に取得が止まった（想定外の失敗）: 取りかけの全曲データもやめる
    made = []

    class Rec(refresh._SongDetailsPrefetch):
        def __init__(self, *a):
            super().__init__(*a)
            made.append(self)
    monkeypatch.setattr(refresh, '_SongDetailsPrefetch', Rec)
    fake = FakeBL()
    fake.publish_song_details(T0 - 100, 'e1')
    st, s, r = make(tmp_path, fake, lambda p, q: RuntimeError('boom') if p.endswith('/history/compact') else None)
    p = r.run_sync()
    assert p['error'] == 'internal' and len(made) == 1 and made[0]._stop.is_set() and r._pre is None
    assert st.meta('maps_checked_at') is None
