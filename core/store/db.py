# SQLite の DB（data/blnext.db）。取得したデータ・計算結果・出力の記録をすべて入れる（docs/spec.md の「データ保存」）。
#  - DB の版（meta.schema_version）を持ち、起動時に足りない変更（MIGRATIONS）を順に当てる。変更は足すだけ（並びを変えない）
#  - WAL モード。書き込みはトランザクションでまとめ、取得の途中で閉じても壊れないようにする
#  - 接続は1つを複数のスレッドで使い、Store のロックで順番にする（取得のスレッドと API のスレッドが同時に触るため）
import json, os, sqlite3, threading, time

MIGRATIONS = [
    # 1: 最初の形
    """
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

    -- 比較対象（国内ランキングで見た人）と自分の最新の状態。差分更新の判定に使う
    CREATE TABLE players(
      player_id TEXT PRIMARY KEY, name TEXT, country TEXT, pp REAL, rank INTEGER, country_rank INTEGER,
      acc_pp REAL, pass_pp REAL, tech_pp REAL, last_ranked_score_time INTEGER, ranked_play_count INTEGER,
      last_week_pp REAL, last_week_rank INTEGER, last_week_country_rank INTEGER, avatar TEXT,
      seen_at INTEGER,                    -- ランキング・プロフィールで最後に見た時刻
      scores_fetched_at INTEGER,          -- スコアを取った時刻
      scores_last_ranked_time INTEGER,    -- スコアを取った時の last_ranked_score_time（変わったら取り直す）
      is_self INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX players_country_rank ON players(country, country_rank);

    -- 起動（取得）ごとの周辺の順位
    CREATE TABLE player_snapshots(
      run_at INTEGER NOT NULL, player_id TEXT NOT NULL, pp REAL, rank INTEGER, country_rank INTEGER,
      PRIMARY KEY(run_at, player_id));

    -- 譜面（1難易度）
    CREATE TABLE leaderboards(
      leaderboard_id TEXT PRIMARY KEY, hash TEXT, song_name TEXT, sub_name TEXT, song_author TEXT, mapper TEXT,
      difficulty TEXT, mode TEXT, status INTEGER, stars REAL, acc_rating REAL, pass_rating REAL, tech_rating REAL,
      predicted_acc REAL, map_type INTEGER, modifiers_rating TEXT, modifier_values TEXT,
      upload_time INTEGER, ranked_time INTEGER, notes INTEGER, duration INTEGER,
      bs_rating REAL, bs_key TEXT, updated_at INTEGER);
    CREATE INDEX leaderboards_hash ON leaderboards(hash);

    -- 各プレイヤーの譜面ごとの自己ベスト（最新のみ）。比較対象は accgraph の項目だけ（fc_acc 等は自分だけ）
    CREATE TABLE scores(
      player_id TEXT NOT NULL, leaderboard_id TEXT NOT NULL, acc REAL, pp REAL, fc_acc REAL, fc_pp REAL, weight REAL,
      acc_pp REAL, pass_pp REAL, tech_pp REAL, modifiers TEXT, stars REAL, timeset INTEGER,
      play_count INTEGER, last_try_time INTEGER, missed_notes INTEGER, bad_cuts INTEGER, bomb_cuts INTEGER,
      walls_hit INTEGER, full_combo INTEGER,
      PRIMARY KEY(player_id, leaderboard_id));
    CREATE INDEX scores_leaderboard ON scores(leaderboard_id);

    -- 自分の自己ベストが更新されるたびに追記する
    CREATE TABLE score_history(
      leaderboard_id TEXT NOT NULL, acc REAL, pp REAL, timeset INTEGER NOT NULL, recorded_at INTEGER,
      PRIMARY KEY(leaderboard_id, timeset));

    -- 自分の順位の推移（日付は UTC の YYYY-MM-DD。履歴 API の値と起動ごとの記録をまとめる）
    CREATE TABLE self_history(
      date TEXT PRIMARY KEY, pp REAL, rank INTEGER, country_rank INTEGER, source TEXT);

    -- PlayerData.dat の内容
    CREATE TABLE local_plays(
      hash TEXT NOT NULL, difficulty TEXT NOT NULL, mode TEXT NOT NULL, play_count INTEGER, best_score INTEGER,
      read_at INTEGER, PRIMARY KEY(hash, difficulty, mode));

    -- 挑戦ごとの記録（取れる場合だけ）
    CREATE TABLE attempts(
      leaderboard_id TEXT NOT NULL, timeset INTEGER NOT NULL, acc REAL, pp REAL, end_type INTEGER, modifiers TEXT,
      PRIMARY KEY(leaderboard_id, timeset));

    -- 似ている人の計算結果
    CREATE TABLE similarities(
      computed_at INTEGER NOT NULL, player_id TEXT NOT NULL, sim REAL, overlap INTEGER, style_sim REAL,
      PRIMARY KEY(computed_at, player_id));

    -- おすすめを計算した1回ごとの記録と中身
    CREATE TABLE recommendation_runs(
      run_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER, settings TEXT);
    CREATE TABLE recommendation_items(
      run_id INTEGER NOT NULL, leaderboard_id TEXT NOT NULL, kind TEXT, est_acc REAL, est_pp REAL, gain REAL,
      rank_up INTEGER, confidence REAL, reason TEXT, pinned INTEGER DEFAULT 0, excluded INTEGER DEFAULT 0,
      exported INTEGER DEFAULT 0, PRIMARY KEY(run_id, leaderboard_id));
    CREATE TABLE exports(
      run_id INTEGER, exported_at INTEGER, path TEXT);

    -- 利用者の印（固定・今後出さない）。固定は次回にも引き継ぐ
    CREATE TABLE user_marks(
      leaderboard_id TEXT PRIMARY KEY, mark TEXT NOT NULL, updated_at INTEGER);
    """,
    # 2: 振り返り（出力した曲の結果。推定精度と実際の精度の差を数値の調整に使う）
    """
    CREATE TABLE reviews(
      run_id INTEGER NOT NULL, leaderboard_id TEXT NOT NULL,
      status TEXT,          -- improved（自己ベストが伸びた）/ played（プレイしたが伸びなかった）/ notPlayed
      est_acc REAL, best_before REAL, best_after REAL,
      attempt_max REAL,     -- 出力の後の挑戦（クリア）で一番よかった精度（挑戦の記録が取れる時だけ）
      checked_at INTEGER, PRIMARY KEY(run_id, leaderboard_id));
    """,
    # 3: 設定のセット（core/settings.py）。固定（pin）はセットごと、今後出さない（never）は全セット共通（preset=''）。
    #    出力の記録にセットと出力の方法（write / oneclick / file）を残す。それまでの記録は最初のセット（p1）の物とする
    """
    CREATE TABLE user_marks_v3(
      leaderboard_id TEXT NOT NULL, mark TEXT NOT NULL, preset TEXT NOT NULL DEFAULT '', updated_at INTEGER,
      PRIMARY KEY(leaderboard_id, preset));
    INSERT INTO user_marks_v3(leaderboard_id, mark, preset, updated_at)
      SELECT leaderboard_id, mark, CASE WHEN mark='pin' THEN 'p1' ELSE '' END, updated_at FROM user_marks;
    DROP TABLE user_marks;
    ALTER TABLE user_marks_v3 RENAME TO user_marks;
    ALTER TABLE recommendation_runs ADD COLUMN preset TEXT;
    UPDATE recommendation_runs SET preset='p1';
    ALTER TABLE exports ADD COLUMN method TEXT;
    UPDATE exports SET method='write';
    """,
    # 4: 譜面の特徴（おすすめの一覧に出す。BPM・NJS・1秒あたりのノーツ数・爆弾・壁・一番速い所の速さ・BeatSaver のタグ）と、
    #    自分のスコアの ID（リプレイのページを開く）。今ある分にも入れるため、ランク譜面の一覧（1日1回）と自分のスコアを
    #    次の取得で全部取り直す
    """
    ALTER TABLE scores ADD COLUMN score_id INTEGER;
    DELETE FROM meta WHERE key='self_scores_synced_at';
    ALTER TABLE leaderboards ADD COLUMN bpm REAL;
    ALTER TABLE leaderboards ADD COLUMN njs REAL;
    ALTER TABLE leaderboards ADD COLUMN nps REAL;
    ALTER TABLE leaderboards ADD COLUMN bombs INTEGER;
    ALTER TABLE leaderboards ADD COLUMN walls INTEGER;
    ALTER TABLE leaderboards ADD COLUMN peak_ebpm REAL;
    ALTER TABLE leaderboards ADD COLUMN tags TEXT;
    DELETE FROM meta WHERE key='maps_fetched_at';
    """,
    # 5: 自己ベストのリプレイの頭から読んだ、その時のジャンプの距離（JD。core/fetch/replay.py）。スコアの ID ごと。
    #    status: ok / bad-url / bad-replay / http-<番号>（無い・読めない。やり直さない）。リプレイの URL を今ある分にも入れるため、
    #    自分のスコアを次の取得で全部取り直す。
    #    rec_lists: 画面のおすすめのリスト（作った時のまま。セットごと。serve.py の「おすすめのリスト」）。
    #    payload は画面に出す中身の JSON、data_at は元にしたデータの取得の回、settings は作った時の設定（変わったら作り直す）
    """
    ALTER TABLE scores ADD COLUMN replay_url TEXT;
    CREATE TABLE replay_info(
      score_id INTEGER PRIMARY KEY, jd REAL, status TEXT, fetched_at INTEGER);
    DELETE FROM meta WHERE key='self_scores_synced_at';
    CREATE TABLE rec_lists(
      preset TEXT PRIMARY KEY, made_at INTEGER, data_at INTEGER, settings TEXT, payload TEXT);
    """,
    # 6: 振り返りの「初クリア」（firstClear。core/review.py）。出力の時に自己ベストが無かった曲の自己ベストができた時は、improved と
    #    分ける。それまでに improved と記録した中で、出力の時の自己ベスト（best_before）が無い物を初クリアに直す
    """
    UPDATE reviews SET status='firstClear' WHERE status='improved' AND best_before IS NULL;
    """,
    # 7: 譜面ごとの精度の曲線の材料（2026-10 の BeatLeader のリウェイトから。スライダーの割合・ノーツの出る長さ（秒））。
    #    今ある分は、PP の式の版（meta.pp_formula）が変わった時の全部の取り直し（core/fetch/refresh.py）で入る
    """
    ALTER TABLE leaderboards ADD COLUMN slider_percentage REAL;
    ALTER TABLE leaderboards ADD COLUMN note_span REAL;
    """,
]


