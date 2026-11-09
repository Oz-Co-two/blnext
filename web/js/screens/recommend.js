// おすすめ画面（中心の画面）: 確認 → 調整 → 出力を1画面で（docs/spec.md の「画面仕様」）。
// 条件を変えるとその場で組み立て直す（データの取り直しはしない）。キーボード: ↑↓ 選択・Alt+↑↓ 動かす・スペース 固定・Delete 外す・Enter 理由
// 一覧の順＝書き出すプレイリストの順。見出しを押す・行の左の持ち手をドラッグ・Alt+↑↓ で並べ替えると、その順を保存する
// （recommend/order。作り直さない）。外す・固定などの操作では手で並べた順を保ち、作り直すと基本の並び（core/estimate/order.py）に戻る
// 上のタブでセット（設定のパターン。presets.js）を切り替える。セットごとに条件・おすすめ・プレイリストが別
// 出力は3通り: Beat Saber に書き込む／BSManager などのワンクリックで入れる／ファイルに保存（どれも振り返りに数える）
// リストは作った時のまま（serve.py の「おすすめ」）。データを取り直して変わる時は上に知らせ、「作り直す」を押した時だけ入れ替える。
// 条件を変える・外す・固定などの操作をした時は、その場で今のデータで作り直す
import { api, native } from '../api.js';
import { t, tf } from '../i18n.js';
import { h, fmt, songCover } from '../util.js';
import { toast } from '../main.js';
import * as refresh from '../refresh.js';
import { choose as chooseBsDir } from '../bsdir.js';
import { P, load as loadPresets, tabs as presetTabs, current as currentPreset, difficultyControl, difficultySetButton,
  inGroup, rebuildDialog } from '../presets.js';
import { coverButton } from '../cover.js';
import { roughText, warnPrivate } from '../stats.js';

let R = null, S = null, sel = null, open = new Set(), main = null, CTX = null;
let sortKey = null, sortDir = 1;   // 最後に見出しで並べ替えた列と向き（今の並びがその順のままの時だけ、見出しに印を出す）
let drag = null;                   // ドラッグで動かしている曲の ID
let orderQ = Promise.resolve(), orderSeq = 0;   // 並びの保存は送った順に1つずつ（続けて動かした時に、前の応答で戻らないように）
let COVER = null;                  // 今のセットのプレイリストの画像（data: URL）
let ONECLICK = undefined;          // ワンクリックを受けるアプリ（'BSManager' など。null＝無い。undefined＝まだ聞いていない）
let jdAsked = new Set();           // リプレイの頭（JD）を読みに行った曲（画面を開くごとに1回だけ。読めなくても繰り返さない）
let jdLoading = false;             // 読みに行っている間

const CLEAR_SHOW = 0.95;   // 一覧の行に「クリア n%」を出すのは、クリアできる見込みがこれより低い曲だけ
// 推定の印（core/estimate/recommend.py の flags）。cleared1・cleared3 の値は recommend.py の CLEAR_DAY_* と、pb の「真ん中」は R4_PB_WEIGHT と同じ。
// beyond・pass・tech・cap は数値を入れて adjustNotes が出す（pass・tech・cap のここの文は数値を出せない時の言い方）
const FLAGS = {
  pb: '推定を前の自己ベストとの真ん中に寄せた（似ている人と混ぜて縮めた分や、ミスの上乗せで高くなりすぎた分を戻すため。あなたの再プレイの記録では、真ん中に寄せた方が当たっていた）',
  cap: '近い★のあなたのほかの曲の一番を、この曲の精度の上限にした（上限より上の精度は取れないものとして見込みを出しています）',
  fc: 'ミスが、同じくらいの pass の曲でのいつもの量より多い（多い分の一部を上乗せ。いつもくらいのミスは次も出るとみて上乗せしない）',
  stall: '伸び悩み（最近挑戦しているのに自己ベストが伸びていない）',
  few: 'あなたの結果が無く、この曲をプレイした似ている人も少ない（確からしさを下げた）',
  nf: 'NF のスコアしか無い（クリアの見込みは未プレイの曲と同じに見ています）',
  pass: 'pass が高い（あなたの結果が無い曲は、pass が高いほど推定を下げています）',
  tech: 'tech が高い（あなたの記録では、tech の高い曲は★の割に精度が低いので、あなたの結果が無い曲は tech が高いほど推定を下げています）',
  cleared1: 'この譜面をクリアした日はまだ1日だけ（初めてクリアした後は次に伸びやすいので、推定のミスを 9% 減らした。精度 89% の曲で +1%、100% に近いほど小さい）',
  cleared3: 'この譜面をクリアした日が3日以上ある（自己ベストは何度も遊んだ中の一番なので、次に遊んだ日の一番は届きにくい。推定のミスを 9% 増やした。精度 89% の曲で −1%）',
};
// BeatSaver のタグのうち、譜面の傾向を表す物（ほかは曲のジャンル）
const STYLE_TAGS = {
  accuracy: '精度', balanced: 'バランス', challenge: 'チャレンジ', 'dance-style': 'ダンス', fitness: 'フィットネス',
  speed: 'スピード', tech: 'テク',
};
// 譜面の特徴の名前（あなたの範囲の説明に使う）
const FEAT_NAMES = {pass: 'pass', tech: 'tech', acc: 'acc', nps: '1秒のノーツ', njs: 'NJS'};
const EXPORT_ERR = {
  'no-beat-saber-dir': 'Beat Saber のフォルダが未設定です。設定画面か「出力先を選ぶ」で選んでください。',
  'beat-saber-dir-missing': '設定した Beat Saber のフォルダが見つかりません。選び直してください。',
  'write-failed': 'プレイリストを書き込めませんでした。フォルダを選び直すか、書き込めるか確かめてください。',
  'nothing-to-export': '出力する曲がありません。',
  'no-oneclick-app': 'ワンクリックを受けるアプリが見つかりません。BSManager の設定でワンクリック（プレイリスト）を有効にしてください。',
  'launch-failed': 'BSManager などのアプリを起動できませんでした。',
};
const exportErr = code => t('rec.exErr.' + code, EXPORT_ERR[code] || tf('rec.exErr', '出力に失敗しました（{e}）', {e: code}));
// プレイリストを作る・出す操作が成功した後の警告（統計が非公開の時。stats.js）。公開を確かめたら、ホームの知らせと同じくすぐ取り直す。
// 利用者の操作の成功の時だけ呼ぶ（条件の変更での自動の作り直し・外す／固定などの一覧の操作・失敗の時は呼ばない）
const warnStats = () => warnPrivate(() => { CTX.status.statsPrivate = false; refresh.start(); });

// 一覧の列。key は並べ替えに使う項目（null は並べ替えない。kind は区分の順）。first は最初に押した時の向き（-1＝大きい順）
const COLS = [
  {key: 'kind', label: '区分', first: 1, i18n: 'rec.th.kind', tip: 'プレイ済み → 新曲 → 未クリア → 挑戦 の順'},
  {key: 'song', label: '曲・難易度 / マッパー', first: 1, i18n: 'rec.th.song'},
  {key: 'stars', label: '★', num: true, first: -1, i18n: 'rec.th.stars',
    tip: '★。下の小さい数字は BeatLeader のクリアの難しさ（pass）。黄色はあなたがクリアしてきた曲の上位5%の難しさ、赤はそのどれよりも難しい'},
  {key: 'duration', label: '長さ', num: true, first: 1, i18n: 'rec.th.len'},
  {key: 'best_acc', label: '自己ベスト', num: true, first: -1, i18n: 'rec.th.best'},
  {key: 'est_acc', label: '推定精度', num: true, first: -1, i18n: 'rec.th.est'},
  {key: 'est_pp', label: '曲のPP', num: true, first: -1, i18n: 'rec.th.pp',
    tip: '「推定」は推定精度の時のこの曲の PP、「今」は今の自己ベストの PP。推定が今より低くても、自己ベストを超える見込みの分だけ総PP は増える'},
  {key: 'gain', label: '総PP', num: true, first: -1, i18n: 'rec.th.gain',
    tip: 'この曲で増える総PP（有効PP）の平均（クリアした時）。次に遊んだ日の一番は推定のまわりでぶれるので、そのぶれの中で自己ベストを超えた時の増えを平均した値。推定が自己ベストより低い曲も、超える見込みの分だけ入る。総PP は曲の PP を高い順に並べ、0.965 倍ずつ重みを下げて合計した値なので、曲の PP の増え方より小さくなる。下の小さい文字の「超える」は自己ベストを超える見込み、「クリア」はその日のうちにクリアできる見込み（95% 以上の曲には出さない）'},
  {key: 'rank_up', label: '国内順位', num: true, first: -1, i18n: 'rec.th.rank',
    tip: 'この曲で増える総PP（平均）の分だけ上がる国内順位（取得した範囲の人で数える）'},
  {key: 'confidence', label: '確からしさ', first: -1, i18n: 'rec.th.conf',
    tip: '推定の確からしさ（0〜1）。並び順の値に掛ける。自分の結果がある曲は 1（伸び悩みの曲は半分）。無い曲は、似ている人がこの曲をプレイしているほど高い'},
  {key: null, label: ''},
];

export async function render(m, ctx){
  main = m; CTX = ctx;
  await loadPresets();
  const preset = P.active;
  const [r, s, c, oc] = await Promise.all([api('recommend', {preset}), api('settings/get', {preset}), api('cover/get', {preset}),
    ONECLICK === undefined ? api('oneclick/info') : null]);
  if(oc) ONECLICK = oc.ok ? oc.app : null;
  S = s.ok ? s.values : null;
  COVER = c.ok ? c.dataUrl : null;
  if(!r.ok || !r.hasData){
    main.append(h('section', {class: 'card empty'},
      h('h2', {}, t('rec.noData', 'まだリストを作れません')),
      h('p', {class: 'mute'}, refresh.running() ? t('rec.loading', 'データを取得しています。終わると自動で表示します。')
        : t('rec.noDataHint', 'プレイヤーを設定し、データを取得してください。'))));
    R = null;
    return;
  }
  R = r;
  jdAsked = new Set();
  draw();
}

