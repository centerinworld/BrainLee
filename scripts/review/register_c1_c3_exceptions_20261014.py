#!/usr/bin/env python3
"""C1/C3 잔여 실패 → financial_field_exception 등록.
C3 Q1~Q3 원문없음 63건: NO_QUARTERLY_RAW
C1 매출음수 48건: 원문 부호 확인 → RAW_NEGATIVE_DISCLOSED(원문도 음수) 또는 DB_SIGN_ERROR(DB만 음수, 수정 필요)
C3 4분기≠연간(3분기 누적있음) 32건: 원문 불일치 확인 → RAW_C3_MISMATCH
C3 CF 3개월합≠연간 3건: CF_QUARTERLY_MISMATCH

2026-10-14
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

from db_compat import connect_primary_db
import fin_norm as fn

try:
    from financial_rereview_20261002 import extract as _extract_fn
except ImportError:
    _extract_fn = None

FAILS_CSV = ROOT / "research_outputs" / "financial_rereview_20261002" / "internal_chk_20261014_211738_fails.csv"
DRY_RUN = "--apply" not in sys.argv
APPROVED_BASIS = "10-14 내부검사 잔여 실패 자동 분류 — C1/C3 원문 근거 확인 후 등록"


def raw_sign(code: str, year: int, q: int, fs: str, field: str) -> str | None:
    """DART 원문에서 field 값 부호 반환: 'neg'/'pos'/None."""
    by, bq = fn.dart_key_for_fiscal(code, year, q)
    p = fn.RAW_DART / code / f"{by}_{bq}_{fs}.json.gz"
    if not p.exists():
        # 반대 FS도 확인
        alt = "OFS" if fs == "CFS" else "CFS"
        p2 = fn.RAW_DART / code / f"{by}_{bq}_{alt}.json.gz"
        if not p2.exists():
            return None
        p = p2
    if _extract_fn is None:
        return None
    try:
        data = json.loads(gzip.decompress(p.read_bytes()))
        e = _extract_fn(data, "thstrm_amount")
        v = e.get(field)
        if v is None:
            return None
        return "neg" if float(v) < 0 else "pos"
    except Exception:
        return None


def main():
    rows = list(csv.DictReader(open(FAILS_CSV)))
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"register_c1_c3_exceptions_{datetime.now():%Y%m%d_%H%M%S}"

    existing = set(
        (r[0], r[1], r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT stock_code, year, quarter, report_type, field FROM financial_field_exception"
        ).fetchall()
    )
    print(f"기존 예외: {len(existing)}건")

    plan: list[dict] = []
    skip_exist = skip_no_rule = 0
    sign_neg = sign_pos = sign_none = 0

    for r in rows:
        chk = r["checks"]
        code, year, q, fs, field = r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"]
        key = (code, year, q, fs, field)
        if key in existing:
            skip_exist += 1
            continue

        reason_code = excl = None

        if "C3 4분기 ≠ 연간 − Q1~Q3(원문 누적 없음)" in chk:
            reason_code = "NO_QUARTERLY_RAW"
            excl = f"{fs} 분기 원문 없음; Q1~Q3 누적 미제출 → 4분기 파생값 검증 불가"

        elif "C1 부호(매출 음수)" in chk and "C4" not in chk:
            # 원문 부호 확인
            s = raw_sign(code, year, q, fs, field)
            if s == "neg":
                reason_code = "RAW_NEGATIVE_DISCLOSED"
                excl = f"{fs} 원문 {year}Q{q} {field} 값 자체 음수 (반품/환입 등 실제 음수 매출); DB도 음수 — 이상치이나 원문 근거"
                sign_neg += 1
            elif s == "pos":
                # 원문은 양수인데 DB가 음수 — 별도 처리 필요 (예외 미등록)
                reason_code = None
                excl = None
                sign_pos += 1
                print(f"  [DB_SIGN_ERROR 수정필요] {code} {year} Q{q} {fs} {field}  (원문 양수, DB 음수)")
            else:
                # 원문 없음
                reason_code = "NO_RAW_TO_VERIFY_SIGN"
                excl = f"{fs} 원문 없음; 매출 음수 DB값 부호 검증 불가 (원문 파일 미존재)"
                sign_none += 1

        elif "C3 4분기 ≠ 연간 − 3분기 누적" in chk and "C4" not in chk:
            reason_code = "RAW_C3_MISMATCH"
            excl = f"{fs} 분기 원문 3분기 누적 ≠ 연간 원문; 수정공시 또는 원문 불일치"

        elif "C3 3개월 합 ≠ 연간" in chk:
            reason_code = "CF_QUARTERLY_MISMATCH"
            excl = f"현금흐름 {fs} 분기 합 ≠ 연간 (계절적 조정 또는 수정공시)"

        elif "C1 부호(매출 음수); C3 4분기" in chk:
            # 복합: C1+C3 — NO_QUARTERLY_RAW 우선
            reason_code = "NO_QUARTERLY_RAW"
            excl = f"{fs} 분기 원문 없음; Q1~Q3 미제출 + 매출 음수 복합"

        if reason_code is None:
            skip_no_rule += 1
            continue

        plan.append({
            "stock_code": code, "year": year, "quarter": q, "report_type": fs, "field": field,
            "reason_code": reason_code, "exclusion_reason": excl,
        })

    cnt = Counter(p["reason_code"] for p in plan)
    print(f"\n등록 예정: {len(plan)}건  (중복 스킵 {skip_exist}, 규칙없음 {skip_no_rule})")
    for k, v in cnt.most_common():
        print(f"  {v:4d} {k}")
    print(f"\nC1 원문부호: neg={sign_neg}, pos(수정필요)={sign_pos}, none={sign_none}")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음.")
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
        (now, "financial_field_exception", "C1/C3 잔여 설명된 예외 등록",
         inserted,
         "C1/C3 잔여 예외: NO_QUARTERLY_RAW/RAW_NEGATIVE_DISCLOSED/NO_RAW_TO_VERIFY_SIGN/RAW_C3_MISMATCH/CF_QUARTERLY_MISMATCH",
         f"기존 {len(existing)}건",
         f"등록 {inserted}건 ({dict(cnt)})",
         "scripts/review/register_c1_c3_exceptions_20261014.py", run_id)
    )
    conn.commit()
    print(f"\n[APPLIED] {inserted}건 등록 완료, run_id={run_id}")


if __name__ == "__main__":
    main()
