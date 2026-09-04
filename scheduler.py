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
from pathlib import Path
from typing import Callable

import config
from collection_health import (
    evaluate_job_outputs,
    finish_collection_run,
    interrupt_running_collection_runs,
    refresh_job_health_snapshot,
    start_collection_run,
)
from db_utils import connect_stock_db, stock_db_write_lock

logger = logging.getLogger(__name__)

_DB_WRITE_JOBS = {
    "야간배치",
    "월간업데이트",
    "공시확인",
    "공시최근증분",
    "장중1분가격",
    "장중5분수급",
    "장마감",
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
    "섹터로테이션캐시",
    "섹터지수보완",
    "스탁이지분석",
    "스탁이지주간",
    "보유종목매도알림",
    "DART수주공시",
    "DART수주계약",
    "DART희석공시",
    "DART희석공시마감",
    "키움신용잔고",
    "키움외국인지분율",
    "키움대량체결",
    "DART임원매매",
    "DART수주잔고",
    "DART원가재고",
    "DART매입재료비",
    "DART직원수",
    "DART임직원CH",
    "근로복지공단",
    "BigQuery동기화",
    "BQ3배파이프라인",
    "BQ아침알림",
    "컨센서스수집",
    "재무무결성일일",
    "캐치업수급",
    "캐치업KIS지수",
    "캐치업KRX지수",
    "KRX종목기본정보",
    "FnGuide재무월간",   # ★ 월간 연결/별도 재무제표 + 스냅샷
    "스탁이지30분동기화",
    "시장시그널브리핑",
    "글로벌매크로수집",
    "거시지표백테스트",
    "V14장중10분",
    "키움실시간스냅샷",
    "WAL일별체크",
    "실적신호스캔",
    "텐버거위클리",
    "텐버거트리거",
    "체리형부패밀리학습",
    "KRX프로그램매매",
    "종목프로그램매매",
    "미국일별시세팩터수집",
    "미국바이오파이프라인",
    "소스인텔리전스주간",
}
# 의도적으로 _DB_WRITE_JOBS에서 제외한 잡 (stock.db에 쓰지 않으므로 배타적 writer 락 불필요):
#   - 키움연결체크: KiwoomCollector.health_check()로 토큰 상태만 확인하고 로깅만 함.
#     10분 주기로 락을 다투다 2026-07 이후에만 404회 lock_timeout을 냈다.
#   - 페이지데이터감사: scripts/audit_all_page_data_quality.py는 전 테이블을 읽고
#     research_outputs/*.md만 쓴다 (DB write 0건).

_DB_LOCK_RETRY_DELAYS = tuple(
    int(value.strip())
    for value in os.getenv("COLLECTION_DB_LOCK_RETRY_SECONDS", "0,60,300").split(",")
    if value.strip().isdigit()
) or (0, 60, 300)
_DB_LOCK_TIMEOUT_SECONDS = float(os.getenv("COLLECTION_DB_LOCK_TIMEOUT_SECONDS", "30"))

# 주간/월간 잡은 한 번 굶으면 다음 기회가 7~30일 뒤라, 기본 재시도 예산(0+60+300초 ≈ 6분)으로는
# 장시간 락 보유자를 절대 기다려낼 수 없다. 실측: 2026-08-02(일) DART수주잔고가 01:20~07:47
# (6.5시간) 락을 쥔 동안 그 뒤 예약된 일요일 잡 5개(DART원가재고/매입재료비/임원매매/직원수/임직원CH)가
# 전부 재시도를 소진하고 그 주를 통째로 건너뛰었다. 이런 잡은 몇 시간이든 기다리는 편이 옳다.
_DB_LOCK_LONG_WAIT_JOBS = {
    "현금흐름배치",
    "DART원가재고",
    "DART매입재료비",
    "DART임원매매",
    "DART직원수",
    "DART임직원CH",
    "DART수주잔고",
    "DART세그먼트",
    "FnGuide재무월간",
    "월간업데이트",
    "텐버거위클리",
    "스탁이지주간",
}
_DB_LOCK_LONG_RETRY_DELAYS = tuple(
    int(value.strip())
    for value in os.getenv(
        "COLLECTION_DB_LOCK_LONG_RETRY_SECONDS", "0,300,900,1800,1800,1800,1800"
    ).split(",")
    if value.strip().isdigit()
) or (0, 300, 900, 1800, 1800, 1800, 1800)

# 장중 시간 (KST)
_MARKET_OPEN  = (9,  0)
_MARKET_CLOSE = (15, 40)

from trading_calendar import is_kr_trading_day, is_trading_day  # noqa: E402


