"""ヘッドレスの Edge で画面を開いて確かめる道具（Node・追加ライブラリ不要）。画面のテスト（tests/e2e）と、手で画面を確かめる時に使う。

Chrome DevTools Protocol（CDP）を、起動した Edge とのパイプ（--remote-debugging-pipe）越しに話す。TCP のデバッグポートは開かない
（認証が無く、同じPCの別のユーザーからも繋いでブラウザを操作できてしまうため）。パイプの部分は NLM の tools/cdp.py の流用。
書き込み先は一時フォルダで、データは tests/_lib/fixture_data.py の作り物（ネットに出ない）。手元の config/ data/ は触らない。

■ コマンドラインから（結果は短い文字だけ。画像を見なくても崩れ・エラーが分かる）
  python tools/cdp.py check                          全画面 × 幅 960/1280/1600 の崩れ・JSのエラー・null 等の表示を調べる
  python tools/cdp.py shot out.png --screen recommend --width 1280   撮る（崩れ・エラーも表示）
  python tools/cdp.py eval "document.title" --screen home            JS の結果を表示
  オプション: --data <データフォルダ>（作り物でなく本物のデータで見る。中は書き換わることがある）/ --height / --js "撮る前に実行するJS"

■ Python から（テストを書くとき）
  import sys; sys.path.insert(0, 'tools'); from cdp import App
  with App(width=1280) as app:
      app.open('recommend')
      app.click_sel('.recRow.head .th', text='推定精度')
      print(app.js("document.querySelectorAll('.recRow').length"), app.errors(), app.layout_issues())
"""
import base64, collections, json, os, shutil, subprocess, sys, tempfile, threading, time, zlib
from pathlib import Path
from urllib.parse import urlsplit

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
SCREENS = ("home", "recommend", "similar", "settings", "guide")
WIDTHS = (960, 1280, 1600)


class CdpError(RuntimeError):
    pass


# ---- Edge とのパイプ（NLM の tools/cdp.py から流用） ----
class _Pipe:
    """Edge との CDP のパイプ（--remote-debugging-pipe）。メッセージは JSON を \\0 で区切って送り合う。
    1本のパイプをブラウザ自身（sessionId 無し）と各タブのセッション（Target.attachToTarget の flatten）で共有するので、
    受信は専用のスレッドで読み、sessionId ごとの箱に振り分ける"""
    TIMEOUT = 60   # 何も届かないまま待つ上限（秒）。以前の WebSocket の受信待ち（ソケットのタイムアウト）と同じ

    def __init__(self, rfd, wfd):
        self.wfd = wfd
        # ブラウザ自身へ送る命令の番号。タブ側の番号（Editor._id / Tab._id）と重ならない範囲にする
        # （閉じたばかりのタブへ送った命令のエラーは sessionId 無し＝ブラウザ宛てとして返ってくるため）
        self._bid = 1 << 30
        self._cv = threading.Condition()
        self._wlock = threading.Lock()
        self._boxes = {}      # sessionId（ブラウザ自身は None）→ 届いたメッセージの列
        self._gone = set()    # 切り離された（タブが閉じた・こちらで閉じた）セッション
        self._eof = False     # パイプが閉じた＝Edge が終了した
        threading.Thread(target=self._read_loop, args=(rfd,), daemon=True).start()

    def _read_loop(self, rfd):
        buf = bytearray()
        try:
            while True:
                chunk = os.read(rfd, 1 << 20)
                if not chunk:
                    break
                start = len(buf)
                buf += chunk
                i = buf.find(b"\0", start)
                while i >= 0:
                    self._dispatch(json.loads(buf[:i]))
                    del buf[:i + 1]
                    i = buf.find(b"\0")
        except Exception:
            pass
        finally:
            with self._cv:
                self._eof = True
                self._cv.notify_all()
            # 読む側はこのスレッドが閉じる（Windows では読んでいる最中の fd を別のスレッドから閉じると、読み終わるまで止まる）
            try:
                os.close(rfd)
            except OSError:
                pass

    def _dispatch(self, msg):
        sid = msg.pop("sessionId", None)   # 受け取る側には以前の WebSocket と同じ形（sessionId 無し）で渡す
        with self._cv:
            if sid is None and "id" not in msg:
                # ブラウザ自身へのイベント。使うのはタブが閉じた知らせだけ（以前はタブの WebSocket が切れて
                # すぐ「接続が切れました」になった。知らせを捨てると、閉じたタブへの命令が応答を待って60秒止まる）
                if msg.get("method") != "Target.detachedFromTarget":
                    return
                self._gone.add(msg.get("params", {}).get("sessionId"))
            elif sid not in self._gone:
                self._boxes.setdefault(sid, collections.deque()).append(msg)
            self._cv.notify_all()

    def send(self, sid, obj):
        if sid is not None:
            obj = {**obj, "sessionId": sid}
        data = memoryview(json.dumps(obj).encode() + b"\0")
        with self._cv:
            if self._eof or sid in self._gone:
                raise CdpError("ブラウザとの接続が切れました")
        with self._wlock:
            try:
                if self.wfd is None:
                    raise OSError("closed")
                while data:
                    data = data[os.write(self.wfd, data):]
            except OSError:
                raise CdpError("ブラウザとの接続が切れました") from None

    def recv(self, sid, timeout=None):
        """sid 宛ての次のメッセージ。セッションが切り離された・Edge が終了した時は（届いていた分を渡し終えた後）CdpError"""
        end = time.monotonic() + (self.TIMEOUT if timeout is None else timeout)
        with self._cv:
            while True:
                box = self._boxes.get(sid)
                if box:
                    return box.popleft()
                if self._eof or sid in self._gone:
                    raise CdpError("ブラウザとの接続が切れました")
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError("timed out")   # 以前（ソケットの受信待ち）と同じ例外
                self._cv.wait(left)

    def call(self, method, timeout=None, **params):
        """ブラウザ自身へ命令を送り、結果を返す（タブ宛ての命令は Editor.call / Tab.call）"""
        self._bid += 1
        my = self._bid
        self.send(None, {"id": my, "method": method, "params": params})
        while True:
            msg = self.recv(None, timeout)
            if msg.get("id") == my:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def attach(self, target_id):
        """タブにセッションを張って返す"""
        return _PipeSession(self, self.call("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"])

    def forget(self, sid):
        with self._cv:
            self._gone.add(sid)
            self._boxes.pop(sid, None)
            self._cv.notify_all()

    def close(self):
        """書く側を閉じる（読む側は、Edge が終了してパイプが閉じた時に読み取りのスレッドが閉じる）"""
        with self._wlock:
            if self.wfd is not None:
                try:
                    os.close(self.wfd)
                except OSError:
                    pass
                self.wfd = None


