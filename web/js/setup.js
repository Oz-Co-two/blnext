// 初回の準備の小さな画面: ① プレイヤーを選ぶ（名前か ID）→ ② データの取得（初回は1分ほど）→ ③ 3つのセット（着実・標準・チャレンジング）の
// リストができたので、ゲームに入れる。サーバー側は serve.py の setup/start・setup/lists。途中で閉じても、次に開いた時に続きから（status.setup）
import { api } from './api.js';
import { t, tf } from './i18n.js';
import { h, fmt } from './util.js';
import * as refresh from './refresh.js';
import { choose as chooseBsDir } from './bsdir.js';
import * as stats from './stats.js';
import { show, toast } from './main.js';

const ERRORS = {
  'player-not-found': 'BeatLeader にそのプレイヤーが見つかりません。',
  offline: 'BeatLeader につながりません。インターネットの接続を確かめてください。',
  'http-429': 'BeatLeader から「アクセスが多すぎる」と言われました。少し時間をおいてください。',
};
const errText = code => t('setup.err.' + code, ERRORS[code] || tf('setup.err.other', 'うまくいきませんでした（{code}）', {code}));
/** ★の範囲の表示（「★自動〜5.7」「★5.1〜6.2」。下限の 0 は自動＝assemble.auto_star_min）。上限が入っていなければ null */
function starText(s){
  if(!s.starMin && !s.starMax) return null;
  return '★' + (s.starMin ? fmt(s.starMin, 1) : t('setup.starAuto', '自動')) + '〜' + (s.starMax ? fmt(s.starMax, 1) : '');
}
const AIMS = {   // 段階のねらい（docs/spec.md の「難しさ」）
  steady: 'クリアしやすい曲で、着実に自己ベストを伸ばす',
  standard: '取れる PP とクリアの見込みのつり合い',
  challenge: '難しい曲にも挑戦する',
};

let back = null, box = null, status = null;
let waiting = false;   // 取得の終わりを待っている
let listening = false;

export const isOpen = () => !!back;

/** 開く（st は main.js の status）。st.setup が 'player' ならプレイヤーを選ぶ所から、'lists' なら取得・リストの所から */
export function open(st){
  if(back) return;
  status = st;
  box = h('div', {class: 'modal setupDlg', role: 'dialog', 'aria-modal': 'true'});
  back = h('div', {class: 'modalBack'}, box);
  document.body.append(back);
  if(!listening){ listening = true; refresh.onDone(onFetched); }
  if(st.setup === 'lists') fetchStep(st.lastRunAt ? null : '');
  else playerStep();
}

function close(){
  refresh.watch(null);
  waiting = false;
  if(back) back.remove();
  back = box = null;
}

function steps(n){
  const names = [t('setup.step1', '1 プレイヤー'), t('setup.step2', '2 データの取得'), t('setup.step3', '3 プレイリスト')];
  return h('div', {class: 'setupSteps'}, names.map((x, i) => h('span', {class: i === n ? 'on' : ''}, x)));
}

// ---- ① プレイヤーを選ぶ ----
function playerStep(message = ''){
  const results = h('div', {class: 'results'});
  const msg = h('p', {class: 'err small'}, message);
  const q = h('input', {type: 'text', id: 'setupQuery', 'data-i18n-ph': 'setup.queryPh', placeholder: 'BeatLeader の表示名か ID',
    onkeydown: e => { if(e.key === 'Enter') search(); }});
  async function search(){
    msg.textContent = '';
    let name = q.value.trim();
    const url = /beatleader\.[a-z]+\/u\/(\d+)/i.exec(name);   // プロフィールの URL をそのまま貼った時
    if(url) name = url[1];
    if(name.length < 2){ results.replaceChildren(h('p', {class: 'mute small'}, t('set.searchShort', '2文字以上入れてください'))); return; }
    if(name.length > 40){ results.replaceChildren(h('p', {class: 'mute small'}, t('setup.tooLong', '長すぎます。表示名か ID を入れてください'))); return; }
    results.replaceChildren(h('p', {class: 'mute small'}, t('set.searching', '探しています…')));
    const r = await api('players/search', {name});
    if(!r.ok){ results.replaceChildren(); msg.textContent = errText(r.error); return; }
    if(!r.players.length){ results.replaceChildren(h('p', {class: 'mute small'}, t('set.searchNone', '見つかりませんでした'))); return; }
    results.replaceChildren(...r.players.map(p => h('div', {class: 'result'},
      h('span', {class: 'name'}, p.name),
      h('span', {class: 'mute'}, (p.country || '').toUpperCase()),
      h('span', {class: 'mute num'}, tf('set.resultRank', '世界 #{r}', {r: p.rank ?? '—'})),
      h('span', {class: 'mute num'}, (p.pp ?? 0).toLocaleString('ja-JP', {maximumFractionDigits: 2}) + ' PP'),
      h('button', {class: 'small primary', onclick: e => choose(p, e.currentTarget)}, t('setup.choose', 'この人です')))));
  }
  async function choose(p, btn){
    btn.disabled = true;
    msg.textContent = '';
    const r = await api('setup/start', {id: p.id});
    if(!r.ok){ btn.disabled = false; msg.textContent = errText(r.error); return; }
    status.playerId = p.id;
    status.setup = 'lists';
    status.statsPrivate = r.statsPublic === false;
    if(r.statsPublic === false) statsStep(r.name);   // 先に公開してもらえば、最初の取得で挑戦の記録も取れる
    else fetchStep('', r.name);
  }
  box.replaceChildren(
    h('h2', {}, t('setup.title', 'はじめに')),
    steps(0),
    h('p', {}, t('setup.intro', 'BeatLeader の公開データから、次に遊ぶと総PP が伸びそうな曲を選んでプレイリストにします。まず、あなたのプレイヤーを教えてください（ログインは要りません）。')),
    h('div', {class: 'setupSearch'}, q, h('button', {class: 'primary', onclick: search}, t('set.search', '探す'))),
    h('p', {class: 'mute small'}, t('setup.queryHint', 'ID は、BeatLeader のプロフィールの URL の最後の数字です（URL をそのまま貼ってもかまいません）。')),
    results, msg,
    h('div', {class: 'modalFoot'}, h('button', {onclick: close}, t('setup.later', 'あとで'))));
  q.focus();
}

