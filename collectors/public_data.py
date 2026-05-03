"""
collectors/public_data.py — 공공데이터포털 (금융위원회) 수집기

담당 데이터 (5종 비동기 병렬 수집):
  1. 주식시세정보       → stock_price_daily      (일별 OHLCV + 시총)
  2. 대차거래/공매도    → short_sell_daily        (대차잔고, 공매도)
  3. 투자자별매매동향   → investor_trading_daily  (개인/기관/외국인)
  4. 외국인보유현황     → foreign_holding_daily   (보유비율)
  5. 상장회사기본정보   → listed_company_info     (업종, 상장주수)

모든 페이지네이션 자동 처리.
직접 SQLite 삽입 (main.py ingest 엔드포인트 불필요).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import config
from collectors.base import BaseCollector

logger = logging.getLogger(__name__)

_DB_PATH  = str(Path(config.BASE_DIR) / "stock.db")
_BASE_URL = getattr(config, "PUBLIC_DATA_BASE", "https://apis.data.go.kr/1160100/service")
_PAGE_SIZE = 1000   # 최대 1000건/page

# 서비스 경로 상수
_SVC = {
    "price":    "GetStockSecuritiesInfoService/getStockPriceInfo",
    "short":    "GetStocLendBorrInfoService/getStItemLendAndBorrStatu",
    "investor": "GetStocTradInfoService/getStocInvtTrdnInfo",
    "foreign":  "GetStocTradInfoService/getStocFrgnTrdnInfo",
    "company":  "GetKrxListedInfoService/getItemInfo",
}


def _safe(v: Any, cast=float) -> float | str:
    try:
        s = str(v).replace(",", "").strip()
        return cast(s) if s not in ("", "-", "N/A") else 0.0
    except Exception:
        return 0.0


class PublicDataCollector(BaseCollector):
    """공공데이터포털 수집기."""

    def __init__(self, api_key: str = ""):
        super().__init__(
            rate_limit_secs = 0.3,   # 공공데이터포털 — 응답 느림 대비 덤 느리게
            name            = "PubData",
        )
        self._api_key = api_key or getattr(config, "PUBLIC_DATA_API_KEY", "")

    async def _ensure_client(self) -> httpx.AsyncClient:  # type: ignore[override]
        """apis.data.go.kr가 느림 대비 60초 타임아웃 사용."""
        if self._client is None or self._client.is_closed:
            import httpx as _httpx
            self._client = _httpx.AsyncClient(
                timeout          = _httpx.Timeout(60.0, connect=10.0),
                limits           = _httpx.Limits(max_connections=10, max_keepalive_connections=5),
                follow_redirects = True,
            )
        return self._client


    # ── 공통 페이지네이션 요청 ──────────────────────────────────

    async def _fetch_all_pages(
        self,
        service_path: str,
        extra_params: dict,
    ) -> list[dict]:
        """페이지네이션을 자동으로 처리해 모든 항목 반환."""
        all_items: list[dict] = []
        page = 1
        while True:
            params = {
                "serviceKey": self._api_key,
                "resultType": "json",
                "numOfRows":  str(_PAGE_SIZE),
                "pageNo":     str(page),
                **extra_params,
            }
            data = await self._get(
                f"{_BASE_URL}/{service_path}",
                params=params,
                timeout=60,   # apis.data.go.kr 응답 느림 대비
            )
            if not data:
                break

            body  = data.get("response", {}).get("body", {})
            items = body.get("items", {})
            if not items:
                break

            # 공공API: items.item이 list 또는 단일 dict
            rows = items.get("item", [])
            if isinstance(rows, dict):
                rows = [rows]
            if not rows:
                break

            all_items.extend(rows)

            total_count = int(body.get("totalCount", 0))
            if len(all_items) >= total_count or len(rows) < _PAGE_SIZE:
                break
            page += 1

        return all_items

    # ══════════════════════════════════════════════════════════
    # 1) 주식 시세 (OHLCV + 시총)
    # ══════════════════════════════════════════════════════════

    async def fetch_stock_prices(self, bas_dt: str) -> list[dict]:
        """일별 전종목 OHLCV + 시총. bas_dt: 'YYYYMMDD'"""
        rows = await self._fetch_all_pages(
            _SVC["price"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", r.get("isinCd", ""))).strip().zfill(6)
            if not code.isdigit():
                continue
            result.append({
                "bas_dt":     bas_dt,
                "stock_code": code,
                "stock_name": r.get("itmsNm", ""),
                "market":     r.get("mrktCtg", ""),
                "open_price": _safe(r.get("mkp",  0)),
                "high_price": _safe(r.get("hipr", 0)),
                "low_price":  _safe(r.get("lopr", 0)),
                "close_price":_safe(r.get("clpr", 0)),
                "vs":         _safe(r.get("vs",   0)),
                "change_pct": _safe(r.get("fltRt",0)),
                "volume":     _safe(r.get("trqu", 0)),
                "trade_amt":  _safe(r.get("trPrc",0)),
                "market_cap": _safe(r.get("mktTotAmt", 0)),
                "shares":     _safe(r.get("lstgStCnt", 0)),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 2) 대차/공매도
    # ══════════════════════════════════════════════════════════

    async def fetch_short_sell(self, bas_dt: str) -> list[dict]:
        rows = await self._fetch_all_pages(
            _SVC["short"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", "")).strip().zfill(6)
            if not code.isdigit():
                continue
            result.append({
                "bas_dt":        bas_dt,
                "stock_code":    code,
                "stock_name":    r.get("itmsNm", ""),
                "short_qty":     _safe(r.get("shrtSellVolume",  0)),
                "short_amt":     _safe(r.get("shrtSellAmt",     0)),
                "borrow_bal_qty":_safe(r.get("borrowBalVolume", 0)),
                "borrow_bal_amt":_safe(r.get("borrowBalAmt",    0)),
                "borrow_bal_pct":_safe(r.get("borrowBalRt",     0)),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 3) 투자자별 매매동향
    # ══════════════════════════════════════════════════════════

    async def fetch_investor_trading(self, bas_dt: str) -> list[dict]:
        rows = await self._fetch_all_pages(
            _SVC["investor"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", r.get("isinCd", ""))).strip().zfill(6)
            if not code.isdigit():
                continue
            result.append({
                "bas_dt":     bas_dt,
                "stock_code": code,
                "stock_name": r.get("itmsNm", ""),
                "indv_buy":   _safe(r.get("indvBuyAmt",  0)),
                "indv_sell":  _safe(r.get("indvSellAmt", 0)),
                "indv_net":   _safe(r.get("indvNetAmt",  0)),
                "inst_buy":   _safe(r.get("instBuyAmt",  0)),
                "inst_sell":  _safe(r.get("instSellAmt", 0)),
                "inst_net":   _safe(r.get("instNetAmt",  0)),
                "frgn_buy":   _safe(r.get("frgnBuyAmt",  0)),
                "frgn_sell":  _safe(r.get("frgnSellAmt", 0)),
                "frgn_net":   _safe(r.get("frgnNetAmt",  0)),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 4) 외국인 보유현황
    # ══════════════════════════════════════════════════════════

    async def fetch_foreign_holding(self, bas_dt: str) -> list[dict]:
        rows = await self._fetch_all_pages(
            _SVC["foreign"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", r.get("isinCd", ""))).strip().zfill(6)
            if not code.isdigit():
                continue
            result.append({
                "bas_dt":       bas_dt,
                "stock_code":   code,
                "stock_name":   r.get("itmsNm", ""),
                "frgn_hold_qty":_safe(r.get("frgnHoldQty", 0)),
                "frgn_hold_pct":_safe(r.get("frgnHoldRt",  0)),
                "frgn_limit_pct":_safe(r.get("frgnLmtExhstRt", 0)),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 5) 상장회사 기본정보
    # ══════════════════════════════════════════════════════════

    async def fetch_listed_companies(self, bas_dt: str) -> list[dict]:
        rows = await self._fetch_all_pages(
            _SVC["company"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", r.get("isinCd", ""))).strip().zfill(6)
            if not code.isdigit():
                continue
            result.append({
                "bas_dt":     bas_dt,
                "stock_code": code,
                "stock_name": r.get("itmsNm", ""),
                "market":     r.get("mrktCtg", ""),
                "sector":     r.get("indutyCd", ""),
                "listing_dt": r.get("lstgDt", ""),
                "shares":     _safe(r.get("lstgStCnt", 0)),
                "face_val":   _safe(r.get("parPrc",    0)),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 6) 날짜별 전체 수집 (5종 병렬)
    # ══════════════════════════════════════════════════════════

    async def collect_all_for_date(self, bas_dt: str) -> dict[str, int]:
        """
        5종 데이터를 asyncio.gather로 병렬 수집 후 SQLite에 bulk upsert.
        반환: {dataset_name: rows_saved}
        """
        price_task    = self.fetch_stock_prices(bas_dt)
        short_task    = self.fetch_short_sell(bas_dt)
        investor_task = self.fetch_investor_trading(bas_dt)
        foreign_task  = self.fetch_foreign_holding(bas_dt)
        company_task  = self.fetch_listed_companies(bas_dt)

        price, short, investor, foreign, company = await asyncio.gather(
            price_task, short_task, investor_task, foreign_task, company_task,
            return_exceptions=True,
        )

        def _unwrap(r, name):
            if isinstance(r, Exception):
                logger.error(f"[PubData] {name} 수집 오류: {r}")
                return []
            return r or []

        datasets = {
            "stock_price_daily":     _unwrap(price,    "price"),
            "short_sell_daily":      _unwrap(short,    "short"),
            "investor_trading_daily":_unwrap(investor, "investor"),
            "foreign_holding_daily": _unwrap(foreign,  "foreign"),
            "listed_company_info":   _unwrap(company,  "company"),
        }

        saved = await asyncio.to_thread(self._bulk_upsert_sync, datasets)
        for name, cnt in saved.items():
            logger.info(f"[PubData] {name}: {cnt}건 저장 ({bas_dt})")
        return saved

    # ══════════════════════════════════════════════════════════
    # 7) 날짜 범위 백필
    # ══════════════════════════════════════════════════════════

    async def backfill(self, start_date: str, end_date: str) -> None:
        """
        start_date ~ end_date (YYYYMMDD) 사이 영업일 데이터 순차 수집.
        영업일 판별: 월~금, 공휴일 제외 없음 (주말만 건너뜀).
        """
        start = datetime.strptime(start_date, "%Y%m%d").date()
        end   = datetime.strptime(end_date,   "%Y%m%d").date()
        cur   = start
        while cur <= end:
            if cur.weekday() < 5:   # 월~금
                bas_dt = cur.strftime("%Y%m%d")
                logger.info(f"[PubData 백필] {bas_dt} 수집 시작")
                await self.collect_all_for_date(bas_dt)
            cur += timedelta(days=1)

    # ══════════════════════════════════════════════════════════
    # 내부: SQLite bulk upsert (동기 — 스레드에서 실행)
    # ══════════════════════════════════════════════════════════

    def _bulk_upsert_sync(self, datasets: dict[str, list[dict]]) -> dict[str, int]:
        saved: dict[str, int] = {}
        conn  = sqlite3.connect(_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            for table, rows in datasets.items():
                if not rows:
                    saved[table] = 0
                    continue
                cols     = list(rows[0].keys())
                placeholders = ", ".join("?" * len(cols))
                col_names    = ", ".join(cols)
                update_set   = ", ".join(
                    f"{c}=excluded.{c}"
                    for c in cols
                    if c not in ("bas_dt", "stock_code")
                )
                sql = (
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                    f"ON CONFLICT(bas_dt, stock_code) DO UPDATE SET {update_set}"
                )
                data = [tuple(r[c] for c in cols) for r in rows]
                conn.executemany(sql, data)
                saved[table] = len(rows)

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[PubData] DB upsert 오류: {e}")
        finally:
            conn.close()

        return saved
