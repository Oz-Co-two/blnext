# serve.py の一覧の並び（画面の一覧の順＝書き出す順）: recommend/order（見出しで並べ替え・ドラッグ・Alt+↑↓・基本の並びに戻す）、
# 操作（外す・固定・入れ替え・今後出さない・戻す）では手で並べた順を保つ、作り直す・リストに効く設定の変更では基本の並び。
# 準備（作り物のデータ・サーバー・呼び方）は test_serve_data.py の物を使う
import json

from core.estimate.order import default_order, merge_order
import serve
from test_serve_data import act, built, call, data, ids, read_json, server, sql   # noqa: F401（built・data・server は fixture）


def saved_ids(preset='p1'):
    return ids(json.loads(serve.STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (preset,))['payload'])['items'])


def manual(port):
    """基本の並びの逆に並べた所から始める（作った時刻は昔＝1 にして、作り直していないことを madeAt で見る）。返り値 (最初の応答, 並べた順)"""
    first = call(port, 'recommend')
    rev = ids(first['items'])[::-1]
    sql("UPDATE rec_lists SET made_at=1 WHERE preset='p1'")
    r = call(port, 'recommend/order', {'ids': rev})
    assert r['ok'] and ids(r['items']) == rev
    return first, rev


def kept(order_ids, r):
    """手で並べた順（order_ids）を保ち、新しく入った曲だけを基本の並びの位置に入れた並びか"""
    now = ids(r['items'])
    assert [i for i in now if i in order_ids] == [i for i in order_ids if i in now]
    assert now == ids(merge_order(order_ids, r['items']))


def test_first_list_is_default_order(server, data):
    r = call(server, 'recommend')
    assert len(r['items']) > 3 and ids(r['items']) == ids(default_order(r['items'])) and r['manualOrder'] is False


def test_order_saves_without_rebuilding(server, data):
    first, rev = manual(server)
    assert saved_ids() == rev
    r = call(server, 'recommend')   # 開き直しても同じ順。作り直していない（作った時刻・元にしたデータの時刻はそのまま）
    assert ids(r['items']) == rev and r['manualOrder'] is True
    assert r['madeAt'] == 1 and r['dataAt'] == first['dataAt'] and r['pending'] is None
    assert sorted(map(json.dumps, r['items'])) == sorted(map(json.dumps, first['items']))   # 曲の中身は変えない（並びだけ）
    # 基本の並びに戻す（これも作り直さない）
    r = call(server, 'recommend/order', {'default': True})
    assert ids(r['items']) == ids(first['items']) and r['manualOrder'] is False and r['madeAt'] == 1
    assert saved_ids() == ids(first['items'])


def test_order_bad_requests_change_nothing(server, data):
    _, rev = manual(server)
    for body in ({'ids': rev[1:]}, {'ids': rev + ['LB999']}, {'ids': rev[:-1] + ['LB999']}, {'ids': rev + rev[:1]},
                 {'ids': rev[:-1] + [rev[0]]}, {'ids': ','.join(rev)}, {'ids': [1] * len(rev)}, {'ids': None}, {},
                 {'default': 'yes'}, {'ids': rev, 'preset': ['p1']}):
        assert call(server, 'recommend/order', body) == {'ok': False, 'error': 'bad-request'}, body
    assert saved_ids() == rev


def test_export_follows_the_order(server, data):
    # 書き出すプレイリストの曲の順＝並べた順（同じ曲の別難易度は、先に出てきた所の1曲にまとまる）
    _, rev = manual(server)
    r = call(server, 'export')
    assert r['ok']
    hashes = {x['leaderboard_id']: (x['hash'] or '').lower() for x in serve.STORE.q('SELECT leaderboard_id, hash FROM leaderboards')}
    want = list(dict.fromkeys(hashes[i] for i in rev))
    assert [s['hash'] for s in read_json(r['path'])['songs']] == want
    # 基本の並びに戻すと、書き出す順も戻る
    base = ids(call(server, 'recommend/order', {'default': True})['items'])
    r = call(server, 'export')
    assert [s['hash'] for s in read_json(r['path'])['songs']] == list(dict.fromkeys(hashes[i] for i in base))


def test_actions_keep_manual_order(server, data):
    first, rev = manual(server)
    # 固定（曲は変わらない）→ 手の順のまま
    r = act(server, 'pin', rev[0])
    assert ids(r['items']) == rev and r['manualOrder'] is True
    r = act(server, 'unpin', rev[0])
    assert ids(r['items']) == rev
    # 外す → 残りは手の順のまま、次点から入った曲は基本の並びの位置
    now = ids(r['items'])
    r = act(server, 'remove', rev[2])
    assert rev[2] not in ids(r['items']) and len(r['items']) == len(rev) and set(ids(r['items'])) - set(rev)
    kept(now, r)
    # 入れ替える（次点の1曲目と）
    now, other = ids(r['items']), r['runnersUp'][0]['leaderboard_id']
    r = act(server, 'swap', now[1], **{'with': other})
    assert other in ids(r['items']) and now[1] not in ids(r['items'])
    kept(now, r)
    # 今後出さない
    now = ids(r['items'])
    r = act(server, 'never', now[0])
    assert now[0] not in ids(r['items'])
    kept(now, r)
    # 外した分を戻す
    now = ids(r['items'])
    r = act(server, 'reset', now[0])
    assert rev[2] in ids(r['items'])
    kept(now, r)
    assert r['madeAt'] > 1   # 操作は今のデータで作り直す（並びだけは保つ）


def test_rebuild_and_list_settings_return_to_default(server, data):
    _, rev = manual(server)
    # リストに効かない設定（文字の大きさ）を変えても並びは保つ
    assert call(server, 'settings/save', {'changes': {'ui_scale': 130}})['ok']
    assert ids(call(server, 'recommend')['items']) == rev
    # 「作り直す」で基本の並び
    r = call(server, 'recommend/rebuild')
    assert ids(r['items']) == ids(default_order(r['items'])) and r['manualOrder'] is False
    # リストに効く設定（曲数）を変えて作り直した時も基本の並び（手で並べた後でも）
    _, rev = manual(server)
    assert call(server, 'settings/save', {'changes': {'playlist_size': 10}})['ok']
    r = call(server, 'recommend')
    assert len(r['items']) == 10 and ids(r['items']) == ids(default_order(r['items'])) and r['manualOrder'] is False


def test_order_is_per_preset(server, data):
    pid = serve.SETTINGS.add_preset('ワンセイバー', copy_from='p1')
    other = ids(call(server, 'recommend', {'preset': pid})['items'])
    _, rev = manual(server)
    assert ids(call(server, 'recommend', {'preset': pid})['items']) == other   # ほかのセットの並びは変わらない
    r = call(server, 'recommend/order', {'preset': pid, 'ids': other[::-1]})
    assert ids(r['items']) == other[::-1] and r['preset'] == pid and saved_ids() == rev
