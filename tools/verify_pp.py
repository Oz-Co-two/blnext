"""core/estimate/ppcalc.py の検証: BeatLeader API の実スコアと計算結果を比べる（仕様書: 95%のスコアで差0.5PP以内）。

使い方:  .venv-build/Scripts/python.exe -X utf8 tools/verify_pp.py [--player ID] [--refresh] [--tol 0.5] [--double] [--mods]
  PP は倍精度で計算する（上流は一部が単精度。差は 0.003PP 以内）。--double は総PP の足し算だけに効く。
  --player を省くと config/settings.json の player_id を使う。
  初回は API から取得して devdata/pp_scores/ に保存し（.gitignore 済み）、2回目以降は保存したものを使う（--refresh で取り直し）。
  BeatLeader の計算式（Utils/ReplayUtils.cs）が変わった時に、移植を直した後でこれを流す。
  API のマナー: User-Agent blnext-dev/0.0、1秒に2回まで、同時1本。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.estimate import ppcalc  # noqa: E402

API = "https://api.beatleader.com"
UA = "blnext-dev/0.0"
MIN_INTERVAL = 0.5  # 秒（1秒に2回まで）
_last_request = 0.0


def http_get(url: str) -> bytes:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    finally:
        _last_request = time.monotonic()


def cached(path: Path, url: str, refresh: bool) -> dict:
    if path.exists() and not refresh:
        return json.loads(path.read_bytes())
    print(f"  取得: {url}", file=sys.stderr)
    body = http_get(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return json.loads(body)


def load_all(player_id: str, raw_dir: Path, refresh: bool) -> tuple[list[dict], dict]:
    scores: list[dict] = []
    page = 1
    while True:
        url = f"{API}/player/{player_id}/scores?type=ranked&sortBy=date&count=100&page={page}"
        d = cached(raw_dir / f"page_{page}.json", url, refresh)
        data = d.get("data") or []
        scores.extend(data)
        total = (d.get("metadata") or {}).get("total") or 0
        if not data or page * 100 >= total:
            break
        page += 1
    player = cached(raw_dir / "player.json", f"{API}/player/{player_id}", refresh)
    return scores, player


def fmt_score(s: dict, calc: dict) -> str:
    diff = s["leaderboard"]["difficulty"]
    song = s["leaderboard"]["song"]
    return (
        f"id={s['id']} {song['name'][:28]!r} {diff['difficultyName']}/{diff['modeName']} "
        f"status={diff.get('status')} mods={s.get('modifiers') or '-'} acc={s['accuracy']:.5f} "
        f"A/P/T={diff.get('accRating')}/{diff.get('passRating')}/{diff.get('techRating')} "
        f"modRating={'有' if diff.get('modifiersRating') else '無'} "
        f"api_pp={s['pp']:.4f} calc={calc['pp']:.4f} diff={calc['pp'] - s['pp']:+.4f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", default="")
    ap.add_argument("--refresh", action="store_true", help="保存済みの応答を使わず API から取り直す")
    ap.add_argument("--tol", type=float, default=0.5)
    ap.add_argument("--double", action="store_true", help="総PP の足し算を単精度の再現をせず倍精度で行う")
    ap.add_argument("--show", type=int, default=15, help="ずれた例を何件表示するか")
    ap.add_argument("--mods", action="store_true",
                    help="補助検証: 他のプレイヤーのモディファイア付きランクスコアでも比べる（devdata/pp_scores/mods/ に保存）")
    args = ap.parse_args()
    f32 = not args.double

    if not args.player:
        try:
            args.player = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8")).get("player_id", "")
        except (OSError, ValueError):
            pass
    if not args.player:
        ap.error("--player を指定するか、設定でプレイヤーIDを入れてください")
    raw_dir = ROOT / "devdata" / "pp_scores"
    scores, player = load_all(args.player, raw_dir, args.refresh)

    # 同じスコアが2ページにまたがって重複する可能性（取得中に新しいスコアが入った場合）を除く
    seen: set = set()
    uniq = []
    for s in scores:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        uniq.append(s)
    dup = len(scores) - len(uniq)
    scores = uniq

    print(f"上流: {ppcalc.UPSTREAM_REPO} {ppcalc.UPSTREAM_BRANCH}@{ppcalc.UPSTREAM_COMMIT[:10]} / 総PP の足し算: {'単精度(C# float 再現)' if f32 else '倍精度'}")
    print(f"プレイヤー: {player.get('name')} ({args.player})  スコア件数: {len(scores)}（重複除外 {dup}）")

    # ---- 1件ずつの比較 -------------------------------------------------------
    rows = []
    for s in scores:
        c = ppcalc.pp_from_score(s)
        rows.append((s, c))

    diffs = [abs(c["pp"] - s["pp"]) for s, c in rows]
    within = sum(1 for d in diffs if d <= args.tol)
    print()
    print(f"[PP] {args.tol}PP 以内: {within}/{len(rows)} = {within / len(rows) * 100:.2f}%")
    print(f"     最大の差: {max(diffs):.6f}PP  平均の差: {sum(diffs) / len(diffs):.6f}PP")
    for th in (0.0001, 0.001, 0.01, 0.1):
        n = sum(1 for d in diffs if d <= th)
        print(f"     {th}PP 以内: {n}/{len(rows)}")

    # 譜面の★（譜面ごとの曲線から。API の difficulty.stars と同じになるはず）
    sd = []
    for s, _ in rows:
        d = s["leaderboard"]["difficulty"]
        if d.get("stars"):
            st = ppcalc.map_stars(float(d.get("accRating") or 0), float(d.get("passRating") or 0), float(d.get("techRating") or 0),
                                  predicted_acc=d.get("predictedAcc"), slider_percentage=d.get("sliderPercentage"),
                                  notes=d.get("notes"), note_span=d.get("noteSpan"))
            sd.append(abs(st - float(d["stars"])))
    if sd:
        print(f"[★] 0.01 以内: {sum(1 for x in sd if x <= 0.01)}/{len(sd)}  最大の差: {max(sd):.6f}")

    for key_api, key_calc in (("accPP", "acc_pp"), ("passPP", "pass_pp"), ("techPP", "tech_pp"), ("bonusPp", "bonus_pp")):
        ds = [abs(c[key_calc] - float(s.get(key_api) or 0.0)) for s, c in rows if s.get(key_api) is not None]
        if not ds:
            print(f"[{key_api}] 応答に無し")
            continue
        n = sum(1 for d in ds if d <= args.tol)
        print(f"[{key_api}] {args.tol}PP 以内: {n}/{len(ds)}  最大の差: {max(ds):.6f}")

    # ---- 内訳（モディファイア・モード・modifiersRating の有無） ------------------
    groups: dict[str, list[float]] = defaultdict(list)
    for (s, c), d in zip(rows, diffs):
        diff = s["leaderboard"]["difficulty"]
        groups[f"mods={s.get('modifiers') or '-'}"].append(d)
        groups[f"mode={diff['modeName']}"].append(d)
        groups[f"modifiersRating={'有' if diff.get('modifiersRating') else '無'}"].append(d)
        groups[f"status={diff.get('status')}"].append(d)
    print()
    print("[内訳] グループ: 件数 / 0.5PP以内 / 最大の差")
    for k in sorted(groups):
        v = groups[k]
        print(f"  {k:28s} {len(v):4d} / {sum(1 for d in v if d <= args.tol):4d} / {max(v):.6f}")

    bad = sorted(((d, s, c) for (s, c), d in zip(rows, diffs) if d > args.tol), key=lambda x: -x[0])
    print()
    print(f"[ずれた例] {len(bad)}件（差の大きい順に最大 {args.show} 件）")
    for d, s, c in bad[: args.show]:
        print("  " + fmt_score(s, c))
    # 差が 0.5 以内でも上位の例を参考に出す
    top = sorted(((d, s, c) for (s, c), d in zip(rows, diffs)), key=lambda x: -x[0])[:5]
    print("[差の大きい上位5件]")
    for d, s, c in top:
        print("  " + fmt_score(s, c))

    # ---- 総PP ---------------------------------------------------------------
    print()
    api_pps = [float(s["pp"]) for s in scores]
    calc_pps = [c["pp"] for _, c in rows]
    tot_api = ppcalc.total_pp(api_pps, f32=f32)
    tot_api_dbl = ppcalc.total_pp(api_pps, f32=False)
    tot_calc = ppcalc.total_pp(calc_pps, f32=f32)
    parts_api = ppcalc.total_pp_parts(
        [{"pp": float(s["pp"]), "acc_pp": float(s.get("accPP") or 0), "pass_pp": float(s.get("passPP") or 0),
          "tech_pp": float(s.get("techPP") or 0)} for s in scores], f32=f32)
    ppl = float(player.get("pp") or 0)
    print(f"[総PP] プロフィール /player の pp: {ppl:.4f}")
    print(f"       API の各スコア pp の重み付け合計: {tot_api:.4f}（差 {tot_api - ppl:+.4f}） 倍精度で足すと {tot_api_dbl:.4f}")
    print(f"       移植した計算の pp の重み付け合計: {tot_calc:.4f}（差 {tot_calc - ppl:+.4f}）")
    for k_player, k in (("accPp", "acc_pp"), ("passPp", "pass_pp"), ("techPp", "tech_pp")):
        if k_player in player:
            print(f"       {k_player}: プロフィール {float(player[k_player]):.4f} / 合計 {parts_api[k]:.4f}（差 {parts_api[k] - float(player[k_player]):+.4f}）")
    for k in ("bonusPp", "rank", "countryRank", "lastWeekPp"):
        if k in player:
            print(f"       プロフィールの {k}: {player[k]}")

    # API が返す weight と、こちらで並べた順位の重みの比較（順位がずれていればランク外の pp 付きスコア等がある）
    order = sorted(scores, key=lambda s: -float(s["pp"]))
    wdiff = []
    for i, s in enumerate(order):
        w_api = s.get("weight")
        if w_api is None or not s["pp"]:
            continue
        wdiff.append((abs(float(w_api) - ppcalc.weight(i)), i, s))
    if wdiff:
        n_ok = sum(1 for d, _, _ in wdiff if d < 1e-6)
        print(f"       API の weight と並び順の重みの一致: {n_ok}/{len(wdiff)}")
        mism = [x for x in wdiff if x[0] >= 1e-6]
        for d, i, s in mism[:5]:
            idx_api = round(math.log(float(s['weight'])) / math.log(0.965)) if float(s['weight']) > 0 else None
            print(f"         不一致の例: 並び {i} 番目 pp={s['pp']:.3f} API weight={s['weight']} (={idx_api} 番目相当)")
    zero = [s for s in scores if not s["pp"]]
    print(f"       pp=0 のスコア: {len(zero)}件  モディファイア: {Counter(s.get('modifiers') or '-' for s in zero).most_common(5)}")

    if args.mods:
        check_modifier_samples(raw_dir / "mods", args.refresh, args.tol, args.show)
    return 0


# 補助検証: 他のプレイヤーのモディファイア付きランクスコア（/scores/all?type=3&modifiers=XX の最新100件）
MOD_SAMPLES = ("FS", "SF", "SS", "BFS", "BSF", "GN", "DA", "NO", "NB", "NA", "SC", "PM", "OP", "SA", "NF")


def check_modifier_samples(mods_dir: Path, refresh: bool, tol: float, show: int) -> None:
    print()
    print("[補助検証] 他のプレイヤーのモディファイア付きランクスコア（/scores/all?type=3&modifiers=XX）")
    print("  ※ この API は accPP/passPP/techPP を 0 で返すことがあるので pp だけ比べる")
    allrows: dict[int, tuple[dict, dict]] = {}
    for m in MOD_SAMPLES:
        url = f"{API}/scores/all?type=3&modifiers={m}&count=100&page=1"
        try:
            d = cached(mods_dir / f"{m}.json", url, refresh)
        except Exception as e:  # noqa: BLE001
            print(f"  {m}: 取得失敗 {e}")
            continue
        data = [s for s in (d.get("data") or []) if s.get("leaderboard")]
        ds = []
        for s in data:
            c = ppcalc.pp_from_score(s)
            allrows[s["id"]] = (s, c)
            ds.append(abs(c["pp"] - s["pp"]))
        if ds:
            n = sum(1 for x in ds if x <= tol)
            print(f"  {m:4s} {len(ds):3d}件  {tol}PP以内 {n:3d}  最大の差 {max(ds):.6f}")
        else:
            print(f"  {m:4s} 0件")
    rows = list(allrows.values())
    if not rows:
        return
    diffs = [abs(c["pp"] - s["pp"]) for s, c in rows]
    n = sum(1 for x in diffs if x <= tol)
    print(f"  合計（重複除く）{len(rows)}件  {tol}PP以内 {n} = {n / len(rows) * 100:.2f}%  最大の差 {max(diffs):.6f}")
    combos = Counter(s.get("modifiers") or "-" for s, _ in rows)
    print(f"  モディファイアの組み合わせ（多い順）: {combos.most_common(12)}")
    nomr = sum(1 for s, _ in rows if not s["leaderboard"]["difficulty"].get("modifiersRating"))
    print(f"  modifiersRating の無い譜面のスコア: {nomr}件")
    bad = sorted(((abs(c["pp"] - s["pp"]), s, c) for s, c in rows if abs(c["pp"] - s["pp"]) > tol), key=lambda x: -x[0])
    for d, s, c in bad[:show]:
        print("    " + fmt_score(s, c))


if __name__ == "__main__":
    raise SystemExit(main())
