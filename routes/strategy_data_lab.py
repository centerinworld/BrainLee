"""Strategy Center data-routing API.

This router deliberately keeps new data-driven ideas separate from the verified
backtest matrix.  It answers three operational questions for the Strategy
Center: which sources are fresh enough to use, what role each source plays,
and which stocks have multiple independent confirmations.
"""

from __future__ import annotations

from datetime import date, datetime
import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from db_compat import connect_primary_db


router = APIRouter()


SOURCE_META = (
    {
        "key": "earnings",
        "table": "earnings_signals",
        "date_column": "created_at",
        "role": "entry",
        "label": "실적 변곡",
        "description": "흑자전환·매출 고성장·이익가속을 신규 진입의 출발 신호로 사용합니다.",
        "max_age_days": 120,
    },
    {
        "key": "contract_advance",
        "table": "contract_advance_signals",
        "date_column": "fiscal_year || '-Q' || fiscal_quarter",
        "role": "confirmation",
        "label": "계약선행",
        "description": "계약부채·선수금 증가로 매출 인식 이전의 고객 자금 유입을 확인합니다.",
        "max_age_days": 180,
    },
    {
        "key": "cash_conversion",
        "table": "cash_conversion_signals",
        "date_column": "fiscal_year || '-Q' || fiscal_quarter",
        "role": "confirmation",
        "label": "현금전환",
        "description": "이익이 실제 영업현금흐름으로 전환되는지 확인해 회계상 착시를 줄입니다.",
        "max_age_days": 180,
    },
    {
        "key": "inventory_sales",
        "table": "inventory_sales_signals",
        "date_column": "fiscal_year || '-Q' || fiscal_quarter",
        "role": "risk_gate",
        "label": "재고·매출",
        "description": "재고 축적 위험은 차단하고, 재고 소진·매출 증가 조합은 보조 확인으로 사용합니다.",
        "max_age_days": 180,
    },
    {
        "key": "order_contracts",
        "table": "order_contracts",
        "date_column": "rcept_dt",
        "role": "catalyst",
        "label": "수주 공시",
        "description": "매출 대비 의미 있는 신규 수주를 촉매로 사용합니다. 단독 진입 근거로는 쓰지 않습니다.",
        "max_age_days": 120,
    },
    {
        "key": "consensus",
        "table": "consensus_targets",
        "date_column": "report_date",
        "role": "confirmation",
        "label": "컨센서스 상향",
        "description": "목표주가 상향은 실적·수주 신호 뒤의 시장 재평가 확인용으로만 사용합니다.",
        "max_age_days": 90,
    },
)


