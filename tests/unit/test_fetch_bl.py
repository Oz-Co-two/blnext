# BeatLeader の応答をそろえる処理（core/fetch/beatleader.py）: 想定外の値・欠けた項目は読み飛ばしてアプリを止めない
import pytest
from core.fetch import beatleader as bl
from core.fetch.http import BLClient
from fakebl import FakeBL


class Stub:
    """get に渡された引数を残し、決めた応答を返すだけの通信の代わり"""
    def __init__(self, res):
        self.res, self.calls = res, []

    def get(self, path, **params):
        self.calls.append((path, params))
        return self.res


def test_numbers():
    # 数にできない値・NaN・無限大は None（DB に入れても計算を壊さない）
    assert [bl._f(v) for v in ('0.5', 3, None, 'x', 'NaN', 'inf', [])] == [0.5, 3.0, None, None, None, None, None]
    assert [bl._i(v) for v in ('12.7', 4, None, 'x', 'NaN')] == [12, 4, None, None, None]


def test_int_of_infinity_is_none():
    # 無限大（"Infinity"・1e400）は値なし。1行の変な値で取得全体を止めない
    assert bl._i('Infinity') is None


def test_modifier_json():
    # モディファイアごとの★・倍率は数だけを JSON にする（id・真偽値・文字は捨てる）。何も残らなければ None
    assert bl._ratings_json({'id': 7, 'ssStars': 7.5, 'fsStars': 8, 'flag': True, 'name': 'x'}) == '{"ssStars":7.5,"fsStars":8}'
    assert bl._ratings_json({'id': 7}) is None and bl._ratings_json(None) is None
    assert bl._values_json({'modifierId': 3, 'sf': 0.2, 'nf': -1.0, 'x': None}) == '{"sf":0.2,"nf":-1.0}'
    assert bl._values_json([]) is None


def test_norm_map_keeps_ranked_and_skips_broken():
    # /maps の1曲: ランク（status=3）で leaderboardId のある難易度だけ。壊れた難易度は飛ばして他は残す
    good = {'leaderboardId': 'LBa', 'status': 3, 'difficultyName': 'Expert', 'modeName': 'Standard', 'stars': 5.5,
            'modifiersRating': {'id': 1, 'ssStars': 6.1}, 'modifierValues': {'modifierId': 2, 'da': 0.0}}
    m = {'id': 'm1', 'hash': 'ABC', 'name': 'Song', 'uploadTime': 0, 'duration': 99,
         'difficulties': [good, {**good, 'leaderboardId': 'LBb', 'status': 0}, {**good, 'leaderboardId': None}, None, 'junk']}
    rows = bl.norm_map(m)
    assert [r['leaderboard_id'] for r in rows] == ['LBa']
    r = rows[0]
    assert r['hash'] == 'abc' and r['upload_time'] is None and r['duration'] == 99   # 0 は「無い」。難易度に長さが無ければ曲の値
    assert r['modifiers_rating'] == '{"ssStars":6.1}' and r['modifier_values'] == '{"da":0.0}'
    assert bl.norm_map({'id': 'm2'}) == []


@pytest.mark.parametrize('fn', [bl.norm_player, bl.norm_score, bl.norm_accgraph, bl.norm_attempt, bl.norm_history])
def test_rows_without_key_are_skipped(fn):
    # 鍵になる項目（id・譜面・時刻）の無い行や dict でない行は読み飛ばす
    assert bl.each([{}, None, 'x', 5, {'leaderboard': 'x'}], fn, 'test') == []


def test_scores_page_skips_broken_rows():
    # 1ページに壊れた行があっても正しい行は取り込む。譜面の情報が無いスコアも読む（時刻は timepost→timeset の順）
    good = {'leaderboardId': 'LB9', 'accuracy': 0.95, 'timeset': '1690000000', 'timepost': 0, 'fullCombo': True}
    c = Stub({'data': [good, {}, None]})
    rows, total = bl.scores_page(c, '1', 1, sort='lastTryTime')
    assert total is None and len(rows) == 1
    row, lbrow = rows[0]
    assert row['leaderboard_id'] == 'LB9' and row['timeset'] == 1690000000 and row['full_combo'] == 1 and lbrow is None
    assert c.calls[0][1]['sortBy'] == 'lastTryTime'


def test_response_shapes():
    # {"metadata","data"} と配列のどちらも読む。想定外の形は空、total は None
    assert bl._data({'data': [1]}) == [1] and bl._data([2]) == [2]
    assert bl._data({'data': None}) == [] and bl._data(None) == [] and bl._data('x') == []
    assert bl._total({'metadata': {'total': '12'}}) == 12
    assert [bl._total(x) for x in ({}, [], None, {'metadata': {'total': 'x'}}, {'metadata': None})] == [None] * 5


