#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import OpenDartReader as odr

DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
ENV_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/.env"

REV_KW = ["매출액", "영업수익", "매출"]
OP_KW = ["영업이익"]
NI_KW = ["당기순이익", "분기순이익", "반기순이익", "당기순손익"]
NI_EXCLUDE = ["주당", "지배"]


@dataclass
class QuarterTarget:
    year: int
    quarter: int
    rprt_code: str


def load_env(path: str = ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def latest_disclosed_quarter(today: Optional[date] = None) -> QuarterTarget:
    d = today or date.today()
    y, m = d.year, d.month
    if m in (1, 2):
        return QuarterTarget(y - 1, 3, "11014")
    if m in (3, 4):
        return QuarterTarget(y - 1, 4, "11011")
    if m in (5, 6, 7):
        return QuarterTarget(y, 1, "11013")
    if m in (8, 9, 10):
        return QuarterTarget(y, 2, "11012")
    return QuarterTarget(y, 3, "11014")


def pick_metrics(df: pd.DataFrame) -> dict[str, Optional[float]]:
    m: dict[str, Optional[float]] = {
        "revenue": None,
        "operating_profit": None,
        "net_income": None,
    }
    if df is None or df.empty:
        return m

    for _, r in df.iterrows():
        acc = str(r.get("account_nm", "")).replace(" ", "")
        val = r.get("thstrm_amount", None)
        if not acc or val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        try:
            v = float(str(val).replace(",", ""))
        except Exception:
            continue

        if any(k in acc for k in REV_KW):
            if v != 0 or m["revenue"] is None:
                m["revenue"] = v
        elif any(k in acc for k in OP_KW):
            if v != 0 or m["operating_profit"] is None:
                m["operating_profit"] = v
        elif any(k in acc for k in NI_KW) and not any(k in acc for k in NI_EXCLUDE):
            if v != 0 or m["net_income"] is None:
                m["net_income"] = v

    return m


def fetch_verified_quarter(dart, stock_code: str, qt: QuarterTarget):
    for fs_div in ("CFS", "OFS"):
        try:
            df = dart.finstate_all(stock_code, qt.year, qt.rprt_code, fs_div=fs_div)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        m = pick_metrics(df)
        valid = sum(1 for k in ("revenue", "operating_profit", "net_income") if m.get(k) is not None)
        if valid >= 2:
            return fs_div, m
    return None, None


def ensure_fix_log_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          stock_code TEXT,
          year INTEGER,
          quarter INTEGER,
          is_annual INTEGER,
          report_type TEXT,
          field_name TEXT NOT NULL,
          old_value REAL,
          new_value REAL,
          fix_rule TEXT NOT NULL,
          source TEXT NOT NULL,
          run_id TEXT NOT NULL
        )
        """
    )


def log_change(conn: sqlite3.Connection, row_id: int, stock_code: str, year: int, quarter: int, report_type: str,
               field: str, old_v: Optional[float], new_v: Optional[float], run_id: str, rule: str, source: str):
    conn.execute(
        """
        INSERT INTO financial_fix_log
        (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
        VALUES (datetime('now'),?,?,?,?,0,?,?,?,?,?,?,?)
        """,
        (row_id, stock_code, year, quarter, report_type, field, old_v, new_v, rule, source, run_id),
    )


def target_stocks(conn: sqlite3.Connection, scope: str) -> list[tuple[str, str]]:
    if scope == "detailed":
        rows = conn.execute(
            """
            SELECT DISTINCT stock_code, stock_name
            FROM detailed_analysis_posts
            WHERE stock_code IS NOT NULL AND stock_code != ''
            ORDER BY stock_code
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT stock_code, stock_name
            FROM stock_universe
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND stock_type='보통주'
              AND market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            ORDER BY stock_code
            """
        ).fetchall()
    return [(str(r[0]), str(r[1] or r[0])) for r in rows]


def main():
    ap = argparse.ArgumentParser(description="분기 재무제표 검증/보강 (DART 키 로테이션)")
    ap.add_argument("--scope", choices=["detailed", "all"], default="detailed")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--quarter", type=int, default=None, choices=[1, 2, 3, 4])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    env = load_env()
    keys = [
        ("KEY1", env.get("DART_API_KEY", "")),
        ("KEY2", env.get("DART_API_KEY2", "")),
        ("KEY3", env.get("DART_API_KEY3", "")),
    ]
    keys = [(n, k) for n, k in keys if k]
    if not keys:
        raise RuntimeError("DART keys not found in .env")

    qt = latest_disclosed_quarter()
    if args.year and args.quarter:
        rmap = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
        qt = QuarterTarget(args.year, args.quarter, rmap[args.quarter])

    run_id = f"quarter_sync_{qt.year}Q{qt.quarter}"

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_fix_log_table(conn)

    stocks = target_stocks(conn, args.scope)
    if args.limit and args.limit > 0:
        stocks = stocks[: args.limit]

    ins = upd = skip = miss = 0

    for code, name in stocks:
        existing = conn.execute(
            """
            SELECT id, report_type, revenue, operating_profit, net_income
            FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
            ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (code, qt.year, qt.quarter),
        ).fetchone()

        # 이미 있더라도 KEY3 기준으로 재검증해서 값 드리프트 방지
        verified_div = None
        verified_metrics = None
        used_key = None
        for key_name, key in keys:
            dart = odr(key)
            fs_div, metrics = fetch_verified_quarter(dart, code, qt)
            if fs_div and metrics:
                verified_div = fs_div
                verified_metrics = metrics
                used_key = key_name
                break

        if not verified_metrics:
            miss += 1
            continue

        source_tag = f"dart_{used_key.lower()}_verified"
        # report_type 단위의 정확한 대상행 우선 조회 (UNIQUE 키 기준)
        by_type = conn.execute(
            """
            SELECT id, report_type, revenue, operating_profit, net_income
            FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND report_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (code, qt.year, qt.quarter, verified_div),
        ).fetchone()

        target_row = by_type if by_type else existing

        if target_row:
            row_id = int(target_row["id"])
            old = {
                "revenue": target_row["revenue"],
                "operating_profit": target_row["operating_profit"],
                "net_income": target_row["net_income"],
            }
            changed = False
            for f in ("revenue", "operating_profit", "net_income"):
                nv = verified_metrics.get(f)
                ov = old.get(f)
                if nv is not None and (ov is None or float(ov) != float(nv)):
                    changed = True
                    log_change(
                        conn, row_id, code, qt.year, qt.quarter, verified_div,
                        f, ov, nv, run_id, "DART_VERIFIED_QUARTER_SYNC", source_tag,
                    )
            conn.execute(
                """
                UPDATE financial_data
                SET revenue=?, operating_profit=?, net_income=?, report_type=?, data_source=?
                WHERE id=?
                """,
                (
                    verified_metrics.get("revenue"),
                    verified_metrics.get("operating_profit"),
                    verified_metrics.get("net_income"),
                    verified_div,
                    source_tag,
                    row_id,
                ),
            )
            upd += 1 if changed else 0
            skip += 0 if changed else 1
        else:
            cur = conn.execute(
                """
                INSERT INTO financial_data
                (stock_code, year, quarter, is_annual, report_type, revenue, operating_profit, net_income, data_source, created_at)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    code,
                    qt.year,
                    qt.quarter,
                    verified_div,
                    verified_metrics.get("revenue"),
                    verified_metrics.get("operating_profit"),
                    verified_metrics.get("net_income"),
                    source_tag,
                ),
            )
            row_id = int(cur.lastrowid)
            for f in ("revenue", "operating_profit", "net_income"):
                nv = verified_metrics.get(f)
                if nv is not None:
                    log_change(
                        conn, row_id, code, qt.year, qt.quarter, verified_div,
                        f, None, nv, run_id, "DART_VERIFIED_QUARTER_INSERT", source_tag,
                    )
            ins += 1

    conn.commit()

    remaining = conn.execute(
        """
        SELECT COUNT(*)
        FROM (
          SELECT DISTINCT stock_code
          FROM detailed_analysis_posts
          WHERE stock_code IS NOT NULL AND stock_code!=''
        ) s
        WHERE NOT EXISTS (
          SELECT 1 FROM financial_data f
          WHERE f.stock_code=s.stock_code AND f.year=? AND f.quarter=? AND f.is_annual=0
        )
        """,
        (qt.year, qt.quarter),
    ).fetchone()[0]

    print(
        f"run_id={run_id} scope={args.scope} target={len(stocks)} inserted={ins} updated={upd} "
        f"unchanged={skip} no_dart={miss} remaining_missing={remaining}"
    )


if __name__ == "__main__":
    main()
