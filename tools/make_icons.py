"""web/icons/app.svg から exe のアイコン（web/icons/app.ico）とプレイリストの画像（web/icons/app.png）、その色違い
（web/icons/covers/*.png）を作る。
SVG の描画はヘッドレスの Edge、縮小と .ico への詰め込みは Pillow（開発用の .venv-build に入れる: pip install pillow）。
アイコンの絵を変えた時だけ実行する: .venv-build/Scripts/python.exe tools/make_icons.py"""
import os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / 'web' / 'icons'
EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]


def render_png(svg, size, out):
    edge = next((p for p in EDGE_CANDIDATES if os.path.exists(p)), None)
    if not edge:
        sys.exit('Edge が見つかりません')
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / 'icon.html'
        page.write_text('<!doctype html><html><body style="margin:0;background:transparent">'
                        f'<img src="{svg.as_uri()}" width="{size}" height="{size}" style="display:block"></body></html>',
                        encoding='utf-8')
        subprocess.run([edge, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--default-background-color=00000000',
                        f'--window-size={size},{size}', f'--user-data-dir={Path(td) / "prof"}',
                        '--allow-file-access-from-files', f'--screenshot={out}', page.as_uri()],
                       check=True, capture_output=True, timeout=60)


# プレイリストの画像の色違い（web/icons/covers/<名前>.png。おすすめの画面・設定画面で選べる。serve.py の COVER_PRESETS と同じ名前）。
# アイコンの絵の色（背景の2色と矢印の色）だけを差し替える。元の緑はアプリのアイコン（app.png）そのもの
APP_COLORS = ('#0f9f70', '#05664a', '#9ff5d2')
COVER_COLORS = {
    'blue':   ('#3b82f6', '#1e3a8a', '#bfdbfe'),
    'purple': ('#8b5cf6', '#4c1d95', '#ddd6fe'),
    'orange': ('#f59e0b', '#b45309', '#fde68a'),
    'red':    ('#ef4444', '#991b1b', '#fecaca'),
    'slate':  ('#64748b', '#1e293b', '#e2e8f0'),
}
COVER_SIZE = 256


def make_covers(td):
    from PIL import Image
    src = (ICONS / 'app.svg').read_text(encoding='utf-8')
    out = ICONS / 'covers'
    out.mkdir(exist_ok=True)
    for name, colors in COVER_COLORS.items():
        svg = src
        for a, b in zip(APP_COLORS, colors):
            assert a in svg, a
            svg = svg.replace(a, b)
        sp = Path(td) / (name + '.svg')
        sp.write_text(svg, encoding='utf-8')
        big = Path(td) / (name + '.png')
        render_png(sp, COVER_SIZE, big)
        Image.open(big).convert('RGBA').crop((0, 0, COVER_SIZE, COVER_SIZE)).save(out / (name + '.png'), optimize=True)
        print('wrote', out / (name + '.png'))


def main():
    from PIL import Image
    svg = ICONS / 'app.svg'
    with tempfile.TemporaryDirectory() as td:
        big = Path(td) / 'app256.png'
        render_png(svg, 256, big)
        img = Image.open(big).convert('RGBA').crop((0, 0, 256, 256))
        img.save(ICONS / 'app.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        img.resize((128, 128), Image.LANCZOS).save(ICONS / 'app.png', optimize=True)   # プレイリストの画像（.bplist に base64 で入る）
        make_covers(td)
    print('wrote', ICONS / 'app.ico', ICONS / 'app.png')


if __name__ == '__main__':
    main()
