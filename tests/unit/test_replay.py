# 自己ベストのリプレイの頭（core/fetch/replay.py）: .bsor の先頭から JD を読む・URL の確かめ・RT の計算・通信の失敗の分け方・
# DB の replay_info に残す物（やり直さない）と残さない物（次の機会にやり直す）。通信は作り物（ネットに出ない）
import http.client, io, struct, urllib.error
import pytest
from core.fetch import beatleader as bl, http as bhttp, replay
from core.fetch.http import NetError
from core.store.db import Store
from bsor import make_header
from fakebl import FakeBL, Resp

URL = 'https://cdn.replays.beatleader.com/35076769-76561198000000000-ExpertPlus-Standard-ABC.bsor'


def url(n):
    return 'https://cdn.replays.beatleader.com/%d-9000-ExpertPlus-Standard-ABC.bsor' % n


def http_err(code):
    return urllib.error.HTTPError('https://x', code, 'x', {}, io.BytesIO(b''))


class Opener:
    """urlopen の代わり。URL → 答え（bytes なら 206 の本体・例外なら投げる・Resp ならそのまま。無ければ JD 20 のヘッダー）。
    reqs に要求を、log に ('get', URL) を残す（sleep の順番を見るため、log は外から渡せる）"""
    def __init__(self, answers=None, log=None):
        self.answers = answers or {}
        self.reqs = []
        self.log = [] if log is None else log

    def __call__(self, req, timeout):
        self.reqs.append(req)
        self.log.append(('get', req.full_url))
        a = self.answers.get(req.full_url, make_header(20.0))
        if isinstance(a, BaseException):
            raise a
        return Resp(a, status=206) if isinstance(a, bytes) else a


@pytest.fixture
def st():
    s = Store(':memory:')
    yield s
    s.close()


def rows(st):
    return {r['score_id']: (r['jd'], r['status']) for r in st.q('SELECT * FROM replay_info')}


# ---- parse_info ----

def test_parse_info_reads_jd():
    # 文字列の長さは UTF-8 のバイト数（日本語の曲名・マッパー名でもずれない）
    h = make_header(21.11, song='日本語の曲名★♪', mapper='マッパー🎵', modifiers='FS,GN')
    song = '日本語の曲名★♪'.encode()
    assert struct.pack('<i', len(song)) + song in h
    assert replay.parse_info(h) == {'jd': 21.11}
    assert replay.parse_info(h + bytes(5000)) == {'jd': 21.11}        # 後ろに続きの記録があっても先頭だけ見る
    assert replay.parse_info(h[:len(h) - 17]) == {'jd': 21.11}       # JD の後ろ（利き手・身長など）は無くてよい


def _set_first_len(h, n):
    b = bytearray(h)
    struct.pack_into('<i', b, 6, n)   # 最初の文字列（version）の長さ
    return bytes(b)


H = make_header(21.11)


@pytest.mark.parametrize('b', [
    struct.pack('<i', 0x12345678) + H[4:],          # magic 違い
    H[:5] + b'\x01' + H[6:],                          # info でない区切り
    b'',
    H[:3],
    H[:40],                                           # 文字列の途中で切れた
    H[:len(H) - 19],                                  # JD の途中で切れた
    _set_first_len(H, -1),                            # 文字列の長さが負
    _set_first_len(H, 5000),                          # 文字列の長さが大きすぎる
    _set_first_len(H, 2000),                          # 長さは許す範囲でも、その分の中身が無い
    make_header(0.0),
    make_header(-5.0),
    make_header(1000.0),
    make_header(float('nan')),
    make_header(float('inf')),
], ids=['magic', 'section', 'empty', 'short-magic', 'cut-string', 'cut-jd', 'len-negative', 'len-huge', 'len-past-end',
        'jd-0', 'jd-negative', 'jd-1000', 'jd-nan', 'jd-inf'])
def test_parse_info_rejects_broken(b):
    with pytest.raises(ValueError):
        replay.parse_info(b)


