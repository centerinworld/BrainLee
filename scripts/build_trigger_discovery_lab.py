#!/usr/bin/env python3
"""Build point-in-time trigger discovery tables for BigQuery/SQLite research.

The lab tables are intentionally narrow and cheap to scan:

- trigger_discovery_events
- trigger_discovery_stock_links
- trigger_discovery_forward_returns

Every event has an `available_date`. Forward returns are calculated from the
first tradable close within a short fill window to reduce sparse-price artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"


def norm_date(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def month_available(period: str) -> str | None:
    s = str(period or "").strip()
    if len(s) >= 6 and s[:4].isdigit() and s[4].upper() == "Q" and s[5].isdigit():
        return quarter_available(int(s[:4]), int(s[5]), lag_days=60)
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5].upper() == "Q" and s[6].isdigit():
        return quarter_available(int(s[:4]), int(s[6]), lag_days=60)
    if len(s) >= 7 and s[4] == "-":
        if not (s[:4].isdigit() and s[5:7].isdigit()):
            return norm_date(s)
        y, m = int(s[:4]), int(s[5:7])
        if m == 12:
            return date(y + 1, 1, 20).isoformat()
        return date(y, m + 1, 20).isoformat()
    return norm_date(s)


def quarter_available(year: int, quarter: int, lag_days: int = 60) -> str:
    month = int(quarter) * 3
    day = 31 if month in (3, 12) else 30
    return (date(int(year), month, day) + timedelta(days=lag_days)).isoformat()


def event_id(parts: list[object]) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trigger_discovery_events (
            event_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            trigger_key TEXT NOT NULL,
            trigger_name TEXT,
            event_date TEXT,
            available_date TEXT NOT NULL,
            period TEXT,
            entity_type TEXT,
            entity_key TEXT,
            sector_name TEXT,
            stock_code TEXT,
            value REAL,
            z_score REAL,
            mom_pct REAL,
            yoy_pct REAL,
            direction TEXT,
            strength REAL,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_tde_avail ON trigger_discovery_events(available_date, trigger_key);
        CREATE INDEX IF NOT EXISTS idx_tde_stock ON trigger_discovery_events(stock_code, available_date);
        CREATE INDEX IF NOT EXISTS idx_tde_entity ON trigger_discovery_events(entity_type, entity_key, available_date);

        CREATE TABLE IF NOT EXISTS trigger_discovery_stock_links (
            event_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            sector_name TEXT,
            link_source TEXT,
            confidence REAL,
            revenue_exposure_pct REAL,
            profit_exposure_pct REAL,
            cost_exposure_pct REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, stock_code)
        );

        CREATE INDEX IF NOT EXISTS idx_tdsl_stock ON trigger_discovery_stock_links(stock_code);

        CREATE TABLE IF NOT EXISTS trigger_discovery_forward_returns (
            event_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,
            entry_date TEXT,
            entry_close REAL,
            exit_date TEXT,
            exit_close REAL,
            return_pct REAL,
            max_drawdown_pct REAL,
            fill_gap_days INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, stock_code, horizon_days)
        );

        CREATE INDEX IF NOT EXISTS idx_tdf_stock ON trigger_discovery_forward_returns(stock_code, horizon_days);
        """
    )


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def stock_name_map(conn: sqlite3.Connection) -> dict[str, str]:
    out = {}
    for table, name_col in (("stock_universe", "stock_name"), ("stock_master", "stock_name")):
        if not table_exists(conn, table):
            continue
        try:
            for r in conn.execute(f"SELECT stock_code, {name_col} FROM {table} WHERE length(stock_code)=6"):
                out.setdefault(r[0], r[1])
        except sqlite3.Error:
            continue
    return out


def insert_event(conn: sqlite3.Connection, row: dict) -> None:
    cols = [
        "event_id", "source", "trigger_key", "trigger_name", "event_date",
        "available_date", "period", "entity_type", "entity_key", "sector_name",
        "stock_code", "value", "z_score", "mom_pct", "yoy_pct", "direction",
        "strength", "metadata_json",
    ]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO trigger_discovery_events ({",".join(cols)})
        VALUES ({",".join("?" for _ in cols)})
        """,
        [row.get(c) for c in cols],
    )


def insert_link(conn: sqlite3.Connection, row: dict) -> None:
    cols = [
        "event_id", "stock_code", "stock_name", "sector_name", "link_source",
        "confidence", "revenue_exposure_pct", "profit_exposure_pct", "cost_exposure_pct",
    ]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO trigger_discovery_stock_links ({",".join(cols)})
        VALUES ({",".join("?" for _ in cols)})
        """,
        [row.get(c) for c in cols],
    )


