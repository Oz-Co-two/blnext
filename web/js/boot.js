// 最初に読まれるスクリプト（index.html で type="module" を付けずに読む＝他のどの通信よりも前に動く）。
// serve.py は起動ごとの合言葉を X-BLN-Token ヘッダーで求める（静的ファイルを除く。serve.py の SESSION_TOKEN）。
// 画面へは URL の #k=… で渡る。同じオリジンへの fetch / XHR に自動で付ける＝個々の通信で付け忘れない。
// NLM（Non-Linear Mapper Oz-Co2版）の settings-mirror.js の先頭部分を流用した。
(function(){
  var m=/(?:^#|&)k=([A-Za-z0-9_-]+)/.exec(location.hash), tok=m?m[1]:'';
  if(!tok){ window.__blnNoToken=true; return; }   // 案内は main.js（言語の読込後）
  function same(u){ try{ return new URL(u,location.href).origin===location.origin; }catch(_){ return false; } }
  var _fetch=window.fetch;
  window.fetch=function(input,init){
    var req=(typeof Request!=='undefined'&&input instanceof Request);
    if(same(req?input.url:String(input))){
      init=Object.assign({},init||{});
      var h=new Headers(init.headers||(req?input.headers:undefined));   // init.headers は Request 側のヘッダーを丸ごと置き換えるので合成する
      h.set('X-BLN-Token',tok); init.headers=h;
    }
    return _fetch.call(window,input,init);
  };
  var _open=XMLHttpRequest.prototype.open, _send=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(method,url){ this._blnTok=same(url); return _open.apply(this,arguments); };
  XMLHttpRequest.prototype.send=function(){ if(this._blnTok) this.setRequestHeader('X-BLN-Token',tok); return _send.apply(this,arguments); };
})();
