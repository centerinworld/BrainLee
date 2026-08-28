"""
collectors/dart_dilution_collector.py

CB/BW(전환사채/신주인수권부사채) 공시를 파싱해 희석 이벤트를 저장.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_utils import connect_stock_db
from collectors.dart_backlog_collector import _fetch_document_with_key_rotation

logger = logging.getLogger(__name__)
PARSER_VERSION = "dilution_v1"


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=60)
    try:
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_dilution_events (
                rcept_no TEXT PRIMARY KEY,
                stock_code TEXT,
                corp_name TEXT,
                rcept_dt TEXT,
                report_nm TEXT,
                instrument_type TEXT,
                issue_amount_krw REAL,
                conversion_price REAL,
                exercise_price REAL,
                maturity_date TEXT,
                potential_shares REAL,
                dilution_ratio_pct REAL,
                confidence REAL DEFAULT 0,
                source_excerpt TEXT,
                source_text_hash TEXT,
                parser_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dart_dilution_code_dt ON dart_dilution_events(stock_code, rcept_dt DESC)")
        conn.commit()
    finally:
        conn.close()


def _to_num(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(s).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None


def _extract_metric(text: str, keyword: str) -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    p = rf"{keyword}[^\d-]{{0,30}}(-?[\d,]+(?:\.\d+)?)\s*(조원|억원|백만원|천만원|만원|원)?"
    cands = []
    for m in re.finditer(p, t, re.IGNORECASE):
        v = _to_num(m.group(1))
        if v is None:
            continue
        unit = (m.group(2) or "원").strip()
        mult = {"조원": 1_0000_0000_0000, "억원": 100_000_000, "백만원": 1_000_000, "천만원": 10_000_000, "만원": 10_000, "원": 1}.get(unit, 1)
        excerpt = t[max(0, m.start()-40):min(len(t), m.end()+40)]
        cands.append((v * mult, excerpt))
    if not cands:
        return None, ""
    cands.sort(key=lambda x: abs(x[0]), reverse=True)
    return cands[0]


def collect_dilution_events(days: int = 365) -> dict:
    _ensure_table()

    conn = connect_stock_db(timeout=60)
    conn.row_factory = __import__("sqlite3").Row
    try:
        rows = conn.execute(
            """
            SELECT stock_code, corp_name, rcept_no, rcept_dt, report_nm
            FROM dart_disclosures
            WHERE rcept_dt >= strftime('%Y%m%d', 'now', ?)
              AND (
                report_nm LIKE '%전환사채권발행결정%'
                OR report_nm LIKE '%신주인수권부사채권발행결정%'
                OR report_nm LIKE '%교환사채권발행결정%'
              )
            ORDER BY rcept_dt DESC
            """,
            (f"-{int(days)} day",),
        ).fetchall()

        saved = 0
        errs = 0
        for r in rows:
            sc = r["stock_code"]
            cn = r["corp_name"]
            rno = r["rcept_no"]
            rdt = r["rcept_dt"]
            rnm = r["report_nm"]
            try:
                txt = _fetch_document_with_key_rotation(rno)
                if not txt:
                    continue
                t = re.sub(r"\s+", " ", txt)
                issue_amt, ex_issue = _extract_metric(t, "발행금액|권면총액|사채총액")
                conv_px, ex_conv = _extract_metric(t, "전환가액|전환가격")
                ex_px, ex_ex = _extract_metric(t, "행사가액|행사가격")
                p_shr, ex_shr = _extract_metric(t, "전환에 따라 발행할 주식수|행사주식수|발행예정주식수")

                # 기존 발행주식수 대비 희석률 추정
                su = conn.execute(
                    "SELECT shares_issued FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
                    (sc,),
                ).fetchone()
                issued = float(su[0]) if su and su[0] not in (None, "") else None
                dilution = (p_shr / issued * 100.0) if (p_shr not in (None, 0) and issued not in (None, 0)) else None

                ins = "CB"
                if "신주인수권부사채" in (rnm or ""):
                    ins = "BW"
                elif "교환사채" in (rnm or ""):
                    ins = "EB"

                excerpt = " | ".join(x for x in [ex_issue, ex_conv, ex_ex, ex_shr] if x)[:800]
                conf = 0.4 + 0.15 * sum(v is not None for v in [issue_amt, conv_px, ex_px, p_shr])
                conf = min(conf, 0.95)
                conn.execute(
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
                        data_source='dart_dilution',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        sc, cn, ins, rdt, rno,
                        issue_amt, conv_px if conv_px is not None else ex_px,
                        p_shr, issued, dilution, rnm, "dart_dilution",
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO dart_dilution_events(
                        rcept_no, stock_code, corp_name, rcept_dt, report_nm, instrument_type,
                        issue_amount_krw, conversion_price, exercise_price, maturity_date,
                        potential_shares, dilution_ratio_pct, confidence, source_excerpt,
                        source_text_hash, parser_version, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(rcept_no) DO UPDATE SET
                        stock_code=excluded.stock_code,
                        corp_name=excluded.corp_name,
                        rcept_dt=excluded.rcept_dt,
                        report_nm=excluded.report_nm,
                        instrument_type=excluded.instrument_type,
                        issue_amount_krw=excluded.issue_amount_krw,
                        conversion_price=excluded.conversion_price,
                        exercise_price=excluded.exercise_price,
                        maturity_date=excluded.maturity_date,
                        potential_shares=excluded.potential_shares,
                        dilution_ratio_pct=excluded.dilution_ratio_pct,
                        confidence=excluded.confidence,
                        source_excerpt=excluded.source_excerpt,
                        source_text_hash=excluded.source_text_hash,
                        parser_version=excluded.parser_version,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        rno, sc, cn, rdt, rnm, ins,
                        issue_amt, conv_px, ex_px, None,
                        p_shr, dilution, conf, excerpt,
                        hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest(),
                        PARSER_VERSION,
                    ),
                )
                saved += 1
            except Exception:
                errs += 1
                logger.exception("[Dilution] parse failed %s", rno)

        conn.commit()
        return {"candidates": len(rows), "saved": saved, "errors": errs, "parser_version": PARSER_VERSION}
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART CB/BW 희석 이벤트 수집")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()
    print(json.dumps(collect_dilution_events(days=args.days), ensure_ascii=False, indent=2))
