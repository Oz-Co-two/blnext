"""画面の比較で撮る画面の一覧。1つ＝ Shot(名前, 画面, 幅, 準備, 文字の大きさ, 全体を撮るか, 題名)。
新しい画面・小さな画面・開いた状態を足したら、ここに1つ足して `tools/run_tests.py visual -k <名前>` で一度流し、
test-out/visual/current/<名前>.png に目的の画面が写っているか見る（初回は基準を作るだけ）。
準備（prep）は (app, monkeypatch) を受ける。撮るたびに同じ絵になるよう、時刻や一時フォルダの場所が写らないようにする。"""
import shutil
from dataclasses import dataclass

import vislib
from fakebs import fake_installs

SCREENS = ('home', 'recommend', 'similar', 'settings', 'guide')
WIDTHS = (960, 1280, 1600)


@dataclass
class Shot:
    name: str
    screen: str
    width: int
    prep: object = None
    scale: int = 100       # 文字の大きさ（%）
    full: bool = True      # 縦に長い画面は全体を撮る（小さな画面を開いた状態は見えている所だけ）
    title: str = ''


def add_presets(a, _mp=None):
    """セットを足して（長い名前も）、足した1つ目を開く。返り値はその id"""
    pid = a.serve.SETTINGS.add_preset('ワンセイバー', copy_from='p1')
    a.serve.SETTINGS.add_preset('とても長いセットの名前_ABCDEFGHIJKLMNOP', copy_from='p1')
    a.serve.SETTINGS.update({'active_preset': pid})
    return pid


def make_pending(a):
    """今のリストを保存してから、その上の3曲を「今後出さない」にする（DB を直接。作り直すと入れ替わる状態）"""
    r = a.serve._api_recommend({})
    with a.serve.STORE.tx():
        for c in r['items'][:3]:
            a.serve.STORE.conn.execute("INSERT INTO user_marks(leaderboard_id, mark, preset, updated_at) VALUES(?, 'never', '', 0)",
                                       (c['leaderboard_id'],))


def manual_order(a):
    """今のリストを基本の並びの逆に並べる（画面の「見出しで並べ替え・ドラッグ」と同じ API）"""
    r = a.serve._api_recommend({})
    a.serve._api_recommend_order({'ids': [c['leaderboard_id'] for c in r['items']][::-1]})


def add_diff_sets(a, _mp=None):
    """難しさ違いのセットを3つ（着実・標準・チャレンジング・5曲ずつ）作る。着実のセットが開く"""
    return a.serve._api_presets_difficulty_set({'copyFrom': 'p1', 'levels': ['steady', 'standard', 'challenge'], 'size': 5})['ids']


def fake_updater(a, mp):
    """新しい版がある更新のふり"""
    import types
    mp.setattr(a.serve, 'UPDATER', types.SimpleNamespace(just_updated=None, check=lambda: {
        'current': 'v0.1.0', 'latest': 'v0.3.0', 'newer': True, 'size': 42 * 1048576, 'auto': 'ok',
        'notes': [{'version': 'v0.3.0', 'ja': ['新しい機能', '直したこと'], 'en': []}, {'version': 'v0.2.0', 'ja': ['その前の機能'], 'en': []}]}))


def add_custom(a, _mp=None):
    """どの段階とも合わないセット（未プレイの pass の上限は95%点・挑戦枠 1）を写して作って開く（p1 の値は変えない）"""
    pid = a.serve.SETTINGS.add_preset('カスタムの例', copy_from='p1')
    a.serve.SETTINGS.update({'unplayed_pass_cap': 'p95', 'challenge_max': 1}, preset=pid)
    a.serve.SETTINGS.update({'active_preset': pid})
    return pid


def bs_candidates(a, mp):
    """出力先の候補を作り物に（場所は毎回同じ所＝写真が変わらないように）"""
    base = vislib.OUT / '_bs'
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    fake_installs(a.serve, base, mp)


TITLES = {'home': 'ホーム', 'recommend': 'プレイリスト', 'similar': '似ている人', 'settings': '設定', 'guide': '使い方'}

