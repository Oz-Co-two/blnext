# 通信（core/fetch/http.py）: 間隔・回数の上限（429・残りわずか）・やり直し・中止・応答の壊れ方。
# 仮の時計を使い、待ちは「時刻を進めて記録する」だけにする（本当には待たない）
import gzip, http.client, io, json, threading, urllib.error, zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import pytest
from core.fetch import http as bhttp
from core.fetch.http import BLClient, Cancelled, NetError, RETRIES, _Limiter, _reset_wait
from fakebl import Resp


class VClock:
    """仮の時計（秒）。waits に待った秒を残す"""
    def __init__(self):
        self.t = 1000.0
        self.waits = []

    def __call__(self):
        return self.t


class VEvent(threading.Event):
    """cancel_event の代わり。wait は本当には待たず、仮の時計を進めるだけ（中止されていれば True）"""
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def wait(self, timeout=None):
        self.clock.waits.append(timeout)
        self.clock.t += timeout
        return self.is_set()


@pytest.fixture
def clock():
    return VClock()


def client(opener, clock):
    c = BLClient('t', opener=opener)
    c._limiter = _Limiter(clock=clock)
    c.cancel_event = VEvent(clock)
    return c


def seq(clock, *answers):
    """呼ばれるたびに answers を順に返す（例外なら投げる。関数なら呼んだ結果）。calls に (パス, 仮の時刻) を残す"""
    it = iter(answers)
    calls = []

    def opener(req, timeout):
        calls.append((urlsplit(req.full_url).path, clock.t))
        a = next(it)
        if callable(a) and not isinstance(a, BaseException):
            a = a()
        if isinstance(a, BaseException):
            raise a
        return a
    opener.calls = calls
    return opener


def ok(body=None, headers=None):
    return Resp({} if body is None else body, headers=headers or {})


def err(code, headers=None):
    return urllib.error.HTTPError('http://x', code, 'x', headers or {}, io.BytesIO(b''))


def iso(sec):
    """今から sec 秒後の時刻を BeatLeader の X-Rate-Limit-Reset の形（小数7桁・Z）で"""
    t = datetime.now(timezone.utc) + timedelta(seconds=sec)
    return t.strftime('%Y-%m-%dT%H:%M:%S.%f') + '4Z'


# ---- 待つ秒の決め方 ----

@pytest.mark.parametrize('headers, want', [
    ({'Retry-After': '7'}, 7.0),
    ({'Retry-After': '999'}, 120.0),          # 長すぎる指示は2分で打ち切る
    ({'Retry-After': '-3'}, 0.0),
    ({'X-Rate-Limit-Reset': 'garbage'}, 10.0),  # 読めなければ決めておいた秒
    ({'X-Rate-Limit-Reset': ''}, 10.0),
    ({}, 10.0),
])
def test_reset_wait_fixed(headers, want):
    assert _reset_wait(headers, 10.0) == want


def test_reset_wait_until_reset_time():
    # X-Rate-Limit-Reset までの秒＋0.5。Retry-After が日付の形（数でない）ならこちらを使う。時差が無ければ UTC。過ぎていれば0
    assert _reset_wait({'X-Rate-Limit-Reset': iso(20)}, 10.0) == pytest.approx(20.5, abs=1)
    assert _reset_wait({'Retry-After': 'Fri, 16 Oct 2026 12:34:56 GMT', 'X-Rate-Limit-Reset': iso(20)}, 10.0) == pytest.approx(20.5, abs=1)
    assert _reset_wait({'X-Rate-Limit-Reset': iso(20)[:-1]}, 10.0) == pytest.approx(20.5, abs=1)
    assert _reset_wait({'X-Rate-Limit-Reset': iso(-30)}, 10.0) == 0.0
    assert _reset_wait({'X-Rate-Limit-Reset': iso(3600)}, 10.0) == 120.0


def test_limiter_spacing_and_hold(clock):
    # 区分ごとに開始の間隔を空ける。短い「止める」指示で前の長い待ちを縮めない
    lim = _Limiter(clock=clock)
    assert [lim.reserve('all', 0.25) for _ in range(3)] == [0, 0.25, 0.5]
    assert lim.reserve('players', 2.0) == 0
    lim.hold('all', 5)
    lim.hold('all', 1)
    assert lim.reserve('all', 0.25) == 5
    clock.t += 100
    assert lim.reserve('all', 0.25) == 0