class Store:
    def __init__(self, path):
        self.path = path
        if path != ':memory:':
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA synchronous=NORMAL')
            self.conn.execute('PRAGMA foreign_keys=OFF')
            self._migrate()

    def close(self):
        with self.lock:
            self.conn.close()

    # ---- 版 ----
    def _migrate(self):
        c = self.conn
        has_meta = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        ver = int(self.meta('schema_version') or 0) if has_meta else 0
        for i in range(ver, len(MIGRATIONS)):
            c.execute('BEGIN')
            try:
                for stmt in _split_sql(MIGRATIONS[i]):   # executescript は暗黙に COMMIT するので1文ずつ
                    c.execute(stmt)
                c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(i + 1),))
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise

    # ---- 小道具 ----
    def tx(self):
        return _Tx(self)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def q1(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return dict(r) if r else None

    def meta(self, key, default=None):
        with self.lock:
            r = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return r[0] if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)', (key, None if value is None else str(value)))

    def meta_json(self, key, default=None):
        v = self.meta(key)
        try:
            return json.loads(v) if v is not None else default
        except ValueError:
            return default

    # ---- 書き込み（呼ぶ側で tx() の中に入れてまとめる） ----
    def upsert(self, table, row, keys):
        """row（dict）を入れる。keys が一致する行があれば、row にある列だけを書き換える（無い列は残す）"""
        cols = list(row)
        sets = ', '.join('%s=excluded.%s' % (k, k) for k in cols if k not in keys) or '%s=%s' % (keys[0], keys[0])
        sql = 'INSERT INTO %s(%s) VALUES(%s) ON CONFLICT(%s) DO UPDATE SET %s' % (
            table, ', '.join(cols), ', '.join('?' * len(cols)), ', '.join(keys), sets)
        self.conn.execute(sql, [row[k] for k in cols])


class _Tx:
    """with store.tx(): … の間はロックを持ち、まとめて COMMIT（例外なら ROLLBACK）"""
    def __init__(self, store):
        self.s = store

    def __enter__(self):
        self.s.lock.acquire()
        self.s.conn.execute('BEGIN')
        return self.s

    def __exit__(self, et, e, tb):
        try:
            self.s.conn.execute('ROLLBACK' if et else 'COMMIT')
        finally:
            self.s.lock.release()
        return False


def _split_sql(script):
    """MIGRATIONS の文を ; で分ける（文字列の中に ; を書かないこと）。-- のコメント行は捨てる"""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith('--')]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def now():
    return int(time.time())
