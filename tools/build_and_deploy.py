"""exe をビルドし、動作を確かめてから、使う用のフォルダ blnext-app/ の blnext.exe と _internal/ だけを差し替える
（NLM の build_and_deploy.py と同じ考え方）。

使い方: .venv-build/Scripts/python.exe tools/build_and_deploy.py [--skip-build] [--no-backup] [--test]
  --skip-build : ビルドを省き、今の dist/blnext/ を確かめて配る
  --no-backup  : 差し替える前の退避（blnext-app/_backup_before_update_<日時>/。新しい順に3つ）を作らない
  --test       : 先に tools/run_tests.py unit を流し、失敗したらビルドしない

  - PyInstaller はビルドのたびに dist/blnext/ を丸ごと消して作り直す。exe の隣にできるデータ（config/ data/ logs/ exports/）も
    消えるので、dist/blnext/ の exe を普段使いしないこと。使うのは blnext-app/blnext.exe（.gitignore 済み）。
  - 配る前に、ビルドした exe を --selftest で起動する（窓は出さない。同梱物の読み込み・サーバー・画面の入口を確かめる。app.py の selftest）。
  - blnext-app/ の config/ data/ logs/ exports/ には触らない。exe が起動中だと差し替えられないので止める（閉じてから流し直す）。
  - 起動中でなくても、ほかのプロセスが _internal の DLL を読み込んだままのことがある（blnext から開いた Chrome の補助のプロセスなど）。
    中身が新しい版と同じならそのまま残して差し替える（replace_dir）。途中で止まった時は、退避を作らずに（--skip-build --no-backup）
    流し直す（退避を作ると、途中まで消えた _internal を写して、古い退避が1つ消える）。
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "blnext"
APP = ROOT / "blnext-app"
VENV_PY = ROOT / ".venv-build" / "Scripts" / "python.exe"
TARGETS = ["blnext.exe", "_internal"]
BACKUP_PREFIX = "_backup_before_update_"
KEEP_BACKUPS = 3


def _same(a, b):
    return a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()


def replace_dir(src, dst):
    """dst を src と同じ中身にする。ほかのプロセスが使っていて消せないファイルがあっても、中身が src と同じならそのまま残す
    （2026-10-17: blnext から開いた Chrome の chrome-native-host が _internal/VCRUNTIME140.dll を読み込んだままで、消せなかった）。
    違う中身のファイルが消せない時は、何が使っているか探すよう伝えて止める"""
    stuck = []

    def keep_locked(func, path, exc):
        p = Path(path)
        rel = p.relative_to(dst) if p != dst else Path('.')
        if isinstance(exc, PermissionError) and _same(p, src / rel):
            stuck.append(rel)
            return
        if func is os.rmdir and any(rel == Path('.') or rel in s.parents for s in stuck):
            return   # 残したファイルの入っているフォルダは消さない
        raise exc

    if dst.is_dir():
        shutil.rmtree(dst, onexc=keep_locked)
    if stuck:
        print("使われていて消せないファイルを、新しい版と同じ中身なので残しました: " + ", ".join(map(str, stuck)))
    skip = {str(p) for p in stuck}
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if str((Path(d) / n).relative_to(src)) in skip])


def _reexec_with_venv_py():
    if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
        os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])


def fail(msg):
    print("中止:", msg)
    sys.exit(1)


def running():
    try:
        # 出力は Windows の文字コード（日本語なら cp932）なので、文字にせずバイト列のまま探す
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq blnext.exe", "/NH"], capture_output=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b""
        return b"blnext.exe" in out.lower()
    except OSError:
        return False


def selftest():
    out = Path(tempfile.mkdtemp(prefix="bln-selftest-")) / "result.json"
    try:
        subprocess.run([str(DIST / "blnext.exe"), "--selftest", str(out)], timeout=90)
        res = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)
    return res


def main():
    _reexec_with_venv_py()
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", "--no-build", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if running():
        fail("blnext.exe が起動中です。閉じてからもう一度実行してください。")
    if a.test and subprocess.run([sys.executable, str(ROOT / "tools" / "run_tests.py"), "unit"], cwd=ROOT).returncode != 0:
        fail("テストが失敗しました")
    t0 = time.time()
    if not a.skip_build:
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", "blnext.spec"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-3000:])
            fail("ビルドに失敗しました")
    if not (DIST / "blnext.exe").is_file():
        fail("dist/blnext/blnext.exe がありません")
    st = selftest()
    if not st.get("ok"):
        fail(f"ビルドした exe の動作確認に失敗しました: {st}")
    APP.mkdir(exist_ok=True)
    if not a.no_backup and (APP / "blnext.exe").exists():
        bk = APP / (BACKUP_PREFIX + time.strftime("%Y%m%d_%H%M%S"))
        bk.mkdir()
        for t in TARGETS:
            src = APP / t
            if src.is_dir():
                shutil.copytree(src, bk / t)
            elif src.exists():
                shutil.copy2(src, bk / t)
        for old in sorted(p for p in APP.iterdir() if p.is_dir() and p.name.startswith(BACKUP_PREFIX))[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)
    replace_dir(DIST / "_internal", APP / "_internal")
    shutil.copy2(DIST / "blnext.exe", APP / "blnext.exe")
    print(f"配布しました: blnext-app/blnext.exe（{st.get('version')}・動作確認OK・{time.time() - t0:.0f}秒）")


if __name__ == "__main__":
    main()
