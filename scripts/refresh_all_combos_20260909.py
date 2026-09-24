"""
2026-09-09: 사용자 지시 "복합전략 모두에 대해서 필요하다면 백테스트 후 % 수정" —
sector_focus+v2 외에, 이번 세션에서 실제 로직이 바뀐 컴포넌트 전략(recovery/
se_momentum/golden_cross — 각각 equity-curve $0버그, CFS/OFS 비결정성, CFS/OFS
SUM중복 버그가 수정됨)을 포함하는 기존 등록 조합들을 전부 오늘 코드로 재실행하고,
사람이 대충 통과시키지 않는 동일한 절차(연속 2020-01-01~오늘 시뮬레이션 → 무결성
감사(price/survivorship/corporate_action) → tiebreak_stability → persist_merged_run)
로 재등록한다.

"sector"/"sector_fresh"/"earn"/"moon30"/"v10_bull"/"v10_latest"/"v10_current_control"
컴포넌트를 쓰는 옛 조합들은 현재 routes/backtest.py ALL_STRATEGIES에 없는 죽은
별칭이라(현재 코드로 재현 불가) 대상에서 제외 — "v4"(run_backtest 제네릭 프리셋)도
호출 규약이 달라 이번 배치에서는 제외하고 별도 처리한다.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402
from backtest_strategies.sector import run_backtest_sector  # noqa: E402
from backtest_strategies.v2 import run_backtest_v2  # noqa: E402
from backtest_strategies.recovery import run_backtest_recovery  # noqa: E402
from backtest_strategies.se_momentum import run_backtest_se_momentum  # noqa: E402
from backtest_strategies.golden_cross import run_backtest_golden_cross  # noqa: E402
from backtest_strategies.earnings_conviction import run_backtest_earnings_conviction  # noqa: E402
from backtest_strategies.moonshot_turnaround import run_backtest_moonshot_turnaround  # noqa: E402
from merged_simulator import CandidateOrder, MergeConfig, tiebreak_stability, persist_merged_run  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, derive_status  # noqa: E402

START = "2020-01-01"
END = "2026-09-09"

# 이미 오늘(2026-09-08) 새로 돌린 컴포넌트는 재사용
KNOWN_RUN_IDS = {
    "sector_focus": "e1245314",
    "v2": "22adc49a",
}

RUNNERS = {
    "sector_focus": lambda: run_backtest_sector(START, END, per_stock=10_000_000, max_positions=9),
    "v2": lambda: run_backtest_v2(START, END, per_stock=10_000_000, max_positions=10),
    "recovery": lambda: run_backtest_recovery(START, END, per_stock=10_000_000, max_positions=10),
    "se_momentum": lambda: run_backtest_se_momentum(START, END, per_stock=10_000_000, max_positions=10),
    "golden_cross": lambda: run_backtest_golden_cross(START, END, per_stock=10_000_000, max_positions=10),
    "earnings_conviction": lambda: run_backtest_earnings_conviction(START, END, total_capital=100_000_000, max_positions=10),
    "moonshot_turnaround": lambda: run_backtest_moonshot_turnaround(START, END, total_capital=100_000_000, max_positions=30),
}

# (조합 이름, 구성 전략 리스트) — 기존에 등록됐던 조합 중 현재 코드로 재현 가능한 것만
COMBOS = [
    ("sector_focus_solo", ["sector_focus"]),
    ("golden_cross_recovery", ["golden_cross", "recovery"]),
    ("earnings_conviction_se_momentum", ["earnings_conviction", "se_momentum"]),
    ("earnings_conviction_recovery_se_momentum", ["earnings_conviction", "recovery", "se_momentum"]),
    ("earnings_conviction_golden_cross_recovery", ["earnings_conviction", "golden_cross", "recovery"]),
]


def _fetch_trades_json(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else "[]"


def _fetch_run_hash(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT run_hash FROM backtest_run_specs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


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
                side, float(row.get("price") or 0), strategy, 1.0,
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
        orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, 1.0))
        orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, 1.0))
    return orders


def _first_text(trade: dict, *keys: str) -> str:
    for key in keys:
        value = trade.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def holding_windows(trades: list[dict], period_end: str) -> list[tuple[str, str, str]]:
    open_buys: dict[str, deque[str]] = defaultdict(deque)
    windows = []
    events = []
    for trade in trades:
        code = _first_text(trade, "code", "stock_code", "sc", "ticker")
        entry = _first_text(trade, "entry_date", "buy_date")[:10]
        exit_date = _first_text(trade, "exit_date", "sell_date")[:10]
        if not entry and len(str(trade.get("entry") or "")) >= 10:
            entry = str(trade["entry"])[:10]
        if not exit_date and len(str(trade.get("exit") or "")) >= 10:
            exit_date = str(trade["exit"])[:10]
        if len(code) == 6 and entry and exit_date:
            windows.append((code, entry, exit_date))
            continue
        action = str(trade.get("action") or trade.get("side") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        day = _first_text(
            trade, "date", "trade_date",
            "buy_date" if action == "BUY" else "sell_date",
            "entry_date" if action == "BUY" else "exit_date",
        )[:10]
        events.append((day, code, action))
    for day, code, action in sorted(events):
        if len(code) != 6 or not day:
            continue
        if action == "BUY":
            open_buys[code].append(day)
        elif action == "SELL" and open_buys[code]:
            windows.append((code, open_buys[code].popleft(), day))
    for code, buys in open_buys.items():
        windows.extend((code, day, period_end) for day in buys)
    return windows


def audit_integrity(run_id: str, run_hash: str, strategy: str) -> None:
    conn = connect_stock_db(readonly=True)
    trades_json = _fetch_trades_json(run_id)
    trades = _trades(trades_json)
    windows = holding_windows(trades, END)
    contaminated = []
    for code, start, end in windows:
        master = conn.execute(
            """SELECT effective_to FROM security_master_history
               WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                 AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
               ORDER BY effective_from DESC LIMIT 1""",
            (code, start, start),
        ).fetchone()
        if not master:
            contaminated.append({"stock_code": code, "holding_start": start, "holding_end": end,
                                  "classification": "missing_asof_security_master"})
        end_master = conn.execute(
            """SELECT 1 FROM security_master_history
               WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                 AND effective_from<=? AND (effective_to IS NULL OR effective_to>?) LIMIT 1""",
            (code, end, end),
        ).fetchone()
        if master and master[0] and str(master[0]) <= end and not end_master:
            contaminated.append({"stock_code": code, "holding_start": start, "holding_end": end,
                                  "event_date": str(master[0]), "classification": "held_through_listing_end"})
        jumps = conn.execute(
            """SELECT event_date,classification,evidence FROM price_jump_audit
               WHERE stock_code=? AND event_date BETWEEN ? AND ? AND return_usable=0
               ORDER BY event_date""",
            (code, start, end),
        ).fetchall()
        for jd, jc, je in jumps:
            contaminated.append({"stock_code": code, "holding_start": start, "holding_end": end,
                                  "event_date": jd, "classification": jc, "evidence": je})
    conn.close()

    survivorship = [r for r in contaminated
                    if r["classification"] in {"missing_asof_security_master", "held_through_listing_end"}]
    corp_action = [r for r in contaminated if r not in survivorship]
    price_passed = len(windows) > 0 and not contaminated
    register_artifact(run_hash, "price_integrity", price_passed,
                       {"strategy": strategy, "holding_windows": len(windows),
                        "contaminated": len(contaminated), "examples": contaminated[:20]})
    register_artifact(run_hash, "survivorship_integrity", len(windows) > 0 and not survivorship,
                       {"holding_windows": len(windows), "findings": len(survivorship)})
    register_artifact(run_hash, "corporate_action_integrity", len(windows) > 0 and not corp_action,
                       {"holding_windows": len(windows), "findings": len(corp_action)})
    print(f"    [integrity] {strategy}: windows={len(windows)} contaminated={len(contaminated)} "
          f"price={'PASS' if price_passed else 'FAIL'}")


def main():
    print("[1/3] 컴포넌트 신선 재실행")
    run_ids: dict[str, str] = dict(KNOWN_RUN_IDS)
    needed = {s for _, strategies in COMBOS for s in strategies}
    for strategy in sorted(needed):
        if strategy in run_ids:
            print(f"  {strategy}: 재사용 run_id={run_ids[strategy]}")
            continue
        rid = RUNNERS[strategy]()
        run_ids[strategy] = rid
        print(f"  {strategy}: 신규 run_id={rid}")

    print("\n[2/3] 컴포넌트 무결성 감사(price/survivorship/corporate_action)")
    hashes: dict[str, str] = {}
    for strategy, rid in run_ids.items():
        rh = _fetch_run_hash(rid)
        hashes[strategy] = rh
        status = derive_status(sqlite3.connect(DB_PATH, timeout=30), rh)
        if status["status_rank"] >= 1:
            print(f"  {strategy}({rh}): 이미 execution_strict 이상 — 스킵")
            continue
        audit_integrity(rid, rh, strategy)

    print("\n[3/3] 조합별 병합계좌 등록")
    results = []
    for combo_name, strategies in COMBOS:
        orders = []
        for s in strategies:
            orders.extend(_orders(s, _fetch_trades_json(run_ids[s])))
        config = MergeConfig(
            initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
            dynamic_tickets=True, strategy_budget_weights={}, tiebreak_mode="neutral_hash",
        )
        try:
            result = persist_merged_run(
                orders, component_run_hashes=[hashes[s] for s in strategies],
                config=config, db_path=DB_PATH, tiebreak_trials=8, allow_path_luck=False,
            )
            ret = result["summary"]["total_return_pct"]
            print(f"  {combo_name}: run_id={result['run_id']} return={ret:.2f}%")
            results.append({"combo": combo_name, "strategies": strategies,
                             "run_id": result["run_id"], "return_pct": ret})
        except Exception as exc:
            print(f"  {combo_name}: 등록 실패 — {exc}")
            results.append({"combo": combo_name, "strategies": strategies, "error": str(exc)})

    out_path = ROOT / "research_outputs" / "combo_refresh_20260909_all.json"
    out_path.write_text(json.dumps({"run_ids": run_ids, "results": results}, ensure_ascii=False, indent=2, default=str))
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