class _PipeSession:
    """パイプ越しの1つのタブとのセッション。以前の WebSocket と同じ send / recv / close を持つ"""

    def __init__(self, pipe, sid):
        self.pipe, self.sid = pipe, sid

    def send(self, obj):
        self.pipe.send(self.sid, obj)

    def recv(self):
        return self.pipe.recv(self.sid)

    def close(self):
        self.pipe.forget(self.sid)


def _launch_with_pipe(args):
    """Edge（args[0]）を CDP のパイプ付きで起動し、(プロセス, _Pipe) を返す。
    Windows は渡すハンドルの番号を --remote-debugging-io-pipes で伝え（子にはその2つだけを継承させる）、
    それ以外は Chromium の決まりどおり fd 3（ブラウザが読む）/ fd 4（ブラウザが書く）に付け替えて渡す"""
    to_r, to_w = os.pipe()       # こちら → Edge
    from_r, from_w = os.pipe()   # Edge → こちら
    try:
        if sys.platform == "win32":
            import msvcrt
            hr, hw = msvcrt.get_osfhandle(to_r), msvcrt.get_osfhandle(from_w)
            os.set_handle_inheritable(hr, True)
            os.set_handle_inheritable(hw, True)
            si = subprocess.STARTUPINFO(lpAttributeList={"handle_list": [hr, hw]})
            proc = subprocess.Popen([args[0], f"--remote-debugging-io-pipes={hr},{hw}", *args[1:]], startupinfo=si,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["/bin/sh", "-c", 'exec "$@" 3<&0 4>&1 </dev/null >/dev/null', "sh", *args],
                                    stdin=to_r, stdout=from_w, stderr=subprocess.DEVNULL)
    except BaseException:
        os.close(to_w)
        os.close(from_r)
        raise
    finally:
        os.close(to_r)     # 子へ渡した側はこちらでは閉じる（Edge が終了した時に読む側が終わりを受け取れるように）
        os.close(from_w)
    return proc, _Pipe(from_r, to_w)


