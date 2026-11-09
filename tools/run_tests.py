"""テストの入口（NLM の tools/run_tests.py と同じ考え方。詳しくは tests/README.md）。

  .venv-build/Scripts/python.exe tools/run_tests.py              全グループ（unit・e2e・visual を並列に）
  .venv-build/Scripts/python.exe tools/run_tests.py unit         グループを指定
  .venv-build/Scripts/python.exe tools/run_tests.py visual --accept   画面の比較の基準を今の画面で置き換える（変化が意図どおりの時）
  .venv-build/Scripts/python.exe tools/run_tests.py release      公開の直前だけ流す特別なテスト（全グループには入らない。tools/make_release.py も流す）
  オプション: -k 式（pytest の -k）/ --coverage（Python と JS のカバレッジ。test-out/coverage/）/ -v（pytest の出力も表示）
              --no-split（グループを分けずに流す。テスト同士の影響を疑う時の切り分け用）

長いグループ（SHARDS）は、前回の各テストの所要時間（test-out/test_secs.json）で同じくらいの長さの組に分け、別々のプロセス
（それぞれ自分の Edge・サーバー・作り物のデータ）で同時に流す（分け方は tests/conftest.py の pytest_collection_modifyitems）。
2026-11-09 に入れた（e2e が 1本の Edge で約4分かかっていた）。-k・--coverage の時は分けない（カバレッジの結果のファイルがぶつかるため）。
画面の比較（visual）は、決まったフォルダ（test-out/visual/_data）の作り物のデータを使い、最後にまとめを1つ作るので分けない。

画面には要約だけを出す（読む量を減らすため）。失敗の詳細は test-out/last.txt、全結果は test-out/summary.json。
失敗・エラーがあった回は test-out/history/<日時>/ にも残す（新しい順に30回分）。終了コード: 0=失敗なし / 1=失敗あり
「要確認」は失敗ではないが人の目が要るもの（画面の比較で見た目が変わった）。テストが record_property('review', 文) で付ける
"""
import argparse, concurrent.futures as cf, json, os, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
OUT = ROOT / "test-out"
HIST = OUT / "history"
KEEP_HISTORY = 30
VENV_PY = ROOT / ".venv-build" / "Scripts" / "python.exe"
COV_SRC = ["core", "serve", "app"]
SHARDS = {"e2e": 6, "unit": 4}        # 分けて同時に流す組の数（CPU 24・GPU の PC で、全体が画面の比較の長さ＝約1分半で終わる数）
ON_DEMAND = {"release"}               # 指名した時だけ流すグループ（release＝公開の直前だけの特別なテスト。tests/release/）
SECS = OUT / "test_secs.json"          # 各テストの前回の所要時間 {"グループ/ファイル名::テスト名": 秒}（分け方に使う）


def _reexec_with_venv_py():
    if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
        os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])


def groups_all():
    return sorted(p.name for p in TESTS.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and p.name != "fixtures" and any(p.glob("test_*.py")))


def run_group(g, a):
    """グループを流す。SHARDS のグループは n 組に分けて同時に流し、結果を合わせる（秒は一番遅い組）"""
    n = SHARDS.get(g, 1) if not (a.k or a.coverage or a.no_split) else 1
    if n == 1:
        res, secs = run_piece(g, a)
        return g, res, secs, 1
    with cf.ThreadPoolExecutor(max_workers=n) as ex:
        parts = list(ex.map(lambda i: run_piece(g, a, (i, n)), range(n)))
    return g, [r for res, _ in parts for r in res], max(secs for _, secs in parts), n


def run_piece(g, a, shard=None):
    tag = g if shard is None else f"{g}.{shard[0]}"
    xml = OUT / f"_{tag}.xml"
    xml.unlink(missing_ok=True)
    cmd = [sys.executable, "-X", "utf8", "-m", "pytest", str(TESTS / g), "-q", "-p", "no:logging", "-p", "no:cacheprovider",
           f"--junitxml={xml}", "-o", "junit_family=xunit1"]
    if a.k:
        cmd += ["-k", a.k]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if shard is not None:
        env["BLN_SHARD"] = f"{shard[0]}/{shard[1]}"
        env["BLN_SHARD_SECS"] = str(SECS)
    if a.accept:
        env["BLN_VISUAL_ACCEPT"] = "1"
    if a.coverage:
        cmd += [f"--cov={s}" for s in COV_SRC] + ["--cov-report="]
        env["COVERAGE_FILE"] = str(OUT / f".coverage.{g}")
        env["BLN_JS_COVERAGE"] = "1"
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    secs = time.time() - t0
    res = []
    try:
        for tc in ET.parse(xml).getroot().iter("testcase"):
            st, msg = "pass", ""
            for el_tag, name in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
                el = tc.find(el_tag)
                if el is not None:
                    st, msg = name, ((el.get("message") or "") + "\n" + (el.text or "")).strip()
                    break
            props = {p.get("name"): p.get("value") or "" for p in tc.iter("property")}
            if st == "pass" and "review" in props:   # 要確認（失敗ではない）
                st, msg = "review", props["review"]
            res.append({"group": g, "file": (tc.get("file") or tc.get("classname") or "").split("/")[-1].split("\\")[-1],
                        "name": tc.get("name"), "status": st, "msg": msg, "secs": round(float(tc.get("time") or 0), 2)})
    except (OSError, ET.ParseError):
        res = [{"group": g, "file": "-", "name": f"(実行 {tag})", "status": "error", "msg": (p.stdout + p.stderr)[-3000:], "secs": secs}]
    if not res and p.returncode not in (0, 5):   # 5 = テストが1つも無い（-k で全部外れた）
        res = [{"group": g, "file": "-", "name": f"(実行 {tag})", "status": "error", "msg": (p.stdout + p.stderr)[-3000:], "secs": secs}]
    if a.verbose:
        print(p.stdout.rstrip())
    return res, secs


