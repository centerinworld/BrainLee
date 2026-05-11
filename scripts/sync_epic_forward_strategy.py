#!/usr/bin/env python3
"""
Sync EPIC industry indicators into local Forward_Strategy tables.

Authentication:
  export EPIC_ACCESS_TOKEN="..."

The token is never printed. The script stores raw JSON responses so the schema can
be re-normalized later without re-downloading the source data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
BASE_URL = "https://epic.ai.kr/api"
SOURCE = "epic_industry"
CATALOG_SEED_SOURCE = "EPIC UI catalog seed; raw values require authenticated EPIC API"

CURRENT_UI_RELATED_COMPANIES = {
    # Observed on https://epic.ai.kr/industry/latest/0/1/1 after login.
    # Indicator: KG모빌리티 내수 판매: 모델별 (월), category=0, subCode=112.
    (0, 112): [
        ("204320", "HL만도"),
        ("009900", "명신산업"),
        ("043370", "피에이치에이"),
        ("405100", "THE CUBE&"),
        ("126600", "BGF에코머티리얼즈"),
        ("090080", "평화산업"),
        ("001380", "SG글로벌"),
        ("005850", "에스엘"),
        ("015750", "성우하이텍"),
        ("010690", "화신"),
        ("200880", "서연이화"),
        ("000430", "대원강업"),
        ("123410", "코리아에프티"),
        ("126640", "화신정공"),
        ("012330", "현대모비스"),
        ("018880", "한온시스템"),
        ("005380", "현대차"),
        ("000270", "기아"),
        ("011210", "현대위아"),
        ("003620", "KG모빌리티"),
        ("060980", "HL홀딩스"),
        ("151860", "KG에코솔루션"),
        ("000680", "LS네트웍스"),
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forward_strategy_sources (
            source_id       TEXT PRIMARY KEY,
            source_name     TEXT NOT NULL,
            base_url        TEXT,
            description     TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS forward_strategy_industry_categories (
            id              INTEGER PRIMARY KEY,
            source_id       TEXT NOT NULL,
            tab             TEXT NOT NULL,
            category_code   INTEGER NOT NULL,
            category_name   TEXT NOT NULL,
            group_id        TEXT DEFAULT '',
            group_name      TEXT DEFAULT '',
            description     TEXT,
            update_date     TEXT,
            raw_json        TEXT,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, tab, category_code, group_id)
        );

        CREATE TABLE IF NOT EXISTS forward_strategy_indicators (
            id                  INTEGER PRIMARY KEY,
            source_id           TEXT NOT NULL,
            tab                 TEXT NOT NULL,
            category_code       INTEGER NOT NULL,
            category_name       TEXT,
            group_id            TEXT DEFAULT '',
            group_name          TEXT DEFAULT '',
            sub_code            INTEGER NOT NULL,
            indicator_name      TEXT NOT NULL,
            indicator_type      TEXT,
            update_date         TEXT,
            data_code           INTEGER NOT NULL DEFAULT 1,
            data_name           TEXT DEFAULT 'Default',
            latest_date         TEXT,
            since_date          TEXT,
            frequency           TEXT,
            unit                TEXT,
            source              TEXT,
            note                TEXT,
            is_latest_tab       INTEGER DEFAULT 0,
            raw_meta_json       TEXT,
            updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, tab, category_code, group_id, sub_code, data_code)
        );

        CREATE TABLE IF NOT EXISTS forward_strategy_indicator_series (
            id              INTEGER PRIMARY KEY,
            source_id       TEXT NOT NULL,
            category_code   INTEGER NOT NULL,
            sub_code        INTEGER NOT NULL,
            data_code       INTEGER NOT NULL,
            series_type     TEXT NOT NULL,
            period          TEXT NOT NULL,
            value           REAL,
            raw_value       TEXT,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, category_code, sub_code, data_code, series_type, period)
        );

        CREATE TABLE IF NOT EXISTS forward_strategy_related_companies (
            id              INTEGER PRIMARY KEY,
            source_id       TEXT NOT NULL,
            category_code   INTEGER NOT NULL,
            sub_code        INTEGER NOT NULL,
            stock_code      TEXT NOT NULL,
            stock_name      TEXT NOT NULL,
            raw_json        TEXT,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, category_code, sub_code, stock_code)
        );

        CREATE TABLE IF NOT EXISTS forward_strategy_raw_responses (
            id              INTEGER PRIMARY KEY,
            source_id       TEXT NOT NULL,
            endpoint        TEXT NOT NULL,
            params_json     TEXT DEFAULT '{}',
            response_hash   TEXT NOT NULL,
            response_json   TEXT NOT NULL,
            fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, endpoint, params_json, response_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_forward_strategy_indicators_code
            ON forward_strategy_indicators(category_code, sub_code, data_code);
        CREATE INDEX IF NOT EXISTS idx_forward_strategy_indicators_name
            ON forward_strategy_indicators(indicator_name);
        CREATE INDEX IF NOT EXISTS idx_forward_strategy_series_lookup
            ON forward_strategy_indicator_series(category_code, sub_code, data_code, series_type, period);
        CREATE INDEX IF NOT EXISTS idx_forward_strategy_companies_stock
            ON forward_strategy_related_companies(stock_code);

        CREATE VIEW IF NOT EXISTS v_forward_strategy_latest_points AS
        SELECT i.source_id, i.category_code, i.category_name, i.group_name,
               i.sub_code, i.indicator_name, i.data_code, i.data_name,
               i.frequency, i.unit, i.source, i.note,
               s.series_type, s.period, s.value, s.raw_value, i.updated_at
        FROM forward_strategy_indicators i
        JOIN (
            SELECT source_id, category_code, sub_code, data_code, series_type, MAX(period) AS max_period
            FROM forward_strategy_indicator_series
            GROUP BY source_id, category_code, sub_code, data_code, series_type
        ) latest
          ON latest.source_id=i.source_id
         AND latest.category_code=i.category_code
         AND latest.sub_code=i.sub_code
         AND latest.data_code=i.data_code
        JOIN forward_strategy_indicator_series s
          ON s.source_id=latest.source_id
         AND s.category_code=latest.category_code
         AND s.sub_code=latest.sub_code
         AND s.data_code=latest.data_code
         AND s.series_type=latest.series_type
         AND s.period=latest.max_period;
        """
    )
    conn.execute(
        """
        INSERT INTO forward_strategy_sources
            (source_id, source_name, base_url, description, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_id) DO UPDATE SET
            source_name=excluded.source_name,
            base_url=excluded.base_url,
            description=excluded.description,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            SOURCE,
            "EPIC 산업지표",
            BASE_URL,
            "EPIC 산업지표 메뉴의 선행 산업 데이터와 관련기업 raw/normalized cache",
        ),
    )


class EpicClient:
    def __init__(self, token: str, delay: float = 0.15) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {token}",
                "Origin": "https://epic.ai.kr",
                "Referer": "https://epic.ai.kr/industry/latest/0/1/1",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            }
        )
        self.delay = delay

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        time.sleep(self.delay)
        url = f"{BASE_URL}{endpoint}"
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError("EPIC API returned 401. EPIC_ACCESS_TOKEN is missing or expired.")
        if resp.status_code == 403:
            raise RuntimeError("EPIC API returned 403. The logged-in account may not have industry access.")
        resp.raise_for_status()
        if not resp.text.strip():
            return None
        return resp.json()


def store_raw(conn: sqlite3.Connection, endpoint: str, params: dict[str, Any] | None, response: Any) -> None:
    payload = json_dumps(response)
    conn.execute(
        """
        INSERT OR IGNORE INTO forward_strategy_raw_responses
            (source_id, endpoint, params_json, response_hash, response_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (SOURCE, endpoint, json_dumps(params or {}), stable_hash(response), payload),
    )


def upsert_category(
    conn: sqlite3.Connection,
    tab: str,
    category: dict[str, Any],
    group: dict[str, Any] | None = None,
) -> None:
    group = group or {}
    code = int(category.get("code") if category.get("code") is not None else category.get("others", {}).get("code"))
    name = category.get("name") or category.get("title") or ""
    desc = category.get("description") or category.get("others", {}).get("description")
    group_id = str(group.get("groupId") or group.get("id") or "")
    group_name = group.get("groupName") or group.get("name") or ""
    conn.execute(
        """
        INSERT INTO forward_strategy_industry_categories
            (source_id, tab, category_code, category_name, group_id, group_name,
             description, update_date, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_id, tab, category_code, group_id) DO UPDATE SET
            category_name=excluded.category_name,
            group_name=excluded.group_name,
            description=excluded.description,
            update_date=excluded.update_date,
            raw_json=excluded.raw_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            SOURCE,
            tab,
            code,
            name,
            group_id,
            group_name,
            desc,
            category.get("updateDate") or category.get("others", {}).get("updateDate"),
            json_dumps({"category": category, "group": group}),
        ),
    )


def iter_subcategories(categories_payload: dict[str, Any], codes_payload: dict[str, Any]) -> list[dict[str, Any]]:
    code_names: dict[int, str] = {}
    for item in codes_payload.get("industryCategories", []) or []:
        if item.get("code") is not None:
            code_names[int(item["code"])] = item.get("name") or ""

    rows: list[dict[str, Any]] = []
    for cat in categories_payload.get("recentIndustryCategories", []) or []:
        for sub in cat.get("subCategories", []) or []:
            rows.append(
                {
                    "tab": "latest",
                    "category": cat,
                    "group": {},
                    "category_code": int(cat["code"]),
                    "category_name": cat.get("name") or code_names.get(int(cat["code"]), ""),
                    "sub": sub,
                    "is_latest_tab": 1,
                }
            )

    for cat in categories_payload.get("industryCategories", []) or []:
        cat_code = int(cat["code"])
        cat_name = cat.get("name") or code_names.get(cat_code, "")
        groups = cat.get("industryGroups") or []
        if groups:
            for group in groups:
                for sub in group.get("subCategories", []) or []:
                    rows.append(
                        {
                            "tab": "categories",
                            "category": {**cat, "name": cat_name},
                            "group": group,
                            "category_code": cat_code,
                            "category_name": cat_name,
                            "sub": sub,
                            "is_latest_tab": 0,
                        }
                    )
        else:
            for sub in cat.get("subCategories", []) or []:
                rows.append(
                    {
                        "tab": "categories",
                        "category": {**cat, "name": cat_name},
                        "group": {},
                        "category_code": cat_code,
                        "category_name": cat_name,
                        "sub": sub,
                        "is_latest_tab": 0,
                    }
                )
    return rows


def normalize_data_codes(sub: dict[str, Any], meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates = sub.get("industryDataCodes") or []
    if meta:
        candidates = meta.get("dataCodes") or meta.get("industryDataCodes") or candidates
    if not candidates:
        return [{"dataCode": 1, "dataName": "Default"}]
    return [
        {
            "dataCode": int(item.get("dataCode") if item.get("dataCode") is not None else item.get("code", 1)),
            "dataName": item.get("dataName") or item.get("name") or "Default",
            "latest": item.get("latest") or item.get("latestDate"),
            "since": item.get("since") or item.get("sinceDate"),
        }
        for item in candidates
    ]


def upsert_indicator(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    data_code: dict[str, Any],
    meta: dict[str, Any] | None,
) -> None:
    sub = row["sub"]
    group = row.get("group") or {}
    meta = meta or {}
    conn.execute(
        """
        INSERT INTO forward_strategy_indicators
            (source_id, tab, category_code, category_name, group_id, group_name,
             sub_code, indicator_name, indicator_type, update_date, data_code, data_name,
             latest_date, since_date, frequency, unit, source, note, is_latest_tab,
             raw_meta_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_id, tab, category_code, group_id, sub_code, data_code) DO UPDATE SET
            category_name=excluded.category_name,
            group_name=excluded.group_name,
            indicator_name=excluded.indicator_name,
            indicator_type=excluded.indicator_type,
            update_date=excluded.update_date,
            data_name=excluded.data_name,
            latest_date=excluded.latest_date,
            since_date=excluded.since_date,
            frequency=excluded.frequency,
            unit=excluded.unit,
            source=excluded.source,
            note=excluded.note,
            raw_meta_json=excluded.raw_meta_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            SOURCE,
            row["tab"],
            row["category_code"],
            row["category_name"],
            str(group.get("groupId") or group.get("id") or ""),
            group.get("groupName") or group.get("name") or "",
            int(sub["subCode"]),
            sub.get("subName") or "",
            sub.get("industryDataType"),
            sub.get("updateDate"),
            int(data_code["dataCode"]),
            data_code.get("dataName") or "Default",
            data_code.get("latest") or meta.get("latestDate"),
            data_code.get("since") or meta.get("sinceDate"),
            meta.get("frequency"),
            meta.get("unit"),
            meta.get("source"),
            meta.get("note"),
            int(row.get("is_latest_tab") or 0),
            json_dumps(meta),
        ),
    )


