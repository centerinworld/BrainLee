from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from rebuild_telegram_flow_mappings import HS_ALIASES


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
EXPORT_DIR = ROOT_DIR / "market_radar_exports"


def load_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item) for item in data if str(item).strip()]
    return []


def join_items(items: list[str]) -> str:
    return " / ".join(dict.fromkeys(item.strip() for item in items if item.strip()))


BAD_PRODUCT_LABELS = {
    "",
    "기타",
    "수입",
    "수출",
    "수입 수입",
    "수출 수출",
    "수입데이터",
    "수출데이터",
}

BAD_COMPANY_LABELS = {
    "",
    "+",
    "관련종목",
    "관련주",
    "월별",
    "수출",
    "수입",
    "데이터",
    "월별 수출 데이터",
    "월별 수입 데이터",
    "월별수출데이터",
    "월별수입데이터",
}


def normalized_label(value: str) -> str:
    return " ".join((value or "").replace("수입데이터", "").replace("수출데이터", "").split()).strip()


def strip_flow_suffix(value: str) -> str:
    text = normalized_label(value)
    for suffix in (" 수입", " 수출"):
        if text.endswith(suffix):
            return text[: -len(suffix)].strip()
    return text


def product_labels(product_title: str, matched_products: list[str]) -> list[str]:
    """Keep the post's actual product first and drop stale labels leaked from other cards."""
    product = normalized_label(product_title)
    product_core = strip_flow_suffix(product)
    labels: list[str] = []
    if product_core and product_core not in BAD_PRODUCT_LABELS:
        labels.append(product_core)
    for item in matched_products:
        label = normalized_label(item)
        core = strip_flow_suffix(label)
        if core in BAD_PRODUCT_LABELS:
            continue
        if product_core:
            if core.startswith("("):
                continue
            if core != product_core and core not in product_core and product_core not in core:
                continue
        labels.append(label)
    return list(dict.fromkeys(labels))


def company_labels(companies: list[str]) -> list[str]:
    return [
        item
        for item in dict.fromkeys(normalized_label(company) for company in companies)
        if item and item not in BAD_COMPANY_LABELS
    ]


def count_hs_candidates(conn: sqlite3.Connection, products: list[str]) -> int:
    if not products:
        return 0
    count = 0
    for product in products:
        alias_codes = HS_ALIASES.get(strip_flow_suffix(product), [])
        count += len(alias_codes)
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM hs_sector_map
            WHERE display_name = ? OR hs_name = ? OR display_name LIKE ? OR hs_name LIKE ?
            """,
            (product, product, f"%{product}%", f"%{product}%"),
        ).fetchone()
        count += int(row[0] or 0)
    return count


def count_company_hs_candidates(conn: sqlite3.Connection, companies: list[str], products: list[str]) -> int:
    if not companies:
        return 0
    count = 0
    for company in companies:
        if products:
            for product in products:
                row = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM hs_code_company_map
                    WHERE stock_name = ?
                      AND (sector_name = ? OR hs_name = ? OR sector_name LIKE ? OR hs_name LIKE ?)
                    """,
                    (company, product, product, f"%{product}%", f"%{product}%"),
                ).fetchone()
                count += int(row[0] or 0)
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM hs_code_company_map WHERE stock_name = ?",
                (company,),
            ).fetchone()
            count += int(row[0] or 0)
    return count


