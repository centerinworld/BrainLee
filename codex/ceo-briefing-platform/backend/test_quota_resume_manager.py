import tempfile
import concurrent.futures
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from quota_resume_manager import QuotaResumeManager


class QuotaResumeManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = QuotaResumeManager(Path(self.tmp.name) / "state.json", monitor_interval=3600)
        self.manager._launch = lambda task_id: None

    def tearDown(self):
        self.manager.stop()
        self.tmp.cleanup()

    @staticmethod
    def probes(codex_ready=False, claude_ready=False, qwen_ready=True):
        return {
            "codex": {"auth_ready": codex_ready, "status": "AVAILABLE_QUOTA_UNKNOWN" if codex_ready else "WAITING_AUTH", "note": "test"},
            "claude": {"auth_ready": claude_ready, "status": "AVAILABLE_QUOTA_UNKNOWN" if claude_ready else "WAITING_AUTH", "note": "test"},
            "qwen": {"auth_ready": qwen_ready, "status": "AVAILABLE_LOCAL" if qwen_ready else "UNAVAILABLE", "note": "test"},
            "deepseek": {"auth_ready": False, "status": "WAITING_AUTH", "note": "test"},
        }

    def apply_probes(self, values):
        state = self.manager._load()
        for name, value in values.items():
            state["providers"][name].update(value)
        self.manager._save(state)
        self.manager.refresh_providers = lambda: self.manager._load()["providers"]

    def test_observed_limit_creates_waiting_quota_with_future_retry(self):
        status = self.manager.record_failure("codex", "Rate limit reached; try again later")
        provider = self.manager._load()["providers"]["codex"]
        self.assertEqual("WAITING_QUOTA", status)
        self.assertTrue(provider["quota_observed"])
        self.assertGreater(datetime.fromisoformat(provider["reset_at"]), datetime.now().astimezone())
        self.assertEqual("observed_provider_error", provider["evidence_source"])

    def test_fallback_checkpoint_waits_for_frontier_review(self):
        self.apply_probes(self.probes())
        task = self.manager.dispatch("검증 작업")
        with patch.object(self.manager, "_run_qwen", return_value="실제 Qwen 결과"):
            self.manager._run_task(task["task_id"])
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual("READY_FOR_FRONTIER_REVIEW", saved["status"])
        self.assertEqual("PENDING_FRONTIER_REVIEW", saved["verdict"])
        self.assertTrue(Path(saved["artifact_path"]).exists())
        self.assertEqual(64, len(saved["artifact_hash"]))

    def test_frontier_provider_reviews_existing_checkpoint(self):
        self.apply_probes(self.probes())
        task = self.manager.dispatch("검토 재개 작업")
        with patch.object(self.manager, "_run_qwen", return_value="체크포인트"):
            self.manager._run_task(task["task_id"])

        self.apply_probes(self.probes(codex_ready=True))
        with patch.object(self.manager, "_run_codex", return_value={"content": "Codex 검토 결과", "session_id": "session-1"}):
            self.manager._run_task(task["task_id"])
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual("FRONTIER_REVIEW_COMPLETE", saved["status"])
        self.assertEqual("REVIEW_COMPLETE", saved["verdict"])
        self.assertEqual("session-1", saved["session_id"])

    def test_primary_result_does_not_claim_independent_review_or_merge(self):
        self.apply_probes(self.probes(codex_ready=True))
        task = self.manager.dispatch("Codex 작업")
        with patch.object(self.manager, "_run_codex", return_value={"content": "결과", "session_id": "session-2"}):
            self.manager._run_task(task["task_id"])
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual("RESULT_READY", saved["status"])
        self.assertEqual("별도 검토는 수행하지 않음", saved["stage3_summary"])
        self.assertNotIn("MERGE", saved["verdict"])

    def test_interrupted_task_is_recovered_for_retry(self):
        task = self.manager.dispatch("중단 복구")
        self.manager._update_task(task["task_id"], status="RUNNING_PRIMARY")
        recovered = QuotaResumeManager(Path(self.tmp.name) / "state.json", monitor_interval=3600)
        try:
            saved = recovered.get_task(task["task_id"])
            self.assertEqual("WAITING_RETRY", saved["status"])
            self.assertIsNotNone(saved["next_retry_at"])
        finally:
            recovered.stop()

    def test_concurrent_status_writes_do_not_collide(self):
        probe = {"auth_ready": True, "status": "AVAILABLE_QUOTA_UNKNOWN", "note": "test"}
        with patch.object(self.manager, "_probe_codex", return_value=probe), \
             patch.object(self.manager, "_probe_claude", return_value=probe), \
             patch.object(self.manager, "_probe_qwen", return_value={"auth_ready": True, "status": "AVAILABLE_LOCAL", "note": "test"}), \
             patch("quota_resume_manager._read_secret", return_value=""):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.manager.status(), range(32)))
        self.assertEqual(32, len(results))
        self.assertTrue(all(result["status"] == "success" for result in results))
        self.assertIsInstance(self.manager._load()["providers"], dict)


if __name__ == "__main__":
    unittest.main()
