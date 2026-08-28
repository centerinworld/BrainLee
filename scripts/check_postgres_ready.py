#!/usr/bin/env python3
from __future__ import annotations

import os

import psycopg

EXPECTED_DATA_DIRECTORY = os.getenv(
    "POSTGRES_EXPECTED_DATA_DIRECTORY",
    "/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data",
)

url = os.getenv(
    "POSTGRES_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard"),
)
url = url.replace("postgresql+psycopg://", "postgresql://", 1)

with psycopg.connect(url) as conn:
    row = conn.execute(
        "SELECT current_database(), current_user, version(), "
        "current_setting('data_directory')"
    ).fetchone()
    if row[3] != EXPECTED_DATA_DIRECTORY:
        raise SystemExit(
            f"unexpected PostgreSQL data_directory: {row[3]} "
            f"(expected {EXPECTED_DATA_DIRECTORY})"
        )
    print(
        {
            "database": row[0],
            "user": row[1],
            "version": row[2].split()[0:2],
            "data_directory": row[3],
        }
    )
