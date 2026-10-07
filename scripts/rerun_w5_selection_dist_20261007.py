#!/usr/bin/env python3
"""§26-2 ①: 일반 엔진 전략의 선택 순서 분포 — 조정 가격 모드에서 code(옛 선착순)·score·random:1..N을 같은 데이터 지문 안에서 실행.
선택·화면 등록 없음. 결과: research_outputs/w5_selection_dist_20261007.json
사용: python3 scripts/rerun_w5_selection_dist_20261007.py [--strategies v_trend,v2] [--random 12] [--workers 3]
"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
from scripts.rerun_w2_generic_compare_20261006 import run_one, GENERIC  # noqa: E402

OUT = ROOT / "research_outputs" / "w5_selection_dist_20261007.json"   # --strategies 하나면 w5_selection_dist_20261007_<전략>.json (프로세스 병렬용)


def one(strategy, spec, order):
    tok = bc.SELECTION_ORDER_OVERRIDE.set(order)
    try:
        r = run_one(strategy, spec, f"sel_{order}")
    finally:
        bc.SELECTION_ORDER_OVERRIDE.reset(tok)
    r.pop("keys", None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default=",".join(GENERIC)); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--random", type=int, default=12)
    a = ap.parse_args()
    global OUT
    if len([x for x in a.strategies.split(',') if x]) == 1:
        OUT = OUT.with_name(f"w5_selection_dist_20261007_{a.strategies.strip()}.json")
    specs = _all_selected_specs({x for x in a.strategies.split(",") if x})
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    res["session_started"] = res.get("session_started") or datetime.now().isoformat(timespec="seconds")
    res.setdefault("strategies", {})
    bc.ADJUSTED_PRICES_DEFAULT = True
    orders = ["code", "score"] + [f"random:{k}" for k in range(1, a.random + 1)]
    for strategy, items in specs.items():
        done = res["strategies"].setdefault(strategy, {})
        jobs = [(s["label"], o, s) for s in items for o in orders if o not in done.get(s["label"], {})]
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futs = [(lab, o, pool.submit(one, strategy, s, o)) for lab, o, s in jobs]
            for lab, o, f in futs:
                done.setdefault(lab, {})[o] = f.result()
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(strategy, "done", flush=True)
    bc.ADJUSTED_PRICES_DEFAULT = False
    res["session_finished"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
