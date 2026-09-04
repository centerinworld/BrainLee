#!/usr/bin/env python3
"""Audit data readiness for converting strategy-center signals to live orders."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import IS_POSTGRES  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from live_trading_data import ensure_live_data_schema  # noqa: E402


API = "http://127.0.0.1:8000"
OUT_JSON = ROOT / "research_outputs" / "strategy_center_live_data_readiness_latest.json"
OUT_MD = ROOT / "research_outputs" / "strategy_center_live_data_readiness_latest.md"
CUTOVER = ROOT / "research_outputs" / "postgres_cutover" / "verification_latest.json"
PRICE_INTEGRITY = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"
CRITICAL_DATASETS = {"program_stock", "investor_flow", "short_balance"}


def audit() -> dict:
    ensure_live_data_schema()
    conn = connect_stock_db(readonly=True)
    try:
        latest_date = str(conn.execute("SELECT MAX(date) FROM price_history").fetchone()[0])[:10]
        universe_count = int(conn.execute("SELECT COUNT(*) FROM stock_universe").fetchone()[0])
        blocked_universe_count = int(conn.execute(
            """SELECT COUNT(*) FROM stock_universe u
               WHERE EXISTS(
                 SELECT 1 FROM trading_restrictions r
                 WHERE r.stock_code=u.stock_code AND r.is_tradable=0
                   AND r.source='KIS_NO_CURRENT_TRADE'
               )"""
        ).fetchone()[0])
        effective_universe_count = universe_count - blocked_universe_count
        price_row = conn.execute(
            """
            SELECT COUNT(DISTINCT p.stock_code),
                   COUNT(*) FILTER (WHERE p.open IS NULL OR p.high IS NULL OR p.low IS NULL OR p.close IS NULL OR p.volume IS NULL),
                   COUNT(*) FILTER (WHERE COALESCE(p.trade_amount,0)>0)
            FROM price_history p JOIN stock_universe u ON u.stock_code=p.stock_code
            WHERE p.date=?
              AND NOT EXISTS(
                SELECT 1 FROM trading_restrictions r
                WHERE r.stock_code=u.stock_code AND r.is_tradable=0
                  AND r.source='KIS_NO_CURRENT_TRADE'
              )
            """,
            (latest_date,),
        ).fetchone()
        latest_price_codes = int(price_row[0])
        price_missing_required = int(price_row[1])
        positive_trade_amount = int(price_row[2])
        missing_latest = conn.execute(
            """SELECT u.stock_code,u.stock_name,MAX(p.date)
               FROM stock_universe u LEFT JOIN price_history p ON p.stock_code=u.stock_code
               WHERE NOT EXISTS(
                 SELECT 1 FROM price_history x WHERE x.stock_code=u.stock_code AND x.date=?
               )
               GROUP BY u.stock_code,u.stock_name ORDER BY MAX(p.date) DESC""",
            (latest_date,),
        ).fetchall()

        financial = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT stock_code) FROM financial_data WHERE is_annual IS FALSE"
        ).fetchone()
        fin_dates = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT stock_code),COUNT(*) FILTER (WHERE avail_date IS NOT NULL) FROM fin_disclosure_dates"
        ).fetchone()
        shares = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT stock_code),MAX(effective_from) FROM security_share_history"
        ).fetchone()
        dilution = conn.execute(
            """SELECT COUNT(*),COUNT(*) FILTER (WHERE issue_amount IS NOT NULL),MAX(disclosed_at)
               FROM dilution_events
               WHERE event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS')
                 AND COALESCE(risk_amount_status,'amount_confirmed')<>'not_amount_applicable'"""
        ).fetchone()
        actions = conn.execute(
            """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE adjustment_status='factor_confirmed'),
                   COUNT(*) FILTER (WHERE adjustment_status='review_required'),
                   COUNT(*) FILTER (WHERE adjustment_status='not_price_adjusting')
            FROM corporate_action_events
            """
        ).fetchone()
        price_jumps = conn.execute(
            """SELECT COUNT(*),COUNT(*) FILTER (WHERE return_usable=0),MAX(audited_at)
               FROM price_jump_audit"""
        ).fetchone()
        realtime = conn.execute(
            "SELECT COUNT(*),MAX(updated_at) FROM kiwoom_realtime_quote"
        ).fetchone()
        table_names = {
            row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
        }
    finally:
        conn.close()

    dashboard = requests.get(f"{API}/api/dashboard/stats", timeout=30).json()
    unhealthy = [
        {
            "key": item.get("key"), "status": item.get("status"),
            "source_as_of": item.get("source_as_of"), "issues": item.get("issues") or [],
        }
        for item in dashboard.get("dataset_health", [])
        if item.get("key") in CRITICAL_DATASETS and item.get("status") != "healthy"
    ]
    matrix = requests.get(f"{API}/api/backtest/matrix?include_legacy=false", timeout=30).json()
    verification_counts: dict[str, int] = {}
    for row in matrix.get("strategies") or []:
        status = (row.get("governance") or {}).get("verification_status") or "unknown"
        verification_counts[status] = verification_counts.get(status, 0) + 1

    cutover = json.loads(CUTOVER.read_text(encoding="utf-8")) if CUTOVER.exists() else {}
    selected_price_integrity = (
        json.loads(PRICE_INTEGRITY.read_text(encoding="utf-8"))
        if PRICE_INTEGRITY.exists() else {}
    )
    share_gaps = [
        item for item in cutover.get("postgres_behind", [])
        if item.get("table") in {"krx_security_share_snapshot", "security_share_history"}
    ]
    missing_execution_data = sorted(
        name for name in ("orderbook_snapshots", "trading_restrictions", "broker_order_reconciliation")
        if name not in table_names
    )

    coverage_pct = latest_price_codes / effective_universe_count * 100 if effective_universe_count else 0.0
    action_confirmed_pct = int(actions[1]) / int(actions[0]) * 100 if actions[0] else 0.0
    dilution_amount_pct = int(dilution[1]) / int(dilution[0]) * 100 if dilution[0] else 0.0
    blockers = []
    if latest_price_codes < effective_universe_count:
        blockers.append({
            "code": "LATEST_PRICE_UNIVERSE_INCOMPLETE",
            "evidence": f"{latest_price_codes}/{effective_universe_count} tradable codes ({coverage_pct:.2f}%), missing={effective_universe_count-latest_price_codes}",
        })
    warnings = []
    if int(actions[2]) > 0:
        warnings.append({
            "code": "CORPORATE_ACTION_REVIEW_BACKLOG",
            "evidence": (
                f"confirmed factors={int(actions[1])}/{int(actions[0])} ({action_confirmed_pct:.2f}%), "
                f"not_price_adjusting={int(actions[3])}, review_required={int(actions[2])}; "
                "candidate gate checks recent actions and audited price jumps"
            ),
        })
    if positive_trade_amount < latest_price_codes:
        warnings.append({
            "code": "DAILY_TURNOVER_ARCHIVE_PENDING",
            "evidence": (
                f"positive archived trade_amount={positive_trade_amount}/{latest_price_codes}; "
                "candidate preflight requires fresh KIS intraday turnover and does not use close*volume"
            ),
        })
    if unhealthy:
        blockers.append({
            "code": "CRITICAL_FLOW_DATA_UNHEALTHY",
            "evidence": json.dumps(unhealthy, ensure_ascii=False),
        })
    if share_gaps:
        blockers.append({
            "code": "POINT_IN_TIME_SHARE_HISTORY_INCOMPLETE",
            "evidence": json.dumps(share_gaps, ensure_ascii=False),
        })
    if selected_price_integrity.get("failed") or selected_price_integrity.get("no_trade_evidence"):
        blockers.append({
            "code": "SELECTED_RUN_PRICE_INTEGRITY_INCOMPLETE",
            "evidence": (
                f"passed={selected_price_integrity.get('passed',0)}/"
                f"{selected_price_integrity.get('strategy_count',0)}, "
                f"failed={selected_price_integrity.get('failed',0)}, "
                f"no_trade_evidence={selected_price_integrity.get('no_trade_evidence',0)}; "
                f"global return-unusable jumps={int(price_jumps[1])}/{int(price_jumps[0])}"
            ),
        })
    if int(dilution[1]) < int(dilution[0]):
        blockers.append({
            "code": "DILUTION_AMOUNT_PARTIAL",
            "evidence": f"issue_amount={int(dilution[1])}/{int(dilution[0])} ({dilution_amount_pct:.2f}%)",
        })
    if missing_execution_data:
        blockers.append({
            "code": "NO_TRADABILITY_OR_ORDERBOOK_DATA_CONTRACT",
            "evidence": f"missing operational datasets={missing_execution_data}",
        })
    if any(key != "forward_validated" for key in verification_counts):
        blockers.append({
            "code": "BACKTEST_EVIDENCE_NOT_FULL_PIT_OR_FORWARD",
            "evidence": json.dumps(verification_counts, ensure_ascii=False),
        })

    passes = [
        {
            "code": "POSTGRES_PRIMARY",
            "evidence": f"IS_POSTGRES={IS_POSTGRES}",
        },
        {
            "code": "LATEST_OHLCV_REQUIRED_FIELDS",
            "evidence": f"latest date={latest_date}, rows with missing OHLCV={price_missing_required}",
        },
        {
            "code": "FINANCIAL_AND_AVAILABILITY_BASE",
            "evidence": (
                f"quarterly financial rows={int(financial[0])}, codes={int(financial[1])}; "
                f"availability rows={int(fin_dates[0])}, dated={int(fin_dates[2])}, codes={int(fin_dates[1])}"
            ),
        },
        {
            "code": "REALTIME_CLOSE_SNAPSHOT_BASE",
            "evidence": f"quote codes={int(realtime[0])}, latest={realtime[1]}",
        },
    ]
    if not missing_execution_data:
        passes.append({
            "code": "LIVE_EXECUTION_DATA_CONTRACT",
            "evidence": "trading restrictions, orderbook snapshots, and broker reconciliation schemas are installed",
        })
    if not share_gaps:
        passes.append({
            "code": "POSTGRES_SHARE_HISTORY_PARITY",
            "evidence": "PostgreSQL is not behind SQLite for share snapshots or as-of share history",
        })
    if latest_price_codes == effective_universe_count:
        passes.append({
            "code": "LATEST_TRADABLE_PRICE_COVERAGE",
            "evidence": (
                f"{latest_price_codes}/{effective_universe_count} tradable candidates covered; "
                f"{blocked_universe_count} no-trade/unavailable codes fail closed"
            ),
        })

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": "READY" if not blockers else "BLOCKED",
        "ready_for_live_orders": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "passes": passes,
        "metrics": {
            "latest_price_date": latest_date,
            "latest_price_codes": latest_price_codes,
            "universe_codes": universe_count,
            "effective_tradable_universe_codes": effective_universe_count,
            "fail_closed_universe_codes": blocked_universe_count,
            "latest_price_coverage_pct": round(coverage_pct, 2),
            "latest_positive_trade_amount": positive_trade_amount,
            "missing_latest_price_samples": [
                {"stock_code": row[0], "stock_name": row[1], "last_price_date": str(row[2])}
                for row in missing_latest[:20]
            ],
            "share_history_rows": int(shares[0]),
            "share_history_codes": int(shares[1]),
            "share_history_latest": str(shares[2]),
            "corporate_action_confirmed_pct": round(action_confirmed_pct, 2),
            "dilution_amount_coverage_pct": round(dilution_amount_pct, 2),
            "verification_counts": verification_counts,
        },
    }


def write(result: dict) -> None:
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 전략센터 실전매매 데이터 준비도",
        "",
        f"- 점검시각: {result['checked_at']}",
        f"- 결론: **{result['verdict']}**",
        "",
        "## 사용 가능한 기반",
        "",
    ]
    for item in result["passes"]:
        lines.append(f"- **{item['code']}**: {item['evidence']}")
    lines.extend(["", "## 운영 경고", ""])
    for item in result["warnings"]:
        lines.append(f"- **{item['code']}**: {item['evidence']}")
    lines.extend(["", "## 실전 차단 데이터", ""])
    for index, item in enumerate(result["blockers"], 1):
        lines.append(f"{index}. **{item['code']}**: {item['evidence']}")
    lines.extend([
        "",
        "실전 전환은 모든 주문 후보가 최신성·거래가능성·유동성·기업행사·희석·PIT 검증을 통과하고,",
        "주문 직전 호가와 주문 후 브로커 체결을 대조할 수 있을 때만 허용한다.",
        "",
    ])
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    result = audit()
    write(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready_for_live_orders"] else 1)
