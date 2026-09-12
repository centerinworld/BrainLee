const state = {
  apiBase: "http://127.0.0.1:8011",
  role: "admin",
  pages: [],
  activePage: null,
  renderToken: 0,
  notice: "",
  noticeType: "info",
  lastSyncResult: null,
};

const pageMeta = {
  page1: { title: "CEO 일정", description: "CEO 캘린더와 주요 미팅을 관리합니다." },
  page2: { title: "회사 주요 일정", description: "회사 일정과 수정 요청을 관리합니다." },
  page3: { title: "KAI 주요 기사", description: "KAI 관련 RSS와 기사 큐를 관리합니다." },
  page4: { title: "경쟁사 동향", description: "경쟁사 RSS와 기사 큐를 관리합니다." },
  page5: { title: "캘린더 설정", description: "Google Calendar 연결과 쓰기 권한을 설정합니다." },
  page6: { title: "OpenAI 설정", description: "기사 요약용 API Key와 모델을 설정합니다." },
};

const roleSelect = document.querySelector("#roleSelect");
const apiBaseInput = document.querySelector("#apiBase");
const pageNav = document.querySelector("#pageNav");
const pageTitle = document.querySelector("#pageTitle");
const pageDescription = document.querySelector("#pageDescription");
const app = document.querySelector("#app");
const notice = document.querySelector("#notice");
const reloadButton = document.querySelector("#reloadButton");

async function api(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Request failed." }));
    throw new Error(payload.detail || "Request failed.");
  }
  return response.json();
}

async function apiOptional(path, fallback) {
  try {
    return await api(path);
  } catch {
    return fallback;
  }
}

function setNotice(message, type = "info") {
  state.notice = message;
  state.noticeType = type;
  notice.innerHTML = message ? `<div class="notice ${type === "error" ? "error" : ""}">${escapeHtml(message)}</div>` : "";
}

function normalizePages(pages) {
  return pages.map((page) => ({
    ...page,
    title: pageMeta[page.id]?.title || page.title,
    description: pageMeta[page.id]?.description || page.description || "",
  }));
}

async function init() {
  roleSelect.value = state.role;
  apiBaseInput.value = state.apiBase;

  apiBaseInput.addEventListener("change", async (event) => {
    state.apiBase = event.target.value.trim();
    await loadPages();
  });

  reloadButton.addEventListener("click", async () => {
    await renderActivePage();
  });

  await loadPages();
}

async function loadPages() {
  try {
    setNotice("");
    const pages = await api(`/pages?role=${state.role}`);
    state.pages = normalizePages(pages);
    state.activePage = state.pages.find((page) => page.id === state.activePage)?.id ?? state.pages[0]?.id ?? null;
    renderNavigation();
    await renderActivePage();
  } catch (error) {
    setNotice(`Backend connection failed: ${error.message}`, "error");
    pageTitle.textContent = "Backend Needed";
    pageDescription.textContent = "백엔드 서버를 실행한 뒤 다시 불러와 주세요.";
    app.innerHTML = "";
  }
}

function renderNavigation() {
  pageNav.innerHTML = state.pages
    .map((page) => `<button class="${page.id === state.activePage ? "active" : ""}" data-page-id="${page.id}">${escapeHtml(page.title)}</button>`)
    .join("");

  pageNav.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      state.activePage = button.dataset.pageId;
      renderNavigation();
      await renderActivePage();
    });
  });
}

