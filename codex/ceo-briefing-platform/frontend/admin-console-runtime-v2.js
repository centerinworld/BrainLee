const queryApiBase = new URLSearchParams(window.location.search).get("api");
const queryRole = (new URLSearchParams(window.location.search).get("role") || "").toLowerCase();
const initialRole = ["admin", "ceo", "staff"].includes(queryRole) ? queryRole : "admin";
const defaultApiBase = window.location.hostname === "newsinfo.cloud" || window.location.hostname === "www.newsinfo.cloud"
  ? "https://api.newsinfo.cloud"
  : "http://127.0.0.1:8011";

const state = {
  apiBase: queryApiBase && queryApiBase.trim() ? queryApiBase.trim() : defaultApiBase,
  role: initialRole,
  pages: [],
  activePage: null,
  renderToken: 0,
  notice: "",
  noticeType: "info",
  lastSyncResult: null,
  startDate: "",
  endDate: "",
  searchKeyword: "",
  calendarEditing: null,
};

const CATEGORY_ORDER = ["kai", "government", "space", "competitor", "reference", "draft", "trash"];
const CATEGORY_LABELS = {
  kai: "KAI기사",
  government: "정부기관",
  space: "항공/방산/우주",
  competitor: "경쟁사",
  hanwha: "경쟁사",
  lig: "경쟁사",
  reference: "협력사",
  draft: "작성 중",
  trash: "버림",
};

const pageMeta = {
  page1: { title: "CEO 일정", description: "CEO 일정과 주요 회의를 확인합니다." },
  page2: { title: "회사 주요 일정", description: "회사 일정과 승인 흐름을 관리합니다." },
  page3: { title: "주요 기사", description: "발행된 기사와 신규 기사 작업을 관리합니다." },
  page4: { title: "경쟁사 동향", description: "경쟁사 RSS 소스와 기사 대기열을 관리합니다." },
  page5: { title: "캘린더 설정", description: "Google 캘린더를 연결하고 쓰기 가능한 캘린더를 선택합니다." },
  page6: { title: "AI 설정", description: "RSS 요약과 기사 분류에 사용할 AI 공급자, API 키, 모델을 저장합니다." },
  page7: { title: "APP 사용자", description: "앱 사용자 계정 및 역할별 접근 권한을 관리합니다." },
  page8: { title: "언론사 추가", description: "RSS 소스와 Naver 뉴스 검색 설정을 관리합니다." },
  page9: { title: "텔레그램 전송", description: "수동으로 브리핑 기사를 텔레그램으로 전송하고 채널 설정을 관리합니다." },
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
  return pages
    .filter((page) => page.id !== "page3" && page.id !== "page4")
    .map((page) => ({
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

  if (state.role !== "admin") {
    const panel = document.querySelector(".panel");
    if (panel) panel.style.display = "none";
    
    const sidebarEyebrow = document.querySelector(".sidebar .eyebrow");
    if (sidebarEyebrow) sidebarEyebrow.textContent = "CEO Portal";
    
    const sidebarTitle = document.querySelector(".sidebar h1");
    if (sidebarTitle) sidebarTitle.textContent = "CEO Briefing";
    
    const sidebarCopy = document.querySelector(".sidebar .copy");
    if (sidebarCopy) sidebarCopy.textContent = "Browse the latest schedules and published articles.";
  }

  await loadPages();
}

let _retryTimer = null;

async function loadPages() {
  if (_retryTimer) { clearTimeout(_retryTimer); _retryTimer = null; }
  try {
    setNotice("");
    state.pages = normalizePages(await api(`/pages?role=${state.role}`));

    let defaultPage = state.pages[0]?.id;
    if (state.role === "ceo" || state.role === "staff") {
      const page3 = state.pages.find((p) => p.id === "page3");
      if (page3) {
        defaultPage = "page3";
      }
    }
    state.activePage = state.pages.find((page) => page.id === state.activePage)?.id ?? defaultPage ?? null;

    renderNavigation();
    await renderActivePage();
  } catch (error) {
    setNotice(`백엔드 연결 실패 — 5초 후 재시도... (${error.message})`, "error");
    pageTitle.textContent = "Backend Needed";
    pageDescription.textContent = "Start the backend server and reload this page.";
    app.innerHTML = "";
    _retryTimer = setTimeout(() => loadPages(), 5000);
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
  setNotice("");
  const token = ++state.renderToken;
  pageTitle.textContent = page.title;
  pageDescription.textContent = page.description;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="item">
          <strong>Loading ${escapeHtml(page.title)}</strong>
          <p>Preparing the latest admin data.</p>
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
      await renderRssPage("company", "주요 기사", "News Workspace", token);
      return;
    }
    if (page.id === "page4") {
      await renderRssPage("competitor", "경쟁사 동향", "Competitor RSS Workspace", token);
      return;
    }
    if (page.id === "page5") {
      await renderCalendarSettingsPage(token);
      return;
    }
    if (page.id === "page8") {
      await renderParsingSettingsPage(token);
      return;
    }
    if (page.id === "page7") {
      await renderAppUsersPage(token);
      return;
    }
    if (page.id === "page9") {
      await renderTelegramSendPage(token);
      return;
    }
    await renderOpenAiSettingsPage(token);
  } catch (error) {
    if (token !== state.renderToken) return;
    const message = error instanceof Error ? error.message : "Unknown error";
    setNotice(`Page load failed: ${message}`, "error");
    app.innerHTML = `
      <section class="grid">
        <article class="card">
          <div class="item">
            <strong>Page load failed</strong>
            <p>${escapeHtml(message)}</p>
          </div>
        </article>
      </section>
    `;
  }
}

async function renderCalendarPage(pageId, token) {
  const [payload, settings, requests] = await Promise.all([
    api(`/calendar/${pageId}?role=${state.role}`),
    apiOptional(`/calendar-settings?role=${state.role}`, []),
    pageId === "page2" ? apiOptional(`/requests?role=${state.role}`, []) : Promise.resolve([]),
  ]);
  if (token !== state.renderToken) return;

  const integration = settings.find((item) => item.page_id === pageId);
  const isCompany = pageId === "page2";
  const isAdmin = state.role === "admin";
  const editing = state.calendarEditing?.pageId === pageId ? state.calendarEditing.event : null;
  
  const rightPane = isAdmin ? `
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${isCompany ? "Approval Flow" : "Calendar Write"}</p>
            <h3>${editing ? "Edit Calendar Event" : isCompany ? "Update Requests" : "Add Calendar Event"}</h3>
          </div>
        </div>
        <div class="stack">
          ${isCompany ? renderRequestCards(requests) : ""}
          ${renderEventForm(pageId, integration, editing)}
          ${isCompany ? renderRequestCreateForm() : ""}
        </div>
      </article>
  ` : "";

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${escapeHtml(payload.source)}</p>
            <h3>${isCompany ? "Company Calendar" : "CEO Calendar"}</h3>
          </div>
          ${integration ? `<span class="pill">${integration.sync_enabled ? "Write Enabled" : "Local Write"}</span>` : ""}
        </div>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr><th>Date</th><th>Time</th><th>Title</th><th>Place</th><th>Owner</th><th>Status</th>${isAdmin ? "<th>Actions</th>" : ""}</tr>
            </thead>
            <tbody>
              ${payload.events.map((event) => `
                <tr>
                  <td>${formatDateLabel(event.date)}</td>
                  <td>${escapeHtml(event.time)}</td>
                  <td>${escapeHtml(event.title)}</td>
                  <td>${escapeHtml(event.place)}</td>
                  <td>${escapeHtml(event.owner)}</td>
                  <td>${escapeHtml(event.status)}</td>
                  ${isAdmin ? `
                    <td>
                      <div class="actions">
                        <button class="button outline" data-edit-event="${escapeAttr(event.id)}" data-event-date="${escapeAttr(event.date)}" data-event-time="${escapeAttr(event.time)}" data-event-title="${escapeAttr(event.title)}" data-event-place="${escapeAttr(event.place)}" data-event-owner="${escapeAttr(event.owner)}" data-event-status="${escapeAttr(event.status)}">Edit</button>
                        <button class="button outline" data-delete-event="${escapeAttr(event.id)}">Delete</button>
                      </div>
                    </td>
                  ` : ""}
                </tr>
              `).join("") || `<tr><td colspan='${isAdmin ? 7 : 6}'>No events</td></tr>`}
            </tbody>
          </table>
        </div>
      </article>
      ${rightPane}
    </section>
  `;
  wireCalendarActions(pageId);
}

function renderRequestCards(requests) {
  if (!requests.length) {
    return `<div class="item"><strong>No pending requests.</strong></div>`;
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
      <strong>Create Request</strong>
      <div class="stack">
        <input id="requestTitle" placeholder="Request title">
        <input id="requester" placeholder="Requester">
        <input id="requestReason" placeholder="Reason">
        <button id="submitRequest" class="button">Submit</button>
      </div>
    </div>
  `;
}

function renderEventForm(pageId, integration, editing = null) {
  return `
    <div class="item">
      <strong>${editing ? "Edit Event" : "Add Event"}</strong>
      <div class="meta">
        <span>Calendar ID: ${escapeHtml(integration?.calendar_id || "-")}</span>
        <span>Status: ${translateCalendarStatus(integration?.status || "not_connected")}</span>
      </div>
      <div class="stack" data-event-form="${pageId}">
        <input data-field="date" type="date" value="${escapeAttr(editing?.date || "2026-04-08")}">
        <input data-field="time" type="time" value="${escapeAttr(editing?.time || "09:00")}">
        <input data-field="title" placeholder="Event title" value="${escapeAttr(editing?.title || "")}">
        <input data-field="place" placeholder="Place" value="${escapeAttr(editing?.place || "")}">
        <input data-field="owner" placeholder="Owner" value="${escapeAttr(editing?.owner || "")}">
        <input data-field="status" placeholder="Status" value="${escapeAttr(editing?.status || "Planned")}">
        <div class="actions">
          <button class="button" data-save-event="${pageId}">${editing ? "Save Event" : "Add Event"}</button>
          ${editing ? `<button class="button outline" data-cancel-event-edit="${pageId}">Cancel</button>` : ""}
        </div>
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
    await api(`/requests?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.querySelector("#requestTitle").value.trim(),
        requester: document.querySelector("#requester").value.trim(),
        reason: document.querySelector("#requestReason").value.trim(),
      }),
    });
    setNotice("Request created.");
    await renderActivePage();
  });
  document.querySelectorAll("[data-edit-event]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.calendarEditing = {
        pageId,
        event: {
          id: button.dataset.editEvent,
          date: button.dataset.eventDate || "",
          time: button.dataset.eventTime || "",
          title: button.dataset.eventTitle || "",
          place: button.dataset.eventPlace || "",
          owner: button.dataset.eventOwner || "",
          status: button.dataset.eventStatus || "",
        },
      };
      await renderActivePage();
    });
  });
  document.querySelectorAll("[data-delete-event]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this calendar event?")) return;
      await api(`/calendar/${pageId}/events/${encodeURIComponent(button.dataset.deleteEvent)}?role=${state.role}`, {
        method: "DELETE",
      });
      if (state.calendarEditing?.event?.id === button.dataset.deleteEvent) {
        state.calendarEditing = null;
      }
      setNotice("Calendar event deleted.");
      await renderActivePage();
    });
  });
  document.querySelector(`[data-cancel-event-edit="${pageId}"]`)?.addEventListener("click", async () => {
    state.calendarEditing = null;
    await renderActivePage();
  });
  document.querySelector(`[data-save-event="${pageId}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-event-form="${pageId}"]`);
    const editingId = state.calendarEditing?.pageId === pageId ? state.calendarEditing.event.id : "";
    await api(editingId ? `/calendar/${pageId}/events/${encodeURIComponent(editingId)}?role=${state.role}` : `/calendar/${pageId}/events?role=${state.role}`, {
      method: editingId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        date: form.querySelector('[data-field="date"]').value,
        time: form.querySelector('[data-field="time"]').value,
        title: form.querySelector('[data-field="title"]').value.trim(),
        place: form.querySelector('[data-field="place"]').value.trim(),
        owner: form.querySelector('[data-field="owner"]').value.trim(),
        status: form.querySelector('[data-field="status"]').value.trim(),
      }),
    });
    setNotice(editingId ? "Calendar event updated." : "Calendar event added.");
    state.calendarEditing = null;
    await renderActivePage();
  });
}

