# 設定（config/settings.json）。docs/spec.md の「設定項目一覧」
#  - キーが無い＝初期値で動く。ファイルには利用者が変えた項目だけが残る（初期値に戻す＝キーを消す）
#  - 読めない値・範囲外の値も初期値として扱う（手で書き換えて壊しても起動できるように）
#  - 知らないキー（新しい版で足した項目など）は消さずに残す。なくした項目（REMOVED_KEYS）は無視し、書く時に消す
#  - 書き終えてから置き換える（fsutil.write_atomic）
#  - フォルダ・ファイルの場所（DIALOG_ONLY）は画面（JS）からは変えられない。app.py のネイティブのダイアログで選んだ物だけ
#
# セット（設定のパターン）: リストの条件・計算の数値・プレイリスト名などは「セット」ごとに持ち、セットごとにおすすめと
# プレイリストを作る（例: スタンダード用・ワンセイバー用）。プレイヤー・比較範囲・出力先のフォルダなどは全セット共通。
#   ファイルの形: {共通の項目…, "presets": [{"id": "p1", "name": "スタンダード", "values": {セットの項目…}, "cover": "p1.png"}, …]}
#   セットの項目が直下にある昔の形は、最初のセット（p1）として読み、次に書く時に presets の中へ移す
#   presets の並びが画面のタブの並び（利用者が並べ替えられる。reorder_presets）。最初のセットが p1 とは限らない
import json, math, os, re, shutil, threading
from .fsutil import write_atomic


class SettingsError(Exception):
    def __init__(self, code, key=''):
        super().__init__(f'{code}: {key}')
        self.code, self.key = code, key


MODES = ('Standard', 'OneSaber', 'NoArrows', '90Degree', '360Degree', 'Lawless', 'Legacy')

