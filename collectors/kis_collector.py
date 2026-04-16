"""
collectors/kis_collector.py — 한국투자증권 Open API 수집기

담당 데이터:
  · 종목 일별 OHLCV (FHKST01010400 — 최대 100일치 1회 호출)
  · 종목 투자자별 수급 (FHKST01010900 — 최근 30거래일)
  · 종목 현재가 (FHKST01010100)
  · 지수 현재가 (FHPUP02100000)
  · 계좌 잔고 / 당일 체결내역

레이트리밋: 1초당 1회 (config.KIS_RATE_LIMIT_SECS)
토큰 관리: 기존 KISClient 위임 (kis_client.py) — 토큰 로직은 그대로 유지
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import config
from collectors.base import BaseCollector

logger = logging.getLogger(__name__)


# KIS TR_ID 상수 (하드코딩 방지)
_TR = {
    "PRICE":    "FHKST01010100",   # 주식 현재가
    "PERIOD":   "FHKST01010400",   # 기간별 일봉 (OHLCV)
    "INVESTOR": "FHKST01010900",   # 투자자별 매매동향
    "INDEX":    "FHPUP02100000",   # 지수 현재가
    "BALANCE":  "TTTC8434R",       # 주식 잔고 조회 (실전)
    "ORDERS":   "TTTC8001R",       # 당일 체결 조회
}

# 지수 코드 매핑 (하드코딩 방지 — config 우선)
_INDEX_CODES: dict[str, str] = {
    "KOSPI":  getattr(config, "KIS_INDEX_KOSPI",  "0001"),
    "KOSDAQ": getattr(config, "KIS_INDEX_KOSDAQ", "1001"),
}


def _date_fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _parse_date(s: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD'"""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) == 8 else s


