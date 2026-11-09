# 画面の崩れ（はみ出し・文字の切れ・重なり・null 等の表示）と JS のエラーを、全画面 × 幅で確かめる。撮った画像は test-out/screens/。
# 新しい画面・ダイアログ・開いた状態を足したら STATES に1つ足す。崩れの見つけ方は tools/cdp.py の LAYOUT_JS
import pytest

from conftest import OUT, SCREENS, WIDTHS, FakeRefresher, FakeUpdater, fake_players, many_passed, since_export
from fakebs import fake_installs

# 開いた後の状態（画面, 名前, 準備の JS / 関数）
STATES = [
    ('recommend', 'reason', lambda a: a.click_sel('.recList .recRow:not(.head) .songName', index=0)),
    ('recommend', 'sorted', lambda a: a.click_sel('.recRow.head .th', text='推定精度')),
    # 手で並べた（区分の見出しは出さず「基本の並びに戻す」を押せる）。理由を開いた行（持ち手が開いた欄の分まで伸びる）と、
    # ドラッグ中（つかんだ行は薄く、落とす位置に線）
    ('recommend', 'manual', lambda a: (manual_order(a), a.open('recommend'),
                                       a.click_sel('.recList .recRow:not(.head) .songName', index=1), drag_over(a))),
    ('recommend', 'openAll', lambda a: a.click_sel('button.openAll')),
    # 挑戦したがクリアしていない曲（未クリアの印）を開いた: 挑戦の回数と最後の挑戦
    ('recommend', 'tried', lambda a: (show_unplayed(a), a.click_sel('.recList .recRow:has(.badge.tried) .songName'))),
    # データが変わって、作り直すとリストが変わる時の知らせ（入る・外れる曲。長い曲名も）
    ('recommend', 'pending', lambda a: (make_pending(a), a.open('recommend'), a.until("!!document.querySelector('.pendBar')"))),
    # ★の範囲を入れた: 条件の欄に下限・上限と、範囲の外で外した曲の数・値を選ぶ参考（最近クリアした曲の★）
    ('recommend', 'starRange', lambda a: (star_range(a), a.open('recommend'),
                                          a.until("!!document.querySelector('.condItem[data-key=star_min] .condHint')"))),
    ('recommend', 'bsdir', lambda a: (a.click_sel('button', text='変える'), a.until("!!document.querySelector('.bsItem, .bsList p')"))),
    ('similar', 'pick', lambda a: (a.click_sel('table.list tbody tr', index=0), a.until("document.querySelectorAll('.scrollBox tr').length>1"))),
    # セットが3つ（長い名前も）: おすすめのタブ・全部書き込むボタン、似ている人のタブ、設定のセットの部分、ホームの振り返りのタブ
    ('recommend', 'presets', lambda a: (add_presets(a), a.open('recommend'))),
    ('recommend', 'addPreset', lambda a: (a.click_sel('.presetTab.add'), a.until("!!document.querySelector('.modal')"))),
    ('similar', 'presets', lambda a: (add_presets(a), a.open('similar'))),
    ('settings', 'presets', lambda a: (add_presets(a), a.open('settings'),
                                       a.js("document.getElementById('setPreset').scrollIntoView(); true"))),
    ('home', 'presets', lambda a: (a.serve._api_export({'preset': add_presets(a)}), a.open('home'))),
    # 抜かれた人が多い（25人。枠の中でスクロール）・抜いた人は9人。見出しの人数
    ('home', 'manyPassed', lambda a: a.open('home')),
    # 前回の出力から（上下の人の表の2段の見出しと2列・タイルの2行目）
    ('home', 'sinceExport', lambda a: (since_export(a.serve), a.open('home'))),
    # セットのタブをドラッグしている所（つかんだタブは薄く、落とす位置に線）
    ('recommend', 'tabDrag', lambda a: (add_presets(a), a.open('recommend'), tab_drag_over(a))),
    # 難しさ: 難しさ違いのセットが3つ（着実を開く。スライダーに印・pass の上限・ほかのセットと同じ曲を外した数）、
    # カスタム（個別に変えた。pass の上限は95%点）、難しさ違いのセットを作る小さな画面、設定画面の難しさの行
    ('recommend', 'diffSets', lambda a: (add_diff_sets(a), a.open('recommend'))),
    ('recommend', 'custom', lambda a: (add_custom(a), a.open('recommend'))),
    ('recommend', 'diffDialog', lambda a: (a.click_sel('.diffSetBtn'), a.until("!!document.querySelector('.modal .dsLevel')"))),
    ('settings', 'diffSets', lambda a: (add_diff_sets(a), a.open('settings'),
                                        a.js("document.getElementById('grp_list').scrollIntoView(); true"))),
    # 組のセット（着実）で「組を交互に選び直す」を押した小さな画面（組全部か、このセットだけかを選ぶ）
    ('recommend', 'rebuildDialog', lambda a: (add_diff_sets(a), a.open('recommend'), a.click_sel('.groupBtn'),
                                              a.until("!!document.querySelector('.modal .rbChoice')"))),
    # プレイリストの画像を選ぶ小さな画面（まとめの欄の画像を押す。cover.js）。読み込んだ画像を使っている時（そのタイルにも印）と、
    # 設定画面の「画像を選ぶ…」から開いた時
    ('recommend', 'coverDialog', lambda a: (a.click_sel('.recSum .coverBtn'),
                                            a.until("document.querySelectorAll('.coverDlg .coverTile').length===6"))),
    ('recommend', 'coverCustom', lambda a: (custom_cover(a), a.open('recommend'), a.click_sel('.recSum .coverBtn'),
                                            a.until("!!document.querySelector('.coverDlg .coverCustom .coverTile.on')"))),
    ('settings', 'coverDialog', lambda a: (a.click_sel('.coverCtl .coverPick'),
                                           a.until("document.querySelectorAll('.coverDlg .coverTile').length===6"))),
    # 初回の準備の小さな画面: 見つかった人（長い名前）・取得の途中（比較対象の人数）・できた3つのリスト
    ('home', 'setupPlayer', lambda a: setup_player(a)),
    ('home', 'setupFetch', lambda a: (a.serve.SETTINGS.update({'player_id': a.self_id}), a.serve.STORE.set_meta('setup_pending', 1), a.open('home'),
                                      a.until("document.querySelector('.setupDlg').innerText.includes('比較対象のスコア')"))),
    ('home', 'setupReady', lambda a: (add_diff_sets(a), a.serve.STORE.set_meta('setup_pending', 1), a.open('home'),
                                      a.until("document.querySelectorAll('.setupSet').length>=3", timeout=20))),
    ('home', 'noPlayer', lambda a: (a.serve.SETTINGS.update({'player_id': ''}), a.open('home'), a.until("!!document.querySelector('.setupDlg')"),
                                    a.click_sel('.setupDlg button', text='あとで'), a.until("!document.querySelector('.setupDlg')"))),
    # 似ている人が足りない人: まとめの欄の「目安です」・★の一番上より上で外した数
    ('recommend', 'fewSimilar', lambda a: (few_similar(a), a.open('recommend'), a.until("!!document.querySelector('.recRough')"))),
    # 統計が非公開: ホームの知らせ・案内の小さな画面・初回の準備の案内・プレイリストを作った後の警告（作り直した知らせのトーストも出ている）
    ('recommend', 'statsWarn', lambda a: (a.serve.STORE.set_meta('stats_public', 0), make_pending(a), a.open('recommend'), a.click_sel('.pendBar > button'),
                                          a.until("!!document.querySelector('.statsDlg') && document.querySelector('#toast').classList.contains('show')"))),
    ('home', 'statsPrivate', lambda a: (a.serve.STORE.set_meta('stats_public', 0), a.open('home'), a.until("!!document.querySelector('.statsNotice')"))),
    ('home', 'statsDialog', lambda a: (a.serve.STORE.set_meta('stats_public', 0), a.open('home'), a.click_sel('.statsHow'),
                                       a.until("!!document.querySelector('.statsDlg')"))),
    ('home', 'setupStats', lambda a: setup_stats(a)),
    # 設定の一番下の全曲データの欄（警告つきの更新のボタン）
    ('settings', 'mapsCard', lambda a: (a.serve.STORE.set_meta('maps_fetched_at', 1760000000), a.serve.STORE.set_meta('maps_checked_at', 1760000000),
                                        a.serve.STORE.set_meta('song_details_as_of', 1760000000), a.open('settings'),
                                        a.js("document.getElementById('grp_maps').scrollIntoView(); true"))),
    # 新しい版の知らせ（長い見出しも）・ダウンロードの途中
    ('settings', 'reportDialog', lambda a: (a.click_sel('#grp_about .reportIssue'), a.until("!!document.querySelector('.reportDlg')"))),
    ('settings', 'updatePrompt', lambda a: (a.click_sel('.updCheck'), a.until("!!document.querySelector('.updDlg .updNotes')"))),
    ('settings', 'updateRun', lambda a: (a.click_sel('.updCheck'), a.until("!!document.querySelector('.updDlg .updNotes')"),
                                         a.click_sel('.updDlg button', text='更新する'), a.until("!!document.querySelector('.updDlg .updBar')"))),
]


