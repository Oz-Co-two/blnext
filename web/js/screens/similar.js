// 似ている人の画面: 上位の人（類似度・共通曲数・国内順位・acc/pass/tech の比率）と、選んだ人との共通曲での精度の比較
import { api } from '../api.js';
import { t, tf } from '../i18n.js';
import { h, fmt, songCover } from '../util.js';
import * as refresh from '../refresh.js';
import { P, load as loadPresets, tabs as presetTabs } from '../presets.js';

let sel = null;

export async function render(main){
  await loadPresets();
  const r = await api('similar', {preset: P.active});   // 似ている人の数え方はセットの計算の設定による
  if(!r.ok || !r.hasData){
    main.append(h('section', {class: 'card empty'}, h('h2', {}, t('sim.noData', 'まだ計算できません')),
      h('p', {class: 'mute'}, refresh.running() ? t('rec.loading', 'データを取得しています。終わると自動で表示します。')
        : t('rec.noDataHint', 'プレイヤーを設定し、データを取得してください。'))));
    return;
  }
  const style = st => st && st[0] != null
    ? h('span', {class: 'style', title: 'acc ' + Math.round(100 * st[0]) + '% / pass ' + Math.round(100 * st[1]) + '% / tech ' + Math.round(100 * st[2]) + '%'},
      h('span', {class: 'st acc', style: `width:${100 * st[0]}%`}), h('span', {class: 'st pass', style: `width:${100 * st[1]}%`}),
      h('span', {class: 'st tech', style: `width:${100 * st[2]}%`}))
    : '—';
  const pctS = st => st && st[0] != null ? st.map(x => Math.round(100 * x)).join(' / ') : '';
  const detail = h('div', {});
  const rows = r.players.map(p => h('tr', {class: (p.similar ? 'simRow' : 'mute') + (sel === p.id ? ' sel' : ''),
    onclick: e => { document.querySelectorAll('tr.sel').forEach(x => x.classList.remove('sel')); e.currentTarget.classList.add('sel'); pick(p, detail); }},
    h('td', {}, p.similar ? '★' : ''),
    h('td', {class: 'name'}, p.name),
    h('td', {class: 'num'}, '#' + fmt(p.countryRank)),
    h('td', {class: 'num'}, p.sim == null ? '—' : fmt(p.sim, 3)),
    h('td', {class: 'num'}, p.corr == null ? '—' : fmt(p.corr, 3)),
    h('td', {class: 'num'}, fmt(p.overlap)),
    h('td', {}, style(p.style))));
  main.append(...[
    P.list.length > 1 ? presetTabs(() => { main.replaceChildren(); render(main); }, null, false) : null,
    h('div', {class: 'pageHead'}, h('h1', {}, t('sim.title', '似ている人')),
      h('span', {class: 'mute'}, tf('sim.sub', '比較対象 {n} 人のうち、共通曲での上振れ・下振れがあなたと似ている順', {n: r.poolSize}))),
    h('div', {class: 'grid2 simGrid'},
      h('section', {class: 'card'},
        h('div', {class: 'mute small simMe'}, tf('sim.me', 'あなた: acc / pass / tech = {s}', {s: pctS(r.me.style)}), ' ', style(r.me.style),
          h('span', {class: 'legend'}, h('i', {class: 'st acc'}), 'acc', h('i', {class: 'st pass'}), 'pass', h('i', {class: 'st tech'}), 'tech')),
        h('table', {class: 'list'},
          h('thead', {}, h('tr', {}, h('th', {}), h('th', {}, t('sim.th.name', '名前')), h('th', {class: 'num'}, t('sim.th.rank', '国内')),
            h('th', {class: 'num'}, t('sim.th.sim', '類似度')), h('th', {class: 'num'}, t('sim.th.corr', '相関')),
            h('th', {class: 'num'}, t('sim.th.overlap', '共通曲')), h('th', {}, t('sim.th.style', 'PP の内訳')))),
          h('tbody', {}, rows)),
        h('p', {class: 'mute small'}, t('sim.note', '★ = 未プレイの曲の推定に使う人。類似度 = 共通曲での残差の相関 × 共通曲が少ないほど0へ寄せる係数。PP の内訳の近さは参考（今は順位付けに使っていない）。'))),
      detail)].filter(Boolean));
  if(sel){ const p = r.players.find(x => x.id === sel); if(p) pick(p, detail); }
  else detail.append(h('section', {class: 'card empty'}, h('p', {class: 'mute'}, t('sim.pick', '左の一覧から人を選ぶと、共通曲での精度を比べます。'))));
}

async function pick(p, detail){
  sel = p.id;
  const r = await api('similar/compare', {id: p.id, preset: P.active});
  if(!r.ok){ detail.replaceChildren(); return; }
  const better = r.maps.filter(m => (m.myRes ?? 0) > (m.theirRes ?? 0)).length;
  detail.replaceChildren(h('section', {class: 'card'},
    h('div', {class: 'cardHead'}, h('h2', {}, tf('sim.cmpTitle', '{name} さんとの共通曲', {name: r.name})),
      h('span', {class: 'mute small'}, tf('sim.cmpN', '{n} 曲（あなたの方が上振れ {b} 曲）', {n: r.maps.length, b: better}))),
    // 精度と上振れは人ごとに1つの欄に2段で出す（7列だと右の欄が箱からはみ出して見えなかった）。画像もこの表だけ小さめ（CSS の .simCmp）
    h('div', {class: 'scrollBox'}, h('table', {class: 'list simCmp'},
      h('thead', {}, h('tr', {}, h('th', {class: 'coverCell'}), h('th', {}, t('sim.th.song', '曲・難易度')), h('th', {class: 'num'}, '★'),
        h('th', {class: 'num'}, t('sim.th.mine', 'あなた'), h('div', {class: 'sub'}, t('sim.th.accRes', '精度・上振れ'))),
        h('th', {class: 'num'}, t('sim.th.theirs', 'その人'), h('div', {class: 'sub'}, t('sim.th.accRes', '精度・上振れ'))))),
      h('tbody', {}, r.maps.map(m => h('tr', {},
        h('td', {class: 'coverCell'}, songCover(m.hash)),
        h('td', {class: 'name'}, m.song || m.id, ' ', h('span', {class: 'mute'}, m.difficulty || '')),
        h('td', {class: 'num'}, fmt(m.stars, 2)),
        h('td', {class: 'num'}, m.myAcc == null ? '—' : fmt(100 * m.myAcc, 2) + '%', h('div', {class: 'sub'}, res(m.myRes))),
        h('td', {class: 'num'}, m.theirAcc == null ? '—' : fmt(100 * m.theirAcc, 2) + '%', m.theirMods ? h('span', {class: 'mute'}, ' ' + m.theirMods) : null,
          h('div', {class: 'sub'}, res(m.theirRes))))))))));
}

function res(v){ return v == null ? '—' : (v >= 0 ? '+' : '') + fmt(100 * v, 2) + '%'; }
