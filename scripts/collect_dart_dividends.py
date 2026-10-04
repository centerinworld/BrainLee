#!/usr/bin/env python3
"""DART alotMatter API → dart_dividends 테이블 수집기.

주당 현금배당금(DPS), 배당수익률, 배당금총액, 배당성향을 수집.
매년 사업보고서(11011) 기준으로 연간 배당 데이터를 수집.

실행:
  python3 scripts/collect_dart_dividends.py --year 2023
  python3 scripts/collect_dart_dividends.py --year 2020 --year 2021 --year 2022 --year 2023 --year 2024
  python3 scripts/collect_dart_dividends.py --all  # 2016~현재
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db

DART_API_KEYS = [
    os.getenv("DART_API_KEY", ""),
    os.getenv("DART_API_KEY2", ""),
    os.getenv("DART_API_KEY3", ""),
    os.getenv("DART_API_KEY4", ""),
]
DART_API_KEYS = [k for k in DART_API_KEYS if k]
ALOT_URL = "https://opendart.fss.or.kr/api/alotMatter.json"
REPRT_CODE = "11011"  # 사업보고서(연간)
RATE_LIMIT_DELAY = 0.3  # 초당 ~3요청


def ensure_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dart_dividends (
            stock_code      TEXT NOT NULL,
            fiscal_year     INTEGER NOT NULL,
            reprt_code      TEXT NOT NULL DEFAULT '11011',
            dps_krw         DOUBLE PRECISION,
            total_cash_div_bn DOUBLE PRECISION,
            div_yield_pct   DOUBLE PRECISION,
            div_payout_pct  DOUBLE PRECISION,
            settlement_date TEXT,
            corp_code       TEXT,
            raw_json        TEXT,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, reprt_code)
        )
    """)
    conn.commit()


def _parse_num(v: str) -> float | None:
    if not v or v in ("-", "없음", "해당없음", "N/A"):
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def fetch_dividends(corp_code: str, bsns_year: str, api_key: str) -> list[dict] | None:
    try:
        r = requests.get(
            ALOT_URL,
            params={"crtfc_key": api_key, "corp_code": corp_code,
                    "bsns_year": bsns_year, "reprt_code": REPRT_CODE},
            timeout=15,
        )
        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])
        if data.get("status") == "013":  # 데이터 없음
            return []
        return None
    except Exception:
        return None


def parse_items(items: list[dict], corp_code: str, bsns_year: str) -> dict | None:
    if not items:
        return None
    result: dict = {"corp_code": corp_code, "fiscal_year": int(bsns_year)}
    settlement_date = None
    raw = []

    for item in items:
        se = str(item.get("se") or "")
        val_str = str(item.get("thstrm") or "")
        knd = str(item.get("stock_knd") or "")
        stlm = str(item.get("stlm_dt") or "")
        if stlm and not settlement_date:
            settlement_date = stlm

        raw.append({"se": se, "thstrm": val_str, "stock_knd": knd})

        if "주당 현금배당금" in se and knd == "보통주":
            v = _parse_num(val_str)
            if v is not None and result.get("dps_krw") is None:
                result["dps_krw"] = v
        elif "현금배당금총액" in se:
            v = _parse_num(val_str)
            if v is not None:
                result["total_cash_div_bn"] = v
        elif "(연결)현금배당성향" in se or "배당성향" in se:
            v = _parse_num(val_str)
            if v is not None and result.get("div_payout_pct") is None:
                result["div_payout_pct"] = v
        elif "현금배당수익률" in se and knd in ("보통주", "", "-"):
            v = _parse_num(val_str)
            if v is not None and result.get("div_yield_pct") is None:
                result["div_yield_pct"] = v

    result["settlement_date"] = settlement_date
    result["raw_json"] = json.dumps(raw, ensure_ascii=False)
    return result


def upsert_row(cur, stock_code: str, row: dict) -> None:
    cur.execute("""
        INSERT INTO dart_dividends
            (stock_code, fiscal_year, reprt_code, dps_krw, total_cash_div_bn,
             div_yield_pct, div_payout_pct, settlement_date, corp_code, raw_json, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (stock_code, fiscal_year, reprt_code)
        DO UPDATE SET
            dps_krw          = EXCLUDED.dps_krw,
            total_cash_div_bn = EXCLUDED.total_cash_div_bn,
            div_yield_pct    = EXCLUDED.div_yield_pct,
            div_payout_pct   = EXCLUDED.div_payout_pct,
            settlement_date  = EXCLUDED.settlement_date,
            corp_code        = EXCLUDED.corp_code,
            raw_json         = EXCLUDED.raw_json,
            updated_at       = CURRENT_TIMESTAMP
    """, (
        stock_code, row["fiscal_year"], REPRT_CODE,
        row.get("dps_krw"), row.get("total_cash_div_bn"),
        row.get("div_yield_pct"), row.get("div_payout_pct"),
        row.get("settlement_date"), row.get("corp_code"), row.get("raw_json"),
    ))


def collect_year(conn, year: int, api_key_idx: int = 0) -> dict:
    cur = conn.cursor()
    # corp_code 목록: dart_report_items_quarterly에서 stock_code↔corp_code 매핑
    cur.execute("""
        SELECT DISTINCT stock_code, corp_code
        FROM dart_report_items_quarterly
        WHERE stock_code ~ '^[0-9]{6}$'
          AND corp_code IS NOT NULL
        LIMIT 5000
    """)
    rows = cur.fetchall()
    corp_map = {r[1]: r[0] for r in rows}  # corp_code → stock_code

    print(f"[{year}] {len(corp_map)}개 회사 대상")
    inserted = 0
    skipped = 0
    errors = 0
    key_idx = api_key_idx % len(DART_API_KEYS)

    for i, (corp_code, stock_code) in enumerate(corp_map.items()):
        api_key = DART_API_KEYS[key_idx % len(DART_API_KEYS)]
        items = fetch_dividends(corp_code, str(year), api_key)
        if items is None:
            key_idx += 1
            items = fetch_dividends(corp_code, str(year), DART_API_KEYS[key_idx % len(DART_API_KEYS)])

        if items is None:
            errors += 1
        elif len(items) == 0:
            skipped += 1
        else:
            row = parse_items(items, corp_code, str(year))
            if row and (row.get("dps_krw") is not None or row.get("total_cash_div_bn") is not None):
                upsert_row(cur, stock_code, row)
                inserted += 1

        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  [{year}] {i+1}/{len(corp_map)} 처리, 저장={inserted}, 스킵={skipped}, 오류={errors}")

        time.sleep(RATE_LIMIT_DELAY)

    conn.commit()
    return {"year": year, "total": len(corp_map), "inserted": inserted,
            "skipped": skipped, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser(description="DART 배당 데이터 수집")
    ap.add_argument("--year", type=int, action="append", dest="years")
    ap.add_argument("--all", action="store_true", help="2016~현재 연도 전체")
    args = ap.parse_args()

    if args.all:
        years = list(range(2016, date.today().year + 1))
    elif args.years:
        years = args.years
    else:
        years = [date.today().year - 1]

    conn = connect_primary_db(timeout=120)
    ensure_table(conn)

    total_inserted = 0
    for year in sorted(years):
        result = collect_year(conn, year)
        total_inserted += result["inserted"]
        print(f"[{year}] 완료: 저장={result['inserted']}, 스킵={result['skipped']}, 오류={result['errors']}")

    print(f"\n전체 완료: {total_inserted}건 저장")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
