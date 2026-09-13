"""
goal_execution_scheduler 회귀 테스트: "접수"(goal_intake_daemon) 다음의 "실행" 단계.

소유자 지시(2026-09-12): "받았다까지만 하지 말고 실행도 가능하도록 해." 이 스케줄러는
state_ledger의 goal_intake 도메인을 폴링해 아직 실행/알림되지 않은 태스크를
L1PMOwner.execute_handoff()로 실제 실행하고 텔레그램으로 완료를 알린다. 실제 네트워크
호출(텔레그램/LLM) 없이 pm_owner와 notify_fn을 모두 stub/mock으로 대체해 검증한다.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from goal_execution_scheduler import GoalExecutionScheduler
from memory.state_ledger import StateLedger


class TestGoalExecutionScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_db.close()
        self.ledger = StateLedger(db_path=self.tmp_db.name)

        self.pm_owner = MagicMock()
        self.pm_owner.ledger = self.ledger
        self.pm_owner.execute_handoff = AsyncMock()
        self.pm_owner.execution_qa = MagicMock(return_value={"qa_passed": True, "notes": ["정상 완수"]})

        self.notify_fn = MagicMock(return_value=True)

        self.scheduler = GoalExecutionScheduler(
            pm_owner=self.pm_owner,
            ledger=self.ledger,
            intake_daemon=MagicMock(),
            notify_fn=self.notify_fn,
        )

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    async def test_pending_task_is_executed_and_notified_once(self):
        self.ledger.upsert("tasks", "T1", {"task_id": "T1", "title": "테스트 태스크", "status": "PENDING"})
        self.ledger.upsert("goal_intake", "T1", {"task_id": "T1", "chat_id": "555"})

        completed = {"task_id": "T1", "title": "테스트 태스크", "status": "COMPLETED"}
        self.pm_owner.execute_handoff.return_value = completed

        processed = await self.scheduler.run_once()

        self.assertEqual(processed, 1)
        self.pm_owner.execute_handoff.assert_awaited_once()
        self.notify_fn.assert_called_once()
        chat_id_arg, message_arg = self.notify_fn.call_args.args
        self.assertEqual(chat_id_arg, "555")
        self.assertIn("테스트 태스크", message_arg)

        entry = self.ledger.get("goal_intake", "T1")
        self.assertTrue(entry.get("notified_at"))
        self.assertEqual(entry.get("final_status"), "COMPLETED")

    async def test_already_notified_entry_is_never_reprocessed(self):
        self.ledger.upsert("tasks", "T2", {"task_id": "T2", "status": "COMPLETED"})
        self.ledger.upsert("goal_intake", "T2", {
            "task_id": "T2", "chat_id": "555", "notified_at": "2026-09-12T00:00:00"
        })

        processed = await self.scheduler.run_once()

        self.assertEqual(processed, 0)
        self.pm_owner.execute_handoff.assert_not_awaited()
        self.notify_fn.assert_not_called()

    async def test_task_missing_from_tasks_domain_is_skipped_without_crashing(self):
        self.ledger.upsert("goal_intake", "T3", {"task_id": "T3", "chat_id": "555"})

        processed = await self.scheduler.run_once()

        self.assertEqual(processed, 1)  # 후보로는 집혔으나
        self.pm_owner.execute_handoff.assert_not_awaited()
        self.notify_fn.assert_not_called()
        self.assertFalse(self.ledger.get("goal_intake", "T3").get("notified_at"))

    async def test_already_completed_task_is_notified_without_re_executing(self):
        """실행은 끝났지만(프로세스 재시작 등으로) 알림만 못 보낸 경우 재실행 없이 알림만 보낸다."""
        self.ledger.upsert("tasks", "T4", {"task_id": "T4", "title": "이미 끝난 태스크", "status": "COMPLETED"})
        self.ledger.upsert("goal_intake", "T4", {"task_id": "T4", "chat_id": "777"})

        processed = await self.scheduler.run_once()

        self.assertEqual(processed, 1)
        self.pm_owner.execute_handoff.assert_not_awaited()
        self.notify_fn.assert_called_once()
        self.assertEqual(self.ledger.get("goal_intake", "T4").get("final_status"), "COMPLETED")

    async def test_running_task_is_left_for_next_poll_without_notifying(self):
        """실행 도중 프로세스가 죽어 RUNNING으로 남은 태스크는 함부로 재실행하지 않는다."""
        self.ledger.upsert("tasks", "T5", {"task_id": "T5", "status": "RUNNING"})
        self.ledger.upsert("goal_intake", "T5", {"task_id": "T5", "chat_id": "555"})

        await self.scheduler.run_once()

        self.pm_owner.execute_handoff.assert_not_awaited()
        self.notify_fn.assert_not_called()
        self.assertFalse(self.ledger.get("goal_intake", "T5").get("notified_at"))

    async def test_execute_handoff_exception_is_caught_and_reported_as_failed(self):
        self.ledger.upsert("tasks", "T6", {"task_id": "T6", "title": "예외 태스크", "status": "PENDING"})
        self.ledger.upsert("goal_intake", "T6", {"task_id": "T6", "chat_id": "555"})
        self.pm_owner.execute_handoff.side_effect = RuntimeError("파이프라인 폭발")
        self.pm_owner.execution_qa.return_value = {"qa_passed": False, "notes": ["QA 실패: 파이프라인 폭발"]}

        await self.scheduler.run_once()

        self.notify_fn.assert_called_once()
        chat_id_arg, message_arg = self.notify_fn.call_args.args
        self.assertIn("실패", message_arg)
        stored = self.ledger.get("tasks", "T6")
        self.assertEqual(stored["status"], "FAILED")
        self.assertIn("파이프라인 폭발", stored["error"])

    def seed_completed(self):
        self.ledger.upsert("tasks", "N", {"task_id": "N", "status": "COMPLETED"})
        self.ledger.upsert("goal_intake", "N", {"task_id": "N", "chat_id": "555"})

    async def test_false_delivery_is_not_marked_sent_or_blindly_retried(self):
        self.seed_completed()
        self.notify_fn.return_value = False
        await self.scheduler.run_once()
        await self.scheduler.run_once()
        entry = self.ledger.get("goal_intake", "N")
        self.assertFalse(entry.get("notified_at"))
        self.assertEqual(entry["notification_state"], "NEEDS_RECONCILIATION")
        self.notify_fn.assert_called_once()

    async def test_delivery_exception_retains_unknown_outcome(self):
        self.seed_completed()
        self.notify_fn.side_effect = TimeoutError("unknown delivery")
        await self.scheduler.run_once()
        self.assertEqual(self.ledger.get("goal_intake", "N")["notification_state"], "NEEDS_RECONCILIATION")

    async def test_interrupted_delivery_is_not_resent(self):
        self.seed_completed()
        self.ledger.upsert("goal_intake", "N", {"notification_state": "SENDING"})
        await self.scheduler.run_once()
        self.notify_fn.assert_not_called()

    async def test_competing_schedulers_execute_once(self):
        import asyncio
        self.ledger.upsert("tasks", "C", {"task_id": "C", "status": "PENDING"})
        self.ledger.upsert("goal_intake", "C", {"task_id": "C", "chat_id": "555"})
        async def finish(task):
            await asyncio.sleep(0)
            return {**task, "status": "COMPLETED"}
        self.pm_owner.execute_handoff.side_effect = finish
        other = GoalExecutionScheduler(pm_owner=self.pm_owner, ledger=self.ledger,
                                       intake_daemon=MagicMock(), notify_fn=self.notify_fn)
        await asyncio.gather(self.scheduler.run_once(), other.run_once())
        self.pm_owner.execute_handoff.assert_awaited_once()
        self.notify_fn.assert_called_once()

    async def test_nonterminal_result_is_persisted_without_notification(self):
        self.ledger.upsert("tasks", "Q", {"task_id": "Q", "status": "PENDING"})
        self.ledger.upsert("goal_intake", "Q", {"task_id": "Q", "chat_id": "555"})
        self.pm_owner.execute_handoff.return_value = {"task_id": "Q", "status": "WAITING_QUOTA"}
        await self.scheduler.run_once()
        self.notify_fn.assert_not_called()
        self.assertEqual(self.ledger.get("tasks", "Q")["status"], "WAITING_QUOTA")

    async def test_pipeline_end_is_not_announced_as_goal_verified(self):
        self.seed_completed()
        await self.scheduler.run_once()
        self.assertIn("목표 검증 필요", self.notify_fn.call_args.args[1])

    def test_atomic_claim_across_connections(self):
        from concurrent.futures import ThreadPoolExecutor
        self.ledger.upsert("tasks", "P", {"task_id": "P", "status": "PENDING"})
        def claim(n):
            ledger = StateLedger(db_path=self.tmp_db.name)
            return ledger.compare_and_update("tasks", "P", {"status": "PENDING"},
                                             {"status": "RUNNING", "owner": n})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))
        self.assertEqual(sum(result is not None for result in results), 1)


if __name__ == "__main__":
    unittest.main()
