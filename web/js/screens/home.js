// ホーム画面: 前回のプレイリストの結果（一番上）、今の国内順位・PP、目標までの差、上下の人、国内のPPの分布、前回からの変化
// （docs/spec.md の「画面仕様」）。タイルと上下の人には「前回の出力から」（一番新しい出力の時点の取得と比べた）の差も出す
import { api } from '../api.js';
import { t, tf } from '../i18n.js';
import { h, fmt, songCover } from '../util.js';
import * as refresh from '../refresh.js';
import * as setup from '../setup.js';
import * as stats from '../stats.js';

export async function render(main, ctx){
  if(!ctx.status.playerId){
    main.append(h('section', {class: 'card empty'},
      h('h2', {}, t('home.noPlayer', 'プレイヤーが未設定です')),
      h('p', {}, t('home.noPlayerHint', 'BeatLeader のプレイヤーを名前か ID で探して選んでください。')),
      h('div', {class: 'btnRow'}, h('button', {class: 'primary', onclick: () => setup.open(ctx.status)}, t('home.toSetup', 'はじめの準備')),
        h('button', {onclick: () => ctx.show('settings')}, t('home.toSettings', '設定へ')))));
    return;
  }
  const d = await api('home');
  if(!d.ok || !d.hasData){
    main.append(h('section', {class: 'card empty'},
      h('h2', {}, refresh.running() ? t('home.loading', 'データを取得しています…') : t('home.noData', 'まだデータがありません')),
      h('p', {class: 'mute'}, refresh.running()
        ? t('home.loadingHint', '初回は比較対象（最大50人）のスコアを取得するので、1分ほどかかります。')
        : t('home.noDataHint', '「更新」を押すと BeatLeader から取得します。')),
      refresh.running() ? null : h('button', {class: 'primary', onclick: () => refresh.start()}, t('home.refresh', '更新'))));
    return;
  }
  const me = d.me;
  main.append(...[   // null（警告が無い時）は DOM の append に渡すと "null" の文字になるので除く
    h('div', {class: 'pageHead'},
      h('h1', {}, tf('home.hello', '{name} さんの今', {name: me.name})),
      h('span', {class: 'mute when'}, d.dataAt ? tf('home.dataAt', '{t} 時点のデータ', {t: when(d.dataAt)}) : '')),
    ctx.status.statsPrivate && ctx.status.statsNotice ? statsNotice(ctx) : null,
    d.ppCheck && !d.ppCheck.ok ? h('div', {class: 'warn'},
      tf('home.ppMismatch', '保存しているスコアから計算した総PP（{calc}）が BeatLeader の値（{api}）と合いません。PP の計算がずれている可能性があります。',
        {calc: fmt(d.ppCheck.calc, 2), api: fmt(d.ppCheck.api, 2)})) : null,
    // 前回のプレイリストの結果が一番上（このアプリの結果。2026-11-01 の利用者の要望）
    d.review ? review(d.review) : null,
    tiles(d),
    h('div', {class: 'grid2'}, neighbors(d), distribution(d)),
    changes(d),
    h('div', {class: 'cta'}, h('button', {class: 'primary', onclick: () => ctx.show('recommend')}, t('home.toRec', 'プレイリストを見る'))),
  ].filter(Boolean));
}

/** BeatLeader の統計が非公開の時の知らせ（web/js/stats.js）。公開を確かめたら取り直す。ここからはオフにできない（止めるのは設定だけ） */
function statsNotice(ctx){
  const el = h('div', {class: 'warn statsNotice'},
    h('div', {class: 'statsNoticeText'}, h('div', {}, t('home.statsPrivate', 'BeatLeader の統計が非公開のため、クリアの見込みとクールダウンが効いていません。')),
      h('div', {class: 'small'}, stats.offHint())),
    h('span', {class: 'btnRow'},
      h('button', {class: 'small statsHow', onclick: () => stats.dialog(async () => {
        ctx.status.statsPrivate = false;
        el.remove();
        refresh.start();
      })}, t('home.statsHow', '公開にする方法'))));
  return el;
}

/** 「前回の出力から」の比べる元（一番新しい出力の時点の取得）。出力が無い・出力の前に取得していなければ null */
const sinceExport = d => d.sinceExport && d.sinceExport.at ? d.sinceExport : null;