async function renderRssPage(feedType, title, workspaceLabel, token) {
  const isAdmin = state.role === "admin";
  const payload = await api(`/feeds/${feedType}?role=${state.role}`);
  let sources = [];
  let keywordConfigRaw = {};
  let syncStatusRaw = {};
  if (isAdmin) {
    [sources, keywordConfigRaw, syncStatusRaw] = await Promise.all([
      api(`/rss-sources/${feedType}?role=${state.role}`),
      apiOptional(`/rss-keywords/${feedType}?role=${state.role}`, {}),
      apiOptional(`/sync-status?role=${state.role}`, {}),
    ]);
  }
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
  const sectionTitle = isAdmin ? `${escapeHtml(title)} Article Queue` : `${escapeHtml(title)} Latest Articles`;
  if (!isAdmin) {
    app.innerHTML = `
      <section class="grid">
        <article class="card">
          <div class="card-header">
            <div>
              <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
              <h3>${sectionTitle}</h3>
            </div>
          </div>
          <div class="stack">
            ${renderPublishedList(payload.published || [], false, feedType)}
          </div>
        </article>
      </section>
    `;
    wireCategoryTabs(feedType);
    return;
  }

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
            <h3>${escapeHtml(title)} RSS Registration</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>Filter Mode</strong>
            <div class="tab-row">
              <button class="tab-button ${keywordConfig.mode === "all" ? "active" : ""}" type="button" data-filter-mode="${feedType}:all">All Articles</button>
              <button class="tab-button ${keywordConfig.mode === "keywords" ? "active" : ""}" type="button" data-filter-mode="${feedType}:keywords">Keyword Match Only</button>
            </div>
            <textarea data-field="keywords" rows="3" placeholder="Include keywords" data-keyword-form="${feedType}">${escapeHtml(keywordConfig.raw)}</textarea>
            <textarea data-field="exclude_keywords" rows="3" placeholder="Exclude keywords" data-keyword-exclude-form="${feedType}">${escapeHtml(keywordConfig.exclude_raw)}</textarea>
            <div class="meta">
              <span>${keywordConfig.keywords.length} include</span>
              <span>${keywordConfig.exclude_keywords.length} exclude</span>
            </div>
            <button class="button" data-save-keywords="${feedType}">Save Keywords</button>
          </div>
          <div class="item">
            <strong>Auto Parsing Status</strong>
            <div class="meta">
              <span>Window: ${escapeHtml(syncStatus.window)}</span>
              <span>Last Run: ${escapeHtml(syncStatus.last_run || "not yet")}</span>
            </div>
          </div>
          <div class="item">
            <strong>Add RSS Source</strong>
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
            <h3>${sectionTitle}</h3>
          </div>
          <button class="button" data-sync-rss="${feedType}">최신화</button>
        </div>
        <div class="stack">
          ${renderLastSyncResult(feedType)}
          ${renderQueueList(feedType, payload.queued || [])}
          <div class="item">
            <strong>Published Articles</strong>
            <div class="stack">
              ${renderPublishedList(payload.published || [], true, feedType)}
            </div>
          </div>
        </div>
      </article>
    </section>
  `;
  wireCategoryTabs(feedType);
  wireRssActions(feedType);
  wireFeedItemActions();
  wireBulkActions();
}

function normalizeKeywordConfig(keywordConfigRaw = {}) {
  return {
    mode: keywordConfigRaw.mode || "all",
    raw: keywordConfigRaw.raw || "",
    keywords: Array.isArray(keywordConfigRaw.keywords) ? keywordConfigRaw.keywords : [],
    exclude_raw: keywordConfigRaw.exclude_raw || "",
    exclude_keywords: Array.isArray(keywordConfigRaw.exclude_keywords) ? keywordConfigRaw.exclude_keywords : [],
  };
}

function normalizeSyncStatus(syncStatusRaw = {}) {
  return {
    window: syncStatusRaw.window || "07:00-17:00 hourly",
    last_run: syncStatusRaw.last_run || "",
  };
}

function renderParsingSettingsCard(feedType, workspaceLabel, sources, keywordConfig, syncStatus) {
  return `
    <article class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
          <h3>수집 언론사 / 매체 관리</h3>
        </div>
      </div>
      <div class="stack">
        <div class="item">
          <strong>새 언론사 추가 (RSS)</strong>
          <div style="display: flex; gap: 8px; margin-top: 8px;" data-rss-form="${feedType}">
            <input data-field="name" placeholder="언론사명 (예: 정책브리핑)" style="flex: 1; padding: 8px; border-radius: 6px; border: 1px solid #ddd;">
            <input data-field="url" placeholder="RSS 주소 URL" style="flex: 2; padding: 8px; border-radius: 6px; border: 1px solid #ddd;">
            <button class="button" data-add-rss="${feedType}" style="padding: 8px 16px;">추가</button>
          </div>
        </div>
        
        <div class="item">
          <strong>등록된 언론사 목록</strong>
          <div class="stack" style="margin-top: 8px; gap: 8px;">
            ${renderSourceList(feedType, sources)}
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderQueueCard(feedType, title, workspaceLabel, payload) {
  return `
    <article class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">${escapeHtml(workspaceLabel)}</p>
          <h3>${escapeHtml(title)} Article Queue</h3>
        </div>
        <button class="button" data-sync-rss="${feedType}">최신화</button>
      </div>
      <div class="stack">
        ${renderLastSyncResult(feedType)}
        <div class="item">
          <strong>Queued Articles</strong>
          <div class="stack">
            ${renderQueueList(feedType, payload.queued || [])}
          </div>
        </div>
        <div class="item">
          <strong>Published Articles</strong>
          <div class="stack">
            ${renderPublishedList(payload.published || [], true, feedType)}
          </div>
        </div>
      </div>
    </article>
  `;
}

function sortArticlesLatest(items) {
  return [...items].sort((a, b) => {
    const aTs = Date.parse(a.article_published_at || "");
    const bTs = Date.parse(b.article_published_at || "");
    const aSafe = Number.isNaN(aTs) ? 0 : aTs;
    const bSafe = Number.isNaN(bTs) ? 0 : bTs;
    return bSafe - aSafe;
  });
}

async function renderNewsOperationsPage(token) {
  const [
    companyPayload,
    competitorPayload,
  ] = await Promise.all([
    api(`/feeds/company?role=${state.role}`),
    api(`/feeds/competitor?role=${state.role}`),
  ]);
  if (token !== state.renderToken) return;

  const published = sortArticlesLatest(
    [
      ...(companyPayload.published || []).map((item) => ({ ...item, feed_type: "company" })),
      ...(competitorPayload.published || []).map((item) => ({ ...item, feed_type: "competitor" })),
    ],
  );
  const queued = sortArticlesLatest(
    [
      ...(companyPayload.queued || []).map((item) => ({ ...item, feed_type: "company" })),
      ...(competitorPayload.queued || []).map((item) => ({ ...item, feed_type: "competitor" })),
    ],
  );

  const allDates = [...new Set([
    ...published.map(i => formatArticleDateKey(i.article_published_at)),
    ...queued.map(i => formatArticleDateKey(i.article_published_at))
  ])].sort((a, b) => {
    if (a === "날짜 미상") return 1;
    if (b === "날짜 미상") return -1;
    return b.localeCompare(a);
  });

  if (state.startDate === undefined) state.startDate = "";
  if (state.endDate === undefined) state.endDate = "";
  if (state.searchKeyword === undefined) state.searchKeyword = "";

  const getYYYYMMDD = (isoStr) => {
    if (!isoStr) return "";
    const match = isoStr.match(/^(\d{4})[./-]?(\d{2})[./-]?(\d{2})/);
    if (match) {
      return `${match[1]}-${match[2]}-${match[3]}`;
    }
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return "";
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  };

  const filterByDateRange = (i) => {
    if (!state.startDate && !state.endDate) return true;
    const dateStr = getYYYYMMDD(i.article_published_at || i.published_at);
    if (!dateStr) return false;
    if (state.startDate && dateStr < state.startDate) return false;
    if (state.endDate && dateStr > state.endDate) return false;
    return true;
  };

  const filterByKeyword = (i) => {
    if (!state.searchKeyword) return true;
    const kw = state.searchKeyword.toLowerCase().trim();
    const title = (i.title || "").toLowerCase();
    const summary = (i.summary || "").toLowerCase();
    return title.includes(kw) || summary.includes(kw);
  };

  const filteredPublished = published.filter(i => filterByDateRange(i) && filterByKeyword(i));
  const filteredQueued = queued.filter(i => filterByDateRange(i) && filterByKeyword(i));

  app.innerHTML = `
    <div style="margin-bottom: 24px; padding: 16px; background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); display: flex; flex-direction: column; gap: 12px;">
      <!-- Row 1: Date Range Filter -->
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <strong style="font-size: 14px; color: #555; width: 70px; flex-shrink: 0;">조회 기간:</strong>
        <input type="date" id="opsStartDate" value="${escapeAttr(state.startDate)}" style="width: 150px; padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none; font-size: 13px;">
        <span style="color: #555;">~</span>
        <input type="date" id="opsEndDate" value="${escapeAttr(state.endDate)}" style="width: 150px; padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none; font-size: 13px;">
        <button id="opsDateSearchBtn" class="button outline" style="padding: 6px 16px; font-size: 13px; border-color: #62717f; color: #62717f;">조회</button>
        <button id="opsClearBtn" class="button outline" style="padding: 6px 16px; font-size: 13px;">초기화</button>
      </div>
      
      <!-- Row 2: Keyword Search Filter -->
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: nowrap; width: 100%;">
        <strong style="font-size: 14px; color: #555; width: 70px; flex-shrink: 0;">검색어:</strong>
        <input type="text" id="opsSearchKeyword" value="${escapeAttr(state.searchKeyword)}" placeholder="검색어를 입력하세요 (제목, 본문/요약)" style="flex-grow: 1; width: 0; min-width: 100px; padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none; font-size: 13px;">
        <button id="opsKeywordSearchBtn" class="button" style="padding: 6px 20px; font-size: 13px; background: var(--brand); color: white; flex-shrink: 0;">조회</button>
      </div>
    </div>
    <section class="grid">
      <article class="card" style="min-height: 480px;">
        <div class="card-header">
          <div>
            <p class="eyebrow">PUBLISHED</p>
            <h3>발행 기사</h3>
          </div>
        </div>
        <div class="stack">
          ${renderPublishedList(filteredPublished, true, "opsPublished")}
        </div>
      </article>

      <article class="card" style="min-height: 480px;">
        <div class="card-header">
          <div>
            <p class="eyebrow">NEW QUEUE</p>
            <h3>신규 기사</h3>
          </div>
          <button class="button" data-sync-rss="company">최신화</button>
        </div>
        <div class="stack">
          ${renderQueueList("opsQueue", filteredQueued)}
        </div>
      </article>
    </section>
  `;

  wireCategoryTabs("opsPublished");
  wireCategoryTabs("opsQueue");
  wireFeedItemActions();
  wireBulkActions();
  
  document.querySelector('#opsDateSearchBtn')?.addEventListener("click", async () => {
    state.startDate = document.querySelector('#opsStartDate').value;
    state.endDate = document.querySelector('#opsEndDate').value;
    state.searchKeyword = document.querySelector('#opsSearchKeyword').value;
    await renderActivePage();
  });

  document.querySelector('#opsKeywordSearchBtn')?.addEventListener("click", async () => {
    state.startDate = document.querySelector('#opsStartDate').value;
    state.endDate = document.querySelector('#opsEndDate').value;
    state.searchKeyword = document.querySelector('#opsSearchKeyword').value;
    await renderActivePage();
  });

  document.querySelector('#opsClearBtn')?.addEventListener("click", async () => {
    state.startDate = "";
    state.endDate = "";
    state.searchKeyword = "";
    await renderActivePage();
  });

  document.querySelector('#opsSearchKeyword')?.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      state.startDate = document.querySelector('#opsStartDate').value;
      state.endDate = document.querySelector('#opsEndDate').value;
      state.searchKeyword = document.querySelector('#opsSearchKeyword').value;
      await renderActivePage();
    }
  });

  document.querySelector('[data-sync-rss="company"]')?.addEventListener("click", async () => {
    state.lastSyncResult = await api(`/rss-sync?role=${state.role}`, { method: "POST" });
    setNotice(`RSS sync complete: ${state.lastSyncResult.total_imported} items.`);
    await renderActivePage();
  });
}

