#!/usr/bin/env python3
"""Seed issuer/SEC-verified US merger and delisting consideration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


# ticker, effective date, type, cash/share, successor, successor ratio, CVR omitted, source
VERIFIED_OUTCOMES = (
    ("ABMD", "2022-12-22", "cash_acquisition", 380.0, None, 0.0, 1,
     "https://www.jnj.com/media-center/press-releases/johnson-johnson-completes-acquisition-of-abiomed"),
    ("ATVI", "2023-10-13", "cash_acquisition", 95.0, None, 0.0, 0,
     "https://news.microsoft.com/source/2022/01/18/microsoft-to-acquire-activision-blizzard-to-bring-the-joy-and-community-of-gaming-to-everyone-across-every-device/"),
    ("CERN", "2022-06-08", "cash_acquisition", 95.0, None, 0.0, 0,
     "https://www.oracle.com/at/corporate/acquisitions/cerner/"),
    ("DISH", "2023-12-31", "stock_merger", 0.0, "SATS", 0.350877, 0,
     "https://ir.echostar.com/news-releases/news-release-details/echostar-corporation-completes-merger-dish-network-corporation"),
    ("DRE", "2022-10-03", "stock_merger", 0.0, "PLD", 0.475, 0,
     "https://investor.prologis.com/financials/sec-filings/content/0000950170-24-014539/0000950170-24-014539.pdf"),
    ("INFO", "2022-02-28", "stock_merger", 0.0, "SPGI", 0.2838, 0,
     "https://investor.spglobal.com/shareholder-services/Exchange-of-Shares-IHS-Markit-shareholders/"),
    ("XLNX", "2022-02-14", "stock_merger", 0.0, "AMD", 1.7234, 0,
     "https://ir.amd.com/financial-information/sec-filings/content/0000002488-22-000031/amd-20220214.htm"),
)


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_security_outcomes (
        ticker TEXT NOT NULL, effective_date TEXT NOT NULL, outcome_type TEXT NOT NULL,
        cash_per_share DOUBLE PRECISION NOT NULL DEFAULT 0,
        successor_ticker TEXT, successor_shares_per_share DOUBLE PRECISION NOT NULL DEFAULT 0,
        contingent_value_unmodeled INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL, source_url TEXT NOT NULL, note TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY(ticker,effective_date))""")


def apply() -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_primary_db(timeout=120)
    try:
        ensure_schema(conn)
        conn.executemany("""INSERT INTO us_security_outcomes
            (ticker,effective_date,outcome_type,cash_per_share,successor_ticker,
             successor_shares_per_share,contingent_value_unmodeled,status,source_url,note,updated_at)
            VALUES(?,?,?,?,?,?,?,'verified',?,'Issuer/SEC verified consideration',?)
            ON CONFLICT(ticker,effective_date) DO UPDATE SET
              outcome_type=excluded.outcome_type,cash_per_share=excluded.cash_per_share,
              successor_ticker=excluded.successor_ticker,
              successor_shares_per_share=excluded.successor_shares_per_share,
              contingent_value_unmodeled=excluded.contingent_value_unmodeled,
              status=excluded.status,source_url=excluded.source_url,note=excluded.note,
              updated_at=excluded.updated_at""", [(*x, now) for x in VERIFIED_OUTCOMES])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        count = verify.execute("SELECT COUNT(*) FROM us_security_outcomes WHERE status='verified'").fetchone()[0]
    finally:
        verify.close()
    if count < len(VERIFIED_OUTCOMES):
        raise RuntimeError("outcome read-back count mismatch")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print({"verified_outcomes": apply()})
    else:
        print({"verified_outcomes": len(VERIFIED_OUTCOMES), "apply": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
