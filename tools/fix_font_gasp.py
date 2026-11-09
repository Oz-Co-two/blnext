"""内蔵フォント（web/fonts/MPLUSRounded1c-*.ttf）の gasp 表を直して、Windows の画面でなめらかに表示されるようにする。

なぜ要るか: 配布元のフォントは gasp 表が version 0・全範囲が 0x0002（DOGRAY だけ）になっている。Windows の Edge/WebView2
（DirectWrite）は、gasp に「symmetric smoothing」の印（version 1 で足された bit 0x0008）が無いと縦方向のなめらか処理をしないので、
丸い字の曲線や横線が段々（ギザギザ）に見える。そこで gasp を version 1 にし、全範囲の behavior を 0x000F（GRIDFIT・DOGRAY・
SYMMETRIC_GRIDFIT・SYMMETRIC_SMOOTHING）にする。ヘッドレスの Edge で撮って比べ、なめらかになることを確かめた（2026-10-17）。
書き換えるのは gasp の中の数値だけで表の長さは変わらない。gasp と head の表のチェックサムと head.checkSumAdjustment を計算し直し、
最後に全部の表のチェックサムが合うことを確かめて表示する。フォントは SIL OFL 1.1 で、改変したことは THIRD_PARTY_NOTICES.txt に書いてある。

フォントを配布元の新しい版に差し替えた時に実行する（何度実行しても同じ。直し済みなら何もしない）。標準ライブラリだけで動く:
  .venv-build/Scripts/python.exe tools/fix_font_gasp.py                 web/fonts/MPLUSRounded1c-*.ttf を直す
  .venv-build/Scripts/python.exe tools/fix_font_gasp.py <フォント> ...     指定したフォントを直す
  .venv-build/Scripts/python.exe tools/fix_font_gasp.py --check          書き換えずに gasp とチェックサムを表示するだけ"""
import os, struct, sys
from pathlib import Path

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FONTS = sorted((ROOT / 'web' / 'fonts').glob('MPLUSRounded1c-*.ttf'))
WANT_VERSION = 1
WANT_BEHAVIOR = 0x000F
CHECKSUM_MAGIC = 0xB1B0AFBA   # 仕様で決まった値。ファイル全体の和がこれになるよう head.checkSumAdjustment を決める
HEAD_ADJ = 8                  # head 表の中の checkSumAdjustment の位置


def checksum(data):
    data = bytes(data) + b'\0' * (-len(data) % 4)
    return sum(struct.unpack(f'>{len(data) // 4}I', data)) & 0xFFFFFFFF


def tables(b):
    """表の一覧 {タグ: (目次の位置, 目次のチェックサム, 表の位置, 表の長さ)}。1つのフォントだけの .ttf/.otf を読む（.ttc は扱わない）"""
    if bytes(b[:4]) not in (b'\x00\x01\x00\x00', b'true', b'OTTO'):
        raise ValueError('TrueType/OpenType のフォントではない（.ttc は扱わない）')
    n = struct.unpack_from('>H', b, 4)[0]
    out = {}
    for i in range(n):
        tag, cs, off, ln = struct.unpack_from('>4sIII', b, 12 + 16 * i)
        if off + ln > len(b):
            raise ValueError(f'表 {tag.decode("latin-1")} がファイルの外を指している')
        out[tag] = (12 + 16 * i, cs, off, ln)
    return out


def read_gasp(b, t):
    """(version, [(rangeMaxPPEM, behavior), ...])。gasp が無ければ None"""
    if b'gasp' not in t:
        return None
    _, _, off, ln = t[b'gasp']
    version, num = struct.unpack_from('>HH', b, off)
    if 4 + 4 * num > ln:
        raise ValueError('gasp 表の範囲の数が表の長さと合わない')
    return version, [struct.unpack_from('>HH', b, off + 4 + 4 * j) for j in range(num)]


