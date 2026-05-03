from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
EXPORT_DIR = ROOT_DIR / "market_radar_exports"


HS_PREFIX_ALIASES = {
    "비메모리 반도체 (시스템 반도체)": ["854231"],
    "메모리 반도체": ["854232", "8523511000"],
    "NCM계 양극재": ["2841909020"],
}

PROVISIONAL_CATEGORY_ALIASES = {
    "비메모리 반도체 (시스템 반도체)": ("export_items_10day", "반도체"),
    "메모리 반도체": ("export_items_10day", "반도체"),
    "반도체": ("export_items_10day", "반도체"),
}


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_trade_card (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_message_id TEXT NOT NULL UNIQUE,
            posted_at TEXT NOT NULL DEFAULT '',
            post_url TEXT NOT NULL DEFAULT '',
            product_title TEXT NOT NULL DEFAULT '',
            related_companies_text TEXT NOT NULL DEFAULT '',
            related_companies_json TEXT NOT NULL DEFAULT '[]',
            matched_products_json TEXT NOT NULL DEFAULT '[]',
            hs_prefixes_json TEXT NOT NULL DEFAULT '[]',
            confirmed_latest_period_ym TEXT NOT NULL DEFAULT '',
            confirmed_export_value REAL NOT NULL DEFAULT 0,
            confirmed_import_value REAL NOT NULL DEFAULT 0,
            provisional_period_ym TEXT NOT NULL DEFAULT '',
            provisional_period_day TEXT NOT NULL DEFAULT '',
            provisional_category_name TEXT NOT NULL DEFAULT '',
            provisional_amount_thousand_usd REAL NOT NULL DEFAULT 0,
            trade_value_scope TEXT NOT NULL DEFAULT '',
            display_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def normalize_product_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def company_text(companies: list[str]) -> str:
    return " / ".join(companies)


def hs_prefixes_for_products(products: list[str]) -> list[str]:
    prefixes: list[str] = []
    for product in products:
        prefixes.extend(HS_PREFIX_ALIASES.get(product, []))
    return list(dict.fromkeys(prefixes))


def latest_confirmed(conn: sqlite3.Connection, prefixes: list[str]) -> tuple[str, float, float]:
    if not prefixes:
        return "", 0.0, 0.0
    clauses = " OR ".join(["hs_code LIKE ?"] * len(prefixes))
    params = [f"{prefix}%" for prefix in prefixes]
    latest = conn.execute(
        f"""
        SELECT MAX(period_ym)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade'
          AND period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
          AND ({clauses})
        """,
        params,
    ).fetchone()[0]
    if not latest:
        return "", 0.0, 0.0
    row = conn.execute(
        f"""
        SELECT SUM(export_value), SUM(import_value)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade'
          AND period_ym=?
          AND ({clauses})
        """,
        [latest, *params],
    ).fetchone()
    return latest, float(row[0] or 0), float(row[1] or 0)


def provisional_for_products(conn: sqlite3.Connection, products: list[str]) -> tuple[str, str, str, float]:
    for product in products:
        mapping = PROVISIONAL_CATEGORY_ALIASES.get(product)
        if not mapping:
            continue
        dataset_key, category_name = mapping
        row = conn.execute(
            """
            SELECT period_ym, period_day, category_name, amount_thousand_usd
            FROM customs_best_available_10day_record
            WHERE dataset_key=? AND category_name=?
            ORDER BY period_ym DESC, period_day_order DESC
            LIMIT 1
            """,
            (dataset_key, category_name),
        ).fetchone()
        if row:
            return row[0], row[1], row[2], float(row[3] or 0)
    return "", "", "", 0.0


def rebuild_cards(conn: sqlite3.Connection) -> int:
    create_table(conn)
    rows = conn.execute(
        """
        SELECT message_id, posted_at, post_url, title, companies_json, matched_hs_codes_json
        FROM telegram_post_cache
        WHERE companies_json <> '[]'
          AND matched_hs_codes_json <> '[]'
          AND (
            title LIKE '%MCO%' OR title LIKE '%복합부품%' OR title LIKE '%반도체%'
            OR title LIKE '%메모리%' OR title LIKE '%양극%'
          )
        ORDER BY posted_at DESC
        """
    ).fetchall()
    count = 0
    for row in rows:
        companies = json.loads(row[4] or "[]")
        products = json.loads(row[5] or "[]")
        prefixes = hs_prefixes_for_products(products)
        latest_period, export_value, import_value = latest_confirmed(conn, prefixes)
        p_ym, p_day, p_category, p_amount = provisional_for_products(conn, products)
        trade_scope = "confirmed_hs_prefix"
        display_note = "텔레그램 원문 기업/제품명을 보존하고, 확정 월별 HS prefix 수출입실적을 연결"
        if p_ym:
            trade_scope = "confirmed_hs_prefix + provisional_10day_proxy"
            display_note += f"; 10일 잠정치는 {p_category} 대분류 프록시"
        conn.execute(
            """
            INSERT INTO telegram_trade_card (
                post_message_id, posted_at, post_url, product_title,
                related_companies_text, related_companies_json, matched_products_json,
                hs_prefixes_json, confirmed_latest_period_ym, confirmed_export_value,
                confirmed_import_value, provisional_period_ym, provisional_period_day,
                provisional_category_name, provisional_amount_thousand_usd,
                trade_value_scope, display_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_message_id) DO UPDATE SET
                posted_at=excluded.posted_at,
                post_url=excluded.post_url,
                product_title=excluded.product_title,
                related_companies_text=excluded.related_companies_text,
                related_companies_json=excluded.related_companies_json,
                matched_products_json=excluded.matched_products_json,
                hs_prefixes_json=excluded.hs_prefixes_json,
                confirmed_latest_period_ym=excluded.confirmed_latest_period_ym,
                confirmed_export_value=excluded.confirmed_export_value,
                confirmed_import_value=excluded.confirmed_import_value,
                provisional_period_ym=excluded.provisional_period_ym,
                provisional_period_day=excluded.provisional_period_day,
                provisional_category_name=excluded.provisional_category_name,
                provisional_amount_thousand_usd=excluded.provisional_amount_thousand_usd,
                trade_value_scope=excluded.trade_value_scope,
                display_note=excluded.display_note,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                row[0],
                row[1],
                row[2],
                normalize_product_title(row[3]),
                company_text(companies),
                json.dumps(companies, ensure_ascii=False),
                json.dumps(products, ensure_ascii=False),
                json.dumps(prefixes, ensure_ascii=False),
                latest_period,
                export_value,
                import_value,
                p_ym,
                p_day,
                p_category,
                p_amount,
                trade_scope,
                display_note,
            ),
        )
        count += 1
    return count


def export_csv(conn: sqlite3.Connection) -> int:
    path = EXPORT_DIR / "telegram_trade_card.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT product_title, related_companies_text, matched_products_json,
               hs_prefixes_json, confirmed_latest_period_ym, confirmed_export_value,
               confirmed_import_value, provisional_period_ym, provisional_period_day,
               provisional_category_name, provisional_amount_thousand_usd,
               trade_value_scope, post_url, display_note
        FROM telegram_trade_card
        ORDER BY posted_at DESC
        """
    ).fetchall()
    headers = [d[0] for d in conn.execute(
        """
        SELECT product_title, related_companies_text, matched_products_json,
               hs_prefixes_json, confirmed_latest_period_ym, confirmed_export_value,
               confirmed_import_value, provisional_period_ym, provisional_period_day,
               provisional_category_name, provisional_amount_thousand_usd,
               trade_value_scope, post_url, display_note
        FROM telegram_trade_card
        LIMIT 0
        """
    ).description]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    count = rebuild_cards(conn)
    conn.commit()
    csv_count = export_csv(conn)
    sample = conn.execute(
        """
        SELECT product_title, related_companies_text, confirmed_latest_period_ym,
               confirmed_export_value, provisional_period_ym, provisional_period_day,
               provisional_category_name, provisional_amount_thousand_usd
        FROM telegram_trade_card
        WHERE product_title LIKE '%복합부품 집적회로(MCOs)%'
        ORDER BY posted_at DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    print(json.dumps({"cards": count, "csv_rows": csv_count, "mcos_sample": sample}, ensure_ascii=False))


if __name__ == "__main__":
    main()
