# 画面の操作: おすすめ画面（並べ替え・固定・外す・入れ替え・条件・出力）・設定・ホーム・似ている人
import json, os

FIRST = '.recList .recRow:not(.head)'   # 一覧の最初の行（querySelector で使う）
IDS = "[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.dataset.id)"


def col(app, i):
    """一覧の i 列目の文字（見えている並び）"""
    return app.js("[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.children[%d].innerText)" % i)


def pct(vals):
    return [float(v.split('\n')[0].rstrip('%').replace(',', '')) if v.strip() not in ('—', '') else None for v in vals]


def test_sort_by_header(ui):
    ui.open('recommend')
    before = ui.js(IDS)
    ui.click_sel('.recRow.head .th', text='推定精度')
    est = pct(col(ui, 5))
    assert est == sorted(est, reverse=True)
    ui.click_sel('.recRow.head .th', text='推定精度')
    assert pct(col(ui, 5)) == sorted(est)
    ui.click_sel('.recRow.head .th', text='★')
    st = pct(col(ui, 2))
    assert st == sorted(st, reverse=True)
    # 見出しで並べ替えた順は書き出す順として保存する（tests/e2e/test_order_ui.py）。「基本の並びに戻す」で戻る
    ui.click_sel('button.orderReset', text='基本の並びに戻す')
    ui.until(f"JSON.stringify({IDS})===JSON.stringify({json.dumps(before)})", what='基本の並びに戻る')


def test_map_features_and_beatleader_pages(ui, monkeypatch):
    # 曲名の下に NJS・BPM・ノーツ数。曲を開くと譜面の特徴と、BeatLeader のページを開くボタン
    # （遊んだ曲は自分のリプレイも。URL は Python が組み立て、本物のブラウザは開かない）
    opened = []
    monkeypatch.setattr(ui.serve, 'open_browser', opened.append)
    # 未プレイの曲も出る条件（作り物のデータの初期の条件。前のテストが初期値に戻していることがある）
    ui.serve.SETTINGS.update({'max_map_age_years': 0}, preset='p1')
    ui.open('recommend')
    line2 = ui.js("document.querySelector('%s .line2').innerText" % FIRST)
    assert all(x in line2 for x in ('NJS', 'BPM', 'ノーツ', '/秒')), line2
    assert ui.js("document.querySelector('%s').children[2].innerText" % FIRST).split('\n')[1].startswith('pass ')
    pid = ui.serve.SETTINGS.values()['player_id']
    kinds = ui.js("[...document.querySelectorAll('.recList .recRow:not(.head)')].map(r=>[r.dataset.id, r.querySelector('.badge').classList.contains('played')])")
    played = next(i for i, p in kinds if p)
    unplayed = next(i for i, p in kinds if not p)

    ui.click_sel(f'.recRow[data-id="{played}"] .songName')
    ui.until("!!document.querySelector('.mapInfo')", what='譜面の欄')
    info = ui.js("document.querySelector('.mapInfo').innerText")
    assert all(x in info for x in ('BeatLeader の評価', 'pass', 'NJS', 'BPM', '傾向', 'あなたがクリアしてきた曲の95%')), info
    ui.click_sel('.mapInfo button', text='自分のリプレイを見る')
    ui.until("document.querySelector('#toast').innerText.includes('リプレイを開きました')", what='開いた知らせ')
    sid = ui.serve.STORE.q1('SELECT score_id FROM scores WHERE player_id=? AND leaderboard_id=?', (pid, played))['score_id']
    assert opened == ['https://replay.beatleader.com/?scoreId=%d' % sid]

    ui.click_sel(f'.recRow[data-id="{played}"] .songName')     # 閉じる
    ui.click_sel(f'.recRow[data-id="{unplayed}"] .songName')
    ui.until("document.querySelectorAll('.mapInfo').length === 1", what='遊んでいない曲の欄')
    assert ui.js("[...document.querySelectorAll('.mapInfo button')].map(b=>b.innerText)") == ['BeatLeader でこの譜面のランキングを見る']
    ui.click_sel('.mapInfo button', text='BeatLeader でこの譜面のランキングを見る')
    ui.until("document.querySelector('#toast').innerText.includes('BeatLeader のこの譜面のページを開きました')", what='開いた知らせ')
    assert opened[1] == 'https://beatleader.com/leaderboard/global/%s/1' % unplayed


def test_tried_unplayed_and_fail_note(ui, monkeypatch):
    # 自己ベストは無いが挑戦の記録がある曲（作り物のデータの1曲）は「未クリア」の印。曲数の欄に「うち未クリア」、開いた欄の先頭に
    # 挑戦の回数（練習は数えない）と最後の挑戦。クリアの見込みがある時は、クリアできなかった日で下げた理由も出る
    from collections import OrderedDict
    from core.estimate import clear as CL
    ui.serve.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50},
                             preset='p1')   # 未プレイの曲も出る条件（作り物のデータの初期の条件。前のテストが初期値に戻していることがある）
    ui.serve._api_recommend_rebuild({'preset': 'p1'})
    ui.open('recommend')
    rows = ui.js("[...document.querySelectorAll('.recList .recRow:not(.head)')].filter(r=>r.querySelector('.badge.tried')).map(r=>r.dataset.id)")
    assert len(rows) == 1
    lid = rows[0]
    assert ui.js("document.querySelector('.badge.tried').innerText").split() == ['未', 'クリア']   # 2行
    assert 'うち未クリア 1）' in ui.js("document.querySelector('.sumTitle').innerText")
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    info = ui.js("document.querySelector('.reasons .bestInfo').innerText")
    assert info.startswith('挑戦 3 回（クリアなし）') and info.endswith('（78.1% で失敗）') and '最後 ' in info, info

    # クリアの見込み（作り物の挑戦の記録は少ないので、ここで入れる）。未クリアの曲は、途中でやめただけの日と失敗した日で下げる
    # （下がってリストから外れるので、固定して理由を見る）
    view = ui.serve.SETTINGS.view('p1')
    model, _ = ui.serve.ENGINE.get(view)
    monkeypatch.setattr(model, 'clear', CL.ClearModel(6.0, -1.5, 100, 20, c=-2.0))
    monkeypatch.setattr(ui.serve.ENGINE, '_ests', OrderedDict())   # 推定を作り直させる（終わったら元の推定に戻る）
    ui.serve._api_recommend_action({'action': 'pin', 'id': lid, 'preset': 'p1'})
    ui.open('recommend')
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    why = ui.js("[...document.querySelectorAll('.reasons div')].map(d=>d.innerText).find(t=>t.startsWith('その日のうちにクリアできる見込み'))")
    assert 'この譜面でクリアできなかった日が 2 日（うちやり直し・途中でやめただけの日 1）あるので' in why and '% から下げた）' in why, why


