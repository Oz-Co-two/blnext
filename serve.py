# ローカルサーバー: 画面（web/）の静的ファイルと、ローカル専用のAPI（POST /__api/*）。
# app.py が空きポートでスレッド起動して pywebview で表示する。開発中は `python serve.py` 単体で起動し、
# 表示される合言葉付きの URL を普通のブラウザ（Edge/Chrome）で開いて確認できる。
# 骨組みは NLM（Non-Linear Mapper Oz-Co2版）の serve.py を流用した。
import base64, binascii, hmac, http.server, json, os, platform, posixpath, secrets, sys, threading, time
from urllib.parse import quote, unquote, urlencode, urlsplit

from core import app_update, launch, logs, review, views
from core.estimate.assemble import RUNNERS_UP, assemble, auto_star_ranges
from core.estimate import order
from core.estimate.engine import Engine
from core.estimate.recommend import beyond_drop
from core.output import bplist, bsfind, oneclick
from core.fetch import beatleader as bl, cover as song_cover, replay
from core.fetch.http import BLClient, NetError
from core.fetch.refresh import Refresher
from core.settings import (DIFFICULTY, DIFFICULTY_SET_DEFAULT, DIFFICULTY_SET_SIZE, DIFFICULTY_SETS, INITIAL_SET_SIZE, SPEC, Settings,
                           SettingsError, describe as describe_settings, difficulty_of, initial_target)
from core.store.db import Store
from core.version import app_version

# BASE_DIR = アプリ本体の置き場（読み取り専用でよい）。exe 版（PyInstaller）ではバンドルの展開先を app.py が渡す。
# DATA_DIR = 書き込み先（config/ data/ logs/ exports/）。exe 版では exe の隣。環境変数が無ければ両方このファイルの隣
BASE_DIR = os.environ.get('BLN_BASE_DIR') or os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('BLN_DATA_DIR') or BASE_DIR
WEB_DIR = os.path.join(BASE_DIR, 'web')
CONFIG_DIR = os.path.join(DATA_DIR, 'config')
APP_VERSION = app_version(BASE_DIR)
FROZEN = bool(getattr(sys, 'frozen', False))

logs.setup(DATA_DIR)
SETTINGS = Settings(CONFIG_DIR)
STORE = Store(os.path.join(DATA_DIR, 'data', 'blnext.db'))
USER_AGENT = 'blnext/%s' % (APP_VERSION.lstrip('v') or 'dev')   # アプリ名と版（仕様書の「アクセス量の約束」）
REFRESHER = Refresher(STORE, SETTINGS, lambda: BLClient(USER_AGENT))
ENGINE = Engine(STORE, SETTINGS)
# おすすめ画面での一時的な操作（外す・次点と入れ替える）。セットごと・アプリを閉じるまで。固定・今後出さないは DB（user_marks）に残す
SESSION = {}   # セットの id → {'removed': set, 'added': set}
_session_lock = threading.Lock()
SHELF = oneclick.Shelf()   # ワンクリックで一時的に配るプレイリスト（/oneclick/<合言葉>/<ファイル名>）
# 曲のカバー画像（/cover/<ハッシュ>.jpg。BeatSaver の CDN から取って data/song_covers/ にためる。core/fetch/cover.py）
SONG_COVERS = song_cover.Covers(os.path.join(DATA_DIR, 'data', 'song_covers'), USER_AGENT)
NO_COVER = os.path.join(WEB_DIR, 'icons', 'no-cover.svg')   # 取れなかった時の代わりの絵（画面も hash が無い時に使う）
SERVER = {'port': None}    # 待ち受けのポート（make_server が入れる。ワンクリックの URL に使う）
UPDATER = app_update.Updater(BASE_DIR, DATA_DIR, FROZEN)   # アプリ内の更新（core/app_update.py。差し替える先は exe の隣＝DATA_DIR）

# ---- アクセス制御（NLM と同じ） ----
# 待ち受けは 127.0.0.1 のみだが、それだけでは「普段のブラウザで開いた悪意あるサイトが 127.0.0.1 へ要求を送る」攻撃（CSRF）と、
# DNS リバインディング（攻撃者のドメイン名を 127.0.0.1 へ向け直して読み書きする）を防げない。
#  ① Host ヘッダーがローカルの名前でなければ拒否（DNS リバインディング対策。追加は環境変数 BLN_ALLOWED_HOSTS にカンマ区切り）
#  ② POST は独自ヘッダー X-BLN-Request: 1 必須（他サイトから付けて送るには CORS の事前確認が要り、ここはそれを許可しない）
#  ③ Origin ヘッダーが付いていて他サイトのものなら拒否（念のための二重化）
#  ④ 起動ごとの合言葉を X-BLN-Token ヘッダーで求める（①〜③はブラウザ経由の攻撃だけを防ぎ、ブラウザ以外のプログラムからは通るため）。
#     画面へは URL の #k=… で渡し（access_url。# 以降はサーバーにも Referer にも送られない）、web/js/boot.js が同じオリジンへの
#     fetch/XHR に自動で付ける。Cookie では渡さない（127.0.0.1 の Cookie はポートを区別せず、別ポートのページへ漏れる）。
#     合言葉なしで返すのは PUBLIC_* の静的ファイル（<script src>・<link>・<img>・import() はヘッダーを付けられない）と、
#     ワンクリックのプレイリスト（/oneclick/。URL の中の推測できない合言葉で守る）と、曲のカバー画像（/cover/。中身は BeatSaver の
#     公開の画像だけ）だけ。
#     守る場所を列挙すると取りこぼすので、返してよい物だけを列挙する。画面がヘッダー無しで読み込む物を新しいフォルダに置いたら足す
ALLOWED_HOSTS = {'127.0.0.1', 'localhost', '::1'} | {
    h.strip().lower() for h in (os.environ.get('BLN_ALLOWED_HOSTS') or '').split(',') if h.strip()}
SESSION_TOKEN = secrets.token_urlsafe(32)
PUBLIC_FILES = {'index.html'}
PUBLIC_DIRS = {'js', 'css', 'icons', 'fonts', 'guide'}   # guide＝使い方・統計の案内の画像（web/guide）


def access_url(port, host='127.0.0.1'):
    """合言葉付きの、画面を開く URL"""
    return 'http://%s:%d/index.html#k=%s' % (host, port, SESSION_TOKEN)


def _url_parts(path):
    """URL のパスを要素に分ける（? # を落とし、%xx を戻して .. を畳む）"""
    rel = posixpath.normpath(unquote(path.split('?', 1)[0].split('#', 1)[0]))
    return [p for p in rel.split('/') if p and p != '.']


def _is_public(path):
    """合言葉なしで返してよい静的ファイルか。名前の完全一致だけで判定する（大文字小文字のみ同一視）"""
    parts = [p.lower() for p in _url_parts(path)]
    if not parts:
        return False
    if len(parts) == 1:
        return parts[0] in PUBLIC_FILES
    return parts[0] in PUBLIC_DIRS


def _hostname(hostport):
    """'127.0.0.1:8139' / '[::1]:8139' / 'localhost' → ホスト名部分（小文字）"""
    hp = (hostport or '').strip().lower()
    if hp.startswith('['):
        return hp[1:hp.find(']')] if ']' in hp else hp[1:]
    return hp.rsplit(':', 1)[0] if hp.count(':') == 1 else hp


# ---- API（POST /__api/<名前>、本体も応答も JSON） ----
# 失敗は {ok:false, error:<種類>} で返し、文言は画面が訳す。
API = {}


class ApiError(Exception):
    def __init__(self, code, **extra):
        super().__init__(code)
        self.code, self.extra = code, extra


def api(name):
    def deco(fn):
        API[name] = fn
        return fn
    return deco


def last_run_at(player_id):
    """最後まで取得できた回の時刻（そのプレイヤーの物でなければ None）"""
    t = int(STORE.meta('last_run_at') or 0)
    who = STORE.meta('last_run_player')   # 無い＝この記録を始める前の DB（プレイヤーは1人だったとみなす）
    return t if t and player_id and who in (None, player_id) else None


def should_auto_refresh(v, now=None):
    """起動時に自動で取得するか: 前回の取得から auto_refresh_hours 以上たっている（0 なら毎回）か、まだ取得していない時だけ。
    それより近ければ取らない（画面の「更新」を押した時だけ取る）。ネットに出ない設定（テスト）では取らない"""
    if not v['player_id'] or os.environ.get('BLN_NO_NET') == '1':
        return False
    last = last_run_at(v['player_id'])
    return last is None or (now or time.time()) - last >= v['auto_refresh_hours'] * 3600


@api('status')
def _api_status(body):
    v = SETTINGS.values()
    return {'version': APP_VERSION, 'frozen': FROZEN, 'playerId': v['player_id'], 'lang': v['lang'],
            'noNet': os.environ.get('BLN_NO_NET') == '1', 'lastRunAt': last_run_at(v['player_id']),
            'autoRefresh': should_auto_refresh(v), 'autoRefreshHours': v['auto_refresh_hours'], 'uiScale': v['ui_scale'],
            'presets': SETTINGS.presets(brief=True), 'activePreset': SETTINGS.active_preset(), 'difficulty': _difficulty_info(),
            'group': _group_ids(),   # 組のセット（交互に選ぶ順。画面の「組を交互に選び直す」）
            'setup': _setup_state(v), 'updateCheck': v['update_check'], 'justUpdated': UPDATER.just_updated,
            'statsPrivate': STORE.meta('stats_public') == '0', 'statsNotice': v['stats_notice']}


def _difficulty_info():
    """難しさの段階（左から順）。画面のスライダーと、難しさ違いのセットを作る小さな画面に使う（core/settings.py の DIFFICULTY）。
    setName は難しさ違いのセットを作る時のセットの名前、setDefault・setSize はその画面の初期値（作る段階・曲数）"""
    return {'levels': [{'id': k, 'values': dict(v), 'setName': DIFFICULTY_SETS[k]} for k, v in DIFFICULTY.items()],
            'setDefault': list(DIFFICULTY_SET_DEFAULT), 'setSize': DIFFICULTY_SET_SIZE}


