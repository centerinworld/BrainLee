from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
ROOT_STOCK_DB = ROOT_DIR.parent / "stock.db"

SIDO_ALIASES = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "경기도": "경기도",
    "강원": "강원특별자치도",
    "강원도": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}

REGION_RE = re.compile(r"\(([^)]*(?:시|군|구)[^)]*)\)")
GENERIC_SHIP_ENGINE_LABEL_RE = re.compile(r"선박추진용|디젤|세미디젤|압축점화식")

USER_ANCHORS = [
    {
        "stock_code": "047810",
        "stock_name": "한국항공우주",
        "hs_code": "8807301000",
        "hs_name": "비행기의 부분품",
        "sido_name": "경상남도",
        "sigungu_name": "경상남도 사천시",
        "note": "사용자 제보: 사천 항공기부품 수출 집중도가 높아 한국항공우주 지역 기반 매핑 후보로 반영",
    },
    {
        "stock_code": "047810",
        "stock_name": "한국항공우주",
        "hs_code": "8807302000",
        "hs_name": "헬리콥터의 부분품",
        "sido_name": "경상남도",
        "sigungu_name": "경상남도 사천시",
        "note": "사용자 제보: 사천 항공기부품 수출 집중도가 높아 한국항공우주 지역 기반 매핑 후보로 반영",
    },
]

COMPANY_HS_DENYLIST = {
    ("005930", "9021290000"),  # 수원시 지역 근거가 덴티움 임플란트와 충돌해 삼성전자에 오매핑되는 사례 차단
}


@dataclass(frozen=True)
class Region:
    sido_name: str
    sigungu_name: str


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS regional_company_mapping_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_key TEXT NOT NULL UNIQUE,
            stock_code TEXT NOT NULL DEFAULT '',
            stock_name TEXT NOT NULL DEFAULT '',
            hs_code TEXT NOT NULL DEFAULT '',
            hs_name TEXT NOT NULL DEFAULT '',
            sido_name TEXT NOT NULL DEFAULT '',
            sigungu_name TEXT NOT NULL DEFAULT '',
            period_start TEXT NOT NULL DEFAULT '',
            period_end TEXT NOT NULL DEFAULT '',
            export_value REAL NOT NULL DEFAULT 0,
            import_value REAL NOT NULL DEFAULT 0,
            post_count INTEGER NOT NULL DEFAULT 0,
            telegram_titles_json TEXT NOT NULL DEFAULT '[]',
            telegram_urls_json TEXT NOT NULL DEFAULT '[]',
            evidence_score REAL NOT NULL DEFAULT 0,
            evidence_status TEXT NOT NULL DEFAULT 'candidate',
            evidence_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_regional_evidence_company ON regional_company_mapping_evidence (stock_code, stock_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_regional_evidence_region ON regional_company_mapping_evidence (sido_name, sigungu_name)"
    )


