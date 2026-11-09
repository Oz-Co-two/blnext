// 画面の入口。上の帯の切り替え（ホーム／プレイリスト（画面の名前は recommend）／似ている人／設定／使い方）と起動時の準備
import { APP_VERSION } from './version.js';
import { api } from './api.js';
import { loadLang, t, tf } from './i18n.js';
import { $, h } from './util.js';
import * as scale from './scale.js';
import * as home from './screens/home.js';
import * as recommend from './screens/recommend.js';
import * as similar from './screens/similar.js';
import * as settings from './screens/settings.js';
import * as guide from './screens/guide.js';
import * as refresh from './refresh.js';
import * as setup from './setup.js';
import * as update from './update.js';
import { P as presetState } from './presets.js';

const SCREENS = { home, recommend, similar, settings, guide };
let current = null;

export async function show(name){
  if(!SCREENS[name]) name = 'home';
  current = name;
  document.body.dataset.busy = '1';   // 描き終わるまでの目印（画面のテストが待つ。tools/cdp.py の wait_ready）
  document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('on', b.dataset.screen === name));
  const main = $('#main');
  main.replaceChildren();
  main.scrollTop = 0;
  // 描き終わるまでに時間がかかる時（プレイリストの画面は、データを取り直した後などに推定の計算をする）は「読み込んでいます」を出す。
  // 黒いままだと止まったように見えた（2026-11-09 の利用者の要望）。速い画面でちらつかないよう、少し待っても何も描かれていない時だけ
  const loading = h('div', {class: 'screenLoading', role: 'status'}, h('span', {class: 'spin'}),
    h('div', {}, h('div', {}, t('top.loading', '読み込んでいます…')),
      name === 'recommend' ? h('div', {class: 'mute small'}, t('top.loadingRec', 'データを取り直した後は、推定の計算に数秒かかります')) : null));
  const timer = setTimeout(() => { if(current === name && !main.childElementCount) main.append(loading); }, LOADING_DELAY);
  try{ await SCREENS[name].render(main, { show, status }); }
  finally{ clearTimeout(timer); loading.remove(); document.body.dataset.screen = name; delete document.body.dataset.busy; }
}
const LOADING_DELAY = 120;   // 「読み込んでいます」を出すまでの待ち（ミリ秒）

const status = {};   // 画面へ ctx.status として渡す（同じ物を書き換えて使う）

/** サーバーの状態を読み直す（取得の後・言語の切り替え） */
async function loadStatus(){
  const st = await api('status');
  if(st.ok){
    Object.assign(status, st);
    presetState.list = st.presets || [];
    presetState.active = st.activePreset;
  }
  showDataAt();
}

/** 上の帯の「いつのデータか」。前回の取得からの時間と、起動時に自動で取る間隔 */
function showDataAt(){
  const el = $('#dataAt');
  const at = status.lastRunAt;
  $('#refreshBtn').hidden = !status.playerId;
  if(!status.playerId){ el.textContent = ''; return; }
  if(!at){ el.textContent = t('top.noData', 'データ未取得'); el.title = ''; return; }
  const d = new Date(at * 1000);
  const time = d.toLocaleString('ja-JP', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  // 狭い時（文字を大きくした時など）は時刻を省いて「データ（3時間前）」にする（base.css の #topbar の @container）
  el.replaceChildren(t('top.data', 'データ'), h('span', {class: 'daTime'}, ' ' + time),
    tf('top.ago', '（{ago}）', {ago: ago(Date.now() / 1000 - at)}));
  const hrs = status.autoRefreshHours;
  el.title = hrs ? tf('top.autoTip', '起動した時、前回の取得から {h} 時間以上たっていれば自動で取り直します（設定の「通信」で変えられます）。それより短ければ「更新」を押した時だけ取ります。', {h: hrs})
    : t('top.autoTip0', '起動するたびに自動で取り直します（設定の「通信」で変えられます）。');
}

function ago(sec){
  if(sec < 90) return t('top.justNow', 'たった今');
  if(sec < 3600) return tf('top.minAgo', '{n}分前', {n: Math.round(sec / 60)});
  if(sec < 48 * 3600) return tf('top.hourAgo', '{n}時間前', {n: Math.floor(sec / 3600)});
  return tf('top.dayAgo', '{n}日前', {n: Math.floor(sec / 86400)});
}

/** 画面の下に知らせを出す（kind: 'ok' | 'err' | 'info'）。文言は呼ぶ側で訳したもの */
export function toast(msg, kind = 'info'){
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'show ' + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ''; }, kind === 'err' ? 10000 : 3500);
}
/** 取得の結果に知らせ（core/fetch/refresh.py の notices）の code があるか */
function hasNotice(p, code){
  return (p.notices || []).some(n => n.code === code);
}
window.addEventListener('error', e => toast(`${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})`, 'err'));
window.addEventListener('unhandledrejection', e => toast('Promise: ' + (e.reason && e.reason.message || e.reason), 'err'));

