"""Shared plausibility gates for market and macro price ingestion."""

from __future__ import annotations

from typing import Any, Iterable


PLAUSIBLE_CLOSE_RANGES: dict[str, tuple[float, float]] = {
    "^VIX": (5.0, 150.0),
    "2YY=F": (0.1, 20.0),
    "^TNX": (0.1, 20.0),
    "10Y=F": (0.1, 20.0),
    "^TYX": (0.1, 20.0),
    "DX-Y.NYB": (50.0, 200.0),
    "USDKRW=X": (500.0, 3000.0),
    "JPYKRW=X": (1.0, 30.0),
    "TWDKRW=X": (10.0, 100.0),
    "EURKRW=X": (500.0, 3000.0),
    "HKDKRW=X": (50.0, 500.0),
    "GC=F": (100.0, 10000.0),
    "CL=F": (5.0, 300.0),
}

RETIRED_MACRO_SYMBOLS = frozenset({"^UST2Y", "30Y=F"})


def close_value(row: Any) -> float:
    raw = row.get("close") if isinstance(row, dict) else getattr(row, "close", None)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def is_plausible_macro_close(symbol: str, value: Any) -> bool:
    if symbol in RETIRED_MACRO_SYMBOLS:
        return False
    limits = PLAUSIBLE_CLOSE_RANGES.get(symbol)
    if not limits:
        return True
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return limits[0] <= numeric <= limits[1]


def filter_plausible_price_rows(symbol: str, rows: Iterable[Any]) -> tuple[list[Any], int]:
    accepted: list[Any] = []
    rejected = 0
    for row in rows:
        if is_plausible_macro_close(symbol, close_value(row)):
            accepted.append(row)
        else:
            rejected += 1
    return accepted, rejected