def company_hs_candidates(conn: sqlite3.Connection, companies: list[str], products: list[str]) -> list[sqlite3.Row]:
    if not companies:
        return []
    candidates: list[sqlite3.Row] = []
    for company in companies:
        if products:
            for product in products:
                candidates.extend(
                    conn.execute(
                        """
                        SELECT hs_code, hs_name, stock_code, stock_name, sector_name, confidence
                        FROM hs_code_company_map
                        WHERE stock_name = ?
                          AND (sector_name = ? OR hs_name = ? OR sector_name LIKE ? OR hs_name LIKE ?)
                        ORDER BY confidence DESC, hs_code
                        """,
                        (company, product, product, f"%{product}%", f"%{product}%"),
                    ).fetchall()
                )
        else:
            candidates.extend(
                conn.execute(
                    """
                    SELECT hs_code, hs_name, stock_code, stock_name, sector_name, confidence
                    FROM hs_code_company_map
                    WHERE stock_name = ?
                    ORDER BY confidence DESC, hs_code
                    """,
                    (company,),
                ).fetchall()
            )
    seen: set[tuple[str, str]] = set()
    unique: list[sqlite3.Row] = []
    for candidate in candidates:
        key = (candidate["hs_code"], candidate["stock_code"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def flow_map_candidates(conn: sqlite3.Connection, message_id: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT hs_code, hs_name, stock_code, stock_name, product_title AS sector_name, confidence
        FROM telegram_company_hs_flow_map
        WHERE post_message_id = ?
        ORDER BY confidence DESC, hs_code, stock_code
        """,
        (message_id,),
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    unique: list[sqlite3.Row] = []
    for row in rows:
        key = (row["hs_code"], row["stock_code"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def latest_confirmed_for_hs_codes(conn: sqlite3.Connection, hs_codes: list[str]) -> tuple[str, float, float]:
    if not hs_codes:
        return "", 0.0, 0.0
    placeholders = ",".join(["?"] * len(hs_codes))
    latest = conn.execute(
        f"""
        SELECT MAX(period_ym)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade'
          AND hs_code IN ({placeholders})
          AND period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
        """,
        hs_codes,
    ).fetchone()[0]
    if not latest:
        return "", 0.0, 0.0
    row = conn.execute(
        f"""
        SELECT SUM(export_value), SUM(import_value)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade'
          AND period_ym=?
          AND hs_code IN ({placeholders})
        """,
        [latest, *hs_codes],
    ).fetchone()
    return latest, float(row[0] or 0), float(row[1] or 0)


def fetch_card(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM telegram_trade_card
        WHERE post_message_id = ?
        """,
        (message_id,),
    ).fetchone()


def audit(limit: int) -> tuple[list[str], list[list[object]]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT message_id, posted_at, post_url, title, product_title, mapping_status,
               companies_json, matched_hs_codes_json, matched_companies_json
        FROM telegram_post_cache
        ORDER BY posted_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    headers = [
        "display_status",
        "display_reason",
        "message_id",
        "posted_at",
        "product_title",
        "related_companies_text",
        "matched_products_text",
        "matched_companies_text",
        "mapping_status",
        "company_hs_candidate_count",
        "hs_candidate_count",
        "candidate_hs_codes",
        "candidate_hs_names",
        "candidate_mapping_confidence_max",
        "confirmed_latest_period_ym",
        "confirmed_export_value",
        "confirmed_import_value",
        "provisional_period_ym",
        "provisional_period_day",
        "provisional_category_name",
        "provisional_amount_thousand_usd",
        "trade_value_scope",
        "post_url",
        "display_note",
    ]
    output: list[list[object]] = []
    for row in rows:
        companies = load_json_list(row["companies_json"])
        product_title = normalized_label(row["product_title"] or row["title"])
        products = product_labels(product_title, load_json_list(row["matched_hs_codes_json"]))
        matched_companies = load_json_list(row["matched_companies_json"])
        display_companies = company_labels(matched_companies) or company_labels(companies)
        card = fetch_card(conn, row["message_id"])
        flow_rows = flow_map_candidates(conn, row["message_id"])
        company_hs_rows = flow_rows or company_hs_candidates(conn, display_companies, products)
        company_hs_count = len(company_hs_rows)
        hs_count = count_hs_candidates(conn, products)
        candidate_hs_codes = list(dict.fromkeys(str(item["hs_code"]) for item in company_hs_rows))
        candidate_hs_names = list(dict.fromkeys(str(item["hs_name"]) for item in company_hs_rows))
        confidence_max = max((float(item["confidence"] or 0) for item in company_hs_rows), default=0.0)

        if card:
            status = "ready_card"
            reason = "telegram_trade_card에 원문 제품명/관련종목/실적 연결값이 있음"
            confirmed_period = card["confirmed_latest_period_ym"]
            confirmed_export = card["confirmed_export_value"]
            confirmed_import = card["confirmed_import_value"]
            provisional_ym = card["provisional_period_ym"]
            provisional_day = card["provisional_period_day"]
            provisional_category = card["provisional_category_name"]
            provisional_amount = card["provisional_amount_thousand_usd"]
            trade_scope = card["trade_value_scope"]
            display_note = card["display_note"]
        elif display_companies and products and company_hs_count:
            status = "ready_mapping"
            reason = "기업명과 제품명이 있고 텔레그램 게시물 exact 매핑 및 확정 월별 HS 실적 연결 가능"
            confirmed_period, confirmed_export, confirmed_import = latest_confirmed_for_hs_codes(
                conn,
                candidate_hs_codes,
            )
            provisional_ym = ""
            provisional_day = ""
            provisional_category = ""
            provisional_amount = ""
            trade_scope = "company_hs_mapping"
            display_note = "원문 제품명/관련종목 표시는 가능, 실적값은 매핑 HS 기준 조회 필요"
        elif display_companies and products and hs_count:
            status = "partial_product_hs"
            reason = "제품명은 HS 후보와 연결되지만 기업-HS 직접 연결은 부족"
            confirmed_period = ""
            confirmed_export = ""
            confirmed_import = ""
            provisional_ym = ""
            provisional_day = ""
            provisional_category = ""
            provisional_amount = ""
            trade_scope = "product_hs_candidate"
            display_note = "표시는 가능하나 관련종목별 실적 배분/확정은 검토 필요"
        elif display_companies or products:
            status = "partial_metadata"
            reason = "기업명 또는 제품명 일부만 구조화됨"
            confirmed_period = ""
            confirmed_export = ""
            confirmed_import = ""
            provisional_ym = ""
            provisional_day = ""
            provisional_category = ""
            provisional_amount = ""
            trade_scope = ""
            display_note = "원문 카드는 표시 가능하나 수출입실적 연결 근거 부족"
        else:
            status = "needs_review"
            reason = "기업명/제품명 구조화 정보가 부족"
            confirmed_period = ""
            confirmed_export = ""
            confirmed_import = ""
            provisional_ym = ""
            provisional_day = ""
            provisional_category = ""
            provisional_amount = ""
            trade_scope = ""
            display_note = "뉴스/해설형 게시물일 수 있어 수동 분류 필요"

        output.append(
            [
                status,
                reason,
                row["message_id"],
                row["posted_at"],
                product_title or row["title"],
                join_items(display_companies),
                join_items(products),
                join_items(matched_companies),
                row["mapping_status"],
                company_hs_count,
                hs_count,
                join_items(candidate_hs_codes),
                join_items(candidate_hs_names),
                confidence_max,
                confirmed_period,
                confirmed_export,
                confirmed_import,
                provisional_ym,
                provisional_day,
                provisional_category,
                provisional_amount,
                trade_scope,
                row["post_url"],
                display_note,
            ]
        )
    conn.close()
    return headers, output


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit whether recent Telegram posts can be displayed with DB mappings.")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    headers, rows = audit(args.limit)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"telegram_display_audit_recent_{args.limit}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(headers)
        writer.writerows(rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row[0])] = counts.get(str(row[0]), 0) + 1
    print(json.dumps({"path": str(path), "rows": len(rows), "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
