# 実装前に確認したこと（2026-10-15）

仕様書（`docs/spec.md`）の「実装の進め方 → 1. 最初に確認すること」の結果。BeatLeader の API は実際に呼んで確かめ、
サーバーのソース（GitHub の BeatLeader/beatleader-server・master）と突き合わせた。ソースの行番号は確認した時点のもの。

## BeatLeader の API

### 回数の上限（`Startup.cs` の AspNetCoreRateLimit の設定・応答ヘッダー）

| 対象 | 上限 |
| --- | --- |
| `GET:/*`（全体） | 10秒に50回 |
| `GET:/players` | 10秒に10回 |
| `GET:/maps` | 1秒に8回 |

- 応答ヘッダー: `X-Rate-Limit-Limit: 10s`・`X-Rate-Limit-Remaining`・`X-Rate-Limit-Reset`（ISO 形式の時刻）。超えると 429。IP ごとに数える。
- → 仕様書の「1秒あたり5回まで」は全体の上限ちょうどなので、**全体は1秒4回まで、`/players` は2秒に1回まで**にする。
  `X-Rate-Limit-Remaining` が少なくなったら `X-Rate-Limit-Reset` まで待つ。

### 自分のプロフィール `GET /player/{id}?stats=true`

- `pp`・`rank`・`countryRank`・`accPp`・`passPp`・`techPp`・`lastWeekPp`・`lastWeekRank`・`lastWeekCountryRank`・`country`（大文字 "JP"）・
  `banned`・`inactive`・`scoreStats`（`rankedPlayCount`・`lastRankedScoreTime` など）。
- `lastWeek*` はライブの値ではなく、約7日前の履歴の記録から入る（`Services/HistoryService.cs` の SetLastWeek）。

### 国内ランキング `GET /players?countries=jp&sortBy=pp&count=100&page=N`

- `count` は100まで（101 は 400）。`metadata.total` は国で絞った人数。
- 各行: `id`・`name`・`pp`・`rank`・`countryRank`・`accPp`・`passPp`・`techPp`・`scoreStats.lastRankedScoreTime`・`lastWeek*` など
  （`banned`・`inactive` のキーは無い。一覧から BAN は除かれている）。
- 並びの位置と `countryRank` は一致する（3ページ300人で確認）。**順位 R はページ `ceil(R/100)` の `(R-1)%100+1` 番目**。
  上100人・下100人は `ceil((R-100)/100)` 〜 `ceil((R+100)/100)` ページ（R=566 なら5〜7ページの3回）。

### スコア `GET /player/{id}/scores?type=ranked&sortBy=date&count=100&page=N`

- `count` は100まで。`time_from`・`time_to` は Unix 秒。`order`（asc/desc）。
- 各スコア: `accuracy`（0〜1）・`pp`・`fcAccuracy`・`fcPp`・`weight`・`accPP`・`passPP`・`techPP`・`modifiers`（カンマ区切りの文字列、無しは ""）・
  `timeset`（文字列の Unix 秒）・`timepost`（数値）・`playCount`・`lastTryTime`・`badCuts`・`missedNotes`・`bombCuts`・`wallsHit`・`fullCombo`・`leaderboardId`。
- `leaderboard.song`: `hash`（小文字40桁）・`name`・`author`・`mapper`。**`uploadTime` は入っていない**（下の `/maps` で取る）。
- `leaderboard.difficulty`: `difficultyName`・`modeName`・`stars`・`predictedAcc`・`accRating`・`passRating`・`techRating`・`modifiersRating`
  （SS/FS/SF/BFS/BSF ごとの★・各レーティング）・`type`（数値。意味は未確認）・`rankedTime`。

### 比較対象のスコア `GET /player/{id}/accgraph?type=acc`

- ランク曲の全スコアを1回で返す（ページ分けなし）。項目: `acc`・`leaderboardId`・`diff`・`mode`・`modifiers`・`songName`・`hash`・`mapper`・`timeset`・
  `stars`・`pp`・`accRating`・`passRating`・`techRating`・`modifiersRating`。
- **`stars` はモディファイアで補正済み**（`PlayerScoresController.cs` の accgraph で、該当モディファイアのレーティングへ差し替えて倍率を掛けている）。

### 履歴 `GET /player/{id}/history/compact?count=N`

- 1日1件。`count` に上限なし（自分は約1年分＝367件が返った）。項目: `timestamp`・`pp`・`rank`・`countryRank`・`rankedPlayCount` など。
  accPp/passPp/techPp は無い。

### ランク譜面の一覧

- **`GET /maps?type=ranked&count=500&page=N`**（1行＝1曲）を使う。曲の `uploadTime`（Unix 秒）があり、`difficulties` の各難易度に
  `status`（3=ranked）・`stars`・`predictedAcc`・各レーティング・`rankedTime`。未ランクの難易度も混ざるので `status==3` で絞る。
  1617曲・3960難易度で、全部で4回。
