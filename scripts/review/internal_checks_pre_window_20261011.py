#!/usr/bin/env python3
"""FnGuide 표시 기간 이전(2016~2022) 내부 검증 — OpenDART 단일 경로 데이터 (2026-10-11 사용자 지시: '음수부호, 합이 맞지 않는다는 등의
내부 값 검증 작업은 진행을 하고 확정 계획을 세울 것'). 읽기 전용, 결과 표 internal_check_daily + 칸 목록 csv.

검사(칸 단위, 해당하는 것만):
  C1 부호      매출 ≥ 0, 자산 > 0, 부채 ≥ 0
  C2 항등식    별도: |자산 − 부채 − 자본| ≤ max(200만원, 자산 0.01%) / 연결: 자산 − 부채 ≥ 지배 자본 − 200만원(비지배 ≥ 0) 그리고 원문 자본총계와 자산 − 부채 일치
  C3 분기 합   손익: Q1+Q2+Q3+Q4(3개월) = 연간(±200만원) / 현금흐름: Q4 누적 = 연간, Q1~Q4 3개월 합 = 연간
  C4 원문 대조 DB = DART 원문(fin_norm.raw; 연간은 재작성 우선 latest_annual 또는 원본 중 하나) ±200만원
내부 확인 = 해당 검사 전부 통과. 연도·영역별 비율 기록.
"""
from __future__ import annotations

import collections
import csv
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
T = 2e6
IS = ("revenue", "operating_profit", "net_income")
CF = ("operating_cf", "investing_cf", "financing_cf")


