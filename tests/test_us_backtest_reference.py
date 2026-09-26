from scripts.sync_us_backtest_reference import (
    VERIFIED_TICKER_ALIASES,
    canonical_yahoo_ticker,
    derive_events,
    derive_intervals,
    parse_snapshots,
)
from us_backtest_common import membership_eligibility


def sample():
    return parse_snapshots(
        b'date,tickers\n2024-01-02,"AAA,BRK.B,OLD"\n2024-02-01,"AAA,BRK.B,NEW"\n',
        min_constituents=1,
    )


def test_parse_and_derive_half_open_intervals():
    intervals = derive_intervals(sample())
    old = next(x for x in intervals if x.ticker_raw == "OLD")
    new = next(x for x in intervals if x.ticker_raw == "NEW")
    assert (old.effective_from, old.effective_to) == ("2024-01-02", "2024-02-01")
    assert (new.effective_from, new.effective_to) == ("2024-02-01", None)


def test_share_class_ticker_is_normalized_for_yahoo_prices():
    assert canonical_yahoo_ticker("brk.b") == "BRK-B"


def test_events_do_not_mislabel_replacement_as_ticker_change():
    events = derive_events(sample())
    assert ("2024-02-01", "remove", "OLD", "OLD") in events
    assert ("2024-02-01", "add", "NEW", "NEW") in events
    assert all(x[1] in {"add", "remove"} for x in events)


def test_membership_provider_uses_exclusive_end():
    fn = membership_eligibility([
        ("OLD", "2024-01-02", "2024-02-01"),
        ("NEW", "2024-02-01", None),
    ])
    assert fn("2024-01-31") == {"OLD"}
    assert fn("2024-02-01") == {"NEW"}


def test_verified_aliases_are_ticker_only_identity_changes():
    aliases = {old: (new, day, source) for old, new, day, source in VERIFIED_TICKER_ALIASES}
    assert aliases["FB"][:2] == ("META", "2022-06-09")
    assert aliases["VIAC"][:2] == ("PARA", "2022-02-17")
    assert aliases["PKI"][:2] == ("RVTY", "2023-05-16")
    assert "ATVI" not in aliases  # acquisition: requires a terminal outcome, not an alias
    assert all(source.startswith("https://") for _, _, source in aliases.values())
