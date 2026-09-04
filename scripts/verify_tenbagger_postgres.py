#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import IS_POSTGRES  # noqa: E402
from db_compat import connect_primary_db, primary_database_label  # noqa: E402
from db_utils import STOCK_DB_PATH  # noqa: E402
from tenbagger_engine import (  # noqa: E402
    _fetch_candidates,
    _fetch_extra_signals,
    _fetch_financials,
    _fetch_price_data,
    _fetch_supply,
    _passes_tenbagger_guardrails,
)


REQUIRED_TABLES = [
    "stock_universe",
    "price_history",
    "financial_data",
    "cash_flow_data",
    "dilution_events",
    "tenbagger_results",
    "tenbagger_ai_analysis",
    "tenbagger_daily_alerts",
    "broker_program_stock_daily",
    "cash_conversion_signals",
    "contract_advance_signals",
    "cost_breakdown",
    "cost_structure",
    "dart_contracts",
    "dart_disclosures",
    "dart_insider_holdings",
    "dart_material_purchase",
    "dart_rd_patent_signals",
    "inventory_sales_signals",
    "investor_flow_quarterly",
    "kiwoom_credit_balance",
    "kiwoom_foreign_flow",
    "margin_balance_daily",
    "order_backlog",
    "order_contracts",
    "quant_major_indicator_series",
    "segment_revenue",
    "short_sell_daily",
    "stock_collection_config",
    "strategy_feature_snapshot",
    "treasury_buyback",
    "valuation_history",
]

