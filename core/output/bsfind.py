# Beat Saber のフォルダ（Beat Saber.exe がある所＝出力先 Playlists/ の親）を探す。
#  - 自動で探す（find_installs）: BSManager（%APPDATA%/bs-manager/config.json の installation-folder と、初期値のホームの下の
#    BSManager/BSInstances/<版>。最後に起動した版を先頭に）・Steam（レジストリの SteamPath と steamapps/libraryfolders.vdf の
#    各ライブラリ）・Meta/Oculus（レジストリの Libraries の OriginalPath）・よくある場所
#  - 利用者がダイアログで選んだフォルダから決める（resolve）: Beat Saber の中のどの階層（CustomLevels・Playlists・UserData・
#    Beat Saber_Data など）を選んでも、上へたどって Beat Saber.exe のある所にする。steamapps/common や BSInstances のような
#    1つ上を選んだ時は中から探す。BSManager の SharedContent（共通コンテンツ）の中を選んだ時は、隣の BSInstances の版にする
#  - BSManager で共通コンテンツにすると、版の Playlists は SharedContent/Playlists へのジャンクションになる。書き込み先は版の
#    Playlists のままでよく（実体は共通の所に入る）、画面にはつながり先を見せる（describe の sharedPlaylists）
#  - ネットワーク上のパス（\\server\...）は触らない（存在を確かめるだけで接続と認証が起きるため。NLM のセキュリティ点検）
import json, os, re

EXE = 'Beat Saber.exe'
STEAM_SUB = ('steamapps', 'common', 'Beat Saber')
OCULUS_SUB = ('Software', 'hyperbolic-magnetism-beat-saber')
BSM_INSTANCES = 'BSInstances'
UP_LEVELS = 6


def _local(p):
    """ドライブ名から始まる絶対パスか（UNC・相対パスは受けない）"""
    return isinstance(p, str) and re.match(r'^[A-Za-z]:[\\/]', p) is not None


def is_root(p):
    return _local(p) and os.path.isfile(os.path.join(p, EXE))


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def resolve(path):
    """選んだフォルダから Beat Saber のフォルダの候補（重複なし）を返す。見つからなければ []"""
    if not _local(path):
        return []
    cur = os.path.abspath(path)
    for _ in range(UP_LEVELS + 1):     # そのもの・上の階層
        if is_root(cur):
            return [cur]
        inst = _instances(os.path.join(cur, BSM_INSTANCES))   # BSManager のフォルダや SharedContent の中を選んだ
        if inst:
            return inst
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    found = []
    base = os.path.abspath(path)
    for sub in ([], ['Beat Saber'], list(STEAM_SUB), list(OCULUS_SUB), ['BSManager', BSM_INSTANCES]):   # 中（1つ上を選んだ時）
        d = os.path.join(base, *sub)
        if sub and is_root(d):
            found.append(d)
        try:
            for name in os.listdir(d) if os.path.isdir(d) else []:
                c = os.path.join(d, name)
                if is_root(c):
                    found.append(c)
        except OSError:
            pass
    return _uniq(found)


def _instances(d):
    """BSManager の BSInstances の中の版のフォルダ（Beat Saber.exe がある物だけ・名前順）"""
    try:
        names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    except OSError:
        return []
    return [os.path.join(d, n) for n in names if is_root(os.path.join(d, n))]


def _uniq(paths):
    seen, out = set(), []
    for p in paths:
        k = _norm(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def parse_libraryfolders(text):
    """Steam の libraryfolders.vdf からライブラリのパスを取り出す（"path" "C:\\\\…" の \\\\ は \\）"""
    return [m.replace('\\\\', '\\') for m in re.findall(r'"path"\s+"([^"]+)"', text or '')]


def _reg(root_name, key, name):
    try:
        import winreg
        root = getattr(winreg, root_name)
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError, AttributeError):
        return None


def _reg_subkeys(root_name, key):
    try:
        import winreg
        root = getattr(winreg, root_name)
        out = []
        with winreg.OpenKey(root, key) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    return out
                i += 1
    except (ImportError, OSError, AttributeError):
        return []