# kind: bool / int / float / str / choice / choices（複数選択）/ path
SPEC = {
    # 基本
    'player_id':            {'default': '', 'kind': 'str', 'pattern': r'[0-9]{1,24}', 'empty': True},
    'country':              {'default': 'jp', 'kind': 'str', 'pattern': r'[a-z]{2}'},
    'target_country_rank':  {'default': 500, 'kind': 'int', 'min': 1, 'max': 100000},
    'neighbor_display':     {'default': 10, 'kind': 'int', 'min': 1, 'max': 50},
    # 比較範囲
    'pool_above':           {'default': 100, 'kind': 'int', 'min': 0, 'max': 300},
    'pool_below':           {'default': 100, 'kind': 'int', 'min': 0, 'max': 300},
    # 上の範囲の中から、ランクのスコアの多い順に何人を取って比べるか（model.pick_pool。2026-11-06 に 200人全員から変えた。根拠は 作者の調べ「比較対象の人数」）
    'pool_max':             {'default': 50, 'kind': 'int', 'min': 1, 'max': 600},
    # リスト
    'playlist_size':        {'default': 15, 'kind': 'int', 'min': 1, 'max': 50},
    'played_ratio':         {'default': 70, 'kind': 'int', 'min': 0, 'max': 100},   # %
    'max_map_age_years':    {'default': 3, 'kind': 'float', 'min': 0, 'max': 20},  # 0で無効
    'age_filter_unplayed':  {'default': True, 'kind': 'bool'},
    'age_filter_played':    {'default': False, 'kind': 'bool'},
    # ★の範囲（譜面の素の★。プレイ済みにも未プレイにもかける。0 で効かない）。セットごとに数字で決める（2026-10-25 に「プレイ済みの
    # 最高★＋余裕」の unplayed_star_margin から替えた。最高★を全モードの曲で数えていた・たまたまクリアできた曲が上限を引き上げていたため）。
    # 両方が 0 より大きい時は 下限 ≦ 上限 でないと保存しない（Settings.update）。
    # 下限の 0（初期値）は自動（assemble.auto_star_min。2026-11-08 に利用者が「上限は自分で決め、下限は点の入りやすい所を自動で。基本は自動」と決めた。
    # それまでの 0 は「下限なし」）
    'star_min':             {'default': 0, 'kind': 'float', 'min': 0, 'max': 15},
    'star_max':             {'default': 0, 'kind': 'float', 'min': 0, 'max': 15},
    'modes':                {'default': ['Standard'], 'kind': 'choices', 'choices': MODES},
    'one_diff_per_song':    {'default': True, 'kind': 'bool'},
    # クールダウン（assemble.cooldown_ids。2026-11-01 に利用者と決めた。遊んだ日で数える・伸びた／伸びないは問わない）。
    # 再プレイ: 遊んだ日から replay_gap_days 日は出さない・3週間に replay_streak_days 日遊んだら replay_rest_weeks 週休み。
    # 初プレイ（まだクリアしていない曲）: 3週間にクリアできなかった日が unplayed_fail_days 日になったら unplayed_rest_weeks 週休み。0 で効かない
    'replay_gap_days':      {'default': 3, 'kind': 'int', 'min': 0, 'max': 30},
    'replay_streak_days':   {'default': 3, 'kind': 'int', 'min': 0, 'max': 10},
    'replay_rest_weeks':    {'default': 4, 'kind': 'int', 'min': 0, 'max': 26},
    'unplayed_fail_days':   {'default': 3, 'kind': 'int', 'min': 0, 'max': 10},
    'unplayed_rest_weeks':  {'default': 4, 'kind': 'int', 'min': 0, 'max': 26},
    # 範囲の外の曲（pass か1秒のノーツが、自分がクリアしてきた曲の95%点を超える曲）を1リストに何曲まで入れるか（挑戦枠）
    'challenge_max':        {'default': 1, 'kind': 'int', 'min': 0, 'max': 50},
    # 未プレイの曲の pass の上限。自分がクリアしてきた曲（ランクのスコア・NF を除く）の pass の分布で決める（none＝上限なし・
    # p95＝95%点・p75＝75%点）。難しさのスライダー（DIFFICULTY）が挑戦枠と一緒に変える。2026-10-18 の調査（作者の調べ「精度の推定とpass」）
    'unplayed_pass_cap':    {'default': 'none', 'kind': 'choice', 'choices': ('none', 'p95', 'p75')},
    # クリアの見込み（clear.py。その日のうちにクリアできる見込み）がこの % より低い曲は入れない（0 で絞らない。固定した曲は入れる）。
    # 着実の段階で使う（2026-10-18: pass は低いが★8.8 で失敗が続いた曲が、見込み 51% のまま着実のリストに入っていた）
    'min_clear':            {'default': 0, 'kind': 'int', 'min': 0, 'max': 100},
    # 精度の上限（recommend.py・model.AccCeiling。近い★のほかの曲の自分の一番）に足す余裕（%）。難しさの段階で変える（着実 0・標準 0.5・
    # チャレンジング 1.0。2026-11-01 に利用者が「上限は着実・標準・チャレンジングのリストの作り方の基準の調整にしてほしい」）。
    # 過去の再プレイの更新 39 回で、上限ちょうどだと大きく稼いだ更新が1回出なくなり（前の自己ベストが上限より上だった）、＋0.5% なら止めた更新は 0 回
    'acc_cap_margin':       {'default': 0.5, 'kind': 'float', 'min': 0, 'max': 5},
    # オンのセットどうしでは同じ曲（別難易度も）を入れない（難しさ違いのセットを並べて比べる時に）
    'exclude_other_sets':   {'default': False, 'kind': 'bool'},
    # 計算
    'min_scores':           {'default': 30, 'kind': 'int', 'min': 5, 'max': 500},
    'self_half_life_days':  {'default': 180, 'kind': 'int', 'min': 7, 'max': 3650},
    'min_overlap':          {'default': 30, 'kind': 'int', 'min': 5, 'max': 500},
    'overlap_shrink':       {'default': 20, 'kind': 'float', 'min': 0, 'max': 200},
    'top_similar':          {'default': 20, 'kind': 'int', 'min': 1, 'max': 200},
    'min_similar_on_map':   {'default': 3, 'kind': 'int', 'min': 1, 'max': 50},
    'self_weight':          {'default': 2, 'kind': 'float', 'min': 0, 'max': 20},
    # 似ている人の上振れを控えめにする強さ（推定の式の分母に足す重み。0 で仕様書の式そのまま）。
    # 2026-10 に自分のプレイ済みの曲で確かめた時、0.6〜1.0 で精度の当たりが一番よかった（docs/investigation.md）
    'similar_prior':        {'default': 0.8, 'kind': 'float', 'min': 0, 'max': 10},
    'fc_gap_factor':        {'default': 0.5, 'kind': 'float', 'min': 0, 'max': 1},
    'stall_play_count':     {'default': 10, 'kind': 'int', 'min': 1, 'max': 1000},
    'stall_days':           {'default': 60, 'kind': 'int', 'min': 1, 'max': 3650},
    # クリアできる見込みを並び順の値にどれだけ効かせるか（1 で見込みをそのまま掛ける・0 で効かせない）
    'clear_factor':         {'default': 1.0, 'kind': 'float', 'min': 0, 'max': 1},
    # 自分の★の範囲より上の推定を、比較対象の人の曲線の下がり方の何倍で下げるか（0 で下げない）
    'beyond_star_factor':   {'default': 1.0, 'kind': 'float', 'min': 0, 'max': 3},
    # 未プレイの曲（自分の結果が無い曲）の推定精度を、pass が基準を超える分 1 あたり何 % 下げるか（★の範囲の外では2倍）。
    # 1 は作者の記録で求めた値（2026-10-18 の調査の R2）で、ほかの人には大きすぎたので初期値は 0.5（2026-11-06。作者の調べ「ほかの人のデータ」。
    # 比較対象の 220人の自己ベストで、1.0 は MAE +0.03・0.5 は +0.004。ほかの人の初クリアでは確かめられない。作者のセットには 1 を書いて変えていない）
    'unplayed_pass_drop':   {'default': 0.5, 'kind': 'float', 'min': 0, 'max': 5},
    # 未プレイの曲（自分の結果が無い曲）の推定精度を、tech が基準（自分がクリアしてきた曲の tech の中央）を超える分 1 あたり何 % 下げるか。
    # 1 は作者の記録で求めた値（2026-10-25 の調査の R5）で、ほかの人には大きすぎたので初期値は 0.5（2026-11-06。作者の調べ「ほかの人のデータ」。
    # 比較対象の 220人の自己ベストで、1.0 は MAE +0.16・89% の人が悪化、0.5 は +0.05。ただし自己ベストには初回の慣れの分が見えないので 0 にはしない。
    # 人ごとに求める形は改善が MAE 0.01 以下だったので入れない。作者のセットには 1 を書いて変えていない）
    'unplayed_tech_drop':   {'default': 0.5, 'kind': 'float', 'min': 0, 'max': 5},
    # 出力
    'beat_saber_dir':       {'default': '', 'kind': 'path'},
    'playerdata_path':      {'default': '', 'kind': 'path'},
    'output_filename':      {'default': 'blnext-next.bplist', 'kind': 'str', 'pattern': r'[A-Za-z0-9_-]{1,60}\.bplist'},
    'playlist_title':       {'default': '次回おすすめ', 'kind': 'str', 'maxlen': 60},
    # 通信
    'auto_refresh_hours':   {'default': 12, 'kind': 'float', 'min': 0, 'max': 168},   # 前回の取得からこの時間たっていれば起動時に取る。0で毎回
    'fetch_song_details':   {'default': True, 'kind': 'bool'},
    'update_check':         {'default': True, 'kind': 'bool'},
    # BeatLeader の統計が非公開の時に、プレイリストを作る・出すたびに警告し、ホームにも知らせるか（web/js/stats.js。止めるのはこの設定だけ。知らせるだけで、働きは変わらない）
    'stats_notice':         {'default': True, 'kind': 'bool'},
    # 新しい版の知らせで「更新しない」を選んだ版。それより新しい版が出るまで起動時に知らせない（設定画面には出さない。core/app_update.py）
    'update_skip':          {'default': '', 'kind': 'str', 'pattern': r'v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}', 'empty': True},
    # 画面
    'lang':                 {'default': 'ja', 'kind': 'choice', 'choices': ('ja', 'en')},
    'ui_scale':             {'default': 100, 'kind': 'choice', 'choices': (100, 115, 130, 145)},   # 文字の大きさ（%。画面全体を拡大する）
    'active_preset':        {'default': 'p1', 'kind': 'str', 'pattern': r'p[0-9]{1,4}'},   # 画面で開いているセット（設定画面には出さない）
}
DIALOG_ONLY = {'beat_saber_dir', 'playerdata_path'}

