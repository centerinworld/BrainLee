"""키움 대량체결(ka00190) '침묵 실패' 회귀 테스트 (2026-09-24).

실측 배경
---------
2026-09-22·09-23 은 KR 거래일이었다(`trading_calendar.is_kr_trading_day` → True).
그 이틀 동안 `data/collection_health.db`의 `collection_job_runs`에는 `키움대량체결`
success 79건(09-22 09:00:39 ~ 09-23 15:21:06)이 남았지만, 정작
`kiwoom_large_trade_rank` 는 **0행**이었다(`MAX(snapshot_at)` = NULL).
수집량 0이 무경보로 success 처리된, `키움실시간스냅샷`과 동일한 침묵 실패 클래스다.

원인 3개 (각각 아래 테스트가 고정한다)
1. `_job_kiwoom_large_trade_rank`가 `ok=False`(토큰 실패 등)를 그대로 통과시켰다
2. `ok=True, saved=0`(ka00190 필터/응답 필드 불일치)도 0건으로 더한 뒤 정상 반환했다
3. 같은 잡이 예외를 스스로 삼켰고(`except: logger.error`), `키움대량체결`이
   `JOB_DATASET_KEYS`에 없어 `evaluate_job_outputs`가 `[]` → 계약 검증 0개였다

라이브 DB에는 쓰지 않는다: 원장은 임시 파일, 계약 평가는 임시 sqlite 파일로 돌린다.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collection_health
import scheduler as scheduler_mod
from collection_health import CONTRACT_BY_KEY, JOB_DATASET_KEYS, DatasetContract, evaluate_contract, evaluate_job_outputs
from scheduler import CollectionScheduler

LEDGER_DDL = """
CREATE TABLE collection_job_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds REAL,
    error TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE dataset_health_snapshot (
    snapshot_key TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class _FakeCollector:
    """KiwoomCollector 대역 — 주어진 ka00190 응답을 순서대로 돌려준다."""

    responses: list[dict] = []
    calls: int = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def fetch_large_trade_rank(self, rank_type: str = "buy", **kwargs) -> dict:
        idx = min(type(self).calls, len(type(self).responses) - 1)
        type(self).calls += 1
        payload = dict(type(self).responses[idx])
        payload.setdefault("rank_type", rank_type)
        return payload


class _FakeConn:
    def close(self) -> None:  # pragma: no cover - 호출 여부만 의미
        pass


@contextmanager
def _fake_write_lock(name, timeout=None):
    yield True


class KiwoomLargeTradeRankSilentFailure(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.ledger = Path(self._tmp.name) / "ledger.db"
        conn = sqlite3.connect(self.ledger)
        conn.executescript(LEDGER_DDL)
        conn.commit()
        conn.close()
        self._patchers = [
            patch.object(collection_health, "LEDGER_DB", self.ledger),
            # 라이브 stock.db writer 락 파일을 건드리지 않는다.
            patch.object(scheduler_mod, "stock_db_write_lock", _fake_write_lock),
            patch.object(scheduler_mod, "connect_stock_db", lambda *a, **kw: _FakeConn()),
            patch.object(scheduler_mod, "evaluate_job_outputs", lambda name, now=None: []),
            patch("collectors.kiwoom_collector.KiwoomCollector", _FakeCollector),
        ]
        for p in self._patchers:
            p.start()
        _FakeCollector.calls = 0
        _FakeCollector.responses = [{"ok": True, "saved": 20}]
        self.sched = CollectionScheduler()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def _ledger_rows(self):
        conn = sqlite3.connect(self.ledger)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT status,error,details_json FROM collection_job_runs ORDER BY run_id"
            ).fetchall()
        finally:
            conn.close()

    # ── 원인 1+3: 토큰 실패가 success로 둔갑하지 않는다 ────────────────────
    def test_token_failure_is_recorded_as_failed_run(self):
        _FakeCollector.responses = [{"ok": False, "reason": "token_fail"}]
        ok = scheduler_mod._run_job_safe("키움대량체결", self.sched._job_kiwoom_large_trade_rank)
        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(ok)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("token_fail", rows[0]["error"] or "")

    # ── 원인 2+3: ok=True·saved=0 이 2사이클 연속이면 실패로 승격 ────────────
    def test_two_consecutive_zero_row_cycles_are_promoted_to_failure(self):
        _FakeCollector.responses = [{"ok": True, "saved": 0, "return_code": "1511"}]
        # 1회차: 일시적 빈 응답은 넘긴다(카운터만 증가)
        self.sched._job_kiwoom_large_trade_rank()
        self.assertEqual(self.sched._kiwoom_ltr_zero_cycles, 1)
        # 2회차: 승격 → _run_job_safe 가 failed 로 기록하고 예외를 삼키지 않는다
        ok = scheduler_mod._run_job_safe("키움대량체결", self.sched._job_kiwoom_large_trade_rank)
        rows = self._ledger_rows()
        self.assertFalse(ok)
        self.assertEqual(rows[-1]["status"], "failed")
        self.assertIn("2사이클 연속", rows[-1]["error"] or "")
        self.assertEqual(self.sched._kiwoom_ltr_zero_cycles, 0)

    # ── 성공 경로는 그대로 success 이고 0건 카운터가 리셋된다 ────────────────
    def test_productive_cycle_stays_success_and_resets_counter(self):
        self.sched._kiwoom_ltr_zero_cycles = 1
        _FakeCollector.responses = [{"ok": True, "saved": 30}]
        ok = scheduler_mod._run_job_safe("키움대량체결", self.sched._job_kiwoom_large_trade_rank)
        rows = self._ledger_rows()
        self.assertTrue(ok)
        self.assertEqual(rows[0]["status"], "success")
        self.assertEqual(self.sched._kiwoom_ltr_zero_cycles, 0)

    # ── 한쪽(매수/매도)만 실패해도 조용히 넘어가지 않는다 ───────────────────
    def test_partial_failure_is_not_silent(self):
        _FakeCollector.responses = [
            {"ok": True, "saved": 20},
            {"ok": False, "reason": "HTTP 500"},
        ]
        ok = scheduler_mod._run_job_safe("키움대량체결", self.sched._job_kiwoom_large_trade_rank)
        rows = self._ledger_rows()
        self.assertFalse(ok)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("HTTP 500", rows[0]["error"] or "")

    # ── 원인 3: 대량체결이 계약에 묶여 실제 0행 공백이 드러난다 ──────────────
    def test_large_trade_rank_is_wired_to_a_contract(self):
        self.assertEqual(JOB_DATASET_KEYS.get("키움대량체결"), ("kiwoom_large_trade_rank",))
        contract = CONTRACT_BY_KEY["kiwoom_large_trade_rank"]
        self.assertEqual((contract.table, contract.source_date_col), ("kiwoom_large_trade_rank", "snapshot_at"))
        self.assertEqual(contract.cadence, "kr_daily")
        self.assertEqual(contract.ready_hour, 16)
        with patch.object(collection_health, "evaluate_contract", lambda c, now=None: {"key": c.key, "status": "x"}):
            outputs = evaluate_job_outputs("키움대량체결")
        self.assertEqual([o["key"] for o in outputs], ["kiwoom_large_trade_rank"])

    def _rank_db_with_last(self, last_snapshot: str) -> Path:
        path = Path(self._tmp.name) / f"rank_{last_snapshot[:10]}.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE kiwoom_large_trade_rank ("
            "snapshot_at TEXT NOT NULL, rank_type TEXT NOT NULL, market_type TEXT NOT NULL,"
            "rank_no INTEGER NOT NULL, stock_code TEXT, stock_name TEXT, raw_json TEXT NOT NULL,"
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
            "PRIMARY KEY (snapshot_at, rank_type, market_type, rank_no))"
        )
        conn.executemany(
            "INSERT INTO kiwoom_large_trade_rank (snapshot_at, rank_type, market_type, rank_no, raw_json)"
            " VALUES (?,?,?,?,?)",
            [(last_snapshot, "buy", "000", 1, "{}"), (last_snapshot, "sell", "000", 1, "{}")],
        )
        conn.commit()
        conn.close()
        return path

    def _rank_contract(self, path: Path) -> DatasetContract:
        return DatasetContract(
            "kiwoom_large_trade_rank", "키움 대량체결 순위(원본)", path,
            "kiwoom_large_trade_rank", "snapshot_at", ready_hour=16, collected_at_col="created_at",
        )

    def test_contract_flags_the_two_trading_day_outage(self):
        """09-24(추석 휴장) 기준 기대치는 '최근 거래일 09-23' — 09-21 스냅샷은 이틀 지연이다."""
        result = evaluate_contract(self._rank_contract(self._rank_db_with_last("2026-09-21 15:20:00")),
                                   now=datetime(2026, 9, 24, 16, 5))
        self.assertEqual(result["expected_as_of"], "2026-09-23")
        self.assertEqual(result["lag"], 2)
        self.assertEqual(result["status"], "stale")
        self.assertIn("stale:2>0", result["issues"])

    def test_contract_is_healthy_when_the_last_trading_day_is_present(self):
        result = evaluate_contract(self._rank_contract(self._rank_db_with_last("2026-09-23 15:30:00")),
                                   now=datetime(2026, 9, 24, 16, 5))
        self.assertEqual(result["lag"], 0)
        self.assertEqual(result["status"], "healthy")

    def test_contract_surfaces_a_completely_empty_table(self):
        """09-22·09-23 라이브 상태(테이블 0행)가 'healthy'로 지나가지 않는다."""
        empty = Path(self._tmp.name) / "rank_empty.db"
        conn = sqlite3.connect(empty)
        conn.execute(
            "CREATE TABLE kiwoom_large_trade_rank (snapshot_at TEXT, rank_no INTEGER, created_at TEXT)"
        )
        conn.commit()
        conn.close()
        result = evaluate_contract(self._rank_contract(empty), now=datetime(2026, 9, 24, 16, 5))
        self.assertEqual(result["source_as_of"], None)
        self.assertIn("source_date_missing", result["issues"])
        self.assertNotEqual(result["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
