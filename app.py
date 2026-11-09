# デスクトップ版ランチャー（pywebview＋serve.py）。骨組みは NLM（Non-Linear Mapper Oz-Co2版）の app.py を流用した。
#   - 画面（web/）とコードはバンドル（読み取り専用）から配信する
#   - 書き込み先（config/ data/ logs/ exports/）は exe の隣（ポータブル。開発中はこのファイルの隣）
#   - ローカルの空きポートで serve.py を起動し、WebView2 のネイティブウィンドウで表示する
#   - 同じ書き込み先でもう動いていれば、そちらの窓を前に出して終わる（二重起動の防止。core/single_instance.py）
import json, os, sys, socket, threading

# pywebview（WinForms）が使う pythonnet のランタイムを .NET Framework（netfx）に固定する。
# pythonnet 3.x は既定で coreclr（.NET 6+）を優先するが、素の Windows 11 は .NET ランタイム未導入のことがあり、
# その場合 "Failed to resolve Python.Runtime.Loader.Initialize" で起動に失敗する（NLM の配布ユーザー報告）。
# ※必ず webview/clr のインポート前に設定すること。
os.environ.setdefault('PYTHONNET_RUNTIME', 'netfx')

APP_TITLE = 'blnext'
WINDOW_SIZE, MIN_SIZE = (1280, 860), (960, 640)   # 初めて開く時の窓の大きさと、最小の大きさ
WINDOW_FILE = 'window.json'   # config/ の中。最後に閉じた時の窓の位置・大きさ（設定画面には出さない）


def _app_root():
    # PyInstaller では _MEIPASS にバンドルが展開される。開発時はこのファイルの隣
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _unblock_dist_folder():
    # 配布zipをダウンロードすると、Windows が「インターネットからのファイル」の印（Zone.Identifier）を付け、展開後の DLL に
    # 引き継がれることがある。この状態だと .NET が DLL の読み込みを拒否し、pythonnet の初期化が
    # "Failed to resolve Python.Runtime.Loader.Initialize" で失敗する（NLM の配布ユーザー報告 2026-09-23）。
    # 同梱物（_internal = sys._MEIPASS）の中のファイルからだけ印を消す（exe の隣全体は触らない＝無関係なダウンロードの印を消さない）。
    # ※必ず webview/clr のインポート前に呼ぶこと。失敗しても起動は妨げない。
    if not getattr(sys, 'frozen', False):
        return
    base = getattr(sys, '_MEIPASS', '')
    if not base or os.path.normcase(os.path.abspath(base)) == os.path.normcase(os.path.dirname(os.path.abspath(sys.executable))):
        return
    try:
        for root, _dirs, files in os.walk(base):
            for f in files:
                try:
                    os.remove(os.path.join(root, f) + ':Zone.Identifier')
                except OSError:
                    pass
    except OSError:
        pass


def _data_root(app_root):
    # 書き込み先。exe 版は exe と同じフォルダ（_internal の外）＝利用者が中を見られる・フォルダごと持ち運べる。
    # Program Files 等の書き込めない場所に置くと保存できないが、zip 配布のポータブル運用を想定している（NLM と同じ）
    d = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else app_root
    for sub in ('config', 'data', 'logs', 'exports'):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


DEFAULT_PLAYERDATA = os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'),
                                  'AppData', 'LocalLow', 'Hyperbolic Magnetism', 'Beat Saber', 'PlayerData.dat')

DOWNLOADS = os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'), 'Downloads')

_DLG_TEXT = {
    'ja': {'playdata': 'Beat Saber のプレイ記録', 'all': 'すべてのファイル', 'playlist': 'Beat Saber のプレイリスト'},
    'en': {'playdata': 'Beat Saber play data', 'all': 'All files', 'playlist': 'Beat Saber playlist'},
}