async function boot(){
  $('#appVersion').textContent = APP_VERSION;
  await loadStatus();
  scale.set(status.uiScale);   // 文字の大きさ（最初の画面を描く前に）
  await loadLang(status.lang || 'ja');
  scale.bind();
  showDataAt();
  if(window.__blnNoToken){
    toast(t('m.noToken', 'サーバーの合言葉がありません。サーバーを起動した時に表示された URL を開いてください。'), 'err');
  }
  document.querySelectorAll('#nav button').forEach(b => b.addEventListener('click', () => show(b.dataset.screen)));
  $('#refreshBtn').addEventListener('click', () => refresh.start());
  refresh.onDone(async p => {
    if(setup.isOpen()) ;   // 初回の準備の小さな画面が結果を出す
    else if(p.error) toast(refresh.errText(p.error), 'err');
    else if(p.cancelled) toast(t('ref.cancelled', '取得をやめました。取得済みの分で表示しています。'), 'info');
    else if(hasNotice(p, 'pp-formula')) toast(t('ref.ppFormula', 'BeatLeader の PP の計算式がアプリと合わなくなっています。推定の PP・増える総PP がずれているので、アプリを直すまで当てにしないでください。'), 'err');
    else if(hasNotice(p, 'reweight')) toast(t('ref.reweight', 'BeatLeader で PP・★が計算し直されていた（リウェイト）ので、全部取り直しました。'), 'info');
    else if(p.warnings && p.warnings.length) toast(tf('ref.partial', '一部の取得に失敗しました（{n}件）。取れた分で表示しています。', {n: p.warnings.length}), 'info');
    await loadStatus();
    if(current === 'home' || current === 'recommend' || current === 'similar') await show(current);
    // 初回の準備の小さな画面を閉じた後に「更新」で取り終えた: リストを作る所から続ける
    if(status.setup && !setup.isOpen() && !p.error && !p.cancelled) setup.open(status);
  });
  setInterval(showDataAt, 60000);   // 「○分前」を進める
  const first = (/(?:^#|&)s=([a-z]+)/.exec(location.hash) || [])[1];   // 最初に開く画面（確認用。#k=…&s=recommend）
  await show(status.playerId && SCREENS[first] ? first : 'home');
  $('#bootCover').classList.add('gone');
  if(status.setup){   // 初回の準備（プレイヤーを選ぶ → 取得 → 3つのリスト）。途中で閉じていれば続きから
    setup.open(status);
    return;
  }
  // 起動時の取得は、前回の取得から設定の間隔（初期値12時間）以上たっている時だけ（serve.py の should_auto_refresh）。
  // 近ければ取りに行かない＝何度も開き直しても BeatLeader へ通信しない。取りたい時は上の「更新」
  update.autoCheck(status);   // 新しい版の確認（exe 版・設定がオンの時だけ。つながらなければ何も出さない）
  if(status.autoRefresh) await refresh.start();
}

/** 言語を切り替えた時など、今の画面を描き直す */
export async function reload(){
  await loadStatus();
  await loadLang(status.lang || 'ja');
  showDataAt();
  await show(current || 'home');
}

boot();
