#!/usr/bin/env python3
"""§26-2 ③: 전략별 min_history_rows(새) 대 60행 고정(이전) 비교 — 선택 순서 score로 고정, 같은 세션. 선택·화면 등록 없음.
결과: research_outputs/w5_minhist_compare_20261007_<전략>.json (전략 하나씩 프로세스 병렬)"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import backtest_common as bc
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs
from scripts.rerun_w2_generic_compare_20261006 import run_one

def one(strategy, spec, mode):
    tok = bc.SELECTION_ORDER_OVERRIDE.set("score")
    try:
        r = run_one(strategy, spec, f"minhist_{mode}")
    finally:
        bc.SELECTION_ORDER_OVERRIDE.reset(tok)
    r.pop("keys", None)
    return r

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--strategy", required=True); a = ap.parse_args()
    specs = _all_selected_specs({a.strategy})
    bc.ADJUSTED_PRICES_DEFAULT = True
    res = {}
    new_tbl = dict(bc.MIN_HISTORY_ROWS)
    for mode in ("new", "old60"):
        bc.MIN_HISTORY_ROWS = new_tbl if mode == "new" else {}
        with ThreadPoolExecutor(max_workers=1) as pool:
            futs = {s["label"]: pool.submit(one, a.strategy, s, mode) for s in specs[a.strategy]}
            for lab, f in futs.items():
                res.setdefault(lab, {})[mode] = f.result()
    bc.MIN_HISTORY_ROWS = new_tbl
    (ROOT / "research_outputs" / f"w5_minhist_compare_20261007_{a.strategy}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
main()
