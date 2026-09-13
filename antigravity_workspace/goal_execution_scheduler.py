"""
Project Antigravity: 목표 실행 스케줄러 (완전 자동화 2단계 - "접수" 다음의 "실행")

소유자 지시(2026-09-12): "받았다까지만 하지 말고 실행도 가능하도록 해."

goal_intake_daemon.py는 텔레그램으로 들어온 목표를 state_ledger의 "goal_intake" 도메인에
PENDING으로 기록하고 "접수했습니다"까지만 응답한다(그 이상을 동기로 하면 텔레그램 응답이
몇 초 안에 와야 하는데 실제 파이프라인 실행은 몇 분 걸릴 수 있어 응답이 막힌다). 이 모듈은
그 뒤를 잇는 별도 프로세스로, "goal_intake" 도메인을 주기적으로 폴링해 아직 실행되지 않은
태스크를 L1PMOwner.execute_handoff()로 실제 실행하고, 결과를 다시 텔레그램으로 알린다.

중복 실행 방지: SQLite 원자적 상태 전이로 PENDING 작업을 선점한다.
중단된 RUNNING 작업은 자동 재실행하지 않으며 별도 조정이 필요하다.
알림은 전송 전에 SENDING을 기록한다. 불명확한 전송 결과는 재전송하지 않고
NEEDS_RECONCILIATION으로 남긴다. 외부 전송의 exactly-once는 보장하지 않는다.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agents.l1_pm_owner import L1PMOwner
from goal_intake_daemon import GoalIntakeDaemon
from memory.state_ledger import StateLedger

logger = logging.getLogger("goal_execution_scheduler")

DEFAULT_POLL_INTERVAL_SECONDS = 30


def _format_completion_message(task: Dict[str, Any], qa_report: Dict[str, Any]) -> str:
    verdict = "파이프라인 종료·목표 검증 필요" if task.get("status") == "COMPLETED" else "실패"
    notes = " / ".join(qa_report.get("notes", [])) or "상세 없음"
    return (
        f"[실행 결과: {verdict}] {task.get('title', task.get('task_id'))}\n"
        f"상태: {task.get('status')}\n"
        f"{notes}"
    )


class GoalExecutionScheduler:
    def __init__(
        self,
        pm_owner: Optional[L1PMOwner] = None,
        ledger: Optional[StateLedger] = None,
        intake_daemon: Optional[GoalIntakeDaemon] = None,
        notify_fn: Optional[Callable[[str, str], bool]] = None,
    ):
        self.pm_owner = pm_owner or L1PMOwner(auto_heal=True)
        # L1PMOwner가 내부에서 자체 StateLedger()를 새로 열므로, 스케줄러도 명시적으로
        # 같은 ledger를 주입받지 않으면 기본 DB 파일 경로가 같아 실질적으로 같은 저장소를
        # 보게 된다 - 다만 테스트에서는 반드시 둘 다 같은 인스턴스를 주입해야 한다.
        self.ledger = ledger or self.pm_owner.ledger
        self.intake_daemon = intake_daemon or GoalIntakeDaemon(ledger=self.ledger, pm_owner=self.pm_owner)
        self.notify_fn = notify_fn or self.intake_daemon.send_ack

    def _pending_intake_entries(self) -> List[Dict[str, Any]]:
        """아직 완료 알림을 보내지 않은 goal_intake 레코드만 후보로 삼는다."""
        return [
            entry for entry in self.ledger.list_domain("goal_intake")
            if not entry.get("notified_at")
        ]

    async def _process_entry(self, entry: Dict[str, Any]) -> None:
        task_id = entry.get("task_id")
        chat_id = entry.get("chat_id")
        if not task_id:
            logger.warning(f"task_id 없는 goal_intake 레코드 무시: {entry}")
            return

        task = self.ledger.get("tasks", task_id)
        if not task:
            logger.warning(f"'tasks' 도메인에 없는 task_id({task_id}) - 아직 파싱 직후 미기록일 수 있음, 다음 폴링에 재시도")
            return

        status = task.get("status")
        if status == "PENDING":
            task = self.ledger.compare_and_update("tasks", task_id, {"status": "PENDING"}, {
                "status": "RUNNING", "attempt_id": uuid.uuid4().hex,
                "started_at": datetime.now().isoformat(),
            })
            if task is None:
                return
            try:
                task = await self.pm_owner.execute_handoff(task)
            except Exception as e:
                logger.error(f"태스크 실행 중 예외({task_id}): {e}")
                task["status"] = "FAILED"
                task["error"] = str(e)
                self.ledger.upsert("tasks", task_id, task)
        elif status not in ("COMPLETED", "FAILED"):
            logger.info(f"태스크 '{task_id}' 상태({status})가 아직 종결되지 않아 이번 폴링에서는 알림을 보류합니다")
            return

        if task.get("status") not in ("COMPLETED", "FAILED"):
            self.ledger.upsert("tasks", task_id, task)
            return
        self.ledger.upsert("tasks", task_id, task)
        qa_report = self.pm_owner.execution_qa(task)
        self.ledger.upsert("tasks", task_id, {"qa_report": qa_report})

        if not chat_id or not self.notify_fn:
            return
        current = self.ledger.get("goal_intake", task_id)
        if current.get("notified_at") or current.get("notification_state") in (
            "SENDING", "SENT", "NEEDS_RECONCILIATION"
        ):
            return
        claimed = self.ledger.compare_and_update("goal_intake", task_id, {
            "notified_at": None, "notification_state": current.get("notification_state"),
        }, {"notification_state": "SENDING", "notification_started_at": datetime.now().isoformat()})
        if claimed is None:
            return
        try:
            sent = self.notify_fn(chat_id, _format_completion_message(task, qa_report))
        except Exception:
            sent = False
            logger.exception("Completion delivery result unknown for %s", task_id)
        patch = {
            "notification_state": "SENT" if sent else "NEEDS_RECONCILIATION",
            "final_status": task.get("status"),
            "qa_passed": qa_report.get("qa_passed"),
        }
        if sent:
            patch["notified_at"] = datetime.now().isoformat()
        self.ledger.upsert("goal_intake", task_id, patch)

    async def run_once(self) -> int:
        """대기 중인 goal_intake 레코드를 한 번 순회해 실행/알림한다. 처리한 개수를 반환한다."""
        entries = self._pending_intake_entries()
        for entry in entries:
            await self._process_entry(entry)
        return len(entries)

    def run_forever(self, poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS):
        logger.info("목표 실행 스케줄러 시작 (goal_intake 도메인 폴링)")
        while True:
            try:
                asyncio.run(self.run_once())
            except Exception as e:
                logger.error(f"실행 스케줄러 폴링 오류: {e}")
            time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if os.getenv("ENABLE_LEGACY_GOAL_INTAKE_EXECUTOR") != "1":
        raise SystemExit(
            "구형 텔레그램 goal_intake 실행기는 기본 비활성입니다. "
            "웹 5단계 strict_agi_orchestrator를 사용하세요."
        )
    GoalExecutionScheduler().run_forever()
