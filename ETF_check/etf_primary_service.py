"""Read ETF stock summaries from the direct KRX/KIS pipeline after gated cutover."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from full_pdf_collector import DB_PATH, connect


def source_mode(db_path:Path=DB_PATH) -> str:
    conn=connect(db_path)
    try:
        row=conn.execute("SELECT mode FROM etf_source_control WHERE control_id=1").fetchone()
        return row[0] if row else "legacy_validation"
    except sqlite3.OperationalError:
        return "legacy_validation"
    finally:
        conn.close()


def direct_summary(stock_code:str,db_path:Path=DB_PATH) -> dict:
    conn=connect(db_path); conn.row_factory=sqlite3.Row
    day=conn.execute(
        """
        SELECT MAX(u.base_date)
        FROM (
            SELECT base_date,COUNT(*) universe_count
            FROM etf_universe_daily GROUP BY base_date
        ) u
        JOIN etf_pdf_full_publication p
          ON p.base_date=u.base_date AND p.universe_count=u.universe_count
        WHERE (
            SELECT COUNT(*) FROM etf_pdf_full_snapshot s
            WHERE s.base_date=u.base_date AND s.status='success'
        )=u.universe_count
          AND (
            SELECT COUNT(*) FROM etf_scale_daily d
            WHERE d.base_date=u.base_date
        )=u.universe_count
        """
    ).fetchone()[0]
    if not day:
        conn.close(); raise RuntimeError("direct ETF snapshot unavailable")
    rows=[dict(row) for row in conn.execute(
        """
        SELECT c.etf_ticker,s.etf_name,c.weight,
               c.valuation_amount*d.scale_factor/100000000.0 amount_100m
        FROM etf_pdf_full_component c
        JOIN etf_pdf_full_snapshot s ON s.base_date=c.base_date AND s.etf_ticker=c.etf_ticker
        JOIN etf_scale_daily d ON d.base_date=c.base_date AND d.etf_ticker=c.etf_ticker
        WHERE c.base_date=? AND c.component_code=? AND s.status='success'
        ORDER BY amount_100m DESC
        """,(day,stock_code)
    )]
    conn.close()
    top_ratio=max(rows,key=lambda r:r["weight"] or -1,default=None)
    top_amount=rows[0] if rows else None
    items=[]
    if top_ratio:
        items.append({"label":"비중 1위","name":top_ratio["etf_name"],"value":f"{top_ratio['weight']:.2f}%" if top_ratio["weight"] is not None else None,"type":"ratio"})
    if top_amount:
        items.append({"label":"편입금액 1위","name":top_amount["etf_name"],"value":f"{top_amount['amount_100m']:,.0f}억","type":"amount"})
    return {"stock_code":stock_code,"stock_name":None,"etf_count":len(rows),"etf_amount_total":sum(row["amount_100m"] or 0 for row in rows),"etf_list":items,"note":f"국내 ETF {len(rows)}개 편입 (KRX/KIS 자체 수집, 기준 {day})","source":"KRX_KIS_DIRECT","base_date":day}
