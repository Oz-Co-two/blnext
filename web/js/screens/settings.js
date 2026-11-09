// 設定画面（docs/spec.md の「設定項目一覧」）。変えたらすぐ保存する（値の確かめは serve.py → core/settings.py）。
// 「全部のセットで共通」と「セットごと」（設定のパターン。presets.js）に分け、地の色で区別する。項目ごとに、値を変えると何が変わるかの
// 説明を付ける。縦に長いので、左に目次（押すとその項目へ飛ぶ。スクロールに合わせて今の場所に印）を置く。
// Beat Saber のフォルダは、自動で探した候補か exe 版のネイティブのダイアログでだけ選べる（bsdir.js。JS から場所を渡す口は無い）。
// プレイリストの画像は、画像を選ぶ小さな画面（cover.js。色違いから選ぶか、ファイルから読み込む）で変える。ファイルは画面で正方形に
// 切り抜いて縮め、中身（PNG/JPEG）だけをサーバーへ送る（場所は送らない）
import { api } from '../api.js';
import { t, tf } from '../i18n.js';
import { h } from '../util.js';
import { toast, reload } from '../main.js';
import * as refresh from '../refresh.js';
import { choose as chooseBsDir } from '../bsdir.js';
import { P, tabs as presetTabs, presetErr, difficultyControl, levelOf, levelName } from '../presets.js';
import * as scale from '../scale.js';
import { coverButton } from '../cover.js';
import * as update from '../update.js';

