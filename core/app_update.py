# アプリ本体の更新。NLM（Non-Linear Mapper Oz-Co2版）の app_update.py を流用し、使用中のファイルへの対策を足した
# （docs/investigation.md の「使用中のファイルと自動更新」）。
#
# 流れ: 起動時（設定 update_check で切れる）または設定の「更新を確認」で GitHub Releases の最新版の update.json を読む
#   → 新しい版があれば画面に更新内容（CHANGELOG の見出し）を出し、「更新する」なら
#   ① zip をダウンロードして update.json の SHA-256・サイズと照合 → exe 隣の _update/new/ へ展開（download）
#   ② 展開した「新しい版の exe」を --apply-update 付きで起動し、今のアプリは終了する（apply）
#   ③ 新しい版の exe が、古いアプリの終了を待ってから exe と _internal を差し替え、起動し直す（apply_main）
#   ④ 起動し直したアプリが _update/ と古い退避を片付け、「更新しました」を出す（startup）
# 差し替えを新しい版の側で行うのは、起動中の exe・_internal は自分自身を上書きできないため。
#
# ★版をまたぐ約束（変えると旧版から更新できなくなる）:
#  - update.json の置き場所（REPO の releases/latest/download/update.json）と形式（format=1 の項目）
#  - apply_main の引数（--apply-update --target <exe のフォルダ> --pid <終了を待つ pid> --lang <ja|en>）
#    ＝旧版が起動するのは「新しい版の」exe なので、新しい版はこの引数を受け付け続けること
#  - 配布 zip の中身の形（最上位 blnext/ の下に exe・_internal・案内文・ライセンス）
# 安全のための約束:
#  - 取得先 URL はここに固定。画面から URL・パスを受け取らない
#  - zip は update.json の SHA-256・サイズと照合してから展開し、フォルダ外へ出る名前を拒否する
#  - 差し替えるのは REPLACE_ITEMS だけ。config/ data/ logs/ exports/（利用者のデータ）には触らない
# 使用中のファイルへの対策:
#  - 差し替えは削除でなく名前の変更で退避する（外のアプリが _internal の DLL を読み込んだままでも通る）。
#    前の退避の削除は失敗してよく、次に起動した時にやり直す（startup）
#  - 差し替え役・起動し直しの起動は core/launch.py を通す（DLL の探し先を引き継がせない）
#  - それでも失敗した時は Restart Manager で使っているアプリの名前を出して案内する（_busy_apps）
import hashlib, json, logging, os, re, shutil, sys, threading, time, urllib.error, urllib.request, zipfile

from . import launch
from .version import app_version as read_app_version

log = logging.getLogger('bln.update')

REPO = 'Oz-Co-two/blnext'
REPO_URL = f'https://github.com/{REPO}'
UPDATE_URL = f'{REPO_URL}/releases/latest/download/update.json'
ZIP_URL = f'{REPO_URL}/releases/download/{{tag}}/{{name}}'
RELEASES_URL = f'{REPO_URL}/releases/latest'
NOTES_URL = f'{REPO_URL}/blob/master/CHANGELOG.md'
FORMAT = 1                    # update.json の形式の版。上げたら旧版は自動更新せず「リリースページを開く」になる
APP_NAME = 'blnext'
EXE_NAME = 'blnext.exe'
ZIP_TOP = 'blnext'            # 配布 zip の最上位フォルダ
# 差し替える物（これ以外には触らない）。どちらかの側に無い物は飛ばす。
# 差し替えは新しい版の exe が自分の一覧で行うので、ここに足した物は古い版からの更新でも届く。
# ここに足したら blnext.spec の最後のコピーにも足す
REPLACE_ITEMS = (EXE_NAME, '_internal', 'はじめにお読みください.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.txt')
UPDATE_DIR = '_update'        # exe 隣の作業フォルダ（ダウンロード・展開先。更新後に消す）
PREV_PREFIX = '_previous_'    # 差し替え前の版の退避（_previous_v0.1.0/。1 世代だけ残す）
MAX_ZIP = 300 * 1024 * 1024
MAX_UNZIP = 800 * 1024 * 1024
_VER_RE = re.compile(r'v(\d{1,4})\.(\d{1,4})\.(\d{1,4})')
_lock = threading.Lock()