/** 「前回の出力から」の説明（いつの出力の、いつの取得と比べているか） */
function sinceTip(se){
  return tf('home.sinceExportTip', '前回の出力（{e}。どのセットでも一番新しい出力）の時点で一番新しい取得（{a}）の順位・PP と、今の差です。',
    {e: when(se.exportAt), a: when(se.at)});
}

// 時刻の表示。表示する要素には class "when" を付ける（画面の比較のテストが隠す。撮るたびに変わるため。tests/visual）
function when(ts){
  const x = new Date(ts * 1000);
  return x.toLocaleString('ja-JP', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}

/** 変化の表示。good は「上がった＝良い」の時 true。色だけに頼らず矢印も付ける */
function delta(v, digits, good, unit = ''){
  if(v == null || !Number.isFinite(v) || Math.abs(v) < Math.pow(10, -digits) / 2) return h('span', {class: 'delta flat'}, '±0' + unit);
  const up = v > 0;
  return h('span', {class: 'delta ' + (up === good ? 'good' : 'bad')}, (up ? '▲ ' : '▼ ') + fmt(Math.abs(v), digits) + unit);
}

function tile(label, value, ...subs){
  return h('div', {class: 'tile'}, h('div', {class: 'tileLbl'}, label), h('div', {class: 'tileVal'}, value),
    subs.filter(Boolean).map(s => s.classList && s.classList.contains('tileSub') ? s : h('div', {class: 'tileSub'}, s)));
}

// 国内順位・PP・世界順位のタイルは「先週から」（BeatLeader の先週の値）と「前回の出力から」の差（1週間に何回も遊ぶので、今回どう
// だったかが分かるように。2026-11-01 の利用者の要望）
function tiles(d){
  const me = d.me, tg = d.target, se = sinceExport(d);
  const lw = t('home.vsLastWeek', '先週から');
  const fromExport = (old, v) => se ? h('div', {class: 'tileSub sinceExp', title: sinceTip(se)},
    t('home.vsExport', '前回の出力から'), ' ', old != null ? v() : '—') : null;
  return h('div', {class: 'tiles'},
    tile(t('home.countryRank', '国内順位'), '#' + fmt(me.countryRank),
      h('span', {}, lw, ' ', me.lastWeekCountryRank ? delta(me.lastWeekCountryRank - me.countryRank, 0, true) : '—'),
      fromExport(me.exportCountryRank, () => delta(me.exportCountryRank - me.countryRank, 0, true))),
    tile(t('home.pp', 'PP'), fmt(me.pp, 2), h('span', {}, lw, ' ', me.lastWeekPp ? delta(me.pp - me.lastWeekPp, 2, true) : '—'),
      fromExport(me.exportPp, () => delta(me.pp - me.exportPp, 2, true))),
    tile(t('home.worldRank', '世界順位'), '#' + fmt(me.rank),
      h('span', {}, lw, ' ', me.lastWeekRank ? delta(me.lastWeekRank - me.rank, 0, true) : '—'),
      fromExport(me.exportRank, () => delta(me.exportRank - me.rank, 0, true))),
    tile(tf('home.toTarget', '目標 {rank}位まで', {rank: fmt(tg.rank)}),
      tg.gap == null ? '—' : (tg.gap > 0 ? '+' + fmt(tg.gap, 2) : t('home.reached', '到達')),
      tg.name ? h('span', {}, tf('home.targetWho', '{rank}位は {name}（{pp}）', {rank: fmt(tg.rank), name: tg.name, pp: fmt(tg.pp, 2)}))
        : t('home.targetUnknown', '目標の順位の人を取得していません')));
}

// 上下の人。右の2列は前回の出力（どのセットでも一番新しい出力）の時点からの国内順位と PP の変化（自分の行も。順位は上がって
// いなくても PP は増えている、が見えるように）。出力が無い・出力の前に取得していなければ2列は出さない
function neighbors(d){
  const se = sinceExport(d);
  const rows = d.neighbors.map(n => h('tr', {class: n.self ? 'me' : ''},
    h('td', {class: 'num'}, '#' + fmt(n.countryRank)),
    h('td', {class: 'name'}, n.name),
    h('td', {class: 'num'}, fmt(n.pp, 2)),
    h('td', {class: 'num'}, n.self ? '' : (n.diff > 0 ? '+' : '') + fmt(n.diff, 2)),
    se ? [h('td', {class: 'num'}, n.rankMove == null ? '—' : delta(n.rankMove, 0, true)),
      h('td', {class: 'num'}, n.ppMove == null ? '—' : delta(n.ppMove, 2, true))] : null));
  const th = (label, attrs = {}) => h('th', attrs, label);
  const fixed = [th(t('home.th.rank', '国内'), {class: 'num', rowspan: 2}), th(t('home.th.name', '名前'), {rowspan: 2}),
    th(t('home.th.pp', 'PP'), {class: 'num', rowspan: 2}), th(t('home.th.diff', '自分との差'), {class: 'num', rowspan: 2})];
  const head = se
    ? [h('tr', {}, fixed, th(t('home.th.sinceExport', '前回の出力から'), {class: 'grp', colspan: 2, title: sinceTip(se)})),
      h('tr', {}, th(t('home.th.rankMove', '順位'), {class: 'num'}), th(t('home.th.ppMove', 'PP'), {class: 'num'}))]
    : [h('tr', {}, fixed.map(x => { x.removeAttribute('rowspan'); return x; }))];
  return h('section', {class: 'card'},
    h('div', {class: 'cardHead'}, h('h2', {}, tf('home.neighbors', '上下 {n} 人', {n: (d.neighbors.length - 1) / 2 | 0})),
      se ? h('span', {class: 'mute small when', title: sinceTip(se)}, tf('home.sinceExport', '前回の出力 {t} から', {t: when(se.exportAt)}))
        : h('span', {class: 'mute small'}, t('home.sinceExportNone', '出力した後の更新から、前回の出力からの変化も出します'))),
    h('table', {class: 'list nbTable'}, h('thead', {}, head), h('tbody', {}, rows)));
}

// 国内のPPの分布（横軸 PP・縦軸 人数）。自分と目標順位の位置に線を引く。系列は1つなので凡例は無し（見出しが名前）
function distribution(d){
  const W = 560, H = 230, L = 34, R = 12, T = 30, B = 26;
  const pps = d.poolPp.filter(Number.isFinite);
  const card = h('section', {class: 'card chartCard'},
    h('div', {class: 'cardHead'}, h('h2', {}, t('home.dist', '周辺の PP の分布')),
      h('span', {class: 'mute small'}, tf('home.distN', '取得した {n} 人', {n: pps.length}))));
  if(pps.length < 5){ card.append(h('p', {class: 'mute'}, t('home.distFew', '人数が少ないので分布を出せません。'))); return card; }
  let lo = Math.min(...pps), hi = Math.max(...pps);
  const step = niceStep((hi - lo) / 24);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step + (hi % step === 0 ? step : 0);
  const n = Math.max(1, Math.round((hi - lo) / step));
  const bins = new Array(n).fill(0);
  for(const p of pps) bins[Math.min(n - 1, Math.floor((p - lo) / step))]++;
  const maxC = Math.max(...bins);
  const yMax = niceCeil(maxC);
  const x = v => L + (v - lo) / (hi - lo) * (W - L - R);
  const y = c => H - B - c / yMax * (H - T - B);
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'chart');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', t('home.dist', '周辺の PP の分布'));
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for(const k in attrs) e.setAttribute(k, attrs[k]);
    if(text != null) e.textContent = text;
    svg.append(e);
    return e;
  };
  // 目盛り（縦: 人数、横: PP）。線は細く控えめに
  for(const c of [0, yMax / 2, yMax]){
    el('line', {x1: L, x2: W - R, y1: y(c), y2: y(c), class: 'grid'});
    el('text', {x: L - 6, y: y(c) + 4, class: 'tick', 'text-anchor': 'end'}, fmt(c));
  }
  const xt = niceStep((hi - lo) / 5);
  for(let v = Math.ceil(lo / xt) * xt; v <= hi; v += xt){
    el('text', {x: x(v), y: H - 8, class: 'tick', 'text-anchor': 'middle'}, fmt(v));
  }
  // 棒（太さは24pxまで・隣と2pxあける・上の角だけ丸める）
  const slot = (W - L - R) / n, bw = Math.min(24, slot - 2);
  const tip = h('div', {class: 'tip'});
  bins.forEach((c, i) => {
    const x0 = L + i * slot + (slot - bw) / 2;
    if(c > 0){
      const top = y(c), r = Math.min(4, (H - B - top) / 2, bw / 2);
      el('path', {class: 'bar', d: `M${x0},${H - B}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + bw - r}Q${x0 + bw},${top} ${x0 + bw},${top + r}V${H - B}Z`});
    }
    const hit = el('rect', {x: L + i * slot, y: T, width: slot, height: H - T - B, class: 'hit'});
    const a = lo + i * step;
    hit.addEventListener('mouseenter', () => {
      tip.textContent = tf('home.distTip', '{a}〜{b} PP：{c}人', {a: fmt(a), b: fmt(a + step), c});
      tip.style.left = (x(a + step / 2) / W * 100) + '%';
      tip.classList.add('on');
    });
    hit.addEventListener('mouseleave', () => tip.classList.remove('on'));
  });
  // 自分と目標の線（ラベルは文字の色、線の脇に名前と値）
  const mark = (v, cls, label) => {
    if(v == null || v < lo || v > hi) return;
    el('line', {x1: x(v), x2: x(v), y1: T - 6, y2: H - B, class: 'ref ' + cls});
    const anchor = x(v) > W - 120 ? 'end' : 'start';
    el('text', {x: x(v) + (anchor === 'end' ? -5 : 5), y: cls === 'me' ? T - 14 + 8 : T + 6, class: 'refLbl', 'text-anchor': anchor}, label);
  };
  mark(d.me.pp, 'me', tf('home.distMe', '自分 {pp}', {pp: fmt(d.me.pp)}));
  if(d.target.pp != null) mark(d.target.pp, 'target', tf('home.distTarget', '{rank}位 {pp}', {rank: d.target.rank, pp: fmt(d.target.pp)}));
  card.append(h('div', {class: 'chartWrap'}, svg, tip));
  return card;
}

