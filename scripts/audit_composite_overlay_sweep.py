#!/usr/bin/env python3
"""Compare composite overlay experiments under identical reproducibility contracts."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from db_compat import connect_primary_db


WINDOWS = (
    ("20.3~21.11", "2020-03-01", "2021-11-30"),
    ("21.12~22.10", "2021-12-01", "2022-10-31"),
    ("22.11~23.10", "2022-11-01", "2023-10-31"),
    ("23.11~24.12", "2023-11-01", "2024-12-31"),
    ("24.6~25.5", "2024-06-01", "2025-05-31"),
    ("25.6~26.3", "2025-06-01", "2026-03-31"),
)
WINDOW_LABELS = {(start, end): label for label, start, end in WINDOWS}
OVERLAY_KEYS = (
    "dynamic_score_gate",
    "dynamic_score_boost",
    "vol_scale_gate",
    "vol_scale_factor",
    "vol_scale_lookback",
    "vol_scale_threshold",
    "vol_scale_smooth",
)


def _overlay(params: dict) -> dict:
    """Only fields changed by the two overlay hypotheses; normalize absent flags."""
    values = {key: params.get(key) for key in OVERLAY_KEYS}
    values["dynamic_score_gate"] = bool(values["dynamic_score_gate"])
    values["vol_scale_gate"] = bool(values["vol_scale_gate"])
    return values


def _identity(params: dict) -> tuple[str, str]:
    snapshot = params.get("_source_snapshot", {}).get("fingerprint", "missing")
    code = params.get("_code_fingerprint", {})
    # The common and composite implementations determine the tested behavior.
    code_id = json.dumps(
        {key: code.get(key) for key in ("backtest_common.py", "backtest_strategies/composite.py")},
        sort_keys=True,
    )
    return snapshot, code_id


def main() -> None:
    conn = connect_primary_db(timeout=60, readonly=True)
    conn.row_factory = None
    rows = conn.execute(
        """
        SELECT r.id,r.name,r.start_date,r.end_date,r.total_return_pct,r.total_trades,
               r.max_drawdown_pct,r.created_at,s.parameter_json
        FROM backtest_runs r
        JOIN backtest_run_specs s ON s.run_id=r.run_id
        WHERE r.strategy='composite' AND r.status='done'
          AND (r.start_date,r.end_date) IN (
              ('2020-03-01','2021-11-30'),('2021-12-01','2022-10-31'),
              ('2022-11-01','2023-10-31'),('2023-11-01','2024-12-31'),
              ('2024-06-01','2025-05-31'),('2025-06-01','2026-03-31')
          )
        ORDER BY r.id DESC
        """
    ).fetchall()
    conn.close()

    groups: dict[tuple, dict] = {}
    for row in rows:
        params = json.loads(row[8] or "{}")
        overlay = _overlay(params)
        # Exclude unrelated overlays: this audit is narrowly about Claude's two hypotheses.
        if params.get("use_market_filter") or params.get("fast_crash_gate"):
            continue
        snapshot, code_id = _identity(params)
        key = (snapshot, code_id, json.dumps(overlay, sort_keys=True))
        group = groups.setdefault(key, {"overlay": overlay, "runs": {}})
        period = WINDOW_LABELS[(str(row[2]), str(row[3]))]
        # Latest run for an identical period/contract wins; duplicates are re-executions.
        group["runs"].setdefault(period, {
            "id": row[0], "name": row[1], "return_pct": float(row[4]),
            "trades": int(row[5] or 0), "mdd_pct": float(row[6] or 0), "created_at": str(row[7]),
        })

    complete = []
    for (snapshot, code_id, _), group in groups.items():
        if set(group["runs"]) != {item[0] for item in WINDOWS}:
            continue
        ordered = [group["runs"][label] for label, *_ in WINDOWS]
        returns = [item["return_pct"] for item in ordered]
        group.update({
            "source_snapshot": snapshot,
            "code_identity": json.loads(code_id),
            "periods": {label: run for (label, *_), run in zip(WINDOWS, ordered)},
            "mean_return_pct": round(sum(returns) / len(returns), 2),
            "median_return_pct": round(sorted(returns)[len(returns) // 2 - 1:len(returns) // 2 + 1][0] / 2 + sorted(returns)[len(returns) // 2 - 1:len(returns) // 2 + 1][1] / 2, 2),
            "worst_return_pct": round(min(returns), 2),
            "total_trades": sum(item["trades"] for item in ordered),
        })
        complete.append(group)

    # A complete configuration must have one fixed implementation across all six windows.
    # Different configurations may legitimately have different composite fingerprints because
    # their overlay branch was introduced during the experiment; expose that limitation.
    cohorts = defaultdict(list)
    for group in complete:
        cohorts[group["source_snapshot"]].append(group)
    cohort_key, cohort = max(cohorts.items(), key=lambda item: len(item[1]), default=(None, []))
    baseline = next((item for item in cohort if not item["overlay"]["dynamic_score_gate"] and not item["overlay"]["vol_scale_gate"]), None)
    for item in cohort:
        item["delta_vs_baseline_pct"] = (
            round(item["mean_return_pct"] - baseline["mean_return_pct"], 2) if baseline else None
        )
        if baseline:
            item["period_deltas_pct"] = {
                label: round(item["periods"][label]["return_pct"] - baseline["periods"][label]["return_pct"], 2)
                for label, *_ in WINDOWS
            }

    cohort.sort(key=lambda item: item["mean_return_pct"], reverse=True)
    output = {
        "generated_on": date.today().isoformat(),
        "purpose": "Paired audit of composite dynamic-score and volatility-scale overlays.",
        "contract": {
            "windows": [label for label, *_ in WINDOWS],
            "same_source_snapshot": True,
            "each_configuration_has_one_fixed_common_and_composite_code": True,
            "excluded_other_overlays": ["use_market_filter", "fast_crash_gate"],
        },
        "selected_cohort": {"source_snapshot": cohort_key} if cohort_key else None,
        "complete_configurations": cohort,
        "interpretation": (
            "A negative mean delta rejects an overlay as a default under this fixed contract; "
            "it does not prove that every market-risk control is invalid."
        ),
    }
    out_dir = Path("research_outputs")
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / f"composite_overlay_audit_{date.today():%Y%m%d}.json"
    md_path = out_dir / f"composite_overlay_audit_{date.today():%Y%m%d}.md"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    distinct_code_count = len({json.dumps(item["code_identity"], sort_keys=True) for item in cohort})
    lines = ["# Composite Overlay Audit", "", f"Cohort: `{cohort_key or 'none'}`", f"Distinct composite/common code fingerprints: `{distinct_code_count}`", "", "| Overlay | Mean | Median | Worst | Trades | Delta vs baseline |", "|---|---:|---:|---:|---:|---:|"]
    for item in cohort:
        overlay = ", ".join(f"{key}={value}" for key, value in item["overlay"].items() if value not in (None, False)) or "baseline"
        lines.append(f"| {overlay} | {item['mean_return_pct']:.2f}% | {item['median_return_pct']:.2f}% | {item['worst_return_pct']:.2f}% | {item['total_trades']} | {item['delta_vs_baseline_pct']:+.2f}%p |")
    lines.extend(["", "Each row has a stable implementation over its six windows. Where fingerprints differ across rows, the delta is directional only, not a definitive paired comparison; rerun every candidate with the final common code before selecting a default."])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "cohort_size": len(cohort), "baseline_found": bool(baseline)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
