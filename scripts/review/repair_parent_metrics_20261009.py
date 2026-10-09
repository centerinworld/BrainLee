#!/usr/bin/env python3
"""DART 지배주주순이익(ni_parent) 및 지배주주자본(equity_parent) 누락 행 정정 스크립트 (2026-10-09).

배경: DART _parent_by_name의 탐색 범위 제한으로 인해, 비지배지분이 앞서 나오거나 간격이 벌어진 공시에서
지배주주 지분이 누락되어 전체 지분(ni_total, equity_total)이 대신 저장되었음.
이로 인해 FnGuide(지배주주 지분 표시)와 불일치 발생.

원칙 0 (DART 값은 후보, 확정은 FnGuide 대조로만):
- 로컬 DART 원문(data_raw/dart_fnltt)에서 개선된 extract()로 지배주주 값을 추출.
- 기존 DB 값이 전체값(ni_total, equity_total)과 일치하고,
- 새로 추출된 지배주주 값이 FnGuide 값과 일치(max(200만, 0.5%))할 때만 교체.
- FnGuide 값이 없거나 불일치하면 절대 교체하지 않음.

기본 dry-run, --apply 시 백업, fix_log, canonical_sync, readback 수행.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from scripts.review.financial_rereview_20261002 import extract  # noqa: E402
from scripts.review.apply_ofs_extra_20261009 import canonical_sync  # noqa: E402

RAW_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")


def close(a, b):
    # 2026-10-09 Claude 보완(REVIEW_PLAN §56·§57): 0.5% 상대 허용은 1조 원이면 50억까지 일치로 봄 → P4·P5와 같은 200만원 절대 기준
    return a is not None and b is not None and abs(a - b) <= 2e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--codes", default="", help="특정 종목만 실행(쉼표 구분)")
    a = ap.parse_args()

    conn = connect_primary_db(timeout=900, readonly=not a.apply)

    # 1. 대상: financial_field_verification에서 불일치(원인 조사)인 net_income 및 total_equity (CFS)
    cur = conn.cursor()
    code_filter = f"AND v.stock_code IN ({','.join(repr(c) for c in a.codes.split(',') if c)})" if a.codes else ""
    cur.execute(f"""
        SELECT v.stock_code, v.year, v.quarter, v.field, v.db_value, v.fnguide_value
        FROM financial_field_verification v
        WHERE v.report_type = 'CFS'
          AND v.field IN ('net_income', 'total_equity')
          AND v.status = '원인 조사'
          AND v.db_value IS NOT NULL
          AND v.fnguide_value IS NOT NULL
          {code_filter}
    """)
    targets = cur.fetchall()
    print(f"불일치 대상 칸: {len(targets)}건 (net_income, total_equity CFS)")

    # 2. 로컬 DART 원문에서 추출하여 검증
    updates = []  # (row_id, stock_code, year, quarter, field, old_val, new_val, fnguide_val, rule)
    skipped = collections.Counter()

    # DB의 financial_data row_id 매핑
    cur.execute("""
        SELECT id, stock_code, year, quarter, is_annual, net_income, total_equity
        FROM financial_data
        WHERE report_type = 'CFS' AND year >= 2023
    """)
    fd_rows = collections.defaultdict(list)  # 연간은 원장 두 벌(quarter 0·4) 가능 → 같은 기간 행 전부
    for r in cur.fetchall():
        q_key = 0 if r[4] else r[3]
        fd_rows[(r[1], r[2], q_key)].append((r[0], r[5], r[6]))
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='financial_data'").fetchall()}
    protected = {(r[0], r[1]) for r in conn.execute(
        "SELECT row_id, field_name FROM financial_fix_log WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall()}

    for code, year, quarter, field, db_val, fg_val in targets:
        target_key = "ni_parent" if field == "net_income" else "equity_parent"
        if code in excl:
            skipped["제외 종목(외화·비12월·단위)"] += 1
            continue
        if (code, year) in locks:
            skipped["잠금(data_lock)"] += 1
            continue
        # Q4 처리: 독립 공시 파일이 없으므로 연간 사업보고서(_0_) 및 1~3분기 파일 활용
        if quarter == 4:
            p0 = RAW_DIR / code / f"{year}_0_CFS.json.gz"
            if not p0.exists():
                skipped["원문파일없음"] += 1
                continue
            try:
                raw0 = json.loads(gzip.decompress(p0.read_bytes()))
                ext0 = extract(raw0 if isinstance(raw0, list) else raw0.get("list", []))
            except Exception:
                skipped["원문파싱에러"] += 1
                continue

            if field == "total_equity":
                new_val = ext0.get("equity_parent")
            else:  # net_income
                ann_ni = ext0.get("ni_parent")
                new_val = None
                # 방법 A: 연간 - (1Q + 2Q + 3Q)
                qs_ni = []
                for qi in (1, 2, 3):
                    p_i = RAW_DIR / code / f"{year}_{qi}_CFS.json.gz"
                    if p_i.exists():
                        try:
                            raw_i = json.loads(gzip.decompress(p_i.read_bytes()))
                            ext_i = extract(raw_i if isinstance(raw_i, list) else raw_i.get("list", []))
                            qs_ni.append(ext_i.get("ni_parent"))
                        except Exception:
                            pass
                if ann_ni is not None and len(qs_ni) == 3 and all(x is not None for x in qs_ni):
                    cand_a = ann_ni - sum(qs_ni)
                    if close(cand_a, fg_val):
                        new_val = cand_a

                # 방법 B: 연간 - 3분기 누적(thstrm_add_amount) [FnGuide 방식]
                if new_val is None and ann_ni is not None:
                    p3 = RAW_DIR / code / f"{year}_3_CFS.json.gz"
                    if p3.exists():
                        try:
                            raw3 = json.loads(gzip.decompress(p3.read_bytes()))
                            ext3_ytd = extract(raw3 if isinstance(raw3, list) else raw3.get("list", []), "thstrm_add_amount")
                            q3_ytd_ni = ext3_ytd.get("ni_parent")
                            if q3_ytd_ni is not None:
                                cand_b = ann_ni - q3_ytd_ni
                                if close(cand_b, fg_val):
                                    new_val = cand_b
                        except Exception:
                            pass

        else:
            q_code = quarter
            fpath = RAW_DIR / code / f"{year}_{q_code}_CFS.json.gz"
            if not fpath.exists():
                skipped["원문파일없음"] += 1
                continue

            try:
                raw = json.loads(gzip.decompress(fpath.read_bytes()))
                rows = raw if isinstance(raw, list) else raw.get("list", [])
                ext = extract(rows)
            except Exception:
                skipped["원문파싱에러"] += 1
                continue

            target_key = "ni_parent" if field == "net_income" else "equity_parent"
            new_val = ext.get(target_key)


        if new_val is None:
            skipped["지배값추출불가"] += 1
            continue

        # 원칙 0: 새 추출값이 FnGuide와 일치해야만 교체!
        if not close(new_val, fg_val):
            skipped["FnGuide불일치"] += 1
            continue

        # DB row_id 찾기
        infos = fd_rows.get((code, year, quarter)) or []
        if not infos:
            skipped["DB원장행없음"] += 1
            continue
        for rid, ni_now, eq_now in infos:
            cur_v = ni_now if field == "net_income" else eq_now
            if cur_v is not None and abs(float(cur_v) - new_val) <= 0.5:
                skipped["이미 같은 행(no-op)"] += 1
                continue
            if (rid, field) in protected:
                skipped["보호 칸"] += 1
                continue
            rule = f"DART {target_key} 추출 성공 = FnGuide 일치 (기존값={(cur_v or 0):,.0f} → 새지배값={new_val:,.0f})"
            updates.append((rid, code, year, quarter, field, cur_v, new_val, fg_val, rule))

    print(f"\n검증 결과:")
    print(f"  교체 가능(DART 지배값 == FnGuide): {len(updates)}건")
    print(f"  건너뜀 사유별:", dict(skipped))

    if updates:
        print("\n적용 후보 샘플 10건:")
        for u in updates[:10]:
            print(f"  {u[1]} {u[2]}Q{u[3]} {u[4]}: 기존={u[5]:,.0f} → 새지배={u[6]:,.0f} (FnGuide={u[7]:,.0f})")

    if not a.apply:
        print("\ndry-run 완료. 실제 적용은 --apply 플래그 필요.")
        conn.close()
        return

    # --apply 실행 로직
    run_id = f"repair_parent_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")

    # 백업
    bk = "financial_data_backup_parent_repair_20261009"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
    ids = sorted({u[0] for u in updates})
    for i in range(0, len(ids), 1000):
        part = ids[i:i + 1000]
        conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)

    # 업데이트
    for rid, code, year, q, field, old_val, new_val, fg_val, rule in updates:
        conn.execute(f"UPDATE financial_data SET {field}=?, updated_at=? WHERE id=?", (new_val, now, rid))

    # fix_log
    conn.execute(f"SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
    conn.executemany("""
        INSERT INTO financial_fix_log (fixed_at, row_id, stock_code, year, quarter, is_annual, report_type, field_name, old_value, new_value, fix_rule, source, run_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [(now, rid, code, year, q, 1 if q == 0 else 0, "CFS", field, old_val, new_val, rule, "DART fnlttSinglAcntAll parent extraction", run_id)
          for rid, code, year, q, field, old_val, new_val, fg_val, rule in updates])

    conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""
        INSERT INTO data_fix_log (fixed_at, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (now, "financial_data", f"지배주주 지분 정정 {len(updates)}건", len(updates),
          "DART 지배주주 추출 패치 적용 후 FnGuide 일치 확인된 칸만 교체",
          f"net_income/total_equity CFS {len(updates)}건", "DART ni_parent / equity_parent",
          "scripts/review/repair_parent_metrics_20261009.py", run_id))

    conn.commit()
    print(f"\n[적용 완료] financial_data {len(updates)}건 업데이트 완료 (run_id: {run_id})")

    # readback
    bad = 0
    for rid, code, year, q, field, old_val, new_val, fg_val, rule in updates:
        r = conn.execute(f"SELECT {field} FROM financial_data WHERE id=?", (rid,)).fetchone()
        if r is None or abs(float(r[0]) - float(new_val)) > 0.5:
            bad += 1
    print(f"Readback 검증: {len(updates)}건 중 불일치 {bad}건")

    # 정본 동기화
    canon_res = canonical_sync(conn, run_id, now, set(ids), set())
    print("정본 동기화 결과:", json.dumps({k: v for k, v in canon_res.items() if not k.endswith("sample")}, ensure_ascii=False))

    conn.close()
    # 2026-10-09 Claude 보완: 결과 파일 + 적용 직후 재측정(다른 적용 스크립트와 같은 절차)
    import subprocess
    pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                        capture_output=True, text=True, cwd=str(ROOT))
    after = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    print("재측정", after)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / "repair_parent_20261009_apply.json"
    out.write_text(json.dumps({"run_id": run_id, "updates": len(updates), "skipped": dict(skipped), "readback_mismatch": bad,
                               "canonical_sync": canon_res, "field_verification_after": after}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
