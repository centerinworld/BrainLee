#!/usr/bin/env python3
"""현금흐름 합계 계정 오태깅(회사 XBRL) 정정 — 원칙 0 파싱 오류 (2026-10-09). 기본 dry-run.

발견(10-09, 사용자 제시 'FnGuide 계정과목 ID vs OpenDART account_id 동일 여부' 점검): 일부 회사는 표준 계정 ID
`ifrs-full_CashFlowsFromUsed{Operating,Investing,Financing}Activities`를 합계가 아닌 세부 행('…현금유입액'·'영업활동에서 창출된 현금흐름')에
붙이고, 진짜 합계 행('…활동으로 인한 현금흐름')은 '-표준계정코드 미사용-'으로 둔다. 추출기(financial_rereview_20261002.extract)는 ID를 먼저
믿어 세부 행을 합계로 저장했다(예: 023810 2023 재무활동 DB 4,969억 vs 원문 합계 행·FnGuide 463억).
대상: 2023~2026 원문(`data_raw/dart_fnltt`)에서 이 패턴이 있는 종목(10-09 전수 탐색 6종목).
정정 조건(칸마다): 이름으로 찾은 합계 행 값(연간 = 당기, 분기 = 누적 YTD → 3개월 = YTD 차분)이 **그 칸의 FnGuide 값과 200만원 이내 일치**하고 DB와 다를 때만.
같은 기간 원장 행 전부(연간 두 벌 포함) 같은 값으로. `fs_quirk:cf_total_tag_mismatch` 기록, 해당 칸의 설명된 예외(있으면) 삭제 대상 표시.
적용: 백업·cashflow_fix_log·data_fix_log·readback·정본 동기화·재측정.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
IDS = {"operating_cf": ("ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업"),
       "investing_cf": ("ifrs-full_CashFlowsFromUsedInInvestingActivities", "투자"),
       "financing_cf": ("ifrs-full_CashFlowsFromUsedInFinancingActivities", "재무")}
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def name_totals(path):
    """오태깅 패턴이 있는 항목만: {field: 이름 기반 합계 값(thstrm_amount)}"""
    if not path.exists():
        return {}
    cf = [r for r in json.loads(gzip.decompress(path.read_bytes())) if r.get("sj_div") == "CF"]
    out = {}
    for f, (idv, act) in IDS.items():
        tagged = [r for r in cf if r.get("account_id") == idv]
        if not tagged or not re.search("유입액|유출액|창출", (tagged[0].get("account_nm") or "").replace(" ", "")):
            continue
        tot = [r for r in cf if r.get("account_id") != idv and re.search(act + "활동(으로인한|)현금흐름$", (r.get("account_nm") or "").replace(" ", ""))]
        if tot and num(tot[0].get("thstrm_amount")) is not None:
            out[f] = num(tot[0].get("thstrm_amount"))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    codes = sorted({p.parent.name for p in RAW.glob("*/20[2][3-6]_*_*.json.gz") if name_totals(p)})
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    fg = {tuple(r[:5]): float(r[5]) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, fnguide_value
        FROM financial_field_verification WHERE stock_code IN ({','.join('?' * len(codes))}) AND fnguide_value IS NOT NULL
        AND field IN ('operating_cf','investing_cf','financing_cf')""", codes).fetchall()}
    plan, st = [], collections.Counter()
    for code in codes:
        for fs in ("CFS", "OFS"):
            for y in (2023, 2024, 2025, 2026):
                ytd = {q: name_totals(RAW / code / f"{y}_{q}_{fs}.json.gz") for q in (0, 1, 2, 3)}
                for f in IDS:
                    for q in (0, 1, 2, 3, 4):
                        if q == 0:
                            cum = ytd[0].get(f); three = None
                        elif q == 4:
                            cum = ytd[0].get(f)
                            three = (cum - ytd[3][f]) if (cum is not None and ytd[3].get(f) is not None) else None
                        else:
                            cum = ytd[q].get(f)
                            prev = ytd[q - 1].get(f) if q > 1 else 0
                            three = (cum - prev) if (cum is not None and prev is not None) else None
                        cmpv = cum if q == 0 else three
                        if cmpv is None:
                            continue
                        e = fg.get((code, y, q, fs, f))
                        if e is None:
                            st["FnGuide 없음 — 보류"] += 1
                            continue
                        if abs(cmpv - e) > 2e6:
                            st["이름 합계 ≠ FnGuide — 보류"] += 1
                            continue
                        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
                        for rid, oc, oq in conn.execute(f"SELECT id, {f}, {f}_q FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                                                        (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall():
                            for col, old, new in ([(f, oc, cum)] + ([] if q == 0 else [(f + "_q", oq, three)])):
                                if new is None or (old is not None and abs(old - new) <= 0.5):
                                    continue
                                plan.append((rid, code, y, q, fs, col, old, new))
                                st["정정"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "codes": codes,
           "stats": dict(st), "planned": len(plan), "sample": plan[:30]}
    if a.apply and plan:
        run_id = f"cf_tag_fix_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "cash_flow_data_backup_cf_tag_20261009"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
        ids = sorted({p[0] for p in plan})
        conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM cash_flow_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
        for rid, code, y, q, fs, col, old, new in plan:
            conn.execute(f"UPDATE cash_flow_data SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
        conn.executemany("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new,
                           "회사 XBRL 오태깅: 표준 ID가 세부 행에 붙고 합계 행은 미사용 코드 — 이름 기반 합계 = FnGuide", "DART 원문(data_raw/dart_fnltt)", run_id)
                          for rid, code, y, q, fs, col, old, new in plan])
        for code in codes:
            conn.execute("""INSERT INTO stock_collection_config (stock_code, config_key, config_value, reason, source, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?) ON CONFLICT (stock_code, config_key) DO UPDATE SET config_value=EXCLUDED.config_value, updated_at=EXCLUDED.updated_at""",
                         (code, "fs_quirk:cf_total_tag_mismatch", "현금흐름 합계 표준 ID가 세부 행(유입액·창출)에 붙음 — 이름 기반 합계 사용",
                          "PLAN 10-09 계정 ID 점검", run_id, now, now))
        n_ex = conn.execute(f"""DELETE FROM financial_field_exception WHERE (stock_code, year, quarter, report_type, field) IN
                               (SELECT stock_code, year, quarter, report_type, field FROM financial_field_exception WHERE stock_code IN ({','.join('?' * len(codes))})
                                AND field IN ('operating_cf','investing_cf','financing_cf') AND reason_code='FNGUIDE_ONLY_DIFF')""", codes).rowcount
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cash_flow_data+stock_collection_config+financial_field_exception", f"현금흐름 합계 오태깅 {len(codes)}종목", len(plan),
                      "이름 기반 합계 행 = FnGuide인 칸만 정정, 해당 종목 현금흐름 'FnGuide만 다름' 예외 삭제(파싱 오류였음)",
                      json.dumps(dict(st), ensure_ascii=False), f"예외 삭제 {n_ex}", "scripts/review/fix_cf_total_tag_mismatch_20261009.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[5]} FROM cash_flow_data WHERE id=?", (p[0],)).fetchone()[0]) - p[7]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "exceptions_removed": n_ex,
                    "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, set(), set(ids))})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    (ROOT / "research_outputs" / "financial_rereview_20261002" / f"cf_tag_fix_20261009_{'apply' if a.apply else 'dry'}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "codes", "stats", "planned", "exceptions_removed", "post_readback", "field_verification_after")}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
