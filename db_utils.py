from __future__ import annotations

import contextlib
import fcntl
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator


BASE_DIR = Path(__file__).resolve().parent
STOCK_DB_PATH = (BASE_DIR / "stock.db").resolve()
DB_WRITE_LOCK_PATH = Path("/tmp/stock_dashboard_db_write.lock")


def connect_stock_db(
    *,
    timeout: float = 30.0,
    row_factory=None,
    readonly: bool = False,
    wal: bool = False,
) -> Any:
    """Open the configured primary database with legacy call semantics.

    PostgreSQL is the operational primary after cutover. SQLite URI/read-only,
    WAL, and file-lock options remain available only for fallback deployments.
    """
    from config import IS_POSTGRES

    if IS_POSTGRES:
        # Lazy import avoids the db_compat -> db_utils fallback import cycle.
        from db_compat import PostgresCompatConnection

        conn = PostgresCompatConnection()
        if row_factory is not None:
            conn.row_factory = row_factory
        return conn
    if readonly:
        uri = f"file:{STOCK_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=timeout)
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys=ON")
    if wal and not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


_LOCK_HELD_ENV = "STOCK_DB_WRITE_LOCK_HELD"


@contextlib.contextmanager
def stock_db_write_lock(name: str, *, timeout: float = 5.0) -> Iterator[bool]:
    """Best-effort cross-process lock for long stock.db writers.

    Yields True when the lock is acquired. If the timeout expires, yields False
    so callers can skip non-critical work instead of competing for SQLite.

    An ancestor process (e.g. scheduler._run_job_safe) that already holds this
    lock while it runs a job may spawn a subprocess that calls this same
    function again (e.g. collect_broker_program_trading.py invoked via
    subprocess.run). Because flock() is scoped per open-file-description, the
    child's acquisition attempt would block on the parent's still-open lock
    for the child's own `timeout` seconds and then fail — a guaranteed
    self-deadlock, not real contention from another process. The parent marks
    STOCK_DB_WRITE_LOCK_HELD=1 in its environment (and thus the child's,
    since subprocess.run inherits os.environ by default) while it holds the
    lock; a child observing that marker treats the lock as already held by
    its own process tree and skips re-acquiring it.
    """
    from config import IS_POSTGRES

    # PostgreSQL handles concurrent writers transactionally; the legacy file
    # lock only serializes access to stock.db and must not gate PG ingestion.
    if IS_POSTGRES:
        yield True
        return
    if os.environ.get(_LOCK_HELD_ENV):
        yield True
        return
    DB_WRITE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with open(DB_WRITE_LOCK_PATH, "a+") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_file.seek(0)
                lock_file.truncate()
                lock_file.write(f"{os.getpid()} {name} {int(time.time())}\n")
                lock_file.flush()
                os.environ[_LOCK_HELD_ENV] = "1"
                try:
                    yield True
                finally:
                    os.environ.pop(_LOCK_HELD_ENV, None)
                    lock_file.seek(0)
                    lock_file.truncate()
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() - start >= timeout:
                    yield False
                    return
                time.sleep(0.25)
