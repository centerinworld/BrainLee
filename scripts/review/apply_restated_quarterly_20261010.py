#!/usr/bin/env python3
"""분기 재작성값 반영 — FINANCIAL §2-5(표시값 = 최신 재작성값, 최초값 보존)의 분기판 (2026-10-10). 기본 dry-run.

발견(10-10): FnGuide 분기 합 = 연간인데 DB와 분기 배분만 다른 128묶음(대부분 2025 현금흐름) 중 차이 칸 169개 가운데 168개가
'다음 해 같은 분기 보고서의 전년 비교 칸'(frmtrm_q_amount — 재작성된 전년 값)과 FnGuide가 일치. 우리 DB는 그 해 보고서 원본을 유지.
재작성 후보(Y년 q분기, Y+1년 q분기 보고서 원문 data_raw/dart_fnltt):
  손익(매출·영업이익·순이익[연결 지배, 별도 전체]): 3개월 = frmtrm_q_amount  → financial_data 분기 칸
  현금흐름(영업·투자·재무): 원문이 누적 → 누적 = frmtrm_q_amount, 3개월 = 이번 누적 − 직전 분기 재작성 누적(1분기는 누적 = 3개월)
                             → cash_flow_data 누적 칸 + *_q 칸 (직전 분기 재작성 누적이 없으면 3개월 칸 건너뜀)
반영 조건(칸마다): 재작성 3개월 값이 **그 칸 FnGuide 값과 200만원 이내** 이고 DB와 다를 때만. FnGuide 없으면 보류(후보 목록).
제외: 외화·비12월·단위 오류 종목, data_lock. 4분기는 대상 아님(연간 재작성은 apply_restated_annual, 4분기 = 연간 − 3분기 누적).
3분기: Y+1년 3분기 보고서가 나오면(매년 11월) 같은 스크립트가 처리 — 야간 파이프라인 등록 권장.
최초값 보존: 백업 표·fix_log(old_value)·financial_facts_pit(as_reported). 적용: readback·정본 동기화·재측정.
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
raw = fn._extract
dkey = fn.dart_key
from financial_rereview_20261002 import extract  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
TOL = 2e6
FIN = ("revenue", "operating_profit", "net_income")
CF = ("operating_cf", "investing_cf", "financing_cf")
TAG = "+restated_q_frmtrm"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
_c = {}


_judge = None


def seibro_proxy(code, y, q, fs, f, ver_all):
    """FnGuide 분기 값이 검증표에 없을 때 SEIBro 대리값(같은 묶음 FnGuide 값 — 연간·다른 분기 — 2칸 이상과 전부 일치할 때만, 사용자 10-09 '빈 분기 확인용')."""
    global _judge
    if _judge is None:
        sp = importlib.util.spec_from_file_location("judge_q", ROOT / "scripts" / "review" / "judge_fnguide_quarter_inconsistent_20261009.py")
        _judge = importlib.util.module_from_spec(sp); sp.loader.exec_module(_judge)
    sb = _judge.seibro(code, y, fs, f)
    have = {qq: ver_all[(code, y, qq, fs, f)] for qq in (0, 1, 2, 3, 4) if (code, y, qq, fs, f) in ver_all}
    common = [qq for qq in have if qq in sb]
    if q in sb and len(common) >= 2 and all(abs(sb[qq] - have[qq]) <= 1.5e6 for qq in common):
        return sb[q]
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--min-year", type=int, default=2021)
    ap.add_argument("--seibro", action="store_true", help="현금흐름 1~3분기 FnGuide 빈 칸을 SEIBro 대리값으로 확인(묶음 대조 일치 시)"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN ('fs_quirk:reporting_currency','fs_quirk:dart_unit_error','fs_quirk:unit_scale_fixed')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    ver = {tuple(r[:5]): r[5] for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, fnguide_value FROM financial_field_verification
        WHERE quarter IN (1,2,3) AND fnguide_value IS NOT NULL AND year>=?""", (a.min_year,)).fetchall()}
    ver_all = {tuple(r[:5]): r[5] for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, fnguide_value FROM financial_field_verification
        WHERE fnguide_value IS NOT NULL AND year>=?""", (a.min_year,)).fetchall()}
    # 대상 키: FnGuide 분기 값이 있는 칸 + (현금흐름) 같은 해 다른 분기에 FnGuide가 있는 1~3분기(SEIBro 대리 확인)
    keys = sorted({k[:4] for k in ver} | {(k[0], k[1], qq, k[3]) for k in ver_all if k[2] in (1, 2, 3, 4) and k[4] in CF for qq in (1, 2, 3)})
    st, plan, hold = collections.Counter(), [], []
    for code, y, q, fs in keys:
        if code in excl:
            continue
        if not any((RAW / code / f"{y + 1}_{qq}_{fs}.json.gz").exists() for qq in (q, q - 1) if qq >= 1):
            st["다음 해 같은 분기 보고서 원문 없음"] += 1
            continue
        for tbl, fields in (("financial_data", FIN), ("cash_flow_data", CF)):
            if (code, y, tbl) in locks:
                continue
            rows = conn.execute(f"""SELECT id, {','.join(fields)}{''.join(',' + f + '_q' for f in fields) if tbl == 'cash_flow_data' else ''}
                                    FROM {tbl} WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND NOT is_annual""", (code, y, q, fs)).fetchall()
            if not rows:
                continue
            for f in fields:
                fg = ver.get((code, y, q, fs, f))
                fg_src = "FnGuide"
                if fg is None and tbl == "cash_flow_data" and a.seibro:
                    fg, fg_src = seibro_proxy(code, y, q, fs, f, ver_all), "SEIBro(FnGuide 대리, 묶음 대조 일치)"
                if fg is None:
                    continue
                k = dkey(f, fs)
                if tbl == "financial_data":
                    r3 = raw(code, y + 1, q, fs, "frmtrm_q_amount").get(k)
                    cum = None
                else:
                    cum, _s = fn.latest_cf_cum(code, y, q, fs, f); rc = _s == "restated"
                    prev, rp = (0.0, False) if q == 1 else (lambda t: (t[0], t[1] == "restated"))(fn.latest_cf_cum(code, y, q - 1, fs, f))
                    r3 = (cum - prev) if (cum is not None and prev is not None) else None
                    if not (rc or rp):
                        r3 = None  # 재작성 원문이 관여하지 않으면 이 스크립트 대상 아님
                if r3 is None:
                    st[f"{tbl[:3]} 재작성 값 없음"] += 1
                    continue
                for row in rows:
                    rd = dict(zip(["id"] + list(fields) + ([x + "_q" for x in fields] if tbl == "cash_flow_data" else []), row))
                    dv = rd[f + "_q"] if tbl == "cash_flow_data" else rd[f]
                    if dv is not None and abs(dv - r3) <= TOL:
                        st[f"{tbl[:3]} 이미 재작성 값"] += 1
                        continue
                    if abs(r3 - fg) > TOL:
                        st[f"{tbl[:3]} 재작성 ≠ FnGuide — 보류"] += 1
                        hold.append((tbl, rd["id"], code, y, q, fs, f, dv, r3, fg))
                        continue
                    lab = (f"{y + 1}년 {q}분기 보고서 전년 비교 칸(재작성)" if tbl == "financial_data" else "현금흐름 누적 최신판(다음 해 보고서 전년 칸 우선) 차분") + f" = {fg_src}"
                    if tbl == "financial_data":
                        plan.append((tbl, rd["id"], code, y, q, fs, f, rd[f], r3, lab))
                    else:
                        if cum is not None and (rd[f] is None or abs(rd[f] - cum) > 0.5):
                            plan.append((tbl, rd["id"], code, y, q, fs, f, rd[f], cum, lab + "(누적)"))
                        plan.append((tbl, rd["id"], code, y, q, fs, f + "_q", rd[f + "_q"], r3, lab + "(3개월)"))
                    st[f"{tbl[:3]} 재작성 반영 대상(칸)"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st),
           "plan_cells": len(plan), "plan_by": dict(collections.Counter((p[0], p[3], p[4]) for p in plan).most_common(20)) if plan else {}, "hold": len(hold)}
    res["plan_by"] = {f"{k[0]}|{k[1]}Q{k[2]}": v for k, v in collections.Counter((p[0], p[3], p[4]) for p in plan).items()}
    pd.DataFrame(plan, columns=["table", "id", "stock_code", "year", "quarter", "fs", "column", "old", "new", "rule"]).to_csv(OUT / "restated_quarterly_plan_20261010.csv", index=False)
    pd.DataFrame(hold, columns=["table", "id", "stock_code", "year", "quarter", "fs", "field", "db", "restated", "fnguide"]).to_csv(OUT / "restated_quarterly_hold_20261010.csv", index=False)
    if a.apply and plan:
        run_id = f"restated_quarterly_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_restated_q_20261010"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
                conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(part))}) AND COALESCE(data_source,'') NOT LIKE ?",
                             [TAG] + part + [f"%{TAG}%"])
            for _, rid, code, y, q, fs, col, old, new, lab in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, fs, col, old, new, "분기 재작성값 반영(§2-5) — " + lab, "DART 다음 해 같은 분기 보고서 frmtrm_q_amount", run_id)
                              for _, rid, code, y, q, fs, col, old, new, lab in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "분기 재작성값(다음 해 같은 분기 보고서 전년 비교 칸) — FnGuide 일치분만", len(plan),
                      "표시값 = 최신 재작성값(§2-5 분기판), 최초값은 백업·fix_log·PIT 보존", json.dumps(dict(st), ensure_ascii=False)[:900],
                      json.dumps(res["plan_by"], ensure_ascii=False)[:900], "scripts/review/apply_restated_quarterly_20261010.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                          {p[1] for p in plan if p[0] == "cash_flow_data"})})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = OUT / f"restated_quarterly_20261010_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "plan_cells", "plan_by", "hold", "post_readback", "field_verification_after")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
