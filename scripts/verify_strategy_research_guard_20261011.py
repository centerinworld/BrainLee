#!/usr/bin/env python
"""P0 검증 — `/api/backtest/strategy-research/summary` 의 fail-closed 대조를 **라이브**에서 재현한다.

읽기 전용 보증
--------------
- `routes.backtest` 를 import 하면 모듈 상단에서 `init_backtest_db()` 가 돌고, PostgreSQL
  에서 그것은 `CREATE OR REPLACE FUNCTION/TRIGGER` DDL 을 실행한다. 검증 목적상 그
  부작용을 **차단**한다(`_ensure_backtest_pg_triggers` 를 no-op 으로 치환) — 이 스크립트는
  스키마를 건드리지 않는다.
- 이 스크립트가 실행하는 SQL 은 전부 SELECT 다(라우트 함수 + guard 모듈).
- uvicorn 재기동 없이 라우트 함수를 직접 호출하므로, 서빙 중인 프로세스는 계속 옛 코드다.

사용
----
    cd runtime && ./venv/bin/python scripts/verify_strategy_research_guard_20261011.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 1) import 부작용(멱등 DDL) 차단 — 반드시 routes.backtest import 전에.
import backtest_common  # noqa: E402

backtest_common._ensure_backtest_pg_triggers = lambda: None  # type: ignore[attr-defined]

import routes.backtest as rb  # noqa: E402

from db_compat import connect_primary_db  # noqa: E402


FINGERPRINT_SQL = (
    ("price_history", "SELECT count(*), max(date) FROM price_history"),
    ("data_fix_log", "SELECT count(*), max(fixed_at) FROM data_fix_log"),
    ("backtest_run_specs", "SELECT count(*), max(created_at) FROM backtest_run_specs"),
)


def fingerprint() -> dict:
    conn = connect_primary_db(timeout=30)
    out = {}
    cur = conn.cursor()
    for name, sql in FINGERPRINT_SQL:
        try:
            row = cur.execute(sql).fetchone()
            out[name] = [str(v) for v in tuple(row)]
        except Exception as exc:  # noqa: BLE001
            out[name] = f"ERROR {type(exc).__name__}: {exc}"
    conn.close()
    return out


def main() -> int:
    before = fingerprint()
    data = rb.get_strategy_research_summary()
    after = fingerprint()

    ev = data.get("strategy_research_evidence") or {}
    report = {
        "strategy_research_status": data.get("strategy_research_status"),
        "strategy_research_reasons": data.get("strategy_research_reasons"),
        "strategy_research_notice": data.get("strategy_research_notice"),
        "current_rankings_is_null": data.get("current_rankings") is None,
        "evaluation_is_null": data.get("evaluation") is None,
        "dataset_table_missing": ev.get("dataset_table_missing"),
        "dataset_table_not_canonical": ev.get("dataset_table_not_canonical"),
        "counts_match": ev.get("counts_match"),
        "count_mismatches": ev.get("count_mismatches"),
        "snapshot_date_match": ev.get("snapshot_date_match"),
        "generated_at_after_snapshot": ev.get("generated_at_after_snapshot"),
        "generated_at_kst": ev.get("generated_at_kst"),
        "checks": ev.get("checks"),
        "declared_table": ev.get("declared_table"),
        "canonical_table": ev.get("canonical_table"),
        "canonical": {k: (str(v) if not isinstance(v, (int, float, type(None))) else v)
                      for k, v in (ev.get("canonical") or {}).items()},
        "pit": ev.get("pit"),
        "run_spec": ev.get("run_spec"),
        "static_summary": {k: v for k, v in (ev.get("static_summary") or {}).items()
                           if k != "sha256"},
        "static_summary_sha256": (ev.get("static_summary") or {}).get("sha256"),
        "db_fingerprint_unchanged": before == after,
        "db_fingerprint": before,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    out_dir = ROOT / ".verification"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "strategy_research_guard_20261011.json").write_text(text, encoding="utf-8")

    ok = (
        data.get("strategy_research_status") == "not_usable"
        and data.get("current_rankings") is None
        and data.get("evaluation") is None
        and ev.get("generated_at_after_snapshot") is True
        and before == after
    )
    print("\nRESULT:", "OK (fail-closed + no DB change)" if ok else "PROBLEM")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
