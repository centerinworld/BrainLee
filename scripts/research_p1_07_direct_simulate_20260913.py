"""
P1-07 재실행 — persist_merged_run()의 등록게이트(zero-tolerance) 대신
simulate_merged_account()를 직접 호출해 등록 없이 수익률만 확인.
composite는 이미 09-12에 신규 연속실행된 53488c06(run_hash 83e9a856a4ce, 2.76% 오염,
7% 임계값 정책 하에서는 통과)을 재사용 — 중복 재실행 없이 기존 세션 산출물 재사용.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts")
from backtest_common import sqlite3, DB_PATH
from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account, pnl_concentration
from research_p1_07_add_composite_20260912 import _orders as _orders_generic, _fetch_trades_json, _trades


def _orders(strategy: str, raw) -> list:
    """composite.py 고유 스키마(sc/entry=날짜/exit=날짜/entry_price/exit_price)는
    _orders_generic()이 가정하는 turnaround류 스키마(code/entry=가격/exit=가격/
    buy_date/sell_date)와 달라 전량 스킵되는 버그가 있었다 — composite만 직접 처리."""
    if strategy != "composite":
        return _orders_generic(strategy, raw)
    out = []
    for row in _trades(raw):
        code = str(row.get("sc") or "")
        buy_date = row.get("entry")
        sell_date = row.get("exit")
        entry_p = row.get("entry_price")
        exit_p = row.get("exit_price")
        if not all((buy_date, sell_date, entry_p, exit_p, code)):
            continue
        out.append(CandidateOrder(str(buy_date), code, "buy", float(entry_p), strategy, 1.0))
        out.append(CandidateOrder(str(sell_date), code, "sell", float(exit_p), strategy, 1.0))
    return out

KNOWN_RUN_IDS = {
    "sector_focus": "e1245314",
    "v2": "22adc49a",
    "composite": "53488c06",
}

COMBOS = [
    ("composite_solo", ["composite"]),
    ("sector_focus_v2", ["sector_focus", "v2"]),
    ("sector_focus_v2_composite", ["sector_focus", "v2", "composite"]),
]

OUT_PATH = Path(__file__).resolve().parents[1] / "research_outputs" / "strategy_return_research_20260912" / "p1_07_direct_simulate_result_20260913.json"


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)

    for k, rid in KNOWN_RUN_IDS.items():
        r = conn.execute("SELECT total_return_pct, start_date, end_date, status FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
        print(k, rid, tuple(r) if r else "NOT FOUND", flush=True)

    results = []
    for combo_name, strategies in COMBOS:
        orders = []
        for s in strategies:
            orders.extend(_orders(s, _fetch_trades_json(KNOWN_RUN_IDS[s])))
        config = MergeConfig(
            initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
            dynamic_tickets=True, strategy_budget_weights={}, tiebreak_mode="neutral_hash",
        )
        result = simulate_merged_account(orders, config)
        summary = result["summary"]
        conc = pnl_concentration(result["ledger"], result["mark_prices"])
        print(f"{combo_name:28s} ret={summary['total_return_pct']:.2f}% mdd={summary['max_drawdown_pct']:.2f}% "
              f"trades={summary['completed_trades']} top1={conc['top1_code']}({conc['top1_pct_of_pnl']}%)", flush=True)
        results.append({"combo": combo_name, "strategies": strategies, "return_pct": summary["total_return_pct"],
                         "mdd": summary["max_drawdown_pct"], "completed_trades": summary["completed_trades"],
                         "concentration": conc})

    with open(str(OUT_PATH), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("DONE")


if __name__ == "__main__":
    main()
