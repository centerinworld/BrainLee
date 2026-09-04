import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/etf-check", tags=["etf-check"])

DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DIR, "etf_check.db")
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)
ETF_SCOPE_LABEL = os.getenv("ETF_CHECK_SCOPE_LABEL", "K-ETF")
MIN_USABLE_COVERAGE = float(os.getenv("ETF_CHECK_MIN_USABLE_COVERAGE", "0.30"))
MAX_BACKFILL_RATIO = float(os.getenv("ETF_CHECK_MAX_BACKFILL_RATIO", "0.05"))
MIN_COMPARISON_OVERLAP = float(os.getenv("ETF_CHECK_MIN_COMPARISON_OVERLAP", "0.75"))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_parse_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _tail_log_text(path: str, max_chars: int = 4000) -> str:
    try:
        with open(path, "rb") as fp:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            fp.seek(max(0, size - max_chars), os.SEEK_SET)
            return fp.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

def format_row(r):
    d = dict(r)
    name = d.get('stock_name')
    if name:
        mapping = {
            "리가켐 바이오사이언스": "리가켐바이오",
            "인텔리안테크놀로지스": "인텔리안테크",
            "YG엔터테인먼트": "와이지엔터테인먼트",
            "에스엠엔터테인먼트": "에스엠"
        }
        if name in mapping:
            d['stock_name'] = mapping[name]
    return d


def _is_etf_change_anomaly(row: Dict[str, Any]) -> tuple[bool, str | None]:
    """ETF Check 원천 파싱/집계 오류로 보이는 비연속 점프를 걸러낸다.

    정상적인 ETF 편입 증가도 있을 수 있으므로 금액 절대값만 보지 않고,
    편입액 급증과 ETF 검색수 급증이 동시에 나타나는 경우만 제외한다.
    """
    cur = row.get("current_amount") or row.get("etf_amount")
    prev = row.get("prev_amount") or row.get("prev_etf_amount")
    diff = row.get("amount_diff")
    cur_count = row.get("current_etf_count")
    prev_count = row.get("prev_etf_count")

    if cur is None or prev is None or diff is None or prev <= 0:
        return False, None

    abs_diff = abs(float(diff))
    amount_jump_pct = abs_diff / float(prev) * 100.0
    count_diff = None
    count_jump_pct = None
    if cur_count is not None and prev_count not in (None, 0):
        count_diff = abs(int(cur_count) - int(prev_count))
        count_jump_pct = count_diff / float(prev_count) * 100.0

    # SK스퀘어 사례: 5일 편입액 +7.8조, ETF 검색수 143→245처럼
    # 금액과 검색수가 함께 비정상적으로 튀는 경우.
    if (
        abs_diff >= 5_000
        and amount_jump_pct >= 50
        and count_diff is not None
        and count_diff >= 30
        and count_jump_pct is not None
        and count_jump_pct >= 30
    ):
        return True, (
            f"ETF검색수 급변({prev_count}→{cur_count})과 편입액 급변"
            f"({amount_jump_pct:.1f}%) 동시 발생"
        )

    # 시총 대비 과도한 편입액 변화가 ETF 검색수 급변과 동반되면 제외.
    market_cap = row.get("market_cap")
    if (
        market_cap
        and market_cap > 0
        and abs_diff / float(market_cap) * 100.0 >= 2.0
        and count_diff is not None
        and count_diff >= 30
    ):
        return True, f"시총 대비 편입액 변화 과대 및 ETF검색수 급변({prev_count}→{cur_count})"

    # 범위가 전체↔K-ETF로 바뀌면 종목별 ETF 검색수가 대략 절반/두 배가 된다.
    # 작은 종목은 절대 검색수 차이가 30 미만이라 위 조건만으로 잡히지 않는다.
    if market_cap and market_cap > 0 and abs_diff / float(market_cap) * 100.0 >= 2.0:
        if cur_count is not None and prev_count not in (None, 0):
            count_ratio = float(cur_count) / float(prev_count)
            if count_ratio < 0.67 or count_ratio > 1.5:
                return True, f"시총 대비 편입액 변화 과대 및 ETF검색수 배율 급변({prev_count}→{cur_count})"

    return False, None


