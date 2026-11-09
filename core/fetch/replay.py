# 自己ベストのリプレイ（.bsor）の頭の部分（info）だけを読んで、その時のジャンプの距離（JD）を取り出す。
# BeatLeader の API の応答には JD が無く、リプレイのファイルの info にだけある。info はファイルの先頭の数百バイトなので、
# Range で先頭だけを読む（1曲1回・数KB。docs/investigation.md の「リプレイの頭（JD）」）。
# 読んだ結果は DB の replay_info にスコアの ID ごとに残す（自己ベストが変われば ID も変わるので、読み直しは要らない）。
# 読むのは、おすすめの画面に出ているプレイ済みの曲の分だけ（serve.py の recommend/replays）。
# RT（反応時間）は読まずに JD と譜面の NJS から計算する（rt_ms）。
import http.client, logging, os, struct, threading, time, urllib.error, urllib.request
from urllib.parse import urlsplit

from . import http as bhttp

log = logging.getLogger('bln.replay')

MAGIC = 0x442D3D69
HEAD_BYTES = 4096        # info はふつう300バイトほど。曲名・マッパー名が長くても収まる大きさ
HOSTS = ('beatleader.com', 'beatleader.xyz')
INTERVAL = 0.25          # 秒。続けて読む時の間隔（API と同じ1秒4回）
MAX_PER_CALL = 30        # 1回に読む数の上限（おすすめ＋次点で足りる）
TIMEOUT = 15
# info の文字列の並び（BeatLeader/BS-Open-Replay の形式）。この後に score（int）・mode・environment・modifiers・jumpDistance（float）
_STR_BEFORE_SCORE = 13   # version・gameVersion・timestamp・playerID・playerName・platform・trackingSystem・hmd・controller・hash・songName・mapper・difficulty
_STR_AFTER_SCORE = 3
_lock = threading.Lock()   # 同じリプレイを2つの要求が同時に読まないように


def is_replay_url(url):
    """BeatLeader の置き場所の .bsor の URL か（任意の場所へ通信しないため。API の応答の replay をそのまま信じない）"""
    if not isinstance(url, str):
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or '').lower()
    return (u.scheme == 'https' and u.path.lower().endswith('.bsor')
            and any(host == h or host.endswith('.' + h) for h in HOSTS))


def parse_info(b):
    """.bsor の先頭 → {'jd': ジャンプの距離}。形が違えば ValueError"""
    try:
        magic, ver, section = struct.unpack_from('<iBB', b, 0)
        if magic != MAGIC or section != 0:
            raise ValueError('not a bsor')
        p = 6

        def skip_str():
            nonlocal p
            n = struct.unpack_from('<i', b, p)[0]
            if not 0 <= n <= 2000:
                raise ValueError('bad string length')
            p += 4 + n

        for _ in range(_STR_BEFORE_SCORE):
            skip_str()
        p += 4   # score
        for _ in range(_STR_AFTER_SCORE):
            skip_str()
        jd = struct.unpack_from('<f', b, p)[0]
    except struct.error:
        raise ValueError('short')
    if not (0 < jd < 100):
        raise ValueError('bad jd %r' % jd)
    return {'jd': round(jd, 3)}


SPEED = {'SS': 0.85, 'FS': 1.2, 'SF': 1.5}   # 曲の速さのモディファイア（ノーツの速さも同じ倍率になり、JD は変わらない）


def rt_ms(jd, njs, modifiers=''):
    """反応時間（ミリ秒）＝ノーツが出てから届くまでの時間＝JD の半分 ÷ ノーツの速さ。BFS・BSF（速さの倍率が分からない）は None"""
    mods = [m.strip().upper() for m in (modifiers or '').split(',') if m.strip()]
    if not jd or not njs or any(m in ('BFS', 'BSF') for m in mods):
        return None
    speed = next((SPEED[m] for m in mods if m in SPEED), 1.0)
    return round(jd / 2 / (njs * speed) * 1000)


def _opener():
    return bhttp._no_net if os.environ.get('BLN_NO_NET') == '1' else bhttp._urlopen


def fetch_info(url, user_agent, opener=None):
    """リプレイの先頭を読んで parse_info の結果を返す。通信できなければ NetError（code は http.py と同じ）、
    中身が読めなければ NetError('bad-replay')"""
    req = urllib.request.Request(url, headers={'User-Agent': user_agent, 'Range': 'bytes=0-%d' % (HEAD_BYTES - 1)})
    try:
        with (opener or _opener())(req, TIMEOUT) as r:
            status = getattr(r, 'status', 200)
            body = r.read(HEAD_BYTES)   # Range を無視して全部を返す所でも、先頭だけ読んで閉じる
    except urllib.error.HTTPError as e:
        raise bhttp.NetError('http-%d' % e.code, url)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise bhttp.NetError('offline', '%s: %s' % (type(e).__name__, e))
    if status not in (200, 206):
        raise bhttp.NetError('http-%d' % status, url)
    try:
        return parse_info(body)
    except ValueError as e:
        raise bhttp.NetError('bad-replay', '%s %s' % (url, e))


def _gone(code):
    """やり直しても同じ失敗（無い・読めない）。通信の失敗・5xx・429 は次の機会にやり直す"""
    if code == 'bad-replay':
        return True
    if code.startswith('http-'):
        n = int(code[5:])
        return 400 <= n < 500 and n != 429
    return False


def known(store, score_ids):
    """スコアの ID → replay_info の行（{jd, status}）。まだ読んでいない ID は入らない"""
    ids = [x for x in dict.fromkeys(score_ids) if isinstance(x, int)]
    if not ids:
        return {}
    rows = store.q('SELECT score_id, jd, status FROM replay_info WHERE score_id IN (%s)' % ','.join('?' * len(ids)), ids)
    return {r['score_id']: r for r in rows}


def ensure(store, replays, user_agent, opener=None, now=None, sleep=time.sleep):
    """replays（[(スコアの ID, リプレイの URL)]）のうち、まだ読んでいないリプレイの頭を読んで replay_info に残す。
    返り値は通信に失敗して残した数（次の機会にやり直す）"""
    with _lock:
        have = known(store, [sid for sid, _ in replays])
        todo = list({sid: url for sid, url in replays if isinstance(sid, int) and url and sid not in have}.items())[:MAX_PER_CALL]
        failed = 0
        for i, (sid, url) in enumerate(todo):
            if not is_replay_url(url):
                jd, status = None, 'bad-url'
            else:
                if i:
                    sleep(INTERVAL)
                try:
                    jd, status = fetch_info(url, user_agent, opener)['jd'], 'ok'
                    log.info('replay head %s jd=%.2f', sid, jd)
                except bhttp.NetError as e:
                    log.warning('replay head %s: %s', url, e)
                    if not _gone(e.code):
                        failed += 1
                        if e.code == 'offline':
                            failed += len(todo) - i - 1   # つながらない時は残りも試さない
                            break
                        continue
                    jd, status = None, e.code
            with store.tx():
                store.conn.execute('INSERT OR REPLACE INTO replay_info(score_id, jd, status, fetched_at) VALUES(?,?,?,?)',
                                   (sid, jd, status, int(now or time.time())))
        return failed