def test_norm_player_and_attempt():
    # 国は小文字、scoreStats が無くても読む。アテンプトの時刻は timeset→timepost→startTime の順
    p = bl.norm_player({'id': 123, 'country': 'JP', 'pp': '1.5'})
    assert p['player_id'] == '123' and p['country'] == 'jp' and p['pp'] == 1.5 and p['last_ranked_score_time'] is None
    assert bl.norm_attempt({'leaderboardId': 'L', 'timepost': 0, 'startTime': '1690000000'})['timeset'] == 1690000000


def test_ranked_maps_page_counts_raw_rows():
    # ページの件数は曲の数（ランクの難易度の数ではない）で返す。続きのページがあるかの判断に使う
    m = {'id': 'm', 'hash': 'h', 'difficulties': [{'leaderboardId': 'a', 'status': 3}, {'leaderboardId': 'b', 'status': 3}]}
    rows, count, total = bl.ranked_maps_page(Stub({'metadata': {'total': 7}, 'data': [m, 'junk', {**m, 'difficulties': []}]}), 1)
    assert [r['leaderboard_id'] for r in rows] == ['a', 'b'] and count == 3 and total == 7


def test_search_players():
    # 名前で探す（作り物の BeatLeader）
    fake = FakeBL()
    rows = bl.search_players(BLClient('t', opener=fake), 'Player 1')
    assert [r['player_id'] for r in rows] == ['9001']
    assert fake.calls == [('/players', {'search': 'Player 1', 'count': '10', 'sortBy': 'pp'})]


def test_norm_map_features_and_score_id():
    # 譜面の特徴（一覧に出す）と、自分のスコアの ID（リプレイのページを開く）
    d = {'leaderboardId': 'LBa', 'status': 3, 'difficultyName': 'Expert', 'modeName': 'Standard', 'stars': 5.5,
         'njs': 18, 'nps': 8.75, 'bombs': 3, 'walls': 'x', 'peakSustainedEBPM': 235.2}
    r = bl.norm_map({'id': 'm1', 'hash': 'A', 'name': 'S', 'bpm': 190, 'tags': ' Speed, dance-style ,,electronic', 'difficulties': [d]})[0]
    assert (r['njs'], r['nps'], r['bpm'], r['bombs'], r['walls'], r['peak_ebpm']) == (18.0, 8.75, 190.0, 3, None, 235.2)
    assert r['tags'] == 'speed,dance-style,electronic'
    assert (r['slider_percentage'], r['note_span']) == (None, None)   # 2026-10 のリウェイトより前の応答には無い
    r = bl.norm_map({'id': 'm1', 'hash': 'A', 'difficulties': [{**d, 'sliderPercentage': 0.25, 'noteSpan': 131.9}]})[0]
    assert (r['slider_percentage'], r['note_span']) == (0.25, 131.9)   # 譜面ごとの精度の曲線の材料
    assert bl.norm_map({'id': 'm1', 'hash': 'A', 'tags': 5, 'difficulties': [d]})[0]['tags'] is None
    assert bl.norm_map({'id': 'm1', 'hash': 'A', 'tags': ' , ', 'difficulties': [d]})[0]['tags'] is None
    row, _ = bl.norm_score({'id': 35076769, 'leaderboardId': 'LBa', 'accuracy': 0.9})
    assert row['score_id'] == 35076769
    assert bl.norm_score({'leaderboardId': 'LBa'})[0]['score_id'] is None


@pytest.mark.parametrize('replay, want', [
    ('https://cdn.replays.beatleader.com/35076769-76561198000000000-ExpertPlus-Standard-ABC.bsor', True),
    ('https://cdn.replays.beatleader.xyz/1-2-Expert-Standard-H.bsor', True),
    ('https://evil.example/35076769-1-ExpertPlus-Standard-ABC.bsor', False),              # 別のホスト
    ('https://cdn.replays.beatleader.com.evil.example/1-2-Expert-Standard-H.bsor', False),
    ('http://cdn.replays.beatleader.com/1-2-Expert-Standard-H.bsor', False),             # http
    ('https://cdn.replays.beatleader.com/1-2-Expert-Standard-H.json', False),            # .bsor でない
    ('', False), (None, False), (5, False), ({'url': 'x'}, False),
])
def test_norm_score_replay_url(replay, want):
    # 自己ベストのリプレイの URL（頭を読んで JD を出す）は、BeatLeader の置き場所の .bsor の時だけ残す。
    # API の応答をそのまま信じて任意の場所へ通信しないため
    row, _ = bl.norm_score({'id': 1, 'leaderboardId': 'LBa', 'accuracy': 0.9, 'replay': replay})
    assert row['replay_url'] == (replay if want else None)


def test_norm_score_without_replay():
    assert bl.norm_score({'id': 1, 'leaderboardId': 'LBa'})[0]['replay_url'] is None


def test_beatleader_page_urls():
    assert bl.leaderboard_url('22717x71') == 'https://beatleader.com/leaderboard/global/22717x71/1'
    assert bl.leaderboard_url('a/b?c') == 'https://beatleader.com/leaderboard/global/a%2Fb%3Fc/1'   # 区切りの文字は混ぜない
    assert bl.replay_url(35076769) == 'https://replay.beatleader.com/?scoreId=35076769'
