# serve.py の API のうち、データがある時の動き（おすすめの操作・出力・似ている人）。
# データは作り物（tests/_lib/fixture_data.py。ネットに出ない）。serve の STORE・SETTINGS などをテストごとの複製へ向け直す
import http.client, json, os, shutil, threading, time

import pytest

import fixture_data
import serve
from core.estimate.engine import Engine
from core.fetch import replay
from core.fetch.http import NetError
from core.settings import Settings
from core.store.db import Store


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp('built')
    fixture_data.build(str(d))
    return d


@pytest.fixture(scope='module')
def server():
    httpd = serve.make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def data(built, tmp_path, monkeypatch):
    """作り物のデータの複製を serve に使わせる（テストごとに元の状態から）"""
    d = tmp_path / 'data'
    shutil.copytree(built, d)
    s = Settings(str(d / 'config'))
    s.update({'beat_saber_dir': str(d / 'FakeBeatSaber')}, trusted=True)
    st = Store(str(d / 'data' / 'blnext.db'))
    monkeypatch.setattr(serve, 'SETTINGS', s)
    monkeypatch.setattr(serve, 'STORE', st)
    monkeypatch.setattr(serve, 'ENGINE', Engine(st, s))
    monkeypatch.setattr(serve, 'SESSION', {})
    monkeypatch.setattr(serve, 'DATA_DIR', str(d))
    # 今のデータのリストを保存しておく（保存したリストが無いと、前回の出力の曲で作る＝_seed_list。それは専用のテストで見る）
    serve._api_recommend_rebuild({})
    yield d
    st.close()


def call(port, name, body=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    c.request('POST', '/__api/' + name, body=json.dumps(body or {}).encode(),
              headers={'X-BLN-Token': serve.SESSION_TOKEN, 'X-BLN-Request': '1'})
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    assert r.status == 200
    return out


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def ids(rows):
    return [c['leaderboard_id'] for c in rows]


def act(port, action, lid, **extra):
    return call(port, 'recommend/action', {'action': action, 'id': lid, **extra})


def marks():
    return {(r['leaderboard_id'], r['mark']) for r in serve.STORE.q('SELECT leaderboard_id, mark FROM user_marks')}


# ---- おすすめと、その操作 ----

def test_recommend_with_data(server, data):
    r = call(server, 'recommend')
    assert r['ok'] and r['hasData'] and len(r['items']) == 15 and r['runnersUp']
    assert r['similarCount'] > 0 and r['removed'] == 0 and r['neverCount'] == 0
    # 画面へ渡す項目だけ（内部の値＝hash・score_id・replay_url を出さない）。プレイ済みの曲には JD・RT、前回の出力があれば
    # 入っていたかを足す
    exp = {'in_export'} if r['lastExport'] else set()
    for c in r['items']:
        extra = {'jd', 'rt', 'jd_state'} if c['kind'] == 'played' else set()
        assert set(c) == set(serve._ITEM_KEYS) | extra | exp
    assert all(set(c) - set(serve._ITEM_KEYS) <= {'jd', 'rt', 'jd_state'} for c in r['runnersUp'])


def test_recommend_map_features_and_range(server, data):
    # 一覧に出す譜面の特徴と、あなたがクリアしてきた曲の範囲（それを超える値に色を付ける）
    r = call(server, 'recommend')
    c = r['items'][0]
    assert c['njs'] and c['bpm'] and c['nps'] and c['notes'] == 500 and c['pass_rating'] and c['tags']
    assert set(r['range']) == {'pass', 'tech', 'acc', 'nps', 'njs'}
    assert all(v['p95'] <= v['max'] for v in r['range'].values())
    played = [x for x in r['items'] + r['runnersUp'] if x['kind'] == 'played']
    assert played and all(x['replay'] for x in played)                       # 自分のスコアの ID がある
    assert not any(x['replay'] for x in r['items'] + r['runnersUp'] if x['kind'] == 'unplayed')


def test_recommend_tried_unplayed(server, data):
    # 自己ベストは無いが挑戦の記録がある曲（作り物のデータの1曲。失敗・やり直し・途中でやめた・練習）→ 挑戦の記録を画面に渡す
    # （練習は数えない。最後は 78.1% で失敗）。推定の区分は未プレイのまま。保存するリストにも入る（作った時のまま）
    r = call(server, 'recommend')
    tried = [c for c in r['items'] if c.get('tried')]
    assert len(tried) == 1
    c = tried[0]
    assert c['kind'] == 'unplayed' and c['best_acc'] is None
    assert {k: c['tried'][k] for k in ('n', 'clears', 'end')} == {'n': 3, 'clears': 0, 'end': 2} and abs(c['tried']['acc'] - 0.781) < 1e-9
    assert c['fails'] is None       # 作り物の挑戦の記録は少ないので、クリアの見込みを求めない（下げることもない）
    assert all(x['tried'] is None for x in r['items'] + r['runnersUp'] if x['kind'] == 'played')
    saved = json.loads(serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])
    assert next(x for x in saved['items'] if x['leaderboard_id'] == c['leaderboard_id'])['tried'] == c['tried']


def test_open_beatleader_pages(server, data, monkeypatch):
    # 画面からは譜面の ID だけ。URL は Python が組み立てる（任意のページを開く口にしない）
    opened = []
    monkeypatch.setattr(serve, 'open_browser', opened.append)
    r = call(server, 'recommend')
    played = next(x for x in r['items'] + r['runnersUp'] if x['kind'] == 'played')
    unplayed = next(x for x in r['items'] + r['runnersUp'] if x['kind'] == 'unplayed')
    sid = serve.STORE.q1('SELECT score_id FROM scores WHERE player_id=? AND leaderboard_id=?',
                         (serve.SETTINGS.values()['player_id'], played['leaderboard_id']))['score_id']
    assert call(server, 'open/beatleader', {'id': played['leaderboard_id'], 'what': 'replay'})['ok']
    assert call(server, 'open/beatleader', {'id': unplayed['leaderboard_id'], 'what': 'leaderboard'})['ok']
    assert opened == ['https://replay.beatleader.com/?scoreId=%d' % sid,
                      'https://beatleader.com/leaderboard/global/%s/1' % unplayed['leaderboard_id']]
    # 遊んでいない曲のリプレイ・知らない譜面・知らない種類・URL を渡す、はどれも開かない
    assert call(server, 'open/beatleader', {'id': unplayed['leaderboard_id'], 'what': 'replay'})['error'] == 'no-replay'
    for body in ({'id': 'nope', 'what': 'leaderboard'}, {'id': played['leaderboard_id'], 'what': 'url'},
                 {'id': ['x'], 'what': 'replay'}, {'url': 'https://example.com/', 'what': 'leaderboard'}):
        assert call(server, 'open/beatleader', body)['error'] == 'bad-request'
    assert len(opened) == 2

    def fail(url):
        raise OSError('no browser')
    monkeypatch.setattr(serve, 'open_browser', fail)
    assert call(server, 'open/beatleader', {'id': unplayed['leaderboard_id'], 'what': 'leaderboard'})['error'] == 'open-failed'


def test_pin_and_unpin(server, data):
    lid = call(server, 'recommend')['items'][3]['leaderboard_id']
    r = act(server, 'pin', lid)
    assert next(c for c in r['items'] if c['leaderboard_id'] == lid)['pinned'] and marks() == {(lid, 'pin')}
    r = act(server, 'unpin', lid)
    assert not any(c['pinned'] for c in r['items']) and marks() == set()


def test_never_and_unnever(server, data):
    lid = call(server, 'recommend')['items'][0]['leaderboard_id']
    r = act(server, 'never', lid)
    assert lid not in ids(r['items']) + ids(r['runnersUp']) and r['neverCount'] == 1 and marks() == {(lid, 'never')}
    r = act(server, 'unnever', lid)
    assert lid in ids(r['items']) and r['neverCount'] == 0 and marks() == set()


def test_remove_drops_pin_and_reset_restores(server, data):
    first = call(server, 'recommend')
    lid = first['items'][0]['leaderboard_id']
    act(server, 'pin', lid)
    r = act(server, 'remove', lid)
    assert lid not in ids(r['items']) and r['removed'] == 1 and marks() == set()   # 固定も外れる
    r = act(server, 'reset', r['items'][0]['leaderboard_id'])
    assert ids(r['items']) == ids(first['items']) and r['removed'] == 0


