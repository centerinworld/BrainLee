"""
routes/dart_contracts.py — DART 수주·공급계약 공시 API

  GET  /api/dart-contracts/list         # 최근 공시 목록 (필터, 페이지)
  GET  /api/dart-contracts/{rcept_no}   # 공시 상세
  POST /api/dart-contracts/refresh      # 즉시 수집 (백그라운드)
  POST /api/dart-contracts/backfill     # 과거 N일 백필
  GET  /api/dart-contracts/signals      # 전략 연계 시그널 (stock_code별)
  GET  /api/dart-contracts/stats        # 통계 요약
"""

import json
import logging
import sqlite3
import threading
from datetime import date, timedelta

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ────────────────────────────────────────────
#  목록 조회
# ────────────────────────────────────────────

@router.get("/list")
def list_contracts(
    days: int = 30,
    min_signal: int = 1,
    signal_type: str = "",   # 강한매수/매수/관망/주의
    contract_type: str = "", # 기술이전/수출계약/방산수주 등
    is_overseas: int = -1,   # -1=전체, 1=해외, 0=국내
    limit: int = 50,
):
    """DART 수주공시 목록 반환."""
    conn = _db()
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    conditions = ["disclosed_at >= ?"]
    params: list = [cutoff]

    if min_signal > 1:
        conditions.append("signal_strength >= ?")
        params.append(min_signal)
    if signal_type:
        conditions.append("ai_signal = ?")
        params.append(signal_type)
    if contract_type:
        conditions.append("contract_type = ?")
        params.append(contract_type)
    if is_overseas >= 0:
        conditions.append("is_overseas = ?")
        params.append(is_overseas)

    where = " AND ".join(conditions)
    rows = conn.execute(f"""
        SELECT id, rcept_no, stock_code, stock_name, disclosed_at, report_nm,
               contract_amount, contract_unit, contract_amount_krw,
               contract_ratio_pct, counterparty, counterparty_country,
               is_overseas, contract_type,
               ai_score, ai_summary, ai_signal, signal_strength,
               telegram_sent, created_at
        FROM dart_contracts
        WHERE {where}
        ORDER BY disclosed_at DESC, signal_strength DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["signal_stars"] = "★" * (d["signal_strength"] or 0)
        d["signal_emoji"] = {
            "강한매수": "🚀", "매수": "📈",
            "관망": "👀", "주의": "⚠️",
        }.get(d.get("ai_signal", ""), "📌")
        result.append(d)
    return result


# ────────────────────────────────────────────
#  상세 조회
# ────────────────────────────────────────────

@router.get("/stats")
def get_stats():
    """시그널 통계 요약."""
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM dart_contracts").fetchone()[0]
    by_signal = conn.execute("""
        SELECT ai_signal, COUNT(*) as cnt
        FROM dart_contracts GROUP BY ai_signal
    """).fetchall()
    by_strength = conn.execute("""
        SELECT signal_strength, COUNT(*) as cnt
        FROM dart_contracts GROUP BY signal_strength ORDER BY signal_strength DESC
    """).fetchall()
    recent_strong = conn.execute("""
        SELECT stock_name, report_nm, disclosed_at, signal_strength, ai_signal
        FROM dart_contracts
        WHERE signal_strength >= 3
        ORDER BY disclosed_at DESC LIMIT 10
    """).fetchall()
    conn.close()
    return {
        "total": total,
        "by_signal": {r[0]: r[1] for r in by_signal},
        "by_strength": {f"★{r[0]}": r[1] for r in by_strength},
        "recent_strong": [dict(r) for r in recent_strong],
    }


@router.get("/signals")
def get_signals(days: int = 30, min_signal: int = 2):
    """
    전략 연계 시그널 반환.
    signal_engine.py / backtest.py에서 활용.
    """
    from collectors.dart_contract_collector import get_recent_contract_signals
    return get_recent_contract_signals(days=days)


@router.get("/{rcept_no}")
def get_contract_detail(rcept_no: str):
    """공시 상세 + 연관 주가 데이터."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM dart_contracts WHERE rcept_no=?", (rcept_no,)
    ).fetchone()
    if not row:
        conn.close()
        return {"error": "not found"}
    detail = dict(row)

    # 관련 주가 데이터 (공시 전후 20일)
    if detail.get("stock_code") and detail.get("disclosed_at"):
        from_date = detail["disclosed_at"]
        price_rows = conn.execute("""
            SELECT date, close, volume,
                   inst_net_buy, frn_net_buy
            FROM price_history
            WHERE stock_code=?
              AND date >= DATE(?, '-10 days')
              AND date <= DATE(?, '+30 days')
              AND close > 0
            ORDER BY date
        """, (detail["stock_code"], from_date, from_date)).fetchall()
        detail["price_chart"] = [dict(r) for r in price_rows]

        # 공시 전 기관 수급 패턴
        inst_rows = conn.execute("""
            SELECT SUM(inst_net_buy_amt) as inst_amt,
                   SUM(frn_net_buy_amt)  as frn_amt
            FROM price_history
            WHERE stock_code=?
              AND date >= DATE(?, '-10 days')
              AND date < ?
              AND close > 0
        """, (detail["stock_code"], from_date, from_date)).fetchone()
        if inst_rows:
            detail["pre_inst_amt_억"] = round((inst_rows[0] or 0) / 100, 1)
            detail["pre_frn_amt_억"] = round((inst_rows[1] or 0) / 100, 1)

        # 수출 데이터 연계 (HS무역)
        try:
            hs_conn = sqlite3.connect(
                "/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db",
                timeout=10
            )
            hs_conn.row_factory = sqlite3.Row
            export_rows = hs_conn.execute("""
                SELECT t.period_ym, SUM(t.export_value) as total_exp
                FROM hs_code_company_map m
                JOIN trade_series_cache t ON m.hs_code = t.hs_code
                WHERE m.stock_code=?
                  AND t.period_ym >= ?
                GROUP BY t.period_ym
                ORDER BY t.period_ym DESC LIMIT 12
            """, (detail["stock_code"],
                  detail["disclosed_at"][:4] + "-01")).fetchall()
            detail["export_trend"] = [dict(r) for r in export_rows]
            hs_conn.close()
        except Exception:
            detail["export_trend"] = []

    conn.close()
    return detail


