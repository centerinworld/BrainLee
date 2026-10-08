#!/usr/bin/env python3
"""W5a(REVIEW_PLAN §32): 조정 가격 이관을 마친 11개 전략의 최종 재실행 — 같은 코드·데이터 지문 안에서 선택 순서 분포까지.

대상과 순서:
  일반 엔진 8개(v_trend·v1_value·v2·v5·v10·v11·vbr·minervini): 조정 가격 + PIT 유니버스, code·score·random:1..N
  v12: adjusted_prices=True, selection_order code·score·random:1..N
  golden_cross·sector_focus: adjusted_prices=True, 순위 고정 전략이라 1회(선택 순서 인자 없음)
선택(select)·화면 교체 없음 — 등급 판정(W6)은 D14 분포 기준으로 따로. 결과: research_outputs/w5a_20261008_<전략>.json
사용: run_w5a_20261008.py --strategy v_trend [--random 12]   (전략 하나씩 프로세스 병렬)
끝난 뒤: scripts/review/check_run_fingerprints.py --name-like 'w2_w5a_%' --since <시작> --label w5a_20261008 로 지문 1종 확인.
"""
from __future__ import annotations
import argparse, copy, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
import scripts.rerun_w2_generic_compare_20261006 as _w2  # noqa: E402
from scripts.rerun_w2_generic_compare_20261006 import run_one, GENERIC  # noqa: E402


class _TradeView(dict):
    """전략마다 다른 거래 기록 키(sector_focus·golden_cross: code·date·action·pnl_krw)를 일반 엔진 키로 읽게 한다."""
    _ALIAS = {"stock_code": ("stock_code", "code"), "entry_date": ("entry_date", "buy_date", "date"),
              "exit_date": ("exit_date", "sell_date", "date"), "profit_amt": ("profit_amt", "pnl", "pnl_krw")}

    def __getitem__(self, k):
        for a in self._ALIAS.get(k, (k,)):
            if a in self and dict.__getitem__(self, a) is not None:
                return dict.__getitem__(self, a)
        return 0 if k == "profit_amt" else None


_orig_loads = _w2.json.loads


def _loads_tradeview(s, *a, **kw):
    v = _orig_loads(s, *a, **kw)
    if isinstance(v, dict) and isinstance(v.get("trades"), list):
        v["trades"] = [_TradeView(t) if isinstance(t, dict) else t for t in v["trades"]]
    return v

PARAM_ORDER = {"v12"}                    # selection_order를 함수 인자로 받는 전략
FIXED_RANK = {"golden_cross", "sector_focus"}
TARGETS = set(GENERIC) | PARAM_ORDER | FIXED_RANK


def one(strategy, spec, order):
    sp = copy.deepcopy(spec)
    sp["parameters"]["adjusted_prices"] = True          # 인자를 받는 전략만 실제로 전달됨(run_one이 서명으로 거름)
    tok = None
    if strategy in PARAM_ORDER:
        sp["parameters"]["selection_order"] = order
    elif strategy not in FIXED_RANK:
        tok = bc.SELECTION_ORDER_OVERRIDE.set(order)
    try:
        if strategy in FIXED_RANK:      # 거래 기록 키가 다른 전략 — run_one 안의 json.loads만 바꿔 읽는다
            _w2.json.loads = _loads_tradeview
        r = run_one(strategy, sp, f"w5a_{order}")
    finally:
        _w2.json.loads = _orig_loads
        if tok is not None:
            bc.SELECTION_ORDER_OVERRIDE.reset(tok)
    r.pop("keys", None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True); ap.add_argument("--random", type=int, default=12)
    a = ap.parse_args()
    if a.strategy not in TARGETS:
        raise SystemExit(f"W5a 대상 아님: {a.strategy}")
    out = ROOT / "research_outputs" / f"w5a_20261008_{a.strategy}.json"
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
