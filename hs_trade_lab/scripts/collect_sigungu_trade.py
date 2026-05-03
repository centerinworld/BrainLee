from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
LOCAL_ENV = ROOT_DIR / ".env"
REFERENCE_ROOT = ROOT_DIR / "data" / "reference"
API_URL = "https://apis.data.go.kr/1220000/sigunguperimexacrs/getSigunguPerImexAcrs"


def load_local_env() -> None:
    if not LOCAL_ENV.exists():
        return
    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


def month_range(start_ym: str, end_ym: str) -> list[str]:
    start_year, start_month = int(start_ym[:4]), int(start_ym[4:6])
    end_year, end_month = int(end_ym[:4]), int(end_ym[4:6])
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def period_ym(value: str) -> str:
    text = str(value).strip()
    if "." in text:
        year, month = text.split(".", 1)
        return f"{year}-{month[:2]}"
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[:4]}-{text[4:6]}"
    return text


def to_float(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value).replace(",", "").replace(" ", "").strip()
    if text in {"", "-", "--"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def load_sidos() -> list[dict[str, str]]:
    return json.loads((REFERENCE_ROOT / "sidos.json").read_text(encoding="utf-8"))


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sigungu_trade_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_ym TEXT NOT NULL,
            sido_code TEXT NOT NULL,
            sido_name TEXT NOT NULL DEFAULT '',
            sigungu_name TEXT NOT NULL,
            export_value REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            trade_balance REAL NOT NULL DEFAULT 0,
            export_count REAL NOT NULL DEFAULT 0,
            import_count REAL NOT NULL DEFAULT 0,
            row_key TEXT NOT NULL UNIQUE,
            raw_json TEXT NOT NULL DEFAULT '{}',
            fetched_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sigungu_trade_period ON sigungu_trade_record (period_ym)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sigungu_trade_region ON sigungu_trade_record (sido_name, sigungu_name)"
    )


def parse_items(xml_text: str) -> tuple[str, str, list[dict[str, str]]]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext(".//resultCode", default="")
    result_msg = root.findtext(".//resultMsg", default="")
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        row = {child.tag: child.text or "" for child in item}
        items.append(row)
    return result_code, result_msg, items


def upsert_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, str]],
    sido_code: str,
    sido_name: str,
    fetched_at: str,
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for row in rows:
        period = period_ym(row.get("priodTitle", ""))
        sigungu_name = row.get("sidoSggNm", "").strip()
        if not period or not sigungu_name:
            continue
        row_key = "|".join(["sigunguperimexacrs", period, sido_code, sigungu_name])
        values = (
            period,
            sido_code,
            sido_name,
            sigungu_name,
            to_float(row.get("expUsdAmt")),
            to_float(row.get("impUsdAmt")),
            to_float(row.get("cmtrBlncAmt")),
            to_float(row.get("expCnt")),
            to_float(row.get("impCnt")),
            row_key,
            json.dumps(row, ensure_ascii=False),
            fetched_at,
        )
        existing = conn.execute(
            "SELECT id FROM sigungu_trade_record WHERE row_key = ?", (row_key,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO sigungu_trade_record (
                period_ym, sido_code, sido_name, sigungu_name,
                export_value, import_value, trade_balance, export_count, import_count,
                row_key, raw_json, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                export_value=excluded.export_value,
                import_value=excluded.import_value,
                trade_balance=excluded.trade_balance,
                export_count=excluded.export_count,
                import_count=excluded.import_count,
                raw_json=excluded.raw_json,
                fetched_at=excluded.fetched_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            values,
        )
        if existing:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Korea Customs sigungu import/export records.")
    parser.add_argument("--start-ym", required=True, help="YYYYMM")
    parser.add_argument("--end-ym", required=True, help="YYYYMM")
    parser.add_argument("--service-key", default="", help="Override data.go.kr service key")
    args = parser.parse_args()

    load_local_env()
    service_key = (
        args.service_key
        or os.getenv("SIGUNGU_PERIMEX_SERVICE_KEY", "")
        or os.getenv("CUSTOMS_ITEMTRADE_SERVICE_KEY", "")
    ).strip()
    if not service_key:
        raise SystemExit("service key is empty")

    sidos = load_sidos()
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary = {
        "inserted": 0,
        "updated": 0,
        "months": {},
        "warnings": [],
    }
    with httpx.Client(timeout=30.0) as client:
        for ym in month_range(args.start_ym, args.end_ym):
            month_rows = 0
            active_sidos = 0
            result_codes: set[tuple[str, str]] = set()
            for sido in sidos:
                response = client.get(
                    API_URL,
                    params={
                        "serviceKey": service_key,
                        "strtYymm": ym,
                        "endYymm": ym,
                        "sidoCd": sido["code"],
                    },
                )
                response.raise_for_status()
                result_code, result_msg, rows = parse_items(response.text)
                result_codes.add((result_code, result_msg))
                if rows:
                    active_sidos += 1
                ins, upd = upsert_rows(conn, rows, sido["code"], sido["name"], fetched_at)
                summary["inserted"] += ins
                summary["updated"] += upd
                month_rows += len(rows)
            summary["months"][ym] = {
                "rows": month_rows,
                "active_sidos": active_sidos,
                "result_codes": [list(item) for item in sorted(result_codes)],
            }
            conn.commit()
    latest = conn.execute("SELECT MAX(period_ym) FROM sigungu_trade_record").fetchone()[0]
    summary["latest_period_ym"] = latest
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
