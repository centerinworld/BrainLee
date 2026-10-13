#!/usr/bin/env python3
"""FnGuide 분기 내부 불일치 판정 — 'FnGuide도 틀릴 수 있다' 전제(사용자 2026-10-09, 008930 한미사이언스 사례). 기본 dry-run.

대상: financial_field_verification '원인 조사'인 분기(1~4) 흐름 칸 — 매출·영업이익·순이익(연결=지배, 별도=전체)·영업/투자/재무 현금흐름.
판정(종목-연도-구분-필드 묶음, 모두 만족해야 'FnGuide 틀림·DART 맞음'):
  ① DART 내부 검산: 1~3분기 3개월 값(원문)의 합 = 3분기 보고서 누적 칸(±200만원)  [현금흐름은 원문이 누적이라 3분기 누적 자체를 사용]
     연간(사업보고서) 원문 존재, 4분기 = 연간 − 3분기 누적
  ② 그 칸의 DB 값 = 그 DART 값(±200만원)  — DB가 원문과 다르면 이 규칙으로 닫지 않음(우리 오류 가능)
  ③ 연간: DB = FnGuide(±200만원)
  ④ FnGuide 4개 분기 합 ≠ FnGuide 연간: 차이 > max(3억, 연간 0.3%)  (1·2분기 일부가 억 단위 화면값이라 반올림 여유를 크게 둠)
  ⑤ 그 칸의 FnGuide 값 ≠ DART 값(그래서 '원인 조사'인 칸)
FnGuide 분기 값: 검증표(wcomp 원문) 우선, 없으면 financial_source_snapshot(fnguide, 최신 수집) — 순이익·현금흐름은 기준 혼재 위험이라 검증표 값만.
--seibro(10-09 사용자 지시): 그래도 빠진 분기는 SEIBro 공개 조회(로그인 불필요, cf_triple_validator와 같은 요청)로 채운다.
  SEIBro는 FnGuide와 같은 공급원(008930 분기 매출 완전 동일)이라 **독립 출처가 아니며 자체 오류도 있음**(008930 영업이익 연간 570억 vs 실제 1,387억)
  → 같은 묶음에서 FnGuide 값이 있는 칸(연간 포함, 2칸 이상)이 SEIBro와 전부 일치(±150만원, 둘 다 백만원 표시)할 때만 빠진 분기의 FnGuide 대리값으로 쓴다.
  SEIBro 열: TO_YEAR=Y일 때 A1 연간, A2 4분기, A3 3분기, A4 2분기, A5 1분기(3개월). 원문은 data_raw/seibro/에 저장.
적용: financial_field_exception reason_code='FNGUIDE_QUARTER_INCONSISTENT'(근거 수치 기록, 분모 유지) + fs_quirk:fnguide_quarter_inconsistent + data_fix_log, 재측정.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402 — 정규화 정의 한 곳(2026-10-10)
raw = fn._extract   # (code, y, q, fs, col) → extract dict
dkey = fn.dart_key  # (field, fs) → extract 키
from financial_rereview_20261002 import extract  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
FLOW_IS = ("revenue", "operating_profit", "net_income")
FLOW_CF = ("operating_cf", "investing_cf", "financing_cf")
TOL = 2e6
_c = {}
SEIBRO_RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/seibro")
SB_CODE = {"revenue": "1210000", "operating_profit": "1211430", "ni_total": "1212450", "ni_parent": "1230000",
           "operating_cf": "1390000", "investing_cf": "1390160", "financing_cf": "1390370"}
_sb = {}


def seibro(code, y, fs, f):
    """{0: 연간, 1..4: 분기} 원 단위, 없으면 {}"""
    import re
    import time
    sys.path.insert(0, str(ROOT))
    from collectors.cf_triple_validator import _seibro_session, _post, lookup_custno
    act = "cashFlowTableList" if f in FLOW_CF else "lsgnInvcList"
    key = (code, y, fs, act)
    if key not in _sb:
        SEIBRO_RAW.mkdir(parents=True, exist_ok=True)
        p = SEIBRO_RAW / f"{code}_{y}_{fs}_{act}.xml"
        if p.exists():
            t = p.read_text()
        else:
            if "s" not in _sb:
                _sb["s"] = _seibro_session()
            cust = lookup_custno(_sb["s"], code)
            if not cust:
                _sb[key] = {}
                return {}
            t = _post(_sb["s"], f'<reqParam action="{act}" task="ksd.safe.bip.cnts.Company.process.EntrFnafInfoPTask"><ISSUCO_CUSTNO value="{cust}"/>'
                                f'<UNIT value="1000000"/><TO_YEAR value="{y}"/><TYPE value="{"연결" if fs == "CFS" else "별도"}"/><FROM_YEAR value="{y - 3}"/></reqParam>')
            time.sleep(0.3)
            if "<result>" in t:
                p.write_text(t)
        rows = {}
        for r in re.findall(r"<result>(.*?)</result>", t, re.S):
            d = dict(re.findall(r'<(\w+) value="([^"]*)"', r))
            cd = d.get("FNAF_ACCSUBJ_CD") or ""
            if len(cd) == 10 and cd[3:] not in rows:  # 첫 번째 행(지배주주지분은 순이익 바로 아래가 먼저 나옴)
                rows[cd[3:]] = {q: float(d[c]) * 1e6 for q, c in ((0, "A1"), (4, "A2"), (3, "A3"), (2, "A4"), (1, "A5")) if (d.get(c) or "").strip()}
        _sb[key] = rows
    k = f if f not in ("net_income",) else ("ni_parent" if fs == "CFS" else "ni_total")
    return _sb[key].get(SB_CODE[k], {})


def dart_quarters(code, y, fs, f):
    """{1..4: 3개월 값, 'A': 연간, 'ytd3': 3분기 누적, 'check': ① 통과 여부}"""
    k = dkey(f, fs)
    A = raw(code, y, 0, fs, "thstrm_amount").get(k)
    if A is None:
        return None
    if f in FLOW_IS:
        q = [raw(code, y, i, fs, "thstrm_amount").get(k) for i in (1, 2, 3)]
        ytd3 = raw(code, y, 3, fs, "thstrm_add_amount").get(k)
        if None in q or ytd3 is None:
            return None
        ok = abs(sum(q) - ytd3) <= TOL
    else:  # 현금흐름 원문 = 누적
        c = [raw(code, y, i, fs, "thstrm_amount").get(k) for i in (1, 2, 3)]
        if None in c:
            return None
        q, ytd3, ok = [c[0], c[1] - c[0], c[2] - c[1]], c[2], True
    return {1: q[0], 2: q[1], 3: q[2], 4: A - ytd3, "A": A, "ytd3": ytd3, "check": ok}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--seibro", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    fields = FLOW_IS + FLOW_CF
    ver = {tuple(r[:5]): (r[5], r[6], r[7], r[8]) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value, status
        FROM financial_field_verification WHERE field IN ({','.join('?' * len(fields))}) AND year>=2021""", fields).fetchall()}
    snap = {}
    for r in conn.execute("""SELECT DISTINCT ON (stock_code, year, quarter, report_type) stock_code, year, quarter, report_type, revenue, operating_profit
                             FROM financial_source_snapshot WHERE data_source='fnguide' AND is_annual=0 AND year>=2021
                             ORDER BY stock_code, year, quarter, report_type, fetched_at DESC""").fetchall():
        snap[(r[0], r[1], r[2], r[3])] = {"revenue": r[4], "operating_profit": r[5]}
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN ('fs_quirk:reporting_currency','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    targets = sorted({k[:4] + (k[4],) for k, v in ver.items() if v[3] == "원인 조사" and k[2] in (1, 2, 3, 4)})
    groups = sorted({(c, y, fs, f) for c, y, q, fs, f in targets})
    st, reg, detail, consistent = collections.Counter(), [], [], []
    for code, y, fs, f in groups:
        if code in excl:
            st["제외 종목(외화·비12월·단위)"] += 1; continue
        d = dart_quarters(code, y, fs, f)
        if d is None:
            st["DART 원문 부족"] += 1; continue
        if not d["check"]:
            st["① DART 내부 검산 실패"] += 1; continue
        fa = (ver.get((code, y, 0, fs, f)) or (None, None))[1]
        dba = (ver.get((code, y, 0, fs, f)) or (None,))[0]
        if fa is None or dba is None or abs(dba - fa) > TOL or abs(dba - d["A"]) > TOL:
            st["③ 연간 DB = FnGuide = DART 아님"] += 1; continue
        fq, src = {}, {}
        for q in (1, 2, 3, 4):
            v = ver.get((code, y, q, fs, f))
            if v and v[1] is not None:
                fq[q], src[q] = v[1], "wcomp"
            elif f in ("revenue", "operating_profit") and (snap.get((code, y, q, fs)) or {}).get(f) is not None:
                fq[q], src[q] = snap[(code, y, q, fs)][f], "snapshot"
        if len(fq) < 4 and a.seibro:
            sb = seibro(code, y, fs, f)
            have = {**{q: v for q, v in fq.items()}, 0: fa}
            common = [q for q in have if q in sb]
            if len(common) >= 2 and all(abs(sb[q] - have[q]) <= 1.5e6 for q in common):
                for q in (1, 2, 3, 4):
                    if q not in fq and q in sb:
                        fq[q], src[q] = sb[q], "seibro"
                st["SEIBro로 빠진 분기 채움(FnGuide와 대조 일치)"] += 1
            elif sb:
                st["SEIBro ≠ FnGuide(대리값 사용 안 함)"] += 1
            else:
                st["SEIBro 값 없음"] += 1
        if len(fq) < 4:
            st["FnGuide 분기 4개 중 빠짐"] += 1; continue
        gap = sum(fq.values()) - fa
        if abs(gap) <= max(3e8, abs(fa) * 0.003):
            st["④ FnGuide 분기 합 = 연간(내부 일치) — 이 규칙 대상 아님"] += 1
            consistent.append((code, y, fs, f, {q: fq[q] for q in (1, 2, 3, 4)}, {q: d[q] for q in (1, 2, 3, 4)}, src))
            continue
        for q in (1, 2, 3, 4):
            v = ver.get((code, y, q, fs, f))
            if not v or v[3] != "원인 조사":
                continue
            dbv, fgv, nv, _ = v
            if dbv is None or abs(dbv - d[q]) > TOL:
                st["② DB ≠ DART(우리 값 의심) — 보류"] += 1; continue
            if fgv is not None and abs(fgv - d[q]) <= TOL:
                continue
            ev = (f"DART 3개월 {d[1]:,.0f}/{d[2]:,.0f}/{d[3]:,.0f}/Q4 {d[4]:,.0f}, 1~3분기 합 = 3분기 누적 {d['ytd3']:,.0f}, 연간 {d['A']:,.0f} = FnGuide 연간. "
                  f"FnGuide 분기 {'/'.join(f'{fq[i]:,.0f}({src[i]})' for i in (1, 2, 3, 4))} 합 − 연간 = {gap:,.0f}")
            reg.append((code, y, q, fs, f, "FNGUIDE_QUARTER_INCONSISTENT", ev, dbv, fgv, nv,
                        "FnGuide 분기 합 ≠ FnGuide 연간(내부 불일치), DART 분기 = 원문 누적 검산 통과·연간 3자 일치 — DART 값 유지",
                        "사용자 판정 2026-10-09: FnGuide도 틀릴 수 있음, DART 분기 합 = 연간이면 DART 채택(008930 사례 일반화)"))
            st["판정: FnGuide 틀림 → 완결"] += 1
            detail.append((code, y, q, fs, f, dbv, fgv, gap))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "target_cells": len(targets),
           "groups": len(groups), "stats": dict(st), "register": len(reg),
           "by_field": dict(collections.Counter(x[4] for x in reg)), "stocks": sorted({x[0] for x in reg}), "detail": detail,
           "fnguide_consistent_groups": consistent}
    if a.apply and reg:
        run_id = f"fnguide_qinc_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany("""INSERT INTO financial_field_exception VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (stock_code, year, quarter, report_type, field) DO UPDATE SET reason_code=EXCLUDED.reason_code, dart_evidence=EXCLUDED.dart_evidence,
            db_value=EXCLUDED.db_value, fnguide_value=EXCLUDED.fnguide_value, naver_value=EXCLUDED.naver_value, exclusion_reason=EXCLUDED.exclusion_reason,
            approved_basis=EXCLUDED.approved_basis, run_id=EXCLUDED.run_id, created_at=EXCLUDED.created_at""", [x + (run_id, now) for x in reg])
        per = collections.defaultdict(set)
        for x in reg:
            per[x[0]].add(f"{x[4]}:{x[1]}Q{x[2]}")
        for code, s in per.items():
            conn.execute("""INSERT INTO stock_collection_config (stock_code, config_key, config_value, reason, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT (stock_code, config_key) DO UPDATE SET config_value=EXCLUDED.config_value, reason=EXCLUDED.reason, source=EXCLUDED.source, updated_at=EXCLUDED.updated_at""",
                         (code, "fs_quirk:fnguide_quarter_inconsistent", "FnGuide 분기 합 ≠ 연간 — DART 유지|" + ",".join(sorted(s)), "판정 규칙(사용자 10-09)", run_id, now, now))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_field_exception+stock_collection_config", "FnGuide 분기 내부 불일치 칸(값 변경 없음)", len(reg),
                      "DART 내부 검산 통과·DB = DART·연간 3자 일치·FnGuide 분기 합 ≠ 연간(> max(3억, 0.3%)) → FnGuide 틀림, 설명된 예외(분모 유지)",
                      json.dumps(dict(st), ensure_ascii=False), json.dumps(res["by_field"], ensure_ascii=False),
                      "scripts/review/judge_fnguide_quarter_inconsistent_20261009.py", run_id))
        conn.commit()
        res.update({"applied": True, "run_id": run_id,
                    "readback": conn.execute("SELECT COUNT(*) FROM financial_field_exception WHERE run_id=?", (run_id,)).fetchone()[0]})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = OUT / f"fnguide_quarter_inconsistent_20261009{'_seibro' if a.seibro else ''}_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "target_cells", "groups", "stats", "register", "by_field", "readback", "field_verification_after")}, ensure_ascii=False))
    print("종목", len(res["stocks"]), res["stocks"][:40])


if __name__ == "__main__":
    main()
