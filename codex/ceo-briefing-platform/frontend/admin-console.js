const state = {
  apiBase: "http://127.0.0.1:8011",
  role: "admin",
  pages: [],
  activePage: null,
  renderToken: 0,
  notice: "",
  noticeType: "info",
  lastSyncResult: null,
  calendarSettings: [],
  openai: null,
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

function setNotice(message, type = "info") {
  state.notice = message;
  state.noticeType = type;
  notice.innerHTML = message ? `<div class="notice ${type === "error" ? "error" : ""}">${message}</div>` : "";
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
    state.pages = await api(`/pages?role=${state.role}`);
    state.activePage = state.pages.find((page) => page.id === state.activePage)?.id ?? state.pages[0]?.id ?? null;
    renderNavigation();
    await renderActivePage();
  } catch (error) {
    setNotice(`Backend connection failed: ${error.message}`, "error");
    pageTitle.textContent = "Backend Needed";
    pageDescription.textContent = "Start the backend server and reload this page.";
    app.innerHTML = "";
  }
}

function renderNavigation() {
  pageNav.innerHTML = state.pages
    .map((page) => `<button class="${page.id === state.activePage ? "active" : ""}" data-page-id="${page.id}">${page.title}</button>`)
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
  await renderOpenAISettingsPage(token);
}