async function renderActivePage() {
  const page = state.pages.find((entry) => entry.id === state.activePage);
  if (!page) {
    app.innerHTML = "";
    return;
  }

  const token = ++state.renderToken;
  pageTitle.textContent = page.title;
  pageDescription.textContent = page.description;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="item">
          <strong>${escapeHtml(page.title)} 로딩 중</strong>
          <p>최신 관리자 데이터를 불러오고 있습니다.</p>
        </div>
      </article>
    </section>
  `;

  try {
    if (page.id === "page1" || page.id === "page2") {
      await renderCalendarPage(page.id, token);
      return;
    }
    if (page.id === "page3") {
      await renderRssFeedPage("company", "KAI 주요 기사", "KAI RSS Workspace", token);
      return;
    }
    if (page.id === "page4") {
      await renderRssFeedPage("competitor", "경쟁사 동향", "Competitor RSS Workspace", token);
      return;
    }
    if (page.id === "page5") {
      await renderCalendarSettingsPage(token);
      return;
    }
    await renderOpenAiSettingsPage(token);
  } catch (error) {
    if (token !== state.renderToken) return;
    const message = error instanceof Error ? error.message : "Unknown error";
    app.innerHTML = `
      <section class="grid">
        <article class="card">
          <div class="item">
            <strong>페이지 로딩 실패</strong>
            <p>${escapeHtml(message)}</p>
          </div>
        </article>
      </section>
    `;
    setNotice(`Page load failed: ${message}`, "error");
  }
}

async function renderCalendarPage(pageId, token) {
  const [payload, settings, requests] = await Promise.all([
    api(`/calendar/${pageId}?role=${state.role}`),
    api(`/calendar-settings?role=${state.role}`),
    pageId === "page2" ? api(`/requests?role=${state.role}`) : Promise.resolve([]),
  ]);
  if (token !== state.renderToken) return;

  const integration = settings.find((item) => item.page_id === pageId);
  const isCompany = pageId === "page2";

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${escapeHtml(payload.source)}</p>
            <h3>${isCompany ? "회사 주요 일정 캘린더" : "CEO 캘린더"}</h3>
          </div>
          <span class="pill">${integration?.sync_enabled ? "Write Enabled" : "Local Write"}</span>
        </div>
        <table>
          <thead>
            <tr><th>Date</th><th>Time</th><th>Title</th><th>Place</th><th>Owner</th><th>Status</th></tr>
          </thead>
          <tbody>
            ${payload.events.map((event) => `<tr><td>${formatDateLabel(event.date)}</td><td>${escapeHtml(event.time)}</td><td>${escapeHtml(event.title)}</td><td>${escapeHtml(event.place)}</td><td>${escapeHtml(event.owner)}</td><td>${escapeHtml(event.status)}</td></tr>`).join("")}
          </tbody>
        </table>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${isCompany ? "Approval Flow" : "Calendar Write"}</p>
            <h3>${isCompany ? "수정 요청 처리" : "캘린더 일정 추가"}</h3>
          </div>
        </div>
        <div class="stack">
          ${isCompany ? renderRequestCards(requests) : ""}
          ${renderEventCreateForm(pageId, integration)}
          ${isCompany ? renderRequestCreateForm() : ""}
        </div>
      </article>
    </section>
  `;

  wireCalendarActions(pageId);
}

function renderRequestCards(requests) {
  if (!requests.length) {
    return `<div class="item"><strong>대기 중인 수정 요청이 없습니다.</strong></div>`;
  }
  return requests.map((request) => `
    <div class="item">
      <strong>${escapeHtml(request.title)}</strong>
      <div class="meta">
        <span>Requester: ${escapeHtml(request.requester)}</span>
        <span>Status: ${escapeHtml(request.status)}</span>
      </div>
      <p>${escapeHtml(request.reason)}</p>
      <div class="actions">
        <button class="button" data-approve="${request.id}">Approve</button>
        <button class="button outline" data-reject="${request.id}">Reject</button>
      </div>
    </div>
  `).join("");
}

function renderRequestCreateForm() {
  return `
    <div class="item">
      <strong>수정 요청 추가</strong>
      <div class="stack">
        <input id="requestTitle" placeholder="Request title">
        <input id="requester" placeholder="Requester">
        <input id="requestReason" placeholder="Reason">
        <button id="submitRequest" class="button">Submit</button>
      </div>
    </div>
  `;
}

function renderEventCreateForm(pageId, integration) {
  return `
    <div class="item">
      <strong>일정 추가</strong>
      <div class="meta">
        <span>Calendar ID: ${escapeHtml(integration?.calendar_id || "-")}</span>
        <span>Status: ${translateCalendarStatus(integration?.status || "not_connected")}</span>
      </div>
      <div class="stack" data-event-form="${pageId}">
        <input data-field="date" type="date" value="2026-04-07">
        <input data-field="time" type="time" value="09:00">
        <input data-field="title" placeholder="Event title">
        <input data-field="place" placeholder="Place">
        <input data-field="owner" placeholder="Owner">
        <input data-field="status" placeholder="Status" value="Planned">
        <button class="button" data-create-event="${pageId}">Add Event</button>
      </div>
    </div>
  `;
}

