#!/usr/bin/env python3
"""
Sync StockEasy-style multi-sector tags into stock.db.

This intentionally does not overwrite stock_universe.sector_*.
One stock can have many sector tags, with source/confidence/evidence preserved.
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
API_BASE = "https://stockeasy.intellio.kr/stockdata"
PORTFOLIOS = {
    "momentum": 1,
    "peak": 2,
    "value": 3,
}

# StockEasy-observed sector vocabulary plus local multi-sector expansion.
# confidence: 100 = directly observed from StockEasy, 55~80 = local expansion.
SECTOR_RULES = [
    ("메모리", ["메모리", "dram", "nand", "hbm", "하이닉스", "삼성전자"], 75),
    ("반도체", ["반도체", "hbm", "dram", "nand", "파운드리", "웨이퍼", "소부장", "삼성전자", "sk하이닉스"], 72),
    ("반도체소재", ["fc-bga", "pcb", "패키지", "기판", "mlcc", "반도체소재", "소켓"], 72),
    ("반도체장비", ["반도체장비", "장비", "증착", "식각", "세정", "검사", "테스트"], 70),
    ("테스트소켓", ["테스트소켓", "프로브", "probe", "소켓"], 70),
    ("전력기기", ["전력", "전선", "변압기", "초고압", "해저케이블", "전력망", "데이터센터", "ess"], 75),
    ("배터리셀", ["배터리", "2차전지", "전고체", "lfp", "ess", "삼성sdi", "lg에너지솔루션"], 74),
    ("철강/비철", ["철강", "비철", "구리", "알루미늄", "리튬", "posco", "풍산"], 72),
    ("정유/화학", ["정유", "화학", "소재", "ppo", "석유", "효성티앤씨", "코오롱인더"], 68),
    ("자동차", ["자동차", "전장", "모비스", "현대차", "기아", "hl만도", "성우하이텍", "화신"], 72),
    ("로봇/자동화", ["로봇", "자동화", "휴머노이드", "피지컬 ai", "로보", "현대차", "두산"], 70),
    ("가전/전자", ["가전", "전자", "tv", "oled", "디스플레이", "스마트폰", "삼성전자", "lg전자"], 68),
    ("건설", ["건설", "주택", "토목", "플랜트", "인프라", "도시정비"], 65),
    ("조선", ["조선", "선박", "해양", "lng선", "해운"], 68),
    ("방산", ["방산", "국방", "미사일", "항공우주", "군수"], 68),
    ("통신", ["통신", "5g", "이동통신", "sk텔레콤", "kt", "lg유플러스"], 65),
    ("증권", ["증권", "투자증권", "금융투자"], 65),
    ("보험", ["보험", "손해보험", "생명보험"], 65),
    ("금융", ["은행", "금융", "지주", "카드", "캐피탈"], 62),
    ("엔터/미디어", ["엔터", "미디어", "콘텐츠", "음원", "하이브", "드라마"], 65),
    ("게임", ["게임", "모바일게임", "온라인게임"], 65),
    ("바이오/제약", ["바이오", "제약", "신약", "의약", "치료제"], 65),
    ("의료기기", ["의료기기", "미용기기", "진단", "클래시스", "에이피알"], 65),
    ("화장품", ["화장품", "뷰티", "코스메틱", "콜마", "달바"], 65),
    ("에너지", ["에너지", "원전", "태양광", "풍력", "발전", "송전"], 62),
]

MANUAL_MULTI_TAGS = {
    "005930": [("메모리", 90, "manual: 삼성전자=메모리"), ("반도체", 88, "manual: 삼성전자=반도체"), ("가전/전자", 82, "manual: 삼성전자=가전/전자")],
    "005380": [("자동차", 90, "manual: 현대차=자동차"), ("로봇/자동화", 78, "manual: 현대차=로봇/자동화")],
    "000270": [("자동차", 90, "manual: 기아=자동차")],
    "012330": [("자동차", 86, "manual: 현대모비스=자동차"), ("로봇/자동화", 68, "manual: 현대모비스=전장/자동화")],
}


def _base36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, rem = divmod(n, 36)
        out = chars[rem] + out
    return out


def _stockeasy_app_token() -> str:
    bucket = int(time.time() // 30)
    sig = ((0x45D9F3B * bucket) ^ 0xDEADBEEF) & 0xFFFFFFFF
    return base64.b64encode(f"{bucket}.{_base36(sig)}".encode("utf-8")).decode("ascii")


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "X-App-Token": _stockeasy_app_token(),
    }


def fetch_stockeasy_portfolio(strategy: str, portfolio_id: int) -> dict:
    url = f"{API_BASE}/api/v1/portfolio/{portfolio_id}/holdings"
    r = requests.get(url, headers=_headers(), timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"StockEasy returned success=false for {strategy}")
    return data


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_sector_tags (
            id INTEGER PRIMARY KEY,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            sector TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence INTEGER DEFAULT 0,
            is_primary INTEGER DEFAULT 0,
            evidence TEXT,
            strategy TEXT DEFAULT '',
            observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, sector, source, strategy)
        );
        CREATE INDEX IF NOT EXISTS idx_stock_sector_tags_code ON stock_sector_tags(stock_code);
        CREATE INDEX IF NOT EXISTS idx_stock_sector_tags_sector ON stock_sector_tags(sector);

        CREATE VIEW IF NOT EXISTS v_stock_sector_tags AS
        SELECT stock_code, stock_name, sector, source, confidence, is_primary, evidence, strategy, updated_at
        FROM stock_sector_tags;
        """
    )