const COMMON_GROUPS = [
  ['basic', '基本', ['player_id', 'country', 'target_country_rank', 'neighbor_display']],
  ['pool', '比較範囲（取得する人）', ['pool_above', 'pool_below', 'pool_max']],
  ['output', '出力先', ['beat_saber_dir']],
  ['net', '通信', ['auto_refresh_hours', 'update_check', 'stats_notice']],
  // PlayerData.dat の場所（playerdata_path）と全曲データの取得（fetch_song_details）は出さない: 今はどちらも何も読まない（2026-11-06 に利用者が決めた）。
  // 使い始めたらここへ戻し、LABELS・DESCS（PlayerData.dat は選ぶ処理も）を足し直す。core/settings.py の SPEC は残してある
  // 表示言語（lang）は出さない: 最初の公開版は日本語だけ（2026-11-05 に利用者が決めた）。英語の訳ができたらここへ戻す
  ['ui', '画面', ['ui_scale']],
];
const PRESET_GROUPS = [
  ['playlist', 'プレイリスト', ['playlist_title', 'output_filename']],
  ['list', 'リストの条件', ['playlist_size', 'played_ratio', 'max_map_age_years', 'age_filter_unplayed', 'age_filter_played',
    'star_min', 'star_max', 'modes', 'one_diff_per_song', 'replay_gap_days', 'replay_streak_days', 'replay_rest_weeks', 'unplayed_fail_days', 'unplayed_rest_weeks',
    'challenge_max', 'unplayed_pass_cap', 'min_clear', 'acc_cap_margin', 'exclude_other_sets']],
  ['calc', '計算', ['min_scores', 'self_half_life_days', 'min_overlap', 'overlap_shrink', 'top_similar', 'min_similar_on_map',
    'self_weight', 'similar_prior', 'fc_gap_factor', 'stall_play_count', 'stall_days', 'clear_factor', 'beyond_star_factor',
    'unplayed_pass_drop', 'unplayed_tech_drop']],
];
const LABELS = {
  player_id: 'プレイヤーID', country: '国', target_country_rank: '目標の国内順位', neighbor_display: 'ホームに表示する上下の人数',
  pool_above: '候補の範囲（自分より上）', pool_below: '候補の範囲（自分より下）', pool_max: '比較する人数（スコアの多い順）',
  playlist_size: '曲数', played_ratio: 'プレイ済みの比率', max_map_age_years: '古い譜面のフィルタ（直近X年）',
  age_filter_unplayed: '古い譜面のフィルタを未プレイにかける', age_filter_played: '古い譜面のフィルタをプレイ済みにかける',
  star_min: '★の範囲（下限）', star_max: '★の範囲（上限）', modes: '対象のモード', one_diff_per_song: '同じ曲の別難易度は1つまで',
  replay_gap_days: 'クールダウン: 再プレイの間をあける日数', replay_streak_days: 'クールダウン: 再プレイを続けて遊ぶ日数',
  replay_rest_weeks: 'クールダウン: 再プレイの休み', unplayed_fail_days: 'クールダウン: 初プレイでクリアできない日数',
  unplayed_rest_weeks: 'クールダウン: 初プレイの休み', challenge_max: '挑戦枠（範囲の外の曲）',
  unplayed_pass_cap: '未プレイ曲の pass の上限', min_clear: 'クリアの見込みの下限', acc_cap_margin: '精度の上限の余裕', exclude_other_sets: '比べる用のセットどうしで同じ曲を入れない',
  unplayed_pass_drop: '未プレイ曲の pass による下げ幅', unplayed_tech_drop: '未プレイ曲の tech による下げ幅',
  min_scores: '曲線を作る最低スコア数', self_half_life_days: '自分の曲線の半減期', min_overlap: '共通曲の最低数',
  overlap_shrink: '類似度を0へ寄せる強さ', top_similar: '似ている人の人数', min_similar_on_map: '推定に必要な、その曲をプレイした似ている人の数',
  self_weight: '自分の結果の重み（β）', similar_prior: '似ている人の結果を控えめにする強さ（0で無し）', fc_gap_factor: 'ミス分の上乗せ割合', stall_play_count: '伸び悩みと見なすプレイ回数',
  stall_days: '伸び悩みと見なす更新なしの日数',
  clear_factor: 'クリアの見込みの効き方', beyond_star_factor: '★の範囲より上の下げ方（比較対象の人の何倍）',
  beat_saber_dir: 'Beat Saber のフォルダ', output_filename: '出力ファイル名', playlist_title: 'プレイリスト名',
  auto_refresh_hours: '起動時に自動で取得する間隔', update_check: '起動時に新しい版を確認する', stats_notice: '統計が非公開の時に警告する', lang: '表示言語',
  ui_scale: '文字の大きさ',
};
// 値を変えると何が変わるか（数値は、大きくした時・小さくした時）
const DESCS = {
  player_id: 'プレイリストを作る BeatLeader のプレイヤー。名前で探して選ぶと、国も合わせて設定し、すぐ取得を始めます。',
  country: '国内順位と比較対象を数える国（2文字の国コード。日本は jp）。プレイヤーを選ぶと自動で合わせます。',
  target_country_rank: 'ホームの「目標まで」に出す順位。取得の時、この順位の人がいるページも取ります。小さく（上の順位に）するほど、目標までの PP の差は大きくなります。',
  neighbor_display: 'ホームの「上下の人」に並べる人数（自分の上と下それぞれ）。表示だけで、リストの計算には影響しません。',
  pool_above: '似ている人を探す候補にする、国内順位で自分より上の人の範囲。大きくするとスコアの多い人を広く探せますが、順位の離れた人も入ります。小さくすると順位の近い人に絞られます。この中から「比較する人数」だけを取得します。',
  pool_below: '候補にする、自分より下の人の範囲。大きくすると候補が増え、小さくすると順位の近い人に絞られます。下の人は、自分がまだ取れていない精度の参考にはなりにくい一方、同じくらいの実力の人として役立ちます。',
  pool_max: '上の範囲の人のうち、ランクのスコアの多い順に何人を取得して比べるか。大きくすると通信と取得の時間が増え（初回は1人あたり約1回の通信）、小さくすると減りますが、似ている人が見つかりにくくなります。スコアが少ない人は、この人数に入っても取得しません。',
  beat_saber_dir: '「Beat Saber に書き込む」の書き込み先。この中の Playlists にプレイリストを書きます。BSManager を使っているなら、遊ぶ版のフォルダを選びます。',
  auto_refresh_hours: '起動した時、前回の取得からこの時間以上たっていれば、自動で BeatLeader から取り直します。それより短ければ取りに行かず、上の「更新」を押した時だけ取ります。大きくすると通信が減り、小さくすると起動時のデータが新しくなります。0 にすると起動するたびに取ります。',
  update_check: '起動した時に新しい版があるか確かめて、あれば更新内容を見せて更新するか聞きます（GitHub の公開の情報を読むだけで、利用者の情報は送りません）。オフにしても、下の「このアプリについて」の「更新を確認」で確かめられます。',
  stats_notice: 'BeatLeader の統計が非公開の時に、プレイリストを作る・出すたびに小さな画面で警告し、ホームにも知らせを出すか。非公開だと挑戦の記録を読めず、クリアの見込みとクールダウンが効きません。オフにしても警告と知らせが消えるだけで、働きは変わりません。止められるのはここだけです。',
  lang: '画面の言語。英語の訳はまだ一部だけです。',
  ui_scale: '画面全体の文字と部品の大きさ。上の帯の「A」のスライダーでも変えられます。大きくすると読みやすくなりますが、1画面に入る量は減ります。ウィンドウが狭い時は、崩れないよう自動で少し控えめにします。',
  playlist_title: 'ゲーム内のプレイリストの一覧に出る名前。セットごとに違う名前にすると、ゲームの中で見分けられます。',
  output_filename: 'Playlists に書き込むファイルの名前（英数字と - _ だけ・最後は .bplist）。セットごとに別の名前にします（同じだと上書きし合うため）。名前を変えると、前の名前のファイルは残ります。',
  playlist_size: 'プレイリストに入れる曲数。大きくすると1回で試す曲が増え、小さくすると1曲あたりに集中できます。',
  played_ratio: '曲数のうち、プレイ済みの曲（自己ベストを伸ばす曲）に使う割合。残りは未プレイの曲（新しく PP を取る曲）です。大きくすると自己ベストの更新が中心に、小さくすると新しい曲が増えます。片方の候補が足りない時は、もう片方で埋めます。',
  max_map_age_years: 'アップロードから何年以内の譜面だけを候補にするか（下の2つで、どちらにかけるかを選びます）。小さくすると新しい譜面に絞られ、大きくすると古い譜面も入ります。0 にすると絞りません。',
  age_filter_unplayed: 'オンにすると、未プレイの曲に「古い譜面のフィルタ」をかけます。',
  age_filter_played: 'オンにすると、プレイ済みの曲にも「古い譜面のフィルタ」をかけます。',
  star_min: '★（譜面の★。一覧に出ている★）がこの値より低い曲は、プレイ済み・未プレイとも入れません（固定した曲は入れます）。大きくすると易しい曲が外れて手ごたえのある曲に絞られ、小さくすると易しい曲も入ります。0 にすると自動で決めます（初期値）: このセットのモードで、自己ベストを超えた時に総PP が 0.5PP 以上増える曲が半分を超える一番低い★を下限にし、取得のたびに決め直します（易しすぎて更新しても総PP がほとんど増えない曲を入れないため）。決めた値はプレイリストの画面の条件の欄に出ます。上限より上にはできません。',
  star_max: '★がこの値より高い曲は、プレイ済み・未プレイとも入れません（固定した曲は入れます）。大きくすると難しい曲も入り、小さくすると今の実力で取りやすい曲に絞ります。0 にすると上限なしです。下限より下にはできません。値を選ぶ参考に、プレイリストの画面の条件の欄に、最近クリアした曲の★を出します。',
  modes: '候補にする譜面のモード。ワンセイバー用のセットなら OneSaber だけにします。',
  one_diff_per_song: 'オンにすると、同じ曲の別の難易度は1つしか入れません（同じ曲ばかりになるのを防ぎます）。',
  replay_gap_days: '自己ベストのある曲は、遊んだ日（挑戦の記録がある日。リストの外で遊んだ日も数えます）からこの日数のあいだ入れません（同じ曲が続けて出ないように）。大きくすると同じ曲が出る間があき、小さくすると続けて出やすくなります。0 にするとあけません。',
  replay_streak_days: '自己ベストのある曲を3週間のうちにこの日数遊んだら（初めてクリアした日から数えます。伸びたかは問いません）、しばらく休ませます（休みの長さは次の項目）。あなたの記録では、3週間で3日目になると自己ベストを超えるのは4回に1回ほどです。大きくすると同じ曲を何度も遊んでから休み、小さくすると早めに休みます。0 にすると休ませません。',
  replay_rest_weeks: '続けて遊んだ曲（上の項目）を、最後に遊んだ日から何週間入れないか。あなたの記録では、1か月ほどあけると半分以上が自己ベストを超えます。大きくすると長く休ませ、小さくすると早く戻ってきます。0 にすると休ませません。',
  unplayed_fail_days: 'まだクリアしていない曲を3週間のうちにこの日数挑戦してクリアできなかったら、しばらく休ませます（間はあけないので、覚えながら続けて挑戦できます）。あなたの記録では、2日目・3日目は4割ほどクリアでき、4日目からは下がります。大きくすると粘って挑戦し、小さくすると早めに休ませます。0 にすると休ませません。',
  unplayed_rest_weeks: 'クリアできない日が続いた曲（上の項目）を、最後に挑戦した日から何週間入れないか。大きくすると長く休ませ、小さくすると早く戻ってきます。0 にすると休ませません。',
  challenge_max: 'あなたがクリアしてきた曲の範囲の外の曲（pass か1秒のノーツが、クリアしてきた曲の95%を超える曲）を、1つのリストに何曲まで入れるか。大きくすると難しい曲・ノーツの多い曲に挑戦しやすくなり、小さくすると今の実力で取りやすい曲に絞ります。0 にすると入れません（固定した曲は数に入れますが、必ず入れます）。難しさのスライダーを動かすと、未プレイ曲の pass の上限と一緒に変わります。',
  unplayed_pass_cap: '未プレイの曲を、pass（BeatLeader のクリアの難しさ）がこの値までの曲に絞ります。基準は、あなたがクリアしてきた曲の pass の分布です（75%点の方が低い＝易しめに絞ります）。難しさのスライダーを動かすと、挑戦枠と一緒に変わります。',
  min_clear: 'その日のうちにクリアできる見込み（あなたの挑戦の記録から求めます）がこの値より低い曲は、プレイ済み・未プレイとも入れません（固定した曲は入れます）。大きくするとクリアしやすい曲だけになり、小さくすると難しい曲も入ります。0 にすると絞りません。難しさのスライダーの「着実」は 80% にします。',
  acc_cap_margin: '曲ごとの精度の上限（近い★＝±0.5 の、あなたのほかの曲の一番の精度）に足す余裕。上限より上の精度は取れないものとして見込みを出すので、自己ベストが上限以上の曲は伸びしろ0で入らず、上限に近い曲は後ろに回ります。大きくすると自分の一番に近い曲・超えた曲にも挑戦し、小さくすると伸びしろのある曲に絞ります。難しさのスライダーを動かすと変わります（着実 0・標準 0.5・チャレンジング 1.0）。',
  exclude_other_sets: 'オンにすると、ほかのセットのうちこれがオンのセットの今のリストに入っている曲（同じ曲の別の難易度も）を入れません。難しさ違いのセットを並べて遊び比べる時に、同じ曲がかぶらないようにします。',
  min_scores: 'ランクのスコアがこれより少ない比較対象の人は、「いつもの実力の曲線」を作れないので使いません。大きくすると確かな人だけになり、小さくすると使える人が増えますが曲線がぶれやすくなります。これより少ない人のスコアは取らないので、小さくした分は次の取得から効きます。',
  self_half_life_days: '自分の曲線を作る時、古いスコアをどれだけ軽く見るか（この日数ごとに重みが半分になります）。小さくすると最近の実力をよく映し、大きくすると昔のスコアも同じくらい使います。',
  min_overlap: '似ているかを比べるのに必要な、自分とその人の共通の曲の数。大きくすると比べ方が確かになりますが似ている人が減り、小さくすると増えますが、たまたま似て見える人も入ります。',
  overlap_shrink: '共通の曲が少ない人の類似度を 0 に寄せる強さ（類似度 ＝ 相関 × 共通曲数 ÷（共通曲数 ＋ この値））。大きくすると共通の曲が多い人ほど優先され、0 にすると相関だけで決めます。',
  top_similar: '推定に使う「似ている人」の人数（類似度の高い順）。大きくすると多くの人の結果を混ぜて安定しますが、あまり似ていない人も入ります。小さくすると特に似ている人だけで推定します。',
  min_similar_on_map: 'その曲をプレイした似ている人がこの人数より少ないと、確からしさを半分にします。大きくすると、参考にできる人の少ない曲が後ろに回りやすくなります。',
  self_weight: 'その曲に自分のスコアがある時、自分の上振れ・下振れをどれだけ重く見るか（似ている人1人分の重みは、その人の類似度）。大きくすると自分の過去の結果に寄り、小さくすると似ている人の結果に寄ります。',
  similar_prior: '似ている人の結果を控えめにする強さ。大きくするほど推定が「いつもの実力の曲線」の値に近づき、似ている人が少ない曲で推定が上振れしすぎるのを抑えます。0 にすると控えめにしません。',
  fc_gap_factor: 'ミスがあった自己ベストは、「FC していた場合の精度」との差のこの割合を推定に上乗せします（0〜1）。大きくすると「ミスさえなければ」の曲が上に来やすくなり、0 にすると上乗せしません。',
  stall_play_count: 'この回数以上プレイしているのに、下の日数以上自己ベストが変わっていない曲を「伸び悩み」とし、確からしさを半分にします。小さくすると伸び悩み扱いになりやすくなります。',
  stall_days: '自己ベストがこの日数以上変わっていない曲を伸び悩みの候補にします（挑戦の記録が取れる時は、この日数以内の挑戦で自己ベストに届いていないかでも判定します）。小さくすると伸び悩み扱いになりやすくなります。',
  clear_factor: 'その日のうちにクリアできる見込み（あなたの挑戦の記録から、pass ごとに求めます）を、並び順の値にどれだけ掛けるか（0〜1）。大きくするとクリアできそうな曲が上に来やすくなり、小さくすると見込みを気にせず「取れる PP」で選びます。0 にすると効かせません。増える総PP の表示は変わりません。',
  beyond_star_factor: 'あなたの★の範囲（ランクのスコアの★の95%点）より上の曲の推定精度を、比較対象の人の曲線の下がり方（★1 あたり）の何倍で下げるか。大きくすると難しい曲の推定が控えめになり、小さくすると今までの曲線どおりに近づきます。0 にすると下げません。',
  unplayed_pass_drop: '未プレイの曲（あなたの結果が無い曲）の推定精度を、pass が基準（あなたがクリアしてきた曲の pass の75%点）を超える分 1 あたり何 % 下げるか（★の範囲より上の曲は2倍）。pass の高い曲が苦手な人ほど効きます。大きくすると pass の高い未プレイの曲が後ろに回り、小さくすると★の曲線どおりに近づきます。0 にすると下げません。',
  unplayed_tech_drop: '未プレイの曲（あなたの結果が無い曲）の推定精度を、tech（BeatLeader の配置の複雑さの評価）が基準（あなたがクリアしてきた曲の tech の中央）を超える分 1 あたり何 % 下げるか。tech の高い曲が苦手な人ほど効きます。大きくすると tech の高い未プレイの曲が後ろに回り、小さくすると★の曲線どおりに近づきます。0 にすると下げません（tech の高い曲が苦手でない人は 0 に）。',
};
const UNITS = {
  target_country_rank: '位', neighbor_display: '人', pool_above: '人', pool_below: '人', pool_max: '人', playlist_size: '曲', played_ratio: '%',
  max_map_age_years: '年（0で無効）', star_min: '★（0で自動）', star_max: '★（0で無効）', challenge_max: '曲まで',
  replay_gap_days: '日（0であけない）', replay_streak_days: '日（0で休ませない）', replay_rest_weeks: '週', unplayed_fail_days: '日（0で休ませない）', unplayed_rest_weeks: '週', min_scores: '件', self_half_life_days: '日',
  min_overlap: '曲', top_similar: '人', min_similar_on_map: '人', stall_play_count: '回', stall_days: '日',
  auto_refresh_hours: '時間（0で毎回）', unplayed_pass_drop: '%（pass 1 あたり）', unplayed_tech_drop: '%（tech 1 あたり）', min_clear: '%（0でなし）', acc_cap_margin: '%',
};
// 数の入力の刻み（無ければ 整数は 1・小数は any）
const STEPS = {star_min: '0.1', star_max: '0.1'};
const CHOICES = {
  unplayed_pass_cap: {none: '上限なし', p95: 'クリアしてきた曲の95%点まで', p75: 'クリアしてきた曲の75%点まで'},
  lang: {ja: '日本語', en: 'English'},
};
// 難しさのスライダー（「リストの条件」の先頭。presets.js の difficultyControl）の説明。値を変えると何が変わるか
const DIFF_DESC = '動かすと、下の「未プレイ曲の pass の上限」「挑戦枠」「クリアの見込みの下限」「精度の上限の余裕」をまとめて変えます。着実の方へ動かすほど、クリアしやすい曲が中心になり'
  + '（増える総PP の見込みは小さくなります）、チャレンジングの方へ動かすほど、難しい曲・ノーツの多い曲が増えます（クリアできない曲も出やすくなります）。'
  + '個別に変えると「カスタム」になります。';