def few_similar(a):
    """保存したリストを、似ている人が足りない人の物にする（DB を直接）"""
    import json
    lst = json.loads(a.serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])
    lst.update(fewSimilar=True, noClear=True)
    lst['filters'].update(starTop=9.8, starTopOut=1234)
    with a.serve.STORE.tx():
        a.serve.STORE.conn.execute("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(lst, ensure_ascii=False),))


def setup_player(a):
    """まだプレイヤーを選んでいない状態で開き直し、名前で探した所（1人。とても長い名前）"""
    a.serve.SETTINGS.update({'player_id': ''})
    a.open('home')
    a.until("!!document.getElementById('setupQuery')")
    a.js("document.getElementById('setupQuery').value='テスト'; true")
    a.click_sel('.setupSearch button')
    a.until("!!document.querySelector('.setupDlg .result')")


def setup_stats(a):
    """初回の準備で、統計が非公開の人を選んだ所"""
    setup_player(a)
    a.click_sel('.setupDlg .result button')
    a.until("!!document.querySelector('.setupSkipStats')")


def setup_fakes(serve, state, monkeypatch):
    """初回の準備・更新の状態で使う、BeatLeader の検索・取得・更新のふり"""
    if state == 'setupPlayer':
        fake_players(serve, monkeypatch, serve.SETTINGS.get('player_id'), 'とても長いプレイヤーの名前のテスト_' + 'ABCDEFGHIJ' * 4)
    if state == 'setupStats':
        fake_players(serve, monkeypatch, serve.SETTINGS.get('player_id'), public=False)
    if state == 'setupFetch':
        monkeypatch.setattr(serve, 'REFRESHER', FakeRefresher(total=1000))
        monkeypatch.setattr(serve, 'last_run_at', lambda pid: None)
    if state in ('updatePrompt', 'updateRun'):
        fake = FakeUpdater()
        fake.steps = 100000
        monkeypatch.setattr(serve, 'UPDATER', fake)


def custom_cover(a):
    """セット p1 の画像を、ファイルから読み込んだ画像（用意した色違いのどれとも違う PNG。64×64 の赤一色）にする"""
    import struct, zlib
    raw = b''.join(b'\x00' + b'\xff\x00\x00' * 64 for _ in range(64))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    a.serve.SETTINGS.set_cover('p1', b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
                               + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''), 'png')


