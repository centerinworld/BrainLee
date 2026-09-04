"""
ETF_check 스케줄러
- 20:30 메인 수집 (장 마감 후)
- 01:45 전일 실패 종목 재수집
- 03:00 전일 값 백필 (재수집에도 실패 시)
실행: python scheduler.py (백그라운드 프로세스로 유지)
"""
import time
import logging
import subprocess
import sys
from datetime import datetime, date
import os
import sqlite3

DIR      = os.path.dirname(__file__)
LOG_PATH = os.path.join(DIR, "scheduler.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

COLLECT_HOUR   = 20  # 저녁 20시
COLLECT_MINUTE = 30  # 20:30 실행 (장 마감 후 ETF 데이터 업데이트 완료 시각)

RETRY_HOUR     = 1   # 새벽 1시
RETRY_MINUTE   = 45  # 01:45 전일 재수집

BACKFILL_HOUR  = 3   # 새벽 3시
BACKFILL_MINUTE = 0  # 03:00 전일 백필

# 한국 공휴일 (stock_dashboard와 동일 기준)
_KR_HOLIDAYS = {
    "2026-01-01","2026-01-27","2026-01-28","2026-01-29","2026-01-30",
    "2026-03-01","2026-05-01","2026-05-05","2026-06-06","2026-08-15",
    "2026-09-24","2026-09-25","2026-09-26","2026-10-03","2026-10-09",
    "2026-12-25",
}


def is_trading_day(d: date = None) -> bool:
    """오늘이 주식 거래일(평일 + 공휴일 아닌 날)인지 판단합니다."""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:    # 토(5) 일(6)
        return False
    if d.strftime("%Y-%m-%d") in _KR_HOLIDAYS:
        return False
    return True


def _run_script(mode: str, trade_date: str = None, timeout: int = 18000):
    """collector.py를 지정 모드로 실행"""
    python = sys.executable
    script = os.path.join(DIR, "collector.py")
    cmd    = [python, script]
    if mode == "retry":
        cmd.append("--retry")
    elif mode == "backfill":
        cmd.append("--backfill")
    if trade_date:
        cmd += ["--date", trade_date]

    logger.info(f"[{mode.upper()}] 실행 시작: {datetime.now()}")
    try:
        result = subprocess.run(cmd, capture_output=False, timeout=timeout)
        logger.info(f"[{mode.upper()}] 완료: return code={result.returncode}")
    except subprocess.TimeoutExpired:
        logger.error(f"[{mode.upper()}] 타임아웃 ({timeout}초 초과)")
        if mode == "main":
            db_path = os.path.join(DIR, "etf_check.db")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE collection_log
                    SET finished_at=datetime('now','localtime'), status='error'
                    WHERE id=(SELECT id FROM collection_log WHERE status='running' ORDER BY id DESC LIMIT 1)
                    """
                )
    except Exception as e:
        logger.error(f"[{mode.upper()}] 오류: {e}")


def main():
    logger.info("ETF_check 스케줄러 시작 (20:30 수집 / 01:45 재수집 / 03:00 백필)")
    last_collect_date  = None
    last_retry_date    = None
    last_backfill_date = None

    while True:
        now   = datetime.now()
        today = now.date()

        # ── 메인 수집: 20:30 (거래일) ──────────────────────────────
        if (
            now.hour == COLLECT_HOUR
            and now.minute == COLLECT_MINUTE
            and is_trading_day(today)
            and last_collect_date != today
        ):
            logger.info(f"[SCHEDULER] 메인 수집 트리거 — {today}")
            # 현재 원천 사이트는 종목당 약 5초가 필요해 2,700종목 전체 수집에
            # 3.5~4시간이 걸린다. 기존 90분 제한은 매일 약 1,000행에서 강제 종료됐다.
            _run_script("main", timeout=18000)
            last_collect_date = today

        # ── 재수집: 01:45 (전일 거래일 실패 종목) ───────────────
        elif (
            now.hour == RETRY_HOUR
            and now.minute == RETRY_MINUTE
            and last_retry_date != today
        ):
            from datetime import timedelta
            yesterday = today - timedelta(days=1)
            if is_trading_day(yesterday):
                logger.info(f"[SCHEDULER] 재수집 트리거 — {yesterday}")
                _run_script("retry", trade_date=yesterday.strftime("%Y-%m-%d"), timeout=3600)
                last_retry_date = today

        # ── 백필: 03:00 (전날 수집된 경우 다음날 새벽 실행) ────────
        elif (
            now.hour == BACKFILL_HOUR
            and now.minute == BACKFILL_MINUTE
            and last_backfill_date != today
        ):
            # 전날이 거래일이었는지 확인
            from datetime import timedelta
            yesterday = today - timedelta(days=1)
            if is_trading_day(yesterday):
                logger.info(f"[SCHEDULER] 백필 트리거 — 기준일: {yesterday}")
                _run_script("backfill", trade_date=yesterday.strftime("%Y-%m-%d"), timeout=1800)
                last_backfill_date = today

        time.sleep(60)


if __name__ == "__main__":
    if "--once" in sys.argv:
        today = date.today()
        if is_trading_day(today):
            logger.info(f"[ONCE] ETF 수집 1회 실행 — {today}")
            _run_script("main")
        else:
            logger.info(f"[ONCE] 비거래일 스킵 — {today}")
    elif "--retry" in sys.argv:
        from datetime import timedelta
        target = date.today() - timedelta(days=1) if datetime.now().hour < 12 else date.today()
        logger.info(f"[ONCE] ETF 재수집 1회 실행 — {target}")
        _run_script("retry", trade_date=target.strftime("%Y-%m-%d"))
    elif "--backfill" in sys.argv:
        from datetime import timedelta
        yesterday = date.today() - timedelta(days=1)
        logger.info(f"[ONCE] ETF 백필 1회 실행 — {yesterday}")
        _run_script("backfill", trade_date=yesterday.strftime("%Y-%m-%d"))
    else:
        main()
