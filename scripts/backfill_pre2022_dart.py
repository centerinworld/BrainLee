#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path
import sys

import OpenDartReader

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from collectors.dart_collector import _parse_cf_df, _parse_fin_df  # noqa: E402

DB = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
OUT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch")
OUT.mkdir(exist_ok=True)


def conn():
    c = sqlite3.connect(DB, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


def target_codes(c: sqlite3.Connection, limit: int | None) -> list[str]:
    q = """
    WITH u AS (
      SELECT DISTINCT stock_code FROM stock_universe
      WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ),
    f AS (
      SELECT DISTINCT stock_code FROM financial_data
      WHERE is_annual=1 AND report_type='CFS' AND year IN (2020,2021)
    ),
    cf AS (
      SELECT DISTINCT stock_code FROM cash_flow_data
      WHERE is_annual=1 AND report_type='CFS' AND year IN (2020,2021)
    )
    SELECT u.stock_code
    FROM u
    LEFT JOIN f ON f.stock_code=u.stock_code
    LEFT JOIN cf ON cf.stock_code=u.stock_code
    WHERE f.stock_code IS NULL OR cf.stock_code IS NULL
    ORDER BY u.stock_code
    """
    rows = c.execute(q).fetchall()
    codes = [r["stock_code"] for r in rows]
    return codes[:limit] if limit else codes


def upsert_fin(c: sqlite3.Connection, code: str, year: int, m: dict):
    c.execute(
        """
        INSERT INTO financial_data (
          stock_code, year, quarter, is_annual, report_type, data_source,
          revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity,
          capital_stock, eps, bps, dps, cash, total_shares, depreciation_amortization
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code, year, quarter, is_annual) DO UPDATE SET
          report_type=COALESCE(financial_data.report_type, excluded.report_type),
          data_source=COALESCE(financial_data.data_source, excluded.data_source),
          revenue=CASE WHEN financial_data.revenue IS NULL OR financial_data.revenue=0 THEN excluded.revenue ELSE financial_data.revenue END,
          operating_profit=CASE WHEN financial_data.operating_profit IS NULL OR financial_data.operating_profit=0 THEN excluded.operating_profit ELSE financial_data.operating_profit END,
          net_income=CASE WHEN financial_data.net_income IS NULL OR financial_data.net_income=0 THEN excluded.net_income ELSE financial_data.net_income END,
          total_assets=CASE WHEN financial_data.total_assets IS NULL OR financial_data.total_assets=0 THEN excluded.total_assets ELSE financial_data.total_assets END,
          total_liabilities=CASE WHEN financial_data.total_liabilities IS NULL OR financial_data.total_liabilities=0 THEN excluded.total_liabilities ELSE financial_data.total_liabilities END,
          total_equity=CASE WHEN financial_data.total_equity IS NULL OR financial_data.total_equity=0 THEN excluded.total_equity ELSE financial_data.total_equity END,
          capital_stock=CASE WHEN financial_data.capital_stock IS NULL OR financial_data.capital_stock=0 THEN excluded.capital_stock ELSE financial_data.capital_stock END,
          eps=CASE WHEN financial_data.eps IS NULL OR financial_data.eps=0 THEN excluded.eps ELSE financial_data.eps END,
          bps=CASE WHEN financial_data.bps IS NULL OR financial_data.bps=0 THEN excluded.bps ELSE financial_data.bps END,
          dps=CASE WHEN financial_data.dps IS NULL OR financial_data.dps=0 THEN excluded.dps ELSE financial_data.dps END,
          cash=CASE WHEN financial_data.cash IS NULL OR financial_data.cash=0 THEN excluded.cash ELSE financial_data.cash END,
          total_shares=CASE WHEN financial_data.total_shares IS NULL OR financial_data.total_shares=0 THEN excluded.total_shares ELSE financial_data.total_shares END,
          depreciation_amortization=CASE WHEN financial_data.depreciation_amortization IS NULL OR financial_data.depreciation_amortization=0 THEN excluded.depreciation_amortization ELSE financial_data.depreciation_amortization END
        """,
        (
            code,
            year,
            4,
            1,
            "CFS",
            "dart",
            m.get("revenue"),
            m.get("operating_profit"),
            m.get("net_income"),
            m.get("total_assets"),
            m.get("total_liabilities"),
            m.get("total_equity"),
            m.get("capital_stock"),
            m.get("eps"),
            m.get("bps"),
            m.get("dps"),
            m.get("cash"),
            m.get("total_shares"),
            m.get("depreciation_amortization"),
        ),
    )


def upsert_cf(c: sqlite3.Connection, code: str, year: int, m: dict):
    c.execute(
        """
        INSERT INTO cash_flow_data (
          stock_code, year, quarter, is_annual, report_type, data_source,
          operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code, year, quarter, is_annual, report_type) DO UPDATE SET
          data_source=COALESCE(cash_flow_data.data_source, excluded.data_source),
          operating_cf=CASE WHEN cash_flow_data.operating_cf IS NULL OR cash_flow_data.operating_cf=0 THEN excluded.operating_cf ELSE cash_flow_data.operating_cf END,
          investing_cf=CASE WHEN cash_flow_data.investing_cf IS NULL OR cash_flow_data.investing_cf=0 THEN excluded.investing_cf ELSE cash_flow_data.investing_cf END,
          financing_cf=CASE WHEN cash_flow_data.financing_cf IS NULL OR cash_flow_data.financing_cf=0 THEN excluded.financing_cf ELSE cash_flow_data.financing_cf END,
          capex=CASE WHEN cash_flow_data.capex IS NULL OR cash_flow_data.capex=0 THEN excluded.capex ELSE cash_flow_data.capex END,
          cash_end=CASE WHEN cash_flow_data.cash_end IS NULL OR cash_flow_data.cash_end=0 THEN excluded.cash_end ELSE cash_flow_data.cash_end END,
          depreciation=CASE WHEN cash_flow_data.depreciation IS NULL OR cash_flow_data.depreciation=0 THEN excluded.depreciation ELSE cash_flow_data.depreciation END
        """,
        (
            code,
            year,
            4,
            1,
            "CFS",
            "dart",
            m.get("operating_cf"),
            m.get("investing_cf"),
            m.get("financing_cf"),
            m.get("capex"),
            m.get("cash_end"),
            m.get("depreciation"),
        ),
    )


def main():
    ap = argparse.ArgumentParser(description="Pre-2022 DART backfill (2020~2021 first)")
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--end-year", type=int, default=2021)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--apply", action="store_true", help="DB write mode (default: dry-run)")
    args = ap.parse_args()

    years = list(range(args.start_year, args.end_year + 1))
    db = conn()
    codes = target_codes(db, args.limit if args.limit > 0 else None)
    from dart_key_manager import RotatingOpenDartReader
    dart = RotatingOpenDartReader()

    ok = 0
    empty = 0
    errs = 0
    writes = 0
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = OUT / f"pre2022_dart_backfill_report_{ts}.csv"

    with open(report_path, "w", encoding="utf-8") as w:
        w.write("stock_code,year,status,source,has_fin,has_cf,error\n")
        for i, code in enumerate(codes, 1):
            for y in years:
                try:
                    df = dart.finstate_all(code, y, "11011", fs_div="CFS")
                    src = "CFS"
                    if df is None or df.empty:
                        df = dart.finstate_all(code, y, "11011", fs_div="OFS")
                        src = "OFS"
                    if df is None or df.empty:
                        empty += 1
                        w.write(f"{code},{y},empty,{src},0,0,\n")
                        continue
                    fin = _parse_fin_df(df, stock_code=code)
                    cf = _parse_cf_df(df, stock_code=code)
                    has_fin = int(any(v not in (None, 0, 0.0) for v in fin.values()))
                    has_cf = int(any(v not in (None, 0, 0.0) for v in cf.values()))
                    if args.apply:
                        upsert_fin(db, code, y, fin)
                        upsert_cf(db, code, y, cf)
                        writes += 2
                    ok += 1
                    w.write(f"{code},{y},ok,{src},{has_fin},{has_cf},\n")
                except Exception as e:
                    errs += 1
                    msg = str(e).replace(",", ";").replace("\n", " ")[:240]
                    w.write(f"{code},{y},error,,0,0,{msg}\n")
                time.sleep(args.sleep)
            if i % 100 == 0 and args.apply:
                db.commit()
                print(f"progress {i}/{len(codes)} writes={writes}")

    if args.apply:
        db.commit()
    db.close()
    print(
        {
            "codes": len(codes),
            "years": years,
            "ok_rows": ok,
            "empty_rows": empty,
            "error_rows": errs,
            "writes": writes,
            "apply_mode": bool(args.apply),
            "report_file": str(report_path),
        }
    )


if __name__ == "__main__":
    main()
