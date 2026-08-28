from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STOCK_DB = ROOT / "stock.db"
HS_DB = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
REPORT_DIR = ROOT / "research_outputs"


ALLOWED_SECTOR_STOCK_ALIASES = {
    ("000660", "하이닉스"),
    ("003550", "LG그룹"),
    ("005380", "현대자동차"),
    ("005490", "POSCO"),
    ("005490", "포스코"),
    ("005930", "삼성"),
    ("329180", "현대중공업"),
    ("475400", "씨메스"),
    ("150900", "파수"),
}


def norm(value: str | None) -> str:
    return (value or "").replace(" ", "").replace("(주)", "").strip().upper()


def rows_as_dicts(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def cleanup_hs_company_map() -> list[dict]:
    conn = sqlite3.connect(HS_DB)
    conn.row_factory = sqlite3.Row
    try:
        candidate_rows = rows_as_dicts(
            conn,
            """
            SELECT id, stock_code, stock_name, hs_code, hs_name, sector_name, note
            FROM hs_code_company_map
            WHERE id IN (26624, 26625, 26626)
               OR (
                    sector_name LIKE '%대덕전자%'
                AND stock_code <> '353200'
               )
            ORDER BY id
            """,
        )
        bad_rows = [
            row for row in candidate_rows
            if row["stock_code"] != "353200"
            and row["stock_name"] not in (row["sector_name"] or "")
        ]
        bad_ids = [row["id"] for row in bad_rows]
        if bad_ids:
            conn.executemany("DELETE FROM hs_code_company_map WHERE id = ?", [(row_id,) for row_id in bad_ids])
            conn.commit()
        return bad_rows
    finally:
        conn.close()


def cleanup_sector_stocks() -> dict:
    conn = sqlite3.connect(STOCK_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = rows_as_dicts(
            conn,
            """
            SELECT ss.id, ss.post_id, ss.category, ss.stock_name, ss.stock_code,
                   su.stock_name AS canonical_name, ss.ref_price
            FROM sector_stocks ss
            JOIN stock_universe su ON su.stock_code = ss.stock_code
            WHERE REPLACE(REPLACE(TRIM(ss.stock_name),' ',''),'(주)','')
               <> REPLACE(REPLACE(TRIM(su.stock_name),' ',''),'(주)','')
            ORDER BY ss.stock_code, ss.stock_name, ss.id
            """,
        )
        kept_aliases: list[dict] = []
        delete_rows: list[dict] = []
        for row in rows:
            key = (row["stock_code"], row["stock_name"])
            if key in ALLOWED_SECTOR_STOCK_ALIASES:
                kept_aliases.append(row)
                continue
            # If the row is only punctuation/case drift, keep it. Do not keep
            # substring collisions such as 네이버→네이블 or 인텔→인텔리안테크.
            if norm(row["stock_name"]) == norm(row["canonical_name"]):
                kept_aliases.append(row)
                continue
            delete_rows.append(row)

        if delete_rows:
            conn.executemany("DELETE FROM sector_stocks WHERE id = ?", [(row["id"],) for row in delete_rows])
            conn.commit()
        return {"deleted": delete_rows, "kept_aliases": kept_aliases}
    finally:
        conn.close()


def verify() -> dict:
    stock_conn = sqlite3.connect(STOCK_DB)
    stock_conn.row_factory = sqlite3.Row
    hs_conn = sqlite3.connect(HS_DB)
    hs_conn.row_factory = sqlite3.Row
    try:
        return {
            "stock_universe_verified": rows_as_dicts(
                stock_conn,
                """
                SELECT stock_code, stock_name, market, sector_large
                FROM stock_universe
                WHERE stock_code IN ('000640', '353200')
                ORDER BY stock_code
                """,
            ),
            "remaining_sector_stock_mismatches": rows_as_dicts(
                stock_conn,
                """
                SELECT ss.id, ss.stock_name, ss.stock_code, su.stock_name AS canonical_name
                FROM sector_stocks ss
                JOIN stock_universe su ON su.stock_code = ss.stock_code
                WHERE REPLACE(REPLACE(TRIM(ss.stock_name),' ',''),'(주)','')
                   <> REPLACE(REPLACE(TRIM(su.stock_name),' ',''),'(주)','')
                ORDER BY ss.stock_code, ss.stock_name
                """,
            ),
            "remaining_daeduck_pollution": rows_as_dicts(
                hs_conn,
                """
                SELECT id, stock_code, stock_name, hs_code, sector_name
                FROM hs_code_company_map
                WHERE sector_name LIKE '%대덕전자%'
                  AND stock_code <> '353200'
                  AND instr(sector_name, stock_name) = 0
                ORDER BY id
                """,
            ),
            "hs_000640_rows": rows_as_dicts(
                hs_conn,
                """
                SELECT stock_code, stock_name, hs_code, sector_name
                FROM hs_code_company_map
                WHERE stock_code = '000640'
                ORDER BY hs_code
                """,
            ),
            "hs_353200_rows": rows_as_dicts(
                hs_conn,
                """
                SELECT stock_code, stock_name, hs_code, sector_name
                FROM hs_code_company_map
                WHERE stock_code = '353200'
                ORDER BY hs_code
                """,
            ),
        }
    finally:
        stock_conn.close()
        hs_conn.close()


def main() -> None:
    hs_deleted = cleanup_hs_company_map()
    sector_result = cleanup_sector_stocks()
    verification = verify()
    report = {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "hs_company_map_deleted": hs_deleted,
        "sector_stocks_deleted_count": len(sector_result["deleted"]),
        "sector_stocks_deleted": sector_result["deleted"],
        "sector_stocks_kept_aliases": sector_result["kept_aliases"],
        "verification": verification,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "stock_code_name_mismatch_audit_20260628.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "hs_deleted": len(hs_deleted),
        "sector_stocks_deleted": len(sector_result["deleted"]),
        "remaining_sector_stock_mismatches": len(verification["remaining_sector_stock_mismatches"]),
        "remaining_daeduck_pollution": len(verification["remaining_daeduck_pollution"]),
        "report": str(out),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
