#!/usr/bin/env python3
"""FCF 파생 지표 계산 — cash_conversion_signals 테이블에 5개 컬럼 추가/갱신.

파생 지표:
  fcf_yield_pct      : TTM FCF / 시가총액 %  (현재 시총 기준)
  pfcf_ratio         : 시가총액 / TTM FCF (배수, P/FCF)
  fcf_per_share_krw  : TTM FCF / 발행주식수 (원)
  fcf_to_ni_pct      : 당기 FCF / 당기순이익 % (이익 현금화율)
  fcf_yoy_pct        : TTM FCF YoY 성장률 %

실행:
  python3 scripts/compute_fcf_derived_signals.py            # 전체 재계산
  python3 scripts/compute_fcf_derived_signals.py --latest   # 종목별 최신 행만

단위:
  cash_conversion_signals.free_cf / rolling4_free_cf  →  원(KRW)
  stock_universe.market_cap                           →  억원 (×1e8 → 원)
  stock_universe.shares_issued                        →  주수
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


# ── 단위 ────────────────────────────────────────────────────────────────────
_억 = 1e8   # 억원 → 원 변환 계수
_YOY_CAP = 1000.0   # YoY 성장률 상한 (1000%)


def _pct(num, denom, cap=None):
    if num is None or denom is None or denom == 0:
        return None
    v = num / denom * 100.0
    if cap and v > cap:
        return cap
    if v < -100.0:
        return -100.0
    return round(v, 2)


def _ratio(num, denom):
    if num is None or denom is None or denom <= 0:
        return None
    return round(num / denom, 2)


# ── 스키마 마이그레이션 ───────────────────────────────────────────────────────
_NEW_COLS = {
    "fcf_yield_pct":     "DOUBLE PRECISION",
    "pfcf_ratio":        "DOUBLE PRECISION",
    "fcf_per_share_krw": "DOUBLE PRECISION",
    "fcf_to_ni_pct":     "DOUBLE PRECISION",
    "fcf_yoy_pct":       "DOUBLE PRECISION",
}


def ensure_columns(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='cash_conversion_signals'
    """)
    existing = {r[0] for r in cur.fetchall()}
    for col, typ in _NEW_COLS.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE cash_conversion_signals ADD COLUMN IF NOT EXISTS {col} {typ}")
            print(f"  컬럼 추가: {col}")
    conn.commit()


# ── 계산 ─────────────────────────────────────────────────────────────────────
def compute_all(conn, latest_only: bool = False) -> int:
    cur = conn.cursor()

    # 1. 시가총액·주식수 조회
    cur.execute("""
        SELECT stock_code, market_cap, shares_issued
        FROM stock_universe
        WHERE market_cap IS NOT NULL AND shares_issued > 0
          AND stock_code ~ '^[0-9]{6}$'
    """)
    universe = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    # 2. YoY 비교를 위해 전년 동분기 rolling4_free_cf 조회
    cur.execute("""
        SELECT DISTINCT ON (stock_code, fiscal_year, fiscal_quarter, fs_div)
               stock_code, fiscal_year, fiscal_quarter, fs_div, rolling4_free_cf
        FROM cash_conversion_signals
        WHERE rolling4_free_cf IS NOT NULL
        ORDER BY stock_code, fiscal_year, fiscal_quarter, fs_div
    """)
    fcf4q_map = {}
    for r in cur.fetchall():
        fcf4q_map[(r[0], r[1], r[2], r[3])] = r[4]

    # 3. 갱신 대상 행 조회
    if latest_only:
        cur.execute("""
            SELECT DISTINCT ON (stock_code, fs_div)
                   stock_code, fiscal_year, fiscal_quarter, fs_div,
                   free_cf, rolling4_free_cf, net_income
            FROM cash_conversion_signals
            ORDER BY stock_code, fs_div, fiscal_year DESC, fiscal_quarter DESC
        """)
    else:
        cur.execute("""
            SELECT stock_code, fiscal_year, fiscal_quarter, fs_div,
                   free_cf, rolling4_free_cf, net_income
            FROM cash_conversion_signals
        """)

    rows = cur.fetchall()
    total = len(rows)
    updated = 0
    batch = []

    for r in rows:
        code, yr, qtr, fs = r[0], r[1], r[2], r[3]
        free_cf   = r[4]       # 당기 FCF (원)
        r4_fcf    = r[5]       # TTM FCF (원)
        net_inc   = r[6]       # 당기 순이익 (원)

        mc_억, shares = universe.get(code, (None, None))
        mc_won = mc_억 * _억 if mc_억 else None

        # FCF yield (TTM FCF / 현재 시총)
        fcf_yield = _pct(r4_fcf, mc_won)

        # P/FCF
        pfcf = _ratio(mc_won, r4_fcf) if (r4_fcf and r4_fcf > 0) else None

        # FCF per share
        fcf_ps = round(r4_fcf / shares, 0) if (r4_fcf is not None and shares and shares > 0) else None

        # FCF to Net Income (당기 품질 지표)
        fcf_to_ni = _pct(free_cf, net_inc)

        # FCF YoY (4분기 전 동일 fs_div rolling4_free_cf와 비교)
        prev_yr = yr - 1
        prev_r4 = fcf4q_map.get((code, prev_yr, qtr, fs))
        fcf_yoy = _pct(r4_fcf, prev_r4, cap=_YOY_CAP) if prev_r4 else None

        batch.append((fcf_yield, pfcf, fcf_ps, fcf_to_ni, fcf_yoy, code, yr, qtr, fs))

        if len(batch) >= 2000:
            _flush(cur, batch)
            updated += len(batch)
            batch = []

    if batch:
        _flush(cur, batch)
        updated += len(batch)

    conn.commit()
    return updated, total