def test_star_scale_note(ui, monkeypatch):
    # モードごとの★の換算（model.star_scales）: 換算するモードの曲は、開いた欄に「×倍率 した★ で曲線を読んでいます」と出す
    # （作り物のデータは全部両手のモードなので、ここで両手に倍率を入れる）
    from collections import OrderedDict
    view = ui.serve.SETTINGS.view('p1')
    model, _ = ui.serve.ENGINE.get(view)
    monkeypatch.setattr(model, 'star_scale', {'Standard': 0.9})
    monkeypatch.setattr(ui.serve.ENGINE, '_ests', OrderedDict())   # 推定を作り直させる（終わったら元の推定に戻る）
    r = ui.serve._api_recommend_rebuild({'preset': 'p1'})
    assert r['calc']['starScale'] == {'Standard': 0.9}
    c = r['items'][0]
    ui.open('recommend')
    ui.click_sel(f'.recRow[data-id="{c["leaderboard_id"]}"] .songName')
    text = ui.js("document.querySelector('.reasons').innerText")
    assert 'Standard の★は、あなたの記録ではいつもの曲線より精度が出やすいので、×0.90 した★%.2f で曲線を読んでいます。' % (
        c['stars'] * 0.9) in text, text


def test_cap_and_pb_notes(ui, monkeypatch):
    # 精度の上限（model.AccCeiling）で見込みを削った曲は、開いた欄に上限の値・その精度の譜面・近い★の範囲・上限より上だった割合を出す。
    # R4（前の自己ベストとの真ん中に寄せた）は印 pb の決まり文句。どちらも「下の • の分も直して」に入る。
    # 上限の譜面は、HTML のタグに見える名前の作り物の曲（LB005）にして、文字のまま出ることも確かめる
    from collections import OrderedDict
    from fixture_data import EVIL
    view = ui.serve.SETTINGS.view('p1')
    model, est = ui.serve.ENGINE.get(view)
    c0 = next(c for c in est if c['kind'] == 'played' and c['p_up'] and c['gain'] > 0)
    lid = c0['leaderboard_id']
    # 上限＝近い★のほかの曲の一番＋難しさの余裕（p1 は標準の 0.5%）。上限が自己ベストと推定のすぐ上（幅の上の半分ほどが上限より上）になるように
    margin = view.values()['acc_cap_margin'] / 100
    assert margin == 0.005
    cap = max(c0['best_acc'], c0['est_acc']) + 0.002
    top = cap - margin
    # 4つ目は使った窓の幅。近い★の曲が足りずに ±1.0 まで広げた上限にする（説明の「近い★（★〜★）」が広げた幅になる）
    monkeypatch.setattr(model, 'ceiling', lambda stars, mode, exclude=None: (top, 'LB005', 7, 1.0) if exclude == lid else (None, None, 2, None))
    monkeypatch.setattr(ui.serve.ENGINE, '_ests', OrderedDict())   # 推定を作り直させる（終わったら元の推定に戻る）
    ui.serve._api_recommend_action({'action': 'pin', 'id': lid, 'preset': 'p1'})   # 見込みが下がっても出るように固定する
    r = ui.serve._api_recommend({'preset': 'p1'})
    c = next(x for x in r['items'] if x['leaderboard_id'] == lid)
    assert 'cap' in c['flags'] and abs(c["acc_cap"]["acc"] - cap) < 1e-12 and c['acc_cap']['top'] == top and c['acc_cap']['over'] >= 0.1
    ui.open('recommend')
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    notes = ui.js("[...document.querySelectorAll('.reasons .flag')].map(d=>d.innerText)")
    want = ('• 近い★（★%.2f〜★%.2f・7 曲）のあなたのほかの曲の一番 %.2f%%（%s ExpertPlus ★3.50）＋難しさの余裕 0.5%% ＝ %.2f%%を、'
            'この曲の精度の上限にしています。次に遊んだ日の一番のぶれのうち %.0f%% が上限より上だったので' % (
                c['stars'] - 1.0, c['stars'] + 1.0, 100 * top, EVIL, 100 * cap, 100 * c['acc_cap']['over']))
    assert any(x.startswith(want) for x in notes), (want, notes)
    assert '下の • の分も直して' in ui.js("document.querySelector('.reasons').innerText")
    assert ui.js('window.__xss === undefined')
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')    # 閉じる
    pb = next(x for x in r['items'] if 'pb' in x['flags'])
    ui.click_sel(f'.recRow[data-id="{pb["leaderboard_id"]}"] .songName')
    notes = ui.js("[...document.querySelectorAll('.reasons .flag')].map(d=>d.innerText)")
    assert '• 推定を前の自己ベストとの真ん中に寄せた（' in '\n'.join(notes), notes


