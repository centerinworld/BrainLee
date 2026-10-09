#!/usr/bin/env python3
"""PLAN D-1(조건부 승인 10-09) — 판정표의 B·C 칸을 '설명된 예외'로 등록 (2026-10-09). 기본 dry-run.

분모에서 빼지 않는다: `financial_field_exception`에 등록된 미확정 칸은 해결률(resolution_rate)에만 들어가고 외부 확정률은 그대로.
등록 조건(칸마다, 최신 판정 `financial_field_cause_review` + 현행 검증이 미확정):
  B 'FnGuide = 전체 순이익(지배 아님)'     → reason_code FNGUIDE_TOTAL_BASIS  (정본 §2-1은 지배 기준 — DB 유지)
  C '원문 = DB, FnGuide 단독 다름'          → reason_code FNGUIDE_CF_RECLASS(현금흐름 세 활동 합 DB = FnGuide — 재분류) / FNGUIDE_ONLY_DIFF(그 밖)
  제외(자동 예외 금지 → 사람 판정): D 전부, 그리고 C 중 **네이버 = FnGuide**(외부 두 곳 일치·DART만 다름 — 원칙 0상 우리 파싱 의심)
예외 비율 상한: 영역(손익·재무상태 / 현금흐름 / CapEx / 감가상각)별 등록 예외가 전체 칸의 0.5%를 넘으면 경고(등록은 하되 리스크 표시).
적용: 등록 + data_fix_log + 검증 재구축(해결률 기록). 이미 등록된 칸은 갱신.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

AREA = {**{f: "손익·재무상태" for f in ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")},
        **{f: "현금흐름" for f in ("operating_cf", "investing_cf", "financing_cf")}, "capex": "CapEx",
        "depreciation": "감가상각", "depreciation_amortization": "감가상각"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    cur = {tuple(r[:5]): r[5] for r in conn.execute(
        "SELECT stock_code, year, quarter, report_type, field, status FROM financial_field_verification").fetchall()}
    totals = collections.Counter(AREA.get(k[4]) for k in cur)
    rows = conn.execute("""SELECT DISTINCT ON (stock_code,year,quarter,report_type,field) stock_code,year,quarter,report_type,field,
                               db_value,fnguide_value,naver_value,verdict,evidence,run_id
                           FROM financial_field_cause_review ORDER BY stock_code,year,quarter,report_type,field, created_at DESC""").fetchall()
    # 현금흐름 분류 차이 근거: 같은 기간 영업+투자+재무 합이 DB와 FnGuide에서 같으면 FnGuide가 항목을 다른 활동으로 옮겨 분류한 것
    cfv = collections.defaultdict(dict)
    for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value FROM financial_field_verification
                             WHERE field IN ('operating_cf','investing_cf','financing_cf')""").fetchall():
        cfv[tuple(r[:4])][r[4]] = (r[5], r[6])
    def cf_reclass(key):
        d = cfv.get(key[:4], {})
        if len(d) < 3 or any(v[0] is None or v[1] is None for v in d.values()):
            return False
        return abs(sum(v[0] for v in d.values()) - sum(v[1] for v in d.values())) <= 6e6
    reg, st = [], collections.Counter()
    for r in rows:
        key = tuple(r[:5]); s = cur.get(key)
        if s is None or s.startswith("확정"):
            continue
        db_v, fg, nv, verdict, ev, run = r[5:]
        if verdict.startswith("B"):
            code, why = "FNGUIDE_TOTAL_BASIS", "FnGuide가 전체(비지배 포함) 기준으로 표시 — 정본 §2-1 지배 기준 유지"
        elif verdict.startswith("C"):
            if nv is not None and fg is not None and abs(nv - fg) <= max(1e8, abs(fg) * 0.005):
                st["사람 판정으로(C 중 네이버 = FnGuide)"] += 1
                continue
            if key[4] in ("operating_cf", "investing_cf", "financing_cf") and cf_reclass(key):
                code, why = "FNGUIDE_CF_RECLASS", "DART 원문 = DB, 영업+투자+재무 합은 DB = FnGuide(600만원 이내) — FnGuide가 항목을 다른 활동으로 재분류"
            else:
                code, why = "FNGUIDE_ONLY_DIFF", "DART 원문 = DB, FnGuide만 다름(정의·재작성 시점 차이 후보) — 네이버 비교값 없음 또는 네이버 ≠ FnGuide"
        else:
            st[f"자동 예외 금지({verdict[:1]})"] += 1
            continue
        st[code] += 1
        reg.append(key + (code, f"{ev} [{run}]", db_v, fg, nv, why, "D-1 조건부 승인(사용자 10-09)"))
    share = collections.Counter(AREA.get(x[4]) for x in reg)
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st),
           "register": len(reg), "by_area": dict(share),
           "share_pct": {k: round(100 * v / totals[k], 4) for k, v in share.items()},
           "cap_warning": [k for k, v in share.items() if 100 * v / totals[k] > 0.5], "sample": reg[:20]}
    if a.apply and reg:
        run_id = f"explained_exception_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("""CREATE TABLE IF NOT EXISTS financial_field_exception (stock_code TEXT, year INT, quarter INT, report_type TEXT, field TEXT,
                        reason_code TEXT, dart_evidence TEXT, db_value DOUBLE PRECISION, fnguide_value DOUBLE PRECISION, naver_value DOUBLE PRECISION,
                        exclusion_reason TEXT, approved_basis TEXT, run_id TEXT, created_at TEXT,
                        PRIMARY KEY (stock_code, year, quarter, report_type, field))""")
        conn.executemany("""INSERT INTO financial_field_exception VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT (stock_code, year, quarter, report_type, field) DO UPDATE SET reason_code=EXCLUDED.reason_code,
                            dart_evidence=EXCLUDED.dart_evidence, db_value=EXCLUDED.db_value, fnguide_value=EXCLUDED.fnguide_value,
                            naver_value=EXCLUDED.naver_value, exclusion_reason=EXCLUDED.exclusion_reason, run_id=EXCLUDED.run_id, created_at=EXCLUDED.created_at""",
                         [x + (run_id, now) for x in reg])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_field_exception", "판정표 B·C 설명된 예외(값 변경 없음)", len(reg),
                      "D-1 조건부 승인: B·C만, C 중 네이버 = FnGuide 제외, D 제외 — 분모 불변·해결률에만 반영",
                      json.dumps(dict(st), ensure_ascii=False), json.dumps(res["share_pct"], ensure_ascii=False),
                      "scripts/review/register_explained_exceptions_20261009.py", run_id))
        conn.commit()
        res.update({"applied": True, "run_id": run_id,
                    "readback": conn.execute("SELECT COUNT(*) FROM financial_field_exception WHERE run_id=?", (run_id,)).fetchone()[0]})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"explained_exception_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "register", "by_area", "share_pct", "cap_warning", "readback", "field_verification_after")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
