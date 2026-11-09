"""画面の比較（基準の写真と画素で比べる）の部品。tests/visual/ が使う。NLM（Non-Linear Mapper Oz-Co2版）の tests/visual と同じ考え方:

  - 基準は tests/visual/baseline/<名前>.png。PC ごとに持つ（git に入れない。フォントの描画・GPU の違いで数画素変わるため）
  - 基準が無ければ、今の写真を基準にする（初回）。BLN_VISUAL_ACCEPT=1（tools/run_tests.py visual --accept）なら今の写真で置き換える
  - R・G・B のどれかの差が THRESH を超えた画素を「変化」とし、MIN_PIXELS 以上なら「画面が変化した」＝要確認（失敗ではない）
  - 変化した画面は test-out/visual/ に before/（基準）・current/（今）・diff/（変わった所を赤く）と、まとめ画像 review.png・
    前後を並べたページ report.html を作る
"""
import html, json, os, shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'tests' / 'visual' / 'baseline'
OUT = ROOT / 'test-out' / 'visual'
THRESH = 32        # 1つの色の差（0〜255）がこれを超えた画素を「変化」とする
MIN_PIXELS = 25    # 変化した画素がこれ以上なら「画面が変化した」
FONT = ROOT / 'web' / 'fonts' / 'MPLUSRounded1c-Bold.ttf'
ACCEPT = os.environ.get('BLN_VISUAL_ACCEPT') == '1'

RESULTS = []   # このテストの回の結果（まとめを作るのに使う）

# 撮る前に入れる CSS: 動き・文字の点滅を止め、撮るたびに変わる表示（時刻・版）を隠す（場所は残す＝並びは変えない。
# 時刻の数字の並びは test_visual.settle が 00 にそろえて、日付で幅が変わらないようにする）
FREEZE_CSS = ('*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}'
              '#dataAt,.when,#appVersion{visibility:hidden!important}#toast{display:none!important}')


def prepare_out():
    """出力のフォルダを空にする（前の回の結果を残さない）"""
    for d in ('current', 'before', 'diff'):
        shutil.rmtree(OUT / d, ignore_errors=True)
        (OUT / d).mkdir(parents=True)
    for f in ('review.png', 'report.html', 'results.json'):
        (OUT / f).unlink(missing_ok=True)
    RESULTS.clear()


def current_path(name):
    return OUT / 'current' / f'{name}.png'


def _load(p):
    with Image.open(p) as im:
        return np.asarray(im.convert('RGB'), dtype=np.int16)


def compare(name, title=''):
    """current/<名前>.png を基準と比べて結果（dict）を返す。status: same / changed / new（基準を作った）/ accepted（基準を置き換えた）"""
    cur, base = current_path(name), BASE / f'{name}.png'
    r = {'name': name, 'title': title, 'pixels': 0, 'note': ''}
    BASE.mkdir(parents=True, exist_ok=True)
    if ACCEPT or not base.exists():
        r['status'] = 'accepted' if base.exists() else 'new'
        shutil.copyfile(cur, base)
        RESULTS.append(r)
        return r
    a, b = _load(base), _load(cur)
    if a.shape != b.shape:   # 大きさが違う（縦に長くなった等）: 大きい方に合わせて、はみ出した所は全部「変化」
        h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
        pa, pb = np.zeros((h, w, 3), np.int16), np.zeros((h, w, 3), np.int16)
        pa[:a.shape[0], :a.shape[1]], pb[:b.shape[0], :b.shape[1]] = a, b
        mask = (np.abs(pa - pb).max(axis=2) > THRESH)
        mask[a.shape[0]:, :] = mask[b.shape[0]:, :] = True
        r['note'] = '大きさが変わった（%d×%d → %d×%d）' % (a.shape[1], a.shape[0], b.shape[1], b.shape[0])
        cur_img = pb
    else:
        mask = np.abs(a - b).max(axis=2) > THRESH
        cur_img = b
    n = int(mask.sum())
    r['pixels'] = n
    if n < MIN_PIXELS and not r['note']:
        r['status'] = 'same'
    else:
        r['status'] = 'changed'
        r['note'] = (r['note'] + '・' if r['note'] else '') + '変化した画素 %d' % n
        shutil.copyfile(base, OUT / 'before' / f'{name}.png')
        d = (cur_img * 0.3).astype(np.uint8)       # 今の画面を暗くして、変わった所を赤く
        d[mask] = (255, 40, 40)
        Image.fromarray(d).save(OUT / 'diff' / f'{name}.png')
    RESULTS.append(r)
    return r


