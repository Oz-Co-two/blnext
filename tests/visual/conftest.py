# 画面の比較の共通部品。撮るたびに同じ絵になるようにする:
#  - データは作り物（tests/_lib/fixture_data.py）を、毎回同じ場所（test-out/visual/_data）に作る（出力先の場所が画面に写るため）
#  - 計算に使う「いまの時刻」を決まった値にする（自分の曲線は直近のスコアを重く扱うので、時刻が進むと総PPの小数などが変わる）
#  - 画面ごとに、足したセット・設定・固定・外した曲・出力の記録を戻す。セットの id の番号も戻す（設定画面にファイル名として写る）
# 結果のまとめ（review.png・report.html）は最後に作る（pytest_sessionfinish）
import shutil, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import App   # noqa: E402
import vislib         # noqa: E402

FIXED_NOW = 1760000000   # 計算に使う「いまの時刻」（2025-10-09）
RESET_KEYS = ['playlist_size', 'played_ratio', 'neighbor_display', 'lang', 'modes', 'active_preset', 'ui_scale', 'stats_notice']


class FixedClock:
    """time モジュールの代わり。time() だけ決まった値を返す（ほかは本物）"""
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def __getattr__(self, name):
        import time
        return getattr(time, name)


def freeze_clock():
    from core.estimate import assemble, engine
    engine.time = assemble.time = FixedClock(FIXED_NOW)


@pytest.fixture(scope='session')
def app():
    import fixture_data
    vislib.prepare_out()
    data = vislib.OUT / '_data'
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    freeze_clock()   # 作り物のデータを作る途中の推定（前回の出力に記録する推定精度。ホームに出る）も同じ時刻で
    fixture_data.build(str(data))
    a = App(width=1280, height=900, data_dir=data)
    a.start()
    a.self_id = a.serve.SETTINGS.get('player_id')
    freeze_clock()
    st = a.serve.STORE
    a.base_rows = {'run': st.q1('SELECT COALESCE(MAX(run_id),0) AS n FROM recommendation_runs')['n'],
                   'export': st.q1('SELECT COALESCE(MAX(rowid),0) AS n FROM exports')['n']}
    yield a
    a.close()


def reset(a):
    """撮る前に、作り物のデータを作った直後の状態に戻す"""
    from core.settings import _presets_of
    s, st = a.serve.SETTINGS, a.serve.STORE
    for pid in [p for p in s.preset_ids() if p != 'p1']:   # 並べ替えて p1 が先頭でないこともある
        s.delete_preset(pid)
    s.reset(RESET_KEYS, preset='p1')
    s.update({'player_id': a.self_id})   # 初回の準備の画面を撮る時にプレイヤーを消す
    st.set_meta('setup_pending', None)
    st.set_meta('setup_sets', None)
    st.set_meta('stats_public', None)
    s.rename_preset('p1', 'スタンダード')
    s.set_cover('p1', None, 'png')
    raw = s._read_raw()
    if raw.pop('preset_seq', None) is not None:   # 次に足すセットを p2 からに（設定画面に「blnext-p2.bplist」などと写る）
        s._write(raw, _presets_of(raw))
    with st.tx():
        st.conn.execute('DELETE FROM user_marks')
        st.conn.execute('DELETE FROM rec_lists')   # 保存したおすすめのリスト（前の画面の操作で作り直した物を残さない）
        st.conn.execute('DELETE FROM exports WHERE rowid>?', (a.base_rows['export'],))
        for t in ('recommendation_items', 'reviews', 'recommendation_runs'):
            st.conn.execute('DELETE FROM %s WHERE run_id>?' % t, (a.base_rows['run'],))
    a.serve.SESSION.clear()
    a.serve._api_recommend_rebuild({'preset': 'p1'})   # 今のデータのリストを保存（無いと前回の出力の曲で作る＝serve._seed_list）


@pytest.fixture
def vis(app):
    reset(app)
    app.resize(1280, 900)
    app._events.clear()
    yield app
    errs = app.errors()
    assert not errs, 'ページのエラー:\n' + '\n'.join(errs)


def pytest_sessionfinish(session, exitstatus):
    vislib.write_report()