async function renderCalendarPage(pageId, token) {
  const payload = await api(`/calendar/${pageId}?role=${state.role}`);
  const requests = pageId === "page2" ? await api(`/requests?role=${state.role}`) : [];
  const settings = await api(`/calendar-settings?role=${state.role}`);
  if (token !== state.renderToken) return;

  const integration = settings.find((item) => item.page_id === pageId);
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${payload.source}</p>
            <h3>${pageId === "page1" ? "CEO Calendar" : "Company Calendar"}</h3>
          </div>
          <span class="pill">${integration?.sync_enabled ? "Write Enabled" : "Local Write"}</span>
        </div>
        <table>
          <thead>
            <tr><th>Date</th><th>Time</th><th>Title</th><th>Place</th><th>Owner</th><th>Status</th></tr>
          </thead>
          <tbody>
            ${payload.events.map((event) => `<tr><td>${formatDateLabel(event.date)}</td><td>${event.time}</td><td>${event.title}</td><td>${event.place}</td><td>${event.owner}</td><td>${event.status}</td></tr>`).join("")}
          </tbody>
        </table>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${pageId === "page1" ? "Calendar Write" : "Approval Flow"}</p>
            <h3>${pageId === "page1" ? "Add Calendar Event" : "Update Requests"}</h3>
          </div>
        </div>
        <div class="stack">
          ${pageId === "page2" ? renderRequestCards(requests) : ""}
          ${renderEventCreateForm(pageId, integration)}
          ${pageId === "page2" ? renderRequestCreateForm() : ""}
        </div>
      </article>
    </section>
  `;

  wireCalendarActions(pageId);
}

function renderRequestCards(requests) {
  return requests.map((request) => `
    <div class="item">
      <strong>${request.title}</strong>
      <div class="meta">
        <span>Requester: ${request.requester}</span>
        <span>Status: ${request.status}</span>
      </div>
      <p>${request.reason}</p>
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

function renderEventCreateForm(pageId, integration) {
  return `
    <div class="item">
      <strong>Add Calendar Event</strong>
      <div class="meta">
        <span>Calendar ID: ${integration?.calendar_id ?? "-"}</span>
        <span>Status: ${translateCalendarStatus(integration?.status ?? "not_connected")}</span>
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
    const payloadRequest = {
      title: document.querySelector("#requestTitle").value.trim(),
      requester: document.querySelector("#requester").value.trim(),
      reason: document.querySelector("#requestReason").value.trim(),
    };
    await api(`/requests?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadRequest),
    });
    setNotice("Request created.");
    await renderActivePage();
  });

  const button = document.querySelector(`[data-create-event="${pageId}"]`);
  if (button) {
    button.addEventListener("click", async () => {
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
}

async function renderRssFeedPage(feedType, title, workspaceLabel, token) {
  const [payload, sources, keywordConfig, syncStatus] = await Promise.all([
    api(`/feeds/${feedType}?role=${state.role}`),
    api(`/rss-sources/${feedType}?role=${state.role}`),
    api(`/rss-keywords/${feedType}?role=${state.role}`),
    api(`/sync-status?role=${state.role}`),
  ]);
  if (token !== state.renderToken) return;

  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${workspaceLabel}</p>
            <h3>${title} RSS Registration</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item">
            <strong>Keyword Filter</strong>
            <p>Choose whether to import all article titles or only keyword matches.</p>
            <div class="stack" data-keyword-form="${feedType}">
              <div class="tab-row">
                <button class="tab-button ${keywordConfig.mode === "all" ? "active" : ""}" type="button" data-filter-mode="${feedType}:all">All Articles</button>
                <button class="tab-button ${keywordConfig.mode === "keywords" ? "active" : ""}" type="button" data-filter-mode="${feedType}:keywords">Keyword Match Only</button>
              </div>
              <textarea data-field="keywords" rows="3" placeholder="Include keywords">${escapeHtml(keywordConfig.raw || "")}</textarea>
              <textarea data-field="exclude_keywords" rows="3" placeholder="Exclude keywords">${escapeHtml(keywordConfig.exclude_raw || "")}</textarea>
              <div class="meta">
                <span>${keywordConfig.keywords.length} include</span>
                <span>${keywordConfig.exclude_keywords.length} exclude</span>
              </div>
              <button class="button" data-save-keywords="${feedType}">Save Keywords</button>
            </div>
          </div>
          <div class="item">
            <strong>Auto Parsing Status</strong>
            <div class="meta">
              <span>Window: ${syncStatus.window}</span>
              <span>Last Run: ${syncStatus.last_run || "not yet"}</span>
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
          ${sources.map((source) => `
            <div class="item">
              <strong>${source.name}</strong>
              <div class="meta">
                <span>Type: ${source.feed_type}</span>
                <span>Status: ${source.active ? "Active" : "Inactive"}</span>
              </div>
              <a href="${source.url}" target="_blank" rel="noreferrer">${source.url}</a>
              <div class="stack" data-source-keyword-form="${source.id}">
                <textarea data-field="include_keywords" rows="2" placeholder="Source include keywords">${escapeHtml(source.include_keywords || "")}</textarea>
                <textarea data-field="exclude_keywords" rows="2" placeholder="Source exclude keywords">${escapeHtml(source.exclude_keywords || "")}</textarea>
                <div class="actions">
                  <button class="button" data-save-source-keywords="${feedType}:${source.id}">Save Source Filter</button>
                  <button class="button outline" data-delete-rss="${feedType}:${source.id}">Delete</button>
                </div>
              </div>
            </div>
          `).join("") || '<div class="item"><strong>No RSS source added yet.</strong></div>'}
        </div>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">${workspaceLabel}</p>
            <h3>${title} Article Queue</h3>
          </div>
          <button class="button" data-sync-rss="${feedType}">최신화</button>
        </div>
        <div class="stack">
          ${renderLastSyncResult(feedType)}
          ${payload.queued.map((item) => `
            <div class="item">
              <strong>${item.title}</strong>
              <div class="meta">
                <span>${item.source}</span>
                <span>Selected: ${item.selected ? "Yes" : "No"}</span>
              </div>
              <p>${item.summary}</p>
              <div class="actions">
                <button class="button" data-publish="${feedType}:${item.id}">Publish</button>
                <button class="button outline" data-toggle="${feedType}:${item.id}">Toggle</button>
              </div>
            </div>
          `).join("") || '<div class="item"><strong>No queued article yet.</strong></div>'}
        </div>
      </article>
    </section>
  `;

  wireRssActions(feedType);
}

function wireRssActions(feedType) {
  document.querySelector(`[data-add-rss="${feedType}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-rss-form="${feedType}"]`);
    const payloadSource = {
      name: form.querySelector('[data-field="name"]').value.trim(),
      url: form.querySelector('[data-field="url"]').value.trim(),
    };
    await api(`/rss-sources/${feedType}?role=${state.role}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadSource),
    });
    setNotice("RSS source added.");
    await renderActivePage();
  });

  document.querySelector(`[data-save-keywords="${feedType}"]`)?.addEventListener("click", async () => {
    const form = document.querySelector(`[data-keyword-form="${feedType}"]`);
    const keywords = form.querySelector('[data-field="keywords"]').value;
    const excludeKeywords = form.querySelector('[data-field="exclude_keywords"]').value;
    const activeMode = form.querySelector(".tab-button.active")?.dataset.filterMode?.split(":")[1] || "all";
    await api(`/rss-keywords/${feedType}?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keywords, exclude_keywords: excludeKeywords, mode: activeMode }),
    });
    setNotice("RSS keywords saved.");
    await renderActivePage();
  });

  document.querySelectorAll(`[data-filter-mode^="${feedType}:"]`).forEach((button) => {
    button.addEventListener("click", () => {
      const form = document.querySelector(`[data-keyword-form="${feedType}"]`);
      form.querySelectorAll(".tab-button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
    });
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
      const includeKeywords = form.querySelector('[data-field="include_keywords"]').value;
      const excludeKeywords = form.querySelector('[data-field="exclude_keywords"]').value;
      await api(`/rss-sources/${kind}/${sourceId}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_keywords: includeKeywords, exclude_keywords: excludeKeywords }),
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
  if (!state.lastSyncResult) return "";
  const matched = state.lastSyncResult.results.filter((entry) => entry.feed_type === feedType);
  if (!matched.length) return "";
  return `
    <div class="item">
      <strong>Latest Sync Result</strong>
      <div class="stack">
        ${matched.map((entry) => `<div class="meta"><span>${entry.source_name}</span><span>${entry.item_count} imported</span></div>`).join("")}
      </div>
    </div>
  `;
}

async function renderCalendarSettingsPage(token) {
  const settings = await api(`/calendar-settings?role=${state.role}`);
  if (token !== state.renderToken) return;
  state.calendarSettings = settings;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Google Calendar</p>
            <h3>Calendar Connections</h3>
          </div>
        </div>
        <div class="stack">
          ${state.calendarSettings.map((item) => `
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
              <div class="actions">
                <button class="button" type="submit">Save Calendar Settings</button>
              </div>
            </form>
          `).join("")}
        </div>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Write Workflow</p>
            <h3>How Write Access Works</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item"><strong>Step 1</strong><p>Register Google account email and calendar ID.</p></div>
          <div class="item"><strong>Step 2</strong><p>Enable write sync for the schedule page.</p></div>
          <div class="item"><strong>Step 3</strong><p>Add events from the schedule pages. This flow can later connect to real Google write APIs.</p></div>
        </div>
      </article>
    </section>
  `;

  document.querySelectorAll("[data-calendar-form]").forEach((formElement) => {
    formElement.addEventListener("submit", async (event) => {
      event.preventDefault();
      const pageId = event.currentTarget.dataset.calendarForm;
      const form = new FormData(event.currentTarget);
      const payload = {
        provider: "google",
        account_email: form.get("account_email"),
        calendar_name: form.get("calendar_name"),
        calendar_id: form.get("calendar_id"),
        status: form.get("status"),
        sync_enabled: form.get("sync_enabled") === "on",
        last_synced_at: new Date().toISOString(),
      };
      await api(`/calendar-settings/${pageId}?role=${state.role}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setNotice(`${pageId} calendar settings saved.`);
      await renderActivePage();
    });
  });
}

async function renderOpenAISettingsPage(token) {
  const openai = await api(`/openai-settings?role=${state.role}`);
  if (token !== state.renderToken) return;
  state.openai = openai;
  app.innerHTML = `
    <section class="grid">
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">OpenAI</p>
            <h3>API Key and Model</h3>
          </div>
        </div>
        <form id="openaiSettingsForm" class="stack">
          <label class="field">
            <span>Saved API Key</span>
            <input value="${escapeAttr(state.openai.api_key_masked || "not saved")}" disabled>
          </label>
          <label class="field">
            <span>New API Key</span>
            <input name="api_key" placeholder="sk-...">
          </label>
          <label class="field">
            <span>Model</span>
            <input name="model" value="${escapeAttr(state.openai.model)}">
          </label>
          <div class="actions">
            <button class="button" type="submit">Save OpenAI Settings</button>
          </div>
        </form>
      </article>
      <article class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Summary Workflow</p>
            <h3>How AI Summary Is Used</h3>
          </div>
        </div>
        <div class="stack">
          <div class="item"><strong>1. Add RSS source</strong><p>Register new RSS URLs on KAI or Competitor pages.</p></div>
          <div class="item"><strong>2. Run RSS sync</strong><p>New feed items are imported into the review queue.</p></div>
          <div class="item"><strong>3. AI fallback</strong><p>If API key exists, summaries are generated by OpenAI. If not, RSS titles/descriptions are still imported.</p></div>
        </div>
      </article>
    </section>
  `;

  document.querySelector("#openaiSettingsForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await api(`/openai-settings?role=${state.role}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: form.get("api_key"), model: form.get("model") }),
    });
    setNotice("OpenAI settings saved.");
    await renderActivePage();
  });
}

function translateCalendarStatus(status) {
  return { not_connected: "Not Connected", ready: "Ready", connected: "Connected" }[status] ?? status;
}

function formatDateLabel(dateText) {
  if (!dateText) return "-";
  const date = new Date(`${dateText}T00:00:00`);
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][date.getDay()];
  return `${dateText} (${weekday})`;
}

function escapeHtml(value = "") {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function escapeAttr(value = "") {
  return escapeHtml(value).replaceAll('"', "&quot;");
}

init();
