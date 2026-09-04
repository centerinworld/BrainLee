"""
migrate_datasource.py — financial_data / cash_flow_data에 data_source 컬럼 추가 및 FnGuide 레코드 마킹

실행: python3 migrate_datasource.py
결과: verification/migration_datasource_YYYYMMDD_HHMMSS.json 에 저장
"""
import sqlite3, json, datetime, os, sys

DB_PATH     = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
VERIFY_DIR  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/verification"
os.makedirs(VERIFY_DIR, exist_ok=True)

TIMESTAMP   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
REPORT_PATH = f"{VERIFY_DIR}/migration_datasource_{TIMESTAMP}.json"

def log(msg): print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


def run():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    report = {"timestamp": TIMESTAMP, "steps": []}

    # ── Step 1: 컬럼 추가 ────────────────────────────────────────────────────
    log("Step 1: data_source 컬럼 추가")
    for table in ("financial_data", "cash_flow_data"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN data_source TEXT")
            conn.commit()
            log(f"  [{table}] data_source 컬럼 추가 완료")
            report["steps"].append({"step": f"alter_{table}", "result": "added"})
        except sqlite3.OperationalError:
            log(f"  [{table}] data_source 컬럼 이미 존재 — 스킵")
            report["steps"].append({"step": f"alter_{table}", "result": "already_exists"})

    # ── Step 2: FnGuide 스냅샷 기반 financial_data 마킹 ─────────────────────
    log("Step 2: financial_data → data_source='fnguide' 마킹")

    # financial_source_snapshot과 financial_data의 JOIN 기준:
    # stock_code + year + quarter + is_annual + report_type
    cur = conn.execute("""
        UPDATE financial_data
        SET    data_source = 'fnguide'
        WHERE  data_source IS NULL
          AND  (stock_code, year, quarter, is_annual, report_type) IN (
                  SELECT stock_code, year, quarter, is_annual, report_type
                  FROM   financial_source_snapshot
                  WHERE  data_source = 'fnguide'
               )
    """)
    fin_marked = cur.rowcount
    conn.commit()
    log(f"  financial_data fnguide 마킹: {fin_marked}건")
    report["steps"].append({"step": "mark_financial_fnguide", "rows_marked": fin_marked})

    # ── Step 3: FnGuide 스냅샷 기반 cash_flow_data 마킹 ─────────────────────
    log("Step 3: cash_flow_data → data_source='fnguide' 마킹")
    cur = conn.execute("""
        UPDATE cash_flow_data
        SET    data_source = 'fnguide'
        WHERE  data_source IS NULL
          AND  (stock_code, year, quarter, is_annual, report_type) IN (
                  SELECT stock_code, year, quarter, is_annual, report_type
                  FROM   financial_source_snapshot
                  WHERE  data_source = 'fnguide'
               )
    """)
    cf_marked = cur.rowcount
    conn.commit()
    log(f"  cash_flow_data fnguide 마킹: {cf_marked}건")
    report["steps"].append({"step": "mark_cashflow_fnguide", "rows_marked": cf_marked})

    # ── Step 4: 마킹 결과 검증 ───────────────────────────────────────────────
    log("Step 4: 마킹 결과 검증")
    stats = {}

    rows = conn.execute("""
        SELECT COALESCE(data_source,'unknown') AS src, COUNT(*) AS cnt, COUNT(DISTINCT stock_code) AS stocks
        FROM financial_data GROUP BY 1
    """).fetchall()
    stats["financial_data_by_source"] = {r[0]: {"records": r[1], "stocks": r[2]} for r in rows}

    rows2 = conn.execute("""
        SELECT COALESCE(data_source,'unknown') AS src, COUNT(*) AS cnt, COUNT(DISTINCT stock_code) AS stocks
        FROM cash_flow_data GROUP BY 1
    """).fetchall()
    stats["cash_flow_data_by_source"] = {r[0]: {"records": r[1], "stocks": r[2]} for r in rows2}

    # fnguide 마킹된 레코드의 NULL 비율 확인
    fn_null = conn.execute("""
        SELECT
            SUM(CASE WHEN revenue IS NULL THEN 1 ELSE 0 END)          rev_null,
            SUM(CASE WHEN operating_profit IS NULL THEN 1 ELSE 0 END) op_null,
            SUM(CASE WHEN net_income IS NULL THEN 1 ELSE 0 END)       ni_null,
            SUM(CASE WHEN total_assets IS NULL THEN 1 ELSE 0 END)     asset_null,
            COUNT(*) total
        FROM financial_data WHERE data_source = 'fnguide'
    """).fetchone()
    if fn_null:
        stats["fnguide_null_check"] = {
            "total": fn_null[4],
            "rev_null": fn_null[0], "op_null": fn_null[1],
            "ni_null": fn_null[2],  "asset_null": fn_null[3],
        }

    report["stats"] = stats
    conn.close()

    # ── 리포트 저장 ──────────────────────────────────────────────────────────
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log(f"\n완료! 리포트 저장: {REPORT_PATH}")
    log(f"  financial_data fnguide: {stats['financial_data_by_source'].get('fnguide', {})}")
    log(f"  financial_data 미분류:  {stats['financial_data_by_source'].get('unknown', {})}")
    log(f"  cash_flow_data fnguide: {stats['cash_flow_data_by_source'].get('fnguide', {})}")

    return report


if __name__ == "__main__":
    run()
