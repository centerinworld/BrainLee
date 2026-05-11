from __future__ import annotations

import contextlib
import fcntl
import os
import sqlite3
import time
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parent
STOCK_DB_PATH = BASE_DIR / "stock.db"
DB_WRITE_LOCK_PATH = Path("/tmp/stock_dashboard_db_write.lock")


def connect_stock_db(
    *,
    timeout: float = 30.0,
    row_factory=None,
    readonly: bool = False,
) -> sqlite3.Connection:
    """Open stock.db with consistent timeout/path settings.

    readonly=True uses SQLite URI mode so accidental writes fail early.
    """
    if readonly:
        uri = f"file:{STOCK_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=timeout)
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


@contextlib.contextmanager
def stock_db_write_lock(name: str, *, timeout: float = 5.0) -> Iterator[bool]:
    """Best-effort cross-process lock for long stock.db writers.

    Yields True when the lock is acquired. If the timeout expires, yields False
    so callers can skip non-critical work instead of competing for SQLite.
    """
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
                try:
                    yield True
                finally:
                    lock_file.seek(0)
                    lock_file.truncate()
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() - start >= timeout:
                    yield False
                    return
                time.sleep(0.25)