# ---- 間隔・回数の上限 ----

def test_request_spacing(clock):
    # 全体は0.25秒ごと（1秒4回）、/players はさらに2秒ごと（上限が厳しい）
    a = seq(clock, ok, ok, ok)
    c = client(a, clock)
    for _ in range(3):
        c.get('/maps')
    assert [t - a.calls[0][1] for _, t in a.calls] == [0, 0.25, 0.5]
    b = seq(clock, ok, ok, ok)
    c = client(b, clock)
    for _ in range(3):
        c.get('/players')
    assert [t - b.calls[0][1] for _, t in b.calls] == [0, 2, 4]


def test_429_waits_as_told_then_succeeds(clock):
    a = seq(clock, err(429, {'Retry-After': '3'}), ok({'v': 1}))
    c = client(a, clock)
    assert c.get('/maps') == {'v': 1}
    assert clock.waits == [3.0] and a.calls[1][1] - a.calls[0][1] == 3.0
    assert c.requests == 2


def test_low_remaining_holds_until_reset(clock):
    # 残りが3回以下になったら区切りまでその区分を止める（/players の残りわずかは他の要求を止めない）。残りが多ければ止めない
    low = {'X-Rate-Limit-Remaining': '1', 'Retry-After': '5'}
    a = seq(clock, ok({}, low), ok, ok, ok({}, low), ok, ok({}, {'X-Rate-Limit-Remaining': '47', 'Retry-After': '5'}), ok)
    c = client(a, clock)
    for p in ('/players', '/maps', '/players', '/maps', '/maps', '/maps', '/maps'):
        c.get(p)
    t = [x for _, x in a.calls]
    assert t[1] - t[0] == 0.25     # /maps は待たない
    assert t[2] - t[0] == 5        # 次の /players は区切りまで待つ
    assert t[4] - t[3] == 5        # /maps の残りわずかは /maps を止める
    assert t[6] - t[5] == 0.25     # 残りが多い時は止めない


# ---- やり直し ----

def test_5xx_and_offline_retry_with_growing_waits(clock):
    # 5xx・通信の失敗・時間切れは 1, 2 秒と待ちを伸ばしてやり直す
    a = seq(clock, err(503), TimeoutError('timed out'), ok({'v': 1}))
    c = client(a, clock)
    assert c.get('/maps') == {'v': 1}
    assert clock.waits == [1, 2] and c.requests == 3


def test_5xx_gives_up_with_status_code(clock):
    a = seq(clock, *[lambda: err(500)] * (1 + RETRIES))
    with pytest.raises(NetError) as e:
        client(a, clock).get('/maps')
    assert e.value.code == 'http-500'
    assert clock.waits == [1, 2, 4] and len(a.calls) == 1 + RETRIES


def test_bad_json_is_not_retried(clock):
    # 200 なのに JSON でない（メンテナンスの画面など）はやり直さずに bad-json
    a = seq(clock, ok(b'<html>maintenance</html>'))
    with pytest.raises(NetError) as e:
        client(a, clock).get('/maps')
    assert e.value.code == 'bad-json' and len(a.calls) == 1


def test_http_error_with_unreadable_body(clock):
    # HTTP エラーの本文が読めなくても状態コードで判断する（4xx はやり直さない）
    class BadBody(io.BytesIO):
        def read(self, *a):
            raise OSError('reset')
    a = seq(clock, urllib.error.HTTPError('http://x', 404, 'nf', {}, BadBody()))
    with pytest.raises(NetError) as e:
        client(a, clock).get('/maps')
    assert e.value.code == 'http-404' and len(a.calls) == 1


def test_gzip_body_is_decoded(clock):
    body = gzip.compress(json.dumps({'v': 'ok'}).encode())
    assert client(seq(clock, ok(body, {'Content-Encoding': 'gzip'})), clock).get('/maps') == {'v': 'ok'}


