(function () {
  const config = window.CONSOLE_PAGE_CONFIG;
  const queueList = document.querySelector("#queueList");
  const publishedList = document.querySelector("#publishedList");
  const publishSelectedButton = document.querySelector("#publishSelected");

  const state = loadState();

  function loadState() {
    const saved = window.localStorage.getItem(config.storageKey);
    if (saved) {
      return JSON.parse(saved);
    }
    const initial = {
      queue: config.seedQueue,
      published: [],
      notice: `${config.title} 선별 콘솔이 준비되었습니다.`,
    };
    persist(initial);
    return initial;
  }

  function persist(nextState) {
    window.localStorage.setItem(config.storageKey, JSON.stringify(nextState));
  }

  function render() {
    const noticeHtml = state.notice ? `<div class="notice">${state.notice}</div>` : "";
    queueList.innerHTML = noticeHtml + state.queue.map(renderQueueItem).join("");
    publishedList.innerHTML = state.published.length
      ? state.published.map(renderPublishedItem).join("")
      : '<div class="item"><strong>아직 APP 전송 이력이 없습니다.</strong><p>왼쪽 목록에서 선택 후 상단 버튼으로 전송하세요.</p></div>';

    bindActions();
  }

  function renderQueueItem(item) {
    return `
      <article class="item">
        <strong>${item.title}</strong>
        <div class="meta">
          <span>${item.source}</span>
          <span>선택: ${item.selected ? "예" : "아니오"}</span>
        </div>
        <p>${item.summary}</p>
        <a href="${item.link}" target="_blank" rel="noreferrer">원문 링크</a>
        <div class="actions">
          <button class="button-outline" data-toggle="${item.id}">선택 토글</button>
          <button class="button" data-publish-one="${item.id}">즉시 APP 전송</button>
        </div>
      </article>
    `;
  }

  function renderPublishedItem(item) {
    return `
      <article class="item">
        <strong>${item.title}</strong>
        <div class="meta">
          <span>${item.source}</span>
          <span class="badge">APP 전송 완료</span>
        </div>
        <p>${item.summary}</p>
      </article>
    `;
  }

  function bindActions() {
    document.querySelectorAll("[data-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = state.queue.find((item) => item.id === button.dataset.toggle);
        if (!target) {
          return;
        }
        target.selected = !target.selected;
        state.notice = `${target.title} 선택 상태를 변경했습니다.`;
        persist(state);
        render();
      });
    });

    document.querySelectorAll("[data-publish-one]").forEach((button) => {
      button.addEventListener("click", () => {
        publishItems([button.dataset.publishOne]);
      });
    });
  }

  function publishItems(ids) {
    const targets = state.queue.filter((item) => ids.includes(item.id));
    if (!targets.length) {
      state.notice = "전송할 항목이 없습니다.";
      persist(state);
      render();
      return;
    }

    targets.forEach((item) => {
      state.published.unshift({
        title: item.title,
        summary: item.summary,
        source: `${item.source} / 로컬전송`,
      });
    });

    state.notice = `${targets.length}건을 APP 전송 목록으로 반영했습니다.`;
    persist(state);
    render();
  }

  publishSelectedButton.addEventListener("click", () => {
    const selectedIds = state.queue.filter((item) => item.selected).map((item) => item.id);
    publishItems(selectedIds);
  });

  render();
})();
