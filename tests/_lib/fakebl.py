# テスト用の作り物の BeatLeader（BLClient の opener に渡す）。プレイヤー・譜面・スコアはすべて作り物。
# 自分のスコアのリプレイ（.bsor）の URL への要求には、先頭（info まで）だけを返す（JD は譜面ごとに 18〜22）
import gzip, hashlib, io, json
from urllib.parse import urlsplit, parse_qs
import urllib.error

import bsor
from core.estimate import ppcalc


class Resp:
    def __init__(self, body, status=200, headers=None):
        self._b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = headers or {}
        self.read_args = []   # read に渡された数（先頭だけ読んでいるかの確かめ）

    def read(self, n=-1):
        self.read_args.append(n)
        return self._b if n is None or n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Stream(Resp):
    """本物と同じように少しずつ読める応答（全曲データ）"""
    def __init__(self, body, headers=None):
        super().__init__(body, headers=headers)
        self._f = io.BytesIO(body)

    def read(self, n=-1):
        return self._f.read(n)


def _pb_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb_int(fn, v):
    return _pb_varint(fn << 3) + _pb_varint(v)


def _pb_bytes(fn, b):
    return _pb_varint(fn << 3 | 2) + _pb_varint(len(b)) + b


def song_details_gz(songs, scrape, version=3):
    """全曲データ（songDetails2_v3.gz の形）を作る。songs は (ハッシュ, rankedStates, [(characteristic, difficulty, ★×100)]) の並び"""
    body = _pb_int(1, version) + _pb_int(2, scrape) + _pb_bytes(3, b''.join(bytes.fromhex(h) for h, _, _ in songs))
    for i, (_h, states, diffs) in enumerate(songs):
        s = _pb_int(5, i + 1)
        for ch, df, stars in diffs:
            s += _pb_bytes(11, _pb_int(1, ch) + _pb_int(2, df) + _pb_int(5, stars))
        body += _pb_bytes(4, s + _pb_int(13, states))
    return gzip.compress(body)


def _hash(lb):
    return hashlib.sha1(lb.encode()).hexdigest()


def _score_id(lb):
    return int(_hash(lb)[:6], 16)


def _diff(lb_id, stars=6.0):
    return {'id': 1, 'value': 9, 'mode': 1, 'difficultyName': 'ExpertPlus', 'modeName': 'Standard', 'status': 3,
            'stars': stars, 'accRating': stars * 1.2, 'passRating': stars * 0.5, 'techRating': stars * 0.3,
            'predictedAcc': 0.95, 'sliderPercentage': 0.0, 'noteSpan': 110.0,
            'type': 1, 'rankedTime': 1700000000, 'notes': 500, 'duration': 120,
            'njs': round(12 + stars, 1), 'nps': round(stars * 0.9, 2), 'bombs': 12, 'walls': 40,
            'peakSustainedEBPM': 150 + stars * 20,
            'leaderboardId': lb_id, 'modifiersRating': None, 'modifierValues': None}