async function renderParsingSettingsPage(token) {
  const [
    companySources,
    competitorSources,
    companyKeywordRaw,
    competitorKeywordRaw,
    syncStatusRaw,
    naverSettings,
  ] = await Promise.all([
    api(`/rss-sources/company?role=${state.role}`),
    api(`/rss-sources/competitor?role=${state.role}`),
    apiOptional(`/rss-keywords/company?role=${state.role}`, {}),
    apiOptional(`/rss-keywords/competitor?role=${state.role}`, {}),
    apiOptional(`/sync-status?role=${state.role}`, {}),
    apiOptional(`/naver-news-settings?role=${state.role}`, {
      has_client_id: false,
      has_client_secret: false,
      client_id_masked: "",
      client_secret_masked: "",
    }),
  ]);

  if (token !== state.renderToken) return;

  const companyKeyword = normalizeKeywordConfig(companyKeywordRaw);
  const competitorKeyword = normalizeKeywordConfig(competitorKeywordRaw);
  const syncStatus = normalizeSyncStatus(syncStatusRaw);

  app.innerHTML = `
    <section class="grid">
      ${renderParsingSettingsCard("company", "Company Feed", companySources, companyKeyword, syncStatus)}
      ${renderParsingSettingsCard("competitor", "Competitor Feed", competitorSources, competitorKeyword, syncStatus)}
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">기사 파싱</p>
            <h3>네이버 뉴스 연동</h3>
          </div>
        </div>
        <form id="naverNewsFormInline" class="stack">
          <label class="field"><span>Client ID</span><input id="naverClientIdInline" placeholder="${naverSettings.has_client_id ? naverSettings.client_id_masked || "saved" : "NAVER CLIENT ID"}"></label>
          <label class="field"><span>Client Secret</span><input id="naverClientSecretInline" type="password" placeholder="${naverSettings.has_client_secret ? naverSettings.client_secret_masked || "saved" : "NAVER CLIENT SECRET"}"></label>
          <button class="button" type="submit">Save Naver Settings</button>
        </form>
      </article>
    </section>
  `;

  wireRssActions("company");
  wireRssActions("competitor");
  document.querySelector("#naverNewsFormInline")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await api(`/naver-news-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: document.querySelector("#naverClientIdInline").value.trim(),
        client_secret: document.querySelector("#naverClientSecretInline").value.trim(),
      }),
    });
    setNotice("Naver News settings saved.");
    await renderActivePage();
  });
}

function renderSourceList(feedType, sources) {
  if (!sources.length) return `<div style="color: var(--muted); font-size: 13px; padding: 8px;">등록된 언론사가 없습니다.</div>`;
  return sources.map((source) => `
    <div style="display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);">
      <div style="display: flex; flex-direction: column; gap: 4px; overflow: hidden; margin-right: 12px;">
        <strong style="font-size: 14px;">${escapeHtml(source.name)}</strong>
        <a href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer" style="font-size: 12px; color: var(--muted); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px;">${escapeHtml(source.url)}</a>
      </div>
      <button type="button" class="button outline" data-delete-rss="${feedType}:${source.id}" style="padding: 4px 10px; font-size: 12px; border-color: #dc3545; color: #dc3545; flex-shrink: 0;">삭제</button>
    </div>
  `).join("");
}

function renderQueueList(feedType, items) {
  if (!items.length) return `<div class="item"><strong>No queued article yet.</strong></div>`;
  return renderCategoryTabs(feedType, items, true, true, "queue");
}

function renderPublishedList(items, isAdmin = false, feedType = "") {
  if (!items.length) return `<div class="item"><strong>No published article yet.</strong></div>`;
  return renderCategoryTabs(feedType, items, isAdmin, false, "published");
}

function groupItemsByCategory(items) {
  const grouped = {};
  CATEGORY_ORDER.forEach((key) => { grouped[key] = []; });
  
  items.forEach((item) => {
    let key = item.article_category;
    if (key === "hanwha" || key === "lig") {
      key = "competitor";
    }
    if (!CATEGORY_ORDER.includes(key)) {
      key = "reference";
    }
    grouped[key].push(item);
  });
  return grouped;
}

function groupItemsByDate(items) {
  const grouped = {};
  items.forEach((item) => {
    const key = formatArticleDateKey(item.article_published_at);
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(item);
  });
  const keys = Object.keys(grouped);
  keys.sort((a, b) => {
    if (a === "날짜 미상") return 1;
    if (b === "날짜 미상") return -1;
    return b.localeCompare(a);
  });
  return { keys, grouped };
}

function renderCategoryTabs(feedType, items, isAdmin, isQueue, sectionType) {
  const grouped = groupItemsByCategory(items);
  const isPublishedSection = (sectionType === "published");
  const tabDefs = [];
  
  CATEGORY_ORDER.forEach((key) => {
    tabDefs.push({
      key,
      label: CATEGORY_LABELS[key] || key,
      count: (grouped[key] || []).length,
    });
  });

  if (isPublishedSection) {
    grouped["all"] = items;
    tabDefs.push({
      key: "all",
      label: "전체",
      count: items.length,
    });
  }
  
  let firstActive = "kai";
  const kaiTab = tabDefs.find((tab) => tab.key === "kai");
  if (!kaiTab || kaiTab.count === 0) {
    firstActive = tabDefs.find((tab) => tab.count > 0)?.key || "kai";
  }

  const tabs = tabDefs.map((tab) => `
    <button
      class="tab-button ${tab.key === firstActive ? "active" : ""}"
      type="button"
      data-category-tab="${feedType}:${sectionType}:${tab.key}"
    >
      ${escapeHtml(tab.label)} (${tab.count})
    </button>
  `).join("");

  const panes = tabDefs.map((tab) => {
    const rows = grouped[tab.key] || [];
    const dateGrouped = groupItemsByDate(rows);
    const dateBlocks = rows.length
      ? dateGrouped.keys.map((dateKey) => `
        <div class="date-group" style="margin-top: 18px; margin-bottom: 8px; width: 100%;">
          <h4 class="date-header" style="margin: 0 0 10px 0; font-size: 15px; color: var(--brand); font-weight: bold; border-left: 4px solid var(--brand); padding-left: 8px; font-family: 'Segoe UI', 'Noto Sans KR', sans-serif;">${escapeHtml(dateKey)}</h4>
          <div class="stack" style="gap: 12px; width: 100%;">
            ${(dateGrouped.grouped[dateKey] || []).map((item) => renderArticleCard(item, feedType, isAdmin, isQueue)).join("")}
          </div>
        </div>
      `).join("")
      : `<div class="item"><strong>No article in this category.</strong></div>`;
    const bulkActionBar = (isAdmin && rows.length) ? `
      <div class="bulk-action-bar" style="margin-bottom: 16px; padding: 12px; background: #f8f9fa; border-radius: 6px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border: 1px solid #e9ecef;">
        <label style="display: flex; align-items: center; gap: 6px; font-weight: 500; font-size: 13px; cursor: pointer; user-select: none; color: #333;">
          <input type="checkbox" data-bulk-select-all="${feedType}:${sectionType}:${tab.key}" style="width: 15px; height: 15px; cursor: pointer;">
          전체 선택
        </label>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="bulk-selected-count" data-selected-count-for="${feedType}:${sectionType}:${tab.key}" style="font-size: 13px; color: #666; font-weight: 500; margin-right: 8px;">0개 선택됨</span>
          <button type="button" class="button outline" data-bulk-action-delete="${feedType}:${sectionType}:${tab.key}" style="padding: 4px 8px; font-size: 12px; border-color: #dc3545; color: #dc3545;">선택 삭제</button>
          ${isQueue ? `<button type="button" class="button" data-bulk-action-publish="${feedType}:${sectionType}:${tab.key}" style="padding: 4px 8px; font-size: 12px;">선택 발행</button>` : ""}
          <div style="display: flex; align-items: center; gap: 4px; margin-left: 8px;">
            <select data-bulk-move-select="${feedType}:${sectionType}:${tab.key}" style="padding: 4px; font-size: 12px; border-radius: 4px; border: 1px solid #ccc; background: white; outline: none; height: 26px;">
              ${CATEGORY_ORDER.map((key) => `<option value="${key}">${CATEGORY_LABELS[key]}</option>`).join("")}
            </select>
            <button type="button" class="button outline" data-bulk-action-move="${feedType}:${sectionType}:${tab.key}" style="padding: 4px 8px; font-size: 12px;">선택 이동</button>
          </div>
        </div>
      </div>
    ` : "";
    return `
      <div class="stack" data-category-pane="${feedType}:${sectionType}:${tab.key}" style="${tab.key === firstActive ? "" : "display:none;"}">
        ${bulkActionBar}
        ${dateBlocks}
      </div>
    `;
  }).join("");

  return `
    <div class="category-tabs-container" style="width: 100%;">
      <div class="tab-row" style="margin-bottom: 16px;">
        ${tabs}
      </div>
      ${panes}
    </div>
  `;
}

function renderArticleCard(item, feedType, isAdmin, isQueue) {
  const actionFeedType = item.feed_type || feedType;
  return `
    <div class="item">
      <div style="display: flex; gap: 10px; align-items: flex-start;">
        ${isAdmin ? `
          <input type="checkbox" data-bulk-select="${escapeAttr(item.id)}" data-bulk-feed-type="${escapeAttr(actionFeedType)}" class="bulk-checkbox" style="margin-top: 4px; width: 18px; height: 18px; cursor: pointer; flex-shrink: 0;">
        ` : ""}
        <div style="flex-grow: 1;">
          <strong>${escapeHtml(item.title)}</strong>
          <div class="meta">
            <span>${escapeHtml(formatPublisherName(item.article_publisher, item.link, item.source))}</span>
            <span>${escapeHtml(formatArticlePublishedAt(item.article_published_at))}</span>
            ${isAdmin ? `<span>Category: ${escapeHtml(CATEGORY_LABELS[item.article_category] || "참고 기사")}</span>` : ""}
            ${isQueue ? `<span>Selected: ${item.selected ? "Yes" : "No"}</span>` : ""}
          </div>
          <p style="white-space: pre-line;">${escapeHtml(item.summary || "")}</p>
          <a href="${escapeAttr(item.link)}" target="_blank" rel="noreferrer">${escapeHtml(item.link)}</a>
          ${isAdmin ? `
            <div class="actions">
              <select data-category-select="${escapeAttr(item.id)}">
                ${CATEGORY_ORDER.map((key) => {
                  const isSelected = (item.article_category === key) ||
                                     (key === "competitor" && (item.article_category === "hanwha" || item.article_category === "lig"));
                  return `<option value="${key}" ${isSelected ? "selected" : ""}>${escapeHtml(CATEGORY_LABELS[key])}</option>`;
                }).join("")}
              </select>
              <button type="button" class="button outline" data-move-category="${actionFeedType}:${item.id}">Move Category</button>
              ${isQueue ? `<button type="button" class="button" data-publish="${actionFeedType}:${item.id}">Publish</button>` : ""}
              ${isQueue ? `<button type="button" class="button outline" data-toggle="${actionFeedType}:${item.id}">Toggle</button>` : ""}
              <button type="button" class="button outline" data-delete-item="${actionFeedType}:${item.id}">Delete</button>
            </div>
          ` : ""}
        </div>
      </div>
    </div>
  `;
}

function wireRssActions(feedType) {
  document.querySelector(`[data-add-rss="${feedType}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-rss-form="${feedType}"]`);
    await api(`/rss-sources/${feedType}?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.querySelector('[data-field="name"]').value.trim(),
        url: form.querySelector('[data-field="url"]').value.trim(),
      }),
    });
    setNotice("RSS source added.");
    await renderActivePage();
  });
  document.querySelectorAll(`[data-filter-mode^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(`[data-filter-mode^="${feedType}:"]`).forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
    });
  });
  document.querySelector(`[data-save-keywords="${feedType}"]`)?.addEventListener("click", async () => {
    await api(`/rss-keywords/${feedType}?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        keywords: document.querySelector(`[data-keyword-form="${feedType}"]`).value,
        exclude_keywords: document.querySelector(`[data-keyword-exclude-form="${feedType}"]`).value,
        mode: document.querySelector(`[data-filter-mode="${feedType}:keywords"]`)?.classList.contains("active") ? "keywords" : "all",
      }),
    });
    setNotice("RSS keywords saved.");
    await renderActivePage();
  });
  document.querySelector(`[data-sync-rss="${feedType}"]`)?.addEventListener("click", async () => {
    state.lastSyncResult = await api(`/rss-sync?role=${state.role}`, { method: "POST" });
    setNotice(`RSS sync complete: ${state.lastSyncResult.total_imported} items.`);
    await renderActivePage();
  });
  document.querySelectorAll(`[data-delete-rss^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, sourceId] = button.dataset.deleteRss.split(":");
      await api(`/rss-sources/${kind}/${sourceId}?role=${state.role}`, { method: "DELETE" });
      setNotice("RSS source deleted.");
      await renderActivePage();
    });
  });
  document.querySelectorAll(`[data-save-source-keywords^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", async () => {
      const [kind, sourceId] = button.dataset.saveSourceKeywords.split(":");
      const form = document.querySelector(`[data-source-keyword-form="${sourceId}"]`);
      await api(`/rss-sources/${kind}/${sourceId}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          include_keywords: form.querySelector('[data-field="include_keywords"]').value,
          exclude_keywords: form.querySelector('[data-field="exclude_keywords"]').value,
        }),
      });
      setNotice("Source filter saved.");
      await renderActivePage();
    });
  });
}

