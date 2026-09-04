#!/usr/bin/env python3
"""Audit hard-coded leadership sector groups against StockEasy sector tags.

The report is intentionally strict for export-linked thematic groups. A stock can
belong to multiple real-world themes, but if StockEasy says it is in a clearly
different middle category, the hard-coded group should be reviewed before it
feeds sector signals or backtests.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


EXPECTED: dict[str, dict[str, set[str]]] = {
    "전력기기": {"major": {"전력/에너지"}, "middle": {"전력기기"}},
    "원자력": {"major": {"전력/에너지"}, "middle": {"원자력"}},
    "화장품/뷰티": {"major": {"K-컬처"}, "middle": {"화장품"}},
    "의료기기/미용": {"major": {"바이오"}, "middle": {"의료기기", "미용기기"}},
    "반도체": {"major": {"반도체"}, "middle": {"메모리", "반도체장비", "반도체소재", "테스트소켓", "비메모리/팹리스"}},
    "기판패키지": {"major": {"반도체"}, "middle": {"반도체소재", "테스트소켓"}},
    "2차전지": {"major": {"2차전지"}, "middle": {"배터리셀", "양극재", "음극재/소재", "전지장비"}},
    "방산": {"major": {"방산"}, "middle": {"방위산업", "우주항공"}},
    "조선": {"major": {"조선/해운"}, "middle": {"조선", "조선기자재"}},
    "바이오": {"major": {"바이오"}, "middle": {"CDMO", "제약", "바이오신약"}},
}


def _latest_snapshot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(source_snapshot_date) FROM stockeasy_sector_membership"
    ).fetchone()
    return row[0] if row and row[0] else None


def _stockeasy_map(conn: sqlite3.Connection, snapshot: str) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, sector_name, sector_level
        FROM stockeasy_sector_membership
        WHERE source_snapshot_date=?
        """,
        (snapshot,),
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for code, name, sector, level in rows:
        out.setdefault(code, {"stock_name": name or ""})
        out[code][level] = sector or ""
    return out


def main() -> None:
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from routes.sector_rotation import SECTOR_GROUPS

    conn = sqlite3.connect(DB_PATH)
    snapshot = _latest_snapshot(conn)
    if not snapshot:
        raise SystemExit("No StockEasy sector snapshot found")
    se = _stockeasy_map(conn, snapshot)

    rows = []
    missing = []
    mismatches = []
    for group, info in SECTOR_GROUPS.items():
        expected = EXPECTED.get(group, {"major": set(), "middle": set()})
        for code in info.get("codes", []):
            rec = se.get(code)
            if not rec:
                item = {"group": group, "code": code, "reason": "missing_in_stockeasy_snapshot"}
                rows.append({**item, "status": "missing"})
                missing.append(item)
                continue
            major = rec.get("major") or ""
            middle = rec.get("middle") or ""
            major_ok = not expected["major"] or major in expected["major"]
            middle_ok = not expected["middle"] or middle in expected["middle"]
            status = "ok" if (major_ok or middle_ok) and middle_ok else "mismatch"
            item = {
                "group": group,
                "code": code,
                "stock_name": rec.get("stock_name") or "",
                "stockeasy_major": major,
                "stockeasy_middle": middle,
                "expected_major": sorted(expected["major"]),
                "expected_middle": sorted(expected["middle"]),
                "status": status,
            }
            rows.append(item)
            if status != "ok":
                mismatches.append(item)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "sector_group_mapping_audit.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stockeasy_snapshot": snapshot,
        "group_count": len(SECTOR_GROUPS),
        "checked": len(rows),
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "mismatches": mismatches,
        "missing": missing,
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "stockeasy_snapshot": snapshot,
        "checked": len(rows),
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "output": str(out_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
