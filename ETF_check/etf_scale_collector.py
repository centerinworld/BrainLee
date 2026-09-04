"""Collect daily ETF CU metadata needed to scale KRX PDF values to fund totals."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from direct_etf_pipeline import COMPONENT_PATH, KISETFSource, trading_date
from full_pdf_collector import DB_PATH, connect


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_scale_daily (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            listed_shares REAL NOT NULL,
            cu_quantity REAL NOT NULL,
            scale_factor REAL NOT NULL,
            close_price REAL,
            nav REAL,
            net_asset_total_raw REAL,
            expected_component_count INTEGER,
            source TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY(base_date,etf_ticker)
        );
        CREATE TABLE IF NOT EXISTS etf_scale_collection_run (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_date TEXT NOT NULL,
            universe_count INTEGER NOT NULL,
            success_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        );
        """
    )


def number(value):
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def collect(day: str, db_path: Path = DB_PATH, delay: float = 0.12) -> dict:
    conn = connect(db_path)
    initialize(conn)
    universe = conn.execute(
        """
        SELECT u.etf_ticker,m.listed_shares
        FROM etf_universe_daily u JOIN etf_meta m USING(etf_ticker)
        WHERE u.base_date=? ORDER BY u.etf_ticker
        """,
        (day,),
    ).fetchall()
    if not universe:
        raise RuntimeError(f"No pinned ETF universe for {day}")
    source = KISETFSource(delay=delay,retries=3)
    started = datetime.now().isoformat(timespec="seconds")
    success,errors = 0,[]
    for ticker,listed_shares in universe:
        try:
            payload = source.get(
                COMPONENT_PATH,"FHKST121600C0",
                {"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":ticker,
                 "FID_COND_SCR_DIV_CODE":"11216"},
            )
            info = payload.get("output1") or {}
            cu = number(info.get("etf_cu_unit_scrt_cnt"))
            listed = number(listed_shares)
            if not cu or not listed or cu <= 0 or listed <= 0:
                raise RuntimeError(f"invalid scale inputs listed={listed}, cu={cu}")
            expected = number(info.get("etf_cnfg_issu_cnt"))
            with conn:
                conn.execute(
                    """
                    INSERT INTO etf_scale_daily VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(base_date,etf_ticker) DO UPDATE SET
                        listed_shares=excluded.listed_shares,
                        cu_quantity=excluded.cu_quantity,
                        scale_factor=excluded.scale_factor,
                        close_price=excluded.close_price,nav=excluded.nav,
                        net_asset_total_raw=excluded.net_asset_total_raw,
                        expected_component_count=excluded.expected_component_count,
                        source=excluded.source,collected_at=excluded.collected_at
                    """,
                    (
                        day,ticker,listed,cu,listed/cu,number(info.get("stck_prpr")),
                        number(info.get("nav")),number(info.get("etf_ntas_ttam")),
                        int(expected) if expected is not None else None,
                        "KIS_FHKST121600C0",datetime.now().isoformat(timespec="seconds"),
                    ),
                )
            success += 1
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"ticker":ticker,"error":str(exc)})
        time.sleep(max(delay,0))
    result = {
        "base_date":day,"universe":len(universe),"success":success,
        "errors":len(universe)-success,"error_samples":errors,
    }
    with conn:
        conn.execute(
            "INSERT INTO etf_scale_collection_run VALUES(NULL,?,?,?,?,?,?,?)",
            (
                day,len(universe),success,len(universe)-success,
                json.dumps(result,ensure_ascii=False),started,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    conn.close()
    return result


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--db",default=str(DB_PATH))
    parser.add_argument("--delay",type=float,default=0.12)
    args=parser.parse_args()
    day=trading_date(args.date)
    print(json.dumps(collect(day,Path(args.db),args.delay),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