- `GET /leaderboards?type=ranked`（1行＝1難易度）もあるが、`song.uploadTime` が 0 なので使わない。
- 確認した範囲のランク譜面は Standard だけ。
- 譜面の特徴（2026-10-16 に一覧へ出すため取り込んだ）: 曲に `bpm`・`tags`（BeatSaver のタグ。傾向の `tech`・`speed`・`dance-style`・
  `accuracy`・`balanced`・`challenge`・`fitness` と、ジャンルが混ざる）、難易度に `njs`・`nps`・`notes`・`bombs`・`walls`・
  `peakSustainedEBPM`（一番速い所が続く間の振りの速さ）。`nps` は BeatLeader の値で、ノーツ数 ÷ 長さとは少し違う。
  `plays` は 0 のまま・`difficultyStatistics` は null だった。

### BeatLeader のページ（ブラウザで開く）

- 譜面のランキング: `https://beatleader.com/leaderboard/global/<leaderboardId>/1`。
- リプレイ: `https://replay.beatleader.com/?scoreId=<スコアのID>`（公式の viewer・BeatLeader/BeatSaber-Web-Replays の README）。
  スコアの ID は `/player/{id}/scores` の各スコアの `id`。`?time=`（ミリ秒）・`?speed=`（%）も付けられる。

### リプレイの頭（JD）（2026-10-17）

- 自分のスコアの応答（`/player/{id}/scores`）にも、アテンプト（`scoresstats`）にも、JD（ジャンプの距離）・RT は**無い**。
  `offsets`・`metadata` は null か、リプレイの中の位置だけ。
- JD はリプレイ（.bsor。スコアの `replay` の URL。`https://cdn.replays.beatleader.com/<スコアのID>-<プレイヤーID>-<難易度>-<モード>-<HASH>.bsor`）
  の先頭の info にある。形式（BeatLeader/BS-Open-Replay）: int32 magic `0x442D3D69`・byte 版・byte 区分（0＝info）、
  文字列（int32 の長さ＋UTF-8）13個（version・gameVersion・timestamp・playerID・playerName・platform・trackingSystem・hmd・
  controller・hash・songName・mapper・difficulty）、int32 score、文字列3個（mode・environment・modifiers）、
  **float32 jumpDistance**、byte leftHanded、float32 height・startTime・failTime・speed。info は300バイトほど。
- CDN は Range に応える（`Range: bytes=0-2047` → 206 Partial Content。全体は約2MB）。先頭だけを読めば足りる。
  自分の Inertia の自己ベストで JD 21.11 を読めた（ほかに gameVersion 1.40.8・hmd・身長も入っている）。
- RT（反応時間）は入っていないので、JD の半分 ÷ NJS で計算する。曲の速さのモディファイア（SS/FS/SF）はノーツの速さも同じ倍率に
  なる（JD は変わらず RT が縮む）。BFS/BSF の倍率は未確認なので RT を出さない。

### アテンプト（1回ごとの挑戦の記録）`GET /player/{id}/scoresstats`

- **ある**。本人・管理者・統計を公開しているプレイヤーなら、ログインなしで取れる（`Controllers/StatsController.cs`）。
- 引数: `sortBy`・`order`・`page`・`count`（100まで）・`type`（ranked など）・`time_from`・`time_to`・`endType` など。
- **`sortBy` が既定の pp だと、PP が付いた挑戦だけに絞られる**。失敗・途中終了も含めて取るには `sortBy=date`。
- 項目: `endType`（1 クリア・2 失敗・3 リスタート・4 途中終了・5 練習）・`attemptsCount`・`accuracy`・`pp`・`modifiers`・`timeset`・`leaderboardId` など。

## PlaylistManager（Beat Saber の Mod）

ソース（rithik-b/PlaylistManager と BeatSaberPlaylistsLib）で確認した。

- **ゲームを起動したまま .bplist を書き換えても自動では読み直さない**（ファイルの監視が無い）。ゲーム内の「Refresh Playlists」ボタンか、
  曲の再読み込みが終わった時に読み直す。→ 画面の出力の案内で「ゲームを起動中ならプレイリストの再読み込みを押す」と伝える。
- 同じファイル名（大文字小文字は区別しない）なら同じプレイリストとして中身が入れ替わる。`playlistTitle` は同一性に使われない。
- `hash` は大文字・小文字どちらでもよい。足りない曲は `hash` だけで BeatSaver からダウンロードされる（`key` は不要）。
- 難易度の `name` は大文字小文字を区別しない（BeatLeader の出力に合わせて先頭を小文字にする）。`image` は `data:image/png;base64,…` でよい。
- `image` は最初の `,` までを読み飛ばして base64 を戻すだけ（BeatSaberPlaylistsLib の `Utilities.GetBase64DataStartIndex`）。
  中身は Unity が読むので PNG でも JPEG（`data:image/jpeg;base64,…`）でもよい（2026-10-16）。
- `customData.syncURL` があると、プレイリストの詳細に「Sync」ボタンが出る。押した時だけその URL を読みに行き、読めなければ
  「cannot be synced」の小窓が出るだけ（`PlaylistDetailViewButtonsController.SyncPlaylistAsync`。起動時などに自動では読まない）。

