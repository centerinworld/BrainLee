"""네이버 컨센서스 예측치(naver_finance_api is_estimate)는 실적·검증률·백테스트 입력에 쓰지 않는다(2026-10-10)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_backtest_does_not_read_forecast_table():
    hits = [str(p.relative_to(ROOT)) for d in ("backtest_strategies",) for p in (ROOT / d).rglob("*.py") if "naver_finance_api" in p.read_text(errors="ignore")]
    hits += [f for f in ("backtest_common.py", "backtest.py") if (ROOT / f).exists() and "naver_finance_api" in (ROOT / f).read_text(errors="ignore")]
    assert not hits, f"백테스트가 예측치 표를 읽음: {hits}"


def test_verification_uses_actuals_only():
    src = (ROOT / "scripts/review/build_field_verification_20261003.py").read_text()
    assert "FROM naver_finance_api WHERE NOT is_estimate" in src
