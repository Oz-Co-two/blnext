# 起動ごとの取得（docs/spec.md の「データ取得」「差分更新」）。別のスレッドで動かし、進捗を progress() で返す。
# 流れ: 自分 → 国内ランキング（自分の上下と目標順位のページ）→ ランク譜面（1日1回）→ 自分のスコア → アテンプト → 自分の履歴
#       → 比較対象のスコア（上下の範囲の中からスコアの多い順に pool_max 人。最終ランクスコア時刻が前回から変わった人だけ。ランクのスコアが min_scores より少ない人は取らない）
#  ランク譜面の一覧は全曲データ（song_details.py）で変化を確かめ、変わった時だけ取る（_update_maps）
#  - 1人分・1段階ごとに保存する＝途中でやめても（閉じても）取得済みの分は残る
#  - 自分のプロフィールが取れない（オフライン等）時は止め、画面は前回までのデータで出す
#  - 範囲から外れた人のデータは消さない（戻ってきた時に差分だけで済むように）
#  - PP の式・★が変わった時（アプリの式を直した版の最初の取得・BeatLeader 側のリウェイト）は、譜面・自分・比較対象を全部取り直す
#    （古い式の PP・古い★を新しい物と混ぜて曲線や総PP を作らないため。_refetch_all）
import logging, math, threading, time
from concurrent.futures import ThreadPoolExecutor

from . import beatleader as bl, song_details as sd
from .http import Cancelled, NetError, MAX_CONCURRENCY
from .. import review
from ..estimate import ppcalc
from ..estimate.clear import ATTEMPT_DAYS
from ..estimate.model import pick_pool
from ..store.db import now

log = logging.getLogger('bln.refresh')

MAPS_INTERVAL = 20 * 3600       # ランク譜面の変化を全曲データで確かめるのは1日1回まで（少し余裕を見て20時間）
MAPS_FALLBACK = 7 * 86400       # 全曲データが取れない・古い間は、ランク譜面の一覧をこの間隔で取る
MAPS_MAX_AGE = 30 * 86400       # 全曲データでは分からない変化（★が同じままの評価のやり直し・★が前からある難易度のランク入り）のために、変化が無くてもこの間隔で取る
TRY_MARGIN = 3600               # 最近挑戦した順で取り直す時の重なり（秒）
HISTORY_FIRST = 1000            # 自分の履歴: 初回は取れるだけ遡る
HISTORY_NEXT = 14
# リウェイトの見分け: 自己ベストが同じ（日時が同じ）なのに PP か★が変わった自分のスコアが、比べられた数の 5% かつ 10 曲以上。
# 譜面1つずつの評価のやり直し（ランクの入れ替えなど）では数曲しか変わらない
REWEIGHT_MIN = 10
REWEIGHT_SHARE = 0.05
PP_CHANGED = 0.01               # PP が変わったと見る差
STARS_CHANGED = 0.001           # ★が変わったと見る差
# アプリの PP の式と BeatLeader の PP のずれの見張り: 取った自分のスコアのうち 0.5PP より大きくずれた物が 10% 以上（10件以上比べた時）
MISMATCH_TOL = 0.5
MISMATCH_SHARE = 0.1
MISMATCH_MIN_COMPARED = 10


def _day(ts):
    return time.strftime('%Y-%m-%d', time.gmtime(ts))


def pool_pages(rank, above, below, target=None, page_size=bl.PLAYERS_PAGE):
    """自分の国内順位 rank の上 above 人・下 below 人と、目標順位が入るページ番号の並び"""
    if not rank:
        return []
    first = max(1, math.ceil(max(1, rank - above) / page_size))
    last = max(first, math.ceil((rank + below) / page_size))
    pages = set(range(first, last + 1))
    if target:
        pages.add(max(1, math.ceil(target / page_size)))
    return sorted(pages)


