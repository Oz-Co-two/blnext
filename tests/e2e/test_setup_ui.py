# 初回の準備の小さな画面（web/js/setup.js）: プレイヤーを選ぶ → 取得 → 3つのリスト → ゲームに入れる。BeatLeader の検索と取得はふりをする（ネットに出ない）
from types import SimpleNamespace
import json

import pytest

from conftest import FakeRefresher, fake_players
from core.fetch.http import NetError

DLG = "document.querySelector('.setupDlg')"
TEXT = "(" + DLG + "?.innerText||'')"


@pytest.fixture
def fresh(ui, monkeypatch):
    """まだプレイヤーを選んでいない状態。3つのセットは最初のセットを写して作る（作り物のデータの条件のまま・3曲ずつ。本物の make_initial_sets
    は値を変えた最初のセットを置き換えないので、test_setup.py で確かめる）。後始末は ui（p1 のほかを消す・プレイヤーを戻す）"""
    s = ui.serve
    pid = s.SETTINGS.get('player_id')
    s.SETTINGS.update({'player_id': ''})
    fake = FakeRefresher(total=3)
    monkeypatch.setattr(s, 'REFRESHER', fake)
    fake_players(s, monkeypatch, pid)
    monkeypatch.setattr(s.SETTINGS, 'make_initial_sets', lambda: s.SETTINGS.add_difficulty_presets('p1', ['steady', 'standard', 'challenge'], 3))
    return SimpleNamespace(fake=fake, id=pid)


def search(ui, text):
    ui.js("document.getElementById('setupQuery').value=%s; true" % json.dumps(text))
    ui.click_sel('.setupSearch button', text='探す')


def choose(ui):
    search(ui, 'テスト')
    ui.until("!!document.querySelector('.setupDlg .result')", what='見つかった人')
    ui.click_sel('.setupDlg .result button', text='この人です')


def test_setup_flow(ui, fresh):
    ui.open('home')
    ui.until("!!" + DLG, what='最初に開く小さな画面')
    assert 'はじめに' in ui.js(TEXT) and ui.js("document.activeElement.id") == 'setupQuery'
    choose(ui)
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='3つのリストができた')
    assert fresh.fake.started == 1                          # 選ぶと取得を始める
    assert ui.serve.SETTINGS.get('player_id') == fresh.id and ui.serve.SETTINGS.get('country') == 'jp'
    assert ui.serve.SETTINGS.get('target_country_rank') == 50   # 56位の上の切りのよい順位
    assert ui.serve.STORE.meta('setup_pending') is None
    rows = ui.js("[...document.querySelectorAll('.setupSet .sn')].map(e=>e.textContent)")
    assert [x.split('★')[0] for x in rows] == ['スタンダード', '着実', '標準', 'チャレンジング']   # 名前（★の範囲が続く）
    assert 'クリアしやすい曲' in ui.js(TEXT) and '3曲' in ui.js(TEXT) and 'PlaylistManager' in ui.js(TEXT)
    ui.click_sel('.setupDlg button', text='プレイリストを見る')
    ui.until("!document.querySelector('.setupDlg') && document.body.dataset.screen==='recommend' && !document.body.dataset.busy", what='おすすめへ')
    ui.until("[...document.querySelectorAll('.presetTab')].some(e=>e.textContent==='チャレンジング')", what='セットのタブ')


def test_setup_shows_star_ranges(ui, fresh, monkeypatch):
    # 自己ベストの★から決めた範囲が3つのセットに入り、できたリストの欄に出る
    monkeypatch.setattr(ui.serve, 'auto_star_ranges', lambda est, model, modes: {'steady': (0, 5.7), 'standard': (5.1, 6.2), 'challenge': (5.7, 7.0)})
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='3つのリストができた')
    stars = ui.js("[...document.querySelectorAll('.setupSet .ss')].map(e=>e.textContent)")
    assert stars == ['★自動〜5.7', '★5.1〜6.2', '★5.7〜7.0']   # 着実の下限は自動（0）
    assert 'あなたの最近の自己ベストの★' in ui.js(TEXT)
    assert ui.js("document.querySelector('.setupRough').innerText").endswith('大まかな目安です。') or '全部クリアできた場合の数字' in ui.js(TEXT)
    assert ui.layout_issues() == []


def test_setup_write_all(ui, fresh):
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='3つのリストができた')
    ui.click_sel('.setupDlg button', text='Beat Saber に書き込む')
    ui.until(TEXT + ".includes('書き込みました')", timeout=15, what='書き込んだ知らせ')
    pl = ui.serve.SETTINGS.get('beat_saber_dir') + '/Playlists/'
    import os
    assert all(os.path.isfile(pl + n) for n in ('blnext-steady.bplist', 'blnext-standard.bplist', 'blnext-challenge.bplist'))


