"""
Failing tests (RED) for the income-statement 당분기(standalone) derivation pipeline.

Contract under test: `scripts/is_standalone_pipeline.py` (NOT YET IMPLEMENTED).
Spec: `docs/codex_handoff_is_standalone_impl_spec_20260920.md`.

These tests assert the pure-function contract of `derive_standalone` and the
storage-separation contract of `record_standalone` (raw/computed/evidence split).
They must fail until the pipeline module exists.

Run:
  python3 -m pytest tests/test_is_standalone_pipeline.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# RED: import fails until scripts/is_standalone_pipeline.py is implemented.
from scripts.is_standalone_pipeline import derive_standalone, record_standalone, Derivation  # noqa: E402,F401


# --- fixtures (mirror 삼성전자 Q2 반기보고서 구조) --------------------------

def _te(acode, acontext, adecimal, value):
    return f'<TE ACODE="{acode}" ACONTEXT="{acontext}" ADECIMAL="{adecimal}"><P>{value}</P></TE>'


C = "ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_ifrs-full_ConsolidatedMember"
S = "ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_ifrs-full_SeparateMember"

Q2_XML = "".join([
    _te("ifrs-full_Revenue", f"CFY2025dHYQ_{C}", "-6", "22,231,952"),   # 당분기 연결
    _te("ifrs-full_Revenue", f"CFY2025dHYA_{C}", "-6", "39,871,093"),   # 누적 연결
    _te("ifrs-full_Revenue", f"PFY2024dHYQ_{C}", "-6", "16,423,258"),   # 전기(비교)
    _te("ifrs-full_Revenue", f"CFY2025dHYQ_{C}_ifrs-full_SegmentsAxis_x_DramMember", "-6", "17,123,990"),  # 세그먼트 제외
    _te("ifrs-full_Revenue", f"CFY2025dHYQ_{S}", "-6", "19,549,466"),   # 별도 → CFS 제외
    _te("dart_OperatingIncomeLoss", f"CFY2025dHYQ_{C}", "-6", "3,114,000"),  # 영업이익(dart 확장)
    _te("ifrs-full_ProfitLoss", f"CFY2025dHYQ_{C}", "-6", "4,120,003"),  # 순이익 당분기
    _te("ifrs-full_ProfitLoss", f"CFY2025dHYA_{C}", "-6", "6,037,042"),  # 순이익 누적
])


# --- tests ------------------------------------------------------------------

def test_direct_extraction_is_primary():
    """당분기 fact(직접 태깅)가 1순위로 추출되고, 계산값=원문이다."""
    d = derive_standalone(Q2_XML, "revenue", 2025, 2)
    assert d.mode == "DIRECT"
    assert d.raw_value == 22231952000000.0          # 22,231,952 × 10^6
    assert d.computed_value == d.raw_value
    assert d.acode == "ifrs-full_Revenue"
    assert d.adecimal == -6
    assert d.consolidated is True
    assert d.period_token == "HY" and d.suffix == "Q"


def test_operating_profit_dart_extension_mapping():
    """운영이익은 dart_OperatingIncomeLoss 확장코드를 1순위로 매핑해야 한다."""
    d = derive_standalone(Q2_XML, "operating_profit", 2025, 2)
    assert d.mode == "DIRECT"
    assert d.computed_value == 3114000000000.0
    assert d.acode == "dart_OperatingIncomeLoss"


def test_net_income_direct():
    d = derive_standalone(Q2_XML, "net_income", 2025, 2)
    assert d.mode == "DIRECT"
    assert d.computed_value == 4120003000000.0


def test_separate_member_not_written_as_cfs():
    """별도(SeparateMember) fact는 CFS 기입 대상이 아니다 — 총액 연결값만 채택."""
    d = derive_standalone(Q2_XML, "revenue", 2025, 2)
    # 별도값(19,549,466 × 10^6)이 아니라 연결 총액값이어야 함
    assert d.consolidated is True
    assert d.raw_value == 22231952000000.0


def test_segment_and_prior_year_excluded():
    """세그먼트/전기(PFY) fact가 총액에 섞이지 않아야 한다."""
    xml = "".join([
        _te("ifrs-full_Revenue", f"CFY2025dHYQ_{C}", "-6", "22,231,952"),
        _te("ifrs-full_Revenue", f"PFY2024dHYQ_{C}", "-6", "99,999,999"),  # 전기 오염 시도
        _te("ifrs-full_Revenue", f"CFY2025dHYQ_{C}_ifrs-full_SegmentsAxis_x_DramMember", "-6", "99,999,999"),
    ])
    d = derive_standalone(xml, "revenue", 2025, 2)
    assert d.computed_value == 22231952000000.0


def test_subtract_fallback_when_no_direct_fact():
    """당분기 fact 부재 시 누적 차감 폴백: 당분기 = 누적(Q2) − 직전 누적(Q1)."""
    cur = _te("ifrs-full_Revenue", f"CFY2025dHYA_{C}", "-6", "39,871,093")  # 누적 H1
    prior_q1_cum = 17750000000000.0                                          # 누적 Q1
    d = derive_standalone(cur, "revenue", 2025, 2, prior_cumulative=prior_q1_cum)
    assert d.mode == "SUBTRACT"
    assert d.raw_value == 39871093000000.0
    assert d.prior_raw_value == prior_q1_cum
    assert d.computed_value == 39871093000000.0 - prior_q1_cum
    assert d.suffix == "A"


def test_missing_prior_cumulative_is_skip():
    """차감 폴백에서 직전 누적 결측 → 산출 거부(SKIP), OPEN 유지."""
    cur = _te("ifrs-full_Revenue", f"CFY2025dHYA_{C}", "-6", "39,871,093")
    d = derive_standalone(cur, "revenue", 2025, 2, prior_cumulative=None)
    assert d.mode == "SKIP"
    assert d.skip_reason == "missing_prior_cumulative"
    assert d.computed_value is None


def test_q1_cumulative_equals_standalone():
    """Q1(최초분기)은 누적=당분기 → 차감 없이 직접 기입."""
    xml = _te("ifrs-full_Revenue", f"CFY2025dFQA_{C}", "-6", "22,231,952")
    d = derive_standalone(xml, "revenue", 2025, 1, prior_cumulative=None)
    assert d.mode == "DIRECT"
    assert d.computed_value == 22231952000000.0


def test_negative_revenue_skip():
    """revenue 음수는 오태깅 → SKIP."""
    xml = _te("ifrs-full_Revenue", f"CFY2025dHYQ_{C}", "-6", "(500,000)")
    d = derive_standalone(xml, "revenue", 2025, 2)
    assert d.mode == "SKIP"
    assert d.skip_reason == "negative_revenue"


def test_negative_net_income_kept():
    """net_income 음수는 유효한 손실 → 기입."""
    xml = _te("ifrs-full_ProfitLoss", f"CFY2025dHYQ_{C}", "-6", "(1,000,000)")
    d = derive_standalone(xml, "net_income", 2025, 2)
    assert d.mode == "DIRECT"
    assert d.computed_value == -1000000000000.0


def test_addecimal_scaling():
    xml = _te("ifrs-full_ProfitLoss", f"CFY2025dHYQ_{C}", "-3", "1,234")
    assert derive_standalone(xml, "net_income", 2025, 2).computed_value == 1234000.0
    xml0 = _te("ifrs-full_ProfitLoss", f"CFY2025dHYQ_{C}", "0", "7")
    assert derive_standalone(xml0, "net_income", 2025, 2).computed_value == 7.0


# --- storage-separation contract (원문/계산값/근거 3계층 분리) -------------

def _create_integrity_tables(conn):
    """Create the integrity tables the pipeline writes (in-memory test)."""
    conn.execute("""CREATE TABLE financial_fix_log (
        id INTEGER PRIMARY KEY, fixed_at TEXT, row_id INTEGER, stock_code TEXT,
        year INTEGER, quarter INTEGER, is_annual INTEGER, report_type TEXT,
        field_name TEXT, old_value REAL, new_value REAL, fix_rule TEXT,
        source TEXT, run_id TEXT)""")
    conn.execute("""CREATE TABLE fin_quarterly_validation_flags (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        field TEXT, check_type TEXT, dart_value REAL, status TEXT,
        source_count INTEGER, notes TEXT, updated_at TEXT)""")


def test_record_standalone_writes_computed_value_only_to_financial_data():
    """financial_data에는 계산값만 기록되고, 원문/근거는 별도 테이블에 분리된다.

    REFACTOR 통합: 실제 기입 시 financial_fix_log(감사)와
    fin_quarterly_validation_flags(source_count 0→1)도 함께 기록한다.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE financial_data (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        is_annual INTEGER, report_type TEXT, revenue REAL, operating_profit REAL,
        net_income REAL)""")
    conn.execute("""CREATE TABLE is_standalone_derivation (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        field TEXT, mode TEXT, raw_value REAL, prior_raw_value REAL, computed_value REAL,
        acode TEXT, adecimal INTEGER, consolidated INTEGER, period_token TEXT,
        suffix TEXT, skip_reason TEXT, rcept_no TEXT, run_id TEXT, created_at TEXT)""")
    _create_integrity_tables(conn)
    conn.execute("""INSERT INTO financial_data
        (id, stock_code, year, quarter, is_annual, report_type, revenue)
        VALUES (1, '005930', 2025, 2, 0, 'CFS', NULL)""")
    conn.execute("""INSERT INTO fin_quarterly_validation_flags
        (id, stock_code, year, quarter, field, check_type, dart_value, status, source_count)
        VALUES (1, '005930', 2025, 2, 'revenue', 'QUARTERLY_4WAY', NULL, 'OPEN', 0)""")

    d = derive_standalone(Q2_XML, "revenue", 2025, 2)
    assert d.mode == "DIRECT"
    ok = record_standalone(conn, "005930", 2025, 2, "revenue", d, "rcept1", "run_test")
    assert ok is True

    row = conn.execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()
    assert row[0] == 22231952000000.0  # 계산값만 기입

    ev = conn.execute(
        "SELECT mode, raw_value, computed_value, acode, consolidated FROM "
        "is_standalone_derivation WHERE stock_code='005930'").fetchone()
    assert ev[0] == "DIRECT"
    assert ev[1] == 22231952000000.0   # 원문
    assert ev[2] == 22231952000000.0   # 계산값 (분리 저장 확인)
    assert ev[3] == "ifrs-full_Revenue"
    assert ev[4] == 1

    # financial_fix_log: 감사 기록 (old=NULL → new=계산값)
    fx = conn.execute(
        "SELECT row_id, field_name, old_value, new_value, fix_rule, source "
        "FROM financial_fix_log").fetchall()
    assert len(fx) == 1
    assert fx[0][0] == 1                     # row_id → financial_data.id
    assert fx[0][1] == "revenue"
    assert fx[0][2] is None                  # old_value = NULL
    assert fx[0][3] == 22231952000000.0      # new_value = 계산값
    assert fx[0][4] == "DART_STANDALONE_IS_DERIVATION"
    assert "rcept_no=rcept1" in fx[0][5]

    # flag: source_count 0→1, dart_value 기록, status는 OPEN 유지
    fg = conn.execute(
        "SELECT dart_value, source_count, status FROM fin_quarterly_validation_flags "
        "WHERE id=1").fetchone()
    assert fg[0] == 22231952000000.0
    assert fg[1] == 1
    assert fg[2] == "OPEN"


def test_record_standalone_is_idempotent_on_rerun():
    """같은 run_id 재실행 시 fix_log/flag/provenance가 중복 기입되지 않는다."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE financial_data (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        is_annual INTEGER, report_type TEXT, revenue REAL, operating_profit REAL,
        net_income REAL)""")
    conn.execute("""CREATE TABLE is_standalone_derivation (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        field TEXT, mode TEXT, raw_value REAL, prior_raw_value REAL, computed_value REAL,
        acode TEXT, adecimal INTEGER, consolidated INTEGER, period_token TEXT,
        suffix TEXT, skip_reason TEXT, rcept_no TEXT, run_id TEXT, created_at TEXT)""")
    _create_integrity_tables(conn)
    conn.execute("""INSERT INTO financial_data
        (id, stock_code, year, quarter, is_annual, report_type, revenue)
        VALUES (1, '005930', 2025, 2, 0, 'CFS', NULL)""")
    conn.execute("""INSERT INTO fin_quarterly_validation_flags
        (id, stock_code, year, quarter, field, check_type, status, source_count)
        VALUES (1, '005930', 2025, 2, 'revenue', 'QUARTERLY_4WAY', 'OPEN', 0)""")

    d = derive_standalone(Q2_XML, "revenue", 2025, 2)
    assert record_standalone(conn, "005930", 2025, 2, "revenue", d, "rcept1", "run_test") is True
    # 재실행: 같은 run_id → 모든 write가 no-op
    assert record_standalone(conn, "005930", 2025, 2, "revenue", d, "rcept1", "run_test") is False

    assert conn.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM is_standalone_derivation").fetchone()[0] == 1
    fg = conn.execute(
        "SELECT source_count FROM fin_quarterly_validation_flags WHERE id=1").fetchone()
    assert fg[0] == 1  # 0→1 한 번만


def test_record_standalone_does_not_overwrite_confirmed():
    """이미 값이 있는(확정) 행은 덮어쓰지 않고 fix_log/flag도 건드리지 않는다."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE financial_data (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        is_annual INTEGER, report_type TEXT, revenue REAL, operating_profit REAL,
        net_income REAL)""")
    conn.execute("""CREATE TABLE is_standalone_derivation (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        field TEXT, mode TEXT, raw_value REAL, prior_raw_value REAL, computed_value REAL,
        acode TEXT, adecimal INTEGER, consolidated INTEGER, period_token TEXT,
        suffix TEXT, skip_reason TEXT, rcept_no TEXT, run_id TEXT, created_at TEXT)""")
    _create_integrity_tables(conn)
    conn.execute("""INSERT INTO financial_data
        (id, stock_code, year, quarter, is_annual, report_type, revenue)
        VALUES (1, '005930', 2025, 2, 0, 'CFS', 9999999999999.0)""")

    d = derive_standalone(Q2_XML, "revenue", 2025, 2)
    ok = record_standalone(conn, "005930", 2025, 2, "revenue", d, "rcept1", "run_test")
    assert ok is False  # 값이 이미 있어 기입하지 않음

    row = conn.execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()
    assert row[0] == 9999999999999.0  # 미변경
    assert conn.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 0


def test_record_standalone_skip_does_not_touch_financial_data():
    """SKIP은 financial_data를 변경하지 않고, skip_reason만 증적에 남긴다."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE financial_data (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        is_annual INTEGER, report_type TEXT, revenue REAL)""")
    conn.execute("""CREATE TABLE is_standalone_derivation (
        id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,
        field TEXT, mode TEXT, raw_value REAL, prior_raw_value REAL, computed_value REAL,
        acode TEXT, adecimal INTEGER, consolidated INTEGER, period_token TEXT,
        suffix TEXT, skip_reason TEXT, rcept_no TEXT, run_id TEXT, created_at TEXT)""")
    _create_integrity_tables(conn)
    conn.execute("""INSERT INTO financial_data
        (id, stock_code, year, quarter, is_annual, report_type, revenue)
        VALUES (1, '005930', 2025, 2, 0, 'CFS', NULL)""")

    xml = _te("ifrs-full_Revenue", f"CFY2025dHYA_{C}", "-6", "39,871,093")
    d = derive_standalone(xml, "revenue", 2025, 2, prior_cumulative=None)
    assert d.mode == "SKIP"
    ok = record_standalone(conn, "005930", 2025, 2, "revenue", d, "rcept1", "run_test")
    assert ok is False

    row = conn.execute("SELECT revenue FROM financial_data WHERE id=1").fetchone()
    assert row[0] is None  # 미변경

    ev = conn.execute(
        "SELECT mode, skip_reason FROM is_standalone_derivation WHERE stock_code='005930'").fetchone()
    assert ev[0] == "SKIP"
    assert ev[1] == "missing_prior_cumulative"

    assert conn.execute("SELECT COUNT(*) FROM financial_fix_log").fetchone()[0] == 0


if __name__ == "__main__":
    import traceback
    mod = sys.modules[__name__]
    tests = [(k, v) for k, v in sorted(vars(mod).items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
