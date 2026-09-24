"""Store issuer PDF fallbacks without disguising their effective dates."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from full_pdf_collector import DB_PATH, RAW_ROOT, connect


PLUS_PDF_URL = "https://www.plusetf.co.kr/api/v1/product/pdf/list"
TIGER_PDF_URL = (
    "https://investments.miraeasset.com/tigeretf/ko/product/search/detail/"
    "pdfListAjax.ajax"
)
SUPPORTED = {
    "489010": {"kind": "plus", "issuer_id": "006368", "source": "PLUS_OFFICIAL"},
    "435420": {
        "kind": "tiger",
        "isin": "KR7435420005",
        "source": "TIGER_OFFICIAL",
    },
}

MATURITY_NAME = re.compile(r"(?<!\d)(\d{2})[-.](\d{2})(?!\d)")
MATURITY_HISTORY_DAYS = 5


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_issuer_fallback (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL,
            status TEXT NOT NULL,
            component_count INTEGER NOT NULL,
            raw_path TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY(base_date, etf_ticker)
        );
        CREATE TABLE IF NOT EXISTS etf_pdf_issuer_component (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            component_order INTEGER NOT NULL,
            effective_date TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            shares_per_cu REAL,
            weight REAL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY(base_date,etf_ticker,component_order),
            FOREIGN KEY(base_date,etf_ticker)
                REFERENCES etf_pdf_issuer_fallback(base_date,etf_ticker)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_issuer_component_stock
            ON etf_pdf_issuer_component(base_date,component_code);
        """
    )


def _raw_digest_matches(raw_path: str | None, expected: str | None) -> bool:
    if not raw_path or not expected:
        return False
    path = Path(raw_path)
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rb") as stream:
            return hashlib.sha256(stream.read()).hexdigest() == expected
    except (OSError, EOFError):
        return False


def validated_maturity_wind_down_exceptions(
    conn: sqlite3.Connection, base_date: str
) -> list[dict[str, Any]]:
    """Return empty target-maturity bond ETFs with independently proven wind-downs.

    An empty KRX response is accepted only when the ETF name targets the current
    year/month, five prior snapshots show a monotonic run-down to at most one
    non-domestic component, and KIS independently reports zero expected PDF rows.
    """
    target_date = datetime.strptime(base_date, "%Y%m%d").date()
    candidates = conn.execute(
        """
        SELECT s.etf_ticker,s.etf_name,s.raw_path,s.raw_sha256,
               d.expected_component_count,d.listed_shares,d.scale_factor
        FROM etf_pdf_full_snapshot s
        JOIN etf_scale_daily d
          ON d.base_date=s.base_date AND d.etf_ticker=s.etf_ticker
        WHERE s.base_date=? AND s.status='empty' AND s.component_count=0
          AND s.domestic_stock_count=0
          AND d.expected_component_count=0
          AND d.listed_shares>0 AND d.scale_factor>0
        ORDER BY s.etf_ticker
        """,
        (base_date,),
    ).fetchall()
    result = []
    for row in candidates:
        name = str(row["etf_name"] or "")
        maturity = MATURITY_NAME.search(name)
        if not maturity or "채" not in name:
            continue
        maturity_year = 2000 + int(maturity.group(1))
        maturity_month = int(maturity.group(2))
        if (maturity_year, maturity_month) != (target_date.year, target_date.month):
            continue
        history = conn.execute(
            """
            SELECT component_count,domestic_stock_count
            FROM etf_pdf_full_snapshot
            WHERE etf_ticker=? AND base_date<? AND status='success'
            ORDER BY base_date DESC LIMIT ?
            """,
            (row["etf_ticker"], base_date, MATURITY_HISTORY_DAYS),
        ).fetchall()
        if len(history) != MATURITY_HISTORY_DAYS:
            continue
        counts = [int(item["component_count"]) for item in history]
        if counts[0] > 1 or any(int(item["domestic_stock_count"]) for item in history):
            continue
        # Rows are newest first; component counts must not rise toward maturity.
        if any(newer > older for newer, older in zip(counts, counts[1:])):
            continue
        if not _raw_digest_matches(row["raw_path"], row["raw_sha256"]):
            continue
        result.append(
            {
                "etf_ticker": row["etf_ticker"],
                "source": "KRX_MATURITY_WINDDOWN",
                "effective_date": base_date,
                "component_count": 0,
                "domestic_components": [],
                "evidence": {
                    "prior_component_counts_newest_first": counts,
                    "kis_expected_component_count": int(row["expected_component_count"]),
                },
            }
        )
    return result


