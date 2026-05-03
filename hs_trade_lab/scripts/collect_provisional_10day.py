from __future__ import annotations

import argparse
import csv
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
EXPORT_DIR = ROOT_DIR / "market_radar_exports"


ENDPOINTS = {
    "import_items_10day": {
        "title": "관세청_수입 주요품목별 10일 단위 잠정치 통계",
        "flow_type": "import",
        "dimension_type": "item",
        "url": "https://apis.data.go.kr/1220000/prlstMmUtPrviImpAcrs/getPrlstMmUtPrviImpAcrs",
        "confirmed_endpoint": "itemtrade",
        "categories": {
            "00": "전체",
            "01": "반도체",
            "02": "원유",
            "03": "기계류",
            "04": "가스",
            "05": "반도체 제조용 장비",
            "06": "정밀기기",
            "07": "석유제품",
            "08": "무선통신기기",
            "09": "승용차",
            "10": "석탄",
        },
    },
    "export_countries_10day": {
        "title": "관세청_수출 주요국가별 10일 단위 잠정치 통계",
        "flow_type": "export",
        "dimension_type": "country",
        "url": "https://apis.data.go.kr/1220000/cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs",
        "confirmed_endpoint": "nationtrade",
        "categories": {
            "00": "전체",
            "01": "중국",
            "02": "미국",
            "03": "유럽연합",
            "04": "베트남",
            "05": "홍콩",
            "06": "일본",
            "07": "대만",
            "08": "인도",
            "09": "싱가포르",
            "10": "말레이시아",
        },
    },
    "export_items_10day": {
        "title": "관세청_수출 주요품목별 10일 단위 잠정치 통계",
        "flow_type": "export",
        "dimension_type": "item",
        "url": "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs",
        "confirmed_endpoint": "itemtrade",
        "categories": {
            "00": "전체",
            "01": "반도체",
            "02": "철강제품",
            "03": "승용차",
            "04": "석유제품",
            "05": "무선통신기기",
            "06": "선박",
            "07": "자동차부품",
            "08": "컴퓨터주변기기",
            "09": "정밀기기",
            "10": "가전제품",
        },
    },
}


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
    year, month = int(start_ym[:4]), int(start_ym[4:6])
    end_year, end_month = int(end_ym[:4]), int(end_ym[4:6])
    months: list[str] = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


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


def period_ym(value: str) -> str:
    text = str(value).strip()
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[:4]}-{text[4:6]}"
    return text


def period_day_order(value: str) -> int:
    text = str(value).strip()
    if text.endswith("10"):
        return 10
    if text.endswith("20"):
        return 20
    return 31


def parse_items(xml_text: str) -> tuple[str, str, list[dict[str, str]]]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext(".//resultCode", default="")
    result_msg = root.findtext(".//resultMsg", default="")
    items = [{child.tag: child.text or "" for child in item} for item in root.findall(".//item")]
    return result_code, result_msg, items


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customs_provisional_10day_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_key TEXT NOT NULL,
            dataset_title TEXT NOT NULL,
            flow_type TEXT NOT NULL,
            dimension_type TEXT NOT NULL,
            category_code TEXT NOT NULL,
            category_name TEXT NOT NULL,
            period_year TEXT NOT NULL DEFAULT '',
            period_month TEXT NOT NULL DEFAULT '',
            period_ym TEXT NOT NULL,
            period_day TEXT NOT NULL,
            period_day_order INTEGER NOT NULL DEFAULT 0,
            amount_thousand_usd REAL NOT NULL DEFAULT 0,
            data_status TEXT NOT NULL DEFAULT 'provisional',
            confirmed_endpoint TEXT NOT NULL DEFAULT '',
            row_key TEXT NOT NULL UNIQUE,
            raw_json TEXT NOT NULL DEFAULT '{}',
            fetched_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_provisional_10day_period ON customs_provisional_10day_record (dataset_key, period_ym, period_day_order)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_provisional_10day_category ON customs_provisional_10day_record (flow_type, dimension_type, category_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_customs_monthly_endpoint_period ON customs_monthly_record (endpoint, period_ym)"
    )
    conn.execute("DROP VIEW IF EXISTS customs_best_available_10day_record")
    conn.execute(
        """
        CREATE VIEW customs_best_available_10day_record AS
        SELECT p.*,
               COALESCE((
                   SELECT MAX(c.period_ym)
                   FROM customs_monthly_record c
                   WHERE c.endpoint = p.confirmed_endpoint
                     AND c.period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
               ), '') AS latest_confirmed_period_ym,
               CASE
                 WHEN p.period_ym > COALESCE((
                   SELECT MAX(c.period_ym)
                   FROM customs_monthly_record c
                   WHERE c.endpoint = p.confirmed_endpoint
                     AND c.period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                 ), '') THEN 'used_as_best_available'
                 ELSE 'shadowed_by_confirmed_monthly'
               END AS best_available_status
        FROM customs_provisional_10day_record p
        WHERE p.period_ym > COALESCE((
            SELECT MAX(c.period_ym)
            FROM customs_monthly_record c
            WHERE c.endpoint = p.confirmed_endpoint
              AND c.period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
        ), '')
        """
    )


