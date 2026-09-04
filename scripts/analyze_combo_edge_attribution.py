#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
STAMP = "20260729"

BASELINE_RUN_ID = "cmb_8d727d5b7a8f"
CHALLENGER_RUN_ID = "cmb_65867aa0f161"
OVERLAP_START = "2020-04-29"
OVERLAP_END = "2026-03-31"

LATEST_STABILITY_WEIGHTS = {
    "sector_focus": 0.2,
    "recovery": 0.4,
    "golden_cross": 0.4,
}
LATEST_STABILITY_END = "2026-07-28"


@dataclass
class FilledTrade:
    strategy: str
    buy_date: str | None
    sell_date: str
    code: str
    qty: int
    pnl: float
    hold_days: int | None


def _load_combined_spec(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT parameter_json, run_hash FROM backtest_run_specs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"missing combined spec for {run_id}")
    spec = json.loads(row[0])
    spec["_run_hash"] = row[1]
    return spec


def _load_run_payload(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT trades_json, total_return_pct, max_drawdown_pct, start_date, end_date FROM backtest_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"missing run payload for {run_id}")
    payload = json.loads(row[0])
    payload["_run_meta"] = {
        "run_id": run_id,
        "total_return_pct": row[1],
        "max_drawdown_pct": row[2],
        "start_date": row[3],
        "end_date": row[4],
    }
    return payload