function wireFeedItemActions() {
  document.querySelectorAll("[data-move-category]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const [kind, itemId] = button.dataset.moveCategory.split(":");
        const selector = document.querySelector(`[data-category-select="${itemId}"]`);
        const category = selector?.value || "reference";
        await api(`/feeds/${kind}/items/${itemId}/category?role=${state.role}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category }),
        });
        setNotice("Article category updated.");
        await renderActivePage();
      } catch (err) {
        console.error("Move Category error:", err);
        setNotice(`Move Category failed: ${err.message}`, "error");
        alert(`이동 실패: ${err.message}\n\n[문제 해결 안내]\n1. 브라우저 주소창과 왼쪽 사이드바의 API Address 주소가 둘 다 동일하게 HTTPS 터널 주소인지 확인해 주세요.\n2. Mixed Content로 인해 브라우저에서 요청을 차단했을 수 있습니다.`);
      }
    });
  });
  document.querySelectorAll("[data-publish]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const [kind, itemId] = button.dataset.publish.split(":");
        const selector = document.querySelector(`[data-category-select="${itemId}"]`);
        const category = selector?.value || "";
        const result = await api(`/feeds/${kind}/publish/${itemId}?role=${state.role}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: category || null }),
        });
        setNotice(result.message);
        await renderActivePage();
      } catch (err) {
        console.error("Publish error:", err);
        setNotice(`Publish failed: ${err.message}`, "error");
        alert(`발행 실패: ${err.message}`);
      }
    });
  });
  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const [kind, itemId] = button.dataset.toggle.split(":");
        const result = await api(`/feeds/${kind}/queue/${itemId}/toggle?role=${state.role}`, { method: "POST" });
        setNotice(result.message);
        await renderActivePage();
      } catch (err) {
        console.error("Toggle error:", err);
        setNotice(`Toggle failed: ${err.message}`, "error");
        alert(`선택 상태 전환 실패: ${err.message}`);
      }
    });
  });
  document.querySelectorAll("[data-delete-item]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        if (!window.confirm("이 기사를 삭제하시겠습니까?")) return;
        const [kind, itemId] = button.dataset.deleteItem.split(":");
        const result = await api(`/feeds/${kind}/items/${itemId}?role=${state.role}`, { method: "DELETE" });
        setNotice(result.message || "Feed item deleted.");
        await renderActivePage();
      } catch (err) {
        console.error("Delete error:", err);
        setNotice(`Delete failed: ${err.message}`, "error");
        alert(`삭제 실패: ${err.message}`);
      }
    });
  });
}