def _make_api(settings, webview, bs_offer=None, exporter=None):
    """JS（window.pywebview.api）へ渡す窓口。フォルダ・ファイルの場所は、ここでネイティブのダイアログから選ばれた物だけが
    設定に入る（JS から場所を渡す口は作らない。出力先を JS から任意に指定させないため。docs/spec.md の「プレイリスト出力」）。
    bs_offer は serve.bs_offer（選んだフォルダの中に Beat Saber が複数あった時に候補として覚えさせる）。
    exporter は serve.export_for_file（「ファイルに保存」の中身を作る）。
    main() の外に置いて、単体テストから偽物の webview を渡して呼べるようにしてある"""
    from core.fsutil import write_atomic
    from core.output import bsfind

    class Api:
        def _lang(self):
            return 'en' if settings.get('lang') == 'en' else 'ja'

        def pick_beat_saber_dir(self):
            # Beat Saber の中のどの階層を選んでも、Beat Saber.exe のある所にする（bsfind.resolve）
            cur = settings.get('beat_saber_dir')
            kw = {'directory': cur} if cur and os.path.isdir(cur) else {}
            paths = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER, **kw)
            if not paths:
                return {'ok': False, 'error': 'cancelled'}
            picked = os.path.abspath(paths[0])
            roots = bsfind.resolve(picked)
            if not roots:
                return {'ok': False, 'error': 'not-beat-saber', 'path': picked}
            if len(roots) > 1 and bs_offer is not None:   # 例: BSInstances（版ごとのフォルダ）を選んだ
                return {'ok': False, 'error': 'choose', 'candidates': bs_offer(roots, 'picked')}
            settings.update({'beat_saber_dir': roots[0]}, trusted=True)
            return {'ok': True, 'path': roots[0], 'picked': picked}

        def pick_playerdata(self):
            cur = settings.get('playerdata_path') or DEFAULT_PLAYERDATA
            d = os.path.dirname(cur)
            kw = {'directory': d} if os.path.isdir(d) else {}
            tx = _DLG_TEXT[self._lang()]
            paths = webview.windows[0].create_file_dialog(
                webview.FileDialog.OPEN, file_types=(tx['playdata'] + ' (PlayerData.dat)', tx['all'] + ' (*.*)'), **kw)
            if not paths:
                return {'ok': False, 'error': 'cancelled'}
            p = os.path.abspath(paths[0])
            settings.update({'playerdata_path': p}, trusted=True)
            return {'ok': True, 'path': p}

        def save_playlist(self, preset=None):
            """プレイリストをファイルに保存する（「名前を付けて保存」で選んだ場所へ。JS から受けるのはセットの id だけ）"""
            if exporter is None or (preset is not None and not isinstance(preset, str)):
                return {'ok': False, 'error': 'bad-request'}
            try:
                text, fname, done = exporter(preset)
            except Exception as e:   # serve.ApiError（出す曲が無い等）
                return {'ok': False, 'error': getattr(e, 'code', 'internal')}
            tx = _DLG_TEXT[self._lang()]
            kw = {'directory': DOWNLOADS} if os.path.isdir(DOWNLOADS) else {}
            paths = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE, save_filename=fname, file_types=(tx['playlist'] + ' (*.bplist)', tx['all'] + ' (*.*)'), **kw)
            if not paths:
                return {'ok': False, 'error': 'cancelled'}
            p = os.path.abspath(paths if isinstance(paths, str) else paths[0])
            if not os.path.splitext(p)[1]:
                p += '.bplist'
            try:
                write_atomic(p, text)
            except OSError:
                return {'ok': False, 'error': 'write-failed', 'path': p}
            done(p)
            return {'ok': True, 'path': p}

        def quit_app(self):
            """アプリを閉じる（更新の差し替え役を起動した後に画面が呼ぶ。core/app_update.py）。別のスレッドから閉じる（画面の呼び出しを待たせない）"""
            threading.Thread(target=lambda: webview.windows[0].destroy(), daemon=True).start()
    return Api()


# ---- 窓の位置・大きさ（閉じた時の状態で次も開く） ----
# 値は pywebview の create_window と同じ「論理の値」（DPI の倍率で割った値。pywebview が倍率を掛けて窓を作る）

def _load_window(config_dir, screens):
    """create_window に渡す width・height（と x・y・maximized）。前回の記録が無い・壊れていれば初めての大きさ。
    位置は、今つながっている画面のどれかにタイトルバーが見える時だけ（外したモニターの上に開いて見えなくならないように）"""
    out = {'width': WINDOW_SIZE[0], 'height': WINDOW_SIZE[1]}
    try:
        with open(os.path.join(config_dir, WINDOW_FILE), encoding='utf-8') as f:
            w = json.load(f)
        width, height, x, y = (int(w[k]) for k in ('width', 'height', 'x', 'y'))
    except (OSError, ValueError, KeyError, TypeError):
        return out
    out.update(width=max(width, MIN_SIZE[0]), height=max(height, MIN_SIZE[1]), maximized=w.get('maximized') is True)
    if any(_title_bar_visible(x, y, out['width'], s) for s in screens):
        out.update(x=x, y=y)
    return out


def _title_bar_visible(x, y, width, screen):
    """窓のタイトルバー（上の 30）が画面 screen（x・y・width・height）に、つかめるくらい（横 100・縦 20）見えるか"""
    w = min(x + width, screen.x + screen.width) - max(x, screen.x)
    h = min(y + 30, screen.y + screen.height) - max(y, screen.y)
    return w >= 100 and h >= 20


def _window_geometry(form, dpi=None):
    """閉じる時の窓の位置・大きさ（form は WinForms の Form＝window.native。UI スレッドから呼ぶ）。最大化・最小化して
    いる時は、元に戻した時の位置・大きさ（RestoreBounds）。dpi はテスト用の差し替え（省略時はその窓の DPI）"""
    if dpi is None:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(form.Handle.ToInt32())   # pywebview と同じ求め方
    scale = (dpi or 96) / 96
    state = str(form.WindowState)
    b = form.Bounds if state.endswith('Normal') else form.RestoreBounds
    return {'x': round(b.X / scale), 'y': round(b.Y / scale), 'width': round(b.Width / scale),
            'height': round(b.Height / scale), 'maximized': state.endswith('Maximized')}


