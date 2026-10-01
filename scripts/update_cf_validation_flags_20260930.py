"""
update_cf_validation_flags_20260930.py
cf_validation_flags 재갱신 (2026-09-30)

처리 3단계:
  1. OPEN 2건 (001720 2026) — 삭제된 cash_flow_data 행의 dangling 참조 → STRUCTURAL
  2. AMBIGUOUS 32건 — 현재 cash_flow_data 값과 재대조, ±3% 이내면 CONFIRMED으로 승격
  3. Sept 26 annual CF 변경분 — operating_cf/investing_cf/financing_cf 약 7,636건
     현재 DB값 vs 저장된 dart_value 재비교 → status 갱신

외부 API 호출 없음 (DB-only 비교).
run_id: cf_flags_refresh_20260930
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_compat import connect_primary_db

RUN_ID = "cf_flags_refresh_20260930"
TOL_CONFIRMED   = 0.03   # ±3%
TOL_CLOSE_MATCH = 0.15   # 3~15%
ABS_TOL         = 5_000_000  # 500만원 (백만원 단위 기준 5,000)

def _ratio(a, b):
    """절대·상대 비율 둘 다 체크 — None이면 None 반환"""
    if a is None or b is None:
        return None
    if a == b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom

def _status_from_ratio(ratio, abs_diff):
    if ratio is None:
        return None
    if ratio <= TOL_CONFIRMED or abs_diff <= ABS_TOL:
        return "CONFIRMED"
    if ratio <= TOL_CLOSE_MATCH:
        return "CLOSE_MATCH"
    return "AMBIGUOUS"

def step1_fix_open(conn):
    """OPEN 2건: 001720 2026 — cash_flow_data 행 삭제됨, STRUCTURAL로 처리"""
    rows = conn.execute(
        "SELECT id, stock_code, year, field FROM cf_validation_flags WHERE status='OPEN'"
    ).fetchall()
    if not rows:
        print("[Step1] OPEN 항목 없음, 스킵")
        return 0

    fixed = 0
    for row_id, code, year, field in rows:
        # cash_flow_data에 해당 행이 실제로 있는지 확인
        cf_row = conn.execute(
            "SELECT id FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
            (code, year)
        ).fetchone()
        if cf_row is None:
            # 데이터 삭제됨 → STRUCTURAL (데이터 없음)
            conn.execute(
                """UPDATE cf_validation_flags
                   SET status='STRUCTURAL', resolved_at=?
                   WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), row_id)
            )
            print(f"  [Step1] {code} {year} {field} → STRUCTURAL (cash_flow_data 행 없음)")
            fixed += 1
        else:
            print(f"  [Step1] {code} {year} {field} — cash_flow_data 존재, OPEN 유지")
    conn.commit()
    return fixed

def step2_resolve_ambiguous(conn):
    """AMBIGUOUS 32건: 현재 cash_flow_data 값과 dart_value/fnguide_value 재대조"""
    rows = conn.execute(
        """SELECT id, stock_code, year, field, dart_value, fnguide_value, seibro_value
           FROM cf_validation_flags WHERE status='AMBIGUOUS'"""
    ).fetchall()
    print(f"\n[Step2] AMBIGUOUS {len(rows)}건 처리")

    CF_FIELD_MAP = {
        "operating_cf":   "operating_cf",
        "investing_cf":   "investing_cf",
        "financing_cf":   "financing_cf",
        "cash_end":       "cash_end",
        "capex":          "capex",
        "depreciation":   "depreciation",
    }
    FIN_FIELD_MAP = {
        "net_income":       "net_income",
        "operating_profit": "operating_profit",
        "revenue":          "revenue",
        "total_equity":     "total_equity",
        "total_assets":     "total_assets",
    }

    confirmed = close = still_ambiguous = 0
    for row_id, code, year, field, dart_val, fg_val, seibro_val in rows:
        db_val = None

        if field in CF_FIELD_MAP:
            cf_row = conn.execute(
                f"SELECT {CF_FIELD_MAP[field]} FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
                (code, year)
            ).fetchone()
            db_val = cf_row[0] if cf_row else None
        elif field in FIN_FIELD_MAP:
            fin_row = conn.execute(
                f"SELECT {FIN_FIELD_MAP[field]} FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
                (code, year)
            ).fetchone()
            db_val = fin_row[0] if fin_row else None

        if db_val is None:
            print(f"  {code} {year} {field}: DB값 없음 → STRUCTURAL")
            conn.execute(
                "UPDATE cf_validation_flags SET status='STRUCTURAL', resolved_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), row_id)
            )
            still_ambiguous += 1
            continue

        # dart_value와 비교
        best_ratio = None
        best_status = None
        for ref_val in [dart_val, fg_val, seibro_val]:
            r = _ratio(db_val, ref_val)
            if r is None:
                continue
            abs_diff = abs(db_val - ref_val) if ref_val is not None else None
            s = _status_from_ratio(r, abs_diff or 9e99)
            if best_ratio is None or r < best_ratio:
                best_ratio = r
                best_status = s

        if best_status in ("CONFIRMED", "CLOSE_MATCH"):
            conn.execute(
                "UPDATE cf_validation_flags SET status=?, resolved_value=?, resolved_at=? WHERE id=?",
                (best_status, db_val, datetime.now(timezone.utc).isoformat(), row_id)
            )
            print(f"  {code} {year} {field}: db={db_val:.0f} ratio={best_ratio:.3f} → {best_status}")
            if best_status == "CONFIRMED":
                confirmed += 1
            else:
                close += 1
        else:
            # ratio가 15% 초과 — 단위 불일치 의심
            # dart_value가 1000배 차이나면 단위 문제로 STRUCTURAL 처리
            if best_ratio is not None and best_ratio > 100:
                conn.execute(
                    "UPDATE cf_validation_flags SET status='STRUCTURAL', resolved_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), row_id)
                )
                print(f"  {code} {year} {field}: ratio={best_ratio:.0f} (단위 불일치) → STRUCTURAL")
            else:
                print(f"  {code} {year} {field}: db={db_val} dart={dart_val} fg={fg_val} ratio={best_ratio} → AMBIGUOUS 유지")
            still_ambiguous += 1

    conn.commit()
    print(f"  결과: CONFIRMED={confirmed}, CLOSE_MATCH={close}, STRUCTURAL/유지={still_ambiguous}")
    return confirmed, close, still_ambiguous

