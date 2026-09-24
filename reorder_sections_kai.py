import re
from pathlib import Path

kai_js_path = Path("/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/frontend/kai.js")
text = kai_js_path.read_text(encoding="utf-8")

# Let's inspect the entire loadClaudeCodexSessionsTable implementation
idx_load = text.find("async function loadClaudeCodexSessionsTable()")
idx_next = text.find("function highlightSelectedSession", idx_load)

# We want to rewrite loadClaudeCodexSessionsTable so that:
# 1. Quota Cards (with 🔴 사용불가, 🟡 한도 상당 소진, 🟢 한도 많음 신호등)
# 2. #1~#4 Session Table placed directly beneath the traffic lights!
# 3. 🎯 8 AGI Strategic Goals Table & Modal
# 4. 🚀 3-Stage Pipeline Dispatch Console
# 5. Live Stepper ONLY IF actively running (is_running = ['PENDING', 'RUNNING_PRIMARY', 'RUNNING_FALLBACK', 'RUNNING_FRONTIER_REVIEW'].includes(latestTask.status))
# 6. 📋 Pipeline Execution History Table
