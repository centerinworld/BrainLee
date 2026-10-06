#!/usr/bin/env python3
"""docs/Stock_Strategy.md S27 후속 — v12 점수 순 선택이 무작위 선택보다 나은가(무작위 순서 분포 대비 백분위),
그리고 '후보가 빈 자리보다 많은 날'이 얼마나 자주 있는가.

- DB에 run을 남기지 않는다(내부 함수 직접 호출, 읽기만). 결과: research_outputs/v12_selection_random_20261006.json
- 구간·파라미터는 선택 suite(strategy_center)와 같다.
사용: python3 scripts/v12_selection_random_test_20261006.py [--seeds 20]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_strategies.v12 as v12  # noqa: E402
from backtest_common import DB_PATH, _calc_metrics, sqlite3  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402

OUT = ROOT / "research_outputs" / "v12_selection_random_20261006.json"


def _one(conn, spec: dict, order: str) -> dict:
    p = spec["parameters"]
    start, end = spec["start"], spec["end"]
    per_stock, max_pos = float(spec["per_stock"]), int(p.get("max_positions", 10))
    warm = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=450)).strftime("%Y-%m-%d")
    sim = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0 ORDER BY date",
        (start, end)).fetchall()]
    trades, eq, _ = v12._run_backtest_v12(conn, warm, start, end, sim, per_stock, max_pos,
                                          stop_loss=-0.07, stop_loss_pct=-0.07,
                                          take_profit_pct=p.get("take_profit_pct", 0.25),
                                          asof_mktcap=p.get("asof_mktcap", False), selection_order=order)
    m = _calc_metrics(trades, eq, start, end, per_stock * max_pos)
    st = list(v12.LAST_SELECTION_STATS)
    return {"ret": m.get("total_return_pct"), "mdd": m.get("max_drawdown_pct"), "trades": len(trades),
            "stats": {"days_with_cands": len(st),
                      "days_cands_gt_free": sum(1 for _, n, f in st if n > f),
                      "days_no_free_slot": sum(1 for _, n, f in st if f == 0),
                      "median_cands": statistics.median([n for _, n, _ in st]) if st else 0,
                      "median_free": statistics.median([f for _, _, f in st]) if st else 0,
                      "max_cands": max([n for _, n, _ in st], default=0)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--period", default="", help="한 구간만(병렬 실행용) — 결과는 OUT 이름에 구간 접미사")
    a = ap.parse_args()
    specs = sorted(_all_selected_specs({"v12"})["v12"], key=lambda s: s["start"])
    global OUT
    if a.period:
        specs = [s for s in specs if s["label"] == a.period]
        OUT = OUT.with_name(OUT.stem + "_" + a.period.replace("~", "-").replace(".", "") + ".json")
    conn = sqlite3.connect(DB_PATH, timeout=300)
    out = {"started_at": datetime.now().isoformat(timespec="seconds"), "seeds": a.seeds, "periods": {}}
    for spec in specs:
        lab = spec["label"]
        row = {"score": _one(conn, spec, "score"), "code": _one(conn, spec, "code"), "random": []}
        for s in range(a.seeds):
            r = _one(conn, spec, f"random:{s}")
            row["random"].append({"seed": s, "ret": r["ret"], "mdd": r["mdd"], "trades": r["trades"]})
        rets = sorted(x["ret"] for x in row["random"])
        row["random_summary"] = {"min": rets[0], "median": statistics.median(rets), "max": rets[-1],
                                 "stdev": round(statistics.pstdev(rets), 2),
                                 "score_percentile": round(100 * sum(1 for x in rets if x < row["score"]["ret"]) / len(rets), 1)}
        out["periods"][lab] = row
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(lab, "score", row["score"]["ret"], "random", row["random_summary"], "stats", row["score"]["stats"], flush=True)
    out["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
