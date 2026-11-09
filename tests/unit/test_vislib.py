# 画面の比較の部品（tests/visual/vislib.py）: 基準との比べ方・基準の作り方と置き換え・まとめ（review.png・report.html）
import json, sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'visual'))
import vislib   # noqa: E402


@pytest.fixture
def vl(tmp_path, monkeypatch):
    monkeypatch.setattr(vislib, 'BASE', tmp_path / 'baseline')
    monkeypatch.setattr(vislib, 'OUT', tmp_path / 'out')
    monkeypatch.setattr(vislib, 'ACCEPT', False)
    vislib.prepare_out()
    return vislib


def shot(vl, name, arr):
    Image.fromarray(np.asarray(arr, dtype=np.uint8)).save(vl.current_path(name))


def img(h=40, w=60, color=(30, 30, 30)):
    a = np.zeros((h, w, 3), np.uint8)
    a[:] = color
    return a


def test_first_time_makes_baseline_then_same(vl):
    shot(vl, 'a', img())
    assert vl.compare('a')['status'] == 'new' and (vl.BASE / 'a.png').exists()
    shot(vl, 'a', img())
    assert vl.compare('a')['status'] == 'same'


def test_small_noise_is_same_and_real_change_is_changed(vl):
    shot(vl, 'a', img())
    vl.compare('a')
    b = img()
    b[0, :24] = (30 + vl.THRESH + 1, 30, 30)     # 24画素だけ変化 → 同じとみなす
    b[1, :] = (30 + vl.THRESH, 30, 30)          # 差がちょうど THRESH の画素は変化に数えない
    shot(vl, 'a', b)
    r = vl.compare('a')
    assert r['status'] == 'same' and r['pixels'] == 24
    b[2, :1] = (255, 255, 255)                  # 25画素目 → 変化
    shot(vl, 'a', b)
    r = vl.compare('a')
    assert r['status'] == 'changed' and r['pixels'] == 25
    assert (vl.OUT / 'before' / 'a.png').exists()
    d = np.asarray(Image.open(vl.OUT / 'diff' / 'a.png').convert('RGB'))
    assert tuple(d[2, 0]) == (255, 40, 40) and tuple(d[10, 10]) == (9, 9, 9)   # 変わった所は赤、ほかは暗く


def test_size_change_is_changed(vl):
    shot(vl, 'a', img(h=40))
    vl.compare('a')
    shot(vl, 'a', img(h=50))
    r = vl.compare('a')
    assert r['status'] == 'changed' and '大きさが変わった' in r['note'] and r['pixels'] == 10 * 60


def test_accept_replaces_baseline(vl, monkeypatch):
    shot(vl, 'a', img())
    vl.compare('a')
    shot(vl, 'a', img(color=(200, 0, 0)))
    monkeypatch.setattr(vl, 'ACCEPT', True)
    assert vl.compare('a')['status'] == 'accepted'
    monkeypatch.setattr(vl, 'ACCEPT', False)
    assert vl.compare('a')['status'] == 'same'


def test_report_only_when_changed(vl):
    for n in ('a', 'b'):
        shot(vl, n, img())
        vl.compare(n)
    vl.RESULTS.clear()
    shot(vl, 'a', img())
    vl.compare('a', 'ホーム')
    assert vl.write_report() == [] and not (vl.OUT / 'review.png').exists()   # 変化が無ければまとめは作らない
    shot(vl, 'b', img(color=(0, 200, 0)))
    vl.compare('b', '<おすすめ>')
    assert vl.write_report() == ['b']
    assert Image.open(vl.OUT / 'review.png').width > 0
    page = (vl.OUT / 'report.html').read_text(encoding='utf-8')
    assert 'before/b.png' in page and 'current/b.png' in page and 'diff/b.png' in page
    assert '&lt;おすすめ&gt;' in page and '<おすすめ>' not in page        # 題名は文字のまま（タグにならない）
    res = json.loads((vl.OUT / 'results.json').read_text(encoding='utf-8'))
    assert [r['status'] for r in res] == ['same', 'changed']
