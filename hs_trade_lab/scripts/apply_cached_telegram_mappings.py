from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
ROOT_STOCK_DB = ROOT_DIR.parent / "stock.db"

COMPANY_ALIASES = {
    "Sk하이닉스": "SK하이닉스",
    "삼성디플레이": "삼성디스플레이",
    "녹십자 등": "GC녹십자",
    "녹십자": "GC녹십자",
    "바우와우코리아": "오에스피",
    "삼화콘덴서": "삼화콘덴서공업",
    "마이크로컨텍솔": "마이크로컨텍솔루션",
    "달바글로벌": "달바글로벌",
    "지앤비에스 에코": "지앤비에스 에코",
    "SEMES": "삼성전자",
    "샌디스크": "SK하이닉스",
    "월별 수출 데이터": "",
    "양극활물질": "",
    "임플란트": "",
    "소주": "",
    "비상장": "",
    "자회사": "",
}

UNLISTED_COMPANIES = {
    "GC녹십자": "006280",
    "삼성디스플레이": "UNLISTED_SAMSUNG_DISPLAY",
    "대한조선": "UNLISTED_DAEHAN_SHIPBUILDING",
    "HD현대삼호중공업": "UNLISTED_HD_HYUNDAI_SAMHO",
    "케이조선": "UNLISTED_K_SHIPBUILDING",
    "BAT": "UNLISTED_BAT_KOREA",
    "SPC그룹": "UNLISTED_SPC_GROUP",
    "성진종합전기": "UNLISTED_SUNGJIN_ELECTRIC",
    "KP일렉트릭": "UNLISTED_KP_ELECTRIC",
    "이녹스리튬": "UNLISTED_INNOX_LITHIUM",
    "한국유미코아": "UNLISTED_UMICORE_KOREA",
    "에코프로이노베이션": "UNLISTED_ECOPRO_INNOVATION",
    "LS전선": "UNLISTED_LS_CABLE",
    "다이요유덴": "UNLISTED_TAIYO_YUDEN_KOREA",
    "제네톡스": "UNLISTED_GENETOX",
}

HS_ALIASES = {
    "MLCC": [{"hs_code": "8532240000", "hs_name": "세라믹 유전체의 것(다층)"}],
    "OLED TV": [{"hs_code": "8528725000", "hs_name": "유기발광다이오드(오엘이디) 방식"}],
    "평판디스플레이 텔레비전용": [{"hs_code": "8528725000", "hs_name": "유기발광다이오드(오엘이디) 방식"}],
    "스크러버": [{"hs_code": "8421219020", "hs_name": "반도체 제조용 여과기나 청정기"}],
    "과산화수소 (반도체 제조용)": [{"hs_code": "2847000000", "hs_name": "과산화수소"}],
    "인터페이스보드 프로브 카드": [{"hs_code": "8534009000", "hs_name": "그 밖의 인쇄회로"}],
    "러버 소켓": [{"hs_code": "8536909090", "hs_name": "그 밖의 전기회로 접속용 기기"}],
}

BAD_COMPANY_RE = re.compile(r"^\d{4}\.|주주총회|월별|데이터$")
GENERIC_SHIP_ENGINE_LABEL_RE = re.compile(r"선박추진용|디젤|세미디젤|압축점화식")


def hs_label_allowed_for_post(label: str, title: str) -> bool:
    if GENERIC_SHIP_ENGINE_LABEL_RE.search(label):
        return any(token in title for token in ["선박", "엔진", "HD현대", "STX", "조선"])
    return True


def normalize_company(name: str) -> str:
    cleaned = name.strip().strip("[]() ")
    cleaned = re.sub(r"\s*등$", "", cleaned)
    cleaned = COMPANY_ALIASES.get(cleaned, cleaned)
    return cleaned


def load_stock_lookup() -> dict[str, dict[str, str]]:
    conn = sqlite3.connect(ROOT_STOCK_DB)
    lookup: dict[str, dict[str, str]] = {}
    for table in ("stock_universe", "stock_meta"):
        try:
            rows = conn.execute(
                f"SELECT stock_code, stock_name, COALESCE(market, '') FROM {table} WHERE stock_name <> ''"
            ).fetchall()
        except sqlite3.Error:
            continue
        for code, name, market in rows:
            lookup.setdefault(name, {"stock_code": code, "stock_name": name, "market": market or ""})
    conn.close()
    for name, code in UNLISTED_COMPANIES.items():
        lookup.setdefault(name, {"stock_code": code, "stock_name": name, "market": "UNLISTED"})
    return lookup


def add_hs(lookup: dict[str, list[dict[str, str]]], label: str, hs_code: str, hs_name: str) -> None:
    label = label.strip()
    if not label:
        return
    entry = {"hs_code": hs_code, "hs_name": hs_name}
    values = lookup.setdefault(label, [])
    if entry not in values:
        values.append(entry)