def _preset_arg(body, key='preset'):
    """本体の中のセットの id（無ければ None＝画面で開いているセット）"""
    pid = body.get(key)
    if pid is not None and not isinstance(pid, str):
        raise ApiError('bad-request')
    return pid


def _settings_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except SettingsError as e:
        raise ApiError(e.code, key=e.key)


def _view(body):
    """本体の preset のセットから見た設定"""
    return _settings_call(SETTINGS.view, _preset_arg(body))


@api('settings/get')
def _api_settings_get(body):
    """level は今の値が合う難しさの段階（どれとも合わなければ null＝カスタム）"""
    view = _view(body)
    v = view.values()
    return {'values': v, 'spec': describe_settings(), 'preset': view.preset, 'presets': SETTINGS.presets(),
            'difficulty': _difficulty_info(), 'level': difficulty_of(v),
            # 全曲データの欄: 最後に BeatLeader からランク譜面を取った時・変化を確かめた時・その全曲データの作成時刻
            'maps': {k: int(STORE.meta(m) or 0) for k, m in (('fetchedAt', 'maps_fetched_at'), ('checkedAt', 'maps_checked_at'), ('asOf', 'song_details_as_of'))}}


@api('settings/save')
def _api_settings_save(body):
    """難しさのスライダーも、その段階の項目をまとめてここで保存する（changes に複数の項目）"""
    v = _settings_call(SETTINGS.update, body.get('changes'), preset=_preset_arg(body))
    return {'values': v, 'level': difficulty_of(v)}


@api('settings/reset')
def _api_settings_reset(body):
    v = _settings_call(SETTINGS.reset, body.get('keys'), preset=_preset_arg(body))
    return {'values': v, 'level': difficulty_of(v)}


# ---- セット（設定のパターン。core/settings.py） ----
@api('presets/add')
def _api_presets_add(body):
    pid = _settings_call(SETTINGS.add_preset, body.get('name'), copy_from=_preset_arg(body, 'copyFrom'))
    SETTINGS.update({'active_preset': pid})
    return {'id': pid, 'presets': SETTINGS.presets(brief=True)}


@api('presets/difficulty-set')
def _api_presets_difficulty_set(body):
    """copyFrom のセット（無ければ画面で開いているセット）を写して、難しさ違いのセットを作り（Settings.add_difficulty_presets）、
    最初に作ったセットを開く。levels は作る段階（DIFFICULTY のキーの並び）、size は曲数（playlist_size の範囲）。どちらも省くと
    初期値（DIFFICULTY_SET_DEFAULT・DIFFICULTY_SET_SIZE）。作ったセットは組のセット（exclude_other_sets オン）になるので、前からある
    組のセットも含めた組全部のリストを、易しいセットから1曲ずつ交互に選んで作る（_draft。前からある組のセットのリストも変わる）。
    group は組のセットの id（選ぶ順）"""
    src = _preset_arg(body, 'copyFrom') or SETTINGS.active_preset()
    levels = body.get('levels', list(DIFFICULTY_SET_DEFAULT))
    size = body.get('size', DIFFICULTY_SET_SIZE)
    ids = _settings_call(SETTINGS.add_difficulty_presets, src, levels, size)
    _color_difficulty_sets([k for k in DIFFICULTY if k in levels], ids)
    SETTINGS.update({'active_preset': ids[0]})
    group = _group_ids()
    _draft([SETTINGS.view(p) for p in group])
    return {'ids': ids, 'presets': SETTINGS.presets(brief=True), 'activePreset': ids[0], 'group': group}


@api('presets/rename')
def _api_presets_rename(body):
    _settings_call(SETTINGS.rename_preset, _preset_arg(body, 'id'), body.get('name'))
    return {'presets': SETTINGS.presets(brief=True)}


@api('presets/order')
def _api_presets_order(body):
    """セットの並びを変える（おすすめの画面のタブをドラッグ・Alt+←→）。ids は今あるセットの id をちょうど1回ずつ並べた物
    （でなければ bad-request）。並び＝タブ・ホームの振り返りのタブ・全部のセットを書き込む／ワンクリックで入れる順。
    組のセットを交互に選ぶ順（_group_ids）は難しさの順なので変わらない"""
    _settings_call(SETTINGS.reorder_presets, body.get('ids'))
    return {'presets': SETTINGS.presets(brief=True)}


@api('presets/delete')
def _api_presets_delete(body):
    pid = _preset_arg(body, 'id')
    _settings_call(SETTINGS.delete_preset, pid)
    with STORE.tx():   # そのセットの固定と保存したリストも消す（出力の記録は振り返りの数値の調整に使うので残す）
        STORE.conn.execute("DELETE FROM user_marks WHERE preset=? AND mark='pin'", (pid,))
        STORE.conn.execute('DELETE FROM rec_lists WHERE preset=?', (pid,))
    with _session_lock:
        SESSION.pop(pid, None)
    return {'presets': SETTINGS.presets(brief=True), 'activePreset': SETTINGS.active_preset()}


@api('presets/open')
def _api_presets_open(body):
    """画面で開くセットを覚える（おすすめ・似ている人の画面と、次に起動した時）"""
    _settings_call(SETTINGS.update, {'active_preset': _preset_arg(body, 'id')})
    return {'activePreset': SETTINGS.active_preset()}


# ---- プレイリストの画像（セットごと） ----
COVER_MAX = 2 * 1024 * 1024
PNG_SIG, JPEG_SIG = b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff'


def cover_of(preset):
    """(画像のバイト列, 'image/png'|'image/jpeg', 選んだ画像か)。選んでいなければアプリのアイコン"""
    path = SETTINGS.cover_path(preset)
    try:
        if path:
            with open(path, 'rb') as f:
                return f.read(), 'image/jpeg' if path.endswith('.jpg') else 'image/png', True
        with open(os.path.join(WEB_DIR, 'icons', 'app.png'), 'rb') as f:
            return f.read(), 'image/png', False
    except OSError:
        return None, 'image/png', False


# 用意した画像（アプリのアイコンの色違い。web/icons/covers/<名前>.png。tools/make_icons.py が作る）。画面からは名前だけを受け取る
COVER_PRESETS = ('blue', 'purple', 'orange', 'red', 'slate')
DIFFICULTY_COVERS = {'steady': 'blue', 'challenge': 'red'}   # 難しさ違いのセットを作る時の画像（標準は写したセットのまま）


def _cover_preset_bytes(name):
    with open(os.path.join(WEB_DIR, 'icons', 'covers', name + '.png'), 'rb') as f:
        return f.read()


def _set_cover_preset(pid, name):
    _settings_call(SETTINGS.set_cover, pid, _cover_preset_bytes(name), 'png')


def _color_difficulty_sets(levels, ids):
    """難しさ違いのセットの画像の色を段階ごとに変える（ゲームのプレイリストの一覧で見分けられるように。標準は写したセットの画像のまま）"""
    for level, pid in zip(levels, ids):
        if DIFFICULTY_COVERS.get(level):
            _set_cover_preset(pid, DIFFICULTY_COVERS[level])


@api('cover/get')
def _api_cover_get(body):
    """そのセットの画像。choice は今の画像がどれか（'app'＝アプリのアイコン・用意した画像の名前・None＝読み込んだ画像）、
    presets は用意した画像の名前（画面は icons/covers/<名前>.png で見せる）"""
    view = _view(body)
    data, typ, custom = cover_of(view.preset)
    choice = 'app' if not custom else None
    if custom and data:
        for name in COVER_PRESETS:
            try:
                if _cover_preset_bytes(name) == data:
                    choice = name
                    break
            except OSError:
                pass
    return {'dataUrl': 'data:%s;base64,%s' % (typ, base64.b64encode(data).decode('ascii')) if data else None, 'custom': custom,
            'choice': choice, 'presets': list(COVER_PRESETS)}


@api('cover/preset')
def _api_cover_preset(body):
    """用意した画像（COVER_PRESETS の名前）をそのセットの画像にする。名前が 'app' ならアプリのアイコンに戻す"""
    view = _view(body)
    name = body.get('name')
    if name == 'app':
        _settings_call(SETTINGS.set_cover, view.preset, None, 'png')
    elif name in COVER_PRESETS:
        try:
            _set_cover_preset(view.preset, name)
        except OSError:
            raise ApiError('bad-image')
    else:
        raise ApiError('bad-request')
    return _api_cover_get({'preset': view.preset})


@api('cover/set')
def _api_cover_set(body):
    """画像を置く。画面が正方形に切り抜いて縮めた PNG/JPEG を base64 で送る（場所ではなく中身。種類と大きさは Python が確かめる）"""
    view = _view(body)
    data = body.get('data')
    if not isinstance(data, str) or len(data) > COVER_MAX * 4 // 3 + 8:
        raise ApiError('bad-image')
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError('bad-image')
    ext = 'png' if raw.startswith(PNG_SIG) else 'jpg' if raw.startswith(JPEG_SIG) else None
    if not ext or len(raw) > COVER_MAX:
        raise ApiError('bad-image')
    _settings_call(SETTINGS.set_cover, view.preset, raw, ext)
    return _api_cover_get({'preset': view.preset})


@api('cover/clear')
def _api_cover_clear(body):
    view = _view(body)
    _settings_call(SETTINGS.set_cover, view.preset, None, 'png')
    return _api_cover_get({'preset': view.preset})


@api('players/search')
def _api_players_search(body):
    name = body.get('name')
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 40:
        raise ApiError('bad-request')
    name = name.strip()
    try:
        rows = _player_by_id(name) if name.isascii() and name.isdigit() else bl.search_players(BLClient(USER_AGENT), name)
    except NetError as e:
        raise ApiError(e.code)
    return {'players': [{'id': r['player_id'], 'name': r['name'], 'country': r['country'], 'pp': r['pp'],
                         'rank': r['rank'], 'countryRank': r['country_rank'], 'avatar': r['avatar']} for r in rows]}


def _player_by_id(pid):
    """プレイヤーの ID で引く（[その人] か、いなければ []。つながらなければ NetError）"""
    try:
        return [bl.player(BLClient(USER_AGENT), pid)]
    except NetError as e:
        if e.code == 'http-404':
            return []
        raise