def add_diff_sets(a):
    """難しさ違いのセットを3つ（着実・標準・チャレンジング・5曲ずつ）作る。着実のセットが開く"""
    return a.serve._api_presets_difficulty_set({'copyFrom': 'p1', 'levels': ['steady', 'standard', 'challenge'], 'size': 5})['ids']


def add_custom(a):
    """どの段階とも合わないセット（未プレイの pass の上限は95%点・挑戦枠 1）を写して作って開く"""
    pid = a.serve.SETTINGS.add_preset('カスタムの例', copy_from='p1')
    a.serve.SETTINGS.update({'unplayed_pass_cap': 'p95', 'challenge_max': 1}, preset=pid)
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


def drag_over(a):
    """1曲目の持ち手をつかんで、3曲目の下半分の上にいる所（落とさない。JS でドラッグのイベントを送る）"""
    a.js("(()=>{const dt=new DataTransfer(), it=document.querySelectorAll('.recItem');"
         "it[0].querySelector('.grip').dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));"
         "const r=it[2].getBoundingClientRect();"
         "it[2].dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+50,clientY:r.bottom-3}));"
         "return true;})()")
    a.until("!!document.querySelector('.recItem.dropAfter') && !!document.querySelector('.recItem.dragging')", what='ドラッグ中')


def tab_drag_over(a):
    """最後のセットのタブをつかんで、最初のタブの左半分の上にいる所（落とさない。JS でドラッグのイベントを送る）"""
    a.js("(()=>{const dt=new DataTransfer(), t=document.querySelectorAll('.presetTabs .presetTab:not(.add)');"
         "t[t.length-1].dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));"
         "const r=t[0].getBoundingClientRect();"
         "t[0].dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+3,clientY:r.top+5}));"
         "return true;})()")
    a.until("!!document.querySelector('.presetTab.dropBefore') && !!document.querySelector('.presetTab.dragging')", what='タブのドラッグ中')


def show_unplayed(a):
    """未プレイの曲も出る条件（作り物のデータの初期の条件。前のテストが初期値に戻していることがある）で作り直して開く"""
    a.serve.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50}, preset='p1')
    a.serve._api_recommend_rebuild({'preset': 'p1'})
    a.open('recommend')


def star_range(a):
    """セット p1 に★の範囲（★4〜★6。作り物のデータの★は 3.0〜10.9）を入れて作り直す"""
    a.serve.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50, 'star_min': 4, 'star_max': 6}, preset='p1')
    a.serve._api_recommend_rebuild({'preset': 'p1'})


