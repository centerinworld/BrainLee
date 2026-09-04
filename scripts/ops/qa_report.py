#!/usr/bin/env python3
"""
qa_report.py — 배치 완료 시 자동 QA 로그 생성

검증 2종:
  A) 교차 검증 (5항목): 대분류합·등급합·AMBIG=0·OK%·A+B급 OPEN 상한
  B) 필드 완결성 (별도): 핵심 필드가 실제로 채워져 있는지 (교차 검증과 무관)

사용:
  python3 scripts/ops/qa_report.py [--out-dir /path/to/logs]
"""
import argparse, json, os, sys, sqlite3
from datetime import datetime

ROOT = "/Volumes/Realtek_NVME/stock_dashboard/runtime"
DB   = f"{ROOT}/stock.db"


# ── A. 교차 검증용 지표 수집 ─────────────────────────────────────────────────
def collect(conn):
    snap_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snap_id = "snap_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    rows = conn.execute("""
        SELECT status, COUNT(*) cnt
        FROM fin_quarterly_validation_flags
        WHERE check_type='QUARTERLY_4WAY'
        GROUP BY status
    """).fetchall()
    total     = sum(r[1] for r in rows)
    by_status = {r[0]: r[1] for r in rows}
    ok_cnt    = sum(v for k, v in by_status.items()
                    if k in ('CONFIRMED', 'CLOSE_MATCH', 'SELF_CONSISTENT'))
    open_cnt  = by_status.get('OPEN', 0)
    str_cnt   = by_status.get('STRUCTURAL', 0)
    ambig_cnt = by_status.get('AMBIGUOUS', 0)

    all_ambig = conn.execute("""
        SELECT check_type, SUM(status='AMBIGUOUS') a
        FROM fin_quarterly_validation_flags
        GROUP BY check_type
    """).fetchall()
    ambig_by_ct = {r[0]: r[1] for r in all_ambig}

    tier_rows = conn.execute("""
        WITH su AS (SELECT stock_code, MAX(market_cap) mc FROM stock_universe GROUP BY stock_code)
        SELECT
          CASE WHEN COALESCE(su.mc,0)>=1000000 THEN 'A'
               WHEN COALESCE(su.mc,0)>=100000  THEN 'B'
               WHEN COALESCE(su.mc,0)>=10000   THEN 'C'
               WHEN COALESCE(su.mc,0)>0        THEN 'D'
               ELSE                                 'E' END tier,
          COUNT(DISTINCT f.stock_code) stocks,
          COUNT(*) records,
          SUM(f.dart_value IS NOT NULL) dart_cnt
        FROM fin_quarterly_validation_flags f
        LEFT JOIN su ON su.stock_code=f.stock_code
        WHERE f.check_type='QUARTERLY_4WAY' AND f.status='OPEN'
        GROUP BY tier ORDER BY tier
    """).fetchall()
    tiers        = {r[0]: {"stocks": r[1], "records": r[2], "dart_cnt": r[3]} for r in tier_rows}
    tier_rec_sum = sum(t["records"] for t in tiers.values())

    return {
        "snapshot_run_id": snap_id,
        "snapshot_ts":     snap_ts,
        "total":           total,
        "ok_cnt":          ok_cnt,
        "ok_pct":          round(ok_cnt / max(total, 1) * 100, 2),
        "open_cnt":        open_cnt,
        "structural_cnt":  str_cnt,
        "ambiguous_cnt":   ambig_cnt,
        "by_status":       dict(sorted(by_status.items())),
        "ambig_by_check_type": ambig_by_ct,
        "tiers":           tiers,
        "tier_rec_sum":    tier_rec_sum,
    }


# ── B. 필드 완결성 검증 (교차 검증과 완전히 별개) ────────────────────────────
def check_completeness(conn) -> dict:
    """
    투자 핵심 필드가 실제로 채워져 있는지 확인.
    교차 검증(소스 간 일치 여부)과 무관 — 필드 자체의 NULL 여부만 봄.
    기준: 2022~2025 연간 CFS, 90% 미만이면 경고.
    """
    THRESHOLD = 90.0
    results = {}

    # 재무제표
    fin_rows = conn.execute("""
        SELECT year,
          ROUND(100.0*SUM(revenue IS NOT NULL AND revenue>0)/COUNT(*),1) rev,
          ROUND(100.0*SUM(operating_profit IS NOT NULL)/COUNT(*),1) op,
          ROUND(100.0*SUM(net_income IS NOT NULL)/COUNT(*),1) ni,
          ROUND(100.0*SUM(total_assets IS NOT NULL)/COUNT(*),1) assets,
          ROUND(100.0*SUM(total_equity IS NOT NULL)/COUNT(*),1) equity,
          COUNT(*) total
        FROM financial_data
        WHERE is_annual=1 AND report_type='CFS' AND year BETWEEN 2022 AND 2025
        GROUP BY year ORDER BY year DESC
    """).fetchall()

    fin_issues = []
    fin_detail = []
    for row in fin_rows:
        yr = row[0]
        row_issues = []
        for field, val in [("revenue",row[1]),("OP",row[2]),("NI",row[3]),
                           ("assets",row[4]),("equity",row[5])]:
            if val is not None and val < THRESHOLD:
                row_issues.append(f"{field}={val}%")
        if row_issues:
            fin_issues.append(f"{yr}년: {', '.join(row_issues)}")
        fin_detail.append({
            "year": yr, "revenue": row[1], "op": row[2], "ni": row[3],
            "assets": row[4], "equity": row[5], "total": row[6]
        })

    results["C1_fin_completeness"] = {
        "desc": "재무제표 핵심 필드 완결성 (≥90%)",
        "detail": fin_detail,
        "issues": fin_issues,
        "pass": len(fin_issues) == 0,
    }

    # 현금흐름
    cf_rows = conn.execute("""
        SELECT year,
          ROUND(100.0*SUM(operating_cf IS NOT NULL)/COUNT(*),1) ocf,
          ROUND(100.0*SUM(capex IS NOT NULL)/COUNT(*),1) capex,
          ROUND(100.0*SUM(depreciation IS NOT NULL)/COUNT(*),1) dep,
          ROUND(100.0*SUM(cash_end IS NOT NULL)/COUNT(*),1) cash,
          COUNT(*) total
        FROM cash_flow_data
        WHERE is_annual=1 AND report_type='CFS' AND year BETWEEN 2022 AND 2025
        GROUP BY year ORDER BY year DESC
    """).fetchall()

    cf_issues = []
    cf_detail = []
    for row in cf_rows:
        yr = row[0]
        row_issues = []
        for field, val, thr in [("OCF",row[1],THRESHOLD),("CapEx",row[2],THRESHOLD),
                                 ("D&A",row[3],80.0),("cash_end",row[4],THRESHOLD)]:
            # D&A는 80% 기준 (구조적 한계 감안)
            if val is not None and val < thr:
                row_issues.append(f"{field}={val}%")
        if row_issues:
            cf_issues.append(f"{yr}년: {', '.join(row_issues)}")
        cf_detail.append({
            "year": yr, "ocf": row[1], "capex": row[2],
            "dep": row[3], "cash_end": row[4], "total": row[5]
        })

    results["C2_cf_completeness"] = {
        "desc": "현금흐름 핵심 필드 완결성 (OCF/CapEx/cash≥90%, D&A≥80%)",
        "detail": cf_detail,
        "issues": cf_issues,
        "pass": len(cf_issues) == 0,
    }

    # 불가능한 값 (BS 오류: equity > assets, 10억+ 차이)
    # 금융/증권/보험은 BS 구조가 달라 제외 (FnGuide total_assets=PP&E만 저장하는 오매핑 알려진 문제)
    # DART 직접값 + 의료/바이오 스타트업 IPO 직후 자본 급증 케이스는 실제값으로 허용
    bs_error_cnt = conn.execute("""
        SELECT COUNT(DISTINCT f.stock_code||f.year||f.quarter) FROM financial_data f
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.total_equity > f.total_assets AND f.total_assets > 0
          AND (f.total_equity - f.total_assets) > 1e9
          AND f.year >= 2022 AND f.is_annual=1
          AND f.data_source NOT IN ('dart','dart_redownload')
          AND su.sector_large NOT IN ('금융','보험','은행','금융서비스','증권','카드')
    """).fetchone()[0]

    results["C3_impossible_values"] = {
        "desc": "불가능한 값 (자본>자산 10억+, 2022+, 비금융)",
        "actual": bs_error_cnt,
        "pass": bs_error_cnt == 0,
    }

    # 음수 revenue (비금융, 10억 이상)
    neg_rev_cnt = conn.execute("""
        SELECT COUNT(*) FROM financial_data f
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.revenue < -1e9 AND f.is_annual=0 AND f.report_type='CFS'
          AND f.year >= 2022 AND su.sector_large NOT IN ('금융','보험','은행','금융서비스')
          AND f.stock_code NOT LIKE '9%'
    """).fetchone()[0]

    results["C4_negative_revenue"] = {
        "desc": "음수 revenue 10억+ (비금융, 분기, 2022+)",
        "actual": neg_rev_cnt,
        "pass": neg_rev_cnt == 0,
    }

    return results


# ── 교차 검증 5항목 ──────────────────────────────────────────────────────────
def validate(m):
    results = {}

    sum_all = m["ok_cnt"] + m["open_cnt"] + m["structural_cnt"] + m["ambiguous_cnt"]
    results["V1_sum_check"] = {
        "desc": "대분류합 = 총건수",
        "expected": m["total"], "actual": sum_all,
        "pass": sum_all == m["total"],
    }
    results["V2_tier_sum"] = {
        "desc": "등급합(A+B+C+D+E) = OPEN 총건수",
        "expected": m["open_cnt"], "actual": m["tier_rec_sum"],
        "pass": m["tier_rec_sum"] == m["open_cnt"],
    }
    total_ambig = sum(m["ambig_by_check_type"].values())
    results["V3_ambiguous_zero"] = {
        "desc": "전 check_type AMBIGUOUS = 0",
        "expected": 0, "actual": total_ambig,
        "pass": total_ambig == 0,
        "detail": m["ambig_by_check_type"],
    }
    results["V4_ok_pct"] = {
        "desc": "OK% >= 60.0%",
        "expected": ">= 60.0%", "actual": f"{m['ok_pct']}%",
        "pass": m["ok_pct"] >= 60.0,
    }
    ab_recs = (m["tiers"].get("A", {}).get("records", 0)
             + m["tiers"].get("B", {}).get("records", 0))
    results["V5_ab_open_limit"] = {
        "desc": "A+B급 OPEN 레코드 <= 1,000건",
        "expected": "<= 1000", "actual": ab_recs,
        "pass": ab_recs <= 1000,
    }
    all_pass = all(v["pass"] for v in results.values())
    return results, all_pass


def format_txt(m, vr, comp, all_pass):
    lines = ["=" * 70]
    lines.append(f"QA REPORT  |  {m['snapshot_run_id']}  |  {m['snapshot_ts']}")
    lines.append("=" * 70)
    lines.append(f"QUARTERLY_4WAY  총 {m['total']:,}건")
    lines.append(f"  OK        : {m['ok_cnt']:,}건  ({m['ok_pct']}%)")
    lines.append(f"  OPEN      : {m['open_cnt']:,}건")
    lines.append(f"  STRUCTURAL: {m['structural_cnt']:,}건")
    lines.append(f"  AMBIGUOUS : {m['ambiguous_cnt']:,}건")
    lines.append("")
    lines.append("OPEN 신뢰등급 (단일쿼리 실측):")
    for tier in "ABCDE":
        t = m["tiers"].get(tier)
        if t:
            lines.append(f"  {tier}: {t['stocks']:,}종목  {t['records']:,}레코드")
    lines.append(f"  등급합: {m['tier_rec_sum']:,}건")
    lines.append("")
    lines.append("─" * 70)
    lines.append("[A] 교차 검증 (소스 간 일치)")
    for k, v in vr.items():
        mark = "✅" if v["pass"] else "❌"
        lines.append(f"  {mark} {v['desc']}: {v['actual']} (기대: {v['expected']})")
    lines.append("")
    lines.append("[B] 필드 완결성 (값이 실제로 채워졌는가 — 교차 검증과 별개)")
    for k, v in comp.items():
        mark = "✅" if v["pass"] else "❌"
        issues = v.get("issues", [])
        actual = v.get("actual", "")
        if issues:
            lines.append(f"  {mark} {v['desc']}")
            for iss in issues:
                lines.append(f"       ⚠️  {iss}")
        elif actual != "":
            lines.append(f"  {mark} {v['desc']}: {actual}건")
        else:
            lines.append(f"  {mark} {v['desc']}: 이슈 없음")
    lines.append("")
    lines.append(f"종합: {'✅ ALL PASS' if all_pass else '❌ FAIL — 담당자 확인 필요'}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=f"{ROOT}/scratch/qa_logs")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")

    conn = sqlite3.connect(args.db, timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")

    m            = collect(conn)
    comp         = check_completeness(conn)
    conn.close()

    vr, cross_pass  = validate(m)
    comp_pass       = all(v["pass"] for v in comp.values())
    all_pass        = cross_pass and comp_pass

    payload = {"metrics": m, "validations": vr, "completeness": comp, "all_pass": all_pass}

    json_path = os.path.join(args.out_dir, f"qa_{ts_file}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    txt      = format_txt(m, vr, comp, all_pass)
    txt_path = os.path.join(args.out_dir, f"qa_{ts_file}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)

    for ext, path in [("json", json_path), ("txt", txt_path)]:
        link = os.path.join(args.out_dir, f"latest.{ext}")
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(path, link)

    print(txt)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
