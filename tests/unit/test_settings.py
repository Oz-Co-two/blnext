import json
import pytest
from core.settings import REMOVED_KEYS, Settings, SettingsError, SPEC, check


def test_missing_file_gives_defaults(tmp_path):
    s = Settings(str(tmp_path))
    v = s.values()
    assert v == {k: (list(x['default']) if isinstance(x['default'], list) else x['default']) for k, x in SPEC.items()}


def raw_of(tmp_path):
    return json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))


def test_hidden_items_keep_their_values(tmp_path):
    # 画面に出さなくした項目（playerdata_path・fetch_song_details）は仕組みごと残す。前の版で変えた値は読めて、書く時にも消えない
    pd = 'C:\\BS\\PlayerData.dat'
    (tmp_path / 'settings.json').write_text(json.dumps({'playerdata_path': pd, 'fetch_song_details': False}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert s.get('playerdata_path') == pd and s.get('fetch_song_details') is False
    s.update({'country': 'us'})
    raw = raw_of(tmp_path)
    assert raw['playerdata_path'] == pd and raw['fetch_song_details'] is False
    assert not {'playerdata_path', 'fetch_song_details'} & set(REMOVED_KEYS)


def test_only_changed_keys_are_written(tmp_path):
    s = Settings(str(tmp_path))
    s.update({'playlist_size': 12})   # セットの項目は、開いているセット（最初は p1）に入る
    assert raw_of(tmp_path) == {'presets': [{'id': 'p1', 'name': 'スタンダード', 'values': {'playlist_size': 12}}]}
    assert s.get('playlist_size') == 12
    s.update({'country': 'us'})       # 共通の項目は直下
    assert raw_of(tmp_path)['country'] == 'us'


def test_broken_value_falls_back_to_default(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'playlist_size': 'abc', 'country': 'JPN', 'top_similar': 5}), encoding='utf-8')
    v = Settings(str(tmp_path)).values()
    assert v['playlist_size'] == 15 and v['country'] == 'jp' and v['top_similar'] == 5


def test_broken_file_gives_defaults(tmp_path):
    (tmp_path / 'settings.json').write_text('{oops', encoding='utf-8')
    assert Settings(str(tmp_path)).get('playlist_size') == 15


def test_unknown_keys_are_kept(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'future_key': 1}), encoding='utf-8')
    s = Settings(str(tmp_path))
    s.update({'playlist_size': 10})
    raw = raw_of(tmp_path)
    assert raw['future_key'] == 1 and raw['presets'][0]['values'] == {'playlist_size': 10}


# ---- セット（設定のパターン） ----