def test_swap_with_runner_up(server, data):
    first = call(server, 'recommend')
    lid, other = first['items'][0]['leaderboard_id'], first['runnersUp'][0]['leaderboard_id']
    r = act(server, 'swap', lid, **{'with': other})
    assert other in ids(r['items']) and lid not in ids(r['items'])
    # 入れ替えた曲を、また入れ替えで戻せる
    r = act(server, 'swap', other, **{'with': lid})
    assert lid in ids(r['items']) and other not in ids(r['items'])


def test_action_bad_requests_change_nothing(server, data):
    lid = call(server, 'recommend')['items'][0]['leaderboard_id']
    for body in ({'action': 'swap', 'id': lid, 'with': 'LB999'}, {'action': 'swap', 'id': lid},
                 {'action': 'delete', 'id': lid}, {'action': 'pin', 'id': 'LB999'}, {'action': 'pin', 'id': ['LB001']},
                 {'action': 'pin'}):
        assert call(server, 'recommend/action', body) == {'ok': False, 'error': 'bad-request'}
    assert all(not x['removed'] and not x['added'] for x in serve.SESSION.values()) and marks() == set()


def test_reset_after_removing_everything(server, data):
    # 全部外して items も runnersUp も空になっても、「外した曲を戻す」で戻せる（以前は reset にも曲の id を求めて断っていた）
    r = call(server, 'recommend')
    for _ in range(500):
        if not r['items']:
            break
        r = act(server, 'remove', r['items'][0]['leaderboard_id'])
    assert r['items'] == [] and r['runnersUp'] == [] and r['removed'] > 0
    # 画面（web/js/screens/recommend.js）は items[0] も runnersUp[0] も無い時、id を付けずに送る
    r = call(server, 'recommend/action', {'action': 'reset'})
    assert r['ok'] and r['removed'] == 0


# ---- 出力 ----

