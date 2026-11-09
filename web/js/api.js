// serve.py の API（POST /__api/<名前>）。合言葉（X-BLN-Token）は boot.js が自動で付ける。
// X-BLN-Request は他サイトからの要求（CSRF）を防ぐ印＝付けないと 403 になる（serve.py の「アクセス制御」）
export async function api(name, body = {}){
  let r;
  try{
    r = await fetch('__api/' + name, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-BLN-Request': '1'},
      body: JSON.stringify(body),
    });
  }catch(e){
    return {ok: false, error: 'network'};
  }
  try{ return await r.json(); }
  catch(_){ return {ok: false, error: 'http-' + r.status}; }
}

/** exe 版（pywebview）の窓口。ブラウザで開いている時は null */
export function native(){
  if(window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise(res => {
    const done = () => res(window.pywebview && window.pywebview.api || null);
    window.addEventListener('pywebviewready', done, {once: true});
    setTimeout(done, 1500);
  });
}
