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
import threading
import time
from datetime import datetime, timedelta, date
from typing import Callable

import config

logger = logging.getLogger(__name__)

# 장중 시간 (KST)
_MARKET_OPEN  = (9,  0)
_MARKET_CLOSE = (15, 40)


def _is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _seconds_until(hour: int, minute: int, skip_weekend: bool = True) -> float:
    """다음 HH:MM까지 남은 초. 주말 건너뜀."""
    now    = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    if skip_weekend:
        while target.weekday() >= 5:
            target += timedelta(days=1)
    return max(0.0, (target - datetime.now()).total_seconds())


def _run_job_safe(name: str, fn: Callable) -> None:
    """예외 격리 래퍼 — 한 잡이 실패해도 스케줄러 전체에 영향 없음."""
    try:
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
            ("KRX일별수집",    self._loop_krx_daily),   # ★ KRX API 전종목 OHLCV
            ("전종목수급수집",  self._loop_supply_daily), # ★ KIS 전종목 30일 수급
            ("네이버밸류에이션", self._loop_naver_fundamentals),  # ★ 네이버 PBR/PER/EPS
            ("현금흐름배치",    self._loop_cashflow_batch),       # ★ DART 현금흐름표 월간
            ("텐버거오전",      self._loop_tenbagger_morning),    # ★ 09:00 텐버거 발굴
            ("텐버거정오",      self._loop_tenbagger_noon),       # ★ 12:00 텐버거 발굴
            ("텐버거오후",      self._loop_tenbagger_afternoon),  # ★ 15:00 텐버거 발굴
        ]
        for name, target in jobs:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        logger.info(f"[스케줄러] {len(jobs)}개 잡 시작")

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
        import sqlite3 as _sl
        from data_collector import DataCollector  # 기존 수집기 재사용 (점진적 전환)

        conn = _sl.connect("stock.db")
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
        """DART 공시 → 재무 공시 있는 종목만 재무 재수집."""
        import sqlite3 as _sl
        from data_collector import DataCollector

        collector = DataCollector(dart_api_key=config.DART_API_KEY)
        conn      = _sl.connect("stock.db")
        try:
            codes = self._get_all_tracked_codes(conn)
        finally:
            conn.close()

        # 오늘 공시 목록 1회 조회
        today_codes = set()
        try:
            today_str = date.today().strftime("%Y%m%d")
            dart      = collector.dart
            if dart:
                for kind in ["A", "B"]:
                    df = dart.list(today_str, today_str, kind=kind)
                    if df is not None and not df.empty and "stock_code" in df.columns:
                        for c in df["stock_code"].dropna().unique():
                            today_codes.add(str(c).zfill(6))
        except Exception as e:
            logger.warning(f"[공시확인] DART 목록 조회 오류: {e}")

        triggered = [c for c in codes if c in today_codes]
        logger.info(f"[공시확인] 오늘 공시 {len(today_codes)}종목 중 추적 대상 {len(triggered)}종목 재무 갱신")
        for code in triggered:
            try:
                if collector._has_dart_financial_disclosure(code):
                    collector.collect_fundamentals(code, latest_only=True)
            except Exception as e:
                logger.warning(f"[공시확인] {code} 재무 갱신 오류: {e}")

    def _job_intraday_prices(self) -> None:
        """장중 watchlist + 포트폴리오 현재가 갱신."""
        import sqlite3 as _sl
        from kis_client import kis_client

        conn = _sl.connect("stock.db")
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
        import sqlite3 as _sl
        from data_collector import DataCollector

        collector = DataCollector(dart_api_key=config.DART_API_KEY)

        # 지수 수급 (매크로)
        try:
            collector.collect_macro_data()
        except Exception as e:
            logger.debug(f"[장중수급] 매크로 오류: {e}")

        # 관심 종목만 (watchlist + portfolio + 활성 보유)
        conn = _sl.connect("stock.db")
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
        if datetime.now().weekday() >= 5:
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
        """공공데이터포털 오늘 날짜 전종목 일별 데이터 수집."""
        if not getattr(config, "PUBLIC_DATA_API_KEY", ""):
            logger.debug("[공공데이터] API 키 없음 — 스킵")
            return

        today  = date.today()
        bas_dt = today.strftime("%Y%m%d")

        loop = asyncio.new_event_loop()
        try:
            from collectors.public_data import PublicDataCollector
            collector = PublicDataCollector()
            saved     = loop.run_until_complete(collector.collect_all_for_date(bas_dt))
            total     = sum(saved.values())
            logger.info(f"[공공데이터] {bas_dt} 총 {total}건 저장")
        except Exception as e:
            logger.error(f"[공공데이터] {e}")
        finally:
            loop.close()

    # ──────────────────────────────────────────────────────────
    # KRX 일별 수집 (16:30 — 장 마감 후)
    # ──────────────────────────────────────────────────────────

    def _loop_krx_daily(self) -> None:
        """18:00 영업일마다 KRX API로 당일 전종목 OHLCV + 지수 수집.
        KRX 데이터는 장 마감(15:30) 후 데이터 처리에 약 2시간 소요 → 18:00 수집.
        """
        logger.info("[KRX일별] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(18, 0, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                self._job_krx_daily()
            except Exception as e:
                logger.error(f"[KRX일별] 잡 오류: {e}")
            # 다음 실행까지 1시간 대기 (같은 날 중복 실행 방지)
            self._wait_secs(3600)
        logger.info("[KRX일별] 루프 종료")

    def _job_krx_daily(self) -> None:
        """KRX 승인 API로 오늘 날짜 전종목 OHLCV + KOSPI/KOSDAQ 지수 저장."""
        import sqlite3
        import requests as _req

        api_key = getattr(config, "KRX_API_KEY", "")
        if not api_key:
            logger.warning("[KRX일별] KRX_API_KEY 없음 — 스킵")
            return

        today     = date.today()
        bas_dd    = today.strftime("%Y%m%d")
        today_str = today.strftime("%Y-%m-%d")
        base_url  = "https://data-dbg.krx.co.kr/svc/apis"
        headers   = {"AUTH_KEY": api_key}

        def _fetch(path: str) -> list:
            try:
                r = _req.get(
                    f"{base_url}/{path}",
                    params={"basDd": bas_dd},
                    headers=headers,
                    timeout=20,
                )
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

        conn = sqlite3.connect("stock.db", timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        ins = upd = idx_saved = 0

        # ① 유가증권 + ② 코스닥 종목 OHLCV
        for path in ("sto/stk_bydd_trd", "sto/ksq_bydd_trd"):
            rows = _fetch(path)
            for r in rows:
                code = str(r.get("ISU_CD", "")).strip()
                if not code or len(code) != 6 or not code.isdigit():
                    continue
                close  = _n(r, "TDD_CLSPRC")
                open_  = _n(r, "TDD_OPNPRC")
                high   = _n(r, "TDD_HGPRC")
                low    = _n(r, "TDD_LWPRC")
                volume = _n(r, "ACC_TRDVOL")
                mktcap = _n(r, "MKTCAP")
                if close <= 0:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (code, today_str, open_, high, low, close, volume))
                if cur.rowcount > 0:
                    ins += 1
                else:
                    conn.execute("""
                        UPDATE price_history SET open=?, high=?, low=?, close=?, volume=?
                        WHERE stock_code=? AND date=? AND (volume IS NULL OR volume=0)
                    """, (open_, high, low, close, volume, code, today_str))
                    upd += conn.execute("SELECT changes()").fetchone()[0]
                if mktcap > 0:
                    conn.execute(
                        "UPDATE stock_universe SET market_cap=? WHERE stock_code=?",
                        (mktcap, code),
                    )

        # ③ KOSPI 지수 (^KS11) + ④ KOSDAQ 지수 (^KQ11)
        _idx_name_map = {
            "코스피": "^KS11", "KOSPI": "^KS11",
            "코스닥": "^KQ11", "KOSDAQ": "^KQ11",
        }
        for path in ("idx/kospi_dd_trd", "idx/kosdaq_dd_trd"):
            for r in _fetch(path):
                idx_nm = str(r.get("IDX_NM", "")).strip()
                code = None
                for key, val in _idx_name_map.items():
                    if key in idx_nm and "200" not in idx_nm and "100" not in idx_nm and "소형" not in idx_nm:
                        code = val
                        break
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

    # ──────────────────────────────────────────────────────────
    # 전종목 수급 일괄 수집 (17:30 — KRX 수집 후)
    # ──────────────────────────────────────────────────────────

    def _loop_supply_daily(self) -> None:
        """17:30 영업일마다 전종목 KIS 30일 수급 일괄 수집."""
        logger.info("[전종목수급] 루프 시작")
        while not self._stop_event.is_set():
            self._wait_until(17, 30, skip_weekend=True)
            if self._stop_event.is_set():
                break
            try:
                self._job_supply_daily()
            except Exception as e:
                logger.error(f"[전종목수급] 잡 오류: {e}")
            # 다음 날까지 대기 (23시간)
            self._wait_secs(23 * 3600)
        logger.info("[전종목수급] 루프 종료")

    def _job_supply_daily(self) -> None:
        """KIS 전종목 최근 30거래일 수급 수집 → price_history 업데이트."""
        import sqlite3 as _sl
        from kis_client import kis_client

        conn = _sl.connect("stock.db", timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")

        try:
            rows = conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (stock_type IS NULL OR stock_type = '보통주')
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
                    conn.execute("""
                        UPDATE price_history
                        SET inst_net_buy     = ?,
                            frn_net_buy      = ?,
                            ind_net_buy      = ?,
                            inst_net_buy_amt = ?,
                            frn_net_buy_amt  = ?,
                            ind_net_buy_amt  = ?
                        WHERE stock_code=? AND date=?
                          AND (inst_net_buy IS NULL OR inst_net_buy = 0)
                          AND (frn_net_buy  IS NULL OR frn_net_buy  = 0)
                    """, (iq, fq, dq, ia, fa, da, code, d))
                    total_saved += conn.execute("SELECT changes()").fetchone()[0]

                if (i + 1) % 50 == 0:
                    conn.commit()
                    logger.info(f"[전종목수급] {i+1}/{len(codes)} 진행 중... 저장 {total_saved}건")

            except Exception as e:
                logger.debug(f"[전종목수급] {code}: {e}")

            time.sleep(1.05)   # KIS rate-limit (초당 1건)

        conn.commit()
        conn.close()
        logger.info(f"[전종목수급] 완료 — 총 {total_saved:,}건 저장")

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
                ["venv/bin/python3", "collect_dart_cashflow_batch.py", "--missing", "--years", "5"],
                capture_output=True, text=True, timeout=14400,
                cwd="/Applications/stock_dashboard",
            )
            logger.info(f"[현금흐름배치] 완료: {result.stdout[-300:] if result.stdout else ''}")
            if result.returncode != 0:
                logger.error(f"[현금흐름배치] 오류: {result.stderr[-300:]}")
        except Exception as e:
            logger.error(f"[현금흐름배치] 잡 오류: {e}")

    def _job_naver_fundamentals(self) -> None:
        """네이버금융 전종목 PBR/PER/EPS 배치 수집."""
        try:
            import subprocess
            result = subprocess.run(
                ["venv/bin/python3", "collect_naver_fundamentals.py", "--missing"],
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
