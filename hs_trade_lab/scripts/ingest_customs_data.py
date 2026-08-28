from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
DOWNLOAD_ROOT = ROOT_DIR / "data" / "customs_downloads"
REFERENCE_ROOT = ROOT_DIR / "data" / "reference"


def _to_float(value: str | int | float | None) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace(" ", "").strip()
    if text in ("", "-", "--"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _period_ym(value: str) -> str:
    text = str(value).strip()
    if "." in text:
        year, month = text.split(".", 1)
        return f"{year}-{month[:2]}"
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[:4]}-{text[4:6]}"
    return text


def _load_lookup(filename: str, key: str, value: str) -> dict[str, str]:
    path = REFERENCE_ROOT / filename
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row[key]): str(row[value]) for row in rows}


def _load_hs_lookup() -> dict[str, dict]:
    path = REFERENCE_ROOT / "hs_codes.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["HS부호"]).zfill(10): row for row in rows}


def normalize_record(endpoint: str, payload: dict, hs_lookup: dict[str, dict], country_lookup: dict[str, str], sido_lookup: dict[str, str]) -> dict:
    row = payload["row"]
    params = payload["params"]
    period_ym = _period_ym(row.get("year") or row.get("priodTitle") or params.get("strtYymm", ""))
    hs_code = str(row.get("hsCode") or row.get("hsSgn") or "").zfill(10) if (row.get("hsCode") or row.get("hsSgn")) else ""
    hs_ref = hs_lookup.get(hs_code, {})
    hs_name = hs_ref.get("한글품목명") or row.get("statKor") or row.get("korePrlstNm") or ""
    country_code = row.get("statCd") or params.get("cntyCd") or ""
    country_name = row.get("statCdCntnKor1") or country_lookup.get(country_code, "")
    sido_code = params.get("sidoCd", "")
    sido_name = row.get("sidoNm") or sido_lookup.get(sido_code, "")
    imex_type_code = params.get("imexTpcd", "")
    nature_code = row.get("godsCd") or row.get("tmprTpcd") or params.get("imexTmprClsfCd") or ""
    nature_name = row.get("godsKor") or row.get("cdValtValNm") or ""
    unified_nature_code = params.get("imexTmprUnfcClsfCd", "")

    export_value = 0.0
    import_value = 0.0
    trade_balance = 0.0
    export_weight = 0.0
    import_weight = 0.0
    weight_total = 0.0
    export_count = 0.0
    import_count = 0.0
    line_count = 0.0

    if endpoint == "itemtrade":
        export_value = _to_float(row.get("expDlr"))
        import_value = _to_float(row.get("impDlr"))
        trade_balance = _to_float(row.get("balPayments"))
        export_weight = _to_float(row.get("expWgt"))
        import_weight = _to_float(row.get("impWgt"))
    elif endpoint == "nationtrade":
        export_value = _to_float(row.get("expDlr"))
        import_value = _to_float(row.get("impDlr"))
        trade_balance = _to_float(row.get("balPayments"))
        export_count = _to_float(row.get("expCnt"))
        import_count = _to_float(row.get("impCnt"))
    elif endpoint == "sidotrade":
        export_value = _to_float(row.get("expUsdAmt"))
        import_value = _to_float(row.get("impUsdAmt"))
        trade_balance = _to_float(row.get("cmtrBlncAmt"))
        export_count = _to_float(row.get("expCnt"))
        import_count = _to_float(row.get("impCnt"))
    elif endpoint == "sidoitemtrade":
        export_value = _to_float(row.get("expUsdAmt"))
        import_value = _to_float(row.get("impUsdAmt"))
        trade_balance = _to_float(row.get("cmtrBlncAmt"))
        export_count = _to_float(row.get("expLnCnt"))
        import_count = _to_float(row.get("impLnCnt"))
    elif endpoint == "sidotempertrade":
        amount = _to_float(row.get("imexUsdAmt"))
        line_count = _to_float(row.get("imexLnCnt"))
        if imex_type_code == "1":
            export_value = amount
        else:
            import_value = amount
        trade_balance = export_value - import_value
    elif endpoint == "idfytempertrade":
        amount = _to_float(row.get("dlr"))
        weight_total = _to_float(row.get("wgt"))
        if row.get("impexp") == "수출" or imex_type_code == "1":
            export_value = amount
            export_weight = weight_total
        else:
            import_value = amount
            import_weight = weight_total
        trade_balance = export_value - import_value

    row_key = "|".join(
        [
            endpoint,
            period_ym,
            hs_code,
            country_code,
            sido_code,
            imex_type_code,
            nature_code,
            unified_nature_code,
        ]
    )
    return {
        "endpoint": endpoint,
        "period_ym": period_ym,
        "hs_code": hs_code,
        "hs_name": hs_name,
        "country_code": country_code,
        "country_name": country_name,
        "sido_code": sido_code,
        "sido_name": sido_name,
        "imex_type_code": imex_type_code,
        "nature_code": str(nature_code),
        "nature_name": nature_name,
        "unified_nature_code": str(unified_nature_code),
        "unified_nature_name": "",
        "export_value": export_value,
        "import_value": import_value,
        "trade_balance": trade_balance,
        "export_weight": export_weight,
        "import_weight": import_weight,
        "weight_total": weight_total,
        "export_count": export_count,
        "import_count": import_count,
        "line_count": line_count,
        "row_key": row_key,
        "source_file": payload.get("source_file", ""),
        "raw_json": json.dumps(payload["row"], ensure_ascii=False),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest customs raw downloads into hs_trade_lab.db")
    parser.add_argument("--endpoints", nargs="*", default=["itemtrade", "sidoitemtrade", "sidotempertrade", "nationtrade", "idfytempertrade", "sidotrade"])
    args = parser.parse_args()

    hs_lookup = _load_hs_lookup()
    country_lookup = _load_lookup("countries.json", "code", "name")
    sido_lookup = _load_lookup("sidos.json", "code", "name")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customs_monthly_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT,
            period_ym TEXT,
            hs_code TEXT DEFAULT '',
            hs_name TEXT DEFAULT '',
            country_code TEXT DEFAULT '',
            country_name TEXT DEFAULT '',
            sido_code TEXT DEFAULT '',
            sido_name TEXT DEFAULT '',
            imex_type_code TEXT DEFAULT '',
            nature_code TEXT DEFAULT '',
            nature_name TEXT DEFAULT '',
            unified_nature_code TEXT DEFAULT '',
            unified_nature_name TEXT DEFAULT '',
            export_value REAL DEFAULT 0,
            import_value REAL DEFAULT 0,
            trade_balance REAL DEFAULT 0,
            export_weight REAL DEFAULT 0,
            import_weight REAL DEFAULT 0,
            weight_total REAL DEFAULT 0,
            export_count REAL DEFAULT 0,
            import_count REAL DEFAULT 0,
            line_count REAL DEFAULT 0,
            row_key TEXT UNIQUE,
            source_file TEXT DEFAULT '',
            raw_json TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    inserted = 0
    updated = 0

    for endpoint in args.endpoints:
        for path in sorted((DOWNLOAD_ROOT / endpoint).glob("*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as fp:
                for line in fp:
                    payload = json.loads(line)
                    payload["source_file"] = str(path)
                    row = normalize_record(endpoint, payload, hs_lookup, country_lookup, sido_lookup)
                    cur = conn.execute("SELECT id FROM customs_monthly_record WHERE row_key = ?", (row["row_key"],))
                    existing = cur.fetchone()
                    if existing:
                        conn.execute(
                            """
                            UPDATE customs_monthly_record
                            SET export_value=?, import_value=?, trade_balance=?, export_weight=?, import_weight=?,
                                weight_total=?, export_count=?, import_count=?, line_count=?, source_file=?, raw_json=?,
                                hs_name=?, country_name=?, sido_name=?, nature_name=?, updated_at=CURRENT_TIMESTAMP
                            WHERE row_key=?
                            """,
                            (
                                row["export_value"], row["import_value"], row["trade_balance"],
                                row["export_weight"], row["import_weight"], row["weight_total"],
                                row["export_count"], row["import_count"], row["line_count"],
                                row["source_file"], row["raw_json"], row["hs_name"], row["country_name"],
                                row["sido_name"], row["nature_name"], row["row_key"],
                            ),
                        )
                        updated += 1
                    else:
                        conn.execute(
                            """
                            INSERT INTO customs_monthly_record (
                                endpoint, period_ym, hs_code, hs_name, country_code, country_name,
                                sido_code, sido_name, imex_type_code, nature_code, nature_name,
                                unified_nature_code, unified_nature_name, export_value, import_value,
                                trade_balance, export_weight, import_weight, weight_total,
                                export_count, import_count, line_count, row_key, source_file, raw_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["endpoint"], row["period_ym"], row["hs_code"], row["hs_name"],
                                row["country_code"], row["country_name"], row["sido_code"], row["sido_name"],
                                row["imex_type_code"], row["nature_code"], row["nature_name"],
                                row["unified_nature_code"], row["unified_nature_name"], row["export_value"],
                                row["import_value"], row["trade_balance"], row["export_weight"],
                                row["import_weight"], row["weight_total"], row["export_count"],
                                row["import_count"], row["line_count"], row["row_key"], row["source_file"], row["raw_json"],
                            ),
                        )
                        inserted += 1
            conn.commit()

    summary = {"inserted": inserted, "updated": updated, "endpoints": args.endpoints}
    print(json.dumps(summary, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