def save_secs(allres):
    """流したテストの所要時間を test_secs.json に足す（流さなかったテストの前の値は残す）"""
    try:
        old = json.loads(SECS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    for r in allres:
        if r["file"] != "-" and r["status"] != "skip":
            old[f"{r['group']}/{r['file']}::{r['name']}"] = r["secs"]
    SECS.write_text(json.dumps(old, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def coverage_report():
    """グループごとの Python のカバレッジをまとめ、JS のカバレッジ（画面のテスト）とあわせて要約の1行を返す"""
    lines = []
    d = OUT / "coverage"
    d.mkdir(parents=True, exist_ok=True)
    files = [str(p) for p in OUT.glob(".coverage.*")]
    if files:
        import coverage
        cov = coverage.Coverage(data_file=str(OUT / ".coverage"))
        cov.combine(files, keep=False)
        cov.save()
        with open(d / "python.txt", "w", encoding="utf-8") as f:
            pct = cov.report(file=f, show_missing=True, skip_covered=True)
        lines.append(f"Python {pct:.0f}%")
    js = sorted(d.glob("js_*.json"))
    if js:
        sys.path.insert(0, str(ROOT / "tools"))
        from cdp import js_coverage_report
        lines.append(js_coverage_report(js, d / "js.txt"))
    return "カバレッジ: " + "・".join(lines) + "  詳細: test-out/coverage/" if lines else ""


def save_history():
    d = HIST / time.strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    for f in ("last.txt", "summary.json"):
        shutil.copyfile(OUT / f, d / f)
    (d / "command.txt").write_text(" ".join(["tools/run_tests.py", *sys.argv[1:]]) + "\n", encoding="utf-8")
    if (OUT / "screens").is_dir():   # 画面のテストで撮った画像（失敗の時の様子）
        shutil.copytree(OUT / "screens", d / "screens")
    for old in sorted(p for p in HIST.iterdir() if p.is_dir())[:-KEEP_HISTORY]:
        shutil.rmtree(old, ignore_errors=True)
    return d


def main():
    _reexec_with_venv_py()
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="*")
    ap.add_argument("-k")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--accept", action="store_true", help="画面の比較の基準を今の画面で置き換える")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--no-split", action="store_true", help="グループを分けずに流す")
    a = ap.parse_args()
    groups = a.groups or [g for g in groups_all() if g not in ON_DEMAND]
    unknown = [g for g in groups if g not in groups_all()]
    if unknown:
        print("知らないグループ:", ", ".join(unknown), "／ある:", ", ".join(groups_all()))
        return 2
    OUT.mkdir(exist_ok=True)
    if a.coverage:
        shutil.rmtree(OUT / "coverage", ignore_errors=True)
        for p in OUT.glob(".coverage*"):
            p.unlink()
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=len(groups)) as ex:
        done = list(ex.map(lambda g: run_group(g, a), groups))
    MARK = {"pass": "OK", "review": "要確認", "fail": "失敗", "error": "エラー", "skip": "飛ばし"}
    allres, lines, detail = [], [], []
    for g, res, secs, nproc in done:
        allres += res
        c = {s: sum(r["status"] == s for r in res) for s in MARK}
        tail = "・".join(f"{MARK[s]}{c[s]}" for s in ("review", "fail", "error", "skip") if c[s])
        lines.append(f"{g:<6} {c['pass'] + c['review']}/{len(res) - c['skip']} OK" + (f"  {tail}" if tail else "")
                     + f"  ({secs:.0f}秒" + (f"・{nproc}つに分けて同時に" if nproc > 1 else "") + ")")
        for r in res:
            if r["status"] in ("fail", "error"):
                first = next((ln for ln in r["msg"].splitlines() if ln.strip()), "")
                lines.append(f"  ✗ {g}/{r['file']}::{r['name']}\n      {first[:200]}")
            elif r["status"] == "review":
                lines.append(f"  ? {r['msg'][:200]}")
            if r["status"] != "pass":
                detail.append(f"[{MARK[r['status']]}] {g}/{r['file']}::{r['name']}  ({r['secs']}秒)\n"
                              + "".join(f"    {ln}\n" for ln in r["msg"].splitlines()))
    nf = sum(r["status"] in ("fail", "error") for r in allres)
    nr = sum(r["status"] == "review" for r in allres)
    lines.append(f"合計 {len(allres)}件: " + (f"失敗{nf}" if nf else "失敗なし") + (f"・要確認{nr}" if nr else "")
                 + f"  ({time.time() - t0:.0f}秒)" + ("  詳細: test-out/last.txt" if detail else ""))
    if nr:
        lines.append("要確認: 画面の見た目が基準と変わりました。test-out/visual/review.png（まとめ画像）か report.html（前後を並べたページ）"
                     "を見て、意図した変化なら tools/run_tests.py visual --accept で基準を置き換えてください。")
    if a.coverage:
        lines.append(coverage_report())
    print("\n".join(lines))
    (OUT / "last.txt").write_text("\n".join(lines) + "\n\n" + "\n".join(detail), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")
    save_secs(allres)
    for p in OUT.glob("_*.xml"):
        p.unlink(missing_ok=True)
    if nf:
        print(f"失敗の記録を残しました: {save_history().relative_to(ROOT).as_posix()}/")
    return 1 if nf else 0


if __name__ == "__main__":
    sys.exit(main())
