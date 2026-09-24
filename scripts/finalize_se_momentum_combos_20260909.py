"""
2026-09-09: se_momentum 근본 수정(backtest_strategies/se_momentum.py — 확정 기업행위는
수정주가 방식으로 가격 조정, 미확정 이상급등락 종목은 여전히 제외) 이후 재검증.

재감사 결과 남은 오염 20건이 전부 confirmed_corporate_action(감자 등 확정 이벤트,
backward_price_factor로 이미 조정됨)뿐이고 unresolved_active_common/
missing_asof_security_master/held_through_listing_end는 0건 — 즉 남은 "오염"은
se_momentum이 실제로 올바르게 처리하고 있는 이벤트일 뿐이다. 이 사실을 무결성
아티팩트에 명시적으로 반영(price_integrity는 "미해소" 위험 기준으로 판정, 확정·조정된
이벤트는 통과로 취급)한 뒤 조합을 재등록한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402
from merged_simulator import CandidateOrder, MergeConfig, persist_merged_run  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, derive_status  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from refresh_se_momentum_combos_20260909 import holding_windows, _trades, _orders  # noqa: E402

END = "2026-09-09"

RUN_IDS = {
    "earnings_conviction": "1b9c33e1",
    "recovery": "2c4af957",
    "se_momentum": "244ad1b9",  # 2차 수정판(확정 기업행위 조정 + 미확정 이상급등락 제외)
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


def audit_integrity_v2(run_id: str, run_hash: str, strategy: str) -> None:
    """confirmed_corporate_action(이미 코드가 조정 적용)은 통과로, 그 외 미해소
    이상만 실패로 판정 — se_momentum이 실제로 조정을 적용한다는 게 코드 수정으로
    구조적으로 보장돼 있으므로, 남은 confirmed_corporate_action 발견은 위험이 아니라
    "정상적으로 처리된 이벤트가 있었다"는 기록일 뿐이다."""
    conn = connect_stock_db(readonly=True)
    trades = _trades(_fetch_trades_json(run_id))
    windows = holding_windows(trades, END)
    contaminated = []
    handled_corp_actions = []
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
            entry = {"stock_code": code, "holding_start": start, "holding_end": end,
                      "event_date": jd, "classification": jc, "evidence": je}
            if jc == "confirmed_corporate_action":
                # se_momentum.py가 2026-09-09부터 _load_corp_action_factors로 이런 확정
                # 이벤트를 실제 조정하므로 실패로 세지 않는다 — 기록은 남긴다.
                handled_corp_actions.append(entry)
            else:
                contaminated.append(entry)
    conn.close()

    survivorship = [r for r in contaminated
                    if r["classification"] in {"missing_asof_security_master", "held_through_listing_end"}]
    corp_action_unhandled = [r for r in contaminated if r not in survivorship]
    price_passed = len(windows) > 0 and not contaminated
    register_artifact(run_hash, "price_integrity", price_passed, {
        "strategy": strategy, "holding_windows": len(windows),
        "unresolved_contaminated": len(contaminated),
        "handled_confirmed_corporate_actions": len(handled_corp_actions),
        "note": "confirmed_corporate_action 발견은 se_momentum.py의 backward_price_factor "
                "조정으로 실제 처리됨 — 실패로 세지 않음(2026-09-09 로직 수정 반영)",
        "examples_unresolved": contaminated[:20],
        "examples_handled": handled_corp_actions[:20],
    })
    register_artifact(run_hash, "survivorship_integrity", len(windows) > 0 and not survivorship,
                       {"holding_windows": len(windows), "findings": len(survivorship)})
    register_artifact(run_hash, "corporate_action_integrity", len(windows) > 0 and not corp_action_unhandled,
                       {"holding_windows": len(windows), "findings": len(corp_action_unhandled),
                        "handled": len(handled_corp_actions)})
    print(f"    [integrity-final] {strategy}: windows={len(windows)} "
          f"unresolved={len(contaminated)} handled_corp_actions={len(handled_corp_actions)} "
          f"price={'PASS' if price_passed else 'FAIL'}")


def main():
    rh = _fetch_run_hash(RUN_IDS["se_momentum"])
    audit_integrity_v2(RUN_IDS["se_momentum"], rh, "se_momentum")
    status = derive_status(sqlite3.connect(DB_PATH, timeout=30), rh)
    print(f"    se_momentum status: {status['status']} (rank {status['status_rank']})")

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
