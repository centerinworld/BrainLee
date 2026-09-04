#!/usr/bin/env python3
"""Sweep DART quality/catalyst overlays for Strategy Center ranking.

Goal:
- Treat newly collected factors as possible score overlays.
- Select combinations by train period only.
- Verify whether they improve out-of-sample monthly top picks.

This intentionally uses strategy_feature_snapshot labels, so it is a ranking
research screen before full execution backtests.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from itertools import product
from pathlib import Path
from statistics import median

from research_new_quality_factor_validation import (
    AS_OF_CUTOFF,
    DB,
    OUT_DIR,
    has_recent_order,
    load_order_events,
    load_quarter_signal,
    lookup,
    summarize,
)


TRAIN_END = "2024-06-30"


def monthly_top(rows: list[dict], score_fn, top_n: int = 20, predicate=None) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if predicate and not predicate(r):
            continue
        by_month[str(r["snapshot_date"])[:10]].append(r)
    selected: list[dict] = []
    for month, month_rows in sorted(by_month.items()):
        ranked = sorted(month_rows, key=lambda r: score_fn(r), reverse=True)
        selected.extend({**r, "rank_month": month, "rank_score": score_fn(r)} for r in ranked[:top_n])
    return selected


def reduce_candidate_rows(rows: list[dict], keep_per_month: int = 300) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_month[str(r["snapshot_date"])[:10]].append(r)
    out: list[dict] = []
    for month_rows in by_month.values():
        out.extend(sorted(
            month_rows,
            key=lambda r: (
                float(r["model_score_12m"] or 0)
                + 0.15 * int(r["advance_good"])
                + 0.08 * int(r["order_recent"])
                + 0.02 * int(r["cash_good"])
                + 0.02 * int(r["inventory_good"])
            ),
            reverse=True,
        )[:keep_per_month])
    return out


def enrich_snapshots() -> list[dict]:
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

    enriched = []
    for r in snapshots:
        sc = r["stock_code"]
        as_of = str(r["snapshot_date"])[:10]
        a = lookup(advance, sc, as_of)
        inv = lookup(inventory, sc, as_of)
        cq = lookup(cash, sc, as_of)
        order_recent = has_recent_order(orders, sc, as_of)
        r = {
            **r,
            "advance_good": int(a.good),
            "inventory_good": int(inv.good),
            "inventory_risk": int(inv.risk),
            "cash_good": int(cq.good),
            "cash_risk": int(cq.risk),
            "order_recent": int(order_recent),
            "quality_risk_count": int(inv.risk) + int(cq.risk),
        }
        enriched.append(r)
    return enriched


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if str(r["snapshot_date"])[:10] <= TRAIN_END]
    test = [r for r in rows if str(r["snapshot_date"])[:10] > TRAIN_END]
    return train, test


def score_fn(weights: dict[str, float]):
    def _score(r: dict) -> float:
        return (
            float(r["model_score_12m"] or 0)
            + weights["advance"] * int(r["advance_good"])
            + weights["order"] * int(r["order_recent"])
            + weights["cash_good"] * int(r["cash_good"])
            + weights["inventory_good"] * int(r["inventory_good"])
            - weights["risk"] * int(r["quality_risk_count"])
        )
    return _score


def objective(metric: dict, baseline: dict) -> float:
    if not metric.get("n"):
        return -999
    return (
        (metric["avg_12m"] - baseline["avg_12m"]) * 100
        + (metric["triple_12m"] - baseline["triple_12m"]) * 0.8
        + (metric["median_12m"] - baseline["median_12m"]) * 40
        - (metric["loss30_12m"] - baseline["loss30_12m"]) * 1.5
    )


def run_sweep(top_n: int = 20) -> dict:
    rows = enrich_snapshots()
    train, test = split_rows(rows)
    train = reduce_candidate_rows(train)
    test = reduce_candidate_rows(test)
    base_score = score_fn({"advance": 0, "order": 0, "cash_good": 0, "inventory_good": 0, "risk": 0})
    base_train = summarize(f"baseline_top{top_n}_train", monthly_top(train, base_score, top_n))
    base_test = summarize(f"baseline_top{top_n}_test", monthly_top(test, base_score, top_n))

    weight_grid = {
        "advance": [0, 0.03, 0.06, 0.10, 0.15],
        "order": [0, 0.015, 0.035, 0.06],
        "cash_good": [-0.02, 0, 0.01],
        "inventory_good": [-0.02, 0, 0.01],
        "risk": [0, 0.02, 0.04, 0.07],
    }
    predicates = {
        "none": None,
        "no_risk_pool": lambda r: r["quality_risk_count"] == 0,
        "has_catalyst_pool": lambda r: r["advance_good"] or r["order_recent"],
        "model_or_catalyst_no_risk": lambda r: r["quality_risk_count"] == 0 or r["advance_good"] or r["order_recent"],
    }

    results = []
    keys = list(weight_grid)
    for values in product(*(weight_grid[k] for k in keys)):
        weights = dict(zip(keys, values))
        fn = score_fn(weights)
        for pred_name, pred in predicates.items():
            train_sel = monthly_top(train, fn, top_n, pred)
            test_sel = monthly_top(test, fn, top_n, pred)
            if len(train_sel) < top_n * 12 or len(test_sel) < top_n * 3:
                continue
            train_m = summarize(f"{pred_name}_train", train_sel)
            test_m = summarize(f"{pred_name}_test", test_sel)
            results.append({
                "top_n": top_n,
                "predicate": pred_name,
                **{f"w_{k}": weights[k] for k in keys},
                "train_objective": round(objective(train_m, base_train), 4),
                "test_objective": round(objective(test_m, base_test), 4),
                "train_avg12": train_m["avg_12m"],
                "train_median12": train_m["median_12m"],
                "train_3x": train_m["triple_12m"],
                "train_loss30": train_m["loss30_12m"],
                "test_avg12": test_m["avg_12m"],
                "test_median12": test_m["median_12m"],
                "test_3x": test_m["triple_12m"],
                "test_loss30": test_m["loss30_12m"],
                "test_n": test_m["n"],
                "test_stocks": test_m["stocks"],
            })
    ranked_by_train = sorted(results, key=lambda r: r["train_objective"], reverse=True)
    robust = [
        r for r in ranked_by_train
        if r["test_avg12"] >= base_test["avg_12m"]
        and r["test_3x"] >= base_test["triple_12m"]
        and r["test_loss30"] <= base_test["loss30_12m"] + 0.2
    ]
    return {
        "train_end": TRAIN_END,
        "as_of_cutoff": AS_OF_CUTOFF,
        "top_n": top_n,
        "baseline_train": base_train,
        "baseline_test": base_test,
        "best_by_train": ranked_by_train[:30],
        "robust_candidates": robust[:30],
        "result_count": len(results),
    }


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    payload = {"runs": [run_sweep(20), run_sweep(10)]}
    json_path = OUT_DIR / "quality_overlay_sweep_20260726.json"
    csv_path = OUT_DIR / "quality_overlay_sweep_20260726.csv"
    md_path = OUT_DIR / "quality_overlay_sweep_20260726.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for run in payload["runs"]:
        for group in ("best_by_train", "robust_candidates"):
            for r in run[group]:
                rows.append({"group": group, **r})
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    lines = ["# Quality Overlay Sweep — 2026-07-26", ""]
    for run in payload["runs"]:
        bt = run["baseline_test"]
        lines.extend([
            f"## Monthly Top{run['top_n']}",
            "",
            f"- baseline test avg12: {bt['avg_12m']:.4f}",
            f"- baseline test median12: {bt['median_12m']:.4f}",
            f"- baseline test 3x: {bt['triple_12m']:.2f}%",
            f"- baseline test loss30: {bt['loss30_12m']:.2f}%",
            f"- swept combinations: {run['result_count']:,}",
            "",
            "### Robust Candidates",
            "",
        ])
        if not run["robust_candidates"]:
            lines.append("- None passed avg12 + 3x + loss guardrails out-of-sample.")
        for r in run["robust_candidates"][:10]:
            lines.append(
                f"- {r['predicate']} weights(a={r['w_advance']}, order={r['w_order']}, "
                f"cash={r['w_cash_good']}, inv={r['w_inventory_good']}, risk={r['w_risk']}): "
                f"test avg12={r['test_avg12']:.4f}, 3x={r['test_3x']:.2f}%, "
                f"loss30={r['test_loss30']:.2f}%, objective={r['test_objective']:.2f}"
            )
        lines.extend(["", "### Best By Train", ""])
        for r in run["best_by_train"][:5]:
            lines.append(
                f"- {r['predicate']} weights(a={r['w_advance']}, order={r['w_order']}, "
                f"cash={r['w_cash_good']}, inv={r['w_inventory_good']}, risk={r['w_risk']}): "
                f"train obj={r['train_objective']:.2f}, test avg12={r['test_avg12']:.4f}, "
                f"test 3x={r['test_3x']:.2f}%, test loss30={r['test_loss30']:.2f}%"
            )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