def _filter_etf_change_rows(rows: List[sqlite3.Row], limit: int = 50) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for row in rows:
        d = format_row(row)
        is_bad, reason = _is_etf_change_anomaly(d)
        if is_bad:
            d["is_anomaly"] = True
            d["anomaly_reason"] = reason
            excluded.append(d)
            continue
        kept.append(d)
        if len(kept) >= limit:
            break
    return kept, excluded

def get_available_dates(conn) -> List[str]:
    # 너무 적은 테스트/백필 날짜는 제외한다. 부분 수집일은 최신 현황에 사용할 수 있지만,
    # 아래 비교 품질검사에서 행수와 공통 종목 비율이 같은 날짜끼리만 묶는다.
    rows = conn.execute("""
        WITH expected AS (
            SELECT COALESCE(
                NULLIF((SELECT COUNT(*) FROM etf_stock_meta), 0),
                (SELECT total_stocks FROM collection_log WHERE total_stocks >= 1000 ORDER BY id DESC LIMIT 1)
            ) AS stock_count
        ), daily AS (
            SELECT trade_date,
                   COUNT(*) AS rows_total,
                   SUM(CASE WHEN etf_amount > 0 THEN 1 ELSE 0 END) AS positive_rows,
                   SUM(CASE WHEN COALESCE(is_backfilled, 0) = 1 THEN 1 ELSE 0 END) AS backfilled_rows,
                   COUNT(DISTINCT COALESCE(scope_label, '')) AS scope_count,
                   MAX(COALESCE(scope_label, '')) AS scope_label
            FROM etf_inclusion_daily
            GROUP BY trade_date
        )
        SELECT d.trade_date
        FROM daily d CROSS JOIN expected x
        WHERE d.positive_rows > 0
          AND d.rows_total >= x.stock_count * ?
          AND d.backfilled_rows * 1.0 / d.rows_total <= ?
          AND d.scope_count = 1
          AND d.scope_label = ?
        ORDER BY d.trade_date DESC
        LIMIT 30
    """, (MIN_USABLE_COVERAGE, MAX_BACKFILL_RATIO, ETF_SCOPE_LABEL)).fetchall()
    return [row["trade_date"] for row in rows]


