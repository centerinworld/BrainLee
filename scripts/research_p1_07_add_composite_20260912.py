"""
P1-07 (핸드오프 docs/claude_handoff_strategy_return_research_20260912.md Section 6) —
단일자본 병합의 현금 활용/자금 경쟁: sector_focus+v2 병합계좌(cmb_90d92c104f0a, 403.09%%/
tiebreak평균368.6%%, 2020-01-01~2026-09-08)에 composite를 추가하면 개선되는가?

방법: 기존 등록된 sector_focus(e1245314)/v2(22adc49a) 컴포넌트 런은 그대로 재사용(재실행하면
동일기간 비교가 아니라 기간연장 비교가 되어버림 — 이 리서치 Section 4의 "과거 종료일 고정 vs
기간연장 분리" 원칙을 지키기 위해 END를 두 컴포넌트의 등록된 end_date(2026-09-08)와 정확히
맞춘다). composite만 신규로 동일 기간 연속 실행(현재 기본값: use_event_bonus=True +
use_material_backlog_bonus=True, composite.py 코드로 이번 세션에 직접 확인).

두 조합을 등록한다:
  1) composite_solo — composite 단독 연속실행 (새 데이터 포인트, 기존 21.88%%는 7구간
     walk-forward 평균이라 이 연속실행 수치와 직접 비교 불가 — 별도 지표로 취급)
  2) sector_focus_v2_composite — 3개 전략 단일계좌 병합 (이번 실험의 본체)

merged_simulator.persist_merged_run()의 8회 무작위 타이브레이크 검증을 반드시 통과해야 등록됨
(id=142 stock_code_tiebreak_path_luck 발견 이후 필수 게이트) — allow_path_luck=False 유지.
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
from backtest_strategies.composite import run_backtest_composite  # noqa: E402
from merged_simulator import CandidateOrder, MergeConfig, persist_merged_run  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, derive_status  # noqa: E402

START = "2020-01-01"
END = "2026-09-08"  # matches sector_focus(e1245314)/v2(22adc49a) registered end_date exactly

KNOWN_RUN_IDS = {
    "sector_focus": "e1245314",
    "v2": "22adc49a",
}

RUNNERS = {
    "composite": lambda: run_backtest_composite(START, END, per_stock=10_000_000, max_positions=10),
}

COMBOS = [
    ("composite_solo", ["composite"]),
    ("sector_focus_v2_composite", ["sector_focus", "v2", "composite"]),
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
          f"price={'PASS' if price_passed else 'FAIL'}", flush=True)


def main():
    print("[1/3] composite 신규 실행 (sector_focus/v2는 기존 등록 런 재사용, 기간 일치 유지)", flush=True)
    run_ids: dict[str, str] = dict(KNOWN_RUN_IDS)
    needed = {s for _, strategies in COMBOS for s in strategies}
    for strategy in sorted(needed):
        if strategy in run_ids:
            print(f"  {strategy}: 재사용 run_id={run_ids[strategy]}", flush=True)
            continue
        rid = RUNNERS[strategy]()
        run_ids[strategy] = rid
        print(f"  {strategy}: 신규 run_id={rid}", flush=True)

    print("\n[2/3] 컴포넌트 무결성 감사(price/survivorship/corporate_action)", flush=True)
    hashes: dict[str, str] = {}
    for strategy, rid in run_ids.items():
        rh = _fetch_run_hash(rid)
        hashes[strategy] = rh
        status = derive_status(sqlite3.connect(DB_PATH, timeout=30), rh)
        if status["status_rank"] >= 1:
            print(f"  {strategy}({rh}): 이미 execution_strict 이상 — 스킵", flush=True)
            continue
        audit_integrity(rid, rh, strategy)

    print("\n[3/3] 조합별 병합계좌 등록", flush=True)
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
            stability = result.get("spec_payload", {}).get("tiebreak_stability") if isinstance(result, dict) else None
            print(f"  {combo_name}: run_id={result['run_id']} return={ret:.2f}% stability={stability}", flush=True)
            results.append({"combo": combo_name, "strategies": strategies,
                             "run_id": result["run_id"], "return_pct": ret})
        except Exception as exc:
            print(f"  {combo_name}: 등록 실패 — {exc}", flush=True)
            results.append({"combo": combo_name, "strategies": strategies, "error": str(exc)})

    out_path = ROOT / "research_outputs" / "strategy_return_research_20260912" / "p1_07_add_composite_result.json"
    out_path.write_text(json.dumps({"start": START, "end": END, "run_ids": run_ids, "results": results}, ensure_ascii=False, indent=2, default=str))
    print(f"\n결과 저장: {out_path}", flush=True)


if __name__ == "__main__":
    main()