function draw(){
  const keep = main.scrollTop;
  const repick = () => { sel = null; open.clear(); main.replaceChildren(); render(main, CTX); };
  main.replaceChildren(h('div', {class: 'recPage'},
    presetTabs(repick, [
      // 難しさ違いのセットを一度に作る（遊び比べる用。presets.js）
      difficultySetButton((id, n) => {
        repick();
        toast(tf('rec.dsDone', '難しさ違いのセットを {n} つ作りました。「全部のセットを書き込む」でまとめてゲームに入れられます。', {n}), 'ok');
        warnStats();
      }),
      // 組のセット（ほかのセットと同じ曲を入れないセットどうし）を、知らせが出ていない時でも交互に選び直せるように
      inGroup(S && S.exclude_other_sets) ? h('button', {class: 'small groupBtn', onclick: () => rebuildDialog(doRebuild),
        title: t('rec.groupTip', '組のセット（ほかのセットと同じ曲を入れないセットどうし）のリストを、易しいセットから1曲ずつ交互に選び直します')},
        t('rec.groupBtn', '組を交互に選び直す')) : null,
      P.list.length > 1 ? h('button', {class: 'small', onclick: exportAll, title: t('rec.allTip', '全部のセットのプレイリストを、それぞれのファイル名で Beat Saber のフォルダに書き込みます')},
        t('rec.exportAll', '全部のセットを書き込む')) : null,
      P.list.length > 1 && ONECLICK ? h('button', {class: 'small', onclick: oneClickAll,
        title: tf('rec.ocAllTip', '全部のセットのプレイリストを {app} に1つずつ渡して入れてもらいます。足りない曲も {app} がその場でダウンロードします', {app: appName(ONECLICK)})},
        ONECLICK === 'other' ? t('rec.ocAll', '全部のセットをワンクリックで入れる') : tf('rec.ocAllApp', '全部のセットを {app} で入れる', {app: ONECLICK})) : null]),
    h('div', {class: 'recLayout'}, h('div', {class: 'recMain'}, pendingBar(), summary(), list(), runners()), conditions())));
  watchCond();
  main.scrollTop = keep;
  loadReplays();
}

// 条件の欄（一覧の右に置く時）は、欄の中ではスクロールさせず、全体のスクロール1つにする（2026-11-09 の利用者の要望。文字を大きく
// すると欄がウィンドウより高くなり、欄の中と全体の二重のスクロールになっていた）。上に貼り付く位置（top）を「本文の見える高さ −
// 欄の高さ」（0 より下へは動かさない）にして、欄の下の端が見えるまでは一覧と一緒にスクロールし、その先は欄の下の端を画面の下に
// そろえて貼り付ける（一番下の「外した曲を戻す」に届き、長い一覧を見ている間も条件が見える）。一覧の上に置く狭い時は position:static
const condObs = typeof ResizeObserver === 'function' ? new ResizeObserver(() => fitCond()) : null;
const COND_GAP = 12;   // 欄の下の端と画面の下の間

function watchCond(){
  if(!condObs) return;
  condObs.disconnect();
  const el = main.querySelector('.recCond');
  if(!el) return;
  condObs.observe(el);
  condObs.observe(main);
  fitCond();
}

function fitCond(){
  const el = main && main.querySelector('.recCond');
  if(!el) return;
  // 貼り付く位置は本文の上の余白の内側から測られる（Chromium）ので、見える高さから上の余白を引く
  const padTop = parseFloat(getComputedStyle(main).paddingTop) || 0;
  el.style.top = Math.min(0, main.clientHeight - padTop - el.offsetHeight - COND_GAP) + 'px';
}

async function reloadRec(){
  const r = await api('recommend', {preset: P.active});
  if(r.ok && r.hasData){ R = r; draw(); }
}

// 自己ベストの時の JD は、リプレイのファイルの頭を読んで出す（まだ読んでいない曲がある時だけ。数秒かかる）
async function loadReplays(){
  const wait = [...R.items, ...R.runnersUp].filter(c => c.jd_state === 'wait' && !jdAsked.has(c.leaderboard_id));
  if(!wait.length) return;
  wait.forEach(c => jdAsked.add(c.leaderboard_id));
  const preset = P.active;
  jdLoading = true;
  let r;
  try { r = await api('recommend/replays', {preset}); } finally { jdLoading = false; }
  if(preset !== P.active || document.body.dataset.screen !== 'recommend') return;   // 待つ間に画面やセットを変えた
  if(r.ok && r.hasData) R = r;
  draw();   // 読めなかった曲は「読み込めませんでした」にする
}

// データを取り直した後、今のデータで作り直すとリストが変わる時の知らせ（押した時だけ作り直す）
function pendingBar(){
  const p = R.pending;
  if(!p) return null;
  const names = xs => xs.map(x => x.song + (x.difficulty ? ' ' + diffName(x.difficulty) : '')).join('・');
  const newer = p.dataAt > (R.dataAt || 0);
  const head = newer ? h('span', {class: 'when'}, tf('rec.pendNew', '新しいデータ（{at} に取得）があります。', {at: when(p.dataAt)})) : '';
  const n = Math.max(p.in.length, p.out.length);
  return h('section', {class: 'card pendBar'},
    h('div', {class: 'pendText'},
      h('div', {}, head, p.same
        ? t('rec.pendSame', '作り直しても曲は同じです（推定の数値が少し変わります）。')
        : tf('rec.pendN', '今のデータで作り直すと {n} 曲入れ替わります。', {n})),
      !p.same && p.in.length ? h('div', {class: 'small pendSongs', title: names(p.in)}, tf('rec.pendIn', '入る: {s}', {s: names(p.in)})) : null,
      !p.same && p.out.length ? h('div', {class: 'small pendSongs', title: names(p.out)}, tf('rec.pendOut', '外れる: {s}', {s: names(p.out)})) : null,
      h('div', {class: 'mute small'}, t('rec.pendHint', '押すまでは、作った時のリスト（理由・数値も作った時の物）のままです。条件を変える・外す・固定などをした時も、今のデータで作り直します。'))),
    h('button', {class: p.same ? '' : 'primary', onclick: rebuild}, t('rec.rebuild', '作り直す')));
}

// 組のセットでは、組全部を交互に選び直すか、このセットだけ作り直すかを小さな画面で選ぶ（presets.js の rebuildDialog）
async function rebuild(){
  if(S && S.exclude_other_sets) await loadPresets();   // 組のセット（P.group）をほかの画面で変えたかもしれない
  if(inGroup(S && S.exclude_other_sets)) rebuildDialog(doRebuild);
  else doRebuild(false);
}

async function doRebuild(group){
  const r = await api('recommend/rebuild', {preset: P.active, group});
  if(!r.ok || !r.hasData){ toast(t('rec.actFail', '操作に失敗しました'), 'err'); return; }
  R = r;
  sel = null;
  draw();
  const names = (r.group || []).map(id => (P.list.find(p => p.id === id) || {}).name).filter(Boolean).join('・');
  toast(group ? tf('rec.rebuiltGroup', '組のセット（{names}）を、今のデータで1曲ずつ交互に選び直しました。', {names})
    : t('rec.rebuilt', '今のデータでリストを作り直しました。'), 'ok');
  warnStats();
}

