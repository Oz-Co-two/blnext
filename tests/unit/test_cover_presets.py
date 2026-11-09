# serve.py の用意した画像（アプリのアイコンの色違い。web/icons/covers/<名前>.png）: cover/preset・cover/get の choice、
# 難しさ違いのセットを作る時の画像。準備（作り物のデータ・サーバー・呼び方）は test_serve_data.py の物を使う
import base64, os

import serve
from test_serve_data import built, call, data, read_json, server   # noqa: F401（built・data・server は fixture）


def png_of(name):
    with open(os.path.join(serve.WEB_DIR, 'icons', 'covers', name + '.png'), 'rb') as f:
        return f.read()


def test_preset_files_exist():
    # 用意した画像は全部あり、PNG で、出力の大きさの上限より十分小さい（.bplist に base64 で入る）
    for name in serve.COVER_PRESETS:
        raw = png_of(name)
        assert raw.startswith(serve.PNG_SIG) and len(raw) < 64 * 1024
    assert len(set(map(png_of, serve.COVER_PRESETS))) == len(serve.COVER_PRESETS)   # どれも違う絵


def test_pick_preset_and_back_to_app(server, data):
    g = call(server, 'cover/get')
    assert g['choice'] == 'app' and not g['custom'] and g['presets'] == list(serve.COVER_PRESETS)
    r = call(server, 'cover/preset', {'name': 'purple'})
    assert r['ok'] and r['custom'] and r['choice'] == 'purple'
    assert r['dataUrl'] == 'data:image/png;base64,' + base64.b64encode(png_of('purple')).decode()
    assert call(server, 'cover/get')['choice'] == 'purple'                                      # 覚えている
    assert read_json(call(server, 'export')['path'])['image'] == r['dataUrl']                   # 出力に入る
    assert call(server, 'cover/get', {'preset': 'p1'})['choice'] == 'purple'
    r = call(server, 'cover/preset', {'name': 'app'})
    assert r['ok'] and not r['custom'] and r['choice'] == 'app'


def test_uploaded_image_is_not_a_preset(server, data):
    jpeg = b'\xff\xd8\xff\xe0' + b'1' * 50
    r = call(server, 'cover/set', {'data': base64.b64encode(jpeg).decode()})
    assert r['custom'] and r['choice'] is None                      # ファイルから読み込んだ画像
    same = call(server, 'cover/set', {'data': base64.b64encode(png_of('red')).decode()})
    assert same['choice'] == 'red'                                  # 中身が同じなら用意した画像とみなす


def test_bad_names_change_nothing(server, data):
    call(server, 'cover/preset', {'name': 'blue'})
    for bad in ('../app', 'green', '', None, 5, ['blue'], {'a': 1}, 'blue.png', 'BLUE'):
        assert call(server, 'cover/preset', {'name': bad})['error'] == 'bad-request'
    assert call(server, 'cover/get')['choice'] == 'blue'
    assert call(server, 'cover/preset', {'name': 'red', 'preset': 'p9'})['error'] == 'no-preset'


def test_difficulty_sets_get_colors(server, data):
    # 難しさ違いのセットを作ると、着実＝青・チャレンジング＝赤（標準は写したセットの画像のまま）
    call(server, 'cover/preset', {'name': 'orange'})
    r = call(server, 'presets/difficulty-set', {'levels': ['steady', 'standard', 'challenge'], 'size': 3})
    got = [call(server, 'cover/get', {'preset': pid})['choice'] for pid in r['ids']]
    assert got == ['blue', 'orange', 'red']
    assert call(server, 'cover/get', {'preset': 'p1'})['choice'] == 'orange'                   # 写した元は変えない
