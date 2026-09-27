/* hubbar.js — Stock Hub 공통 상단바 (2026-09-27 사이트 전면 개편)
 * newsinfo.cloud(Key Indicator)의 모든 화면 최상단에 붙는 얇은 바: 허브 복귀 + 모듈 이동.
 * 기존 화면 코드를 건드리지 않도록 독립 스크립트로 주입한다(index.html, kai/index.html 에서 <script src="/hubbar.js" defer>).
 * 디자인은 stock.leanguy.cloud 의 앱바와 동일 토큰(파랑 #1a73e8, 선 #e2e6ef). */
(function () {
  if (window.__hubBar) return;
  window.__hubBar = true;
  var HUB = 'https://stock.leanguy.cloud';
  var items = [
    ['Stock Info', HUB + '/info'],
    ['Key Indicator', 'https://newsinfo.cloud/', true],
    ['Stock Lab', HUB + '/lab'],
    ['Stock LLM', HUB + '/llm/'],
  ];
  var css = '' +
    '#hubbar{position:sticky;top:0;z-index:2147483000;display:flex;align-items:center;gap:6px;height:44px;padding:0 16px;background:#fff;border-bottom:1px solid #e2e6ef;font-family:Pretendard,-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;box-sizing:border-box}' +
    '#hubbar *{box-sizing:border-box;font-family:inherit}' +
    '#hubbar a{display:inline-flex;align-items:center;padding:6px 11px;border-radius:8px;font-size:13px;font-weight:600;color:#334155;text-decoration:none;white-space:nowrap}' +
    '#hubbar a:hover{background:#f4f6fb;color:#0b1220}' +
    '#hubbar a.on{background:#e8f0fe;color:#1a73e8}' +
    '#hubbar .brand{gap:8px;color:#0b1220;font-weight:800;font-size:14px;letter-spacing:-.02em;padding-left:0;margin-right:8px}' +
    '#hubbar .brand:hover{background:transparent}' +
    '#hubbar .logo{width:24px;height:24px;border-radius:7px;background:#1a73e8;color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:800}' +
    '#hubbar .sp{flex:1}' +
    '#hubbar .tabs{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none}' +
    '@media(max-width:640px){#hubbar{padding:0 8px}#hubbar .brand span:last-child{display:none}#hubbar a{padding:6px 8px;font-size:12px}}';
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
  var bar = document.createElement('nav');
  bar.id = 'hubbar';
  bar.setAttribute('aria-label', 'Stock Hub');
  var html = '<a class="brand" href="' + HUB + '/"><span class="logo">S</span><span>Stock Hub</span></a><div class="tabs">';
  items.forEach(function (it) {
    html += '<a href="' + it[1] + '"' + (it[2] ? ' class="on" aria-current="page"' : '') + '>' + it[0] + '</a>';
  });
  html += '</div><span class="sp"></span><a href="' + HUB + '/admin">관리자</a>';
  bar.innerHTML = html;
  function mount() { document.body.insertBefore(bar, document.body.firstChild); }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
})();