## BSManager のワンクリック（2026-10-16・BSManager v1.6.0 のソースで確認）

ソース（Zagrios/bs-manager の `local-playlists-manager.service.ts`）で確認した。BeatSaver などのサイトの「One-Click」ボタンと同じ仕組み。

- リンクは `bsplaylist://playlist/<プレイリストの URL>`。BSManager は `new URL(link)` の `pathname` の先頭の `/` を落とした物（と `search`）を
  プレイリストの URL として、ワンクリック用の小さな窓を開く。URL は http でもよく、`127.0.0.1` のローカルサーバーからも読める
  （Node の HTTP で GET。応答は JSON）。URL の代わりに PC 内のファイルのパスも受けるが、リンクの中では日本語などが %xx のまま残って
  見つからないので使わない。
- 入れ先は**入っている全部の版**の `Playlists`（最後の版に入れて曲をダウンロードし、ほかの版へ写す）。足りない曲は BeatSaver からダウンロードする。
- 上書きか新しいファイルか: 入れ先に**同じ `customData.syncURL` のプレイリストがあればそれを上書き**。無ければ `Content-Disposition` の
  ファイル名（無ければ `playlistTitle`.bplist）で作り、同じ名前が既にあれば番号を付けた別の名前にする。→ blnext は `syncURL` に
  `blnext://playlist/<出力ファイル名>` を入れ、書き込み・ワンクリックのどちらで入れても同じファイルになるようにした。
- Windows では `HKCU\Software\Classes\bsplaylist\shell\open\command` に `"…\BSManager.exe" "%1"` が登録される（BSManager の設定で
  ワンクリックを有効にした時）。ModAssistant も同じ `bsplaylist://` を登録することがある。blnext は `HKCR` を読んで、どのアプリが受けるかを画面に出す。
- `.bplist` のファイルの関連付けも登録され、ファイルを開くと同じワンクリックの窓が開く。
- 続けて何本も渡した時（2026-10-18・master のソースで確認。`main.ts`・`deep-link.service.ts`・`window-manager.service.ts`）:
  BSManager は1つしか起動しない（`requestSingleInstanceLock`。後から起動した方はリンクを先の BSManager に渡して終わる）。
  リンク1本ごとに**新しい窓**（`oneclick-download-playlist.html`）を開き、窓ごとに並べて入れる（前の窓を置き換えない）。
  窓は先にプレイリストのファイルを置き（`installBPListFile`）、それから曲を1曲ずつダウンロードする。1曲失敗しても記録して続ける。
  受け口（`addLinkOpenedListener`）は `whenReady` の後の初期化で登録されるので、起動しきる前に届いたリンクを取りこぼすおそれが
  否定できない → blnext は BSManager が前の分を読みに来てから次を渡す（serve.py の `export/oneclick/all`）。
  2つの窓が同じ曲を同時に落とすと、片方がその曲だけ失敗することはありうる（難しさ違いの組どうしは曲がかぶらない）。

## PP の計算式

- **2026-10-23〜26 に BeatLeader がリウェイトした**（「Rebalanced pp」。beatleader-server の `rebalanced-pp` ブランチ。master には
  入っていないが本番はこの式）。中身・自分への影響は 作者の調べ「BeatLeaderのリウェイト」。それに合わせて移植し直した。
- 移植元は `Utils/RebalancedPp/`（`Curve.cs`＝譜面ごとの精度の曲線・`PpFormula.cs`＝pass/acc/tech の式と★・`PpCalculator.cs`）と
  `Utils/ReplayUtils.cs`（`PpFromScore`・`GetCurveInputs`・`SpeedCurveInputs`・`GetTotalMultiplier`）、モディファイアの値の
  `beatleader-server-models` の `Models/Modifiers.cs`。総PP は `Utils/PlayerUtils.cs` の `RecalculatePPAndRankFastGeneral`
  （pp の高い順に 0.965^i の重みを掛けて合計。リウェイトでも変わっていない）。移植は `core/estimate/ppcalc.py`（移植元のコミットはファイルの先頭）。
- 精度の曲線は譜面ごと。材料は予測精度 `predictedAcc`・スライダーの割合 `sliderPercentage`・ノーツ数 `notes`・ノーツの出る長さ `noteSpan`
  （秒）で、API の difficulty にある（保存の層の leaderboards にも持つ）。SS/FS/SF は `modifiersRating` のそのモディファイアの予測精度・
  スライダーの割合と、長さを 0.85/1.2/1.5 で割った曲線。BFS/BSF は譜面の曲線のまま。
- 上流は曲線を作る所を倍精度、式の所を単精度で計算している。移植の PP は倍精度だけ（差は 0.003PP 以内）。モディファイアの倍率と総PP は
  前の版と同じく単精度を再現している。