# ---- is_replay_url ----

@pytest.mark.parametrize('u', [
    URL,
    'https://cdn.replays.beatleader.xyz/1-2-Expert-Standard-H.bsor',
    'https://beatleader.com/x.bsor',
    'https://CDN.Replays.BeatLeader.com/x.BSOR',      # 大文字・小文字は区別しない
])
def test_is_replay_url_accepts(u):
    assert replay.is_replay_url(u)


@pytest.mark.parametrize('u', [
    'http://cdn.replays.beatleader.com/x.bsor',               # http
    'https://cdn.replays.beatleader.com.evil.example/x.bsor',  # 名前の頭だけ同じ別のホスト
    'https://evilbeatleader.com/x.bsor',
    'https://evil.example/cdn.replays.beatleader.com/x.bsor',
    'https://beatleader.com@evil.example/x.bsor',             # ユーザー名に見せかけた別のホスト
    'https://cdn.replays.beatleader.com/x.json',               # .bsor でない
    'https://cdn.replays.beatleader.com/x.bsor.exe',
    'file:///C:/x.bsor',
    'https://[::1/x.bsor',                                     # 壊れた URL（urlsplit が ValueError）
    '',
    None,
    123,
    b'https://cdn.replays.beatleader.com/x.bsor',
    [URL],
])
def test_is_replay_url_rejects(u):
    assert not replay.is_replay_url(u)


# ---- rt_ms ----

def test_rt_ms():
    # RT = JD の半分 ÷ ノーツの速さ（NJS × 曲の速さ）
    assert replay.rt_ms(21.1, 18) == replay.rt_ms(21.1, 18, '') == replay.rt_ms(21.1, 18, None) == 586
    assert replay.rt_ms(21.1, 18, 'FS') == 488                     # 1.2 倍速いので RT は短い
    assert abs(replay.rt_ms(21.1, 18, 'FS') * 1.2 - 586) < 1
    assert replay.rt_ms(21.1, 18, 'GN, fs ') == 488                # ほかのモディファイア・空白・小文字が混ざっても読む
    assert replay.rt_ms(21.1, 18, 'SS') == 690
    assert replay.rt_ms(21.1, 18, 'SF,NF') == 391


@pytest.mark.parametrize('args', [(21.1, 18, 'BFS'), (21.1, 18, 'FS,BSF'), (None, 18, ''), (21.1, None, ''), (0, 18, ''), (21.1, 0, '')])
def test_rt_ms_unknown(args):
    # 速さの倍率が分からない（BFS・BSF）・JD か NJS が無い時は出さない
    assert replay.rt_ms(*args) is None


# ---- fetch_info ----

def test_fetch_info_reads_head():
    op = Opener({URL: make_header(21.11)})
    assert replay.fetch_info(URL, 'blnext/test', opener=op) == {'jd': 21.11}
    req = op.reqs[0]
    assert req.full_url == URL
    assert req.get_header('Range') == 'bytes=0-%d' % (replay.HEAD_BYTES - 1)
    assert req.get_header('User-agent') == 'blnext/test'


def test_fetch_info_reads_only_head_of_full_body():
    # Range を無視して全部（200）を返す所でも、先頭だけ読んで閉じる
    resp = Resp(make_header(19.5) + bytes(1_000_000), status=200)
    assert replay.fetch_info(URL, 'ua', opener=Opener({URL: resp})) == {'jd': 19.5}
    assert resp.read_args == [replay.HEAD_BYTES]


