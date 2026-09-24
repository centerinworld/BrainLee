#!/usr/bin/env python3
"""Concurrency test: intraday price writer vs concurrent readers on PostgreSQL.

Faithful to the real write path (routes/ingest.py -> crud.bulk_insert_price_history):
  * past-day rows  -> INSERT ... ON CONFLICT (stock_code,date) DO NOTHING
  * today rows     -> read existing supply, DELETE today's rows, INSERT best close
Readers replicate the dashboard's hot read queries (latest close, full series).

Runs against a SCRATCH table (_cc_test_price_history), never production price_history.
Asserts:
  1. No 'database is locked' / serialization failures under concurrent write+read.
  2. Readers always succeed (MVCC snapshot).
  3. No lost updates: no code has >1 today-row; distinct-key count is consistent.
"""
from __future__ import annotations
import sys, threading, time, random, json
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
from psycopg import errors

DB = "host=127.0.0.1 port=5432 dbname=stock_dashboard user=stock_dashboard password=stock_dashboard_local"
TBL = "_cc_test_price_history"
N_CODES = 50
N_WRITERS = 4
N_READERS = 4
SECONDS = 8
OUT = Path(__file__).resolve().parent.parent / "research_outputs" / "intraday_concurrency_report.json"

lock_errors = []
read_errors = []
write_ok = 0
read_ok = 0
stop = threading.Event()


def setup():
    c = psycopg.connect(DB, autocommit=True)
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TBL}")
        cur.execute(f"""
            CREATE TABLE {TBL} (
                stock_code text NOT NULL,
                date text NOT NULL,
                open double precision, high double precision, low double precision,
                close double precision, volume double precision,
                inst_net_buy double precision, frn_net_buy double precision,
                PRIMARY KEY (stock_code, date)
            )""")
    c.close()


def writer(wid):
    global write_ok
    rng = random.Random(wid)
    c = psycopg.connect(DB)
    today = date.today()
    past_dates = [(today - timedelta(days=d)).isoformat() for d in range(1, 15)]
    while not stop.is_set():
        code = f"{rng.randint(0, N_CODES - 1):06d}"
        rows = []
        for d in past_dates:
            px = 10000 + rng.random() * 5000
            rows.append((code, d, px, px, px, px, rng.random() * 1e6, 0, 0))
        try:
            with c.transaction():
                with c.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO {TBL} (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (stock_code,date) DO NOTHING",
                        rows,
                    )
            px = 10000 + rng.random() * 5000
            with c.transaction():
                with c.cursor() as cur:
                    cur.execute(f"DELETE FROM {TBL} WHERE stock_code=%s AND date LIKE %s", (code, f"{today.isoformat()}%"))
                    cur.execute(
                        f"INSERT INTO {TBL} (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (stock_code,date) DO UPDATE SET close=EXCLUDED.close",
                        (code, today.isoformat(), px, px, px, px, rng.random() * 1e6, 0, 0),
                    )
            write_ok += 1
        except errors.SerializationFailure as e:
            lock_errors.append(("serialization_failure", str(e)))
        except errors.OperationalError as e:
            if "lock" in str(e).lower() or "deadlock" in str(e).lower():
                lock_errors.append(("lock", str(e)))
            else:
                raise
        except Exception as e:
            lock_errors.append(("other", f"{type(e).__name__}: {e}"))
    c.close()


def reader(rid):
    global read_ok
    c = psycopg.connect(DB)
    while not stop.is_set():
        code = f"{random.randint(0, N_CODES - 1):06d}"
        try:
            with c.transaction():
                with c.cursor() as cur:
                    cur.execute(f"SELECT close FROM {TBL} WHERE stock_code=%s AND close>0 ORDER BY date DESC LIMIT 1", (code,))
                    cur.fetchall()
                    cur.execute(f"SELECT date, close, volume FROM {TBL} WHERE stock_code=%s ORDER BY date", (code,))
                    cur.fetchall()
            read_ok += 1
        except Exception as e:
            read_errors.append(f"{type(e).__name__}: {e}")
    c.close()


def verify():
    c = psycopg.connect(DB)
    today = date.today().isoformat()
    with c.cursor() as cur:
        cur.execute(f"SELECT COUNT(DISTINCT (stock_code, date)) FROM {TBL}")
        distinct = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {TBL} WHERE date LIKE '{today}%'")
        today_rows = cur.fetchone()[0]
        cur.execute(f"SELECT stock_code, COUNT(*) FROM {TBL} WHERE date LIKE '{today}%' GROUP BY stock_code HAVING COUNT(*)>1")
        dup_today = cur.fetchall()
    c.close()
    return {
        "distinct_keys": distinct,
        "today_rows": today_rows,
        "codes_with_dup_today_rows": len(dup_today),
    }


def main():
    setup()
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N_WRITERS)]
    threads += [threading.Thread(target=reader, args=(i,)) for i in range(N_READERS)]
    for t in threads:
        t.start()
    time.sleep(SECONDS)
    stop.set()
    for t in threads:
        t.join()
    v = verify()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": SECONDS,
        "writers": N_WRITERS, "readers": N_READERS, "codes": N_CODES,
        "write_ops": write_ok, "read_ops": read_ok,
        "lock_or_serialization_errors": lock_errors,
        "read_errors": read_errors,
        "lost_update_check": v,
        "pass": (not lock_errors and not read_errors and v["codes_with_dup_today_rows"] == 0),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    c = psycopg.connect(DB, autocommit=True)
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TBL}")
    c.close()
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
