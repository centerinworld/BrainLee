#!/usr/bin/env python3
"""CLAUDE.md 섹션 12(변경 이력)를 최근 N개 항목만 남기고 나머지를 docs/CLAUDE_CHANGELOG_ARCHIVE.md 로 옮긴다 (2026-09-27, 토큰 최적화).

CLAUDE.md 는 매 세션 전체가 컨텍스트에 로드된다(2026-09-27 실측 147KB ≈ 37k 토큰 중 변경 이력이 74KB). 옮기기만 하고 지우지 않는다(아카이브 맨 아래에 그대로 추가).
  python3 scripts/ops/trim_claude_changelog.py [--keep 12] [--dry-run]
항목 = 섹션 12 안에서 날짜(2026-…)로 시작하는(앞에 `- `·`**` 허용) 150자 이상의 줄. 그 외 ### 제목·색인 줄은 첫 3개 소제목까지만 유지한다.
"""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE, ARCHIVE = ROOT / "CLAUDE.md", ROOT / "docs" / "CLAUDE_CHANGELOG_ARCHIVE.md"
ENTRY = re.compile(r"^(?:- )?(?:\*\*)?20\d\d-\d\d-\d\d")


def main() -> int:
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 12
    dry = "--dry-run" in sys.argv
    lines = CLAUDE.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("## 12. 변경 이력"))
    body = lines[start + 1:]
    heads = [i for i, l in enumerate(body) if l.startswith("### ")]
    if len(heads) < 3:
        print("소제목이 3개 미만 — 이미 정리된 상태")
        return 0
    # 첫 3개 소제목 블록(작은 것들)은 유지: 4번째 소제목 직전까지
    cut = heads[3] if len(heads) > 3 else len(body)
    fixed, rest = body[:cut], body[cut:]
    entries = [i for i, l in enumerate(rest) if ENTRY.match(l) and len(l) >= 150]
    if len(entries) <= keep:
        print(f"항목 {len(entries)}개 ≤ keep {keep} — 옮길 것 없음")
        return 0
    boundary = entries[-keep]                      # 이 줄부터 끝까지 유지
    moved, kept = rest[:boundary], rest[boundary:]
    moved_text = "\n".join(moved).strip("\n")
    pointer = (f"> 📦 {date.today()}: 이 아래 최근 {keep}개 항목만 유지 — 이전 항목({len(entries) - keep}개 + 관련 소제목/색인)은 "
               "[docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래 「CLAUDE.md에서 이동」 절. (`scripts/ops/trim_claude_changelog.py`)")
    print(f"{len(entries)}개 중 {len(entries) - keep}개 이동, CLAUDE.md {len(moved_text.encode()) / 1024:.0f}KB 감소")
    if dry:
        return 0
    with ARCHIVE.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## CLAUDE.md에서 이동 ({date.today()})\n\n{moved_text}\n")
    CLAUDE.write_text("\n".join(lines[:start + 1] + fixed + ["", pointer, ""] + kept), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
