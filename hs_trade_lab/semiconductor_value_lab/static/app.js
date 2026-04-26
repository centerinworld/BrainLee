/* ── 반도체 Value Stream Lab — app.js v20260426 ── */

const state = {
  meta: {},
  summary: [],
  stocks: [],
  watchlist: [],
  selectedStock: null,
  refreshTimer: null,
};

const API_BASE = (() => {
  const path = window.location.pathname || "/";
  const match = path.match(/^(.*?\/semiconductor-lab)(?:\/|$)/);
  return match ? `${match[1]}/api` : "/api";
})();

/* ────────────────────────────────────
   포맷 유틸
──────────────────────────────────── */
const money = (value) => {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (isNaN(n)) return "-";
  if (Math.abs(n) >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(1)}T`;
  if (Math.abs(n) >= 100_000_000)       return `${Math.round(n / 100_000_000)}억`;
  if (Math.abs(n) >= 1_000_000)         return `${Math.round(n / 1_000_000)}M`;
  return Math.round(n).toLocaleString("ko-KR");
};

const integer = (value) => {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (isNaN(n)) return "-";
  return Math.round(n).toLocaleString("ko-KR");
};

const decimal1 = (value) => {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (isNaN(n)) return "-";
  return n.toLocaleString("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
};

/* 주가변동률: 소수점 1자리 */
const pct = (value) => {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (isNaN(n)) return "-";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
};

const ratioPct = (value) => {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (isNaN(n)) return "-";
  return `${Math.round(n * 100)}%`;
};

/* 아웃퍼폼/언더퍼폼 아이콘 */
const benchmarkIcon = (value) => {
  if (!value) return "<span class=\"benchmark neutral-icon\" title=\"-\">•</span>";
  const s = String(value);
  if (s.includes("아웃퍼폼") || s.includes("outperform"))
    return `<span class="benchmark up-icon" title="${s}">▲</span>`;
  if (s.includes("언더퍼폼") || s.includes("underperform"))
    return `<span class="benchmark down-icon" title="${s}">▼</span>`;
  return `<span class="benchmark neutral-icon" title="${s}">•</span>`;
};

/* YYYYMMDD → YYYY.MM.DD */
const formatDateLabel = (value) => {
  const raw = String(value || "");
  if (!/^\d{8}$/.test(raw)) return value || "-";
  return `${raw.slice(0, 4)}.${raw.slice(4, 6)}.${raw.slice(6, 8)}`;
};

/* YYYYMMDD → <input type="date"> value (YYYY-MM-DD) */
const yyyymmddToInput = (v) => {
  const s = String(v || "");
  if (!/^\d{8}$/.test(s)) return "";
  return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
};

/* YYYY-MM-DD → YYYYMMDD */
const inputToYyyymmdd = (v) => (v || "").replace(/-/g, "");

const formatDateTime = (value) => {
  if (!value || value === "-") return "-";
  return String(value).replace("T", " ").slice(0, 19);
};

/* ────────────────────────────────────
   API 호출
──────────────────────────────────── */
async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`request failed: ${url}`);
  return res.json();
}

async function loadBootstrapData() {
  try {
    return await getJson(`${API_BASE}/bootstrap`);
  } catch {
    const [meta, summary, stocks, watchlist] = await Promise.all([
      getJson(`${API_BASE}/meta`),
      getJson(`${API_BASE}/summary`),
      getJson(`${API_BASE}/stocks`),
      getJson(`${API_BASE}/watchlist`),
    ]);
    return { meta, summary, stocks, watchlist };
  }
}

/* ────────────────────────────────────
   렌더: 메타 카드 + 기준일 입력
──────────────────────────────────── */
function renderMeta() {
  const refC = state.meta.ref_c || state.meta.current_label || "";
  const refA = state.meta.ref_a || "";
  const refB = state.meta.ref_b || "";

  /* 미니 카드 */
  document.getElementById("metaCards").innerHTML = `
    <div class="mini-card">
      <span>현재 기준일 (C)</span>
      <strong>${formatDateLabel(refC)}</strong>
    </div>
    <div class="mini-card">
      <span>비교일 A</span>
      <strong>${formatDateLabel(refA)}</strong>
    </div>
    <div class="mini-card">
      <span>비교일 B</span>
      <strong>${formatDateLabel(refB)}</strong>
    </div>
    <div class="mini-card">
      <span>캐시 갱신</span>
      <strong style="font-size:0.78rem;">${formatDateTime(state.meta.rebuilt_at || "-")}</strong>
    </div>
  `;

  /* 날짜 입력 폼 동기화 */
  document.getElementById("refCInput").value = yyyymmddToInput(refC);
  document.getElementById("refAInput").value = yyyymmddToInput(refA);
  document.getElementById("refBInput").value = yyyymmddToInput(refB);

  /* 전종목 컬럼 헤더에 날짜 반영 */
  const hA = document.getElementById("colHeadA");
  const hB = document.getElementById("colHeadB");
  const hC = document.getElementById("colHeadC");
  if (hA) hA.textContent = refA ? `${formatDateLabel(refA)} 대비` : "A대비";
  if (hB) hB.textContent = refB ? `${formatDateLabel(refB)} 대비` : "B대비";
  if (hC) hC.textContent = refC ? `${formatDateLabel(refC)} 대비` : "C대비";
}

/* ────────────────────────────────────
   렌더: 섹터 종합 테이블
──────────────────────────────────── */
function renderSummary() {
  const tbody = document.querySelector("#summaryTable tbody");
  tbody.innerHTML = state.summary.map((row) => `
    <tr>
      <td>${integer(row.sector_order)}</td>
      <td style="font-weight:700;">${row.sector_name || row.resolved_sector_key || row.sample_lv1 || "-"}</td>
      <td>${integer(row.stock_count)}</td>
      <td>${integer(row.rising_count)}</td>
      <td>${ratioPct(row.rising_ratio ?? 0)}</td>
      <td>${decimal1(row.avg_psr)}</td>
      <td style="text-align:center;">${benchmarkIcon(row.kospi_vs)}</td>
      <td style="text-align:center;">${benchmarkIcon(row.nasdaq_vs)}</td>
      <td style="font-weight:600;">${row.sample_stock || "-"}</td>
      <td style="color:var(--text-secondary);">${row.sample_industry || "-"}</td>
    </tr>
  `).join("");
}

/* ────────────────────────────────────
   렌더: 전종목 Lv1 필터
──────────────────────────────────── */
function renderLv1Filter() {
  const select = document.getElementById("lv1Filter");
  const lv1s = [...new Set(state.stocks.map((i) => i.lv1).filter(Boolean))];
  select.innerHTML =
    `<option value="">전체 Lv1</option>` +
    lv1s.map((lv1) => `<option value="${lv1}">${lv1}</option>`).join("");
}

/* ────────────────────────────────────
   렌더: 전종목 테이블 (완성 열 제외)
──────────────────────────────────── */
function renderStocks() {
  const lv1   = document.getElementById("lv1Filter").value;
  const items = lv1 ? state.stocks.filter((i) => i.lv1 === lv1) : state.stocks;
  const tbody = document.querySelector("#stocksTable tbody");
  let currentLv1 = null;
  const rows = [];

  items.forEach((item) => {
    const nextLv1 = item.lv1 || "미분류";
    if (!lv1 && nextLv1 !== currentLv1) {
      currentLv1 = nextLv1;
      rows.push(`
        <tr class="group-row">
          <td colspan="14"><span class="group-chip">${currentLv1}</span></td>
        </tr>
      `);
    }
    const aClass = (item.ref_a_change_pct || 0) >= 0 ? "up" : "down";
    const bClass = (item.ref_b_change_pct || 0) >= 0 ? "up" : "down";
    const cClass = (item.ref_c_change_pct || 0) >= 0 ? "up" : "down";
    const yoyClass = (item.annual_yoy || 0) >= 0 ? "up" : "down";
    const qoqClass = (item.quarter_qoq || 0) >= 0 ? "up" : "down";

    rows.push(`
      <tr data-code="${item.stock_code}">
        <td class="name">${item.stock_name}<span>${item.stock_code}</span></td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${lv1 ? "" : (item.lv1 || "-")}</td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.lv2 || "-"}</td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.customer || "-"}</td>
        <td style="font-weight:700;">${integer(item.current_price)}</td>
        <td class="${aClass}">${pct(item.ref_a_change_pct)}</td>
        <td class="${bClass}">${pct(item.ref_b_change_pct)}</td>
        <td class="${cClass}">${pct(item.ref_c_change_pct)}</td>
        <td>${money(item.current_market_cap)}</td>
        <td>${decimal1(item.current_pbr)}</td>
        <td>${decimal1(item.current_per)}</td>
        <td>${money(item.latest_revenue)}</td>
        <td class="${yoyClass}">${pct(item.annual_yoy)}</td>
        <td class="${qoqClass}">${pct(item.quarter_qoq)}</td>
      </tr>
    `);
  });

  tbody.innerHTML = rows.join("");
  tbody.querySelectorAll("tr[data-code]").forEach((row) => {
    row.addEventListener("click", () => loadStockDetail(row.dataset.code));
  });
}

/* ────────────────────────────────────
   렌더: 관심종목 테이블
──────────────────────────────────── */
function renderWatchlist() {
  const tbody = document.querySelector("#watchTable tbody");
  tbody.innerHTML = state.watchlist.map((item) => {
    const aClass = (item.ref_a_change_pct || 0) >= 0 ? "up" : "down";
    const bClass = (item.ref_b_change_pct || 0) >= 0 ? "up" : "down";
    const cClass = (item.ref_c_change_pct || 0) >= 0 ? "up" : "down";
    const yoyClass = (item.annual_yoy || 0) >= 0 ? "up" : "down";
    const qoqClass = (item.quarter_qoq || 0) >= 0 ? "up" : "down";
    return `
      <tr>
        <td class="name">${item.stock_name}<span>${item.stock_code}</span></td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.lv1 || "-"}</td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.lv2_override || item.lv2 || "-"}</td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.customer_override || item.customer || "-"}</td>
        <td style="font-weight:700;">${integer(item.current_price)}</td>
        <td class="${aClass}">${pct(item.ref_a_change_pct)}</td>
        <td class="${bClass}">${pct(item.ref_b_change_pct)}</td>
        <td class="${cClass}">${pct(item.ref_c_change_pct)}</td>
        <td class="${yoyClass}">${pct(item.annual_yoy)}</td>
        <td class="${qoqClass}">${pct(item.quarter_qoq)}</td>
        <td style="color:var(--text-secondary);font-size:0.75rem;">${item.memo || "-"}</td>
        <td><button class="delete-btn" data-code="${item.stock_code}">삭제</button></td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`${API_BASE}/watchlist/${btn.dataset.code}`, { method: "DELETE" });
      await refreshLive();
    });
  });
}

