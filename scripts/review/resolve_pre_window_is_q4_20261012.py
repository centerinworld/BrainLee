#!/usr/bin/env python3
"""2021~2022(FnGuide 표시 이전, OpenDART 단일 경로) 내부 검증 남은 손익 칸 정리 (2026-10-12). 기본 dry-run.

입력: internal_checks_pre_window 최신 실패 csv 중 손익(매출·영업이익·순이익) 칸.
판정 규칙(사용자 10-12 '사람이 정할 필요 없는 건 원칙대로 정하라' — PLAN §7 10-12 기록):
  U  단위 오기: 연간 후보(재작성·원래 공시) 중 |값| > 10 × 총자산 중앙값인 것은 버리고 정상 단위 값을 연간으로.
  4분기 = 같은 기준 연간 − 1~3분기(D-6). 기준 우선순위:
    R  재작성 기준: 재작성 연간 − 다음 해 분기보고서 전기 3개월 합(세 분기 모두 있을 때). 매출이면 ≥ 0 이어야 함.
    O  원래 공시 기준: 원래 연간 − 원래 3분기 누적. 매출이면 ≥ 0. 이때 1~3분기 DB 값도 원래 공시 3개월 값으로 맞춘다.
    X  원문 모순(두 기준 모두 4분기 매출 < 0 — 중단사업 재분류로 연간만 줄어든 경우): 4분기 매출 = 비움, 예외 RAW_CONTRADICTION.
    N  같은 재무제표 종류의 연간 원문이 없음(연결 분기만 있고 연간은 별도뿐 등): 4분기 = 비움, 예외 NO_SAME_FS_ANNUAL.
  1~3분기 원문 자체가 음수 매출: 공시값 유지, 예외 RAW_NEGATIVE_DISCLOSED.
적용: 백업·fix_log·financial_field_exception·data_fix_log·되읽기·정본 동기화. 출처 '+resolve_is_q4_20261012'.
"""
from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
TAG = "+resolve_is_q4_20261012"
T = 2e6
IS = ("revenue", "operating_profit", "net_income")
BASIS = "사용자 10-12 위임('사람이 결정해야 할 걸 굳이') — PLAN §7 10-12 규칙"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--fails")
    ap.add_argument("--no-annual-sweep", action="store_true", help="OpenDART 013(연간 자료 없음) 확인된 회사-연도의 4분기 손익 전부 비움(probe_missing_annual_20261012.json)")
    a = ap.parse_args()
    if a.no_annual_sweep:
        return sweep_no_annual(a)
    src = Path(a.fails) if a.fails else sorted(OUT.glob("internal_chk_*_fails.csv"))[-1]
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    cells = sorted({(x["stock_code"], int(x["year"]), x["fs"], x["field"]) for x in csv.DictReader(open(src))
                    if x["year"] in ("2021", "2022") and x["field"] in IS})
    qcells = {(x["stock_code"], int(x["year"]), int(x["quarter"]), x["fs"], x["field"]) for x in csv.DictReader(open(src))
              if x["year"] in ("2021", "2022") and x["field"] in IS}
    scale = {}

    def plaus(code, v):
        if v is None:
            return False
        if code not in scale:
            xs = sorted(abs(r[0]) for r in conn.execute("SELECT total_assets FROM financial_data WHERE stock_code=? AND year BETWEEN 2020 AND 2023 AND total_assets IS NOT NULL AND total_assets<>0", (code,)).fetchall())
            scale[code] = xs[len(xs) // 2] if xs else None
        return scale[code] is None or abs(v) <= 10 * scale[code]

    plan, exc, st = [], [], collections.Counter()  # plan: (rid, code, y, q, fs, fld, old, new, rule)
    for code, y, fs, f in cells:
        rows = {(0 if r[1] else r[0]): (r[2], r[3]) for r in conn.execute(
            f"SELECT quarter, is_annual, id, {f} FROM financial_data WHERE stock_code=? AND year=? AND report_type=? ORDER BY id", (code, y, fs)).fetchall()}
        db = {q: v for q, (_, v) in rows.items()}
        rf = fn.effective_fs(code, y, fs)
        # 1~3분기 원문 음수 매출(공시값 그대로)
        for q in (1, 2, 3):
            if (code, y, q, fs, f) in qcells and f == "revenue" and db.get(q) is not None and db[q] < -T:
                rq = [fn.raw(code, y, q, rf, f), fn.latest_quarter_3m(code, y, q, rf, f)[0]]
                if any(r is not None and abs(r - db[q]) <= T for r in rq):
                    exc.append((code, y, q, fs, f, "RAW_NEGATIVE_DISCLOSED", f"분기보고서 3개월 매출 원문 = {db[q]:,.0f}(공시값)", db[q],
                                "DART 원문 자체가 음수 매출(매출 차감·정정) — 공시값 그대로 둠")); st["1~3분기 원문 음수 매출 — 공시값 유지"] += 1
                else:
                    st["1~3분기 음수 매출 — 원문과도 다름(보류)"] += 1
        if (code, y, 4, fs, f) not in qcells and (code, y, 0, fs, f) not in qcells:
            continue
        Ao, (Ar, _) = fn.raw(code, y, 0, rf, f), fn.latest_annual(code, y, rf, f)
        Ao, Ar = (Ao if plaus(code, Ao) else None), (Ar if plaus(code, Ar) else None)
        y3 = fn.ytd3(code, y, rf, f)
        qo = [fn.raw(code, y, q, rf, f) for q in (1, 2, 3)]
        qr = [fn.raw(code, y + 1, q, rf, f, "frmtrm_q_amount") for q in (1, 2, 3)]
        ok_rev = (lambda v: f != "revenue" or v >= -T)
        new = {}  # q -> (value, rule)
        if Ar is not None and None not in qr and ok_rev(Ar - sum(qr)):
            A, q4, qs, rule = Ar, Ar - sum(qr), qr, "R 재작성 기준(재작성 연간 − 다음 해 분기보고서 전기 3개월 합)"
        elif Ao is not None and y3 is not None and ok_rev(Ao - y3):
            A, q4, qs, rule = (Ar if Ar is not None else Ao), Ao - y3, (qo if None not in qo and abs(sum(qo) - y3) <= T else None), "O 원래 공시 기준(원래 연간 − 원래 3분기 누적)"
        elif Ao is None and Ar is None and db.get(0) is not None and not plaus(code, db[0]):
            # 원문 연간 자체가 단위 오기(두 후보 모두 규모 초과): 정확히 1,000배면 ÷1,000, 아니면 비움
            fixd = db[0] / 1000 if plaus(code, db[0] / 1000) else None
            exc.append((code, y, 0, fs, f, "RAW_UNIT_ERROR", f"원문 연간 {db[0]:,.0f} — 총자산 중앙값의 10배 초과, 3분기 누적 {y3}", db[0],
                        "원문 연간이 단위 오기 — " + ("1,000으로 나눈 값 사용" if fixd is not None else "정상 단위를 알 수 없어 비움")))
            st["U 원문 연간 단위 오기"] += 1
            if 0 in rows:
                new[0] = (fixd, "U 원문 연간 단위 오기 → " + ("÷1,000" if fixd is not None else "비움"))
            if fixd is not None and y3 is not None and ok_rev(fixd - y3):
                A, q4, qs, rule = fixd, fixd - y3, None, "O 원래 공시 기준(단위 바로잡은 연간 − 원래 3분기 누적)"
            else:
                A, q4, qs, rule = None, None, None, "N"
        elif Ao is None and Ar is None:
            A, q4, qs, rule = None, None, None, "N"
        else:
            A, q4, qs, rule = (Ar if Ar is not None else Ao), None, None, "X"
        if A is not None and (db.get(0) is None or abs(db[0] - A) > 0.5) and 0 in rows:
            new[0] = (A, "U 연간 = 정상 단위 원문" if db.get(0) is not None and not plaus(code, db[0]) else "연간 = 원문")
        if rule == "N":
            if db.get(4) is not None and 4 in rows:
                new[4] = (None, "N 같은 종류(연결/별도) 연간 원문 없음 → 4분기 계산 불가, 비움")
                exc.append((code, y, 4, fs, f, "NO_SAME_FS_ANNUAL", f"{rf} 연간 원문 없음(분기만 존재), DB 연간 {db.get(0)}", db.get(4),
                            "같은 재무제표 종류의 연간 원문이 없어 4분기 = 연간 − 3분기 누적을 계산할 수 없음 — 다른 종류 섞지 않고 비움"))
            st["N 연간 원문 없음"] += 1
        elif rule == "X":
            if 4 in rows and db.get(4) is not None:
                new[4] = (None, "X 원문 모순(재작성·원래 공시 모두 4분기 매출 < 0) → 비움")
            exc.append((code, y, 4, fs, f, "RAW_CONTRADICTION", f"원래 연간 {Ao}, 재작성 연간 {Ar}, 3분기 누적 {y3}, 재작성 분기 {qr}", db.get(4),
                        "연간 매출 < 1~3분기 합(중단사업 재분류로 연간만 줄어든 것으로 보임) — 음수 4분기 매출을 넣지 않음"))
            st["X 원문 모순"] += 1
        else:
            if 4 in rows and (db.get(4) is None or abs(db[4] - q4) > 0.5):
                new[4] = (q4, rule)
            if qs is not None:
                for i, q in enumerate((1, 2, 3)):
                    if q in rows and qs[i] is not None and (db.get(q) is None or abs(db[q] - qs[i]) > 0.5):
                        new[q] = (qs[i], rule + " — 1~3분기도 같은 기준")
            st[rule[:1] + " 기준 4분기"] += 1
        for q, (v, r) in new.items():
            plan.append((rows[q][0], code, y, q, fs, f, db.get(q), v, r))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "src": src.name, "cells": len(cells), "stats": dict(st),
           "plan_rows": len(plan), "exceptions": len(exc), "plan": [p[1:] for p in plan], "exc": [e[:6] for e in exc]}
    if a.apply and (plan or exc):
        run_id = f"resolve_is_q4_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "financial_data_backup_resolve_is_q4_20261012"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
        ids = sorted({p[0] for p in plan})
        if ids:
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            conn.execute(f"UPDATE financial_data SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?",
                         [TAG] + ids + [f"%{TAG}%"])
        for rid, code, y, q, fs, fld, old, v, r in plan:
            conn.execute(f"UPDATE financial_data SET {fld}=?, updated_at=? WHERE id=?", (v, now, rid))
        conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
        conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if q == 0 else 0, fs, fld, old, v, r, "DART 원문(data_raw/dart_fnltt)", run_id) for rid, code, y, q, fs, fld, old, v, r in plan])
        for code, y, q, fs, fld, rc, ev, dbv, why in exc:
            conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=? AND reason_code=?", (code, y, q, fs, fld, rc))
            conn.execute("""INSERT INTO financial_field_exception(stock_code,year,quarter,report_type,field,reason_code,dart_evidence,db_value,fnguide_value,naver_value,exclusion_reason,approved_basis,run_id,created_at)
                            VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?)""", (code, y, q, fs, fld, rc, ev, dbv, why, BASIS, run_id, now))
        rel = sorted({(p[1], p[2]) for p in plan})
        for code_, y_ in rel:
            conn.execute("UPDATE data_lock SET is_locked=0, unlock_reason=?, unlocked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1",
                         (f"원문 > 잠금(§5-10) — {run_id}", now, code_, y_))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data", "2021~2022 내부 검증 남은 손익 칸(4분기 기준 통일·단위 오기·원문 모순)", len(plan), BASIS,
                      json.dumps(dict(st), ensure_ascii=False), f"예외 {len(exc)}건", "scripts/review/resolve_pre_window_is_q4_20261012.py", run_id))
        conn.commit()
        bad = [p for p in plan if (lambda cur: (cur is None) != (p[7] is None) or (cur is not None and abs(float(cur) - p[7]) > 0.5))(
            conn.execute(f"SELECT {p[5]} FROM financial_data WHERE id=?", (p[0],)).fetchone()[0])]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, set(ids), set()).items() if "sample" not in k}})
    print(json.dumps(res, ensure_ascii=False, default=str))