# ---- 初回の準備（web/js/setup.js の小さな画面） ----
# プレイヤーを選ぶ → 最初のセットを難しさ違いの3つに置き換える（setup/start）→ 画面が取得を始める → 取得が終わったら3つのリストを
# 交互に選んで作る（setup/lists）。途中で閉じても、次に開いた時に続きから（meta の setup_pending）。
# 3つのセットを設定の初期値にせず初回の準備で作るのは、前の版から使っている人（キーが無い＝初期値）のセットを変えないため
def _setup_state(v):
    """'player'＝プレイヤーを選ぶ所から、'lists'＝選んだ（取得が終わったらリストを作る）、None＝初回の準備は無い"""
    if not v['player_id']:
        return 'player'
    return 'lists' if STORE.meta('setup_pending') else None


@api('setup/start')
def _api_setup_start(body):
    """id の人を自分にする（BeatLeader で確かめ、国と目標の順位も合わせる）。手を付けていない最初のセットは、難しさ違いの3つのセットに
    する。取得は画面が続けて始める（refresh/start）"""
    pid = body.get('id')
    if not isinstance(pid, str) or not pid.isascii() or not pid.isdigit():
        raise ApiError('bad-request')
    try:
        rows = _player_by_id(pid)
    except NetError as e:
        raise ApiError(e.code)
    if not rows:
        raise ApiError('player-not-found')
    me = rows[0]
    changes = {'player_id': me['player_id']}
    if bl.is_country_code(me['country']):
        changes['country'] = me['country']
    target = initial_target(me['country_rank'])
    if target:
        changes['target_country_rank'] = target
    _settings_call(SETTINGS.update, changes)
    ids = _settings_call(SETTINGS.make_initial_sets)
    _color_difficulty_sets(list(DIFFICULTY), ids)
    if ids:   # ★の範囲を入れるのはこのセットだけ（人を選び直した時は ids が空＝前の記録のまま）
        STORE.set_meta('setup_sets', json.dumps(ids))
    STORE.set_meta('setup_pending', 1)
    _remember_stats(me.get('stats_public'))
    return {'name': me['name'], 'statsPublic': me.get('stats_public')}


def _remember_stats(public):
    STORE.set_meta('stats_public', None if public is None else int(public))


@api('stats/check')
def _api_stats_check(body):
    """統計を公開にしたかを BeatLeader に確かめる（案内の「オンにした」。web/js/stats.js）。statsPublic は true・false・null（分からない）"""
    pid = SETTINGS.values()['player_id']
    if not pid:
        raise ApiError('no-player')
    try:
        rows = _player_by_id(pid)
    except NetError as e:
        raise ApiError(e.code)
    if not rows:
        raise ApiError('player-not-found')
    public = rows[0].get('stats_public')
    _remember_stats(public)
    return {'statsPublic': public}


@api('open/bl-settings')
def _api_open_bl_settings(body):
    """BeatLeader の設定（統計を公開にするページ）をブラウザで開く。開くのはこの決まったページだけ"""
    try:
        open_browser(bl.SETTINGS_SCORES_URL)
    except OSError as e:
        raise ApiError('open-failed', detail=str(e))
    return {}


@api('setup/lists')
def _api_setup_lists(body):
    """取得が終わった後に、初回の準備で作ったセットへ★の範囲を入れ（_set_auto_stars）、全部のセットのリストを作って保存する（組のセットは
    交互に選ぶ）。sets はセットごとの曲数と増える総PPの見込みと★の範囲（データが足りなければ曲数 0。ランク曲のスコアが無い人）"""
    _set_auto_stars()
    group = _group_ids()
    made = (_draft([SETTINGS.view(p) for p in group]) if len(group) >= 2 else None) or {}
    out = []
    for p in SETTINGS.presets(brief=True):
        saved = made.get(p['id']) or _lists(SETTINGS.view(p['id']))[0]
        lst = saved['list'] if saved else {'items': [], 'summary': None}
        v = SETTINGS.values(p['id'])
        out.append({'id': p['id'], 'name': p['name'], 'level': difficulty_of(v), 'songs': len(lst['items']),
                    'gain': (lst.get('summary') or {}).get('gain'), 'starMin': v['star_min'], 'starMax': v['star_max'],
                    'fewSimilar': lst.get('fewSimilar'), 'noClear': lst.get('noClear')})
    STORE.set_meta('setup_pending', None)
    return {'sets': out}


def _set_auto_stars():
    """初回の準備で作ったセット（meta の setup_sets）に、自分の自己ベストの★から決めた★の範囲を入れる（assemble.auto_star_ranges）。
    もう消した・★の範囲を自分で入れた・段階を変えたセットには入れない。データが足りなければ入れない（0 のまま）"""
    for pid in STORE.meta_json('setup_sets') or []:
        if pid not in SETTINGS.preset_ids():
            continue
        view = SETTINGS.view(pid)
        v = view.values()
        model, est = ENGINE.get(view)
        ranges = auto_star_ranges(est, model, v['modes']) if model is not None else None
        level = difficulty_of(v)
        if ranges and level in ranges and not v['star_min'] and not v['star_max']:
            lo, hi = ranges[level]
            _settings_call(SETTINGS.update, {'star_min': lo, 'star_max': hi}, preset=pid)
    STORE.set_meta('setup_sets', None)


@api('refresh/start')
def _api_refresh_start(body):
    return {'progress': REFRESHER.start()}


@api('maps/refetch')
def _api_maps_refetch(body):
    """設定の「BeatLeader から全曲データを更新」: 次の取得でランク譜面の一覧を必ず取る印を付ける（maps_fetched_at を空に。画面が続けて refresh/start を呼ぶ）"""
    if not SETTINGS.get('player_id'):
        return {'ok': False, 'error': 'no-player'}
    if REFRESHER.progress()['running']:
        return {'ok': False, 'error': 'busy'}
    STORE.set_meta('maps_fetched_at', None)
    return {}


@api('refresh/progress')
def _api_refresh_progress(body):
    return {'progress': REFRESHER.progress()}


@api('refresh/cancel')
def _api_refresh_cancel(body):
    return {'progress': REFRESHER.cancel()}


@api('home')
def _api_home(body):
    return views.home(STORE, SETTINGS)


# ---- おすすめ ----
# 画面のリストは作った時のまま DB の rec_lists にセットごとに残し、データを取り直しても勝手には変えない（作り直すかは利用者が
# 決める。前に作ったリストの理由を、次のプレイまでの間に見直せるように）。作り直すのは、まだ無い時・リストに効く設定が
# 変わった時・画面の操作（外す・固定・入れ替え・戻す）の時・「作り直す」を押した時。今のデータで作り直すと変わる時は、
# そのことを画面に知らせる（pending）。出力するのも、この保存したリスト（画面に出ている物）
_ITEM_KEYS = ('leaderboard_id', 'kind', 'song', 'mapper', 'difficulty', 'mode', 'stars', 'duration', 'best_acc', 'best_pp', 'best_time',
              'base_acc', 'est_acc', 'est_pp', 'gain', 'rank_up', 'confidence', 'score', 'flags', 'similar_n', 'refs', 'upload_time',
              'pinned', 'pick', 'replay', 'clear', 'challenge', 'notes', 'nps', 'njs', 'bpm', 'bombs', 'walls', 'peak_ebpm', 'tags',
              'acc_rating', 'pass_rating', 'tech_rating', 'best_mods', 'best_fc', 'best_miss', 'play_count', 'last_try',
              'tried', 'fails',   # tried: 自己ベストの無い曲の挑戦の記録（未クリア）、fails: クリアできなかった日で見込みを下げた時
              'p_up', 'gain_up',  # 推定の幅（recommend.py）: 自己ベストを超える見込み・超えた時の増える総PP の平均
              'acc_cap',          # 精度の上限（recommend.py）: 上限・その精度の譜面・窓の曲の数・推定の幅のうち上限より上の割合
              'hash')             # 譜面のハッシュ（曲のカバー画像 /cover/<hash>.jpg。出力にも使う）
# 保存するリストの曲（出力・リプレイの頭を読むのに使う物も）。parts（推定の内訳）は出力の記録に残すだけで、画面には出さない
_KEEP_KEYS = _ITEM_KEYS + ('score_id', 'replay_url', 'parts')
_LIST_KEYS = ('slots', 'short', 'counts', 'summary', 'filters', 'similarCount', 'fewSimilar', 'noClear', 'range', 'calc')
# リストの中身に効かない設定（変えても作り直さない）
_NOT_LIST_KEYS = ('neighbor_display', 'beat_saber_dir', 'playerdata_path', 'output_filename', 'playlist_title',
                  'auto_refresh_hours', 'fetch_song_details', 'update_check', 'update_skip', 'stats_notice', 'lang', 'ui_scale', 'active_preset')


def _item(c):
    return {k: c.get(k) for k in _ITEM_KEYS}


def _calc_info(model, view):
    """開いた欄の説明に使う数値: 自分の★の範囲の上端と、その上で★1 あたり下げる精度・クリアの見込みの元にした日数・
    モードの★の換算の倍率（上端と下げ方は換算した★の値。画面がその曲のモードの★に直して出す）"""
    c = model.clear
    return {'starHi': model.star_hi, 'drop': beyond_drop(model, view.values()), 'starScale': model.star_scale,
            'clearDays': c.days if c else None, 'clearFails': c.fails if c else None}


def _sess(pid):
    """そのセットの一時的な操作（呼ぶ側で _session_lock を持つ）"""
    return SESSION.setdefault(pid, {'removed': set(), 'added': set()})


def _other_songs(view):
    """このセットの exclude_other_sets がオンなら、ほかのセットのうちオンのセットの保存したリストの曲（items）の hash（候補から外す。
    別難易度も外れる）。保存したリストは作った時のまま残るので、先に作った（作り直した）セットが先に選ぶ"""
    if not view.values()['exclude_other_sets']:
        return set()
    out = set()
    for p in SETTINGS.presets():
        if p['id'] == view.preset or not p['values']['exclude_other_sets']:
            continue
        row = STORE.q1('SELECT payload FROM rec_lists WHERE preset=?', (p['id'],))
        lst = _saved_list(row['payload']) if row else None
        if lst:
            out |= {c['hash'] for c in lst['items'] if c.get('hash')}
    return out