function niceStep(raw){
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  for(const m of [1, 2, 2.5, 5, 10]) if(raw <= m * p) return m * p;
  return 10 * p;
}
function niceCeil(v){
  if(v <= 4) return Math.max(2, Math.ceil(v / 2) * 2);
  const s = niceStep(v / 2);
  return Math.ceil(v / s) * s;
}

// 3列（伸ばした曲・抜いた人・抜かれた人）は、それぞれ高さに上限のある枠に入れ、長ければ枠の中でスクロールする（リウェイトの後に
// 抜かれた人が130人並んだ）。枠の高さは3列で一番長い列にそろえる（CSS の .chgGrid・.chgBox）
// 伸ばした曲は、曲の PP の増えと総PP（重み付きの合計）の増えを並べる。曲の PP が大きく増えても、自分の上位の曲に入らなければ総PP は
// ほとんど増えない（2026-11-09 の利用者の要望。求め方は core/views.py の _improved_since＝振り返りの表の「総PP」と同じ）
function changes(d){
  const none = !d.prevAt ? t('home.chgFirst', '次の更新から表示します') : t('home.chgNone', 'ありません');
  const col = (key, title, count, items, render, extra) => h('div', {class: 'chgCol', dataset: {key}},
    h('h3', {}, title, items.length ? [' ', h('span', {class: 'chgCount'}, count), ...(extra ? [' ', extra] : [])] : null),
    h('div', {class: 'chgBox'}, items.length ? h('ul', {class: 'chg'}, items.map(render)) : h('p', {class: 'mute small'}, none)));
  const gainTip = t('home.chgGainTip', '重み付きの総PP に足された分（前回の時点の自己ベストの並びに、この曲の新しい自己ベストを入れた時の差）。' +
    '自分の上位の曲より PP の低い曲は、曲の PP が増えても総PP はほとんど増えません');
  const totalGain = h('span', {class: 'chgGain', title: t('home.chgGainAllTip',
    '伸ばした曲を全部入れた時の総PP（重み付きの合計）の増え。初めてクリアした曲の分は入りません')},
    t('home.chgGainAll', '総PP'), ' ', delta(d.improvedGain, 2, true));
  const person = x => h('li', {}, h('span', {class: 'name'}, x.name), h('span', {class: 'num mute'}, '#' + fmt(x.countryRank)));
  const people = (key, title, items) => col(key, title, tf('home.chgCount', '{n}人', {n: items.length}), items, person);
  return h('section', {class: 'card'},
    h('div', {class: 'cardHead'}, h('h2', {}, t('home.changes', '前回からの変化')),
      d.prevAt ? h('span', {class: 'mute small when'}, tf('home.since', '{t} から', {t: when(d.prevAt)})) : null),
    h('div', {class: 'chgGrid'},
      col('improved', t('home.improved', '自分が伸ばした曲'), tf('home.chgSongs', '{n}曲', {n: d.improved.length}), d.improved,
        x => h('li', {class: 'withCover'}, songCover(x.hash),
          h('div', {class: 'chgSong'},
            h('div', {class: 'chgName'}, h('span', {class: 'song'}, x.song || x.leaderboard_id), ' ', h('span', {class: 'mute'}, diffName(x.difficulty))),
            h('div', {class: 'num chgAcc'}, fmt(100 * x.old_acc, 2), '% → ', fmt(100 * x.acc, 2), '%')),
          h('span', {class: 'chgPp'},
            h('span', {class: 'chgSongPp', title: t('home.chgSongPpTip', 'この曲の PP の増え（今の式）')},
              h('span', {class: 'mute'}, t('home.chgSongPp', '曲のPP')), ' ', delta((x.pp || 0) - (x.old_pp || 0), 2, true)),
            h('span', {class: 'chgSongGain', title: gainTip},
              h('span', {class: 'mute'}, t('home.chgGain', '総PP')), ' ', delta(x.gain, 2, true)))),
        totalGain),
      people('passed', t('home.passed', '抜いた人'), d.passed),
      people('passedBy', t('home.passedBy', '抜かれた人'), d.passedBy)));
}