// ---- 統計が非公開の時の案内（①と②の間） ----
function statsStep(name){
  const msg = h('p', {class: 'err small', role: 'status'});
  const go = () => fetchStep('', name);
  box.replaceChildren(
    h('h2', {}, t('setup.statsTitle', '統計を公開にしてください（おすすめ）')), steps(0),
    ...stats.guide(), msg,
    h('div', {class: 'modalFoot'},
      h('button', {class: 'setupSkipStats', onclick: go}, t('setup.statsSkip', 'このまま進む')),
      stats.openButton(),
      stats.checkButton(msg, () => { status.statsPrivate = false; go(); })));
}

// ---- ② データの取得 ----
/** resume: null＝もう取り終えている（リストを作る所へ）、''＝取得を始める。name は選んだ人の名前（続きからの時は無い） */
function fetchStep(resume, name){
  if(resume === null) return listsStep();
  const label = h('p', {class: 'small'}, t('setup.starting', '始めています…'));
  const fill = h('div', {class: 'stripFill', style: 'width:15%'});
  const bar = h('div', {class: 'stripBar setupBar indet'}, fill);
  box.replaceChildren(
    h('h2', {}, name ? tf('setup.fetchTitle', '{name} さんのデータを取得しています', {name}) : t('setup.fetchTitleResume', 'データを取得しています')),
    steps(1),
    label, bar,
    h('p', {class: 'mute small'}, t('setup.fetchHint', '自分のスコアと、国内の順位が近い人（最大50人）のスコアを取得するので、1分ほどかかります。途中でアプリを閉じても、取得した分は残り、次に開いた時に続きから取得します。')),
    h('div', {class: 'modalFoot'}, h('button', {class: 'setupCancel', onclick: () => api('refresh/cancel')}, t('ref.cancel', 'やめる'))));
  refresh.watch(p => {
    if(!p.running) return;
    label.textContent = refresh.phaseText(p);
    bar.classList.toggle('indet', !p.total);
    fill.style.width = p.total ? Math.round(100 * p.done / p.total) + '%' : '15%';
  });
  waiting = true;
  refresh.start();
}

async function onFetched(p){
  if(!back || !waiting) return;
  waiting = false;
  refresh.watch(null);
  if(p.error || p.cancelled) failedStep(p);
  else await listsStep();
}

function failedStep(p){
  box.replaceChildren(
    h('h2', {}, p.cancelled ? t('setup.cancelled', '取得をやめました') : t('setup.failed', '取得できませんでした')),
    steps(1),
    h('p', {class: p.cancelled ? '' : 'err'}, p.cancelled ? t('setup.cancelledHint', '取得した分は残っています。もう一度始めると続きから取得します。') : refresh.errText(p.error)),
    h('div', {class: 'modalFoot'},
      h('button', {onclick: reselect}, t('setup.reselect', 'プレイヤーを選び直す')),
      h('button', {onclick: close}, t('setup.laterResume', '閉じる（次に開いた時に続けます）')),
      h('button', {class: 'primary', onclick: () => fetchStep('')}, t('setup.retry', 'もう一度'))));
}

async function reselect(){
  const r = await api('settings/save', {changes: {player_id: ''}});
  if(!r.ok){ toast(errText(r.error), 'err'); return; }
  status.playerId = '';
  status.setup = 'player';
  playerStep();
}

