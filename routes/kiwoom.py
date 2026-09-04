"""
routes/kiwoom.py — 키움 REST 연동 상태 점검 API
"""

from __future__ import annotations

import sqlite3
import json
from typing import Any
from fastapi import APIRouter, Body, Query

from collectors.kiwoom_collector import KiwoomCollector
from db_utils import STOCK_DB_PATH, connect_stock_db

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


def _query_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Optional data sources must never make a stock-detail request fail."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _stock_flow_history(conn: sqlite3.Connection, code: str, days: int = 60) -> dict[str, Any]:
    """Build a point-in-time investor/short/credit context from collected rows."""
    supply_rows = _query_rows(conn, """
        SELECT date AS dt, ind_net_buy_amt, frn_net_buy_amt, inst_net_buy_amt
        FROM price_history
        WHERE stock_code=?
          AND (COALESCE(ind_net_buy_amt, 0) != 0
               OR COALESCE(frn_net_buy_amt, 0) != 0
               OR COALESCE(inst_net_buy_amt, 0) != 0)
        ORDER BY date DESC
        LIMIT ?
    """, (code, days))
    supply = [dict(r) for r in reversed(supply_rows)]
    foreign_cum = institution_cum = 0.0
    for row in supply:
        foreign_cum += _to_float(row.get("frn_net_buy_amt")) or 0.0
        institution_cum += _to_float(row.get("inst_net_buy_amt")) or 0.0
        row["foreign_cum_amt"] = foreign_cum
        row["institution_cum_amt"] = institution_cum

    short_rows = _query_rows(conn, """
        SELECT bas_dt AS dt, borrow_bal_qty, borrow_bal_amt, borrow_bal_pct, short_qty, short_amt
        FROM short_sell_daily
        WHERE stock_code=? AND stock_code != '000000'
        ORDER BY bas_dt DESC
        LIMIT ?
    """, (code, days))
    short_history = [dict(r) for r in reversed(short_rows)]
    for index, row in enumerate(short_history):
        prev = short_history[index - 1] if index else None
        current_balance = _to_float(row.get("borrow_bal_qty"))
        prev_balance = _to_float(prev.get("borrow_bal_qty")) if prev else None
        row["borrow_bal_qty_change"] = (
            current_balance - prev_balance
            if current_balance is not None and prev_balance is not None else None
        )

    credit_rows = _latest_credit_rows(conn, code)
    credit_history = [_row_to_dict(r) for r in reversed(credit_rows)]
    for index, row in enumerate(credit_history):
        prev = credit_history[index - 1] if index else None
        current_balance = _to_float(row.get("credit_balance_qty"))
        prev_balance = _to_float(prev.get("credit_balance_qty")) if prev else None
        row["credit_balance_qty_change"] = (
            current_balance - prev_balance
            if current_balance is not None and prev_balance is not None else None
        )

    def _net(period: int, key: str) -> float | None:
        values = [_to_float(r.get(key)) for r in supply[-period:]]
        values = [v for v in values if v is not None]
        return sum(values) if values else None

    return {
        "supply_history": supply,
        "supply_summary": {
            "latest_dt": supply[-1].get("dt") if supply else None,
            "foreign_net_amt_5d": _net(5, "frn_net_buy_amt"),
            "institution_net_amt_5d": _net(5, "inst_net_buy_amt"),
            "foreign_net_amt_20d": _net(20, "frn_net_buy_amt"),
            "institution_net_amt_20d": _net(20, "inst_net_buy_amt"),
        },
        "short_history": short_history,
        "credit_history": credit_history,
    }


