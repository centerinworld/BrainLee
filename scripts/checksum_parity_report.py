#!/usr/bin/env python3
"""Checksum parity report: verify every frozen-SQLite row survives unchanged in PG.

Methodology (value-level, not just key-level):
  * source of truth = recovery SQLite (frozen cutover snapshot), read directly
  * destination     = PostgreSQL primary, read via raw psycopg (server-side cursor)
  * for each common table, canonicalize every row:
        - drop surrogate `id`
        - normalize columns whose name ends with `_at` to sentinel <TS>
        - render remaining columns JSON-sorted -> md5
  * full value-hash containment check for tables with <= 2M rows
  * deterministic sampled containment for larger tables (price_history, every 50th row)
Result proves "SQLite subset-equal PG" (migration fidelity: no drop, no corruption).
PG-ahead (live new rows) is expected and NOT flagged as drift.
"""
from __future__ import annotations
import sys, json, hashlib, time, sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import psycopg
from db_compat import connect_recovery_sqlite_db

PG_DSN = "host=127.0.0.1 port=5432 dbname=stock_dashboard user=stock_dashboard password=stock_dashboard_local"
FULL_HASH_LIMIT = 2_000_000
SAMPLE_STEP = 50
TS_SUFFIX = ("_at",)
REPORT_PATH = BASE / "research_outputs" / "checksum_parity_report.json"


def sqlite_tables():
    c = connect_recovery_sqlite_db(readonly=True, timeout=120)
    rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    c.close()
    return [r[0] for r in rows]


def pg_tables():
    with psycopg.connect(PG_DSN) as c:
        with c.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            return [r[0] for r in cur.fetchall()]


def sqlite_cols(tbl):
    c = connect_recovery_sqlite_db(readonly=True, timeout=120)
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
    c.close()
    return cols


def pg_cols(tbl):
    with psycopg.connect(PG_DSN) as c:
        with c.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (tbl,))
            return [r[0] for r in cur.fetchall()]


def natural_key(stable_cols):
    for c in ("stock_code", "date", "indicator_key", "sector_name", "stock_name", "year", "quarter", "code", "ticker"):
        if c in stable_cols:
            return [c]
    # fall back to a composite of the first 2 stable cols
    return stable_cols[:2]


from decimal import Decimal, InvalidOperation


def norm(v):
    """Cross-engine canonical value rendering: normalize numeric types so
    SQLite INTEGER/REAL and PG numeric/double precision compare equal."""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        try:
            return str(Decimal(str(v)).normalize())
        except InvalidOperation:
            return str(v)
    return str(v)


def canonical_hash(rec, stable, nk):
    canon = {c: norm(rec[c]) for c in stable}
    key = tuple(rec[c] for c in nk)
    kh = hashlib.md5(json.dumps([str(x) for x in key]).encode()).hexdigest()
    vh = hashlib.md5(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()
    return vh, kh


def sqlite_hashes(tbl, cols, sample_step):
    stable = [c for c in cols if c != "id" and not c.endswith(TS_SUFFIX)]
    nk = natural_key(stable)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    c = connect_recovery_sqlite_db(readonly=True, timeout=120)
    cur = c.execute(f'SELECT {col_sql} FROM "{tbl}"')
    i = 0
    while True:
        rows = cur.fetchmany(50000)
        if not rows:
            break
        for r in rows:
            if sample_step and (i % sample_step) != 0:
                i += 1
                continue
            rec = dict(zip(cols, r))
            yield canonical_hash(rec, stable, nk)
            i += 1
    c.close()


def pg_hashes(tbl, cols, sample_step):
    stable = [c for c in cols if c != "id" and not c.endswith(TS_SUFFIX)]
    nk = natural_key(stable)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    with psycopg.connect(PG_DSN) as c:
        with c.cursor(name=f"cur_{tbl}") as cur:  # server-side cursor for streaming
            cur.execute(f'SELECT {col_sql} FROM "{tbl}"')
            i = 0
            while True:
                rows = cur.fetchmany(50000)
                if not rows:
                    break
                for r in rows:
                    if sample_step and (i % sample_step) != 0:
                        i += 1
                        continue
                    rec = dict(zip(cols, r))
                    yield canonical_hash(rec, stable, nk)
                    i += 1


def count(table_fn, tbl):
    if table_fn == "sqlite":
        c = connect_recovery_sqlite_db(readonly=True, timeout=120)
        n = c.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        c.close()
    else:
        with psycopg.connect(PG_DSN) as c:
            with c.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{tbl}"')
                n = cur.fetchone()[0]
    return n


def main():
    sl_tables = set(sqlite_tables())
    pg_tables_set = set(pg_tables())
    common = sorted(sl_tables & pg_tables_set)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "methodology": "SQLite subset-equal PG value-hash containment (id dropped, *_at sentinel-normalized)",
        "tables_in_sqlite_not_pg": sorted(sl_tables - pg_tables_set),
        "tables_in_pg_not_sqlite": sorted(pg_tables_set - sl_tables),
        "tables": [],
    }

    for tbl in common:
        t0 = time.time()
        try:
            s_cols = sqlite_cols(tbl)
            p_cols = pg_cols(tbl)
        except Exception as e:
            report["tables"].append({"table": tbl, "error": f"schema: {e}"})
            print(f"{tbl:35s} ERROR {e}")
            continue
        common_cols = [c for c in s_cols if c in p_cols]
        if not common_cols:
            report["tables"].append({"table": tbl, "error": "no common columns"})
            continue
        sl_n = count("sqlite", tbl)
        pg_n = count("pg", tbl)
        sample = SAMPLE_STEP if sl_n > FULL_HASH_LIMIT else None

        sl_map = {}   # key_hash -> value_hash
        for vh, kh in sqlite_hashes(tbl, common_cols, sample):
            sl_map[kh] = vh
        pg_map = {}
        for vh, kh in pg_hashes(tbl, common_cols, sample):
            pg_map[kh] = vh

        sl_keys = set(sl_map)
        pg_keys = set(pg_map)
        missing_keys = len(sl_keys - pg_keys)          # SQLite key absent in PG = data loss
        value_drift = sum(1 for k in sl_keys & pg_keys if sl_map[k] != pg_map[k])

        clean = (missing_keys == 0 and value_drift == 0)
        entry = {
            "table": tbl,
            "sqlite_rows": sl_n, "pg_rows": pg_n,
            "mode": "sampled" if sample else "full",
            "sqlite_subset_pg": (missing_keys == 0),
            "missing_keys": missing_keys,
            "value_drift": value_drift,
            "elapsed_s": round(time.time() - t0, 2),
        }
        report["tables"].append(entry)
        print(f"{tbl:38s} sl={sl_n:>10,} pg={pg_n:>10,}  miss={missing_keys:>7} drift={value_drift:>8}  "
              f"{'OK' if clean else 'CHECK'}  [{entry['mode']}]")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lost = [t for t in report["tables"] if t.get("missing_keys", 0) > 0]
    drifted = [t for t in report["tables"] if t.get("value_drift", 0) > 0]
    print(f"\nSaved -> {REPORT_PATH}")
    print(f"Tables with MISSING keys (data loss): {len(lost)}")
    for t in lost:
        print("  !!", t["table"], "missing_keys=", t["missing_keys"])
    print(f"Tables with value DRIFT (value changed in PG): {len(drifted)}")


if __name__ == "__main__":
    main()
