from us_price_integrity import (
    USPricePoint, basis_whiplashes, large_price_events, overlap_basis_mismatches,
    valid_ohlc,
)


def p(day, close):
    return USPricePoint(day, close)


def test_large_events_use_asymmetric_plus_minus_30_percent_bounds():
    rows = [p("2026-01-01", 100), p("2026-01-02", 131), p("2026-01-03", 91)]
    events = large_price_events(rows)
    assert [(x["event_date"], round(x["ratio"], 2)) for x in events] == [
        ("2026-01-02", 1.31), ("2026-01-03", 0.69),
    ]


def test_basis_whiplash_flags_aph_shape_but_not_persistent_real_jump():
    aph = [p("2024-09-02", 66), p("2024-09-03", 30), p("2024-09-04", 30),
           p("2024-09-05", 61)]
    mrna = [p("2026-08-18", 63), p("2026-08-19", 174), p("2026-08-20", 133),
            p("2026-08-21", 145)]
    assert len(basis_whiplashes(aph)) == 1
    assert basis_whiplashes(mrna) == []


def test_overlap_basis_mismatch_compares_same_date_not_daily_return():
    found = overlap_basis_mismatches(
        [("2024-09-05", 61.36), ("2024-09-06", 59.37)],
        [("2024-09-05", 30.39), ("2024-09-06", 29.41)],
    )
    assert len(found) == 2
    assert all(x["ratio"] > 2 for x in found)


def test_incomplete_intraday_ohlc_is_rejected_but_epsilon_is_allowed():
    assert valid_ohlc(74.85, 74.82, 73.95, 74.17) is False
    assert valid_ohlc(10.0, 10.999999999999998, 9.0, 11.0) is True
