# 曲のカバー画像（core/fetch/cover.py と serve.py の GET /cover/<ハッシュ>.jpg）: ハッシュの確かめ・CDN から取ってためる・
# 中身の確かめ・取れなかった物を覚える・予備の CDN・つながらない時に止める・ネットに出ない設定・合言葉なしで返すが Host は確かめる。
# 通信は作り物（ネットに出ない）
import http.client, io, os, threading, urllib.error
import pytest

import serve
from core.fetch import cover
from fakebl import Resp

H = 'ab' * 20                       # 40桁の16進
H2 = 'cd' * 20
JPEG = b'\xff\xd8\xff\xe0' + b'jpeg-body' * 50
PNG = b'\x89PNG\r\n\x1a\n' + b'png-body' * 50
MAIN, BACKUP = cover.CDN_URLS[0] % H, cover.CDN_URLS[1] % H


def http_err(code):
    return urllib.error.HTTPError('https://x', code, 'x', {}, io.BytesIO(b''))


class Opener:
    """urlopen の代わり。URL → 答え（bytes なら 200 の本体・例外なら投げる・Resp ならそのまま。無ければ JPEG）"""
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.reqs = []

    def __call__(self, req, timeout):
        self.reqs.append(req)
        a = self.answers.get(req.full_url, JPEG)
        if isinstance(a, BaseException):
            raise a
        return Resp(a) if isinstance(a, bytes) else a

    @property
    def urls(self):
        return [r.full_url for r in self.reqs]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def net(monkeypatch):
    """取りに行ける設定（テスト全体は BLN_NO_NET=1。通信は必ず作り物の Opener）"""
    monkeypatch.setenv('BLN_NO_NET', '0')


def make(tmp_path, answers=None):
    """待つ（取り始めの間）は数えるだけで本当には待たない（clock.slept）"""
    op, clock = Opener(answers), Clock()
    clock.slept = []
    return cover.Covers(str(tmp_path / 'song_covers'), 'blnext/test', opener=op, clock=clock, sleep=clock.slept.append), op, clock


# ---- ハッシュと中身の確かめ ----

def test_parse_name():
    assert cover.parse_name(H + '.jpg') == H
    assert cover.parse_name(H.upper() + '.jpg') == H          # 大文字は小文字にそろえる
    for bad in (H[:-1] + '.jpg', H + 'a.jpg', 'g' * 40 + '.jpg', H + '.png', H, H + '.jpg.jpg', '..', '', None, 'x/' + H + '.jpg'):
        assert cover.parse_name(bad) is None, bad
    assert cover.is_hash(H) and not cover.is_hash(H.upper()) and not cover.is_hash(H[:39])


def test_image_type():
    assert cover.image_type(JPEG) == 'image/jpeg' and cover.image_type(PNG) == 'image/png'
    assert cover.image_type(b'<html>not found</html>') is None and cover.image_type(b'') is None and cover.image_type(None) is None
    assert cover.image_type(b'\xff\xd8\xff' + bytes(cover.MAX_BYTES)) is None   # 大きすぎる


# ---- 取ってためる ----

def test_fetch_and_store(tmp_path, net):
    c, op, _ = make(tmp_path)
    assert c.get(H) == (JPEG, 'image/jpeg')
    assert op.urls == [MAIN]                                   # 主の CDN から。URL はハッシュから決まる
    assert op.reqs[0].get_header('User-agent') == 'blnext/test'
    with open(tmp_path / 'song_covers' / (H + '.jpg'), 'rb') as f:
        assert f.read() == JPEG
    assert not os.path.exists(tmp_path / 'song_covers' / (H + '.jpg.tmp'))   # 書き終えてから置き換える
    assert c.get(H) == (JPEG, 'image/jpeg') and len(op.reqs) == 1             # 2回目はためた物（取りに行かない）


def test_fetches_are_paced(tmp_path, net):
    """取り始めは MIN_INTERVAL 秒ずつ空ける（初めて見る長い表でも一度に取りに行かない）。ためた物を返す時は待たない"""
    c, op, clock = make(tmp_path)
    assert c.get(H) and clock.slept == []                     # 1枚目はすぐ
    assert c.get(H2) and clock.slept == [pytest.approx(cover.MIN_INTERVAL)]   # 2枚目は前の取り始めから空ける
    assert c.get(H) and c.get(H2) and len(clock.slept) == 1 and len(op.reqs) == 2   # ためた物
    clock.t += 10                                              # 間が空いていれば待たない
    assert c.get('ef' * 20) and len(clock.slept) == 1


def test_png_is_kept_with_its_type(tmp_path, net):
    c, _, _ = make(tmp_path, {MAIN: PNG})
    assert c.get(H) == (PNG, 'image/png')
    assert c.cached(H) == (PNG, 'image/png')


def test_bad_hash_is_not_fetched(tmp_path, net):
    c, op, _ = make(tmp_path)
    assert c.get(H.upper()) is None and c.get('../' + H) is None and c.get(None) is None
    assert op.reqs == []


