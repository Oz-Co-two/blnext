# PyInstaller spec — blnext デスクトップ版（pywebview＋serve.py）。NLM の nlm.spec を流用した。
# ビルド: .venv-build/Scripts/python.exe -m PyInstaller --noconfirm --clean blnext.spec
# 出力:   dist/blnext/blnext.exe（onedir＝exe＋_internal/）
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
HERE = os.path.abspath('.')

# 画面（web/）は読み取り専用のデータとして同梱する。書き込み先（config/ data/ logs/ exports/）は実行時に exe の隣へ作る
datas = [
    ('web', 'web'),
]

# serve.py は app.py の main() の中で import する。core/ はパッケージごと取り込む
# （NLM は .py を足すたびにここへ足す約束だったが、core/ の中なら足さなくてよい。core/ の外に .py を足した時だけここへ足す）
hiddenimports = ['serve'] + collect_submodules('core')
binaries = []
for pkg in ('webview', 'clr_loader', 'pythonnet'):
    try:
        b, d, h = collect_all(pkg)
        binaries += b; datas += d; hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ['app.py'],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='blnext',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUIアプリ（コンソール窓を出さない）。調べたい時だけ一時的に True
    disable_windowed_traceback=False,
    icon=os.path.join(HERE, 'web', 'icons', 'app.ico'),
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='blnext',
)

# 案内文とライセンスは exe と同じ階層（_internal の外）に置く。COLLECT() に足すと _internal の下にまとめられるので、ビルドの後に直接写す
# （NLM の nlm.spec と同じ）。ここに足した物は core/app_update.py の REPLACE_ITEMS にも足す（自動更新でも入れ替わるように）
import shutil
for _name in ('はじめにお読みください.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.txt'):
    shutil.copy2(os.path.join(HERE, _name), os.path.join(DISTPATH, 'blnext', _name))
