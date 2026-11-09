# ワンクリック（bsplaylist://）の部品: 受けるアプリの見分け・リンクの形・一時的に配る棚（core/output/oneclick.py）
import contextlib, threading, types

import pytest

from core.output import oneclick


def fake_winreg(cmd):
    """bsplaylist の open のコマンドが cmd（None なら登録なし）のレジストリ"""
    def open_key(root, path):
        assert root == 'HKCR' and path == r'bsplaylist\shell\open\command'
        if cmd is None:
            raise OSError('not found')
        return contextlib.nullcontext('key')
    return types.SimpleNamespace(HKEY_CLASSES_ROOT='HKCR', OpenKey=open_key, QueryValueEx=lambda k, name: (cmd, 1))


@pytest.mark.parametrize('cmd,app', [
    (r'"C:\Users\x\AppData\Local\Programs\bs-manager\BSManager.exe" "%1"', 'BSManager'),   # この PC の実際の登録
    (r'"C:\Tools\ModAssistant.exe" "--install" "%1"', 'ModAssistant'),
    (r'"D:\Other\PlaylistTool.exe" "%1"', 'PlaylistTool'),
    ('something odd', 'other'),
    ('   ', None),
    (None, None),
])
def test_handler_app(cmd, app):
    assert oneclick.handler_app(fake_winreg(cmd)) == app


def test_handler_app_real_registry_does_not_fail():
    r = oneclick.handler_app()   # この PC の本物（登録の有無はどちらでもよい）
    assert r is None or isinstance(r, str)


def test_link():
    # BSManager は bsplaylist://playlist/ の後ろをそのまま URL として読む（BeatSaver のボタンと同じ形）
    assert oneclick.link(1234, 'tok', 'a.bplist') == 'bsplaylist://playlist/http://127.0.0.1:1234/oneclick/tok/a.bplist'


def test_launch_opens_the_link(monkeypatch):
    # 外の起動は core/launch.py を通す（exe 版の DLL の探し先を BSManager に引き継がせない）
    from core import launch
    opened = []
    monkeypatch.setattr(launch, 'start', opened.append)
    oneclick.launch('bsplaylist://playlist/x')
    assert opened == ['bsplaylist://playlist/x']


def test_shelf_expires_and_checks_name():
    sh = oneclick.Shelf(ttl=10)
    tok = sh.put(b'x', 'a.bplist', now=100)
    assert len(tok) >= 30                                 # 推測できない長さ
    assert sh.get(tok, 'a.bplist', now=105) == b'x'
    assert sh.get(tok, 'a.bplist', now=105) == b'x'      # 何度でも読める（アプリが読み直しても）
    assert sh.get(tok, 'b.bplist', now=105) is None       # ファイル名が違う
    assert sh.get('nope', 'a.bplist', now=105) is None
    assert sh.get(tok, 'a.bplist', now=110) is None       # 期限切れ
    tok2 = sh.put(b'y', 'a.bplist', now=200)
    assert tok not in sh._items and tok2 in sh._items     # 期限切れは次に置く時に捨てる


def test_shelf_wait_fetched():
    # まとめて渡す時は、アプリが前の分を読みに来たのを待ってから次を渡す（serve.py の export/oneclick/all）
    sh = oneclick.Shelf()
    tok = sh.put(b'x', 'a.bplist')
    assert not sh.wait_fetched(tok, 0.05)                 # まだ来ていない
    threading.Timer(0.1, lambda: sh.get(tok, 'a.bplist')).start()
    assert sh.wait_fetched(tok, 5)                        # 別のスレッドで読みに来たら起きる
    assert sh.wait_fetched(tok, 0)                        # 一度来れば待たない
    tok2 = sh.put(b'y', 'b.bplist')
    assert sh.get(tok2, 'other.bplist') is None
    assert not sh.wait_fetched(tok2, 0.05)                # 名前が違って返さなかった分は数えない
