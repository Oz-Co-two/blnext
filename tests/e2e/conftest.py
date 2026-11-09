# 画面のテストの共通部品。ヘッドレスの Edge を1つ起動して使い回し（tools/cdp.py の App）、テストごとに状態を戻す。
# データは tests/_lib/fixture_data.py の作り物（ネットに出ない）。BLN_JS_COVERAGE=1 なら JS のカバレッジも測って test-out/coverage/ へ
import os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cdp import App, SCREENS, WIDTHS   # noqa: E402

OUT = ROOT / 'test-out'
CHANGED_KEYS = ['playlist_size', 'played_ratio', 'neighbor_display', 'lang', 'modes', 'active_preset', 'ui_scale', 'star_min', 'star_max', 'stats_notice',
                'fetch_song_details', 'playerdata_path', 'auto_refresh_hours']


def many_passed(serve, monkeypatch, n_by=25, n_passed=9):
    """ホームの「前回からの変化」の抜かれた人を n_by 人（#101〜。長い名前も）、抜いた人を n_passed 人にする（作り物のデータでは少ないので）"""
    real = serve.views.home

    def home(store, settings):
        d = real(store, settings)
        d['passedBy'] = [{'name': 'とても長いプレイヤーの名前_%02d' % i if i % 5 == 0 else 'Player_%02d' % i, 'countryRank': 101 + i, 'pp': 0}
                         for i in range(n_by)]
        d['passed'] = [{'name': 'Passed_%02d' % i, 'countryRank': 201 + i, 'pp': 0} for i in range(n_passed)]
        return d
    monkeypatch.setattr(serve.views, 'home', home)


def since_export(serve):
    """一番新しい出力を、作り物のデータの1回目の取得の時刻にする（ホームの上下の人の表とタイルに「前回の出力から」が出る。2回目の
    取得で自分の PP と1人の PP が変わっている）。作り物のデータの出力は1回目の取得より前なので、そのままでは比べる元が無い。
    足した出力の記録は ui の後始末で消える。返り値は比べる元の取得の時刻"""
    st = serve.STORE
    first = st.q1('SELECT MIN(run_at) AS t FROM player_snapshots')['t']
    with st.tx():
        st.conn.execute("INSERT INTO exports(run_id, exported_at, path, method) VALUES(1, ?, 'x', 'write')", (first,))
    return first


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


def fake_players(serve, monkeypatch, pid, name='テスト太郎', public=None):
    """BeatLeader の検索と ID での引きのふり（ネットに出ない）。「テスト」で探すと pid の人が1人だけ見つかる。ほかの ID は 404"""
    from core.fetch.http import NetError
    row = {'player_id': pid, 'name': name, 'country': 'jp', 'pp': 12345.678, 'rank': 1234, 'country_rank': 56, 'avatar': '', 'stats_public': public}

    def player(client, p):
        if p != pid:
            raise NetError('http-404')
        return row
    monkeypatch.setattr(serve.bl, 'search_players', lambda client, q: [row] if q == 'テスト' else [])
    monkeypatch.setattr(serve.bl, 'player', player)
    return row


class FakeUpdater:
    """アプリの更新のふり（core/app_update.py の Updater の口）。ダウンロードは進み具合を聞かれるたびに進み、steps 回で final になる"""
    just_updated = None

    def __init__(self):
        self.newer, self.auto, self.check_error, self.apply_error = True, 'ok', None, None
        self.steps, self.final = 3, {'state': 'ready'}
        self.checked = self.started = self.n = 0
        self.applied, self.cancelled = [], False

    def check(self):
        self.checked += 1
        if self.check_error:
            from core.app_update import UpdateError
            raise UpdateError(self.check_error)
        return {'current': 'v0.1.0', 'latest': 'v0.3.0' if self.newer else 'v0.1.0', 'newer': self.newer, 'size': 12 * 1048576, 'auto': self.auto,
                'notes': [{'version': 'v0.3.0', 'ja': ['新しい機能', '外から来た文字 <img src=x onerror="window.__xss=1">', 'とても長い見出し' * 12], 'en': []},
                          {'version': 'v0.2.0', 'ja': ['その前の機能'], 'en': []}] if self.newer else []}

    def start_download(self):
        self.started += 1
        self.n, self.cancelled = 0, False
        return self.progress()

    def progress(self):
        self.n += 1
        if self.cancelled:
            return {'state': 'cancel'}
        if self.n > self.steps:
            return dict(self.final)
        return {'state': 'download', 'version': 'v0.3.0', 'done': self.n * 1048576, 'total': 12 * 1048576}

    def cancel(self):
        self.cancelled = True
        return self.progress()

    def apply(self, lang):
        if self.apply_error:
            from core.app_update import UpdateError
            raise UpdateError(self.apply_error)
        self.applied.append(lang)
        return {}