SHOTS = [Shot(f'{s}_{w}', s, w, title=f'{TITLES[s]}（幅{w}）') for s in SCREENS for w in WIDTHS] + [
    Shot('recommend_reason_1280', 'recommend', 1280, title='プレイリスト: 理由を開いた',
         prep=lambda a, mp: a.click_sel('.recList .recRow:not(.head) .songName', index=0)),
    Shot('recommend_sorted_1280', 'recommend', 1280, title='プレイリスト: 推定精度で並べ替え',
         prep=lambda a, mp: a.click_sel('.recRow.head .th', text='推定精度')),
    Shot('recommend_manual_1280', 'recommend', 1280, title='プレイリスト: 手で並べた（区分の見出しなし・「基本の並びに戻す」を押せる）',
         prep=lambda a, mp: (manual_order(a), a.open('recommend'))),
    Shot('recommend_openAll_1280', 'recommend', 1280, full=False, title='プレイリスト: 全部開いた（自己ベストの JD・RT）',
         prep=lambda a, mp: a.click_sel('button.openAll')),
    Shot('recommend_tried_1280', 'recommend', 1280, full=False, title='プレイリスト: 未クリアの曲を開いた（挑戦の回数と最後の挑戦）',
         prep=lambda a, mp: a.click_sel('.recList .recRow:has(.badge.tried) .songName')),
    Shot('recommend_pending_1280', 'recommend', 1280, full=False, title='プレイリスト: 作り直すと変わる時の知らせ',
         prep=lambda a, mp: (make_pending(a), a.open('recommend'), a.until("!!document.querySelector('.pendBar')"))),
    Shot('recommend_presets_1280', 'recommend', 1280, title='プレイリスト: セットが3つ',
         prep=lambda a, mp: (add_presets(a), a.open('recommend'))),
    Shot('recommend_addPreset_1280', 'recommend', 1280, full=False, title='プレイリスト: セットを足す小さな画面',
         prep=lambda a, mp: (a.click_sel('.presetTab.add'), a.until("!!document.querySelector('.modal')"))),
    Shot('recommend_bsdir_1280', 'recommend', 1280, full=False, title='プレイリスト: 出力先を選ぶ小さな画面',
         prep=lambda a, mp: (bs_candidates(a, mp), a.click_sel('button', text='変える'),
                             a.until("document.querySelectorAll('.bsItem').length===2"))),
    Shot('similar_pick_1280', 'similar', 1280, title='似ている人: 人を選んだ',
         prep=lambda a, mp: (a.click_sel('table.list tbody tr', index=0), a.until("document.querySelectorAll('.scrollBox tr').length>1"))),
    Shot('settings_presets_1280', 'settings', 1280, title='設定: セットが3つ',
         prep=lambda a, mp: (add_presets(a), a.open('settings'))),
    Shot('home_presets_1280', 'home', 1280, title='ホーム: 振り返りがセットごと',
         prep=lambda a, mp: (a.serve._api_export({'preset': add_presets(a)}), a.open('home'))),
    Shot('recommend_diffSets_1280', 'recommend', 1280, title='プレイリスト: 難しさ違いのセットが3つ（着実を開いた）',
         prep=lambda a, mp: (add_diff_sets(a), a.open('recommend'))),
    Shot('recommend_custom_1280', 'recommend', 1280, title='プレイリスト: 難しさがカスタム（pass の上限は95%点）',
         prep=lambda a, mp: (add_custom(a), a.open('recommend'))),
    Shot('recommend_diffDialog_1280', 'recommend', 1280, full=False, title='プレイリスト: 難しさ違いのセットを作る小さな画面',
         prep=lambda a, mp: (a.click_sel('.diffSetBtn'), a.until("!!document.querySelector('.modal .dsLevel')"))),
    Shot('settings_diffSets_1280', 'settings', 1280, title='設定: 難しさ違いのセットが3つ（難しさの行）',
         prep=lambda a, mp: (add_diff_sets(a), a.open('settings'))),
    Shot('recommend_rebuildDialog_1280', 'recommend', 1280, full=False, title='プレイリスト: 組のセットを作り直す小さな画面（組全部か、このセットだけか）',
         prep=lambda a, mp: (add_diff_sets(a), a.open('recommend'), a.click_sel('.groupBtn'),
                             a.until("!!document.querySelector('.modal .rbChoice')"))),
    Shot('recommend_coverDialog_1280', 'recommend', 1280, full=False, title='プレイリスト: プレイリストの画像を選ぶ小さな画面（青を選んだ）',
         prep=lambda a, mp: (a.serve._api_cover_preset({'preset': 'p1', 'name': 'blue'}), a.open('recommend'),
                             a.click_sel('.recSum .coverBtn'),
                             a.until("document.querySelectorAll('.coverDlg .coverTile.on[data-cover=blue]').length===1"
                                     " && [...document.querySelectorAll('.coverDlg img')].every(i=>i.complete&&i.naturalWidth>0)"))),
    Shot('home_setupPlayer_1280', 'home', 1280, full=False, title='初回の準備: プレイヤーを選ぶ',
         prep=lambda a, mp: (a.serve.SETTINGS.update({'player_id': ''}), a.open('home'), a.until("!!document.getElementById('setupQuery')"))),
    Shot('home_setupReady_1280', 'home', 1280, full=False, title='初回の準備: 3つのリストができた',
         prep=lambda a, mp: (add_diff_sets(a), a.serve.STORE.set_meta('setup_pending', 1), a.open('home'),
                             a.until("document.querySelectorAll('.setupSet').length>=3", timeout=20))),
    Shot('home_statsPrivate_1280', 'home', 1280, full=False, title='ホーム: 統計が非公開の知らせ',
         prep=lambda a, mp: (a.serve.STORE.set_meta('stats_public', 0), a.open('home'), a.until("!!document.querySelector('.statsNotice')"))),
    Shot('home_statsDialog_1280', 'home', 1280, full=False, title='統計を公開にする案内',
         prep=lambda a, mp: (a.serve.STORE.set_meta('stats_public', 0), a.open('home'), a.click_sel('.statsHow'), a.until("!!document.querySelector('.statsDlg')"))),
    Shot('settings_updatePrompt_1280', 'settings', 1280, full=False, title='新しい版の知らせ',
         prep=lambda a, mp: (fake_updater(a, mp), a.click_sel('.updCheck'), a.until("!!document.querySelector('.updDlg .updNotes')"))),
] +[Shot(f'{s}_x145_960', s, 960, scale=145, title=f'{TITLES[s]}（文字 最大・幅960）') for s in SCREENS]