def upsert_series(
    conn: sqlite3.Connection,
    category_code: int,
    sub_code: int,
    data_code: int,
    chart_payload: dict[str, Any] | None,
) -> int:
    if not chart_payload:
        return 0
    count = 0
    series_list = chart_payload.get("chartData") or chart_payload.get("data") or []
    for series in series_list:
        series_type = series.get("type") or series.get("name") or "default"
        for point in series.get("data") or []:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            period = str(point[0])
            raw_value = point[1]
            value = float(raw_value) if isinstance(raw_value, (int, float)) else None
            conn.execute(
                """
                INSERT INTO forward_strategy_indicator_series
                    (source_id, category_code, sub_code, data_code, series_type, period,
                     value, raw_value, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_id, category_code, sub_code, data_code, series_type, period)
                DO UPDATE SET
                    value=excluded.value,
                    raw_value=excluded.raw_value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (SOURCE, category_code, sub_code, data_code, series_type, period, value, str(raw_value)),
            )
            count += 1
    return count


def upsert_companies(
    conn: sqlite3.Connection,
    category_code: int,
    sub_code: int,
    payload: dict[str, Any] | None,
) -> int:
    if not payload:
        return 0
    companies = payload.get("relatedCompanies") or payload.get("companies") or []
    count = 0
    for item in companies:
        code = item.get("companyCode") or item.get("stockCode")
        name = item.get("companyName") or item.get("stockName")
        if not code or not name:
            continue
        conn.execute(
            """
            INSERT INTO forward_strategy_related_companies
                (source_id, category_code, sub_code, stock_code, stock_name, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(source_id, category_code, sub_code, stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                raw_json=excluded.raw_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (SOURCE, category_code, sub_code, str(code), str(name), json_dumps(item)),
        )
        count += 1
    return count


def lookup_stock_code(conn: sqlite3.Connection, stock_name: str) -> str:
    row = conn.execute(
        """
        SELECT stock_code FROM stock_meta WHERE stock_name = ?
        UNION
        SELECT stock_code FROM stock_universe WHERE stock_name = ?
        UNION
        SELECT stock_code FROM listed_company_info WHERE stock_name = ?
        LIMIT 1
        """,
        (stock_name, stock_name, stock_name),
    ).fetchone()
    if row and row[0]:
        return str(row[0]).zfill(6)
    digest = hashlib.sha1(stock_name.encode("utf-8")).hexdigest()[:12]
    return f"UI:{digest}"


def clean_ui_company_name(value: str) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    blocked = {
        "더보기",
        "해당 데이터의 관련 기업이 없습니다.",
        "관련 기업",
        "오늘의 증시",
        "Top 종목 순위",
    }
    return "" if name in blocked else name


def import_ui_crawl(args: argparse.Namespace) -> dict[str, int]:
    crawl_path = Path(args.import_ui_crawl)
    if not crawl_path.exists():
        raise SystemExit(f"UI crawl file does not exist: {crawl_path}")

    payload = json.loads(crawl_path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    indicator_count = 0
    company_count = 0
    raw_count = 0
    for item in items:
        if not item.get("ok"):
            continue
        category_code = int(item["category_code"])
        sub_code = int(item["sub_code"])
        data_code = int(item.get("data_code") or 1)
        source_note = "EPIC authenticated browser UI crawl; token-free screen/DOM capture."
        raw_meta = {
            "source": source_note,
            "capturedAt": item.get("captured_at") or payload.get("captured_at"),
            "url": item.get("url"),
            "bodyHash": item.get("body_hash"),
            "uiOnly": True,
        }

        conn.execute(
            """
            UPDATE forward_strategy_indicators
               SET latest_date = COALESCE(NULLIF(?, ''), latest_date),
                   since_date = COALESCE(NULLIF(?, ''), since_date),
                   frequency = COALESCE(NULLIF(?, ''), frequency),
                   unit = COALESCE(NULLIF(?, ''), unit),
                   source = ?,
                   note = ?,
                   raw_meta_json = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE source_id = ?
               AND category_code = ?
               AND sub_code = ?
               AND data_code = ?
            """,
            (
                item.get("latest_date") or "",
                item.get("since_date") or "",
                item.get("frequency") or "",
                item.get("unit") or "",
                source_note,
                "EPIC API token was not used. Time-series raw values remain unavailable unless the UI exposes them.",
                json_dumps(raw_meta),
                SOURCE,
                category_code,
                sub_code,
                data_code,
            ),
        )
        indicator_count += conn.total_changes

        endpoint = f"ui:/industry/latest/{category_code}/{sub_code}/{data_code}"
        response = {
            "url": item.get("url"),
            "capturedAt": item.get("captured_at") or payload.get("captured_at"),
            "bodyHash": item.get("body_hash"),
            "frequency": item.get("frequency"),
            "unit": item.get("unit"),
            "since": item.get("since_date"),
            "latest": item.get("latest_date"),
            "relatedCompanies": item.get("related_companies") or [],
            "bodyText": item.get("body_text"),
        }
        store_raw(conn, endpoint, {"ui_crawl": crawl_path.name}, response)
        raw_count += 1

        for raw_name in item.get("related_companies") or []:
            name = clean_ui_company_name(raw_name)
            if not name:
                continue
            code = lookup_stock_code(conn, name)
            conn.execute(
                """
                INSERT INTO forward_strategy_related_companies
                    (source_id, category_code, sub_code, stock_code, stock_name, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_id, category_code, sub_code, stock_code) DO UPDATE SET
                    stock_name=excluded.stock_name,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    SOURCE,
                    category_code,
                    sub_code,
                    code,
                    name,
                    json_dumps(
                        {
                            "companyName": name,
                            "companyCode": code,
                            "source": "EPIC visible UI related companies",
                            "url": item.get("url"),
                        }
                    ),
                ),
            )
            company_count += 1

        if not args.dry_run and raw_count % args.commit_every == 0:
            conn.commit()

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    return {
        "categories": 0,
        "indicators": len(items),
        "series_points": 0,
        "companies": company_count,
        "raw_pages": raw_count,
    }


def load_catalog_seed(seed_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    categories_path = seed_dir / "t3e.json"
    recent_path = seed_dir / "r3e.json"
    if not categories_path.exists() or not recent_path.exists():
        raise SystemExit(
            f"Catalog seed files are missing under {seed_dir}. "
            "Expected t3e.json and r3e.json from the EPIC app bundle."
        )
    categories = json.loads(categories_path.read_text(encoding="utf-8"))
    recent = json.loads(recent_path.read_text(encoding="utf-8"))
    codes_payload = {
        "industryCategories": [
            {
                "code": item.get("others", {}).get("code"),
                "name": item.get("title"),
                "description": item.get("others", {}).get("description"),
                "updateDate": item.get("others", {}).get("updateDate"),
            }
            for item in categories
            if item.get("others", {}).get("code") is not None
        ]
    }
    return recent, codes_payload


def seed_catalog(args: argparse.Namespace) -> dict[str, int]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    seed_dir = Path(args.seed_catalog)
    categories_payload, codes_payload = load_catalog_seed(seed_dir)
    store_raw(conn, "bundle:/industry/categories", {"seed_dir": str(seed_dir)}, categories_payload)
    store_raw(conn, "bundle:/industry/codes", {"seed_dir": str(seed_dir)}, codes_payload)

    code_names = {
        int(item["code"]): item.get("name") or ""
        for item in codes_payload.get("industryCategories", [])
        if item.get("code") is not None
    }
    for item in codes_payload.get("industryCategories", []):
        if item.get("code") is None:
            continue
        upsert_category(
            conn,
            "catalog",
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "description": item.get("description"),
                "updateDate": item.get("updateDate"),
            },
            None,
        )

    rows = iter_subcategories(categories_payload, codes_payload)
    indicator_count = 0
    company_count = 0
    category_seen: set[tuple[str, int, str]] = set()
    for row in rows:
        row["category_name"] = row["category_name"] or code_names.get(row["category_code"], "")
        category_key = (row["tab"], row["category_code"], "")
        if category_key not in category_seen:
            upsert_category(conn, row["tab"], row["category"], None)
            category_seen.add(category_key)

        for data_code in normalize_data_codes(row["sub"], None):
            upsert_indicator(
                conn,
                row,
                data_code,
                {
                    "source": CATALOG_SEED_SOURCE,
                    "note": "Catalog-only seed. Use EPIC authenticated endpoints for source tooltip/reference-date and time-series values.",
                    "sourceEndpoints": {
                        "categories": "/api/industry/categories",
                        "codes": "/api/industry/codes",
                        "chart": f"/api/industry/codes/{row['category_code']}/sub-codes/{row['sub']['subCode']}/chart",
                        "companies": f"/api/industry/codes/{row['category_code']}/sub-codes/{row['sub']['subCode']}/companies",
                        "referenceDate": f"/api/industry/codes/{row['category_code']}/sub-codes/{row['sub']['subCode']}/reference-date",
                    },
                },
            )
            indicator_count += 1

        key = (row["category_code"], int(row["sub"]["subCode"]))
        companies = CURRENT_UI_RELATED_COMPANIES.get(key)
        if companies:
            company_count += upsert_companies(
                conn,
                row["category_code"],
                int(row["sub"]["subCode"]),
                {
                    "relatedCompanies": [
                        {
                            "companyCode": code,
                            "companyName": name,
                            "source": "EPIC visible UI related companies",
                        }
                        for code, name in companies
                    ]
                },
            )

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    return {
        "categories": len(category_seen) + len(code_names),
        "indicators": indicator_count,
        "series_points": 0,
        "companies": company_count,
    }


def sync(args: argparse.Namespace) -> dict[str, int]:
    token = os.environ.get("EPIC_ACCESS_TOKEN", "").strip()
    if not token and not args.init_only:
        raise SystemExit(
            "EPIC_ACCESS_TOKEN is required for network sync. "
            "Run with --init-only to create tables without downloading."
        )

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    if args.init_only:
        conn.commit()
        return {"categories": 0, "indicators": 0, "series_points": 0, "companies": 0}

    client = EpicClient(token=token, delay=args.delay)
    categories = client.get("/industry/categories")
    store_raw(conn, "/industry/categories", None, categories)
    codes = client.get("/industry/codes")
    store_raw(conn, "/industry/codes", None, codes)

    rows = iter_subcategories(categories or {}, codes or {})
    if args.max_indicators:
        rows = rows[: args.max_indicators]

    category_seen: set[tuple[str, int, str]] = set()
    indicator_count = 0
    series_count = 0
    company_count = 0
    for row in rows:
        group_id = str((row.get("group") or {}).get("groupId") or (row.get("group") or {}).get("id") or "")
        category_key = (row["tab"], row["category_code"], group_id)
        if category_key not in category_seen:
            upsert_category(conn, row["tab"], row["category"], row.get("group"))
            category_seen.add(category_key)

        code = row["category_code"]
        sub_code = int(row["sub"]["subCode"])
        meta = None
        try:
            meta = client.get(f"/industry/codes/{code}/sub-codes/{sub_code}/reference-date", {"dataCode": 1})
            store_raw(conn, f"/industry/codes/{code}/sub-codes/{sub_code}/reference-date", {"dataCode": 1}, meta)
        except Exception as exc:
            if args.strict:
                raise
            print(f"[WARN] reference-date failed code={code} sub={sub_code}: {exc}", file=sys.stderr)

        for data_code in normalize_data_codes(row["sub"], meta):
            dc = int(data_code["dataCode"])
            upsert_indicator(conn, row, data_code, meta)
            indicator_count += 1
            try:
                chart = client.get(f"/industry/codes/{code}/sub-codes/{sub_code}/chart", {"dataCode": dc})
                store_raw(conn, f"/industry/codes/{code}/sub-codes/{sub_code}/chart", {"dataCode": dc}, chart)
                series_count += upsert_series(conn, code, sub_code, dc, chart)
            except Exception as exc:
                if args.strict:
                    raise
                print(f"[WARN] chart failed code={code} sub={sub_code} data={dc}: {exc}", file=sys.stderr)

        try:
            companies = client.get(f"/industry/codes/{code}/sub-codes/{sub_code}/companies")
            store_raw(conn, f"/industry/codes/{code}/sub-codes/{sub_code}/companies", None, companies)
            company_count += upsert_companies(conn, code, sub_code, companies)
        except Exception as exc:
            if args.strict:
                raise
            print(f"[WARN] companies failed code={code} sub={sub_code}: {exc}", file=sys.stderr)

        if not args.dry_run and indicator_count % args.commit_every == 0:
            conn.commit()

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()

    return {
        "categories": len(category_seen),
        "indicators": indicator_count,
        "series_points": series_count,
        "companies": company_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-only", action="store_true", help="Create Forward_Strategy tables only.")
    parser.add_argument("--seed-catalog", default="", help="Load catalog-only seed from a directory containing t3e.json and r3e.json.")
    parser.add_argument("--import-ui-crawl", default="", help="Import token-free EPIC browser UI crawl JSON into Forward_Strategy tables.")
    parser.add_argument("--dry-run", action="store_true", help="Download and normalize, then rollback DB writes.")
    parser.add_argument("--strict", action="store_true", help="Stop on the first per-indicator API failure.")
    parser.add_argument("--max-indicators", type=int, default=0, help="Limit subcategories for smoke tests.")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between EPIC API calls.")
    parser.add_argument("--commit-every", type=int, default=25)
    args = parser.parse_args()
    if args.import_ui_crawl:
        result = import_ui_crawl(args)
    elif args.seed_catalog:
        result = seed_catalog(args)
    else:
        result = sync(args)
    print(json.dumps({"source": SOURCE, "fetched_at": now_iso(), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
