"""
ai_verify_financials.py — 2번째 AI가 financial_source_snapshot을 검증

방법:
  1. financial_source_snapshot의 'unverified' 레코드 조회
  2. FnGuide/Naver에서 동일 데이터 재확인 (독립 재요청)
  3. snapshot 값과 독립 요청값 비교
  4. verification_status 업데이트: match / mismatch / partial
  5. financial_data의 DB값과도 3자 교차 검증
  6. 요약 보고서 → scratch/ai_verification_report.json

이 스크립트는 Claude 또는 다른 AI 모델이 실행 가능.
`--verifier` 인자로 검증 AI 식별자를 기록.

실행:
    # 현재 AI (Claude)가 검증
    python3 scripts/ai_verify_financials.py --limit 50 --verifier "claude-sonnet-4-6"

    # 다른 AI를 위한 검증 배치 준비 (snapshot JSON 출력)
    python3 scripts/ai_verify_financials.py --export-for-review --limit 100

    # 특정 종목
    python3 scripts/ai_verify_financials.py --codes 005930 000660 --verifier "claude-opus-4"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_rate_limiter import api_limiter

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"
REPORT_PATH = Path(__file__).resolve().parent.parent / "scratch" / "ai_verification_report.json"

FNGUIDE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://comp.fnguide.com/",
}
REPORT_TYPE_PARAMS = {"CFS": "", "OFS": "B"}
TOLERANCE_PCT = 0.02   # 2% — 검증은 더 엄격하게
TOLERANCE_ABS = 1e8    # 1억원 이하 차이 허용


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def _close_match(a, b):
    if a is None or b is None:
        return False
    diff = abs(a - b)
    if diff < TOLERANCE_ABS:
        return True
    denom = max(abs(a), abs(b))
    return denom > 0 and diff / denom <= TOLERANCE_PCT


def _flatten(c):
    if isinstance(c, tuple):
        return " ".join(str(x) for x in c if str(x) != "nan")
    return str(c)


def _parse_val(raw):
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "N/A", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def re_fetch_fnguide(stock_code: str, report_type: str, year: int) -> dict:
    """독립적으로 FnGuide 재요청 → 해당 연도 값만 추출."""
    gb = REPORT_TYPE_PARAMS.get(report_type, "")
    url = (
        f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y"
        f"&ReportGB={gb}&NewMenuID=103&stkGb=701"
    )
    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return {}

    target_col_pattern = f"{year}/12"
    result: dict = {}

    kw_map = {
        "revenue":          (["매출액", "영업수익", "매출"],           []),
        "operating_profit": (["영업이익"],                              []),
        "net_income":       (["지배주주순이익", "당기순이익"],          ["비지배"]),
        "total_assets":     (["자산"],                                  []),
        "total_equity":     (["자본"],                                  ["비지배", "기타금융"]),
        "operating_cf":     (["영업활동으로인한현금흐름", "영업활동현금흐름"], []),
        "investing_cf":     (["투자활동으로인한현금흐름", "투자활동현금흐름"], []),
        "financing_cf":     (["재무활동으로인한현금흐름", "재무활동현금흐름"], []),
    }

    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        labels = t.iloc[:, 0].astype(str).str.replace(" ", "", regex=False)
        target_col = next((c for c in t.columns if target_col_pattern in str(c)), None)
        if target_col is None:
            continue

        for key, (kws, excls) in kw_map.items():
            if key in result:
                continue
            mask = pd.Series([False] * len(t))
            for k in kws:
                mask = mask | labels.str.contains(re.escape(k.replace(" ", "")), na=False)
            for ex in excls:
                mask = mask & ~labels.str.contains(re.escape(ex.replace(" ", "")), na=False)
            rows = t.loc[mask]
            if rows.empty:
                continue
            v = _parse_val(rows.iloc[0][target_col])
            if v is not None:
                result[key] = v * 1e8   # 억원 → 원

    return result


def verify_snapshots(
    conn: sqlite3.Connection,
    codes: Optional[list[str]],
    limit: int,
    verifier: str,
) -> list[dict]:
    """
    unverified 스냅샷을 독립 재확인하여 verification_status 업데이트.
    반환: 검증 결과 리스트
    """
    if codes:
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(f"""
            SELECT id, stock_code, year, quarter, is_annual, report_type, data_source,
                   revenue, operating_profit, net_income, total_assets, total_equity,
                   operating_cf, investing_cf, financing_cf, fetched_at
            FROM financial_source_snapshot
            WHERE stock_code IN ({placeholders})
              AND is_annual = 1
              AND verification_status = 'unverified'
            ORDER BY stock_code, year DESC
            LIMIT ?
        """, (*codes, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, stock_code, year, quarter, is_annual, report_type, data_source,
                   revenue, operating_profit, net_income, total_assets, total_equity,
                   operating_cf, investing_cf, financing_cf, fetched_at
            FROM financial_source_snapshot
            WHERE is_annual = 1
              AND verification_status = 'unverified'
              AND data_source = 'fnguide'
            ORDER BY stock_code, year DESC
            LIMIT ?
        """, (limit,)).fetchall()

    results = []
    checked_codes: dict[tuple, dict] = {}  # (code, rt) → fresh_data

    verified_at = datetime.now(timezone.utc).isoformat()

    for snap in rows:
        code = snap["stock_code"]
        yr   = snap["year"]
        rt   = snap["report_type"]

        # 동일 (code, rt) 재요청 캐시
        cache_key = (code, rt)
        if cache_key not in checked_codes:
            checked_codes[cache_key] = re_fetch_fnguide(code, rt, yr)
        fresh = checked_codes[cache_key]

        # 비교 필드
        fields = ["revenue", "operating_profit", "net_income",
                  "total_assets", "total_equity",
                  "operating_cf", "investing_cf", "financing_cf"]

        mismatches: list[str] = []
        matches: list[str] = []

        for f in fields:
            snap_val = snap[f]
            fresh_val = fresh.get(f)
            if snap_val is None or fresh_val is None:
                continue
            if _close_match(snap_val, fresh_val):
                matches.append(f)
            else:
                mismatches.append(
                    f"{f}: snapshot={snap_val/1e8:.1f}억 / re-fetch={fresh_val/1e8:.1f}억"
                )

        if not matches and not mismatches:
            status = "unverified"
        elif not mismatches:
            status = "match"
        elif not matches:
            status = "mismatch"
        else:
            status = "partial"

        note = "; ".join(mismatches) if mismatches else "all checked fields match"

        # DB값과도 3자 교차 검증
        db_row = conn.execute("""
            SELECT revenue, operating_profit, net_income
            FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=0 AND is_annual=1 AND report_type=?
        """, (code, yr, rt)).fetchone()

        db_mismatches: list[str] = []
        if db_row:
            for f in ("revenue", "operating_profit", "net_income"):
                db_val = db_row[f]
                fresh_val = fresh.get(f)
                if db_val and fresh_val and not _close_match(db_val, fresh_val):
                    db_mismatches.append(
                        f"DB.{f}={db_val/1e8:.1f}억 vs FnGuide={fresh_val/1e8:.1f}억"
                    )

        full_note = note
        if db_mismatches:
            full_note += " | DB 불일치: " + "; ".join(db_mismatches[:3])

        # 스냅샷 업데이트
        conn.execute("""
            UPDATE financial_source_snapshot
            SET verified_by=?, verified_at=?, verification_status=?, verification_note=?
            WHERE id=?
        """, (verifier, verified_at, status, full_note[:2000], snap["id"]))

        results.append({
            "snapshot_id": snap["id"],
            "stock_code": code,
            "year": yr,
            "report_type": rt,
            "status": status,
            "mismatches": mismatches[:5],
            "db_mismatches": db_mismatches[:3],
        })

    conn.commit()
    return results