def _flush(cur, batch):
    cur.executemany("""
        UPDATE cash_conversion_signals
        SET fcf_yield_pct     = %s,
            pfcf_ratio        = %s,
            fcf_per_share_krw = %s,
            fcf_to_ni_pct     = %s,
            fcf_yoy_pct       = %s
        WHERE stock_code = %s
          AND fiscal_year = %s
          AND fiscal_quarter = %s
          AND fs_div = %s
    """, batch)


# ── 검증 ─────────────────────────────────────────────────────────────────────
def verify(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, fiscal_year, fiscal_quarter,
               fcf_yield_pct, pfcf_ratio, fcf_per_share_krw,
               fcf_to_ni_pct, fcf_yoy_pct
        FROM cash_conversion_signals
        WHERE stock_code = '005930' AND fs_div = 'CFS'
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        LIMIT 4
    """)
    print("\n=== 검증: 삼성전자 FCF 파생 지표 ===")
    print(f"{'기간':<10} {'FCF_yield%':>10} {'P/FCF':>8} {'FCF/주(원)':>12} {'FCF/NI%':>10} {'YoY%':>8}")
    for r in cur.fetchall():
        print(f"{r[1]}Q{r[2]:<6}  {r[3] or '-':>9}  {r[4] or '-':>7}  {(r[5] or 0):>11,.0f}  {r[6] or '-':>9}  {r[7] or '-':>7}")

    # 전체 커버리지
    cur.execute("""
        SELECT
          COUNT(*) total,
          COUNT(fcf_yield_pct) has_yield,
          COUNT(pfcf_ratio) has_pfcf,
          COUNT(fcf_per_share_krw) has_fps,
          COUNT(fcf_to_ni_pct) has_fni,
          COUNT(fcf_yoy_pct) has_yoy
        FROM cash_conversion_signals
    """)
    r = cur.fetchone()
    print(f"\n전체 {r[0]}행: yield={r[1]}, P/FCF={r[2]}, FPS={r[3]}, FCF/NI={r[4]}, YoY={r[5]}")


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="FCF 파생 지표 계산")
    ap.add_argument("--latest", action="store_true", help="종목별 최신 행만 갱신")
    ap.add_argument("--verify-only", action="store_true", help="계산 없이 검증만")
    args = ap.parse_args()

    conn = connect_primary_db(timeout=120)
    t0 = time.time()

    if not args.verify_only:
        print("컬럼 확인/추가 중...")
        ensure_columns(conn)

        mode = "최신 행만" if args.latest else "전체"
        print(f"FCF 파생 지표 계산 중 ({mode})...")
        updated, total = compute_all(conn, latest_only=args.latest)
        elapsed = time.time() - t0
        print(f"완료: {updated}/{total}행 갱신 ({elapsed:.1f}초)")

    verify(conn)
    conn.close()


if __name__ == "__main__":
    main()