def _save_window(config_dir, geometry):
    path = os.path.join(config_dir, WINDOW_FILE)
    os.makedirs(config_dir, exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(geometry, f)
    os.replace(path + '.tmp', path)


def _stop_server(httpd):
    """サーバーを止めて、待ち受けのソケットも閉じる（止めるのに失敗しても閉じる）"""
    for stop in (httpd.shutdown, httpd.server_close):
        try:
            stop()
        except Exception:
            pass


def selftest(out_path):
    """ビルドした exe の動作確認（tools/build_and_deploy.py が配る前に呼ぶ）。窓は出さない。
    同梱物から serve.py を読み込んでサーバーを立て、/__api/status と画面の入口が返るかを確かめて、結果を out_path に JSON で書く。
    書き込み先は一時フォルダ（利用者のデータに触らない）"""
    import json, tempfile, urllib.request
    res = {'ok': False}
    try:
        app_root = _app_root()
        os.environ['BLN_BASE_DIR'] = app_root
        os.environ['BLN_DATA_DIR'] = tempfile.mkdtemp(prefix='bln-selftest-')
        os.environ['BLN_NO_NET'] = '1'
        sys.path.insert(0, app_root)
        import serve, ssl, sqlite3, numpy   # noqa: F401（同梱し忘れを見つける）
        httpd = serve.make_server(port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            req = urllib.request.Request('http://127.0.0.1:%d/__api/status' % port, data=b'{}', method='POST',
                                         headers={'X-BLN-Request': '1', 'X-BLN-Token': serve.SESSION_TOKEN})
            with urllib.request.urlopen(req, timeout=10) as r:
                st = json.loads(r.read())
            with urllib.request.urlopen('http://127.0.0.1:%d/index.html' % port, timeout=10) as r:
                page = r.read()
        finally:
            _stop_server(httpd)
        res = {'ok': bool(st.get('ok')) and b'js/main.js' in page, 'version': st.get('version'), 'ssl': ssl.OPENSSL_VERSION}
    except Exception as e:
        res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)


def main():
    if '--selftest' in sys.argv:
        i = sys.argv.index('--selftest')
        return selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else 'selftest.json')
    # 自動更新の差し替え役として起動された（旧版が _update/new/ に展開したこの exe を起動した）時は、差し替えて
    # 配布フォルダの exe を起動し直すだけで終わる（画面・サーバーは出さない。書き込み先のフォルダも作らない）
    if '--apply-update' in sys.argv:
        from core import app_update
        app_update.apply_main(sys.argv)
        return
    app_root = _app_root()
    data_root = _data_root(app_root)
    # 二重起動の防止: 同じ書き込み先の blnext がもう動いていたら、その窓を前に出して終わる（サーバーも窓も作らない）。
    # 印は閉じずにプロセスが終わるまで持つ（OS が放す）。--selftest・--apply-update は上で終わるので調べない（core/single_instance.py）
    from core import single_instance
    instance = single_instance.claim(data_root)
    if instance is None:
        return
    _unblock_dist_folder()   # webview（→pythonnet/clr）のインポート前に済ませる
    # serve.py がインポート時に読む環境変数を先に設定する
    os.environ['BLN_BASE_DIR'] = app_root
    os.environ['BLN_DATA_DIR'] = data_root

    sys.path.insert(0, app_root)
    import serve
    from core import app_update
    app_update.startup(serve.UPDATER)   # 更新の直後なら「更新しました」を出す準備と、_update/・古い退避の片付け

    port = _free_port()
    httpd = serve.make_server(port=port, bind='127.0.0.1')
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    import webview
    api = _make_api(serve.SETTINGS, webview, serve.bs_offer, serve.export_for_file)
    config_dir = os.path.join(data_root, 'config')
    try:
        screens = list(webview.screens)
    except Exception:
        screens = []   # 分からなければ位置は覚えていても使わない（真ん中に開く）
    win = webview.create_window(
        (APP_TITLE + ' ' + serve.APP_VERSION).strip(),
        serve.access_url(port),   # 合言葉（#k=…）付き。serve.py の SESSION_TOKEN の説明
        min_size=MIN_SIZE, js_api=api,
        **_load_window(config_dir, screens),   # 前回閉じた時の位置・大きさ・最大化
    )

    def _on_closing():
        # UI スレッドで同期に呼ばれる＝ここで画面（JS）の応答を待たないこと（デッドロックする）。
        # 取得の途中なら止めるよう頼むだけ（取得済みの分は保存済み。docs/spec.md の「アクセス量の約束」）
        try:
            serve.on_closing()
        except Exception:
            pass
        try:
            _save_window(config_dir, _window_geometry(win.native))   # 次に開く時の位置・大きさ
        except Exception:
            pass
        return True
    win.events.closing += _on_closing
    try:
        webview.start()   # WebView2（Edge Chromium）。メインスレッドをブロックする
    finally:
        _stop_server(httpd)


if __name__ == '__main__':
    main()
