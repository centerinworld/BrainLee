#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Iterable

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import merged_simulator as ms
from merged_simulator import CandidateOrder, MergeConfig, persist_merged_run, simulate_merged_account


STRATEGIES = [
    "sector_focus",
    "se_momentum",
    "earnings_conviction",
    "recovery",
    "golden_cross",
]

STRATEGY_LABELS = {
    "sector_focus": "V-SECTOR",
    "se_momentum": "V-SE",
    "earnings_conviction": "V-EARNINGS",
    "recovery": "V-RECOVERY",
    "golden_cross": "V-GC",
}

PERIODS = [
    {"start": "2020-03-01", "end": "2021-11-30", "bucket": "offensive", "label": "20.3~21.11"},
    {"start": "2021-12-01", "end": "2022-10-31", "bucket": "defensive", "label": "21.12~22.10"},
    {"start": "2022-11-01", "end": "2023-10-31", "bucket": "offensive", "label": "22.11~23.10"},
    {"start": "2023-11-01", "end": "2024-12-31", "bucket": "offensive", "label": "23.11~24.12"},
    {"start": "2024-06-01", "end": "2025-05-31", "bucket": "defensive", "label": "24.6~25.5"},
    {"start": "2025-06-01", "end": "2026-03-31", "bucket": "offensive", "label": "25.6~26.3"},
]

# 연속검증용 비중 스케줄. 겹치는 기간은 사용자 관점에서 더 보수적인 최근 방어장 정의를 우선한다.
DYNAMIC_SCHEDULE = [
    ("2020-03-01", "2021-11-30", "offensive"),
    ("2021-12-01", "2022-10-31", "defensive"),
    ("2022-11-01", "2024-05-31", "offensive"),
    ("2024-06-01", "2025-05-31", "defensive"),
    ("2025-06-01", "2026-03-31", "offensive"),
]

OUT_DIR = ROOT / "research_outputs"

SEEDED_PROFILES = {
    "recovery_gc_balance": {
        "offensive": {"sector_focus": 0.2, "se_momentum": 0.0, "earnings_conviction": 0.0, "recovery": 0.4, "golden_cross": 0.4},
        "defensive": {"sector_focus": 0.2, "se_momentum": 0.0, "earnings_conviction": 0.0, "recovery": 0.4, "golden_cross": 0.4},
    },
    "sf_heavy": {
        "offensive": {"sector_focus": 0.6, "se_momentum": 0.2, "earnings_conviction": 0.2, "recovery": 0.0, "golden_cross": 0.0},
        "defensive": {"sector_focus": 0.2, "se_momentum": 0.0, "earnings_conviction": 0.2, "recovery": 0.6, "golden_cross": 0.0},
    },
    "gc_mix": {
        "offensive": {"sector_focus": 0.4, "se_momentum": 0.0, "earnings_conviction": 0.2, "recovery": 0.0, "golden_cross": 0.4},
        "defensive": {"sector_focus": 0.2, "se_momentum": 0.0, "earnings_conviction": 0.2, "recovery": 0.4, "golden_cross": 0.2},
    },
    "current_loose": {
        "offensive": {"sector_focus": 0.2, "se_momentum": 0.6, "earnings_conviction": 0.2, "recovery": 0.0, "golden_cross": 0.0},
        "defensive": {"sector_focus": 0.2, "se_momentum": 0.2, "earnings_conviction": 0.2, "recovery": 0.4, "golden_cross": 0.0},
    },
}


@dataclass
class PeriodRun:
    strategy: str
    start_date: str
    end_date: str
    run_id: str
    run_hash: str
    created_at: str
    total_return_pct: float | None
    trades: list[dict]