def build_quant_events(conn: sqlite3.Connection, start: str) -> int:
    if not table_exists(conn, "quant_major_indicator_series"):
        return 0
    catalog = {}
    if table_exists(conn, "quant_major_indicator_catalog"):
        catalog = {
            r["indicator_key"]: dict(r)
            for r in conn.execute("SELECT * FROM quant_major_indicator_catalog")
        }
    mappings: dict[str, list[sqlite3.Row]] = defaultdict(list)
    if table_exists(conn, "cafe_stock_indicator_mappings"):
        for r in conn.execute(
            """
            SELECT stock_code, stock_name, sector_name, indicator_key, confidence,
                   revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct
            FROM cafe_stock_indicator_mappings
            WHERE stock_code IS NOT NULL AND length(stock_code)=6
            """
        ):
            mappings[r["indicator_key"]].append(r)

    keys = conn.execute(
        """
        SELECT DISTINCT indicator_key, series_name
        FROM quant_major_indicator_series
        WHERE value IS NOT NULL
        ORDER BY indicator_key, series_name
        """
    ).fetchall()
    inserted = 0
    today = date.today().isoformat()
    for key in keys:
        rows = conn.execute(
            """
            SELECT period, value
            FROM quant_major_indicator_series
            WHERE indicator_key=? AND series_name=? AND value IS NOT NULL
            ORDER BY period
            """,
            (key["indicator_key"], key["series_name"]),
        ).fetchall()
        vals: list[float] = []
        by_period = {r["period"]: float(r["value"]) for r in rows}
        for i, r in enumerate(rows):
            available = month_available(r["period"])
            if not available or available < start or available > today:
                vals.append(float(r["value"]))
                continue
            val = float(r["value"])
            prev = float(rows[i - 1]["value"]) if i > 0 else None
            yoy_key = f"{str(r['period'])[:4]}-{str(r['period'])[5:7]}" if False else None
            if len(str(r["period"])) >= 7 and str(r["period"])[4] == "-":
                yoy_key = f"{int(str(r['period'])[:4]) - 1}{str(r['period'])[4:7]}"
            yoy_val = by_period.get(yoy_key or "")
            mom = (val / prev - 1) * 100 if prev not in (None, 0) else None
            yoy = (val / yoy_val - 1) * 100 if yoy_val not in (None, 0) else None
            mean = sum(vals[-24:]) / len(vals[-24:]) if vals[-24:] else None
            std = pd_std(vals[-24:]) if len(vals[-24:]) >= 6 else None
            z = (val - mean) / std if mean is not None and std else None
            direction = "up" if (mom or 0) > 0 else "down" if (mom or 0) < 0 else "flat"
            strength = max(abs(z or 0), abs(mom or 0) / 10, abs(yoy or 0) / 20)
            if strength < 1.2 and abs(mom or 0) < 8 and abs(yoy or 0) < 15:
                vals.append(val)
                continue
            cat = catalog.get(key["indicator_key"], {})
            eid = event_id(["quant", key["indicator_key"], key["series_name"], r["period"]])
            insert_event(conn, {
                "event_id": eid,
                "source": "quant_major_indicator_series",
                "trigger_key": key["indicator_key"],
                "trigger_name": cat.get("epic_indicator_name") or key["indicator_key"],
                "event_date": available,
                "available_date": available,
                "period": r["period"],
                "entity_type": "indicator",
                "entity_key": key["series_name"],
                "sector_name": cat.get("category"),
                "value": val,
                "z_score": z,
                "mom_pct": mom,
                "yoy_pct": yoy,
                "direction": direction,
                "strength": strength,
                "metadata_json": json.dumps({"series_name": key["series_name"]}, ensure_ascii=False),
            })
            for m in mappings.get(key["indicator_key"], []):
                insert_link(conn, {
                    "event_id": eid,
                    "stock_code": m["stock_code"],
                    "stock_name": m["stock_name"],
                    "sector_name": m["sector_name"],
                    "link_source": "cafe_stock_indicator_mappings",
                    "confidence": m["confidence"],
                    "revenue_exposure_pct": m["revenue_exposure_pct"],
                    "profit_exposure_pct": m["profit_exposure_pct"],
                    "cost_exposure_pct": m["cost_exposure_pct"],
                })
            inserted += 1
            vals.append(val)
    return inserted


