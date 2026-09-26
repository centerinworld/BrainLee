import json
import sqlite3

from sector_taxonomy import (
    LATEST_MEMBERSHIPS_SQL,
    ensure_sector_taxonomy_tables,
    make_node_key,
    record_source_run,
    replace_source_snapshot,
    upsert_node,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_sector_taxonomy_tables(conn)
    return conn


def test_multiple_dimensions_and_sources_are_preserved():
    conn = _db()
    industry = make_node_key("stockeasy", "industry_middle", "메모리")
    process = make_node_key("internal_taxonomy", "process", "전공정 장비")
    upsert_node(conn, node_key=industry, taxonomy="stockeasy", dimension="industry_middle", name="메모리")
    upsert_node(conn, node_key=process, taxonomy="internal_taxonomy", dimension="process", name="전공정 장비")
    replace_source_snapshot(conn, source_system="stockeasy", snapshot_date="2026-09-23", memberships=[{"stock_code": "123456", "node_key": industry, "confidence": 80, "status": "observed"}])
    replace_source_snapshot(conn, source_system="internal_taxonomy", snapshot_date="2026-09-26", memberships=[{"stock_code": "123456", "node_key": process, "confidence": 70, "status": "inferred", "evidence_json": {"matched": "식각"}}])
    record_source_run(conn, source_system="stockeasy", snapshot_date="2026-09-23", status="success", membership_count=1)
    record_source_run(conn, source_system="internal_taxonomy", snapshot_date="2026-09-26", status="success", membership_count=1)
    rows = conn.execute(f"SELECT * FROM ({LATEST_MEMBERSHIPS_SQL}) x WHERE stock_code='123456'").fetchall()
    assert {(row["source_system"], row["node_key"]) for row in rows} == {
        ("stockeasy", industry), ("internal_taxonomy", process)
    }
    assert json.loads(next(row["evidence_json"] for row in rows if row["source_system"] == "internal_taxonomy"))["matched"] == "식각"


def test_latest_successful_snapshot_excludes_failed_and_old_runs():
    conn = _db()
    node = make_node_key("kiwoom", "theme", "HBM")
    upsert_node(conn, node_key=node, taxonomy="kiwoom", dimension="theme", name="HBM")
    for snapshot, code, status in (
        ("2026-09-24", "111111", "success"),
        ("2026-09-25", "222222", "failed"),
        ("2026-09-26", "333333", "success"),
    ):
        replace_source_snapshot(conn, source_system="kiwoom_theme", snapshot_date=snapshot, memberships=[{"stock_code": code, "node_key": node}])
        record_source_run(conn, source_system="kiwoom_theme", snapshot_date=snapshot, status=status, membership_count=1)
    rows = conn.execute(LATEST_MEMBERSHIPS_SQL).fetchall()
    assert [row["stock_code"] for row in rows] == ["333333"]


def test_replacing_same_snapshot_is_idempotent():
    conn = _db()
    node = make_node_key("internal_taxonomy", "peer_group", "PBA 경쟁군")
    upsert_node(conn, node_key=node, taxonomy="internal_taxonomy", dimension="peer_group", name="PBA 경쟁군")
    first = [{"stock_code": "094970", "node_key": node}, {"stock_code": "054040", "node_key": node}]
    replace_source_snapshot(conn, source_system="internal_taxonomy", snapshot_date="2026-09-26", memberships=first)
    inserted = replace_source_snapshot(conn, source_system="internal_taxonomy", snapshot_date="2026-09-26", memberships=first[:1] + first[:1])
    assert inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM stock_sector_membership_v2").fetchone()[0] == 1
