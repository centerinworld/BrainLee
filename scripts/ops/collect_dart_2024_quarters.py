#!/usr/bin/env python3
"""
P1-1/P1-2: DART에서 2024 분기 데이터 수집 (financial_data + cash_flow_data).

FnGuide 웹 페이지는 현재 연도 분기만 표시하므로 2024 분기는 DART API 사용.
- Q1 (11013): 누적값 = Q1값
- 반기 (11012): Q1+Q2 누적 → Q2 = 반기 - Q1
- 3분기 (11014): Q1+Q2+Q3 누적 → Q3 = 3분기 - 반기
- Q4: Annual - (Q1+Q2+Q3)

사용법:
  python3 scripts/ops/collect_dart_2024_quarters.py              # P1 우선순위 43종목
  python3 scripts/ops/collect_dart_2024_quarters.py --all        # 전체 NULL-source 대상
  python3 scripts/ops/collect_dart_2024_quarters.py --dry-run    # DB 수정 없이 리포트만
"""
import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import OpenDartReader
from config import DART_API_KEY

_DART = OpenDartReader(DART_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("/Applications/stock_dashboard/stock.db")

# DART 보고서 코드
_RPRT_Q1 = "11013"
_RPRT_H1 = "11012"
_RPRT_9M = "11014"

# P1 우선순위 종목 (Codex handoff checklist)
P1_STOCKS = [
    "000150","000660","000670","000720","000880","000990","001570","003490",
    "003690","004490","004990","005110","005380","005850","006400","007310",
    "010120","010780","011170","012450","021080","028670","034220","035720",
    "041190","042000","047040","047810","051910","052300","058820","066570",
    "066970","067160","069260","073540","078930","100790","102710","104480",
    "161390","200880","267260",
]

_ACCT_MAP = {
    "revenue":          ["매출액", "영업수익", "수익(매출액)"],
    "operating_profit": ["영업이익"],
    "net_income":       ["당기순이익", "분기순이익", "반기순이익"],
    "total_assets":     ["자산총계"],
    "total_equity":     ["자본총계"],
}
_CF_MAP = {
    "operating_cf":    ["영업활동으로인한현금흐름", "영업활동현금흐름"],
    "investing_cf":    ["투자활동으로인한현금흐름", "투자활동현금흐름"],
    "financing_cf":    ["재무활동으로인한현금흐름", "재무활동현금흐름"],
    "capex":           ["유형자산의취득", "유형자산취득"],
    "free_cash_flow":  [],
}


def _fetch_dart_quarter(code: str, year: int, rprt_code: str, fs_div: str) -> dict | None:
    try:
        df = _DART.finstate_all(code, year, rprt_code, fs_div=fs_div)  # type: ignore
        if df is None or df.empty:
            return None
        result = {}
        for key, kws in {**_ACCT_MAP, **_CF_MAP}.items():
            for kw in kws:
                rows = df[df["account_nm"].str.contains(kw, na=False)]
                if not rows.empty:
                    val_str = str(rows.iloc[0].get("thstrm_amount", "") or "")
                    val_str = val_str.replace(",","").strip()
                    if val_str and val_str not in ("-",""):
                        try:
                            result[key] = float(val_str)
                        except ValueError:
                            pass
                    break
        return result or None
    except Exception as e:
        log.warning(f"  DART {code} {year} {rprt_code}: {e}")
        return None


def _subtract(a: dict | None, b: dict | None, keys: list[str]) -> dict:
    if not a or not b:
        return {}
    return {k: a[k] - b[k] for k in keys if k in a and k in b}


def _get_target_stocks(conn: sqlite3.Connection, p1_only: bool) -> list[str]:
    if p1_only:
        return P1_STOCKS
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM financial_data
        WHERE is_annual=0 AND year=2024 AND data_source IS NULL
        AND stock_code IN (
            SELECT DISTINCT stock_code FROM financial_data
            WHERE is_annual=1 AND year=2024 AND data_source='fnguide'
        )
    """).fetchall()
    return [r[0] for r in rows]


def _upsert_financial(conn: sqlite3.Connection, code: str, year: int, quarter: int,
                      report_type: str, data: dict, dry_run: bool) -> str:
    if not data:
        return "skip"
    if dry_run:
        return "dry"
    existing = conn.execute("""
        SELECT id FROM financial_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND report_type=? AND data_source IS NULL
    """, (code, year, quarter, report_type)).fetchone()
    fields = ["revenue","operating_profit","net_income","total_assets","total_equity"]
    vals = {k: data.get(k) for k in fields}
    if existing:
        sets = ", ".join(f"{k}=?" for k, v in vals.items() if v is not None)
        args = [v for v in vals.values() if v is not None]
        if sets:
            conn.execute(f"UPDATE financial_data SET {sets}, data_source='dart' WHERE id=?",
                         args + [existing[0]])
        return "updated"
    conn.execute("""
        INSERT OR IGNORE INTO financial_data
          (stock_code, year, quarter, is_annual, report_type, data_source,
           revenue, operating_profit, net_income, total_assets, total_equity)
        VALUES (?,?,?,0,?,'dart',?,?,?,?,?)
    """, (code, year, quarter, report_type,
          vals.get("revenue"), vals.get("operating_profit"), vals.get("net_income"),
          vals.get("total_assets"), vals.get("total_equity")))
    return "inserted"


def _upsert_cashflow(conn: sqlite3.Connection, code: str, year: int, quarter: int,
                     report_type: str, data: dict, dry_run: bool) -> str:
    cf_keys = ["operating_cf","investing_cf","financing_cf","capex"]
    cf = {k: data.get(k) for k in cf_keys if data.get(k) is not None}
    if not cf:
        return "skip"
    if dry_run:
        return "dry"
    existing = conn.execute("""
        SELECT id FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND report_type=? AND data_source IS NULL
    """, (code, year, quarter, report_type)).fetchone()
    if existing:
        sets = ", ".join(f"{k}=?" for k in cf)
        conn.execute(f"UPDATE cash_flow_data SET {sets}, data_source='dart' WHERE id=?",
                     list(cf.values()) + [existing[0]])
        return "updated"
    conn.execute("""
        INSERT OR IGNORE INTO cash_flow_data
          (stock_code, year, quarter, is_annual, report_type, data_source,
           operating_cf, investing_cf, financing_cf, capex)
        VALUES (?,?,?,0,?,'dart',?,?,?,?)
    """, (code, year, quarter, report_type,
          cf.get("operating_cf"), cf.get("investing_cf"),
          cf.get("financing_cf"), cf.get("capex")))
    return "inserted"


def run(p1_only: bool, dry_run: bool, year: int = 2024):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stocks = _get_target_stocks(conn, p1_only)
    log.info(f"대상 종목: {len(stocks)}개, year={year}, dry_run={dry_run}")

    stats = {"total": len(stocks), "errors": 0, "inserted": 0, "updated": 0, "skipped": 0}

    for i, code in enumerate(stocks, 1):
        for fs_div, rt in [("CFS", "CFS"), ("OFS", "OFS")]:
            try:
                q1 = _fetch_dart_quarter(code, year, _RPRT_Q1, fs_div)
                time.sleep(0.3)
                h1 = _fetch_dart_quarter(code, year, _RPRT_H1, fs_div)
                time.sleep(0.3)
                m9 = _fetch_dart_quarter(code, year, _RPRT_9M, fs_div)
                time.sleep(0.3)

                keys = list(_ACCT_MAP.keys()) + list(_CF_MAP.keys())
                q2 = _subtract(h1, q1, keys) if h1 and q1 else None
                q3 = _subtract(m9, h1, keys) if m9 and h1 else None

                for q, data in [(1, q1), (2, q2), (3, q3)]:
                    if not data:
                        stats["skipped"] += 1
                        continue
                    r1 = _upsert_financial(conn, code, year, q, rt, data, dry_run)
                    r2 = _upsert_cashflow(conn, code, year, q, rt, data, dry_run)
                    if r1 == "inserted" or r2 == "inserted":
                        stats["inserted"] += 1
                    elif r1 == "updated" or r2 == "updated":
                        stats["updated"] += 1

            except Exception as e:
                log.warning(f"{code} {rt}: {e}")
                stats["errors"] += 1

        if i % 10 == 0:
            if not dry_run:
                conn.commit()
            log.info(f"[{i}/{len(stocks)}] {stats}")

    if not dry_run:
        conn.commit()
    conn.close()

    log.info(f"완료: {stats}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="전체 NULL-source 대상 (기본: P1 43종목)")
    ap.add_argument("--dry-run", action="store_true", help="DB 수정 없이 리포트만")
    ap.add_argument("--year", type=int, default=2024)
    args = ap.parse_args()
    result = run(p1_only=not args.all, dry_run=args.dry_run, year=args.year)
    print(json.dumps(result, ensure_ascii=False))
