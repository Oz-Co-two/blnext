# .bplist の出力（docs/spec.md の「プレイリスト出力」）。
#  - 書き込み先は設定した Beat Saber のフォルダ（ネイティブのダイアログで選んだ物）の中の Playlists/ だけ。ファイル名は英数字の固定名
#  - 毎回同じファイルを上書きする＝ゲーム内には常に「次回おすすめ」が1つだけ。一時ファイルに書いてから置き換える
#  - 同じ内容の控えを exe の隣の exports/ に日時付きで残す
#  - PlaylistManager はゲームを起動したままファイルを書き換えても自動では読み直さない（docs/investigation.md）。
#    ゲーム内の「Refresh Playlists」を押すと読み直す
#  - customData.syncURL に「どのセットのリストか」の印を入れる。BSManager のワンクリック（oneclick.py）は、同じ syncURL の
#    プレイリストが Playlists にあればそれを上書きする（無いと毎回「名前 (1).bplist」のような別のファイルを作る）。
#    PlaylistManager は syncURL があるとゲーム内に「Sync」ボタンを出し、押すとその URL を読みに行く（読めなければエラーの
#    小窓が出るだけ）。決まった URL では配れない（アプリのポートは起動ごとに変わる）ので、http でない印にしている
import base64, json, os, re, time

from ..fsutil import write_atomic
from ..settings import check


class OutputError(Exception):
    def __init__(self, code, detail=''):
        super().__init__(code)
        self.code, self.detail = code, detail


def _diff_name(name):
    """難易度名の先頭を小文字に（BeatLeader が出すプレイリストと同じ。例 ExpertPlus → expertPlus）"""
    return (name[:1].lower() + name[1:]) if name else name


SYNC_PREFIX = 'blnext://playlist/'


def build(items, title, description, image=None, author='blnext', image_type='image/png', sync_id=None):
    """選んだ曲（assemble の items）から .bplist の中身（dict）を作る。同じ曲の別難易度は1曲の difficulties にまとめる。
    image は画像のバイト列（image_type は 'image/png' か 'image/jpeg'）、sync_id は syncURL の印（出力ファイル名）"""
    songs, by_hash = [], {}
    for c in items:
        h = (c.get('hash') or '').lower()
        if not re.fullmatch(r'[0-9a-f]{40}', h):
            continue
        d = {'characteristic': c.get('mode') or 'Standard', 'name': _diff_name(c.get('difficulty') or '')}
        if h in by_hash:
            if d not in by_hash[h]['difficulties']:
                by_hash[h]['difficulties'].append(d)
            continue
        song = {'hash': h, 'songName': c.get('song') or '', 'levelAuthorName': c.get('mapper') or '', 'difficulties': [d]}
        by_hash[h] = song
        songs.append(song)
    out = {'playlistTitle': title, 'playlistAuthor': author, 'playlistDescription': description}
    if image:
        out['image'] = 'data:%s;base64,%s' % (image_type, base64.b64encode(image).decode('ascii'))
    if sync_id:
        out['customData'] = {'syncURL': SYNC_PREFIX + sync_id}
    out['songs'] = songs
    return out


def dumps(content):
    """ファイルに書く文字列（書き込み・ワンクリック・ファイルに保存で同じ物）"""
    return json.dumps(content, ensure_ascii=False, indent=2)


def target_path(settings):
    """書き込み先（Beat Saber のフォルダ/Playlists/<出力ファイル名>）。settings はセットから見た設定。だめなら OutputError"""
    s = settings.values()
    bs = s['beat_saber_dir']
    if not bs:
        raise OutputError('no-beat-saber-dir')
    if not os.path.isdir(bs):
        raise OutputError('beat-saber-dir-missing', bs)
    name = check('output_filename', s['output_filename'])   # 英数字と - _ だけ＋.bplist（設定の決まり）
    pl = os.path.join(bs, 'Playlists')
    return pl, os.path.join(pl, name)


def write(settings, data_dir, content):
    """Playlists/ へ書き、控えを exports/ に残す。返り値 (書いたパス, 控えのパス)"""
    pl, path = target_path(settings)
    text = dumps(content)
    try:
        os.makedirs(pl, exist_ok=True)
        write_atomic(path, text)
    except OSError as e:
        raise OutputError('write-failed', str(e))
    copy = None
    try:
        ex = os.path.join(data_dir, 'exports')
        os.makedirs(ex, exist_ok=True)
        copy = os.path.join(ex, time.strftime('%Y%m%d-%H%M%S-') + os.path.basename(path))
        write_atomic(copy, text)
    except OSError:
        copy = None   # 控えが書けなくても出力は済んでいる
    return path, copy
