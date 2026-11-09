# 出力先（Beat Saber のフォルダ）を探す・選ぶ: core/output/bsfind.py・serve.py の bs/*・app.py のフォルダ選択
import json
import os
from types import SimpleNamespace

import pytest

from core.output import bsfind
from core.settings import Settings
from fakebs import make_bs, make_shared


@pytest.mark.parametrize('sub', [(), ('Beat Saber_Data',), ('Beat Saber_Data', 'CustomLevels'), ('UserData',), ('Playlists',)])
def test_resolve_from_inside(tmp_path, sub):
    bs = make_bs(tmp_path, 'steamapps', 'common', 'Beat Saber')
    assert bsfind.resolve(str(bs.joinpath(*sub))) == [str(bs)]


def test_resolve_from_parent(tmp_path):
    bs = make_bs(tmp_path, 'steamapps', 'common', 'Beat Saber')
    assert bsfind.resolve(str(tmp_path / 'steamapps' / 'common')) == [str(bs)]
    assert bsfind.resolve(str(tmp_path)) == [str(bs)]           # Steam のライブラリそのもの


def test_resolve_many_and_none(tmp_path):
    a = make_bs(tmp_path, 'BSManager', 'BSInstances', '1.39.1')
    b = make_bs(tmp_path, 'BSManager', 'BSInstances', '1.40.8')
    assert sorted(bsfind.resolve(str(tmp_path / 'BSManager' / 'BSInstances'))) == sorted([str(a), str(b)])
    (tmp_path / 'other').mkdir()   # BSManager のフォルダの外
    assert bsfind.resolve(str(tmp_path / 'other')) == []


def test_unc_and_relative_are_not_touched():
    assert bsfind.resolve(r'\\server\share\Beat Saber') == []
    assert bsfind.resolve('Beat Saber') == []
    assert not bsfind.is_root(r'\\server\share\Beat Saber')


def test_parse_libraryfolders():
    text = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"G:\\\\SteamLibrary"\n\t}\n}'
    assert bsfind.parse_libraryfolders(text) == ['C:\\Program Files (x86)\\Steam', 'G:\\SteamLibrary']


def test_find_installs(tmp_path, monkeypatch):
    steam = make_bs(tmp_path, 'lib1', 'steamapps', 'common', 'Beat Saber')
    ocu = make_bs(tmp_path, 'oculus', 'Software', 'hyperbolic-magnetism-beat-saber')
    bsm = make_bs(tmp_path, 'home', 'BSManager', 'BSInstances', '1.40.8')
    monkeypatch.setattr(bsfind, 'steam_libraries', lambda: [str(tmp_path / 'lib1'), str(tmp_path / 'lib2')])
    monkeypatch.setattr(bsfind, 'oculus_libraries', lambda: [str(tmp_path / 'oculus')])
    got = bsfind.find_installs(home=str(tmp_path / 'home'), appdata=str(tmp_path / 'appdata'))
    got = [g for g in got if str(tmp_path) in g['path']]   # この PC に本物があれば、それは除いて比べる
    assert [(g['path'], g['source']) for g in got] == [(str(bsm), 'bsmanager'), (str(steam), 'steam'), (str(ocu), 'oculus')]
    assert bsfind.describe(str(steam)) == {'hasPlaylists': True, 'playlists': 1, 'sharedPlaylists': None}


def write_bsm_config(appdata, conf):
    d = appdata / 'bs-manager'
    d.mkdir(parents=True)
    (d / 'config.json').write_text(conf if isinstance(conf, str) else json.dumps(conf), encoding='utf-8')


def test_find_bsmanager_in_installation_folder(tmp_path, monkeypatch):
    # BSManager の入れ先を変えている（ホームの下に無い）。最後に起動した版の path はドライブを移す前の古い物
    monkeypatch.setattr(bsfind, 'steam_libraries', lambda: [])
    monkeypatch.setattr(bsfind, 'oculus_libraries', lambda: [])
    old = make_bs(tmp_path, 'drive', 'BSManager', 'BSInstances', '1.39.1')
    new = make_bs(tmp_path, 'drive', 'BSManager', 'BSInstances', '1.40.8')
    write_bsm_config(tmp_path / 'appdata', {'installation-folder': str(tmp_path / 'drive'),
                                             'last-version-launched': {'BSVersion': '1.40.8', 'path': 'Z:\\BSManager\\BSInstances\\1.40.8'}})
    got = bsfind.find_installs(home=str(tmp_path / 'home'), appdata=str(tmp_path / 'appdata'))
    got = [g for g in got if str(tmp_path) in g['path']]
    assert got == [{'path': str(new), 'source': 'bsmanager', 'last': True}, {'path': str(old), 'source': 'bsmanager'}]


