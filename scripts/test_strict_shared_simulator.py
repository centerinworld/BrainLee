#!/usr/bin/env python3
"""C3-C6 regression contracts for the shared strict simulator."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account
from run_registry import derive_status, ensure_schema, register_artifact, select_run
from security_master import resolve_security


def test_historical_security_intervals() -> None:
    conn = sqlite3.connect(ROOT / "stock.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT stock_code,effective_from,effective_to
        FROM security_master_history
        WHERE interval_quality='krx_delisting_reference'
          AND is_tradable=1
          AND effective_to IS NOT NULL
        ORDER BY stock_code LIMIT 20
        """
    ).fetchall()
    assert len(rows) == 20
    for row in rows:
        assert resolve_security(conn, row["stock_code"], row["effective_from"]).eligible
        assert not resolve_security(conn, row["stock_code"], row["effective_to"]).eligible
    assert conn.execute(
        "SELECT COUNT(*) FROM security_master_history WHERE interval_quality='krx_delisting_reference'"
    ).fetchone()[0] >= 200
    assert resolve_security(conn, "000145", "2026-07-13").eligible
    assert not resolve_security(conn, "069500", "2026-07-13").eligible
    assert conn.execute(
        """SELECT COUNT(*) FROM security_master_history
           WHERE interval_quality LIKE '%approx%' AND is_tradable=1"""
    ).fetchone()[0] <= 100
    conn.close()


def test_historical_share_intervals() -> None:
    conn = sqlite3.connect(ROOT / "stock.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT stock_code,effective_from,effective_to,shares_issued
        FROM security_share_history
        WHERE quality='official_daily_observed' AND effective_to IS NOT NULL
        ORDER BY stock_code,effective_from LIMIT 20
        """
    ).fetchall()
    assert len(rows) == 20
    for row in rows:
        resolved = resolve_security(conn, row["stock_code"], row["effective_from"])
        assert resolved.eligible
        assert resolved.shares_issued == row["shares_issued"]
    conn.close()


def test_merged_account_contracts() -> None:
    cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000,
                      fee_bps=0, slippage_bps=0, sell_tax_bps=0)
    orders = [
        CandidateOrder("2026-01-02", "DUP", "buy", 10_000, "A", 90),
        CandidateOrder("2026-01-02", "DUP", "buy", 10_000, "B", 80),
    ]
    result = simulate_merged_account(orders, cfg)
    assert result["summary"]["buy_fills"] == 1
    assert result["open_attribution"]["DUP"] == ["A", "B"]

    eleven = [CandidateOrder("2026-01-02", f"S{i:02d}", "buy", 10_000, "A", 100-i) for i in range(11)]
    result = simulate_merged_account(eleven, cfg)
    assert result["summary"]["buy_fills"] == 10
    assert result["summary"]["rejections"] == 1
    rejected = [event for event in result["events"] if event["status"] == "rejected"]
    assert rejected[0]["stock_code"] == "S10"

    rotation = eleven[:10] + [
        CandidateOrder("2026-01-05", "S00", "sell", 10_000, "A", 100),
        CandidateOrder("2026-01-05", "NEW", "buy", 10_000, "B", 100),
    ]
    result = simulate_merged_account(rotation, cfg)
    assert any(event["stock_code"] == "NEW" and event["status"] == "filled" for event in result["events"])
    assert result["summary"]["open_positions"] == 10

    capped = simulate_merged_account([
        CandidateOrder("2026-01-02", "A1", "buy", 10_000, "A", 100, sector="반도체"),
        CandidateOrder("2026-01-02", "A2", "buy", 10_000, "B", 90, sector="반도체"),
    ], MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000,
                   fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                   max_sector_positions=1))
    assert capped["summary"]["buy_fills"] == 1
    assert any(event.get("reason") == "sector_position_limit" for event in capped["events"])

    budgeted = simulate_merged_account([
        CandidateOrder("2026-01-02", "B1", "buy", 10_000, "A", 100),
        CandidateOrder("2026-01-02", "B2", "buy", 10_000, "A", 90),
    ], MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000,
                   fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                   strategy_budget_weights={"A": 0.1}))
    assert budgeted["summary"]["buy_fills"] == 1
    assert any(event.get("reason") == "strategy_budget_limit" for event in budgeted["events"])


def _temp_registry_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE backtest_runs (
          run_id TEXT PRIMARY KEY,strategy TEXT,status TEXT,start_date TEXT,end_date TEXT
        );
        CREATE TABLE backtest_run_specs (
          run_id TEXT PRIMARY KEY,strategy TEXT,engine_version TEXT,git_commit TEXT,
          signal_timing TEXT,execution_timing TEXT,market_cap_mode TEXT,
          universe_version TEXT,allocation_rule TEXT,fee_model TEXT,
          parameter_json TEXT,run_hash TEXT,created_at TEXT
        );
        INSERT INTO backtest_runs VALUES ('r1','demo','done','2020-01-01','2021-01-01');
        INSERT INTO backtest_run_specs VALUES
          ('r1','demo','v1','abc','close_D','next_open','asof_approx',
           'security_master_history_approx','dynamic','fees','{}','hash1','2026-01-01');
        """
    )
    ensure_schema(conn)
    conn.commit()
    conn.close()


