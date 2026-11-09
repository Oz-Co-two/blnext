# serve.py の守り（Host・独自ヘッダー・合言葉・パスの制限）と API の基本の動き
import http.client, http.server, json, runpy, socket, sys, threading, time, types
import pytest
import serve


@pytest.fixture(scope='module')
def server():
    httpd = serve.make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, body=None, token=True, write_header=True, host=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    h = {}
    if token:
        h['X-BLN-Token'] = serve.SESSION_TOKEN
    if write_header and method == 'POST':
        h['X-BLN-Request'] = '1'
    if host:
        h['Host'] = host
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h['Content-Type'] = 'application/json'
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, out


def api(port, name, body=None):
    st, out = req(port, 'POST', '/__api/' + name, body or {})
    assert st == 200
    return json.loads(out)


def test_public_files_without_token(server):
    assert req(server, 'GET', '/index.html', token=False)[0] == 200
    assert req(server, 'GET', '/js/boot.js', token=False)[0] == 200


def test_private_files_need_token(server):
    assert req(server, 'GET', '/lang/en.json', token=False)[0] == 403
    assert req(server, 'GET', '/lang/en.json')[0] == 200


def test_post_needs_token_and_header(server):
    assert req(server, 'POST', '/__api/status', {}, token=False)[0] == 403
    assert req(server, 'POST', '/__api/status', {}, write_header=False)[0] == 403
    assert req(server, 'POST', '/__api/status', {})[0] == 200


def test_foreign_host_is_denied(server):
    assert req(server, 'GET', '/index.html', token=False, host='evil.example')[0] == 403


@pytest.mark.parametrize('path', ['/js/..%5C..%5Cserve.py', '/C:%5CWindows%5Cwin.ini', '/../serve.py', '/js/'])
def test_no_escape_from_web_dir(server, path):
    assert req(server, 'GET', path)[0] == 404


def test_unknown_api(server):
    st, out = req(server, 'POST', '/__api/nope', {})
    assert st == 404 and json.loads(out)['error'] == 'unknown-endpoint'


def test_bad_json(server):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    c.request('POST', '/__api/status', body=b'{oops', headers={'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'})
    r = c.getresponse()
    assert json.loads(r.read())['error'] == 'bad-request'


def test_home_without_data(server):
    r = api(server, 'home')
    assert r['ok'] and r['hasData'] is False


def test_refresh_progress(server):
    r = api(server, 'refresh/progress')
    assert r['ok'] and r['progress']['running'] is False


def test_search_needs_name(server):
    assert api(server, 'players/search', {'name': 'a'})['error'] == 'bad-request'
    assert api(server, 'players/search', {'name': 123})['error'] == 'bad-request'


def test_settings_roundtrip(server):
    r = api(server, 'settings/get')
    assert r['ok'] and r['values']['playlist_size'] == 15 and r['spec']['beat_saber_dir']['dialogOnly']
    r = api(server, 'settings/save', {'changes': {'playlist_size': 11}})
    assert r['ok'] and r['values']['playlist_size'] == 11
    r = api(server, 'settings/save', {'changes': {'beat_saber_dir': 'C:\\x'}})
    assert not r['ok'] and r['error'] == 'not-allowed'
    r = api(server, 'settings/reset', {'keys': ['playlist_size']})
    assert r['ok'] and r['values']['playlist_size'] == 15


# ---- 守りの細かい所 ----

def raw(port, head, close_write=True):
    """http.client では作れない要求（Host 無し・本体の長さの嘘など）を送り、(状態, 本体, かかった秒) を返す"""
    t0 = time.monotonic()
    s = socket.create_connection(('127.0.0.1', port), timeout=15)
    s.sendall(head.encode('latin-1'))
    if close_write:
        s.shutdown(socket.SHUT_WR)
    data = b''
    while True:
        d = s.recv(65536)
        if not d:
            break
        data += d
    s.close()
    return int(data.split(b' ', 2)[1]), data.split(b'\r\n\r\n', 1)[1], time.monotonic() - t0


