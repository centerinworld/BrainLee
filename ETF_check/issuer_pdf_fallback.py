"""Store issuer PDF fallbacks without disguising their effective dates."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from full_pdf_collector import DB_PATH, RAW_ROOT, connect


PLUS_PDF_URL = "https://www.plusetf.co.kr/api/v1/product/pdf/list"
SUPPORTED = {"489010": {"issuer_id": "006368", "source": "PLUS_OFFICIAL"}}


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


def store(
    conn: sqlite3.Connection,
    base_date: str,
    ticker: str,
    source: str,
    source_url: str,
    payload: dict[str, Any],
    raw_root: Path = RAW_ROOT,
) -> dict[str, Any]:
    initialize(conn)
    effective, rows = parse_plus(payload)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
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
            payload,url=fetch_plus(base_date,adapter["issuer_id"])
            result["collected"].append(store(conn,base_date,ticker,adapter["source"],url,payload,raw_root))
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
