#!/usr/bin/env python3
"""Publish validated KRX/KIS ETF holdings as stock-level dashboard rows."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from db_utils import connect_stock_db
from etf_parity_cutover import THRESHOLDS
from full_pdf_collector import DB_PATH, connect


DOMESTIC_CODE = re.compile(r"^[0-9]{6}$")
MIN_SAMPLE_SIZE = 60
MAX_ISSUER_LAG_DAYS = 10


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_direct_stock_publication (
            base_date TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            status TEXT NOT NULL,
            universe_count INTEGER NOT NULL,
            successful_etf_count INTEGER NOT NULL,
            excepted_etf_count INTEGER NOT NULL,
            scale_count INTEGER NOT NULL,
            stock_count INTEGER NOT NULL,
            positive_stock_count INTEGER NOT NULL,
            price_count INTEGER NOT NULL,
            market_cap_count INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            source TEXT NOT NULL,
            details_json TEXT NOT NULL,
            published_at TEXT NOT NULL
        );
        """
    )


def _iso_day(day: str) -> str:
    return datetime.strptime(day, "%Y%m%d").date().isoformat()


def _issuer_exception(conn: sqlite3.Connection, day: str, ticker: str) -> dict | None:
    row = conn.execute(
        """
        SELECT effective_date,status,component_count,source
        FROM etf_pdf_issuer_fallback
        WHERE base_date=? AND etf_ticker=?
        """,
        (day, ticker),
    ).fetchone()
    if not row:
        return None
    effective = datetime.strptime(row[0], "%Y%m%d").date()
    lag_days = (datetime.strptime(day, "%Y%m%d").date() - effective).days
    components = conn.execute(
        """
        SELECT component_code FROM etf_pdf_issuer_component
        WHERE base_date=? AND etf_ticker=?
        """,
        (day, ticker),
    ).fetchall()
    domestic = [str(item[0]) for item in components if DOMESTIC_CODE.fullmatch(str(item[0]))]
    if lag_days < 0 or lag_days > MAX_ISSUER_LAG_DAYS or domestic:
        return None
    return {
        "etf_ticker": ticker,
        "source": row[3],
        "effective_date": row[0],
        "lag_days": lag_days,
        "component_count": int(row[2]),
        "domestic_components": domestic,
    }


def _quality_gate(conn: sqlite3.Connection, day: str) -> dict:
    universe = int(conn.execute(
        "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?", (day,)
    ).fetchone()[0])
    rows = conn.execute(
        """
        SELECT etf_ticker,status FROM etf_pdf_full_snapshot
        WHERE base_date=? ORDER BY etf_ticker
        """,
        (day,),
    ).fetchall()
    successful = sum(row[1] == "success" for row in rows)
    scale = int(conn.execute(
        "SELECT COUNT(*) FROM etf_scale_daily WHERE base_date=?", (day,)
    ).fetchone()[0])
    exceptions = []
    unresolved = []
    for ticker, status in rows:
        if status == "success":
            continue
        exception = _issuer_exception(conn, day, ticker)
        if exception:
            exceptions.append(exception)
        else:
            unresolved.append({"etf_ticker": ticker, "status": status})

    sample = conn.execute(
        """
        SELECT COUNT(*) attempted,SUM(status='success') successes
        FROM etfcheck_k_sample_daily WHERE base_date=?
        """,
        (day,),
    ).fetchone()
    parity = conn.execute(
        """
        SELECT membership_jaccard,count_within_one_ratio,amount_correlation,
               amount_total_ratio,amount_median_smape
        FROM etf_source_parity_daily WHERE base_date=?
        """,
        (day,),
    ).fetchone()
    failures = []
    if not universe or len(rows) != universe:
        failures.append("pdf_snapshot_coverage")
    if successful + len(exceptions) != universe or unresolved:
        failures.append("domestic_membership_unresolved")
    if scale != universe:
        failures.append("scale_coverage")
    attempted = int(sample[0] or 0) if sample else 0
    sample_success = int(sample[1] or 0) if sample else 0
    if attempted < MIN_SAMPLE_SIZE or sample_success != attempted:
        failures.append("sample_coverage")
    if not parity:
        failures.append("parity_missing")
    else:
        metric_names = (
            "membership_jaccard",
            "count_within_one_ratio",
            "amount_correlation",
        )
        for index, name in enumerate(metric_names):
            if parity[index] is None or float(parity[index]) < THRESHOLDS[name]:
                failures.append(name)
        ratio = parity[3]
        if ratio is None or not (
            THRESHOLDS["amount_total_ratio_min"]
            <= float(ratio)
            <= THRESHOLDS["amount_total_ratio_max"]
        ):
            failures.append("amount_total_ratio")
        smape = parity[4]
        if smape is None or float(smape) > THRESHOLDS["amount_median_smape_max"]:
            failures.append("amount_median_smape")
    return {
        "universe": universe,
        "snapshots": len(rows),
        "successful": successful,
        "scale": scale,
        "sample": attempted,
        "sample_success": sample_success,
        "exceptions": exceptions,
        "unresolved": unresolved,
        "failures": failures,
    }


