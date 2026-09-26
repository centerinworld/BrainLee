#!/usr/bin/env python3
"""Build the source-aware Korean stock taxonomy and peer groups.

Sources are deliberately kept separate.  StockEasy and Kiwoom observations do
not overwrite the internal taxonomy, and inferred rules never masquerade as a
provider classification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.kiwoom_collector import KiwoomCollector
from db_compat import connect_primary_db
from sector_taxonomy import (
    ensure_sector_taxonomy_tables,
    make_node_key,
    record_source_run,
    replace_source_snapshot,
    upsert_node,
)

STOCKEASY_URL = "https://stockeasy.intellio.kr/stockdata/api/v1/valuation/data"
KIWOOM_GUIDE_URL = "https://openapi1.kiwoom.com/guide/apiguide"
INTERNAL_VERSION = "2026-09-26.1"


def _rows(conn, sql: str, params=()) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys()} for row in conn.execute(sql, params).fetchall()]


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _valid_code(code: Any) -> str | None:
    value = str(code or "").strip().upper()
    return value if re.fullmatch(r"[0-9A-Z]{6}", value) else None


def _node(conn, taxonomy: str, dimension: str, name: str, **kwargs) -> str:
    key = make_node_key(taxonomy, dimension, kwargs.get("external_code") or name)
    upsert_node(
        conn,
        node_key=key,
        taxonomy=taxonomy,
        dimension=dimension,
        name=name,
        taxonomy_version=kwargs.pop("taxonomy_version", INTERNAL_VERSION),
        **kwargs,
    )
    return key


def sync_universe_legacy(conn, snapshot_date: str) -> dict[str, Any]:
    source = "universe_legacy"
    url = "internal:stock_universe"
    rows = _rows(
        conn,
        """SELECT stock_code, stock_name, sector_large, sector_mid, sector_small
           FROM stock_universe
           WHERE COALESCE(stock_type, '보통주') NOT IN ('ETF', 'ETN')""",
    )
    memberships: list[dict[str, Any]] = []
    nodes: set[str] = set()
    for row in rows:
        code = _valid_code(row["stock_code"])
        if not code:
            continue
        parent = None
        for dimension, column, confidence, primary in (
            ("industry_large", "sector_large", 75, True),
            ("industry_middle", "sector_mid", 68, False),
            ("industry_small", "sector_small", 62, False),
        ):
            name = str(row.get(column) or "").strip()
            if not name:
                continue
            key = _node(
                conn,
                source,
                dimension,
                name,
                parent_key=parent,
                source_url=url,
                description="기존 stock_universe 분류. 제공자 원천이 혼재할 수 있어 기준선·비교용으로만 사용",
            )
            nodes.add(key)
            memberships.append(
                {
                    "stock_code": code,
                    "node_key": key,
                    "confidence": confidence,
                    "status": "legacy",
                    "is_primary": primary,
                    "evidence_json": {"field": column, "value": name, "stock_name": row["stock_name"]},
                }
            )
            parent = key
    count = replace_source_snapshot(conn, source_system=source, snapshot_date=snapshot_date, memberships=memberships)
    record_source_run(conn, source_system=source, snapshot_date=snapshot_date, status="success", node_count=len(nodes), membership_count=count, source_url=url)
    return {"source": source, "nodes": len(nodes), "memberships": count}


def fetch_stockeasy() -> tuple[str, dict[str, Any], str]:
    response = requests.get(STOCKEASY_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success") or not isinstance(payload.get("data", {}).get("data"), dict):
        raise RuntimeError("StockEasy valuation payload shape changed")
    raw_date = str(payload["data"].get("date") or datetime.now().strftime("%Y%m%d"))
    snapshot = datetime.strptime(raw_date[:8], "%Y%m%d").strftime("%Y-%m-%d")
    return snapshot, payload["data"]["data"], _digest(payload)


def sync_stockeasy(conn) -> dict[str, Any]:
    source = "stockeasy"
    snapshot, data, payload_hash = fetch_stockeasy()
    memberships: list[dict[str, Any]] = []
    nodes: set[str] = set()
    for raw_code, item in data.items():
        code = _valid_code(raw_code)
        if not code or not isinstance(item, dict):
            continue
        major = str(item.get("대분류") or "").strip()
        middle = str(item.get("중분류") or "").strip()
        parent = None
        if major:
            parent = _node(conn, source, "industry_major", major, source_url=STOCKEASY_URL, taxonomy_version=snapshot)
            nodes.add(parent)
            memberships.append({"stock_code": code, "node_key": parent, "confidence": 82, "status": "observed", "is_primary": True, "evidence_json": {"field": "대분류", "value": major, "stock_name": item.get("name")}})
        if middle:
            key = _node(conn, source, "industry_middle", middle, parent_key=parent, source_url=STOCKEASY_URL, taxonomy_version=snapshot)
            nodes.add(key)
            memberships.append({"stock_code": code, "node_key": key, "confidence": 80, "status": "observed", "evidence_json": {"field": "중분류", "value": middle, "stock_name": item.get("name")}})
    if len({m["stock_code"] for m in memberships}) < 2000:
        raise RuntimeError(f"StockEasy coverage collapse: {len(memberships)} memberships")
    count = replace_source_snapshot(conn, source_system=source, snapshot_date=snapshot, memberships=memberships)
    record_source_run(conn, source_system=source, snapshot_date=snapshot, status="success", node_count=len(nodes), membership_count=count, source_url=STOCKEASY_URL, payload_hash=payload_hash, metadata={"stocks": len(data)})
    return {"source": source, "snapshot": snapshot, "nodes": len(nodes), "memberships": count}


def _kiwoom_pages(k: KiwoomCollector, api_id: str, path: str, body: dict[str, str], delay: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cont, next_key = "N", ""
    while True:
        response = requests.post(k.base_url + path, headers=k._auth_headers(api_id, cont, next_key), json=body, timeout=25)
        data = response.json() if response.content else {}
        if response.status_code >= 400 or str(data.get("return_code", "0")) not in {"0", "", "None"}:
            raise RuntimeError(f"{api_id}: HTTP {response.status_code} {str(data)[:300]}")
        rows.extend(k._first_list_payload(data))
        cont = response.headers.get("cont-yn", response.headers.get("Cont-Yn", "N"))
        next_key = response.headers.get("next-key", response.headers.get("Next-Key", ""))
        if str(cont).upper() != "Y" or not next_key:
            break
        time.sleep(delay)
    return rows


def sync_kiwoom(conn, snapshot_date: str, delay: float = 0.22) -> dict[str, Any]:
    k = KiwoomCollector()
    if not k.ensure_token():
        raise RuntimeError("Kiwoom token unavailable")
    universe = {r["stock_code"] for r in _rows(conn, "SELECT stock_code FROM stock_universe")}
    results = []

    theme_groups = _kiwoom_pages(k, "ka90001", "/api/dostk/thme", {"qry_tp": "0", "stk_cd": "", "date_tp": "1", "thema_nm": "", "flu_pl_amt_tp": "1", "stex_tp": "1"}, delay)
    theme_members: list[dict[str, Any]] = []
    theme_nodes: set[str] = set()
    for index, group in enumerate(theme_groups):
        ext = str(group.get("thema_grp_cd") or "").strip()
        name = str(group.get("thema_nm") or "").strip()
        if not ext or not name:
            continue
        key = _node(conn, "kiwoom", "theme", name, external_code=ext, source_url=KIWOOM_GUIDE_URL, taxonomy_version=snapshot_date, description="키움 ka90001/ka90002 테마 및 구성종목")
        theme_nodes.add(key)
        rows = _kiwoom_pages(k, "ka90002", "/api/dostk/thme", {"date_tp": "1", "thema_grp_cd": ext, "stex_tp": "1"}, delay)
        for row in rows:
            code = _valid_code(row.get("stk_cd"))
            if code and code in universe:
                theme_members.append({"stock_code": code, "node_key": key, "confidence": 78, "status": "observed", "evidence_json": {"api_id": "ka90002", "theme_code": ext, "theme_name": name, "stock_name": row.get("stk_nm")}})
        if index + 1 < len(theme_groups):
            time.sleep(delay)
    count = replace_source_snapshot(conn, source_system="kiwoom_theme", snapshot_date=snapshot_date, memberships=theme_members)
    record_source_run(conn, source_system="kiwoom_theme", snapshot_date=snapshot_date, status="success", node_count=len(theme_nodes), membership_count=count, source_url=KIWOOM_GUIDE_URL, payload_hash=_digest(theme_groups), metadata={"theme_groups": len(theme_groups)})
    results.append({"source": "kiwoom_theme", "nodes": len(theme_nodes), "memberships": count})

    industry_members: list[dict[str, Any]] = []
    industry_nodes: set[str] = set()
    for market in ("0", "1"):
        groups = _kiwoom_pages(k, "ka10101", "/api/dostk/stkinfo", {"mrkt_tp": market}, delay)
        for group in groups:
            ext = str(group.get("code") or "").strip()
            name = str(group.get("name") or "").strip()
            if not ext or not name:
                continue
            is_market_segment = bool(re.search(r"종합\(|대형주|중형주|소형주|KOSPI|KOSDAQ|벤처기업|우량기업|중견기업|기술성장기업", name, re.I))
            dimension = "market_segment" if is_market_segment else "industry"
            key = _node(conn, "kiwoom", dimension, name, external_code=f"{market}:{ext}", source_url=KIWOOM_GUIDE_URL, taxonomy_version=snapshot_date, description="키움 ka10101 업종코드 및 ka20002 구성종목")
            industry_nodes.add(key)
            rows = _kiwoom_pages(k, "ka20002", "/api/dostk/sect", {"mrkt_tp": market, "inds_cd": ext, "stex_tp": "1"}, delay)
            for row in rows:
                code = _valid_code(row.get("stk_cd"))
                if code and code in universe:
                    industry_members.append({"stock_code": code, "node_key": key, "confidence": 88, "status": "observed", "is_primary": int(group.get("group") or 99) >= 5, "evidence_json": {"api_id": "ka20002", "industry_code": ext, "industry_name": name, "market_type": market, "stock_name": row.get("stk_nm")}})
            time.sleep(delay)
    count = replace_source_snapshot(conn, source_system="kiwoom_industry", snapshot_date=snapshot_date, memberships=industry_members)
    record_source_run(conn, source_system="kiwoom_industry", snapshot_date=snapshot_date, status="success", node_count=len(industry_nodes), membership_count=count, source_url=KIWOOM_GUIDE_URL, metadata={"markets": ["KOSPI", "KOSDAQ"]})
    results.append({"source": "kiwoom_industry", "nodes": len(industry_nodes), "memberships": count})
    return {"source": "kiwoom", "results": results}


SEMICON_RULES = [
    ("value_chain", "설계·팹리스", r"팹리스|fabless|반도체.{0,6}(설계|design)|system\s*ic"),
    ("value_chain", "파운드리·IDM", r"파운드리|foundry|idm|dram|nand|hbm"),
    ("process", "전공정 장비", r"노광|식각|etch|증착|cvd|ald|세정.{0,5}(장비|system)|이온주입|implant|cmp.{0,5}장비"),
    ("process", "후공정 패키징·조립", r"후공정|패키징|packaging|osat|범핑|bumping|flip.?chip"),
    ("process", "테스트·검사", r"프로브.?카드|probe.?card|테스트.?소켓|test.?socket|테스트.?핸들러|handler|반도체.{0,8}(검사|test)"),
    ("material", "웨이퍼·기판", r"실리콘.?웨이퍼|silicon.?wafer|fc-?bga|반도체.?기판|package.?substrate"),
    ("material", "케미칼·가스·소재", r"포토레지스트|photoresist|슬러리|precursor|전구체|특수가스|반도체.{0,8}(소재|케미칼|가스)"),
]


def sync_internal(conn, snapshot_date: str) -> dict[str, Any]:
    source = "internal_taxonomy"
    universe = _rows(conn, """SELECT stock_code, stock_name, sector_large, sector_mid, sector_small
                              FROM stock_universe WHERE COALESCE(stock_type,'보통주') NOT IN ('ETF','ETN')""")
    mixes = _rows(conn, """SELECT p.stock_code, p.year, p.category, p.product_name, p.revenue_pct
                           FROM company_product_mix p
                           JOIN (SELECT stock_code, MAX(year) AS year FROM company_product_mix GROUP BY stock_code) y
                             ON y.stock_code=p.stock_code AND y.year=p.year""")
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in mixes:
        by_code[row["stock_code"]].append(row)

    root = _node(conn, source, "industry", "반도체", description="제품·사업 근거 기반 내부 다중분류", source_url="internal:rules", taxonomy_version=INTERNAL_VERSION)
    electronics = _node(conn, source, "industry", "전자부품", description="제품·사업 근거 기반 내부 다중분류", source_url="internal:rules", taxonomy_version=INTERNAL_VERSION)
    ems = _node(conn, source, "value_chain", "EMS·모듈 조립", parent_key=electronics, description="PBA·FPBA·전자 모듈 제조/조립", source_url="internal:rules", taxonomy_version=INTERNAL_VERSION)
    pba = _node(conn, source, "process", "PBA·FPBA·모듈 조립", parent_key=ems, source_url="internal:rules", taxonomy_version=INTERNAL_VERSION)
    peer = _node(conn, source, "peer_group", "OLED·스마트기기 PBA/FPBA 제조사", parent_key=pba, description="제품 및 생산공정이 겹치는 실질 경쟁·비교군", source_url="internal:curated", taxonomy_version=INTERNAL_VERSION)
    nodes = {root, electronics, ems, pba, peer}
    rule_nodes = {}
    for dimension, name, _ in SEMICON_RULES:
        rule_nodes[(dimension, name)] = _node(conn, source, dimension, name, parent_key=root, source_url="internal:rules", taxonomy_version=INTERNAL_VERSION)
        nodes.add(rule_nodes[(dimension, name)])

    memberships: list[dict[str, Any]] = []
    curated_peer = {
        "094970": "DART 2025 제품: PBA 32.27%, FPBA 25.83%",
        "290550": "DART 2025 제품: 스마트폰 65.04%, 전장 25.19% (연성회로/모듈 조립)",
        "054040": "DART 2025 제품: OLED-PBA 59.19%, QD/LCD 모듈 부품 32.84%",
    }
    for row in universe:
        code = _valid_code(row["stock_code"])
        if not code:
            continue
        products = by_code.get(code, [])
        product_text = " | ".join(f"{p.get('category','')} {p.get('product_name','')} {p.get('revenue_pct','')}%" for p in products)
        base_text = " ".join(str(row.get(k) or "") for k in ("stock_name", "sector_large", "sector_mid", "sector_small"))
        text = f"{base_text} {product_text}".lower()
        semiconductor_context = bool(re.search(r"반도체|semiconductor|dram|nand|hbm|wafer|웨이퍼|fabless|파운드리", text))
        if semiconductor_context:
            memberships.append({"stock_code": code, "node_key": root, "confidence": 72, "status": "inferred", "is_primary": True, "evidence_json": {"matched": "semiconductor_context", "text": text[:700]}})
            for dimension, name, pattern in SEMICON_RULES:
                match = re.search(pattern, text, re.I)
                if match:
                    memberships.append({"stock_code": code, "node_key": rule_nodes[(dimension, name)], "confidence": 70, "status": "inferred", "evidence_json": {"matched": match.group(0), "products": product_text[:700], "rule_version": INTERNAL_VERSION}})
        pba_match = re.search(r"\b(?:pba|fpba)\b|oled-?pba|module용 부품|모듈.{0,5}(조립|부품)", text, re.I)
        if pba_match:
            for key, confidence in ((electronics, 74), (ems, 78), (pba, 80)):
                memberships.append({"stock_code": code, "node_key": key, "confidence": confidence, "status": "inferred", "evidence_json": {"matched": pba_match.group(0), "products": product_text[:700], "rule_version": INTERNAL_VERSION}})
        if code in curated_peer:
            # A verified child peer membership also carries its parent path,
            # including cases where the latest DART product label is broad
            # (e.g. 디케이티's "스마트폰") and does not contain PBA literally.
            for key, confidence in ((electronics, 90), (ems, 94), (pba, 96)):
                memberships.append({"stock_code": code, "node_key": key, "confidence": confidence, "status": "verified", "evidence_json": {"rationale": curated_peer[code], "source": "DART company_product_mix 2025 + curated peer review"}})
            memberships.append({"stock_code": code, "node_key": peer, "confidence": 98, "status": "verified", "is_primary": True, "evidence_json": {"rationale": curated_peer[code], "comparison_axis": "PBA/FPBA·OLED/스마트기기 모듈 제조", "source": "DART company_product_mix 2025"}})

    count = replace_source_snapshot(conn, source_system=source, snapshot_date=snapshot_date, memberships=memberships)
    record_source_run(conn, source_system=source, snapshot_date=snapshot_date, status="success", node_count=len(nodes), membership_count=count, source_url="internal:rules", payload_hash=_digest({"version": INTERNAL_VERSION, "rules": SEMICON_RULES, "curated": curated_peer}), metadata={"rule_version": INTERNAL_VERSION, "verified_peer_members": len(curated_peer)})
    return {"source": source, "nodes": len(nodes), "memberships": count}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-stockeasy", action="store_true")
    parser.add_argument("--skip-kiwoom", action="store_true")
    parser.add_argument("--kiwoom-delay", type=float, default=0.22)
    args = parser.parse_args()
    today = datetime.now().strftime("%Y-%m-%d")
    results = []
    errors = []
    conn = connect_primary_db(timeout=120)
    try:
        ensure_sector_taxonomy_tables(conn)
        conn.commit()
        tasks = [("universe_legacy", lambda: sync_universe_legacy(conn, today))]
        if not args.skip_stockeasy:
            tasks.append(("stockeasy", lambda: sync_stockeasy(conn)))
        if not args.skip_kiwoom:
            tasks.append(("kiwoom_refresh", lambda: sync_kiwoom(conn, today, max(0.12, args.kiwoom_delay))))
        tasks.append(("internal_taxonomy", lambda: sync_internal(conn, today)))
        for source_name, task in tasks:
            try:
                results.append(task())
                conn.commit()
            except Exception as exc:
                conn.rollback()
                stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                record_source_run(
                    conn,
                    source_system=source_name,
                    snapshot_date=today,
                    status="failed",
                    error_message=str(exc)[:1500],
                    run_key=f"{source_name}:{today}:failed:{stamp}",
                )
                conn.commit()
                errors.append({"source": source_name, "error": str(exc)})
    finally:
        conn.close()
    print(json.dumps({"ok": not errors, "results": results, "errors": errors}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
