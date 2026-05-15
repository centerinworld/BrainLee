import os
import re
import sqlite3
import time
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/etf-check", tags=["etf-check"])

DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DIR, "etf_check.db")
STOCK_DB = "/Applications/stock_dashboard/stock.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Attach main DB to filter by market (KOSPI/KOSDAQ)
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
    return conn

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

def get_available_dates(conn) -> List[str]:
    # etf_amount > 0 인 종목이 실제로 존재하는 날만 유효 날짜로 사용 (수집 실패일 제외)
    rows = conn.execute("""
        SELECT DISTINCT e.trade_date
        FROM etf_inclusion_daily e
        JOIN collection_log l
          ON l.run_date = e.trade_date
         AND l.status = 'done'
        WHERE e.etf_amount > 0
        ORDER BY e.trade_date DESC
        LIMIT 6
    """).fetchall()
    return [row["trade_date"] for row in rows]

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
                SELECT e.stock_code, m.stock_name, e.etf_amount,
                       (SELECT ph.close FROM main_db.price_history ph
                        WHERE ph.stock_code = e.stock_code AND ph.close > 0
                        ORDER BY ph.date DESC LIMIT 1) AS current_price,
                       e.market_cap, e.mktcap_ratio,
                       (SELECT ROUND((ph1.close - ph2.close) * 100.0 / ph2.close, 2)
                        FROM (SELECT close FROM main_db.price_history
                              WHERE stock_code = e.stock_code AND close > 0
                              ORDER BY date DESC LIMIT 1) AS ph1,
                             (SELECT close FROM main_db.price_history
                              WHERE stock_code = e.stock_code AND close > 0
                              ORDER BY date DESC LIMIT 1 OFFSET 1) AS ph2
                        WHERE ph2.close > 0
                       ) AS price_change_pct
                FROM etf_inclusion_daily e
                JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
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
        date_1d = dates[1]
        date_5d = dates[-1] if len(dates) == 6 else dates[-1] # fallback to oldest available if less than 6
        
        def get_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff,
                       t0.market_cap
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND {ALL_MARKET}
                ORDER BY amount_diff {order}
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d":     get_change(date_1d),
            "5d":     get_change(date_5d),
            "1d_dec": get_change(date_1d, asc=True),
            "5d_dec": get_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
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
        date_1d = dates[1]
        date_5d = dates[-1] if len(dates) == 6 else dates[-1]
        
        # 시총대비 편입금액 증감 = (현재편입금액 - 과거편입금액) / 현재시가총액 * 100
        def get_ratio_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff, t0.market_cap,
                       ((t0.etf_amount - t1.etf_amount) / t0.market_cap * 100) as ratio_increase
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND t0.market_cap IS NOT NULL AND t0.market_cap > 0
                  AND {ALL_MARKET}
                ORDER BY ratio_increase {order}
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d":     get_ratio_change(date_1d),
            "5d":     get_ratio_change(date_5d),
            "1d_dec": get_ratio_change(date_1d, asc=True),
            "5d_dec": get_ratio_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
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
            SELECT e.stock_code, m.stock_name, e.etf_amount,
                   (SELECT ph.close FROM main_db.price_history ph
                    WHERE ph.stock_code = e.stock_code AND ph.close > 0
                    ORDER BY ph.date DESC LIMIT 1) AS current_price,
                   e.market_cap,
                   (e.etf_amount / e.market_cap * 100) as calc_ratio,
                   (SELECT ROUND((ph1.close - ph2.close) * 100.0 / ph2.close, 2)
                    FROM (SELECT close FROM main_db.price_history
                          WHERE stock_code = e.stock_code AND close > 0
                          ORDER BY date DESC LIMIT 1) AS ph1,
                         (SELECT close FROM main_db.price_history
                          WHERE stock_code = e.stock_code AND close > 0
                          ORDER BY date DESC LIMIT 1 OFFSET 1) AS ph2
                    WHERE ph2.close > 0
                   ) AS price_change_pct
            FROM etf_inclusion_daily e
            LEFT JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
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
        compare_date = dates[-1] if len(dates) > 1 else None
        needle = f"%{q.strip()}%"

        # CTE로 종목별 최근 유효일(etf_amount>0)을 먼저 구해 JOIN — 코어 서브쿼리 전체 스캔 방지
        query = """
            WITH best_dates AS (
                SELECT stock_code, MAX(trade_date) AS best_date
                FROM etf_inclusion_daily
                WHERE etf_amount > 0 AND trade_date <= ?
                GROUP BY stock_code
            )
            SELECT e.stock_code,
                   m.stock_name,
                   COALESCE(
                       (SELECT ph.close FROM main_db.price_history ph
                        WHERE ph.stock_code = e.stock_code AND ph.close > 0
                        ORDER BY ph.date DESC LIMIT 1),
                       m.close,
                       e.current_price) AS current_price,
                   CASE
                       WHEN (
                           SELECT ph.close FROM main_db.price_history ph
                           WHERE ph.stock_code = e.stock_code AND ph.close > 0
                           ORDER BY ph.date DESC LIMIT 1 OFFSET 5
                       ) > 0
                       THEN ROUND((
                           (SELECT ph.close FROM main_db.price_history ph
                            WHERE ph.stock_code = e.stock_code AND ph.close > 0
                            ORDER BY ph.date DESC LIMIT 1)
                           -
                           (SELECT ph.close FROM main_db.price_history ph
                            WHERE ph.stock_code = e.stock_code AND ph.close > 0
                            ORDER BY ph.date DESC LIMIT 1 OFFSET 5)
                       ) * 100.0 / (
                           SELECT ph.close FROM main_db.price_history ph
                           WHERE ph.stock_code = e.stock_code AND ph.close > 0
                           ORDER BY ph.date DESC LIMIT 1 OFFSET 5
                       ), 2)
                       ELSE NULL
                   END AS price_change_pct,
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
            JOIN best_dates bd ON e.stock_code = bd.stock_code AND e.trade_date = bd.best_date
            JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
            LEFT JOIN etf_inclusion_daily prev
                   ON prev.stock_code = e.stock_code AND prev.trade_date = ?
            WHERE (m.stock_name LIKE ? OR e.stock_code LIKE ?)
              AND """ + ORDINARY_STOCK_FILTER + """
            ORDER BY e.etf_amount DESC NULLS LAST
            LIMIT 50
        """
        rows = [format_row(r) for r in conn.execute(
            query,
            (compare_date or latest_date, latest_date, needle, needle),
        ).fetchall()]
        return {
            "rows": rows,
            "date": latest_date,
            "compare_date": compare_date,
            "query": q.strip(),
        }
    finally:
        conn.close()


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
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(1.5)

            if "signin" in page.url:
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
                SELECT e.etf_count, e.etf_amount, m.stock_name
                FROM etf_inclusion_daily e
                JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
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

    return {
        "stock_code": stock_code,
        "stock_name": db_info.get("stock_name") if db_info else None,
        "etf_count": etf_count,
        "etf_amount_total": db_info.get("etf_amount") if db_info else None,
        "etf_list": etf_list,
        "note": f"총 {etf_count or '?'}개 ETF 편입 (비중·금액 1위만 표시, 전체 목록은 etfcheck.co.kr 참조)",
    }
