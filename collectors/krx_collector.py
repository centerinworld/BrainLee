"""
collectors/krx_collector.py — 한국거래소(KRX) / K-mydata 수집기

담당 데이터:
  · KOSPI/KOSDAQ 실시간 지수
  · 지수 투자자별 매매동향 (기관/외국인/개인)
  · 지수 히스토리 (3년치)
  · 네이버 금융 수급 (KRX/K-mydata fallback)

우선순위: K-mydata → KRX 데이터포털 → 네이버 스크래핑
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

import config
from collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# ── URL 상수 (config 우선) ─────────────────────────────────────
_KRX_DATA_URL  = getattr(config, "KRX_DATA_URL",  "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")
_KMYDATA_BASE  = getattr(config, "KMYDATA_BASE",  "https://oap.k-mydata.org")
_NAVER_SUPPLY  = "https://finance.naver.com/sise/sise_index.naver?code={code}"
_NAVER_IFRAME  = "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=&sosok={sosok}"

# 지수 코드 매핑
_MARKET_CODES = {
    "kospi":  {"issuecode": "K1", "mkt_id": "STK", "pykrx": "KOSPI"},
    "kosdaq": {"issuecode": "K2", "mkt_id": "KSQ", "pykrx": "KOSDAQ"},
}


def _clean_num(s) -> float:
    """콤마/공백/+/▲/▼ 제거 후 float 변환. 음수 접두사(- 또는 ▼) 보존."""
    s = str(s).strip()
    neg = s.startswith("-") or "▼" in s
    nums = re.sub(r"[^0-9.]", "", s)
    if not nums:
        return 0.0
    return -float(nums) if neg else float(nums)


class KRXCollector(BaseCollector):
    """KRX / K-mydata 수집기."""

    def __init__(self, api_key: str = ""):
        super().__init__(
            rate_limit_secs = 0.2,   # KRX 데이터포털 — 공식 제한 없음, 예의상 0.2초
            name            = "KRX",
        )
        self._api_key  = api_key or getattr(config, "KRX_API_KEY", "")
        self._kmydata  = _KMYDATA_BASE
        self._web_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Referer":    "http://data.krx.co.kr/",
            "Accept":     "application/json, text/javascript, */*",
        }
        # 1분 메모리 캐시
        self._cache: dict[str, tuple[float, object]] = {}

    def _from_cache(self, key: str, ttl: int = 60) -> object | None:
        entry = self._cache.get(key)
        if entry and (time.monotonic() - entry[0]) < ttl:
            return entry[1]
        return None

    def _to_cache(self, key: str, val: object) -> None:
        self._cache[key] = (time.monotonic(), val)

    # ══════════════════════════════════════════════════════════
    # 1) 지수 실시간
    # ══════════════════════════════════════════════════════════

    async def fetch_index_realtime(self, market: str = "kospi") -> dict | None:
        """KOSPI/KOSDAQ 실시간 지수. K-mydata → KRX 포털 순."""
        key = f"idx_{market}"
        cached = self._from_cache(key)
        if cached:
            return cached

        result = await self._fetch_index_kmydata(market)
        if not result:
            result = await self._fetch_index_krx_portal(market)
        if result:
            self._to_cache(key, result)
        return result

    async def _fetch_index_kmydata(self, market: str) -> dict | None:
        if not self._api_key:
            return None
        mc   = _MARKET_CODES.get(market.lower(), _MARKET_CODES["kospi"])
        path = f"/v3/market/realtime/index/{market.lower()}/{mc['issuecode']}/marketcap"
        data = await self._get(
            f"{self._kmydata}{path}",
            headers={"apikey": self._api_key},
        )
        if data and data.get("result"):
            r = data["result"]
            return {
                "market":      market.upper(),
                "value":       float(r.get("clpr",       r.get("mktcap", 0))),
                "volume":      float(r.get("accTrdvol",  0)),
                "trade_value": float(r.get("accTrdval",  0)),
                "mktcap":      float(r.get("mktcap",     0)),
            }
        return None

    async def _fetch_index_krx_portal(self, market: str) -> dict | None:
        mc    = _MARKET_CODES.get(market.lower(), _MARKET_CODES["kospi"])
        today = date.today().strftime("%Y%m%d")
        data  = await self._post(
            _KRX_DATA_URL,
            data={
                "bld":         "dbms/MDC/STAT/standard/MDCSTAT00101",
                "mktId":       mc["mkt_id"],
                "trdDd":       today,
                "share":       "1",
                "money":       "1",
                "csvxls_isNo": "false",
            },
            headers=self._web_headers,
        )
        if data and data.get("OutBlock_1"):
            r = data["OutBlock_1"][0]
            return {
                "market":      market.upper(),
                "value":       _clean_num(r.get("CLSPRC_IDX",  0)),
                "change":      _clean_num(r.get("PRV_DD_CMPR", 0)),
                "change_rate": _clean_num(r.get("FLUC_RT",     0)),
                "volume":      _clean_num(r.get("ACC_TRDVOL",  0)),
            }
        return None

    # ══════════════════════════════════════════════════════════
    # 2) 지수 투자자별 수급
    # ══════════════════════════════════════════════════════════

    async def fetch_index_investor(self, market: str = "kospi") -> dict | None:
        """기관/외국인/개인 순매수 (억원). K-mydata → KRX 포털 → 네이버 순."""
        key = f"inv_{market}"
        cached = self._from_cache(key, ttl=300)
        if cached:
            return cached

        result = (
            await self._fetch_investor_kmydata(market)
            or await self._fetch_investor_krx_portal(market)
            or await self._fetch_investor_naver(market)
        )
        if result:
            self._to_cache(key, result)
        return result

    async def _fetch_investor_kmydata(self, market: str) -> dict | None:
        if not self._api_key:
            return None
        mc   = _MARKET_CODES.get(market.lower(), _MARKET_CODES["kospi"])
        path = f"/v3/market/realtime/index/{market.lower()}/{mc['issuecode']}/investor"
        data = await self._get(
            f"{self._kmydata}{path}",
            headers={"apikey": self._api_key},
        )
        if data and data.get("result"):
            r = data["result"]
            frn  = float(r.get("frnNetBuySellAmt", r.get("frnIvsrNetAmt", 0)))
            inst = float(r.get("orgNetBuySellAmt", r.get("orgIvsrNetAmt", 0)))
            ind  = float(r.get("indNetBuySellAmt", r.get("indIvsrNetAmt", 0)))
            if frn != 0 or inst != 0:
                return {"frn": frn, "inst": inst, "ind": ind}
        return None

    async def _fetch_investor_krx_portal(self, market: str) -> dict | None:
        mc    = _MARKET_CODES.get(market.lower(), _MARKET_CODES["kospi"])
        today = date.today().strftime("%Y%m%d")
        data  = await self._post(
            _KRX_DATA_URL,
            data={
                "bld":       "dbms/MDC/STAT/standard/MDCSTAT02201",
                "mktId":     mc["mkt_id"],
                "invstTpCd": "ALL",
                "strtDd":    today,
                "endDd":     today,
                "share":     "1",
                "money":     "3",
            },
            headers=self._web_headers,
        )
        if not data:
            return None
        rows   = data.get("output") or data.get("OutBlock_1") or []
        result = {"frn": 0.0, "inst": 0.0, "ind": 0.0}
        for row in rows:
            nm  = str(row.get("INVST_TP_NM", row.get("invst_tp_nm", "")))
            net = _clean_num(row.get("NET_BUY_AMT", row.get("net_buy_amt", 0)))
            if "외국인" in nm:
                result["frn"] = net
            elif "기관" in nm and "합계" in nm:
                result["inst"] = net
            elif "개인" in nm:
                result["ind"] = net
        if result["frn"] != 0 or result["inst"] != 0:
            return result
        return None

    async def _fetch_investor_naver(self, market: str) -> dict | None:
        """네이버 금융 스크래핑 (최후 fallback)."""
        code = "KOSPI" if "kospi" in market.lower() else "KOSDAQ"
        try:
            resp = await self._get(
                _NAVER_SUPPLY.format(code=code),
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer":    "https://finance.naver.com/",
                    "Accept-Language": "ko-KR,ko;q=0.9",
                },
            )
            # _get returns parsed JSON; naver returns HTML → use raw
        except Exception:
            pass

        # 네이버는 HTML이므로 동기 requests + BeautifulSoup 사용
        return await asyncio.to_thread(self._scrape_naver_sync, code)

    def _scrape_naver_sync(self, code: str) -> dict | None:
        """동기 스레드에서 실행 — asyncio.to_thread()로 호출."""
        import requests as _req
        try:
            from bs4 import BeautifulSoup as _BS
        except ImportError:
            logger.debug("[네이버수급] beautifulsoup4 미설치")
            return None

        headers = {
            "User-Agent":     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Referer":        "https://finance.naver.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        for attempt in range(2):
            try:
                if attempt == 0:
                    url = _NAVER_SUPPLY.format(code=code)
                else:
                    sosok = "0" if code == "KOSPI" else "1"
                    url   = _NAVER_IFRAME.format(sosok=sosok)

                res = _req.get(url, headers=headers, timeout=10)
                res.encoding = "euc-kr"
                soup = _BS(res.text, "html.parser")
                lines = [l.strip() for l in soup.get_text().split("\n") if l.strip()]

                result = {"inst": 0.0, "frn": 0.0, "ind": 0.0}
                for line in lines:
                    if line.startswith("기관") and "억" in line:
                        result["inst"] = _clean_num(line[2:])
                    elif line.startswith("외국인") and "억" in line:
                        result["frn"] = _clean_num(line[3:])
                    elif line.startswith("개인") and "억" in line:
                        result["ind"] = _clean_num(line[2:])

                if result["frn"] != 0 or result["inst"] != 0:
                    return result
            except Exception as e:
                logger.debug(f"[네이버수급] {code} attempt={attempt}: {e}")

        return None

    # ══════════════════════════════════════════════════════════
    # 3) 지수 히스토리 (KRX 포털)
    # ══════════════════════════════════════════════════════════

    async def fetch_index_history(
        self,
        market:     str = "kospi",
        start_date: str | None = None,
        end_date:   str | None = None,
    ) -> list[dict]:
        """
        KRX 포털에서 지수 일별 OHLCV.
        반환: [{date, open, high, low, close, volume}, ...]
        """
        mc    = _MARKET_CODES.get(market.lower(), _MARKET_CODES["kospi"])
        today = date.today()
        if not end_date:
            end_date = today.strftime("%Y%m%d")
        if not start_date:
            start_date = (today - timedelta(days=1095)).strftime("%Y%m%d")  # 3년

        data = await self._post(
            _KRX_DATA_URL,
            data={
                "bld":         "dbms/MDC/STAT/standard/MDCSTAT00301",
                "mktId":       mc["mkt_id"],
                "strtDd":      start_date,
                "endDd":       end_date,
                "csvxls_isNo": "false",
            },
            headers=self._web_headers,
        )
        if not data:
            return []

        rows   = data.get("output") or data.get("OutBlock_1") or []
        result = []
        for r in rows:
            d = str(r.get("TRD_DD", r.get("trd_dd", ""))).replace("/", "")
            if len(d) != 8:
                continue
            result.append({
                "date":   f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                "open":   _clean_num(r.get("OPNPRC_IDX",  r.get("MKT_OPNPRC",  0))),
                "high":   _clean_num(r.get("HGPRC_IDX",   r.get("MKT_HGPRC",   0))),
                "low":    _clean_num(r.get("LWPRC_IDX",   r.get("MKT_LWPRC",   0))),
                "close":  _clean_num(r.get("CLSPRC_IDX",  r.get("MKT_CLSPRC",  0))),
                "volume": _clean_num(r.get("ACC_TRDVOL",  r.get("ACC_TRDVOL",  0))),
            })

        result.sort(key=lambda x: x["date"])
        return result

    # ══════════════════════════════════════════════════════════
    # 4) pykrx 수급 (동기 — asyncio.to_thread로 호출)
    # ══════════════════════════════════════════════════════════

    async def fetch_pykrx_investor(self, market: str = "KOSPI") -> dict | None:
        """pykrx로 당일 투자자별 순매수 (억원). 동기 라이브러리를 스레드에서 실행."""
        return await asyncio.to_thread(self._pykrx_investor_sync, market)

    def _pykrx_investor_sync(self, market: str) -> dict | None:
        try:
            from pykrx import stock as _pykrx
            today = datetime.now().strftime("%Y%m%d")
            df    = _pykrx.get_market_net_purchases_of_business_day(today, today, market)
            if df is None or df.empty:
                return None

            # 순매수거래대금 컬럼 자동 탐색
            net_col = next(
                (c for c in df.columns if "순매수" in str(c) and "거래대금" in str(c)),
                df.columns[-1],
            )
            result = {"frn": 0.0, "inst": 0.0, "ind": 0.0}
            for idx_val in df.index:
                idx_str = str(idx_val)
                val     = float(df.loc[idx_val, net_col]) / 100  # 백만→억원
                if "기관" in idx_str and "합계" in idx_str:
                    result["inst"] = val
                elif "외국인" in idx_str:
                    result["frn"] = val
                elif "개인" in idx_str:
                    result["ind"] = val

            if result["frn"] != 0 or result["inst"] != 0:
                return result
        except Exception as e:
            logger.debug(f"[pykrx] {market} 수급 오류: {e}")
        return None