def _assembled(view, keep_ids=None):
    """今のデータで組み立てる。keep_ids を渡すと、その曲だけのリストになるように組み立てる（入れ替えと同じ仕組みで入れ、
    ほかは外す。_seed_list）。ほかのセットと同じ曲を入れない時（_other_songs）は、その曲を外す集合を別に渡す（SESSION とは混ぜない）"""
    model, est = ENGINE.get(view)
    if model is None:
        return None, None
    with _session_lock:
        ss = _sess(view.preset)
        sess = {'removed': set(ss['removed']), 'added': set(ss['added'])}
    if keep_ids is not None:
        sess = {'removed': {c['leaderboard_id'] for c in est} - set(keep_ids), 'added': set(keep_ids)}
    return model, assemble(est, model, view, STORE, sess, other_songs=_other_songs(view))


def _make_list(view, keep_ids=None):
    """今のデータで組み立てたリスト（保存する形）。データが無ければ None"""
    model, res = _assembled(view, keep_ids)
    if res is None:
        return None
    return _as_list(model, view, res)


def _as_list(model, view, res):
    """組み立てた結果（assemble）→ 保存する形のリスト（_make_list と、交互に選ぶ時の _draft）"""
    keep = lambda c: {k: c.get(k) for k in _KEEP_KEYS}
    return {'items': [keep(c) for c in res['items']], 'runnersUp': [keep(c) for c in res['runnersUp']],
            'slots': res['slots'], 'short': res['short'], 'counts': res['counts'], 'summary': res['summary'],
            'filters': res['filters'], 'similarCount': len(model.similar),
            # 見込みの数字の確かさ（画面の「目安です」）: 似ている人が足りない・クリアの見込みが無い（全部クリアできる扱いの数字になる）
            'fewSimilar': model.few_similar, 'noClear': model.clear is None,
            'range': model.range, 'calc': _calc_info(model, view)}


def _list_settings(view):
    v = view.values()
    return json.dumps({k: v[k] for k in v if k not in _NOT_LIST_KEYS}, ensure_ascii=False, sort_keys=True)


def _same_list_settings(stored, conf):
    """保存したリストを作った時の設定（JSON）と今の設定（_list_settings）が同じか。版が変わって設定の項目が増えた・減った
    だけでは作り直さない: 今は無い項目（なくした項目）は数えず、保存した時に無かった項目は初期値だったとみなす
    （2026-10-18: 項目を足した・なくした版に替えるたびに、作った時のまま残すはずのリストが作り直されていた）"""
    if stored == conf:
        return True
    try:
        old, new = json.loads(stored), json.loads(conf)
    except (TypeError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    return all(old.get(k, SPEC[k]['default'] if k in SPEC else None) == v for k, v in new.items())


def _saved_list(payload):
    """保存したリストの JSON → リスト。読めない・形が違う（JSON としては読めても items などが無い）なら None"""
    try:
        lst = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(lst, dict) or not all(
            isinstance(lst.get(k), list) and all(isinstance(c, dict) and c.get('leaderboard_id') for c in lst[k])
            for k in ('items', 'runnersUp')):
        return None
    return lst


def _seed_list(view, live):
    """保存したリストがまだ無い時（この仕組みを足した版に替えた時など）に、そのセットで前回出力した曲のリストを作る（前回出した
    リストの理由を見直せるように。数値は今のデータで計算し直す）。出力が無い・今のリストと同じ曲なら None"""
    e = STORE.q1('SELECT e.run_id FROM exports e JOIN recommendation_runs r ON r.run_id=e.run_id WHERE r.preset=? '
                 'ORDER BY e.exported_at DESC, e.rowid DESC LIMIT 1', (view.preset,))
    if not e:
        return None
    ids = {r['leaderboard_id'] for r in STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1',
                                                (e['run_id'],))}
    if not ids or ids == {c['leaderboard_id'] for c in live['items']}:
        return None
    lst = _make_list(view, keep_ids=ids)
    if lst is None or not lst['items']:
        return None
    # ほかの曲は全部外して組み立てたので、次点・候補の数・フィルタの数は今のリストの物にする
    lst.update(runnersUp=[c for c in live['runnersUp'] if c['leaderboard_id'] not in ids], counts=live['counts'], filters=live['filters'])
    return lst


def _lists(view, rebuild=False, keep_order=False):
    """(保存したリスト, 今のデータで作ったリスト)。保存したリストは {made_at, data_at, list}。
    保存した物が無い・リストに効く設定が変わった・rebuild の時は、今の物を保存し直す（保存した物が無い時だけは前回の出力の曲で。
    _seed_list）。データが無ければ (None, None)。
    作り直した時の並び（items の順＝書き出す順）は基本の並び（order.default_order）。keep_order（画面の操作）の時は、保存して
    あったリストの手で並べた順を保ち、新しく入った曲だけを基本の並びの位置に入れる（order.merge_order。設定が変わっていれば基本の並び）"""
    live = _make_list(view)
    if live is None:
        return None, None
    conf = _list_settings(view)
    row = STORE.q1('SELECT made_at, data_at, settings, payload FROM rec_lists WHERE preset=?', (view.preset,))
    if row and not rebuild and _same_list_settings(row['settings'], conf):
        lst = _saved_list(row['payload'])
        if lst is not None:   # 壊れていれば作り直す
            return {'made_at': row['made_at'], 'data_at': row['data_at'], 'list': lst}, live
    lst = (_seed_list(view, live) if row is None and not rebuild else None) or live
    old = _saved_list(row['payload']) if keep_order and row and _same_list_settings(row['settings'], conf) else None
    items = order.merge_order([c['leaderboard_id'] for c in old['items']], lst['items']) if old else order.default_order(lst['items'])
    return _store_list(view, {**lst, 'items': items}), live


def _store_list(view, lst):
    """リスト（保存する形）をそのセットの保存したリストにする。返り値は {made_at, data_at, list}。並び（items の順）が書き出す順"""
    saved = {'made_at': int(time.time()), 'data_at': int(STORE.meta('last_run_at') or 0), 'list': lst}
    with STORE.tx():
        STORE.conn.execute('INSERT OR REPLACE INTO rec_lists(preset, made_at, data_at, settings, payload) VALUES(?,?,?,?,?)',
                           (view.preset, saved['made_at'], saved['data_at'], _list_settings(view),
                            json.dumps(lst, ensure_ascii=False)))
    return saved


def _drop_from_saved(lid, except_preset):
    """「今後出さない」にした曲を、ほかのセットの保存したリストからも抜く（全部のセットを書き込む時に入らないように）。
    ほかの曲はそのまま（代わりに入る曲は、そのセットを開いた時の「作り直す」の知らせで選んでもらう）"""
    with STORE.tx():
        for row in STORE.conn.execute('SELECT preset, payload FROM rec_lists WHERE preset<>?', (except_preset,)).fetchall():
            lst = _saved_list(row['payload'])
            if lst is None or not any(c['leaderboard_id'] == lid for c in lst['items'] + lst['runnersUp']):
                continue
            for k in ('items', 'runnersUp'):
                lst[k] = [c for c in lst[k] if c['leaderboard_id'] != lid]
            STORE.conn.execute('UPDATE rec_lists SET payload=? WHERE preset=?', (json.dumps(lst, ensure_ascii=False), row['preset']))


def _songs(ids):
    """譜面の ID → [{id, song, difficulty, mode}]（並びはそのまま。名前を出すため）"""
    if not ids:
        return []
    rows = STORE.q('SELECT leaderboard_id, song_name, difficulty, mode FROM leaderboards WHERE leaderboard_id IN (%s)'
                   % ','.join('?' * len(ids)), list(ids))
    by = {r['leaderboard_id']: r for r in rows}
    return [{'id': i, 'song': (by.get(i) or {}).get('song_name') or i, 'difficulty': (by.get(i) or {}).get('difficulty'),
             'mode': (by.get(i) or {}).get('mode')} for i in ids]


def _pending(saved, live):
    """今のデータで作り直すと変わる所（変わらなければ None）。in/out は入る・外れる曲、same は曲は同じで数値だけ変わる"""
    sig = lambda c: (c['leaderboard_id'], round(c['est_acc'] or 0, 4), round(c['gain'] or 0, 2), round(c.get('best_acc') or 0, 4))
    old, new = saved['list']['items'], live['items']
    if sorted(map(sig, old)) == sorted(map(sig, new)):
        return None
    o, n = [c['leaderboard_id'] for c in old], [c['leaderboard_id'] for c in new]
    return {'in': _songs([x for x in n if x not in o]), 'out': _songs([x for x in o if x not in n]), 'same': sorted(o) == sorted(n),
            'dataAt': int(STORE.meta('last_run_at') or 0)}


def _last_export(preset, items):
    """そのセットで最後に出力したリストとの違い。出した曲のうち今のリストに無い物（dropped）と、出した時刻・方法"""
    e = STORE.q1('SELECT e.run_id, e.exported_at, e.method, e.path FROM exports e JOIN recommendation_runs r ON r.run_id=e.run_id '
                 'WHERE r.preset=? ORDER BY e.exported_at DESC, e.rowid DESC LIMIT 1', (preset,))
    if not e:
        return None, set()
    ids = [r['leaderboard_id'] for r in STORE.q('SELECT leaderboard_id FROM recommendation_items WHERE run_id=? AND exported=1',
                                                (e['run_id'],))]
    cur = {c['leaderboard_id'] for c in items}
    app = (e['path'] or '').split(':')[0] if e['method'] == 'oneclick' else None   # ワンクリックの記録は「アプリ: ファイル名」
    return {'at': e['exported_at'], 'method': e['method'] or 'write', 'app': app, 'n': len(ids),
            'dropped': _songs([x for x in ids if x not in cur])}, set(ids)