def normalize_region_text(text: str) -> str:
    text = re.sub(r"_[^)]+", "", text)
    text = text.replace("+", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_regions(title: str) -> list[Region]:
    regions: list[Region] = []
    for raw_region in REGION_RE.findall(title):
        text = normalize_region_text(raw_region)
        if "전국" in text or "글로벌" in text:
            continue
        parts = text.split()
        if not parts:
            continue
        sido_name = SIDO_ALIASES.get(parts[0], parts[0])
        local_tokens = [part for part in parts[1:] if part.endswith(("시", "군", "구"))]
        if not local_tokens:
            continue
        sigungu_short = local_tokens[-1]
        regions.append(Region(sido_name=sido_name, sigungu_name=f"{sido_name} {sigungu_short}"))
    return list(dict.fromkeys(regions))


def load_stock_lookup(stock_conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for table in ("stock_universe", "stock_meta"):
        try:
            rows = stock_conn.execute(
                f"SELECT stock_code, stock_name, COALESCE(market, '') FROM {table} WHERE stock_name <> ''"
            ).fetchall()
        except sqlite3.Error:
            continue
        for code, name, market in rows:
            lookup.setdefault(name, {"stock_code": code, "stock_name": name, "market": market or ""})
    return lookup


def load_hs_lookup(conn: sqlite3.Connection) -> dict[str, list[dict[str, str]]]:
    lookup: dict[str, list[dict[str, str]]] = {}
    for hs_code, hs_name, display_name in conn.execute(
        """
        SELECT hs_code, hs_name, display_name
        FROM hs_sector_map
        WHERE display_name <> ''
        """
    ):
        lookup.setdefault(display_name, []).append({"hs_code": hs_code, "hs_name": hs_name})
    for hs_code, hs_name, sector_name in conn.execute(
        """
        SELECT hs_code, hs_name, sector_name
        FROM hs_code_company_map
        WHERE sector_name <> ''
        """
    ):
        lookup.setdefault(sector_name, []).append({"hs_code": hs_code, "hs_name": hs_name})
    return lookup


def region_trade(conn: sqlite3.Connection, region: Region) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT MIN(period_ym), MAX(period_ym), SUM(export_value), SUM(import_value)
        FROM sigungu_trade_record
        WHERE sido_name = ? AND sigungu_name = ?
        """,
        (region.sido_name, region.sigungu_name),
    ).fetchone()
    return {
        "period_start": row[0] or "",
        "period_end": row[1] or "",
        "export_value": float(row[2] or 0),
        "import_value": float(row[3] or 0),
    }


def existing_mapping(conn: sqlite3.Connection, stock_code: str, hs_code: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, confidence, mapping_status, note
        FROM hs_code_company_map
        WHERE stock_code = ? AND hs_code = ?
        """,
        (stock_code, hs_code),
    ).fetchone()


def is_denied_mapping(stock_code: str, hs_code: str) -> bool:
    return (stock_code, hs_code) in COMPANY_HS_DENYLIST


def evidence_score(company_count: int, trade: dict[str, Any], has_existing: bool, is_anchor: bool) -> float:
    score = 0.52
    if is_anchor:
        score += 0.18
    if 0 < company_count <= 2:
        score += 0.14
    elif company_count <= 4:
        score += 0.08
    if trade["export_value"] >= 10_000_000:
        score += 0.08
    if has_existing:
        score += 0.08
    if trade["period_end"]:
        score += 0.05
    return min(round(score, 2), 0.95)


def upsert_evidence(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    stock_name: str,
    hs_code: str,
    hs_name: str,
    region: Region,
    trade: dict[str, Any],
    titles: list[str],
    urls: list[str],
    score: float,
    note: str,
) -> None:
    evidence_key = "|".join([stock_code, hs_code, region.sido_name, region.sigungu_name])
    conn.execute(
        """
        INSERT INTO regional_company_mapping_evidence (
            evidence_key, stock_code, stock_name, hs_code, hs_name, sido_name, sigungu_name,
            period_start, period_end, export_value, import_value, post_count,
            telegram_titles_json, telegram_urls_json, evidence_score, evidence_status, evidence_note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?)
        ON CONFLICT(evidence_key) DO UPDATE SET
            stock_name=excluded.stock_name,
            hs_name=excluded.hs_name,
            period_start=excluded.period_start,
            period_end=excluded.period_end,
            export_value=excluded.export_value,
            import_value=excluded.import_value,
            post_count=excluded.post_count,
            telegram_titles_json=excluded.telegram_titles_json,
            telegram_urls_json=excluded.telegram_urls_json,
            evidence_score=excluded.evidence_score,
            evidence_note=excluded.evidence_note,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            evidence_key,
            stock_code,
            stock_name,
            hs_code,
            hs_name,
            region.sido_name,
            region.sigungu_name,
            trade["period_start"],
            trade["period_end"],
            trade["export_value"],
            trade["import_value"],
            len(titles),
            json.dumps(titles[:12], ensure_ascii=False),
            json.dumps(urls[:12], ensure_ascii=False),
            score,
            note,
        ),
    )


def apply_mapping(conn: sqlite3.Connection, evidence: sqlite3.Row) -> str:
    if is_denied_mapping(evidence["stock_code"], evidence["hs_code"]):
        return "denied"
    existing = existing_mapping(conn, evidence["stock_code"], evidence["hs_code"])
    display_region = evidence["sigungu_name"]
    if not str(display_region).startswith(str(evidence["sido_name"])):
        display_region = f"{evidence['sido_name']} {display_region}"
    note = (
        f"시군구 수출입실적({display_region}, "
        f"{evidence['period_start']}~{evidence['period_end']} 수출 {evidence['export_value']:.0f}달러) "
        f"+ 텔레그램/기존 매핑 증거 기반 보강. {evidence['evidence_note']}"
    )
    confidence = min(float(evidence["evidence_score"]), 0.86)
    if existing:
        if confidence <= float(existing["confidence"] or 0):
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
            evidence["hs_code"],
            evidence["hs_name"],
            evidence["stock_code"],
            evidence["stock_name"],
            evidence["hs_name"],
            confidence,
            note,
        ),
    )
    return "inserted"


def build_from_posts(conn: sqlite3.Connection, stock_lookup: dict[str, dict[str, str]], hs_lookup: dict[str, list[dict[str, str]]]) -> int:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT title, post_url, companies_json, company_count, matched_hs_codes_json
        FROM telegram_post_cache
        WHERE posted_at >= datetime('now', '-370 days')
          AND matched_hs_codes_json <> '[]'
        """
    ).fetchall()
    for row in rows:
        regions = extract_regions(row["title"])
        if not regions:
            continue
        companies = json.loads(row["companies_json"] or "[]")
        hs_names = json.loads(row["matched_hs_codes_json"] or "[]")
        for company_name in companies:
            stock = stock_lookup.get(company_name)
            if not stock:
                continue
            for hs_name_key in hs_names:
                if GENERIC_SHIP_ENGINE_LABEL_RE.search(str(hs_name_key)) and not any(
                    token in row["title"] for token in ["선박", "엔진", "HD현대", "STX", "조선"]
                ):
                    continue
                for hs in hs_lookup.get(hs_name_key, []):
                    if is_denied_mapping(stock["stock_code"], hs["hs_code"]):
                        continue
                    for region in regions:
                        key = (stock["stock_code"], hs["hs_code"], region.sido_name, region.sigungu_name)
                        item = grouped.setdefault(
                            key,
                            {
                                "stock_code": stock["stock_code"],
                                "stock_name": stock["stock_name"],
                                "hs_code": hs["hs_code"],
                                "hs_name": hs["hs_name"],
                                "region": region,
                                "company_count": row["company_count"],
                                "titles": [],
                                "urls": [],
                            },
                        )
                        item["company_count"] = min(item["company_count"], row["company_count"])
                        item["titles"].append(row["title"])
                        item["urls"].append(row["post_url"])
    for item in grouped.values():
        trade = region_trade(conn, item["region"])
        has_existing = existing_mapping(conn, item["stock_code"], item["hs_code"]) is not None
        score = evidence_score(item["company_count"], trade, has_existing, False)
        upsert_evidence(
            conn,
            stock_code=item["stock_code"],
            stock_name=item["stock_name"],
            hs_code=item["hs_code"],
            hs_name=item["hs_name"],
            region=item["region"],
            trade=trade,
            titles=sorted(set(item["titles"])),
            urls=sorted(set(item["urls"])),
            score=score,
            note="텔레그램 1년치 지역/기업/품목 언급과 시군구 수출입실적 결합",
        )
    return len(grouped)


def build_user_anchors(conn: sqlite3.Connection) -> int:
    count = 0
    for anchor in USER_ANCHORS:
        region = Region(anchor["sido_name"], anchor["sigungu_name"])
        trade = region_trade(conn, region)
        score = evidence_score(1, trade, existing_mapping(conn, anchor["stock_code"], anchor["hs_code"]) is not None, True)
        upsert_evidence(
            conn,
            stock_code=anchor["stock_code"],
            stock_name=anchor["stock_name"],
            hs_code=anchor["hs_code"],
            hs_name=anchor["hs_name"],
            region=region,
            trade=trade,
            titles=["사용자 제보: 사천 항공기부품 수출 집중도 기반 한국항공우주 매핑"],
            urls=[],
            score=score,
            note=anchor["note"],
        )
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Build regional evidence for company-HS mappings.")
    parser.add_argument("--apply-mappings", action="store_true")
    parser.add_argument("--min-score", type=float, default=0.72)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    stock_conn = sqlite3.connect(ROOT_STOCK_DB)
    stock_lookup = load_stock_lookup(stock_conn)
    stock_conn.close()
    hs_lookup = load_hs_lookup(conn)

    post_candidates = build_from_posts(conn, stock_lookup, hs_lookup)
    anchor_candidates = build_user_anchors(conn)
    applied = {"inserted": 0, "updated": 0, "kept": 0}
    if args.apply_mappings:
        rows = conn.execute(
            """
            SELECT *
            FROM regional_company_mapping_evidence
            WHERE evidence_score >= ?
              AND stock_code <> ''
              AND hs_code <> ''
            ORDER BY evidence_score DESC
            """,
            (args.min_score,),
        ).fetchall()
        for row in rows:
            result = apply_mapping(conn, row)
            applied[result] += 1

    conn.commit()
    top = conn.execute(
        """
        SELECT stock_code, stock_name, hs_code, hs_name, sigungu_name, evidence_score, export_value
        FROM regional_company_mapping_evidence
        ORDER BY evidence_score DESC, export_value DESC
        LIMIT 15
        """
    ).fetchall()
    total_evidence = conn.execute("SELECT COUNT(*) FROM regional_company_mapping_evidence").fetchone()[0]
    conn.close()
    print(
        json.dumps(
            {
                "post_candidates": post_candidates,
                "anchor_candidates": anchor_candidates,
                "total_evidence": total_evidence,
                "applied": applied,
                "top": [dict(row) for row in top],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
