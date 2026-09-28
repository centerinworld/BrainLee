"""Pure helpers for US adjusted OHLCV technical data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class TechnicalSnapshot:
    ma5: float | None
    ma20: float | None
    ma50: float | None
    ma60: float | None
    ma200: float | None
    high_52w: float
    low_52w: float


def technical_snapshot(
    rows: Sequence[tuple[float, float, float, float]],
) -> TechnicalSnapshot:
    """Compute close MAs and actual high/low bounds from ascending OHLC rows."""
    if not rows:
        raise ValueError("at least one OHLC row is required")
    highs = [float(r[1]) for r in rows]
    lows = [float(r[2]) for r in rows]
    closes = [float(r[3]) for r in rows]

    def mean(n: int) -> float | None:
        return sum(closes[-n:]) / n if len(closes) >= n else None

    window = min(252, len(rows))
    return TechnicalSnapshot(
        ma5=mean(5), ma20=mean(20), ma50=mean(50), ma60=mean(60), ma200=mean(200),
        high_52w=max(highs[-window:]), low_52w=min(lows[-window:]),
    )


def _aggregate_by_bucket(
    rows: Sequence[tuple[str, float, float, float, float, float | None]],
    bucket_key,
) -> list[tuple[str, float, float, float, float, float]]:
    """Shared OHLCV rollup: group ascending daily bars by ``bucket_key(date)``.

    Each returned date is the last actual trading date in that bucket, so
    holiday-shortened weeks/months never get an invented closing date.
    """
    buckets: dict[object, list[tuple[str, float, float, float, float, float | None]]] = {}
    for row in rows:
        day = date.fromisoformat(str(row[0])[:10])
        buckets.setdefault(bucket_key(day), []).append(row)
    out = []
    for bars in buckets.values():
        bars = sorted(bars, key=lambda x: x[0])
        out.append((
            str(bars[-1][0])[:10], float(bars[0][1]),
            max(float(x[2]) for x in bars), min(float(x[3]) for x in bars),
            float(bars[-1][4]), sum(float(x[5] or 0) for x in bars),
        ))
    return sorted(out, key=lambda x: x[0])


def aggregate_weekly_ohlcv(
    rows: Sequence[tuple[str, float, float, float, float, float | None]],
) -> list[tuple[str, float, float, float, float, float]]:
    """Aggregate ascending daily bars into Monday-based trading weeks."""
    return _aggregate_by_bucket(rows, lambda day: day.isocalendar()[:2])


def aggregate_monthly_ohlcv(
    rows: Sequence[tuple[str, float, float, float, float, float | None]],
) -> list[tuple[str, float, float, float, float, float]]:
    """Aggregate ascending daily bars into calendar-month trading months."""
    return _aggregate_by_bucket(rows, lambda day: (day.year, day.month))
