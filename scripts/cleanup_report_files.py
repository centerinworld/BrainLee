from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path("/Applications/stock_dashboard/stock.db")

COMMON_FALSE_NAMES = {
    "업데이트",
    "해당기업",
    "기업",
    "산업",
    "시장",
    "투자",
    "증권",
    "리서치",
}


def normalize(text: str | None) -> str:
    text = text or ""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).upper()


def load_stock_names(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """
        WITH names AS (
            SELECT stock_code, stock_name, market
            FROM stock_meta
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND UPPER(COALESCE(market, '')) IN ('KOSPI', 'KOSDAQ')
            UNION
            SELECT stock_code, stock_name, market
            FROM stock_universe
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
              AND COALESCE(stock_type, '보통주') = '보통주'
        )
        SELECT stock_code, stock_name, MAX(market)
        FROM names
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        GROUP BY stock_code, stock_name
        """
    ).fetchall()
    candidates: list[tuple[str, str, str]] = []
    for code, name, market in rows:
        name = str(name or "").strip()
        norm = normalize(name)
        if len(norm) < 3 or name in COMMON_FALSE_NAMES:
            continue
        candidates.append((str(code).zfill(6), name, norm))
    candidates.sort(key=lambda item: len(item[2]), reverse=True)
    return candidates


def find_stock(text: str, candidates: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    norm_text = normalize(text)
    for code, name, norm_name in candidates:
        if norm_name and norm_name in norm_text:
            return code, name
    return None


def classify_individual_reports(conn: sqlite3.Connection) -> int:
    candidates = load_stock_names(conn)
    rows = conn.execute(
        """
        SELECT id, stock_code, stock_name, sector, file_name, saved_name, caption
        FROM report_files
        WHERE stock_code IS NULL OR stock_code=''
        """
    ).fetchall()
    updated = 0
    for row in rows:
        haystack = " ".join(str(row[key] or "") for key in ("file_name", "saved_name", "caption", "stock_name"))
        match = find_stock(haystack, candidates)
        if not match:
            continue
        code, name = match
        conn.execute(
            """
            UPDATE report_files
            SET stock_code=?, stock_name=?
            WHERE id=?
            """,
            (code, name, row["id"]),
        )
        updated += 1
    return updated


def duplicate_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    groups = conn.execute(
        """
        SELECT report_date, file_size
        FROM report_files
        WHERE report_date IS NOT NULL AND report_date!=''
          AND file_size IS NOT NULL AND file_size > 0
        GROUP BY report_date, file_size
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    duplicates: list[list[sqlite3.Row]] = []
    for group in groups:
        rows = conn.execute(
            """
            SELECT id, stock_code, stock_name, sector, file_name, saved_name, caption, created_at
            FROM report_files
            WHERE report_date=? AND file_size=?
            ORDER BY id
            """,
            (group["report_date"], group["file_size"]),
        ).fetchall()
        duplicates.append(rows)
    return duplicates


def choose_keeper(rows: list[sqlite3.Row]) -> sqlite3.Row:
    def score(row: sqlite3.Row) -> tuple[int, int, int]:
        has_code = 1 if str(row["stock_code"] or "").strip() else 0
        has_name = 1 if str(row["stock_name"] or "").strip() else 0
        text_len = len(str(row["file_name"] or "")) + len(str(row["caption"] or ""))
        return has_code, has_name, text_len

    return sorted(rows, key=lambda row: (score(row), -int(row["id"])), reverse=True)[0]


def remove_duplicates(conn: sqlite3.Connection) -> int:
    removed = 0
    for rows in duplicate_groups(conn):
        keeper = choose_keeper(rows)
        delete_ids = [row["id"] for row in rows if row["id"] != keeper["id"]]
        for report_id in delete_ids:
            conn.execute("DELETE FROM report_files WHERE id=?", (report_id,))
            removed += 1
    return removed


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    backup_name = "report_files_backup_before_cleanup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    conn.execute(f"CREATE TABLE {backup_name} AS SELECT * FROM report_files")
    updated = classify_individual_reports(conn)
    removed = remove_duplicates(conn)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM report_files").fetchone()[0]
    with_code = conn.execute(
        "SELECT COUNT(*) FROM report_files WHERE stock_code IS NOT NULL AND stock_code!=''"
    ).fetchone()[0]
    conn.close()
    print(
        {
            "backup_table": backup_name,
            "classified_to_stock": updated,
            "duplicates_removed": removed,
            "remaining_reports": total,
            "reports_with_stock_code": with_code,
        }
    )


if __name__ == "__main__":
    main()
