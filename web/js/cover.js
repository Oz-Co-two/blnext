// プレイリストの画像（セットごと）。おすすめ画面（まとめの欄の画像を押す）と設定画面（セットの「画像を選ぶ…」）で共通に使う。
//  - 選べるのは、アプリのアイコン（緑）・用意した色違い（serve.py の COVER_PRESETS。web/icons/covers/<名前>.png）・ファイルから読み込んだ画像
//  - 用意した画像はサーバーへ名前だけを送る（cover/preset）。ファイルは画面で正方形に切り抜いて縮めた中身だけを送る（cover/set）。
//    どちらも場所（パス）は送らない（CLAUDE.md の「ローカルサーバーの守り」）
//  - 用意した画像のタイルは公開のフォルダ（icons）をそのまま見せる。今の画像（読み込んだ画像も）は API が data: URL で返す
import { api } from './api.js';
import { t, tf } from './i18n.js';
import { h } from './util.js';
import { presetErr } from './presets.js';

// タイルの下に出す短い名前（'app' はアプリのアイコン）。知らない名前（サーバーに足した物）は名前をそのまま出す
const NAMES = {app: '緑', blue: '青', purple: '紫', orange: '橙', red: '赤', slate: '灰'};
const ERRORS = {
  'bad-image': '画像を読めませんでした（PNG か JPEG を選んでください）',
  'bad-request': '入力が正しくありません',
  network: 'サーバーにつながりません',
};
const coverErr = code => ERRORS[code] ? t('cv.err.' + code, ERRORS[code]) : presetErr(code);
const tileName = name => t('cv.n.' + name, NAMES[name] || name);
const tileSrc = name => name === 'app' ? 'icons/app.png' : 'icons/covers/' + encodeURIComponent(name) + '.png';

/** 押すと画像を選ぶ小さな画面を開く、プレイリストの画像。変えたら絵を差し替えて onChange(新しい data: URL) を呼ぶ。
 *  src は今の画像（data: URL。まだ無ければ null で、後から img の src を入れる）。big は設定画面の大きい方 */
export function coverButton({src, preset, name = '', big = false, onChange = null}){
  const img = h('img', {class: 'coverThumb' + (big ? ' big' : ''), src: src || null, alt: ''});
  return h('button', {type: 'button', class: 'coverBtn', title: t('cv.tip', '画像を変える'),
    'aria-label': t('cv.tipLong', 'プレイリストの画像を変える'),
    onclick: () => chooseCover({preset, name, onChange: url => { if(url) img.src = url; if(onChange) onChange(url); }})},
    // 上に乗せた時の「変える」は CSS で出す（ボタンの文字に入れると、同じ画面の「変える」（出力先）と紛れる）
    img, h('span', {class: 'coverHint', 'aria-hidden': 'true', dataset: {hint: t('cv.hint', '変える')}}));
}

/** 画像を選ぶ小さな画面。タイルを押すとすぐ変えて印を移す（閉じるまで何度でも選び直せる）。変えるたびに onChange(新しい data: URL)。
 *  読み込んだ画像は、ほかの画像を選んでも、この画面を開いている間はタイルが残り、押すと戻せる（閉じると選べなくなる） */
