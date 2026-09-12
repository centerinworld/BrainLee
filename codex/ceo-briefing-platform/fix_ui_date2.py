with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\admin-console-runtime-v2.js", "r", encoding="utf-8") as f:
    content = f.read()

import re

# Update HTML for date selection to add a Search button and remove values from inputs so we can read them on click
new_html = '''<strong style="font-size: 14px; color: #555;">조회 기간:</strong>
        <input type="date" id="opsStartDate" value="${escapeAttr(state.startDate)}" style="padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none;">
        <span style="color: #555;">~</span>
        <input type="date" id="opsEndDate" value="${escapeAttr(state.endDate)}" style="padding: 8px; border-radius: 4px; border: 1px solid #ddd; outline: none;">
        <button id="opsSearchDateBtn" class="button" style="margin-left: 8px; padding: 6px 12px; font-size: 13px;">조회</button>
        <button id="opsClearDateBtn" class="button outline" style="margin-left: 8px; padding: 6px 12px; font-size: 13px;">초기화</button>'''

content = re.sub(
    r'<strong style="font-size: 14px; color: #555;">조회 기간:</strong>[\s\S]*?<button id="opsClearDateBtn"[^>]*>초기화</button>',
    new_html,
    content
)

# Update event listeners
old_event = '''document.querySelector('#opsStartDate')?.addEventListener("change", async (e) => {
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

new_event = '''document.querySelector('#opsSearchDateBtn')?.addEventListener("click", async () => {
      const start = document.querySelector('#opsStartDate')?.value || "";
      const end = document.querySelector('#opsEndDate')?.value || "";
      state.startDate = start;
      state.endDate = end;
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

print("UI updated for Date Range with Search button.")