def _daily_source_profile(conn, trade_date: str) -> Dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS rows_total,
            SUM(CASE WHEN etf_amount > 0 THEN 1 ELSE 0 END) AS positive_rows,
            AVG(CASE WHEN etf_amount > 0 THEN etf_count END) AS avg_etf_count,
            AVG(CASE WHEN etf_amount > 0 THEN mktcap_ratio END) AS avg_mktcap_ratio,
            SUM(CASE WHEN COALESCE(is_backfilled, 0) = 1 THEN 1 ELSE 0 END) AS backfilled_rows,
            COUNT(DISTINCT COALESCE(scope_label, '')) AS scope_count,
            MAX(COALESCE(scope_label, '')) AS scope_label
        FROM etf_inclusion_daily
        WHERE trade_date = ?
        """,
        (trade_date,),
    ).fetchone()
    if not row:
        return None
    return {
        "trade_date": trade_date,
        "rows_total": int(row["rows_total"] or 0),
        "positive_rows": int(row["positive_rows"] or 0),
        "avg_etf_count": float(row["avg_etf_count"] or 0),
        "avg_mktcap_ratio": float(row["avg_mktcap_ratio"] or 0),
        "backfilled_rows": int(row["backfilled_rows"] or 0),
        "scope_count": int(row["scope_count"] or 0),
        "scope_label": row["scope_label"],
    }


def _find_comparable_previous_date(
    conn,
    latest_date: str,
    candidates: List[str],
    min_gap_days: int = 1,
    max_gap_days: int | None = None,
) -> tuple[str | None, Dict[str, Any] | None]:
    latest_profile = _daily_source_profile(conn, latest_date)
    if not latest_profile:
        return None, None
    latest_dt = _safe_parse_dt(latest_date)
    date_gap_rejected = False
    for prev_date in candidates:
        if prev_date == latest_date:
            continue
        prev_dt = _safe_parse_dt(prev_date)
        if latest_dt and prev_dt:
            gap_days = (latest_dt.date() - prev_dt.date()).days
            if gap_days < min_gap_days or (max_gap_days is not None and gap_days > max_gap_days):
                date_gap_rejected = True
                continue
        prev_profile = _daily_source_profile(conn, prev_date)
        if not prev_profile:
            continue
        if prev_profile["positive_rows"] <= 0 or latest_profile["positive_rows"] <= 0:
            continue
        if latest_profile["scope_count"] != 1 or prev_profile["scope_count"] != 1:
            continue
        if latest_profile["scope_label"] != prev_profile["scope_label"]:
            continue
        row_ratio = latest_profile["positive_rows"] / max(prev_profile["positive_rows"], 1)
        count_ratio = latest_profile["avg_etf_count"] / max(prev_profile["avg_etf_count"], 1e-9)
        overlap = conn.execute(
            """
            SELECT COUNT(*)
            FROM etf_inclusion_daily a
            JOIN etf_inclusion_daily b ON b.stock_code = a.stock_code
            WHERE a.trade_date=? AND b.trade_date=?
              AND a.etf_amount > 0 AND b.etf_amount > 0
              AND COALESCE(a.is_backfilled, 0)=0 AND COALESCE(b.is_backfilled, 0)=0
            """,
            (latest_date, prev_date),
        ).fetchone()[0]
        overlap_ratio = overlap / max(min(latest_profile["positive_rows"], prev_profile["positive_rows"]), 1)
        # 원천 사이트 집계 체계가 바뀌면 positive_rows·평균 ETF검색수가 전시장 수준에서 함께 점프한다.
        # 그 전후 날짜를 그대로 비교하면 삼성전자처럼 "증감액 11조" 같은 왜곡값이 나온다.
        if row_ratio > 1.25 or row_ratio < 0.8:
            continue
        if count_ratio > 1.15 or count_ratio < 0.85:
            continue
        if overlap_ratio < MIN_COMPARISON_OVERLAP:
            continue
        return prev_date, {
            "latest_profile": latest_profile,
            "previous_profile": prev_profile,
            "row_ratio": round(row_ratio, 3),
            "avg_etf_count_ratio": round(count_ratio, 3),
            "overlap_ratio": round(overlap_ratio, 3),
            "regime_break_detected": False,
        }
    return None, {
        "latest_profile": latest_profile,
        "previous_profile": None,
        "row_ratio": None,
        "avg_etf_count_ratio": None,
        "date_gap_rejected": date_gap_rejected,
        "regime_break_detected": True,
    }


@router.get("/status")
def get_etf_status() -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        latest_trade_date = dates[0] if dates else None
        latest_log = conn.execute(
            """
            SELECT run_date, started_at, finished_at, total_stocks, success, failed, status
            FROM collection_log
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_success = conn.execute(
            """
            SELECT run_date, started_at, finished_at, total_stocks, success, failed, status
            FROM collection_log
            WHERE status='done'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_snapshot = conn.execute(
            """
            WITH expected AS (
                SELECT COALESCE(
                    NULLIF((SELECT COUNT(*) FROM etf_stock_meta), 0),
                    (SELECT total_stocks FROM collection_log WHERE total_stocks >= 1000 ORDER BY id DESC LIMIT 1)
                ) AS stock_count
            ), latest AS (
                SELECT MAX(trade_date) AS trade_date
                FROM etf_inclusion_daily
                WHERE scope_label = ?
            )
            SELECT l.trade_date,
                   COUNT(e.stock_code) AS rows_collected,
                   x.stock_count AS rows_expected,
                   ROUND(COUNT(e.stock_code) * 1.0 / NULLIF(x.stock_count, 0), 3) AS coverage_ratio
            FROM latest l CROSS JOIN expected x
            LEFT JOIN etf_inclusion_daily e
              ON e.trade_date=l.trade_date
             AND e.scope_label = ?
            GROUP BY l.trade_date, x.stock_count
            """
        , (ETF_SCOPE_LABEL, ETF_SCOPE_LABEL)).fetchone()
        scheduler_log = _tail_log_text(os.path.join(DIR, "scheduler.log"))
        etf_log = _tail_log_text(os.path.join(DIR, "etf_check.log"))
        session_path = os.path.join(DIR, "session_state.json")
        session_exists = os.path.exists(session_path)
        session_updated_at = None
        if session_exists:
            session_updated_at = datetime.fromtimestamp(os.path.getmtime(session_path)).isoformat()

        latest_run = dict(latest_log) if latest_log else None
        latest_success_run = dict(latest_success) if latest_success else None
        issue_code = None
        issue_message = None

        if "세션 만료" in scheduler_log or "세션 만료" in etf_log:
            issue_code = "session_expired"
            issue_message = "ETF Check 로그인 세션이 만료되어 자동수집이 실패 중입니다."
        elif latest_run and latest_run.get("status") == "error":
            issue_code = "collection_error"
            issue_message = "최근 ETF 수집이 오류 상태로 종료되었습니다."

        stale_days = None
        if latest_trade_date:
            latest_dt = _safe_parse_dt(latest_trade_date)
            if latest_dt:
                stale_days = (datetime.now().date() - latest_dt.date()).days
                if stale_days >= 3 and issue_code is None:
                    issue_code = "stale_data"
                    issue_message = f"ETF 데이터 최신 기준일이 {latest_trade_date}로 오래되었습니다."

        return {
            "latest_trade_date": latest_trade_date,
            "stale_days": stale_days,
            "latest_run": latest_run,
            "latest_success_run": latest_success_run,
            "latest_snapshot": dict(latest_snapshot) if latest_snapshot else None,
            "session_state": {
                "exists": session_exists,
                "updated_at": session_updated_at,
            },
            "issue_code": issue_code,
            "issue_message": issue_message,
        }
    finally:
        conn.close()

# stock_universe.market / secugrp_nm 분포:
#   market='KOSPI'    + secugrp_nm='주권' → 코스피 실제 보통주·우선주 (삼성전자 등)
#   market='유가증권'  + secugrp_nm=NULL  → 코스피 거래소 상장 ETF/ETN 전용
#   market='KOSDAQ'   + secugrp_nm='주권' → 코스닥 실제 보통주
#   market='코스닥'   + secugrp_nm=NULL  → 코스닥 거래소 상장 ETF/ETN 전용
#
# secugrp_nm='주권' 필터만으로 ETF/ETN 전체 배제 가능 (이름 패턴 불필요)
STOCK_FILTER  = "m.secugrp_nm = '주권'"
KOSPI_MARKET  = f"m.market IN ('KOSPI', '유가증권') AND {STOCK_FILTER}"
KOSDAQ_MARKET = f"m.market IN ('KOSDAQ', '코스닥') AND {STOCK_FILTER}"
ALL_MARKET    = STOCK_FILTER

# 하위호환: 기존 코드가 ORDINARY_STOCK_FILTER·ETF_NAME_FILTER를 참조하는 경우 대비
ETF_NAME_FILTER      = "1=1"
ORDINARY_STOCK_FILTER = STOCK_FILTER

@router.get("/tab1")
def get_tab1() -> Dict[str, Any]:
    """1번째 탭: 코스피/코스닥 별 ETF 편입 금액이 큰 종목 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"kospi": [], "kosdaq": [], "date": None}

        latest_date = dates[0]

        def fetch_market(market_filter: str):
            query = f"""
                SELECT e.stock_code, COALESCE(m.stock_name, e.stock_name) AS stock_name, e.etf_amount,
                       e.current_price,
                       e.market_cap, e.mktcap_ratio,
                       NULL AS price_change_pct
                FROM etf_inclusion_daily e
                JOIN etf_stock_meta m ON e.stock_code = m.stock_code
                WHERE e.trade_date = ?
                  AND {market_filter}
                ORDER BY e.etf_amount DESC NULLS LAST
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date,)).fetchall()]

        return {
            "kospi":  fetch_market(KOSPI_MARKET),
            "kosdaq": fetch_market(KOSDAQ_MARKET),
            "date": latest_date,
        }
    finally:
        conn.close()

@router.get("/tab2")
def get_tab2() -> Dict[str, Any]:
    """2번째 탭: 1일/5일 기준 ETF 편입 금액의 증가가 큰 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if len(dates) < 2:
            return {"1d": [], "5d": [], "dates": dates}
        
        latest_date = dates[0]
        date_1d, q1 = _find_comparable_previous_date(
            conn, latest_date, dates[1:2], max_gap_days=4
        )
        date_5d, q5 = _find_comparable_previous_date(
            conn, latest_date, dates[5:6], min_gap_days=5, max_gap_days=12
        ) if len(dates) > 5 else (None, None)
        if not date_1d:
            return {
                "1d": [],
                "5d": [],
                "1d_dec": [],
                "5d_dec": [],
                "dates": {"latest": latest_date, "1d": None, "5d": None},
                "quality": {
                    "issue": "comparison_unavailable",
                    "message": "비교 가능한 직전 거래일 데이터가 없어 증감 비교를 중단했습니다.",
                    "comparison_1d": q1,
                    "comparison_5d": q5,
                },
            }
        
        def get_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff,
                       t0.market_cap,
                       t0.etf_count AS current_etf_count,
                       t1.etf_count AS prev_etf_count
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN etf_stock_meta m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND COALESCE(t0.is_backfilled, 0)=0 AND COALESCE(t1.is_backfilled, 0)=0
                  AND {ALL_MARKET}
                ORDER BY amount_diff {order}
                LIMIT 200
            """
            return _filter_etf_change_rows(conn.execute(query, (latest_date, prev_date)).fetchall())[0]

        raw_1d = conn.execute(f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff,
                       t0.market_cap,
                       t0.etf_count AS current_etf_count,
                       t1.etf_count AS prev_etf_count
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN etf_stock_meta m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND COALESCE(t0.is_backfilled, 0)=0 AND COALESCE(t1.is_backfilled, 0)=0
                  AND {ALL_MARKET}
                ORDER BY amount_diff DESC
                LIMIT 200
            """, (latest_date, date_1d)).fetchall()
        raw_5d = conn.execute(f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff,
                       t0.market_cap,
                       t0.etf_count AS current_etf_count,
                       t1.etf_count AS prev_etf_count
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN etf_stock_meta m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND COALESCE(t0.is_backfilled, 0)=0 AND COALESCE(t1.is_backfilled, 0)=0
                  AND {ALL_MARKET}
                ORDER BY amount_diff DESC
                LIMIT 200
            """, (latest_date, date_5d)).fetchall()
        _, excluded_1d = _filter_etf_change_rows(raw_1d)
        _, excluded_5d = _filter_etf_change_rows(raw_5d)

        return {
            "1d":     get_change(date_1d),
            "5d":     get_change(date_5d),
            "1d_dec": get_change(date_1d, asc=True),
            "5d_dec": get_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
            "quality": {
                "excluded_1d": len(excluded_1d),
                "excluded_5d": len(excluded_5d),
                "excluded_examples": (excluded_1d + excluded_5d)[:5],
                "comparison_1d": q1,
                "comparison_5d": q5,
            },
        }
    finally:
        conn.close()

@router.get("/tab3")
def get_tab3() -> Dict[str, Any]:
    """3번째 탭: 1일/5일 기준 시가총액 대비 편입금액 증가가 큰 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if len(dates) < 2:
            return {"1d": [], "5d": [], "dates": dates}
        
        latest_date = dates[0]
        date_1d, q1 = _find_comparable_previous_date(
            conn, latest_date, dates[1:2], max_gap_days=4
        )
        date_5d, q5 = _find_comparable_previous_date(
            conn, latest_date, dates[5:6], min_gap_days=5, max_gap_days=12
        ) if len(dates) > 5 else (None, None)
        if not date_1d:
            return {
                "1d": [],
                "5d": [],
                "1d_dec": [],
                "5d_dec": [],
                "dates": {"latest": latest_date, "1d": None, "5d": None},
                "quality": {
                    "issue": "comparison_unavailable",
                    "message": "비교 가능한 직전 거래일 데이터가 없어 증감 비교를 중단했습니다.",
                    "comparison_1d": q1,
                    "comparison_5d": q5,
                },
            }
        
        # 시총대비 편입금액 증감 = (현재편입금액 - 과거편입금액) / 현재시가총액 * 100
        def get_ratio_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff, t0.market_cap,
                       ((t0.etf_amount - t1.etf_amount) / t0.market_cap * 100) as ratio_increase,
                       t0.etf_count AS current_etf_count,
                       t1.etf_count AS prev_etf_count
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN etf_stock_meta m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND COALESCE(t0.is_backfilled, 0)=0 AND COALESCE(t1.is_backfilled, 0)=0
                  AND t0.market_cap IS NOT NULL AND t0.market_cap > 0
                  AND {ALL_MARKET}
                ORDER BY ratio_increase {order}
                LIMIT 200
            """
            return _filter_etf_change_rows(conn.execute(query, (latest_date, prev_date)).fetchall())[0]

        return {
            "1d":     get_ratio_change(date_1d),
            "5d":     get_ratio_change(date_5d),
            "1d_dec": get_ratio_change(date_1d, asc=True),
            "5d_dec": get_ratio_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
            "quality": {
                "comparison_1d": q1,
                "comparison_5d": q5,
            },
        }
    finally:
        conn.close()

@router.get("/tab4")
def get_tab4() -> Dict[str, Any]:
    """4번째 탭: ETF 편입금액이 시가총액 대비 큰 순서"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"top": [], "date": None}
        
        latest_date = dates[0]
        
        query = """
            SELECT e.stock_code, COALESCE(m.stock_name, e.stock_name) AS stock_name, e.etf_amount,
                   e.current_price,
                   e.market_cap,
                   (e.etf_amount / e.market_cap * 100) as calc_ratio,
                   NULL AS price_change_pct
            FROM etf_inclusion_daily e
            LEFT JOIN etf_stock_meta m ON e.stock_code = m.stock_code
            WHERE e.trade_date = ?
              AND e.etf_amount IS NOT NULL AND e.market_cap IS NOT NULL AND e.market_cap > 0
              AND """ + ORDINARY_STOCK_FILTER + """
            ORDER BY calc_ratio DESC
            LIMIT 50
        """
        top = [format_row(r) for r in conn.execute(query, (latest_date,)).fetchall()]
        
        return {"top": top, "date": latest_date}
    finally:
        conn.close()

@router.get("/search")
def search_stock_etf(q: str = Query(..., min_length=1, max_length=30)) -> Dict[str, Any]:
    """종목 검색: 최신 ETF 편입액, 시총대비 비중, 5수집일 전 대비 편입액 차이."""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"rows": [], "date": None, "compare_date": None, "query": q.strip()}

        latest_date = dates[0]
        compare_date = None
        if len(dates) > 5:
            compare_date, _ = _find_comparable_previous_date(
                conn, latest_date, dates[5:6], min_gap_days=5, max_gap_days=12
            )
        needle = f"%{q.strip()}%"

        query = """
            SELECT e.stock_code,
                   COALESCE(m.stock_name, e.stock_name) AS stock_name,
                   e.current_price,
                   NULL AS price_change_pct,
                   e.market_cap,
                   e.etf_amount,
                   CASE
                       WHEN e.market_cap IS NOT NULL AND e.market_cap > 0
                       THEN ROUND(e.etf_amount * 100.0 / e.market_cap, 3)
                       ELSE NULL
                   END AS mktcap_ratio,
                   prev.etf_amount AS prev_etf_amount,
                   CASE
                       WHEN prev.etf_amount IS NOT NULL THEN e.etf_amount - prev.etf_amount
                       ELSE NULL
                   END AS amount_diff
            FROM etf_inclusion_daily e
            JOIN etf_stock_meta m ON e.stock_code = m.stock_code
            LEFT JOIN etf_inclusion_daily prev
                   ON prev.stock_code = e.stock_code AND prev.trade_date = ?
                  AND COALESCE(prev.is_backfilled, 0)=0
            WHERE e.trade_date = ?
              AND COALESCE(e.is_backfilled, 0)=0
              AND (m.stock_name LIKE ? OR e.stock_code LIKE ?)
              AND """ + ORDINARY_STOCK_FILTER + """
            ORDER BY e.etf_amount DESC NULLS LAST
            LIMIT 50
        """
        rows = [format_row(r) for r in conn.execute(
            query,
            (compare_date, latest_date, needle, needle),
        ).fetchall()]
        return {
            "rows": rows,
            "date": latest_date,
            "compare_date": compare_date,
            "query": q.strip(),
        }
    finally:
        conn.close()


