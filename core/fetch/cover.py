# 曲のカバー画像（Beat Saber の中で曲の横に出る絵）。BeatSaver の CDN の <譜面のハッシュ>.jpg を取ってきて、書き込み先の
# data/song_covers/<ハッシュ>.jpg にためる（プレイリストの画像の config/covers/ とは別）。画面は serve.py の GET /cover/<ハッシュ>.jpg で読む。
# BeatLeader の応答の song.coverImage は調べた500曲すべてが https://eu.cdn.beatsaver.com/<ハッシュ>.jpg だったので、URL はハッシュから
# 決める（DB に URL を持たない）。取り先は BeatSaver の CDN だけで、画面から URL やホストを受け取る口は作らない。
#  - ハッシュは 40桁の16進（小文字）だけ。中身は JPEG か PNG の印があり、MAX_BYTES 以下の物だけをためる
#  - 同時に取りに行くのは MAX_CONCURRENCY 本まで。取り始めは MIN_INTERVAL 秒ずつ空ける（初めて見る長い表でも一度に取りに行かない）。
#    時間切れは短め（画面の読み込みを長く止めないように）
#  - 取れなかったハッシュはしばらく覚えて、すぐには取り直さない（アプリを開いている間だけ。無い・画像でない＝長め、
#    通信の失敗＝短め）。どちらの CDN にもつながらない時は、しばらく全部の取得を止める（オフラインの時に1枚ずつ待たない）
#  - ネットに出ない設定（BLN_NO_NET=1。テスト）では取りに行かず、ためた物だけを返す
import http.client, logging, os, re, threading, time, urllib.error, urllib.request
from urllib.parse import urlsplit

from .. import fsutil

log = logging.getLogger('bln.cover')

CDN_URLS = ('https://eu.cdn.beatsaver.com/%s.jpg', 'https://cdn.beatsaver.com/%s.jpg')   # 先が主、後ろは予備
CDN_HOST = 'beatsaver.com'   # 転送（リダイレクト）で行ってよいのは、この名前とその下だけ
MAX_BYTES = 1024 * 1024      # 1枚の上限（ふつうは 256×256 前後の JPEG で数十KB）
MAX_CONCURRENCY = 2
MIN_INTERVAL = 0.25          # 秒。取り始めの間（1秒に4枚まで。BeatSaver の CDN に一度に多く頼まない）
TIMEOUT = 6                  # 秒
QUEUE_WAIT = 15              # 秒。ほかの取得が終わるのを待つ最長（過ぎたら今回は代わりの絵にして、次の機会に取る）
GONE_TTL = 6 * 3600          # 秒。無い（4xx）・画像でない時に、取り直さない間
NET_TTL = 5 * 60             # 秒。通信の失敗・5xx・429 の時に、取り直さない間
DOWN_TTL = 60                # 秒。主も予備もつながらなかった時に、全部の取得を止める間
PNG_SIG, JPEG_SIG = b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff'
_HASH = re.compile(r'[0-9a-f]{40}')
_NAME = re.compile(r'([0-9a-fA-F]{40})\.jpg')


def is_hash(h):
    return isinstance(h, str) and _HASH.fullmatch(h) is not None


def parse_name(name):
    """URL の最後の部分（'<ハッシュ>.jpg'）→ 小文字のハッシュ。形が違えば None"""
    m = _NAME.fullmatch(name) if isinstance(name, str) else None
    return m.group(1).lower() if m else None


def image_type(data):
    """中身 → 'image/jpeg' / 'image/png'。画像の印が無い・大きすぎる・空なら None"""
    if not data or len(data) > MAX_BYTES:
        return None
    return 'image/jpeg' if data.startswith(JPEG_SIG) else 'image/png' if data.startswith(PNG_SIG) else None


