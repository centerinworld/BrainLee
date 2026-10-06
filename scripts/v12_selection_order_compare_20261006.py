#!/usr/bin/env python3
"""docs/Stock_Strategy.md S27·D13 — v12 매수 후보 선택 순서 비교(점수 순 vs 종목코드 순) + 재현성 확인.

- 기존 선택 suite(strategy_center)의 구간·파라미터를 그대로 쓰고 selection_order만 바꾼다.
- 점수 순은 6구간 전부 2회 실행해 결과(수익률·MDD·거래 수·거래 목록)가 같은지 확인한다.
- 선택(select)·화면 교체는 하지 않는다. 결과: research_outputs/v12_selection_order_20261006.json
사용: python3 scripts/v12_selection_order_compare_20261006.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest as bt  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
from scripts.rerun_selected_after_price_repair import _price_integrity  # noqa: E402

OUT = ROOT / "research_outputs" / "v12_selection_order_20261006.json"
TAG = "d13_v12"


def _run(spec: dict, order: str, rep: int) -> dict:
    params = {k: v for k, v in spec["parameters"].items()
              if k in {"max_positions", "asof_mktcap", "take_profit_pct"}}
    run_id = str(uuid.uuid4())[:8]
    name = f"{TAG} {order}#{rep} {spec['label']}"
    conn = connect_stock_db()
    conn.execute("""INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status)
                    VALUES(?,?,?,?,?,?,'running') ON CONFLICT(run_id) DO NOTHING""",
                 (run_id, name, "v12", spec["start"], spec["end"], spec["per_stock"]))
    conn.commit()
    conn.close()
    bt.run_backtest_v12(spec["start"], spec["end"], per_stock=spec["per_stock"], selection_order=order,
                        run_name=name, run_id=run_id, **params)
    conn = connect_stock_db(readonly=True)
    r = conn.execute("""SELECT status,total_return_pct,max_drawdown_pct,total_trades,win_rate,trades_json
                        FROM backtest_runs WHERE run_id=?""", (run_id,)).fetchone()
    conn.close()
    trades = json.loads(r[5]) if isinstance(r[5], str) else (r[5] or {})
    keys = sorted((t["stock_code"], t["entry_date"], t.get("exit_date")) for t in trades.get("trades", []))
    passed, _ = _price_integrity(run_id, spec["end"], strategy="v12")
    return {"run_id": run_id, "status": r[0], "total_return_pct": r[1], "mdd": r[2], "trades": r[3],
            "win_rate": r[4], "trade_set_sha": hashlib.sha256(json.dumps(keys).encode()).hexdigest()[:16],
            "integrity_passed": bool(passed)}


def main():
    specs = _all_selected_specs({"v12"})["v12"]
    out = {"started_at": datetime.now().isoformat(timespec="seconds"), "periods": {}}
    for spec in sorted(specs, key=lambda s: s["start"]):
        lab = spec["label"]
        row = {"score": _run(spec, "score", 1), "score_repeat": _run(spec, "score", 2), "code": _run(spec, "code", 1)}
        a, b = row["score"], row["score_repeat"]
        row["score_reproducible"] = all(a[k] == b[k] for k in ("total_return_pct", "mdd", "trades", "trade_set_sha"))
        out["periods"][lab] = row
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(lab, {k: (v["total_return_pct"], v["trades"], v["integrity_passed"]) for k, v in row.items() if isinstance(v, dict)},
              "재현", row["score_reproducible"], flush=True)
    out["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
