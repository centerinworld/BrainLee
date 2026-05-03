"""
ETF_check 스케줄러
매일 새벽 00:10 (장이 열리는 날 기준) 자동 수집 실행
실행: python scheduler.py (백그라운드 프로세스로 유지)
"""
import time
import logging
import subprocess
import sys
from datetime import datetime, date, timedelta
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

COLLECT_HOUR   = 0   # 새벽 00시
COLLECT_MINUTE = 10  # 00:10 실행


def is_trading_day(d: date = None) -> bool:
    """새벽에 실행되므로 '전날'이 평일(월~금)인지 판단합니다."""
    if d is None:
        d = date.today()
    # 실행 시점이 자정(00:10)이므로, 수집할 데이터는 어제 기준입니다.
    yesterday = d - timedelta(days=1)
    return yesterday.weekday() < 5  # 0=월 ... 4=금 (어제가 평일이면 True)


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
    logger.info("ETF_check 스케줄러 시작")
    last_run_date = None

    while True:
        now = datetime.now()
        today = now.date()

        # 실행 조건: 장이 열리는 날 + 새벽 00:10 + 오늘 아직 실행 안 했으면
        if (
            now.hour == COLLECT_HOUR
            and now.minute == COLLECT_MINUTE
            and is_trading_day(today)
            and last_run_date != today
        ):
            logger.info(f"[SCHEDULER] 수집 트리거 — {today}")
            run_collector()
            last_run_date = today

        # 1분 대기
        time.sleep(60)


if __name__ == "__main__":
    main()
