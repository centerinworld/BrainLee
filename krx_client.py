"""
krx_client.py — 한국거래소(KRX) Open API 클라이언트

제공 기능:
  · 코스피/코스닥 실시간 지수 (현재가 + 등락률)
  · 코스피/코스닥 투자자별 매매동향 (기관/외국인/개인)
  · 종목 시장구분 + 시가총액 + 시총순위
  · 코스피/코스닥 일별 시세 (3년치 히스토리)

KRX Open API 기본 정보:
  Base URL: https://openapi.koreainvestment.com:9443  (KIS와 동일 게이트웨이)
  또는 k-mydata: https://api.k-mydata.org

실제 KRX 데이터포털 (data.krx.co.kr) REST API:
  - 로그인 없이 POST로 데이터 조회
  - 헤더: apikey
"""

import requests
import time
import logging
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)

# KRX 데이터포털 기본 URL
KRX_DATA_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

# K-mydata Open API URL (신청한 API 키용)
KMYDATA_URL = "https://api.k-mydata.org"


class KRXClient:
    """
    KRX + K-mydata Open API 클라이언트.
    config.py의 KRX_API_KEY 사용.
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Referer":    "http://data.krx.co.kr/",
            "Accept":     "application/json, text/javascript, */*",
        })
        # K-mydata 운영/테스트 URL 자동 탐색
        self._kmydata_base = self._find_kmydata_base()
        self._cache = {}
        self._cache_time = {}
        self.CACHE_TTL = 60  # 1분 캐시

    def _cached(self, key: str, fn, ttl: int = None):
        """캐시 래퍼."""
        t = ttl or self.CACHE_TTL
        if key in self._cache and time.time() - self._cache_time.get(key, 0) < t:
            return self._cache[key]
        result = fn()
        if result is not None:
            self._cache[key] = result
            self._cache_time[key] = time.time()
        return result

    # ──────────────────────────────────────────────────────────────
    # K-mydata API (apikey 방식)
    # ──────────────────────────────────────────────────────────────

    def _find_kmydata_base(self) -> str:
        """K-mydata 실제 작동하는 Base URL 탐색."""
        if not self.api_key:
            return KMYDATA_URL
        candidates = [
            "https://oap.k-mydata.org",
            "https://api.k-mydata.org",
            "https://testoap.k-mydata.org",
        ]
        for base in candidates:
            try:
                r = requests.get(
                    f"{base}/v3/market/realtime/index/kospi/K1/marketcap",
                    headers={"apikey": self.api_key},
                    timeout=5,
                )
                if r.status_code == 200:
                    logger.info(f"[KRX] K-mydata base URL: {base}")
                    return base
                elif r.status_code == 401:
                    logger.warning(f"[KRX] {base}: 401 인증실패")
                elif r.status_code != 404:
                    logger.debug(f"[KRX] {base}: {r.status_code}")
            except Exception:
                continue
        logger.warning("[KRX] K-mydata 모든 URL 실패 → Yahoo fallback 사용")
        return KMYDATA_URL

    def _kmydata_get(self, path: str, timeout: int = 8) -> dict | None:
        """K-mydata API GET 호출."""
        if not self.api_key:
            return None
        try:
            r = self.session.get(
                f"{self._kmydata_base}{path}",
                headers={"apikey": self.api_key},
                timeout=timeout,
            )
            if r.status_code == 200:
                return r.json()
            logger.debug(f"[KRX] {path}: {r.status_code} {r.text[:80]}")
        except Exception as e:
            logger.debug(f"[KRX] {path}: {e}")
        return None

    def get_index_realtime(self, market: str = "kospi") -> dict | None:
        """
        실시간 지수 조회.
        market: "kospi" | "kosdaq"
        issuecode: K1(KOSPI) | K2(KOSDAQ)

        반환: { value, change, change_rate, volume, mktcap }
        """
        issuecode = "K1" if market.lower() == "kospi" else "K2"

        def _fetch():
            # 1순위: K-mydata 시가총액 API
            data = self._kmydata_get(
                f"/v3/market/realtime/index/{market}/{issuecode}/marketcap"
            )
            if data and data.get("result"):
                r = data["result"]
                return {
                    "market":      market.upper(),
                    "issuecode":   issuecode,
                    "mktcap":      r.get("mktcap", 0),
                    "volume":      r.get("accTrdvol", 0),
                    "trade_value": r.get("accTrdval", 0),
                }

            # 2순위: KRX 데이터포털
            return self._get_index_from_krx_portal(market)

        return self._cached(f"index_{market}", _fetch, ttl=60)

    def _get_index_from_krx_portal(self, market: str) -> dict | None:
        """KRX 데이터포털에서 지수 조회 (로그인 불필요)."""
        try:
            mkt_id = "STK" if market.lower() == "kospi" else "KSQ"
            today  = date.today().strftime("%Y%m%d")
            r = self.session.post(
                KRX_DATA_URL,
                data={
                    "bld":      "dbms/MDC/STAT/standard/MDCSTAT00101",
                    "mktId":    mkt_id,
                    "trdDd":    today,
                    "share":    "1",
                    "money":    "1",
                    "csvxls_isNo": "false",
                },
                timeout=8,
            )
            if r.ok:
                j = r.json()
                if j.get("OutBlock_1"):
                    row = j["OutBlock_1"][0]
                    return {
                        "market":      market.upper(),
                        "value":       float(str(row.get("CLSPRC_IDX","0")).replace(",","")),
                        "change":      float(str(row.get("PRV_DD_CMPR","0")).replace(",","")),
                        "change_rate": float(str(row.get("FLUC_RT","0")).replace(",","")),
                        "volume":      int(str(row.get("ACC_TRDVOL","0")).replace(",","")),
                    }
        except Exception as e:
            logger.debug(f"[KRX Portal] {market}: {e}")
        return None

    def get_index_investor(self, market: str = "kospi") -> dict | None:
        """
        투자자별 매매동향 조회.
        반환: { frn, inst, ind } 순매수 (억원)
        """
        issuecode = "K1" if market.lower() == "kospi" else "K2"

        def _fetch():
            # K-mydata 투자자 API 시도
            data = self._kmydata_get(
                f"/v3/market/realtime/index/{market}/{issuecode}/investor"
            )
            if data and data.get("result"):
                r = data["result"]
                return {
                    "frn":  r.get("frnNetBuySellAmt", r.get("frnIvsrNetAmt", 0)),
                    "inst": r.get("orgNetBuySellAmt", r.get("orgIvsrNetAmt", 0)),
                    "ind":  r.get("indNetBuySellAmt", r.get("indIvsrNetAmt", 0)),
                }

            # KRX 데이터포털 투자자별
            return self._get_investor_from_krx_portal(market)

        return self._cached(f"investor_{market}", _fetch, ttl=300)

    def _get_investor_from_krx_portal(self, market: str) -> dict | None:
        """KRX 데이터포털 투자자별 매매동향."""
        try:
            mkt_id = "STK" if market.lower() == "kospi" else "KSQ"
            today  = date.today().strftime("%Y%m%d")
            r = self.session.post(
                KRX_DATA_URL,
                data={
                    "bld":     "dbms/MDC/STAT/standard/MDCSTAT02201",
                    "mktId":   mkt_id,
                    "invstTpCd": "ALL",
                    "strtDd":  today,
                    "endDd":   today,
                    "share":   "1",
                    "money":   "3",  # 억원 단위
                },
                timeout=8,
            )
            if r.ok:
                j = r.json()
                rows = j.get("output") or j.get("OutBlock_1") or []
                result = {"frn": 0.0, "inst": 0.0, "ind": 0.0}
                for row in rows:
                    inv_nm = str(row.get("INVST_TP_NM", row.get("invst_tp_nm", "")))
                    net    = float(str(row.get("NET_BUY_AMT", row.get("net_buy_amt", "0"))).replace(",",""))
                    if "외국인" in inv_nm:
                        result["frn"] = net
                    elif "기관" in inv_nm and "합계" in inv_nm:
                        result["inst"] = net
                    elif "개인" in inv_nm:
                        result["ind"] = net
                if result["frn"] != 0 or result["inst"] != 0:
                    return result
        except Exception as e:
            logger.debug(f"[KRX Investor] {market}: {e}")
        return None

    def get_stock_info(self, stock_code: str) -> dict | None:
        """
        종목 시장구분 + 시가총액 + 시총순위.
        반환: { market, mktcap, mktcap_rank, stock_name }
        """
        def _fetch():
            # KRX 데이터포털 종목 기본정보
            try:
                today = date.today().strftime("%Y%m%d")
                r = self.session.post(
                    KRX_DATA_URL,
                    data={
                        "bld":     "dbms/MDC/STAT/standard/MDCSTAT01901",
                        "isuCd":   stock_code,
                        "trdDd":   today,
                        "share":   "1",
                        "money":   "1",
                    },
                    timeout=8,
                )
                if r.ok:
                    j = r.json()
                    rows = j.get("OutBlock_1") or j.get("output") or []
                    if rows:
                        row = rows[0]
                        mkt = row.get("MKT_NM", row.get("mkt_nm", ""))
                        market = "KOSPI" if "유가" in mkt or "KOSPI" in mkt.upper() else \
                                 "KOSDAQ" if "코스닥" in mkt or "KOSDAQ" in mkt.upper() else mkt
                        return {
                            "market":      market,
                            "stock_name":  row.get("ISU_NM", row.get("isu_nm", "")),
                            "mktcap":      int(str(row.get("MKTCAP", row.get("mktcap", "0"))).replace(",","")),
                            "mktcap_rank": None,
                        }
            except Exception as e:
                logger.debug(f"[KRX StockInfo] {stock_code}: {e}")
            return None

        return self._cached(f"stock_{stock_code}", _fetch, ttl=3600)

    def get_index_history(self, market: str = "kospi", years: int = 3) -> list:
        """
        지수 일별 시세 (최대 3년치).
        반환: [{ date, close, open, high, low, volume }, ...]
        """
        end_date   = date.today()
        start_date = end_date - timedelta(days=365 * years + 30)
        mkt_id     = "STK" if market.lower() == "kospi" else "KSQ"
        issuecode  = "K1"  if market.lower() == "kospi" else "K2"

        try:
            r = self.session.post(
                KRX_DATA_URL,
                data={
                    "bld":     "dbms/MDC/STAT/standard/MDCSTAT00301",
                    "isuCd":   issuecode,
                    "strtDd":  start_date.strftime("%Y%m%d"),
                    "endDd":   end_date.strftime("%Y%m%d"),
                    "share":   "1",
                    "money":   "1",
                    "csvxls_isNo": "false",
                },
                timeout=30,
            )
            if r.ok:
                j = r.json()
                rows = j.get("output") or j.get("OutBlock_1") or []
                result = []
                for row in rows:
                    try:
                        dt_str = str(row.get("TRD_DD", row.get("trd_dd", "")))
                        if not dt_str:
                            continue
                        # "2026/03/24" or "20260324"
                        dt_str = dt_str.replace("/","").replace("-","")
                        dt = datetime.strptime(dt_str[:8], "%Y%m%d").date()
                        close = float(str(row.get("CLSPRC_IDX", row.get("CLSPRC", row.get("clsprc","0")))).replace(",",""))
                        if close > 0:
                            result.append({
                                "date":  dt.isoformat(),
                                "close": close,
                                "open":  float(str(row.get("OPNPRC_IDX", row.get("opnprc","0"))).replace(",","")),
                                "high":  float(str(row.get("HGPRC_IDX",  row.get("hgprc","0"))).replace(",","")),
                                "low":   float(str(row.get("LWPRC_IDX",  row.get("lwprc","0"))).replace(",","")),
                                "volume": int(str(row.get("ACC_TRDVOL", row.get("acc_trdvol","0"))).replace(",","")),
                            })
                    except Exception:
                        continue
                result.sort(key=lambda x: x["date"])
                logger.info(f"[KRX History] {market}: {len(result)}건")
                return result
        except Exception as e:
            logger.error(f"[KRX History] {market}: {e}")

        # ── Yahoo Finance fallback ────────────────────────────────
        logger.info(f"[KRX History] {market}: Yahoo Finance fallback")
        try:
            import yfinance as yf
            import pandas as pd
            symbol = "^KS11" if market.lower() == "kospi" else "^KQ11"
            df = yf.download(symbol, period=f"{years}y", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return []
            if hasattr(df.columns, 'get_level_values'):
                try: df.columns = df.columns.get_level_values(0)
                except: pass
            result = []
            for ts, row in df.iterrows():
                try:
                    dt = ts.date()
                    def gv(col):
                        v = row.get(col, 0) if hasattr(row,'get') else getattr(row, col, 0)
                        if isinstance(v, pd.Series): v = v.iloc[0]
                        return float(v) if v is not None else 0.0
                    close = gv("Close")
                    if close > 0:
                        result.append({
                            "date":   dt.isoformat(),
                            "close":  close,
                            "open":   gv("Open"),
                            "high":   gv("High"),
                            "low":    gv("Low"),
                            "volume": int(gv("Volume")),
                        })
                except Exception:
                    continue
            result.sort(key=lambda x: x["date"])
            logger.info(f"[Yahoo History] {market}: {len(result)}건")
            return result
        except Exception as e2:
            logger.error(f"[Yahoo History] {market}: {e2}")
        return []


# 싱글톤
try:
    import config as _config
    krx_client = KRXClient(api_key=getattr(_config, "KRX_API_KEY", ""))
except Exception:
    krx_client = KRXClient()