def _conn():
    return connect_primary_db(timeout=20, row_factory=sqlite3.Row, readonly=True)


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def _one(conn, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _rows(conn, sql, params)
    return rows[0] if rows else None


def _parse_as_of(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text or "-Q" in text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _source_status(conn, meta: dict[str, Any]) -> dict[str, Any]:
    table = meta["table"]
    date_column = meta["date_column"]
    row = _one(
        conn,
        f"SELECT COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks, MAX({date_column}) AS as_of FROM {table}",
    ) or {"rows": 0, "stocks": 0, "as_of": None}
    as_of_date = _parse_as_of(row.get("as_of"))
    age_days = (date.today() - as_of_date).days if as_of_date else None
    # Quarterly sources expose fiscal period rather than a publication date.
    freshness = "periodic" if as_of_date is None and row.get("as_of") else (
        "unavailable" if not row.get("rows") else "fresh" if age_days is not None and age_days <= meta["max_age_days"] else "stale"
    )
    return {
        **meta,
        "rows": int(row.get("rows") or 0),
        "stocks": int(row.get("stocks") or 0),
        "as_of": row.get("as_of"),
        "age_days": age_days,
        "freshness": freshness,
    }


def _latest_per_stock(conn, table: str, columns: str, where: str = "1=1") -> dict[str, dict[str, Any]]:
    rows = _rows(conn, f"""
        WITH ranked AS (
            SELECT {columns},
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code
                       ORDER BY fiscal_year DESC, fiscal_quarter DESC, updated_at DESC
                   ) AS rn
            FROM {table}
            WHERE {where} AND length(stock_code)=6
        )
        SELECT * FROM ranked WHERE rn=1
    """)
    return {str(row["stock_code"]): row for row in rows}


def _candidates(conn, limit: int) -> dict[str, list[dict[str, Any]]]:
    advance = _latest_per_stock(
        conn, "contract_advance_signals",
        "stock_code, stock_name, signal_score, quality_flag, fiscal_year, fiscal_quarter, updated_at",
        "quality_flag='ok'",
    )
    cash = _latest_per_stock(
        conn, "cash_conversion_signals",
        "stock_code, stock_name, signal_score, risk_score, signal_type, quality_flag, fiscal_year, fiscal_quarter, updated_at",
        "quality_flag='ok'",
    )
    inventory = _latest_per_stock(
        conn, "inventory_sales_signals",
        "stock_code, stock_name, signal_score, risk_score, signal_type, quality_flag, fiscal_year, fiscal_quarter, updated_at",
        "quality_flag='ok'",
    )
    earnings_rows = _rows(conn, """
        WITH ranked AS (
            SELECT stock_code, stock_name, signal_type, created_at,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY created_at DESC) AS rn
            FROM earnings_signals
            WHERE COALESCE(is_active, 1)=1 AND length(stock_code)=6
        )
        SELECT * FROM ranked WHERE rn=1
    """)
    earnings = {str(row["stock_code"]): row for row in earnings_rows}
    order_rows = _rows(conn, """
        WITH ranked AS (
            SELECT stock_code, stock_name, rcept_dt, revenue_ratio_pct,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY rcept_dt DESC) AS rn
            FROM order_contracts
            WHERE COALESCE(is_termination, 0)=0 AND length(stock_code)=6
        )
        SELECT * FROM ranked WHERE rn=1
    """)
    orders = {str(row["stock_code"]): row for row in order_rows}
    consensus_rows = _rows(conn, """
        WITH ranked AS (
            SELECT stock_code, stock_name, report_date, target_price, prev_target_price,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY report_date DESC, id DESC) AS rn
            FROM consensus_targets
            WHERE target_price > 0 AND prev_target_price > 0 AND length(stock_code)=6
        )
        SELECT * FROM ranked WHERE rn=1
    """)
    consensus = {str(row["stock_code"]): row for row in consensus_rows}

    codes = set(advance) | set(cash) | set(inventory) | set(earnings) | set(orders) | set(consensus)
    rows: list[dict[str, Any]] = []
    for code in codes:
        a, c, i = advance.get(code, {}), cash.get(code, {}), inventory.get(code, {})
        e, o, co = earnings.get(code, {}), orders.get(code, {}), consensus.get(code, {})
        advance_good = int((a.get("signal_score") or 0) >= 4)
        cash_good = int(c.get("signal_type") == "cash_quality" and (c.get("signal_score") or 0) >= 4)
        inventory_good = int(i.get("signal_type") in ("build_up", "digestion") and (i.get("signal_score") or 0) >= 4)
        inventory_risk = int((i.get("risk_score") or 0) >= 4 or (c.get("risk_score") or 0) >= 4)
        earnings_good = int(bool(e))
        order_good = int((o.get("revenue_ratio_pct") or 0) >= 10)
        revision_pct = ((float(co.get("target_price") or 0) / float(co.get("prev_target_price") or 1)) - 1.0) * 100 if co else 0.0
        revision_good = int(revision_pct >= 5.0)
        score = advance_good * 2 + cash_good + inventory_good + earnings_good * 2 + order_good + revision_good - inventory_risk * 3
        name = next((x.get("stock_name") for x in (a, c, i, e, o, co) if x.get("stock_name")), code)
        signals = []
        if earnings_good: signals.append("실적 변곡")
        if advance_good: signals.append("계약선행")
        if cash_good: signals.append("현금전환")
        if inventory_good: signals.append("재고소진/매출")
        if order_good: signals.append("대형수주")
        if revision_good: signals.append(f"목표가 +{revision_pct:.0f}%")
        if inventory_risk: signals.append("재고/현금 위험")
        if score <= 0 or not signals:
            continue
        rows.append({
            "stock_code": code, "stock_name": name, "score": score,
            "signals": signals, "risk": bool(inventory_risk),
            "earnings_type": e.get("signal_type"), "revision_pct": round(revision_pct, 1) if co else None,
            "has_order": bool(order_good), "fiscal_period": next((f"{x.get('fiscal_year')}Q{x.get('fiscal_quarter')}" for x in (a, c, i) if x.get("fiscal_year")), None),
        })
    rows.sort(key=lambda row: (row["risk"], -row["score"], row["stock_code"]))
    return {
        "catalyst": [row for row in rows if row["score"] >= 4 and not row["risk"]][:limit],
        "revision": [row for row in rows if row["revision_pct"] is not None and row["revision_pct"] >= 5 and not row["risk"]][:limit],
        "watch": rows[:limit],
    }


@router.get("/overview")
def get_strategy_data_lab(limit: int = Query(default=15, ge=5, le=50)):
    """Return source health, data roles, and unverified multi-signal research ideas."""
    conn = _conn()
    try:
        sources = [_source_status(conn, meta) for meta in SOURCE_META]
        candidates = _candidates(conn, limit)
    finally:
        conn.close()
    strategies = [
        {
            "key": "catalyst_fundamental",
            "label": "V-CATALYST 펀더멘털 촉매",
            "status": "research_only",
            "entry": "실적 변곡 + 계약선행/현금전환/의미 있는 수주 중 2개 이상",
            "risk_gate": "재고 또는 현금전환 위험 점수 4점 이상은 제외",
            "note": "독립 6기간 백테스트와 전방 검증 전에는 자동매매·성과 매트릭스에 포함하지 않습니다.",
        },
        {
            "key": "earnings_revision",
            "label": "V-REVISION 실적·컨센서스 재평가",
            "status": "research_only",
            "entry": "실적 변곡 이후 목표주가 상향 5% 이상으로 시장의 재평가를 확인",
            "risk_gate": "컨센서스만 있는 종목은 제외하고 실적 또는 수주 근거가 필요",
            "note": "컨센서스는 후행 확인 신호라서 단독 진입 전략으로 쓰지 않습니다.",
        },
        {
            "key": "quality_route",
            "label": "V-QUALITY-ROUTE 데이터 역할 조합",
            "status": "research_only",
            "entry": "가격·수급 전략의 후보에 펀더멘털 확인 신호를 순서대로 부착",
            "risk_gate": "신선도 부족 데이터는 점수에 반영하지 않고 화면에서 보류로 표시",
            "note": "기존 V1~V3를 대체하지 않고, 각 전략의 진입·확인·위험제거 역할을 분리합니다.",
        },
    ]
    return {
        "as_of": date.today().isoformat(),
        "sources": sources,
        "strategies": strategies,
        "candidates": candidates,
        "disclaimer": "연구 후보입니다. 검증된 성과나 매수 추천이 아니며, 자동매매 연결은 차단되어 있습니다.",
    }
