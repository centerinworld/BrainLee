#!/usr/bin/env python3
"""
Build a governance-aware meta strategy plan.

This intentionally does not promote any strategy to live capital unless the
machine adoption review says it passed all statistical gates.  The current
system has many strategies, but the latest adoption review still reports zero
fully adopted strategies, so this script separates:

1. live_weights: real-capital allocation, fail-closed to cash
2. shadow_weights: paper/shadow candidates for the next validation cycle
3. improvement_queue: concrete next work per strategy
"""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
import signal_engine


OUT_PATH = ROOT / "scratch" / "meta_strategy_plan_latest.json"
LEGACY_OUT_PATH = ROOT / "scratch" / "meta_strategy_plan_2026-05-19.json"
RESEARCH = ROOT / "research_outputs"
ADOPTION_CSV = RESEARCH / "strategy_adoption_review_20260925.csv"
PRICE_AUDIT_JSON = RESEARCH / "selected_strategy_price_integrity_latest.json"
DATA_AUDIT_JSON = RESEARCH / "selected_strategy_data_availability_latest.json"


@dataclass
class StrategyPlanRow:
    strategy: str
    selected_suite_hash: str | None
    adoption_passed: bool
    curve_ok: bool
    oos_excess_pct: float | None
    dsr_n38: float | None
    t12m_expectancy_pct: float | None
    mdd_pct: float | None
    trades_per_year: float | None
    score: float
    tier: str
    reason: str


def _as_float(value: Any) -> float | None:
    if value in (None, "", "nan", "NaN"):
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(val) else val


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_adoption_rows() -> dict[str, dict]:
    if not ADOPTION_CSV.exists():
        return {}
    with ADOPTION_CSV.open("r", encoding="utf-8") as f:
        return {row["strategy"]: row for row in csv.DictReader(f)}


def _selected_suites() -> dict[str, str]:
    conn = connect_primary_db(timeout=30)
    try:
        conn.row_factory = None
        rows = conn.execute(
            """
            SELECT strategy, run_hash
            FROM selected_run_registry
            WHERE report_type='strategy_center'
            ORDER BY strategy
            """
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}
    finally:
        conn.close()


def _market_stage() -> tuple[int, dict]:
    try:
        snap = signal_engine.get_market_regime_snapshot()
    except Exception as exc:
        return 3, {"error": f"{type(exc).__name__}: {exc}"}
    stages: list[int] = []
    for market in snap.get("markets", []):
        try:
            stages.append(int(market.get("stage", 3)))
        except Exception:
            continue
    return (max(stages) if stages else 3), snap


def _score(row: dict) -> float:
    """Score only for shadow ranking, not live adoption."""
    oos = _as_float(row.get("oos_excess_pct")) or 0.0
    dsr = _as_float(row.get("dsr_n38")) or 0.0
    ev = _as_float(row.get("t12m_expectancy_pct")) or 0.0
    mdd = abs(_as_float(row.get("mdd_pct")) or 0.0)
    turnover = _as_float(row.get("trades_per_year")) or 0.0
    curve_bonus = 8.0 if _as_bool(row.get("curve_ok")) else -12.0
    # Penalize very high turnover because Korean small-cap costs/capacity hurt live transfer.
    turnover_penalty = max(0.0, turnover - 80.0) * 0.08
    return round(oos * 0.55 + dsr * 45.0 + ev * 0.25 - mdd * 0.08 + curve_bonus - turnover_penalty, 2)


def _tier_and_reason(row: dict, price_ok: bool, data_ok: bool) -> tuple[str, str]:
    if _as_bool(row.get("adopt")) and price_ok and data_ok:
        return "live_candidate", "adoption/price/data gates passed"
    if not price_ok or not data_ok:
        missing = []
        if not price_ok:
            missing.append("price_integrity")
        if not data_ok:
            missing.append("data_availability")
        return "blocked_by_audit", ", ".join(missing)
    if _as_bool(row.get("c1_oos_excess")) and _as_bool(row.get("c4_t12m_ev")) and _as_bool(row.get("curve_ok")):
        return "shadow_priority", "OOS alpha and recent expectancy positive, but DSR/PBO adoption gates not passed"
    if _as_bool(row.get("c4_t12m_ev")) and _as_bool(row.get("curve_ok")):
        return "paper_observe", "recent expectancy positive, but OOS alpha or statistical gates are insufficient"
    return "research_only", "insufficient OOS/statistical evidence"


def _normalize_weights(rows: list[StrategyPlanRow], *, limit: int) -> dict[str, float]:
    picked = [r for r in rows if r.tier in {"live_candidate", "shadow_priority", "paper_observe"} and r.score > 0]
    picked = sorted(picked, key=lambda r: r.score, reverse=True)[:limit]
    total = sum(r.score for r in picked)
    if total <= 0:
        return {}
    return {r.strategy: round(r.score / total, 4) for r in picked}


