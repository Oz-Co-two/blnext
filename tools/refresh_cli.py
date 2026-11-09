"""取得（core/fetch/refresh.py）をコマンドラインから動かす開発用の道具。進捗を表示し、終わったらホームの内容の要約を出す。

使い方: .venv-build/Scripts/python.exe -X utf8 tools/refresh_cli.py [--data <書き込み先>] [--player <ID>]
  --data を省くとリポジトリ直下（アプリの開発中と同じ config/ data/）。--player を渡すとその書き込み先の設定に入れる。
"""
import argparse, os, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=ROOT)
    ap.add_argument('--player', default='')
    a = ap.parse_args()
    os.environ['BLN_DATA_DIR'] = os.path.abspath(a.data)
    import serve
    from core import views
    if a.player:
        serve.SETTINGS.update({'player_id': a.player})
    t0 = time.time()
    serve.REFRESHER.start()
    last = None
    while True:
        p = serve.REFRESHER.progress()
        line = '%5.1fs %-10s %d/%d req=%d skip=%d' % (time.time() - t0, p['phase'], p['done'], p['total'], p['requests'], p['skipped'])
        if line != last:
            print(line, flush=True)
            last = line
        if not p['running'] and p['finishedAt']:
            break
        time.sleep(0.5)
    print('error=%s cancelled=%s warnings=%s' % (p['error'], p['cancelled'], p['warnings']))
    h = views.home(serve.STORE, serve.SETTINGS)
    if h.get('hasData'):
        me = h['me']
        print('国内 %s位 / 世界 %s位 / %.2fPP（計算し直した総PP %s）' % (me['countryRank'], me['rank'], me['pp'], h['ppCheck']['calc']))
        print('目標', h['target'])
        print('上下の人', len(h['neighbors']), '人 / 分布の人数', len(h['poolPp']), '/ 伸ばした曲', len(h['improved']),
              '/ 抜いた', len(h['passed']), '/ 抜かれた', len(h['passedBy']))
    st = serve.STORE
    for t in ('players', 'leaderboards', 'scores', 'attempts', 'self_history', 'player_snapshots', 'score_history'):
        print(t, st.q1('SELECT COUNT(*) AS n FROM %s' % t)['n'])


if __name__ == '__main__':
    main()