- 譜面に `modifiersRating` があると、SS/FS/SF/BFS/BSF はレーティングと曲線ごと差し替え（倍率には入れない）。NF は PP 0。
- 照合（2026-10-28。`tools/verify_pp.py` と調べるための取得）: 自分のランクのスコア 753 件すべてで API の pp との差が 0.0002PP 以内・
  譜面の★の差 0.000002 以内・総PP と acc/pass/tech の内訳もプロフィールと一致。7 譜面の上位 700 件（SS/FS/SF/PM/DA など）も
  全件 0.003PP 以内。上流のテストの素材（RatingAPI の曲線の点と★）とも一致（`tests/fixtures/rating_curves.json`）。
- 上流の調整が続く時の備え（`core/fetch/refresh.py`）:
  - アプリの式の版（`ppcalc.UPSTREAM_COMMIT`）が前回の取得と違えば、譜面・自分・比較対象を全部取り直す（`meta.pp_formula`）。
  - 自己ベストの日時が同じなのに PP か★が変わった自分のスコアが多ければ（比べた数の 5% かつ 10 曲以上）、BeatLeader 側のリウェイトと見て、
    知らせて（`reweight`）全部取り直す。
  - 取った自分のスコアをアプリの式で計算し、0.5PP より大きくずれた物が 10% 以上なら知らせる（`pp-formula`。式の移植を直すまで推定の PP がずれる）。
- 実データで確かめられなかった分岐（移植は上流どおり）: `modifiersRating` の無い譜面、BFS/BSF 付きのスコア、RhythmGameStandard の譜面。
- 前の式（PPv3.6・2026-07-19 の master 1474312940）は `Curve2`（全譜面で共通の曲線）と `Inflate`（650·x^1.3/650^1.3）だった。

## 推定の確かめ（2026-10-15・自分のプレイ済みの曲で）

自分のプレイ済みの曲の残差（実際の精度 − 自分の曲線）を、自分の結果を伏せて似ている人だけで当てられるかを見た
（比較対象200人・似ている人20人・似ている人がプレイしていた曲715曲）。

| 推定のしかた | 誤差の平均（精度） |
| --- | --- |
| 自分の曲線だけ | 2.83% |
| 似ている人の上振れの重み付き平均（仕様書の式） | 2.75% |
| 同上＋控えめにする重み 0.6〜1.0 を分母に足す | 2.60% |
| 比較対象の全員の平均（似ているかを見ない） | 似ている人より当たらない（相関 0.31 と 0.48） |

- 「プレイスタイルが近い人を使う」狙いは効いている（全員の平均より、似ている人の方が当たる）。
- 似ている人が少ない曲は平均がぶれるので、「上振れ0」の仮の票を分母に足して控えめにした（設定 `similar_prior`、初期値 0.8。0 で仕様書の式）。
- 半年より前の自己ベストは、今の自分の曲線より平均 2.4% 低い（直近2か月は ±0）。自分の結果（β=2）を混ぜると、古い自己ベストの曲では
  推定が低めに出る。使いながら、振り返り（推定と実際の差）を見て β や半減期を調整する。
- ミスの多い自己ベストは、FC した場合の精度との差の半分が上乗せされる（仕様書）ため、推定が大きく伸びる曲がある（例: 80% → 92%）。
  → 2026-10-18 に、普通の量より多いミスの分だけにした（下の R1）。

### pass・ミスの量・クリアした日の数で直す（2026-10-18）

10/18 のプレイでクリアした10曲の「その回の一番 − 推定」が平均 −6.2%（pass 3.5 より上は −9.6%）だったので調べ、`recommend.estimate` に
3つ足した。調べた中身（表・候補の比べ）は 作者の調べ「精度の推定とpass」。

- R1: FC との差の上乗せは、その pass で普通のミスの量（自分の自己ベストの fc_acc − acc の pass ±0.5 の中央。`model.FcNorm`。この人は
  pass 2: 1.4%・3: 2.8%・4: 4.4%）より多い分 × `fc_gap_factor` だけ。ミスの量は pass とともに増える（偶然のミスでなく、次も出る分）。
- R2: 自分の結果が無い曲（未プレイ・NF のみ）は u × max(0, pass − 自分の pass の75%点) 下げる。★の範囲より上は2倍（根拠は26件と少ない）。
  u は設定 `unplayed_pass_drop`（初期値 1%）。★が同じなら自己ベストは pass 1 あたり −0.7〜−1.2% 低く、似ている人にはこの傾向がほぼ無い
  （−0.09%）ので、似ている人を混ぜても入らない。プレイ済みの曲は自分の残差に譜面の難しさが入っているので pass では外れない。
- R3: プレイ済みの曲は、その譜面をクリアした日の数 k で +1%（k=1）・0（2）・−1%（3以上）。自己ベストのある曲をクリアした日 785 件で、
  その日の一番 − 自己ベストは +1.88%・+0.07%・−1.86%。数えるのは挑戦の記録のクリア（NF を除く）と自己ベストの日（日の区切りは
  `clear.day_key`＝挑戦の記録のまとめ方と同じ 0 時）。挑戦の記録が無い人は直さない。自己ベストが記録の始まりより前の曲（その前の
  クリアした日が数えられない）は、3日以上と分かった時だけ直す。