def test_export_writes_playlist(server, data):
    before = serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n']
    rec = call(server, 'recommend')
    r = call(server, 'export')
    assert r['ok'] and r['path'] == str(data / 'FakeBeatSaber' / 'Playlists' / 'blnext-next.bplist')
    pl = read_json(r['path'])
    assert pl['playlistTitle'] == '次回おすすめ' and len(pl['songs']) == r['songs'] > 0 and r['items'] == len(rec['items'])
    assert pl['image'].startswith('data:image/png;base64,')
    assert os.path.dirname(r['copy']) == str(data / 'exports') and os.path.isfile(r['copy'])   # 控え
    # 出力の記録（次回の振り返り・クールダウンに使う）
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n'] == before + 1
    run = serve.STORE.q1('SELECT MAX(run_id) AS id FROM exports')['id']
    got = {x['leaderboard_id'] for x in serve.STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1', (run,))}
    assert got == set(ids(rec['items']))
    # 下限が 0（自動）のセットは、その時に決めた下限も設定の記録に残す（振り返りの条件の行に出す）
    saved = json.loads(serve.STORE.q1('SELECT settings FROM recommendation_runs WHERE run_id=?', (run,))['settings'])
    assert saved['star_min'] == 0 and saved['star_min_auto'] == rec['filters']['starMinAuto'] is not None


def test_export_without_icon(server, data, tmp_path, monkeypatch):
    # アイコンの画像が読めなくても、画像なしで出力する
    monkeypatch.setattr(serve, 'WEB_DIR', str(tmp_path / 'noweb'))
    r = call(server, 'export')
    assert r['ok'] and 'image' not in read_json(r['path'])


def test_export_errors_leave_no_record(server, data):
    before = serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n']
    serve.SETTINGS.reset(['beat_saber_dir'])
    assert call(server, 'export') == {'ok': False, 'error': 'no-beat-saber-dir', 'detail': ''}
    gone = str(data / 'gone')
    serve.SETTINGS.update({'beat_saber_dir': gone}, trusted=True)
    assert call(server, 'export') == {'ok': False, 'error': 'beat-saber-dir-missing', 'detail': gone}
    # Playlists がファイルになっていて書けない
    bad = data / 'BadBeatSaber'
    bad.mkdir()
    (bad / 'Playlists').write_text('', encoding='utf-8')
    serve.SETTINGS.update({'beat_saber_dir': str(bad)}, trusted=True)
    r = call(server, 'export')
    assert r['error'] == 'write-failed' and r['detail']
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n'] == before


# ---- セット（設定のパターン）ごとのおすすめと出力 ----

def test_presets_have_their_own_list_and_pins(server, data):
    r = call(server, 'presets/add', {'name': 'ワンセイバー', 'copyFrom': 'p1'})
    assert r['ok'] and r['id'] == 'p2' and [p['name'] for p in r['presets']] == ['スタンダード', 'ワンセイバー']
    assert call(server, 'status')['activePreset'] == 'p2'           # 足したセットを開く
    one = call(server, 'recommend', {'preset': 'p1'})
    assert ids(call(server, 'recommend', {'preset': 'p2'})['items']) == ids(one['items'])   # 写したので同じ
    # 条件はそのセットだけに効く
    call(server, 'settings/save', {'changes': {'playlist_size': 5}, 'preset': 'p2'})
    assert len(call(server, 'recommend', {'preset': 'p2'})['items']) == 5 and len(call(server, 'recommend', {'preset': 'p1'})['items']) == 15
    call(server, 'settings/save', {'changes': {'modes': ['NoArrows']}, 'preset': 'p2'})
    assert call(server, 'recommend', {'preset': 'p2'})['items'] == []   # 作り物の譜面は Standard だけ
    # 固定はそのセットだけ。今後出さないは全セット
    low = one['runnersUp'][0]['leaderboard_id']
    r = call(server, 'recommend/action', {'action': 'pin', 'id': low, 'preset': 'p1'})
    assert low in ids(r['items']) and low not in ids(call(server, 'recommend', {'preset': 'p2'})['items'])
    assert serve.STORE.q1("SELECT preset FROM user_marks WHERE leaderboard_id=?", (low,))['preset'] == 'p1'
    call(server, 'settings/save', {'changes': {'modes': ['Standard']}, 'preset': 'p2'})
    top = one['items'][0]['leaderboard_id']
    before = ids(call(server, 'recommend', {'preset': 'p1'})['items'])
    call(server, 'recommend/action', {'action': 'never', 'id': top, 'preset': 'p2'})
    # p1 の保存したリストからもその曲だけすぐ抜く（全部のセットを書き込んだ時に入らないように）。ほかの曲は作った時のままで、
    # 代わりに入る曲は「作り直す」の知らせで出す
    r = call(server, 'recommend', {'preset': 'p1'})
    assert ids(r['items']) == [x for x in before if x != top] and r['pending'] and not r['pending']['out']
    assert len(r['pending']['in']) == 1
    assert top not in ids(call(server, 'recommend/rebuild', {'preset': 'p1'})['items'])
    # 外した曲もそのセットだけ
    second = one['items'][1]['leaderboard_id']
    r = call(server, 'recommend/action', {'action': 'remove', 'id': second, 'preset': 'p2'})
    assert second not in ids(r['items']) and r['removed'] == 1
    assert second in ids(call(server, 'recommend', {'preset': 'p1'})['items'])
    # セットを消すと、そのセットの固定と外した曲も消える
    call(server, 'recommend/action', {'action': 'pin', 'id': low, 'preset': 'p2'})
    r = call(server, 'presets/delete', {'id': 'p2'})
    assert r['ok'] and r['activePreset'] == 'p1' and 'p2' not in serve.SESSION
    assert marks() == {(low, 'pin'), (top, 'never')}
    assert [x['preset'] for x in serve.STORE.q('SELECT preset FROM rec_lists')] == ['p1']   # 保存したリストも消える


def test_presets_api_errors(server, data):
    assert call(server, 'presets/add', {'name': ''})['error'] == 'bad-name'
    assert call(server, 'presets/add', {'name': 'x', 'copyFrom': 'p9'})['error'] == 'no-preset'
    assert call(server, 'presets/rename', {'id': 'p1', 'name': 'x' * 31})['error'] == 'bad-name'
    assert call(server, 'presets/delete', {'id': 'p1'})['error'] == 'last-preset'
    assert call(server, 'presets/open', {'id': 'p9'})['error'] == 'no-preset'
    assert call(server, 'presets/open', {'id': ['p1']})['error'] == 'bad-request'
    assert call(server, 'presets/rename', {'id': 'p1', 'name': '標準'})['presets'][0]['name'] == '標準'
    g = call(server, 'settings/get')
    assert g['preset'] == 'p1' and g['presets'][0]['values']['playlist_size'] == 15 and g['spec']['playlist_size']['scope'] == 'preset'
    assert g['spec']['player_id']['scope'] == 'common'


def test_presets_order(server, data):
    """セットの並び（おすすめのタブ）を変える。今あるセットのちょうどの並べ替えでなければ bad-request（何も変えない）。
    全部のセットを書き込む順・ホームの振り返りのタブの順もこの並び。組のセットを交互に選ぶ順（難しさの順）は変わらない"""
    p2 = call(server, 'presets/add', {'name': 'B', 'copyFrom': 'p1'})['id']
    p3 = call(server, 'presets/add', {'name': 'C', 'copyFrom': 'p1'})['id']
    r = call(server, 'presets/order', {'ids': [p3, 'p1', p2]})
    assert r['ok'] and [p['id'] for p in r['presets']] == [p3, 'p1', p2]
    assert [p['id'] for p in call(server, 'status')['presets']] == [p3, 'p1', p2]
    for bad in ({'ids': [p3, 'p1']}, {'ids': [p3, 'p1', p2, p2]}, {'ids': [p3, 'p1', 'p9']}, {'ids': 'p1'}, {'ids': [p3, 'p1', 7]}, {}):
        assert call(server, 'presets/order', bad)['error'] == 'bad-request', bad
    assert serve.SETTINGS.preset_ids() == [p3, 'p1', p2]
    assert [x['preset'] for x in call(server, 'export/all')['results']] == [p3, 'p1', p2]
    runs = call(server, 'home')['review']['runs']
    assert [(x['preset'], x['presetName']) for x in runs] == [(p3, 'C'), ('p1', 'スタンダード'), (p2, 'B')]
    # 難しさ違いのセット（組）を作って並びを逆にしても、交互に選ぶ順は 着実 → チャレンジング のまま
    ds = call(server, 'presets/difficulty-set', {'copyFrom': 'p1'})
    steady, challenge = ds['ids']
    assert ds['group'] == [steady, challenge]
    ids = serve.SETTINGS.preset_ids()
    call(server, 'presets/order', {'ids': ids[::-1]})
    assert serve.SETTINGS.preset_ids()[:2] == [challenge, steady] and call(server, 'status')['group'] == [steady, challenge]


def test_export_all_writes_each_preset(server, data):
    call(server, 'presets/add', {'name': 'B', 'copyFrom': 'p1'})
    r = call(server, 'export/all')
    assert [x['ok'] for x in r['results']] == [True, True]
    pl = data / 'FakeBeatSaber' / 'Playlists'
    a, b = read_json(pl / 'blnext-next.bplist'), read_json(pl / 'blnext-p2.bplist')
    assert a['playlistTitle'] == '次回おすすめ' and b['playlistTitle'] == '次回おすすめ（B）'
    assert a['customData']['syncURL'] != b['customData']['syncURL']   # BSManager が別のプレイリストとして扱う印
    runs = serve.STORE.q("SELECT r.preset, e.method FROM exports e JOIN recommendation_runs r USING(run_id) ORDER BY e.rowid DESC LIMIT 2")
    assert sorted(x['preset'] for x in runs) == ['p1', 'p2'] and {x['method'] for x in runs} == {'write'}


def test_same_list_twice_is_one_run(server, data):
    # 書き込んだ後にファイルにも保存した等（間に取得していない・同じ曲）は、振り返り・クールダウンで1回に数える
    before = serve.STORE.q1('SELECT COUNT(*) AS n FROM recommendation_runs')['n']
    call(server, 'export')
    r = call(server, 'export/file')
    assert r['ok'] and r['filename'] == 'blnext-next.bplist' and json.loads(r['text'])['songs']
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM recommendation_runs')['n'] == before + 1
    run = serve.STORE.q1('SELECT MAX(run_id) AS id FROM recommendation_runs')['id']
    assert [x['method'] for x in serve.STORE.q('SELECT method FROM exports WHERE run_id=? ORDER BY rowid', (run,))] == ['write', 'file']
    # 曲が変わればもう1回
    call(server, 'settings/save', {'changes': {'playlist_size': 4}})
    call(server, 'export')
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM recommendation_runs')['n'] == before + 2


def test_export_oneclick(server, data, monkeypatch):
    launched = []
    monkeypatch.setattr(serve.oneclick, 'handler_app', lambda: None)
    assert call(server, 'oneclick/info') == {'ok': True, 'app': None}
    assert call(server, 'export/oneclick')['error'] == 'no-oneclick-app'   # BSManager などが登録されていない
    monkeypatch.setattr(serve.oneclick, 'handler_app', lambda: 'BSManager')
    monkeypatch.setattr(serve.oneclick, 'launch', launched.append)
    monkeypatch.setitem(serve.SERVER, 'port', server)
    r = call(server, 'export/oneclick')
    assert r['ok'] and r['app'] == 'BSManager' and r['items'] == 15
    url = launched[0]
    prefix = 'bsplaylist://playlist/http://127.0.0.1:%d/oneclick/' % server
    assert url.startswith(prefix) and url.endswith('/blnext-next.bplist')
    # BSManager が読みに来る（合言葉のヘッダーは付かない）
    path = '/' + url.split('/', 6)[6]   # bsplaylist: / / playlist / http: / / 127.0.0.1:port / oneclick/…
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
    c.request('GET', path)
    res = c.getresponse()
    body = res.read()
    assert res.status == 200 and res.getheader('Content-Disposition') == 'attachment; filename="blnext-next.bplist"'
    pl = json.loads(body)
    assert pl['customData']['syncURL'] == 'blnext://playlist/blnext-next.bplist' and len(pl['songs']) > 0
    # ファイル名が違う・合言葉が違う・他所の Host は返さない
    for p, host in ((path.rsplit('/', 1)[0] + '/other.bplist', None), ('/oneclick/nope/blnext-next.bplist', None), (path, 'evil.example')):
        c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
        c.request('GET', p, headers={'Host': host} if host else {})
        assert c.getresponse().status in (403, 404)
    assert serve.STORE.q1('SELECT method FROM exports ORDER BY rowid DESC LIMIT 1')['method'] == 'oneclick'
    le = call(server, 'recommend')['lastExport']   # おすすめ画面の「前回の出力」に、入れたアプリの名前を出す
    assert le['method'] == 'oneclick' and le['app'] == 'BSManager' and le['n'] == 15 and le['dropped'] == []
    # 起動に失敗した時は記録しない
    def fail(url):
        raise OSError('no app')
    monkeypatch.setattr(serve.oneclick, 'launch', fail)
    n = serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n']
    assert call(server, 'export/oneclick')['error'] == 'launch-failed'
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n'] == n


def test_export_oneclick_all(server, data, monkeypatch):
    # 全部のセットを1つずつ渡す。アプリ（BSManager）が前の分を読みに来てから次を渡す（起動しきる前に渡すと取りこぼすおそれ）
    call(server, 'presets/add', {'name': 'B', 'copyFrom': 'p1'})
    monkeypatch.setattr(serve.oneclick, 'handler_app', lambda: None)
    assert call(server, 'export/oneclick/all')['error'] == 'no-oneclick-app'
    monkeypatch.setattr(serve.oneclick, 'handler_app', lambda: 'BSManager')
    monkeypatch.setitem(serve.SERVER, 'port', server)
    got = []

    def app_reads(url):   # BSManager のふり: リンクを受けるとその場で読みに来る
        c = http.client.HTTPConnection('127.0.0.1', server, timeout=10)
        c.request('GET', '/' + url.split('/', 6)[6])
        got.append(json.loads(c.getresponse().read())['playlistTitle'])
    monkeypatch.setattr(serve.oneclick, 'launch', app_reads)
    waits = []
    wait = serve.SHELF.wait_fetched
    monkeypatch.setattr(serve.SHELF, 'wait_fetched', lambda tok, timeout: waits.append(timeout) or wait(tok, timeout))
    r = call(server, 'export/oneclick/all')
    assert r['app'] == 'BSManager' and [(x['preset'], x['ok']) for x in r['results']] == [('p1', True), ('p2', True)]
    assert got == ['次回おすすめ', '次回おすすめ（B）'] and len(waits) == 1   # 待つのは2つ目を渡す前だけ
    runs = serve.STORE.q("SELECT r.preset, e.method, e.path FROM exports e JOIN recommendation_runs r USING(run_id) ORDER BY e.rowid DESC LIMIT 2")
    assert sorted(x['preset'] for x in runs) == ['p1', 'p2'] and {x['method'] for x in runs} == {'oneclick'}
    assert {x['path'] for x in runs} == {'BSManager: blnext-next.bplist', 'BSManager: blnext-p2.bplist'}
    # 読みに来ないアプリ: 1回だけ待って、その後は待たずに渡す
    call(server, 'presets/add', {'name': 'C', 'copyFrom': 'p1'})
    launched, waits[:] = [], []
    monkeypatch.setattr(serve.oneclick, 'launch', launched.append)
    monkeypatch.setattr(serve, 'ONECLICK_WAIT', 0.2)
    r = call(server, 'export/oneclick/all')
    assert len(launched) == 3 and waits == [0.2] and all(x['ok'] for x in r['results'])


def test_cover_set_get_clear(server, data):
    import base64
    g = call(server, 'cover/get')
    assert g['dataUrl'].startswith('data:image/png;base64,') and not g['custom']   # 選んでいなければアプリのアイコン
    jpeg = b'\xff\xd8\xff\xe0' + b'0' * 100
    r = call(server, 'cover/set', {'data': base64.b64encode(jpeg).decode()})
    assert r['ok'] and r['custom'] and r['dataUrl'] == 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()
    assert read_json(call(server, 'export')['path'])['image'].startswith('data:image/jpeg;base64,')   # 出力に入る
    for bad in ('not base64!', base64.b64encode(b'GIF89a....').decode(), 5, base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * (3 * 1024 * 1024)).decode()):
        assert call(server, 'cover/set', {'data': bad})['error'] in ('bad-image', 'too-large')
    r = call(server, 'cover/clear')
    assert r['ok'] and not r['custom']


