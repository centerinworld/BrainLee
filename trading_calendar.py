"""
trading_calendar.py — 한국·미국·일본 주식시장 휴장일 관리

사용법:
    from trading_calendar import is_trading_day, is_kr_trading_day

    if not is_kr_trading_day():          # 오늘이 한국 장이 쉬는 날이면
        return                           # 수집 스킵

    if not is_trading_day(date, 'KR'):   # 특정 날짜 확인
        skip_write_to_db(date)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Literal

import holidays as _holidays

logger = logging.getLogger(__name__)

Market = Literal["KR", "US", "JP"]

# KRX는 제헌절(7/17)을 2008년부터 휴장하지 않음 — holidays 패키지는 포함하므로 제거
_KR_REMOVED = {
    # (month, day): 제헌절은 공휴일이지만 주식시장은 개장
    (7, 17),
}


@lru_cache(maxsize=16)
def _get_holiday_set(market: Market, year: int) -> frozenset[date]:
    """연도별 휴장일 집합 (캐시됨)."""
    if market == "KR":
        raw = _holidays.KR(years=year)
        result = frozenset(
            d for d, _ in raw.items()
            if (d.month, d.day) not in _KR_REMOVED
        )
    elif market == "US":
        # NYSE 기준: NYSE Early Close 제외, 완전 휴장일만
        raw = _holidays.NYSE(years=year)
        result = frozenset(raw.keys())
    elif market == "JP":
        raw = _holidays.JP(years=year)
        result = frozenset(raw.keys())
    else:
        result = frozenset()
    return result


def is_trading_day(d: date | None = None, market: Market = "KR") -> bool:
    """
    d가 해당 마켓의 정규 거래일이면 True.
    d=None이면 오늘 날짜 사용.
    주말(토·일) + 공휴일 모두 False.
    """
    if d is None:
        d = date.today()
    if d.weekday() >= 5:          # 토=5, 일=6
        return False
    return d not in _get_holiday_set(market, d.year)


def is_kr_trading_day(d: date | None = None) -> bool:
    """한국 거래일 여부 (편의 함수)."""
    return is_trading_day(d, "KR")


def is_us_trading_day(d: date | None = None) -> bool:
    """미국(NYSE) 거래일 여부."""
    return is_trading_day(d, "US")


def is_jp_trading_day(d: date | None = None) -> bool:
    """일본 거래일 여부."""
    return is_trading_day(d, "JP")


def get_holiday_name(d: date, market: Market = "KR") -> str | None:
    """해당 날짜의 휴일 이름 반환. 거래일이면 None."""
    if d.weekday() >= 5:
        return "주말"
    if market == "KR":
        raw = _holidays.KR(years=d.year)
        if (d.month, d.day) in _KR_REMOVED:
            return None
        return raw.get(d)
    elif market == "US":
        raw = _holidays.NYSE(years=d.year)
        return raw.get(d)
    elif market == "JP":
        raw = _holidays.JP(years=d.year)
        return raw.get(d)
    return None


def filter_trading_dates(dates: list[date], market: Market = "KR") -> list[date]:
    """날짜 리스트에서 거래일만 필터링."""
    return [d for d in dates if is_trading_day(d, market)]


# ── 빠른 자가 진단 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    today = date.today()
    for mkt in ("KR", "US", "JP"):
        print(f"{mkt}: today({today}) trading={is_trading_day(today, mkt)}")  # type: ignore[arg-type]
    print()
    print("=== KR 2026 holidays ===")
    for d in sorted(_get_holiday_set("KR", 2026)):
        print(d, get_holiday_name(d, "KR"))
