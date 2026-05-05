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

# V2 대차정보 서비스 (2026-04-21 승인)
_SHORT_V2_BASE  = "https://apis.data.go.kr/1160100/GetStocLendBorrInfoService_V2"
_SHORT_V2_SVC   = "getStItemLendAndBorrStatu_V2"      # 종목별 대차거래현황
_SHORT_V2_RANK  = "getStLendAndBorrItemRank_V2"       # 대차종목순위
_SHORT_V2_MONTH = "getMontLendAndBorrStatu_V2"        # 월별대차거래현황
_SHORT_V2_INDST = "getStBusiTypePartStatu_V2"         # 업종별참여현황
_SHORT_V2_FBAL  = "getNatiAndForeLendAndBorrBalaCo_V2" # 내외국인 잔고비교
_SHORT_V2_FTRAD = "getNatiAndForeLendAndBorrTrad_V2"  # 내외국인 거래량

# 서비스 경로 상수
_SVC = {
    "price":    "GetStockSecuritiesInfoService/getStockPriceInfo",
    "short":    "GetStocLendBorrInfoService/getStItemLendAndBorrStatu",  # V1 fallback
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
    # 2) 대차/공매도 — V2 우선, V1 fallback
    # ══════════════════════════════════════════════════════════

    async def fetch_short_sell_v2(self, target_date: str | None = None) -> list[dict]:
        """V2 대차정보 API — 날짜 필터 미지원으로 마지막 페이지부터 역순 수집.

        target_date: 'YYYYMMDD' — None이면 가장 최신 영업일 데이터 반환.
        V2 API는 날짜 파라미터를 무시하므로, 마지막 페이지를 역방향으로 읽어
        target_date(또는 최신일)에 해당하는 레코드만 추출한다.
        """
        import math as _math
        import httpx as _httpx

        url = f"{_SHORT_V2_BASE}/{_SHORT_V2_SVC}"
        base_params = {
            "serviceKey": self._api_key,
            "resultType": "json",
            "numOfRows":  str(_PAGE_SIZE),
        }

        # 1) 총 건수 조회
        async with _httpx.AsyncClient(timeout=60) as client:
            r0 = await client.get(url, params={**base_params, "pageNo": "1", "numOfRows": "1"})
            total = (r0.json().get("response", {}).get("body", {})
                     .get("totalCount", 0))
        if not total:
            return []

        last_page  = _math.ceil(total / _PAGE_SIZE)
        all_recent: list[dict] = []

        # 2) 마지막 페이지부터 역순으로 읽어 target_date 레코드 수집
        async with _httpx.AsyncClient(timeout=120) as client:
            for pg in range(last_page, max(last_page - 5, 0), -1):
                r = await client.get(url, params={**base_params, "pageNo": str(pg)})
                items = (r.json().get("response", {}).get("body", {})
                         .get("items", {}).get("item", []))
                if not items:
                    break
                if isinstance(items, dict):
                    items = [items]

                dates_in_page = {i.get("basDt") for i in items}
                if not target_date:
                    target_date = max(dates_in_page)

                page_records = [i for i in items if i.get("basDt") == target_date]
                all_recent.extend(page_records)

                # 이 페이지에 target_date 이전 날짜도 있으면 → 수집 완료
                if any(d < target_date for d in dates_in_page):
                    break

        result = []
        for r in all_recent:
            isin = str(r.get("isinCd", "")).strip()
            if not isin.startswith("KR") or len(isin) != 12:
                continue
            code = isin[3:9]
            if not code.isdigit() or code == "000000":
                continue
            result.append({
                "bas_dt":        target_date,
                "stock_code":    code,
                "stock_name":    r.get("isinCdNm", ""),
                "short_qty":     _safe(r.get("lnbCclStckCnt",  0)),
                "short_amt":     None,
                "borrow_bal_qty":_safe(r.get("lnbRmanStckCnt", 0)),
                "borrow_bal_amt":None,
                "borrow_bal_pct":None,
            })
        logger.info(f"[대차V2] {target_date}: {len(result)}건 수집")
        return result

    async def fetch_short_sell(self, bas_dt: str) -> list[dict]:
        """V2 우선, 실패 시 V1 fallback."""
        try:
            result = await self.fetch_short_sell_v2(bas_dt)
            if result:
                return result
        except Exception as e:
            logger.warning(f"[대차V2] 실패 ({e}), V1 fallback 시도")

        # V1 fallback (apis.data.go.kr 차단 해제 시 사용)
        rows = await self._fetch_all_pages(
            _SVC["short"],
            {"basDt": bas_dt},
        )
        result = []
        for r in rows:
            code = str(r.get("srtnCd", "")).strip().zfill(6)
            if not code.isdigit() or code == "000000":
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
    # V2 대차정보 — 공통 헬퍼
    # ══════════════════════════════════════════════════════════

    async def _fetch_v2_pages(self, service: str, bas_dt: str | None = None, extra: dict | None = None) -> list[dict]:
        """V2 대차 API 일반 페이지네이션 (날짜 필터 지원 엔드포인트용)."""
        import httpx as _httpx
        url = f"{_SHORT_V2_BASE}/{service}"
        params: dict = {"serviceKey": self._api_key, "resultType": "json", "numOfRows": str(_PAGE_SIZE)}
        if bas_dt:
            params["basDt"] = bas_dt
        if extra:
            params.update(extra)
        all_items: list[dict] = []
        page = 1
        async with _httpx.AsyncClient(timeout=60) as client:
            while True:
                r = await client.get(url, params={**params, "pageNo": str(page)})
                body  = r.json().get("response", {}).get("body", {})
                items = body.get("items") or {}
                rows  = items.get("item", []) if isinstance(items, dict) else []
                if isinstance(rows, dict):
                    rows = [rows]
                if not rows:
                    break
                all_items.extend(rows)
                total = int(body.get("totalCount", 0))
                if len(all_items) >= total or len(rows) < _PAGE_SIZE:
                    break
                page += 1
        return all_items

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 대차종목순위 (getStLendAndBorrItemRank_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_rank(self, bas_dt: str) -> list[dict]:
        """일별 대차종목순위 (대차잔여주식수 기준 정렬)."""
        rows = await self._fetch_v2_pages(_SHORT_V2_RANK, bas_dt)
        result = []
        for r in rows:
            isin = str(r.get("isinCd", "")).strip()
            # stock_code: KR-prefix ISIN이면 [3:9], 6자리 숫자면 그대로
            code: str | None = None
            if isin.startswith("KR") and len(isin) == 12:
                c = isin[3:9]
                code = c if c.isdigit() and c != "000000" else None
            elif len(isin) == 6 and isin.isdigit() and isin != "000000":
                code = isin
            result.append({
                "bas_dt":             bas_dt,
                "isin_cd":            isin,
                "stock_code":         code,
                "stock_name":         r.get("isinCdNm", ""),
                "lnb_scrt_dcd":       r.get("lnbScrtDcd", ""),
                "lnb_ccl_stck_cnt":   _safe(r.get("lnbCclStckCnt",  0)),
                "rcal_rdpt_stck_cnt": _safe(r.get("rcalRdptStckCnt",0)),
                "rdpt_stck_cnt":      _safe(r.get("rdptStckCnt",    0)),
                "lnb_rman_stck_cnt":  _safe(r.get("lnbRmanStckCnt", 0)),
                "lnb_bal":            _safe(r.get("lnbBal",         0)),
            })
        logger.info(f"[대차순위] {bas_dt}: {len(result)}건")
        return result

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 월별 집계 (getMontLendAndBorrStatu_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_monthly(self, bas_dt: str | None = None) -> list[dict]:
        """월별 대차거래현황 집계."""
        rows = await self._fetch_v2_pages(_SHORT_V2_MONTH, bas_dt)
        return [{
            "bas_dt":            r.get("basDt", bas_dt or ""),
            "lnb_expr_itms_cnt": int(_safe(r.get("lnbExprItmsCnt", 0))),
            "lnb_ccl_stck_cnt":  _safe(r.get("lnbCclStckCnt",  0)),
            "lnb_ccl_amt":       _safe(r.get("lnbCclAmt",      0)),
            "lnb_rdpt_stck_cnt": _safe(r.get("lnbRdptStckCnt", 0)),
            "lnb_rdpt_amt":      _safe(r.get("lnbRdptAmt",     0)),
            "lnb_rman_stck_cnt": _safe(r.get("lnbRmanStckCnt", 0)),
            "lnb_bal":           _safe(r.get("lnbBal",         0)),
        } for r in rows]

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 내외국인 잔고비교 (getNatiAndForeLendAndBorrBalaCo_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_foreign_balance(self, bas_dt: str | None = None) -> list[dict]:
        """일별 내외국인 대차잔고비교."""
        rows = await self._fetch_v2_pages(_SHORT_V2_FBAL, bas_dt)
        return [{
            "bas_dt":            r.get("basDt", bas_dt or ""),
            "ntiv_brw_bal":      _safe(r.get("ntivBrwBal",    0)),
            "forg_brw_bal":      _safe(r.get("forgBrwBal",    0)),
            "brw_bal_forg_rto":  _safe(r.get("brwBalForgRto", 0)),
            "ntiv_lndn_bal":     _safe(r.get("ntivLndnBal",   0)),
            "forg_lndn_bal":     _safe(r.get("forgLndnBal",   0)),
            "lndn_bal_forg_rto": _safe(r.get("lndnBalForgRto",0)),
        } for r in rows]

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 내외국인 거래량 (getNatiAndForeLendAndBorrTrad_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_foreign_trade(self, bas_dt: str | None = None) -> list[dict]:
        """일별 내외국인 대차거래량."""
        rows = await self._fetch_v2_pages(_SHORT_V2_FTRAD, bas_dt)
        return [{
            "bas_dt":                 r.get("basDt", bas_dt or ""),
            "forg_lnb_ccl_stck_cnt":  _safe(r.get("forgLnbCclStckCnt", 0)),
            "forg_lnb_ccl_amt":       _safe(r.get("forgLnbCclAmt",     0)),
            "ntiv_lnb_ccl_stck_cnt":  _safe(r.get("ntivLnbCclStckCnt", 0)),
            "ntiv_lnb_ccl_amt":       _safe(r.get("ntivLnbCclAmt",     0)),
            "sum_lnb_ccl_stck_cnt":   _safe(r.get("sumLnbCclStckCnt",  0)),
            "sum_lnb_ccl_amt":        _safe(r.get("sumLnbCclAmt",      0)),
        } for r in rows]

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 날짜별 전체 대차 수집 (4종 병렬)
    # ══════════════════════════════════════════════════════════

    async def collect_short_all_for_date(self, bas_dt: str) -> dict[str, int]:
        """대차종목순위 + 월별 + 내외국인 잔고/거래량 병렬 수집 후 DB 저장."""
        rank_task   = self.fetch_short_rank(bas_dt)
        month_task  = self.fetch_short_monthly(bas_dt)
        fbal_task   = self.fetch_short_foreign_balance(bas_dt)
        ftrad_task  = self.fetch_short_foreign_trade(bas_dt)

        rank, month, fbal, ftrad = await asyncio.gather(
            rank_task, month_task, fbal_task, ftrad_task,
            return_exceptions=True,
        )

        def _unwrap(r, name):
            if isinstance(r, Exception):
                logger.error(f"[대차수집] {name} 오류: {r}")
                return []
            return r or []

        saved = await asyncio.to_thread(
            self._bulk_upsert_short_sync,
            _unwrap(rank,  "rank"),
            _unwrap(month, "monthly"),
            _unwrap(fbal,  "fbal"),
            _unwrap(ftrad, "ftrad"),
        )
        for name, cnt in saved.items():
            logger.info(f"[대차수집] {name}: {cnt}건 ({bas_dt})")
        return saved

    def _bulk_upsert_short_sync(self, rank_rows, month_rows, fbal_rows, ftrad_rows) -> dict[str, int]:
        """대차 관련 4개 테이블 동기 upsert."""
        conn = sqlite3.connect(_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        saved: dict[str, int] = {}
        try:
            # 대차종목순위 — UNIQUE(bas_dt, isin_cd)
            if rank_rows:
                conn.executemany("""
                    INSERT INTO short_rank_daily
                      (bas_dt,isin_cd,stock_code,stock_name,lnb_scrt_dcd,
                       lnb_ccl_stck_cnt,rcal_rdpt_stck_cnt,rdpt_stck_cnt,
                       lnb_rman_stck_cnt,lnb_bal)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt,isin_cd) DO UPDATE SET
                      stock_code=excluded.stock_code, stock_name=excluded.stock_name,
                      lnb_ccl_stck_cnt=excluded.lnb_ccl_stck_cnt,
                      rcal_rdpt_stck_cnt=excluded.rcal_rdpt_stck_cnt,
                      rdpt_stck_cnt=excluded.rdpt_stck_cnt,
                      lnb_rman_stck_cnt=excluded.lnb_rman_stck_cnt,
                      lnb_bal=excluded.lnb_bal
                """, [(r["bas_dt"],r["isin_cd"],r["stock_code"],r["stock_name"],r["lnb_scrt_dcd"],
                       r["lnb_ccl_stck_cnt"],r["rcal_rdpt_stck_cnt"],r["rdpt_stck_cnt"],
                       r["lnb_rman_stck_cnt"],r["lnb_bal"]) for r in rank_rows])
                saved["short_rank_daily"] = len(rank_rows)

            # 월별대차거래현황 — UNIQUE(bas_dt)
            if month_rows:
                conn.executemany("""
                    INSERT INTO short_monthly_stat
                      (bas_dt,lnb_expr_itms_cnt,lnb_ccl_stck_cnt,lnb_ccl_amt,
                       lnb_rdpt_stck_cnt,lnb_rdpt_amt,lnb_rman_stck_cnt,lnb_bal)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt) DO UPDATE SET
                      lnb_expr_itms_cnt=excluded.lnb_expr_itms_cnt,
                      lnb_ccl_stck_cnt=excluded.lnb_ccl_stck_cnt,
                      lnb_ccl_amt=excluded.lnb_ccl_amt,
                      lnb_rdpt_stck_cnt=excluded.lnb_rdpt_stck_cnt,
                      lnb_rdpt_amt=excluded.lnb_rdpt_amt,
                      lnb_rman_stck_cnt=excluded.lnb_rman_stck_cnt,
                      lnb_bal=excluded.lnb_bal
                """, [(r["bas_dt"],r["lnb_expr_itms_cnt"],r["lnb_ccl_stck_cnt"],r["lnb_ccl_amt"],
                       r["lnb_rdpt_stck_cnt"],r["lnb_rdpt_amt"],r["lnb_rman_stck_cnt"],r["lnb_bal"]) for r in month_rows])
                saved["short_monthly_stat"] = len(month_rows)

            # 내외국인 잔고비교 — UNIQUE(bas_dt)
            if fbal_rows:
                conn.executemany("""
                    INSERT INTO short_foreign_balance
                      (bas_dt,ntiv_brw_bal,forg_brw_bal,brw_bal_forg_rto,
                       ntiv_lndn_bal,forg_lndn_bal,lndn_bal_forg_rto)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt) DO UPDATE SET
                      ntiv_brw_bal=excluded.ntiv_brw_bal,
                      forg_brw_bal=excluded.forg_brw_bal,
                      brw_bal_forg_rto=excluded.brw_bal_forg_rto,
                      ntiv_lndn_bal=excluded.ntiv_lndn_bal,
                      forg_lndn_bal=excluded.forg_lndn_bal,
                      lndn_bal_forg_rto=excluded.lndn_bal_forg_rto
                """, [(r["bas_dt"],r["ntiv_brw_bal"],r["forg_brw_bal"],r["brw_bal_forg_rto"],
                       r["ntiv_lndn_bal"],r["forg_lndn_bal"],r["lndn_bal_forg_rto"]) for r in fbal_rows])
                saved["short_foreign_balance"] = len(fbal_rows)

            # 내외국인 거래량 — UNIQUE(bas_dt)
            if ftrad_rows:
                conn.executemany("""
                    INSERT INTO short_foreign_trade
                      (bas_dt,forg_lnb_ccl_stck_cnt,forg_lnb_ccl_amt,
                       ntiv_lnb_ccl_stck_cnt,ntiv_lnb_ccl_amt,
                       sum_lnb_ccl_stck_cnt,sum_lnb_ccl_amt)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt) DO UPDATE SET
                      forg_lnb_ccl_stck_cnt=excluded.forg_lnb_ccl_stck_cnt,
                      forg_lnb_ccl_amt=excluded.forg_lnb_ccl_amt,
                      ntiv_lnb_ccl_stck_cnt=excluded.ntiv_lnb_ccl_stck_cnt,
                      ntiv_lnb_ccl_amt=excluded.ntiv_lnb_ccl_amt,
                      sum_lnb_ccl_stck_cnt=excluded.sum_lnb_ccl_stck_cnt,
                      sum_lnb_ccl_amt=excluded.sum_lnb_ccl_amt
                """, [(r["bas_dt"],r["forg_lnb_ccl_stck_cnt"],r["forg_lnb_ccl_amt"],
                       r["ntiv_lnb_ccl_stck_cnt"],r["ntiv_lnb_ccl_amt"],
                       r["sum_lnb_ccl_stck_cnt"],r["sum_lnb_ccl_amt"]) for r in ftrad_rows])
                saved["short_foreign_trade"] = len(ftrad_rows)

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[대차수집] DB upsert 오류: {e}")
        finally:
            conn.close()
        return saved

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
