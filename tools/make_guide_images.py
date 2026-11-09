"""使い方の画面（web/js/screens/guide.js）の画像を、作り物のデータの画面から作る（web/guide/*.webp）。画面の見た目を変えたら流し直す。

  .venv-build/Scripts/python.exe tools/make_guide_images.py

- 作り物のデータ（tests/_lib/fixture_data。曲名は Song LB… ・人の名前は Player …）だけを使う。本物のデータ（個人の情報）は使わない。
- ヘッドレスの Edge で画面を開き（tools/cdp.py）、部品の所だけを 2 倍で撮って 1.5 倍に縮め、WebP で保存する（高 DPI の画面でもぼやけにくく、軽い）。
- 撮った画像の大きさ（CSS の px）を出す。guide.js の img の width・height（縦横の比）と違えば知らせるので、直す。
- BeatLeader の設定の画面の画像（web/guide/bl-public-history.png）はここでは作らない（stats.js の howTo の説明）。
"""
import base64
import io
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import App, ROOT   # noqa: E402

OUT = ROOT / 'web' / 'guide'
GUIDE_JS = ROOT / 'web' / 'js' / 'screens' / 'guide.js'
DSF = 2        # 撮る時の倍率
KEEP = 1.5     # 保存する倍率（CSS の px に対して）
WIDTH = 1200   # 画面の幅（CSS の px）

# 切り出す部品を返す JS（要素を返す式）。height は切り出す高さの上限（CSS の px）
CARD = "[...document.querySelectorAll('#main section.card')].find(e => e.querySelector('h2')?.textContent === %r)"
# おすすめの画面: 作り物のデータの「作り直すと変わる」の帯を隠し、出力先（テストの一時フォルダ＝この PC のユーザー名が入る）を
# よくある場所の表記に置き換える
# （曲を開くと画面を描き直すので、開いた後に置き換える）
BS_DIR = r'C:\Program Files (x86)\Steam\steamapps\common\Beat Saber'
REC = ("document.querySelector('.pendBar')?.remove();"
       "document.querySelectorAll('.path').forEach(e => { e.textContent = %s; e.title = ''; });"
       % json.dumps('出力先: ' + BS_DIR + '\\Playlists', ensure_ascii=False))
SHOTS = [
    # (ファイル名, 画面, 準備の JS, 部品, 高さの上限)
    ('rec.webp', 'recommend', None, "document.querySelector('.recMain')", 640),
    ('rec-open.webp', 'recommend', "document.querySelectorAll('.recList .recRow:not(.head) .songName')[1].click();",
     "document.querySelectorAll('.recList .recItem')[1]", 620),
    ('home-review.webp', 'home', None, CARD % '前回のプレイリストの結果', 520),
    ('home-changes.webp', 'home', None, CARD % '前回からの変化', 400),
]


def settle(app):
    """カバー画像（見えたら読む data-src）を全部読ませて、読み終えるまで待つ"""
    app.js("document.querySelectorAll('img[data-src]').forEach(i => { i.src = i.dataset.src; })")
    app.until("[...document.images].every(i => i.complete)", timeout=10, what='画像')
    app.wait(0.4)


def grab(app, expr, max_h):
    r = app.js(f"(() => {{ const e = {expr}; if(!e) return null; const b = e.getBoundingClientRect();"
               " return {x: b.left, y: b.top, w: b.width, h: b.height}; })()")
    if not r:
        raise SystemExit(f'部品が見つかりません: {expr}')
    clip = {'x': r['x'], 'y': r['y'], 'width': r['w'], 'height': min(r['h'], max_h), 'scale': 1}
    data = app.call('Page.captureScreenshot', format='png', clip=clip, captureBeyondViewport=True)['data']
    im = Image.open(io.BytesIO(base64.b64decode(data))).convert('RGB')
    w, h = round(clip['width'] * KEEP), round(clip['height'] * KEEP)
    return im.resize((w, h), Image.LANCZOS), (round(clip['width']), round(clip['height']))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    js = GUIDE_JS.read_text(encoding='utf-8')
    bad = 0
    with App(width=WIDTH, height=900) as app:
        # 縦に長い画面でも全部が見えるよう窓を高くする（#main の中のスクロールは撮れないため）
        app.call('Emulation.setDeviceMetricsOverride', width=WIDTH, height=3000, deviceScaleFactor=DSF, mobile=False)
        for name, screen, prep, expr, max_h in SHOTS:
            app.open(screen)
            if prep:
                app.js(prep)
                app.wait(0.4)
            if screen == 'recommend':
                app.js(REC)
            settle(app)
            # この PC の場所・ユーザー名が画面に残っていないか（画像は exe と GitHub で配る）
            for secret in {str(Path.home()), Path.home().name, os.environ.get('USERNAME', '')} - {''}:
                if app.js(f"document.getElementById('main').innerText.includes({json.dumps(secret)})"):
                    raise SystemExit(f'{name}: 画面にこの PC の情報（{secret}）が出ています。準備の JS で隠してください')
            im, (cw, ch) = grab(app, expr, max_h)
            path = OUT / name
            im.save(path, 'WEBP', quality=88, method=6)
            m = re.search(r"img\('%s',.*?,\s*(\d+),\s*(\d+)\)" % re.escape(name), js)
            note = ''
            if not m:
                note = '  ← guide.js で使っていません'
            elif abs(int(m.group(1)) / int(m.group(2)) - cw / ch) > 0.01:
                note = f'  ← guide.js の大きさ（{m.group(1)}×{m.group(2)}）を {cw}×{ch} に直す'
                bad += 1
            print(f'{name}: {cw}×{ch}（CSS の px）・{path.stat().st_size // 1024}KB{note}')
        for ln in app.errors():
            print('  ' + ln)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
