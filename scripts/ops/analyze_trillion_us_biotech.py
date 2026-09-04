#!/usr/bin/env python3
"""Ticker-first review of US biotech references in the Telegram Trillion channel.

The channel often uses a symbol without writing "biotech".  This tool therefore
does not discard an SEC-valid US ticker merely because the surrounding message
has no biotech keyword: unclassified symbols are exported to a review queue.
It is a research aid, not investment advice or an automatic trade signal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from telethon import TelegramClient


RUNTIME = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
OUTPUT_DIR = RUNTIME / "research_outputs" / "trillion_us_biotech"
SEC_CACHE = OUTPUT_DIR / "sec_company_tickers.json"
DEFAULT_SESSION = RUNTIME / "telegram_session_user"
DEFAULT_CHANNEL = "Trillion_labs"
DB_PATH = RUNTIME / "stock.db"
POSTGRES_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard",
).replace("postgresql+psycopg://", "postgresql://")
SEC_URL = "https://www.sec.gov/files/company_tickers.json"

# These are high-confidence US biotech symbols and the symbols directly used in
# Trillion's professional-investor-flow posts.  Ambiguous symbols remain here
# but are only accepted when a biotech/company context is present.
KNOWN_US_BIOTECH = {
    "ACHV", "ADMA", "AGLE", "ALMR", "ALRN", "ALT", "ANL", "ARTV", "AURA",
    "AVLN", "AVTX", "AXSM", "BEAM", "BHVN", "BIOA", "BNTX", "CAPR", "CLRB",
    "COAG", "CRSP", "CUE", "DFTX", "DNA", "EDIT", "ELVN", "FTH", "GLUE",
    "GUBRA", "IFRX", "INM", "INSM", "IOVA", "KARD", "KLRA", "MDGL", "MOBI",
    "MERO", "MRNA", "NKTR", "NTLA", "NVO", "NVCT", "ODTX", "ORKA", "PBLS", "PFE",
    "REGN", "RLAY", "ROIV", "RVMD", "RXRX", "SGMT", "SMMT", "SPTX", "TRAX",
    "VERV", "XENE",
}

# A plain token such as ALT, DNA, or BEAM also has non-biotech meanings.  It is
# retained when written as a cashtag/exchange ticker, otherwise needs context.
AMBIGUOUS_TICKERS = {"ALT", "ANL", "ARTV", "BEAM", "BIOA", "DNA", "FTH", "INM", "ORKA", "TRAX"}

COMPANY_ALIASES = {
    "Biohaven": "BHVN", "바이오헤이븐": "BHVN", "Capricor": "CAPR", "카프리코": "CAPR",
    "Roivant": "ROIV", "로이반트": "ROIV", "Revolution Medicines": "RVMD",
    "Nektar": "NKTR", "넥타": "NKTR", "Xenon": "XENE", "제논": "XENE",
    "Moderna": "MRNA", "모더나": "MRNA", "BioNTech": "BNTX", "바이오엔텍": "BNTX",
    "Regeneron": "REGN", "리제네론": "REGN", "Pfizer": "PFE", "화이자": "PFE",
    "Novo Nordisk": "NVO", "노보노디스크": "NVO", "CRISPR Therapeutics": "CRSP",
    "Beam Therapeutics": "BEAM", "Ginkgo Bioworks": "DNA", "Altimmune": "ALT",
    "Oruka": "ORKA", "Insmed": "INSM", "Axsome": "AXSM", "Relay Therapeutics": "RLAY",
}

BIOTECH_CONTEXT = re.compile(
    r"바이오|biotech|제약|pharma|FDA|임상|clinical|trial|drug|therapy|oncology|oncolog|"
    r"신약|희귀질환|유전자|세포치료|단백질|항체|PDUFA|의약품|의료", re.IGNORECASE
)
MARKET_CONTEXT = re.compile(r"미국|미장|나스닥|NYSE|NASDAQ|티커|종목|주식|시총|달러|불|포지션|보유|매수|매도", re.IGNORECASE)
BUY_TERMS = re.compile(r"매수|롱포지션|롱 포지션|장기보유|장기 보유|포지셔닝|담았|들고.?가|좋겠다고|매력", re.IGNORECASE)
SELL_TERMS = re.compile(r"매도|숏포지션|숏 포지션|손절|안.?사|피하|더 볼 것도 없|반대", re.IGNORECASE)
RISK_TERMS = re.compile(r"실패|주의|위험|약점|부정|하락|불확실|중단|우려|과도", re.IGNORECASE)
CATALYST_TERMS = re.compile(r"승인|허가|성공|우월|호재|상향|진입|유입|phase|상|PDUFA|FDA", re.IGNORECASE)
INVESTOR_FLOW_TERMS = re.compile(r"전문투자자|기관.?유입|보유.?건수|13F|신규.?진입|기존.?투자자", re.IGNORECASE)
PERSONAL_ACTION_TERMS = re.compile(r"개인적으로|저같으면|제가|포지셔닝.?셋|내.?포지션|장기보유", re.IGNORECASE)
EXPLICIT_BUY_ACTION = re.compile(r"매수|롱포지션|롱 포지션|장기보유|장기 보유|포지셔닝.?셋|저같으면", re.IGNORECASE)
EXPLICIT_SELL_ACTION = re.compile(r"매도|숏포지션|숏 포지션|손절|안.?사|사지.?마|더 볼 것도 없|반대", re.IGNORECASE)
SEC_BIOTECH_TITLE = re.compile(
    r"therapeutics|pharma|pharmaceutical|biotech|biosciences|biopharma|biomedical|"
    r"oncology|genetics|genomics|life sciences|cell therapy|diagnostics", re.IGNORECASE
)
CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5})(?![A-Za-z0-9])")
HASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])#([A-Z]{1,5})(?![A-Za-z0-9])")
EXCHANGE_RE = re.compile(r"\b(?:NASDAQ|NYSE|AMEX|OTC)\s*[:\-]?\s*([A-Z]{1,5})\b", re.IGNORECASE)
BARE_RE = re.compile(r"(?<![A-Za-z])\b([A-Z]{2,5})\b(?![A-Za-z])")


def load_env() -> None:
    env_file = RUNTIME / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_sec_master(refresh: bool) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    is_fresh = SEC_CACHE.exists() and (datetime.now().timestamp() - SEC_CACHE.stat().st_mtime) < 7 * 86400
    if refresh or not is_fresh:
        try:
            response = requests.get(
                SEC_URL,
                headers={"User-Agent": "stock-dashboard research ops@newsinfo.cloud", "Accept-Encoding": "gzip, deflate"},
                timeout=30,
            )
            response.raise_for_status()
            SEC_CACHE.write_text(response.text, encoding="utf-8")
        except requests.RequestException as exc:
            # SEC occasionally blocks automated requests.  Direct ticker syntax
            # remains reviewable below, so a temporary 403 never creates a gap.
            if not SEC_CACHE.exists():
                print(f"[warning] SEC ticker master unavailable: {exc}")
                return {}
    payload = json.loads(SEC_CACHE.read_text(encoding="utf-8"))
    return {
        str(row.get("ticker", "")).upper(): str(row.get("title", ""))
        for row in payload.values()
        if row.get("ticker")
    }


def excerpt(text: str, max_len: int = 280) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def relevant_text(text: str, symbol: str) -> str:
    aliases = [alias for alias, mapped in COMPANY_ALIASES.items() if mapped == symbol]
    needle = re.compile(rf"\b{re.escape(symbol)}\b|" + "|".join(re.escape(alias) for alias in aliases), re.IGNORECASE)
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    hits = [idx for idx, line in enumerate(lines) if needle.search(line)]
    if not hits:
        return text
    keep: set[int] = set()
    for idx in hits:
        keep.update(range(max(0, idx - 1), min(len(lines), idx + 2)))
    return " ".join(lines[idx] for idx in sorted(keep))


def direction(text: str, symbol: str) -> tuple[str, str]:
    text = relevant_text(text, symbol)
    buy, sell, risk, catalyst = (bool(rx.search(text)) for rx in (BUY_TERMS, SELL_TERMS, RISK_TERMS, CATALYST_TERMS))
    if INVESTOR_FLOW_TERMS.search(text) and not PERSONAL_ACTION_TERMS.search(text):
        return "investor_flow_watch", "research"
    if EXPLICIT_BUY_ACTION.search(text) and not EXPLICIT_SELL_ACTION.search(text):
        return "explicit_buy_or_hold", "explicit"
    if EXPLICIT_SELL_ACTION.search(text) and not EXPLICIT_BUY_ACTION.search(text):
        return "explicit_sell_or_avoid", "explicit"
    if catalyst and not risk:
        return "positive_catalyst", "research"
    if risk and not catalyst:
        return "risk_or_negative_catalyst", "research"
    if buy and sell:
        return "mixed_view", "research"
    return "neutral_reference", "research"


def signal_level(stance: str) -> str:
    return {
        "explicit_buy_or_hold": "strong_positive",
        "positive_catalyst": "positive",
        "investor_flow_watch": "watch",
        "mixed_view": "mixed",
        "risk_or_negative_catalyst": "negative",
        "explicit_sell_or_avoid": "strong_negative",
        "neutral_reference": "neutral",
    }.get(stance, "neutral")


def key_summary(text: str, symbol: str, stance: str) -> str:
    """Publishable synthesis only; never returns an authenticated post excerpt."""
    focus = relevant_text(text, symbol).lower()
    topics = []
    if any(word in focus for word in ("fda", "pdufa", "승인", "허가", "adcom")): topics.append("FDA·허가 일정")
    if any(word in focus for word in ("임상", "phase", "상", "trial", "clinical")): topics.append("임상 결과·개발 단계")
    if any(word in focus for word in ("파이프라인", "asset", "후보물질")): topics.append("파이프라인 가치")
    if any(word in focus for word in ("현금", "증자", "시총", "밸류", "valuation")): topics.append("현금·밸류에이션")
    if any(word in focus for word in ("전문투자자", "보유", "유입", "13f")): topics.append("전문투자자 보유 변화")
    if not topics: topics.append("기업·산업 동향")
    action = {"strong_positive":"강한 긍정 관점", "positive":"긍정 촉매", "watch":"관찰 신호", "mixed":"상반된 근거", "negative":"위험 요인", "strong_negative":"명시적 회피 관점", "neutral":"정보성 언급"}[signal_level(stance)]
    return f"{action}: {', '.join(topics[:3])}을 핵심 논점으로 다뤘습니다."


def find_symbols(text: str, sec_master: dict[str, str]) -> list[tuple[str, str]]:
    """Return (symbol, confidence source), preserving ambiguous cases for review."""
    sec_symbols = set(sec_master)
    symbols: dict[str, str] = {}
    for pattern, source in ((CASHTAG_RE, "cashtag"), (HASHTAG_RE, "hashtag"), (EXCHANGE_RE, "exchange")):
        for raw in pattern.findall(text):
            symbol = raw.upper()
            if not sec_symbols or symbol in sec_symbols:
                symbols[symbol] = source
    for alias, symbol in COMPANY_ALIASES.items():
        if re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text, re.IGNORECASE):
            symbols.setdefault(symbol, "company_alias")
    biotech_context = bool(BIOTECH_CONTEXT.search(text))
    biotech_or_market_context = bool(biotech_context or MARKET_CONTEXT.search(text))
    for raw in BARE_RE.findall(text):
        symbol = raw.upper()
        if sec_symbols and symbol not in sec_symbols:
            continue
        sec_title_is_biotech = bool(SEC_BIOTECH_TITLE.search(sec_master.get(symbol, "")))
        if symbol in KNOWN_US_BIOTECH and (symbol not in AMBIGUOUS_TICKERS or biotech_or_market_context):
            symbols.setdefault(symbol, "known_biotech_ticker")
        elif sec_title_is_biotech:
            symbols.setdefault(symbol, "sec_biotech_ticker")
        elif biotech_context:
            # Never silently drop an SEC-valid ticker in a market/biotech post.
            symbols.setdefault(symbol, "unclassified_ticker")
    return sorted(symbols.items())


async def collect(channel: str, session: Path, sec_master: dict[str, str], limit: int) -> dict[str, Any]:
    load_env()
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH 설정이 필요합니다.")
    # A running collector may hold the long-lived session SQLite file.  Telethon
    # writes connection metadata even for read-only work, so isolate this scan.
    source_session = session if session.suffix == ".session" else session.with_suffix(".session")
    scan_session = Path(tempfile.gettempdir()) / f"trillion_biotech_scan_{os.getpid()}.session"
    shutil.copy2(source_session, scan_session)
    client = TelegramClient(str(scan_session.with_suffix("")), api_id, api_hash)
    await client.connect()
    entity = await client.get_entity(channel)
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reviewed: list[dict[str, Any]] = []
    priority_reviewed: list[dict[str, Any]] = []
    total = 0
    try:
        async for message in client.iter_messages(entity, limit=limit or None):
            total += 1
            text = (message.message or "").strip()
            if not text:
                continue
            for symbol, source in find_symbols(text, sec_master):
                label, evidence_type = direction(text, symbol)
                row = {
                    "message_id": int(message.id),
                    "date": message.date.astimezone(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "detection": source,
                    "classification": (
                        "known_us_biotech"
                        if symbol in KNOWN_US_BIOTECH or SEC_BIOTECH_TITLE.search(sec_master.get(symbol, ""))
                        else "needs_biotech_review"
                    ),
                    "stance": label,
                    "signal_level": signal_level(label),
                    "summary_text": key_summary(text, symbol, label),
                    "evidence_type": evidence_type,
                    "excerpt": excerpt(text),
                }
                matches[symbol].append(row)
                if row["classification"] == "needs_biotech_review":
                    reviewed.append(row)
                    if source in {"cashtag", "hashtag", "exchange"}:
                        priority_reviewed.append(row)
    finally:
        await client.disconnect()
        scan_session.unlink(missing_ok=True)
    summaries = []
    for symbol, rows in matches.items():
        stances = Counter(r["stance"] for r in rows)
        summaries.append({
            "symbol": symbol,
            "classification": rows[0]["classification"],
            "mentions": len(rows),
            "explicit_buy_or_hold": stances["explicit_buy_or_hold"],
            "explicit_sell_or_avoid": stances["explicit_sell_or_avoid"],
            "positive_catalyst": stances["positive_catalyst"],
            "risk_or_negative_catalyst": stances["risk_or_negative_catalyst"],
            "latest_date": max(r["date"] for r in rows),
            "latest_excerpt": max(rows, key=lambda r: r["date"])["excerpt"],
        })
    summaries.sort(key=lambda row: (-row["mentions"], row["symbol"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": getattr(entity, "title", channel),
        "channel_handle": channel,
        "messages_scanned": total,
        "known_biotech_symbols": len([r for r in summaries if r["classification"] == "known_us_biotech"]),
        "review_queue_symbols": len({r["symbol"] for r in priority_reviewed}),
        "unresolved_candidate_symbols": len({r["symbol"] for r in reviewed}),
        "symbols": summaries,
        "mentions": {symbol: rows for symbol, rows in sorted(matches.items())},
        "review_queue": priority_reviewed,
        "unresolved_candidates": reviewed,
        "methodology": {
            "scope": "All readable channel messages, with ticker-first detection.",
            "important": "Explicit buy/sell wording is separated from news, catalysts and investor-flow references. It is not investment advice.",
            "review_queue": "Ticker syntax ($TICKER, #TICKER, NASDAQ: TICKER) without a verified biotech entry is the priority review queue. All lower-confidence unresolved candidates are retained separately instead of being dropped.",
        },
    }


def save_results(result: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"trillion_us_biotech_{stamp}.json"
    latest_path = OUTPUT_DIR / "latest.json"
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    json_path.write_text(rendered, encoding="utf-8")
    latest_path.write_text(rendered, encoding="utf-8")
    return json_path, latest_path


def _mention_rows(result: dict[str, Any], sec_master: dict[str, str], source_key: str, source_name: str, source_type: str) -> list[tuple[Any, ...]]:
    rows = []
    for ticker, mentions in result["mentions"].items():
        for row in mentions:
            rows.append((
                source_key, source_type, source_name, str(row["message_id"]), row["date"], ticker,
                sec_master.get(ticker) or ticker,
                "us_biotech" if row["classification"] == "known_us_biotech" else "other_us_equity_candidate",
                row["detection"], row["stance"], row["signal_level"], row["evidence_type"], row["summary_text"], row["excerpt"], row["excerpt"],
            ))
    return rows


def _persist_results_postgres(result: dict[str, Any], sec_master: dict[str, str], source_key: str, source_name: str, source_type: str) -> None:
    """Persist the source mirror in the PostgreSQL database used by the API."""
    import psycopg

    with psycopg.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS source_intelligence_sources (
                    source_key TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    refresh_frequency TEXT NOT NULL DEFAULT 'daily',
                    last_refreshed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS source_intelligence_mentions (
                    id BIGSERIAL PRIMARY KEY,
                    source_key TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    published_at TIMESTAMPTZ NOT NULL,
                    ticker TEXT NOT NULL,
                    company_name TEXT,
                    asset_class TEXT NOT NULL,
                    detection TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    signal_level TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    summary_text TEXT NOT NULL DEFAULT '',
                    excerpt TEXT NOT NULL,
                    raw_text TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_key, message_id, ticker)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_source_intel_asset ON source_intelligence_mentions(asset_class, signal_level, published_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_source_intel_source ON source_intelligence_mentions(source_key, published_at DESC)")
            cur.execute(
                """INSERT INTO source_intelligence_sources (source_key, source_name, source_type, last_refreshed_at, updated_at)
                   VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                   ON CONFLICT(source_key) DO UPDATE SET source_name=EXCLUDED.source_name, source_type=EXCLUDED.source_type,
                       last_refreshed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP""",
                (source_key, source_name, source_type),
            )
            rows = _mention_rows(result, sec_master, source_key, source_name, source_type)
            cur.executemany(
                """INSERT INTO source_intelligence_mentions
                (source_key, source_type, source_name, message_id, published_at, ticker, company_name, asset_class,
                 detection, stance, signal_level, evidence_type, summary_text, excerpt, raw_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(source_key, message_id, ticker) DO UPDATE SET
                    company_name=EXCLUDED.company_name, asset_class=EXCLUDED.asset_class, detection=EXCLUDED.detection,
                    stance=EXCLUDED.stance, signal_level=EXCLUDED.signal_level, evidence_type=EXCLUDED.evidence_type,
                    summary_text=EXCLUDED.summary_text, excerpt=EXCLUDED.excerpt, raw_text=EXCLUDED.raw_text,
                    updated_at=CURRENT_TIMESTAMP""",
                rows,
            )


def _persist_results_sqlite(result: dict[str, Any], sec_master: dict[str, str], source_key: str, source_name: str, source_type: str) -> None:
    """Portable fallback for an offline SQLite-only installation."""
    conn = sqlite3.connect(DB_PATH, timeout=120)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_intelligence_sources (
                source_key TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                refresh_frequency TEXT NOT NULL DEFAULT 'weekly',
                last_refreshed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS source_intelligence_mentions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                message_id TEXT NOT NULL,
                published_at TEXT NOT NULL,
                ticker TEXT NOT NULL,
                company_name TEXT,
                asset_class TEXT NOT NULL,
                detection TEXT NOT NULL,
                stance TEXT NOT NULL,
                signal_level TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                summary_text TEXT NOT NULL DEFAULT '',
                excerpt TEXT NOT NULL,
                raw_text TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_key, message_id, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_source_intel_asset ON source_intelligence_mentions(asset_class, signal_level, published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_source_intel_source ON source_intelligence_mentions(source_key, published_at DESC);
        """)
        try:
            conn.execute("ALTER TABLE source_intelligence_mentions ADD COLUMN summary_text TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """INSERT INTO source_intelligence_sources (source_key, source_name, source_type, last_refreshed_at, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(source_key) DO UPDATE SET source_name=excluded.source_name, source_type=excluded.source_type,
                   last_refreshed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP""",
            (source_key, source_name, source_type),
        )
        rows = _mention_rows(result, sec_master, source_key, source_name, source_type)
        conn.executemany(
            """INSERT INTO source_intelligence_mentions
            (source_key, source_type, source_name, message_id, published_at, ticker, company_name, asset_class,
             detection, stance, signal_level, evidence_type, summary_text, excerpt, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, message_id, ticker) DO UPDATE SET
                company_name=excluded.company_name, asset_class=excluded.asset_class, detection=excluded.detection,
                stance=excluded.stance, signal_level=excluded.signal_level, evidence_type=excluded.evidence_type, summary_text=excluded.summary_text,
                excerpt=excluded.excerpt, raw_text=excluded.raw_text, updated_at=CURRENT_TIMESTAMP""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def persist_results(result: dict[str, Any], sec_master: dict[str, str], source_key: str, source_name: str, source_type: str) -> None:
    """Persist to the API database; retain a SQLite fallback for standalone use."""
    if POSTGRES_URL:
        _persist_results_postgres(result, sec_master, source_key, source_name, source_type)
        return
    _persist_results_sqlite(result, sec_master, source_key, source_name, source_type)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticker-first US biotech review for Telegram Trillion")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--source-key", default="telegram:trillion")
    parser.add_argument("--source-name", default="트릴리온")
    parser.add_argument("--source-type", default="telegram")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    parser.add_argument("--limit", type=int, default=0, help="0 scans full history")
    parser.add_argument("--refresh-sec-master", action="store_true")
    args = parser.parse_args()
    sec_master = load_sec_master(args.refresh_sec_master)
    result = asyncio.run(collect(args.channel, args.session, sec_master, args.limit))
    json_path, _ = save_results(result)
    # The dashboard can serve this durable external-SSD snapshot while the
    # PostgreSQL mirror is unavailable or being migrated.
    try:
        persist_results(result, sec_master, args.source_key, args.source_name, args.source_type)
    except Exception as exc:
        print(f"[warning] PostgreSQL source mirror skipped: {exc}")
    print(json.dumps({
        "messages_scanned": result["messages_scanned"],
        "known_biotech_symbols": result["known_biotech_symbols"],
        "review_queue_symbols": result["review_queue_symbols"],
        "output": str(json_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
