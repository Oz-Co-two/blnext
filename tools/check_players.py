"""ほかの人の公開データで、初回の準備（難しさ違いの3つのリスト）に問題が無いかを確かめる開発用の道具（2026-11-15 に作った）。

統計（Score history）を公開にした人は、アプリを使い始めた人の見込みが高い（アプリが公開を案内している）ので、前の scan から
新しく公開にした人を優先して選ぶ。1人ずつ空の作業用フォルダで、アプリと同じ流れ（setup/start → 取得 → setup/lists）を通し、
3つのリストと推定の材料を調べて、問題の見込みのある所に印を付けた報告（Markdown）を作る。アプリのコードとデータには触らない。

取ったデータ・結果は devdata/player_check/ に置く（.gitignore 済み。ほかの人の ID・名前・曲が入るのでリポジトリに入れない）:
  scans/<日付>.json       国内ランキングの写し（統計を公開しているか・スコア数・最後にスコアを出した時刻）
  checked.json            確かめた人と日付（次に選ぶ時に、最近確かめた人を後回しにする）
  <日付>/<ID>/            その人の作業用の書き込み先（アプリの data/ config/ と同じ形。BLN_DATA_DIR にすれば画面でも見られる）
  <日付>/<ID>.json        その人の結果
  <日付>/report.md        報告

使い方: .venv-build/Scripts/python.exe -X utf8 tools/check_players.py <コマンド>
  scan  [--pages 20]               国内ランキングを上から取り、統計を公開している人を数えて前の scan と比べる（1ページ100人・1回）
  pick  [--n 5]                    今日の scan から確かめる人を選んで見せる（取らない）
  run   [--n 5] [--ids ID,…] [--ranks 順位,…]
                                   選んだ人（--ids・--ranks ならその人）を確かめて報告を作る。今日の scan が無ければ先に scan する。
                                   終わった人は飛ばす（途中で止めても続きから。やり直すなら --again）
  report [<日付>]                  結果（<日付>/*.json）から報告を作り直す（省略時は今日。印は付け直す）
  recollect [<日付>]               保存したデータとリストから調べ直して報告を作り直す（調べる所を直した時。通信しない）
通信: scan は pages 回（/players）。run は 1人 70〜100回（BeatLeader。アプリの初回と同じ）と全曲データ 約10MB（jsDelivr）。
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BASE = os.path.join(ROOT, 'devdata', 'player_check')
ACTIVE_DAYS = 14        # この日数の中にランクのスコアを出した人だけ選ぶ（遊んでいない人のリストは確かめても役に立たない）
MIN_SCORES = 20         # ランクのスコアがこれより少ない人は選ばない
RECHECK_DAYS = 30       # これより前に確かめた人は、まだ確かめていない人と同じに扱う
BANDS = ((1, 50), (51, 100), (101, 200), (201, 500), (501, 1000), (1001, 2000), (2001, 10 ** 9))
MODE_JA = {'Standard': '両手', 'OneSaber': 'ワンセイバー'}


def today():
    return datetime.now().strftime('%Y-%m-%d')


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def my_ids():
    """この PC のアプリの設定のプレイヤー（作者）。選ばない（自分のデータは普段使いで見ている）"""
    out = set()
    for d in (os.path.join(ROOT, 'blnext-app'), ROOT):
        pid = (load(os.path.join(d, 'config', 'settings.json'), {}) or {}).get('player_id')
        if pid:
            out.add(str(pid))
    return out


def client():
    """アプリと同じ名前・版の User-Agent（serve.USER_AGENT と同じ形。serve を読み込むとリポジトリ直下に書き込み先ができるので読まない）"""
    from core.version import app_version
    from core.fetch.http import BLClient
    return BLClient('blnext/%s' % (app_version(ROOT).lstrip('v') or 'dev'))


# ---- scan: 国内ランキングを取って、統計を公開している人を数える ----

def cmd_scan(a):
    c = client()
    rows, total = [], None
    for page in range(1, a.pages + 1):
        res = c.get('/players', countries=a.country, sortBy='pp', count=100, page=page)
        data = res.get('data') or []
        total = (res.get('metadata') or {}).get('total', total)
        for r in data:
            st = r.get('scoreStats') or {}
            rows.append({'id': str(r.get('id')), 'name': r.get('name'), 'countryRank': r.get('countryRank'), 'rank': r.get('rank'),
                         'pp': r.get('pp'), 'scores': st.get('rankedPlayCount'), 'lastScore': st.get('lastRankedScoreTime'),
                         # /players の一覧は本当の値が入る（/player/{id} は stats=true の時だけ。beatleader.stats_public）
                         'public': bool((r.get('profileSettings') or {}).get('showStatsPublic'))})
        if len(data) < 100:
            break
    path = os.path.join(BASE, 'scans', today() + '.json')
    save(path, {'at': int(time.time()), 'country': a.country, 'total': total, 'requests': c.requests, 'players': rows})
    print('%s 位まで %d 人（国内 %s 人）・通信 %d 回 → %s' % (rows[-1]['countryRank'] if rows else '-', len(rows), total, c.requests, path))
    print_scan(rows)


def scans():
    d = os.path.join(BASE, 'scans')
    names = sorted(n for n in os.listdir(d) if n.endswith('.json')) if os.path.isdir(d) else []
    return [(n[:-5], load(os.path.join(d, n))) for n in names]


def newly_public(rows, date):
    """今は公開で、前の scan（date より前）で最後に見た時は非公開だった人の id → 非公開だった日"""
    seen = {}
    for d, s in scans():
        if d >= date or not s:
            continue
        for r in s['players']:
            seen[r['id']] = (d, r['public'])
    return {r['id']: seen[r['id']][0] for r in rows if r['public'] and r['id'] in seen and not seen[r['id']][1]}


def band_counts(rows):
    out = []
    for lo, hi in BANDS:
        b = [r for r in rows if r['countryRank'] and lo <= r['countryRank'] <= hi]
        if b:
            out.append((lo, hi, len(b), sum(1 for r in b if r['public'])))
    return out


def print_scan(rows, date=None):
    date = date or today()
    new = newly_public(rows, date)
    print('統計を公開している人: %d / %d 人（前の scan から新しく公開: %d 人）' % (sum(r['public'] for r in rows), len(rows), len(new)))
    for lo, hi, n, k in band_counts(rows):
        print('  国内 %4d〜%-5s %3d / %3d 人' % (lo, hi if hi < 10 ** 9 else '', k, n))


# ---- pick: 確かめる人を選ぶ ----

def candidates(rows, date):
    """選べる人（公開・作者でない・最近遊んだ・スコアが MIN_SCORES 以上）"""
    now, mine = time.time(), my_ids()
    return [r for r in rows if r['public'] and r['id'] not in mine and (r['scores'] or 0) >= MIN_SCORES
            and r['lastScore'] and now - r['lastScore'] <= ACTIVE_DAYS * 86400]


def pick(rows, n, date):
    """国内順位を対数の目盛りで n 組に分け、組ごとに1人（公開の人は上位に多いので、人数で分けると上位に偏る）。人のいない組の分は
    残りの人から足す。組の中は 新しく公開にした人 → 最近確かめていない人 → スコア 100 以上 → 最近遊んだ人 の順"""
    import math
    cands = sorted(candidates(rows, date), key=lambda r: r['countryRank'])
    if len(cands) <= n:
        return cands
    new = newly_public(rows, date)
    checked = load(os.path.join(BASE, 'checked.json'), {}) or {}
    limit = datetime.fromtimestamp(time.time() - RECHECK_DAYS * 86400).strftime('%Y-%m-%d')
    now = time.time()
    key = lambda r: (r['id'] not in new, any(d >= limit for d in checked.get(r['id'], [])), (r['scores'] or 0) < 100, now - r['lastScore'])
    lo, hi = math.log(cands[0]['countryRank']), math.log(cands[-1]['countryRank'] + 1)
    groups = [[] for _ in range(n)]
    for r in cands:
        groups[min(n - 1, int((math.log(r['countryRank']) - lo) / (hi - lo) * n))].append(r)
    out = [min(g, key=key) for g in groups if g]
    rest = sorted((r for r in cands if r not in out), key=key)
    return sorted(out + rest[:n - len(out)], key=lambda r: r['countryRank'])


def today_scan(a):
    s = load(os.path.join(BASE, 'scans', today() + '.json'))
    if s is None:
        cmd_scan(a)
        s = load(os.path.join(BASE, 'scans', today() + '.json'))
    return s['players']


def cmd_pick(a):
    rows = today_scan(a)
    new = newly_public(rows, today())
    print('選べる人 %d 人（公開・最近 %d 日に遊んだ・スコア %d 以上・作者を除く）' % (len(candidates(rows, today())), ACTIVE_DAYS, MIN_SCORES))
    for r in pick(rows, a.n, today()):
        print('  国内 %4s位 世界 %6s位 スコア %4s %s %s' % (r['countryRank'], r['rank'], r['scores'], r['id'],
                                                   '新しく公開' if r['id'] in new else ''))


# ---- run: 1人ずつ確かめる ----

def cmd_run(a):
    rows = today_scan(a)
    by = {r['id']: r for r in rows}
    if a.ranks:
        at = {str(r['countryRank']): r for r in rows}
        people = [at[x] for x in a.ranks.split(',') if x in at]
    elif a.ids:
        people = [by.get(i) or {'id': i} for i in a.ids.split(',')]
    else:
        people = pick(rows, a.n, today())
    day_dir = os.path.join(BASE, today())
    checked_path = os.path.join(BASE, 'checked.json')
    for r in people:
        out = os.path.join(day_dir, r['id'] + '.json')
        if os.path.exists(out) and not a.again:
            print('== %s は確かめ済み（--again でやり直す）' % r['id'])
            continue
        data = os.path.join(day_dir, r['id'])
        if a.again and os.path.isdir(data):
            import shutil
            shutil.rmtree(data)
        print('== 国内 %s位 %s' % (r.get('countryRank', '?'), r['id']), flush=True)
        p = subprocess.run([sys.executable, '-X', 'utf8', os.path.abspath(__file__), 'one', '--data', data, '--player', r['id'],
                            '--out', out], cwd=ROOT)
        if p.returncode and not os.path.exists(out):
            save(out, {'player': r['id'], 'error': 'exit %d' % p.returncode, 'flags': [['NG', '確かめの途中で止まった（exit %d）' % p.returncode]]})
        checked = load(checked_path, {}) or {}
        checked.setdefault(r['id'], [])
        if today() not in checked[r['id']]:
            checked[r['id']].append(today())
        save(checked_path, checked)
    path = make_report(today())
    print('報告 → %s' % path)


def _q(store, sql, args=()):
    return [dict(r) for r in store.q(sql, args)]


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def cmd_one(a):
    """1人分（別のプロセスで動かす。serve は読み込んだ時の BLN_DATA_DIR を書き込み先にするため）"""
    os.makedirs(a.data, exist_ok=True)
    os.environ['BLN_DATA_DIR'] = os.path.abspath(a.data)
    import serve
    if a.collect_only:   # recollect: 取り直さずに、保存したデータとリストから調べ直す
        res = load(a.out)
        res['flags'] = []
        collect(serve, res)
        save(a.out, res)
        return
    res = {'player': a.player, 'at': int(time.time()), 'flags': []}

    def done():
        save(a.out, res)

    try:
        st = serve.API['setup/start']({'id': a.player})
    except serve.ApiError as e:
        res['error'] = 'setup/start: %s' % e.code
        res['flags'].append(['NG', '初回の準備で人を選べなかった（%s）' % e.code])
        return done()
    res['statsPublic'] = st.get('statsPublic')
    t0 = time.time()
    serve.REFRESHER.start()
    last = None
    while True:
        p = serve.REFRESHER.progress()
        if p['phase'] != last:
            print('  %5.1fs %s' % (time.time() - t0, p['phase']), flush=True)
            last = p['phase']
        if not p['running'] and p['finishedAt']:
            break
        time.sleep(0.5)
    res['refresh'] = {'seconds': round(time.time() - t0, 1), 'requests': p['requests'], 'error': p['error'],
                      'warnings': p['warnings'], 'notices': [n.get('code') if isinstance(n, dict) else n for n in p['notices']]}
    if p['error']:
        res['error'] = 'refresh: %s' % p['error']
        res['flags'].append(['NG', '取得が失敗した（%s）' % p['error']])
        return done()
    t1 = time.time()
    try:
        lists = serve.API['setup/lists']({})
    except Exception as e:   # 例外もそのまま報告に残す（アプリでは画面にエラーが出る所）
        import traceback
        res['error'] = 'setup/lists: %r' % e
        res['traceback'] = traceback.format_exc()
        res['flags'].append(['NG', 'リストを作る所で例外（%r）' % e])
        return done()
    res['listsSeconds'] = round(time.time() - t1, 1)
    print('  リスト %.1fs' % res['listsSeconds'], flush=True)
    collect(serve, res)
    done()
    for lv, msg in res['flags']:
        print('  [%s] %s' % (lv, msg))


def collect(serve, res):
    """初回の準備（setup/lists）の後の serve の状態から、res に人・スコア・挑戦の記録・セットごとのリストと推定の材料を足し、印を付ける"""
    from core import views
    from core.estimate import clear as clearmod
    from core.settings import difficulty_of
    store, settings = serve.STORE, serve.SETTINGS
    h = views.home(store, settings)
    if h.get('hasData'):
        me = h['me']
        res['me'] = {'countryRank': me['countryRank'], 'rank': me['rank'], 'pp': me['pp']}
        res['ppCheck'] = h['ppCheck']
    pid = settings.values()['player_id']
    sc = _q(store, 'SELECT s.modifiers, s.acc, s.stars, l.mode FROM scores s JOIN leaderboards l USING(leaderboard_id) WHERE s.player_id=?', (pid,))
    mods = {}
    for r in sc:
        for m in filter(None, (r['modifiers'] or '').split(',')):
            mods[m] = mods.get(m, 0) + 1
    modes = {}
    for r in sc:
        modes[r['mode']] = modes.get(r['mode'], 0) + 1
    res['scores'] = {'n': len(sc), 'withMods': sum(1 for r in sc if (r['modifiers'] or '').strip(',')), 'mods': mods, 'modes': modes}
    # 挑戦の記録（直近1年・練習と NF を除く）の（譜面, 日）のまとめ。失敗が記録されない人（clear.soft_only）を見分ける材料
    since = int(time.time()) - clearmod.ATTEMPT_DAYS * 86400
    att = _q(store, 'SELECT leaderboard_id, timeset, end_type, modifiers, acc FROM attempts WHERE timeset IS NOT NULL')
    ends = {}
    for r in att:
        ends[str(r['end_type'])] = ends.get(str(r['end_type']), 0) + 1
    days = {}
    for r in att:
        if r['timeset'] < since or r['end_type'] not in (1, 2, 3, 4) or 'NF' in (r['modifiers'] or ''):
            continue
        d = days.setdefault((r['leaderboard_id'], clearmod.day_key(r['timeset'])), {'ok': False, 'hard': False})
        d['ok'] = d['ok'] or r['end_type'] == 1
        d['hard'] = d['hard'] or r['end_type'] == 2
    vals = list(days.values())
    # NF が付いた挑戦（ゲージが 0 になっても続けた挑戦と読める。2026-11-15 の確かめで、精度の中央 60〜75%・PP は全部 0）。
    # アプリはクリアの見込みの材料から外しているので、NF で遊ぶ人は失敗が見えず「失敗が記録されない人」になる
    nf = [r for r in att if r['timeset'] >= since and r['end_type'] in (1, 2, 3, 4) and 'NF' in (r['modifiers'] or '')]
    res['nfAttempts'] = {'n': len(nf), 'accMedian': _median([r['acc'] for r in nf if r['end_type'] == 1 and r['acc']]),
                         'of': sum(1 for r in att if r['timeset'] >= since and r['end_type'] in (1, 2, 3, 4))}
    res['attempts'] = {'rows': len(att), 'ends': ends, 'oldest': min((r['timeset'] for r in att), default=None),
                       'days': len(vals), 'okDays': sum(d['ok'] for d in vals),
                       'hardDays': sum(1 for d in vals if not d['ok'] and d['hard']),
                       'softDays': sum(1 for d in vals if not d['ok'] and not d['hard']),
                       'softOnly': clearmod.soft_only(vals)}

    sets, all_hashes = [], {}
    for pr in settings.presets(brief=True):
        view = settings.view(pr['id'])
        v = view.values()
        model, est = serve.ENGINE.get(view)
        saved = serve._lists(view)[0]
        lst = saved['list'] if saved else {'items': [], 'summary': None, 'filters': {}}
        items = lst['items']
        for c in items:
            all_hashes.setdefault(c['hash'], []).append(pr['name'])
        f = lst.get('filters') or {}
        s = {'id': pr['id'], 'name': pr['name'], 'level': difficulty_of(v), 'size': v['playlist_size'], 'songs': len(items),
             'gain': (lst.get('summary') or {}).get('gain'), 'starMin': v['star_min'], 'starMax': v['star_max'],
             'starMinAuto': f.get('starMinAuto'), 'minClear': v['min_clear'], 'short': lst.get('short'),
             'fewSimilar': lst.get('fewSimilar'), 'noClear': lst.get('noClear'), 'similarCount': lst.get('similarCount'),
             'out': {k: f.get(k) for k in ('starOut', 'clearOut', 'passOut', 'otherOut', 'cooldown', 'starTopOut', 'challenge')},
             'candidates': len(est),
             'items': [{'song': c['song'], 'difficulty': c['difficulty'], 'mode': c['mode'], 'stars': c['stars'], 'kind': c['kind'],
                        'tried': bool(c.get('tried')), 'est': c['est_acc'], 'best': c['best_acc'], 'gain': c['gain'],
                        'clear': c.get('clear'), 'pUp': c.get('p_up'), 'challenge': c.get('challenge'), 'flags': c.get('flags'),
                        'pass': c.get('pass_rating'), 'tech': c.get('tech_rating'), 'similarN': c.get('similar_n'),
                        'cap': (c.get('acc_cap') or {}).get('cap') if isinstance(c.get('acc_cap'), dict) else None,
                        'mods': c.get('best_mods')} for c in items]}
        if model is not None:
            cm = model.clear
            s['model'] = {'nScores': model.me.n_scores, 'similar': len(model.similar), 'pool': len(model.pool),
                          'poolSize': model.pool_size, 'fewSimilar': model.few_similar, 'starHi': model.star_hi,
                          'popSlope': model.pop_slope, 'starScale': model.star_scale, 'ceiling': model.ceiling is not None,
                          'clear': None if cm is None else {'a': cm.a, 'b': cm.b, 'days': cm.days, 'fails': cm.fails, 'c': cm.c,
                                                            'failFitted': cm.fail_fitted}}
        sets.append(s)
    res['sets'] = sets
    res['dupSongs'] = {h: n for h, n in all_hashes.items() if len(n) > 1}
    res['flags'] += judge(res)
    return res


# ---- 問題の見込みのある所 ----

def judge(res):
    """[(重さ, 文)]。NG＝不具合の見込みが高い、注意＝見ておく所、情報＝その人の性質（不具合ではない）"""
    out = []
    add = lambda lv, msg: out.append([lv, msg])
    pp = (res.get('me') or {}).get('pp') or 0
    if res.get('ppCheck') and not res['ppCheck'].get('ok'):
        add('NG', 'アプリの PP の式と BeatLeader の総PP がずれた（%s / %s）' % (res['ppCheck'].get('calc'), res['ppCheck'].get('api')))
    for w in (res.get('refresh') or {}).get('warnings') or []:
        add('注意', '取得の警告 %s %s' % (w.get('code'), w.get('detail', '')[:80]))
    if res.get('listsSeconds', 0) > 30:
        add('注意', 'リストを作るのに %.0f秒かかった' % res['listsSeconds'])
    sets = res.get('sets') or []
    for s in sets:
        name = '%s（%s）' % (s['name'], s['level'])
        if s['songs'] == 0:
            add('NG', '%s が 0曲（候補 %d・外した数 %s）' % (name, s['candidates'], s['out']))
            continue
        if s['songs'] < s['size']:
            add('NG', '%s が %d / %d 曲（外した数 %s）' % (name, s['songs'], s['size'], s['out']))
        g = s['gain'] or 0
        each = [((c['gain'] or 0) * (1.0 if c['clear'] is None else c['clear']), c) for c in s['items']]
        top, c = max(each, key=lambda x: x[0])
        if top > 15 and g and top > 0.4 * g:
            add('注意', '%s の見込みの %.0f%% が1曲（%s ★%.2f %s 推定 %.2f%% +%.1fPP）' % (
                name, 100 * top / g, c['song'], c['stars'] or 0, '未プレイ' if c['kind'] != 'played' else 'プレイ済み',
                100 * (c['est'] or 0), top))
        for c in s['items']:
            if c['kind'] != 'played' and (c['est'] or 0) >= 0.99 and (c['gain'] or 0) >= 10:
                add('注意', '%s に推定 99%% 以上の未プレイ（%s ★%.2f 推定 %.2f%% +%.1fPP）' % (
                    name, c['song'], c['stars'] or 0, 100 * c['est'], c['gain']))
            if s['minClear'] and c['clear'] is not None and 100 * c['clear'] < s['minClear'] - 0.01 and not c['challenge']:
                add('NG', '%s にクリアの見込みが下限 %s%% より低い曲（%s %.0f%%）' % (name, s['minClear'], c['song'], 100 * c['clear']))
        un = [c['gain'] or 0 for c in s['items'] if c['kind'] != 'played']
        pl = [c['gain'] or 0 for c in s['items'] if c['kind'] == 'played']
        if un and pl and max(un) < 0.1 and min(pl) >= 1:
            # 仕様（2026-11-15 に利用者が決めた）: 未プレイの枠は「プレイ済みの比率」で利用者が変えられるので、このままにする
            add('情報', '%s の未プレイ %d曲の見込みがほぼ 0（合わせて +%.2fPP。プレイ済みは +%.1fPP 以上）。スコアが多い人は新しい曲が総PP に効かない'
                       '（仕様。「プレイ済みの比率」で変えられる）' % (name, len(un), sum(un), min(pl)))
        if pp and g > 0.02 * pp:
            add('注意', '%s の見込み +%.0fPP が総PP の %.1f%%（大きすぎないか）' % (name, g, 100 * g / pp))
    # 難しさの順（同じモードの★の中央。着実 ≤ 標準 ≤ チャレンジング）
    order = [s for lv in ('steady', 'standard', 'challenge') for s in sets if s['level'] == lv and s['items']]
    if len(order) >= 2:
        mode = max((c['mode'] for s in order for c in s['items']), key=lambda m: sum(c['mode'] == m for s in order for c in s['items']))
        med = [(s['name'], _median([c['stars'] for c in s['items'] if c['mode'] == mode])) for s in order]
        for (n1, m1), (n2, m2) in zip(med, med[1:]):
            if m1 is not None and m2 is not None and m1 > m2 + 0.3:
                add('NG', '★の中央が難しさの順になっていない（%s ★%.2f > %s ★%.2f）' % (n1, m1, n2, m2))
    if res.get('dupSongs'):
        add('NG', '同じ曲が2つのセットに入った（%d曲）' % len(res['dupSongs']))
    m = next((s['model'] for s in sets if s.get('model')), None)
    sc = res.get('scores') or {}
    at = res.get('attempts') or {}
    if m:
        if m['similar'] == 0:
            add('注意', '似ている人が 0人（比較対象 %d人）' % m['pool'])
        elif m['fewSimilar']:
            add('情報', '似ている人が少ない（%d人）' % m['similar'])
        if res.get('statsPublic') and m['clear'] is None:
            if at.get('softOnly'):   # 失敗が無いので回帰できない（統計が非公開の人と同じ扱い。決めてある動き）
                add('情報', 'クリアの見込みは無し（失敗が記録されない人。統計が非公開の人と同じ扱い）')
            else:
                add('注意', '統計は公開なのにクリアの見込みが求まらない（挑戦の記録 %d行・%d日）' % (at.get('rows', 0), at.get('days', 0)))
    if sc.get('n') and sc['withMods'] >= 0.5 * sc['n']:
        add('注意', 'スコアの %d / %d がモディファイア付き（推定は素の★で読むので外れやすい。%s）' % (sc['withMods'], sc['n'], sc['mods']))
    nf = res.get('nfAttempts') or {}
    if at.get('softOnly') and nf.get('n', 0) >= max(20, 0.1 * nf.get('of', 0)):
        # 仕様（2026-11-15 に利用者が決めた）: NF は難しい曲の練習と読み、NF で失敗した曲が標準・チャレンジングに出てもよい。このままにする
        add('情報', '失敗が NF として残る人（直近1年の挑戦 %d のうち NF %d・NF で最後まで遊んだ時の精度の中央 %s%%）。NF の挑戦を読まないので'
                    'クリアの見込みが効かない（仕様）' % (nf['of'], nf['n'], '-' if nf.get('accMedian') is None else '%.0f' % (100 * nf['accMedian'])))
    elif at.get('softOnly'):
        add('情報', '失敗が記録されない人（やり直し・やめただけの日を数えない。clear.HARD_MIN_SHARE）')
    if res.get('statsPublic') is False:
        add('情報', '統計が非公開になっていた')
    return out


# ---- 報告 ----

def _pct(x):
    return '' if x is None else '%.2f' % (100 * x)


def make_report(date):
    day_dir = os.path.join(BASE, date)
    results = [load(os.path.join(day_dir, n)) for n in sorted(os.listdir(day_dir)) if n.endswith('.json')] if os.path.isdir(day_dir) else []
    results = sorted((r for r in results if r), key=lambda r: (r.get('me') or {}).get('countryRank') or 10 ** 9)
    for r in results:   # 印は保存した結果から付け直す（印の決まりを直した後に report で作り直せる。止まった人は止まった時の印のまま）
        if r.get('sets') is not None:
            r['flags'] = judge(r)
    scan = load(os.path.join(BASE, 'scans', date + '.json'))
    L = ['# ほかの人のデータでリストの確かめ（%s）' % date, '',
         '`tools/check_players.py` の出力。ほかの人の ID・名前・曲が入るので、リポジトリ・notes には数字と結論だけを写す。', '']
    if scan:
        rows = scan['players']
        new = newly_public(rows, date)
        L += ['## 統計を公開している人（国内 %s位まで）' % (rows[-1]['countryRank'] if rows else '-'), '',
              '公開 %d / %d 人・前の scan から新しく公開 %d 人・選べる人（最近 %d 日に遊んだ・スコア %d 以上・作者を除く）%d 人' % (
                  sum(r['public'] for r in rows), len(rows), len(new), ACTIVE_DAYS, MIN_SCORES, len(candidates(rows, date))), '',
              '| 国内 | 人数 | 公開 |', '| --- | ---: | ---: |']
        L += ['| %d〜%s | %d | %d |' % (lo, hi if hi < 10 ** 9 else '', n, k) for lo, hi, n, k in band_counts(rows)]
        L.append('')
    L += ['## まとめ', '', '| 国内 | 世界 | スコア | 挑戦の記録（日） | 似ている人 | 着実 | 標準 | チャレンジング | NG | 注意 |',
          '| ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: |']
    for r in results:
        me = r.get('me') or {}
        m = next((s['model'] for s in r.get('sets') or [] if s.get('model')), {}) or {}
        cell = {}
        for s in r.get('sets') or []:
            cell[s['level']] = '%d曲 +%.0f' % (s['songs'], s['gain'] or 0)
        n = lambda lv: sum(1 for f in r['flags'] if f[0] == lv)
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %d | %d |' % (
            me.get('countryRank', '?'), me.get('rank', '?'), (r.get('scores') or {}).get('n', '?'), (r.get('attempts') or {}).get('days', '?'),
            m.get('similar', '?'), cell.get('steady', '-'), cell.get('standard', '-'), cell.get('challenge', '-'), n('NG'), n('注意')))
    L.append('')
    for r in results:
        me = r.get('me') or {}
        L += ['## 国内 %s位（世界 %s位・%.0fPP）' % (me.get('countryRank', '?'), me.get('rank', '?'), me.get('pp') or 0), '']
        if r.get('error'):
            L += ['**止まった**: %s' % r['error'], '']
        for lv, msg in r['flags']:
            L.append('- **%s** %s' % (lv, msg) if lv == 'NG' else '- %s %s' % (lv, msg))
        if not r['flags']:
            L.append('- 問題の印なし')
        L.append('')
        rf, sc, at = r.get('refresh') or {}, r.get('scores') or {}, r.get('attempts') or {}
        m = next((s['model'] for s in r.get('sets') or [] if s.get('model')), None)
        L.append('- 取得 %s秒・%s回・リスト %s秒。スコア %s（モディファイア付き %s。%s）・モード %s' % (
            rf.get('seconds'), rf.get('requests'), r.get('listsSeconds'), sc.get('n'), sc.get('withMods'), sc.get('mods'),
            {MODE_JA.get(k, k): v for k, v in (sc.get('modes') or {}).items()}))
        if at:
            oldest = datetime.fromtimestamp(at['oldest']).strftime('%Y-%m-%d') if at.get('oldest') else '-'
            L.append('- 挑戦の記録 %d行（一番古い %s・終わり方 %s）。直近1年の（譜面, 日）%d: クリア %d・失敗 %d・やり直しとやめただけ %d%s' % (
                at['rows'], oldest, at['ends'], at['days'], at['okDays'], at['hardDays'], at['softDays'], '（失敗が記録されない人）' if at['softOnly'] else ''))
        if m:
            cm = m['clear']
            L.append('- 似ている人 %d / 比較対象 %d（範囲 %d）・★の範囲の上端 %s・上の下がり方 %s・モードの換算 %s・精度の上限 %s・クリアの見込み %s' % (
                m['similar'], m['pool'], m['poolSize'], None if m['starHi'] is None else round(m['starHi'], 2),
                None if m['popSlope'] is None else round(m['popSlope'], 2), m['starScale'] or '無し', 'あり' if m['ceiling'] else '無し',
                '無し' if cm is None else 'a=%.2f b=%.2f（%d日・できなかった日 %d・c=%.2f%s）' % (
                    cm['a'], cm['b'], cm['days'], cm['fails'], cm['c'], '' if cm['failFitted'] else ' 既定')))
        L += ['', '| セット | 曲数 | 見込み | ★の範囲 | ★の中央 | プレイ済み / 未プレイ（未クリア） | 挑戦 | クリアの見込み 最小 | 外した数 |',
              '| --- | ---: | ---: | --- | ---: | --- | ---: | ---: | --- |']
        for s in r.get('sets') or []:
            it = s['items']
            auto = s.get('starMinAuto') or {}
            lo = ('自動' + '/'.join('%s' % (v if v is None else round(v, 1)) for v in auto.values())) if not s['starMin'] else s['starMin']
            clears = [c['clear'] for c in it if c['clear'] is not None]
            L.append('| %s（%s） | %d/%d | +%.1f | %s〜%s | %s | %d / %d（%d） | %d | %s | %s |' % (
                s['name'], s['level'], s['songs'], s['size'], s['gain'] or 0, lo, s['starMax'] or '上限なし',
                '' if not it else '%.2f' % _median([c['stars'] for c in it]),
                sum(c['kind'] == 'played' for c in it), sum(c['kind'] != 'played' for c in it), sum(c['tried'] for c in it),
                sum(bool(c['challenge']) for c in it), '' if not clears else '%.0f%%' % (100 * min(clears)),
                ' '.join('%s %s' % (k, v) for k, v in s['out'].items() if v)))
        L.append('')
        for s in r.get('sets') or []:
            if not s['items']:
                continue
            L += ['<details><summary>%s の曲</summary>' % s['name'], '', '| 曲 | 難易度 | モード | ★ | 種類 | 推定 | 自己ベスト | 見込み | クリア | 印 |',
                  '| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |']
            for c in s['items']:
                kind = 'プレイ済み' if c['kind'] == 'played' else ('未クリア' if c['tried'] else '未プレイ')
                L.append('| %s | %s | %s | %.2f | %s%s | %s | %s | %+.1f | %s | %s |' % (
                    c['song'].replace('|', '/'), c['difficulty'], MODE_JA.get(c['mode'], c['mode']), c['stars'] or 0, kind,
                    '・挑戦' if c['challenge'] else '', _pct(c['est']), _pct(c['best']), c['gain'] or 0,
                    '' if c['clear'] is None else '%.0f%%' % (100 * c['clear']), ' '.join(c['flags'] or [])))
            L += ['', '</details>', '']
    path = os.path.join(day_dir, 'report.md')
    os.makedirs(day_dir, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    return path


def cmd_report(a):
    print(make_report(a.date or today()))


def cmd_recollect(a):
    """その日の結果を、保存したデータ（取得・リストはそのまま）から調べ直して報告を作り直す（調べる所を直した時。通信しない）"""
    date = a.date or today()
    day_dir = os.path.join(BASE, date)
    for n in sorted(os.listdir(day_dir)):
        out, data = os.path.join(day_dir, n), os.path.join(day_dir, n[:-5])
        if not n.endswith('.json') or not os.path.isdir(data) or not (load(out) or {}).get('sets'):
            continue
        print('== %s' % n[:-5], flush=True)
        subprocess.run([sys.executable, '-X', 'utf8', os.path.abspath(__file__), 'one', '--data', data, '--player', n[:-5],
                        '--out', out, '--collect-only'], cwd=ROOT, env={**os.environ, 'BLN_NO_NET': '1'})
    print(make_report(date))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('scan', 'pick', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--country', default='jp')
        p.add_argument('--pages', type=int, default=20)
        if name != 'scan':
            p.add_argument('--n', type=int, default=5)
        if name == 'run':
            p.add_argument('--ids', default='')
            p.add_argument('--ranks', default='', help='今日の scan の国内順位で選ぶ（例 31,72,223）')
            p.add_argument('--again', action='store_true')
    for name in ('report', 'recollect'):
        p = sub.add_parser(name)
        p.add_argument('date', nargs='?')
    p = sub.add_parser('one')
    p.add_argument('--data', required=True)
    p.add_argument('--player', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--collect-only', action='store_true')
    a = ap.parse_args()
    {'scan': cmd_scan, 'pick': cmd_pick, 'run': cmd_run, 'report': cmd_report, 'recollect': cmd_recollect, 'one': cmd_one}[a.cmd](a)


if __name__ == '__main__':
    main()
