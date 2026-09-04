from __future__ import annotations

import json
import os
import re
import sqlite3
import math
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
import pandas as pd
import requests
from services.gemini import generate_text, is_configured
from pydantic import BaseModel

DB_PATH = "stock.db"
router = APIRouter()
POSTS_PAGE_SIZE_MAX = 100


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detailed_analysis_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT,
            stock_name TEXT NOT NULL,
            title TEXT NOT NULL,
            content_md TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detailed_analysis_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT DEFAULT 'xlsx',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(post_id) REFERENCES detailed_analysis_posts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dap_stock ON detailed_analysis_posts(stock_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dap_updated ON detailed_analysis_posts(updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daf_post ON detailed_analysis_files(post_id)")


class PostUpsert(BaseModel):
    stock_code: Optional[str] = None
    stock_name: str
    title: str
    content_md: str
    source: str = "manual"
    files: List[str] = []


class BootstrapRequest(BaseModel):
    file_paths: List[str]


def _extract_stock_name(file_path: str) -> str:
    stem = Path(file_path).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    stock = re.split(r"[_\-]", stem)[0].strip()
    return stock or stem


def _norm_text(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).upper()


def _tokenize_stocks_field(value: str | None) -> set[str]:
    text = str(value or "")
    raw_tokens = re.split(r"[,/|;·\s]+", text)
    tokens = {_norm_text(tok) for tok in raw_tokens if tok and _norm_text(tok)}
    return tokens


def _report_file_matches_stock(row: sqlite3.Row | dict, stock_name: str, stock_code: Optional[str]) -> bool:
    name_norm = _norm_text(stock_name)
    code_norm = _norm_text(stock_code)
    row_name_norm = _norm_text(row.get("stock_name") if isinstance(row, dict) else row["stock_name"])
    row_code_norm = _norm_text(row.get("stock_code") if isinstance(row, dict) else row["stock_code"])
    file_name_norm = _norm_text(row.get("file_name") if isinstance(row, dict) else row["file_name"])
    caption_norm = _norm_text(row.get("caption") if isinstance(row, dict) else row["caption"])

    if code_norm and row_code_norm == code_norm:
        return True
    if name_norm and row_name_norm == name_norm:
        return True

    # stock_code / stock_name 메타가 비어 있는 예전 데이터만 제한적으로 파일명/캡션 fallback 허용
    if not row_code_norm and not row_name_norm and name_norm:
        if name_norm in file_name_norm or name_norm in caption_norm:
            return True
    return False


def _telegram_message_matches_stock(row: sqlite3.Row | dict, stock_name: str, stock_code: Optional[str]) -> bool:
    name_norm = _norm_text(stock_name)
    code_norm = _norm_text(stock_code)
    tokens = _tokenize_stocks_field(row.get("stocks") if isinstance(row, dict) else row["stocks"])
    if code_norm and code_norm in tokens:
        return True
    if name_norm and name_norm in tokens:
        return True
    return False


def _find_stock_code(conn: sqlite3.Connection, stock_name: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT stock_code
        FROM stock_universe
        WHERE stock_name=?
        LIMIT 1
        """,
        (stock_name,),
    ).fetchone()
    return row["stock_code"] if row else None


def _safe_num(v):
    try:
        n = float(v)
        return n
    except Exception:
        return None


def _fmt_num(v: Optional[float], unit: str = "") -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}{unit}"


def _extract_quarter_key(text: str) -> Optional[str]:
    s = (text or "").lower()
    m = re.search(r"(20\d{2})[._\- ]?([1-4])q", s)
    if m:
        return f"{m.group(1)}q{m.group(2)}"
    m = re.search(r"(\d{2})[._\- ]?([1-4])q", s)
    if m:
        return f"20{m.group(1)}q{m.group(2)}"
    m = re.search(r"(20\d{2})[^0-9]?([1-4])분기", text or "")
    if m:
        return f"{m.group(1)}q{m.group(2)}"
    return None


def _file_group_key(file_name: str) -> str:
    stem = Path(file_name or "").stem
    low = stem.lower()
    # 예상실적/26E/27E/28E는 별도 그룹으로 유지
    if any(k in low for k in ["예상실적", "forecast", "26e", "27e", "28e"]):
        return "forecast_26e_28e"
    qk = _extract_quarter_key(stem)
    if qk:
        return f"quarter_{qk}"
    if "밸류에이션" in stem or "valuation" in low:
        return "valuation"
    if "리포트" in stem or "report" in low:
        return "report"
    # REV/버전/날짜/중복표기 제거 후 기본 그룹화
    norm = low
    norm = re.sub(r"rev(?:ision)?[ ._\-]?\d+", "", norm)
    norm = re.sub(r"\bver(?:sion)?[ ._\-]?\d+\b", "", norm)
    norm = re.sub(r"20\d{6,8}", "", norm)
    norm = re.sub(r"\d{2}[._\-]?\d{2}[._\-]?\d{2}", "", norm)
    norm = re.sub(r"\(\d+\)$", "", norm)
    norm = re.sub(r"\s+", " ", norm).strip(" _.-")
    return norm or low


def _dedupe_latest_files(rows: list[dict], date_fields: list[str]) -> list[dict]:
    groups = {}
    for r in rows:
        key = _file_group_key(r.get("file_name", ""))
        best = groups.get(key)
        if not best:
            groups[key] = r
            continue

        def dt_of(x):
            for f in date_fields:
                v = x.get(f)
                if v:
                    try:
                        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                    except Exception:
                        pass
            return datetime.min

        if dt_of(r) >= dt_of(best):
            groups[key] = r

    deduped = list(groups.values())

    def sort_dt(x):
        for f in date_fields:
            v = x.get(f)
            if v:
                try:
                    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                except Exception:
                    pass
        return datetime.min

    deduped.sort(key=sort_dt, reverse=True)
    return deduped


def _to_eok(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    # DB 값이 원 단위로 저장된 경우(대형 수치) 억 단위로 변환한다.
    if abs(v) >= 1_000_000:
        return v / 100_000_000
    return v


def _collect_financial_context(conn: sqlite3.Connection, stock_code: Optional[str]) -> str:
    if not stock_code:
        return "재무 데이터 없음"
    rows = conn.execute(
        """
        SELECT year, quarter, revenue, operating_profit, net_income
        FROM financial_data
        WHERE stock_code=? AND is_annual=0 AND report_type='CFS'
        ORDER BY year DESC, quarter DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    if not rows:
        return "분기 재무 데이터 없음"

    lines = []
    for r in rows:
        lines.append(
            f"{r['year']}Q{r['quarter']}: 매출={_fmt_num(_to_eok(_safe_num(r['revenue'])), '억')}, "
            f"영업이익={_fmt_num(_to_eok(_safe_num(r['operating_profit'])), '억')}, "
            f"순이익={_fmt_num(_to_eok(_safe_num(r['net_income'])), '억')}"
        )
    return "\n".join(lines)


def _collect_cashflow_context(conn: sqlite3.Connection, stock_code: Optional[str]) -> str:
    if not stock_code:
        return "현금흐름 데이터 없음"
    rows = conn.execute(
        """
        SELECT year, quarter, operating_cf, investing_cf, financing_cf, capex
        FROM cash_flow_data
        WHERE stock_code=? AND is_annual=0 AND report_type='CFS'
        ORDER BY year DESC, quarter DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    if not rows:
        return "분기 현금흐름 데이터 없음"
    lines = []
    for r in rows:
        lines.append(
            f"{r['year']}Q{r['quarter']}: 영업CF={_fmt_num(_to_eok(_safe_num(r['operating_cf'])), '억')}, "
            f"투자CF={_fmt_num(_to_eok(_safe_num(r['investing_cf'])), '억')}, "
            f"재무CF={_fmt_num(_to_eok(_safe_num(r['financing_cf'])), '억')}, "
            f"CAPEX={_fmt_num(_to_eok(_safe_num(r['capex'])), '억')}"
        )
    return "\n".join(lines)


def _collect_disclosure_context(conn: sqlite3.Connection, stock_code: Optional[str]) -> str:
    if not stock_code:
        return "공시 데이터 없음"
    rows = conn.execute(
        """
        SELECT rcept_dt, report_nm
        FROM dart_disclosures
        WHERE stock_code=?
        ORDER BY rcept_dt DESC
        LIMIT 8
        """,
        (stock_code,),
    ).fetchall()
    if not rows:
        return "최근 공시 없음"
    return "\n".join([f"{r['rcept_dt']} {r['report_nm']}" for r in rows])


def _collect_telegram_signal_context(conn: sqlite3.Connection, stock_name: str, stock_code: Optional[str]) -> str:
    rows = conn.execute(
        """
        SELECT report_date, file_name, caption, stock_name, stock_code
        FROM report_files
        WHERE (
            (stock_name IS NOT NULL AND stock_name != '' AND stock_name = ?)
            OR (stock_code IS NOT NULL AND stock_code != '' AND stock_code = ?)
            OR (file_name IS NOT NULL AND file_name LIKE '%' || ? || '%')
            OR (caption IS NOT NULL AND caption LIKE '%' || ? || '%')
        )
        ORDER BY COALESCE(report_date, created_at) DESC
        LIMIT 20
        """,
        (stock_name, stock_code, stock_name, stock_name),
    ).fetchall()
    lines = []
    filtered_rows = [r for r in rows if _report_file_matches_stock(r, stock_name, stock_code)]
    for r in filtered_rows:
        cap = (r["caption"] or "").replace("\n", " ").strip()
        if cap:
            cap = cap[:180]
        lines.append(f"{r['report_date'] or '-'} | {r['file_name'] or '-'} | {cap}")

    msg_rows = conn.execute(
        """
        SELECT date, text, stocks
        FROM telegram_messages
        WHERE stocks LIKE '%' || ? || '%'
        ORDER BY date DESC
        LIMIT 40
        """,
        (stock_name,),
    ).fetchall()
    filtered_msg_rows = [r for r in msg_rows if _telegram_message_matches_stock(r, stock_name, stock_code)]
    for r in filtered_msg_rows[:12]:
        txt = (r["text"] or "").replace("\n", " ").strip()[:180]
        lines.append(f"{r['date'] or '-'} | {txt}")
    if not lines:
        return "연관 텔레그램 데이터 없음"
    return "\n".join(lines)


def _openai_mini_investment_analysis(stock_name: str, stock_code: Optional[str], files: List[str], fin_ctx: str, cf_ctx: str, disc_ctx: str, tg_ctx: str) -> str:
    file_list = "\n".join([f"- {Path(fp).name}" for fp in files]) if files else "- 없음"

    fallback = (
        f"# {stock_name} 투자관점 상세분석\n\n"
        "## 1) 최근 실적 흐름\n"
        f"{fin_ctx}\n\n"
        "## 2) 현금흐름/투자강도\n"
        f"{cf_ctx}\n\n"
        "## 3) 최근 공시 핵심\n"
        f"{disc_ctx}\n\n"
        "## 4) 텔레그램/첨부자료 관심 포인트\n"
        f"{tg_ctx}\n\n"
        "## 5) 투자관점 체크포인트\n"
        "- 매출/영업이익의 동반 개선 여부\n"
        "- 영업CF가 이익을 따라오는지 여부\n"
        "- CAPEX 확대가 성장 투자 성격인지 점검\n"
        "- 최근 공시에서 수요/원가/재고/인력 관련 방향성 확인\n"
    )
    if not is_configured():
        return fallback

    prompt = f"""
너는 한국주식 투자분석가다. 아래 데이터(재무/현금흐름/공시/첨부파일)를 기반으로 {stock_name}({stock_code or '-'})을 분석해라.

중요 규칙:
1) 투자자 관점으로 작성: 매출 증가/감소, 원가·재료비 압력, 재고 변화, 인력/판관비, 현금흐름 질, 공시 이벤트의 실적영향.
2) 확실하지 않은 내용은 추정이라고 표시.
3) 의미 없는 시스템 안내문구(키 미설정, 시트 수 등) 절대 금지.
4) 결과 형식:
   - 한줄결론
   - 실적/수익성 변화
   - 현금흐름·투자(CAPEX) 해석
   - 공시 기반 포인트(긍정/부정)
   - 텔레그램/첨부자료 기반 '왜 지금 관심 가져야 하는지'
   - 투자 관점 리스크
   - 다음 분기 확인지표 3개

