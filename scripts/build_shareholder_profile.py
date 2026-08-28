#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_shareholder_profile (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            base_date TEXT NOT NULL DEFAULT '',
            shares_issued REAL,
            shares_outstanding REAL,
            float_shares REAL,
            treasury_shares_est REAL,
            free_float_ratio REAL,
            major_holder_name TEXT NOT NULL DEFAULT '',
            major_holder_shares REAL,
            major_holder_ratio REAL,
            major_holder_report_date TEXT NOT NULL DEFAULT '',
            major_holder_report_no TEXT NOT NULL DEFAULT '',
            major_holder_report_type TEXT NOT NULL DEFAULT '',
            major_holder_count INTEGER NOT NULL DEFAULT 0,
            data_quality TEXT NOT NULL DEFAULT 'unknown',
            quality_note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_shareholder_profile_market ON stock_shareholder_profile(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_shareholder_profile_holder ON stock_shareholder_profile(major_holder_name)")


def latest_major_holder(conn: sqlite3.Connection, stock_code: str) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT repror, stkqy, stkrt, rcept_dt, rcept_no, report_tp
        FROM (
            SELECT repror, stkqy, stkrt, rcept_dt, rcept_no, report_tp,
                   ROW_NUMBER() OVER (PARTITION BY repror ORDER BY rcept_dt DESC, id DESC) AS rn
            FROM dart_major_holders
            WHERE stock_code = ?
              AND COALESCE(repror, '') <> ''
        )
        WHERE rn = 1
        ORDER BY COALESCE(stkrt, 0) DESC, COALESCE(stkqy, 0) DESC
        LIMIT 20
        """,
        (stock_code,),
    ).fetchall()
    if not rows:
        return {
            "name": "",
            "shares": None,
            "ratio": None,
            "date": "",
            "rcept_no": "",
            "report_type": "",
            "count": 0,
        }
    top = rows[0]
    return {
        "name": top["repror"] or "",
        "shares": top["stkqy"],
        "ratio": top["stkrt"],
        "date": top["rcept_dt"] or "",
        "rcept_no": top["rcept_no"] or "",
        "report_type": top["report_tp"] or "",
        "count": len(rows),
    }


def classify_quality(float_shares: float | None, issued: float | None, holder_name: str) -> tuple[str, str]:
    notes: list[str] = []
    quality = "ok"
    if not float_shares:
        quality = "missing_float"
        notes.append("유통주식수 없음")
    if not issued:
        quality = "missing_issued" if quality == "ok" else quality
        notes.append("발행주식수 없음")
    if float_shares and issued and float_shares > issued * 1.02:
        quality = "review"
        notes.append("유통주식수가 발행주식수보다 큼")
    if not holder_name:
        quality = "partial" if quality == "ok" else quality
        notes.append("DART 5% 주요주주 최신값 없음")
    return quality, " / ".join(notes)


def rebuild(limit: int = 0) -> dict[str, object]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    rows = conn.execute(
        """
        SELECT su.stock_code, su.stock_name, su.market, su.base_date,
               su.shares_issued, sm.shares_outstanding, sm.float_shares
        FROM stock_universe su
        LEFT JOIN stock_meta sm ON sm.stock_code = su.stock_code
        WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND su.market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
        ORDER BY su.market_cap DESC
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]
    now = datetime.now().isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for row in rows:
        issued = float(row["shares_issued"] or 0) or None
        outstanding = float(row["shares_outstanding"] or 0) or issued
        float_shares = float(row["float_shares"] or 0) or None
        treasury = issued - outstanding if issued and outstanding and issued >= outstanding else None
        free_float_ratio = (float_shares / issued * 100.0) if float_shares and issued else None
        major = latest_major_holder(conn, row["stock_code"])
        quality, note = classify_quality(float_shares, issued, str(major["name"] or ""))
        counts[quality] = counts.get(quality, 0) + 1
        source_parts = ["stock_universe.shares_issued", "stock_meta.float_shares"]
        if major["name"]:
            source_parts.append("DART.majorstock")
        conn.execute(
            """
            INSERT INTO stock_shareholder_profile (
                stock_code, stock_name, market, base_date, shares_issued,
                shares_outstanding, float_shares, treasury_shares_est,
                free_float_ratio, major_holder_name, major_holder_shares,
                major_holder_ratio, major_holder_report_date, major_holder_report_no,
                major_holder_report_type, major_holder_count,
                data_quality, quality_note, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                market=excluded.market,
                base_date=excluded.base_date,
                shares_issued=excluded.shares_issued,
                shares_outstanding=excluded.shares_outstanding,
                float_shares=excluded.float_shares,
                treasury_shares_est=excluded.treasury_shares_est,
                free_float_ratio=excluded.free_float_ratio,
                major_holder_name=excluded.major_holder_name,
                major_holder_shares=excluded.major_holder_shares,
                major_holder_ratio=excluded.major_holder_ratio,
                major_holder_report_date=excluded.major_holder_report_date,
                major_holder_report_no=excluded.major_holder_report_no,
                major_holder_report_type=excluded.major_holder_report_type,
                major_holder_count=excluded.major_holder_count,
                data_quality=excluded.data_quality,
                quality_note=excluded.quality_note,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                row["stock_code"], row["stock_name"], row["market"], row["base_date"],
                issued, outstanding, float_shares, treasury, free_float_ratio,
                major["name"], major["shares"], major["ratio"], major["date"],
                major["rcept_no"], major["report_type"], major["count"],
                quality, note, " + ".join(source_parts), now,
            ),
        )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM stock_shareholder_profile").fetchone()[0]
    conn.close()
    return {"rows_processed": len(rows), "table_rows": total, "quality_counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build consolidated float share and major-holder profile table.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