def pd_std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(var)
    return std if std > 0 else None


def build_order_events(conn: sqlite3.Connection, start: str, names: dict[str, str]) -> int:
    if not table_exists(conn, "order_contracts"):
        return 0
    inserted = 0
    today = date.today().isoformat()
    for r in conn.execute(
        """
        SELECT stock_code, stock_name, rcept_no, rcept_dt, report_nm,
               contract_amount, revenue_ratio_pct, counterpart
        FROM order_contracts
        WHERE is_termination=0
          AND rcept_dt IS NOT NULL
          AND length(stock_code)=6
          AND COALESCE(revenue_ratio_pct,0) >= 10
        """
    ):
        available = norm_date(r["rcept_dt"])
        if not available or available < start or available > today:
            continue
        eid = event_id(["order_contract", r["rcept_no"], r["stock_code"]])
        ratio = float(r["revenue_ratio_pct"] or 0)
        insert_event(conn, {
            "event_id": eid,
            "source": "order_contracts",
            "trigger_key": "order_contract:revenue_ratio_ge10",
            "trigger_name": "수주공시 매출대비 10% 이상",
            "event_date": available,
            "available_date": available,
            "period": available[:7],
            "entity_type": "stock",
            "entity_key": r["stock_code"],
            "stock_code": r["stock_code"],
            "value": ratio,
            "direction": "up",
            "strength": min(ratio / 10, 10),
            "metadata_json": json.dumps({
                "rcept_no": r["rcept_no"],
                "report_nm": r["report_nm"],
                "counterpart": r["counterpart"],
                "contract_amount": r["contract_amount"],
            }, ensure_ascii=False),
        })
        insert_link(conn, {
            "event_id": eid,
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"] or names.get(r["stock_code"]),
            "link_source": "direct_stock_event",
            "confidence": 1.0,
            "revenue_exposure_pct": ratio,
        })
        inserted += 1
    return inserted