[첨부파일 목록]
{file_list}

[재무 분기 데이터]
{fin_ctx}

[현금흐름 분기 데이터]
{cf_ctx}

[최근 공시]
{disc_ctx}

[텔레그램 연관 텍스트]
{tg_ctx}
"""
    try:
        text = generate_text(
            prompt,
            system_instruction="당신은 숫자 기반 주식 투자 애널리스트다.",
            temperature=0.2,
            max_output_tokens=1500,
            timeout=60,
        )
        return f"# {stock_name} 투자관점 상세분석\n\n{text}"
    except Exception:
        return fallback


def _attach_related_telegram_files(conn: sqlite3.Connection, post_id: int, stock_name: str, stock_code: Optional[str]) -> int:
    q = """
        SELECT file_name, file_path, mime_type
        FROM report_files
        WHERE (
            (stock_name IS NOT NULL AND stock_name != '' AND stock_name = ?)
            OR (stock_code IS NOT NULL AND stock_code != '' AND stock_code = ?)
            OR (file_name IS NOT NULL AND file_name LIKE '%' || ? || '%')
            OR (caption IS NOT NULL AND caption LIKE '%' || ? || '%')
        )
        ORDER BY COALESCE(report_date, created_at) DESC
        LIMIT 100
    """
    rows = conn.execute(q, (stock_name, stock_code, stock_name, stock_name)).fetchall()
    added = 0
    for r in rows:
        if not _report_file_matches_stock(r, stock_name, stock_code):
            continue
        fp = r["file_path"] or ""
        fn = r["file_name"] or Path(fp).name or "telegram_file"
        exists = conn.execute(
            "SELECT 1 FROM detailed_analysis_files WHERE post_id=? AND file_path=? LIMIT 1",
            (post_id, fp),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO detailed_analysis_files (post_id, file_name, file_path, file_type)
            VALUES (?, ?, ?, ?)
            """,
            (post_id, fn, fp, "telegram"),
        )
        added += 1
    return added


