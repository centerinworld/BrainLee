#!/usr/bin/env python3
"""
DART 공시 전종목 일괄 수집 (10년치 DB 저장)
- stock_universe의 국내 종목 전체 대상
- dart_disclosures 테이블에 UPSERT
- 일 10,000건 API 제한 고려: 배치당 최대 9,000건 (재실행 안전)
- 이미 최근 7일 이내 수집된 종목은 스킵
"""
import sqlite3
import time
import logging
import argparse
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = "stock.db"
YEARS_BACK = 10
RATE_LIMIT_SEC = 0.25   # 4 calls/sec → 14,400/hour (여유있게)
MAX_CALLS_PER_RUN = 9000  # 일일 한도 10,000 대비 안전 마진

DDL = """CREATE TABLE IF NOT EXISTS dart_disclosures (
    stock_code TEXT,
    rcept_no   TEXT,
    rcept_dt   TEXT,
    report_nm  TEXT,
    flr_nm     TEXT,
    corp_name  TEXT,
    dart_url   TEXT,
    fetched_at TEXT,
    PRIMARY KEY (stock_code, rcept_no)
)"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def already_fetched(conn, code: str, within_days: int = 7) -> bool:
    """최근 within_days 이내 수집된 종목이면 스킵"""
    cutoff = (datetime.now() - timedelta(days=within_days)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT MAX(fetched_at) FROM dart_disclosures WHERE stock_code=?", (code,)
    ).fetchone()
    if not row or not row[0]:
        return False
    return row[0] >= cutoff


def save_disclosures(conn, code: str, items: list):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM dart_disclosures WHERE stock_code=?", (code,))
    conn.executemany(
        "INSERT OR REPLACE INTO dart_disclosures VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                code,
                i.get("rcept_no", ""),
                i.get("rcept_dt", ""),
                i.get("report_nm", ""),
                i.get("flr_nm", ""),
                i.get("corp_name", ""),
                i.get("dart_url", ""),
                now,
            )
            for i in items
        ],
    )
    conn.commit()


def fetch_via_api(dart, code: str, start_str: str, end_str: str) -> list:
    """OpenDartReader API로 조회"""
    df = dart.list(code, start=start_str, end=end_str, final=False)
    if df is None or df.empty:
        return []
    if "rcept_dt" in df.columns:
        df = df.sort_values("rcept_dt", ascending=False)
    items = []
    for _, row in df.head(500).iterrows():
        rcept_no  = str(row.get("rcept_no", ""))
        rcept_dt  = str(row.get("rcept_dt", ""))
        report_nm = str(row.get("report_nm", ""))
        flr_nm    = str(row.get("flr_nm", ""))
        corp_name = str(row.get("corp_name", ""))
        if len(rcept_dt) == 8:
            rcept_dt = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"
        dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
        items.append({
            "rcept_no": rcept_no, "rcept_dt": rcept_dt, "report_nm": report_nm,
            "flr_nm": flr_nm, "corp_name": corp_name, "dart_url": dart_url,
        })
    return items


def main():
    parser = argparse.ArgumentParser(description="DART 공시 전종목 배치 수집")
    parser.add_argument("--skip-days", type=int, default=7,
                        help="최근 N일 이내 수집된 종목은 스킵 (기본 7)")
    parser.add_argument("--years", type=int, default=YEARS_BACK,
                        help="수집 기간(년) 기본 10")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS_PER_RUN,
                        help="이번 실행에서 최대 API 호출 수")
    parser.add_argument("--only-watched", action="store_true",
                        help="관심종목+포트폴리오+매수후보 우선 처리")
    args = parser.parse_args()

    import config
    import OpenDartReader

    dart = OpenDartReader(config.DART_API_KEY)
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365 * args.years)
    start_str = start_dt.strftime("%Y%m%d")
    end_str   = end_dt.strftime("%Y%m%d")

    conn = connect()
    conn.execute(DDL)
    conn.commit()

    # 수집 대상 종목 목록 (6자리 숫자 종목코드만 — ETF/선물/해외 제외)
    def _is_kr_stock(code: str) -> bool:
        return len(code) == 6 and code.isdigit()

    if args.only_watched:
        codes = [r[0] for r in conn.execute(
            """SELECT DISTINCT stock_code FROM (
               SELECT stock_code FROM watchlist
               UNION SELECT stock_code FROM portfolio
               UNION SELECT stock_code FROM buy_candidates
            )"""
        ).fetchall() if _is_kr_stock(r[0])]
        log.info(f"관심/포트/후보 종목 {len(codes)}개 대상")
    else:
        # 최근 1년 이내 가격 데이터 있는 국내 종목만 대상
        codes = [r[0] for r in conn.execute(
            """SELECT DISTINCT stock_code FROM price_history
               WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                 AND date >= date('now','-365 days')
               ORDER BY stock_code"""
        ).fetchall()]
        log.info(f"전체 국내 종목 {len(codes)}개 대상")

    total = len(codes)
    api_calls = 0
    skipped = 0
    fetched = 0
    errors = 0
    empty_streak = 0  # 연속 빈 결과 카운터 (할당량 초과 감지용)

    for i, code in enumerate(codes, 1):
        if api_calls >= args.max_calls:
            log.warning(f"API 호출 한도({args.max_calls}) 도달, 중단. 내일 재실행 필요.")
            break

        if already_fetched(conn, code, within_days=args.skip_days):
            skipped += 1
            if skipped % 100 == 0:
                log.info(f"[{i}/{total}] 스킵 {skipped}개 누적")
            continue

        try:
            items = fetch_via_api(dart, code, start_str, end_str)
            if items:
                save_disclosures(conn, code, items)
                fetched += 1
                empty_streak = 0
                log.info(f"[{i}/{total}] {code}: {len(items)}건 저장")
            else:
                empty_streak += 1
                # 연속 5회 이상 빈 결과 → 할당량 초과로 판단, 중단
                if empty_streak >= 5:
                    log.warning(
                        f"[{i}/{total}] 연속 {empty_streak}회 빈 결과 → DART API 할당량 초과. "
                        "내일 재실행 필요 (매주 일요일 02:00 자동 실행)."
                    )
                    break
                log.debug(f"[{i}/{total}] {code}: 공시 없음 (streak={empty_streak})")
            api_calls += 1
            time.sleep(RATE_LIMIT_SEC)

        except Exception as e:
            errors += 1
            log.warning(f"[{i}/{total}] {code} 실패: {e}")
            time.sleep(1.0)

    conn.close()
    log.info(
        f"완료: 수집={fetched}, 스킵={skipped}, 오류={errors}, "
        f"API호출={api_calls}/{args.max_calls}"
    )


if __name__ == "__main__":
    main()
