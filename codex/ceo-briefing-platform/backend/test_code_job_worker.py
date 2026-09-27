"""
CodeJobWorker 회귀 테스트 - 2026-09-17 handoff P1-2.

CodeJobs 자체의 claim_next_approved()/reconcile_stale_running() 로직은
test_approved_code_jobs.py에서 실제 SQLite로 검증한다. 여기서는 워커가 그 두
메서드를 매 틱마다 올바른 순서(정리 먼저, 그 다음 새 작업 점유·실행)로 부르는지,
그리고 폴링 루프 자체(start/stop, 예외로 죽지 않음)가 동작하는지만 Mock으로
확인한다.
"""
import time
import unittest
from unittest.mock import Mock, call

from services.code_job_worker import CodeJobWorker


class CodeJobWorkerTests(unittest.TestCase):
    def test_tick_reconciles_then_claims_and_executes_when_job_available(self):
        jobs = Mock()
        jobs.claim_next_approved.return_value = {"id": "code-123"}
        worker = CodeJobWorker(jobs, lease_seconds=300)
        worker._tick()
        jobs.reconcile_stale_running.assert_called_once_with(stale_after_seconds=300)
        jobs.claim_next_approved.assert_called_once_with(worker.worker_id, lease_seconds=300)
        jobs.execute.assert_called_once_with("code-123")

    def test_tick_does_not_execute_when_no_job_claimed(self):
        jobs = Mock()
        jobs.claim_next_approved.return_value = None
        worker = CodeJobWorker(jobs)
        worker._tick()
        jobs.execute.assert_not_called()

    def test_each_worker_gets_a_distinct_id(self):
        a = CodeJobWorker(Mock())
        b = CodeJobWorker(Mock())
        self.assertNotEqual(a.worker_id, b.worker_id)

    def test_loop_survives_tick_exceptions_and_stops_cleanly(self):
        """폴링 스레드 하나가 예외로 죽으면 안전망 전체가 사라진다 - 한 틱 실패는
        무시하고 다음 틱을 계속 시도해야 한다."""
        jobs = Mock()
        jobs.reconcile_stale_running.side_effect = [RuntimeError("boom"), None, None]
        jobs.claim_next_approved.return_value = None
        worker = CodeJobWorker(jobs, poll_interval=0.02)
        worker.start()
        try:
            deadline = time.time() + 2
            while jobs.reconcile_stale_running.call_count < 3 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(jobs.reconcile_stale_running.call_count, 3)
        finally:
            worker.stop()

    def test_start_is_idempotent(self):
        worker = CodeJobWorker(Mock(), poll_interval=5)
        worker.start()
        first_thread = worker._thread
        worker.start()
        self.assertIs(worker._thread, first_thread)
        worker.stop()


if __name__ == "__main__":
    unittest.main()
