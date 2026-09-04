#!/usr/bin/env python3
"""Sequential, leakage-safe assessment of prospectively captured signals."""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402

OUT = ROOT / "research_outputs" / "forward_validation_latest.json"
TARGET_HORIZON = 20
MIN_SIGNALS = 30
MIN_CALENDAR_DAYS = 90
ALLOWED_STRATEGIES = ("v_gc", "v_contract_momentum")


def wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator, (centre + margin) / denominator


def audit() -> dict:
    conn = connect_stock_db()
    rows = conn.execute(
        """SELECT s.strategy_id,s.signal_date,s.stock_code,s.available_at,
                  s.signal_payload_json,o.return_pct,o.max_gain_pct,o.max_loss_pct,o.status
           FROM live_signal_registry s JOIN live_signal_outcomes o ON o.signal_id=s.signal_id
           WHERE s.action='BUY_CANDIDATE' AND o.horizon_days=?
             AND s.strategy_id IN (?,?)
           ORDER BY s.strategy_id,s.available_at,s.signal_date""",
        (TARGET_HORIZON, *ALLOWED_STRATEGIES),
    ).fetchall()
    # Old collectors emitted the same open holding every day. Treat a stock's
    # unchanged source entry as one episode so correlated duplicates cannot
    # inflate sample size or confidence intervals.
    episodes: dict[tuple[str, str, str], tuple] = {}
    for row in rows:
        try:
            payload = json.loads(row[4] or "{}")
        except (TypeError, ValueError):
            payload = {}
        source_entry = str(payload.get("source_entry_date") or row[1])[:10]
        episodes.setdefault((str(row[0]), str(row[2]), source_entry), row)
    grouped: dict[str, list] = {}
    for row in episodes.values():
        grouped.setdefault(str(row[0]), []).append(row)
    strategies = []
    for strategy in sorted(grouped):
        episode_rows = grouped[strategy]
        values = [row for row in episode_rows if str(row[8]) == "complete"]
        pending_count = sum(str(row[8]) == "pending" for row in episode_rows)
        returns = [float(row[5]) for row in values]
        wins = sum(value > 0 for value in returns)
        low, high = wilson_interval(wins, len(returns))
        first_date = min((str(row[1])[:10] for row in episode_rows), default=None)
        elapsed = (date.today() - date.fromisoformat(first_date)).days if first_date else 0
        enough = len(returns) >= MIN_SIGNALS and elapsed >= MIN_CALENDAR_DAYS
        passed = bool(
            enough and statistics.mean(returns) > 0 and low is not None and low >= 0.45
            and min(float(row[7]) for row in values) >= -25
        )
        early_fail = bool(
            len(returns) >= 15 and high is not None and high < 0.5
        )
        strategies.append({
            "strategy": strategy, "completed": len(returns), "pending": pending_count,
            "deduplicated_episodes": len(episode_rows),
            "elapsed_calendar_days": elapsed,
            "mean_return_pct": round(statistics.mean(returns), 3) if returns else None,
            "median_return_pct": round(statistics.median(returns), 3) if returns else None,
            "win_rate_pct": round(wins / len(returns) * 100, 2) if returns else None,
            "win_rate_ci95_pct": [round(low * 100, 2), round(high * 100, 2)] if low is not None else None,
            "worst_max_loss_pct": round(min(float(row[7]) for row in values), 3) if values else None,
            "enough_evidence": enough, "passed": passed, "early_fail": early_fail,
            "status": "passed" if passed else "early_fail" if early_fail else "collecting",
        })
    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "policy": {"horizon_days": TARGET_HORIZON, "min_signals": MIN_SIGNALS,
                   "min_calendar_days": MIN_CALENDAR_DAYS, "no_automatic_live_approval": True},
        "strategies": strategies,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
