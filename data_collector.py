"""
data_collector.py — 주식 데이터 수집기 (재설계 버전)

═══════════════════════════════════════════════════════════════
수집 정책 (확정)
═══════════════════════════════════════════════════════════════

[즉시 수집 — 화면 조회 시]
  · run_ondemand(stock_code)  호출됨
  · 주가/수급 : 오늘 데이터 DB에 없으면 → 즉시 수집
  · 재무      : DB에 최신 분기 없으면   → 즉시 수집
  · 수집 완료 후 프론트로 응답

[자정 배치 — 매일 00:00]
  · watchlist + portfolio 전체 종목 대상
  · 주가/수급 : 무조건 오늘 데이터 수집 (매일 기록)
  · 재무      : DART 당일 공시 있는 종목만 업데이트
  · 매크로    : 오늘 DB 없으면 수집

[상시 수집 모드 — data_collector.py 단독 실행 시]
  · 5분 주기로 watchlist 전체 주가/수급 수집
  · 재무는 _collect_fundamentals_if_needed 조건부

═══════════════════════════════════════════════════════════════
"""

import OpenDartReader
import yfinance as yf
import pandas as pd
from financial_profiles import get_profile
import httpx
import time
import argparse
import logging
from datetime import datetime, timedelta, date
import config
from dart_key_manager import RotatingOpenDartReader
from kis_client import kis_client

logger = logging.getLogger(__name__)

try:
    from api_rate_limiter import api_limiter as _rl
    _USE_RL = True
except ImportError:
    _USE_RL = False