/* ────────────────────────────────────
   종목 상세 렌더
──────────────────────────────────── */
function renderStockDetail(payload) {
  state.selectedStock = payload.stock;
  document.getElementById("stockDetailEmpty").classList.add("hidden");
  document.getElementById("stockDetail").classList.remove("hidden");

  document.getElementById("detailHeader").innerHTML = `
    <div>
      <h3>${payload.stock.stock_name} <span style="color:var(--text-secondary);font-size:0.75rem;">${payload.stock.stock_code}</span></h3>
      <p>${payload.stock.lv1 || "-"} / ${payload.stock.lv2 || "-"} / ${payload.stock.customer || "고객 미입력"}</p>
    </div>
    <div class="tag-list">
      <span class="tag">${payload.stock.main_business || "주요업 비어있음"}</span>
      <span class="tag">${payload.stock.last_quarter_label || "최근 분기 없음"}</span>
    </div>
  `;

  document.getElementById("detailMetrics").innerHTML = `
    <div class="mini-card"><span>현재가</span><strong>${integer(payload.stock.current_price)}</strong></div>
    <div class="mini-card"><span>시가총액</span><strong>${money(payload.stock.current_market_cap)}</strong></div>
    <div class="mini-card"><span>최근 매출</span><strong>${money(payload.stock.latest_revenue)}</strong></div>
    <div class="mini-card"><span>YoY / QoQ</span><strong>${pct(payload.stock.annual_yoy)} / ${pct(payload.stock.quarter_qoq)}</strong></div>
  `;

  document.querySelector("#yearlyTable tbody").innerHTML = payload.yearly.map((row) => `
    <tr>
      <td style="color:var(--text-secondary);font-size:0.75rem;">${row.item_type}</td>
      <td>${row.fiscal_year}</td>
      <td>${money(row.value)}</td>
    </tr>
  `).join("");

  document.querySelector("#quarterlyTable tbody").innerHTML = payload.quarterly.map((row) => `
    <tr>
      <td style="color:var(--text-secondary);font-size:0.75rem;">${row.item_type}</td>
      <td>${row.period_label}</td>
      <td>${money(row.value)}</td>
    </tr>
  `).join("");
}

