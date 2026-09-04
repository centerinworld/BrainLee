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


def _analysis2_sector_key_case(sector_expr: str, hs_expr: str) -> str:
    """Split the legacy energy/materials bucket without duplicating HS observations."""
    return f"""
        CASE
          WHEN {sector_expr} = 'energy_materials' AND {hs_expr} LIKE '31%' THEN 'fertilizers'
          WHEN {sector_expr} = 'energy_materials' AND {hs_expr} LIKE '27%' THEN 'energy'
          WHEN {sector_expr} = 'energy_materials' AND {hs_expr} LIKE '85%' THEN 'power_infra'
          WHEN {sector_expr} = 'energy_materials' AND ({hs_expr} LIKE '68%' OR {hs_expr} LIKE '72%' OR {hs_expr} LIKE '73%') THEN 'steel_materials'
          WHEN {sector_expr} = 'energy_materials' AND ({hs_expr} LIKE '28%' OR {hs_expr} LIKE '29%' OR {hs_expr} LIKE '32%' OR {hs_expr} LIKE '38%' OR {hs_expr} LIKE '39%' OR {hs_expr} LIKE '40%' OR {hs_expr} LIKE '54%') THEN 'chemicals'
          ELSE {sector_expr}
        END
    """


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS analysis2_company_hs_monthly_cache")
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
            sector_name TEXT NOT NULL DEFAULT '',
            flow_type TEXT NOT NULL DEFAULT 'export',
            mapping_status TEXT NOT NULL DEFAULT 'provisional',
            export_value REAL NOT NULL DEFAULT 0,
            export_weight REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            import_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (sector_key, stock_code, period_ym, hs_code, flow_type)
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
        f"""
        DELETE FROM analysis2_sector_monthly_cache;
        DELETE FROM analysis2_sector_hs_monthly_cache;
        DELETE FROM analysis2_company_monthly_cache;
        DELETE FROM analysis2_company_hs_monthly_cache;

        DROP TABLE IF EXISTS tmp_sector_canonical;
        CREATE TEMP TABLE tmp_sector_canonical AS
        WITH mapped AS (
            SELECT
                sp.sector_key AS source_sector_key,
                CASE
                  WHEN sp.sector_key = 'semiconductors' OR sp.sector_key LIKE 'semiconductors_%' THEN 'semiconductors'
                  WHEN sp.sector_key = 'autos' OR sp.sector_key LIKE 'autos_%' THEN 'autos'
                  WHEN sp.sector_key = 'batteries' OR sp.sector_key LIKE 'batteries_%' THEN 'batteries'
                  WHEN sp.sector_key = 'biotech' OR sp.sector_key LIKE 'biotech_%' THEN 'biotech'
                  WHEN sp.sector_key = 'consumer' OR sp.sector_key LIKE 'consumer_%' THEN 'consumer'
                  WHEN sp.sector_key = 'shipbuilding' OR sp.sector_key LIKE 'shipbuilding_%' THEN 'shipbuilding'
                  WHEN sp.sector_key = 'energy_materials' OR sp.sector_key LIKE 'energy_materials_%' THEN 'energy_materials'
                  ELSE sp.sector_key
                END AS canonical_sector_key,
                CASE
                  WHEN sp.sector_key IN (
                    'semiconductors', 'autos', 'batteries', 'biotech',
                    'consumer', 'shipbuilding', 'energy_materials', 'energy',
                    'chemicals', 'fertilizers', 'steel_materials', 'power_infra'
                  ) THEN 0
                  ELSE 1
                END AS alias_rank
            FROM sector_preset sp
        )
        SELECT
            mapped.source_sector_key,
            mapped.canonical_sector_key AS sector_key,
            parent.label AS sector_label,
            mapped.alias_rank
        FROM mapped
        JOIN sector_preset parent
          ON parent.sector_key = mapped.canonical_sector_key
        WHERE mapped.canonical_sector_key IN (
            'semiconductors', 'autos', 'batteries', 'biotech',
            'consumer', 'shipbuilding', 'energy_materials', 'energy',
            'chemicals', 'fertilizers', 'steel_materials', 'power_infra'
        );

        DROP TABLE IF EXISTS tmp_sector_match;
        CREATE TEMP TABLE tmp_sector_match AS
        WITH categorized AS (
            SELECT
                cmr.row_key,
                cmr.period_ym,
                cmr.export_value,
                cmr.export_weight,
                cmr.import_value,
                cmr.import_weight,
                {_analysis2_sector_key_case('sc.sector_key', 'sm.hs_code')} AS sector_key,
                sm.hs_code,
                COALESCE(NULLIF(sm.display_name, ''), NULLIF(sm.hs_name, ''), sm.hs_code) AS hs_name,
                sm.mapping_status,
                sc.alias_rank
            FROM customs_monthly_record cmr
            JOIN hs_sector_map sm
              ON cmr.hs_code = sm.hs_code
              OR cmr.hs_code LIKE sm.hs_code || '%'
            JOIN tmp_sector_canonical sc
              ON sc.source_sector_key = sm.sector_key
            WHERE length(cmr.period_ym) = 7
        ),
        ranked AS (
            SELECT
                c.*,
                sp.label AS sector_label,
                ROW_NUMBER() OVER (
                    PARTITION BY c.row_key, c.sector_key
                    ORDER BY c.alias_rank ASC,
                             LENGTH(c.hs_code) DESC,
                             CASE c.mapping_status
                               WHEN 'exact' THEN 1
                               WHEN 'composite' THEN 2
                               ELSE 3 END,
                             c.hs_code
                ) AS rn
            FROM categorized c
            JOIN sector_preset sp ON sp.sector_key = c.sector_key
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
        -- hs_code 별 매핑 기업 수 계산 (시장비율 미지정 시 균등 분할용)
        WITH hs_company_counts AS (
            SELECT hs_code,
                   COUNT(DISTINCT stock_code) AS company_count
            FROM hs_code_company_map
            GROUP BY hs_code
        ),
        -- telegram 매핑: hs_sector_map에 정확히 일치하는 hs_code (10자리)
        tg_exact AS (
            SELECT sc.sector_key, sc.sector_label,
                   t.stock_code, t.stock_name,
                   t.flow_type,
                   t.hs_code,
                   COALESCE(NULLIF(t.hs_name,''), NULLIF(sm.display_name,''), sm.hs_name, t.hs_code) AS hs_name,
                   COALESCE(NULLIF(t.flow_scope,''), '') AS sector_name
            FROM telegram_company_hs_flow_map t
            JOIN hs_sector_map sm ON sm.hs_code = t.hs_code
            JOIN tmp_sector_canonical sc ON sc.source_sector_key = sm.sector_key
            WHERE t.stock_code != '' AND LENGTH(t.hs_code) = 10
        ),
        -- telegram 매핑: 6자리 접두사 일치
        tg_6 AS (
            SELECT sc.sector_key, sc.sector_label,
                   t.stock_code, t.stock_name,
                   t.flow_type,
                   t.hs_code,
                   COALESCE(NULLIF(t.hs_name,''), NULLIF(sm.display_name,''), sm.hs_name, t.hs_code) AS hs_name,
                   COALESCE(NULLIF(t.flow_scope,''), '') AS sector_name
            FROM telegram_company_hs_flow_map t
            JOIN hs_sector_map sm ON sm.hs_code = t.hs_code
            JOIN tmp_sector_canonical sc ON sc.source_sector_key = sm.sector_key
            WHERE t.stock_code != '' AND LENGTH(t.hs_code) = 6
        ),
        -- telegram 매핑: 4자리 접두사 일치
        tg_4 AS (
            SELECT sc.sector_key, sc.sector_label,
                   t.stock_code, t.stock_name,
                   t.flow_type,
                   t.hs_code,
                   COALESCE(NULLIF(t.hs_name,''), NULLIF(sm.display_name,''), sm.hs_name, t.hs_code) AS hs_name,
                   COALESCE(NULLIF(t.flow_scope,''), '') AS sector_name
            FROM telegram_company_hs_flow_map t
            JOIN hs_sector_map sm ON sm.hs_code = t.hs_code
            JOIN tmp_sector_canonical sc ON sc.source_sector_key = sm.sector_key
            WHERE t.stock_code != '' AND LENGTH(t.hs_code) = 4
        ),
        tg_all AS (
            SELECT * FROM tg_exact
            UNION ALL
            SELECT * FROM tg_6
            UNION ALL
            SELECT * FROM tg_4
        ),
        tg_company_counts AS (
            SELECT hs_code, sector_key, COUNT(DISTINCT stock_code) AS company_count
            FROM tg_all
            GROUP BY hs_code, sector_key
        ),
        candidates AS (
            -- 1) 기존 hs_code_company_map 매핑 (우선순위 높음)
            SELECT
                sc.sector_key,
                sc.sector_label,
                hcm.stock_code,
                hcm.stock_name,
                hcm.sector_name,
                COALESCE(NULLIF(hcm.flow_type, ''), CASE WHEN instr(hcm.sector_name, '수입') > 0 THEN 'import' ELSE 'export' END) AS flow_type,
                hcm.hs_code,
                COALESCE(NULLIF(hcm.hs_name, ''), NULLIF(sm.display_name, ''), NULLIF(sm.hs_name, ''), hcm.hs_code) AS hs_name,
                hcm.mapping_status,
                CASE hcm.mapping_status
                  WHEN 'exact' THEN 1
                  WHEN 'composite' THEN 2
                  ELSE 3 END AS rank_num,
                CASE WHEN COALESCE(NULLIF(hcm.flow_type, ''), '') = 'import' OR instr(hcm.sector_name, '수입') > 0 THEN 1 ELSE 0 END AS is_import_label,
                COALESCE(
                    hcm.market_share_pct,
                    1.0 / NULLIF(hcc.company_count, 0),
                    1.0
                ) AS share_pct
            FROM hs_code_company_map hcm
            JOIN hs_sector_map sm
              ON sm.hs_code = hcm.hs_code
            JOIN tmp_sector_canonical sc
              ON sc.source_sector_key = sm.sector_key
            LEFT JOIN hs_company_counts hcc
              ON hcc.hs_code = hcm.hs_code

            UNION ALL

            -- 2) telegram 매핑 (hs_code_company_map에 없는 기업만 추가)
            SELECT
                ta.sector_key,
                ta.sector_label,
                ta.stock_code,
                ta.stock_name,
                ta.sector_name,
                ta.flow_type,
                ta.hs_code,
                ta.hs_name,
                'provisional' AS mapping_status,
                3 AS rank_num,
                CASE WHEN ta.flow_type = 'import' THEN 1 ELSE 0 END AS is_import_label,
                1.0 / NULLIF(tc.company_count, 0) AS share_pct
            FROM tg_all ta
            LEFT JOIN tg_company_counts tc
                ON tc.hs_code = ta.hs_code AND tc.sector_key = ta.sector_key
            WHERE NOT EXISTS (
                SELECT 1 FROM hs_code_company_map hcm WHERE hcm.stock_code = ta.stock_code
            )
        )
        , categorized AS (
            SELECT
                {_analysis2_sector_key_case('c.sector_key', 'c.hs_code')} AS sector_key,
                c.stock_code, c.stock_name, c.sector_name, c.flow_type,
                c.hs_code, c.hs_name, c.mapping_status, c.rank_num, c.share_pct
            FROM candidates c
        ), ranked AS (
            SELECT
                c.*,
                sp.label AS sector_label,
                ROW_NUMBER() OVER (
                    PARTITION BY c.sector_key, c.stock_code, c.hs_code, c.flow_type, c.sector_name
                    ORDER BY c.rank_num ASC
                ) AS rn
            FROM categorized c
            JOIN sector_preset sp ON sp.sector_key = c.sector_key
        )
        SELECT sector_key, sector_label, stock_code, stock_name, sector_name, flow_type,
               hs_code, hs_name, mapping_status, rank_num, share_pct
        FROM ranked
        WHERE rn = 1
        ;

        DROP TABLE IF EXISTS tmp_company_match;
        CREATE TEMP TABLE tmp_company_match AS
        -- UNION ALL 방식: OR LIKE 대신 SUBSTR 기반 인덱스 조인 (성능 최적화)
        WITH base_matches AS (
            -- 10자리 완전 일치
            SELECT cmr.row_key, cmr.period_ym,
                   cmr.export_value, cmr.export_weight, cmr.import_value, cmr.import_weight,
                   tcm.sector_key, tcm.sector_label, tcm.stock_code, tcm.stock_name,
                   tcm.sector_name, tcm.flow_type, tcm.hs_code, tcm.hs_name,
                   tcm.mapping_status, tcm.rank_num, tcm.share_pct
            FROM customs_monthly_record cmr
            JOIN tmp_company_mapping tcm ON cmr.hs_code = tcm.hs_code
            WHERE length(cmr.period_ym) = 7 AND LENGTH(tcm.hs_code) = 10

            UNION ALL

            -- 6자리 접두사 일치 (표현 인덱스 ix_cmr_hs6 사용)
            SELECT cmr.row_key, cmr.period_ym,
                   cmr.export_value, cmr.export_weight, cmr.import_value, cmr.import_weight,
                   tcm.sector_key, tcm.sector_label, tcm.stock_code, tcm.stock_name,
                   tcm.sector_name, tcm.flow_type, tcm.hs_code, tcm.hs_name,
                   tcm.mapping_status, tcm.rank_num, tcm.share_pct
            FROM customs_monthly_record cmr
            JOIN tmp_company_mapping tcm ON SUBSTR(cmr.hs_code, 1, 6) = tcm.hs_code
            WHERE length(cmr.period_ym) = 7 AND LENGTH(tcm.hs_code) = 6

            UNION ALL

            -- 4자리 접두사 일치 (표현 인덱스 ix_cmr_hs4 사용)
            SELECT cmr.row_key, cmr.period_ym,
                   cmr.export_value, cmr.export_weight, cmr.import_value, cmr.import_weight,
                   tcm.sector_key, tcm.sector_label, tcm.stock_code, tcm.stock_name,
                   tcm.sector_name, tcm.flow_type, tcm.hs_code, tcm.hs_name,
                   tcm.mapping_status, tcm.rank_num, tcm.share_pct
            FROM customs_monthly_record cmr
            JOIN tmp_company_mapping tcm ON SUBSTR(cmr.hs_code, 1, 4) = tcm.hs_code
            WHERE length(cmr.period_ym) = 7 AND LENGTH(tcm.hs_code) = 4
        ),
        ranked AS (
            SELECT
                row_key,
                period_ym,
                -- 시장비율 적용하여 수출/수입/중량 조정
                export_value * share_pct AS export_value,
                export_weight * share_pct AS export_weight,
                import_value * share_pct AS import_value,
                import_weight * share_pct AS import_weight,
                sector_key,
                sector_label,
                stock_code,
                stock_name,
                sector_name,
                flow_type,
                hs_code,
                hs_name,
                mapping_status,
                rank_num,
                share_pct,
                ROW_NUMBER() OVER (
                    PARTITION BY row_key, sector_key, stock_code
                    ORDER BY LENGTH(hs_code) DESC, rank_num ASC, hs_code
                ) AS rn
            FROM base_matches
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
            MAX(sector_label),
            stock_code,
            MAX(stock_name),
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
        GROUP BY sector_key, stock_code, period_ym;

        INSERT INTO analysis2_company_hs_monthly_cache (
            sector_key, sector_label, stock_code, stock_name, period_ym, hs_code, hs_name,
            sector_name, flow_type, mapping_status, export_value, export_weight, import_value, import_weight
        )
        WITH flow_rows AS (
          SELECT
            sector_key,
            MAX(sector_label) AS sector_label,
            stock_code,
            MAX(stock_name) AS stock_name,
            period_ym,
            hs_code,
            MAX(hs_name) AS hs_name,
            GROUP_CONCAT(DISTINCT sector_name) AS sector_names,
            flow_type,
            MIN(rank_num) AS best_rank,
            SUM(export_value) AS export_value,
            SUM(export_weight) AS export_weight,
            SUM(import_value) AS import_value,
            SUM(import_weight) AS import_weight
          FROM tmp_company_match
          GROUP BY
            sector_key,
            stock_code,
            period_ym,
            hs_code,
            flow_type
        )
        SELECT
            sector_key,
            sector_label,
            stock_code,
            stock_name,
            period_ym,
            hs_code,
            hs_name,
            GROUP_CONCAT(DISTINCT sector_names) AS sector_names,
            flow_type,
            CASE MIN(best_rank)
              WHEN 1 THEN 'exact'
              WHEN 2 THEN 'composite'
              ELSE 'provisional' END AS mapping_status,
            ROUND(SUM(export_value), 6),
            ROUND(SUM(export_weight), 6),
            ROUND(SUM(import_value), 6),
            ROUND(SUM(import_weight), 6)
        FROM flow_rows
        GROUP BY sector_key, sector_label, stock_code, stock_name, period_ym, hs_code, hs_name, flow_type;

        DROP TABLE IF EXISTS tmp_sector_match;
        DROP TABLE IF EXISTS tmp_sector_canonical;
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