@pytest.mark.parametrize('answer', [b'<html>404</html>', b'', Resp(JPEG, headers={'Content-Length': str(cover.MAX_BYTES + 1)}),
                                    b'\xff\xd8\xff' + bytes(cover.MAX_BYTES)], ids=['html', 'empty', 'big-header', 'big-body'])
def test_not_an_image_is_not_stored(tmp_path, net, answer):
    c, op, clock = make(tmp_path, {MAIN: answer})
    assert c.get(H) is None
    assert not os.path.exists(tmp_path / 'song_covers' / (H + '.jpg'))
    assert op.urls == [MAIN]                                   # 中身がだめな物は、予備も同じなので取りに行かない
    assert c.get(H) is None and len(op.reqs) == 1              # 覚えて、すぐには取り直さない
    clock.t += cover.NET_TTL + 1
    assert c.get(H) is None and len(op.reqs) == 1              # 無い・画像でない物は長く覚える
    clock.t += cover.GONE_TTL
    assert c.get(H) is None and len(op.reqs) == 2


def test_not_found_is_remembered(tmp_path, net):
    c, op, clock = make(tmp_path, {MAIN: http_err(404)})
    assert c.get(H) is None and op.urls == [MAIN]              # 404 は予備にも無い
    assert c.get(H) is None and len(op.reqs) == 1
    assert c.get(H2) == (JPEG, 'image/jpeg')                   # ほかの曲は取りに行く
    clock.t += cover.GONE_TTL + 1
    op.answers[MAIN] = JPEG
    assert c.get(H) == (JPEG, 'image/jpeg')                    # 時間がたてば取り直す


def test_server_error_tries_backup_and_retries_sooner(tmp_path, net):
    c, op, clock = make(tmp_path, {MAIN: http_err(503), BACKUP: http_err(500)})
    assert c.get(H) is None and op.urls == [MAIN, BACKUP]
    assert c.get(H) is None and len(op.reqs) == 2
    assert c.get(H2) is not None                               # 5xx では全部は止めない
    clock.t += cover.NET_TTL + 1
    op.answers[MAIN] = JPEG
    assert c.get(H) == (JPEG, 'image/jpeg')


def test_backup_cdn_when_main_unreachable(tmp_path, net):
    c, op, _ = make(tmp_path, {MAIN: urllib.error.URLError('timed out'), BACKUP: PNG})
    assert c.get(H) == (PNG, 'image/png')
    assert op.urls == [MAIN, BACKUP]
    assert c.cached(H) == (PNG, 'image/png')


def test_offline_stops_all_fetches_for_a_while(tmp_path, net):
    down = {MAIN: urllib.error.URLError('offline'), BACKUP: http.client.IncompleteRead(b'')}
    c, op, clock = make(tmp_path, {**down, cover.CDN_URLS[0] % H2: urllib.error.URLError('offline')})
    assert c.get(H) is None and len(op.reqs) == 2
    assert c.get(H2) is None and len(op.reqs) == 2             # つながらないと分かったので、ほかの曲もしばらく取りに行かない
    clock.t += cover.DOWN_TTL + 1
    op.answers = {}
    assert c.get(H2) == (JPEG, 'image/jpeg')                   # 止める間が過ぎた
    assert c.get(H) is None                                    # 取れなかった曲は、その曲の間だけ待つ
    clock.t += cover.NET_TTL
    assert c.get(H) == (JPEG, 'image/jpeg')


def test_no_net_uses_only_stored(tmp_path, monkeypatch):
    monkeypatch.setenv('BLN_NO_NET', '1')
    c, op, _ = make(tmp_path)
    assert c.get(H) is None and op.reqs == []
    os.makedirs(tmp_path / 'song_covers')
    (tmp_path / 'song_covers' / (H + '.jpg')).write_bytes(JPEG)
    assert c.get(H) == (JPEG, 'image/jpeg') and op.reqs == []


def test_broken_stored_file_is_fetched_again(tmp_path, net):
    c, op, _ = make(tmp_path)
    os.makedirs(tmp_path / 'song_covers')
    (tmp_path / 'song_covers' / (H + '.jpg')).write_bytes(b'broken')
    assert c.get(H) == (JPEG, 'image/jpeg') and len(op.reqs) == 1
    assert c.cached(H) == (JPEG, 'image/jpeg')


def test_same_hash_is_fetched_once_at_a_time(tmp_path, net):
    gate, started = threading.Event(), threading.Event()

    class Slow(Opener):
        def __call__(self, req, timeout):
            started.set()
            gate.wait(5)
            return super().__call__(req, timeout)
    op = Slow()
    c = cover.Covers(str(tmp_path / 'song_covers'), 'ua', opener=op, clock=Clock())
    out = []
    ts = [threading.Thread(target=lambda: out.append(c.get(H))) for _ in range(3)]
    for t in ts:
        t.start()
    started.wait(5)
    gate.set()
    for t in ts:
        t.join(5)
    assert out == [(JPEG, 'image/jpeg')] * 3 and len(op.reqs) == 1


