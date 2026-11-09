// 使い方（チュートリアル）。上の帯の「使い方」と、初回の準備の最後から開く。2026-11-09 に最初の公開（v1.0.0）に合わせて作った。
// 画面の画像（web/guide/*.png）は作り物のデータの画面から tools/make_guide_images.py が作る（画面を変えたら作り直す）。
// BeatLeader の設定の画像は stats.js の howTo（統計の案内の小さな画面と同じ物）。
// 文中のボタン・項目の名前は、実際の画面の文言と同じにする（変えたらここも直す）
import { t } from '../i18n.js';
import { h } from '../util.js';
import * as stats from '../stats.js';

const SECTIONS = [
  ['flow', '遊び方の流れ'],
  ['home', 'ホーム'],
  ['recommend', 'プレイリスト'],
  ['stats', '統計を公開にする'],
  ['similar', '似ている人'],
  ['settings', '設定'],
  ['trouble', '困った時'],
];

const img = (name, alt, w, hgt) => h('img', {class: 'guideImg', src: 'guide/' + name, alt, width: w, height: hgt});
const p = (key, text) => h('p', {}, t('guide.' + key, text));
const li = (key, text) => h('li', {}, t('guide.' + key, text));
const term = (key, name, text) => h('li', {}, h('b', {}, t('guide.' + key + 'Name', name)), ' ', t('guide.' + key, text));

