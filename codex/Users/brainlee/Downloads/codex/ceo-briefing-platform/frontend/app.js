/* ============================================
   KAI News Center - Frontend App
   ============================================ */

const API_BASE = "http://127.0.0.1:8011";
const ROLE = "admin";

// ============================================
// State
// ============================================

const state = {
  activeTab: "dashboard",
  indicators: [],       // all indicator data
  news: [],             // news articles
  lastFetchTime: null,
  loading: false,
};

// ============================================
// DOM Refs
// ============================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const mainContent = $("#mainContent");
const lastUpdated = $("#lastUpdated");
const refreshBtn = $("#refreshBtn");
const navTabs = $("#navTabs");
const loadingScreen = $("#loadingScreen");

// ============================================
// API Helpers
// ============================================

async function api(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return res.json();
}

// ============================================
// Data Fetching
// ============================================

async function fetchAllData() {
  state.loading = true;
  showLoading(true);

  try {
    // Fetch indicator data from the backend API
    const [indicators, newsFeeds] = await Promise.all([
      fetchIndicators(),
      fetchNews(),
    ]);

    state.indicators = indicators;
    state.news = newsFeeds;
    state.lastFetchTime = new Date();
    updateLastUpdated();

    renderActiveTab();
  } catch (err) {
    console.error("Data fetch error:", err);
    showError(`데이터를 불러오는 중 오류가 발생했습니다: ${err.message}`);
  } finally {
    state.loading = false;
    showLoading(false);
  }
}

async function fetchIndicators() {
  try {
    // Try to get latest values from the eco_db API
    const data = await api(`/api/eco/indicators?role=${ROLE}`).catch(() => null);
    if (data && data.length > 0) return data;

    // Fallback: try fetch_log endpoint
    const log = await api(`/api/eco/log?role=${ROLE}`).catch(() => null);
    if (log && log.length > 0) {
      // Transform log data into indicator format
      return log.map((item) => ({
        indicator_code: item.source || item.id,
        date: item.fetched_at?.split("T")[0] || "",
        value: null,
        prev_value: null,
        change_rate: null,
        source_ref: item.source,
        status: item.status,
      }));
    }

    return [];
  } catch {
    // If backend is not available, use mock data for demo
    return getMockIndicators();
  }
}

async function fetchNews() {
  try {
    // Try to fetch from KAI RSS feeds
    const payload = await api(`/feeds/company?role=${ROLE}`).catch(() => null);
    if (payload && payload.queued) return payload.queued;

    return [];
  } catch {
    return [];
  }
}

// ============================================
// Mock Data (for demo when backend is off)
// ============================================