// まとめて初期値に戻す時も残す項目（それぞれの欄で変える）
const KEEP_ON_GROUP_RESET = new Set(['player_id', 'beat_saber_dir', 'lang']);

const ERRORS = {
  'bad-value': '値が正しくありません（範囲や形式を確かめてください）',
  'not-allowed': 'この項目は画面からは変えられません',
  'bad-request': '入力が正しくありません',
  'duplicate-filename': 'ほかのセットと同じファイル名です。別の名前にしてください',
  'bad-image': '画像を読めませんでした（PNG か JPEG を選んでください）',
  offline: 'BeatLeader につながりません',
  network: 'サーバーにつながりません',
  'open-failed': '開けませんでした',
};
const errText = code => ERRORS[code] ? t('set.err.' + code, ERRORS[code]) : presetErr(code);

let S = null;   // {values, spec, preset, presets}
let CTX = null;
let MAIN = null;

export async function render(main, ctx){
  CTX = ctx; MAIN = main;
  const r = await api('settings/get');
  if(!r.ok){ main.append(h('p', {class: 'err'}, errText(r.error))); return; }
  S = r;
  P.list = r.presets.map(p => ({id: p.id, name: p.name, cover: p.cover}));
  P.active = r.preset;
  if(r.difficulty) P.diff = r.difficulty;
  const preset = r.presets.find(p => p.id === r.preset);
  const common = h('section', {class: 'setPart common', id: 'setCommon'},
    h('div', {class: 'partHead'}, h('span', {class: 'partChip'}, t('set.chipCommon', '共通')), h('h2', {}, t('set.common', '全部のセットで共通'))),
    h('p', {class: 'mute small partNote'}, t('set.commonNote', 'プレイヤー・取得する範囲・出力先のフォルダなど。どのセットにも同じ値が使われます。')),
    COMMON_GROUPS.map(g => group(g)),
    aboutCard());
  const per = h('section', {class: 'setPart preset', id: 'setPreset'},
    h('div', {class: 'partHead'}, h('span', {class: 'partChip'}, t('set.chipPreset', 'セットごと')),
      h('h2', {}, tf('set.perPresetOf', '「{name}」のセット', {name: preset.name}))),
    h('p', {class: 'mute small partNote'}, t('set.perPresetNote',
      'リストの条件・計算の数値・プレイリスト名・画像はセットごとに持てます。例えば「ワンセイバー用」のセットを足して、モードを OneSaber だけにすると、別のプレイリストを作れます。下のタブで、どのセットを変えるかを選びます。')),
    presetTabs(() => redraw('setPreset')),
    presetCard(preset),
    PRESET_GROUPS.map(g => group(g)));
  main.append(
    h('div', {class: 'pageHead setHead'}, h('h1', {}, t('set.title', '設定'))),
    h('div', {class: 'setLayout'}, navMenu(preset.name), h('div', {class: 'setBody'}, common, per, mapsCard())));
  if(!MAIN.dataset.spy){ MAIN.dataset.spy = '1'; MAIN.addEventListener('scroll', spy, {passive: true}); }
  spy();
}

