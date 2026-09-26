import pytest

from scripts.backfill_us_delisted_prices import normalize_rows


def test_tiingo_adjusted_fields_are_mapped_to_engine_price_contract():
    rows = normalize_rows("old", [{
        "date": "2022-01-03T00:00:00.000Z", "adjOpen": 10, "adjHigh": 12,
        "adjLow": 9, "adjClose": 11, "adjVolume": 1234,
    }])
    assert rows == [("OLD", "2022-01-03", 11.0, 1234.0, 10.0, 12.0, 9.0)]


def test_tiingo_invalid_or_empty_payload_fails_closed():
    with pytest.raises(ValueError):
        normalize_rows("OLD", [])
    with pytest.raises(ValueError):
        normalize_rows("OLD", [{
            "date": "2022-01-03", "adjOpen": 10, "adjHigh": 8,
            "adjLow": 9, "adjClose": 11, "adjVolume": 1,
        }])
