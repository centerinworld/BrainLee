#!/usr/bin/env python3
"""Validate newly added business-quality factors against strategy snapshots.

Point-in-time approximation:
- Monthly `strategy_feature_snapshot.snapshot_date` is the observation date.
- Quarterly derived factors become usable 60 days after fiscal quarter end.
- DART order-contract events use their actual receipt date.

This is an event-study / candidate-quality validation, not an execution
backtest. It tells us whether factors are worth promoting into strategy center
portfolio simulations.
"""

from __future__ import annotations

import bisect
import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
AS_OF_CUTOFF = "2025-06-30"


@dataclass(frozen=True)
class SignalPoint:
    available_date: str
    good: int = 0
    risk: int = 0
    score: int = 0
    risk_score: int = 0


def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return date(year, month, day)


def available_date(year: int, quarter: int, lag_days: int = 60) -> str:
    return (quarter_end(int(year), int(quarter)) + timedelta(days=lag_days)).isoformat()


def load_quarter_signal(conn: sqlite3.Connection, table: str, good_types: set[str] | None = None) -> dict[str, list[SignalPoint]]:
    if table == "contract_advance_signals":
        rows = conn.execute("""
            SELECT stock_code, fiscal_year, fiscal_quarter,
                   CASE WHEN signal_score >= 4 AND quality_flag='ok' THEN 1 ELSE 0 END AS good,
                   0 AS risk,
                   signal_score, 0 AS risk_score
            FROM contract_advance_signals
            WHERE length(stock_code)=6
        """).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT stock_code, fiscal_year, fiscal_quarter, signal_type,
                   CASE WHEN signal_score >= 4 THEN 1 ELSE 0 END AS good,
                   CASE WHEN risk_score >= 4 THEN 1 ELSE 0 END AS risk,
                   signal_score, risk_score
            FROM {table}
            WHERE length(stock_code)=6
        """).fetchall()

    out: dict[str, list[SignalPoint]] = {}
    for r in rows:
        sc = r["stock_code"]
        good = int(r["good"] or 0)
        if good_types is not None and r["signal_type"] not in good_types:
            good = 0
        p = SignalPoint(
            available_date=available_date(r["fiscal_year"], r["fiscal_quarter"]),
            good=good,
            risk=int(r["risk"] or 0),
            score=int(r["signal_score"] or 0),
            risk_score=int(r["risk_score"] or 0),
        )
        out.setdefault(sc, []).append(p)
    for points in out.values():
        points.sort(key=lambda p: p.available_date)
    return out


def lookup(points_by_code: dict[str, list[SignalPoint]], code: str, as_of: str) -> SignalPoint:
    points = points_by_code.get(code) or []
    dates = [p.available_date for p in points]
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return SignalPoint("0000-00-00")
    return points[idx]


def load_order_events(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute("""
        SELECT stock_code, rcept_dt
        FROM order_contracts
        WHERE is_termination=0
          AND rcept_dt IS NOT NULL
          AND COALESCE(revenue_ratio_pct,0) >= 10
          AND length(stock_code)=6
        ORDER BY stock_code, rcept_dt
    """).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["stock_code"], []).append(str(r["rcept_dt"])[:10])
    return out


def has_recent_order(events: dict[str, list[str]], code: str, as_of: str, lookback_days: int = 120) -> int:
    dates = events.get(code) or []
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return 0
    return int((date.fromisoformat(as_of) - date.fromisoformat(dates[idx])).days <= lookback_days)


def summarize(name: str, rows: list[dict]) -> dict:
    valid = [r for r in rows if r["forward_max_ret_12m"] is not None]
    if not valid:
        return {"name": name, "n": 0}
    vals = [float(r["forward_max_ret_12m"] or 0) for r in valid]
    vals6 = [float(r["forward_max_ret_6m"] or 0) for r in valid]
    losses = [v for v in vals if v <= -0.30]
    pos = [v for v in vals if v > 0]
    neg_abs = sum(abs(v) for v in vals if v < 0)
    pos_sum = sum(v for v in vals if v > 0)
    return {
        "name": name,
        "n": len(valid),
        "stocks": len({r["stock_code"] for r in valid}),
        "avg_12m": round(sum(vals) / len(vals), 4),
        "median_12m": round(median(vals), 4),
        "avg_6m": round(sum(vals6) / len(vals6), 4),
        "hit_12m": round(len(pos) / len(vals) * 100, 2),
        "triple_12m": round(sum(int(r["label_3x_12m"] or 0) for r in valid) / len(valid) * 100, 2),
        "double_12m": round(sum(int(r["label_2x_12m"] or 0) for r in valid) / len(valid) * 100, 2),
        "loss30_12m": round(len(losses) / len(valid) * 100, 2),
        "profit_factor_12m": round(pos_sum / neg_abs, 4) if neg_abs > 0 else None,
    }


def monthly_top(enriched: list[dict], score_fn, top_n: int = 20, predicate=None) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        if predicate and not predicate(r):
            continue
        by_month[str(r["snapshot_date"])[:10]].append(r)
    selected: list[dict] = []
    for month, rows in sorted(by_month.items()):
        ranked = sorted(rows, key=lambda r: score_fn(r), reverse=True)
        for r in ranked[:top_n]:
            selected.append({**r, "rank_month": month, "rank_score": score_fn(r)})
    return selected


def main() -> int:
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    snapshots = [dict(r) for r in conn.execute("""
        SELECT *
        FROM strategy_feature_snapshot
        WHERE snapshot_date <= ?
          AND label_3x_12m IS NOT NULL
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    """, (AS_OF_CUTOFF,)).fetchall()]

    advance = load_quarter_signal(conn, "contract_advance_signals")
    inventory = load_quarter_signal(conn, "inventory_sales_signals", {"build_up", "digestion"})
    cash = load_quarter_signal(conn, "cash_conversion_signals", {"cash_quality"})
    orders = load_order_events(conn)
    conn.close()

    enriched: list[dict] = []
    for r in snapshots:
        sc = r["stock_code"]
        as_of = str(r["snapshot_date"])[:10]
        a = lookup(advance, sc, as_of)
        inv = lookup(inventory, sc, as_of)
        cq = lookup(cash, sc, as_of)
        quality_good_count = int(a.good) + int(inv.good) + int(cq.good) + has_recent_order(orders, sc, as_of)
        risk_count = int(inv.risk) + int(cq.risk)
        r = {
            **r,
            "advance_good": int(a.good),
            "inventory_good": int(inv.good),
            "inventory_risk": int(inv.risk),
            "cash_good": int(cq.good),
            "cash_risk": int(cq.risk),
            "order_recent": has_recent_order(orders, sc, as_of),
            "quality_good_count": quality_good_count,
            "quality_risk_count": risk_count,
        }
        enriched.append(r)

    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        by_month[str(r["snapshot_date"])[:10]].append(r)
    for month_rows in by_month.values():
        ranked = sorted(month_rows, key=lambda r: float(r["model_score_12m"] or 0))
        denom = max(len(ranked) - 1, 1)
        for idx, r in enumerate(ranked):
            r["model_rank_pct_12m"] = idx / denom

    rows = []
    rows.append(summarize("baseline_all", enriched))
    rows.append(summarize("any_quality_good", [r for r in enriched if r["quality_good_count"] >= 1]))
    rows.append(summarize("quality_2plus", [r for r in enriched if r["quality_good_count"] >= 2]))
    rows.append(summarize("any_quality_risk", [r for r in enriched if r["quality_risk_count"] >= 1]))
    rows.append(summarize("exclude_quality_risk", [r for r in enriched if r["quality_risk_count"] == 0]))
    rows.append(summarize("quality_good_no_risk", [r for r in enriched if r["quality_good_count"] >= 1 and r["quality_risk_count"] == 0]))
    rows.append(summarize("model_top_decile", [r for r in enriched if r.get("model_rank_pct_12m", 0) >= 0.9]))
    rows.append(summarize("model_top_decile_no_risk", [r for r in enriched if r.get("model_rank_pct_12m", 0) >= 0.9 and r["quality_risk_count"] == 0]))
    rows.append(summarize("model_top_decile_quality", [r for r in enriched if r.get("model_rank_pct_12m", 0) >= 0.9 and r["quality_good_count"] >= 1]))
    rows.append(summarize("model_top_decile_quality_no_risk", [r for r in enriched if r.get("model_rank_pct_12m", 0) >= 0.9 and r["quality_good_count"] >= 1 and r["quality_risk_count"] == 0]))

    for flag in ["advance_good", "inventory_good", "inventory_risk", "cash_good", "cash_risk", "order_recent"]:
        rows.append(summarize(flag, [r for r in enriched if r[flag] == 1]))

    score_model = lambda r: float(r["model_score_12m"] or 0)
    score_quality_balanced = lambda r: (
        float(r["model_score_12m"] or 0)
        + 0.040 * int(r["advance_good"])
        + 0.020 * int(r["order_recent"])
        - 0.025 * int(r["quality_risk_count"])
    )
    score_catalyst_strong = lambda r: (
        float(r["model_score_12m"] or 0)
        + 0.070 * int(r["advance_good"])
        + 0.035 * int(r["order_recent"])
        - 0.020 * int(r["quality_risk_count"])
    )
    score_defensive = lambda r: (
        float(r["model_score_12m"] or 0)
        + 0.020 * int(r["advance_good"])
        + 0.010 * int(r["order_recent"])
        - 0.040 * int(r["quality_risk_count"])
    )
    ranking_rows = [
        summarize("monthly_top20_model", monthly_top(enriched, score_model, 20)),
        summarize("monthly_top20_model_no_risk_pool", monthly_top(enriched, score_model, 20, lambda r: r["quality_risk_count"] == 0)),
        summarize("monthly_top20_quality_balanced", monthly_top(enriched, score_quality_balanced, 20)),
        summarize("monthly_top20_catalyst_strong", monthly_top(enriched, score_catalyst_strong, 20)),
        summarize("monthly_top20_defensive_risk_penalty", monthly_top(enriched, score_defensive, 20)),
        summarize("monthly_top20_catalyst_only_pool", monthly_top(enriched, score_model, 20, lambda r: r["advance_good"] or r["order_recent"])),
        summarize("monthly_top10_model", monthly_top(enriched, score_model, 10)),
        summarize("monthly_top10_quality_balanced", monthly_top(enriched, score_quality_balanced, 10)),
    ]
    rows.extend(ranking_rows)

    OUT_DIR.mkdir(exist_ok=True)
    csv_path = OUT_DIR / "new_quality_factor_validation_20260726.csv"
    json_path = OUT_DIR / "new_quality_factor_validation_20260726.json"
    md_path = OUT_DIR / "new_quality_factor_validation_20260726.md"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["name", "n", "stocks", "avg_12m", "median_12m", "avg_6m", "hit_12m", "triple_12m", "double_12m", "loss30_12m", "profit_factor_12m"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    payload = {"as_of_cutoff": AS_OF_CUTOFF, "rows": rows, "ranking_rows": ranking_rows}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    base = next(r for r in rows if r["name"] == "baseline_all")
    lines = [
        "# New Quality Factor Validation — 2026-07-26",
        "",
        f"- sample rows: {base['n']:,}",
        f"- cutoff: {AS_OF_CUTOFF}",
        "- PIT approximation: quarter-end + 60 days availability lag",
        "",
        "## Summary",
        "",
    ]
    for r in rows:
        if not r.get("n"):
            continue
        delta = r["avg_12m"] - base["avg_12m"] if r["name"] != "baseline_all" else 0
        lines.append(
            f"- {r['name']}: n={r['n']:,}, avg12={r['avg_12m']:.4f} "
            f"(delta {delta:+.4f}), median12={r['median_12m']:.4f}, "
            f"3x={r['triple_12m']:.2f}%, loss30={r['loss30_12m']:.2f}%, PF={r['profit_factor_12m']}"
        )
    lines.extend(["", "## Strategy Center Monthly Ranking Check", ""])
    rank_base = next(r for r in ranking_rows if r["name"] == "monthly_top20_model")
    for r in ranking_rows:
        if not r.get("n"):
            continue
        delta = r["avg_12m"] - rank_base["avg_12m"] if r["name"] != "monthly_top20_model" else 0
        lines.append(
            f"- {r['name']}: n={r['n']:,}, avg12={r['avg_12m']:.4f} "
            f"(vs top20 model {delta:+.4f}), median12={r['median_12m']:.4f}, "
            f"3x={r['triple_12m']:.2f}%, loss30={r['loss30_12m']:.2f}%"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
