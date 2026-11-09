// 文字の大きさ（設定の ui_scale）。上の帯のスライダーと設定画面で変える。
//  - 文字だけを大きくすると、幅を決めた列（おすすめの一覧など）からはみ出すので、画面全体を拡大する（html の CSS zoom）
//  - 拡大すると、部品を並べられる幅（CSS の px）は「ウィンドウの幅 ÷ 倍率」に狭まる。狭い時のレイアウトは base.css の
//    @container（#main の幅で切り替える。@media はウィンドウの幅を見るので拡大に合わない）
//  - ウィンドウが狭い時は、並べられる幅が MIN_W より狭くならないよう倍率を自動で控えめにする（崩れないように）
import { api } from './api.js';
import { t, tf } from './i18n.js';

export const STEPS = [100, 115, 130, 145];
export const MIN_W = 720;   // 拡大した後に部品を並べられる幅の下限（CSS の px。画面のテストはこの幅で崩れないことを確かめる）

let chosen = 100;

/** 実際に使う倍率（%）。選んだ倍率と、ウィンドウの幅から決まる上限の小さい方 */
export function effective(){
  return Math.min(chosen, Math.max(100, Math.floor(innerWidth / MIN_W * 100)));
}

export function set(v){
  chosen = STEPS.includes(v) ? v : 100;
  apply();
}

function apply(){
  document.documentElement.style.zoom = String(effective() / 100);
  sync();
}

/** 上の帯のスライダーを今の値に合わせ、説明（title）を付ける */
function sync(){
  const el = document.getElementById('fontScale');
  if(!el) return;
  el.value = String(STEPS.indexOf(chosen));
  const eff = effective();
  const box = el.closest('.fontCtl');
  const tip = eff < chosen
    ? tf('top.scaleCapped', '文字の大きさ: {v}%（ウィンドウが狭いので、崩れないよう {e}% で表示しています）', {v: chosen, e: eff})
    : tf('top.scale', '文字の大きさ: {v}%', {v: chosen});
  if(box) box.title = tip;
  el.setAttribute('aria-valuetext', chosen + '%');
  const sel = document.getElementById('set_ui_scale');   // 設定画面を開いている時はそちらも合わせる
  if(sel) sel.value = String(chosen);
}

/** 上の帯のスライダーをつなぐ。動かしている間は見え方だけ変え、離した時に保存する */
export function bind(){
  const el = document.getElementById('fontScale');
  if(!el) return;
  el.addEventListener('input', () => { chosen = STEPS[+el.value] || 100; apply(); });
  el.addEventListener('change', async () => {
    chosen = STEPS[+el.value] || 100;
    apply();
    await api('settings/save', {changes: {ui_scale: chosen}});
  });
  addEventListener('resize', apply);
  sync();
}

export const label = v => t('scale.' + v, ({100: '標準（100%）', 115: '大（115%）', 130: 'より大（130%）', 145: '最大（145%）'})[v] || v + '%');
