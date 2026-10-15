"""회귀 테스트: fix_p0_unit_errors_20261015 수정 결과 검증.

검증 항목:
1. 5개 수정 행의 원장 값과 정본 값 일치
2. 102260 OFS 예외 3건(NO_OFS_QUARTERLY_RAW) 등록 확인
3. DART_CIS_UNIT_ERROR / RAW_CIS_IS_DIVERGE 예외 등록 확인
4. canonical_financial_data에 032080 2018 Q3/Q4 OFS 행 존재
5. 1e15 규모 이상 값이 수정 대상 행에 없음
6. Q4 계산이 Annual_raw - YTD3(Q3_db) 방식임을 확인 (fn.q4_flow 표준)
7. 102260 OFS 행(id=188978) 직접 접근 시 오류 값 그대로임을 인지 (예외 격리)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

import pytest
from db_compat import connect_primary_db
import fin_norm as fn


@pytest.fixture(scope="module")
def conn():
    c = connect_primary_db(timeout=30, readonly=True)
    yield c


# ── 1. 원장 수정값 검증 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("fd_id,col,want", [
    # 032080 2018 Q3 OFS
    (167328, "operating_profit", 1_683_830_850.0),
    (167328, "net_income",       1_716_915_608.0),
    # 032080 2018 Q4 OFS — Annual_raw(2046477455) - YTD3_db(1683830850)
    (669032, "operating_profit", 362_646_605.0),
    (669032, "net_income",       2_208_397_708.0),
    # 032680 2022 Q0 OFS
    (132557, "revenue",          32_431_035_679.0),
    (132557, "operating_profit", 12_630_548_130.0),
    (132557, "net_income",       10_395_270_779.0),
    # 032680 2022 Q4 OFS — Annual_corrected - YTD3_raw (YTD3=Q1_3m+Q2_3m+Q3_3m=17,057,630,723; Q3_db is 3-month)
    (669402, "revenue",          15_373_404_956.0),
    (669402, "operating_profit", 10_314_667_847.0),
    (669402, "net_income",        8_147_105_941.0),
    # 102260 2016 Q2 CFS
    (65987, "revenue",           213_371_655_426.0),
    (65987, "operating_profit",   21_557_470_653.0),
    (65987, "net_income",         18_047_546_402.0),
])
def test_fd_value(conn, fd_id, col, want):
    row = conn.execute(f"SELECT {col} FROM financial_data WHERE id=%s", (fd_id,)).fetchone()
    assert row is not None, f"id={fd_id} 없음"
    assert abs(float(row[0]) - want) <= 1.0, f"id={fd_id} {col}: got={row[0]}, want={want}"


# ── 2. 정본(canonical) 동기화 검증 ───────────────────────────────────────────

@pytest.mark.parametrize("code,yr,q,rt,col,want", [
    # 032080 Q3 OFS (새로 INSERT됨)
    ("032080", 2018, 3, "OFS", "operating_profit", 1_683_830_850.0),
    ("032080", 2018, 3, "OFS", "net_income",       1_716_915_608.0),
    # 032080 Q4 OFS (새로 INSERT됨)
    ("032080", 2018, 4, "OFS", "operating_profit", 362_646_605.0),
    ("032080", 2018, 4, "OFS", "net_income",       2_208_397_708.0),
    # 032680 Q0 OFS
    ("032680", 2022, 0, "OFS", "revenue",          32_431_035_679.0),
    ("032680", 2022, 0, "OFS", "operating_profit", 12_630_548_130.0),
    # 032680 Q4 OFS
    ("032680", 2022, 4, "OFS", "revenue",          15_373_404_956.0),
    ("032680", 2022, 4, "OFS", "operating_profit", 10_314_667_847.0),
    # 102260 Q2 CFS
    ("102260", 2016, 2, "CFS", "revenue",          213_371_655_426.0),
    ("102260", 2016, 2, "CFS", "net_income",       18_047_546_402.0),
])
def test_canonical_value(conn, code, yr, q, rt, col, want):
    ia = 1 if q == 0 else 0
    row = conn.execute(
        f"SELECT {col} FROM canonical_financial_data "
        "WHERE stock_code=%s AND year=%s AND quarter=%s AND is_annual=%s AND report_type=%s",
        (code, yr, q, ia, rt)
    ).fetchone()
    assert row is not None, f"canonical 없음: {code} {yr} Q{q} {rt}"
    assert abs(float(row[0]) - want) <= 1.0, f"canonical {code} Q{q} {rt} {col}: got={row[0]}, want={want}"


# ── 3. 예외 등록 확인 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,yr,q,rt,fld,rc", [
    ("102260", 2016, 2, "OFS", "revenue",          "NO_OFS_QUARTERLY_RAW"),
    ("102260", 2016, 2, "OFS", "operating_profit", "NO_OFS_QUARTERLY_RAW"),
    ("102260", 2016, 2, "OFS", "net_income",        "NO_OFS_QUARTERLY_RAW"),
    ("032680", 2022, 0, "OFS", "revenue",           "DART_CIS_UNIT_ERROR"),
    ("102260", 2016, 2, "CFS", "net_income",         "RAW_CIS_IS_DIVERGE"),
])
def test_exception_registered(conn, code, yr, q, rt, fld, rc):
    row = conn.execute(
        "SELECT reason_code FROM financial_field_exception "
        "WHERE stock_code=%s AND year=%s AND quarter=%s AND report_type=%s AND field=%s",
        (code, yr, q, rt, fld)
    ).fetchone()
    assert row is not None, f"예외 미등록: {code} {yr} Q{q} {rt} {fld}"
    assert row[0] == rc, f"예외 reason_code 불일치: got={row[0]}, want={rc}"


# ── 4. 1e15 규모 이상 값 없음 (수정 대상 행) ─────────────────────────────────

@pytest.mark.parametrize("fd_id", [167328, 669032, 132557, 669402, 65987])
def test_no_1e15_scale(conn, fd_id):
    row = conn.execute(
        "SELECT revenue, operating_profit, net_income FROM financial_data WHERE id=%s",
        (fd_id,)
    ).fetchone()
    assert row is not None, f"id={fd_id} 없음"
    for col_name, val in zip(("revenue", "operating_profit", "net_income"), row):
        if val is not None:
            assert abs(val) < 1e14, f"id={fd_id} {col_name}={val} — 1e14 초과 (단위오류 의심)"


# ── 5. Q4 계산이 Annual - YTD3(Q3_db) 방식임을 확인 ─────────────────────────

def test_032080_q4_formula(conn):
    """Q4 = Annual_raw - YTD3(=Q3_db). Annual-YTD1-YTD2-YTD3 방식이면 실패."""
    q3 = conn.execute(
        "SELECT operating_profit, net_income FROM financial_data "
        "WHERE stock_code='032080' AND year=2018 AND quarter=3 AND report_type='OFS' AND is_annual=0"
    ).fetchone()
    q4 = conn.execute(
        "SELECT operating_profit, net_income FROM financial_data WHERE id=669032"
    ).fetchone()
    annual_raw_op = 2_046_477_455.0
    annual_raw_ni = 3_925_313_316.0
    assert abs(float(q4[0]) - (annual_raw_op - float(q3[0]))) <= 1.0, "Q4 op != Annual_raw - YTD3"
    assert abs(float(q4[1]) - (annual_raw_ni - float(q3[1]))) <= 1.0, "Q4 ni != Annual_raw - YTD3"


def test_032680_q4_formula(conn):
    """Q4 = Annual_corrected - YTD3_raw.
    032680 Q1/Q2/Q3 DB값은 3-month; YTD3 = Q1+Q2+Q3 합 = fn.ytd3 = 17057630723 (revenue).
    """
    import fin_norm as fn
    q4 = conn.execute(
        "SELECT revenue, operating_profit, net_income FROM financial_data WHERE id=669402"
    ).fetchone()
    annual = conn.execute(
        "SELECT revenue, operating_profit, net_income FROM financial_data WHERE id=132557"
    ).fetchone()
    rfs = fn.effective_fs("032680", 2022, "OFS")
    for i, col in enumerate(("revenue", "operating_profit", "net_income")):
        y3 = fn.ytd3("032680", 2022, rfs, col)
        assert y3 is not None, f"fn.ytd3 없음: 032680 2022 {col}"
        expected = float(annual[i]) - y3
        assert abs(float(q4[i]) - expected) <= 1.0, f"032680 Q4 {col} != Annual - YTD3: {q4[i]} vs {expected}"


# ── 6. 102260 OFS 오류 행 인지 (예외 격리 확인) ───────────────────────────────

def test_102260_ofs_row_has_exception(conn):
    """102260 2016 Q2 OFS rev/op/ni 예외가 모두 등록돼 있어 내부검사 분모에서 제외됨."""
    for fld in ("revenue", "operating_profit", "net_income"):
        row = conn.execute(
            "SELECT reason_code FROM financial_field_exception "
            "WHERE stock_code='102260' AND year=2016 AND quarter=2 AND report_type='OFS' AND field=%s",
            (fld,)
        ).fetchone()
        assert row is not None, f"102260 OFS Q2 {fld} 예외 미등록"
        assert row[0] == "NO_OFS_QUARTERLY_RAW"


def test_102260_ofs_row_not_in_canonical(conn):
    """102260 2016 Q2 OFS는 canonical_financial_data에 없어야 함 (오류 행 격리)."""
    row = conn.execute(
        "SELECT id FROM canonical_financial_data "
        "WHERE stock_code='102260' AND year=2016 AND quarter=2 AND report_type='OFS' AND is_annual=0"
    ).fetchone()
    assert row is None, f"102260 2016 Q2 OFS가 canonical에 존재: id={row[0] if row else None}"
