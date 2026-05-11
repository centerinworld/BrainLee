"""
scheduler.py — 통합 수집 스케줄러

기존 main.py의 7개 daemon thread를 단일 CollectionScheduler로 교체:

  [기존]                        [신규]
  NightlyScheduler       →  job_nightly_batch()      00:10 daily
  MonthlyBulkUpdate      →  job_monthly_bulk()       1st 03:00 monthly
  DailyDisclosureCheck   →  job_disclosure_check()   03:30 daily
  MinutePriceLoop        →  job_intraday_prices()    매 1분 (장중)
  FiveMinInvestorLoop    →  job_intraday_investor()  매 5분 (장중)
  ClosingScheduler       →  job_closing()            15:40 daily
  ScreenerPrecompute     →  job_screener()           매 30분

사용법 (main.py):
    from scheduler import CollectionScheduler
    _scheduler = CollectionScheduler(db_factory=SessionLocal)
    _scheduler.start()          # lifespan startup에서 호출
    _scheduler.stop()           # lifespan shutdown에서 호출
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, date
from typing import Callable

import config
from db_utils import connect_stock_db, stock_db_write_lock

logger = logging.getLogger(__name__)

_DB_WRITE_JOBS = {
    "야간배치",
    "월간업데이트",
    "공시확인",
    "장중1분가격",
    "장중5분수급",
    "장마감",
    "스크리너사전계산",
    "스크리너",
    "공공데이터",
    "KIS일별수집",
    "KRX일별수집",
    "전종목수급17시",
    "전종목수급21시",
    "레이더해외가격",
    "네이버밸류에이션",
    "현금흐름배치",
    "텐버거오전",
    "텐버거정오",
    "텐버거오후",
    "HOT섹터블로그",
    "스탁이지분석",
    "스탁이지주간",
    "DART수주공시",
    "근로복지공단",
    "BigQuery동기화",
    "컨센서스수집",
    "캐치업수급",
    "캐치업KIS지수",
    "캐치업KRX지수",
    "KRX종목기본정보",
    "FnGuide재무월간",   # ★ 월간 연결/별도 재무제표 + 스냅샷
}

# 장중 시간 (KST)
_MARKET_OPEN  = (9,  0)
_MARKET_CLOSE = (15, 40)

from trading_calendar import is_kr_trading_day, is_trading_day  # noqa: E402


def _is_market_open() -> bool:
    """한국 장이 열려 있는지 확인 (주말 + 공휴일 포함)."""
    now = datetime.now()
    if not is_kr_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _seconds_until(hour: int, minute: int, skip_weekend: bool = True) -> float:
    """다음 HH:MM까지 남은 초. skip_weekend=True면 주말+한국 공휴일 건너뜀."""
    now    = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    if skip_weekend:
        while not is_kr_trading_day(target.date()):
            target += timedelta(days=1)
    return max(0.0, (target - datetime.now()).total_seconds())


def _run_job_safe(name: str, fn: Callable) -> None:
    """예외 격리 래퍼 — 한 잡이 실패해도 스케줄러 전체에 영향 없음."""
    try:
        if name in _DB_WRITE_JOBS:
            with stock_db_write_lock(name, timeout=3) as acquired:
                if not acquired:
                    logger.warning(f"[스케줄러] {name} 스킵 — 다른 DB writer 실행 중")
                    return
                logger.info(f"[스케줄러] {name} 시작")
                fn()
                logger.info(f"[스케줄러] {name} 완료")
            return
        logger.info(f"[스케줄러] {name} 시작")
        fn()
        logger.info(f"[스케줄러] {name} 완료")
    except Exception as e:
        logger.error(f"[스케줄러] {name} 오류: {e}", exc_info=True)


class CollectionScheduler:
    """
    전체 수집 주기를 관리하는 단일 스케줄러.
    각 잡은 독립 daemon thread에서 실행 (I/O 블로킹 허용).
    """

    def __init__(self, db_factory: Callable = None):
        self._db_factory = db_factory
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()

    # ══════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════

    def start(self) -> None:
        """서버 startup 시 호출 — 모든 스케줄 스레드 시작."""
        self._stop_event.clear()
        jobs = [
            ("야간배치",        self._loop_nightly),
            ("월간업데이트",    self._loop_monthly),
            ("공시확인",        self._loop_disclosure),
            ("장중1분가격",     self._loop_intraday_price),
            ("장중5분수급",     self._loop_intraday_investor),
            ("장마감",          self._loop_closing),
            ("스크리너사전계산", self._loop_screener),
            ("공공데이터",      self._loop_public_data),
            ("KIS일별수집",    self._loop_kis_daily),         # ★ KIS API 전종목 OHLCV (KRX 차단 대체)
            ("전종목수급17시",  self._loop_supply_daily),      # ★ 17:30 KIS 전종목 수급
            ("전종목수급21시",  self._loop_supply_evening),    # ★ 21:00 재갱신
            ("레이더해외가격",  self._loop_radar_price_update), # ★ 1시간마다 해외 yfinance
            ("네이버밸류에이션", self._loop_naver_fundamentals),  # ★ 네이버 PBR/PER/EPS
            ("현금흐름배치",    self._loop_cashflow_batch),       # ★ DART 현금흐름표 월간
            ("텐버거오전",      self._loop_tenbagger_morning),    # ★ 09:00 텐버거 발굴
            ("텐버거정오",      self._loop_tenbagger_noon),       # ★ 12:00 텐버거 발굴
            ("텐버거오후",      self._loop_tenbagger_afternoon),  # ★ 15:00 텐버거 발굴
            # ("NPS고용업데이트", self._loop_nps_daily),          # ⛔ 비활성: apis.data.go.kr DNS 차단 + 연간 총인원은 월별차이 없어 신호 불필요
            ("미국지수수집",    self._loop_us_indices),           # ★ 매일 06:30 나스닥/S&P500 수집
            ("HOT섹터블로그",   self._loop_sector_blog),          # ★ 매일 07:00 블로그 신규 포스트 파싱
            ("AI주도섹터",      self._loop_ai_leading_sector),    # ★ 매일 07:20 미국 증시 기반 주도 섹터 판독
            ("섹터오전텔레그램", self._loop_sector_morning_tg),    # ★ 매일 08:30 섹터 AI 리포트 텔레그램
            ("섹터점심텔레그램", self._loop_sector_lunch_tg),      # ★ 매일 12:30 오전장 섹터 분석 텔레그램
            ("스탁이지분석",    self._loop_stockeasy_analysis),   # ★ 매일 16:30 스탁이지 전략 분석
            ("스탁이지주간",    self._loop_stockeasy_weekly),     # ★ 매주 일요일 09:00 주간 요약
            # ("고용보험배치",  self._loop_insurance_monthly),    # ⛔ 비활성: 연간 총인원 수집 — 월별차이 없어 의미 없음 (사용자 요청)
            ("DART수주공시",   self._loop_dart_contracts),        # ★ 매일 08:00/13:00/17:00 DART 수주공시
            ("근로복지공단",   self._loop_wlb_monthly),           # ★ 매일 20:30 변화감지 → 수집
            ("BigQuery동기화", self._loop_bigquery_sync),         # ★ 매일 23:30 BigQuery 증분 동기화
            ("컨센서스수집",   self._loop_consensus),             # ★ 매일 04:00 한경 컨센서스 증분 수집
            ("재무무결점월간", self._loop_financial_integrity_monthly),  # ★ 매월 1일 05:00 재무 무결점 검사
            ("재무무결점분기", self._loop_financial_integrity_quarterly), # ★ 분기 공시마감 1주 후 자동 보완
            ("KRX종목기본정보", self._loop_krx_base_info),               # ★ 매일 18:35 KRX 종목기본정보 + 변동 감지
            ("FnGuide재무월간", self._loop_fnguide_financial_monthly),  # ★ 매월 3일 05:00 연결/별도 재무제표 전종목
            ("수출입가집계",   self._loop_trade_provisional),          # ★ 매주 월요일 06:00 수출입 10일 가집계 수집
        ]
        for name, target in jobs:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        logger.info(f"[스케줄러] {len(jobs)}개 잡 시작")

        # ★ 서버 재시작 후 당일 누락 데이터 자동 캐치업 (30초 딜레이 후 실행)
        t_catchup = threading.Thread(target=self._startup_catchup, name="시작캐치업", daemon=True)
        t_catchup.start()
        self._threads.append(t_catchup)

    def stop(self) -> None:
        """서버 shutdown 시 호출."""
        self._stop_event.set()
        logger.info("[스케줄러] 중지 신호 전송")

    # ══════════════════════════════════════════════════════════
    # 스케줄 루프
    # ══════════════════════════════════════════════════════════

    def _loop_nightly(self) -> None:
        """매일 00:10 — watchlist+포트폴리오 전종목 OHLCV + 수급."""
        self._wait_secs(5)  # 서버 기동 여유
        while not self._stop_event.is_set():
            self._wait_until(0, 10)
            _run_job_safe("야간배치", self._job_nightly_batch)

    def _loop_monthly(self) -> None:
        """매월 1일 03:00 — 전종목 메타 + 시총 갱신."""
        self._wait_secs(10)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 다음 월 1일 03:00 계산
            if now.day == 1 and now.hour < 3:
                next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            else:
                next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
                next_run   = next_month.replace(hour=3, minute=0, second=0, microsecond=0)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("월간업데이트", self._job_monthly_bulk)

    def _loop_disclosure(self) -> None:
        """매일 03:30 — DART 공시 확인 → 재무 재수집."""
        self._wait_secs(15)
        while not self._stop_event.is_set():
            self._wait_until(3, 30)
            _run_job_safe("공시확인", self._job_disclosure_check)

    def _loop_intraday_price(self) -> None:
        """장중 매 1분 — watchlist 현재가 갱신."""
        self._wait_secs(3)
        while not self._stop_event.is_set():
            if _is_market_open():
                _run_job_safe("장중가격", self._job_intraday_prices)
            self._wait_secs(60)

    def _loop_intraday_investor(self) -> None:
        """장중 매 5분 — 지수 수급 + watchlist 수급."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            if _is_market_open():
                _run_job_safe("장중수급", self._job_intraday_investor)
            self._wait_secs(300)

    def _loop_closing(self) -> None:
        """평일 15:40 — 장마감 처리 (지수일봉·KIS체결·스냅샷)."""
        self._wait_secs(20)
        while not self._stop_event.is_set():
            self._wait_until(15, 40)
            _run_job_safe("장마감", self._job_closing)

    def _loop_us_indices(self) -> None:
        """매일 06:30 — 미국 장 마감 후 나스닥/S&P500/VIX 수집 (한국시간 기준).
        미국 정규장 마감: EST 16:00 = KST 06:00 (서머타임) / 07:00 (동절기).
        06:30에 실행하면 서머타임/동절기 무관하게 전날 종가 확보.
        """
        logger.info("[미국지수수집] 루프 시작")
        self._wait_secs(5)
        while not self._stop_event.is_set():
            self._wait_until(6, 30, skip_weekend=False)  # 주말도 실행 (금요일 미국장 → 토요일 06:30 수집)
            if self._stop_event.is_set():
                break
            _run_job_safe("미국지수수집", self._job_collect_us_indices)
            # 다음 날까지 대기
            self._wait_secs(23 * 3600)
        logger.info("[미국지수수집] 루프 종료")

    def _job_collect_us_indices(self) -> None:
        """나스닥(^IXIC)·S&P500(^GSPC)·VIX(^VIX) 최신 데이터 수집.
        Yahoo Finance 우선, 실패 시 네이버 증권 fallback.
        """
        from data_collector import DataCollector
        today_iso = date.today().isoformat()
        collector = DataCollector(dart_api_key=config.DART_API_KEY)
        us_symbols = {
            "^IXIC": "NASDAQ",
            "^GSPC": "S&P500",
            "^VIX":  "VIX",
        }
        for symbol, name in us_symbols.items():
            try:
                collector._collect_macro_yahoo(symbol, name, today_iso)
            except Exception as e:
                logger.warning(f"[미국지수수집] {name}: {e}")
        logger.info("[미국지수수집] 완료")

    def _loop_screener(self) -> None:
        """매 30분 — AI 스크리너 사전계산."""
        self._wait_secs(60)   # 서버 완전 기동 후 시작
        while not self._stop_event.is_set():
            _run_job_safe("스크리너", self._job_screener_precompute)
            self._wait_secs(1800)

    def _loop_public_data(self) -> None:
        """매일 18:30 — 공공데이터포털 전종목 일별 데이터 수집."""
        self._wait_secs(25)
        while not self._stop_event.is_set():
            self._wait_until(18, 30, skip_weekend=True)
            _run_job_safe("공공데이터", self._job_public_data)

    def _loop_naver_fundamentals(self) -> None:
        """매일 02:00 — 네이버금융 전종목 PBR/PER/EPS 배치 수집."""
        self._wait_secs(35)
        while not self._stop_event.is_set():
            self._wait_until(2, 0)
            _run_job_safe("네이버밸류에이션", self._job_naver_fundamentals)

    def _loop_cashflow_batch(self) -> None:
        """매월 2일 04:00 — DART 전종목 현금흐름표 배치 수집."""
        self._wait_secs(40)
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.day == 2 and now.hour < 4:
                next_run = now.replace(hour=4, minute=0, second=0, microsecond=0)
            else:
                next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=2)
                next_run   = next_month.replace(hour=4, minute=0, second=0, microsecond=0)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("현금흐름배치", self._job_cashflow_batch)

    # ══════════════════════════════════════════════════════════
    # 잡 구현
    # ══════════════════════════════════════════════════════════

    def _job_nightly_batch(self) -> None:
        """watchlist + 포트폴리오 전종목 OHLCV + 수급 + 매크로 수집."""
        from data_collector import DataCollector  # 기존 수집기 재사용 (점진적 전환)

        conn = connect_stock_db(timeout=30)
        try:
            codes = self._get_all_tracked_codes(conn)
        finally:
            conn.close()

        logger.info(f"[야간배치] 대상 {len(codes)}종목")
        collector = DataCollector(dart_api_key=config.DART_API_KEY)

        for code in codes:
            try:
                collector.collect_prices(code, skip_if_today=True)
            except Exception as e:
                logger.warning(f"[야간배치] {code} 가격 수집 오류: {e}")

        try:
            collector.collect_macro_data()
        except Exception as e:
            logger.warning(f"[야간배치] 매크로 수집 오류: {e}")

        try:
            collector.backfill_index_history()
        except Exception as e:
            logger.warning(f"[야간배치] 지수히스토리 오류: {e}")

    def _job_monthly_bulk(self) -> None:
        """전종목 메타(시총·섹터) 갱신."""
        try:
            import stock_universe
            stock_universe.update_universe()
        except Exception as e:
            logger.error(f"[월간업데이트] {e}")

    def _job_disclosure_check(self) -> None:
        """DART 공시 → 전체 상장종목 중 재무 공시 있는 종목 재수집 + FnGuide 잔여 보완."""
        from data_collector import DataCollector
        import sqlite3 as _sl

        collector = DataCollector(dart_api_key=config.DART_API_KEY)

        # 전체 상장 보통주 코드 (추적 대상 한정 제거 → 전종목)
        conn = connect_stock_db(timeout=30)
        try:
            rows = conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE stock_type = '보통주'
                  AND market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            """).fetchall()
            all_codes = {r[0] for r in rows}
        finally:
            conn.close()

        # 오늘 DART 공시 종목 (OpenDart API → 공개사이트 fallback)
        today_codes: set[str] = set()
        today_str = date.today().strftime("%Y%m%d")

        # 1차: OpenDart API
        _opendart_ok = False
        try:
            dart = collector.dart
            if dart:
                for kind in ["A", "B"]:
                    df = dart.list(today_str, today_str, kind=kind)
                    if df is not None and not df.empty and "stock_code" in df.columns:
                        for c in df["stock_code"].dropna().unique():
                            today_codes.add(str(c).zfill(6))
                _opendart_ok = True
        except Exception as e:
            logger.warning(f"[공시확인] OpenDart API 실패 (IP 차단 등): {e}")

        # 2차 fallback: dart.fss.or.kr 공개사이트 스크래핑 (전체 오늘 공시 종목 수집)
        if not _opendart_ok:
            try:
                import requests as _req
                from bs4 import BeautifulSoup as _BS
                _headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://dart.fss.or.kr",
                }
                _today_fmt = date.today().strftime("%Y%m%d")
                _resp = _req.post(
                    "https://dart.fss.or.kr/dsab007/detailSearch.ax",
                    data={"startDate": _today_fmt, "endDate": _today_fmt,
                          "pageNo": 1, "pageCount": 200},
                    headers=_headers, timeout=20,
                )
                _soup = _BS(_resp.text, "html.parser")
                # 종목코드는 링크 href에서 추출 불가하므로 회사명으로 매핑
                _names = set()
                for _row in _soup.select("table.tbList tbody tr"):
                    _cols = _row.select("td")
                    if len(_cols) >= 2:
                        _corp = _cols[1].get_text(strip=True)
                        # "유" prefix 제거 (유가증권 표시)
                        _corp = _corp.lstrip("유코")
                        _names.add(_corp)

                # 회사명 → stock_code 매핑 (stock_universe 조회)
                if _names:
                    _conn2 = connect_stock_db(timeout=10)
                    try:
                        _phs = ",".join("?" * len(_names))
                        _mrows = _conn2.execute(
                            f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_name IN ({_phs})",
                            list(_names)
                        ).fetchall()
                        for _mc, _mn in _mrows:
                            today_codes.add(_mc)
                    finally:
                        _conn2.close()
                logger.info(f"[공시확인] 공개사이트 폴백: {len(_names)}개 회사 → {len(today_codes)}개 코드 매핑")
            except Exception as _e2:
                logger.warning(f"[공시확인] 공개사이트 폴백 실패: {_e2}")

        # 재무 공시가 있는 상장 보통주만 재수집
        triggered = [c for c in all_codes if c in today_codes]
        logger.info(f"[공시확인] DART 공시 {len(today_codes)}종목 | 보통주 재무갱신 대상 {len(triggered)}종목")

        financial_updated: list[str] = []
        for code in triggered:
            try:
                if collector._has_dart_financial_disclosure(code):
                    collector.collect_fundamentals(code, latest_only=True)
                    financial_updated.append(code)
                    time.sleep(0.3)
            except Exception as e:
                logger.warning(f"[공시확인] {code} 재무 갱신 오류: {e}")

        logger.info(f"[공시확인] 재무 수집 완료: {len(financial_updated)}종목")

        # DART 수집 후에도 공백이 남은 종목 → FnGuide 보완 (최근 4분기)
        if financial_updated:
            self._fnguide_fill_after_disclosure(financial_updated)

        # ── 대주주 + 임원지분변동 수집 (어제~오늘 D 공시 종목만, DART 한도 절약) ──
        try:
            from collectors.dart_insider_collector import collect_recent_disclosures
            res = collect_recent_disclosures(days=2)
            logger.info(
                f"[공시확인] 지분공시 수집: 대량보유 {res['major']} / 임원지분 {res['insider']} / 오류 {res['errors']}"
            )
        except Exception as e:
            logger.warning(f"[공시확인] 지분공시 수집 오류: {e}")

    def _fnguide_fill_after_disclosure(self, codes: list[str]) -> None:
        """공시 수집 후 FnGuide로 잔여 공백 보완 (최근 4분기)."""
        import sqlite3 as _sl
        try:
            from check_financial_integrity import fnguide_fill_financial, recent_quarters, check_financial_gaps, get_conn as _cfi_conn
            qs = recent_quarters(4)
            conn = _cfi_conn()
            gaps = check_financial_gaps(conn, qs)
            conn.close()
            # 방금 수집한 종목 중 여전히 공백인 것만
            gap_codes = list({g["stock_code"] for g in gaps} & set(codes))
            if not gap_codes:
                return
            logger.info(f"[공시FnGuide] DART 수집 후 공백 {len(gap_codes)}종목 FnGuide 보완 시작")
            fnguide_fill_financial(gap_codes, qs)
            logger.info(f"[공시FnGuide] 보완 완료")
        except Exception as e:
            logger.warning(f"[공시FnGuide] 보완 오류: {e}")

    def _job_intraday_prices(self) -> None:
        """장중 watchlist + 포트폴리오 현재가 갱신."""
        from kis_client import kis_client

        conn = connect_stock_db(timeout=30)
        try:
            codes = self._get_active_codes(conn)
        finally:
            conn.close()

        import httpx as _hx
        base = "http://127.0.0.1:8000"
        for code in codes:
            try:
                data = kis_client.get_current_price(code)
                if data:
                    _hx.post(f"{base}/api/ingest/market-price",
                             json={"stock_code": code, "prices": [data]}, timeout=5)
            except Exception as e:
                logger.debug(f"[장중가격] {code}: {e}")

    def _job_intraday_investor(self) -> None:
        """장중 매 5분 — 관심종목(watchlist+포트폴리오+보유) 수급만 갱신.

        ※ 전종목 수집은 _job_supply_daily(17:30)에서 처리.
           장중 5분 루프에서 2700종목 전체를 순차 처리하면
           1사이클 = 45분이 걸려 앞 300종목만 반복 수집되는 버그 수정.
        """
        from data_collector import DataCollector

        collector = DataCollector(dart_api_key=config.DART_API_KEY)

        # 지수 수급 (매크로)
        try:
            collector.collect_macro_data()
        except Exception as e:
            logger.debug(f"[장중수급] 매크로 오류: {e}")

        # 관심 종목만 (watchlist + portfolio + 활성 보유)
        conn = connect_stock_db(timeout=30)
        try:
            codes = self._get_active_codes(conn)
        finally:
            conn.close()

        logger.debug(f"[장중수급] 관심종목 {len(codes)}개 수급 갱신")
        for code in codes:
            if self._stop_event.is_set():
                break
            try:
                collector._update_investor_trends_bulk(code)
            except Exception as e:
                logger.debug(f"[장중수급] {code}: {e}")
            time.sleep(1.05)

    def _job_closing(self) -> None:
        """15:40 장마감: 지수 일봉 저장 + KIS 체결 동기화 + 포트폴리오 스냅샷."""
        if not is_kr_trading_day():
            logger.info(f"[장마감] {date.today()} 휴장일 — 스킵")
            return

        # main.py의 기존 함수들 재사용 (점진적 전환)
        try:
            import main as _main
            _main._save_index_history_today()
        except Exception as e:
            logger.warning(f"[장마감] 지수일봉: {e}")

        try:
            from database import SessionLocal
            from routes.portfolio import sync_kis_executions, save_portfolio_snapshot
            db = SessionLocal()
            try:
                cnt = sync_kis_executions(db)
                logger.info(f"[장마감] KIS 체결 {cnt}건 동기화")
                save_portfolio_snapshot(db)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[장마감] 체결/스냅샷: {e}")

    def _job_screener_precompute(self) -> None:
        """AI 스크리너 3종 + combo 사전계산."""
        try:
            import main as _main
            _main._run_screener_precompute()
        except Exception as e:
            logger.error(f"[스크리너] 사전계산 오류: {e}")

    def _job_public_data(self) -> None:
        """공공데이터포털 수집 (Gap 자동감지 + 백필).
        
        마지막 성공 수집일부터 오늘까지 누락된 영업일을 자동 보완.
        ⚠️ apis.data.go.kr DNS 차단 시 수집 실패 — 네트워크 수준 해결 필요.
        """
        import sqlite3 as _sl
        from datetime import timedelta as _td
        if not getattr(config, "PUBLIC_DATA_API_KEY", ""):
            logger.debug("[공공데이터] API 키 없음 — 스킵")
            return

        today     = date.today()
        today_str = today.strftime("%Y%m%d")

        # ── Gap 감지: short_sell_daily 마지막 수집일 확인 ──────────
        dates_to_collect: list[str] = []
        try:
            _conn = _sl.connect("stock.db")
            _last = _conn.execute("SELECT MAX(bas_dt) FROM short_sell_daily WHERE stock_code != '000000'").fetchone()
            _conn.close()
            last_date = _last[0] if _last and _last[0] else None
        except Exception:
            last_date = None

        if last_date and last_date < today_str:
            # 마지막 수집일 다음날부터 오늘까지 영업일 목록 생성
            _cur = date.fromisoformat(
                f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:8]}"
            ) + _td(days=1)
            while _cur <= today:
                if _cur.weekday() < 5:
                    dates_to_collect.append(_cur.strftime("%Y%m%d"))
                _cur += _td(days=1)
            logger.info(
                f"[공공데이터] Gap 감지: {last_date}→{today_str}, "
                f"{len(dates_to_collect)}일치 백필 시작"
            )
        else:
            dates_to_collect = [today_str]

        if not dates_to_collect:
            logger.info(f"[공공데이터] {today_str} 이미 수집됨 — 스킵")
            return

        loop = asyncio.new_event_loop()
        try:
            from collectors.public_data import PublicDataCollector
            collector   = PublicDataCollector()
            total_saved = 0
            for bas_dt in dates_to_collect:
                try:
                    saved = loop.run_until_complete(collector.collect_all_for_date(bas_dt))
                    cnt   = sum(saved.values())
                    total_saved += cnt
                    logger.info(f"[공공데이터] {bas_dt}: {cnt}건 저장")
                    if cnt == 0:
                        logger.warning(
                            f"[공공데이터] {bas_dt}: 0건 — "
                            "휴장일/미공개일 또는 API 응답 지연 가능"
                        )
                        continue
                except Exception as _e:
                    logger.error(f"[공공데이터] {bas_dt} 오류: {_e}")
                    break

            # 대차종목순위/내외국인/월별 추가 수집.
            # 공공데이터포털 대차 V2는 당일 데이터가 늦게 열릴 수 있어 최근 영업일을 역순 확인.
            try:
                saved_short = {}
                for i in range(0, 10):
                    cand = today - _td(days=i)
                    if cand.weekday() >= 5:
                        continue
                    cand_str = cand.strftime("%Y%m%d")
                    saved_short = loop.run_until_complete(
                        collector.collect_short_all_for_date(cand_str)
                    )
                    rank_ok = saved_short.get("short_rank_daily", 0) > 0
                    svc_ok  = saved_short.get("short_sell_daily", 0) > 0
                    if rank_ok and svc_ok:
                        logger.info(f"[대차추가수집] {cand_str}: {saved_short}")
                        if saved_short.get("short_foreign_balance", 0) == 0:
                            logger.warning(
                                f"[대차추가수집] {cand_str}: 내외국인 잔고비교(단일일) 0건 "
                                "(API 미공개/지연 가능) — 다음 루프에서 재시도"
                            )
                        break
                    logger.info(
                        f"[대차추가수집] {cand_str}: rank={saved_short.get('short_rank_daily',0)}, "
                        f"svc={saved_short.get('short_sell_daily',0)} — 이전 영업일 확인"
                    )
            except Exception as _e:
                logger.error(f"[대차추가수집] {_e}")

            logger.info(f"[공공데이터] 완료: {total_saved}건 저장")
        except Exception as e:
            logger.error(f"[공공데이터] {e}")
        finally:
            loop.close()


    # ──────────────────────────────────────────────────────────
    # KIS 일별 수집 (18:00 — 장 마감 후, KRX 차단 대체)
    # ──────────────────────────────────────────────────────────

    def _loop_kis_daily(self) -> None:
        """18:00 영업일마다 KIS API로 당일 전종목 OHLCV + 주요 지수 수집."""
        logger.info("[KIS일별] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(18, 0, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                self._job_kis_ohlcv_daily()
            except Exception as e:
                logger.error(f"[KIS일별] 잡 오류: {e}")
            # 다음 실행까지 1시간 대기 (같은 날 중복 실행 방지)
            self._wait_secs(3600)
        logger.info("[KIS일별] 루프 종료")

    def _job_kis_ohlcv_daily(self) -> None:
        """KIS API로 당일 전종목 OHLCV를 수집하고 KIS/네이버로 주요 지수를 보완."""
        today = date.today()
        if today.weekday() >= 5:
            logger.info("[KIS일별] 주말 — 스킵")
            return

        with stock_db_write_lock("KIS일별수집", timeout=3) as acquired:
            if not acquired:
                logger.warning("[KIS일별] 스킵 — 다른 DB writer 실행 중")
                return

            py = "/Applications/stock_dashboard/venv/bin/python"
            if not os.path.exists(py):
                py = sys.executable
            cmd = [py, "collect_kis_ohlcv.py", "--days", "1"]
            logger.info(f"[KIS일별] {' '.join(cmd)} 시작")
            proc = subprocess.run(
                cmd,
                cwd="/Applications/stock_dashboard",
                text=True,
                capture_output=True,
                timeout=3600,
            )
            if proc.stdout:
                logger.info("[KIS일별] stdout\n%s", proc.stdout[-4000:])
            if proc.stderr:
                logger.info("[KIS일별] stderr\n%s", proc.stderr[-4000:])
            if proc.returncode != 0:
                raise RuntimeError(f"KIS OHLCV 수집 실패(returncode={proc.returncode})")

            core_saved = self._collect_kis_core_indices()
            sub_saved = self._collect_derivative_indices()
            logger.info(f"[KIS일별] 주요지수 KIS {core_saved}건, 파생지수 fallback {sub_saved}건 저장")

    def _collect_kis_core_indices(self) -> int:
        """KIS 지수 현재가로 KOSPI/KOSDAQ/KOSPI200/KOSDAQ150 당일 값을 저장."""
        from kis_client import KISClient

        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        mapping = {
            "0001": "^KS11",
            "1001": "^KQ11",
            "2001": "^KS200",
            "2203": "^KQ150",
        }
        client = KISClient()
        saved = 0
        conn = connect_stock_db(timeout=30)
        try:
            for kis_code, symbol in mapping.items():
                price = client.get_index_price(kis_code)
                if not price or not price.get("value"):
                    continue
                conn.execute("""
                    INSERT INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stock_code, date) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume
                """, (
                    symbol, today_str,
                    price.get("open") or price["value"],
                    price.get("high") or price["value"],
                    price.get("low") or price["value"],
                    price["value"],
                    price.get("volume") or 0,
                ))
                saved += 1
            conn.commit()
        finally:
            conn.close()
        return saved

    # Backward-compatible name for old manual hooks. It now uses KIS first.
    def _job_krx_daily(self) -> None:
        self._job_kis_ohlcv_daily()

    def _job_krx_daily_legacy(self) -> None:
        """Legacy KRX 승인 API 수집. KRX 차단 환경에서는 사용하지 않음."""

    def _job_krx_daily(self) -> None:
        """KRX 승인 API로 오늘 날짜 전종목 OHLCV + KOSPI/KOSDAQ 지수 저장."""
        import sqlite3
        import requests as _req

        today = date.today()
        if not is_kr_trading_day(today):
            logger.info(f"[KRX일별] {today} 휴장일 — 스킵")
            return

        api_key = getattr(config, "KRX_API_KEY", "")
        if not api_key:
            logger.warning("[KRX일별] KRX_API_KEY 없음 — 스킵")
            return

        bas_dd    = today.strftime("%Y%m%d")
        today_str = today.strftime("%Y-%m-%d")
        base_url  = "https://data-dbg.krx.co.kr/svc/apis"
        headers   = {"AUTH_KEY": api_key}

        try:
            from api_rate_limiter import api_limiter as _rl_krx
        except ImportError:
            _rl_krx = None

        def _fetch(path: str) -> list:
            if _rl_krx and not _rl_krx.wait("KRX"):
                logger.warning("[KRX일별] 일일 쿼터 소진 — 스킵")
                return []
            try:
                r = _req.get(
                    f"{base_url}/{path}",
                    params={"basDd": bas_dd},
                    headers=headers,
                    timeout=20,
                )
                if r.status_code == 429:
                    if _rl_krx:
                        _rl_krx.report_block("KRX", cooldown=3600)
                    return []
                if r.status_code == 200:
                    return r.json().get("OutBlock_1", [])
            except Exception as e:
                logger.warning(f"[KRX일별] {path}: {e}")
            return []

        def _n(row, key):
            v = row.get(key, "")
            try:
                return float(str(v).replace(",", "")) if v not in ("", "-", None) else 0.0
            except Exception:
                return 0.0

        conn = connect_stock_db(timeout=60)
        ins = upd = idx_saved = 0

        # ① 유가증권 + ② 코스닥 종목 OHLCV + 거래대금 + 상장주식수 + 소속부
        for path in ("sto/stk_bydd_trd", "sto/ksq_bydd_trd"):
            rows = _fetch(path)
            for r in rows:
                code = str(r.get("ISU_CD", "")).strip()
                if not code or len(code) != 6 or not code.isdigit():
                    continue
                close        = _n(r, "TDD_CLSPRC")
                open_        = _n(r, "TDD_OPNPRC")
                high         = _n(r, "TDD_HGPRC")
                low          = _n(r, "TDD_LWPRC")
                volume       = _n(r, "ACC_TRDVOL")
                trade_amount = _n(r, "ACC_TRDVAL")   # 거래대금(원) ★신규
                mktcap       = _n(r, "MKTCAP")
                list_shrs    = _n(r, "LIST_SHRS")    # 상장주식수 ★신규
                sect_tp      = str(r.get("SECT_TP_NM", "")).strip()  # 소속부 ★신규
                if close <= 0:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume, trade_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (code, today_str, open_, high, low, close, volume, trade_amount))
                if cur.rowcount > 0:
                    ins += 1
                else:
                    conn.execute("""
                        UPDATE price_history
                        SET open=?, high=?, low=?, close=?, volume=?, trade_amount=?
                        WHERE stock_code=? AND date=? AND (volume IS NULL OR volume=0)
                    """, (open_, high, low, close, volume, trade_amount, code, today_str))
                    upd += conn.execute("SELECT changes()").fetchone()[0]
                # stock_universe 일별 갱신: 시가총액 + 상장주식수 + 소속부
                if mktcap > 0:
                    conn.execute(
                        "UPDATE stock_universe SET market_cap=? WHERE stock_code=?",
                        (mktcap, code),
                    )
                if list_shrs > 0:
                    conn.execute(
                        "UPDATE stock_universe SET shares_issued=? WHERE stock_code=?",
                        (int(list_shrs), code),
                    )
                if sect_tp:
                    conn.execute(
                        "UPDATE stock_universe SET sector_type=? WHERE stock_code=?",
                        (sect_tp, code),
                    )

        # ③ KOSPI 지수 (^KS11) + ④ KOSDAQ 지수 (^KQ11) + ⑤ KOSDAQ150 (^KQ150)
        # 정확한 이름 매칭만 허용 — 서브인덱스(중형주, 기술성장기업부 등)가 덮어쓰는 버그 방지
        _idx_exact_map = {
            "코스피":    "^KS11",
            "KOSPI":     "^KS11",
            "코스닥":    "^KQ11",
            "KOSDAQ":    "^KQ11",
            "코스피 200": "^KS200",
            "코스닥 150": "^KQ150",
        }
        for path in ("idx/kospi_dd_trd", "idx/kosdaq_dd_trd"):
            for r in _fetch(path):
                idx_nm = str(r.get("IDX_NM", "")).strip()
                code = _idx_exact_map.get(idx_nm)
                if not code:
                    continue
                close  = _n(r, "CLSPRC_IDX")
                open_  = _n(r, "OPNPRC_IDX")
                high   = _n(r, "HGPRC_IDX")
                low    = _n(r, "LWPRC_IDX")
                volume = _n(r, "ACC_TRDVOL")
                if close <= 0:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (code, today_str, open_, high, low, close, volume))
                if cur.rowcount > 0:
                    idx_saved += 1
                else:
                    conn.execute("""
                        UPDATE price_history SET open=?, high=?, low=?, close=?, volume=?
                        WHERE stock_code=? AND date=?
                    """, (open_, high, low, close, volume, code, today_str))

        # close=0 잔존 행 자동 정리 (ingest.py 버그 수정 전 생성분 포함)
        del_cnt = conn.execute(
            "DELETE FROM price_history WHERE (close IS NULL OR close=0) AND substr(date,1,10) < ?",
            (today_str,)
        ).rowcount
        if del_cnt:
            logger.info(f"[KRX일별] close=0 잔존행 {del_cnt}건 삭제")

        conn.commit()
        conn.close()
        logger.info(f"[KRX일별] {today_str} 종목+{ins} 수정{upd} 지수+{idx_saved}")

        # ── KRX 지수 수집 0건이면 Yahoo Finance로 보완 ───────────────────
        # NASDAQ(^IXIC), S&P500(^GSPC) 등
        # KRX API 차단/지연 시 자동으로 Yahoo에서 수집
        if idx_saved == 0:
            logger.info("[KRX일별] 지수 0건 → Yahoo Finance fallback 시작")
            try:
                from data_collector import DataCollector
                import config as _cfg
                collector = DataCollector(dart_api_key=_cfg.DART_API_KEY)
                collector.backfill_index_history()
                logger.info("[KRX일별] Yahoo fallback 완료")
            except Exception as _e:
                logger.warning(f"[KRX일별] Yahoo fallback 오류: {_e}")

        # ── KOSPI200/KOSDAQ150 누락 확인 → 네이버 금융 fallback ─────────
        # KRX승인 API가 서브지수를 반환하지 않는 경우가 있어 별도 수집
        try:
            conn2 = connect_stock_db(timeout=10)
            missing_sub = conn2.execute(
                "SELECT COUNT(*) FROM price_history WHERE stock_code IN ('^KS200','^KQ150') AND date=?",
                (today_str,)
            ).fetchone()[0]
            conn2.close()
            if missing_sub < 2:
                logger.info(f"[KRX일별] KOSPI200/KOSDAQ150 누락({missing_sub}건) → 네이버 fallback")
                n = self._collect_derivative_indices()
                logger.info(f"[KRX일별] 네이버 fallback: {n}건 저장")
        except Exception as _e2:
            logger.warning(f"[KRX일별] KOSPI200/KOSDAQ150 fallback 오류: {_e2}")


    # ──────────────────────────────────────────────────────────
    # 전종목 수급 일괄 수집 (17:30 — KRX 수집 후)
    # ──────────────────────────────────────────────────────────

    def _loop_supply_daily(self) -> None:
        """17:30 영업일마다 전종목 KIS 30일 수급 일괄 수집."""
        logger.info("[전종목수급17시] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(17, 30, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                _run_job_safe("전종목수급17시", self._job_supply_daily)
            except Exception as e:
                logger.error(f"[전종목수급17시] 잡 오류: {e}")
            # 다음 날까지 대기 (23시간)
            self._wait_secs(23 * 3600)
        logger.info("[전종목수급17시] 루프 종료")

    def _loop_supply_evening(self) -> None:
        """21:00 영업일마다 전종목 수급 재갱신 (장 마감 후 KIS 데이터 확정 반영)."""
        logger.info("[전종목수급21시] 루프 시작")
        self._wait_secs(20)
        while not self._stop_event.is_set():
            self._wait_until(21, 0, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                _run_job_safe("전종목수급21시", self._job_supply_daily)
            except Exception as e:
                logger.error(f"[전종목수급21시] 잡 오류: {e}")
            self._wait_secs(23 * 3600)
        logger.info("[전종목수급21시] 루프 종료")

    def _loop_radar_price_update(self) -> None:
        """해외 종목 가격 갱신 (radar_price_cache).
        - 장중(09:00-22:00 KST): 2시간마다
        - 비장중:                 4시간마다  (Yahoo 일일쿼터 절약)
        """
        logger.info("[레이더해외가격] 루프 시작")
        self._wait_secs(30)  # 서버 기동 여유
        while not self._stop_event.is_set():
            try:
                from routes.market_radar import refresh_foreign_prices_sync
                updated = refresh_foreign_prices_sync()
                logger.info(f"[레이더해외가격] {updated}개 종목 갱신 완료")
            except Exception as e:
                logger.error(f"[레이더해외가격] 오류: {e}", exc_info=True)
            # 장중 여부에 따라 대기 시간 조정
            from datetime import datetime as _dt
            _h = _dt.now().hour
            _wait = 7200 if (9 <= _h < 22) else 14400  # 장중 2h, 비장중 4h
            self._wait_secs(_wait)
        logger.info("[레이더해외가격] 루프 종료")

    def _job_supply_daily(self) -> None:
        """KIS 전종목 최근 30거래일 수급 수집 → price_history 업데이트."""
        import sqlite3 as _sl
        from kis_client import kis_client

        conn = connect_stock_db(timeout=60)

        try:
            rows = conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND market IN ('유가증권', '코스닥', 'KOSPI', 'KOSDAQ')
                  AND COALESCE(stock_type, '보통주') = '보통주'
                  AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
                  AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
                  AND COALESCE(stock_name, '') NOT LIKE '%KODEX%'
                  AND COALESCE(stock_name, '') NOT LIKE '%TIGER%'
                  AND COALESCE(stock_name, '') NOT LIKE '%KBSTAR%'
                  AND COALESCE(stock_name, '') NOT LIKE '%ACE%'
                  AND COALESCE(stock_name, '') NOT LIKE '%SOL%'
                  AND COALESCE(stock_name, '') NOT LIKE '%HANARO%'
                  AND COALESCE(stock_name, '') NOT LIKE '%KOSEF%'
                  AND COALESCE(stock_name, '') NOT LIKE '%ARIRANG%'
                  AND COALESCE(stock_name, '') NOT LIKE '%PLUS%'
                  AND COALESCE(stock_name, '') NOT LIKE '%레버리지%'
                  AND COALESCE(stock_name, '') NOT LIKE '%인버스%'
                  AND COALESCE(stock_name, '') NOT LIKE '%2X%'
                ORDER BY market_cap DESC NULLS LAST
            """).fetchall()
            codes = [r[0] for r in rows]
        except Exception as e:
            logger.error(f"[전종목수급] 종목 조회 오류: {e}")
            conn.close()
            return

        logger.info(f"[전종목수급] {len(codes)}종목 수급 수집 시작")
        total_saved = 0

        for i, code in enumerate(codes):
            if self._stop_event.is_set():
                break
            try:
                trends = kis_client.get_investor_trends_bulk(code)
                if not trends:
                    continue
                for t in trends:
                    d = t.get("date", "")
                    if not d:
                        continue
                    # 한국 거래일이 아닌 날짜(공휴일/주말) 데이터는 KIS가 잘못 반환한 것 → 저장 금지
                    try:
                        _d = date.fromisoformat(d) if isinstance(d, str) else d
                        if not is_kr_trading_day(_d):
                            continue
                    except Exception:
                        pass
                    iq = t.get("inst_net_buy", 0);  fq = t.get("frn_net_buy", 0)
                    dq = t.get("ind_net_buy",  0)
                    ia = t.get("inst_net_buy_amt", 0); fa = t.get("frn_net_buy_amt", 0)
                    da = t.get("ind_net_buy_amt",  0)
                    if iq == 0 and fq == 0 and ia == 0 and fa == 0:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM price_history WHERE stock_code=? AND date=?", (code, d)
                    ).fetchone()
                    if not exists:
                        continue
                    # 기존 수급 데이터가 없는 날짜만 업데이트 (기존 데이터 불변)
                    # ★ amt 기준으로 스킵 여부 판단 (qty는 장중잡이 먼저 쓸 수 있음)
                    # 기존: qty 있으면 skip → 장중잡이 쓴 qty행이 amt 없이 남는 버그
                    # 수정: amt 없으면 항상 업데이트 (qty 포함 전체 갱신)
                    conn.execute("""
                        UPDATE price_history
                        SET inst_net_buy     = ?,
                            frn_net_buy      = ?,
                            ind_net_buy      = ?,
                            inst_net_buy_amt = ?,
                            frn_net_buy_amt  = ?,
                            ind_net_buy_amt  = ?
                        WHERE stock_code=? AND date=?
                          AND (inst_net_buy_amt IS NULL OR inst_net_buy_amt = 0)
                          AND (frn_net_buy_amt  IS NULL OR frn_net_buy_amt  = 0)
                    """, (iq, fq, dq, ia, fa, da, code, d))
                    total_saved += conn.execute("SELECT changes()").fetchone()[0]

                if (i + 1) % 50 == 0:
                    conn.commit()
                    logger.info(f"[전종목수급] {i+1}/{len(codes)} 진행 중... 저장 {total_saved}건")

            except Exception as e:
                logger.debug(f"[전종목수급] {code}: {e}")
            # KIS rate-limit은 kis_client._call_investor_api 내 api_rate_limiter가 처리
            # (jitter 포함 1.05~1.15초 간격, 쿼터 초과 시 자동 중단)

        conn.commit()
        conn.close()
        logger.info(f"[전종목수급] 완료 — 총 {total_saved:,}건 저장")

    # ══════════════════════════════════════════════════════════
    # 시작 캐치업 (서버 재시작 시 당일 누락 데이터 즉시 수집)
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _fetch_naver_index(naver_code: str, count: int = 5) -> list[dict]:
        """네이버 금융 fchart API로 지수 일봉 데이터 수집.

        naver_code: KPI200(KOSPI200), KSQ150(KOSDAQ150), KOSPI, KOSDAQ
        반환: [{date:'YYYY-MM-DD', open, high, low, close, volume}, ...]
        """
        import requests as _rq
        try:
            url = f"https://fchart.stock.naver.com/sise.nhn?symbol={naver_code}&timeframe=day&count={count}&requestType=0"
            r = _rq.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                return []
            import re
            items = []
            for m in re.findall(r'<item data="([^"]+)"', r.text):
                parts = m.split('|')
                if len(parts) < 6:
                    continue
                raw_dt = parts[0].strip()
                if len(raw_dt) == 8:
                    dt_str = f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}"
                else:
                    continue
                try:
                    close = float(parts[4])
                    items.append({
                        "date":   dt_str,
                        "open":   float(parts[1]),
                        "high":   float(parts[2]),
                        "low":    float(parts[3]),
                        "close":  close,
                        "volume": float(parts[5]) if len(parts) > 5 else 0.0,
                    })
                except (ValueError, IndexError):
                    continue
            return items
        except Exception as _e:
            logger.debug(f"[네이버지수] {naver_code}: {_e}")
            return []

    def _fetch_krx_index_recent(self, code: str, days: int = 7) -> list[dict]:
        """KRX 승인 API에서 최근 거래일의 KOSPI200/KOSDAQ150 일봉을 조회."""
        import requests as _rq

        api_key = getattr(config, "KRX_API_KEY", "")
        if not api_key:
            return []

        name_map = {
            "^KS200": ("idx/kospi_dd_trd", "코스피 200"),
            "^KQ150": ("idx/kosdaq_dd_trd", "코스닥 150"),
        }
        target = name_map.get(code)
        if not target:
            return []

        path, idx_name = target
        base_url = "https://data-dbg.krx.co.kr/svc/apis"
        headers = {"AUTH_KEY": api_key}

        def _n(row, key):
            v = row.get(key, "")
            try:
                return float(str(v).replace(",", "")) if v not in ("", "-", None) else 0.0
            except Exception:
                return 0.0

        today = date.today()
        for offset in range(days):
            d = today - timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            try:
                r = _rq.get(
                    f"{base_url}/{path}",
                    params={"basDd": d.strftime("%Y%m%d")},
                    headers=headers,
                    timeout=12,
                )
                if r.status_code != 200:
                    continue
                for row in r.json().get("OutBlock_1", []):
                    if str(row.get("IDX_NM", "")).strip() != idx_name:
                        continue
                    close = _n(row, "CLSPRC_IDX")
                    if close <= 0:
                        continue
                    return [{
                        "date": d.strftime("%Y-%m-%d"),
                        "open": _n(row, "OPNPRC_IDX"),
                        "high": _n(row, "HGPRC_IDX"),
                        "low": _n(row, "LWPRC_IDX"),
                        "close": close,
                        "volume": _n(row, "ACC_TRDVOL"),
                    }]
            except Exception as e:
                logger.debug(f"[KRX최근지수] {code} {d}: {e}")
        return []

    def _collect_derivative_indices(self) -> int:
        """KOSPI/KOSDAQ/KOSPI200/KOSDAQ150 — KRX API 실패 시 네이버 금융 fallback.

        반환: 저장 건수
        """
        import sqlite3 as _sl
        INDEX_MAP = {
            "^KS11": "KOSPI",
            "^KQ11": "KOSDAQ",
            "^KS200": "KPI200",   # KOSPI200
            "^KQ150": "KSQ150",   # KOSDAQ150
        }
        saved = 0
        conn = connect_stock_db(timeout=15)
        try:
            for code, naver_code in INDEX_MAP.items():
                rows = self._fetch_naver_index(naver_code, count=10)
                if not rows and code in ("^KS200", "^KQ150"):
                    rows = self._fetch_krx_index_recent(code, days=7)
                for row in rows:
                    dt_str = row["date"]
                    # 주말/공휴일 스킵
                    from datetime import date as _dt_cls
                    d = _dt_cls.fromisoformat(dt_str)
                    if d.weekday() >= 5:
                        continue
                    if row["close"] <= 0:
                        continue
                    cur = conn.execute("""
                        INSERT INTO price_history
                            (stock_code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(stock_code, date) DO UPDATE SET
                            open=excluded.open,
                            high=excluded.high,
                            low=excluded.low,
                            close=excluded.close,
                            volume=excluded.volume
                    """, (code, dt_str, row["open"], row["high"], row["low"], row["close"], row["volume"]))
                    saved += cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return saved

    def _startup_catchup(self) -> None:
        """서버 재시작 후 당일 누락된 잡을 감지해 즉시 실행.

        문제: _wait_until()은 다음날 해당 시각을 기다림.
             서버가 17:30 이후 재시작되면 당일 수급/KRX 수집 기회를 잃음.
        해결: 시작 30초 후 누락 여부 확인 → 즉시 수집.
        """
        import sqlite3 as _sl
        self._wait_secs(30)  # 서버 완전 기동 대기
        if self._stop_event.is_set():
            return

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 주말이면 스킵
        if now.weekday() >= 5:
            logger.info("[캐치업] 주말 — 스킵")
            return

        logger.info(f"[캐치업] {today_str} 당일 누락 데이터 확인 시작")

        # ① KOSPI/KOSDAQ/KOSPI200/KOSDAQ150 누락 확인 → KIS/네이버 fallback
        # 16:00 이후이고 오늘 데이터가 없으면 즉시 수집
        if now.hour >= 16:
            try:
                conn = connect_stock_db(timeout=15)
                missing = conn.execute(
                    "SELECT COUNT(*) FROM price_history WHERE stock_code IN ('^KS11','^KQ11','^KS200','^KQ150') AND date=?",
                    (today_str,)
                ).fetchone()[0]
                conn.close()
                if missing < 4:
                    logger.info(f"[캐치업] {today_str} 주요 지수 {missing}/4건 존재 → KIS/네이버 fallback")
                    with stock_db_write_lock("캐치업KIS지수", timeout=3) as acquired:
                        if not acquired:
                            logger.warning("[캐치업] 주요 지수 KIS/네이버 수집 스킵 — 다른 DB writer 실행 중")
                            n = 0
                        else:
                            n = self._collect_kis_core_indices()
                            n += self._collect_derivative_indices()
                            logger.info(f"[캐치업] 주요 지수 KIS/네이버 수집: {n}건 저장")
                    if n == 0:
                        self._job_kis_ohlcv_daily()
                else:
                    logger.info(f"[캐치업] 주요 지수 정상 ({missing}건 존재)")
            except Exception as e:
                logger.warning(f"[캐치업] KRX 지수 확인 오류: {e}")

        # ② 수급 amount (inst_net_buy_amt) 누락 확인
        # 17:00 이후이고 오늘 amt 데이터 < 50건이면 즉시 수집
        if now.hour >= 17:
            try:
                conn = connect_stock_db(timeout=15)
                amt_cnt = conn.execute(
                    "SELECT COUNT(*) FROM price_history WHERE date=? "
                    "AND inst_net_buy_amt IS NOT NULL AND inst_net_buy_amt != 0",
                    (today_str,)
                ).fetchone()[0]
                conn.close()
                if amt_cnt < 50:
                    logger.info(f"[캐치업] {today_str} 수급 amt {amt_cnt}건 부족 → 즉시 수집 (~43분 소요)")
                    _run_job_safe("캐치업수급", self._job_supply_daily)
                else:
                    logger.info(f"[캐치업] 수급 amt 정상 ({amt_cnt}건 존재)")
            except Exception as e:
                logger.warning(f"[캐치업] 수급 확인 오류: {e}")

        logger.info("[캐치업] 완료")

    # ══════════════════════════════════════════════════════════
    # 내부 유틸
    # ══════════════════════════════════════════════════════════

    def _wait_secs(self, secs: float) -> None:
        """stop_event를 체크하면서 대기."""
        self._stop_event.wait(timeout=max(0.0, secs))

    def _wait_until(self, hour: int, minute: int, skip_weekend: bool = True) -> None:
        secs = _seconds_until(hour, minute, skip_weekend)
        self._stop_event.wait(timeout=secs)

    @staticmethod
    def _get_all_tracked_codes(conn) -> list[str]:
        """watchlist + 포트폴리오 종목코드 합집합."""
        codes: set[str] = set()
        for table in ("watchlist", "portfolio"):
            try:
                rows = conn.execute(
                    f"SELECT DISTINCT stock_code FROM {table} "
                    f"WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'"
                ).fetchall()
                codes.update(r[0] for r in rows)
            except Exception:
                pass
        return list(codes)

    @staticmethod
    def _get_active_codes(conn) -> list[str]:
        """장중 실시간 갱신 대상: watchlist + 활성 peak_holding."""
        codes: set[str] = set()
        for query in (
            "SELECT DISTINCT stock_code FROM watchlist "
            "WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'",
            "SELECT DISTINCT stock_code FROM peak_holding "
            "WHERE is_active=1 AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'",
            "SELECT DISTINCT stock_code FROM portfolio "
            "WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'",
        ):
            try:
                rows = conn.execute(query).fetchall()
                codes.update(r[0] for r in rows)
            except Exception:
                pass
        return list(codes)

    def _job_cashflow_batch(self) -> None:
        """DART 전종목 현금흐름표 배치 수집 (missing_only=True)."""
        try:
            import subprocess
            result = subprocess.run(
                ["venv/bin/python3", "collect_dart_cashflow_batch.py", "--fill-missing", "--years", "5"],
                capture_output=True, text=True, timeout=14400,
                cwd="/Applications/stock_dashboard",
            )
            logger.info(f"[현금흐름배치] 완료: {result.stdout[-300:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[현금흐름배치] 오류: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[현금흐름배치] 잡 오류: {e}")

    def _job_naver_fundamentals(self) -> None:
        """네이버금융 전종목 PBR/PER/EPS 배치 수집 → financial_data + stock_universe 동시 갱신."""
        try:
            import subprocess
            result = subprocess.run(
                ["venv/bin/python3", "collect_naver_fundamentals.py"],  # 전종목 매일 갱신
                capture_output=True, text=True, timeout=7200,
                cwd="/Applications/stock_dashboard",
            )
            logger.info(f"[네이버밸류에이션] 완료: {result.stdout[-200:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[네이버밸류에이션] 오류: {result.stderr[-200:]}")
        except Exception as e:
            logger.error(f"[네이버밸류에이션] 잡 오류: {e}")

    # ══════════════════════════════════════════════════════════
    # 텐버거 발굴 스케줄 (09:00 / 12:00 / 15:00 평일)
    # ══════════════════════════════════════════════════════════

    def _loop_tenbagger_morning(self) -> None:
        """평일 09:00 — 장 시작 직후 텐버거 후보 발굴."""
        self._wait_secs(45)
        while not self._stop_event.is_set():
            self._wait_until(9, 0, skip_weekend=True)
            _run_job_safe("텐버거오전", lambda: self._job_tenbagger("morning"))

    def _loop_tenbagger_noon(self) -> None:
        """평일 12:00 — 오전장 수급 반영 텐버거 발굴."""
        self._wait_secs(50)
        while not self._stop_event.is_set():
            self._wait_until(12, 0, skip_weekend=True)
            _run_job_safe("텐버거정오", lambda: self._job_tenbagger("noon"))

    def _loop_tenbagger_afternoon(self) -> None:
        """평일 15:00 — 장 종료 전 최종 텐버거 발굴."""
        self._wait_secs(55)
        while not self._stop_event.is_set():
            self._wait_until(15, 0, skip_weekend=True)
            _run_job_safe("텐버거오후", lambda: self._job_tenbagger("afternoon"))

    def _job_tenbagger(self, run_type: str) -> None:
        """텐버거 발굴 실행 — tenbagger_engine.run_discovery 위임."""
        if datetime.now().weekday() >= 5:
            return
        try:
            from tenbagger_engine import run_discovery
            results = run_discovery(run_type)
            logger.info(f"[텐버거{run_type}] {len(results)}종목 선정")
        except Exception as e:
            logger.error(f"[텐버거{run_type}] 오류: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════
    # NPS 고용 데이터 자동 업데이트 (매일 06:00)
    # ══════════════════════════════════════════════════════════

    def _loop_nps_daily(self) -> None:
        """매일 06:00 — 국민연금 고용 데이터 신규 월 확인 및 수집."""
        self._wait_secs(65)
        while not self._stop_event.is_set():
            self._wait_until(6, 0)
            _run_job_safe("NPS고용업데이트", self._job_nps_daily)

    def _job_nps_daily(self) -> None:
        """NPS 신규 월 데이터 수집 — update_nps_daily.py 위임."""
        try:
            import subprocess
            result = subprocess.run(
                ["venv/bin/python3", "employment_monitor/update_nps_daily.py"],
                capture_output=True, text=True, timeout=7200,
                cwd="/Applications/stock_dashboard",
            )
            out = (result.stdout or "")[-300:]
            logger.info(f"[NPS고용] 완료: {out}")
            if result.returncode != 0:
                logger.warning(f"[NPS고용] 오류(returncode={result.returncode}): {(result.stderr or '')[-200:]}")
            # 캐시 무효화
            try:
                import sys, importlib
                emp_mod = sys.modules.get("employment_monitor.routes_employment_v2")
                if emp_mod:
                    emp_mod._trend_data_cache = None
                    emp_mod._trend_data_cache_at = 0
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[NPS고용] 잡 오류: {e}")

    # ══════════════════════════════════════════════════════════
    # HOT 섹터 블로그 자동파싱 (매일 07:00)
    # ══════════════════════════════════════════════════════════

    def _loop_sector_blog(self) -> None:
        """매일 07:00 — 네이버 블로그 신규 포스트 파싱 + 기존 종목 없는 포스트 재파싱."""
        self._wait_secs(70)
        while not self._stop_event.is_set():
            self._wait_until(7, 0)
            _run_job_safe("HOT섹터블로그", self._job_sector_blog)

    def _job_sector_blog(self) -> None:
        """블로그 자동파싱 — Sector_define/blog_parser.run_parser() 위임."""
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from Sector_define.blog_parser import run_parser
            run_parser(reprocess_empty=True)
            logger.info("[HOT섹터] 블로그 파싱 완료")
        except Exception as e:
            logger.error(f"[HOT섹터] 블로그 파싱 오류: {e}")

    def _loop_ai_leading_sector(self) -> None:
        """매일 07:20 — 미국 증시/뉴스 기반 주도 섹터 AI 리포트 캐시 갱신."""
        self._wait_secs(75)
        while not self._stop_event.is_set():
            self._wait_until(7, 20)
            _run_job_safe("AI주도섹터", self._job_ai_leading_sector)

    def _job_ai_leading_sector(self) -> None:
        """AI 주도 섹터 리포트 — routes.tenbagger.refresh_sector_ai_report() 위임."""
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from routes.tenbagger import refresh_sector_ai_report
            data = refresh_sector_ai_report(limit=30, force=True)
            sectors = ", ".join([s.get("kr", s.get("ticker", "")) for s in data.get("sectors", [])[:3]])
            logger.info(f"[AI주도섹터] 리포트 갱신 완료: {sectors}")
        except Exception as e:
            logger.error(f"[AI주도섹터] 리포트 갱신 오류: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════
    # 섹터 AI 텔레그램 — 오전 8:30 / 점심 12:30
    # ══════════════════════════════════════════════════════════

    def _loop_sector_morning_tg(self) -> None:
        """매일 08:30 (평일) — AI 주도 섹터 리포트 텔레그램 발송."""
        self._wait_secs(80)
        while not self._stop_event.is_set():
            self._wait_until(8, 30, skip_weekend=True)
            _run_job_safe("섹터오전텔레그램", self._job_sector_morning_tg)

    def _job_sector_morning_tg(self) -> None:
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from routes.tenbagger import _send_sector_ai_telegram
            ok = _send_sector_ai_telegram("morning")
            logger.info(f"[섹터오전텔레그램] {'발송완료' if ok else '스킵(캐시없음)'}")
        except Exception as e:
            logger.error(f"[섹터오전텔레그램] 오류: {e}", exc_info=True)

    def _loop_sector_lunch_tg(self) -> None:
        """매일 12:30 (평일) — 오전장 마감 섹터 분석 텔레그램 발송."""
        self._wait_secs(85)
        while not self._stop_event.is_set():
            self._wait_until(12, 30, skip_weekend=True)
            _run_job_safe("섹터점심텔레그램", self._job_sector_lunch_tg)

    def _job_sector_lunch_tg(self) -> None:
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from routes.tenbagger import _send_sector_ai_telegram
            ok = _send_sector_ai_telegram("lunch")
            logger.info(f"[섹터점심텔레그램] {'발송완료' if ok else '스킵(캐시없음)'}")
        except Exception as e:
            logger.error(f"[섹터점심텔레그램] 오류: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════
    # 스탁이지 전략 분석 (매일 16:30 + 일요일 09:00 주간 요약)
    # ══════════════════════════════════════════════════════════

    def _loop_stockeasy_analysis(self) -> None:
        """매일 16:30 장마감 후 — 스탁이지 3전략 분석 + 텔레그램."""
        self._wait_secs(90)
        while not self._stop_event.is_set():
            self._wait_until(16, 30)
            _run_job_safe("스탁이지분석", self._job_stockeasy_analysis)

    def _loop_stockeasy_weekly(self) -> None:
        """매주 일요일 09:00 — 주간 전략 패턴 요약 리포트."""
        self._wait_secs(95)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 다음 일요일 09:00 계산 (weekday: 0=월 ~ 6=일)
            days_until_sun = (6 - now.weekday()) % 7
            if days_until_sun == 0 and now.hour < 9:
                next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                if days_until_sun == 0:
                    days_until_sun = 7
                next_run = (now + timedelta(days=days_until_sun)).replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("스탁이지주간", self._job_stockeasy_weekly)

    def _job_stockeasy_analysis(self) -> None:
        """스탁이지 3전략 현황 분석 — stockeasy_analyzer.run_daily_analysis() 위임."""
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from stockeasy_analyzer import run_daily_analysis
            run_daily_analysis()
            logger.info("[스탁이지] 일별 분석 완료")
        except Exception as e:
            logger.error(f"[스탁이지] 분석 오류: {e}")

    def _job_stockeasy_weekly(self) -> None:
        """주간 전략 패턴 요약 — DB 누적 분석 결과 기반 리포트."""
        try:
            import sys as _sys
            if "/Applications/stock_dashboard" not in _sys.path:
                _sys.path.insert(0, "/Applications/stock_dashboard")
            from stockeasy_analyzer import run_weekly_summary
            run_weekly_summary()
            logger.info("[스탁이지] 주간 요약 완료")
        except Exception as e:
            logger.error(f"[스탁이지] 주간 요약 오류: {e}")

    # ══════════════════════════════════════════════════════════
    # 고용보험 상시인원 배치 (매월 5일 02:00)
    # ══════════════════════════════════════════════════════════

    def _loop_insurance_monthly(self) -> None:
        """매월 5일 02:00 — 고용보험 상시인원 전종목 배치 수집."""
        self._wait_secs(80)
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.day == 5 and now.hour < 2:
                next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
            else:
                next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=5)
                next_run   = next_month.replace(hour=2, minute=0, second=0, microsecond=0)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("고용보험배치", self._job_insurance_monthly)

    def _job_insurance_monthly(self) -> None:
        """고용보험 상시인원 전종목 수집 — fetch_employment_insurance.py 위임."""
        try:
            import subprocess
            result = subprocess.run(
                ["venv/bin/python3", "employment_monitor/fetch_employment_insurance.py", "--delay", "0.4"],
                capture_output=True, text=True, timeout=14400,
                cwd="/Applications/stock_dashboard",
            )
            out = (result.stdout or "")[-300:]
            logger.info(f"[고용보험] 완료: {out}")
            if result.returncode != 0:
                logger.warning(f"[고용보험] 오류(returncode={result.returncode}): {(result.stderr or '')[-200:]}")
        except Exception as e:
            logger.error(f"[고용보험] 잡 오류: {e}")

    # ══════════════════════════════════════════════════════════
    # BigQuery 동기화 (매일 23:30)
    # ══════════════════════════════════════════════════════════

    def _loop_bigquery_sync(self) -> None:
        """매일 23:30 stock.db → BigQuery 증분 동기화 (price_history 최근 7일 + 소형 테이블 FULL REFRESH)."""
        logger.info("[BigQuery동기화] 루프 시작")
        self._wait_secs(60)  # 서버 기동 여유
        while not self._stop_event.is_set():
            now = datetime.now()
            # 매일 23:30 실행
            target = now.replace(hour=23, minute=30, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info(f"[BigQuery동기화] 다음 실행: {target.strftime('%m/%d %H:%M')} ({wait/3600:.1f}시간 후)")
            self._stop_event.wait(wait)
            if self._stop_event.is_set():
                break
            try:
                self._job_bigquery_sync()
            except Exception as e:
                logger.error(f"[BigQuery동기화] 오류: {e}")
        logger.info("[BigQuery동기화] 루프 종료")

    def _job_bigquery_sync(self) -> None:
        """BigQuery 동기화 실제 실행"""
        logger.info("[BigQuery동기화] 증분 동기화 시작")
        try:
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "/Applications/stock_dashboard/bigquery_sync.py",
                 "--mode", "daily", "--days", "7"],
                capture_output=True, text=True, timeout=1800
            )
            if result.returncode == 0:
                logger.info(f"[BigQuery동기화] ✅ 완료")
            else:
                logger.error(f"[BigQuery동기화] ❌ 오류:\n{result.stderr[-2000:]}")
        except subprocess.TimeoutExpired:
            logger.error("[BigQuery동기화] 타임아웃 (30분 초과)")
        except Exception as e:
            logger.error(f"[BigQuery동기화] 실행 오류: {e}")

    # ══════════════════════════════════════════════════════════
    # DART 수주공시 (매일 08:00 / 13:00 / 17:00)
    # ══════════════════════════════════════════════════════════

    def _loop_wlb_monthly(self) -> None:
        """근로복지공단 고용보험 — 매일 20:30 API totalCount 변화 감지, 변화 시 전체 수집 (~14분).

        동작 원리:
        - 매일 저녁 API totalCount(전국 사업장 수)를 확인
        - 마지막 수집 시점과 비교해 달라지면 전체 스캔 수행
        - 같은 달에 여러 번 갱신 가능 (사업장 신규등록·폐업·인원변동 반영)
        """
        self._wait_secs(120)
        _last_total = None   # 마지막 확인한 totalCount
        while not self._stop_event.is_set():
            now = datetime.now()
            # 매일 20:30 실행
            target = now.replace(hour=20, minute=30, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            secs = max(0.0, (target - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("근로복지공단변화감지", self._job_wlb_check_and_collect)

    def _job_wlb_check_and_collect(self) -> None:
        """근로복지공단: totalCount 변화 감지 후 필요 시 전체 수집."""
        try:
            import sys as _sys, requests as _req, xml.etree.ElementTree as _ET
            _wlb_path = os.path.join(os.path.dirname(__file__), 'employment_monitor')
            if _wlb_path not in _sys.path:
                _sys.path.insert(0, _wlb_path)
            from collect_labor_welfare import collect_all, save_to_db, init_db, API_KEY, BASE_URL, PAGE_SIZE
            import sqlite3 as _sl
            from datetime import datetime as _dt

            # 1) 현재 totalCount 확인
            r = _req.get(f'{BASE_URL}/getGySjBoheomBsshItem', params={
                'serviceKey': API_KEY, 'pageNo': 1, 'numOfRows': 1, 'opaBoheomFg': '1'
            }, timeout=30)
            root = _ET.fromstring(r.text)
            current_total = int(root.findtext('.//totalCount', '0') or '0')

            # 2) DB에서 마지막 수집 시의 total (wlb_meta 테이블)
            emp_db = os.path.join(_wlb_path, 'employment.db')
            conn = _sl.connect(emp_db)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wlb_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)
            row = conn.execute("SELECT value FROM wlb_meta WHERE key='last_total_count'").fetchone()
            last_total = int(row[0]) if row else 0
            conn.close()

            logger.info(f"[근로복지공단] totalCount: 현재={current_total:,}, 이전={last_total:,}")

            if current_total == last_total:
                logger.info("[근로복지공단] 변화 없음 — 수집 생략")
                return

            # 3) 변화 감지 → 전체 수집
            data_ym = _dt.now().strftime('%Y%m')
            logger.info(f"[근로복지공단] 변화 감지({last_total:,}→{current_total:,}) — 전체 수집 시작 (ym={data_ym})")
            init_db()
            aggregated = collect_all(data_ym, test_pages=0)
            if aggregated:
                save_to_db(data_ym, aggregated)
                # totalCount 갱신
                conn = _sl.connect(emp_db)
                conn.execute("""
                    INSERT INTO wlb_meta(key, value, updated_at) VALUES('last_total_count', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """, (str(current_total), _dt.now().isoformat()))
                conn.commit(); conn.close()
                logger.info(f"[근로복지공단] {len(aggregated)}개 기업 저장 완료 (ym={data_ym}), total={current_total:,}")
            else:
                logger.warning("[근로복지공단] 매칭 결과 없음")
        except Exception as e:
            logger.error(f"[근로복지공단] 잡 오류: {e}", exc_info=True)

    def _job_wlb_monthly(self) -> None:
        """근로복지공단 고용보험 전사업장 스캔 → wlb_monthly 테이블 저장 (수동 호출용)."""
        self._job_wlb_check_and_collect()

    def _loop_dart_contracts(self) -> None:
        """DART 수주·공급계약 공시 — 하루 3회 (08:00/13:00/17:00) 수집."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            now = datetime.now()
            targets = [8, 13, 17]  # 수집 시각 (시)
            next_times = []
            for h in targets:
                candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
                if candidate > now:
                    next_times.append(candidate)
            if next_times:
                next_run = min(next_times)
            else:
                # 오늘 모두 지났으면 내일 08:00
                next_run = (now + timedelta(days=1)).replace(
                    hour=8, minute=0, second=0, microsecond=0
                )
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("DART수주공시", self._job_dart_contracts)

    def _job_dart_contracts(self) -> None:
        """DART 수주·공급계약 공시 수집 + AI 분석 + 텔레그램 알림."""
        try:
            from collectors.dart_contract_collector import collect_dart_contracts
            results = collect_dart_contracts(days=1, min_signal=2)
            if results:
                logger.info(f"[DART수주] {len(results)}건 처리 완료")
            else:
                logger.debug("[DART수주] 신규 수주공시 없음")
        except Exception as e:
            logger.error(f"[DART수주] 잡 오류: {e}")

    # ── 한경 컨센서스 수집 ─────────────────────────────────────────────────

    def _loop_consensus(self) -> None:
        """매일 04:00 — 한경 컨센서스 증분 수집 (최근 3일치, 03:30 공시확인 후)."""
        self._wait_secs(20)
        while not self._stop_event.is_set():
            self._wait_until(4, 0, skip_weekend=False)   # 주말도 수집 (리포트는 평일이지만 DB 갱신)
            _run_job_safe("컨센서스수집", self._job_consensus)

    def _job_consensus(self) -> None:
        """한경 컨센서스 최근 3일치 증분 수집."""
        try:
            from collectors.hankyung_consensus_collector import collect_consensus
            saved = collect_consensus(db_path="stock.db", days=3, full=False)
            logger.info(f"[컨센서스] 증분 수집 완료 — {saved}건 신규 저장")
        except Exception as e:
            logger.error(f"[컨센서스] 잡 오류: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════
    # 재무 무결점 — 월간 + 분기 마감 후
    # ══════════════════════════════════════════════════════════

    def _loop_financial_integrity_monthly(self) -> None:
        """매월 1일 05:00 — 재무제표·현금흐름표 무결점 검사 및 자동 보완."""
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.day == 1 and now.hour < 5:
                next_run = now.replace(hour=5, minute=0, second=0, microsecond=0)
            else:
                next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
                next_run   = next_month.replace(hour=5, minute=0, second=0, microsecond=0)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("재무무결점월간", self._job_financial_integrity_monthly)

    def _job_financial_integrity_monthly(self) -> None:
        """월간 재무 무결점 검사 실행."""
        try:
            from check_financial_integrity import run_integrity_check
            run_integrity_check(n_quarters=8, trigger="월간자동")
            logger.info("[재무무결점] 월간 점검 완료")
        except Exception as e:
            logger.error(f"[재무무결점] 월간 잡 오류: {e}", exc_info=True)

    def _loop_financial_integrity_quarterly(self) -> None:
        """분기 공시 마감 1주일 후 자동 재무 보완.

        한국 상장사 공시 마감일 (법정 기한):
          - 사업보고서 (Q4/연간): 3월 31일  → 1주 후 4월 7일 05:00
          - 분기보고서 (Q1):      5월 15일  → 1주 후 5월 22일 05:00
          - 반기보고서 (Q2):      8월 14일  → 1주 후 8월 21일 05:00
          - 분기보고서 (Q3):      11월 14일 → 1주 후 11월 21일 05:00
        """
        self._wait_secs(90)
        # (월,일) 기준 연간 4회 실행 날짜
        QUARTERLY_DATES = [(4, 7), (5, 22), (8, 21), (11, 21)]

        while not self._stop_event.is_set():
            now = datetime.now()
            # 다음 실행 날짜 계산
            next_run = None
            for (m, d) in QUARTERLY_DATES:
                candidate = now.replace(month=m, day=d, hour=5, minute=0, second=0, microsecond=0)
                if candidate > now:
                    next_run = candidate
                    break
            if next_run is None:
                # 올해 모든 날짜 지남 → 내년 첫 번째
                next_run = now.replace(year=now.year + 1, month=4, day=7,
                                       hour=5, minute=0, second=0, microsecond=0)

            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            label = next_run.strftime("%Y-%m-%d")
            logger.info(f"[재무무결점분기] 다음 실행: {label} (대기 {secs/3600:.1f}h)")
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("재무무결점분기", self._job_financial_integrity_quarterly)

    def _job_financial_integrity_quarterly(self) -> None:
        """분기 마감 후 전체 재무 보완 (최근 4분기 집중 점검)."""
        try:
            from check_financial_integrity import run_integrity_check
            run_integrity_check(n_quarters=4, trigger="분기마감자동")
            logger.info("[재무무결점] 분기 마감 후 점검 완료")
        except Exception as e:
            logger.error(f"[재무무결점] 분기 잡 오류: {e}", exc_info=True)

    # ──────────────────────────────────────────────────────────
    # KRX 종목기본정보 + 일별 변동 추적 (매일 18:35)
    # ──────────────────────────────────────────────────────────
    def _loop_krx_base_info(self) -> None:
        """매일 18:35 영업일 — KRX 종목기본정보 갱신 + 변동 감지.

        18:00 KRX 일별매매 수집 후 35분 뒤에 실행 (KRX API 부하 분산).
        호출량은 일 2건 (KRX 한도 500건의 0.4%)로 매우 가벼움.
        """
        logger.info("[KRX종목기본정보] 루프 시작")
        self._wait_secs(45)  # 서버 기동 여유
        while not self._stop_event.is_set():
            self._wait_until(18, 35, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                _run_job_safe("KRX종목기본정보", self._job_krx_base_info)
            except Exception as e:
                logger.error(f"[KRX종목기본정보] 잡 오류: {e}")
            self._wait_secs(23 * 3600)
        logger.info("[KRX종목기본정보] 루프 종료")

    def _job_krx_base_info(self) -> None:
        """KRX 종목기본정보 수집 → stock_universe 보강 + 일별 스냅샷 + 변동 감지."""
        from collectors.krx_isu_base_info import collect_base_info
        today = date.today()
        if not is_kr_trading_day(today):
            logger.info(f"[KRX종목기본정보] {today} 휴장일 — 스킵")
            return
        res = collect_base_info(today.isoformat())
        logger.info(
            f"[KRX종목기본정보] 갱신 {res['updated']} / 스냅샷 {res['history']} / "
            f"변경 {res['changes']} / 스킵 {res['skipped']}"
        )

    # ──────────────────────────────────────────────────────────
    # FnGuide 연결/별도 재무제표 + 스냅샷 (매월 3일 05:00)
    # ──────────────────────────────────────────────────────────
    def _loop_fnguide_financial_monthly(self) -> None:
        """FnGuide 재무 수집 — 두 가지 모드:

        [초기 백로그 모드] stale_days=7 미만 종목이 남아 있는 동안:
          매일 01:30 실행 → 미검증 종목을 rate limit 소진까지 처리

        [정기 유지보수 모드] 백로그 소진 후:
          분기 공시 직후(1·4·7·10월 15일) 1회 실행 → 최신 공시 반영
          → 파싱이 정확해지면 재검증 불필요 (자동 학습 덕분)
        """
        logger.info("[FnGuide재무] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 백로그 잔량 확인
            try:
                import sqlite3 as _sl
                _c = _sl.connect(str(Path(__file__).resolve().parent / "stock.db"))
                unvalidated = _c.execute("""
                    SELECT COUNT(DISTINCT su.stock_code)
                    FROM stock_universe su
                    LEFT JOIN financial_source_snapshot fss
                      ON fss.stock_code=su.stock_code AND fss.data_source='fnguide'
                    WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                      AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    GROUP BY 1 HAVING MAX(fss.fetched_at) IS NULL
                      OR MAX(fss.fetched_at) < datetime('now','-7 days')
                """).fetchone()
                _c.close()
                backlog = (unvalidated[0] if unvalidated else 0)
            except Exception:
                backlog = 9999  # 알 수 없으면 백로그 모드 유지

            if backlog > 50:
                # 백로그 모드: 매일 01:30
                next_run = now.replace(hour=1, minute=30, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                logger.info(f"[FnGuide재무] 백로그 {backlog}개 — 매일 모드")
            else:
                # 정기 유지보수 모드: 분기 공시 직후 15일 (1·4·7·10월)
                y, mo, d = now.year, now.month, now.day
                _quarterly_months = [1, 4, 7, 10]
                run_month = next(
                    (m for m in _quarterly_months if m > mo or (m == mo and d <= 15)),
                    _quarterly_months[0]
                )
                run_year = y if run_month >= mo else y + 1
                next_run = datetime(run_year, run_month, 15, 2, 0, 0)
                if next_run <= now:
                    next_run = datetime(run_year, run_month + 3 if run_month <= 9 else 1,
                                       15, 2, 0, 0)
                logger.info(f"[FnGuide재무] 정기 모드 — 다음 실행: {next_run.strftime('%Y-%m-%d')}")

            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("FnGuide재무", self._job_fnguide_financial_monthly)

    def _job_fnguide_financial_monthly(self) -> None:
        """FnGuide를 단일 권위 소스로 재무 수집·보정.

        파서 자동 학습 덕분에 첫 수집 후에는 재검증 없이 정확도 유지.
        - --override: FnGuide 값이 DB와 다르면 FnGuide 기준으로 덮어쓰기
        - --no-cross-validate: DART 쿼터 보존
        - --stale-days 7: 최근 1주일 내 수집된 종목 스킵
        """
        import subprocess
        import sys
        script = str(Path(__file__).resolve().parent / "collectors" / "fnguide_financial_collector.py")
        year_from = datetime.now().year - 5
        year_to   = datetime.now().year + 1
        logger.info(f"[FnGuide재무] CFS 수집 시작 ({year_from}~{year_to})")
        try:
            result = subprocess.run(
                [sys.executable, script,
                 "--limit",            "9999",
                 "--year-from",        str(year_from),
                 "--year-to",          str(year_to),
                 "--stale-days",       "7",
                 "--override",
                 "--no-cross-validate",
                 "--report-types",     "CFS"],
                capture_output=True, text=True, timeout=7200,
                cwd=str(Path(__file__).resolve().parent),
            )
            if result.returncode == 0:
                logger.info(f"[FnGuide재무] CFS 완료: {result.stdout[-300:]}")
            else:
                logger.error(f"[FnGuide재무] CFS 오류: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[FnGuide재무] CFS 예외: {e}")

        # OFS(별도재무제표): 분기 15일에만 추가 실행
        if datetime.now().day == 15:
            logger.info("[FnGuide재무] OFS 추가 수집")
            try:
                result = subprocess.run(
                    [sys.executable, script,
                     "--limit",        "9999",
                     "--year-from",    str(year_from),
                     "--year-to",      str(year_to),
                     "--stale-days",   "85",
                     "--override",
                     "--no-cross-validate",
                     "--report-types", "OFS"],
                    capture_output=True, text=True, timeout=7200,
                    cwd=str(Path(__file__).resolve().parent),
                )
                if result.returncode == 0:
                    logger.info(f"[FnGuide재무] OFS 완료: {result.stdout[-200:]}")
            except Exception as e:
                logger.error(f"[FnGuide재무] OFS 예외: {e}")

    # ══════════════════════════════════════════════════════════
    # 수출입 10일 가집계 수집 (매주 월요일 06:00)
    # ══════════════════════════════════════════════════════════

    def _loop_trade_provisional(self) -> None:
        """매주 월요일 06:00 — 관세청 10일 단위 가집계 수집."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 다음 월요일 06:00 계산
            days_until_monday = (7 - now.weekday()) % 7  # 0=오늘이 월요일
            if days_until_monday == 0 and now.hour >= 6:
                days_until_monday = 7
            next_run = (now + timedelta(days=days_until_monday)).replace(
                hour=6, minute=0, second=0, microsecond=0
            )
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("수출입가집계", self._job_trade_provisional)

    def _job_trade_provisional(self) -> None:
        """관세청 10일 가집계 + analysis2 캐시 재빌드."""
        import subprocess, sys
        lab = str(Path(__file__).resolve().parent / "hs_trade_lab")
        try:
            # 1. 가집계 수집
            r = subprocess.run(
                [sys.executable, str(Path(lab) / "scripts" / "collect_provisional_10day.py")],
                capture_output=True, text=True, timeout=300,
                cwd=lab,
            )
            logger.info(f"[수출입가집계] 수집 완료: {r.stdout[-200:]}")
            if r.returncode != 0:
                logger.error(f"[수출입가집계] 수집 오류: {r.stderr[-300:]}")
                return
            # 2. analysis2 캐시 재빌드
            r2 = subprocess.run(
                [sys.executable, str(Path(lab) / "scripts" / "rebuild_analysis2_cache.py")],
                capture_output=True, text=True, timeout=600,
                cwd=lab,
            )
            if r2.returncode == 0:
                logger.info(f"[수출입가집계] 캐시 재빌드 완료: {r2.stdout[-200:]}")
            else:
                logger.error(f"[수출입가집계] 캐시 재빌드 오류: {r2.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[수출입가집계] 예외: {e}")