def upsert_tag(
    conn: sqlite3.Connection,
    stock_code: str,
    stock_name: str,
    sector: str,
    source: str,
    confidence: int,
    evidence: str,
    strategy: str | None = None,
    is_primary: int = 0,
) -> None:
    if not stock_code or not sector:
        return
    conn.execute(
        """
        INSERT INTO stock_sector_tags
            (stock_code, stock_name, sector, source, confidence, is_primary, evidence, strategy, observed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code, sector, source, strategy) DO UPDATE SET
            stock_name=excluded.stock_name,
            confidence=max(stock_sector_tags.confidence, excluded.confidence),
            is_primary=max(stock_sector_tags.is_primary, excluded.is_primary),
            evidence=excluded.evidence,
            updated_at=CURRENT_TIMESTAMP
        """,
        (stock_code, stock_name, sector, source, confidence, is_primary, evidence[:1000], strategy or ""),
    )


def flatten_stockeasy(data: dict, strategy: str) -> list[dict]:
    rows = []
    for bucket in ("holdings", "exits"):
        for sector, items in (data.get(bucket) or {}).items():
            for item in items or []:
                rows.append(
                    {
                        "strategy": strategy,
                        "bucket": bucket,
                        "sector": item.get("sector") or sector,
                        "stock_code": item.get("stock_code"),
                        "stock_name": item.get("stock_name"),
                        "research": item.get("research") or {},
                    }
                )
    return rows


def row_text(row: sqlite3.Row, research_text: str = "") -> str:
    # Do not include market name here. "유가증권" would otherwise tag most KOSPI names as 증권.
    parts = [
        row["stock_code"] or "",
        row["stock_name"] or "",
        row["sector_large"] or "",
        row["sector_mid"] or "",
        row["sector_small"] or "",
        research_text or "",
    ]
    return " ".join(parts).lower()


