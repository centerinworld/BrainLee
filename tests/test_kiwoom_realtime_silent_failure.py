"""키움 장중 실시간 피드의 '침묵 실패' 회귀 테스트 (2026-09-24).

실측 배경
---------
2026-09-22·09-23 은 KR 거래일이었다(`trading_calendar.is_kr_trading_day` → True).
그런데 토큰 발급이 `8050:IP가 등록되지 않았습니다`로 실패해 `ws_saved=0`이 6청크 전부였고,
`kiwoom_realtime_quote`/`minute_snapshot`/`tick_history` 워터마크는 09-21 15:30에서 멈췄다.
그럼에도 `data/collection_health.db`의 `collection_job_runs`에는 success 386(09-22)/387(09-23)건만
남았다 — 이틀치 데이터 공백이 무경보로 통과했다.

원인 3개 (각각 아래 테스트가 고정한다)
1. `_job_kiwoom_realtime`이 `ok=False`(token_fail 등)를 무시하고 0건을 더한 뒤 정상 반환
2. 같은 잡이 예외를 스스로 삼켜(`except: logger.error`) `_run_job_safe`가 항상 success 기록
3. `키움실시간스냅샷`이 `JOB_DATASET_KEYS`에 없어 `evaluate_job_outputs`가 `[]` → 계약 검증 0개

이 테스트는 라이브 DB에 쓰지 않는다: 원장은 임시 파일로, 계약 평가는 임시 sqlite 파일로 돌린다.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collection_health
import scheduler as scheduler_mod
from collection_health import (
    CONTRACT_BY_KEY,
    JOB_DATASET_KEYS,
    DatasetContract,
    evaluate_contract,
    evaluate_job_outputs,
)
from scheduler import CollectionScheduler
from scripts.audit_kiwoom_intraday_integrity import FeedSpec, check_feed, recent_trading_days

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
    """KiwoomCollector 대역 — 주어진 ws 응답을 순서대로 돌려준다."""

    responses: list[dict] = []
    calls: int = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def collect_realtime_snapshot(self, stock_codes, types=None, duration_sec=15):
        idx = min(type(self).calls, len(type(self).responses) - 1)
        type(self).calls += 1
        return dict(type(self).responses[idx])


class _FakeConn:
    def close(self) -> None:  # pragma: no cover - 호출 여부만 의미
        pass


@contextmanager
def _fake_write_lock(name, timeout=None):
    yield True


class KiwoomRealtimeSilentFailure(unittest.TestCase):
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
            patch.object(CollectionScheduler, "_get_all_market_codes", staticmethod(lambda conn: ["005930", "000660"])),
            patch.object(scheduler_mod, "evaluate_job_outputs", lambda name, now=None: []),
            patch("collectors.kiwoom_collector.KiwoomCollector", _FakeCollector),
        ]
        for p in self._patchers:
            p.start()
        _FakeCollector.calls = 0
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

    # ── 원인 1+2: 토큰 실패가 success로 둔갑하지 않는다 ─────────────────
    def test_token_failure_is_recorded_as_failed_run(self):
        _FakeCollector.responses = [{"ok": False, "reason": "token_fail"}]
        ok = scheduler_mod._run_job_safe("키움실시간스냅샷", self.sched._job_kiwoom_realtime)
        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(ok)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("token_fail", rows[0]["error"] or "")
        # 예외가 원장에 남았다는 사실 자체가 '삼킴' 제거의 증거
        self.assertNotEqual(rows[0]["status"], "success")

    # ── 성공 경로는 그대로 success이고 0건 카운터가 리셋된다 ───────────────
    def test_productive_cycle_stays_success_and_resets_counter(self):
        self.sched._kiwoom_rt_zero_cycles = 2
        _FakeCollector.responses = [{"ok": True, "saved": 7}]
        ok = scheduler_mod._run_job_safe("키움실시간스냅샷", self.sched._job_kiwoom_realtime)
        rows = self._ledger_rows()
        self.assertTrue(ok)
        self.assertEqual(rows[0]["status"], "success")
        self.assertEqual(self.sched._kiwoom_rt_zero_cycles, 0)

    # ── 원인 1: ok=True인데 3사이클 연속 0건이면 실패로 승격 ───────────────
    def test_three_consecutive_empty_cycles_raise(self):
        _FakeCollector.responses = [{"ok": True, "saved": 0}]
        self.sched._job_kiwoom_realtime()
        self.sched._job_kiwoom_realtime()
        self.assertEqual(self.sched._kiwoom_rt_zero_cycles, 2)
        with self.assertRaises(RuntimeError) as ctx:
            self.sched._job_kiwoom_realtime()
        self.assertIn("3사이클 연속", str(ctx.exception))
        self.assertEqual(self.sched._kiwoom_rt_zero_cycles, 0)

    # ── 원인 3: 장중 피드가 계약에 묶여 실제 공백이 stale로 드러난다 ────────
    def test_intraday_contract_is_wired_to_the_job(self):
        # `_save_realtime_snapshot`이 쓰는 세 저장 경로가 모두 계약에 묶여야 한다.
        # 하나만 묶으면 나머지 경로가 0행으로 멈춰도 잡은 success로 남는다.
        self.assertEqual(
            JOB_DATASET_KEYS.get("키움실시간스냅샷"),
            ("kiwoom_intraday", "kiwoom_intraday_quote", "kiwoom_intraday_tick"),
        )
        expected = {
            "kiwoom_intraday": ("kiwoom_minute_snapshot", "minute_ts"),
            "kiwoom_intraday_quote": ("kiwoom_realtime_quote", "updated_at"),
            "kiwoom_intraday_tick": ("kiwoom_tick_history", "event_ts"),
        }
        for key, (table, col) in expected.items():
            contract = CONTRACT_BY_KEY[key]
            self.assertEqual((contract.table, contract.source_date_col), (table, col))
            self.assertEqual(contract.cadence, "kr_daily")
            self.assertEqual(contract.ready_hour, 16)
        # 계약이 실제로 평가에 포함된다(예전에는 [] 였다)
        with patch.object(collection_health, "evaluate_contract", lambda c, now=None: {"key": c.key, "status": "x"}):
            outputs = evaluate_job_outputs("키움실시간스냅샷")
        self.assertEqual(
            [o["key"] for o in outputs],
            ["kiwoom_intraday", "kiwoom_intraday_quote", "kiwoom_intraday_tick"],
        )

    def _minute_db_with_last(self, last_minute_ts: str) -> Path:
        path = Path(self._tmp.name) / f"minute_{last_minute_ts[:10]}.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE kiwoom_minute_snapshot (stock_code TEXT, minute_ts TEXT, updated_at TEXT)")
        conn.executemany(
            "INSERT INTO kiwoom_minute_snapshot VALUES (?,?,?)",
            [("005930", last_minute_ts, last_minute_ts), ("000660", last_minute_ts, last_minute_ts)],
        )
        conn.commit()
        conn.close()
        return path

    def test_contract_flags_the_two_trading_day_outage(self):
        """09-24(추석 휴장) 기준 기대치는 '최근 거래일 09-23' — 09-21 데이터는 이틀 지연이다."""
        path = self._minute_db_with_last("2026-09-21 15:30:00")
        contract = DatasetContract(
            "kiwoom_intraday", "키움 실시간(장중 분봉)", path, "kiwoom_minute_snapshot", "minute_ts",
            ready_hour=16, collected_at_col="updated_at",
        )
        result = evaluate_contract(contract, now=datetime(2026, 9, 24, 16, 5))
        self.assertEqual(result["expected_as_of"], "2026-09-23")
        self.assertEqual(result["lag"], 2)
        self.assertEqual(result["status"], "stale")
        self.assertIn("stale:2>0", result["issues"])

    def test_contract_is_healthy_when_the_last_trading_day_is_present(self):
        path = self._minute_db_with_last("2026-09-23 15:30:00")
        contract = DatasetContract(
            "kiwoom_intraday", "키움 실시간(장중 분봉)", path, "kiwoom_minute_snapshot", "minute_ts",
            ready_hour=16, collected_at_col="updated_at",
        )
        result = evaluate_contract(contract, now=datetime(2026, 9, 24, 16, 5))
        self.assertEqual(result["lag"], 0)
        self.assertEqual(result["status"], "healthy")

    # ── 휴장일 오판 방지: 게이트 판정 + 스킵 로그 ──────────────────────────
    def test_session_gate_separates_holiday_from_trading_day(self):
        self.assertTrue(CollectionScheduler._kiwoom_rt_in_session(datetime(2026, 9, 23, 10, 0)))   # 수 거래일
        self.assertFalse(CollectionScheduler._kiwoom_rt_in_session(datetime(2026, 9, 24, 10, 0)))  # 추석 휴장
        self.assertFalse(CollectionScheduler._kiwoom_rt_in_session(datetime(2026, 9, 23, 16, 0)))  # 장 마감 후

    def test_gate_logs_only_on_state_change(self):
        with self.assertLogs("scheduler", level="INFO") as captured:
            self.sched._log_kiwoom_rt_gate("closed")
            self.sched._log_kiwoom_rt_gate("closed")
            self.sched._log_kiwoom_rt_gate("in_session")
        self.assertEqual(len(captured.records), 2)
        self.assertIn("장외/휴장", captured.records[0].getMessage())
        self.assertIn("장중 진입", captured.records[1].getMessage())


class KiwoomIntradayIntegrityAudit(unittest.TestCase):
    """`scripts/audit_kiwoom_intraday_integrity.py`의 행 단위 점검 회귀.

    계약(collection_health)은 '마지막 데이터가 최근 거래일인가'만 보므로,
    자연키 중복/NaN/비장시간은 이 감사가 담당한다.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "feed.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.execute("CREATE TABLE feed (stock_code TEXT, ts TEXT, val TEXT)")

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _spec(self, **kwargs) -> FeedSpec:
        base: dict = dict(
            table="feed", ts_col="ts", key_expr="stock_code || '|' || ts || '|' || val",
            price_cols=("val",), contract_key=None,
        )
        base.update(kwargs)
        return FeedSpec(**base)

    def test_recent_trading_days_skips_holiday_and_weekend(self):
        # 2026-09-24(추석 전날)·09-25(추석)·주말은 휴장 → 최근 거래일은 09-23부터
        days = recent_trading_days(now=datetime(2026, 9, 24, 12, 0), count=3)
        self.assertEqual([d.isoformat() for d in days], ["2026-09-23", "2026-09-22", "2026-09-21"])

    def test_clean_session_is_ok(self):
        self.conn.executemany(
            "INSERT INTO feed VALUES (?,?,?)",
            [("005930", "2026-09-23 10:00:00", "100"), ("000660", "2026-09-23 10:00:00", "101")],
        )
        self.conn.commit()
        result = check_feed(self.conn, self._spec(), [date(2026, 9, 23)], now=datetime(2026, 9, 23, 16, 5))
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["per_day"][0]["off_session_rows"], 0)
        self.assertEqual(result["nan_rows"], 0)

    def test_off_session_nan_and_duplicate_are_flagged(self):
        self.conn.executemany(
            "INSERT INTO feed VALUES (?,?,?)",
            [
                ("005930", "2026-09-23 08:59:00", "100"),   # 장 개시 전
                ("000660", "2026-09-23 10:00:00", "NaN"),   # NaN 표기
                ("035420", "2026-09-23 10:00:00", "102"),
                ("035420", "2026-09-23 10:00:00", "102"),   # 자연키 완전 중복
            ],
        )
        self.conn.commit()
        result = check_feed(self.conn, self._spec(), [date(2026, 9, 23)], now=datetime(2026, 9, 23, 16, 5))
        self.assertFalse(result["ok"])
        self.assertEqual(result["nan_rows"], 1)
        self.assertTrue(any(i.startswith("off_session:2026-09-23") for i in result["issues"]), result["issues"])
        self.assertTrue(any(i.startswith("duplicate_grain:2026-09-23") for i in result["issues"]), result["issues"])

    def test_intra_second_repeats_are_tolerated_but_spikes_are_not(self):
        # REG 초기 스냅샷 재전송으로 같은 초·같은 가격 반복은 정상(실측 기준선 31~33%) → 비율이 임계 미만이면 통과
        normal = [("005930", "2026-09-23 10:00:00", "100")] * 2 + [
            ("000660", "2026-09-23 10:00:00", str(101 + i)) for i in range(8)
        ]
        self.conn.executemany("INSERT INTO feed VALUES (?,?,?)", normal)
        self.conn.commit()
        spec = self._spec(allow_intra_second_repeats=True)
        result = check_feed(self.conn, spec, [date(2026, 9, 23)], now=datetime(2026, 9, 23, 16, 5))
        self.assertEqual(result["latest_dup_rows"], 1)
        self.assertTrue(result["ok"], result["issues"])

        # 같은 날 동일 페이로드 반복이 다수를 차지하면(임계 45% 초과) 이상으로 본다
        self.conn.executemany("INSERT INTO feed VALUES (?,?,?)", [("035420", "2026-09-23 10:00:00", "102")] * 10)
        self.conn.commit()
        result = check_feed(self.conn, spec, [date(2026, 9, 23)], now=datetime(2026, 9, 23, 16, 5))
        self.assertGreater(result["latest_dup_ratio"], 0.45)
        self.assertFalse(result["ok"])
        self.assertTrue(any(i.startswith("duplicate_ratio_high:") for i in result["issues"]), result["issues"])


if __name__ == "__main__":
    unittest.main()
