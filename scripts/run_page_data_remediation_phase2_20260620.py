#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_page_data_remediation_20260620 import (  # noqa: E402
    LOG_DIR,
    run_kiwoom_credit_foreign,
    run_kiwoom_universe,
    run_market_radar_refresh,
    run_step,
    PY,
)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results = [
        run_kiwoom_universe(),
        run_kiwoom_credit_foreign(),
        run_market_radar_refresh(),
        run_step("final_page_data_audit_phase2", [str(PY), "scripts/audit_all_page_data_quality.py"]),
    ]
    path = LOG_DIR / "summary_phase2.json"
    path.write_text(json.dumps({"generated_at": now(), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(r.get("returncode") == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
