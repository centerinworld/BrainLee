from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
REPORT_DIR = ROOT_DIR.parent / "research_outputs"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_snapshot(conn: sqlite3.Connection, table: str, row_id: int) -> dict | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def update_sector_map(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    sector_key: str | None = None,
    display_name: str | None = None,
    hs_name: str | None = None,
    reason: str,
) -> dict:
    before = row_snapshot(conn, "hs_sector_map", row_id)
    if not before:
        return {"table": "hs_sector_map", "id": row_id, "action": "missing", "reason": reason}

    intended = {
        "sector_key": sector_key if sector_key is not None else before["sector_key"],
        "display_name": display_name if display_name is not None else before["display_name"],
        "hs_name": hs_name if hs_name is not None else before["hs_name"],
    }
    if (
        before["sector_key"] == intended["sector_key"]
        and before["display_name"] == intended["display_name"]
        and before["hs_name"] == intended["hs_name"]
    ):
        return {
            "table": "hs_sector_map",
            "id": row_id,
            "action": "noop",
            "reason": reason,
            "row": before,
        }

    updates: list[str] = []
    params: list[str | int] = []
    if sector_key is not None:
        updates.append("sector_key = ?")
        params.append(sector_key)
    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name)
    if hs_name is not None:
        updates.append("hs_name = ?")
        params.append(hs_name)
    updates.extend(["note = note || ?", "updated_at = CURRENT_TIMESTAMP"])
    params.append(f" | audit 2026-06-28: {reason}")
    params.append(row_id)
    conn.execute(f"UPDATE hs_sector_map SET {', '.join(updates)} WHERE id = ?", params)
    after = row_snapshot(conn, "hs_sector_map", row_id)
    return {
        "table": "hs_sector_map",
        "id": row_id,
        "action": "update",
        "reason": reason,
        "before": before,
        "after": after,
    }


def delete_row(conn: sqlite3.Connection, table: str, row_id: int, *, reason: str) -> dict:
    before = row_snapshot(conn, table, row_id)
    if not before:
        return {"table": table, "id": row_id, "action": "missing", "reason": reason}
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    return {"table": table, "id": row_id, "action": "delete", "reason": reason, "before": before}


