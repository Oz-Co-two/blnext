"""画面のテスト用のデータフォルダ（config/ と data/blnext.db）を作る。作り物の BeatLeader（fakebl.py）から取得して作るので、
本物の取得と同じ形の DB になる。崩れ・安全の確かめのため、とても長い名前・日本語・絵文字・HTML のタグに見える名前を混ぜる。
前回の出力の振り返りが出る状態（出力 → 1曲伸ばす → もう一度取得）まで作る。
自己ベストのリプレイの頭（JD）も読んでおく。ただし1曲だけはリプレイの URL を消し、「次の更新の後に出ます」の表示にする
（返り値の no_replay。最後の取得の後のおすすめに入るプレイ済みの曲の最初の1曲）。
おすすめに入る未プレイの曲の1曲には、クリアできなかった挑戦の記録（失敗・やり直し・途中でやめた・練習）を付け、「未クリア」の
表示にする（返り値の tried）。

  build(data_dir) → {'self_id', 'beat_saber', 'no_replay', 'tried'}   （数秒もかからない。tests/e2e/conftest.py と tools/cdp.py が使う）
"""
import os, time

from fakebl import FakeBL

EVIL = '<img src=x onerror="window.__xss=1">'
LONG = 'とても長いプレイヤー名のテスト_' + 'ABCDEFGHIJ' * 4


def build(data_dir, n_players=60, self_rank=30, n_maps=120):
    from core.fetch import http as bhttp
    from core.fetch.http import BLClient
    from core.fetch.refresh import Refresher
    from core.fetch import replay
    from core.estimate.engine import Engine
    from core.estimate.assemble import assemble
    from core.settings import Settings
    from core.store.db import Store
    from core import review

    fake = FakeBL(n_players=n_players, self_rank=self_rank, n_maps=n_maps)
    fake.players[self_rank - 2]['name'] = EVIL
    fake.players[self_rank]['name'] = LONG
    fake.players[self_rank + 1]['name'] = '日本語の名前🎵'
    fake.names['LB003'] = 'An Extremely Long Song Title That Should Be Cut With An Ellipsis In Narrow Columns (Extended Mix)'
    fake.names['LB005'] = EVIL
    fake.tags['LB005'] = 'tech,' + EVIL
    fake.names['LB040'] = '曲名に日本語と記号 ★☆♪'

    bs = os.path.join(data_dir, 'FakeBeatSaber')
    os.makedirs(os.path.join(bs, 'Playlists'), exist_ok=True)
    open(os.path.join(bs, 'Beat Saber.exe'), 'wb').close()
    s = Settings(os.path.join(data_dir, 'config'))
    s.update({'player_id': fake.self_id, 'pool_above': 20, 'pool_below': 20, 'target_country_rank': 5, 'min_scores': 10,
              'min_overlap': 5, 'max_map_age_years': 0,
              'challenge_max': 50})   # 作り物の未プレイの曲はどれも範囲の外なので、挑戦枠で絞らない
    s.update({'beat_saber_dir': bs}, trusted=True)
    st = Store(os.path.join(data_dir, 'data', 'blnext.db'))
    old = bhttp.GLOBAL_INTERVAL, bhttp.PLAYERS_INTERVAL
    bhttp.GLOBAL_INTERVAL = bhttp.PLAYERS_INTERVAL = 0
    try:
        ref = Refresher(st, s, lambda: BLClient('test', opener=fake))
        ref.run_sync()
        model, est = Engine(st, s).get()
        res = assemble(est, model, s, st)
        review.record_export(st, model, res['items'], res['runnersUp'], s.values(), os.path.join(bs, 'Playlists', 'x.bplist'),
                             int(time.time()) - 100)
        played = [c['leaderboard_id'] for c in res['items'] if c['kind'] == 'played']
        if played:
            fake.my_scores[played[0]] = {'acc': 0.995, 'time': int(time.time()) - 50}
        fake.players[self_rank - 1]['pp'] += 1   # 誰かの順位を動かす（「前回から」の表示）
        ref.run_sync()
        # リプレイの URL を消す1曲（返り値の no_replay）: 最後の取得の後のおすすめ（画面で開いているセット）に入るプレイ済みの
        # 曲の最初の1曲。無ければ次点の、それも無ければ自分のスコアの譜面の ID の最も小さい曲。
        # 前の版の DB から上げた直後の、URL がまだ無いスコアと同じ形になる（画面は「次の更新の後に出ます」）
        model, est = Engine(st, s).get()
        res = assemble(est, model, s, st)
        no_replay = next((c['leaderboard_id'] for c in res['items'] + res['runnersUp'] if c['kind'] == 'played'), None)
        if no_replay is None:
            no_replay = st.q1('SELECT MIN(leaderboard_id) AS id FROM scores WHERE player_id=?', (fake.self_id,))['id']
        with st.tx():
            st.conn.execute('UPDATE scores SET replay_url=NULL WHERE player_id=? AND leaderboard_id=?', (fake.self_id, no_replay))
        # 未クリアの曲（返り値の tried）: 最後の取得の後のおすすめに入る未プレイの曲の最初の1曲に、クリアできなかった挑戦の記録を
        # 2日に分けて入れる（途中でやめた・練習 → 8日後にやり直し・失敗。練習は回数に数えない）。挑戦の記録の取得と同じ形で DB に直接。
        # 出力より前の日時なので、クールダウンには数えない。作り物の記録は少ないので、クリアの見込みは求めない（推定は変わらない）
        tried = next((c['leaderboard_id'] for c in res['items'] if c['kind'] == 'unplayed'), None)
        if tried:
            t = int(time.mktime(time.localtime(time.time() - 10 * 86400)[:3] + (12, 0, 0, 0, 0, -1)))   # 10日前の昼
            with st.tx():
                for acc, et, ts in ((0.66, 4, t), (0.71, 5, t + 600), (0.74, 3, t + 8 * 86400), (0.781, 2, t + 8 * 86400 + 120)):
                    st.conn.execute('INSERT INTO attempts(leaderboard_id, timeset, acc, pp, end_type, modifiers) VALUES(?,?,?,?,?,?)',
                                    (tried, ts, acc, 0, et, ''))
        # ほかの自己ベストはリプレイの頭を読んで JD を入れておく（画面を開いても読みに行く分は無い）
        pairs = [(r['score_id'], r['replay_url']) for r in st.q(
            'SELECT score_id, replay_url FROM scores WHERE player_id=? AND replay_url IS NOT NULL ORDER BY leaderboard_id', (fake.self_id,))]
        for i in range(0, len(pairs), replay.MAX_PER_CALL):
            assert replay.ensure(st, pairs[i:i + replay.MAX_PER_CALL], 'test', opener=fake, sleep=lambda _: None) == 0
    finally:
        bhttp.GLOBAL_INTERVAL, bhttp.PLAYERS_INTERVAL = old
        st.close()
    return {'self_id': fake.self_id, 'beat_saber': bs, 'no_replay': no_replay, 'tried': tried}
