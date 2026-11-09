# 組のセット（exclude_other_sets がオンのセットどうし）を1曲ずつ交互に選ぶ（serve.py の _draft）。順はスネーク
# （A, B, B, A, A, B, …）。各セットの条件は守り、同じ曲（別難易度も）は入らない。難しさ違いのセットを作った時（presets/difficulty-set）と、
# 「作り直す」で組全部を選んだ時（recommend/rebuild の group）に交互に選ぶ。
# 準備（作り物のデータ・サーバー・呼び方）は test_serve_data.py の物を使う
import json

import serve
from core.estimate.engine import Engine
from test_serve_data import built, call, data, ids, server, sql   # noqa: F401（built・data・server は fixture）


def saved(pid):
    return json.loads(serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (pid,))['payload'])


def songs(pid):
    return set(ids(saved(pid)['items']))


def hashes(pid):
    return {c['hash'] for c in saved(pid)['items']}


def made_at(pid):
    return serve.STORE.q1('SELECT made_at FROM rec_lists WHERE preset=?', (pid,))['made_at']


def group(n, **vals):
    """p1 を写した組のセットを n 個作る（段階はどれもカスタムなので、選ぶ順＝id の順）"""
    out = []
    for i in range(n):
        pid = serve.SETTINGS.add_preset('組%d' % i, copy_from='p1')
        serve.SETTINGS.update({'exclude_other_sets': True, **vals}, preset=pid)
        out.append(pid)
    return out


def ranked(port, n, **vals):
    """組でないセット（p1 を写した物）で曲数 n にした時の曲の ID を、並び順の値の高い順に（取り合いが無い時の順位）"""
    pid = serve.SETTINGS.add_preset('比べる用', copy_from='p1')
    serve.SETTINGS.update({**vals, 'playlist_size': n}, preset=pid)
    r = call(port, 'recommend/rebuild', {'preset': pid})
    serve.SETTINGS.delete_preset(pid)
    return [c['leaderboard_id'] for c in sorted(r['items'], key=lambda c: -c['score'])]


def rebuild(port, pid, **extra):
    return call(port, 'recommend/rebuild', {'preset': pid, **extra})


def redraft(port, pid):
    r = rebuild(port, pid, group=True)
    assert r['ok'], r
    return r


# ---- スネークの順 ----

def test_snake_two_sets(server, data):
    p = ranked(server, 6, played_ratio=100)
    a, b = group(2, played_ratio=100, playlist_size=3)
    r = redraft(server, a)
    assert r['group'] == [a, b] and r['preset'] == a and len(r['items']) == 3
    # A, B, B, A, A, B（1曲ずつ交互だと A は 0・2・4 番目になる）
    assert songs(a) == {p[0], p[3], p[4]} and songs(b) == {p[1], p[2], p[5]}
    assert ids(r['items']) == ids(saved(a)['items'])
    assert all(c['pick'] == 'auto' and not c['pinned'] for c in saved(a)['items'] + saved(b)['items'])
    # 取り合った後でも、そのセットだけ作り直した時と同じリスト（作り直すと変わるという知らせは出ない）
    for pid in (a, b):
        assert call(server, 'recommend', {'preset': pid})['pending'] is None


def test_snake_three_sets(server, data):
    p = ranked(server, 6, played_ratio=100)
    a, b, c = group(3, played_ratio=100, playlist_size=2)
    assert redraft(server, b)['group'] == [a, b, c]
    # A, B, C, C, B, A
    assert songs(a) == {p[0], p[5]} and songs(b) == {p[1], p[4]} and songs(c) == {p[2], p[3]}


def test_group_order_easy_first(server, data):
    x = group(1)[0]   # カスタム（どの段階とも合わない）は後ろ
    r = call(server, 'presets/difficulty-set', {'copyFrom': 'p1', 'levels': ['challenge', 'steady', 'standard'], 'size': 3})
    assert r['group'] == r['ids'] + [x] and serve._group_ids() == r['group']
    assert [serve.difficulty_of(serve.SETTINGS.values(p)) for p in r['ids']] == ['steady', 'standard', 'challenge']
    assert call(server, 'status')['group'] == r['group']