def write_report():
    """変化した画面のまとめ（review.png・report.html）と、全部の結果（results.json）を書く。返り値は変化した画面の名前"""
    if not RESULTS:
        return []
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding='utf-8')
    changed = [r for r in RESULTS if r['status'] == 'changed']
    if not changed:
        return []
    _review_png(changed)
    _report_html(changed)
    return [r['name'] for r in changed]


def _review_png(changed, col=440, max_h=1400, limit=12):
    """1枚のまとめ画像: 画面ごとに「基準 | 今 | 変わった所」を横に並べる（縦に長い画面は上の max_h まで）"""
    font = ImageFont.truetype(str(FONT), 18)
    rows = []
    for r in changed[:limit]:
        ims = []
        for d in ('before', 'current', 'diff'):
            im = Image.open(OUT / d / f'{r["name"]}.png').convert('RGB')
            im = im.resize((col, max(1, round(im.height * col / im.width))))
            ims.append(im.crop((0, 0, col, min(im.height, max_h))))
        h = max(i.height for i in ims)
        row = Image.new('RGB', (col * 3 + 40, h + 34), (24, 24, 24))
        ImageDraw.Draw(row).text((10, 6), '%s  %s' % (r['name'], r['note']), fill=(238, 238, 238), font=font)
        for i, im in enumerate(ims):
            row.paste(im, (10 + i * (col + 10), 30))
        rows.append(row)
    head = Image.new('RGB', (col * 3 + 40, 40), (24, 24, 24))
    ImageDraw.Draw(head).text((10, 8), '基準 ｜ 今 ｜ 変わった所（赤）  %d 画面' % len(changed)
                              + ('（先頭の %d 画面）' % limit if len(changed) > limit else ''), fill=(238, 238, 238), font=font)
    sheet = Image.new('RGB', (head.width, head.height + sum(r.height + 8 for r in rows)), (18, 18, 18))
    sheet.paste(head, (0, 0))
    y = head.height
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height + 8
    sheet.save(OUT / 'review.png')


def _report_html(changed):
    """前後を並べて見るページ（画像は同じフォルダの before/・current/・diff/ を指す。押すと原寸）"""
    e = html.escape
    items = ''.join(
        '<section><h2>%s</h2><p>%s</p><div class="row">%s</div></section>' % (
            e(r['title'] or r['name']), e(r['name'] + '：' + r['note']),
            ''.join('<figure><figcaption>%s</figcaption><a href="%s/%s.png"><img src="%s/%s.png" alt=""></a></figure>'
                    % (cap, d, e(r['name']), d, e(r['name'])) for cap, d in (('基準', 'before'), ('今', 'current'), ('変わった所（赤）', 'diff'))))
        for r in changed)
    style = ('body{margin:0;padding:16px 20px;background:#121212;color:#eee;font-family:"Segoe UI","Yu Gothic",sans-serif}'
             'h1{font-size:20px}h2{font-size:16px;margin:0 0 4px}p{color:#aaa;margin:0 0 8px;font-size:13px}'
             'section{border-top:1px solid #333;padding:14px 0}.row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}'
             'figure{margin:0}figcaption{font-size:12px;color:#aaa;margin-bottom:4px}img{width:100%;height:auto;border:1px solid #333;display:block}')
    page = ('<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>画面の比較</title><style>' + style + '</style></head><body>'
            '<h1>画面の比較: 変化した画面 %d 件</h1>' % len(changed)
            + '<p>意図した変化なら <code>tools/run_tests.py visual --accept</code> で基準を置き換えます。</p>' + items + '</body></html>')
    (OUT / 'report.html').write_text(page, encoding='utf-8')
