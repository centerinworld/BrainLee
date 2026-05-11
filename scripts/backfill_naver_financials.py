#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from io import StringIO
from typing import Optional

import pandas as pd
import requests

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from api_rate_limiter import api_limiter

DB_PATH = "/Applications/stock_dashboard/stock.db"


@dataclass
class FillResult:
    scanned_rows: int = 0
    fetched_stocks: int = 0
    updated_cells: int = 0
    skipped_cells: int = 0
    failed_stocks: int = 0


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def target_rows(conn: sqlite3.Connection, limit: int, years: int) -> list[sqlite3.Row]:
    cur_year = time.localtime().tm_year
    min_year = cur_year - years
    return conn.execute(
        """
        SELECT fd.stock_code, su.stock_name, fd.year, fd.quarter,
               fd.revenue, fd.operating_profit, fd.net_income, fd.eps,
               ann.net_income AS annual_net_income
        FROM financial_data fd
        JOIN stock_universe su ON su.stock_code = fd.stock_code
        LEFT JOIN financial_data ann
          ON ann.stock_code = fd.stock_code
         AND ann.year = fd.year
         AND ann.quarter = 0
         AND ann.is_annual = 1
        WHERE fd.is_annual = 0
          AND fd.year >= ?
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
          AND (
              fd.net_income IS NULL OR fd.net_income = 0
              OR fd.eps IS NULL OR fd.eps = 0
          )
        ORDER BY fd.year DESC, fd.quarter DESC, fd.stock_code
        LIMIT ?
        """,
        (min_year, limit),
    ).fetchall()


def _pick_table(tables: list[pd.DataFrame]) -> Optional[pd.DataFrame]:
    for t in tables:
        first_col = str(t.columns[0])
        if "주요재무정보" in first_col:
            return t
    return tables[0] if tables else None


def _flatten_col(c) -> str:
    if isinstance(c, tuple):
        return " ".join(str(x).strip() for x in c if str(x).strip())
    return str(c).strip()


def parse_naver_quarterly_metrics(
    html: str, year: int, quarter: int, stock_code: str = "",
) -> dict[str, Optional[float]]:
    """
    네이버 분기 재무 표를 파싱.
    종목별 financial_profiles.json 키워드 우선 적용 (없으면 기본 룰).
    제외 키워드(예: '귀속/비지배')도 종목별 프로파일에서 가져와 적용.
    """
    # 종목별 프로파일 로드 (캐시)
    try:
        from financial_profiles import get_profile
        prof = get_profile(stock_code or "")
    except Exception:
        prof = {}

    base_rev_kw = ["매출액", "영업수익", "매출"]
    base_op_kw  = ["영업이익"]
    base_ni_kw  = ["당기순이익", "분기순이익", "반기순이익", "당기순손익"]
    base_eps_kw = ["EPS", "주당순이익", "기본주당이익"]

    rev_kw = list(prof.get("revenue_keywords", [])) + base_rev_kw
    op_kw  = base_op_kw
    ni_kw  = list(prof.get("net_income_keywords", [])) + base_ni_kw
    eps_kw = list(prof.get("eps_keywords", [])) + base_eps_kw
    ni_excludes = tuple(prof.get("net_income_exclude_keywords", []))

    tables = pd.read_html(StringIO(html))
    t = _pick_table(tables)
    if t is None or t.empty:
        return {"revenue": None, "operating_profit": None, "net_income": None, "eps": None}

    t = t.copy()
    t.columns = [_flatten_col(c) for c in t.columns]
    metric_col = t.columns[0]
    month_map = {1: "03", 2: "06", 3: "09", 4: "12"}
    ym_key = f"{year}.{month_map[quarter]}"
    cand_cols = [c for c in t.columns if ym_key in c]
    if not cand_cols:
        return {"revenue": None, "operating_profit": None, "net_income": None, "eps": None}
    target_col = cand_cols[-1]

    metric_series = t[metric_col].astype(str).str.replace(" ", "", regex=False)

    def find_row(keywords: list[str], excludes: tuple = ()) -> Optional[float]:
        mask = False
        for k in keywords:
            mask = mask | metric_series.str.contains(k, na=False)
        # 제외 키워드 적용 (예: net_income에서 '귀속/비지배' 차단)
        for ex in excludes:
            mask = mask & ~metric_series.str.contains(ex, na=False)
        rows = t.loc[mask, target_col]
        if rows.empty:
            return None
        # 비영(non-null/non-zero) 값 우선
        for val in rows:
            try:
                if pd.isna(val):
                    continue
                f = float(str(val).replace(",", ""))
                if f != 0.0:
                    return f
            except Exception:
                continue
        # 모두 0이면 첫 번째 반환
        try:
            v = rows.iloc[0]
            if pd.isna(v):
                return None
            return float(str(v).replace(",", ""))
        except Exception:
            return None

    return {
        "revenue":          find_row(rev_kw),
        "operating_profit": find_row(op_kw),
        "net_income":       find_row(ni_kw, excludes=ni_excludes),
        "eps":              find_row(eps_kw),
    }


