"""
2026-09-08: sector_focus+v2 병합계좌를 오늘까지의 모든 수정사항(sector.py op_yoy
버그, v8/HS 관련 여러 버그는 이 조합과 무관하지만 공통 인프라 수정 포함)을 반영해
완전히 새로 재실행 — 어제(2026-09-07) 계산된 component_trades JSON은 이번 세션의
후반부 수정을 반영 못 했을 수 있어 신뢰하지 않고 지금 코드로 처음부터 다시 만든다.
6개 표준 구간 × (sector_focus 단독 / v2 단독 / 50:50 / 60:40 / 40:60) 조합을
train(첫 3구간)/val(마지막 3구간)로 평가하고, 최고 조합에 tiebreak_stability까지
적용해 "숫자가 실력인지 타이브레이크 운인지"까지 확인한다.
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

PERIODS = [
    ("2020-03-01", "2021-11-30", "20.3_21.11"),
    ("2021-12-01", "2022-10-31", "21.12_22.10"),
    ("2022-11-01", "2023-10-31", "22.11_23.10"),
    ("2023-11-01", "2024-12-31", "23.11_24.12"),
    ("2024-06-01", "2025-05-31", "24.06_25.05"),
    ("2025-06-01", "2026-03-31", "25.06_26.03"),
]


def _fetch_trades_json(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else "[]"


def main():
    print("[1/3] 컴포넌트 신선 재실행 (sector_focus, v2) — 오늘 코드 기준")
    data: dict[str, dict[str, str]] = {"sector_focus": {}, "v2": {}}
    for start, end, label in PERIODS:
        rid_s = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9)
        rid_v = run_backtest_v2(start, end, per_stock=10_000_000, max_positions=10)
        data["sector_focus"][label] = _fetch_trades_json(rid_s)
        data["v2"][label] = _fetch_trades_json(rid_v)
        print(f"  {label}: sector_focus run_id={rid_s}, v2 run_id={rid_v}")

    def _trades(raw) -> list[dict]:
        payload = json.loads(raw or "[]")
        return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)

    def _orders(strategy: str, raw, weight: float) -> list[CandidateOrder]:
        if weight <= 0:
            return []
        orders = []
        for row in _trades(raw):
            if row.get("action"):
                side = str(row["action"]).lower()
                if side not in {"buy", "sell", "pyramid"}:
                    continue
                orders.append(CandidateOrder(
                    str(row.get("date") or ""), str(row.get("code") or row.get("stock_code") or ""),
                    side, float(row.get("price") or 0), strategy,
                    weight * 1000 + float(row.get("surge_score") or row.get("score") or 0),
                    sector=str(row.get("sector") or ""),
                ))
                continue
            buy_date = row.get("buy_date") or row.get("entry_date")
            sell_date = row.get("sell_date") or row.get("exit_date")
            entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
            exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
            code = str(row.get("code") or row.get("stock_code") or "")
            if not all((buy_date, sell_date, entry, exit_price, code)):
                continue
            priority = weight * 1000
            orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, priority))
            orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, priority))
        return orders

    def _simulate(period: str, weights: dict[str, float]) -> dict:
        orders = []
        for strategy, weight in weights.items():
            orders.extend(_orders(strategy, data[strategy][period], weight))
        config = MergeConfig(
            initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=10,
            dynamic_tickets=True,
            strategy_budget_weights={k: v for k, v in weights.items() if v > 0},
            tiebreak_mode="neutral_hash",
        )
        summary = simulate_merged_account(orders, config)["summary"]
        return {"period": period, "return_pct": round(float(summary["total_return_pct"]), 2),
                "mdd_pct": round(float(summary.get("max_drawdown_pct") or 0), 2)}

    print("\n[2/3] 프로파일별 6구간 평가 (train=첫3, val=마지막3)")
    profiles = {
        "solo_sector_focus": {"sector_focus": 1.0, "v2": 0.0},
        "solo_v2": {"sector_focus": 0.0, "v2": 1.0},
        "sector_v2_5050": {"sector_focus": .5, "v2": .5},
        "sector_v2_6040": {"sector_focus": .6, "v2": .4},
        "sector_v2_4060": {"sector_focus": .4, "v2": .6},
    }
    labels = [p[2] for p in PERIODS]
    train_labels, val_labels = labels[:3], labels[3:]

    results = {}
    for name, weights in profiles.items():
        rows = [_simulate(lbl, weights) for lbl in labels]
        train_rows = [r for r in rows if r["period"] in train_labels]
        val_rows = [r for r in rows if r["period"] in val_labels]
        avg_all = sum(r["return_pct"] for r in rows) / len(rows)
        avg_train = sum(r["return_pct"] for r in train_rows) / len(train_rows)
        avg_val = sum(r["return_pct"] for r in val_rows) / len(val_rows)
        pos_all = sum(1 for r in rows if r["return_pct"] > 0)
        results[name] = {"rows": rows, "avg6": avg_all, "avg_train": avg_train,
                          "avg_val": avg_val, "positive": pos_all}
        print(f"  {name}: avg6={avg_all:+.2f}% (train={avg_train:+.2f}%, val={avg_val:+.2f}%) "
              f"양수구간={pos_all}/6")
        for r in rows:
            print(f"      {r['period']}: {r['return_pct']:+.2f}% (MDD {r['mdd_pct']:.1f}%)")

    best_name = max(results, key=lambda k: results[k]["avg_val"])
    print(f"\n[3/3] val 기준 최고 프로파일: {best_name} (val avg={results[best_name]['avg_val']:+.2f}%)")
    print("  -> 이 프로파일로 tiebreak_stability 검증 (25.06_26.03 최신구간 기준)")

    best_weights = profiles[best_name]
    latest_label = labels[-1]
    orders = []
    for strategy, weight in best_weights.items():
        orders.extend(_orders(strategy, data[strategy][latest_label], weight))
    config = MergeConfig(
        initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=10,
        dynamic_tickets=True,
        strategy_budget_weights={k: v for k, v in best_weights.items() if v > 0},
        tiebreak_mode="neutral_hash",
    )
    stability = tiebreak_stability(orders, config, trials=8)
    print(f"  base={stability['base_return_pct']}% mean={stability['mean_return_pct']}% "
          f"median={stability['median_return_pct']}% min={stability['min_return_pct']}% "
          f"max={stability['max_return_pct']}% cv={stability['cv_pct']}% "
          f"base_above_max={stability['base_above_max']}")

    out = {"profiles": results, "best_name": best_name, "best_weights": best_weights,
           "tiebreak_stability_latest_period": stability}
    out_path = ROOT / "research_outputs" / "combo_research_20260908_refresh.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
