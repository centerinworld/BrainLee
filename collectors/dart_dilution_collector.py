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
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_utils import connect_stock_db
from collectors.dart_backlog_collector import _fetch_document_with_key_rotation

logger = logging.getLogger(__name__)
PARSER_VERSION = "dilution_v2"


def _execute_with_retry(conn, sql: str, params: tuple | list = (), attempts: int = 8):
    delay = 0.5
    for attempt in range(attempts):
        try:
            return conn.execute(sql, params)
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 6.0)


def _commit_with_retry(conn, attempts: int = 8) -> None:
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
        # 2026-07-19 신규 — 사용자 지시(에이엘티 CB 200억 풋옵션 vs 현금 77.5억 사례): 조기상환청구권
        # (put option) 개시일이 있어야 "다가오는 상환 vs 보유현금" 유동성 리스크를 계산할 수 있음.
        for col, ddl in [
            ("put_option_date", "ALTER TABLE dilution_events ADD COLUMN put_option_date TEXT"),
            ("put_option_date", "ALTER TABLE dart_dilution_events ADD COLUMN put_option_date TEXT"),
        ]:
            try:
                conn.execute(ddl)
            except Exception:
                pass  # 컬럼이 이미 존재
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


def _normalize_disclosed_date(value: str) -> str:
    s = re.sub(r"\D", "", str(value or ""))
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(value or "")


def _extract_date(text: str, keyword: str) -> Optional[str]:
    """키워드(예: 조기상환청구권/풋옵션) 뒤 240자 내 첫 날짜를 YYYY-MM-DD로 반환.
    사용자 사례(에이엘티 172670): "전환청구 개시 2026.12.13" — 사채 발행 후 약 2~3년 뒤
    조기상환청구권(put option) 개시일이 명시되는 것이 KOSDAQ CB의 일반적 구조."""
    t = re.sub(r"\s+", " ", text or "")
    date_pat = r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?"
    for km in re.finditer(keyword, t, re.IGNORECASE):
        window = t[km.end():km.end() + 240]
        dm = re.search(date_pat, window)
        if dm:
            y, mo, d = dm.groups()
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except Exception:
                continue
    return None


def _extract_metric(text: str, keyword: str, prefer: str = "max") -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    p = rf"(?:{keyword})[^\d-]{{0,180}}(-?[\d,]+(?:\.\d+)?)\s*(조원|억원|백만원|천만원|만원|원)?"
    cands = []
    for m in re.finditer(p, t, re.IGNORECASE):
        v = _to_num(m.group(1))
        if v is None:
            continue
        before = t[max(0, m.start()-20):m.start()]
        after = t[m.end():min(len(t), m.end()+5)]
        if "%" in after or "100분의" in before:
            continue
        unit = (m.group(2) or "원").strip()
        mult = {"조원": 1_0000_0000_0000, "억원": 100_000_000, "백만원": 1_000_000, "천만원": 10_000_000, "만원": 10_000, "원": 1}.get(unit, 1)
        excerpt = t[max(0, m.start()-40):min(len(t), m.end()+40)]
        cands.append((v * mult, excerpt))
    if not cands:
        return None, ""
    if prefer == "last":
        return cands[-1]
    if prefer == "min":
        cands.sort(key=lambda x: abs(x[0]))
        return cands[0]
    cands.sort(key=lambda x: abs(x[0]), reverse=True)
    return cands[0]


def _extract_conversion_or_exercise_price(text: str, instrument: str) -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    if instrument == "BW":
        labels = r"행사가액|행사가격"
    elif instrument == "EB":
        labels = r"교환가액|교환가격"
    else:
        labels = r"전환가액|전환가격"
    cands: list[tuple[float, str]] = []

    # 정정공시 표: "전환가액 (원/주) ... 5,118 3,486" → 정정 후 값(마지막 숫자)
    for m in re.finditer(rf"(?:{labels})\s*\(원/주\)[^\d]{{0,120}}((?:[\d,]+\s+){{0,3}}[\d,]+)", t):
        nums = re.findall(r"[\d,]+", m.group(1))
        if nums:
            v = _to_num(nums[-1])
            if v and v > 100:
                cands.append((v, t[max(0, m.start()-40):min(len(t), m.end()+40)]))

    # 본문 설명: "전환가액: 1주당 ...(\3,486)"
    for m in re.finditer(rf"(?:{labels})\s*[:：][^\\d]{{0,120}}\\?([\d,]+)", t):
        v = _to_num(m.group(1))
        if v and v > 100:
            cands.append((v, t[max(0, m.start()-40):min(len(t), m.end()+40)]))

    if cands:
        return cands[-1]
    return _extract_metric(t, labels, prefer="last")


