// アプリ本体の更新の小さな画面（NLM の update-dialog.js を流用）。確認・ダウンロード・差し替えは serve.py → core/app_update.py。
// 起動時の確認（設定 update_check で切れる）は exe 版だけ・通信の失敗は何も出さない。「更新しない」を選んだ版は設定 update_skip に残し、
// それより新しい版が出るまで起動時には出さない（設定の「更新を確認」からはいつでも更新できる）。
// 更新内容は外から来る文字なので、h() の文字（textContent）で入れる
import { api, native } from './api.js';
import { t, tf } from './i18n.js';
import { h } from './util.js';
import { toast } from './main.js';

const ERR = {
  network: 'GitHub に接続できませんでした。ネットの接続を確かめてください。',
  notFound: '配布元に更新情報（update.json）が見つかりませんでした。',
  badManifest: '配布元の更新情報（update.json）が読めませんでした。',
  tooLarge: 'ファイルが大きすぎます。',
  hash: 'ダウンロードしたファイルが配布元の情報と一致しません（壊れているか改ざんの可能性）。更新を中止しました。',
  badZip: 'ダウンロードしたファイルの中身が正しくありません。更新を中止しました。',
  extract: 'ダウンロードしたファイルを展開できませんでした。',
  notAuto: 'この環境では自動で更新できません。',
  noUpdate: '新しい版はありません。',
  notReady: '更新の準備ができていません。',
  spawn: '更新を始められませんでした。',
};
const errText = r => t('upd.err.' + r.error, ERR[r.error] || r.error) + (r.detail ? ` (${r.detail})` : '');
const mb = n => (n / 1048576).toFixed(1);
const sleep = ms => new Promise(r => setTimeout(r, ms));

let back = null;
function dialog(title, ...body){
  if(back) back.remove();
  const box = h('div', {class: 'modal updDlg', role: 'dialog', 'aria-modal': 'true'}, h('h2', {}, title), ...body);
  back = h('div', {class: 'modalBack'}, box);
  document.body.append(back);
}
function close(){ if(back) back.remove(); back = null; }
const foot = (...btns) => h('div', {class: 'modalFoot'}, ...btns);
function info(title, text, isErr){
  dialog(title, h('p', {class: isErr ? 'err' : ''}, text), foot(h('button', {class: 'primary', onclick: close}, t('bs.close', '閉じる'))));
}

/** 起動時: 更新の直後なら知らせ、exe 版で設定がオンなら少し待ってから確かめる（画面が開いてから出す） */
export async function autoCheck(status){
  if(status.justUpdated) toast(tf('upd.done', '{v} に更新しました', {v: status.justUpdated.to}), 'ok');
  if(!status.frozen || !status.updateCheck) return;
  await sleep(3000);
  const r = await api('update/check');
  if(r.ok && r.newer && !r.skipped && !document.querySelector('.modalBack')) prompt(r);
}

/** 設定の「更新を確認」。結果は必ず出す。「更新しない」を選んだ版も出す */
export async function manualCheck(){
  const title = t('upd.checkTitle', '更新の確認');
  dialog(title, h('p', {}, t('upd.checking', '確認しています…')));
  const r = await api('update/check');
  if(!r.ok) return info(title, errText(r), true);
  if(r.newer) return prompt(r);
  info(title, tf('upd.upToDate', '最新の版です（{v}）。', {v: r.current}));
}

function prompt(r){
  const notes = r.notes || [];
  const skip = async () => { await api('settings/save', {changes: {update_skip: r.latest}}); close(); };
  const why = {readonly: t('upd.whyReadonly', 'アプリのフォルダに書き込めないため、自動では更新できません。'),
    manual: t('upd.whyManual', 'この版は自動では更新できません。'),
    notExe: t('upd.whyNotExe', '開発用の起動方法（python serve.py）では自動で更新しません。')}[r.auto] || '';
  dialog(tf('upd.newTitle', '新しい版 {v} があります', {v: r.latest}),
    h('p', {class: 'mute small'}, tf('upd.current', '今の版: {v}', {v: r.current})),
    h('div', {class: 'updNotes'}, notes.length ? notes.map(n => [h('h3', {}, n.version),
      h('ul', {}, (n.ja || []).map(s => h('li', {}, s)))]).flat() : h('p', {}, t('upd.noNotes', '更新内容の情報がありません。'))),
    r.auto === 'ok'
      ? h('p', {class: 'small'}, tf('upd.hint', '「更新する」を押すと、ダウンロード（{mb}MB）してからアプリを再起動して更新します。設定とデータはそのまま残ります。', {mb: mb(r.size)}))
      : h('p', {class: 'small'}, why, t('upd.manualHint', 'リリースページから zip をダウンロードして更新してください。')),
    foot(h('button', {onclick: skip}, t('upd.skip', '更新しない')),
      r.auto === 'ok' ? h('button', {class: 'primary', onclick: () => run(r)}, t('upd.update', '更新する'))
        : h('button', {class: 'primary', onclick: () => { api('update/open', {what: 'releases'}); close(); }}, t('upd.openReleases', 'リリースページを開く'))));
}

/** ダウンロード → 差し替え役の起動 → アプリを閉じる */
async function run(r){
  const title = tf('upd.dlTitle', '{v} に更新しています', {v: r.latest});
  const msg = h('p', {class: 'updMsg'}, t('upd.downloading', 'ダウンロードしています…'));
  const fill = h('div', {class: 'stripFill', style: 'width:0%'});
  const cancel = h('button', {onclick: () => { cancel.disabled = true; api('update/cancel'); }}, t('upd.cancel', 'やめる'));
  dialog(title, msg, h('div', {class: 'stripBar updBar'}, fill), foot(cancel));
  const fail = text => info(t('upd.failTitle', '更新できませんでした'), text, true);
  let p = await api('update/download');
  while(p.ok && (p.state === 'download' || p.state === 'extract')){
    if(p.state === 'download' && p.total){
      fill.style.width = (100 * p.done / p.total).toFixed(1) + '%';
      msg.textContent = tf('upd.dlProgress', 'ダウンロードしています… {d} / {t}MB', {d: mb(p.done), t: mb(p.total)});
    }else if(p.state === 'extract'){
      fill.style.width = '100%';
      msg.textContent = t('upd.extracting', '展開しています…');
      cancel.disabled = true;
    }
    await sleep(300);
    p = await api('update/progress');
  }
  if(p.state === 'cancel') return close();
  if(!p.ok || p.state !== 'ready') return fail(errText(p));
  msg.textContent = t('upd.restarting', '更新するため、アプリを再起動します…');
  cancel.disabled = true;
  const a = await api('update/apply');
  if(!a.ok) return fail(errText(a));
  await sleep(300);
  const n = await native();
  if(n && n.quit_app) n.quit_app();
}
