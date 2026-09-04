#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_governance import classify_strategy


def periods(status: str, *, complete_risk: bool) -> dict:
    return {
        str(index): {
            "total_return_pct": 20.0,
            "verification_status": status,
            "mdd": -10.0 if complete_risk else None,
            "sharpe": 1.2 if complete_risk else None,
            "pl_ratio": 2.0 if complete_risk else None,
        }
        for index in range(6)
    }


pit_only = classify_strategy(periods("point_in_time_verified", complete_risk=True))
assert not pit_only["live_ready"]
assert pit_only["tier"] != "live_eligible"

missing_risk = classify_strategy(periods("forward_validated", complete_risk=False))
assert not missing_risk["live_ready"]
assert missing_risk["tier"] != "live_eligible"

qualified = classify_strategy(periods("forward_validated", complete_risk=True))
assert qualified["live_ready"]
assert qualified["tier"] == "live_eligible"

print("strategy governance live gate: PASS")
