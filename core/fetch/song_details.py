# 全曲データ（kinsi55 さんの BeatSaberScrappedData の songDetails2_v3.gz。gzip した protobuf・約10MB・ほぼ毎日更新。BetterSongList などの Mod が使っている物）で、
# ランク譜面に変化があるかを確かめる（2026-11-06 に利用者が決めた。BeatLeader の /maps を毎日取らないように）。
# **/maps の代わりにはならない**: ★はあるが、PP の計算に要るレーティング・予測精度・モディファイアの値・leaderboard_id が無い。変化があった時に /maps を取る（refresh.py）。
# 形式は kinsi55/BeatSaber_SongDetails（MIT）の Structs/SongProtoContainer.cs・SongProto.cs・SongDifficultyProto.cs に従う:
#   container: 1 formatVersion(=3) 2 scrapeEndedTimeUnix 3 songHashes(20バイト×曲数・songs と同じ順) 4 songs(繰り返し)
#   song: 11 difficulties(繰り返し) 13 rankedStates（ビット）   difficulty: 1 characteristic 2 difficulty 5 BeatLeader の★×100
# 安全のための約束（NLM の song_details.py と同じ）: 取得先の URL はここに固定で、引数では受け取らない。大きさの上限を守る。ネットから来たデータは信用しない
# （読み取りは範囲を確かめ、壊れていれば Unavailable。中身をコードとして扱わない）。ファイルは残さない（ETag だけ覚える）
import hashlib
import json
import urllib.error
import urllib.request
import zlib

from .http import Cancelled

URL = 'https://cdn.jsdelivr.net/gh/kinsi55/BeatSaberScrappedData/songDetails2_v3.gz'
FORMAT_VERSION = 3
MAX_DOWNLOAD = 50 * 1024 * 1024      # 取得する gz の上限（実物は約10MB）
MAX_UNZIP = 400 * 1024 * 1024        # 展開後の上限（実物は約15MB）
TIMEOUT = 30
CHUNK = 1 << 16
STARS_TOLERANCE = 0.011              # ★が変わったと見る差（全曲データは★を 0.01 刻みで持つ。作者の DB の 3968譜面で差は最大 0.005）
RANKED_BL = 2                        # rankedStates の BeatLeader のランクのビット
CHARACTERISTICS = {0: 'Custom', 1: 'Standard', 2: 'OneSaber', 3: 'NoArrows', 4: '90Degree', 5: '360Degree', 6: 'Lightshow', 7: 'Lawless'}
DIFFICULTIES = {0: 'Easy', 1: 'Normal', 2: 'Hard', 3: 'Expert', 4: 'ExpertPlus'}


class Unavailable(Exception):
    """取れない・読めない・形式の版が違う（今回は全曲データを使わない）"""


def _varint(b, i, end):
    r = s = 0
    while True:
        if i >= end or s > 63:
            raise ValueError('varint')
        c = b[i]
        i += 1
        r |= (c & 0x7f) << s
        if c < 0x80:
            return r, i
        s += 7


def _fields(b, i, end):
    """(番号, 値) を順に返す。長さ付きの値は (開始, 終了)、固定長は None（使わない）、可変長は整数。壊れていれば ValueError"""
    while i < end:
        tag, i = _varint(b, i, end)
        fn, wt = tag >> 3, tag & 7
        if fn == 0:
            raise ValueError('field0')
        if wt == 0:
            v, i = _varint(b, i, end)
        elif wt == 2:
            n, i = _varint(b, i, end)
            if n > end - i:
                raise ValueError('len')
            v = (i, i + n)
            i += n
        elif wt in (1, 5):
            n = 8 if wt == 1 else 4
            if n > end - i:
                raise ValueError('fixed')
            v = None
            i += n
        else:
            raise ValueError('wire')
        yield fn, v


