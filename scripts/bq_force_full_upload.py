#!/usr/bin/env python3
"""Force full SQLite table uploads to BigQuery.

This is for large historical tables where repeated partitioned chunk loads can
hit BigQuery partition-modification quotas. It drops/recreates target tables and
loads chunks into a non-partitioned table so row completeness wins.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
PROJECT = "project-d8a62269-8156-4f96-870"
DATASET = "stock_dashboard"
CHUNK_SIZE = 250_000

DATE_LIKE_COLUMNS = {
    "date", "dt", "bas_dt", "created_at", "updated_at", "collected_at",
    "disclosed_at", "contract_start", "contract_end",
}


def sanitize(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).replace(" ", "_").replace("-", "_").replace(".", "_") for c in df.columns]
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).where(df[col].notna(), None).replace("None", None)
        elif str(df[col].dtype).startswith("float"):
            df[col] = df[col].replace([float("inf"), float("-inf")], None)
        if col.lower() in DATE_LIKE_COLUMNS:
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass
    return df


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def upload_table(client: bigquery.Client, conn: sqlite3.Connection, table: str) -> dict:
    ref = f"{PROJECT}.{DATASET}.{table}"
    try:
        client.delete_table(ref)
        print(f"[{table}] dropped existing BigQuery table")
    except NotFound:
        pass

    total = table_count(conn, table)
    uploaded = 0
    first = True
    cfg = bigquery.LoadJobConfig(
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    for df in pd.read_sql_query(f'SELECT * FROM "{table}"', conn, chunksize=CHUNK_SIZE):
        df = sanitize(df)
        job = client.load_table_from_dataframe(df, ref, job_config=cfg)
        job.result()
        uploaded += len(df)
        if first:
            cfg.write_disposition = bigquery.WriteDisposition.WRITE_APPEND
            first = False
        print(f"[{table}] {uploaded:,}/{total:,}")

    bq_rows = client.get_table(ref).num_rows if total else 0
    return {"table": table, "local_rows": total, "bq_rows": bq_rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", nargs="+")
    args = ap.parse_args()

    client = bigquery.Client(project=PROJECT)
    conn = sqlite3.connect(DB)
    try:
        results = []
        for table in args.tables:
            results.append(upload_table(client, conn, table))
        print("[DONE]", results)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
