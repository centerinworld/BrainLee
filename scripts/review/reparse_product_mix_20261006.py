#!/usr/bin/env python3
"""제품별 매출(company_product_mix) 2021+ 재파싱 — 저장된 사업보고서 원문 + 고친 표 파서(TE·TU 셀) (2026-10-06, docs/OPEN_ITEMS.md C6).

예전 수집기(dart_product_mix_collector)는 표 셀 <TE>를 버려 2023년 이후 XBRL형 표의 값이 빠질 수 있었다.
판정: 새 파싱의 품목 합계가 연간 매출(financial_data, CFS 또는 OFS)과 2% 이내면 'ok'.
  기존 행이 없거나, 기존 합계는 매출과 안 맞는데 새 합계는 맞으면 → 교체(백업). 둘 다 맞거나 새 것이 안 맞으면 → 그대로.
기본 dry-run, --apply.
"""
import argparse
import collections
import glob
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "collectors"))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
from dart_product_mix_collector import extract_product_mix  # noqa: E402
from parse_business_docs_20261005 import load  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600)
    rev = collections.defaultdict(list)
    for c_, y, v in map(tuple, conn.execute("SELECT stock_code, year, revenue FROM financial_data WHERE is_annual AND revenue>0 AND year>=2021").fetchall()):
        rev[(c_, y)].append(v)
    cur = collections.defaultdict(float)
    for c_, y, v in map(tuple, conn.execute("SELECT stock_code, year, SUM(revenue_krw) FROM company_product_mix WHERE year>=2021 GROUP BY 1,2").fetchall()):
        cur[(c_, y)] = v or 0
    conn.commit()
    conn.close()
    latest = {}
    for f in glob.glob(str(RAW / "*" / "20[2-9][0-9]_*.zip")):
        p = Path(f)
        m = re.match(r"(\d{4})_(\d+)\.zip$", p.name)
        if m and int(m.group(1)) >= 2021:
            k = (p.parent.name, int(m.group(1)))
            if k not in latest or m.group(2) > latest[k][1]:
                latest[k] = (f, m.group(2))
    ok = lambda s, k: any(abs(s - r) <= abs(r) * 0.02 for r in rev.get(k, []))
    st, plan = collections.Counter(), []
    for k, (f, rc) in sorted(latest.items()):
        try:
            recs, _, mul = extract_product_mix(load(f))
        except Exception:
            st["파싱 예외"] += 1
            continue
        tot = sum((q["revenue"] or 0) * mul for q in recs)
        if not recs or tot <= 0:
            st["새 파싱 없음"] += 1
            continue
        new_ok, old_ok = ok(tot, k), ok(cur.get(k, 0), k)
        if new_ok and (k not in cur or not old_ok):
            plan.append((k, rc, recs, mul, tot))
            st["교체(새 것만 매출과 일치)" if k in cur else "추가(기존 없음)"] += 1
        else:
            st["유지(기존도 일치)" if old_ok else ("유지(새 것도 불일치)" if not new_ok else "유지")] += 1
    print(dict(st))
    if not a.apply or not plan:
        return
    run_id = f"product_mix_reparse_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn = connect_primary_db(timeout=600)
    conn.execute("CREATE TABLE IF NOT EXISTS company_product_mix_backup_20261006 AS SELECT *, CAST(NULL AS TEXT) run_id FROM company_product_mix WHERE false")
    for (code, y), rc, recs, mul, tot in plan:
        conn.execute("INSERT INTO company_product_mix_backup_20261006 SELECT *, ? FROM company_product_mix WHERE stock_code=? AND year=?", (run_id, code, y))
        conn.execute("DELETE FROM company_product_mix WHERE stock_code=? AND year=?", (code, y))
        conn.executemany("""INSERT INTO company_product_mix(stock_code, year, category, product_name, revenue_krw, revenue_pct, rcept_no, source)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         [(code, y, q["category"], q["product_name"], q["revenue"] * mul, round(q["revenue"] * mul / tot * 100, 2), rc, "dart_doc_cache_v2") for q in recs])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (datetime.now().isoformat(timespec="seconds"), "company_product_mix", "제품별 매출 2023+ 재파싱", len(plan),
                  "새 파싱 품목 합계 = 연간 매출(2%)일 때만, 기존이 없거나 기존 합계가 매출과 안 맞을 때", str(dict(st)), "", "scripts/review/reparse_product_mix_20261006.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan))


if __name__ == "__main__":
    main()
