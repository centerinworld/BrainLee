#!/usr/bin/env python3
"""Stock_Strategy W3(v12): 기준선(원주가+재기준 방식) vs 조정 가격 로더 — 점수 순 1회 + 무작위 순서 N회 분포, 6구간. 선택·화면 등록 없음.
결과: research_outputs/w3_v12_compare_20261007.json. 사용: python3 scripts/rerun_w3_v12_compare_20261007.py [--random 12]
"""
from __future__ import annotations
import argparse, json, statistics as st, sys, uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
import backtest_strategies.v12 as v12  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402

OUT = ROOT / "research_outputs" / "w3_v12_compare_20261007.json"
PERIODS = {"20.3~21.11": ("2020-03-01", "2021-11-30"), "21.12~22.10": ("2021-12-01", "2022-10-31"),
           "22.11~23.10": ("2022-11-01", "2023-10-31"), "23.11~24.12": ("2023-11-01", "2024-12-31"),
           "24.6~25.5": ("2024-06-01", "2025-05-31"), "25.6~26.3": ("2025-06-01", "2026-03-31")}
WATCH = {"077500": "인적분할 2023-10-23", "196170": "무상증자 2020-07-23", "052710": "무상증자 2023-02-24"}


def one(label, order, adjusted):
    s, e = PERIODS[label]
    rid = str(uuid.uuid4())[:8]
    name = f"w3_v12 {'adj' if adjusted else 'base'} {order} {label}"
    c = connect_stock_db()
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES(?,?,'v12',?,?,?,'running')",
              (rid, name, s, e, 10_000_000))
    c.commit(); c.close()
    v12.run_backtest_v12(s, e, selection_order=order, adjusted_prices=adjusted, run_name=name, run_id=rid)
    c = connect_stock_db(readonly=True)
    r = c.execute("SELECT total_return_pct,max_drawdown_pct,total_trades,trades_json,summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    sp = c.execute("SELECT run_hash,parameter_json FROM backtest_run_specs WHERE run_id=?", (rid,)).fetchone()
    c.close()
    p = json.loads(r[3] or "[]"); tr = p.get("trades", []) if isinstance(p, dict) else p
    pj = json.loads(sp[1]) if sp and sp[1] else {}
    stats = None
    for line in (r[4] or "").split("\n"):
        if line.startswith("조정가격(W3):"):
            stats = json.loads(line.split(":", 1)[1])
    watch = [{k: t.get(k) for k in ("stock_code", "entry_date", "exit_date", "exit_reason", "profit_pct", "entry_price_raw", "exit_price_raw")}
             for t in tr if t["stock_code"] in WATCH]
    return {"run_id": rid, "ret": r[0], "mdd": r[1], "trades": r[2], "stats": stats, "watch": watch,
            "data_fp": {"snap": (pj.get("_source_snapshot") or {}).get("fingerprint"), "extras": pj.get("_data_revision_extras")},
            "break_exits": sum(1 for t in tr if str(t.get("exit_reason", "")).startswith("단절"))}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--random", type=int, default=12); a = ap.parse_args()
    res = {"started": datetime.now().isoformat(timespec="seconds"), "periods": {}}
    for label in PERIODS:
        item = {}
        for mode, flag in (("baseline", False), ("adjusted", True)):
            item[mode] = {"score": one(label, "score", flag)}
            item[mode]["random"] = [one(label, f"random:{k}", flag) for k in range(a.random)]
        res["periods"][label] = item
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        for mode in ("baseline", "adjusted"):
            rr = [x["ret"] for x in item[mode]["random"]]
            print(label, mode, "score", item[mode]["score"]["ret"], "random median", round(st.median(rr), 2), "p25", round(sorted(rr)[len(rr)//4], 2), "sd", round(st.pstdev(rr), 2), flush=True)
    res["finished"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
