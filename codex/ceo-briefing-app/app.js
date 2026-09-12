const roles = {
  ceo: {
    name: "CEO",
    description: "CEO 전용 열람자입니다. 승인이나 데이터 발행은 할 수 없습니다.",
    pages: ["page1", "page2", "page3", "page4"],
  },
  admin: {
    name: "관리자",
    description: "전 페이지 접근과 승인, 발행, 편집이 가능합니다.",
    pages: ["page1", "page2", "page3", "page4", "page5"],
  },
  staff: {
    name: "실무자",
    description: "회사 일정 수정 요청과 기사 검토 페이지 일부 접근이 가능합니다.",
    pages: ["page2", "page3", "page4"],
  },
};

const state = {
  activeRole: "admin",
  activePage: "page1",
  toast: "",
  calendarPages: {
    page1: [
      { time: "08:30", title: "임원 조찬 미팅", place: "본사 18층", owner: "비서실", status: "확정" },
      { time: "11:00", title: "신사업 리뷰", place: "화상회의", owner: "전략실", status: "조정중" },
      { time: "16:00", title: "대외 미팅", place: "서울 여의도", owner: "대외협력", status: "확정" },
    ],
    page2: [
      { time: "09:00", title: "전사 타운홀", place: "대강당", owner: "인사팀", status: "사내공지" },
      { time: "14:00", title: "감사위원회", place: "이사회실", owner: "재경팀", status: "승인완료" },
      { time: "17:30", title: "분기 실적 점검", place: "본사 12층", owner: "IR", status: "수정요청대기" },
    ],
  },
  pendingRequests: [
    {
      id: "req-101",
      title: "분기 실적 점검 시간을 17:30 -> 18:00으로 변경 요청",
      requester: "IR팀 김민수",
      reason: "해외 법인 보고 시차 반영",
      status: "대기",
    },
    {
      id: "req-102",
      title: "전사 타운홀 장소를 대강당 -> 온라인 병행으로 변경 요청",
      requester: "인사팀 박서연",
      reason: "지사 참석자 고려",
      status: "대기",
    },
  ],
  feeds: {
    page3: {
      published: [
        {
          id: "news-1",
          title: "주요 사업부, 해외 수주 확대",
          summary: "해외 주요 고객사와 장기 계약 체결.",
          link: "https://example.com/company-news-1",
          source: "경제매체 A",
        },
        {
          id: "news-2",
          title: "AI 기반 운영 효율화 계획 발표",
          summary: "내부 생산성과 의사결정 고도화를 위한 AI 로드맵 발표.",
          link: "https://example.com/company-news-2",
          source: "산업매체 B",
        },
      ],
      queued: [
        {
          id: "draft-1",
          title: "신공장 투자 검토 기사",
          summary: "투자 검토 초기 단계 관련 업계 보도.",
          link: "https://example.com/draft-1",
          source: "RSS Parser",
          selected: true,
        },
        {
          id: "draft-2",
          title: "노조 협상 관련 커뮤니티 이슈",
          summary: "검증 전 내용으로 보류 필요.",
          link: "https://example.com/draft-2",
          source: "AI Agent",
          selected: false,
        },
      ],
    },
    page4: {
      published: [
        {
          id: "comp-1",
          title: "경쟁사 A, 북미 신제품 출시",
          summary: "프리미엄 라인업 확장 및 현지 채널 강화.",
          link: "https://example.com/competitor-1",
          source: "산업뉴스 C",
        },
      ],
      queued: [
        {
          id: "comp-draft-1",
          title: "경쟁사 B, 구조조정 가능성 보도",
          summary: "복수 언론에서 관련 보도 수집.",
          link: "https://example.com/comp-draft-1",
          source: "Python Scraper",
          selected: true,
        },
        {
          id: "comp-draft-2",
          title: "경쟁사 C, 일본 JV 논의",
          summary: "사실 확인 전 참고 자료.",
          link: "https://example.com/comp-draft-2",
          source: "AI Summarizer",
          selected: false,
        },
      ],
    },
  },
};