def export_for_review(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """다른 AI가 검토할 수 있도록 스냅샷 JSON 내보내기."""
    rows = conn.execute("""
        SELECT fss.stock_code, su.stock_name, fss.year, fss.report_type,
               fss.revenue, fss.operating_profit, fss.net_income,
               fss.total_assets, fss.total_equity,
               fss.operating_cf, fss.investing_cf, fss.financing_cf,
               fss.fetched_at, fss.verification_status,
               fd.revenue AS db_revenue, fd.operating_profit AS db_op,
               fd.net_income AS db_ni
        FROM financial_source_snapshot fss
        JOIN stock_universe su ON su.stock_code = fss.stock_code
        LEFT JOIN financial_data fd ON fd.stock_code=fss.stock_code
            AND fd.year=fss.year AND fd.quarter=0 AND fd.is_annual=1
            AND fd.report_type=fss.report_type
        WHERE fss.is_annual = 1
          AND fss.data_source = 'fnguide'
        ORDER BY fss.stock_code, fss.year DESC
        LIMIT ?
    """, (limit,)).fetchall()

    result = []
    for r in rows:
        def fmt(v):
            return round(v / 1e8, 1) if v else None  # 억원

        entry = {
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "year": r["year"],
            "report_type": r["report_type"],
            "fnguide_values": {
                "revenue_억": fmt(r["revenue"]),
                "operating_profit_억": fmt(r["operating_profit"]),
                "net_income_억": fmt(r["net_income"]),
                "total_assets_억": fmt(r["total_assets"]),
                "total_equity_억": fmt(r["total_equity"]),
                "operating_cf_억": fmt(r["operating_cf"]),
                "investing_cf_억": fmt(r["investing_cf"]),
                "financing_cf_억": fmt(r["financing_cf"]),
            },
            "db_values": {
                "revenue_억": fmt(r["db_revenue"]),
                "operating_profit_억": fmt(r["db_op"]),
                "net_income_억": fmt(r["db_ni"]),
            },
            "fetched_at": r["fetched_at"],
            "verification_status": r["verification_status"],
        }
        result.append(entry)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",    type=int,   default=100)
    ap.add_argument("--verifier", type=str,   default="claude-sonnet-4-6")
    ap.add_argument("--codes",    nargs="*",  help="특정 종목코드")
    ap.add_argument("--export-for-review", action="store_true",
                    help="다른 AI를 위한 JSON 내보내기 (검증 미실행)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = _conn()

    if args.export_for_review:
        data = export_for_review(conn, args.limit)
        out = Path(__file__).resolve().parent.parent / "scratch" / "fnguide_for_review.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"내보내기 완료: {out} ({len(data)}건)")
        conn.close()
        return

    results = verify_snapshots(conn, args.codes, args.limit, args.verifier)

    summary = {
        "verifier": args.verifier,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "match":    sum(1 for r in results if r["status"] == "match"),
        "partial":  sum(1 for r in results if r["status"] == "partial"),
        "mismatch": sum(1 for r in results if r["status"] == "mismatch"),
        "samples": results[:20],
    }

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info(f"검증 완료: {summary['match']}일치 / {summary['partial']}부분 / {summary['mismatch']}불일치")
    logger.info(f"리포트: {REPORT_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