# セットごとの項目（残りは全セット共通）
PRESET_KEYS = ('playlist_size', 'played_ratio', 'max_map_age_years', 'age_filter_unplayed', 'age_filter_played',
               'star_min', 'star_max', 'modes', 'one_diff_per_song', 'replay_gap_days', 'replay_streak_days', 'replay_rest_weeks',
               'unplayed_fail_days', 'unplayed_rest_weeks', 'challenge_max', 'unplayed_pass_cap',
               'min_clear', 'acc_cap_margin', 'exclude_other_sets',
               'min_scores', 'self_half_life_days', 'min_overlap', 'overlap_shrink', 'top_similar', 'min_similar_on_map',
               'self_weight', 'similar_prior', 'fc_gap_factor', 'stall_play_count', 'stall_days', 'clear_factor', 'beyond_star_factor',
               'unplayed_pass_drop', 'unplayed_tech_drop', 'output_filename', 'playlist_title')
COMMON_KEYS = tuple(k for k in SPEC if k not in PRESET_KEYS)
# なくした項目（前の版の設定ファイルに残っていることがある）。セットの値の中の物は読む時に落ち（PRESET_KEYS に無いため）、
# 昔の形で直下にある物は次に書く時に消す。order（プレイリスト内の並び）は 2026-10-18 に基本の並び（core/estimate/order.py）にした。
# unplayed_star_margin（未プレイ曲の★の上限＝プレイ済みの最高★＋この値）は 2026-10-25 に★の範囲（star_min・star_max）にした。
# cooldown_runs（伸びなかった曲を直近何回分の出力について外すか）は 2026-11-01 に遊んだ日で数えるクールダウン（replay_*・unplayed_fail_days・
# unplayed_rest_weeks）にした
REMOVED_KEYS = ('order', 'unplayed_star_margin', 'cooldown_runs')