def sweep_no_annual(a):
    """같은 종류 연간 원문이 OpenDART 013(자료 없음)으로 확인된 회사-연도: 4분기 = 연간 − 3분기 누적을 계산할 수 없으므로 손익 4분기를 비운다(규칙 N).
    (다른 종류 연간 − 이 종류 3분기 누적으로 만든 값이 남아 있었다: 036620 2021 연결 등)"""
    probe = json.load(open(OUT / "probe_missing_annual_20261012.json"))
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    plan, exc = [], []
    for k, v in probe.items():
        if not v.startswith("013"):
            continue
        code, y, rf = k.split("|"); y = int(y)
        for rid, fs, *vals in conn.execute(f"SELECT id, report_type, {','.join(IS)} FROM financial_data WHERE stock_code=? AND year=? AND quarter=4 AND NOT is_annual", (code, y)).fetchall():
            if fn.effective_fs(code, y, fs) != rf:
                continue
            for f, old in zip(IS, vals):
                if old is not None:
                    plan.append((rid, code, y, 4, fs, f, old, None, "N 같은 종류 연간 원문 없음(OpenDART 013 확인) → 4분기 계산 불가, 비움"))
                    exc.append((code, y, 4, fs, f, "NO_SAME_FS_ANNUAL", f"{rf} 사업보고서 OpenDART 013(자료 없음) 확인 10-12, 분기 원문만 존재", old,
                                "같은 재무제표 종류의 연간 원문이 없어 4분기를 계산할 수 없음 — 다른 종류를 섞지 않고 비움"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "mode": "no_annual_sweep", "plan_rows": len(plan), "exceptions": len(exc),
           "by_year": dict(collections.Counter(p[2] for p in plan))}
    if a.apply and plan:
        _apply(conn, plan, exc, res, "N 연간 원문 없음 4분기 비움(013 확인)")
    print(json.dumps(res, ensure_ascii=False, default=str))


def _apply(conn, plan, exc, res, scope):
    run_id = f"resolve_is_q4_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    bk = "financial_data_backup_resolve_is_q4_20261012"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
    ids = sorted({p[0] for p in plan})
    conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
    conn.execute(f"UPDATE financial_data SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?",
                 [TAG] + ids + [f"%{TAG}%"])
    for rid, code, y, q, fs, fld, old, v, r in plan:
        conn.execute(f"UPDATE financial_data SET {fld}=?, updated_at=? WHERE id=?", (v, now, rid))
    conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
    conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(now, rid, code, y, q, 1 if q == 0 else 0, fs, fld, old, v, r, "DART 원문(data_raw/dart_fnltt)·OpenDART 응답", run_id) for rid, code, y, q, fs, fld, old, v, r in plan])
    for code, y, q, fs, fld, rc, ev, dbv, why in exc:
        conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=? AND reason_code=?", (code, y, q, fs, fld, rc))
        conn.execute("""INSERT INTO financial_field_exception(stock_code,year,quarter,report_type,field,reason_code,dart_evidence,db_value,fnguide_value,naver_value,exclusion_reason,approved_basis,run_id,created_at)
                        VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?)""", (code, y, q, fs, fld, rc, ev, dbv, why, BASIS, run_id, now))
    for code_, y_ in sorted({(p[1], p[2]) for p in plan}):
        conn.execute("UPDATE data_lock SET is_locked=0, unlock_reason=?, unlocked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1",
                     (f"원문 > 잠금(§5-10) — {run_id}", now, code_, y_))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "financial_data", scope, len(plan), BASIS, json.dumps(res.get("stats") or res.get("by_year"), ensure_ascii=False, default=str), f"예외 {len(exc)}건",
                  "scripts/review/resolve_pre_window_is_q4_20261012.py", run_id))
    conn.commit()
    bad = [p for p in plan if (lambda cur: (cur is None) != (p[7] is None) or (cur is not None and abs(float(cur) - p[7]) > 0.5))(
        conn.execute(f"SELECT {p[5]} FROM financial_data WHERE id=?", (p[0],)).fetchone()[0])]
    res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, set(ids), set()).items() if k != "mismatch_sample"}})


if __name__ == "__main__":
    main()