const pages = [
  {
    id: "page1",
    title: "페이지#1 · CEO 일정",
    description: "CEO 일정은 Google Calendar와 동일한 형태로 보이고, 실시간 동기화 확장 포인트를 둔 상태입니다.",
    access: ["ceo", "admin"],
    render: renderCalendarPage,
  },
  {
    id: "page2",
    title: "페이지#2 · 회사 주요일정",
    description: "회사 내 주요 일정과 수정 요청 승인 흐름을 함께 관리합니다.",
    access: ["ceo", "admin", "staff"],
    render: renderCompanySchedulePage,
  },
  {
    id: "page3",
    title: "페이지#3 · 회사관련 주요 기사",
    description: "로컬 파싱 페이지에서 선택해 APP으로 전달한 기사만 노출합니다.",
    access: ["ceo", "admin", "staff"],
    render: () => renderFeedPage("page3", "회사 주요 기사"),
  },
  {
    id: "page4",
    title: "페이지#4 · 타회사 동향 공유",
    description: "AI 또는 Python 파서가 모은 경쟁사 동향 중 승인 항목만 APP에 표시합니다.",
    access: ["ceo", "admin", "staff"],
    render: () => renderFeedPage("page4", "타회사 동향"),
  },
  {
    id: "page5",
    title: "페이지#5 · 페이지 관리",
    description: "페이지가 추가될 수 있음을 고려한 메뉴/권한 등록 영역입니다.",
    access: ["admin"],
    render: renderManagementPage,
  },
];

const roleSelect = document.querySelector("#roleSelect");
const roleDescription = document.querySelector("#roleDescription");
const pageNav = document.querySelector("#pageNav");
const pageTitle = document.querySelector("#pageTitle");
const pageDescription = document.querySelector("#pageDescription");
const appMount = document.querySelector("#appMount");
const syncButton = document.querySelector("#syncButton");

function init() {
  renderRoleOptions();
  renderNavigation();
  renderPage();

  roleSelect.addEventListener("change", (event) => {
    state.activeRole = event.target.value;
    const allowedPages = getAllowedPages();
    if (!allowedPages.some((page) => page.id === state.activePage)) {
      state.activePage = allowedPages[0]?.id;
    }
    renderNavigation();
    renderPage();
  });

  syncButton.addEventListener("click", () => {
    state.toast = `${new Date().toLocaleTimeString("ko-KR")} 기준 mock sync가 완료되었습니다.`;
    renderPage();
  });
}

function renderRoleOptions() {
  roleSelect.innerHTML = Object.entries(roles)
    .map(([id, role]) => `<option value="${id}" ${id === state.activeRole ? "selected" : ""}>${role.name}</option>`)
    .join("");
}

function getAllowedPages() {
  return pages.filter((page) => page.access.includes(state.activeRole));
}

function renderNavigation() {
  const allowedPages = getAllowedPages();
  pageNav.innerHTML = allowedPages
    .map((page) => `<button class="${page.id === state.activePage ? "active" : ""}" data-page-id="${page.id}">${page.title}</button>`)
    .join("");

  roleDescription.textContent = roles[state.activeRole].description;

  pageNav.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.activePage = button.dataset.pageId;
      renderNavigation();
      renderPage();
    });
  });
}

function renderPage() {
  const currentPage = pages.find((page) => page.id === state.activePage);
  pageTitle.textContent = currentPage.title;
  pageDescription.textContent = currentPage.description;

  appMount.innerHTML = state.toast ? `<div class="banner">${state.toast}</div>` : "";
  appMount.appendChild(currentPage.render());
}