def add_presets(a):
    pid = a.serve.SETTINGS.add_preset('ワンセイバー', copy_from='p1')
    a.serve.SETTINGS.add_preset('とても長いセットの名前_ABCDEFGHIJKLMNOP', copy_from='p1')
    a.serve.SETTINGS.update({'active_preset': pid})
    return pid


def check(app, name):
    app.shot(OUT / 'screens' / f'{name}.png', full=True)
    issues = app.layout_issues()
    assert not issues, '崩れ:\n' + '\n'.join(f"[{i['kind']}] {i['where']} {i['text']}" for i in issues)
    assert app.js('window.__xss') is None, 'HTML のタグに見える名前のスクリプトが動いた'


@pytest.mark.parametrize('width', WIDTHS)
@pytest.mark.parametrize('screen', SCREENS)
def test_screen(ui, screen, width):
    ui.resize(width)
    ui.open(screen)
    check(ui, f'{screen}_{width}')


@pytest.mark.parametrize('width', (960, 1600))
@pytest.mark.parametrize('screen,state,prep', STATES, ids=[f'{s}-{n}' for s, n, _ in STATES])
def test_state(ui, screen, state, prep, width, tmp_path, monkeypatch):
    if state == 'bsdir':   # 自動で探す結果は作り物（このPCの本物に左右されない。共通コンテンツのつながり先の長い表示も確かめる）
        fake_installs(ui.serve, tmp_path, monkeypatch)
    if state == 'manyPassed':
        many_passed(ui.serve, monkeypatch)
    setup_fakes(ui.serve, state, monkeypatch)
    ui.resize(width)
    ui.open(screen)
    prep(ui)
    ui.wait(0.2)
    check(ui, f'{screen}_{state}_{width}')


# 文字を最大（145%）にしても崩れない。960 はウィンドウが狭いので 133% に抑える（scale.js の MIN_W）
@pytest.mark.parametrize('width,zoom', ((960, '1.33'), (1280, '1.45')))
@pytest.mark.parametrize('screen', SCREENS)
def test_screen_large_text(ui, screen, width, zoom):
    ui.serve.SETTINGS.update({'ui_scale': 145})
    ui.resize(width)
    ui.open(screen)
    assert ui.js('document.documentElement.style.zoom') == zoom
    check(ui, f'{screen}_x145_{width}')


LARGE_STATES = ('settings-mapsCard', 'recommend-fewSimilar', 'recommend-reason', 'recommend-manual', 'recommend-openAll','recommend-tried', 'recommend-pending', 'recommend-starRange', 'recommend-presets', 'recommend-addPreset', 'recommend-bsdir', 'similar-pick', 'settings-presets',
                'home-presets', 'home-manyPassed', 'home-sinceExport', 'recommend-tabDrag', 'recommend-diffSets', 'recommend-custom', 'recommend-diffDialog', 'settings-diffSets',
                'recommend-rebuildDialog', 'recommend-coverCustom', 'settings-coverDialog', 'home-setupPlayer', 'home-setupFetch', 'home-setupReady', 'home-setupStats', 'home-statsPrivate', 'home-statsDialog', 'recommend-statsWarn',
                'settings-updatePrompt', 'settings-reportDialog')


@pytest.mark.parametrize('screen,state,prep', [s for s in STATES if f'{s[0]}-{s[1]}' in LARGE_STATES],
                         ids=[f'{s}-{n}' for s, n, _ in STATES if f'{s}-{n}' in LARGE_STATES])
def test_state_large_text(ui, screen, state, prep, tmp_path, monkeypatch):
    if state == 'bsdir':
        fake_installs(ui.serve, tmp_path, monkeypatch)
    if state == 'manyPassed':
        many_passed(ui.serve, monkeypatch)
    setup_fakes(ui.serve, state, monkeypatch)
    ui.serve.SETTINGS.update({'ui_scale': 145})
    ui.resize(960)
    ui.open(screen)
    prep(ui)
    ui.wait(0.2)
    check(ui, f'{screen}_{state}_x145_960')


def test_names_are_text(ui):
    """API から来た名前（HTML のタグに見える物も）は文字のまま出て、要素にならない"""
    ui.open('home')
    assert ui.js("[...document.querySelectorAll('#main td.name')].some(e=>e.textContent.includes('<img src=x'))")
    assert ui.js("document.querySelectorAll('#main img:not(.songCover)').length") == 0   # 曲のカバー画像のほかに画像は無い
    # 見えるまで読まない画像は、読む先が data-src にある
    assert ui.js("[...document.querySelectorAll('#main img.songCover')].every(i=>"
                 "/^[/]cover[/][0-9a-f]{40}[.]jpg$|^[/]icons[/]no-cover[.]svg$/.test("
                 "new URL(i.getAttribute('src')||i.dataset.src, location.href).pathname))")
