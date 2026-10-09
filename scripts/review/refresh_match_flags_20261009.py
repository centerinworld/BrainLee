#!/usr/bin/env python3
"""PLAN_99_99_NUMERIC P12 — 현금흐름 옛 대조 기록(`cf_validation_flags` flag_type MATCH)을 현행 검증 기준으로 재기록 (2026-10-09). 기본 dry-run.

화면 `/api/dashboard/data-quality`의 현금흐름 항목은 MATCH 행이 하나라도 CONFIRMED면 '검증됨'으로 보인다. 10-09 집계: 현행 검증(FnGuide 대조)은
확정이 아닌데 옛 기록은 CONFIRMED인 행이 감가상각 625·CapEx 43·현금흐름 약 70(**거짓 안심**), 현행 확정 행 약 1.28만은 값이 낡음.
규칙(연결·연간, 현행 `financial_field_verification`과 같은 칸):
  현행 '확정…'        → dart_value=DB, fnguide_value=FnGuide, resolved_value=DB, status=CONFIRMED, ai_verdict=NULL
  현행 '원인 조사'    → 값 갱신 + status=AMBIGUOUS(경고 — 현행 판정과 같게)
  현행 '미확인(DB 없음)' → 값 갱신 + status=OPEN(중립 — 확정으로 보이지 않게)
  값은 백만원 단위로 환산해 기록(MATCH 관례). seibro_value(세이브로 값)는 건드리지 않는다. cash_end는 현행 검증에 없어 대상 아님. 바뀌는 행만.
백업 `cf_validation_flags_backup_match_refresh_20261009`, data_fix_log, readback(행 수·상태·값 숫자 비교), 적용 후 dry-run 0건.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

BACKUP = "cf_validation_flags_backup_match_refresh_20261009"
SCALE = 1e6  # ⚠ MATCH 행의 값은 백만원 단위(예: -12454.338495 = -124.5억) — FIN 행(원 단위)과 다르다. 현행 검증 값(원)을 백만원으로 환산해 쓴다
FIELDS = ("operating_cf", "investing_cf", "financing_cf", "capex", "depreciation")


def target_status(vst):
    if vst.startswith("확정"):
        return "CONFIRMED"
    if vst.startswith("원인"):
        return "AMBIGUOUS"
    return "OPEN"


def diff(a, b):
    """백만원 단위 비교(1천원 = 0.001 초과 차이)."""
    return (a is None) != (b is None) or (a is not None and abs(float(a) - float(b)) > 0.001)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=300, readonly=not a.apply)
    cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='cf_validation_flags' ORDER BY ordinal_position").fetchall()]
    rows = conn.execute(f"""
        SELECT {','.join('f.' + c for c in cols)}, v.db_value, v.fnguide_value, v.status
        FROM cf_validation_flags f
        JOIN financial_field_verification v
          ON v.stock_code=f.stock_code AND v.year=f.year AND v.quarter=0 AND v.report_type='CFS' AND v.field=f.field
        WHERE COALESCE(f.flag_type,'MATCH')='MATCH' AND f.field IN ({','.join('?' * len(FIELDS))})""", FIELDS).fetchall()
    plan, why = [], Counter()
    for r in rows:
        old = dict(zip(cols, r[:len(cols)]))
        db_v, fg_v, vst = r[len(cols):]
        db_v = None if db_v is None else float(db_v) / SCALE
        fg_v = None if fg_v is None else float(fg_v) / SCALE
        ts = target_status(str(vst))
        ch = []
        if diff(old.get("dart_value"), db_v): ch.append("dart_value")
        if diff(old.get("fnguide_value"), fg_v): ch.append("fnguide_value")
        if diff(old.get("resolved_value"), db_v): ch.append("resolved_value")
        if str(old.get("status") or "").upper() != ts: ch.append(f"status→{ts}")
        if ts == "CONFIRMED" and old.get("ai_verdict"): ch.append("ai_verdict")
        if not ch:
            continue
        for c in ch: why[c] += 1
        plan.append((old, db_v, fg_v, ts, str(vst)))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "match_rows_joined": len(rows), "rows_to_update": len(plan), "stocks": len({p[0]["stock_code"] for p in plan}),
           "change_reasons": dict(why),
           "status_transition": dict(Counter(f"{str(p[0]['status']).upper()}→{p[3]}" for p in plan)),
           "false_reassurance_by_field": dict(Counter(p[0]["field"] for p in plan
                                                      if str(p[0]["status"]).upper() in ("CONFIRMED", "CLOSE_MATCH") and p[3] != "CONFIRMED"))}
    if a.apply and plan:
        run_id = f"match_flags_refresh_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, backed_up_at TEXT, row_json TEXT)")
        conn.executemany(f"INSERT INTO {BACKUP} VALUES (?,?,?)", [(run_id, now, json.dumps(p[0], ensure_ascii=False, default=str)) for p in plan])
        for old, db_v, fg_v, ts, vst in plan:
            conn.execute("""UPDATE cf_validation_flags SET dart_value=?, fnguide_value=?, resolved_value=?, status=?,
                            ai_verdict=CASE WHEN ?='CONFIRMED' THEN NULL ELSE ai_verdict END, resolved_at=?, ai_reasoning=? WHERE id=?""",
                         (db_v, fg_v, db_v, ts, ts, now,
                          f"재검증 {now[:10]}: 현행 financial_field_verification '{vst}' → {ts} (이전 {old['status']})", old["id"]))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cf_validation_flags", f"MATCH 현금흐름·CapEx·감가상각 {res['stocks']}종목", len(plan),
                      "현행 검증 상태로 재기록: 확정→CONFIRMED, 원인 조사→AMBIGUOUS, DB 없음→OPEN(거짓 안심 제거), 값은 현재 DB·FnGuide",
                      json.dumps(res["status_transition"], ensure_ascii=False)[:900], json.dumps(dict(why), ensure_ascii=False)[:900],
                      "scripts/review/refresh_match_flags_20261009.py", run_id))
        conn.commit()
        want = {p[0]["id"]: p for p in plan}
        bad, got = [], 0
        ids = list(want)
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            for r in conn.execute(f"SELECT id, dart_value, fnguide_value, resolved_value, status FROM cf_validation_flags WHERE id IN ({','.join('?' * len(part))})", part).fetchall():
                got += 1
                _, db_v, fg_v, ts, _ = want[r[0]]
                if str(r[4]).upper() != ts or diff(r[1], db_v) or diff(r[2], fg_v) or diff(r[3], db_v):
                    bad.append(r[0])
        res["post_readback"] = {"planned_rows": len(plan), "rows_read": got, "mismatch": len(bad), "sample": bad[:20], "pass": got == len(plan) and not bad}
        res.update({"applied": True, "run_id": run_id, "backup_table": BACKUP})
        print("readback", json.dumps({k: v for k, v in res["post_readback"].items() if k != "sample"}, ensure_ascii=False), flush=True)
    out = ROOT / "research_outputs" / f"match_flags_refresh_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(res, ensure_ascii=False, default=str)[:900])


if __name__ == "__main__":
    main()
