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
        """V2 종목별대차거래현황 (getStItemLendAndBorrStatu_V2).

        API 가이드 확인: basDt 파라미터를 지원함 (항목구분: 옵션).
        target_date 지정 시 해당 날짜 직접 요청. None이면 전체에서 최신일 추출.

        응답 필드:
          isinCd         — KR7XXXXXX 형식 12자리 ISIN
          isinCdNm       — 종목명
          lnbCclStckCnt  — 대차체결주식수 (당일 신규 대차)
          lnbRdptStckCnt — 대차상환주식수 (당일 상환)
          lnbRmanStckCnt — 대차잔여주식수 (누적 잔고)
        """
        rows = await self._fetch_v2_pages(_SHORT_V2_SVC, target_date)

        # target_date 미지정 시 최신 날짜 자동 결정
        if not target_date and rows:
            target_date = max(r.get("basDt", "") for r in rows)

        result = []
        for r in rows:
            isin = str(r.get("isinCd", "")).strip()
            if not isin.startswith("KR") or len(isin) != 12:
                continue
            code = isin[3:9]
            if not code.isdigit() or code == "000000":
                continue
            bas = r.get("basDt", target_date or "")
            if target_date and bas != target_date:
                continue  # 날짜 필터가 작동 안 할 경우 방어
            result.append({
                "bas_dt":          bas,
                "stock_code":      code,
                "stock_name":      r.get("isinCdNm", ""),
                "short_qty":       _safe(r.get("lnbCclStckCnt",  0)),  # 당일 체결
                "short_rdpt_qty":  _safe(r.get("lnbRdptStckCnt", 0)),  # 당일 상환
                "borrow_bal_qty":  _safe(r.get("lnbRmanStckCnt", 0)),  # 잔고
                "short_amt":       None,
                "borrow_bal_amt":  None,
                "borrow_bal_pct":  None,
            })
        logger.info(f"[대차V2-SVC] {target_date}: {len(result)}건 수집")
        return result

    async def fetch_short_sector(self, bas_dt: str | None = None) -> list[dict]:
        """V2 업종별참여현황 (getStBusiTypePartStatu_V2).

        응답 필드:
          isinCd, isinCdNm — 종목코드/명
          sicCd, sicNm     — 표준산업분류코드/명
          stckLndnBal      — 주식대여잔액
          stckLndnRto      — 주식대여비율
          stckBrwBal       — 주식차입잔액
          stckBrwRto       — 주식차입비율
        """
        rows = await self._fetch_v2_pages(_SHORT_V2_INDST, bas_dt)
        result = []
        for r in rows:
            isin = str(r.get("isinCd", "")).strip()
            code: str | None = None
            if isin.startswith("KR") and len(isin) == 12:
                c = isin[3:9]
                code = c if c.isdigit() and c != "000000" else None
            elif len(isin) == 6 and isin.isdigit() and isin != "000000":
                code = isin
            result.append({
                "bas_dt":       r.get("basDt", bas_dt or ""),
                "isin_cd":      isin,
                "stock_code":   code,
                "stock_name":   r.get("isinCdNm", ""),
                "sic_cd":       r.get("sicCd", ""),
                "sic_nm":       r.get("sicNm", ""),
                "stck_lndn_bal":_safe(r.get("stckLndnBal", 0)),
                "stck_lndn_rto":_safe(r.get("stckLndnRto", 0)),
                "stck_brw_bal": _safe(r.get("stckBrwBal",  0)),
                "stck_brw_rto": _safe(r.get("stckBrwRto",  0)),
            })
        logger.info(f"[대차V2-SECTOR] {bas_dt}: {len(result)}건 수집")
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
                if r.status_code == 429:
                    logger.warning(f"[대차V2] {service} {bas_dt or ''} page={page} 429 — 잠시 대기 후 중단")
                    await asyncio.sleep(2.0)
                    break
                if r.status_code >= 400:
                    logger.warning(f"[대차V2] {service} {bas_dt or ''} page={page} HTTP {r.status_code}")
                    break
                try:
                    payload = r.json()
                except Exception as e:
                    logger.warning(f"[대차V2] {service} {bas_dt or ''} page={page} JSON 오류: {e}")
                    break
                body  = payload.get("response", {}).get("body", {})
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
                await asyncio.sleep(0.35)
        return all_items

    def _listed_common_codes_sync(self) -> set[str]:
        """코스피/코스닥 보통주 코드만 반환. ETF/ETN/지수성 코드는 제외."""
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute("""
                SELECT stock_code
                FROM stock_universe
                WHERE length(stock_code)=6
                  AND stock_code GLOB '[0-9]*'
                  AND market IN ('KOSPI', 'KOSDAQ', '유가증권', '코스닥')
                  AND COALESCE(stock_type, '') NOT IN ('ETF', 'ETF/ETN', 'ETN')
                  AND stock_name NOT LIKE '%ETF%'
                  AND stock_name NOT LIKE '%ETN%'
            """).fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()

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
        result = [{
            "bas_dt":            r.get("basDt", bas_dt or ""),
            "lnb_expr_itms_cnt": int(_safe(r.get("lnbExprItmsCnt", 0))),
            "lnb_ccl_stck_cnt":  _safe(r.get("lnbCclStckCnt",  0)),
            "lnb_ccl_amt":       _safe(r.get("lnbCclAmt",      0)),
            "lnb_rdpt_stck_cnt": _safe(r.get("lnbRdptStckCnt", 0)),
            "lnb_rdpt_amt":      _safe(r.get("lnbRdptAmt",     0)),
            "lnb_rman_stck_cnt": _safe(r.get("lnbRmanStckCnt", 0)),
            "lnb_bal":           _safe(r.get("lnbBal",         0)),
        } for r in rows]
        if bas_dt:
            result = [r for r in result if r.get("bas_dt") == bas_dt]
        return result

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 내외국인 잔고비교 (getNatiAndForeLendAndBorrBalaCo_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_foreign_balance(self, bas_dt: str | None = None) -> list[dict]:
        """일별 내외국인 대차잔고비교."""
        rows = await self._fetch_v2_pages(_SHORT_V2_FBAL, bas_dt)
        result = [{
            "bas_dt":            r.get("basDt", bas_dt or ""),
            "ntiv_brw_bal":      _safe(r.get("ntivBrwBal",    0)),
            "forg_brw_bal":      _safe(r.get("forgBrwBal",    0)),
            "brw_bal_forg_rto":  _safe(r.get("brwBalForgRto", 0)),
            "ntiv_lndn_bal":     _safe(r.get("ntivLndnBal",   0)),
            "forg_lndn_bal":     _safe(r.get("forgLndnBal",   0)),
            "lndn_bal_forg_rto": _safe(r.get("lndnBalForgRto",0)),
        } for r in rows]
        if bas_dt:
            result = [r for r in result if r.get("bas_dt") == bas_dt]
        return result

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 내외국인 거래량 (getNatiAndForeLendAndBorrTrad_V2)
    # ══════════════════════════════════════════════════════════

    async def fetch_short_foreign_trade(self, bas_dt: str | None = None) -> list[dict]:
        """일별 내외국인 대차거래량."""
        rows = await self._fetch_v2_pages(_SHORT_V2_FTRAD, bas_dt)
        result = [{
            "bas_dt":                 r.get("basDt", bas_dt or ""),
            "forg_lnb_ccl_stck_cnt":  _safe(r.get("forgLnbCclStckCnt", 0)),
            "forg_lnb_ccl_amt":       _safe(r.get("forgLnbCclAmt",     0)),
            "ntiv_lnb_ccl_stck_cnt":  _safe(r.get("ntivLnbCclStckCnt", 0)),
            "ntiv_lnb_ccl_amt":       _safe(r.get("ntivLnbCclAmt",     0)),
            "sum_lnb_ccl_stck_cnt":   _safe(r.get("sumLnbCclStckCnt",  0)),
            "sum_lnb_ccl_amt":        _safe(r.get("sumLnbCclAmt",      0)),
        } for r in rows]
        if bas_dt:
            result = [r for r in result if r.get("bas_dt") == bas_dt]
        return result

    # ══════════════════════════════════════════════════════════
    # V2 대차정보 — 날짜별 전체 대차 수집
    # ══════════════════════════════════════════════════════════

    async def collect_short_all_for_date(self, bas_dt: str) -> dict[str, int]:
        """대차 전 6종 순차 수집 후 DB 저장.
        수집: 종목순위 + 종목별현황 + 월별 + 업종별 + 내외국인잔고 + 내외국인거래량.
        공공데이터포털 V2는 짧은 시간 병렬 호출 시 429가 잦아 순차 수집한다.
        """
        async def _safe(name: str, fn):
            try:
                rows = await fn()
                await asyncio.sleep(0.8)
                return rows or []
            except Exception as e:
                logger.error(f"[대차수집] {name} 오류: {e}")
                return []

        rank   = await _safe("rank",    lambda: self.fetch_short_rank(bas_dt))
        svc    = await _safe("svc",     lambda: self.fetch_short_sell_v2(bas_dt))
        month  = await _safe("monthly", lambda: self.fetch_short_monthly(bas_dt))
        fbal   = await _safe("fbal",    lambda: self.fetch_short_foreign_balance(bas_dt))
        ftrad  = await _safe("ftrad",   lambda: self.fetch_short_foreign_trade(bas_dt))
        sector = await _safe("sector",  lambda: self.fetch_short_sector(bas_dt))

        saved = await asyncio.to_thread(
            self._bulk_upsert_short_sync,
            bas_dt,
            rank,
            svc,
            month,
            sector,
            fbal,
            ftrad,
        )
        for name, cnt in saved.items():
            logger.info(f"[대차수집] {name}: {cnt}건 ({bas_dt})")
        return saved

    def _bulk_upsert_short_sync(self, bas_dt: str, rank_rows, svc_rows, month_rows, sector_rows, fbal_rows, ftrad_rows) -> dict[str, int]:
        """대차 관련 4개 테이블 동기 upsert."""
        conn = sqlite3.connect(_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        saved: dict[str, int] = {}
        try:
            listed_codes = self._listed_common_codes_sync()
            rank_rows = [r for r in rank_rows if r.get("stock_code") in listed_codes]
            svc_rows = [r for r in svc_rows if r.get("stock_code") in listed_codes]
            sector_rows = [r for r in sector_rows if r.get("stock_code") in listed_codes]

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

            # 종목별대차현황 → short_sell_daily — UNIQUE(bas_dt, stock_code)
            if svc_rows:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS short_sell_daily (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        bas_dt          TEXT NOT NULL,
                        stock_code      TEXT NOT NULL,
                        stock_name      TEXT,
                        short_qty       REAL DEFAULT 0,
                        short_rdpt_qty  REAL DEFAULT 0,
                        borrow_bal_qty  REAL DEFAULT 0,
                        short_amt       REAL,
                        borrow_bal_amt  REAL,
                        borrow_bal_pct  REAL,
                        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(bas_dt, stock_code)
                    )
                """)
                # short_rdpt_qty 컬럼이 없는 구버전 테이블 대비
                try:
                    conn.execute("ALTER TABLE short_sell_daily ADD COLUMN short_rdpt_qty REAL DEFAULT 0")
                except Exception:
                    pass
                conn.executemany("""
                    INSERT INTO short_sell_daily
                      (bas_dt, stock_code, stock_name, short_qty, short_rdpt_qty,
                       borrow_bal_qty, short_amt, borrow_bal_amt, borrow_bal_pct)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt, stock_code) DO UPDATE SET
                      stock_name     = excluded.stock_name,
                      short_qty      = excluded.short_qty,
                      short_rdpt_qty = excluded.short_rdpt_qty,
                      borrow_bal_qty = excluded.borrow_bal_qty
                """, [(r["bas_dt"], r["stock_code"], r["stock_name"], r["short_qty"],
                       r.get("short_rdpt_qty", 0), r["borrow_bal_qty"],
                       r["short_amt"], r["borrow_bal_amt"], r["borrow_bal_pct"])
                      for r in svc_rows])
                # V2 종목별대차현황은 잔고수량만 제공하고 금액/비율은 비어 있다.
                # 같은 기준일의 short_rank_daily.lnb_bal(대차잔고금액)과 최신 상장주식수로 보강한다.
                conn.execute("""
                    UPDATE short_sell_daily AS d
                    SET borrow_bal_amt = COALESCE((
                            SELECT MAX(r.lnb_bal)
                            FROM short_rank_daily r
                            WHERE r.bas_dt = d.bas_dt
                              AND r.stock_code = d.stock_code
                              AND r.lnb_bal > 0
                        ), d.borrow_bal_amt),
                        borrow_bal_pct = COALESCE((
                            SELECT ROUND(d.borrow_bal_qty * 100.0 / x.shares_issued, 6)
                            FROM (
                                SELECT shares_issued, snapshot_date AS asof_date, 1 AS src_priority
                                FROM krx_security_share_snapshot
                                WHERE stock_code = d.stock_code AND shares_issued > 0
                                UNION ALL
                                SELECT shares_issued, snapshot_date AS asof_date, 2 AS src_priority
                                FROM stock_base_info_history
                                WHERE stock_code = d.stock_code AND shares_issued > 0
                                UNION ALL
                                SELECT shares_issued, base_date AS asof_date, 3 AS src_priority
                                FROM stock_universe
                                WHERE stock_code = d.stock_code AND shares_issued > 0
                            ) x
                            ORDER BY x.asof_date DESC, x.src_priority ASC
                            LIMIT 1
                        ), d.borrow_bal_pct)
                    WHERE d.bas_dt = ?
                      AND (
                           d.borrow_bal_amt IS NULL OR d.borrow_bal_amt <= 0
                        OR d.borrow_bal_pct IS NULL OR d.borrow_bal_pct <= 0
                      )
                """, (bas_dt,))
                saved["short_sell_daily"] = len(svc_rows)

            # 업종별참여현황 → short_sector_daily — UNIQUE(bas_dt, isin_cd)
            if sector_rows:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS short_sector_daily (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        bas_dt        TEXT NOT NULL,
                        isin_cd       TEXT NOT NULL,
                        stock_code    TEXT,
                        stock_name    TEXT,
                        sic_cd        TEXT,
                        sic_nm        TEXT,
                        stck_lndn_bal REAL DEFAULT 0,
                        stck_lndn_rto REAL DEFAULT 0,
                        stck_brw_bal  REAL DEFAULT 0,
                        stck_brw_rto  REAL DEFAULT 0,
                        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(bas_dt, isin_cd)
                    )
                """)
                conn.executemany("""
                    INSERT INTO short_sector_daily
                      (bas_dt, isin_cd, stock_code, stock_name, sic_cd, sic_nm,
                       stck_lndn_bal, stck_lndn_rto, stck_brw_bal, stck_brw_rto)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(bas_dt, isin_cd) DO UPDATE SET
                      stock_code    = excluded.stock_code,
                      stck_lndn_bal = excluded.stck_lndn_bal,
                      stck_lndn_rto = excluded.stck_lndn_rto,
                      stck_brw_bal  = excluded.stck_brw_bal,
                      stck_brw_rto  = excluded.stck_brw_rto
                """, [(r["bas_dt"], r["isin_cd"], r["stock_code"], r["stock_name"],
                       r["sic_cd"], r["sic_nm"], r["stck_lndn_bal"], r["stck_lndn_rto"],
                       r["stck_brw_bal"], r["stck_brw_rto"]) for r in sector_rows])
                saved["short_sector_daily"] = len(sector_rows)

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
        2종 데이터(가격/대차)를 asyncio.gather로 병렬 수집 후 SQLite에 bulk upsert.
        반환: {dataset_name: rows_saved}

        ⚠️ 2026-08-24: investor_trading_daily(getInvstByTrdrStkInfo)는 2026-07-10 이후,
        foreign_holding_daily(외국인보유)는 2026-06-08 이후 매일 0건만 반환하는
        죽은 API 호출로 확인(공공데이터포털 서비스 폐지/중단 추정) — 매일 무의미한 API
        왕복만 반복하고 있어 제거. investor는 kiwoom_investor_daily, foreign은
        kiwoom_foreign_flow가 이미 더 신선하고 넓은 커버리지로 대체 중.
        listed_company_info는 처음부터 0행(수집 로직 자체가 유효 응답을 받은 적 없음)이라
        함께 제거. 세 테이블 모두 스키마/기존 행은 그대로 보존(과거 데이터 조회 코드 영향 없음).
        """
        price_task    = self.fetch_stock_prices(bas_dt)
        short_task    = self.fetch_short_sell(bas_dt)

        price, short = await asyncio.gather(
            price_task, short_task,
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