// 左の目次。押すとその項目へ飛ぶ。「全部のセットで共通」「セットごと」の見出しも押せて、その部分の見出しへ飛ぶ。
// スクロールに合わせて、いま見ている項目に印を付ける
function navMenu(presetName){
  const item = (id, label) => h('button', {class: 'navItem', dataset: {target: id}, onclick: () => jump(id)}, label);
  const head = (cls, id, first, label) => h('button', {class: 'navHead ' + cls, dataset: {target: id}, title: label,
    onclick: () => jump(id, first)}, label);
  return h('nav', {class: 'setNav', 'aria-label': t('set.navLbl', '設定の目次')},
    head('common', 'setCommon', 'grp_' + COMMON_GROUPS[0][0], t('set.common', '全部のセットで共通')),
    COMMON_GROUPS.map(([gid, gname]) => item('grp_' + gid, t('set.g.' + gid, gname))),
    item('grp_about', t('set.g.about', 'このアプリについて')),
    head('preset', 'setPreset', 'grp_presetCard', tf('set.navPreset', 'セットごと（{name}）', {name: presetName})),
    item('grp_presetCard', t('set.navCard', 'セットの名前・画像')),
    PRESET_GROUPS.map(([gid, gname]) => item('grp_' + gid, t('set.g.' + gid, gname))),
    head('common', 'grp_maps', 'grp_maps', t('set.g.maps', '全曲データ')));
}