def _replay_urls(items):
    """保存したリストの曲（プレイ済み）→ {スコアの ID: リプレイの URL}。リストを作った時に URL が無かった曲（その後の取得で入った）は、
    同じスコアの ID の今の行から補う（ID が同じなら同じプレイ。2026-10-17: 取得が終わる前に保存したリストで JD が出なかった）"""
    urls = {c['score_id']: c.get('replay_url') for c in items if c.get('kind') == 'played' and isinstance(c.get('score_id'), int)}
    miss = [sid for sid, url in urls.items() if not url]
    if miss:
        rows = STORE.q('SELECT score_id, replay_url FROM scores WHERE player_id=? AND replay_url IS NOT NULL AND score_id IN (%s)'
                       % ','.join('?' * len(miss)), (SETTINGS.values()['player_id'], *miss))
        urls.update({r['score_id']: r['replay_url'] for r in rows})
    return urls


def _screen_items(items, exported=None):
    """保存したリストの曲 → 画面に出す形。自己ベストの時の JD・RT（リプレイの頭から読んだ物）と、前回の出力に入っていたかを足す。
    jd_state: ok / wait（まだ読んでいない）/ nourl（リプレイの URL が次の取得の後に入る）/ none（読めなかった）/
    changed（リストを作った後に自己ベストが変わり、作った時のプレイのリプレイが分からない）"""
    info = replay.known(STORE, [c.get('score_id') for c in items])
    urls = _replay_urls(items)
    lids = [c['leaderboard_id'] for c in items if c.get('kind') == 'played']
    now_sid = {r['leaderboard_id']: r['score_id'] for r in STORE.q(
        'SELECT leaderboard_id, score_id FROM scores WHERE player_id=? AND leaderboard_id IN (%s)' % ','.join('?' * len(lids)),
        (SETTINGS.values()['player_id'], *lids))} if lids else {}
    out = []
    for c in items:
        x = _item(c)
        if c.get('kind') == 'played':
            r = info.get(c.get('score_id'))
            if r:
                x['jd'], x['jd_state'] = r['jd'], 'ok' if r['status'] == 'ok' else 'none'
            elif urls.get(c.get('score_id')):
                x['jd'], x['jd_state'] = None, 'wait'
            else:
                sid = now_sid.get(c['leaderboard_id'])
                x['jd'], x['jd_state'] = None, 'changed' if sid and c.get('score_id') and sid != c['score_id'] else 'nourl'
            x['rt'] = replay.rt_ms(x['jd'], c.get('njs'), c.get('best_mods'))
        if exported is not None:
            x['in_export'] = c['leaderboard_id'] in exported
        out.append(x)
    return out


def _rec_payload(view, rebuild=False, keep_order=False, **extra):
    """おすすめ画面に出す物。manualOrder は、一覧の並び（＝書き出す順）が基本の並びと違うか（「基本の並びに戻す」を押せるか）"""
    saved, live = _lists(view, rebuild, keep_order)
    if saved is None:
        return {'hasData': False, 'preset': view.preset}
    lst = saved['list']
    with _session_lock:
        removed = len(_sess(view.preset)['removed'])
    exp, exported = _last_export(view.preset, lst['items'])
    # ★の範囲を選ぶ参考（このセットのモードで最近クリアした曲の★。assemble.recent_stars）は、リストの中身ではなく次に値を選ぶ
    # 材料なので今のデータの物にする（保存したリストが前の版で作った物＝参考が無い時にも出るように）
    filters = {**(lst.get('filters') or {}), 'recentStars': (live.get('filters') or {}).get('recentStars')}
    return {'hasData': True, 'preset': view.preset, **{k: lst.get(k) for k in _LIST_KEYS}, 'filters': filters,
            'items': _screen_items(lst['items'], exported if exp else None), 'runnersUp': _screen_items(lst['runnersUp']),
            'madeAt': saved['made_at'], 'dataAt': saved['data_at'], 'lastExport': exp, 'pending': _pending(saved, live),
            'manualOrder': not order.is_default(lst['items']),
            'removed': removed, 'neverCount': STORE.q1("SELECT COUNT(*) AS n FROM user_marks WHERE mark='never'")['n'], **extra}


@api('recommend')
def _api_recommend(body):
    return _rec_payload(_view(body))


# ---- 組のセットを交互に選ぶ（組＝exclude_other_sets がオンのセットどうし。難しさ違いのセットを遊び比べる時） ----
# 組のセットどうしは同じ曲（別難易度も）を入れないので、先に作ったセットが先に良い曲を取ると、比べる時にそのセットが有利になる。
# そこで組全部を作る時は1曲ずつ交互に選ぶ（2026-10-18 に利用者が決めた）。順はスネーク（A, B, B, A, A, B, …。3つなら
# A, B, C, C, B, A, A, …）で、最初に選ぶ有利を打ち消す。最初に選ぶのは易しいセット。交互に選ぶのは、難しさ違いのセットを作った時と、
# 組のセットの「作り直す」で利用者が「組のセット全部を交互に選び直す」を選んだ時だけ。条件の変更での作り直しと「このセットだけ
# 作り直す」は今までどおり、ほかのセットの保存したリストの曲を外してそのセットだけを作る（_other_songs）

def _group_ids():
    """組のセットの id（選ぶ順）。易しい順（DIFFICULTY の並び）で、どの段階とも合わないセット（カスタム）は後ろ。同じ段階どうしは id の順"""
    levels = list(DIFFICULTY)

    def key(p):
        lv = difficulty_of(p['values'])
        return levels.index(lv) if lv in levels else len(levels), int(p['id'][1:])
    return [p['id'] for p in sorted((p for p in SETTINGS.presets() if p['values']['exclude_other_sets']), key=key)]


def _draft(views):
    """組のセット（views。選ぶ順）のリストを1曲ずつスネークの順に選んで作り、保存する。返り値は {セットの id: 保存したリスト}
    （データが無ければ何もせずに None）。
    1曲を選ぶ時は、そのセットがもう選んだ曲を入れ替えと同じ仕組み（SESSION の added）で必ず入れ、ほかのセットが選んだ曲の hash を
    other_songs で外して、そのセットの曲数のまま組み立て（assemble）、新しく入った曲のうち並び順の値が一番高い曲を取る。なので
    そのセットの条件（プレイ済みの比率の枠・挑戦枠・pass の上限・クリアの見込みの下限・同じ曲の別難易度・クールダウンなど）はそのまま
    効き、取り合う曲が無ければ、ふつうに作った時と同じリストになる。
    曲数を「選んだ数＋1」にして組み立てると、何曲目にプレイ済みと未プレイのどちらを取るかが比率の丸めで決まってしまう（比率 50% なら
    1曲目は必ずプレイ済み）ので、最後の曲数の枠のうちまだ空いている所から、一番値の高い曲を取る形にした。
    固定した曲・入れ替えで入れた曲は最初から入る（順番は使わない。ほかのセットはその曲も外す）。選べる曲が無くなったセットは飛ばす"""
    sets = []
    for v in views:
        model, est = ENGINE.get(v)
        if model is None:
            return None
        with _session_lock:
            ss = _sess(v.preset)
            sess = {'removed': set(ss['removed']), 'added': set(ss['added'])}
        first = assemble(est, model, v, STORE, sess)
        fixed = [c for c in first['items'] if c['pick'] != 'auto']   # 固定・入れ替えの曲（どのフィルタよりも優先して入る）
        sets.append({'view': v, 'model': model, 'est': est, 'sess': sess, 'size': v.values()['playlist_size'],
                     'rank': {c['leaderboard_id']: i for i, c in enumerate(est)},   # est は並び順の値の高い順
                     'have': {c['leaderboard_id'] for c in fixed}, 'hashes': {c['hash'] for c in fixed}, 'picks': [], 'done': False})

    def build(x):
        """x のもう選んだ曲を必ず入れ、ほかのセットの曲を外して組み立てる"""
        sess = {'removed': x['sess']['removed'], 'added': x['sess']['added'] | set(x['picks'])}
        others = set().union(*(y['hashes'] for y in sets if y is not x))
        return assemble(x['est'], x['model'], x['view'], STORE, sess, other_songs=others), others

    turn = 0
    while not all(x['done'] for x in sets):
        for x in sets if turn % 2 == 0 else sets[::-1]:   # スネーク: 0..n-1, n-1..0, 0..n-1, …
            if x['done']:
                continue
            new = [c for c in build(x)[0]['items'] if c['leaderboard_id'] not in x['have']] if len(x['have']) < x['size'] else []
            if not new:   # 曲数に届いた・選べる曲が無くなった
                x['done'] = True
                continue
            c = min(new, key=lambda c: x['rank'][c['leaderboard_id']])
            x['picks'].append(c['leaderboard_id'])
            x['have'].add(c['leaderboard_id'])
            x['hashes'].add(c['hash'])
        turn += 1

    out = {}
    for x in sets:
        v, est, model, picks = x['view'], x['est'], x['model'], set(x['picks'])
        res, others = build(x)
        # assemble は、固定した曲が片方の区分の枠を超えていると、もう片方の空いた枠（候補が足りなければ超えた方の区分の曲）を
        # 曲数を見ずに足す。選んだ曲は全部「固定」の扱いなので、足された曲（extra）が出ることがある。リストは選んだ曲だけにして
        # （ほかを全部外して組み立てる。まとめの数値もこれで）、足された曲は次点の先頭の候補に回す
        extra = [c for c in res['items'] if c['leaderboard_id'] not in x['have']]
        kept = assemble(est, model, v, STORE, {'removed': {c['leaderboard_id'] for c in est} - x['have'],
                                               'added': x['sess']['added'] | picks}, other_songs=others)
        runners = sorted([{k: c[k] for k in c if k != 'pick'} for c in extra] + res['runnersUp'],
                         key=lambda c: x['rank'][c['leaderboard_id']])[:RUNNERS_UP]
        # 候補の数・フィルタで外した数は、ほかのセットの曲を外してふつうに作った時の物（そのセットだけ作り直した時と同じ数え方）
        plain = assemble(est, model, v, STORE, x['sess'], other_songs=others)
        lst = _as_list(model, v, {**kept, 'runnersUp': runners, 'counts': plain['counts'],
                                  'items': [{**c, 'pick': 'auto'} if c['leaderboard_id'] in picks else c for c in kept['items']],
                                  'filters': {**plain['filters'], 'challenge': kept['filters']['challenge']}})
        out[v.preset] = _store_list(v, {**lst, 'items': order.default_order(lst['items'])})
    return out


