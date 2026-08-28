"""
routes/reports.py — 보고서 파일 API

  GET  /api/reports/stock/{stock_code}          # 개별종목 보고서
  GET  /api/reports/download/{report_id}        # 파일 다운로드
  GET  /api/reports/sectors                     # 섹터 목록 (통계)
  GET  /api/reports/sector/{sector}             # 섹터별 보고서
  POST /api/reports/extract/{report_id}         # PDF → AI 컨센서스 추출
  GET  /api/reports/extracts/{stock_code}       # 종목별 추출 결과 목록
"""

import json
import os
import sqlite3 as _sl

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path

router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

# ── analyst_pdf_extracts 테이블 자동 생성 ─────────────────────────
def _ensure_extracts_table():
    c = _sl.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS analyst_pdf_extracts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id    INTEGER NOT NULL UNIQUE,
            stock_code   TEXT,
            target_price REAL,
            opinion      TEXT,
            fwd_eps_1y   REAL,
            fwd_rev_1y   REAL,
            fwd_per      REAL,
            extracted_at TEXT DEFAULT (datetime('now','localtime')),
            raw_text     TEXT
        )
    """)
    c.commit()
    c.close()

_ensure_extracts_table()

_COLS = "id, channel_id, stock_name, report_date, file_name, saved_name, file_size, caption, stock_code, sector"

def _db():
    c = _sl.connect(DB_PATH, timeout=15)
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
    """보고서 파일 다운로드. drive_file_id가 있으면 구글 드라이브로 리다이렉트."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT file_path, saved_name, mime_type, drive_file_id FROM report_files WHERE id=?",
            (report_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="파일 없음")
    if row["drive_file_id"]:
        drive_url = f"https://drive.google.com/file/d/{row['drive_file_id']}/view?usp=sharing"
        return RedirectResponse(url=drive_url, status_code=302)
    fp = Path(row["file_path"])
    if not fp.exists():
        raise HTTPException(status_code=404, detail="파일이 로컬에도 없고 드라이브 ID도 없습니다")
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


# ── PDF 텍스트 추출 헬퍼 ────────────────────────────────────────────
def _extract_pdf_text(file_path: str, max_pages: int = 4) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            pages = pdf.pages[:max_pages]
            texts = []
            for p in pages:
                t = p.extract_text()
                if t:
                    texts.append(t)
            return "\n".join(texts)[:6000]
    except Exception:
        return ""


def _call_gpt_extract(text: str, stock_name: str) -> dict:
    """gpt-4o-mini로 애널리스트 보고서에서 컨센서스 지표 추출."""
    try:
        import openai
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        prompt = (
            f"다음은 한국 주식 애널리스트 보고서 텍스트입니다 (종목: {stock_name}).\n"
            "아래 JSON 형식으로 핵심 컨센서스 지표를 추출하세요. 없으면 null.\n\n"
            "{\n"
            '  "target_price": <목표주가 숫자, 원 단위>,\n'
            '  "opinion": <투자의견 문자열, 예: "매수"/"BUY"/"중립" 등>,\n'
            '  "fwd_eps_1y": <12개월 선행 EPS, 원 단위 숫자>,\n'
            '  "fwd_rev_1y": <12개월 선행 매출, 억원 단위 숫자>,\n'
            '  "fwd_per": <12개월 선행 PER, 배 단위 숫자>\n'
            "}\n\n"
            f"보고서 텍스트:\n{text}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {}


@router.post("/extract/{report_id}")
def extract_report(report_id: int):
    """PDF 보고서에서 AI로 컨센서스 지표 추출. 기존 결과가 있으면 반환."""
    conn = _db()
    try:
        existing = conn.execute(
            "SELECT * FROM analyst_pdf_extracts WHERE report_id=?", (report_id,)
        ).fetchone()
        if existing:
            return {
                "ok": True, "cached": True,
                "data": {k: existing[k] for k in existing.keys() if k != "raw_text"},
            }
        row = conn.execute(
            "SELECT file_path, saved_name, stock_code, stock_name FROM report_files WHERE id=?",
            (report_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="보고서 없음")

    file_path = row["file_path"] or ""
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="PDF 파일 없음")

    raw_text = _extract_pdf_text(file_path)
    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="PDF 텍스트 추출 실패")

    gpt_result = _call_gpt_extract(raw_text, row["stock_name"] or "")

    conn2 = _db()
    try:
        conn2.execute("""
            INSERT OR REPLACE INTO analyst_pdf_extracts
              (report_id, stock_code, target_price, opinion,
               fwd_eps_1y, fwd_rev_1y, fwd_per, raw_text)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            report_id,
            row["stock_code"] or "",
            gpt_result.get("target_price"),
            gpt_result.get("opinion"),
            gpt_result.get("fwd_eps_1y"),
            gpt_result.get("fwd_rev_1y"),
            gpt_result.get("fwd_per"),
            raw_text[:2000],
        ))
        conn2.commit()
        saved = conn2.execute(
            "SELECT * FROM analyst_pdf_extracts WHERE report_id=?", (report_id,)
        ).fetchone()
        return {
            "ok": True, "cached": False,
            "data": {k: saved[k] for k in saved.keys() if k != "raw_text"},
        }
    finally:
        conn2.close()


@router.get("/extracts/{stock_code}")
def get_extracts(stock_code: str):
    """종목별 PDF 추출 결과 목록 (report_files와 JOIN)."""
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT
                e.id, e.report_id, e.stock_code,
                e.target_price, e.opinion,
                e.fwd_eps_1y, e.fwd_rev_1y, e.fwd_per,
                e.extracted_at,
                r.file_name, r.report_date, r.channel_id
            FROM analyst_pdf_extracts e
            JOIN report_files r ON r.id = e.report_id
            WHERE e.stock_code = ?
            ORDER BY r.report_date DESC
        """, (stock_code,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
