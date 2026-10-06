#!/usr/bin/env python3
"""Stock_Strategy W2: 공용 일반 엔진(_run_generic_backtest)을 쓰는 전략을 같은 세션에서 ① 기준선(원주가) ② 조정 가격 로더 순으로 6구간 실행해 비교한다.
선택(select)·화면 등록 없음. 결과: research_outputs/w2_generic_compare_20261006.json
사용: python3 scripts/rerun_w2_generic_compare_20261006.py [--strategies v_trend,v2] [--workers 3]
"""
from __future__ import annotations
import argparse, hashlib, inspect, json, sys, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest as bt  # noqa: E402
import backtest_common as bc  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
from scripts.rerun_selected_after_price_repair import FUNCTIONS, _price_integrity  # noqa: E402

OUT = ROOT / "research_outputs" / "w2_generic_compare_20261006.json"
GENERIC = ["v_trend", "v1_value", "v2", "v5", "v10", "v11", "vbr"]


def run_one(strategy, spec, mode):
    fn = getattr(bt, FUNCTIONS[strategy])
    acc = set(inspect.signature(fn).parameters)
    params = {k: v for k, v in spec["parameters"].items()
              if k in acc and k not in {"start_date", "end_date", "start", "end", "run_id", "run_name"}}
    if "per_stock" in acc:
        params["per_stock"] = spec["per_stock"]
    run_id = str(uuid.uuid4())[:8]
    name = f"w2_{mode} {strategy} {spec['label']}"
    c = connect_stock_db()
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES(?,?,?,?,?,?,'running')",
              (run_id, name, strategy, spec["start"], spec["end"], spec["per_stock"]))
    c.commit(); c.close()
    fn(spec["start"], spec["end"], run_name=name, run_id=run_id, **params)
    c = connect_stock_db(readonly=True)
    r = c.execute("SELECT total_return_pct,max_drawdown_pct,total_trades,trades_json,summary_text,status FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    c.close()
    payload = json.loads(r[3] or "[]"); trades = payload.get("trades", []) if isinstance(payload, dict) else payload
    sig = hashlib.sha1(json.dumps(sorted([(t["stock_code"], t["entry_date"], t["exit_date"], round(t["profit_amt"])) for t in trades]),
                                  sort_keys=True).encode()).hexdigest()[:12]
    try:
        ok, det = _price_integrity(run_id, spec["end"], strategy=strategy)
        gate = {"passed": ok, "contaminated": len(det["contaminated_events"])}
    except Exception as e:  # noqa
        gate = {"error": str(e)[:80]}
    stats = None
    for line in (r[4] or "").split("\n"):
        if line.startswith("조정가격(W2):"):
            stats = json.loads(line.split(":", 1)[1])
    return {"run_id": run_id, "ret": r[0], "mdd": r[1], "trades": r[2], "status": r[5], "trade_sig": sig, "gate": gate,
            "break_exits": sum(1 for t in trades if t.get("exit_reason", "").startswith("단절 전 청산")), "adj_stats": stats,
            "keys": sorted((t["stock_code"], t["entry_date"]) for t in trades)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default=",".join(GENERIC)); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--repeat", action="store_true")
    a = ap.parse_args()
    only = {x for x in a.strategies.split(",") if x}
    specs = _all_selected_specs(only)
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    res["session_started"] = res.get("session_started") or datetime.now().isoformat(timespec="seconds")
    res.setdefault("strategies", {})
    for mode, flag in (("baseline", False), ("adjusted", True)):
        bc.ADJUSTED_PRICES_DEFAULT = flag
        for strategy, items in specs.items():
            with ThreadPoolExecutor(max_workers=a.workers) as pool:
                futs = {s["label"]: pool.submit(run_one, strategy, s, mode) for s in items}
                runs = {k: f.result() for k, f in futs.items()}
            res["strategies"].setdefault(strategy, {})[mode] = runs
            OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            print(mode, strategy, {k: (v["ret"], v["trades"]) for k, v in sorted(runs.items())}, flush=True)
        if mode == "adjusted" and a.repeat:
            strategy = sorted(specs)[0]; s = specs[strategy][0]
            again = run_one(strategy, s, "adjusted_repeat")
            res["repeat_check"] = {"strategy": strategy, "label": s["label"], "same": again["trade_sig"] == res["strategies"][strategy]["adjusted"][s["label"]]["trade_sig"]}
    bc.ADJUSTED_PRICES_DEFAULT = False
    res["session_finished"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
