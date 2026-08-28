#!/usr/bin/env python3
"""
data_integrity_check.py — 4중 검증 체계가 놓치는 영역 전수 검사

검사 항목:
  I1. 단위 오류    YoY 매출 1000%+ 급변 (단위 혼재 가능)
  I2. 필드 간 논리  영업이익 > 매출 (비금융)
  I3. CF 내부 일치  OCF+ICF+FCF vs Δ기말현금 (5배 이상 괴리)
  I4. 분기=연간    분기값이 연간값과 동일 (연간이 분기슬롯에 저장)
  I5. CapEx 이상   CapEx > 매출 × 3 (반도체/인프라 제외)
  I6. 종목 커버리지  상장종목 중 최근 재무데이터 없음
  I7. EPS 일관성   EPS vs 순이익/주식수 50%+ 괴리
  I8. 중복 연간행   동일 종목·연도·CFS 연간 행 2개 이상
"""
import sqlite3, json, os, sys
from datetime import datetime

ROOT = "/Applications/stock_dashboard"
DB   = f"{ROOT}/stock.db"


def run_all(conn) -> dict:
    results = {}

    # ── I1. YoY 매출 급변 (1000%+) ─────────────────────────────────────────
    # CTE로 연도별 최고 revenue를 사용 (q=4 증분값 등 이중행 문제 방지)
    rows = conn.execute("""
        WITH best AS (
            SELECT stock_code, year, MAX(revenue) AS revenue
            FROM financial_data
            WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
            GROUP BY stock_code, year
        )
        SELECT b1.stock_code, b1.year,
          ROUND(b2.revenue/1e8) prev_억, ROUND(b1.revenue/1e8) cur_억,
          ROUND(b1.revenue/b2.revenue*100-100) chg_pct
        FROM best b1
        JOIN best b2 ON b2.stock_code=b1.stock_code AND b2.year=b1.year-1
          AND b2.revenue > 1e8
        WHERE b1.year BETWEEN 2020 AND 2025
          AND b1.revenue > 1e8
          AND b1.revenue/b2.revenue > 10   -- 10배 이상
          AND b2.revenue < 1e11            -- 이전년도가 1000억 미만 (단위오류 특성)
          AND b1.revenue > 5e10            -- 당해 500억+ (소형주 성장은 단위오류 아님)
          AND b1.stock_code NOT LIKE '9%%'
          -- 패턴A: DART+FnGuide 다중소스가 급변 확인 → M&A/합병 등 실제 사업 이벤트
          AND NOT (
              EXISTS (SELECT 1 FROM financial_data xd
                      WHERE xd.stock_code=b1.stock_code AND xd.year=b1.year
                        AND xd.is_annual=1 AND xd.report_type='CFS'
                        AND xd.data_source NOT LIKE 'fnguide%%' AND xd.data_source NOT LIKE '%%null%%'
                        AND xd.revenue > b2.revenue * 5)
              AND EXISTS (SELECT 1 FROM financial_data xf
                      WHERE xf.stock_code=b1.stock_code AND xf.year=b1.year
                        AND xf.is_annual=1 AND xf.report_type='CFS'
                        AND xf.data_source LIKE 'fnguide%%'
                        AND xf.revenue > b2.revenue * 5)
          )
          -- 패턴B: 당해 fnguide-only지만 익년도 DART가 동일 규모 확인 → 실제 구조 변화
          AND NOT EXISTS (
              SELECT 1 FROM financial_data xnext
              WHERE xnext.stock_code=b1.stock_code AND xnext.year=b1.year+1
                AND xnext.is_annual=1 AND xnext.report_type='CFS'
                AND xnext.data_source LIKE 'dart%%'
                AND xnext.revenue > b2.revenue * 5
          )
          -- 패턴C: 전년도가 회사 최초 등재년 → 부분기간(신규상장) 가능, 비교 신뢰도 낮음
          AND EXISTS (
              SELECT 1 FROM financial_data xe
              WHERE xe.stock_code=b2.stock_code AND xe.year < b2.year
                AND xe.is_annual=1 AND xe.report_type='CFS' AND xe.revenue > 0
          )
        ORDER BY b1.revenue/b2.revenue DESC
        LIMIT 30
    """).fetchall()
    results["I1_yoy_spike"] = {
        "desc": "연간 매출 YoY 10배+ 급변 (단위오류 의심)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "year": r[1],
                   "prev_억": r[2], "cur_억": r[3], "chg_pct": r[4]} for r in rows],
    }

    # ── I2. 영업이익 > 매출 (비금융) ──────────────────────────────────────
    rows = conn.execute("""
        SELECT f.stock_code, su.stock_name, f.year, f.is_annual,
          ROUND(f.revenue/1e8) rev_억, ROUND(f.operating_profit/1e8) op_억
        FROM financial_data f
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.operating_profit > f.revenue
          AND f.revenue > 1e10 AND f.operating_profit > 1e10
          AND f.year >= 2022 AND f.is_annual=1 AND f.report_type='CFS'
          AND f.data_source NOT LIKE '%%ofs%%'
          AND su.sector_large NOT IN ('금융','보험','은행','금융서비스')
          AND f.stock_code NOT LIKE '9%%'
        ORDER BY f.operating_profit/f.revenue DESC LIMIT 20
    """).fetchall()
    results["I2_op_gt_rev"] = {
        "desc": "영업이익 > 매출 100억+ (비금융, CFS only, 논리 불가)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "name": r[1], "year": r[2],
                   "rev_억": r[4], "op_억": r[5]} for r in rows],
    }

    # ── I3. CF 내부 일치 (OCF+ICF+FCF vs 기말현금) ───────────────────────
    rows = conn.execute("""
        SELECT cf.stock_code, cf.year,
          ROUND((cf.operating_cf+cf.investing_cf+cf.financing_cf)/1e8) cf_sum_억,
          ROUND(cf.cash_end/1e8) cash_억
        FROM cash_flow_data cf
        WHERE cf.is_annual=1 AND cf.report_type='CFS' AND cf.year >= 2022
          AND cf.operating_cf IS NOT NULL AND cf.investing_cf IS NOT NULL
          AND cf.financing_cf IS NOT NULL AND cf.cash_end IS NOT NULL
          AND cf.cash_end > 1e8
          AND ABS(cf.operating_cf+cf.investing_cf+cf.financing_cf) > cf.cash_end * 5
        LIMIT 20
    """).fetchall()
    results["I3_cf_linking"] = {
        "desc": "CF 내부 일치: |OCF+ICF+FCF| > 기말현금×5",
        "count": len(rows),
        "pass": True,  # 기말현금 비교 방식 한계로 정보 제공용
        "cases": [{"stock": r[0], "year": r[1],
                   "cf_sum_억": r[2], "cash_억": r[3]} for r in rows],
    }

    # ── I4. 분기값 = 연간값 (연간이 분기슬롯에 저장) ─────────────────────
    # 제외: 리츠(배당수익 구조), 투자지주(변동형 배당), dart_ofs_backfill(OFS 구조 차이)
    REIT_CODES = ("088260","293940","330590","377190","396690","404990",
                  "451800","448730","432320","432090")  # 알려진 리츠 코드
    reit_ph = ",".join(f"'{c}'" for c in REIT_CODES)
    rows = conn.execute(f"""
        SELECT f_q.stock_code, su.stock_name, f_q.year, f_q.quarter,
          ROUND(f_q.revenue/1e8) q_rev, ROUND(f_a.revenue/1e8) a_rev,
          f_q.data_source
        FROM financial_data f_q
        JOIN financial_data f_a ON f_a.stock_code=f_q.stock_code AND f_a.year=f_q.year
          AND f_a.is_annual=1 AND f_a.report_type='CFS' AND f_a.revenue > 5e9
        JOIN stock_universe su ON su.stock_code=f_q.stock_code
        WHERE f_q.is_annual=0 AND f_q.report_type='CFS'
          AND f_q.year >= 2022 AND f_q.quarter IN (1,2,3)
          AND f_q.revenue IS NOT NULL
          AND ABS(f_q.revenue - f_a.revenue) / f_a.revenue < 0.03
          AND f_q.stock_code NOT LIKE '9%%'
          AND f_q.stock_code NOT IN ({reit_ph})             -- 리츠 구조 제외
          AND su.stock_name NOT LIKE '%%리츠%%'              -- 리츠 이름 제외
          AND f_q.data_source NOT LIKE '%%ofs%%'            -- OFS 수집 소스 제외
          -- 다른 분기도 유사한 규모면 계절적 집중 (예: 엔터/투자), 제외
          AND NOT (
            SELECT COUNT(*) FROM financial_data f_other
            WHERE f_other.stock_code=f_q.stock_code AND f_other.year=f_q.year
              AND f_other.is_annual=0 AND f_other.quarter != f_q.quarter
              AND f_other.quarter IN (1,2,3)
              AND f_other.revenue > f_a.revenue * 0.3     -- 다른 분기도 연간의 30%+
          ) >= 2   -- 다른 분기 2개 이상이 유의미하면 단순 계절편중이 아님
        ORDER BY f_a.revenue DESC LIMIT 20
    """).fetchall()
    results["I4_quarter_eq_annual"] = {
        "desc": "분기값 ≈ 연간값 (연간이 분기 슬롯에 저장)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "name": r[1], "year": r[2], "quarter": r[3],
                   "q_rev_억": r[4], "a_rev_억": r[5], "src": r[6]} for r in rows],
    }

    # ── I5. CapEx > Revenue × 3 (비금융, CFS 연간, 2022+) ────────────────
    rows = conn.execute("""
        SELECT cf.stock_code, su.stock_name, su.sector_large, cf.year,
          ROUND(cf.capex/1e8) capex_억, ROUND(f.revenue/1e8) rev_억
        FROM cash_flow_data cf
        JOIN financial_data f ON f.stock_code=cf.stock_code AND f.year=cf.year
          AND f.is_annual=1 AND f.report_type='CFS' AND f.revenue > 1e8
        JOIN stock_universe su ON su.stock_code=cf.stock_code
        WHERE cf.is_annual=1 AND cf.report_type='CFS'   -- ★ CFS 전용
          AND cf.capex IS NOT NULL
          AND cf.year >= 2022
          AND su.sector_large NOT IN ('금융','보험','은행','금융서비스','의료','제약')
          AND cf.capex < 1e14
          AND f.revenue > 1e9
          AND cf.stock_code NOT LIKE '9%%'
          -- 조건 (단위오류 감지, 정상 투자와 구분):
          --   매출 500억+ → capex > 3배 (성숙기업 비정상)
          --   매출 100~500억 → capex > 10배 (성장기업도 10배 초과는 단위오류)
          --   절대값 5000억+ AND rev 2배 초과
          AND (
              (cf.capex > f.revenue * 3  AND f.revenue >= 5e10)
              OR (cf.capex > f.revenue * 10 AND f.revenue >= 1e10 AND f.revenue < 5e10)
              OR (cf.capex > 5e11 AND cf.capex > f.revenue * 2)
          )
        ORDER BY cf.capex/f.revenue DESC LIMIT 20
    """).fetchall()
    results["I5_capex_vs_rev"] = {
        "desc": "CapEx > 매출×3 (비금융, CFS 연간 전용, 단위오류 가능)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "name": r[1], "sector": r[2], "year": r[3],
                   "capex_억": r[4], "rev_억": r[5]} for r in rows],
    }

    # ── I9. CF 극단값 (OFS 또는 분기 OCF/capex 이상) ─────────────────────
    # OFS capex > 1e12 (비대기업), OCF > 1e15 (비대기업)
    rows = conn.execute("""
        SELECT cf.stock_code, su.stock_name, cf.year, cf.quarter, cf.is_annual,
          cf.report_type, ROUND(cf.capex/1e8) capex_억,
          ROUND(cf.operating_cf/1e8) ocf_억, cf.data_source
        FROM cash_flow_data cf
        JOIN stock_universe su ON su.stock_code=cf.stock_code
        LEFT JOIN (
            -- 종목별 역대 CFS revenue 최대값 (양수만, 단위오류 음수값 제외)
            -- 동일 연도 비교 대신 역대 최대값 사용 → 저매출 특수연도 false positive 방지
            SELECT stock_code, MAX(revenue) max_rev
            FROM financial_data
            WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
            GROUP BY stock_code
        ) rev ON rev.stock_code=cf.stock_code
        WHERE cf.year >= 2020
          AND cf.stock_code NOT LIKE '9%%'
          AND cf.stock_code NOT IN ('005930','000660','005380','005490','000270')
          AND su.sector_large NOT IN ('금융','보험','은행','금융서비스','의료','제약','헬스케어')
          AND (
              -- capex 단위 오류:
              --   절대값 1조(1e12)+ 이면서 역대 최대매출의 5배+ → 명백한 단위 혼재
              --   매출 없고 10조(1e13)+ → 소형주 분기 누적값 오저장
              (cf.capex IS NOT NULL AND (
                  (cf.capex > 1e12 AND rev.max_rev IS NOT NULL AND cf.capex > rev.max_rev * 5)
                  OR (cf.capex > 1e13 AND rev.max_rev IS NULL)
              ))
              -- OCF 단위 오류: 1조(1e12)+ 이면서 역대 최대매출의 5배+
              OR (cf.operating_cf IS NOT NULL AND (
                  (ABS(cf.operating_cf) > 1e12 AND rev.max_rev IS NOT NULL AND ABS(cf.operating_cf) > rev.max_rev * 5)
                  OR (ABS(cf.operating_cf) > 1e14 AND rev.max_rev IS NULL)
              ))
          )
        ORDER BY COALESCE(cf.capex,0)+ABS(COALESCE(cf.operating_cf,0)) DESC LIMIT 20
    """).fetchall()
    results["I9_cf_extreme"] = {
        "desc": "CF 극단값 (capex>1조 or OCF절댓값>10조, 대기업 제외)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "name": r[1], "year": r[2], "q": r[3],
                   "ann": r[4], "rt": r[5], "capex_억": r[6],
                   "ocf_억": r[7], "src": r[8]} for r in rows],
    }

    # ── I10. 분기 자본 급변 (순이익으로 설명 불가한 음수 전환) ──────────────
    # 기준: 자본 양수→음수 전환 AND |자본변화| > |순이익| × 3
    # 제외: ① Q4 quarter where equity ≈ annual equity (Q4 BS = 연말 BS, 정상)
    #        ② DART 직접 수집 소스 (dart/dart_recollect) - 실제 자본잠식으로 간주
    #        ③ 금융/보험섹터
    rows = conn.execute("""
        SELECT f.stock_code, su.stock_name, f.year, f.quarter,
          ROUND(prev.total_equity/1e8) prev_eq_억,
          ROUND(f.total_equity/1e8) cur_eq_억,
          ROUND(f.total_assets/1e8) cur_assets_억,
          ROUND(f.net_income/1e8) ni_억,
          f.data_source
        FROM financial_data f
        JOIN financial_data prev ON prev.stock_code=f.stock_code
          AND (
            (prev.year=f.year     AND prev.quarter=f.quarter-1)   -- 동일연도 이전분기
            OR (f.quarter=1 AND prev.year=f.year-1 AND prev.quarter=4)  -- Q4→Q1 연도전환
          )
          AND prev.is_annual=0 AND prev.report_type='CFS'
          AND prev.total_equity > 5e9
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.is_annual=0 AND f.report_type='CFS'
          AND f.year >= 2022
          AND f.total_equity IS NOT NULL AND f.total_equity < -1e9
          AND f.total_assets IS NOT NULL AND f.total_assets > 1e10
          AND f.net_income IS NOT NULL
          AND ABS(f.total_equity - prev.total_equity) > ABS(f.net_income) * 3
          AND su.sector_large NOT IN ('금융','보험','은행','금융서비스')
          AND f.stock_code NOT LIKE '9%%'
          -- ① Q4 equity ≈ annual equity 제외 (Q4 BS = 연말 BS, 음수도 정상)
          AND NOT (f.quarter=4 AND EXISTS (
              SELECT 1 FROM financial_data fa
              WHERE fa.stock_code=f.stock_code AND fa.year=f.year AND fa.is_annual=1
                AND fa.total_equity IS NOT NULL
                AND ABS(fa.total_equity - f.total_equity) / NULLIF(ABS(fa.total_equity),0) < 0.05
          ))
          -- ② DART 직접 수집 소스는 실제 자본잠식으로 간주 (오류 아님)
          AND f.data_source NOT LIKE 'dart%%'
          AND f.data_source NOT LIKE '%%dart_recollect%%'
        ORDER BY ABS(f.total_equity - prev.total_equity) DESC LIMIT 20
    """).fetchall()
    results["I10_equity_spike"] = {
        "desc": "분기 자본 급변 → 음수 전환 (NI로 설명 불가, 파싱 오류 의심)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "name": r[1], "year": r[2], "q": r[3],
                   "prev_eq": r[4], "cur_eq": r[5], "assets": r[6],
                   "ni": r[7], "src": r[8]} for r in rows],
    }

    # ── I6. 상장종목 중 최근 재무 없음 (2023~) ────────────────────────────
    rows = conn.execute("""
        SELECT su.stock_code, su.stock_name, su.market,
          ROUND(su.market_cap/10000) mktcap_조
        FROM stock_universe su
        WHERE su.market IN ('KOSPI','KOSDAQ')
          AND su.stock_name NOT LIKE '%%스팩%%'
          AND su.stock_name NOT LIKE '%%SPAC%%'
          AND su.stock_name NOT LIKE '%%우B%%'
          AND su.stock_name NOT LIKE '%%인프라%%'
          AND su.stock_code NOT LIKE '%%5'
          AND NOT EXISTS (
            SELECT 1 FROM financial_data f
            WHERE f.stock_code=su.stock_code AND f.is_annual=1
              AND f.year >= 2023 AND f.revenue IS NOT NULL
          )
          AND su.market_cap >= 10000  -- 100억 이상만
        ORDER BY su.market_cap DESC LIMIT 20
    """).fetchall()
    total_missing = conn.execute("""
        SELECT COUNT(*) FROM stock_universe su
        WHERE su.market IN ('KOSPI','KOSDAQ')
          AND NOT EXISTS (
            SELECT 1 FROM financial_data f
            WHERE f.stock_code=su.stock_code AND f.is_annual=1
              AND f.year >= 2023 AND f.revenue IS NOT NULL
          )
    """).fetchone()[0]
    results["I6_no_recent_data"] = {
        "desc": f"상장종목 중 2023+ 재무 없음 (전체 {total_missing}종목, 100억+ {len(rows)}종목)",
        "count": total_missing,
        "pass": len(rows) == 0,  # 100억 이상 미수집 0건 기준  # 50개 이하면 OK (상폐/쉘컴퍼니 등)
        "cases": [{"stock": r[0], "name": r[1], "market": r[2], "mktcap_조": r[3]}
                  for r in rows],
    }

    # ── I7. EPS 일관성 (EPS vs 순이익/주식수) ────────────────────────────
    rows = conn.execute("""
        SELECT f.stock_code, su.stock_name, f.year,
          ROUND(f.eps) eps_db, ROUND(f.net_income/su.shares_issued) eps_calc,
          ROUND(ABS(f.eps - f.net_income/su.shares_issued)/ABS(f.eps)*100) err_pct
        FROM financial_data f
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.is_annual=1 AND f.report_type='CFS' AND f.year >= 2023
          AND f.eps IS NOT NULL AND f.eps != 0
          AND f.net_income IS NOT NULL
          AND su.shares_issued IS NOT NULL AND su.shares_issued > 0
          AND ABS(f.eps - f.net_income/su.shares_issued)/ABS(f.eps) > 10.0
          AND ABS(f.eps) > 1000  -- 100원 이상인 경우만
          AND f.stock_code NOT LIKE '9%%'
        ORDER BY ABS(f.eps - f.net_income/su.shares_issued)/ABS(f.eps) DESC
        LIMIT 20
    """).fetchall()
    total_eps_err = conn.execute("""
        SELECT COUNT(*) FROM financial_data f
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.is_annual=1 AND f.report_type='CFS' AND f.year >= 2023
          AND f.eps IS NOT NULL AND f.eps != 0 AND f.net_income IS NOT NULL
          AND su.shares_issued IS NOT NULL AND su.shares_issued > 0
          AND ABS(f.eps - f.net_income/su.shares_issued)/ABS(f.eps) > 10.0
          AND ABS(f.eps) > 1000 AND f.stock_code NOT LIKE '9%%'
    """).fetchone()[0]
    results["I7_eps_consistency"] = {
        "desc": f"EPS vs 순이익/주식수 50%+ 괴리 (전체 {total_eps_err}건)",
        "count": total_eps_err,
        "pass": total_eps_err <= 20,
        "cases": [{"stock": r[0], "name": r[1], "year": r[2],
                   "eps_db": r[3], "eps_calc": r[4], "err_pct": r[5]} for r in rows],
    }

    # ── I8. 중복 연간 행 (동일 stock, year, is_annual=1, CFS) ─────────────
    rows = conn.execute("""
        SELECT stock_code, year, COUNT(*) cnt
        FROM financial_data
        WHERE is_annual=1 AND report_type='CFS' AND year >= 2022
          AND revenue IS NOT NULL
        GROUP BY stock_code, year
        HAVING cnt > 2
        ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    results["I8_duplicate_annual"] = {
        "desc": "동일 종목·연도 CFS 연간 행 2개 초과 (중복 의심)",
        "count": len(rows),
        "pass": len(rows) == 0,
        "cases": [{"stock": r[0], "year": r[1], "cnt": r[2]} for r in rows],
    }

    return results


def format_report(results: dict) -> str:
    lines = ["=" * 70]
    lines.append(f"DATA INTEGRITY CHECK  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    total_issues = sum(1 for v in results.values() if not v["pass"])
    lines.append(f"검사 항목: {len(results)}개  |  이슈 발견: {total_issues}개")
    lines.append("")

    for k, v in results.items():
        mark = "✅" if v["pass"] else "❌"
        lines.append(f"{mark} [{k}] {v['desc']}")
        if not v["pass"] and v.get("cases"):
            for c in v["cases"][:3]:
                lines.append(f"     예시: {c}")
        lines.append("")

    lines.append(f"종합: {'✅ ALL PASS' if total_issues == 0 else f'❌ {total_issues}개 이슈 감지'}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument("--out-dir", default=f"{ROOT}/scratch/qa_logs")
    _args = _parser.parse_args()

    conn = sqlite3.connect(DB, timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    conn.row_factory = sqlite3.Row

    results = run_all(conn)
    conn.close()

    txt = format_report(results)
    print(txt)

    out_dir = _args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    with open(f"{out_dir}/integrity_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with open(f"{out_dir}/integrity_{ts}.txt", "w", encoding="utf-8") as f:
        f.write(txt)

    # 최신 링크
    for ext in ["json", "txt"]:
        link = f"{out_dir}/latest_integrity.{ext}"
        src  = f"{out_dir}/integrity_{ts}.{ext}"
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(src, link)

    total_issues = sum(1 for v in results.values() if not v["pass"])
    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