def test_auto_refresh_decision(data, monkeypatch):
    v = serve.SETTINGS.values()
    now = int(serve.STORE.meta('last_run_at'))
    monkeypatch.delenv('BLN_NO_NET', raising=False)
    assert not serve.should_auto_refresh(v, now + 11 * 3600)        # 12時間たっていなければ取らない
    assert serve.should_auto_refresh(v, now + 12 * 3600)
    assert serve.should_auto_refresh({**v, 'auto_refresh_hours': 0}, now)   # 0 なら毎回
    serve.STORE.set_meta('last_run_player', 'someone-else')           # 別のプレイヤーのデータなら取る
    assert serve.should_auto_refresh(v, now + 60)
    assert not serve.should_auto_refresh({**v, 'player_id': ''}, now + 99 * 3600)   # プレイヤー未設定
    monkeypatch.setenv('BLN_NO_NET', '1')
    assert not serve.should_auto_refresh(v, now + 99 * 3600)          # ネットに出ない設定（テスト）


# ---- 保存したリスト（作った時のまま残す。データを取り直しても勝手には変えない） ----
# 前に作ったリストの理由を次のプレイまでに見直せるように、作り直すかは利用者が決める。作り直すのは、まだ無い時・
# リストに効く設定を変えた時・画面の操作の時・「作り直す」の時だけ

def sql(stmt, args=()):
    with serve.STORE.tx():
        serve.STORE.conn.execute(stmt, args)


def fresh(port):
    """保存したリストを今のデータで作り直した所から始める（無い時に前回の出力の曲で作るのは test_first_list_is_last_export）"""
    sql('DELETE FROM rec_lists')
    return call(port, 'recommend/rebuild')


def test_first_list_is_last_export(server, data):
    # 保存したリストがまだ無い時（この仕組みを足した版に替えた時）は、前回出力した曲で作る（出したリストの理由を見直せるように）。
    # 作り物のデータは出力の後にデータが変わっているので、今のデータで作り直すと変わることを知らせる
    sql('DELETE FROM rec_lists')
    e = serve.STORE.q1("SELECT e.run_id FROM exports e JOIN recommendation_runs r USING(run_id) WHERE r.preset='p1' "
                       "ORDER BY e.exported_at DESC LIMIT 1")
    out = {x['leaderboard_id'] for x in serve.STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1',
                                                     (e['run_id'],))}
    live = ids(serve._make_list(serve.SETTINGS.view('p1'))['items'])
    assert set(live) != out   # 前提: 今のデータのリストとは違う
    r = call(server, 'recommend')
    assert set(ids(r['items'])) == out and all(c['in_export'] for c in r['items']) and r['lastExport']['dropped'] == []
    assert r['runnersUp'] and not out & set(ids(r['runnersUp'])) and r['counts']['played'] > 0   # 次点・候補の数は今のデータの物
    p = r['pending']
    assert {x['id'] for x in p['in']} == set(live) - out and {x['id'] for x in p['out']} == out - set(live)
    assert call(server, 'recommend')['items'] == r['items']   # 一度保存したら、それを使う
    assert set(ids(call(server, 'recommend/rebuild')['items'])) == set(live)
    # 出力が無いセット・作り直す時は、前回の出力の曲では作らない
    sql('DELETE FROM rec_lists')
    sql('DELETE FROM exports')
    assert set(ids(call(server, 'recommend')['items'])) == set(live)


def age(preset='p1'):
    """保存したリストの作った時刻を昔（1）にする。同じ秒のうちに作り直しても、madeAt で作り直したかが分かる"""
    sql('UPDATE rec_lists SET made_at=1 WHERE preset=?', (preset,))


def data_changed(lid):
    """取得してデータが変わった状態を作る: 今のリストの曲を「今後出さない」にし（今のデータで作り直すと外れる曲）、
    取得の回（last_run_at）を進める（ENGINE はデータの印が変わると計算し直す）。進めた回を返す"""
    sql("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', 0)", (lid,))
    run = int(serve.STORE.meta('last_run_at')) + 1
    serve.STORE.set_meta('last_run_at', run)
    return run


def test_saved_list_survives_added_and_removed_setting_keys(server, data):
    # 版が変わって設定の項目が増えた・減っただけでは作り直さない（保存した時に無かった項目は初期値だった、今は無い項目は数えない）。
    # 本当にリストに効く設定が変わった時は作り直す
    r = fresh(server)
    age()
    stored = json.loads(serve.STORE.q1("SELECT settings FROM rec_lists WHERE preset='p1'")['settings'])
    stored.pop('min_clear')        # 保存した時にはまだ無かった項目（初期値 0）
    stored.pop('unplayed_tech_drop')   # 同じ（2026-10-25 に足した R5。初期値 1.0。この版に替えても前に作ったリストはそのまま）
    stored['order'] = 'score'      # 今はなくした項目
    sql("UPDATE rec_lists SET settings=? WHERE preset='p1'", (json.dumps(stored),))
    again = call(server, 'recommend')
    assert again['madeAt'] == 1 and again['items'] == r['items']
    stored['playlist_size'] = 3    # 本当に変わった
    sql("UPDATE rec_lists SET settings=? WHERE preset='p1'", (json.dumps(stored),))
    assert call(server, 'recommend')['madeAt'] > 1
    assert serve._same_list_settings('{broken', '{}') is False and serve._same_list_settings('[]', '{}') is False


