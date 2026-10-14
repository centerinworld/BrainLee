#!/usr/bin/env python3
"""AGI 신설 스키마 migration CLI (migration 0001_research_core).

**기본값은 dry-run 이며 아무것도 쓰지 않는다.** 실제 적용은 `--apply` 로만 한다(계약
AGI-01 §11-2: 적용은 별도 실행 계약 = 사람 승인 단위).

    ./venv/bin/python scripts/migrate_agi_research_core.py                     # up 계획만 출력
    ./venv/bin/python scripts/migrate_agi_research_core.py --direction down    # rollback 계획만 출력
    ./venv/bin/python scripts/migrate_agi_research_core.py --apply             # 실제 up
    ./venv/bin/python scripts/migrate_agi_research_core.py --apply --direction down
    ./venv/bin/python scripts/migrate_agi_research_core.py --schema agi_tmp --apply

argparse 를 DB/file 작업보다 먼저 처리하므로 `--help` 는 아무것도 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AGI 신설 스키마 migration (기본 dry-run, 적용은 --apply)"
    )
    parser.add_argument("--direction", choices=("up", "down"), default="up")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 실행한다(미지정 시 dry-run — 출력만 하고 쓰지 않는다)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="적용 스키마(기본 public). 검증용 임시 스키마 격리에 사용한다.",
    )
    parser.add_argument(
        "--show-sql",
        action="store_true",
        help="dry-run 출력에 실행될 SQL 전문을 포함한다",
    )
    parser.add_argument("--applied-by", default=None, help="원장에 기록할 적용자")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from agi.db import connect_primary
    from agi.migrations import MIGRATIONS
    from agi.migrations.runner import MigrationRunner

    results = []
    conn = connect_primary(timeout=60)
    try:
        runner = MigrationRunner(conn, schema=args.schema, applied_by=args.applied_by)
        # down 은 적용 역순이다(2026-10-13, m0002 추가) — 앞선 migration 을 먼저 내리면
        # 그 뒤에 추가된 migration 이 의존하는 컬럼/테이블이 먼저 없어질 수 있다.
        ordered = MIGRATIONS if args.direction == "up" else tuple(reversed(MIGRATIONS))
        for migration in ordered:
            if args.direction == "up":
                result = runner.up(migration, dry_run=not args.apply)
            else:
                result = runner.down(migration, dry_run=not args.apply)
            payload = result.as_dict()
            payload["sql"] = [statement.sql for statement in result.statements]
            if result.statements and result.statements[-1].params:
                payload["last_statement_params"] = [str(p) for p in result.statements[-1].params]
            results.append(payload)
    finally:
        conn.close()

    for payload in results:
        if not args.show_sql:
            payload.pop("sql", None)
    print(
        json.dumps(
            {
                "direction": args.direction,
                "mode": "apply" if args.apply else "dry-run",
                "schema_target": args.schema or "public",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