async function loadStockDetail(stockCode) {
  const payload = await getJson(`${API_BASE}/stock/${stockCode}`);
  renderStockDetail(payload);
}

/* ────────────────────────────────────
   부트스트랩 / 새로고침
──────────────────────────────────── */
function applyBootstrap(data) {
  state.meta      = data.meta || {};
  state.summary   = data.summary?.items || [];
  state.stocks    = data.stocks?.items  || [];
  state.watchlist = data.watchlist?.items || [];
  renderMeta();
  renderSummary();
  renderLv1Filter();
  renderStocks();
  renderWatchlist();
}

async function boot() {
  document.getElementById("rebuildStatus").textContent = "";
  const data = await loadBootstrapData();
  applyBootstrap(data);
}

async function refreshLive() {
  const active  = document.activeElement;
  const editing = active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName);
  if (editing) return;
  const data = await loadBootstrapData();
  applyBootstrap(data);
}

/* ────────────────────────────────────
   이벤트 바인딩
──────────────────────────────────── */

/* 탭 전환 */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((n) => n.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((n) => n.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
  });
});

/* 전종목 Lv1 필터 */
document.getElementById("lv1Filter").addEventListener("change", renderStocks);

/* 전종목 새로고침 버튼 */
document.getElementById("reloadStocks").addEventListener("click", boot);