- 確かめ（入れた関数で、調査の過去の（曲, 日）1066件を前半 〜07-31・後半 08-01〜 に分けて。昔の fc_acc が無いので R1 は含まない）:

  | | 後半の外れ | MAE | RMSE |
  | --- | ---: | ---: | ---: |
  | 入れる前 | −0.57 | 2.80 | 3.88 |
  | R2（基準＝75%点） | −0.39 | 2.78 | 3.81 |
  | R3 | −0.31 | 2.77 | 3.79 |
  | R2＋R3 | −0.13 | 2.75 | 3.73 |

  調査の U2＋K（基準 pass 3.0・4時区切り・モディファイア無しのクリアだけ）は −0.10 / 2.74 / 3.72 でほぼ同じ（基準を 3.0 にすると −0.09）。
- 10/18 08:19 のリストの元のデータ（10/17 21:12）に入れたコードで当てると、クリアした10曲の外れは −6.22% → −4.56%（pass 3.5 より上の
  6曲は −9.45% → −7.67%。調査の見積もりは −4.42%）。リストは15曲のうち6曲が入れ替わり、増える総PP の見積もりは 68.7 → 20.4PP
  （実際に遊んで +26.46）、pass の平均は 3.64 → 3.45。難易度を大きく下げるのは推定ではなく条件（未プレイ曲の pass の上限）の役目。

### 推定の幅（2026-10-28）

推定どおりの時の増えで並べると、推定が自己ベストより低い曲（PP の高い自己ベストに多い）はいつも 0 で、再プレイを選ばなかった。
自分の記録で幅を調べ、`recommend.estimate` の増える総PP を期待値にした。調べた中身は 作者の調べ「推定の幅」。

- データ: 自己ベストのある曲をクリアした（曲, 日）825件・初クリア 752件。推定はその日より前の自己ベストで作り直した。
- 今の推定は「その日の一番」の中心としてほぼ合っている（一番 − 自己ベスト ＝ −0.57% ＋ 1.08 ×（推定 − 自己ベスト）、残りの sd 3.4%）。
- 幅は精度の差では推定が高いほど縮む（推定 85% 未満の上の幅 4.1% → 95% 以上 1.1%）。ミスの割合の比 z ＝ log((1 − 推定)/(1 − 一番)) では、
  推定の高さにも pass にもほとんど依らない（良い側 0.23〜0.31）。
- z は左右で幅の違う正規分布によく当てはまる（`SPREAD`）。
  - 再プレイ: 中央 +0.04・良い側 0.24・悪い側 0.31。
  - 初クリア: 中央 −0.04・良い側 0.26・悪い側 0.37。
- 再プレイで「自己ベストを超えた割合」は予測 48.9% / 実際 46.7%、超えた分の平均は 1.37% / 1.41%。
  それまでにクリアした日が 1・2・3日以上で、超えた割合は 予測 73・53・31% / 実際 71・49・29%。
- R3 もミスの割合の倍率にした（0.91 / 1.09。精度 89% の曲で ±1%）。精度の差の ±1% のままでは、Easy の 96〜98% の曲を大きく上げすぎていた
  （96% の曲の +1% はミスを 25% 減らすのと同じ）。直した R3 で作り直しても幅はほぼ同じ（良い側 0.248 → 0.242）。
- 同じ日の夕方、利用者が「★の範囲を低くした方が見込みが大きい・チャレンジングが効いていない」と気づいた。並び順の値に掛ける確からしさが、
  自分の結果がある曲でも似ている人が3人未満なら半分で、★7〜8 の再プレイの 69% が当たっていた（確からしさの中央 0.38、★7 未満は 0.87）。
  再プレイで自己ベストを超えた割合は、似ている人が3人未満で 予測 50% / 実際 47%（284件）、3人以上で 48% / 46%（541件）と同じだったので、
  自分の結果がある曲の確からしさは 1 にした。チャレンジングPL（★0〜8）の見込みは +13.2 → +17.8、★6〜8 は +8.7 → +15.9。
- 未プレイの難しい曲は、★7 以上の初クリア 15件で推定より平均 +5.0%（R2 1.1%・R5 3.9% を引いていて、曲線とはほぼ同じ）。ただし★7 以上の
  自分のスコアは少なく、曲線がその曲で作られている（同じデータで確かめている）ので、R2・R5 は変えていない。出力の記録の `parts` で確かめる。

## クリアの見込みと高い★（2026-10-16・実際に遊んだ後の振り返りから）

11:35 に出した10曲（★7.1〜8.8）で自己ベストの更新は0、クリアは1曲だけだった。調べて分かったこと:

