# 初回の準備（serve.py の setup/start・setup/lists、core/settings.py の make_initial_sets・initial_target）。
# 準備（作り物のデータ・サーバー・呼び方）は test_serve_data.py の物を使う。新しい利用者は作り物の BeatLeader（fakebl）から本物と同じ流れで取得する
import json, time

import pytest

import serve
from core.estimate.engine import Engine
from core.fetch import http as bhttp
from core.fetch.http import BLClient, NetError
from core.fetch.refresh import Refresher
from core.settings import Settings, initial_target
from core.store.db import Store
from fakebl import FakeBL
from test_serve_data import built, call, data, server   # noqa: F401（built・data・server は fixture）


# ---- 目標の順位・3つのセット（設定だけ） ----

@pytest.mark.parametrize('rank,target', [(677, 500), (500, 300), (120, 100), (101, 100), (100, 50), (45, 30), (11, 10), (10, 5), (3, 2), (2, 1), (1, 1),
                                         (2345, 2000), (0, None), (None, None)])
def test_initial_target(rank, target):
    assert initial_target(rank) == target


def test_make_initial_sets(tmp_path):
    s = Settings(str(tmp_path))
    ids = s.make_initial_sets()
    ps = s.presets()
    assert [p['id'] for p in ps] == ids and len(ids) == 3
    assert [p['name'] for p in ps] == ['着実', '標準', 'チャレンジング']
    assert [p['values']['output_filename'] for p in ps] == ['blnext-steady.bplist', 'blnext-standard.bplist', 'blnext-challenge.bplist']
    assert [p['values']['playlist_title'] for p in ps] == ['次回 着実', '次回 標準', '次回 チャレンジング']
    assert all(p['values']['playlist_size'] == 10 and p['values']['exclude_other_sets'] for p in ps)
    assert s.active_preset() == ids[0]      # 最初のセットが消えても、開いているセットは残っているセット
    assert s.make_initial_sets() == []      # 2回目は何もしない（3つのまま）
    assert s.preset_ids() == ids


@pytest.mark.parametrize('touch', [
    lambda s: s.update({'playlist_size': 12}),                   # 値を変えた
    lambda s: s.rename_preset('p1', 'スタンダード') or s.add_preset('もう1つ'),   # セットを足した
    lambda s: s.set_cover('p1', b'\x89PNG\r\n\x1a\nx', 'png'),   # 画像を選んだ
])
def test_make_initial_sets_keeps_touched_sets(tmp_path, touch):
    s = Settings(str(tmp_path))
    touch(s)
    before = s.preset_ids()
    assert s.make_initial_sets() == []
    assert s.preset_ids() == before