class FakeBL:
    """players: 国内ランキング順の [{id, name, pp, lastRanked}]、self_id。maps: [(lb_id, stars, uploadTime)]"""
    def __init__(self, n_players=30, self_rank=15, n_maps=40):
        self.self_id = '9%03d' % self_rank
        self.players = [{'id': '9%03d' % r, 'name': 'Player %d' % r, 'pp': 10000 - r * 10.0, 'lastRanked': 1000 + r}
                        for r in range(1, n_players + 1)]
        self.maps = [('LB%03d' % i, 3 + i * 0.1, 1600000000 + i * 1000) for i in range(n_maps)]
        self.my_scores = {lb: {'acc': 0.9 + (i % 5) * 0.01, 'time': 1690000000 + i} for i, (lb, _, _) in enumerate(self.maps[:20])}
        self.calls = []
        self.song_details = None   # 全曲データの (gz, ETag)。None なら 404
        self.file_requests = []    # 全曲データへの要求の If-None-Match（calls は BeatLeader への通信だけ）
        self.fail = {}        # パス → 失敗させる回数
        self.status_for = {}  # パス → 返す状態コード（ずっと）
        self.names = {}       # leaderboard_id → 曲名（無ければ 'Song <id>'）
        self.tags = {}        # leaderboard_id → BeatSaver のタグ（無ければ偶数・奇数で2通り）
        self.jds = {}         # leaderboard_id → リプレイの JD（無ければ jd_of の値）
        self.pp_scale = 1.0   # 返す PP の倍率（1 以外＝アプリの式と合わない BeatLeader のふり）

    def rank_of(self, pid):
        return next(i for i, p in enumerate(self.players, 1) if p['id'] == pid)

    def jd_of(self, lb):
        """自己ベストのリプレイの JD（譜面ごとに 18.0〜22.0 の決まった値）"""
        return self.jds.get(lb, 18 + int(_hash(lb)[:4], 16) % 41 / 10)

    def replay_path(self, lb):
        return '/%d-%s-ExpertPlus-Standard-%s.bsor' % (_score_id(lb), self.self_id, _hash(lb).upper())

    def _player(self, p, r):
        return {'id': p['id'], 'name': p['name'], 'country': p.get('country', 'JP'), 'pp': p['pp'], 'rank': p.get('rank', r * 10),
                **({'profileSettings': {'showStatsPublic': p['statsPublic']}} if 'statsPublic' in p else {}),
                'countryRank': p.get('countryRank', r),
                'accPp': p['pp'] * 0.7, 'passPp': p['pp'] * 0.2, 'techPp': p['pp'] * 0.1,
                'lastWeekPp': p['pp'] - 5, 'lastWeekRank': r * 10 + 3, 'lastWeekCountryRank': r + 1,
                'scoreStats': {'lastRankedScoreTime': p['lastRanked'], 'rankedPlayCount': p.get('rankedPlayCount', 50)}}

    def pp_of(self, lb, acc):
        """そのスコアの PP（アプリの式で計算。pp_scale を掛ける）"""
        stars = next(st for l, st, _ in self.maps if l == lb)
        return ppcalc.pp_from_difficulty(acc, _diff(lb, stars))['pp'] * self.pp_scale

    def _score(self, lb, s):
        stars = next(st for l, st, _ in self.maps if l == lb)
        return {'id': _score_id(lb), 'accuracy': s['acc'], 'pp': self.pp_of(lb, s['acc']), 'fcAccuracy': s['acc'] + 0.01, 'fcPp': 0,
                'weight': 1, 'accPP': 0, 'passPP': 0, 'techPP': 0, 'modifiers': '', 'timeset': str(s['time']), 'timepost': s['time'],
                'playCount': 3, 'lastTryTime': s['time'], 'missedNotes': 1, 'badCuts': 0, 'bombCuts': 0, 'wallsHit': 0,
                'fullCombo': False, 'leaderboardId': lb, 'replay': 'https://cdn.replays.beatleader.com' + self.replay_path(lb),
                'leaderboard': {'id': lb, 'song': {'hash': _hash(lb).upper(), 'name': self.names.get(lb, 'Song ' + lb), 'mapper': 'M'}, 'difficulty': _diff(lb, stars)}}

    def publish_song_details(self, scrape, etag, stars=None, extra=()):
        """今の譜面（self.maps）の全曲データを出す。stars は ★を差し替える {譜面: ★}。extra は足す曲（song_details_gz の songs の形）"""
        stars = stars or {}
        songs = [(_hash(lb), 2, [(1, 4, round(stars.get(lb, st) * 100))]) for lb, st, _ in self.maps]
        self.song_details = (song_details_gz(songs + list(extra), scrape), etag)

    def __call__(self, req, timeout):
        u = urlsplit(req.full_url)
        if u.netloc == 'cdn.jsdelivr.net':
            tag = req.get_header('If-none-match')
            self.file_requests.append(tag)
            if self.song_details is None:
                raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, io.BytesIO(b''))
            gz, etag = self.song_details
            if tag and tag == etag:
                raise urllib.error.HTTPError(req.full_url, 304, 'nm', {}, io.BytesIO(b''))
            return Stream(gz, {'ETag': etag})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        self.calls.append((path, q))
        if self.fail.get(path, 0) > 0:
            self.fail[path] -= 1
            raise urllib.error.URLError('fake offline')
        if path in self.status_for:
            code = self.status_for[path]
            raise urllib.error.HTTPError(req.full_url, code, 'x', {'X-Rate-Limit-Reset': ''}, io.BytesIO(b''))
        if path.endswith('.bsor'):
            # 自己ベストのリプレイ（URL は _score の replay）。Range があれば 206（本物と同じ）。中身は先頭の info だけ
            lb = next((l for l, _, _ in self.maps if self.replay_path(l) == path), None)
            if lb is None:
                raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, io.BytesIO(b''))
            head = bsor.make_header(self.jd_of(lb), song=self.names.get(lb, 'Song ' + lb), hash=_hash(lb).upper(),
                                    playerID=self.self_id)
            return Resp(head, status=206 if req.get_header('Range') else 200)
        if path.startswith('/player/') and path.count('/') == 2:
            pid = path.split('/')[2]
            p = next((x for x in self.players if x['id'] == pid), None)
            if not p:
                raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, io.BytesIO(b''))
            return Resp(self._player(p, self.rank_of(pid)))
        if path == '/players':
            if 'search' in q:
                return Resp({'metadata': {'total': 1}, 'data': [self._player(self.players[0], 1)]})
            page, count = int(q.get('page', 1)), int(q.get('count', 100))
            rows = [self._player(p, r) for r, p in enumerate(self.players, 1)][(page - 1) * count: page * count]
            return Resp({'metadata': {'total': len(self.players), 'page': page, 'itemsPerPage': count}, 'data': rows})
        if path == '/maps':
            page, count = int(q.get('page', 1)), int(q.get('count', 500))
            rows = [{'id': lb, 'hash': _hash(lb), 'name': self.names.get(lb, 'Song ' + lb), 'author': 'A', 'mapper': 'M', 'uploadTime': up,
                     'bpm': 100 + int(lb[2:]) * 7 % 120,
                     'tags': self.tags.get(lb, 'tech,electronic' if int(lb[2:]) % 2 else 'speed,dance-style,pop'),
                     'difficulties': [_diff(lb, st), {**_diff(lb + 'u', st), 'status': 0}]}
                    for lb, st, up in self.maps][(page - 1) * count: page * count]
            return Resp({'metadata': {'total': len(self.maps)}, 'data': rows})
        if path.endswith('/scores'):
            items = sorted(self.my_scores.items(), key=lambda kv: -kv[1]['time'])
            page, count = int(q.get('page', 1)), int(q.get('count', 100))
            rows = [self._score(lb, s) for lb, s in items][(page - 1) * count: page * count]
            return Resp({'metadata': {'total': len(items)}, 'data': rows})
        if path.endswith('/accgraph'):
            pid = path.split('/')[2]
            if pid == self.self_id:
                src = self.my_scores
            else:
                r = self.rank_of(pid)
                src = {lb: {'acc': 0.88 + (r % 7) * 0.01 + i * 0.001, 'time': 1690000000 + i}
                       for i, (lb, _, _) in enumerate(self.maps[: 10 + r])}
            return Resp([{'acc': s['acc'], 'leaderboardId': lb, 'diff': 'ExpertPlus', 'mode': 'Standard', 'modifiers': '',
                          'stars': next(st for l, st, _ in self.maps if l == lb), 'pp': self.pp_of(lb, s['acc']), 'timeset': s['time']}
                         for lb, s in src.items()])
        if path.endswith('/history/compact'):
            return Resp([{'timestamp': 1700000000 + d * 86400, 'pp': 9800 + d, 'rank': 150, 'countryRank': 15} for d in range(5)])
        if path.endswith('/scoresstats'):
            return Resp({'metadata': {'total': 2}, 'data': [
                {'leaderboardId': 'LB000', 'accuracy': 0.9, 'pp': 90, 'endType': 1, 'modifiers': '', 'timeset': 1690000100},
                {'leaderboardId': 'LB000', 'accuracy': 0.5, 'pp': 0, 'endType': 2, 'modifiers': '', 'timeset': 1690000200}]})
        raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, io.BytesIO(b''))