class DataCollector:
    def __init__(self, dart_api_key: str, base_url: str = "http://127.0.0.1:8000"):
        self.dart     = self._init_dart(dart_api_key)
        self.kis      = kis_client
        self.base_url = base_url

        # 인메모리 캐시
        self._fn_collected: set       = set()   # (code, year, quarter)
        self._macro_collected_date    = None     # date
        self._dart_disclosure_cache: dict = {}  # date → set(stock_codes with disclosures)

    # ──────────────────────────────────────────────────────────────────────
    # 초기화 헬퍼
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _init_dart(dart_api_key: str):
        """OpenDartReader 초기화. pkl 캐시 오류 시 자동 복구."""
        try:
            return RotatingOpenDartReader()
        except Exception:
            import glob, os
            for f in glob.glob("/tmp/**/*.pkl", recursive=True) + glob.glob(os.path.expanduser("~/**/*.pkl"), recursive=True):
                try: os.remove(f)
                except Exception: pass
            try:
                return RotatingOpenDartReader()
            except Exception as e:
                logger.error(f"OpenDartReader 초기화 실패: {e}")
                return None

    def wait_for_server(self, timeout: int = 30) -> bool:
        start = time.time()
        print(f"서버 대기 중 ({self.base_url})...")
        while time.time() - start < timeout:
            try:
                if httpx.get(f"{self.base_url}/").status_code == 200:
                    print("서버 준비 완료.")
                    return True
            except Exception:
                pass
            time.sleep(2)
        print("서버 대기 시간 초과.")
        return False

    # ──────────────────────────────────────────────────────────────────────
    # DB 조회 헬퍼
    # ──────────────────────────────────────────────────────────────────────

    def _price_exists_today(self, stock_code: str) -> bool:
        """오늘 날짜의 주가 데이터가 DB에 있는지 확인."""
        today = datetime.now().date().isoformat()
        try:
            res = httpx.get(
                f"{self.base_url}/api/dashboard/chart/{stock_code}",
                params={"days": 2}, timeout=5,
            )
            if res.status_code == 200:
                return any(row.get("date", "").startswith(today) for row in res.json())
        except Exception:
            pass
        return False

    def _financial_exists(
        self,
        stock_code: str,
        year: int,
        quarter: int,
        is_annual: bool | None = None,
        data_source: str | None = "dart",
    ) -> bool:
        """해당 분기 재무 데이터 존재 여부 확인 (기본: DART 행 기준)."""
        key = (stock_code, year, quarter, is_annual, data_source)
        if key in self._fn_collected:
            return True
        try:
            import sys; sys.path.insert(0, '/Applications/stock_dashboard')
            from database import SessionLocal
            from models import FinancialData
            db = SessionLocal()
            q = db.query(FinancialData).filter(
                FinancialData.stock_code == stock_code,
                FinancialData.year == year,
                FinancialData.quarter == quarter,
            )
            if is_annual is not None:
                q = q.filter(FinancialData.is_annual == bool(is_annual))
            if data_source:
                q = q.filter(FinancialData.data_source == data_source)
            exists = q.first() is not None
            db.close()
            if exists:
                self._fn_collected.add(key)
            return exists
        except Exception:
            return False

    def _has_any_financial(self, stock_code: str) -> bool:
        """DB에 해당 종목 재무 1건이라도 있는지."""
        try:
            import sys; sys.path.insert(0, '/Applications/stock_dashboard')
            from database import SessionLocal
            from models import FinancialData
            db = SessionLocal()
            e = db.query(FinancialData).filter(FinancialData.stock_code == stock_code).first() is not None
            db.close(); return e
        except: return False

    @staticmethod
    def _get_latest_disclosed_quarter() -> tuple:
        """DART에 실제 공시된 최신 분기 (year, quarter)."""
        from datetime import date
        m, y = date.today().month, date.today().year
        if m >= 11: return (y, 3)
        elif m >= 8: return (y, 2)
        elif m >= 5: return (y, 1)
        elif m >= 4: return (y-1, 4)
        else:        return (y-1, 3)

    def _macro_exists_today(self, symbol: str) -> bool:
        today = datetime.now().date().isoformat()
        try:
            res = httpx.get(
                f"{self.base_url}/api/dashboard/chart/{symbol}",
                params={"days": 1}, timeout=5,
            )
            if res.status_code == 200:
                return any(row.get("date", "").startswith(today) for row in res.json())
        except Exception:
            pass
        return False

    # ──────────────────────────────────────────────────────────────────────
    # DART 공시 확인
    # ──────────────────────────────────────────────────────────────────────

    def _has_dart_disclosure_today(self, stock_code: str) -> bool:
        """
        오늘 날짜에 해당 종목의 DART 공시가 있는지 확인.
        캐시: 자정 배치 1회 호출 동안 종목별 결과 보관.
        """
        today = datetime.now().date()
        if today not in self._dart_disclosure_cache:
            self._dart_disclosure_cache[today] = set()
            # 오늘 전체 공시 목록을 한 번에 가져와 캐시
            try:
                if self.dart is None:
                    return False
                today_str = today.strftime("%Y%m%d")
                for _kind in ["A", "B"]:
                    try:
                        if _USE_RL and not _rl.wait("DART"):
                            break  # 쿼터 소진
                        disc = self.dart.list(today_str, today_str, kind=_kind)
                    except Exception:
                        continue  # 결과 없을 때 예외 무시
                    if disc is not None and not disc.empty and "stock_code" in disc.columns:
                        for code in disc["stock_code"].dropna().unique():
                            self._dart_disclosure_cache[today].add(str(code).zfill(6))

                print(f"  [DART] 오늘 공시 종목 {len(self._dart_disclosure_cache[today])}개 캐시 완료")
            except Exception as e:
                logger.warning(f"[DART] 공시 목록 조회 오류: {e}")

        return stock_code in self._dart_disclosure_cache.get(today, set())

    def _has_dart_financial_disclosure(self, stock_code: str) -> bool:
        """
        재무제표 변동을 유발할 수 있는 공시가 있는지 확인.
        단순 수시공시(IR, 임원변경 등)는 무시하고, 아래 키워드를 포함한
        공시가 있을 때만 True 반환 → 재무 재수집 트리거.

        대상 키워드:
          - 사업보고서 / 반기보고서 / 분기보고서  (정기공시)
          - 재무제표 재작성 / 감사보고서 수정
          - 회계처리 위반 / 내부회계관리제도
          - 합병 / 분할 / 영업양수도 / 유상증자 (재무구조 변동)
        """
        FINANCIAL_KEYWORDS = [
            "사업보고서", "반기보고서", "분기보고서",
            "재무제표", "감사보고서", "회계처리",
            "내부회계관리", "합병", "분할합병", "영업양수도",
            "유상증자", "무상증자", "자본감소", "전환사채",
        ]
        today = datetime.now().date()
        today_str = today.strftime("%Y%m%d")
        try:
            if self.dart is None:
                return False
            # 최근 7일 공시 조회 (당일 포함, 공시 등록 지연 대응)
            from datetime import timedelta
            week_ago = (today - timedelta(days=7)).strftime("%Y%m%d")
            for kind in ["A", "B"]:
                try:
                    if _USE_RL and not _rl.wait("DART"):
                        break  # 쿼터 소진
                    disc = self.dart.list(week_ago, today_str, kind=kind)
                except Exception:
                    continue  # OpenDartReader가 결과 없을 때 예외 던지는 경우 무시
                if disc is None or disc.empty:
                    continue
                if "stock_code" not in disc.columns or "report_nm" not in disc.columns:
                    continue
                target = disc[disc["stock_code"] == stock_code]
                if target.empty:
                    continue
                for _, row in target.iterrows():
                    report_nm = str(row.get("report_nm", ""))
                    if any(kw in report_nm for kw in FINANCIAL_KEYWORDS):
                        print(f"  [DART] {stock_code} 재무관련 공시 발견: {report_nm}")
                        return True
        except Exception as e:
            logger.warning(f"[DART] {stock_code} 재무공시 확인 오류: {e}")
        return False

    # ──────────────────────────────────────────────────────────────────────
    # 주가/수급 수집
    # ──────────────────────────────────────────────────────────────────────

    def collect_prices(self, stock_code: str, force_history: bool = False, skip_if_today: bool = False):
        from datetime import datetime as _dt
        if _dt.now().weekday() >= 5 and not force_history:
            return  # 주말 수집 스킵
        # skip_if_today=True: 오늘 데이터 이미 있으면 스킵 (자정배치 중복 방지)
        if skip_if_today and not force_history and self._price_exists_today(stock_code):
            return
        """
        주가 / 수급 / 거래량 수집.
        force_history=True: 1년치 히스토리 전체 수집 (최초 등록 시)
        force_history=False: 오늘 or 최근 1일치만 수집 (일반 업데이트)
        """
        try:
            prices = []

            if stock_code.isdigit() and len(stock_code) == 6:
                # ── KIS 실시간 (당일 현재가) ──────────────────
                kis_data   = self.kis.get_current_price(stock_code)
                kis_trends = self.kis.get_investor_trends(stock_code)
                if kis_data:
                    _d = kis_data["date"]
                    _d_str = _d.isoformat() if hasattr(_d, "isoformat") else str(_d)[:10]
                    prices.append({
                        "date":         _d_str,
                        "open":         kis_data["open"],
                        "high":         kis_data["high"],
                        "low":          kis_data["low"],
                        "close":        kis_data["close"],
                        "volume":       kis_data["volume"],
                        "inst_net_buy": kis_trends["inst_net_buy"] if kis_trends else 0.0,
                        "frn_net_buy":  kis_trends["frn_net_buy"]  if kis_trends else 0.0,
                    })

            if not prices or force_history:
                # ── Yahoo Finance (히스토리 or KIS 실패 시 fallback) ──
                period   = "1y" if force_history else "5d"
                interval = "1d"
                for suffix in [".KS", ".KQ"]:
                    ticker_symbol = f"{stock_code}{suffix}"
                    df = yf.download(ticker_symbol, period=period, interval=interval, progress=False)
                    if not df.empty:
                        for index, row in df.iterrows():
                            def _gv(col, _row=row):
                                v = _row[col]
                                return float(v.iloc[0] if isinstance(v, pd.Series) else v)
                            prices.append({
                                "date":         str(index),
                                "open":         _gv("Open"),
                                "high":         _gv("High"),
                                "low":          _gv("Low"),
                                "close":        _gv("Close"),
                                "volume":       _gv("Volume"),
                                "inst_net_buy": 0.0,
                                "frn_net_buy":  0.0,
                            })
                        break

            if prices:
                httpx.post(
                    f"{self.base_url}/api/ingest/market-price",
                    json={"stock_code": stock_code, "prices": prices},
                    timeout=15,
                )
                print(f"  [주가] {stock_code} — {len(prices)}건 저장")
            else:
                print(f"  [주가] {stock_code} — 데이터 없음 (장 마감 또는 API 오류)")

            # ── KIS 수급 30거래일 bulk ──────────────────────
            if stock_code.isdigit() and len(stock_code) == 6:
                self._update_investor_trends_bulk(stock_code)

        except Exception as e:
            print(f"  [주가] {stock_code} 오류: {e}")

    def _update_investor_trends_bulk(self, stock_code: str):
        """KIS 최근 30거래일 수급 일괄 업데이트."""
        try:
            trends = self.kis.get_investor_trends_bulk(stock_code)
            if not trends:
                return
            valid = [t for t in trends if t["inst_net_buy"] != 0 or t["frn_net_buy"] != 0]
            if not valid:
                return
            httpx.post(
                f"{self.base_url}/api/ingest/investor-trends",
                json={"stock_code": stock_code, "trends": valid},
                timeout=10,
            )
            print(f"  [수급] {stock_code} — {len(valid)}일치 업데이트")
        except Exception as e:
            print(f"  [수급] {stock_code} 오류: {e}")


    # ──────────────────────────────────────────────────────────────────────
    # 매크로 수집
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _is_kr_trading_day(cls, d: date | None = None) -> bool:
        """한국 증시 영업일 여부 (주말·공휴일 False). trading_calendar 모듈 위임."""
        from trading_calendar import is_kr_trading_day as _tc
        return _tc(d or date.today())

    def collect_macro_data(self):
        """
        매크로 지표 수집 (KIS 우선, Yahoo Finance fallback).

        수집 항목:
          · KOSPI / KOSDAQ : KIS FHPUP02100000 (지수 현재가) + FHKUP03010100 (수급)
          · VIX / GOLD / OIL / USD/KRW : Yahoo Finance
        ⚠️ 주말·공휴일에는 DB 덮어쓰기 안 함 (is_kr_trading_day 체크)
        """
        from datetime import date as _date
        today     = datetime.now().date()
        today_iso = today.isoformat()
        is_kr_open = self._is_kr_trading_day(today)

        print("  [매크로] 지표 수집 시작")
        _krx = None  # KRX 수급 보완 (현재 차단 상태 — 항상 None)

        # ── KOSPI / KOSDAQ : KIS 실시간 ──────────────────────────
        index_map = {
            "0001": ("^KS11", "KOSPI"),
            "1001": ("^KQ11", "KOSDAQ"),
        }
        for index_code, (symbol, name) in index_map.items():
            if not is_kr_open:
                continue
            try:
                # ─ KIS 지수 현재가 (키워드 인자 수정: index_code 위치 인수) ─
                price  = self.kis.get_index_price(index_code)
                time.sleep(1.1)
                supply = self.kis.get_index_investor(index_code)
                time.sleep(1.1)

                if price and price.get("value"):
                    inst = supply["inst_net_buy"] if supply else 0.0
                    frn  = supply["frn_net_buy"]  if supply else 0.0
                    ind  = supply.get("ind_net_buy", -(inst+frn)) if supply else -(inst+frn)
                    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    row  = {
                        "date":         now_str,
                        "open":         price["open"],
                        "high":         price["high"],
                        "low":          price["low"],
                        "close":        price["value"],
                        "volume":       price["volume"],
                        "inst_net_buy": inst,
                        "frn_net_buy":  frn,
                    }
                    httpx.post(f"{self.base_url}/api/ingest/market-price",
                               json={"stock_code": symbol, "prices": [row]}, timeout=10)
                    print(f"  [KIS지수] {name}: {price['value']:,.2f} "
                          f"기관={inst:+,.0f} 외국인={frn:+,.0f} 개인={ind:+,.0f}")

                    # KIS 수급이 0이면 KRX → 네이버 순으로 보완
                    if inst == 0 and frn == 0:
                        sup_done = False
                        # 1순위: KRX 투자자별
                        if _krx:
                            try:
                                krx_sup = _krx.get_index_investor(name.lower())
                                if krx_sup and (krx_sup.get("frn",0)!=0 or krx_sup.get("inst",0)!=0):
                                    httpx.post(f"{self.base_url}/api/ingest/investor-trends",
                                        json={"stock_code": symbol, "trends": [{
                                            "date": today_iso,
                                            "inst_net_buy": krx_sup["inst"],
                                            "frn_net_buy":  krx_sup["frn"],
                                        }]}, timeout=10)
                                    print(f"  [KRX수급] {name}: 기관={krx_sup['inst']:+,.1f} 외국인={krx_sup['frn']:+,.1f}")
                                    sup_done = True
                            except Exception as e_krx:
                                logger.debug(f"[KRX수급] {name}: {e_krx}")
                        # 2순위: 네이버
                        if not sup_done:
                            print(f"  [KIS지수] {name}: 수급=0 → 네이버 보완")
                            naver_sup = self._scrape_naver_investor(name)
                            if naver_sup:
                                httpx.post(f"{self.base_url}/api/ingest/investor-trends",
                                    json={"stock_code": symbol, "trends": [{
                                        "date": today_iso,
                                        "inst_net_buy": naver_sup["inst"],
                                        "frn_net_buy":  naver_sup["frn"],
                                    }]}, timeout=10)
                                print(f"  [네이버수급] {name}: 기관={naver_sup['inst']:+,.0f} 외국인={naver_sup['frn']:+,.0f}")
                else:
                    print(f"  [KIS지수] {name}: KIS 실패 → Yahoo+네이버 fallback")
                    self._collect_macro_yahoo(symbol, name, today_iso)
                    naver_sup = self._scrape_naver_investor(name)
                    if naver_sup:
                        httpx.post(f"{self.base_url}/api/ingest/investor-trends",
                            json={"stock_code": symbol, "trends": [{
                                "date": today_iso,
                                "inst_net_buy": naver_sup["inst"],
                                "frn_net_buy":  naver_sup["frn"],
                            }]}, timeout=10)
                        print(f"  [네이버수급] {name}: 기관={naver_sup['inst']:+,.0f} 외국인={naver_sup['frn']:+,.0f}")
            except Exception as e:
                logger.warning(f"  [KIS지수] {name} 오류: {e}")
                self._collect_macro_yahoo(symbol, name, today_iso)
                try:
                    naver_sup = self._scrape_naver_investor(name)
                    if naver_sup:
                        httpx.post(f"{self.base_url}/api/ingest/investor-trends",
                            json={"stock_code": symbol, "trends": [{
                                "date": today_iso,
                                "inst_net_buy": naver_sup["inst"],
                                "frn_net_buy":  naver_sup["frn"],
                            }]}, timeout=10)
                except Exception:
                    pass
        else:
            print(f"  [매크로] {today_iso} — 한국 휴장일. KOSPI/KOSDAQ 수집 스킵")

        # ── 글로벌 지표: Yahoo Finance ───────────────────────────
        global_symbols = {
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
            "JPYKRW=X": "JPY/KRW",
            "TWDKRW=X": "TWD/KRW",
            "EURKRW=X": "EUR/KRW",
            "HKDKRW=X": "HKD/KRW",
            "^IXIC":    "NASDAQ",   # 나스닥 종합
            "^GSPC":    "S&P500",   # S&P 500
            "^DJI":     "DJI",      # 다우존스
        }
        for symbol, name in global_symbols.items():
            self._collect_macro_yahoo(symbol, name, today_iso)

        print("  [매크로] 수집 완료")
        self._macro_collected_date = today


    def _scrape_naver_investor(self, market_name: str) -> dict | None:
        """
        네이버 금융 코스피/코스닥 수급 스크래핑.
        https://finance.naver.com/sise/sise_index.naver?code=KOSPI
        페이지 텍스트에서 "개인+7,270억", "외국인-19,863억", "기관+9,683억" 형식으로 직접 파싱.
        반환: { inst, frn, ind } (단위: 억원)
        """
        import requests as _req
        from bs4 import BeautifulSoup as _BS
        import re as _re

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://finance.naver.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        is_kospi = "KOSPI" in market_name.upper()
        code     = "KOSPI" if is_kospi else "KOSDAQ"

        # ── 1순위: 메인 페이지 텍스트 직접 파싱 ─────────────────
        try:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = _req.get(url, headers=headers, timeout=10)
            res.encoding = "euc-kr"
            soup = _BS(res.text, "html.parser")

            text_lines = [l.strip() for l in soup.get_text().split("\n") if l.strip()]

            result = {"inst": 0.0, "frn": 0.0, "ind": 0.0}

            def _parse_val(s: str) -> float:
                """'+7,270억' or '-19,863억' → float(억원)"""
                s = s.strip()
                neg = s.startswith("-") or "▼" in s
                # 숫자+콤마만 추출
                nums = _re.sub(r"[^0-9]", "", s)
                if not nums:
                    return 0.0
                v = float(nums)
                return -v if neg else v

            # 텍스트에서 "개인+X억", "외국인-X억", "기관+X억" 패턴 찾기
            for line in text_lines:
                if line.startswith("개인") and "억" in line:
                    result["ind"] = _parse_val(line[2:])
                elif line.startswith("외국인") and "억" in line:
                    result["frn"] = _parse_val(line[3:])
                elif line.startswith("기관") and "억" in line:
                    result["inst"] = _parse_val(line[2:])

            if result["frn"] != 0 or result["inst"] != 0:
                logger.info(
                    f"[네이버수급] {code}: "
                    f"기관={result['inst']:+,.1f} "
                    f"외국인={result['frn']:+,.1f} "
                    f"개인={result['ind']:+,.1f}억원"
                )
                return result

        except Exception as e:
            logger.debug(f"[네이버수급] {code}: {e}")

        # ── 2순위: investorDealTrendDay iframe ────────────────────
        try:
            sosok    = "0" if is_kospi else "1"
            url2     = f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=&sosok={sosok}"
            res2     = _req.get(url2, headers=headers, timeout=10)
            res2.encoding = "euc-kr"
            soup2    = _BS(res2.text, "html.parser")
            result2  = {"inst": 0.0, "frn": 0.0, "ind": 0.0}

            def _n(td):
                t   = td.get_text(strip=True).replace(",","").replace("+","").replace(" ","")
                neg = t.startswith("-") or "▼" in t
                t   = _re.sub(r"[^0-9.]", "", t)
                if not t:
                    return 0.0
                v = float(t)
                return -v if neg else v

            for table in soup2.select("table"):
                for row in table.select("tr"):
                    cols = row.select("td")
                    if len(cols) < 2:
                        continue
                    label    = cols[0].get_text(strip=True)
                    val_col  = cols[3] if len(cols) > 3 else cols[1]
                    if "개인" in label:
                        result2["ind"]  = _n(val_col)
                    elif "외국인" in label:
                        result2["frn"]  = _n(val_col)
                    elif "기관" in label:
                        result2["inst"] = _n(val_col)

            if result2["frn"] != 0 or result2["inst"] != 0:
                logger.info(
                    f"[네이버수급2] {code}: "
                    f"기관={result2['inst']:+,.1f} "
                    f"외국인={result2['frn']:+,.1f}억원"
                )
                return result2

        except Exception as e2:
            logger.debug(f"[네이버수급2] {code}: {e2}")

        logger.warning(f"[네이버수급] {code}: 수급 데이터 없음")
        return None


    def backfill_index_history(self):
        """
        코스피/코스닥 3년치 히스토리 백필.
        DB에 365일치 미만이면 Yahoo Finance에서 3년치(period=3y) 수집.
        장 마감 후 매일 최신 일봉도 저장.
        """
        from datetime import date as _date
        today_iso = datetime.now().date().isoformat()

        # KOSPI/KOSDAQ (KIS+Yahoo) + KOSDAQ150/NASDAQ/S&P500 (Yahoo 전용)
        index_targets = [
            ("^KS11",  "KOSPI"),
            ("^KQ11",  "KOSDAQ"),
            ("^KQ150", "KOSDAQ150"),  # KRX API 차단 대비 Yahoo 백업
            ("^IXIC",  "NASDAQ"),
            ("^GSPC",  "S&P500"),
        ]
        for symbol, name in index_targets:
            try:
                # DB 레코드 수 확인
                res = httpx.get(
                    f"{self.base_url}/api/dashboard/chart/{symbol}",
                    params={"days": 400}, timeout=5
                )
                db_count = len(res.json()) if res.status_code == 200 else 0

                # 365일치 미만이면 3년치 백필
                period = "3y" if db_count < 365 else "5d"
                print(f"  [지수히스토리] {name}({symbol}): DB={db_count}건 → period={period}")

                # KOSDAQ150(ˆKQ150)는 Yahoo 미지원 → pykrx (KRX 공식 라이브러리)로 대체
                if symbol == "^KQ150":
                    self._backfill_kq150_pykrx(today_iso)
                    continue

                df = yf.download(symbol, period=period, interval="1d",
                                 progress=False, auto_adjust=True)
                if df is None or df.empty:
                    print(f"  [지수히스토리] {name}: Yahoo 응답 없음")
                    # 네이버 fallback (나스닥/S&P500)
                    if symbol in self._NAVER_WORLD_SYMBOL:
                        self._collect_macro_naver_world(symbol, name, today_iso)
                    continue

                if hasattr(df.columns, 'get_level_values'):
                    try: df.columns = df.columns.get_level_values(0)
                    except Exception: pass  # yfinance 멀티인덱스 이미 단층인 경우

                def gv(col, r):
                    v = r.get(col,0) if hasattr(r,'get') else getattr(r,col,0)
                    if isinstance(v, pd.Series): v = v.iloc[0]
                    try: return float(v) if v is not None else 0.0
                    except: return 0.0

                prices = []
                for ts, row in df.iterrows():
                    try:
                        row_date = ts.date() if hasattr(ts,'date') else date.fromisoformat(str(ts)[:10])
                        prices.append({
                            "date":         row_date.isoformat(),
                            "open":         gv("Open", row),
                            "high":         gv("High", row),
                            "low":          gv("Low",  row),
                            "close":        gv("Close",row),
                            "volume":       gv("Volume",row),
                            "inst_net_buy": 0.0,
                            "frn_net_buy":  0.0,
                        })
                    except Exception as e2:
                        logger.debug(f"[지수히스토리] {symbol} 행 파싱: {e2}")

                if not prices:
                    continue

                # ⚠️ 한국 휴장일에는 오늘 날짜 데이터 복사 금지
                kr_syms = {"^KS11", "^KQ11", "^KS200", "^KQ150"}
                if symbol in kr_syms and not self._is_kr_trading_day():
                    prices = [p for p in prices if p["date"] != today_iso]

                if not prices:
                    continue

                httpx.post(f"{self.base_url}/api/ingest/market-price",
                           json={"stock_code": symbol, "prices": prices}, timeout=30)
                print(f"  [지수히스토리] {name}: {len(prices)}건 저장 완료")

            except Exception as e:
                logger.warning(f"[지수히스토리] {symbol} 오류: {e}")

    def _backfill_kq150_pykrx(self, today_iso: str) -> None:
        """
        pykrx로 KOSDAQ150 지수 일별 데이터 수집.
        KRX 코스닥150 지수코드: 2150
        KOSPI200 수집과 동일한 방식으로 적용.
        """
        try:
            from pykrx import stock as _pykrx
            from datetime import datetime as _dt, timedelta as _td

            # 90일치 수집 (최신 데이터 채우기)
            end_dt   = _dt.now()
            start_dt = end_dt - _td(days=90)
            fromdate = start_dt.strftime("%Y%m%d")
            todate   = end_dt.strftime("%Y%m%d")

            # KRX KOSDAQ150 지수 코드 = 2150
            df = _pykrx.get_index_ohlcv_by_date(fromdate, todate, "2150")
            if df is None or df.empty:
                print(f"  [지수히스토리] KOSDAQ150: pykrx 데이터 없음")
                return

            prices = []
            for ts, row in df.iterrows():
                date_iso = ts.strftime("%Y-%m-%d") if hasattr(ts, 'strftime') else str(ts)[:10]
                close = float(row.get("종가", row.get("Close", 0)) or 0)
                if close <= 0:
                    continue
                prices.append({
                    "date":         date_iso,
                    "open":         float(row.get("시가", row.get("Open", close)) or close),
                    "high":         float(row.get("고가", row.get("High", close)) or close),
                    "low":          float(row.get("저가", row.get("Low",  close)) or close),
                    "close":        close,
                    "volume":       float(row.get("거래량", row.get("Volume", 0)) or 0),
                    "inst_net_buy": 0.0,
                    "frn_net_buy":  0.0,
                })

            if not prices:
                print(f"  [지수히스토리] KOSDAQ150: 유효 데이터 없음")
                return

            # ⚠️ 한국 휴장일에는 오늘 날짜 복사 금지
            if not self._is_kr_trading_day():
                prices = [p for p in prices if p["date"] != today_iso]

            httpx.post(f"{self.base_url}/api/ingest/market-price",
                       json={"stock_code": "^KQ150", "prices": prices}, timeout=30)
            latest = max(p['date'] for p in prices)
            print(f"  [지수히스토리] KOSDAQ150(pykrx): {len(prices)}건 저장 완료 (최신={latest})")


        except ImportError:
            print(f"  [지수히스토리] KOSDAQ150: pykrx 미설치 (pip install pykrx)")
        except Exception as e:
            logger.warning(f"[지수히스토리] KOSDAQ150 pykrx 오류: {e}")


    def backfill_portfolio_investor(self):
        """
        보유 종목 전체 1년치 수급 데이터 강제 수집.
        KIS inquire-investor-bulk로 최근 30거래일 → 부족하면 반복 수집으로 1년치 채움.
        """
        try:
            res = httpx.get(f"{self.base_url}/api/portfolio", timeout=5)
            if res.status_code != 200:
                return
            holdings = res.json()
            codes = [h["stock_code"] for h in holdings
                     if h.get("stock_code","").isdigit() and len(h["stock_code"]) == 6]
            print(f"  [수급백필] 보유종목 {len(codes)}개 1년치 수급 수집 시작")

            for i, code in enumerate(codes, 1):
                try:
                    # KIS bulk (최근 30거래일치)
                    trends = self.kis.get_investor_trends_bulk(code)
                    if trends:
                        valid = [t for t in trends if t["inst_net_buy"] != 0 or t["frn_net_buy"] != 0]
                        if valid:
                            httpx.post(f"{self.base_url}/api/ingest/investor-trends",
                                       json={"stock_code": code, "trends": valid}, timeout=10)
                            print(f"  [수급백필] [{i}/{len(codes)}] {code}: {len(valid)}건")
                        else:
                            print(f"  [수급백필] [{i}/{len(codes)}] {code}: 수급 데이터 없음")
                    time.sleep(1.1)
                except Exception as e:
                    logger.warning(f"[수급백필] {code}: {e}")
                    time.sleep(1.1)

            print(f"  [수급백필] 완료 ({len(codes)}종목)")
        except Exception as e:
            logger.warning(f"[수급백필] 전체 오류: {e}")


    # ── 네이버 해외지수 심볼 매핑 ───────────────────────────────────
    _NAVER_WORLD_SYMBOL = {
        "^IXIC": "NAS",   # NASDAQ Composite
        "^GSPC": "SPI",   # S&P 500
        "^DJI":  "DJI",   # Dow Jones
    }

    def _collect_macro_naver_world(self, symbol: str, name: str, today_iso: str) -> bool:
        """
        네이버 증권 해외지수 API로 나스닥/S&P500 등 수집.
        Yahoo Finance가 차단된 환경에서 fallback으로 사용.
        반환: 성공 여부(bool)
        """
        naver_sym = self._NAVER_WORLD_SYMBOL.get(symbol)
        if not naver_sym:
            return False

        import requests as _req
        from datetime import datetime as _dt, timedelta as _td

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://finance.naver.com/",
        }

        try:
            # ── 1. 현재가 (현재 시세) ─────────────────────────────
            url_now = f"https://finance.naver.com/world/sise.nhn?symbol={naver_sym}"
            r_now = _req.get(url_now, headers=headers, timeout=10)
            r_now.encoding = "euc-kr"

            from bs4 import BeautifulSoup as _BS
            soup = _BS(r_now.text, "html.parser")

            # 현재가 파싱: <span class="num">25,970.1</span>
            close_val = None
            change_val = None  # 전일 대비 등락률 (%)

            price_tag = soup.select_one("span.num")
            if price_tag:
                try:
                    close_val = float(price_tag.get_text(strip=True).replace(",", ""))
                except Exception:
                    pass

            # 등락률
            rate_tags = soup.select("span.num2")
            for tag in rate_tags:
                txt = tag.get_text(strip=True).replace(",", "").replace("%", "")
                try:
                    change_val = float(txt)
                    break
                except Exception:
                    pass

            # ── 2. 히스토리 (일봉 차트 API) ──────────────────────
            end_dt   = _dt.now()
            start_dt = end_dt - _td(days=100)
            start_str = start_dt.strftime("%Y%m%d") + "000000"
            end_str   = end_dt.strftime("%Y%m%d") + "235959"

            url_chart = (
                f"https://api.stock.naver.com/chart/world/{naver_sym}/day"
                f"?startDateTime={start_str}&endDateTime={end_str}"
            )
            r_chart = _req.get(url_chart, headers=headers, timeout=15)
            chart_data = r_chart.json() if r_chart.status_code == 200 else []

            prices = []
            if isinstance(chart_data, list):
                for item in chart_data:
                    try:
                        # 형식: {"localDate":"20260508","closePrice":25970.39,...}
                        dt_str = str(item.get("localDate", ""))
                        if len(dt_str) != 8:
                            continue
                        d_iso = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]}"
                        c = float(item.get("closePrice") or item.get("close") or 0)
                        o = float(item.get("openPrice")  or item.get("open")  or c)
                        h = float(item.get("highPrice")  or item.get("high")  or c)
                        l = float(item.get("lowPrice")   or item.get("low")   or c)
                        v = float(item.get("accTradeVolume") or item.get("volume") or 0)
                        if c <= 0:
                            continue
                        prices.append({
                            "date":         d_iso,
                            "open":         o,
                            "high":         h,
                            "low":          l,
                            "close":        c,
                            "volume":       v,
                            "inst_net_buy": 0.0,
                            "frn_net_buy":  0.0,
                        })
                    except Exception as ep:
                        logger.debug(f"[Naver차트] {naver_sym} 항목 파싱: {ep}")

            # 데이터 무결성: 차트 히스토리 없이 현재가 단독 저장 금지
            # (네이버 world 페이지 파싱 값이 지수 본값이 아닐 때 오염 가능)
            if not prices:
                logger.warning(f"[Naver] {name}({naver_sym}): 차트 히스토리 없음 — 단독 현재가 저장 생략")
                return False

            if not prices:
                logger.warning(f"[Naver] {name}({naver_sym}): 데이터 없음")
                return False

            httpx.post(
                f"{self.base_url}/api/ingest/market-price",
                json={"stock_code": symbol, "prices": prices},
                timeout=15,
            )
            latest = max(prices, key=lambda x: x["date"])
            print(f"  [Naver] {name}({naver_sym}): {latest['close']:,.2f} ({latest['date']}) {len(prices)}건")
            return True

        except Exception as e:
            logger.warning(f"[Naver] {name}({naver_sym}) 오류: {e}")
            return False

    def _macro_prev_close(self, symbol: str) -> float | None:
        """최근 저장값(직전 종가) 조회."""
        try:
            res = httpx.get(
                f"{self.base_url}/api/dashboard/chart/{symbol}",
                params={"days": 7},
                timeout=5,
            )
            if res.status_code != 200:
                return None
            arr = res.json()
            if not isinstance(arr, list) or not arr:
                return None
            # 최신값이 오늘일 수도 있어 직전값 우선 탐색
            vals = [x for x in arr if isinstance(x, dict) and x.get("close")]
            if not vals:
                return None
            return float(vals[-1]["close"])
        except Exception:
            return None

    def _macro_spike_threshold(self, symbol: str) -> float:
        """심볼별 급변 허용치(절대 %)."""
        if symbol in ("^IXIC", "^GSPC", "^KS11", "^KQ11", "^KS200", "^KQ150"):
            return 12.0
        if symbol in ("^TNX", "^TYX", "2YY=F", "10Y=F", "30Y=F", "^UST2Y"):
            return 6.0
        if symbol == "^VIX":
            return 25.0
        return 15.0

    def _fetch_naver_world_latest(self, symbol: str) -> float | None:
        """네이버 해외지수 차트의 최신 종가 1건 조회(검증용)."""
        naver_sym = self._NAVER_WORLD_SYMBOL.get(symbol)
        if not naver_sym:
            return None
        try:
            import requests as _req
            from datetime import datetime as _dt, timedelta as _td
            end_dt = _dt.now()
            start_dt = end_dt - _td(days=20)
            url_chart = (
                f"https://api.stock.naver.com/chart/world/{naver_sym}/day"
                f"?startDateTime={start_dt.strftime('%Y%m%d')}000000"
                f"&endDateTime={end_dt.strftime('%Y%m%d')}235959"
            )
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.naver.com/",
            }
            r = _req.get(url_chart, headers=headers, timeout=10)
            if r.status_code != 200:
                return None
            arr = r.json()
            if not isinstance(arr, list) or not arr:
                return None
            last = arr[-1]
            c = float(last.get("closePrice") or last.get("close") or 0)
            return c if c > 0 else None
        except Exception:
            return None

    def _collect_macro_yahoo(self, symbol: str, name: str, today_iso: str):
        """
        Yahoo Finance로 단일 매크로 심볼 수집 + 오늘 날짜 강제 upsert.
        DB에 30일치 미만이면 60d로 히스토리 백필.
        Yahoo 실패 시 네이버 해외지수 API로 자동 fallback (나스닥/S&P500).
        """
        try:
            # DB에 해당 심볼 레코드 수 확인 → 부족하면 60d 수집
            try:
                res_check = httpx.get(
                    f"{self.base_url}/api/dashboard/chart/{symbol}",
                    params={"days": 35}, timeout=5
                )
                db_count = len(res_check.json()) if res_check.status_code == 200 else 0
            except Exception:
                db_count = 0
            period = "60d" if db_count < 20 else "5d"

            def _download(_period: str):
                _df = yf.download(symbol, period=_period, interval="1d",
                                  progress=False, auto_adjust=True)
                if _df is None or _df.empty:
                    return []
                if hasattr(_df.columns, 'get_level_values'):
                    try:
                        _df.columns = _df.columns.get_level_values(0)
                    except Exception:
                        pass

                def gv(col, r):
                    v = r.get(col, 0) if hasattr(r, 'get') else getattr(r, col, 0)
                    if isinstance(v, pd.Series):
                        v = v.iloc[0]
                    try:
                        return float(v) if v is not None else 0.0
                    except Exception:
                        return 0.0

                _prices = []
                for ts, row in _df.iterrows():
                    try:
                        row_date = ts.date() if hasattr(ts, 'date') else date.fromisoformat(str(ts)[:10])
                        _prices.append({
                            "date":         row_date.isoformat(),
                            "open":         gv("Open",   row),
                            "high":         gv("High",   row),
                            "low":          gv("Low",    row),
                            "close":        gv("Close",  row),
                            "volume":       gv("Volume", row),
                            "inst_net_buy": 0.0,
                            "frn_net_buy":  0.0,
                        })
                    except Exception as e2:
                        logger.debug(f"[Yahoo] {symbol} 행 파싱: {e2}")
                return _prices

            prices = _download(period)
            if not prices:
                print(f"  [Yahoo] {name}({symbol}): 데이터 없음 → 네이버 fallback 시도")
                self._collect_macro_naver_world(symbol, name, today_iso)
                return

            latest = max(prices, key=lambda x: x["date"])
            latest_close = float(latest.get("close") or 0.0)
            prev_close = self._macro_prev_close(symbol)
            th = self._macro_spike_threshold(symbol)

            # 1) 직전값 대비 급변 → Yahoo 재파싱 1회
            if prev_close and latest_close > 0:
                diff_pct = abs((latest_close - prev_close) / prev_close * 100.0)
                if diff_pct >= th:
                    logger.warning(
                        f"[MacroGuard] {symbol}: 급변 감지 {diff_pct:.2f}% (prev={prev_close:.4f}, new={latest_close:.4f}) -> 재파싱"
                    )
                    prices_retry = _download("10d")
                    if prices_retry:
                        latest_retry = max(prices_retry, key=lambda x: x["date"])
                        retry_close = float(latest_retry.get("close") or 0.0)
                        retry_diff_pct = abs((retry_close - prev_close) / prev_close * 100.0) if prev_close else 0.0
                        if retry_close > 0 and retry_diff_pct < diff_pct:
                            prices = prices_retry
                            latest = latest_retry
                            latest_close = retry_close
                            diff_pct = retry_diff_pct
                    # 재파싱 후에도 급변이면(특히 해외지수) 2차 소스 교차검증
                    if diff_pct >= th and symbol in self._NAVER_WORLD_SYMBOL:
                        naver_close = self._fetch_naver_world_latest(symbol)
                        if naver_close and naver_close > 0:
                            cross_gap = abs((latest_close - naver_close) / naver_close * 100.0)
                            if cross_gap >= 2.5:
                                logger.warning(
                                    f"[MacroGuard] {symbol}: Yahoo↔Naver 괴리 {cross_gap:.2f}% -> Naver 데이터로 대체"
                                )
                                self._collect_macro_naver_world(symbol, name, today_iso)
                                return
                    # 여전히 이상치면 저장 스킵
                    if diff_pct >= th:
                        logger.error(
                            f"[MacroGuard] {symbol}: 급변 재검증 실패(diff={diff_pct:.2f}%, th={th}%) 저장 스킵"
                        )
                        return

            # ⚠️ 오늘이 한국 휴장일이면 오늘 날짜 entry 생성 금지
            # 오늘 실제 Yahoo 데이터가 없으면 최신 날짜 그대로 사용 (복사 금지)
            kr_symbols = {"^KS11", "^KQ11", "^KS200", "^KQ150"}
            is_kr      = symbol in kr_symbols
            if is_kr and not self._is_kr_trading_day():
                # 한국 휴장일: 오늘 날짜 데이터 제거 (이미 DB에 있어도 갱신 안 함)
                prices = [p for p in prices if p["date"] != today_iso]

            httpx.post(f"{self.base_url}/api/ingest/market-price",
                       json={"stock_code": symbol, "prices": prices}, timeout=10)
            latest = max(prices, key=lambda x: x["date"])
            print(f"  [Yahoo] {name}({symbol}): {latest['close']:,.2f} ({latest['date'][:10]})")
        except Exception as e:
            logger.warning(f"  [Yahoo] {symbol} 오류: {e}")
            # Yahoo 예외 시에도 네이버 fallback
            if symbol in self._NAVER_WORLD_SYMBOL:
                print(f"  [Yahoo→Naver] {name}: Yahoo 예외 → 네이버 fallback")
                self._collect_macro_naver_world(symbol, name, today_iso)

    def collect_fundamentals(self, stock_code: str, latest_only: bool = False):
        """
        DART 재무제표 수집.
        latest_only=False: 2015년~현재 전체 (최초 등록 시)
        latest_only=True : 최근 2개 분기만 확인 (당일 공시 업데이트 시)
        """
        if self.dart is None:
            print(f"  [재무] DART 초기화 실패 — {stock_code} 스킵")
            return
        try:
            latest_y, latest_q = self._get_latest_disclosed_quarter()
            quarter_map = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
            target_years = [latest_y, latest_y - 1] if latest_only else list(range(latest_y, 2014, -1))

            total_new = total_skip = 0
            for target_year in target_years:
                for rprt_code, quarter_num in quarter_map.items():
                    if target_year > latest_y or (target_year == latest_y and quarter_num > latest_q):
                        continue
                    is_annual_flag = (rprt_code == "11011")
                    if self._financial_exists(
                        stock_code,
                        target_year,
                        quarter_num,
                        is_annual=is_annual_flag,
                        data_source="dart",
                    ):
                        total_skip += 1
                        continue

                    # ── Rate limiter: finstate 호출마다 쿼터 차감 ──
                    if _USE_RL and not _rl.wait("DART"):
                        logger.warning(f"[재무] DART 일일 쿼터 소진 — {stock_code} 재무수집 중단")
                        return  # 오늘치 쿼터 소진 → 루프 전체 중단

                    fn_data = None
                    for fs_div in ["CFS", "OFS"]:
                        try:
                            df = self.dart.finstate_all(stock_code, target_year, rprt_code, fs_div=fs_div)
                            if df is not None and not df.empty:
                                fn_data = df; break
                        except Exception:
                            pass
                        if _USE_RL:
                            _rl.wait("DART")  # CFS/OFS 각각 호출 차감
                    if fn_data is None or fn_data.empty:
                        if _USE_RL and not _rl.wait("DART"):
                            return
                        try:
                            fn_data = self.dart.finstate(stock_code, target_year, rprt_code)
                        except Exception:
                            pass
                    if fn_data is None or fn_data.empty:
                        continue

                    self._save_financial_data(
                        stock_code, target_year, quarter_num, is_annual_flag, fn_data, report_type=fs_div
                    )
                    total_new += 1

                if latest_only and total_new >= 2:
                    break  # 당일 공시 업데이트는 최근 2개 분기면 충분

            print(f"  [재무] {stock_code} — 신규 {total_new}건 / 스킵 {total_skip}건")
        except Exception as e:
            print(f"  [재무] {stock_code} 오류: {e}")

    def _save_financial_data(self, stock_code, year, quarter, is_annual, fn_data, report_type: str = "CFS"):
        """파싱된 재무 데이터 API로 저장."""
        prof = get_profile(stock_code)
        extra_rev = prof.get("revenue_keywords", [])
        extra_ni = prof.get("net_income_keywords", [])
        ni_excludes = tuple(prof.get("net_income_exclude_keywords", []))
        extra_eps = prof.get("eps_keywords", [])

        m = {
            "revenue": None, "operating_profit": None, "net_income": None,
            "total_assets": None, "total_liabilities": None, "total_equity": None,
            "capital_stock": None, "eps": None, "bps": None, "dps": None,
            "cash": None, "total_shares": None, "depreciation_amortization": None,
        }
        for _, row in fn_data.iterrows():
            acc = str(row.get("account_nm", "")).replace(" ", "")
            val_col = "thstrm_amount"
            if not acc or val_col not in row or pd.isna(row[val_col]):
                continue
            try:
                val = float(str(row[val_col]).replace(",", ""))
            except ValueError:
                continue
            _ni_kw = ("당기순이익" in acc or "분기순이익" in acc or "반기순이익" in acc or "당기순손익" in acc)
            if any(k in acc for k in (["매출액", "영업수익", "매출"] + extra_rev)):
                if val != 0 or m["revenue"] is None: m["revenue"] = val
            elif "영업이익" in acc:
                if val != 0 or m["operating_profit"] is None: m["operating_profit"] = val
            elif (_ni_kw or any(k in acc for k in extra_ni)) and "주당" not in acc and "지배" not in acc and not any(x in acc for x in ni_excludes):
                if val != 0 or m["net_income"] is None: m["net_income"] = val
            elif "자산총계" in acc:
                if val != 0 or m["total_assets"] is None: m["total_assets"] = val
            elif "부채총계" in acc:
                if val != 0 or m["total_liabilities"] is None: m["total_liabilities"] = val
            elif "자본총계" in acc:
                if val != 0 or m["total_equity"] is None: m["total_equity"] = val
            elif "자본금" in acc:
                if val != 0 or m["capital_stock"] is None: m["capital_stock"] = val
            elif any(k in acc for k in ["기본주당순이익","주당순이익","기본EPS"] + extra_eps):
                if val != 0 or m["eps"] is None: m["eps"] = val
            elif any(k in acc for k in ["주당순자산","1주당순자산가액"]):
                if val != 0 or m["bps"] is None: m["bps"] = val
            elif any(k in acc for k in ["주당배당금","주당현금배당금"]):
                if val != 0 or m["dps"] is None: m["dps"] = val
            elif any(k in acc for k in ["현금및현금성자산","현금성자산"]):
                if val != 0 or m["cash"] is None: m["cash"] = val
            elif any(k in acc for k in ["발행주식수","유통주식수"]):
                if val != 0 or m["total_shares"] is None: m["total_shares"] = val
            elif "감가상각비" in acc:
                if val != 0 or m["depreciation_amortization"] is None: m["depreciation_amortization"] = val
        try:
            resp = httpx.post(
                f"{self.base_url}/api/ingest/fundamentals",
                json={
                    "stock_code": stock_code,
                    "year": year,
                    "quarter": quarter,
                    "is_annual": is_annual,
                    "report_type": report_type,
                    "data_source": "dart",
                    **m,
                },
                timeout=10,
            )
            resp.raise_for_status()
            self._fn_collected.add((stock_code, year, quarter))
            print(f"  [재무] {stock_code} {year}Q{quarter} 저장({report_type})")
        except Exception as e:
            print(f"  [재무] {stock_code} {year}Q{quarter} 저장 오류: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # ★ 온디맨드 수집 — 화면 조회 시 즉시 호출
    # ──────────────────────────────────────────────────────────────────────

    def run_ondemand(self, stock_code: str):
        """온디맨드: 주가(히스토리/당일) + 재무(공시기준)."""
        print(f"[온디맨드] {stock_code} 시작")
        is_kr = stock_code.isdigit() and len(stock_code) == 6

        has_history = self._has_price_history(stock_code)
        if not has_history:
            print(f"  [주가] {stock_code} 없음 -> 1년치")
            self.collect_prices(stock_code, force_history=True)
        elif not self._price_exists_today(stock_code):
            print(f"  [주가] {stock_code} 오늘없음 -> 당일")
            self.collect_prices(stock_code, force_history=False)
        else:
            print(f"  [주가] {stock_code} 오늘있음 -> 스킵")

        if is_kr:
            ly, lq = self._get_latest_disclosed_quarter()
            has_any    = self._has_any_financial(stock_code)
            has_latest = self._financial_exists(stock_code, ly, lq)
            if not has_any:
                print(f"  [재무] {stock_code} 없음 -> 전체수집")
                self.collect_fundamentals(stock_code, latest_only=False)
            elif not has_latest:
                print(f"  [재무] {stock_code} {ly}Q{lq} 없음 -> 최신")
                self.collect_fundamentals(stock_code, latest_only=True)
            else:
                print(f"  [재무] {stock_code} {ly}Q{lq} 있음 -> 스킵")
        print(f"[온디맨드] {stock_code} 완료")

    def _has_price_history(self, stock_code: str) -> bool:
        """DB에 주가 레코드가 1건이라도 있는지 확인."""
        try:
            res = httpx.get(f"{self.base_url}/api/dashboard/chart/{stock_code}",
                            params={"days": 30}, timeout=5)
            if res.status_code == 200:
                return len(res.json()) > 0
        except Exception:
            pass
        return False

    # ──────────────────────────────────────────────────────────────────────
    # ★ 자정 배치 — 매일 00:00 호출
    # ──────────────────────────────────────────────────────────────────────

    def collect_closing_investor(self):
        """
        장 마감 후 코스피/코스닥 당일 최종 수급 수집.
        ※ pykrx API가 더 이상 get_market_net_purchases_of_business_day를 지원하지 않음.
           지수 수급은 KIS collect_macro_data()에서 처리되므로 이 함수는 비활성화.
        """
        logger.debug("[수급] collect_closing_investor — 비활성화 (pykrx API 미지원)")
        return

    def run_nightly_batch(self, codes: list):
        """
        자정 배치 처리.

        처리 순서:
        1. 매크로 지표 수집 (오늘 DB 없으면)
        2. 전체 종목 주가/수급 수집 (매일 무조건)
        3. DART 당일 공시 있는 종목만 재무 업데이트

        Args:
            codes: watchlist + portfolio 전체 종목 코드 리스트
        """
        today = datetime.now().date().isoformat()
        print(f"\n{'='*60}")
        print(f"[자정 배치] {today} 시작 — 대상 {len(codes)}개 종목")
        print(f"{'='*60}")

        # Step 1: 매크로 + 수급
        print("\n[Step 1] 매크로 지표 수집")
        self.collect_macro_data()
        print("\n[Step 1-b] 장 마감 수급 수집 (pykrx)")
        self.collect_closing_investor()

        # Step 2: 전체 종목 주가/수급 (매일 무조건 기록)
        print(f"\n[Step 2] 주가/수급 수집 ({len(codes)}종목)")
        for i, code in enumerate(codes, 1):
            # 오늘 데이터 이미 있으면 스킵 (중복 방지)
            if self._price_exists_today(code):
                continue
            print(f"  [{i}/{len(codes)}] {code}")
            self.collect_prices(code, force_history=False)
            time.sleep(1.0)  # API 과부하 방지

        # Step 3: 재무 업데이트 (재무없는 종목 우선 전체수집, 공시종목 최신업데이트)
        print("\n[Step 3] 재무 업데이트")
        updated_fin = 0
        kr_codes = [c for c in codes if c.isdigit() and len(c) == 6]
        for code in kr_codes:
            if not self._has_any_financial(code):
                print(f"  [재무없음] {code} -> 전체수집")
                self.collect_fundamentals(code, latest_only=False)
                updated_fin += 1; time.sleep(2.0)
            elif self._has_dart_disclosure_today(code):
                print(f"  [공시] {code} -> 최신분기")
                self.collect_fundamentals(code, latest_only=True)
                updated_fin += 1; time.sleep(1.5)

        print(f"\n{'='*60}")
        print(f"[자정 배치] 완료")
        print(f"  · 주가/수급: {len(codes)}종목")
        print(f"  · 재무 업데이트: {updated_fin}종목 (공시 기준)")
        print(f"{'='*60}\n")

    # ──────────────────────────────────────────────────────────────────────
    # 레거시 호환 (기존 코드와 호환)
    # ──────────────────────────────────────────────────────────────────────

    def run_one_shot(self, stock_code: str):
        """레거시 호환용. run_ondemand와 동일."""
        self.run_ondemand(stock_code)

    def _collect_fundamentals_if_needed(self, stock_code: str):
        """
        상시 수집 모드 재무 조건부 수집.

        수집 조건 (OR):
          1. DB에 재무 데이터가 전혀 없는 종목  → 전체 수집
          2. 최신 공시 분기 데이터가 없는 종목  → 최신 수집
          3. 최근 7일 내 재무관련 공시가 있는 종목 → 최신 수집 (변동 공시 대응)
        """
        if not stock_code.isdigit() or len(stock_code) != 6:
            return
        ly, lq = self._get_latest_disclosed_quarter()

        if not self._has_any_financial(stock_code):
            print(f"  [재무] {stock_code} DB 없음 → 전체 수집")
            self.collect_fundamentals(stock_code, latest_only=False)
        elif not self._financial_exists(stock_code, ly, lq):
            print(f"  [재무] {stock_code} 최신분기({ly}Q{lq}) 없음 → 최신 수집")
            self.collect_fundamentals(stock_code, latest_only=True)
        elif self._has_dart_financial_disclosure(stock_code):
            print(f"  [재무] {stock_code} 재무관련 공시 감지 → 최신 수집")
            self.collect_fundamentals(stock_code, latest_only=True)


# ──────────────────────────────────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────────────────────────────────

def _acquire_daemon_lock() -> None:
    """데몬 모드 중복 실행 방지. 이미 실행 중인 인스턴스가 있으면 즉시 종료."""
    import atexit
    pid_path = "/Applications/stock_dashboard/logs/data_collector.pid"
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)

    if os.path.exists(pid_path):
        try:
            existing = int(open(pid_path).read().strip())
            os.kill(existing, 0)  # signal 0 = 프로세스 존재 여부만 확인
            print(f"[중복 실행 방지] PID {existing}가 이미 실행 중입니다. 종료합니다.")
            raise SystemExit(0)
        except (ProcessLookupError, ValueError):
            pass  # stale PID 파일 — 덮어쓰기 진행

    with open(pid_path, "w") as _f:
        _f.write(str(os.getpid()))

    atexit.register(lambda: os.unlink(pid_path) if os.path.exists(pid_path) else None)
    print(f"[시작] PID {os.getpid()} — {pid_path} 잠금 획득")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="주식 데이터 수집기")
    parser.add_argument("--stock", nargs="+", metavar="CODE",
                        help="즉시 수집할 종목코드 (예: --stock 005930)")
    parser.add_argument("--nightly", action="store_true",
                        help="자정 배치를 즉시 실행 (테스트용)")
    args = parser.parse_args()

    # 데몬 모드(상시 수집)만 중복 실행 방지 — --stock/--nightly 단발 모드는 허용
    if not args.stock and not args.nightly:
        _acquire_daemon_lock()

    collector = DataCollector(dart_api_key=config.DART_API_KEY)
    if not collector.wait_for_server():
        raise SystemExit("서버 연결 실패")

    # ── 즉시 수집 모드 ──────────────────────────────────────────
    if args.stock:
        print("=" * 55)
        print(f"즉시 수집 모드: {', '.join(args.stock)}")
        print("=" * 55)
        for code in args.stock:
            collector.run_ondemand(code.strip())
        print("\n✅ 완료. 브라우저를 새로고침하세요.")
        raise SystemExit(0)

    # ── 자정 배치 즉시 실행 (테스트) ───────────────────────────
    if args.nightly:
        res = httpx.get(f"{collector.base_url}/api/commands/watchlist", timeout=5)
        codes_wl = [item["stock_code"] for item in res.json()] if res.status_code == 200 else []
        res2 = httpx.get(f"{collector.base_url}/api/portfolio", timeout=5)
        codes_pf = [item["stock_code"] for item in res2.json()] if res2.status_code == 200 else []
        all_codes = list(set(codes_wl + codes_pf))
        collector.run_nightly_batch(all_codes)
        raise SystemExit(0)

    # ── 상시 수집 모드 (5분 주기) ───────────────────────────────
    cycle_delay = getattr(config, "COLLECT_CYCLE_DELAY", 300)
    print("=" * 60)
    print("상시 수집 모드 (재무 전용)")
    print(f"  · 재무      : {cycle_delay}초 주기 (watchlist + portfolio 합집합)")
    print(f"  · 주가/수급  : scheduler.py 담당 (중복 제거)")
    print(f"  · 매크로    : scheduler.py 담당 (중복 제거)")
    print("=" * 60)

    # 시작 시 지수 히스토리 백필 (3년치)
    print("  [초기화] 지수 히스토리 백필 시작...")
    try:
        collector.backfill_index_history()
        print("  [초기화] 지수 히스토리 백필 완료")
    except Exception as e_bf:
        print(f"  [초기화] 백필 오류: {e_bf}")

    # 보유종목 수급 1년치 백필
    print("  [초기화] 보유종목 수급 백필 시작...")
    try:
        collector.backfill_portfolio_investor()
    except Exception as e_pf:
        print(f"  [초기화] 수급 백필 오류: {e_pf}")

    while True:
        loop_start = time.time()
        try:
            # ── watchlist + portfolio 합집합 ──────────────────────
            res_wl = httpx.get(f"{collector.base_url}/api/commands/watchlist", timeout=5)
            res_pf = httpx.get(f"{collector.base_url}/api/portfolio", timeout=5)

            codes_wl = [item["stock_code"] for item in res_wl.json()] if res_wl.status_code == 200 else []
            codes_pf = [item["stock_code"] for item in res_pf.json()] if res_pf.status_code == 200 else []
            stock_codes = list(dict.fromkeys(codes_wl + codes_pf))  # 순서 유지 + 중복 제거

            if not stock_codes:
                print("[대기] 수집 대상 없음 (watchlist + portfolio 모두 비어있음)")
                time.sleep(30)
                continue

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 재무수집 시작 — watchlist {len(codes_wl)}종목 + portfolio {len(codes_pf)}종목 = 합계 {len(stock_codes)}종목")

            # ※ 주가/수급/매크로는 scheduler.py 잡에서 처리:
            #   - 장중1분가격  (_job_intraday_prices):  watchlist 현재가
            #   - 장중5분수급  (_job_intraday_investor): watchlist 수급
            #   - KRX일별      (_job_krx_daily):        전종목 OHLCV (18:00)
            #   - 전종목수급   (_job_supply_daily):     전종목 수급 (17:30)
            # ─ 이 루프는 재무 수집에만 집중 ─────────────────────
            kr_codes = [c for c in stock_codes if c.isdigit() and len(c) == 6]
            fin_updated = 0
            for i, stock_code in enumerate(kr_codes, 1):
                try:
                    collector._collect_fundamentals_if_needed(stock_code)
                    fin_updated += 1
                except Exception as e_fin:
                    logger.debug(f"[재무] {stock_code}: {e_fin}")
                time.sleep(0.5)

            print(f"  재무 수집 완료: {fin_updated}종목 처리")

        except Exception as e:
            print(f"[루프 오류] {e}")
            import traceback; traceback.print_exc()

        elapsed = time.time() - loop_start
        wait = max(0, cycle_delay - elapsed)
        next_t = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M:%S")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 주기 완료 ({elapsed:.0f}s) — 다음 실행: {next_t}")
        time.sleep(wait)
