const qs = (selector) => document.querySelector(selector);

const API_BASE = "/hs";

async function api(url, options = {}) {
  url = API_BASE + url;
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.detail || data.message || "request failed");
  return data;
}

function formToObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function numberFmt(value) {
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(value || 0);
}

function percentFmt(value) {
  return value === null || value === undefined ? "-" : `${value.toFixed(2)}%`;
}

function statusLabel(status) {
  if (status === "exact") return "정확 일치";
  if (status === "composite") return "복수코드 검증";
  return "추정";
}

function renderLineChart(points) {
  const wrap = qs("#chart");
  if (!points.length) {
    wrap.innerHTML = `<div class="empty">매핑된 HS 코드가 아직 없거나 적재된 데이터가 없습니다.</div>`;
    return;
  }
  const width = 980;
  const height = 320;
  const padding = 30;
  const values = points.flatMap((point) => [point.export_value, point.import_value, point.trade_balance]);
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const scaleX = (index) => padding + (index * (width - padding * 2)) / Math.max(points.length - 1, 1);
  const scaleY = (value) => height - padding - ((value - min) / Math.max(max - min, 1)) * (height - padding * 2);
  const line = (key) =>
    points.map((point, index) => `${scaleX(index)},${scaleY(point[key])}`).join(" ");

  wrap.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" class="chart-svg">
      <polyline class="axis-line" points="${padding},${height - padding} ${width - padding},${height - padding}" />
      <polyline class="axis-line" points="${padding},${padding} ${padding},${height - padding}" />
      <polyline class="line export" points="${line("export_value")}" />
      <polyline class="line import" points="${line("import_value")}" />
      <polyline class="line balance" points="${line("trade_balance")}" />
      ${points
        .map(
          (point, index) => `
            <text x="${scaleX(index)}" y="${height - 8}" class="tick">${point.period_ym.slice(2)}</text>
          `
        )
        .filter((_, index) => index % Math.ceil(points.length / 8) === 0)
        .join("")}
    </svg>
    <div class="legend">
      <span><i class="legend-dot export"></i>수출</span>
      <span><i class="legend-dot import"></i>수입</span>
      <span><i class="legend-dot balance"></i>무역수지</span>
    </div>
  `;
}

async function loadStatus() {
  const data = await api("/api/customs/status");
  qs("#status-grid").innerHTML = Object.entries(data.detail.counts)
    .map(
      ([key, value]) => `
        <div class="stat">
          <strong>${key}</strong>
          <span>${numberFmt(value)} rows</span>
        </div>
      `
    )
    .join("") + `<div class="stat"><strong>latest</strong><span>${data.detail.latest_period || "-"}</span></div>`;
}

async function loadSectors() {
  const sectors = await api("/api/sectors");
  qs("#sector-tabs").innerHTML = sectors
    .map(
      (sector, index) => `
        <button class="tab ${index === 0 ? "active" : ""}" data-sector="${sector.sector_key}" data-description="${sector.description}">
          ${sector.label}
        </button>
      `
    )
    .join("");
  qs("#sector-map-select").innerHTML = sectors
    .map((sector) => `<option value="${sector.sector_key}">${sector.label}</option>`)
    .join("");
  const active = qs(".tab.active");
  if (active) qs("#sector-description").textContent = active.dataset.description;
}

function currentSectorKey() {
  return qs(".tab.active")?.dataset.sector || "";
}

async function loadSectorDashboard() {
  const sectorKey = currentSectorKey();
  if (!sectorKey) return;
  const data = await api(`/api/dashboard/sectors/${sectorKey}`);
  qs("#chart-title").textContent = `${data.label} 트렌드`;
  qs("#sector-description").textContent = data.description;
  renderLineChart(data.points);
  qs("#top-hs-list").innerHTML = data.top_hs_codes.length
    ? data.top_hs_codes
        .map(
          (row) => `
            <div class="list-item">
              <strong>${row.display_name}</strong>
              <span>${row.hs_code}</span>
              <div class="badge-row"><span class="badge ${row.mapping_status || "provisional"}">${statusLabel(row.mapping_status)}</span></div>
              <span>수출 ${numberFmt(row.export_value)} / 수입 ${numberFmt(row.import_value)}</span>
            </div>
          `
        )
        .join("")
    : `<div class="empty">아직 섹터 HS 매핑이 없습니다.</div>`;
}

async function loadFeatures() {
  const scope = qs("#feature-scope").value;
  const data = await api(`/api/dashboard/features?scope=${scope}&limit=20`);
  qs("#feature-list").innerHTML = data.length
    ? data
        .map(
          (row) => `
            <div class="list-item">
              <strong>${row.name}</strong>
              <span>${row.item_type} · ${row.key}</span>
              <span>YoY ${percentFmt(row.export_yoy)} · Score ${row.feature_score}</span>
            </div>
          `
        )
        .join("")
    : `<div class="empty">표시할 특징 항목이 없습니다.</div>`;
}

async function loadSectorMappings() {
  const rows = await api("/api/sector-mappings");
  qs("#sector-map-list").innerHTML = rows.length
    ? `<table><thead><tr><th>섹터</th><th>HS</th><th>표시 이름</th><th>상태</th><th></th></tr></thead><tbody>${rows
        .map(
          (row) => `
            <tr>
              <td>${row.sector_key}</td>
              <td>${row.hs_code}</td>
              <td>${row.display_name || row.hs_name || "-"}</td>
              <td><span class="badge ${row.mapping_status || "provisional"}">${statusLabel(row.mapping_status)}</span></td>
              <td><button data-del-sector="${row.id}">삭제</button></td>
            </tr>
          `
        )
        .join("")}</tbody></table>`
    : `<div class="empty">아직 등록된 섹터 매핑이 없습니다.</div>`;
}

async function loadCompanyMappings() {
  const rows = await api("/api/mappings");
  qs("#company-map-list").innerHTML = rows.length
    ? `<table><thead><tr><th>HS</th><th>기업</th><th>섹터</th><th>상태</th><th></th></tr></thead><tbody>${rows
        .map(
          (row) => `
            <tr>
              <td>${row.hs_code}</td>
              <td>${row.stock_name} (${row.stock_code})</td>
              <td>${row.sector_name || "-"}</td>
              <td><span class="badge ${row.mapping_status || "provisional"}">${statusLabel(row.mapping_status)}</span></td>
              <td><button data-del-company="${row.id}">삭제</button></td>
            </tr>
          `
        )
        .join("")}</tbody></table>`
    : `<div class="empty">아직 등록된 기업 매핑이 없습니다.</div>`;
}

async function searchCompanies(query) {
  const data = await api(`/api/reference/companies?query=${encodeURIComponent(query)}&limit=12`);
  qs("#company-results").innerHTML = data
    .map(
      (item) => `
        <button type="button" class="pick" data-code="${item.stock_code}" data-name="${item.stock_name}" data-sector="${item.sector_mid || item.sector_large}">
          <strong>${item.stock_name}</strong>
          <span>${item.stock_code} · ${item.market} · ${item.sector_mid || item.sector_large || "-"}</span>
        </button>
      `
    )
    .join("");
}

qs("#sector-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-sector]");
  if (!button) return;
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
  button.classList.add("active");
  qs("#sector-description").textContent = button.dataset.description || "";
});

qs("#load-sector").addEventListener("click", loadSectorDashboard);
qs("#refresh-status").addEventListener("click", loadStatus);
qs("#refresh-features").addEventListener("click", loadFeatures);

qs("#run-ingest").addEventListener("click", async () => {
  const data = await api("/api/customs/ingest", { method: "POST" });
  qs("#job-result").textContent = data.detail;
  await loadStatus();
});

qs("#run-refresh").addEventListener("click", async () => {
  const data = await api("/api/customs/daily-refresh", { method: "POST" });
  qs("#job-result").textContent = data.detail;
  await loadStatus();
});

qs("#sector-map-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formToObject(event.currentTarget);
  await api("/api/sector-mappings", { method: "POST", body: JSON.stringify(payload) });
  event.currentTarget.reset();
  await loadSectorMappings();
  await loadSectorDashboard();
});

qs("#company-map-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formToObject(event.currentTarget);
  payload.confidence = Number(payload.confidence || 0.5);
  await api("/api/mappings", { method: "POST", body: JSON.stringify(payload) });
  event.currentTarget.reset();
  await loadCompanyMappings();
  await loadFeatures();
});

qs("#company-search").addEventListener("input", async (event) => {
  const query = event.currentTarget.value.trim();
  if (!query) {
    qs("#company-results").innerHTML = "";
    return;
  }
  await searchCompanies(query);
});

qs("#company-results").addEventListener("click", (event) => {
  const button = event.target.closest("[data-code]");
  if (!button) return;
  qs('#company-map-form [name="stock_code"]').value = button.dataset.code;
  qs('#company-map-form [name="stock_name"]').value = button.dataset.name;
  qs('#company-map-form [name="sector_name"]').value = button.dataset.sector || "";
});

qs("#sector-map-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-del-sector]");
  if (!button) return;
  await api(`/api/sector-mappings/${button.dataset.delSector}`, { method: "DELETE" });
  await loadSectorMappings();
  await loadSectorDashboard();
});

qs("#company-map-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-del-company]");
  if (!button) return;
  await api(`/api/mappings/${button.dataset.delCompany}`, { method: "DELETE" });
  await loadCompanyMappings();
  await loadFeatures();
});

qs("#ai-sector-summary").addEventListener("click", async () => {
  const sectorKey = currentSectorKey();
  const data = await api(`/api/dashboard/ai-summary?scope_type=sector&scope_key=${encodeURIComponent(sectorKey)}`, {
    method: "POST",
  });
  qs("#ai-summary").textContent = data.summary_text;
});

await loadStatus();
await loadSectors();
await loadSectorMappings();
await loadCompanyMappings();
await loadFeatures();
await loadSectorDashboard();