function wireCalendarActions(pageId) {
  document.querySelectorAll("[data-approve]").forEach((button) => {
    button.addEventListener("click", async () => {
      const result = await api(`/requests/${button.dataset.approve}/approve?role=${state.role}`, { method: "POST" });
      setNotice(`${result.id} approved.`);
      await renderActivePage();
    });
  });

  document.querySelectorAll("[data-reject]").forEach((button) => {
    button.addEventListener("click", async () => {
      const result = await api(`/requests/${button.dataset.reject}/reject?role=${state.role}`, { method: "POST" });
      setNotice(`${result.id} rejected.`);
      await renderActivePage();
    });
  });

  document.querySelector("#submitRequest")?.addEventListener("click", async () => {
    const payload = {
      title: document.querySelector("#requestTitle").value.trim(),
      requester: document.querySelector("#requester").value.trim(),
      reason: document.querySelector("#requestReason").value.trim(),
    };
    await api(`/requests?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("Request created.");
    await renderActivePage();
  });

  document.querySelector(`[data-create-event="${pageId}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-event-form="${pageId}"]`);
    const payload = {
      date: form.querySelector('[data-field="date"]').value,
      time: form.querySelector('[data-field="time"]').value,
      title: form.querySelector('[data-field="title"]').value.trim(),
      place: form.querySelector('[data-field="place"]').value.trim(),
      owner: form.querySelector('[data-field="owner"]').value.trim(),
      status: form.querySelector('[data-field="status"]').value.trim(),
    };
    await api(`/calendar/${pageId}/events?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("Calendar event added.");
    await renderActivePage();
  });
}

async function renderRssFeedPage(feedType, title, workspaceLabel, token) {
  const [payload, sources, keywordConfigRaw, syncStatusRaw] = await Promise.all([
    api(`/feeds/${feedType}?role=${state.role}`),
    api(`/rss-sources/${feedType}?role=${state.role}`),
    apiOptional(`/rss-keywords/${feedType}?role=${state.role}`, {}),
    apiOptional(`/sync-status?role=${state.role}`, {}),
  ]);
  if (token !== state.renderToken) return;

  const keywordConfig = {
    mode: keywordConfigRaw.mode || "all",
    raw: keywordConfigRaw.raw || "",
    keywords: Array.isArray(keywordConfigRaw.keywords) ? keywordConfigRaw.keywords : [],
    exclude_raw: keywordConfigRaw.exclude_raw || "",
    exclude_keywords: Array.isArray(keywordConfigRaw.exclude_keywords) ? keywordConfigRaw.exclude_keywords : [],
  };
  const syncStatus = {
    window: syncStatusRaw.window || "07:00-17:00 hourly",
    last_run: syncStatusRaw.last_run || "",
  };

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
            <h3>${escapeHtml(title)} RSS 등록</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>기사 수집 모드</strong>
            <p>전체 기사 수집 또는 키워드 일치 기사만 수집 중 하나를 선택합니다.</p>
            <div class="stack" data-keyword-form="${feedType}">
              <div class="tab-row">
                <button class="tab-button ${keywordConfig.mode === "all" ? "active" : ""}" type="button" data-filter-mode="${feedType}:all">All Articles</button>
                <button class="tab-button ${keywordConfig.mode === "keywords" ? "active" : ""}" type="button" data-filter-mode="${feedType}:keywords">Keyword Match Only</button>
              </div>
              <textarea data-field="keywords" rows="3" placeholder="Include keywords">${escapeHtml(keywordConfig.raw)}</textarea>
              <textarea data-field="exclude_keywords" rows="3" placeholder="Exclude keywords">${escapeHtml(keywordConfig.exclude_raw)}</textarea>
              <div class="meta">
                <span>${keywordConfig.keywords.length} include</span>
                <span>${keywordConfig.exclude_keywords.length} exclude</span>
              </div>
              <button class="button" data-save-keywords="${feedType}">Save Keywords</button>
            </div>
          </div>
          <div class="item">
            <strong>자동 파싱 상태</strong>
            <div class="meta">
              <span>Window: ${escapeHtml(syncStatus.window)}</span>
              <span>Last Run: ${escapeHtml(syncStatus.last_run || "not yet")}</span>
            </div>
          </div>
          <div class="item">
            <strong>RSS 주소 추가</strong>
            <div class="stack" data-rss-form="${feedType}">
              <input data-field="name" placeholder="Source name">
              <input data-field="url" placeholder="https://example.com/rss.xml">
              <button class="button" data-add-rss="${feedType}">Add RSS Source</button>
            </div>
          </div>
          ${renderSourceList(feedType, sources)}
        </div>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
            <h3>${escapeHtml(title)} 기사 큐</h3>
          </div>
          <button class="button" data-sync-rss="${feedType}">최신화</button>
        </div>
        <div class="stack">
          ${renderLastSyncResult(feedType)}
          ${renderQueueList(feedType, payload.queued || [])}
        </div>
      </article>
    </section>
  `;

  wireRssActions(feedType);
}

function renderSourceList(feedType, sources) {
  if (!sources.length) {
    return `<div class="item"><strong>등록된 RSS 주소가 없습니다.</strong></div>`;
  }
  return sources.map((source) => `
    <div class="item">
      <strong>${escapeHtml(source.name)}</strong>
      <div class="meta">
        <span>Type: ${escapeHtml(source.feed_type)}</span>
        <span>Status: ${source.active ? "Active" : "Inactive"}</span>
      </div>
      <a href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.url)}</a>
      <div class="stack" data-source-keyword-form="${source.id}">
        <textarea data-field="include_keywords" rows="2" placeholder="Source include keywords">${escapeHtml(source.include_keywords || "")}</textarea>
        <textarea data-field="exclude_keywords" rows="2" placeholder="Source exclude keywords">${escapeHtml(source.exclude_keywords || "")}</textarea>
        <div class="actions">
          <button class="button" data-save-source-keywords="${feedType}:${source.id}">Save Source Filter</button>
          <button class="button outline" data-delete-rss="${feedType}:${source.id}">Delete</button>
        </div>
      </div>
    </div>
  `).join("");
}

function renderQueueList(feedType, items) {
  if (!items.length) {
    return `<div class="item"><strong>대기 중인 기사가 없습니다.</strong></div>`;
  }
  return items.map((item) => `
    <div class="item">
      <strong>${escapeHtml(item.title)}</strong>
      <div class="meta">
        <span>${escapeHtml(item.source)}</span>
        <span>Selected: ${item.selected ? "Yes" : "No"}</span>
      </div>
      <p>${escapeHtml(item.summary || "")}</p>
      <div class="actions">
        <button class="button" data-publish="${feedType}:${item.id}">Publish</button>
        <button class="button outline" data-toggle="${feedType}:${item.id}">Toggle</button>
      </div>
    </div>
  `).join("");
}

function wireRssActions(feedType) {
  document.querySelector(`[data-add-rss="${feedType}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-rss-form="${feedType}"]`);
    const payload = {
      name: form.querySelector('[data-field="name"]').value.trim(),
      url: form.querySelector('[data-field="url"]').value.trim(),
    };
    await api(`/rss-sources/${feedType}?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("RSS source added.");
    await renderActivePage();
  });

  document.querySelectorAll(`[data-filter-mode^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      const form = document.querySelector(`[data-keyword-form="${feedType}"]`);
      form.querySelectorAll(".tab-button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
    });
  });

  document.querySelector(`[data-save-keywords="${feedType}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-keyword-form="${feedType}"]`);
    const payload = {
      keywords: form.querySelector('[data-field="keywords"]').value,
      exclude_keywords: form.querySelector('[data-field="exclude_keywords"]').value,
      mode: form.querySelector(".tab-button.active")?.dataset.filterMode?.split(":")[1] || "all",
    };
    await api(`/rss-keywords/${feedType}?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("RSS keywords saved.");
    await renderActivePage();
  });

  document.querySelector(`[data-sync-rss="${feedType}"]`)?.addEventListener("click", async () => {
    state.lastSyncResult = await api(`/rss-sync?role=${state.role}`, { method: "POST" });
    setNotice(`RSS sync complete: ${state.lastSyncResult.total_imported} items.`);
    await renderActivePage();
  });

  document.querySelectorAll("[data-delete-rss]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, sourceId] = button.dataset.deleteRss.split(":");
      await api(`/rss-sources/${kind}/${sourceId}?role=${state.role}`, { method: "DELETE" });
      setNotice("RSS source deleted.");
      await renderActivePage();
    });
  });

  document.querySelectorAll("[data-save-source-keywords]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, sourceId] = button.dataset.saveSourceKeywords.split(":");
      const form = document.querySelector(`[data-source-keyword-form="${sourceId}"]`);
      const payload = {
        include_keywords: form.querySelector('[data-field="include_keywords"]').value,
        exclude_keywords: form.querySelector('[data-field="exclude_keywords"]').value,
      };
      await api(`/rss-sources/${kind}/${sourceId}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setNotice("Source filter saved.");
      await renderActivePage();
    });
  });

  document.querySelectorAll("[data-publish]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, itemId] = button.dataset.publish.split(":");
      const result = await api(`/feeds/${kind}/publish/${itemId}?role=${state.role}`, { method: "POST" });
      setNotice(result.message);
      await renderActivePage();
    });
  });

  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, itemId] = button.dataset.toggle.split(":");
      const result = await api(`/feeds/${kind}/queue/${itemId}/toggle?role=${state.role}`, { method: "POST" });
      setNotice(result.message);
      await renderActivePage();
    });
  });
}