// 前回出力したプレイリストの結果（振り返り）。セットごとに一番新しい出力の分。セットが複数あれば小さなタブで切り替える
// （タブの並び＝おすすめの画面のセットの並び。最初に開くのは一番前のセット）。
// 表は曲ごとに「出した時の狙い」（推定精度・増える総PP の見込み）・前の自己ベスト・「今回の結果」（出力から今までの一番の精度・
// その PP・重み付きの総PP に足された分）。上の1行はこのリストで増えた総PP と出した時の見込み（core/review.py の _one）。
// 2026-11-01 に利用者が「出力の時・推定・今の自己ベスト・挑戦が何のことか分からない」と言ったので、何の数字かが分かる見出しにした。
// 初クリア（出力の時に自己ベストが無かった曲）は「伸びた」と分けて数える（初めてのクリアは推定より低く出やすく、総PP にもほとんど効かない。core/review.py）
const REVIEW = {improved: '伸びた', firstClear: '初クリア', played: '伸びなかった', notPlayed: 'まだ'};
const REVIEW_ORDER = ['improved', 'firstClear', 'played', 'notPlayed'];
const SCORED = ['improved', 'firstClear'];   // 新しい自己ベストができた（総PP に効く）
let revSel = null;
const pct = v => v == null ? '—' : fmt(100 * v, 2) + '%';
const plus = (v, digits = 2) => '+' + fmt(Math.max(0, v), digits);   // 総PP の増え（0 より小さくならない）