def test_star_range_version_change_and_setting(server, data):
    # ★の範囲の版（2026-10-25）に替えただけでは作り直さない: 前の版で保存したリスト（設定に unplayed_star_margin があり star_min・
    # star_max は無い。filters は最高★と上限）はそのまま出し、値を選ぶ参考（recentStars）だけ今のデータで足す。
    # そのセットで★の範囲を変えると作り直し（filters も新しい形に）、ほかのセットの保存したリストはそのまま。
    # 下限を上限より上にする保存は断る（bad-value）
    r = fresh(server)
    age()
    row = serve.STORE.q1("SELECT settings, payload FROM rec_lists WHERE preset='p1'")
    stored, payload = json.loads(row['settings']), json.loads(row['payload'])
    for k in ('star_min', 'star_max'):
        stored.pop(k)
    stored['unplayed_star_margin'] = 0.5
    payload['filters'] = {k: v for k, v in payload['filters'].items() if k not in ('starMin', 'starMax', 'starOut', 'recentStars')}
    payload['filters'].update(maxPlayedStars=4.9, starCap=5.4)
    sql("UPDATE rec_lists SET settings=?, payload=? WHERE preset='p1'", (json.dumps(stored), json.dumps(payload)))
    again = call(server, 'recommend')
    assert again['madeAt'] == 1 and again['items'] == r['items']
    assert again['filters']['starCap'] == 5.4 and 'starOut' not in again['filters']
    assert again['filters']['recentStars'] == r['filters']['recentStars'] is not None   # 作り物のデータは最近の自己ベストが1曲
    pid = serve.SETTINGS.add_preset('B', copy_from='p1')
    call(server, 'recommend/rebuild', {'preset': pid})
    other = serve.STORE.q1('SELECT made_at, payload FROM rec_lists WHERE preset=?', (pid,))
    assert call(server, 'settings/save', {'changes': {'star_min': 4, 'star_max': 6}, 'preset': 'p1'})['ok']
    now = call(server, 'recommend')
    assert now['madeAt'] > 1 and all(4 <= c['stars'] <= 6 for c in now['items'])
    assert now['filters']['starMin'] == 4 and now['filters']['starMax'] == 6 and now['filters']['starOut'] > 0
    assert dict(serve.STORE.q1('SELECT made_at, payload FROM rec_lists WHERE preset=?', (pid,))) == dict(other)
    bad = call(server, 'settings/save', {'changes': {'star_min': 7}, 'preset': 'p1'})
    assert not bad['ok'] and bad['error'] == 'bad-value' and serve.SETTINGS.values('p1')['star_min'] == 4


def test_saved_list_is_kept(server, data):
    r = fresh(server)
    row = serve.STORE.q1("SELECT made_at, data_at, payload FROM rec_lists WHERE preset='p1'")
    assert ids(json.loads(row['payload'])['items']) == ids(r['items'])
    assert r['madeAt'] == row['made_at'] and r['dataAt'] == row['data_at'] == int(serve.STORE.meta('last_run_at'))
    assert r['pending'] is None
    age()
    again = call(server, 'recommend')   # もう一度開いても作り直さない
    assert again['madeAt'] == 1 and again['pending'] is None and again['items'] == r['items']
    assert serve.STORE.q1('SELECT COUNT(*) AS n FROM rec_lists')['n'] == 1


def test_last_export_against_saved_list(server, data):
    # 作り物のデータには出力の記録が1つある。出した曲のうち今のリストに無い曲（dropped）と、各曲が前回の出力に入っていたか
    r = fresh(server)
    e = serve.STORE.q1("SELECT e.run_id, e.exported_at FROM exports e JOIN recommendation_runs r USING(run_id) "
                       "WHERE r.preset='p1' ORDER BY e.exported_at DESC LIMIT 1")
    out = {x['leaderboard_id'] for x in serve.STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1',
                                                     (e['run_id'],))}
    le = r['lastExport']
    assert le['at'] == e['exported_at'] and le['method'] == 'write' and le['app'] is None and le['n'] == len(out)
    assert {x['id'] for x in le['dropped']} == out - set(ids(r['items'])) and all(x['song'] for x in le['dropped'])
    assert [c['in_export'] for c in r['items']] == [c['leaderboard_id'] in out for c in r['items']]
    assert not any('in_export' in c for c in r['runnersUp'])
    # 出力の記録が無ければ、どちらも出さない
    sql('DELETE FROM exports')
    r = call(server, 'recommend')
    assert r['lastExport'] is None and not any('in_export' in c for c in r['items'])


def test_list_stays_when_data_changes(server, data, monkeypatch):
    first = fresh(server)
    lid = first['items'][0]['leaderboard_id']
    age()
    run = data_changed(lid)
    # アプリを起動し直しても（計算をため直し、外した曲を忘れる）、保存したリストは作った時のまま（選んだ理由も）
    monkeypatch.setattr(serve, 'ENGINE', Engine(serve.STORE, serve.SETTINGS))
    monkeypatch.setattr(serve, 'SESSION', {})
    r = call(server, 'recommend')
    assert r['items'] == first['items'] and r['runnersUp'] == first['runnersUp']
    assert r['madeAt'] == 1 and r['dataAt'] == first['dataAt']
    # 今のデータで作り直すと変わることを知らせる
    p = r['pending']
    top = first['items'][0]
    assert p['out'] == [{'id': lid, 'song': top['song'], 'difficulty': top['difficulty'], 'mode': top['mode']}]
    assert p['in'] and not p['same'] and p['dataAt'] == run
    # 「作り直す」で今のデータの物になる（知らせた通りに入れ替わる）
    r = call(server, 'recommend/rebuild')
    assert set(ids(r['items'])) == set(ids(first['items'])) - {lid} | {x['id'] for x in p['in']}
    assert r['pending'] is None and r['madeAt'] > 1 and r['dataAt'] == run
    assert call(server, 'recommend')['madeAt'] == r['madeAt']   # 作り直した物が残る