def cleanup_noisy_tags(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM stock_sector_tags
        WHERE sector='증권'
          AND source IN ('local_rule', 'stockeasy_research_rule')
          AND stock_code IN (
            SELECT t.stock_code
            FROM stock_sector_tags t
            LEFT JOIN stock_universe u ON u.stock_code=t.stock_code
            WHERE t.sector='증권'
              AND t.source IN ('local_rule', 'stockeasy_research_rule')
              AND (
                COALESCE(u.stock_name, '') NOT LIKE '%증권%'
                AND COALESCE(u.stock_name, '') NOT LIKE '%금융투자%'
                AND COALESCE(u.sector_large, '') NOT LIKE '%증권%'
                AND COALESCE(u.sector_mid, '') NOT LIKE '%증권%'
                AND COALESCE(u.sector_small, '') NOT LIKE '%증권%'
                AND COALESCE(u.sector_large, '') NOT LIKE '%금융투자%'
                AND COALESCE(u.sector_mid, '') NOT LIKE '%금융투자%'
                AND COALESCE(u.sector_small, '') NOT LIKE '%금융투자%'
              )
          )
        """
    )


def build_research_text(research: dict) -> str:
    summary = (research or {}).get("summary") or {}
    items = summary.get("content_list") or []
    return " ".join([summary.get("title") or "", *items])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-network", action="store_true", help="Do not fetch StockEasy; only use local rules.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    universe_rows = conn.execute(
        """
        SELECT stock_code, stock_name, market, sector_large, sector_mid, sector_small
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND COALESCE(stock_type, '보통주') NOT IN ('ETF', 'ETN')
        GROUP BY stock_code
        """
    ).fetchall()
    by_code = {r["stock_code"]: r for r in universe_rows}

    observed_rows: list[dict] = []
    if not args.no_network:
        for strategy, portfolio_id in PORTFOLIOS.items():
            data = fetch_stockeasy_portfolio(strategy, portfolio_id)
            observed_rows.extend(flatten_stockeasy(data, strategy))

    ops = 0
    observed_codes = set()
    for item in observed_rows:
        code = item["stock_code"]
        observed_codes.add(code)
        sector = item["sector"]
        stock_name = item["stock_name"] or (by_code.get(code, {}) or {}).get("stock_name", "")
        evidence = f"StockEasy {item['strategy']} {item['bucket']}: {sector}"
        upsert_tag(conn, code, stock_name, sector, "stockeasy_strategy", 100, evidence, item["strategy"], 1)
        ops += 1

        research_text = build_research_text(item.get("research") or {})
        fake_row = by_code.get(code)
        if fake_row:
            text = row_text(fake_row, research_text)
            for sector_name, keywords, confidence in SECTOR_RULES:
                hits = [kw for kw in keywords if kw.lower() in text]
                if hits and sector_name != sector:
                    upsert_tag(
                        conn,
                        code,
                        stock_name,
                        sector_name,
                        "stockeasy_research_rule",
                        confidence,
                        f"research/local keywords: {', '.join(hits[:5])}",
                        item["strategy"],
                        0,
                    )
                    ops += 1

    for r in universe_rows:
        text = row_text(r)
        for sector_name, keywords, confidence in SECTOR_RULES:
            hits = [kw for kw in keywords if kw.lower() in text]
            if hits:
                upsert_tag(
                    conn,
                    r["stock_code"],
                    r["stock_name"],
                    sector_name,
                    "local_rule",
                    confidence,
                    f"universe keywords: {', '.join(hits[:5])}",
                    None,
                    0,
                )
                ops += 1

    for code, tags in MANUAL_MULTI_TAGS.items():
        r = by_code.get(code)
        if not r:
            continue
        for sector, confidence, evidence in tags:
            upsert_tag(conn, code, r["stock_name"], sector, "manual_multi", confidence, evidence, None, 0)
            ops += 1

    cleanup_noisy_tags(conn)

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM stock_sector_tags").fetchone()[0]
    multi = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT stock_code FROM stock_sector_tags GROUP BY stock_code HAVING COUNT(DISTINCT sector) > 1
        )
        """
    ).fetchone()[0]
    print(json.dumps({
        "dry_run": args.dry_run,
        "ops": ops,
        "observed_stockeasy_codes": len(observed_codes),
        "total_tags": total,
        "multi_sector_stocks": multi,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