# 難しさの段階（おすすめの画面の条件・設定画面のスライダー。左から 着実・標準・チャレンジング）。選ぶと、セットの項目をまとめて
# 書き換える。段階は設定として持たず、今の値から決める（difficulty_of。どの段階とも合わなければ「カスタム」＝利用者が個別に変えた）。
# 2026-10-18 の調査（作者の調べ「精度の推定とpass」 の「難しさを3段階で選ぶプリセット」）: 着実（未プレイの pass を自分の
# 75%点まで・挑戦枠 0）は、その日の条件で組むと pass の平均 2.94・クリアの見込み 90%。代わりに増える総PP の期待値は標準の約4割
DIFFICULTY = {
    'steady':    {'unplayed_pass_cap': 'p75', 'challenge_max': 0, 'min_clear': 80, 'acc_cap_margin': 0.0},
    'standard':  {'unplayed_pass_cap': 'none', 'challenge_max': 1, 'min_clear': 0, 'acc_cap_margin': 0.5},
    'challenge': {'unplayed_pass_cap': 'none', 'challenge_max': 2, 'min_clear': 0, 'acc_cap_margin': 1.0},
}
# 難しさ違いのセットを一度に作る時（Settings.add_difficulty_presets）の、セットの名前（並びは作る順）。
# ファイル名は blnext-<段階>.bplist、プレイリスト名は「次回 <名前>」。作る段階と曲数は画面で選ぶ（初期値は利用者が決めた
# 2026-10-18: 着実とチャレンジングの2つ・7曲ずつ。両端を比べる）
DIFFICULTY_SETS = {'steady': '着実', 'standard': '標準', 'challenge': 'チャレンジング'}
DIFFICULTY_SET_DEFAULT = ('steady', 'challenge')
DIFFICULTY_SET_SIZE = 7
# 初回の準備（web/js/setup.js）で作るセットの曲数（着実・標準・チャレンジングの3つ。2026-11-05 に利用者が決めた）
INITIAL_SET_SIZE = 10


def difficulty_of(values):
    """セットの値が合う難しさの段階（DIFFICULTY のキー）。どれとも合わなければ None（カスタム）"""
    return next((k for k, d in DIFFICULTY.items() if all(values.get(x) == v for x, v in d.items())), None)


def initial_target(rank):
    """初回の準備で入れる目標の国内順位: 今の順位より上で一番近い切りのよい順位（1・2・3・5 × 10 の累乗。677 位なら 500 位）。
    順位が無ければ None（初期値のまま）、1位なら 1"""
    if not rank or rank < 1:
        return None
    best, base = 1, 1
    while base < rank:
        for m in (1, 2, 3, 5):
            if base * m < rank:
                best = base * m
        base *= 10
    return best


def _free_filename(stem, taken):
    """stem.bplist。taken（小文字のファイル名の集合）に入っていれば stem-2.bplist, stem-3.bplist …"""
    name, n = stem + '.bplist', 2
    while name.lower() in taken:
        name, n = '%s-%d.bplist' % (stem, n), n + 1
    return name


FIRST_PRESET = 'p1'
FIRST_NAME = 'スタンダード'
MAX_PRESETS = 10
NAME_MAX = 30
PRESET_ID = re.compile(r'p[0-9]{1,4}')
COVER_NAME = re.compile(r'p[0-9]{1,4}\.(png|jpg)')   # config/covers/ の中のファイル名（Python が付ける）

_CTRL = re.compile(r'[\x00-\x1f\x7f]')