def build_plan() -> dict:
    adoption = _load_adoption_rows()
    selected = _selected_suites()
    price_audit = _load_json(PRICE_AUDIT_JSON)
    data_audit = _load_json(DATA_AUDIT_JSON)
    price_status = {
        str(row.get("strategy")): bool(row.get("price_integrity_passed"))
        for row in price_audit.get("strategies", [])
    }
    data_status = {
        strategy: (summary.get("failed") == 0)
        for strategy, summary in (data_audit.get("strategies") or {}).items()
    }

    rows: list[StrategyPlanRow] = []
    for strategy in sorted(set(selected) | set(adoption)):
        row = adoption.get(strategy, {"strategy": strategy})
        price_ok = price_status.get(strategy, bool(price_audit.get("passed") and strategy in selected))
        data_ok = data_status.get(strategy, bool(data_audit.get("verdict") == "PASS" and strategy in selected))
        tier, reason = _tier_and_reason(row, price_ok, data_ok)
        rows.append(
            StrategyPlanRow(
                strategy=strategy,
                selected_suite_hash=selected.get(strategy),
                adoption_passed=_as_bool(row.get("adopt")),
                curve_ok=_as_bool(row.get("curve_ok")),
                oos_excess_pct=_as_float(row.get("oos_excess_pct")),
                dsr_n38=_as_float(row.get("dsr_n38")),
                t12m_expectancy_pct=_as_float(row.get("t12m_expectancy_pct")),
                mdd_pct=_as_float(row.get("mdd_pct")),
                trades_per_year=_as_float(row.get("trades_per_year")),
                score=_score(row),
                tier=tier,
                reason=reason,
            )
        )

    rows_sorted = sorted(rows, key=lambda r: (r.tier != "live_candidate", -r.score, r.strategy))
    live_candidates = [r for r in rows_sorted if r.tier == "live_candidate"]
    stage, regime_snapshot = _market_stage()

    live_weights = _normalize_weights(live_candidates, limit=5)
    if not live_weights:
        live_weights = {"cash": 1.0}
    shadow_weights = _normalize_weights(rows_sorted, limit=6)

    priority = [r for r in rows_sorted if r.tier == "shadow_priority"][:6]
    improvement_queue = [
        {
            "strategy": r.strategy,
            "next_action": (
                "run rolling walk-forward plus parameter sensitivity; keep in shadow only"
                if r.strategy in {"golden_cross", "contract_momentum"}
                else "verify curve fidelity, then re-run adoption review"
                if not r.curve_ok
                else "monitor as paper candidate; do not promote until DSR/PBO gates improve"
            ),
            "why": r.reason,
            "score": r.score,
        }
        for r in priority
    ]
    improvement_queue.extend(
        [
            {
                "strategy": "minervini",
                "next_action": "replace KOSPI-relative 6m RS approximation with PIT-universe weighted RS percentile and weekly VCP evidence rows",
                "why": "current Trend/SEPA/VCP implementation exists, but RS/VCP evidence is still approximate",
                "score": None,
            },
            {
                "strategy": "meta_labeling",
                "next_action": "train entry/skip labels on existing strategy signals using regime, RS, earnings surprise/revision, liquidity and drawdown context",
                "why": "all adoption gates failed; improving trade selection is safer than adding another raw buy strategy",
                "score": None,
            },
        ]
    )

    counts: dict[str, int] = {}
    for r in rows_sorted:
        counts[r.tier] = counts.get(r.tier, 0) + 1

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "governance_fail_closed",
        "capital_policy": (
            "No live allocation unless a selected strategy passes adoption, price integrity, and data availability gates."
        ),
        "regime_stage_used": stage,
        "regime_snapshot_compact": {
            "stage": stage,
            "markets": [
                {
                    "market": item.get("market") or item.get("name"),
                    "stage": item.get("stage"),
                    "label": item.get("label") or item.get("regime"),
                }
                for item in (regime_snapshot.get("markets") or [])[:4]
            ],
            "error": regime_snapshot.get("error"),
        },
        "audit_inputs": {
            "adoption_csv": str(ADOPTION_CSV),
            "price_audit_checked_at": price_audit.get("checked_at"),
            "price_audit_passed": price_audit.get("passed"),
            "data_audit_checked_at": data_audit.get("checked_at"),
            "data_audit_verdict": data_audit.get("verdict"),
            "selected_strategy_count": len(selected),
        },
        "tier_counts": counts,
        "live_weights": live_weights,
        "shadow_weights": shadow_weights,
        "top_ranked_strategies": [asdict(r) for r in rows_sorted[:12]],
        "improvement_queue": improvement_queue,
        "risk_rules": {
            "auto_trading_allowed": False,
            "live_cash_floor": 1.0 if live_weights == {"cash": 1.0} else 0.2,
            "shadow_min_days": 60,
            "shadow_min_closed_trades": 20,
            "promotion_requires": ["adopt=True", "price_integrity=PASS", "data_availability=PASS", "paper/live fixture match"],
        },
    }


def main() -> None:
    plan = build_plan()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan, ensure_ascii=False, indent=2)
    OUT_PATH.write_text(payload, encoding="utf-8")
    LEGACY_OUT_PATH.write_text(payload, encoding="utf-8")
    print(str(OUT_PATH))
    print(
        json.dumps(
            {
                "mode": plan["mode"],
                "live_weights": plan["live_weights"],
                "shadow_weights": plan["shadow_weights"],
                "tier_counts": plan["tier_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