function renderLastSyncResult(feedType) {
  if (!state.lastSyncResult?.results?.length) return "";
  const rows = state.lastSyncResult.results.filter((item) => item.feed_type === feedType);
  if (!rows.length) return "";
  return `
    <div class="item">
      <strong>Latest Sync Result</strong>
      <div class="stack">
        ${rows.map((item) => `<div class="meta"><span>${escapeHtml(item.source_name)}</span><span>${item.item_count} imported</span></div>`).join("")}
      </div>
    </div>
  `;
}

async function renderCalendarSettingsPage(token) {
  const settings = await api(`/calendar-settings?role=${state.role}`);
  if (token !== state.renderToken) return;

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Google Calendar</p>
            <h3>캘린더 연결 설정</h3>
          </div>
        </div>
        <div class="stack">
          ${settings.map((item) => `
            <form class="item stack" data-calendar-form="${item.page_id}">
              <div class="card-subheader">
                <strong>${item.page_id === "page1" ? "CEO 캘린더" : "회사 캘린더"}</strong>
                <span class="pill">${translateCalendarStatus(item.status)}</span>
              </div>
              <label class="field"><span>Google Account Email</span><input name="account_email" value="${escapeAttr(item.account_email)}"></label>
              <label class="field"><span>Calendar Name</span><input name="calendar_name" value="${escapeAttr(item.calendar_name)}"></label>
              <label class="field"><span>Calendar ID</span><input name="calendar_id" value="${escapeAttr(item.calendar_id)}"></label>
              <label class="field">
                <span>Status</span>
                <select name="status">
                  ${["not_connected", "ready", "connected"].map((status) => `<option value="${status}" ${item.status === status ? "selected" : ""}>${translateCalendarStatus(status)}</option>`).join("")}
                </select>
              </label>
              <label class="toggle">
                <input type="checkbox" name="sync_enabled" ${item.sync_enabled ? "checked" : ""}>
                <span>Enable write sync</span>
              </label>
              <button class="button" type="submit">Save Calendar Settings</button>
            </form>
          `).join("")}
        </div>
      </article>
      <article class="card">
        <div class="item">
          <strong>안내</strong>
          <p>현재 콘솔에서는 캘린더 연결 정보와 쓰기 동기화 여부를 저장할 수 있습니다.</p>
        </div>
      </article>
    </section>
  `;

  document.querySelectorAll("[data-calendar-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const pageId = form.dataset.calendarForm;
      const payload = {
        provider: "google",
        account_email: form.account_email.value.trim(),
        calendar_name: form.calendar_name.value.trim(),
        calendar_id: form.calendar_id.value.trim(),
        status: form.status.value,
        sync_enabled: form.sync_enabled.checked,
        last_synced_at: "",
      };
      await api(`/calendar-settings/${pageId}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setNotice("Calendar settings saved.");
      await renderActivePage();
    });
  });
}