def collect_dilution_events(days: int = 365, limit: Optional[int] = None, missing_only: bool = False) -> dict:
    _ensure_table()

    conn = connect_stock_db(timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = __import__("sqlite3").Row
    try:
        limit_sql = "LIMIT ?" if limit else ""
        # 2026-08-23: strftime('%Y%m%d','now',?)는 SQLite 전용 — PostgreSQL 라우팅 하에서
        # "function strftime(...) does not exist"로 매번 실패하던 버그. 오프셋을
        # Python에서 미리 계산한 YYYYMMDD 리터럴로 바꿔 두 엔진 모두에서 동작하게 함.
        cutoff_ymd = (datetime.now() - timedelta(days=int(days))).strftime("%Y%m%d")
        params: list = [cutoff_ymd]
        if limit:
            params.append(int(limit))
        rows = conn.execute(
            """
            SELECT stock_code, corp_name, rcept_no, rcept_dt, report_nm
            FROM dart_disclosures
            WHERE replace(rcept_dt, '-', '') >= ?
              AND (
                report_nm LIKE '%전환사채권발행결정%'
                OR report_nm LIKE '%신주인수권부사채권발행결정%'
                OR report_nm LIKE '%교환사채권발행결정%'
              )
              AND (
                ? = 0 OR NOT EXISTS (
                  SELECT 1
                  FROM dilution_events de
                  WHERE de.rcept_no = dart_disclosures.rcept_no
                    AND de.issue_amount IS NOT NULL AND de.issue_amount > 0
                    AND (
                      de.conversion_price IS NOT NULL OR de.shares_to_issue IS NOT NULL
                    )
                )
              )
              AND (
                ? = 0 OR NOT EXISTS (
                  SELECT 1
                  FROM dart_dilution_events dde
                  WHERE dde.rcept_no = dart_disclosures.rcept_no
                    AND dde.parser_version = ?
                )
              )
            ORDER BY rcept_dt DESC
            """ + f"\n{limit_sql}",
            [cutoff_ymd, 1 if missing_only else 0, 1 if missing_only else 0, PARSER_VERSION] + ([int(limit)] if limit else []),
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
                issue_amt, ex_issue = _extract_metric(
                    t,
                    r"발행금액|권면\s*\(?전자등록\)?\s*총액|권면총액|사채(?:의\s*)?권면\s*\(?전자등록\)?\s*총액|사채총액",
                    prefer="max",
                )
                ins = "CB"
                if "신주인수권부사채" in (rnm or ""):
                    ins = "BW"
                elif "교환사채" in (rnm or ""):
                    ins = "EB"

                px, ex_px_text = _extract_conversion_or_exercise_price(t, ins)
                conv_px, ex_conv = (px, ex_px_text) if ins != "BW" else (None, "")
                ex_px, ex_ex = (px, ex_px_text) if ins == "BW" else (None, "")
                p_shr, ex_shr = _extract_metric(
                    t,
                    r"전환에\s*따라\s*발행할\s*주식(?:\s*-\s*주식\s*수|\s*수)?|교환대상\s*종류[^주]{0,80}주식수|교환대상\s*주식수|행사주식수|발행예정주식수|발행할\s*주식\s*수",
                    prefer="last",
                )
                if p_shr is None and issue_amt and (conv_px or ex_px):
                    price = conv_px if conv_px is not None else ex_px
                    if price:
                        p_shr = issue_amt / price
                        ex_shr = ex_shr or "발행금액/전환·행사가액으로 잠재주식수 추정"

                # 조기상환청구권(put option) 개시일 — 2026-07-19 신규(사용자 사례: 에이엘티 172670
                # CB 200억, 풋옵션 개시 2026.12.13 vs 보유현금 77.5억 — 유동성 리스크 판단에 필수).
                put_date = _extract_date(t, r"조기상환청구권|매도청구권|풋옵션|Put\s*Option")

                # 기존 발행주식수 대비 희석률 추정
                su = _execute_with_retry(
                    conn,
                    "SELECT shares_issued FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
                    (sc,),
                ).fetchone()
                issued = float(su[0]) if su and su[0] not in (None, "") else None
                dilution = (
                    p_shr / issued * 100.0
                    if (ins != "EB" and p_shr not in (None, 0) and issued not in (None, 0))
                    else None
                )

                excerpt = " | ".join(x for x in [ex_issue, ex_conv, ex_ex, ex_shr] if x)[:800]
                conf = 0.4 + 0.15 * sum(v is not None for v in [issue_amt, conv_px, ex_px, p_shr])
                conf = min(conf, 0.95)
                _execute_with_retry(
                    conn,
                    """
                    INSERT INTO dilution_events(
                        stock_code, stock_name, event_type, disclosed_at, rcept_no,
                        issue_amount, conversion_price, shares_to_issue, current_shares,
                        dilution_pct, report_nm, data_source, put_option_date, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
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
                        put_option_date=excluded.put_option_date,
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        sc, cn, ins, _normalize_disclosed_date(rdt), rno,
                        issue_amt, conv_px if conv_px is not None else ex_px,
                        p_shr, issued, dilution, rnm, "dart_dilution", put_date,
                    ),
                )

                _execute_with_retry(
                    conn,
                    """
                    INSERT INTO dart_dilution_events(
                        rcept_no, stock_code, corp_name, rcept_dt, report_nm, instrument_type,
                        issue_amount_krw, conversion_price, exercise_price, maturity_date,
                        potential_shares, dilution_ratio_pct, confidence, source_excerpt,
                        source_text_hash, parser_version, put_option_date, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
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
                        put_option_date=excluded.put_option_date,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        rno, sc, cn, _normalize_disclosed_date(rdt), rnm, ins,
                        issue_amt, conv_px, ex_px, None,
                        p_shr, dilution, conf, excerpt,
                        hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest(),
                        PARSER_VERSION, put_date,
                    ),
                )
                saved += 1
                if saved % 100 == 0:
                    _commit_with_retry(conn)
            except Exception:
                errs += 1
                logger.exception("[Dilution] parse failed %s", rno)

        _commit_with_retry(conn)
        return {
            "candidates": len(rows),
            "saved": saved,
            "errors": errs,
            "parser_version": PARSER_VERSION,
            "days": int(days),
            "limit": limit,
            "missing_only": missing_only,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART CB/BW 희석 이벤트 수집")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--missing-only", action="store_true")
    args = ap.parse_args()
    print(json.dumps(collect_dilution_events(days=args.days, limit=args.limit, missing_only=args.missing_only), ensure_ascii=False, indent=2))