function getMockIndicators() {
  const today = new Date();
  const dateStr = today.toISOString().split("T")[0];
  const prevDate = new Date(today);
  prevDate.setDate(prevDate.getDate() - 1);
  const prevDateStr = prevDate.toISOString().split("T")[0];

  return [
    {
      indicator_code: "EXCHANGE_USD",
      name_kr: "원/달러 환율",
      category: "exchange",
      date: dateStr,
      value: 1378.50,
      prev_value: 1382.10,
      change_rate: -0.26,
    },
    {
      indicator_code: "EXCHANGE_JPY",
      name_kr: "원/100엔 환율",
      category: "exchange",
      date: dateStr,
      value: 925.30,
      prev_value: 920.15,
      change_rate: 0.56,
    },
    {
      indicator_code: "EXCHANGE_CNY",
      name_kr: "원/위안 환율",
      category: "exchange",
      date: dateStr,
      value: 191.20,
      prev_value: 191.80,
      change_rate: -0.31,
    },
    {
      indicator_code: "EXCHANGE_EUR",
      name_kr: "원/유로 환율",
      category: "exchange",
      date: dateStr,
      value: 1502.80,
      prev_value: 1498.50,
      change_rate: 0.29,
    },
    {
      indicator_code: "KOSPI",
      name_kr: "코스피",
      category: "stock",
      date: dateStr,
      value: 2745.32,
      prev_value: 2723.15,
      change_rate: 0.81,
    },
    {
      indicator_code: "KOSDAQ",
      name_kr: "코스닥",
      category: "stock",
      date: dateStr,
      value: 852.47,
      prev_value: 858.90,
      change_rate: -0.75,
    },
    {
      indicator_code: "BASE_RATE",
      name_kr: "기준금리",
      category: "interest",
      date: dateStr,
      value: 3.50,
      prev_value: 3.50,
      change_rate: 0.00,
    },
    {
      indicator_code: "BOND_YIELD_3Y",
      name_kr: "국고채 3년",
      category: "interest",
      date: dateStr,
      value: 3.14,
      prev_value: 3.18,
      change_rate: -1.26,
    },
    {
      indicator_code: "BOND_YIELD_10Y",
      name_kr: "국고채 10년",
      category: "interest",
      date: dateStr,
      value: 3.42,
      prev_value: 3.45,
      change_rate: -0.87,
    },
    {
      indicator_code: "CALL_RATE",
      name_kr: "콜금리",
      category: "interest",
      date: dateStr,
      value: 3.51,
      prev_value: 3.51,
      change_rate: 0.00,
    },
    {
      indicator_code: "CPI_CHANGE",
      name_kr: "소비자물가 (전년동월비)",
      category: "economy",
      date: dateStr,
      value: 2.40,
      prev_value: 2.50,
      change_rate: -4.00,
    },
    {
      indicator_code: "CORE_CPI",
      name_kr: "근원물가",
      category: "economy",
      date: dateStr,
      value: 2.20,
      prev_value: 2.30,
      change_rate: -4.35,
    },
    {
      indicator_code: "GDP_GROWTH",
      name_kr: "GDP 성장률",
      category: "economy",
      date: dateStr,
      value: 1.30,
      prev_value: 1.40,
      change_rate: -7.14,
    },
    {
      indicator_code: "UNEMPLOYMENT_RATE",
      name_kr: "실업률",
      category: "economy",
      date: dateStr,
      value: 2.80,
      prev_value: 2.90,
      change_rate: -3.45,
    },
    {
      indicator_code: "FOREIGN_RESERVE",
      name_kr: "외환보유액 (억$)",
      category: "economy",
      date: dateStr,
      value: 4150.00,
      prev_value: 4132.00,
      change_rate: 0.44,
    },
    {
      indicator_code: "EXPORT_AMOUNT",
      name_kr: "수출 (백만$)",
      category: "economy",
      date: dateStr,
      value: 57100.00,
      prev_value: 56300.00,
      change_rate: 1.42,
    },
    {
      indicator_code: "IMPORT_AMOUNT",
      name_kr: "수입 (백만$)",
      category: "economy",
      date: dateStr,
      value: 52300.00,
      prev_value: 51800.00,
      change_rate: 0.97,
    },
    {
      indicator_code: "TRADE_BALANCE",
      name_kr: "무역수지 (백만$)",
      category: "economy",
      date: dateStr,
      value: 4800.00,
      prev_value: 4500.00,
      change_rate: 6.67,
    },
    {
      indicator_code: "EMPLOYMENT_RATE",
      name_kr: "고용률",
      category: "economy",
      date: dateStr,
      value: 63.20,
      prev_value: 63.10,
      change_rate: 0.16,
    },
  ];
}

// ============================================
// Rendering
// ============================================

function renderActiveTab() {
  const tab = state.activeTab;
  switch (tab) {
    case "dashboard":
      renderDashboard();
      break;
    case "exchange":
      renderCategoryPage("exchange", "환율 정보", "주요 통화별 원화 환율 현황");
      break;
    case "interest":
      renderCategoryPage("interest", "금리 정보", "기준금리 및 시장금리 현황");
      break;
    case "stock":
      renderCategoryPage("stock", "주식 시장", "국내 주요 주가지수 현황");
      break;
    case "economy":
      renderCategoryPage("economy", "경제 지표", "국내 주요 거시경제 지표");
      break;
    case "news":
      renderNewsPage();
      break;
    default:
      renderDashboard();
  }
}