/** 推定と実際の差を結果ごとに「伸びた +0.30%（3曲）・初クリア −8.11%（4曲）…」。数えた曲が無ければ空 */
function errParts(e){
  if(!e) return '';
  return ['improved', 'firstClear', 'played'].filter(k => e[k] && e[k].n).map(k => tf('home.revErrPart', '{k} {v}（{n}曲）', {
    k: t('home.rev.' + k, REVIEW[k]), v: (e[k].mean >= 0 ? '+' : '') + fmt(100 * e[k].mean, 2) + '%', n: e[k].n})).join('・');
}

function review(all){
  const run = all.runs.find(x => x.preset === revSel) || all.runs[0];
  const card = h('section', {class: 'card'});
  const draw = r => {
    revSel = r.preset;
    card.replaceChildren(...reviewBody(r, all).filter(Boolean));
  };
  const reviewBody = (r, all) => {
    const c = r.counts;
    const here = errParts(r.estError), past = errParts(all.estError);
    const err = [here ? tf('home.revErrRun', 'この回の推定との差（今回の精度 − 推定精度の平均）: {v}', {v: here}) : '',
      past ? tf('home.revErrAll', 'これまでの推定との差: {v}', {v: past}) : ''].filter(Boolean);
    return [
      h('div', {class: 'cardHead'}, h('h2', {}, t('home.review', '前回のプレイリストの結果')),
        h('span', {class: 'mute small when'}, all.runs.length > 1 ? '' : r.presetName + ' · ',
          tf('home.revAt', '{t} に出力', {t: when(r.exportedAt)}))),
      all.runs.length > 1 ? h('div', {class: 'presetTabs small revTabs'}, all.runs.map(x => h('button', {
        class: 'presetTab' + (x.preset === r.preset ? ' on' : ''), dataset: {preset: x.preset}, onclick: () => draw(x)}, x.presetName))) : null,
      revSummary(r),
      h('div', {class: 'revCounts'},
        REVIEW_ORDER.map(k => h('span', {class: 'revCount ' + k},
          t('home.rev.' + k, REVIEW[k]), ' ', h('b', {}, String(c[k] || 0)), t('home.revUnit', '曲')))),
      revTable(r),
      r.checked ? null : h('p', {class: 'mute small'}, t('home.revUnchecked', '出力の後にまだ更新していません。遊んだ後に「更新」を押すと結果が出ます。')),
      ...err.map(x => h('p', {class: 'mute small revErr'}, x)),
      c.firstClear ? h('p', {class: 'mute small'}, t('home.revFirst',
        '初クリアは初めてクリアした曲です。最初のクリアは推定より低く出やすいので、推定との差は「伸びた」と分けて出します。')) : null,
      c.played ? h('p', {class: 'mute small'}, t('home.revCool', '遊んだ曲は、伸びたかに関係なく、しばらくリストに入れません（クールダウン。間をあける日数・休ませる長さはプレイリストの画面の条件で決めます）。')) : null];
  };
  draw(run);
  return card;
}