def validated_domestic_exceptions(conn: sqlite3.Connection, base_date: str) -> list[dict[str, Any]]:
    """Return independently validated snapshots with no Korean stock holdings."""
    initialize(conn)
    candidates = conn.execute(
        """
        SELECT f.etf_ticker,f.effective_date,f.source,f.component_count,
               f.raw_path,f.raw_sha256
        FROM etf_pdf_issuer_fallback f
        JOIN etf_pdf_full_snapshot s
          ON s.base_date=f.base_date AND s.etf_ticker=f.etf_ticker
        WHERE f.base_date=? AND f.effective_date=? AND f.status='current'
          AND f.component_count>0 AND s.status!='success'
          AND NOT EXISTS (
              SELECT 1 FROM etf_pdf_issuer_component c
              WHERE c.base_date=f.base_date AND c.etf_ticker=f.etf_ticker
                AND c.component_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          )
          AND f.component_count=(
              SELECT COUNT(*) FROM etf_pdf_issuer_component c
              WHERE c.base_date=f.base_date AND c.etf_ticker=f.etf_ticker
          )
        ORDER BY f.etf_ticker
        """,
        (base_date, base_date),
    ).fetchall()
    result = []
    for row in candidates:
        if not _raw_digest_matches(row["raw_path"], row["raw_sha256"]):
            continue
        result.append(
            {
                "etf_ticker": row["etf_ticker"],
                "source": row["source"],
                "effective_date": row["effective_date"],
                "component_count": int(row["component_count"]),
                "domestic_components": [],
            }
        )
    known = {item["etf_ticker"] for item in result}
    result.extend(
        item
        for item in validated_maturity_wind_down_exceptions(conn, base_date)
        if item["etf_ticker"] not in known
    )
    return sorted(result, key=lambda item: item["etf_ticker"])