let jumped = 0;   // 目次で飛んだ直後は、スクロールでの印の付け直しをしない（下の方の項目は一番上まで来ないため）
/** id の所へ飛び、目次の mark の項目に印を付ける（見出しを押した時は、その部分の最初の項目） */
function jump(id, mark = id){
  const el = document.getElementById(id);
  if(!el) return;
  el.scrollIntoView({block: 'start'});
  jumped = Date.now();
  document.querySelectorAll('.setNav .navItem').forEach(b => b.classList.toggle('on', b.dataset.target === mark));
}

function spy(){
  const nav = document.querySelector('.setNav');
  if(!nav || Date.now() - jumped < 400) return;
  const items = [...nav.querySelectorAll('.navItem')];
  const top = MAIN.getBoundingClientRect().top;
  let cur = items[0];
  for(const b of items){
    const el = document.getElementById(b.dataset.target);
    if(el && el.getBoundingClientRect().top - top <= 80) cur = b;
  }
  if(MAIN.scrollTop + MAIN.clientHeight >= MAIN.scrollHeight - 4) cur = items[items.length - 1];   // 一番下まで来た
  items.forEach(b => b.classList.toggle('on', b === cur));
}

async function redraw(scrollTo){
  const keep = MAIN.scrollTop;
  MAIN.replaceChildren();
  await render(MAIN, CTX);
  if(scrollTo){ const el = document.getElementById(scrollTo); if(el) el.scrollIntoView({block: 'start'}); }
  else MAIN.scrollTop = keep;
  spy();
}

function group([gid, gname, keys]){
  const resettable = keys.filter(k => !KEEP_ON_GROUP_RESET.has(k));
  return h('section', {class: 'card', id: 'grp_' + gid},
    h('div', {class: 'cardHead'},
      h('h2', {}, t('set.g.' + gid, gname)),
      resettable.length ? h('button', {class: 'small', onclick: () => resetKeys(resettable)}, t('set.resetGroup', '初期値に戻す')) : null),
    h('div', {class: 'rows'}, gid === 'list' ? diffRow() : null, keys.map(k => row(k))));
}

// 全曲データ（設定の一番下）: BeatLeader から全部取り直すボタン。ふつうは全曲データで変化を確かめて自動で取るので（core/fetch/refresh.py の _update_maps）、
// BeatLeader の負担にならないよう、押さなくてよいこと・何度も押さないことをボタンの所に書く
function mapsCard(){
  const m = S.maps || {};
  const when = x => x ? new Date(x * 1000).toLocaleString('ja-JP', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';
  const msg = h('p', {class: 'err small mapsMsg', role: 'status'});
  const btn = h('button', {class: 'mapsRefetch', onclick: async () => {
    msg.textContent = '';
    const r = await api('maps/refetch');
    if(!r.ok){
      msg.textContent = r.error === 'busy' ? t('set.maps.busy', '取得の途中です。終わってから押してください。')
        : r.error === 'no-player' ? t('set.maps.noPlayer', 'プレイヤーを選んでから押してください。') : errText(r.error);
      return;
    }
    btn.disabled = true;   // 続けて押せないように（この画面を開き直すまで）
    refresh.start();
    toast(t('set.maps.started', 'BeatLeader から全曲データを取り直しています。'), 'ok');
  }}, t('set.maps.button', '全曲データを更新'));
  return h('section', {class: 'card', id: 'grp_maps'},
    h('div', {class: 'cardHead'}, h('h2', {}, t('set.g.maps', '全曲データ'))),
    h('div', {class: 'rows'},
      h('div', {class: 'row', dataset: {key: 'maps_refetch'}},
        h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.maps.label', 'BeatLeader から全曲データを更新')),
          h('div', {class: 'desc'}, t('set.maps.desc', 'ランク譜面の情報（★・レーティングなど）を、BeatLeader のサーバーから全部取り直します。')),
          h('div', {class: 'warn small mapsWarn'}, t('set.maps.warn', 'BeatLeader のサーバーに負担がかかるので、ふだんは押さないでください。アプリが毎日、公開の全曲データ（kinsi55 さんの BeatSaberScrappedData）で変化を確かめ、変わった時だけ自動で取り直します。新しいランク譜面が出てこない、★がおかしいなど、困った時に1回だけ押してください。続けて何度も押さないでください。')),
          h('div', {class: 'desc mapsWhen'}, t('set.maps.last', '最後に BeatLeader から取得: '), h('span', {class: 'when'}, when(m.fetchedAt)),
            t('set.maps.checked', ' ／ 最後に変化を確かめた時: '), h('span', {class: 'when'}, when(m.checkedAt)),
            t('set.maps.asOf', ' ／ 全曲データの作成: '), h('span', {class: 'when'}, when(m.asOf)))),
        h('div', {class: 'val'}, h('div', {class: 'ctl'}, btn), msg))));
}