def test_tech_drop_note(ui):
    # R5: 自分の結果が無い曲で tech が基準（クリアしてきた曲の tech の中央）より高い曲は、開いた欄に tech で下げた分を出す
    # （設定の値 × (tech − 中央)。印 tech の決まり文句は出さずに、数値の入った文にする）
    import re
    ui.serve.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50}, preset='p1')   # 未プレイの曲も出る条件（作り物のデータの初期の条件）
    r = ui.serve._api_recommend_rebuild({'preset': 'p1'})
    c = next(c for c in r['items'] if 'tech' in c['flags'])
    t50, u = r['range']['tech']['p50'], ui.serve.SETTINGS.values('p1')['unplayed_tech_drop']
    ui.open('recommend')
    ui.click_sel(f'.recRow[data-id="{c["leaderboard_id"]}"] .songName')
    notes = ui.js("[...document.querySelectorAll('.reasons .flag')].map(d=>d.innerText)")
    why = [x for x in notes if x.startswith('• tech ')]
    assert len(why) == 1 and not any('tech が高い（' in x for x in notes), notes
    m = re.match(r'• tech ([\d.]+) が、あなたがクリアしてきた曲の tech の中央（([\d.]+)）より高いので、([\d.]+)% 下げて推定しています', why[0])
    assert m, why[0]
    x, base, d = map(float, m.groups())
    assert abs(x - c['tech_rating']) < 0.006 and abs(base - t50) < 0.006 and abs(d - u * (c['tech_rating'] - t50)) < 0.006
    assert '下の • の分も直して' in ui.js("document.querySelector('.reasons').innerText")