def test_redirect_only_within_cdn():
    hd = cover._CdnOnly()
    import urllib.request
    req = urllib.request.Request(MAIN)
    assert hd.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/x.jpg') is None
    assert hd.redirect_request(req, None, 302, 'Found', {}, 'http://cdn.beatsaver.com/x.jpg') is None    # https だけ
    assert hd.redirect_request(req, None, 302, 'Found', {}, 'https://notbeatsaver.com/x.jpg') is None
    assert hd.redirect_request(req, None, 302, 'Found', {}, 'https://cdn.beatsaver.com/x.jpg').full_url == 'https://cdn.beatsaver.com/x.jpg'


# ---- serve.py の GET /cover/<ハッシュ>.jpg ----

@pytest.fixture(scope='module')
def server():
    httpd = serve.make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def covers(tmp_path, monkeypatch):
    c, op, clock = make(tmp_path)
    monkeypatch.setattr(serve, 'SONG_COVERS', c)
    return c, op


def get(port, path, host=None, headers=None):
    """合言葉なしの GET → (状態, ヘッダー, 本体)"""
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    c.request('GET', path, headers={**({'Host': host} if host else {}), **(headers or {})})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.headers, body


def no_cover():
    with open(serve.NO_COVER, 'rb') as f:
        return f.read()


def test_serve_stored_cover_without_token(server, covers):
    c, op = covers
    os.makedirs(c.folder)
    with open(c.path(H), 'wb') as f:
        f.write(JPEG)
    st, hd, body = get(server, '/cover/%s.jpg' % H.upper())
    assert st == 200 and body == JPEG
    assert hd['Content-Type'] == 'image/jpeg' and hd['Content-Length'] == str(len(JPEG))
    assert 'max-age=31536000' in hd['Cache-Control'] and 'no-store' not in hd['Cache-Control']   # ハッシュごとに中身は変わらない
    assert op.reqs == []


def test_serve_fetches_and_stores(server, covers, net):
    c, op = covers
    st, hd, body = get(server, '/cover/%s.jpg' % H, headers={'Sec-Fetch-Site': 'same-origin'})
    assert st == 200 and body == JPEG and hd['Content-Type'] == 'image/jpeg'
    assert op.urls == [MAIN] and c.cached(H) == (JPEG, 'image/jpeg')


def test_serve_fallback_when_not_an_image(server, covers, net):
    c, op = covers
    op.answers[MAIN] = b'<html>oops</html>'
    st, hd, body = get(server, '/cover/%s.jpg' % H)
    assert st == 200 and body == no_cover()
    assert hd['Content-Type'] == 'image/svg+xml' and hd['Content-Length'] == str(len(body))
    assert 'no-store' in hd['Cache-Control']                  # 代わりの絵は覚えさせない（後で取れたら出るように）
    assert c.cached(H) is None
    assert get(server, '/cover/%s.jpg' % H)[2] == body and len(op.reqs) == 1   # 失敗を覚えて、すぐには取り直さない


def test_serve_no_net_returns_fallback(server, covers):
    c, op = covers   # テスト全体の BLN_NO_NET=1 のまま
    st, hd, body = get(server, '/cover/%s.jpg' % H)
    assert st == 200 and body == no_cover() and op.reqs == []


@pytest.mark.parametrize('path', ['/cover/%s.jpg' % H[:-1], '/cover/%s.png' % H, '/cover/%s.jpg' % ('z' * 40), '/cover/%s' % H,
                                  '/cover/%s.jpg.jpg' % H, '/cover/%2e%2e.jpg'])
def test_serve_bad_name_is_404(server, covers, path):
    assert get(server, path)[0] == 404
    assert covers[1].reqs == []


def test_serve_checks_host_and_site(server, covers):
    c, op = covers
    path = '/cover/%s.jpg' % H
    assert get(server, path, host='evil.example')[0] == 403                          # DNS リバインディング
    assert get(server, path, headers={'Sec-Fetch-Site': 'cross-site'})[0] == 403     # ほかのサイトのページの <img>
    assert get(server, path, headers={'Sec-Fetch-Site': 'same-site'})[0] == 403
    assert get(server, path, headers={'Sec-Fetch-Site': 'none'})[0] == 200           # アドレス欄に入れた時
    assert op.reqs == []


def test_other_responses_stay_uncached(server, covers):
    # 曲のカバー画像の後に同じ接続で読んだ物には、長く覚えるヘッダーが残らない（keep-alive で Handler を使い回す）
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    os.makedirs(covers[0].folder)
    with open(covers[0].path(H), 'wb') as f:
        f.write(JPEG)
    c.request('GET', '/cover/%s.jpg' % H)
    r = c.getresponse()
    r.read()
    assert 'max-age' in r.headers['Cache-Control']
    c.request('GET', '/index.html')
    r = c.getresponse()
    r.read()
    assert r.headers['Cache-Control'].startswith('no-cache')
    c.close()
