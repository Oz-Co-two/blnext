"""推定（core/estimate）の結果を CSV に出して目で確かめる開発用の道具（仕様書「推定はまず結果をCSVで出して目で確かめ」）。

使い方: .venv-build/Scripts/python.exe -X utf8 tools/estimate_csv.py [--data <書き込み先>] [--out <CSV>] [--preset p2]
  取得済みの DB（--data の data/blnext.db）を読む。CSV は Excel で開けるよう BOM 付き UTF-8。
  計算の設定は --preset のセット（省略時は画面で開いているセット）の物を使う。
"""
import argparse, csv, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=ROOT)
    ap.add_argument('--out', default='')
    ap.add_argument('--preset', default=None)
    a = ap.parse_args()
    data = os.path.abspath(a.data)
    from core.settings import Settings
    from core.store.db import Store
    from core.estimate import model as M, recommend as R
    st = Store(os.path.join(data, 'data', 'blnext.db'))
    s = Settings(os.path.join(data, 'config')).view(a.preset)
    print('セット:', s.preset)
    now = int(time.time())
    t0 = time.time()
    m = M.build(st, s, now)
    t1 = time.time()
    if m is None:
        sys.exit('データがありません（先に取得してください）')
    rows = R.estimate(st, m, s, now)
    t2 = time.time()
    print('曲線・類似度 %.2fs / 推定 %.2fs / 候補 %d' % (t1 - t0, t2 - t1, len(rows)))
    print('比較対象 %d人（スコア不足で除外 %d人）/ 共通曲が足りた人 %d / 似ている人 %d' % (
        m.pool_size, m.skipped_few_scores, sum(1 for p in m.pool if p.sim is not None), len(m.similar)))
    for p in m.similar[:10]:
        print('  %-20s sim=%.3f corr=%.3f 共通=%d 国内#%d style=%.3f' % (p.name[:20], p.sim, p.corr, p.overlap, p.country_rank, p.style_sim or 0))
    xs = [3, 5, 7, 9, 11]
    print('自分の曲線', {x: round(float(m.me.curve(x)), 4) for x in xs})
    out = a.out or os.path.join(data, 'exports', 'estimate-%s.csv' % time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # gain は推定の幅で見た増える総PP の期待値、p_up は自己ベストを超える見込み、gain_up は超えた時の増えの平均（recommend.py の SPREAD）
    cols = ['kind', 'song', 'difficulty', 'mode', 'stars', 'best_acc', 'base_acc', 'est_acc', 'best_pp', 'est_pp', 'gain', 'p_up', 'gain_up',
            'rank_up', 'confidence', 'clear', 'score', 'similar_n', 'flags', 'refs', 'upload_time', 'leaderboard_id']
    with open(out, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([('|'.join(r[c]) if c == 'flags' else
                         '; '.join('%s %.2f%%(%+.2f)' % (x['name'], 100 * (x['acc'] or 0), 100 * x['residual']) for x in r['refs']) if c == 'refs' else
                         round(r[c], 5) if isinstance(r[c], float) else r[c]) for c in cols])
    print('CSV:', out)
    print('\n上位20件（並び順の値＝増える総PP の期待値×確からしさ×クリアの見込み）')
    for r in rows[:20]:
        print('  %-8s %-34s %-10s ★%5.2f 自己ベスト %6s → 推定 %6.2f%%  +%6.2fPP（超える %4s） 国内+%d 確か%.2f %s' % (
            r['kind'], (r['song'] or '')[:34], r['difficulty'], r['stars'],
            '%.2f%%' % (100 * r['best_acc']) if r['best_acc'] else '—', 100 * r['est_acc'], r['gain'],
            '%.0f%%' % (100 * r['p_up']) if r['p_up'] is not None else '—', r['rank_up'], r['confidence'], ','.join(r['flags'])))


if __name__ == '__main__':
    main()