// 決まったページ・フォルダを開く（何を開くかはサーバーが決める。画面からは名前だけを送る）。開けなければ知らせる
async function openFixed(name, body){
  const r = await api(name, body);
  if(!r.ok) toast(errText(r.error), 'err');
}

// 「不具合を報告」は、GitHub を開く前に確認の小さな画面を出す（v1.0.1。ログインしていない人には、いきなり GitHub のログインの画面が
// 出て驚かせたため。報告は GitHub の Issues で受ける＝ログのファイルを添えてもらえる。少しの手間はあってよい、と利用者が決めた）。
// 最初に選ばれているのは「やめる」（Enter だけで開かないように）
function reportDialog(){
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const cancel = h('button', {class: 'reportCancel', onclick: close}, t('set.report.cancel', 'やめる'));
  const box = h('div', {class: 'modal narrow reportDlg', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('set.report.title', 'GitHub で不具合を報告します')),
    h('p', {}, t('set.report.login', 'GitHub の Issues（不具合の報告を受ける所）のページをブラウザで開きます。書き込むには GitHub のアカウントでのログインが要ります。ログインしていなければ、先に GitHub のログインの画面が出ます（アカウントは無料で作れます）。')),
    h('p', {}, t('set.report.body', '開くページには、このアプリの版と Windows の版を最初から入れてあります。起きたことと、その前にした操作を書いてください。')),
    h('p', {class: 'mute small'}, t('set.report.log', '「ログのフォルダを開く」で開くフォルダの blnext.log を、書く欄にドラッグして添えてもらえると、原因を調べやすくなります（ログには BeatLeader に問い合わせた URL＝あなたのプレイヤー ID が入ります）。')),
    h('div', {class: 'modalFoot'}, cancel,
      h('button', {class: 'reportLogs', onclick: () => openFixed('open/logs')}, t('set.about.logs', 'ログのフォルダを開く')),
      h('button', {class: 'primary reportGo', onclick: () => { close(); openFixed('update/open', {what: 'issues'}); }}, t('set.report.go', 'GitHub を開く'))));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  cancel.focus();
}

// このアプリについて: 版・更新の確認・ライセンス（GPLv3。無保証）とソースコードの場所・不具合の報告（GitHub の Issues）とログのフォルダ
function aboutCard(){
  return h('section', {class: 'card', id: 'grp_about'},
    h('div', {class: 'cardHead'}, h('h2', {}, t('set.g.about', 'このアプリについて'))),
    h('div', {class: 'rows'},
      h('div', {class: 'row', dataset: {key: 'about_version'}},
        h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.about.version', '版')),
          h('div', {class: 'desc'}, t('set.about.versionDesc', '新しい版があれば、更新内容を見せて、このアプリの中から更新できます。設定やデータはそのまま残ります。'))),
        h('div', {class: 'val'}, h('div', {class: 'ctl'}, h('span', {class: 'when'}, CTX.status.version || ''),
          h('button', {class: 'updCheck', onclick: () => update.manualCheck()}, t('set.about.check', '更新を確認'))))),
      h('div', {class: 'row', dataset: {key: 'about_license'}},
        h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.about.license', 'ライセンス')),
          h('div', {class: 'desc'}, t('set.about.licenseDesc', 'GNU GPL v3 のフリーソフトで、保証はありません。同じ条件で自由に配布・改変できます（全文はアプリのフォルダの LICENSE。使っている部品は THIRD_PARTY_NOTICES.txt）。'))),
        h('div', {class: 'val'}, h('div', {class: 'ctl'},
          h('button', {onclick: () => openFixed('update/open', {what: 'repo'})}, t('set.about.source', 'ソースコードを開く'))))),
      // 報告のページ（版と Windows の版を入れた本文付き）とログのフォルダは serve.py が決める（issue_url・open/logs）
      h('div', {class: 'row', dataset: {key: 'about_report'}},
        h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.about.report', '不具合の報告')),
          h('div', {class: 'desc'}, t('set.about.reportDesc', '動かない・表示がおかしいなど困った時は、「不具合を報告」を押して GitHub の Issues に書いてください（GitHub のアカウントが要ります）。開くページには、このアプリの版と Windows の版を自動で入れてあります。起きたこと・その前にした操作・期待したことを書いてもらえると助かります。「ログのフォルダを開く」で開くフォルダの中のファイル（blnext.log）を添えてもらえると、原因を調べやすくなります。ログには BeatLeader に問い合わせた URL（あなたのプレイヤー ID）と、失敗した時はこのアプリのフォルダの場所が入ることがあります。'))),
        h('div', {class: 'val'}, h('div', {class: 'ctl'},
          h('button', {class: 'reportIssue', onclick: reportDialog}, t('set.about.reportBtn', '不具合を報告')),
          h('button', {class: 'openLogs', onclick: () => openFixed('open/logs')}, t('set.about.logs', 'ログのフォルダを開く')))))));
}

// 難しさのスライダーの行（項目ではなく、未プレイ曲の pass の上限と挑戦枠をまとめて変える。今の値がどの段階とも合わなければカスタム）
function diffRow(){
  const keys = P.diff.levels.length ? Object.keys(P.diff.levels[0].values) : [];
  const def = levelOf(Object.fromEntries(keys.map(k => [k, defaultOf(k)])));
  return h('div', {class: 'row', dataset: {key: 'difficulty'}},
    h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.k.difficulty', '難しさ')),
      h('div', {class: 'desc'}, t('set.d.difficulty', DIFF_DESC))),
    h('div', {class: 'val'},
      difficultyControl(S.values, {preset: S.preset, id: 'set_difficulty', onSaved: v => {
        S.values = v;
        for(const k of keys){   // まとめて変えた項目の欄も今の値に
          const el = document.getElementById('set_' + k);
          if(el){ el.value = String(v[k]); setMsg(k, ''); flash(el); }
        }
        syncDiff();
        toast(t('set.saved', '保存しました'), 'ok');
      }, onError: code => toast(errText(code), 'err')}),
      def ? h('div', {class: 'def'}, tf('set.default', '初期値: {v}', {v: levelName(def)})) : null));
}

