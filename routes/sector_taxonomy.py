"""Multi-source Korean stock taxonomy API."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from db_compat import connect_primary_db
from sector_taxonomy import LATEST_MEMBERSHIPS_SQL

router = APIRouter()


def _dict(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _json(value: Any) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"raw": str(value)}


def _days_old(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return (date.today() - datetime.fromisoformat(str(value)[:10]).date()).days
    except ValueError:
        return None


@router.get("/overview")
def overview():
    with connect_primary_db(readonly=True) as conn:
        universe = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM stock_universe WHERE COALESCE(stock_type,'보통주') NOT IN ('ETF','ETN')"
        ).fetchone()[0]
        covered = conn.execute(f"SELECT COUNT(DISTINCT stock_code) FROM ({LATEST_MEMBERSHIPS_SQL}) x").fetchone()[0]
        dimensions = [_dict(r) for r in conn.execute(
            f"""SELECT n.taxonomy, n.dimension, COUNT(DISTINCT n.node_key) AS node_count,
                       COUNT(DISTINCT m.stock_code) AS stock_count, COUNT(*) AS membership_count
                FROM ({LATEST_MEMBERSHIPS_SQL}) m
                JOIN sector_taxonomy_nodes n ON n.node_key=m.node_key
                GROUP BY n.taxonomy, n.dimension
                ORDER BY n.taxonomy, n.dimension"""
        ).fetchall()]
        runs = [_dict(r) for r in conn.execute(
            """SELECT source_system, snapshot_date, status, node_count, membership_count,
                      source_url, completed_at, error_message, metadata_json
               FROM sector_taxonomy_source_runs
               ORDER BY source_system, snapshot_date DESC, completed_at DESC"""
        ).fetchall()]
    latest_runs = {}
    for run in runs:
        if run["source_system"] not in latest_runs:
            run["days_old"] = _days_old(run["snapshot_date"])
            run["metadata"] = _json(run.pop("metadata_json"))
            latest_runs[run["source_system"]] = run
    return {
        "universe_count": int(universe or 0),
        "covered_stock_count": int(covered or 0),
        "coverage_pct": round(100 * covered / universe, 2) if universe else 0,
        "dimensions": dimensions,
        "sources": list(latest_runs.values()),
        "model": {
            "axes": ["industry", "theme", "value_chain", "process", "material", "peer_group"],
            "principle": "원천 분류와 내부 해석을 분리하고 종목당 여러 태그를 허용",
        },
    }


@router.get("/tree")
def tree(
    taxonomy: str | None = Query(None),
    dimension: str | None = Query(None),
):
    clauses, params = ["n.is_active=1"], []
    if taxonomy:
        clauses.append("n.taxonomy=?")
        params.append(taxonomy)
    if dimension:
        clauses.append("n.dimension=?")
        params.append(dimension)
    with connect_primary_db(readonly=True) as conn:
        rows = conn.execute(
            f"""SELECT n.node_key, n.taxonomy, n.dimension, n.external_code, n.name,
                       n.parent_key, n.description, n.source_url, n.taxonomy_version,
                       COUNT(DISTINCT m.stock_code) AS stock_count,
                       COUNT(*) AS membership_count,
                       MAX(m.source_snapshot_date) AS snapshot_date
                FROM sector_taxonomy_nodes n
                LEFT JOIN ({LATEST_MEMBERSHIPS_SQL}) m ON m.node_key=n.node_key
                WHERE {' AND '.join(clauses)}
                GROUP BY n.node_key, n.taxonomy, n.dimension, n.external_code, n.name,
                         n.parent_key, n.description, n.source_url, n.taxonomy_version
                HAVING COUNT(m.stock_code) > 0
                ORDER BY n.taxonomy, n.dimension, stock_count DESC, n.name""",
            params,
        ).fetchall()
    return {"nodes": [_dict(row) for row in rows]}


@router.get("/stocks")
def stocks(
    node_key: str | None = Query(None),
    q: str | None = Query(None, min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    clauses = ["COALESCE(u.stock_type,'보통주') NOT IN ('ETF','ETN')"]
    params: list[Any] = []
    membership_join = ""
    if node_key:
        membership_join = f"JOIN ({LATEST_MEMBERSHIPS_SQL}) selected ON selected.stock_code=u.stock_code AND selected.node_key=?"
        params.append(node_key)
    if q:
        clauses.append("(LOWER(u.stock_name) LIKE LOWER(?) OR LOWER(u.stock_code) LIKE LOWER(?))")
        params.extend([f"%{q}%", f"%{q}%"])
    params.extend([limit, offset])
    with connect_primary_db(readonly=True) as conn:
        rows = conn.execute(
            f"""SELECT u.stock_code, u.stock_name, u.market, u.market_cap,
                       u.sector_large, u.sector_mid, u.sector_small,
                       COUNT(DISTINCT all_m.node_key) AS tag_count,
                       COUNT(DISTINCT CASE WHEN all_n.dimension='peer_group' THEN all_m.node_key END) AS peer_group_count
                FROM stock_universe u
                {membership_join}
                LEFT JOIN ({LATEST_MEMBERSHIPS_SQL}) all_m ON all_m.stock_code=u.stock_code
                LEFT JOIN sector_taxonomy_nodes all_n ON all_n.node_key=all_m.node_key
                WHERE {' AND '.join(clauses)}
                GROUP BY u.stock_code, u.stock_name, u.market, u.market_cap,
                         u.sector_large, u.sector_mid, u.sector_small
                ORDER BY u.market_cap DESC NULLS LAST, u.stock_name
                LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
    return {"stocks": [_dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/stock/{stock_code}")
def stock_detail(stock_code: str):
    code = stock_code.strip().upper()
    with connect_primary_db(readonly=True) as conn:
        stock = conn.execute(
            """SELECT stock_code, stock_name, market, market_cap, sector_large, sector_mid, sector_small
               FROM stock_universe WHERE stock_code=?""",
            (code,),
        ).fetchone()
        if not stock:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        rows = conn.execute(
            f"""SELECT m.stock_code, m.source_system, m.source_snapshot_date, m.confidence,
                       m.status, m.is_primary, m.evidence_json,
                       n.node_key, n.taxonomy, n.dimension, n.external_code, n.name,
                       n.parent_key, n.description, n.source_url
                FROM ({LATEST_MEMBERSHIPS_SQL}) m
                JOIN sector_taxonomy_nodes n ON n.node_key=m.node_key
                WHERE m.stock_code=?
                ORDER BY CASE m.status WHEN 'verified' THEN 0 WHEN 'observed' THEN 1 WHEN 'inferred' THEN 2 ELSE 3 END,
                         m.confidence DESC, n.taxonomy, n.dimension, n.name""",
            (code,),
        ).fetchall()
        mix = conn.execute(
            """SELECT year, category, product_name, revenue_pct
               FROM company_product_mix
               WHERE stock_code=? AND year=(SELECT MAX(year) FROM company_product_mix WHERE stock_code=?)
               ORDER BY revenue_pct DESC NULLS LAST LIMIT 15""",
            (code, code),
        ).fetchall()
    tags = []
    industry_names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        item = _dict(row)
        item["evidence"] = _json(item.pop("evidence_json"))
        tags.append(item)
        if item["dimension"].startswith("industry_") and item["dimension"] != "industry_large":
            industry_names[item["dimension"]].add(item["name"])
    disagreements = [
        {"dimension": dimension, "values": sorted(names), "notice": "출처별 분류가 다릅니다. 복수 관점을 유지합니다."}
        for dimension, names in industry_names.items() if len(names) > 1
    ]
    return {
        "stock": _dict(stock),
        "tags": tags,
        "peer_groups": [tag for tag in tags if tag["dimension"] == "peer_group"],
        "source_disagreements": disagreements,
        "product_mix": [_dict(row) for row in mix],
    }


@router.get("/peer-groups")
def peer_groups(limit: int = Query(100, ge=1, le=500)):
    with connect_primary_db(readonly=True) as conn:
        groups = conn.execute(
            f"""SELECT n.node_key, n.name, n.description, n.taxonomy_version,
                       COUNT(DISTINCT m.stock_code) AS member_count,
                       MAX(m.source_snapshot_date) AS snapshot_date
                FROM sector_taxonomy_nodes n
                JOIN ({LATEST_MEMBERSHIPS_SQL}) m ON m.node_key=n.node_key
                WHERE n.dimension='peer_group'
                GROUP BY n.node_key, n.name, n.description, n.taxonomy_version
                ORDER BY member_count DESC, n.name LIMIT ?""",
            (limit,),
        ).fetchall()
        output = []
        for group in groups:
            item = _dict(group)
            members = conn.execute(
                f"""SELECT u.stock_code, u.stock_name, u.market, u.market_cap,
                           m.confidence, m.status, m.evidence_json
                    FROM ({LATEST_MEMBERSHIPS_SQL}) m
                    JOIN stock_universe u ON u.stock_code=m.stock_code
                    WHERE m.node_key=? ORDER BY u.market_cap DESC NULLS LAST""",
                (item["node_key"],),
            ).fetchall()
            item["members"] = []
            for member in members:
                value = _dict(member)
                value["evidence"] = _json(value.pop("evidence_json"))
                item["members"].append(value)
            output.append(item)
    return {"groups": output}
