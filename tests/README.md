# テスト

```
.venv-build/Scripts/python.exe tools/run_tests.py              全部（unit・e2e・visual を並列に。約1分半）
.venv-build/Scripts/python.exe tools/run_tests.py e2e --no-split   分けずに1本で順番に流す（テスト同士の影響を疑う時の切り分け用）
.venv-build/Scripts/python.exe tools/run_tests.py unit         グループを指定
.venv-build/Scripts/python.exe tools/run_tests.py -k sort      名前の一部で絞り込み（pytest の -k）
.venv-build/Scripts/python.exe tools/run_tests.py --coverage   Python と JS のカバレッジも測る（test-out/coverage/）
.venv-build/Scripts/python.exe tools/run_tests.py visual --accept   画面の比較の基準を今の画面で置き換える（見た目を意図して変えた時）
```

画面には要約だけを出す。失敗の詳細は `test-out/last.txt`、全結果は `test-out/summary.json`。失敗・エラーのあった回は
`test-out/history/<日時>/` にも残す（その時の画面の画像 `screens/` も。新しい順に30回分）。終了コード: 0=失敗なし / 1=失敗あり。
**要確認**は失敗ではないが人の目が要るもの（画面の比較で見た目が基準と変わった）。下の「画面の比較」参照。

**分けて同時に流す**（2026-11-09。それまで e2e が1本の Edge で約4分かかり、全体もそれで決まっていた）: e2e は6つ・unit は4つの組に分け、
別々のプロセス（それぞれ自分の Edge・サーバー・作り物のデータ）で同時に流す（`tools/run_tests.py` の `SHARDS`）。組の分け方は
前回の各テストの所要時間（`test-out/test_secs.json`。流すたびに更新）で、長いテストから今いちばん短い組へ入れる
（`tests/conftest.py` の `pytest_collection_modifyitems`。どのプロセスも同じ分け方を求める）。`-k`・`--coverage` の時は分けない。
visual は決まったフォルダの作り物のデータを使い、最後にまとめを1つ作るので分けない（全体の長さはほぼ visual で決まる）。
テストの Edge は既定で GPU で描く（この PC では RTX の Direct3D11。NLM のようにソフトウェア描画を指定していない）。
**どの組に入っても同じ結果になるように書く**: e2e の `ui` は、テストごとに始めた時の設定ファイル（`app.base_settings`）・保存したリスト・
固定などを戻す。前のテストが残した状態に頼ると、組によって落ちる（分けた最初の回に3件あった）。疑わしい時は `--no-split` と比べる。

## グループ

| フォルダ | 中身 | ブラウザ |
|---|---|---|
| `unit/` | Python 側（PP の計算・設定・取得・推定・組み立て・出力・振り返り・出力先の探索・serve.py の守り・画面の比較の部品） | 使わない |
| `e2e/` | 画面（全画面 × 幅 960/1280/1600 の崩れ・JS のエラー、おすすめ画面・設定画面の操作、出力、取得の進捗） | ヘッドレスの Edge |
| `visual/` | 画面の比較（決まった画面を撮り、基準の写真と画素で比べる） | ヘッドレスの Edge（e2e とは別） |
| `release/` | **公開の直前だけ**流す特別なテスト（アプリの中の更新の失敗の時の分岐）。全グループには入らず、`run_tests.py release` と指名した時と、`tools/make_release.py` を起動した時だけ流れる | 使わない |

- どれもネットに出ない（`BLN_NO_NET=1`）。BeatLeader の応答は `_lib/fakebl.py`（作り物）。画面のデータは `_lib/fixture_data.py` が
  作り物の BeatLeader から取得して作る（長い名前・日本語・絵文字・HTML のタグに見える名前を混ぜている）。
- 画面のテストは Edge を1つ起動して使い回し、テストごとに設定・足したセット（並べ替えた順も）・固定・外した曲・テストで出力した記録を
  戻す（`e2e/conftest.py` の `ui`）。
  消したセットの id は使い回さないので、テストで足したセットの id は毎回違う（`p2` と決め打ちせず、`add_preset` が返した id を使う）。
- ワンクリック（`bsplaylist://`）のテストは `oneclick.launch` を必ず差し替える（本物を呼ぶと、この PC の BSManager が起動する）。
  各テストの終わりにページのエラー（例外・console.error・ブラウザのログのエラー）が無いことも確かめる。
