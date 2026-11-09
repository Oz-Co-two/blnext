# セット（設定のパターン）と出力の3通り・プレイリストの画像・上の帯の「データの時刻」と「更新」の画面の操作
import http.client, json, os, struct, zlib

IDS = "[...document.querySelectorAll('.recList')[0].querySelectorAll('.recRow:not(.head)')].map(r=>r.dataset.id)"
TABS = "[...document.querySelectorAll('.presetTabs .presetTab:not(.add)')].map(b=>b.innerText)"
ACTIVE = "document.querySelector('.presetTab.on')?.innerText"
TOAST = "document.querySelector('#toast').innerText"


def png(w, h):
    """w×h の PNG（赤一色）"""
    raw = b''.join(b'\x00' + b'\xff\x00\x00' * w for _ in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def test_add_preset_and_switch(ui):
    ui.open('recommend')
    assert ui.js(TABS) == ['スタンダード'] and ui.js(ACTIVE) == 'スタンダード'
    ui.click_sel('.presetTab.add')
    ui.until("!!document.querySelector('.modal input[type=text]')")
    ui.js("(()=>{const e=document.querySelector('.modal input[type=text]'); e.value='ワンセイバー';})()")
    ui.click_sel('.modal button.primary', text='作る')
    ui.until(f"{TABS}.length===2 && {ACTIVE}==='ワンセイバー'", what='足したセットのタブが開く')
    pid = ui.serve.SETTINGS.active_preset()
    assert pid != 'p1'
    # 条件は開いているセットだけに効く
    ui.js("(()=>{const e=document.querySelector('.recCond input[type=number]'); e.value='4'; e.dispatchEvent(new Event('change'));})()")
    ui.until(f"{IDS}.length===4", what='このセットだけ4曲')
    assert '次回おすすめ（ワンセイバー）' in ui.js("document.querySelector('.plTitle').innerText")
    ui.click_sel('.presetTab', text='スタンダード')
    ui.until(f"{ACTIVE}==='スタンダード' && {IDS}.length===15", what='元のセットは15曲のまま')
    assert ui.serve.SETTINGS.values(pid)['playlist_size'] == 4 and ui.serve.SETTINGS.values('p1')['playlist_size'] == 15
    ui.open('recommend')   # 開き直しても最後に開いたセット
    assert ui.js(ACTIVE) == 'スタンダード'


def test_add_preset_dialog_errors_and_escape(ui):
    ui.open('recommend')
    ui.click_sel('.presetTab.add')
    ui.until("!!document.querySelector('.modal')")
    ui.click_sel('.modal button.primary', text='作る')   # 名前が空
    ui.until("document.querySelector('.modal .err').textContent.includes('1〜30文字')", what='名前の知らせ')
    ui.key('Escape')
    ui.until("!document.querySelector('.modal')", what='Esc で閉じる')
    assert ui.serve.SETTINGS.preset_ids() == ['p1']


def test_pin_is_per_preset(ui):
    ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    ui.serve.SETTINGS.update({'active_preset': 'p1'})
    ui.open('recommend')
    runner = ui.js("document.querySelectorAll('.recList')[1].querySelector('.recRow:not(.head)').dataset.id")
    ui.click_sel('button', text='入れ替える', index=0)
    ui.until(f"{IDS}.includes('{runner}')")
    ui.click_sel(f'.recRow[data-id="{runner}"] button.icon', index=0)   # 📌
    ui.until(f"document.querySelector('.recRow[data-id=\"{runner}\"]').classList.contains('pinned')")
    ui.click_sel('.presetTab', text='B')
    ui.until(f"{ACTIVE}==='B'")
    assert runner not in ui.js(IDS)   # 別のセットには入らない


def test_settings_common_and_preset_parts(ui):
    pid = ui.serve.SETTINGS.add_preset('ワンセイバー', copy_from='p1')
    ui.serve.SETTINGS.update({'active_preset': pid})
    ui.open('settings')
    heads = ui.js("[...document.querySelectorAll('.partHead h2')].map(e=>e.innerText)")
    assert heads == ['全部のセットで共通', '「ワンセイバー」のセット']
    # 2つの部分は地の色で区別する
    bg = ui.js("[...document.querySelectorAll('.setPart')].map(e=>getComputedStyle(e).backgroundColor)")
    assert len(set(bg)) == 2 and ui.js("getComputedStyle(document.body).backgroundColor") not in bg
    # どの項目にも「何が変わるか」の説明がある（数値の項目は、大きく・小さくした時）
    rows = ui.js("[...document.querySelectorAll('.row[data-key]')].map(r=>[r.dataset.key, (r.querySelector('.desc')||{}).innerText||''])")
    assert len(rows) > 30 and all(d for _, d in rows), [k for k, d in rows if not d]
    nums = ui.js("[...document.querySelectorAll('.row[data-key]')].filter(r=>r.querySelector('input[type=number]')).map(r=>[r.dataset.key, r.querySelector('.desc').innerText])")
    assert all(('大きく' in d or '小さく' in d) for k, d in nums if k not in ('target_country_rank', 'neighbor_display')), nums
    # セットの項目はセットの部分、共通の項目は共通の部分にある
    assert ui.js("!!document.querySelector('#setPreset .row[data-key=playlist_size]') && !!document.querySelector('#setCommon .row[data-key=player_id]')")
    assert ui.js("!document.querySelector('#setCommon .row[data-key=playlist_size]')")
    # セットごとに違う初期値（ファイル名）
    assert f'blnext-{pid}.bplist' in ui.js("document.querySelector('.row[data-key=output_filename] .def').innerText")
    # 名前を変える
    ui.js("(()=>{const e=document.getElementById('presetName'); e.value='片手'; e.dispatchEvent(new Event('change'));})()")
    ui.until("[...document.querySelectorAll('.presetTab')].some(b=>b.innerText==='片手')", what='タブの名前が変わる')
    assert ui.serve.SETTINGS.presets(brief=True)[1]['name'] == '片手'


def test_settings_duplicate_filename_message(ui):
    pid = ui.serve.SETTINGS.add_preset('B')
    ui.serve.SETTINGS.update({'active_preset': pid})
    ui.open('settings')
    ui.js("(()=>{const e=document.getElementById('set_output_filename'); e.value='blnext-next.bplist'; e.dispatchEvent(new Event('change'));})()")
    ui.until("document.getElementById('msg_output_filename').innerText.includes('ほかのセットと同じ')", what='重なりの知らせ')
    assert ui.js("document.getElementById('set_output_filename').value") == f'blnext-{pid}.bplist'   # 元に戻る


def test_settings_copy_and_delete_preset(ui):
    ui.open('settings')
    assert ui.js("document.querySelector('.presetActs .danger').disabled")   # 最後の1つは消せない
    ui.click_sel('.presetActs button', text='このセットを複製')
    ui.until(f"{TABS}.length===2 && {ACTIVE}==='スタンダードのコピー'", what='複製したセットが開く')
    ui.click_sel('.presetActs .danger')
    assert ui.js("document.querySelector('.presetActs .danger').innerText").startswith('もう一度')   # 1回では消えない
    assert len(ui.serve.SETTINGS.preset_ids()) == 2
    ui.click_sel('.presetActs .danger')
    ui.until(f"{TABS}.length===1", what='2回押すと消える')
    assert ui.serve.SETTINGS.preset_ids() == ['p1']


def test_cover_upload(ui, tmp_path):
    # 選んだ画像は画面で正方形に切り抜いて 512 までに縮め、中身だけを送る
    f = tmp_path / 'wide.png'
    f.write_bytes(png(900, 600))
    # ファイルを読み込むのは画像を選ぶ小さな画面（web/js/cover.js。tests/e2e/test_cover_ui.py）
    ui.open('settings')
    ui.click_sel('.coverCtl .coverPick')
    ui.until("document.querySelectorAll('.coverDlg .coverTile').length===6", what='画像を選ぶ小さな画面')
    obj = ui.call('Runtime.evaluate', expression="document.querySelector('.coverDlg input[type=file]')")['result']['objectId']
    ui.call('DOM.setFileInputFiles', files=[str(f)], objectId=obj)
    ui.until("document.querySelector('.coverDlg .coverTile.on').dataset.cover==='custom'", what='読み込んだ画像に印')
    path = ui.serve.SETTINGS.cover_path('p1')
    data = open(path, 'rb').read()
    assert path.endswith('p1.png') and data[:8] == b'\x89PNG\r\n\x1a\n'
    w, h = struct.unpack('>II', data[16:24])
    assert (w, h) == (512, 512)
    assert ui.js("document.querySelector('.coverThumb').src").startswith('data:image/png;base64,')
    ui.open('recommend')
    assert ui.js("document.querySelector('.recSum .coverThumb').src").startswith('data:image/png;base64,')
    # 画像でないファイル
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not an image')
    ui.open('settings')
    ui.click_sel('.coverCtl .coverPick')
    ui.until("document.querySelectorAll('.coverDlg .coverTile').length===7", what='画像を選ぶ小さな画面（読み込んだ画像のタイルも）')
    obj = ui.call('Runtime.evaluate', expression="document.querySelector('.coverDlg input[type=file]')")['result']['objectId']
    ui.call('DOM.setFileInputFiles', files=[str(bad)], objectId=obj)
    ui.until("document.querySelector('.coverDlg .coverMsg').innerText.includes('画像を読めませんでした')", what='読めない画像の知らせ')
    ui.click_sel('.coverDlg .coverTile[data-cover="app"]')   # アプリのアイコンに戻す（緑のタイル）
    ui.until("document.querySelector('.coverDlg .coverTile.on').dataset.cover==='app'", what='アプリのアイコンに印')
    assert ui.serve.SETTINGS.cover_path('p1') is None


def test_oneclick_button(ui, monkeypatch):
    launched = []
    monkeypatch.setattr(ui.serve.oneclick, 'handler_app', lambda: 'BSManager')
    monkeypatch.setattr(ui.serve.oneclick, 'launch', launched.append)
    ui.open('recommend')
    ui.until("[...document.querySelectorAll('.outBox button')].some(b=>b.innerText==='BSManager で入れる' && !b.disabled)", what='ボタンの名前')
    ui.click_sel('.outBox button', text='BSManager で入れる')
    ui.until(TOAST + ".includes('BSManager を起動しました')", what='起動した知らせ')
    assert launched[0].startswith('bsplaylist://playlist/http://127.0.0.1:%d/oneclick/' % ui.port)


def test_oneclick_all_button(ui, monkeypatch):
    # セットが2つ以上で、ワンクリックを受けるアプリがある時だけ出る
    monkeypatch.setattr(ui.serve.oneclick, 'handler_app', lambda: 'BSManager')
    ui.open('recommend')
    assert not ui.js("[...document.querySelectorAll('.presetExtra button')].some(b=>b.innerText.includes('BSManager で入れる'))")
    ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    got = []

    def app_reads(url):   # BSManager のふり: リンクを受けるとその場で読みに来る
        c = http.client.HTTPConnection('127.0.0.1', ui.port, timeout=10)
        c.request('GET', '/' + url.split('/', 6)[6])
        got.append(json.loads(c.getresponse().read())['playlistTitle'])
    monkeypatch.setattr(ui.serve.oneclick, 'launch', app_reads)
    ui.open('recommend')
    ui.click_sel('.presetExtra button', text='全部のセットを BSManager で入れる')
    ui.until(TOAST + ".includes('2個のセットのプレイリストを BSManager に渡しました')", what='渡した知らせ')
    assert got == ['次回おすすめ', '次回おすすめ（B）']
    monkeypatch.setattr(ui.serve.oneclick, 'handler_app', lambda: None)   # 受けるアプリが無ければ出さない
    ui.open('recommend')
    assert not ui.js("[...document.querySelectorAll('.presetExtra button')].some(b=>b.innerText.includes('で入れる'))")


def test_oneclick_without_app(ui, monkeypatch):
    monkeypatch.setattr(ui.serve.oneclick, 'handler_app', lambda: None)
    ui.open('recommend')
    ui.until("[...document.querySelectorAll('.outBox')].some(b=>b.innerText.includes('ワンクリックを有効にすると'))", what='案内')
    assert ui.js("[...document.querySelectorAll('.outBox button')].find(b=>b.innerText==='ワンクリックで入れる').disabled")


def test_save_file_with_native_dialog(ui):
    # exe 版（pywebview）の「名前を付けて保存」のふり。渡すのはセットの id だけ
    ui.open('recommend')
    ui.js("window.__saved=[]; window.pywebview={api:{save_playlist:p=>{window.__saved.push(p); return Promise.resolve({ok:true, path:'C:\\\\x\\\\a.bplist'});}}}; true")
    ui.click_sel('.outBox button', text='ファイルに保存')
    ui.until(TOAST + ".includes('に保存しました')", what='保存した知らせ')
    assert ui.js("window.__saved") == ['p1']
    ui.js("window.pywebview.api.save_playlist=()=>Promise.resolve({ok:false, error:'cancelled'}); true")
    ui.click_sel('.outBox button', text='ファイルに保存')   # やめた時は何も言わない
    ui.js("delete window.pywebview; true")


def test_save_file_in_browser(ui):
    # ブラウザで開いている時（開発中）は、サーバーから中身をもらってダウンロードさせる。出力として記録する
    ui.call('Browser.setDownloadBehavior', behavior='deny')
    n = ui.serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n']
    ui.open('recommend')
    ui.click_sel('.outBox button', text='ファイルに保存')
    ui.until(TOAST + ".includes('ダウンロードしました')", what='ダウンロードの知らせ', timeout=15)
    assert ui.serve.STORE.q1("SELECT method FROM exports ORDER BY rowid DESC LIMIT 1")['method'] == 'file'
    assert ui.serve.STORE.q1('SELECT COUNT(*) AS n FROM exports')['n'] == n + 1


def test_export_all_presets(ui):
    pid = ui.serve.SETTINGS.add_preset('B', copy_from='p1')
    ui.open('recommend')
    bs = ui.serve.SETTINGS.get('beat_saber_dir')
    for f in ('blnext-next.bplist', f'blnext-{pid}.bplist'):
        p = os.path.join(bs, 'Playlists', f)
        if os.path.exists(p):
            os.remove(p)
    ui.click_sel('.presetExtra button', text='全部のセットを書き込む')
    ui.until(TOAST + ".includes('2個のセット')", what='全部書き込んだ知らせ')
    with open(os.path.join(bs, 'Playlists', f'blnext-{pid}.bplist'), encoding='utf-8') as f:
        assert json.load(f)['playlistTitle'] == '次回おすすめ（B）'
    assert os.path.exists(os.path.join(bs, 'Playlists', 'blnext-next.bplist'))


def test_top_bar_data_time_and_refresh_button(ui):
    ui.open('home')
    txt = ui.js("document.getElementById('dataAt').innerText")
    assert txt.startswith('データ ') and ('前' in txt or 'たった今' in txt)
    assert '12 時間以上' in ui.js("document.getElementById('dataAt').title")
    assert ui.js("!document.getElementById('refreshBtn').hidden")
    assert not ui.js("[...document.querySelectorAll('#main .pageHead button')].some(b=>b.innerText==='更新')")   # ホームの更新ボタンは上の帯へ移した