def _latest_runs(conn: sqlite3.Connection) -> dict[tuple[str, str, str], PeriodRun]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT r.strategy, r.start_date, r.end_date, r.run_id, r.created_at, r.total_return_pct,
               r.trades_json, COALESCE(s.run_hash, '') AS run_hash
        FROM backtest_runs r
        LEFT JOIN backtest_run_specs s ON s.run_id = r.run_id
        WHERE r.status='done'
          AND r.strategy IN ({})
        ORDER BY r.strategy, r.start_date, r.end_date, r.created_at DESC
        """.format(",".join("?" for _ in STRATEGIES)),
        STRATEGIES,
    ).fetchall()
    latest: dict[tuple[str, str, str], PeriodRun] = {}
    for row in rows:
        key = (row["strategy"], row["start_date"], row["end_date"])
        if key in latest:
            continue
        raw = json.loads(row["trades_json"]) if row["trades_json"] else []
        trades = raw.get("trades", []) if isinstance(raw, dict) else raw
        latest[key] = PeriodRun(
            strategy=row["strategy"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            run_id=row["run_id"],
            run_hash=row["run_hash"] or "",
            created_at=row["created_at"],
            total_return_pct=row["total_return_pct"],
            trades=trades,
        )
    return latest


def _weight_grid(step: float = 0.2) -> Iterable[dict[str, float]]:
    units = int(round(1.0 / step))
    for alloc in product(range(units + 1), repeat=len(STRATEGIES)):
        if sum(alloc) != units:
            continue
        if alloc[0] == 0 or alloc[2] == 0:
            # 핵심축 2개(V-SECTOR, V-EARNINGS)는 항상 최소 비중 유지
            continue
        out = {strategy: round(value * step, 4) for strategy, value in zip(STRATEGIES, alloc)}
        if sum(v > 0 for v in out.values()) < 3:
            continue
        yield out


def _bucket_for_date(day: str) -> str:
    for start, end, bucket in DYNAMIC_SCHEDULE:
        if start <= day <= end:
            return bucket
    return "offensive"


def _budget_multiplier(weights: dict[str, float], strategy: str) -> float:
    active = max(1, sum(1 for weight in weights.values() if weight > 0))
    weight = float(weights.get(strategy, 0.0))
    # 동등비중은 1.0 티켓, 상위전략은 2~3배까지 확대.
    return max(0.45, weight * active)


def _priority(weights: dict[str, float], strategy: str, trade: dict) -> float:
    raw_score = trade.get("score")
    if raw_score is None:
        raw_score = trade.get("signal_score")
    try:
        score = float(raw_score or 0.0)
    except Exception:
        score = 0.0
    return round(weights.get(strategy, 0.0) * 1000.0 + min(score, 999.0), 4)


def _trade_dates_prices(trade: dict) -> tuple[str | None, str | None, float | None, float | None]:
    buy_date = trade.get("buy_date") or trade.get("entry_date")
    sell_date = trade.get("sell_date") or trade.get("exit_date")
    entry_price = trade.get("entry") if trade.get("entry") is not None else trade.get("entry_price")
    exit_price = trade.get("exit") if trade.get("exit") is not None else trade.get("exit_price")
    try:
        entry_price = float(entry_price) if entry_price is not None else None
    except Exception:
        entry_price = None
    try:
        exit_price = float(exit_price) if exit_price is not None else None
    except Exception:
        exit_price = None
    return buy_date, sell_date, entry_price, exit_price


def _orders_from_runs(
    runs: list[PeriodRun],
    weights_by_bucket: dict[str, dict[str, float]],
    ticket_budget: float = 10_000_000,
) -> tuple[list[CandidateOrder], list[str]]:
    orders: list[CandidateOrder] = []
    component_hashes: list[str] = []
    for run in runs:
        if run.run_hash:
            component_hashes.append(run.run_hash)
        for trade in run.trades:
            buy_date, sell_date, entry_price, exit_price = _trade_dates_prices(trade)
            if not buy_date or not sell_date or not entry_price or not exit_price:
                continue
            bucket = _bucket_for_date(buy_date)
            weights = weights_by_bucket[bucket]
            if weights.get(run.strategy, 0.0) <= 0:
                continue
            budget = ticket_budget * _budget_multiplier(weights, run.strategy)
            prio = _priority(weights, run.strategy, trade)
            code = str(trade.get("code") or trade.get("stock_code") or "")
            if not code:
                continue
            orders.append(
                CandidateOrder(
                    date=buy_date,
                    stock_code=code,
                    side="buy",
                    price=entry_price,
                    strategy=run.strategy,
                    priority=prio,
                    budget=budget,
                )
            )
            orders.append(
                CandidateOrder(
                    date=sell_date,
                    stock_code=code,
                    side="sell",
                    price=exit_price,
                    strategy=run.strategy,
                    priority=prio,
                )
            )
    return orders, sorted(set(component_hashes))


def _simulate_period_combo(
    period_runs: list[PeriodRun],
    weights_by_bucket: dict[str, dict[str, float]],
    fast_mode: bool = False,
) -> dict:
    orders, _ = _orders_from_runs(period_runs, weights_by_bucket)
    cfg = MergeConfig(
        initial_cash=100_000_000,
        ticket_budget=10_000_000,
        max_positions=20,
        dynamic_tickets=True,
        max_sector_positions=None,
    )
    if fast_mode:
        original_loader = ms._load_daily_price_map
        try:
            ms._load_daily_price_map = lambda *args, **kwargs: {}
            result = simulate_merged_account(orders, cfg)
        finally:
            ms._load_daily_price_map = original_loader
    else:
        result = simulate_merged_account(orders, cfg)
    summary = result["summary"]
    return {
        "return_pct": round(float(summary["total_return_pct"]), 2),
        "mdd_pct": round(float(summary.get("max_drawdown_pct") or 0.0), 2),
        "completed_trades": int(summary.get("completed_trades") or 0),
        "buy_fills": int(summary.get("buy_fills") or 0),
        "deduplicated": int(summary.get("deduplicated") or 0),
        "rejections": int(summary.get("rejections") or 0),
    }


def _score_bucket(results: list[dict]) -> float:
    avg_ret = sum(item["return_pct"] for item in results) / len(results)
    worst_ret = min(item["return_pct"] for item in results)
    avg_mdd = sum(item["mdd_pct"] for item in results) / len(results)
    return round(avg_ret + 0.30 * worst_ret + 0.15 * avg_mdd, 6)


def _search_bucket(latest: dict[tuple[str, str, str], PeriodRun], bucket: str) -> dict:
    periods = [period for period in PERIODS if period["bucket"] == bucket]
    trials: list[dict] = []
    for weights in _weight_grid():
        period_rows = []
        weights_by_bucket = {"offensive": weights, "defensive": weights}
        for period in periods:
            runs = []
            ok = True
            for strategy in STRATEGIES:
                run = latest.get((strategy, period["start"], period["end"]))
                if not run:
                    ok = False
                    break
                runs.append(run)
            if not ok:
                period_rows = []
                break
            sim = _simulate_period_combo(runs, weights_by_bucket, fast_mode=True)
            sim.update({"period": period["label"], "start": period["start"], "end": period["end"]})
            period_rows.append(sim)
        if not period_rows:
            continue
        trials.append(
            {
                "weights": weights,
                "score": _score_bucket(period_rows),
                "avg_return_pct": round(sum(item["return_pct"] for item in period_rows) / len(period_rows), 2),
                "worst_return_pct": round(min(item["return_pct"] for item in period_rows), 2),
                "avg_mdd_pct": round(sum(item["mdd_pct"] for item in period_rows) / len(period_rows), 2),
                "periods": period_rows,
            }
        )
    trials.sort(key=lambda item: item["score"], reverse=True)
    if not trials:
        raise RuntimeError(f"no valid trials for bucket={bucket}")
    return {"best": trials[0], "top5": trials[:5], "trial_count": len(trials)}


def _evaluate_full_range(
    latest: dict[tuple[str, str, str], PeriodRun],
    weights_by_bucket: dict[str, dict[str, float]],
    full_range: dict[str, str],
) -> dict:
    runs = []
    for strategy in STRATEGIES:
        run = latest.get((strategy, full_range["start"], full_range["end"]))
        if not run:
            raise RuntimeError(f"missing full-range run for {strategy} {full_range['start']}~{full_range['end']}")
        runs.append(run)
    orders, hashes = _orders_from_runs(runs, weights_by_bucket)
    cfg = MergeConfig(
        initial_cash=100_000_000,
        ticket_budget=10_000_000,
        max_positions=20,
        dynamic_tickets=True,
        max_sector_positions=None,
    )
    sim = simulate_merged_account(orders, cfg)
    persisted = persist_merged_run(orders, hashes, cfg, DB_PATH)
    summary = sim["summary"]
    return {
        "summary": {
            "total_return_pct": round(float(summary["total_return_pct"]), 2),
            "max_drawdown_pct": round(float(summary.get("max_drawdown_pct") or 0.0), 2),
            "completed_trades": int(summary.get("completed_trades") or 0),
            "buy_fills": int(summary.get("buy_fills") or 0),
            "deduplicated": int(summary.get("deduplicated") or 0),
            "rejections": int(summary.get("rejections") or 0),
        },
        "component_run_hashes": hashes,
        "persisted": persisted,
    }


def _candidate_score(summary: dict) -> float:
    return round(float(summary["total_return_pct"]) + 0.25 * float(summary["max_drawdown_pct"]), 6)


def _render_markdown(result: dict, md_path: Path) -> None:
    offensive = result["search"]["offensive"]["best"]
    defensive = result["search"]["defensive"]["best"]
    full_range = result["full_range"]
    lines = [
        f"# strategy barbell combo audit — {result['as_of']}",
        "",
        "## Best Bucket Weights",
        f"- offensive: {json.dumps(offensive['weights'], ensure_ascii=False)}",
        f"- defensive: {json.dumps(defensive['weights'], ensure_ascii=False)}",
        f"- full-range merged return: {full_range['summary']['total_return_pct']}%",
        f"- full-range merged MDD: {full_range['summary']['max_drawdown_pct']}%",
        f"- full-range merged trades: {full_range['summary']['completed_trades']}",
        f"- persisted combined run: {full_range['persisted']['run_id']} ({full_range['persisted']['run_hash']})",
        "",
        "## Offensive Search",
        f"- score: {offensive['score']}",
        f"- avg return: {offensive['avg_return_pct']}%",
        f"- worst return: {offensive['worst_return_pct']}%",
        f"- avg MDD: {offensive['avg_mdd_pct']}%",
    ]
    for row in offensive["periods"]:
        lines.append(
            f"- {row['period']}: ret {row['return_pct']}% / mdd {row['mdd_pct']}% / trades {row['completed_trades']}"
        )
    lines.extend(
        [
            "",
            "## Defensive Search",
            f"- score: {defensive['score']}",
            f"- avg return: {defensive['avg_return_pct']}%",
            f"- worst return: {defensive['worst_return_pct']}%",
            f"- avg MDD: {defensive['avg_mdd_pct']}%",
        ]
    )
    for row in defensive["periods"]:
        lines.append(
            f"- {row['period']}: ret {row['return_pct']}% / mdd {row['mdd_pct']}% / trades {row['completed_trades']}"
        )
    lines.extend(
        [
            "",
        "## Full-Range Components",
        ]
    )
    for item in result["full_range_components"]:
        lines.append(
            f"- {STRATEGY_LABELS[item['strategy']]}: {item['start_date']}~{item['end_date']} ret {item['total_return_pct']}% (run_id {item['run_id']})"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research top-5 strategy barbell combinations.")
    parser.add_argument(
        "--selection-mode",
        choices=["full_range_compound", "period_reset_robust"],
        default="full_range_compound",
        help="Choose whether to persist the best long-range compound candidate or the period-reset robust searched weights.",
    )
    parser.add_argument("--full-range-start", default="2020-03-01")
    parser.add_argument("--full-range-end", default="2026-03-31")
    parser.add_argument("--stamp", default="20260729")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    stamp = args.stamp
    full_range = {"start": args.full_range_start, "end": args.full_range_end}
    json_path = OUT_DIR / f"strategy_barbell_combo_{stamp}.json"
    md_path = OUT_DIR / f"strategy_barbell_combo_{stamp}.md"
    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        latest = _latest_runs(conn)
    finally:
        conn.close()

    offensive = _search_bucket(latest, "offensive")
    defensive = _search_bucket(latest, "defensive")
    searched_weights = {
        "offensive": offensive["best"]["weights"],
        "defensive": defensive["best"]["weights"],
    }
    candidates = {"searched_best": searched_weights, **SEEDED_PROFILES}
    candidate_results = []
    for name, weights_by_bucket in candidates.items():
        full_range_result = _evaluate_full_range(latest, weights_by_bucket, full_range)
        candidate_results.append(
            {
                "name": name,
                "weights_by_bucket": weights_by_bucket,
                "full_range": full_range_result,
                "score": _candidate_score(full_range_result["summary"]),
            }
        )
    candidate_results.sort(key=lambda item: item["score"], reverse=True)
    if args.selection_mode == "period_reset_robust":
        chosen = next(item for item in candidate_results if item["name"] == "searched_best")
    else:
        chosen = candidate_results[0]
    full_components = []
    for strategy in STRATEGIES:
        run = latest[(strategy, full_range["start"], full_range["end"])]
        full_components.append(
            {
                "strategy": strategy,
                "run_id": run.run_id,
                "run_hash": run.run_hash,
                "start_date": run.start_date,
                "end_date": run.end_date,
                "created_at": run.created_at,
                "total_return_pct": run.total_return_pct,
            }
        )

    result = {
        "as_of": stamp,
        "strategies": STRATEGIES,
        "search": {
            "offensive": offensive,
            "defensive": defensive,
        },
        "weights_by_bucket": chosen["weights_by_bucket"],
        "selected_profile": chosen["name"],
        "selection_mode": args.selection_mode,
        "dynamic_schedule": DYNAMIC_SCHEDULE,
        "full_range_window": full_range,
        "full_range": chosen["full_range"],
        "candidate_results": candidate_results,
        "full_range_components": full_components,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _render_markdown(result, md_path)
    print(str(json_path))
    print(str(md_path))
    print(json.dumps(result["weights_by_bucket"], ensure_ascii=False))
    print(json.dumps(result["full_range"]["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
