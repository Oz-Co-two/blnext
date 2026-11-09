// セット（設定のパターン。例: スタンダード用・ワンセイバー用）の小道具。おすすめ画面・設定画面・似ている人の画面で共通に使う。
//  - 今開いているセットはサーバーの設定（active_preset）に覚える＝画面を移っても、次に起動しても同じセットを開く
//  - 上に並べるタブと、セットを足す小さな画面
//  - 難しさのスライダー（セットの中の項目をまとめて変える）と、難しさ違いのセットを一度に作る小さな画面
//  - 組のセット（ほかのセットと同じ曲を入れないセットどうし）で「作り直す」時に、組全部を交互に選び直すかを選ぶ小さな画面
import { api } from './api.js';
import { t, tf } from './i18n.js';
import { h } from './util.js';

// diff: 難しさの段階 {levels: [{id, values, setName}], setSize}（core/settings.py の DIFFICULTY。status・settings/get が返す）
// group: 組のセット（「ほかのセットと同じ曲を入れない」がオンのセットどうし）の id。交互に選ぶ順＝易しい順（serve.py の _group_ids）
export const P = {list: [], active: null, diff: {levels: [], setSize: 5}, group: []};

const ERRORS = {
  'bad-name': '名前を1〜30文字で入れてください',
  'too-many-presets': 'セットはこれ以上作れません（10個まで）',
  'last-preset': '最後のセットは消せません',
  'no-preset': 'そのセットはもうありません',
};
export const presetErr = code => t('ps.err.' + code, ERRORS[code] || code);

/** サーバーからセットの並びと開いているセットを読み直す */
export async function load(){
  const r = await api('status');
  if(r.ok){ P.list = r.presets || []; P.active = r.activePreset; if(r.difficulty) P.diff = r.difficulty; P.group = r.group || []; }
  return P;
}

export const current = () => P.list.find(p => p.id === P.active) || P.list[0] || null;

/** セットを開く（覚える） */
export async function open(id){
  const r = await api('presets/open', {id});
  if(r.ok) P.active = r.activePreset;
  return r.ok;
}

/** セットのタブ。onPick(id) で切り替え、「＋」でセットを足す（足したら onPick(新しい id)）。extra は右端に置く物。
 *  add=false なら「＋」を出さない。
 *  タブはドラッグか Alt+←→（Alt+↑↓ も。おすすめの一覧の曲と同じ作法）で並べ替えられる（presets/order。設定の presets の並び＝
 *  タブの並び・ホームの振り返りのタブの並び・全部のセットを書き込む順）。並べ替えはその場で描き直し、保存は後から送った順に届ける */
