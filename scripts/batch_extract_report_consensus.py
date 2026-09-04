#!/usr/bin/env python3
"""Batch extract target price / forward metrics from local analyst PDFs.

This fills analyst_pdf_extracts, which is shown on the domestic stock detail
page through /api/reports/extracts/{stock_code}.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.gemini import is_configured
from services.gemini_openai_compat import OpenAI
DB_PATH = ROOT / "stock.db"
LOG_DIR = ROOT / "logs"
MODEL = "gpt-4o-mini"
PDF_CONSENSUS_EXTRACT_DISABLED = True


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        """
    )
    conn.commit()


def candidate_reports(
    conn: sqlite3.Connection,
    limit: int | None,
    since: str | None,
    latest_per_stock: int | None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where = [
        "COALESCE(r.stock_code, '') != ''",
        "e.report_id IS NULL",
        "(LOWER(COALESCE(r.mime_type, '')) LIKE '%pdf%' OR LOWER(COALESCE(r.file_path, '')) LIKE '%.pdf')",
    ]
    if since:
        where.append("r.report_date >= ?")
        params.append(since)

    rank_sql = ""
    outer_where = ""
    if latest_per_stock:
        rank_sql = (
            ", ROW_NUMBER() OVER (PARTITION BY r.stock_code "
            "ORDER BY r.report_date DESC, r.id DESC) AS stock_rank"
        )
        outer_where = "WHERE stock_rank <= ?"
        params.append(latest_per_stock)

    limit_sql = ""
    if limit:
        limit_sql = "LIMIT ?"
        params.append(limit)

    sql = f"""
        SELECT * FROM (
            SELECT
                r.id, r.stock_code, r.stock_name, r.report_date, r.file_name,
                r.saved_name, r.file_path, r.mime_type
                {rank_sql}
            FROM report_files r
            LEFT JOIN analyst_pdf_extracts e ON e.report_id = r.id
            WHERE {' AND '.join(where)}
        )
        {outer_where}
        ORDER BY report_date DESC, id DESC
        {limit_sql}
    """
    return conn.execute(sql, params).fetchall()


def extract_pdf_text(file_path: Path, max_pages: int) -> str:
    import pdfplumber

    texts: list[str] = []
    with pdfplumber.open(str(file_path)) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text() or ""
            if text.strip():
                texts.append(text)
    return "\n".join(texts)[:7000]


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("원", "").replace("배", "").strip()
    if text in {"-", "N/A", "n/a", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def call_openai(client: Any, text: str, stock_name: str) -> dict[str, Any]:
    prompt = (
        f"다음은 한국 주식 애널리스트 보고서 텍스트입니다. 종목명은 {stock_name or '알 수 없음'}입니다.\n"
        "보고서 본문에서 해당 종목의 목표주가, 투자의견, 12개월 선행 EPS, 12개월 선행 매출, "
        "12개월 선행 PER를 찾아 JSON으로만 반환하세요. 값이 없거나 종목과 무관하면 null을 넣으세요.\n"
        "단위 규칙: target_price는 원, fwd_eps_1y는 원, fwd_rev_1y는 억원, fwd_per는 배입니다.\n"
        "{\n"
        '  "target_price": null,\n'
        '  "opinion": null,\n'
        '  "fwd_eps_1y": null,\n'
        '  "fwd_rev_1y": null,\n'
        '  "fwd_per": null\n'
        "}\n\n"
        f"보고서 텍스트:\n{text}"
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=350,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return {
        "target_price": _number_or_none(payload.get("target_price")),
        "opinion": payload.get("opinion"),
        "fwd_eps_1y": _number_or_none(payload.get("fwd_eps_1y")),
        "fwd_rev_1y": _number_or_none(payload.get("fwd_rev_1y")),
        "fwd_per": _number_or_none(payload.get("fwd_per")),
    }


def save_extract(conn: sqlite3.Connection, row: sqlite3.Row, result: dict[str, Any], raw_text: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO analyst_pdf_extracts
          (report_id, stock_code, target_price, opinion,
           fwd_eps_1y, fwd_rev_1y, fwd_per, raw_text)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            row["id"],
            row["stock_code"] or "",
            result.get("target_price"),
            result.get("opinion"),
            result.get("fwd_eps_1y"),
            result.get("fwd_rev_1y"),
            result.get("fwd_per"),
            raw_text[:2000],
        ),
    )
    conn.commit()


def has_extracted_value(result: dict[str, Any]) -> bool:
    return any(
        result.get(key) not in (None, "")
        for key in ("target_price", "opinion", "fwd_eps_1y", "fwd_rev_1y", "fwd_per")
    )


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 건수")
    parser.add_argument("--since", default=None, help="report_date 하한, 예: 2026-06-01")
    parser.add_argument("--latest-per-stock", type=int, default=None, help="종목별 최신 N건만 처리")
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if PDF_CONSENSUS_EXTRACT_DISABLED:
        print("애널리스트 PDF AI 컨센서스 배치 추출은 비용 절감을 위해 비활성화되었습니다.", file=sys.stderr)
        return 0

    load_dotenv(ROOT / ".env")
    if not is_configured():
        print("GEMINI_API_KEY 또는 GOOGLE_API_KEY가 없어 중단합니다.", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    fail_log = LOG_DIR / f"batch_extract_report_consensus_failures_{run_id}.jsonl"

    conn = connect()
    ensure_table(conn)
    rows = candidate_reports(conn, args.limit, args.since, args.latest_per_stock)
    existing_rows = [r for r in rows if Path(r["file_path"] or "").exists()]
    missing_count = len(rows) - len(existing_rows)
    print(
        json.dumps(
            {
                "candidate_rows": len(rows),
                "existing_pdf_rows": len(existing_rows),
                "missing_file_rows": missing_count,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run:
        for row in existing_rows[:20]:
            print(dict(row))
        conn.close()
        return 0

    client = OpenAI()
    ok = 0
    failed = 0
    skipped_empty_text = 0
    skipped_no_metrics = 0
    for idx, row in enumerate(existing_rows, start=1):
        file_path = Path(row["file_path"])
        try:
            raw_text = extract_pdf_text(file_path, args.max_pages)
            if not raw_text.strip():
                skipped_empty_text += 1
                append_jsonl(fail_log, {"report_id": row["id"], "reason": "empty_text", "file_path": str(file_path)})
                continue
            result = call_openai(client, raw_text, row["stock_name"] or "")
            if not has_extracted_value(result):
                skipped_no_metrics += 1
                append_jsonl(
                    fail_log,
                    {
                        "report_id": row["id"],
                        "stock_code": row["stock_code"],
                        "stock_name": row["stock_name"],
                        "reason": "no_metrics",
                        "file_path": str(file_path),
                    },
                )
                continue
            save_extract(conn, row, result, raw_text)
            ok += 1
        except Exception as exc:
            failed += 1
            append_jsonl(
                fail_log,
                {
                    "report_id": row["id"],
                    "stock_code": row["stock_code"],
                    "stock_name": row["stock_name"],
                    "file_path": str(file_path),
                    "error": repr(exc),
                },
            )
        if idx % 10 == 0 or idx == len(existing_rows):
            print(
                json.dumps(
                    {
                        "processed": idx,
                        "saved": ok,
                        "failed": failed,
                        "empty_text": skipped_empty_text,
                        "no_metrics": skipped_no_metrics,
                        "remaining": len(existing_rows) - idx,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if args.sleep:
            time.sleep(args.sleep)

    conn.close()
    print(
        json.dumps(
            {
                "done": True,
                "saved": ok,
                "failed": failed,
                "empty_text": skipped_empty_text,
                "no_metrics": skipped_no_metrics,
                "failure_log": str(fail_log) if failed or skipped_empty_text or skipped_no_metrics else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