/** 難しさの行を今の値で描き直す（まとめて変えた時・pass の上限や挑戦枠を個別に変えた時） */
function syncDiff(){
  const el = document.querySelector('.row[data-key="difficulty"]');
  if(el) el.replaceWith(diffRow());
}

function defaultOf(key){
  const p = S.presets.find(x => x.id === S.preset);
  return p && p.defaults && key in p.defaults ? p.defaults[key] : S.spec[key].default;
}

function row(key){
  const spec = S.spec[key], val = S.values[key];
  const label = h('label', {class: 'lbl', for: 'set_' + key}, t('set.k.' + key, LABELS[key] || key));
  const desc = DESCS[key] ? h('div', {class: 'desc'}, t('set.d.' + key, DESCS[key])) : null;
  const unit = UNITS[key] ? h('span', {class: 'unit'}, t('set.u.' + key, UNITS[key])) : null;
  const msg = h('div', {class: 'msg', id: 'msg_' + key});
  const def = h('div', {class: 'def'}, tf('set.default', '初期値: {v}', {v: showValue(key, defaultOf(key))}));
  return h('div', {class: 'row', dataset: {key}},
    h('div', {class: 'what'}, label, desc),
    h('div', {class: 'val'}, h('div', {class: 'ctl'}, control(key, spec, val), unit), def, msg));
}

function showValue(key, v){
  if(key === 'ui_scale') return scale.label(v);
  if(CHOICES[key]) return t('set.c.' + key + '.' + v, CHOICES[key][v] || v);
  if(typeof v === 'boolean') return v ? t('set.on', 'オン') : t('set.off', 'オフ');
  if(Array.isArray(v)) return v.join(', ');
  if(v === '') return t('set.empty', '（なし）');
  return String(v);
}

function control(key, spec, val){
  const id = 'set_' + key;
  if(spec.kind === 'bool'){
    return h('input', {type: 'checkbox', id, checked: !!val, onchange: e => save(key, e.target.checked, e.target)});
  }
  if(spec.kind === 'int' || spec.kind === 'float'){
    return h('input', {type: 'number', id, value: String(val), min: spec.min, max: spec.max,
      step: STEPS[key] || (spec.kind === 'int' ? '1' : 'any'), class: 'num',
      onchange: e => { const n = Number(e.target.value); save(key, e.target.value === '' || !Number.isFinite(n) ? e.target.value : n, e.target); }});
  }
  if(spec.kind === 'choice'){
    const num = typeof spec.choices[0] === 'number';   // 文字の大きさ（%）など、選択肢が数の時は数で送る
    return h('select', {id, onchange: e => save(key, num ? Number(e.target.value) : e.target.value, e.target)},
      spec.choices.map(c => h('option', {value: String(c), selected: c === val}, showValue(key, c))));
  }
  if(spec.kind === 'choices'){
    const box = h('div', {class: 'choices', id});
    for(const c of spec.choices){
      const cb = h('input', {type: 'checkbox', value: c, checked: val.includes(c),
        onchange: () => save(key, [...box.querySelectorAll('input:checked')].map(x => x.value), box)});
      box.append(h('label', {class: 'chk'}, cb, c));
    }
    return box;
  }
  if(spec.kind === 'path') return pathControl(key, val);
  if(key === 'player_id') return playerControl(id, val);
  return h('input', {type: 'text', id, value: val, class: ({country: 'short'})[key] || 'wide',
    onchange: e => save(key, e.target.value.trim(), e.target)});
}

// セットの名前・画像・複製・削除
function presetCard(p){
  const name = h('input', {type: 'text', class: 'mid', value: p.name, maxlength: 30, id: 'presetName',
    onchange: async e => {
      const r = await api('presets/rename', {id: p.id, name: e.target.value});
      if(!r.ok){ toast(presetErr(r.error), 'err'); e.target.value = p.name; return; }
      toast(t('set.saved', '保存しました'), 'ok');
      redraw();
    }});
  // 画像（押すと画像を選ぶ小さな画面。cover.js）。アプリのアイコンに戻すのも小さな画面の「緑」のタイルで
  const thumb = coverButton({src: null, preset: p.id, name: p.name, big: true});
  api('cover/get', {preset: p.id}).then(r => { if(r.ok && r.dataUrl) thumb.querySelector('img').src = r.dataUrl; });
  let armed = null;
  const del = h('button', {class: 'small danger', disabled: S.presets.length <= 1,
    title: S.presets.length <= 1 ? t('set.delLast', '最後のセットは消せません') : '',
    onclick: async () => {
      if(!armed){   // 2回押して消す（間違えて消さないように）
        del.textContent = t('set.delConfirm', 'もう一度押すと消します');
        armed = setTimeout(() => { armed = null; del.textContent = t('set.del', 'このセットを消す'); }, 4000);
        return;
      }
      clearTimeout(armed); armed = null;
      const r = await api('presets/delete', {id: p.id});
      if(!r.ok){ toast(presetErr(r.error), 'err'); return; }
      toast(tf('set.deleted', '「{name}」を消しました', {name: p.name}), 'ok');
      redraw('setPreset');
    }}, t('set.del', 'このセットを消す'));
  return h('section', {class: 'card presetCard', id: 'grp_presetCard'},
    h('div', {class: 'rows'},
      h('div', {class: 'row'},
        h('div', {class: 'what'}, h('label', {class: 'lbl', for: 'presetName'}, t('set.presetName', 'セットの名前')),
          h('div', {class: 'desc'}, t('set.d.presetName', 'プレイリストの画面のタブに出る名前（例: スタンダード・ワンセイバー）。'))),
        h('div', {class: 'val'}, h('div', {class: 'ctl'}, name))),
      h('div', {class: 'row'},
        h('div', {class: 'what'}, h('span', {class: 'lbl'}, t('set.cover', 'プレイリストの画像')),
          h('div', {class: 'desc'}, t('set.d.cover', 'ゲーム内のプレイリストの一覧に出る画像。色違いの画像から選ぶか、ファイルから読み込みます（PNG・JPEG。正方形に切り抜いて、512×512 までに縮めます）。セットごとに色を変えると、ゲームの中で見分けやすくなります。選ばなければアプリのアイコン（緑）です。'))),
        h('div', {class: 'val'}, h('div', {class: 'ctl coverCtl'}, thumb,
          h('button', {class: 'coverPick', onclick: () => thumb.click()}, t('set.coverPick', '画像を選ぶ…')))))),
    h('div', {class: 'btnRow presetActs'},
      h('button', {class: 'small', onclick: async () => {
        const r = await api('presets/add', {name: (p.name + t('set.copySuffix', 'のコピー')).slice(0, 30), copyFrom: p.id});
        if(!r.ok){ toast(presetErr(r.error), 'err'); return; }
        toast(t('set.copied', 'セットを複製しました'), 'ok');
        redraw('setPreset');
      }}, t('set.copy', 'このセットを複製')),
      del));
}

