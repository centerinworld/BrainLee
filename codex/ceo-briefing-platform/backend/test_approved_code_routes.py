"""
approved_code_routes.py 회귀 테스트.

2026-09-17 handoff(AGENTIC_EXECUTION_REPAIR_HANDOFF_2026-09-17.md) 지적: rollback API가
code_jobs.rollback()의 반환 상태(ROLLBACK_FAILED 가능)를 확인하지 않고 무조건
"롤백됨" 알림을 예약했다. apply() 라우트는 이미 상태를 확인하고 있어 대조군으로 둔다.
"""

import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks

import approved_code_routes as routes
from approved_code_routes import Approval, apply, rollback, _execute_and_notify


class NotifyTelegramFailureLoggingTests(unittest.TestCase):
    """2026-09-18 실사용 중 재현: notify_telegram()도 send_final_approval_prompt()와
    같은 무음 실패 패턴이었다 - 실패해도 아무 흔적이 안 남아 원인 추적이
    불가능했다. 이제 로그에는 남아야 한다."""

    def test_send_failure_is_logged_not_silently_swallowed(self):
        with patch.object(routes, "_read_env", side_effect=lambda k: {"GOAL_INTAKE_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "C"}[k]), \
             patch.object(routes.urllib.request, "urlopen", side_effect=OSError("network down")), \
             self.assertLogs(routes._LOG, level="ERROR"):
            routes.notify_telegram("테스트 알림")  # 예외 없이 끝나야 한다(승인 절차를 막지 않음)


class RollbackNotificationTests(unittest.TestCase):
    def _call(self, fn, method_name, result):
        bg = BackgroundTasks()
        with patch.object(routes.code_jobs, method_name, return_value=result), \
             patch.object(routes, "notify_telegram") as mock_notify:
            fn(job_id="job1", req=Approval(fingerprint="a" * 64), background=bg,
               session={"username": "tester"})
        return mock_notify, bg

    def test_successful_rollback_notifies_success(self):
        mock_notify, bg = self._call(rollback, "rollback", {"status": "ROLLED_BACK"})
        # BackgroundTasks에 등록된 콜백을 직접 실행해 실제 호출 인자를 확인한다.
        for task in bg.tasks:
            task.func(*task.args, **task.kwargs)
        text = mock_notify.call_args[0][0]
        self.assertIn("롤백됨", text)
        self.assertNotIn("실패", text)

    def test_failed_rollback_does_not_claim_success(self):
        """2026-09-17 발견: 반환 status를 안 보고 무조건 '롤백됨'을 보냈다."""
        mock_notify, bg = self._call(rollback, "rollback", {"status": "ROLLBACK_FAILED", "error": "git apply 실패"})
        for task in bg.tasks:
            task.func(*task.args, **task.kwargs)
        text = mock_notify.call_args[0][0]
        self.assertIn("실패", text)
        self.assertNotIn("롤백됨 (job", text)

    def test_apply_route_already_gates_on_status(self):
        """대조군: apply() 라우트는 이미 상태를 확인하고 있어야 한다(회귀 방지)."""
        mock_notify, bg = self._call(apply, "apply_to_workspace", {"status": "APPLY_FAILED", "error": "x"})
        self.assertEqual(len(bg.tasks), 0)


class ReviewNotificationContentTests(unittest.TestCase):
    """2026-09-18 소유자 지적(1차): "어떤 내용을 수정/개선할건지 보내야 승인을 할 거
    같아" - 이전엔 검토 대기 알림에 "테스트: PASS"와 버튼만 있고 실제로 뭘
    바꾸는지가 없었다.
    2026-09-18 소유자 지적(2차, 실제 첫 실사용 후): "너가 보낸 메세지를 내가
    이해 못하겠는데? 뭘 수정하는지에 대한 내용을 적어 줘야지" - 원본 지시문과
    raw diff만으로는 여전히 못 읽겠다고 하셨다. execute()가 모델에게 받아 저장한
    평이한 한국어 요약(job['summary'])이 메시지 맨 앞에 오는지, 요약이 없을 때도
    안 죽고 대체 문구가 나오는지, 그리고 Telegram 4096바이트 상한을 넘기지
    않는지 확인한다."""

    def _job(self, diff, summary="calc.py의 add 함수가 두 수를 뺐던 걸 더하도록 고쳤습니다."):
        return {
            "status": "READY_FOR_REVIEW",
            "spec": {"provider": "claude", "instruction": "add() 함수의 뺄셈 버그를 고쳐라"},
            "files_changed": ["calc.py"],
            "tests": {"passed": True},
            "diff": diff,
            "diff_hash": "f" * 64,
            "summary": summary,
        }

    def test_review_message_includes_plain_summary_first_and_diff_as_detail(self):
        job = self._job("--- a/calc.py\n+++ b/calc.py\n-    return a - b\n+    return a + b\n")
        with patch.object(routes.code_jobs, "execute"), \
             patch.object(routes.code_jobs, "get", return_value=job), \
             patch.object(routes, "send_final_approval_prompt") as mock_prompt:
            _execute_and_notify("job1")
        text = mock_prompt.call_args[0][2]
        self.assertIn("두 수를 뺐던 걸 더하도록 고쳤습니다", text)
        self.assertIn("return a + b", text)
        self.assertLess(text.index("무엇이 바뀌나요"), text.index("--- 상세"))

    def test_review_message_falls_back_gracefully_when_model_gave_no_summary(self):
        job = self._job("--- a/calc.py\n+++ b/calc.py\n-    return a - b\n+    return a + b\n", summary="")
        with patch.object(routes.code_jobs, "execute"), \
             patch.object(routes.code_jobs, "get", return_value=job), \
             patch.object(routes, "send_final_approval_prompt") as mock_prompt:
            _execute_and_notify("job1")
        text = mock_prompt.call_args[0][2]
        self.assertIn("요약을 만들지 않음", text)

    def test_review_message_never_exceeds_telegram_byte_limit_even_with_huge_diff(self):
        huge_diff = "+line\n" * 5000  # 실제 diff보다 훨씬 큰 입력으로 잘림 로직을 강제한다
        job = self._job(huge_diff)
        with patch.object(routes.code_jobs, "execute"), \
             patch.object(routes.code_jobs, "get", return_value=job), \
             patch.object(routes, "send_final_approval_prompt") as mock_prompt:
            _execute_and_notify("job1")
        summary = mock_prompt.call_args[0][2]
        self.assertLessEqual(len(summary.encode("utf-8")), 4096)
        self.assertIn("전체 diff는 웹 대시보드에서 확인", summary)

    def test_review_message_does_not_crash_on_multibyte_truncation_boundary(self):
        """바이트 단위로 자르면 한글 문자 중간에서 잘릴 수 있다 - decode(errors='ignore')로
        예외 없이 안전하게 처리되는지 확인한다."""
        job = self._job("한글 " * 3000)
        with patch.object(routes.code_jobs, "execute"), \
             patch.object(routes.code_jobs, "get", return_value=job), \
             patch.object(routes, "send_final_approval_prompt") as mock_prompt:
            _execute_and_notify("job1")  # 예외 없이 끝나야 한다
        summary = mock_prompt.call_args[0][2]
        self.assertLessEqual(len(summary.encode("utf-8")), 4096)


if __name__ == "__main__":
    unittest.main()