def post_head(path='/__api/status', token=True, extra=''):
    h = 'POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-BLN-Request: 1\r\n' % path
    if token:
        h += 'X-BLN-Token: %s\r\n' % serve.SESSION_TOKEN
    return h + extra + '\r\n'


def test_access_url_carries_token():
    # 合言葉は # の後ろ（サーバーにも Referer にも送られない）
    assert serve.access_url(8139) == 'http://127.0.0.1:8139/index.html#k=' + serve.SESSION_TOKEN
    assert len(serve.SESSION_TOKEN) >= 32


@pytest.mark.parametrize('hostport, name', [('127.0.0.1:8139', '127.0.0.1'), ('[::1]:8139', '::1'), ('LocalHost', 'localhost'),
                                            ('::1', '::1'), ('', ''), (None, ''), ('evil.example:127.0.0.1', 'evil.example')])
def test_hostname(hostport, name):
    assert serve._hostname(hostport) == name


@pytest.mark.parametrize('path, public', [
    ('/index.html', True), ('/INDEX.HTML', True), ('/index.html?x=1#y', True), ('/js/boot.js', True), ('/./css/a.css', True),
    ('/', False), ('', False), ('/js', False), ('/lang/en.json', False), ('/foo/index.html', False), ('/index.htm', False),
    # .. や %2f で公開のフォルダから外へ出ても、公開扱いにならない
    ('/js/../lang/en.json', False), ('/js/%2e%2e/lang/en.json', False), ('/js%2f..%2flang%2fen.json', False),
    ('/icons/../../serve.py', False),
])
def test_is_public(path, public):
    assert serve._is_public(path) is public


@pytest.mark.parametrize('host, status', [
    ('localhost', 200), ('LOCALHOST:8139', 200), ('[::1]:8139', 200),
    ('127.0.0.1.evil.example', 403), ('localhost.evil.example:80', 403), ('127.0.0.1:80:x', 403)])
def test_host_header(server, host, status):
    assert req(server, 'GET', '/index.html', token=False, host=host)[0] == status


def test_missing_host_is_denied(server):
    st, _, _ = raw(server, 'GET /index.html HTTP/1.0\r\n\r\n')
    assert st == 403


@pytest.mark.parametrize('origin, status', [(None, 200), ('null', 200), ('http://127.0.0.1:8139', 200),
                                            ('http://evil.example', 403), ('http://localhost.evil.example', 403)])
def test_origin_header(server, origin, status):
    # 合言葉があっても、他のサイトの Origin なら断る（念のための二重化）
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    h = {'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'}
    if origin:
        h['Origin'] = origin
    c.request('POST', '/__api/status', body=b'{}', headers=h)
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status == status


def test_wrong_or_misplaced_token(server):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    for h in ({'X-BLN-Token': 'x'}, {'X-BLN-Token': serve.SESSION_TOKEN[:-1]}, {'X-BLN-Token': serve.SESSION_TOKEN + 'x'}):
        c.request('GET', '/lang/en.json', headers=h)
        r = c.getresponse()
        r.read()
        assert r.status == 403
    c.close()
    # 合言葉は URL では受けない（ヘッダーだけ）
    assert req(server, 'GET', '/lang/en.json?k=' + serve.SESSION_TOKEN, token=False)[0] == 403


def test_post_header_must_be_exactly_1(server):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    for v in ('0', 'true', ''):
        c.request('POST', '/__api/status', body=b'{}', headers={'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': v})
        r = c.getresponse()
        r.read()
        assert r.status == 403
    c.close()


def test_post_to_static_file(server):
    # 公開の静的ファイルでも、POST は合言葉が要る。API 以外への POST は unknown-endpoint
    assert req(server, 'POST', '/index.html', {}, token=False)[0] == 403
    st, out = req(server, 'POST', '/index.html', {})
    assert st == 404 and json.loads(out) == {'ok': False, 'error': 'unknown-endpoint'}


