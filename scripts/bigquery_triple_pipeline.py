#!/usr/bin/env python3
"""
BigQuery 3배주/우상향 패턴 일일 파이프라인

기능:
1) v_3x_candidate_screen 뷰를 기반으로 일일 후보 테이블(triple_pattern_daily) 적재
2) 섹터 요약 테이블(triple_pattern_sector_daily) 적재

실행:
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python scripts/bigquery_triple_pipeline.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


PROJECT_ID = os.getenv("BQ_PROJECT_ID", "project-d8a62269-8156-4f96-870")
DATASET_ID = os.getenv("BQ_DATASET_ID", "stock_dashboard")
PATTERN_MIN_SCORE = float(os.getenv("TRIPLE_PATTERN_MIN_SCORE", "62"))
MAX_ROWS = int(os.getenv("TRIPLE_PATTERN_MAX_ROWS", "200"))


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=PROJECT_ID)


def _ensure_tables(client):
    from google.cloud import bigquery

    daily_ref = f"{PROJECT_ID}.{DATASET_ID}.triple_pattern_daily"
    sector_ref = f"{PROJECT_ID}.{DATASET_ID}.triple_pattern_sector_daily"

    daily_schema = [
        bigquery.SchemaField("run_date", "DATE"),
        bigquery.SchemaField("run_ts", "TIMESTAMP"),
        bigquery.SchemaField("stock_code", "STRING"),
        bigquery.SchemaField("stock_name", "STRING"),
        bigquery.SchemaField("sector", "STRING"),
        bigquery.SchemaField("market", "STRING"),
        bigquery.SchemaField("mktcap_100m", "FLOAT"),
        bigquery.SchemaField("per", "FLOAT"),
        bigquery.SchemaField("pbr", "FLOAT"),
        bigquery.SchemaField("roe", "FLOAT"),
        bigquery.SchemaField("op_margin_pct", "FLOAT"),
        bigquery.SchemaField("net_margin_pct", "FLOAT"),
        bigquery.SchemaField("debt_ratio_pct", "FLOAT"),
        bigquery.SchemaField("avg_inst_60d_100m", "FLOAT"),
        bigquery.SchemaField("avg_frn_60d_100m", "FLOAT"),
        bigquery.SchemaField("avg_vol_60d", "FLOAT"),
        bigquery.SchemaField("pct_above_52w_low", "FLOAT"),
        bigquery.SchemaField("pct_below_52w_high", "FLOAT"),
        bigquery.SchemaField("triple_pattern_score", "FLOAT"),
        bigquery.SchemaField("rank_no", "INT64"),
        bigquery.SchemaField("screen_reason", "STRING"),
    ]
    sector_schema = [
        bigquery.SchemaField("run_date", "DATE"),
        bigquery.SchemaField("run_ts", "TIMESTAMP"),
        bigquery.SchemaField("sector", "STRING"),
        bigquery.SchemaField("market", "STRING"),
        bigquery.SchemaField("cnt", "INT64"),
        bigquery.SchemaField("avg_score", "FLOAT"),
        bigquery.SchemaField("avg_mktcap_100m", "FLOAT"),
        bigquery.SchemaField("avg_roe", "FLOAT"),
        bigquery.SchemaField("avg_op_margin_pct", "FLOAT"),
    ]

    for table_ref, schema in ((daily_ref, daily_schema), (sector_ref, sector_schema)):
        try:
            table = client.get_table(table_ref)
            existing = {field.name for field in table.schema}
            missing = [field for field in schema if field.name not in existing]
            if missing:
                table.schema = list(table.schema) + missing
                client.update_table(table, ["schema"])
        except Exception:
            tbl = bigquery.Table(table_ref, schema=schema)
            tbl.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="run_date",
            )
            client.create_table(tbl)


def _run_pipeline(client) -> dict:
    table_daily = f"`{PROJECT_ID}.{DATASET_ID}.triple_pattern_daily`"
    table_sector = f"`{PROJECT_ID}.{DATASET_ID}.triple_pattern_sector_daily`"
    source_view = f"`{PROJECT_ID}.{DATASET_ID}.v_3x_candidate_screen`"

    sql_delete = f"DELETE FROM {table_daily} WHERE run_date = CURRENT_DATE('Asia/Seoul')"
    client.query(sql_delete).result()

    sql_insert = f"""
    INSERT INTO {table_daily}
    (
      run_date, run_ts, stock_code, stock_name, sector, market,
      mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct, debt_ratio_pct,
      avg_inst_60d_100m, avg_frn_60d_100m, avg_vol_60d, pct_above_52w_low, pct_below_52w_high,
      triple_pattern_score, rank_no, screen_reason
    )
    WITH base AS (
      SELECT
        stock_code, stock_name, sector, market,
        mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct, debt_ratio_pct,
        avg_inst_60d_100m, avg_frn_60d_100m, avg_vol_60d,
        pct_above_52w_low, pct_below_52w_high, triple_pattern_score
      FROM {source_view}
      WHERE triple_pattern_score >= @min_score
        AND market IN ('KOSPI', 'KOSDAQ')
        AND mktcap_100m BETWEEN 300 AND 30000
        AND avg_vol_60d > 0
    ),
    ranked AS (
      SELECT
        *,
        ROW_NUMBER() OVER (ORDER BY triple_pattern_score DESC, mktcap_100m ASC, stock_code) AS rank_no
      FROM base
    )
    SELECT
      CURRENT_DATE('Asia/Seoul') AS run_date,
      CURRENT_TIMESTAMP() AS run_ts,
      stock_code, stock_name, sector, market,
      mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct, debt_ratio_pct,
      avg_inst_60d_100m, avg_frn_60d_100m, avg_vol_60d, pct_above_52w_low, pct_below_52w_high,
      triple_pattern_score, rank_no,
      CONCAT(
        'score=', CAST(ROUND(triple_pattern_score,1) AS STRING),
        ', ROE=', CAST(ROUND(roe,1) AS STRING),
        ', OPM=', CAST(ROUND(op_margin_pct,1) AS STRING),
        ', 수급(inst/frn)=', CAST(ROUND(avg_inst_60d_100m,0) AS STRING), '/', CAST(ROUND(avg_frn_60d_100m,0) AS STRING)
      ) AS screen_reason
    FROM ranked
    WHERE rank_no <= @max_rows
    """
    from google.cloud import bigquery
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("min_score", "FLOAT64", PATTERN_MIN_SCORE),
            bigquery.ScalarQueryParameter("max_rows", "INT64", MAX_ROWS),
        ]
    )
    client.query(sql_insert, job_config=job_cfg).result()

    sql_delete_sector = f"DELETE FROM {table_sector} WHERE run_date = CURRENT_DATE('Asia/Seoul')"
    client.query(sql_delete_sector).result()

    sql_insert_sector = f"""
    INSERT INTO {table_sector}
    (run_date, run_ts, sector, market, cnt, avg_score, avg_mktcap_100m, avg_roe, avg_op_margin_pct)
    SELECT
      CURRENT_DATE('Asia/Seoul') AS run_date,
      CURRENT_TIMESTAMP() AS run_ts,
      COALESCE(sector, 'etc') AS sector,
      market,
      COUNT(*) AS cnt,
      ROUND(AVG(triple_pattern_score), 2) AS avg_score,
      ROUND(AVG(mktcap_100m), 2) AS avg_mktcap_100m,
      ROUND(AVG(roe), 2) AS avg_roe,
      ROUND(AVG(op_margin_pct), 2) AS avg_op_margin_pct
    FROM {table_daily}
    WHERE run_date = CURRENT_DATE('Asia/Seoul')
    GROUP BY sector, market
    """
    client.query(sql_insert_sector).result()

    stats_sql = f"""
    SELECT
      COUNT(*) AS cnt,
      ROUND(AVG(triple_pattern_score), 2) AS avg_score,
      ROUND(MAX(triple_pattern_score), 2) AS max_score
    FROM {table_daily}
    WHERE run_date = CURRENT_DATE('Asia/Seoul')
    """
    row = list(client.query(stats_sql).result())[0]
    return {
        "run_date": datetime.now(timezone.utc).astimezone().date().isoformat(),
        "candidate_count": int(row["cnt"] or 0),
        "avg_score": float(row["avg_score"] or 0.0),
        "max_score": float(row["max_score"] or 0.0),
        "min_score_threshold": PATTERN_MIN_SCORE,
    }


def _sync_to_local(rows: list) -> int:
    """BQ triple_pattern_daily 결과를 local stock.db에 동기화."""
    import sqlite3 as _sl
    DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
    conn = _sl.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS triple_pattern_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            sector TEXT,
            market TEXT,
            mktcap_100m REAL,
            per REAL,
            pbr REAL,
            roe REAL,
            op_margin_pct REAL,
            net_margin_pct REAL,
            avg_inst_60d_100m REAL,
            avg_frn_60d_100m REAL,
            triple_pattern_score REAL,
            tenbagger_score REAL,
            rank_no INTEGER,
            screen_reason TEXT,
            updated_at TEXT DEFAULT (DATETIME('now')),
            UNIQUE(run_date, stock_code)
        )
    """)
    saved = 0
    for row in rows:
        if isinstance(row, dict):
            r = row
        else:
            # BigQuery Row object
            r = dict(row)
        conn.execute("""
            INSERT OR REPLACE INTO triple_pattern_daily
            (run_date, stock_code, stock_name, sector, market,
             mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct,
             avg_inst_60d_100m, avg_frn_60d_100m, triple_pattern_score,
             tenbagger_score, rank_no, screen_reason, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,DATETIME('now'))
        """, (
            str(r.get("run_date", "")),
            str(r.get("stock_code", "")),
            r.get("stock_name"), r.get("sector"), r.get("market"),
            r.get("mktcap_100m"), r.get("per"), r.get("pbr"),
            r.get("roe"), r.get("op_margin_pct"), r.get("net_margin_pct"),
            r.get("avg_inst_60d_100m"), r.get("avg_frn_60d_100m"),
            r.get("triple_pattern_score"),
            r.get("tenbagger_score"),
            r.get("rank_no"), r.get("screen_reason"),
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def main():
    client = _client()
    _ensure_tables(client)
    out = _run_pipeline(client)

    # BQ 결과를 local stock.db에도 동기화
    try:
        run_date = out.get("run_date", "")
        # BQ에서 오늘 날짜 행 조회
        table_daily = f"`{PROJECT_ID}`.`{DATASET_ID}`.triple_pattern_daily"
        fetch_sql = f"""
        SELECT stock_code, stock_name, sector, market,
               mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct,
               avg_inst_60d_100m, avg_frn_60d_100m,
               triple_pattern_score, rank_no, screen_reason,
               CAST(run_date AS STRING) AS run_date
        FROM {table_daily}
        WHERE run_date = CURRENT_DATE('Asia/Seoul')
        ORDER BY rank_no
        """
        bq_rows = list(client.query(fetch_sql).result())
        local_saved = _sync_to_local([dict(r) for r in bq_rows])
        out["local_synced"] = local_saved
        print(f"[local_sync] {local_saved}건 stock.db에 동기화 완료", flush=True)
    except Exception as e:
        out["local_sync_error"] = str(e)
        print(f"[local_sync] 오류 (BQ 작업은 완료됨): {e}", flush=True)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
