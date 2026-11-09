// 画面の小道具

/** innerHTML へ差し込む文字列は必ず通す（APIから来た曲名・プレイヤー名には記号やタグが入っていることがある）。
 *  文字だけなら textContent を使うのが一番安全 */
export function escHtml(s){
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

export const $ = (sel, root = document) => root.querySelector(sel);

/** 要素を作る。attrs の on* は addEventListener（CSP で HTML 属性のハンドラは禁止）。children は文字列（textContent）か要素 */
export function h(tag, attrs = {}, ...children){
  const el = document.createElement(tag);
  for(const [k, v] of Object.entries(attrs || {})){
    if(v == null || v === false) continue;
    if(k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if(k === 'class') el.className = v;
    else if(k === 'dataset') Object.assign(el.dataset, v);
    else if(k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for(const c of children.flat()){
    if(c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** 数値の表示（桁区切り・小数の桁数） */
export function fmt(n, digits = 0){
  if(n == null || !Number.isFinite(+n)) return '—';
  return (+n).toLocaleString('ja-JP', {minimumFractionDigits: digits, maximumFractionDigits: digits});
}

/** 曲のカバー画像（serve.py の /cover/<hash>.jpg。アプリが BeatSaver の CDN から取ってきてためる。取れなければ代わりの絵が返る）。
 *  hash が 40桁の16進でなければ、代わりの絵をそのまま出す。大きさは CSS の .songCover（狭い時は小さくする）。
 *  読むのは見えた画像だけ（data-src に置いておき、見えたら src にする。COVER_MARGIN はウィンドウの端にだけ効き、本文や表の
 *  スクロールする箱の中では、箱に見えた時に読む）。loading="lazy" はブラウザが画面の数枚分先まで読むので、初めて開いた
 *  長い表（似ている人の共通曲など）で、見ない曲までアプリに BeatSaver へ取りに行かせていた */
export const NO_COVER = '/icons/no-cover.svg';
const COVER_MARGIN = '150px';
let coverIO = null;
const coverWatched = new Set();
function showCover(img){ coverWatched.delete(img); img.src = img.dataset.src; }
function watchCover(img){
  if(typeof IntersectionObserver !== 'function'){ showCover(img); return; }
  coverIO = coverIO || new IntersectionObserver(es => es.forEach(e => {
    if(e.isIntersecting){ coverIO.unobserve(e.target); showCover(e.target); }
  }), {rootMargin: COVER_MARGIN});
  if(coverWatched.size > 500){   // 描き直しで画面から外れた画像は見張りをやめる
    for(const x of coverWatched) if(!x.isConnected){ coverIO.unobserve(x); coverWatched.delete(x); }
  }
  coverWatched.add(img);
  coverIO.observe(img);
}
export function songCover(hash){
  const x = typeof hash === 'string' ? hash.toLowerCase() : '';
  const url = /^[0-9a-f]{40}$/.test(x) ? '/cover/' + x + '.jpg' : NO_COVER;
  const img = h('img', {class: 'songCover', decoding: 'async', width: 64, height: 64, alt: '', draggable: 'false', dataset: {src: url},
    onerror: () => { if(!img.src.endsWith(NO_COVER)) img.src = NO_COVER; }});   // サーバーに届かなかった時も代わりの絵に
  if(url === NO_COVER) img.src = NO_COVER;
  else watchCover(img);
  return img;
}