def _is_stock_detail_page(page_url: str, body_text: str, stock_code: str) -> bool:
    expected_path = f"/mobile/searchPdf/{stock_code}"
    return (
        expected_path in str(page_url or "")
        and stock_code in str(body_text or "")
        and "현재가" in str(body_text or "")
        and not ("로그인" in str(body_text or "") and "회원가입" in str(body_text or ""))
    )


def _sanitize_scope_result(result: Dict, scope_label: str) -> Dict:
    if scope_label != "K-ETF":
        return result

    top_amount = result.get("top_amount") or {}
    if "$" in str(top_amount.get("amount") or ""):
        result["top_amount"] = None
    return result


def _fetch_etf_top_from_web(stock_code: str) -> Dict:
    """
    etfcheck.co.kr 모바일 페이지에서 비중 TOP / 편입금액 TOP ETF를 파싱.
    페이지 구조:
      '비중 TOP'    → 다음 줄: ETF명, '|', 비중%
      '투자금액 TOP' → 다음 줄: 'ETF명 | 금액억'
    """
    state_path = os.path.join(DIR, "session_state.json")
    if not os.path.exists(state_path):
        raise FileNotFoundError("session_state.json 없음 — 먼저 로그인 필요")

    from playwright.sync_api import sync_playwright

    url = f"https://www.etfcheck.co.kr/mobile/searchPdf/{stock_code}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            storage_state=state_path,
            user_agent=BROWSER_USER_AGENT,
            locale="ko-KR",
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(2.5)

            body_text = page.locator("body").inner_text(timeout=5000)
            if "signin" in page.url or not _is_stock_detail_page(
                page.url, body_text, stock_code
            ):
                raise PermissionError("세션 만료 — 재로그인 필요")

            if ETF_SCOPE_LABEL:
                try:
                    scope_toggle = page.get_by_text(ETF_SCOPE_LABEL, exact=True).first
                    scope_toggle.click(timeout=3000)
                    time.sleep(2.0)
                    if "inactive" in str(scope_toggle.get_attribute("class") or ""):
                        scope_toggle.click(timeout=3000)
                        time.sleep(2.0)
                except Exception as exc:
                    raise RuntimeError(f"'{ETF_SCOPE_LABEL}' 범위를 확인하지 못했습니다") from exc

                scoped_body_text = page.locator("body").inner_text(timeout=5000)
                if (
                    not _is_stock_detail_page(page.url, scoped_body_text, stock_code)
                    or "inactive" in str(scope_toggle.get_attribute("class") or "")
                ):
                    raise PermissionError("세션 만료 — 재로그인 필요")

            result = page.evaluate("""
            () => {
                const lines = document.body.innerText.split('\\n').map(l=>l.trim()).filter(l=>l);
                const out = { top_ratio: null, top_amount: null, etf_count: null };

                for (let i = 0; i < lines.length; i++) {
                    const l = lines[i];

                    // ETF 검색수: 다음 줄 '218 종목'
                    if (l === 'ETF 검색수' && i+1 < lines.length) {
                        const m = lines[i+1].match(/(\\d+)/);
                        if (m) out.etf_count = parseInt(m[1]);
                    }

                    // 비중 TOP: 다음줄 ETF명, '|', 비중%
                    if (l === '비중 TOP' && i+1 < lines.length) {
                        const name = lines[i+1];
                        let ratio = null;
                        for (let j=i+2; j<=i+4 && j<lines.length; j++) {
                            if (/^[\\d.]+%$/.test(lines[j])) { ratio = lines[j]; break; }
                        }
                        out.top_ratio = { name: name, ratio: ratio, label: '비중 1위' };
                    }

                    // 투자금액 TOP: 다음줄 'ETF명 | 금액억'
                    if (l === '투자금액 TOP' && i+1 < lines.length) {
                        const raw = lines[i+1];
                        // 형식: 'KODEX 200 | 85,394억' 또는 'KODEX 200'
                        const parts = raw.split(' | ');
                        const name   = parts[0].trim();
                        const amount = parts[1] ? parts[1].trim() : null;
                        out.top_amount = { name: name, amount: amount, label: '편입금액 1위' };
                    }
                }
                return out;
            }
            """)
            result = _sanitize_scope_result(result, ETF_SCOPE_LABEL)
            if not result.get("etf_count") or not (result.get("top_ratio") or result.get("top_amount")):
                raise RuntimeError("K-ETF 상세 영역을 확인하지 못했습니다")
            return result
        finally:
            browser.close()