def test_gain_spread_text_and_old_list(ui):
    # 増える総PP は推定の幅で見た期待値（recommend.py の SPREAD）。プレイ済みの曲は一覧の「総PP」の下に自己ベストを超える見込み、
    # 開いた欄に見込みと超えた時の平均。まとめは「見込み」。推定の幅より前の版で作ったリスト（曲に gain_up が無い）は前の文で説明する
    import json
    r = ui.serve._api_recommend_rebuild({'preset': 'p1'})
    c = next(c for c in r['items'] if c['p_up'] is not None)
    lid = c['leaderboard_id']
    ui.open('recommend')
    assert ui.js("document.querySelector('.sumMain .mute.small').innerText") == '増える総PP（有効PP）の見込み'
    assert '超える %.0f%%' % (100 * c['p_up']) in ui.js(f"document.querySelector('.recRow[data-id=\"{lid}\"]').innerText")
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    why = ui.js("document.querySelector('.reasons').innerText")
    assert '自己ベストを超える見込みは %.0f%%' % (100 * c['p_up']) in why and '平均すると +%.2f' % c['gain'] in why, why
    assert '平均 +%.2f 増える' % c['gain_up'] in why

    row = ui.serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")
    old = json.loads(row['payload'])
    for x in old['items'] + old['runnersUp']:
        x.pop('p_up', None)
        x.pop('gain_up', None)
    with ui.serve.STORE.tx():
        ui.serve.STORE.conn.execute("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(old, ensure_ascii=False),))
    ui.open('recommend')
    assert ui.js("document.querySelector('.sumMain .mute.small').innerText") == '全部取れた場合の総PP（有効PP）'
    assert '超える ' not in ui.js(f"document.querySelector('.recRow[data-id=\"{lid}\"]').innerText")
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    why = ui.js("document.querySelector('.reasons').innerText")
    assert '総PP（有効PP）では +%.2f 増える。' % c['gain'] in why and '見込み' not in why.split('総PP（有効PP）では')[0], why


def test_row_pp_and_gain_labels(ui):
    # 一覧の行: 曲の PP は「推定」「今」のラベル付き（どちらが予想か分かるように）。総PP の下は「超える」（自己ベストを超える見込み）と
    # 「クリア」（クリアできる見込み。95% 以上の曲には出さない）を両方出す。自己ベストが無い曲は「今」も「超える」も出さない
    import json
    r = ui.serve._api_recommend_rebuild({'preset': 'p1'})
    played = [c for c in r['items'] if c['p_up'] is not None and c['best_pp'] is not None]
    unplayed = next(c for c in r['items'] if c['best_pp'] is None)
    low, high = played[0], played[1]
    clears = {low['leaderboard_id']: 0.8, high['leaderboard_id']: 0.97, unplayed['leaderboard_id']: 0.6}
    saved = json.loads(ui.serve.STORE.q1("SELECT payload FROM rec_lists WHERE preset='p1'")['payload'])
    for x in saved['items']:
        if x['leaderboard_id'] in clears:
            x['clear'] = clears[x['leaderboard_id']]
    with ui.serve.STORE.tx():
        ui.serve.STORE.conn.execute("UPDATE rec_lists SET payload=? WHERE preset='p1'", (json.dumps(saved, ensure_ascii=False),))
    ui.open('recommend')
    text = lambda c: ui.js(f"document.querySelector('.recRow[data-id=\"{c['leaderboard_id']}\"]').innerText")
    pp = lambda v: ui.js(f"Number({v}).toLocaleString('ja-JP', {{minimumFractionDigits:1, maximumFractionDigits:1}})")
    t = text(low)
    assert '推定 ' + pp(low['est_pp']) in t and '今 ' + pp(low['best_pp']) in t, t
    assert '超える %.0f%%' % (100 * low['p_up']) in t and 'クリア 80%' in t and '見込み' not in t, t
    t = text(high)
    assert '超える %.0f%%' % (100 * high['p_up']) in t and 'クリア' not in t, t
    t = text(unplayed)
    assert '推定 ' + pp(unplayed['est_pp']) in t and 'クリア 60%' in t and '今 ' not in t and '超える' not in t, t


def test_pin_persists(ui):
    ui.open('recommend')
    first = ui.js(IDS)[0]
    ui.click_sel(f'.recRow[data-id="{first}"] button.icon', index=0)   # 📌
    ui.until(f"document.querySelector('.recRow[data-id=\"{first}\"]').classList.contains('pinned')")
    ui.open('recommend')   # 開き直しても固定のまま（DB に残る）
    assert ui.js(f"document.querySelector('.recRow[data-id=\"{first}\"]').classList.contains('pinned')")
    assert ui.serve.STORE.q1("SELECT mark FROM user_marks WHERE leaderboard_id=?", (first,))['mark'] == 'pin'


def test_keyboard_remove_and_restore(ui):
    ui.open('recommend')
    ids = ui.js(IDS)
    ui.js("document.querySelector('.recList').focus()")
    ui.key('ArrowDown')
    ui.key('ArrowDown')
    sel = ui.js("document.querySelector('.recRow.sel').dataset.id")
    assert sel == ids[1]
    ui.key('Delete')
    ui.until(f"!{IDS}.includes('{sel}')", what='外した曲が一覧から消える')
    assert len(ui.js(IDS)) == len(ids)   # 次点から補充される
    ui.click_sel('button', text='曲を戻す')
    ui.until(f"{IDS}.includes('{sel}')", what='戻した曲が一覧に戻る')


def test_swap_with_runner_up(ui):
    ui.open('recommend')
    target = ui.js(IDS)[3]
    ui.click_sel(f'.recRow[data-id="{target}"] .songName')
    runner = ui.js("document.querySelectorAll('.recList')[1].querySelector('.recRow:not(.head)').dataset.id")
    ui.click_sel('button', text='入れ替える', index=0)
    ui.until(f"{IDS}.includes('{runner}') && !{IDS}.includes('{target}')", what='次点と入れ替わる')


def test_never(ui):
    ui.open('recommend')
    first = ui.js(IDS)[0]
    ui.click_sel(f'.recRow[data-id="{first}"] button.icon', index=2)   # 🚫
    ui.until(f"!{IDS}.includes('{first}')")
    ui.open('recommend')
    assert first not in ui.js(IDS)


def test_condition_changes_list(ui):
    ui.open('recommend')
    ui.js("(()=>{const e=document.querySelector('.recCond input[type=number]'); e.value='5'; e.dispatchEvent(new Event('change'));})()")
    ui.until(f"{IDS}.length===5", what='曲数を5にすると5曲')
    assert ui.serve.SETTINGS.get('playlist_size') == 5


def test_export_writes_playlist(ui):
    ui.open('recommend')
    n = len(ui.js(IDS))
    path = os.path.join(ui.serve.SETTINGS.get('beat_saber_dir'), 'Playlists', 'blnext-next.bplist')
    if os.path.exists(path):
        os.remove(path)
    ui.click_sel('button.primary', text='Beat Saber に書き込む')
    ui.until("document.querySelector('#toast').classList.contains('ok')", what='書き込みの知らせ')
    d = json.loads(open(path, encoding='utf-8').read())
    assert d['playlistTitle'] == '次回おすすめ' and d['image'].startswith('data:image/png;base64,')
    assert 0 < len(d['songs']) <= n and all(len(s['hash']) == 40 for s in d['songs'])


def test_bsdir_dialog_opens_and_closes(ui):
    ui.open('recommend')
    ui.click_sel('button', text='変える')
    ui.until("!!document.querySelector('.modal')")
    ui.key('Escape')
    ui.until("!document.querySelector('.modal')", what='Esc で閉じる')


def test_settings_save_and_invalid(ui):
    ui.open('settings')
    ui.js("(()=>{const e=document.getElementById('set_playlist_size'); e.value='12'; e.dispatchEvent(new Event('change'));})()")
    ui.until("true")
    ui.wait(0.3)
    assert ui.serve.SETTINGS.get('playlist_size') == 12
    ui.js("(()=>{const e=document.getElementById('set_playlist_size'); e.value='0'; e.dispatchEvent(new Event('change'));})()")
    ui.until("document.getElementById('msg_playlist_size').classList.contains('show')", what='間違った値の知らせ')
    assert ui.js("document.getElementById('set_playlist_size').value") == '12'   # 元の値に戻る
    assert ui.serve.SETTINGS.get('playlist_size') == 12


def test_search_offline_message(ui):
    ui.open('settings')
    ui.js("(()=>{const q=document.querySelector('.playerBox input:not(#set_player_id)'); q.value='abc';})()")
    ui.click_sel('.playerBox button', text='探す')
    ui.until("document.querySelector('.results').innerText.includes('つながりません')", what='オフラインの知らせ')


def test_home_review_and_neighbors(ui):
    # 振り返りは一番新しい出力の分を出す（出力のテストが先に流れると、まだプレイしていない出力になる）＝表示の有無と、
    # 作り物のデータで最初の出力の1曲が「伸びた」と記録されていることを確かめる
    ui.open('home')
    assert ui.js("[...document.querySelectorAll('#main h2')].some(e=>e.textContent==='前回のプレイリストの結果')")
    assert ui.serve.STORE.q1("SELECT COUNT(*) AS n FROM reviews WHERE run_id=1 AND status='improved'")['n'] == 1
    assert ui.js("document.querySelectorAll('table.list tr.me').length") == 1
    assert ui.js("!!document.querySelector('svg.chart .bar')")


def test_home_changes_scroll(ui, monkeypatch):
    # 前回からの変化の3列は、たたまずに全員・全曲を枠に入れ、見出しに人数・曲数。長い列は枠の高さの上限（360px）で枠の中を
    # スクロールし、横に並んだ3列の枠は同じ高さ。狭い時（縦に並ぶ）は列ごとに中身の高さ（上限まで）
    from conftest import many_passed
    many_passed(ui.serve, monkeypatch)
    ui.open('home')
    col = "document.querySelector('.chgCol[data-key=%s]')"
    assert ui.js(f"{col % 'passedBy'}.querySelector('h3').textContent") == '抜かれた人 25人'
    assert ui.js(f"{col % 'passedBy'}.querySelectorAll('li').length") == 25
    assert ui.js(f"{col % 'passed'}.querySelector('h3').textContent") == '抜いた人 9人'
    assert ui.js(f"{col % 'passed'}.querySelectorAll('li').length") == 9
    # 伸ばした曲は、見出しに全部の総PP の増え、曲ごとに曲の PP と総PP の増え（作り物のデータは1曲伸ばしてある）
    home = ui.serve.views.home(ui.serve.STORE, ui.serve.SETTINGS)
    n = len(home['improved'])
    assert n and ui.js(f"{col % 'improved'}.querySelectorAll('li').length") == n
    up = lambda v: '±0' if abs(v) < 0.005 else '▲ {:,.2f}'.format(v)
    assert ui.js(f"{col % 'improved'}.querySelector('h3').textContent") == '自分が伸ばした曲 %d曲 総PP %s' % (n, up(home['improvedGain']))
    assert ui.js(f"[...{col % 'improved'}.querySelectorAll('.chgPp')].map(e=>e.textContent)") == [
        '曲のPP %s総PP %s' % (up(x['pp'] - x['old_pp']), up(x['gain'])) for x in home['improved']]
    assert ui.js("!!document.querySelector('.chgToggle')") is False
    boxes = "[...document.querySelectorAll('.chgBox')].map(b=>[b.clientHeight,b.scrollHeight])"
    hs = ui.js(boxes)
    assert len(hs) == 3 and len({h for h, _ in hs}) == 1
    assert hs[2][0] <= 360 < hs[2][1]   # 抜かれた人は枠の中でスクロールする
    assert ui.layout_issues() == []
    ui.resize(700)
    assert ui.js("getComputedStyle(document.querySelector('.chgGrid')).gridTemplateColumns.split(' ').length") == 1
    hs = ui.js(boxes)
    assert hs[1][0] == hs[1][1] < hs[2][0]   # 抜いた人（9人）は中身の高さ。抜かれた人は上限
    assert ui.layout_issues() == []


def test_language_switch(ui):
    # 最初の公開版は日本語だけで、設定の画面には出さない（仕組みは残す。設定ファイルの lang で切り替わる）
    ui.open('settings')
    assert not ui.js("!!document.getElementById('set_lang')")
    ui.serve.SETTINGS.update({'lang': 'en'})
    ui.open('settings')
    ui.until("document.querySelector('#nav button').textContent==='Home'", what='英語に切り替わる')


def test_refresh_progress_strip(ui):
    """「更新」で取得が始まり、進捗の帯が出て、終わったら描き直す（作り物の BeatLeader を少しずつ遅らせて返す）"""
    import time
    from core.fetch.http import BLClient
    from fakebl import FakeBL
    fake = FakeBL(n_players=60, self_rank=30, n_maps=120)

    def slow(req, timeout):
        time.sleep(0.05)
        return fake(req, timeout)
    old = ui.serve.REFRESHER.client_factory
    ui.serve.REFRESHER.client_factory = lambda: BLClient('test', opener=slow)
    try:
        ui.open('home')
        ui.click_sel('#refreshBtn')   # 上の帯の「更新」（どの画面にもある）
        ui.until("document.getElementById('strip').classList.contains('on')", what='進捗の帯が出る')
        ui.until("!document.getElementById('strip').classList.contains('on')", timeout=60, what='取得が終わる')
        ui.wait_ready()
        assert ui.js("document.body.dataset.screen") == 'home'
    finally:
        ui.serve.REFRESHER.client_factory = old


def test_home_review_first_clear(ui):
    # 振り返りの初クリア（出力の時に自己ベストが無かった曲）は「伸びた」と別の印・別の数で出し、推定との差も分けて出す。
    # 作り物のデータには初クリアが無いので、ホームに出る出力（スタンダードの一番新しい出力）の振り返りの記録を入れ替え、終わったら戻す
    from core import review
    st = ui.serve.STORE
    run = next(r['run_id'] for r in review._recent_runs(st, 1) if r['preset'] == 'p1')
    items = st.q('SELECT leaderboard_id, est_acc FROM recommendation_items WHERE run_id=? AND exported=1 ORDER BY leaderboard_id', (run,))
    saved = st.q('SELECT * FROM reviews WHERE run_id=?', (run,))
    rest = len(items) - 4   # 表は出力した曲の全部（振り返りの記録の無い曲も「まだ」）なので、残りは全部「まだ」にする
    try:
        with st.tx():
            st.conn.execute('DELETE FROM reviews WHERE run_id=?', (run,))
            for it, status in zip(items, ('firstClear', 'improved', 'firstClear', 'played') + ('notPlayed',) * rest):
                est = it['est_acc']
                st.conn.execute('INSERT INTO reviews(run_id, leaderboard_id, status, est_acc, best_before, best_after, attempt_max, checked_at) '
                                'VALUES(?,?,?,?,?,?,?,0)', (run, it['leaderboard_id'], status, est,
                                                            None if status == 'firstClear' else est - 0.02,
                                                            {'firstClear': est - 0.05, 'improved': est + 0.01}.get(status),
                                                            est - 0.03 if status == 'played' else None))
        ui.open('home')
        counts = ui.js("Object.fromEntries([...document.querySelectorAll('.revCount')].map(e=>[e.classList[1], e.querySelector('b').textContent]))")
        assert counts == {'improved': '1', 'firstClear': '2', 'played': '1', 'notPlayed': str(rest)}
        assert ui.js("[...document.querySelectorAll('.revTag.firstClear')].map(e=>e.textContent)") == ['初クリア', '初クリア']
        errs = ui.js("[...document.querySelectorAll('.revErr')].map(e=>e.textContent)")
        assert errs[0] == ('この回の推定との差（今回の精度 − 推定精度の平均）: 伸びた +1.00%（1曲）・初クリア -5.00%（2曲）・'
                           '伸びなかった -3.00%（1曲）'), errs
        assert errs[1].startswith('これまでの推定との差: ') and '初クリア ' in errs[1]
        assert ui.js("[...document.querySelectorAll('#main p')].some(e=>e.textContent.startsWith('初クリアは'))")
        # 数が4つ・推定との差が2行になっても、狭い幅・文字を最大にして崩れない
        ui.serve.SETTINGS.update({'ui_scale': 145})
        ui.resize(960, 900)
        ui.open('home')
        assert ui.layout_issues() == []
    finally:
        with st.tx():
            st.conn.execute('DELETE FROM reviews WHERE run_id=?', (run,))
            for r in saved:
                st.conn.execute('INSERT INTO reviews(%s) VALUES(%s)' % (', '.join(r), ', '.join('?' * len(r))), list(r.values()))


# ---- 難しさ（スライダー・難しさ違いのセット） ----
# 難しさの項目（unplayed_pass_cap・challenge_max）は ui の後始末で戻らないので、写したセットで試す（足したセットは消える）

ON = "(document.querySelector('%s .diffStop.on')||{}).innerText||null"


def diff_preset(ui):
    pid = ui.serve.SETTINGS.add_preset('難しさの確認', copy_from='p1')
    ui.serve.SETTINGS.update({'active_preset': pid})
    return pid


def test_difficulty_slider_on_recommend(ui):
    pid = diff_preset(ui)
    ui.open('recommend')
    # 作り物のデータは挑戦枠 50（個別に変えた＝カスタム）: どの点にも印が無い
    assert ui.js("document.querySelector('.recCond .condItem').dataset.key") == 'difficulty'   # 条件の一番上
    assert ui.js(ON % '.recCond') is None and ui.js("!!document.querySelector('.recCond .diffSlider.custom')")
    assert 'カスタム（個別に変えています）' in ui.js("document.querySelector('.recCond .diffNow').innerText")
    assert ui.js("[...document.querySelectorAll('.recCond .diffStop')].map(b=>b.innerText)") == ['着実', '標準', 'チャレンジング']
    ui.click_sel('.recCond .diffStop', text='着実')
    ui.until(f"{ON % '.recCond'}==='着実'", what='着実に印')
    v = ui.serve.SETTINGS.values(pid)
    assert v['unplayed_pass_cap'] == 'p75' and v['challenge_max'] == 0
    # リストを作り直した: 作り物の未プレイの曲はどれも pass が高いので入らない。条件の欄に pass の上限
    ui.until("![...document.querySelectorAll('.recList')[0].querySelectorAll('.badge')].some(b=>!b.classList.contains('played'))",
             what='未プレイの曲が外れる')
    hint = ui.js("document.querySelector('.condItem[data-key=difficulty] .condHint').innerText")
    assert hint.startswith('未プレイは pass 2.20 まで（あなたがクリアしてきた曲の75%点）'), hint
    assert '挑戦枠: 0 曲まで' in ui.js("document.querySelector('.recCond .diffNow').innerText")
    # キーでも動かせる（右へ＝難しく）
    ui.js("document.querySelector('.recCond .diffStop.on').focus()")
    ui.key('ArrowRight')
    ui.until(f"{ON % '.recCond'}==='標準'", what='右へ動かすと標準')
    assert ui.serve.SETTINGS.values(pid)['challenge_max'] == 1
    ui.until("document.activeElement && document.activeElement.classList.contains('diffStop')", what='スライダーに戻る')
    # 挑戦枠を個別に変えるとカスタム
    ui.js("(()=>{const e=document.getElementById('cond_challenge_max'); e.value='3'; e.dispatchEvent(new Event('change'));})()")
    ui.until("!!document.querySelector('.recCond .diffSlider.custom')", what='カスタムに戻る')
    assert ui.serve.SETTINGS.values('p1')['challenge_max'] == 50   # ほかのセットは変わらない


def test_difficulty_slider_on_settings(ui):
    pid = diff_preset(ui)
    ui.open('settings')
    assert ui.js("document.querySelector('#grp_list .row').dataset.key") == 'difficulty'   # リストの条件の先頭
    assert ui.js("document.querySelector('.row[data-key=difficulty] .def').innerText") == '初期値: 標準'
    ui.click_sel('.row[data-key=difficulty] .diffStop', text='チャレンジング')
    ui.until(f"{ON % '.row[data-key=difficulty]'}==='チャレンジング'", what='チャレンジングに印')
    # まとめて変えた項目の欄も今の値になる
    assert ui.js("document.getElementById('set_challenge_max').value") == '2'
    assert ui.js("document.getElementById('set_unplayed_pass_cap').value") == 'none'
    assert ui.serve.SETTINGS.values(pid)['challenge_max'] == 2
    # 個別に変えるとカスタム、段階の値に戻すとその段階
    ui.js("(()=>{const e=document.getElementById('set_unplayed_pass_cap'); e.value='p75'; e.dispatchEvent(new Event('change'));})()")
    ui.until("!!document.querySelector('.row[data-key=difficulty] .diffSlider.custom')", what='カスタム')
    ui.js("(()=>{const e=document.getElementById('set_challenge_max'); e.value='0'; e.dispatchEvent(new Event('change'));})()")
    ui.until("document.getElementById('set_challenge_max').value==='0'", what='挑戦枠を保存した')
    ui.wait(0.3)
    assert ui.js("!!document.querySelector('.row[data-key=difficulty] .diffSlider.custom')")   # クリアの見込みの下限がまだ違う
    ui.js("(()=>{const e=document.getElementById('set_min_clear'); e.value='80'; e.dispatchEvent(new Event('change'));})()")
    ui.until("document.getElementById('set_min_clear').value==='80'", what='クリアの見込みの下限を保存した')
    ui.wait(0.3)
    assert ui.js("!!document.querySelector('.row[data-key=difficulty] .diffSlider.custom')")   # 精度の上限の余裕がまだ違う（チャレンジングの 1.0）
    assert ui.js("document.getElementById('set_acc_cap_margin').value") == '1'
    ui.js("(()=>{const e=document.getElementById('set_acc_cap_margin'); e.value='0'; e.dispatchEvent(new Event('change'));})()")
    ui.until(f"{ON % '.row[data-key=difficulty]'}==='着実'", what='着実の値になった')


def test_difficulty_set_dialog(ui, monkeypatch):
    ui.open('recommend')
    ui.click_sel('.diffSetBtn')
    ui.until("!!document.querySelector('.modal .dsLevel')")
    checked = "Object.fromEntries([...document.querySelectorAll('.modal .dsLevel')].map(l=>[l.dataset.level, [l.querySelector('input').checked, l.querySelector('input').disabled]]))"
    # 初期値は着実とチャレンジングの2つ・7曲
    assert ui.js(checked) == {'steady': [True, False], 'standard': [False, False], 'challenge': [True, False]}
    assert ui.js("document.getElementById('dsSize').value") == '7'
    # 曲数は選んだ値になる（作り物のデータでは、着実に入れられるプレイ済みの曲がチャレンジングと分け合うと7曲に足りないので5曲で）
    ui.js("document.getElementById('dsSize').value='5'")
    ui.click_sel('.modal button.primary', text='作る')
    ui.until("!document.querySelector('.modal') && document.querySelector('.presetTab.on')?.innerText==='着実'", what='着実のセットを開く')
    tabs = ui.js("[...document.querySelectorAll('.presetTabs .presetTab:not(.add)')].map(b=>b.innerText)")
    assert tabs == ['スタンダード', '着実', 'チャレンジング']
    assert len(ui.js(IDS)) == 5 and '難しさ違いのセットを 2 つ作りました' in ui.js("document.querySelector('#toast').innerText")
    assert ui.js(ON % '.recCond') == '着実' and ui.js("document.getElementById('cond_exclude_other_sets').checked")
    steady = set(ui.js(IDS))
    ui.click_sel('.presetTab', text='チャレンジング')
    ui.until("document.querySelector('.presetTab.on')?.innerText==='チャレンジング'")
    assert ui.js(ON % '.recCond') == 'チャレンジング' and not steady & set(ui.js(IDS))   # 同じ曲は入らない
    assert 'ほかのセットのリストにある 5 曲を外した' in ui.js("document.querySelector('.condItem[data-key=exclude_other_sets]').innerText")
    # もう一度開くと、作った段階は選べない。何も選ばずに作ると知らせる。Esc で閉じる
    ui.click_sel('.diffSetBtn')
    ui.until("!!document.querySelector('.modal .dsLevel')")
    assert ui.js(checked) == {'steady': [False, True], 'standard': [False, False], 'challenge': [False, True]}
    ui.click_sel('.modal button.primary', text='作る')
    ui.until("document.querySelector('.modal .err').textContent.includes('1つ以上')", what='段階を選ぶ知らせ')
    ui.key('Escape')
    ui.until("!document.querySelector('.modal')", what='Esc で閉じる')
    # 標準も作ると、3つとも揃うのでボタンは出なくなる
    ui.serve._api_presets_difficulty_set({'levels': ['standard'], 'size': 5})
    ui.open('recommend')
    assert ui.js("!document.querySelector('.diffSetBtn')")


def test_star_range_condition(ui, monkeypatch):
    # 条件の欄の★の範囲（下限〜上限・0で無効）: 入れるとそのセットに保存して作り直し、範囲の外の曲はプレイ済みも未プレイも入らない。
    # 下に、自動で決めた下限（下限が 0 の時。assemble.auto_star_min）・範囲の外で外した曲の数・値を選ぶ参考（このセットのモードで最近
    # クリアした曲の★）。下限を上限より上にすると断って元の値に戻す。上限は 0 にすると効かず、下限は 0 にすると自動に戻る
    from core.estimate import assemble as A
    # 作り物のデータの自己ベストは古い（取得のテストが流れた後は全部 2023 年）ので、参考の「最近」を広げて全部の自己ベストを数える
    monkeypatch.setattr(A, 'RECENT_DAYS', 365 * 100)
    ui.serve.SETTINGS.update({'max_map_age_years': 0, 'challenge_max': 50}, preset='p1')   # 未プレイの曲も出る条件
    ui.serve._api_recommend_rebuild({'preset': 'p1'})
    ui.open('recommend')
    hint = "[...document.querySelectorAll('.condItem[data-key=star_min] .condHint')].map(e=>e.innerText).join('|')"
    rs = ui.serve._api_recommend({'preset': 'p1'})['filters']['recentStars']
    assert rs['n'] >= 5 and rs['p95'] is not None
    two = lambda x: ui.js("(%r).toLocaleString('ja-JP', {minimumFractionDigits: 2, maximumFractionDigits: 2})" % x)   # 画面と同じ丸め
    got = ui.js(hint)
    assert got.endswith('参考: このセットのモードで最近90日にクリアした曲は最高 ★%s・95%%点 ★%s（%d曲。NF・SS を除く）' % (
        two(rs['max']), two(rs['p95']), rs['n']))
    auto = ui.serve._api_recommend({'preset': 'p1'})['filters']['starMinAuto']
    assert set(auto) == {'Standard'} and got.startswith('下限は自動で ★%s（更新すると総PP が 0.5PP 以上増える曲が半分を超える★' % (
        ui.js("(%r).toLocaleString('ja-JP', {minimumFractionDigits: 1, maximumFractionDigits: 1})" % auto['Standard'])))
    assert ui.js("document.getElementById('cond_star_min').value") == '0' and ui.js("document.getElementById('cond_star_max').value") == '0'

    def put(key, value):
        ui.js("(()=>{const e=document.getElementById('cond_%s'); e.value='%s'; e.dispatchEvent(new Event('change')); return true})()"
              % (key, value))

    put('star_max', '6')
    ui.until(f"{hint}.includes('範囲の外で外した曲: ')", what='上限で外した曲の数')
    assert ui.serve.SETTINGS.values('p1')['star_max'] == 6
    before = ui.js(hint)
    put('star_min', '4')
    ui.until(f"{hint}!=={json.dumps(before)}", what='下限でも外した曲の数')
    assert '下限は自動' not in ui.js(hint)                              # 数字で決めた下限には自動の説明を出さない
    v = ui.serve.SETTINGS.values('p1')
    assert v['star_min'] == 4 and v['star_max'] == 6
    items = ui.serve._api_recommend({'preset': 'p1'})['items']
    assert all(4 <= c['stars'] <= 6 for c in items) and {c['kind'] for c in items} == {'played', 'unplayed'}   # どちらにも効く
    assert ui.js(IDS) == [c['leaderboard_id'] for c in items]
    # 下限を上限より上にすると断る（知らせて、入れた値を元に戻す）
    put('star_min', '7')
    ui.until("document.querySelector('#toast').innerText.includes('その値は使えません')", what='断った知らせ')
    assert ui.js("document.getElementById('cond_star_min').value") == '4' and ui.serve.SETTINGS.values('p1')['star_min'] == 4
    # 上限は 0 にすると効かず、下限は 0 にすると自動に戻る（作り物のデータの★は 3.0 から）
    before = ui.js(hint)
    put('star_max', '0')
    ui.until(f"{hint}!=={json.dumps(before)}", what='上限をなくす')
    put('star_min', '0')
    ui.until(f"!{hint}.includes('範囲の外')", what='範囲の外で外した曲が無くなる')
    v = ui.serve.SETTINGS.values('p1')
    assert v['star_min'] == 0 and v['star_max'] == 0 and ui.js(hint).startswith('下限は自動で ★')


def test_few_similar_notes(ui, monkeypatch):
    # 似ている人が足りない人: まとめの欄に「目安です」、★の一番上より上で外した数、近い★の曲が足りない時の上限の説明
    from collections import OrderedDict
    view = ui.serve.SETTINGS.view('p1')
    model, est = ui.serve.ENGINE.get(view)
    c0 = next(c for c in est if c['kind'] == 'played' and c['p_up'] and c['gain'] > 0)
    lid = c0['leaderboard_id']
    top = max(c0['best_acc'], c0['est_acc']) + 0.002 - view.values()['acc_cap_margin'] / 100

    class Ceiling:
        def __call__(self, stars, mode, exclude=None):
            return None, None, 2, None

        def overall(self, mode, exclude=None):
            return (top, 'LB005') if exclude == lid else (None, None)
    monkeypatch.setattr(model, 'ceiling', Ceiling())
    monkeypatch.setattr(model, 'few_similar', True)
    monkeypatch.setattr(model, 'clear', None)
    monkeypatch.setattr(ui.serve.ENGINE, '_ests', OrderedDict())   # 推定を作り直させる（終わったら元の推定に戻る）
    ui.serve._api_recommend_action({'action': 'pin', 'id': lid, 'preset': 'p1'})
    r = ui.serve._api_recommend({'preset': 'p1'})
    assert r['fewSimilar'] is True and r['noClear'] is True
    assert r['filters']['starTop'] == model.me.curve.hi
    ui.open('recommend')
    assert '比べられる人が少なく、クリアの見込みもまだ無いので' in ui.js("document.querySelector('.recSum .recRough').innerText")
    ui.click_sel(f'.recRow[data-id="{lid}"] .songName')
    notes = ui.js("[...document.querySelectorAll('.reasons .flag')].map(d=>d.innerText)")
    assert any(x.startswith('• 近い★の曲がまだ少ないので、あなたのほかの曲の一番 ') for x in notes), notes
    assert ui.layout_issues() == []


def test_rough_note_only_no_clear(ui, monkeypatch):
    # 似ている人がいて、クリアの見込みだけ無い人（統計が非公開の人など）: 全部クリアできた場合の数字だと分かるように
    from collections import OrderedDict
    view = ui.serve.SETTINGS.view('p1')
    model, _ = ui.serve.ENGINE.get(view)
    monkeypatch.setattr(model, 'few_similar', False)
    monkeypatch.setattr(model, 'clear', None)
    monkeypatch.setattr(ui.serve.ENGINE, '_ests', OrderedDict())
    ui.serve._api_recommend_rebuild({'preset': 'p1'})
    ui.open('recommend')
    assert ui.js("document.querySelector('.recSum .recRough').innerText") == 'クリアの見込みがまだ無いので、全部クリアできた場合の数字です。'


def test_cond_single_scroll(ui):
    # プレイリストの画面の右の条件の欄は、欄の中ではスクロールしない（全体のスクロール1つ。2026-11-09 の利用者の要望。文字を大きくすると
    # 欄の中と全体の二重のスクロールになっていた）。欄の下の端が見えるまでは一緒にスクロールし、その先は下の端を画面の下にそろえて貼り付く
    ui.serve.SETTINGS.update({'ui_scale': 145})
    ui.resize(1920, 1000)
    ui.open('recommend')
    m = "(() => { const m = document.getElementById('main'), c = document.querySelector('.recCond'), z = parseFloat(document.documentElement.style.zoom || 1)," \
        " mr = m.getBoundingClientRect(), cr = c.getBoundingClientRect(); return {inner: c.scrollHeight - c.clientHeight, pos: getComputedStyle(c).position," \
        " right: cr.left > mr.left + 300, high: c.offsetHeight > m.clientHeight, top: (cr.top - mr.top) / z, gap: (mr.bottom - cr.bottom) / z}; })()"
    r = ui.js(m)
    assert r['pos'] == 'sticky' and r['right'] and r['high'] and r['inner'] == 0   # 右に置いた欄がウィンドウより高くても、中ではスクロールしない
    assert r['top'] > 0                                                              # 一番上では欄の一番上が見える
    ui.js("document.getElementById('main').scrollTop = 1e6")
    ui.wait(0.2)
    assert ui.js(m)['gap'] > 0                                                       # 一番下では欄の一番下（外した曲を戻す）が見える
    ui.js("document.getElementById('main').scrollTop = document.querySelector('.recCond').offsetHeight")
    ui.wait(0.2)
    assert abs(ui.js(m)['gap'] - 12) < 2                                             # 途中では下の端を画面の下にそろえて貼り付く
    assert ui.layout_issues() == []


def test_loading_while_screen_renders(ui, monkeypatch):
    # 画面を描き終わるまでに時間がかかる時は「読み込んでいます…」を出し、描き終わったら消す（2026-11-09 の利用者の要望。プレイリストの
    # 画面は推定の計算で黒いまま待たされた）。速い画面では出さない（main.js の LOADING_DELAY）
    import time as _time
    real = ui.serve.API['recommend']

    def slow(body):
        _time.sleep(1.0)
        return real(body)
    monkeypatch.setitem(ui.serve.API, 'recommend', slow)
    ui.open('home')
    assert not ui.js("!!document.querySelector('.screenLoading')")
    ui.click_sel('#nav button', text='プレイリスト', settle=0)
    ui.until("!!document.querySelector('.screenLoading')", what='読み込んでいます')
    text = ui.js("document.querySelector('.screenLoading').innerText")
    assert '読み込んでいます' in text and '推定の計算' in text
    ui.until("!document.querySelector('.screenLoading') && !!document.querySelector('.recList')", timeout=10, what='描き終わる')
    ui.click_sel('#nav button', text='ホーム')
    ui.until("document.body.dataset.screen==='home' && !document.body.dataset.busy", what='ホーム')
    assert not ui.js("!!document.querySelector('.screenLoading')")