@pytest.mark.parametrize('conf', ['{壊れた', '[]', {'installation-folder': 5, 'last-version-launched': 'x'},
                                  {'installation-folder': '\\\\server\\share'}])
def test_bsmanager_config_broken(tmp_path, conf):
    write_bsm_config(tmp_path / 'appdata', conf)
    bsm = make_bs(tmp_path, 'home', 'BSManager', 'BSInstances', '1.40.8')
    assert bsfind.bsmanager_installs(str(tmp_path / 'home'), str(tmp_path / 'appdata')) == [(str(bsm), False)]


def test_shared_playlists(tmp_path):
    inst, shared = make_shared(tmp_path)
    d = bsfind.describe(str(inst))
    assert d['hasPlaylists'] and d['playlists'] == 1
    assert os.path.normcase(d['sharedPlaylists']) == os.path.normcase(os.path.realpath(shared))


@pytest.mark.parametrize('sub', [('SharedContent',), ('SharedContent', 'Playlists'), (), ('BSInstances',)])
def test_resolve_bsmanager_folders(tmp_path, sub):
    # 共通コンテンツの場所（SharedContent）や BSManager のフォルダを選んでも、版のフォルダにする
    inst, _ = make_shared(tmp_path)
    assert bsfind.resolve(str((tmp_path / 'BSManager').joinpath(*sub))) == [str(inst)]
    assert bsfind.resolve(str(tmp_path)) == [str(inst)]   # BSManager の入れ先（ドライブの直下など）


# ---- serve.py の bs/*（画面からは候補の番号だけ） ----

def test_serve_bs_find_and_use(tmp_path, monkeypatch):
    import serve
    bs = make_bs(tmp_path, 'Beat Saber')
    monkeypatch.setattr(bsfind, 'find_installs', lambda home=None: [{'path': str(bs), 'source': 'bsmanager', 'last': True}])
    r = serve.API['bs/find']({})
    assert r['candidates'][0]['path'] == str(bs) and r['candidates'][0]['id'] == 0 and r['candidates'][0]['last']
    for bad in ({'id': 5}, {'id': '0'}, {'id': True}, {'path': 'C:\\x'}):
        with pytest.raises(serve.ApiError):
            serve.API['bs/use'](bad)
    assert serve.API['bs/use']({'id': 0})['path'] == str(bs)
    assert serve.SETTINGS.get('beat_saber_dir') == str(bs)
    serve.SETTINGS.reset(['beat_saber_dir'])


# ---- app.py のフォルダ選択（偽のダイアログ） ----

class FakeWebview:
    class FileDialog:
        FOLDER, OPEN = 'folder', 'open'

    def __init__(self, answer):
        self.answer = answer
        self.windows = [SimpleNamespace(create_file_dialog=lambda kind, **kw: self.answer)]


def test_app_pick_inside_folder(tmp_path):
    import app
    bs = make_bs(tmp_path, 'Beat Saber')
    s = Settings(str(tmp_path / 'config'))
    api = app._make_api(s, FakeWebview([str(bs / 'Beat Saber_Data' / 'CustomLevels')]))
    r = api.pick_beat_saber_dir()
    assert r['ok'] and r['path'] == str(bs) and s.get('beat_saber_dir') == str(bs)


def test_app_pick_many_and_none(tmp_path):
    import app
    make_bs(tmp_path, 'BSManager', 'BSInstances', 'a')
    make_bs(tmp_path, 'BSManager', 'BSInstances', 'b')
    s = Settings(str(tmp_path / 'config'))
    offered = []
    api = app._make_api(s, FakeWebview([str(tmp_path / 'BSManager' / 'BSInstances')]), lambda paths, src: offered.extend(paths) or [{'id': 0}])
    r = api.pick_beat_saber_dir()
    assert r['error'] == 'choose' and len(offered) == 2 and s.get('beat_saber_dir') == ''
    (tmp_path / 'x').mkdir()
    assert app._make_api(s, FakeWebview([str(tmp_path / 'x')])).pick_beat_saber_dir()['error'] == 'not-beat-saber'
    assert app._make_api(s, FakeWebview(None)).pick_beat_saber_dir()['error'] == 'cancelled'