def test_setup_oneclick_all(ui, fresh, monkeypatch):
    s = ui.serve
    monkeypatch.setattr(s.oneclick, 'handler_app', lambda: 'BSManager')
    launched = []
    monkeypatch.setattr(s.oneclick, 'launch', launched.append)   # 本物の BSManager を起動しない
    monkeypatch.setattr(s, 'ONECLICK_WAIT', 0)
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='3つのリストができた')
    ui.click_sel('.setupDlg button', text='BSManager で入れる')
    ui.until(TEXT + ".includes('渡しました')", timeout=15, what='渡した知らせ')
    assert len(launched) == 4


def test_setup_later_and_reopen(ui, fresh):
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    ui.click_sel('.setupDlg button', text='あとで')
    ui.until("!" + DLG, what='閉じる')
    assert 'プレイヤーが未設定' in ui.js("document.getElementById('main').innerText")
    ui.click_sel('#main button', text='はじめの準備')
    ui.until("!!" + DLG, what='また開く')


def test_setup_search_messages(ui, fresh, monkeypatch):
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    search(ui, 'a')
    ui.until(TEXT + ".includes('2文字以上')", what='短すぎる')
    search(ui, 'だれもいない')
    ui.until(TEXT + ".includes('見つかりませんでした')", what='見つからない')
    search(ui, 'https://beatleader.com/u/%s?page=2' % fresh.id)   # プロフィールの URL をそのまま貼った
    ui.until("!!document.querySelector('.setupDlg .result')", what='URL の数字で見つかる')

    def offline(c, name):
        raise NetError('offline')
    monkeypatch.setattr(ui.serve.bl, 'search_players', offline)
    search(ui, 'テスト')
    ui.until(TEXT + ".includes('BeatLeader につながりません')", what='つながらない')
    assert ui.js("document.querySelectorAll('.setupDlg .result').length") == 0


def test_setup_failed_then_reselect_and_retry(ui, fresh):
    fresh.fake.error = 'offline'
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('取得できませんでした')", what='失敗の知らせ')
    assert 'BeatLeader につながりません' in ui.js(TEXT)
    assert ui.js("document.querySelector('#toast').className") == ''   # 小さな画面が出すので、下の知らせは出さない
    assert ui.serve.STORE.meta('setup_pending') == '1'
    fresh.fake.error = None
    ui.click_sel('.setupDlg button', text='もう一度')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='もう一度で進む')
    assert fresh.fake.started == 2


def test_setup_reselect(ui, fresh):
    fresh.fake.error = 'player-not-found'
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('取得できませんでした')", what='失敗の知らせ')
    ui.click_sel('.setupDlg button', text='プレイヤーを選び直す')
    ui.until("!!document.getElementById('setupQuery')", what='選ぶ所へ戻る')
    assert ui.serve.SETTINGS.get('player_id') == ''


def test_setup_cancel(ui, fresh):
    fresh.fake.total = 1000
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('比較対象のスコア')", what='進み具合')
    ui.click_sel('.setupDlg .setupCancel', text='やめる')
    ui.until(TEXT + ".includes('取得をやめました')", what='やめた知らせ')
    assert fresh.fake.cancelled
    ui.click_sel('.setupDlg button', text='閉じる（次に開いた時に続けます）')
    ui.until("!" + DLG, what='閉じる')
    # 開き直すと続きから（プレイヤーは選んだまま）
    fresh.fake.total = 3
    ui.serve.last_run_at, keep = (lambda pid: None), ui.serve.last_run_at
    try:
        ui.open('home')
        ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='続きから')
    finally:
        ui.serve.last_run_at = keep


def test_setup_resume_without_fetch(ui, fresh):
    # 取り終えた後に閉じていた（リストを作る前）: 開き直すとリストを作る所から。取り直さない
    ui.serve.SETTINGS.update({'player_id': fresh.id})
    ui.serve.STORE.set_meta('setup_pending', 1)
    ui.open('home')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='リストができた')
    assert fresh.fake.started == 0


def test_setup_no_lists(ui, fresh, monkeypatch):
    # ランク曲のスコアが無い人: リストを作れないことを知らせてホームへ
    monkeypatch.setattr(ui.serve, '_draft', lambda views: None)
    monkeypatch.setattr(ui.serve, '_lists', lambda view, *a, **k: (None, None))
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('まだプレイリストを作れません')", timeout=20, what='作れない知らせ')
    ui.click_sel('.setupDlg button', text='ホームへ')
    ui.until("!" + DLG, what='閉じる')
    assert ui.serve.STORE.meta('setup_pending') is None


def private_setup(ui, fresh, monkeypatch):
    """統計が非公開の人を選んだ所まで進める。返り値は BeatLeader の応答のふり（stats_public を変えると確かめの結果が変わる）"""
    row = fake_players(ui.serve, monkeypatch, fresh.id, public=False)
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('統計を公開にしてください')", what='案内')
    return row