function renderCalendarPage() {
  const template = document.querySelector("#calendarPageTemplate").content.cloneNode(true);
  const tableBody = template.querySelector("[data-calendar-body]");
  const rows = state.calendarPages.page1
    .map((item) => `
      <tr>
        <td>${item.time}</td>
        <td>${item.title}</td>
        <td>${item.place}</td>
        <td>${item.owner}</td>
        <td>${item.status}</td>
      </tr>
    `)
    .join("");

  template.querySelector("[data-calendar-source]").textContent = "Google Calendar / ceo@company.com";
  template.querySelector("[data-calendar-title]").textContent = "CEO 일정 캘린더";
  template.querySelector("[data-action-title]").textContent = "실시간 동기화 준비 상태";
  tableBody.innerHTML = rows;

  template.querySelector("[data-action-body]").innerHTML = `
    <div class="action-stack">
      <div class="action-box">
        <strong>양방향 동기화 포인트</strong>
        <p>현재 MVP는 로컬 데이터로 동작합니다. 실제 연동 시 Google Calendar API의 읽기/쓰기 권한을 연결하면 됩니다.</p>
        <span class="tag">읽기</span>
        <span class="tag">수정</span>
        <span class="tag">실시간 갱신</span>
      </div>
      <div class="action-box">
        <strong>권한 예시</strong>
        <p>CEO와 관리자만 페이지 접근 가능하며, 실무자는 메뉴가 보이지 않습니다.</p>
      </div>
    </div>
  `;

  return template;
}

function renderCompanySchedulePage() {
  const template = document.querySelector("#calendarPageTemplate").content.cloneNode(true);
  const tableBody = template.querySelector("[data-calendar-body]");
  const rows = state.calendarPages.page2
    .map((item) => `
      <tr>
        <td>${item.time}</td>
        <td>${item.title}</td>
        <td>${item.place}</td>
        <td>${item.owner}</td>
        <td>${item.status}</td>
      </tr>
    `)
    .join("");

  template.querySelector("[data-calendar-source]").textContent = "Google Calendar / company-events@company.com";
  template.querySelector("[data-calendar-title]").textContent = "회사 주요일정";
  template.querySelector("[data-action-title]").textContent = "수정 요청 승인";
  tableBody.innerHTML = rows;

  const canApprove = state.activeRole === "admin";
  const canRequest = state.activeRole === "staff";

  template.querySelector("[data-action-body]").innerHTML = `
    <div class="action-stack">
      ${state.pendingRequests
        .map(
          (request) => `
            <div class="action-box">
              <strong>${request.title}</strong>
              <div class="inline-meta">
                <span>요청자: ${request.requester}</span>
                <span>상태: ${request.status}</span>
              </div>
              <p>${request.reason}</p>
              ${canApprove ? `<button class="primary-button" data-approve-id="${request.id}">승인</button>` : ""}
              ${canApprove ? `<button class="secondary-button" data-reject-id="${request.id}">반려</button>` : ""}
            </div>
          `,
        )
        .join("")}
      ${
        canRequest
          ? `
            <div class="action-box">
              <strong>실무자 수정 요청</strong>
              <p>실운영에서는 폼 제출 시 관리자 승인 큐로 전송되도록 연결합니다.</p>
              <button class="primary-button" data-create-request="true">샘플 요청 생성</button>
            </div>
          `
          : ""
      }
    </div>
  `;

  wireScheduleActions(template);
  return template;
}

function wireScheduleActions(fragment) {
  fragment.querySelectorAll("[data-approve-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = state.pendingRequests.find((request) => request.id === button.dataset.approveId);
      if (item) {
        item.status = "승인";
        state.toast = `${item.id} 요청을 승인했습니다.`;
        renderPage();
      }
    });
  });

  fragment.querySelectorAll("[data-reject-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = state.pendingRequests.find((request) => request.id === button.dataset.rejectId);
      if (item) {
        item.status = "반려";
        state.toast = `${item.id} 요청을 반려했습니다.`;
        renderPage();
      }
    });
  });

  const createButton = fragment.querySelector("[data-create-request]");
  if (createButton) {
    createButton.addEventListener("click", () => {
      state.pendingRequests.unshift({
        id: `req-${100 + state.pendingRequests.length + 1}`,
        title: "새 일정 변경 요청이 등록되었습니다.",
        requester: "실무자 자동생성",
        reason: "로컬 MVP 요청 흐름 테스트",
        status: "대기",
      });
      state.toast = "새 수정 요청이 관리자 승인 큐에 등록되었습니다.";
      renderPage();
    });
  }
}