/* 기준일 저장 (종합 탭 인라인 폼) */
document.getElementById("saveDates").addEventListener("click", async () => {
  const cVal = inputToYyyymmdd(document.getElementById("refCInput").value);
  const aVal = inputToYyyymmdd(document.getElementById("refAInput").value);
  const bVal = inputToYyyymmdd(document.getElementById("refBInput").value);
  const payload = {};
  if (cVal) payload.ref_c = cVal;
  if (aVal) payload.ref_a = aVal;
  if (bVal) payload.ref_b = bVal;
  const res  = await fetch(`${API_BASE}/reference-dates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  const note = document.getElementById("rebuildStatus");
  note.textContent = data.note || data.message || "저장 완료";
  setTimeout(() => { note.textContent = ""; }, 4000);
  await boot();
});

/* 캐시 재계산 */
document.getElementById("rebuildCache").addEventListener("click", async () => {
  const note = document.getElementById("rebuildStatus");
  note.textContent = "캐시 재계산 중...";
  const res  = await fetch(`${API_BASE}/rebuild`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    note.textContent = `재계산 실패: ${data.message || "오류"}`;
    return;
  }
  note.textContent = "✓ 재계산 완료 — 최신 데이터로 새로고침했습니다.";
  setTimeout(() => { note.textContent = ""; }, 5000);
  await boot();
});

/* 관심종목 추가 폼 */
document.getElementById("watchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form    = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  await fetch(`${API_BASE}/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  event.currentTarget.reset();
  await refreshLive();
});

/* ────────────────────────────────────
   초기 로드
──────────────────────────────────── */
boot().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML(
    "beforeend",
    `<div class="error-box">로딩 실패: ${error.message}</div>`,
  );
});

/* 5분마다 자동 갱신 */
state.refreshTimer = window.setInterval(() => {
  refreshLive().catch(() => {});
}, 300_000);
