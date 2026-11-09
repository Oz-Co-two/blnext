# 設定画面のプレイヤー（名前で探して選ぶ・IDを直接入れる）・グループごとに初期値に戻す・取得中の帯（進み具合・やめる・失敗の知らせ）。
# BeatLeader の検索と取得はふりをする（ネットに出ない）
import pytest

from conftest import FakeRefresher
from core.settings import SPEC

TOAST = "document.querySelector('#toast').innerText"
STRIP = "document.getElementById('strip')"
QUERY = "document.querySelector('.playerBox input:not(#set_player_id)')"
RESULTS = "document.querySelector('.results').innerText"
FAKE_ID = '90000000000000001'


class FakeRefresher:
    """取得のふり。進み具合を聞かれるたびに1つ進む（最初の2回は件数の分からない段階）。total まで行くと終わる"""

    def __init__(self, total=1000, error=None):
        self.total, self.error = total, error
        self.started = self.done = 0
        self.running = self.cancelled = False

    def start(self):
        self.started += 1
        self.running, self.cancelled, self.done = True, False, 0
        return self.progress()

    def cancel(self):
        self.running, self.cancelled = False, True
        return self.progress()

    def progress(self):
        if self.running:
            self.done += 1
            self.running = self.done < self.total
        known = self.done > 2
        return {'running': self.running, 'phase': 'pool' if known else 'self', 'done': self.done if known else 0,
                'total': self.total if known else 0, 'error': None if self.running or self.cancelled else self.error,
                'warnings': [], 'cancelled': self.cancelled}


@pytest.fixture
def keep_player(ui):
    keep = {k: ui.serve.SETTINGS.get(k) for k in ('player_id', 'country')}
    yield
    ui.serve.SETTINGS.update(keep, trusted=True)


def search(ui, name):
    ui.js("(()=>{const q=%s; q.value=%r;})()" % (QUERY, name))
    ui.click_sel('.playerBox button', text='探す')


def test_player_search_and_choose(ui, monkeypatch, keep_player):
    rows = [{'player_id': FAKE_ID, 'name': 'テスト太郎', 'country': 'jp', 'pp': 12345.678, 'rank': 1234,
             'country_rank': 56, 'avatar': ''}]
    monkeypatch.setattr(ui.serve.bl, 'search_players', lambda client, name: rows if name == 'テスト' else [])
    fake = FakeRefresher(total=1)
    monkeypatch.setattr(ui.serve, 'REFRESHER', fake)
    ui.open('settings')
    search(ui, 'a')
    ui.until(RESULTS + ".includes('2文字以上')", what='短すぎる知らせ')
    search(ui, 'だれもいない')
    ui.until(RESULTS + ".includes('見つかりませんでした')", what='見つからない知らせ')
    search(ui, 'テスト')
    ui.until("!!document.querySelector('.result')", what='見つかった人')
    text = ui.js("document.querySelector('.result').innerText")
    assert 'テスト太郎' in text and 'JP' in text and '世界 #1234' in text and '12,345.68 PP' in text
    ui.click_sel('.result button', text='この人にする')
    ui.until(TOAST + ".includes('テスト太郎 さんにしました')", what='選んだ知らせ')
    assert ui.serve.SETTINGS.get('player_id') == FAKE_ID and ui.serve.SETTINGS.get('country') == 'jp'
    assert ui.js("document.getElementById('set_player_id').value") == FAKE_ID and ui.js(RESULTS) == ''
    assert fake.started == 1   # 選ぶとすぐ取得を始める


def test_player_id_typed_starts_refresh(ui, monkeypatch, keep_player):
    fake = FakeRefresher(total=1)
    monkeypatch.setattr(ui.serve, 'REFRESHER', fake)
    ui.open('settings')
    ui.js("(()=>{const e=document.getElementById('set_player_id'); e.value=%r; e.dispatchEvent(new Event('change'));})()" % FAKE_ID)
    ui.until("document.getElementById('set_player_id').classList.contains('saved')", what='保存の印')
    # 取得は保存の印の後に画面が頼む（別の通信）ので、届くまで待つ（重い時に、保存の印だけ見て確かめると間に合わなかった）
    import time
    end = time.time() + 10
    while fake.started < 1 and time.time() < end:
        time.sleep(0.05)
    assert ui.serve.SETTINGS.get('player_id') == FAKE_ID and fake.started == 1


def test_reset_group(ui):
    ui.open('settings')
    ui.js("(()=>{const e=document.getElementById('set_playlist_size'); e.value='12'; e.dispatchEvent(new Event('change'));})()")
    ui.until("document.getElementById('set_playlist_size').classList.contains('saved')", what='保存の印')
    assert ui.serve.SETTINGS.get('playlist_size') == 12
    ui.js("document.getElementById('set_playlist_size').closest('section').querySelector('.cardHead button').dataset.t='reset'")
    ui.click_sel('[data-t=reset]', text='初期値に戻す')
    ui.until(TOAST + ".includes('初期値に戻しました')", what='戻した知らせ')
    default = SPEC['playlist_size']['default']
    assert ui.serve.SETTINGS.get('playlist_size') == default
    assert ui.js("document.getElementById('set_playlist_size').value") == str(default)   # 画面も描き直している


