"""fin_norm(재무 정규화 공통 함수) 회귀 테스트 — 2026-10-09~10 확인 사례를 고정 (정의를 바꾸면 여기와 FINANCIAL_STATEMENTS.md를 함께 바꿀 것)."""
import re
from pathlib import Path

import pytest

import fin_norm as fn

ROOT = Path(__file__).resolve().parents[1]
HAS_RAW = (fn.RAW_DART / "008930").exists()


def test_q4_and_three_month_formulas():
    assert fn.q4_flow(1356957532846, 1012931165519) == 344026367327   # 008930 2025 매출 Q4(D-6)
    assert fn.q4_flow(None, 1) is None and fn.q4_flow(1, None) is None   # 파생 안 함
    assert fn.q4_bs(5) == 5
    assert fn.cf_three_month(100, None, 1) == 100
    assert fn.cf_three_month(300, 100, 2) == 200
    assert fn.cf_three_month(300, None, 3) is None


def test_dart_key_parent_basis():
    assert fn.dart_key("net_income", "CFS") == "ni_parent" and fn.dart_key("net_income", "OFS") == "ni_total"
    assert fn.dart_key("total_equity", "CFS") == "equity_parent" and fn.dart_key("total_equity", "OFS") == "equity_total"
    assert fn.dart_key("operating_cf", "CFS") == "ocf"


def test_pick_fs_never_substitutes():
    assert fn.pick_fs({"OFS": 1}, "CFS") is None
    assert fn.pick_fs({"CFS": 2, "OFS": 1}, "OFS") == 1


def test_fiscal_key_december_and_nondec():
    assert fn.fiscal_key_from_period_end("005930", "202603", False) == (2026, 1)
    assert fn.fiscal_key_from_period_end("005930", "202512", True) == (2025, 0)
    # 084440 9월 결산: 2025-12 = 2026 회계연도 1분기, 2025-09 = 2025 연간·4분기
    assert fn.fiscal_key_from_period_end("084440", "202512", False) == (2026, 1)
    assert fn.fiscal_key_from_period_end("084440", "202509", True) == (2025, 0)


@pytest.mark.skipif(not HAS_RAW, reason="DART 원문 폴더 없음")
def test_raw_and_ytd3_008930():
    assert fn.raw("008930", 2025, 3, "CFS", "revenue") == 342511980369          # 3분기 3개월(원본 = 공시)
    assert fn.ytd3("008930", 2025, "CFS", "revenue") == 1012931165519            # 3분기 누적
    a, src = fn.latest_annual("008930", 2025, "CFS", "revenue")
    assert a == 1356957532846


def test_no_local_copies_of_central_definitions():
    """정의 중복 금지: 아래 스크립트는 4분기·누적 차분·회계 키·원문 읽기를 fin_norm에서 가져와야 한다."""
    files = ["scripts/review/unify_q4_ytd3_20261009.py", "scripts/review/apply_restated_quarterly_20261010.py",
             "scripts/review/apply_restated_annual_20261004.py", "scripts/review/apply_ofs_extra_20261009.py",
             "scripts/review/apply_dart_refetch_20261002.py", "scripts/review/fetch_naver_finance_api_20261010.py",
             "scripts/review/judge_fnguide_quarter_inconsistent_20261009.py"]
    pat = re.compile(r"^def (ytd3_raw|fiscal_key|latest_cum|dkey|raw)\(", re.M)
    bad = [f"{f}: {m.group(1)}" for f in files for m in pat.finditer((ROOT / f).read_text())]
    assert not bad, "중앙 정의를 스크립트가 다시 정의함(fin_norm 사용):\n" + "\n".join(bad)


def test_dart_key_for_fiscal_inverse():
    assert fn.dart_key_for_fiscal("005930", 2026, 1) == (2026, 1)          # 12월 결산 = 그대로
    assert fn.dart_key_for_fiscal("084440", 2026, 1) == (2025, 1)          # 9월 결산: 회계 2026 1분기 = 2025-12
    assert fn.dart_key_for_fiscal("084440", 2025, 0) == (2025, 0)
    for code in ("084440", "005930"):
        for fy in (2025, 2026):
            for q in (1, 2, 3):
                by, bq = fn.dart_key_for_fiscal(code, fy, q)
                assert fn.to_fiscal(code, by, bq) == (fy, q)               # to_fiscal의 정확한 역


@pytest.mark.skipif(not (fn.RAW_DART / "052770").exists(), reason="원문 없음")
def test_loss_label_positive_amount_is_negative():
    v = fn.raw("052770", 2025, 3, "CFS", "operating_profit")
    assert v is not None and v < 0          # 원문 '영업손실' 양수 → 손실(FnGuide −13.24억)