- 曲線は★6〜8.5 で 89.0% のまま平ら（スコアはクリアできた曲にしか無いので、難しい曲ほど上手くいった曲だけが残る）。
  曲線の上の方（その人の★の中央→95%点）の下がり方は、自分が ★1 あたり −0.97%、比較対象 163 人の中央が −2.86%（25〜75%: −3.49〜−2.03）。
  → 自分の★の95%点（★6.65）より上は、比較対象の人の下がり方で下げることにした（`beyond_star_factor`）。
- 自分のランクのスコア 738 曲の pass は 95% が 4.35 以下・最大 6.14。挑戦の記録で、クリアした挑戦の pass の中央は 2.64、失敗は 3.74。
- 挑戦の記録を（譜面, 日）にまとめた 1838 日（2025-09-29 から）で「その日のうちにクリアできた」割合:

  | pass | 0〜2 | 2〜3 | 3〜3.5 | 3.5〜4 | 4〜4.5 | 4.5〜5 | 5〜6 | 6〜 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | クリアできた日 | 95.5% | 89.8% | 83.5% | 68.8% | 70.5% | 56.5% | 25.5% | 5.3% |

  ロジスティック回帰の見込みは pass 2: 94%・3: 85%・4: 68%・5: 45%・6: 23%・7: 10%（よく合う）→ 並び順の値に掛ける（`clear_factor`）。
- 調整の後の同じデータのリストは ★6.3〜8.2・pass 3.0〜4.4 に収まり、範囲の外は挑戦枠の1曲だけになった。

### その譜面でクリアできなかった日が続いている曲（2026-10-18）

Pounce（★8.82・pass 2.81）は 9/16 に途中でやめた1回、10/16 にやり直し・途中でやめた・失敗・失敗の4回で、一度もクリアしていない
のに、pass だけで見込み 87% と出ていた。（譜面, 日）1854 日を時間の順にたどり、「その日より前の、最後にクリアした日の後の
クリアできなかった日の数 k」ごとに、その日にクリアできた割合を数えた（クリアした日で k は 0 に戻る＝失敗の後にクリアできた日も
数に入る。見込みは同じデータで pass から求めた回帰と、今の CLEARED_BONUS 込みの値）:

| k | 日数 | クリアできた | pass だけの見込み | 今の見込み |
| --- | --- | --- | --- | --- |
| 0 | 1688 | 86.7% | 84.6% | 88.1% |
| 1 | 118 | 55.1% | 73.2% | 78.2% |
| 2 | 29 | 48.3% | 68.8% | 72.6% |
| 3〜4 | 15 | 20.0% | 62.0% | 65.2% |
| 5〜 | 4 | 0.0% | 39.1% | 39.1% |

- 前にクリアした曲でも同じように低い（k=1 で 73.7%、今の見込みは 89.6%）。
- 前の日の中身で分けると、失敗（end_type 2）があった日の後は 40.0%（pass だけ 67.2%・65 日）、やり直し・途中でやめただけの日の後は
  73.6%（80.5%・53 日）。→ やり直し・やめただけの日は半分に数える（`SOFT_DAY_WEIGHT`）。
- 最後のクリアできなかった日からの日数で分けても、2〜7日 37.5%（73.7%）・7〜30日 47.2%（70.7%）・30日〜 51.4%（69.9%）とあまり
  戻らない。k を半減期で弱める形（7・30・90・180日）も試したが、弱めない形より当てはまりが悪いか同じだった → 日数では弱めない。
- 回帰で比べた（対数尤度は大きいほど、Brier は小さいほど良い。交差検証は譜面ごとに5つに分けた）:

  | 形 | 対数尤度 | Brier | 交差検証の対数尤度 | 交差検証の Brier |
  | --- | --- | --- | --- | --- |
  | 今の形（pass の回帰＋CLEARED_BONUS） | −692.1 | 0.1114 | −694.1 | 0.1118 |
  | ＋ c·log(1+k)（クリアできなかった日を全部1に数える） | −657.3 | 0.1052 | −660.1 | 0.1058 |
  | ＋ c·log(1+k)（やり直し・やめただけの日は 0.5）＝入れた形 | −655.6 | 0.1047 | −658.5 | 0.1053 |
  | （参考）CLEARED_BONUS の代わりに「前にクリアした」を回帰の項にして ＋ c·log(1+k) | −654.8 | 0.1054 | −658.3 | 0.1061 |

  下げる項は CLEARED_BONUS の後（今の見込みの対数オッズに足す）に入れた。前に入れる形（下げてからボーナス）は −667.2 で合わなかった。
  k の線形（c·k）より log(1+k) の方が少し良かった（−656.3 と −654.8。「前にクリアした」を項にした形で比べた）。