// プレイヤーID: 直接入れるか、名前で探して選ぶ（選ぶと国も合わせ、すぐ取得を始める）
function playerControl(id, val){
  const idInput = h('input', {type: 'text', id, value: val, class: 'mid', inputmode: 'numeric',
    onchange: e => save('player_id', e.target.value.trim(), e.target)});
  const results = h('div', {class: 'results'});
  const q = h('input', {type: 'text', class: 'mid', 'data-i18n-ph': 'set.searchPh', placeholder: '名前で探す（BeatLeader の表示名）',
    onkeydown: e => { if(e.key === 'Enter') search(); }});
  async function search(){
    const name = q.value.trim();
    if(name.length < 2){ results.replaceChildren(h('p', {class: 'mute small'}, t('set.searchShort', '2文字以上入れてください'))); return; }
    results.replaceChildren(h('p', {class: 'mute small'}, t('set.searching', '探しています…')));
    const r = await api('players/search', {name});
    if(!r.ok){ results.replaceChildren(h('p', {class: 'err small'}, errText(r.error))); return; }
    if(!r.players.length){ results.replaceChildren(h('p', {class: 'mute small'}, t('set.searchNone', '見つかりませんでした'))); return; }
    results.replaceChildren(...r.players.map(p => h('div', {class: 'result'},
      h('span', {class: 'name'}, p.name),
      h('span', {class: 'mute'}, (p.country || '').toUpperCase()),
      h('span', {class: 'mute num'}, tf('set.resultRank', '世界 #{r}', {r: p.rank ?? '—'})),
      h('span', {class: 'mute num'}, (p.pp ?? 0).toLocaleString('ja-JP', {maximumFractionDigits: 2}) + ' PP'),
      h('button', {class: 'small', onclick: () => choose(p)}, t('set.choose', 'この人にする')))));
  }
  async function choose(p){
    const r = await api('settings/save', {changes: {player_id: p.id, country: p.country || 'jp'}});
    if(!r.ok){ toast(errText(r.error), 'err'); return; }
    S.values = r.values;
    idInput.value = p.id;
    const c = document.getElementById('set_country'); if(c) c.value = r.values.country;
    results.replaceChildren();
    if(CTX && CTX.status) CTX.status.playerId = p.id;
    toast(tf('set.chosen', '{name} さんにしました。BeatLeader からデータを取得します。', {name: p.name}), 'ok');
    refresh.start();
  }
  return h('div', {class: 'playerBox'},
    h('div', {class: 'ctlRow'}, idInput),
    h('div', {class: 'ctlRow'}, q, h('button', {onclick: search}, t('set.search', '探す'))),
    results);
}

function pathControl(key, val){
  const shown = h('span', {class: 'path'}, val || t('set.empty', '（なし）'));
  const pick = h('button', {onclick: () => {   // 自動で探す＋どの階層を選んでもよいフォルダ選択（bsdir.js）
    chooseBsDir(path => { shown.textContent = path; S.values[key] = path; setMsg(key, ''); toast(t('set.saved', '保存しました'), 'ok'); });
  }}, t('set.pickBs', '探す・選ぶ…'));
  const forget = h('button', {class: 'small', onclick: async () => {
    const r = await api('settings/reset', {keys: [key]});
    if(r.ok){ S.values = r.values; shown.textContent = t('set.empty', '（なし）'); }
  }}, t('set.forget', '解除'));
  return h('div', {class: 'pathBox'}, shown, pick, forget);
}

function setMsg(key, text){
  const el = document.getElementById('msg_' + key);
  if(el){ el.textContent = text || ''; el.classList.toggle('show', !!text); }
}

async function save(key, value, el){
  const r = await api('settings/save', {changes: {[key]: value}, preset: S.preset});
  if(!r.ok){
    setMsg(key, errText(r.error));
    restore(key, el);
    return;
  }
  setMsg(key, '');
  S.values = r.values;
  if(key === 'stats_notice' && CTX && CTX.status) CTX.status.statsNotice = r.values.stats_notice;   // ホームの知らせにその場で効かせる（警告は毎回サーバーに聞く。stats.js）
  if(P.diff.levels.some(l => key in l.values)) syncDiff();   // 難しさの段階が変わる（合わなくなればカスタム）
  if(key === 'lang') return reload();
  if(key === 'ui_scale') scale.set(value);   // 上の帯のスライダーも合わせる
  if(key === 'player_id' && value){
    if(CTX && CTX.status) CTX.status.playerId = value;
    refresh.start();
  }
  flash(el);
}

function restore(key, el){   // 保存できなかった値を元に戻す
  const v = S.values[key];
  if(!el) return;
  if(el.type === 'checkbox') el.checked = !!v;
  else if(el.classList.contains('choices')) el.querySelectorAll('input').forEach(c => { c.checked = v.includes(c.value); });
  else el.value = String(v);
}

function flash(el){
  if(!el) return;
  el.classList.remove('saved'); void el.offsetWidth; el.classList.add('saved');
}

async function resetKeys(keys){
  const r = await api('settings/reset', {keys, preset: S.preset});
  if(!r.ok){ toast(errText(r.error), 'err'); return; }
  if(keys.includes('stats_notice') && CTX && CTX.status) CTX.status.statsNotice = r.values.stats_notice;
  await redraw();
  toast(t('set.resetDone', '初期値に戻しました'), 'ok');
}
