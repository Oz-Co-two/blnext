# テスト用のリプレイ（.bsor）の先頭（info まで）を作る。core/fetch/replay.py の parse_info が読む形
# （BeatLeader/BS-Open-Replay の形式。int32・float32 はリトルエンディアン、文字列は int32 のバイト数＋UTF-8）。
# 中身は作り物。tests/unit/test_replay.py と作り物の BeatLeader（fakebl.py）が使う
import struct

MAGIC = 0x442D3D69
# info の文字列の並び。score（int32）の前の13個と後の3個
STR_BEFORE_SCORE = ('version', 'gameVersion', 'timestamp', 'playerID', 'playerName', 'platform', 'trackingSystem', 'hmd',
                    'controller', 'hash', 'songName', 'mapper', 'difficulty')
STR_AFTER_SCORE = ('mode', 'environment', 'modifiers')
DEFAULTS = {'version': '0.9.0', 'gameVersion': '1.40.0', 'timestamp': '1700000000', 'playerID': '9000',
            'playerName': 'Player', 'platform': 'steam', 'trackingSystem': 'Oculus', 'hmd': 'Quest 3',
            'controller': 'Quest 3 Touch', 'hash': 'ABCDEF0123456789', 'mapper': 'M', 'difficulty': 'ExpertPlus',
            'mode': 'Standard', 'environment': 'DefaultEnvironment',
            'score': 1000000, 'leftHanded': 0, 'height': 1.7, 'startTime': 0.0, 'failTime': 0.0, 'speed': 0.0}


def _str(s):
    b = s.encode('utf-8')
    return struct.pack('<i', len(b)) + b


def make_header(jd, song='Song', modifiers='', **kw):
    """.bsor の先頭（magic・版・info）→ bytes。kw は上の DEFAULTS の名前で文字列・数を変える（知らない名前は TypeError）"""
    unknown = set(kw) - set(DEFAULTS)
    if unknown:
        raise TypeError('unknown field: %s' % ', '.join(sorted(unknown)))
    v = {**DEFAULTS, 'songName': song, 'modifiers': modifiers, **kw}
    out = [struct.pack('<iBB', MAGIC, 1, 0)]   # 版 1、区切り 0（info）
    out += [_str(v[k]) for k in STR_BEFORE_SCORE]
    out.append(struct.pack('<i', v['score']))
    out += [_str(v[k]) for k in STR_AFTER_SCORE]
    out.append(struct.pack('<fBffff', jd, v['leftHanded'], v['height'], v['startTime'], v['failTime'], v['speed']))
    return b''.join(out)