def gasp_text(g):
    version, ranges = g
    return f'version {version}・' + ', '.join(f'(~{ppem:#06x}, {beh:#06x})' for ppem, beh in ranges)


def verify(b):
    """全部の表のチェックサムと head.checkSumAdjustment を確かめる。合わない物の説明の並び（空なら全部合っている）"""
    t = tables(b)
    bad = []
    for tag, (_, cs, off, ln) in t.items():
        data = bytearray(b[off:off + ln])
        if tag == b'head':
            struct.pack_into('>I', data, HEAD_ADJ, 0)   # head の表のチェックサムは checkSumAdjustment を 0 として求める
        if checksum(data) != cs:
            bad.append(f'表 {tag.decode("latin-1")} のチェックサム')
    if b'head' not in t:
        bad.append('head 表が無い')
    elif checksum(b) != CHECKSUM_MAGIC:
        bad.append('head.checkSumAdjustment')
    return bad


def fix(b):
    """gasp を version 1・全範囲 0x000F にして、gasp と head のチェックサムと head.checkSumAdjustment を計算し直す（b を書き換える）"""
    t = tables(b)
    grec, _, goff, gln = t[b'gasp']
    num = struct.unpack_from('>H', b, goff + 2)[0]
    struct.pack_into('>H', b, goff, WANT_VERSION)
    for j in range(num):
        struct.pack_into('>H', b, goff + 4 + 4 * j + 2, WANT_BEHAVIOR)
    struct.pack_into('>I', b, grec + 4, checksum(b[goff:goff + gln]))
    hrec, _, hoff, hln = t[b'head']
    struct.pack_into('>I', b, hoff + HEAD_ADJ, 0)
    struct.pack_into('>I', b, hrec + 4, checksum(b[hoff:hoff + hln]))
    struct.pack_into('>I', b, hoff + HEAD_ADJ, (CHECKSUM_MAGIC - checksum(b)) & 0xFFFFFFFF)


def report_checksums(b):
    bad = verify(b)
    print('  チェックサム: ' + ('全部の表と head.checkSumAdjustment が合っている' if not bad else '合わない: ' + '・'.join(bad)))
    return not bad


def process(path, check_only):
    """1つのフォントを確かめて、要れば直す。問題が残れば False"""
    path = Path(path)
    print(f'{path.name}:')
    b = bytearray(path.read_bytes())
    t = tables(b)
    g = read_gasp(b, t)
    if g is None:
        print('  gasp 表が無い。何もしない')
        return True
    print(f'  gasp: {gasp_text(g)}')
    if b'DSIG' in t:
        print('  注意: 電子署名（DSIG 表）がある。書き換えると署名は合わなくなる（画面での表示には関係ない）')
    done = g[0] == WANT_VERSION and all(beh == WANT_BEHAVIOR for _, beh in g[1])
    if done or check_only:
        print('  直し済み。何もしない' if done else '  まだ直していない（--check なので書き換えない）')
        return report_checksums(b)
    fix(b)
    g2 = read_gasp(b, tables(b))
    print(f'  → gasp: {gasp_text(g2)}')
    ok = report_checksums(b)
    if not ok:
        print('  書き換えた結果が正しくないので保存しない')
        return False
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(bytes(b))
    os.replace(tmp, path)   # 書き込みの途中で止まっても元のフォントが壊れないよう、別名で書いてから置き換える
    print('  保存した')
    return True


def main(argv):
    check_only = '--check' in argv
    paths = [a for a in argv if a != '--check'] or DEFAULT_FONTS
    if not paths:
        sys.exit('フォントが見つからない（web/fonts/MPLUSRounded1c-*.ttf）')
    ok = True
    for p in paths:
        try:
            ok = process(p, check_only) and ok
        except (OSError, ValueError, struct.error) as e:
            print(f'  読めない（{e}）')   # 名前は process が先に表示している
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main(sys.argv[1:])