async function renderOpenAiSettingsPage(token) {
  const settings = await api(`/openai-settings?role=${state.role}`);
  if (token !== state.renderToken) return;

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">OpenAI Mini</p>
            <h3>기사 요약 설정</h3>
          </div>
        </div>
        <form id="openAiForm" class="stack">
          <label class="field"><span>API Key</span><input id="openaiApiKey" type="password" placeholder="${settings.has_api_key ? settings.api_key_masked || "saved" : "sk-..."}"></label>
          <label class="field"><span>Model</span><input id="openaiModel" value="${escapeAttr(settings.model || "gpt-5.4-mini")}"></label>
          <button class="button" type="submit">Save OpenAI Settings</button>
        </form>
      </article>
      <article class="card">
        <div class="item">
          <strong>동작 방식</strong>
          <p>API Key가 없으면 RSS 제목과 기본 설명만 저장하고, Key가 있으면 OpenAI로 요약문을 생성합니다.</p>
        </div>
      </article>
    </section>
  `;

  document.querySelector("#openAiForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      api_key: document.querySelector("#openaiApiKey").value.trim(),
      model: document.querySelector("#openaiModel").value.trim(),
    };
    await api(`/openai-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("OpenAI settings saved.");
    await renderActivePage();
  });
}

function translateCalendarStatus(status) {
  if (status === "connected") return "Connected";
  if (status === "ready") return "Ready";
  return "Not Connected";
}

function formatDateLabel(dateText) {
  if (!dateText) return "";
  const date = new Date(`${dateText}T00:00:00`);
  if (Number.isNaN(date.getTime())) return escapeHtml(dateText);
  const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${dateText} (${weekdays[date.getDay()]})`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

init();