def test_single_set_is_the_normal_list(server, data):
    # 取り合う相手がいなければ、ふつうに組み立てたリストと同じ
    for vals in ({}, {'played_ratio': 50, 'playlist_size': 9}, {'played_ratio': 0, 'playlist_size': 4, 'challenge_max': 1}):
        pid = group(1, **vals)[0]
        view = serve.SETTINGS.view(pid)
        normal = serve._make_list(view)
        lst = serve._draft([view])[pid]['list']
        assert set(ids(lst['items'])) == set(ids(normal['items'])), vals
        for k in ('slots', 'short', 'counts', 'summary', 'filters'):
            assert lst[k] == normal[k], (vals, k)
        assert ids(lst['runnersUp']) == ids(normal['runnersUp'])
        serve.SETTINGS.delete_preset(pid)


# ---- 各セットの条件 ----

def test_played_ratio_slots(server, data):
    # プレイ済みの比率の枠は最後の曲数で守る（比率 50% で 4 曲なら 2 と 2）
    a, b = group(2, played_ratio=50, playlist_size=4)
    redraft(server, a)
    for pid in (a, b):
        lst = saved(pid)
        assert lst['slots'] == {'played': 2, 'unplayed': 2} and lst['short'] == 0
        assert sum(c['kind'] == 'played' for c in lst['items']) == 2, pid
    # 比率 0% と 100% を組にしても、それぞれの枠のまま
    serve.SETTINGS.update({'played_ratio': 0}, preset=a)
    serve.SETTINGS.update({'played_ratio': 100}, preset=b)
    redraft(server, a)
    assert {c['kind'] for c in saved(a)['items']} == {'unplayed'} and {c['kind'] for c in saved(b)['items']} == {'played'}


def low_clear(monkeypatch, lids, value=0.5):
    """推定のうち lids の曲のクリアの見込みを value にする（作り物のデータは挑戦の記録が少なく、見込みを求めないので）"""
    get = serve.ENGINE.get

    def patched(view=None):
        model, est = get(view)
        return model, est and [{**c, 'clear': value} if c['leaderboard_id'] in lids else c for c in est]
    monkeypatch.setattr(serve.ENGINE, 'get', patched)


def test_difficulty_conditions_kept(server, data, monkeypatch):
    # 着実（未プレイの pass の上限 75%点・挑戦枠 0・クリアの見込み 80% 以上）・標準（挑戦枠 1）・チャレンジング（挑戦枠 2）
    top = ranked(server, 3, played_ratio=100, challenge_max=0)
    low_clear(monkeypatch, set(top[:2]))   # 挑戦枠でない、一番値の高いプレイ済みの2曲は、クリアの見込み 50%
    r = call(server, 'presets/difficulty-set', {'copyFrom': 'p1', 'levels': ['steady', 'standard', 'challenge'], 'size': 6})
    steady, std, hard = (saved(p) for p in r['ids'])
    s = steady['filters']
    # （見込みの低い2曲はほかのセットが取ったので、着実の外した理由は「ほかのセットのリストにある」の方に数える）
    assert s['passCap'] is not None and s['passOut'] > 0 and s['minClear'] == 80
    assert not any(c['challenge'] for c in steady['items'])
    assert all(c['pass_rating'] <= s['passCap'] for c in steady['items'] if c['kind'] == 'unplayed')
    assert all(c['clear'] is None or c['clear'] >= 0.8 for c in steady['items'])
    assert not set(top[:2]) & songs(r['ids'][0])                        # 見込みの低い曲は着実には入らない
    assert set(top[:2]) <= songs(r['ids'][1]) | songs(r['ids'][2])      # ほかのセットが取る
    assert sum(c['challenge'] for c in std['items']) <= 1 and sum(c['challenge'] for c in hard['items']) == 2
    assert [len(x['items']) for x in (steady, std, hard)] == [6, 6, 6]
    for x in (steady, std, hard):
        assert x['filters']['challenge'] == sum(c['challenge'] for c in x['items'])