@api('recommend/rebuild')
def _api_recommend_rebuild(body):
    """今のデータで作り直す（画面の「作り直す」）。group: true なら、このセットの組（exclude_other_sets がオンのセットどうし）を
    全部、交互に選び直す（_draft。ほかのセットの保存したリストも変わる）。組でない（このセットがオフ・ほかにオンのセットが無い）
    時の group: true は bad-request。返り値は開いているセットの recommend と同じ形（組全部の時は、選び直したセットの id の並び group も）"""
    view = _view(body)
    group = body.get('group')
    if group is not None and not isinstance(group, bool):
        raise ApiError('bad-request')
    if not group:
        return _rec_payload(view, rebuild=True)
    ids = _group_ids()
    if view.preset not in ids or len(ids) < 2:
        raise ApiError('bad-request')
    _draft([SETTINGS.view(p) for p in ids])
    return _rec_payload(view, group=ids)


@api('recommend/replays')
def _api_recommend_replays(body):
    """画面のリスト（と次点）のプレイ済みの曲の、自己ベストのリプレイの頭を読んで JD を出す（まだ読んでいない分だけ）"""
    view = _view(body)
    saved, _ = _lists(view)
    if saved is None:
        return {'hasData': False, 'preset': view.preset}
    urls = _replay_urls(saved['list']['items'] + saved['list']['runnersUp'])
    failed = replay.ensure(STORE, list(urls.items()), USER_AGENT)
    return _rec_payload(view, replayFailed=failed)


def _lb_exists(lid):
    return isinstance(lid, str) and STORE.q1('SELECT 1 AS x FROM leaderboards WHERE leaderboard_id=?', (lid,)) is not None


def open_browser(url):
    """ページを既定のブラウザで開く（テストでは差し替える）。開けなければ OSError。
    外を起動するのは core/launch.py を通す（exe 版の DLL の探し先を引き継がせない）"""
    launch.start(url)


@api('open/beatleader')
def _api_open_beatleader(body):
    """BeatLeader のページをブラウザで開く。replay＝自分のリプレイ、leaderboard＝その譜面のランキング。
    画面からは譜面の ID だけを受け取り、URL はここで組み立てる（任意のページを開く口にしない）"""
    lid, what = body.get('id'), body.get('what')
    if not _lb_exists(lid) or what not in ('replay', 'leaderboard'):
        raise ApiError('bad-request')
    if what == 'replay':
        r = STORE.q1('SELECT score_id FROM scores WHERE player_id=? AND leaderboard_id=?', (SETTINGS.values()['player_id'], lid))
        if not r or not r['score_id']:
            raise ApiError('no-replay')
        url = bl.replay_url(r['score_id'])
    else:
        url = bl.leaderboard_url(lid)
    try:
        open_browser(url)
    except OSError as e:
        raise ApiError('open-failed', detail=str(e))
    return {}


@api('recommend/action')
def _api_recommend_action(body):
    """固定（そのセットだけ）・今後出さない（全セット）・外す・入れ替える（そのセットだけ・アプリを閉じるまで）"""
    view = _view(body)
    pid = view.preset
    act, lid = body.get('action'), body.get('id')
    if act != 'reset' and not _lb_exists(lid):   # reset は曲を指さない（全部外して一覧が空の時は id が無い）
        raise ApiError('bad-request')
    now = int(time.time())
    with _session_lock:
        ss = _sess(pid)
        if act == 'pin':
            with STORE.tx():
                STORE.conn.execute("DELETE FROM user_marks WHERE leaderboard_id=? AND mark='never'", (lid,))
                STORE.conn.execute("INSERT OR REPLACE INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'pin', ?, ?)",
                                   (lid, pid, now))
            ss['removed'].discard(lid)
        elif act == 'never':    # どのセットにも出さない（全セットの固定も外す）
            with STORE.tx():
                STORE.conn.execute('DELETE FROM user_marks WHERE leaderboard_id=?', (lid,))
                STORE.conn.execute("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', ?)", (lid, now))
            for x in SESSION.values():
                x['removed'].discard(lid)
                x['added'].discard(lid)
            _drop_from_saved(lid, pid)
        elif act == 'unpin':
            with STORE.tx():
                STORE.conn.execute("DELETE FROM user_marks WHERE leaderboard_id=? AND mark='pin' AND preset=?", (lid, pid))
        elif act == 'unnever':
            with STORE.tx():
                STORE.conn.execute("DELETE FROM user_marks WHERE leaderboard_id=? AND mark='never'", (lid,))
        elif act in ('remove', 'swap'):   # 今回のリストから外す（固定も外す）／次点と入れ替える
            other = body.get('with')
            if act == 'swap' and not _lb_exists(other):
                raise ApiError('bad-request')
            with STORE.tx():
                STORE.conn.execute("DELETE FROM user_marks WHERE leaderboard_id=? AND mark='pin' AND preset=?", (lid, pid))
            ss['added'].discard(lid)
            ss['removed'].add(lid)
            if act == 'swap':
                ss['removed'].discard(other)
                ss['added'].add(other)
        elif act == 'reset':    # 外した・入れ替えた分を元に戻す
            ss['removed'].clear()
            ss['added'].clear()
        else:
            raise ApiError('bad-request')
    # 利用者の操作なので、今のデータで作り直す。手で並べた順は保ち、新しく入った曲だけを基本の並びの位置に入れる
    return _rec_payload(view, rebuild=True, keep_order=True)


@api('recommend/order')
def _api_recommend_order(body):
    """一覧の並び（＝書き出す順）を変える（見出しで並べ替え・ドラッグ・Alt+↑↓）。ids は保存したリストの曲の ID を並べた物で、
    曲の集合が保存したリストの items と同じでなければ bad-request。default: true なら基本の並びに戻す。
    作り直さない（保存したリストの items の順だけを書き換える。作った時刻・元にしたデータの時刻はそのまま）"""
    view = _view(body)
    saved, _ = _lists(view)
    if saved is None:
        return {'hasData': False, 'preset': view.preset}
    lst = saved['list']
    if body.get('default') is True:
        items = order.default_order(lst['items'])
    else:
        ids, by = body.get('ids'), {c['leaderboard_id']: c for c in lst['items']}
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or set(ids) != set(by):
            raise ApiError('bad-request')
        items = [by[i] for i in ids]
    with STORE.tx():
        STORE.conn.execute('UPDATE rec_lists SET payload=? WHERE preset=?',
                           (json.dumps({**lst, 'items': items}, ensure_ascii=False), view.preset))
    return _rec_payload(view)


# ---- 出力（Beat Saber に書き込む／BSManager などのワンクリックで入れる／ファイルに保存。どれも振り返りに数える） ----
def prepare_export(view):
    """出力する中身を作る。出すのは画面に出ている保存したリスト（_lists）。返り値 (model, リスト, 設定の値, .bplist の中身)。
    出す曲が無ければ ApiError"""
    model, _ = ENGINE.get(view)
    saved, _ = _lists(view)
    res = saved and saved['list']
    if not res or not res['items']:
        raise ApiError('nothing-to-export')
    s = view.values()
    sm = res['summary']
    desc = '%s作成 / 国内%s位 / 見込み+%.1fPP・国内%s位' % (
        time.strftime('%Y-%m-%d'), sm['rank'] or '—', sm['gain'], sm['newRank'] or '—')
    img, typ, _ = cover_of(view.preset)
    content = bplist.build(res['items'], s['playlist_title'], desc, img, image_type=typ, sync_id=s['output_filename'])
    return model, res, s, content


def _record(view, model, res, s, path, method):
    """出力の記録（次回の振り返りとクールダウンに使う）。★の下限を自動で決めたリスト（star_min が 0）は、その時に決めた下限
    {モード: ★}（assemble.auto_star_min）も star_min_auto として設定の記録に残す（振り返りの条件の行に出す。tools/play_report.py）"""
    auto = (res.get('filters') or {}).get('starMinAuto')
    if auto is not None:
        s = {**s, 'star_min_auto': auto}
    review.record_export(STORE, model, res['items'], res['runnersUp'], s, path, int(time.time()), preset=view.preset, method=method)


def _write(view):
    model, res, s, content = prepare_export(view)
    try:
        path, copy = bplist.write(view, DATA_DIR, content)
    except bplist.OutputError as e:
        raise ApiError(e.code, detail=e.detail)
    _record(view, model, res, s, path, 'write')
    return {'path': path, 'copy': copy, 'songs': len(content['songs']), 'items': len(res['items'])}


@api('export')
def _api_export(body):
    """Beat Saber のフォルダの Playlists に書き込む"""
    return _write(_view(body))


@api('export/all')
def _api_export_all(body):
    """全部のセットを書き込む。1つ失敗しても残りは続ける"""
    out = []
    for p in SETTINGS.presets(brief=True):
        r = {'preset': p['id'], 'name': p['name']}
        try:
            r.update(ok=True, **_write(SETTINGS.view(p['id'])))
        except ApiError as e:
            r.update(ok=False, error=e.code)
        out.append(r)
    return {'results': out}


@api('oneclick/info')
def _api_oneclick_info(body):
    """ワンクリック（bsplaylist://）を受けるアプリ（BSManager など）。登録が無ければ null"""
    return {'app': oneclick.handler_app()}


def _oneclick_app():
    """ワンクリックを受けるアプリの名前。無ければ ApiError"""
    app = oneclick.handler_app()
    if not app:
        raise ApiError('no-oneclick-app')
    if not SERVER['port']:
        raise ApiError('internal')
    return app