/** 表の上の1行: このリスト（このセットのこの出力）で増えた総PP・出した時の見込み・遊んだ曲の数 */
function revSummary(r){
  const exp = tf('home.revExp', '出した時の見込み {v}', {v: plus(r.expected)});
  return h('div', {class: 'revSum'},
    h('span', {class: 'revSumHead'}, t('home.revSumHead', 'このリスト')),
    h('span', {class: 'revSumGain', title: t('home.revSumGainTip',
      'このリストの曲で増えた総PP（重み付きの合計）。出力の時点の自己ベストの並びに、このリストの新しい自己ベストを全部入れた時の差です。')},
      t('home.revSumGain', '総PP'), ' ', h('b', {class: 'delta ' + (r.gain >= 0.005 ? 'good' : 'flat')}, plus(r.gain))),
    h('span', {class: 'mute revSumExp', title: r.expectedClear
      ? t('home.revExpTip', '出した時に見込んだ増え。プレイリストの画面のまとめと同じく、曲ごとの「クリアの見込み × 総PP見込み」の合計です。')
      : t('home.revExpTipOld', 'この出力はクリアの見込みを残す前の版なので、曲ごとの総PP見込みをそのまま足しています（プレイリストの画面のまとめは、クリアの見込みを掛けた値で、これより小さめ）。')},
      '（', exp, r.expectedClear ? '' : t('home.revExpOld', '・クリアの見込みを掛けない合計'), '）'),
    h('span', {class: 'mute'}, tf('home.revPlayed', '遊んだ {n} / {all}曲', {n: r.played, all: r.items.length})));
}