def _recent_price_trade_dates(
    limit: int = 5,
    *,
    min_coverage: int = 2000,
    include_today_after_close: bool = False,
) -> list[str]:
    """Return recent fully populated trade dates as YYYYMMDD.

    Price history is the most reliable local signal that a Korean trading day
    has finished and has enough listed-stock coverage to run dependent jobs.
    """
    now = datetime.now()
    today = now.date().isoformat()
    params: list[object] = [min_coverage, limit]
    today_clause = ""
    if not include_today_after_close or (now.hour, now.minute) < _MARKET_CLOSE:
        today_clause = "WHERE date < ?"
        params = [today, min_coverage, limit]

    try:
        with connect_stock_db(timeout=30, row_factory=None, readonly=True) as conn:
            rows = conn.execute(
                f"""
                SELECT date
                FROM price_history
                {today_clause}
                GROUP BY date
                HAVING COUNT(DISTINCT stock_code) >= ?
                ORDER BY date DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [str(row[0]).replace("-", "") for row in rows]
    except Exception as exc:
        logger.warning(f"[스케줄러] 최근 거래일 조회 실패: {exc}")
        return [date.today().strftime("%Y%m%d")]


def _missing_dataset_dates(
    table: str,
    date_col: str,
    dates: list[str],
    *,
    min_coverage: int,
    coverage_expr: str,
    where: str = "",
) -> list[str]:
    """Return dates whose local table coverage is below the required count."""
    if not dates:
        return []
    iso_dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    placeholders = ",".join("?" for _ in iso_dates)
    where_sql = f" AND ({where})" if where else ""
    try:
        with connect_stock_db(timeout=30, row_factory=None, readonly=True) as conn:
            rows = conn.execute(
                f"""
                SELECT {date_col}, {coverage_expr} AS coverage
                FROM {table}
                WHERE {date_col} IN ({placeholders}){where_sql}
                GROUP BY {date_col}
                """,
                iso_dates,
            ).fetchall()
        coverage_by_date = {str(row[0]): int(row[1] or 0) for row in rows}
        return [
            raw
            for raw, iso in zip(dates, iso_dates)
            if coverage_by_date.get(iso, 0) < min_coverage
        ]
    except Exception as exc:
        logger.warning(f"[스케줄러] 결측 날짜 조회 실패 {table}: {exc}")
        return dates


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


def _current_lock_holder() -> str:
    """Best-effort read of who currently holds the stock.db writer lock.

    stock_db_write_lock writes "<pid> <job-name> <epoch>" into the lock file
    while held, so a starved job can name its blocker instead of just logging
    "timeout". Purely diagnostic — never raises.
    """
    try:
        from db_utils import DB_WRITE_LOCK_PATH

        raw = DB_WRITE_LOCK_PATH.read_text().strip()
        if not raw:
            return "unknown(빈 락파일 — 방금 해제됐거나 비스케줄러 프로세스)"
        parts = raw.split()
        if len(parts) >= 3 and parts[2].isdigit():
            held_for = int(time.time()) - int(parts[2])
            return f"pid={parts[0]} job={parts[1]} 보유 {held_for // 60}분{held_for % 60}초"
        return raw[:120]
    except Exception:
        return "unknown"


def _run_job_safe(name: str, fn: Callable) -> bool:
    """Run one collection job with a durable ledger and DB-lock retries."""

    def run_once(attempt: int) -> bool:
        run_id = start_collection_run(name, attempt=attempt)
        try:
            logger.info(f"[스케줄러] {name} 시작 (시도 {attempt})")
            fn()
            outputs = evaluate_job_outputs(name)
            refresh_job_health_snapshot(name, outputs)
            warning = any(item["status"] != "healthy" for item in outputs)
            status = "success_with_warning" if warning else "success"
            finish_collection_run(run_id, status, details={"datasets": outputs})
            if warning:
                logger.warning(f"[스케줄러] {name} 완료 후 데이터 계약 경고: {outputs}")
                return False
            logger.info(f"[스케줄러] {name} 완료")
            return True
        except Exception as exc:
            finish_collection_run(run_id, "failed", error=str(exc))
            logger.error(f"[스케줄러] {name} 오류: {exc}", exc_info=True)
            return False

    if name not in _DB_WRITE_JOBS:
        return run_once(1)

    delays = (
        _DB_LOCK_LONG_RETRY_DELAYS
        if name in _DB_LOCK_LONG_WAIT_JOBS
        else _DB_LOCK_RETRY_DELAYS
    )
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            logger.warning(f"[스케줄러] {name} DB 잠금 재시도 대기: {delay}초")
            time.sleep(delay)
        run_id = start_collection_run(name, attempt=attempt)
        try:
            with stock_db_write_lock(name, timeout=_DB_LOCK_TIMEOUT_SECONDS) as acquired:
                if not acquired:
                    finish_collection_run(
                        run_id,
                        "lock_timeout",
                        error=f"stock.db writer lock timeout ({_DB_LOCK_TIMEOUT_SECONDS:g}s)",
                        details={
                            "retry_scheduled": attempt < len(delays),
                            "lock_holder": _current_lock_holder(),
                        },
                    )
                    logger.warning(
                        f"[스케줄러] {name} DB writer 잠금 실패 "
                        f"({attempt}/{len(delays)}) — 보유자: {_current_lock_holder()}"
                    )
                    continue
                logger.info(f"[스케줄러] {name} 시작 (시도 {attempt})")
                fn()
                outputs = evaluate_job_outputs(name)
                refresh_job_health_snapshot(name, outputs)
                warning = any(item["status"] != "healthy" for item in outputs)
                status = "success_with_warning" if warning else "success"
                finish_collection_run(run_id, status, details={"datasets": outputs})
                if warning:
                    logger.warning(f"[스케줄러] {name} 완료 후 데이터 계약 경고: {outputs}")
                    return False
                logger.info(f"[스케줄러] {name} 완료")
                return True
        except Exception as exc:
            finish_collection_run(run_id, "failed", error=str(exc))
            logger.error(f"[스케줄러] {name} 오류: {exc}", exc_info=True)
            return False
    logger.error(f"[스케줄러] {name} DB 잠금 재시도 소진")
    return False


class CollectionScheduler:
    """
    전체 수집 주기를 관리하는 단일 스케줄러.
    각 잡은 독립 daemon thread에서 실행 (I/O 블로킹 허용).
    """

    def __init__(self, db_factory: Callable = None):
        self._db_factory = db_factory
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        # FastAPI startup hooks can be invoked more than once during reloads or
        # integration setup. Starting the same loop twice doubles API traffic.
        self._start_lock = threading.Lock()
        self._started = False
        self._kiwoom_rt_cursor = 0

    # ══════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════

    def start(self) -> None:
        """서버 startup 시 호출 — 모든 스케줄 스레드 시작."""
        with self._start_lock:
            # Do not clear stop_event while an earlier generation is winding
            # down: that would revive its loops and duplicate every collector.
            if self._started or any(thread.is_alive() for thread in self._threads):
                logger.warning("[스케줄러] 중복 start 요청 무시")
                return
            self._started = True
        self._stop_event.clear()
        interrupted = interrupt_running_collection_runs()
        if interrupted:
            logger.warning(f"[스케줄러] 이전 프로세스 미종료 실행 {interrupted}건 정리")
        jobs = [
            ("야간배치",        self._loop_nightly),
            ("월간업데이트",    self._loop_monthly),
            ("공시확인",        self._loop_disclosure),
            ("공시최근증분",    self._loop_disclosure_recent_refresh),
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
            ("NPS고용업데이트", self._loop_nps_daily),            # ★ 매일 06:00 공개 최신월 감지 + 최근 3개월 보완
            ("미국지수수집",    self._loop_us_indices),           # ★ 매일 06:30 나스닥/S&P500 수집
            ("글로벌매크로수집", self._loop_global_macro_daily),    # ★ 매일 06:45 글로벌 거시/원자재/이벤트 지표 수집
            ("거시가격품질감사", self._loop_macro_price_quality),   # ★ 매일 07:05 심볼 혼입·범위 이탈 탐지
            ("미국일별시세팩터수집", self._loop_us_daily_quotes_and_factors), # ★ 매일 06:30 미국 전종목 OHLCV+팩터 stale-only
            ("미국바이오파이프라인", self._loop_us_biotech_pipeline), # ★ 매일 06:50 SEC 10-K/10-Q 기반 바이오 후보물질 보강
            ("소스인텔리전스주간", self._loop_source_intelligence_weekly), # ★ 매주 일요일 10:00 트릴리온 등 등록 소스 티커 의견 재검토
            ("미국13F거물공시", self._loop_us_13f_refresh), # ★ 매일 07:12 SEC 13F + House PTR 갱신
            ("시장시그널브리핑", self._loop_market_signal_briefing), # ★ 매일 07:00 시장 5단계 국면 + AI 브리핑
            ("HOT섹터블로그",   self._loop_sector_blog),          # ★ 매일 07:00 블로그 신규 포스트 파싱
            ("섹터지수보완",    self._loop_sector_index_rebuild),  # ★ 매일 18:40 가격히스토리 기반 섹터지수 보완
            ("섹터로테이션캐시", self._loop_sector_rotation_cache), # ★ 장중 1시간 + 장마감 기준 주도섹터 캐시
            ("AI주도섹터",      self._loop_ai_leading_sector),    # ★ 매일 07:20 미국 증시 기반 주도 섹터 판독
            ("섹터오전텔레그램", self._loop_sector_morning_tg),    # ★ 매일 08:30 섹터 AI 리포트 텔레그램
            ("섹터점심텔레그램", self._loop_sector_lunch_tg),      # ★ 매일 12:30 오전장 섹터 분석 텔레그램
            ("스탁이지분석",    self._loop_stockeasy_analysis),   # ★ 매일 16:30 스탁이지 전략 분석
            ("스탁이지주간",    self._loop_stockeasy_weekly),     # ★ 매주 일요일 09:00 주간 요약
            ("스탁이지30분동기화", self._loop_stockeasy_30m_sync),  # ★ 매 30분 모멘텀 동기화 + 실주문(옵션)
            ("보유종목매도알림", self._loop_portfolio_sell_alerts), # ★ 매일 15:00 보유종목 매도검토 텔레그램 요약
            # ("V14장중10분", self._loop_v14_10m),                  # ⛔ 2026-07-23 비활성: GPT V18 가상매매 승률27%(-8.6M 누적손실)로 저효율 확인되어 삭제, 조합 가상매매로 대체
            ("V12골든크로스",  self._loop_gc_20m),                   # ★ 장중 20분 V12 골든크로스 가상매매
            ("V-RECOVERY",    self._loop_rec_20m),                  # ★ 장중 20분 V-RECOVERY 낙폭반등 가상매매
            ("V-CONTRACT",    self._loop_cm_20m),                   # ★ 장중 20분 V-CONTRACT-MOMENTUM 해외수주 모멘텀 가상매매(2026-08-09)
            ("전방검증체크",   self._loop_forward_validation_check),  # ★ 매일 06:10 라이브 가상매매 실측으로 forward_validation 아티팩트 재평가(2026-08-13)
            ("전략센터상위5가상매매", self._loop_combo_daily),          # ★ 매일 18:35 전략센터 현재 상위 5개를 재선정해 가상매매
            ("키움연결체크",   self._loop_kiwoom_health),            # ★ 키움 REST 연결 상태 점검(장중 10분)
            ("키움실시간스냅샷", self._loop_kiwoom_realtime),         # ★ 장중 1분 키움 실시간 스냅샷 수집
            ("키움조건검색",   self._loop_kiwoom_condition_snapshot), # ★ 장중 조건식 현재 편입 + 편입/편출 이력
            # ("고용보험배치",  self._loop_insurance_monthly),    # ⛔ 비활성: 연간 총인원 수집 — 월별차이 없어 의미 없음 (사용자 요청)
            ("DART수주공시",   self._loop_dart_contracts),        # ★ 매일 08:00/13:00/17:00 DART 수주공시
            ("DART수주계약",   self._loop_order_contracts),       # ★ 매일 19:00 수주잔고 급증 proxy 테이블 적재
            ("DART희석공시",   self._loop_dart_dilution),         # ★ 매일 07:10 CB/BW/EB 희석 공시 수집
            ("DART희석공시마감", self._loop_dart_dilution_close),  # ★ 평일 17:20 당일 CB/BW/EB·증자 공시 반영
            ("키움신용잔고",   self._loop_kiwoom_margin),         # ★ 매일 18:45 종목별 신용/대주 잔고 (코스피+코스닥 80%)
            ("키움외국인지분율", self._loop_kiwoom_foreign_hold),  # ★ 매일 19:15 외국인 지분율 수집 (코스피+코스닥 80%)
            ("DART임원매매",   self._loop_dart_insider),          # ★ 매주 일요일 02:30 임원매매 전종목 + 매일 공시 incremental
            ("DART수주잔고",   self._loop_dart_backlog),          # ★ 매주 일요일 01:20 수주잔고 분기 수집(5년)
            ("DART원가재고",   self._loop_dart_cost),             # ★ 매주 일요일 01:50 매입재료비/재고/감가상각 수집(5년)
            ("DART매입재료비", self._loop_dart_material_purchase), # ★ 매주 일요일 02:20 원재료 매입액 전용 수집
            ("DART직원수",     self._loop_dart_employee_count),    # ★ 매주 일요일 02:55 dart_employee_count 전용 수집
            ("DART임직원CH",   self._loop_dart_ch_extra),          # ★ 매주 일요일 03:10 직원현황/판관비/매출채권 보강
            ("DART세그먼트",   self._loop_dart_segment),          # ★ 매주 일요일 03:30 사업부문별 매출 수집(시총상위500)
            ("근로복지공단",   self._loop_wlb_monthly),           # ★ 매일 20:30 변화감지 → 수집
            ("BigQuery동기화", self._loop_bigquery_sync),         # ★ 매일 23:30 BigQuery 전체 운영테이블 동기화 → 텐버거 BQ 분석
            ("BQ아침알림", self._loop_bq_morning_alert),          # ★ 매일 07:30 3배 패턴 아침 알림
            ("컨센서스수집",   self._loop_consensus),             # ★ 매일 04:00 한경 컨센서스 증분 수집
            ("재무무결성일일", self._loop_financial_integrity_daily), # ★ 매일 06:20 재무 이상값 수리 + 무결성 리포트
            ("재무무결점월간", self._loop_financial_integrity_monthly),  # ★ 매월 1일 05:00 재무 무결점 검사
            ("재무무결점분기", self._loop_financial_integrity_quarterly), # ★ 분기 공시마감 1주 후 자동 보완
            ("KRX종목기본정보", self._loop_krx_base_info),               # ★ 매일 18:35 KRX 종목기본정보 + 변동 감지
            ("FnGuide재무월간", self._loop_fnguide_financial_monthly),  # ★ 매월 3일 05:00 연결/별도 재무제표 전종목
            ("수출입가집계",   self._loop_trade_provisional),          # ★ 매주 월요일 06:00 수출입 10일 가집계 수집
            ("공시DB배치",     self._loop_disclosure_db_batch),        # ★ 매주 일요일 02:00 DART 전종목 공시 DB 저장
            ("KRX투자자수급",  self._loop_krx_investor_playwright),    # ★ 매일 18:10 KRX 전종목 기관/외국인 순매수(Playwright)
            ("KRX프로그램매매", self._loop_krx_program_trading),         # ★ 매일 18:20 KRX 프로그램매매(차익/비차익) Playwright
            ("종목프로그램매매", self._loop_broker_program_stock_trading), # ★ 매일 18:50 Kiwoom 종목별 프로그램 매수/매도
            ("RS사전계산",    self._loop_rs_precompute),               # ★ 매일 18:30 RS/52주 캐시 사전계산
            ("CF3중검증",     self._loop_cf_triple_validate),          # ★ 매일 05:30 신규 CF 3중 검증 (DART·FnGuide·Seibro)
            ("주간4중검증",   self._loop_weekly_revalidation),         # ★ 매주 일요일 03:00 전종목 4중 검증 Phase A+B+C+E+F
            ("DB유지보수",    self._loop_db_maintenance),              # ★ 매주 일요일 04:00 VACUUM/ANALYZE/WAL checkpoint
            ("PostgreSQL주간백업", self._loop_postgres_weekly_backup), # ★ 매주 일요일 05:10 전체 스냅샷 백업+검증
            ("PostgreSQL백업상태", self._loop_postgres_backup_health), # ★ 매일 05:50 해시·카탈로그·신선도 검증
            ("PostgreSQL커트오버검증", self._loop_postgres_cutover_verify), # ★ 매일 06:10 테이블별 드리프트/매크로오염 감시+자동복구
            ("WAL일별체크",   self._loop_wal_daily_check),            # ★ 매일 04:30 WAL 크기 감시 + 100MB 초과 시 checkpoint
            ("실적신호스캔",  self._loop_earnings_signal_scan),      # ★ 매일 06:00 + 분기실적 시즌 추가 스캔
            ("키움투자자수급", self._loop_kiwoom_investor_daily),       # ★ 매일 19:00 키움 ka10059 종목별 투자자 순매수 수집
            ("키움종목기본정보", self._loop_kiwoom_stock_universe),     # ★ 매주 월요일 06:30 키움 ka10001 PER/PBR/ROE/유동주식수 갱신
            ("키움대량체결", self._loop_kiwoom_large_trade_rank),      # ★ 장중 10분 키움 ka00190 대량체결 원본 순위
            ("카페시그널주간",  self._loop_cafe_signal_weekly),         # ★ 매주 월요일 07:10 지표상회 카페 종목/섹터 시그널 구조화
            ("카페시그널월간",  self._loop_cafe_signal_monthly),        # ★ 매월 1일 07:15 지표상회 카페 월간 시그널 구조화
            ("체리형부최신채널", self._loop_cherry_latest_channel),      # ★ 매일 08:45 @Brianlee4 최신 체리형부 문서 증분 수집
            ("체리형부패밀리학습", self._loop_cherry_family_learning),   # ★ 매일 09:05 체리형부 family 등록상태 확인 + 재학습 로그 저장
            ("퀀트지표트리거",  self._loop_quant_indicator_signal),       # ★ 매일 07:40 지표 이상치 → 관련 종목 매수 후보 텔레그램
            ("거시지표백테스트", self._loop_macro_indicator_backtest),     # ★ 매주 월요일 07:50 거시지표×섹터 후보 검증
            ("데이터무결성후속검증", self._loop_data_integrity_followup), # ★ 매일 00:05 2026-08-22 세션 발견 잔여 이상치(revenue_extreme_yoy/dilution) DART 원문대조 재검증
            ("기업행위조정계수후속확정", self._loop_corporate_action_confirmation_followup), # ★ 매일 00:10 유상증자 TERP 조정계수 매칭 재시도 (turnaround/regime_adaptive 등 백테스트 검증 병목 해소용, DART 미사용)
            ("가격점프감사재빌드", self._loop_price_jump_audit_rebuild), # ★ 매일 00:15 price_jump_audit 재빌드(2026-08-24 세션: 2주 이상 스테일 방치로 허위오탐 발생 확인, 재발방지)
            ("가격외부소스재대조", self._loop_naver_price_verify), # ★ 매일 00:20 신규 가격점프 이벤트만 Naver와 교차대조(--only-new, DART 미사용)
            ("다중소스재무교차검증", self._loop_multi_source_financial_crosscheck), # ★ 매일 00:25 손익/현금흐름/매입재료비를 DART(anchor) vs FnGuide/Naver/Yahoo 다중소스로 교차검증(2026-08-26 세션, DART API 미사용 — FnGuide스크레이핑+naver_financial테이블+yfinance)
            ("전략센터주간재검증", self._loop_weekly_strategy_reverify), # ★ 매주 일요일 01:30 등록전략 전량 최신데이터로 재실행(2026-08-24: V8 승격/V10·V12 정직한 하향 확인된 바로 그 배치를 정기화)
            ("DART재무재수집",  self._loop_dart_financial_recollect),  # ★ 매일 00:30 DART 재무제표 재수집 (ETF/ETN/상폐 제외)
            ("DART_CF위험군재수집", self._loop_dart_cf_risk_recollect), # ★ 매일 01:00 CF MISSING_Q123/NULL_Q123_DEPR/MIXED_SOURCE 재수집
            ("텐버거위클리",      self._loop_tenbagger_weekly),          # ★ 매주 월요일 07:30 위클리 리포트 + 텔레그램
            ("텐버거역사검증",    self._loop_tenbagger_historical_validation), # ★ 매주 월요일 08:10 지속형 텐버거 로직 재검증
            ("텐버거트리거",      self._loop_tenbagger_trigger),         # ★ 평일 18:00 복합 트리거 알림
            ("페이지데이터감사",   self._loop_page_data_audit),           # ★ 매일 06:40 전체 페이지 데이터 신선도 감사
            ("턴어라운드워치사전계산", self._loop_turnaround_watch_precompute), # ★ 매일 04:40 무거운 턴어라운드 발굴 스캔 사전계산(CPU 유휴시간대)
            ("체리형부스크리너사전계산", self._loop_cherry_screener_precompute), # ★ 매일 04:45 체리형부식 3대 스크리닝 전종목 스캔 사전계산
            ("투자의사결정RAG", self._loop_investment_decision_rag), # 평일 저가 구간에만 대기 중인 문서 RAG 처리
            ("FnGuideDART전종목검증", self._loop_fnguide_dart_verify_sweep),   # ★ 매일 03:15 FNGUIDE 일일한도 내에서 전종목 순차 교차검증
            ("미검증스냅샷백필", self._loop_unverified_snapshot_backfill),   # ★ 매일 03:45 financial_source_snapshot unverified 백로그 정리(2026-08-28 신설, DART만 소비, FnGuide 재수집 불필요)
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
        with self._start_lock:
            if not self._started:
                return
            self._started = False
        self._stop_event.set()
        logger.info("[스케줄러] 중지 신호 전송")

    def _loop_postgres_sync(self) -> None:
        """Keep PostgreSQL current while legacy collectors are being retired."""
        if not config.IS_POSTGRES:
            return
        self._wait_secs(90)
        while not self._stop_event.is_set():
            try:
                subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "scripts" / "sync_tenbagger_postgres.py")],
                    cwd=str(Path(__file__).parent),
                    check=True,
                    timeout=25 * 60,
                )
            except Exception as exc:
                logger.error("[PostgreSQL증분동기화] 실패: %s", exc)
            self._wait_secs(30 * 60)

    def _loop_tenbagger_historical_validation(self) -> None:
        """Rebuild the leakage-controlled historical scoreboard every Monday."""
        while not self._stop_event.is_set():
            wait = _seconds_until(8, 10, skip_weekend=False)
            if self._stop_event.wait(wait):
                break
            if datetime.now().weekday() != 0:
                self._wait_secs(23 * 3600)
                continue
            try:
                outputs = []
                for script_name in (
                    "research_historical_tenbagger_scoreboard_v2.py",
                    "research_historical_tenbagger_causes.py",
                    "discover_historical_tenbagger_signals.py",
                ):
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).parent / "scripts" / script_name)],
                        cwd=str(Path(__file__).parent),
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=20 * 60,
                    )
                    outputs.append(f"{script_name}: {result.stdout[-500:]}")
                logger.info("[텐버거역사검증] 완료: %s", " | ".join(outputs))
            except Exception as exc:
                logger.error("[텐버거역사검증] 실패: %s", exc)

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

    def _loop_disclosure_recent_refresh(self) -> None:
        """DART 최근 공시 목록 증분 갱신 — 하루 4회 dart_disclosures upsert."""
        self._wait_secs(25)
        while not self._stop_event.is_set():
            now = datetime.now()
            targets = [(8, 5), (13, 5), (17, 5), (22, 5)]
            next_times = []
            for hour, minute in targets:
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate > now:
                    next_times.append(candidate)
            if next_times:
                next_run = min(next_times)
            else:
                next_run = (now + timedelta(days=1)).replace(
                    hour=8, minute=5, second=0, microsecond=0
                )
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("공시최근증분", self._job_disclosure_recent_refresh)

    def _job_disclosure_recent_refresh(self) -> None:
        """OpenDART 날짜범위 API로 최근 공시 목록을 빠르게 보강."""
        script = str(Path(__file__).resolve().parent / "scripts" / "refresh_dart_disclosures_recent.py")
        try:
            r = subprocess.run(
                [sys.executable, script, "--days", "5"],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(Path(__file__).resolve().parent),
            )
            if r.returncode == 0:
                logger.info(f"[공시최근증분] 완료: {r.stdout.strip()[-300:]}")
            else:
                logger.error(f"[공시최근증분] 오류: {r.stderr.strip()[-500:]}")
        except Exception as e:
            logger.error(f"[공시최근증분] 예외: {e}")

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
        """나스닥·S&P500·VIX + 미 국채 2Y/10Y/30Y 최신 데이터 수집.
        Yahoo Finance 우선, 실패 시 네이버 증권 fallback.
        """
        from data_collector import DataCollector
        today_iso = date.today().isoformat()
        collector = DataCollector(dart_api_key=config.DART_API_KEY)
        us_symbols = {
            "^IXIC": "NASDAQ",
            "^GSPC": "S&P500",
            "^VIX":  "VIX",
            "2YY=F": "US2Y",
            "^TNX": "US10Y",
            "10Y=F": "US10Y_ALT",
            "^TYX": "US30Y",
            "DX-Y.NYB": "DXY",
        }
        for symbol, name in us_symbols.items():
            try:
                collector._collect_macro_yahoo(symbol, name, today_iso)
            except Exception as e:
                logger.warning(f"[미국지수수집] {name}: {e}")
        logger.info("[미국지수수집] 완료")

    def _loop_global_macro_daily(self) -> None:
        """매일 06:45 — 글로벌 거시/원자재/이벤트 fast-moving 지표 갱신."""
        logger.info("[글로벌매크로수집] 루프 시작")
        self._wait_secs(10)
        while not self._stop_event.is_set():
            self._wait_until(6, 45, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("글로벌매크로수집", self._job_global_macro_daily)
            self._wait_secs(23 * 3600)
        logger.info("[글로벌매크로수집] 루프 종료")

    def _job_global_macro_daily(self) -> None:
        """global_macro_data 최신화. 퀀트 메뉴 반영은 19:35 브릿지에서 수행."""
        result = subprocess.run(
            [
                sys.executable,
                "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/collect_global_macro_daily.py",
            ],
            capture_output=True,
            text=True,
            timeout=420,
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
        )
        logger.info(f"[글로벌매크로수집] 완료: {result.stdout[-800:] if result.stdout else ''}")
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "global macro collection failed")[-800:])

    def _loop_macro_price_quality(self) -> None:
        """매일 07:05 거시 심볼 혼입과 비정상 범위 감사."""
        logger.info("[거시가격품질감사] 루프 시작")
        self._wait_secs(90)
        while not self._stop_event.is_set():
            self._wait_until(7, 5, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("거시가격품질감사", self._job_macro_price_quality)
            self._wait_secs(23 * 3600)
        logger.info("[거시가격품질감사] 루프 종료")

    def _job_macro_price_quality(self) -> None:
        script = str(Path(__file__).resolve().parent / "scripts" / "repair_macro_price_contamination.py")
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            try:
                from notifier import send

                send(
                    "<b>거시 가격 품질 감사 실패</b>\n"
                    + (result.stderr or result.stdout)[-1200:],
                    key=f"macro_price_quality_failure_{date.today().isoformat()}",
                )
            except Exception:
                pass
            raise RuntimeError(f"macro price quality audit failed: {(result.stderr or result.stdout)[-1200:]}")
        logger.info("[거시가격품질감사] 정상: 오염 0건")

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
        # ⚠️ 2026-08-15 발견: 이 잡은 매일 03:30(자정 이후)에 실행되는데
        # date.today()로 "오늘"만 조회하면, 실제 공시는 대부분 전날 영업시간 중
        # 이뤄지므로(예: 8/14 오후 공시 → 8/15 03:30 실행 시점엔 이미 "어제"가 됨)
        # 구조적으로 항상 놓치고 있었음(에이엘티 172670 반기보고서 등 다수 종목
        # 영향 — dart_disclosures엔 잡히지만 financial_data 자동갱신은 트리거 안 됨).
        # 전일~당일 범위로 조회해 자정 전후 어느 쪽에 걸리든 놓치지 않도록 수정.
        today_codes: set[str] = set()
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        today_str = date.today().strftime("%Y%m%d")

        # 1차: OpenDart API
        _opendart_ok = False
        try:
            dart = collector.dart
            if dart:
                for kind in ["A", "B"]:
                    df = dart.list(start=yesterday_str, end=today_str, kind=kind)
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
                _resp = _req.post(
                    "https://dart.fss.or.kr/dsab007/detailSearch.ax",
                    data={"startDate": yesterday_str, "endDate": today_str,
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

        # kiwoom_tick_history/kiwoom_minute_snapshot 보존정책 (2026-07-22 신규): 두 테이블 모두
        # 모든 소비처가 "오늘" 데이터만 조회하는데 삭제로직이 없어 5/26 이후 각각 1210만행/183만행까지
        # 무제한 누적되던 문제 발견 → 매일 장마감 후 7일 초과분 배치삭제(200건씩 나눠 긴 락 방지).
        # 2026-08-24: rowid는 SQLite 전용(PostgreSQL엔 없음) — 이 배치삭제가 PostgreSQL
        # 라우팅 하에서 매일 조용히 실패해 7/31부터 24일치(tick 373만/minute 68만행)가
        # 다시 무제한 누적됨. id 컬럼이 있는 테이블은 id 기반 배치삭제, 없는 테이블
        # (kiwoom_minute_snapshot)은 날짜 단위로 나눠 삭제(포터블, 큰 락 회피).
        for _table, _col in (("kiwoom_tick_history", "event_ts"), ("kiwoom_minute_snapshot", "minute_ts")):
            try:
                import sqlite3 as _sl3
                _c = _sl3.connect("stock.db", timeout=60)
                _c.execute("PRAGMA busy_timeout=60000")
                _cutoff = _c.execute("SELECT date('now','-7 day')").fetchone()[0]
                _deleted = 0
                if _table == "kiwoom_tick_history":
                    while True:
                        _cur = _c.execute(
                            f"DELETE FROM {_table} WHERE id IN "
                            f"(SELECT id FROM {_table} WHERE substr({_col},1,10) < ? LIMIT 200000)",
                            (_cutoff,),
                        )
                        _c.commit()
                        _deleted += _cur.rowcount
                        if _cur.rowcount < 200000:
                            break
                else:
                    stale_dates = [
                        r[0] for r in _c.execute(
                            f"SELECT DISTINCT substr({_col},1,10) FROM {_table} WHERE substr({_col},1,10) < ?",
                            (_cutoff,),
                        ).fetchall()
                    ]
                    for _d in stale_dates:
                        _cur = _c.execute(f"DELETE FROM {_table} WHERE substr({_col},1,10) = ?", (_d,))
                        _c.commit()
                        _deleted += _cur.rowcount
                _c.close()
                if _deleted:
                    logger.info(f"[장마감] {_table} 보존정책: {_deleted}건 삭제(cutoff={_cutoff})")
            except Exception as e:
                logger.warning(f"[장마감] {_table} 보존정책: {e}")

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

            py = "/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python"
            if not os.path.exists(py):
                py = sys.executable
            cmd = [py, "collect_kis_ohlcv.py", "--days", "1"]
            logger.info(f"[KIS일별] {' '.join(cmd)} 시작")
            proc = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
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
                    cursor = conn.execute("""
                        UPDATE price_history
                        SET open=?, high=?, low=?, close=?, volume=?, trade_amount=?
                        WHERE stock_code=? AND date=? AND (volume IS NULL OR volume=0)
                    """, (open_, high, low, close, volume, trade_amount, code, today_str))
                    upd += max(cursor.rowcount, 0)
                    # 2026-08-09 버그수정: 위 UPDATE는 volume이 비어있을 때만 실행되는데,
                    # KIS 등 다른 경로가 그날 행을 volume까지 이미 채워둔 상태(정상)이면
                    # trade_amount만 KRX전용 필드라 영원히 못 채워짐(2026-07~08 전종목
                    # trade_amount=0 회귀의 원인, V-CONTRACT-MOMENTUM 실전 신호가 avg20_amt
                    # 필터에 전부 걸려 buy_candidates=0으로 나오던 문제로 발견). trade_amount만
                    # 비어있으면 그 필드만 별도로 채운다(다른 필드는 건드리지 않아 안전).
                    if trade_amount and trade_amount > 0:
                        conn.execute("""
                            UPDATE price_history SET trade_amount=?
                            WHERE stock_code=? AND date=? AND (trade_amount IS NULL OR trade_amount=0)
                        """, (trade_amount, code, today_str))
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
                    cursor = conn.execute("""
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
                    total_saved += max(cursor.rowcount, 0)

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

    @staticmethod
    def _get_all_market_codes(conn) -> list[str]:
        """코스피/코스닥 전종목 코드 반환 (상장폐지/특수코드 제외)."""
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT stock_code
                FROM stock_universe
                WHERE LENGTH(stock_code)=6
                  AND stock_code GLOB '[0-9]*'
                  AND UPPER(COALESCE(market,'')) IN ('KOSPI','KOSDAQ')
                ORDER BY stock_code
                """
            ).fetchall()
            codes = [r[0] for r in rows if r and r[0]]
            if codes:
                return codes
        except Exception:
            pass
        return CollectionScheduler._get_active_codes(conn)

    def _job_cashflow_batch(self) -> None:
        """DART 전종목 현금흐름표 배치 수집 (missing_only=True).

        2026-08-23: 서브프로세스 non-zero returncode를 raise하지 않아 _run_job_safe가
        실패를 "성공"으로 오기록하던 버그 수정(네이버밸류에이션과 동일 클래스).
        """
        import subprocess
        result = subprocess.run(
            ["venv/bin/python3", "collect_dart_cashflow_batch.py", "--fill-missing", "--years", "5"],
            capture_output=True, text=True, timeout=14400,
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
        )
        if result.returncode != 0:
            raise RuntimeError(f"collect_dart_cashflow_batch.py failed: {result.stderr[-500:]}")
        logger.info(f"[현금흐름배치] 완료: {result.stdout[-300:] if result.stdout else ''}")

        derived = subprocess.run(
            [sys.executable, "scripts/build_cash_conversion_signals.py", "--since-year", "2020"],
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            capture_output=True,
            text=True,
            timeout=900,
        )
        logger.info(
            "[현금전환품질] 파생 신호 재구축 완료: returncode=%s stdout=%s",
            derived.returncode,
            derived.stdout[-300:] if derived.stdout else "",
        )
        if derived.returncode != 0:
            logger.warning(f"[현금전환품질] stderr: {derived.stderr[-500:]}")

    def _job_naver_fundamentals(self) -> None:
        """네이버금융 전종목 PBR/PER/EPS 배치 수집 → financial_data + stock_universe 동시 갱신.

        2026-08-23: 서브프로세스가 non-zero로 종료해도 이 메서드 자체는 예외 없이
        정상 반환해 _run_job_safe가 "성공"으로 기록하던 버그(키움투자자수급에서
        2026-08-14에 이미 한 번 발견된 것과 동일 클래스) — 실측 결과 PRAGMA
        synchronous=NORMAL이 PostgreSQL 라우팅 하에서 매번 즉시 크래시(db_compat.py에서
        같은 날 수정)해 8/17 이후 completion_health.db엔 매일 "success"로 찍히면서도
        실제로는 stock_universe per/pbr이 전혀 갱신되지 않고 있었음. non-zero
        returncode를 raise해 _run_job_safe가 정확히 실패로 기록하고 재시도하도록 수정.
        """
        import subprocess
        result = subprocess.run(
            ["venv/bin/python3", "collect_naver_fundamentals.py"],  # 전종목 매일 갱신
            capture_output=True, text=True, timeout=7200,
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
        )
        if result.returncode != 0:
            raise RuntimeError(f"collect_naver_fundamentals.py failed: {result.stderr[-500:]}")
        logger.info(f"[네이버밸류에이션] 완료: {result.stdout[-200:] if result.stdout else ''}")

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
        """NPS 최근월 데이터 보완 — 월별 seq 차이를 고려해 최근 3개월 스냅샷을 수집."""
        try:
            import subprocess
            result = subprocess.run(
                [
                    "venv/bin/python3",
                    "employment_monitor/collect_nps_monthly.py",
                    "--historical",
                    "--months-back",
                    "3",
                ],
                capture_output=True, text=True, timeout=7200,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            out = (result.stdout or "")[-300:]
            logger.info(f"[NPS고용] 완료: {out}")
            if result.returncode != 0:
                raise RuntimeError(
                    f"NPS 수집 실패(returncode={result.returncode}): {(result.stderr or '')[-500:]}"
                )
            from employment_monitor.data_quality import audit_employment_data
            quality = audit_employment_data()
            if quality["status"] == "error":
                raise RuntimeError(f"NPS 수집 후 무결성 오류: {quality['errors']}")
            if quality["warnings"]:
                logger.warning(f"[NPS고용] 품질 경고: {quality['warnings']}")
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

    def _loop_sector_index_rebuild(self) -> None:
        """매일 18:40 — price_history 기반 파생 섹터지수 보완."""
        self._wait_secs(72)
        while not self._stop_event.is_set():
            self._wait_until(18, 40, skip_weekend=True)
            _run_job_safe("섹터지수보완", self._job_sector_index_rebuild)

    def _job_sector_index_rebuild(self) -> None:
        """Rebuild derived sector_index_daily rows from local OHLCV."""
        try:
            import subprocess, sys

            script = str(Path(__file__).resolve().parent / "scripts" / "rebuild_sector_index_from_price_history.py")
            r = subprocess.run(
                [sys.executable, script],
                capture_output=True,
                text=True,
                timeout=1800,
                cwd=str(Path(__file__).resolve().parent),
            )
            logger.info(f"[섹터지수보완] 완료: {(r.stdout or '')[-500:]}")
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "")[-1000:])
        except Exception as e:
            logger.error(f"[섹터지수보완] 오류: {e}", exc_info=True)

    def _loop_market_signal_briefing(self) -> None:
        """매일 07:00 — 5단계 시장 국면 점수 계산 + OpenAI 아침 브리핑 저장."""
        self._wait_secs(68)
        # 캐치업: 서버가 07:00 이후 재시작된 경우 오늘 브리핑 누락 방지
        try:
            self._maybe_catchup_market_signal_briefing()
        except Exception as e:
            logger.warning(f"[시장시그널브리핑] 캐치업 점검 실패: {e}")
        while not self._stop_event.is_set():
            self._wait_until(7, 0)
            _run_job_safe("시장시그널브리핑", self._job_market_signal_briefing)

    def _maybe_catchup_market_signal_briefing(self) -> None:
        """당일(영업일) 07:00 경과 + 브리핑 미생성이면 즉시 1회 생성."""
        now = datetime.now()
        if now.hour < 7:
            return
        if not is_kr_trading_day(now.date()):
            return
        try:
            conn = connect_stock_db(timeout=10)
            today = now.date().isoformat()
            row = conn.execute(
                "SELECT COUNT(*) FROM market_signal_briefing WHERE briefing_date=?",
                (today,),
            ).fetchone()
            conn.close()
            cnt = int(row[0] or 0) if row else 0
            if cnt >= 2:  # KOSPI, KOSDAQ 2건
                return
            logger.info(f"[시장시그널브리핑] 캐치업 실행 (today={today}, rows={cnt})")
            ok = _run_job_safe("시장시그널브리핑", self._job_market_signal_briefing)
            if not ok:
                # startup 시 DB writer 경합으로 스킵되면 2분 후 1회 재시도
                def _retry_once():
                    self._wait_secs(120)
                    if not self._stop_event.is_set():
                        _run_job_safe("시장시그널브리핑", self._job_market_signal_briefing)
                t = threading.Thread(target=_retry_once, name="시장시그널브리핑재시도", daemon=True)
                t.start()
                self._threads.append(t)
        except Exception as e:
            logger.warning(f"[시장시그널브리핑] 캐치업 조회 실패: {e}")

    def _job_market_signal_briefing(self) -> None:
        """시장 국면 브리핑 생성 — signal_engine.generate_market_ai_briefings 위임."""
        try:
            from signal_engine import generate_market_ai_briefings
            res = generate_market_ai_briefings()
            logger.info(f"[시장시그널브리핑] 완료: {res}")
        except Exception as e:
            logger.error(f"[시장시그널브리핑] 오류: {e}", exc_info=True)

    def _job_sector_blog(self) -> None:
        """블로그 자동파싱 — Sector_define/blog_parser.run_parser() 위임."""
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from Sector_define.blog_parser import run_parser
            run_parser(reprocess_empty=True)
            logger.info("[HOT섹터] 블로그 파싱 완료")
        except Exception as e:
            logger.error(f"[HOT섹터] 블로그 파싱 오류: {e}")

    def _loop_sector_rotation_cache(self) -> None:
        """장중 1시간마다, 장후 15:45에 주도섹터 로테이션 캐시 갱신."""
        self._wait_secs(72)
        try:
            now = datetime.now()
            hm = now.hour * 100 + now.minute
            if is_kr_trading_day(now.date()) and 900 <= hm < 1540:
                _run_job_safe("섹터로테이션캐시", self._job_sector_rotation_cache)
        except Exception as e:
            logger.warning(f"[섹터로테이션캐시] startup 보강 실패: {e}")

        while not self._stop_event.is_set():
            now = datetime.now()
            targets = []
            if is_kr_trading_day(now.date()):
                targets.extend([now.replace(hour=h, minute=5, second=0, microsecond=0) for h in range(9, 16)])
                targets.append(now.replace(hour=15, minute=45, second=0, microsecond=0))
            next_times = [t for t in targets if t > now]
            if next_times:
                next_run = min(next_times)
            else:
                next_day = now + timedelta(days=1)
                while not is_kr_trading_day(next_day.date()):
                    next_day += timedelta(days=1)
                next_run = next_day.replace(hour=9, minute=5, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("섹터로테이션캐시", self._job_sector_rotation_cache)

    def _job_sector_rotation_cache(self) -> None:
        """routes.sector_rotation 캐시 갱신."""
        try:
            from routes.sector_rotation import refresh_sector_rotation_cache
            res = refresh_sector_rotation_cache(force=True)
            logger.info(f"[섹터로테이션캐시] 갱신 완료: {res}")
        except Exception as e:
            logger.error(f"[섹터로테이션캐시] 갱신 오류: {e}", exc_info=True)

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
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
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
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
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
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
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
        """스탁이지 3전략 현황 분석 + 로직 적응 검증(조정 전/후 텔레그램).

        2026-08-22: run_daily_analysis() 자체가 내부에서 이미 run_validation()을
        호출하고 있는데(stockeasy_analyzer.py 948~954행), 여기서 또 명시적으로
        run_validation()을 한 번 더 호출해 매일 1회가 아니라 2회씩(각 회당 최대
        수십 분 소요되는 replay_entry_day_inclusion 전체이력 재현 포함) 중복
        실행되고 있었음 — stockeasy_logic_tracker.md에 매일 완전히 동일한 내용이
        2블록씩 쌓이던 원인. 중복 호출 제거.
        """
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from stockeasy_analyzer import run_daily_analysis
            run_daily_analysis()
            logger.info("[스탁이지] 일별 분석 완료")
        except Exception as e:
            logger.error(f"[스탁이지] 분석 오류: {e}")

    def _job_stockeasy_weekly(self) -> None:
        """주간 전략 패턴 요약 — DB 누적 분석 결과 기반 리포트."""
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from stockeasy_analyzer import run_weekly_summary
            run_weekly_summary()
            logger.info("[스탁이지] 주간 요약 완료")
        except Exception as e:
            logger.error(f"[스탁이지] 주간 요약 오류: {e}")

    def _loop_stockeasy_30m_sync(self) -> None:
        """장중 5분 / 장외 30분 — StockEasy 모멘텀 편입 즉시 실주문."""
        self._wait_secs(60)
        while not self._stop_event.is_set():
            _run_job_safe("스탁이지동기화", self._job_stockeasy_30m_sync)
            # 장중(09:00~15:30)은 5분, 그 외 30분 대기
            from datetime import datetime as _dt
            _now = _dt.now()
            _hm = _now.hour * 100 + _now.minute
            _market = _now.weekday() < 5 and 900 <= _hm < 1530
            self._wait_secs(300 if _market else 1800)

    def _job_stockeasy_30m_sync(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from stockeasy_autotrade import run_stockeasy_all_strategies_sync
            result = run_stockeasy_all_strategies_sync()
            logger.info(f"[스탁이지30분동기화] {result}")
        except Exception as e:
            logger.error(f"[스탁이지30분동기화] 오류: {e}", exc_info=True)

    def _loop_portfolio_sell_alerts(self) -> None:
        """매일 15:00 — 실제 보유종목 매도검토 텔레그램 요약 1회."""
        self._wait_secs(150)
        while not self._stop_event.is_set():
            wait = _seconds_until(15, 0, skip_weekend=True)
            if self._stop_event.wait(wait):
                break
            _run_job_safe("보유종목매도알림", self._job_portfolio_sell_alerts)

    def _job_portfolio_sell_alerts(self) -> None:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/portfolio_sell_signal_alert.py",
                    "--send-telegram",
                    "--min-score",
                    "6",
                    "--limit",
                    "10",
                    "--summary",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.info(f"[보유종목매도알림] returncode={result.returncode} {result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[보유종목매도알림] 오류: {result.stderr[-500:] if result.stderr else ''}")
        except Exception as e:
            logger.error(f"[보유종목매도알림] 오류: {e}", exc_info=True)

    def _loop_v14_10m(self) -> None:
        """평일 장중 10분마다 V18.1 가상매매 실행.

        실행 시점: 평일 09:00 ~ 15:30, 10분 간격
          - 매도: 10분마다 실시간 체크 → 장중 악재(공시, 급락) 즉시 대응
            * v_anchor: 하드스탑 -10% OR KOSPI < MA60 3일 연속 (개별 MA 조건 없음)
            * combo:    하드스탑 -10% OR 추세이탈(MA20/MA60)
          - 매수: 미보유 종목만, 쿨다운(v_anchor 5일 / combo 3일) 체크

        ※ 스캘핑 방지 원칙
           - 보유 중인 종목은 절대 추가 매수하지 않음
           - v_anchor 매도 조건에 개별 MA를 적용하지 않음 (매수 즉시 매도 방지)
           - 매도 후 쿨다운 기간 내 재매수 금지
        """
        self._wait_secs(110)
        while not self._stop_event.is_set():
            now = datetime.now()
            is_weekday = now.weekday() < 5
            hm = now.hour * 60 + now.minute
            in_session = (9 * 60) <= hm <= (15 * 60 + 30)

            if is_weekday and in_session:
                _run_job_safe("V18장중10분", self._job_v14_10m)
                self._wait_secs(600)   # 10분 대기
            else:
                self._wait_secs(60)    # 장외: 1분마다 시간 체크

    def _job_v14_10m(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.trend import execute_v18_now
            result = execute_v18_now()
            logger.info(f"[V18가상매매] {result}")
        except Exception as e:
            logger.error(f"[V18가상매매] 오류: {e}", exc_info=True)

    def _loop_gc_20m(self) -> None:
        """평일 장중 20분마다 V12 골든크로스 가상매매 실행."""
        self._wait_secs(130)  # 서버 시작 초기화 대기 (V18과 시차)
        while not self._stop_event.is_set():
            now = datetime.now()
            is_weekday = now.weekday() < 5
            hm = now.hour * 60 + now.minute
            in_session = (9 * 60) <= hm <= (15 * 60 + 30)
            if is_weekday and in_session:
                _run_job_safe("V12골든크로스매매", self._job_gc_20m)
                self._wait_secs(1200)   # 20분 대기
            else:
                self._wait_secs(60)

    def _job_gc_20m(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.trend import execute_gc_now
            result = execute_gc_now()
            logger.info(f"[V12골든크로스] sold={result.get('sold')} bought={result.get('bought')}")
        except Exception as e:
            logger.error(f"[V12골든크로스] 오류: {e}", exc_info=True)

    def _loop_rec_20m(self) -> None:
        """평일 장중 20분마다 V-RECOVERY 낙폭반등 가상매매 실행."""
        self._wait_secs(190)  # 서버 시작 초기화 대기 (V18/GC와 시차)
        while not self._stop_event.is_set():
            now = datetime.now()
            is_weekday = now.weekday() < 5
            hm = now.hour * 60 + now.minute
            in_session = (9 * 60) <= hm <= (15 * 60 + 30)
            if is_weekday and in_session:
                _run_job_safe("V-RECOVERY매매", self._job_rec_20m)
                self._wait_secs(1200)   # 20분 대기
            else:
                self._wait_secs(60)

    def _job_rec_20m(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.trend import execute_rec_now
            result = execute_rec_now()
            logger.info(f"[V-RECOVERY] sold={result.get('sold')} bought={result.get('bought')}")
        except Exception as e:
            logger.error(f"[V-RECOVERY] 오류: {e}", exc_info=True)

    def _loop_cm_20m(self) -> None:
        """평일 장중 20분마다 V-CONTRACT-MOMENTUM 해외수주 모멘텀 가상매매 실행.
        2026-08-09 신규: 대형수주 카테고리(251개 텐버거 중 14.3%) 실전 반영 —
        매트릭스 등록(execution_strict)만으로는 실제 수익률에 영향이 없다는 사용자
        지적에 따라 v_gc/v_recovery와 동일한 라이브 가상매매 라인으로 신규 추가."""
        self._wait_secs(220)  # V12/V-RECOVERY와 시차
        while not self._stop_event.is_set():
            now = datetime.now()
            is_weekday = now.weekday() < 5
            hm = now.hour * 60 + now.minute
            in_session = (9 * 60) <= hm <= (15 * 60 + 30)
            if is_weekday and in_session:
                _run_job_safe("V-CONTRACT매매", self._job_cm_20m)
                self._wait_secs(1200)   # 20분 대기
            else:
                self._wait_secs(60)

    def _job_cm_20m(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.trend import execute_cm_now
            result = execute_cm_now()
            logger.info(f"[V-CONTRACT] sold={result.get('sold')} bought={result.get('bought')}")
        except Exception as e:
            logger.error(f"[V-CONTRACT] 오류: {e}", exc_info=True)

    def _loop_forward_validation_check(self) -> None:
        """매일 06:15 — 라이브 가상매매(peak_holding/peak_trade) 실측 데이터로
        forward_validation 검증 아티팩트를 재평가(2026-08-13 신규, 2026-08-14
        06:10→06:15 조정: 기존 _loop_postgres_cutover_verify와 정확히 같은
        시각(06:10)에 등록했던 것을 발견 — 스케줄 전수감사로 재발방지).
        governance의 live_eligible 승격 조건이 rank>=forward_validated로
        강화됐으나, 이를 채울 파이프라인이 없어서 어떤 전략도 도달 불가능했음.
        운용기간/거래건수/계좌손실 최소조건(scripts/verify_forward_validation.py
        참조, 의도적으로 보수적)을 매일 재확인 — 시간이 지나 조건을 충족하면
        자동으로 반영된다."""
        self._wait_secs(90)
        while not self._stop_event.is_set():
            self._wait_until(6, 15)
            _run_job_safe("전방검증체크", self._job_forward_validation_check)

    def _job_forward_validation_check(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from scripts.verify_forward_validation import main as _fv_main
            _fv_main()
            logger.info("[전방검증체크] 완료")
        except Exception as e:
            logger.error(f"[전방검증체크] 오류: {e}", exc_info=True)

    def _loop_combo_daily(self) -> None:
        """평일 18:35에 전략센터 현재 상위 5개를 가상매매로 재선정·실행한다.

        메서드 이름은 기존 스레드 등록 호환을 위해 유지한다. 과거 고정 병합조합은
        더 이상 스케줄하지 않으며, 전략센터 매트릭스의 순위가 바뀌면 다음 실행부터
        가상매매 대상도 함께 바뀐다.
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_seconds_until(18, 35, skip_weekend=True))
            if self._stop_event.is_set():
                break
            _run_job_safe("병합조합가상매매", self._job_combo_daily)

    def _job_combo_daily(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.trend import execute_strategy_center_top_five_now
            payload = execute_strategy_center_top_five_now()
            for key, r in payload.get("results", {}).items():
                logger.info(f"[전략센터상위5가상매매] {key} sold={r.get('sold')} bought={r.get('bought')} ok={r.get('ok')}")
        except Exception as e:
            logger.error(f"[전략센터상위5가상매매] 오류: {e}", exc_info=True)

    def _loop_kiwoom_health(self) -> None:
        """평일 장중 10분마다 키움 REST 인증 상태 점검."""
        self._wait_secs(120)
        while not self._stop_event.is_set():
            now = datetime.now()
            is_weekday = now.weekday() < 5
            hm = now.hour * 60 + now.minute
            in_session = (9 * 60) <= hm <= (15 * 60 + 30)
            if is_weekday and in_session:
                _run_job_safe("키움연결체크", self._job_kiwoom_health)
                self._wait_secs(600)
            else:
                self._wait_secs(120)

    def _job_kiwoom_health(self) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from collectors.kiwoom_collector import KiwoomCollector
            kc = KiwoomCollector()
            st = kc.health_check()
            if st.get("ok"):
                logger.info("[키움연결체크] OK")
            else:
                logger.warning(f"[키움연결체크] 대기/오류: {st}")
        except Exception as e:
            logger.error(f"[키움연결체크] 오류: {e}", exc_info=True)

    def _loop_kiwoom_realtime(self) -> None:
        """평일 장중 1분마다 키움 실시간 스냅샷 수집 (옵션)."""
        self._wait_secs(90)
        while not self._stop_event.is_set():
            try:
                if not getattr(config, "KIWOOM_RT_ENABLED", False):
                    self._wait_secs(180)
                    continue
                now = datetime.now()
                is_weekday = now.weekday() < 5
                hm = now.hour * 60 + now.minute
                in_session = (9 * 60) <= hm <= (15 * 60 + 30)
                if is_weekday and in_session:
                    _run_job_safe("키움실시간스냅샷", self._job_kiwoom_realtime)
                    self._wait_secs(60)
                else:
                    self._wait_secs(120)
            except Exception as e:
                logger.warning(f"[키움실시간스냅샷] 루프 오류: {e}")
                self._wait_secs(120)

    def _job_kiwoom_realtime(self) -> None:
        """키움 실시간 스냅샷 수집.
        - ALL 모드: 코스피/코스닥 전종목을 배치 순환 수집
        - ACTIVE 모드: 관심/보유 종목 수집
        """
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")

            from collectors.kiwoom_collector import KiwoomCollector

            conn = connect_stock_db(timeout=5)
            try:
                mode = str(getattr(config, "KIWOOM_RT_UNIVERSE", "ALL")).upper()
                if mode == "ACTIVE":
                    universe = self._get_active_codes(conn)
                else:
                    universe = self._get_all_market_codes(conn)
            finally:
                conn.close()

            if not universe:
                logger.info("[키움실시간스냅샷] 대상 종목 없음")
                return

            batch_size = max(20, int(getattr(config, "KIWOOM_RT_BATCH_SIZE", 120)))
            batches_per_cycle = max(1, int(getattr(config, "KIWOOM_RT_BATCHES_PER_CYCLE", 6)))
            types = [x.strip() for x in str(getattr(config, "KIWOOM_RT_TYPES", "0A,0B,0C")).split(",") if x.strip()]
            dur = int(getattr(config, "KIWOOM_RT_SNAPSHOT_SEC", 12))

            kc = KiwoomCollector()
            total = len(universe)
            ws_saved = 0
            chunks_done = 0
            covered = 0

            for _ in range(batches_per_cycle):
                start = self._kiwoom_rt_cursor
                end = min(start + batch_size, total)
                batch = universe[start:end]
                if not batch:
                    self._kiwoom_rt_cursor = 0
                    start = 0
                    end = min(batch_size, total)
                    batch = universe[start:end]
                if not batch:
                    break

                ws_result = kc.collect_realtime_snapshot(stock_codes=batch, types=types, duration_sec=dur)
                ws_saved += int(ws_result.get("saved") or 0)
                chunks_done += 1
                covered += len(batch)
                self._kiwoom_rt_cursor = end if end < total else 0

            flow_ok = 0
            flow_try = 0
            if getattr(config, "KIWOOM_FLOW_ENABLED", False):
                flow_n = max(1, int(getattr(config, "KIWOOM_FLOW_TOP_N", 10)))
                start = self._kiwoom_rt_cursor
                flow_batch = universe[start:start + flow_n] or universe[:flow_n]
                for code in flow_batch:
                    flow_try += 1
                    r = kc.fetch_foreign_flow(code)
                    if r.get("ok"):
                        flow_ok += 1

            logger.info(
                f"[키움실시간스냅샷] universe={total} covered={covered} chunks={chunks_done} "
                f"ws_saved={ws_saved} cursor={self._kiwoom_rt_cursor} flow_ok={flow_ok}/{flow_try}"
            )
        except Exception as e:
            logger.error(f"[키움실시간스냅샷] 오류: {e}", exc_info=True)

    def _loop_kiwoom_condition_snapshot(self) -> None:
        """Persist each saved Hero4 condition's current members and IN/OUT deltas."""
        logger.info("[키움조건검색] 루프 시작")
        self._wait_secs(110)
        while not self._stop_event.is_set():
            now = datetime.now()
            in_session = now.weekday() < 5 and 9 * 60 <= now.hour * 60 + now.minute <= 15 * 60 + 30
            if in_session:
                _run_job_safe("키움조건검색", self._job_kiwoom_condition_snapshot)
                self._wait_secs(max(60, int(getattr(config, "KIWOOM_CONDITION_SNAPSHOT_SECONDS", 300))))
            else:
                self._wait_secs(120)

    def _job_kiwoom_condition_snapshot(self) -> None:
        try:
            from collectors.kiwoom_collector import KiwoomCollector

            result = KiwoomCollector().collect_condition_snapshot(
                max_conditions=max(1, int(getattr(config, "KIWOOM_CONDITION_SCAN_LIMIT", 100)))
            )
            if not result.get("ok"):
                raise RuntimeError(result.get("reason") or "condition snapshot failed")
            logger.info("[키움조건검색] 완료: %s", result)
        except Exception as exc:
            logger.error("[키움조건검색] 오류: %s", exc, exc_info=True)

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
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
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
        if os.getenv("ENABLE_BIGQUERY_DAILY", "0") != "1":
            logger.info("[BigQuery동기화] 자동 실행 비활성화: ENABLE_BIGQUERY_DAILY=1 설정 시에만 실행")
            return
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
        """BigQuery 동기화 후 텐버거/3배 후보 분석까지 연속 실행."""
        if os.getenv("ENABLE_BIGQUERY_DAILY", "0") != "1":
            logger.info("[BigQuery동기화] 수동/스케줄 작업 건너뜀: ENABLE_BIGQUERY_DAILY=1 미설정")
            return
        logger.info("[BigQuery동기화] 증분 동기화 시작")
        try:
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/bigquery_sync.py",
                 "--mode", "daily-lite", "--days", "7"],
                capture_output=True, text=True, timeout=1800
            )
            if result.returncode == 0:
                logger.info(f"[BigQuery동기화] ✅ 완료")
                self._job_bq_triple_pipeline()
            else:
                logger.error(f"[BigQuery동기화] ❌ 오류:\n{result.stderr[-2000:]}")
        except subprocess.TimeoutExpired:
            logger.error("[BigQuery동기화] 타임아웃 (30분 초과)")
        except Exception as e:
            logger.error(f"[BigQuery동기화] 실행 오류: {e}")

    def _loop_bq_triple_pipeline(self) -> None:
        """매일 18:30 BigQuery 3배 패턴 일일 파이프라인 실행."""
        if os.getenv("ENABLE_BQ_TRIPLE_PIPELINE", "0") != "1":
            logger.info("[BQ3배파이프라인] 자동 실행 비활성화: ENABLE_BQ_TRIPLE_PIPELINE=1 설정 시에만 실행")
            return
        logger.info("[BQ3배파이프라인] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            self._wait_until(18, 30, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("BQ3배파이프라인", self._job_bq_triple_pipeline)

    def _job_bq_triple_pipeline(self) -> None:
        """BigQuery 3배 패턴 계산 스크립트 실행."""
        if os.getenv("ENABLE_BQ_TRIPLE_PIPELINE", "0") != "1":
            logger.info("[BQ3배파이프라인] 작업 건너뜀: ENABLE_BQ_TRIPLE_PIPELINE=1 미설정")
            return
        try:
            result = subprocess.run(
                ["venv/bin/python3", "scripts/bigquery_triple_pipeline.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if result.returncode == 0:
                stdout_tail = (result.stdout or '').strip()
                # local_synced 수 추출해서 명시 로그
                local_synced = None
                for line in stdout_tail.splitlines():
                    if "local_sync" in line and "건" in line:
                        local_synced = line.strip()
                        break
                sync_msg = f" | {local_synced}" if local_synced else ""
                logger.info(f"[BQ3배파이프라인] ✅{sync_msg} | {stdout_tail[-200:]}")
            else:
                logger.error(f"[BQ3배파이프라인] ❌ {(result.stderr or '')[-500:]}")
        except Exception as e:
            logger.error(f"[BQ3배파이프라인] 실행 오류: {e}")

    def _loop_bq_morning_alert(self) -> None:
        """매일 07:30 BigQuery 3배 패턴 아침 알림 실행."""
        if os.getenv("ENABLE_BQ_MORNING_ALERT", "0") != "1":
            logger.info("[BQ아침알림] 자동 실행 비활성화: ENABLE_BQ_MORNING_ALERT=1 설정 시에만 실행")
            return
        logger.info("[BQ아침알림] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            self._wait_until(7, 30, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("BQ아침알림", self._job_bq_morning_alert)

    def _job_bq_morning_alert(self) -> None:
        """텐버거 헌터 아침 알림 — 상위 15 후보 + OpenAI mini TOP3 심층 분석 → 텔레그램 발송."""
        if os.getenv("ENABLE_BQ_MORNING_ALERT", "0") != "1":
            logger.info("[BQ아침알림] 작업 건너뜀: ENABLE_BQ_MORNING_ALERT=1 미설정")
            return
        try:
            result = subprocess.run(
                ["venv/bin/python3", "scripts/tenbagger_morning_alert.py", "--top", "15", "--ai-top", "3"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=300,  # AI 3종목 × 90초 기준 여유 포함
            )
            if result.returncode == 0:
                logger.info(f"[텐버거알림] ✅ {(result.stdout or '').strip()[-300:]}")
            else:
                logger.error(f"[텐버거알림] ❌ {(result.stderr or '')[-500:]}")
        except Exception as e:
            logger.error(f"[텐버거알림] 실행 오류: {e}")

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

            now_dt = _dt.now()
            data_ym = now_dt.strftime('%Y%m')
            is_month_end = (now_dt + timedelta(days=1)).month != now_dt.month

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
            snap_row = conn.execute("SELECT value FROM wlb_meta WHERE key='last_month_end_snapshot_ym'").fetchone()
            last_month_end_snapshot_ym = snap_row[0] if snap_row else None
            conn.close()

            logger.info(f"[근로복지공단] totalCount: 현재={current_total:,}, 이전={last_total:,}")

            force_month_end_snapshot = is_month_end and (last_month_end_snapshot_ym != data_ym)
            if current_total == last_total and not force_month_end_snapshot:
                logger.info("[근로복지공단] 변화 없음 — 수집 생략")
                return

            # 3) 변화 감지 → 전체 수집
            if force_month_end_snapshot:
                logger.info(f"[근로복지공단] 월말 강제 스냅샷 실행 (ym={data_ym})")
            else:
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
                if force_month_end_snapshot:
                    conn.execute("""
                        INSERT INTO wlb_meta(key, value, updated_at) VALUES('last_month_end_snapshot_ym', ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """, (data_ym, _dt.now().isoformat()))
                conn.commit(); conn.close()
                logger.info(f"[근로복지공단] {len(aggregated)}개 기업 저장 완료 (ym={data_ym}), total={current_total:,}")
                from employment_monitor.data_quality import audit_employment_data
                quality = audit_employment_data()
                if quality["status"] == "error":
                    raise RuntimeError(f"WLB 수집 후 무결성 오류: {quality['errors']}")
                if quality["warnings"]:
                    logger.warning(f"[근로복지공단] 품질 경고: {quality['warnings']}")
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
        """DART 수주·공급계약 공시 수집. AI 분석/텔레그램 발송은 중단."""
        try:
            from collectors.dart_contract_collector import collect_dart_contracts_catchup
            result = collect_dart_contracts_catchup(
                max_backfill_days=14,
                min_signal=99,
                skip_ai=True,
            )
            if result.get("saved_count", 0):
                logger.info(
                    "[DART수주] %s~%s 구간 %s건 처리 완료 (AI/텔레그램 OFF)",
                    result.get("start_date"),
                    result.get("end_date"),
                    result.get("saved_count", 0),
                )
            else:
                logger.debug(
                    "[DART수주] %s~%s 구간 신규 수주공시 없음",
                    result.get("start_date"),
                    result.get("end_date"),
                )
        except Exception as e:
            logger.error(f"[DART수주] 잡 오류: {e}", exc_info=True)
            raise

    def _loop_order_contracts(self) -> None:
        """DART 단일판매·공급계약 공시 → order_contracts proxy 테이블 일일 적재."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            self._wait_until(19, 0, skip_weekend=True)
            _run_job_safe("DART수주계약", self._job_order_contracts)

    def _job_order_contracts(self) -> None:
        """오늘자 단일판매·공급계약 공시를 order_contracts 테이블에 저장."""
        try:
            from routes.order_contracts import collect_today
            result = asyncio.run(collect_today())
            logger.info(
                "[DART수주계약] 오늘자 스캔 %s건, 신규 %s건 저장",
                result.get("scanned", 0),
                result.get("saved", 0),
            )
        except Exception as e:
            logger.error(f"[DART수주계약] 잡 오류: {e}", exc_info=True)

    def _loop_dart_dilution(self) -> None:
        """매일 07:10 — CB/BW/EB 및 유상/무상증자 희석 이벤트 공시 수집."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            self._wait_until(7, 10, skip_weekend=False)
            _run_job_safe("DART희석공시", self._job_dart_dilution)

    def _job_dart_dilution(self) -> None:
        """DART 희석 이벤트(CB/BW/EB + 유상/무상/유무상증자) 수집."""
        try:
            from collectors.dart_dilution_collector import collect_dilution_events
            from collectors.dart_equity_issue_collector import collect_equity_issue_events
            stats = {
                # 새 접수번호만 처리한다. 정정 공시는 별도 접수번호로 들어오므로 누락되지 않는다.
                "cb_bw_eb": collect_dilution_events(days=365, missing_only=True),
                "equity_issue_2020": collect_equity_issue_events(
                    since="2020-01-01",
                    missing_only=True,
                    limit=500,
                ),
            }
            logger.info(f"[DART희석] 완료: {stats}")
        except Exception as e:
            logger.error(f"[DART희석] 잡 오류: {e}", exc_info=True)

    def _loop_dart_dilution_close(self) -> None:
        """평일 17:20 — 장 마감 뒤 접수된 희석 공시를 개별종목 화면에 반영."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            self._wait_until(17, 20, skip_weekend=True)
            _run_job_safe("DART희석공시마감", self._job_dart_dilution)

    def _loop_us_biotech_pipeline(self) -> None:
        """매일 06:50 — SEC 원문 근거 기반 미국 바이오 파이프라인을 소량씩 갱신."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            self._wait_until(6, 50, skip_weekend=False)
            _run_job_safe("미국바이오파이프라인", self._job_us_biotech_pipeline)

    def _job_us_biotech_pipeline(self) -> None:
        """시총 기준 바이오/의약품 종목을 순차 수집해 SEC 호출 부담을 제한한다."""
        try:
            from collectors.us_biotech_pipeline_collector import collect_biotech_pipelines
            min_cap = float(os.getenv("US_BIOTECH_MIN_MARKET_CAP_USD", "300000000"))
            batch_size = int(os.getenv("US_BIOTECH_PIPELINE_BATCH_SIZE", "100"))
            stats = collect_biotech_pipelines(min_market_cap=min_cap, limit=batch_size)
            logger.info("[미국바이오파이프라인] 완료: %s", stats)
        except Exception as e:
            logger.error("[미국바이오파이프라인] 잡 오류: %s", e, exc_info=True)

    def _loop_source_intelligence_weekly(self) -> None:
        """매일 20:00 — 소스별 신규 글을 반영하고 전수 요약을 갱신한다."""
        self._wait_secs(45)
        while not self._stop_event.is_set():
            self._wait_until(20, 0, skip_weekend=False)
            _run_job_safe("소스인텔리전스주간", self._job_source_intelligence_weekly)

    def _job_source_intelligence_weekly(self) -> None:
        script = Path(__file__).resolve().parent / "scripts" / "ops" / "analyze_trillion_us_biotech.py"
        try:
            result = subprocess.run([sys.executable, str(script)], cwd=str(script.parent.parent.parent), capture_output=True, text=True, timeout=20 * 60)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout)[-1000:])
            logger.info("[소스인텔리전스주간] 완료: %s", (result.stdout or "ok")[-500:])
        except Exception as exc:
            logger.error("[소스인텔리전스주간] 오류: %s", exc, exc_info=True)

    def _loop_us_13f_refresh(self) -> None:
        """매일 07:12 — 공개 13F/PTR 변경 여부를 확인해 미국종목 화면 캐시를 갱신."""
        self._wait_secs(40)
        while not self._stop_event.is_set():
            self._wait_until(7, 12, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("미국13F거물공시", self._job_us_13f_refresh)

    def _job_us_13f_refresh(self) -> None:
        try:
            from routes.us_13f import _build_13f_summary
            result = _build_13f_summary()
            logger.info("[미국13F거물공시] 운용사 %s명, 정치인 %s명, 오류 %s건", len(result.get("managers", [])), len(result.get("politicians", [])), len(result.get("errors", [])))
        except Exception as exc:
            logger.error("[미국13F거물공시] 갱신 실패: %s", exc, exc_info=True)

    def _loop_kiwoom_margin(self) -> None:
        """매일 18:45 — 키움 종목별 신용/대주 잔고 수집."""
        self._wait_secs(30)
        while not self._stop_event.is_set():
            self._wait_until(18, 45, skip_weekend=False)
            _run_job_safe("키움신용잔고", self._job_kiwoom_margin)

    def _job_kiwoom_margin(self) -> None:
        """키움 신용잔고 수집 (코스피+코스닥 시총 상위 80% ≈ 2200종목)."""
        try:
            from collectors.kiwoom_margin_collector import collect_kiwoom_margin_daily
            stats = collect_kiwoom_margin_daily(limit=2200)
            logger.info(f"[키움신용잔고] 완료: {stats}")
        except Exception as e:
            logger.error(f"[키움신용잔고] 잡 오류: {e}", exc_info=True)

    # ── 키움 대량체결 순위 (장중 10분) ────────────────────────────────────
    def _loop_kiwoom_large_trade_rank(self) -> None:
        """키움 ka00190 장중 대량체결 매수/매도 상위 원본을 10분마다 저장."""
        logger.info("[키움대량체결] 루프 시작")
        self._wait_secs(75)
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.weekday() < 5 and 900 <= now.hour * 100 + now.minute <= 1530:
                _run_job_safe("키움대량체결", self._job_kiwoom_large_trade_rank)
                self._wait_secs(600)
            else:
                self._wait_secs(60)
        logger.info("[키움대량체결] 루프 종료")

    def _job_kiwoom_large_trade_rank(self) -> None:
        """주문 없이 키움 대량체결 원본 순위를 수집한다."""
        try:
            from collectors.kiwoom_collector import KiwoomCollector

            collector = KiwoomCollector()
            results = {
                "buy": collector.fetch_large_trade_rank(rank_type="buy"),
                "sell": collector.fetch_large_trade_rank(rank_type="sell"),
            }
            logger.info("[키움대량체결] 완료: %s", results)
        except Exception as exc:
            logger.error("[키움대량체결] 잡 오류: %s", exc, exc_info=True)

    # ── 외국인 지분율 (매일 19:15) ─────────────────────────────────────────
    def _loop_kiwoom_foreign_hold(self) -> None:
        """매일 19:15 — 키움 ka10008 외국인 지분율 수집 (코스피+코스닥 80%)."""
        self._wait_secs(40)
        while not self._stop_event.is_set():
            self._wait_until(19, 15, skip_weekend=True)
            _run_job_safe("키움외국인지분율", self._job_kiwoom_foreign_hold)

    def _job_kiwoom_foreign_hold(self) -> None:
        """키움 ka10008 외국인 지분율 수집."""
        try:
            from collectors.kiwoom_collector import KiwoomCollector
            conn = connect_stock_db(timeout=30)
            stocks = [r[0] for r in conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND market_cap IS NOT NULL
                ORDER BY market_cap DESC
            """).fetchall()]
            conn.close()
            kc = KiwoomCollector()
            result = kc.bulk_collect_foreign_holding(stock_codes=stocks)
            logger.info(f"[키움외국인지분율] 완료: {result}")
        except Exception as e:
            logger.error(f"[키움외국인지분율] 잡 오류: {e}", exc_info=True)

    # ── DART 임원매매 (매주 일요일 02:30 전종목 + 매일 공시 incremental) ───
    def _loop_dart_insider(self) -> None:
        """매주 일요일 02:30 전종목 bulk + 매일 07:05 최근 2일 incremental."""
        self._wait_secs(50)
        _last_daily = None
        while not self._stop_event.is_set():
            now = datetime.now()
            today = now.date()
            # 매일 07:05 incremental (최근 2일 공시)
            if _last_daily != today and now.hour >= 7 and now.minute >= 5:
                _run_job_safe("DART임원매매", self._job_dart_insider_daily)
                _last_daily = today
            # 매주 일요일 02:30 전종목 bulk
            if now.weekday() == 6 and now.hour == 2 and now.minute >= 30:
                _run_job_safe("DART임원매매", self._job_dart_insider_bulk)
                self._wait_secs(3600)
            else:
                self._wait_secs(300)

    def _job_dart_insider_daily(self) -> None:
        """DART 임원매매 최근 2일 incremental."""
        try:
            from collectors.dart_insider_collector import collect_recent_disclosures
            from scripts.build_shareholder_profile import rebuild as rebuild_shareholder_profile
            stats = collect_recent_disclosures(days=2)
            logger.info(f"[DART임원매매daily] 완료: {stats}")
            profile_stats = rebuild_shareholder_profile()
            logger.info(f"[주식수/주요주주프로필] daily 재생성 완료: {profile_stats}")
        except Exception as e:
            logger.error(f"[DART임원매매daily] 오류: {e}", exc_info=True)

    def _job_dart_insider_bulk(self) -> None:
        """DART 임원매매와 5% 주요주주 공시를 전종목 단위로 주간 백필한다."""
        try:
            from collectors.dart_insider_collector import (
                collect_insider_holdings_bulk,
                collect_major_holders_bulk,
            )
            from scripts.build_shareholder_profile import rebuild as rebuild_shareholder_profile
            insider_stats = collect_insider_holdings_bulk(limit=0)
            logger.info(f"[DART임원매매bulk] 완료: {insider_stats}")
            major_stats = collect_major_holders_bulk()
            logger.info(f"[DART주요주주bulk] 완료: {major_stats}")
            profile_stats = rebuild_shareholder_profile()
            logger.info(f"[주식수/주요주주프로필] bulk 재생성 완료: {profile_stats}")
        except Exception as e:
            logger.error(f"[DART임원매매bulk] 오류: {e}", exc_info=True)

    def _loop_dart_backlog(self) -> None:
        """매주 일요일 01:20 — DART 수주잔고 분기 수집(2020년 이후)."""
        self._wait_secs(45)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=1, minute=20, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART수주잔고", self._job_dart_backlog)

    def _job_dart_backlog(self) -> None:
        """DART 정기보고서 기반 수주잔고 분기 적재."""
        try:
            from collectors.dart_backlog_collector import collect_backlog_quarterly
            y_to = datetime.now().year
            stats = collect_backlog_quarterly(year_from=2020, year_to=y_to, limit=None, report_type="CFS")
            missing_stats = collect_backlog_quarterly(
                year_from=2020,
                year_to=y_to,
                limit=None,
                report_type="CFS",
                missing_only=True,
                eligible_only=True,
            )
            stats["missing_only_retry"] = missing_stats
            logger.info(f"[DART수주잔고] 완료: {stats}")
        except Exception as e:
            logger.error(f"[DART수주잔고] 잡 오류: {e}", exc_info=True)

    def _loop_dart_cost(self) -> None:
        """매주 일요일 01:50 — DART 매입재료비/재고/감가상각 분기 수집(최근 5년)."""
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=1, minute=50, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART원가재고", self._job_dart_cost)

    def _job_dart_cost(self) -> None:
        """DART 정기보고서 기반 매입재료비/재고/감가상각 분기 적재."""
        try:
            from collectors.dart_cost_collector import collect_cost_quarterly
            y_to = datetime.now().year
            stats = collect_cost_quarterly(year_from=y_to - 5, year_to=y_to, limit=None, report_type="CFS")
            logger.info(f"[DART원가재고] 완료: {stats}")
            try:
                import subprocess as _subprocess
                import sys as _sys
                _script = Path(__file__).resolve().parent / "scripts" / "build_inventory_sales_signals.py"
                _proc = _subprocess.run(
                    [_sys.executable, str(_script), "--since-year", str(y_to - 6)],
                    cwd=str(Path(__file__).resolve().parent),
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
                if _proc.returncode == 0:
                    logger.info(f"[DART원가재고] 재고·매출·수주 시그널 빌드 완료: {_proc.stdout.strip()}")
                else:
                    logger.warning(
                        "[DART원가재고] 재고·매출·수주 시그널 빌드 실패 "
                        f"rc={_proc.returncode} stdout={_proc.stdout[-500:]} stderr={_proc.stderr[-500:]}"
                    )
            except Exception as _sig_e:
                logger.warning(f"[DART원가재고] 재고·매출·수주 시그널 빌드 오류: {_sig_e}")
        except Exception as e:
            logger.error(f"[DART원가재고] 잡 오류: {e}", exc_info=True)

    def _loop_dart_material_purchase(self) -> None:
        """매주 일요일 02:20 — DART 사업보고서 원재료 매입액 전용 수집."""
        self._wait_secs(75)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=2, minute=20, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART매입재료비", self._job_dart_material_purchase)

    def _job_dart_material_purchase(self) -> None:
        """dart_material_purchase 보강. 감사에서 저용량이면 이 테이블을 직접 채운다."""
        try:
            y_to = datetime.now().year - 1
            years = [str(y) for y in range(max(2020, y_to - 5), y_to + 1)]
            cmd = [
                sys.executable,
                "collectors/dart_material_purchase_collector.py",
                "--limit", "2700",
                "--years",
                *years,
            ]
            logger.info(f"[DART매입재료비] 시작: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=4 * 3600,
            )
            logger.info(f"[DART매입재료비] 완료: returncode={result.returncode} stdout={result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.warning(f"[DART매입재료비] stderr: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            logger.warning("[DART매입재료비] 4시간 타임아웃 — 다음 주 재시도")
        except Exception as e:
            logger.error(f"[DART매입재료비] 잡 오류: {e}", exc_info=True)

    def _loop_dart_employee_count(self) -> None:
        """매주 일요일 02:55 — DART empSttus 기반 dart_employee_count 전용 수집."""
        self._wait_secs(80)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=2, minute=55, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART직원수", self._job_dart_employee_count)

    def _job_dart_employee_count(self) -> None:
        """기존 CH 배치 스크립트를 직원수 전용 모드로 실행해 dart_employee_count를 보강."""
        try:
            cmd = [
                sys.executable,
                "scripts/collect_dart_ch_data.py",
                "--limit", "2200",
                "--skip-existing",
                "--employee-only",
            ]
            logger.info(f"[DART직원수] 시작: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=4 * 3600,
            )
            logger.info(f"[DART직원수] 완료: returncode={result.returncode} stdout={result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.warning(f"[DART직원수] stderr: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            logger.warning("[DART직원수] 4시간 타임아웃 — 다음 주 재시도")
        except Exception as e:
            logger.error(f"[DART직원수] 잡 오류: {e}", exc_info=True)

    def _loop_dart_ch_extra(self) -> None:
        """매주 일요일 03:10 — 직원현황/판관비/매출채권 CH 보강 수집."""
        self._wait_secs(85)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=3, minute=10, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART임직원CH", self._job_dart_ch_extra)

    def _job_dart_ch_extra(self) -> None:
        """dart_employee_count 포함 CH 보강 배치."""
        try:
            cmd = [
                sys.executable,
                "scripts/collect_dart_ch_extra.py",
                "--limit", "2200",
            ]
            logger.info(f"[DART임직원CH] 시작: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=4 * 3600,
            )
            logger.info(f"[DART임직원CH] 완료: returncode={result.returncode} stdout={result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.warning(f"[DART임직원CH] stderr: {result.stderr[-500:]}")
            # 2026-09 수정: 아래 두 파생신호 재구축을 "DART임직원CH 성공 시에만"
            # 실행하도록 else절에 묶어뒀던 기존 구조 — collect_dart_ch_extra.py가
            # (무관한 원인으로) 실패/타임아웃하면 계약부채·현금전환 신호가
            # 매주 통째로 갱신 안 되는 연쇄장애였음(실측: 현금전환 신호 39일
            # 무갱신). 두 신호 모두 financial_data/cash_flow_data/dart_bs_items
            # 등 별도 원천을 쓰므로 이 잡의 성공 여부와 무관하게 독립 실행.
            derived = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_contract_advance_signals.py",
                    "--since-year",
                    "2020",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=600,
            )
            logger.info(
                "[계약부채선수금] 파생 신호 재구축 완료: returncode=%s stdout=%s",
                derived.returncode,
                derived.stdout[-300:] if derived.stdout else "",
            )
            if derived.returncode != 0:
                logger.warning(f"[계약부채선수금] stderr: {derived.stderr[-500:]}")
            cashq = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_cash_conversion_signals.py",
                    "--since-year",
                    "2020",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=900,
            )
            logger.info(
                "[현금전환품질] 파생 신호 재구축 완료: returncode=%s stdout=%s",
                cashq.returncode,
                cashq.stdout[-300:] if cashq.stdout else "",
            )
            if cashq.returncode != 0:
                logger.warning(f"[현금전환품질] stderr: {cashq.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            logger.warning("[DART임직원CH] 4시간 타임아웃 — 다음 주 재시도")
        except Exception as e:
            logger.error(f"[DART임직원CH] 잡 오류: {e}", exc_info=True)

    def _loop_dart_segment(self) -> None:
        """매주 일요일 03:30 — 사업부문별 매출 수집."""
        self._wait_secs(90)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_to_sunday)).replace(hour=3, minute=30, second=0, microsecond=0)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("DART세그먼트", self._job_dart_segment)

    def _job_dart_segment(self) -> None:
        """DART HTML 사업보고서 기반 사업부문별 매출 적재."""
        try:
            import subprocess, sys
            limit = int(os.getenv("DART_SEGMENT_WEEKLY_LIMIT", "1200"))
            start_year = int(os.getenv("DART_SEGMENT_START_YEAR", "2020"))
            years = ",".join(str(y) for y in range(start_year, datetime.now().year + 1))
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/collect_dart_segment_revenue.py",
                    "--limit",
                    str(limit),
                    "--years",
                    years,
                ],
                capture_output=True, text=True, timeout=7200,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[DART세그먼트] 완료: {result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.warning(f"[DART세그먼트] stderr: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[DART세그먼트] 잡 오류: {e}", exc_info=True)

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

    def _loop_financial_integrity_daily(self) -> None:
        """매일 06:20 — 전일/새벽 수집 후 재무 이상값 수리와 무결성 리포트 생성."""
        logger.info("[재무무결성일일] 루프 시작")
        self._wait_secs(70)
        while not self._stop_event.is_set():
            self._wait_until(6, 20, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("재무무결성일일", self._job_financial_integrity_daily)
        logger.info("[재무무결성일일] 루프 종료")

    def _job_financial_integrity_daily(self) -> None:
        """가벼운 재무 품질 수리 후 data_integrity_check 리포트 저장."""
        try:
            repair = subprocess.run(
                [sys.executable, "scripts/ops/repair_integrity_findings_20260727.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=900,
            )
            logger.info(
                "[재무무결성일일] 수리 완료: returncode=%s stdout=%s",
                repair.returncode,
                repair.stdout[-700:] if repair.stdout else "",
            )
            if repair.returncode != 0:
                logger.warning("[재무무결성일일] 수리 stderr=%s", repair.stderr[-700:] if repair.stderr else "")
                raise RuntimeError(f"daily financial integrity repair failed: {repair.returncode}")

            audit = subprocess.run(
                [
                    sys.executable,
                    "scripts/ops/data_integrity_check.py",
                    "--out-dir",
                    "research_outputs/data_integrity_daily",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=900,
            )
            logger.info(
                "[재무무결성일일] 검사 완료: returncode=%s stdout=%s",
                audit.returncode,
                audit.stdout[-700:] if audit.stdout else "",
            )
            if audit.returncode != 0:
                logger.warning("[재무무결성일일] 검사 경고 stderr=%s", audit.stderr[-700:] if audit.stderr else "")
                raise RuntimeError(f"daily financial integrity audit failed: {audit.returncode}")
        except subprocess.TimeoutExpired:
            logger.warning("[재무무결성일일] 15분 타임아웃")
        except Exception as e:
            logger.error(f"[재무무결성일일] 오류: {e}", exc_info=True)

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
            from check_financial_integrity import latest_reported_quarter

            latest_year, latest_quarter = latest_reported_quarter()
            if latest_quarter == 2:
                start = f"{latest_year}0701"
                end = date.today().strftime("%Y%m%d")
                refresh = subprocess.run(
                    [
                        sys.executable,
                        "scripts/refresh_dart_disclosures_recent.py",
                        "--start", start,
                        "--end", end,
                    ],
                    cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )
                if refresh.returncode != 0:
                    raise RuntimeError(f"Q2 disclosure refresh failed: {refresh.stderr[-700:]}")
                backfill = subprocess.run(
                    [
                        sys.executable,
                        "scripts/backfill_dart_q2_financials.py",
                        "--year", str(latest_year),
                        "--report", f"scratch/q2_backfill_{latest_year}.json",
                    ],
                    cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                    capture_output=True,
                    text=True,
                    timeout=4 * 3600,
                )
                if backfill.returncode != 0:
                    raise RuntimeError(f"Q2 financial backfill failed: {backfill.stderr[-700:]}")
                logger.info("[재무무결점분기] Q2 원문 수집 완료: %s", backfill.stdout[-700:])

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
        try:
            from routes.stock_analysis_rs import capture_theme_membership_snapshot
            snapshot = capture_theme_membership_snapshot(today.isoformat())
            logger.info(f"[RSTagSnapshot] {snapshot}")
        except Exception as e:
            logger.error(f"[RSTagSnapshot] 오류: {e}", exc_info=True)
        try:
            from collectors.krx_security_reference_collector import collect_reference
            from security_master import rebuild_security_master
            reference_stats = collect_reference(as_of=today.isoformat())
            logger.info(f"[KRXSecurityReference] 갱신 완료: {reference_stats}")
            master_stats = rebuild_security_master()
            logger.info(f"[AsOfSecurityMaster] 재생성 완료: {master_stats}")
        except Exception as e:
            logger.error(f"[AsOfSecurityMaster] 재생성 오류: {e}", exc_info=True)
        # 상장주식수 스냅샷과 최신 DART 공시를 결합해 자본행위/수정계수를 갱신한다.
        try:
            import subprocess, sys
            script = str(Path(__file__).resolve().parent / "scripts" / "build_corporate_action_adjustment_engine.py")
            result = subprocess.run(
                [sys.executable, script], capture_output=True, text=True, timeout=300,
                cwd=str(Path(__file__).resolve().parent),
            )
            if result.returncode == 0:
                logger.info(f"[자본행위보정] 갱신 완료: {result.stdout.strip()[-300:]}")
                audit_script = str(Path(__file__).resolve().parent / "scripts" / "audit_price_jumps_and_build_canonical.py")
                audit = subprocess.run(
                    [sys.executable, audit_script], capture_output=True, text=True, timeout=600,
                    cwd=str(Path(__file__).resolve().parent),
                )
                if audit.returncode == 0:
                    logger.info(f"[가격급변감사] 갱신 완료: {audit.stdout.strip()[-300:]}")
                    external_script = str(Path(__file__).resolve().parent / "scripts" / "verify_price_history_with_naver.py")
                    external = subprocess.run(
                        [sys.executable, external_script, "--only-new"],
                        capture_output=True, text=True, timeout=600,
                        cwd=str(Path(__file__).resolve().parent),
                    )
                    if external.returncode == 0:
                        logger.info(f"[외부가격검증] 신규 사건 확인 완료: {external.stdout.strip()[-300:]}")
                    else:
                        logger.error(f"[외부가격검증] 오류: {external.stderr[-300:]}")
                    for label, rel_script in (
                        ("시장국면", "scripts/build_market_regime_history.py"),
                        ("설명형신호", "scripts/build_explainable_stock_signals.py"),
                        ("데이터계보", "scripts/build_data_lineage_catalog.py"),
                        ("전략센터전진신호", "scripts/capture_strategy_center_forward_signals.py"),
                        ("신호사후성과", "scripts/update_live_signal_outcomes.py"),
                        ("전진검증감사", "scripts/audit_forward_validation.py"),
                    ):
                        refresh = subprocess.run(
                            [sys.executable, str(Path(__file__).resolve().parent / rel_script)],
                            capture_output=True, text=True, timeout=600,
                            cwd=str(Path(__file__).resolve().parent),
                        )
                        if refresh.returncode == 0:
                            logger.info(f"[{label}] 갱신 완료: {refresh.stdout.strip()[-200:]}")
                        else:
                            logger.error(f"[{label}] 오류: {refresh.stderr[-300:]}")
                else:
                    logger.error(f"[가격급변감사] 오류: {audit.stderr[-300:]}")
            else:
                logger.error(f"[자본행위보정] 오류: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[자본행위보정] 예외: {e}")

    # ──────────────────────────────────────────────────────────
    # FnGuide 연결/별도 재무제표 + 스냅샷 (매월 3일 05:00)
    # ──────────────────────────────────────────────────────────
    def _loop_fnguide_financial_monthly(self) -> None:
        """FnGuide 재무 수집 — 두 가지 모드:

        [초기 백로그 모드] stale_days=7 미만 종목이 남아 있는 동안:
          매일 05:00 실행 → 미검증 종목을 rate limit 소진까지 처리

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
                _c = connect_stock_db()
                row = _c.execute("""
                    SELECT COUNT(DISTINCT su.stock_code)
                    FROM stock_universe su
                    LEFT JOIN (
                        SELECT stock_code, MAX(fetched_at) AS last_fetched
                        FROM financial_source_snapshot
                        WHERE data_source='fnguide'
                        GROUP BY stock_code
                    ) fss ON fss.stock_code = su.stock_code
                    WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                      AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                      AND (fss.last_fetched IS NULL
                           OR fss.last_fetched::timestamp < datetime('now','-7 days'))
                """).fetchone()
                _c.close()
                backlog = (row[0] if row else 0)
            except Exception:
                backlog = 9999  # 알 수 없으면 백로그 모드 유지

            if backlog > 50:
                # 백로그 모드: 매일 05:00 (DART 재수집 00:30~04:30 이후 실행)
                next_run = now.replace(hour=5, minute=0, second=0, microsecond=0)
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

        # FnGuide 수집 후 financial_data 자동 동기화 (스냅샷 → DB)
        logger.info("[FnGuide재무] 동기화 실행 (fnguide_integrity_sync.py --all)")
        try:
            sync_script = str(Path(__file__).resolve().parent / "scripts" / "fnguide_integrity_sync.py")
            sync_result = subprocess.run(
                [sys.executable, sync_script, "--all"],
                capture_output=True, text=True, timeout=1800,
                cwd=str(Path(__file__).resolve().parent),
            )
            if sync_result.returncode == 0:
                last = sync_result.stdout.strip().split("\n")[-1]
                logger.info(f"[FnGuide재무] 동기화 완료: {last}")
            else:
                logger.error(f"[FnGuide재무] 동기화 오류: {sync_result.stderr[-200:]}")
        except Exception as e:
            logger.error(f"[FnGuide재무] 동기화 예외: {e}")

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

        def _ym_offset(base: datetime, month_offset: int) -> str:
            year = base.year
            month = base.month + month_offset
            while month <= 0:
                year -= 1
                month += 12
            while month > 12:
                year += 1
                month -= 12
            return f"{year}{month:02d}"

        today = datetime.now()
        start_ym = _ym_offset(today, -1)
        end_ym = _ym_offset(today, 0)
        try:
            # 1. 가집계 수집
            r = subprocess.run(
                [
                    sys.executable,
                    str(Path(lab) / "scripts" / "collect_provisional_10day.py"),
                    "--start-ym",
                    start_ym,
                    "--end-ym",
                    end_ym,
                    "--export-csv",
                ],
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

    def _loop_disclosure_db_batch(self) -> None:
        """매주 일요일 02:00 — DART 전종목 공시 10년치 DB 저장."""
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 다음 일요일 02:00 계산
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0 and now.hour >= 2:
                days_until_sunday = 7
            next_run = (now + timedelta(days=days_until_sunday)).replace(
                hour=2, minute=0, second=0, microsecond=0
            )
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("공시DB배치", self._job_disclosure_db_batch)

    def _job_disclosure_db_batch(self) -> None:
        """DART API로 전종목 공시 일괄 수집 → dart_disclosures 테이블 저장."""
        import subprocess, sys
        script = str(Path(__file__).resolve().parent / "collect_dart_disclosures.py")
        try:
            r = subprocess.run(
                [sys.executable, script, "--skip-days", "7", "--years", "10"],
                capture_output=True, text=True, timeout=7 * 3600,  # 최대 7시간
                cwd=str(Path(__file__).resolve().parent),
            )
            logger.info(f"[공시DB배치] 완료: {r.stdout[-300:]}")
            if r.returncode != 0:
                logger.error(f"[공시DB배치] 오류: {r.stderr[-300:]}")
            else:
                ledger_script = str(Path(__file__).resolve().parent / "scripts" / "build_data_availability_ledger.py")
                ledger = subprocess.run(
                    [sys.executable, ledger_script], capture_output=True, text=True, timeout=600,
                    cwd=str(Path(__file__).resolve().parent),
                )
                if ledger.returncode == 0:
                    logger.info(f"[시점일치원장] 갱신 완료: {ledger.stdout.strip()[-300:]}")
                else:
                    logger.error(f"[시점일치원장] 오류: {ledger.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[공시DB배치] 예외: {e}")

    # ── KRX 전종목 투자자 수급 — Playwright 브라우저 (매일 18:10 영업일) ──
    def _loop_krx_investor_playwright(self) -> None:
        """매일 18:10 영업일 — KRX 전종목 기관/외국인/개인 순매수 금액 수집.

        requests 방식은 KRX CSV 다운로드 보안에 막혀 실패.
        Playwright(실 브라우저)로 세션 유지 → OTP 발급 → CSV 저장.
        저장: price_history.inst_net_buy_amt / frn_net_buy_amt / ind_net_buy_amt (백만원)
        """
        logger.info("[KRX투자자수급] Playwright 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            self._wait_until(18, 10, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                _run_job_safe("KRX투자자수급", self._job_krx_investor_playwright)
            except Exception as e:
                logger.error(f"[KRX투자자수급] 잡 오류: {e}")
            self._wait_secs(23 * 3600)
        logger.info("[KRX투자자수급] Playwright 루프 종료")

    def _job_krx_investor_playwright(self) -> None:
        """KRX Playwright 수급 수집 실행."""
        import subprocess, sys
        script = str(Path(__file__).resolve().parent / "scripts" / "collect_krx_investor_playwright.py")
        today = date.today().strftime("%Y%m%d")
        try:
            r = subprocess.run(
                [sys.executable, script, "--date", today],
                capture_output=True, text=True, timeout=600,
                cwd=str(Path(__file__).resolve().parent),
            )
            logger.info(f"[KRX투자자수급] {today} 완료: {r.stdout[-200:]}")
            if r.returncode != 0:
                logger.error(f"[KRX투자자수급] 오류: {r.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[KRX투자자수급] 예외: {e}")

    # ── KRX 프로그램매매(차익/비차익) — 매일 18:20 영업일 ──
    def _loop_krx_program_trading(self) -> None:
        """KRX 프로그램매매 수집 루프 (18:20 영업일)."""
        logger.info("[KRX프로그램매매] 루프 시작")
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.weekday() < 5 and _seconds_until(18, 20) < 60:
                _run_job_safe("KRX프로그램매매", self._job_krx_program_trading)
            self._stop_event.wait(60)
        logger.info("[KRX프로그램매매] 루프 종료")

    def _job_krx_program_trading(self) -> None:
        """Broker market-level program trading catch-up.

        The legacy KRX Playwright collector writes program_trading_daily, while
        dashboard health and tenbagger signals read broker_program_market_daily.
        Use the broker collector here so the visible dataset is actually kept
        fresh, and catch up recent fully populated trading days.
        """
        import subprocess, sys
        script = str(Path(__file__).resolve().parent / "scripts" / "collect_broker_program_trading.py")
        dates = _recent_price_trade_dates(limit=5)
        missing = _missing_dataset_dates(
            "broker_program_market_daily",
            "dt",
            dates,
            min_coverage=2,
            coverage_expr="COUNT(DISTINCT market)",
            where="source='kiwoom'",
        )
        if not missing:
            logger.info("[KRX프로그램매매] 최근 거래일 결측 없음")
            return
        try:
            start, end = min(missing), max(missing)
            r = subprocess.run(
                [
                    sys.executable, script,
                    "--start", start,
                    "--end", end,
                    "--source", "kiwoom",
                    "--market-only",
                    "--skip-existing",
                ],
                capture_output=True, text=True, timeout=600,
                cwd=str(Path(__file__).resolve().parent),
            )
            logger.info(f"[KRX프로그램매매] {start}~{end} 완료: {r.stdout[-500:]}")
            if r.returncode != 0:
                logger.error(f"[KRX프로그램매매] 오류: {r.stderr[-500:]}")
        except Exception as e:
            logger.error(f"[KRX프로그램매매] 예외: {e}")

    # ── 브로커 종목별 프로그램매매 — 매일 18:50 영업일 ──
    def _loop_broker_program_stock_trading(self) -> None:
        """Kiwoom 종목별 프로그램매매 수집 및 서버 재시작 후 결측 보충."""
        logger.info("[종목프로그램매매] 루프 시작")
        # A process restart at 18:50 used to skip the entire trading day.  Run
        # one bounded catch-up after startup; the job itself selects only dates
        # that are below coverage, so a healthy store is a no-op.
        self._wait_secs(120)
        if not self._stop_event.is_set():
            _run_job_safe("종목프로그램매매", self._job_broker_program_stock_trading)
        while not self._stop_event.is_set():
            wait = _seconds_until(18, 50, skip_weekend=True)
            if self._stop_event.wait(wait):
                break
            _run_job_safe("종목프로그램매매", self._job_broker_program_stock_trading)
        logger.info("[종목프로그램매매] 루프 종료")

    def _job_broker_program_stock_trading(self) -> None:
        """Kiwoom 종목별 프로그램 매수/매도/순매수 일별 수집."""
        import subprocess, sys
        script = str(Path(__file__).resolve().parent / "scripts" / "collect_broker_program_trading.py")
        dates = _recent_price_trade_dates(limit=5)
        # Do not depend solely on price-history completion.  Program data may
        # be available for the latest KRX session even while another collector
        # is late, and that session must still be repaired after a restart.
        expected = datetime.now().date()
        while not is_trading_day(expected, "KR"):
            expected -= timedelta(days=1)
        expected_key = expected.strftime("%Y%m%d")
        dates = [expected_key] + [d for d in dates if d != expected_key]
        missing = _missing_dataset_dates(
            "broker_program_stock_daily",
            "dt",
            dates,
            min_coverage=2000,
            coverage_expr="COUNT(DISTINCT stock_code)",
            where="source='kiwoom'",
        )
        if not missing:
            logger.info("[종목프로그램매매] 최근 거래일 결측 없음")
            return
        try:
            start, end = min(missing), max(missing)
            r = subprocess.run(
                [
                    sys.executable, script,
                    "--start", start,
                    "--end", end,
                    "--source", "kiwoom",
                    "--all-stocks",
                    "--save-all-returned",
                    "--skip-existing",
                    "--sleep", "0",
                ],
                capture_output=True, text=True, timeout=3600,
                cwd=str(Path(__file__).resolve().parent),
            )
            logger.info(f"[종목프로그램매매] {start}~{end} 완료: {r.stdout[-800:]}")
            if r.returncode != 0:
                logger.error(f"[종목프로그램매매] 오류: {r.stderr[-500:]}")
        except Exception as e:
            logger.error(f"[종목프로그램매매] 예외: {e}")

    # ── RS/52주 사전계산 캐시 워밍 (매일 18:30 영업일) ──
    def _loop_rs_precompute(self) -> None:
        """KRX 데이터 확정 후 RS·52주 캐시를 선반영. API 첫 요청 대기 시간(7초) 제거."""
        logger.info("[RS사전계산] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            self._wait_until(18, 30, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                import requests as _req
                resp = _req.post("http://localhost:8000/api/stock-analysis-rs/precompute", timeout=300)
                logger.info(f"[RS사전계산] 완료: {resp.text[:200]}")
            except Exception as e:
                logger.error(f"[RS사전계산] 오류: {e}")
            self._wait_secs(23 * 3600)
        logger.info("[RS사전계산] 루프 종료")

    # ── CF 3중 검증 (매일 05:30 — DART 03:30 수집 + FnGuide 수집 이후) ──
    def _loop_weekly_revalidation(self) -> None:
        """매주 일요일 03:00 전종목 4중 검증 (Phase C+E+F, DB 전용)."""
        logger.info("[주간4중검증] 루프 시작")
        self._wait_secs(120)
        while not self._stop_event.is_set():
            # 일요일(weekday=6) 03:00까지 대기
            import datetime as _dt
            now = _dt.datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_sunday = now.replace(hour=3, minute=0, second=0, microsecond=0) + _dt.timedelta(days=days_to_sunday)
            wait_secs = max(0, (next_sunday - now).total_seconds())
            self._stop_event.wait(timeout=wait_secs)
            if self._stop_event.is_set():
                break
            _run_job_safe("주간4중검증", self._job_weekly_revalidation)
        logger.info("[주간4중검증] 루프 종료")

    def _job_weekly_revalidation(self) -> None:
        """전종목 4중 검증 실행 (약 2~3시간 소요)."""
        try:
            import subprocess
            import sys
            result = subprocess.run(
                [sys.executable, "scratch/weekly_revalidation.py", "--phase", "C,E,F"],
                capture_output=True, text=True, timeout=10800  # 3시간 타임아웃
            )
            logger.info(f"[주간4중검증] 완료: returncode={result.returncode}")
            if result.returncode != 0:
                logger.error(f"[주간4중검증] stderr: {result.stderr[-500:]}")
        except Exception as e:
            logger.error(f"[주간4중검증] 오류: {e}", exc_info=True)

    def _loop_postgres_weekly_backup(self) -> None:
        """매주 일요일 05:10 전체 PostgreSQL 스냅샷 백업."""
        logger.info("[PostgreSQL주간백업] 루프 시작")
        self._wait_secs(180)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_to_sunday = (6 - now.weekday()) % 7
            next_run = now.replace(hour=5, minute=10, second=0, microsecond=0) + timedelta(days=days_to_sunday)
            if next_run <= now:
                next_run += timedelta(days=7)
            self._stop_event.wait(timeout=max(0, (next_run - now).total_seconds()))
            if self._stop_event.is_set():
                break
            _run_job_safe("PostgreSQL주간백업", self._job_postgres_weekly_backup)
        logger.info("[PostgreSQL주간백업] 루프 종료")

    def _job_postgres_weekly_backup(self) -> None:
        script = str(Path(__file__).resolve().parent / "scripts" / "postgres_disaster_recovery.py")
        backup_result = subprocess.run(
            [sys.executable, script, "backup"],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if backup_result.returncode != 0:
            raise RuntimeError(f"PostgreSQL backup failed: {backup_result.stderr[-1000:]}")

        # 2026-08-22: 백업은 매주 정상 생성됐지만 그 백업을 실제로 복원해보는
        # restore-test를 트리거하는 스케줄이 애초에 없어서, restore_test_latest.json이
        # 8/15자 옛 백업을 계속 참조 — audit(매일 05:50)이 "최신 백업과 다른 백업을
        # 복원테스트함"으로 3일 넘게 매일 텔레그램 실패알림을 반복해서 보내고 있었음.
        # 백업 생성 직후 그 백업을 대상으로 즉시 restore-test까지 수행해 봉합.
        import json as _json
        try:
            manifest = _json.loads(backup_result.stdout)
            backup_path = manifest.get("backup_path")
        except Exception:
            backup_path = None
        if backup_path:
            restore_result = subprocess.run(
                [sys.executable, script, "restore-test", backup_path],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if restore_result.returncode != 0:
                logger.error(f"[PostgreSQL주간백업] 복원테스트 실패: {restore_result.stderr[-1000:]}")
            else:
                logger.info(f"[PostgreSQL주간백업] 복원테스트 완료: {restore_result.stdout[-500:]}")
        else:
            logger.warning("[PostgreSQL주간백업] backup 출력에서 backup_path 파싱 실패 — 복원테스트 스킵")

        prune_result = subprocess.run(
            [sys.executable, script, "prune", "--apply"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if prune_result.returncode != 0:
            raise RuntimeError(f"PostgreSQL backup pruning failed: {prune_result.stderr[-1000:]}")
        logger.info("[PostgreSQL주간백업] 완료: %s", backup_result.stdout[-1200:])

    def _loop_postgres_backup_health(self) -> None:
        """매일 05:50 최신 전체 백업과 복원시험 증적 검증."""
        logger.info("[PostgreSQL백업상태] 루프 시작")
        self._wait_secs(240)
        while not self._stop_event.is_set():
            self._wait_until(5, 50, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("PostgreSQL백업상태", self._job_postgres_backup_health)
            self._wait_secs(23 * 3600)
        logger.info("[PostgreSQL백업상태] 루프 종료")

    def _job_postgres_backup_health(self) -> None:
        script = str(Path(__file__).resolve().parent / "scripts" / "postgres_disaster_recovery.py")
        result = subprocess.run(
            [sys.executable, script, "audit"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            try:
                from notifier import send

                send(
                    "<b>PostgreSQL 백업 검증 실패</b>\n"
                    + (result.stderr or result.stdout)[-1200:],
                    key=f"postgres_backup_failure_{date.today().isoformat()}",
                )
            except Exception:
                pass
            raise RuntimeError(f"PostgreSQL backup audit failed: {(result.stderr or result.stdout)[-1200:]}")
        logger.info("[PostgreSQL백업상태] 정상: %s", result.stdout[-1000:])

    def _loop_postgres_cutover_verify(self) -> None:
        """매일 06:10 — SQLite→PostgreSQL 커트오버 전체 정합성(테이블별 드리프트·매크로 오염) 감시.

        2026-08-11 발견: 크론/스케줄러의 일부 작업이 postgres 라우터를 타지 않고
        stock.db에만 계속 써서 특정 테이블이 조용히 뒤처지는 사례가 반복됨
        (signal_result/report_files/short_rank_daily/체리형부최신채널 등).
        이 잡은 verify_postgres_cutover.py로 매일 감시하고, 단순 행수 드리프트는
        검증된 자연키 upsert 브리지(sync_sqlite_bridge_delta.py)로 자동 복구를
        1회 시도한 뒤 재검증한다. 복구 후에도 실패가 남으면(매크로 오염, 백업
        신선도 등 사람 판단이 필요한 항목) 텔레그램으로 알린다.
        """
        logger.info("[PostgreSQL커트오버검증] 루프 시작")
        self._wait_secs(300)
        while not self._stop_event.is_set():
            self._wait_until(6, 10, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("PostgreSQL커트오버검증", self._job_postgres_cutover_verify)
        logger.info("[PostgreSQL커트오버검증] 루프 종료")

    def _job_postgres_cutover_verify(self) -> None:
        import json as _json

        script = str(Path(__file__).resolve().parent / "scripts" / "verify_postgres_cutover.py")
        bridge = str(Path(__file__).resolve().parent / "scripts" / "sync_sqlite_bridge_delta.py")

        def _run_verify() -> dict:
            # 2026-08-22: 데이터량 증가(price_history 840만+행 등)로 실행이 4분대에
            # 근접해 300초 타임아웃에 자주 걸림 — 강제종료된 불완전 출력이 매번
            # "report parse failed"로 기록되며 3일 연속 텔레그램 실패알림을 유발.
            # 실측 단독실행 소요 약 4분 → 여유를 두고 900초로 상향.
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True,
                text=True,
                timeout=900,
            )
            try:
                return _json.loads(result.stdout)
            except Exception:
                # 2026-08-25: 실패 원인이 "report parse failed"로만 남아 실제 원인(스크립트
                # 크래시 시 stderr에 남는 traceback)을 알 수 없었음 — stderr도 함께 로깅해
                # 다음 발생 시 원인을 바로 알 수 있게 함(예: sqlite3 lock timeout).
                logger.error(
                    "[PostgreSQL커트오버검증] 보고서 파싱 실패, returncode=%s stderr=%s stdout=%s",
                    result.returncode, result.stderr[-1500:] if result.stderr else "", result.stdout[-500:] if result.stdout else "",
                )
                return {
                    "ok": result.returncode == 0,
                    "failures": ["report parse failed"],
                    "raw": result.stdout[-1000:],
                    "stderr": result.stderr[-1000:] if result.stderr else "",
                }

        report = _run_verify()
        behind_tables = [item["table"] for item in report.get("postgres_behind", [])]
        healed = False
        if behind_tables:
            logger.warning("[PostgreSQL커트오버검증] 드리프트 감지: %s → 자동 복구 시도", behind_tables)
            heal_result = subprocess.run(
                [sys.executable, bridge, *behind_tables],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if heal_result.returncode != 0:
                raise RuntimeError(
                    f"PostgreSQL cutover bridge exited {heal_result.returncode}: "
                    f"{(heal_result.stderr or heal_result.stdout)[-1500:]}"
                )
            logger.info("[PostgreSQL커트오버검증] 복구 결과: %s", heal_result.stdout[-1500:])
            healed = True
            report = _run_verify()

        if report.get("ok"):
            if healed:
                logger.info("[PostgreSQL커트오버검증] 드리프트 자동 복구 완료: %s", behind_tables)
            else:
                logger.info("[PostgreSQL커트오버검증] 정상")
            return

        failures = report.get("failures", [])
        try:
            from notifier import send

            stderr_hint = report.get("stderr") or ""
            send(
                "<b>PostgreSQL 커트오버 검증 실패</b>\n"
                + ("자동복구 시도 후에도 실패\n" if healed else "")
                + "\n".join(str(f) for f in failures[:10])
                + (f"\n\nstderr:\n{stderr_hint[-500:]}" if stderr_hint else ""),
                key=f"postgres_cutover_verify_failure_{date.today().isoformat()}",
            )
        except Exception:
            pass
        raise RuntimeError(f"PostgreSQL cutover verify failed: {failures}")

    def _loop_cf_triple_validate(self) -> None:
        """신규 수집된 현금흐름(최근 7일) 대상 DART·FnGuide·Seibro 3중 자동 검증."""
        logger.info("[CF3중검증] 루프 시작")
        self._wait_secs(90)  # 서버 초기화 이후 실행
        while not self._stop_event.is_set():
            self._wait_until(5, 30)
            if self._stop_event.is_set():
                break
            _run_job_safe("CF3중검증", self._job_cf_triple_validate)
        logger.info("[CF3중검증] 루프 종료")

    def _job_cf_triple_validate(self) -> None:
        """최근 7일 내 신규 DART CF 데이터 3중 검증 실행."""
        try:
            from collectors.cf_triple_validator import validate_recent
            stats = validate_recent(days=7)
            logger.info(
                f"[CF3중검증] 완료 — "
                f"CONFIRMED:{stats.get('confirmed',0)} "
                f"AMBIGUOUS:{stats.get('ambiguous',0)} "
                f"SKIP:{stats.get('skipped',0)}"
            )
        except Exception as e:
            logger.error(f"[CF3중검증] 오류: {e}", exc_info=True)

    # ── DART 재무 재수집 (매일 00:30 — 상폐·ETF·ETN 제외 활성 종목) ───────────
    def _loop_data_integrity_followup(self) -> None:
        """매일 00:05 — 2026-08-22 세션에서 발견했으나 DART 일일한도 소진으로 미완료된
        데이터 이상치(revenue_extreme_yoy, dilution.suspicious_denominator)를
        scripts/data_integrity_followup.py로 이어서 검증/복구한다.

        DART재무재수집(00:30)보다 먼저 실행 — 신선한 쿼터를 먼저 사용하도록 배치
        (이 잡의 대상 건수가 훨씬 적어 뒤따르는 잡의 쿼터를 크게 잠식하지 않음).
        DART 한도 소진(status=020) 감지 시 즉시 안전 종료, 다음날 자동 재개.
        """
        logger.info("[데이터무결성후속검증] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = now.replace(hour=0, minute=5, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("데이터무결성후속검증", self._job_data_integrity_followup)
        logger.info("[데이터무결성후속검증] 루프 종료")

    def _job_data_integrity_followup(self) -> None:
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/data_integrity_followup.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=1800,  # 30분
            )
            if result.returncode == 0:
                logger.info("[데이터무결성후속검증] 완료: %s", (result.stdout or "")[-800:])
            else:
                logger.warning(
                    "[데이터무결성후속검증] 비정상 종료 (code=%s): %s",
                    result.returncode, (result.stderr or "")[-800:],
                )
        except subprocess.TimeoutExpired:
            logger.warning("[데이터무결성후속검증] 30분 타임아웃 — 내일 재시도")
        except Exception as e:
            logger.error(f"[데이터무결성후속검증] 오류: {e}", exc_info=True)

    def _loop_multi_source_financial_crosscheck(self) -> None:
        """매일 00:25 — scripts/multi_source_financial_crosscheck.py 실행.

        2026-08-26 사용자 지시: "재무데이터 뿐만 아니라 현금흐름표/매입재료비/감가상각비/
        연결·별도기준 등 모든 데이터에 대해 DART뿐만 아니라 야후/네이버/FnGuide와도
        중복·다중 점검을 해야한다." DART API 쿼터를 전혀 쓰지 않음(FnGuide는 스크레이핑,
        Naver는 기존 naver_financial 테이블 재사용, Yahoo는 yfinance) — 다른 DART 잡과
        경합하지 않으므로 별도 시간대 배치. 결과는 multi_source_financial_mismatch_log에
        누적되며, DB 값은 자동수정하지 않고 판정만 기록(재무 무결성 선행규칙 준수 —
        외부소스 불일치가 곧 DART 오류를 의미하지 않음, 분류기준 차이일 수 있어 원문
        재확인 없이는 자동교정 금지).
        """
        logger.info("[다중소스재무교차검증] 루프 시작")
        self._wait_secs(60)
        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = now.replace(hour=0, minute=25, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("다중소스재무교차검증", self._job_multi_source_financial_crosscheck)
        logger.info("[다중소스재무교차검증] 루프 종료")

    def _job_multi_source_financial_crosscheck(self) -> None:
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/multi_source_financial_crosscheck.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=2400,  # 40분 (yfinance/FnGuide 스크레이핑 네트워크 대기 포함)
            )
            if result.returncode == 0:
                logger.info("[다중소스재무교차검증] 완료: %s", (result.stdout or "")[-800:])
            else:
                logger.warning(
                    "[다중소스재무교차검증] 비정상 종료 (code=%s): %s",
                    result.returncode, (result.stderr or "")[-800:],
                )
        except subprocess.TimeoutExpired:
            logger.warning("[다중소스재무교차검증] 40분 타임아웃 — 내일 이어서 진행(미확인 대상만 재조회)")
        except Exception as e:
            logger.error(f"[다중소스재무교차검증] 오류: {e}", exc_info=True)

    def _loop_corporate_action_confirmation_followup(self) -> None:
        """매일 00:10 — scripts/corporate_action_confirmation_followup.py 실행.

        2026-08-23: turnaround/regime_adaptive 등 백테스트 검증을 막던 price_jump_audit
        미해결(21.12~22.10 구간 2,321건)의 79%가 유상증자(rights_issue) 였고, 발행가
        데이터(dilution_events.conversion_price)로 권리락 이론가(TERP) 조정계수를
        계산해 확정하는 작업. dilution_events는 다른 스케줄 작업으로 계속 갱신되므로
        매일 재시도하면 조금씩 review_required가 줄어든다. DART API 미사용(할당량 무관).

        ⚠️ 계수를 실제 backtest 수익률 계산에 적용하는 로직(canonical_price_returns_v
        등)은 이 자동 잡의 범위가 아니다 — 그건 사람이 코드 리뷰하며 별도로 진행.
        """
        logger.info("[기업행위조정계수후속확정] 루프 시작")
        self._wait_secs(90)
        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = now.replace(hour=0, minute=10, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("기업행위조정계수후속확정", self._job_corporate_action_confirmation_followup)
        logger.info("[기업행위조정계수후속확정] 루프 종료")

    def _job_corporate_action_confirmation_followup(self) -> None:
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/corporate_action_confirmation_followup.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=600,  # 10분
            )
            if result.returncode == 0:
                logger.info("[기업행위조정계수후속확정] 완료: %s", (result.stdout or "")[-800:])
            else:
                logger.warning(
                    "[기업행위조정계수후속확정] 비정상 종료 (code=%s): %s",
                    result.returncode, (result.stderr or "")[-800:],
                )
        except subprocess.TimeoutExpired:
            logger.warning("[기업행위조정계수후속확정] 10분 타임아웃 — 내일 재시도")
        except Exception as e:
            logger.error(f"[기업행위조정계수후속확정] 오류: {e}", exc_info=True)

    def _loop_price_jump_audit_rebuild(self) -> None:
        """매일 00:15 — price_jump_audit 전체 재빌드.

        2026-08-24 세션에서 이 테이블이 2026-08-07~08-14 시점 스냅샷에 멈춰 있어
        그 사이 정정된 가격(예: 352770 6,013원→96,645원)이 반영 안 되고 허위
        "가격점프" 오탐을 계속 발생시킨 것을 발견(전체 미해결 5,206→3,002건으로
        재빌드 한 번에 해소). 수동 실행에만 의존하면 재발이 확실해 매일 자동화한다.
        """
        logger.info("[가격점프감사재빌드] 루프 시작")
        self._wait_secs(90)
        while not self._stop_event.is_set():
            self._wait_until(0, 15)
            _run_job_safe("가격점프감사재빌드", self._job_price_jump_audit_rebuild)
        logger.info("[가격점프감사재빌드] 루프 종료")

    def _job_price_jump_audit_rebuild(self) -> None:
        import json
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/audit_price_jumps_and_build_canonical.py", "--require-postgres"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True, text=True, timeout=900,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"price jump audit exited {result.returncode}: "
                    f"{(result.stderr or result.stdout)[-800:]}"
                )
            payload = json.loads(result.stdout)
            if payload.get("database_backend") != "postgresql":
                raise RuntimeError(f"unexpected database backend: {payload.get('database_backend')}")

            from db_utils import connect_stock_db

            conn = connect_stock_db(timeout=30)
            try:
                stored_count, stored_audited_at = conn.execute(
                    "SELECT COUNT(*), MAX(audited_at) FROM price_jump_audit"
                ).fetchone()
            finally:
                conn.close()
            if int(stored_count) != int(payload.get("audited_jumps", -1)):
                raise RuntimeError(
                    f"postcondition count mismatch: output={payload.get('audited_jumps')} "
                    f"postgres={stored_count}"
                )
            if str(stored_audited_at) != str(payload.get("audited_at")):
                raise RuntimeError(
                    f"postcondition timestamp mismatch: output={payload.get('audited_at')} "
                    f"postgres={stored_audited_at}"
                )
            logger.info("[가격점프감사재빌드] 완료: %s", (result.stdout or "")[-700:])
        except subprocess.TimeoutExpired:
            raise RuntimeError("가격점프감사재빌드 15분 타임아웃")
        except Exception as e:
            logger.error(f"[가격점프감사재빌드] 오류: {e}", exc_info=True)
            raise

    def _loop_naver_price_verify(self) -> None:
        """매일 00:20 — 가격점프감사재빌드(00:15) 직후, 신규 플래그된 이벤트만
        Naver 공식 시세와 교차대조(--only-new라 매일 늘어난 분만 확인, 가볍다)."""
        logger.info("[가격외부소스재대조] 루프 시작")
        self._wait_secs(120)
        while not self._stop_event.is_set():
            self._wait_until(0, 20)
            _run_job_safe("가격외부소스재대조", self._job_naver_price_verify)
        logger.info("[가격외부소스재대조] 루프 종료")

    def _job_naver_price_verify(self) -> None:
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/verify_price_history_with_naver.py", "--only-new", "--workers", "6"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode == 0:
                logger.info("[가격외부소스재대조] 완료: %s", (result.stdout or "")[-500:])
            else:
                logger.warning("[가격외부소스재대조] 비정상 종료: %s", (result.stderr or "")[-800:])
        except subprocess.TimeoutExpired:
            logger.warning("[가격외부소스재대조] 30분 타임아웃")
        except Exception as e:
            logger.error(f"[가격외부소스재대조] 오류: {e}", exc_info=True)

    def _loop_weekly_strategy_reverify(self) -> None:
        """매주 일요일 01:30 — 등록된 전략 전량을 최신 가격/데이터로 재실행해
        governance 등급 드리프트를 감지한다.

        2026-08-24 세션에서 1회 수동 실행한 결과 V8이 퇴역→종이운용핵심으로
        승격되고 V10·V12는 검증대기→퇴역으로 정직하게 하향(오래된 가격으로
        부풀려졌던 수치가 정정됨)됨을 확인 — 1회성이 아니라 데이터가 계속
        갱신되므로 정기적으로 재확인해야 함.
        """
        logger.info("[전략센터주간재검증] 루프 시작")
        self._wait_secs(150)
        while not self._stop_event.is_set():
            now = datetime.now()
            days_ahead = (6 - now.weekday()) % 7  # 6=일요일
            next_run = (now + timedelta(days=days_ahead)).replace(hour=1, minute=30, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=7)
            self._wait_secs(max(0.0, (next_run - datetime.now()).total_seconds()))
            if not self._stop_event.is_set():
                _run_job_safe("전략센터주간재검증", self._job_weekly_strategy_reverify)
        logger.info("[전략센터주간재검증] 루프 종료")

    def _job_weekly_strategy_reverify(self) -> None:
        import subprocess, sys
        try:
            result = subprocess.run(
                [sys.executable, "scripts/rerun_all_after_audit_rebuild.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True, text=True, timeout=3600,  # 최대 1시간
            )
            if result.returncode == 0:
                logger.info("[전략센터주간재검증] 완료: %s", (result.stdout or "")[-1000:])
            else:
                logger.warning("[전략센터주간재검증] 비정상 종료: %s", (result.stderr or "")[-1000:])
        except subprocess.TimeoutExpired:
            logger.warning("[전략센터주간재검증] 1시간 타임아웃 — 다음주 재시도")
        except Exception as e:
            logger.error(f"[전략센터주간재검증] 오류: {e}", exc_info=True)

    def _loop_dart_financial_recollect(self) -> None:
        """매일 00:30 — DART API로 2016~현재 재무제표 재수집 (legacy_dart_recollect.py).

        목적: dart_recollect NI NULL 5,228건 점진적 해소 + 신규 공시 자동 반영.
        필터: 상폐(price_history 180일 미거래) · ETF · ETN · 스팩 제외.
        """
        logger.info("[DART재무재수집] 루프 시작")
        self._wait_secs(120)
        while not self._stop_event.is_set():
            now = datetime.now()
            # 매일 00:30 실행
            next_run = now.replace(hour=0, minute=30, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("DART재무재수집", self._job_dart_financial_recollect)
        logger.info("[DART재무재수집] 루프 종료")

    def _job_dart_financial_recollect(self) -> None:
        """DART finstate_all 기반 재무제표 재수집 (배치).

        - 상폐·ETF·ETN·스팩 제외 (legacy_dart_recollect.py active_q 필터)
        - 최근 2년 분기 우선 → 전체 연도 순차 처리
        """
        import subprocess, sys
        try:
            from check_financial_integrity import latest_reported_quarter

            latest_year, latest_quarter = latest_reported_quarter()
            if latest_quarter == 2:
                q2_result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/backfill_dart_q2_financials.py",
                        "--year", str(latest_year),
                        "--report", f"scratch/q2_backfill_{latest_year}.json",
                    ],
                    cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                    capture_output=True,
                    text=True,
                    timeout=4 * 3600,
                )
                if q2_result.returncode != 0:
                    raise RuntimeError(f"Q2 verified backfill failed: {q2_result.stderr[-700:]}")
                logger.info("[DART재무재수집] Q2 검증 수집: %s", q2_result.stdout[-500:])

            script = "/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/legacy_dart_recollect.py"
            cmd = [sys.executable, script, "--resume"]
            logger.info(f"[DART재무재수집] 시작: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=False,
                text=True,
                timeout=4 * 3600,  # 최대 4시간
            )
            if result.returncode == 0:
                logger.info("[DART재무재수집] 완료")
            else:
                logger.warning(f"[DART재무재수집] 비정상 종료 (code={result.returncode})")
        except subprocess.TimeoutExpired:
            logger.warning("[DART재무재수집] 4시간 타임아웃 — 내일 재시도")
        except Exception as e:
            logger.error(f"[DART재무재수집] 오류: {e}", exc_info=True)

    # ── DART CF 위험군 재수집 (매일 01:00 — MISSING_Q123/NULL_Q123_DEPR/MIXED_SOURCE) ──
    def _loop_dart_cf_risk_recollect(self) -> None:
        """매일 01:00 — resolve_quarterly_risks_with_dart.py 실행.

        대상: MISSING_Q123 / NULL_Q123_DEPR / MIXED_SOURCE_ANNUAL_Q 위험군
        - DART재무재수집(00:30) 완료 후 30분 뒤 실행
        - DART 020(일일한도초과) 상태면 즉시 종료 (재시도 내일)
        - 최대 3시간 타임아웃
        """
        logger.info("[DART_CF위험군재수집] 루프 시작")
        self._wait_secs(180)
        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = now.replace(hour=1, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            secs = max(0.0, (next_run - datetime.now()).total_seconds())
            self._wait_secs(secs)
            if not self._stop_event.is_set():
                _run_job_safe("DART_CF위험군재수집", self._job_dart_cf_risk_recollect)
        logger.info("[DART_CF위험군재수집] 루프 종료")

    def _job_dart_cf_risk_recollect(self) -> None:
        """CF MISSING_Q123 / NULL_Q123_DEPR / MIXED_SOURCE_ANNUAL_Q 위험군 DART 재수집."""
        import subprocess, sys
        # DART 키 상태 먼저 확인 — 020이면 건너뜀
        try:
            import urllib.request as _ur, json as _js
            _env = {}
            with open("/Volumes/Realtek_NVME/stock_dashboard/runtime/.env") as _f:
                for _ln in _f:
                    if "=" in _ln and not _ln.startswith("#"):
                        _k, _v = _ln.strip().split("=", 1)
                        _env[_k.strip()] = _v.strip().strip('"').strip("'")
            _key = _env.get("DART_API_KEY", "")
            _url = f"https://opendart.fss.or.kr/api/company.json?crtfc_key={_key}&corp_code=00126380"
            _res = _ur.urlopen(_url, timeout=10)
            _status = _js.loads(_res.read()).get("status", "")
            if _status == "020":
                logger.warning("[DART_CF위험군재수집] DART 한도초과(020) — 오늘 건너뜀")
                return
        except Exception as _e:
            logger.warning(f"[DART_CF위험군재수집] DART 상태 확인 실패: {_e}")

        try:
            script = "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/resolve_quarterly_risks_with_dart.py"
            cmd = [
                sys.executable, script,
                "--year-from", "2020",
                "--year-to", "2026",
                "--limit", "200",   # 1회 최대 200건 (한도 절약)
            ]
            logger.info(f"[DART_CF위험군재수집] 시작: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=False,
                text=True,
                timeout=3 * 3600,  # 최대 3시간
            )
            if result.returncode == 0:
                logger.info("[DART_CF위험군재수집] 완료")
            else:
                logger.warning(f"[DART_CF위험군재수집] 비정상 종료 (code={result.returncode})")
        except subprocess.TimeoutExpired:
            logger.warning("[DART_CF위험군재수집] 3시간 타임아웃 — 내일 재시도")
        except Exception as e:
            logger.error(f"[DART_CF위험군재수집] 오류: {e}", exc_info=True)

    # ── DB 유지보수 (매주 일요일 04:00) ─────────────────────────────────────────
    def _loop_db_maintenance(self) -> None:
        """매주 일요일 04:00 — WAL checkpoint + VACUUM + ANALYZE (freelist/WAL 정리)."""
        logger.info("[DB유지보수] 루프 시작")
        self._wait_secs(180)  # 서버 초기화 완료 대기
        while not self._stop_event.is_set():
            import datetime as _dt
            now = _dt.datetime.now()
            # 다음 일요일 04:00 계산
            days_to_sunday = (6 - now.weekday()) % 7 or 7
            next_run = (now + _dt.timedelta(days=days_to_sunday)).replace(
                hour=4, minute=0, second=0, microsecond=0
            )
            wait_secs = max(0, (next_run - now).total_seconds())
            self._stop_event.wait(timeout=wait_secs)
            if self._stop_event.is_set():
                break
            _run_job_safe("DB유지보수", self._job_db_maintenance)
        logger.info("[DB유지보수] 루프 종료")

    def _job_db_maintenance(self) -> None:
        """WAL checkpoint(TRUNCATE) + VACUUM + ANALYZE — stock.db / hs_trade_lab.db.

        2026-08-23: stock.db는 IS_POSTGRES 상태에서 sqlite3.connect()가 라우터를 통해
        실제로 PostgreSQL에 연결되므로, SQLite 전용 PRAGMA(freelist_count/wal_checkpoint)와
        VACUUM(인자 없는 SQLite 전체 VACUUM)이 매주 "syntax error at or near PRAGMA"로
        실패하고 있었음. stock.db는 PostgreSQL 자체 VACUUM ANALYZE로 대체하고,
        hs_trade_lab.db는 router 미적용 독립 SQLite 파일이므로 기존 방식 유지.
        """
        import time as _time
        from config import IS_POSTGRES

        t0 = _time.time()
        try:
            if IS_POSTGRES:
                import psycopg

                with psycopg.connect(config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as pconn:
                    pconn.execute("VACUUM ANALYZE")
                elapsed = _time.time() - t0
                logger.info(f"[DB유지보수] stock.db(PostgreSQL VACUUM ANALYZE) 완료 ({elapsed:.1f}s)")
            else:
                import sqlite3 as _sl
                conn = _sl.connect("stock.db", timeout=120)
                fl_before = conn.execute("PRAGMA freelist_count").fetchone()[0]
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
                fl_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
                conn.close()
                elapsed = _time.time() - t0
                logger.info(
                    f"[DB유지보수] stock.db 완료 ({elapsed:.1f}s) freelist: {fl_before} → {fl_after}"
                )
        except Exception as e:
            logger.error(f"[DB유지보수] stock.db 오류: {e}", exc_info=True)

        import sqlite3 as _sl2
        try:
            t0 = _time.time()
            conn = _sl2.connect("hs_trade_lab/data/hs_trade_lab.db", timeout=120)
            fl_before = conn.execute("PRAGMA freelist_count").fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            fl_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
            conn.close()
            elapsed = _time.time() - t0
            logger.info(
                f"[DB유지보수] hs_trade_lab.db 완료 ({elapsed:.1f}s) freelist: {fl_before} → {fl_after}"
            )
        except Exception as e:
            logger.error(f"[DB유지보수] hs_trade_lab.db 오류: {e}", exc_info=True)

    # ── WAL 일별 크기 감시 (매일 04:30) ─────────────────────────────────────────
    def _loop_wal_daily_check(self) -> None:
        """매일 04:30 — WAL 파일 100MB 초과 시 TRUNCATE checkpoint. VACUUM 없이 빠르게."""
        logger.info("[WAL일별체크] 루프 시작")
        self._wait_secs(120)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_seconds_until(4, 30))
            if self._stop_event.is_set():
                break
            _run_job_safe("WAL일별체크", self._job_wal_daily_check)

    def _job_wal_daily_check(self) -> None:
        """WAL 100MB 초과 시에만 TRUNCATE checkpoint — hs_trade_lab + stock.db."""
        import sqlite3 as _sl, os as _os
        THRESHOLD_BYTES = 100 * 1024 * 1024  # 100MB
        targets = [
            ("stock.db",        "stock.db"),
            ("hs_trade_lab.db", "hs_trade_lab/data/hs_trade_lab.db"),
        ]
        for label, path in targets:
            wal_path = path + "-wal"
            try:
                wal_size = _os.path.getsize(wal_path) if _os.path.exists(wal_path) else 0
                if wal_size < THRESHOLD_BYTES:
                    continue  # 100MB 미만이면 스킵
                logger.info(f"[WAL일별체크] {label} WAL={wal_size//1024//1024}MB → checkpoint 실행")
                conn = _sl.connect(path, timeout=30)
                result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                conn.close()
                wal_after = _os.path.getsize(wal_path) if _os.path.exists(wal_path) else 0
                logger.info(
                    f"[WAL일별체크] {label} 완료: "
                    f"{wal_size//1024//1024}MB → {wal_after//1024//1024}MB"
                    f" (blocked={result[0]}, frames={result[1]})"
                )
            except Exception as e:
                logger.error(f"[WAL일별체크] {label} 오류: {e}")

    def _loop_page_data_audit(self) -> None:
        """매일 06:40 — 전체 페이지 데이터 수집/신선도 감사 리포트 생성."""
        logger.info("[페이지데이터감사] 루프 시작")
        self._wait_secs(150)
        while not self._stop_event.is_set():
            self._wait_until(6, 40, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("페이지데이터감사", self._job_page_data_audit)
        logger.info("[페이지데이터감사] 루프 종료")

    def _job_page_data_audit(self) -> None:
        """scripts/audit_all_page_data_quality.py 실행."""
        try:
            result = subprocess.run(
                [sys.executable, "scripts/audit_all_page_data_quality.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=1800,
            )
            logger.info(f"[페이지데이터감사] 완료: returncode={result.returncode} stdout={result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.warning(f"[페이지데이터감사] stderr: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            logger.warning("[페이지데이터감사] 30분 타임아웃")
        except Exception as e:
            logger.error(f"[페이지데이터감사] 오류: {e}", exc_info=True)

    # ── 턴어라운드워치 사전계산 (매일 04:40, CPU 유휴시간대) ──────────────────
    def _loop_turnaround_watch_precompute(self) -> None:
        """매일 04:40 — /api/tenbagger/turnaround-watch 무거운 전종목 스캔을 미리 계산해 캐싱.
        2026-07-26 신규: 이 API가 요청마다 재계산되어 CPU를 과점유하는 문제(사용자 리포트)를
        해결하기 위해, 요청 시점 계산을 없애고 새벽 유휴시간에만 1회 계산하도록 전환.
        04:40 선택 이유: 00:30 DART재무재수집(최대 4시간)과 04:30 WAL일별체크가 보통 끝난
        직후이고, 05:00 FnGuide재무월간(매월 3일만)·05:30 CF3중검증 시작 전이라 이 시간대가
        상대적으로 가장 한가함(CLAUDE.md 야간배치 소요시간 실측 기록 참조)."""
        logger.info("[턴어라운드워치사전계산] 루프 시작")
        self._wait_secs(180)
        while not self._stop_event.is_set():
            self._wait_until(4, 40, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("턴어라운드워치사전계산", self._job_turnaround_watch_precompute)
        logger.info("[턴어라운드워치사전계산] 루프 종료")

    def _job_turnaround_watch_precompute(self) -> None:
        try:
            from routes.tenbagger import refresh_turnaround_watch_cache
            data = refresh_turnaround_watch_cache(min_mktcap=300.0)
            counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
            logger.info(f"[턴어라운드워치사전계산] 완료: {counts}")
        except Exception as e:
            logger.error(f"[턴어라운드워치사전계산] 오류: {e}", exc_info=True)

    # ── 투자 의사결정 RAG (평일 20:05, DeepSeek 비피크) ─────────────────────
    def _loop_investment_decision_rag(self) -> None:
        """평일 20:05에 피크 시간에 접수된 RAG 작업을 자동 재개한다."""
        logger.info("[투자의사결정RAG] 루프 시작")
        self._wait_secs(210)
        while not self._stop_event.is_set():
            self._wait_until(20, 5, skip_weekend=False)
            if self._stop_event.is_set(): break
            _run_job_safe("투자의사결정RAG", self._job_investment_decision_rag)
        logger.info("[투자의사결정RAG] 루프 종료")

    def _job_investment_decision_rag(self) -> None:
        try:
            from routes.investment_decisions import resume_waiting_tasks
            resumed=resume_waiting_tasks()
            logger.info(f"[투자의사결정RAG] 대기 작업 {resumed}건 재개")
        except Exception as e:
            logger.error(f"[투자의사결정RAG] 오류: {e}", exc_info=True)

    # ── 체리형부식 스크리너 사전계산 (매일 04:45) ──────────────────────────────
    def _loop_cherry_screener_precompute(self) -> None:
        """2026-08-09 신규 — 체리형부 채널(수집 텍스트 3,657건+파일 491건) 원문 리포트를
        역설계한 '3대 스크리닝' 전종목 스캔. 연산 자체는 가벼움(전종목 1초 내)이나
        턴어라운드워치와 동일한 유휴시간대 사전계산 관례를 따라 04:40 직후(04:45)로 배치."""
        logger.info("[체리형부스크리너사전계산] 루프 시작")
        self._wait_secs(200)
        while not self._stop_event.is_set():
            self._wait_until(4, 45, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("체리형부스크리너사전계산", self._job_cherry_screener_precompute)
        logger.info("[체리형부스크리너사전계산] 루프 종료")

    def _job_cherry_screener_precompute(self) -> None:
        try:
            from routes.cherry_screener import refresh_cherry_screener_cache
            data = refresh_cherry_screener_cache()
            logger.info(
                f"[체리형부스크리너사전계산] 완료: 스캔 {data['universe_scanned']}종목, "
                f"3스크린 {len(data['three_screen_pass'])}건, 2스크린 {len(data['two_screen_pass'])}건")
        except Exception as e:
            logger.error(f"[체리형부스크리너사전계산] 오류: {e}", exc_info=True)

    # ── FnGuide/DART 전종목 순차 교차검증 (매일 03:15, FNGUIDE 일일한도 준수) ────
    def _loop_fnguide_dart_verify_sweep(self) -> None:
        """2026-08-09(2) 신규 — financial_source_snapshot(FnGuide) vs financial_data(DART)
        연간 P&L/BS 교차검증(cross_validate_annual)을 전종목(~2,585개, 우선주 제외)에 대해
        FNGUIDE 일일한도(1,500건/일, annual_only 모드는 종목당 3건 소비) 내에서 순차 실행.
        "오늘 이미 처리한 종목"은 자동 skip하므로 매일 450종목씩만 처리해도 날짜가 바뀌면
        이어서 다음 종목으로 진행 — 약 6일에 한 바퀴(2,585/450) 순환한다. 03:15 선택 이유:
        cf_triple_validator(현금흐름 3중검증, 05:30)·FnGuide재무월간(매월3일 05:00)·
        턴어라운드워치(04:40)·체리형부스크리너(04:45)와 겹치지 않는 새벽 첫 유휴 슬롯이며,
        450종목×3건×3초(FNGUIDE min_interval)≈68분이라 04:40 이전에 여유있게 종료된다.
        이 검증은 2026-08-08 이전에는 cross_validate_annual() 자체가 설정 import 버그로
        한 번도 정상 실행된 적이 없었던 것을 이번 세션에서 발견·수정한 뒤 신설한 것 —
        CLAUDE.md의 "Codex 검증(500종목)" 기록(2026-05-16)은 이 상시 잡과 무관한 별도의
        1회성 스팟체크였다."""
        logger.info("[FnGuideDART전종목검증] 루프 시작")
        self._wait_secs(150)
        while not self._stop_event.is_set():
            self._wait_until(3, 15, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("FnGuideDART전종목검증", self._job_fnguide_dart_verify_sweep)
        logger.info("[FnGuideDART전종목검증] 루프 종료")

    def _job_fnguide_dart_verify_sweep(self) -> None:
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _root = str(_Path(__file__).resolve().parent)
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from scripts.verify_all_fnguide_dart_20260809 import run_verify_sweep
            result = run_verify_sweep(limit=450)
            stats = result["stats"]
            logger.info(
                f"[FnGuideDART전종목검증] 완료: 대상 {result['target_count']}종목, {stats}, "
                f"불일치 {len(result['mismatches'])}건")
            if result["mismatches"]:
                self._record_fnguide_dart_mismatches(result["mismatches"])
        except Exception as e:
            logger.error(f"[FnGuideDART전종목검증] 오류: {e}", exc_info=True)

    def _record_fnguide_dart_mismatches(self, mismatches: list) -> None:
        """발견된 불일치를 fnguide_dart_mismatch_log에 누적 기록 — 계정 오매칭 재발 여부를
        추적하고, 이후 systematic 재감사(어떤 필드가 가장 자주 걸리는지) 근거로 사용."""
        try:
            import sqlite3 as _sl
            import re as _re
            from datetime import datetime as _dt, timezone as _tz
            conn = _sl.connect(str(Path(__file__).resolve().parent / "stock.db"), timeout=30)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fnguide_dart_mismatch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    field TEXT,
                    note TEXT,
                    found_at TEXT NOT NULL
                )
            """)
            now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for m in mismatches:
                # 2026-08-12 수정: note가 "DART불일치: net_income: FnG=..." 형태로 "DART불일치: "
                # 접두어 뒤에 필드명이 오는데, 기존 ^([a-z_]+): 는 문자열 맨 앞(한글 접두어)에서만
                # 찾아 전혀 매칭이 안 되고 있었음(165건 전부 field=NULL로 저장된 채 방치됨 확인)
                # — 접두어와 무관하게 " {필드명}: FnG=" 패턴을 문자열 어디서든 검색하도록 수정.
                field_match = _re.search(r"([a-z_]+):\s*FnG=", m.get("note") or "")
                field = field_match.group(1) if field_match else None
                conn.execute(
                    "INSERT INTO fnguide_dart_mismatch_log (stock_code, year, field, note, found_at) "
                    "VALUES (?,?,?,?,?)",
                    (m["code"], m["year"], field, m["note"], now_iso),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[FnGuideDART전종목검증] 불일치 로그 기록 실패: {e}")

    # ── financial_source_snapshot 미검증 백로그 정리 (매일 03:45) ────────────
    def _loop_unverified_snapshot_backfill(self) -> None:
        """2026-08-28 신설 — cross_validate_annual()이 DART 쿼터체크 실패 시 재시도 없이
        'unverified'로 끝내버려(근본원인 자체는 이번 세션에서 미수정) 매일 신규 수집분의
        90%(실측 136,441/150,261)가 검증 안 된 채 영구 방치되던 문제의 후속 대응.
        FnGuide 원본은 financial_source_snapshot에 이미 저장돼 있으므로 재수집 없이
        DART 쪽만 다시 시도(scripts/backfill_unverified_snapshot.py) — FnGuide 일일한도와
        무관하게 DART 쿼터만 소비. 03:15 FnGuideDART전종목검증(신규 스냅샷 생성, ~68분)
        직후 30분 버퍼를 두고 03:45 실행, 04:40 턴어라운드워치 전에 여유있게 종료되도록
        limit=1000(약 15~20분 예상, DART min_interval 0.8s 기준)."""
        logger.info("[미검증스냅샷백필] 루프 시작")
        self._wait_secs(150)
        while not self._stop_event.is_set():
            self._wait_until(3, 45, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("미검증스냅샷백필", self._job_unverified_snapshot_backfill)
        logger.info("[미검증스냅샷백필] 루프 종료")

    def _job_unverified_snapshot_backfill(self) -> None:
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _root = str(_Path(__file__).resolve().parent)
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from scripts.backfill_unverified_snapshot import run_backfill
            result = run_backfill(limit=1000)
            logger.info(f"[미검증스냅샷백필] 완료: {result}")
        except Exception as e:
            logger.error(f"[미검증스냅샷백필] 오류: {e}", exc_info=True)

    # ── 분기실적 TTM 신호 스캔 (매일 06:00, 분기시즌 추가) ──────────────────────
    def _loop_earnings_signal_scan(self) -> None:
        """
        매일 06:00 TTM 신호 스캔 (최근 30일 업데이트 종목).
        분기보고서 시즌(3/5/8/11월)에는 추가로 2시간마다 증분 스캔.
        """
        logger.info("[실적신호스캔] 루프 시작")
        self._wait_secs(120)
        import datetime as _dt
        while not self._stop_event.is_set():
            now = _dt.datetime.now()
            month = now.month
            # 분기보고서 시즌: 3, 5, 8, 11월
            is_season = month in (3, 5, 8, 11)
            if is_season and 6 <= now.hour <= 20:
                # 시즌 중 낮 시간대: 2시간마다 증분 스캔
                self._stop_event.wait(timeout=7200)
                if self._stop_event.is_set():
                    break
                _run_job_safe("실적신호스캔", lambda: self._job_earnings_scan(days_back=3))
            else:
                # 평상시: 매일 06:00 스캔
                self._stop_event.wait(timeout=_seconds_until(6, 0))
                if self._stop_event.is_set():
                    break
                _run_job_safe("실적신호스캔", lambda: self._job_earnings_scan(days_back=30))

    def _job_earnings_scan(self, days_back: int = 30) -> None:
        """TTM 신호 실제 스캔 + 신규 신호 텔레그램 발송"""
        import json
        try:
            from collectors.earnings_signal_detector import run_full_scan, SIGNAL_TYPES
            result = run_full_scan(days_back=days_back, min_mktcap_억=100)
            logger.info(f"[실적신호스캔] 완료: {result}")

            if result.get("new_signals", 0) > 0:
                self._send_earnings_signal_telegram(result, days_back)
        except Exception as e:
            logger.error(f"[실적신호스캔] 오류: {e}", exc_info=True)

    def _send_earnings_signal_telegram(self, result: dict, days_back: int) -> None:
        """신규 TTM 신호를 텔레그램으로 발송"""
        import sqlite3 as _sl
        try:
            conn = connect_stock_db(timeout=30, row_factory=_sl.Row)
            import datetime as _dt
            cutoff = (_dt.datetime.now() - _dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute("""
                SELECT es.stock_code, es.stock_name, es.signal_type, es.detail,
                       es.year, es.quarter, ph.close as price
                FROM earnings_signals es
                LEFT JOIN (
                    SELECT stock_code, close FROM price_history p1
                    WHERE date=(SELECT MAX(date) FROM price_history p2
                               WHERE p2.stock_code=p1.stock_code AND p2.close>0)
                ) ph ON es.stock_code=ph.stock_code
                WHERE es.created_at >= ? AND es.telegram_sent=0
                ORDER BY CASE es.signal_type
                    WHEN 'TTM_BOTH' THEN 0 WHEN 'TTM_OP_INFLECT' THEN 1
                    WHEN 'TTM_REV_30' THEN 2 ELSE 3 END
                LIMIT 10
            """, (cutoff,)).fetchall()
            conn.close()

            if not rows:
                return

            from collectors.earnings_signal_detector import SIGNAL_TYPES
            lines = ["🔔 <b>분기실적 TTM 신호 감지</b>\n"]
            for r in rows:
                meta = SIGNAL_TYPES.get(r["signal_type"], {})
                emoji = meta.get("emoji", "📊")
                label = meta.get("label", r["signal_type"])
                ratio = meta.get("avg_ratio", 0)
                price_str = f" | {r['price']:,.0f}원" if r["price"] else ""
                lines.append(
                    f"{emoji} <b>{r['stock_name']}({r['stock_code']})</b>\n"
                    f"   {label} | 역사적 평균 <b>{ratio}배</b>\n"
                    f"   {r['detail']}{price_str}\n"
                    f"   {r['year']}년 {r['quarter']}분기\n"
                )

            msg = "\n".join(lines)
            from notifier import send as send_telegram
            if not send_telegram(msg, key=f"earnings_signals_{cutoff[:10]}"):
                logger.warning("[실적신호스캔] 텔레그램 미전송 - 발송완료 표기를 건너뜁니다")
                return

            # telegram_sent 마킹은 공통 발송기가 성공했을 때만 수행한다.
            conn2 = connect_stock_db(timeout=30, row_factory=_sl.Row)
            conn2.execute(
                "UPDATE earnings_signals SET telegram_sent=1 WHERE created_at>=?", (cutoff,)
            )
            conn2.commit()
            conn2.close()
            logger.info(f"[실적신호스캔] 텔레그램 발송 {len(rows)}건")
        except Exception as e:
            logger.error(f"[실적신호스캔] 텔레그램 오류: {e}")

    # ── 키움 투자자 수급 (매일 19:00) ──────────────────────────────────────────
    def _loop_kiwoom_investor_daily(self) -> None:
        """매일 19:00 — 키움 ka10059 종목별 투자자 일별 수급 수집."""
        logger.info("[키움투자자수급] 루프 시작")
        self._wait_secs(300)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_seconds_until(19, 0, skip_weekend=True))
            if self._stop_event.is_set():
                break
            _run_job_safe("키움투자자수급", self._job_kiwoom_investor_daily)

    def _job_kiwoom_investor_daily(self) -> None:
        """키움 ka10059: listed-stock investor flow collection."""
        try:
            from collectors.kiwoom_collector import KiwoomCollector
            kc = KiwoomCollector()
            if not kc.is_configured():
                logger.info("[키움투자자수급] 미설정 — 건너뜀")
                return
            result = kc.bulk_investor_collect(
                limit=3000,
                max_pages=3,
                sleep_secs=0.2,
                skip_existing_latest=True,
            )
            logger.info(
                f"[키움투자자수급] 완료 — "
                f"updated={result.get('updated',0)} failed={result.get('failed',0)} "
                f"saved={result.get('total_saved',0):,}행 "
                f"target_dt={result.get('target_dt')} skipped_existing={result.get('skipped_existing',0)}"
            )
        except Exception as e:
            logger.error(f"[키움투자자수급] 오류: {e}", exc_info=True)

    # ── 키움 종목기본정보 PER/PBR/유동주식수 (매주 월요일 06:30) ──────────────
    def _loop_kiwoom_stock_universe(self) -> None:
        """매주 월요일 06:30 — 키움 ka10001 전종목 PER/PBR/ROE/유동주식수 갱신."""
        logger.info("[키움종목기본정보] 루프 시작")
        self._wait_secs(300)
        while not self._stop_event.is_set():
            import datetime as _dt
            now = _dt.datetime.now()
            days_to_monday = (0 - now.weekday()) % 7 or 7
            next_run = (now + _dt.timedelta(days=days_to_monday)).replace(
                hour=6, minute=30, second=0, microsecond=0
            )
            wait_secs = max(0, (next_run - now).total_seconds())
            self._stop_event.wait(timeout=wait_secs)
            if self._stop_event.is_set():
                break
            _run_job_safe("키움종목기본정보", self._job_kiwoom_stock_universe)

    def _job_kiwoom_stock_universe(self) -> None:
        """키움 ka10001: 전종목 PER/PBR/ROE/유동주식수 갱신."""
        try:
            from collectors.kiwoom_collector import KiwoomCollector
            kc = KiwoomCollector()
            if not kc.is_configured():
                logger.info("[키움종목기본정보] 미설정 — 건너뜀")
                return
            result = kc.bulk_update_stock_universe(limit=3945, sleep_secs=0.3)
            logger.info(
                f"[키움종목기본정보] 완료 — "
                f"updated={result.get('updated',0)} failed={result.get('failed',0)}"
            )
        except Exception as e:
            logger.error(f"[키움종목기본정보] 오류: {e}", exc_info=True)

    # ── 텐버거 위클리 리포트 ─────────────────────────────────────────
    def _loop_tenbagger_weekly(self) -> None:
        """매주 월요일 07:30 텐버거 위클리 리포트 생성 + 텔레그램 발송."""
        logger.info("[텐버거위클리] 루프 시작")
        while not self._stop_event.is_set():
            now = datetime.now()
            # 매주 월요일 07:30
            days_until_monday = (7 - now.weekday()) % 7  # 0=월
            if days_until_monday == 0 and now.hour < 7 or (now.hour == 7 and now.minute < 30):
                days_until_monday = 0
            elif days_until_monday == 0:
                days_until_monday = 7
            target = (now + timedelta(days=days_until_monday)).replace(
                hour=7, minute=30, second=0, microsecond=0)
            if now.weekday() == 0 and now.hour == 7 and now.minute < 30:
                target = now.replace(hour=7, minute=30, second=0, microsecond=0)
            wait = max(0.0, (target - datetime.now()).total_seconds())
            if self._stop_event.wait(wait):
                break
            _run_job_safe("텐버거위클리", self._job_tenbagger_weekly)

    def _job_tenbagger_weekly(self) -> None:
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/tenbagger_weekly_report.py"],
                capture_output=True, text=True, timeout=120
            )
            logger.info(f"[텐버거위클리] 완료: {result.stdout[-200:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[텐버거위클리] 오류: {result.stderr[-200:]}")
        except Exception as e:
            logger.error(f"[텐버거위클리] 오류: {e}", exc_info=True)

    # ── 지표상회 카페 시그널 구조화 ────────────────────────────────
    def _loop_cafe_signal_weekly(self) -> None:
        """매주 월요일 07:10 카페 글을 종목/섹터/지표 시그널로 구조화."""
        logger.info("[카페시그널주간] 루프 시작")
        while not self._stop_event.is_set():
            now = datetime.now()
            days_until_monday = (7 - now.weekday()) % 7
            target = (now + timedelta(days=days_until_monday)).replace(
                hour=7, minute=10, second=0, microsecond=0)
            if days_until_monday == 0 and now >= target:
                target += timedelta(days=7)
            wait = max(0.0, (target - datetime.now()).total_seconds())
            if self._stop_event.wait(wait):
                break
            _run_job_safe("카페시그널주간", lambda: self._job_cafe_signal("weekly", max_pages=4))

    def _loop_cafe_signal_monthly(self) -> None:
        """매월 1일 07:15 카페 월간 시그널 요약 생성."""
        logger.info("[카페시그널월간] 루프 시작")
        while not self._stop_event.is_set():
            now = datetime.now()
            if now.day == 1 and now.hour < 7:
                target = now.replace(hour=7, minute=15, second=0, microsecond=0)
            elif now.day == 1 and now.hour == 7 and now.minute < 15:
                target = now.replace(hour=7, minute=15, second=0, microsecond=0)
            else:
                year = now.year + (1 if now.month == 12 else 0)
                month = 1 if now.month == 12 else now.month + 1
                target = now.replace(year=year, month=month, day=1, hour=7, minute=15, second=0, microsecond=0)
            wait = max(0.0, (target - datetime.now()).total_seconds())
            if self._stop_event.wait(wait):
                break
            _run_job_safe("카페시그널월간", lambda: self._job_cafe_signal("monthly", max_pages=8))

    def _job_cafe_signal(self, run_type: str = "weekly", max_pages: int = 4) -> None:
        try:
            import subprocess
            script = "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/naver_cafe_signal_pipeline.py"
            result = subprocess.run(
                [sys.executable, script, "--collect", "--max-pages", str(max_pages), "--run-type", run_type],
                capture_output=True, text=True, timeout=900,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] {run_type} 완료: {result.stdout[-300:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[카페시그널] {run_type} 오류: {result.stderr[-400:]}")
            leadership = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/generate_cafe_monthly_leadership.py"],
                capture_output=True, text=True, timeout=300,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 월별 주도섹터/HS 갱신: {leadership.stdout[-300:] if leadership.stdout else ''}")
            if leadership.returncode != 0:
                logger.error(f"[카페시그널] 월별 리더십 오류: {leadership.stderr[-400:]}")
            housing = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/collect_molit_housing_starts.py"],
                capture_output=True, text=True, timeout=120,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 국토부 주택건설실적 갱신: {housing.stdout[-300:] if housing.stdout else ''}")
            if housing.returncode != 0:
                logger.error(f"[카페시그널] 국토부 주택건설실적 오류: {housing.stderr[-400:]}")
            steam = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/collect_steam_listed_game_activity.py"],
                capture_output=True, text=True, timeout=120,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 상장 게임 Steam 동접 갱신: {steam.stdout[-300:] if steam.stdout else ''}")
            if steam.returncode != 0:
                logger.error(f"[카페시그널] 상장 게임 Steam 동접 오류: {steam.stderr[-400:]}")
            bridges = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/sync_cafe_existing_series_bridges.py"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 기존 시계열 브리지 갱신: {bridges.stdout[-300:] if bridges.stdout else ''}")
            if bridges.returncode != 0:
                logger.error(f"[카페시그널] 기존 시계열 브리지 오류: {bridges.stderr[-400:]}")
            mappings = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/sync_cafe_quant_mappings.py"],
                capture_output=True, text=True, timeout=120,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 퀀트지표 매핑 갱신: {mappings.stdout[-300:] if mappings.stdout else ''}")
            if mappings.returncode != 0:
                logger.error(f"[카페시그널] 퀀트지표 매핑 오류: {mappings.stderr[-400:]}")
            stock_mappings = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/sync_cafe_stock_indicator_mappings.py"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 종목별 지표 매핑 갱신: {stock_mappings.stdout[-300:] if stock_mappings.stdout else ''}")
            if stock_mappings.returncode != 0:
                logger.error(f"[카페시그널] 종목별 지표 매핑 오류: {stock_mappings.stderr[-400:]}")
            indicator_signals = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/quant_indicator_signal_engine.py", "--send-telegram"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[카페시그널] 지표 이상치 매수 후보 계산: {indicator_signals.stdout[-300:] if indicator_signals.stdout else ''}")
            if indicator_signals.returncode != 0:
                logger.error(f"[카페시그널] 지표 이상치 계산 오류: {indicator_signals.stderr[-400:]}")
        except Exception as e:
            logger.error(f"[카페시그널] {run_type} 오류: {e}", exc_info=True)

    # ── 퀀트 지표 이상치 → 관련 종목 매수 후보 ────────────────────────
    def _loop_quant_indicator_signal(self) -> None:
        """매일 07:40 지표 이상치 기반 관련 종목 후보를 계산하고 텔레그램으로 알림."""
        logger.info("[퀀트지표트리거] 루프 시작")
        while not self._stop_event.is_set():
            wait = _seconds_until(7, 40, skip_weekend=False)
            if self._stop_event.wait(wait):
                break
            _run_job_safe("퀀트지표트리거", self._job_quant_indicator_signal)

    def _job_quant_indicator_signal(self) -> None:
        try:
            import subprocess
            housing = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/collect_molit_housing_starts.py"],
                capture_output=True, text=True, timeout=120,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 국토부 주택건설실적 갱신: {housing.stdout[-300:] if housing.stdout else ''}")
            if housing.returncode != 0:
                logger.error(f"[퀀트지표트리거] 국토부 주택건설실적 오류: {housing.stderr[-400:]}")
            steam = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/collect_steam_listed_game_activity.py"],
                capture_output=True, text=True, timeout=120,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 상장 게임 Steam 동접 갱신: {steam.stdout[-300:] if steam.stdout else ''}")
            if steam.returncode != 0:
                logger.error(f"[퀀트지표트리거] 상장 게임 Steam 동접 오류: {steam.stderr[-400:]}")
            bridges = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/sync_cafe_existing_series_bridges.py"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 기존 시계열 브리지 갱신: {bridges.stdout[-300:] if bridges.stdout else ''}")
            if bridges.returncode != 0:
                logger.error(f"[퀀트지표트리거] 기존 시계열 브리지 오류: {bridges.stderr[-400:]}")
            result = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/sync_cafe_stock_indicator_mappings.py"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 종목별 지표 매핑 갱신: {result.stdout[-300:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[퀀트지표트리거] 종목별 지표 매핑 오류: {result.stderr[-400:]}")
            signals = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/quant_indicator_signal_engine.py", "--send-telegram"],
                capture_output=True, text=True, timeout=180,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 완료: {signals.stdout[-300:] if signals.stdout else ''}")
            if signals.returncode != 0:
                logger.error(f"[퀀트지표트리거] 오류: {signals.stderr[-400:]}")
            snapshots = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/snapshot_quant_stock_trade_signals.py"],
                capture_output=True, text=True, timeout=240,
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
            )
            logger.info(f"[퀀트지표트리거] 종목 매매 시그널 스냅샷: {snapshots.stdout[-300:] if snapshots.stdout else ''}")
            if snapshots.returncode != 0:
                logger.error(f"[퀀트지표트리거] 종목 매매 시그널 스냅샷 오류: {snapshots.stderr[-400:]}")
        except Exception as e:
            logger.error(f"[퀀트지표트리거] 오류: {e}", exc_info=True)

    # 2026-08-11 제거: _job_quant_major_indicator_daily가 crontab의
    # "30 19 * * 1-5 ... quant_indicators_cron.py --mode daily"와 5분 차이로
    # 완전히 같은 작업을 중복 실행하고 있었다. CLAUDE.md의 퀀트지표 크론 설계는
    # "FastAPI 서버와 완전히 분리된 별도 프로세스로만 실행(scheduler.py 내부 X)"을
    # 명시적 리스크회피 설계로 문서화하고 있고, weekly/monthly/annual 모드는
    # scheduler.py에 대응 항목이 없어 daily만 뒤늦게 잘못 추가된 것으로 판단됨.
    # crontab 쪽(문서화된 정식 경로)만 남기고 이 중복 트리거는 삭제.

    def _loop_macro_indicator_backtest(self) -> None:
        """매주 월요일 07:50 — 거시지표×섹터 후보 백테스트 후 검증 통과 조합 승격."""
        logger.info("[거시지표백테스트] 루프 시작")
        while not self._stop_event.is_set():
            wait = _seconds_until(7, 50, skip_weekend=False)
            if self._stop_event.wait(wait):
                break
            if datetime.now().weekday() != 0:
                self._wait_secs(23 * 3600)
                continue
            _run_job_safe("거시지표백테스트", self._job_macro_indicator_backtest)

    def _job_macro_indicator_backtest(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/ops/backtest_macro_indicator_candidates.py",
                "--promote",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
        )
        logger.info(f"[거시지표백테스트] 완료: {result.stdout[-800:] if result.stdout else ''}")
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "macro indicator backtest failed")[-800:])

    def _loop_cherry_latest_channel(self) -> None:
        """매일 08:45 — @Brianlee4 세션으로 최신 체리형부 채널 증분 수집."""
        logger.info("[체리형부최신채널] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(8, 45, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("체리형부최신채널", self._job_cherry_latest_channel)

    def _job_cherry_latest_channel(self) -> None:
        try:
            # sys.executable(venv python)을 써야 PYTHONPATH의 postgres 라우터가
            # 적용된다. 과거 하드코딩된 /opt/homebrew/bin/python3.14는 psycopg/
            # sqlalchemy가 없어 라우터를 못 태우고 stock.db(SQLite)에만 직접
            # 써서 PostgreSQL과 조용히 드리프트를 일으켰다(2026-08-11 발견).
            result = subprocess.run(
                [sys.executable, "scripts/ops/collect_cherry_latest_channel.py"],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=1800,
            )
            logger.info(f"[체리형부최신채널] 완료: returncode={result.returncode} stdout={result.stdout[-500:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[체리형부최신채널] 오류: {result.stderr[-700:] if result.stderr else ''}")
        except subprocess.TimeoutExpired:
            logger.error("[체리형부최신채널] 30분 타임아웃")
        except Exception as e:
            logger.error(f"[체리형부최신채널] 오류: {e}", exc_info=True)

    def _loop_cherry_family_learning(self) -> None:
        """매일 09:05 — 체리형부 family 4채널 등록 상태를 확인하고 증분 수집/재학습 메타를 저장."""
        logger.info("[체리형부패밀리학습] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(9, 5, skip_weekend=False)
            if self._stop_event.is_set():
                break
            _run_job_safe("체리형부패밀리학습", self._job_cherry_family_learning)

    def _job_cherry_family_learning(self) -> None:
        try:
            # sys.executable(venv python): 위 _job_cherry_latest_channel과 동일한
            # 이유로 postgres 라우팅을 태우기 위해 python3.14 하드코딩을 제거.
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/ops/refresh_cherry_family_pipeline.py",
                    "--run-type",
                    "scheduled",
                    "--days",
                    "7",
                    "--limit",
                    "1500",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=2700,
            )
            logger.info(
                f"[체리형부패밀리학습] 완료: returncode={result.returncode} "
                f"stdout={result.stdout[-700:] if result.stdout else ''}"
            )
            if result.returncode != 0:
                logger.error(f"[체리형부패밀리학습] 오류: {result.stderr[-700:] if result.stderr else ''}")
        except subprocess.TimeoutExpired:
            logger.error("[체리형부패밀리학습] 45분 타임아웃")
        except Exception as e:
            logger.error(f"[체리형부패밀리학습] 오류: {e}", exc_info=True)

    # ── 텐버거 트리거 알림 ──────────────────────────────────────────
    def _loop_tenbagger_trigger(self) -> None:
        """평일 18:00 복합 트리거 알림 (기관연속+임원매수+신용급감+외국인급증)."""
        logger.info("[텐버거트리거] 루프 시작")
        while not self._stop_event.is_set():
            wait = _seconds_until(18, 0, skip_weekend=True)
            if self._stop_event.wait(wait):
                break
            _run_job_safe("텐버거트리거", self._job_tenbagger_trigger)

    def _job_tenbagger_trigger(self) -> None:
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/tenbagger_trigger_alert.py"],
                capture_output=True, text=True, timeout=120
            )
            logger.info(f"[텐버거트리거] 완료: {result.stdout[-200:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[텐버거트리거] 오류: {result.stderr[-200:]}")
        except Exception as e:
            logger.error(f"[텐버거트리거] 오류: {e}", exc_info=True)

    # ── 미국 종목 OHLCV 일별 시세 & 팩터 자동 적재 ───────────────────
    def _loop_us_daily_quotes_and_factors(self) -> None:
        """매일 한국시간 06:30에 직전 미국 거래 세션을 처리한다.

        토요일 아침은 미국 금요일 정규장 마감분을 처리해야 하므로 한국 주말을
        기준으로 이 루프를 차단하면 안 된다.
        """
        logger.info("[미국일별시세팩터수집] 루프 시작")
        while not self._stop_event.is_set():
            wait = _seconds_until(6, 30, skip_weekend=False)
            if self._stop_event.wait(wait):
                break
            _run_job_safe("미국일별시세팩터수집", self._job_us_daily_quotes_and_factors)

    def _job_us_daily_quotes_and_factors(self) -> None:
        try:
            # At 06:30 KST the completed US session is the prior Korea calendar
            # day. Weekend/holiday mornings should be skipped rather than
            # repeatedly reprocessing Friday's session on Sunday/Monday.
            expected_session = (datetime.now().date() - timedelta(days=1))
            if not is_trading_day(expected_session, "US"):
                logger.info("[미국일별시세팩터수집] %s 미국 휴장일 - 스킵", expected_session)
                return
            result = subprocess.run(
                [
                    sys.executable, "scripts/ops/sync_us_daily_quotes_and_factors.py",
                    "--stale-only", "--stale-before", expected_session.isoformat(),
                    "--batch-size", "100",
                ],
                cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
                capture_output=True,
                text=True,
                timeout=3600,
            )
            logger.info(f"[미국일별시세팩터수집] 완료: returncode={result.returncode}")
            if result.returncode != 0:
                logger.error(f"[미국일별시세팩터수집] 오류: {result.stderr[-300:] if result.stderr else ''}")
                return
            # The rebalance consumes exactly the just-collected US session.  It
            # must not run on a wall-clock timer while a long stale-only repair
            # is still in progress.
            self._job_us_virtual_rebalance(expected_session.isoformat())
        except Exception as e:
            logger.error(f"[미국일별시세팩터수집] 오류: {e}", exc_info=True)

    def _job_us_virtual_rebalance(self, expected_market_date: str) -> None:
        try:
            import sys as _sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in _sys.path:
                _sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from routes.us_virtual_trading import run_us_virtual_daily_rebalance
            result = run_us_virtual_daily_rebalance(expected_market_date=expected_market_date)
            logger.info(
                "[미국가상매매리밸런싱] ok=%s market_date=%s bought=%s sold=%s skipped=%s",
                result.get("ok"), result.get("market_date"), len(result.get("bought", [])),
                len(result.get("sold", [])), result.get("skipped", False),
            )
        except Exception as e:
            logger.error("[미국가상매매리밸런싱] 오류: %s", e, exc_info=True)