def test_unused_items_are_hidden(ui):
    # 今は使っていない項目は画面に出さない。設定の仕組みは残すので、前の版で変えた値は「初期値に戻す」でも消えない
    pd = 'C:\\BS\\PlayerData.dat'
    ui.serve.SETTINGS.update({'fetch_song_details': False, 'playerdata_path': pd, 'auto_refresh_hours': 3}, trusted=True)
    ui.open('settings')
    keys = ui.js("[...document.querySelectorAll('.row[data-key]')].map(r=>r.dataset.key)")
    assert {'beat_saber_dir', 'auto_refresh_hours', 'update_check', 'stats_notice'} <= set(keys)
    assert not {'playerdata_path', 'fetch_song_details', 'lang'} & set(keys)
    text = ui.js("document.getElementById('setCommon').innerText")
    assert 'PlayerData' not in text and '全曲データ' not in text and '今は使っていません' not in text
    ui.click_sel('#grp_net .cardHead button', text='初期値に戻す')
    ui.until(TOAST + ".includes('初期値に戻しました')", what='戻した知らせ')
    assert ui.serve.SETTINGS.get('auto_refresh_hours') == SPEC['auto_refresh_hours']['default']   # 画面に出ている項目は戻る
    assert ui.serve.SETTINGS.get('fetch_song_details') is False and ui.serve.SETTINGS.get('playerdata_path') == pd


def test_refresh_strip_cancel_and_error(ui, monkeypatch):
    fake = FakeRefresher()
    monkeypatch.setattr(ui.serve, 'REFRESHER', fake)
    ui.open('home')
    ui.click_sel('#refreshBtn')
    ui.until(STRIP + ".classList.contains('on')", what='取得中の帯が出る')
    assert ui.js("document.getElementById('refreshBtn').disabled")   # 取得中は押せない
    ui.until(STRIP + ".innerText.includes('比較対象のスコア')", what='段階と人数が出る')
    assert '1000人中' in ui.js(STRIP + ".innerText")
    ui.click_sel('#strip button', text='やめる')
    ui.until("!" + STRIP + ".classList.contains('on')", what='やめると帯が消える')
    assert fake.cancelled
    ui.wait_ready()   # 取得が終わるとホームを描き直す
    ui.until("!document.getElementById('refreshBtn').disabled", what='更新ボタンがまた押せる')
    fake.total, fake.error = 4, 'offline'   # 失敗して終わる
    ui.click_sel('#refreshBtn')
    ui.until(TOAST + ".includes('BeatLeader につながりません')", what='失敗の知らせ')
    assert not ui.js(STRIP + ".classList.contains('on')")


def test_maps_refetch_button(ui, monkeypatch):
    # 設定の一番下: BeatLeader から全曲データを取り直すボタンと警告。押すと取り直す印を付けて取得を始め、続けて押せない
    fake = FakeRefresher(total=1000)
    monkeypatch.setattr(ui.serve, 'REFRESHER', fake)
    ui.serve.STORE.set_meta('maps_fetched_at', 1760000000)
    ui.open('settings')
    assert ui.js("document.querySelector('.setBody').lastElementChild.id") == 'grp_maps'
    warn = ui.js("document.querySelector('#grp_maps .mapsWarn').innerText")
    assert 'ふだんは押さないでください' in warn and '何度も押さないでください' in warn and 'kinsi55' in warn
    assert ui.js("document.querySelectorAll('#grp_maps .mapsWhen .when').length") == 3
    assert ui.layout_issues() == []
    ui.click_sel('.mapsRefetch')
    ui.until("document.querySelector('.mapsRefetch').disabled", what='ボタンが押せなくなる')
    assert fake.started == 1 and ui.serve.STORE.meta('maps_fetched_at') is None


def test_maps_refetch_while_fetching(ui, monkeypatch):
    # 取得の途中は印を付けず、その旨を出す（もう一度押せる）
    fake = FakeRefresher(total=1000)
    fake.start()
    monkeypatch.setattr(ui.serve, 'REFRESHER', fake)
    ui.serve.STORE.set_meta('maps_fetched_at', 1760000000)
    ui.open('settings')
    ui.click_sel('.mapsRefetch')
    ui.until("document.querySelector('.mapsMsg').innerText.includes('取得の途中です')", what='取得の途中の知らせ')
    assert ui.serve.STORE.meta('maps_fetched_at') == '1760000000' and not ui.js("document.querySelector('.mapsRefetch').disabled")
