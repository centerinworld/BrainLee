import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from services import telegram_approval_poller as tap


class TokenStoreTests(unittest.TestCase):
    """2026-09-17 handoff P0-2: job_id/diff_hash를 콜백에 직접 담으면 64바이트
    상한을 넘는다(실측 108바이트) - 짧은 1회용 토큰으로 대체했다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher_dir = patch.object(tap, "_STATE_DIR", Path(self.tmp.name))
        patcher_db = patch.object(tap, "_STATE_DB", Path(self.tmp.name) / "tokens.sqlite3")
        patcher_dir.start()
        patcher_db.start()
        self.addCleanup(patcher_dir.stop)
        self.addCleanup(patcher_db.stop)

    def test_token_round_trip_and_single_use(self):
        token = tap.mint_approval_token("code-abc", "f" * 64)
        job_id, diff_hash, reason = tap._resolve_and_consume_token(token)
        self.assertEqual((job_id, diff_hash, reason), ("code-abc", "f" * 64, ""))
        # 같은 토큰 재사용(중복 클릭/재전송)은 거부돼야 한다.
        job_id2, diff_hash2, reason2 = tap._resolve_and_consume_token(token)
        self.assertIsNone(job_id2)
        self.assertIn("이미 처리된", reason2)

    def test_unknown_token_rejected(self):
        job_id, _, reason = tap._resolve_and_consume_token("0" * 16)
        self.assertIsNone(job_id)
        self.assertIn("알 수 없거나 만료", reason)

    def test_expired_token_rejected(self):
        token = tap.mint_approval_token("code-abc", "f" * 64)
        with patch.object(tap.time, "time", return_value=time.time() + tap._TOKEN_TTL_SECONDS + 1):
            job_id, _, reason = tap._resolve_and_consume_token(token)
        self.assertIsNone(job_id)
        self.assertIn("유효기간", reason)

    def test_offset_persists_across_restart(self):
        """2026-09-17 발견: offset이 메모리에만 있어 재시작마다 과거 콜백을 다시 받아
        올 수 있었다 - 이제 같은 SQLite 파일에 영속화해 재시작 후에도 이어간다."""
        poller1 = tap.TelegramApprovalPoller(code_jobs=Mock())
        poller1._offset = 0
        poller1._save_offset(42)
        poller2 = tap.TelegramApprovalPoller(code_jobs=Mock())
        self.assertEqual(poller2._offset, 42)

    def test_send_final_approval_prompt_callback_data_within_telegram_limit(self):
        """Telegram InlineKeyboardButton.callback_data 공식 상한은 1-64바이트다.
        job_id(37자)+diff_hash(64자)를 그대로 담으면 108바이트로 초과했다(2026-09-17
        발견) - 토큰 치환 후 실제로 보내는 payload가 상한 안에 드는지 확인한다."""
        sent = {}

        def fake_urlopen(req, timeout=10):
            sent["url"] = req.full_url
            sent["data"] = req.data
            return _FakeResponse(b"{}")

        with patch.object(tap, "_read_env", side_effect=lambda k: {"GOAL_INTAKE_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "C"}[k]), \
             patch.object(tap.urllib.request, "urlopen", side_effect=fake_urlopen):
            tap.send_final_approval_prompt("code-" + "a" * 32, "f" * 64, "요약")
        import urllib.parse as up
        body = up.parse_qs(sent["data"].decode())
        keyboard = json.loads(body["reply_markup"][0])
        callback_data = keyboard["inline_keyboard"][0][0]["callback_data"]
        self.assertLessEqual(len(callback_data.encode("utf-8")), 64)
        self.assertTrue(callback_data.startswith("apply:"))

    def test_send_failure_is_logged_not_silently_swallowed(self):
        """2026-09-18 실사용 중 재현: 1차 승인은 서버에 정상 기록되고 토큰도
        발급됐는데(mint_approval_token까지는 실행됨) 실제 텔레그램에는 아무것도
        오지 않은 사고가 있었다. 원인이 urlopen 실패였는지조차 알 방법이 없었던
        건 여기서 예외를 그냥 삼켰기 때문 - 이제 최소한 로그에는 남아야 한다."""
        with patch.object(tap, "_read_env", side_effect=lambda k: {"GOAL_INTAKE_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "C"}[k]), \
             patch.object(tap.urllib.request, "urlopen", side_effect=OSError("network down")), \
             self.assertLogs(tap._LOG, level="ERROR") as logs:
            tap.send_final_approval_prompt("code-" + "a" * 32, "f" * 64, "요약")  # 예외 없이 끝나야 한다(승인 절차를 막지 않음)
        self.assertTrue(any("code-" + "a" * 32 in line for line in logs.output))


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class HandleCallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher_dir = patch.object(tap, "_STATE_DIR", Path(self.tmp.name))
        patcher_db = patch.object(tap, "_STATE_DB", Path(self.tmp.name) / "tokens.sqlite3")
        patcher_dir.start()
        patcher_db.start()
        self.addCleanup(patcher_dir.stop)
        self.addCleanup(patcher_db.stop)
        self.jobs = Mock()
        self.poller = tap.TelegramApprovalPoller(code_jobs=self.jobs)
        self.poller.token = "BOTTOKEN"
        self.poller.allowed_chat_id = "12345"
        self._api_calls = []
        patcher_api = patch.object(self.poller, "_api", side_effect=self._fake_api)
        patcher_api.start()
        self.addCleanup(patcher_api.stop)

    def _fake_api(self, method, payload):
        self._api_calls.append((method, payload))
        return {"ok": True}

    def _cq(self, data, chat_id="12345"):
        return {"id": "cbid1", "data": data, "message": {"chat": {"id": chat_id}, "message_id": 999}}

    def test_missing_allowed_chat_id_fails_closed(self):
        """2026-09-17 발견: TELEGRAM_CHAT_ID가 비어 있으면 `if self.allowed_chat_id and ...`
        조건 자체가 거짓이 돼 아무나 승인 가능했다 - 이제 설정 누락 자체를 거부한다."""
        self.poller.allowed_chat_id = ""
        token = tap.mint_approval_token("code-x", "f" * 64)
        self.poller._handle_callback(self._cq(f"apply:{token}"))
        self.jobs.apply_to_workspace.assert_not_called()
        self.assertIn("설정 누락", self._api_calls[0][1]["text"])

    def test_wrong_chat_id_rejected(self):
        token = tap.mint_approval_token("code-x", "f" * 64)
        self.poller._handle_callback(self._cq(f"apply:{token}", chat_id="99999"))
        self.jobs.apply_to_workspace.assert_not_called()

    def test_malformed_callback_data_rejected(self):
        self.poller._handle_callback(self._cq("not-a-valid-token"))
        self.jobs.apply_to_workspace.assert_not_called()

    def test_successful_apply_reports_success(self):
        self.jobs.apply_to_workspace.return_value = {"status": "APPLIED_TO_WORKSPACE"}
        token = tap.mint_approval_token("code-x", "f" * 64)
        self.poller._handle_callback(self._cq(f"apply:{token}"))
        self.jobs.apply_to_workspace.assert_called_once_with("code-x", "f" * 64, approved_by="owner_via_telegram")
        answer_text = self._api_calls[0][1]["text"]
        self.assertIn("적용 완료", answer_text)

    def test_apply_failed_status_is_not_reported_as_success(self):
        """2026-09-17 발견(handoff): apply_to_workspace()는 실패해도 예외를 던지지 않고
        status="APPLY_FAILED"를 정상 반환한다 - 예외 여부만으로 판정하면 실패가
        성공으로 보고된다. status 필드를 직접 확인해야 한다."""
        self.jobs.apply_to_workspace.return_value = {"status": "APPLY_FAILED", "error": "diff 불일치"}
        token = tap.mint_approval_token("code-x", "f" * 64)
        self.poller._handle_callback(self._cq(f"apply:{token}"))
        answer_text = self._api_calls[0][1]["text"]
        self.assertNotIn("적용 완료", answer_text)
        self.assertIn("실패", answer_text)

    def test_replayed_token_after_success_does_not_reapply(self):
        """재시작 후 Telegram이 같은 업데이트를 다시 보내는 상황을 재현: 이미 소비된
        토큰으로 다시 콜백이 오면 apply_to_workspace를 또 호출하면 안 된다."""
        self.jobs.apply_to_workspace.return_value = {"status": "APPLIED_TO_WORKSPACE"}
        token = tap.mint_approval_token("code-x", "f" * 64)
        cq = self._cq(f"apply:{token}")
        self.poller._handle_callback(cq)
        self.poller._handle_callback(cq)  # replay
        self.assertEqual(self.jobs.apply_to_workspace.call_count, 1)

    def test_exception_from_apply_is_reported_as_failure_not_success(self):
        self.jobs.apply_to_workspace.side_effect = RuntimeError("boom")
        token = tap.mint_approval_token("code-x", "f" * 64)
        self.poller._handle_callback(self._cq(f"apply:{token}"))
        answer_text = self._api_calls[0][1]["text"]
        self.assertNotIn("적용 완료", answer_text)


if __name__ == "__main__":
    unittest.main()