def test_make_initial_sets_keeps_old_format(tmp_path):
    # 前の版の設定ファイル（項目が直下）は最初のセットの値として読むので、手を付けたセットとして扱う
    (tmp_path / 'settings.json').write_text(json.dumps({'playlist_size': 7}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert s.make_initial_sets() == [] and s.preset_ids() == ['p1'] and s.get('playlist_size') == 7


# ---- 新しい利用者の流れ（作り物の BeatLeader） ----

@pytest.fixture
def newcomer(tmp_path, monkeypatch):
    """まだ何も無い書き込み先と作り物の BeatLeader。取得は refresh(fake) でその場で最後まで流す"""
    monkeypatch.setattr(bhttp, 'GLOBAL_INTERVAL', 0)
    monkeypatch.setattr(bhttp, 'PLAYERS_INTERVAL', 0)
    s = Settings(str(tmp_path / 'config'))
    st = Store(str(tmp_path / 'data' / 'blnext.db'))
    monkeypatch.setattr(serve, 'SETTINGS', s)
    monkeypatch.setattr(serve, 'STORE', st)
    monkeypatch.setattr(serve, 'ENGINE', Engine(st, s))
    monkeypatch.setattr(serve, 'SESSION', {})
    monkeypatch.setattr(serve, 'DATA_DIR', str(tmp_path))
    fake = FakeBL(n_players=60, self_rank=30, n_maps=120)
    monkeypatch.setattr(serve, 'BLClient', lambda ua: BLClient(ua, opener=fake))
    yield fake
    st.close()


def refresh(fake):
    return Refresher(serve.STORE, serve.SETTINGS, lambda: BLClient('test', opener=fake)).run_sync()


def status():
    return serve._api_status({})


def test_newcomer_flow(server, newcomer):
    fake = newcomer
    assert status()['setup'] == 'player' and not status()['playerId']
    r = call(server, 'setup/start', {'id': fake.self_id})
    assert r['ok'] and r['name'] == fake.players[29]['name']
    v = serve.SETTINGS.values()
    assert v['player_id'] == fake.self_id and v['country'] == 'jp' and v['target_country_rank'] == 20   # 30位の上の切りのよい順位
    assert status()['setup'] == 'lists' and [p['name'] for p in status()['presets']] == ['着実', '標準', 'チャレンジング']
    assert [bool(p['cover']) for p in status()['presets']] == [True, False, True]   # 着実＝青・標準＝アプリのアイコン・チャレンジング＝赤
    p = refresh(fake)
    assert not p['error'] and status()['setup'] == 'lists'
    r = call(server, 'setup/lists')
    assert r['ok'] and [s['level'] for s in r['sets']] == ['steady', 'standard', 'challenge']
    assert all(s['songs'] > 0 for s in r['sets'])
    assert status()['setup'] is None
    lists = [json.loads(serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (s['id'],))['payload']) for s in r['sets']]
    hashes = [c['hash'] for lst in lists for c in lst['items']]
    assert len(hashes) == len(set(hashes))                 # 3つのリストに同じ曲は入らない（組のセットを交互に選ぶ）
    assert len(lists[0]['items']) == r['sets'][0]['songs']
    # 開くとそのまま出る（作り直さない）
    made = serve.STORE.q1('SELECT made_at FROM rec_lists WHERE preset=?', (r['sets'][1]['id'],))['made_at']
    rec = call(server, 'recommend', {'preset': r['sets'][1]['id']})
    assert rec['hasData'] and rec['madeAt'] == made


def test_similar_counts_only_compared_people(server, newcomer):
    # スコアが少なくて取らなかった人は「比較対象 n 人」に数えず（n は表の行の数）、fewScores に数える
    fake = newcomer
    for k, p in enumerate(fake.players, 1):
        p['rankedPlayCount'] = 10 + k           # fakebl の accgraph の行の数。min_scores 30 に届かないのは 1〜19位の 19人
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    p = refresh(fake)
    assert not p['error'] and p['total'] == 40 and p['skipped'] == 19
    r = call(server, 'similar')
    assert r['poolSize'] == len(r['players']) == 40 and r['fewScores'] == 19


def many_scores(fake, n=60):
    """自己ベストを n 曲にする（最近の日時。★3.0〜）"""
    now = int(time.time())
    fake.my_scores = {lb: {'acc': 0.88 + (i % 5) * 0.01, 'time': now - 86400 * 3 - i} for i, (lb, _, _) in enumerate(fake.maps[:n])}


def test_newcomer_gets_star_ranges(server, newcomer):
    # 自己ベストが十分ある人: 着実 〜p75・標準 p50〜p90・チャレンジング p75〜p95+0.5 が入り、リストの曲はその中
    fake = newcomer
    many_scores(fake)
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert not refresh(fake)['error']
    r = call(server, 'setup/lists')
    assert r['ok']
    # 作り物の譜面の★は 3.0, 3.1, … の 60曲（3.0〜8.9。5.95 は 6 に丸まる）
    assert [(s['starMin'], s['starMax']) for s in r['sets']] == [(0, 7.4), (6, 8.3), (7.4, 9.1)]
    for s in r['sets']:
        lst = json.loads(serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (s['id'],))['payload'])
        assert lst['items'] and all(s['starMin'] <= c['stars'] <= s['starMax'] for c in lst['items'])
    assert serve.STORE.meta('setup_sets') is None
    # 開いても作り直さない（入れた★の範囲で作ったリストのまま）
    made = serve.STORE.q1('SELECT made_at FROM rec_lists WHERE preset=?', (r['sets'][0]['id'],))['made_at']
    rec = call(server, 'recommend', {'preset': r['sets'][0]['id']})
    assert rec['madeAt'] == made and rec['filters']['starMax'] == 7.4