def build_backlog_events(conn: sqlite3.Connection, start: str, names: dict[str, str]) -> int:
    if not table_exists(conn, "dart_backlog_quarterly"):
        return 0
    rows = conn.execute(
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, backlog_amount_krw,
               backlog_confidence, source_rcept_dt, source_report_nm
        FROM dart_backlog_quarterly
        WHERE backlog_amount_krw IS NOT NULL AND length(stock_code)=6
        ORDER BY stock_code, fiscal_year, fiscal_quarter
        """
    ).fetchall()
    by_code: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_code[r["stock_code"]].append(r)
    inserted = 0
    today = date.today().isoformat()
    for code, vals in by_code.items():
        prev = None
        prev_conf = 0.0
        for r in vals:
            available = norm_date(r["source_rcept_dt"]) or quarter_available(r["fiscal_year"], r["fiscal_quarter"])
            cur = float(r["backlog_amount_krw"] or 0)
            conf = float(r["backlog_confidence"] or 0)
            comparable = bool(
                prev and prev > 0 and cur > 0
                and max(cur, prev) / min(cur, prev) <= 20.0
            )
            if start <= available <= today and comparable and conf >= 0.95 and prev_conf >= 0.95:
                growth = cur / prev - 1
                if growth >= 0.30:
                    eid = event_id(["backlog_growth", code, r["fiscal_year"], r["fiscal_quarter"]])
                    insert_event(conn, {
                        "event_id": eid,
                        "source": "dart_backlog_quarterly",
                        "trigger_key": "backlog:growth_qoq_ge30",
                        "trigger_name": "수주잔고 전분기 대비 30% 이상 증가",
                        "event_date": available,
                        "available_date": available,
                        "period": f"{r['fiscal_year']}Q{r['fiscal_quarter']}",
                        "entity_type": "stock",
                        "entity_key": code,
                        "stock_code": code,
                        "value": growth * 100,
                        "direction": "up",
                        "strength": min(growth * 3, 10),
                        "metadata_json": json.dumps({
                            "backlog_amount_krw": cur,
                            "prev_backlog_amount_krw": prev,
                            "source_report_nm": r["source_report_nm"],
                        }, ensure_ascii=False),
                    })
                    insert_link(conn, {
                        "event_id": eid,
                        "stock_code": code,
                        "stock_name": names.get(code),
                        "link_source": "direct_stock_event",
                        "confidence": conf,
                    })
                    inserted += 1
            prev = cur
            prev_conf = conf
    return inserted


def first_price(conn: sqlite3.Connection, code: str, after_date: str, max_gap_days: int = 10) -> sqlite3.Row | None:
    limit = (date.fromisoformat(after_date) + timedelta(days=max_gap_days)).isoformat()
    return conn.execute(
        """
        SELECT substr(date,1,10) AS dt, close
        FROM price_history
        WHERE stock_code=? AND date>? AND date<=? AND close>0
        ORDER BY date
        LIMIT 1
        """,
        (code, after_date, limit),
    ).fetchone()


def forward_price_path(conn: sqlite3.Connection, code: str, entry_date: str, horizon_days: int) -> list[sqlite3.Row]:
    target = (date.fromisoformat(entry_date) + timedelta(days=horizon_days)).isoformat()
    limit = (date.fromisoformat(target) + timedelta(days=10)).isoformat()
    return conn.execute(
        """
        SELECT substr(date,1,10) AS dt, close
        FROM price_history
        WHERE stock_code=? AND date>=? AND date<=? AND close>0
        ORDER BY date
        """,
        (code, entry_date, limit),
    ).fetchall()


def build_forward_returns(conn: sqlite3.Connection, limit_events: int | None = None) -> int:
    events = conn.execute(
        """
        SELECT event_id, available_date, stock_code
        FROM trigger_discovery_events
        WHERE stock_code IS NOT NULL
        UNION
        SELECT e.event_id, e.available_date, l.stock_code
        FROM trigger_discovery_events e
        JOIN trigger_discovery_stock_links l ON l.event_id=e.event_id
        ORDER BY available_date
        """
    ).fetchall()
    if limit_events:
        events = events[-limit_events:]
    inserted = 0
    for e in events:
        entry = first_price(conn, e["stock_code"], e["available_date"])
        if not entry:
            continue
        entry_close = float(entry["close"])
        for horizon in (20, 60, 120):
            path = forward_price_path(conn, e["stock_code"], entry["dt"], horizon)
            if len(path) < 2:
                continue
            target = date.fromisoformat(entry["dt"]) + timedelta(days=horizon)
            exits = [p for p in path if date.fromisoformat(p["dt"]) >= target]
            if not exits:
                continue
            exitp = exits[0]
            exit_close = float(exitp["close"])
            min_close = min(float(p["close"]) for p in path)
            conn.execute(
                """
                INSERT OR REPLACE INTO trigger_discovery_forward_returns
                (event_id, stock_code, horizon_days, entry_date, entry_close,
                 exit_date, exit_close, return_pct, max_drawdown_pct, fill_gap_days)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    e["event_id"],
                    e["stock_code"],
                    horizon,
                    entry["dt"],
                    entry_close,
                    exitp["dt"],
                    exit_close,
                    (exit_close / entry_close - 1) * 100,
                    (min_close / entry_close - 1) * 100,
                    (date.fromisoformat(entry["dt"]) - date.fromisoformat(e["available_date"])).days,
                ),
            )
            inserted += 1
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--limit-forward-events", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    init_tables(conn)
    conn.execute("DELETE FROM trigger_discovery_forward_returns")
    conn.execute("DELETE FROM trigger_discovery_stock_links")
    conn.execute("DELETE FROM trigger_discovery_events")
    names = stock_name_map(conn)

    counts = {
        "quant_events": build_quant_events(conn, args.start),
        "order_events": build_order_events(conn, args.start, names),
        "backlog_events": build_backlog_events(conn, args.start, names),
    }
    conn.commit()
    counts["forward_return_rows"] = build_forward_returns(conn, args.limit_forward_events)
    conn.commit()

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start": args.start,
        **counts,
        "tables": {
            "trigger_discovery_events": conn.execute("SELECT COUNT(*) FROM trigger_discovery_events").fetchone()[0],
            "trigger_discovery_stock_links": conn.execute("SELECT COUNT(*) FROM trigger_discovery_stock_links").fetchone()[0],
            "trigger_discovery_forward_returns": conn.execute("SELECT COUNT(*) FROM trigger_discovery_forward_returns").fetchone()[0],
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
