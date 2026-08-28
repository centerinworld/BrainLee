"""
routes/kiwoom.py — 키움 REST 연동 상태 점검 API
"""

from __future__ import annotations

import sqlite3
from typing import Any
from fastapi import APIRouter, Query

from collectors.kiwoom_collector import KiwoomCollector
from db_utils import STOCK_DB_PATH

router = APIRouter()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(rows: list[sqlite3.Row], key: str) -> float | None:
    values = [_to_float(r[key]) for r in rows if key in r.keys()]
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def _latest_and_prev(conn: sqlite3.Connection, table: str, code: str, order_col: str = "dt") -> list[sqlite3.Row]:
    try:
        return conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE stock_code=?
            ORDER BY {order_col} DESC
            LIMIT 6
            """,
            (code,),
        ).fetchall()
    except Exception:
        return []


def _latest_credit_rows(conn: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    """Return the freshest credit balance rows across legacy and daily Kiwoom tables."""
    legacy = _latest_and_prev(conn, "kiwoom_credit_balance", code)
    margin = []
    try:
        margin = conn.execute(
            """
            SELECT
                stock_code,
                dt,
                credit_balance AS credit_balance_qty,
                credit_amount AS credit_balance_amt,
                credit_ratio,
                NULL AS new_credit_qty,
                NULL AS repay_credit_qty,
                data_source AS source_api_id,
                collected_at AS updated_at,
                'margin_balance_daily' AS source_table
            FROM margin_balance_daily
            WHERE stock_code=?
            ORDER BY dt DESC
            LIMIT 6
            """,
            (code,),
        ).fetchall()
    except Exception:
        margin = []

    if not margin:
        try:
            margin = conn.execute(
                """
                SELECT
                    stock_code,
                    base_date AS dt,
                    credit_balance AS credit_balance_qty,
                    NULL AS credit_balance_amt,
                    NULL AS credit_ratio,
                    credit_buy_balance AS new_credit_qty,
                    credit_sell_balance AS repay_credit_qty,
                    source_api_id,
                    updated_at,
                    'kiwoom_margin_daily' AS source_table
                FROM kiwoom_margin_daily
                WHERE stock_code=?
                ORDER BY base_date DESC
                LIMIT 6
                """,
                (code,),
            ).fetchall()
        except Exception:
            margin = []

    if not legacy:
        return margin
    if not margin:
        return legacy

    legacy_dt = str(legacy[0]["dt"] or "")
    margin_dt = str(margin[0]["dt"] or "")
    return margin if margin_dt > legacy_dt else legacy


@router.get("/status")
def kiwoom_status():
    kc = KiwoomCollector()
    return {
        "enabled": kc.enabled,
        "configured": kc.is_configured(),
        "health": kc.health_check(),
    }


@router.post("/token/refresh")
def kiwoom_token_refresh():
    kc = KiwoomCollector()
    return kc.issue_token()


@router.post("/realtime/snapshot")
def kiwoom_realtime_snapshot(
    codes: str = Query(..., description="쉼표 구분 종목코드. 예: 005930,000660"),
    types: str = Query("0A,0B,0C", description="쉼표 구분 실시간 타입"),
    duration_sec: int = Query(12, ge=3, le=60),
):
    kc = KiwoomCollector()
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    type_list = [t.strip() for t in types.split(",") if t.strip()]
    return kc.collect_realtime_snapshot(stock_codes=code_list, types=type_list, duration_sec=duration_sec)


@router.post("/foreign-flow")
def kiwoom_foreign_flow(code: str = Query(..., description="종목코드 6자리")):
    kc = KiwoomCollector()
    return kc.fetch_foreign_flow(code.strip())


@router.post("/investor/collect")
def kiwoom_investor_collect(
    code: str = Query(..., description="종목코드 6자리"),
    base_dt: str = Query(None, description="기준일 YYYYMMDD (없으면 오늘)"),
    max_pages: int = Query(10, ge=1, le=30, description="최대 페이지 (1page=100행)"),
):
    """ka10059: 종목별 투자자 일별 매매 수집 (기관/외국인/개인 + 세부 기관분류)."""
    kc = KiwoomCollector()
    return kc.fetch_investor_by_stock(
        stock_code=code.strip(),
        base_dt=base_dt,
        max_pages=max_pages,
    )


@router.post("/stock-info/update")
def kiwoom_stock_info_update(
    code: str = Query(..., description="종목코드 6자리"),
):
    """ka10001: 종목 PER/PBR/ROE/EPS/BPS/유동주식수 수집 → stock_universe + stock_meta 업데이트."""
    kc = KiwoomCollector()
    return kc.fetch_stock_info(code.strip())


@router.get("/summary/{code}")
def kiwoom_stock_summary(code: str):
    """종목 상세 화면용 키움 확장정보: 수급/신용/프로그램/실시간 핵심값."""
    code = (code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        return {"ok": False, "reason": "종목코드는 6자리 숫자여야 합니다.", "stock_code": code}

    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        base = _row_to_dict(conn.execute(
            """
            SELECT su.stock_code, su.stock_name, su.market, su.close, su.change_rate,
                   su.market_cap, su.per, su.pbr, su.eps, su.bps, su.roe,
                   su.revenue, su.operating_profit, su.base_info_updated_at,
                   sm.float_shares, sm.shares_outstanding, sm.float_updated_at
            FROM stock_universe su
            LEFT JOIN stock_meta sm ON sm.stock_code = su.stock_code
            WHERE su.stock_code=?
            ORDER BY su.base_date DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone())

        foreign_rows = _latest_and_prev(conn, "kiwoom_foreign_flow", code)
        credit_rows = _latest_credit_rows(conn, code)
        investor_rows = _latest_and_prev(conn, "kiwoom_investor_daily", code)
        program_rows = conn.execute(
            """
            SELECT *
            FROM broker_program_stock_daily
            WHERE source='kiwoom' AND stock_code=?
            ORDER BY dt DESC
            LIMIT 6
            """,
            (code,),
        ).fetchall()
        realtime = _row_to_dict(conn.execute(
            """
            SELECT *
            FROM kiwoom_realtime_quote
            WHERE stock_code=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone())

        foreign_latest = _row_to_dict(foreign_rows[0]) if foreign_rows else None
        if foreign_latest:
            foreign_latest["five_day_change_qty"] = _sum(foreign_rows[:5], "change_qty")

        credit_latest = _row_to_dict(credit_rows[0]) if credit_rows else None
        if credit_latest and len(credit_rows) > 1:
            prev = credit_rows[1]
            latest_qty = _to_float(credit_latest.get("credit_balance_qty"))
            latest_ratio = _to_float(credit_latest.get("credit_ratio"))
            prev_qty = _to_float(prev["credit_balance_qty"])
            prev_ratio = _to_float(prev["credit_ratio"])
            credit_latest["credit_balance_qty_chg"] = (
                latest_qty - prev_qty if latest_qty is not None and prev_qty is not None else None
            )
            credit_latest["credit_ratio_chg"] = (
                latest_ratio - prev_ratio if latest_ratio is not None and prev_ratio is not None else None
            )

        investor_latest = _row_to_dict(investor_rows[0]) if investor_rows else None
        investor_summary = None
        if investor_rows:
            investor_summary = {
                "latest_dt": investor_rows[0]["dt"],
                "individual_net_qty_5d": _sum(investor_rows[:5], "ind_invsr"),
                "foreign_net_qty_5d": _sum(investor_rows[:5], "frgnr_invsr"),
                "institution_net_qty_5d": _sum(investor_rows[:5], "orgn"),
                "financial_investment_net_qty_5d": _sum(investor_rows[:5], "fnnc_invt"),
                "pension_net_qty_5d": _sum(investor_rows[:5], "penfnd_etc"),
                "latest": investor_latest,
            }

        program_latest = _row_to_dict(program_rows[0]) if program_rows else None
        program_summary = None
        if program_rows:
            program_summary = {
                "latest_dt": program_rows[0]["dt"],
                "latest": program_latest,
                "five_day_net_buy_qty": _sum(program_rows[:5], "net_buy_qty"),
                "five_day_net_buy_amt_krw": _sum(program_rows[:5], "net_buy_amt_krw"),
            }

        has_data = any([base, foreign_latest, credit_latest, investor_summary, program_summary, realtime])
        return {
            "ok": True,
            "stock_code": code,
            "source": "Kiwoom REST API + local DB",
            "notice": "키움 확장정보입니다. 공시 재무제표가 아니라 수급/신용/프로그램/실시간 보조 시그널로 해석해야 합니다.",
            "has_data": bool(has_data),
            "base_info": base,
            "foreign_flow": foreign_latest,
            "credit_balance": credit_latest,
            "investor_flow": investor_summary,
            "program_trading": program_summary,
            "realtime_quote": realtime,
        }
    except Exception as e:
        return {"ok": False, "stock_code": code, "reason": str(e)}
    finally:
        conn.close()


@router.post("/stock-universe/bulk-update")
def kiwoom_bulk_universe_update(
    limit: int = Query(100, ge=1, le=3945, description="처리 종목 수 (시가총액 내림차순)"),
):
    """ka10001 배치: stock_universe 전종목 PER/PBR/ROE/유동주식수 키움 실시간 갱신."""
    kc = KiwoomCollector()
    return kc.bulk_update_stock_universe(limit=limit)


@router.get("/investor/status")
def kiwoom_investor_status():
    """kiwoom_investor_daily 테이블 적재 현황."""
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=10)
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT stock_code) AS symbols,
                   MIN(dt) AS earliest,
                   MAX(dt) AS latest
            FROM kiwoom_investor_daily
        """).fetchone()
        itd = conn.execute("""
            SELECT COUNT(*) AS rows FROM investor_trading_daily
        """).fetchone()
        return {
            "ok": True,
            "kiwoom_investor_daily": {
                "rows": row[0], "symbols": row[1],
                "earliest": row[2], "latest": row[3]
            } if row else {},
            "investor_trading_daily_rows": itd[0] if itd else 0,
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    finally:
        conn.close()


@router.get("/data-status")
def kiwoom_data_status():
    """키움 적재 상태 점검: 스냅샷/틱히스토리/1분집계/수급 테이블 커버리지."""
    kc = KiwoomCollector()
    kc._ensure_tables()
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        def q1(sql: str):
            try:
                return conn.execute(sql).fetchone()
            except Exception:
                return None

        rt = q1("""
            SELECT COUNT(*) AS c, COUNT(DISTINCT stock_code) AS symbols, MAX(updated_at) AS latest
            FROM kiwoom_realtime_quote
        """)
        tk = q1("""
            SELECT COUNT(*) AS c, COUNT(DISTINCT stock_code) AS symbols, MAX(event_ts) AS latest
            FROM kiwoom_tick_history
        """)
        mn = q1("""
            SELECT COUNT(*) AS c, COUNT(DISTINCT stock_code) AS symbols, MAX(minute_ts) AS latest
            FROM kiwoom_minute_snapshot
        """)
        fl = q1("""
            SELECT COUNT(*) AS c, COUNT(DISTINCT stock_code) AS symbols, MAX(updated_at) AS latest
            FROM kiwoom_foreign_flow
        """)

        recent = []
        try:
            rows = conn.execute("""
                SELECT stock_code, close_price, minute_ts, sample_count
                FROM kiwoom_minute_snapshot
                ORDER BY minute_ts DESC
                LIMIT 15
            """).fetchall()
            recent = [dict(r) for r in rows]
        except Exception:
            pass

        return {
            "ok": True,
            "realtime_quote": dict(rt) if rt else {"c": 0, "symbols": 0, "latest": None},
            "tick_history": dict(tk) if tk else {"c": 0, "symbols": 0, "latest": None},
            "minute_snapshot": dict(mn) if mn else {"c": 0, "symbols": 0, "latest": None},
            "foreign_flow": dict(fl) if fl else {"c": 0, "symbols": 0, "latest": None},
            "recent_minute_samples": recent,
        }
    finally:
        conn.close()