# ────────────────────────────────────────────
#  수동 실행 / 백필
# ────────────────────────────────────────────

_refresh_running = False


@router.post("/refresh")
async def refresh_contracts(payload: dict = {}):
    """즉시 수집 (당일 공시 재스캔). 백그라운드 실행."""
    global _refresh_running
    if _refresh_running:
        return {"status": "already_running"}

    days = payload.get("days", 1)
    min_signal = payload.get("min_signal", 2)

    def _run():
        global _refresh_running
        _refresh_running = True
        try:
            from collectors.dart_contract_collector import collect_dart_contracts
            results = collect_dart_contracts(days=days, min_signal=min_signal)
            logger.info(f"[DART수주/refresh] {len(results)}건 처리")
        except Exception as e:
            logger.error(f"[DART수주/refresh] 오류: {e}")
        finally:
            _refresh_running = False

    threading.Thread(target=_run, daemon=True, name="DartContractRefresh").start()
    return {"status": "started", "days": days}


@router.post("/backfill")
async def backfill_contracts(payload: dict = {}):
    """과거 N일 공시 백필. 텔레그램 발송 없이."""
    days = payload.get("days", 30)

    def _run():
        try:
            from collectors.dart_contract_collector import backfill_dart_contracts
            n = backfill_dart_contracts(days=days)
            logger.info(f"[DART수주/backfill] {n}건 저장")
        except Exception as e:
            logger.error(f"[DART수주/backfill] 오류: {e}")

    threading.Thread(target=_run, daemon=True, name="DartContractBackfill").start()
    return {"status": "started", "days": days}