def test_old_scores_are_used_when_few_recent(server, newcomer):
    # 最近の自己ベストが少ない人は、全部の自己ベストで決める
    fake = newcomer
    many_scores(fake)
    for s in fake.my_scores.values():
        s['time'] = 1690000000
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert not refresh(fake)['error']
    assert call(server, 'setup/lists')['sets'][0]['starMax'] == 7.4


def test_star_ranges_respect_user_changes(server, newcomer):
    # 取得の間に自分で★の範囲を入れた・段階を変えたセットには入れない。消したセットは飛ばす
    fake = newcomer
    many_scores(fake)
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    steady, standard, challenge = serve.SETTINGS.preset_ids()
    serve.SETTINGS.update({'star_max': 5}, preset=steady)
    serve.SETTINGS.update({'challenge_max': 7}, preset=standard)
    serve.SETTINGS.update({'active_preset': steady})
    serve.SETTINGS.delete_preset(challenge)
    assert not refresh(fake)['error']
    r = call(server, 'setup/lists')
    assert [(s['starMin'], s['starMax']) for s in r['sets']] == [(0, 5), (0, 0)]


def test_star_ranges_kept_when_reselecting(server, newcomer):
    # 人を選び直しても、初回の準備で作ったセットには★の範囲が入る（2回目の setup/start は make_initial_sets が何もしない）
    fake = newcomer
    many_scores(fake)
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert call(server, 'settings/save', {'changes': {'player_id': ''}})['ok']
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert not refresh(fake)['error']
    assert call(server, 'setup/lists')['sets'][0]['starMax'] == 7.4


def test_no_star_ranges_for_touched_sets(server, newcomer):
    # 手を付けていたセット（初回の準備で作っていない）には入れない
    fake = newcomer
    many_scores(fake)
    serve.SETTINGS.update({'playlist_size': 12})
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert not refresh(fake)['error']
    r = call(server, 'setup/lists')
    assert [(s['starMin'], s['starMax']) for s in r['sets']] == [(0, 0)] and r['sets'][0]['songs'] == 12


@pytest.mark.parametrize('keep', [0, 1, 5, 29])
def test_newcomer_with_few_scores(server, newcomer, keep):
    # ランク曲のスコアが無い・少ない人でも落ちない（リストが作れなければ曲数 0 で返り、画面が知らせる）
    fake = newcomer
    fake.my_scores = dict(list(fake.my_scores.items())[:keep])
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert not refresh(fake)['error']
    r = call(server, 'setup/lists')
    assert r['ok'], r
    assert len(r['sets']) == 3 and status()['setup'] is None
    if keep < 5:
        assert all(s['starMin'] == 0 and s['starMax'] == 0 for s in r['sets'])   # 自己ベストが少なければ★の範囲は入れない
    else:
        assert [s['starMax'] > 0 for s in r['sets']] == [True] * 3, r
        assert [s['fewSimilar'] for s in r['sets']] == [True] * 3, r   # 共通の曲が 30曲に届かない＝似ている人がいない
        assert r['sets'][0]['songs'] > 0, r                            # 作り物の譜面は★の間隔が広いので、上の段階は候補が無いことがある
    if keep == 0:
        assert all(s['songs'] == 0 for s in r['sets'])
    assert call(server, 'home')['ok']
    for s in r['sets']:
        assert call(server, 'recommend', {'preset': s['id']})['ok']


