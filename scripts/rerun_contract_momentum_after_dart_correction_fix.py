#!/usr/bin/env python3
"""Recompute the contract_momentum strategy_center suite after the 2026-09-04/05
order_contracts/dart_contracts correction-matching fix + SQLite/Postgres divergence
merge (see docs/codex_handoff_order_contracts_proxy_20260725.md, 2026-09-05 entry).

Reuses the exact re-run/registration mechanism from
scripts/rerun_selected_after_price_repair.py (saved params per period, price
integrity check, atomic suite registration) but scoped to contract_momentum
only and labeled with the real reason instead of "price_basis_repair".
"""
from __future__ import annotations

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_run_set, select_run  # noqa: E402
from scripts.rerun_selected_after_price_repair import (  # noqa: E402
    _price_integrity,
    _selected_specs,
    _run_one,
)

OUT = ROOT / "research_outputs" / "contract_momentum_rerun_after_dart_fix_latest.json"

STRATEGY = "contract_momentum"
SELECTED_BY = "dart_contracts_correction_fix"
NOTE = (
    "Recomputed after order_contracts/dart_contracts correction-matching fix "
    "(EXCLUDE_KEYWORDS no longer drops '정정' disclosures) + merge of 139 rows "
    "stranded in a local-SQLite-only copy by an audit script bug (2026-09-05)"
)


def run() -> dict:
    selected = _selected_specs({STRATEGY})
    specs = selected.get(STRATEGY)
    if not specs:
        raise RuntimeError(f"no selected suite found for {STRATEGY}")
    result = {"started_at": datetime.now().isoformat(timespec="seconds"),
              "strategy": STRATEGY, "old_suite": specs[0]["old_suite"], "runs": []}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_run_one, STRATEGY, spec) for spec in specs]
        for future in as_completed(futures):
            result["runs"].append(future.result())
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(result["runs"]) != 6:
        raise RuntimeError(f"expected 6 periods, got {len(result['runs'])}")
    members = {row["label"]: row["run_hash"] for row in result["runs"]}
    suite = register_run_set(STRATEGY, "strategy_center", members)
    selected_suite = select_run(
        STRATEGY, "strategy_center", suite["suite_hash"],
        selected_by=SELECTED_BY, note=NOTE,
    )
    result.update({"status": "selected", "new_suite": suite["suite_hash"],
                   "verification": selected_suite.get("verification")})
    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
