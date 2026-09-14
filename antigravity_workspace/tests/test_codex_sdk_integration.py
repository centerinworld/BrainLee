"""Codex SDK 영속 실행 adapter의 네트워크 비의존 계약 테스트."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from execution import CodexSDKAdapter, ExecutionRequest
from memory.state_ledger import StateLedger


class _FakeThread:
    def __init__(self, thread_id="thread-1", response="SDK_RESULT"):
        self.id = thread_id
        self.response = response
        self.prompts = []
        self.run_kwargs = []

    def run(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.run_kwargs.append(kwargs)
        usage = SimpleNamespace(total=SimpleNamespace(
            input_tokens=11, output_tokens=7, total_tokens=18
        ))
        return SimpleNamespace(id="turn-1", final_response=self.response, usage=usage)


class _FakeCodex:
    def __init__(self):
        self.started = []
        self.resumed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def thread_start(self, **kwargs):
        thread = _FakeThread()
        self.started.append((kwargs, thread))
        return thread

    def thread_resume(self, session_id, **kwargs):
        thread = _FakeThread(thread_id=session_id, response="RESUMED_RESULT")
        self.resumed.append((session_id, kwargs, thread))
        return thread


class TestCodexSDKAdapter(unittest.TestCase):
    def setUp(self):
        sandbox_patch = patch("execution.codex_sdk.Sandbox", SimpleNamespace(read_only="read-only", workspace_write="workspace-write"))
        sandbox_patch.start()
        self.addCleanup(sandbox_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "ledger.sqlite3")
        self.artifacts = os.path.join(self.tmp.name, "artifacts")
        self.instances = []

        def factory():
            instance = _FakeCodex()
            self.instances.append(instance)
            return instance

        self.adapter = CodexSDKAdapter(
            ledger=StateLedger(self.db_path),
            artifact_dir=self.artifacts,
            codex_factory=factory,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, run_id="run-1", session_id=None):
        return ExecutionRequest(
            prompt="검증 요청",
            workspace=self.tmp.name,
            run_id=run_id,
            task_id="task-1",
            attempt_id=f"attempt-{run_id}",
            session_id=session_id,
        )

    def test_submit_persists_ids_usage_and_verified_artifact(self):
        result = self.adapter.submit(self._request())
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["session_id"], "thread-1")
        self.assertEqual(result["turn_id"], "turn-1")
        self.assertEqual(result["usage"]["total_tokens"], 18)
        self.assertTrue(Path(result["artifact_path"]).exists())
        self.assertEqual(self.adapter.reconcile("run-1")["status"], "SUCCEEDED")

    def test_same_terminal_run_is_idempotent(self):
        first = self.adapter.submit(self._request())
        second = self.adapter.submit(self._request())
        self.assertEqual(first["artifact_hash"], second["artifact_hash"])
        self.assertEqual(len(self.instances), 1)

    def test_resume_uses_recorded_session(self):
        self.adapter.submit(self._request())
        resumed = self.adapter.resume("run-1", "계속", run_id="run-2")
        self.assertEqual(resumed["status"], "SUCCEEDED")
        self.assertEqual(resumed["session_id"], "thread-1")
        self.assertEqual(self.instances[-1].resumed[0][0], "thread-1")

    def test_reconcile_detects_artifact_tampering(self):
        result = self.adapter.submit(self._request())
        Path(result["artifact_path"]).write_text("tampered", encoding="utf-8")
        reconciled = self.adapter.reconcile("run-1")
        self.assertEqual(reconciled["status"], "NEEDS_RECONCILIATION")

    def test_auth_error_is_preserved_as_waiting_auth(self):
        class AuthFailure:
            def __enter__(self):
                raise RuntimeError("reauthenticationRequired")
            def __exit__(self, *args):
                return False

        adapter = CodexSDKAdapter(
            ledger=StateLedger(self.db_path),
            artifact_dir=self.artifacts,
            codex_factory=lambda: AuthFailure(),
        )
        result = adapter.submit(self._request(run_id="auth-run"))
        self.assertEqual(result["status"], "WAITING_AUTH")

    def test_sol_is_explicit_on_start_and_resume(self):
        self.adapter.submit(self._request())
        self.assertEqual(self.instances[0].started[0][0]["model"], "gpt-5.6-sol")
        self.adapter.resume("run-1", "continue", run_id="next")
        self.assertEqual(self.instances[-1].resumed[0][1]["model"], "gpt-5.6-sol")

    def test_model_is_passed_to_run_not_only_thread_start(self):
        """2026-09-14 진단(D01): thread_start()에만 model을 넘기면 세션 기본값에만
        의존하게 된다 - Thread.run()도 per-turn model 인자를 받으므로 매 턴에도
        명시적으로 재전달해야 사용자 전역 config.toml 기본값이 끼어들 여지가 줄어든다."""
        self.adapter.submit(self._request())
        thread = self.instances[0].started[0][1]
        self.assertEqual(thread.run_kwargs[0].get("model"), "gpt-5.6-sol")

    def test_resolved_model_is_explicit_unknown_not_silent_none(self):
        """SDK의 TurnResult/Turn에는 실제 사용된 모델을 알려주는 필드가 없다(SDK 0.154.0
        확인) - resolved_model을 조용히 None으로 두면 "확인했는데 없다"와 "확인 안 함"을
        구분할 수 없다. 이유가 담긴 명시적 상태를 남겨야 한다."""
        result = self.adapter.submit(self._request())
        self.assertEqual(result["resolved_model"], "UNKNOWN_SDK_DOES_NOT_REPORT_MODEL")

    def test_non_sol_models_rejected_before_dispatch(self):
        for model in ("gpt-6-astra", "gpt-4o-mini"):
            with self.assertRaises(ValueError):
                ExecutionRequest(prompt="test", workspace=self.tmp.name, model=model)


if __name__ == "__main__":
    unittest.main()