def test_pending_when_only_numbers_change(server, data):
    # 曲は同じで推定の数値だけが変わる時は same（入る・外れる曲は無い）
    fresh(server)
    lst = json.loads(serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])
    lst['items'][0]['gain'] += 1
    sql("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(lst),))
    r = call(server, 'recommend')
    assert r['pending'] == {'in': [], 'out': [], 'same': True, 'dataAt': int(serve.STORE.meta('last_run_at'))}
    assert r['items'][0]['gain'] == lst['items'][0]['gain']   # 出すのは保存した数値


def test_list_settings_rebuild_others_do_not(server, data):
    first = fresh(server)
    lid = first['items'][0]['leaderboard_id']
    age()
    data_changed(lid)
    # リストの中身に効かない設定（文字の大きさ・プレイリスト名）を変えても作り直さない
    assert call(server, 'settings/save', {'changes': {'ui_scale': 130, 'playlist_title': '別の名前'}})['ok']
    r = call(server, 'recommend')
    assert r['madeAt'] == 1 and r['items'] == first['items'] and r['pending']
    # リストに効く設定（曲数）を変えると、今のデータで作り直す
    assert call(server, 'settings/save', {'changes': {'playlist_size': 10}})['ok']
    r = call(server, 'recommend')
    assert r['madeAt'] > 1 and len(r['items']) == 10 and lid not in ids(r['items']) and r['pending'] is None


def test_hidden_settings_do_not_rebuild_list(server, data):
    # 画面に出さない項目も、値を変えて保存したリストが作り直しにならないのは前のまま（_NOT_LIST_KEYS）
    first = fresh(server)
    lid = first['items'][0]['leaderboard_id']
    age()
    data_changed(lid)
    assert call(server, 'settings/save', {'changes': {'fetch_song_details': False}})['ok']
    serve.SETTINGS.update({'playerdata_path': 'C:\\BS\\PlayerData.dat'}, trusted=True)
    r = call(server, 'recommend')
    assert r['madeAt'] == 1 and r['items'] == first['items'] and r['pending']
    # 画面の値としては今までどおり読める・ダイアログでだけ選ぶ項目も今までどおり画面からは変えられない
    got = call(server, 'settings/get')
    assert got['values']['fetch_song_details'] is False and got['values']['playerdata_path'] == 'C:\\BS\\PlayerData.dat'
    assert got['spec']['playerdata_path']['dialogOnly'] and not got['spec']['fetch_song_details']['dialogOnly']
    assert call(server, 'settings/save', {'changes': {'playerdata_path': 'C:\\other.dat'}})['error'] == 'not-allowed'


def test_action_rebuilds_with_current_data(server, data):
    # 画面の操作は利用者が決めたことなので、今のデータで作り直す（データの変化で外れる曲も外れる）
    first = fresh(server)
    lid, other = first['items'][0]['leaderboard_id'], first['items'][1]['leaderboard_id']
    age()
    data_changed(lid)
    r = act(server, 'remove', other)
    assert r['madeAt'] > 1 and r['pending'] is None and r['removed'] == 1
    assert lid not in ids(r['items']) and other not in ids(r['items'])
    assert call(server, 'recommend')['madeAt'] == r['madeAt']


def test_export_uses_saved_list(server, data):
    first = fresh(server)
    lid = first['items'][0]['leaderboard_id']
    age()
    data_changed(lid)
    t0 = int(time.time())
    r = call(server, 'export')
    assert r['ok'] and r['items'] == len(first['items'])
    # 書き込んだのは画面に出ている保存したリスト（今のデータなら外れる曲も入っている）。振り返りの記録も同じ曲
    hashes = {x['leaderboard_id']: (x['hash'] or '').lower() for x in serve.STORE.q('SELECT leaderboard_id, hash FROM leaderboards')}
    assert [s['hash'] for s in read_json(r['path'])['songs']] == [hashes[x] for x in ids(first['items'])]
    run = serve.STORE.q1('SELECT MAX(run_id) AS id FROM exports')['id']
    got = {x['leaderboard_id'] for x in serve.STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1', (run,))}
    assert got == set(ids(first['items']))
    # 前回の出力が今の出力になる。出力しても作り直さない
    r = call(server, 'recommend')
    le = r['lastExport']
    assert le['at'] >= t0 and le['method'] == 'write' and le['n'] == len(first['items']) and le['dropped'] == []
    assert all(c['in_export'] for c in r['items']) and r['madeAt'] == 1 and r['pending']
    # 作り直すと、出した曲のうち外れた曲が dropped に出て、新しく入った曲は「前回の出力に無い」
    r = call(server, 'recommend/rebuild')
    assert [x['id'] for x in r['lastExport']['dropped']] == [lid]
    assert [c['leaderboard_id'] for c in r['items'] if not c['in_export']] == [x for x in ids(r['items']) if x not in ids(first['items'])]


def test_export_records_estimate_parts(server, data):
    # 保存したリストの曲は推定の内訳（parts。合計が推定精度）を持ち（画面の応答には出さない）、出力の記録の reason にそのまま入る。
    # atExport.fcAcc は出力した時点の自己ベストの fc_acc。内訳を持たない保存したリスト（内訳を足す前の版で作った物）も出力できる
    r = fresh(server)
    assert all('parts' not in c for c in r['items'] + r['runnersUp'])
    lst = json.loads(serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])
    saved = {c['leaderboard_id']: c for c in lst['items'] + lst['runnersUp']}
    assert all(abs(sum(v for k, v in c['parts'].items() if k != 'r1Skipped') - c['est_acc']) < 1e-5 for c in saved.values())
    me = serve.SETTINGS.values()['player_id']
    fc = {x['leaderboard_id']: x['fc_acc'] for x in serve.STORE.q('SELECT leaderboard_id, fc_acc FROM scores WHERE player_id=?', (me,))}

    def recorded():
        run = serve.STORE.q1('SELECT MAX(run_id) AS id FROM exports')['id']
        return {x['leaderboard_id']: json.loads(x['reason'])
                for x in serve.STORE.q('SELECT leaderboard_id, reason FROM recommendation_items WHERE run_id=?', (run,))}
    assert call(server, 'export')['ok']
    got = recorded()
    assert set(got) == set(saved)
    assert all(x['parts'] == saved[lid]['parts'] and x['atExport']['fcAcc'] == fc.get(lid) for lid, x in got.items())
    assert any(x['atExport']['fcAcc'] for x in got.values())
    for c in lst['items'] + lst['runnersUp']:
        c.pop('parts')
    lst['items'] = lst['items'][1:]     # 前回と違う曲にする（同じ曲なら前の記録に出力を足すだけ）
    sql("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(lst),))
    assert call(server, 'export')['ok']
    assert all(x['parts'] is None for x in recorded().values())


# リプレイの頭（JD）。作り物のデータの中身（URL・読んだ JD の有る無し）に依らないよう、テストの中で決める
REPLAY = 'https://cdn.replays.beatleader.xyz/%d-test.bsor'


def set_replays(fmt):
    """自分のスコアのリプレイの URL を決め（fmt は score_id を入れる書式。None で URL 無し）、読んだ JD と保存したリストを消す
    （保存したリストの曲は作った時の URL を持つ）。曲ごとの推定（自己ベストの情報を持つ）を計算し直させる"""
    me = serve.SETTINGS.values()['player_id']
    with serve.STORE.tx():
        for x in serve.STORE.q('SELECT leaderboard_id, score_id FROM scores WHERE player_id=?', (me,)):
            serve.STORE.conn.execute('UPDATE scores SET replay_url=? WHERE player_id=? AND leaderboard_id=?',
                                     (fmt % x['score_id'] if fmt else None, me, x['leaderboard_id']))
        serve.STORE.conn.execute('DELETE FROM replay_info')
        serve.STORE.conn.execute('DELETE FROM rec_lists')
    serve.ENGINE = Engine(serve.STORE, serve.SETTINGS)   # 戻すのは fixture の data（monkeypatch で差し替えてある）
    return me


def played(r):
    return [c for c in r['items'] + r['runnersUp'] if c['kind'] == 'played']


def test_jd_state_without_replay_url(server, data, monkeypatch):
    # リプレイの URL が無い（次の取得の後に入る）曲は nourl。読みに行かない。遊んでいない曲には JD の項目を付けない
    set_replays(None)
    r = call(server, 'recommend')
    assert played(r) and all(c['jd_state'] == 'nourl' and c['jd'] is None and c['rt'] is None for c in played(r))
    assert not any('jd_state' in c for c in r['items'] + r['runnersUp'] if c['kind'] != 'played')
    calls = []
    monkeypatch.setattr(serve.replay, 'fetch_info', lambda *a, **k: calls.append(a) or {'jd': 20.0})
    r = call(server, 'recommend/replays')
    assert r['replayFailed'] == 0 and calls == [] and all(c['jd_state'] == 'nourl' for c in played(r))


def test_replay_url_filled_after_saving(server, data, monkeypatch):
    # リストを保存した後の取得でリプレイの URL が入った時（2026-10-17 に実際に起きた: 取得が終わる前に開いて保存した）は、
    # 保存したリストは作った時のままでも、同じスコアの ID の今の行から URL を補って JD を読む
    me = set_replays(None)
    r = call(server, 'recommend')
    assert played(r) and all(c['jd_state'] == 'nourl' for c in played(r))
    with serve.STORE.tx():
        for x in serve.STORE.q('SELECT leaderboard_id, score_id FROM scores WHERE player_id=?', (me,)):
            serve.STORE.conn.execute('UPDATE scores SET replay_url=? WHERE player_id=? AND leaderboard_id=?',
                                     (REPLAY % x['score_id'], me, x['leaderboard_id']))
    serve.STORE.set_meta('last_run_at', int(serve.STORE.meta('last_run_at')) + 1)
    changed = played(r)[0]['leaderboard_id']   # この曲は自己ベストが変わった（スコアの ID が違う）＝作った時のプレイの URL は分からない
    sql('UPDATE scores SET score_id=score_id+1000000 WHERE player_id=? AND leaderboard_id=?', (me, changed))
    r2 = call(server, 'recommend')
    assert ids(r2['items']) == ids(r['items'])   # 保存したリストは作った時のまま
    st = {c['leaderboard_id']: c['jd_state'] for c in played(r2)}
    assert st.pop(changed) == 'changed' and st and set(st.values()) == {'wait'}
    calls = []
    monkeypatch.setattr(serve.replay, 'INTERVAL', 0)
    monkeypatch.setattr(serve.replay, 'fetch_info', lambda url, ua, opener=None: calls.append(url) or {'jd': 19.5})
    r3 = call(server, 'recommend/replays')
    by = {c['leaderboard_id']: c for c in played(r3)}
    assert by.pop(changed)['jd_state'] == 'changed' and all(c['jd_state'] == 'ok' and c['jd'] == 19.5 for c in by.values())
    assert len(calls) == len(by)


def test_replays_offline_stay_waiting(server, data):
    # 通信できない（BLN_NO_NET）時は失敗の数を返し、まだ読んでいない（wait）のまま（次の機会にやり直す）
    set_replays(REPLAY)
    pl = played(call(server, 'recommend'))
    assert pl and all(c['jd_state'] == 'wait' and c['jd'] is None and c['rt'] is None for c in pl)
    r = call(server, 'recommend/replays')
    assert r['replayFailed'] == min(len(pl), replay.MAX_PER_CALL)
    assert all(c['jd_state'] == 'wait' for c in played(r)) and serve.STORE.q1('SELECT COUNT(*) AS n FROM replay_info')['n'] == 0


def relist():
    """保存したリストを消し、曲ごとの推定を計算し直させる（自己ベストの情報を変えた後に）"""
    sql('DELETE FROM rec_lists')
    serve.ENGINE = Engine(serve.STORE, serve.SETTINGS)


def test_replays_read_jd_and_rt(server, data, monkeypatch):
    monkeypatch.setattr(serve.replay, 'INTERVAL', 0)
    me = set_replays(REPLAY)
    fs = played(call(server, 'recommend'))[0]['leaderboard_id']
    sql("UPDATE scores SET modifiers='FS' WHERE player_id=? AND leaderboard_id=?", (me, fs))   # 速さのモディファイア
    relist()   # モディファイアを変えると推定が変わり、入る曲が変わりうるので、変えた後のリストから残りの2曲を選ぶ
    pl = played(call(server, 'recommend'))
    assert fs in {c['leaderboard_id'] for c in pl} and len(pl) >= 3
    bad, gone = [c['leaderboard_id'] for c in pl if c['leaderboard_id'] != fs][:2]
    sql("UPDATE scores SET replay_url='https://example.com/x.bsor' WHERE player_id=? AND leaderboard_id=?", (me, bad))   # 置き場所が違う
    sql("UPDATE scores SET replay_url=replace(replay_url, '-test', '-gone') WHERE player_id=? AND leaderboard_id=?", (me, gone))
    relist()   # 保存したリストの曲は作った時の URL を持つ（URL は推定に効かないので、入る曲は同じ）
    calls = []

    def fetch(url, user_agent, opener=None):
        calls.append(url)
        if '-gone' in url:
            raise NetError('http-404', url)   # 消えたリプレイ（やり直さない）
        return {'jd': 20.0}
    monkeypatch.setattr(serve.replay, 'fetch_info', fetch)
    r = call(server, 'recommend/replays')
    assert r['replayFailed'] == 0
    by = {c['leaderboard_id']: c for c in played(r)}
    assert by[bad]['jd_state'] == by[gone]['jd_state'] == 'none' and by[bad]['jd'] is None and by[gone]['rt'] is None
    for lid, c in by.items():
        if lid not in (bad, gone):
            assert c['jd_state'] == 'ok' and c['jd'] == 20.0 and c['rt'] == replay.rt_ms(20.0, c['njs'], c['best_mods'])
    assert by[fs]['best_mods'] == 'FS' and by[fs]['rt'] == round(20.0 / 2 / (by[fs]['njs'] * 1.2) * 1000)
    assert len(calls) == len(by) - 1 and not any('example.com' in u for u in calls)   # 置き場所が違う URL へは通信しない
    # 読んだ物は残り、もう一度は読まない（404 もやり直さない）
    r2 = call(server, 'recommend/replays')
    assert r2['replayFailed'] == 0 and len(calls) == len(by) - 1 and r2['items'] == r['items']
    assert call(server, 'recommend')['items'] == r['items']


@pytest.mark.parametrize('payload', ['{broken', '{}', 'null', '[]', '{"items": [1], "runnersUp": []}', None])
def test_broken_saved_list_is_rebuilt(server, data, payload):
    first = fresh(server)
    sql("UPDATE rec_lists SET payload=?, made_at=1 WHERE preset='p1'", (payload,))
    r = call(server, 'recommend')
    assert r['madeAt'] > 1 and r['items'] == first['items'] and r['pending'] is None
    assert ids(json.loads(serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])['items']) == ids(first['items'])


def test_presets_keep_their_own_saved_list(server, data):
    fresh(server)
    pid = serve.SETTINGS.add_preset('B', copy_from='p1')
    other = call(server, 'recommend', {'preset': pid})
    age('p1')
    age(pid)
    # 片方を作り直しても、もう片方はそのまま
    assert call(server, 'recommend/rebuild', {'preset': 'p1'})['madeAt'] > 1
    assert call(server, 'recommend', {'preset': pid})['madeAt'] == 1
    age('p1')
    assert act(server, 'remove', other['items'][0]['leaderboard_id'], preset=pid)['madeAt'] > 1
    assert call(server, 'recommend', {'preset': 'p1'})['madeAt'] == 1
    # 設定を変えたセットだけ作り直す
    age(pid)
    call(server, 'settings/save', {'changes': {'playlist_size': 5}, 'preset': 'p1'})
    assert len(call(server, 'recommend', {'preset': 'p1'})['items']) == 5
    assert call(server, 'recommend', {'preset': pid})['madeAt'] == 1
    assert sorted(x['preset'] for x in serve.STORE.q('SELECT preset FROM rec_lists')) == sorted(['p1', pid])


# ---- 似ている人 ----

def test_similar_with_data(server, data):
    r = call(server, 'similar')
    assert r['ok'] and r['hasData'] and r['poolSize'] == len(r['players'])   # 画面の「比較対象 n 人」は表の行の数
    flags = [p['similar'] for p in r['players']]
    assert any(flags) and flags == sorted(flags, reverse=True)   # 似ている人が先
    assert abs(sum(r['me']['style']) - 1) < 1e-9


def test_similar_compare(server, data):
    pid = next(p['id'] for p in call(server, 'similar')['players'] if p['similar'])
    r = call(server, 'similar/compare', {'id': pid})
    assert r['ok'] and r['maps']
    assert all(m['myAcc'] is not None and m['theirAcc'] is not None for m in r['maps'])   # 2人ともやった曲だけ
    stars = [m['stars'] for m in r['maps']]
    assert stars == sorted(stars, reverse=True)
    for bad in ('nobody', None, 5):
        assert call(server, 'similar/compare', {'id': bad}) == {'ok': False, 'error': 'bad-request'}


# ---- 難しさ（スライダー・難しさ違いのセット・ほかのセットと同じ曲を入れない） ----

def hashes(pid):
    """保存したリストの曲の hash"""
    lst = json.loads(serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (pid,))['payload'])
    return {c['hash'] for c in lst['items']}