# 崩れの検出（画像を使わない）。見えている要素だけを調べ、[{kind, where, text}] を返す
#  - page   : ページが横にはみ出している（横スクロールが出る）
#  - out    : カード等（.card .tile .modal .bsItem .recRow .result）の中の要素が、カードの左右の外へはみ出している
#  - clip   : overflow で隠れて文字が切れている（text-overflow:ellipsis の「…」は意図した省略なので除く）
#  - overlap: ボタン・入力欄どうしが重なっている
#  - text   : 画面に null / undefined / NaN / [object Object] の文字が出ている
LAYOUT_JS = r"""(()=>{
const out=[], vis=e=>{ const r=e.getBoundingClientRect(), s=getComputedStyle(e);
  return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&+s.opacity!==0; };
const name=e=>e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\s+/).join('.'):'');
const txt=e=>(e.innerText||e.value||'').trim().replace(/\s+/g,' ').slice(0,40);
const add=(kind,e,extra)=>out.push({kind,where:name(e),text:(extra||txt(e))});
const W=document.documentElement.clientWidth;
if(document.documentElement.scrollWidth>W+1) out.push({kind:'page',where:'html',text:'scrollWidth '+document.documentElement.scrollWidth+' > '+W});
const roots=[...document.querySelectorAll('#topbar,#strip,#main,.modal')].filter(vis);
// 本文・小さな画面そのものが横にはみ出す（横のスクロールバーが出る）
for(const root of roots) if(root.scrollWidth>root.clientWidth+1) add('page',root,'scrollWidth '+root.scrollWidth+' > '+root.clientWidth);
// 枠（カード等）からのはみ出し。枠の中で横にスクロールする所・切って隠す所（「…」で切る文字など）に入っている物は除く
// （見えている分はその親の中にあり、親のほうを確かめる）。#main は縦のスクロールのために overflow:auto なので、枠より外の親は
// 見ない（2026-10-16 まで #main を見てしまい、本文の中のはみ出しを全部見逃していた）
const scrolls=(e,box)=>{ for(let p=e.parentElement;p&&p!==box;p=p.parentElement){ const o=getComputedStyle(p).overflowX; if(o!=='visible') return true; } return false; };
for(const box of document.querySelectorAll('.card,.tile,.modal,.bsItem,.recRow,.result')){
  if(!vis(box)) continue; const b=box.getBoundingClientRect();
  for(const e of box.querySelectorAll('*')){
    if(!vis(e)||e.closest('svg')&&e.tagName.toLowerCase()!=='svg') continue;
    const r=e.getBoundingClientRect();
    if((r.right>b.right+1||r.left<b.left-1)&&!scrolls(e,box)&&e.closest('.card,.tile,.modal,.bsItem,.recRow,.result')===box){ add('out',e); }
  }
}
for(const root of roots) for(const e of root.querySelectorAll('*')){
  if(!vis(e)||!e.childNodes.length) continue;
  const s=getComputedStyle(e);
  if((s.overflowX==='hidden'||s.overflow==='hidden')&&s.textOverflow!=='ellipsis'&&e.scrollWidth>e.clientWidth+1&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())) add('clip',e);
}
// 重なりは同じ区切り（上の帯・本文・小さな画面）の中だけで、スクロールの外に隠れていない部品どうしを比べる
const shown=e=>{ const r=e.getBoundingClientRect(); for(let p=e.parentElement;p;p=p.parentElement){ const o=getComputedStyle(p).overflowY;
  if(o==='auto'||o==='scroll'||o==='hidden'){ const q=p.getBoundingClientRect(); return r.bottom>q.top+1&&r.top<q.bottom-1; } } return true; };
for(const root of roots){
const ctl=[...root.querySelectorAll('button,input,select,textarea')].filter(e=>vis(e)&&shown(e)&&!(root.matches('#main')&&e.closest('.modal')));
for(let i=0;i<ctl.length;i++){ const a=ctl[i].getBoundingClientRect();
  for(let j=i+1;j<ctl.length;j++){ const b=ctl[j].getBoundingClientRect();
    const w=Math.min(a.right,b.right)-Math.max(a.left,b.left), h=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);
    if(w>2&&h>2&&!ctl[i].contains(ctl[j])&&!ctl[j].contains(ctl[i])) add('overlap',ctl[i],txt(ctl[i])+' × '+name(ctl[j])+' '+txt(ctl[j])); } } }
const tw=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
for(let n;(n=tw.nextNode());){ if(/(^|[^A-Za-z])(null|undefined|NaN|\[object Object\])([^A-Za-z]|$)/.test(n.textContent)&&n.parentElement&&vis(n.parentElement)&&!n.parentElement.closest('script,style,code'))
  add('text',n.parentElement,n.textContent.trim().slice(0,60)); }
return out;})()"""