def test_newcomer_without_rank(server, newcomer):
    # まだランクが付いていない人（国内順位が無い）: 目標は初期値のまま・比較対象なし
    fake = newcomer
    me = next(p for p in fake.players if p['id'] == fake.self_id)
    me.update(rank=0, countryRank=0)
    fake.my_scores = {}
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert serve.SETTINGS.get('target_country_rank') == 500
    assert not refresh(fake)['error']
    r = call(server, 'setup/lists')
    assert r['ok'] and all(s['songs'] == 0 for s in r['sets'])


def test_setup_start_errors(server, newcomer, monkeypatch):
    fake = newcomer
    for bad in ({}, {'id': 5}, {'id': 'abc'}, {'id': '１２３'}, {'id': '../x'}):
        assert call(server, 'setup/start', bad)['error'] == 'bad-request'
    assert call(server, 'setup/start', {'id': '999'})['error'] == 'player-not-found'

    def offline(c, pid):
        raise NetError('offline')
    monkeypatch.setattr(serve.bl, 'player', offline)
    assert call(server, 'setup/start', {'id': fake.self_id})['error'] == 'offline'
    # 失敗した時は何も変えない
    assert status()['setup'] == 'player' and serve.SETTINGS.preset_ids() == ['p1']


def test_setup_start_keeps_country_when_not_set(server, newcomer, monkeypatch):
    fake = newcomer
    me = next(p for p in fake.players if p['id'] == fake.self_id)
    me['country'] = 'not set'
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert serve.SETTINGS.get('country') == 'jp'


def test_setup_start_again_keeps_sets(server, newcomer):
    # 人を選び直しても、作ったセットはそのまま（数も id も）
    fake = newcomer
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    ids = serve.SETTINGS.preset_ids()
    assert call(server, 'settings/save', {'changes': {'player_id': ''}})['ok']
    assert status()['setup'] == 'player'
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert serve.SETTINGS.preset_ids() == ids


@pytest.mark.parametrize('public,value', [(True, '1'), (False, '0')])
def test_setup_start_reads_stats_public(server, newcomer, public, value):
    fake = newcomer
    next(p for p in fake.players if p['id'] == fake.self_id)['statsPublic'] = public
    r = call(server, 'setup/start', {'id': fake.self_id})
    assert r['statsPublic'] is public and serve.STORE.meta('stats_public') == value
    assert status()['statsPrivate'] is (not public)


def test_stats_check(server, newcomer):
    fake = newcomer
    me = next(p for p in fake.players if p['id'] == fake.self_id)
    assert call(server, 'stats/check')['error'] == 'no-player'
    me['statsPublic'] = False
    assert call(server, 'setup/start', {'id': fake.self_id})['ok']
    assert call(server, 'stats/check')['statsPublic'] is False and status()['statsPrivate'] is True
    me['statsPublic'] = True                 # 公開に変えた
    assert call(server, 'stats/check')['statsPublic'] is True and status()['statsPrivate'] is False
    del me['statsPublic']                    # 印が無い応答
    r = call(server, 'stats/check')
    assert r['ok'] and r['statsPublic'] is None and status()['statsPrivate'] is False


def test_open_bl_settings_opens_only_the_fixed_page(server, monkeypatch):
    opened = []
    monkeypatch.setattr(serve, 'open_browser', opened.append)
    assert call(server, 'open/bl-settings', {'url': 'https://evil.example/'})['ok']
    assert opened == ['https://beatleader.com/settings#scores']


def test_stats_notice_does_not_rebuild_lists():
    assert 'stats_notice' in serve._NOT_LIST_KEYS


def test_search_by_id(server, newcomer):
    fake = newcomer
    r = call(server, 'players/search', {'name': fake.self_id})
    assert r['ok'] and [p['id'] for p in r['players']] == [fake.self_id] and r['players'][0]['countryRank'] == 30
    assert call(server, 'players/search', {'name': '999'})['players'] == []


def test_existing_user_has_no_setup(data):
    # 前の版から使っている人（プレイヤーがいる）: 初回の準備は出ない・セットは変わらない
    st = status()
    assert st['setup'] is None and st['playerId']
    assert serve.SETTINGS.preset_ids() == ['p1']