function wireBulkActions() {
  document.querySelectorAll("[data-bulk-select-all]").forEach((selectAllCheckbox) => {
    const key = selectAllCheckbox.dataset.bulkSelectAll; // "feedType:sectionType:category"
    const [feedType, sectionType, category] = key.split(":");
    
    const pane = document.querySelector(`[data-category-pane="${feedType}:${sectionType}:${category}"]`);
    if (!pane) return;
    
    const checkboxes = pane.querySelectorAll(".bulk-checkbox");
    const countDisplay = pane.querySelector(`[data-selected-count-for="${feedType}:${sectionType}:${category}"]`);
    
    const updateCount = () => {
      const checkedCount = pane.querySelectorAll(".bulk-checkbox:checked").length;
      if (countDisplay) {
        countDisplay.textContent = `${checkedCount}개 선택됨`;
      }
      selectAllCheckbox.checked = checkedCount === checkboxes.length && checkboxes.length > 0;
    };
    
    selectAllCheckbox.addEventListener("change", (e) => {
      const isChecked = e.target.checked;
      checkboxes.forEach((cb) => {
        cb.checked = isChecked;
      });
      updateCount();
    });
    
    checkboxes.forEach((cb) => {
      cb.addEventListener("change", () => {
        updateCount();
      });
    });
    
    const deleteBtn = pane.querySelector(`[data-bulk-action-delete="${feedType}:${sectionType}:${category}"]`);
    if (deleteBtn) {
      deleteBtn.addEventListener("click", async () => {
        const checkedBoxes = pane.querySelectorAll(".bulk-checkbox:checked");
        if (checkedBoxes.length === 0) {
          alert("선택된 기사가 없습니다.");
          return;
        }
        if (!confirm(`선택한 ${checkedBoxes.length}개의 기사를 삭제하시겠습니까?`)) {
          return;
        }
        const itemIds = Array.from(checkedBoxes).map((cb) => cb.dataset.bulkSelect);
        const apiFeedType = (feedType === "opsPublished" || feedType === "opsQueue") ? "company" : feedType;
        
        try {
          const result = await api(`/feeds/${apiFeedType}/batch-delete?role=${state.role}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ item_ids: itemIds }),
          });
          setNotice(result.message);
          await renderActivePage();
        } catch (err) {
          setNotice(`Batch delete failed: ${err.message}`, "error");
        }
      });
    }
    
    const publishBtn = pane.querySelector(`[data-bulk-action-publish="${feedType}:${sectionType}:${category}"]`);
    if (publishBtn) {
      publishBtn.addEventListener("click", async () => {
        const checkedBoxes = pane.querySelectorAll(".bulk-checkbox:checked");
        if (checkedBoxes.length === 0) {
          alert("선택된 기사가 없습니다.");
          return;
        }
        if (!confirm(`선택한 ${checkedBoxes.length}개의 기사를 발행하시겠습니까?`)) {
          return;
        }
        const itemIds = Array.from(checkedBoxes).map((cb) => cb.dataset.bulkSelect);
        const apiFeedType = (feedType === "opsPublished" || feedType === "opsQueue") ? "company" : feedType;
        
        try {
          const result = await api(`/feeds/${apiFeedType}/batch-publish?role=${state.role}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ item_ids: itemIds }),
          });
          setNotice(result.message);
          await renderActivePage();
        } catch (err) {
          setNotice(`Batch publish failed: ${err.message}`, "error");
        }
      });
    }
    
    const moveBtn = pane.querySelector(`[data-bulk-action-move="${feedType}:${sectionType}:${category}"]`);
    const moveSelect = pane.querySelector(`[data-bulk-move-select="${feedType}:${sectionType}:${category}"]`);
    if (moveBtn && moveSelect) {
      moveBtn.addEventListener("click", async () => {
        const checkedBoxes = pane.querySelectorAll(".bulk-checkbox:checked");
        if (checkedBoxes.length === 0) {
          alert("선택된 기사가 없습니다.");
          return;
        }
        const targetCategory = moveSelect.value;
        const targetLabel = CATEGORY_LABELS[targetCategory] || targetCategory;
        if (!confirm(`선택한 ${checkedBoxes.length}개의 기사를 '${targetLabel}' 카테고리로 이동하시겠습니까?`)) {
          return;
        }
        const itemIds = Array.from(checkedBoxes).map((cb) => cb.dataset.bulkSelect);
        const apiFeedType = (feedType === "opsPublished" || feedType === "opsQueue") ? "company" : feedType;
        
        try {
          const result = await api(`/feeds/${apiFeedType}/batch-move?role=${state.role}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              item_ids: itemIds,
              category: targetCategory,
            }),
          });
          setNotice(result.message);
          await renderActivePage();
        } catch (err) {
          setNotice(`Batch move failed: ${err.message}`, "error");
        }
      });
    }
  });
}

function wireCategoryTabs(feedType) {
  document.querySelectorAll(`[data-category-tab^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      const [kind, section, category] = button.dataset.categoryTab.split(":");
      document.querySelectorAll(`[data-category-tab^="${kind}:${section}:"]`).forEach((tab) => tab.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(`[data-category-pane^="${kind}:${section}:"]`).forEach((pane) => {
        pane.style.display = "none";
      });
      const activePane = document.querySelector(`[data-category-pane="${kind}:${section}:${category}"]`);
      if (activePane) activePane.style.display = "";
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
  const [settings, googleStatus] = await Promise.all([
    api(`/calendar-settings?role=${state.role}`),
    apiOptional(`/google-calendar/status?role=${state.role}`, {
      credentials_file_present: false,
      credentials_file_path: "",
      credentials_template_path: "",
      token_file_present: false,
      authenticated: false,
      callback_url: "",
      scopes: [],
      calendars: [],
      error: "",
    }),
  ]);
  if (token !== state.renderToken) return;

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Google Calendar</p>
            <h3>Calendar Connection Settings</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>Google OAuth</strong>
            <div class="meta">
              <span>Client File: ${googleStatus.credentials_file_present ? "Ready" : "Missing"}</span>
              <span>Token: ${googleStatus.authenticated ? "Connected" : "Not Connected"}</span>
            </div>
            <p>${googleStatus.credentials_file_present ? "Use the connect button to authorize Google Calendar access." : `Create ${escapeHtml(googleStatus.credentials_file_path || "backend/config/google_oauth_client.json")} from the example file first.`}</p>
            <div class="actions">
              <button class="button" type="button" id="googleConnectButton" ${googleStatus.credentials_file_present ? "" : "disabled"}>Connect Google Calendar</button>
              <button class="button outline" type="button" id="googleDisconnectButton" ${googleStatus.authenticated ? "" : "disabled"}>Disconnect</button>
            </div>
            ${googleStatus.error ? `<p>${escapeHtml(googleStatus.error)}</p>` : ""}
          </div>
          <div class="item">
            <strong>Accessible Calendars</strong>
            <div class="stack">
              ${(googleStatus.calendars || []).map((calendar) => `
                <div class="item">
                  <strong>${escapeHtml(calendar.summary || "(No title)")}</strong>
                  <div class="meta">
                    <span>${escapeHtml(calendar.id)}</span>
                    <span>${calendar.primary === "true" ? "Primary" : escapeHtml(calendar.access_role || "")}</span>
                  </div>
                  <div class="actions">
                    <button class="button outline" type="button" data-copy-calendar-id="${escapeAttr(calendar.id)}" data-target-page="page1">Use For CEO</button>
                    <button class="button outline" type="button" data-copy-calendar-id="${escapeAttr(calendar.id)}" data-target-page="page2">Use For Company</button>
                  </div>
                </div>
              `).join("") || `<div class="item"><strong>No calendar list available yet.</strong></div>`}
            </div>
          </div>
          ${settings.map((item) => `
            <form class="item stack" data-calendar-form="${item.page_id}">
              <div class="card-subheader">
                <strong>${item.page_id === "page1" ? "CEO Calendar" : "Company Calendar"}</strong>
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
          <strong>What Happens Next</strong>
          <p>If OAuth is connected and write sync is enabled, events created from this console will also be written to Google Calendar.</p>
        </div>
      </article>
    </section>
  `;

  document.querySelector("#googleConnectButton")?.addEventListener("click", async () => {
    const payload = await api(`/google-calendar/auth/start?role=${state.role}`);
    window.open(payload.auth_url, "_blank", "noopener,noreferrer");
    setNotice("Google OAuth window opened. Complete sign-in, then reload this page.");
  });
  document.querySelector("#googleDisconnectButton")?.addEventListener("click", async () => {
    await api(`/google-calendar/disconnect?role=${state.role}`, { method: "POST" });
    setNotice("Google Calendar disconnected.");
    await renderActivePage();
  });
  document.querySelectorAll("[data-copy-calendar-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const pageId = button.dataset.targetPage;
      const form = document.querySelector(`[data-calendar-form="${pageId}"]`);
      if (!form) return;
      form.calendar_id.value = button.dataset.copyCalendarId;
      form.status.value = googleStatus.authenticated ? "connected" : "ready";
    });
  });
  document.querySelectorAll("[data-calendar-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await api(`/calendar-settings/${form.dataset.calendarForm}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "google",
          account_email: form.account_email.value.trim(),
          calendar_name: form.calendar_name.value.trim(),
          calendar_id: form.calendar_id.value.trim(),
          status: form.status.value,
          sync_enabled: form.sync_enabled.checked,
          last_synced_at: "",
        }),
      });
      setNotice("Calendar settings saved.");
      await renderActivePage();
    });
  });
}

