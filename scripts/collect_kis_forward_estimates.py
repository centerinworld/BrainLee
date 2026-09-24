#!/usr/bin/env python3
"""KIS 종목추정실적을 forward_estimates 테이블에 저장한다."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from kis_client import kis_client  # noqa: E402


def num(value):
    try:
        if value in (None, "", "-"):
            return None
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def pick(rows, idx, key):
    if not rows or idx >= len(rows):
        return None
    return num((rows[idx] or {}).get(key))


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            period TEXT NOT NULL,
            is_estimate INTEGER NOT NULL DEFAULT 0,
            revenue_억원 REAL,
            revenue_growth_pct REAL,
            operating_profit_억원 REAL,
            operating_profit_growth_pct REAL,
            net_income_억원 REAL,
            net_income_growth_pct REAL,
            ebitda_십억원 REAL,
            eps_원 REAL,
            eps_growth_pct REAL,
            per REAL,
            ev_ebitda REAL,
            roe_pct REAL,
            debt_ratio_pct REAL,
            interest_coverage REAL,
            analyst TEXT,
            estimate_date TEXT,
            opinion TEXT,
            source TEXT NOT NULL DEFAULT 'KIS 국내주식 종목추정실적',
            raw_message TEXT,
            collected_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(stock_code, period, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forward_estimates_code_est ON forward_estimates(stock_code, is_estimate, period)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forward_estimates_collected ON forward_estimates(collected_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_estimate_snapshots AS
        SELECT stock_code, stock_name, period, is_estimate,
               revenue_억원, revenue_growth_pct,
               operating_profit_억원, operating_profit_growth_pct,
               net_income_억원, net_income_growth_pct,
               ebitda_십억원, eps_원, eps_growth_pct, per, ev_ebitda,
               roe_pct, debt_ratio_pct, interest_coverage,
               analyst, estimate_date, opinion, source, raw_message,
               COALESCE(
                   substr(collected_at, 1, 10),
                   CASE WHEN length(estimate_date) = 8 THEN
                       substr(estimate_date, 1, 4) || '-' ||
                       substr(estimate_date, 5, 2) || '-' ||
                       substr(estimate_date, 7, 2)
                   END,
                   CAST(CURRENT_DATE AS TEXT)
               ) AS snapshot_date
        FROM forward_estimates WHERE 1=0
    """)
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_forward_estimate_snapshot
                    ON forward_estimate_snapshots(stock_code, period, source, snapshot_date)""")
    conn.execute("""
        UPDATE forward_estimate_snapshots
        SET snapshot_date = COALESCE(
            CASE WHEN length(estimate_date) = 8 THEN
                substr(estimate_date, 1, 4) || '-' ||
                substr(estimate_date, 5, 2) || '-' ||
                substr(estimate_date, 7, 2)
            END,
            CAST(CURRENT_DATE AS TEXT)
        )
        WHERE snapshot_date IS NULL OR trim(snapshot_date) = ''
    """)
    conn.execute("""
        INSERT INTO forward_estimate_snapshots
        SELECT stock_code, stock_name, period, is_estimate,
               revenue_억원, revenue_growth_pct,
               operating_profit_억원, operating_profit_growth_pct,
               net_income_억원, net_income_growth_pct,
               ebitda_십억원, eps_원, eps_growth_pct, per, ev_ebitda,
               roe_pct, debt_ratio_pct, interest_coverage,
               analyst, estimate_date, opinion, source, raw_message,
               COALESCE(
                   substr(collected_at, 1, 10),
                   CASE WHEN length(estimate_date) = 8 THEN
                       substr(estimate_date, 1, 4) || '-' ||
                       substr(estimate_date, 5, 2) || '-' ||
                       substr(estimate_date, 7, 2)
                   END,
                   CAST(CURRENT_DATE AS TEXT)
               )
        FROM forward_estimates
        ON CONFLICT(stock_code, period, source, snapshot_date) DO NOTHING
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_estimate_collection_status (
            stock_code TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            message TEXT,
            last_checked_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()


def universe(conn, limit=None, stale_days=7):
    cutoff = (datetime.now() - timedelta(days=max(1, int(stale_days)))).isoformat(timespec="seconds")
    sql = """
        SELECT u.stock_code, MAX(u.stock_name) AS stock_name
        FROM stock_universe u
        WHERE LENGTH(u.stock_code) = 6
          AND u.stock_code BETWEEN '000000' AND '999999'
          AND COALESCE(u.secugrp_nm, '') NOT LIKE '%ETF%'
          AND COALESCE(u.secugrp_nm, '') NOT LIKE '%ETN%'
          AND COALESCE(u.kind_stkcert_nm, '') NOT LIKE '%ETF%'
          AND COALESCE(u.kind_stkcert_nm, '') NOT LIKE '%ETN%'
          AND NOT EXISTS (
              SELECT 1 FROM forward_estimates f
              WHERE f.stock_code = u.stock_code
                AND f.source = 'KIS 국내주식 종목추정실적'
                AND f.collected_at >= ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM forward_estimate_collection_status s
              WHERE s.stock_code = u.stock_code
                AND s.last_checked_at >= ?
          )
        GROUP BY u.stock_code
        ORDER BY MAX(u.market_cap) DESC NULLS LAST, u.stock_code
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (cutoff, cutoff)).fetchall()


def fetch_estimate(stock_code, token):
    url = f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/estimate-perform"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": "HHKST668300C0",
        "custtype": "P",
    }
    res = requests.get(url, headers=headers, params={"SHT_CD": stock_code}, timeout=12)
    data = res.json() if res.content else {}
    if res.status_code >= 400 or data.get("rt_cd") != "0":
        return None, data.get("msg1") or f"HTTP {res.status_code}"

    meta = data.get("output1") or {}
    periods = [str(r.get("dt") or "").strip() for r in (data.get("output4") or []) if r]
    out2 = data.get("output2") or []
    out3 = data.get("output3") or []
    rows = []
    for i, period in enumerate(periods[:5], start=1):
        key = f"data{i}"
        row = {
            "stock_code": stock_code,
            "stock_name": meta.get("item_kor_nm"),
            "period": period,
            "is_estimate": 1 if "E" in period.upper() else 0,
            "revenue_억원": pick(out2, 0, key),
            "revenue_growth_pct": (pick(out2, 1, key) / 10.0) if pick(out2, 1, key) is not None else None,
            "operating_profit_억원": pick(out2, 2, key),
            "operating_profit_growth_pct": (pick(out2, 3, key) / 10.0) if pick(out2, 3, key) is not None else None,
            "net_income_억원": pick(out2, 4, key),
            "net_income_growth_pct": (pick(out2, 5, key) / 10.0) if pick(out2, 5, key) is not None else None,
            "ebitda_십억원": (pick(out3, 0, key) / 10.0) if pick(out3, 0, key) is not None else None,
            "eps_원": (pick(out3, 1, key) / 10.0) if pick(out3, 1, key) is not None else None,
            "eps_growth_pct": (pick(out3, 2, key) / 10.0) if pick(out3, 2, key) is not None else None,
            "per": (pick(out3, 3, key) / 10.0) if pick(out3, 3, key) is not None else None,
            "ev_ebitda": (pick(out3, 4, key) / 10.0) if pick(out3, 4, key) is not None else None,
            "roe_pct": (pick(out3, 5, key) / 10.0) if pick(out3, 5, key) is not None else None,
            "debt_ratio_pct": (pick(out3, 6, key) / 10.0) if pick(out3, 6, key) is not None else None,
            "interest_coverage": (pick(out3, 7, key) / 10.0) if pick(out3, 7, key) is not None else None,
            "analyst": meta.get("name1"),
            "estimate_date": meta.get("estdate"),
            "opinion": meta.get("rcmd_name"),
            "source": "KIS 국내주식 종목추정실적",
            "raw_message": data.get("msg1"),
        }
        if any(row.get(k) is not None for k in ("revenue_억원", "operating_profit_억원", "net_income_억원", "eps_원", "per")):
            rows.append(row)
    return rows, data.get("msg1")


def upsert(conn, rows):
    if not rows:
        return 0
    cols = [
        "stock_code", "stock_name", "period", "is_estimate",
        "revenue_억원", "revenue_growth_pct",
        "operating_profit_억원", "operating_profit_growth_pct",
        "net_income_억원", "net_income_growth_pct",
        "ebitda_십억원", "eps_원", "eps_growth_pct", "per", "ev_ebitda",
        "roe_pct", "debt_ratio_pct", "interest_coverage",
        "analyst", "estimate_date", "opinion", "source", "raw_message",
    ]
    conn.executemany(f"""
        INSERT INTO forward_estimates ({",".join(cols)})
        VALUES ({",".join("?" for _ in cols)})
        ON CONFLICT(stock_code, period, source) DO UPDATE SET
            stock_name=excluded.stock_name,
            is_estimate=excluded.is_estimate,
            revenue_억원=excluded.revenue_억원,
            revenue_growth_pct=excluded.revenue_growth_pct,
            operating_profit_억원=excluded.operating_profit_억원,
            operating_profit_growth_pct=excluded.operating_profit_growth_pct,
            net_income_억원=excluded.net_income_억원,
            net_income_growth_pct=excluded.net_income_growth_pct,
            ebitda_십억원=excluded.ebitda_십억원,
            eps_원=excluded.eps_원,
            eps_growth_pct=excluded.eps_growth_pct,
            per=excluded.per,
            ev_ebitda=excluded.ev_ebitda,
            roe_pct=excluded.roe_pct,
            debt_ratio_pct=excluded.debt_ratio_pct,
            interest_coverage=excluded.interest_coverage,
            analyst=excluded.analyst,
            estimate_date=excluded.estimate_date,
            opinion=excluded.opinion,
            raw_message=excluded.raw_message,
            collected_at=datetime('now','localtime')
    """, [tuple(r.get(c) for c in cols) for r in rows])
    snapshot_cols = cols + ["snapshot_date"]
    snapshot_values = [tuple(r.get(c) for c in cols) + (date.today().isoformat(),) for r in rows]
    conn.executemany(f"""
        INSERT INTO forward_estimate_snapshots ({",".join(snapshot_cols)})
        VALUES ({",".join("?" for _ in snapshot_cols)})
        ON CONFLICT(stock_code, period, source, snapshot_date) DO UPDATE SET
            stock_name=excluded.stock_name,
            is_estimate=excluded.is_estimate,
            revenue_억원=excluded.revenue_억원,
            revenue_growth_pct=excluded.revenue_growth_pct,
            operating_profit_억원=excluded.operating_profit_억원,
            operating_profit_growth_pct=excluded.operating_profit_growth_pct,
            net_income_억원=excluded.net_income_억원,
            net_income_growth_pct=excluded.net_income_growth_pct,
            eps_원=excluded.eps_원,
            eps_growth_pct=excluded.eps_growth_pct,
            per=excluded.per,
            ev_ebitda=excluded.ev_ebitda,
            roe_pct=excluded.roe_pct,
            debt_ratio_pct=excluded.debt_ratio_pct,
            interest_coverage=excluded.interest_coverage,
            estimate_date=excluded.estimate_date,
            opinion=excluded.opinion,
            raw_message=excluded.raw_message
    """, snapshot_values)
    conn.commit()
    return len(rows)


def record_status(conn, stock_code, status, message=None):
    conn.execute("""
        INSERT INTO forward_estimate_collection_status(stock_code,status,message,last_checked_at)
        VALUES(?,?,?,datetime('now','localtime'))
        ON CONFLICT(stock_code) DO UPDATE SET
            status=excluded.status,
            message=excluded.message,
            last_checked_at=datetime('now','localtime')
    """, (stock_code, status, str(message or "")[:500]))
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=450)
    ap.add_argument("--stale-days", type=int, default=7)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    token = kis_client.get_token()
    if not token:
        raise SystemExit("KIS 토큰 발급 실패")

    conn = connect_stock_db(timeout=30)
    ensure_schema(conn)

    codes = universe(conn, args.limit, args.stale_days)
    ok = saved = no_data = err = 0
    for i, row in enumerate(codes, start=1):
        code = row["stock_code"]
        try:
            rows, msg = fetch_estimate(code, token)
            if rows:
                n = upsert(conn, rows)
                record_status(conn, code, "ok", msg)
                ok += 1
                saved += n
                print(f"[{i}/{len(codes)}] {code} saved={n}")
            else:
                record_status(conn, code, "no_data", msg)
                no_data += 1
                print(f"[{i}/{len(codes)}] {code} no_data {msg or ''}")
        except Exception as exc:
            err += 1
            print(f"[{i}/{len(codes)}] {code} error {exc}")
        time.sleep(args.sleep)
    print(f"[DONE] universe={len(codes)} ok={ok} saved_rows={saved} no_data={no_data} err={err}")


if __name__ == "__main__":
    main()
