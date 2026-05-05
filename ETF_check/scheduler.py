"""
ETF_check 스케줄러
매일 20:30 (장 마감 후, 장이 열리는 날 기준) 자동 수집 실행
실행: python scheduler.py (백그라운드 프로세스로 유지)
"""
import time
import logging
import subprocess
import sys
from datetime import datetime, date
import os

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


def run_collector():
    """collector.py 실행"""
    python = sys.executable
    script = os.path.join(DIR, "collector.py")
    logger.info(f"수집 시작: {datetime.now()}")
    try:
        result = subprocess.run(
            [python, script],
            capture_output=False,
            timeout=7200  # 최대 2시간
        )
        logger.info(f"수집 완료: return code={result.returncode}")
    except subprocess.TimeoutExpired:
        logger.error("수집 타임아웃 (2시간 초과)")
    except Exception as e:
        logger.error(f"수집 오류: {e}")


def main():
    logger.info("ETF_check 스케줄러 시작 (20:30 저녁 수집 모드)")
    last_run_date = None

    while True:
        now = datetime.now()
        today = now.date()

        # 실행 조건: 거래일 + 20:30 + 오늘 아직 실행 안 했으면
        if (
            now.hour == COLLECT_HOUR
            and now.minute == COLLECT_MINUTE
            and is_trading_day(today)
            and last_run_date != today
        ):
            logger.info(f"[SCHEDULER] 수집 트리거 — {today}")
            run_collector()
            last_run_date = today

        time.sleep(60)


if __name__ == "__main__":
    main()