@pytest.mark.parametrize('answer, code', [
    (http_err(404), 'http-404'),
    (http_err(429), 'http-429'),
    (http_err(503), 'http-503'),
    (Resp(b'', status=204), 'http-204'),                 # 200・206 以外
    (urllib.error.URLError('down'), 'offline'),
    (TimeoutError('slow'), 'offline'),
    (ConnectionResetError('reset'), 'offline'),
    (http.client.IncompleteRead(b'', 100), 'offline'),
    (b'<html>not a replay</html>', 'bad-replay'),
    (b'', 'bad-replay'),
    (make_header(1000.0), 'bad-replay'),
], ids=['404', '429', '503', '204', 'urlerror', 'timeout', 'reset', 'incomplete', 'html', 'empty', 'bad-jd'])
def test_fetch_info_errors(answer, code):
    with pytest.raises(NetError) as e:
        replay.fetch_info(URL, 'ua', opener=Opener({URL: answer}))
    assert e.value.code == code


def test_fetch_info_no_net(monkeypatch):
    # opener を省くと、BLN_NO_NET=1 の時はネットに出ない（本物の urlopen を呼ばない）
    monkeypatch.setenv('BLN_NO_NET', '1')
    monkeypatch.setattr(bhttp, '_urlopen', lambda req, timeout: pytest.fail('went to the net'))
    with pytest.raises(NetError) as e:
        replay.fetch_info(URL, 'ua')
    assert e.value.code == 'offline'


# ---- known・ensure ----

def test_ensure_saves_jd(st):
    op = Opener({url(1): make_header(21.11), url(2): make_header(18.5)})
    assert replay.ensure(st, [(1, url(1)), (2, url(2))], 'ua', opener=op, now=1234, sleep=lambda s: None) == 0
    assert st.q('SELECT * FROM replay_info ORDER BY score_id') == [
        {'score_id': 1, 'jd': 21.11, 'status': 'ok', 'fetched_at': 1234},
        {'score_id': 2, 'jd': 18.5, 'status': 'ok', 'fetched_at': 1234}]
    assert op.reqs[0].get_header('User-agent') == 'ua'
    # known: 読んだ物だけ。ID でない物は無視する
    k = replay.known(st, [1, 2, 3, None, 'x', 1])
    assert sorted(k) == [1, 2] and k[1]['jd'] == 21.11 and k[1]['status'] == 'ok'
    assert replay.known(st, []) == {} and replay.known(st, [None]) == {}


def test_ensure_does_not_read_again(st):
    # もう読んだ物は読まない（自己ベストが変わればスコアの ID も変わる）
    op = Opener()
    replay.ensure(st, [(1, url(1))], 'ua', opener=op, sleep=lambda s: None)
    assert replay.ensure(st, [(1, url(1)), (2, url(2))], 'ua', opener=op, sleep=lambda s: None) == 0
    assert [r.full_url for r in op.reqs] == [url(1), url(2)]


def test_ensure_keeps_gone_and_does_not_retry(st):
    # 無い・読めない（4xx（429 を除く）・中身が読めない・URL が不正）は残し、次からは試さない。失敗の数には数えない
    op = Opener({url(1): http_err(404), url(2): b'junk', url(3): http_err(403)})
    pairs = [(1, url(1)), (2, url(2)), (3, url(3)), (4, 'https://evil.example/x.bsor'), (5, 'http://cdn.replays.beatleader.com/x.bsor')]
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 0
    assert rows(st) == {1: (None, 'http-404'), 2: (None, 'bad-replay'), 3: (None, 'http-403'), 4: (None, 'bad-url'), 5: (None, 'bad-url')}
    assert [r.full_url for r in op.reqs] == [url(1), url(2), url(3)]   # 不正な URL へは通信しない
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 0
    assert len(op.reqs) == 3


def test_ensure_retries_server_errors_later(st):
    # 5xx・429 は残さず失敗に数え、次の機会にやり直す。つながらないのとは違い、残りは続けて試す
    op = Opener({url(1): http_err(500), url(2): http_err(429), url(3): http_err(503)})
    pairs = [(1, url(1)), (2, url(2)), (3, url(3)), (4, url(4))]
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 3
    assert rows(st) == {4: (20.0, 'ok')}
    op.answers = {}
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 0
    assert rows(st) == {i: (20.0, 'ok') for i in (1, 2, 3, 4)}
    assert len(op.reqs) == 7


