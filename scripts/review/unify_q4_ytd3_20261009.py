#!/usr/bin/env python3
"""D-6 — 흐름 항목 4분기를 '연간 − 3분기 보고서 누적'(② FnGuide 방식)으로 통일 (2026-10-09). 기본 dry-run.

배경: DB 4분기에 ① 연간 − 1~3분기 3개월 합(옛 정본 정의, 재작성 반영 4분기 재계산)과 ② 연간 − 3분기 누적(P5 정밀도의 FnGuide 일치분)이 섞였다.
FnGuide 값이 있는 386칸 중 336칸이 ② (10-09 측정). 외부 검토 조건(10-09):
  - 대상 = 현재 DB가 ①이고 ① ≠ ② 이며 ② 산출 근거가 명확한 칸만 → ②로 전환. **'둘 다 아님'은 섞지 않고 별도 목록.**
  - 손익(financial_data 매출·영업이익·순이익) / 현금흐름 3개월(cash_flow_data *_q) / 현금흐름 누적(Q4 행 누적 칸 = 연간) / 정본 반영 거부를 분리 집계.
  - 재무상태표 4분기 = 연간(이 스크립트는 건드리지 않음), 1~3분기 원문 값 보존(건드리지 않음).
  - 전환 행 data_source에 '+derived_q4_from_ytd3' 표시, 백업·fix_log·data_fix_log, readback, 정본 동기화, 재측정.
② 근거가 명확 = 연간 원장 값이 하나로 정해지고 DART 사업보고서 원문과 200만원 이내, 3분기 보고서 누적 원문 존재.
제외: 보고통화 외화·비12월·단위 오류 종목, data_lock. 현금흐름 CapEx는 3순위라 이번 범위 밖.
dry-run 정본 거부: 바뀐 값으로 게이트(data_write_gate)를 실제 호출해 세고 롤백(커밋 없음).
"""
from __future__ import annotations

import argparse
import collections
import gzip
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402 — 정규화 정의 한 곳(2026-10-10)
raw = fn._extract   # (code, y, q, fs, col) → extract dict
dkey = fn.dart_key  # (field, fs) → extract 키
from financial_rereview_20261002 import extract  # noqa: E402
from data_write_gate import gate_cashflow_row, gate_financial_row  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
TOL = 2e6
FIN = ("revenue", "operating_profit", "net_income")
CF = ("operating_cf", "investing_cf", "financing_cf")
TAG = "+derived_q4_from_ytd3"        # 4분기 3개월 칸(손익·현금흐름 *_q) = 연간 − 3분기 누적
TAG_CUM = "+q4_cum_equals_annual"     # 현금흐름 4분기 누적 칸 = 연간(파생 아님 — 연간값 정합화, D-6과 별개 정밀도 보정)
CUM_RULE = "현금흐름 누적(4분기 누적 = 연간)"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
_c = {}


STALE_RULE = "4분기 재계산(낡은 파생값 — 연간·3분기 누적 원문 확인, 10-10)"


RULE_ONLY_UNTIL = 0


def stale_ok(dv, v2, e, y=None):
    if y is not None and y <= RULE_ONLY_UNTIL and e is None:
        return True  # FnGuide 표시 기간 이전: 규칙값(연간·3분기 누적 원문 확인)으로 — PLAN §8-3
    """'둘 다 아님' 칸 중 재계산 허용: FnGuide가 현재 DB를 지지하면 제외. 빈 칸 / 억 단위 반올림값 / 1% 이내."""
    if e is not None and dv is not None and abs(dv - e) <= TOL:
        return False
    if dv is None:
        return True
    if dv != 0 and abs(dv) % 1e8 == 0:
        return True
    return v2 != 0 and abs(dv / v2 - 1) <= 0.01


