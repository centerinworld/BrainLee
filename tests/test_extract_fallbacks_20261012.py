"""원문 추출 보강 회귀 테스트(2026-10-12) — 합성 원문 행으로 고정. 실제 사례: 238090·008040·013000·085810·032680·311390."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from financial_rereview_20261002 import extract  # noqa: E402


def _r(sj, aid, nm, v):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": v}


def test_total_assets_from_equity_and_liabilities_when_blank():
    rows = [_r("BS", "ifrs-full_Assets", "자산총계", ""), _r("BS", "ifrs-full_Liabilities", "부채총계", "21783188317"),
            _r("BS", "ifrs-full_Equity", "자본총계", "153644360594"), _r("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", "175427548911")]
    assert extract(rows)["total_assets"] == 175427548911            # 238090 2021 반기


def test_zero_assets_and_liabilities_recovered():
    rows = [_r("BS", "ifrs-full_Assets", "자산총계", "0"), _r("BS", "ifrs-full_Liabilities", "부채총계", "0"),
            _r("BS", "ifrs-full_Equity", "자본총계", "111932093545"), _r("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", "470511381854")]
    e = extract(rows)
    assert e["total_assets"] == 470511381854 and e["total_liabilities"] == 358579288309   # 008040 2026 반기


def test_parent_equity_from_total_minus_nci():
    rows = [_r("BS", "ifrs-full_Equity", "자본총계", "48451992720"), _r("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", "31572278")]
    assert extract(rows)["equity_parent"] == 48420420442              # 013000 2023


def test_revenue_name_variant():
    rows = [_r("CIS", "ifrs-full_GrossProfit", "영업수익(매출액)", "5772707362")]
    assert extract(rows)["revenue"] == 5772707362                     # 085810 2023


def test_unit_multiple_detection():
    import importlib.util
    s = importlib.util.spec_from_file_location("fu", ROOT / "scripts" / "review" / "fix_restated_unit_error_20261012.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    assert m._mult(1.5616639515e16, 15616639515) == 1e6               # 032680 2021 재작성 칸
    assert m._mult(1368718533000, 1368718533) == 1e3                  # 311390 2021
    assert m._mult(1.05e9, 1.0e9) is None                             # 5% 차이는 단위 오기 아님
