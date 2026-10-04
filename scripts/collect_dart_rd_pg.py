#!/usr/bin/env python3
"""
scripts/collect_dart_rd_pg.py

DART 공시 API(fnlttSinglAcntAll) → PostgreSQL dart_report_items_quarterly
에 research_development_expense(연구개발비) 항목 수집.

기존 collect_dart_report_items.py 는 SQLite 기반이므로,
PostgreSQL 운영 DB 직접 저장을 위한 별도 스크립트.

실행:
  python3 scripts/collect_dart_rd_pg.py --years 2023 2024 2025 2026
  python3 scripts/collect_dart_rd_pg.py --codes 005930,000660 --years 2024
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db

DART_KEYS = [
    os.environ.get("DART_API_KEY", ""),
    os.environ.get("DART_API_KEY2", ""),
    os.environ.get("DART_API_KEY3", ""),
    os.environ.get("DART_API_KEY4", ""),
]
DART_KEYS = [k for k in DART_KEYS if k]

# .env 파일에서 직접 로드 (환경변수 없을 때)
if not DART_KEYS:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("DART_API_KEY"):
                val = line.split("=", 1)[1].strip()
                if val and val not in DART_KEYS:
                    DART_KEYS.append(val)

REPRT_CODES = {
    1: "11013",  # 1Q
    2: "11012",  # 2Q(반기)
    3: "11014",  # 3Q
    4: "11011",  # 사업보고서(연간)
}

RD_KEYWORDS = ("연구개발비", "경상연구개발비", "연구비", "개발비")
RD_EXCLUDE  = ("자산화", "무형자산", "개발비상각", "내부개발비")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _parse_num(val: str) -> float | None:
    v = re.sub(r"[,\s\(\)]", "", str(val or ""))
    v = v.lstrip("-+")
    try:
        return float(v) * 1_000_000  # DART 단위: 백만원
    except ValueError:
        return None


class DartClient:
    def __init__(self, keys: list[str]):
        self.keys = keys
        self._idx = 0

    @property
    def key(self) -> str:
        return self.keys[self._idx % len(self.keys)]

    def rotate(self) -> None:
        self._idx = (self._idx + 1) % len(self.keys)

    def get(self, endpoint: str, params: dict) -> dict | None:
        for _ in range(len(self.keys)):
            p = dict(params)
            p["crtfc_key"] = self.key
            try:
                r = requests.get(
                    f"https://opendart.fss.or.kr/api/{endpoint}",
                    params=p, timeout=20,
                )
                data = r.json()
            except Exception as e:
                print(f"[WARN] {endpoint} 요청 실패: {e}")
                self.rotate()
                time.sleep(0.5)
                continue
            status = str(data.get("status", ""))
            if status == "000":
                return data
            if status == "020":
                print("[WARN] DART 쿼터 초과 — 키 교체")
                self.rotate()
                time.sleep(1.0)
                continue
            return None
        print("[ERROR] 모든 키 소진")
        return None


def _get_corp_map(conn) -> dict[str, str]:
    """stock_code → corp_code 매핑 (dart_report_items_quarterly 기반)."""
    rows = conn.execute("""
        SELECT DISTINCT stock_code, corp_code FROM dart_report_items_quarterly
        WHERE stock_code ~ '^[0-9]{6}$' AND corp_code IS NOT NULL
        LIMIT 5000
    """).fetchall()
    return {r[0]: r[1] for r in rows}


def _target_stocks(conn, codes_filter: list[str] | None) -> list[tuple[str, str]]:
    corp_map = _get_corp_map(conn)
    if codes_filter:
        return [(c, corp_map[c]) for c in codes_filter if c in corp_map]

    # 전종목: stock_universe 기준 (시가총액 상위순)
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM stock_universe
        WHERE stock_code ~ '^[0-9]{6}$'
        ORDER BY stock_code
    """).fetchall()
    result = []
    for (sc,) in rows:
        cc = corp_map.get(sc)
        if cc:
            result.append((sc, cc))
    return result


def collect_year_quarter(
    conn,
    client: DartClient,
    stock_code: str,
    corp_code: str,
    year: int,
    quarter: int,
) -> int:
    reprt_code = REPRT_CODES.get(quarter)
    if not reprt_code:
        return 0

    data = client.get("fnlttSinglAcntAll.json", {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": "CFS",
    })
    if not data or "list" not in data:
        return 0

    saved = 0
    for item in data["list"]:
        sj_div = item.get("sj_div", "")
        account_nm = _normalize(item.get("account_nm") or "")
        account_id = item.get("account_id") or ""

        # 손익계산서(IS) 연구개발비 항목만 처리
        if sj_div not in ("IS", "IS1"):
            continue
        if not any(kw in account_nm for kw in RD_KEYWORDS):
            continue
        if any(ex in account_nm for ex in RD_EXCLUDE):
            continue

        thstrm_val = item.get("thstrm_amount") or item.get("thstrm_dt_amount")
        value = _parse_num(thstrm_val)
        if value is None:
            continue

        rcept_no = item.get("rcept_no") or ""
        conn.execute("""
            INSERT INTO dart_report_items_quarterly
              (stock_code, corp_code, fiscal_year, fiscal_quarter, reprt_code,
               fs_div, metric_name, account_id, account_nm, sj_div, sj_nm,
               value, rcept_no, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (stock_code, fiscal_year, fiscal_quarter, fs_div, metric_name, account_id, account_nm)
            DO UPDATE SET value=EXCLUDED.value, rcept_no=EXCLUDED.rcept_no,
                          updated_at=EXCLUDED.updated_at
        """, (
            stock_code, corp_code, year, quarter, reprt_code,
            "CFS", "research_development_expense",
            account_id, account_nm, sj_div, item.get("sj_nm") or "",
            value, rcept_no,
            datetime.now().isoformat(timespec="seconds"),
        ))
        saved += 1

    return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--codes", help="종목코드 콤마 구분 (기본: 전종목)")
    args = ap.parse_args()

    if not DART_KEYS:
        print("[ERROR] DART API 키 없음 (.env DART_API_KEY 확인)")
        sys.exit(1)

    conn = connect_primary_db(timeout=300)

    codes_filter = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()] if args.codes else None
    targets = _target_stocks(conn, codes_filter)
    print(f"대상 종목: {len(targets)}개, 연도: {args.years}")

    client = DartClient(DART_KEYS)
    total_saved = 0
    errors = 0

    for i, (stock_code, corp_code) in enumerate(targets):
        for year in args.years:
            for quarter in [1, 2, 3, 4]:
                saved = collect_year_quarter(conn, client, stock_code, corp_code, year, quarter)
                total_saved += saved
                time.sleep(0.1)  # DART API 속도 제한 준수

        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  {i+1}/{len(targets)} 종목 처리 | 저장 {total_saved:,}건 | 오류 {errors}건")

    conn.commit()
    conn.close()
    print(f"\n완료: 총 {total_saved:,}건 저장, 오류 {errors}건")


if __name__ == "__main__":
    main()
