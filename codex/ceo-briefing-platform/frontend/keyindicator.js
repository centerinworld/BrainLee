/* keyindicator.js — Key Indicator 공개 화면 (2026-10-02): 로그인 없이 읽기 전용 API만 호출한다(backend/role_gate.py PUBLIC_READ). */
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

  // 서브탭 (경제지표/뉴스정보)
  document.querySelectorAll('.ki-tabs button').forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll('.ki-tabs button').forEach(function (x) { x.classList.toggle('on', x === b); });
      $('pane-eco').hidden = b.dataset.tab !== 'eco';
      $('pane-news').hidden = b.dataset.tab !== 'news';
      if (b.dataset.tab === 'news' && !news.loaded) loadNews();
    };
  });

  // 뉴스 타입 전환 (company/competitor/github) - index.html 통합 시 전역 함수로 노출
  var currentNewsType = 'company';
  window.switchNewsType = function (type) {
    if (currentNewsType === type) return;
    currentNewsType = type;
    ['company', 'competitor', 'github'].forEach(function (t) {
      var btn = $('news-btn-' + t);
      if (btn) btn.classList.toggle('on', t === type);
    });
    news.loaded = false;
    news.items = [];
    $('news-list').innerHTML = '<li class="ki-muted">불러오는 중…</li>';
    loadNews();
  };

  // 국내 지표 — 증감(절대값)과 증감률(이전값이 양수일 때만: 0 근처·음수 기준의 %는 의미가 없다)
  var diffCell = function (a, b) {
    if (a == null || b == null) return '<span class="ki-muted">–</span>';
    var d = a - b, cls = d > 0 ? 'up' : d < 0 ? 'down' : '';
    return '<span class="' + cls + '">' + (d > 0 ? '▲ ' : d < 0 ? '▼ ' : '') + num(Math.abs(d), 2) + '</span>';
  };
  get('/api/eco/indicators').then(function (rows) {
    var body = rows.map(function (r) {
      var pct = r.prev_value > 0 && r.value != null ? (r.value - r.prev_value) / r.prev_value * 100 : null;
      return '<tr class="ki-clickable" data-kind="eco" data-code="' + esc(r.indicator_code) + '" data-name="' + esc(r.name_kr) + '"><td>' + esc(r.name_kr) + '</td><td>' + esc(r.category) + '</td><td>' + esc(String(r.date || '').slice(0, 7)) + '</td><td class="num">' + num(r.value) + '</td><td class="num">' + num(r.prev_value) + '</td><td class="num">' + diffCell(r.value, r.prev_value) + '</td><td class="num">' + chg(pct) + '</td><td>' + esc(r.source_ref) + '</td></tr>';
    }).join('');
    $('kr-table').querySelector('tbody').innerHTML = body || '<tr><td colspan="8" class="ki-muted">데이터가 없습니다.</td></tr>';
    $('kr-meta').textContent = rows.length + '개 지표 · 행을 클릭하면 과거 이력을 볼 수 있습니다';
  }).catch(function () { fail('kr-table', 8, '지표를 불러오지 못했습니다. 잠시 후 다시 시도하세요.'); });

  // 글로벌 지표
  var gl = { rows: [], cat: '전체', q: '' };
  function renderGlobal() {
    var q = gl.q.toLowerCase();
    var rows = gl.rows.filter(function (r) { return (gl.cat === '전체' || r.category === gl.cat) && (!q || (r.name + ' ' + r.category + ' ' + (r.name_en || '') + ' ' + r.source).toLowerCase().indexOf(q) >= 0); });
    var shown = rows.slice(0, 300);
    $('gl-table').querySelector('tbody').innerHTML = shown.map(function (r) {
      return '<tr class="ki-clickable" data-kind="global" data-code="' + esc(r.code) + '" data-name="' + esc(r.name) + '"><td>' + esc(r.name) + '</td><td>' + esc(r.category) + '</td><td>' + esc(r.unit) + '</td><td>' + esc(r.date) + '</td><td class="num">' + num(r.value) + '</td><td class="num">' + num(r.prev_value) + '</td><td class="num">' + chg(r.change_pct) + '</td><td>' + esc(r.source) + '</td></tr>';
    }).join('') || '<tr><td colspan="8" class="ki-muted">조건에 맞는 지표가 없습니다.</td></tr>';
    $('gl-meta').textContent = rows.length + '개 중 ' + shown.length + '개 표시 · 값이 있는 지표만, 중요도 높은 순 · 행을 클릭하면 과거 이력을 볼 수 있습니다';
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
  // GitHub Actions 수집 뉴스 JSON URL (저장소 공개 후 또는 GitHub Pages 사용 시 설정)
  var GITHUB_NEWS_URL = '/data/overseas-news-latest.json';

  function loadNews() {
    var feedType = currentNewsType || 'company';

    if (feedType === 'github') {
      // GitHub Actions가 수집한 정적 JSON에서 로드
      fetch(GITHUB_NEWS_URL + '?t=' + Date.now())
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          news.loaded = true;
          var items = (d.items || []).map(function (item) {
            return {
              title: item.title || '',
              link: item.link || '',
              summary: item.summary || '',
              article_published_at: item.published || '',
              article_publisher: item.source_name || item.category || '해외',
              source: item.category || '해외',
            };
          });
          news.items = items.sort(function (a, b) { return String(b.article_published_at).localeCompare(String(a.article_published_at)); });
          var meta = $('news-meta');
          if (meta && d.generated_at) meta.textContent = '수집 시각: ' + d.generated_at.slice(0, 16).replace('T', ' ') + ' UTC · ' + (d.total || 0) + '건';
          renderNews();
        })
        .catch(function () {
          $('news-list').innerHTML = '<li class="ki-muted">GitHub Actions 수집 데이터를 불러오지 못했습니다. 워크플로우가 아직 실행되지 않았거나 파일이 없습니다.</li>';
        });
      return;
    }

    get('/feeds/' + feedType).then(function (d) {
      news.loaded = true;
      news.items = (d.published || []).slice().sort(function (a, b) { return String(b.article_published_at).localeCompare(String(a.article_published_at)); });
      renderNews();
    }).catch(function () { $('news-list').innerHTML = '<li class="ki-muted">뉴스를 불러오지 못했습니다. 잠시 후 다시 시도하세요.</li>'; });
  }
  $('news-q').oninput = function () { news.q = this.value.trim(); renderNews(); };

  // 지표 상세(이력) 모달 — 2026-09-29: "예전엔 상세 내역이 보였는데 지금은 안 보인다"는 지적으로 복원.
  // /api/eco/indicators/{code}/history 와 /api/global-macro/timeseries/{code} 는 이미 공개(PUBLIC_READ) 엔드포인트라
  // 백엔드 변경 없이 프런트에서 클릭→이력 조회만 추가하면 된다.
  var modal = $('ki-modal');
  function closeModal() { modal.hidden = true; }
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
  $('ki-modal-close').onclick = closeModal;
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) closeModal(); });

  function sparkline(points) {
    // points: [{date, value}], 의존성 없는 인라인 SVG 라인차트
    var vals = points.map(function (p) { return p.value; }).filter(function (v) { return v != null && !isNaN(v); });
    if (!vals.length) return '<p class="ki-muted">차트를 그릴 값이 없습니다.</p>';
    var w = 760, h = 220, pad = 28;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (min === max) { min -= 1; max += 1; }
    var n = points.length;
    var x = function (i) { return pad + (n <= 1 ? 0 : (w - pad * 2) * i / (n - 1)); };
    var y = function (v) { return h - pad - (h - pad * 2) * (v - min) / (max - min); };
    var d = '', started = false;
    points.forEach(function (p, i) {
      if (p.value == null || isNaN(p.value)) return;
      d += (started ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(p.value).toFixed(1) + ' ';
      started = true;
    });
    var last = points[points.length - 1];
    var lastX = x(n - 1), lastY = y(last.value);
    var firstLabel = points[0] ? points[0].date : '';
    var lastLabel = last ? last.date : '';
    return '' +
      '<svg viewBox="0 0 ' + w + ' ' + h + '" class="ki-spark" preserveAspectRatio="xMidYMid meet">' +
      '<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) + '" y2="' + (h - pad) + '" stroke="var(--line-strong)" stroke-width="1"/>' +
      '<path d="' + d + '" fill="none" stroke="#1a73e8" stroke-width="2"/>' +
      '<circle cx="' + lastX.toFixed(1) + '" cy="' + lastY.toFixed(1) + '" r="3.5" fill="#1a73e8"/>' +
      '<text x="' + pad + '" y="' + (h - 8) + '" font-size="11" fill="var(--muted)">' + esc(firstLabel) + '</text>' +
      '<text x="' + (w - pad) + '" y="' + (h - 8) + '" font-size="11" fill="var(--muted)" text-anchor="end">' + esc(lastLabel) + '</text>' +
      '<text x="' + (w - pad) + '" y="' + (pad - 8) + '" font-size="11" fill="var(--muted)" text-anchor="end">최고 ' + num(max) + '</text>' +
      '<text x="' + (w - pad) + '" y="' + (h - pad + 14) + '" font-size="11" fill="var(--muted)" text-anchor="end">최저 ' + num(min) + '</text>' +
      '</svg>';
  }

  function openDetail(kind, code, name) {
    if (!code) return;
    $('ki-modal-title').textContent = name + ' 상세 이력';
    $('ki-modal-meta').textContent = '불러오는 중…';
    $('ki-modal-chart').innerHTML = '';
    $('ki-modal-table').querySelector('tbody').innerHTML = '<tr><td colspan="4" class="ki-muted">불러오는 중…</td></tr>';
    modal.hidden = false;

    var req = kind === 'eco'
      ? get('/api/eco/indicators/' + encodeURIComponent(code) + '/history').then(function (rows) {
          return (rows || []).map(function (r) { return { date: r.date, value: r.value, prev_value: r.prev_value, change_pct: r.change_rate }; });
        })
      : get('/api/global-macro/timeseries/' + encodeURIComponent(code)).then(function (d) {
          return ((d && d.data) || []).map(function (r) { return { date: r.date, value: r.value, prev_value: r.prev_value, change_pct: r.change_pct }; });
        });

    req.then(function (points) {
      if (!points.length) {
        $('ki-modal-meta').textContent = '이력 데이터가 없습니다.';
        $('ki-modal-table').querySelector('tbody').innerHTML = '<tr><td colspan="4" class="ki-muted">데이터가 없습니다.</td></tr>';
        return;
      }
      $('ki-modal-meta').textContent = '표시 범위: ' + points[0].date + ' ~ ' + points[points.length - 1].date + ' · 총 ' + points.length.toLocaleString('ko-KR') + '건';
      $('ki-modal-chart').innerHTML = sparkline(points);
      var recent = points.slice().reverse().slice(0, 30);
      $('ki-modal-table').querySelector('tbody').innerHTML = recent.map(function (r) {
        var d = r.prev_value != null && r.value != null ? r.value - r.prev_value : null;
        var cls = d > 0 ? 'up' : d < 0 ? 'down' : '';
        return '<tr><td>' + esc(r.date) + '</td><td class="num">' + num(r.value) + '</td><td class="num ' + cls + '">' + (d == null ? '–' : (d > 0 ? '+' : '') + num(d, 2)) + '</td><td class="num">' + chg(r.change_pct) + '</td></tr>';
      }).join('');
    }).catch(function () {
      $('ki-modal-meta').textContent = '이력을 불러오지 못했습니다. 잠시 후 다시 시도하세요.';
      $('ki-modal-table').querySelector('tbody').innerHTML = '<tr><td colspan="4" class="ki-muted">불러오기 실패</td></tr>';
    });
  }

  $('kr-table').addEventListener('click', function (e) {
    var tr = e.target.closest('tr[data-code]');
    if (tr) openDetail('eco', tr.dataset.code, tr.dataset.name);
  });
  $('gl-table').addEventListener('click', function (e) {
    var tr = e.target.closest('tr[data-code]');
    if (tr) openDetail('global', tr.dataset.code, tr.dataset.name);
  });
})();
