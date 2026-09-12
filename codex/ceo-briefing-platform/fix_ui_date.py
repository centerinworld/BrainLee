with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\admin-console-runtime-v2.js", "r", encoding="utf-8") as f:
    content = f.read()

import re

# Update state variables
content = content.replace(
    '''  if (state.selectedDate === undefined) {
    state.selectedDate = "";
  }''',
    '''  if (state.startDate === undefined) state.startDate = "";
  if (state.endDate === undefined) state.endDate = "";'''
)

# Update filter logic
content = content.replace(
    '''  const filteredPublished = state.selectedDate ? published.filter(i => formatArticleDateKey(i.article_published_at) === state.selectedDate) : published;
  const filteredQueued = state.selectedDate ? queued.filter(i => formatArticleDateKey(i.article_published_at) === state.selectedDate) : queued;''',
    '''  const filterByDateRange = (i) => {
    if (!state.startDate && !state.endDate) return true;
    const key = formatArticleDateKey(i.article_published_at);
    if (key === "날짜 미상") return false;
    if (state.startDate && key < state.startDate) return false;
    if (state.endDate && key > state.endDate) return false;
    return true;
  };
  const filteredPublished = published.filter(filterByDateRange);
  const filteredQueued = queued.filter(filterByDateRange);'''
)

# Update HTML for date selection
old_html = '''<strong style="font-size: 14px; color: #555;">조회 날짜:</strong>
        <select id="opsDateSelect" style="padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none;">
          <option value="">전체 보기 (All Dates)</option>
          ${allDates.map(date => `<option value="${escapeAttr(date)}" ${date === state.selectedDate ? "selected" : ""}>${escapeHtml(date)}</option>`).join("")}
        </select>'''

new_html = '''<strong style="font-size: 14px; color: #555;">조회 기간:</strong>
        <input type="date" id="opsStartDate" value="${escapeAttr(state.startDate)}" style="padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none;">
        <span style="color: #555;">~</span>
        <input type="date" id="opsEndDate" value="${escapeAttr(state.endDate)}" style="padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none;">
        <button id="opsClearDateBtn" class="button outline" style="margin-left: 8px; padding: 6px 12px; font-size: 13px;">초기화</button>'''

# Because of encoding issues in terminal outputs, the old_html string above uses clean Korean characters.
# However, the source code might be encoded such that python replaces correctly.
content = re.sub(
    r'<strong style="font-size: 14px; color: #555;">조회 [^<]+</strong>\s*<select id="opsDateSelect"[^>]*>[\s\S]*?</select>',
    new_html,
    content
)

# Update event listeners
old_event = '''document.querySelector('#opsDateSelect')?.addEventListener("change", async (e) => {
      state.selectedDate = e.target.value;
      await renderActivePage();
    });'''

new_event = '''document.querySelector('#opsStartDate')?.addEventListener("change", async (e) => {
      state.startDate = e.target.value;
      await renderActivePage();
    });
    document.querySelector('#opsEndDate')?.addEventListener("change", async (e) => {
      state.endDate = e.target.value;
      await renderActivePage();
    });
    document.querySelector('#opsClearDateBtn')?.addEventListener("click", async () => {
      state.startDate = "";
      state.endDate = "";
      await renderActivePage();
    });'''

content = content.replace(old_event, new_event)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\admin-console-runtime-v2.js", "w", encoding="utf-8") as f:
    f.write(content)

print("UI updated for Date Range.")