- 入れた形（`core/estimate/clear.py`）: 見込み ＝ 1 / (1 + e^−(今の見込みの対数オッズ + c·log(1+k)))。c は自分の記録から求め、この
  データでは **−2.035**（k>0 の日 166 日から）。k=0 の曲は今までと同じ。求めるのに足りない時（k>0 の日が30日より少ない）は
  控えめな −1.0。入れた後の見込みと実際:

  | 前にクリア | k | 日数 | クリアできた | 今の見込み | 入れた後 |
  | --- | --- | --- | --- | --- | --- |
  | なし | 0 | 858 | 82.8% | 83.3% | 83.3% |
  | なし | 0.5（やめただけの日1） | 17 | 58.8% | 73.8% | 60.9% |
  | なし | 1 | 46 | 30.4% | 65.4% | 37.1% |
  | なし | 1.5〜2 | 17 | 35.3% | 60.3% | 18.6% |
  | なし | 2.5〜 | 14 | 14.3% | 52.2% | 10.3% |
  | あり | 0 | 830 | 90.8% | 93.0% | 93.0% |
  | あり | 0.5 | 36 | 80.6% | 91.8% | 83.7% |
  | あり | 1 | 27 | 66.7% | 86.6% | 64.6% |
  | あり | 1.5〜2 | 6 | 33.3% | 90.2% | 53.8% |

- Pounce（失敗した日1・途中でやめただけの日1 → k=1.5）は 87.4% → 51.7%、並び順の値は 2.91 → 1.72。ただし総PP の増加（+4.75）が
  ほかの未プレイの曲（+1〜2）よりずっと大きいので、クールダウンを切って組み立てても未プレイの枠の1曲目のまま（今はクールダウンで外れている）。
  同じように1日失敗した Welcome To Chaos は 62% → 28% で、未プレイの枠の2番目から5番目に下がった。

## 使用中のファイルと自動更新（2026-10-17）

開発中に exe と `_internal/` を新しい版に差し替えようとしたら、`_internal\VCRUNTIME140.dll` が消せずに止まった（blnext は終了して
いた）。自動更新でも同じことが起きるので、Web の資料とこの PC での実験で調べた。

### 原因: PyInstaller の「DLL の探し先」が子プロセスに引き継がれる

- 経緯: blnext（exe 版）の「自分のリプレイを見る」が `webbrowser.open` で Chrome を起動した（Chrome が動いていなかったので blnext の
  子になる）→ その Chrome が拡張の補助プロセス（native messaging host の chrome-native-host.exe）を cmd.exe 経由で起動 → それが
  blnext の `_internal\VCRUNTIME140.dll` を読み込み、Chrome を閉じるまでつかみ続けた。
- PyInstaller 6.22.3 の bootloader は、Windows では `SetDllDirectoryW(_internal)` を呼ぶだけ（AddDllDirectory は呼ばず、PATH も
  変えない。`bootloader/src/pyi_main.c`）。Microsoft の SetDllDirectoryW の文書に「呼んだプロセスから起動した子の DLL の探す順番にも
  効く」とある。探す順番は「exe のフォルダ → SetDllDirectory の場所 → System32 → … → PATH」なので、自分の隣に VCRUNTIME140 を
  置いていないプログラムは、System32 より先に blnext の物を読む（VCRUNTIME140 は KnownDLLs に無い）。
- 実験（PyInstaller 6.22.3 の onedir の小さな exe。`_internal` にだけある DLL を子が名前だけで読めるか）: `subprocess.Popen`・
  `os.startfile`（ShellExecute。`webbrowser.open` の中身）・`cmd /c start` のどれでも、子の GetDllDirectoryW は親の `_internal` になり、
  その DLL を読めた。PATH に `_internal` は入っていなかった。
- `PYINSTALLER_RESET_ENVIRONMENT=1` は効かない（起動される側が PyInstaller 製の時に、PyInstaller の内部の環境変数を捨てるだけ）。
- 公式の対処（PyInstaller の説明書 "Common Issues and Pitfalls" の "Launching External Programs from the Frozen Application"・
  issue #3795・#7594）: `ctypes.windll.kernel32.SetDllDirectoryW(None)`。実験で、呼んだ後に起動した子には引き継がれなかった。
- 戻した後の親（凍結した exe）への影響（実験）:

  | 親の読み方（`_internal` にだけある DLL・.pyd） | 何もしない | `SetDllDirectoryW(None)` | ＋先に `os.add_dll_directory(_internal)` |
  | --- | --- | --- | --- |
  | .pyd の import（依存の libssl・sqlite3.dll なども） | 読める | 読める | 読める |
  | ctypes の名前だけ（PyInstaller が `_MEIPASS` のフルパスに書き換える） | 読める | 読める | 読める |
  | ネイティブのコードの素の `LoadLibraryW(名前だけ)` | 読める | **読めない（126）** | **読めない（126）** |
  | `LoadLibraryExW(…, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)` | 読める | 読めない | 読める |
  | 子（Popen・startfile）が名前だけで読めるか | 読める（漏れる） | 読めない | 読めない（AddDllDirectory は子に引き継がれない） |

  → 起動直後に戻しっぱなしにすると、ネイティブのコードが名前だけで `_internal` の DLL を読む所（pywebview → pythonnet → WebView2
  のあたりが候補。未確認）で壊れるおそれがある。`SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)` も足すとその穴は塞がるが、
  プロセス全体で PATH とカレントフォルダを探さなくなり、元に戻せない。外のプログラムを**起動する直前だけ**戻してすぐ元に戻す形なら、
  親の動きはほぼ変わらない（戻している数ミリ秒の間に別のスレッドがネイティブのコードで DLL を読むと競合するが、Python の import は影響しない）。