def parse_plus(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    content = payload.get("content") or []
    if not content:
        raise RuntimeError("PLUS official PDF response is empty")
    expected = int(payload.get("totalElements") or len(content))
    if len(content) != expected:
        raise RuntimeError(f"PLUS PDF pagination incomplete: {len(content)}/{expected}")
    effective_dates = {str(row.get("wkdate") or "") for row in content}
    if len(effective_dates) != 1 or not next(iter(effective_dates)):
        raise RuntimeError(f"PLUS PDF effective date mismatch: {sorted(effective_dates)}")
    effective = next(iter(effective_dates))
    rows = []
    for order, row in enumerate(content, 1):
        rows.append(
            {
                "order": order,
                "code": str(row.get("jmCd") or row.get("krJmCd") or "").strip(),
                "name": str(row.get("jmNm") or "").strip(),
                "shares": float(row.get("amount")) if row.get("amount") is not None else None,
                "weight": float(row.get("ratio")) if row.get("ratio") is not None else None,
                "raw": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    return effective, rows


def fetch_plus(base_date: str, issuer_id: str) -> tuple[dict[str, Any], str]:
    params = {"n": issuer_id, "page": 0, "d": base_date, "pageSize": 1000}
    response = requests.get(PLUS_PDF_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json(), response.url


def parse_tiger(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    source_rows = soup.select("tr[data-tot-cnt]")
    if not source_rows:
        raise RuntimeError("TIGER official PDF response is empty")
    totals = {int(row.get("data-tot-cnt", "0")) for row in source_rows}
    if len(totals) != 1 or len(source_rows) != next(iter(totals)):
        raise RuntimeError(
            f"TIGER PDF pagination incomplete: {len(source_rows)}/{sorted(totals)}"
        )
    rows = []
    for order, source_row in enumerate(source_rows, 1):
        cells = [cell.get_text(" ", strip=True) for cell in source_row.select("td")]
        if len(cells) < 5:
            raise RuntimeError(f"TIGER PDF row has only {len(cells)} cells")
        rows.append(
            {
                "order": order,
                "code": cells[0],
                "name": cells[1],
                "shares": float(cells[2].replace(",", "")) if cells[2] not in {"", "-"} else None,
                "valuation": float(cells[3].replace(",", "")) if cells[3] not in {"", "-"} else None,
                "weight": float(cells[4].replace(",", "")) if cells[4] not in {"", "-"} else None,
                "raw": str(source_row),
            }
        )
    return rows


def fetch_tiger(base_date: str, isin: str) -> tuple[str, list[dict[str, Any]], str]:
    params = {
        "ksdFund": isin,
        "fixDate": base_date,
        "prfPrd": "Week01",
        "order": "SRD",
        "pageIndex": 1,
        "firstIndex": 0,
        "listCnt": 1000,
    }
    response = requests.get(TIGER_PDF_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.text, parse_tiger(response.text), response.url


def store_rows(
    conn: sqlite3.Connection,
    base_date: str,
    ticker: str,
    effective: str,
    source: str,
    source_url: str,
    rows: list[dict[str, Any]],
    raw: bytes,
    raw_root: Path = RAW_ROOT,
) -> dict[str, Any]:
    initialize(conn)
    digest = hashlib.sha256(raw).hexdigest()
    directory = raw_root / base_date
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticker}.issuer.json.gz"
    temporary = path.with_suffix(".json.gz.tmp")
    with gzip.open(temporary, "wb") as stream:
        stream.write(raw)
    temporary.replace(path)
    status = "current" if effective == base_date else "stale"
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "DELETE FROM etf_pdf_issuer_component WHERE base_date=? AND etf_ticker=?",
            (base_date, ticker),
        )
        conn.execute(
            """
            INSERT INTO etf_pdf_issuer_fallback(
                base_date,etf_ticker,effective_date,source,source_url,status,
                component_count,raw_path,raw_sha256,collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date,etf_ticker) DO UPDATE SET
                effective_date=excluded.effective_date,source=excluded.source,
                source_url=excluded.source_url,status=excluded.status,
                component_count=excluded.component_count,raw_path=excluded.raw_path,
                raw_sha256=excluded.raw_sha256,collected_at=excluded.collected_at
            """,
            (base_date,ticker,effective,source,source_url,status,len(rows),str(path),digest,now),
        )
        conn.executemany(
            """
            INSERT INTO etf_pdf_issuer_component(
                base_date,etf_ticker,component_order,effective_date,component_code,
                component_name,shares_per_cu,weight,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            [
                (base_date,ticker,row["order"],effective,row["code"],row["name"],
                 row["shares"],row["weight"],row["raw"])
                for row in rows
            ],
        )
    return {
        "base_date":base_date,"etf_ticker":ticker,"effective_date":effective,
        "status":status,"component_count":len(rows),"raw_path":str(path),
    }


def store(
    conn: sqlite3.Connection,
    base_date: str,
    ticker: str,
    source: str,
    source_url: str,
    payload: dict[str, Any],
    raw_root: Path = RAW_ROOT,
) -> dict[str, Any]:
    effective, rows = parse_plus(payload)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return store_rows(
        conn,base_date,ticker,effective,source,source_url,rows,raw,raw_root
    )


def collect_missing(base_date: str, db_path: Path = DB_PATH, raw_root: Path = RAW_ROOT) -> dict[str, Any]:
    conn=connect(db_path); initialize(conn)
    missing=[row[0] for row in conn.execute(
        """
        SELECT etf_ticker FROM etf_pdf_full_snapshot
        WHERE base_date=? AND status IN ('empty','error') ORDER BY etf_ticker
        """,(base_date,)
    )]
    result={"base_date":base_date,"missing":len(missing),"collected":[],"unsupported":[],"errors":[]}
    for ticker in missing:
        adapter=SUPPORTED.get(ticker)
        if not adapter:
            result["unsupported"].append(ticker); continue
        try:
            if adapter["kind"] == "plus":
                payload,url=fetch_plus(base_date,adapter["issuer_id"])
                stored=store(conn,base_date,ticker,adapter["source"],url,payload,raw_root)
            else:
                html,rows,url=fetch_tiger(base_date,adapter["isin"])
                stored=store_rows(
                    conn,base_date,ticker,base_date,adapter["source"],url,
                    rows,html.encode("utf-8"),raw_root,
                )
            result["collected"].append(stored)
        except Exception as exc:
            result["errors"].append({"ticker":ticker,"error":str(exc)})
    conn.close(); return result


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--date",required=True)
    parser.add_argument("--db",default=str(DB_PATH)); parser.add_argument("--raw-root",default=str(RAW_ROOT))
    args=parser.parse_args()
    print(json.dumps(collect_missing(args.date,Path(args.db),Path(args.raw_root)),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