function renderDashboard() {
  const indicators = state.indicators;
  if (!indicators || indicators.length === 0) {
    mainContent.innerHTML = `
      <div class="empty-state">
        <p>아직 수집된 데이터가 없습니다.</p>
        <p>잠시 후 다시 시도해주세요.</p>
      </div>
    `;
    return;
  }

  // Top KPI cards
  const kpis = getKPIIndicators(indicators);

  // Categories
  const exchangeItems = indicators.filter((i) => i.category === "exchange").slice(0, 4);
  const stockItems = indicators.filter((i) => i.category === "stock").slice(0, 2);
  const interestItems = indicators.filter((i) => i.category === "interest").slice(0, 3);
  const economyItems = indicators.filter((i) => i.category === "economy").slice(0, 6);

  const html = `
    <!-- KPI Grid -->
    <div class="kpi-grid">
      ${kpis.map((kpi) => renderKPICard(kpi)).join("")}
    </div>

    <!-- Exchange Rate Section -->
    ${exchangeItems.length > 0 ? `
      <div class="section-card">
        <div class="section-card-header">
          <h3>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.2"/><path d="M5 6.5H11M5 9.5H11" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M8 4V12" stroke="currentColor" stroke-width="1.2"/></svg>
            환율
          </h3>
          <button class="tab" data-tab="exchange" style="font-size:12px;background:none;border:none;color:var(--brand-primary);font-weight:600;padding:4px 8px;">더보기 →</button>
        </div>
        <div class="section-card-body">
          <div class="indicator-list">
            ${exchangeItems.map((item) => renderIndicatorItem(item)).join("")}
          </div>
        </div>
      </div>
    ` : ""}

    <!-- Stock & Interest Row -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
      ${stockItems.length > 0 ? `
        <div class="section-card" style="margin-bottom:0;">
          <div class="section-card-header">
            <h3>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1" y="9" width="4" height="6" rx="0.8" fill="currentColor" fill-opacity="0.3" stroke="currentColor" stroke-width="1"/><rect x="6" y="5" width="4" height="10" rx="0.8" fill="currentColor" fill-opacity="0.3" stroke="currentColor" stroke-width="1"/><rect x="11" y="1" width="4" height="14" rx="0.8" fill="currentColor" fill-opacity="0.3" stroke="currentColor" stroke-width="1"/></svg>
              주식
            </h3>
            <button class="tab" data-tab="stock" style="font-size:12px;background:none;border:none;color:var(--brand-primary);font-weight:600;padding:4px 8px;">더보기 →</button>
          </div>
          <div class="section-card-body">
            <div class="indicator-list">
              ${stockItems.map((item) => renderIndicatorItem(item)).join("")}
            </div>
          </div>
        </div>
      ` : ""}

      ${interestItems.length > 0 ? `
        <div class="section-card" style="margin-bottom:0;">
          <div class="section-card-header">
            <h3>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 14L4 10L7 11L9 7L12 8L14 4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="2" cy="14" r="1.5" fill="currentColor" fill-opacity="0.3"/></svg>
              금리
            </h3>
            <button class="tab" data-tab="interest" style="font-size:12px;background:none;border:none;color:var(--brand-primary);font-weight:600;padding:4px 8px;">더보기 →</button>
          </div>
          <div class="section-card-body">
            <div class="indicator-list">
              ${interestItems.map((item) => renderIndicatorItem(item)).join("")}
            </div>
          </div>
        </div>
      ` : ""}
    </div>

    <!-- Economy Section -->
    ${economyItems.length > 0 ? `
      <div class="section-card">
        <div class="section-card-header">
          <h3>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 14H14" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M4 12V8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M7 12V6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M10 12V4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M13 12V2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
            거시경제 지표
          </h3>
          <button class="tab" data-tab="economy" style="font-size:12px;background:none;border:none;color:var(--brand-primary);font-weight:600;padding:4px 8px;">더보기 →</button>
        </div>
        <div class="section-card-body">
          <div class="indicator-list">
            ${economyItems.map((item) => renderIndicatorItem(item)).join("")}
          </div>
        </div>
      </div>
    ` : ""}
  `;

  mainContent.innerHTML = html;

  // Wire up "more" buttons
  mainContent.querySelectorAll('[data-tab]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      setActiveTab(tab);
    });
  });
}