class KISCollector(BaseCollector):
    """한국투자증권 API 수집기."""

    def __init__(self, kis_client=None):
        super().__init__(
            rate_limit_secs = config.KIS_RATE_LIMIT_SECS,
            name            = "KIS",
        )
        # 기존 KISClient 인스턴스 (토큰 관리 위임)
        self._kis = kis_client

    # ── 인증 헤더 생성 ─────────────────────────────────────────

    def _auth_headers(self, tr_id: str) -> dict:
        token = self._kis.get_token() if self._kis else None
        if not token:
            raise RuntimeError("KIS 토큰 없음 — get_token() 실패")
        return {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {token}",
            "appkey":        config.KIS_APP_KEY,
            "appsecret":     config.KIS_APP_SECRET,
            "tr_id":         tr_id,
        }

    # ══════════════════════════════════════════════════════════
    # 1) 기간 OHLCV (핵심 — 1회 호출로 최대 100일치)
    # ══════════════════════════════════════════════════════════

    async def fetch_period_ohlcv(
        self,
        stock_code:  str,
        start_date:  str,        # "YYYYMMDD"
        end_date:    str | None = None,
        adj_price:   str = "1",  # 수정주가: "1"=수정, "0"=원주가
    ) -> list[dict]:
        """
        FHKST01010400 — 국내 주식 기간별 시세.
        한 번 호출로 최대 100 거래일치 OHLCV 반환.
        100일 초과 기간은 페이지네이션하여 자동 합산.

        반환: [{date, open, high, low, close, volume, inst_net_buy, frn_net_buy}, ...]
        """
        if not end_date:
            end_date = _date_fmt(datetime.now())

        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        result: list[dict] = []

        # KIS는 최대 100건 — 기간이 길면 분할 호출
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt   = datetime.strptime(end_date,   "%Y%m%d")
        chunk_days = 100

        cursor_end = end_dt
        while cursor_end >= start_dt:
            cursor_start = max(start_dt, cursor_end - timedelta(days=chunk_days - 1))
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         stock_code,
                "FID_INPUT_DATE_1":       _date_fmt(cursor_start),
                "FID_INPUT_DATE_2":       _date_fmt(cursor_end),
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        adj_price,
            }
            try:
                data = await self._get(url, params=params, headers=self._auth_headers(_TR["PERIOD"]))
            except RuntimeError as e:
                logger.error(f"[KIS] {stock_code} OHLCV 헤더 오류: {e}")
                break

            if not data or data.get("rt_cd") != "0":
                logger.debug(f"[KIS] {stock_code} OHLCV 없음: {data and data.get('msg1','')}")
                break

            rows = data.get("output2") or data.get("output") or []
            for r in rows:
                d = r.get("stck_bsop_date", "")
                if not d:
                    continue
                result.append({
                    "date":         _parse_date(d),
                    "open":         float(r.get("stck_oprc",  0) or 0),
                    "high":         float(r.get("stck_hgpr",  0) or 0),
                    "low":          float(r.get("stck_lwpr",  0) or 0),
                    "close":        float(r.get("stck_clpr",  0) or 0),
                    "volume":       float(r.get("acml_vol",   0) or 0),
                    "inst_net_buy": 0.0,
                    "frn_net_buy":  0.0,
                })

            if cursor_start <= start_dt:
                break
            cursor_end = cursor_start - timedelta(days=1)

        return result

    # ══════════════════════════════════════════════════════════
    # 2) 투자자별 수급 (30거래일 bulk)
    # ══════════════════════════════════════════════════════════

    async def fetch_investor_trends(self, stock_code: str) -> list[dict]:
        """
        FHKST01010900 — 최근 30거래일 기관/외국인/개인 순매수.
        반환: [{date, inst_net_buy, frn_net_buy, ind_net_buy,
                inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt}, ...]
        수량: 주(株) / 금액: 백만원
        """
        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        try:
            data = await self._get(url, params=params, headers=self._auth_headers(_TR["INVESTOR"]))
        except RuntimeError as e:
            logger.error(f"[KIS] {stock_code} 수급 헤더 오류: {e}")
            return []

        if not data or data.get("rt_cd") != "0":
            return []

        result = []
        for r in (data.get("output") or []):
            d = r.get("stck_bsop_date", "")
            if not d:
                continue
            result.append({
                "date":             _parse_date(d),
                "inst_net_buy":     float(r.get("orgn_ntby_qty",      0) or 0),
                "frn_net_buy":      float(r.get("frgn_ntby_qty",      0) or 0),
                "ind_net_buy":      float(r.get("prsn_ntby_qty",      0) or 0),
                "inst_net_buy_amt": float(r.get("orgn_ntby_tr_pbmn",  0) or 0),
                "frn_net_buy_amt":  float(r.get("frgn_ntby_tr_pbmn",  0) or 0),
                "ind_net_buy_amt":  float(r.get("prsn_ntby_tr_pbmn",  0) or 0),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 3) 종목 현재가 (단건)
    # ══════════════════════════════════════════════════════════

    async def fetch_current_price(self, stock_code: str) -> dict | None:
        """
        FHKST01010100 — 실시간 현재가.
        반환: {date, open, high, low, close, volume}
        """
        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        try:
            data = await self._get(url, params=params, headers=self._auth_headers(_TR["PRICE"]))
        except RuntimeError as e:
            logger.error(f"[KIS] {stock_code} 현재가 헤더 오류: {e}")
            return None

        if not data or data.get("rt_cd") != "0":
            return None

        o = data.get("output", {})
        return {
            "date":   datetime.now().strftime("%Y-%m-%d"),
            "open":   float(o.get("stck_oprc", 0) or 0),
            "high":   float(o.get("stck_hgpr", 0) or 0),
            "low":    float(o.get("stck_lwpr", 0) or 0),
            "close":  float(o.get("stck_prpr", 0) or 0),
            "volume": float(o.get("acml_vol",  0) or 0),
        }

    # ══════════════════════════════════════════════════════════
    # 4) 다종목 현재가 (rate-limit 준수 직렬 실행)
    # ══════════════════════════════════════════════════════════

    async def fetch_current_prices_bulk(
        self,
        stock_codes: list[str],
    ) -> dict[str, dict]:
        """
        여러 종목 현재가를 KIS 1req/sec 제한을 지키며 순차 조회.
        반환: {stock_code: {date, open, high, low, close, volume}}
        """
        result: dict[str, dict] = {}
        for code in stock_codes:
            try:
                data = await self.fetch_current_price(code)
                if isinstance(data, dict) and data:
                    result[code] = data
            except Exception:
                pass
            await asyncio.sleep(config.KIS_RATE_LIMIT_SECS)
        return result

    # ══════════════════════════════════════════════════════════
    # 5) 지수 현재가
    # ══════════════════════════════════════════════════════════

    async def fetch_index_price(
        self,
        market: str = "KOSPI",     # "KOSPI" | "KOSDAQ"
    ) -> dict | None:
        """
        FHPUP02100000 — 지수 현재가.
        반환: {value, open, high, low, change, change_rate, volume, date}
        """
        index_code = _INDEX_CODES.get(market.upper(), "0001")
        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD":         index_code,
        }
        try:
            data = await self._get(url, params=params, headers=self._auth_headers(_TR["INDEX"]))
        except RuntimeError as e:
            logger.error(f"[KIS] {market} 지수 헤더 오류: {e}")
            return None

        if not data or data.get("rt_cd") != "0":
            return None

        o = data.get("output", {})
        return {
            "value":       float(o.get("bstp_nmix_prpr",     0) or 0),
            "open":        float(o.get("bstp_nmix_oprc",     0) or 0),
            "high":        float(o.get("bstp_nmix_hgpr",     0) or 0),
            "low":         float(o.get("bstp_nmix_lwpr",     0) or 0),
            "change":      float(o.get("bstp_nmix_prdy_vrss",0) or 0),
            "change_rate": float(o.get("prdy_ctrt",          0) or 0),
            "volume":      float(o.get("acml_vol",           0) or 0),
            "date":        datetime.now().strftime("%Y-%m-%d"),
        }

    # ══════════════════════════════════════════════════════════
    # 6) 계좌 잔고
    # ══════════════════════════════════════════════════════════

    async def fetch_account_balance(self) -> list[dict]:
        """
        TTTC8434R — 주식 잔고 조회.
        반환: [{stock_code, stock_name, quantity, avg_price, current_price,
                eval_amount, profit_loss, profit_rate}, ...]
        """
        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO":                  config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD":          config.KIS_ACCOUNT_PROD,
            "AFHR_FLPR_YN":          "N",
            "OFL_YN":                "",
            "INQR_DVSN":             "02",
            "UNPR_DVSN":             "01",
            "FUND_STTL_ICLD_YN":     "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN":             "01",
            "CTX_AREA_FK100":        "",
            "CTX_AREA_NK100":        "",
        }
        try:
            data = await self._get(url, params=params, headers=self._auth_headers(_TR["BALANCE"]))
        except RuntimeError as e:
            logger.error(f"[KIS] 잔고 헤더 오류: {e}")
            return []

        if not data or data.get("rt_cd") != "0":
            return []

        result = []
        for r in (data.get("output1") or []):
            qty = int(r.get("hldg_qty", 0) or 0)
            if qty <= 0:
                continue
            result.append({
                "stock_code":    r.get("pdno", ""),
                "stock_name":    r.get("prdt_name", ""),
                "quantity":      qty,
                "avg_price":     float(r.get("pchs_avg_pric",  0) or 0),
                "current_price": float(r.get("prpr",           0) or 0),
                "eval_amount":   float(r.get("evlu_amt",       0) or 0),
                "profit_loss":   float(r.get("evlu_pfls_amt",  0) or 0),
                "profit_rate":   float(r.get("evlu_pfls_rt",   0) or 0),
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 7) 당일 체결 내역
    # ══════════════════════════════════════════════════════════

    async def fetch_today_executions(self) -> list[dict]:
        """
        TTTC8001R — 당일 체결 조회.
        반환: [{time, stock_code, stock_name, side, quantity, price, amount}, ...]
        """
        url    = f"{config.KIS_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        params = {
            "CANO":          config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD":  config.KIS_ACCOUNT_PROD,
            "INQR_STRT_DT":  datetime.now().strftime("%Y%m%d"),
            "INQR_END_DT":   datetime.now().strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN":     "00",
            "PDNO":          "",
            "CCLD_DVSN":     "01",
            "ORD_GNO_BRNO":  "",
            "ODNO":          "",
            "INQR_DVSN_3":   "00",
            "INQR_DVSN_1":   "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            data = await self._get(url, params=params, headers=self._auth_headers(_TR["ORDERS"]))
        except RuntimeError as e:
            logger.error(f"[KIS] 체결 헤더 오류: {e}")
            return []

        if not data or data.get("rt_cd") != "0":
            return []

        result = []
        for r in (data.get("output1") or []):
            qty = int(r.get("tot_ccld_qty", 0) or 0)
            if qty <= 0:
                continue
            result.append({
                "time":        r.get("ord_tmd", ""),
                "stock_code":  r.get("pdno", ""),
                "stock_name":  r.get("prdt_name", ""),
                "side":        "buy" if r.get("sll_buy_dvsn_cd") == "02" else "sell",
                "quantity":    qty,
                "price":       float(r.get("avg_prvs", 0) or 0),
                "amount":      float(r.get("tot_ccld_amt", 0) or 0),
            })
        return result
