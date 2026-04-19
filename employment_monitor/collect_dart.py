"""
employment_monitor/collect_dart.py
— DART API를 통한 상장기업 임직원 현황 수집 + 월별 보간

데이터 소스: OpenDartReader (dart.employee)
  - 사업보고서 연간 임직원 수 → 5년+ 히스토리
  - 12월 실제 데이터 → 나머지 월 선형보간
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from .db import connect as emp_connect, STOCK_DB_PATH

logger = logging.getLogger(__name__)

# 기업 규모 분류 기준 (시가총액 기준, 단위: 원)
_LARGE_CAP  = 1_000_000_000_000   # 1조원 이상 → 대기업
_MID_CAP    = 100_000_000_000     # 1000억원 이상 → 중견기업
# 미만 → 중소기업


def classify_company_size(mktcap: float | None) -> str:
    if mktcap is None or mktcap <= 0:
        return "중소기업"
    if mktcap >= _LARGE_CAP:
        return "대기업"
    if mktcap >= _MID_CAP:
        return "중견기업"
    return "중소기업"


def _get_dart():
    """OpenDartReader 클라이언트 반환."""
    try:
        import config as _cfg
        import OpenDartReader
        return OpenDartReader.OpenDartReader(_cfg.DART_API_KEY)
    except Exception as e:
        logger.error(f"[DART] 클라이언트 초기화 실패: {e}")
        return None


def _get_corp_code(dart, stock_code: str, conn_emp) -> str | None:
    """stock_code → DART 8자리 corp_code 조회 (캐시 우선)."""
    # 캐시 확인
    row = conn_emp.execute(
        "SELECT corp_code FROM dart_corp_map WHERE stock_code=?",
        (stock_code,)
    ).fetchone()
    if row:
        return row["corp_code"]

    # DART API로 조회
    try:
        df = dart.company(stock_code)
        if df is not None and not df.empty and "corp_code" in df.columns:
            corp_code = str(df["corp_code"].iloc[0]).zfill(8)
            corp_name = str(df.get("corp_name", pd.Series([""])).iloc[0])
            conn_emp.execute(
                "INSERT OR REPLACE INTO dart_corp_map(stock_code,corp_code,corp_name) VALUES(?,?,?)",
                (stock_code, corp_code, corp_name)
            )
            conn_emp.commit()
            return corp_code
    except Exception as e:
        logger.debug(f"[DART] corp_code 조회 실패 {stock_code}: {e}")

    return None


def _interpolate_monthly(annual: list[tuple[int, int]]) -> list[tuple[str, int, int]]:
    """
    연간 임직원 수 → 월별 선형보간.
    annual: [(year, count), ...] 내림차순
    Returns: [(ym, count, is_actual), ...] 내림차순
    """
    if not annual:
        return []

    # 오름차순으로 정렬
    sorted_data = sorted(annual, key=lambda x: x[0])
    result = []

    for i, (year, count) in enumerate(sorted_data):
        # 12월 = 실제값
        result.append((f"{year}-12", count, 1))

        # 이전 연도가 있으면 1~11월 선형보간
        if i > 0:
            prev_year, prev_count = sorted_data[i - 1]
            for m in range(1, 12):
                frac = m / 12.0
                interp = int(prev_count + (count - prev_count) * frac)
                result.append((f"{year}-{m:02d}", interp, 0))

    return sorted(result, key=lambda x: x[0], reverse=True)


def collect_company(stock_code: str, stock_name: str,
                    mktcap: float | None, sector: str | None,
                    dart, conn_emp) -> int:
    """단일 종목 DART 임직원 현황 수집 → employment_company_monthly 저장."""
    corp_code = _get_corp_code(dart, stock_code, conn_emp)
    if not corp_code:
        return 0

    try:
        df = dart.employee(corp_code)
    except Exception as e:
        logger.debug(f"[DART] employee 조회 실패 {stock_code}: {e}")
        return 0

    if df is None or df.empty:
        return 0

    company_size = classify_company_size(mktcap)

    # 연도별 합계 계산 (사업보고서만 사용: reprt_code=11011)
    annual: list[tuple[int, int]] = []
    for year, grp in df.groupby("bsns_year"):
        try:
            yr = int(str(year))
        except Exception:
            continue

        # 필드명 후보 (OpenDartReader 버전별 차이)
        count = 0
        for col in ("nm_emp", "직원수합계", "empCnt", "emp_total"):
            if col in grp.columns:
                total = grp[col].apply(
                    lambda v: int(str(v).replace(",", "")) if v and str(v).strip() not in ("", "-") else 0
                ).sum()
                if total > 0:
                    count = total
                    break

        if count > 0:
            annual.append((yr, count))

    if not annual:
        return 0

    monthly = _interpolate_monthly(annual)
    saved = 0
    for ym, emp_count, is_actual in monthly:
        try:
            conn_emp.execute(
                """INSERT INTO employment_company_monthly
                   (stock_code, stock_name, ym, employee_count, is_actual,
                    source, company_size, mktcap, sector_large)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(stock_code, ym) DO UPDATE SET
                   employee_count=excluded.employee_count,
                   is_actual=excluded.is_actual,
                   company_size=excluded.company_size,
                   mktcap=excluded.mktcap""",
                (stock_code, stock_name, ym, emp_count, is_actual,
                 "dart", company_size, mktcap, sector or "")
            )
            saved += 1
        except Exception as e:
            logger.debug(f"[DART] insert 오류 {stock_code} {ym}: {e}")

    conn_emp.commit()
    return saved


def collect_all_listed(batch_size: int = 50, delay: float = 0.5) -> int:
    """
    전체 상장 종목 임직원 현황 수집.
    stock_universe에서 종목 목록 읽어 DART API로 5년치 수집.
    """
    dart = _get_dart()
    if dart is None:
        logger.error("[DART] 클라이언트 없음 — 수집 중단")
        return 0

    conn_stock = sqlite3.connect(str(STOCK_DB_PATH))
    conn_stock.row_factory = sqlite3.Row
    conn_emp = emp_connect()

    stocks = conn_stock.execute(
        """SELECT stock_code, stock_name, market_cap, sector_large
           FROM stock_universe
           WHERE stock_code NOT LIKE '%^%'
             AND stock_code NOT LIKE '%-F'
             AND stock_code NOT LIKE '%=%'
           ORDER BY market_cap DESC NULLS LAST"""
    ).fetchall()
    conn_stock.close()

    logger.info(f"[DART] 전체 {len(stocks)}종목 임직원 수집 시작")
    total_saved = 0

    for i, s in enumerate(stocks):
        try:
            n = collect_company(
                stock_code=s["stock_code"],
                stock_name=s["stock_name"],
                mktcap=s["market_cap"],
                sector=s["sector_large"],
                dart=dart,
                conn_emp=conn_emp,
            )
            total_saved += n
            if n > 0:
                logger.debug(f"[DART] {s['stock_name']}({s['stock_code']}): {n}건")
        except Exception as e:
            logger.warning(f"[DART] {s['stock_code']} 오류: {e}")

        if (i + 1) % batch_size == 0:
            logger.info(f"[DART] {i+1}/{len(stocks)} 처리, 누적 {total_saved}건")
            time.sleep(delay)
        else:
            time.sleep(0.1)

    conn_emp.close()
    logger.info(f"[DART] 수집 완료: 총 {total_saved}건")
    return total_saved


def collect_watchlist() -> int:
    """관심종목 + 포트폴리오 + 매수후보 대상으로만 수집 (빠른 갱신)."""
    dart = _get_dart()
    if dart is None:
        return 0

    conn_stock = sqlite3.connect(str(STOCK_DB_PATH))
    conn_stock.row_factory = sqlite3.Row
    conn_emp = emp_connect()

    stocks = conn_stock.execute(
        """SELECT DISTINCT u.stock_code, u.stock_name, u.market_cap, u.sector_large
           FROM stock_universe u
           WHERE u.stock_code IN (
               SELECT stock_code FROM watchlist
               UNION SELECT stock_code FROM portfolio
               UNION SELECT stock_code FROM buy_candidates
           )"""
    ).fetchall()
    conn_stock.close()

    total = 0
    for s in stocks:
        try:
            n = collect_company(
                s["stock_code"], s["stock_name"],
                s["market_cap"], s["sector_large"],
                dart, conn_emp
            )
            total += n
        except Exception as e:
            logger.warning(f"[DART] {s['stock_code']}: {e}")
        time.sleep(0.2)

    conn_emp.close()
    logger.info(f"[DART] 관심종목 임직원 수집: {total}건")
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collect_all_listed()