async function renderOpenAiSettingsPage(token) {
  const [settings, naverSettings] = await Promise.all([
    api(`/openai-settings?role=${state.role}`),
    apiOptional(`/naver-news-settings?role=${state.role}`, {
      has_client_id: false,
      has_client_secret: false,
      client_id_masked: "",
      client_secret_masked: "",
    }),
  ]);

  if (token !== state.renderToken) return;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">AI Provider</p>
            <h3>Article Summary Settings</h3>
          </div>
        </div>
        <form id="openAiForm" class="stack">
          <label class="field">
            <span>Provider</span>
            <select id="aiProvider">
              <option value="openai" ${(settings.provider || "openai") === "openai" ? "selected" : ""}>OpenAI / GPT</option>
              <option value="gemini" ${settings.provider === "gemini" ? "selected" : ""}>Google Gemini</option>
            </select>
          </label>
          <label class="field"><span>API Key</span><input id="openaiApiKey" type="password" placeholder="${settings.has_api_key ? settings.api_key_masked || "saved" : ((settings.provider || "openai") === "gemini" ? "AIza..." : "sk-...")}"></label>
          <label class="field"><span>Summary Model</span><input id="openaiModel" value="${escapeAttr(settings.model || ((settings.provider || "openai") === "gemini" ? "gemini-3.7-flash" : "gpt-5.4-mini"))}"></label>
          <label class="field"><span>Classification Model</span><input id="classificationModel" value="${escapeAttr(settings.classification_model || ((settings.provider || "openai") === "gemini" ? "gemini-3.7-flash" : "gpt-5.4-mini"))}"></label>
          <button class="button" type="submit">Save AI Settings</button>
        </form>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Naver News API</p>
            <h3>KAI News Search Connection</h3>
          </div>
        </div>
        <form id="naverNewsForm" class="stack">
          <label class="field"><span>Client ID</span><input id="naverClientId" placeholder="${naverSettings.has_client_id ? naverSettings.client_id_masked || "saved" : "NAVER CLIENT ID"}"></label>
          <label class="field"><span>Client Secret</span><input id="naverClientSecret" type="password" placeholder="${naverSettings.has_client_secret ? naverSettings.client_secret_masked || "saved" : "NAVER CLIENT SECRET"}"></label>
          <button class="button" type="submit">Save Naver Settings</button>
        </form>
      </article>

      <article class="card">
        <div class="item">
          <strong>Behavior</strong>
          <p>API 키는 이 화면에만 저장하세요. Gemini로 전환하려면 Provider를 Google Gemini로 선택하고 Gemini API 키와 모델명을 저장하면, RSS 기사 분류·요약·텔레그램 중요도 선별이 Gemini로 실행됩니다.</p>
        </div>
      </article>
    </section>
  `;
  document.querySelector("#openAiForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api(`/openai-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: document.querySelector("#aiProvider").value,
        api_key: document.querySelector("#openaiApiKey").value.trim(),
        model: document.querySelector("#openaiModel").value.trim(),
        classification_model: document.querySelector("#classificationModel").value.trim(),
      }),
    });
    setNotice("AI settings saved.");
    await renderActivePage();
  });
  document.querySelector("#naverNewsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api(`/naver-news-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: document.querySelector("#naverClientId").value.trim(),
        client_secret: document.querySelector("#naverClientSecret").value.trim(),
      }),
    });
    setNotice("Naver News settings saved.");
    await renderActivePage();
  });
}

