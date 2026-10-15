#!/usr/bin/env python3
"""연간 감가상각 = 현금흐름표 조정 '감가상각비' 행 정렬 (2026-10-13, C14 정의 정리). 기본 dry-run(CSV만).

정의(FINANCIAL §2, 사용자 승인 10-03): `depreciation` = 현금흐름표 조정 '감가상각비'. 구성요소(유형·사용권·무형)는 따로 보존한다.
**10-13 정의 구체화(근거: FnGuide 2023~24 연간 490칸 대조)**: 현금흐름표 조정 항목 중 이름에 '감가상각'이 들어가는 행들의 **합**(무형자산상각·대손상각·상각후원가 제외)이 FnGuide와 78% 일치,
'감가상각비' 한 행은 후보 있는 칸 기준 57.7%(260칸, 490칸 공통 분모로는 30.6%), XBRL 태그 행(추출기 기존 값)은 59.0%(512칸). 재현: verify_depreciation_definition_20261013.py. 한 보고서 안에 총액 행과 세부 행이 함께 있어 이중으로 더해지는 경우는 관측되지 않았다(단일 행 일치 150칸은 전부 합과도 일치).
측정(10-13 내부 검증 '감가상각' 영역): 2016~2022 연간 원문 근거 칸의 일치율 25~84% — 불일치 원인 분해(2021~22 표본): DB가 사용권자산 상각 행(006200: 36.7억 vs 감가상각비 71.8억),
  조정감가+무형상각(D&A) 변형, 단위 오류(19.39 vs 19.4억), FnGuide·legacy 수집값 등. 전부 정의와 다른 값이다.
규칙(FnGuide 표시 기간 이전 2016~2022 = OpenDART 단일 경로): 원문 후보(다음 해 사업보고서 전기 칸 = 재작성값 우선, 없으면 당기 칸)가 있고 DB와 2e6 넘게 다르면 원문값으로 맞춘다. 원문이 없으면 건드리지 않는다.
출력: reviewed_cells_depreciation_align_20261013.csv (apply_reviewed_cells_20261012.py 입력 — 연간 중복 행 모두 같은 값으로).
"""
from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
T = 2e6


def _cf_rows(code, y, fs):
    import gzip
    import json
    p = fn.RAW_DART / code / f"{y}_0_{fs}.json.gz"
    return [r for r in json.loads(gzip.decompress(p.read_bytes())) if r.get("sj_div") == "CF"] if p.exists() else None


def dep_sum(rows, col):
    """현금흐름표 조정 '감가상각' 행들의 합(무형·대손·상각후원가 제외). 행이 없으면 None."""
    import re
    vals = []
    for r in rows or []:
        nm = r.get("account_nm") or ""
        if r.get("sj_div", "CF") == "CF" and "감가상각" in nm and not re.search(r"무형|대손|상각후원가", nm):
            try:
                vals.append(abs(float(str(r.get(col) or "").replace(",", ""))))
            except ValueError:
                pass
    return sum(vals) if vals else None


def raw_dep(code, y, fs):
    """다음 해 사업보고서 전기 칸(재작성값) 우선, 없으면 당기 칸."""
    nxt = dep_sum(_cf_rows(code, y + 1, fs), "frmtrm_amount")
    return nxt if nxt is not None else dep_sum(_cf_rows(code, y, fs), "thstrm_amount")


def classify(v, raw, comp):
    ppe, rou, am = comp
    if v is not None and raw and (abs(v * 1e3 - raw) <= T or abs(v * 1e6 - raw) <= T or abs(v * 1e8 - raw) <= T):
        return "단위 오류"
    if am and abs(raw + am - v) <= T:
        return "조정감가+무형상각(D&A) 변형"
    if rou and abs(rou - v) <= T:
        return "사용권자산 상각 행"
    if ppe and abs(ppe - v) <= T:
        return "주석 유형자산"
    return "기타(legacy·FnGuide 수집값 등)"


