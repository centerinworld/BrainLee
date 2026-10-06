#!/usr/bin/env python3
"""Stock_Strategy W3(golden_cross): 기준선(원주가+재기준+인공물 종목 제거) vs 조정 가격 로더, 6구간. 짝을 바로 연달아 실행하고 데이터 지문이 다르면 그 짝만 최대 2회 재실행. 선택·화면 등록 없음."""
from __future__ import annotations
import json, sys, uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_strategies.golden_cross as gc  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402

OUT = ROOT / "research_outputs" / "w3_gc_compare_20261007.json"
PERIODS = {"20.3~21.11": ("2020-03-01", "2021-11-30"), "21.12~22.10": ("2021-12-01", "2022-10-31"),
           "22.11~23.10": ("2022-11-01", "2023-10-31"), "23.11~24.12": ("2023-11-01", "2024-12-31"),
           "24.6~25.5": ("2024-06-01", "2025-05-31"), "25.6~26.3": ("2025-06-01", "2026-03-31")}
WATCH = {"077500", "196170", "052710"}


def one(label, adjusted):
    s, e = PERIODS[label]
    rid = str(uuid.uuid4())[:8]
    name = f"w3_gc {'adj' if adjusted else 'base'} {label}"
    c = connect_stock_db()
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES(?,?,'golden_cross',?,?,?,'running')", (rid, name, s, e, 10_000_000))
    c.commit(); c.close()
    gc.run_backtest_golden_cross(s, e, adjusted_prices=adjusted, run_name=name, run_id=rid)
    c = connect_stock_db(readonly=True)
    r = c.execute("SELECT total_return_pct,max_drawdown_pct,total_trades,trades_json,summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    sp = c.execute("SELECT run_hash,parameter_json FROM backtest_run_specs WHERE run_id=?", (rid,)).fetchone()
    c.close()
    tr = json.loads(r[3] or "[]"); tr = tr.get("trades", []) if isinstance(tr, dict) else tr
    pj = json.loads(sp[1]) if sp and sp[1] else {}
    stats = None
    for line in (r[4] or "").split("\n"):
        if line.startswith("조정가격(W3):"):
            stats = json.loads(line.split(":", 1)[1])
    return {"run_id": rid, "ret": r[0], "mdd": r[1], "trades": r[2], "stats": stats,
            "data_fp": {"snap": (pj.get("_source_snapshot") or {}).get("fingerprint"), "extras": pj.get("_data_revision_extras")},
            "watch": [{k: t.get(k) for k in ("stock_code", "entry_date", "exit_date", "exit_reason", "profit_pct")} for t in tr if t["stock_code"] in WATCH],
            "unevaluable": [{k: t.get(k) for k in ("stock_code", "entry_date", "exit_date", "profit_pct", "basis_date")} for t in tr if t.get("evaluation") == "unevaluable_break"],
            "break_exits": sum(1 for t in tr if str(t.get("exit_reason", "")).startswith("단절"))}


def main():
    res = {"started": datetime.now().isoformat(timespec="seconds"), "periods": {}}
    for label in PERIODS:
        for attempt in range(3):
            b = one(label, False); a = one(label, True)
            ok = b["data_fp"] == a["data_fp"]
            if ok or attempt == 2:
                break
        res["periods"][label] = {"baseline": b, "adjusted": a, "valid": ok, "attempts": attempt + 1}
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(label, "기준선", b["ret"], b["trades"], "조정", a["ret"], a["trades"], "유효" if ok else "무효", flush=True)
    res["finished"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