@router.get("/etf-list/{stock_code}")
def get_etf_list(stock_code: str) -> Dict[str, Any]:
    """
    특정 종목을 편입한 ETF TOP 정보 on-demand 조회.
    etfcheck.co.kr 모바일 페이지에서 비중 1위 / 편입금액 1위 ETF를 파싱 (약 3~5초 소요).
    전체 목록은 DB에 etf_count로 기록 (종목명은 미저장).
    """
    if not re.match(r"^\d{6}$", stock_code):
        raise HTTPException(status_code=400, detail="종목코드는 6자리 숫자여야 합니다")

    # DB에서 etf_count, etf_amount 먼저 확인
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        db_info = None
        if dates:
            row = conn.execute("""
                SELECT e.etf_count, e.etf_amount, COALESCE(m.stock_name, e.stock_name) AS stock_name
                FROM etf_inclusion_daily e
                LEFT JOIN etf_stock_meta m ON e.stock_code = m.stock_code
                WHERE e.stock_code = ? AND e.trade_date = ?
            """, (stock_code, dates[0])).fetchone()
            if row:
                db_info = dict(row)
    finally:
        conn.close()

    try:
        parsed = _fetch_etf_top_from_web(stock_code)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"수집 오류: {e}")

    # etf_count는 페이지 파싱값을 우선, fallback은 DB값
    etf_count = parsed.get("etf_count") or (db_info.get("etf_count") if db_info else None)

    # TOP 2를 etf_list 형식으로 정리
    etf_list = []
    if parsed.get("top_ratio"):
        t = parsed["top_ratio"]
        etf_list.append({"label": "비중 1위", "name": t["name"], "value": t.get("ratio"), "type": "ratio"})
    if parsed.get("top_amount"):
        t = parsed["top_amount"]
        etf_list.append({"label": "편입금액 1위", "name": t["name"], "value": t.get("amount"), "type": "amount"})

    displayed_labels = "·".join(item["label"] for item in etf_list)

    return {
        "stock_code": stock_code,
        "stock_name": db_info.get("stock_name") if db_info else None,
        "etf_count": etf_count,
        "etf_amount_total": db_info.get("etf_amount") if db_info else None,
        "etf_list": etf_list,
        "note": f"총 {etf_count or '?'}개 ETF 편입 ({displayed_labels} 표시, 전체 목록은 etfcheck.co.kr 참조)",
    }