def main():
    conn = connect_primary_db(timeout=900)
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN ('fs_quirk:reporting_currency')
        OR (config_key='fs_quirk:fiscal_period' AND config_value LIKE 'REIT%%') UNION SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'""").fetchall()}
    fd = collections.defaultdict(dict)
    for r in conn.execute("""SELECT stock_code, year, quarter, is_annual, report_type, revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity
                             FROM financial_data WHERE year BETWEEN 2016 AND 2022 ORDER BY id""").fetchall():
        code, y, q, ia, fs = r[:5]
        k = (code, y, fs)
        fd[k].setdefault(0 if ia else q, dict(zip(IS + ("total_assets", "total_liabilities", "total_equity"), r[5:])))
    cfd = collections.defaultdict(dict)
    for r in conn.execute("""SELECT stock_code, year, quarter, is_annual, report_type, operating_cf, investing_cf, financing_cf, operating_cf_q, investing_cf_q, financing_cf_q
                             FROM cash_flow_data WHERE year BETWEEN 2016 AND 2022 AND quarter IS NOT NULL ORDER BY id""").fetchall():
        code, y, q, ia, fs = r[:5]
        cfd[(code, y, fs)].setdefault(0 if ia else q, dict(zip(CF + tuple(f + "_q" for f in CF), r[5:])))
    # 10-12: 사유 확정 칸(financial_field_exception — 원문 모순·연간 원문 없음·원문 음수 공시·단위 오기 등). 분모에 그대로 두고 따로 센다
    exc = {}
    for r in conn.execute("SELECT stock_code, year, quarter, report_type, field, reason_code FROM financial_field_exception WHERE year BETWEEN 2016 AND 2022").fetchall():
        exc[tuple(r[:5])] = r[5]
    seen = set()
    res = collections.Counter(); fails = []
    def rec(y, area, ok, key, chk, rb=False):
        # 10-13 외부 검토 P2: 원문 후보가 있어 원문과 대조된 칸(raw_backed)과, 원문 없이 DB 사슬·내부 일치만 본 칸(no_raw_candidate)을 따로 센다
        seen.add(key)
        res[(y, area, "raw_backed" if rb else "no_raw_candidate")] += 1
        if rb and ok:
            res[(y, area, "raw_backed_pass")] += 1
        if not ok and key in exc:
            res[(y, area, "explained")] += 1; res[(y, area, "explained:" + exc[key])] += 1
            ok = True
        res[(y, area, "total")] += 1
        res[(y, area, "pass" if ok else "fail")] += 1
        if not ok:
            res[(y, area, chk)] += 1
            if len(fails) < 200000:
                fails.append(key + (chk,))
    for (code, y, fs), d in fd.items():
        if code in excl:
            continue
        for q, v in d.items():
            for f in IS + ("total_assets", "total_liabilities", "total_equity"):
                val = v.get(f)
                if val is None:
                    continue
                area = "손익" if f in IS else "재무상태"
                bad = []
                rb = False
                if f == "revenue" and val < -T:
                    bad.append("C1 부호(매출 음수)")
                if f == "total_assets" and val <= 0:
                    bad.append("C1 부호(자산)")
                if f == "total_liabilities" and val < -T:
                    bad.append("C1 부호(부채 음수)")
                if f in ("total_assets", "total_liabilities", "total_equity"):
                    A, L, E = v.get("total_assets"), v.get("total_liabilities"), v.get("total_equity")
                    if None not in (A, L, E):
                        if fs == "OFS" and abs(A - L - E) > max(T, abs(A) * 1e-4):
                            # 10-12: 원문 자체가 A ≠ L + E이고 DB 세 값 = 원문(당기 또는 다음 해 전기 칸)이면 통과(OpenDART 단일 경로)
                            src = [(fn.dart_key_for_fiscal(code, y, q) if q in (1, 2, 3) else (y, 0), "thstrm_amount")] + ([((y + 1, 0), "frmtrm_amount")] if q in (0, 4) else [])
                            exs = [fn._extract(code, *k, "OFS", col) for k, col in src]
                            if not any(all(e.get(k2) is not None and abs(e[k2] - x) <= T for k2, x in (("total_assets", A), ("total_liabilities", L), ("equity_total", E))) for e in exs):
                                bad.append("C2 항등식(별도 A≠L+E)")
                        if fs == "CFS":
                            # 10-11: 비지배지분이 음수(자본잠식 자회사)면 지배 자본 > 자산 − 부채가 정상 → 원문 자본총계(지배+비지배)와 A − L 대조
                            # 10-12: 원문 후보 = 그 보고서 당기 + (연간·4분기) 다음 해 사업보고서 전기(재작성). 원문 자체가 A−L ≠ 자본총계이면 DB 자산·부채 = 원문이면 통과
                            rfs2 = fn.effective_fs(code, y, fs)
                            src = [(fn.dart_key_for_fiscal(code, y, q) if q in (1, 2, 3) else (y, 0), "thstrm_amount")] + ([((y + 1, 0), "frmtrm_amount")] if q in (0, 4) else [])
                            exs = [fn._extract(code, *k, rfs2, col) for k, col in src]
                            ets = [e.get("equity_total") for e in exs if e.get("equity_total") is not None]
                            tol = max(T, abs(A) * 1e-4)
                            if ets:
                                ok_et = any(abs((A - L) - et) <= tol for et in ets)
                                ok_raw = any(e.get("total_assets") is not None and e.get("total_liabilities") is not None and abs(e["total_assets"] - A) <= T
                                             and abs(e["total_liabilities"] - L) <= T and abs(e["total_assets"] - e["total_liabilities"] - (e.get("equity_total") or 0)) > tol for e in exs)
                                if not (ok_et or ok_raw):
                                    bad.append("C2 항등식(연결 A−L ≠ 원문 자본총계)")
                            elif (A - L) < E - T:
                                bad.append("C2 항등식(연결 A−L < 지배자본, 원문 없음)")
                if f in IS and q == 4:  # D-6: 4분기 = 연간 − 3분기 보고서 누적(원문 있을 때만 검사). 앞 분기 재작성 시 4개 분기 합 ≠ 연간은 정상
                    y3 = fn.ytd3(code, y, fn.effective_fs(code, y, fs), f)
                    A = (d.get(0) or {}).get(f)
                    # 10-12: 재작성 기준(§2-5) 4분기 = 재작성 연간 − 재작성 1~3분기 합 → 네 분기 합 = 연간이면 통과
                    _rf = fn.effective_fs(code, y, fs)
                    _qv = [d[qq][f] if qq in d and d[qq].get(f) is not None else fn.raw(code, y + 1, qq, _rf, f, "frmtrm_q_amount") for qq in (1, 2, 3)]
                    s3 = sum(_qv) if None not in _qv else None  # 10-12: DB에 없는 분기(상장 전 등)는 다음 해 보고서 재작성 분기 원문으로
                    rb = (y3 is not None) or (s3 is not None and A is not None)
                    _Ao = fn.raw(code, y, 0, _rf, f)
                    if s3 is not None and A is not None and abs(A - s3 - val) <= T:
                        pass
                    elif y3 is not None and _Ao is not None and abs(fn.q4_flow(_Ao, y3) - val) <= T:
                        pass  # 10-12: 원래 공시 기준 4분기(원래 연간 − 원래 3분기 누적) — 재작성 분기가 없거나 재작성 기준이 음수 매출일 때(resolve_pre_window_is_q4)
                    elif y3 is not None and A is not None and abs(fn.q4_flow(A, y3) - val) > T:
                        bad.append("C3 4분기 ≠ 연간 − 3분기 누적")
                    elif y3 is None and A is not None and all(qq in d and d[qq].get(f) is not None for qq in (1, 2, 3)) and abs(A - sum(d[qq][f] for qq in (1, 2, 3)) - val) > T:
                        bad.append("C3 4분기 ≠ 연간 − Q1~Q3(원문 누적 없음)")
                rv = None
                rfs = fn.effective_fs(code, y, fs)  # 10-12: 연결 미작성 회사의 'CFS' 행은 별도 원문과 대조
                if q in (0, 1, 2, 3):
                    cand = [fn.raw(code, y, q, rfs, f)] if q else [fn.latest_annual(code, y, rfs, f)[0], fn.raw(code, y, 0, rfs, f)]
                    cand = [c for c in cand if c is not None]
                    if f in IS and q in (1, 2, 3):
                        cand.append(fn.latest_quarter_3m(code, y, q, rfs, f)[0])
                    cand = [c for c in cand if c is not None]
                    rb = bool(cand)
                    if cand and not any(abs(c - val) <= T for c in cand):
                        bad.append("C4 원문 ≠ DB")
                elif f not in IS:  # 재무상태 4분기 = 연간(D-6): 연간 원문이 있으면 원문 근거
                    rb = fn.latest_annual(code, y, rfs, f)[0] is not None
                rec(y, area, not bad, (code, y, q, fs, f), "; ".join(bad), rb=rb)
    for (code, y, fs), d in cfd.items():
        if code in excl or 0 not in d:
            continue
        for f in CF:
            A = d[0].get(f)
            if A is None:
                continue
            bad = []
            if 4 in d and d[4].get(f) is not None and abs(d[4][f] - A) > T:
                bad.append("C3 4분기 누적 ≠ 연간")
            if all(qq in d and d[qq].get(f + "_q") is not None for qq in (1, 2, 3, 4)) and abs(sum(d[qq][f + "_q"] for qq in (1, 2, 3, 4)) - A) > T:
                bad.append("C3 3개월 합 ≠ 연간")
            rfs = fn.effective_fs(code, y, fs)  # 10-12: 연결 미작성 회사의 'CFS' 행은 별도 원문과 대조
            cand = [c for c in (fn.latest_annual(code, y, rfs, f)[0], fn.raw(code, y, 0, rfs, f)) if c is not None]
            if cand and not any(abs(c - A) <= T for c in cand):
                bad.append("C4 원문 ≠ DB")
            rec(y, "현금흐름", not bad, (code, y, 0, fs, f), "; ".join(bad), rb=bool(cand))
    # 값을 비우고 사유를 기록한 칸(4분기 계산 불가 등)도 분모에 넣는다 — 분모를 줄여 비율을 올리지 않음
    for (code, y, q, fs, f), rc in exc.items():
        if code in excl or (code, y, q, fs, f) in seen:
            continue
        area = "손익" if f in IS else "재무상태" if f in ("total_assets", "total_liabilities", "total_equity") else "현금흐름" if f in CF else None
        if area:
            res[(y, area, "total")] += 1; res[(y, area, "pass")] += 1
            res[(y, area, "explained")] += 1; res[(y, area, "explained:" + rc)] += 1
    now = datetime.now().isoformat(timespec="seconds")
    run = f"internal_chk_{datetime.now():%Y%m%d_%H%M%S}"
    conn.execute("""CREATE TABLE IF NOT EXISTS internal_check_daily (run_id TEXT, measured_at TEXT, year INT, area TEXT, total INT, passed INT, rate DOUBLE PRECISION, breakdown TEXT)""")
    rows, summary = [], {}
    for (y, area) in sorted({(k[0], k[1]) for k in res}):
        tot, ok = res[(y, area, "total")], res[(y, area, "pass")]
        br = {k[2]: v for k, v in res.items() if k[0] == y and k[1] == area and k[2] not in ("total", "pass", "fail")}
        ex = res[(y, area, "explained")]
        rows.append((run, now, y, area, tot, ok, round(100 * ok / tot, 4) if tot else None, json.dumps(br, ensure_ascii=False)))
        summary[f"{y}|{area}"] = {"total": tot, "pass": ok, "rate": round(100 * ok / tot, 3) if tot else None,
                                  "rate_without_explained": round(100 * (ok - ex) / tot, 3) if tot else None,
                                  "raw_backed": res[(y, area, "raw_backed")], "raw_backed_rate": round(100 * res[(y, area, "raw_backed_pass")] / res[(y, area, "raw_backed")], 3) if res[(y, area, "raw_backed")] else None,
                                  "no_raw_candidate": res[(y, area, "no_raw_candidate")], "fail_by_check": br}
    conn.executemany("INSERT INTO internal_check_daily VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    (OUT / f"{run}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    with open(OUT / f"{run}_fails.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["stock_code", "year", "quarter", "fs", "field", "checks"]); w.writerows(fails)
    print(run)
    for k, v in summary.items():
        print(k, v["total"], f'{v["rate"]}%', f'(사유 확정 제외 {v["rate_without_explained"]}%)', f'[원문 근거 {v["raw_backed"]}칸 {v["raw_backed_rate"]}% / 원문 후보 없음 {v["no_raw_candidate"]}칸]', v["fail_by_check"])


if __name__ == "__main__":
    main()