class UpdateError(Exception):
    """利用者に見せる失敗。code は画面で文言にする（web/js/update.js）"""
    def __init__(self, code, detail=''):
        super().__init__(code)
        self.code, self.detail = code, str(detail)[:500]


def vtuple(v):
    m = _VER_RE.fullmatch(str(v or ''))
    return tuple(int(x) for x in m.groups()) if m else None


def _user_agent():
    return APP_NAME


def _check_net(url):
    """テスト（BLN_NO_NET=1）では本物の配布元へ出ない。テストの偽の配布元は http://127.0.0.1"""
    if os.environ.get('BLN_NO_NET') == '1' and url.startswith('https:'):
        raise UpdateError('network', 'BLN_NO_NET')


def _get(url, limit):
    _check_net(url)
    req = urllib.request.Request(url, headers={'User-Agent': _user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError('notFound' if e.code == 404 else 'network', e)   # 404＝最新リリースに update.json が無い
    except Exception as e:
        raise UpdateError('network', e)
    if len(data) > limit:
        raise UpdateError('tooLarge')
    return data


def _clean_notes(notes):
    """更新内容（版ごとの見出しの一覧）。画面に出す文字なので形と長さだけ整える（表示は textContent）"""
    out = []
    for n in (notes if isinstance(notes, list) else [])[:200]:
        if not isinstance(n, dict) or not vtuple(n.get('version')):
            continue
        item = {'version': n['version']}
        for lang in ('ja', 'en'):
            xs = n.get(lang)
            item[lang] = [str(x)[:300] for x in xs[:60] if isinstance(x, str)] if isinstance(xs, list) else []
        out.append(item)
    return out


def check_manifest(raw):
    """update.json を検査して返す。format が違う（新しすぎる）時は manual=True（自動更新しない）"""
    try:
        m = json.loads(raw)
        ver = str(m['version'])
        ok = m.get('name') == APP_NAME and vtuple(ver) is not None and isinstance(m.get('format'), int)
    except Exception:
        ok = False
    if not ok:
        raise UpdateError('badManifest')
    res = {'version': ver, 'notes': _clean_notes(m.get('notes')), 'manual': m['format'] != FORMAT, 'zip': None}
    if not res['manual']:
        z = m.get('zip') or {}
        try:
            zok = (z.get('name') == f'{ZIP_TOP}-{ver}.zip' and re.fullmatch(r'[0-9a-f]{64}', str(z.get('sha256')))
                   and 0 < int(z.get('size')) <= MAX_ZIP)
        except Exception:
            zok = False
        if not zok:
            raise UpdateError('badManifest')
        res['zip'] = {'name': z['name'], 'sha256': z['sha256'], 'size': int(z['size'])}
    return res


def _env_for_child():
    """別の PyInstaller 製 exe を起動する時の環境変数。親（このアプリ）の PyInstaller の設定を引き継がせない
    （引き継ぐと子の exe が正しく起動しない）"""
    env = {k: v for k, v in os.environ.items() if not k.startswith('_PYI_') and k not in ('_MEIPASS2', 'BLN_BASE_DIR', 'BLN_DATA_DIR')}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def _spawn(args, cwd):
    launch.spawn(args, cwd, _env_for_child())


class Updater:
    """serve.py から使う（1 プロセスに 1 つ）。exe_dir は配布フォルダ（exe の隣）"""
    def __init__(self, app_root, exe_dir, frozen):
        self.app_root, self.exe_dir, self.frozen = app_root, exe_dir, frozen
        self.job = {'state': 'idle'}   # idle / download / extract / ready / error / cancel
        self._cancel = False
        self.just_updated = None       # 起動し直した直後なら {'from','to'}（startup が入れる）

    @property
    def work(self):
        return os.path.join(self.exe_dir, UPDATE_DIR)

    def current(self):
        return read_app_version(self.app_root)

    def auto_mode(self):
        """'ok'＝自動更新できる / 'notExe'＝exe 版でない（開発用の python serve.py 等）/ 'readonly'＝フォルダに書き込めない"""
        if not self.frozen:
            return 'notExe'
        try:   # _update/ ではなく exe の隣で試す（起動直後の startup が _update/ を消している最中でも誤判定しない）
            p = os.path.join(self.exe_dir, '.bln-write-test')
            with open(p, 'w') as f:
                f.write('1')
            os.remove(p)
            return 'ok'
        except OSError:
            return 'readonly'

    def check(self):
        m = check_manifest(_get(UPDATE_URL, 1024 * 1024))
        cur = self.current()
        cv, lv = vtuple(cur), vtuple(m['version'])
        newer = bool(cv and lv > cv)
        notes = sorted([n for n in m['notes'] if cv and cv < vtuple(n['version']) <= lv],
                       key=lambda n: vtuple(n['version']), reverse=True)
        return {'current': cur, 'latest': m['version'], 'newer': newer, 'notes': notes,
                'size': m['zip']['size'] if m['zip'] else 0,
                'auto': 'manual' if m['manual'] else self.auto_mode()}

    # ---- ① ダウンロード・照合・展開（別スレッド。進み具合は progress で見る）----
    def start_download(self):
        with _lock:
            if self.job['state'] in ('download', 'extract'):
                return self.progress()
            if self.auto_mode() != 'ok':
                raise UpdateError('notAuto')
            m = check_manifest(_get(UPDATE_URL, 1024 * 1024))   # 画面から版を受け取らず、もう一度ここで読む
            if m['manual'] or not (vtuple(m['version']) > (vtuple(self.current()) or (0, 0, 0))):
                raise UpdateError('noUpdate')
            self._cancel = False
            self.job = {'state': 'download', 'version': m['version'], 'done': 0, 'total': m['zip']['size']}
            threading.Thread(target=self._download, args=(m,), name='update', daemon=True).start()
        return self.progress()

    def cancel(self):
        self._cancel = True
        return self.progress()

    def progress(self):
        return dict(self.job)

    def _download(self, m):
        z, ver = m['zip'], m['version']
        part = os.path.join(self.work, z['name'] + '.part')
        try:
            shutil.rmtree(os.path.join(self.work, 'new'), ignore_errors=True)
            os.makedirs(self.work, exist_ok=True)
            url = ZIP_URL.format(tag=ver, name=z['name'])
            _check_net(url)
            req = urllib.request.Request(url, headers={'User-Agent': _user_agent()})
            h, done = hashlib.sha256(), 0
            try:
                with urllib.request.urlopen(req, timeout=60) as r, open(part, 'wb') as f:
                    while True:
                        if self._cancel:
                            raise UpdateError('cancelled')
                        b = r.read(256 * 1024)
                        if not b:
                            break
                        done += len(b)
                        if done > z['size']:
                            raise UpdateError('hash')
                        h.update(b)
                        f.write(b)
                        self.job['done'] = done
            except UpdateError:
                raise
            except Exception as e:
                raise UpdateError('network', e)
            if done != z['size'] or h.hexdigest() != z['sha256']:
                raise UpdateError('hash')
            self.job['state'] = 'extract'
            extract_release(part, os.path.join(self.work, 'new'), ver)
            self.job['state'] = 'ready'
        except UpdateError as e:
            self.job = {'state': 'cancel' if e.code == 'cancelled' else 'error', 'error': e.code, 'detail': e.detail}
        except Exception as e:
            log.exception('update failed')
            self.job = {'state': 'error', 'error': 'extract', 'detail': str(e)[:500]}
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
            if self.job['state'] != 'ready':
                shutil.rmtree(os.path.join(self.work, 'new'), ignore_errors=True)

    # ---- ② 新しい版の exe に差し替えを任せる（この後、画面がアプリを閉じる）----
    def apply(self, lang):
        if self.job.get('state') != 'ready':
            raise UpdateError('notReady')
        new_dir = os.path.join(self.work, 'new', ZIP_TOP)
        try:
            _spawn([os.path.join(new_dir, EXE_NAME), '--apply-update', '--target', self.exe_dir,
                    '--pid', str(os.getpid()), '--lang', 'en' if lang == 'en' else 'ja'], new_dir)
        except OSError as e:
            raise UpdateError('spawn', e)
        return {}


def extract_release(zip_path, dst, ver):
    """照合済みの配布 zip を dst へ展開し、中身が ver のこのアプリであることを確かめる（テストからも呼ぶ）"""
    tmp = dst + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            total = 0
            for info in zf.infolist():
                name = info.filename
                parts = name.rstrip('/').split('/')
                # フォルダ外へ出る名前・絶対パス・ドライブ名・'\' を含む名前・最上位フォルダ以外は拒否（zip slip）
                if (not name or name.startswith('/') or '\\' in name or ':' in name
                        or any(p in ('..', '.', '') for p in parts) or parts[0] != ZIP_TOP):
                    raise UpdateError('badZip', name)
                total += info.file_size
                if total > MAX_UNZIP:
                    raise UpdateError('tooLarge')
            os.makedirs(tmp)
            zf.extractall(tmp)
        top = os.path.join(tmp, ZIP_TOP)
        if not os.path.isfile(os.path.join(top, EXE_NAME)) or read_app_version(os.path.join(top, '_internal')) != ver:
            raise UpdateError('badZip', 'version')
        shutil.rmtree(dst, ignore_errors=True)
        os.replace(tmp, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- ③ 差し替え（新しい版の exe が _update/new/blnext/ から --apply-update で起動された時）----
_MSG = {
    'ja': {'fail': '更新できませんでした。元の版のまま起動します。\n\n{err}\n\n'
                   '手動で更新する場合は、GitHub のリリースページから zip をダウンロードしてください。',
           'inUse': '{apps} が blnext のファイルを使っています。閉じてから、もう一度更新してください。',
           'inUseUnknown': 'blnext のフォルダのファイルが使われています。エクスプローラーでこのフォルダを開いていたら閉じて、もう一度更新してください。',
           'busy': 'blnext が終了しないため、更新できませんでした。\n窓をすべて閉じてから、もう一度更新してください。'},
    'en': {'fail': 'The update failed. Starting the previous version.\n\n{err}\n\n'
                   'To update manually, download the zip from the GitHub releases page.',
           'inUse': '{apps} is using files of blnext. Close it and try updating again.',
           'inUseUnknown': 'Files in the blnext folder are in use. If the folder is open in Explorer, close it and try updating again.',
           'busy': 'Could not update because blnext did not exit.\nClose all of its windows and try again.'},
}


def _msgbox(text):
    log.warning('update message: %s', text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10 | 0x40000)   # MB_ICONERROR | MB_TOPMOST
    except Exception:
        pass


def _wait_pid(pid, timeout):
    """pid のプロセスが終わるまで待つ。終わったら True"""
    if sys.platform != 'win32' or pid <= 0:
        return True
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not h:
        return True   # もう居ない
    try:
        return k.WaitForSingleObject(h, int(timeout * 1000)) == 0
    finally:
        k.CloseHandle(h)


def _retry(fn, tries=40, wait=0.25):
    """終了直後のプロセスがまだファイルを掴んでいることがあるので、少し待って何度か試す"""
    for i in range(tries):
        try:
            return fn()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _arg(argv, name, default=''):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def _files_under(target):
    """REPLACE_ITEMS の中のファイルの場所（Restart Manager はフォルダを受けない）"""
    out = []
    for n in REPLACE_ITEMS:
        p = os.path.join(target, n)
        if os.path.isfile(p):
            out.append(p)
        for root, _dirs, files in os.walk(p):
            out += [os.path.join(root, f) for f in files]
    return out


def _busy_apps(paths):
    """paths（ファイルの場所）を使っているアプリの名前の並び（Restart Manager。分からなければ []。閉じさせることはしない。
    docs/investigation.md の「使っているアプリを調べる」）"""
    if sys.platform != 'win32' or not paths:
        return []
    try:
        import ctypes
        from ctypes import wintypes as w

        class UniqueProcess(ctypes.Structure):
            _fields_ = [('dwProcessId', w.DWORD), ('ProcessStartTime', w.FILETIME)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [('Process', UniqueProcess), ('strAppName', w.WCHAR * 256), ('strServiceShortName', w.WCHAR * 64),
                        ('ApplicationType', ctypes.c_int), ('AppStatus', w.ULONG), ('TSSessionId', w.DWORD), ('bRestartable', w.BOOL)]

        rm = ctypes.WinDLL('rstrtmgr')
        session, key = w.DWORD(0), ctypes.create_unicode_buffer(33)
        if rm.RmStartSession(ctypes.byref(session), 0, key):
            return []
        try:
            arr = (w.LPCWSTR * len(paths))(*paths)
            if rm.RmRegisterResources(session, len(paths), arr, 0, None, 0, None):
                return []
            need, n, reasons = w.UINT(0), w.UINT(0), w.DWORD(0)
            r = rm.RmGetList(session, ctypes.byref(need), ctypes.byref(n), None, ctypes.byref(reasons))
            if r != 234 or not need.value:   # ERROR_MORE_DATA。0＝使っているアプリは無い
                return []
            infos, n = (ProcessInfo * need.value)(), w.UINT(need.value)
            if rm.RmGetList(session, ctypes.byref(need), ctypes.byref(n), infos, ctypes.byref(reasons)):
                return []
            return sorted({infos[i].strAppName for i in range(n.value) if infos[i].strAppName})
        finally:
            rm.RmEndSession(session)
    except Exception:
        log.exception('Restart Manager')
        return []


def _clean_previous(target, keep=None):
    """前の版の退避のうち keep のほかを消す。消せなくてもよい（外のアプリがつかんでいる間は消えない。次に起動した時にやり直す）"""
    try:
        names = [n for n in os.listdir(target) if n.startswith(PREV_PREFIX) and os.path.isdir(os.path.join(target, n))]
    except OSError:
        return
    for n in names:
        if n != keep:
            shutil.rmtree(os.path.join(target, n), ignore_errors=True)


def _backup_dir(target, old_ver):
    """今回の退避の場所。同じ名前の前の退避が消せずに残っていれば番号をずらす（残った物と混ぜない）"""
    base = bk = os.path.join(target, PREV_PREFIX + old_ver)
    n = 2
    while os.path.exists(bk):
        bk, n = '%s-%d' % (base, n), n + 1
    return bk


def apply_main(argv, src=None, launch_after=True):
    """--apply-update の本体。src＝新しい版のフォルダ（既定は自分の exe のフォルダ）。戻り値は成功したか"""
    target = os.path.abspath(_arg(argv, '--target'))
    lang = 'en' if _arg(argv, '--lang') == 'en' else 'ja'
    try:
        pid = int(_arg(argv, '--pid', '0'))
    except ValueError:
        pid = 0
    src = os.path.abspath(src or os.path.dirname(sys.executable))
    msg = _MSG[lang]
    # 自分が「target の _update/new/blnext」にいる時だけ動く（任意のフォルダを書き換えさせない）
    if _norm(src) != _norm(os.path.join(target, UPDATE_DIR, 'new', ZIP_TOP)) or not os.path.isfile(os.path.join(target, EXE_NAME)):
        _msgbox(msg['fail'].format(err='bad target'))
        return False
    new_ver = read_app_version(os.path.join(src, '_internal'))
    if not vtuple(new_ver) or not os.path.isfile(os.path.join(src, EXE_NAME)):
        _msgbox(msg['fail'].format(err='bad package'))
        return False
    if not _wait_pid(pid, 60):
        _msgbox(msg['busy'])
        return False
    old_ver = read_app_version(os.path.join(target, '_internal')) or 'old'
    _clean_previous(target)   # 退避は 1 世代だけ。消せずに残った物は次に起動した時にやり直す
    bk = _backup_dir(target, old_ver)
    moved, copied, ok, err = [], [], False, ''
    try:
        os.makedirs(bk)
        for n in REPLACE_ITEMS:
            p = os.path.join(target, n)
            if os.path.exists(p):
                _retry(lambda: os.replace(p, os.path.join(bk, n)))
                moved.append(n)
        for n in REPLACE_ITEMS:
            s, d = os.path.join(src, n), os.path.join(target, n)
            if os.path.isdir(s):
                copied.append(n)
                shutil.copytree(s, d)
            elif os.path.isfile(s):
                copied.append(n)
                shutil.copy2(s, d)
        if read_app_version(os.path.join(target, '_internal')) != new_ver or not os.path.isfile(os.path.join(target, EXE_NAME)):
            raise OSError('copy check failed')
        ok = True
    except Exception as e:
        err = str(e)[:300]
        # 元に戻す: 途中まで入れた新しい版を消し、退避した古い版を戻す
        for n in copied:
            p = os.path.join(target, n)
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass
        for n in moved:
            try:
                _retry(lambda: os.replace(os.path.join(bk, n), os.path.join(target, n)), tries=8)
            except OSError:
                pass
        try:
            os.rmdir(bk)   # 全部戻せた時だけ空になる（戻せなかった物が残っていれば退避ごと残す）
        except OSError:
            pass
        apps = _busy_apps(_files_under(target))   # 戻した後の場所で調べる
        err = (msg['inUse'].format(apps='・'.join(apps[:5])) if apps else msg['inUseUnknown']) + '\n\n' + err
    if ok:
        try:
            with open(os.path.join(target, UPDATE_DIR, 'applied.json'), 'w', encoding='utf-8') as f:
                json.dump({'from': old_ver, 'to': new_ver}, f)
        except OSError:
            pass
    else:
        _msgbox(msg['fail'].format(err=err))
    if launch_after and '--no-launch' not in argv:   # --no-launch はテスト用（差し替えだけ確かめる）
        try:
            _spawn([os.path.join(target, EXE_NAME)], target)
        except OSError:
            pass
    return ok


# ---- ④ 起動し直した後の片付け（app.py の main から呼ぶ）----
def startup(updater):
    """更新直後なら「更新しました」用に版を覚え、_update/ を消す（差し替えた exe の終了待ちで少し粘る）。
    前の差し替えで消せなかった古い退避（一番新しい退避のほか）も、ここで消し直す"""
    if not updater.frozen:
        return
    work = updater.work
    try:
        prev = [n for n in os.listdir(updater.exe_dir) if n.startswith(PREV_PREFIX)]
        keep = max(prev, key=lambda n: os.path.getmtime(os.path.join(updater.exe_dir, n))) if prev else None
    except OSError:
        keep = None
    try:
        with open(os.path.join(work, 'applied.json'), encoding='utf-8') as f:
            a = json.load(f)
        if vtuple(a.get('to')):
            updater.just_updated = {'from': str(a.get('from', ''))[:40], 'to': a['to']}
    except Exception:
        pass

    def clean():
        _clean_previous(updater.exe_dir, keep)
        for _ in range(30):
            if not os.path.exists(work):
                return
            if updater.job['state'] != 'idle':
                return   # このセッションで更新を始めた＝消さない
            shutil.rmtree(work, ignore_errors=True)
            if not os.path.exists(work):
                return
            time.sleep(1)
    threading.Thread(target=clean, name='update-clean', daemon=True).start()
