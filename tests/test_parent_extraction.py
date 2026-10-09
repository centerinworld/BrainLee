import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from financial_rereview_20261002 import _parent_by_name, extract


def test_parent_by_name_standard_order():
    rows = [
        {"sj_div": "CIS", "account_nm": "당기순이익", "thstrm_amount": "1000"},
        {"sj_div": "CIS", "account_nm": "지배기업 소유주지분 순이익", "thstrm_amount": "800"},
        {"sj_div": "CIS", "account_nm": "비지배지분 순이익", "thstrm_amount": "200"},
    ]
    val = _parent_by_name(rows, "ni", 1000.0)
    assert val == 800.0


def test_parent_by_name_nci_before_parent():
    """000300 실사례: 비지배지분순이익이 지배기업소유주지분순익보다 앞에 나오는 경우."""
    rows = [
        {"sj_div": "CIS", "account_nm": "비지배지분순이익（손실）", "thstrm_amount": "1590803544"},
        {"sj_div": "CIS", "account_nm": "기타자본변동", "thstrm_amount": "-1081340"},
        {"sj_div": "CIS", "account_nm": "지배기업소유주지분순익", "thstrm_amount": "4164368888"},
    ]
    val = _parent_by_name(rows, "ni", 5755172432.0)
    assert val == 4164368888.0


def test_parent_by_name_large_distance_in_bs():
    """003720 실사례: 재무상태표에서 지배지분과 비지배지분 사이 간격이 5행 이상인 경우."""
    rows = [
        {"sj_div": "BS", "account_nm": "지배기업의 소유지분", "thstrm_amount": "36205403979"},
        {"sj_div": "BS", "account_nm": "기타자본구성요소", "thstrm_amount": "97944149850"},
        {"sj_div": "BS", "account_nm": "자본금", "thstrm_amount": "20718527500"},
        {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "32101054220"},
        {"sj_div": "BS", "account_nm": "비지배지분", "thstrm_amount": "-4104349759"},
    ]
    val = _parent_by_name(rows, "eq", 32101054220.0)
    assert val == 36205403979.0


def test_parent_by_name_excludes_comprehensive():
    """포괄손익 지배귀속은 제외되는지 확인."""
    rows = [
        {"sj_div": "CIS", "account_nm": "포괄손익, 지배기업의 소유주에게 귀속되는 지분", "thstrm_amount": "500"},
        {"sj_div": "CIS", "account_nm": "포괄손익, 비지배지분", "thstrm_amount": "100"},
    ]
    val = _parent_by_name(rows, "ni", 600.0)
    assert val is None
