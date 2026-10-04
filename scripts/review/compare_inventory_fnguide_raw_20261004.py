#!/usr/bin/env python3
"""dart_cost_quarterly.inventory_assets_krw ↔ FnGuide 원문 재고자산 대조(읽기 전용).

FINANCIAL_STATEMENTS.md §9 '재고자산 원문 대조' 후속.
FnGuide wcomp 원문 저장분에서 재고자산을 파싱하고, 운영 PostgreSQL의
dart_cost_quarterly.inventory_assets_krw 와 같은 종목·연도·분기·연결/별도 기준으로 비교한다.
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

from compare_db_vs_fnguide_raw_20261003 import RAW, OUT, close, parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402


def main() -> int:
    codes = sorted(p.name for p in RAW.iterdir() if p.is_dir()) if RAW.exists() else []
    fg = {}
    days = {}
    for code in codes:
        parsed, day = parse_code(code)
        for key, vals in parsed.items():
            inv = vals.get("inventory")
            if inv is not None:
                fg[(code,) + key] = inv
        days[code] = day
    if not fg:
        print("FnGuide 재고자산 원문 없음")
        return 0

    conn = connect_primary_db(timeout=600, readonly=True)
    ph = ",".join("?" * len(codes))
    db = {}
    for r in conn.execute(f"""SELECT stock_code, fiscal_year, fiscal_quarter, report_type, inventory_assets_krw,
                                     source_rcept_no, parser_version, confidence
                              FROM dart_cost_quarterly
                              WHERE stock_code IN ({ph}) AND inventory_assets_krw IS NOT NULL""", codes).fetchall():
        code, year, quarter, fs, inv, rcp, parser, conf = tuple(r)
        db[(code, int(year), 0 if int(quarter) == 4 else int(quarter), fs)] = {
            "inventory": inv,
            "source_rcept_no": rcp,
            "parser_version": parser,
            "confidence": conf,
        }
    conn.close()

    st = collections.Counter()
    mismatches = []
    for key, fgv in fg.items():
        code, year, q, fs = key
        if isinstance(q, str):
            st["non_december_hold"] += 1
            continue
        row = db.get(key)
        if row is None:
            st["db_missing"] += 1
            continue
        dbv = row["inventory"]
        if close(dbv, fgv):
            st["match"] += 1
        else:
            st["mismatch"] += 1
            mismatches.append([
                code, year, q, fs, dbv, fgv, round(dbv / fgv, 6) if fgv else None,
                row.get("source_rcept_no"), row.get("parser_version"), row.get("confidence"), days.get(code),
            ])

    out_m = OUT / "inventory_fnguide_raw_mismatches_20261004.csv"
    out_s = OUT / "inventory_fnguide_raw_summary_20261004.json"
    with out_m.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stock_code", "year", "q", "fs", "db_inventory", "fnguide_inventory", "db/fnguide",
                    "source_rcept_no", "parser_version", "confidence", "fetched_day"])
        w.writerows(mismatches)
    json.dump({"stocks": len(codes), "fnguide_inventory_points": len(fg), "stats": dict(st), "mismatch_file": str(out_m)},
              out_s.open("w"), ensure_ascii=False, indent=1)
    compared = st["match"] + st["mismatch"]
    print(f"FnGuide 재고 원문 {len(fg):,}칸 / 비교 {compared:,} / 일치 {st['match'] / compared * 100 if compared else 0:.3f}% / {dict(st)}")
    print(out_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
