"""
routes/reports.py — 보고서 파일 API

  GET /api/reports/stock/{stock_code}   # 개별종목 보고서
  GET /api/reports/download/{report_id} # 파일 다운로드
  GET /api/reports/sectors              # 섹터 목록 (통계)
  GET /api/reports/sector/{sector}      # 섹터별 보고서
"""

import sqlite3 as _sl

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

_COLS = "id, channel_id, stock_name, report_date, file_name, saved_name, file_size, caption, stock_code, sector"

def _db():
    c = _sl.connect(DB_PATH)
    c.row_factory = _sl.Row
    return c


def _row_to_dict(r):
    return {
        "id":          r["id"],
        "channel_id":  r["channel_id"],
        "stock_name":  r["stock_name"],
        "report_date": r["report_date"],
        "file_name":   r["file_name"],
        "saved_name":  r["saved_name"],
        "file_size":   r["file_size"],
        "caption":     r["caption"],
        "stock_code":  r["stock_code"] or "",
        "sector":      r["sector"] or "",
    }


@router.get("/stock/{stock_code}")
def get_stock_reports(stock_code: str):
    """개별 종목 보고서 목록.
    우선순위:
      1) report_files.stock_code 일치
      2) stock_universe에서 종목명 조회 → stock_name/file_name LIKE 검색
      3) report_files.stock_name 4자 LIKE 직접 검색 (폴백)
    """
    conn = _db()
    try:
        # 1) stock_code 직접 매칭
        rows = conn.execute(f"""
            SELECT {_COLS} FROM report_files
            WHERE stock_code=?
            ORDER BY report_date DESC LIMIT 50
        """, (stock_code,)).fetchall()

        if not rows:
            # 2) stock_universe에서 종목명 가져오기 (listed_company_info 대신)
            name_row = conn.execute(
                "SELECT stock_name FROM stock_universe WHERE stock_code=? LIMIT 1",
                (stock_code,)
            ).fetchone()
            if name_row:
                sname = name_row["stock_name"][:4]
                rows = conn.execute(f"""
                    SELECT {_COLS} FROM report_files
                    WHERE stock_name LIKE ? OR file_name LIKE ?
                    ORDER BY report_date DESC LIMIT 50
                """, (f"%{sname}%", f"%{sname}%")).fetchall()

        if not rows:
            # 3) report_files.stock_name 직접 LIKE 검색 — stock_code 6자리로 종목명 찾기
            # stock_universe에 없는 경우를 위한 최종 폴백
            rows = conn.execute(f"""
                SELECT {_COLS} FROM report_files
                WHERE stock_code LIKE ?
                ORDER BY report_date DESC LIMIT 50
            """, (f"%{stock_code}%",)).fetchall()

        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/download/{report_id}")
def download_report(report_id: int):
    """보고서 파일 다운로드."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT file_path, saved_name, mime_type FROM report_files WHERE id=?",
            (report_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="파일 없음")
    fp = Path(row["file_path"])
    if not fp.exists():
        raise HTTPException(status_code=404, detail="파일이 로컬에 없습니다")
    return FileResponse(str(fp), filename=row["saved_name"], media_type=row["mime_type"] or "application/octet-stream")


@router.get("/sectors")
def get_report_sectors():
    """섹터별 보고서 통계.
    stock_code가 없는(섹터 단위) 보고서만 집계.
    """
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT sector, COUNT(*) as cnt, MAX(report_date) as latest
            FROM report_files
            WHERE (stock_code IS NULL OR stock_code='')
              AND sector != '' AND sector IS NOT NULL
            GROUP BY sector ORDER BY cnt DESC
        """).fetchall()
        return [{"sector": r["sector"], "count": r["cnt"], "latest": r["latest"]} for r in rows]
    finally:
        conn.close()


@router.get("/sector/{sector:path}")
def get_sector_reports(sector: str, limit: int = 100):
    """특정 섹터 보고서 목록.
    개별종목으로 분류된 보고서는 종목 탭에서만 보여주고,
    섹터 화면에는 stock_code 없는 순수 섹터 보고서만 노출한다.
    """
    conn = _db()
    try:
        rows = conn.execute(f"""
            SELECT {_COLS} FROM report_files
            WHERE sector=?
              AND (stock_code IS NULL OR stock_code='')
            ORDER BY report_date DESC, id DESC
            LIMIT ?
        """, (sector, limit)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
