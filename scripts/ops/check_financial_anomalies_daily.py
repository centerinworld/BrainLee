#!/usr/bin/env python3
"""운영 재무 이상값 매일 감시 — 보고만, 수정 금지(2026-10-05, FINANCIAL_STATEMENTS.md §9-2-8).

§9-2-8의 결함(1,000배 단위 오류·미래 기간 행·연결/별도 1,000배)은 화면에 바로 노출되는데 어떤 검사도 잡지 못했다.
검사 3가지(결과 = data_anomaly_daily 테이블 + research_outputs/daily_anomaly/<날짜>.json):
  scale_outlier   : total_assets 가 같은 종목·같은 구분 중앙값의 300배 이상 / 300분의 1 이하
  future_period   : 기간 종료(회계 키 → period_end, 리츠는 DART 표기 그대로 달력 분기)가 이번 달 이후인 분기 행
  cfs_ofs_1000x   : 같은 기간 연결·별도 total_assets 비율이 1,000배·100만 배(±1%) 근처
fs_quirk:dart_unit_error 로 이미 기록된 종목·기간도 집계에 포함하되 known=1 로 구분한다(새로 생긴 것만 경보 대상).
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import fiscal_period as fp  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "daily_anomaly"
DDL = """CREATE TABLE IF NOT EXISTS data_anomaly_daily (check_date TEXT, check_name TEXT, stock_code TEXT, year INTEGER, quarter INTEGER,
    report_type TEXT, detail TEXT, known INTEGER, PRIMARY KEY (check_date, check_name, stock_code, year, quarter, report_type))"""


def main():
    conn = connect_primary_db(timeout=600)
    conn.execute(DDL)
    today = date.today().isoformat()
    this_month = today[:7]
    known = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:dart_unit_error'").fetchall()}
    # 2026-10-07(REVIEW_PLAN §14): 원화 환산을 일부러 보류한 외국기업(보고통화 표시가 '환산완료'가 아닌 종목)도 알려진 보류 — 경보 피로 방지
    known |= {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency' "
                                         "AND config_value NOT LIKE '%환산완료%'").fetchall()}
    found = []
    for r in conn.execute("""WITH m AS (SELECT stock_code, report_type, percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(total_assets)) med
                                        FROM financial_data WHERE total_assets IS NOT NULL AND total_assets<>0 GROUP BY 1,2)
                             SELECT f.stock_code, f.year, f.quarter, f.report_type, f.total_assets/m.med FROM financial_data f JOIN m USING (stock_code, report_type)
                             WHERE f.total_assets IS NOT NULL AND f.total_assets<>0 AND m.med>0
                               AND (abs(f.total_assets)/m.med>=300 OR abs(f.total_assets)/m.med<=1.0/300)""").fetchall():
        r = tuple(r)
        found.append(("scale_outlier", r[0], r[1], r[2], r[3], f"중앙값 대비 {r[4]:.4g}배", int(r[0] in known)))
    fp.fiscal_month_map(conn)
    for t in ("financial_data", "cash_flow_data"):
        for r in conn.execute(f"SELECT DISTINCT stock_code, year, quarter, report_type FROM {t} WHERE NOT is_annual AND quarter IN (1,2,3,4) AND year>=?",
                              (int(today[:4]),)).fetchall():
            code, y, q, fs = tuple(r)
            pe = f"{y}-{q * 3:02d}" if fp.is_reit(code, conn) else fp.period_end(code, y, q, conn)
            if pe > this_month:
                found.append(("future_period", code, y, q, fs, f"{t} 기간 종료 {pe}", 0))
    for r in conn.execute("""SELECT c.stock_code, c.year, c.quarter, c.total_assets/o.total_assets FROM financial_data c JOIN financial_data o
                             ON o.stock_code=c.stock_code AND o.year=c.year AND o.quarter=c.quarter AND o.is_annual=c.is_annual AND o.report_type='OFS'
                             WHERE c.report_type='CFS' AND c.total_assets>0 AND o.total_assets>0""").fetchall():
        code, y, q, ratio = tuple(r)
        if any(abs(ratio / k - 1) < 0.01 for k in (1e3, 1e6, 1e-3, 1e-6)):
            found.append(("cfs_ofs_1000x", code, y, q, "CFS/OFS", f"연결/별도 = {ratio:.4g}", int(code in known)))
    conn.execute("DELETE FROM data_anomaly_daily WHERE check_date=?", (today,))
    rows = list({x[:5]: x for x in found}.values())
    if rows:
        conn.executemany("INSERT INTO data_anomaly_daily VALUES (?,?,?,?,?,?,?,?)", [(today,) + x for x in rows])
    conn.commit()
    summary = {}
    for x in rows:
        s = summary.setdefault(x[0], {"total": 0, "new": 0})
        s["total"] += 1
        s["new"] += 0 if x[6] else 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today}.json").write_text(json.dumps({"date": today, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str))
    print(datetime.now().isoformat(timespec="seconds"), "재무 이상 감시(보고만):", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