def _oneclick(view, app):
    """1つのセットのリンクを開いて app に入れてもらう。返り値 (応答, 合言葉)"""
    model, res, s, content = prepare_export(view)
    fname = s['output_filename']
    token = SHELF.put(bplist.dumps(content).encode('utf-8'), fname)
    try:
        oneclick.launch(oneclick.link(SERVER['port'], token, fname))
    except OSError as e:
        raise ApiError('launch-failed', detail=str(e))
    _record(view, model, res, s, '%s: %s' % (app, fname), 'oneclick')
    return {'app': app, 'songs': len(content['songs']), 'items': len(res['items'])}, token


@api('export/oneclick')
def _api_export_oneclick(body):
    """BSManager など（bsplaylist:// を受けるアプリ）を起動して入れてもらう。プレイリストはこのサーバーが一時的に配る"""
    view = _view(body)
    return _oneclick(view, _oneclick_app())[0]


ONECLICK_WAIT = 30   # まとめて渡す時に、アプリが前の分を読みに来るのを待つ最長の秒数（アプリが起動していなければ起動する時間も入る）


@api('export/oneclick/all')
def _api_export_oneclick_all(body):
    """全部のセットをワンクリックで入れてもらう。BSManager はリンクごとに窓を開いて並べて入れるが、起動しきる前に次の
    リンクを渡すと取りこぼすおそれがあるので、アプリが前の分を読みに来てから次を渡す（来なければ、その後は待たない）。
    1つ失敗しても残りは続ける"""
    app = _oneclick_app()
    out, last, wait = [], None, True
    for p in SETTINGS.presets(brief=True):
        r = {'preset': p['id'], 'name': p['name']}
        try:
            if last and wait:
                wait = SHELF.wait_fetched(last, ONECLICK_WAIT)
            res, last = _oneclick(SETTINGS.view(p['id']), app)
            r.update(ok=True, **res)
        except ApiError as e:
            r.update(ok=False, error=e.code)
        out.append(r)
    return {'app': app, 'results': out}


def export_for_file(preset):
    """ファイルに保存する中身 (文字列, ファイル名, 保存した後に呼ぶ関数(保存先))。app.py の「名前を付けて保存」から呼ぶ。
    だめなら ApiError（code を画面へ返す）"""
    view = _settings_call(SETTINGS.view, preset)
    model, res, s, content = prepare_export(view)
    return bplist.dumps(content), s['output_filename'], lambda path: _record(view, model, res, s, path, 'file')


@api('export/file')
def _api_export_file(body):
    """ファイルに保存（ブラウザで開いている時＝開発中の確認用。exe 版はネイティブの「名前を付けて保存」＝app.py）。
    中身を返し、画面がダウンロードさせる"""
    text, fname, done = export_for_file(_preset_arg(body))
    done('(browser download) ' + fname)
    return {'text': text, 'filename': fname}


# ---- 出力先（Beat Saber のフォルダ） ----
# 候補を探して Beat Saber.exe があるかを確かめるのは Python だけ。画面からは「何番目の候補にするか」だけを受け取る
# （画面の JavaScript から任意のパスを渡せないようにする。docs/spec.md の「プレイリスト出力」）
_BS = {'choices': []}
_bs_lock = threading.Lock()


def bs_offer(paths, source):
    """候補を覚えて、画面に出す形（id 付き）で返す。app.py のフォルダ選択で中に複数あった時にも使う"""
    with _bs_lock:
        _BS['choices'] = [{'path': p, 'source': source if isinstance(source, str) else source[i]} for i, p in enumerate(paths)]
        return [{'id': i, **c, **bsfind.describe(c['path'])} for i, c in enumerate(_BS['choices'])]


@api('bs/find')
def _api_bs_find(body):
    found = bsfind.find_installs()
    cands = bs_offer([f['path'] for f in found], [f['source'] for f in found])
    for c, f in zip(cands, found):
        c['last'] = bool(f.get('last'))   # BSManager で最後に起動した版
    cur = SETTINGS.get('beat_saber_dir')
    return {'current': cur, 'currentOk': bsfind.is_root(cur) if cur else False, 'candidates': cands}


@api('bs/use')
def _api_bs_use(body):
    i = body.get('id')
    with _bs_lock:
        ch = _BS['choices']
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(ch):
            raise ApiError('bad-request')
        path = ch[i]['path']
    if not bsfind.is_root(path):   # 選んでいる間に消えた等
        raise ApiError('not-beat-saber')
    SETTINGS.update({'beat_saber_dir': path}, trusted=True)
    return {'path': path}


# ---- アプリ内の更新（core/app_update.py。画面は web/js/update.js） ----
# 取得先の URL・差し替える物は app_update.py に固定。画面からは何も受け取らない（開くページも決まった名前だけ）
def _update_call(fn, *a):
    try:
        return fn(*a)
    except app_update.UpdateError as e:
        raise ApiError(e.code, detail=e.detail)


@api('update/check')
def _api_update_check(body):
    """skipped は「更新しない」を選んだ版より新しくない時（起動時には知らせない）"""
    r = _update_call(UPDATER.check)
    skip = app_update.vtuple(SETTINGS.values()['update_skip'])
    return {**r, 'skipped': bool(skip and app_update.vtuple(r['latest']) <= skip)}


@api('update/download')
def _api_update_download(body):
    return _update_call(UPDATER.start_download)


@api('update/progress')
def _api_update_progress(body):
    return UPDATER.progress()


@api('update/cancel')
def _api_update_cancel(body):
    return UPDATER.cancel()


@api('update/apply')
def _api_update_apply(body):
    """差し替え役（新しい版の exe）を起動する。この後、画面がアプリを閉じる（quit_app）"""
    return _update_call(UPDATER.apply, SETTINGS.values()['lang'])


@api('update/open')
def _api_update_open(body):
    """決まったページだけをブラウザで開く（画面からは名前だけを受け取る）。issues は不具合の報告の新規作成のページ（issue_url）"""
    pages = {'releases': lambda: app_update.RELEASES_URL, 'notes': lambda: app_update.NOTES_URL, 'repo': lambda: app_update.REPO_URL,
             'issues': issue_url}
    what = body.get('what')
    if not isinstance(what, str) or what not in pages:
        raise ApiError('bad-request')
    try:
        open_browser(pages[what]())
    except OSError as e:
        raise ApiError('open-failed', detail=str(e))
    return {}


# ---- 不具合の報告（設定の「このアプリについて」） ----
# 報告は GitHub の Issues に書いてもらう。新規作成のページを、版と Windows の版を入れた本文付きで開く（URL の引数）。
# 本文に個人の情報（プレイヤーの ID・名前・PC の中の場所）は入れない。ログ（logs/）は自動では送らず、利用者が見て添える
# （ログには BeatLeader に問い合わせた URL＝プレイヤー ID が入るため）。<!-- --> の中は書き方の案内で、GitHub の表示には出ない
ISSUES_URL = app_update.REPO_URL + '/issues/new'
ISSUE_TITLE = '[不具合] '
ISSUE_BODY = '''### 起きたこと
<!-- 出たメッセージがあれば、そのまま写してください -->


### その前にした操作
<!-- 例: プレイリストの画面で★の範囲を変えて「作り直す」を押した -->


### 期待したこと


### 環境（アプリが自動で入れました）
- blnext: {version}
- Windows: {windows}

<!-- 設定 → このアプリについて →「ログのフォルダを開く」で開くフォルダの blnext.log を、ここへドラッグして添えてもらえると、原因を調べやすくなります。
ログには BeatLeader に問い合わせた URL（あなたのプレイヤー ID）が入ります -->
'''


def windows_version():
    """報告に入れる Windows の版（例 'Windows 11 Professional 10.0.26300 AMD64'）。Windows 以外は OS の名前。読めなければ sys.platform"""
    try:
        if sys.platform != 'win32':
            return platform.platform()
        rel, ver = platform.win32_ver()[:2]
        return ' '.join(x for x in ('Windows', rel, platform.win32_edition(), ver, platform.machine()) if x)
    except Exception:
        return sys.platform


def issue_url():
    """不具合の報告の新規作成のページ（REPO の Issues）。タイトルと本文（書く欄の見出し・版・Windows の版）を入れておく"""
    version = '%s（%s）' % (APP_VERSION or '?', 'exe 版' if FROZEN else '開発用の起動')
    body = ISSUE_BODY.format(version=version, windows=windows_version())
    return ISSUES_URL + '?' + urlencode({'title': ISSUE_TITLE, 'body': body}, quote_via=quote)


@api('open/logs')
def _api_open_logs(body):
    """書き込み先の logs/（core/logs.py。exe 版は exe の隣）をエクスプローラーで開く。無ければ作る。
    画面からは何も受け取らない（決まった場所だけを開く）。外の起動は core/launch.py を通す"""
    d = logs.log_dir(DATA_DIR)
    try:
        os.makedirs(d, exist_ok=True)   # 同じ名前のファイルがあれば OSError（ファイルは開かない）
        launch.start(d)
    except OSError as e:
        raise ApiError('open-failed', detail=str(e))
    return {}


# ---- 似ている人 ----
@api('similar')
def _api_similar(body):
    model, _ = ENGINE.get(_view(body))   # 似ている人の数え方（人数・共通曲の数など）はセットの計算の設定による
    if model is None:
        return {'hasData': False}
    sim_ids = {p.player_id for p in model.similar}

    def share(st):
        tot = sum(x or 0 for x in st)
        return [((x or 0) / tot) if tot else None for x in st]
    rows = sorted(model.pool, key=lambda p: (p.player_id not in sim_ids, -(p.sim if p.sim is not None else -9)))
    return {'hasData': True, 'me': {'name': model.me.name, 'countryRank': model.me.country_rank, 'style': share(model.me.style),
                                    'scores': model.me.n_scores},
            'players': [{'id': p.player_id, 'name': p.name, 'countryRank': p.country_rank, 'pp': p.pp, 'sim': p.sim, 'corr': p.corr,
                         'overlap': p.overlap, 'styleSim': p.style_sim, 'style': share(p.style), 'similar': p.player_id in sim_ids}
                        for p in rows],
            # 画面の「比較対象 n 人」は実際に比べた人（表の行）の数。スコアが少なくて取らなかった人を入れない
            'poolSize': len(model.pool), 'fewScores': model.skipped_few_scores}