- 撮った画像は `test-out/screens/<画面>_<幅>.png`。崩れは画像を使わずに見つける（`tools/cdp.py` の `LAYOUT_JS`）:
  ページの横のはみ出し・カードからのはみ出し・「…」以外での文字の切れ・ボタンや入力欄の重なり・null/undefined/NaN の表示。
- **新しい画面・ダイアログ・開いた状態を足したら** `e2e/test_screens.py` の `STATES`（画面そのものなら `tools/cdp.py` の `SCREENS`）に足す。
  文字を最大（145%）にした時も崩れないか確かめたい状態は `LARGE_STATES` にも足す（全画面は `test_screen_large_text` が幅 960・1280 で確かめる）。
- 枠からのはみ出しの確かめは、枠の中の「横にスクロールする所・切って隠す所」に入った物を除く。2026-10-16 までは本文（`#main`。縦の
  スクロールのため overflow:auto）まで見てしまい、本文の中のはみ出しを全部見逃していた（似ている人の表が幅 960 ではみ出していた）。
- 本物のデータで見たい時は `tools/cdp.py check --data <書き込み先>`（中は書き換わることがある）。

## 画面の比較（visual/）

NLM の tests/visual と同じ考え方。崩れ（重なり・はみ出し）は e2e が見つけるが、色・余白・文字の大きさのような「崩れではない見た目の
変化」は見つけられないので、写真で比べる。

- 決まった画面（`visual/screens.py` の `SHOTS`。4画面×3幅・開いた状態・文字の最大）を撮り、基準の写真 `tests/visual/baseline/<名前>.png`
  と比べる。基準は **PC ごと**（git に入れない。フォントの描画・GPU の違いで数画素変わるため）。基準が無ければ今の写真を基準にする（初回）。
- 判定（`visual/vislib.py` の冒頭）: R・G・B のどれかの差が 32/255 を超えた画素を「変化」とし、25画素以上で「画面が変化した」。
- 変化した画面は失敗にせず**要確認**にする。`test-out/visual/review.png`（基準｜今｜変わった所（赤）を並べた1枚）と `report.html`
  （前後を並べたページ。押すと原寸）を見て、意図した変化なら `tools/run_tests.py visual --accept` で基準を置き換える。
- 撮るたびに同じ絵になるようにしている（変えると毎回「要確認」になる）:
  - データは毎回同じ場所（`test-out/visual/_data`）に作る（出力先の場所が画面に写る）。出力先の候補の作り物も同じ場所（`_bs`）。
  - 計算に使う「いまの時刻」を決まった値にする（`visual/conftest.py` の `FIXED_NOW`。自分の曲線は直近を重く扱うので、時刻が進むと
    総PPの小数などが変わる）。時計を9日進めても同じ絵になることを確かめた（2026-10-16）。
  - 時刻の表示（`#dataAt`・class `when` の要素）と版を隠し、動き・文字の点滅を止める（`vislib.FREEZE_CSS`）。**画面に時刻を出す所を
    足したら class `when` を付ける**。フォントと画像を読み終えてから撮る。縦に長い画面は、画面の高さを伸ばして全体を撮る。
  - 画面ごとに、足したセット・設定・固定・出力の記録・セットの id の番号を戻す（`visual/conftest.py` の `reset`）。
- 新しい画面・小さな画面を足したら `SHOTS` に1つ足し、`tools/run_tests.py visual -k <名前>` で一度流して
  `test-out/visual/current/<名前>.png` に目的の画面が写っているか見る。
- Edge の更新・GPU のドライバの更新で文字の描画が変わると、全画面が要確認になることがある（NLM で経験済み）。中身を確かめて `--accept`。

## 手で画面を確かめる（tools/cdp.py）

```
.venv-build/Scripts/python.exe tools/cdp.py check                                  全画面 × 幅の崩れ・エラーを文字で
.venv-build/Scripts/python.exe tools/cdp.py shot out.png --screen recommend --width 960
.venv-build/Scripts/python.exe tools/cdp.py eval "document.title" --screen settings
```

Edge とはパイプで話す（TCP のデバッグポートは開かない）。プロファイルとデータは一時フォルダで、終わったら消す。

## カバレッジ

`--coverage` で、unit と e2e の両方で動いた Python（core・serve・app）と、画面のテストで動いた JS（web/js）の割合を出す。
ファイルごとの割合は `test-out/coverage/python.txt`、JS は `js.txt`（5行以上続けて動いていない所の行番号も）。
「動いた」は「確かめた」とは限らない（画面を開いただけの処理も数える）。`app.py` の窓を出す部分はテストでは動かせない
（ビルドの時に `--selftest` で起動だけ確かめる）。
