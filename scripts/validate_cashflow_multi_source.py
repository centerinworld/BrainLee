"""
validate_cashflow_multi_source.py — 다중 소스 현금흐름표 검증 + 불일치 자동 누적

전략:
  1. 네이버 main → coinfo: 연간 CF 추출 (분기는 미제공 빈번)
  2. FnGuide finance/CompFinanceA: 연간 CF + 일부 분기
  3. DB의 cash_flow_data (is_annual=1)와 대조
  4. 일치율 측정 (영업/투자/재무CF 각각)
  5. 불일치 종목은 financial_profiles_pending.json에 자동 누적

사용:
    python3 scripts/validate_cashflow_multi_source.py --limit 100
    python3 scripts/validate_cashflow_multi_source.py --year 2024 --limit 500
    python3 scripts/validate_cashflow_multi_source.py --source naver --year 2024
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
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_rate_limiter import api_limiter
from financial_profiles import append_pending_mismatch, get_profile

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

# 일치 판정 허용 오차
TOLERANCE_PCT = 0.05    # 5% 이내면 일치
TOLERANCE_ABS = 1e8     # 1억원 미만 차이는 노이즈로 간주

REPORT_PATH = Path(__file__).resolve().parent.parent / "scratch" / "validation_cashflow_multi.json"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=30)


def _close_match(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    diff = abs(a - b)
    if diff < TOLERANCE_ABS:
        return True
    if max(abs(a), abs(b)) == 0:
        return diff < TOLERANCE_ABS
    return diff / max(abs(a), abs(b)) <= TOLERANCE_PCT


# ──────────────────────────────────────────────────────────
# Naver 연간 CF 추출
# ──────────────────────────────────────────────────────────
def _flatten(c) -> str:
    if isinstance(c, tuple):
        return " ".join(str(x) for x in c if str(x) != "nan")
    return str(c)


def fetch_fnguide_annual_cf(stock_code: str) -> dict[int, dict[str, float]]:
    """
    FnGuide SVD_Finance.asp에서 연간 CF 추출.
    반환: {year: {operating_cf, investing_cf, financing_cf}} (단위: 원)
    FnGuide 단위는 백만원이므로 *1e6 변환.
    """
    url = (
        "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"
    )
    resp = api_limiter.get("FNGUIDE", url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://comp.fnguide.com/",
    }, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return {}

    # 종목별 프로파일 키워드
    prof = get_profile(stock_code)
    op_kw  = list(prof.get("op_cf_keywords",  [])) + ["영업활동으로인한현금흐름", "영업활동현금흐름"]
    inv_kw = list(prof.get("inv_cf_keywords", [])) + ["투자활동으로인한현금흐름", "투자활동현금흐름"]
    fin_kw = list(prof.get("fin_cf_keywords", [])) + ["재무활동으로인한현금흐름", "재무활동현금흐름"]

    result: dict[int, dict[str, float]] = {}
    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        first_col = t.columns[0]
        labels = t[first_col].astype(str).str.replace(" ", "", regex=False)

        # 연도 컬럼: 'YYYY/12' 패턴 (FnGuide 표 헤더)
        year_cols: dict[int, str] = {}
        for c in t.columns[1:]:
            m = re.search(r"(20\d{2})/12", str(c))
            if m:
                year_cols[int(m.group(1))] = c
        if not year_cols:
            continue

        def _find(keywords: list[str]) -> Optional[pd.Series]:
            mask = False
            for k in keywords:
                mask = mask | labels.str.contains(re.escape(k.replace(" ", "")), na=False)
            rows = t.loc[mask]
            if rows.empty:
                return None
            return rows.iloc[0]

        op_row, inv_row, fin_row = _find(op_kw), _find(inv_kw), _find(fin_kw)
        if op_row is None and inv_row is None and fin_row is None:
            continue

        for y, col in year_cols.items():
            yvals = result.setdefault(y, {})
            for key, row in (("operating_cf", op_row),
                             ("investing_cf", inv_row),
                             ("financing_cf", fin_row)):
                if row is None or key in yvals:
                    continue
                try:
                    raw = str(row[col]).replace(",", "").strip()
                    if raw in ("", "-", "N/A", "nan", "NaN"):
                        continue
                    # FnGuide 단위는 억원 (페이지 헤더에 '단위 : 억원' 명시)
                    yvals[key] = float(raw) * 1e8
                except Exception:
                    continue

    return result


# ──────────────────────────────────────────────────────────
# 검증 + 누적
# ──────────────────────────────────────────────────────────
def run(limit: int, year: int, source: str) -> dict:
    conn = _conn()
    cur = conn.cursor()

    # 검증 대상: 코스피/코스닥 보통주 + 연간 CF 보유 + ETF/ETN 제외
    rows = cur.execute("""
        SELECT cf.stock_code, cf.year,
               cf.operating_cf, cf.investing_cf, cf.financing_cf,
               u.stock_name
        FROM cash_flow_data cf
        JOIN stock_universe u ON u.stock_code = cf.stock_code
        WHERE cf.is_annual = 1
          AND cf.year = ?
          AND cf.operating_cf IS NOT NULL
          AND u.market IN ('유가증권', '코스닥', 'KOSPI', 'KOSDAQ')
          AND COALESCE(u.stock_type, '보통주') = '보통주'
          AND COALESCE(u.stock_name, '') NOT LIKE '%ETF%'
          AND COALESCE(u.stock_name, '') NOT LIKE '%ETN%'
        ORDER BY cf.stock_code
        LIMIT ?
    """, (year, limit)).fetchall()

    matches = {"operating_cf": 0, "investing_cf": 0, "financing_cf": 0}
    misses  = {"operating_cf": 0, "investing_cf": 0, "financing_cf": 0}
    no_source = 0
    checked = 0
    mismatch_samples: list[dict] = []

    for code, yr, db_op, db_inv, db_fin, name in rows:
        if source == "fnguide":
            ext = fetch_fnguide_annual_cf(code)
        else:
            ext = {}
        ext_yr = ext.get(yr) if ext else None
        if not ext_yr:
            no_source += 1
            continue

        checked += 1
        for key, db_val in (("operating_cf", db_op),
                            ("investing_cf", db_inv),
                            ("financing_cf", db_fin)):
            ext_val = ext_yr.get(key)
            if ext_val is None or db_val is None:
                continue
            if _close_match(db_val, ext_val):
                matches[key] += 1
            else:
                misses[key] += 1
                # 불일치를 _pending.json에 자동 누적
                append_pending_mismatch(
                    code,
                    reason=f"cf_{key}_mismatch_{source}_{yr}",
                    details={
                        "year":     yr,
                        "db":       db_val,
                        "external": ext_val,
                        "source":   source,
                        "diff":     db_val - ext_val,
                        "diff_pct": ((db_val - ext_val) / ext_val * 100) if ext_val else None,
                    },
                )
                if len(mismatch_samples) < 30:
                    mismatch_samples.append({
                        "stock_code": code, "stock_name": name,
                        "year": yr, "key": key,
                        "db": db_val, "external": ext_val,
                    })

    # 일치율
    rates: dict[str, float | None] = {}
    for k in ("operating_cf", "investing_cf", "financing_cf"):
        total = matches[k] + misses[k]
        rates[k] = round(matches[k] / total * 100, 2) if total else None

    report = {
        "year": year,
        "source": source,
        "limit": limit,
        "rows_processed": len(rows),
        "no_source": no_source,
        "checked": checked,
        "match_counts": matches,
        "miss_counts":  misses,
        "match_rates_pct": rates,
        "tolerance_pct": TOLERANCE_PCT * 100,
        "tolerance_abs_krw": TOLERANCE_ABS,
        "mismatch_samples": mismatch_samples,
    }

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info(f"리포트: {REPORT_PATH}")
    logger.info(f"checked={checked} / no_source={no_source}")
    logger.info(f"match_rates: {rates}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--source", choices=["fnguide", "naver"], default="fnguide")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = run(args.limit, args.year, args.source)
    print(json.dumps(res, ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    main()
