#!/usr/bin/env python3
"""PLAN P10-3 — 비12월 결산 외국기업 원화 환산(보류 4종목: 950210 USD·900290 CNY·900120 CNY·950170 JPY) (2026-10-09). 기본 dry-run.

FINANCIAL §2-6 FnGuide 규칙(ECOS 731Y001 매매기준율): 손익 = 기간 평균, 재무상태표·현금흐름 = 기말. 기존 `convert_foreign_currency_20261003.py`는
12월 결산·달력 분기만 다뤄 이 4종목을 보류했다. 여기서는 회계 기간(`fiscal_period.period_end`)으로 환율 기간을 잡는다.
통화 확인: DB 원통화 값 ÷ FnGuide 원화 값 비율이 그 통화 환율의 역수와 맞는지로(900290은 '?' → 비율 1/192 = CNY).
**행 단위 판정**(한 행 안에서 원화·원통화가 섞이지 않게): 그 행(종목·연도·분기·구분)의 FnGuide 값이 있는 칸이 **모두**
  |DB × 환율 − FnGuide| ≤ max(200만원, |FnGuide| × 0.1%)  (환율 소수 둘째 자리·FnGuide 백만원 반올림)
을 만족하면 그 행의 숫자 칸 전부 환산(FnGuide 칸 없는 칸 포함 — 같은 행이 규칙으로 검증됨), 하나라도 어긋나면 보류.
이미 원화인 칸(비율 ≈ 1)은 건드리지 않는다. 적용: 백업·fix_log·data_fix_log·readback·정본 동기화·보고통화 표시 갱신·재측정.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
import fiscal_period  # noqa: E402

FX = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/ecos_fx/731Y001_daily.json")
TARGET = {"950210": "USD", "900290": "CNY", "900120": "CNY", "950170": "JPY100"}
IS_F = ("revenue", "operating_profit", "net_income")
BS_F = ("total_assets", "total_liabilities", "total_equity")
CF_F = ("operating_cf", "investing_cf", "financing_cf", "capex")
CFQ_F = ("operating_cf_q", "investing_cf_q", "financing_cf_q", "capex_q")
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def series():
    raw = json.load(open(FX))
    return {c: pd.Series({pd.Timestamp(t): float(v) for t, v in rows}).sort_index() for c, rows in raw.items()}


def period(code, fy, q):
    """(시작, 끝) 달력 날짜. q=0 연간(12개월), 1~4 분기(3개월)."""
    pe = fiscal_period.period_end(code, fy, 4 if q in (0, 4) else q)
    end = pd.Timestamp(pe + "-01") + pd.offsets.MonthEnd(0)
    start = (end - pd.DateOffset(months=12 if q == 0 else 3)) + pd.Timedelta(days=1)
    return start, end


def rate(s, ccy, code, fy, q, kind):
    a, b = period(code, fy, q)
    x = s[ccy][a:b] if kind == "avg" else s[ccy][:b]
    if not len(x) or (kind == "end" and (b - x.index[-1]).days > 10):
        return None
    v = float(x.mean()) if kind == "avg" else float(x.iloc[-1])
    return v / 100 if ccy == "JPY100" else v


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    S = series()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fg = collections.defaultdict(dict)
    for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, fnguide_value FROM financial_field_verification
                              WHERE stock_code IN ({','.join('?' * len(TARGET))}) AND fnguide_value IS NOT NULL""", list(TARGET)).fetchall():
        fg[(r[0], r[1], r[2], r[3])][r[4]] = float(r[5])
    plan, st, detail = [], collections.Counter(), []
    for tbl, cols in (("financial_data", IS_F + BS_F), ("cash_flow_data", CF_F + CFQ_F)):
        for r in conn.execute(f"""SELECT id, stock_code, year, quarter, is_annual, report_type, {','.join(cols)} FROM {tbl}
                                  WHERE stock_code IN ({','.join('?' * len(TARGET))}) AND year>=2022""", list(TARGET)).fetchall():
            r = tuple(r)
            rid, code, y, q, ia, fs = r[:6]
            vals = dict(zip(cols, r[6:]))
            vq = 0 if ia else q
            ccy = TARGET[code]
            rates = {}
            for c in cols:
                kind = "avg" if c in IS_F else "end"
                rates[c] = rate(S, ccy, code, y, vq, kind)
            ext = fg.get((code, y, vq, fs), {})
            checks = []
            raw_like = 0
            for c, v in vals.items():
                fgv = ext.get(c[:-2] if c.endswith("_q") else c) if (c in IS_F + BS_F or (c in CF_F and vq == 0) or (c in CFQ_F and vq)) else None
                if v in (None, 0) or fgv in (None, 0) or not rates.get(c):
                    continue
                ratio = abs(v / fgv)
                if ratio > 0.3:  # 이미 원화(≈1) — 이 행은 환산 대상 아님 신호
                    checks.append((c, "원화로 보임", ratio))
                    continue
                raw_like += 1
                cand = v * rates[c]
                ok = abs(cand - fgv) <= max(2e6, abs(fgv) * 0.001)
                checks.append((c, "일치" if ok else "불일치", round(cand / fgv, 5)))
            if not checks:
                st[(tbl, "FnGuide 대조 칸 없음 — 보류")] += 1
                continue
            if any(k[1] == "원화로 보임" for k in checks) and raw_like == 0:
                st[(tbl, "이미 원화 행")] += 1
                continue
            if any(k[1] != "일치" for k in checks):
                st[(tbl, "보류: 환산 후 FnGuide와 다른 칸 있음(또는 원화·원통화 혼재)")] += 1
                detail.append((tbl, code, y, vq, fs, checks))
                continue
            st[(tbl, "환산(행 전부 FnGuide 일치)")] += 1
            for c, v in vals.items():
                if v is None or not rates.get(c):
                    continue
                plan.append((tbl, rid, code, y, q, ia, fs, c, v, v * rates[c], rates[c], ccy))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "stats": {f"{k[0]}|{k[1]}": n for k, n in st.items()}, "cells": len(plan),
           "by_code": dict(collections.Counter(p[2] for p in plan)), "hold_detail_sample": detail[:25], "sample": plan[:20]}
    if a.apply and plan:
        run_id = f"fx_nondec_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ups = [p for p in plan if p[0] == tbl]
            if not ups:
                continue
            bk = f"{tbl}_backup_fx_nondec_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ups})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            for _, rid, code, y, q, ia, fs, c, v, nv, rt, ccy in ups:
                conn.execute(f"UPDATE {tbl} SET {c}=?, updated_at=? WHERE id=?", (nv, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if ia else 0, fs, c, v, nv,
                               f"원화 환산({ccy}, {'기간 평균' if c in IS_F else '기말'} {rt:.4f}) — 행의 FnGuide 칸 전부 일치", "ECOS 731Y001", run_id)
                              for _, rid, code, y, q, ia, fs, c, v, nv, rt, ccy in ups])
        for code, ccy in TARGET.items():
            if code in res["by_code"]:
                conn.execute("""UPDATE stock_collection_config SET config_value=?, updated_at=? WHERE stock_code=? AND config_key='fs_quirk:reporting_currency'""",
                             (f"{ccy.replace('100', '')}→KRW 부분 환산({run_id}, FnGuide 일치 행만 — 보류 행은 원통화)", now, code))
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "비12월 결산 외국기업 4종목 원화 환산", len(plan),
                      "ECOS 매매기준율(손익 평균·재무상태/현금흐름 기말, 회계 기간) — 행의 FnGuide 칸 전부 일치할 때만",
                      json.dumps(res["stats"], ensure_ascii=False)[:900], json.dumps(res["by_code"]), "scripts/review/convert_nondec_foreign_20261009.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[7]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[9]) > 0.5]
        res["post_readback"] = {"planned": len(plan), "mismatch": len(bad), "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                    {p[1] for p in plan if p[0] == "cash_flow_data"})
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"fx_nondec_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "cells", "by_code", "post_readback", "field_verification_after")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