def test_setup_stats_guide_skip(ui, fresh, monkeypatch):
    private_setup(ui, fresh, monkeypatch)
    text = ui.js(TEXT)
    assert 'Public history' in text and 'クールダウン' in text and '順位や PP は変わりません' in text
    assert fresh.fake.started == 0     # 取得はまだ（先に公開してもらえば、最初の取得で挑戦の記録も取れる）
    ui.click_sel('.setupDlg .setupSkipStats', text='このまま進む')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='進む')
    assert fresh.fake.started == 1 and ui.serve.STORE.meta('stats_public') == '0'


def test_setup_stats_turned_on(ui, fresh, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.serve, 'open_browser', opened.append)
    row = private_setup(ui, fresh, monkeypatch)
    ui.click_sel('.setupDlg .statsOpen', text='BeatLeader の設定を開く')
    ui.until("true")
    ui.wait(0.2)
    assert opened == ['https://beatleader.com/settings#scores']
    ui.click_sel('.setupDlg .statsCheck')
    ui.until(TEXT + ".includes('まだ非公開')", what='まだの知らせ')
    assert fresh.fake.started == 0
    row['stats_public'] = True
    ui.click_sel('.setupDlg .statsCheck')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='公開を確かめて進む')
    assert fresh.fake.started == 1 and ui.serve.STORE.meta('stats_public') == '1'


def test_setup_public_player_has_no_stats_step(ui, fresh, monkeypatch):
    fake_players(ui.serve, monkeypatch, fresh.id, public=True)
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    choose(ui)
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='案内なしで進む')
    assert ui.serve.STORE.meta('stats_public') == '1'


def test_home_stats_notice(ui, monkeypatch):
    s = ui.serve
    NOTICE = "!!document.querySelector('.statsNotice')"
    ui.open('home')
    assert not ui.js(NOTICE)                # 公開か分からない人には出さない
    s.STORE.set_meta('stats_public', 1)
    ui.open('home')
    assert not ui.js(NOTICE)
    s.STORE.set_meta('stats_public', 0)
    ui.open('home')
    assert ui.js(NOTICE)
    row = fake_players(s, monkeypatch, s.SETTINGS.get('player_id'), public=False)
    fake = FakeRefresher(total=2)
    monkeypatch.setattr(s, 'REFRESHER', fake)
    ui.click_sel('.statsNotice .statsHow', text='公開にする方法')
    ui.until("!!document.querySelector('.statsDlg')", what='案内の小さな画面')
    ui.click_sel('.statsDlg .statsCheck')
    ui.until("document.querySelector('.statsDlg').innerText.includes('まだ非公開')", what='まだの知らせ')
    row['stats_public'] = True
    ui.click_sel('.statsDlg .statsCheck')
    ui.until("!document.querySelector('.statsDlg') && !document.querySelector('.statsNotice')", what='閉じて知らせが消える')
    ui.wait(0.3)
    assert fake.started == 1                # 公開になったらすぐ取り直す
    assert s.STORE.meta('stats_public') == '1'


def test_setup_has_no_warning_after_lists(ui, fresh, monkeypatch):
    # 初回の準備には専用の案内があるので、リストを作っても書き込んでも、おすすめへ移っても警告を出さない
    private_setup(ui, fresh, monkeypatch)
    ui.click_sel('.setupDlg .setupSkipStats', text='このまま進む')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='3つのリストができた')
    ui.click_sel('.setupDlg button', text='Beat Saber に書き込む')
    ui.until(TEXT + ".includes('書き込みました')", timeout=15, what='書き込んだ知らせ')
    ui.wait(0.4)
    assert not ui.js("!!document.querySelector('.statsDlg')")
    ui.click_sel('.setupDlg button', text='プレイリストを見る')
    ui.until("!document.querySelector('.setupDlg') && document.body.dataset.screen==='recommend' && !document.body.dataset.busy", what='おすすめへ')
    ui.wait(0.4)
    assert not ui.js("!!document.querySelector('.statsDlg')")


def test_no_setup_for_existing_user(ui):
    ui.open('home')
    ui.wait(0.3)
    assert not ui.js("!!" + DLG)


def test_home_refresh_after_closing_continues_setup(ui, fresh):
    # 小さな画面を閉じた後に、ホームの「更新」で取り終えたら、リストを作る所から続ける
    ui.open('home')
    ui.until("!!" + DLG, what='小さな画面')
    fresh.fake.total = 1000
    choose(ui)
    ui.until(TEXT + ".includes('比較対象のスコア')", what='進み具合')
    ui.click_sel('.setupDlg .setupCancel', text='やめる')
    ui.until(TEXT + ".includes('取得をやめました')", what='やめた知らせ')
    ui.click_sel('.setupDlg button', text='閉じる（次に開いた時に続けます）')
    ui.until("!" + DLG, what='閉じる')
    fresh.fake.total = 3
    ui.click_sel('#refreshBtn')
    ui.until(TEXT + ".includes('準備ができました')", timeout=20, what='続ける')