def test_difficulty_slider_saves_together(server, data):
    st = call(server, 'status')['difficulty']
    assert [x['id'] for x in st['levels']] == ['steady', 'standard', 'challenge']
    assert st['levels'][0] == {'id': 'steady', 'values': {'unplayed_pass_cap': 'p75', 'challenge_max': 0, 'min_clear': 80, 'acc_cap_margin': 0.0},
                               'setName': '着実'}
    assert st['setDefault'] == ['steady', 'challenge'] and st['setSize'] == 7
    g = call(server, 'settings/get')
    assert g['difficulty'] == st and g['level'] is None   # 作り物のデータは挑戦枠 50（個別に変えた＝カスタム）
    first = fresh(server)
    assert any(c['kind'] == 'unplayed' for c in first['items'])
    age()
    # スライダーは段階の項目をまとめて保存する。リストに効く設定なので作り直す
    r = call(server, 'settings/save', {'changes': st['levels'][0]['values']})
    assert r['level'] == 'steady' and r['values']['unplayed_pass_cap'] == 'p75' and r['values']['challenge_max'] == 0
    rec = call(server, 'recommend')
    f = rec['filters']
    assert rec['madeAt'] > 1 and f['passCap'] == rec['range']['pass']['p75'] and f['passOut'] > 0
    assert not any(c['kind'] == 'unplayed' for c in rec['items'])   # 作り物の未プレイの曲は、どれもクリアしてきた曲より pass が高い
    assert len(rec['items']) == 15
    for level in ('standard', 'challenge'):
        vals = next(x['values'] for x in st['levels'] if x['id'] == level)
        assert call(server, 'settings/save', {'changes': vals})['level'] == level
        assert call(server, 'recommend')['filters']['passCap'] is None
    # 1つだけ変えるとカスタム。初期値に戻すと標準
    assert call(server, 'settings/save', {'changes': {'challenge_max': 3}})['level'] is None
    assert call(server, 'settings/get')['level'] is None
    # チャレンジングの精度の上限の余裕（1.0）が残っているので、挑戦枠だけ戻してもカスタム。余裕も戻すと標準
    assert call(server, 'settings/reset', {'keys': ['challenge_max']})['level'] is None
    assert call(server, 'settings/reset', {'keys': ['acc_cap_margin']})['level'] == 'standard'
    assert call(server, 'settings/save', {'changes': {'unplayed_pass_cap': 'p95'}})['level'] is None
    assert call(server, 'settings/save', {'changes': {'unplayed_pass_cap': 'hard'}})['error'] == 'bad-value'


