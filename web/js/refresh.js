// 起動時の自動更新（取得）の進捗の帯。取得は serve.py → core/fetch/refresh.py が別のスレッドで行い、ここは0.5秒ごとに様子を聞くだけ
import { api } from './api.js';
import { t, tf } from './i18n.js';
import { $, h } from './util.js';

const PHASES = {
  self: '自分の情報', ranking: '国内ランキング', mapsCheck: 'ランク譜面の変化の確認', maps: 'ランク譜面の一覧', selfScores: '自分のスコア',
  attempts: '挑戦の記録', history: '順位の推移', pool: '比較対象のスコア',
};
const ERRORS = {
  offline: 'BeatLeader につながりません。前回までのデータで表示しています。',
  'no-player': 'プレイヤーIDが未設定です。',
  'player-not-found': 'BeatLeader にこのプレイヤーIDが見つかりません。設定を確かめてください。',
  'http-429': 'BeatLeader から「アクセスが多すぎる」と言われました。少し時間をおいてから更新してください。',
  internal: '取得の途中でエラーが起きました（logs/ に記録があります）。',
};
export const errText = code => t('ref.err.' + code, ERRORS[code] || tf('ref.err.other', '取得に失敗しました（{code}）', {code}));

let timer = null;
const listeners = new Set();
let watcher = null;
export let last = null;

/** 取得が終わった時に呼ぶ関数を足す（画面の描き直し） */
export function onDone(fn){ listeners.add(fn); }
/** 進み具合を聞くたびに呼ぶ関数（初回の準備の小さな画面。setup.js。null で外す） */
export function watch(fn){ watcher = fn; }
/** 今の段階の文（「比較対象のスコア 200人中3人」など） */
export function phaseText(p){
  const phase = t('ref.ph.' + p.phase, PHASES[p.phase] || p.phase || '…');
  if(!p.total) return phase;
  return phase + ' ' + (p.phase === 'pool'
    ? tf('ref.countPeople', '{total}人中{done}人', {done: p.done, total: p.total})
    : tf('ref.count', '{done} / {total}', {done: p.done, total: p.total}));
}

export async function start(){
  const r = await api('refresh/start');
  if(!r.ok) return;
  show(r.progress);
  poll();
}

async function poll(){
  clearTimeout(timer);
  const r = await api('refresh/progress');
  if(!r.ok){ timer = setTimeout(poll, 2000); return; }
  last = r.progress;
  show(r.progress);
  if(r.progress.running){ timer = setTimeout(poll, 500); return; }
  for(const fn of listeners){ try{ await fn(r.progress); }catch(e){ console.error(e); } }
}

function show(p){
  const el = $('#strip');
  const btn = $('#refreshBtn');
  if(btn) btn.disabled = !!p.running;
  if(watcher) watcher(p);
  if(!p.running){ el.classList.remove('on'); el.replaceChildren(); return; }
  const pct = p.total ? Math.round(100 * p.done / p.total) : 0;
  el.classList.add('on');
  el.replaceChildren(
    h('span', {class: 'stripLbl'}, t('ref.updating', '更新中'), ' — ', phaseText(p)),
    h('div', {class: 'stripBar'}, h('div', {class: 'stripFill', style: `width:${p.total ? pct : 15}%`})),
    h('button', {class: 'small', onclick: () => api('refresh/cancel')}, t('ref.cancel', 'やめる')));
  el.querySelector('.stripBar').classList.toggle('indet', !p.total);
}

/** 取得中か（最後に聞いた時点） */
export const running = () => !!(last && last.running);
