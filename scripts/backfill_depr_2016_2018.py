#!/usr/bin/env python3
"""
2016~2018 감가상각비 NULL 보완 — FnGuide SVD_Finance.asp 재수집
대상: cash_flow_data에 CFS 연간 행이 있으나 depreciation=NULL/0인 종목
"""
from __future__ import annotations
import logging, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/Applications/stock_dashboard/logs/backfill_depr_2016_2018.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 대상 종목: 2016~2018 연간 CFS에서 depreciation NULL인 종목
    rows = conn.execute("""
        SELECT DISTINCT cf.stock_code
        FROM cash_flow_data cf
        JOIN stock_universe su ON su.stock_code = cf.stock_code
        WHERE cf.is_annual=1 AND cf.report_type='CFS'
          AND cf.year BETWEEN 2016 AND 2018
          AND (cf.depreciation IS NULL OR cf.depreciation=0)
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY cf.stock_code
    """).fetchall()
    codes = [r[0] for r in rows]
    conn.close()
    log.info("대상 종목: %d개 (2016~2018 감가상각비 NULL)", len(codes))

    from collectors.fnguide_financial_collector import run as fg_run
    
    BATCH = 100
    total_updated = 0
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i+BATCH]
        log.info("[%d/%d] 배치 수집 시작 (%s ~ %s)", i, len(codes), batch[0], batch[-1])
        
        # 임시로 stock_universe에서 이 배치만 처리되도록
        # fnguide_financial_collector의 run()은 stock_universe 전체를 조회하므로
        # 직접 종목별로 호출
        from collectors.fnguide_financial_collector import fetch_fnguide_all, upsert_cashflow, _conn
        
        batch_conn = _conn()
        updated = 0
        for j, code in enumerate(batch):
            try:
                result = fetch_fnguide_all(code, "CFS")
                if not result or not result.get("annual"):
                    continue
                annual_data = result.get("annual", {})
                for yr, ydata in annual_data.items():
                    if not (2016 <= yr <= 2018):
                        continue
                    depr = ydata.get("depreciation")
                    if not depr:
                        continue
                    # depreciation NULL/0인 행만 업데이트
                    existing = batch_conn.execute(
                        "SELECT id, depreciation FROM cash_flow_data "
                        "WHERE stock_code=? AND year=? AND is_annual=1 AND report_type='CFS'",
                        (code, yr)
                    ).fetchone()
                    if existing and (existing["depreciation"] is None or existing["depreciation"] == 0):
                        batch_conn.execute(
                            "UPDATE cash_flow_data SET depreciation=?, data_source='fnguide_depr_backfill' WHERE id=?",
                            (depr, existing["id"])
                        )
                        updated += 1
                        log.debug("  %s %d 감가상각비 %.1f억 업데이트", code, yr, depr/1e8)
                if j % 20 == 0:
                    batch_conn.commit()
                    log.info("  진행: %d/%d, 누적 업데이트 %d건", i+j+1, len(codes), total_updated+updated)
                time.sleep(0.3)  # FnGuide rate limit
            except Exception as e:
                log.warning("  %s 오류: %s", code, e)
                time.sleep(1)
        
        batch_conn.commit()
        total_updated += updated
        log.info("배치 완료: %d건 업데이트 (누적 %d건)", updated, total_updated)
        time.sleep(2)

    log.info("=== 완료: 총 %d건 감가상각비 backfill ===", total_updated)

if __name__ == "__main__":
    main()