def _condition_membership(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Return Hero4 condition membership history when the authenticated feed is connected.

    The table is intentionally optional: condition IDs belong to the user's Kiwoom
    account and must not be guessed or replaced with a hard-coded screening rule.
    """
    rows = _query_rows(conn, """
        SELECT condition_id, condition_name, event_type, event_at, source, captured_at
        FROM kiwoom_condition_membership
        WHERE stock_code=?
        ORDER BY event_at DESC
        LIMIT 100
    """, (code,))
    items = [dict(r) for r in rows]
    current = [dict(r) for r in _query_rows(conn, """
        SELECT condition_id, condition_name, detected_at, source
        FROM kiwoom_condition_current
        WHERE stock_code=?
        ORDER BY condition_name, condition_id
    """, (code,))]
    definition_count_rows = _query_rows(conn, "SELECT COUNT(*) AS count FROM kiwoom_condition_definition")
    definition_count = int(definition_count_rows[0]["count"] or 0) if definition_count_rows else 0
    return {
        "connected": definition_count > 0,
        "current": current,
        "events": items,
        "notice": (
            "키움 영웅문 조건식의 현재 편입 상태와 스냅샷 기반 편입·편출 이력입니다."
            if definition_count else
            "키움 영웅문 조건식 목록을 아직 읽지 못했습니다. 조건식이 영웅문4에 저장돼 있는지 확인하세요."
        ),
    }


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


@router.post("/conditions/snapshot")
def kiwoom_condition_snapshot(
    max_conditions: int = Query(100, ge=1, le=100, description="영웅문4 저장 조건식 중 조회할 최대 개수"),
):
    """Read saved Hero4 conditions and persist current members plus IN/OUT deltas."""
    return KiwoomCollector().collect_condition_snapshot(max_conditions=max_conditions)


@router.get("/conditions/status")
def kiwoom_condition_status():
    """Expose condition-search freshness without treating a hit as an order signal."""
    KiwoomCollector()._ensure_condition_membership_table()
    conn = connect_stock_db(timeout=10)
    try:
        definitions = conn.execute("SELECT COUNT(*) FROM kiwoom_condition_definition").fetchone()[0]
        current = conn.execute("SELECT COUNT(*) FROM kiwoom_condition_current").fetchone()[0]
        latest = conn.execute("SELECT MAX(detected_at) FROM kiwoom_condition_current").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM kiwoom_condition_membership").fetchone()[0]
        return {
            "ok": True,
            "condition_count": int(definitions or 0),
            "current_memberships": int(current or 0),
            "event_count": int(events or 0),
            "last_snapshot_at": latest,
            "notice": "키움 조건검색은 후보 발굴용이며, 단독 매수 신호나 자동 주문으로 사용하지 않습니다.",
        }
    finally:
        conn.close()


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


@router.post("/us/realtime/snapshot")
def kiwoom_us_realtime_snapshot(
    items: list[dict[str, str]] = Body(..., embed=True),
    types: str = Query("F5,FE,FT", description="미국 실시간 TR. 체결/체결가/10호가"),
    duration_sec: int = Query(12, ge=3, le=60),
):
    """Capture only explicit US candidates/positions using Kiwoom F5/FE/FT."""
    type_list = [value.strip().upper() for value in types.split(",") if value.strip()]
    allowed = {"F5", "FE", "FT"}
    type_list = [value for value in type_list if value in allowed]
    return KiwoomCollector().collect_us_realtime_snapshot(items, type_list, duration_sec)


@router.get("/us/realtime/{ticker}")
def kiwoom_us_realtime_context(ticker: str):
    """Expose raw-verified US realtime availability without pretending EOD is live."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return {"ok": False, "reason": "ticker is required"}
    conn = connect_stock_db(timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = _query_rows(conn, """
            SELECT ticker, exchange_code, source_types, updated_at
            FROM kiwoom_us_realtime_quote WHERE ticker=? LIMIT 1
        """, (symbol,))
        return {
            "ok": True, "ticker": symbol,
            "connected": bool(row),
            "quote": dict(row[0]) if row else None,
            "notice": "키움 미국 실시간 원본 이벤트가 수집되면 표시됩니다." if row else "키움 미국 실시간 미연결: 일별 종가 데이터와 구분합니다.",
        }
    finally:
        conn.close()


@router.post("/foreign-flow")
def kiwoom_foreign_flow(code: str = Query(..., description="종목코드 6자리")):
    kc = KiwoomCollector()
    return kc.fetch_foreign_flow(code.strip())


@router.post("/rankings/large-trades")
def kiwoom_large_trade_rank(
    market_type: str = Query("000", pattern="^(000|001|101)$", description="000 전체, 001 코스피, 101 코스닥"),
    rank_type: str = Query("buy", pattern="^(buy|sell)$"),
    min_case_amount: str = Query("10", description="키움 건별금액구분: 10=1억원 이상"),
    min_turnover: str = Query("0", description="키움 거래대금구분: 0=전체"),
):
    """ka00190 대량체결상위 원본 스냅샷 수집.

    이 호출은 주문과 무관하며, 수집 결과는 이후 KIS/공식 수급 대조 전까지
    연구·가상매매 보조 신호로만 취급됩니다.
    """
    return KiwoomCollector().fetch_large_trade_rank(
        market_type=market_type,
        rank_type=rank_type,
        min_case_amount=min_case_amount,
        min_turnover=min_turnover,
    )


@router.get("/rankings/large-trades/latest")
def kiwoom_latest_large_trade_rank(
    rank_type: str = Query("buy", pattern="^(buy|sell)$"),
    market_type: str = Query("000", pattern="^(000|001|101)$"),
    limit: int = Query(50, ge=1, le=100),
):
    """가장 최근 키움 대량체결 원본 스냅샷을 반환한다."""
    conn = connect_stock_db(timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute(
            """
            SELECT MAX(snapshot_at) AS snapshot_at
            FROM kiwoom_large_trade_rank
            WHERE rank_type=? AND market_type=?
            """,
            (rank_type, market_type),
        ).fetchone()
        snapshot_at = latest["snapshot_at"] if latest else None
        if not snapshot_at:
            return {
                "ok": True, "items": [], "snapshot_at": None,
                "notice": "키움 대량체결 스냅샷이 아직 없습니다. 수집 후 KIS 수급과 대조해 사용합니다.",
            }
        rows = conn.execute(
            """
            SELECT snapshot_at, rank_type, market_type, rank_no, stock_code, stock_name, raw_json
            FROM kiwoom_large_trade_rank
            WHERE snapshot_at=? AND rank_type=? AND market_type=?
            ORDER BY rank_no
            LIMIT ?
            """,
            (snapshot_at, rank_type, market_type, limit),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["raw"] = json.loads(item.pop("raw_json"))
            except (TypeError, ValueError):
                item["raw"] = {}
                item.pop("raw_json", None)
            items.append(item)
        return {
            "ok": True, "snapshot_at": snapshot_at, "items": items,
            "notice": "키움 대량체결 원본입니다. 단독 매수 신호가 아니며 KIS·공식 수급 대조 전에는 전략 편입하지 않습니다.",
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "items": []}
    finally:
        conn.close()


@router.get("/rankings/large-trades/confirmation")
def kiwoom_large_trade_kis_confirmation(
    rank_type: str = Query("buy", pattern="^(buy|sell)$"),
    market_type: str = Query("000", pattern="^(000|001|101)$"),
    limit: int = Query(50, ge=1, le=100),
):
    """Compare the latest Kiwoom large-trade rank with canonical KIS daily flow.

    A positive match only means that two independent feeds agree on attention
    and daily net buying.  It remains a research/paper-trading signal, never
    an execution instruction.
    """
    conn = connect_stock_db(timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        snapshot = conn.execute(
            """
            SELECT MAX(snapshot_at) AS snapshot_at
            FROM kiwoom_large_trade_rank
            WHERE rank_type=? AND market_type=?
            """,
            (rank_type, market_type),
        ).fetchone()
        snapshot_at = snapshot["snapshot_at"] if snapshot else None
        if not snapshot_at:
            return {
                "ok": True, "items": [], "snapshot_at": None,
                "decision_mode": "research_paper_only",
                "notice": "키움 대량체결 스냅샷이 아직 없어 KIS 수급 대조를 할 수 없습니다.",
            }
        rank_rows = conn.execute(
            """
            SELECT rank_no, stock_code, stock_name
            FROM kiwoom_large_trade_rank
            WHERE snapshot_at=? AND rank_type=? AND market_type=?
              AND stock_code IS NOT NULL AND stock_code != ''
            ORDER BY rank_no
            LIMIT ?
            """,
            (snapshot_at, rank_type, market_type, limit),
        ).fetchall()
        codes = [str(row["stock_code"]) for row in rank_rows]
        if not codes:
            return {
                "ok": True, "items": [], "snapshot_at": snapshot_at,
                "decision_mode": "research_paper_only",
                "notice": "키움 응답에 정규화 가능한 종목코드가 없어 원본만 보관했습니다. 장중 응답 필드 검증 후 대조합니다.",
            }
        marks = ",".join("?" for _ in codes)
        flow_rows = conn.execute(
            f"""
            SELECT stock_code, date, inst_net_buy_amt, frn_net_buy_amt
            FROM (
                SELECT stock_code, date, inst_net_buy_amt, frn_net_buy_amt,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC, id DESC) AS rn
                FROM price_history
                WHERE stock_code IN ({marks})
                  AND substr(date, 1, 10) <= ?
            ) latest
            WHERE rn=1
            """,
            codes + [str(snapshot_at)[:10]],
        ).fetchall()
        flow_map = {str(row["stock_code"]): dict(row) for row in flow_rows}
        items = []
        for rank_row in rank_rows:
            rank = dict(rank_row)
            flow = flow_map.get(str(rank["stock_code"]))
            inst_amt = _to_float(flow.get("inst_net_buy_amt")) if flow else None
            frn_amt = _to_float(flow.get("frn_net_buy_amt")) if flow else None
            combined_억 = ((inst_amt or 0) + (frn_amt or 0)) / 100.0 if flow else None
            confirmed = combined_억 is not None and combined_억 > 0
            items.append({
                **rank,
                "kis_flow_date": flow.get("date") if flow else None,
                "kis_inst_net_buy_억": round((inst_amt or 0) / 100.0, 1) if flow else None,
                "kis_frn_net_buy_억": round((frn_amt or 0) / 100.0, 1) if flow else None,
                "kis_combined_net_buy_억": round(combined_억, 1) if combined_억 is not None else None,
                "status": "confirmed_for_paper_review" if confirmed else "not_confirmed",
                "strategy_eligibility": "research_paper_only",
            })
        return {
            "ok": True, "snapshot_at": snapshot_at, "items": items,
            "decision_mode": "research_paper_only",
            "notice": "키움 대량체결과 KIS 일별 기관·외국인 순매수가 같은 방향일 때만 검토 표시합니다. 당일 장중 체결과 일별 수급의 시점 차이를 반드시 확인하세요.",
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "items": []}
    finally:
        conn.close()


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


@router.post("/conditions/events")
def kiwoom_condition_events(events: list[dict[str, Any]] = Body(..., embed=True)):
    """Ingest normalized Hero4 condition-search inclusion/removal events.

    This endpoint is deliberately data-only. A condition hit remains a research
    candidate and cannot create an order or alter a live trading setting.
    """
    return KiwoomCollector().record_condition_membership_events(events)


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

    conn = connect_stock_db(timeout=10)
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
        # ka10059의 현재 수집값은 매수금액(buy-only)으로 확인되어 순매수로
        # 사용할 수 없다. 종목 차트와 동일한 검증済 price_history 순매수를 쓴다.
        investor_rows = conn.execute(
            """
            SELECT date AS dt,
                   ind_net_buy,
                   frn_net_buy,
                   inst_net_buy,
                   ind_net_buy_amt,
                   frn_net_buy_amt,
                   inst_net_buy_amt
            FROM price_history
            WHERE stock_code=?
              AND (COALESCE(ind_net_buy, 0) != 0
                   OR COALESCE(frn_net_buy, 0) != 0
                   OR COALESCE(inst_net_buy, 0) != 0)
            ORDER BY date DESC
            LIMIT 5
            """,
            (code,),
        ).fetchall()
        program_rows = conn.execute(
            """
            SELECT *
            FROM broker_program_stock_daily
            WHERE source='kiwoom' AND stock_code=?
            ORDER BY dt DESC
            LIMIT 260
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
        flow_analysis = _stock_flow_history(conn, code)
        condition_membership = _condition_membership(conn, code)

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
                "period_start_dt": investor_rows[-1]["dt"],
                "trading_days": len(investor_rows),
                "individual_net_qty_5d": _sum(investor_rows, "ind_net_buy"),
                "foreign_net_qty_5d": _sum(investor_rows, "frn_net_buy"),
                "institution_net_qty_5d": _sum(investor_rows, "inst_net_buy"),
                "individual_net_amt_5d": _sum(investor_rows, "ind_net_buy_amt"),
                "foreign_net_amt_5d": _sum(investor_rows, "frn_net_buy_amt"),
                "institution_net_amt_5d": _sum(investor_rows, "inst_net_buy_amt"),
                "source": "price_history",
                "latest": investor_latest,
            }

        program_latest = _row_to_dict(program_rows[0]) if program_rows else None
        program_summary = None
        if program_rows:
            program_history = [_row_to_dict(row) for row in program_rows]
            program_summary = {
                "latest_dt": program_rows[0]["dt"],
                "latest": program_latest,
                "five_day_net_buy_qty": _sum(program_rows[:5], "net_buy_qty"),
                "five_day_net_buy_amt_krw": _sum(program_rows[:5], "net_buy_amt_krw"),
                "five_day_buy_qty": _sum(program_rows[:5], "buy_qty"),
                "five_day_sell_qty": _sum(program_rows[:5], "sell_qty"),
                "five_day_buy_amt_krw": _sum(program_rows[:5], "buy_amt_krw"),
                "five_day_sell_amt_krw": _sum(program_rows[:5], "sell_amt_krw"),
                "one_year_net_buy_qty": _sum(program_rows, "net_buy_qty"),
                "one_year_net_buy_amt_krw": _sum(program_rows, "net_buy_amt_krw"),
                "one_year_buy_qty": _sum(program_rows, "buy_qty"),
                "one_year_sell_qty": _sum(program_rows, "sell_qty"),
                "one_year_buy_amt_krw": _sum(program_rows, "buy_amt_krw"),
                "one_year_sell_amt_krw": _sum(program_rows, "sell_amt_krw"),
                "history_days": len(program_rows),
                "period_start_dt": program_rows[-1]["dt"],
                "history_1y": program_history,
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
            "flow_analysis": flow_analysis,
            "condition_membership": condition_membership,
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
    conn = connect_stock_db(timeout=10)
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
    conn = connect_stock_db(timeout=10)
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