def test_old_flat_file_becomes_first_preset(tmp_path):
    # セットを作る前の形（項目が直下）は最初のセットとして読み、次に書く時に presets の中へ移す
    (tmp_path / 'settings.json').write_text(json.dumps({'player_id': '7', 'playlist_size': 9, 'modes': ['OneSaber']}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert s.get('playlist_size') == 9 and s.get('modes') == ['OneSaber'] and s.preset_ids() == ['p1']
    s.update({'one_diff_per_song': False})
    raw = raw_of(tmp_path)
    assert raw['player_id'] == '7' and 'playlist_size' not in raw and 'modes' not in raw
    assert raw['presets'][0]['values'] == {'playlist_size': 9, 'modes': ['OneSaber'], 'one_diff_per_song': False}


def test_removed_key_order_is_ignored(tmp_path):
    # なくした項目（order。2026-10-18 に基本の並びにした）が前の版の設定ファイルに残っていても、エラーにせず無視し、書く時に消す
    (tmp_path / 'settings.json').write_text(json.dumps({'player_id': '7', 'order': 'score', 'presets': [
        {'id': 'p1', 'name': 'スタンダード', 'values': {'playlist_size': 9, 'order': 'score'}},
        {'id': 'p2', 'name': 'B', 'values': {'order': 'stars', 'modes': ['OneSaber']}}]}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert 'order' not in s.values() and 'order' not in s.values('p2') and 'order' not in s.view('p2').values()
    assert s.get('playlist_size') == 9 and s.values('p2')['modes'] == ['OneSaber']
    assert all('order' not in p['values'] for p in s.presets())
    p3 = s.add_preset('C', copy_from='p2')                                    # 写しにも入らない
    assert s.values(p3)['modes'] == ['OneSaber']
    raw = raw_of(tmp_path)
    assert 'order' not in raw and all('order' not in p['values'] for p in raw['presets'])
    s.reset(['playlist_size'])
    assert s.get('playlist_size') == 15
    with pytest.raises(SettingsError) as e:                                   # 画面から送っても受けない
        s.update({'order': 'score'})
    assert e.value.code == 'unknown-key'


def test_removed_key_order_in_old_flat_file(tmp_path):
    # セットを作る前の形（項目が直下）に残っていた order も、無視して書く時に消す（知らないキーとしては残さない）
    (tmp_path / 'settings.json').write_text(json.dumps({'playlist_size': 9, 'order': 'score', 'future_key': 1}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert s.get('playlist_size') == 9 and 'order' not in s.values()
    s.update({'country': 'us'})
    raw = raw_of(tmp_path)
    assert 'order' not in raw and raw['future_key'] == 1 and raw['presets'][0]['values'] == {'playlist_size': 9}


def test_removed_key_unplayed_star_margin(tmp_path):
    # なくした項目（unplayed_star_margin。2026-10-25 に★の範囲 star_min・star_max にした）が設定ファイルに残っていても、
    # 無視して書く時に消す（セットの中・直下のどちらでも）。★の範囲は初期値（0＝効かない）のまま
    (tmp_path / 'settings.json').write_text(json.dumps({'player_id': '7', 'unplayed_star_margin': 2, 'presets': [
        {'id': 'p1', 'name': 'スタンダード', 'values': {'playlist_size': 9, 'unplayed_star_margin': 1.5}},
        {'id': 'p2', 'name': 'B', 'values': {'unplayed_star_margin': 3}}]}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert 'unplayed_star_margin' not in s.values() and 'unplayed_star_margin' not in s.values('p2')
    assert s.get('star_min') == 0 and s.get('star_max') == 0 and s.get('playlist_size') == 9
    s.update({'star_max': 9.5})
    raw = raw_of(tmp_path)
    assert 'unplayed_star_margin' not in raw and all('unplayed_star_margin' not in p['values'] for p in raw['presets'])
    assert raw['presets'][0]['values'] == {'playlist_size': 9, 'star_max': 9.5}
    with pytest.raises(SettingsError) as e:                                   # 画面から送っても受けない
        s.update({'unplayed_star_margin': 0.5})
    assert e.value.code == 'unknown-key'
    # セットを作る前の形（項目が直下）に残っていた物も
    (tmp_path / 'settings.json').write_text(json.dumps({'playlist_size': 9, 'unplayed_star_margin': 0.5}), encoding='utf-8')
    s.update({'country': 'us'})
    raw = raw_of(tmp_path)
    assert 'unplayed_star_margin' not in raw and raw['presets'][0]['values'] == {'playlist_size': 9}


def test_removed_key_cooldown_runs(tmp_path):
    # cooldown_runs（伸びなかった曲を直近何回分の出力について外す）は 2026-11-01 に遊んだ日で数えるクールダウンにした。
    # 残っていても無視して書く時に消し、新しい項目は初期値（再プレイ 3日あける・3日で4週休み・初プレイ 3日で4週休み）
    (tmp_path / 'settings.json').write_text(json.dumps({'player_id': '7', 'presets': [
        {'id': 'p1', 'name': 'A', 'values': {'playlist_size': 9, 'cooldown_runs': 5}},
        {'id': 'p2', 'name': 'B', 'values': {'cooldown_runs': 0}}]}), encoding='utf-8')
    s = Settings(str(tmp_path))
    assert 'cooldown_runs' not in s.values() and 'cooldown_runs' not in s.values('p2')
    assert [s.values('p2')[k] for k in ('replay_gap_days', 'replay_streak_days', 'replay_rest_weeks', 'unplayed_fail_days',
                                         'unplayed_rest_weeks')] == [3, 3, 4, 3, 4]
    s.update({'replay_rest_weeks': 2})
    raw = raw_of(tmp_path)
    assert all('cooldown_runs' not in p['values'] for p in raw['presets'])
    assert raw['presets'][0]['values'] == {'playlist_size': 9, 'replay_rest_weeks': 2}
    with pytest.raises(SettingsError) as e:
        s.update({'cooldown_runs': 1})
    assert e.value.code == 'unknown-key'


def test_star_range_min_not_above_max(tmp_path):
    # ★の範囲: 下限と上限がどちらも 0 より大きい時、下限が上限より上になる保存は断る（片方だけ変えた時もセットの今の値と比べる）。
    # どちらかが 0（効かない）なら何でも通す。比べるのは同じセットの値だけ
    s = Settings(str(tmp_path))
    s.update({'star_min': 7, 'star_max': 9})
    for changes, key in (({'star_min': 9.5}, 'star_min'), ({'star_max': 6.5}, 'star_max'), ({'star_min': 9, 'star_max': 8}, 'star_min')):
        with pytest.raises(SettingsError) as e:
            s.update(changes)
        assert e.value.code == 'bad-value' and e.value.key == key
    assert s.get('star_min') == 7 and s.get('star_max') == 9                  # 何も書いていない
    s.update({'star_min': 9})                                                 # 同じ値はよい
    s.update({'star_max': 0})
    s.update({'star_min': 12})                                                # 上限が 0 なら何でも
    s.update({'star_min': 0, 'star_max': 3})
    s.update({'star_min': 0})
    assert s.get('star_min') == 0 and s.get('star_max') == 3
    p2 = s.add_preset('B')
    s.update({'star_min': 5}, preset=p2)                                      # p1 の上限 3 とは比べない
    assert s.values(p2)['star_min'] == 5 and s.get('star_max') == 3
    s.update({'playlist_size': 8})                                            # ★の範囲を変えない時は確かめない
    with pytest.raises(SettingsError):
        s.update({'star_max': 4}, preset=p2)


def test_add_copy_and_defaults(tmp_path):
    s = Settings(str(tmp_path))
    s.update({'playlist_size': 8, 'modes': ['OneSaber'], 'playlist_title': '自分の名前'})
    pid = s.add_preset(' ワンセイバー ', copy_from='p1')
    assert pid == 'p2'
    v = s.values(pid)
    assert v['playlist_size'] == 8 and v['modes'] == ['OneSaber']              # 写す
    assert v['output_filename'] == 'blnext-p2.bplist'                          # ファイル名・名前は新しいセットの初期値
    assert v['playlist_title'] == '次回おすすめ（ワンセイバー）'
    assert s.values('p1')['output_filename'] == 'blnext-next.bplist'
    p3 = s.add_preset('白紙')
    assert s.values(p3)['playlist_size'] == 15                                 # 写さなければ初期値
    names = {p['id']: p['name'] for p in s.presets()}
    assert names == {'p1': 'スタンダード', 'p2': 'ワンセイバー', 'p3': '白紙'}
    p = next(x for x in s.presets() if x['id'] == 'p2')
    assert p['defaults'] == {'output_filename': 'blnext-p2.bplist', 'playlist_title': '次回おすすめ（ワンセイバー）'}


def test_update_and_reset_go_to_the_given_preset(tmp_path):
    s = Settings(str(tmp_path))
    p2 = s.add_preset('B')
    s.update({'playlist_size': 5}, preset=p2)
    assert s.values('p1')['playlist_size'] == 15 and s.values(p2)['playlist_size'] == 5
    s.update({'active_preset': p2})
    assert s.active_preset() == p2 and s.get('playlist_size') == 5           # 省略時は開いているセット
    assert s.view().values()['playlist_size'] == 5 and s.view('p1').get('playlist_size') == 15
    s.reset(['playlist_size'])
    assert s.values(p2)['playlist_size'] == 15
    with pytest.raises(SettingsError) as e:
        s.update({'playlist_size': 5}, preset='p9')
    assert e.value.code == 'no-preset'
    with pytest.raises(SettingsError):
        s.update({'active_preset': 'p9'})
    with pytest.raises(SettingsError):
        s.view('p9')


def test_output_filenames_must_differ(tmp_path):
    s = Settings(str(tmp_path))
    p2 = s.add_preset('B')
    with pytest.raises(SettingsError) as e:
        s.update({'output_filename': 'blnext-next.bplist'}, preset=p2)
    assert e.value.code == 'duplicate-filename'
    with pytest.raises(SettingsError):   # 大文字小文字だけの違いも同じファイル（Windows）
        s.update({'output_filename': 'BLNEXT-NEXT.bplist'}, preset=p2)
    # 別のセットが手でその名前を使っていたら、新しいセットの名前をずらす
    s.update({'output_filename': 'blnext-p3.bplist'}, preset='p1')
    p3 = s.add_preset('C')
    assert p3 == 'p3' and s.values(p3)['output_filename'] == 'blnext-p3-2.bplist'


def test_rename_delete_and_ids_not_reused(tmp_path):
    s = Settings(str(tmp_path))
    p2 = s.add_preset('B')
    s.rename_preset(p2, 'ワンセイバー')
    assert s.values(p2)['playlist_title'] == '次回おすすめ（ワンセイバー）'   # 名前を変えると、変えていないプレイリスト名も変わる
    for bad in ('', '   ', 'x' * 31, 'a\nb', 5, None):
        with pytest.raises(SettingsError) as e:
            s.rename_preset(p2, bad)
        assert e.value.code == 'bad-name'
    s.update({'active_preset': p2})
    s.delete_preset(p2)
    assert s.preset_ids() == ['p1'] and s.active_preset() == 'p1'
    assert s.add_preset('C') == 'p3'                                         # 消した p2 は使い回さない
    s.delete_preset('p1')
    with pytest.raises(SettingsError) as e:
        s.delete_preset('p3')                                                # 最後の1つは消せない
    assert e.value.code == 'last-preset'
    with pytest.raises(SettingsError):
        s.delete_preset('p9')


def test_reorder_presets(tmp_path):
    """セットの並び（タブの並び）を変える。ちょうどの並べ替え（今あるセットを1回ずつ）でなければ何も書かない。
    ほかの項目（共通の値・セットの値・知らないキー）はそのまま。開いているセットを消すと、並びの一番前のセットを開く"""
    s = Settings(str(tmp_path))
    s.update({'target_country_rank': 300})
    p2, p3 = s.add_preset('B'), s.add_preset('C')
    s.update({'playlist_size': 7}, preset=p3)
    raw = json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))
    raw['future_key'] = 1
    (tmp_path / 'settings.json').write_text(json.dumps(raw), encoding='utf-8')
    s.reorder_presets([p3, 'p1', p2])
    assert s.preset_ids() == [p3, 'p1', p2] and [p['name'] for p in s.presets(brief=True)] == ['C', 'スタンダード', 'B']
    assert s.values(p3)['playlist_size'] == 7 and s.get('target_country_rank') == 300
    raw = json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))
    assert raw['future_key'] == 1 and 'active_preset' not in raw
    assert s.active_preset() == 'p1'                       # 開いているセットは並べ替えでは変わらない
    for bad in ([p3, 'p1'], [p3, 'p1', p2, 'p9'], [p3, 'p1', 'p1'], [p3, 'p1', 'p9'], 'p1', None, [p3, 'p1', 2]):
        with pytest.raises(SettingsError) as e:
            s.reorder_presets(bad)
        assert e.value.code == 'bad-request'
    assert s.preset_ids() == [p3, 'p1', p2]
    s.delete_preset('p1')                                  # 開いていたセットを消すと、並びの一番前のセット
    assert s.active_preset() == p3 and s.preset_ids() == [p3, p2]


def test_too_many_presets(tmp_path):
    s = Settings(str(tmp_path))
    for i in range(9):
        s.add_preset('S%d' % i)
    with pytest.raises(SettingsError) as e:
        s.add_preset('over')
    assert e.value.code == 'too-many-presets'


def test_broken_presets_are_skipped(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'presets': [
        'x', {'id': 'bad'}, {'id': 'p2', 'name': 5, 'values': {'playlist_size': 'abc', 'country': 'us'}},
        {'id': 'p2', 'name': 'dup'}, {'id': 'p4', 'name': 'ok', 'values': {'playlist_size': 3}, 'cover': '../x.png'}]}), encoding='utf-8')
    s = Settings(str(tmp_path))
    ps = s.presets()
    assert [p['id'] for p in ps] == ['p2', 'p4'] and ps[0]['name'] == 'p2' and ps[1]['cover'] is None
    assert ps[0]['values']['playlist_size'] == 15 and ps[1]['values']['playlist_size'] == 3
    assert s.active_preset() == 'p2'   # 開いているセットが無ければ最初のセット


def test_cover_files(tmp_path):
    s = Settings(str(tmp_path))
    s.set_cover('p1', b'\x89PNG data', 'png')
    assert open(s.cover_path('p1'), 'rb').read() == b'\x89PNG data'
    p2 = s.add_preset('B', copy_from='p1')                                    # 複製すると画像も写す
    assert s.cover_path(p2).endswith('p2.png')
    s.set_cover('p1', b'\xff\xd8\xff jpeg', 'jpg')                           # 種類が変わると前のファイルは消す
    assert s.cover_path('p1').endswith('p1.jpg') and not (tmp_path / 'covers' / 'p1.png').exists()
    s.set_cover('p1', None, 'png')
    assert s.cover_path('p1') is None and not (tmp_path / 'covers' / 'p1.jpg').exists()
    s.delete_preset(p2)
    assert not (tmp_path / 'covers' / 'p2.png').exists()
    with pytest.raises(SettingsError):
        s.set_cover('p1', b'x', 'gif')


def test_bad_update_writes_nothing(tmp_path):
    s = Settings(str(tmp_path))
    with pytest.raises(SettingsError) as e:
        s.update({'playlist_size': 10, 'played_ratio': 101})
    assert e.value.code == 'bad-value' and e.value.key == 'played_ratio'
    assert not (tmp_path / 'settings.json').exists()


def test_dialog_only_keys_need_trust(tmp_path):
    s = Settings(str(tmp_path))
    with pytest.raises(SettingsError) as e:
        s.update({'beat_saber_dir': 'C:\\Games\\Beat Saber'})
    assert e.value.code == 'not-allowed'
    s.update({'beat_saber_dir': 'C:\\Games\\Beat Saber'}, trusted=True)
    assert s.get('beat_saber_dir') == 'C:\\Games\\Beat Saber'


def test_reset_removes_keys(tmp_path):
    s = Settings(str(tmp_path))
    s.update({'playlist_size': 10, 'replay_gap_days': 5})
    v = s.reset(['playlist_size'])
    assert v['playlist_size'] == 15 and v['replay_gap_days'] == 5


@pytest.mark.parametrize('key,value,ok', [
    ('playlist_size', True, False),          # bool は数値として受けない
    ('playlist_size', 10.0, True),
    ('playlist_size', 10.5, False),
    ('max_map_age_years', 2.5, True),
    ('max_map_age_years', float('nan'), False),
    ('player_id', '', True),
    ('player_id', '76561190000000000', True),
    ('player_id', 'abc', False),
    ('output_filename', 'next.bplist', True),
    ('output_filename', '../evil.bplist', False),
    ('output_filename', 'next.json', False),
    ('playlist_title', 'a\nb', False),
    ('modes', ['Standard', 'Standard'], True),
    ('modes', [], False),
    ('modes', ['Nope'], False),
    ('unplayed_pass_cap', 'p75', True),
    ('unplayed_pass_cap', 'p50', False),
    ('order', 'score', False),            # なくした項目（REMOVED_KEYS）
    ('unplayed_star_margin', 0.5, False),
    ('star_min', 7.5, True),
    ('star_max', 15, True),
    ('star_max', 15.1, False),
    ('star_min', -0.1, False),
    ('ui_scale', 145, True),
    ('ui_scale', 115.0, True),
    ('ui_scale', 120, False),             # 決めた段階だけ
    ('ui_scale', True, False),            # True は 1 と等しいが受けない
    ('ui_scale', '115', False),
])
def test_check(key, value, ok):
    if ok:
        check(key, value)
    else:
        with pytest.raises(SettingsError):
            check(key, value)
