"""
collectors/dart_equity_issue_collector.py

DART 유상증자/무상증자/유무상증자 결정 공시를 파싱해 희석 이벤트와
corporate action 이벤트로 저장한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_utils import connect_stock_db
from collectors.dart_backlog_collector import _fetch_document_with_key_rotation

logger = logging.getLogger(__name__)
PARSER_VERSION = "equity_issue_v1"


def _execute_with_retry(conn: sqlite3.Connection, sql: str, params: tuple | list = (), attempts: int = 8):
    delay = 0.5
    for attempt in range(attempts):
        try:
            return conn.execute(sql, params)
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 6.0)


def _commit_with_retry(conn: sqlite3.Connection, attempts: int = 8) -> None:
    delay = 0.5
    for attempt in range(attempts):
        try:
            conn.commit()
            return
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 6.0)


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=120)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dilution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                event_type TEXT NOT NULL,
                disclosed_at TEXT,
                rcept_no TEXT UNIQUE,
                issue_amount REAL,
                conversion_price REAL,
                shares_to_issue REAL,
                current_shares REAL,
                dilution_pct REAL,
                report_nm TEXT,
                data_source TEXT DEFAULT 'dart_dilution',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_de_code ON dilution_events(stock_code, disclosed_at)")
        try:
            conn.execute("ALTER TABLE dilution_events ADD COLUMN put_option_date TEXT")
        except Exception:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_equity_issue_events (
                rcept_no TEXT PRIMARY KEY,
                stock_code TEXT,
                corp_name TEXT,
                rcept_dt TEXT,
                report_nm TEXT,
                event_type TEXT,
                issue_method TEXT,
                issue_amount_krw REAL,
                issue_price REAL,
                new_shares REAL,
                old_shares REAL,
                dilution_ratio_pct REAL,
                record_date TEXT,
                listing_date TEXT,
                confidence REAL DEFAULT 0,
                source_excerpt TEXT,
                source_text_hash TEXT,
                parser_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deie_code_dt ON dart_equity_issue_events(stock_code, rcept_dt DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                old_shares REAL,
                new_shares REAL,
                share_ratio REAL,
                backward_price_factor REAL,
                evidence_report_name TEXT,
                evidence_rcept_no TEXT,
                evidence_url TEXT,
                source TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                adjustment_status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(stock_code, event_date, event_type)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cae_code_date ON corporate_action_events(stock_code, event_date)")
        conn.commit()
    finally:
        conn.close()


def _to_num(value: str | None) -> Optional[float]:
    if not value:
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(value).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def _normalize_date(value: str | None) -> str:
    s = re.sub(r"\D", "", str(value or ""))
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(value or "")


def _extract_date(text: str, keyword: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", text or "")
    pat = r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?"
    for km in re.finditer(keyword, t, re.IGNORECASE):
        window = t[km.end():km.end() + 180]
        m = re.search(pat, window)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _plausible_event_date(value: Optional[str], disclosed_at: str) -> Optional[str]:
    if not value:
        return None
    try:
        event_dt = datetime.strptime(value, "%Y-%m-%d")
        disc_dt = datetime.strptime(disclosed_at, "%Y-%m-%d")
    except Exception:
        return None
    # 유/무상증자 기준일·상장예정일은 보통 공시일 전후 수개월이다.
    # 먼 미래 숫자(예: 법조문/오탈자 2052년)를 이벤트 일자로 쓰지 않는다.
    if event_dt < disc_dt.replace(year=disc_dt.year - 1):
        return None
    if event_dt > disc_dt.replace(year=disc_dt.year + 3):
        return None
    return value


def _extract_labeled_num(text: str, keyword: str, window: int = 180, prefer: str = "first") -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    cands: list[tuple[float, str]] = []
    for km in re.finditer(keyword, t, re.IGNORECASE):
        w = t[km.end():km.end() + window]
        # 표에서 '-'는 값 없음이므로, 다음 숫자만 후보로 본다.
        for m in re.finditer(r"(?<![A-Za-z])(-?[\d,]+(?:\.\d+)?)", w):
            v = _to_num(m.group(1))
            if v is None:
                continue
            cands.append((v, t[max(0, km.start() - 50):min(len(t), km.end() + window)]))
            break
    if not cands:
        return None, ""
    return cands[-1] if prefer == "last" else cands[0]


def _extract_share_pair(text: str, keyword: str) -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    for km in re.finditer(keyword, t, re.IGNORECASE):
        w = t[km.end():km.end() + 240]
        common = None
        other = None
        m1 = re.search(r"보통주식?\s*\(주\)\s*(-|[\d,]+(?:\.\d+)?)", w)
        if m1 and m1.group(1) != "-":
            common = _to_num(m1.group(1))
        m2 = re.search(r"기타주식?\s*\(주\)\s*(-|[\d,]+(?:\.\d+)?)", w)
        if m2 and m2.group(1) != "-":
            other = _to_num(m2.group(1))
        if common is not None or other is not None:
            return float(common or 0) + float(other or 0), t[max(0, km.start() - 50):min(len(t), km.end() + 240)]
        nums = [_to_num(x) for x in re.findall(r"[\d,]+(?:\.\d+)?", w)]
        nums = [x for x in nums if x is not None and x > 1000]
        if nums:
            return nums[0], t[max(0, km.start() - 50):min(len(t), km.end() + 240)]
    return None, ""


def _extract_issue_amount(text: str) -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    labels = ["시설자금", "영업양수자금", "운영자금", "채무상환자금", "타법인 증권취득자금", "기타자금"]
    total = 0.0
    excerpts = []
    for label in labels:
        v, ex = _extract_labeled_num(t, re.escape(label) + r"\s*\(원\)", window=80)
        if v and v > 0:
            total += v
            excerpts.append(ex[:120])
    if total > 0:
        return total, " | ".join(excerpts)[:800]
    return _extract_labeled_num(t, r"모집(?:또는\s*매출)?총액|발행총액|납입총액|청약금액", window=160)


def _classify(report_nm: str) -> str:
    name = report_nm or ""
    if "유무상증자" in name:
        return "RIGHTS_BONUS"
    if "무상증자" in name:
        return "BONUS"
    return "RIGHTS"


def _issue_method(text: str, report_nm: str) -> str:
    compact = re.sub(r"\s+", "", f"{report_nm} {text[:1200]}")
    for label in ["제3자배정", "주주배정후실권주일반공모", "주주배정", "일반공모", "공모", "출자전환"]:
        if label in compact:
            return label
    return ""


def _should_collect_report(report_nm: str) -> bool:
    n = re.sub(r"\s+", "", report_nm or "")
    if "종속회사" in n or "자회사" in n:
        return False
    if "첨부정정" in n:
        return True
    return (
        "주요사항보고서(유상증자결정)" in n
        or "주요사항보고서(무상증자결정)" in n
        or "주요사항보고서(유무상증자결정)" in n
        or n == "유상증자결정"
        or n == "무상증자결정"
    )


def collect_equity_issue_events(
    since: str = "2020-01-01",
    limit: Optional[int] = None,
    missing_only: bool = False,
    parse_docs: bool = True,
) -> dict:
    _ensure_table()
    since_compact = re.sub(r"\D", "", since)[:8] or "20200101"
    conn = connect_stock_db(timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    try:
        limit_sql = "LIMIT ?" if limit else ""
        rows = conn.execute(
            """
            SELECT stock_code, corp_name, rcept_no, rcept_dt, report_nm, dart_url
            FROM dart_disclosures
            WHERE replace(rcept_dt, '-', '') >= ?
              AND (
                report_nm LIKE '%주요사항보고서(유상증자결정)%'
                OR report_nm LIKE '%주요사항보고서(무상증자결정)%'
                OR report_nm LIKE '%주요사항보고서(유무상증자결정)%'
                OR replace(report_nm, ' ', '') IN ('유상증자결정', '무상증자결정', '유무상증자결정')
              )
              AND report_nm NOT LIKE '%종속회사%'
              AND report_nm NOT LIKE '%자회사%'
              AND (
                ? = 0 OR NOT EXISTS (
                  SELECT 1 FROM dart_equity_issue_events e
                  WHERE e.rcept_no = dart_disclosures.rcept_no
                    AND e.parser_version = ?
                )
              )
            ORDER BY rcept_dt DESC
            """ + f"\n{limit_sql}",
            [since_compact, 1 if missing_only else 0, PARSER_VERSION] + ([int(limit)] if limit else []),
        ).fetchall()

        candidates = [r for r in rows if _should_collect_report(r["report_nm"])]
        saved = 0
        parsed = 0
        errors = 0
        skipped = len(rows) - len(candidates)
        now = datetime.now().isoformat(timespec="seconds")

        for r in candidates:
            sc = r["stock_code"]
            cn = r["corp_name"]
            rno = r["rcept_no"]
            rdt = _normalize_date(r["rcept_dt"])
            rnm = r["report_nm"]
            event_type = _classify(rnm)
            ca_type = "bonus_issue" if event_type == "BONUS" else "rights_issue"
            try:
                text = ""
                if parse_docs:
                    text = _fetch_document_with_key_rotation(rno) or ""
                    if text == "020" or text.startswith("020"):
                        text = ""
                t = re.sub(r"\s+", " ", text)
                if "철회" in t[:900] and ("발행결정철회" in re.sub(r"\s+", "", t[:900]) or "전항목" in t[:300]):
                    confidence = 0.35
                    note = "withdrawn_or_cancelled_notice"
                else:
                    confidence = 0.45
                    note = ""

                new_shares, ex_shares = _extract_share_pair(t, r"신주의\s*종류와\s*수|신주\s*발행주식수|발행할\s*주식")
                old_shares, ex_old = _extract_share_pair(t, r"증자전\s*발행주식총수|증자\s*전\s*발행주식총수")
                issue_price, ex_price = _extract_labeled_num(t, r"신주\s*발행가액\s*보통주식\s*\(원\)|발행가액\s*보통주식\s*\(원\)", window=100)
                if event_type == "BONUS":
                    issue_price = None
                    issue_amount = None
                else:
                    issue_amount, ex_amount = _extract_issue_amount(t)
                    if (not issue_amount) and new_shares and issue_price:
                        issue_amount = new_shares * issue_price
                        ex_amount = "신주수 x 발행가액으로 모집금액 추정"

                record_date = _plausible_event_date(_extract_date(t, r"신주배정기준일|배정기준일"), rdt)
                listing_date = _plausible_event_date(_extract_date(t, r"신주의\s*상장\s*예정일|상장예정일"), rdt)
                method = _issue_method(t, rnm)
                dilution = new_shares / old_shares * 100.0 if new_shares and old_shares else None
                share_ratio = (old_shares + new_shares) / old_shares if new_shares and old_shares else None
                backward_factor = 1.0 / share_ratio if event_type == "BONUS" and share_ratio else None
                if any(v is not None for v in [new_shares, old_shares, issue_price, issue_amount]):
                    confidence = max(confidence, 0.75)
                    parsed += 1
                if event_type == "BONUS" and record_date and new_shares:
                    confidence = max(confidence, 0.85)

                excerpt = " | ".join(x for x in [ex_shares, ex_old, ex_price, locals().get("ex_amount", "")] if x)[:800]
                text_hash = hashlib.sha1(t.encode("utf-8", errors="ignore")).hexdigest() if t else None

                _execute_with_retry(
                    conn,
                    """
                    INSERT INTO dilution_events(
                        stock_code, stock_name, event_type, disclosed_at, rcept_no,
                        issue_amount, conversion_price, shares_to_issue, current_shares,
                        dilution_pct, report_nm, data_source, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(rcept_no) DO UPDATE SET
                        stock_code=excluded.stock_code,
                        stock_name=excluded.stock_name,
                        event_type=excluded.event_type,
                        disclosed_at=excluded.disclosed_at,
                        issue_amount=excluded.issue_amount,
                        conversion_price=excluded.conversion_price,
                        shares_to_issue=excluded.shares_to_issue,
                        current_shares=excluded.current_shares,
                        dilution_pct=excluded.dilution_pct,
                        report_nm=excluded.report_nm,
                        data_source='dart_equity_issue',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        sc, cn, event_type, rdt, rno, issue_amount, issue_price, new_shares,
                        old_shares, dilution, rnm, "dart_equity_issue",
                    ),
                )

                _execute_with_retry(
                    conn,
                    """
                    INSERT INTO dart_equity_issue_events(
                        rcept_no, stock_code, corp_name, rcept_dt, report_nm, event_type,
                        issue_method, issue_amount_krw, issue_price, new_shares, old_shares,
                        dilution_ratio_pct, record_date, listing_date, confidence,
                        source_excerpt, source_text_hash, parser_version, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(rcept_no) DO UPDATE SET
                        stock_code=excluded.stock_code,
                        corp_name=excluded.corp_name,
                        rcept_dt=excluded.rcept_dt,
                        report_nm=excluded.report_nm,
                        event_type=excluded.event_type,
                        issue_method=excluded.issue_method,
                        issue_amount_krw=excluded.issue_amount_krw,
                        issue_price=excluded.issue_price,
                        new_shares=excluded.new_shares,
                        old_shares=excluded.old_shares,
                        dilution_ratio_pct=excluded.dilution_ratio_pct,
                        record_date=excluded.record_date,
                        listing_date=excluded.listing_date,
                        confidence=excluded.confidence,
                        source_excerpt=excluded.source_excerpt,
                        source_text_hash=excluded.source_text_hash,
                        parser_version=excluded.parser_version,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        rno, sc, cn, rdt, rnm, event_type, method, issue_amount, issue_price,
                        new_shares, old_shares, dilution, record_date, listing_date, confidence,
                        excerpt, text_hash, PARSER_VERSION,
                    ),
                )

                action_date = record_date or listing_date or rdt
                _execute_with_retry(
                    conn,
                    """
                    INSERT INTO corporate_action_events(
                        stock_code,event_date,event_type,old_shares,new_shares,share_ratio,
                        backward_price_factor,evidence_report_name,evidence_rcept_no,evidence_url,
                        source,confidence,adjustment_status,note,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(stock_code,event_date,event_type) DO UPDATE SET
                        old_shares=COALESCE(excluded.old_shares, corporate_action_events.old_shares),
                        new_shares=COALESCE(excluded.new_shares, corporate_action_events.new_shares),
                        share_ratio=COALESCE(excluded.share_ratio, corporate_action_events.share_ratio),
                        backward_price_factor=COALESCE(excluded.backward_price_factor, corporate_action_events.backward_price_factor),
                        evidence_report_name=excluded.evidence_report_name,
                        evidence_rcept_no=excluded.evidence_rcept_no,
                        evidence_url=excluded.evidence_url,
                        source=excluded.source,
                        confidence=CASE
                            WHEN COALESCE(corporate_action_events.confidence, 0) >= COALESCE(excluded.confidence, 0)
                            THEN corporate_action_events.confidence ELSE excluded.confidence
                        END,
                        adjustment_status=excluded.adjustment_status,
                        note=excluded.note,
                        updated_at=excluded.updated_at
                    """,
                    (
                        sc, action_date, ca_type, old_shares, (old_shares + new_shares) if old_shares and new_shares else new_shares,
                        share_ratio, backward_factor, rnm, rno, r["dart_url"], "DART_equity_issue",
                        confidence, "factor_confirmed" if backward_factor else "review_required", note, now, now,
                    ),
                )

                saved += 1
                if saved % 100 == 0:
                    _commit_with_retry(conn)
            except Exception:
                errors += 1
                logger.exception("[EquityIssue] parse failed %s", rno)

        _commit_with_retry(conn)
        return {
            "source_rows": len(rows),
            "candidates": len(candidates),
            "skipped": skipped,
            "saved": saved,
            "parsed": parsed,
            "errors": errors,
            "parser_version": PARSER_VERSION,
            "since": since,
            "limit": limit,
            "missing_only": missing_only,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART 유상증자/무상증자 이벤트 수집")
    ap.add_argument("--since", default="2020-01-01")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--missing-only", action="store_true")
    ap.add_argument("--no-parse-docs", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(json.dumps(
        collect_equity_issue_events(
            since=args.since,
            limit=args.limit,
            missing_only=args.missing_only,
            parse_docs=not args.no_parse_docs,
        ),
        ensure_ascii=False,
        indent=2,
    ))