AUDIT_PATH = ROOT / "research_outputs" / "tenbagger_claude_change_audit_20260810.json"
DISCOVERY_PATH = ROOT / "research_outputs" / "historical_tenbagger_signal_discovery.json"
CONFIRMATION_PATH = ROOT / "research_outputs" / "tenbagger_confirmation_filters_20260811.json"
SURVIVORSHIP_PATH = ROOT / "research_outputs" / "tenbagger_survivorship_bias_20260811.json"
WALKFORWARD_PATH = ROOT / "research_outputs" / "tenbagger_walkforward_cohorts_20260811.json"
OUT_PATH = ROOT / "research_outputs" / "postgres_cutover" / "tenbagger_verification_latest.json"
PIT_SNAPSHOT_TABLE = "strategy_feature_snapshot_pit_v2"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    failures: list[str] = []
    report: dict = {"primary": primary_database_label(), "is_postgres": IS_POSTGRES}
    if not IS_POSTGRES:
        failures.append("POSTGRES_DATABASE_URL is not active")

    sqlite_conn = sqlite3.connect(str(STOCK_DB_PATH))
    pg_conn = connect_primary_db()
    try:
        parity = {}
        for table in REQUIRED_TABLES:
            sqlite_count = sqlite_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            try:
                postgres_count = pg_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except Exception as exc:
                failures.append(f"{table}: missing or unreadable in PostgreSQL: {exc}")
                continue
            # The cutover merges rows written to PostgreSQL after the SQLite
            # snapshot. PostgreSQL may therefore be ahead, but never behind.
            matches = postgres_count >= sqlite_count
            parity[table] = {
                "sqlite": sqlite_count,
                "postgres": postgres_count,
                "postgres_ahead_by": postgres_count - sqlite_count,
                "matches": matches,
            }
            if not matches:
                failures.append(
                    f"{table}: PostgreSQL is behind SQLite ({postgres_count} vs {sqlite_count})"
                )
        report["row_count_parity"] = parity

        started = time.monotonic()
        candidates = _fetch_candidates(pg_conn)
        if not candidates:
            failures.append("no tenbagger candidates returned")
        else:
            probe = None
            price = financials = None
            for candidate in candidates[:200]:
                candidate_price = _fetch_price_data(pg_conn, candidate["stock_code"])
                candidate_financials = _fetch_financials(pg_conn, candidate["stock_code"])
                if candidate_price and candidate_financials:
                    probe = candidate
                    price = candidate_price
                    financials = candidate_financials
                    break
            if probe is None:
                failures.append("no candidate with complete price and financial inputs")
                probe = candidates[0]
                price = _fetch_price_data(pg_conn, probe["stock_code"])
                financials = _fetch_financials(pg_conn, probe["stock_code"])
            code = probe["stock_code"]
            supply = _fetch_supply(pg_conn, code)
            extra = _fetch_extra_signals(pg_conn, code)
            guardrails = _passes_tenbagger_guardrails(price, financials, supply, probe, extra)
            report["calculation_probe"] = {
                "stock_code": code,
                "candidate_count": len(candidates),
                "price_loaded": bool(price),
                "financials_loaded": bool(financials),
                "supply_quality": supply.get("supply_data_quality"),
                "extra_signal_keys": sorted(key for key, value in extra.items() if value is not None),
                "guardrails": guardrails,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            if not price or not financials:
                failures.append(f"{code}: required calculation inputs missing")

        latest = pg_conn.execute(
            "SELECT run_time, run_type, COUNT(*) AS count "
            "FROM tenbagger_results GROUP BY run_time, run_type ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        report["latest_run"] = dict(latest) if latest else None
        if not latest:
            failures.append("tenbagger_results has no saved run")

        pit_quality = pg_conn.execute(
            f"""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT stock_code) AS stocks,
                   COUNT(*) - COUNT(DISTINCT snapshot_date || ':' || stock_code) AS duplicate_rows,
                   SUM(CASE WHEN label_10x_24m IS NOT NULL THEN 1 ELSE 0 END) AS labeled_rows,
                   SUM(CASE WHEN forward_min_ret_24m IS NOT NULL
                                 AND pre_peak_min_ret_24m IS NOT NULL
                                 AND payoff_to_pain_24m IS NOT NULL
                            THEN 1 ELSE 0 END) AS risk_labeled_rows
            FROM {PIT_SNAPSHOT_TABLE}
            """
        ).fetchone()
        pit_interval_violations = pg_conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {PIT_SNAPSHOT_TABLE} p
            WHERE NOT EXISTS (
                SELECT 1 FROM security_master_history m
                WHERE m.stock_code=p.stock_code
                  AND p.snapshot_date >= m.effective_from
                  AND p.snapshot_date <= COALESCE(m.effective_to, p.snapshot_date)
                  AND m.is_tradable=1 AND m.is_etf_etn=0
                  AND m.market IN ('KOSPI','KOSDAQ')
            )
            """
        ).fetchone()
        report["pit_snapshot_quality"] = {
            **dict(pit_quality),
            "security_interval_violations": int(pit_interval_violations["count"]),
        }
        if int(pit_quality["rows"]) < 100_000:
            failures.append(f"{PIT_SNAPSHOT_TABLE}: insufficient rows")
        if int(pit_quality["risk_labeled_rows"]) < 100_000:
            failures.append(f"{PIT_SNAPSHOT_TABLE}: insufficient path-risk labels")
        if int(pit_quality["duplicate_rows"]) != 0:
            failures.append(f"{PIT_SNAPSHOT_TABLE}: duplicate snapshot keys")
        if int(pit_interval_violations["count"]) != 0:
            failures.append(f"{PIT_SNAPSHOT_TABLE}: security interval violations")
    finally:
        pg_conn.close()
        sqlite_conn.close()

    audit = _load_json(AUDIT_PATH)
    discovery = _load_json(DISCOVERY_PATH)
    confirmation = _load_json(CONFIRMATION_PATH)
    survivorship = _load_json(SURVIVORSHIP_PATH)
    walkforward = _load_json(WALKFORWARD_PATH)
    precision_tier = discovery.get("precision_tier") or {}
    report["historical_validation"] = {
        "assessment": audit.get("overall_assessment", "missing"),
        "production_decision": audit.get("production_decision", "missing"),
        "production_ready": bool(discovery.get("production_ready", False)),
        "auto_trading_allowed": bool(discovery.get("auto_trading_allowed", False)),
        "precision_tier_decision": precision_tier.get("decision", "missing"),
        "precision_target_pass": bool(precision_tier.get("precision_target_pass", False)),
        "snapshot_table": discovery.get("methodology", {}).get("snapshot_table", "missing"),
        "confirmation_promoted_rules": confirmation.get("promoted_rules", []),
        "aggregate_risk_adjusted_research_signal_count": len(
            discovery.get("aggregate_risk_adjusted_research_signals", [])
        ),
        "walkforward_stable_every_year": walkforward.get(
            "candidate_stability", {}
        ).get("stable_every_year"),
        "sustainable_signal_count": walkforward.get(
            "candidate_stability", {}
        ).get("sustainable_signal_count"),
        "survivorship_missing_clean_10x_rate_pct": survivorship.get(
            "missing_historical_equities", {}
        ).get("clean_10x_row_rate_pct"),
        "evidence": [
            str(AUDIT_PATH), str(DISCOVERY_PATH), str(CONFIRMATION_PATH),
            str(SURVIVORSHIP_PATH), str(WALKFORWARD_PATH)
        ],
    }
    if not audit:
        failures.append(f"historical audit evidence missing: {AUDIT_PATH}")
    if not discovery:
        failures.append(f"historical signal evidence missing: {DISCOVERY_PATH}")
    if not confirmation:
        failures.append(f"confirmation evidence missing: {CONFIRMATION_PATH}")
    if not survivorship:
        failures.append(f"survivorship evidence missing: {SURVIVORSHIP_PATH}")
    if not walkforward:
        failures.append(f"walk-forward evidence missing: {WALKFORWARD_PATH}")
    if discovery.get("methodology", {}).get("snapshot_table") != PIT_SNAPSHOT_TABLE:
        failures.append("historical discovery is not based on PIT snapshot")
    if discovery.get("auto_trading_allowed") is not False:
        failures.append("tenbagger historical evidence does not explicitly disable auto trading")
    if confirmation.get("auto_trading_allowed") is not False:
        failures.append("tenbagger confirmation evidence does not explicitly disable auto trading")
    if walkforward.get("auto_trading_allowed") is not False:
        failures.append("tenbagger walk-forward evidence does not explicitly disable auto trading")

    report["ok"] = not failures
    report["failures"] = failures
    report["verified_at"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    tmp_path.replace(OUT_PATH)
    print(rendered)
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
