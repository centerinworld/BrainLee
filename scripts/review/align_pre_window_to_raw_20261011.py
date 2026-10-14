#!/usr/bin/env python3
"""FnGuide 표시 기간 이전(2016~2022) — 원문 = DB 정렬, OpenDART 단일 경로 (PLAN_99_99_NUMERIC §8-2, 사용자 지시 2026-10-11). 기본 dry-run.

대상: 최신 내부 검증 실패 목록(internal_chk_*_fails.csv) 중 'C4 원문 ≠ DB' 칸(연도 범위 지정).
값(fin_norm 정의 그대로 — 외부 확인 없이 규칙만): 연간 = latest_annual(Y+1 사업보고서 전기 칸 > Y 당기) / 분기 손익 = latest_quarter_3m /
  분기 재무상태 = 그 분기 보고서 / 현금흐름 연간 = latest_annual. 순이익·자본 연결 = 지배. 손실 라벨 양수 = 음수(fin_norm.raw).
보류: 1,000배 급변(원문 단위 오류 의심)·원문 후보 없음. 잠금은 10-03 '원문 > 잠금' 원칙으로 무시하지 않고 건너뜀(잠금 칸 목록 기록).
적용: 백업·fix_log·data_fix_log·readback·정본 동기화. 출처 '+align_raw_20261011'.
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
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
TAG = "+align_raw_20261011"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def cand(code, y, q, fs, f):
    fs = fn.effective_fs(code, y, fs)  # 10-12: 연결 미작성 회사의 'CFS' 행 = 별도 원문
    if q == 0:
        return fn.latest_annual(code, y, fs, f)[0]
    if f in fn.BS:
        return fn.raw(code, y, q, fs, f)
    if f in fn.FLOW_IS:
        return fn.latest_quarter_3m(code, y, q, fs, f)[0]
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--years", default="2021-2022")
    ap.add_argument("--is-restated-basis", action="store_true", help="손익 기준 통일(§2-5): 다음 해 보고서의 재작성 연간·1~3분기 3개월이 모두 있으면 연간·1~3분기·4분기(= 연간 − 세 분기 합)를 재작성 기준으로")
    ap.add_argument("--release-locks", action="store_true", help="원문 > 잠금(§5-10): 잠긴 종목-연도도 정렬하고, 실제로 바뀐 종목-연도의 잠금만 해제")
    ap.add_argument("--bs-q4", action="store_true", help="재무상태 4분기 = 연간(D-6). 연간 행이 여러 개면 원문과 맞는 행, 없으면 첫 행")
    ap.add_argument("--cf-chain", action="store_true", help="현금흐름 사슬 전체(연간·1~3분기 누적·3개월·4분기)를 원문 규칙값으로(fin_norm). 연결 미작성 회사는 별도 원문(fn.effective_fs)"); a = ap.parse_args()
    y0, y1 = map(int, a.years.split("-"))
    f = sorted(OUT.glob("internal_chk_*_fails.csv"))[-1]
    # 10-14: 감가상각(depreciation)은 dart_key 미지원·C14 전용 처리 — C4 정렬 대상 제외
    _SKIP_FIELDS = {"depreciation"}
    rows = [r for r in csv.DictReader(open(f)) if "C4" in r["checks"] and y0 <= int(r["year"]) <= y1
            and r["field"] not in _SKIP_FIELDS]
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    locked_all = set(locks)
    if a.release_locks:  # 10-12: '원문 > 잠금'(FINANCIAL §5-10, 사용자 승인 10-03) — 잠긴 종목-연도도 원문 규칙값으로, 바뀌는 곳만 잠금 해제
        locks = set()
    st, plan, hold = collections.Counter(), [], []
    if a.cf_chain:
        rows = []
        keys = sorted({tuple(r) for r in conn.execute("SELECT stock_code, year, report_type FROM cash_flow_data WHERE year BETWEEN ? AND ? AND quarter IS NOT NULL", (y0, y1)).fetchall()})
        for code, y, fs in keys:
            if (code, y, "cash_flow_data") in locks:
                continue
            rfs = fn.effective_fs(code, y, fs)  # 10-12: 연결 미작성 회사의 'CFS' 행 = 별도 원문(연간 일치 확인 후에만)
            if rfs != fs:
                dbA = [r[0] for r in conn.execute("SELECT operating_cf FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual",
                                                  (code, y, fs)).fetchall() if r[0] is not None]
                oA = [v for v in (fn.latest_annual(code, y, rfs, "operating_cf")[0], fn.raw(code, y, 0, rfs, "operating_cf")) if v is not None]
                if not (dbA and oA and any(abs(a - o) <= 2e6 for a in dbA for o in oA)):
                    st["연결 원문 없음 — 별도 원문과 연간 불일치, 보류"] += 1; hold.append((code, y, 0, fs, "*", "연결 원문 없음·별도 연간 불일치")); continue
                st["연결 미작성 → 별도 원문 사용(회사-연도)"] += 1
            for fld in fn.FLOW_CF:
                A = fn.latest_annual(code, y, rfs, fld)[0]
                cum = {q: fn.latest_cf_cum(code, y, q, rfs, fld)[0] for q in (1, 2, 3)}
                # 10-12: 원문 누적이 없는 분기·연간은 DB 누적값(하나로 정해질 때만)으로 사슬을 잇는다 — 3개월 = 누적 차분, 4분기 = 연간 − 3분기 누적(D-6)
                def _db(cond, prm):
                    vs = {r[0] for r in conn.execute(f"SELECT {fld} FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}", (code, y, fs) + prm).fetchall() if r[0] is not None}
                    if len(vs) > 1:  # 10-12: 억 단위 반올림 옛 값(FnGuide 캡처)과 정밀값이 함께 있으면, 반올림 오차 안의 정밀값 하나만
                        ex = {v for v in vs if v % 1e8 != 0}
                        rd = vs - ex
                        if len(ex) == 1 and all(abs(r - next(iter(ex))) <= 5e7 for r in rd):
                            vs = ex
                    return vs.pop() if len(vs) == 1 else None
                if A is None:
                    A = _db("is_annual", ())
                    if A is not None:
                        st["연간 원문 없음 → DB 연간(단일값)으로 사슬"] += 1
                for q in (1, 2, 3):
                    if cum[q] is None:
                        cum[q] = _db("NOT is_annual AND quarter=?", (q,))
                        if cum[q] is not None:
                            st["분기 누적 원문 없음 → DB 누적으로 사슬"] += 1
                tgt = {}
                if A is not None:
                    tgt[(0, fld)] = A
                    tgt[(4, fld)] = A
                    if cum[3] is not None:
                        tgt[(4, fld + "_q")] = fn.q4_flow(A, cum[3])
                for q in (1, 2, 3):
                    if cum[q] is not None:
                        tgt[(q, fld)] = cum[q]
                        th = fn.cf_three_month(cum[q], cum[q - 1] if q > 1 else None, q)
                        if th is not None:
                            tgt[(q, fld + "_q")] = th
                for (q, col), v in tgt.items():
                    cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
                    for rid, old in conn.execute(f"SELECT id, {col} FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                                                 (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall():
                        if old is not None and abs(old - v) <= 0.5:
                            continue
                        if old is not None and _ofs.scale_jump(old, v):
                            st["1,000배 급변 — 보류"] += 1; hold.append((code, y, q, fs, col, f"1000배 {old}→{v}")); continue
                        plan.append(("cash_flow_data", rid, code, y, q, fs, col, old, v)); st["현금흐름 사슬 정렬"] += 1
    if a.is_restated_basis:
        rows = []
        unit_err = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:dart_unit_error'").fetchall()}
        keys = sorted({tuple(r) for r in conn.execute("SELECT stock_code, year, report_type FROM financial_data WHERE year BETWEEN ? AND ? AND quarter=4 AND NOT is_annual", (y0, y1)).fetchall()})
        for code, y, fs in keys:
            if (code, y, "financial_data") in locks:
                continue
            rfs = fn.effective_fs(code, y, fs)
            for fld in fn.FLOW_IS:
                Ar = fn.raw(code, y + 1, 0, rfs, fld, "frmtrm_amount")
                qr = [fn.raw(code, y + 1, q, rfs, fld, "frmtrm_q_amount") for q in (1, 2, 3)]
                if Ar is None or None in qr:
                    st["재작성 세트 불완전 — 건너뜀"] += 1; continue
                q4 = Ar - sum(qr)
                if fld == "revenue" and q4 < -2e6:
                    st["재작성 세트로도 4분기 매출 음수 — 보류"] += 1; hold.append((code, y, 4, fs, fld, f"재작성 4분기 음수 {q4}")); continue
                tg = {0: Ar, 1: qr[0], 2: qr[1], 3: qr[2], 4: q4}
                n0 = len(plan)
                for q, v in tg.items():
                    cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
                    for rid, old in conn.execute(f"SELECT id, {fld} FROM financial_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                                                 (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall():
                        if old is not None and abs(old - v) <= 0.5:
                            continue
                        if old is not None and _ofs.scale_jump(old, v):
                            # 10-12: 단위 오기 종목(fs_quirk:dart_unit_error)에서 DB 값 = 원래 공시 원문(단위 오기)이고 새 값이 재작성 세트면 허용
                            ao = fn.raw(code, y, q, rfs, fld) if q in (0, 1, 2, 3) else None
                            # 10-12 정정: 작아지는 방향만(오기값 → 정상 단위). 재작성 칸 쪽이 오기인 경우(032680 2021)를 막는다
                            if not (code in unit_err and abs(v) < abs(old) and (q == 4 or (ao is not None and abs(old - ao) <= 0.5))):
                                st["1,000배 급변 — 보류"] += 1; hold.append((code, y, q, fs, fld, f"1000배 {old}→{v}")); continue
                            st["단위 오기 → 재작성 정상 단위"] += 1
                        plan.append(("financial_data", rid, code, y, q, fs, fld, old, v))
                st["재작성 기준으로 바뀜(회사-연도-항목)" if len(plan) > n0 else "이미 재작성 기준"] += 1
    if a.is_restated_basis:  # 10-12: 재무상태 연간도 한 세트로(재작성 세트가 완전하고 항등식이 맞으면 재작성, 아니면 원래 공시 세트)
        BS3 = ("total_assets", "total_liabilities", "total_equity")
        keys = sorted({tuple(r) for r in conn.execute("SELECT stock_code, year, report_type FROM financial_data WHERE year BETWEEN ? AND ? AND is_annual", (y0, y1)).fetchall()})
        for code, y, fs in keys:
            if (code, y, "financial_data") in locks:
                continue
            rfs = fn.effective_fs(code, y, fs)
            pick = None
            for yy, col, lab in ((y + 1, "frmtrm_amount", "재작성"), (y, "thstrm_amount", "원래 공시")):
                sv = [fn.raw(code, yy, 0, rfs, f, col) for f in BS3]
                if None in sv:
                    continue
                et = fn._extract(code, yy, 0, rfs, col).get("equity_total")
                if et is not None and abs(sv[0] - sv[1] - et) <= max(2e6, abs(sv[0]) * 1e-4):
                    pick = (lab, sv); break
            if pick is None:
                st["재무상태 세트 없음·항등식 불일치 — 건너뜀"] += 1; continue
            n0 = len(plan)
            for rid, *old in conn.execute("SELECT id, total_assets, total_liabilities, total_equity FROM financial_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual", (code, y, fs)).fetchall():
                for i, col in enumerate(BS3):
                    v = pick[1][i]
                    if old[i] is not None and abs(old[i] - v) <= 0.5:
                        continue
                    if old[i] is not None and _ofs.scale_jump(old[i], v):
                        st["1,000배 급변 — 보류"] += 1; hold.append((code, y, 0, fs, col, f"1000배 {old[i]}→{v}")); continue
                    plan.append(("financial_data", rid, code, y, 0, fs, col, old[i], v))
            st[f"재무상태 연간 {pick[0]} 세트로 바뀜" if len(plan) > n0 else "재무상태 연간 이미 한 세트"] += 1
    if a.bs_q4:
        rows = []
        BSF = ("total_assets", "total_liabilities", "total_equity")
        keys = sorted({tuple(r) for r in conn.execute("SELECT stock_code, year, report_type FROM financial_data WHERE year BETWEEN ? AND ? AND quarter=4 AND NOT is_annual", (y0, y1)).fetchall()})
        for code, y, fs in keys:
            if (code, y, "financial_data") in locks:
                continue
            ann = conn.execute("SELECT total_assets, total_liabilities, total_equity FROM financial_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual ORDER BY id",
                               (code, y, fs)).fetchall()
            if not ann:
                st["연간 행 없음"] += 1; continue
            rv = [fn.latest_annual(code, y, fn.effective_fs(code, y, fs), f)[0] for f in ("total_assets", "total_liabilities")]
            pick = next((r for r in ann if all(v is None or (r[i] is not None and abs(r[i] - v) <= 2e6) for i, v in enumerate(rv))), ann[0])
            for rid, *old in conn.execute("SELECT id, total_assets, total_liabilities, total_equity FROM financial_data WHERE stock_code=? AND year=? AND report_type=? AND quarter=4 AND NOT is_annual",
                                          (code, y, fs)).fetchall():
                for i, col in enumerate(BSF):
                    v = pick[i]
                    if v is None or (old[i] is not None and abs(old[i] - v) <= 0.5):
                        continue
                    if old[i] is not None and _ofs.scale_jump(old[i], v):
                        st["1,000배 급변 — 보류"] += 1; hold.append((code, y, 4, fs, col, f"1000배 {old[i]}→{v}")); continue
                    plan.append(("financial_data", rid, code, y, 4, fs, col, old[i], v)); st["재무상태 4분기 = 연간"] += 1
    for r in rows:
        code, y, q, fs, fld = r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"]
        v = cand(code, y, q, fs, fld)
        if v is None:
            st["원문 후보 없음"] += 1; continue
        tbl = "cash_flow_data" if fld in fn.FLOW_CF else "financial_data"
        if (code, y, tbl) in locks:
            st["잠금"] += 1; hold.append((code, y, q, fs, fld, "잠금")); continue
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        for rid, old in conn.execute(f"SELECT id, {fld} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                                     (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall():
            if old is not None and abs(old - v) <= 0.5:
                continue
            if old is not None and _ofs.scale_jump(old, v):
                st["1,000배 급변 — 보류"] += 1; hold.append((code, y, q, fs, fld, f"1000배 {old}→{v}")); continue
            plan.append((tbl, rid, code, y, q, fs, fld, old, v)); st["정렬"] += 1
    # 10-12: 규모 검사 — 재작성 칸 자체가 단위 오기(1,000배~1,000,000배)인 종목이 있다(032680 2021 등). |새 값| > 10 × 총자산 중앙값이면 보류
    _scale = {}
    def _plausible(code, v):
        if code not in _scale:
            xs = sorted(abs(r[0]) for r in conn.execute("SELECT total_assets FROM financial_data WHERE stock_code=? AND year BETWEEN ? AND ? AND total_assets IS NOT NULL AND total_assets<>0",
                                                        (code, y0 - 1, y1 + 1)).fetchall())
            _scale[code] = xs[len(xs) // 2] if xs else None
        return _scale[code] is None or abs(v) <= 10 * _scale[code]
    kept = []
    for p_ in plan:
        if _plausible(p_[2], p_[8]):
            kept.append(p_)
        else:
            st["규모상 불가능(> 10 × 총자산 중앙값) — 보류"] += 1; hold.append((p_[2], p_[3], p_[4], p_[5], p_[6], f"규모 {p_[7]}→{p_[8]}"))
    plan = kept
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "years": a.years, "src": f.name, "c4_cells": len(rows),
           "stats": dict(st), "plan_rows": len(plan), "hold": len(hold)}
    with open(OUT / f"align_pre_window_hold_{a.years}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["stock_code", "year", "quarter", "fs", "field", "reason"]); w.writerows(hold)
    if a.apply and plan:
        run_id = f"align_raw_{a.years.replace('-', '_')}_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_align_raw_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
                conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(part))}) AND COALESCE(data_source,'') NOT LIKE ?",
                             [TAG] + part + [f"%{TAG}%"])
            for _, rid, code, y, q, fs, fld, old, new in ps:
                conn.execute(f"UPDATE {tbl} SET {fld}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, fld, old, new, "원문 = DB 정렬(OpenDART 단일 경로, fin_norm 규칙 — 사용자 지시 10-11)",
                               "DART 원문(data_raw/dart_fnltt)", run_id) for _, rid, code, y, q, fs, fld, old, new in ps])
        rel = sorted({(p[2], p[3], p[0]) for p in plan} & locked_all)
        for code_, y_, tbl_ in rel:
            conn.execute("UPDATE data_lock SET is_locked=0, unlock_reason=?, unlocked_at=? WHERE stock_code=? AND year=? AND table_name=? AND is_locked=1",
                         (f"원문 > 잠금(§5-10) — OpenDART 원문 규칙값과 달라 해제, {run_id}", now, code_, y_, tbl_))
        res["locks_released"] = rel
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", f"FnGuide 표시 기간 이전 {a.years} 원문 = DB 정렬", len(plan), "OpenDART 단일 경로·fin_norm 규칙(PLAN §8-2)",
                      json.dumps(dict(st), ensure_ascii=False), "", "scripts/review/align_pre_window_to_raw_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                            {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if "sample" not in k}})
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
