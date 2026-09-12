/* ============================================
   KAI News Center - 통합 프론트엔드
   ============================================ */

const API = "http://127.0.0.1:8011";
const $ = (q) => document.querySelector(q);
const $$ = (q) => document.querySelectorAll(q);
const esc = (t) => (t == null ? "" : String(t).replace(/[&<>"]/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]));

const state = { page: "dashboard", data: [], lastFetch: null };

// ============================================
// API
// ============================================

async function api(path) {
  const r = await fetch(`${API}${path}`, { headers: { "Content-Type": "application/json" } });
  if (!r.ok) { const d = await r.json().catch(()=>({})); throw new Error(d.detail || `HTTP ${r.status}`); }
  return r.json();
}

// ============================================
// Data
// ============================================

async function loadData() {
  loading(true);
  try {
    const d = await api("/api/eco/indicators?role=admin").catch(() => null);
    state.data = (d && d.length > 0) ? d : mockData();
    state.lastFetch = new Date();
    tick();
    if (state.page !== "admin") render();
  } catch { state.data = mockData(); state.lastFetch = new Date(); if (state.page !== "admin") render(); }
  finally { loading(false); }
}

function mockData() {
  const d = new Date().toISOString().split("T")[0];
  return [
    { indicator_code:"EXCHANGE_USD", name_kr:"원/달러 환율", category:"exchange", date:d, value:1378.50, change_rate:-0.26 },
    { indicator_code:"EXCHANGE_JPY", name_kr:"원/100엔 환율", category:"exchange", date:d, value:925.30, change_rate:0.56 },
    { indicator_code:"EXCHANGE_CNY", name_kr:"원/위안 환율", category:"exchange", date:d, value:191.20, change_rate:-0.31 },
    { indicator_code:"EXCHANGE_EUR", name_kr:"원/유로 환율", category:"exchange", date:d, value:1502.80, change_rate:0.29 },
    { indicator_code:"BASE_RATE", name_kr:"기준금리", category:"interest", date:d, value:3.50, change_rate:0 },
    { indicator_code:"BOND_YIELD_3Y", name_kr:"국고채 3년", category:"interest", date:d, value:3.14, change_rate:-1.26 },
    { indicator_code:"BOND_YIELD_10Y", name_kr:"국고채 10년", category:"interest", date:d, value:3.42, change_rate:-0.87 },
    { indicator_code:"CALL_RATE", name_kr:"콜금리", category:"interest", date:d, value:3.51, change_rate:0 },
    { indicator_code:"CPI_CHANGE", name_kr:"소비자물가", category:"economy", date:d, value:2.40, change_rate:-4.00 },
    { indicator_code:"CORE_CPI", name_kr:"근원물가", category:"economy", date:d, value:2.20, change_rate:-4.35 },
    { indicator_code:"GDP_GROWTH", name_kr:"GDP 성장률", category:"economy", date:d, value:1.30, change_rate:-7.14 },
    { indicator_code:"UNEMPLOYMENT_RATE", name_kr:"실업률", category:"economy", date:d, value:2.80, change_rate:-3.45 },
    { indicator_code:"FOREIGN_RESERVE", name_kr:"외환보유액", category:"economy", date:d, value:4150, change_rate:0.44 },
    { indicator_code:"EXPORT_AMOUNT", name_kr:"수출액", category:"economy", date:d, value:57100, change_rate:1.42 },
    { indicator_code:"IMPORT_AMOUNT", name_kr:"수입액", category:"economy", date:d, value:52300, change_rate:0.97 },
    { indicator_code:"TRADE_BALANCE", name_kr:"무역수지", category:"economy", date:d, value:4800, change_rate:6.67 },
    { indicator_code:"EMPLOYMENT_RATE", name_kr:"고용률", category:"economy", date:d, value:63.20, change_rate:0.16 },
  ];
}

// ============================================
// Navigation
// ============================================

async function go(page) {
  state.page = page;
  $$(".kai-tab").forEach((t) => t.classList.toggle("active", t.dataset.page === page));

  const isAdmin = page === "admin";
  const kaiShell = $("#kaiShell");
  const adminConsole = $("#adminConsole");

  if (isAdmin) {
    // KAI 숨기고 관리자 콘솔 표시
    kaiShell.style.display = "none";
    adminConsole.style.display = "flex";
    
    // Runtime init() 호출 (admin-console-runtime-v2.js의 __adminInit)
    if (window.__adminInit && typeof window.__adminInit === "function") {
      // init()이 여러 번 호출되어도 괜찮도록 처리
      window.__adminInit();
    }
  } else {
    kaiShell.style.display = "block";
    adminConsole.style.display = "none";
    render();
  }
}

// ============================================
// Render
// ============================================

function render() {
  const p = state.page;
  if (p === "dashboard") renderDashboard();
  else if (p === "exchange") renderCat("exchange", "💱 환율 정보", "주요 통화별 원화 환율");
  else if (p === "interest") renderCat("interest", "🏦 금리 정보", "기준금리 및 시장금리");
  else if (p === "economy") renderCat("economy", "📈 경제 지표", "국내 거시경제 지표");
}

function renderDashboard() {
  const d = state.data;
  if (!d.length) { $("#kaiMain").innerHTML = `<div class="kai-empty"><p>데이터 없음</p></div>`; return; }

  const ex = d.filter(i => i.category === "exchange");
  const ir = d.filter(i => i.category === "interest");
  const ec = d.filter(i => i.category === "economy");

  $("#kaiMain").innerHTML = `
    <div class="kpi-grid">${pickKPIs(d).map(kpiCard).join("")}</div>
    ${ex.length ? sec("💱 환율", "exchange", ex.map(indRow).join("")) : ""}
    ${ir.length ? sec("🏦 금리", "interest", ir.map(indRow).join("")) : ""}
    ${ec.length ? sec("📈 경제 지표", "economy", ec.map(indRow).join("")) : ""}
  `;
}

function renderCat(cat, title, desc) {
  const items = state.data.filter(i => i.category === cat);
  $("#kaiMain").innerHTML = `
    <div style="margin-bottom:20px;"><h2 style="font-size:18px;font-weight:700;margin:0;">${title}</h2><p style="font-size:13px;color:#8892a8;margin:4px 0 0;">${desc}</p></div>
    ${items.length ? `<div class="section-card"><div class="section-bd" style="padding:0;">${items.map((i, idx) => `<div style="padding:12px 18px;${idx < items.length-1 ? 'border-bottom:1px solid #e2e6ef;' : ''}">${indRow(i)}</div>`).join("")}</div></div>`
    : `<div class="kai-empty"><p>데이터 없음</p></div>`}
  `;
}

function kpiCard(k) {
  const c = k.change_rate;
  const cls = c > 0 ? "up" : c < 0 ? "down" : "neutral";
  const arr = c > 0 ? "▲" : c < 0 ? "▼" : "―";
  return `<div class="kpi-card"><div class="kpi-label">${esc(k.name_kr)}</div><div class="kpi-value">${k.value != null ? fmt(k.value, k.indicator_code) : "—"}</div>${c != null ? `<div class="kpi-change ${cls}">${arr} ${Math.abs(c).toFixed(2)}%</div>` : ""}<div class="kpi-date">${k.date || ""}</div></div>`;
}

function indRow(i) {
  const c = i.change_rate;
  const cls = c > 0 ? "up" : c < 0 ? "down" : "neutral";
  const arr = c > 0 ? "▲" : c < 0 ? "▼" : "―";
  return `<div class="ind-row"><div><div class="ind-name">${esc(i.name_kr)}</div><div class="ind-meta">${i.date || ""}</div></div><div><div class="ind-val">${i.value != null ? fmt(i.value, i.indicator_code) : "—"}</div><div class="ind-chg ${cls}">${c != null ? `${arr} ${Math.abs(c).toFixed(2)}%` : "—"}</div></div></div>`;
}

function sec(title, page, body) {
  return `<div class="section-card"><div class="section-hd"><h3>${title}</h3><button data-page="${page}">자세히 →</button></div><div class="section-bd">${body}</div></div>`;
}

function fmt(v, c) {
  if (v == null) return "—";
  if (["BASE_RATE","BOND_YIELD_3Y","BOND_YIELD_10Y","CALL_RATE","CPI_CHANGE","CORE_CPI","UNEMPLOYMENT_RATE","EMPLOYMENT_RATE","GDP_GROWTH"].includes(c)) return v.toFixed(2)+"%";
  if (["FOREIGN_RESERVE","EXPORT_AMOUNT","IMPORT_AMOUNT","TRADE_BALANCE"].includes(c)) return v >= 1000 ? (v/1000).toFixed(1)+"조" : v.toFixed(0);
  if (c.includes("EXCHANGE")) return v.toFixed(2)+"원";
  return v.toLocaleString();
}

function pickKPIs(data) {
  const p = ["EXCHANGE_USD","BASE_RATE","CPI_CHANGE","UNEMPLOYMENT_RATE"];
  const r = [];
  for (const c of p) { const f = data.find(i => i.indicator_code === c); if (f) r.push(f); }
  return r.slice(0, 4);
}

function loading(v) {
  const m = $("#kaiMain");
  if (v) m.innerHTML = `<div class="kai-loading"><div class="spinner"></div><p>경제 데이터를 불러오는 중...</p></div>`;
}

function tick() {
  const el = $("#kaiTime");
  if (!state.lastFetch) return;
  const d = Math.floor((Date.now() - state.lastFetch) / 1000);
  el.textContent = d < 60 ? "방금 전" : d < 3600 ? `${Math.floor(d/60)}분 전` : state.lastFetch.toLocaleString("ko-KR", { month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" });
}

// ============================================
// Init
// ============================================

document.addEventListener("DOMContentLoaded", () => {
  // Nav tabs
  $("#kaiNav").addEventListener("click", (e) => {
    const tab = e.target.closest(".kai-tab");
    if (tab) go(tab.dataset.page);
  });

  // In-page links
  $("#kaiMain").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-page]");
    if (btn) go(btn.dataset.page);
  });

  // Refresh
  $("#kaiRefresh").addEventListener("click", loadData);

  // Load
  loadData();
  setInterval(tick, 30000);
});