export function chooseCover({preset, name = '', onChange = null}){
  const opener = document.activeElement;
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onEsc);
    if(opener && opener.isConnected && opener.focus) opener.focus();
  };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  let cur;              // 今の画像（'app'・用意した画像の名前・null＝読み込んだ画像）
  let custom = null;    // 読み込んだ画像（data: URL）
  let busy = false;
  const msg = h('p', {class: 'err small coverMsg', role: 'alert'});
  const tiles = h('div', {class: 'coverTiles', role: 'group', 'aria-label': t('cv.presets', '用意した画像')});
  const customBox = h('div', {class: 'coverCustom'});
  const file = h('input', {type: 'file', accept: 'image/png,image/jpeg', hidden: true, onchange: onFile});
  const box = h('div', {class: 'modal coverDlg', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'coverDlgTitle'},
    h('h2', {id: 'coverDlgTitle'}, t('cv.title', 'プレイリストの画像')),
    h('p', {class: 'mute small'}, tf('cv.what',
      '「{name}」のセットの画像です。ゲーム内のプレイリストの一覧に出るので、セットごとに色を変えると見分けやすくなります。押すとすぐ変わります。',
      {name})),
    tiles,
    h('div', {class: 'coverFile'}, customBox,
      h('div', {class: 'coverFileCtl'},
        h('button', {type: 'button', class: 'coverFileBtn', onclick: () => file.click()}, t('cv.file', 'ファイルから読み込む…')),
        h('span', {class: 'mute small'}, t('cv.fileHint', '好きな画像を使います（PNG・JPEG）。正方形に切り抜いて、512×512 までに縮めます。')),
        file)),
    msg,
    h('div', {class: 'modalFoot'}, h('button', {type: 'button', class: 'coverClose', onclick: close}, t('cv.close', '閉じる'))));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);
  load();

  async function load(){
    const r = await api('cover/get', {preset});
    if(!r.ok){ msg.textContent = coverErr(r.error); return; }
    tiles.replaceChildren(...['app', ...(r.presets || [])].map(n => tile(n, tileSrc(n), tileName(n),
      n === 'app' ? t('cv.appTip', 'アプリのアイコン（緑）。選ばなければこの画像です') : tileName(n), () => pick(n))));
    cur = r.choice;
    if(cur === null) custom = r.dataUrl;
    mark();
    const on = box.querySelector('.coverTile.on') || box.querySelector('.coverTile');
    if(on && back.isConnected) on.focus();
  }

  function tile(id, src, label, title, onclick){
    return h('button', {type: 'button', class: 'coverTile', dataset: {cover: id}, title, 'aria-pressed': 'false', onclick},
      h('img', {src, alt: ''}), h('span', {class: 'coverName'}, label));
  }

  // 今の画像に印（読み込んだ画像のタイルは、読み込んだ画像がある時だけ）
  function mark(){
    const had = customBox.querySelector('.coverTile') === document.activeElement;
    customBox.replaceChildren(...(custom ? [tile('custom', custom, t('cv.custom', '読み込んだ画像'),
      cur === null ? t('cv.customTip', 'ファイルから読み込んだ画像')
        : t('cv.customBack', 'さっき読み込んだ画像に戻します（この画面を閉じると選べなくなります）'), pickCustom)] : []));
    for(const b of box.querySelectorAll('.coverTile')){
      const on = b.dataset.cover === (cur === null ? 'custom' : cur);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    if(had){ const b = customBox.querySelector('.coverTile'); if(b) b.focus(); }
  }

  function changed(r){
    cur = r.choice;
    if(cur === null) custom = r.dataUrl;
    mark();
    if(onChange) onChange(r.dataUrl);
  }

  async function send(name, body){
    if(busy) return;
    busy = true;
    msg.textContent = '';
    let r;
    try { r = await api(name, {preset, ...body}); } finally { busy = false; }
    if(!r.ok){ msg.textContent = coverErr(r.error); return; }
    changed(r);
  }

  function pick(n){ if(n !== cur) send('cover/preset', {name: n}); }

  // 読み込んだ画像に戻す（中身をもう一度送る。場所は送らない）
  function pickCustom(){ if(cur !== null && custom) send('cover/set', {data: custom.slice(custom.indexOf(',') + 1)}); }

  async function onFile(e){
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if(!f || busy) return;
    busy = true;
    msg.textContent = '';
    let r;
    try { r = await uploadCover(preset, f); } finally { busy = false; }
    if(!r.ok){ msg.textContent = coverErr(r.error); return; }
    changed(r);
  }
}

/** 画像を正方形に切り抜いて 512×512 までに縮め、PNG（元が PNG の時。透明を残す）か JPEG にして送る（cover/set。応答は cover/get と同じ形） */
export async function uploadCover(preset, f){
  if(f.size > 40 * 1024 * 1024) return {ok: false, error: 'bad-image'};
  let bmp;
  try{ bmp = await createImageBitmap(f); }catch(_){ return {ok: false, error: 'bad-image'}; }
  const side = Math.min(bmp.width, bmp.height), size = Math.min(512, side);
  const cv = document.createElement('canvas');
  cv.width = cv.height = size;
  cv.getContext('2d').drawImage(bmp, (bmp.width - side) / 2, (bmp.height - side) / 2, side, side, 0, 0, size, size);
  if(bmp.close) bmp.close();
  const url = cv.toDataURL(f.type === 'image/png' ? 'image/png' : 'image/jpeg', 0.9);
  return api('cover/set', {preset, data: url.slice(url.indexOf(',') + 1)});
}
