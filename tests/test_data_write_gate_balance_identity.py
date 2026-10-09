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


def test_allows_cfs_parent_equity_non_controlling_interest_gap():
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {
            "stock_code": "009150",
            "report_type": "CFS",
            "total_assets": 16_565_974_324_511,
            "total_liabilities": 6_036_832_095_050,
            "total_equity": 10_230_579_289_988,
        },
    )
    assert ok is True
    assert reason == "OK"
    assert row["total_equity"] == 10_230_579_289_988


def test_allows_large_cfs_parent_equity_non_controlling_interest_gap():
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {
            "stock_code": "000150",
            "report_type": "CFS",
            "total_assets": 100_000_000_000,
            "total_liabilities": 50_000_000_000,
            "total_equity": 30_000_000_000,
        },
    )
    assert ok is True
    assert reason == "OK"
    assert row["total_equity"] == 30_000_000_000


def test_allows_holding_company_nci_larger_than_parent_equity():
    # REVIEW_PLAN §45: 지주사는 비지배지분이 지배지분보다 클 수 있다(CJ 001040 2023: 자산−부채 17.89조, 지배 5.51조 — FnGuide 일치)
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {
            "stock_code": "001040",
            "report_type": "CFS",
            "total_assets": 100_000_000_000,
            "total_liabilities": 20_000_000_000,
            "total_equity": 30_000_000_000,
        },
    )
    assert ok is True
    assert reason == "OK"


def test_allows_capital_impaired_cfs_nci():
    # 자본잠식(011810 2025: 자본총계 −494억, 지배 −1,144억 — FnGuide 일치)도 비지배지분 차이는 정상
    conn = _conn()
    ok, _, reason = gate_financial_row(
        conn,
        {"stock_code": "011810", "report_type": "CFS",
         "total_assets": 300_000_000_000, "total_liabilities": 349_400_000_000, "total_equity": -114_400_000_000},
    )
    assert ok is True and reason == "OK"


def test_rejects_cfs_parent_equity_unit_error():
    # 지배자본이 단위 오류로 1/1000로 들어오면(전체 자본의 0.1%) 비지배지분으로 허용하지 않는다(상한: 지배자본 ≥ 전체 자본 5%)
    conn = _conn()
    ok, row, reason = gate_financial_row(
        conn,
        {
            "stock_code": "000150",
            "report_type": "CFS",
            "total_assets": 100_000_000_000,
            "total_liabilities": 20_000_000_000,
            "total_equity": 80_000_000,
        },
    )
    assert ok is False
    assert reason == "BS_IDENTITY_AMBIGUOUS"


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


def test_rejects_completely_empty_financial_row():
    conn = _conn()
    ok, _, reason = gate_financial_row(
        conn,
        {"stock_code": "000001", "year": 2025, "quarter": 4, "report_type": "CFS"},
    )
    assert ok is False
    assert reason == "EMPTY_FINANCIAL_ROW"
