#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import research_3x_capture_filter_logic as capture  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "three_x_high_precision_logic_2021plus.json"


def _bool(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


def condition_map(panel: pd.DataFrame) -> dict[str, pd.Series]:
    cond: dict[str, pd.Series] = {}
    cond["KOSDAQ"] = _bool(panel["kosdaq"])
    cond["core_sector"] = _bool(panel["core_sector"])
    cond["small_cap_3000"] = _bool(panel["small_cap_3000"])
    cond["amount_8x"] = panel["amount_peak_x"].fillna(0) >= 8
    cond["amount_10x"] = panel["amount_peak_x"].fillna(0) >= 10
    cond["amount_15x"] = panel["amount_peak_x"].fillna(0) >= 15
    cond["amount_20x"] = panel["amount_peak_x"].fillna(0) >= 20
    cond["volume_10x"] = panel["vol_peak_x"].fillna(0) >= 10
    cond["volume_15x"] = panel["vol_peak_x"].fillna(0) >= 15
    cond["volume_20x"] = panel["vol_peak_x"].fillna(0) >= 20
    cond["ma60_reclaim"] = _bool(panel["ma60_reclaim"])
    cond["new_60d_high"] = _bool(panel["new_60d_high"])
    cond["follow20_15pct"] = panel["follow20_ret"].fillna(0) >= 0.15
    cond["follow20_30pct"] = panel["follow20_ret"].fillna(0) >= 0.30
    cond["follow20_50pct"] = panel["follow20_ret"].fillna(0) >= 0.50
    cond["inst_20d_buy"] = _bool(panel["inst_20d_buy"])
    cond["frn_20d_buy"] = _bool(panel["frn_20d_buy"])
    cond["both_inst_frn_buy"] = _bool(panel["both_inst_frn_buy"])
    cond["contract_signal"] = _bool(panel["contract_signal"])
    cond["overseas_contract"] = _bool(panel["overseas_contract"])
    cond["export_yoy_30"] = _bool(panel["export_yoy_30"])
    cond["export_yoy_100"] = _bool(panel["export_yoy_100"])
    cond["revenue_yoy_pos"] = _bool(panel["revenue_yoy_pos"])
    cond["revenue_yoy_15"] = _bool(panel["revenue_yoy_15"])
    cond["op_profit_pos"] = _bool(panel["op_profit_pos"])
    cond["op_turnaround"] = _bool(panel["op_turnaround"])
    cond["backlog_growth"] = _bool(panel["backlog_growth"])
    cond["employee_growth"] = _bool(panel["employee_growth"])
    cond["material_purchase_growth"] = _bool(panel["material_purchase_growth"])
    cond["short_cover"] = _bool(panel["short_cover"])
    cond["no_short_pressure"] = ~_bool(panel["short_pressure"])
    cond["debt_le_500_or_unknown"] = ~_bool(panel["debt_over_500"])
    cond["debt_le_200_or_unknown"] = panel["prev_debt_ratio"].isna() | (panel["prev_debt_ratio"] <= 200)
    cond["not_capital_impaired"] = ~_bool(panel["capital_impaired"])
    cond["no_risk_disclosure"] = ~_bool(panel["risk_disclosure_before_low"])
    return cond


def eval_mask(panel: pd.DataFrame, mask: pd.Series, name: str, winners_total: int) -> dict | None:
    n = int(mask.sum())
    if n <= 0:
        return None
    s = panel[mask]
    w = int(s["winner"].sum())
    return {
        "name": name,
        "count": n,
        "winner_count": w,
        "winner_rate_pct": round(w / n * 100, 2),
        "winner_capture_pct": round(w / winners_total * 100, 2) if winners_total else 0.0,
        "median_multiple": capture.base._num(s["multiple"].median(), 2),
        "avg_multiple": capture.base._num(s["multiple"].mean(), 2),
    }


def main() -> int:
    panel = capture.build_panel()
    winners_total = int(panel["winner"].sum())
    base_rate = float(panel["winner"].mean() * 100)
    cond = condition_map(panel)

    min_count = 40
    rows: list[dict] = []

    # Hand-built shapes first: pulse + trend + quality + risk-off.
    shapes = {
        "amount15_volume15_follow30_kosdaq_riskoff": (
            cond["amount_15x"] & cond["volume_15x"] & cond["follow20_30pct"] & cond["KOSDAQ"]
            & cond["debt_le_500_or_unknown"] & cond["not_capital_impaired"]
        ),
        "amount20_follow30_core_riskoff": (
            cond["amount_20x"] & cond["follow20_30pct"] & cond["core_sector"]
            & cond["debt_le_500_or_unknown"] & cond["not_capital_impaired"]
        ),
        "amount10_volume10_newhigh_follow30": (
            cond["amount_10x"] & cond["volume_10x"] & cond["new_60d_high"] & cond["follow20_30pct"]
        ),
        "amount10_newhigh_follow50": (
            cond["amount_10x"] & cond["new_60d_high"] & cond["follow20_50pct"]
        ),
        "amount15_follow30_instbuy": (
            cond["amount_15x"] & cond["follow20_30pct"] & cond["inst_20d_buy"]
        ),
        "amount10_follow30_contract": (
            cond["amount_10x"] & cond["follow20_30pct"] & cond["contract_signal"]
        ),
    }
    for name, mask in shapes.items():
        r = eval_mask(panel, mask, name, winners_total)
        if r and r["count"] >= min_count:
            rows.append({**r, "kind": "manual"})

    search_names = [
        "KOSDAQ", "core_sector", "small_cap_3000",
        "amount_10x", "amount_15x", "amount_20x",
        "volume_10x", "volume_15x", "volume_20x",
        "ma60_reclaim", "new_60d_high",
        "follow20_15pct", "follow20_30pct", "follow20_50pct",
        "inst_20d_buy", "frn_20d_buy", "both_inst_frn_buy",
        "contract_signal", "overseas_contract",
        "export_yoy_30", "revenue_yoy_15", "op_profit_pos",
        "short_cover", "no_short_pressure",
        "debt_le_500_or_unknown", "not_capital_impaired",
    ]
    for k in range(2, 6):
        for combo in combinations(search_names, k):
            # Avoid redundant threshold combinations where the stricter one implies the looser one.
            cset = set(combo)
            if {"amount_10x", "amount_15x"} <= cset or {"amount_10x", "amount_20x"} <= cset or {"amount_15x", "amount_20x"} <= cset:
                continue
            if {"volume_10x", "volume_15x"} <= cset or {"volume_10x", "volume_20x"} <= cset or {"volume_15x", "volume_20x"} <= cset:
                continue
            if {"follow20_15pct", "follow20_30pct"} <= cset or {"follow20_15pct", "follow20_50pct"} <= cset or {"follow20_30pct", "follow20_50pct"} <= cset:
                continue
            mask = pd.Series(True, index=panel.index)
            for name in combo:
                mask &= cond[name]
            if int(mask.sum()) < min_count:
                continue
            r = eval_mask(panel, mask, " AND ".join(combo), winners_total)
            if r:
                rows.append({**r, "kind": f"combo_{k}"})

    result = sorted(
        rows,
        key=lambda r: (r["winner_rate_pct"], r["winner_count"], r["count"]),
        reverse=True,
    )
    # Keep diverse rows: identical masks often appear through redundant descriptors.
    seen = set()
    top = []
    for r in result:
        key = (r["count"], r["winner_count"], r["winner_rate_pct"])
        if key in seen:
            continue
        seen.add(key)
        top.append(r)
        if len(top) >= 80:
            break

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {
            "years": "2021-2026",
            "unit": "stock-year low-date cohort",
            "sample_count": int(len(panel)),
            "winner_count": winners_total,
            "base_winner_rate_pct": round(base_rate, 2),
            "min_count": min_count,
            "note": "고승률 탐색용. 저점 이후 신호 기반이라 실전 적용 전 신호 발생일 기준 백테스트가 필요합니다.",
        },
        "top_rules": top,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "scope": payload["scope"], "top_rules": top[:25]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
