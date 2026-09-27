/* keyindicator.js — Key Indicator 공개 화면 (2026-09-27): 로그인 없이 읽기 전용 API만 호출한다(backend/role_gate.py PUBLIC_READ). */
(function () {
  var host = location.hostname;
  var API = /(^|\.)newsinfo\.cloud$/.test(host) ? 'https://api.newsinfo.cloud' : 'http://127.0.0.1:8011';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var num = function (v, d) { return v == null || isNaN(v) ? '–' : Number(v).toLocaleString('ko-KR', { maximumFractionDigits: d == null ? 2 : d }); };
  var chg = function (v) {
    if (v == null || isNaN(v)) return '<span class="ki-muted">–</span>';
    var cls = v > 0 ? 'up' : v < 0 ? 'down' : '';   // 한국식: 상승 빨강 / 하락 파랑
    return '<span class="' + cls + '">' + (v > 0 ? '▲ ' : v < 0 ? '▼ ' : '') + num(Math.abs(v), 2) + '%</span>';
  };
  var get = function (path) { return fetch(API + path + (path.indexOf('?') < 0 ? '?' : '&') + 'role=staff').then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); };
  var fail = function (tbodyId, cols, msg) { $(tbodyId).innerHTML = '<tr><td colspan="' + cols + '" class="ki-muted">' + esc(msg) + '</td></tr>'; };

  // 탭
  document.querySelectorAll('.ki-tabs button').forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll('.ki-tabs button').forEach(function (x) { x.classList.toggle('on', x === b); });
      $('pane-eco').hidden = b.dataset.tab !== 'eco';
      $('pane-news').hidden = b.dataset.tab !== 'news';
      if (b.dataset.tab === 'news' && !news.loaded) loadNews();
    };
  });

  // 국내 지표 — 증감(절대값)과 증감률(이전값이 양수일 때만: 0 근처·음수 기준의 %는 의미가 없다)
  var diffCell = function (a, b) {
    if (a == null || b == null) return '<span class="ki-muted">–</span>';
    var d = a - b, cls = d > 0 ? 'up' : d < 0 ? 'down' : '';
    return '<span class="' + cls + '">' + (d > 0 ? '▲ ' : d < 0 ? '▼ ' : '') + num(Math.abs(d), 2) + '</span>';
  };
  get('/api/eco/indicators').then(function (rows) {
    var body = rows.map(function (r) {
      var pct = r.prev_value > 0 && r.value != null ? (r.value - r.prev_value) / r.prev_value * 100 : null;
      return '<tr><td>' + esc(r.name_kr) + '</td><td>' + esc(r.category) + '</td><td>' + esc(String(r.date || '').slice(0, 7)) + '</td><td class="num">' + num(r.value) + '</td><td class="num">' + num(r.prev_value) + '</td><td class="num">' + diffCell(r.value, r.prev_value) + '</td><td class="num">' + chg(pct) + '</td><td>' + esc(r.source_ref) + '</td></tr>';
    }).join('');
    $('kr-table').querySelector('tbody').innerHTML = body || '<tr><td colspan="8" class="ki-muted">데이터가 없습니다.</td></tr>';
    $('kr-meta').textContent = rows.length + '개 지표';
  }).catch(function () { fail('kr-table', 8, '지표를 불러오지 못했습니다. 잠시 후 다시 시도하세요.'); });

  // 글로벌 지표
  var gl = { rows: [], cat: '전체', q: '' };
  function renderGlobal() {
    var q = gl.q.toLowerCase();
    var rows = gl.rows.filter(function (r) { return (gl.cat === '전체' || r.category === gl.cat) && (!q || (r.name + ' ' + r.category + ' ' + (r.name_en || '') + ' ' + r.source).toLowerCase().indexOf(q) >= 0); });
    var shown = rows.slice(0, 300);
    $('gl-table').querySelector('tbody').innerHTML = shown.map(function (r) {
      return '<tr><td>' + esc(r.name) + '</td><td>' + esc(r.category) + '</td><td>' + esc(r.unit) + '</td><td>' + esc(r.date) + '</td><td class="num">' + num(r.value) + '</td><td class="num">' + num(r.prev_value) + '</td><td class="num">' + chg(r.change_pct) + '</td><td>' + esc(r.source) + '</td></tr>';
    }).join('') || '<tr><td colspan="8" class="ki-muted">조건에 맞는 지표가 없습니다.</td></tr>';
    $('gl-meta').textContent = rows.length + '개 중 ' + shown.length + '개 표시 · 값이 있는 지표만, 중요도 높은 순';
  }
  get('/api/global-macro/latest').then(function (rows) {
    gl.rows = rows.filter(function (r) { return r.value != null; }).sort(function (a, b) { return (b.importance || 0) - (a.importance || 0) || String(b.date).localeCompare(String(a.date)); });
    var cats = ['전체'].concat(Array.from(new Set(gl.rows.map(function (r) { return r.category; }))).sort());
    $('gl-cats').innerHTML = cats.map(function (c) { return '<button data-c="' + esc(c) + '" class="' + (c === gl.cat ? 'on' : '') + '">' + esc(c) + '</button>'; }).join('');
    $('gl-cats').onclick = function (e) { var c = e.target.dataset && e.target.dataset.c; if (!c) return; gl.cat = c; $('gl-cats').querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.dataset.c === c); }); renderGlobal(); };
    renderGlobal();
  }).catch(function () { fail('gl-table', 8, '글로벌 지표를 불러오지 못했습니다.'); });
  $('gl-q').oninput = function () { gl.q = this.value.trim(); renderGlobal(); };

  // 뉴스정보
  var news = { loaded: false, items: [], q: '' };
  function kst(iso) { try { return new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }); } catch (e) { return ''; } }
  function renderNews() {
    var q = news.q.toLowerCase();
    var items = news.items.filter(function (n) { return !q || (n.title + ' ' + n.summary + ' ' + n.article_publisher).toLowerCase().indexOf(q) >= 0; });
    $('news-meta').textContent = '전체 ' + news.items.length + '건 중 ' + Math.min(items.length, 100) + '건 표시';
    $('news-list').innerHTML = items.slice(0, 100).map(function (n) {
      var href = /^https?:\/\//i.test(n.link || '') ? n.link : '#';
      return '<li><div class="meta"><span class="pub">' + esc(n.article_publisher || n.source) + '</span><span>' + esc(kst(n.article_published_at)) + '</span></div><a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(n.title) + '</a><p>' + esc(n.summary) + '</p></li>';
    }).join('') || '<li class="ki-muted">조건에 맞는 뉴스가 없습니다.</li>';
  }
  function loadNews() {
    get('/feeds/company').then(function (d) {
      news.loaded = true;
      news.items = (d.published || []).slice().sort(function (a, b) { return String(b.article_published_at).localeCompare(String(a.article_published_at)); });
      renderNews();
    }).catch(function () { $('news-list').innerHTML = '<li class="ki-muted">뉴스를 불러오지 못했습니다. 잠시 후 다시 시도하세요.</li>'; });
  }
  $('news-q').oninput = function () { news.q = this.value.trim(); renderNews(); };
})();