def _component_runs_for_hashes(conn: sqlite3.Connection, run_hashes: list[str]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.run_id, r.strategy, r.start_date, r.end_date, r.total_return_pct, r.created_at, s.run_hash
        FROM backtest_runs r
        JOIN backtest_run_specs s ON s.run_id = r.run_id
        WHERE s.run_hash IN ({})
        ORDER BY r.strategy, r.created_at DESC
        """.format(",".join("?" for _ in run_hashes)),
        run_hashes,
    ).fetchall()
    out = []
    seen: set[str] = set()
    for row in rows:
        if row[6] in seen:
            continue
        seen.add(row[6])
        out.append(
            {
                "run_id": row[0],
                "strategy": row[1],
                "start_date": row[2],
                "end_date": row[3],
                "total_return_pct": round(float(row[4]), 2) if row[4] is not None else None,
                "created_at": row[5],
                "run_hash": row[6],
            }
        )
    return out


def _fifo_realized_trades(payload: dict[str, Any]) -> list[FilledTrade]:
    event_map: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in payload["events"]:
        if event.get("status") != "filled":
            continue
        event_map[(event["date"], event["stock_code"], event["side"])].append(event)

    positions: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    realized: list[FilledTrade] = []

    for row in payload["ledger"]:
        code = row["code"]
        qty = int(row["quantity"])
        price = float(row["price"])
        fee = float(row.get("fee") or 0.0)
        tax = float(row.get("tax") or 0.0)
        events = event_map[(row["date"], code, row["side"])]
        event = events.pop(0) if events else {}
        contributors = event.get("contributors") or []
        strategy = event.get("capital_owner") or (contributors[0] if contributors else "unknown")

        if row["side"] == "buy":
            unit_cost = (price * qty + fee) / qty
            positions[code].append(
                {
                    "qty": qty,
                    "unit_cost": unit_cost,
                    "buy_date": row["date"],
                    "strategy": strategy,
                }
            )
            continue

        net_per_share = (price * qty - fee - tax) / qty
        remaining = qty
        while remaining > 0 and positions[code]:
            lot = positions[code][0]
            matched = min(remaining, int(lot["qty"]))
            pnl = (net_per_share - float(lot["unit_cost"])) * matched
            realized.append(
                FilledTrade(
                    strategy=str(lot["strategy"]),
                    buy_date=str(lot["buy_date"]),
                    sell_date=str(row["date"]),
                    code=str(code),
                    qty=matched,
                    pnl=float(pnl),
                    hold_days=(
                        (
                            date.fromisoformat(str(row["date"])) - date.fromisoformat(str(lot["buy_date"]))
                        ).days
                        if lot.get("buy_date")
                        else None
                    ),
                )
            )
            lot["qty"] = int(lot["qty"]) - matched
            remaining -= matched
            if int(lot["qty"]) == 0:
                positions[code].popleft()
    return realized


def _quarter_key(day: str) -> str:
    month = int(day[5:7])
    quarter = (month - 1) // 3 + 1
    return f"{day[:4]}-Q{quarter}"


def _counter_hhi(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if not total:
        return 0.0
    return round(sum((count / total) ** 2 for count in counter.values()), 6)


def _counter_rows(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"period": key, "count": count} for key, count in counter.most_common(limit)]


def _run_strategy_breakdown(payload: dict[str, Any], realized: list[FilledTrade]) -> dict[str, Any]:
    strategy_rows: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"realized_pnl": 0.0, "closed_trades": 0, "wins": 0, "hold_days_sum": 0, "hold_days_count": 0}
    )
    monthly_pnl: dict[str, float] = defaultdict(float)
    overlap_monthly_pnl: dict[str, float] = defaultdict(float)
    filled_buys: Counter[str] = Counter()
    filled_sells: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    buy_rejected: Counter[str] = Counter()
    sell_rejected: Counter[str] = Counter()
    monthly_entries: dict[str, Counter[str]] = defaultdict(Counter)
    reject_reasons: Counter[str] = Counter()
    reject_days: Counter[str] = Counter()
    strategy_reject_days: dict[str, Counter[str]] = defaultdict(Counter)
    buy_reject_days: Counter[str] = Counter()
    buy_reject_months: Counter[str] = Counter()
    buy_reject_years: Counter[str] = Counter()
    buy_reject_quarters: Counter[str] = Counter()
    strategy_buy_reject_days: dict[str, Counter[str]] = defaultdict(Counter)
    strategy_buy_reject_years: dict[str, Counter[str]] = defaultdict(Counter)
    strategy_buy_reject_quarters: dict[str, Counter[str]] = defaultdict(Counter)
    fill_days: Counter[str] = Counter()

    for trade in realized:
        row = strategy_rows[trade.strategy]
        row["realized_pnl"] = float(row["realized_pnl"]) + trade.pnl
        row["closed_trades"] = int(row["closed_trades"]) + 1
        if trade.pnl > 0:
            row["wins"] = int(row["wins"]) + 1
        if trade.hold_days is not None:
            row["hold_days_sum"] = int(row["hold_days_sum"]) + trade.hold_days
            row["hold_days_count"] = int(row["hold_days_count"]) + 1
        month = trade.sell_date[:7]
        monthly_pnl[month] += trade.pnl
        if OVERLAP_START <= trade.sell_date <= OVERLAP_END:
            overlap_monthly_pnl[month] += trade.pnl

    for event in payload["events"]:
        contributors = event.get("contributors") or []
        for strategy in contributors:
            if event["status"] == "rejected":
                rejected[strategy] += 1
                if event["side"] == "buy":
                    buy_rejected[strategy] += 1
                    buy_reject_days[event["date"]] += 1
                    buy_reject_months[event["date"][:7]] += 1
                    buy_reject_years[event["date"][:4]] += 1
                    buy_reject_quarters[_quarter_key(event["date"])] += 1
                    strategy_buy_reject_days[strategy][event["date"]] += 1
                    strategy_buy_reject_years[strategy][event["date"][:4]] += 1
                    strategy_buy_reject_quarters[strategy][_quarter_key(event["date"])] += 1
                elif event["side"] == "sell":
                    sell_rejected[strategy] += 1
            elif event["status"] == "filled" and event["side"] == "buy":
                filled_buys[strategy] += 1
                monthly_entries[strategy][event["date"][:7]] += 1
                fill_days[event["date"]] += 1
            elif event["status"] == "filled" and event["side"] == "sell":
                filled_sells[strategy] += 1
        if event["status"] == "rejected":
            reject_reasons[str(event.get("reason") or "unknown")] += 1
            reject_days[event["date"]] += 1
            for strategy in contributors or ["unknown"]:
                strategy_reject_days[strategy][event["date"]] += 1

    strategy_summary = {}
    for strategy, row in sorted(strategy_rows.items()):
        trades = int(row["closed_trades"])
        wins = int(row["wins"])
        strategy_summary[strategy] = {
            "realized_pnl": round(float(row["realized_pnl"]), 2),
            "closed_trades": trades,
            "win_rate_pct": round(wins / trades * 100.0, 2) if trades else None,
            "filled_buys": int(filled_buys[strategy]),
            "filled_sells": int(filled_sells[strategy]),
            "rejections": int(rejected[strategy]),
            "buy_rejections": int(buy_rejected[strategy]),
            "sell_rejections": int(sell_rejected[strategy]),
            "buy_rejection_rate_pct": round(
                buy_rejected[strategy] / (buy_rejected[strategy] + filled_buys[strategy]) * 100.0, 2
            ) if (buy_rejected[strategy] + filled_buys[strategy]) else None,
            "avg_hold_days": round(
                int(row["hold_days_sum"]) / int(row["hold_days_count"]), 2
            ) if int(row["hold_days_count"]) else None,
            "closed_trade_hold_days_total": int(row["hold_days_sum"]),
            "entry_active_months": len(monthly_entries[strategy]),
            "avg_monthly_entries": round(
                sum(monthly_entries[strategy].values()) / len(monthly_entries[strategy]), 2
            ) if monthly_entries[strategy] else None,
            "max_monthly_entries": max(monthly_entries[strategy].values()) if monthly_entries[strategy] else None,
        }

    return {
        "summary": payload["summary"],
        "strategy_summary": strategy_summary,
        "best_months": [
            {"month": month, "realized_pnl": round(value, 2)}
            for month, value in sorted(monthly_pnl.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
        "worst_months": [
            {"month": month, "realized_pnl": round(value, 2)}
            for month, value in sorted(monthly_pnl.items(), key=lambda item: item[1])[:8]
        ],
        "overlap_monthly_pnl": {
            month: round(value, 2)
            for month, value in sorted(overlap_monthly_pnl.items())
        },
        "reject_reason_summary": dict(reject_reasons),
        "top_reject_days": [
            {"date": day, "count": count}
            for day, count in reject_days.most_common(10)
        ],
        "top_fill_days": [
            {"date": day, "count": count}
            for day, count in fill_days.most_common(10)
        ],
        "strategy_top_reject_days": {
            strategy: [{"date": day, "count": count} for day, count in counter.most_common(6)]
            for strategy, counter in sorted(strategy_reject_days.items())
        },
        "buy_reject_structure": {
            "total_buy_rejections": int(sum(buy_reject_days.values())),
            "active_days": len(buy_reject_days),
            "day_concentration_hhi": _counter_hhi(buy_reject_days),
            "top_days": [{"date": day, "count": count} for day, count in buy_reject_days.most_common(10)],
            "top_months": _counter_rows(buy_reject_months, 8),
            "top_years": _counter_rows(buy_reject_years, 8),
            "top_quarters": _counter_rows(buy_reject_quarters, 8),
            "by_strategy": {
                strategy: {
                    "total_buy_rejections": int(sum(counter.values())),
                    "active_days": len(counter),
                    "day_concentration_hhi": _counter_hhi(counter),
                    "top_days": [{"date": day, "count": count} for day, count in counter.most_common(6)],
                    "top_years": _counter_rows(strategy_buy_reject_years[strategy], 6),
                    "top_quarters": _counter_rows(strategy_buy_reject_quarters[strategy], 6),
                }
                for strategy, counter in sorted(strategy_buy_reject_days.items())
            },
        },
    }


def _latest_strategy_run(conn: sqlite3.Connection, strategy: str, end_date: str) -> tuple[str, list[dict[str, Any]]]:
    row = conn.execute(
        """
        SELECT run_id, trades_json
        FROM backtest_runs
        WHERE strategy=?
          AND status='done'
          AND start_date='2020-03-01'
          AND end_date=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (strategy, end_date),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing {strategy} run ending {end_date}")
    raw = json.loads(row[1]) if row[1] else []
    trades = raw.get("trades", []) if isinstance(raw, dict) else raw
    return row[0], trades


def _trade_dates_prices(trade: dict[str, Any]) -> tuple[str | None, str | None, float | None, float | None]:
    buy_date = trade.get("buy_date") or trade.get("entry_date")
    sell_date = trade.get("sell_date") or trade.get("exit_date")
    entry = trade.get("entry") if trade.get("entry") is not None else trade.get("entry_price")
    exit_ = trade.get("exit") if trade.get("exit") is not None else trade.get("exit_price")
    try:
        entry_price = float(entry) if entry is not None else None
    except Exception:
        entry_price = None
    try:
        exit_price = float(exit_) if exit_ is not None else None
    except Exception:
        exit_price = None
    return buy_date, sell_date, entry_price, exit_price


def _simulate_latest_stability(conn: sqlite3.Connection) -> dict[str, Any]:
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account

    run_ids: dict[str, str] = {}
    orders: list[CandidateOrder] = []
    active = sum(1 for value in LATEST_STABILITY_WEIGHTS.values() if value > 0)
    for strategy, weight in LATEST_STABILITY_WEIGHTS.items():
        run_id, trades = _latest_strategy_run(conn, strategy, LATEST_STABILITY_END)
        run_ids[strategy] = run_id
        budget = 10_000_000 * max(0.45, weight * active)
        for trade in trades:
            buy_date, sell_date, entry_price, exit_price = _trade_dates_prices(trade)
            if not buy_date or not sell_date or not entry_price or not exit_price:
                continue
            code = str(trade.get("code") or trade.get("stock_code") or "")
            if not code:
                continue
            raw_score = trade.get("score")
            if raw_score is None:
                raw_score = trade.get("signal_score")
            try:
                score = float(raw_score or 0.0)
            except Exception:
                score = 0.0
            priority = round(weight * 1000.0 + min(score, 999.0), 4)
            orders.append(
                CandidateOrder(
                    date=buy_date,
                    stock_code=code,
                    side="buy",
                    price=entry_price,
                    strategy=strategy,
                    priority=priority,
                    budget=budget,
                )
            )
            orders.append(
                CandidateOrder(
                    date=sell_date,
                    stock_code=code,
                    side="sell",
                    price=exit_price,
                    strategy=strategy,
                    priority=priority,
                )
            )

    cfg = MergeConfig(
        initial_cash=100_000_000,
        ticket_budget=10_000_000,
        max_positions=20,
        dynamic_tickets=True,
        max_sector_positions=None,
    )
    result = simulate_merged_account(orders, cfg)
    summary = result["summary"]
    return {
        "weights": LATEST_STABILITY_WEIGHTS,
        "component_run_ids": run_ids,
        "summary": {
            "total_return_pct": round(float(summary["total_return_pct"]), 2),
            "max_drawdown_pct": round(float(summary.get("max_drawdown_pct") or 0.0), 2),
            "completed_trades": int(summary.get("completed_trades") or 0),
            "buy_fills": int(summary.get("buy_fills") or 0),
            "rejections": int(summary.get("rejections") or 0),
            "buy_rejections": int(summary.get("buy_rejections") or 0),
            "sell_rejections": int(summary.get("sell_rejections") or 0),
            "rejection_reasons": dict(summary.get("rejection_reasons") or {}),
        },
    }


def _exact_replay_check(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account

    payload = _load_run_payload(conn, run_id)
    spec = _load_combined_spec(conn, run_id)
    orders = [CandidateOrder(**order) for order in spec["orders"]]
    cfg = MergeConfig(**spec["config"])
    replay = simulate_merged_account(orders, cfg)["summary"]
    persisted = payload["summary"]
    checks = {
        "total_return_pct": round(float(replay["total_return_pct"]), 2),
        "max_drawdown_pct": round(float(replay.get("max_drawdown_pct") or 0.0), 2),
        "completed_trades": int(replay.get("completed_trades") or 0),
        "buy_fills": int(replay.get("buy_fills") or 0),
        "rejections": int(replay.get("rejections") or 0),
        "buy_rejections": int(replay.get("buy_rejections") or 0),
        "sell_rejections": int(replay.get("sell_rejections") or 0),
        "rejection_reasons": dict(replay.get("rejection_reasons") or {}),
    }
    persisted_core = {
        "total_return_pct": round(float(persisted["total_return_pct"]), 2),
        "max_drawdown_pct": round(float(persisted.get("max_drawdown_pct") or 0.0), 2),
        "completed_trades": int(persisted.get("completed_trades") or 0),
        "buy_fills": int(persisted.get("buy_fills") or 0),
        "rejections": int(persisted.get("rejections") or 0),
    }
    persisted_view = {
        **persisted_core,
        "buy_rejections": persisted.get("buy_rejections"),
        "sell_rejections": persisted.get("sell_rejections"),
        "rejection_reasons": dict(persisted.get("rejection_reasons") or {}),
    }
    core_matches = all(checks[key] == persisted_core[key] for key in persisted_core)
    extended_matches = checks == {
        **persisted_core,
        "buy_rejections": int(persisted.get("buy_rejections") or 0),
        "sell_rejections": int(persisted.get("sell_rejections") or 0),
        "rejection_reasons": dict(persisted.get("rejection_reasons") or {}),
    }
    return {
        "run_id": run_id,
        "run_hash": spec["_run_hash"],
        "component_runs": _component_runs_for_hashes(conn, list(spec["component_run_hashes"])),
        "persisted_summary": persisted_view,
        "replayed_summary": checks,
        "core_matches": core_matches,
        "extended_matches": extended_matches,
    }


def _compare_overlap_months(base: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    base_months = base["overlap_monthly_pnl"]
    challenger_months = challenger["overlap_monthly_pnl"]
    months = sorted(set(base_months) | set(challenger_months))
    rows = []
    base_better = 0
    challenger_better = 0
    delta_sum = 0.0
    for month in months:
        base_value = float(base_months.get(month, 0.0))
        challenger_value = float(challenger_months.get(month, 0.0))
        delta = round(base_value - challenger_value, 2)
        delta_sum += delta
        if delta > 0:
            base_better += 1
        elif delta < 0:
            challenger_better += 1
        rows.append(
            {
                "month": month,
                "baseline_realized_pnl": round(base_value, 2),
                "challenger_realized_pnl": round(challenger_value, 2),
                "baseline_edge": delta,
            }
        )
    top_baseline = sorted(rows, key=lambda row: row["baseline_edge"], reverse=True)[:8]
    top_challenger = sorted(rows, key=lambda row: row["baseline_edge"])[:8]
    return {
        "months_compared": len(rows),
        "baseline_better_months": base_better,
        "challenger_better_months": challenger_better,
        "net_baseline_edge_realized_pnl": round(delta_sum, 2),
        "top_baseline_edge_months": top_baseline,
        "top_challenger_edge_months": top_challenger,
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    base = report["baseline"]
    challenger = report["challenger"]
    overlap = report["overlap_comparison"]
    stability = report["latest_stability_check"]
    baseline_replay = report["exact_replay_checks"]["baseline"]
    challenger_replay = report["exact_replay_checks"]["challenger"]
    baseline_buy_rejects = base["buy_reject_structure"]
    challenger_buy_rejects = challenger["buy_reject_structure"]

    lines = [
        f"# combo edge attribution audit - {STAMP}",
        "",
        "## Headline",
        f"- Baseline run: `{base['run_id']}` -> `{base['summary']['total_return_pct']}%`, MDD `{base['summary']['max_drawdown_pct']}%`",
        f"- Challenger run: `{challenger['run_id']}` -> `{challenger['summary']['total_return_pct']}%`, MDD `{challenger['summary']['max_drawdown_pct']}%`",
        f"- Overlap window: `{OVERLAP_START}` to `{OVERLAP_END}`",
        f"- Baseline better months in overlap: `{overlap['baseline_better_months']}` / `{overlap['months_compared']}`",
        f"- Challenger better months in overlap: `{overlap['challenger_better_months']}` / `{overlap['months_compared']}`",
        f"- Net realized PnL edge in overlap: `{overlap['net_baseline_edge_realized_pnl']}`",
        f"- Exact replay core match baseline/challenger: `{baseline_replay['core_matches']}` / `{challenger_replay['core_matches']}`",
        f"- Buy-side slot pressure baseline/challenger: `{baseline_buy_rejects['total_buy_rejections']}` / `{challenger_buy_rejects['total_buy_rejections']}`",
        "",
        "## Strategy Breakdown",
    ]
    for label, block in [("baseline", base), ("challenger", challenger)]:
        lines.append(f"### {label} `{block['run_id']}`")
        summary = block["summary"]
        lines.append(
            f"- summary: total_rejections `{summary.get('rejections')}`, "
            f"buy_rejections `{summary.get('buy_rejections', 'n/a')}`, "
            f"sell_rejections `{summary.get('sell_rejections', 'n/a')}`"
        )
        for strategy, row in block["strategy_summary"].items():
            lines.append(
                f"- {strategy}: pnl `{row['realized_pnl']}`, closed `{row['closed_trades']}`, "
                f"win_rate `{row['win_rate_pct']}%`, avg_hold `{row['avg_hold_days']}`d, "
                f"hold_days_total `{row['closed_trade_hold_days_total']}`, "
                f"buy_fills `{row['filled_buys']}`, buy_rejections `{row['buy_rejections']}` "
                f"(`{row['buy_rejection_rate_pct']}%`), sell_rejections `{row['sell_rejections']}`"
            )
            lines.append(
                f"- {strategy}: entry_active_months `{row['entry_active_months']}`, "
                f"avg_monthly_entries `{row['avg_monthly_entries']}`, max_monthly_entries `{row['max_monthly_entries']}`"
            )
        lines.append("")
        lines.append(f"- reject reasons: `{json.dumps(block['reject_reason_summary'], ensure_ascii=False)}`")
        if block["top_reject_days"]:
            top_reject = ", ".join(f"{row['date']} ({row['count']})" for row in block["top_reject_days"][:5])
            lines.append(f"- top reject days: `{top_reject}`")
        if block["top_fill_days"]:
            top_fill = ", ".join(f"{row['date']} ({row['count']})" for row in block["top_fill_days"][:5])
            lines.append(f"- top fill days: `{top_fill}`")
        lines.append("")

    lines.extend(
        [
            "## Exact Replay Checks",
            f"- Baseline `{baseline_replay['run_id']}` core match: `{baseline_replay['core_matches']}`, extended match: `{baseline_replay['extended_matches']}` from run hash `{baseline_replay['run_hash']}`",
            f"- Challenger `{challenger_replay['run_id']}` core match: `{challenger_replay['core_matches']}`, extended match: `{challenger_replay['extended_matches']}` from run hash `{challenger_replay['run_hash']}`",
            "",
            "## Buy-Rejection Structure",
            f"- Baseline: total `{baseline_buy_rejects['total_buy_rejections']}`, active_days `{baseline_buy_rejects['active_days']}`, HHI `{baseline_buy_rejects['day_concentration_hhi']}`",
            f"- Challenger: total `{challenger_buy_rejects['total_buy_rejections']}`, active_days `{challenger_buy_rejects['active_days']}`, HHI `{challenger_buy_rejects['day_concentration_hhi']}`",
        ]
    )
    if baseline_buy_rejects["top_days"]:
        top_days = ", ".join(f"{row['date']} ({row['count']})" for row in baseline_buy_rejects["top_days"][:5])
        lines.append(f"- Baseline top buy-reject days: `{top_days}`")
    if challenger_buy_rejects["top_days"]:
        top_days = ", ".join(f"{row['date']} ({row['count']})" for row in challenger_buy_rejects["top_days"][:5])
        lines.append(f"- Challenger top buy-reject days: `{top_days}`")
    if baseline_buy_rejects["top_years"]:
        rows = ", ".join(f"{row['period']} ({row['count']})" for row in baseline_buy_rejects["top_years"][:4])
        lines.append(f"- Baseline top buy-reject years: `{rows}`")
    if challenger_buy_rejects["top_years"]:
        rows = ", ".join(f"{row['period']} ({row['count']})" for row in challenger_buy_rejects["top_years"][:4])
        lines.append(f"- Challenger top buy-reject years: `{rows}`")
    if baseline_buy_rejects["top_quarters"]:
        rows = ", ".join(f"{row['period']} ({row['count']})" for row in baseline_buy_rejects["top_quarters"][:5])
        lines.append(f"- Baseline top buy-reject quarters: `{rows}`")
    if challenger_buy_rejects["top_quarters"]:
        rows = ", ".join(f"{row['period']} ({row['count']})" for row in challenger_buy_rejects["top_quarters"][:5])
        lines.append(f"- Challenger top buy-reject quarters: `{rows}`")
    for label, block in [("Baseline", baseline_buy_rejects), ("Challenger", challenger_buy_rejects)]:
        strategy_rows = []
        for strategy, row in sorted(block["by_strategy"].items()):
            top_year = row["top_years"][0]["period"] if row["top_years"] else "n/a"
            top_year_count = row["top_years"][0]["count"] if row["top_years"] else 0
            strategy_rows.append(
                f"{strategy}: {row['total_buy_rejections']} total, {row['active_days']} days, top_year {top_year} ({top_year_count})"
            )
        if strategy_rows:
            lines.append(f"- {label} strategy split: `{' | '.join(strategy_rows)}`")
    lines.extend(
        [
            "",
            "## Overlap Edge",
            "- Strongest baseline edge months:",
        ]
    )
    for row in overlap["top_baseline_edge_months"]:
        lines.append(
            f"- {row['month']}: baseline `{row['baseline_realized_pnl']}` vs challenger `{row['challenger_realized_pnl']}` "
            f"(edge `{row['baseline_edge']}`)"
        )
    lines.append("- Strongest challenger edge months:")
    for row in overlap["top_challenger_edge_months"]:
        lines.append(
            f"- {row['month']}: baseline `{row['baseline_realized_pnl']}` vs challenger `{row['challenger_realized_pnl']}` "
            f"(edge `{row['baseline_edge']}`)"
        )

    lines.extend(
        [
            "",
            "## Latest Stability Check",
            f"- Tested the challenger weights on latest common component horizon `{LATEST_STABILITY_END}`.",
            f"- Weights: `{json.dumps(stability['weights'], ensure_ascii=False)}`",
            f"- Component runs: `{json.dumps(stability['component_run_ids'], ensure_ascii=False)}`",
            f"- Result: `{stability['summary']['total_return_pct']}%`, MDD `{stability['summary']['max_drawdown_pct']}%`, "
            f"trades `{stability['summary']['completed_trades']}`, total_rejections `{stability['summary']['rejections']}`, "
            f"buy_rejections `{stability['summary']['buy_rejections']}`, sell_rejections `{stability['summary']['sell_rejections']}`",
            f"- Rejection reasons: `{json.dumps(stability['summary']['rejection_reasons'], ensure_ascii=False)}`",
            "",
            "## Takeaway",
            "- The baseline edge is not just a small weight difference. It comes from a broader and more durable monthly edge profile.",
            "- The challenger depends heavily on a few large payoff windows and degrades sharply when extended into the latest months.",
            "- On buy-side slot pressure, the challenger is not just worse in total count; it is active on far more blocked dates, which points to persistent opportunity crowding rather than a few isolated collisions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        baseline_payload = _load_run_payload(conn, BASELINE_RUN_ID)
        challenger_payload = _load_run_payload(conn, CHALLENGER_RUN_ID)
        baseline_realized = _fifo_realized_trades(baseline_payload)
        challenger_realized = _fifo_realized_trades(challenger_payload)
        baseline = _run_strategy_breakdown(baseline_payload, baseline_realized)
        challenger = _run_strategy_breakdown(challenger_payload, challenger_realized)
        baseline["run_id"] = BASELINE_RUN_ID
        challenger["run_id"] = CHALLENGER_RUN_ID
        latest_stability = _simulate_latest_stability(conn)
        replay_checks = {
            "baseline": _exact_replay_check(conn, BASELINE_RUN_ID),
            "challenger": _exact_replay_check(conn, CHALLENGER_RUN_ID),
        }
        baseline["summary"] = {**replay_checks["baseline"]["replayed_summary"], **baseline["summary"]}
        challenger["summary"] = {**replay_checks["challenger"]["replayed_summary"], **challenger["summary"]}
        report = {
            "as_of": STAMP,
            "baseline": baseline,
            "challenger": challenger,
            "overlap_window": {"start": OVERLAP_START, "end": OVERLAP_END},
            "overlap_comparison": _compare_overlap_months(baseline, challenger),
            "exact_replay_checks": replay_checks,
            "latest_stability_check": latest_stability,
        }
    finally:
        conn.close()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"combo_edge_attribution_{STAMP}.json"
    md_path = OUT_DIR / f"combo_edge_attribution_{STAMP}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)
    print(str(json_path))
    print(str(md_path))
    print(json.dumps(report["overlap_comparison"], ensure_ascii=False))
    print(json.dumps(report["latest_stability_check"]["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
