#!/usr/bin/env python3
"""C2 항등식 수정: 연결 A−L ≠ 원문 자본총계 (Q4, OFS-only 회사)
원인: effective_fs('CFS') → 'OFS'이지만 fn.raw(code, y, 4, 'OFS', ...) 이 _4_OFS.json.gz(없음)을 읽으려 해서 C4 정렬 대상에서 누락됨.
수정: 원문 OFS 연간(q=0) 값으로 DB Q4 BS 3개 필드 갱신.
2026-10-14
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

import fin_norm as fn
from db_compat import connect_primary_db

FAILS = ROOT / "research_outputs" / "financial_rereview_20261002" / "internal_chk_20261014_202006_fails.csv"
DRY_RUN = "--apply" not in sys.argv
T = 2e6  # 200만원 허용 오차


def main():
    rows = [r for r in csv.DictReader(open(FAILS))
            if r["checks"] == "C2 항등식(연결 A−L ≠ 원문 자본총계)"
            and 2016 <= int(r["year"]) <= 2020
            and int(r["quarter"]) == 4]

    # unique (code, year) combos — each appears 3 times (assets/liabilities/equity)
    combos = sorted(set((r["stock_code"], int(r["year"])) for r in rows))
    print(f"대상 (code, year) 조합: {len(combos)}개  (총 {len(rows)}행)")

    conn = connect_primary_db(timeout=600)

    updates = []
    skip_no_raw = []
    skip_no_change = []

    for code, y in combos:
        fs = "CFS"
        rfs = fn.effective_fs(code, y, fs)  # 'OFS' for OFS-only companies, 'CFS' for companies with CFS files

        # Read raw annual (q=0) using effective_fs
        by, bq = fn.dart_key_for_fiscal(code, y, 0)
        e = fn._extract(code, by, bq, rfs, "thstrm_amount")
        if not e.get("total_assets"):
            # try next year frmtrm
            e2 = fn._extract(code, by + 1, 0, rfs, "frmtrm_amount")
            e = e2 if e2.get("total_assets") else e

        raw_A = e.get("total_assets")
        raw_L = e.get("total_liabilities")
        # OFS 파일에는 equity_total, CFS 파일에는 equity_parent → rfs 기준으로 매핑
        raw_E = e.get(fn.dart_key("total_equity", rfs))  # 'equity_parent' for rfs=CFS, 'equity_total' for rfs=OFS

        if not all(x is not None for x in (raw_A, raw_L, raw_E)):
            skip_no_raw.append((code, y))
            continue

        # OFS BS identity check: 원문 자체가 A=L+E인지
        raw_diff = abs(raw_A - raw_L - raw_E)
        if raw_diff > max(T, abs(raw_A) * 1e-4):
            print(f"  SKIP {code} {y}: 원문 자체 BS 불일치 {raw_diff:.0f}원")
            skip_no_raw.append((code, y))
            continue

        # Current DB values
        db_row = conn.execute(
            "SELECT total_assets, total_liabilities, total_equity FROM financial_data "
            "WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type=?",
            (code, y, fs)
        ).fetchone()
        if not db_row:
            skip_no_raw.append((code, y))
            continue
        db_A, db_L, db_E = db_row

        changed = {}
        for field, raw_val, db_val in (("total_assets", raw_A, db_A), ("total_liabilities", raw_L, db_L), ("total_equity", raw_E, db_E)):
            if db_val is None or abs(db_val - raw_val) > T:
                changed[field] = (db_val, raw_val)

        if not changed:
            skip_no_change.append((code, y))
            continue

        updates.append((code, y, changed, raw_A, raw_L, raw_E))

    print(f"\n수정 예정: {len(updates)}개, 원문 없음/이미 일치: {len(skip_no_raw)+len(skip_no_change)}개")
    print(f"  원문 없음: {len(skip_no_raw)}, 변경 없음: {len(skip_no_change)}")

    if DRY_RUN:
        print("\n[DRY-RUN] 첫 10개 수정 예정:")
        for code, y, changed, rA, rL, rE in updates[:10]:
            print(f"  {code} {y}Q4 CFS: ", end="")
            for f, (old, new) in changed.items():
                print(f"{f} {(old or 0)//1e8:.1f}→{new//1e8:.1f}억", end="  ")
            print()
        print("\n--apply 없이 실행 중(dry-run). 적용하려면: python3 fix_c2_ofs_q4_20261014.py --apply")
        return

    # Apply
    ts = datetime.now().isoformat(timespec="seconds")
    applied = 0
    for code, y, changed, rA, rL, rE in updates:
        # readback before
        before = conn.execute(
            "SELECT total_assets, total_liabilities, total_equity FROM financial_data "
            "WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type=?",
            (code, y, "CFS")
        ).fetchone()

        set_clause = ", ".join(f"{f}=?" for f in changed)
        vals = list(v[1] for v in changed.values()) + [code, y]
        conn.execute(
            f"UPDATE financial_data SET {set_clause} "
            "WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type='CFS'",
            vals
        )

        # readback after
        after = conn.execute(
            "SELECT total_assets, total_liabilities, total_equity FROM financial_data "
            "WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type=?",
            (code, y, "CFS")
        ).fetchone()

        # Log
        conn.execute(
            "INSERT INTO data_fix_log (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, fixed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("financial_data", f"{code}/{y}/Q4/{fs}",
             len(changed),
             "fix_c2_ofs_q4_20261014: Q4 BS from raw annual",
             json.dumps(dict(zip(("total_assets","total_liabilities","total_equity"), list(before or [])))),
             json.dumps(dict(zip(("total_assets","total_liabilities","total_equity"), list(after or [])))),
             "fix_c2_ofs_q4_20261014", ts)
        )
        applied += 1

    conn.execute("COMMIT")
    print(f"\n[APPLIED] {applied}건 수정 완료")

    # readback pass: verify all 285 rows now have A-L ≈ raw equity_total
    fail_after = 0
    for code, y, changed, rA, rL, rE in updates:
        row = conn.execute(
            "SELECT total_assets, total_liabilities, total_equity FROM financial_data "
            "WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type='CFS'",
            (code, y)
        ).fetchone()
        if not row:
            continue
        A, L, E = row
        if abs((A - L) - rE) > max(T, abs(A) * 1e-4):
            fail_after += 1
            print(f"  READBACK FAIL {code} {y}: A-L={(A-L)//1e8:.1f}억 raw_E={rE//1e8:.1f}억")
    print(f"readback pass: {fail_after}건 실패" if fail_after else "readback pass: 모두 통과")


if __name__ == "__main__":
    main()