@api('similar/compare')
def _api_similar_compare(body):
    model, _ = ENGINE.get(_view(body))
    pid = body.get('id')
    p = next((x for x in (model.pool if model else []) if x.player_id == pid), None)
    if p is None:
        raise ApiError('bad-request')
    common = [k for k in p.acc if k in model.me.acc]
    lbs = {}
    if common:
        for r in STORE.q('SELECT leaderboard_id, hash, song_name, difficulty, mode, stars FROM leaderboards WHERE leaderboard_id IN (%s)'
                         % ','.join('?' * len(common)), common):
            lbs[r['leaderboard_id']] = r
    out = []
    for k in common:
        lb = lbs.get(k) or {}
        out.append({'id': k, 'hash': lb.get('hash'), 'song': lb.get('song_name'), 'difficulty': lb.get('difficulty'), 'stars': lb.get('stars'),
                    'myAcc': model.me.acc.get(k), 'theirAcc': p.acc.get(k), 'myRes': model.me.residuals.get(k),
                    'theirRes': p.residuals.get(k), 'theirMods': p.mods.get(k, '')})
    out.sort(key=lambda x: -(x['stars'] or 0))
    return {'name': p.name, 'maps': out}


class Handler(http.server.SimpleHTTPRequestHandler):
    # 接続を使い回す（HTTP/1.1 の keep-alive）。HTTP/1.0 だと要求のたびに接続を作って捨て、画面を開くたびに約30本の接続ができる。
    # 画面のテストで何百回も開くと Windows のソケットが足りなくなり、読み込みが ERR_NO_BUFFER_SPACE で失敗した（2026-10-16）。
    # 応答には必ず Content-Length を付けること（付けないと受け手が終わりを判断できない）。使われない接続は timeout 秒で閉じる
    protocol_version = 'HTTP/1.1'
    timeout = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def handle_one_request(self):
        # 使い回している接続を相手（画面・ブラウザ）が切るのはよくあること（閉じた・使わなくなった）。例外の表示を出さずに閉じる
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _access_ok(self, write=False):
        if _hostname(self.headers.get('Host')) not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'null' and _hostname(urlsplit(origin).netloc) not in ALLOWED_HOSTS:
            return False
        if write and self.headers.get('X-BLN-Request') != '1':
            return False
        if write or not _is_public(self.path):
            given = (self.headers.get('X-BLN-Token') or '').encode('utf-8', 'replace')
            return hmac.compare_digest(given, SESSION_TOKEN.encode('ascii'))
        return True

    def _deny(self):
        self._drain()   # 拒否する時も本体を読み捨ててから応答する（_drain の説明）
        self.send_error(403, 'Forbidden (local server: this host/origin is not allowed, or the access key is missing.'
                             ' Open the URL shown when the server started.)')

    def translate_path(self, path):
        parts = _url_parts(path)
        # Windows では '\' やドライブ名（'C:'）もパスの区切りとして効くため、1要素でもそれを含めば配信しない（NLM のセキュリティ点検 2026-09-28）
        if any(p == '..' or '\\' in p or ':' in p or os.path.splitdrive(p)[0] for p in parts):
            return os.path.join(WEB_DIR, '__forbidden__')   # 存在しないパス＝404
        return os.path.join(WEB_DIR, *parts) if parts else WEB_DIR

    def list_directory(self, path):   # フォルダの一覧は見せない
        self.send_error(404, 'File not found')
        return None

    def do_HEAD(self):
        if not self._access_ok():
            return self._deny()
        return super().do_HEAD()

    def do_GET(self):
        parts = _url_parts(self.path)
        if len(parts) == 3 and parts[0] == 'oneclick':
            return self._oneclick(parts[1], parts[2])
        if len(parts) == 2 and parts[0] == 'cover':
            return self._song_cover(parts[1])
        if not self._access_ok():
            return self._deny()
        if self.path.split('?', 1)[0] in ('', '/'):
            self.path = '/index.html'
        return super().do_GET()

    def _oneclick(self, token, filename):
        """ワンクリックで入れるプレイリスト（core/output/oneclick.py）。読みに来るのは BSManager などのアプリで、合言葉の
        ヘッダーは付けられない。代わりに URL の中の推測できない合言葉（数分で消える）で守る。Host の確かめはほかと同じ"""
        if _hostname(self.headers.get('Host')) not in ALLOWED_HOSTS:
            return self._deny()
        body = SHELF.get(token, filename)
        if body is None:
            return self.send_error(404, 'File not found')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="%s"' % filename)   # 英数字と - _ . だけ（設定の決まり）
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _song_cover(self, name):
        """曲のカバー画像（core/fetch/cover.py）。<img> は合言葉のヘッダーを付けられないので合言葉なしで返す（中身は BeatSaver の
        公開の画像か代わりの絵だけ。取り先は CDN だけで、画面からはハッシュしか受け取らない）。Host の確かめはほかと同じ。
        ほかのサイトのページからの読み込み（Sec-Fetch-Site が same-origin・none 以外）は断る（アプリに CDN へ取りに行かせない）。
        ハッシュごとに中身は変わらないので、ブラウザに長く覚えさせる。代わりの絵は覚えさせない（後で取れたら出るように）"""
        if (_hostname(self.headers.get('Host')) not in ALLOWED_HOSTS
                or (self.headers.get('Sec-Fetch-Site') or 'same-origin') not in ('same-origin', 'none')):
            return self._deny()
        h = song_cover.parse_name(name)
        if not h:
            return self.send_error(404, 'File not found')
        got = SONG_COVERS.get(h)
        if got:
            body, typ = got
            self._cache_control = 'private, max-age=31536000, immutable'
        else:
            body, typ = _no_cover(), 'image/svg+xml'
        self.send_response(200)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        cache = self.__dict__.pop('_cache_control', None)   # 曲のカバー画像だけ長く覚えさせる（_song_cover）
        if cache:
            self.send_header('Cache-Control', cache)
        else:
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, *a):   # 静かに
        pass

    _DRAIN_MAX = 16 * 1024 * 1024
    _BODY_MAX = 4 * 1024 * 1024   # API の本体の上限（JSON の設定・操作だけなので小さい）

    def _content_length(self):
        if self.command != 'POST':
            return 0
        try:
            return max(0, int(self.headers.get('Content-Length', 0) or 0))
        except ValueError:
            return 0

    def _raw_body(self):
        # POST の本体は1回だけ読む
        if getattr(self, '_raw', None) is None:
            n = self._content_length()
            if n > self._BODY_MAX:
                raise ApiError('too-large')
            self._raw = self.rfile.read(n) if n > 0 else b''
        return self._raw

    def _drain(self):
        # 応答する前に、まだ読んでいない本体を読み捨てる。読まずに応答して閉じると、Windows では残ったデータのせいで
        # 接続がリセットされ、応答が届かずに通信エラーになることがある（NLM 2026-10-05）。拒否する要求に大きなメモリを
        # 使わせないよう、保存せずに少しずつ読み、上限を超える本体は諦める
        # 読みきれなかった時は、この応答で接続を閉じる（残りを次の要求として読んでしまわないように。HTTP/1.1 で接続を使い回すため）
        if getattr(self, '_raw', None) is not None:
            return
        n, self._raw = self._content_length(), b''
        if n <= 0:
            return
        if n > self._DRAIN_MAX:
            self.close_connection = True
            return
        try:
            self.connection.settimeout(2)
            while n > 0:
                d = self.rfile.read(min(n, 65536))
                if not d:
                    break
                n -= len(d)
        except OSError:
            pass
        if n > 0:
            self.close_connection = True

    def _reply(self, code, obj):
        self._drain()
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._raw = None
        if not self._access_ok(write=True):
            return self._deny()
        path = self.path.split('?', 1)[0]
        if not path.startswith('/__api/'):
            return self._reply(404, {'ok': False, 'error': 'unknown-endpoint'})
        fn = API.get(path[len('/__api/'):])
        if fn is None:
            return self._reply(404, {'ok': False, 'error': 'unknown-endpoint'})
        try:
            body = json.loads(self._raw_body() or b'{}')
            if not isinstance(body, dict):
                raise ApiError('bad-request')
            res = fn(body)
            return self._reply(200, {'ok': True, **(res or {})})
        except ApiError as e:
            return self._reply(200, {'ok': False, 'error': e.code, **e.extra})
        except ValueError:
            return self._reply(200, {'ok': False, 'error': 'bad-request'})
        except Exception as e:   # 想定外の失敗も画面へ返す（無言で止めない）
            return self._reply(500, {'ok': False, 'error': 'internal', 'detail': '%s: %s' % (type(e).__name__, e)})


_NO_COVER = {}


def _no_cover():
    """代わりの絵（web/icons/no-cover.svg）の中身。1回だけ読む"""
    if 'body' not in _NO_COVER:
        with open(NO_COVER, 'rb') as f:
            _NO_COVER['body'] = f.read()
    return _NO_COVER['body']


def on_closing():
    """ウィンドウを閉じる時に app.py が呼ぶ（UI スレッド。待たずにすぐ返すこと）。取得の途中なら止めるよう頼むだけ
    （取得済みの分は1人分・1段階ごとに保存済み）"""
    REFRESHER.cancel()


def make_server(port=8139, bind='127.0.0.1'):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    httpd = http.server.ThreadingHTTPServer((bind, port), Handler)
    SERVER['port'] = httpd.server_address[1]
    return httpd


if __name__ == '__main__':
    port = next((int(a.split('=', 1)[1]) for a in sys.argv[1:] if a.startswith('--port=')), 8139)
    httpd = make_server(port=port)
    url = access_url(port)
    # 表示は英語（ASCII）だけ: 日本語は、日本語を表せない文字コードの環境で出力をファイルへ回すと変換に失敗して起動前に落ちる
    print('Local server is running. Open this URL in Chrome / Edge'
          ' (it contains an access key that changes every time the server starts):', flush=True)
    print('  ' + url, flush=True)
    print('Press Ctrl+C to stop.', flush=True)
    if '--no-browser' not in sys.argv:
        try:
            launch.start(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
