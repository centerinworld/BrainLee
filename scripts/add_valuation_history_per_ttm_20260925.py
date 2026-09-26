#!/usr/bin/env python3
"""HANDOFF §10 P0-2: add comparable TTM PER columns to valuation_history WITHOUT touching the legacy `per`/`eps`.

Legacy `per` is price/quarterly-EPS for Q1-Q3 rows but price/annual-EPS for Q4 rows (not comparable across quarters). New columns:
  ttm_net_income  DOUBLE PRECISION   sum of net_income of the 4 consecutive fiscal quarters ENDING at the row's quarter (원; CFS preferred)
  per_ttm         DOUBLE PRECISION   market_cap_억*1e8 / ttm_net_income, only if ttm_net_income > 0 and market cap known
  ttm_basis       TEXT               'CFS' or 'OFS' - the statement basis of ALL FOUR quarters (V2 2026-09-26: the first version picked "CFS first" per quarter and
                                     mixed consolidated and separate quarters inside one TTM for ~15% of stocks, dividing a consolidated market cap by a partly
                                     separate net income). Rule: 4 quarters all CFS -> CFS; else 4 quarters all OFS -> OFS; else NULL (no TTM).
Note: this is per FISCAL-PERIOD TTM at the period-end price (like the legacy column), not point-in-time (a quarter's figures are filed
up to 45/90 days after period end). For point-in-time use strategy_feature_snapshot.per (built with --ttm-valuation).
Fills 2026 Q2 too (legacy per is NULL there).
Dry-run by default; --apply performs ALTER + UPDATE in one transaction."""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def main(apply: bool) -> int:
    conn = connect_primary_db(timeout=900, readonly=not apply)
    fd = defaultdict(lambda: {"CFS": {}, "OFS": {}})   # code -> basis -> qidx -> net_income
    for code, y, q, rt, ni in conn.execute(
            "SELECT stock_code,year,quarter,report_type,net_income FROM financial_data "
            "WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL").fetchall():
        fd[code]["CFS" if rt == "CFS" else "OFS"][int(y) * 4 + int(q)] = float(ni)
    rows = conn.execute("SELECT id,stock_code,year,quarter,market_cap_억 FROM valuation_history").fetchall()
    updates, n_pos, n_neg, n_no4, n_ofs = [], 0, 0, 0, 0
    for vid, code, y, q, mc in rows:
        k = int(y) * 4 + int(q)
        chosen = None
        for basis in ("CFS", "OFS"):                    # one basis for the WHOLE 4-quarter window
            qs = fd[code][basis]
            if all((k - i) in qs for i in range(4)):
                chosen = (basis, sum(qs[k - i] for i in range(4)))
                break
        if chosen is None:
            n_no4 += 1
            updates.append((None, None, None, vid))
            continue
        basis, ttm = chosen
        per = float(mc) * 1e8 / ttm if (ttm > 0 and mc) else None
        n_pos += per is not None
        n_neg += ttm <= 0
        n_ofs += basis == "OFS"
        updates.append((ttm, per, basis, vid))
    print(f"rows={len(rows)} per_ttm_filled={n_pos} ttm_ni<=0={n_neg} no_single_basis_4q_window={n_no4} ofs_basis_windows={n_ofs}")
    if not apply:
        print("dry-run: 변경 없음 (--apply 로 실행)")
        return 0
    try:
        conn.execute("ALTER TABLE valuation_history ADD COLUMN IF NOT EXISTS ttm_net_income DOUBLE PRECISION, "
                     "ADD COLUMN IF NOT EXISTS per_ttm DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS ttm_basis TEXT DEFAULT NULL")
        conn.executemany("UPDATE valuation_history SET ttm_net_income=?, per_ttm=?, ttm_basis=? WHERE id=?", updates)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"롤백: {exc}")
        return 3
    print("적용 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