def steam_libraries():
    # HKLM の InstallPath を先に（HKCU の SteamPath は小文字・/ 区切りで入っていることがあり、画面の表示が読みにくい）
    roots = [_reg('HKEY_LOCAL_MACHINE', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
             _reg('HKEY_LOCAL_MACHINE', r'SOFTWARE\Valve\Steam', 'InstallPath'),
             _reg('HKEY_CURRENT_USER', r'Software\Valve\Steam', 'SteamPath')]
    libs = []
    for r in roots:
        if not r:
            continue
        r = os.path.normpath(r)
        libs.append(r)
        vdf = os.path.join(r, 'steamapps', 'libraryfolders.vdf')
        try:
            with open(vdf, encoding='utf-8', errors='replace') as f:
                libs.extend(os.path.normpath(p) for p in parse_libraryfolders(f.read()))
        except OSError:
            pass
    return _uniq([p for p in libs if _local(p)])


def oculus_libraries():
    base = r'Software\Oculus VR, LLC\Oculus\Libraries'
    out = []
    for sub in _reg_subkeys('HKEY_CURRENT_USER', base):
        p = _reg('HKEY_CURRENT_USER', base + '\\' + sub, 'OriginalPath')
        if _local(p):
            out.append(os.path.normpath(p))
    return _uniq(out)


def bsmanager_config(appdata=None):
    """BSManager の設定（%APPDATA%/bs-manager/config.json）。無い・壊れている時は {}"""
    appdata = appdata or os.environ.get('APPDATA') or ''
    if not _local(appdata):
        return {}
    try:
        with open(os.path.join(appdata, 'bs-manager', 'config.json'), encoding='utf-8') as f:
            conf = json.load(f)
    except (OSError, ValueError):
        return {}
    return conf if isinstance(conf, dict) else {}


def bsmanager_installs(home, appdata=None):
    """BSManager の版のフォルダ [(path, 最後に起動した版か)]。最後に起動した版が先頭"""
    conf = bsmanager_config(appdata)
    roots = []
    folder = conf.get('installation-folder')   # BSManager の設定で変えた入れ先（初期値はホーム）
    if _local(folder):
        roots.append(os.path.join(folder, 'BSManager'))
    roots.append(os.path.join(home, 'BSManager'))
    # 最後に起動した版: path は BSManager の場所を移すと古いまま残る（ドライブ名が違う等）ので、版のフォルダ名だけ見る
    last = conf.get('last-version-launched')
    last = last if isinstance(last, dict) else {}
    lp = last.get('path')
    names = {n for n in (os.path.basename(os.path.normpath(lp)) if isinstance(lp, str) and lp else '', last.get('BSVersion'))
             if isinstance(n, str) and n}
    found = [p for r in _uniq(roots) for p in _instances(os.path.join(r, BSM_INSTANCES))]
    return sorted(((p, os.path.basename(p) in names) for p in found), key=lambda x: not x[1])


def find_installs(home=None, appdata=None):
    """見つかった Beat Saber のフォルダ [{path, source, last?}]（Beat Saber.exe があることを確かめた物だけ）。
    BSManager を入れている人はそこから起動しているはずなので、BSManager の版を先に（最後に起動した版は last: True）"""
    home = home or os.environ.get('USERPROFILE') or os.path.expanduser('~')
    cands = [(p, 'bsmanager', last) for p, last in bsmanager_installs(home, appdata)]
    for lib in steam_libraries():
        cands.append((os.path.join(lib, *STEAM_SUB), 'steam', False))
    for lib in oculus_libraries():
        cands.append((os.path.join(lib, *OCULUS_SUB), 'oculus', False))
    cands += [(r'C:\Program Files (x86)\Steam\steamapps\common\Beat Saber', 'steam', False),
              (r'C:\Program Files\Oculus\Software\Software\hyperbolic-magnetism-beat-saber', 'oculus', False)]
    out, seen = [], set()
    for p, src, last in cands:
        if is_root(p) and _norm(p) not in seen:
            seen.add(_norm(p))
            out.append({'path': os.path.normpath(p), 'source': src, **({'last': True} if last else {})})
    return out


def shared_playlists(path):
    """Playlists が別の場所へのリンク（BSManager の共通コンテンツのジャンクション等）なら、つながり先。違えば None"""
    pl = os.path.join(path, 'Playlists')
    if not os.path.isdir(pl):
        return None
    try:
        real = os.path.realpath(pl)
    except (OSError, ValueError):
        return None
    return real if _norm(real) != _norm(pl) else None


def describe(path):
    """候補の説明（画面に出す）: Playlists の有無とプレイリストの数・共通のフォルダへのつながり先"""
    pl = os.path.join(path, 'Playlists')
    n = 0
    try:
        n = sum(1 for f in os.listdir(pl) if f.lower().endswith(('.bplist', '.json')))
    except OSError:
        pass
    return {'hasPlaylists': os.path.isdir(pl), 'playlists': n, 'sharedPlaylists': shared_playlists(path)}
