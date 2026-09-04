#!/usr/bin/env python3
"""Backfill one reported half-year period from OpenDART into PostgreSQL.

The DART all-accounts API exposes standalone Q2 income in ``thstrm_amount``
and half-year cumulative income in ``thstrm_add_amount``.  The canonical
``financial_data`` quarterly rows use the standalone value.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.dart_collector import _parse_cf_df, _parse_fin_df
from dart_key_manager import RotatingOpenDartReader, get_dart_api_keys
from database import engine

LOG = logging.getLogger("dart_q2_backfill")
REPORT_CODE = "11012"


def eligible_filed_codes(year: int) -> list[str]:
    period = f"%({year}.06)%"
    sql = text("""
        WITH latest_universe AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY stock_code ORDER BY base_date DESC NULLS LAST, id DESC
            ) rn
            FROM stock_universe
        ), eligible AS (
            SELECT stock_code
            FROM latest_universe u
            WHERE rn = 1
              AND COALESCE(stock_type, '보통주') = '보통주'
              AND market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              AND stock_code ~ '^[0-9]{6}$'
              AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
              AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
              AND EXISTS (
                  SELECT 1 FROM price_history p
                  WHERE p.stock_code=u.stock_code
                    AND p.date::date >= (
                        SELECT MAX(date::date)-INTERVAL '30 days' FROM price_history
                    )
              )
        )
        SELECT DISTINCT d.stock_code
        FROM dart_disclosures d JOIN eligible e USING (stock_code)
        WHERE d.report_nm LIKE '%반기보고서%'
          AND d.report_nm LIKE :period
        ORDER BY d.stock_code
    """)
    with engine.connect() as conn:
        return [str(row[0]).zfill(6) for row in conn.execute(sql, {"period": period})]


def verified_codes(year: int) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT stock_code FROM financial_data
            WHERE year=:year AND quarter=2 AND is_annual=FALSE
              AND data_source='dart_q2_verified'
        """), {"year": year})
        return {str(row[0]).zfill(6) for row in rows}


def balance_identity_failure_codes(year: int) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT stock_code FROM financial_data
            WHERE year=:year AND quarter=2 AND is_annual=FALSE
              AND data_source='dart_q2_verified'
              AND total_assets IS NOT NULL
              AND total_liabilities IS NOT NULL
              AND total_equity IS NOT NULL
              AND ABS(total_assets-total_liabilities-total_equity)
                  > GREATEST(ABS(total_assets)*0.02, 1000000)
        """), {"year": year})
        return {str(row[0]).zfill(6) for row in rows}


def core_null_codes(year: int) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT stock_code FROM financial_data
            WHERE year=:year AND quarter=2 AND is_annual=FALSE
              AND data_source='dart_q2_verified'
              AND (revenue IS NULL OR net_income IS NULL
                   OR total_assets IS NULL OR total_equity IS NULL)
        """), {"year": year})
        return {str(row[0]).zfill(6) for row in rows}


def _upsert_financial(conn, code: str, year: int, report_type: str, values: dict) -> None:
    params = {
        "code": code, "year": year, "report_type": report_type,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **{k: values.get(k) for k in (
            "revenue", "operating_profit", "net_income", "total_assets",
            "total_liabilities", "total_equity", "capital_stock", "eps",
            "bps", "dps", "cash", "total_shares", "depreciation_amortization",
        )},
    }
    row_id = conn.execute(text("""
        SELECT id FROM financial_data
        WHERE stock_code=:code AND year=:year AND quarter=2
          AND is_annual=FALSE AND report_type=:report_type
        ORDER BY CASE
          WHEN data_source='dart_q2_verified' THEN 0
          WHEN data_source IS NULL THEN 1
          WHEN data_source='dart' THEN 2 ELSE 3 END, id DESC
        LIMIT 1
    """), params).scalar()
    if row_id is None:
        conn.execute(text("""
            INSERT INTO financial_data
              (stock_code,year,quarter,is_annual,report_type,data_source,created_at,
               revenue,operating_profit,net_income,total_assets,total_liabilities,
               total_equity,capital_stock,eps,bps,dps,cash,total_shares,
               depreciation_amortization)
            VALUES
              (:code,:year,2,FALSE,:report_type,'dart_q2_verified',:created_at,
               :revenue,:operating_profit,:net_income,:total_assets,:total_liabilities,
               :total_equity,:capital_stock,:eps,:bps,:dps,:cash,:total_shares,
               :depreciation_amortization)
        """), params)
    else:
        params["row_id"] = row_id
        conn.execute(text("""
            UPDATE financial_data SET
              data_source='dart_q2_verified', created_at=:created_at,
              revenue=:revenue, operating_profit=:operating_profit,
              net_income=:net_income, total_assets=:total_assets,
              total_liabilities=:total_liabilities, total_equity=:total_equity,
              capital_stock=:capital_stock, eps=:eps, bps=:bps, dps=:dps,
              cash=:cash, total_shares=:total_shares,
              depreciation_amortization=:depreciation_amortization
            WHERE id=:row_id
        """), params)


