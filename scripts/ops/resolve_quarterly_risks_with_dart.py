#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import OpenDartReader as odr

DB = "/Applications/stock_dashboard/stock.db"
ENV_PATH = "/Applications/stock_dashboard/.env"
OUT_DIR = Path("/Applications/stock_dashboard/scratch")


@dataclass
class Target:
    stock_code: str
    year: int
    report_type: str
    cause: str


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


def safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def account_value(df, include: list[str], exclude: list[str] | None = None) -> Optional[float]:
    if df is None or len(df) == 0:
        return None
    exclude = exclude or []
    for _, r in df.iterrows():
        nm = str(r.get("account_nm", "")).replace(" ", "")
        if not nm:
            continue
        if not any(k in nm for k in include):
            continue
        if any(k in nm for k in exclude):
            continue
        v = safe_float(r.get("thstrm_amount"))
        if v is not None:
            return v
    return None


def depreciation_value(df) -> Optional[float]:
    if df is None or len(df) == 0:
        return None
    vals = []
    for _, r in df.iterrows():
        nm = str(r.get("account_nm", "")).replace(" ", "")
        if "감가상각" in nm:
            v = safe_float(r.get("thstrm_amount"))
            if v is not None:
                vals.append(v)
    if not vals:
        return None
    # 감가상각비 + 무형자산상각비 등이 별도인 경우 합산
    return sum(vals)


def capex_value(df) -> Optional[float]:
    if df is None or len(df) == 0:
        return None
    keys = ["유형자산의취득", "무형자산의취득", "유무형자산의취득", "취득"]
    vals = []
    for _, r in df.iterrows():
        nm = str(r.get("account_nm", "")).replace(" ", "")
        if not any(k in nm for k in keys):
            continue
        if "자회사" in nm:
            continue
        v = safe_float(r.get("thstrm_amount"))
        if v is None:
            continue
        vals.append(abs(v))
    if not vals:
        return None
    return sum(vals)


def parse_cashflow(df) -> dict[str, Optional[float]]:
    return {
        "operating_cf": account_value(df, ["영업활동현금흐름"]),
        "investing_cf": account_value(df, ["투자활동현금흐름"]),
        "financing_cf": account_value(df, ["재무활동현금흐름"]),
        "cash_end": account_value(df, ["기말현금및현금성자산", "현금및현금성자산"]),
        "depreciation": depreciation_value(df),
        "capex": capex_value(df),
    }


