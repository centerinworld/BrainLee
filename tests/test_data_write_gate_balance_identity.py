import sqlite3

from data_write_gate import ensure_canonical_schema, gate_financial_row


def _conn():
    conn = sqlite3.connect(":memory:")
    ensure_canonical_schema(conn)
    return conn


def test_repairs_liabilities_when_liabilities_were_mapped_as_assets():
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "total_assets": 100_000_000_000, "total_liabilities": 100_000_000_000, "total_equity": 30_000_000_000},
    )
    assert ok is True
    assert reason == "OK"
    assert row["total_liabilities"] == 70_000_000_000
    assert row["total_equity"] == 30_000_000_000


def test_repairs_equity_when_equity_was_mapped_as_assets():
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "total_assets": 100_000_000_000, "total_liabilities": 70_000_000_000, "total_equity": 100_000_000_000},
    )
    assert ok is True
    assert reason == "OK"
    assert row["total_equity"] == 30_000_000_000


def test_rejects_ambiguous_balance_sheet_instead_of_guessing():
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "total_assets": 100_000_000_000, "total_liabilities": 60_000_000_000, "total_equity": 90_000_000_000},
    )
    assert ok is False
    assert reason == "BS_IDENTITY_AMBIGUOUS"
    assert row["total_equity"] == 90_000_000_000


def test_rejects_negative_revenue():
    conn = _conn()
    ok, _, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "revenue": -1},
    )
    assert ok is False
    assert reason == "NEGATIVE_REVENUE"


def test_rejects_operating_profit_materially_above_revenue():
    conn = _conn()
    ok, _, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "revenue": 100, "operating_profit": 106},
    )
    assert ok is False
    assert reason == "OPERATING_PROFIT_EXCEEDS_REVENUE"