def test_no_same_song_even_other_difficulty(server, data, monkeypatch):
    p = ranked(server, 7, played_ratio=100)
    a, b = group(2, played_ratio=100, playlist_size=3)
    # B が最初に取るはずの曲（p[1]）を、A が最初に取る曲（p[0]）の別難易度にする（hash を同じにする）
    h = serve.STORE.q1('SELECT hash FROM leaderboards WHERE leaderboard_id=?', (p[0],))['hash']
    sql('UPDATE leaderboards SET hash=? WHERE leaderboard_id=?', (h, p[1]))
    monkeypatch.setattr(serve, 'ENGINE', Engine(serve.STORE, serve.SETTINGS))
    redraft(server, a)
    # A: p0 → B: p2, p3（p1 は A の曲の別難易度）→ A: p4, p5（p1 は同じ曲の別難易度なので1つまで）→ B: p6
    assert songs(a) == {p[0], p[4], p[5]} and songs(b) == {p[2], p[3], p[6]}
    assert not hashes(a) & hashes(b)


def test_pinned_song_is_in_from_the_start(server, data):
    p = ranked(server, 6, played_ratio=100)
    a, b = group(2, played_ratio=100, playlist_size=3)
    # B が A の一番の曲を固定: B に最初から入り（順番は使わない）、A は取れない
    assert call(server, 'recommend/action', {'action': 'pin', 'id': p[0], 'preset': b})['ok']
    redraft(server, a)
    # A: p1 → B: p2, p3（固定と合わせて 3 曲）→ A: p4, p5
    assert songs(b) == {p[0], p[2], p[3]} and songs(a) == {p[1], p[4], p[5]}
    pinned = [c for c in saved(b)['items'] if c['pinned']]
    assert ids(pinned) == [p[0]] and pinned[0]['pick'] == 'pin'
    # 外した曲（そのセットだけ・アプリを閉じるまで）は、交互に選ぶ時もそのセットには入らない（ほかのセットは取れる）
    assert call(server, 'recommend/action', {'action': 'remove', 'id': p[1], 'preset': a})['ok']
    redraft(server, a)
    # A: p2 → B: p1, p3 → A: p4, p5
    assert songs(a) == {p[2], p[4], p[5]} and songs(b) == {p[0], p[1], p[3]}


def test_runs_out_of_songs(server, data):
    # 選べる曲が無いセットは飛ばす（ほかのセットは続ける）
    a, b = group(2, playlist_size=5)
    serve.SETTINGS.update({'modes': ['OneSaber']}, preset=a)
    redraft(server, b)
    assert saved(a)['items'] == [] and saved(a)['short'] == 5
    assert songs(b) == set(ids(serve._make_list(serve.SETTINGS.view(b))['items'])) and saved(b)['short'] == 0
    # 両方とも曲数に届かない時は、候補を交互に分け合う（数の差は1曲まで）。候補が1つのリストの曲数（最大 50）に収まるように、
    # ★の範囲の上限で絞る（作り物のデータの★は 3.0〜10.9。2026-10-25 までの「プレイ済みの最高★ 4.9 ＋ 3」と同じ候補になる）
    serve.SETTINGS.update({'modes': ['Standard'], 'playlist_size': 50, 'star_max': 7.9}, preset=a)
    serve.SETTINGS.update({'playlist_size': 50, 'star_max': 7.9}, preset=b)
    every = set(ranked(server, 50, star_max=7.9))
    redraft(server, a)
    x, y = saved(a), saved(b)
    assert not songs(a) & songs(b) and songs(a) | songs(b) == every
    assert abs(len(x['items']) - len(y['items'])) <= 1 and x['short'] > 0 and y['short'] > 0
    assert len(x['items']) + x['short'] == 50