def _upsert_file(conn: sqlite3.Connection, post_id: int, fp: str, file_type: str = "xlsx") -> None:
    if not fp:
        return
    exists = conn.execute(
        "SELECT id FROM detailed_analysis_files WHERE post_id=? AND file_path=? LIMIT 1",
        (post_id, fp),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """
        INSERT INTO detailed_analysis_files (post_id, file_name, file_path, file_type)
        VALUES (?, ?, ?, ?)
        """,
        (post_id, Path(fp).name, fp, file_type),
    )


def _consolidate_to_single_post_per_stock(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT stock_name, GROUP_CONCAT(id) ids
        FROM detailed_analysis_posts
        GROUP BY stock_name
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    removed = 0
    for r in rows:
        ids = [int(x) for x in (r["ids"] or "").split(",") if x]
        keep = max(ids)
        drop = [x for x in ids if x != keep]
        for did in drop:
            files = conn.execute("SELECT file_path, file_name, file_type FROM detailed_analysis_files WHERE post_id=?", (did,)).fetchall()
            for f in files:
                _upsert_file(conn, keep, f["file_path"], f["file_type"] or "file")
            conn.execute("DELETE FROM detailed_analysis_files WHERE post_id=?", (did,))
            conn.execute("DELETE FROM detailed_analysis_posts WHERE id=?", (did,))
            removed += 1
    return removed


@router.get("/posts")
def list_posts(
    q: str = Query("", description="stock_name/title keyword"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=POSTS_PAGE_SIZE_MAX),
):
    with _conn() as conn:
        _ensure_tables(conn)
        total = conn.execute(
            """
            WITH ranked AS (
                SELECT
                    p.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(NULLIF(TRIM(p.stock_code), ''), TRIM(p.stock_name))
                        ORDER BY datetime(p.updated_at) DESC, p.id DESC
                    ) AS rn
                FROM detailed_analysis_posts p
                WHERE (? = '' OR p.stock_name LIKE '%' || ? || '%' OR p.title LIKE '%' || ? || '%')
            )
            SELECT COUNT(*)
            FROM ranked
            WHERE rn = 1
            """,
            (q, q, q),
        ).fetchone()[0]
        total_pages = max(1, math.ceil(total / page_size))
        safe_page = min(page, total_pages)
        offset = (safe_page - 1) * page_size
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT
                    p.id, p.stock_code, p.stock_name, p.title, p.source, p.created_at, p.updated_at,
                    su.market AS market,
                    su.market_cap AS market_cap,
                    su.close AS current_price,
                    su.change_rate AS change_rate,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(NULLIF(TRIM(p.stock_code), ''), TRIM(p.stock_name))
                        ORDER BY datetime(p.updated_at) DESC, p.id DESC
                    ) AS rn
                FROM detailed_analysis_posts p
                LEFT JOIN stock_universe su
                  ON su.stock_code = p.stock_code
                 AND su.base_date = (
                    SELECT MAX(su2.base_date) FROM stock_universe su2 WHERE su2.stock_code = p.stock_code
                 )
                WHERE (? = '' OR p.stock_name LIKE '%' || ? || '%' OR p.title LIKE '%' || ? || '%')
            )
            SELECT id, stock_code, stock_name, title, source, created_at, updated_at, market, market_cap, current_price, change_rate
            FROM ranked
            WHERE rn = 1
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (q, q, q, page_size, offset),
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": safe_page,
            "page_size": page_size,
            "total_pages": total_pages,
        }


