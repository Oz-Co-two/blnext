# 作り物の Beat Saber のフォルダ（出力先を探す・選ぶテスト用）。
# BSManager の共通コンテンツ（版の Playlists が SharedContent/Playlists へのジャンクション）も作れる
import pytest


def make_bs(base, *parts):
    """Beat Saber.exe・Playlists（プレイリスト1個）・CustomLevels・UserData があるフォルダ"""
    d = base.joinpath(*parts)
    (d / 'Beat Saber_Data' / 'CustomLevels').mkdir(parents=True)
    (d / 'UserData').mkdir()
    (d / 'Playlists').mkdir()
    (d / 'Playlists' / 'a.bplist').write_text('{}', encoding='utf-8')
    (d / 'Beat Saber.exe').write_bytes(b'')
    return d


def make_shared(base):
    """base/BSManager の下に、共通コンテンツにした版 1.40.8 を作る。返り値 (版のフォルダ, SharedContent/Playlists)"""
    _winapi = pytest.importorskip('_winapi')
    root = base / 'BSManager'
    shared = root / 'SharedContent' / 'Playlists'
    shared.mkdir(parents=True)
    (shared / 'x.bplist').write_text('{}', encoding='utf-8')
    inst = root / 'BSInstances' / '1.40.8'
    inst.mkdir(parents=True)
    (inst / 'Beat Saber.exe').write_bytes(b'')
    _winapi.CreateJunction(str(shared), str(inst / 'Playlists'))
    return inst, shared


def fake_installs(serve, base, monkeypatch):
    """serve.py の自動で探す結果を作り物にする: BSManager（共通コンテンツ・最後に起動した版）と Steam。
    返り値 (BSManager の版, SharedContent/Playlists, Steam の Beat Saber)"""
    inst, shared = make_shared(base)
    steam = make_bs(base, 'Steam', 'steamapps', 'common', 'Beat Saber')
    found = [{'path': str(inst), 'source': 'bsmanager', 'last': True}, {'path': str(steam), 'source': 'steam'}]
    monkeypatch.setattr(serve.bsfind, 'find_installs', lambda home=None, appdata=None: [dict(f) for f in found])
    return inst, shared, steam
