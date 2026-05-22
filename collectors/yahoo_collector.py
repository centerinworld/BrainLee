"""
collectors/yahoo_collector.py — Yahoo Finance 수집기

담당 데이터:
  · 글로벌 매크로 지표 (VIX, GOLD, OIL, USD/KRW, 나스닥, S&P500)
  · 국내 지수 히스토리 (KOSPI ^KS11, KOSDAQ ^KQ11) — KRX 불가 시 fallback
  · 개별 종목 가격 히스토리 — KIS 불가 시 fallback

yfinance는 동기 라이브러리 → asyncio.to_thread()로 병렬 실행
MACRO_SYMBOLS는 config에서 override 가능
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

import config
from collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# ── 매크로 심볼 (config override 가능) ─────────────────────────
_DEFAULT_MACRO: dict[str, str] = {
    "^VIX":     "VIX",
    "2YY=F":    "US2Y",
    "^UST2Y":   "US2Y_ALT",
    "^TNX":     "US10Y",
    "10Y=F":    "US10Y_ALT",
    "^TYX":     "US30Y",
    "30Y=F":    "US30Y_ALT",
    "GC=F":     "GOLD",
    "CL=F":     "OIL",
    "USDKRW=X": "USD/KRW",
    "DX-Y.NYB": "DXY",
    "^IXIC":    "NASDAQ",
    "^GSPC":    "S&P500",
    "^KS11":    "KOSPI",
    "^KQ11":    "KOSDAQ",
}

MACRO_SYMBOLS: dict[str, str] = getattr(config, "YAHOO_MACRO_SYMBOLS", _DEFAULT_MACRO)

# KIS 실패 시 종목 시장 구분 suffix 후보
_KR_SUFFIXES = [".KS", ".KQ"]


def _safe_float(v: Any) -> float:
    try:
        import pandas as _pd
        if isinstance(v, _pd.Series):
            v = v.iloc[0]
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def _df_to_price_rows(df, inst_net_buy: float = 0.0, frn_net_buy: float = 0.0) -> list[dict]:
    """yfinance DataFrame → price_history 형식 rows."""
    import pandas as _pd
    rows = []
    if df is None or df.empty:
        return rows

    # MultiIndex 컬럼 평탄화 (yfinance 0.2+)
    if isinstance(df.columns, _pd.MultiIndex):
        try:
            df.columns = df.columns.get_level_values(0)
        except Exception:
            pass

    for ts, row in df.iterrows():
        try:
            row_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            rows.append({
                "date":         row_date.isoformat(),
                "open":         _safe_float(row.get("Open",   0)),
                "high":         _safe_float(row.get("High",   0)),
                "low":          _safe_float(row.get("Low",    0)),
                "close":        _safe_float(row.get("Close",  0)),
                "volume":       _safe_float(row.get("Volume", 0)),
                "inst_net_buy": inst_net_buy,
                "frn_net_buy":  frn_net_buy,
            })
        except Exception as e:
            logger.debug(f"[Yahoo] 행 파싱 오류: {e}")

    return rows


def _download_sync(symbol: str, period: str, interval: str = "1d") -> list[dict]:
    """동기 다운로드 — asyncio.to_thread()로 호출."""
    import yfinance as _yf
    df = _yf.download(symbol, period=period, interval=interval,
                      progress=False, auto_adjust=True)
    return _df_to_price_rows(df)


class YahooCollector(BaseCollector):
    """Yahoo Finance 수집기."""

    def __init__(self):
        # yfinance 자체 레이트리밋: 공식 제한 없음, 병렬 과다 요청 방지
        super().__init__(rate_limit_secs=0.0, name="Yahoo")

    # ══════════════════════════════════════════════════════════
    # 1) 매크로 심볼 일괄 수집 (병렬)
    # ══════════════════════════════════════════════════════════

    async def fetch_macro_batch(
        self,
        period: str = "5d",
        backfill_threshold: int = 20,
    ) -> dict[str, list[dict]]:
        """
        MACRO_SYMBOLS 전체를 asyncio.gather로 병렬 수집.
        DB에 backfill_threshold 미만이면 자동으로 60d 수집.

        반환: {symbol: [{date, open, high, low, close, volume, ...}, ...]}
        """
        tasks = {
            symbol: asyncio.to_thread(_download_sync, symbol, period)
            for symbol in MACRO_SYMBOLS
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        output: dict[str, list[dict]] = {}
        for symbol, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"[Yahoo] {symbol} 수집 오류: {result}")
                output[symbol] = []
            else:
                # 데이터 무결성: 합성(today 복제) 행을 만들지 않고
                # 실제 거래일 데이터만 저장한다.
                output[symbol] = result or []

        return output

    async def fetch_macro_backfill(self) -> dict[str, list[dict]]:
        """60d 히스토리 전체 수집 (초기 설치 또는 DB 재구성 시)."""
        return await self.fetch_macro_batch(period="60d")

    # ══════════════════════════════════════════════════════════
    # 2) 개별 종목 가격 히스토리 (KIS fallback)
    # ══════════════════════════════════════════════════════════

    async def fetch_stock_history(
        self,
        stock_code: str,
        period: str = "1y",
    ) -> list[dict]:
        """
        KIS 불가 시 Yahoo로 종목 가격 수집.
        .KS(KOSPI) 먼저 시도 → 없으면 .KQ(KOSDAQ).
        """
        for suffix in _KR_SUFFIXES:
            symbol = f"{stock_code}{suffix}"
            rows   = await asyncio.to_thread(_download_sync, symbol, period)
            if rows:
                logger.debug(f"[Yahoo] {stock_code}{suffix}: {len(rows)}건")
                return rows

        logger.warning(f"[Yahoo] {stock_code}: .KS/.KQ 모두 데이터 없음")
        return []

    async def fetch_stock_histories_bulk(
        self,
        stock_codes: list[str],
        period: str = "1y",
    ) -> dict[str, list[dict]]:
        """여러 종목 Yahoo 히스토리를 병렬 수집."""
        tasks   = [self.fetch_stock_history(code, period) for code in stock_codes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            code: (r if isinstance(r, list) else [])
            for code, r in zip(stock_codes, results)
        }

    # ══════════════════════════════════════════════════════════
    # 3) 지수 히스토리 (KRX 불가 시 fallback)
    # ══════════════════════════════════════════════════════════

    async def fetch_index_history(
        self,
        symbol: str,   # "^KS11" | "^KQ11"
        period: str = "3y",
    ) -> list[dict]:
        """KOSPI/KOSDAQ 지수 히스토리."""
        rows = await asyncio.to_thread(_download_sync, symbol, period)
        return rows