async function renderTelegramSendPage(token) {
  const telegramSettings = await apiOptional(`/telegram-settings?role=${state.role}`, {
    has_telegram_bot_token: false,
    has_telegram_chat_id: false,
    telegram_chat_id: "",
  });
  if (token !== state.renderToken) return;

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Telegram API</p>
            <h3>Breaking News Alert Settings</h3>
          </div>
          <span class="pill">${telegramSettings.has_telegram_bot_token && telegramSettings.has_telegram_chat_id ? "Active" : "Disabled"}</span>
        </div>
        <form id="telegramForm" class="stack">
          <label class="field"><span>Bot Token</span><input id="telegramBotToken" type="password" placeholder="${telegramSettings.has_telegram_bot_token ? "saved" : "123456:ABC-DEF..."}"></label>
          <label class="field"><span>Chat ID</span><input id="telegramChatId" placeholder="${telegramSettings.has_telegram_chat_id ? telegramSettings.telegram_chat_id : "-1001234567890"}"></label>
          <div class="actions">
            <button class="button" type="submit">Save Telegram Settings</button>
          </div>
        </form>
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Workflow</p>
            <h3>알림 및 브리핑 전송 주기</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>자동 브리핑 전송</strong>
            <p>백엔드 서버의 자동 파싱 스케줄러가 매 시간 동작하며 새롭게 발행된 기사들의 브리핑 요약본을 등록된 텔레그램 채널로 자동 전송합니다.</p>
          </div>
          <div class="item">
            <strong>설정 방법</strong>
            <p>1. @BotFather를 통해 생성한 Telegram Bot Token을 입력합니다.<br>2. 메시지를 수신할 Telegram Chat ID(채널 ID)를 입력하고 저장합니다.</p>
          </div>
        </div>
      </article>
    </section>
  `;

  document.querySelector("#telegramForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api(`/telegram-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_token: document.querySelector("#telegramBotToken").value.trim(),
        chat_id: document.querySelector("#telegramChatId").value.trim(),
      }),
    });
    setNotice("Telegram settings saved.");
    await renderActivePage();
  });
}

