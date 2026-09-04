from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")


def normalize(text: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", (text or "")).upper()


def load_candidates(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """
        WITH names AS (
            SELECT stock_code, stock_name, market
            FROM stock_universe
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
              AND COALESCE(stock_type, '보통주')='보통주'
            UNION
            SELECT stock_code, stock_name, market
            FROM stock_meta
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND UPPER(COALESCE(market, '')) IN ('KOSPI', 'KOSDAQ')
        )
        SELECT stock_code, stock_name, MAX(market)
        FROM names
        GROUP BY stock_code, stock_name
        """
    ).fetchall()
    out = []
    for code, name, _ in rows:
        nm = str(name or "").strip()
        nn = normalize(nm)
        if len(nn) < 2:
            continue
        out.append((str(code).zfill(6), nm, nn))
    out.sort(key=lambda x: len(x[2]), reverse=True)
    return out


def find_stock(text: str, candidates: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    nn = normalize(text)
    if not nn:
        return None
    best: tuple[float, tuple[str, str]] | None = None
    for code, name, norm_name in candidates:
        pos = nn.find(norm_name)
        if pos < 0:
            continue
        # 앞쪽 등장 + 긴 종목명 우선
        score = (200 - min(pos, 200)) + (len(norm_name) * 1.5)
        # 증권사명(작성자) 오탐 방지: 문서 후반부에 등장하는 '...증권'은 강한 패널티
        if name.endswith("증권") and pos > 24:
            score -= 120
        if best is None or score > best[0]:
            best = (score, (code, name))
    return best[1] if best else None


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    candidates = load_candidates(conn)

    rows = conn.execute(
        """
        SELECT id, stock_code, stock_name, sector, file_name, saved_name, caption
        FROM report_files
        WHERE (stock_code IS NULL OR stock_code='')
          AND COALESCE(sector,'') <> ''
        ORDER BY id
        """
    ).fetchall()

    updated = 0
    for r in rows:
        text = " ".join(str(r[k] or "") for k in ("file_name", "saved_name", "caption", "stock_name"))
        m = find_stock(text, candidates)
        if not m:
            continue
        code, name = m
        conn.execute(
            """
            UPDATE report_files
            SET stock_code=?, stock_name=?, sector=''
            WHERE id=?
            """,
            (code, name, r["id"]),
        )
        updated += 1

    # 2차 보정: "기업_종목명_..." 패턴은 파일명 접두 종목명을 최우선 신뢰
    # (작성 증권사명이 말미에 있어 오매핑되는 문제 방지)
    code_by_name = {
        str(name).strip(): str(code).zfill(6)
        for code, name, _ in candidates
    }
    rows2 = conn.execute(
        """
        SELECT id, stock_code, stock_name, file_name
        FROM report_files
        WHERE file_name LIKE '기업_%'
        """
    ).fetchall()
    for r in rows2:
        fname = str(r["file_name"] or "")
        m = re.match(r"^기업_([^_]{2,30})_", fname)
        if not m:
            continue
        lead_name = m.group(1).strip()
        if lead_name not in code_by_name:
            continue
        lead_code = code_by_name[lead_name]
        if str(r["stock_code"] or "").strip() == lead_code and str(r["stock_name"] or "").strip() == lead_name:
            continue
        conn.execute(
            """
            UPDATE report_files
            SET stock_code=?, stock_name=?, sector=''
            WHERE id=?
            """,
            (lead_code, lead_name, r["id"]),
        )
        updated += 1

    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM report_files WHERE (stock_code IS NULL OR stock_code='') AND COALESCE(sector,'')<>''"
    ).fetchone()[0]
    conn.close()
    print({"updated_to_stock": updated, "remaining_sector_only": remaining})


if __name__ == "__main__":
    main()