function renderCategoryPage(category, title, description) {
  const items = state.indicators.filter((i) => i.category === category);

  const html = `
    <div class="section-header">
      <div>
        <h2>${title}</h2>
        <p style="font-size:13px;color:var(--text-secondary);margin-top:2px;">${description}</p>
      </div>
      <span class="badge">${items.length}개 지표</span>
    </div>

    ${items.length > 0 ? `
      <div class="section-card">
        <div class="section-card-body" style="padding:0;">
          <div class="indicator-list" style="gap:0;">
            ${items.map((item, idx) => `
              <div style="padding:14px 20px;${idx < items.length - 1 ? 'border-bottom:1px solid var(--border-color);' : ''}">
                ${renderIndicatorItem(item)}
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    ` : `
      <div class="empty-state">
        <p>해당 카테고리의 데이터가 없습니다.</p>
      </div>
    `}
  `;

  mainContent.innerHTML = html;
}

function renderNewsPage() {
  const items = state.news;

  const html = `
    <div class="section-header">
      <div>
        <h2>📰 뉴스</h2>
        <p style="font-size:13px;color:var(--text-secondary);margin-top:2px;">KAI 관련 주요 뉴스 및 기사</p>
      </div>
      <span class="badge">${items.length}건</span>
    </div>

    ${items.length > 0 ? `
      <div class="news-list">
        ${items.map((item) => `
          <div class="news-item">
            <div class="news-source">${escapeHtml(item.source || "KAI")}</div>
            <div class="news-title">${escapeHtml(item.title || "제목 없음")}</div>
            ${item.summary ? `<div class="news-summary">${escapeHtml(item.summary)}</div>` : ""}
            <div class="news-meta">
              <span>${item.date || ""}</span>
              ${item.url ? `<a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">원문 보기 →</a>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    ` : `
      <div class="empty-state">
        <p>아직 뉴스 데이터가 없습니다.</p>
        <p>RSS 수집을 실행해주세요.</p>
      </div>
    `}
  `;

  mainContent.innerHTML = html;
}

// ============================================
// Component Renderers
// ============================================

function renderKPICard(kpi) {
  const value = kpi.value;
  const change = kpi.change_rate;
  const changeClass = change > 0 ? "up" : change < 0 ? "down" : "neutral";
  const changeArrow = change > 0 ? "▲" : change < 0 ? "▼" : "―";
  const displayValue = value != null
    ? formatValue(value, kpi.indicator_code)
    : "—";
  const displayChange = change != null
    ? `${changeArrow} ${Math.abs(change).toFixed(2)}%`
    : "";
  const name = kpi.name_kr || getIndicatorName(kpi.indicator_code);

  return `
    <div class="kpi-card">
      <div class="kpi-label">${escapeHtml(name)}</div>
      <div class="kpi-value">${displayValue}</div>
      ${displayChange ? `<div class="kpi-change ${changeClass}">${displayChange}</div>` : ""}
      <div class="kpi-date">${kpi.date || ""}</div>
    </div>
  `;
}

function renderIndicatorItem(item) {
  const value = item.value;
  const change = item.change_rate;
  const changeClass = change > 0 ? "up" : change < 0 ? "down" : "neutral";
  const changeArrow = change > 0 ? "▲" : change < 0 ? "▼" : "―";
  const displayValue = value != null
    ? formatValue(value, item.indicator_code)
    : "—";
  const displayChange = change != null
    ? `${changeArrow} ${Math.abs(change).toFixed(2)}%`
    : "—";
  const name = item.name_kr || getIndicatorName(item.indicator_code);

  return `
    <div class="indicator-item">
      <div class="info">
        <div class="name">${escapeHtml(name)}</div>
        <div class="meta">${item.date || ""}</div>
      </div>
      <div class="numbers">
        <div class="value">${displayValue}</div>
        <div class="change ${changeClass}">${displayChange}</div>
      </div>
    </div>
  `;
}

// ============================================
// Helpers
// ============================================

function formatValue(value, code) {
  if (value == null) return "—";

  // 금리는 소수점 2자리
  if (["BASE_RATE", "BOND_YIELD_3Y", "BOND_YIELD_10Y", "CALL_RATE", "CPI_CHANGE", "CORE_CPI", "UNEMPLOYMENT_RATE", "EMPLOYMENT_RATE", "GDP_GROWTH"].includes(code)) {
    return value.toFixed(2) + (code.includes("RATE") && code !== "EXCHANGE" ? "%" : "");
  }

  // 외환보유액, 수출입
  if (["FOREIGN_RESERVE", "EXPORT_AMOUNT", "IMPORT_AMOUNT", "TRADE_BALANCE"].includes(code)) {
    if (value >= 1000) return (value / 1000).toFixed(1) + "조";
    return value.toFixed(0);
  }

  // 환율
  if (code.includes("EXCHANGE")) {
    return value.toFixed(2) + "원";
  }

  // 주가지수
  if (["KOSPI", "KOSDAQ"].includes(code)) {
    return value.toFixed(2);
  }

  return value.toLocaleString();
}

function getIndicatorName(code) {
  const names = {
    EXCHANGE_USD: "원/달러 환율",
    EXCHANGE_JPY: "원/100엔 환율",
    EXCHANGE_CNY: "원/위안 환율",
    EXCHANGE_EUR: "원/유로 환율",
    KOSPI: "코스피",
    KOSDAQ: "코스닥",
    BASE_RATE: "기준금리",
    BOND_YIELD_3Y: "국고채 3년",
    BOND_YIELD_10Y: "국고채 10년",
    CALL_RATE: "콜금리",
    CPI_CHANGE: "소비자물가",
    CORE_CPI: "근원물가",
    GDP_GROWTH: "GDP 성장률",
    UNEMPLOYMENT_RATE: "실업률",
    EMPLOYMENT_RATE: "고용률",
    FOREIGN_RESERVE: "외환보유액",
    EXPORT_AMOUNT: "수출액",
    IMPORT_AMOUNT: "수입액",
    TRADE_BALANCE: "무역수지",
  };
  return names[code] || code;
}

function getKPIIndicators(indicators) {
  // Return the most important indicators for the dashboard header
  const priority = ["EXCHANGE_USD", "KOSPI", "BASE_RATE", "CPI_CHANGE"];
  const result = [];
  for (const code of priority) {
    const found = indicators.find((i) => i.indicator_code === code);
    if (found) result.push(found);
  }
  // Fill up to 4 with whatever else
  if (result.length < 4) {
    for (const ind of indicators) {
      if (!result.find((r) => r.indicator_code === ind.indicator_code)) {
        result.push(ind);
        if (result.length >= 4) break;
      }
    }
  }
  return result.slice(0, 4);
}

function escapeHtml(text) {
  if (text == null) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(text) {
  return escapeHtml(text).replace(/'/g, "&#39;");
}

function updateLastUpdated() {
  if (state.lastFetchTime) {
    const now = new Date();
    const diff = Math.floor((now - state.lastFetchTime) / 1000);
    if (diff < 60) {
      lastUpdated.textContent = "방금 전 업데이트";
    } else if (diff < 3600) {
      lastUpdated.textContent = `${Math.floor(diff / 60)}분 전 업데이트`;
    } else {
      lastUpdated.textContent = state.lastFetchTime.toLocaleString("ko-KR", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
  }
}

function showLoading(show) {
  if (!loadingScreen) return;
  loadingScreen.style.display = show ? "flex" : "none";
}

function showError(msg) {
  mainContent.innerHTML = `
    <div class="notice-banner error">
      <span>⚠️</span>
      <span>${escapeHtml(msg)}</span>
    </div>
    ${mainContent.innerHTML || ""}
  `;
}

function showToast(msg) {
  const existing = document.querySelector(".alert-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.className = "alert-toast";
  toast.textContent = msg;
  document.body.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  });
}

// ============================================
// Tab Navigation
// ============================================

function setActiveTab(tabId) {
  state.activeTab = tabId;
  $$(".tab").forEach((t) => t.classList.remove("active"));
  const activeTab = document.querySelector(`.tab[data-tab="${tabId}"]`);
  if (activeTab) activeTab.classList.add("active");
  renderActiveTab();
}

// ============================================
// Auto Refresh Timer
// ============================================

let refreshInterval = null;

function startAutoRefresh() {
  // Refresh every 5 minutes
  refreshInterval = setInterval(() => {
    if (!state.loading) {
      fetchAllData();
    }
  }, 5 * 60 * 1000);
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
}

// ============================================
// Initialization
// ============================================

async function init() {
  // Tab click handlers
  navTabs.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    const tabId = tab.dataset.tab;
    if (!tabId) return;
    setActiveTab(tabId);
  });

  // Refresh button
  refreshBtn.addEventListener("click", () => {
    refreshBtn.classList.add("spinning");
    fetchAllData().finally(() => {
      setTimeout(() => refreshBtn.classList.remove("spinning"), 500);
    });
  });

  // Initial data load
  await fetchAllData();

  // Auto refresh
  startAutoRefresh();

  // Update "last updated" periodically
  setInterval(updateLastUpdated, 30 * 1000);
}

// Start
document.addEventListener("DOMContentLoaded", init);
