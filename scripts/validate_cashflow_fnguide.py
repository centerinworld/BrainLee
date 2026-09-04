#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from check_financial_integrity import (
    FNGUIDE_CF_Q,
    _extract_quarterly,
    _fetch_soup,
    _parse_unit,
)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"


def get_targets(conn: sqlite3.Connection, limit_stocks: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.stock_code, su.stock_name
        FROM cash_flow_data c
        JOIN stock_universe su ON su.stock_code = c.stock_code
        WHERE c.is_annual = 0
          AND c.year >= 2025
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
        LIMIT ?
        """,
        (limit_stocks,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="FnGuide 현금흐름 대조검증")
    ap.add_argument("--limit-stocks", type=int, default=120)
    ap.add_argument("--out", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/validation_cashflow_fnguide.json")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    targets = get_targets(conn, args.limit_stocks)

    CF_KW = {
        "영업활동현금흐름": "operating_cf",
        "투자활동현금흐름": "investing_cf",
        "재무활동현금흐름": "financing_cf",
    }

    checked = 0
    all_match = 0
    op_match = 0
    inv_match = 0
    fin_match = 0
    mismatches: list[dict] = []
    fail_fetch = 0

    for idx, (code, name) in enumerate(targets, 1):
        soup = _fetch_soup(FNGUIDE_CF_Q.format(code=code))
        if not soup:
            fail_fetch += 1
            continue
        mult = _parse_unit(soup)
        qdata = _extract_quarterly(soup, mult, CF_KW)  # {(year,quarter): {col:val}}
        if not qdata:
            continue

        db_rows = conn.execute(
            """
            SELECT year, quarter, operating_cf, investing_cf, financing_cf
            FROM cash_flow_data
            WHERE stock_code=? AND is_annual=0 AND year>=2025
            """,
            (code,),
        ).fetchall()
        for r in db_rows:
            k = (int(r["year"]), int(r["quarter"]))
            if k not in qdata:
                continue
            ref = qdata[k]
            if not all(x in ref for x in ("operating_cf", "investing_cf", "financing_cf")):
                continue

            checked += 1
            op_ok = abs(float(r["operating_cf"] or 0) - float(ref["operating_cf"])) <= 1e8
            inv_ok = abs(float(r["investing_cf"] or 0) - float(ref["investing_cf"])) <= 1e8
            fin_ok = abs(float(r["financing_cf"] or 0) - float(ref["financing_cf"])) <= 1e8
            if op_ok:
                op_match += 1
            if inv_ok:
                inv_match += 1
            if fin_ok:
                fin_match += 1
            if op_ok and inv_ok and fin_ok:
                all_match += 1
            elif len(mismatches) < 80:
                mismatches.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "year": k[0],
                        "quarter": k[1],
                        "db": {
                            "operating_cf": float(r["operating_cf"] or 0),
                            "investing_cf": float(r["investing_cf"] or 0),
                            "financing_cf": float(r["financing_cf"] or 0),
                        },
                        "fnguide": {
                            "operating_cf": float(ref["operating_cf"]),
                            "investing_cf": float(ref["investing_cf"]),
                            "financing_cf": float(ref["financing_cf"]),
                        },
                        "ok": {"operating_cf": op_ok, "investing_cf": inv_ok, "financing_cf": fin_ok},
                    }
                )

        if idx % 20 == 0:
            print(f"[progress] {idx}/{len(targets)} checked_rows={checked}")

    report = {
        "target_stocks": len(targets),
        "checked_rows": checked,
        "fetch_fail_stocks": fail_fetch,
        "all_match": all_match,
        "op_match": op_match,
        "inv_match": inv_match,
        "fin_match": fin_match,
        "all_match_rate": round((all_match / checked) * 100, 2) if checked else 0,
        "op_match_rate": round((op_match / checked) * 100, 2) if checked else 0,
        "inv_match_rate": round((inv_match / checked) * 100, 2) if checked else 0,
        "fin_match_rate": round((fin_match / checked) * 100, 2) if checked else 0,
        "mismatches": mismatches,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: report[k] for k in [
        "target_stocks", "checked_rows", "fetch_fail_stocks", "all_match_rate", "op_match_rate", "inv_match_rate", "fin_match_rate"
    ]}, ensure_ascii=False))
    print(f"saved={args.out}")


if __name__ == "__main__":
    main()