def test_registry_and_automatic_badges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "registry.db")
        _temp_registry_db(db)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        assert derive_status(conn, "hash1")["status"] == "legacy"
        conn.close()
        register_artifact("hash1", "execution_contract", True, {"integer_shares": True}, db)
        register_artifact("hash1", "cash_reconciliation", True, {"delta": 0}, db)
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        assert derive_status(conn, "hash1")["status"] == "point_in_time_approx"
        conn.execute("UPDATE backtest_run_specs SET market_cap_mode='pit',universe_version='security_master_history_v1' WHERE run_hash='hash1'")
        conn.commit(); conn.close()
        register_artifact("hash1", "point_in_time_coverage", True, {"coverage": 1.0}, db)
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        assert derive_status(conn, "hash1")["status"] == "point_in_time_verified"
        conn.execute("UPDATE backtest_run_specs SET fee_model='' WHERE run_hash='hash1'")
        conn.commit()
        assert derive_status(conn, "hash1")["status"] == "legacy"
        conn.execute("UPDATE backtest_run_specs SET fee_model='fees' WHERE run_hash='hash1'")
        conn.commit()
        assert derive_status(conn, "hash1")["status"] == "point_in_time_verified"
        conn.close()
        selected = select_run("demo", "strategy_center", "hash1", db_path=db)
        assert selected["status"] == "point_in_time_verified"
        try:
            select_run("demo", "strategy_center", "missing", db_path=db)
        except ValueError:
            pass
        else:
            raise AssertionError("run without immutable spec must not be selected")


def test_frontend_has_no_manual_verification_sources() -> None:
    source = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    for forbidden in ("엄격검증 완료", "LEGACY_PERIOD_RETURNS", "auditTag:", "bestPeriod:",
                      "const auditNotes", "const highProfitBacktest", "avg6=+47.6%",
                      "avg6=+29.5%"):
        assert forbidden not in source, f"manual frontend verification source remains: {forbidden}"


def test_selected_strategy_center_suite_identity() -> None:
    conn = sqlite3.connect(ROOT / "stock.db")
    conn.row_factory = sqlite3.Row
    selected = conn.execute(
        """SELECT run_hash FROM selected_run_registry
           WHERE strategy='v10' AND report_type='strategy_center'"""
    ).fetchone()
    assert selected
    status = derive_status(conn, selected["run_hash"])
    assert status.get("is_suite") is True
    assert len(status.get("components", [])) == 6
    assert len({item["run_hash"] for item in status["components"]}) == 6
    conn.close()


if __name__ == "__main__":
    test_historical_security_intervals()
    test_historical_share_intervals()
    test_merged_account_contracts()
    test_registry_and_automatic_badges()
    test_frontend_has_no_manual_verification_sources()
    test_selected_strategy_center_suite_identity()
    print("C3-C6 ALL PASS")
