// BeatLeader の統計（Score history）を公開にしてもらう案内。初回の準備（setup.js）・ホームの知らせ（home.js）・プレイリストを作る／出す操作の後の警告（recommend.js）で使う。
// 警告とホームの知らせを止めるのは設定 stats_notice だけ。**小さな画面・知らせにオフにするボタンを置かない**（利用者の指示。文面で場所だけ伝える）。
// 非公開だと挑戦の記録（scoresstats）が 401 で読めず、クリアの見込み・クールダウンなどが効かない。認証情報は預からない（公開にしてもらう）。
// 中身の根拠は 作者の調べ「非公開の人の挑戦の記録」。公開の切り替えは無料で、過去の分も読めるようになる（ソースから読み、公開している無料の人の実物でも確かめた。切り替えた瞬間の動きは未確認）
import { api } from './api.js';
import { t } from './i18n.js';
import { h } from './util.js';

/** 案内の本文（要素の並び） */
export function guide(){
  return [
    h('p', {}, t('stats.why', 'BeatLeader の統計が非公開のため、失敗や途中でやめた挑戦の記録を読めません。このままだと次の働きが効きません。')),
    h('ul', {class: 'statsList'},
      h('li', {}, t('stats.w1', 'クリアの見込み（難しすぎる曲を避ける・着実の「見込み 80% 以上」）')),
      h('li', {}, t('stats.w2', 'クールダウン（同じ曲を続けて出さない）')),
      h('li', {}, t('stats.w3', '失敗が続いた曲を下げる・遊んだのに伸びなかった曲の振り返り'))),
    ...howTo(),
    h('p', {class: 'mute small'}, t('stats.effect', 'オンにすると、挑戦の回数や失敗した記録、遊んだ日時をほかの人も見られるようになります。順位や PP は変わりません。いつでも戻せます。')),
  ];
}

/** 変え方（手順と、BeatLeader の設定の画面の画像）。案内の小さな画面・初回の準備と、使い方の画面（screens/guide.js）で使う。
 *  画像 web/guide/bl-public-history.png は、利用者が 2026-11-09 に撮った BeatLeader の設定の画面（beatleader.com/settings#scores）から、
 *  個人の情報の無い所（左の項目・SCORE HISTORY の行・Save）だけを切り出して番号を付けた物。BeatLeader の画面が変わったら撮り直す。
 *  Save を押さないと保存されない（2026-11-09 まで文に無かった）。隣の Show history は自分の画面の表示だけで、公開とは別 */
export function howTo(){
  return [
    h('p', {class: 'statsHowHead'}, t('stats.howHead', '変え方（無料・過去の分も読めるようになります）:')),
    h('ol', {class: 'statsSteps'},
      h('li', {}, t('stats.how1', 'beatleader.com にログインし、右上の Settings を開いて、左の「Scores」を選ぶ（下の「BeatLeader の設定を開く」でこの画面が開きます）')),
      h('li', {}, t('stats.how2', '「SCORE HISTORY」の「Public history (auto-synced)」をオンにする（左の Show history ではありません）')),
      h('li', {}, t('stats.how3', '画面の一番下の「Save」を押す（押さないと保存されません）'))),
    h('img', {class: 'statsImg', src: 'guide/bl-public-history.png', width: 848, height: 443, alt: t('stats.imgAlt',
      'BeatLeader の設定の画面。1: 左の Scores、2: SCORE HISTORY の Public history (auto-synced) のスイッチ、3: 一番下の Save')}),
  ];
}

/** 見込みの数字が大まかな時の説明（おすすめのまとめ・初回の準備）。無ければ null。x はリストの fewSimilar・noClear */
export function roughText(x){
  if(x.fewSimilar && x.noClear) return t('stats.roughBoth', '比べられる人が少なく、クリアの見込みもまだ無いので、推定と見込みの数字は大まかな目安です。');
  if(x.fewSimilar) return t('stats.roughSimilar', '比べられる人が少ないので、未プレイの曲の推定と見込みの数字は大まかな目安です。');
  if(x.noClear) return t('stats.roughClear', 'クリアの見込みがまだ無いので、全部クリアできた場合の数字です。');
  return null;
}

/** 「BeatLeader の設定を開く」のボタン */
export const openButton = () => h('button', {class: 'statsOpen', onclick: () => api('open/bl-settings')}, t('stats.open', 'BeatLeader の設定を開く'));

/** 「オンにした（確かめる）」のボタン。BeatLeader に確かめて、公開なら onPublic()、まだなら msg に知らせる */
export function checkButton(msg, onPublic){
  const btn = h('button', {class: 'primary statsCheck', onclick: async () => {
    btn.disabled = true;
    msg.textContent = '';
    const r = await api('stats/check');
    btn.disabled = false;
    if(!r.ok){ msg.textContent = t('stats.checkFail', 'BeatLeader に確かめられませんでした。少し待ってからもう一度押してください。'); return; }
    if(r.statsPublic === false){ msg.textContent = t('stats.stillPrivate', 'まだ非公開と返ってきました。オンにしてから少し待って、もう一度押してください。'); return; }
    onPublic();
  }}, t('stats.check', 'オンにした（確かめる）'));
  return btn;
}

/** 警告を止める場所の説明（設定の項目の名前は settings.js の LABELS の stats_notice と同じ。テストが確かめる） */
export const offHint = () => t('stats.offHint', 'この警告は、設定の「通信」にある「統計が非公開の時に警告する」で止められます。');

/** プレイリストを作る・出す操作が終わった後の警告（毎回出す。操作は止めない）。統計が非公開で設定 stats_notice がオンの時だけ。
 *  毎回サーバーに聞く（設定を変えた・取得で印が変わったのが、開き直さなくてもその場で効くように）。失敗した操作の後には呼ばない */
export async function warnPrivate(onPublic){
  const s = await api('status');
  if(!s.ok || !s.statsPrivate || !s.statsNotice || document.querySelector('.statsDlg')) return;
  dialog(onPublic, t('stats.warnLead', 'プレイリストの操作は終わっています（このまま使えます）。'));
}

/** 小さな画面（ホームの知らせの「公開にする方法」と、上の警告）。公開を確かめたら onPublic()。lead は本文の前に出す一言。
 *  書き込みの結果などのトーストは、この画面より手前に出る（base.css の z-index）ので隠れない */
export function dialog(onPublic, lead = null){
  const msg = h('p', {class: 'err small', role: 'status'});
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const closeBtn = h('button', {onclick: close}, t('bs.close', '閉じる'));
  const box = h('div', {class: 'modal statsDlg', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('stats.title', '統計を公開にしてください')), lead ? h('p', {class: 'small'}, lead) : null, guide(),
    h('p', {class: 'mute small'}, offHint()), msg,
    h('div', {class: 'modalFoot'}, closeBtn, openButton(), checkButton(msg, () => { close(); onPublic(); })));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  closeBtn.focus();   // 押したボタンに当たったままだと、スペース・Enter でもう一度出力してしまう
}