def upsert_endpoint(
    conn: sqlite3.Connection,
    client: httpx.Client,
    service_key: str,
    dataset_key: str,
    ym: str,
    fetched_at: str,
) -> dict[str, object]:
    spec = ENDPOINTS[dataset_key]
    response = client.get(
        spec["url"],
        params={"serviceKey": service_key, "strtYymm": ym, "endYymm": ym},
    )
    if response.status_code in {401, 403}:
        return {"status": "auth_pending", "http_status": response.status_code, "rows": 0}
    response.raise_for_status()
    result_code, result_msg, items = parse_items(response.text)
    inserted = 0
    updated = 0
    for item in items:
        p_ym = period_ym(item.get("priodMon", ym))
        p_day = item.get("priodDt", "").strip()
        for code, category_name in spec["categories"].items():
            amount = to_float(item.get(f"itemUsdAmt{code}"))
            row_key = "|".join([dataset_key, p_ym, p_day, code])
            existing = conn.execute(
                "SELECT id FROM customs_provisional_10day_record WHERE row_key = ?",
                (row_key,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO customs_provisional_10day_record (
                    dataset_key, dataset_title, flow_type, dimension_type,
                    category_code, category_name, period_year, period_month,
                    period_ym, period_day, period_day_order, amount_thousand_usd,
                    data_status, confirmed_endpoint, row_key, raw_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'provisional', ?, ?, ?, ?)
                ON CONFLICT(row_key) DO UPDATE SET
                    amount_thousand_usd=excluded.amount_thousand_usd,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    dataset_key,
                    spec["title"],
                    spec["flow_type"],
                    spec["dimension_type"],
                    code,
                    category_name,
                    item.get("priodYear", ""),
                    item.get("priodMon", ""),
                    p_ym,
                    p_day,
                    period_day_order(p_day),
                    amount,
                    spec["confirmed_endpoint"],
                    row_key,
                    json.dumps(item, ensure_ascii=False),
                    fetched_at,
                ),
            )
            if existing:
                updated += 1
            else:
                inserted += 1
    return {
        "status": "ok",
        "result_code": result_code,
        "result_msg": result_msg,
        "source_items": len(items),
        "inserted": inserted,
        "updated": updated,
    }


def export_query(conn: sqlite3.Connection, query: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(query).fetchall()
    headers = [desc[0] for desc in conn.execute(query).description]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


def export_csvs(conn: sqlite3.Connection, export_dir: Path = EXPORT_DIR) -> dict[str, int]:
    exports = {
        "customs_provisional_10day_record.csv": """
            SELECT dataset_key, dataset_title, flow_type, dimension_type, category_code,
                   category_name, period_ym, period_day, amount_thousand_usd,
                   data_status, confirmed_endpoint, fetched_at
            FROM customs_provisional_10day_record
            ORDER BY dataset_key, period_ym, period_day_order, category_code
        """,
        "customs_best_available_10day_record.csv": """
            SELECT dataset_key, dataset_title, flow_type, dimension_type, category_code,
                   category_name, period_ym, period_day, amount_thousand_usd,
                   data_status, latest_confirmed_period_ym, best_available_status, fetched_at
            FROM customs_best_available_10day_record
            ORDER BY dataset_key, period_ym, period_day_order, category_code
        """,
        "sigungu_trade_record.csv": """
            SELECT period_ym, sido_code, sido_name, sigungu_name, export_value,
                   import_value, trade_balance, export_count, import_count, fetched_at
            FROM sigungu_trade_record
            ORDER BY period_ym, sido_code, sigungu_name
        """,
        "regional_company_mapping_evidence.csv": """
            SELECT stock_code, stock_name, hs_code, hs_name, sido_name, sigungu_name,
                   period_start, period_end, export_value, import_value, post_count,
                   evidence_score, evidence_status, evidence_note
            FROM regional_company_mapping_evidence
            ORDER BY evidence_score DESC, export_value DESC, stock_name
        """,
        "company_hs_mappings.csv": """
            SELECT stock_code, stock_name, hs_code, hs_name, sector_name,
                   match_type, mapping_status, confidence, note, updated_at
            FROM hs_code_company_map
            ORDER BY stock_name, hs_code
        """,
    }
    return {name: export_query(conn, query, export_dir / name) for name, query in exports.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect customs 10-day provisional trade APIs.")
    parser.add_argument("--start-ym", required=True, help="YYYYMM")
    parser.add_argument("--end-ym", required=True, help="YYYYMM")
    parser.add_argument("--service-key", default="")
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()

    load_local_env()
    service_key = (
        args.service_key
        or os.getenv("PROVISIONAL_10DAY_SERVICE_KEY", "")
        or os.getenv("CUSTOMS_ITEMTRADE_SERVICE_KEY", "")
    ).strip()
    if not service_key:
        raise SystemExit("service key is empty")

    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary: dict[str, object] = {"months": {}, "warnings": []}
    with httpx.Client(timeout=30.0) as client:
        for ym in month_range(args.start_ym, args.end_ym):
            month_summary = {}
            for dataset_key in ENDPOINTS:
                result = upsert_endpoint(conn, client, service_key, dataset_key, ym, fetched_at)
                month_summary[dataset_key] = result
                if result.get("status") == "auth_pending":
                    summary["warnings"].append(
                        f"{dataset_key} {ym}: approval may still be pending ({result.get('http_status')})"
                    )
            summary["months"][ym] = month_summary
            conn.commit()
    latest = conn.execute(
        """
        SELECT dataset_key, MAX(period_ym || ' ' || printf('%02d', period_day_order))
        FROM customs_provisional_10day_record
        GROUP BY dataset_key
        ORDER BY dataset_key
        """
    ).fetchall()
    summary["latest"] = {row[0]: row[1] for row in latest}
    if args.export_csv:
        summary["csv_counts"] = export_csvs(conn)
        summary["csv_dir"] = str(EXPORT_DIR)
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