def test_root_is_index_but_needs_token(server):
    # '/' は index.html を返すが、公開の名前と一致しないので合言葉が要る（返してよい物だけを列挙する）
    st, out = req(server, 'GET', '/')
    assert st == 200 and b'js/main.js' in out
    assert req(server, 'GET', '/', token=False)[0] == 403


def test_head(server):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    c.request('HEAD', '/index.html')
    r = c.getresponse()
    r.read()
    assert r.status == 200 and 'no-store' in r.getheader('Cache-Control')
    c.request('HEAD', '/lang/en.json')
    r = c.getresponse()
    r.read()
    assert r.status == 403
    c.close()


@pytest.mark.parametrize('path', ['/js/C:/Windows/win.ini', '/%5C%5Cserver%5Cshare%5Cx', '/js/boot.js::$DATA',
                                  '/js/%2e%2e/%2e%2e/serve.py'])
def test_no_escape_windows_paths(server, path):
    # ドライブ名・UNC・NTFS のストリーム名・%2e%2e。合言葉があっても web/ の外は返さない
    assert req(server, 'GET', path)[0] == 404


def test_api_query_is_ignored(server):
    st, out = req(server, 'POST', '/__api/status?x=1', {})
    assert st == 200 and json.loads(out)['ok']


# ---- 本体の読み方（長さの嘘・大きすぎ・壊れた中身） ----

@pytest.mark.parametrize('body', [b'[]', b'null', b'"x"', b'1', b'\xff\xfe{}'])
def test_body_must_be_json_object(server, body):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    c.request('POST', '/__api/status', body=body, headers={'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'})
    r = c.getresponse()
    assert (r.status, json.loads(r.read())) == (200, {'ok': False, 'error': 'bad-request'})
    c.close()


@pytest.mark.parametrize('cl', ['abc', '-5'])
def test_bad_content_length_is_empty_body(server, cl):
    st, out, _ = raw(server, post_head(extra='Content-Length: %s\r\n' % cl))
    assert st == 200 and json.loads(out)['ok']


def test_too_large_body_is_drained(server):
    # 上限（4MB）を超える本体は読まずに too-large。送られた分は読み捨ててから応答する（Windows で応答が届かなくならないように）
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=30)
    c.request('POST', '/__api/status', body=b' ' * (5 * 1024 * 1024),
              headers={'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'})
    r = c.getresponse()
    assert json.loads(r.read()) == {'ok': False, 'error': 'too-large'}
    c.close()


def test_huge_body_is_not_waited_for(server):
    # 読み捨ての上限（16MB）を超えると申告された本体は待たずに断る
    st, out, sec = raw(server, post_head(extra='Content-Length: %d\r\n' % (64 * 1024 * 1024)), close_write=False)
    assert json.loads(out) == {'ok': False, 'error': 'too-large'} and sec < 1.5


def test_keep_alive_reuses_one_connection(server):
    # 1本の接続で何回も要求できる（HTTP/1.1。画面を開くたびに約30本の接続を作って捨てていたのをやめた）
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    h = {'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'}
    for _ in range(3):
        c.request('POST', '/__api/status', body=b'{}', headers=h)
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read())['ok'] and r.getheader('Connection') != 'close'
    c.request('GET', '/index.html')
    r = c.getresponse()
    assert r.status == 200 and b'js/main.js' in r.read()
    c.close()


def test_unread_body_closes_connection(server):
    # 読みきれなかった本体（大きすぎる）の残りを、次の要求として読まないよう接続を閉じる
    s = socket.create_connection(('127.0.0.1', server), timeout=15)
    s.sendall(post_head(extra='Content-Length: %d\r\n' % (64 * 1024 * 1024)).encode('latin-1') + b'POST /__api/status HTTP/1.1\r\n\r\n')
    data = b''
    while True:
        d = s.recv(65536)
        if not d:
            break   # サーバーが閉じた
        data += d
    s.close()
    assert data.count(b'HTTP/1.') == 1 and b'Connection: close' in data and b'too-large' in data