function renderFeedPage(pageId, label) {
  const feed = state.feeds[pageId];
  const template = document.querySelector("#feedPageTemplate").content.cloneNode(true);

  template.querySelector("[data-feed-title]").textContent = `${label} APP 노출`;
  template.querySelector("[data-feed-console-title]").textContent = `${label} 로컬 선별/전송`;

  template.querySelector("[data-feed-list]").innerHTML = `
    <div class="feed-list">
      ${feed.published
        .map(
          (item) => `
            <article class="feed-item">
              <strong>${item.title}</strong>
              <div class="inline-meta">
                <span>${item.source}</span>
                <span>APP 게시중</span>
              </div>
              <details>
                <summary>상세 내용 보기</summary>
                <p>${item.summary}</p>
                <a class="link-button" href="${item.link}" target="_blank" rel="noreferrer">원문 링크</a>
              </details>
            </article>
          `,
        )
        .join("")}
    </div>
  `;

  const canManage = state.activeRole === "admin" || state.activeRole === "staff";
  template.querySelector("[data-feed-console]").innerHTML = `
    <div class="console-list">
      ${feed.queued
        .map(
          (item) => `
            <article class="console-item">
              <strong>${item.title}</strong>
              <div class="inline-meta">
                <span>수집: ${item.source}</span>
                <span>선택: ${item.selected ? "예" : "아니오"}</span>
              </div>
              <p>${item.summary}</p>
              ${
                canManage
                  ? `<button class="primary-button" data-publish-id="${pageId}:${item.id}">APP 전송</button>
                     <button class="secondary-button" data-toggle-id="${pageId}:${item.id}">선택 토글</button>`
                  : ""
              }
            </article>
          `,
        )
        .join("")}
    </div>
  `;

  wireFeedActions(template, pageId);
  return template;
}

function wireFeedActions(fragment, pageId) {
  fragment.querySelectorAll("[data-publish-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const [, itemId] = button.dataset.publishId.split(":");
      const feed = state.feeds[pageId];
      const item = feed.queued.find((entry) => entry.id === itemId);
      if (!item) {
        return;
      }
      feed.published.unshift({ ...item, source: `${item.source} / 승인게시` });
      state.toast = `${item.title} 항목을 APP 게시 목록으로 전송했습니다.`;
      renderPage();
    });
  });

  fragment.querySelectorAll("[data-toggle-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const [, itemId] = button.dataset.toggleId.split(":");
      const feed = state.feeds[pageId];
      const item = feed.queued.find((entry) => entry.id === itemId);
      if (!item) {
        return;
      }
      item.selected = !item.selected;
      state.toast = `${item.title} 항목의 선택 상태를 변경했습니다.`;
      renderPage();
    });
  });
}

function renderManagementPage() {
  const wrapper = document.createElement("section");
  wrapper.className = "card";
  wrapper.innerHTML = `
    <div class="card-header">
      <div>
        <p class="eyebrow">확장 가능 구조</p>
        <h3>페이지/권한 등록 현황</h3>
      </div>
      <span class="pill">Scalable Registry</span>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>페이지 ID</th>
          <th>제목</th>
          <th>접근 권한</th>
          <th>설명</th>
        </tr>
      </thead>
      <tbody>
        ${pages
          .map(
            (page) => `
              <tr>
                <td>${page.id}</td>
                <td>${page.title}</td>
                <td>${page.access.map((roleId) => roles[roleId].name).join(", ")}</td>
                <td>${page.description}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
    <div class="action-box" style="margin-top: 16px;">
      <strong>다음 확장 방법</strong>
      <p><code>pages</code> 배열에 새 페이지를 추가하고 <code>access</code> 권한만 설정하면 메뉴와 접근 제어가 자동 반영됩니다.</p>
    </div>
  `;
  return wrapper;
}

init();
