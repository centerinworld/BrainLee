"""Pure integrity checks for the adjusted US OHLCV series."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


UPPER_DAILY_RATIO = 1.30
LOWER_DAILY_RATIO = 0.70


@dataclass(frozen=True)
class USPricePoint:
    date: str
    close: float


def valid_ohlc(opn: float, high: float, low: float, close: float) -> bool:
    values = tuple(float(x) for x in (opn, high, low, close))
    if min(values) <= 0:
        return False
    tolerance = max(values) * 1e-6
    return (high + tolerance >= max(opn, close)
            and low - tolerance <= min(opn, close)
            and high + tolerance >= low)


def large_price_events(
    points: Sequence[USPricePoint], *, upper: float = UPPER_DAILY_RATIO,
    lower: float = LOWER_DAILY_RATIO,
) -> list[dict]:
    """Return adjacent close moves outside the explicit +30%/-30% band."""
    events: list[dict] = []
    for previous, current in zip(points, points[1:]):
        if previous.close <= 0 or current.close <= 0:
            continue
        ratio = current.close / previous.close
        if ratio > upper or ratio < lower:
            events.append({
                "previous_date": previous.date,
                "event_date": current.date,
                "previous_close": previous.close,
                "close": current.close,
                "ratio": ratio,
            })
    return events


def basis_whiplashes(
    points: Sequence[USPricePoint], *, upper: float = UPPER_DAILY_RATIO,
    lower: float = LOWER_DAILY_RATIO, max_following_sessions: int = 3,
    net_low: float = 0.80, net_high: float = 1.20,
) -> list[dict]:
    """Detect a large move quickly reversed to the old basis.

    This is a narrow splice detector, not a corporate-action classifier.  A
    genuine one-day event such as MRNA on 2026-08-19 remains on the new level
    and therefore does not match this pattern.
    """
    found: list[dict] = []
    for i in range(1, len(points)):
        before, event = points[i - 1], points[i]
        if before.close <= 0 or event.close <= 0:
            continue
        first = event.close / before.close
        if not (first > upper or first < lower):
            continue
        for later in points[i + 1:i + 1 + max_following_sessions]:
            if later.close <= 0:
                continue
            reverse = later.close / event.close
            net = later.close / before.close
            opposite = (first > upper and reverse < lower) or (first < lower and reverse > upper)
            if opposite and net_low <= net <= net_high:
                found.append({
                    "previous_date": before.date,
                    "event_date": event.date,
                    "reversal_date": later.date,
                    "event_ratio": first,
                    "reversal_ratio": reverse,
                    "net_ratio": net,
                })
                break
    return found


def overlap_basis_mismatches(
    existing: Iterable[tuple[str, float]], incoming: Iterable[tuple[str, float]],
    *, tolerance: float = 0.02,
) -> list[dict]:
    """Compare the same adjusted close/date from DB and a fresh source."""
    old = {str(day)[:10]: float(close) for day, close in existing if close and float(close) > 0}
    mismatches: list[dict] = []
    for day, close in incoming:
        day = str(day)[:10]
        close = float(close)
        if day not in old or close <= 0:
            continue
        ratio = old[day] / close
        if abs(ratio - 1.0) > tolerance:
            mismatches.append({"date": day, "stored_close": old[day],
                               "incoming_close": close, "ratio": ratio})
    return mismatches