async function renderAppUsersPage(token) {
  const users = await api(`/app-users?role=${state.role}`);
  if (token !== state.renderToken) return;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">APP Access</p>
            <h3>Mobile User Management</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>Create App User</strong>
            <div class="stack" id="appUserCreateForm">
              <input data-field="username" placeholder="username">
              <input data-field="display_name" placeholder="display name">
              <input data-field="pin" placeholder="PIN" maxlength="8">
              <select data-field="role">
                <option value="staff">Staff</option>
                <option value="ceo">CEO</option>
                <option value="admin">Admin</option>
              </select>
              <button class="button" id="createAppUserButton">Create User</button>
            </div>
          </div>
          ${users.map((user) => `
            <div class="item">
              <strong>${escapeHtml(user.display_name)}</strong>
              <div class="meta">
                <span>${escapeHtml(user.username)}</span>
                <span>${escapeHtml(user.role)}</span>
                <span>${user.active ? "Active" : "Inactive"}</span>
              </div>
              <div class="stack" data-app-user-form="${escapeAttr(user.username)}">
                <input data-field="display_name" value="${escapeAttr(user.display_name)}">
                <input data-field="pin" placeholder="Leave blank to keep current PIN" maxlength="8">
                <select data-field="role">
                  <option value="staff" ${user.role === "staff" ? "selected" : ""}>Staff</option>
                  <option value="ceo" ${user.role === "ceo" ? "selected" : ""}>CEO</option>
                  <option value="admin" ${user.role === "admin" ? "selected" : ""}>Admin</option>
                </select>
                <label class="toggle">
                  <input type="checkbox" data-field="active" ${user.active ? "checked" : ""}>
                  <span>Active</span>
                </label>
                <div class="actions">
                  <button class="button" data-save-app-user="${escapeAttr(user.username)}">Save User</button>
                  ${user.role !== "admin" ? `<button class="button outline" data-delete-app-user="${escapeAttr(user.username)}">Delete User</button>` : ""}
                </div>
              </div>
            </div>
          `).join("")}
        </div>
      </article>
    </section>
  `;

  document.querySelector("#createAppUserButton")?.addEventListener("click", async () => {
    const form = document.querySelector("#appUserCreateForm");
    await api(`/app-users?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.querySelector('[data-field="username"]').value.trim(),
        display_name: form.querySelector('[data-field="display_name"]').value.trim(),
        pin: form.querySelector('[data-field="pin"]').value.trim(),
        role: form.querySelector('[data-field="role"]').value,
      }),
    });
    setNotice("APP user created.");
    await renderActivePage();
  });

  document.querySelectorAll("[data-save-app-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      const username = button.dataset.saveAppUser;
      const form = document.querySelector(`[data-app-user-form="${username}"]`);
      await api(`/app-users/${username}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: form.querySelector('[data-field="display_name"]').value.trim(),
          pin: form.querySelector('[data-field="pin"]').value.trim(),
          role: form.querySelector('[data-field="role"]').value,
          active: form.querySelector('[data-field="active"]').checked,
        }),
      });
      setNotice("APP user updated.");
      await renderActivePage();
    });
  });

  document.querySelectorAll("[data-delete-app-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/app-users/${button.dataset.deleteAppUser}?role=${state.role}`, {
        method: "DELETE",
      });
      setNotice("APP user deleted.");
      await renderActivePage();
    });
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

function formatArticlePublishedAt(value) {
  if (!value) return "Published: Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `Published: ${value}`;
  const text = date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `Published: ${text}`;
}

function formatArticleDateKey(value) {
  if (!value) return "날짜 미상";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "날짜 미상";
  return date.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  });
}

function formatPublisherName(publisher, link, fallbackSource) {
  const host = normalizeHost(publisher) || normalizeHost(link);
  if (!host) return fallbackSource || "-";

  const publisherMap = {
    "news.mtn.co.kr": "MTN",
    "mtn.co.kr": "MTN",
    "kyosu.net": "교수신문",
    "chosun.com": "조선일보",
    "www.chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
  };

  return publisherMap[host] || host;
}

function normalizeHost(value) {
  if (!value) return "";
  const text = String(value).trim().toLowerCase();
  if (!text) return "";
  if (text.includes("://")) {
    try {
      const host = new URL(text).hostname.toLowerCase();
      return host.startsWith("www.") ? host.slice(4) : host;
    } catch {
      return "";
    }
  }
  return text.startsWith("www.") ? text.slice(4) : text;
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
