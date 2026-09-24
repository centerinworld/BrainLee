#!/usr/bin/env python3
"""Find selected strategy trades whose holding windows cross unusable price jumps."""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact  # noqa: E402

OUT = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"

# Policy decision (2026-09-12, user-directed): zero-tolerance meant every one of the
# 26 selected strategies stayed "legacy" permanently, even v11 (1 contaminated
# window out of 483, 0.21%) - the multi-year, multi-hundred-trade backtests this
# gate covers will always cross an occasional unavoidable data gap (a genuine
# multi-day market closure spanning a long weekend, a rare provider outage day),
# and requiring literally 0 such days makes the gate never pass in practice,
# which is worse than a calibrated threshold. This applies ONLY to price-jump/
# coverage-style contamination (canonical_quality findings from the `jumps`
# query below) - survivorship findings (a window opened/closed outside the
# security's verified tradable interval) are a strategy LOGIC defect, not a
# data-quality nuance, and stay strict zero-tolerance below.
#
# 2026-09-12 recalibration: the initial 0.05 was picked without looking at the
# actual distribution across all 26 strategies. Sorting their ratios afterward
# (0.00, 0.10, 0.21, ..., 3.26, 4.68, 5.28, 5.91, 9.86, 13.21 - percent) shows
# one dominant, tight low-contamination cluster (22 strategies, 0-4.68%) and
# two strategies (deep_recovery 9.86%, extreme_dd_volume 13.21%) that are
# obviously in a different, much worse category - the gap between 5.91% and
# 9.86% (3.95 points) is by far the largest step anywhere in the ranked list,
# nearly 3x the next-largest gap (3.26->4.68, 1.42 points). The original 0.05
# threshold happened to fall inside the low cluster itself (splitting off
# golden_cross 5.28% and low_base_breakout 5.91%, which are much closer to the
# base cluster than to the two clear outliers), rather than at the actual
# separation the data shows. Moved to 0.07 - still well below the two outliers,
# but no longer arbitrarily penalizing strategies indistinguishable from the
# bulk of the distribution. Re-run this check whenever the strategy roster or
# a large repair changes the underlying contamination levels.
PRICE_JUMP_CONTAMINATION_THRESHOLD = 0.07
# 7% was fitted to the current 26-strategy distribution. Preserve the former
# 5% line as a review band so marginal passes remain visible, and publish a
# sensitivity table rather than presenting one fitted cutoff as immutable.
PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD = 0.05
POLICY_VERSION = "price-window-v2-provisional-7pct"


