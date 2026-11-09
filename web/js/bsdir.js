// 出力先（Beat Saber のフォルダ）を選ぶ小さな画面。設定画面とおすすめ画面で共通に使う。
// 開くと自動で探した候補を出し、「これにする」で決める。見つからない時は exe 版のフォルダ選択（Beat Saber の中のどの階層を選んでもよい）。
// 候補のパスを確かめて覚えるのは Python（serve.py の bs/find・bs/use）。画面から送るのは候補の番号だけ
import { api, native } from './api.js';
import { t, tf } from './i18n.js';
import { h } from './util.js';

const SOURCES = {steam: 'Steam', oculus: 'Meta（Oculus）', bsmanager: 'BSManager', picked: '選んだフォルダの中'};

/** 開く。決まったら onChosen(path) を呼ぶ */
export async function choose(onChosen){
  const close = () => { back.remove(); document.removeEventListener('keydown', onEsc); };
  const onEsc = e => { if(e.key === 'Escape') close(); };
  const list = h('div', {class: 'bsList'}, h('p', {class: 'mute small'}, t('bs.searching', '探しています…')));
  const msg = h('p', {class: 'err small'});
  const fname = h('code', {}, 'blnext-next.bplist');
  const box = h('div', {class: 'modal', role: 'dialog', 'aria-modal': 'true'},
    h('h2', {}, t('bs.title', 'Beat Saber のフォルダ（出力先）')),
    h('p', {class: 'mute small'}, t('bs.what', 'プレイリストは、このフォルダの中の Playlists に次の名前で書き込みます（毎回上書き）:'), ' ', fname),
    h('h3', {}, t('bs.found', '見つかったフォルダ')),
    list,
    h('h3', {}, t('bs.other', '別の場所を選ぶ')),
    h('p', {class: 'mute small'}, t('bs.pickHint',
      'Beat Saber のフォルダ（Beat Saber.exe がある所）か、その中のフォルダ（CustomLevels・Playlists・UserData など）を選んでください。どの階層を選んでも、Beat Saber.exe がある所を自動で出力先にします。BSManager の場合は BSManager のフォルダや SharedContent を選んでもかまいません。')),
    h('div', {class: 'btnRow'}, h('button', {onclick: pick}, t('bs.pick', 'フォルダを選ぶ…'))),
    msg,
    h('div', {class: 'modalFoot'}, h('button', {onclick: close}, t('bs.close', '閉じる'))));
  const back = h('div', {class: 'modalBack', onclick: e => { if(e.target === back) close(); }}, box);
  document.body.append(back);
  document.addEventListener('keydown', onEsc);

  const s = await api('settings/get');
  if(s.ok) fname.textContent = s.values.output_filename;
  const r = await api('bs/find');
  show(r.ok ? r.candidates : [], r.ok ? r.current : '', null);

  function show(cands, current, heading){
    const cur = (current || '').toLowerCase();
    list.replaceChildren(...[   // null は DOM に渡すと "null" の文字になるので除く
      heading ? h('p', {class: 'small'}, heading) : null,
      ...(cands.length ? cands.map(c => h('div', {class: 'bsItem' + (c.path.toLowerCase() === cur ? ' cur' : '')},
        h('div', {class: 'bsPath'}, h('span', {class: 'path'}, c.path),
          h('span', {class: 'mute small'}, t('bs.src.' + c.source, SOURCES[c.source] || c.source),
            c.last ? ' · ' + t('bs.last', '最後に起動した版') : '', ' · ',
            c.hasPlaylists ? tf('bs.lists', 'Playlists にプレイリスト {n} 個', {n: c.playlists}) : t('bs.noLists', 'Playlists はまだ無い（作ります）')),
          c.sharedPlaylists ? h('span', {class: 'mute small'}, t('bs.shared', 'Playlists は共通のフォルダにつながっています（BSManager の共通コンテンツ）。書き込むとここに入ります:'),
            ' ', h('span', {class: 'path'}, c.sharedPlaylists)) : null),
        c.path.toLowerCase() === cur ? h('span', {class: 'mute small'}, t('bs.current', '今の出力先'))
          : h('button', {class: 'primary small', onclick: () => use(c.id)}, t('bs.use', 'これにする'))))
        : [h('p', {class: 'mute small'}, t('bs.none', '自動では見つかりませんでした。下の「フォルダを選ぶ…」で選んでください。'))])].filter(Boolean));
  }
  async function use(id){
    const r = await api('bs/use', {id});
    if(!r.ok){ msg.textContent = t('bs.err.' + r.error, '選べませんでした。もう一度探してください。'); return; }
    close();
    onChosen && onChosen(r.path);
  }
  async function pick(){
    msg.textContent = '';
    const n = await native();
    if(!n){ msg.textContent = t('set.exeOnly', '場所はアプリ（exe版）のダイアログでだけ選べます'); return; }
    const r = await n.pick_beat_saber_dir();
    if(!r || r.error === 'cancelled') return;
    if(r.ok){ close(); onChosen && onChosen(r.path); return; }
    if(r.error === 'choose'){ show(r.candidates, '', t('bs.many', '選んだフォルダの中に Beat Saber が複数ありました。使う物を選んでください。')); return; }
    msg.textContent = t('bs.notFound', '選んだフォルダの中にも上にも Beat Saber.exe が見つかりませんでした。Beat Saber をインストールしたフォルダを選んでください。');
  }
}
