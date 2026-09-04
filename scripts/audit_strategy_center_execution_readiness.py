#!/usr/bin/env python3
"""Audit whether strategy-center research can be operated as real buy/sell logic."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from virtual_trading_ledger import account_summary, ensure_schema  # noqa: E402


API = "http://127.0.0.1:8000"
OUT_JSON = ROOT / "research_outputs" / "strategy_center_execution_readiness_latest.json"
OUT_MD = ROOT / "research_outputs" / "strategy_center_execution_readiness_latest.md"
PRICE_INTEGRITY_JSON = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"
LEDGER_MIGRATION_JSON = ROOT / "research_outputs" / "virtual_cash_ledger_migration_latest.json"

# Only these strategy-center strategies have any current-day signal adapter.
STANDALONE_ADAPTERS = {"golden_cross", "recovery", "contract_momentum"}
COMBO_ONLY_ADAPTERS = {
    "v4", "v2", "sector_focus", "v10", "earnings_conviction", "moonshot_turnaround"
}
TRACKED_VIRTUAL = {
    "golden_cross": "v_gc",
    "recovery": "v_recovery",
    "contract_momentum": "v_contract_momentum",
    "combo_605": "combo_605",
    "combo_539": "combo_539",
    "combo_510": "combo_510",
    "combo_474": "combo_474",
    "combo_546": "combo_546",
}
RISK_GATE_EXECUTION_PATHS = ("universal_strategy_order_gateway",)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _matrix() -> dict:
    response = requests.get(f"{API}/api/backtest/matrix?include_legacy=false", timeout=30)
    response.raise_for_status()
    return response.json()


def audit() -> dict:
    matrix = _matrix()
    price_integrity = _read_json(PRICE_INTEGRITY_JSON)
    ledger_migration = _read_json(LEDGER_MIGRATION_JSON)
    strategies = matrix.get("strategies") or []
    keys = {row.get("strategy") for row in strategies}
    adapter_keys = STANDALONE_ADAPTERS | COMBO_ONLY_ADAPTERS
    missing_adapters = sorted(keys - adapter_keys)

    selected_end_dates = sorted({
        str(period.get("end_date"))[:10]
        for row in strategies
        for period in (row.get("periods") or {}).values()
        if period.get("end_date")
    })
    latest_selected_end = max(selected_end_dates, default=None)
    selected_age_days = (
        (date.today() - date.fromisoformat(latest_selected_end)).days if latest_selected_end else None
    )

    conn = connect_stock_db(readonly=True)
    try:
        selected_strategy_count = int(conn.execute(
            "SELECT COUNT(*) FROM selected_run_registry WHERE report_type='strategy_center'"
        ).fetchone()[0])
        virtual_rows = conn.execute(
            """
            SELECT strategy,
                   COUNT(*) FILTER (WHERE tx_type='buy') AS buys,
                   COUNT(*) FILTER (WHERE tx_type='sell') AS sells,
                   COUNT(*) FILTER (WHERE tx_type='sell' AND profit>0) AS wins,
                   COALESCE(SUM(profit) FILTER (WHERE tx_type='sell'),0) AS realized
            FROM peak_trade
            WHERE strategy IN ({})
            GROUP BY strategy
            """.format(",".join("?" * len(TRACKED_VIRTUAL))),
            tuple(TRACKED_VIRTUAL.values()),
        ).fetchall()
        active_rows = conn.execute(
            """
            SELECT strategy,COUNT(*) AS positions,
                   COALESCE(SUM((current_price-buy_price)*quantity),0) AS unrealized
            FROM peak_holding
            WHERE is_active=1 AND strategy IN ({})
            GROUP BY strategy
            """.format(",".join("?" * len(TRACKED_VIRTUAL))),
            tuple(TRACKED_VIRTUAL.values()),
        ).fetchall()
        gate_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(risk_gate_decisions)").fetchall()
        }
        source_filter = (
            "AND decision_source IN ('strategy_virtual_execution','paper_order_execution',"
            "'stockeasy_live_execution')" if "decision_source" in gate_columns else ""
        )
        gate_count = int(conn.execute(
            """
            SELECT COUNT(*) FROM risk_gate_decisions
            WHERE strategy_key IN ({})
            {}
            """.format(",".join("?" * len(TRACKED_VIRTUAL)), source_filter),
            tuple(TRACKED_VIRTUAL.values()),
        ).fetchone()[0])
        ensure_schema(conn)
        ledger_accounts = {
            strategy: account_summary(conn, strategy)
            for strategy in TRACKED_VIRTUAL.values()
        }
    finally:
        conn.close()

    by_virtual = {
        row[0]: {
            "buys": int(row[1]), "sells": int(row[2]), "wins": int(row[3]),
            "win_rate_pct": round(int(row[3]) / int(row[2]) * 100, 1) if row[2] else None,
            "realized_pnl_krw": float(row[4]),
        }
        for row in virtual_rows
    }
    for strategy, positions, unrealized in active_rows:
        by_virtual.setdefault(strategy, {
            "buys": 0, "sells": 0, "wins": 0, "win_rate_pct": None, "realized_pnl_krw": 0.0,
        })
        by_virtual[strategy].update({
            "active_positions": int(positions),
            "unrealized_pnl_krw": float(unrealized),
            "net_pnl_before_costs_krw": float(by_virtual[strategy]["realized_pnl_krw"]) + float(unrealized),
        })

    standalone_results = {
        center_key: by_virtual.get(virtual_key, {})
        for center_key, virtual_key in TRACKED_VIRTUAL.items()
    }
    blockers = [
        {
            "code": "NO_FORWARD_VALIDATED_STRATEGY",
            "evidence": f"forward_validated=0, latest selected suite end={latest_selected_end} ({selected_age_days} days old)",
        },
        {
            "code": "MOST_STRATEGIES_HAVE_NO_ORDERABLE_SIGNAL_ADAPTER",
            "evidence": f"strategy-center={len(keys)}, adapters={len(keys & adapter_keys)}, missing={len(missing_adapters)}",
        },
        {
            "code": "BACKTEST_AND_VIRTUAL_EXECUTION_TIMING_DIFFER",
            "evidence": "selected backtests use close_D -> next_open; standalone virtual engines buy/sell at the latest current close during 20-minute runs",
        },
        {
            "code": "GOLDEN_CROSS_PARAMETER_DRIFT",
            "evidence": "backtest max_positions=10/trails=-30%,-35%; virtual max_positions=8/trails=-25%,-30%",
        },
        {
            "code": "CONTRACT_ENTRY_RULE_DRIFT",
            "evidence": "backtest enters only next trading open after disclosure; virtual can select any still-eligible disclosure from the last 20 calendar days",
        },
        {
            "code": "COMBO_SIGNALS_CAN_REPAINT",
            "evidence": "combo engine reruns 2020-present backtests daily with current-universe modes, then extracts only the latest-date trades",
        },
        {
            "code": "LIVE_LIKE_RESULTS_DO_NOT_CONFIRM_BACKTEST",
            "evidence": json.dumps({
                key: standalone_results.get(key, {})
                for key in ("golden_cross", "recovery", "contract_momentum", "combo_605")
            }, ensure_ascii=False),
        },
    ]
    missing_ledger_accounts = sorted(
        strategy for strategy, summary in ledger_accounts.items() if summary is None
    )
    if missing_ledger_accounts:
        blockers.append({
            "code": "VIRTUAL_CASH_LEDGER_MISSING_ACCOUNTS",
            "evidence": json.dumps(missing_ledger_accounts, ensure_ascii=False),
        })
    warnings = []
    if gate_count == 0:
        warnings.append({
            "code": "RISK_GATE_EXECUTION_NOT_YET_OBSERVED",
            "evidence": (
                "all strategy virtual buy paths are wired fail-closed, but no post-deployment "
                "strategy_virtual_execution decision has been produced yet"
            ),
        })
    invalid_historical_ledgers = {
        key: value for key, value in (ledger_migration.get("results") or {}).items()
        if value.get("status") != "complete"
    }
    if invalid_historical_ledgers:
        warnings.append({
            "code": "LEGACY_VIRTUAL_TRADES_INVALID",
            "evidence": json.dumps(invalid_historical_ledgers, ensure_ascii=False),
        })

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "ready_for_order_execution": False,
        "verdict": "BLOCKED",
        "blockers": blockers,
        "strategy_count": len(keys),
        "standalone_adapter_count": len(keys & STANDALONE_ADAPTERS),
        "combo_only_adapter_count": len(keys & COMBO_ONLY_ADAPTERS),
        "missing_adapter_count": len(missing_adapters),
        "missing_adapters": missing_adapters,
        "latest_selected_backtest_end": latest_selected_end,
        "selected_backtest_age_days": selected_age_days,
        "governance": matrix.get("governance"),
        "virtual_results_before_costs": standalone_results,
        "risk_gate_decision_count": gate_count,
        "risk_gate_connection_count": len(RISK_GATE_EXECUTION_PATHS),
        "risk_gate_registered_strategy_count": selected_strategy_count,
        "risk_gate_execution_paths": list(RISK_GATE_EXECUTION_PATHS),
        "risk_gate_connected": True,
        "risk_gate_execution_observed": gate_count > 0,
        "virtual_cash_ledger_complete": not missing_ledger_accounts,
        "virtual_cash_accounts": ledger_accounts,
        "warnings": warnings,
        "price_integrity_failed_count": int(price_integrity.get("failed", 0) or 0),
        "price_integrity_failed_strategies": [
            row.get("strategy") for row in (price_integrity.get("strategies") or [])
            if row.get("status") == "failed"
        ],
    }


def write(result: dict) -> None:
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 전략센터 매수·매도 실행 준비도 감사",
        "",
        f"- 점검시각: {result['checked_at']}",
        f"- 결론: **{result['verdict']}**",
        f"- 전략: {result['strategy_count']}개",
        f"- 독립 가상매매 어댑터: {result['standalone_adapter_count']}개",
        f"- 병합 신호 전용 어댑터: {result['combo_only_adapter_count']}개",
        f"- 실행 어댑터 없음: {result['missing_adapter_count']}개",
        f"- 선택 백테스트 최종일: {result['latest_selected_backtest_end']}",
        "",
        "## 차단 사유",
        "",
    ]
    for index, item in enumerate(result["blockers"], 1):
        lines.append(f"{index}. **{item['code']}**: {item['evidence']}")
    if result["warnings"]:
        lines.extend(["", "## 관찰 필요", ""])
        for index, item in enumerate(result["warnings"], 1):
            lines.append(f"{index}. **{item['code']}**: {item['evidence']}")
    lines.extend([
        "",
        "## 가상운용 실측",
        "",
        "```json",
        json.dumps(result["virtual_results_before_costs"], ensure_ascii=False, indent=2),
        "```",
        "",
        "과거 가상운용 표는 호환용 원천 수치이며, 비용 차감 후 수치는 virtual_cash_accounts를 사용한다.",
        "",
    ])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    result = audit()
    write(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
