import requests
import json
import time
import os
import threading
from datetime import datetime
import config
import logging

logger = logging.getLogger(__name__)

# 중앙 rate limiter — KIS 전용 쿼터/간격 관리
try:
    from api_rate_limiter import api_limiter as _rl
    _USE_RL = True
except ImportError:
    _USE_RL = False

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

    def _issue_hashkey(self, payload: dict) -> str:
        """KIS POST 주문용 hashkey 발급."""
        try:
            url = f"{self.base_url}/uapi/hashkey"
            headers = {
                "Content-Type": "application/json",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }
            r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
            d = r.json()
            return d.get("HASH") or d.get("hash") or ""
        except Exception as e:
            logger.error(f"KIS hashkey 발급 오류: {e}")
            return ""

    def get_token(self):
        """2026-08-13(3차) 수정(docs/codex_handoff_live_trading_gaps_20260813.md #3):
        기존엔 _issue_token() 실패 시 self.access_token을 그대로 둬서(만료된 토큰이라도)
        get_token()이 그 값을 돌려줬음 — 호출부는 "토큰을 받았다"고 착각하고 KIS에
        요청을 보내지만 매번 인증거부만 당하고(get_current_price 등은 rt_cd!=0을
        그냥 None으로 뭉개버림), 원인이 토큰 갱신 실패라는 게 로그를 뒤지지 않는 한
        전혀 드러나지 않았음. 갱신 실패 시 access_token을 명시적으로 비워 호출부가
        "토큰 없음"을 정상적으로 인지하게 하고, 장중(09:00~15:30) 실패는 텔레그램으로
        1일 1회 한도 알림(notifier의 key dedup 재사용, 스팸 방지)."""
        if not self.access_token or time.time() > self.token_expiry:
            if self._load_token_from_file():
                return self.access_token
            if not self._issue_token():
                self.access_token = None
                self._alert_token_failure()
        return self.access_token

    def _alert_token_failure(self) -> None:
        try:
            now = datetime.now()
            if not (9 <= now.hour < 16):
                return
            import notifier
            notifier.send(
                f"⚠️ KIS 토큰 갱신 실패 — 장중 시세/주문 API가 인증 거부될 수 있습니다 "
                f"({now.strftime('%H:%M')})",
                key=f"kis_token_fail_{now.strftime('%Y-%m-%d')}",
            )
        except Exception as e:
            logger.error(f"KIS 토큰 실패 알림 오류: {e}")

    def _call_investor_api(self, stock_code: str) -> list:
        """KIS inquire-investor API 호출 → output 배열 반환 (최근 30거래일)."""
        token = self.get_token()
        if not token:
            return []
        with self.lock:
            # 중앙 rate limiter 사용 (jitter + 쿼터 체크 포함)
            if _USE_RL:
                if not _rl.wait("KIS"):
                    return []  # 쿼터 소진
            else:
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
                res = requests.get(url, headers=headers, params=params, timeout=15)
                if res.status_code == 429:
                    if _USE_RL:
                        _rl.report_block("KIS", cooldown=60)
                    return []
                data = res.json()
                if data.get("rt_cd") == "0" and data.get("output"):
                    return data["output"]
                msg = data.get("msg1", "")
                if "초과" in msg or "limit" in msg.lower():
                    logger.warning(f"KIS 요청 제한 감지 [{stock_code}]: {msg}")
                    if _USE_RL:
                        _rl.report_block("KIS", cooldown=60)
                else:
                    logger.warning(f"KIS 수급 조회 실패 [{stock_code}]: {msg}")
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
        from trading_calendar import is_kr_trading_day as _is_kr_td
        from datetime import date as _date
        result = []
        for row in output:
            stck_bsop_date = row.get("stck_bsop_date", "")
            if len(stck_bsop_date) == 8:
                date_str = f"{stck_bsop_date[:4]}-{stck_bsop_date[4:6]}-{stck_bsop_date[6:]}"
            else:
                continue
            # KIS API는 공휴일 행도 반환함 (전일 수량 복사, 금액=0) — 원천 차단
            try:
                if not _is_kr_td(_date.fromisoformat(date_str)):
                    continue
            except Exception:
                pass
            def _f(key):
                try:
                    v = row.get(key, "0") or "0"
                    return float(str(v).replace(",", ""))
                except Exception:
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
        # ⚠️ 2026-08-23: KIS "현재가 조회"(inquire-price) 응답은 휴장일에도
        # 항상 200으로 직전 거래일의 최종 시세를 반환하는데, 아래 코드가
        # 그 값을 datetime.now()로 라벨링해 저장하면 price_history에 실제로는
        # 없었던 거래일(휴장일)의 "가짜 거래" 행이 생긴다(실증: 172670이
        # 2026-08-17(광복절 대체휴일) 휴장일에 8/14 종가와 완전히 동일한
        # OHLCV로 중복 저장됨 — 스케줄러 루프는 is_kr_trading_day()로 막혀
        # 있었으나 이 함수 자체엔 방어가 없어 수동/온디맨드 호출 경로가 뚫림).
        # 실시간 현재가는 휴장일에 의미가 없으므로 원천에서 차단한다.
        from trading_calendar import is_kr_trading_day
        if not is_kr_trading_day():
            return None

        token = self.get_token()
        if not token:
            return None

        with self.lock:
            if _USE_RL:
                if not _rl.wait("KIS"):
                    return None
            else:
                elapsed = time.time() - self.last_call_time
                if elapsed < 1.0:
                    time.sleep(1.0 - elapsed)
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
                        "trade_amount": float(price_info.get("acml_tr_pbmn", 0) or 0),
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "observed_at": datetime.now().isoformat(timespec="seconds"),
                        "raw": price_info,
                    }
                return None
            except Exception as e:
                logger.error(f"KIS API 조회 오류: {e}")
                return None

    def get_orderbook(self, stock_code: str) -> dict | None:
        """Return KIS top-of-book data used by the live-order data contract."""
        token = self.get_token()
        if not token:
            return None
        with self.lock:
            if _USE_RL:
                if not _rl.wait("KIS"):
                    return None
            else:
                elapsed = time.time() - self.last_call_time
                if elapsed < 1.0:
                    time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010200",
            }
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                data = response.json()
                if data.get("rt_cd") != "0" or not data.get("output1"):
                    logger.warning("KIS 호가 조회 실패 [%s]: %s", stock_code, data.get("msg1", ""))
                    return None
                row = data["output1"]
                return {
                    "bid1": float(row.get("bidp1", 0) or 0),
                    "ask1": float(row.get("askp1", 0) or 0),
                    "bid_qty1": float(row.get("bidp_rsqn1", 0) or 0),
                    "ask_qty1": float(row.get("askp_rsqn1", 0) or 0),
                    "total_bid_qty": float(row.get("total_bidp_rsqn", 0) or 0),
                    "total_ask_qty": float(row.get("total_askp_rsqn", 0) or 0),
                    "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "raw": row,
                }
            except Exception as exc:
                logger.error("KIS 호가 조회 오류 [%s]: %s", stock_code, exc)
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
                        "order_no": row.get("odno", ""),
                        "stock_code": row.get("pdno", ""),
                        "stock_name": row.get("prdt_name", ""),
                        "tx_type":    "sell" if sll_buy == "01" else "buy",
                        "quantity":   qty,
                        "price":      price,
                        "tx_time":    row.get("ord_tmd", ""),
                        "broker_status": row.get("ord_dvsn_name", "filled") or "filled",
                        "raw": row,
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

    def get_account_snapshot(self) -> dict:
        """
        계좌 스냅샷 조회 (TTTC8434R)
        반환:
          {
            "holdings": [...],
            "summary": {
              "cash_available": ...,
              "total_eval": ...,
              "total_buy": ...,
              "total_profit": ...,
              "total_profit_pct": ...
            }
          }
        """
        token = self.get_token()
        if not token:
            return {"holdings": [], "summary": {}}
        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "TTTC8434R",
            }
            params = {
                "CANO": getattr(config, "KIS_ACCOUNT_NO", ""),
                "ACNT_PRDT_CD": getattr(config, "KIS_ACCOUNT_PROD", "01"),
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10)
                data = res.json()
                if data.get("rt_cd") != "0":
                    logger.warning(f"[KIS계좌스냅샷] {data.get('msg1','')}")
                    return {"holdings": [], "summary": {}}

                def _f(v):
                    try:
                        return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
                    except Exception:
                        return 0.0

                holdings = []
                for row in data.get("output1", []) or []:
                    qty = _f(row.get("hldg_qty"))
                    if qty <= 0:
                        continue
                    holdings.append({
                        "stock_code": row.get("pdno", ""),
                        "stock_name": row.get("prdt_name", ""),
                        "quantity": qty,
                        "avg_price": _f(row.get("pchs_avg_pric")),
                        "current_price": _f(row.get("prpr")),
                        "eval_amount": _f(row.get("evlu_amt")),
                        "profit": _f(row.get("evlu_pfls_amt")),
                        "profit_pct": _f(row.get("evlu_pfls_rt")),
                    })

                s = (data.get("output2") or [{}])[0]
                cash_deposit = _f(s.get("dnca_tot_amt"))
                cash_orderable = _f(s.get("ord_psbl_cash"))
                cash_d2 = _f(s.get("nxdy_excc_amt") or s.get("d2_auto_rdpt_amt"))
                strict_candidates = [v for v in (cash_deposit, cash_orderable, cash_d2) if v > 0]
                strict_cash = min(strict_candidates) if strict_candidates else 0.0
                summary = {
                    # cash_available kept for backward compatibility. New live-order guards should use
                    # strict_cash_available to avoid accidental margin/receivable-funded buys.
                    "cash_available": cash_orderable or cash_deposit,
                    "strict_cash_available": strict_cash,
                    "cash_deposit": cash_deposit,
                    "cash_orderable": cash_orderable,
                    "cash_d2": cash_d2,
                    "total_eval": _f(s.get("tot_evlu_amt")),
                    "total_buy": _f(s.get("pchs_amt_smtl_amt")),
                    "total_profit": _f(s.get("evlu_pfls_smtl_amt")),
                    "total_profit_pct": _f(s.get("asst_icdc_erng_rt") or s.get("evlu_erng_rt")),
                }
                return {"holdings": holdings, "summary": summary}
            except Exception as e:
                logger.error(f"[KIS계좌스냅샷] 오류: {e}")
                return {"holdings": [], "summary": {}}

    def place_order_cash(self, stock_code: str, side: str, qty: int, order_type: str = "market", price: float = 0.0) -> dict:
        """
        국내주식 현금 주문.
        side: buy|sell
        order_type: market|limit
        """
        token = self.get_token()
        if not token:
            return {"ok": False, "error": "token_fail"}
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "invalid_side"}
        if qty <= 0:
            return {"ok": False, "error": "invalid_qty"}

        if side == "buy" and os.getenv("KIS_STRICT_CASH_BUY_ONLY", "true").lower() == "true":
            estimate_price = float(price or 0.0)
            if estimate_price <= 0:
                quote = self.get_current_price(stock_code) or {}
                estimate_price = float(quote.get("close") or 0.0)
            if estimate_price <= 0:
                return {"ok": False, "error": "strict_cash_guard_price_missing"}

            snapshot = self.get_account_snapshot() or {"summary": {}}
            summary = snapshot.get("summary") or {}
            strict_cash = float(summary.get("strict_cash_available") or 0.0)
            buffer_pct = float(os.getenv("KIS_STRICT_CASH_BUY_BUFFER_PCT", "1.02"))
            required_cash = float(qty) * estimate_price * buffer_pct
            if strict_cash <= 0:
                return {
                    "ok": False,
                    "error": "strict_cash_guard_no_cash",
                    "strict_cash_available": strict_cash,
                    "required_cash": round(required_cash, 0),
                }
            if strict_cash < required_cash:
                return {
                    "ok": False,
                    "error": "strict_cash_guard_cash_short",
                    "strict_cash_available": round(strict_cash, 0),
                    "required_cash": round(required_cash, 0),
                    "estimated_price": estimate_price,
                    "qty": qty,
                }

        with self.lock:
            elapsed = time.time() - self.last_call_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.last_call_time = time.time()

            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
            tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
            ord_dvsn = "01" if order_type == "market" else "00"  # 01 시장가 / 00 지정가
            ord_unpr = "0" if ord_dvsn == "01" else str(int(price))

            payload = {
                "CANO": getattr(config, "KIS_ACCOUNT_NO", ""),
                "ACNT_PRDT_CD": getattr(config, "KIS_ACCOUNT_PROD", "01"),
                "PDNO": str(stock_code).zfill(6),
                "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(int(qty)),
                "ORD_UNPR": ord_unpr,
            }
            hashkey = self._issue_hashkey(payload)
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
            }
            if hashkey:
                headers["hashkey"] = hashkey

            try:
                res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
                data = res.json()
                if data.get("rt_cd") == "0":
                    out = data.get("output", {}) or {}
                    return {
                        "ok": True,
                        "order_no": out.get("ODNO") or out.get("odno") or "",
                        "msg": data.get("msg1", "OK"),
                    }
                return {
                    "ok": False,
                    "error": data.get("msg1") or f"http_{res.status_code}",
                    "raw": data,
                }
            except Exception as e:
                logger.error(f"[KIS주문] 오류: {e}")
                return {"ok": False, "error": str(e)}


kis_client = KISClient()