def one(vals):
    v = {x for x in vals if x is not None}
    return v.pop() if len(v) == 1 else (None if not v else "모호")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--from-year", type=int, default=2021, help="10-11: 2016~2022(FnGuide 표시 기간 이전)도 OpenDART 단일 경로 규칙으로")
    ap.add_argument("--rule-only-until", type=int, default=0, help="10-11: 이 연도 이하(FnGuide 표시 기간 이전)는 1% 제한 없이 규칙값(②)으로 — OpenDART 단일 경로(사용자 지시)")
    ap.add_argument("--stale", action="store_true", help="10-10: '둘 다 아님' 중 낡은 4분기(①=② 확정값과 1% 이내·억 단위 반올림·빈 칸)만 재계산")
    a = ap.parse_args()
    global RULE_ONLY_UNTIL
    RULE_ONLY_UNTIL = a.rule_only_until
    conn = connect_primary_db(timeout=900)  # dry-run도 쓰기 연결(게이트 시뮬레이션) — 끝에 rollback
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN ('fs_quirk:reporting_currency','fs_quirk:dart_unit_error','fs_quirk:unit_scale_fixed')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    fg = {tuple(r[:4]): r[4] for r in conn.execute("SELECT stock_code, year, report_type, field, fnguide_value FROM financial_field_verification WHERE quarter=4 AND fnguide_value IS NOT NULL").fetchall()}
    # 10-13: 값을 일부러 비운 4분기 칸(같은 재무제표 연간 원문 없음·원문 모순)은 재계산이 다시 채우지 못하게 한다 — 10-13 실측: 이 스크립트가 2021~22의 93칸을 도로 채웠다
    blank_exc = {tuple(r[:4]) for r in conn.execute("SELECT stock_code, year, report_type, field FROM financial_field_exception "
                                                     "WHERE quarter=4 AND reason_code IN ('NO_SAME_FS_ANNUAL','RAW_CONTRADICTION')").fetchall()}
    st, plan, neither = collections.Counter(), [], []
    for tbl, fields in (("financial_data", FIN), ("cash_flow_data", CF)):
        cols = list(fields) + ([f + "_q" for f in fields] if tbl == "cash_flow_data" else [])
        g = collections.defaultdict(list)
        for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, report_type, {','.join(cols)} FROM {tbl} WHERE year BETWEEN {a.from_year} AND 2026").fetchall():
            g[(r[1], r[2], r[5])].append(tuple(r))
        ci = {c: i + 6 for i, c in enumerate(cols)}
        area = "손익" if tbl == "financial_data" else "현금흐름"
        for (code, y, fs), rows in g.items():
            q4 = [r for r in rows if r[3] == 4 and not r[4]]
            if not q4:
                continue
            if code in excl:
                st[(area, "제외 종목")] += len(fields); continue
            if (code, y, tbl) in locks:
                st[(area, "잠금")] += len(fields); continue
            if tbl == "cash_flow_data" and y <= RULE_ONLY_UNTIL:
                st[(area, "이전 기간 현금흐름 — align --cf-chain 규칙 하나만 사용(10-12)")] += len(fields); continue
            rfs = fn.effective_fs(code, y, fs) if y <= RULE_ONLY_UNTIL else fs  # 10-12: 이전 기간 연결 미작성 회사의 'CFS' 행 = 별도 원문
            for f in fields:
                if (code, y, fs, f) in blank_exc:
                    st[(area, "건너뜀: 값을 비우기로 한 4분기 칸(NO_SAME_FS_ANNUAL·RAW_CONTRADICTION)")] += 1; continue
                A = one(r[ci[f]] for r in rows if r[4])
                ra = raw(code, y, 0, rfs, "thstrm_amount").get(dkey(f, rfs))
                if y <= RULE_ONLY_UNTIL and A not in (None, "모호") and ra is not None and abs(A - ra) > TOL:
                    rr = fn.latest_annual(code, y, rfs, f)[0]  # 10-12: 이전 기간 연간이 재작성값(다음 해 사업보고서 전기 칸)이면 그것도 원문(§2-5)
                    if rr is not None and abs(A - rr) <= TOL:
                        ra = rr
                if not (A not in (None, "모호") and ra is None and y <= RULE_ONLY_UNTIL) and (A in (None, "모호") or ra is None or abs(A - ra) > TOL):  # 10-11: 이전 기간은 연간 원문 파일이 없으면 원장 연간(DART jsonl 적용값) 사용
                    st[(area, "보류: 연간 원장 모호·원문 없음·원문과 다름")] += 1; continue
                if tbl == "financial_data":
                    qs = [one(r[ci[f]] for r in rows if r[3] == q and not r[4]) for q in (1, 2, 3)]
                    y3 = raw(code, y, 3, rfs, "thstrm_add_amount").get(dkey(f, rfs))
                    col = f
                else:
                    qs = [one(r[ci[f + "_q"]] for r in rows if r[3] == q and not r[4]) for q in (1, 2, 3)]
                    y3 = raw(code, y, 3, rfs, "thstrm_amount").get(dkey(f, rfs))  # 현금흐름 원문 = 누적
                    col = f + "_q"
                    for r in q4:  # 현금흐름 누적 칸: 4분기 누적 = 연간
                        cv = r[ci[f]]
                        if cv is None or abs(cv - A) > 0.5:
                            plan.append((tbl, r[0], code, y, fs, f, cv, A, CUM_RULE))
                            st[("현금흐름 누적", "4분기 누적 ≠ 연간 → 연간으로")] += 1
                        else:
                            st[("현금흐름 누적", "이미 연간과 같음")] += 1
                if y3 is None and tbl == "financial_data" and y <= RULE_ONLY_UNTIL and all(x not in (None, "모호") for x in qs):
                    y3 = sum(qs)  # 10-11: FnGuide 표시 기간 이전·3분기 누적 원문 없음 → 1~3분기 3개월 합(원래 공시 누적과 같음, 재작성 정보 없음) — OpenDART 단일 경로 대체
                    st[(area, "대체: 3분기 누적 없음 → 1~3분기 합(이전 기간)")] += 1
                if y3 is None:
                    st[(area, "보류: 3분기 누적 원문 없음")] += 1; continue
                v2 = A - y3
                if f == "revenue" and v2 < -TOL:
                    st[(area, "보류: 4분기 매출 음수(입력 모순 — 1~3분기 합 > 연간)")] += 1; continue
                v1 = A - sum(qs) if all(x not in (None, "모호") for x in qs) else None
                for r in q4:
                    dv = r[ci[col]]
                    lab = area if tbl == "financial_data" else "현금흐름 3개월"
                    if v1 is not None and abs(v1 - v2) <= TOL:
                        st[(lab, "① = ② (변화 없음)")] += 1
                        if dv is None or abs(dv - v2) > TOL:
                            e = fg.get((code, y, fs, f))
                            if a.stale and stale_ok(dv, v2, e, y):
                                plan.append((tbl, r[0], code, y, fs, col, dv, v2, STALE_RULE))
                                st[(lab, "낡은 4분기 재계산(①=②)")] += 1
                                continue
                            neither.append((tbl, r[0], code, y, fs, col, dv, v1, v2, e, "①=②인데 DB 다름"))
                            st[(lab, "둘 다 아님(별도 목록)")] += 1
                        continue
                    if dv is not None and abs(dv - v2) <= TOL:
                        st[(lab, "이미 ②")] += 1; continue
                    if dv is not None and v1 is not None and abs(dv - v1) <= TOL:
                        e = fg.get((code, y, fs, f))
                        st[(lab, "① → ② 전환 대상")] += 1
                        if e is not None:
                            st[(lab, "  전환 대상 중 FnGuide 4분기 있음: " + ("② = FnGuide" if abs(e - v2) <= TOL else "② ≠ FnGuide"))] += 1
                        plan.append((tbl, r[0], code, y, fs, col, dv, v2, f"{lab} ① → ② (연간 {A:,.0f} − 3분기 누적 {y3:,.0f})"))
                        continue
                    e = fg.get((code, y, fs, f))
                    if a.stale and stale_ok(dv, v2, e, y):
                        plan.append((tbl, r[0], code, y, fs, col, dv, v2, STALE_RULE))
                        st[(lab, "낡은 4분기 재계산(②)")] += 1
                        continue
                    neither.append((tbl, r[0], code, y, fs, col, dv, v1, v2, e, "DB가 ①도 ②도 아님" if dv is not None else "DB 빈 칸"))
                    st[(lab, "둘 다 아님(별도 목록)")] += 1
    # 정본 게이트 시뮬레이션(바뀐 값으로) — 행 단위
    newv = collections.defaultdict(dict)
    for tbl, rid, code, y, fs, col, old, new, why in plan:
        newv[(tbl, rid)][col] = new
    refused = collections.Counter()
    sample_ref = []
    for (tbl, rid), ch in newv.items():
        colsx = _ofs.FD_COLS if tbl == "financial_data" else _ofs.CFC_COLS
        r = dict(conn.execute(f"SELECT stock_code, year, quarter, is_annual, report_type, data_source, {','.join(colsx)} FROM {tbl} WHERE id=?", (rid,)).fetchone())
        r.update(ch)
        ok, _, reason = (gate_financial_row if tbl == "financial_data" else gate_cashflow_row)(conn, r)
        if not ok:
            refused[(tbl, reason)] += 1
            if len(sample_ref) < 20:
                sample_ref.append((tbl, rid, r["stock_code"], r["year"], reason))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "stats": {f"{k[0]}|{k[1]}": v for k, v in sorted(st.items())}, "plan_cells": len(plan), "plan_rows": len(newv),
           "plan_by": dict(collections.Counter(p[8].split(" (")[0] for p in plan)),
           "gate_refused_sim": {f"{k[0]}|{k[1]}": v for k, v in refused.items()}, "gate_refused_sample": sample_ref, "neither": len(neither)}
    sfx = "20261010_stale" if a.stale else "20261009"
    pd.DataFrame(neither, columns=["table", "id", "stock_code", "year", "fs", "column", "db", "v1_sum3m", "v2_ytd3", "fnguide_q4", "reason"]).to_csv(
        OUT / f"q4_unify_neither_{sfx}.csv", index=False)
    pd.DataFrame(plan, columns=["table", "id", "stock_code", "year", "fs", "column", "old", "new", "rule"]).to_csv(OUT / f"q4_unify_plan_{sfx}.csv", index=False)
    if not a.apply:
        conn.rollback()
    elif plan:
        conn.rollback()  # 시뮬레이션 로그 제거 후 실제 적용
        run_id = f"q4_unify_ytd3_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_q4_unify_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for tag, rows_ in ((TAG, {p[1] for p in ps if p[8] != CUM_RULE}), (TAG_CUM, {p[1] for p in ps if p[8] == CUM_RULE})):
                for rid in rows_:  # 행에 해당 규칙 칸이 있을 때만 그 태그
                    conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id=? AND COALESCE(data_source,'') NOT LIKE ?", (tag, rid, f"%{tag}%"))
            for _, rid, code, y, fs, col, old, new, why in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,4,0,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, fs, col, old, new,
                               ("현금흐름 4분기 누적 = 연간 정밀도 보정(D-6과 별개)" if why == CUM_RULE else "D-6 4분기 3개월 통일: " + why),
                               "DART 사업보고서(연간)" if why == CUM_RULE else "DART 사업보고서·3분기 보고서 누적 원문", run_id)
                              for _, rid, code, y, fs, col, old, new, why in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        n_cum = sum(1 for p in plan if p[8] == CUM_RULE)
        for scope, n, rule in (("D-6 4분기 3개월 통일(손익·현금흐름 *_q, ① → ②, 둘 다 아님 제외)", len(plan) - n_cum,
                                "4분기 3개월 = 연간 − 3분기 보고서 누적(FnGuide 방식), 출처 표시 +derived_q4_from_ytd3"),
                               ("현금흐름 4분기 누적 = 연간 정밀도 보정(D-6과 별개)", n_cum,
                                "4분기 누적 칸 = 연간 원장 값(DART 사업보고서 원문 확인), 출처 표시 +q4_cum_equals_annual")):
            if n:
                conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                             (now, "financial_data+cash_flow_data", scope, n, rule, json.dumps(res["stats"], ensure_ascii=False)[:900],
                              json.dumps(res["plan_by"], ensure_ascii=False), "scripts/review/unify_q4_ytd3_20261009.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[5]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[7]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                          {p[1] for p in plan if p[0] == "cash_flow_data"})})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = OUT / f"q4_unify_ytd3_{'stale_20261010' if a.stale else '20261009'}_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "plan_cells", "plan_rows", "plan_by", "gate_refused_sim", "neither", "post_readback", "field_verification_after")},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
