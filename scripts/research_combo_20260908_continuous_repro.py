"""
2026-09-08: 원본 헤드라인(cmb_c8f841b9708d, 688.9%)이 어떻게 만들어졌는지 정확히
재구성해 오늘 코드로 재현 — parameter_json에서 확인한 원본 설정 그대로 사용:
  - 연속 단일 시뮬레이션(구간 분리 없음): 2020-01-01 ~ 오늘(2026-09-08 데이터까지)
  - 컴포넌트: sector_focus + v2
  - max_positions=20, ticket_budget=10,000,000, dynamic_tickets=True,
    strategy_budget_weights={}(가중치 없음 — 두 전략이 동등하게 경쟁)
같은 설정으로 오늘 수정된 코드(sector.py op_yoy 버그 수정 등 포함)를 돌려
"600%대가 재현되는지" 직접 확인하고, tiebreak_stability로 안정성까지 검증한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_strategies.sector import run_backtest_sector
from backtest_strategies.v2 import run_backtest_v2
from backtest_common import DB_PATH
from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account, tiebreak_stability
import sqlite3

START = "2020-01-01"
END = "2026-09-08"


def _fetch_trades_json(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else "[]"


def _trades(raw) -> list[dict]:
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _orders(strategy: str, raw) -> list[CandidateOrder]:
    orders = []
    for row in _trades(raw):
        if row.get("action"):
            side = str(row["action"]).lower()
            if side not in {"buy", "sell", "pyramid"}:
                continue
            orders.append(CandidateOrder(
                str(row.get("date") or ""), str(row.get("code") or row.get("stock_code") or ""),
                side, float(row.get("price") or 0), strategy,
                1.0, sector=str(row.get("sector") or ""),
            ))
            continue
        buy_date = row.get("buy_date") or row.get("entry_date")
        sell_date = row.get("sell_date") or row.get("exit_date")
        entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
        exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
        code = str(row.get("code") or row.get("stock_code") or "")
        if not all((buy_date, sell_date, entry, exit_price, code)):
            continue
        orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, 1.0))
        orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, 1.0))
    return orders


def main():
    print(f"[1/2] 연속 단일 백테스트 재실행: {START} ~ {END} (오늘 코드 기준)")
    rid_s = run_backtest_sector(START, END, per_stock=10_000_000, max_positions=9)
    rid_v = run_backtest_v2(START, END, per_stock=10_000_000, max_positions=10)
    print(f"  sector_focus run_id={rid_s}, v2 run_id={rid_v}")

    trades_s = _fetch_trades_json(rid_s)
    trades_v = _fetch_trades_json(rid_v)
    orders = _orders("sector_focus", trades_s) + _orders("v2", trades_v)
    print(f"  총 주문(체결쌍 기준 트레이드) sector_focus={len(_trades(trades_s))}건, "
          f"v2={len(_trades(trades_v))}건")

    config = MergeConfig(
        initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
        dynamic_tickets=True, strategy_budget_weights={}, tiebreak_mode="neutral_hash",
    )
    result = simulate_merged_account(orders, config)
    summary = result["summary"]
    print(f"\n[2/2] 병합계좌 연속 시뮬레이션 결과 (원본 cmb_c8f841b9708d 설정 동일 재현)")
    print(f"  총수익률: {summary['total_return_pct']:.2f}% (원본 헤드라인: 688.9%)")
    print(f"  MDD: {summary.get('max_drawdown_pct')}")

    print("\n  tiebreak_stability(8회 무작위 타이브레이크 재시행) 진행 중...")
    stability = tiebreak_stability(orders, config, trials=8)
    print(f"  base={stability['base_return_pct']}% mean={stability['mean_return_pct']}% "
          f"median={stability['median_return_pct']}% min={stability['min_return_pct']}% "
          f"max={stability['max_return_pct']}% cv={stability['cv_pct']}% "
          f"base_above_max={stability['base_above_max']} "
          f"base_percentile={stability['base_percentile']}")

    out = {
        "start": START, "end": END, "sector_focus_run_id": rid_s, "v2_run_id": rid_v,
        "total_return_pct": summary["total_return_pct"], "mdd": summary.get("max_drawdown_pct"),
        "original_headline_pct": 688.9, "tiebreak_stability": stability,
    }
    out_path = ROOT / "research_outputs" / "combo_research_20260908_continuous_repro.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