def load_hs_lookup(conn: sqlite3.Connection) -> dict[str, list[dict[str, str]]]:
    lookup: dict[str, list[dict[str, str]]] = {}
    for hs_code, hs_name, display_name in conn.execute(
        "SELECT hs_code, hs_name, display_name FROM hs_sector_map"
    ):
        add_hs(lookup, display_name or "", hs_code, hs_name)
        add_hs(lookup, hs_name or "", hs_code, hs_name)
    for hs_code, hs_name, sector_name in conn.execute(
        "SELECT hs_code, hs_name, sector_name FROM hs_code_company_map"
    ):
        add_hs(lookup, sector_name or "", hs_code, hs_name)
        add_hs(lookup, hs_name or "", hs_code, hs_name)
    for label, rows in HS_ALIASES.items():
        for row in rows:
            add_hs(lookup, label, row["hs_code"], row["hs_name"])
    return lookup


def infer_confidence(company_count: int, matched_hs_count: int, market: str) -> float:
    score = 0.66
    if 0 < company_count <= 2:
        score += 0.08
    if matched_hs_count <= 2:
        score += 0.03
    if market == "UNLISTED":
        score -= 0.03
    return round(min(max(score, 0.58), 0.82), 2)


def upsert_mapping(
    conn: sqlite3.Connection,
    *,
    stock: dict[str, str],
    hs: dict[str, str],
    confidence: float,
    post: sqlite3.Row,
) -> str:
    existing = conn.execute(
        "SELECT id, confidence FROM hs_code_company_map WHERE hs_code=? AND stock_code=?",
        (hs["hs_code"], stock["stock_code"]),
    ).fetchone()
    note = (
        f"텔레그램 @BeOn_BeClear 기존 1년 캐시 기반 자동 보강: {post['title']} "
        f"({post['post_url']}). 품목명/기업명/지역명 매칭 후 provisional 후보로 반영"
    )
    if existing:
        if float(existing["confidence"] or 0) >= confidence:
            return "kept"
        conn.execute(
            """
            UPDATE hs_code_company_map
            SET confidence=?, note=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (confidence, note, existing["id"]),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO hs_code_company_map (
            hs_code, hs_name, stock_code, stock_name, sector_name,
            match_type, mapping_status, confidence, note
        )
        VALUES (?, ?, ?, ?, ?, 'company', 'provisional', ?, ?)
        """,
        (
            hs["hs_code"],
            hs["hs_name"],
            stock["stock_code"],
            stock["stock_name"],
            hs["hs_name"],
            confidence,
            note,
        ),
    )
    return "inserted"


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply mappings from cached Telegram posts first.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stock_lookup = load_stock_lookup()
    hs_lookup = load_hs_lookup(conn)
    query = """
        SELECT *
        FROM telegram_post_cache
        WHERE mapping_status = 'unmapped'
          AND matched_hs_codes_json <> '[]'
        ORDER BY posted_at DESC
    """
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    rows = conn.execute(query).fetchall()
    summary = {
        "posts_scanned": len(rows),
        "posts_mapped": 0,
        "inserted": 0,
        "updated": 0,
        "kept": 0,
        "missing_company": {},
        "missing_hs": {},
    }
    for post in rows:
        raw_companies = json.loads(post["companies_json"] or "[]")
        raw_hs_labels = json.loads(post["matched_hs_codes_json"] or "[]")
        stocks: list[dict[str, str]] = []
        for raw_name in raw_companies:
            name = normalize_company(raw_name)
            if not name or BAD_COMPANY_RE.search(name):
                continue
            stock = stock_lookup.get(name)
            if stock:
                stocks.append(stock)
            else:
                summary["missing_company"][name] = summary["missing_company"].get(name, 0) + 1
        hs_entries: list[dict[str, str]] = []
        for label in raw_hs_labels:
            if not hs_label_allowed_for_post(str(label), post["title"]):
                continue
            matches = hs_lookup.get(str(label).strip(), [])
            if matches:
                hs_entries.extend(matches)
            else:
                summary["missing_hs"][label] = summary["missing_hs"].get(label, 0) + 1
        stocks = list({item["stock_code"]: item for item in stocks}.values())
        hs_entries = list({item["hs_code"]: item for item in hs_entries}.values())
        if not stocks or not hs_entries:
            continue
        confidence = infer_confidence(len(raw_companies), len(raw_hs_labels), stocks[0].get("market", ""))
        post_results = {"inserted": 0, "updated": 0, "kept": 0}
        for stock in stocks:
            for hs in hs_entries:
                result = upsert_mapping(conn, stock=stock, hs=hs, confidence=confidence, post=post)
                summary[result] += 1
                post_results[result] += 1
        conn.execute(
            """
            UPDATE telegram_post_cache
            SET mapping_status='mapped',
                matched_companies_json=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (json.dumps([s["stock_name"] for s in stocks], ensure_ascii=False), post["id"]),
        )
        summary["posts_mapped"] += 1
    conn.commit()
    top_missing_company = sorted(summary["missing_company"].items(), key=lambda x: x[1], reverse=True)[:25]
    top_missing_hs = sorted(summary["missing_hs"].items(), key=lambda x: x[1], reverse=True)[:25]
    summary["missing_company"] = dict(top_missing_company)
    summary["missing_hs"] = dict(top_missing_hs)
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