def test_not_gzip_body_is_retried(clock):
    # gzip のはずが gzip でない本文は通信の失敗として扱い、やり直す
    a = seq(clock, ok(b'not gzip', {'Content-Encoding': 'gzip'}), ok({'v': 1}))
    assert client(a, clock).get('/maps') == {'v': 1}
    assert clock.waits == [1]


class CutResp(Resp):
    """本文を読む途中で接続が切れる応答（Content-Length より短い）"""
    def read(self):
        raise http.client.IncompleteRead(b'{"da', 1000)


_GZ = gzip.compress(json.dumps({'data': list(range(2000))}).encode())


@pytest.mark.parametrize('bad', [
    lambda: CutResp(b''),
    lambda: ok(_GZ[:len(_GZ) // 2], {'Content-Encoding': 'gzip'}),
    lambda: ok(_GZ[:20] + bytes(50) + _GZ[70:], {'Content-Encoding': 'gzip'}),
], ids=['incomplete-read', 'gzip-truncated', 'gzip-corrupt'])
def test_cut_body_is_retried(clock, bad):
    # 本文の途中切れ（IncompleteRead）・途中で切れた gzip（EOFError）・壊れた gzip（zlib.error）も通信の失敗としてやり直す
    a = seq(clock, bad, ok({'v': 1}))
    assert client(a, clock).get('/maps') == {'v': 1}


# ---- 中止 ----

def test_cancel_before_request_does_not_connect(clock):
    a = seq(clock)
    c = client(a, clock)
    c.cancel_event.set()
    with pytest.raises(Cancelled):
        c.get('/maps')
    assert a.calls == []


@pytest.mark.parametrize('fail', [lambda: urllib.error.URLError('down'), lambda: err(429, {'Retry-After': '60'})],
                         ids=['offline', '429'])
def test_cancel_while_waiting_to_retry(clock, fail):
    # やり直し・回数の上限で待っている間に中止されたら、待ち切らずにすぐやめる
    box = []

    def opener(req, timeout):
        box[0].cancel_event.set()
        raise fail()
    c = client(opener, clock)
    box.append(c)
    with pytest.raises(Cancelled):
        c.get('/maps')
    assert c.requests == 1


def test_real_sleep_and_cancel():
    # 本物の _sleep: 待ち0は何もしない。中止済みなら（待つ秒があってもなくても）すぐ Cancelled
    c = BLClient('t', opener=lambda req, timeout: None)
    c._sleep(0)
    c.cancel_event.set()
    for s in (0, 30):
        with pytest.raises(Cancelled):
            c._sleep(s)


# ---- 同時接続・ネットの設定 ----

def test_at_most_two_connections(monkeypatch):
    # 同時に通信するのは2本まで（2本はそろって通信でき、3本目は入らない）
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    lock, active, peak = threading.Lock(), [0], [0]
    pair = threading.Barrier(2, timeout=5)

    def opener(req, timeout):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            pair.wait()
        finally:
            with lock:
                active[0] -= 1
        return Resp({})
    c = BLClient('t', opener=opener)
    errors = []

    def work():
        try:
            c.get('/maps')
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=work) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert errors == [] and peak[0] == 2


def test_no_net_setting_fails_fast():
    # テストでは（BLN_NO_NET=1）通信せず、待たずにすぐ offline
    c = BLClient('t')
    with pytest.raises(NetError) as e:
        c.get('/player/1')
    assert e.value.code == 'offline' and c.requests == 0
    with pytest.raises(urllib.error.URLError):
        bhttp._no_net(None, 1)


def test_default_opener_uses_urlopen_with_timeout(monkeypatch):
    # 普段は urllib の urlopen で、時間切れの秒（TIMEOUT）を付けて通信する（ここでは urlopen を差し替えてネットに出ない）
    seen = []
    monkeypatch.setattr(bhttp.urllib.request, 'urlopen', lambda req, timeout: seen.append((req.full_url, timeout)) or Resp({'v': 1}))
    monkeypatch.delenv('BLN_NO_NET')
    assert BLClient('t').get('/maps', page=2, time_from=None) == {'v': 1}
    assert seen == [(bhttp.API_BASE + '/maps?page=2', bhttp.TIMEOUT)]   # 値の無い引数は URL に入れない
