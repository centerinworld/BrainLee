from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
EXPORT_DIR = ROOT_DIR / "data" / "mapping_exports"
DOWNLOADS_DIR = Path("/Users/brainlee/Downloads")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis2_sector_monthly_cache (
            sector_key TEXT NOT NULL,
            sector_label TEXT NOT NULL,
            period_ym TEXT NOT NULL,
            export_value REAL NOT NULL DEFAULT 0,
            export_weight REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            import_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (sector_key, period_ym)
        );

        CREATE TABLE IF NOT EXISTS analysis2_sector_hs_monthly_cache (
            sector_key TEXT NOT NULL,
            sector_label TEXT NOT NULL,
            period_ym TEXT NOT NULL,
            hs_code TEXT NOT NULL,
            hs_name TEXT NOT NULL,
            mapping_status TEXT NOT NULL DEFAULT 'provisional',
            export_value REAL NOT NULL DEFAULT 0,
            export_weight REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            import_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (sector_key, period_ym, hs_code)
        );

        CREATE TABLE IF NOT EXISTS analysis2_company_monthly_cache (
            sector_key TEXT NOT NULL,
            sector_label TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            period_ym TEXT NOT NULL,
            sector_names TEXT NOT NULL DEFAULT '',
            hs_names TEXT NOT NULL DEFAULT '',
            mapping_status TEXT NOT NULL DEFAULT 'provisional',
            export_value REAL NOT NULL DEFAULT 0,
            export_weight REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            import_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (sector_key, stock_code, period_ym)
        );

        CREATE TABLE IF NOT EXISTS analysis2_company_hs_monthly_cache (
            sector_key TEXT NOT NULL,
            sector_label TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            period_ym TEXT NOT NULL,
            hs_code TEXT NOT NULL,
            hs_name TEXT NOT NULL,
            mapping_status TEXT NOT NULL DEFAULT 'provisional',
            export_value REAL NOT NULL DEFAULT 0,
            export_weight REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            import_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (sector_key, stock_code, period_ym, hs_code)
        );

        CREATE INDEX IF NOT EXISTS ix_analysis2_sector_monthly_period
            ON analysis2_sector_monthly_cache (period_ym);
        CREATE INDEX IF NOT EXISTS ix_analysis2_sector_hs_monthly_period
            ON analysis2_sector_hs_monthly_cache (sector_key, period_ym);
        CREATE INDEX IF NOT EXISTS ix_analysis2_company_monthly_period
            ON analysis2_company_monthly_cache (sector_key, stock_code, period_ym);
        CREATE INDEX IF NOT EXISTS ix_analysis2_company_hs_monthly_period
            ON analysis2_company_hs_monthly_cache (sector_key, stock_code, period_ym);
        """
    )


def rebuild_cache(conn: sqlite3.Connection) -> dict[str, int]:
    conn.executescript(
        """
        DELETE FROM analysis2_sector_monthly_cache;
        DELETE FROM analysis2_sector_hs_monthly_cache;
        DELETE FROM analysis2_company_monthly_cache;
        DELETE FROM analysis2_company_hs_monthly_cache;

        DROP TABLE IF EXISTS tmp_sector_match;
        CREATE TEMP TABLE tmp_sector_match AS
        WITH ranked AS (
            SELECT
                cmr.row_key,
                cmr.period_ym,
                cmr.export_value,
                cmr.export_weight,
                cmr.import_value,
                cmr.import_weight,
                sp.sector_key,
                sp.label AS sector_label,
                sm.hs_code,
                COALESCE(NULLIF(sm.display_name, ''), NULLIF(sm.hs_name, ''), sm.hs_code) AS hs_name,
                sm.mapping_status,
                ROW_NUMBER() OVER (
                    PARTITION BY cmr.row_key, sp.sector_key
                    ORDER BY LENGTH(sm.hs_code) DESC,
                             CASE sm.mapping_status
                               WHEN 'exact' THEN 1
                               WHEN 'composite' THEN 2
                               ELSE 3 END,
                             sm.hs_code
                ) AS rn
            FROM customs_monthly_record cmr
            JOIN hs_sector_map sm
              ON cmr.hs_code = sm.hs_code
              OR cmr.hs_code LIKE sm.hs_code || '%'
            JOIN sector_preset sp
              ON sp.sector_key = sm.sector_key
            WHERE length(cmr.period_ym) = 7
        )
        SELECT *
        FROM ranked
        WHERE rn = 1;

        INSERT INTO analysis2_sector_monthly_cache (
            sector_key, sector_label, period_ym, export_value, export_weight, import_value, import_weight
        )
        SELECT
            sector_key,
            sector_label,
            period_ym,
            ROUND(SUM(export_value), 6),
            ROUND(SUM(export_weight), 6),
            ROUND(SUM(import_value), 6),
            ROUND(SUM(import_weight), 6)
        FROM tmp_sector_match
        GROUP BY sector_key, sector_label, period_ym;

        INSERT INTO analysis2_sector_hs_monthly_cache (
            sector_key, sector_label, period_ym, hs_code, hs_name, mapping_status,
            export_value, export_weight, import_value, import_weight
        )
        SELECT
            sector_key,
            sector_label,
            period_ym,
            hs_code,
            hs_name,
            mapping_status,
            ROUND(SUM(export_value), 6),
            ROUND(SUM(export_weight), 6),
            ROUND(SUM(import_value), 6),
            ROUND(SUM(import_weight), 6)
        FROM tmp_sector_match
        GROUP BY sector_key, sector_label, period_ym, hs_code, hs_name, mapping_status;

        DROP TABLE IF EXISTS tmp_company_mapping;
        CREATE TEMP TABLE tmp_company_mapping AS
        WITH candidates AS (
            SELECT
                sm.sector_key,
                sp.label AS sector_label,
                hcm.stock_code,
                hcm.stock_name,
                hcm.sector_name,
                hcm.hs_code,
                COALESCE(NULLIF(hcm.hs_name, ''), NULLIF(sm.display_name, ''), NULLIF(sm.hs_name, ''), hcm.hs_code) AS hs_name,
                hcm.mapping_status,
                CASE hcm.mapping_status
                  WHEN 'exact' THEN 1
                  WHEN 'composite' THEN 2
                  ELSE 3 END AS rank_num,
                CASE WHEN instr(hcm.sector_name, '수입') > 0 THEN 1 ELSE 0 END AS is_import_label
            FROM hs_code_company_map hcm
            JOIN hs_sector_map sm
              ON sm.hs_code = hcm.hs_code
            JOIN sector_preset sp
              ON sp.sector_key = sm.sector_key
        ),
        company_pref AS (
            SELECT
                sector_key,
                stock_code,
                MAX(CASE WHEN is_import_label = 0 THEN 1 ELSE 0 END) AS has_non_import
            FROM candidates
            GROUP BY sector_key, stock_code
        ),
        filtered AS (
            SELECT c.*
            FROM candidates c
            JOIN company_pref cp
              ON cp.sector_key = c.sector_key
             AND cp.stock_code = c.stock_code
            WHERE cp.has_non_import = 0
               OR c.is_import_label = 0
        ),
        ranked AS (
            SELECT sector_key, stock_code, MIN(rank_num) AS best_rank
            FROM filtered
            GROUP BY sector_key, stock_code
        )
        SELECT
            f.sector_key,
            f.sector_label,
            f.stock_code,
            f.stock_name,
            f.sector_name,
            f.hs_code,
            f.hs_name,
            f.mapping_status,
            f.rank_num
        FROM filtered f
        JOIN ranked r
          ON r.sector_key = f.sector_key
         AND r.stock_code = f.stock_code
         AND r.best_rank = f.rank_num;

        DROP TABLE IF EXISTS tmp_company_match;
        CREATE TEMP TABLE tmp_company_match AS
        WITH ranked AS (
            SELECT
                cmr.row_key,
                cmr.period_ym,
                cmr.export_value,
                cmr.export_weight,
                cmr.import_value,
                cmr.import_weight,
                tcm.sector_key,
                tcm.sector_label,
                tcm.stock_code,
                tcm.stock_name,
                tcm.sector_name,
                tcm.hs_code,
                tcm.hs_name,
                tcm.mapping_status,
                tcm.rank_num,
                ROW_NUMBER() OVER (
                    PARTITION BY cmr.row_key, tcm.sector_key, tcm.stock_code
                    ORDER BY LENGTH(tcm.hs_code) DESC, tcm.rank_num ASC, tcm.hs_code
                ) AS rn
            FROM customs_monthly_record cmr
            JOIN tmp_company_mapping tcm
              ON cmr.hs_code = tcm.hs_code
              OR cmr.hs_code LIKE tcm.hs_code || '%'
            WHERE length(cmr.period_ym) = 7
        )
        SELECT *
        FROM ranked
        WHERE rn = 1;

        INSERT INTO analysis2_company_monthly_cache (
            sector_key, sector_label, stock_code, stock_name, period_ym,
            sector_names, hs_names, mapping_status,
            export_value, export_weight, import_value, import_weight
        )
        SELECT
            sector_key,
            sector_label,
            stock_code,
            stock_name,
            period_ym,
            GROUP_CONCAT(DISTINCT sector_name),
            GROUP_CONCAT(DISTINCT hs_name),
            CASE MIN(rank_num)
              WHEN 1 THEN 'exact'
              WHEN 2 THEN 'composite'
              ELSE 'provisional' END AS mapping_status,
            ROUND(SUM(export_value), 6),
            ROUND(SUM(export_weight), 6),
            ROUND(SUM(import_value), 6),
            ROUND(SUM(import_weight), 6)
        FROM tmp_company_match
        GROUP BY sector_key, sector_label, stock_code, stock_name, period_ym;

        INSERT INTO analysis2_company_hs_monthly_cache (
            sector_key, sector_label, stock_code, stock_name, period_ym, hs_code, hs_name,
            mapping_status, export_value, export_weight, import_value, import_weight
        )
        SELECT
            sector_key,
            sector_label,
            stock_code,
            stock_name,
            period_ym,
            hs_code,
            hs_name,
            CASE MIN(rank_num)
              WHEN 1 THEN 'exact'
              WHEN 2 THEN 'composite'
              ELSE 'provisional' END AS mapping_status,
            ROUND(SUM(export_value), 6),
            ROUND(SUM(export_weight), 6),
            ROUND(SUM(import_value), 6),
            ROUND(SUM(import_weight), 6)
        FROM tmp_company_match
        GROUP BY sector_key, sector_label, stock_code, stock_name, period_ym, hs_code, hs_name;

        DROP TABLE IF EXISTS tmp_sector_match;
        DROP TABLE IF EXISTS tmp_company_mapping;
        DROP TABLE IF EXISTS tmp_company_match;
        """
    )

    return {
        "sector_monthly": conn.execute("SELECT COUNT(*) FROM analysis2_sector_monthly_cache").fetchone()[0],
        "sector_hs_monthly": conn.execute("SELECT COUNT(*) FROM analysis2_sector_hs_monthly_cache").fetchone()[0],
        "company_monthly": conn.execute("SELECT COUNT(*) FROM analysis2_company_monthly_cache").fetchone()[0],
        "company_hs_monthly": conn.execute("SELECT COUNT(*) FROM analysis2_company_hs_monthly_cache").fetchone()[0],
    }


def export_csv(conn: sqlite3.Connection, query: str, path: Path) -> int:
    rows = conn.execute(query).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows([tuple(row) for row in rows])
    return len(rows)


def export_mapping_csvs(conn: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "sector_hs_mappings.csv": """
            SELECT sector_key, hs_code, display_name, hs_name, mapping_status, note
            FROM hs_sector_map
            ORDER BY sector_key, hs_code
        """,
        "company_hs_mappings.csv": """
            SELECT stock_code, stock_name, hs_code, hs_name, sector_name, match_type, mapping_status, confidence, note
            FROM hs_code_company_map
            ORDER BY stock_name, hs_code
        """,
        "company_mapping_summary.csv": """
            SELECT stock_code, stock_name,
                   GROUP_CONCAT(DISTINCT sector_name) AS sector_names,
                   GROUP_CONCAT(DISTINCT hs_code) AS hs_codes,
                   MIN(mapping_status) AS min_mapping_status,
                   MAX(confidence) AS max_confidence
            FROM hs_code_company_map
            GROUP BY stock_code, stock_name
            ORDER BY stock_name
        """,
    }

    result: dict[str, int] = {}
    for filename, query in queries.items():
        count = export_csv(conn, query, EXPORT_DIR / filename)
        export_csv(conn, query, DOWNLOADS_DIR / filename)
        result[filename] = count
    return result


def main() -> None:
    started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    conn = connect()
    try:
        ensure_tables(conn)
        cache_counts = rebuild_cache(conn)
        csv_counts = export_mapping_csvs(conn)
        conn.commit()
    finally:
        conn.close()

    payload = {
        "ran_at": started_at,
        "cache_counts": cache_counts,
        "csv_counts": csv_counts,
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
