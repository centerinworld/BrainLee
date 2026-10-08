#!/usr/bin/env python3
"""W5b(REVIEW_PLAN §33): W5b로 조정 가격 이관을 마친 10개 전략의 최종 재실행 — W5a와 같은 방식.

대상과 순서:
  v8: adjusted_prices=True, selection_order code·score·random:1..N
  deep_recovery·extreme_dd_volume·low_base_breakout·megatrend·recovery·earnings_conviction·earnings_supply_discovery·
  moonshot_turnaround·contract_momentum: adjusted_prices=True, 점수 정렬 고정(선택 순서 인자 없음 — '순서 분포 미측정')
선택(select)·화면 교체 없음 — 등급 판정(W6)은 D14 분포 기준으로 따로. 결과: research_outputs/w5b_20261008_<전략>.json
사용: run_w5b_20261008.py --strategy v_trend [--random 12]   (전략 하나씩 프로세스 병렬)
끝난 뒤: scripts/review/check_run_fingerprints.py --name-like 'w2_w5b_%' --since <시작> --label w5b_20261008 로 지문 1종 확인.
"""
from __future__ import annotations
import argparse, copy, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
import hashlib, inspect, uuid  # noqa: E402
import backtest as bt  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from scripts.rerun_w2_generic_compare_20261006 import FUNCTIONS  # noqa: E402
from scripts.rerun_selected_after_price_repair import _price_integrity  # noqa: E402


def _g(t, *keys, default=None):
    for k in keys:
        if t.get(k) is not None:
            return t[k]
    return default


def run_one(strategy, spec, mode):
    """rerun_w2_generic_compare.run_one과 같은 기록 방식 — 전략마다 다른 거래 기록 키(code·buy_date·sell_date·pnl 등)를 읽는다."""
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
    sp = c.execute("SELECT run_hash, parameter_json FROM backtest_run_specs WHERE run_id=?", (run_id,)).fetchone()
    c.close()
    payload = json.loads(r[3] or "[]")
    trades = payload.get("trades", []) if isinstance(payload, dict) else payload
    closed = [t for t in trades if _g(t, "exit_date", "sell_date")]
    sig = hashlib.sha1(json.dumps(sorted([(str(_g(t, "stock_code", "code")), str(_g(t, "entry_date", "buy_date")),
                                           str(_g(t, "exit_date", "sell_date")), round(float(_g(t, "profit_amt", "pnl", default=0) or 0)))
                                          for t in closed]), sort_keys=True).encode()).hexdigest()[:12]
    try:
        ok, det = _price_integrity(run_id, spec["end"], strategy=strategy)
        gate = {"passed": ok, "contaminated": len(det["contaminated_events"])}
    except Exception as e:  # noqa
        gate = {"error": str(e)[:80]}
    pj = json.loads(sp[1]) if sp and sp[1] else {}
    data_fp = {"source_snapshot": (pj.get("_source_snapshot") or {}).get("fingerprint"), "extras": pj.get("_data_revision_extras")}
    stats = payload.get("adjusted_stats_w5b") if isinstance(payload, dict) else None
    for line in (r[4] or "").split("\n"):
        if line.startswith("조정가격(W5b):"):
            stats = json.loads(line.split(":", 1)[1])
    return {"run_hash": sp[0] if sp else None, "data_fp": data_fp, "run_id": run_id, "ret": r[0], "mdd": r[1], "trades": r[2],
            "status": r[5], "trade_sig": sig, "gate": gate, "adj_stats": stats,
            "unevaluable": sum(1 for t in closed if str(t.get("evaluation", "")).startswith("unevaluable")),
            "delisted_exits": sum(1 for t in closed if t.get("delisted"))}


PARAM_ORDER = {"v8"}                     # selection_order를 함수 인자로 받는 전략
FIXED_RANK = {"deep_recovery", "extreme_dd_volume", "low_base_breakout", "megatrend", "recovery",
              "earnings_conviction", "earnings_supply_discovery", "moonshot_turnaround", "contract_momentum"}
TARGETS = PARAM_ORDER | FIXED_RANK


def one(strategy, spec, order):
    sp = copy.deepcopy(spec)
    sp["parameters"]["adjusted_prices"] = True          # 인자를 받는 전략만 실제로 전달됨(run_one이 서명으로 거름)
    tok = None
    if strategy in PARAM_ORDER:
        sp["parameters"]["selection_order"] = order
    try:
        r = run_one(strategy, sp, f"w5b_{order}")
    finally:
        if tok is not None:
            bc.SELECTION_ORDER_OVERRIDE.reset(tok)
    r.pop("keys", None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True); ap.add_argument("--random", type=int, default=12)
    a = ap.parse_args()
    if a.strategy not in TARGETS:
        raise SystemExit(f"W5b 대상 아님: {a.strategy}")
    out = ROOT / "research_outputs" / f"w5b_20261008_{a.strategy}.json"
    res = json.loads(out.read_text()) if out.exists() else {}
    res.setdefault("started", datetime.now().isoformat(timespec="seconds"))
    runs = res.setdefault("runs", {})
    bc.ADJUSTED_PRICES_DEFAULT = True
    orders = ["fixed"] if a.strategy in FIXED_RANK else ["code", "score"] + [f"random:{k}" for k in range(1, a.random + 1)]
    for spec in sorted(_all_selected_specs({a.strategy})[a.strategy], key=lambda s: s["start"]):
        for o in orders:
            if o in runs.get(spec["label"], {}):
                continue
            runs.setdefault(spec["label"], {})[o] = one(a.strategy, spec, o)
            out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(a.strategy, spec["label"], "done", flush=True)
    bc.ADJUSTED_PRICES_DEFAULT = False
    res["finished"] = datetime.now().isoformat(timespec="seconds")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