def test_difficulty_set_default_two_sets(server, data):
    before = ids(fresh(server)['items'])
    r = call(server, 'presets/difficulty-set', {'copyFrom': 'p1'})   # 段階・曲数を省くと初期値（着実とチャレンジング・7曲）
    assert r['ok'] and len(r['ids']) == 2 and r['activePreset'] == r['ids'][0] == call(server, 'status')['activePreset']
    assert [p['name'] for p in r['presets']] == ['スタンダード', '着実', 'チャレンジング']
    steady, hard = (serve.SETTINGS.values(p) for p in r['ids'])
    assert (steady['output_filename'], steady['playlist_title']) == ('blnext-steady.bplist', '次回 着実')
    assert (hard['output_filename'], hard['playlist_title']) == ('blnext-challenge.bplist', '次回 チャレンジング')
    assert serve.difficulty_of(steady) == 'steady' and serve.difficulty_of(hard) == 'challenge'
    assert steady['exclude_other_sets'] and hard['exclude_other_sets'] and not serve.SETTINGS.values('p1')['exclude_other_sets']
    # 作った後にリストを作ってある（着実から1曲ずつ交互に選ぶ。かぶらない）。元のセットのリストはそのまま
    a, b = (call(server, 'recommend', {'preset': p}) for p in r['ids'])
    assert len(a['items']) == len(b['items']) == 7 and a['pending'] is None and b['pending'] is None
    assert not hashes(r['ids'][0]) & hashes(r['ids'][1]) and b['filters']['otherOut'] == 7
    assert a['filters']['otherOut'] == 7 and r['group'] == r['ids']   # 交互に選んだので、着実もチャレンジングの曲を外している
    assert ids(call(server, 'recommend', {'preset': 'p1'})['items']) == before
    # 全部のセットを書き込むと、セットごとのファイル名で出る
    out = call(server, 'export/all')['results']
    assert [x['ok'] for x in out] == [True, True, True]
    pl = data / 'FakeBeatSaber' / 'Playlists'
    assert read_json(pl / 'blnext-steady.bplist')['playlistTitle'] == '次回 着実'
    assert len(read_json(pl / 'blnext-challenge.bplist')['songs']) == 7


def test_difficulty_set_chosen_levels(server, data):
    fresh(server)
    r = call(server, 'presets/difficulty-set', {'copyFrom': 'p1', 'levels': ['challenge', 'standard', 'steady'], 'size': 5})
    assert [p['name'] for p in r['presets']][1:] == ['着実', '標準', 'チャレンジング']   # 並びは易しい方から
    lists = [call(server, 'recommend', {'preset': p}) for p in r['ids']]
    assert [len(x['items']) for x in lists] == [5, 5, 5]
    hs = [hashes(p) for p in r['ids']]
    assert not hs[0] & hs[1] and not hs[0] & hs[2] and not hs[1] & hs[2]   # どの2つもかぶらない
    assert [x['filters']['otherOut'] for x in lists] == [10, 10, 10]       # 交互に選ぶので、どれもほかの2つの曲を外す
    # 同じ名前のセットがあれば作らずに知らせる（何も足さない）
    e = call(server, 'presets/difficulty-set', {'levels': ['steady', 'challenge']})
    assert e == {'ok': False, 'error': 'preset-exists', 'key': '着実・チャレンジング'} and len(serve.SETTINGS.preset_ids()) == 4


def test_difficulty_set_errors(server, data):
    for body, err in (({'levels': []}, 'bad-levels'), ({'levels': ['hard']}, 'bad-levels'), ({'levels': ['steady', 'steady']}, 'bad-levels'),
                      ({'levels': 'steady'}, 'bad-levels'), ({'size': 0}, 'bad-value'), ({'size': 51}, 'bad-value'),
                      ({'size': '7'}, 'bad-value'), ({'copyFrom': 'p9'}, 'no-preset'), ({'copyFrom': 5}, 'bad-request')):
        assert call(server, 'presets/difficulty-set', body)['error'] == err, body
    assert serve.SETTINGS.preset_ids() == ['p1']
    # セットの上限（10個）: あと1つしか足せない時に2つは作らない。1つなら作れる
    for i in range(8):
        serve.SETTINGS.add_preset('S%d' % i, copy_from='p1')
    assert call(server, 'presets/difficulty-set', {})['error'] == 'too-many-presets' and len(serve.SETTINGS.preset_ids()) == 9
    r = call(server, 'presets/difficulty-set', {'levels': ['steady']})
    assert r['ok'] and len(serve.SETTINGS.preset_ids()) == 10


def test_exclude_other_sets_only_between_on_sets(server, data):
    base = fresh(server)
    b = serve.SETTINGS.add_preset('B', copy_from='p1')
    c = serve.SETTINGS.add_preset('C', copy_from='p1')
    for pid in (b, c):
        call(server, 'recommend', {'preset': pid})
    assert hashes(b) == hashes(c) == hashes('p1')        # 写したので同じ曲
    # B をオンにしても、ほかにオンのセットが無ければ同じ
    call(server, 'settings/save', {'changes': {'exclude_other_sets': True}, 'preset': b})
    call(server, 'recommend', {'preset': b})
    assert hashes(b) == hashes('p1')
    # C もオンにすると（設定が変わったので作り直す）、B のリストの曲を外す。オフの p1 の曲は外さない
    call(server, 'settings/save', {'changes': {'exclude_other_sets': True}, 'preset': c})
    rc = call(server, 'recommend', {'preset': c})
    assert not hashes(c) & hashes(b) and rc['filters']['otherOut'] == len(base['items']) and len(rc['items']) == 15
    # B は作った時のまま（先に作った方が先に選ぶ）。C の曲は B が選ばなかった曲なので、B を作り直しても変わらない（知らせも出ない）
    rb = call(server, 'recommend', {'preset': b})
    assert hashes(b) == hashes('p1') and rb['pending'] is None
    call(server, 'recommend/rebuild', {'preset': b})
    assert hashes(b) == hashes('p1')
    # 外す集合は SESSION（外した曲）とは別。オフに戻すと C は p1 と同じに戻る
    with serve._session_lock:
        assert not serve.SESSION.get(c, {}).get('removed')
    call(server, 'settings/save', {'changes': {'exclude_other_sets': False}, 'preset': c})
    call(server, 'recommend', {'preset': c})
    assert hashes(c) == hashes('p1')
    # 消したセットのリストは外す元にならない
    call(server, 'settings/save', {'changes': {'exclude_other_sets': True}, 'preset': c})
    call(server, 'presets/delete', {'id': b})
    assert call(server, 'recommend/rebuild', {'preset': c})['filters']['otherOut'] == 0 and hashes(c) == hashes('p1')
