"""
collectors/dart_cost_collector.py

정기보고서 원문에서 매입재료비/재고자산/감가상각비를 분기 단위로 추출한다.
추출값은 10배거 트리거 분석에 재사용할 수 있도록 표준 테이블에 저장한다.
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
from collectors.dart_backlog_collector import (
    _candidate_disclosures,
    _infer_period,
    _normalize_ws,
    _parse_num,
    _korean_to_krw,
    _fetch_document_with_key_rotation,
)

logger = logging.getLogger(__name__)
PARSER_VERSION = "cost_v1"


@dataclass
class CostMetric:
    material_cost_krw: Optional[float] = None
    inventory_assets_krw: Optional[float] = None
    depreciation_krw: Optional[float] = None
    confidence: float = 0.0
    excerpt: str = ""


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=60)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_structure (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL,
                raw_material_cost REAL,
                labor_cost REAL,
                overhead_cost REAL,
                total_cogs REAL,
                revenue REAL,
                raw_material_ratio REAL,
                cogs_ratio REAL,
                yoy_raw_material_chg REAL,
                data_source TEXT DEFAULT 'dart_cost',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, year, quarter)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_code ON cost_structure(stock_code)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_cost_quarterly (
                stock_code TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'CFS',
                material_cost_krw REAL,
                inventory_assets_krw REAL,
                depreciation_krw REAL,
                confidence REAL DEFAULT 0,
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dart_cost_src ON dart_cost_quarterly(source_rcept_no)")
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


def _pick_amount(text: str, keywords: list[str]) -> tuple[Optional[float], str]:
    t = _normalize_ws(text)
    if not t:
        return None, ""
    # 단위 명시 패턴 우선, 없으면 큰 숫자(5자리+)만 허용
    pattern_with_unit = r"(?:%s)[^\d-]{0,40}(-?[\d,]+(?:\.\d+)?)\s*(조원|억원|백만원|천만원|만원|원)" % "|".join(keywords)
    pattern_no_unit   = r"(?:%s)[^\d-]{0,40}(-?[\d,]{5,}(?:\.\d+)?)" % "|".join(keywords)
    cands: list[tuple[float, str]] = []
    for pattern in (pattern_with_unit, pattern_no_unit):
        for m in re.finditer(pattern, t, re.IGNORECASE):
            v = _parse_num(m.group(1))
            if v is None:
                continue
            unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "원").strip()
            krw = _korean_to_krw(v, unit)
            # 연도값(1990~2030) 또는 10만원 미만은 파싱 오류로 간주
            if 1_990 <= abs(krw) <= 2_030:
                continue
            if abs(krw) < 100_000:
                continue
            excerpt = t[max(0, m.start()-40):min(len(t), m.end()+40)]
            cands.append((krw, excerpt))
        if cands:
            break  # 단위 명시 패턴에서 찾으면 no_unit 패턴 불필요
    if not cands:
        return None, ""
    cands.sort(key=lambda x: abs(x[0]), reverse=True)
    return cands[0]


def _extract_cost_metrics(text: str) -> CostMetric:
    m_cost, ex1 = _pick_amount(text, ["재료비", "원재료비", r"원재료\s*매입", "매입재료비", r"재료\s*매입"])
    inv, ex2 = _pick_amount(text, ["재고자산", "상품재고", "제품재고", "원재료재고"])
    dep, ex3 = _pick_amount(text, ["감가상각비", "유형자산감가상각비", r"감가\s*상각"])

    non_null = sum(v is not None for v in [m_cost, inv, dep])
    conf = min(0.95, 0.45 + non_null * 0.15)
    excerpt = " | ".join([x for x in [ex1, ex2, ex3] if x])[:800]
    return CostMetric(
        material_cost_krw=m_cost,
        inventory_assets_krw=inv,
        depreciation_krw=dep,
        confidence=conf,
        excerpt=excerpt,
    )


def _upsert_trigger_rows(conn, stock_code: str, fy: int, fq: int, report_type: str) -> None:
    # metric별 시계열 변화율 계산 후 trigger 저장
    def _series(metric_col: str):
        rows = conn.execute(
            f"""
            SELECT fiscal_year, fiscal_quarter, {metric_col}
            FROM dart_cost_quarterly
            WHERE stock_code=? AND report_type=? AND {metric_col} IS NOT NULL
            ORDER BY fiscal_year, fiscal_quarter
            """,
            (stock_code, report_type),
        ).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    metrics = {
        "material_cost": _series("material_cost_krw"),
        "inventory_assets": _series("inventory_assets_krw"),
        "depreciation": _series("depreciation_krw"),
    }

    for mname, m in metrics.items():
        cur = m.get((fy, fq))
        if cur is None:
            continue
        prev_q = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
        prev_y = (fy - 1, fq)
        qv = m.get(prev_q)
        yv = m.get(prev_y)
        qoq = ((cur - qv) / abs(qv) * 100.0) if (qv not in (None, 0)) else None
        yoy = ((cur - yv) / abs(yv) * 100.0) if (yv not in (None, 0)) else None

        # 트리거 룰(초기 버전)
        level = None
        if mname == "inventory_assets":
            if yoy is not None and yoy >= 30:
                level = "WARN_INVENTORY_SURGE"
        elif mname == "material_cost":
            if yoy is not None and yoy >= 25:
                level = "WATCH_COST_INFLATION"
        elif mname == "depreciation":
            if yoy is not None and yoy >= 20:
                level = "CAPEX_RAMP_SIGNAL"

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
            (stock_code, fy, fq, report_type, mname, cur, yoy, qoq, level, "dart_cost_quarterly"),
        )


def collect_cost_quarterly(
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
            sc = row["stock_code"]
            rno = row["rcept_no"]
            rnm = row["report_nm"]
            rdt = row["rcept_dt"]
            corp_name = row.get("corp_name")
            fy = int(row["fiscal_year"])
            fq = int(row["fiscal_quarter"])

            try:
                raw = _fetch_document_with_key_rotation(rno)
                if not raw:
                    no_text += 1
                    continue
                m = _extract_cost_metrics(raw)
                if all(v is None for v in [m.material_cost_krw, m.inventory_assets_krw, m.depreciation_krw]):
                    no_metric += 1
                    continue

                text_hash = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO cost_structure(
                        stock_code, stock_name, year, quarter,
                        raw_material_cost, labor_cost, overhead_cost, total_cogs,
                        revenue, raw_material_ratio, cogs_ratio, yoy_raw_material_chg,
                        data_source, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                        stock_name=excluded.stock_name,
                        raw_material_cost=excluded.raw_material_cost,
                        labor_cost=excluded.labor_cost,
                        overhead_cost=excluded.overhead_cost,
                        total_cogs=excluded.total_cogs,
                        revenue=excluded.revenue,
                        raw_material_ratio=excluded.raw_material_ratio,
                        cogs_ratio=excluded.cogs_ratio,
                        yoy_raw_material_chg=excluded.yoy_raw_material_chg,
                        data_source='dart_cost',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        sc, corp_name, fy, fq,
                        m.material_cost_krw, None, None, None,
                        None, None, None, None,
                        "dart_cost",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO dart_cost_quarterly(
                        stock_code,fiscal_year,fiscal_quarter,report_type,
                        material_cost_krw,inventory_assets_krw,depreciation_krw,
                        confidence,source_excerpt,
                        source_rcept_no,source_report_nm,source_rcept_dt,
                        source_text_hash,parser_version,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type) DO UPDATE SET
                        material_cost_krw=excluded.material_cost_krw,
                        inventory_assets_krw=excluded.inventory_assets_krw,
                        depreciation_krw=excluded.depreciation_krw,
                        confidence=excluded.confidence,
                        source_excerpt=excluded.source_excerpt,
                        source_rcept_no=excluded.source_rcept_no,
                        source_report_nm=excluded.source_report_nm,
                        source_rcept_dt=excluded.source_rcept_dt,
                        source_text_hash=excluded.source_text_hash,
                        parser_version=excluded.parser_version,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        sc, fy, fq, report_type,
                        m.material_cost_krw, m.inventory_assets_krw, m.depreciation_krw,
                        m.confidence, m.excerpt,
                        rno, rnm, rdt,
                        text_hash, PARSER_VERSION,
                    ),
                )
                _upsert_trigger_rows(conn, sc, fy, fq, report_type)
                ok += 1
            except Exception:
                errs += 1
                logger.exception("[Cost] parse failed %s %s", sc, rno)

            if i % 100 == 0:
                conn.commit()
                logger.info("[Cost] progress %s/%s ok=%s no_text=%s no_metric=%s err=%s", i, len(cands), ok, no_text, no_metric, errs)

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

def collect_cost_structure(
    year_from: int = 2021,
    year_to: int = 2026,
    limit: int | None = None,
    report_type: str = "CFS",
) -> dict:
    """지시서 호환 별칭."""
    return collect_cost_quarterly(year_from=year_from, year_to=year_to, limit=limit, report_type=report_type)


def collect_cogs_from_dart_api(
    year_from: int = 2022,
    year_to: int = 2026,
    limit: int | None = None,
) -> dict:
    """DART fnlttSinglAcntAll.json API로 매출원가(COGS)를 직접 수집해 cost_structure에 저장.
    텍스트 파싱 방식보다 신뢰도 높음 — 재무제표 계정 직접 조회.
    """
    import sqlite3 as _sl, time as _t, requests as _rq
    from dart_key_manager import get_dart_api_keys
    DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
    DART_BASE = "https://opendart.fss.or.kr/api"
    KEYS = get_dart_api_keys()
    _exhausted: set = set()
    _ki = 0

    def _key():
        nonlocal _ki
        k = KEYS[_ki % len(KEYS)]
        _ki += 1
        return k

    def _next_valid_key() -> str | None:
        """현재 키가 한도 초과이면 다음 키로 교체. 전부 소진이면 None 반환."""
        nonlocal _ki
        for _ in range(len(KEYS)):
            candidate = KEYS[_ki % len(KEYS)]
            _ki += 1
            if candidate not in _exhausted:
                return candidate
        return None

    COGS_KW = ["매출원가", "제품매출원가", "상품매출원가", "영업비용"]
    REV_KW  = ["매출액", "영업수익", "수익(매출액)"]

    def _best(items, keywords):
        for kw in keywords:
            for item in items:
                if kw in (item.get("account_nm") or ""):
                    raw = (item.get("thstrm_amount") or "").replace(",", "")
                    try:
                        v = float(raw)
                        if abs(v) > 0:
                            return v
                    except Exception:
                        pass
        return None

    conn = _sl.connect(DB)
    conn.row_factory = _sl.Row
    # corp_code 조회
    corp_map = {}
    for row in conn.execute(
        "SELECT DISTINCT stock_code FROM stock_universe WHERE market IN ('KOSPI','KOSDAQ') AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' ORDER BY market_cap DESC NULLS LAST"
    ).fetchall():
        corp_map[row["stock_code"]] = None

    stocks = list(corp_map.keys())
    if limit:
        stocks = stocks[:limit]

    # CORPCODE.XML 캐시에서 corp_code 일괄 매핑 (API 호출 없이 빠름)
    import xml.etree.ElementTree as _ET, os as _os, zipfile as _zf
    _xml_path = "/tmp/CORPCODE.xml"
    if not _os.path.exists(_xml_path):
        try:
            resp = _rq.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": _key()}, timeout=30)
            with open("/tmp/CORPCODE.zip", "wb") as f:
                f.write(resp.content)
            with _zf.ZipFile("/tmp/CORPCODE.zip") as z:
                z.extractall("/tmp/")
        except Exception as e:
            logger.warning("[COGS] CORPCODE.xml 다운로드 실패: %s", e)
    if _os.path.exists(_xml_path):
        tree = _ET.parse(_xml_path)
        for item in tree.getroot():
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc and sc in corp_map:
                corp_map[sc] = cc
    logger.info("[COGS] corp_code 매핑 완료: %d/%d", sum(1 for v in corp_map.values() if v), len(stocks))

    RPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    ok = err = skip = 0

    for sc, corp_code in corp_map.items():
        if not corp_code:
            skip += 1
            continue
        for year in range(year_from, year_to + 1):
            for qtr in (4,):  # 연간만 (사업보고서)
                rprt = RPRT[qtr]
                for fs_div in ("CFS", "OFS"):
                    try:
                        resp = _rq.get(
                            f"{DART_BASE}/fnlttSinglAcntAll.json",
                            params={"crtfc_key": _key(), "corp_code": corp_code,
                                    "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                            timeout=12
                        )
                        _t.sleep(0.3)
                        data = resp.json()
                        if data.get("status") == "020":  # 한도 초과 → 다음 키 시도
                            used_key = KEYS[(_ki - 1) % len(KEYS)]
                            _exhausted.add(used_key)
                            logger.warning("[COGS] KEY %s...한도초과, 다음 키 시도 (%d/%d 소진)",
                                           used_key[:8], len(_exhausted), len(KEYS))
                            next_key = _next_valid_key()
                            if next_key is None:
                                logger.warning("[COGS] 전체 키 한도 초과 — 중단")
                                conn.commit()
                                conn.close()
                                return {"ok": ok, "err": err, "skip": skip, "stopped": "rate_limit"}
                            # 같은 요청을 다음 키로 재시도
                            resp = _rq.get(
                                f"{DART_BASE}/fnlttSinglAcntAll.json",
                                params={"crtfc_key": next_key, "corp_code": corp_code,
                                        "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                                timeout=12
                            )
                            _t.sleep(0.3)
                            data = resp.json()
                            if data.get("status") == "020":
                                _exhausted.add(next_key)
                                next_key2 = _next_valid_key()
                                if next_key2 is None:
                                    conn.commit(); conn.close()
                                    return {"ok": ok, "err": err, "skip": skip, "stopped": "rate_limit"}
                                resp = _rq.get(
                                    f"{DART_BASE}/fnlttSinglAcntAll.json",
                                    params={"crtfc_key": next_key2, "corp_code": corp_code,
                                            "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                                    timeout=12
                                )
                                _t.sleep(0.3)
                                data = resp.json()
                                if data.get("status") == "020":
                                    conn.commit(); conn.close()
                                    return {"ok": ok, "err": err, "skip": skip, "stopped": "rate_limit"}
                        if data.get("status") not in ("000",):
                            break
                        items = data.get("list", [])
                        cogs = _best(items, COGS_KW)
                        rev  = _best(items, REV_KW)
                        if cogs is None:
                            break
                        cogs_ratio = round(cogs / rev * 100, 2) if rev and rev > 0 else None
                        conn.execute(
                            """INSERT INTO cost_structure(stock_code, year, quarter,
                               total_cogs, revenue, cogs_ratio, data_source, collected_at)
                               VALUES(?,?,?,?,?,?,'dart_api',CURRENT_TIMESTAMP)
                               ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                               total_cogs=excluded.total_cogs, revenue=excluded.revenue,
                               cogs_ratio=excluded.cogs_ratio, data_source='dart_api',
                               collected_at=CURRENT_TIMESTAMP""",
                            (sc, year, qtr, cogs, rev, cogs_ratio)
                        )
                        ok += 1
                        break  # CFS 성공 시 OFS 불필요
                    except Exception as e:
                        err += 1
                        logger.debug("[COGS] %s %s %s: %s", sc, year, qtr, e)
        if ok % 200 == 0 and ok > 0:
            conn.commit()

    conn.commit()
    conn.close()
    return {"ok": ok, "err": err, "skip": skip}


def collect_cogs_quarters(
    year_from: int = 2023,
    year_to: int = 2026,
    quarters: tuple = (1, 2, 3),
    limit: int = 0,
) -> dict:
    """
    DART fnlttSinglAcntAll.json API로 분기(Q1/Q2/Q3) 매출원가·매출액 수집.
    연간(Q4)은 collect_cogs_from_dart_api가 담당하므로 여기서는 분기만 처리.
    """
    import sqlite3 as _sl, time as _t, requests as _rq
    from dart_key_manager import get_dart_api_keys
    import xml.etree.ElementTree as _ET, zipfile as _zf
    DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
    DART_BASE = "https://opendart.fss.or.kr/api"
    KEYS = get_dart_api_keys()
    _exhausted: set = set()
    _ki = 0

    def _next_key() -> str | None:
        nonlocal _ki
        for _ in range(len(KEYS)):
            k = KEYS[_ki % len(KEYS)]; _ki += 1
            if k not in _exhausted: return k
        return None

    COGS_KW = ["매출원가", "제품매출원가", "상품매출원가", "영업비용"]
    REV_KW  = ["매출액", "영업수익", "수익(매출액)"]
    # Q1=11013, H1(Q2)=11012, 9M(Q3)=11014
    RPRT = {1: "11013", 2: "11012", 3: "11014"}

    def _best(items, keywords):
        for kw in keywords:
            for item in items:
                if kw in (item.get("account_nm") or ""):
                    raw = (item.get("thstrm_amount") or "").replace(",", "")
                    try:
                        v = float(raw)
                        if abs(v) > 0: return v
                    except Exception: pass
        return None

    # corp_code 맵
    corp_map: dict[str, str | None] = {}
    conn = _sl.connect(DB, timeout=60); conn.row_factory = _sl.Row
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market_cap IS NOT NULL
        ORDER BY market_cap DESC{}
    """.format(f" LIMIT {limit}" if limit else "")).fetchall()
    for r in rows: corp_map[r[0]] = None
    conn.close()

    _xml_path = "/tmp/CORPCODE.xml"
    if not _os.path.exists(_xml_path):
        k = _next_key()
        if k:
            try:
                resp = _rq.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": k}, timeout=30)
                with open("/tmp/CORPCODE.zip","wb") as f: f.write(resp.content)
                with _zf.ZipFile("/tmp/CORPCODE.zip") as z: z.extractall("/tmp/")
            except Exception as e:
                logger.warning("[COGS_Q] CORPCODE 다운로드 실패: %s", e)
    if _os.path.exists(_xml_path):
        tree = _ET.parse(_xml_path)
        for item in tree.getroot():
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc and sc in corp_map: corp_map[sc] = cc

    stocks = [(sc, cc) for sc, cc in corp_map.items() if cc]
    ok = err = skip_no_corp = skip_exists = 0
    conn = _sl.connect(DB, timeout=60); conn.row_factory = _sl.Row
    conn.execute("PRAGMA journal_mode=WAL")

    total = len(stocks) * len(range(year_from, year_to+1)) * len(quarters)
    done = 0

    for sc, corp_code in stocks:
        for year in range(year_from, year_to + 1):
            for qtr in quarters:
                done += 1
                rprt = RPRT[qtr]
                saved = False
                for fs_div in ("CFS", "OFS"):
                    key = _next_key()
                    if not key:
                        conn.commit(); conn.close()
                        return {"ok": ok, "err": err, "skip_exists": skip_exists, "stopped": "rate_limit"}
                    try:
                        resp = _rq.get(
                            f"{DART_BASE}/fnlttSinglAcntAll.json",
                            params={"crtfc_key": key, "corp_code": corp_code,
                                    "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                            timeout=12
                        )
                        _t.sleep(0.25)
                        data = resp.json()
                        if data.get("status") == "020":
                            _exhausted.add(key)
                            key2 = _next_key()
                            if not key2:
                                conn.commit(); conn.close()
                                return {"ok": ok, "err": err, "skip_exists": skip_exists, "stopped": "rate_limit"}
                            resp = _rq.get(
                                f"{DART_BASE}/fnlttSinglAcntAll.json",
                                params={"crtfc_key": key2, "corp_code": corp_code,
                                        "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                                timeout=12
                            )
                            _t.sleep(0.25)
                            data = resp.json()
                        if data.get("status") != "000":
                            break
                        items = data.get("list", [])
                        cogs = _best(items, COGS_KW)
                        rev  = _best(items, REV_KW)
                        if cogs is None: break
                        # 누적값 → 분기값 변환 (H1/9M은 누적이므로 Q2=H1-Q1, Q3=9M-H1)
                        # 여기서는 일단 누적 그대로 저장(quarter=qtr로), 분기 역산은 별도 처리
                        cogs_ratio = round(cogs / rev * 100, 2) if rev and rev > 0 else None
                        conn.execute("""
                            INSERT INTO cost_structure(stock_code, year, quarter,
                               total_cogs, revenue, cogs_ratio, data_source, collected_at)
                            VALUES(?,?,?,?,?,?,'dart_api_q',CURRENT_TIMESTAMP)
                            ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                               total_cogs=excluded.total_cogs, revenue=excluded.revenue,
                               cogs_ratio=excluded.cogs_ratio, data_source='dart_api_q',
                               collected_at=CURRENT_TIMESTAMP
                        """, (sc, year, qtr, cogs, rev, cogs_ratio))
                        ok += 1; saved = True; break
                    except Exception as e:
                        err += 1
                        logger.debug("[COGS_Q] %s %s Q%s: %s", sc, year, qtr, e)
                if not saved and done % 500 == 0:
                    pass
        if ok % 300 == 0 and ok > 0:
            conn.commit()
            logger.info("[COGS_Q] 진행: %d/%d, ok=%d", done, total, ok)

    conn.commit(); conn.close()
    return {"ok": ok, "err": err, "skip_exists": skip_exists}


def collect_order_backlog_api(
    year_from: int = 2023,
    year_to: int = 2026,
    quarters: tuple = (1, 2, 3, 4),
    limit: int = 0,
) -> dict:
    """
    DART fnlttSinglAcntAll.json API로 수주잔고(order backlog) 수집.
    BS 계정에서 수주잔고 계정 추출.
    대상: 건설/방산/조선/플랜트 등 수주산업 위주이나 전종목 시도.
    """
    import sqlite3 as _sl, time as _t, requests as _rq
    from dart_key_manager import get_dart_api_keys
    import xml.etree.ElementTree as _ET, zipfile as _zf
    DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
    DART_BASE = "https://opendart.fss.or.kr/api"
    KEYS = get_dart_api_keys()
    _exhausted: set = set()
    _ki = 0

    def _next_key() -> str | None:
        nonlocal _ki
        for _ in range(len(KEYS)):
            k = KEYS[_ki % len(KEYS)]; _ki += 1
            if k not in _exhausted: return k
        return None

    # 수주잔고 관련 키워드
    BACKLOG_KW = ["수주잔고", "미완성공사금액", "공사미수금", "잔여수주", "수주액잔액",
                  "수주잔액", "미청구공사", "계약자산", "진행중인계약금액"]
    NEW_ORDER_KW = ["신규수주", "수주액", "당기수주"]
    RPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}

    def _best(items, keywords, partial=True):
        for kw in keywords:
            for item in items:
                nm = item.get("account_nm") or ""
                if (kw in nm) if partial else (kw == nm):
                    raw = (item.get("thstrm_amount") or "").replace(",", "")
                    try:
                        v = float(raw)
                        if abs(v) > 0: return v, nm
                    except Exception: pass
        return None, None

    # 테이블 생성
    conn_init = _sl.connect(DB, timeout=30)
    conn_init.execute("""
        CREATE TABLE IF NOT EXISTS order_backlog_api (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            backlog_amount REAL,
            new_order_amount REAL,
            backlog_account_nm TEXT,
            data_source TEXT DEFAULT 'dart_api',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, year, quarter)
        )
    """)
    conn_init.commit(); conn_init.close()

    # corp_code 맵
    corp_map: dict[str, tuple[str, str]] = {}
    conn = _sl.connect(DB, timeout=60); conn.row_factory = _sl.Row
    rows = conn.execute("""
        SELECT stock_code, stock_name FROM stock_universe
        WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market_cap IS NOT NULL
        ORDER BY market_cap DESC{}
    """.format(f" LIMIT {limit}" if limit else "")).fetchall()
    for r in rows: corp_map[r[0]] = (None, r[1])
    conn.close()

    _xml_path = "/tmp/CORPCODE.xml"
    if _os.path.exists(_xml_path):
        tree = _ET.parse(_xml_path)
        for item in tree.getroot():
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc and sc in corp_map:
                corp_map[sc] = (cc, corp_map[sc][1])

    stocks = [(sc, cc, nm) for sc, (cc, nm) in corp_map.items() if cc]
    ok = err = no_data = 0
    conn = _sl.connect(DB, timeout=60); conn.row_factory = _sl.Row
    conn.execute("PRAGMA journal_mode=WAL")

    for i, (sc, corp_code, sname) in enumerate(stocks):
        found_any = False
        for year in range(year_from, year_to + 1):
            for qtr in quarters:
                key = _next_key()
                if not key:
                    conn.commit(); conn.close()
                    return {"ok": ok, "err": err, "no_data": no_data, "stopped": "rate_limit"}
                rprt = RPRT[qtr]
                for fs_div in ("CFS", "OFS"):
                    try:
                        resp = _rq.get(
                            f"{DART_BASE}/fnlttSinglAcntAll.json",
                            params={"crtfc_key": key, "corp_code": corp_code,
                                    "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                            timeout=12
                        )
                        _t.sleep(0.25)
                        data = resp.json()
                        if data.get("status") == "020":
                            _exhausted.add(key)
                            key = _next_key()
                            if not key:
                                conn.commit(); conn.close()
                                return {"ok": ok, "err": err, "no_data": no_data, "stopped": "rate_limit"}
                            resp = _rq.get(
                                f"{DART_BASE}/fnlttSinglAcntAll.json",
                                params={"crtfc_key": key, "corp_code": corp_code,
                                        "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                                timeout=12
                            )
                            _t.sleep(0.25)
                            data = resp.json()
                        if data.get("status") != "000": break
                        items = data.get("list", [])
                        backlog, acct_nm = _best(items, BACKLOG_KW)
                        new_order, _ = _best(items, NEW_ORDER_KW)
                        if backlog is None: break
                        conn.execute("""
                            INSERT INTO order_backlog_api(stock_code, stock_name, year, quarter,
                               backlog_amount, new_order_amount, backlog_account_nm,
                               data_source, collected_at)
                            VALUES(?,?,?,?,?,?,?,'dart_api',CURRENT_TIMESTAMP)
                            ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                               backlog_amount=excluded.backlog_amount,
                               new_order_amount=excluded.new_order_amount,
                               backlog_account_nm=excluded.backlog_account_nm,
                               collected_at=CURRENT_TIMESTAMP
                        """, (sc, sname, year, qtr, backlog, new_order, acct_nm))
                        ok += 1; found_any = True; break
                    except Exception as e:
                        err += 1
                        logger.debug("[BACKLOG_API] %s %s Q%s: %s", sc, year, qtr, e)
        if not found_any:
            no_data += 1
        if i % 100 == 0 and i > 0:
            conn.commit()
            logger.info("[BACKLOG_API] %d/%d 종목 처리, ok=%d, no_data=%d", i, len(stocks), ok, no_data)

    conn.commit(); conn.close()
    return {"ok": ok, "err": err, "no_data": no_data}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART 매입재료비/재고/감가상각 분기 수집기")
    ap.add_argument("--year-from", type=int, default=2021)
    ap.add_argument("--year-to", type=int, default=datetime.now().year)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report-type", default="CFS")
    args = ap.parse_args()

    stats = collect_cost_quarterly(
        year_from=args.year_from,
        year_to=args.year_to,
        limit=(args.limit or None),
        report_type=args.report_type,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
