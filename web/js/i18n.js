// 表示言語（NLM と同じ仕組み）。
//  - JS の文言は t('キー','日本語')／差し込み穴つきは tf('キー','{n}人',{n})。日本語はコードに直書きし、訳は lang/<言語>.json だけに置く
//  - HTML は data-i18n（文字）/ data-i18n-t（title）/ data-i18n-ph（placeholder）。日本語は要素の中身がそのまま既定値
//  - 訳文に '<' があれば捨てて既定文にする（訳文も innerHTML に入ることがあるため）
//  - 一度だけ組み立てる画面に文言を入れる時は data-i18n を付ける（付けないと言語を切り替えても前の言語のまま残る）
let LANG = {};
export let langCode = 'ja';

export function t(k, def){ return Object.prototype.hasOwnProperty.call(LANG, k) ? LANG[k] : def; }

export function tf(k, def, vars){
  let s = t(k, def);
  if(vars) for(const v in vars) s = s.split('{' + v + '}').join(String(vars[v]));
  return s;
}

export async function loadLang(code){
  langCode = code === 'en' ? 'en' : 'ja';
  LANG = {};
  if(langCode !== 'ja'){
    try{ LANG = await (await fetch('lang/' + langCode + '.json', {cache: 'no-cache'})).json(); }
    catch(e){ LANG = {}; console.warn('lang', e); }
    for(const k in LANG){ if(typeof LANG[k] !== 'string' || LANG[k].includes('<')) delete LANG[k]; }
  }
  document.documentElement.lang = langCode;
  applyI18n(document);
}

const ORIG = new WeakMap();   // 日本語の既定値（言語を行き来しても元へ戻せるように）
export function applyI18n(root){
  const pick = (el, attr, get, set) => {
    let o = ORIG.get(el); if(!o){ o = {}; ORIG.set(el, o); }
    if(!(attr in o)) o[attr] = get();
    set(t(el.dataset[attr], o[attr]));
  };
  root.querySelectorAll('[data-i18n]').forEach(el => pick(el, 'i18n', () => el.textContent, v => { el.textContent = v; }));
  root.querySelectorAll('[data-i18n-t]').forEach(el => pick(el, 'i18nT', () => el.title, v => { el.title = v; }));
  root.querySelectorAll('[data-i18n-ph]').forEach(el => pick(el, 'i18nPh', () => el.placeholder, v => { el.placeholder = v; }));
}
