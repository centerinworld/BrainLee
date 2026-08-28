#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "run" / "investor_quantity_sync"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def coverage(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT substr(date,1,4) AS year,
               COUNT(*) AS total_rows,
               SUM(CASE
                   WHEN COALESCE(inst_net_buy,0) != 0
                     OR COALESCE(frn_net_buy,0) != 0
                     OR COALESCE(ind_net_buy,0) != 0
                   THEN 1 ELSE 0 END) AS quantity_rows
        FROM price_history
        WHERE substr(date,1,10) BETWEEN ? AND ?
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY substr(date,1,4)
        ORDER BY year
        """,
        (start, end),
    ).fetchall()
    out = []
    for row in rows:
        total = int(row["total_rows"] or 0)
        qty = int(row["quantity_rows"] or 0)
        out.append(
            {
                "year": row["year"],
                "total_rows": total,
                "quantity_rows": qty,
                "coverage_pct": round(qty / total * 100, 1) if total else 0.0,
            }
        )
    return out


def ensure_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_quality_repair_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            repair_name TEXT NOT NULL,
            affected_rows INTEGER NOT NULL,
            backup_table TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def prepare_updates(conn: sqlite3.Connection, start: str, end: str, overwrite: bool) -> dict:
    start_key = start.replace("-", "")
    end_key = end.replace("-", "")
    conn.executescript(
        """
        DROP TABLE IF EXISTS temp.itd_norm;
        DROP TABLE IF EXISTS temp.ph_qty_target;
        DROP TABLE IF EXISTS temp.sync_updates;
        """
    )
    conn.execute(
        """
        CREATE TEMP TABLE itd_norm AS
        SELECT stock_code,
               replace(substr(bas_dt,1,10), '-', '') AS dt_key,
               COALESCE(inst_net, 0) AS inst_net,
               COALESCE(frgn_net, 0) AS frgn_net,
               COALESCE(indv_net, 0) AS indv_net
        FROM investor_trading_daily
        WHERE replace(substr(bas_dt,1,10), '-', '') BETWEEN ? AND ?
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND (
              COALESCE(inst_net, 0) != 0
           OR COALESCE(frgn_net, 0) != 0
           OR COALESCE(indv_net, 0) != 0
          )
        """,
        (start_key, end_key),
    )
    conn.execute("CREATE INDEX idx_temp_itd_norm_code_dt ON itd_norm(stock_code, dt_key)")
    conn.execute(
        """
        CREATE TEMP TABLE ph_qty_target AS
        SELECT id, stock_code,
               replace(substr(date,1,10), '-', '') AS dt_key,
               COALESCE(inst_net_buy, 0) AS inst_net_buy,
               COALESCE(frn_net_buy, 0) AS frn_net_buy,
               COALESCE(ind_net_buy, 0) AS ind_net_buy
        FROM price_history
        WHERE substr(date,1,10) BETWEEN ? AND ?
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """,
        (start, end),
    )
    conn.execute("CREATE INDEX idx_temp_ph_qty_target_code_dt ON ph_qty_target(stock_code, dt_key)")

    if overwrite:
        where = """
            (COALESCE(i.inst_net,0) != COALESCE(p.inst_net_buy,0)
          OR COALESCE(i.frgn_net,0) != COALESCE(p.frn_net_buy,0)
          OR COALESCE(i.indv_net,0) != COALESCE(p.ind_net_buy,0))
        """
    else:
        where = """
            (COALESCE(p.inst_net_buy,0)=0 AND COALESCE(i.inst_net,0)!=0)
         OR (COALESCE(p.frn_net_buy,0)=0 AND COALESCE(i.frgn_net,0)!=0)
         OR (COALESCE(p.ind_net_buy,0)=0 AND COALESCE(i.indv_net,0)!=0)
        """

    conn.execute(
        f"""
        CREATE TEMP TABLE sync_updates AS
        SELECT p.id,
               CASE
                   WHEN ? THEN i.inst_net
                   WHEN COALESCE(p.inst_net_buy,0)=0 AND COALESCE(i.inst_net,0)!=0 THEN i.inst_net
                   ELSE p.inst_net_buy
               END AS new_inst_net_buy,
               CASE
                   WHEN ? THEN i.frgn_net
                   WHEN COALESCE(p.frn_net_buy,0)=0 AND COALESCE(i.frgn_net,0)!=0 THEN i.frgn_net
                   ELSE p.frn_net_buy
               END AS new_frn_net_buy,
               CASE
                   WHEN ? THEN i.indv_net
                   WHEN COALESCE(p.ind_net_buy,0)=0 AND COALESCE(i.indv_net,0)!=0 THEN i.indv_net
                   ELSE p.ind_net_buy
               END AS new_ind_net_buy,
               CASE WHEN ? OR (COALESCE(p.inst_net_buy,0)=0 AND COALESCE(i.inst_net,0)!=0) THEN 1 ELSE 0 END AS fill_inst,
               CASE WHEN ? OR (COALESCE(p.frn_net_buy,0)=0 AND COALESCE(i.frgn_net,0)!=0) THEN 1 ELSE 0 END AS fill_frn,
               CASE WHEN ? OR (COALESCE(p.ind_net_buy,0)=0 AND COALESCE(i.indv_net,0)!=0) THEN 1 ELSE 0 END AS fill_ind
        FROM ph_qty_target p
        JOIN itd_norm i ON i.stock_code = p.stock_code AND i.dt_key = p.dt_key
        WHERE {where}
        """,
        (overwrite, overwrite, overwrite, overwrite, overwrite, overwrite),
    )
    conn.execute("CREATE INDEX idx_temp_sync_updates_id ON sync_updates(id)")
    return {
        "source_rows": scalar(conn, "SELECT COUNT(*) FROM itd_norm"),
        "target_rows": scalar(conn, "SELECT COUNT(*) FROM ph_qty_target"),
        "rows_to_update": scalar(conn, "SELECT COUNT(*) FROM sync_updates"),
        "inst_cells_to_fill": scalar(conn, "SELECT SUM(fill_inst) FROM sync_updates"),
        "frn_cells_to_fill": scalar(conn, "SELECT SUM(fill_frn) FROM sync_updates"),
        "ind_cells_to_fill": scalar(conn, "SELECT SUM(fill_ind) FROM sync_updates"),
    }


def apply_updates(conn: sqlite3.Connection, run_id: str) -> tuple[int, str]:
    backup_table = f"backup_price_history_qty_supply_{run_id}"
    conn.execute(f"DROP TABLE IF EXISTS {backup_table}")
    conn.execute(
        f"""
        CREATE TABLE {backup_table} AS
        SELECT ph.id, ph.stock_code, ph.date,
               ph.inst_net_buy, ph.frn_net_buy, ph.ind_net_buy,
               su.new_inst_net_buy, su.new_frn_net_buy, su.new_ind_net_buy
        FROM price_history ph
        JOIN sync_updates su ON su.id = ph.id
        """
    )
    backup_rows = scalar(conn, f"SELECT COUNT(*) FROM {backup_table}")
    conn.execute(
        """
        UPDATE price_history
        SET inst_net_buy = (SELECT new_inst_net_buy FROM sync_updates WHERE id = price_history.id),
            frn_net_buy = (SELECT new_frn_net_buy FROM sync_updates WHERE id = price_history.id),
            ind_net_buy = (SELECT new_ind_net_buy FROM sync_updates WHERE id = price_history.id)
        WHERE id IN (SELECT id FROM sync_updates)
        """
    )
    ensure_log_table(conn)
    conn.execute(
        """
        INSERT INTO data_quality_repair_log(run_id, table_name, repair_name, affected_rows, backup_table)
        VALUES (?, 'price_history', 'sync_investor_trading_daily_quantity_to_price_history', ?, ?)
        """,
        (run_id, backup_rows, backup_table),
    )
    return backup_rows, backup_table


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill price_history quantity investor net buy fields from investor_trading_daily volumes."
    )
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2022-12-31")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing non-zero price_history quantity values. Default only fills zero/null gaps.",
    )
    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        before = coverage(conn, args.start, args.end)
        stats = prepare_updates(conn, args.start, args.end, args.overwrite)
        backup_table = None
        applied_rows = 0
        if not args.dry_run and stats["rows_to_update"]:
            applied_rows, backup_table = apply_updates(conn, run_id)
            conn.commit()
            after = coverage(conn, args.start, args.end)
        else:
            conn.rollback()
            after = before

    summary = {
        "run_id": run_id,
        "db_path": str(DB_PATH),
        "period": {"start": args.start, "end": args.end},
        "dry_run": args.dry_run,
        "overwrite": args.overwrite,
        "source": "investor_trading_daily volume net fields (indv_net/inst_net/frgn_net)",
        "excluded_source": "kiwoom_investor_daily / ka10059 recrawl not used until net-buy parameters are verified",
        "stats": stats,
        "applied_rows": applied_rows,
        "backup_table": backup_table,
        "coverage_before": before,
        "coverage_after": after,
    }
    out_path = OUT_DIR / f"summary_{run_id}{'_dry_run' if args.dry_run else ''}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary_path={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
