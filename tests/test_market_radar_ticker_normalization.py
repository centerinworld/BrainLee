from routes.market_radar import _is_korean_ticker, _norm_ticker


def test_taiwan_ticker_aliases_use_correct_yahoo_exchange() -> None:
    assert _norm_ticker("6236.TW") == "6257.TW"
    assert _norm_ticker("3529.TW") == "3529.TWO"
    assert _norm_ticker("6488.TW") == "6488.TWO"
    assert _norm_ticker("8299.TW") == "8299.TWO"


def test_non_market_and_delisted_tickers_are_not_requested() -> None:
    for ticker in ("비상장", "UNLISTED", "EA", "9613.T"):
        assert _norm_ticker(ticker) == ""


def test_korean_tickers_are_detected_before_foreign_refresh() -> None:
    assert _is_korean_ticker(_norm_ticker("005930"))
    assert not _is_korean_ticker(_norm_ticker("6257.TW"))
