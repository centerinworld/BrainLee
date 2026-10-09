#!/usr/bin/env python3
"""재무(매출·영업이익·순이익) 옛 대조 기록 재검증 — 화면 '재확인 필요' 거짓 경고 정리 (2026-10-09). 기본 dry-run, --apply 때만 쓴다.

배경: 종목 데이터 품질 화면(`/api/dashboard/data-quality`)의 '재무제표(매출·영업이익)' 항목은 `cf_validation_flags`의
FIN_NAVER·FIN_CROSS 행(2026-05·08 배치)으로 판정한다. 그 뒤 DB 재무 값이 정정됐는데(예: 에이엘티 2024 매출 442.75억 → 366.67억 =
FnGuide·네이버) 옛 기록은 정정 전 DART 값과 '금융업 구조적 차이' 오분류를 그대로 들고 있어 경고가 남는다.
현행 기준은 `financial_field_verification`(FINANCIAL_STATEMENTS.md — FnGuide·네이버 실제 값 대조)이다.

규칙: FIN 행(연도·항목)에 대응하는 현행 검증(연결 CFS·연간)이 '확정…'이면 그 값으로 다시 기록한다
  dart_value=현행 DB 값, fnguide_value=현행 FnGuide 값, (FIN_NAVER는 seibro_value 칸 = 네이버 값), status=CONFIRMED,
  ai_verdict=NULL(오분류 제거), resolved_value=현행 DB 값, resolved_at=지금, ai_reasoning에 근거 한 줄.
  현행 검증이 확정이 아니면 손대지 않는다(경고 유지). 바뀌는 행만 대상.
쓰기 전 원래 행 전체를 cf_validation_flags_backup_fin_refresh_20261009(run_id, backed_up_at, row_json)에 백업, data_fix_log 기록.
사용: refresh_fin_validation_flags_20261009.py [--apply]
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

BACKUP = "cf_validation_flags_backup_fin_refresh_20261009"
FIELDS = ("revenue", "operating_profit", "net_income")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=300, readonly=not a.apply)
    cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='cf_validation_flags' "
                                       "ORDER BY ordinal_position").fetchall()]
    sel = ",".join("f." + c for c in cols)
    rows = conn.execute(f"""
        SELECT {sel}, v.db_value, v.fnguide_value, v.naver_value, v.status
        FROM cf_validation_flags f
        JOIN financial_field_verification v
          ON v.stock_code=f.stock_code AND v.year=f.year AND v.quarter=0 AND v.report_type='CFS' AND v.field=f.field
        WHERE f.flag_type IN ('FIN_NAVER','FIN_CROSS') AND f.field IN ({','.join('?' * len(FIELDS))})
          AND v.status LIKE '확정%%'""", FIELDS).fetchall()
    plan, why = [], Counter()
    for r in rows:
        old = dict(zip(cols, r[:len(cols)]))
        db_v, fg_v, nv_v, vst = r[len(cols):]
        new_seibro = nv_v if old["flag_type"] == "FIN_NAVER" else old.get("seibro_value")
        changed = []
        if old.get("dart_value") is None or db_v is None or abs(float(old["dart_value"] or 0) - float(db_v or 0)) > 1:
            changed.append("dart_value")
        if str(old.get("status") or "").upper() != "CONFIRMED":
            changed.append("status")
        if old.get("ai_verdict"):
            changed.append("ai_verdict")
        if not changed:
            continue
        for c in changed:
            why[c] += 1
        plan.append((old, db_v, fg_v, new_seibro, vst))
    stocks = {p[0]["stock_code"] for p in plan}
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "fin_rows_with_confirmed_current": len(rows), "rows_to_update": len(plan), "stocks": len(stocks),
           "change_reasons": dict(why), "status_before": dict(Counter(str(p[0]["status"]).upper() for p in plan)),
           "sample": [{"stock_code": p[0]["stock_code"], "year": p[0]["year"], "field": p[0]["field"], "flag_type": p[0]["flag_type"],
                       "old_status": p[0]["status"], "old_dart": p[0]["dart_value"], "new_dart": p[1], "fnguide": p[2],
                       "old_ai_verdict": p[0]["ai_verdict"]} for p in plan[:15]]}
    if a.apply and plan:
        run_id = f"fin_flags_refresh_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, backed_up_at TEXT, row_json TEXT)")
        conn.executemany(f"INSERT INTO {BACKUP} (run_id, backed_up_at, row_json) VALUES (?,?,?)",
                         [(run_id, now, json.dumps(p[0], ensure_ascii=False, default=str)) for p in plan])
        for old, db_v, fg_v, new_seibro, vst in plan:
            conn.execute("""UPDATE cf_validation_flags SET dart_value=?, fnguide_value=?, seibro_value=?, status='CONFIRMED',
                            ai_verdict=NULL, resolved_value=?, resolved_at=?,
                            ai_reasoning=? WHERE id=?""",
                         (db_v, fg_v, new_seibro, db_v, now,
                          f"재검증 {now[:10]}: 현행 financial_field_verification '{vst}'(FnGuide·네이버 값 대조) — 이전 {old['status']}/{old.get('ai_verdict') or '-'} 대체",
                          old["id"]))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cf_validation_flags", f"FIN_NAVER·FIN_CROSS 매출·영업이익·순이익 {len(stocks)}종목", len(plan),
                      "현행 검증(financial_field_verification) 확정 항목의 옛 대조 기록을 현재 값으로 재검증(오분류 판정 제거)",
                      json.dumps(res["status_before"], ensure_ascii=False), "CONFIRMED", "scripts/review/refresh_fin_validation_flags_20261009.py", run_id))
        conn.commit()
        res.update({"applied": True, "run_id": run_id, "backup_table": BACKUP})
    else:
        res["applied"] = False
    out = ROOT / "research_outputs" / f"fin_flags_refresh_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "fin_rows_with_confirmed_current", "rows_to_update", "stocks", "change_reasons",
                                          "status_before", "applied")}, ensure_ascii=False))
    for s in res["sample"][:5]:
        print(" ", s)


if __name__ == "__main__":
    main()