@pytest.mark.parametrize('at', [0, 1])
def test_ensure_offline_stops(st, at):
    # つながらない時は残さず、残りも試さずに全部を失敗に数える
    pairs = [(i, url(i)) for i in range(1, 5)]
    op = Opener({url(at + 1): urllib.error.URLError('down')})
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 4 - at
    assert len(op.reqs) == at + 1
    assert rows(st) == {i: (20.0, 'ok') for i in range(1, at + 1)}


def test_ensure_no_net_without_opener(st, monkeypatch):
    monkeypatch.setenv('BLN_NO_NET', '1')
    monkeypatch.setattr(bhttp, '_urlopen', lambda req, timeout: pytest.fail('went to the net'))
    assert replay.ensure(st, [(1, url(1)), (2, url(2))], 'ua', sleep=lambda s: None) == 2
    assert rows(st) == {}


def test_ensure_sleeps_between_reads(st):
    # 続けて読む時だけ間を空ける（1つ目の前には待たない）
    log = []
    op = Opener(log=log)
    replay.ensure(st, [(i, url(i)) for i in range(1, 4)], 'ua', opener=op, sleep=lambda s: log.append(('sleep', s)))
    assert log == [('get', url(1)), ('sleep', replay.INTERVAL), ('get', url(2)), ('sleep', replay.INTERVAL), ('get', url(3))]


def test_ensure_max_per_call(st):
    # 1回に読むのは MAX_PER_CALL まで。残りは次に呼んだ時
    n = replay.MAX_PER_CALL + 5
    pairs = [(i, url(i)) for i in range(1, n + 1)]
    op = Opener()
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 0
    assert len(op.reqs) == replay.MAX_PER_CALL and len(rows(st)) == replay.MAX_PER_CALL
    replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None)
    assert len(op.reqs) == n and len(rows(st)) == n


def test_ensure_skips_missing_and_duplicates(st):
    # スコアの ID・URL の無い物は飛ばす（URL はまだ無い＝次の取得の後）。同じ ID は1回だけ読む
    op = Opener()
    pairs = [(None, url(1)), (2, None), (3, ''), ('4', url(4)), (5, url(5)), (5, url(5))]
    assert replay.ensure(st, pairs, 'ua', opener=op, sleep=lambda s: None) == 0
    assert [r.full_url for r in op.reqs] == [url(5)]
    assert rows(st) == {5: (20.0, 'ok')}
    assert replay.ensure(st, [], 'ua', opener=op) == 0


def test_fake_beatleader_replays(st):
    # 作り物の BeatLeader: 自分のスコアにリプレイの URL があり（norm_score が残す）、その先頭から譜面ごとの JD が読める
    fake = FakeBL()
    lbs = ['LB000', 'LB001', 'LB007']
    fake.names['LB007'] = '曲名に日本語と記号 ★☆♪'
    scores = [bl.norm_score(fake._score(lb, fake.my_scores[lb]))[0] for lb in lbs]
    assert all(replay.is_replay_url(s['replay_url']) and fake.self_id in s['replay_url'] for s in scores)
    assert replay.ensure(st, [(s['score_id'], s['replay_url']) for s in scores], 'ua', opener=fake, sleep=lambda s: None) == 0
    got = replay.known(st, [s['score_id'] for s in scores])
    assert [got[s['score_id']]['jd'] for s in scores] == [round(fake.jd_of(lb), 3) for lb in lbs]
    assert all(18 <= fake.jd_of(lb) <= 22 for lb in lbs) and len({fake.jd_of(lb) for lb in lbs}) > 1
    # 無いリプレイは 404 で残す
    assert replay.ensure(st, [(99, 'https://cdn.replays.beatleader.com/99-x.bsor')], 'ua', opener=fake, sleep=lambda s: None) == 0
    assert replay.known(st, [99])[99]['status'] == 'http-404'