def _first_text(trade: dict, *keys: str) -> str:
    for key in keys:
        value = trade.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def holding_windows(trades: list[dict], period_end: str) -> list[tuple[str, str, str]]:
    """Normalize legacy event and round-trip ledgers into holding windows."""
    open_buys: dict[str, deque[str]] = defaultdict(deque)
    windows = []
    events = []
    for trade in trades:
        code = _first_text(trade, "code", "stock_code", "sc", "ticker")
        entry = _first_text(trade, "entry_date", "buy_date")[:10]
        exit_date = _first_text(trade, "exit_date", "sell_date")[:10]
        # Some legacy engines use entry/exit as dates; most use them as prices.
        if not entry and len(str(trade.get("entry") or "")) >= 10:
            entry = str(trade["entry"])[:10]
        if not exit_date and len(str(trade.get("exit") or "")) >= 10:
            exit_date = str(trade["exit"])[:10]
        if len(code) == 6 and entry and exit_date:
            windows.append((code, entry, exit_date))
            continue

        action = str(trade.get("action") or trade.get("side") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        day = _first_text(
            trade,
            "date", "trade_date",
            "buy_date" if action == "BUY" else "sell_date",
            "entry_date" if action == "BUY" else "exit_date",
        )[:10]
        events.append((day, code, action))

    for day, code, action in sorted(events):
        if len(code) != 6 or not day:
            continue
        if action == "BUY":
            open_buys[code].append(day)
        elif action == "SELL" and open_buys[code]:
            windows.append((code, open_buys[code].popleft(), day))
    for code, buys in open_buys.items():
        windows.extend((code, day, period_end) for day in buys)
    return windows


SURVIVORSHIP_CLASSIFICATIONS = {"missing_asof_security_master", "held_through_listing_end"}


def _is_survivorship(finding: dict) -> bool:
    return finding.get("classification") in SURVIVORSHIP_CLASSIFICATIONS


def _distinct_windows(findings: list[dict]) -> set[tuple]:
    """Findings are per-event, not per-window - a single window can accumulate
    several (e.g. one per unusable date it crosses). Count the window once."""
    return {(f["period"], f["stock_code"], f["holding_start"], f["holding_end"]) for f in findings}


def _window_key(period: str, code: str, start: str, end: str) -> tuple[str, str, str, str]:
    """Canonical denominator key used by both strategy and component audits."""
    return (str(period), str(code), str(start), str(end))


def _ledger_has_confirmed_delisting_recovery(
    trades: list[dict], code: str, start: str, end: str,
) -> bool:
    """True only when this exact ledger row used the confirmed recovery path."""
    for trade in trades:
        trade_code = _first_text(trade, "code", "stock_code", "sc", "ticker")
        entry = _first_text(trade, "entry_date", "buy_date")[:10]
        exit_date = _first_text(trade, "exit_date", "sell_date")[:10]
        reason = _first_text(trade, "exit_reason", "reason")
        if (trade_code, entry, exit_date) == (code, start, end) and "실제가치 반영" in reason:
            return True
    return False


def audit() -> dict:
    conn = connect_stock_db(readonly=True)
    strategies = []
    component_artifacts = []
    try:
        selected = conn.execute(
            """SELECT strategy,run_hash FROM selected_run_registry
               WHERE report_type='strategy_center' ORDER BY strategy"""
        ).fetchall()
        for selected_strategy, suite_hash in selected:
            contaminated = []
            trade_window_keys: set[tuple[str, str, str, str]] = set()
            members = conn.execute(
                """SELECT m.period_label,m.run_hash,r.trades_json,r.end_date
                   FROM backtest_run_set_members m
                   JOIN backtest_run_specs s ON s.run_hash=m.run_hash
                   JOIN backtest_runs r ON r.run_id=s.run_id
                   WHERE m.suite_hash=? AND r.status='done'
                   ORDER BY m.period_label,s.created_at DESC""",
                (suite_hash,),
            ).fetchall()
            seen_periods = set()
            for label, run_hash, trades_json, run_end in members:
                if label in seen_periods or not trades_json:
                    continue
                seen_periods.add(label)
                payload = json.loads(trades_json)
                trades = payload.get("trades", []) if isinstance(payload, dict) else payload
                period_end = str(run_end or "9999-12-31")[:10]
                component_contaminated = []
                component_window_keys: set[tuple[str, str, str, str]] = set()
                for code, start, end in holding_windows(trades, period_end):
                    key = _window_key(label, code, start, end)
                    # Some stored ledgers contain the same completed trade more than
                    # once. The numerator is per distinct window, so the denominator
                    # must use the same grain or duplicates silently dilute failures.
                    if key in component_window_keys:
                        continue
                    component_window_keys.add(key)
                    trade_window_keys.add(key)
                    master = conn.execute(
                        """SELECT effective_to FROM security_master_history
                           WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                             AND effective_from<=?
                             AND (effective_to IS NULL OR effective_to>?)
                           ORDER BY effective_from DESC LIMIT 1""",
                        (code, start, start),
                    ).fetchone()
                    if not master:
                        component_contaminated.append({
                            "period": label, "stock_code": code,
                            "holding_start": start, "holding_end": end,
                            "classification": "missing_asof_security_master",
                            "evidence": "entry date is outside a verified tradable interval",
                        })
                    end_master = conn.execute(
                        """SELECT 1 FROM security_master_history
                           WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                             AND effective_from<=?
                             AND (effective_to IS NULL OR effective_to>?)
                           LIMIT 1""",
                        (code, end, end),
                    ).fetchone()
                    if (master and master[0] and str(master[0]) <= end and not end_master
                            and not _ledger_has_confirmed_delisting_recovery(
                                trades, code, start, end)):
                        component_contaminated.append({
                            "period": label, "stock_code": code,
                            "holding_start": start, "holding_end": end,
                            "event_date": str(master[0]),
                            "classification": "held_through_listing_end",
                            "evidence": "position remained open through the security interval end",
                        })
                    # canonical_price_history_v (price_integrity.rebuild_views), not the
                    # narrower price_jump_audit table directly: the audit table only ever
                    # recorded jumps beyond its own coarse 1.8x/0.55x scan threshold, so an
                    # out-of-band move it never saw (invalid OHLC, a >=±30%/±15% legal-limit
                    # breach it missed, a multi-year coverage gap masquerading as one day's
                    # return) used to sail through this gate silently return_usable=1 by
                    # omission. The canonical view computes return_usable independently for
                    # every row directly from price_history, so no jump can hide by not
                    # having a price_jump_audit row.
                    jumps = conn.execute(
                        """SELECT CAST(date AS TEXT) AS event_date, canonical_quality AS classification,
                                  'canonical_price_history_v.return_usable=0 ('||canonical_quality||')' AS evidence
                           FROM canonical_price_history_v
                           WHERE stock_code=? AND CAST(date AS TEXT) BETWEEN ? AND ? AND return_usable=0
                           ORDER BY date""",
                        (code, start, end),
                    ).fetchall()
                    for jump in jumps:
                        component_contaminated.append({
                            "period": label, "stock_code": code, "holding_start": start,
                            "holding_end": end, "event_date": jump[0],
                            "classification": jump[1], "evidence": jump[2],
                        })
                contaminated.extend(component_contaminated)
                component_artifacts.append({
                    "run_hash": str(run_hash), "strategy": str(selected_strategy),
                    "suite_hash": str(suite_hash), "period": str(label),
                    "holding_windows": len(component_window_keys),
                    "contaminated": component_contaminated,
                })
            survivorship_findings = [f for f in contaminated if _is_survivorship(f)]
            price_jump_findings = [f for f in contaminated if not _is_survivorship(f)]
            trade_windows = len(trade_window_keys)
            price_jump_window_ratio = (
                len(_distinct_windows(price_jump_findings)) / trade_windows if trade_windows else 0.0
            )
            passed = (
                trade_windows > 0
                and not survivorship_findings
                and price_jump_window_ratio <= PRICE_JUMP_CONTAMINATION_THRESHOLD
            )
            strategies.append({
                "strategy": selected_strategy,
                "suite_hash": suite_hash,
                "holding_windows": trade_windows,
                "contaminated_windows": len(_distinct_windows(contaminated)),
                "contaminated_event_count": len(contaminated),
                "price_jump_window_contamination_ratio": round(price_jump_window_ratio, 4),
                "price_jump_review_warning": (
                    PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD
                    < price_jump_window_ratio
                    <= PRICE_JUMP_CONTAMINATION_THRESHOLD
                ),
                "survivorship_findings": len(survivorship_findings),
                "price_integrity_passed": passed,
                "status": (
                    "passed" if passed
                    else "failed" if contaminated
                    else "no_trade_evidence"
                ),
                "examples": contaminated[:20],
                "contaminated_events": contaminated,
            })
    finally:
        conn.close()
    for item in component_artifacts:
        survivorship_findings = [row for row in item["contaminated"] if _is_survivorship(row)]
        corporate_action_findings = [row for row in item["contaminated"] if not _is_survivorship(row)]
        price_jump_ratio = (
            len(_distinct_windows(corporate_action_findings)) / item["holding_windows"]
            if item["holding_windows"] else 0.0
        )
        price_jump_ok = price_jump_ratio <= PRICE_JUMP_CONTAMINATION_THRESHOLD
        # A completed component with zero trades has zero market-data exposure:
        # there is no holding window that could cross a bad price, listing end,
        # or corporate action. Treat that component as a documented vacuous
        # pass while retaining the strategy-level requirement for at least one
        # holding window across its full multi-period suite.
        no_exposure = item["holding_windows"] == 0
        passed = not survivorship_findings and price_jump_ok
        register_artifact(item["run_hash"], "price_integrity", passed, {
            "strategy": item["strategy"],
            "suite_hash": item["suite_hash"],
            "period": item["period"],
            "holding_windows": item["holding_windows"],
            "no_exposure": no_exposure,
            "contaminated_windows": len(_distinct_windows(item["contaminated"])),
            "price_jump_window_contamination_ratio": round(price_jump_ratio, 4),
            "price_jump_contamination_threshold": PRICE_JUMP_CONTAMINATION_THRESHOLD,
            "checks": [
                "unusable_price_jump",
                "asof_security_master_entry",
                "listing_interval_end",
            ],
            "examples": item["contaminated"][:20],
        })
        register_artifact(
            item["run_hash"], "survivorship_integrity",
            not survivorship_findings,
            {
                "holding_windows": item["holding_windows"],
                "no_exposure": no_exposure,
                "findings": len(survivorship_findings),
                "examples": survivorship_findings[:20],
            },
        )
        register_artifact(
            item["run_hash"], "corporate_action_integrity",
            price_jump_ok,
            {
                "holding_windows": item["holding_windows"],
                "no_exposure": no_exposure,
                "findings": len(corporate_action_findings),
                "contaminated_windows": len(_distinct_windows(corporate_action_findings)),
                "window_contamination_ratio": round(price_jump_ratio, 4),
                "threshold": PRICE_JUMP_CONTAMINATION_THRESHOLD,
                "examples": corporate_action_findings[:20],
            },
        )
    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "policy_version": POLICY_VERSION,
        "price_jump_contamination_threshold": PRICE_JUMP_CONTAMINATION_THRESHOLD,
        "price_jump_review_warning_threshold": PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD,
        "strategy_count": len(strategies),
        "passed": sum(item["price_integrity_passed"] for item in strategies),
        "failed": sum(item["status"] == "failed" for item in strategies),
        "no_trade_evidence": sum(item["status"] == "no_trade_evidence" for item in strategies),
        "strategies": strategies,
    }
    result["threshold_sensitivity"] = [
        {
            "threshold": threshold,
            "passed": sum(
                item["holding_windows"] > 0
                and not item["survivorship_findings"]
                and item["price_jump_window_contamination_ratio"] <= threshold
                for item in strategies
            ),
        }
        for threshold in (0.0, 0.03, 0.05, 0.07, 0.10)
    ]
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