// ---- ③ リストができた・ゲームに入れる ----
async function listsStep(){
  box.replaceChildren(h('h2', {}, t('setup.making', 'プレイリストを作っています…')), steps(2),
    h('div', {class: 'stripBar setupBar indet'}, h('div', {class: 'stripFill', style: 'width:15%'})));
  const [r, oc] = await Promise.all([api('setup/lists'), api('oneclick/info')]);
  if(!back) return;
  status.setup = null;
  const sets = r.ok ? r.sets : [];
  if(!sets.some(s => s.songs)){   // ランク曲のスコアがまだ無い人
    box.replaceChildren(
      h('h2', {}, t('setup.noLists', 'まだプレイリストを作れません')), steps(2),
      h('p', {}, r.ok ? t('setup.noListsHint', 'BeatLeader にランク曲のスコアがまだ無いか、少なすぎます。ランク曲を遊んでから「更新」を押し、プレイリストの画面を開いてください。')
        : errText(r.error)),
      h('div', {class: 'modalFoot'}, h('button', {class: 'primary', onclick: () => finish('home')}, t('setup.toHome', 'ホームへ'))));
    return;
  }
  const app = oc.ok ? oc.app : null;
  const rough = stats.roughText(sets.find(s => s.songs));
  const msg = h('p', {class: 'small', role: 'status'});
  const toRec = h('button', {onclick: () => finish('recommend')}, t('setup.toRec', 'プレイリストを見る'));
  const toGuide = h('button', {class: 'setupGuide', onclick: () => finish('guide')}, t('setup.toGuide', '使い方を見る'));
  const write = h('button', {class: 'primary', onclick: () => writeAll(write)}, t('setup.write', 'Beat Saber に書き込む'));
  const click = app ? h('button', {class: 'primary', onclick: () => oneClick(click, app, msg)},
    app === 'other' ? t('setup.oneclick', 'ワンクリックで入れる') : tf('setup.oneclickApp', '{app} で入れる', {app})) : null;
  async function writeAll(btn){
    const s = await api('settings/get');
    if(s.ok && !s.values.beat_saber_dir){ chooseBsDir(() => writeAll(btn)); return; }
    btn.disabled = true;
    const w = await api('export/all');
    btn.disabled = false;
    report(w, msg, t('setup.written', '書き込みました。ゲームの中で足りない曲をダウンロードして遊んでください。'));
  }
  box.replaceChildren(
    h('h2', {}, t('setup.ready', '準備ができました')), steps(2),
    h('p', {}, t('setup.readyHint', '難しさ違いの3つのプレイリストを作りました。ゲームに入れて、遊んでみてください。曲数や難しさは「プレイリスト」の画面の右の条件で変えられます。'),
      sets.some(starText) ? t('setup.starHint', '★の範囲は、あなたの最近の自己ベストの★から決めました。') : null),
    h('div', {class: 'setupSets'}, sets.map(s => h('div', {class: 'setupSet'},
      h('div', {}, h('div', {class: 'sn'}, s.name, starText(s) ? h('span', {class: 'ss num'}, starText(s)) : null),
        AIMS[s.level] ? h('div', {class: 'sd mute'}, t('setup.aim.' + s.level, AIMS[s.level])) : null),
      h('span', {class: 'num'}, tf('setup.songs', '{n}曲', {n: s.songs})),
      h('span', {class: 'num mute'}, tf('setup.gain', '見込み +{g}PP', {g: fmt(s.gain ?? 0, 2)}))))),
    rough ? h('p', {class: 'mute small setupRough'}, rough) : null,
    h('p', {class: 'mute small'}, t('setup.modHint', 'ゲームの中でプレイリストを読むには Mod の PlaylistManager が要ります。BSManager で入れると、足りない曲もその場でダウンロードします。遊んだ後にこのアプリを開くと、結果をホームで振り返れます。遊び方と画面の見方は、上の「使い方」でいつでも見られます。')),
    msg,
    h('div', {class: 'modalFoot'}, toGuide, toRec, write, click));
}

async function oneClick(btn, app, msg){
  btn.disabled = true;
  msg.textContent = tf('rec.ocAllWait', '{app} に1つずつ渡しています…', {app: app === 'other' ? t('rec.theApp', 'アプリ') : app});
  const r = await api('export/oneclick/all');
  btn.disabled = false;
  report(r, msg, t('setup.handed', '渡しました。入れ終わるまで、このアプリは閉じないでください。'));
}

function report(r, msg, okText){
  if(!r.ok){ msg.textContent = errText(r.error); return; }
  const bad = r.results.filter(x => !x.ok);
  msg.textContent = bad.length ? bad.map(x => x.name + ': ' + errText(x.error)).join(' / ') : okText;
}

async function finish(screen){
  close();
  await show(screen);
}
