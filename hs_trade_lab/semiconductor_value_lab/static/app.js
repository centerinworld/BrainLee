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
  if (match) {
    return `${match[1]}/api`;
  }
  return "/api";
})();

const money = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  if (Math.abs(n) >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(1)}T`;
  if (Math.abs(n) >= 100_000_000) return `${Math.round(n / 100_000_000)}억`;
  if (Math.abs(n) >= 1_000_000) return `${Math.round(n / 1_000_000)}M`;
  return Math.round(n).toLocaleString("ko-KR");
};

const integer = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return Math.round(n).toLocaleString("ko-KR");
};

const decimal1 = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return n.toLocaleString("ko-KR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
};

const pct = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return `${n > 0 ? "+" : ""}${Math.round(n)}%`;
};

const ratioPct = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return `${Math.round(n * 100)}%`;
};

const benchmarkIcon = (value) => {
  if (!value) return "-";
  if (String(value).includes("아웃퍼폼")) return `<span class="benchmark up-icon" title="${value}">▲</span>`;
  if (String(value).includes("언더퍼폼")) return `<span class="benchmark down-icon" title="${value}">▼</span>`;
  return `<span class="benchmark neutral-icon" title="${value}">•</span>`;
};

const formatDateLabel = (value) => {
  const raw = String(value || "");
  if (!/^\d{8}$/.test(raw)) return value || "-";
  return `${raw.slice(0, 4)}.${raw.slice(4, 6)}.${raw.slice(6, 8)}`;
};

const formatDateTime = (value) => {
  if (!value || value === "-") return "-";
  return String(value).replace("T", " ").slice(0, 19);
};

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`request failed: ${url}`);
  return res.json();
}

async function loadBootstrapData() {
  try {
    return await getJson(`${API_BASE}/bootstrap`);
  } catch (error) {
    const [meta, summary, stocks, watchlist] = await Promise.all([
      getJson(`${API_BASE}/meta`),
      getJson(`${API_BASE}/summary`),
      getJson(`${API_BASE}/stocks`),
      getJson(`${API_BASE}/watchlist`),
    ]);
    return { meta, summary, stocks, watchlist };
  }
}

function renderMeta() {
  const root = document.getElementById("metaCards");
  document.getElementById("refCInput").value = state.meta.ref_c || state.meta.current_label || "";
  document.getElementById("refAInput").value = state.meta.ref_a || "";
  document.getElementById("refBInput").value = state.meta.ref_b || "";
  root.innerHTML = `
    <div class="mini-card"><span>현재 기준일</span><strong>${formatDateLabel(state.meta.current_label || state.meta.ref_c || "-")}</strong></div>
    <div class="mini-card"><span>비교일 A</span><strong>${formatDateLabel(state.meta.ref_a || "-")}</strong></div>
    <div class="mini-card"><span>비교일 B</span><strong>${formatDateLabel(state.meta.ref_b || "-")}</strong></div>
    <div class="mini-card"><span>캐시 갱신</span><strong>${formatDateTime(state.meta.rebuilt_at || "-")}</strong></div>
  `;
}

function renderSummary() {
  const tbody = document.querySelector("#summaryTable tbody");
  tbody.innerHTML = state.summary.map((row) => `
    <tr>
      <td>${integer(row.sector_order)}</td>
      <td>${row.sector_name || row.resolved_sector_key || row.sample_lv1 || "-"}</td>
      <td>${integer(row.stock_count)}</td>
      <td>${integer(row.rising_count)}</td>
      <td>${ratioPct(row.rising_ratio ?? 0)}</td>
      <td>${decimal1(row.avg_psr)}</td>
      <td>${benchmarkIcon(row.kospi_vs)}</td>
      <td>${benchmarkIcon(row.nasdaq_vs)}</td>
      <td>${row.sample_stock || "-"}</td>
      <td>${row.sample_industry || "-"}</td>
    </tr>
  `).join("");
}

function renderLv1Filter() {
  const select = document.getElementById("lv1Filter");
  const lv1s = [...new Set(state.stocks.map((item) => item.lv1).filter(Boolean))];
  select.innerHTML = `<option value="">전체 Lv1</option>` + lv1s.map((lv1) => `<option value="${lv1}">${lv1}</option>`).join("");
}

function renderStocks() {
  const lv1 = document.getElementById("lv1Filter").value;
  const items = lv1 ? state.stocks.filter((item) => item.lv1 === lv1) : state.stocks;
  const tbody = document.querySelector("#stocksTable tbody");
  let currentLv1 = null;
  const rows = [];
  items.forEach((item) => {
    const nextLv1 = item.lv1 || "미분류";
    if (!lv1 && nextLv1 !== currentLv1) {
      currentLv1 = nextLv1;
      rows.push(`
        <tr class="group-row">
          <td colspan="14">
            <span class="group-chip">${currentLv1}</span>
          </td>
        </tr>
      `);
    }
    rows.push(`
      <tr data-code="${item.stock_code}">
        <td class="name">${item.stock_name}<span>${item.stock_code}</span></td>
        <td>${lv1 ? (item.lv1 || "-") : ""}</td>
        <td>${item.lv2 || "-"}</td>
        <td>${item.customer || "-"}</td>
        <td>${integer(item.current_price)}</td>
        <td class="${(item.ref_a_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_a_change_pct)}</td>
        <td class="${(item.ref_b_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_b_change_pct)}</td>
        <td class="${(item.ref_c_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_c_change_pct)}</td>
        <td>${money(item.current_market_cap)}</td>
        <td>${decimal1(item.current_pbr)}</td>
        <td>${decimal1(item.current_per)}</td>
        <td>${money(item.latest_revenue)}</td>
        <td class="${(item.annual_yoy || 0) > 0 ? "up" : "down"}">${pct(item.annual_yoy)}</td>
        <td class="${(item.quarter_qoq || 0) > 0 ? "up" : "down"}">${pct(item.quarter_qoq)}</td>
      </tr>
    `);
  });
  tbody.innerHTML = rows.join("");
  tbody.querySelectorAll("tr[data-code]").forEach((row) => {
    row.addEventListener("click", () => loadStockDetail(row.dataset.code));
  });
}

function renderWatchlist() {
  const tbody = document.querySelector("#watchTable tbody");
  tbody.innerHTML = state.watchlist.map((item) => `
    <tr>
      <td class="name">${item.stock_name}<span>${item.stock_code}</span></td>
      <td>${item.lv1 || "-"}</td>
      <td>${item.lv2_override || item.lv2 || "-"}</td>
      <td>${item.customer_override || item.customer || "-"}</td>
      <td>${integer(item.current_price)}</td>
      <td class="${(item.ref_a_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_a_change_pct)}</td>
      <td class="${(item.ref_b_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_b_change_pct)}</td>
      <td class="${(item.ref_c_change_pct || 0) > 0 ? "up" : "down"}">${pct(item.ref_c_change_pct)}</td>
      <td class="${(item.annual_yoy || 0) > 0 ? "up" : "down"}">${pct(item.annual_yoy)}</td>
      <td class="${(item.quarter_qoq || 0) > 0 ? "up" : "down"}">${pct(item.quarter_qoq)}</td>
      <td>${item.memo || "-"}</td>
      <td><button class="ghost delete-btn" data-code="${item.stock_code}">삭제</button></td>
    </tr>
  `).join("");
  tbody.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`${API_BASE}/watchlist/${btn.dataset.code}`, { method: "DELETE" });
      await refreshLive();
    });
  });
}

function renderStockDetail(payload) {
  state.selectedStock = payload.stock;
  document.getElementById("stockDetailEmpty").classList.add("hidden");
  document.getElementById("stockDetail").classList.remove("hidden");
  document.getElementById("detailHeader").innerHTML = `
    <div>
      <h3>${payload.stock.stock_name} <span>${payload.stock.stock_code}</span></h3>
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
      <td>${row.item_type}</td>
      <td>${row.fiscal_year}</td>
      <td>${money(row.value)}</td>
    </tr>
  `).join("");
  document.querySelector("#quarterlyTable tbody").innerHTML = payload.quarterly.map((row) => `
    <tr>
      <td>${row.item_type}</td>
      <td>${row.period_label}</td>
      <td>${money(row.value)}</td>
    </tr>
  `).join("");
}

async function loadStockDetail(stockCode) {
  const payload = await getJson(`${API_BASE}/stock/${stockCode}`);
  renderStockDetail(payload);
}

function applyBootstrap(data) {
  state.meta = data.meta || {};
  state.summary = data.summary?.items || [];
  state.stocks = data.stocks?.items || [];
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
  const active = document.activeElement;
  const editing = active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName);
  if (editing) return;
  const data = await loadBootstrapData();
  applyBootstrap(data);
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((node) => node.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
  });
});

document.getElementById("lv1Filter").addEventListener("change", renderStocks);
document.getElementById("reloadStocks").addEventListener("click", boot);
document.getElementById("metaForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  const res = await fetch(`${API_BASE}/reference-dates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  document.getElementById("rebuildStatus").textContent = data.note || data.message || "";
});

document.getElementById("rebuildCache").addEventListener("click", async () => {
  const status = document.getElementById("rebuildStatus");
  status.textContent = "캐시 재계산 중...";
  const res = await fetch(`${API_BASE}/rebuild`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    status.textContent = `재계산 실패: ${data.message || "unknown error"}`;
    return;
  }
  status.textContent = "캐시 재계산 완료. 최신 데이터를 다시 불러옵니다.";
  await boot();
});

document.getElementById("watchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  await fetch(`${API_BASE}/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  event.currentTarget.reset();
  await refreshLive();
});

boot().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML("beforeend", `<div class="error-box">로딩 실패: ${error.message}</div>`);
});

state.refreshTimer = window.setInterval(() => {
  refreshLive().catch(() => {});
}, 300000);