@router.get("/posts/{post_id}")
def get_post(post_id: int):
    with _conn() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            """
            SELECT id, stock_code, stock_name, title, content_md, source, created_at, updated_at
            FROM detailed_analysis_posts
            WHERE id=?
            """,
            (post_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="post not found")

        files = conn.execute(
            """
            SELECT id, file_name, file_path, file_type, created_at
            FROM detailed_analysis_files
            WHERE post_id=?
            ORDER BY id ASC
            """,
            (post_id,),
        ).fetchall()
        file_paths = [f["file_path"] for f in files if f["file_path"]]
        channel_by_path = {}
        if file_paths:
            ph = ",".join(["?"] * len(file_paths))
            q = f"""
                SELECT file_path, channel_id
                FROM report_files
                WHERE file_path IN ({ph})
            """
            for rr in conn.execute(q, tuple(file_paths)).fetchall():
                fp = rr["file_path"] or ""
                ch = rr["channel_id"] or ""
                if fp and ch and fp not in channel_by_path:
                    channel_by_path[fp] = ch

        tg_files = conn.execute(
            """
            SELECT id, channel_id, message_id, file_name, file_path, report_date, caption, mime_type, created_at, stock_name, stock_code
            FROM report_files
            WHERE (
                (stock_name IS NOT NULL AND stock_name != '' AND stock_name = ?)
                OR (stock_code IS NOT NULL AND stock_code != '' AND stock_code = ?)
                OR (file_name IS NOT NULL AND file_name LIKE '%' || ? || '%')
                OR (caption IS NOT NULL AND caption LIKE '%' || ? || '%')
            )
            ORDER BY COALESCE(report_date, created_at) DESC, id DESC
            """,
            (row["stock_name"], row["stock_code"], row["stock_name"], row["stock_name"]),
        ).fetchall()
        tg_files = [f for f in tg_files if _report_file_matches_stock(f, row["stock_name"], row["stock_code"])]

        tg_msgs = conn.execute(
            """
            SELECT channel, message_id, text, date, stocks
            FROM telegram_messages
            WHERE stocks LIKE '%' || ? || '%'
            ORDER BY date DESC
            LIMIT 80
            """,
            (row["stock_name"],),
        ).fetchall()
        tg_msgs = [m for m in tg_msgs if _telegram_message_matches_stock(m, row["stock_name"], row["stock_code"])]

        out = dict(row)
        normalized_files = []
        for f in files:
            fd = dict(f)
            fp = fd.get("file_path") or ""
            modified_at = None
            if fp and Path(fp).exists():
                modified_at = datetime.fromtimestamp(Path(fp).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            fd["modified_at"] = modified_at
            fd["channel_id"] = channel_by_path.get(fp)
            fd["download_url"] = f"/api/detailed-analysis/files/{fd['id']}/download"
            normalized_files.append(fd)
        out["files"] = _dedupe_latest_files(normalized_files, ["modified_at", "created_at"])
        normalized_tg_files = []
        for f in tg_files:
            tf = dict(f)
            fp = tf.get("file_path") or ""
            modified_at = None
            if fp and Path(fp).exists():
                modified_at = datetime.fromtimestamp(Path(fp).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            tf["modified_at"] = modified_at
            tf["download_url"] = f"/api/detailed-analysis/report-files/{tf['id']}/download"
            normalized_tg_files.append(tf)
        out["telegram_files"] = _dedupe_latest_files(normalized_tg_files, ["report_date", "modified_at", "created_at"])
        out["telegram_messages"] = [dict(m) for m in tg_msgs]
        return out


@router.get("/files/{file_id}/download")
def download_file(file_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT file_name, file_path FROM detailed_analysis_files WHERE id=? LIMIT 1",
            (file_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="file not found")
        fp = row["file_path"] or ""
        p = Path(fp)
        if not p.exists():
            raise HTTPException(status_code=404, detail="file missing on disk")
        return FileResponse(path=str(p), filename=row["file_name"], media_type="application/octet-stream")


@router.get("/report-files/{report_file_id}/download")
def download_report_file(report_file_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT file_name, file_path FROM report_files WHERE id=? LIMIT 1",
            (report_file_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="report file not found")
        fp = row["file_path"] or ""
        p = Path(fp)
        if not p.exists():
            raise HTTPException(status_code=404, detail="file missing on disk")
        return FileResponse(path=str(p), filename=row["file_name"], media_type="application/octet-stream")


@router.post("/posts")
def upsert_post(payload: PostUpsert):
    with _conn() as conn:
        _ensure_tables(conn)
        existing = conn.execute(
            "SELECT id FROM detailed_analysis_posts WHERE stock_name=? LIMIT 1",
            (payload.stock_name,),
        ).fetchone()

        if existing:
            post_id = existing["id"]
            conn.execute(
                """
                UPDATE detailed_analysis_posts
                SET stock_code=?, title=?, content_md=?, source=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (
                    payload.stock_code,
                    payload.title,
                    payload.content_md,
                    payload.source,
                    post_id,
                ),
            )
            conn.execute("DELETE FROM detailed_analysis_files WHERE post_id=?", (post_id,))
        else:
            cur = conn.execute(
                """
                INSERT INTO detailed_analysis_posts (stock_code, stock_name, title, content_md, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload.stock_code,
                    payload.stock_name,
                    payload.title,
                    payload.content_md,
                    payload.source,
                ),
            )
            post_id = cur.lastrowid

        for fp in payload.files:
            if not fp:
                continue
            conn.execute(
                """
                INSERT INTO detailed_analysis_files (post_id, file_name, file_path, file_type)
                VALUES (?, ?, ?, ?)
                """,
                (post_id, Path(fp).name, fp, Path(fp).suffix.replace(".", "") or "file"),
            )

        conn.commit()
        return {"ok": True, "post_id": post_id}


@router.post("/bootstrap")
def bootstrap_from_files(payload: BootstrapRequest):
    created = 0
    updated = 0
    attached_related = 0

    with _conn() as conn:
        _ensure_tables(conn)
        _consolidate_to_single_post_per_stock(conn)
        for fp in payload.file_paths:
            if not fp:
                continue
            stock_name = _extract_stock_name(fp)
            code = _find_stock_code(conn, stock_name)
            title = f"{stock_name} 상세분석"

            existing = conn.execute(
                "SELECT id FROM detailed_analysis_posts WHERE stock_name=? LIMIT 1",
                (stock_name,),
            ).fetchone()

            if existing:
                post_id = existing["id"]
                updated += 1
            else:
                cur = conn.execute(
                    """
                    INSERT INTO detailed_analysis_posts (stock_code, stock_name, title, content_md, source)
                    VALUES (?, ?, ?, ?, 'xlsx_bootstrap')
                    """,
                    (code, stock_name, title, ""),
                )
                post_id = cur.lastrowid
                created += 1

            _upsert_file(conn, post_id, fp, Path(fp).suffix.replace(".", "") or "file")
            attached_related += _attach_related_telegram_files(conn, post_id, stock_name, code)
            all_xlsx = conn.execute(
                """
                SELECT file_path
                FROM detailed_analysis_files
                WHERE post_id=? AND file_type != 'telegram'
                ORDER BY id ASC
                """,
                (post_id,),
            ).fetchall()
            file_paths = [x["file_path"] for x in all_xlsx if x["file_path"]]
            fin_ctx = _collect_financial_context(conn, code)
            cf_ctx = _collect_cashflow_context(conn, code)
            disc_ctx = _collect_disclosure_context(conn, code)
            tg_ctx = _collect_telegram_signal_context(conn, stock_name, code)
            content_md = _openai_mini_investment_analysis(stock_name, code, file_paths, fin_ctx, cf_ctx, disc_ctx, tg_ctx)
            conn.execute(
                """
                UPDATE detailed_analysis_posts
                SET stock_code=?, title=?, content_md=?, source='openai_mini_auto', updated_at=datetime('now')
                WHERE id=?
                """,
                (code, title, content_md, post_id),
            )

        conn.commit()

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "files": len([x for x in payload.file_paths if x]),
        "related_attached": attached_related,
    }


@router.get("/telegram/channel-link")
def telegram_channel_link():
    return {
        "channel_invite": "https://t.me/+Sx6sQ6wCwzE5MWM1",
        "note": "텔레그램 비공개 채널은 서버 인증 세션이 있어야 자동 수집됩니다. 현재는 DB에 수집된 파일/메시지를 종목별로 자동 연동합니다.",
    }


@router.post("/normalize")
def normalize_posts():
    with _conn() as conn:
        _ensure_tables(conn)
        removed = _consolidate_to_single_post_per_stock(conn)
        rows = conn.execute("SELECT id, stock_name FROM detailed_analysis_posts").fetchall()
        for r in rows:
            conn.execute(
                "UPDATE detailed_analysis_posts SET title=?, updated_at=datetime('now') WHERE id=?",
                (f"{r['stock_name']} 상세분석", r["id"]),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) c FROM detailed_analysis_posts").fetchone()["c"]
    return {"ok": True, "removed_duplicates": removed, "posts": total}