def test_pins_over_the_slot_keep_the_size(server, data):
    # 固定した曲がプレイ済みの枠を超えていても、曲数より多くはしない（選んだ曲は固定と同じ仕組みで入れるため）
    p = ranked(server, 8, played_ratio=100)
    a, b = group(2, played_ratio=50, playlist_size=4)
    for lid in p[:3]:
        call(server, 'recommend/action', {'action': 'pin', 'id': lid, 'preset': a})
    redraft(server, a)
    assert len(saved(a)['items']) == 4 and set(p[:3]) <= songs(a) and saved(a)['short'] == 0
    assert len(saved(b)['items']) == 4 and not songs(a) & songs(b)


# ---- 難しさ違いのセットを作る・作り直す ----

def test_difficulty_set_drafts_the_whole_group(server, data):
    x = group(1, playlist_size=5)[0]   # 前からある組のセット
    call(server, 'recommend', {'preset': x})
    sql('UPDATE rec_lists SET made_at=1 WHERE preset=?', (x,))
    before = songs(x)
    r = call(server, 'presets/difficulty-set', {'copyFrom': 'p1'})
    steady, hard = r['ids']
    assert r['group'] == [steady, hard, x] and made_at(x) > 1   # 前からある組のセットも選び直した
    assert not songs(steady) & songs(hard) and not songs(steady) & songs(x) and not songs(hard) & songs(x)
    assert songs(x) != before
    for pid in r['group']:
        rec = call(server, 'recommend', {'preset': pid})
        assert rec['pending'] is None and len(rec['items']) == (5 if pid == x else 7)
    assert saved('p1')['items'] and not serve.SETTINGS.values('p1')['exclude_other_sets']   # 組でない元のセットはそのまま


def test_rebuild_group_and_this_set_only(server, data):
    p = ranked(server, 7, played_ratio=100)
    a, b = group(2, played_ratio=100, playlist_size=3)
    redraft(server, a)
    assert songs(a) == {p[0], p[3], p[4]} and songs(b) == {p[1], p[2], p[5]}
    # データが変わった（A の一番の曲を今後出さない。DB を直接＝ほかのセットの保存したリストからは抜かない）
    sql("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', 0)", (p[0],))
    sql('UPDATE rec_lists SET made_at=1')
    # このセットだけ（今までどおり）: B は A の保存したリストの曲を外して作り直す。A はそのまま
    r = rebuild(server, b)
    assert r['ok'] and 'group' not in r and songs(b) == {p[1], p[2], p[5]} and made_at(a) == 1 and made_at(b) > 1
    assert rebuild(server, b, group=False)['ok'] and made_at(a) == 1
    # 組全部: 交互に選び直すので、B のリストも変わる
    sql('UPDATE rec_lists SET made_at=1')
    r = rebuild(server, b, group=True)
    assert r['ok'] and r['preset'] == b and r['group'] == [a, b] and set(ids(r['items'])) == songs(b)
    assert songs(a) == {p[1], p[4], p[5]} and songs(b) == {p[2], p[3], p[6]}
    assert made_at(a) > 1 and made_at(b) > 1


def test_rebuild_group_errors(server, data):
    a = group(1)[0]
    # 組でない（このセットがオフ・オンのセットが1つだけ）・group が真偽でない
    for body in ({'preset': 'p1', 'group': True}, {'preset': a, 'group': True}, {'preset': a, 'group': 'yes'}, {'preset': a, 'group': 1}):
        assert call(server, 'recommend/rebuild', body) == {'ok': False, 'error': 'bad-request'}, body
    b = group(1)[0]
    assert call(server, 'recommend/rebuild', {'preset': 'p1', 'group': True})['error'] == 'bad-request'   # p1 は組に入っていない
    assert call(server, 'recommend/rebuild', {'preset': a, 'group': True})['group'] == [a, b]
    assert call(server, 'recommend/rebuild', {'preset': 'p1', 'group': None})['ok']
