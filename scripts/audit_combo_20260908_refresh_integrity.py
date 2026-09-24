"""
2026-09-08: 사용자 지시("제대로 다 돌려줘") — persist_merged_run의 execution_strict
게이트(rank>=1)를 통과시키기 위해, sector_focus/v2 재실행분(run_hash
24ab3fdb0a28/5d1dbed8b823)에 price_integrity/survivorship_integrity/
corporate_action_integrity 아티팩트를 실제로 검증해서 등록한다.

scripts/audit_selected_strategy_price_integrity.py와 완전히 동일한 검증 로직
(holding_windows, security_master_history as-of 거래가능구간 체크,
price_jump_audit 오염구간 체크)을 재사용하되, 그 스크립트는 selected_run_registry
(전략센터에 "선택된" 구간별 스위트)에 등록된 런만 대상으로 하는 반면, 이번 재실행분은
2020-01-01~2026-09-08 연속 단일 시뮬레이션이라 그 인프라에 맞지 않는다 — 같은 검증
로직을 단일 run_hash 2개에 직접 적용한다. 결과는 대충 통과 처리하지 않고 실제 발견된
오염 건수 그대로 반영한다(0건이면 진짜 0건이라서 통과, 있으면 실패로 정직하게 기록).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402
import sqlite3  # noqa: E402

RUN_HASHES = {
    "sector_focus": ("e1245314", "24ab3fdb0a28"),
    "v2": ("22adc49a", "5d1dbed8b823"),
}


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


def main():
    main_conn = sqlite3.connect(DB_PATH, timeout=60)
    conn = connect_stock_db(readonly=True)

    for strategy, (run_id, run_hash) in RUN_HASHES.items():
        row = main_conn.execute(
            "SELECT trades_json, end_date FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        trades_json, run_end = row
        payload = json.loads(trades_json or "[]")
        trades = payload.get("trades", []) if isinstance(payload, dict) else payload
        period_end = str(run_end or "2026-09-08")[:10]

        windows = holding_windows(trades, period_end)
        contaminated = []
        for code, start, end in windows:
            master = conn.execute(
                """SELECT effective_to FROM security_master_history
                   WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                     AND effective_from<=?
                     AND (effective_to IS NULL OR effective_to>?)
                   ORDER BY effective_from DESC LIMIT 1""",
                (code, start, start),
            ).fetchone()
            if not master:
                contaminated.append({
                    "stock_code": code, "holding_start": start, "holding_end": end,
                    "classification": "missing_asof_security_master",
                    "evidence": "entry date is outside a verified tradable interval",
                })
            end_master = conn.execute(
                """SELECT 1 FROM security_master_history
                   WHERE stock_code=? AND is_tradable=1 AND is_etf_etn=0
                     AND effective_from<=?
                     AND (effective_to IS NULL OR effective_to>?)
                   LIMIT 1""",
                (code, end, end),
            ).fetchone()
            if master and master[0] and str(master[0]) <= end and not end_master:
                contaminated.append({
                    "stock_code": code, "holding_start": start, "holding_end": end,
                    "event_date": str(master[0]),
                    "classification": "held_through_listing_end",
                    "evidence": "position remained open through the security interval end",
                })
            jumps = conn.execute(
                """SELECT event_date,classification,evidence FROM price_jump_audit
                   WHERE stock_code=? AND event_date BETWEEN ? AND ? AND return_usable=0
                   ORDER BY event_date""",
                (code, start, end),
            ).fetchall()
            for jd, jc, je in jumps:
                contaminated.append({
                    "stock_code": code, "holding_start": start, "holding_end": end,
                    "event_date": jd, "classification": jc, "evidence": je,
                })

        survivorship_findings = [
            r for r in contaminated
            if r["classification"] in {"missing_asof_security_master", "held_through_listing_end"}
        ]
        corporate_action_findings = [
            r for r in contaminated
            if r["classification"] not in {"missing_asof_security_master", "held_through_listing_end"}
        ]
        price_passed = len(windows) > 0 and not contaminated

        register_artifact(run_hash, "price_integrity", price_passed, {
            "strategy": strategy, "run_id": run_id, "period_end": period_end,
            "holding_windows": len(windows), "contaminated_windows": len(contaminated),
            "checks": ["unusable_price_jump", "asof_security_master_entry", "listing_interval_end"],
            "examples": contaminated[:20],
        })
        register_artifact(run_hash, "survivorship_integrity",
                           len(windows) > 0 and not survivorship_findings, {
                               "holding_windows": len(windows), "findings": len(survivorship_findings),
                               "examples": survivorship_findings[:20],
                           })
        register_artifact(run_hash, "corporate_action_integrity",
                           len(windows) > 0 and not corporate_action_findings, {
                               "holding_windows": len(windows), "findings": len(corporate_action_findings),
                               "examples": corporate_action_findings[:20],
                           })

        print(f"{strategy}({run_hash}): holding_windows={len(windows)} "
              f"contaminated={len(contaminated)} "
              f"price_integrity={'PASS' if price_passed else 'FAIL'} "
              f"survivorship={'PASS' if not survivorship_findings else 'FAIL'} "
              f"corp_action={'PASS' if not corporate_action_findings else 'FAIL'}")
        if contaminated:
            for c in contaminated[:10]:
                print("   ", c)

    conn.close()
    main_conn.close()


if __name__ == "__main__":
    main()
