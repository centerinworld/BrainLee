import requests
import json
import time
import os
import threading
from datetime import datetime
import config
import logging

logger = logging.getLogger(__name__)

class KISClient:
    """
    한국투자증권(KIS) API 클라이언트
    - 토큰 발급 및 자동 갱신 (파일 캐시 지원)
    - 국내 주식 실시간 시세 조회 (1초 1회 제한 엄격 준수)
    """

    def __init__(self):
        self.app_key = config.KIS_APP_KEY
        self.app_secret = config.KIS_APP_SECRET
        self.base_url = config.KIS_URL
        self.access_token = None
        self.token_expiry = 0
        self.headers = {
            "Content-Type": "application/json",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        self.lock = threading.Lock()
        self.last_call_time = 0
        self._load_token_from_file()

    def _save_token(self, token, expiry):
        try:
            with open("kis_token.json", "w") as f:
                json.dump({"access_token": token, "token_expiry": expiry}, f)
        except Exception as e:
            logger.error(f"KIS Token 저장 실패: {e}")

    def _load_token_from_file(self):
        try:
            if os.path.exists("kis_token.json"):
                with open("kis_token.json", "r") as f:
                    data = json.load(f)
                    self.access_token = data.get("access_token")
                    self.token_expiry = data.get("token_expiry", 0)
                    if self.access_token and time.time() < self.token_expiry:
                        return True
        except Exception as e:
            logger.error(f"KIS Token 로드 실패: {e}")
        return False

    def _issue_token(self):
        if self._load_token_from_file():
            return True

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        try:
            res = requests.post(url, data=json.dumps(payload))
            data = res.json()
            if "access_token" in data:
                self.access_token = data["access_token"]
                self.token_expiry = int(time.time() + data.get("expires_in", 86400) - 3600)
                self._save_token(self.access_token, self.token_expiry)
                logger.info("KIS Access Token 신규 발급 및 저장 성공")
                return True
            else:
                logger.error(f"KIS Token 발급 실패: {data}")
                return False
        except Exception as e:
            logger.error(f"KIS Token 발급 중 오류 발생: {e}")
            return False

    def get_token(self):
        if not self.access_token or time.time() > self.token_expiry:
            if self._load_token_from_file():
                return self.access_token
            self._issue_token()
        return self.access_token

    def _call_investor_api(self, stock_code: str) -> list:
        """KIS inquire-investor API 호출 → output 배열 반환 (최근 30거래일)."""
        token = self.get_token()
        if not token:
            return []
        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010900"
            }
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
            try:
                res = requests.get(url, headers=headers, params=params)
                data = res.json()
                if data.get("rt_cd") == "0" and data.get("output"):
                    return data["output"]
                logger.warning(f"KIS 수급 조회 실패 [{stock_code}]: {data.get('msg1','')}")
            except Exception as e:
                logger.error(f"KIS 수급 조회 오류 [{stock_code}]: {e}")
            return []

    def get_investor_trends(self, stock_code: str):
        """당일 기관/외국인 순매수 반환 (output[0] = 가장 최신)."""
        output = self._call_investor_api(stock_code)
        if not output:
            return None
        row = output[0]
        return {
            "inst_net_buy": float(row.get("orgn_ntby_qty", 0) or 0),
            "frn_net_buy":  float(row.get("frgn_ntby_qty", 0) or 0),
        }

    def get_investor_trends_bulk(self, stock_code: str) -> list:
        """
        최근 30거래일 기관/외국인/개인 순매수 전체 반환.
        collect_kis_supply_history.py 에서 사용.
        반환: [{ date, inst_net_buy, frn_net_buy, ind_net_buy,
                  inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt }, ...]
        날짜 형식: "YYYY-MM-DD"
        """
        output = self._call_investor_api(stock_code)
        if not output:
            return []
        result = []
        for row in output:
            stck_bsop_date = row.get("stck_bsop_date", "")
            if len(stck_bsop_date) == 8:
                date_str = f"{stck_bsop_date[:4]}-{stck_bsop_date[4:6]}-{stck_bsop_date[6:]}"
            else:
                continue
            def _f(key):
                try:
                    v = row.get(key, "0") or "0"
                    return float(str(v).replace(",", ""))
                except:
                    return 0.0
            result.append({
                "date":             date_str,
                "inst_net_buy":     _f("orgn_ntby_qty"),   # 수량(주)
                "frn_net_buy":      _f("frgn_ntby_qty"),
                "ind_net_buy":      _f("prsn_ntby_qty"),
                "inst_net_buy_amt": _f("orgn_ntby_tr_pbmn"), # 금액(백만원)
                "frn_net_buy_amt":  _f("frgn_ntby_tr_pbmn"),
                "ind_net_buy_amt":  _f("prsn_ntby_tr_pbmn"),
            })
        return result

    def get_current_price(self, stock_code: str):
        """국내 주식 현재가 조회 (Strict Throttling 적용)"""
        token = self.get_token()
        if not token:
            return None

        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                wait_time = 1.0 - elapsed
                logger.info(f"KIS API 레이트 리밋 대기: {wait_time:.2f}초")
                time.sleep(wait_time)

            self.last_call_time = time.time()

            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010100"
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code
            }

            try:
                res = requests.get(url, headers=headers, params=params)
                data = res.json()
                if data.get("rt_cd") == "0":
                    price_info = data["output"]
                    return {
                        "close": float(price_info["stck_prpr"]),
                        "open": float(price_info["stck_oprc"]),
                        "high": float(price_info["stck_hgpr"]),
                        "low": float(price_info["stck_lwpr"]),
                        "volume": float(price_info["acml_vol"]),
                        "date": datetime.now().strftime("%Y-%m-%d")
                    }
                return None
            except Exception as e:
                logger.error(f"KIS API 조회 오류: {e}")
                return None


    # ──────────────────────────────────────────────────────────────────────
    # KIS 지수 시세/수급 (KOSPI·KOSDAQ)
    # ──────────────────────────────────────────────────────────────────────

    def get_index_price(self, index_code: str) -> dict | None:
        """
        KIS 국내 지수 현재가 조회 (FHPUP02100000).
        index_code: "0001" = KOSPI, "1001" = KOSDAQ

        반환: { value, open, high, low, change, change_rate, date }
        """
        token = self.get_token()
        if not token:
            return None
        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()

            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
            headers = {
                "Content-Type":  "application/json",
                "authorization": f"Bearer {token}",
                "appkey":        self.app_key,
                "appsecret":     self.app_secret,
                "tr_id":         "FHPUP02100000",
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD":         index_code,
            }
            try:
                res  = requests.get(url, headers=headers, params=params, timeout=5)
                data = res.json()
                if data.get("rt_cd") == "0" and data.get("output"):
                    o = data["output"]
                    return {
                        "value":       float(o.get("bstp_nmix_prpr", 0) or 0),
                        "open":        float(o.get("bstp_nmix_oprc", 0) or 0),
                        "high":        float(o.get("bstp_nmix_hgpr", 0) or 0),
                        "low":         float(o.get("bstp_nmix_lwpr", 0) or 0),
                        "change":      float(o.get("bstp_nmix_prdy_vrss", 0) or 0),
                        "change_rate": float(o.get("prdy_ctrt", 0) or 0),
                        "volume":      float(o.get("acml_vol", 0) or 0),
                        "date":        datetime.now().strftime("%Y-%m-%d"),
                    }
                logger.warning(f"[KIS-Index] {index_code} 조회 실패: {data.get('msg1','')}")
            except Exception as e:
                logger.error(f"[KIS-Index] {index_code} 오류: {e}")
            return None

    def get_index_investor(self, index_code: str) -> dict | None:
        """
        지수 투자자별 매매동향.
        KIS TR에서 지수 수급을 제공하지 않으므로 pykrx 사용.

        index_code: "0001"(KOSPI), "1001"(KOSDAQ)
        반환: { inst_net_buy, frn_net_buy, ind_net_buy, date }  (단위: 억원)
        """
        # pykrx로 당일 투자자별 순매수 조회 (억원 단위)
        try:
            from pykrx import stock as _pykrx
            today_str = datetime.now().strftime("%Y%m%d")
            market    = "KOSPI" if index_code == "0001" else "KOSDAQ"

            # get_market_net_purchases_of_business_day: 투자자별 순매수
            df = _pykrx.get_market_net_purchases_of_business_day(
                today_str, today_str, market
            )
            if df is not None and not df.empty:
                # 컬럼: 투자자, 매도거래량, 매수거래량, 순매수거래량, 매도거래대금, 매수거래대금, 순매수거래대금
                def _get(label):
                    for col in df.columns:
                        if label in str(col):
                            row = df[col]
                            return float(row.values[-1]) if hasattr(row, 'values') else float(row)
                    return 0.0

                # index 기준으로 투자자명 찾기
                inst = frn = ind = 0.0
                for idx_val in df.index:
                    idx_str = str(idx_val)
                    # 순매수거래대금 컬럼 (백만원) → 억원으로 변환
                    net_col = None
                    for col in df.columns:
                        if "순매수" in str(col) and "거래대금" in str(col):
                            net_col = col
                            break
                    if net_col is None:
                        # 컬럼명 다를 경우 마지막 컬럼 사용
                        net_col = df.columns[-1]
                    val = float(df.loc[idx_val, net_col]) / 100  # 백만→억원
                    if "기관" in idx_str and "합계" in idx_str:
                        inst = val
                    elif "외국인" in idx_str:
                        frn  = val
                    elif "개인" in idx_str:
                        ind  = val

                if inst != 0 or frn != 0 or ind != 0:
                    logger.info(f"[pykrx수급] {market}: 기관={inst:+,.1f} 외국인={frn:+,.1f} 개인={ind:+,.1f}억원")
                    return {
                        "inst_net_buy": round(inst, 1),
                        "frn_net_buy":  round(frn,  1),
                        "ind_net_buy":  round(ind,  1),
                        "date": datetime.now().strftime("%Y-%m-%d"),
                    }
        except Exception as e:
            logger.warning(f"[pykrx수급] {index_code}: {e}")

        logger.warning(f"[KIS-수급] {index_code}: pykrx 수급 없음")
        return None

    def get_today_executions(self) -> list:
        """
        당일 주식 체결내역 조회 (TTTC8001R).
        반환: [{ stock_code, stock_name, tx_type, quantity, price, tx_time }, ...]
        """
        token = self.get_token()
        if not token:
            return []
        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
            headers = {
                "Content-Type":  "application/json",
                "authorization": f"Bearer {token}",
                "appkey":        self.app_key,
                "appsecret":     self.app_secret,
                "tr_id":         "TTTC8001R",
            }
            today = datetime.now().strftime("%Y%m%d")
            params = {
                "CANO":           getattr(config, "KIS_ACCOUNT_NO", ""),
                "ACNT_PRDT_CD":   getattr(config, "KIS_ACCOUNT_PROD", "01"),
                "INQR_STRT_DT":   today,
                "INQR_END_DT":    today,
                "SLL_BUY_DVSN_CD": "00",  # 00: 전체, 01: 매도, 02: 매수
                "INQR_DVSN":       "00",
                "PDNO":            "",
                "CCLD_DVSN":       "01",   # 체결만
                "ORD_GNO_BRNO":    "",
                "ODNO":            "",
                "INQR_DVSN_3":     "00",
                "INQR_DVSN_1":     "",
                "CTX_AREA_FK100":  "",
                "CTX_AREA_NK100":  "",
            }
            try:
                res  = requests.get(url, headers=headers, params=params, timeout=10)
                data = res.json()
                if data.get("rt_cd") != "0":
                    logger.warning(f"[KIS체결] {data.get('msg1','')}")
                    return []
                output1 = data.get("output1", [])
                result  = []
                for row in output1:
                    qty   = float(row.get("tot_ccld_qty", 0) or 0)
                    price = float(row.get("avg_prvs",    0) or row.get("ccld_avg_pric", 0) or 0)
                    if qty == 0 or price == 0:
                        continue
                    sll_buy = row.get("sll_buy_dvsn_cd", "")  # "01"=매도, "02"=매수
                    result.append({
                        "stock_code": row.get("pdno", ""),
                        "stock_name": row.get("prdt_name", ""),
                        "tx_type":    "sell" if sll_buy == "01" else "buy",
                        "quantity":   qty,
                        "price":      price,
                        "tx_time":    row.get("ord_tmd", ""),
                    })
                logger.info(f"[KIS체결] {len(result)}건 조회")
                return result
            except Exception as e:
                logger.error(f"[KIS체결] 오류: {e}")
                return []

    def get_account_balance(self) -> list:
        """
        계좌 잔고 조회 (TTTC8434R).
        반환: [{ stock_code, stock_name, quantity, avg_price, current_price }, ...]
        """
        token = self.get_token()
        if not token:
            return []
        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = {
                "Content-Type":  "application/json",
                "authorization": f"Bearer {token}",
                "appkey":        self.app_key,
                "appsecret":     self.app_secret,
                "tr_id":         "TTTC8434R",
            }
            params = {
                "CANO":           getattr(config, "KIS_ACCOUNT_NO", ""),
                "ACNT_PRDT_CD":   getattr(config, "KIS_ACCOUNT_PROD", "01"),
                "AFHR_FLPR_YN":   "N",
                "OFL_YN":         "",
                "INQR_DVSN":      "02",
                "UNPR_DVSN":      "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN":      "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            try:
                res  = requests.get(url, headers=headers, params=params, timeout=10)
                data = res.json()
                if data.get("rt_cd") != "0":
                    logger.warning(f"[KIS잔고] {data.get('msg1','')}")
                    return []
                output1 = data.get("output1", [])
                result  = []
                for row in output1:
                    qty = float(row.get("hldg_qty", 0) or 0)
                    if qty == 0:
                        continue
                    result.append({
                        "stock_code":    row.get("pdno", ""),
                        "stock_name":    row.get("prdt_name", ""),
                        "quantity":      qty,
                        "avg_price":     float(row.get("pchs_avg_pric", 0) or 0),
                        "current_price": float(row.get("prpr", 0) or 0),
                        "eval_amount":   float(row.get("evlu_amt", 0) or 0),
                        "profit":        float(row.get("evlu_pfls_amt", 0) or 0),
                        "profit_pct":    float(row.get("evlu_pfls_rt", 0) or 0),
                    })
                logger.info(f"[KIS잔고] {len(result)}종목 조회")
                return result
            except Exception as e:
                logger.error(f"[KIS잔고] 오류: {e}")
                return []


kis_client = KISClient()
