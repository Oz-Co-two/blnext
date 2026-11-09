# ファイル書き込みの小道具（NLM の serve.py の _write_text_atomic を移したもの）
import os, time


def write_atomic(path, data):
    """一時ファイルに書き終えてから置き換える＝読み手が書きかけ（空・途中まで）を見ない・途中で落ちても前の中身が残る。
    data は str（UTF-8 で書く）か bytes。Windows では置き換え先を他が開いていると失敗する
    （ウイルス対策ソフト・ゲームが読んでいる途中など）ので少し待ってやり直す"""
    tmp = path + '.tmp'
    mode, kw = ('wb', {}) if isinstance(data, (bytes, bytearray)) else ('w', {'encoding': 'utf-8', 'newline': '\n'})
    with open(tmp, mode, **kw) as f:
        f.write(data)
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)