class Refresher:
    def __init__(self, store, settings, client_factory):
        self.store, self.settings, self.client_factory = store, settings, client_factory
        self._lock = threading.Lock()
        self._client = None
        self._public = None   # 今の取得で読んだ統計の公開の印（_do が入れる）
        self._state = self._blank()

    @staticmethod
    def _blank():
        # notices: 失敗ではない知らせ（reweight＝BeatLeader 側で PP・★が変わったので全部取り直した、
        #          pp-formula＝アプリの PP の式が BeatLeader と合わない＝推定の PP がずれている。アプリの更新が要る）
        return {'running': False, 'phase': '', 'done': 0, 'total': 0, 'error': None, 'warnings': [], 'notices': [],
                'startedAt': None, 'finishedAt': None, 'cancelled': False, 'requests': 0, 'skipped': 0}

    # ---- 外から ----
    def start(self):
        with self._lock:
            if self._state['running']:
                return self.progress()
            self._state = self._blank()
            self._state.update(running=True, startedAt=now())
            self._client = self.client_factory()
            threading.Thread(target=self._run, name='refresh', daemon=True).start()
        return self.progress()

    def cancel(self):
        c = self._client
        if c is not None:
            c.cancel_event.set()
        return self.progress()

    def progress(self):
        st = dict(self._state)
        st['warnings'] = list(st['warnings'])
        st['notices'] = list(st['notices'])
        if self._client is not None:
            st['requests'] = self._client.requests
        return st

    def run_sync(self):
        """テスト・コマンドライン用: その場で最後まで動かして進捗（結果）を返す"""
        with self._lock:
            self._state = self._blank()
            self._state.update(running=True, startedAt=now())
            self._client = self.client_factory()
        self._run()
        return self.progress()

    # ---- 中身 ----
    def _phase(self, name, total=0):
        self._state.update(phase=name, done=0, total=total)

    def _warn(self, code, detail=''):
        self._state['warnings'].append({'code': code, 'detail': str(detail)[:200]})

    def _notice(self, code, detail=''):
        log.info('notice %s %s', code, detail)
        if not any(n['code'] == code for n in self._state['notices']):   # 自分のスコアを取り直した回は同じ知らせが2回出うる
            self._state['notices'].append({'code': code, 'detail': str(detail)[:200]})

    def _run(self):
        # 取得の回は時刻（秒）で区別する（player_snapshots の run_at）。同じ秒に2回取得しても前の回を上書きしないよう、必ず前回より後にする
        run_at = max(now(), int(self.store.meta('last_run_started') or 0) + 1)
        self.store.set_meta('last_run_started', run_at)
        try:
            self._do(run_at)
        except Cancelled:
            self._state['cancelled'] = True
        except NetError as e:
            self._state['error'] = e.code
        except Exception as e:   # 想定外の失敗もアプリは止めない（前回までのデータで続ける）
            log.exception('refresh failed')
            self._state['error'] = 'internal'
            self._warn('internal', '%s: %s' % (type(e).__name__, e))
        finally:
            self._state.update(running=False, finishedAt=now())

    def _do(self, run_at):
        s = self.settings.values()
        pid = s['player_id']
        if not pid:
            self._state['error'] = 'no-player'
            return
        c, st = self._client, self.store

        # 1. 自分
        self._phase('self')
        try:
            me = bl.player(c, pid)
        except NetError as e:
            if e.code == 'http-404':
                self._state['error'] = 'player-not-found'
                return
            raise
        public = me.pop('stats_public', None)   # 統計を公開しているか（None＝分からない）
        self._public = public
        st.set_meta('stats_public', None if public is None else int(public))
        if public is True and st.meta('attempts_private') == '1':
            # 非公開から公開に変えた: プレイ回数・最終挑戦日時が全部 0 のままなので、自分のスコアを全部取り直す（挑戦の記録も待たずに取る）
            st.set_meta('self_scores_synced_at', None)
        prev_run = st.q1('SELECT MAX(run_at) AS t FROM player_snapshots WHERE player_id=?', (pid,))['t']
        with st.tx():
            st.upsert('players', {**me, 'seen_at': run_at, 'is_self': 1}, ['player_id'])
            st.conn.execute('INSERT OR REPLACE INTO player_snapshots(run_at, player_id, pp, rank, country_rank) VALUES(?,?,?,?,?)',
                            (run_at, pid, me['pp'], me['rank'], me['country_rank']))
            st.upsert('self_history', {'date': _day(run_at), 'pp': me['pp'], 'rank': me['rank'],
                                       'country_rank': me['country_rank'], 'source': 'run'}, ['date'])
            st.set_meta('prev_run_at', prev_run)
            st.set_meta('self_country', me['country'])
        country = me['country'] or s['country']
        if s['country'] != country:
            self._warn('country-mismatch', country)

        # 2. 国内ランキング（自分の上下と目標順位のページ）
        pages = pool_pages(me['country_rank'], s['pool_above'], s['pool_below'], s['target_country_rank'])
        self._phase('ranking', len(pages))
        got_any = False
        for i, page in enumerate(pages):
            try:
                rows, _total = bl.country_page(c, country, page)
            except NetError as e:
                if isinstance(e, Cancelled):
                    raise
                self._warn('ranking-page', '%d %s' % (page, e.code))
                continue
            got_any = True
            with st.tx():
                for r in rows:
                    st.upsert('players', {**r, 'seen_at': run_at}, ['player_id'])
                    st.conn.execute('INSERT OR REPLACE INTO player_snapshots(run_at, player_id, pp, rank, country_rank) '
                                    'VALUES(?,?,?,?,?)', (run_at, r['player_id'], r['pp'], r['rank'], r['country_rank']))
            self._state['done'] = i + 1
        if got_any or not pages:   # 1ページも取れなかった回は進めない（前回の目標順位の人・周りの人・比較対象をそのまま使う）
            st.set_meta('ranking_at', run_at)

        # 3. ランク譜面の一覧（全曲データで変化を確かめて、変わった時だけ）。アプリの PP の式の版が変わったら（直した版の最初の取得）全部取り直す
        full = st.meta('pp_formula') != ppcalc.UPSTREAM_COMMIT
        if full:
            self._refetch_all()
        full = self._update_maps(run_at) or full

        # 4. 自分のスコア。BeatLeader 側で PP・★が変わっていたら（リウェイト）、譜面と自分のスコアをこの回で取り直し、比較対象も全員取り直す
        changed = self._fetch_self_scores(pid, run_at, detect=not full)
        if changed:
            self._notice('reweight', changed)
            self._refetch_all()
            self._fetch_maps(run_at)
            self._fetch_self_scores(pid, run_at, detect=False)

        # 5. アテンプト（統計を公開している時だけ）
        self._fetch_attempts(pid, run_at)
        review.update(st, pid, run_at)   # 前回出力した曲の結果（振り返り）

        # 6. 自分の履歴
        self._phase('history')
        try:
            first = not st.q1("SELECT 1 AS x FROM self_history WHERE source='api' LIMIT 1")
            hist = bl.history(c, pid, HISTORY_FIRST if first else HISTORY_NEXT)
            with st.tx():
                for h in hist:
                    d = _day(h['timestamp'])
                    if d == _day(run_at):
                        continue   # 今日の分は起動時の値（source='run'）のほうが新しい
                    st.upsert('self_history', {'date': d, 'pp': h['pp'], 'rank': h['rank'],
                                               'country_rank': h['country_rank'], 'source': 'api'}, ['date'])
        except NetError as e:
            if isinstance(e, Cancelled):
                raise
            self._warn('history', e.code)

        # 7. 比較対象のスコア
        self._fetch_pool(pid, country, me['country_rank'], s['pool_above'], s['pool_below'], run_at)
        st.set_meta('last_run_at', run_at)          # 最後まで取得できた回（起動時の自動の取得はここからの時間で決める）
        st.set_meta('last_run_player', pid)

    def _update_maps(self, run_at):
        """ランク譜面の一覧を取るかを決めて取る。BeatLeader の /maps を毎日取らないように、1日1回、全曲データの★と手元の★を比べ、違いが前に取り直した時と変わった時だけ取る
        （全曲データにはレーティングが無いので、/maps の代わりにはならない。song_details.py）。初回・全部取り直し・設定のボタン（maps_fetched_at が空）は必ず取る。
        多くの譜面の★が変わっていたらリウェイトとして全部取り直す（2026-11-06 の変更を全曲データで見つけられた。自分のスコアの PP の変化での見分けも別に残す）。返り値はそうしたか"""
        c, st = self._client, self.store
        last = int(st.meta('maps_fetched_at') or 0)
        if last and run_at - int(st.meta('maps_checked_at') or 0) < MAPS_INTERVAL:
            return False
        self._phase('mapsCheck')
        ranked, etag, as_of = None, None, int(st.meta('song_details_as_of') or 0)
        try:
            # 必ず取る時は比べる元が要るので、前と同じでも読む
            gz, etag = sd.fetch(c._open, c.user_agent, st.meta('song_details_etag') if last else None, c.cancel_event.is_set)
            if gz is not None:
                as_of, ranked = sd.parse(gz)
            usable = run_at - as_of < MAPS_FALLBACK
        except sd.Unavailable as e:
            log.info('song details unavailable: %s', e)
            usable = False
        st.set_meta('maps_checked_at', run_at)
        rows = lambda: st.q('SELECT hash, mode, difficulty, stars FROM leaderboards WHERE status=3')
        need = not last or run_at - last >= (MAPS_MAX_AGE if usable else MAPS_FALLBACK)
        mass = False
        if ranked is not None and usable and last:
            mine = rows()
            diff = sd.changes(ranked, mine)
            if sd.signature(diff) != st.meta('maps_diff_sig'):
                need = True
                mass = len(diff) >= max(REWEIGHT_MIN, len(mine) * REWEIGHT_SHARE)
                log.info('ranked maps changed: %d differences', len(diff))
        if mass:
            self._notice('reweight', 'stars %d' % len(diff))
            self._refetch_all()
        if need and not self._fetch_maps(run_at):
            return mass   # 取れなかった: ETag と印を覚えないので、次に確かめた時にもう一度取る
        if ranked is not None:
            with st.tx():
                st.set_meta('maps_diff_sig', sd.signature(sd.changes(ranked, rows())))
                st.set_meta('song_details_etag', etag)
                st.set_meta('song_details_as_of', as_of)
        return mass

    def _fetch_maps(self, run_at):
        """ランク譜面の一覧を全部取る。返り値は取れたか"""
        c, st = self._client, self.store
        self._phase('maps')
        rows, page = [], 1
        try:
            while True:
                got, n, total = bl.ranked_maps_page(c, page)
                rows.extend(got)
                if total:
                    self._state['total'] = math.ceil(total / bl.MAPS_PAGE)
                self._state['done'] = page
                if n < bl.MAPS_PAGE or page >= 50:
                    break
                page += 1
        except NetError as e:
            if isinstance(e, Cancelled):
                raise
            self._warn('maps', e.code)
            return False   # 途中までの一覧で「ランクから外れた」と決めつけないよう、全部取れた時だけ入れ替える
        with st.tx():
            st.conn.execute('UPDATE leaderboards SET status=NULL WHERE status=3')   # ランクから外れた譜面を区別する
            for r in rows:
                st.upsert('leaderboards', {**r, 'updated_at': run_at}, ['leaderboard_id'])
            st.set_meta('maps_fetched_at', run_at)
        return True

    def _refetch_all(self):
        """譜面・自分のスコア・比較対象のスコアを全部取り直す印を付ける。印は取り直せた分から消える
        （途中でやめても、次の取得で残りを取る。取り直していない比較対象は、それまで曲線に使わない＝model.build）"""
        st = self.store
        with st.tx():
            st.conn.execute("DELETE FROM meta WHERE key IN ('maps_fetched_at', 'self_scores_synced_at')")
            st.conn.execute('UPDATE players SET scores_fetched_at=NULL')
            st.set_meta('pp_formula', ppcalc.UPSTREAM_COMMIT)

    def _check_formula(self, scores):
        """取った自分のスコア（同じ応答の譜面のレーティングで）をアプリの式で計算し、BeatLeader の PP と比べる。
        合わない＝上流の式が変わった（ppcalc.py を直すまで推定の PP がずれる）"""
        compared = bad = 0
        for row, lbrow in scores:
            if not lbrow or lbrow.get('status') != 3 or not row['pp'] or row['acc'] is None:
                continue
            compared += 1
            if abs(ppcalc.pp_from_row(row['acc'], lbrow, row['modifiers'] or '')['pp'] - row['pp']) > MISMATCH_TOL:
                bad += 1
        if compared >= MISMATCH_MIN_COMPARED and bad >= compared * MISMATCH_SHARE:
            self._notice('pp-formula', '%d/%d' % (bad, compared))

    def _save_self_scores(self, pid, scores, run_at):
        """保存して、自己ベストの日時が同じなのに PP か譜面の★が変わったスコアの譜面の集合と、比べられた数を返す"""
        st = self.store
        changed, compared = set(), 0
        with st.tx():
            for row, lbrow in scores:
                old = st.conn.execute('SELECT s.timeset, s.pp, l.stars FROM scores s LEFT JOIN leaderboards l '
                                      'ON l.leaderboard_id=s.leaderboard_id WHERE s.player_id=? AND s.leaderboard_id=?',
                                      (pid, row['leaderboard_id'])).fetchone()
                if old is not None and old['timeset'] == row['timeset']:
                    compared += 1
                    if (old['pp'] is not None and row['pp'] is not None and abs(old['pp'] - row['pp']) > PP_CHANGED) or \
                            (lbrow and old['stars'] is not None and lbrow['stars'] is not None
                             and abs(old['stars'] - lbrow['stars']) > STARS_CHANGED):
                        changed.add(row['leaderboard_id'])
                if lbrow:
                    # スコアの譜面情報にはアップロード日が無い等、欠けている項目は上書きしない
                    st.upsert('leaderboards', {k: v for k, v in lbrow.items() if v is not None or k == 'leaderboard_id'},
                              ['leaderboard_id'])
                if old is None or (row['timeset'] and old['timeset'] != row['timeset']):
                    st.conn.execute('INSERT OR IGNORE INTO score_history(leaderboard_id, acc, pp, timeset, recorded_at) '
                                    'VALUES(?,?,?,?,?)', (row['leaderboard_id'], row['acc'], row['pp'], row['timeset'], run_at))
                # ★は後で accgraph から入れる（スコアの一覧には無い）。accgraph が失敗した回に前回の★を消さないよう、無い時は書かない
                st.upsert('scores', {**{k: v for k, v in row.items() if not (k == 'stars' and v is None)}, 'player_id': pid},
                          ['player_id', 'leaderboard_id'])
        return changed, compared

    def _fetch_self_scores(self, pid, run_at, detect=True):
        """自分のスコアを取る。detect の時、BeatLeader 側で PP・★が変わっていたら（リウェイト）変わった曲の数を返す（それ以外は 0）"""
        c, st = self._client, self.store
        synced = int(st.meta('self_scores_synced_at') or 0)
        self._phase('selfScores')
        page = 1
        changed, compared, fetched = set(), 0, []
        while True:
            # 非公開の人は lastTryTime が 0 で返るので、2回目からも投稿日時の順で取る（下の止まる条件は timeset を見る）。
            # 初回は全部（新しい順）。2回目からは最近挑戦した順に、前回より前の挑戦が出てくるまで（プレイ回数・最終挑戦日時の更新も拾う）
            got, total = bl.scores_page(c, pid, page, sort='date' if not synced or self._public is False else 'lastTryTime')
            if total:
                self._state['total'] = math.ceil(total / bl.SCORES_PAGE)
            ch, n = self._save_self_scores(pid, got, run_at)
            changed |= ch
            compared += n
            fetched.extend(got)
            self._state['done'] = page
            if len(got) < bl.SCORES_PAGE or page >= 100:
                break
            if synced and min((r['last_try_time'] or r['timeset'] or 0) for r, _ in got) < synced - TRY_MARGIN:
                break
            page += 1
        self._check_formula(fetched)
        # ★はモディファイアで補正した値をそろえるため accgraph のものを使う（比較対象と同じ物差し）
        try:
            pts = bl.accgraph(c, pid)
            with st.tx():
                old = {r['leaderboard_id']: r['stars'] for r in
                       st.q('SELECT leaderboard_id, stars FROM scores WHERE player_id=? AND stars IS NOT NULL', (pid,))}
                stars_compared = 0
                for p in pts:
                    o = old.get(p['leaderboard_id'])
                    if o is not None and p['stars'] is not None:
                        stars_compared += 1
                        if abs(o - p['stars']) > STARS_CHANGED:
                            changed.add(p['leaderboard_id'])
                    st.conn.execute('UPDATE scores SET stars=? WHERE player_id=? AND leaderboard_id=?',
                                    (p['stars'], pid, p['leaderboard_id']))
                compared = max(compared, stars_compared)
                st.conn.execute('UPDATE players SET scores_fetched_at=?, scores_last_ranked_time=last_ranked_score_time '
                                'WHERE player_id=?', (run_at, pid))
        except NetError as e:
            if isinstance(e, Cancelled):
                raise
            self._warn('self-accgraph', e.code)
        st.set_meta('self_scores_synced_at', run_at)
        if detect and len(changed) >= max(REWEIGHT_MIN, compared * REWEIGHT_SHARE):
            log.info('reweight: %d/%d of own scores changed pp or stars', len(changed), compared)
            return len(changed)
        return 0

    def _fetch_attempts(self, pid, run_at):
        c, st = self._client, self.store
        if self._public is False:   # プロフィールの印で非公開と分かる（401 を待たない。公開に変えたら次の取得ですぐ取れる）
            st.set_meta('attempts_private', '1')
            st.set_meta('attempts_checked_at', run_at)
            return
        if self._public is None and st.meta('attempts_private') == '1' and run_at - int(st.meta('attempts_checked_at') or 0) < 7 * 86400:
            return   # 印が分からない時、非公開だった人は1週間に1回だけ確かめ直す
        synced = st.meta('attempts_synced_at')
        floor = run_at - ATTEMPT_DAYS * 86400   # 直近1年分だけ（初めて取る時も、長く開かなかった時も）
        time_from = max(int(synced) - TRY_MARGIN, floor) if synced else floor
        self._phase('attempts')
        page = 1
        try:
            while True:
                got, total = bl.attempts_page(c, pid, page, time_from)
                if total:
                    self._state['total'] = math.ceil(total / bl.ATTEMPTS_PAGE)
                with st.tx():
                    for a in got:
                        if a['timeset']:
                            st.conn.execute('INSERT OR REPLACE INTO attempts(leaderboard_id, timeset, acc, pp, end_type, modifiers) '
                                            'VALUES(?,?,?,?,?,?)', (a['leaderboard_id'], a['timeset'], a['acc'], a['pp'],
                                                                    a['end_type'], a['modifiers']))
                self._state['done'] = page
                if len(got) < bl.ATTEMPTS_PAGE or page >= 200:
                    break
                page += 1
            st.set_meta('attempts_synced_at', run_at)
            st.set_meta('attempts_private', '0')
        except NetError as e:
            if isinstance(e, Cancelled):
                raise
            if e.code in ('http-401', 'http-403'):
                st.set_meta('attempts_private', '1')   # 統計が非公開＝プレイ回数・最終挑戦日時を使わずに続ける
            else:
                self._warn('attempts', e.code)
        st.set_meta('attempts_checked_at', run_at)

    def _fetch_pool(self, pid, country, rank, above, below, run_at):
        st = self.store
        if not rank:
            return
        pool = st.q('SELECT player_id, last_ranked_score_time, ranked_play_count, scores_fetched_at, scores_last_ranked_time FROM players '
                    'WHERE country=? AND country_rank BETWEEN ? AND ? AND seen_at=? AND player_id<>?',
                    (country, rank - above, rank + below, run_at, pid))
        # ランクのスコアが min_scores より少ないと分かっている人は取らない（取っても model.build が外す。国内 1600位台の周りでは 200人のうち 190人）。
        # min_scores はセットごとなので、一番小さい値で見る。順位表の数は毎回新しいので、増えた時・min_scores を下げた時は次の取得で取る（scores_fetched_at が空のまま）
        floor = min(p['values']['min_scores'] for p in self.settings.presets())

        def few(p):
            return p['ranked_play_count'] is not None and p['ranked_play_count'] < floor

        def changed(p):
            return (p['last_ranked_score_time'] or 0) != (p['scores_last_ranked_time'] or 0)
        # 取るのはスコアの多い順に pool_max 人だけ（model.build も同じ人を使う）。外れた人の前のスコアは消さない（また選ばれた時に、変わっていなければ取らずに済む）
        need = [p for p in pick_pool(pool, self.settings.get('pool_max')) if not few(p) and (not p['scores_fetched_at'] or changed(p))]
        # 前に取った人（この版より前の取得・min_scores を下げていた時）のスコアが変わったのに足りないまま: 取り直しても外されるので、古いスコアを使わないよう「取っていない」に戻す
        old = [p['player_id'] for p in pool if few(p) and p['scores_fetched_at'] and changed(p)]
        if old:
            with st.tx():
                st.conn.executemany('UPDATE players SET scores_fetched_at=NULL WHERE player_id=?', [(x,) for x in old])
        self._phase('pool', len(need))
        self._state['skipped'] = len(pool) - len(need)
        if not need:
            return
        c = self._client

        def one(p):
            pts = bl.accgraph(c, p['player_id'])
            with st.tx():
                st.conn.execute('DELETE FROM scores WHERE player_id=?', (p['player_id'],))
                for r in pts:
                    st.upsert('scores', {**r, 'player_id': p['player_id']}, ['player_id', 'leaderboard_id'])
                st.conn.execute('UPDATE players SET scores_fetched_at=?, scores_last_ranked_time=? WHERE player_id=?',
                                (run_at, p['last_ranked_score_time'], p['player_id']))
            return len(pts)

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
            futs = [ex.submit(one, p) for p in need]
            for f in futs:
                try:
                    f.result()
                except Cancelled:
                    for g in futs:
                        g.cancel()
                    raise
                except NetError as e:
                    self._warn('pool', e.code)
                self._state['done'] += 1
