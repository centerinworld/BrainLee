#!/usr/bin/env python3
"""PLAN_99_99_NUMERIC P5 — 현행 검증 '외부 2곳 일치·DB 다름'(FnGuide = 네이버 ≠ DB) 칸 정리 (2026-10-09). 기본 dry-run.

원칙 0: 외부 두 곳이 같고 DB만 다르면 먼저 우리 값(파싱·파생·재작성 미반영)을 의심한다. 그러나 FnGuide 값을 그대로 복사하지 않는다.
DART 원문(`dart_cf_full.jsonl`, 재수집 원문)에서 **FnGuide와 일치하는 후보**를 찾을 때만 그 원문값으로 바꾼다.
후보(칸마다):
  - 연간: 그 해 사업보고서 당기 값 / 다음 해 사업보고서 전기 칸(재작성값, `vals_prev`)
  - Q1~Q3: 그 분기 보고서 값(손익 3개월)
  - Q4: 연간(당기 또는 재작성) − 3분기 보고서 누적(D-6, 10-09 — 이전 '− (Q1+Q2+Q3 원문)' 후보 폐기)
  일치 기준: |후보 − FnGuide| ≤ 200만원(FnGuide 백만원 표시 반올림 + Q4 파생의 반올림 누적). 차이 크기로 닫지 않는다 — 일치 후보가 없으면 값 유지·원인 기록.
제외: 보고통화 외화·비12월 결산·단위 오류 종목, 재작성값·원화 환산 보호 칸, data_lock, 1,000배 급변.
적용(--apply): financial_data 백업(`financial_data_backup_p5_20261009`)·financial_fix_log·data_fix_log, readback, 정본 동기화(apply_ofs_extra의 canonical_sync), build_field_verification 재측정.
사용: fix_external_agree_db_diff_20261009.py [--apply]
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_full.jsonl"
RAW_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from financial_rereview_20261002 import extract  # noqa: E402
KEY = {"revenue": "revenue", "operating_profit": "operating_profit"}
# --precision(2026-10-09): '확정'이지만 |DB − FnGuide| > 200만원인 칸(억 단위 반올림값 등 — 검증 허용오차 0.5%가 가림)까지 확장
KEY_P = {**KEY, "total_assets": "total_assets", "total_liabilities": "total_liabilities"}
BS = ("total_assets", "total_liabilities", "total_equity")
SRCS = [SRC, ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_2021_2022.jsonl"]


def key_of(f, fs):
    if f == "net_income":
        return "ni_parent" if fs == "CFS" else "ni_total"
    if f == "total_equity":
        return "equity_parent" if fs == "CFS" else "equity_total"
    return KEY_P[f]
TOL = 2e6
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def load_raw(codes):
    t = {}
    for src in SRCS:
        if not src.exists():
            continue
        for line in open(src):
            d = json.loads(line)
            if d.get("ok") and d.get("fs") and d["code"] in codes:
                t.setdefault((d["code"], d["year"], d["q"], d["fs"]), d)  # 첫 파일(2023+ 재수집) 우선
    return t


_ytd_cache = {}


def ytd(code, y, q, fs, k):
    """원문 계정 파일의 누적(thstrm_add_amount) 값 — 분기 보고서 누적 칸(앞 분기 재작성 반영)."""
    key = (code, y, q, fs)
    if key not in _ytd_cache:
        p = RAW_DIR / code / f"{y}_{q}_{fs}.json.gz"
        _ytd_cache[key] = extract(json.loads(__import__("gzip").decompress(p.read_bytes())), "thstrm_add_amount") if p.exists() else {}
    return _ytd_cache[key].get(k)


def candidates(raw, code, y, q, fs, f):
    k = key_of(f, fs)
    g = lambda yy, qq: raw.get((code, yy, qq, fs))  # noqa: E731
    out = []
    if q == 0:
        a = g(y, 0)
        if a and (a.get("vals") or {}).get(k) is not None:
            out.append(("연간 당기(그 해 사업보고서)", a["vals"][k]))
        n = g(y + 1, 0)
        if n and (n.get("vals_prev") or {}).get(k) is not None:
            out.append(("연간 재작성(다음 해 사업보고서 전기 칸)", n["vals_prev"][k]))
    elif q in (1, 2, 3):
        x = g(y, q)
        if x and (x.get("vals") or {}).get(k) is not None:
            out.append((f"{q}분기 보고서 값", x["vals"][k]))
        if q in (2, 3) and f not in BS:  # 누적 차분(흐름 항목만): 이번 보고서 누적 − 직전 보고서 누적(Q1 보고서는 누적 칸이 3개월 값)
            cur = ytd(code, y, q, fs, k)
            prev = ytd(code, y, q - 1, fs, k) if q == 3 else (ytd(code, y, 1, fs, k) or ((g(y, 1) or {}).get("vals") or {}).get(k))
            if cur is not None and prev is not None:
                out.append((f"{q}분기 = 이번 누적 − 직전 누적(원문 누적 칸)", cur - prev))
    elif q == 4 and f in BS:  # 재무상태표 4분기 = 기말 = 연간 값
        out += [(f"Q4(재무상태) = {label}", av) for label, av in candidates(raw, code, y, 0, fs, f)]
    elif q == 4:
        y3 = ytd(code, y, 3, fs, k)
        if y3 is not None:
            for label, av in candidates(raw, code, y, 0, fs, f):
                out.append((f"Q4 = {label} − 3분기 보고서 누적(FnGuide 방식)", av - y3))
        # 옛 후보 'Q4 = 연간 − (Q1+Q2+Q3 원문)'은 D-6(2026-10-09)으로 폐기 — FnGuide와 맞더라도 4분기 정의가 섞임
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--precision", action="store_true", help="'확정'이지만 |DB−FnGuide|>200만원인 손익·재무상태 칸")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    if a.precision:
        cells = [tuple(r) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value
                    FROM financial_field_verification WHERE status LIKE '확정%%' AND db_value IS NOT NULL AND fnguide_value IS NOT NULL
                    AND ABS(db_value - fnguide_value) > {TOL} AND field IN ({','.join("'" + f + "'" for f in list(KEY_P) + ['net_income', 'total_equity'])})""").fetchall()]
    else:
        cells = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value
                                               FROM financial_field_verification WHERE cause LIKE '외부 2곳%%'""").fetchall()]
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='financial_data'").fetchall()}
    protected = {(r[0], r[1]) for r in conn.execute(
        "SELECT row_id, field_name FROM financial_fix_log WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall()}
    raw = load_raw({c[0] for c in cells})
    plan, keep, st = [], [], collections.Counter()
    for code, y, q, fs, f, dv, fg, nv in cells:
        if code in excl:
            st["제외 종목(외화·비12월·단위)"] += 1; keep.append((code, y, q, f, "제외 종목")); continue
        if (code, y) in locks:
            st["잠금"] += 1; keep.append((code, y, q, f, "data_lock")); continue
        # 연간은 원장 두 벌(quarter 0·4) 가능 — 첫 행으로 판정하되, 정정은 아래에서 같은 기간 행 전부(값 다른 것만)
        row = conn.execute(f"""SELECT id, {f} FROM financial_data WHERE stock_code=? AND year=? AND report_type=?
                               AND {'is_annual' if q == 0 else 'NOT is_annual AND quarter=?'} ORDER BY id LIMIT 1""",
                           (code, y, fs) if q == 0 else (code, y, fs, q)).fetchone()
        if not row:
            st["원장 행 없음"] += 1; keep.append((code, y, q, f, "원장 행 없음")); continue
        cands = candidates(raw, code, y, q, fs, f)
        if not cands:
            st["원문 없음"] += 1; keep.append((code, y, q, f, "DART 원문 없음(재수집 대상)")); continue
        hit = [(lab, v) for lab, v in cands if abs(v - fg) <= TOL]
        if not hit:
            st["원문 후보가 FnGuide와 불일치"] += 1
            keep.append((code, y, q, f, "원문 후보 " + "; ".join(f"{lab}={v:,.0f}" for lab, v in cands) + f" ≠ FnGuide {fg:,.0f}"))
            continue
        lab, newv = hit[0]
        if (row[0], f) in protected:
            st["보호 칸"] += 1; keep.append((code, y, q, f, "보호 칸")); continue
        if _ofs.scale_jump(row[1], newv):
            st["1,000배 급변"] += 1; keep.append((code, y, q, f, "1,000배 급변")); continue
        st["정정(" + lab.split("(")[0].split(" =")[0] + ")"] += 1
        for rr in conn.execute(f"""SELECT id, {f} FROM financial_data WHERE stock_code=? AND year=? AND report_type=?
                                   AND {'is_annual' if q == 0 else 'NOT is_annual AND quarter=?'} ORDER BY id""",
                               (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall():
            if rr[1] is not None and abs(float(rr[1]) - newv) <= 0.5:
                continue  # no-op 제외
            plan.append((rr[0], code, y, q, fs, f, rr[1], newv, f"P5{'-정밀도' if a.precision else ''}: {lab} = FnGuide" + ("(확정이었으나 200만원 초과 차이)" if a.precision else " = 네이버(DB만 다름)")))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "cells": len(cells),
           "stats": dict(st), "fix": len(plan), "keep": len(keep), "fix_sample": plan[:40], "keep_list": keep}
    if a.apply and plan:
        run_id = f"p5_{'precision' if a.precision else 'external_agree'}_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "financial_data_backup_p5_20261009"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
        ids = sorted({p[0] for p in plan})
        conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
        for rid, code, y, q, fs, f, old, new, rule in plan:
            conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute(f"UPDATE financial_data SET data_source=COALESCE(data_source,'')||'+p5_20261009' WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
        conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if q == 0 else 0, fs, f, old, new, rule, "DART fnlttSinglAcntAll 원문(dart_cf_full.jsonl)", run_id)
                          for rid, code, y, q, fs, f, old, new, rule in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data", "'확정'이나 DB−FnGuide 200만원 초과 칸(정밀도)" if a.precision else "현행 검증 '외부 2곳 일치·DB 다름' 칸", len(plan),
                      "FnGuide = 네이버 ≠ DB인 칸 중 DART 원문 후보(당기·재작성·Q4 파생)가 FnGuide와 200만원 이내 일치할 때만 원문값으로",
                      json.dumps(dict(st), ensure_ascii=False)[:900], "DART 원문값", "scripts/review/fix_external_agree_db_diff_20261009.py", run_id))
        conn.commit()
        bad = []
        for rid, code, y, q, fs, f, old, new, rule in plan:
            v = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (rid,)).fetchone()[0]
            if v is None or abs(float(v) - new) > 0.5:
                bad.append((rid, f, v, new))
        res["post_readback"] = {"planned": len(plan), "mismatch": len(bad), "sample": bad[:10], "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, set(ids), set())
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
        print("readback", res["post_readback"], "정본", {k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")},
              "재측정", res["field_verification_after"], flush=True)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"p5_{'precision' if a.precision else 'external_agree'}_20261009_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")  # 기존 적용 결과 보존(L24)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "cells", "stats", "fix", "keep")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