def step3_refresh_sept26_changes(conn):
    """
    Sept 26 cf_quarterly_dart 변경으로 업데이트된 annual CF 행의
    cf_validation_flags status를 현재 DB값 기준으로 재계산
    """
    ANNUAL_CF_FIELDS = ["operating_cf", "investing_cf", "financing_cf"]
    RUN_ID_26 = "cf_quarterly_dart_20260926_151905"

    # 영향받은 stock_code + year 목록
    affected = conn.execute(
        """SELECT DISTINCT
               REPLACE(REPLACE(field_name,'cash_flow_data.',''),'_q','') as field_base,
               stock_code, year
           FROM financial_fix_log
           WHERE run_id=? AND field_name NOT LIKE '%_q'
             AND field_name IN (
               'cash_flow_data.operating_cf',
               'cash_flow_data.investing_cf',
               'cash_flow_data.financing_cf'
             )""",
        (RUN_ID_26,)
    ).fetchall()

    print(f"\n[Step3] Sept 26 annual CF 변경분 {len(affected)}건 재비교")

    refreshed = confirmed = close_match = opened = 0
    for field, code, year in affected:
        # 현재 DB값
        cf_row = conn.execute(
            f"SELECT {field} FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
            (code, year)
        ).fetchone()
        if cf_row is None or cf_row[0] is None:
            continue
        db_val = float(cf_row[0])

        # cf_validation_flags 기존 항목
        flag = conn.execute(
            "SELECT id, dart_value, fnguide_value, seibro_value, status FROM cf_validation_flags WHERE stock_code=? AND year=? AND field=?",
            (code, year, field)
        ).fetchone()
        if flag is None:
            # 해당 플래그 없음 — 신규 항목이므로 스킵 (신규 등록은 cf_triple_validator가 담당)
            continue

        flag_id, dart_val, fg_val, seibro_val, old_status = flag

        # dart_value와 비교
        best_ratio = None
        best_status = None
        for ref_val in [dart_val, fg_val, seibro_val]:
            if ref_val is None:
                continue
            r = _ratio(db_val, float(ref_val))
            abs_diff = abs(db_val - float(ref_val))
            s = _status_from_ratio(r, abs_diff)
            if best_ratio is None or r < best_ratio:
                best_ratio = r
                best_status = s

        if best_status is None:
            continue  # 모든 ref_val이 None

        if best_status == old_status:
            continue  # 변경 없음

        conn.execute(
            "UPDATE cf_validation_flags SET status=?, resolved_value=?, resolved_at=? WHERE id=?",
            (best_status, db_val, datetime.now(timezone.utc).isoformat(), flag_id)
        )
        refreshed += 1
        if best_status == "CONFIRMED":
            confirmed += 1
        elif best_status == "CLOSE_MATCH":
            close_match += 1
        else:
            opened += 1

    conn.commit()
    print(f"  상태 변경: {refreshed}건 (→CONFIRMED:{confirmed}, →CLOSE_MATCH:{close_match}, →기타:{opened})")
    return refreshed

def main():
    conn = connect_primary_db(timeout=120)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== cf_validation_flags 재갱신 시작 ({now}) ===\n")

    # 시작 전 현황
    before = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM cf_validation_flags GROUP BY status"
    ).fetchall()}
    print("시작 전:", before)

    step1_fixed = step1_fix_open(conn)
    step2_conf, step2_close, step2_remain = step2_resolve_ambiguous(conn)
    step3_refreshed = step3_refresh_sept26_changes(conn)

    # 완료 후 현황
    after = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM cf_validation_flags GROUP BY status"
    ).fetchall()}
    print(f"\n=== 완료 ===")
    print("완료 후:", after)
    print(f"\n처리 요약:")
    print(f"  Step1 OPEN→STRUCTURAL: {step1_fixed}건")
    print(f"  Step2 AMBIGUOUS 해소: CONFIRMED={step2_conf}, CLOSE_MATCH={step2_close}, STRUCTURAL/유지={step2_remain}")
    print(f"  Step3 Sept26 변경분 status 갱신: {step3_refreshed}건")

    conn.close()

if __name__ == "__main__":
    main()
