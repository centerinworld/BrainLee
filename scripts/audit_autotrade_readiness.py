#!/usr/bin/env python3
"""Fail-closed audit explaining whether Korean live auto trading is ready."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import IS_POSTGRES  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402


OUTPUT_JSON = ROOT / "research_outputs" / "autotrade_readiness_latest.json"
OUTPUT_MD = ROOT / "research_outputs" / "autotrade_readiness_latest.md"
CUTOVER_REPORT = ROOT / "research_outputs" / "postgres_cutover" / "verification_latest.json"
API_BASE = os.getenv("AUTOTRADE_AUDIT_API_BASE", "http://127.0.0.1:8000")
CRITICAL_DATASETS = {"program_stock", "investor_flow", "short_balance"}


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _api(path: str) -> dict:
    response = requests.get(f"{API_BASE}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def audit() -> dict:
    checked_at = datetime.now().isoformat(timespec="seconds")
    blockers: list[dict] = []
    warnings: list[dict] = []

    def block(code: str, evidence: str, remediation: str) -> None:
        blockers.append({"code": code, "evidence": evidence, "remediation": remediation})

    conn = connect_stock_db(readonly=True)
    try:
        strategy_completed_exits = int(conn.execute(
            "SELECT COUNT(DISTINCT p.id) FROM peak_trade p "
            "JOIN live_orders o ON o.decision_reason = "
            "('strategy_virtual_ledger:peak_trade:' || CAST(p.id AS TEXT)) "
            "WHERE p.tx_type='sell' AND "
            "(p.strategy LIKE 'sc_%' OR p.strategy IN ('v_contract_momentum', 'v_gc', 'v_recovery'))"
        ).fetchone()[0])
        paper = {
            "orders": _count(conn, "kis_paper_orders"),
            "positions": _count(conn, "kis_paper_positions"),
            "closed_trades": _count(conn, "kis_paper_realized"),
            "strategy_completed_exits": strategy_completed_exits,
            "lifecycle_orders": _count(conn, "live_orders"),
            "fills": _count(conn, "live_fills"),
            "risk_decisions": _count(conn, "risk_gate_decisions"),
        }
        if paper["strategy_completed_exits"] < 30:
            block(
                "PAPER_FORWARD_SAMPLE_MISSING",
                "strategy completed exits="
                f"{paper['strategy_completed_exits']} (minimum evidence gate=30)",
                "Run the exact production signal and execution path in PAPER mode until at least 30 independent exits exist.",
            )
        if paper["lifecycle_orders"] == 0 or paper["fills"] == 0:
            block(
                "ORDER_LIFECYCLE_UNPROVEN",
                f"lifecycle orders={paper['lifecycle_orders']}, fills={paper['fills']}",
                "Verify submission, partial fill, rejection, cancellation, and reconciliation using paper/broker test orders.",
            )
    finally:
        conn.close()

    matrix = _api("/api/backtest/matrix?include_legacy=false")
    strategies = []
    for row in matrix.get("strategies", []):
        governance = row.get("governance") or {}
        periods = list((row.get("periods") or {}).values())
        strategy = {
            "strategy": row.get("strategy"),
            "tier": governance.get("tier"),
            "verification": governance.get("verification_status"),
            "auto_trading_allowed": bool(governance.get("auto_trading_allowed")),
            "average_return_pct": (governance.get("metrics") or {}).get("average_return_pct"),
            "worst_period_return_pct": (governance.get("metrics") or {}).get("worst_period_return_pct"),
            "trade_count": sum(int(period.get("trade_count") or 0) for period in periods),
            "missing_risk_periods": sum(
                period.get("mdd") is None or period.get("sharpe") is None or period.get("pl_ratio") is None
                for period in periods
            ),
        }
        strategies.append(strategy)

    forward = [s for s in strategies if s["verification"] == "forward_validated"]
    allowed = [s for s in strategies if s["auto_trading_allowed"]]
    if not forward:
        block(
            "NO_FORWARD_VALIDATED_STRATEGY",
            "forward_validated strategies=0",
            "Freeze parameters and pass an untouched chronological forward-validation window.",
        )
    if not allowed:
        block(
            "GOVERNANCE_DENIES_LIVE",
            "auto_trading_allowed strategies=0",
            "Promote only after forward validation and paper execution evidence; do not override the policy flag.",
        )

    linked = next((s for s in strategies if s["strategy"] == "se_momentum"), None)
    if linked and linked["tier"] == "retired":
        block(
            "LIVE_PATH_POINTS_TO_RETIRED_STRATEGY",
            f"se_momentum tier=retired, worst period={linked['worst_period_return_pct']}%",
            "Keep StockEasy momentum live eligibility disabled and connect only a separately approved production strategy.",
        )

    candidate = next((s for s in strategies if s["strategy"] == "contract_momentum"), None)
    if candidate and candidate["missing_risk_periods"]:
        block(
            "CANDIDATE_RISK_METRICS_MISSING",
            f"{candidate['strategy']} has {candidate['missing_risk_periods']} period(s) missing MDD/Sharpe/profit-loss ratio",
            "Re-run the selected suite with complete risk and concentration attribution before promotion.",
        )

    dashboard = _api("/api/dashboard/stats")
    unhealthy = []
    for item in dashboard.get("dataset_health", []):
        if item.get("key") in CRITICAL_DATASETS and item.get("status") != "healthy":
            unhealthy.append({
                "key": item.get("key"),
                "status": item.get("status"),
                "source_as_of": item.get("source_as_of"),
                "issues": item.get("issues") or [],
            })
    if unhealthy:
        block(
            "CRITICAL_INPUTS_UNHEALTHY",
            json.dumps(unhealthy, ensure_ascii=False),
            "Repair collectors and require healthy freshness/coverage contracts at order time.",
        )

    if not IS_POSTGRES:
        block("PRIMARY_DB_NOT_POSTGRES", "IS_POSTGRES=false", "Complete PostgreSQL cutover before live trading.")
    if CUTOVER_REPORT.exists():
        cutover = json.loads(CUTOVER_REPORT.read_text(encoding="utf-8"))
        if not cutover.get("ok"):
            block(
                "POSTGRES_CUTOVER_INCOMPLETE",
                "; ".join(cutover.get("failures") or ["latest cutover verification failed"]),
                "Reconcile every PostgreSQL-behind table and rerun verify_postgres_cutover.py successfully.",
            )
    else:
        block(
            "POSTGRES_CUTOVER_UNVERIFIED",
            f"missing {CUTOVER_REPORT}",
            "Run verify_postgres_cutover.py and retain a successful verification artifact.",
        )

    stockeasy_source = (ROOT / "stockeasy_autotrade.py").read_text(encoding="utf-8")
    if "kis_client.place_order_cash" in stockeasy_source:
        block(
            "LIVE_EXECUTION_BYPASSES_UNIFIED_LIFECYCLE",
            "stockeasy_autotrade.py calls kis_client.place_order_cash directly",
            "Route live orders through one idempotent lifecycle with broker reconciliation and the same strict risk gates.",
        )

    switches = {
        "stockeasy_live_autotrade": os.getenv("STOCKEASY_LIVE_AUTOTRADE", "false").lower() == "true",
        "kis_trading_mode": os.getenv("KIS_TRADING_MODE", "PAPER").upper(),
        "kis_live_order_enable": os.getenv("KIS_LIVE_ORDER_ENABLE", "false").lower() == "true",
        "approved_strategies": [
            value.strip() for value in os.getenv("STOCKEASY_LIVE_APPROVED_STRATEGIES", "").split(",") if value.strip()
        ],
    }
    if switches["stockeasy_live_autotrade"] or switches["kis_trading_mode"] == "LIVE" or switches["kis_live_order_enable"]:
        warnings.append({"code": "LIVE_SWITCH_SET_WHILE_BLOCKED", "evidence": switches})

    return {
        "checked_at": checked_at,
        "ready_for_live_autotrading": not blockers,
        "verdict": "BLOCKED" if blockers else "READY",
        "blocker_count": len(blockers),
        "blockers": blockers,
        "warnings": warnings,
        "switches": switches,
        "paper_evidence": paper,
        "strategy_governance": matrix.get("governance"),
        "strategies": strategies,
        "critical_data_health": unhealthy,
        "primary_database": "postgresql" if IS_POSTGRES else "sqlite",
    }


def write_reports(result: dict) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 자동매매 준비도 감사",
        "",
        f"- 점검시각: {result['checked_at']}",
        f"- 결론: **{result['verdict']}**",
        f"- 차단 사유: {result['blocker_count']}건",
        f"- 운영 DB: {result['primary_database']}",
        "",
        "## 차단 사유",
        "",
    ]
    for index, item in enumerate(result["blockers"], 1):
        lines.extend([
            f"{index}. **{item['code']}**",
            f"   - 증거: {item['evidence']}",
            f"   - 해제 조건: {item['remediation']}",
        ])
    lines.extend([
        "",
        "## 페이퍼 증거",
        "",
        "```json",
        json.dumps(result["paper_evidence"], ensure_ascii=False, indent=2),
        "```",
        "",
        "자동매매는 모든 차단 사유가 0건이 되기 전까지 활성화하지 않는다.",
        "",
    ])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    result = audit()
    write_reports(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready_for_live_autotrading"] else 1)
