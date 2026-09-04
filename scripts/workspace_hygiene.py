#!/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python
"""
workspace_hygiene.py

목적:
1) .claude/worktrees 용량 점검 및 오래된 워크트리 정리
2) report_files 테이블에서 "실파일이 없는" 레코드 정리(옵션)

기본은 dry-run이며, 실제 삭제는 --apply 필요.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path
import sys

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
sys.path.insert(0, str(ROOT))
from report_store import REPORTS_DB_PATH

DB_PATH = REPORTS_DB_PATH
CLAUDE_DIR = ROOT / ".claude"
WORKTREES_DIR = CLAUDE_DIR / "worktrees"


def _human_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(num)
    for u in units:
        if n < 1024 or u == units[-1]:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{num}B"


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def claude_worktrees_cleanup(days: int, apply: bool, delete_all: bool) -> None:
    print(f"[claude] base={CLAUDE_DIR}")
    if not CLAUDE_DIR.exists():
        print("[claude] 없음")
        return

    claude_size = _dir_size(CLAUDE_DIR)
    worktrees_size = _dir_size(WORKTREES_DIR)
    print(f"[claude] total={_human_size(claude_size)}, worktrees={_human_size(worktrees_size)}")

    if delete_all:
        if apply:
            shutil.rmtree(CLAUDE_DIR, ignore_errors=True)
            print("[claude] .claude 전체 삭제 완료")
        else:
            print("[claude] dry-run: --delete-claude-all 대상")
        return

    if not WORKTREES_DIR.exists():
        print("[claude] worktrees 디렉토리 없음")
        return

    now = time.time()
    cutoff = now - days * 86400
    targets: list[Path] = []
    for child in WORKTREES_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            targets.append(child)

    if not targets:
        print(f"[claude] {days}일 초과 worktree 없음")
        return

    reclaim = sum(_dir_size(p) for p in targets)
    print(f"[claude] 정리 대상 {len(targets)}개, 예상 회수={_human_size(reclaim)}")
    for p in sorted(targets):
        print(f"  - {p}")
        if apply:
            shutil.rmtree(p, ignore_errors=True)

    if apply:
        print("[claude] worktrees 정리 완료")
    else:
        print("[claude] dry-run: 실제 삭제하려면 --apply")


def cleanup_missing_report_rows(apply: bool) -> None:
    print("[reports] report_files 누락 파일 레코드 점검")
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT id, file_path FROM report_files WHERE file_path IS NOT NULL").fetchall()
        missing_ids = [rid for rid, fp in rows if not Path(fp).exists()]
        print(f"[reports] total={len(rows)}, missing={len(missing_ids)}")
        if missing_ids and apply:
            conn.executemany("DELETE FROM report_files WHERE id=?", [(i,) for i in missing_ids])
            conn.commit()
            print(f"[reports] 삭제 완료: {len(missing_ids)}건")
        elif missing_ids:
            print("[reports] dry-run: 실제 삭제하려면 --apply")
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본 dry-run)")
    ap.add_argument("--claude-days", type=int, default=14, help=".claude/worktrees 정리 기준 일수")
    ap.add_argument("--delete-claude-all", action="store_true", help=".claude 전체 삭제")
    ap.add_argument("--clean-missing-report-rows", action="store_true", help="실파일 없는 report_files 레코드 삭제")
    args = ap.parse_args()

    claude_worktrees_cleanup(days=args.claude_days, apply=args.apply, delete_all=args.delete_claude_all)
    if args.clean_missing_report_rows:
        cleanup_missing_report_rows(apply=args.apply)


if __name__ == "__main__":
    main()