def remaining_cross_sector_duplicates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        WITH canonical AS (
            SELECT
                hs_code,
                CASE
                  WHEN sector_key = 'semiconductors' OR sector_key LIKE 'semiconductors_%' THEN 'semiconductors'
                  WHEN sector_key = 'autos' OR sector_key LIKE 'autos_%' THEN 'autos'
                  WHEN sector_key = 'batteries' OR sector_key LIKE 'batteries_%' THEN 'batteries'
                  WHEN sector_key = 'biotech' OR sector_key LIKE 'biotech_%' THEN 'biotech'
                  WHEN sector_key = 'consumer' OR sector_key LIKE 'consumer_%' THEN 'consumer'
                  WHEN sector_key = 'shipbuilding' OR sector_key LIKE 'shipbuilding_%' THEN 'shipbuilding'
                  WHEN sector_key = 'energy_materials' OR sector_key LIKE 'energy_materials_%' THEN 'energy_materials'
                  ELSE sector_key
                END AS canonical_sector,
                sector_key,
                display_name
            FROM hs_sector_map
        )
        SELECT
            hs_code,
            COUNT(DISTINCT canonical_sector) AS canonical_sector_count,
            GROUP_CONCAT(DISTINCT canonical_sector) AS canonical_sectors,
            GROUP_CONCAT(sector_key || ':' || display_name, ' || ') AS mappings
        FROM canonical
        GROUP BY hs_code
        HAVING canonical_sector_count > 1
        ORDER BY canonical_sector_count DESC, hs_code
        """
    ).fetchall()
    return [dict(row) for row in rows]


def suspicious_keyword_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, sector_key, hs_code, display_name, hs_name, mapping_status
        FROM hs_sector_map
        WHERE
            (sector_key = 'shipbuilding' AND (
                display_name LIKE '%반도체%' OR display_name LIKE '%CMP%' OR display_name LIKE '%동박적층판%'
                OR display_name LIKE '%솔더볼%' OR display_name LIKE '%진공펌프%' OR display_name LIKE '%변압기%'
                OR display_name LIKE '%배전반%' OR display_name LIKE '%인공호흡기%' OR display_name LIKE '%캐눌러%'
                OR display_name LIKE '%바늘%' OR display_name LIKE '%OIS%' OR display_name LIKE '%음반%'
            ))
            OR (sector_key = 'consumer' AND (
                display_name LIKE '%보툴리눔%' OR display_name LIKE '%의료%' OR display_name LIKE '%레이더%'
            ))
            OR (sector_key = 'energy_materials' AND (
                display_name LIKE '%반도체%' OR display_name LIKE '%CMP%'
            ))
            OR (sector_key = 'batteries' AND display_name LIKE '%Relay%')
        ORDER BY sector_key, hs_code, id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def main() -> None:
    conn = connect()
    changes: list[dict] = []
    try:
        # Shipbuilding/machinery rows that were auto-filled from unrelated product labels.
        changes.append(update_sector_map(conn, 280, sector_key="energy_materials", display_name="석유와 역청유 수입", hs_name="석유와 역청유", reason="270900 is crude oil, not shipbuilding or NCH precursor"))
        for row_id in [284, 289, 290, 292, 293, 294, 302]:
            changes.append(update_sector_map(conn, row_id, sector_key="semiconductors", reason="semiconductor/display materials or equipment mapped under shipbuilding"))
        for row_id in [255, 256, 257, 258, 259, 273, 296, 300]:
            changes.append(update_sector_map(conn, row_id, sector_key="energy_materials", reason="power equipment mapped under shipbuilding"))
        for row_id in [303, 304, 305]:
            changes.append(update_sector_map(conn, row_id, sector_key="biotech", reason="medical device mapped under shipbuilding"))
        changes.append(update_sector_map(conn, 295, sector_key="consumer", reason="OIS camera actuator is consumer/electronics, not shipbuilding"))
        changes.append(update_sector_map(conn, 297, sector_key="consumer", reason="music record media mapped under shipbuilding"))
        changes.append(update_sector_map(conn, 298, display_name="레이더 기기", hs_name="레이더 기기", reason="852610 is radar equipment; remove incorrect construction-engine label"))
        changes.append(update_sector_map(conn, 262, sector_key="shipbuilding", display_name="레이더 기기", hs_name="레이더 기기", reason="radar equipment belongs with machinery/defense bucket, not consumer"))

        # Rows whose HS code and display label contradict each other; the correct HS already exists elsewhere.
        for row_id in [285, 291]:
            changes.append(delete_row(conn, "hs_sector_map", row_id, reason="display label contradicts HS code; correct heat-exchanger HS 8419509000 already exists"))

        # Consumer/biotech/electronics cleanup.
        changes.append(update_sector_map(conn, 31, sector_key="consumer", display_name="CCTV/산업용 카메라", reason="CCTV camera is not biotech"))
        changes.append(update_sector_map(conn, 47, sector_key="biotech", reason="botulinum toxin is biotech/healthcare, not consumer"))
        changes.append(delete_row(conn, "hs_sector_map", 43, reason="duplicate broad medical-device row under consumer; biotech 901890 already exists"))
        for row_id in [44, 45, 46, 96, 99]:
            changes.append(update_sector_map(conn, row_id, sector_key="biotech", reason="medical/aesthetic or optical healthcare product mapped under consumer"))
        changes.append(delete_row(conn, "hs_sector_map", 229, reason="EV relay has a cleaner auto-specific 8536491000 mapping; avoid cross-sector duplicate with power relay 8536490000"))
        changes.append(delete_row(conn, "hs_sector_map", 299, reason="broad 853649 auto relay overlaps power relay 8536490000; keep auto-specific 8536491000 only"))
        changes.append(update_sector_map(conn, 248, sector_key="semiconductors", reason="semiconductor-grade hydrogen peroxide mapped under energy/materials"))
        changes.append(update_sector_map(conn, 251, sector_key="semiconductors", reason="CMP polishing material mapped under energy/materials"))
        changes.append(update_sector_map(conn, 219, sector_key="consumer", reason="OIS camera actuator is consumer/electronics, not auto"))

        # Company-level mappings that would reintroduce wrong sector attribution.
        for row_id in [26645, 26646, 26647, 26648]:
            changes.append(delete_row(conn, "hs_code_company_map", row_id, reason="NCH precursor company row incorrectly used crude-oil HS 270900; correct 2825902090 rows already exist"))
        for row_id in [25235, 25236, 25237, 26635, 26636]:
            changes.append(delete_row(conn, "hs_code_company_map", row_id, reason="SNT heat-exchanger row used lithium/lysine/plastic-film HS; correct 8419509000 row already exists"))
        for row_id in [26608, 26623, 26720, 26722]:
            changes.append(delete_row(conn, "hs_code_company_map", row_id, reason="HD construction-equipment engine row used radar HS; correct 8408909090 rows already exist"))
        for row_id in [26686, 26275]:
            changes.append(delete_row(conn, "hs_code_company_map", row_id, reason="YMT EV relay has exact 8536491000 row; remove broad relay rows that overlap power-equipment relay"))

        conn.commit()

        report = {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(DB_PATH),
            "change_count": sum(1 for item in changes if item["action"] in {"update", "delete"}),
            "changes": changes,
            "remaining_cross_sector_duplicates": remaining_cross_sector_duplicates(conn),
            "remaining_suspicious_keyword_rows": suspicious_keyword_rows(conn),
        }
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / "hs_mapping_mismatch_audit_20260628.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "change_count": report["change_count"],
            "remaining_cross_sector_duplicates": len(report["remaining_cross_sector_duplicates"]),
            "remaining_suspicious_keyword_rows": len(report["remaining_suspicious_keyword_rows"]),
            "report": str(out),
        }, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