def check(key, value):
    """値を確かめて正規化した値を返す。だめなら SettingsError('bad-value', key)"""
    s = SPEC.get(key)
    if s is None:
        raise SettingsError('unknown-key', key)
    k = s['kind']
    bad = SettingsError('bad-value', key)
    if k == 'bool':
        if isinstance(value, bool):
            return value
        raise bad
    if k in ('int', 'float'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise bad
        if k == 'int':
            if value != int(value):
                raise bad
            value = int(value)
        else:
            value = float(value)
            if value == int(value):
                value = int(value)   # 3.0 は 3 として保存する（設定ファイルを読みやすく）
        if not s['min'] <= value <= s['max']:
            raise bad
        return value
    if k in ('str', 'path'):
        if not isinstance(value, str) or _CTRL.search(value) or len(value) > s.get('maxlen', 1000):
            raise bad
        if value == '':
            if s.get('empty') or k == 'path':
                return value
            raise bad
        if 'pattern' in s and not re.fullmatch(s['pattern'], value):
            raise bad
        return value
    if k == 'choice':
        if not isinstance(value, bool) and value in s['choices']:   # True は 1 と等しいので除く
            return int(value) if isinstance(value, float) else value
        raise bad
    if k == 'choices':
        if not isinstance(value, list) or not value or any(v not in s['choices'] for v in value):
            raise bad
        return list(dict.fromkeys(value))   # 重複を除く（順は保つ）
    raise bad


def _check_star_range(changes, values):
    """★の範囲を変えた時、下限が上限より上なら SettingsError('bad-value', 変えた項目)。片方だけ変えた時もセットの今の値と比べる。
    どちらかが 0（効かない）なら何でも通す。ほかの項目だけ変えた時は確かめない（手で書き換えた設定ファイルでも、ほかの項目は変えられる）"""
    if 'star_min' not in changes and 'star_max' not in changes:
        return
    lo, hi = values['star_min'], values['star_max']
    if lo > 0 and hi > 0 and lo > hi:
        raise SettingsError('bad-value', 'star_max' if 'star_min' not in changes else 'star_min')


def check_name(name):
    """セットの名前（1〜30文字・制御文字なし。前後の空白は落とす）"""
    if not isinstance(name, str):
        raise SettingsError('bad-name')
    name = name.strip()
    if not name or len(name) > NAME_MAX or _CTRL.search(name):
        raise SettingsError('bad-name')
    return name


def preset_default(key, pid, name):
    """セットの項目の初期値。ファイル名とプレイリスト名だけはセットごとに違う（同じだとゲームの中で区別できず、ファイルも上書きし合う）"""
    if pid != FIRST_PRESET:
        if key == 'output_filename':
            return 'blnext-%s.bplist' % pid
        if key == 'playlist_title':
            return ('次回おすすめ（%s）' % name)[:SPEC['playlist_title']['maxlen']]
    d = SPEC[key]['default']
    return list(d) if isinstance(d, (list, tuple)) else d


def _value(key, raw_values, default):
    if key in raw_values:
        try:
            return check(key, raw_values[key])
        except SettingsError:
            pass
    return list(default) if isinstance(default, (list, tuple)) else default


def _presets_of(raw):
    """ファイルの中身からセットの並びを取り出す（壊れた物は飛ばす）。1つも無ければ、昔の形（項目が直下）から最初のセットを作る"""
    out, seen = [], set()
    ps = raw.get('presets')
    for p in ps if isinstance(ps, list) else []:
        if not isinstance(p, dict) or len(out) >= MAX_PRESETS:
            continue
        pid = p.get('id')
        if not isinstance(pid, str) or not PRESET_ID.fullmatch(pid) or pid in seen:
            continue
        seen.add(pid)
        try:
            name = check_name(p.get('name'))
        except SettingsError:
            name = FIRST_NAME if pid == FIRST_PRESET else pid
        vals = p.get('values') if isinstance(p.get('values'), dict) else {}
        q = {'id': pid, 'name': name, 'values': _margin_of_level({k: v for k, v in vals.items() if k in PRESET_KEYS})}
        cover = p.get('cover')
        if isinstance(cover, str) and COVER_NAME.fullmatch(cover) and cover.split('.')[0] == pid:
            q['cover'] = cover
        out.append(q)
    if not out:
        out = [{'id': FIRST_PRESET, 'name': FIRST_NAME, 'values': _margin_of_level({k: raw[k] for k in PRESET_KEYS if k in raw})}]
    return out


def _margin_of_level(vals):
    """精度の上限の余裕（acc_cap_margin。2026-11-01 に足した）を持たないセットは、ほかの難しさの項目が合う段階の余裕で始める
    （足す前に着実・チャレンジングだったセットが、初期値（標準の 0.5）のせいでカスタムにならないように）。次に書く時にファイルに残る"""
    if 'acc_cap_margin' in vals:
        return vals
    cur = {k: _value(k, vals, SPEC[k]['default']) for k in SPEC if k in DIFFICULTY['standard']}
    lv = next((k for k, d in DIFFICULTY.items() if all(cur.get(x) == v for x, v in d.items() if x != 'acc_cap_margin')), None)
    if lv and DIFFICULTY[lv]['acc_cap_margin'] != SPEC['acc_cap_margin']['default']:
        return {**vals, 'acc_cap_margin': DIFFICULTY[lv]['acc_cap_margin']}
    return vals


class Settings:
    def __init__(self, config_dir):
        self.dir = config_dir
        self.path = os.path.join(config_dir, 'settings.json')
        self.cover_dir = os.path.join(config_dir, 'covers')
        self._lock = threading.RLock()

    def _read_raw(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, raw, presets):
        raw = dict(raw)
        for k in PRESET_KEYS + REMOVED_KEYS:   # 昔の形で直下にあった項目は presets の中へ移した（なくした項目は捨てる）
            raw.pop(k, None)
        raw['presets'] = presets
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_atomic(self.path, json.dumps(raw, ensure_ascii=False, indent=1))

    @staticmethod
    def _find(presets, pid):
        return next((p for p in presets if p['id'] == pid), None)

    def _pick(self, raw, presets, pid):
        """pid のセット。None なら画面で開いているセット、無ければ最初のセット"""
        if pid is None:
            pid = _value('active_preset', raw, SPEC['active_preset']['default'])
        return self._find(presets, pid) or presets[0]

    @staticmethod
    def _merged(raw, p):
        out = {k: _value(k, raw, SPEC[k]['default']) for k in COMMON_KEYS}
        for k in PRESET_KEYS:
            out[k] = _value(k, p['values'], preset_default(k, p['id'], p['name']))
        return out

    # ---- 読む ----
    def values(self, preset=None):
        """共通の項目と、セットの項目の今の値（無い・壊れている項目は初期値）。preset 省略時は画面で開いているセット"""
        with self._lock:
            raw = self._read_raw()
        presets = _presets_of(raw)
        return self._merged(raw, self._pick(raw, presets, preset))

    def get(self, key, preset=None):
        return self.values(preset)[key]

    def view(self, preset=None):
        """1つのセットから見た設定（values()/get() を持つ）。推定・組み立て・出力に渡す。無いセットなら SettingsError('no-preset')"""
        if preset is not None and preset not in self.preset_ids():
            raise SettingsError('no-preset', str(preset))
        return PresetView(self, preset or self.active_preset())

    def preset_ids(self):
        return [p['id'] for p in self.presets(brief=True)]

    def active_preset(self):
        with self._lock:
            raw = self._read_raw()
        presets = _presets_of(raw)
        return self._pick(raw, presets, None)['id']

    def presets(self, brief=False):
        """セットの並び。brief でなければ、各セットの全項目の値と、セットごとに違う初期値も付ける"""
        with self._lock:
            raw = self._read_raw()
        out = []
        for p in _presets_of(raw):
            d = {'id': p['id'], 'name': p['name'], 'cover': p.get('cover')}
            if not brief:
                d['values'] = self._merged(raw, p)
                d['defaults'] = {k: preset_default(k, p['id'], p['name']) for k in ('output_filename', 'playlist_title')}
            out.append(d)
        return out

    def cover_path(self, preset):
        """セットの画像のファイル（無ければ None）"""
        p = next((x for x in self.presets(brief=True) if x['id'] == preset), None)
        if not p or not p['cover']:
            return None
        path = os.path.join(self.cover_dir, p['cover'])
        return path if os.path.isfile(path) else None

    # ---- 書く ----
    def update(self, changes, *, trusted=False, preset=None):
        """changes の項目を書き込み、（preset のセットから見た）全項目の値を返す。1つでもだめなら何も書かずに SettingsError。
        セットの項目は preset のセット（省略時は画面で開いているセット）に入る。
        trusted=False（画面から）の時は DIALOG_ONLY の項目を受けない"""
        if not isinstance(changes, dict):
            raise SettingsError('bad-request')
        ok = {}
        for key, value in changes.items():
            if key in DIALOG_ONLY and not trusted:
                raise SettingsError('not-allowed', key)
            ok[key] = check(key, value)
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            if preset is not None and not self._find(presets, preset):
                raise SettingsError('no-preset', str(preset))
            p = self._pick(raw, presets, preset)
            if 'active_preset' in ok and not self._find(presets, ok['active_preset']):
                raise SettingsError('no-preset', ok['active_preset'])
            for k, v in ok.items():
                if k in PRESET_KEYS:
                    p['values'][k] = v
                else:
                    raw[k] = v
            self._check_unique(raw, presets)
            merged = self._merged(raw, p)
            _check_star_range(ok, merged)
            self._write(raw, presets)
            return merged

    def reset(self, keys, preset=None):
        """keys の項目を初期値に戻す（ファイルから消す）。セットの項目は preset のセットだけ。DIALOG_ONLY も消してよい（場所を忘れるだけ）"""
        if not isinstance(keys, list) or any(k not in SPEC for k in keys):
            raise SettingsError('bad-request')
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            if preset is not None and not self._find(presets, preset):
                raise SettingsError('no-preset', str(preset))
            p = self._pick(raw, presets, preset)
            for k in keys:
                if k in PRESET_KEYS:
                    p['values'].pop(k, None)
                else:
                    raw.pop(k, None)
            self._check_unique(raw, presets)
            self._write(raw, presets)
            return self._merged(raw, p)

    def _check_unique(self, raw, presets):
        """出力ファイル名がセット同士で重ならないこと（重なると同じファイルを上書きし合う）"""
        names = [self._merged(raw, p)['output_filename'].lower() for p in presets]
        if len(set(names)) != len(names):
            raise SettingsError('duplicate-filename', 'output_filename')

    def add_preset(self, name, copy_from=None):
        """セットを足して id を返す。copy_from のセットの値（と画像）を写す。ファイル名・プレイリスト名は新しいセットの初期値にする"""
        name = check_name(name)
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            if len(presets) >= MAX_PRESETS:
                raise SettingsError('too-many-presets')
            src = None
            if copy_from is not None:
                src = self._find(presets, copy_from)
                if not src:
                    raise SettingsError('no-preset', str(copy_from))
            # 消したセットの id は使い回さない（DB に残る固定・出力の記録が別のセットに付かないように）
            seq = raw.get('preset_seq') if isinstance(raw.get('preset_seq'), int) else 0
            n = max([seq] + [int(p['id'][1:]) for p in presets]) + 1
            if n > 9999:
                raise SettingsError('too-many-presets')
            pid = 'p%d' % n
            raw['preset_seq'] = n
            vals ={k: v for k, v in (src['values'] if src else {}).items() if k not in ('output_filename', 'playlist_title')}
            q = {'id': pid, 'name': name, 'values': vals}
            if src and src.get('cover'):
                ext = src['cover'].rsplit('.', 1)[1]
                try:
                    shutil.copyfile(os.path.join(self.cover_dir, src['cover']), os.path.join(self.cover_dir, '%s.%s' % (pid, ext)))
                    q['cover'] = '%s.%s' % (pid, ext)
                except OSError:
                    pass
            presets.append(q)
            # 新しいセットの初期のファイル名を、別のセットが手で使っていたら番号をずらす
            taken = {self._merged(raw, p)['output_filename'].lower() for p in presets[:-1]}
            if preset_default('output_filename', pid, name).lower() in taken:
                n = 2
                while ('blnext-%s-%d.bplist' % (pid, n)).lower() in taken:
                    n += 1
                q['values']['output_filename'] = 'blnext-%s-%d.bplist' % (pid, n)
            self._write(raw, presets)
            return pid

    def add_difficulty_presets(self, copy_from, levels=DIFFICULTY_SET_DEFAULT, size=DIFFICULTY_SET_SIZE):
        """copy_from のセットを写して、難しさだけを変えたセットを levels（DIFFICULTY のキー）の分だけ足し、その id の並びを返す
        （遊び比べる用。並びは 着実 → 標準 → チャレンジング の順にそろえる。曲数 size・exclude_other_sets オン・難しさの段階の値・
        プレイリスト名とファイル名はセットごと）。levels が空・知らない段階・重複なら 'bad-levels'、size が曲数の範囲外なら 'bad-value'。
        同じ名前のセットが1つでもあれば何も作らずに SettingsError('preset-exists', その名前を「・」でつないだ物)、
        足すと上限を超えるなら 'too-many-presets'"""
        if (not isinstance(levels, (list, tuple)) or not levels or len(set(map(str, levels))) != len(levels)
                or any(not isinstance(x, str) or x not in DIFFICULTY for x in levels)):
            raise SettingsError('bad-levels')
        size = check('playlist_size', size)
        levels = [k for k in DIFFICULTY if k in levels]
        with self._lock:   # RLock なので add_preset・update をこの中から呼べる（途中で別の書き込みが割り込まない）
            presets = self.presets()
            if not any(p['id'] == copy_from for p in presets):
                raise SettingsError('no-preset', str(copy_from))
            taken = [DIFFICULTY_SETS[k] for k in levels if any(p['name'] == DIFFICULTY_SETS[k] for p in presets)]
            if taken:
                raise SettingsError('preset-exists', '・'.join(taken))
            if len(presets) + len(levels) > MAX_PRESETS:
                raise SettingsError('too-many-presets')
            files = {p['values']['output_filename'].lower() for p in presets}
            out = []
            for level in levels:
                name = DIFFICULTY_SETS[level]
                pid = self.add_preset(name, copy_from=copy_from)
                fname = _free_filename('blnext-' + level, files)   # 前に作って名前だけ変えたセットが使っていれば番号をずらす
                files.add(fname.lower())
                self.update({**DIFFICULTY[level], 'playlist_size': size, 'exclude_other_sets': True,
                             'playlist_title': '次回 ' + name, 'output_filename': fname}, preset=pid)
                out.append(pid)
            return out

    def make_initial_sets(self, size=INITIAL_SET_SIZE):
        """初回の準備: まだ手を付けていない最初のセット（1つだけ・値も画像も無い）を、難しさ違いの3つのセット（add_difficulty_presets）に
        置き換え、id の並びを返す。手を付けていれば何も変えずに []（初回の準備を途中でやり直した時・前の版からの設定）"""
        with self._lock:
            presets = _presets_of(self._read_raw())
            if len(presets) != 1 or presets[0]['values'] or presets[0].get('cover'):
                return []
            first = presets[0]['id']
            ids = self.add_difficulty_presets(first, list(DIFFICULTY), size)
            self.delete_preset(first)
            return ids

    def rename_preset(self, pid, name):
        name = check_name(name)
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            p = self._find(presets, pid)
            if not p:
                raise SettingsError('no-preset', str(pid))
            p['name'] = name
            self._check_unique(raw, presets)
            self._write(raw, presets)

    def reorder_presets(self, ids):
        """セットの並びを ids（今あるセットの id をちょうど1回ずつ並べた物）にする。並び＝おすすめの画面のタブ・ホームの振り返りの
        タブ・全部のセットを書き込む順。ちょうどの並べ替えでなければ何も書かずに SettingsError('bad-request')。
        組のセットを交互に選ぶ順（serve.py の _group_ids）は難しさの順なので、並びを変えても変わらない"""
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids) or len(set(ids)) != len(ids)
                    or sorted(ids) != sorted(p['id'] for p in presets)):
                raise SettingsError('bad-request')
            self._write(raw, [self._find(presets, x) for x in ids])

    def delete_preset(self, pid):
        """セットを消す（最後の1つは消せない）。開いていたセットなら最初のセット（並びの一番前）を開く"""
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            p = self._find(presets, pid)
            if not p:
                raise SettingsError('no-preset', str(pid))
            if len(presets) <= 1:
                raise SettingsError('last-preset')
            was_open = self._pick(raw, presets, None) is p
            presets.remove(p)
            if was_open or not self._find(presets, _value('active_preset', raw, FIRST_PRESET)):
                raw.pop('active_preset', None)   # 初期値（p1）のままで合う時はキーを残さない
                if presets[0]['id'] != FIRST_PRESET:
                    raw['active_preset'] = presets[0]['id']
            self._write(raw, presets)
            if p.get('cover'):
                try:
                    os.remove(os.path.join(self.cover_dir, p['cover']))
                except OSError:
                    pass

    def set_cover(self, pid, data, ext):
        """セットの画像を置く（中身の確かめは呼ぶ側。ファイル名は Python が付ける）。data=None で消す（アプリのアイコンに戻る）"""
        if ext not in ('png', 'jpg'):
            raise SettingsError('bad-image')
        with self._lock:
            raw = self._read_raw()
            presets = _presets_of(raw)
            p = self._find(presets, pid)
            if not p:
                raise SettingsError('no-preset', str(pid))
            old = p.pop('cover', None)
            if data is not None:
                os.makedirs(self.cover_dir, exist_ok=True)
                name = '%s.%s' % (pid, ext)
                write_atomic(os.path.join(self.cover_dir, name), data)
                p['cover'] = name
            self._write(raw, presets)
            if old and old != p.get('cover'):
                try:
                    os.remove(os.path.join(self.cover_dir, old))
                except OSError:
                    pass


class PresetView:
    """1つのセットから見た設定（推定・組み立て・出力へ渡す）。Settings と同じく values()/get() を持つ"""
    def __init__(self, settings, preset):
        self.settings, self.preset = settings, preset

    def values(self):
        return self.settings.values(self.preset)

    def get(self, key):
        return self.values()[key]


def describe():
    """画面に渡す項目の説明（初期値・種類・範囲・共通かセットごとか）"""
    out = {}
    for key, s in SPEC.items():
        d = {k: (list(v) if isinstance(v, tuple) else v) for k, v in s.items() if k != 'pattern'}
        d['dialogOnly'] = key in DIALOG_ONLY
        d['scope'] = 'preset' if key in PRESET_KEYS else 'common'
        out[key] = d
    return out