// 「出した時の狙い」「今回の結果」は1つの列の中に値を並べる（見出しの2段目も同じ並び）。狭い時（@container main）は値を縦に積む
// （列の数を変えずに幅を詰める。base.css の .grpVals）
function revTable(r){
  const v = (cls, body, title) => h('span', {class: 'rvV ' + cls, title}, body);
  const head = h('tr', {},
    h('th', {class: 'coverCell'}),
    h('th', {}, t('home.revTh.song', '曲・難易度')),
    h('th', {class: 'num', title: t('home.revTh.starsTip', '今の BeatLeader の★')}, '★'),
    h('th', {}, t('home.revTh.status', '結果')),
    h('th', {class: 'grpCell'},
      h('div', {class: 'grpHead'}, t('home.revTh.aim', '出した時の狙い')),
      h('div', {class: 'grpVals'},
        v('est', t('home.revTh.est', '推定精度'), t('home.revTh.estTip', '出した時の推定精度（次に遊んだ日に出せそうな精度）')),
        v('exp', t('home.revTh.exp', '総PP見込み'), t('home.revTh.expTip',
          '出した時に見込んだ、この曲で増える総PP（プレイリストの画面の「総PP」と同じ値。自己ベストを超える見込みも入れた平均）')))),
    h('th', {class: 'num', title: t('home.revTh.beforeTip', '出力した時点の自己ベスト（— は未プレイ）')}, t('home.revTh.before', '前の自己ベスト')),
    h('th', {class: 'grpCell'},
      h('div', {class: 'grpHead', title: t('home.revTh.resultTip', '出力してから今までの一番（同じリストを何日かに分けて遊んだ時は全部の中で）')},
        t('home.revTh.result', '今回の結果')),
      h('div', {class: 'grpVals'},
        v('acc', t('home.revTh.acc', '精度'), t('home.revTh.accTip', '伸びた・初クリアは新しい自己ベスト、伸びなかった曲は出力の後のクリアで一番よかった精度')),
        v('pp', t('home.revTh.pp', '曲のPP'), t('home.revTh.ppTip', 'その精度の曲の PP（今の式）。伸びなかった曲は参考（薄い字）')),
        v('gain', t('home.revTh.gain', '総PP'), t('home.revTh.gainTip',
          '重み付きの総PP に足された分（出力の時点の自己ベストの並びに、この曲の新しい自己ベストを入れた時の差）')))));
  const row = x => {
    const scored = SCORED.includes(x.status);
    const acc = x.acc != null ? pct(x.acc) : x.status === 'played'
      ? h('span', {class: 'mute', title: t('home.revNoClearTip', '遊んだがクリアの記録が無い（失敗・やり直し・途中でやめた）')}, t('home.revNoClear', 'クリアなし'))
      : '—';
    return h('tr', {class: x.status === 'notPlayed' ? 'mute' : '', dataset: {id: x.id, status: x.status}},
      h('td', {class: 'coverCell'}, songCover(x.hash)),
      h('td', {class: 'name'}, x.song || '', ' ', h('span', {class: 'mute'}, diffName(x.difficulty))),
      h('td', {class: 'num'}, fmt(x.stars, 2)),
      h('td', {}, h('span', {class: 'revTag ' + x.status}, t('home.rev.' + x.status, REVIEW[x.status]))),
      h('td', {class: 'grpCell'}, h('div', {class: 'grpVals'},
        v('est', pct(x.est)),
        v('exp', x.expGain == null ? '—' : plus(x.expGain),
          x.clear != null ? tf('home.revClearTip', 'クリアの見込み {p}%', {p: fmt(100 * x.clear)}) : null))),
      h('td', {class: 'num'}, pct(x.before)),
      h('td', {class: 'grpCell'}, h('div', {class: 'grpVals'},
        v('acc', acc),
        v('pp' + (scored ? '' : ' ref'), x.pp != null ? fmt(x.pp, 1) : '',
          !scored && x.pp != null ? t('home.revPpRefTip', '参考: この精度の PP。自己ベストを超えていないので、総PP は増えません') : null),
        v('gain', x.contrib != null ? h('span', {class: 'delta ' + (x.contrib >= 0.005 ? 'good' : 'flat')}, plus(x.contrib))
          : x.acc != null ? '—' : ''))));
  };
  return h('table', {class: 'list revTable'}, h('thead', {}, head), h('tbody', {}, r.items.map(row)));
}

function diffName(s){
  return ({ExpertPlus: 'Expert+'})[s] || s || '';
}
