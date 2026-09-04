"""
collectors/earnings_signal_detector.py
분기실적 기반 TTM 신호 탐지 엔진

탐지 신호 (BigQuery 실증 분석 기반):
  1. TTM_OP_INFLECT  : TTM 영업이익 음→양 전환 (평균 6.14배 상승)
  2. TTM_REV_30      : TTM 매출 YoY +30% 이상 (평균 6.03배 상승)
  3. TTM_OP_ACCEL    : TTM 영업이익 전분기 대비 50%+ 급증
  4. TTM_BOTH        : 위 1+2 동시 충족 (최강)
  5. QOQ_REV_20      : QoQ 매출 +20% 이상 2분기 연속

트리거:
  - DART 분기/반기/사업보고서 새 공시 감지 시 즉시 실행
  - 매일 새벽 financial_data 전체 재스캔
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from db_utils import connect_stock_db

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"

# 신호 유형 정의
SIGNAL_TYPES = {
    "TTM_OP_INFLECT": {
        "label": "TTM 영업이익 흑자전환",
        "emoji": "🔄",
        "avg_ratio": 6.14,
        "priority": "P1",
        "color": "#34d399",
        "desc": "최근 4개 분기 영업이익 합산이 음수→양수로 전환",
    },
    "TTM_REV_30": {
        "label": "TTM 매출 고성장 +30%",
        "emoji": "🚀",
        "avg_ratio": 6.03,
        "priority": "P1",
        "color": "#60a5fa",
        "desc": "최근 4Q 매출 합산이 전년 동기 대비 +30% 이상",
    },
    "TTM_BOTH": {
        "label": "흑자전환 + 매출 고성장",
        "emoji": "💎",
        "avg_ratio": 6.5,
        "priority": "P0",
        "color": "#f472b6",
        "desc": "흑자전환 AND 매출 +30% 동시 충족 — 최강 신호",
    },
    "TTM_OP_ACCEL": {
        "label": "TTM 영업이익 급가속",
        "emoji": "⚡",
        "avg_ratio": 5.5,
        "priority": "P2",
        "color": "#fbbf24",
        "desc": "TTM OP가 전분기 TTM 대비 +50% 이상 증가",
    },
    "QOQ_REV_20_2CON": {
        "label": "QoQ 매출 +20% 2연속",
        "emoji": "📈",
        "avg_ratio": 5.0,
        "priority": "P2",
        "color": "#a78bfa",
        "desc": "전분기 및 당분기 모두 QoQ +20% 이상",
    },
}


def _get_ttm(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int,
             field: str = "operating_profit") -> Optional[float]:
    """주어진 분기 기준 최근 4개 분기 합산 (TTM)"""
    rows = conn.execute(f"""
        SELECT {field}, year, quarter
        FROM financial_data
        WHERE stock_code=? AND is_annual=0 AND quarter>0
          AND {field} IS NOT NULL
          AND (year < ? OR (year=? AND quarter<=?))
        ORDER BY year DESC, quarter DESC
        LIMIT 4
    """, (stock_code, year, year, quarter)).fetchall()

    if len(rows) < 3:  # 최소 3분기 있어야 TTM 의미 있음
        return None
    return sum(r[0] for r in rows)


def _get_prev_ttm(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int,
                  field: str = "operating_profit") -> Optional[float]:
    """한 분기 이전 기준 TTM (전분기 TTM)"""
    pq = quarter - 1
    py = year
    if pq < 1:
        pq = 4
        py = year - 1
    return _get_ttm(conn, stock_code, py, pq, field)


def _get_yoy_ttm(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int,
                 field: str = "revenue") -> Optional[float]:
    """전년 동기 기준 TTM (YoY 비교용)"""
    return _get_ttm(conn, stock_code, year - 1, quarter, field)


def detect_signals_for_stock(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    quarter: int,
    price: Optional[float] = None,
) -> list[dict]:
    """
    특정 종목의 특정 분기 기준 TTM 신호 탐지

    Returns:
        [{"signal_type": "TTM_OP_INFLECT", "ttm_op_cur": 123, ...}, ...]
    """
    signals = []

    # ── TTM 영업이익 계산 ────────────────────────────────────────────
    ttm_op_cur  = _get_ttm(conn, stock_code, year, quarter, "operating_profit")
    ttm_op_prev = _get_prev_ttm(conn, stock_code, year, quarter, "operating_profit")
    ttm_op_yoy  = _get_yoy_ttm(conn, stock_code, year - 1, quarter, "operating_profit")

    # ── TTM 매출 계산 ─────────────────────────────────────────────────
    ttm_rev_cur  = _get_ttm(conn, stock_code, year, quarter, "revenue")
    ttm_rev_yoy  = _get_yoy_ttm(conn, stock_code, year, quarter, "revenue")

    # ── 당분기/전분기 매출 (QoQ) ─────────────────────────────────────
    def _get_quarter_rev(y, q):
        row = conn.execute("""
            SELECT revenue FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND revenue>0
            LIMIT 1
        """, (stock_code, y, q)).fetchone()
        return row[0] if row else None

    pq, py = (quarter - 1, year) if quarter > 1 else (4, year - 1)
    ppq, ppy = (pq - 1, py) if pq > 1 else (4, py - 1)
    rev_cur  = _get_quarter_rev(year, quarter)
    rev_prev = _get_quarter_rev(py, pq)
    rev_pp   = _get_quarter_rev(ppy, ppq)

    base = {
        "stock_code": stock_code,
        "year": year,
        "quarter": quarter,
        "ttm_op_cur": ttm_op_cur,
        "ttm_op_prev_q": ttm_op_prev,
        "ttm_op_yoy_base": ttm_op_yoy,
        "ttm_rev_cur": ttm_rev_cur,
        "ttm_rev_yoy_base": ttm_rev_yoy,
        "price_at_signal": price,
        "detected_at": datetime.now().isoformat(),
    }

    has_inflect = False
    has_rev30   = False

    # 신호 1: TTM 영업이익 흑자전환
    if (ttm_op_cur is not None and ttm_op_yoy is not None
            and ttm_op_yoy < 0 and ttm_op_cur > 0):
        has_inflect = True
        signals.append({**base,
            "signal_type": "TTM_OP_INFLECT",
            "detail": f"TTM OP {ttm_op_yoy/1e8:.0f}억→{ttm_op_cur/1e8:.0f}억 (음→양)",
        })

    # 신호 2: TTM 매출 +30% YoY
    if (ttm_rev_cur is not None and ttm_rev_yoy is not None
            and ttm_rev_yoy > 0
            and (ttm_rev_cur / ttm_rev_yoy - 1) >= 0.30):
        has_rev30 = True
        yoy_pct = round((ttm_rev_cur / ttm_rev_yoy - 1) * 100, 1)
        signals.append({**base,
            "signal_type": "TTM_REV_30",
            "detail": f"TTM 매출 YoY +{yoy_pct}% ({ttm_rev_yoy/1e8:.0f}억→{ttm_rev_cur/1e8:.0f}억)",
            "ttm_rev_yoy_pct": yoy_pct,
        })

    # 신호 3: 흑자전환 + 고성장 동시
    if has_inflect and has_rev30:
        signals.append({**base,
            "signal_type": "TTM_BOTH",
            "detail": "흑자전환 + 매출 +30% 동시 충족",
        })

    # 신호 4: TTM OP 급가속 (+50% QoQ TTM)
    if (ttm_op_cur is not None and ttm_op_prev is not None
            and ttm_op_prev > 0 and ttm_op_cur > 0
            and (ttm_op_cur / ttm_op_prev - 1) >= 0.50):
        accel_pct = round((ttm_op_cur / ttm_op_prev - 1) * 100, 1)
        signals.append({**base,
            "signal_type": "TTM_OP_ACCEL",
            "detail": f"TTM OP 전분기대비 +{accel_pct}% 가속",
            "ttm_op_accel_pct": accel_pct,
        })

    # 신호 5: QoQ 매출 +20% 2분기 연속
    if (rev_cur is not None and rev_prev is not None and rev_pp is not None
            and rev_prev > 0 and rev_pp > 0
            and (rev_cur / rev_prev - 1) >= 0.20
            and (rev_prev / rev_pp - 1) >= 0.20):
        qoq1 = round((rev_cur / rev_prev - 1) * 100, 1)
        qoq2 = round((rev_prev / rev_pp - 1) * 100, 1)
        signals.append({**base,
            "signal_type": "QOQ_REV_20_2CON",
            "detail": f"QoQ +{qoq2}% → +{qoq1}% 2연속",
        })

    return signals


def _ensure_signals_table(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS earnings_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code      TEXT    NOT NULL,
            stock_name      TEXT,
            year            INTEGER NOT NULL,
            quarter         INTEGER NOT NULL,
            signal_type     TEXT    NOT NULL,
            detail          TEXT,
            ttm_op_cur      REAL,
            ttm_op_prev_q   REAL,
            ttm_op_yoy_base REAL,
            ttm_rev_cur     REAL,
            ttm_rev_yoy_base REAL,
            ttm_rev_yoy_pct REAL,
            ttm_op_accel_pct REAL,
            price_at_signal REAL,
            telegram_sent   INTEGER DEFAULT 0,
            is_active       INTEGER DEFAULT 1,
            detected_at     TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, year, quarter, signal_type)
        );
        CREATE INDEX IF NOT EXISTS idx_es_code ON earnings_signals(stock_code);
        CREATE INDEX IF NOT EXISTS idx_es_type ON earnings_signals(signal_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_es_active ON earnings_signals(is_active, created_at);
    """)
    conn.commit()


