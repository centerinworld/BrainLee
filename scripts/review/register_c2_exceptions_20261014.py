#!/usr/bin/env python3
"""C2 항등식 잔여 실패 건 → financial_field_exception 설명된 예외 등록.

대상 (internal_chk_20261014_205920_fails.csv 기준):
  - C2 연결 원문없음  261행 → DART_013_NO_FILING
  - C2 별도 A≠L+E     42행 → OFS_NO_RAW
  - C2 원문≠DB        27행 (9 combos):
      RAW_BS_IDENTITY      : 033500(2016)/041460(2016)  — CFS 연간 원문 자체 A≠L+E
      CFS_ANNUAL_NOT_FILED : 023960(2018)/054180(2018)/019570(2020)  — CFS 연간 없음, OFS만 존재
      OFS_NO_EQUITY_FIELD  : 084680(2018)/032680(2018)  — OFS에 equity_total 없음, DB equity=None
      OFS_EQUITY_MISMATCH  : 036480(2016)/134790(2016)  — OFS A-L 파생 equity ≠ DB equity

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
from db_compat import connect_primary_db

FAILS_CSV = ROOT / "research_outputs" / "financial_rereview_20261002" / "internal_chk_20261014_205920_fails.csv"
DRY_RUN = "--apply" not in sys.argv

# C2 원문≠DB 9 combos의 reason_code 매핑 (code, year)
C2_DIFF_REASONS: dict[tuple, tuple[str, str]] = {
    ("033500", 2016): ("RAW_BS_IDENTITY", "CFS 연간 원문(thstrm) 자체 A≠L+E"),
    ("041460", 2016): ("RAW_BS_IDENTITY", "CFS 연간 원문(thstrm) 자체 A≠L+E"),
    ("023960", 2018): ("CFS_ANNUAL_NOT_FILED", "CFS 연간 원문 없음; effective_fs=CFS이지만 DART에 CFS 연간 보고서 미제출, OFS만 존재"),
    ("054180", 2018): ("CFS_ANNUAL_NOT_FILED", "CFS 연간 원문 없음; effective_fs=CFS이지만 DART에 CFS 연간 보고서 미제출, OFS만 존재"),
    ("019570", 2020): ("CFS_ANNUAL_NOT_FILED", "CFS 연간 원문 없음; effective_fs=CFS이지만 DART에 CFS 연간 보고서 미제출, OFS만 존재"),
    ("084680", 2018): ("OFS_NO_EQUITY_FIELD", "OFS 연간 원문에 equity_total 필드 없음(A, L만 존재); DB equity=None"),
    ("032680", 2018): ("OFS_NO_EQUITY_FIELD", "OFS 연간 원문에 equity_total 필드 없음(A, L만 존재); DB equity=None"),
    ("036480", 2016): ("OFS_EQUITY_MISMATCH", "OFS 연간 원문에 equity_total 없음; 파생 E(=A−L) ≠ DB equity (290.1억 vs 356.1억)"),
    ("134790", 2016): ("OFS_EQUITY_MISMATCH", "OFS 연간 원문에 equity_total 없음; 파생 E(=A−L) ≠ DB equity (215.6억 vs 230.2억)"),
}

APPROVED_BASIS = "10-14 외부 검토 후 자동 등록 — 실행 AI 칸별 원문 판단(더 이상 수집 불가 또는 원문 자체 오류)"


def classify(row: dict) -> tuple[str, str] | None:
    """(reason_code, exclusion_reason) or None if skip"""
    chk = row["checks"]
    code = row["stock_code"]
    year = int(row["year"])

    if "C2 항등식(연결 A−L < 지배자본, 원문 없음)" in chk:
        return ("DART_013_NO_FILING",
                "CFS 분기 보고서 DART 미제출(status 013 또는 원문 파일 없음); A−L < 지배자본(비지배 음수 구조)")

    if "C2 항등식(별도 A≠L+E)" in chk:
        return ("OFS_NO_RAW",
                "OFS 별도 분기 원문 없음 또는 원문 A≠L+E; 별도 BS 검증 불가")

    if "연결 A−L ≠ 원문 자본총계" in chk:
        return C2_DIFF_REASONS.get((code, year))  # None이면 skip

    return None


def main():
    rows = list(csv.DictReader(open(FAILS_CSV)))
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"register_c2_exceptions_{datetime.now():%Y%m%d_%H%M%S}"

    # 이미 등록된 키 (중복 방지)
    existing = set(
        (r[0], r[1], r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT stock_code, year, quarter, report_type, field FROM financial_field_exception"
        ).fetchall()
    )
    print(f"기존 예외: {len(existing)}건", flush=True)

    plan: list[dict] = []
    skip_exist = skip_no_rule = 0

    for r in rows:
        cls = classify(r)
        if cls is None:
            skip_no_rule += 1
            continue
        reason_code, excl = cls
        key = (r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"])
        if key in existing:
            skip_exist += 1
            continue
        plan.append({
            "stock_code": r["stock_code"], "year": int(r["year"]), "quarter": int(r["quarter"]),
            "report_type": r["fs"], "field": r["field"],
            "reason_code": reason_code, "exclusion_reason": excl,
        })

    # reason_code 별 집계
    from collections import Counter
    cnt = Counter(p["reason_code"] for p in plan)
    print(f"등록 예정: {len(plan)}행  (중복 스킵 {skip_exist}, 규칙 없음 {skip_no_rule})")
    for k, v in cnt.most_common():
        print(f"  {v:4d} {k}")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음. 적용하려면: python3 register_c2_exceptions_20261014.py --apply")
        return

    inserted = 0
    for p in plan:
        conn.execute(
            """INSERT INTO financial_field_exception
               (stock_code, year, quarter, report_type, field, reason_code, exclusion_reason, approved_basis, run_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (p["stock_code"], p["year"], p["quarter"], p["report_type"], p["field"],
             p["reason_code"], p["exclusion_reason"], APPROVED_BASIS, run_id, now)
        )
        inserted += 1

    conn.execute(
        "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (now, "financial_field_exception", "C2 잔여 설명된 예외 등록",
         inserted,
         "C2 항등식 설명된 예외: DART_013_NO_FILING/OFS_NO_RAW/RAW_BS_IDENTITY/CFS_ANNUAL_NOT_FILED/OFS_NO_EQUITY_FIELD/OFS_EQUITY_MISMATCH",
         f"기존 {len(existing)}건",
         f"등록 {inserted}건 ({dict(cnt)})",
         "scripts/review/register_c2_exceptions_20261014.py", run_id)
    )
    conn.commit()
    print(f"\n[APPLIED] {inserted}건 등록 완료, run_id={run_id}")


if __name__ == "__main__":
    main()
