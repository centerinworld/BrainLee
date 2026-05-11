"""
validate_financial_multi_source.py — 재무제표 다중 소스 검증 + 자동 보정

전략:
  1. FnGuide SVD_Finance.asp 1회 요청 → 손익계산서(연간/분기) + 재무상태표 + 현금흐름표
  2. DB financial_data (is_annual=1) 와 비교: 매출, 영업이익, 순이익
  3. FnGuide SVD_Main.asp 별도 요청 → EPS, BPS (--with-eps 플래그 시)
  4. 불일치 → financial_profiles_pending.json 자동 누적
  5. --backfill: NULL/0 값을 FnGuide 값으로 채움 (오차 허용범위 초과 값도 override 가능)
  6. --override: 불일치값도 FnGuide 기준으로 덮어쓰기

실행:
    # 검증만 (200종목, 2024년)
    python3 scripts/validate_financial_multi_source.py --limit 200

    # 전 종목 + 결측값 자동 채우기
    python3 scripts/validate_financial_multi_source.py --limit 9999 --backfill

    # 불일치도 FnGuide 기준으로 강제 수정
    python3 scripts/validate_financial_multi_source.py --limit 9999 --backfill --override

    # EPS/BPS 포함 (SVD_Main.asp 추가 요청, 2배 시간 소요)
    python3 scripts/validate_financial_multi_source.py --limit 500 --with-eps
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_rate_limiter import api_limiter
from financial_profiles import append_pending_mismatch, get_profile

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

TOLERANCE_PCT = 0.03    # 3% 이내 일치
TOLERANCE_ABS = 5e8     # 5억원 미만 차이는 노이즈

REPORT_PATH = Path(__file__).resolve().parent.parent / "scratch" / "validation_financial_multi.json"

FNGUIDE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://comp.fnguide.com/",
}


# ─────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _flatten(c) -> str:
    if isinstance(c, tuple):
        return " ".join(str(x) for x in c if str(x) != "nan")
    return str(c)


def _close_match(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    diff = abs(a - b)
    if diff < TOLERANCE_ABS:
        return True
    denom = max(abs(a), abs(b))
    if denom == 0:
        return diff < TOLERANCE_ABS
    return diff / denom <= TOLERANCE_PCT


def _parse_val(raw) -> Optional[float]:
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "N/A", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# FnGuide SVD_Finance.asp → 손익계산서 + 재무상태표 (연간)
# 반환: {year: {revenue, operating_profit, net_income, total_assets, total_equity}}
# 단위: FnGuide 억원 → × 1e8 → 원
# ─────────────────────────────────────────────────────────
def fetch_fnguide_annual_fs(stock_code: str) -> dict[int, dict[str, float]]:
    url = (
        "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"
    )
    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return {}

    prof = get_profile(stock_code)
    rev_kw  = list(prof.get("revenue_keywords", [])) + ["매출액", "영업수익", "매출"]
    op_kw   = ["영업이익"]
    ni_kw   = list(prof.get("net_income_keywords", [])) + ["지배주주순이익", "당기순이익"]
    ni_excl = list(prof.get("net_income_exclude_keywords", [])) + ["비지배"]

    result: dict[int, dict[str, float]] = {}

    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        first_col = t.columns[0]
        labels = t[first_col].astype(str).str.replace(" ", "", regex=False)

        # 연간 컬럼만 (YYYY/12 패턴)
        year_cols: dict[int, str] = {}
        for c in t.columns[1:]:
            m = re.search(r"(20\d{2})/12", str(c))
            if m:
                year_cols[int(m.group(1))] = c
        if not year_cols:
            continue

        def _find_row(keywords: list[str], excludes: list[str] = []) -> Optional[pd.Series]:
            mask = pd.Series([False] * len(t))
            for k in keywords:
                mask = mask | labels.str.contains(re.escape(k.replace(" ", "")), na=False)
            for ex in excludes:
                mask = mask & ~labels.str.contains(re.escape(ex.replace(" ", "")), na=False)
            rows = t.loc[mask]
            return rows.iloc[0] if not rows.empty else None

        # 손익계산서 (테이블0 — 매출/영업이익/순이익)
        rev_row = _find_row(rev_kw)
        op_row  = _find_row(op_kw)
        ni_row  = _find_row(ni_kw, ni_excl)

        # 재무상태표 (테이블2 — 자산/자본)
        asset_row  = _find_row(["자산"])
        equity_row = _find_row(["자본"], ["비지배", "기타"])

        has_any = any(r is not None for r in [rev_row, op_row, ni_row, asset_row, equity_row])
        if not has_any:
            continue

        for y, col in year_cols.items():
            yvals = result.setdefault(y, {})
            for key, row in [
                ("revenue",        rev_row),
                ("operating_profit", op_row),
                ("net_income",     ni_row),
                ("total_assets",   asset_row),
                ("total_equity",   equity_row),
            ]:
                if row is None or key in yvals:
                    continue
                v = _parse_val(row[col])
                if v is not None:
                    yvals[key] = v * 1e8   # 억원 → 원

    return result


# ─────────────────────────────────────────────────────────
# FnGuide SVD_Main.asp → EPS, BPS (연간, IFRS 연결)
# 반환: {year: {eps, bps}}
# ─────────────────────────────────────────────────────────
def fetch_fnguide_eps_bps(stock_code: str) -> dict[int, dict[str, float]]:
    url = (
        "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"
    )
    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return {}

    result: dict[int, dict[str, float]] = {}

    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        labels = t.iloc[:, 0].astype(str)

        # EPS/BPS 행 탐색
        eps_mask = labels.str.contains("EPS", na=False)
        bps_mask = labels.str.contains("BPS", na=False)
        if not eps_mask.any() and not bps_mask.any():
            continue

        # Annual YYYY/12 컬럼
        year_cols: dict[int, str] = {}
        for c in t.columns[1:]:
            m = re.search(r"Annual\s*(20\d{2})/12", str(c))
            if not m:
                m = re.search(r"(20\d{2})/12", str(c))
            if m:
                year_cols[int(m.group(1))] = c

        # "E" 예측 컬럼 제외
        year_cols = {y: c for y, c in year_cols.items() if "(E)" not in str(c)}
        if not year_cols:
            continue

        for y, col in year_cols.items():
            yvals = result.setdefault(y, {})
            if eps_mask.any():
                v = _parse_val(t.loc[eps_mask].iloc[0][col])
                if v is not None and "eps" not in yvals:
                    yvals["eps"] = v
            if bps_mask.any():
                v = _parse_val(t.loc[bps_mask].iloc[0][col])
                if v is not None and "bps" not in yvals:
                    yvals["bps"] = v

    return result


# ─────────────────────────────────────────────────────────
# 메인 검증
# ─────────────────────────────────────────────────────────
def run(
    limit: int,
    year: Optional[int],
    with_eps: bool,
    backfill: bool,
    override: bool,
) -> dict:
    conn = _conn()
    cur = conn.cursor()

    where_year = "AND fd.year = ?" if year else ""
    params_year = [year] if year else []

    rows = cur.execute(f"""
        SELECT fd.id, fd.stock_code, fd.year,
               fd.revenue, fd.operating_profit, fd.net_income,
               fd.eps, fd.bps,
               u.stock_name
        FROM financial_data fd
        JOIN stock_universe u ON u.stock_code = fd.stock_code
        WHERE fd.is_annual = 1
          {where_year}
          AND fd.year >= 2018
          AND u.market IN ('유가증권', '코스닥', 'KOSPI', 'KOSDAQ')
          AND COALESCE(u.stock_type, '보통주') = '보통주'
          AND COALESCE(u.stock_name, '') NOT LIKE '%ETF%'
          AND COALESCE(u.stock_name, '') NOT LIKE '%ETN%'
        ORDER BY fd.stock_code, fd.year DESC
        LIMIT ?
    """, (*params_year, limit)).fetchall()

    counters = {k: {"match": 0, "miss": 0, "filled": 0, "overridden": 0}
                for k in ("revenue", "operating_profit", "net_income", "eps", "bps")}
    no_source = 0
    checked = 0
    mismatch_samples: list[dict] = []
    filled_samples: list[dict] = []

    # 종목별로 묶어서 처리 (한 종목에 여러 연도 → 1회 요청)
    from collections import defaultdict
    by_code: dict[str, list] = defaultdict(list)
    for r in rows:
        by_code[r["stock_code"]].append(r)

    for code, code_rows in by_code.items():
        fs_data = fetch_fnguide_annual_fs(code)
        eps_data = fetch_fnguide_eps_bps(code) if with_eps else {}

        if not fs_data and not eps_data:
            no_source += len(code_rows)
            continue

        for r in code_rows:
            yr = r["year"]
            fs_yr = fs_data.get(yr, {})
            ep_yr = eps_data.get(yr, {})

            if not fs_yr and not ep_yr:
                no_source += 1
                continue

            checked += 1
            updates: dict[str, float] = {}
            name = r["stock_name"]

            # 손익계산서 필드 검증
            for key in ("revenue", "operating_profit", "net_income"):
                db_val = r[key]
                ext_val = fs_yr.get(key)
                if ext_val is None:
                    continue

                if db_val is None or db_val == 0:
                    # 결측값 → 채우기
                    if backfill:
                        updates[key] = ext_val
                        counters[key]["filled"] += 1
                        if len(filled_samples) < 20:
                            filled_samples.append({"code": code, "name": name,
                                "year": yr, "key": key, "value": ext_val})
                    continue

                if _close_match(db_val, ext_val):
                    counters[key]["match"] += 1
                else:
                    counters[key]["miss"] += 1
                    append_pending_mismatch(
                        code,
                        reason=f"fs_{key}_mismatch_{yr}",
                        details={
                            "year": yr, "db": db_val, "external": ext_val,
                            "source": "fnguide",
                            "diff": db_val - ext_val,
                            "diff_pct": ((db_val - ext_val) / ext_val * 100) if ext_val else None,
                        },
                    )
                    if len(mismatch_samples) < 40:
                        mismatch_samples.append({
                            "stock_code": code, "stock_name": name, "year": yr, "key": key,
                            "db": db_val, "external": ext_val,
                            "diff_pct": round((db_val - ext_val) / abs(ext_val) * 100, 1) if ext_val else None,
                        })
                    if override and backfill:
                        updates[key] = ext_val
                        counters[key]["overridden"] += 1

            # EPS / BPS 검증
            for key in ("eps", "bps"):
                src = ep_yr if with_eps else {}
                ext_val = src.get(key)
                if ext_val is None:
                    continue
                db_val = r[key]

                if db_val is None or db_val == 0:
                    if backfill:
                        updates[key] = ext_val
                        counters[key]["filled"] += 1
                    continue

                # EPS 단위: DB=원, FnGuide=원 (원 단위 그대로)
                if _close_match(db_val, ext_val):
                    counters[key]["match"] += 1
                else:
                    counters[key]["miss"] += 1
                    append_pending_mismatch(
                        code,
                        reason=f"fs_{key}_mismatch_{yr}",
                        details={"year": yr, "db": db_val, "external": ext_val, "source": "fnguide_main"},
                    )
                    if override and backfill:
                        updates[key] = ext_val
                        counters[key]["overridden"] += 1

            # DB UPDATE
            if updates and backfill:
                sets = ", ".join(f"{k}=?" for k in updates)
                vals = list(updates.values()) + [r["id"]]
                conn.execute(f"UPDATE financial_data SET {sets} WHERE id=?", vals)

    if backfill:
        conn.commit()
    conn.close()

    # 일치율
    rates: dict[str, float | None] = {}
    for k, c in counters.items():
        total = c["match"] + c["miss"]
        rates[k] = round(c["match"] / total * 100, 2) if total else None

    report = {
        "year_filter": year,
        "limit": limit,
        "with_eps": with_eps,
        "backfill": backfill,
        "override": override,
        "rows_queried": len(rows),
        "stocks_queried": len(by_code),
        "no_source": no_source,
        "checked": checked,
        "counters": {k: dict(v) for k, v in counters.items()},
        "match_rates_pct": rates,
        "tolerance_pct": TOLERANCE_PCT * 100,
        "mismatch_samples": mismatch_samples,
        "filled_samples": filled_samples,
    }

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info(f"리포트: {REPORT_PATH}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",    type=int,  default=200, help="처리할 DB 행수")
    ap.add_argument("--year",     type=int,  default=None, help="특정 연도만 (없으면 전체)")
    ap.add_argument("--with-eps", action="store_true",  help="EPS/BPS 검증 포함 (SVD_Main 추가 요청)")
    ap.add_argument("--backfill", action="store_true",  help="NULL/0 값 자동 채우기")
    ap.add_argument("--override", action="store_true",  help="불일치값도 FnGuide로 덮어쓰기 (--backfill 필요)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = run(
        limit=args.limit,
        year=args.year,
        with_eps=args.with_eps,
        backfill=args.backfill,
        override=args.override,
    )
    print(json.dumps({
        "match_rates_pct": res["match_rates_pct"],
        "counters": res["counters"],
        "checked": res["checked"],
        "no_source": res["no_source"],
        "mismatch_samples": res["mismatch_samples"][:5],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