// 時刻の表示（月/日 時:分）と、日付＋何日前
function when(sec){
  return new Date(sec * 1000).toLocaleString('ja-JP', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function daysAgo(sec){
  const d = Math.floor((Date.now() / 1000 - sec) / 86400);
  return d <= 0 ? t('rec.today', '今日') : tf('rec.daysAgo', '{n}日前', {n: d});
}
function dateOnly(sec){
  return new Date(sec * 1000).toLocaleDateString('ja-JP', {year: 'numeric', month: 'numeric', day: 'numeric'});
}
function dateDays(sec){ return dateOnly(sec) + '（' + daysAgo(sec) + '）'; }

function pct(v, d = 2){ return v == null ? '—' : fmt(100 * v, d) + '%'; }
function diffName(s){ return ({ExpertPlus: 'Expert+'})[s] || s || ''; }
function mmss(sec){ return sec ? Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0') : '—'; }

function summary(){
  const sm = R.summary;
  const tgt = S ? S.beat_saber_dir : '';
  const none = !R.items.length;
  const ocLabel = ONECLICK && ONECLICK !== 'other' ? tf('rec.oneclickApp', '{app} で入れる', {app: ONECLICK}) : t('rec.oneclick', 'ワンクリックで入れる');
  return h('section', {class: 'card recSum'},
    h('div', {class: 'sumHead'},
      // 押すと画像を選ぶ小さな画面（cover.js。色違い・ファイルから読み込む）。変えたらその場で絵を差し替える
      coverButton({src: COVER, preset: P.active, name: (currentPreset() || {}).name || '', onChange: url => { COVER = url; }}),
      h('div', {class: 'sumTitle'},
        h('div', {class: 'plTitle', title: S ? S.playlist_title : ''}, S ? S.playlist_title : ''),
        h('div', {class: 'mute small'}, counts()),
        R.short ? h('div', {class: 'warnText small'}, tf('rec.short', '候補が足りないため {n} 曲少なくなっています', {n: R.short})) : null,
        listState()),
      h('div', {class: 'sumMain'},
        // 推定の幅（2026-10-28）より前の版で作ったリスト（曲に gain_up が無い）のまとめは「全部取れた場合」の値
        oldSummary() ? h('div', {class: 'mute small'}, t('rec.ifAll', '全部取れた場合の総PP（有効PP）'))
          : h('div', {class: 'mute small', title: t('rec.expectTip', 'このリストで増える総PP の見込み: 曲ごとの「クリアできる見込み × 増える総PP の平均」の合計')},
            t('rec.expect', '増える総PP（有効PP）の見込み')),
        h('div', {class: 'sumBig'}, '+' + fmt(sm.gain, 2), h('span', {class: 'unitBig'}, ' PP')),
        h('div', {class: 'mute'}, tf('rec.rankTo', '国内 #{a} → #{b}', {a: fmt(sm.rank), b: fmt(sm.newRank)}),
          sm.passed ? ' ' + tf('rec.passN', '（{n}人を抜く）', {n: sm.passed}) : ''),
        roughText(R) ? h('div', {class: 'mute small recRough'}, roughText(R)) : null)),
    h('div', {class: 'outRow'},
      h('div', {class: 'outBox'},
        h('button', {class: 'primary', onclick: doExport, disabled: none,
          title: t('rec.writeTip', 'Beat Saber のフォルダの Playlists に書き込みます（毎回同じファイルを上書き）')}, t('rec.write', 'Beat Saber に書き込む')),
        h('div', {class: 'pathRow'},
          h('span', {class: 'mute small path', title: tgt ? tgt + '\\Playlists\\' + (S ? S.output_filename : '') : ''},
            tgt ? tf('rec.to', '出力先: {p}\\Playlists', {p: tgt}) : t('rec.noDir', '出力先が未設定')),
          h('button', {class: 'small linkBtn', onclick: () => pickDir()}, tgt ? t('rec.changeDir', '変える') : t('rec.pickDir', '選ぶ')))),
      h('div', {class: 'outBox'},
        h('button', {class: 'primary', onclick: doOneClick, disabled: none || ONECLICK === null,
          title: t('rec.oneclickTip', 'BeatSaver などのサイトの「One-Click」と同じ方法で、BSManager などのアプリを起動してプレイリストを入れてもらいます。足りない曲もアプリがダウンロードします')}, ocLabel),
        h('div', {class: 'mute small'}, ONECLICK === null
          ? t('rec.oneclickNone', 'BSManager の設定でワンクリックを有効にすると使えます')
          : t('rec.oneclickHint', 'アプリが入っている全部の版に入れます'))),
      h('div', {class: 'outBox'},
        h('button', {onclick: doSaveFile, disabled: none, title: t('rec.fileTip', '.bplist のファイルとして好きな場所に保存します')},
          t('rec.file', 'ファイルに保存…')),
        h('div', {class: 'mute small'}, t('rec.fileHint', '人に渡す・手で入れる時に')))));
}

const oldSummary = () => R.items.length > 0 && R.items.every(x => x.gain_up == null);

// 曲数（プレイ済み・未プレイ。未プレイのうち挑戦したがクリアしていない曲があれば、その数も）
function counts(){
  const xs = R.items, nTried = xs.filter(x => kindOf(x) === 'tried').length;
  const v = {n: xs.length, p: xs.filter(x => x.kind === 'played').length, u: xs.filter(x => x.kind === 'unplayed').length, c: nTried,
    len: mmss(xs.reduce((a, x) => a + (x.duration || 0), 0))};
  return nTried ? tf('rec.countsTried', '{n}曲（プレイ済み {p}・未プレイ {u}（うち未クリア {c}））・合計 {len}', v)
    : tf('rec.counts', '{n}曲（プレイ済み {p}・未プレイ {u}）・合計 {len}', v);
}

const METHOD = {write: 'Beat Saber に書き込み', oneclick: 'ワンクリック', file: 'ファイルに保存'};
// いつ作ったリストか（元にしたデータの取得の時刻も）と、前回の出力（Beat Saber に入っている物）と同じか
function listState(){
  const made = h('div', {class: 'mute small listAt'},
    h('span', {class: 'when'}, tf('rec.madeAt', 'このリストは {m} に作成（{d} に取得したデータから）', {
      m: R.madeAt ? when(R.madeAt) : '—', d: R.dataAt ? when(R.dataAt) : '—'})));
  const e = R.lastExport;
  if(!e) return [made, h('div', {class: 'mute small'}, t('rec.notExported', 'まだ出力していません'))];
  const how = e.method === 'oneclick' && e.app && e.app !== 'other' ? e.app : t('rec.method.' + e.method, METHOD[e.method] || e.method);
  const at = h('span', {class: 'when'}, tf('rec.expAt', '前回の出力（{at}・{how}）', {at: when(e.at), how}));
  const added = R.items.filter(x => x.in_export === false).length;
  if(!added && !e.dropped.length)
    return [made, h('div', {class: 'okText small'}, '✓ ', at, t('rec.expSame', 'と同じ曲です'))];
  const names = e.dropped.map(x => x.song + (x.difficulty ? ' ' + diffName(x.difficulty) : '')).join('・');
  return [made,
    h('div', {class: 'warnText small'}, at, tf('rec.expDiff', 'から {n} 曲入れ替わっています（「前回なし」の印の曲）', {n: Math.max(added, e.dropped.length)})),
    e.dropped.length ? h('div', {class: 'mute small dropped', title: names}, tf('rec.expDropped', '前回にあって今は無い曲: {s}', {s: names})) : null];
}

// 一覧の区分: プレイ済み・未クリア（自己ベストは無いが挑戦の記録がある。BeatLeader にはクリアしたプレイしかスコアが残らない）・未プレイ。
// 推定の区分（kind）は未クリアも 'unplayed'（枠の数え方は同じ）
function kindOf(c){ return c.kind === 'unplayed' && c.tried && !c.tried.clears ? 'tried' : c.kind; }
const BADGE = {
  played: ['rec.played', '済', 'rec.playedTip', 'プレイ済み'],
  // 未クリアは「未／クリア」の2行（base.css の .badge.tried）
  tried: ['rec.tried', '未\nクリア', 'rec.triedTip', '挑戦したがクリアしていない（BeatLeader にはクリアしたプレイしかスコアが残らない）'],
  unplayed: ['rec.unplayed', '新', 'rec.unplayedTip', '未プレイ'],
};
function badge(c){
  const k = kindOf(c), [key, text, tipKey, tip] = BADGE[k] || BADGE.unplayed;
  return h('span', {class: 'badge ' + k, title: t(tipKey, tip)}, t(key, text));
}

function confBar(c){
  return h('span', {class: 'conf', title: tf('rec.confTip', '確からしさ {v}', {v: fmt(c, 2)})},
    h('span', {class: 'confFill', style: `width:${Math.round(100 * c)}%`}));
}

// 一覧に出ている並び（＝書き出すプレイリストの順。保存したリストの順そのまま）
function viewItems(){ return R.items; }
const idsOf = xs => xs.map(x => x.leaderboard_id);

// 区分（core/estimate/order.py の group_of と同じ。基本の並びは区分をこの順に並べ、区分の中はクリアの見込みの高い順）
const GROUPS = ['played', 'new', 'tried', 'challenge'];
const GROUP_NAMES = {played: 'プレイ済み', new: '新曲', tried: '未クリア', challenge: '挑戦'};
function groupOf(c){ return c.challenge ? 'challenge' : c.kind === 'played' ? 'played' : c.tried ? 'tried' : 'new'; }

// 見出しで並べ替えた時の曲の ID の並び。値の無い物は向きによらず最後。同じ値の曲は今の並びのまま
function sortedIds(key, dir){
  const val = c => key === 'song' ? (c.song || '').toLowerCase() : key === 'kind' ? GROUPS.indexOf(groupOf(c)) : c[key];
  return idsOf([...R.items].sort((a, b) => {
    const x = val(a), y = val(b);
    if(x == null && y == null) return 0;
    if(x == null) return 1;
    if(y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  }));
}

// 並びを保存する（作り直さない）。ids＝曲の ID の並び。null なら基本の並びに戻す。動かした時は先に画面を変えてから送る
async function setOrder(ids){
  const preset = P.active, seq = ++orderSeq;
  if(ids){
    const by = new Map(R.items.map(x => [x.leaderboard_id, x]));
    R = {...R, items: ids.map(i => by.get(i)), manualOrder: true};
    draw(); focusList();
  }
  const r = await (orderQ = orderQ.then(() => api('recommend/order', ids ? {preset, ids} : {preset, default: true})));
  if(seq !== orderSeq || preset !== P.active || document.body.dataset.screen !== 'recommend') return;   // 後から動かした・画面を変えた
  if(!r.ok || !r.hasData){ toast(t('rec.orderFail', '並べ替えられませんでした'), 'err'); await reloadRec(); return; }
  R = r;
  draw(); focusList();
}

function headRow(sortable){
  // 印を出すのは、今の並びが最後に押した見出しの順のままの時だけ（その後に動かした・作り直した時は出さない）
  const marked = sortable && sortKey && sortedIds(sortKey, sortDir).join('\n') === idsOf(R.items).join('\n') ? sortKey : null;
  return h('div', {class: 'recRow head'}, COLS.map(c => {
    const label = c.i18n ? t(c.i18n, c.label) : c.label;
    const tip = c.tip ? t(c.i18n + 'Tip', c.tip) : null;
    if(!sortable || !c.key || !label) return h('span', {class: c.num ? 'num' : '', title: tip}, label);
    const on = marked === c.key;
    return h('button', {class: 'th' + (c.num ? ' num' : '') + (on ? ' on' : ''),
      title: (tip ? tip + '\n' : '') + t('rec.sortTip', '押すとこの列の順に並べ替えます（その順でプレイリストに書き出します。もう一度押すと逆の順）'),
      onclick: () => {
        sortDir = on ? -sortDir : c.first;
        sortKey = c.key;
        setOrder(sortedIds(sortKey, sortDir));
      }}, label, on ? h('span', {class: 'sortMark'}, sortDir > 0 ? '▲' : '▼') : null);
  }));
}

function list(){
  const items = viewItems();
  const allOpen = items.length > 0 && items.every(x => open.has(x.leaderboard_id));
  return h('section', {class: 'card recList', tabindex: '0', onkeydown: onKey, ondragenter: dragOver, ondragover: dragOver, ondrop: drop},
    h('div', {class: 'cardHead listHead'},
      h('div', {class: 'listTitle'}, h('h2', {}, t('rec.list', '次回のプレイリスト')),
        items.length ? h('button', {class: 'small openAll', onclick: () => {
          if(allOpen) open.clear(); else items.forEach(x => open.add(x.leaderboard_id));
          draw(); focusList();
        }}, allOpen ? t('rec.closeAll', '全部閉じる') : t('rec.openAll', '全部開く')) : null),
      h('span', {class: 'mute small'}, t('rec.keys2', '↑↓ 選ぶ・Alt+↑↓ 動かす・スペース 固定・Delete 外す・クリックで理由'))),
    items.length > 1 ? h('div', {class: 'orderBar'},
      h('button', {class: 'small orderReset', disabled: !R.manualOrder, onclick: () => setOrder(null),
        title: t('rec.orderResetTip', 'プレイ済み → 新曲 → 未クリア → 挑戦 の順（区分の中はクリアの見込みの高い順）に戻します')},
        t('rec.orderReset', '基本の並びに戻す')),
      h('span', {class: 'mute small'}, t('rec.orderHint',
        'この順でプレイリストに書き出します。見出しを押す・行の左の ⋮⋮ をドラッグする・Alt+↑↓ で並べ替えられます。'))) : null,
    headRow(true), sections(items));
}

// 一覧の行。基本の並びのままの時は、区分の切れ目に区分の名前を出す（手で並べた時は区分が混ざるので出さない）
function sections(items){
  if(R.manualOrder) return items.map(c => row(c, false));
  const n = {};
  items.forEach(c => { const g = groupOf(c); n[g] = (n[g] || 0) + 1; });
  return items.flatMap((c, i) => {
    const g = groupOf(c), r = row(c, false);
    if(i && groupOf(items[i - 1]) === g) return [r];
    return [h('div', {class: 'recSect' + (i ? '' : ' first')}, t('rec.group.' + g, GROUP_NAMES[g]),
      h('span', {class: 'mute'}, tf('rec.groupN', '{n}曲', {n: n[g]}))), r];
  });
}

// ドラッグで並べ替える（行の左の持ち手でつかむ。開いた欄のある行も、欄ごと動く）。指している行の上半分ならその前、
// 下半分なら後ろに入れ、その位置に線を出す
function dragStart(e, id){
  drag = id;
  const item = e.currentTarget.closest('.recItem');
  item.classList.add('dragging');
  if(e.dataTransfer){
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
    e.dataTransfer.setDragImage(item, 24, 18);
  }
}
function dropAt(e){
  const item = e.target instanceof Element ? e.target.closest('.recItem') : null;
  if(!item) return null;
  const r = item.getBoundingClientRect();
  return {item, after: e.clientY > r.top + r.height / 2};
}
function clearDrop(){ main.querySelectorAll('.dropBefore, .dropAfter').forEach(x => x.classList.remove('dropBefore', 'dropAfter')); }
function dragOver(e){
  const p = drag && dropAt(e);
  if(!p) return;
  e.preventDefault();
  if(e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  const cls = p.item.dataset.item === drag ? null : p.after ? 'dropAfter' : 'dropBefore';
  if(cls && p.item.classList.contains(cls)) return;
  clearDrop();
  if(cls) p.item.classList.add(cls);
}
function drop(e){
  const id = drag, p = id && dropAt(e);
  if(!p) return;
  e.preventDefault();
  dragEnd();
  const now = idsOf(R.items), ids = now.filter(x => x !== id);
  const i = ids.indexOf(p.item.dataset.item);
  if(i < 0) return;   // 自分の上に落とした
  ids.splice(i + (p.after ? 1 : 0), 0, id);
  if(ids.join('\n') !== now.join('\n')) setOrder(ids);
}
function dragEnd(){
  drag = null;
  clearDrop();
  main.querySelectorAll('.recItem.dragging').forEach(x => x.classList.remove('dragging'));
}

function row(c, runner){
  const id = c.leaderboard_id;
  const r = h('div', {class: 'recRow' + (sel === id ? ' sel' : '') + (c.pinned ? ' pinned' : ''), dataset: {id},
    onclick: e => { if(e.target.closest('button')) return; sel = id; if(open.has(id)) open.delete(id); else open.add(id); draw(); }},
    badge(c),
    // 曲名の左に曲のカバー画像（どの曲か思い出せるように。util.js の songCover）。右の文字は 印の行（無ければ出さない）・
    // 曲名と難易度の行・譜面の特徴の行
    h('span', {class: 'song'}, songCover(c.hash), h('span', {class: 'songText'},
      tagLine(c),
      h('span', {class: 'line1'}, h('span', {class: 'songName'}, c.song || id), ' ', h('span', {class: 'mute'}, diffName(c.difficulty)),
        c.mode !== 'Standard' ? h('span', {class: 'mute'}, ' ' + c.mode) : null),
      h('span', {class: 'line2 mute'}, featLine(c)))),
    h('span', {class: 'num two'}, h('span', {}, fmt(c.stars, 2)),
      c.pass_rating != null ? feat('pass', c.pass_rating, 'pass ' + fmt(c.pass_rating, 1), 'small') : null),
    h('span', {class: 'num'}, mmss(c.duration)),
    h('span', {class: 'num two', title: c.best_time ? tf('rec.bestAtTip', '自己ベストは {d}', {d: dateDays(c.best_time)}) : null},
      h('span', {}, pct(c.best_acc)), c.best_time ? h('span', {class: 'mute small when'}, daysAgo(c.best_time)) : null),
    h('span', {class: 'num strong'}, pct(c.est_acc)),
    h('span', {class: 'num two', title: tf('rec.ppTip', '推定の PP {e}（今の自己ベスト {b}）', {e: fmt(c.est_pp, 2), b: c.best_pp == null ? '—' : fmt(c.best_pp, 2)})},
      h('span', {}, h('span', {class: 'mute small'}, t('rec.ppEst', '推定')), ' ', fmt(c.est_pp, 1)),
      c.best_pp == null ? null : h('span', {class: 'mute small'}, tf('rec.ppNow', '今 {v}', {v: fmt(c.best_pp, 1)}))),
    h('span', {class: 'num two', title: gainTip(c)},
      h('span', {class: 'strong'}, '+' + fmt(c.gain, 2)),
      c.p_up != null ? h('span', {class: 'small mute'}, tf('rec.upShort', '超える {p}%', {p: fmt(100 * c.p_up)})) : null,
      c.clear != null && c.clear < CLEAR_SHOW ? h('span', {class: 'small ' + (c.clear < 0.5 ? 'clearLow' : 'mute')},
        tf('rec.clearShort', 'クリア {p}%', {p: fmt(100 * c.clear)})) : null),
    h('span', {class: 'num'}, c.rank_up ? tf('rec.rankUp', '+{n}位', {n: c.rank_up}) : '—'),
    confBar(c.confidence),
    runner
      ? h('span', {class: 'acts'}, h('button', {class: 'small', onclick: () => swapIn(id)}, t('rec.swapIn', '入れ替える')))
      : h('span', {class: 'acts'},
        h('button', {class: 'icon' + (c.pinned ? ' on' : ''), title: c.pinned ? t('rec.unpin', '固定をやめる') : t('rec.pin', '固定（次回にも引き継ぐ）'),
          onclick: () => act(c.pinned ? 'unpin' : 'pin', id)}, '📌'),
        h('button', {class: 'icon', title: t('rec.remove', '今回は外す'), onclick: () => act('remove', id)}, '✕'),
        h('button', {class: 'icon', title: t('rec.never', '今後出さない'), onclick: () => act('never', id)}, '🚫')));
  const body = open.has(id) ? [r, reasons(c)] : [r];
  if(runner) return body.length > 1 ? h('div', {class: 'recGroup'}, ...body) : r;
  // 一覧の行は、持ち手（行の左。.recRow の格子の外）と行と開いた欄をまとめて1つ（ドラッグで一緒に動く）
  return h('div', {class: 'recItem' + (body.length > 1 ? ' recGroup' : ''), dataset: {item: id}},
    h('span', {class: 'grip', draggable: true, title: t('rec.gripTip', 'つかんで上下に動かすと並べ替えます（この順でプレイリストに書き出します）'),
      ondragstart: e => dragStart(e, id), ondragend: dragEnd}, '⋮⋮'),
    ...body);
}

// 一覧の「総PP」の説明: 自己ベストを超える見込みと超えた時の増え（推定の幅。recommend.py）・クリアできる見込み
function gainTip(c){
  const xs = [];
  if(c.p_up != null) xs.push(tf('rec.upTip', '自己ベストを超える見込み {p}%（超えた時は総PP が平均 +{g}）', {p: fmt(100 * c.p_up), g: fmt(c.gain_up, 2)}));
  if(c.clear != null) xs.push(tf('rec.clearTip', 'その日のうちにクリアできる見込み {p}%（並び順はこれを掛けて決めます）', {p: fmt(100 * c.clear)}));
  return xs.length ? xs.join('\n') : null;
}

// 譜面の特徴の値の印。あなたがクリアしてきた曲（ランクのスコア・NF を除く）の上位5%に入る値は hi、どれよりも上なら over
function level(key, v){
  const r = R.range && R.range[key];
  if(!r || v == null) return '';
  return v > r.max ? 'over' : v > r.p95 ? 'hi' : '';
}
function feat(key, v, text, cls = ''){
  const lv = level(key, v), r = R.range && R.range[key];
  const tip = lv === 'over' ? tf('rec.featOver', 'あなたがクリアしてきた曲のどれよりも上です（今までの最大 {m}）', {m: fmt(r.max, 2)})
    : lv === 'hi' ? tf('rec.featHi', 'あなたがクリアしてきた曲の上位5%に入ります（95%は {p} 以下）', {p: fmt(r.p95, 2)}) : null;
  return h('span', {class: ['feat', lv, cls].filter(Boolean).join(' '), title: tip}, text);
}
// 曲名の上の行の印（挑戦枠・前回の出力に無い曲）。どちらも無い曲は行を出さない（null）。2026-11-09 までは曲名の後ろに続けていて、
// 長い曲名だと印が曲名と一緒に「…」で切られて見えなかった（利用者の指示で曲名の上の行へ移した）
function tagLine(c){
  const xs = [
    c.challenge ? h('span', {class: 'chTag', title: t('rec.challengeTip', '挑戦枠: あなたがクリアしてきた曲より難しいか、ノーツが多い曲')},
      t('rec.challenge', '挑戦')) : null,
    c.in_export === false ? h('span', {class: 'prevTag', title: t('rec.notInExportTip', '前回出力したリスト（Beat Saber に入っている物）には無い曲')},
      t('rec.notInExport', '前回なし')) : null,
  ].filter(Boolean);
  return xs.length ? h('span', {class: 'tagLine'}, xs) : null;
}

const njsText = v => 'NJS ' + fmt(v, v % 1 ? 1 : 0);
// 曲名の下の行: NJS・BPM・ノーツ数・1秒あたりのノーツ数・マッパー（「これは無理」を一覧で見分けられるように）
function featLine(c){
  const xs = [
    c.njs != null ? feat('njs', c.njs, njsText(c.njs)) : null,
    c.bpm != null ? h('span', {}, 'BPM ' + fmt(c.bpm, 0)) : null,
    c.notes != null ? h('span', {}, tf('rec.notes', '{n}ノーツ', {n: fmt(c.notes)})) : null,
    c.nps != null ? feat('nps', c.nps, tf('rec.nps', '{v}/秒', {v: fmt(c.nps, 1)})) : null,
    c.mapper ? h('span', {}, c.mapper) : null,
  ].filter(Boolean);
  return xs.flatMap((x, i) => i ? [' · ', x] : [x]);
}

// 開いた欄の「譜面」: BeatLeader の評価・譜面の数値・BeatSaver のタグと、BeatLeader のページを開くボタン
function mapInfo(c){
  const sep = xs => xs.filter(Boolean).flatMap((x, i) => i ? [' · ', x] : [x]);
  const tags = (c.tags || '').split(',').filter(Boolean);
  const style = tags.filter(x => STYLE_TAGS[x]).map(x => t('rec.tag.' + x, STYLE_TAGS[x]));
  const genre = tags.filter(x => !STYLE_TAGS[x]);
  const rg = R.range || {};
  const yours = ['pass', 'tech', 'nps'].filter(k => rg[k]).map(k =>
    tf('rec.rangeOne', '{n} は {p} 以下（最大 {m}）', {n: t('rec.feat.' + k, FEAT_NAMES[k]), p: fmt(rg[k].p95, k === 'nps' ? 1 : 2),
      m: fmt(rg[k].max, k === 'nps' ? 1 : 2)}));
  const rate = (key, v) => v == null ? null : feat(key, v, key + ' ' + fmt(v, 2));
  return h('div', {class: 'mapInfo'},
    h('div', {}, h('span', {class: 'mute'}, t('rec.blRating', 'BeatLeader の評価') + ': '),
      sep([rate('pass', c.pass_rating), rate('acc', c.acc_rating), rate('tech', c.tech_rating)])),
    h('div', {}, h('span', {class: 'mute'}, t('rec.mapNums', '譜面') + ': '), sep([
      c.njs != null ? feat('njs', c.njs, njsText(c.njs)) : null,
      c.bpm != null ? 'BPM ' + fmt(c.bpm, 0) : null,
      c.notes != null ? tf('rec.notesFull', 'ノーツ {n}', {n: fmt(c.notes)}) : null,
      c.nps != null ? feat('nps', c.nps, tf('rec.npsFull', '1秒に {v}', {v: fmt(c.nps, 1)})) : null,
      c.bombs ? tf('rec.bombs', '爆弾 {n}', {n: fmt(c.bombs)}) : null,
      c.walls ? tf('rec.walls', '壁 {n}', {n: fmt(c.walls)}) : null,
      c.peak_ebpm ? h('span', {title: t('rec.peakTip', '一番速い所が続く間の振りの速さ（BeatLeader の Peak sustained EBPM）')},
        tf('rec.peak', '一番速い所 {v} EBPM', {v: fmt(c.peak_ebpm, 0)})) : null])),
    style.length || genre.length ? h('div', {}, h('span', {class: 'mute'}, t('rec.tags', '傾向') + ': '),
      style.join('・'), genre.length ? h('span', {class: 'mute'}, (style.length ? '（' : '') + genre.join(', ') + (style.length ? '）' : '')) : null) : null,
    yours.length ? h('div', {class: 'mute small'}, tf('rec.yourRange', 'あなたがクリアしてきた曲の95%: {v}', {v: yours.join('・')})) : null,
    h('div', {class: 'btnRow'},
      c.replay ? h('button', {class: 'small', onclick: () => openBL(c.leaderboard_id, 'replay')}, t('rec.openReplay', '自分のリプレイを見る')) : null,
      h('button', {class: 'small', onclick: () => openBL(c.leaderboard_id, 'leaderboard')},
        t('rec.openLb', 'BeatLeader でこの譜面のランキングを見る')),
      c.kind === 'played' && !c.replay ? h('span', {class: 'mute small'}, t('rec.replayLater', 'リプレイのボタンは次の更新の後に出ます')) : null));
}

const OPEN_ERR = {
  'no-replay': 'この曲の自分のスコアの ID がまだありません。更新してからもう一度試してください。',
  'open-failed': 'ブラウザを開けませんでした。',
};
async function openBL(id, what){
  const r = await api('open/beatleader', {id, what});
  if(r.ok) toast(what === 'replay' ? t('rec.openedReplay', 'ブラウザで自分のリプレイを開きました')
    : t('rec.openedLb', 'ブラウザで BeatLeader のこの譜面のページを開きました'), 'ok');
  else toast(t('rec.openErr.' + r.error, OPEN_ERR[r.error] || t('rec.openErr', 'ページを開けませんでした')), 'err');
}

// 自分の曲線を読む時に、この曲のモードの★に掛ける倍率（model.py の star_scales。ワンセイバーなど。無ければ 1）
function starScale(c){
  return ((R.calc || {}).starScale || {})[c.mode || ''] || 1;
}

// 2026-10 に足した調整の説明（★の範囲より上の下げ方・pass と tech の高い未プレイの曲の下げ方・クリアの見込み・挑戦枠）
function adjustNotes(c){
  const k = R.calc || {};
  // ★の範囲の上端と下げ方は換算した★の値なので、この曲のモードの★に直して出す
  const sc = starScale(c);
  const fl = c.flags || [];
  const cleared = c.kind === 'played' && !fl.includes('nf');
  // pass による下げ（recommend.py の unplayed_pass_drop と同じ式。説明のためだけに求める）: 設定の値 × (pass − 基準)、★の範囲より上は2倍。
  // 基準はクリアしてきた曲の pass の75%点（リストを作った時の range）。分からなければ 3.0
  const p75 = ((R.range || {}).pass || {}).p75;
  const p0 = p75 != null ? p75 : 3.0, twice = k.starHi != null && c.stars * sc > k.starHi;
  const passDrop = S && S.unplayed_pass_drop != null && c.pass_rating != null ? S.unplayed_pass_drop * (c.pass_rating - p0) * (twice ? 2 : 1) : null;
  // tech による下げ（recommend.py の unplayed_tech_drop と同じ式）: 設定の値 × (tech − 基準)。基準はクリアしてきた曲の tech の中央
  // （リストを作った時の range。無ければ数値を出さない）。★の範囲より上でも2倍にはしない
  const t50 = ((R.range || {}).tech || {}).p50;
  const techDrop = S && S.unplayed_tech_drop != null && c.tech_rating != null && t50 != null ? S.unplayed_tech_drop * (c.tech_rating - t50) : null;
  return [
    fl.includes('beyond') ? h('div', {class: 'flag'}, '• ', tf('rec.flag.beyond',
      'あなたの★の範囲（★{hi} まで）より上なので、★1 につき {d}% 下げて推定しています（比較対象の人の曲線の下がり方）', {
        hi: fmt(k.starHi / sc, 2), d: fmt(100 * (k.drop || 0) * sc, 2)})) : null,
    fl.includes('pass') ? h('div', {class: 'flag'}, '• ', passDrop == null ? t('rec.flag.pass', FLAGS.pass) : tf('rec.flag.passDrop',
      'pass {x} が{base}より高いので、{d}% 下げて推定しています（あなたの結果が無い曲だけ。pass の高い曲は★の割に精度が下がりやすく、似ている人の結果には出にくいため{w}）', {
        x: fmt(c.pass_rating, 2), d: fmt(passDrop, 2),
        base: p75 != null ? tf('rec.passBase', '、あなたがクリアしてきた曲の pass の75%点（{v}）', {v: fmt(p75, 2)}) : tf('rec.passBase0', '基準の {v}', {v: fmt(p0, 1)}),
        w: twice ? t('rec.passTwice', '。★の範囲より上なので2倍') : ''})) : null,
    fl.includes('tech') ? h('div', {class: 'flag'}, '• ', techDrop == null ? t('rec.flag.tech', FLAGS.tech) : tf('rec.flag.techDrop',
      'tech {x} が、あなたがクリアしてきた曲の tech の中央（{v}）より高いので、{d}% 下げて推定しています（あなたの結果が無い曲だけ。あなたの記録では、tech の高い曲は★の割に精度が低く、似ている人の結果には出にくいため）', {
        x: fmt(c.tech_rating, 2), v: fmt(t50, 2), d: fmt(techDrop, 2)})) : null,
    fl.includes('cap') ? h('div', {class: 'flag'}, '• ', capNote(c)) : null,
    c.clear != null ? h('div', {}, tf('rec.clearWhy',
      'その日のうちにクリアできる見込み {p}%（pass {x}。あなたの挑戦の記録 {n} 日分から{b}{f}）。並び順はこの見込みを掛けて決めています。', {
        p: fmt(100 * c.clear), x: fmt(c.pass_rating, 2), n: fmt(k.clearDays), b: cleared ? t('rec.clearCleared', '。前にクリアした曲なので高め') : '',
        f: failNote(c, cleared)})) : null,
    c.challenge ? h('div', {class: 'flag'}, '• ', tf('rec.challengeWhy',
      '挑戦枠: pass か1秒のノーツが、あなたがクリアしてきた曲の95%を超える曲です（1リストに {n} 曲まで。条件の「挑戦枠」）', {
        n: S ? S.challenge_max : '—'})) : null,
  ];
}

// クリアできなかった日が続いているので見込みを下げた時の一言（clear.py。最後にクリアした日の後の、クリアできなかった日）
function failNote(c, cleared){
  const f = c.fails;
  if(!f || !f.days) return '';
  const v = {d: f.days, b: fmt(100 * f.base), s: f.soft ? tf('rec.clearFailsSoft', '（うちやり直し・途中でやめただけの日 {s}）', {s: f.soft}) : ''};
  return cleared ? tf('rec.clearFailsAfter', '。前にクリアした後、クリアできなかった日が {d} 日{s}あるので {b}% から下げた', v)
    : tf('rec.clearFails', '。この譜面でクリアできなかった日が {d} 日{s}あるので {b}% から下げた', v);
}

// 精度の上限（recommend.py・model.py の AccCeiling）: 近い★（同じモードで★ ±window）の、ほかの曲の自分の一番。
// 自己ベストが上限以上なら伸びしろ 0、そうでなければ推定の幅のうち上限より上の割合（over）を出す
function capNote(c){
  const x = c.acc_cap;
  if(!x) return t('rec.flag.cap', FLAGS.cap);
  if(x.window == null){   // 近い★の曲が足りない人の上限（似ている人が足りない人だけ）: 同じモードのほかの曲の一番
    const w = {a: pct(x.top), ref: tf('rec.capRef', '{s} {d} ★{x}', {s: x.song || '—', d: x.difficulty || '', x: fmt(x.stars, 2)}),
      m: x.margin ? tf('rec.capMargin', '＋難しさの余裕 {m}% ＝ {c}', {m: fmt(100 * x.margin, 1), c: pct(x.acc)}) : ''};
    return c.best_acc != null && c.best_acc >= x.acc
      ? tf('rec.capAllAtBest', '自己ベスト {b} が、あなたのほかの曲の一番 {a}（{ref}）{m}以上なので、これより上は取れないものとして、増える総PP を 0 にしています（精度の上限）', {...w, b: pct(c.best_acc)})
      : tf('rec.capAllWhy', '近い★の曲がまだ少ないので、あなたのほかの曲の一番 {a}（{ref}）{m}を、この曲の精度の上限にしています。次に遊んだ日の一番のぶれのうち {o}% が上限より上だったので、そこは上限で取れたものとして見込みを出しています{cut}', {
        ...w, o: fmt(100 * x.over), cut: c.est_acc >= x.acc - 1e-9 ? t('rec.capCut', '（推定精度も上限で頭打ち）') : ''});
  }
  // 上限＝近い★のほかの曲の一番（top）＋難しさの段階の余裕（margin。着実 0・標準 0.5%・チャレンジング 1.0%）
  const v = {lo: fmt(c.stars - x.window, 2), hi: fmt(c.stars + x.window, 2), a: pct(x.top != null ? x.top : x.acc),
    ref: tf('rec.capRef', '{s} {d} ★{x}', {s: x.song || '—', d: x.difficulty || '', x: fmt(x.stars, 2)}), n: fmt(x.n),
    m: x.margin ? tf('rec.capMargin', '＋難しさの余裕 {m}% ＝ {c}', {m: fmt(100 * x.margin, 1), c: pct(x.acc)}) : ''};
  if(c.best_acc != null && c.best_acc >= x.acc){
    return tf('rec.capAtBest', '自己ベスト {b} が、近い★（★{lo}〜★{hi}・{n} 曲）のあなたのほかの曲の一番 {a}（{ref}）{m}以上なので、これより上は取れないものとして、増える総PP を 0 にしています（精度の上限）', {...v, b: pct(c.best_acc)});
  }
  return tf('rec.capWhy', '近い★（★{lo}〜★{hi}・{n} 曲）のあなたのほかの曲の一番 {a}（{ref}）{m}を、この曲の精度の上限にしています。次に遊んだ日の一番のぶれのうち {o}% が上限より上だったので、そこは上限で取れたものとして見込みを出しています{cut}', {
    ...v, o: fmt(100 * x.over), cut: c.est_acc >= x.acc - 1e-9 ? t('rec.capCut', '（推定精度も上限で頭打ち）') : ''});
}

// 自己ベストのプレイ: いつ・精度・ミス・モディファイア・その時の JD と RT（設定を変えてきたので、古い設定の自己ベストか分かるように）・
// 挑戦の回数と最後の挑戦。JD はリプレイの頭から読む（loadReplays）。RT ＝ JD の半分 ÷ ノーツの速さ（NJS）
function jdText(c){
  if(c.jd_state === 'ok'){
    const rt = c.rt != null ? h('span', {title: tf('rec.rtTip', '反応時間（ノーツが出てから届くまで）＝ JD の半分 ÷ NJS {njs}', {njs: fmt(c.njs, 1)})},
      tf('rec.rt', 'RT {v}ms', {v: c.rt})) : null;
    return [tf('rec.jd', 'JD {v}', {v: fmt(c.jd, 1)}), rt ? ' / ' : '', rt];
  }
  const failed = c.jd_state === 'wait' && !jdLoading && jdAsked.has(c.leaderboard_id);
  return h('span', {class: 'mute'}, failed ? t('rec.jdFail', 'JD —（読み込めませんでした。次に開いた時にもう一度読みます）')
    : c.jd_state === 'wait' ? t('rec.jdWait', 'JD 読み込み中…')
    : c.jd_state === 'nourl' ? t('rec.jdNoUrl', 'JD は次の更新の後に出ます')
    : c.jd_state === 'changed' ? t('rec.jdChanged', 'JD —（このリストを作った後に自己ベストが変わりました。作り直すと今の自己ベストの JD を出します）')
    : t('rec.jdNone', 'JD —（リプレイを読めませんでした）'));
}
function bestInfo(c){
  if(c.kind !== 'played') return null;
  const sep = xs => xs.filter(Boolean).flatMap((x, i) => i ? [' · ', x] : [x]);
  return h('div', {class: 'bestInfo'},
    h('div', {}, h('span', {class: 'mute'}, t('rec.best', '自己ベスト') + ': '), sep([
      c.best_time ? h('span', {class: 'when'}, dateDays(c.best_time)) : null,
      h('b', {}, pct(c.best_acc)),
      c.best_fc ? 'FC' : c.best_miss != null ? tf('rec.miss', 'ミス {n}', {n: c.best_miss}) : null,
      c.best_mods ? tf('rec.mods', 'モディファイア {m}', {m: c.best_mods}) : null,
      h('span', {class: 'jd'}, jdText(c))])),
    c.play_count != null || c.last_try ? h('div', {class: 'mute small'}, sep([
      c.play_count != null ? tf('rec.plays', 'この譜面の挑戦 {n} 回', {n: fmt(c.play_count)}) : null,
      c.last_try ? h('span', {class: 'when'}, tf('rec.lastTry', '最後の挑戦 {d}', {d: dateDays(c.last_try)})) : null])) : null);
}

// 自己ベストの無い曲の挑戦の記録（挑戦の回数・クリアしたか・最後の挑戦の日と結果）。練習は数えない
const END = {1: 'クリア', 2: '失敗', 3: 'やり直し', 4: '途中でやめた', 5: '練習'};
function triedInfo(c){
  const x = c.tried;
  if(c.kind !== 'unplayed' || !x || !x.n) return null;
  const end = t('rec.end.' + x.end, END[x.end] || String(x.end));
  return h('div', {class: 'bestInfo'}, h('div', {},
    tf('rec.triedN', '挑戦 {n} 回', {n: fmt(x.n)}),
    x.clears ? tf('rec.triedClears', '（クリア {n} 回）', {n: fmt(x.clears)}) : t('rec.triedNone', '（クリアなし）'),
    ' · ', h('span', {class: 'when'}, tf('rec.triedLast', '最後 {d}', {d: dateOnly(x.at)})),
    x.acc != null ? tf('rec.triedEnd', '（{a} で{e}）', {a: pct(x.acc, 1), e: end}) : '（' + end + '）'));
}

function reasons(c){
  const refs = (c.refs || []).map(x => h('li', {},
    h('span', {class: 'name'}, x.name), ' ', h('span', {class: 'num'}, pct(x.acc)),
    h('span', {class: 'mute num'}, tf('rec.refRes', '（その人の実力より {v}）', {v: (x.residual >= 0 ? '+' : '') + fmt(100 * x.residual, 2) + '%'})),
    x.mods ? h('span', {class: 'mute'}, ' ' + x.mods) : null));
  // 曲線と混ぜた値のほかに直した分（R1 ミスの上乗せ・R2 pass・R3 クリアした日の数・R5 tech・R4 前の自己ベスト・精度の上限。recommend.py）があるか
  const adjusted = (c.flags || []).some(f => ['fc', 'pass', 'cleared1', 'cleared3', 'tech', 'pb', 'cap'].includes(f));
  return h('div', {class: 'reasons'},
    bestInfo(c) || triedInfo(c),
    h('div', {}, tf('rec.why1', '★{s} の時のあなたのいつもの精度は {b}。似ている人 {n} 人の結果{self}を混ぜて{adj} {e} と推定。', {
      s: fmt(c.stars, 2), b: pct(c.base_acc), n: c.similar_n, e: pct(c.est_acc),
      self: c.best_acc != null ? t('rec.withSelf', 'とあなたの自己ベスト') : '',
      adj: adjusted ? t('rec.withAdj', '（下の • の分も直して）') : ''})),
    starScale(c) !== 1 ? h('div', {class: 'mute small'}, tf('rec.starScale',
      '{m} の★は、あなたの記録ではいつもの曲線より精度が出やすいので、×{k} した★{x} で曲線を読んでいます。', {
        m: c.mode, k: fmt(starScale(c), 2), x: fmt(c.stars * starScale(c), 2)})) : null,
    h('div', {}, gainWhy(c)),
    (c.flags || []).filter(f => !['beyond', 'pass', 'tech', 'cap'].includes(f)).map(f => h('div', {class: 'flag'}, '• ', t('rec.flag.' + f, FLAGS[f] || f))),
    adjustNotes(c),
    refs.length ? h('div', {}, h('div', {class: 'mute small'}, t('rec.refs', '参考にした似ている人（上位3人）')), h('ul', {class: 'refs'}, refs))
      : h('div', {class: 'mute small'}, t('rec.noRefs', 'この曲をプレイした似ている人はいません（あなたの曲線だけで推定）')),
    mapInfo(c));
}

// 開いた欄の「増える総PP」の説明。推定の幅（recommend.py の SPREAD）を入れる前の版で作ったリストの曲（p_up・gain_up が無い）は前の文
function gainWhy(c){
  const v = {b: fmt(c.best_pp, 2), e: fmt(c.est_pp, 2), d: fmt(c.est_pp - c.best_pp, 2), g: fmt(c.gain, 2)};
  if(c.gain_up == null){
    return c.best_pp != null ? tf('rec.why2', 'この曲の PP は 自己ベスト {b} → 推定 {e}（+{d}）。総PP（有効PP）では +{g} 増える。', v)
      : tf('rec.why2new', 'この曲の PP は推定 {e}。総PP（有効PP）では +{g} 増える。', v);
  }
  if(c.p_up === 0 && c.acc_cap && c.best_acc != null && c.best_acc >= c.acc_cap.acc){
    return tf('rec.why2cap', 'この曲の PP は 自己ベスト {b}。自己ベストが精度の上限（下の •）以上なので、増える総PP は 0 とみています。', v);
  }
  if(c.p_up != null){
    return tf('rec.why2up', 'この曲の PP は 自己ベスト {b}、推定精度の時 {e}。次に遊んだ日の一番は推定のまわりでぶれる（あなたの記録から）ので、自己ベストを超える見込みは {p}%、超えた時は総PP（有効PP）が平均 +{u} 増える。平均すると +{g}。', {
      ...v, p: fmt(100 * c.p_up), u: fmt(c.gain_up, 2)});
  }
  return tf('rec.why2spread', 'この曲の PP は推定 {e}。取れる精度は推定のまわりでぶれる（あなたの記録から）ので、そのぶれの中で平均すると総PP（有効PP）は +{g} 増える（クリアした時）。', v);
}

function runners(){
  if(!R.runnersUp.length) return null;
  return h('section', {class: 'card recList'},
    h('div', {class: 'cardHead'}, h('h2', {}, t('rec.runners', '次点')),
      h('span', {class: 'mute small'}, t('rec.runnersHint', '選んだ曲（無ければ一番下の曲）と入れ替えます'))),
    headRow(false), R.runnersUp.map(c => row(c, true)));
}

async function act(action, id, extra = {}){
  const r = await api('recommend/action', {action, id, preset: P.active, ...extra});
  if(!r.ok){ toast(t('rec.actFail', '操作に失敗しました'), 'err'); return; }
  R = r;
  if(action === 'remove' || action === 'never'){
    const ids = viewItems().map(x => x.leaderboard_id);
    sel = ids.length ? ids[0] : null;
  }
  draw();
  focusList();
}

function swapIn(id){
  let target = sel && R.items.some(x => x.leaderboard_id === sel && !x.pinned) ? sel : null;
  if(!target){   // 選んでいなければ、固定していない曲のうち並び順の値が一番低い曲
    const cand = R.items.filter(x => !x.pinned).sort((a, b) => a.score - b.score)[0];
    target = cand && cand.leaderboard_id;
  }
  if(!target){ toast(t('rec.noSwap', '入れ替えられる曲がありません（全部固定されています）'), 'info'); return; }
  sel = id;
  act('swap', target, {with: id});
}

function focusList(){ const el = main.querySelector('.recList'); if(el) el.focus({preventScroll: true}); }

function onKey(e){
  const ids = idsOf(viewItems());   // 見えている並び（＝書き出す順）のとおりに動く
  if(!ids.length) return;
  let i = ids.indexOf(sel);
  if((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.altKey){   // 選んだ曲を1つ上・下へ動かす（並びを保存する）
    e.preventDefault();
    const j = i + (e.key === 'ArrowDown' ? 1 : -1);
    if(i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    setOrder(ids);
    const el = main.querySelector('.recRow.sel'); if(el) el.scrollIntoView({block: 'nearest'});
  }else if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    i = i < 0 ? 0 : Math.max(0, Math.min(ids.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
    sel = ids[i]; draw(); focusList();
    const el = main.querySelector('.recRow.sel'); if(el) el.scrollIntoView({block: 'nearest'});
  }else if(e.key === ' ' && sel){
    e.preventDefault();
    const c = R.items.find(x => x.leaderboard_id === sel);
    if(c) act(c.pinned ? 'unpin' : 'pin', sel);
  }else if((e.key === 'Delete' || e.key === 'Backspace') && sel){
    e.preventDefault();
    act('remove', sel);
  }else if(e.key === 'Enter' && sel){
    if(open.has(sel)) open.delete(sel); else open.add(sel);
    draw(); focusList();
  }
}

// 条件（変えたらすぐ保存して組み立て直す）。広い時は一覧の右、狭い時は一覧の上（base.css の @container main）。
// 1項目＝「名前」と「入力・チェック・補足」の束（.condItem）。右にある時は名前の下に入力、上にある時は「左に名前・右に入力」の
// 行を縦に並べる（設定画面と同じ形。以前は狭い時に部品を格子に並べたので、項目と入力の対応がばらばらに見えた）
function conditions(){
  if(!S) return h('aside', {});
  const item = (key, label, ...body) => h('div', {class: 'condItem', dataset: {key}},
    h('label', {class: 'condLbl', for: 'cond_' + key}, label), h('div', {class: 'condBody'}, ...body));
  const num = (key, unit, attrs = {}) => h('span', {class: 'condCtl'},
    h('input', {type: 'number', id: 'cond_' + key, value: String(S[key]), class: 'num', ...attrs,
      onchange: e => setCond(key, Number(e.target.value), e.target)}), unit ? h('span', {class: 'mute small'}, unit) : null);
  const chk = (key, label, id) => h('label', {class: 'chk'}, h('input', {type: 'checkbox', id, checked: !!S[key],
    onchange: e => setCond(key, e.target.checked, e.target)}), h('span', {}, label));
  const hint = text => h('div', {class: 'condHint'}, text);
  const ratio = h('input', {type: 'range', id: 'cond_played_ratio', min: 0, max: 100, step: 5, value: String(S.played_ratio), class: 'slider',
    oninput: e => { ratioVal.textContent = e.target.value + '%'; },
    onchange: e => setCond('played_ratio', Number(e.target.value), e.target)});
  const ratioVal = h('span', {class: 'num'}, S.played_ratio + '%');
  // ★の範囲の入力（0.1 刻み。空にすると 0＝効かない）
  const starNum = key => h('input', {type: 'number', id: 'cond_' + key, value: String(S[key]), class: 'num', min: 0, max: 15, step: 0.1,
    onchange: e => setCond(key, Number(e.target.value), e.target)});
  // クールダウンの数の入力（文の中に並べる。0 で効かない）
  const coolNum = (key, max) => h('input', {type: 'number', id: 'cond_' + key, value: String(S[key]), class: 'num', min: 0, max, step: 1,
    onchange: e => setCond(key, Number(e.target.value), e.target)});
  // 前の言葉・数・単位のまとまり（まとまりの途中では折り返さない）
  const coolGrp = (pre, key, max, unit) => h('span', {class: 'coolGrp'}, pre ? h('span', {class: 'mute small'}, pre) : null, coolNum(key, max),
    h('span', {class: 'mute small'}, unit));
  const f = R.filters || {};
  const recent = f.recentStars;
  // 難しさ（未プレイ曲の pass の上限と挑戦枠をまとめて変える。presets.js）と、今のリストでの pass の上限（自分がクリアしてきた曲の分布から）
  const capBy = S.unplayed_pass_cap;
  const passHint = capBy === 'none' || !('passCap' in f) ? null
    : f.passCap == null ? hint(t('rec.c.passCapNone', 'クリアしてきた曲がまだ少ないので、pass の上限は効いていない'))
    : hint(tf('rec.c.passCap', '未プレイは pass {v} まで（あなたがクリアしてきた曲の{q}）{n}', {v: fmt(f.passCap, 2),
      q: capBy === 'p75' ? t('rec.c.p75', '75%点') : t('rec.c.p95', '95%点'),
      n: f.passOut ? tf('rec.c.passOut', '。超えて外した曲: {n}', {n: f.passOut}) : ''}));
  return h('aside', {class: 'card recCond'},
    h('h2', {}, t('rec.cond', '条件')),
    item('difficulty', t('rec.c.diff', '難しさ'),
      difficultyControl(S, {preset: P.active, id: 'cond_difficulty', onSaved: async v => { S = v; await reloadRec(); },
        onError: () => toast(t('rec.condBad', 'その値は使えません'), 'err')}),
      passHint,
      f.minClear ? hint(tf('rec.c.minClear', 'クリアの見込みが {p}% より低い曲は入れません{n}', {p: f.minClear,
        n: f.clearOut ? tf('rec.c.clearOut', '。外した曲: {n}', {n: f.clearOut}) : ''})) : null),
    item('playlist_size', t('set.k.playlist_size', '曲数'), num('playlist_size', t('set.u.playlist_size', '曲'), {min: 1, max: 50, step: 1})),
    item('played_ratio', t('set.k.played_ratio', 'プレイ済みの比率'), h('span', {class: 'condCtl ratio'}, ratio, ratioVal)),
    item('max_map_age_years', t('rec.c.age', '古い譜面を外す（直近X年）'),
      num('max_map_age_years', t('rec.c.ageUnit', '年・0で無効'), {min: 0, max: 20, step: 0.5}),
      chk('age_filter_unplayed', t('rec.c.ageUnplayed', '未プレイの曲にかける')),
      chk('age_filter_played', t('rec.c.agePlayed', 'プレイ済みの曲にかける'))),
    // ★の範囲（プレイ済みにも未プレイにもかける。0 で効かない）。下に、範囲の外で外した曲の数と、値を選ぶ参考
    // （このセットのモードで最近クリアした曲の★。assemble.recent_stars）
    item('star_min', t('rec.c.stars', '★の範囲'),
      h('span', {class: 'condCtl starRange'}, h('span', {class: 'mute small'}, '★'), starNum('star_min'),
        h('span', {class: 'mute small'}, '〜 ★'), starNum('star_max'), h('span', {class: 'mute small'}, t('rec.c.starsUnit', '下限は0で自動・上限は0で無効'))),
      starAutoHint(f.starMinAuto),
      f.starOut ? hint(tf('rec.c.starOut', '範囲の外で外した曲: {n}', {n: f.starOut})) : null,
      f.starTopOut ? hint(tf('rec.c.starTopOut', 'あなたがクリアしてきた★の一番上（★{x}）より上で外した未プレイの曲: {n}', {x: fmt(f.starTop, 2), n: f.starTopOut})) : null,
      recent ? hint(recent.p95 != null
        ? tf('rec.c.recentStars', '参考: このセットのモードで最近90日にクリアした曲は最高 ★{a}・95%点 ★{b}（{n}曲。NF・SS を除く）',
          {a: fmt(recent.max, 2), b: fmt(recent.p95, 2), n: recent.n})
        : tf('rec.c.recentStarsFew', '参考: このセットのモードで最近90日にクリアした曲は最高 ★{a}（{n}曲。NF・SS を除く）',
          {a: fmt(recent.max, 2), n: recent.n})) : null),
    item('one_diff_per_song', t('rec.c.oneDiff', '同じ曲の別難易度'),
      chk('one_diff_per_song', t('rec.c.oneDiffOn', '1つまでにする'), 'cond_one_diff_per_song')),
    // オンのセットどうしで同じ曲（別難易度も）を入れない（難しさ違いのセットを遊び比べる時。serve.py の _other_songs）
    item('exclude_other_sets', t('rec.c.otherSets', 'ほかのセットと同じ曲'),
      chk('exclude_other_sets', t('rec.c.otherSetsOn', '入れない（これがオンのセットどうし）'), 'cond_exclude_other_sets'),
      S.exclude_other_sets && f.otherOut ? hint(tf('rec.c.otherOut', 'ほかのセットのリストにある {n} 曲を外した', {n: f.otherOut})) : null),
    // クールダウン（遊んだ日で数える。assemble.cooldown_ids）: 再プレイは間をあけ、続けて遊んだら休み。初プレイはクリアできない日が続いたら休み
    item('replay_gap_days', t('rec.c.cool', 'クールダウン'),
      h('div', {class: 'coolHead'}, t('rec.c.coolReplay', '再プレイ（自己ベストのある曲）')),
      h('span', {class: 'condCtl coolRange'}, coolGrp(t('rec.c.coolGapPre', '遊んだ日から'), 'replay_gap_days', 30, t('rec.c.coolGap', '日あける'))),
      h('span', {class: 'condCtl coolRange'}, coolGrp(t('rec.c.coolStreakPre', '3週に'), 'replay_streak_days', 10, t('rec.c.coolStreak', '日遊んだら')),
        coolGrp('', 'replay_rest_weeks', 26, t('rec.c.coolRest', '週休み'))),
      h('div', {class: 'coolHead'}, t('rec.c.coolFirst', '初プレイ（まだクリアしていない曲）')),
      h('span', {class: 'condCtl coolRange'}, coolGrp(t('rec.c.coolFailPre', '3週にクリアできない日が'), 'unplayed_fail_days', 10, t('rec.c.coolFail', '日で')),
        coolGrp('', 'unplayed_rest_weeks', 26, t('rec.c.coolRest', '週休み'))),
      f.cooldown ? hint(tf('rec.c.cooled', 'クールダウンで外した曲: {n}（間をあける {g}・続けて遊んだ {r}・クリアできない日が続いた {x}）', {
        n: f.cooldown, g: (f.cooldownBy || {}).gap || 0, r: (f.cooldownBy || {}).rest || 0, x: (f.cooldownBy || {}).failRest || 0})) : null),
    item('challenge_max', t('set.k.challenge_max', '挑戦枠（範囲の外の曲）'),
      num('challenge_max', t('set.u.challenge_max', '曲まで'), {min: 0, max: 50, step: 1}),
      hint(tf('rec.c.challenge', 'あなたがクリアしてきた曲より難しい・ノーツが多い曲。今のリストに {n} 曲{k}', {
        n: f.challenge || 0, k: f.challengeSkipped ? tf('rec.c.challengeSkip', '（枠がいっぱいで {k} 曲を外した）', {k: f.challengeSkipped}) : ''}))),
    h('div', {class: 'condFoot'},
      h('div', {}, tf('rec.c.info', '候補: プレイ済み {p} 曲・未プレイ {u} 曲 / 似ている人 {s} 人', {
        p: R.counts.played, u: R.counts.unplayed, s: R.similarCount})),
      R.removed ? h('button', {class: 'small', onclick: () => act('reset')},
        tf('rec.c.restore', '外した {n} 曲を戻す', {n: R.removed})) : null,
      R.neverCount ? h('div', {}, tf('rec.c.never', '「今後出さない」にした曲: {n}（全セット共通）', {n: R.neverCount})) : null,
      h('div', {}, tf('rec.c.more', '条件は「{name}」のセットだけに効きます。計算の数値・プレイリスト名・画像は',
        {name: (currentPreset() || {}).name || ''}),
        h('button', {class: 'small linkBtn', onclick: () => CTX && CTX.show('settings')}, t('rec.c.toSettings', '設定画面')),
        t('rec.c.more2', 'で変えられます。'))));
}

const MODE_NAMES = {Standard: '両手', OneSaber: 'ワンセイバー'};

/** 自動で決めた★の下限の説明（下限が 0 のセット。assemble.auto_star_min）。下限を数字で決めたセット・前の版で作ったリストは null */
function starAutoHint(auto){
  if(!auto) return null;
  const modes = Object.keys(auto);
  const one = m => (modes.length > 1 ? t('mode.' + m, MODE_NAMES[m] || m) + ' ' : '')
    + (auto[m] != null ? '★' + fmt(auto[m], 1) : t('rec.c.starAutoNone', '決められないので下限なし'));
  return h('div', {class: 'condHint'}, tf('rec.c.starAuto', '下限は自動で {v}（更新すると総PP が 0.5PP 以上増える曲が半分を超える★。取得のたびに決め直します）',
    {v: modes.map(one).join('・')}));
}

async function setCond(key, value, el){
  const r = await api('settings/save', {changes: {[key]: value}, preset: P.active});
  if(!r.ok){ toast(t('rec.condBad', 'その値は使えません'), 'err'); if(el) el.value = String(S[key]); return; }
  S = r.values;
  await reloadRec();
}

function pickDir(then){
  chooseBsDir(path => {
    S.beat_saber_dir = path;
    draw();
    toast(tf('rec.dirSet', '出力先を {p} にしました', {p: path}), 'ok');
    if(then) then();
  });
}

async function doExport(){
  if(S && !S.beat_saber_dir){ pickDir(doExport); return; }   // 出力先が未設定なら、選んでから出力する
  const r = await api('export', {preset: P.active});
  if(!r.ok){ toast(exportErr(r.error), 'err'); return; }
  toast(tf('rec.exported', '{n}曲を書き込みました。ゲームを起動中なら PlaylistManager の「Refresh Playlists」を押すと読み直します。', {n: r.items}), 'ok');
  reloadRec();   // 「前回の出力と同じ曲です」に変わる
  warnStats();
}

async function doOneClick(){
  const r = await api('export/oneclick', {preset: P.active});
  if(!r.ok){ toast(exportErr(r.error), 'err'); return; }
  toast(tf('rec.oneclickDone', '{app} を起動しました（{n}曲）。{app} の画面で入れ終わるまで、このアプリは閉じないでください。',
    {app: appName(r.app), n: r.items}), 'ok');
  reloadRec();
  warnStats();
}

function appName(app){ return app === 'other' ? t('rec.theApp', 'アプリ') : app; }

// 全部のセットをワンクリックで入れる。サーバーはアプリが前の分を読みに来てから次を渡すので、アプリが起動していない時は数秒かかる
async function oneClickAll(ev){
  const btn = ev.currentTarget;
  btn.disabled = true;
  toast(tf('rec.ocAllWait', '{app} に1つずつ渡しています…', {app: appName(ONECLICK)}), 'info');
  let r;
  try { r = await api('export/oneclick/all'); } finally { btn.disabled = false; }
  if(!r.ok){ toast(exportErr(r.error), 'err'); return; }
  const bad = r.results.filter(x => !x.ok);
  if(!bad.length) toast(tf('rec.ocAllDone', '{n}個のセットのプレイリストを {app} に渡しました。{app} の窓が全部閉じるまで、このアプリは閉じないでください。',
    {n: r.results.length, app: appName(r.app)}), 'ok');
  else toast(bad.map(x => x.name + ': ' + exportErr(x.error)).join(' / '), 'err');
  reloadRec();
  if(!bad.length) warnStats();   // 1つでも失敗してエラーを出す時は出さない
}

async function doSaveFile(){
  const n = window.pywebview ? await native() : null;   // exe 版はネイティブの「名前を付けて保存」
  if(n && n.save_playlist){
    const r = await n.save_playlist(P.active);
    if(r && r.ok){ toast(tf('rec.saved', '{p} に保存しました。', {p: r.path}), 'ok'); reloadRec(); warnStats(); }
    else if(r && r.error !== 'cancelled') toast(exportErr(r.error), 'err');
    return;
  }
  // ブラウザで開いている時（開発中の確かめ）はダウンロードさせる
  const r = await api('export/file', {preset: P.active});
  if(!r.ok){ toast(exportErr(r.error), 'err'); return; }
  const url = URL.createObjectURL(new Blob([r.text], {type: 'application/json'}));
  const a = h('a', {href: url, download: r.filename});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  toast(tf('rec.downloaded', '{f} をダウンロードしました。', {f: r.filename}), 'ok');
  reloadRec();
  warnStats();
}

async function exportAll(){
  if(S && !S.beat_saber_dir){ pickDir(exportAll); return; }
  const r = await api('export/all');
  if(!r.ok){ toast(exportErr(r.error), 'err'); return; }
  const bad = r.results.filter(x => !x.ok);
  if(!bad.length) toast(tf('rec.allDone', '{n}個のセットのプレイリストを書き込みました。', {n: r.results.length}), 'ok');
  else toast(bad.map(x => x.name + ': ' + exportErr(x.error)).join(' / '), 'err');
  reloadRec();
  if(!bad.length) warnStats();
}
