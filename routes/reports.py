"""
routes/reports.py — 보고서 파일 API

  GET /api/reports/stock/{stock_code}
  GET /api/reports/download/{report_id}
  GET /api/reports/sectors
  GET /api/reports/sector/{sector:path}
"""

import sqlite3 as _sl

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"


def _db():
    conn = _sl.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@router.get("/stock/{stock_code}")
def get_stock_reports(stock_code: str):
    """종목코드별 보고서 목록 — 코드 없으면 종목명으로 fallback."""
    conn = _db()
    rows = conn.execute("""
        SELECT id, channel_id, stock_name, report_date,
               file_name, saved_name, file_size, caption
        FROM report_files
        WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 50
    """, (stock_code,)).fetchall()
    if not rows:
        name_row = conn.execute(
            "SELECT stock_name FROM listed_company_info WHERE stock_code=? LIMIT 1",
            (stock_code,)
        ).fetchone()
        if name_row:
            sname = name_row[0][:4]
            rows = conn.execute("""
                SELECT id, channel_id, stock_name, report_date,
                       file_name, saved_name, file_size, caption
                FROM report_files
                WHERE stock_name LIKE ? OR file_name LIKE ?
                ORDER BY report_date DESC LIMIT 50
            """, (f"%{sname}%", f"%{sname}%")).fetchall()
    conn.close()
    return [{"id": r[0], "channel_id": r[1], "stock_name": r[2], "report_date": r[3],
             "file_name": r[4], "saved_name": r[5], "file_size": r[6], "caption": r[7]}
            for r in rows]


@router.get("/download/{report_id}")
def download_report(report_id: int):
    """보고서 파일 다운로드."""
    conn = _db()
    row = conn.execute(
        "SELECT file_path, saved_name, mime_type FROM report_files WHERE id=?",
        (report_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="파일 없음")
    fp = Path(row[0])
    if not fp.exists():
        raise HTTPException(status_code=404, detail="파일이 로컬에 없습니다")
    return FileResponse(str(fp), filename=row[1], media_type=row[2] or "application/octet-stream")


@router.get("/sectors")
def get_report_sectors():
    """섹터별 보고서 통계 (종목코드 없는 것만)."""
    conn = _db()
    rows = conn.execute("""
        SELECT sector, COUNT(*) as cnt, MAX(report_date) as latest
        FROM report_files
        WHERE (stock_code IS NULL OR stock_code='')
          AND sector != '' AND sector IS NOT NULL
        GROUP BY sector ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    return [{"sector": r[0], "count": r[1], "latest": r[2]} for r in rows]


@router.get("/sector/{sector:path}")
def get_sector_reports(sector: str, limit: int = 50):
    """특정 섹터 보고서 목록 (종목코드 없는 것만)."""
    conn = _db()
    rows = conn.execute("""
        SELECT id, channel_id, stock_name, report_date,
               file_name, saved_name, file_size, caption
        FROM report_files
        WHERE sector=? AND (stock_code IS NULL OR stock_code='')
        ORDER BY report_date DESC, id DESC LIMIT ?
    """, (sector, limit)).fetchall()
    conn.close()
    return [{"id": r[0], "channel_id": r[1], "stock_name": r[2], "report_date": r[3],
             "file_name": r[4], "saved_name": r[5], "file_size": r[6], "caption": r[7]}
            for r in rows]