def ensure_log_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cashflow_fix_log (
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


def log_fix(conn: sqlite3.Connection, row_id: int, stock_code: str, year: int, quarter: int, is_annual: int,
            report_type: str, field_name: str, old_v: Optional[float], new_v: Optional[float], run_id: str):
    conn.execute(
        """
        INSERT INTO cashflow_fix_log
        (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
        VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row_id, stock_code, year, quarter, is_annual, report_type,
            field_name, old_v, new_v, "DART_RISK_RESOLVE", "scripts/ops/resolve_quarterly_risks_with_dart.py", run_id
        ),
    )


def collect_targets(conn: sqlite3.Connection, years_from: int, years_to: int, limit: int) -> list[Target]:
    rows = conn.execute(
        """
        WITH g AS (
          SELECT stock_code, year, COALESCE(report_type,'CFS') report_type
          FROM cash_flow_data
          WHERE year BETWEEN ? AND ?
          GROUP BY 1,2,3
        ),
        c AS (
          SELECT
            g.stock_code, g.year, g.report_type,
            MAX(CASE WHEN q.quarter=1 THEN 1 ELSE 0 END) has_q1,
            MAX(CASE WHEN q.quarter=2 THEN 1 ELSE 0 END) has_q2,
            MAX(CASE WHEN q.quarter=3 THEN 1 ELSE 0 END) has_q3,
            MAX(CASE WHEN q.quarter=1 AND q.depreciation IS NOT NULL THEN 1 ELSE 0 END) q1_dep_ok,
            MAX(CASE WHEN q.quarter=2 AND q.depreciation IS NOT NULL THEN 1 ELSE 0 END) q2_dep_ok,
            MAX(CASE WHEN q.quarter=3 AND q.depreciation IS NOT NULL THEN 1 ELSE 0 END) q3_dep_ok,
            MAX(CASE WHEN q.quarter=3 THEN q.data_source END) q3_src,
            MAX(CASE WHEN a.is_annual=1 THEN a.data_source END) ann_src
          FROM g
          LEFT JOIN cash_flow_data q
            ON q.stock_code=g.stock_code AND q.year=g.year AND COALESCE(q.report_type,'CFS')=g.report_type AND q.is_annual=0
          LEFT JOIN cash_flow_data a
            ON a.stock_code=g.stock_code AND a.year=g.year AND COALESCE(a.report_type,'CFS')=g.report_type AND a.is_annual=1
          GROUP BY 1,2,3
        )
        SELECT stock_code, year, report_type,
               CASE
                 WHEN has_q1=0 OR has_q2=0 OR has_q3=0 THEN 'MISSING_Q123'
                 WHEN q1_dep_ok=0 OR q2_dep_ok=0 OR q3_dep_ok=0 THEN 'NULL_Q123_DEPR'
                 WHEN LOWER(COALESCE(q3_src,'')) != LOWER(COALESCE(ann_src,'')) THEN 'MIXED_SOURCE_ANNUAL_Q'
                 ELSE 'OK'
               END cause
        FROM c
        WHERE cause <> 'OK'
        ORDER BY year DESC, stock_code
        """,
        (years_from, years_to),
    ).fetchall()
    targets = [Target(str(r[0]), int(r[1]), str(r[2] or "CFS"), str(r[3])) for r in rows]
    if limit and limit > 0:
        return targets[:limit]
    return targets


def upsert_cf_row(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int, is_annual: int, report_type: str,
                  vals: dict[str, Optional[float]], run_id: str) -> int:
    existing = conn.execute(
        """
        SELECT id, operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation, value_type
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND COALESCE(report_type,'CFS')=?
        ORDER BY id DESC LIMIT 1
        """,
        (stock_code, year, quarter, is_annual, report_type),
    ).fetchone()

    if existing:
        row_id = int(existing["id"])
        updates = []
        params = []
        for f in ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"):
            nv = vals.get(f)
            ov = safe_float(existing[f])
            if nv is None:
                continue
            if ov is None or abs(ov - nv) > 1:
                updates.append(f"{f}=?")
                params.append(nv)
                log_fix(conn, row_id, stock_code, year, quarter, is_annual, report_type, f, ov, nv, run_id)

        if existing["value_type"] != "cumulative":
            updates.append("value_type='cumulative'")
        updates.append("data_source='dart_api_unified'")
        updates.append("created_at=CURRENT_TIMESTAMP")
        if updates:
            conn.execute(
                f"UPDATE cash_flow_data SET {', '.join(updates)} WHERE id=?",
                (*params, row_id),
            )
        return row_id

    conn.execute(
        """
        INSERT INTO cash_flow_data
        (stock_code,year,quarter,is_annual,report_type,operating_cf,investing_cf,financing_cf,capex,cash_end,depreciation,value_type,data_source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            stock_code, year, quarter, is_annual, report_type,
            vals.get("operating_cf"), vals.get("investing_cf"), vals.get("financing_cf"),
            vals.get("capex"), vals.get("cash_end"), vals.get("depreciation"),
            "cumulative", "dart_api_unified",
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def fetch_dart_finstate(dart, stock_code: str, year: int, rprt_code: str, report_type: str):
    for fs_div in [report_type, "CFS", "OFS"]:
        try:
            df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
        except Exception:
            df = None
        if df is not None and len(df) > 0:
            return df
    return None


def run(args) -> int:
    env = load_env()
    keys = [
        env.get("DART_API_KEY", ""),
        env.get("DART_API_KEY2", ""),
        env.get("DART_API_KEY3", ""),
    ]
    keys = [k for k in keys if k]
    if not keys:
        print("No DART keys in .env")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_log_table(conn)
    targets = collect_targets(conn, args.year_from, args.year_to, args.limit)
    if not targets:
        print("No risk targets found.")
        return 0

    run_id = f"dart_risk_resolve_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report_codes = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}

    key_idx = 0
    ok, fail = 0, 0
    touched = 0
    details = []

    for t in targets:
        dart = odr(keys[key_idx % len(keys)])
        key_idx += 1
        try:
            for q in (1, 2, 3):
                df = fetch_dart_finstate(dart, t.stock_code, t.year, report_codes[q], t.report_type)
                vals = parse_cashflow(df) if df is not None else {}
                if not vals:
                    continue
                upsert_cf_row(conn, t.stock_code, t.year, q, 0, t.report_type, vals, run_id)
                touched += 1

            df_ann = fetch_dart_finstate(dart, t.stock_code, t.year, report_codes[4], t.report_type)
            vals_ann = parse_cashflow(df_ann) if df_ann is not None else {}
            if vals_ann:
                upsert_cf_row(conn, t.stock_code, t.year, 4, 1, t.report_type, vals_ann, run_id)
                touched += 1

            ok += 1
            details.append((t.stock_code, t.year, t.report_type, t.cause, "OK"))
        except Exception as e:
            fail += 1
            details.append((t.stock_code, t.year, t.report_type, t.cause, f"ERR:{e}"))

    conn.commit()
    conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"risk_resolve_dart_{ts}.csv"
    with out.open("w", encoding="utf-8-sig") as f:
        f.write("stock_code,year,report_type,cause,status\n")
        for r in details:
            f.write(",".join([str(x).replace(",", " ") for x in r]) + "\n")

    print({
        "targets": len(targets),
        "ok": ok,
        "fail": fail,
        "rows_touched": touched,
        "report": str(out),
        "run_id": run_id,
    })
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year-from", type=int, default=2020)
    ap.add_argument("--year-to", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
