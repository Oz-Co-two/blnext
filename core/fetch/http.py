# BeatLeader の公開API（https://api.beatleader.com・ログイン不要）への通信。
# アクセス量の約束（docs/spec.md の「アクセス量の約束」・上限は docs/investigation.md）:
#  - 全体は1秒4回まで（上限は10秒に50回）、/players は2秒に1回まで（上限は10秒に10回）、同時接続は2本まで
#  - 応答の X-Rate-Limit-Remaining が少なくなったら X-Rate-Limit-Reset まで待つ
#  - 失敗は待ち時間を伸ばしながら3回までやり直す。429 は指示された時間だけ待ってやり直す
#  - User-Agent にアプリ名と版を入れる
# これらの数は利用者に変えさせない定数（仕様書）。
import gzip, http.client, json, logging, os, threading, time, urllib.error, urllib.parse, urllib.request, zlib
from datetime import datetime, timezone

API_BASE = 'https://api.beatleader.com'
GLOBAL_INTERVAL = 0.25     # 秒。1秒4回
PLAYERS_INTERVAL = 2.0     # 秒。/players は別に数えられ、上限が厳しい
MAX_CONCURRENCY = 2
RETRIES = 3                # 失敗（通信・5xx）のやり直しの回数
RETRY_429 = 5              # 429 のやり直しの回数
LOW_REMAINING = 3          # 残りがこれ以下なら区切りの時刻まで待つ
TIMEOUT = 30

log = logging.getLogger('bln.net')


class NetError(Exception):
    """code: offline / http-<番号> / bad-json / cancelled / no-net"""
    def __init__(self, code, detail=''):
        super().__init__(f'{code} {detail}'.strip())
        self.code, self.detail = code, detail


class Cancelled(NetError):
    def __init__(self):
        super().__init__('cancelled')


class _Limiter:
    """要求の開始の間隔を、区分（bucket）ごとに空ける"""
    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._next = {}
        self._clock = clock

    def reserve(self, bucket, interval):
        """次に始めてよい時刻までの待ち秒を返し、その次の枠を予約する"""
        with self._lock:
            now = self._clock()
            t = max(now, self._next.get(bucket, 0.0))
            self._next[bucket] = t + interval
            return t - now

    def hold(self, bucket, seconds):
        """今から seconds 秒は bucket の要求を始めない"""
        with self._lock:
            self._next[bucket] = max(self._next.get(bucket, 0.0), self._clock() + seconds)


def _reset_wait(headers, default):
    """429 や残りわずかの時に待つ秒。Retry-After（秒）→ X-Rate-Limit-Reset（ISO時刻）→ default"""
    ra = headers.get('Retry-After')
    if ra:
        try:
            return max(0.0, min(120.0, float(ra)))
        except ValueError:
            pass
    rs = headers.get('X-Rate-Limit-Reset')
    if rs:
        try:
            t = datetime.fromisoformat(rs.replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return max(0.0, min(120.0, (t - datetime.now(timezone.utc)).total_seconds() + 0.5))
        except ValueError:
            pass
    return default


def _urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def _no_net(req, timeout):
    raise urllib.error.URLError('BLN_NO_NET')


class BLClient:
    def __init__(self, user_agent, opener=None, base=API_BASE):
        if opener is None:
            opener = _no_net if os.environ.get('BLN_NO_NET') == '1' else _urlopen
        self.user_agent = user_agent
        self.base = base.rstrip('/')
        self._open = opener
        self._limiter = _Limiter()
        self._sem = threading.BoundedSemaphore(MAX_CONCURRENCY)
        self.cancel_event = threading.Event()
        self.requests = 0   # 数えるだけ（テストと進捗の表示用）

    def _sleep(self, seconds):
        if seconds > 0 and self.cancel_event.wait(seconds):
            raise Cancelled()
        if self.cancel_event.is_set():
            raise Cancelled()

    def url(self, path, params=None):
        q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        return self.base + path + ('?' + q if q else '')

    def get(self, path, **params):
        """GET して JSON を返す。だめなら NetError"""
        url = self.url(path, params)
        if self._open is _no_net:   # ネットに出ない設定（テスト）。やり直しで待たずにすぐ失敗にする
            raise NetError('offline', 'BLN_NO_NET')
        fails = limited = 0
        while True:
            self._sleep(self._limiter.reserve('all', GLOBAL_INTERVAL))
            if path.startswith('/players'):
                self._sleep(self._limiter.reserve('players', PLAYERS_INTERVAL))
            t0 = time.monotonic()
            status, headers, body, err = self._fetch(url)
            self.requests += 1
            ms = int((time.monotonic() - t0) * 1000)
            if status == 200:
                log.info('GET %s 200 %dms %dB', url, ms, len(body))
                self._watch_remaining(path, headers)
                try:
                    return json.loads(body)
                except ValueError as e:
                    log.warning('bad json %s: %s', url, e)
                    raise NetError('bad-json', url)
            log.warning('GET %s %s %dms %s', url, status or '-', ms, err or '')
            if status == 429:
                limited += 1
                if limited > RETRY_429:
                    raise NetError('http-429', url)
                wait = _reset_wait(headers, 10.0)
                self._limiter.hold('all', wait)
                self._sleep(wait)
                continue
            if status is None or status >= 500:
                fails += 1
                if fails > RETRIES:
                    raise NetError('offline' if status is None else 'http-%d' % status, err or url)
                self._sleep(2 ** (fails - 1))   # 1, 2, 4 秒
                continue
            raise NetError('http-%d' % status, url)   # 4xx はやり直しても同じ

    def _watch_remaining(self, path, headers):
        try:
            remaining = int(headers.get('X-Rate-Limit-Remaining', ''))
        except ValueError:
            return
        if remaining <= LOW_REMAINING:
            wait = _reset_wait(headers, 10.0)
            log.info('rate limit low (%d left) — wait %.1fs', remaining, wait)
            self._limiter.hold('players' if path.startswith('/players') else 'all', wait)

    def _fetch(self, url):
        """(status, headers, body, error)。通信できなければ status は None"""
        req = urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Accept': 'application/json',
                                                   'Accept-Encoding': 'gzip'})
        with self._sem:
            if self.cancel_event.is_set():
                raise Cancelled()
            try:
                with self._open(req, TIMEOUT) as r:
                    body = r.read()
                    headers = r.headers
                    status = getattr(r, 'status', 200)
            except urllib.error.HTTPError as e:
                try:
                    e.read()
                except Exception:
                    pass
                return e.code, e.headers or {}, b'', str(e)
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:   # 本文の途中切れ（IncompleteRead）も
                return None, {}, b'', '%s: %s' % (type(e).__name__, e)
        if (headers.get('Content-Encoding') or '').lower() == 'gzip':
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError, zlib.error) as e:   # 途中で切れた・壊れた gzip も通信の失敗としてやり直す
                return None, {}, b'', 'gzip: %s' % e
        return status, headers, body, None