def _cov_paint(functions, n):
    """V8 の詳しいカバレッジ（関数ごとの範囲と回数）から、文字ごとに「動いた」を 1 にした bytearray"""
    arr = bytearray(n)
    for f in functions:
        for r in f.get("ranges", []):
            s, e = max(0, r["startOffset"]), min(n, r["endOffset"])
            arr[s:e] = (b"\x01" if r["count"] > 0 else b"\x00") * (e - s)
    return arr


class App:
    """serve.py＋ヘッドレスの Edge でアプリの画面を開いて操作する。with 文で使う（終了時に後始末）"""

    def __init__(self, width=1280, height=900, data_dir=None, js_coverage=False):
        self.width, self.height, self.given_data = width, height, data_dir
        self.js_coverage = js_coverage
        self.cov = {}   # url のパス → bytearray（動いた文字）
        self._id, self._nav, self._events = 0, 0, []
        self.edge = self.httpd = self.ws = self.pipe = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        edge = next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)
        if not edge:
            raise CdpError("Microsoft Edge が見つかりません")
        if self.given_data:
            self.data_dir = str(self.given_data)
        else:
            self.data_dir = tempfile.mkdtemp(prefix="bln_cdp_data_")
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(ROOT / "tests" / "_lib"))
            import fixture_data
            self.info = fixture_data.build(self.data_dir)
        os.environ["BLN_DATA_DIR"] = self.data_dir
        os.environ["BLN_NO_NET"] = "1"
        sys.path.insert(0, str(ROOT))
        sys.modules.pop("serve", None)   # serve は import した時に BLN_DATA_DIR を読む
        import serve
        self.serve = serve
        self.token = serve.SESSION_TOKEN
        self.httpd = serve.make_server(port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.profile = tempfile.mkdtemp(prefix="bln_cdp_edge_")
        self.edge, self.pipe = _launch_with_pipe([
            edge, "--headless=new", f"--user-data-dir={self.profile}", "--remote-debugging-pipe",
            f"--window-size={self.width},{self.height}", "--hide-scrollbars", "--mute-audio",
            "--disable-renderer-backgrounding", "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            # 新しいプロファイルだと同期の確認・自動のサインインが開き、ページとの接続が切れることがある（NLM で経験済み）
            "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions",
            "--disable-component-extensions-with-background-pages", "--disable-default-apps",
            "--disable-features=msEdgeSyncConsent,msImplicitSignin,msSignInPromo,EdgeSignIn,Translate",
            "about:blank",
        ])
        page, end = None, time.time() + 20
        while not page and time.time() < end:
            try:
                tabs = self.pipe.call("Target.getTargets", timeout=max(0.1, end - time.time()))["targetInfos"]
            except (CdpError, TimeoutError):
                break
            page = next((t for t in tabs if t.get("type") == "page"), None)
            if not page:
                time.sleep(0.2)
        if not page:
            raise CdpError("Edge のデバッグ接続に失敗しました")
        self.ws = self.pipe.attach(page["targetId"])
        for m in ("Runtime.enable", "Page.enable", "Log.enable"):
            self.call(m)
        self.resize(self.width, self.height)
        if self.js_coverage:
            self.call("Profiler.enable")
            self.call("Profiler.startPreciseCoverage", callCount=True, detailed=True)

    def close(self):
        if self.js_coverage and self.ws:
            try:
                self.take_coverage()
            except Exception:
                pass
        if self.ws:
            self.ws.close()
        if self.pipe:
            self.pipe.close()
        if self.edge:
            self.edge.kill()
            try:
                self.edge.wait(5)
            except Exception:
                pass
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        try:
            self.serve.STORE.close()   # DB を閉じてから一時フォルダを消す
        except Exception:
            pass
        for d in (getattr(self, "profile", None), None if self.given_data else getattr(self, "data_dir", None)):
            if d:
                shutil.rmtree(d, ignore_errors=True)

    # ---- CDP の基本 ----
    def call(self, method, **params):
        self._id += 1
        my = self._id
        self.ws.send({"id": my, "method": method, "params": params})
        while True:
            msg = self.ws.recv()
            if msg.get("id") == my:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self._events.append(msg)
                if msg["method"] == "Page.javascriptDialogOpening":   # 開いたままだとページが止まる
                    self._id += 1
                    self.ws.send({"id": self._id, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}})

    def js(self, expr, await_promise=True):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise CdpError("JS例外: " + (d.get("exception", {}).get("description") or d.get("text", "")))
        return r.get("result", {}).get("value")

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.js("0")
            time.sleep(min(0.1, max(0, end - time.time())))

    def errors(self):
        """ページ内の例外・console.error（文字のリスト）"""
        out = []
        for e in self._events:
            m, p = e.get("method"), e.get("params", {})
            if m == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails", {})
                out.append("例外: " + (d.get("exception", {}).get("description") or d.get("text", "")))
            elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
                out.append("console.error: " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
                en = p["entry"]
                out.append(f"ログ: {en.get('text', '')} {en.get('url', '')}".rstrip())
        return out

    # ---- 画面 ----
    def resize(self, width, height=None):
        self.width, self.height = width, height or self.height
        self.call("Emulation.setDeviceMetricsOverride", width=self.width, height=self.height, deviceScaleFactor=1, mobile=False)

    def url(self, screen="home"):
        # 合言葉は #k=。# 付きの同じ URL へ移ると読み込み直さないので、呼ぶたびに n を変える
        self._nav += 1
        return f"http://127.0.0.1:{self.port}/index.html?n={self._nav}#k={self.token}&s={screen}"

    def open(self, screen="home"):
        """画面を開き直して、描き終わるまで待つ"""
        if self.js_coverage:
            self.take_coverage()
        self.call("Page.navigate", url=self.url(screen))
        self.wait_ready()

    def wait_ready(self, timeout=20.0):
        """描き終わるまで待つ（main.js の show() が body に data-screen を付け、data-busy を外す）"""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js("document.readyState==='complete'&&!!document.body.dataset.screen&&!document.body.dataset.busy"
                           "&&document.getElementById('bootCover').classList.contains('gone')"):
                    self.wait(0.1)
                    return
            except CdpError:
                pass   # 読み込みの途中で実行の文脈が入れ替わった
            time.sleep(0.05)
        raise CdpError(f"画面の描画が {timeout} 秒以内に終わりませんでした\n" + "\n".join(self.errors()[:10]))

    def until(self, expr, timeout=10.0, what=""):
        """JS の式が真になるまで待つ"""
        end = time.time() + timeout
        while time.time() < end:
            v = self.js(expr)
            if v:
                return v
            time.sleep(0.05)
        raise CdpError(f"待っても条件を満たしませんでした: {what or expr}")

    def layout_issues(self):
        return self.js(LAYOUT_JS) or []

    def shot(self, path, full=False):
        params = {"format": "png"}
        if full:   # ページ全体（縦に長い画面）
            h = self.js("Math.max(document.getElementById('main').scrollHeight+60, innerHeight)")
            params["clip"] = {"x": 0, "y": 0, "width": self.width, "height": h, "scale": 1}
            params["captureBeyondViewport"] = True
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(self.call("Page.captureScreenshot", **params)["data"]))
        return str(path)

    # ---- 入力（ブラウザの本物の入力として届く） ----
    def click(self, x, y):
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", buttons=0, clickCount=1)

    def find(self, selector, text=None, index=0):
        """要素を画面に入れて、真ん中の座標 {x, y} を返す（text は表示の文字の一部）"""
        r = self.js("(()=>{const els=[...document.querySelectorAll(%s)].filter(e=>%s===null||(e.innerText||e.value||'').includes(%s));"
                    "const e=els[%d]; if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect();"
                    "return {x:r.left+r.width/2, y:r.top+r.height/2};})()"
                    % (json.dumps(selector), json.dumps(text), json.dumps(text or ""), index))
        if not r:
            raise CdpError(f"見つかりません: {selector} {text or ''}")
        return r

    def click_sel(self, selector, text=None, index=0, settle=0.15):
        p = self.find(selector, text, index)
        self.click(p["x"], p["y"])
        self.wait(settle)

    _KEYS = {"Enter": ("Enter", 13), "Escape": ("Escape", 27), "Tab": ("Tab", 9), " ": ("Space", 32), "Backspace": ("Backspace", 8),
             "Delete": ("Delete", 46), "ArrowUp": ("ArrowUp", 38), "ArrowDown": ("ArrowDown", 40),
             "ArrowLeft": ("ArrowLeft", 37), "ArrowRight": ("ArrowRight", 39)}

    def key(self, key):
        code, vk = self._KEYS.get(key, ("Key" + key.upper(), ord(key.upper())) if len(key) == 1 else (key, 0))
        text = key if len(key) == 1 else ""
        base = dict(key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        self.call("Input.dispatchKeyEvent", type="keyDown" if text else "rawKeyDown", text=text, **base)
        self.call("Input.dispatchKeyEvent", type="keyUp", **base)
        self.wait(0.1)

    # ---- JS のカバレッジ ----
    def take_coverage(self):
        """今のページで動いた範囲を足し込む（ページを開き直すと前のページの分は取れなくなるので、開き直す前に呼ぶ）"""
        res = self.call("Profiler.takePreciseCoverage").get("result", [])
        for sc in res:
            u = urlsplit(sc.get("url", ""))
            if u.hostname != "127.0.0.1" or not u.path.startswith("/js/"):
                continue
            f = ROOT / "web" / u.path.lstrip("/")
            try:
                n = len(f.read_text(encoding="utf-8"))
            except OSError:
                continue
            arr = _cov_paint(sc.get("functions", []), n)
            old = self.cov.get(u.path)
            self.cov[u.path] = bytes(a | b for a, b in zip(old, arr)) if old and len(old) == n else bytes(arr)

    def save_coverage(self, path):
        data = {k: base64.b64encode(zlib.compress(v)).decode() for k, v in self.cov.items()}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")


def js_coverage_report(paths, out_txt):
    """保存した JS のカバレッジ（複数可）をまとめて、ファイルごとの割合と、動いていない長い所（5行以上）を書く。要約の1行を返す"""
    merged = {}
    for p in paths:
        for k, v in json.loads(Path(p).read_text(encoding="utf-8")).items():
            b = zlib.decompress(base64.b64decode(v))
            merged[k] = bytes(x | y for x, y in zip(merged[k], b)) if k in merged and len(merged[k]) == len(b) else b
    lines, tot_c, tot_n = [], 0, 0
    for k in sorted(merged):
        text = (ROOT / "web" / k.lstrip("/")).read_text(encoding="utf-8")
        b = merged[k]
        cov = [i for i, ch in enumerate(text) if not ch.isspace()]
        c = sum(b[i] for i in cov)
        tot_c, tot_n = tot_c + c, tot_n + len(cov)
        lines.append(f"{k:<34} {100 * c / max(1, len(cov)):5.1f}%")
        # 行ごと: 空白以外の文字が1つも動いていない行が5行以上続く所
        pos, run = 0, []
        for no, ln in enumerate(text.split("\n"), 1):
            idx = [pos + i for i, ch in enumerate(ln) if not ch.isspace()]
            dead = bool(idx) and not any(b[i] for i in idx if i < len(b))
            if dead:
                run.append(no)
            elif idx:
                if len(run) >= 5:
                    lines.append(f"    動いていない: {run[0]}〜{run[-1]}行目")
                run = []
            pos += len(ln) + 1
        if len(run) >= 5:
            lines.append(f"    動いていない: {run[0]}〜{run[-1]}行目")
    pct = 100 * tot_c / max(1, tot_n)
    Path(out_txt).parent.mkdir(parents=True, exist_ok=True)
    Path(out_txt).write_text(f"JS のカバレッジ（文字の割合）: {pct:.1f}%\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return f"JS {pct:.1f}%"


def _fmt_issues(issues):
    return [f"  [{i['kind']}] {i['where']}  {i['text']}" for i in issues]


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "shot", "eval"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--screen", default="home")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--data")
    ap.add_argument("--js")
    a = ap.parse_args(argv)
    with App(width=a.width, height=a.height, data_dir=a.data) as app:
        if a.cmd == "check":
            bad = 0
            for w in WIDTHS:
                app.resize(w)
                for s in SCREENS:
                    app.open(s)
                    iss, err = app.layout_issues(), app.errors()
                    app._events.clear()
                    if iss or err:
                        bad += 1
                        print(f"{s} @{w}:", *(_fmt_issues(iss) + ["  " + e for e in err]), sep="\n")
            print("崩れ・エラーなし" if not bad else f"{bad} 画面で問題あり")
            return 1 if bad else 0
        app.open(a.screen)
        if a.js:
            app.js(a.js)
            app.wait(0.3)
        if a.cmd == "eval":
            print(json.dumps(app.js(a.arg), ensure_ascii=False))
        else:
            print(app.shot(a.arg or "shot.png", full=True))
        for ln in _fmt_issues(app.layout_issues()) + ["  " + e for e in app.errors()]:
            print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
