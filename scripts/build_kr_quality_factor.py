#!/usr/bin/env python3
"""
scripts/build_kr_quality_factor.py

한국 주식 Quality 팩터 스코어 산출 및 kr_quality_factor 테이블 갱신.

Quality 5요소:
  1. ROE            — 자기자본이익률 (높을수록 ↑)
  2. op_margin      — 영업이익률 = operating_profit / revenue (높을수록 ↑)
  3. fcf_yield      — FCF 수익률 (cash_conversion_signals.fcf_yield_pct, 높을수록 ↑)
  4. leverage_inv   — 부채비율 역수 = equity / (liabilities+equity) (낮은 부채 = 높은 점수)
  5. accruals_inv   — 발생액 역수 = 1 - |net_income - ocf| / total_assets (낮은 발생액 = 높은 점수)

각 요소를 분위(0~1)로 정규화 → 평균 → quality_score (0~1)
퍼센타일 기반 분류: quality_grade A(상위20%) / B(40%) / C(60%) / D(80%) / F(하위20%)

실행:
  python3 scripts/build_kr_quality_factor.py           # 증분(최신 분기)
  python3 scripts/build_kr_quality_factor.py --full    # 전체 재빌드
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db


def _rank_normalize(values: list[float | None]) -> list[float]:
    """None 제외 후 퍼센타일 순위 0~1 반환. None은 0.5(중립)."""
    valid = sorted(v for v in values if v is not None)
    n = len(valid)
    if n == 0:
        return [0.5] * len(values)
    result = []
    for v in values:
        if v is None:
            result.append(0.5)
        else:
            rank = sum(1 for x in valid if x <= v)
            result.append(rank / n)
    return result


def build(full: bool = False) -> dict:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_quality_factor (
            stock_code      TEXT,
            year            INTEGER,
            quarter         INTEGER,
            roe             DOUBLE PRECISION,
            op_margin       DOUBLE PRECISION,
            fcf_yield       DOUBLE PRECISION,
            leverage_inv    DOUBLE PRECISION,
            accruals_inv    DOUBLE PRECISION,
            quality_score   DOUBLE PRECISION,
            quality_grade   TEXT,
            updated_at      TEXT,
            PRIMARY KEY (stock_code, year, quarter)
        )
    """)
    conn.commit()

    # 최신 분기 파악 (revenue IS NOT NULL인 유효 행 기준)
    row = conn.execute("""
        SELECT year, quarter FROM financial_data
        WHERE is_annual=false AND revenue IS NOT NULL AND year IS NOT NULL
        ORDER BY year DESC, quarter DESC LIMIT 1
    """).fetchone()
    latest_year, latest_quarter = int(row[0] or 2026), int(row[1] or 2)

    if full:
        where_fd = "fd.is_annual=false AND fd.revenue IS NOT NULL"
        mode = "전체 재빌드"
    else:
        where_fd = (
            f"fd.is_annual=false AND fd.revenue IS NOT NULL"
            f" AND fd.year={latest_year} AND fd.quarter={latest_quarter}"
        )
        mode = f"증분({latest_year}Q{latest_quarter})"

    rows = conn.execute(f"""
        SELECT
            fd.stock_code,
            fd.year,
            fd.quarter,
            fd.roe,
            CASE WHEN fd.revenue IS NOT NULL AND fd.revenue <> 0
                 THEN fd.operating_profit::float / fd.revenue::float
                 ELSE NULL END AS op_margin,
            ccs.fcf_yield_pct,
            CASE WHEN (fd.total_equity + COALESCE(fd.total_liabilities, 0)) > 0
                 THEN fd.total_equity::float / (fd.total_equity + COALESCE(fd.total_liabilities, fd.total_equity))::float
                 ELSE NULL END AS leverage_inv,
            CASE WHEN fd.total_assets IS NOT NULL AND fd.total_assets <> 0
                      AND ccs.operating_cf IS NOT NULL
                 THEN 1.0 - ABS(fd.net_income::float - ccs.operating_cf::float) / fd.total_assets::float
                 ELSE NULL END AS accruals_inv
        FROM financial_data fd
        LEFT JOIN cash_conversion_signals ccs
               ON ccs.stock_code = fd.stock_code
              AND ccs.fiscal_year = fd.year
              AND ccs.fiscal_quarter = fd.quarter
              AND ccs.fs_div = 'CFS'
        WHERE {where_fd}
          AND fd.stock_code IS NOT NULL
          AND fd.stock_code ~ '^[0-9]{{6}}$'
        ORDER BY fd.stock_code, fd.year, fd.quarter
    """).fetchall()

    if not rows:
        print(f"[{mode}] 처리 대상 없음")
        conn.close()
        return {"mode": mode, "processed": 0}

    print(f"[{mode}] 처리 대상: {len(rows):,}건")

    # 컬럼별 정규화
    roes      = [float(r[3]) if r[3] is not None else None for r in rows]
    op_margins = [float(r[4]) if r[4] is not None else None for r in rows]
    fcf_yields = [float(r[5]) if r[5] is not None else None for r in rows]
    lev_invs   = [float(r[6]) if r[6] is not None else None for r in rows]
    acc_invs   = [float(r[7]) if r[7] is not None else None for r in rows]

    roe_n  = _rank_normalize(roes)
    opm_n  = _rank_normalize(op_margins)
    fcf_n  = _rank_normalize(fcf_yields)
    lev_n  = _rank_normalize(lev_invs)
    acc_n  = _rank_normalize(acc_invs)

    now = datetime.now().isoformat(timespec="seconds")
    upsert_rows = []
    scores = []
    for i, r in enumerate(rows):
        score = (roe_n[i] + opm_n[i] + fcf_n[i] + lev_n[i] + acc_n[i]) / 5.0
        scores.append(score)
        upsert_rows.append((
            r[0], int(r[1]), int(r[2]),
            roes[i], op_margins[i], fcf_yields[i], lev_invs[i], acc_invs[i],
            round(score, 4),
            None,  # grade 나중에
            now,
        ))

    # 퍼센타일 기반 등급 배분 (A:상위20%, B:40%, C:60%, D:80%, F:하위20%)
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    thresholds = {
        "A": sorted_scores[int(n * 0.8)] if n > 0 else 1,
        "B": sorted_scores[int(n * 0.6)] if n > 0 else 1,
        "C": sorted_scores[int(n * 0.4)] if n > 0 else 1,
        "D": sorted_scores[int(n * 0.2)] if n > 0 else 1,
    }

    def grade(s: float) -> str:
        if s >= thresholds["A"]:
            return "A"
        if s >= thresholds["B"]:
            return "B"
        if s >= thresholds["C"]:
            return "C"
        if s >= thresholds["D"]:
            return "D"
        return "F"

    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for i in range(len(upsert_rows)):
        g = grade(scores[i])
        grade_counts[g] += 1
        row_list = list(upsert_rows[i])
        row_list[9] = g
        upsert_rows[i] = tuple(row_list)

    BATCH = 2000
    for i in range(0, len(upsert_rows), BATCH):
        chunk = upsert_rows[i:i + BATCH]
        for row in chunk:
            conn.execute("""
                INSERT INTO kr_quality_factor
                  (stock_code, year, quarter, roe, op_margin, fcf_yield,
                   leverage_inv, accruals_inv, quality_score, quality_grade, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (stock_code, year, quarter) DO UPDATE SET
                  roe=EXCLUDED.roe, op_margin=EXCLUDED.op_margin,
                  fcf_yield=EXCLUDED.fcf_yield, leverage_inv=EXCLUDED.leverage_inv,
                  accruals_inv=EXCLUDED.accruals_inv, quality_score=EXCLUDED.quality_score,
                  quality_grade=EXCLUDED.quality_grade, updated_at=EXCLUDED.updated_at
            """, row)
        conn.commit()
        print(f"  {min(i + BATCH, len(upsert_rows)):,}/{len(upsert_rows):,} 처리됨")

    conn.close()
    result = {"mode": mode, "processed": len(upsert_rows), "grades": grade_counts}
    print(f"완료: {result}")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    build(full=args.full)
