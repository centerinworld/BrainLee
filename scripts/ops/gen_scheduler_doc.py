#!/usr/bin/env python3
"""scheduler.py 의 루프 등록부에서 docs/SCHEDULER_JOBS.md 를 생성한다 (CLAUDE.md 섹션 4의 정본).
사용: python3 scripts/ops/gen_scheduler_doc.py   (스케줄러 잡을 추가/변경한 뒤 재실행)"""
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
src = (ROOT / "scheduler.py").read_text(encoding="utf-8")
first = src.index('("월간업데이트"')
start = src.rfind("[", 0, first)
block = src[start: src.index("\n        ]", first)]
rows = []
for m in re.finditer(r'\("([^"]+)",\s*self\.(\w+)\),?\s*(?:#\s*(.*))?', block):
    name, fn, desc = m.group(1), m.group(2), (m.group(3) or "").strip()
    disabled = desc.startswith("⛔")
    rows.append((name, fn, desc.lstrip("★⛔ ").strip(), disabled))
out = [
    "# 스케줄러 잡 목록 (자동 생성)",
    "",
    f"> `scripts/ops/gen_scheduler_doc.py` 가 `scheduler.py` 루프 등록부에서 생성 — {date.today()} 기준 {len(rows)}개. **수동 편집 금지**(잡 추가/변경 후 스크립트 재실행).",
    "> 시각/주기 설명은 등록 라인의 주석에서 가져오므로 주석이 없는 잡은 설명이 비어 있다. 정확한 실행 조건은 `scheduler.py`의 `_loop_*`/`_job_*` 참조.",
    "",
    "| 잡 | 상태 | 설명 |",
    "|----|------|------|",
]
for name, fn, desc, dis in rows:
    out.append(f"| `{name}` (`{fn}`) | {'⛔ 비활성' if dis else '활성'} | {desc.replace('|', '/')} |")
(ROOT / "docs" / "SCHEDULER_JOBS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"{len(rows)} jobs -> docs/SCHEDULER_JOBS.md")