export async function render(main, ctx){
  const sec = (id, ...body) => h('section', {class: 'card guideCard', id: 'guide_' + id},
    h('h2', {}, t('guide.s.' + id, SECTIONS.find(s => s[0] === id)[1])), ...body);
  const go = name => h('button', {class: 'guideGo', onclick: () => ctx.show(name)}, t('guide.go.' + name, {
    home: 'ホームを開く', recommend: 'プレイリストを開く', similar: '似ている人を開く', settings: '設定を開く'}[name]));
  main.append(
    h('div', {class: 'pageHead guideHead'}, h('h1', {}, t('guide.title', '使い方'))),
    h('p', {class: 'mute guideLead'}, t('guide.lead',
      'blnext は、BeatLeader の公開データから「次に遊ぶと総PP（有効PP）が伸びそうな曲」を選んで、Beat Saber のプレイリストにするアプリです。' +
      'ここでは、遊び方の流れと各画面の見方を説明します。')),
    h('nav', {class: 'guideToc', 'aria-label': t('guide.tocLbl', '使い方の目次')},
      SECTIONS.map(([id, name]) => h('button', {dataset: {target: id},
        onclick: () => document.getElementById('guide_' + id).scrollIntoView({block: 'start'})}, t('guide.s.' + id, name)))),

    sec('flow',
      h('ol', {class: 'guideSteps'},
        li('flow1', 'アプリを開きます。前回の取得から時間がたっていれば（初めは 12 時間）、BeatLeader から自動で最新のデータを取ります。上の帯の「更新」でいつでも取り直せます。'),
        li('flow2', '「プレイリスト」でリストを確かめます。難しさ違いのセット（着実・標準・チャレンジングなど）ごとに曲が選ばれています。'),
        li('flow3', '「Beat Saber に書き込む」か「ワンクリックで入れる」（BSManager）でゲームに入れます。全部のセットをまとめて入れることもできます。ゲームを起動する前に入れるのが確実です。'),
        li('flow4', 'ゲームのプレイリスト（Mod の PlaylistManager が要ります）から遊びます。推定はモディファイア無しで遊ぶ前提です。'),
        li('flow5', '遊び終わったらアプリを開き（開いたままなら「更新」を押し）、ホームの「前回のプレイリストの結果」で、伸びた曲と増えた総PP を確かめます。'),
        li('flow6', '次のリストは、プレイリストの画面の「作り直す」で作ります。リストは作った時のまま残すので、データが変わって作り直すと曲が変わる時は、画面で知らせます。')),
      img('rec.webp', t('guide.recAlt', 'プレイリストの画面'), 1156, 640),
      p('flowNote', '遊んだ曲は、伸びたかどうかに関係なく、しばらくリストに入れません（クールダウン）。毎回少しずつ違う曲で遊べます。'),
      h('div', {class: 'btnRow'}, go('recommend'))),

    sec('home',
      img('home-review.webp', t('guide.homeReviewAlt', 'ホームの「前回のプレイリストの結果」'), 1100, 520),
      h('ul', {class: 'guideTerms'},
        term('homeReview', '前回のプレイリストの結果', '前にゲームに入れたリストの曲ごとに、出した時の狙い（推定精度・総PP の見込み）と、今回の結果（精度・曲の PP・総PP）を並べます。セットごとにタブで切り替えます。'),
        term('homeTiles', '国内順位・PP', '「先週から」と「前回の出力から」の変化。上下の人の表と、国内の PP の分布も出します。'),
        term('homeChanges', '前回からの変化', '伸ばした曲（曲の PP と総PP の増え）・抜いた人・抜かれた人。')),
      img('home-changes.webp', t('guide.homeChangesAlt', 'ホームの「前回からの変化」'), 1100, 165),
      h('div', {class: 'guideNote'},
        h('b', {}, t('guide.ppName', '「曲のPP」と「総PP」の違い')),
        p('pp', '総PP は、自己ベストの PP を高い順に並べ、上から ×1・×0.965・×0.965² …と重みを掛けて足した値です（BeatLeader の順位はこれで決まります）。' +
          '自分の上位の曲より PP の低い曲は、更新して曲の PP が増えても、総PP はほとんど増えません。このアプリが見込むのも、総PP の増えです。')),
      h('div', {class: 'btnRow'}, go('home'))),

    sec('recommend',
      h('ul', {class: 'guideTerms'},
        term('recSets', 'セット', 'リストの条件ごとのまとまり（タブ）。初めは難しさ違いの 3 つ（着実・標準・チャレンジング）。ワンセイバー用などを足せます。タブはドラッグで並べ替えられます。'),
        term('recEst', '推定精度', '次に遊んだ日に出せそうな精度。自分の記録と、順位の近い人の記録から求めます。'),
        term('recGain', '総PP', 'この曲で増える総PP の見込み。次に遊んだ日の一番はぶれるので、自己ベストを超える見込みも入れた平均です。推定精度が自己ベストより低くても、超える見込みの分だけ増えます。'),
        term('recClear', 'クリア', 'その日のうちにクリアできる見込み（BeatLeader の統計を公開にすると出ます）。'),
        term('recTags', '印', '「挑戦」は、これまでクリアしてきた曲より難しいか、ノーツの多い曲。「前回なし」は、前にゲームに入れたリストに無かった曲。')),
      img('rec-open.webp', t('guide.recOpenAlt', 'プレイリストの画面で曲を開いた所'), 1122, 405),
      h('ul', {class: 'guideTerms'},
        term('recOpen', '曲を押すと', '推定の内訳・譜面の特徴（NJS・ノーツの多さ・難しさ）・自己ベストの JD を見られます。自分のリプレイや BeatLeader の譜面のページも開けます。'),
        term('recActs', '曲の操作', '「今回は外す」（アプリを閉じるまで）・「入れ替える」（次点の曲と）・「固定（次回にも引き継ぐ）」・「今後出さない」。'),
        term('recCond', '条件', '「難しさ」・★の範囲（下限を 0 にすると、更新した時に総PP が増えやすい★を自動で決めます）・曲数・クールダウンなど。変えるとそのセットだけ作り直します。'),
        term('recOrder', '並び', '見出しを押す・ドラッグで並べ替えると、その順でゲームに入ります。')),
      h('div', {class: 'btnRow'}, go('recommend'))),

    sec('stats',
      p('stats', 'BeatLeader の統計が非公開だと、失敗や途中でやめた挑戦の記録を読めないので、クリアの見込みと、続けて同じ曲を出さない働き（クールダウン）が効きません。公開にするのがおすすめです。'),
      ...stats.howTo(),
      h('p', {class: 'mute small'}, t('stats.effect', 'オンにすると、挑戦の回数や失敗した記録、遊んだ日時をほかの人も見られるようになります。順位や PP は変わりません。いつでも戻せます。')),
      h('div', {class: 'btnRow'}, stats.openButton())),

    sec('similar',
      p('similar', '自分と精度の傾向が似ている人の一覧です。未プレイの曲の推定精度は、似ている人のその曲の結果を使って求めます。共通の曲での差も見られます。'),
      h('div', {class: 'btnRow'}, go('similar'))),

    sec('settings',
      h('ul', {class: 'guideTerms'},
        term('setCommon', '全部のセットで共通', 'プレイヤー・Beat Saber のフォルダ・比較する人の範囲・自動で取る間隔など。'),
        term('setPreset', 'セットごと', 'リストの条件・計算の数値・プレイリストの名前と画像。'),
        term('setScale', '文字の大きさ', '上の帯のスライダーで変えられます。'),
        term('setAbout', 'このアプリについて', '版・更新の確認・不具合の報告。')),
      h('div', {class: 'btnRow'}, go('settings'))),

    sec('trouble',
      h('ul', {class: 'guideTerms'},
        term('trTwice', 'もう一度開いた時', '開いている窓が前に出ます（同じフォルダの blnext は 1 つしか開けません）。'),
        term('trNoList', 'リストができない', 'ランク曲のスコアが無いと作れません。BeatLeader の Mod を入れて、ランク曲を何曲か遊んでから「更新」を押してください。'),
        term('trReport', '不具合の報告', '設定 →「このアプリについて」→「不具合を報告」で GitHub の Issues のページが開きます（GitHub のアカウントが要ります）。' +
          '「ログのフォルダを開く」で開くフォルダのファイルを添えてもらえると、原因を調べやすくなります（ログには BeatLeader に問い合わせた URL＝あなたのプレイヤー ID と、失敗した時はこのアプリのフォルダの場所が入ることがあります）。'),
        term('trData', 'データの場所', '設定と取得したデータは blnext.exe の隣の config・data・logs・exports にあります。消す時はフォルダごと消してください。')))
  );
}