def parse(gz):
    """gz のバイト列 → (作成の時刻, {(ハッシュ, モード, 難易度): BeatLeader の★})。BeatLeader でランクの曲の、★のある難易度だけ。読めなければ Unavailable"""
    try:
        d = zlib.decompressobj(31)
        buf = d.decompress(gz, MAX_UNZIP + 1)
        if len(buf) > MAX_UNZIP or d.unconsumed_tail:
            raise ValueError('too big')
        version = scrape = None
        hashes = None
        songs = []
        for fn, v in _fields(buf, 0, len(buf)):
            if fn == 1 and isinstance(v, int):
                version = v
            elif fn == 2 and isinstance(v, int):
                scrape = v
            elif fn == 3 and isinstance(v, tuple):
                hashes = v
            elif fn == 4 and isinstance(v, tuple):
                songs.append(v)
        if version != FORMAT_VERSION or not scrape or hashes is None or not songs or (hashes[1] - hashes[0]) // 20 < len(songs):
            raise ValueError('format')
        ranked = {}
        for i, (s, e) in enumerate(songs):
            states, diffs = 0, []
            for fn, v in _fields(buf, s, e):
                if fn == 13 and isinstance(v, int):
                    states = v
                elif fn == 11 and isinstance(v, tuple):
                    diffs.append(v)
            if not states & RANKED_BL:
                continue
            h = bytes(buf[hashes[0] + 20 * i:hashes[0] + 20 * i + 20]).hex()
            for ds, de in diffs:
                # 無い項目は protobuf の 0 ではなく SongDifficultyProto の既定（Standard / ExpertPlus）
                ch, df, stars = 1, 4, 0
                for fn, v in _fields(buf, ds, de):
                    if isinstance(v, int):
                        if fn == 1:
                            ch = v
                        elif fn == 2:
                            df = v
                        elif fn == 5:
                            stars = v
                if stars and ch in CHARACTERISTICS and df in DIFFICULTIES:
                    ranked[(h, CHARACTERISTICS[ch], DIFFICULTIES[df])] = stars / 100
        return scrape, ranked
    except (ValueError, IndexError, zlib.error) as e:
        raise Unavailable('broken: %s' % e)


def fetch(opener, user_agent, etag=None, cancelled=None):
    """取る。返り値は (中身の gz か None（前と同じ＝304）, ETag)。取れなければ Unavailable。cancelled() が真なら Cancelled"""
    req = urllib.request.Request(URL, headers={'User-Agent': user_agent, 'Accept-Encoding': 'identity'})
    if etag:
        req.add_header('If-None-Match', etag)
    try:
        with opener(req, TIMEOUT) as r:
            status = getattr(r, 'status', 200)
            if status == 304:
                return None, etag
            if status != 200:
                raise Unavailable('http-%s' % status)
            chunks, total = [], 0
            while True:
                if cancelled and cancelled():
                    raise Cancelled()
                c = r.read(CHUNK)
                if not c:
                    break
                total += len(c)
                if total > MAX_DOWNLOAD:
                    raise Unavailable('too big')
                chunks.append(c)
            new = r.headers.get('ETag')
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise Unavailable('http-%s' % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unavailable('%s: %s' % (type(e).__name__, e))
    if not total:
        raise Unavailable('empty')
    return b''.join(chunks), new if isinstance(new, str) and len(new) < 300 else None


def changes(ranked, rows):
    """全曲データのランク譜面（parse）と手元の譜面（hash・mode・difficulty・stars の行）の違い: 全曲データにだけある・手元にだけある・★が違う譜面の並び（順は決まっている）"""
    mine = {((r['hash'] or '').lower(), r['mode'], r['difficulty']): r['stars'] for r in rows}
    out = sorted(k for k in ranked if k not in mine)
    out += sorted(k for k in mine if k not in ranked)
    out += sorted(k for k in ranked if k in mine and abs(ranked[k] - (mine[k] or 0)) > STARS_TOLERANCE)
    return out


def signature(diff):
    """changes の結果の印。全曲データにはランクでない難易度の★も入っていて（ランクの曲の 23譜面）いつも少し違うので、「前に取り直した時と同じ違いか」で比べる"""
    return hashlib.sha1(json.dumps(diff, ensure_ascii=False).encode()).hexdigest()
