# テストの共通設定。リポジトリ直下と tests/_lib を import できるようにし、serve.py の書き込み先を一時フォルダへ向ける
# （手元の config/ を汚さない。serve.py は import した時に BLN_DATA_DIR を読むので、import より前に設定する）。
# 画面のテスト（tests/e2e）は自分で作ったテスト用のデータフォルダを使う（tests/e2e/conftest.py）
import json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests', '_lib'))
os.environ.setdefault('BLN_DATA_DIR', tempfile.mkdtemp(prefix='bln-test-'))
os.environ['BLN_NO_NET'] = '1'


def pytest_collection_modifyitems(config, items):
    """分けて流す（tools/run_tests.py）: BLN_SHARD が「i/n」の時、集めたテストを前回の所要時間（BLN_SHARD_SECS の JSON。
    {"グループ/ファイル名::テスト名": 秒}）で n 組に分け、i 番目の組だけを流す。長いテストから順に、今いちばん短い組へ入れる。
    どのプロセスも同じ並び・同じ秒数から同じ分け方を求めるので、全部の組を合わせるとどのテストもちょうど1回流れる。
    秒数の無いテスト（新しく足した物）は、ある物の中央値とみなす。テストは ui の準備で毎回状態を戻すので、どの組に入っても結果は同じ"""
    shard = os.environ.get('BLN_SHARD')
    if not shard:
        return
    i, n = (int(x) for x in shard.split('/'))
    try:
        with open(os.environ.get('BLN_SHARD_SECS', ''), encoding='utf-8') as f:
            secs = json.load(f)
    except (OSError, ValueError):
        secs = {}
    keys = ['%s/%s::%s' % (it.path.parent.name, it.path.name, it.name) for it in items]
    known = sorted(v for v in (secs.get(k) for k in keys) if isinstance(v, (int, float)))
    default = known[len(known) // 2] if known else 1.0
    cost = [secs.get(k, default) if isinstance(secs.get(k), (int, float)) else default for k in keys]
    bins = [[0.0, b] for b in range(n)]
    mine = set()
    for k in sorted(range(len(items)), key=lambda k: (-cost[k], k)):
        b = min(bins, key=lambda x: (x[0], x[1]))
        b[0] += cost[k]
        if b[1] == i:
            mine.add(k)
    drop = [it for k, it in enumerate(items) if k not in mine]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = [it for k, it in enumerate(items) if k in mine]