@pytest.fixture(scope='session')
def app():
    a = App(width=1280, height=900, js_coverage=os.environ.get('BLN_JS_COVERAGE') == '1')
    a.start()
    a.self_id = a.serve.SETTINGS.get('player_id')
    # 作り物のデータを作った直後の設定ファイル。ui がテストごとにこれへ戻す（CHANGED_KEYS の外の項目・p1 の難しさやクールダウンを
    # 画面で変えたままのテストがあっても、次のテストに残さない。2026-11-09 に分けて流すようにした時、前のテストの戻し忘れに
    # 頼っていたテストが組によって落ちた）
    a.base_settings = open(a.serve.SETTINGS.path, encoding='utf-8').read()
    st = a.serve.STORE   # 作り物のデータを作った直後の出力の記録（テストで出力した分は ui が消す）
    a.base_rows = {'run': st.q1('SELECT COALESCE(MAX(run_id),0) AS n FROM recommendation_runs')['n'],
                   'export': st.q1('SELECT COALESCE(MAX(rowid),0) AS n FROM exports')['n']}
    yield a
    a.close()
    if a.js_coverage:
        a.save_coverage(OUT / 'coverage' / 'js_e2e.json')


@pytest.fixture
def ui(app):
    """テストごとに、足したセット（並べ替えた順も。p1 のほかを消す）・変えた設定・セットの名前と画像・固定・今後出さない・外した曲・
    保存したおすすめのリスト・テストで出力した記録（ホームの振り返りと「前回の出力から」が前のテストの出力に左右されないように）を
    戻し、幅を 1280 にする"""
    s = app.serve
    for pid in [p for p in s.SETTINGS.preset_ids() if p != 'p1']:
        s.SETTINGS.delete_preset(pid)
    s.SETTINGS.reset(CHANGED_KEYS, preset='p1')
    s.SETTINGS.update({'player_id': app.self_id, 'country': 'jp'})   # 初回の準備のテストがプレイヤーを消すことがある
    s.STORE.set_meta('setup_pending', None)
    s.STORE.set_meta('setup_sets', None)
    s.STORE.set_meta('stats_public', None)
    s.SETTINGS.rename_preset('p1', 'スタンダード')
    s.SETTINGS.set_cover('p1', None, 'png')
    with open(s.SETTINGS.path, 'w', encoding='utf-8') as f:   # 上で戻しきれない項目も、始めた時の設定ファイルのとおりに
        f.write(app.base_settings)
    with s.STORE.tx():
        s.STORE.conn.execute('DELETE FROM user_marks')
        s.STORE.conn.execute('DELETE FROM rec_lists')
        s.STORE.conn.execute('DELETE FROM exports WHERE rowid>?', (app.base_rows['export'],))
        for t in ('recommendation_items', 'reviews', 'recommendation_runs'):
            s.STORE.conn.execute('DELETE FROM %s WHERE run_id>?' % t, (app.base_rows['run'],))
    s.SESSION.clear()
    s._api_recommend_rebuild({'preset': 'p1'})   # 今のデータのリストを保存（無いと前回の出力の曲で作る＝serve._seed_list）
    app.resize(1280, 900)
    app._events.clear()
    yield app
    errs = app.errors()
    assert not errs, 'ページのエラー:\n' + '\n'.join(errs)