class _CdnOnly(urllib.request.HTTPRedirectHandler):
    """転送は BeatSaver の CDN（https の beatsaver.com とその下）の中だけ追う"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlsplit(newurl)
        host = (u.hostname or '').lower()
        if u.scheme != 'https' or not (host == CDN_HOST or host.endswith('.' + CDN_HOST)):
            return None   # 追わない（元の 3xx が HTTPError になり、失敗として扱う）
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CdnOnly)


def _urlopen(req, timeout):
    return _OPENER.open(req, timeout=timeout)


class _Fail(Exception):
    """kind: gone（無い・画像でない。長く覚える）/ retry（5xx・429。短く覚える）/ net（つながらない。予備を試す）"""
    def __init__(self, kind, detail):
        super().__init__('%s %s' % (kind, detail))
        self.kind = kind


class Covers:
    """曲のカバー画像の置き場所。get(ハッシュ) → (中身, 種類) か None（代わりの絵を出す）"""

    def __init__(self, folder, user_agent, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.folder = folder
        self.user_agent = user_agent
        self._open = opener or _urlopen
        self._clock = clock
        self._sleep = sleep
        self._next_at = 0.0      # 次に取り始めてよい時刻（MIN_INTERVAL ずつ空ける）
        self._sem = threading.BoundedSemaphore(MAX_CONCURRENCY)
        self._lock = threading.Lock()
        self._busy = {}          # ハッシュ → Lock（同じハッシュを2つの要求が同時に取りに行かない）
        self._retry_at = {}      # ハッシュ → この時刻までは取り直さない（取れなかった）
        self._down_until = 0.0   # この時刻までは、どのハッシュも取りに行かない（CDN につながらない）
        self.fetches = 0         # 取りに行った回数（テストの確かめ用）

    def path(self, h):
        return os.path.join(self.folder, h + '.jpg')

    def cached(self, h):
        """ためた物 (中身, 種類)。無い・読めない・形が違えば None"""
        try:
            with open(self.path(h), 'rb') as f:
                data = f.read(MAX_BYTES + 1)
        except OSError:
            return None
        typ = image_type(data)
        return (data, typ) if typ else None

    def get(self, h):
        """ハッシュの画像 (中身, 'image/jpeg'|'image/png')。ためた物があればそれ、無ければ CDN から取ってためる。
        取れない・取りに行かない時は None"""
        if not is_hash(h):
            return None
        got = self.cached(h)
        if got or os.environ.get('BLN_NO_NET') == '1' or not self._may_fetch(h):
            return got
        with self._lock:
            busy = self._busy.setdefault(h, threading.Lock())
        with busy:
            got = self.cached(h)   # 待っている間に、ほかの要求が取ってためた
            if got or not self._may_fetch(h):
                return got
            if not self._sem.acquire(timeout=QUEUE_WAIT):
                return None        # 混んでいる。覚えずに、次の機会に取る
            try:
                if not self._may_fetch(h):   # 待っている間に CDN につながらないと分かった
                    return None
                self._pace()
                return self._fetch(h)
            finally:
                self._sem.release()

    def _pace(self):
        """前に取り始めた時から MIN_INTERVAL 秒たつまで待つ（同時の数は _sem で決まるので、待つのは長くて数本分）"""
        with self._lock:
            now = self._clock()
            start = max(now, self._next_at)
            self._next_at = start + MIN_INTERVAL
        if start > now:
            self._sleep(start - now)

    def _may_fetch(self, h):
        now = self._clock()
        with self._lock:
            return now >= self._down_until and now >= self._retry_at.get(h, 0.0)

    def _fetch(self, h):
        """主の CDN → （つながらない・5xx の時だけ）予備の CDN。取れたら確かめてためる"""
        fail = None
        for tmpl in CDN_URLS:
            url = tmpl % h
            try:
                data = self._download(url)
            except _Fail as e:
                log.warning('cover %s: %s', url, e)
                fail = e
                if e.kind == 'gone':
                    break   # 無い・画像でない物は、予備にも無い（同じ置き場所）
                continue
            typ = image_type(data)
            try:
                os.makedirs(self.folder, exist_ok=True)
                fsutil.write_atomic(self.path(h), data)
            except OSError as e:   # ためられなくても、今回は返す
                log.warning('cover %s: cannot save: %s', h, e)
            log.info('cover %s %dB', url, len(data))
            return data, typ
        now = self._clock()
        with self._lock:
            self._retry_at[h] = now + (GONE_TTL if fail.kind == 'gone' else NET_TTL)
            if fail.kind == 'net':   # 主も予備もつながらない
                self._down_until = now + DOWN_TTL
        return None

    def _download(self, url):
        """1つの URL の中身（確かめた画像）。だめなら _Fail"""
        self.fetches += 1
        req = urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Accept': 'image/jpeg,image/png'})
        try:
            with self._open(req, TIMEOUT) as r:
                status = getattr(r, 'status', 200)
                if status != 200:
                    raise _Fail('retry' if status >= 500 else 'gone', 'status %s' % status)
                try:
                    size = int((r.headers or {}).get('Content-Length') or 0)
                except (TypeError, ValueError):
                    size = 0
                if size > MAX_BYTES:
                    raise _Fail('gone', 'too large (%d)' % size)
                data = r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise _Fail('retry' if e.code >= 500 or e.code == 429 else 'gone', 'http-%d' % e.code)
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:   # 時間切れ・途中切れも
            raise _Fail('net', '%s: %s' % (type(e).__name__, e))
        if not image_type(data):
            raise _Fail('gone', 'not an image (%dB)' % len(data or b''))
        return data