- 同じ構図の報告: PyInstaller #3795（凍結アプリから起動した子が、凍結アプリの Qt の DLL を読んで動かなくなる）・#9075（外から
  注入された DLL が同梱の VCRUNTIME140 をつかみ、onefile の一時フォルダが消えない）・cx_Freeze #2140。
- BSManager（`bsplaylist://`）も、動いていない時に blnext から起動されると同じ探し先を引き継ぐ（起動したまま使われることが多い）。
  Chrome が既に動いている時は、新しい chrome.exe が URL を渡してすぐ終わるので、引き継ぎは残らない。

### 使用中のファイルで何ができるか（NTFS・管理者でない。実験）

| 操作 | 読み込み中の DLL・実行中の exe |
| --- | --- |
| 削除・上書き（`os.remove`・`open('r+b')`・`os.replace` で上へ置く） | できない（WinError 5） |
| POSIX 削除（FileDispositionInfoEx。Windows 10 以降） | できない（読み込み中の物は消せない） |
| 同じフォルダの中での名前の変更・同じドライブの別フォルダへの移動 | できる |
| **中に使用中の DLL がある `_internal` を、フォルダごと名前の変更・移動** | できる |
| `shutil.rmtree` | 途中まで消えて止まる（今回の失敗） |

- 名前を変えた直後に、元の名前で新しい物を置ける。つかんでいるプロセスは古い物で動き続け、終わった後なら古い物を消せる。
- フォルダの名前の変更は、中に**ハンドルが開いている**ファイル（開いたファイル・作業フォルダ・エクスプローラーの窓など）があると
  失敗する（FILE_RENAME_INFORMATION の文書）。DLL の読み込みはハンドルを残さないので通る。
- 再起動の時に消す予約（MoveFileEx の MOVEFILE_DELAY_UNTIL_REBOOT）は管理者が要るので、一般ユーザーのアプリでは使えない。

### 使っているアプリを調べる（Restart Manager）

- rstrtmgr.dll の RmStartSession → RmRegisterResources（ファイルを1つずつ。フォルダは不可）→ RmGetList → RmEndSession を ctypes で
  呼べる。管理者でなくても、読み込んでいるだけの DLL でも、PID とアプリの名前が分かった（今回の DLL では「chrome-native-host.exe」）。
- 「○○が blnext のファイルを使っています。閉じてからもう一度」の案内に使える（閉じさせることはしない）。

### 自動更新の作り方の比較

| 方式 | 使用中のファイルがあっても更新できるか | 管理者なしで動くか | 古い版の片付け |
| --- | --- | --- | --- |
| 旧版を名前の変更で退避して差し替え（NLM の作り） | できる（上の実験） | exe の隣に書ける場所なら | 退避した版の削除はつかまれている間は失敗する → 次に起動した時にやり直す |
| 版ごとのフォルダ＋小さな起動用 exe（Squirrel.Windows） | できる（新しい版は別のフォルダ） | できる | 古い版を後で消す |
| Velopack（MIT・Python 向けの SDK あり・作るのに .NET SDK が要る） | `current/` を名前の変更で入れ替える（上と同じ考え方）。中の exe で動いている物は止めるが、外のプロセスは対象外 | できる | 一時フォルダへ移して消す |
| tufup（PyUpdater の後継） | 弱い（同じフォルダへ robocopy で上書き。使用中の DLL で待ち続ける） | できる | 入れる前に消す（任意） |

### 結論（自動更新を作る時の対策）

1. **そもそもつかませない**: 外のプログラム（ブラウザ・BSManager・更新の補助プロセス）を起動する直前だけ `SetDllDirectoryW(None)` で
   探し先を戻し、起動したら元に戻す（起動する所は1か所の関数にまとめる）。起動直後に戻しっぱなしにする方が漏れは確実に無くなるが、
   本物の exe で画面（WebView2）が出ることを確かめてからにする（上の表）。外のプログラムを起動する時は、作業フォルダもアプリの外に
   する（`os.startfile(url, cwd=…)`。作業フォルダはハンドルなので、アプリのフォルダの名前の変更を止める）。
2. **差し替えは削除でなく名前の変更（退避）で**。退避した古い版の削除は失敗してよく、次に起動した時にやり直す。
3. それでも失敗した時は、Restart Manager で使っているアプリの名前を出して案内する。
4. Velopack などの仕組みは 1 の代わりにならない（どれも最後は名前の変更に頼り、外のプロセスにつかまれるのは防げない）。

## まだ確かめていないこと

- 難易度の `type`（数値。譜面タイプのビットと思われる）の意味。
- `inactive` のプレイヤーが国内順位にどう影響するか。
- 429 を受けた時の実際の応答（`Retry-After` の有無）。
