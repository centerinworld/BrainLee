"""
collectors/dart_backlog_collector.py

정기보고서(사업/반기/분기) 원문에서 수주잔고(Backlog) 관련 값을 추출해
분기 단위 DB로 적재한다.

설계 원칙
- 별도 DB를 만들지 않고 stock.db 내 전용 테이블로 분리 저장
  (운영/백업/트랜잭션 관리 단일화 + 조인 용이)
- 원문 근거(rcept_no/report_nm/rcept_dt)와 parser_version 보존
- 동일 종목/연도/분기 중 최신 공시만 유효(UPSERT)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_utils import connect_stock_db
import config
from dart_key_manager import get_dart_api_keys

logger = logging.getLogger(__name__)

PARSER_VERSION = "backlog_v2"

# dart_contract_collector의 검증된 문서 fetch 로직 재사용
import collectors.dart_contract_collector as _dcc
import io, zipfile, requests as _requests

# 수주잔고는 문서 깊숙한 곳에 위치 → 8000자 제한 해제, 최대 300000자 사용
_DOC_MAX_CHARS = 300_000


def _fetch_document_full(rcept_no: str, api_key: str) -> str:
    """DART 원문 전체를 최대 _DOC_MAX_CHARS 자까지 가져온다 (ZIP 처리 포함)."""
    import re as _re
    url = "https://opendart.fss.or.kr/api/document.xml"
    try:
        resp = _requests.get(url, params={"crtfc_key": api_key, "rcept_no": rcept_no},
                             timeout=30)
        if resp.status_code != 200:
            return ""
        raw = resp.content
        # 한도 초과 체크 (JSON 오류 응답)
        if raw[:1] == b"{":
            if "020" in raw.decode("utf-8", errors="ignore"):
                return "020"
            return ""
        # ZIP 형식 (PK 헤더)
        if raw[:2] == b"PK":
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                texts = []
                for name in sorted(zf.namelist()):
                    with zf.open(name) as f:
                        try:
                            content = f.read().decode("utf-8", errors="ignore")
                        except Exception:
                            content = f.read().decode("euc-kr", errors="ignore")
                        content = _re.sub(r"<[^>]+>", " ", content)
                        content = _re.sub(r"&[a-zA-Z#0-9]+;", " ", content)
                        content = _re.sub(r"\s+", " ", content).strip()
                        texts.append(content)
                return " ".join(texts)[:_DOC_MAX_CHARS]
            except Exception as e:
                logger.warning("[Backlog] ZIP 해제 실패 %s: %s", rcept_no, e)
                return ""
        # XML/HTML 직접 처리
        text = raw.decode("utf-8", errors="ignore")
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:_DOC_MAX_CHARS]
    except Exception as e:
        logger.warning("[Backlog] 문서 fetch 실패 %s: %s", rcept_no, e)
        return ""


def _fetch_document_with_key_rotation(rcept_no: str) -> str:
    """3-key 라운드로빈으로 전체 문서(최대 300000자) 취득."""
    keys = get_dart_api_keys()
    tried = set()
    for k in keys:
        if not k or k in tried:
            continue
        tried.add(k)
        txt = _fetch_document_full(rcept_no, k)
        if txt == "020":
            continue  # 한도 초과 → 다음 키
        if txt:
            return txt
    return ""


@dataclass
class BacklogMetric:
    backlog_amount: Optional[float] = None
    backlog_unit: Optional[str] = None
    backlog_amount_krw: Optional[float] = None
    backlog_confidence: float = 0.0
    source_excerpt: str = ""


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=60)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_backlog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL,
                report_type TEXT,
                rcept_no TEXT,
                backlog_amount REAL,
                backlog_unit TEXT DEFAULT '원',
                backlog_normalized REAL,
                new_orders REAL,
                revenue_base REAL,
                backlog_to_rev REAL,
                data_source TEXT DEFAULT 'dart_backlog',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, year, quarter)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_backlog_code ON order_backlog(stock_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_backlog_year ON order_backlog(year, quarter)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_backlog_quarterly (
                stock_code TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'CFS',
                backlog_amount REAL,
                backlog_unit TEXT,
                backlog_amount_krw REAL,
                backlog_confidence REAL DEFAULT 0,
                source_excerpt TEXT,
                source_rcept_no TEXT,
                source_report_nm TEXT,
                source_rcept_dt TEXT,
                source_text_hash TEXT,
                parser_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, report_type)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dart_backlog_src ON dart_backlog_quarterly(source_rcept_no)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_tenbagger_triggers_quarterly (
                stock_code TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'CFS',
                metric_name TEXT NOT NULL,
                metric_value REAL,
                yoy_pct REAL,
                qoq_pct REAL,
                trigger_level TEXT,
                source_table TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, report_type, metric_name)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _korean_to_krw(value: float, unit: str) -> float:
    u = (unit or "원").strip()
    if u == "조원":
        return value * 1_0000_0000_0000
    if u == "억원":
        return value * 100_000_000
    if u == "백만원":
        return value * 1_000_000
    if u == "천만원":
        return value * 10_000_000
    if u == "만원":
        return value * 10_000
    return value


def _parse_num(s: str) -> Optional[float]:
    if s is None:
        return None
    t = str(s).replace(",", "").replace(" ", "").strip()
    if not t:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _extract_backlog(text: str) -> BacklogMetric:
    t = _normalize_ws(text)
    if not t:
        return BacklogMetric()

    # 1) 수주잔고 키워드 인접 값 (다양한 표현 포함)
    kw_group = (
        r"(?:수주\s*잔고|수주잔고|수주\s*잔액|수주잔액|계약\s*잔고|계약잔고"
        r"|미착공\s*수주잔고|미완성\s*공사|잔여\s*공사잔고|잔여공사잔고"
        r"|공사\s*잔고|용역\s*잔고|수주\s*금액\s*잔액"
        r"|order\s*backlog|backlog)"
    )
    patterns = [
        kw_group + r"[^\d-]{0,60}(-?[\d,]+(?:\.\d+)?)\s*(조원|억원|백만원|천만원|만원|원)",
        kw_group + r"[^\d-]{0,60}(-?[\d,]+(?:\.\d+)?)",
    ]

    cands: list[tuple[float, str, float, str]] = []
    for p in patterns:
        for m in re.finditer(p, t, re.IGNORECASE):
            raw_v = _parse_num(m.group(1))
            if raw_v is None:
                continue
            unit = m.group(2) if len(m.groups()) >= 2 else "원"
            krw = _korean_to_krw(raw_v, unit)
            # 신뢰도: 단위 명시 + 키워드 정확도 가중
            conf = 0.85 if len(m.groups()) >= 2 else 0.65
            excerpt = t[max(0, m.start()-40):min(len(t), m.end()+40)]
            cands.append((krw, unit, conf, excerpt))

    # 연도값·소액 필터 적용
    valid_cands = []
    for krw, unit, conf, excerpt in cands:
        if 1_990 <= abs(krw) <= 2_030:  # 연도값 제거
            continue
        if abs(krw) < 100_000:  # 10만원 미만 파싱 오류 제거
            continue
        valid_cands.append((krw, unit, conf, excerpt))

    if valid_cands:
        krw, unit, conf, excerpt = sorted(valid_cands, key=lambda x: abs(x[0]), reverse=True)[0]
        return BacklogMetric(
            backlog_amount=(krw / (_korean_to_krw(1, unit) or 1)),
            backlog_unit=unit,
            backlog_amount_krw=krw,
            backlog_confidence=conf,
            source_excerpt=excerpt,
        )

    return BacklogMetric()


def _infer_period(report_nm: str, rcept_dt: str) -> tuple[Optional[int], Optional[int]]:
    nm = report_nm or ""
    # 보고서명 내 기준기간 우선 사용: 2026.03 / 2026.06 / 2026.09 / 2025.12
    m = re.search(r"(20\d{2})[./-](0[1-9]|1[0-2])", nm)
    if m:
        y = int(m.group(1))
        mm = int(m.group(2))
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(mm)
        if q:
            return y, q

    # fallback: 접수일 + 보고서 유형
    try:
        y = int((rcept_dt or "")[:4])
        mm = int((rcept_dt or "")[4:6])
    except Exception:
        return None, None

    if "사업보고서" in nm:
        return y - 1, 4
    if "반기보고서" in nm:
        return y, 2
    if "분기보고서" in nm:
        return (y, 1) if mm <= 6 else (y, 3)
    return None, None


def _candidate_disclosures(year_from: int, year_to: int, limit: int | None) -> list[dict]:
    conn = connect_stock_db(timeout=60)
    conn.row_factory = __import__("sqlite3").Row
    try:
        rows = conn.execute(
            """
            SELECT stock_code, rcept_no, rcept_dt, report_nm, corp_name
            FROM dart_disclosures
            WHERE stock_code IS NOT NULL AND stock_code<>''
              AND (
                report_nm LIKE '%사업보고서%'
                OR report_nm LIKE '%반기보고서%'
                OR report_nm LIKE '%분기보고서%'
              )
              AND substr(rcept_dt,1,4) BETWEEN ? AND ?
            ORDER BY rcept_dt DESC, rcept_no DESC
            """,
            (str(year_from), str(year_to)),
        ).fetchall()
    finally:
        conn.close()

    # 동일 종목/연도/분기 최신 1건만 남김
    dedup: dict[tuple[str, int, int], dict] = {}
    for r in rows:
        fy, fq = _infer_period(r["report_nm"], r["rcept_dt"])
        if not fy or not fq:
            continue
        key = (r["stock_code"], fy, fq)
        if key not in dedup:
            dedup[key] = dict(r)
            dedup[key]["fiscal_year"] = fy
            dedup[key]["fiscal_quarter"] = fq

    out = list(dedup.values())
    out.sort(key=lambda x: (x["fiscal_year"], x["fiscal_quarter"], x["stock_code"]), reverse=True)
    if limit:
        out = out[: max(1, int(limit))]
    return out


def collect_backlog_quarterly(
    year_from: int = 2021,
    year_to: int = 2026,
    limit: int | None = None,
    report_type: str = "CFS",
) -> dict:
    _ensure_table()
    cands = _candidate_disclosures(year_from, year_to, limit)

    conn = connect_stock_db(timeout=60)
    try:
        ok = 0
        no_text = 0
        no_metric = 0
        errs = 0

        for i, row in enumerate(cands, start=1):
            stock_code = row["stock_code"]
            rcept_no = row["rcept_no"]
            report_nm = row["report_nm"]
            rcept_dt = row["rcept_dt"]
            corp_name = row.get("corp_name")
            fy = int(row["fiscal_year"])
            fq = int(row["fiscal_quarter"])

            try:
                raw = _fetch_document_with_key_rotation(rcept_no)
                if not raw:
                    no_text += 1
                    continue
                metric = _extract_backlog(raw)
                if metric.backlog_amount_krw is None:
                    no_metric += 1
                    continue

                text_hash = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO order_backlog(
                        stock_code, stock_name, year, quarter, report_type, rcept_no,
                        backlog_amount, backlog_unit, backlog_normalized, data_source, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                        stock_name=excluded.stock_name,
                        report_type=excluded.report_type,
                        rcept_no=excluded.rcept_no,
                        backlog_amount=excluded.backlog_amount,
                        backlog_unit=excluded.backlog_unit,
                        backlog_normalized=excluded.backlog_normalized,
                        data_source='dart_backlog',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        stock_code, corp_name, fy, fq, report_nm, rcept_no,
                        metric.backlog_amount_krw, "원", (metric.backlog_amount_krw / 1_000_000.0) if metric.backlog_amount_krw is not None else None, "dart_backlog",
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO dart_backlog_quarterly(
                        stock_code,fiscal_year,fiscal_quarter,report_type,
                        backlog_amount,backlog_unit,backlog_amount_krw,backlog_confidence,
                        source_excerpt,source_rcept_no,source_report_nm,source_rcept_dt,
                        source_text_hash,parser_version,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type) DO UPDATE SET
                        backlog_amount=excluded.backlog_amount,
                        backlog_unit=excluded.backlog_unit,
                        backlog_amount_krw=excluded.backlog_amount_krw,
                        backlog_confidence=excluded.backlog_confidence,
                        source_excerpt=excluded.source_excerpt,
                        source_rcept_no=excluded.source_rcept_no,
                        source_report_nm=excluded.source_report_nm,
                        source_rcept_dt=excluded.source_rcept_dt,
                        source_text_hash=excluded.source_text_hash,
                        parser_version=excluded.parser_version,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        stock_code, fy, fq, report_type,
                        metric.backlog_amount, metric.backlog_unit, metric.backlog_amount_krw, metric.backlog_confidence,
                        metric.source_excerpt, rcept_no, report_nm, rcept_dt,
                        text_hash, PARSER_VERSION,
                    ),
                )
                # backlog 변화율 트리거 적재
                _upsert_backlog_trigger(conn, stock_code, fy, fq, report_type)
                ok += 1
            except Exception:
                errs += 1
                logger.exception("[Backlog] parse failed %s %s", stock_code, rcept_no)

            if i % 20 == 0:
                conn.commit()
                logger.info("[Backlog] progress %s/%s ok=%s no_text=%s no_metric=%s err=%s", i, len(cands), ok, no_text, no_metric, errs)

        conn.commit()
        return {
            "ok": ok,
            "candidates": len(cands),
            "no_text": no_text,
            "no_metric": no_metric,
            "errors": errs,
            "year_from": year_from,
            "year_to": year_to,
            "report_type": report_type,
            "parser_version": PARSER_VERSION,
        }
    finally:
        conn.close()


def collect_order_backlog(
    year_from: int = 2021,
    year_to: int = 2026,
    limit: int | None = None,
    report_type: str = "CFS",
) -> dict:
    """지시서 호환 별칭."""
    return collect_backlog_quarterly(year_from=year_from, year_to=year_to, limit=limit, report_type=report_type)


def _upsert_backlog_trigger(conn, stock_code: str, fy: int, fq: int, report_type: str) -> None:
    rows = conn.execute(
        """
        SELECT fiscal_year, fiscal_quarter, backlog_amount_krw
        FROM dart_backlog_quarterly
        WHERE stock_code=? AND report_type=? AND backlog_amount_krw IS NOT NULL
        ORDER BY fiscal_year, fiscal_quarter
        """,
        (stock_code, report_type),
    ).fetchall()
    mp = {(int(r[0]), int(r[1])): float(r[2]) for r in rows}
    cur = mp.get((fy, fq))
    if cur is None:
        return
    prev_q = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
    prev_y = (fy - 1, fq)
    qv = mp.get(prev_q)
    yv = mp.get(prev_y)
    qoq = ((cur - qv) / abs(qv) * 100.0) if (qv not in (None, 0)) else None
    yoy = ((cur - yv) / abs(yv) * 100.0) if (yv not in (None, 0)) else None
    lvl = "BACKLOG_SURGE" if (yoy is not None and yoy >= 25.0) else None
    conn.execute(
        """
        INSERT INTO dart_tenbagger_triggers_quarterly(
            stock_code,fiscal_year,fiscal_quarter,report_type,
            metric_name,metric_value,yoy_pct,qoq_pct,trigger_level,source_table,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type,metric_name) DO UPDATE SET
            metric_value=excluded.metric_value,
            yoy_pct=excluded.yoy_pct,
            qoq_pct=excluded.qoq_pct,
            trigger_level=excluded.trigger_level,
            source_table=excluded.source_table,
            updated_at=CURRENT_TIMESTAMP
        """,
        (stock_code, fy, fq, report_type, "backlog", cur, yoy, qoq, lvl, "dart_backlog_quarterly"),
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART 수주잔고 분기 수집기")
    ap.add_argument("--year-from", type=int, default=2021)
    ap.add_argument("--year-to", type=int, default=datetime.now().year)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report-type", default="CFS")
    args = ap.parse_args()

    stats = collect_backlog_quarterly(
        year_from=args.year_from,
        year_to=args.year_to,
        limit=(args.limit or None),
        report_type=args.report_type,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
