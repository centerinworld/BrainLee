"""
routes/earnings_signals.py  →  /api/earnings-signals/*

분기실적 TTM 신호 조회 API
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter()
DB_PATH = "stock.db"

SIGNAL_META = {
    "TTM_OP_INFLECT":  {"label": "TTM 영업이익 흑자전환", "emoji": "🔄", "avg_ratio": 6.14, "priority": "P1", "color": "#34d399"},
    "TTM_REV_30":      {"label": "TTM 매출 고성장 +30%",  "emoji": "🚀", "avg_ratio": 6.03, "priority": "P1", "color": "#60a5fa"},
    "TTM_BOTH":        {"label": "흑자전환+매출고성장",    "emoji": "💎", "avg_ratio": 6.50, "priority": "P0", "color": "#f472b6"},
    "TTM_OP_ACCEL":    {"label": "TTM 이익 급가속",        "emoji": "⚡", "avg_ratio": 5.50, "priority": "P2", "color": "#fbbf24"},
    "QOQ_REV_20_2CON": {"label": "QoQ +20% 2연속",         "emoji": "📈", "avg_ratio": 5.00, "priority": "P2", "color": "#a78bfa"},
}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


@router.get("/latest")
def get_latest_signals(
    days: int = Query(30, description="최근 N일"),
    signal_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None, description="P0/P1/P2"),
    limit: int = Query(50),
):
    """최신 TTM 신호 목록"""
    conn = _conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 신호 테이블 없으면 빈 결과
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='earnings_signals'"
    ).fetchone()
    if not exists:
        conn.close()
        return {"signals": [], "summary": {}}

    where = f"WHERE es.created_at >= '{cutoff}' AND es.is_active=1"
    params = []
    if signal_type:
        where += " AND es.signal_type=?"
        params.append(signal_type)
    if priority:
        types = [k for k, v in SIGNAL_META.items() if v["priority"] == priority]
        if types:
            where += f" AND es.signal_type IN ({','.join('?' for _ in types)})"
            params.extend(types)

    rows = conn.execute(f"""
        SELECT
            es.stock_code, es.stock_name, es.year, es.quarter,
            es.signal_type, es.detail,
            es.ttm_op_cur, es.ttm_op_yoy_base,
            es.ttm_rev_cur, es.ttm_rev_yoy_base, es.ttm_rev_yoy_pct,
            es.price_at_signal, es.created_at,
            su.sector_large, su.market, su.market_cap,
            su.per, su.pbr, su.roe,
            ph.close AS current_price,
            -- 신호 이후 수익률
            CASE WHEN es.price_at_signal > 0 AND ph.close > 0
                 THEN ROUND((ph.close - es.price_at_signal) / es.price_at_signal * 100, 1)
                 ELSE NULL END AS return_since_signal
        FROM earnings_signals es
        LEFT JOIN stock_universe su ON es.stock_code = su.stock_code
        LEFT JOIN (
            SELECT stock_code, close FROM price_history p1
            WHERE date = (SELECT MAX(date) FROM price_history p2
                         WHERE p2.stock_code=p1.stock_code AND p2.close>0)
        ) ph ON es.stock_code = ph.stock_code
        {where}
        ORDER BY
            CASE es.signal_type WHEN 'TTM_BOTH' THEN 0 WHEN 'TTM_OP_INFLECT' THEN 1
                                WHEN 'TTM_REV_30' THEN 2 ELSE 3 END,
            es.created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    signals = []
    for r in rows:
        meta = SIGNAL_META.get(r["signal_type"], {})
        d = dict(r)
        d["signal_label"] = meta.get("label", r["signal_type"])
        d["signal_emoji"] = meta.get("emoji", "📊")
        d["signal_color"] = meta.get("color", "#94a3b8")
        d["avg_ratio_hist"] = meta.get("avg_ratio", 0)
        d["priority"] = meta.get("priority", "P2")
        d["mktcap_억"] = round(d["market_cap"]) if d["market_cap"] else None
        signals.append(d)

    # 요약
    summary_rows = conn.execute(f"""
        SELECT signal_type, COUNT(*) cnt
        FROM earnings_signals es {where}
        GROUP BY signal_type ORDER BY cnt DESC
    """, params).fetchall()

    conn.close()
    return {
        "signals": signals,
        "summary": {r["signal_type"]: r["cnt"] for r in summary_rows},
        "total": len(signals),
        "period_days": days,
    }


@router.get("/stock/{code}")
def get_stock_signals(code: str, limit: int = 20):
    """특정 종목의 신호 이력"""
    conn = _conn()
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='earnings_signals'"
    ).fetchone()
    if not exists:
        conn.close()
        return {"signals": []}

    rows = conn.execute("""
        SELECT * FROM earnings_signals
        WHERE stock_code=? ORDER BY year DESC, quarter DESC, created_at DESC
        LIMIT ?
    """, (code, limit)).fetchall()

    conn.close()
    return {"signals": [dict(r) for r in rows]}


@router.post("/scan")
def trigger_scan(days_back: int = Query(7), min_mktcap: int = Query(100)):
    """수동 스캔 트리거 (관리자용)"""
    try:
        from collectors.earnings_signal_detector import run_full_scan
        result = run_full_scan(days_back=days_back, min_mktcap_억=min_mktcap)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/stats")
def get_signal_stats():
    """신호 통계 (대시보드 요약용)"""
    conn = _conn()
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='earnings_signals'"
    ).fetchone()
    if not exists:
        conn.close()
        return {"total": 0, "by_type": {}, "recent_30d": 0}

    total = conn.execute("SELECT COUNT(*) FROM earnings_signals WHERE is_active=1").fetchone()[0]
    cutoff_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent = conn.execute(
        "SELECT COUNT(*) FROM earnings_signals WHERE created_at>=? AND is_active=1", (cutoff_30,)
    ).fetchone()[0]

    by_type = conn.execute("""
        SELECT signal_type, COUNT(*) cnt,
               AVG(return_since_signal) avg_return
        FROM (
            SELECT es.signal_type,
                   CASE WHEN es.price_at_signal>0 AND ph.close>0
                        THEN (ph.close - es.price_at_signal)/es.price_at_signal*100
                        ELSE NULL END AS return_since_signal
            FROM earnings_signals es
            LEFT JOIN (
                SELECT stock_code, close FROM price_history p1
                WHERE date=(SELECT MAX(date) FROM price_history p2
                            WHERE p2.stock_code=p1.stock_code AND p2.close>0)
            ) ph ON es.stock_code=ph.stock_code
            WHERE es.is_active=1
        ) GROUP BY signal_type
    """).fetchall()

    conn.close()
    return {
        "total": total,
        "recent_30d": recent,
        "by_type": {
            r["signal_type"]: {
                "cnt": r["cnt"],
                "avg_return_pct": round(r["avg_return"], 1) if r["avg_return"] else None,
                **SIGNAL_META.get(r["signal_type"], {}),
            }
            for r in by_type
        },
    }
