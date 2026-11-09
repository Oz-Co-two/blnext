# プレイリストの画像を選ぶ小さな画面（web/js/cover.js）: おすすめのまとめの欄の画像を押す・設定画面の「画像を選ぶ…」から開き、
# 用意した色違い（web/icons/covers/）・アプリのアイコン・ファイルから読み込んだ画像を選ぶ
import base64, struct, zlib
from pathlib import Path

ICONS = Path(__file__).resolve().parent.parent.parent / 'web' / 'icons'
DLG = "document.querySelector('.coverDlg')"
TILES = "[...document.querySelectorAll('.coverDlg .coverTiles .coverTile')]"
ON = "[...document.querySelectorAll('.coverDlg .coverTile.on')].map(b=>b.dataset.cover)"
SUM_IMG = "document.querySelector('.recSum .coverBtn img').src"


def png(w, h):
    """w×h の PNG（赤一色。test_presets.py と同じ）"""
    raw = b''.join(b'\x00' + b'\xff\x00\x00' * w for _ in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def data_url(path):
    return 'data:image/png;base64,' + base64.b64encode(Path(path).read_bytes()).decode('ascii')


def open_dialog(ui, sel='.recSum .coverBtn'):
    ui.click_sel(sel)
    ui.until(f"{TILES}.length===6", what='画像を選ぶ小さな画面のタイル')


def test_pick_color_from_recommend(ui):
    ui.open('recommend')
    assert ui.js(SUM_IMG) == data_url(ICONS / 'app.png')
    assert ui.js("document.querySelector('.recSum .coverBtn').title") == '画像を変える'
    open_dialog(ui)
    assert ui.js(f"{DLG}.querySelector('h2').innerText") == 'プレイリストの画像'
    assert '「スタンダード」のセット' in ui.js(f"{DLG}.innerText")
    # アプリのアイコン（緑）と用意した5色。今の画像（アプリのアイコン）に印・読み込んだ画像のタイルは無い
    assert ui.js(f"{TILES}.map(b=>b.innerText.trim())") == ['緑', '青', '紫', '橙', '赤', '灰']
    assert ui.js(f"{TILES}.map(b=>b.querySelector('img').getAttribute('src'))") == [
        'icons/app.png'] + [f'icons/covers/{n}.png' for n in ('blue', 'purple', 'orange', 'red', 'slate')]
    assert ui.js(ON) == ['app']
    assert ui.js("document.activeElement.dataset.cover") == 'app'   # 今の画像のタイルへ
    assert ui.js("document.querySelectorAll('.coverDlg .coverCustom .coverTile').length") == 0
    ui.until(f"{TILES}.every(b=>b.querySelector('img').naturalWidth>0)", what='タイルの画像が読める（合言葉なしの公開のフォルダ）')
    # 青を押す: その場で変わり、印が移る。まとめの欄の画像も変わる（小さな画面は開いたまま）
    ui.click_sel('.coverDlg .coverTile[data-cover="blue"]')
    ui.until(f"JSON.stringify({ON})==='[\"blue\"]'", what='青に印')
    blue = data_url(ICONS / 'covers' / 'blue.png')
    ui.until(f"{SUM_IMG}==={blue!r}", what='まとめの欄の画像が青')
    assert Path(ui.serve.SETTINGS.cover_path('p1')).read_bytes() == (ICONS / 'covers' / 'blue.png').read_bytes()
    assert ui.js(f"{DLG}.querySelector('.coverTile.on').getAttribute('aria-pressed')") == 'true'
    # 閉じるボタン → 開き直しても青
    ui.click_sel('.coverDlg .coverClose')
    ui.until(f"!{DLG}", what='閉じる')
    assert ui.js("document.activeElement.classList.contains('coverBtn')")   # 開いたボタンへ戻る
    open_dialog(ui)
    assert ui.js(ON) == ['blue']
    ui.key('Escape')
    ui.until(f"!{DLG}", what='Esc で閉じる')
    # 画面を開き直しても青（保存してある）
    ui.open('recommend')
    assert ui.js(SUM_IMG) == blue
    open_dialog(ui)
    assert ui.js(ON) == ['blue']
    # アプリのアイコン（緑）に戻す
    ui.click_sel('.coverDlg .coverTile[data-cover="app"]')
    ui.until(f"JSON.stringify({ON})==='[\"app\"]'", what='緑に印')
    ui.until(f"{SUM_IMG}==={data_url(ICONS / 'app.png')!r}", what='まとめの欄の画像がアプリのアイコン')
    assert ui.serve.SETTINGS.cover_path('p1') is None
    # 外側を押すと閉じる
    ui.click(5, 5)
    ui.until(f"!{DLG}", what='外側を押すと閉じる')


def enter(ui):
    """Enter（文字の入力も伴う本物の押し方。ui.key('Enter') は文字を伴わないので、ボタンが押されない）"""
    base = dict(key='Enter', code='Enter', windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
    ui.call('Input.dispatchKeyEvent', type='keyDown', text='\r', **base)
    ui.call('Input.dispatchKeyEvent', type='keyUp', **base)
    ui.wait(0.1)


def test_keyboard(ui):
    ui.open('recommend')
    ui.js("document.querySelector('.recSum .coverBtn').focus()")
    enter(ui)
    ui.until(f"{TILES}.length===6", what='Enter で開く')
    assert ui.js("document.activeElement.dataset.cover") == 'app'
    ui.key('Tab')
    ui.key('Tab')
    assert ui.js("document.activeElement.dataset.cover") == 'purple'
    enter(ui)
    ui.until(f"JSON.stringify({ON})==='[\"purple\"]'", what='Enter で紫')
    ui.key('Tab')
    ui.key(' ')
    ui.until(f"JSON.stringify({ON})==='[\"orange\"]'", what='スペースで橙')
    ui.key('Tab')
    ui.key('Tab')
    ui.key('Tab')
    assert ui.js("document.activeElement.classList.contains('coverFileBtn')")   # タイルの次は「ファイルから読み込む…」
    ui.key('Tab')
    assert ui.js("document.activeElement.classList.contains('coverClose')")
    ui.key('Escape')
    ui.until(f"!{DLG}")
    assert ui.js("document.activeElement.classList.contains('coverBtn')")
    assert Path(ui.serve.SETTINGS.cover_path('p1')).read_bytes() == (ICONS / 'covers' / 'orange.png').read_bytes()


def test_upload_file(ui, tmp_path):
    # ファイルは画面で正方形に切り抜いて 512 までに縮め、中身だけを送る。読み込んだ画像もタイルに出して印を付ける
    f = tmp_path / 'wide.png'
    f.write_bytes(png(900, 600))
    ui.open('recommend')
    ui.serve.SETTINGS.set_cover('p1', (ICONS / 'covers' / 'red.png').read_bytes(), 'png')
    open_dialog(ui)
    assert ui.js(ON) == ['red']
    obj = ui.call('Runtime.evaluate', expression="document.querySelector('.coverDlg input[type=file]')")['result']['objectId']
    ui.call('DOM.setFileInputFiles', files=[str(f)], objectId=obj)
    ui.until(f"JSON.stringify({ON})==='[\"custom\"]'", what='読み込んだ画像に印')
    data = Path(ui.serve.SETTINGS.cover_path('p1')).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack('>II', data[16:24]) == (512, 512)
    assert ui.js("document.querySelector('.coverDlg .coverCustom .coverTile').innerText.trim()") == '読み込んだ画像'
    custom = ui.js("document.querySelector('.coverDlg .coverCustom img').src")
    assert custom.startswith('data:image/png;base64,') and ui.js(SUM_IMG) == custom
    # ほかの色を選んでも、開いている間は読み込んだ画像のタイルが残り、押すと戻せる
    ui.click_sel('.coverDlg .coverTile[data-cover="orange"]')
    ui.until(f"JSON.stringify({ON})==='[\"orange\"]'", what='橙に印')
    assert ui.js("document.querySelectorAll('.coverDlg .coverCustom .coverTile').length") == 1
    ui.click_sel('.coverDlg .coverCustom .coverTile')
    ui.until(f"JSON.stringify({ON})==='[\"custom\"]'", what='読み込んだ画像に戻す')
    assert Path(ui.serve.SETTINGS.cover_path('p1')).read_bytes() == data
    ui.key('Escape')
    # 開き直しても読み込んだ画像に印
    ui.open('recommend')
    open_dialog(ui)
    assert ui.js(ON) == ['custom']
    # 画像でないファイル: 知らせを出し、画像は変えない
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not an image')
    obj = ui.call('Runtime.evaluate', expression="document.querySelector('.coverDlg input[type=file]')")['result']['objectId']
    ui.call('DOM.setFileInputFiles', files=[str(bad)], objectId=obj)
    ui.until("document.querySelector('.coverDlg .coverMsg').innerText.includes('画像を読めませんでした')", what='読めない画像の知らせ')
    assert ui.js(ON) == ['custom'] and Path(ui.serve.SETTINGS.cover_path('p1')).read_bytes() == data


def test_open_from_settings(ui):
    pid = ui.serve.SETTINGS.add_preset('ワンセイバー', copy_from='p1')
    ui.serve.SETTINGS.update({'active_preset': pid})
    ui.open('settings')
    ui.until("document.querySelector('.coverCtl .coverBtn img').src.startsWith('data:')", what='設定の画像')
    # 欄のボタンは「画像を選ぶ…」だけ（アプリのアイコンに戻すのは小さな画面の「緑」のタイル）
    assert ui.js("[...document.querySelectorAll('.coverCtl button:not(.coverBtn)')].map(b=>b.innerText.trim())") == ['画像を選ぶ…']
    assert '色違いの画像から選ぶか、ファイルから読み込みます' in ui.js("document.getElementById('grp_presetCard').innerText")
    open_dialog(ui, '.coverCtl .coverPick')
    assert '「ワンセイバー」のセット' in ui.js(f"{DLG}.innerText")
    ui.click_sel('.coverDlg .coverTile[data-cover="slate"]')
    ui.until(f"JSON.stringify({ON})==='[\"slate\"]'", what='灰に印')
    slate = data_url(ICONS / 'covers' / 'slate.png')
    ui.until(f"document.querySelector('.coverCtl .coverBtn img').src==={slate!r}", what='設定の画像が灰')
    assert Path(ui.serve.SETTINGS.cover_path(pid)).read_bytes() == (ICONS / 'covers' / 'slate.png').read_bytes()
    assert ui.serve.SETTINGS.cover_path('p1') is None   # ほかのセットは変わらない
    ui.key('Escape')
    ui.until(f"!{DLG}")
    assert ui.js("document.activeElement.classList.contains('coverPick')")
    # 画像そのものを押しても開ける。おすすめの画面のそのセットの画像も灰
    open_dialog(ui, '.coverCtl .coverBtn')
    assert ui.js(ON) == ['slate']
    ui.key('Escape')
    ui.open('recommend')
    assert ui.js(SUM_IMG) == slate