def _load_market_data(day: str) -> dict[str, dict]:
    trade_date = _iso_day(day)
    conn = connect_stock_db()
    try:
        rows = conn.execute(
            """
            SELECT u.stock_code,u.stock_name,u.market,u.secugrp_nm,u.shares_issued,
                   p.close AS current_price,p.date AS price_date,
                   u.market_cap AS fallback_market_cap
            FROM stock_universe u
            LEFT JOIN LATERAL (
                SELECT date,close FROM price_history p
                WHERE p.stock_code=u.stock_code AND p.date<=?
                ORDER BY p.date DESC LIMIT 1
            ) p ON TRUE
            WHERE LENGTH(u.stock_code)=6
            """,
            (trade_date,),
        ).fetchall()
        return {str(row[0]): dict(row) for row in rows}
    finally:
        conn.close()


def publish(day: str, db_path: Path = DB_PATH) -> dict:
    conn = connect(db_path)
    initialize(conn)
    gate = _quality_gate(conn, day)
    if gate["failures"]:
        conn.close()
        raise RuntimeError(json.dumps(gate, ensure_ascii=False))

    direct = {
        str(row[0]): {"etf_count": int(row[1]), "etf_amount": float(row[2] or 0)}
        for row in conn.execute(
            """
            SELECT c.component_code,COUNT(DISTINCT c.etf_ticker),
                   SUM(c.valuation_amount*d.scale_factor)/100000000.0
            FROM etf_pdf_full_component c
            JOIN etf_pdf_full_snapshot s
              ON s.base_date=c.base_date AND s.etf_ticker=c.etf_ticker
            JOIN etf_scale_daily d
              ON d.base_date=c.base_date AND d.etf_ticker=c.etf_ticker
            WHERE c.base_date=? AND s.status='success'
              AND c.is_domestic_stock=1 AND c.valuation_amount>=0
            GROUP BY c.component_code
            """,
            (day,),
        )
    }
    market = _load_market_data(day)
    metadata = conn.execute(
        "SELECT stock_code,stock_name,market,secugrp_nm FROM etf_stock_meta ORDER BY stock_code"
    ).fetchall()
    if len(metadata) < 2500:
        conn.close()
        raise RuntimeError(f"stock metadata incomplete: {len(metadata)}")

    trade_date = _iso_day(day)
    records = []
    price_count = 0
    market_cap_count = 0
    for code, name, _market_name, _secugrp in metadata:
        code = str(code)
        holding = direct.get(code, {"etf_count": 0, "etf_amount": 0.0})
        quote = market.get(code, {})
        price = float(quote.get("current_price") or 0)
        shares = float(quote.get("shares_issued") or 0)
        market_cap = price * shares / 100000000.0 if price > 0 and shares > 0 else 0.0
        if market_cap <= 0:
            market_cap = float(quote.get("fallback_market_cap") or 0)
        if price > 0:
            price_count += 1
        if market_cap > 0:
            market_cap_count += 1
        ratio = holding["etf_amount"] * 100.0 / market_cap if market_cap > 0 else 0.0
        records.append(
            (
                trade_date,
                code,
                quote.get("stock_name") or name,
                round(holding["etf_amount"], 4),
                price or None,
                market_cap or None,
                round(ratio, 4),
                holding["etf_count"],
                "K-ETF",
                0,
            )
        )

    positive = sum(item[3] > 0 for item in records)
    now = datetime.now().isoformat(timespec="seconds")
    details = {
        **gate,
        "price_coverage": price_count / len(records),
        "market_cap_coverage": market_cap_count / len(records),
        "aggregation": "KRX_MDCSTAT05001 valuation_amount * KIS listed_shares/CU",
    }
    with conn:
        conn.execute(
            "DELETE FROM etf_inclusion_daily WHERE trade_date=? AND scope_label='K-ETF'",
            (trade_date,),
        )
        conn.executemany(
            """
            INSERT INTO etf_inclusion_daily(
                trade_date,stock_code,stock_name,etf_amount,current_price,
                market_cap,mktcap_ratio,etf_count,scope_label,is_backfilled
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            records,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_direct_stock_publication VALUES(
                ?,?,'published',?,?,?,?,?,?,?,?,?,'KRX_PDF_KIS_SCALE',?,?
            )
            """,
            (
                day,trade_date,gate["universe"],gate["successful"],
                len(gate["exceptions"]),gate["scale"],len(records),positive,
                price_count,market_cap_count,gate["sample"],
                json.dumps(details,ensure_ascii=False),now,
            ),
        )
    conn.close()
    return {
        "base_date": day,
        "trade_date": trade_date,
        "published": len(records),
        "positive": positive,
        "price_coverage": price_count / len(records),
        "market_cap_coverage": market_cap_count / len(records),
        "exceptions": gate["exceptions"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    print(json.dumps(publish(args.date, Path(args.db)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
