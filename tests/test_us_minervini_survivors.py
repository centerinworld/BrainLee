from scripts.run_us_minervini_survivors import sepa_pass, trend_template
from us_backtest_common import USBar


def _bars(ticker, count, growth):
    return [USBar(ticker, f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
                  10 * growth ** i, 10 * growth ** i, 10 * growth ** i,
                  10 * growth ** i, 1000) for i in range(count)]


def test_trend_template_requires_relative_strength_and_ma_alignment():
    passed, score = trend_template(
        _bars("AAA", 260, 1.003),
        _bars("QQQ", 260, 1.0005),
        {"percentile": 95.0},
    )
    assert passed is True
    assert score > 0.95


def test_sepa_uses_only_available_quarters_and_yoy_growth():
    rows = []
    for year, eps, rev in ((2024, 1.0, 100.0), (2025, 1.4, 120.0)):
        for quarter in range(1, 5):
            rows.append({"period_end": f"{year}-{quarter * 3:02d}-28", "period_type": "quarter",
                         "revenue": rev, "operating_income": 20, "eps": eps,
                         "roe": None, "opm": 20 + quarter, "avail_date": f"{year}-{quarter * 3:02d}-29"})
    rows.append({"period_end": "2025-12-31", "period_type": "annual", "revenue": 480,
                 "operating_income": 100, "eps": 5.0, "roe": 25.0, "opm": 21,
                 "avail_date": "2026-02-01"})
    assert sepa_pass(rows, "2026-03-01") is True
    assert sepa_pass(rows, "2025-01-01") is False
