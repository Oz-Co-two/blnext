# ログ（exe の隣の logs/）。通信の URL・応答コード・例外を残す。大きさで古いものから消す（docs/spec.md の「ログとテスト」）
import logging, logging.handlers, os

_done = False


def log_dir(data_dir):
    """ログのフォルダ（書き込み先の logs/。設定の「ログのフォルダを開く」もここを開く）"""
    return os.path.join(data_dir, 'logs')


def setup(data_dir):
    """1回だけ呼ぶ（2回目以降は何もしない）。書けない場所でもアプリは止めない"""
    global _done
    if _done:
        return
    _done = True
    root = logging.getLogger('bln')
    root.setLevel(logging.INFO)
    try:
        d = log_dir(data_dir)
        os.makedirs(d, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(os.path.join(d, 'blnext.log'), maxBytes=1_000_000, backupCount=3, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        root.addHandler(h)
    except OSError:
        pass
