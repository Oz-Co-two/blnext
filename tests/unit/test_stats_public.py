# 統計の公開の印（core/fetch/beatleader.py の stats_public・player）
import pytest

from core.fetch import beatleader as bl


@pytest.mark.parametrize('raw,want', [
    ({'profileSettings': {'showStatsPublic': True}}, True),
    ({'profileSettings': {'showStatsPublic': False}}, False),
    ({'profileSettings': {'showStatsPublic': 'true'}}, None),   # 形が違えば分からない扱い
    ({'profileSettings': {}}, None),
    ({'profileSettings': None}, None),
    ({}, None),
    (None, None),
])
def test_stats_public(raw, want):
    assert bl.stats_public(raw) is want


def test_player_asks_with_stats_true():
    # stats=false は公開の人にも false を返すので、必ず stats=true で聞く（2026-11-06 に作者の分で確かめた）
    calls = []

    class C:
        def get(self, path, **q):
            calls.append((path, q))
            return {'id': '1', 'name': 'x', 'profileSettings': {'showStatsPublic': True}}
    r = bl.player(C(), '1')
    assert calls == [('/player/1', {'stats': 'true'})]
    assert r['player_id'] == '1' and r['stats_public'] is True
