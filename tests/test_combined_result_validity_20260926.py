import json
import sqlite3

from run_registry import derive_status


def test_failed_result_validity_downgrades_execution_run():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE backtest_runs(run_id TEXT, strategy TEXT, status TEXT, start_date TEXT, end_date TEXT);
        CREATE TABLE backtest_run_specs(run_id TEXT, strategy TEXT, engine_version TEXT, git_commit TEXT,
          signal_timing TEXT, execution_timing TEXT, market_cap_mode TEXT, universe_version TEXT,
          allocation_rule TEXT, fee_model TEXT, parameter_json TEXT, run_hash TEXT, created_at TEXT);
        CREATE TABLE run_verification_artifacts(run_hash TEXT, artifact_type TEXT, passed INTEGER,
          details_json TEXT, artifact_hash TEXT, created_at TEXT);
    """)
    conn.execute("INSERT INTO backtest_runs VALUES('r','v2','done','2020-01-01','2021-01-01')")
    conn.execute("INSERT INTO backtest_run_specs VALUES('r','v2','e','g','close_D','next_open','pit','official','a','fees','{}','h','now')")
    for kind in ('execution_contract','cash_reconciliation','price_integrity','survivorship_integrity','corporate_action_integrity'):
        conn.execute("INSERT INTO run_verification_artifacts VALUES('h',?,1,'{}','x','now')", (kind,))
    conn.execute("INSERT INTO run_verification_artifacts VALUES('h','result_validity',0,?,'x','now')",
                 (json.dumps({'reason': 'stored P&L used a corrupt quote'}),))
    result = derive_status(conn, 'h')
    assert result['status'] == 'legacy'
    assert result['gates']['result_validity'] is False
    assert 'result_validity' in result['reasons']