def test_short_body_on_denied_request(server):
    # 申告より短い本体で送るのをやめた相手（途中で閉じた）にも、断りの応答を返す
    st, _, _ = raw(server, post_head(token=False, extra='Content-Length: 100\r\n') + 'abc')
    assert st == 403


def test_silent_client_gets_answer_after_timeout(server):
    # 本体を送らないまま待っている相手にも、待ち切り（2秒）のあとで断りを返す（いつまでも止まらない）
    st, _, sec = raw(server, post_head(token=False, extra='Content-Length: 100\r\n'), close_write=False)
    assert st == 403 and sec < 10


# ---- API の失敗時の応答（{ok:false, error:種類}） ----

def test_internal_error_is_reported(server, monkeypatch):
    def boom(body):
        raise RuntimeError('壊れた')
    monkeypatch.setitem(serve.API, 'test/boom', boom)
    st, out = req(server, 'POST', '/__api/test/boom', {})
    assert st == 500 and json.loads(out) == {'ok': False, 'error': 'internal', 'detail': 'RuntimeError: 壊れた'}


def test_settings_errors_name_the_key(server):
    assert api(server, 'settings/save', {'changes': {'playlist_size': 999}}) == {'ok': False, 'error': 'bad-value', 'key': 'playlist_size'}
    assert api(server, 'settings/save', {'changes': {'nope': 1}}) == {'ok': False, 'error': 'unknown-key', 'key': 'nope'}
    assert api(server, 'settings/save', {'changes': 'x'})['error'] == 'bad-request'
    # 1つでもだめなら何も書かない
    assert not api(server, 'settings/save', {'changes': {'playlist_size': 20, 'nope': 1}})['ok']
    assert api(server, 'settings/get')['values']['playlist_size'] == 15


@pytest.mark.parametrize('keys', ['playlist_size', ['nope'], None])
def test_settings_reset_bad(server, keys):
    assert api(server, 'settings/reset', {'keys': keys}) == {'ok': False, 'error': 'bad-request', 'key': ''}


def test_search_offline_and_trimmed(server, monkeypatch):
    # ネットに出られない時は offline。名前の前後の空白は落とす。空白を除いて2文字未満・41文字以上はだめ
    assert api(server, 'players/search', {'name': 'abc'})['error'] == 'offline'
    assert api(server, 'players/search', {'name': ' a '})['error'] == 'bad-request'
    assert api(server, 'players/search', {'name': 'x' * 41})['error'] == 'bad-request'
    got = []

    def fake(client, name):
        got.append(name)
        return [{'player_id': '1', 'name': '作り物', 'country': 'JP', 'pp': 1.5, 'rank': 9, 'country_rank': 2, 'avatar': None}]
    monkeypatch.setattr(serve.bl, 'search_players', fake)
    r = api(server, 'players/search', {'name': '  abc  '})
    assert got == ['abc']
    assert r['players'] == [{'id': '1', 'name': '作り物', 'country': 'JP', 'pp': 1.5, 'rank': 9, 'countryRank': 2, 'avatar': None}]


def test_refresh_cancel_when_idle(server):
    r = api(server, 'refresh/cancel')
    assert r['ok'] and r['progress']['running'] is False


