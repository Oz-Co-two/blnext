# tools/check_players.py（ほかの人の公開データで初回の準備のリストを確かめる開発用の道具）。
# 本物の BeatLeader には出ない: 選び方（scan の比べ・対数の目盛りで分ける）は作り物の scan で、リストを調べる所（collect・judge）は
# 作り物の BeatLeader で初回の準備を通した serve の状態で確かめる（serve の中身を変えた時に道具が壊れていないか）
import json, os, sys, time

import pytest

import serve
from test_setup import newcomer, refresh   # noqa: F401（newcomer は fixture）
from test_serve_data import server          # noqa: F401（fixture）

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import check_players as cp   # noqa: E402


def row(i, rank, public, scores=200, days=1):
    return {'id': str(i), 'name': 'p%d' % i, 'countryRank': rank, 'rank': rank * 20, 'pp': 20000 - rank, 'scores': scores,
            'lastScore': int(time.time() - days * 86400), 'public': public}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'BASE', str(tmp_path))
    monkeypatch.setattr(cp, 'my_ids', lambda: {'999'})
    return tmp_path


def write_scan(base, date, rows):
    cp.save(os.path.join(str(base), 'scans', date + '.json'), {'at': 0, 'country': 'jp', 'players': rows})


def test_newly_public(base):
    write_scan(base, '2026-11-01', [row(1, 10, False), row(2, 20, True), row(3, 30, False)])
    now = [row(1, 11, True), row(2, 20, True), row(3, 30, False), row(4, 40, True)]   # 4 は前の scan にいない（分からない）
    assert cp.newly_public(now, '2026-11-15') == {'1': '2026-11-01'}
    assert cp.newly_public(now, '2026-11-01') == {}   # 同じ日・後の scan とは比べない


def test_candidates_and_pick(base):
    rows = [row(i, r, True) for i, r in enumerate((3, 5, 8, 12, 20, 40, 80, 150, 300, 700), start=1)]
    rows += [row(50, 4, True, days=30), row(51, 6, True, scores=5), row(52, 7, False), row(999, 9, True)]   # 遊んでいない・少ない・非公開・作者
    c = cp.candidates(rows, '2026-11-15')
    assert {r['id'] for r in c} == {str(i) for i in range(1, 11)}
    got = cp.pick(rows, 5, '2026-11-15')
    assert len(got) == 5 and len({r['id'] for r in got}) == 5
    ranks = [r['countryRank'] for r in got]
    assert ranks == sorted(ranks) and ranks[-1] >= 300   # 対数の目盛りで分けるので、下の方の人も入る（人数で分けると上位に偏る）
    # 新しく公開にした人を組の中で先に選ぶ
    write_scan(base, '2026-11-01', [{**row(6, 40, False)}])
    assert '6' in {r['id'] for r in cp.pick(rows, 5, '2026-11-15')}
    assert cp.pick(rows[:3], 5, '2026-11-15') == sorted(rows[:3], key=lambda r: r['countryRank'])   # 少なければ全員


def item(stars, kind='played', est=0.95, gain=5.0, clear=None, mode='Standard', challenge=False):
    return {'song': 's%.2f' % stars, 'difficulty': 'ExpertPlus', 'mode': mode, 'stars': stars, 'kind': kind, 'tried': False,
            'est': est, 'best': 0.94 if kind == 'played' else None, 'gain': gain, 'clear': clear, 'challenge': challenge, 'flags': []}


def one_set(level, stars, size=10, min_clear=0, **kw):
    its = [item(s, **kw) for s in stars]
    return {'name': level, 'level': level, 'size': size, 'songs': len(its), 'gain': sum(i['gain'] for i in its), 'minClear': min_clear,
            'candidates': 100, 'out': {}, 'items': its}


def test_judge():
    ok = {'me': {'pp': 10000}, 'ppCheck': {'ok': True}, 'sets': [one_set('steady', [5] * 10), one_set('standard', [6] * 10),
                                                                 one_set('challenge', [7] * 10)]}
    assert [f for f in cp.judge(ok) if f[0] != '情報'] == []
    bad = {'me': {'pp': 10000}, 'ppCheck': {'ok': False, 'calc': 1, 'api': 2}, 'dupSongs': {'h': ['着実', '標準']},
           'sets': [one_set('steady', [8] * 9, min_clear=80, clear=0.5), one_set('standard', [6] * 10),
                    {**one_set('challenge', []), 'songs': 0}]}
    msgs = [m for lv, m in cp.judge(bad) if lv == 'NG']
    assert any('PP の式' in m for m in msgs) and any('同じ曲' in m for m in msgs) and any('9 / 10' in m for m in msgs)
    assert any('0曲' in m for m in msgs) and any('難しさの順' in m for m in msgs) and any('クリアの見込みが下限' in m for m in msgs)
    big = {'me': {'pp': 1000}, 'sets': [one_set('steady', [5] * 9 + [4], gain=1.0) for _ in range(1)]}
    big['sets'][0]['items'][-1].update(kind='unplayed', est=0.995, gain=40.0)
    big['sets'][0]['gain'] = 49.0
    notes = [m for lv, m in cp.judge(big) if lv == '注意']
    assert any('1曲' in m for m in notes) and any('99%' in m for m in notes) and any('総PP の' in m for m in notes)


def test_collect_after_setup(server, newcomer):
    # 初回の準備（setup/start → 取得 → setup/lists）を通した後の serve から、道具が報告に要る物を取り出せる
    fake = newcomer
    assert serve._api_setup_start({'id': fake.self_id})['name']
    refresh(fake)
    serve._api_setup_lists({})
    res = {'player': fake.self_id, 'flags': []}
    cp.collect(serve, res)
    assert [s['level'] for s in res['sets']] == ['steady', 'standard', 'challenge']
    assert all(s['songs'] > 0 and s['model'] for s in res['sets'])
    assert res['scores']['n'] > 0 and 'softOnly' in res['attempts'] and 'n' in res['nfAttempts'] and res['me']['countryRank']
    # 作り物の BeatLeader は総PP を決めた値で返し（スコアの合計と合わない）、譜面も少ないので、PP の式のずれと曲数の不足の印は出てよい
    assert all(lv in ('NG', '注意', '情報') and isinstance(m, str) for lv, m in res['flags'])
    assert not any('同じ曲' in m or '例外' in m for _, m in res['flags'])
    json.dumps(res)   # 結果のファイルに書ける
    assert cp.judge(res) == res['flags']   # 報告を作り直す時（make_report）も同じ印になる
