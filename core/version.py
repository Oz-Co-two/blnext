# 版番号の定義元は web/js/version.js の APP_VERSION（画面の表示と共通）。ここはそれを読むだけ
import os, re


def app_version(app_root):
    """例 'v0.1.0'。読めなければ ''"""
    try:
        with open(os.path.join(app_root, 'web', 'js', 'version.js'), encoding='utf-8') as f:
            m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", f.read())
        return m.group(1) if m else ''
    except OSError:
        return ''