def main():
    conn = connect_primary_db(timeout=300, readonly=True)
    cells = conn.execute("SELECT DISTINCT stock_code, year, report_type FROM cash_flow_data WHERE is_annual AND depreciation IS NOT NULL AND year BETWEEN 2016 AND 2022").fetchall()
    fails = [{"stock_code": r[0], "year": r[1], "fs": r[2]} for r in cells]
    out, st = [], collections.Counter()
    for r in fails:
        code, y, fs = r["stock_code"], int(r["year"]), r["fs"]
        rfs = fn.effective_fs(code, y, fs)
        raw = raw_dep(code, y, rfs)
        db = conn.execute("SELECT depreciation FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual AND depreciation IS NOT NULL ORDER BY quarter LIMIT 1", (code, y, fs)).fetchone()
        if raw is None or raw == 0 or not db:
            st["건너뜀: 원문 없음·0 또는 DB 없음"] += 1; continue
        v = float(db[0])
        if abs(v - raw) <= T:
            st["이미 일치(재작성 기준 차이)"] += 1; continue
        cm = conn.execute("SELECT dep_ppe, dep_rou, amort_intangible FROM financial_dep_capex_components WHERE stock_code=? AND year=? AND quarter=0 AND report_type=?", (code, y, fs)).fetchone()
        cls = classify(v, raw, [None if x is None else float(x) for x in (cm or (None, None, None))])
        st[cls] += 1
        out.append(["cash_flow_data", code, y, 0, fs, "depreciation", f"{raw:.0f}",
                    "연간 감가상각 = 현금흐름표 조정 감가상각 행 합계(FINANCIAL §2 정의 구체화 10-13, OpenDART 단일 경로 2016~2022)", "", f"DB {v:,.0f} → 원문 {raw:,.0f} [{cls}]"])
    with open(OUT / "reviewed_cells_depreciation_align_20261013.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow("table stock_code year quarter report_type field new_value rule reason_code evidence".split()); w.writerows(out)
    print(f"연간 감가상각 {len(fails)}칸 → 정렬 대상 {len(out)}")
    for k, n in sorted(st.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")


def sync_components(csv_path=OUT / "reviewed_cells_depreciation_align_20261013.csv"):
    """정렬한 칸의 구성요소 표 dep_cf_total(= 정의 칸 '현금흐름표 조정 감가상각')을 같은 값으로 맞춘다. 백업 후 UPDATE."""
    from datetime import datetime
    conn = connect_primary_db(timeout=300)
    run_id = f"dep_comp_align_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("CREATE TABLE IF NOT EXISTS financial_dep_capex_components_backup_dep_align_20261013 AS SELECT *, CAST(NULL AS TEXT) bk_run_id FROM financial_dep_capex_components WHERE false")
    n = 0
    for r in csv.DictReader(open(csv_path)):
        key = (r["stock_code"], int(r["year"]), r["report_type"])
        conn.execute("INSERT INTO financial_dep_capex_components_backup_dep_align_20261013 SELECT *, ? FROM financial_dep_capex_components WHERE stock_code=? AND year=? AND quarter=0 AND report_type=?", (run_id, *key))
        cur = conn.execute("UPDATE financial_dep_capex_components SET dep_cf_total=?, dep_source=?, run_id=?, updated_at=? WHERE stock_code=? AND year=? AND quarter=0 AND report_type=?",
                           (float(r["new_value"]), "dart_cf_statement(원문 감가상각비 행, 연간 정렬 10-13)", run_id, now, *key))
        n += cur.rowcount
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "financial_dep_capex_components", "연간 감가상각 정렬에 맞춰 dep_cf_total 갱신", n, "C14 정의 정리 — dep_cf_total = 현금흐름표 조정 감가상각 행", "정의 미확인 값", "원문 감가상각비 행",
                  "scripts/review/align_depreciation_annual_20261013.py", run_id))
    conn.commit()
    print(f"구성요소 갱신 {n}행 ({run_id})")


if __name__ == "__main__":
    if "--sync-components" in sys.argv:
        sync_components()
    else:
        main()