def _upsert_cashflow(conn, code: str, year: int, report_type: str, values: dict) -> None:
    params = {
        "code": code, "year": year, "report_type": report_type,
        **{k: values.get(k) for k in (
            "operating_cf", "investing_cf", "financing_cf", "capex",
            "cash_end", "depreciation",
        )},
    }
    row_id = conn.execute(text("""
        SELECT id FROM cash_flow_data
        WHERE stock_code=:code AND year=:year AND quarter=2 AND is_annual=FALSE
        ORDER BY CASE WHEN report_type='CFS' THEN 0 ELSE 1 END, id DESC
        LIMIT 1
    """), params).scalar()
    if row_id is None:
        conn.execute(text("""
            INSERT INTO cash_flow_data
              (stock_code,year,quarter,is_annual,report_type,data_source,value_type,
               operating_cf,investing_cf,financing_cf,capex,cash_end,depreciation)
            VALUES
              (:code,:year,2,FALSE,:report_type,'dart_q2_verified','cumulative',
               :operating_cf,:investing_cf,:financing_cf,:capex,:cash_end,:depreciation)
        """), params)
    else:
        params["row_id"] = row_id
        conn.execute(text("""
            UPDATE cash_flow_data SET
              report_type=:report_type, data_source='dart_q2_verified',
              value_type='cumulative', operating_cf=:operating_cf,
              investing_cf=:investing_cf, financing_cf=:financing_cf,
              capex=:capex, cash_end=:cash_end, depreciation=:depreciation
            WHERE id=:row_id
        """), params)


def collect_one(dart, code: str, year: int) -> tuple[str, str]:
    last_error = "no CFS/OFS statement"
    for report_type in ("CFS", "OFS"):
        try:
            df = dart.finstate_all(code, year, REPORT_CODE, fs_div=report_type)
        except Exception as exc:
            last_error = str(exc)
            continue
        if df is None or df.empty:
            continue
        fin = _parse_fin_df(df, stock_code=code)
        if not any(fin.get(k) is not None for k in ("revenue", "operating_profit", "net_income", "total_assets")):
            last_error = "statement parsed without core values"
            continue
        cf = _parse_cf_df(df, stock_code=code)
        with engine.begin() as conn:
            _upsert_financial(conn, code, year, report_type, fin)
            if any(cf.get(k) is not None for k in ("operating_cf", "investing_cf", "financing_cf", "capex")):
                _upsert_cashflow(conn, code, year, report_type, cf)
        return "saved", report_type
    return "missing", last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=0.12)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retry-verified", action="store_true")
    parser.add_argument("--repair-bs-failures", action="store_true")
    parser.add_argument("--repair-core-nulls", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    codes = eligible_filed_codes(args.year)
    if args.repair_core_nulls:
        nulls = core_null_codes(args.year)
        codes = [code for code in codes if code in nulls]
    elif args.repair_bs_failures:
        failures = balance_identity_failure_codes(args.year)
        codes = [code for code in codes if code in failures]
    elif not args.retry_verified:
        done = verified_codes(args.year)
        codes = [code for code in codes if code not in done]
    if args.limit:
        codes = codes[:args.limit]

    stats = {"target": len(codes), "saved": 0, "missing": 0, "errors": 0, "details": []}
    keys = get_dart_api_keys()
    worker_count = min(max(args.workers, 1), max(len(keys), 1), max(len(codes), 1))
    local = threading.local()
    key_lock = threading.Lock()
    key_index = iter(range(worker_count))

    def _worker(code: str) -> tuple[str, str, str]:
        if not hasattr(local, "dart"):
            with key_lock:
                idx = next(key_index)
            local.dart = RotatingOpenDartReader(keys=[keys[idx]])
        try:
            status, detail = collect_one(local.dart, code, args.year)
            time.sleep(max(args.sleep, 0))
            return code, status, detail
        except Exception as exc:
            return code, "errors", str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_worker, code) for code in codes]
        for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, status, detail = future.result()
            stats[status] += 1
            if status != "saved":
                stats["details"].append({"stock_code": code, "status": status, "detail": detail})
            if idx % 50 == 0 or idx == len(codes):
                LOG.info("progress %s/%s saved=%s missing=%s errors=%s", idx, len(codes), stats["saved"], stats["missing"], stats["errors"])

    payload = json.dumps(stats, ensure_ascii=False, indent=2)
    print(payload)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