def test_no_data_answers(server):
    # 取得前（データなし）: おすすめ・似ている人は hasData:false、出力・操作は失敗の種類を返す
    assert api(server, 'recommend') == {'ok': True, 'hasData': False, 'preset': 'p1'}
    assert api(server, 'recommend/rebuild') == {'ok': True, 'hasData': False, 'preset': 'p1'}
    assert api(server, 'recommend/replays') == {'ok': True, 'hasData': False, 'preset': 'p1'}
    assert api(server, 'similar') == {'ok': True, 'hasData': False}
    assert api(server, 'export') == {'ok': False, 'error': 'nothing-to-export'}
    assert api(server, 'export/file') == {'ok': False, 'error': 'nothing-to-export'}
    assert api(server, 'export/all')['results'] == [{'preset': 'p1', 'name': 'スタンダード', 'ok': False, 'error': 'nothing-to-export'}]
    assert api(server, 'similar/compare', {'id': '1'})['error'] == 'bad-request'
    for body in ({'action': 'pin', 'id': 'LB001'}, {'action': 'pin', 'id': 5}):
        assert api(server, 'recommend/action', body)['error'] == 'bad-request'
    assert api(server, 'recommend/action', {'action': 'reset'}) == {'ok': True, 'hasData': False, 'preset': 'p1'}   # reset は曲を指さない
    assert api(server, 'recommend', {'preset': 'p9'})['error'] == 'no-preset'
    assert api(server, 'recommend', {'preset': 5}) == {'ok': False, 'error': 'bad-request'}


def test_bs_use_when_folder_vanished(tmp_path):
    # 候補を出した後で消えた場所は、番号で選んでも設定に入れない
    before = serve.SETTINGS.get('beat_saber_dir')
    cands = serve.bs_offer([str(tmp_path / 'gone')], 'picked')
    assert cands[0]['id'] == 0 and cands[0]['source'] == 'picked'
    with pytest.raises(serve.ApiError) as e:
        serve.API['bs/use']({'id': 0})
    assert e.value.code == 'not-beat-saber' and serve.SETTINGS.get('beat_saber_dir') == before


def test_on_closing_cancels_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(serve, 'REFRESHER', types.SimpleNamespace(cancel=lambda: calls.append(1)))
    serve.on_closing()
    assert calls == [1]


# ---- python serve.py（単体で起動） ----

class _FakeHTTPD:
    """serve_forever で Ctrl+C を押したことにする"""
    made = []

    def __init__(self, addr, handler):
        self.addr, self.closed = addr, False
        self.server_address = addr
        _FakeHTTPD.made.append(self)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


def _run_script(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['serve.py', *args])
    ns = runpy.run_path(serve.__file__, run_name='__main__')
    ns['STORE'].close()
    return ns


def test_run_as_script(monkeypatch, capsys):
    from core import launch
    opened = []
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', _FakeHTTPD)
    monkeypatch.setattr(launch, 'start', opened.append)
    _FakeHTTPD.made.clear()
    ns = _run_script(monkeypatch, '--port=8200')
    out = capsys.readouterr().out
    assert out.isascii()   # 日本語を出さない（表せない文字コードの環境で起動前に落ちないように）
    url = next(l.strip() for l in out.splitlines() if l.strip().startswith('http'))
    assert url == ns['access_url'](8200) and opened == [url]
    assert _FakeHTTPD.made[0].addr == ('127.0.0.1', 8200) and _FakeHTTPD.made[0].closed
    _run_script(monkeypatch, '--no-browser')
    assert len(opened) == 1 and _FakeHTTPD.made[1].addr == ('127.0.0.1', 8139)

    def no_browser(url):
        raise OSError('ブラウザが無い')
    monkeypatch.setattr(launch, 'start', no_browser)
    _run_script(monkeypatch)   # ブラウザを開けなくても起動は続ける
    assert _FakeHTTPD.made[2].closed


def test_refresh_without_player(server):
    # 取得を始めても、プレイヤーが未設定なら no-player で止まる（画面へ失敗の種類が届く）
    r = api(server, 'refresh/start')
    assert r['ok']
    t0 = time.monotonic()
    while r['progress']['running'] and time.monotonic() - t0 < 10:
        time.sleep(0.05)
        r = api(server, 'refresh/progress')
    assert r['progress']['running'] is False and r['progress']['error'] == 'no-player'