def valid_net_income(existing_revenue: Optional[float], candidate_krw: float, annual_net_income: Optional[float]) -> bool:
    if not math.isfinite(candidate_krw):
        return False
    # 과도한 이상치 차단
    if abs(candidate_krw) > 2e14:
        return False
    if existing_revenue and existing_revenue > 0:
        if abs(candidate_krw) > existing_revenue * 5:
            return False
    if annual_net_income and annual_net_income != 0:
        if abs(candidate_krw) > abs(annual_net_income) * 5:
            return False
    return True


def run(limit: int, years: int, sleep_sec: float) -> FillResult:
    conn = get_conn()
    rows = target_rows(conn, limit=limit, years=years)
    result = FillResult(scanned_rows=len(rows))

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.naver.com/",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    sess = requests.Session()
    sess.headers.update(headers)

    # NAVER 중앙 제한 설정이 존재하면 스크립트 sleep은 보조값으로만 사용
    use_central_rl = True

    sess.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.naver.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )

    stock_cache: dict[str, dict[tuple[int, int], dict[str, Optional[float]]]] = {}

    for i, r in enumerate(rows, 1):
        code = r["stock_code"]
        y = int(r["year"])
        q = int(r["quarter"])
        try:
            if code not in stock_cache:
                url = f"https://finance.naver.com/item/main.naver?code={code}"
                if use_central_rl:
                    resp = api_limiter.get("NAVER", url, timeout=20, headers=headers)
                else:
                    resp = sess.get(url, timeout=20)
                resp.raise_for_status()
                stock_cache[code] = {}
                # 최근 5년 x 4분기 키를 캐시
                for yy in range(y - 1, y + 2):
                    for qq in (1, 2, 3, 4):
                        stock_cache[code][(yy, qq)] = parse_naver_quarterly_metrics(resp.text, yy, qq, code)
                result.fetched_stocks += 1
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

            metrics = stock_cache[code].get((y, q), {})
            updates: list[tuple[str, float]] = []

            cand_ni = metrics.get("net_income")
            if cand_ni is not None:
                cand_ni_krw = cand_ni * 1e8
                if (r["net_income"] is None or r["net_income"] == 0) and valid_net_income(r["revenue"], cand_ni_krw, r["annual_net_income"]):
                    updates.append(("net_income", cand_ni_krw))
                elif r["net_income"] in (None, 0):
                    result.skipped_cells += 1

            cand_eps = metrics.get("eps")
            if cand_eps is not None and math.isfinite(cand_eps):
                if r["eps"] is None or r["eps"] == 0:
                    if abs(cand_eps) < 1_000_000:
                        updates.append(("eps", float(cand_eps)))
                    else:
                        result.skipped_cells += 1

            if updates:
                sets = ", ".join([f"{k}=?" for k, _ in updates])
                params = [v for _, v in updates] + [code, y, q]
                conn.execute(
                    f"""
                    UPDATE financial_data
                    SET {sets}
                    WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
                    """,
                    params,
                )
                result.updated_cells += len(updates)
            else:
                result.skipped_cells += 1

            if i % 100 == 0:
                print(f"[progress] {i}/{len(rows)} updated_cells={result.updated_cells}")
            if i % 50 == 0:
                conn.commit()
        except Exception:
            result.failed_stocks += 1

    conn.commit()
    conn.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="네이버 재무로 0/NULL 보강(검증 포함)")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.4)
    args = ap.parse_args()

    res = run(limit=args.limit, years=args.years, sleep_sec=args.sleep)
    print(
        f"[done] scanned_rows={res.scanned_rows} fetched_stocks={res.fetched_stocks} "
        f"updated_cells={res.updated_cells} skipped_cells={res.skipped_cells} failed_stocks={res.failed_stocks}"
    )


if __name__ == "__main__":
    main()
