from us_market_data import aggregate_weekly_ohlcv, technical_snapshot


def test_technical_snapshot_uses_actual_high_low_and_builds_ma50():
    rows = [(x, x + 5, x - 5, x + 1) for x in range(1, 61)]
    result = technical_snapshot(rows)
    assert result.ma50 == sum(x + 1 for x in range(11, 61)) / 50
    assert result.ma60 == sum(x + 1 for x in range(1, 61)) / 60
    assert result.ma200 is None
    assert result.high_52w == 65
    assert result.low_52w == -4


def test_weekly_aggregation_labels_last_real_session_and_aggregates_ohlcv():
    rows = [
        ("2026-09-21", 10, 12, 9, 11, 100),
        ("2026-09-22", 11, 14, 10, 13, 200),
        ("2026-09-25", 13, 15, 8, 9, 300),
        ("2026-09-28", 20, 21, 19, 20, 400),
    ]
    assert aggregate_weekly_ohlcv(rows) == [
        ("2026-09-25", 10.0, 15.0, 8.0, 9.0, 600.0),
        ("2026-09-28", 20.0, 21.0, 19.0, 20.0, 400.0),
    ]
