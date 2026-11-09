# ワンクリックでプレイリストを入れる（BeatSaver などのサイトの「One-Click」ボタンと同じ仕組み）。
#  - リンク bsplaylist://playlist/<プレイリストの URL> を開くと、Windows に登録されたアプリ（BSManager・ModAssistant）が起動し、
#    その URL から .bplist を読んで Playlists に入れ、足りない曲もダウンロードする
#  - BSManager（v1.6.0 のソースで確認）: 入っている全部の版の Playlists に入れる。同じ customData.syncURL のプレイリストが
#    あれば上書き、無ければ Content-Disposition のファイル名（重なれば番号を付けた別名）で作る（bplist.py の syncURL の説明）
#  - URL はこのアプリのローカルサーバーが一時的に配る（serve.py の /oneclick/<合言葉>/<ファイル名>）。合言葉は推測できない値で、
#    TTL 秒で消える。アプリのポートは起動ごとに変わるので、ボタンを押す時にアプリが開いていればよい
import os, re, secrets, threading, time

SCHEME = 'bsplaylist'
TTL = 600   # 配る時間（秒）。アプリが起動して読みに来るまで


def handler_app(winreg=None):
    """bsplaylist:// を受けるアプリの名前（'BSManager' / 'ModAssistant' / exe の名前）。登録が無ければ None。
    winreg はテスト用の差し替え（省略時は本物のレジストリ）"""
    if winreg is None:
        if os.name != 'nt':
            return None
        import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, SCHEME + r'\shell\open\command') as k:
            cmd = winreg.QueryValueEx(k, '')[0]
    except OSError:
        return None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    low = cmd.lower()
    if 'bsmanager' in low or 'bs-manager' in low:
        return 'BSManager'
    if 'modassistant' in low:
        return 'ModAssistant'
    m = re.search(r'([^\\/"]+)\.exe', cmd, re.I)
    return m.group(1) if m else 'other'


def link(port, token, filename):
    return '%s://playlist/http://127.0.0.1:%d/oneclick/%s/%s' % (SCHEME, port, token, filename)


def launch(url):
    """リンクを開く（登録されたアプリが起動する）。失敗したら OSError。BSManager は起動したまま使われることが多いので、
    exe 版の DLL の探し先を引き継がせない（core/launch.py）"""
    from core import launch as _launch
    _launch.start(url)


class Shelf:
    """一時的に配るプレイリスト。合言葉 → (中身, ファイル名, 期限)。アプリが読みに来たかも覚える（まとめて渡す時に、
    前の分を読みに来てから次を渡す。serve.py の export/oneclick/all）"""
    def __init__(self, ttl=TTL):
        self.ttl = ttl
        self._items = {}
        self._fetched = set()
        self._cond = threading.Condition()

    def put(self, body, filename, now=None):
        now = now or time.time()
        token = secrets.token_urlsafe(24)
        with self._cond:
            self._items = {k: v for k, v in self._items.items() if v[2] > now}   # 期限切れを捨てる
            self._fetched = {k for k in self._fetched if k in self._items}
            self._items[token] = (body, filename, now + self.ttl)
        return token

    def get(self, token, filename, now=None):
        """中身（無い・期限切れ・ファイル名が違えば None）。返したら「読みに来た」と覚える"""
        now = now or time.time()
        with self._cond:
            v = self._items.get(token)
            if not v or v[2] <= now or v[1] != filename:
                return None
            self._fetched.add(token)
            self._cond.notify_all()
        return v[0]

    def wait_fetched(self, token, timeout):
        """token の中身をアプリが読みに来るまで待つ（最長 timeout 秒）。来たら True"""
        with self._cond:
            return self._cond.wait_for(lambda: token in self._fetched, timeout)
