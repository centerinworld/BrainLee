"""
2026-09-09: se_momentum 조합 재검증 실패의 진짜 원인을 찾아 수정 — se_momentum의
trades_json은 완결된 라운드트립 거래(buy_date/sell_date/entry/exit, action 키 없음)
1,752건과, **정확히 동일한 거래를 code+buy_date까지 100% 중복하는** action='buy'
이벤트로그 1,752건이 섞여 있었다(왜 둘 다 나오는지는 se_momentum 백테스트 엔진
자체의 로깅 방식 — 버그 아님, 단지 소비자가 골라 써야 하는 이중 표현). 이전
스크립트(refresh_all_combos_20260909.py)의 _orders()/holding_windows()는 action
키가 있으면 무조건 이벤트로그로 취급해버려서: (1) 라운드트립 판을 무시하고
buy만 있는 걸로 오인해 매도 주문이 없다고 에러, (2) 감사에서 "아직도 보유 중인
포지션"으로 25건을 오탐(실제로는 전부 이미 청산된 완결 거래).

수정: 라운드트립 필드(buy_date+sell_date+entry+exit 전부 존재)가 있으면 action
키 유무와 무관하게 무조건 라운드트립으로 우선 처리하고, 그 경우가 아닐 때만
이벤트로그로 처리한다.
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
from merged_simulator import CandidateOrder, MergeConfig, persist_merged_run  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, derive_status  # noqa: E402

END = "2026-09-09"

RUN_IDS = {
    "earnings_conviction": "1b9c33e1",
    "golden_cross": "33950c6a",
    "recovery": "2c4af957",
    "se_momentum": "60bfee24",
}

COMBOS = [
    ("earnings_conviction_se_momentum", ["earnings_conviction", "se_momentum"]),
    ("earnings_conviction_recovery_se_momentum", ["earnings_conviction", "recovery", "se_momentum"]),
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


def _is_round_trip(row: dict) -> bool:
    buy_date = row.get("buy_date") or row.get("entry_date")
    sell_date = row.get("sell_date") or row.get("exit_date")
    entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
    exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
    code = row.get("code") or row.get("stock_code")
    return all((buy_date, sell_date, entry is not None, exit_price is not None, code))


def _orders(strategy: str, raw) -> list[CandidateOrder]:
    orders = []
    for row in _trades(raw):
        if _is_round_trip(row):
            buy_date = row.get("buy_date") or row.get("entry_date")
            sell_date = row.get("sell_date") or row.get("exit_date")
            entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
            exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
            code = str(row.get("code") or row.get("stock_code") or "")
            orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, 1.0))
            orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, 1.0))
            continue
        if row.get("action"):
            side = str(row["action"]).lower()
            if side not in {"buy", "sell", "pyramid"}:
                continue
            orders.append(CandidateOrder(
                str(row.get("date") or row.get("buy_date") or row.get("sell_date") or ""),
                str(row.get("code") or row.get("stock_code") or ""),
                side, float(row.get("price") or row.get("entry") or row.get("exit") or 0), strategy,
                1.0, sector=str(row.get("sector") or ""),
            ))
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
        if _is_round_trip(trade):
            code = _first_text(trade, "code", "stock_code")
            entry_d = _first_text(trade, "buy_date", "entry_date")[:10]
            exit_d = _first_text(trade, "sell_date", "exit_date")[:10]
            if len(code) == 6 and entry_d and exit_d:
                windows.append((code, entry_d, exit_d))
            continue
        action = str(trade.get("action") or trade.get("side") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        code = _first_text(trade, "code", "stock_code", "sc", "ticker")
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
    trades = _trades(_fetch_trades_json(run_id))
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
    print(f"    [integrity-v2] {strategy}: windows={len(windows)} contaminated={len(contaminated)} "
          f"price={'PASS' if price_passed else 'FAIL'}")
    if contaminated:
        for c in contaminated[:10]:
            print("       ", c)


def main():
    print("[1/2] se_momentum 재감사(수정된 파서)")
    rh = _fetch_run_hash(RUN_IDS["se_momentum"])
    audit_integrity(RUN_IDS["se_momentum"], rh, "se_momentum")
    status = derive_status(sqlite3.connect(DB_PATH, timeout=30), rh)
    print(f"    se_momentum status: {status['status']} (rank {status['status_rank']})")

    print("\n[2/2] 조합 재등록")
    hashes = {s: _fetch_run_hash(rid) for s, rid in RUN_IDS.items()}
    for combo_name, strategies in COMBOS:
        orders = []
        for s in strategies:
            orders.extend(_orders(s, _fetch_trades_json(RUN_IDS[s])))
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
        except Exception as exc:
            print(f"  {combo_name}: 등록 실패 — {exc}")


if __name__ == "__main__":
    main()