export function tabs(onPick, extra = null, add = true){
  const bar = h('div', {class: 'presetTabs', role: 'tablist', onkeydown: onKey, ondragover: dragOver, ondrop: drop});
  const extraBox = extra ? h('span', {class: 'presetExtra'}, extra) : null;
  const addBtn = add ? h('button', {class: 'presetTab add', title: t('ps.addTip', 'セットを足す（例: ワンセイバー用・曲数を変えた版）'),
    onclick: () => addDialog(onPick)}, '＋ ', t('ps.add', 'セットを足す')) : null;
  let drag = null;   // ドラッグで動かしているセットの id
  const many = () => P.list.length > 1;
  const tab = p => h('button', {class: 'presetTab' + (p.id === P.active ? ' on' : ''), role: 'tab', dataset: {preset: p.id},
    'aria-selected': p.id === P.active ? 'true' : 'false', draggable: many() ? 'true' : null,
    title: many() ? t('ps.tabTip', 'ドラッグか Alt+←→ で並びを変えられます（ホームの振り返りのタブもこの並びになります）') : null,
    ondragstart: e => {
      drag = p.id;
      e.currentTarget.classList.add('dragging');
      if(e.dataTransfer){ e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', p.id); }
    },
    ondragend: dragEnd,
    onclick: async () => { if(p.id === P.active) return; await open(p.id); onPick(p.id); }}, p.name);
  // null（「＋」・右端の物が無い時）は replaceChildren に渡すと "null" の文字になるので除く
  const fill = () => bar.replaceChildren(...[h('span', {class: 'presetLbl mute small'}, t('ps.label', 'セット')), ...P.list.map(tab), addBtn, extraBox]
    .filter(Boolean));

  function at(e){   // 指しているタブと、その右半分か
    const b = e.target instanceof Element ? e.target.closest('.presetTab[data-preset]') : null;
    if(!b) return null;
    const r = b.getBoundingClientRect();
    return {b, after: e.clientX > r.left + r.width / 2};
  }
  function clearDrop(){ bar.querySelectorAll('.dropBefore, .dropAfter').forEach(x => x.classList.remove('dropBefore', 'dropAfter')); }
  function dragOver(e){
    const p = drag && at(e);
    if(!p) return;
    e.preventDefault();
    if(e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    clearDrop();
    if(p.b.dataset.preset !== drag) p.b.classList.add(p.after ? 'dropAfter' : 'dropBefore');
  }
  function drop(e){
    const id = drag, p = id && at(e);
    if(!p) return;
    e.preventDefault();
    dragEnd();
    const ids = P.list.map(x => x.id).filter(x => x !== id);
    const i = ids.indexOf(p.b.dataset.preset);
    if(i < 0) return;   // 自分の上に落とした
    ids.splice(i + (p.after ? 1 : 0), 0, id);
    reorder(ids, null);
  }
  function dragEnd(){
    drag = null;
    clearDrop();
    bar.querySelectorAll('.presetTab.dragging').forEach(x => x.classList.remove('dragging'));
  }
  function onKey(e){
    const id = e.target instanceof Element && e.target.dataset ? e.target.dataset.preset : null;
    const dir = {ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1}[e.key];
    if(!e.altKey || !dir || !id || !many()) return;
    e.preventDefault();   // Alt+← はブラウザの「戻る」でもある
    const ids = P.list.map(x => x.id), i = ids.indexOf(id), j = i + dir;
    if(i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder(ids, id);
  }
  function reorder(ids, focusId){
    if(ids.join('\n') === P.list.map(x => x.id).join('\n')) return;
    P.list = ids.map(id => P.list.find(x => x.id === id));
    fill();
    if(focusId){ const b = bar.querySelector(`.presetTab[data-preset="${focusId}"]`); if(b) b.focus(); }
    orderQ = orderQ.then(() => api('presets/order', {ids})).then(r => {
      if(!r.ok) return load().then(() => { if(bar.isConnected) fill(); });   // 保存できなければサーバーの並びに戻す
    });
  }
  fill();
  return bar;
}
let orderQ = Promise.resolve();   // セットの並びの保存は送った順に1つずつ（続けて動かした時に、前の応答で戻らないように）

/** セットを足す小さな画面。今のセットを写すか、初期値から作るかを選べる */
export function addDialog(onCreated){
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const cur = current();
  const name = h('input', {type: 'text', class: 'wide', maxlength: 30, 'data-i18n-ph': 'ps.namePh', placeholder: '例: ワンセイバー',
    onkeydown: e => { if(e.key === 'Enter') create(); }});
  const copy = h('input', {type: 'checkbox', checked: true});
  const msg = h('p', {class: 'err small'});
  const box = h('div', {class: 'modal narrow', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('ps.addTitle', 'セットを足す')),
    h('p', {class: 'mute small'}, t('ps.addWhat',
      'セットごとに、リストの条件・計算の数値・プレイリスト名・画像を変えて、別のプレイリストを作れます（例: ワンセイバー用・曲数を変えた版）。')),
    h('label', {class: 'cond'}, h('span', {}, t('ps.name', 'セットの名前')), name),
    cur ? h('label', {class: 'chk'}, copy, tf('ps.copyFrom', '「{name}」の設定を写して作る（オフなら初期値から）', {name: cur.name})) : null,
    msg,
    h('div', {class: 'modalFoot'}, h('button', {onclick: close}, t('ps.cancel', 'やめる')),
      h('button', {class: 'primary', onclick: create}, t('ps.create', '作る'))));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  name.focus();

  async function create(){
    const r = await api('presets/add', {name: name.value, copyFrom: cur && copy.checked ? cur.id : null});
    if(!r.ok){ msg.textContent = presetErr(r.error); return; }
    P.list = r.presets; P.active = r.id;
    close();
    onCreated && onCreated(r.id);
  }
}

// ---- 難しさ（セットの中の「未プレイ曲の pass の上限」と「挑戦枠」をまとめて変える） ----
// 段階の値はサーバーから受け取る（P.diff）。名前と「何が変わるか・期待できること」の文はここ（訳は lang/en.json）
const LEVEL_TEXT = {
  steady: ['着実', 'クリアしやすい曲が中心です。遊ぶたびに自己ベストを伸ばしやすくなります（着実に登っている感）。代わりに、増える総PP の見込みは小さめです'],
  standard: ['標準', '取れる PP とクリアの見込みのつり合いで選びます。あなたがクリアしてきた曲より難しい曲も少しだけ入ります'],
  challenge: ['チャレンジング', '難しい曲・ノーツの多い曲が増えます。クリアできない曲も出やすくなりますが、取れた時の伸びと達成感は大きくなります'],
};
const CAP_TEXT = {none: '上限なし', p95: 'クリアしてきた曲の95%点まで', p75: 'クリアしてきた曲の75%点まで'};
export const levelName = id => t('ps.lv.' + id, (LEVEL_TEXT[id] || [id])[0]);
const levelWhat = lv => tf('ps.lv.vals', '未プレイ曲の pass: {cap}・挑戦枠: {n} 曲まで', {
  cap: t('ps.cap.' + lv.values.unplayed_pass_cap, CAP_TEXT[lv.values.unplayed_pass_cap] || lv.values.unplayed_pass_cap),
  n: lv.values.challenge_max})
  + (lv.values.min_clear ? tf('ps.lv.minClear', '・クリアの見込み {p}% 以上', {p: lv.values.min_clear}) : '')
  + tf('ps.lv.capMargin', '・精度の上限: 近い★の自分の一番{m}', {m: lv.values.acc_cap_margin ? '＋' + lv.values.acc_cap_margin + '%' : 'まで'});
const levelGood = id => t('ps.lv.' + id + 'Desc', (LEVEL_TEXT[id] || ['', ''])[1]);

/** 今の値が合う段階の id（どれとも合わなければ null＝カスタム。core/settings.py の difficulty_of と同じ決め方） */
export function levelOf(values){
  const lv = values && P.diff.levels.find(l => Object.entries(l.values).every(([k, v]) => values[k] === v));
  return lv ? lv.id : null;
}

let refocus = false;   // キーで動かした時は、描き直した後のスライダーへ戻す

/** 難しさのスライダー（左から 着実・標準・チャレンジング。押せる点を3つ並べた形）。押すとその段階の項目を settings/save で
 *  まとめて保存し、onSaved(新しい値) を呼ぶ（呼ぶ側が描き直す）。だめなら onError(エラーの種類)。
 *  今の値がどの段階とも合わない時（個別に変えた時）は、どの点にも印を付けず「カスタム」と出す */
export function difficultyControl(values, {preset, onSaved, onError, id = null}){
  const levels = P.diff.levels, cur = levelOf(values), idx = levels.findIndex(l => l.id === cur);
  let busy = false;
  const pick = async lv => {
    if(busy || lv.id === cur) return;
    busy = true;
    const r = await api('settings/save', {changes: lv.values, preset});
    busy = false;
    if(r.ok) onSaved(r.values); else if(onError) onError(r.error);
  };
  const onkeydown = e => {
    if(e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = idx < 0 ? 1 : Math.max(0, Math.min(levels.length - 1, idx + (e.key === 'ArrowRight' ? 1 : -1)));
    refocus = true;
    pick(levels[i]);
  };
  const stops = levels.map((lv, i) => h('button', {type: 'button', role: 'radio', dataset: {level: lv.id},
    class: 'diffStop s' + i + (i === idx ? ' on' : i < idx ? ' past' : ''), 'aria-checked': i === idx ? 'true' : 'false',
    title: levelGood(lv.id) + '（' + levelWhat(lv) + '）', onclick: () => pick(lv)},
    h('span', {class: 'diffKnob'}, h('span', {class: 'diffDot'})), h('span', {class: 'diffName'}, levelName(lv.id))));
  const slider = h('div', {class: 'diffSlider' + (idx < 0 ? ' custom' : ''), role: 'radiogroup', id,
    'aria-label': t('ps.diff', '難しさ'), onkeydown},
    h('span', {class: 'diffRail'}, h('span', {class: 'diffFill f' + Math.max(0, idx)})), stops);
  const now = idx < 0
    ? h('div', {class: 'diffNow custom'}, h('b', {}, t('ps.lv.custom', 'カスタム（個別に変えています）')),
      h('div', {class: 'diffVals'}, t('ps.lv.customHint', '段階を選ぶと、未プレイ曲の pass の上限・挑戦枠・クリアの見込みの下限をまとめて変えます')))
    : h('div', {class: 'diffNow'}, h('b', {}, levelName(cur)), '：', levelGood(cur), h('div', {class: 'diffVals'}, levelWhat(levels[idx])));
  const box = h('div', {class: 'diffCtl'}, slider, now);
  if(refocus){
    refocus = false;
    requestAnimationFrame(() => { const b = box.querySelector('.diffStop.on') || box.querySelector('.diffStop'); if(b && b.isConnected) b.focus(); });
  }
  return box;
}

/** 難しさ違いのセットを一度に作るボタン（押すと、作る段階と曲数を選ぶ小さな画面）。どの段階も同じ名前のセットが既にあれば出さない */
export function difficultySetButton(onCreated){
  const names = P.diff.levels.map(l => l.setName);
  if(!names.length || names.every(n => P.list.some(p => p.name === n))) return null;
  return h('button', {class: 'small diffSetBtn', onclick: () => difficultySetDialog(onCreated),
    title: t('ps.dsTip', '今のセットを写して、難しさだけを変えたセットを作ります（遊び比べる用）')}, t('ps.dsBtn', '難しさ違いのセットを作る'));
}

const DS_ERRORS = {
  'preset-exists': '同じ名前のセット（{names}）がもうあります。作り直す時は、先にそのセットを消すか名前を変えてください',
  'too-many-presets': 'セットは10個までです。選んだ数だけ足すと超えるので、使っていないセットを消してから作ってください',
  'bad-levels': '作る段階を1つ以上選んでください',
  'bad-value': '曲数は 1〜50 曲です',
};

/** 難しさ違いのセットを作る小さな画面。作る段階（同じ名前のセットがある段階は選べない）と曲数を選び、今のセットを写して作る。
 *  初期値はサーバーの setDefault・setSize（着実とチャレンジング・7曲）。作ったら onCreated(最初に作ったセットの id) */
export function difficultySetDialog(onCreated){
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const cur = current();
  const msg = h('p', {class: 'err small'});
  const checks = P.diff.levels.map(lv => {
    const exists = P.list.some(p => p.name === lv.setName);
    const cb = h('input', {type: 'checkbox', value: lv.id, disabled: exists, checked: !exists && P.diff.setDefault.includes(lv.id)});
    return h('label', {class: 'chk dsLevel' + (exists ? ' mute' : ''), dataset: {level: lv.id}}, cb,
      h('span', {}, h('b', {}, levelName(lv.id)), ' ', h('span', {class: 'mute'}, levelWhat(lv)),
        exists ? h('span', {class: 'mute'}, t('ps.dsExists', '（同じ名前のセットがもうあります）')) : null));
  });
  const size = h('input', {type: 'number', class: 'num', id: 'dsSize', min: 1, max: 50, step: 1, value: String(P.diff.setSize)});
  const ok = h('button', {class: 'primary', onclick: create}, t('ps.create', '作る'));
  const box = h('div', {class: 'modal narrow', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('ps.dsTitle', '難しさ違いのセットを作る')),
    h('p', {class: 'small'}, tf('ps.dsWhat', '「{name}」の設定を写して、難しさだけを変えたセットを作ります。', {name: cur ? cur.name : ''})),
    h('div', {class: 'dsLevels'}, h('div', {class: 'mute small'}, t('ps.dsLevels', '作る段階')), checks),
    h('label', {class: 'cond dsSize', for: 'dsSize'}, h('span', {}, t('ps.dsSize', '1つのセットの曲数')),
      h('span', {class: 'condCtl'}, size, h('span', {class: 'mute small'}, t('set.u.playlist_size', '曲')))),
    h('ul', {class: 'small dsList'},
      h('li', {}, t('ps.ds1', 'セットどうしで同じ曲（別の難易度も）は入れません（易しい方のセットから1曲ずつ交互に選びます）')),
      h('li', {}, t('ps.ds2', 'プレイリスト名とファイル名はセットごとに別にします（例: 次回 着実・blnext-steady.bplist）')),
      h('li', {}, t('ps.ds3', '「全部のセットを書き込む」でまとめてゲームに入れ、遊んだ後にホームの振り返りでセットごとの結果を比べられます'))),
    msg,
    h('div', {class: 'modalFoot'}, h('button', {onclick: close}, t('ps.cancel', 'やめる')), ok));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  ok.focus();

  async function create(){
    const levels = checks.map(c => c.querySelector('input')).filter(c => c.checked).map(c => c.value);
    if(!levels.length){ msg.textContent = t('ps.dsErr.bad-levels', DS_ERRORS['bad-levels']); return; }
    const n = Number(size.value);
    ok.disabled = true;
    const r = await api('presets/difficulty-set', {copyFrom: cur ? cur.id : null, levels, size: size.value === '' ? size.value : n});
    ok.disabled = false;
    if(!r.ok){
      msg.textContent = DS_ERRORS[r.error] ? tf('ps.dsErr.' + r.error, DS_ERRORS[r.error], {names: r.key || ''}) : presetErr(r.error);
      return;
    }
    P.list = r.presets; P.active = r.activePreset; P.group = r.group || P.group;
    close();
    onCreated && onCreated(r.activePreset, r.ids.length);
  }
}

// ---- 組のセット（「ほかのセットと同じ曲を入れない」がオンのセットどうし。難しさ違いのセットを遊び比べる時） ----
// 組全部を作り直す時は、易しいセットから1曲ずつ交互に選ぶ（A → B → B → A → A …。serve.py の _draft）

/** 開いているセットが組のセットか（このセットがオンで、ほかにもオンのセットがある）。on は開いているセットの今の値 */
export const inGroup = on => !!on && P.group.some(id => id !== P.active);

/** 組のセットの名前（選ぶ順）。開いているセットが P.group に無ければ（オンにした直後）最後に足す */
function groupNames(){
  const ids = P.group.includes(P.active) ? P.group : [...P.group, P.active];
  return ids.map(id => (P.list.find(p => p.id === id) || {}).name).filter(Boolean);
}

/** 組のセットで「作り直す」を押した時の小さな画面。「組のセット全部を交互に選び直す」か「このセットだけ作り直す」かを選ぶ。
 *  onPick(true＝組全部 / false＝このセットだけ)。やめた時は呼ばない */
export function rebuildDialog(onPick){
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const cur = current();
  const choice = (group, title, desc) => h('button', {class: 'rbChoice' + (group ? ' primary' : ''), dataset: {group: group ? 'all' : 'one'},
    onclick: () => { close(); onPick(group); }}, h('b', {}, title), h('span', {class: 'small'}, desc));
  const all = choice(true, t('ps.rbAll', '組のセット全部を交互に選び直す'),
    t('ps.rbAllDesc', '易しいセットから1曲ずつ交互に選びます（A → B → B → A → A … の順にして、先に選ぶ側が有利にならないようにします）。ほかのセットのリストも変わります'));
  const box = h('div', {class: 'modal narrow', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('ps.rbTitle', 'リストを作り直す')),
    h('p', {class: 'small'}, tf('ps.rbWhat', '「{name}」は、ほかのセットと同じ曲を入れない組のセットです（組: {names}）。', {
      name: cur ? cur.name : '', names: groupNames().join('・')})),
    h('div', {class: 'rbChoices'}, all,
      choice(false, t('ps.rbOne', 'このセットだけ作り直す'),
        t('ps.rbOneDesc', 'ほかのセットのリストはそのままにして、そこに無い曲から選びます'))),
    h('div', {class: 'modalFoot'}, h('button', {onclick: close}, t('ps.cancel', 'やめる'))));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  all.focus();
}