def save_signals(conn: sqlite3.Connection, signals: list[dict], stock_name: str = ""):
    saved = 0
    for s in signals:
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO earnings_signals
                (stock_code, stock_name, year, quarter, signal_type, detail,
                 ttm_op_cur, ttm_op_prev_q, ttm_op_yoy_base,
                 ttm_rev_cur, ttm_rev_yoy_base, ttm_rev_yoy_pct, ttm_op_accel_pct,
                 price_at_signal, detected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s["stock_code"], stock_name or s.get("stock_name", ""),
                s["year"], s["quarter"], s["signal_type"], s.get("detail", ""),
                s.get("ttm_op_cur"), s.get("ttm_op_prev_q"),
                s.get("ttm_op_yoy_base"), s.get("ttm_rev_cur"),
                s.get("ttm_rev_yoy_base"), s.get("ttm_rev_yoy_pct"),
                s.get("ttm_op_accel_pct"), s.get("price_at_signal"),
                s.get("detected_at"),
            ))
            saved += max(cursor.rowcount, 0)
        except Exception as e:
            logger.debug(f"신호 저장 스킵 {s.get('stock_code')} {s.get('signal_type')}: {e}")
    conn.commit()
    return saved


def run_full_scan(days_back: int = 30, min_mktcap_억: int = 100) -> dict:
    """
    전종목 최신 분기 데이터 기준 TTM 신호 전체 스캔

    Args:
        days_back: 최근 N일 내 financial_data 업데이트된 종목만 스캔
        min_mktcap_억: 최소 시가총액 필터 (억원)

    Returns:
        {"scanned": N, "signals": M, "new_signals": K, "by_type": {...}}
    """
    conn = connect_stock_db(row_factory=sqlite3.Row)
    _ensure_signals_table(conn)

    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # 최근 업데이트된 종목의 최신 분기 가져오기
    # (PostgreSQL 라우팅 하에서는 GROUP BY f.stock_code + 비집계 컬럼 SELECT/HAVING가
    #  GroupingError이므로, 종목별 최신 (year,quarter) 1행을 ROW_NUMBER()로 뽑는다.)
    stocks = conn.execute(f"""
        WITH ranked AS (
            SELECT
                f.stock_code,
                su.stock_name,
                f.year,
                f.quarter,
                su.market_cap,
                ROW_NUMBER() OVER (
                    PARTITION BY f.stock_code ORDER BY f.year DESC, f.quarter DESC
                ) AS rn
            FROM financial_data f
            JOIN stock_universe su ON f.stock_code = su.stock_code
            WHERE f.is_annual = 0 AND f.quarter > 0
              AND f.revenue IS NOT NULL AND f.revenue > 0
              AND f.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND su.market IN ('KOSPI','KOSDAQ')
              AND (su.market_cap IS NULL
                   OR su.market_cap >= {min_mktcap_억}
                   OR su.market_cap >= {min_mktcap_억* 100000000})
              AND f.created_at >= '{cutoff}'
        )
        SELECT stock_code, stock_name, year, quarter, market_cap
        FROM ranked
        WHERE rn = 1
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()

    scanned = 0
    total_signals = 0
    new_signals = 0
    by_type: dict[str, int] = {}

    for row in stocks:
        code = row["stock_code"]
        name = row["stock_name"] or ""
        year = row["year"]
        quarter = row["quarter"]

        # 현재가 조회
        price_row = conn.execute("""
            SELECT close FROM price_history
            WHERE stock_code=? AND close>0
            ORDER BY date DESC LIMIT 1
        """, (code,)).fetchone()
        price = price_row[0] if price_row else None

        sigs = detect_signals_for_stock(conn, code, year, quarter, price)
        if sigs:
            saved = save_signals(conn, sigs, name)
            total_signals += len(sigs)
            new_signals += saved
            for s in sigs:
                t = s["signal_type"]
                by_type[t] = by_type.get(t, 0) + 1
                if saved > 0:
                    logger.info(
                        f"[{code}] {name} {year}Q{quarter} "
                        f"▶ {SIGNAL_TYPES[t]['emoji']} {SIGNAL_TYPES[t]['label']} | {s.get('detail','')}"
                    )
        scanned += 1

    conn.close()

    result = {
        "scanned": scanned, "signals": total_signals,
        "new_signals": new_signals, "by_type": by_type,
        "run_at": datetime.now().isoformat(),
    }
    logger.info(f"[실적신호스캔] 완료: {scanned}종목 스캔, {new_signals}건 신규 신호")
    return result


def scan_single_stock(stock_code: str) -> list[dict]:
    """단일 종목 즉시 스캔 (DART 공시 감지 시 호출)"""
    conn = connect_stock_db(row_factory=sqlite3.Row)
    _ensure_signals_table(conn)

    # 최신 분기 찾기
    row = conn.execute("""
        SELECT year, quarter FROM financial_data
        WHERE stock_code=? AND is_annual=0 AND quarter>0 AND revenue>0
        ORDER BY year DESC, quarter DESC LIMIT 1
    """, (stock_code,)).fetchone()

    if not row:
        conn.close()
        return []

    name_row = conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_code=?", (stock_code,)
    ).fetchone()
    name = name_row[0] if name_row else ""

    price_row = conn.execute("""
        SELECT close FROM price_history
        WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    price = price_row[0] if price_row else None

    sigs = detect_signals_for_stock(conn, stock_code, row["year"], row["quarter"], price)
    saved = save_signals(conn, sigs, name)
    conn.close()

    return sigs


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = run_full_scan(days_back=90, min_mktcap_억=50)
    print(json.dumps(result, ensure_ascii=False, indent=2))
