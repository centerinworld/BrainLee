"""
Project Antigravity: 작업/주문/발행 상태 영속 원장 (A08)

이전에는 L1PMOwner.tasks, QuantTraderWorker.orders, ContentOrchestrator.published_reports가
파이썬 리스트에만 존재해 프로세스가 재시작되면 전부 사라졌다. 이 모듈은 SQLite 파일에
domain+record_key로 레코드를 저장해 재시작 후에도 남게 하고, 같은 record_key로 다시 쓰면
새 레코드를 만들지 않고 기존 레코드를 갱신(멱등)하도록 한다 - 재시작 후 중복 실행/발행을
막는 최소 단위다.
"""

import os
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_LEDGER_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_ledger.sqlite3")


class StateLedger:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_LEDGER_DB_PATH
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_records (
                domain TEXT NOT NULL,
                record_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (domain, record_key)
            )
        """)
        conn.commit()
        conn.close()

    def upsert(self, domain: str, record_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """domain+record_key로 레코드를 저장하거나, 이미 있으면 병합 갱신한다(멱등 - 새 레코드를 만들지 않음)."""
        now = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "SELECT payload_json FROM ledger_records WHERE domain=? AND record_key=?",
                (domain, record_key)
            )
            row = cur.fetchone()
            if row:
                merged = json.loads(row[0])
                merged.update(payload)
                conn.execute(
                    "UPDATE ledger_records SET payload_json=?, updated_at=? WHERE domain=? AND record_key=?",
                    (json.dumps(merged, ensure_ascii=False, default=str), now, domain, record_key)
                )
                conn.commit()
                return merged
            conn.execute(
                "INSERT INTO ledger_records (domain, record_key, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (domain, record_key, json.dumps(payload, ensure_ascii=False, default=str), now, now)
            )
            conn.commit()
            return payload
        finally:
            conn.close()

    def get(self, domain: str, record_key: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM ledger_records WHERE domain=? AND record_key=?",
                (domain, record_key)
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()

    def exists(self, domain: str, record_key: str) -> bool:
        return self.get(domain, record_key) is not None

    def list_domain(self, domain: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM ledger_records WHERE domain=? ORDER BY created_at ASC",
                (domain,)
            ).fetchall()
            return [json.loads(r[0]) for r in rows]
        finally:
            conn.close()

    def compare_and_update(self, domain, record_key, expected, patch):
        """Atomically check selected fields and merge a patch; return None on conflict."""
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json FROM ledger_records WHERE domain=? AND record_key=?",
                (domain, record_key),
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(row[0])
            if any(payload.get(key) != value for key, value in expected.items()):
                return None
            payload.update(patch)
            conn.execute(
                "UPDATE ledger_records SET payload_json=?, updated_at=? WHERE domain=? AND record_key=?",
                (json.dumps(payload, ensure_ascii=False, default=str), datetime.now().isoformat(), domain, record_key),
            )
            return payload
