# プレイリストの中の並び（画面の一覧の順＝書き出す順）。2026-10-18 に利用者が決めた:
#  - 基本の並び: 区分ごとに プレイ済み → 新曲 → 未クリア（挑戦したがクリアしていない）→ 挑戦（挑戦枠の曲。ほかの区分に当てはまっても最後）。
#    区分の中はクリアの見込みの高い順（見込みが無ければ一番上の扱い。同じなら並び順の値の高い順）
#  - 手で並べた順は、外す・固定・入れ替えなどの操作では保ち、新しく入った曲だけを基本の並びの位置に入れる（merge_order）。
#    作り直す・条件の変更では基本の並びに戻す
GROUPS = ('played', 'new', 'tried', 'challenge')


def group_of(c):
    """区分: played（プレイ済み）/ new（新曲）/ tried（未クリア）/ challenge（挑戦枠）"""
    if c.get('challenge'):
        return 'challenge'
    if c.get('kind') == 'played':
        return 'played'
    return 'tried' if c.get('tried') else 'new'


def _key(c):
    clear = c.get('clear')
    return GROUPS.index(group_of(c)), -(1.0 if clear is None else clear), -(c.get('score') or 0.0)


def default_order(items):
    """基本の並びにした新しいリスト（元のリストは変えない）"""
    return sorted(items, key=_key)


def merge_order(old_ids, items):
    """手で並べた順（old_ids。曲の ID の並び）を保ち、old_ids に無い曲だけを基本の並びの位置に入れる。
    基本の並びで直前にある曲（もう並べた曲）の後ろに入れ、無ければ先頭に入れる。old_ids にあって items に無い曲は落とす"""
    by = {c['leaderboard_id']: c for c in items}
    base = [c['leaderboard_id'] for c in default_order(items)]
    out = [i for i in dict.fromkeys(old_ids) if i in by]
    placed = set(out)
    for n, i in enumerate(base):
        if i in placed:
            continue
        prev = next((j for j in reversed(base[:n]) if j in placed), None)
        out.insert(out.index(prev) + 1 if prev is not None else 0, i)
        placed.add(i)
    return [by[i] for i in out]


def is_default(items):
    """基本の並びのままか（画面の「基本の並びに戻す」を押せるか）"""
    return [c['leaderboard_id'] for c in items] == [c['leaderboard_id'] for c in default_order(items)]
