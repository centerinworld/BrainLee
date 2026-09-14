import tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Load definitions without constructing the production singleton or starting its monitor.
# This prevents tests from reading/writing production state or calling real providers.
import ast
import types
_source = Path(__file__).with_name("strict_agi_orchestrator.py")
_tree = ast.parse(_source.read_text())
assert isinstance(_tree.body[-2], ast.Assign)
assert _tree.body[-2].targets[0].id == "strict_agi_orchestrator"
assert isinstance(_tree.body[-1], ast.Expr)
_tree.body = _tree.body[:-2]
_module = types.ModuleType("strict_agi_under_test")
_module.__file__ = str(_source)
exec(compile(_tree, str(_source), "exec"), _module.__dict__)
StrictAGIOrchestrator = _module.StrictAGIOrchestrator


class StrictOrchestratorTests(unittest.TestCase):
    def setUp(self):
        for obj, attr in ((_module.subprocess, "run"), (_module, "urlopen")):
            guard = patch.object(obj, attr, side_effect=AssertionError("Unmocked external I/O"))
            guard.start()
            self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = StrictAGIOrchestrator(Path(self.tmp.name) / "state.json", 999)

    def tearDown(self):
        self.manager.stop_event.set()
        self.tmp.cleanup()

    def wait_idle(self):
        for _ in range(100):
            if not self.manager.active:
                return
            time.sleep(.01)

    def test_retry_parses_absolute_clock_time_instead_of_blind_five_hours(self):
        """2026-09-14 진단(D03): Claude가 실제로 보내는 "resets 9:40am (Asia/Seoul)" 같은
        절대 시각은 예전 정규식(상대 시간만 인식)이 못 잡아 전부 현재+5시간으로 떨어졌다.
        실측(오늘 세션)으로 확인됨: Claude가 이미 다시 쓸 수 있는데도 그 임의의 5시간
        기본값 때문에 next_retry_at이 몇 시간이나 더 뒤로 잡혀 있었다."""
        with patch.object(_module, "now", return_value=datetime(2026, 9, 14, 20, 0, 0, tzinfo=timezone.utc)):
            r, confirmed = self.manager._retry("You've hit your limit · resets 9:40am (Asia/Seoul)")
        self.assertTrue(confirmed)
        self.assertEqual((r.hour, r.minute), (9, 40))
        # 20:00에 "9:40am"을 보면 오늘 9:40은 이미 지났으므로 다음날로 넘어가야 한다.
        self.assertEqual(r.date(), datetime(2026, 9, 15).date())

    def test_retry_unparseable_message_uses_short_unconfirmed_backoff(self):
        with patch.object(_module, "now", return_value=datetime(2026, 9, 14, 20, 0, 0, tzinfo=timezone.utc)):
            r, confirmed = self.manager._retry("something went wrong, no timing info at all")
        self.assertFalse(confirmed)
        self.assertLessEqual(r - datetime(2026, 9, 14, 20, 0, 0, tzinfo=timezone.utc), timedelta(minutes=30))

    def test_failure_note_distinguishes_confirmed_from_estimated_reset(self):
        ready = {n: {"auth_ready": True, "status": "AVAILABLE", "reset_at": None} for n in ("codex", "gemini", "qwen", "deepseek", "claude")}
        with patch.object(self.manager, "refresh_providers", return_value=ready):
            self.manager._failure("claude", "usage limit exceeded, resets 11:15pm")
        providers = self.manager._load()["providers"]
        self.assertIn("실제 한도 감지", providers["claude"]["note"])

        with patch.object(self.manager, "refresh_providers", return_value=ready):
            self.manager._failure("gemini", "usage limit exceeded, no reset info")
        providers = self.manager._load()["providers"]
        self.assertIn("추정", providers["gemini"]["note"])
        self.assertFalse(providers["gemini"]["quota_observed"])

    def test_missing_stage_provider_blocks_without_advancing(self):
        with patch.object(self.manager, "refresh_providers", return_value={
            "codex":{"auth_ready":False,"status":"WAITING_QUOTA","reset_at":"2099-01-01T00:00:00+09:00"}
        }):
            task = self.manager.dispatch("gate test")
            self.wait_idle()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 1)
        self.assertEqual(saved["status"], "WAITING_QUOTA")
        self.assertFalse(saved["stage1_output"])

    def test_gemini_call_uses_generous_model_and_passes_api_key(self):
        """2026-09-14 소유자 지적: Gemini를 거의 안 썼는데 한도에 걸림 - 원인은 API 키를
        서브프로세스에 안 넘기고 모델도 지정 안 해 gemini CLI가 기본 모델(하루 20회
        무료 한도)로 떨어졌기 때문이었다. 올바른 모델/키가 실제로 전달되는지 검증."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return type("R", (), {"returncode": 0, "stdout": '{"response":"ok"}', "stderr": ""})()

        with patch.object(_module.subprocess, "run", side_effect=fake_run), \
             patch.object(_module, "secret", side_effect=lambda name: "fake-gemini-key" if name == "GEMINI_API_KEY" else ""):
            result = self.manager._gemini({"title": "t", "description": "d", "artifacts": [], "stage1_output": "", "stage2_output": "", "stage3_output": "", "stage4_output": ""})
        self.assertEqual(result, "ok")
        cmd = captured["cmd"]
        self.assertIn("--model", cmd)
        self.assertNotEqual(cmd[cmd.index("--model") + 1], "gemini-3.6-flash")
        self.assertEqual(captured["env"].get("GEMINI_API_KEY"), "fake-gemini-key")


    def test_deepseek_raises_when_content_is_empty_despite_200_response(self):
        """2026-09-14 발견: deepseek-flash는 추론 모델이라 reasoning_content에 사고
        과정을 다 쓰고 나면 content(최종 답변)가 빈 문자열인 채 HTTP 200이 올 수 있다.
        예전엔 이걸 그대로 성공으로 반환해 3단계에 빈 검토 결과가 조용히 박혔다."""
        import sys as _sys, json as _json
        ag_path = str(Path("/Volumes/Realtek_NVME/AI System/antigravity_workspace"))
        if ag_path not in _sys.path:_sys.path.insert(0, ag_path)

        class FakeResponse:
            def __init__(self, payload):
                self._payload = _json.dumps(payload).encode()
            def read(self):
                return self._payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        fake_payload = {
            "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "..."}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 16000},
        }
        fake_ledger = type("FakeLedger", (), {
            "reserve_monthly_budget": lambda self, *a, **k: "fake-reservation-id",
            "release_reservation": lambda self, *a, **k: None,
            "finalize_reservation": lambda self, *a, **k: None,
        })()
        with patch.object(_module, "secret", side_effect=lambda name: "fake-deepseek-key" if name == "DEEPSEEK_API_KEY" else ("10000" if name == "DEEPSEEK_MONTHLY_BUDGET_KRW" else "")), \
             patch.object(_module, "urlopen", return_value=FakeResponse(fake_payload)), \
             patch("memory.llm_usage_ledger.LLMUsageLedger", return_value=fake_ledger):
            with self.assertRaises(RuntimeError) as ctx:
                self.manager._deepseek("qwen output")
        self.assertIn("결과 없음", str(ctx.exception))




    def test_stage1_still_blocks_when_both_codex_and_claude_unavailable(self):
        ready = {
            "codex": {"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"},
            "claude": {"auth_ready": False, "status": "WAITING_AUTH", "reset_at": None},
        }
        with patch.object(self.manager, "refresh_providers", return_value=ready), \
             patch.object(self.manager, "_claude_plan_fallback") as mock_fallback:
            task = self.manager.dispatch("no fallback available")
            self.wait_idle()
        mock_fallback.assert_not_called()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 1)
        self.assertFalse(saved["stage1_output"])


    def test_each_artifact_is_required_before_next_stage(self):
        ready={n:{"auth_ready":True,"status":"AVAILABLE","reset_at":None} for n in ("codex","gemini","qwen","deepseek","claude")}
        with patch.object(self.manager,"refresh_providers",return_value=ready), patch.object(self.manager,"_gemini_call",return_value="plan"):
            task=self.manager.dispatch("stage test");self.wait_idle()
        saved=self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"],2)
        self.assertEqual(saved["stage1_output"],"plan")
        self.assertEqual(saved["artifacts"][0]["stage"],1)

    def test_goal_detail_contains_linked_task_evidence(self):
        goal=self.manager.upsert_goal("goal","desc","criteria",auto_continue=False)
        with patch.object(self.manager,"_launch"):
            task=self.manager.dispatch("work",goal_id=goal["goal_id"])
        detail=self.manager.get_goal(goal["goal_id"])
        self.assertEqual(detail["tasks"][0]["task_id"],task["task_id"])
        self.assertEqual(detail["progress_pct"],0)

    def test_seven_goal_limit(self):
        for i in range(7): self.manager.upsert_goal(f"g{i}",auto_continue=False)
        with self.assertRaises(ValueError): self.manager.upsert_goal("g8",auto_continue=False)

    def test_goal_auto_switch_and_bulk_start_only_one(self):
        goals=[self.manager.upsert_goal(f"g{i}",auto_continue=False) for i in range(3)]
        with patch.object(self.manager,"_launch"):
            result=self.manager.set_all_goals_auto(True)
        self.assertTrue(all(g["auto_continue"] for g in result["goals"]))
        self.assertEqual(len(result["launched_task_ids"]),1)
        self.assertEqual(len([t for t in self.manager.list_tasks() if t["status"] not in {"COMPLETED","FAILED","CANCELLED"}]),1)
        changed=self.manager.set_goal_auto(goals[1]["goal_id"],False)
        self.assertFalse(changed["goal"]["auto_continue"])

    def test_waiting_task_blocks_another_goal_dispatch(self):
        first=self.manager.upsert_goal("first",auto_continue=False)
        self.manager.upsert_goal("second",auto_continue=False)
        with patch.object(self.manager,"_launch"):
            self.manager.dispatch("waiting",goal_id=first["goal_id"])
            result=self.manager.set_all_goals_auto(True)
        self.assertEqual(result["launched_task_ids"],[])
        self.assertEqual(len(self.manager.list_tasks()),1)

    def test_recovery_invalidates_fake_five_stage_completion(self):
        with patch.object(self.manager,"_launch"):
            task=self.manager.dispatch("fake")
        state=self.manager._load();saved=next(t for t in state["tasks"] if t["task_id"]==task["task_id"])
        saved.update(status="COMPLETED",stage_label="5/5 완료",artifacts=[{"stage":5}],stage1_output="claimed")
        self.manager._save(state)
        repaired=StrictAGIOrchestrator(self.manager.state_file,999)
        self.assertEqual(repaired.get_task(task["task_id"])["status"],"INVALIDATED")

    def test_first_iteration_has_no_prior_feedback(self):
        """2026-09-13 소유자 지적 보완: 첫 실행에는 참조할 직전 시도가 없으므로 피드백 절이 없어야 한다."""
        goal = self.manager.upsert_goal("g", "설명", "기준", auto_continue=False)
        with patch.object(self.manager, "_launch"):
            result = self.manager.set_goal_auto(goal["goal_id"], True)
        task = self.manager.get_task(result["launched_task_ids"][0])
        self.assertNotIn("직전 시도", task["description"])

    def test_second_iteration_repeats_prior_final_review_as_feedback(self):
        """자율 반복이 매번 같은 목표 원문만 반복해 동일한 계획을 만들던 결함 수정 검증:
        직전 시도의 5단계 최종 검수 내용이 다음 프롬프트에 그대로 포함돼야 한다."""
        goal = self.manager.upsert_goal("g", "설명", "기준", auto_continue=False)
        with patch.object(self.manager, "_launch"):
            first = self.manager.dispatch("first attempt", goal_id=goal["goal_id"])
        state = self.manager._load()
        saved = next(t for t in state["tasks"] if t["task_id"] == first["task_id"])
        saved.update(
            status="COMPLETED", verdict="REVISE",  # FINAL 상태여야 다음 반복이 발사됨(전역 단일 미완료 정책)
            stage5_output="REVISE\n캐시 무효화 로직이 동시성 상황에서 깨짐 - 락 없이 갱신됨.",
        )
        self.manager._save(state)

        with patch.object(self.manager, "_launch"):
            result = self.manager.set_goal_auto(goal["goal_id"], True)
        launched = result["launched_task_ids"]
        self.assertEqual(len(launched), 1)
        task = self.manager.get_task(launched[0])
        self.assertIn("직전 시도", task["description"])
        self.assertIn(first["task_id"], task["description"])
        self.assertIn("캐시 무효화 로직이 동시성 상황에서 깨짐", task["description"])
        self.assertIn("반복 금지", task["description"])

    def test_second_iteration_after_early_stop_reports_stop_reason_not_blank_repeat(self):
        """직전 시도가 5단계까지 못 가고 정지(예: 인증/한도)했을 때도 그 사실을 다음 프롬프트에 남긴다."""
        goal = self.manager.upsert_goal("g", "설명", "기준", auto_continue=False)
        with patch.object(self.manager, "_launch"):
            first = self.manager.dispatch("first attempt", goal_id=goal["goal_id"])
        state = self.manager._load()
        saved = next(t for t in state["tasks"] if t["task_id"] == first["task_id"])
        saved.update(status="FAILED", stage_label="1/5 codex 사용 불가 · 이 단계에서 정지")  # FINAL 상태여야 다음 반복이 발사됨
        self.manager._save(state)

        with patch.object(self.manager, "_launch"):
            result = self.manager.set_goal_auto(goal["goal_id"], True)
        task = self.manager.get_task(result["launched_task_ids"][0])
        self.assertIn("직전 시도", task["description"])
        self.assertIn("FAILED", task["description"])
        self.assertIn("codex 사용 불가", task["description"])

    def test_final_verdict_requires_execution_evidence(self):
        for response in ("PASS\nLooks fine", "REVISE\nTests failed", "Notes\nPASS"):
            with self.subTest(response=response), patch.object(self.manager, "_launch"):
                task = self.manager.dispatch("review")
                task.update({f"stage{n}_output": "draft" for n in range(1, 5)})
                task["artifacts"] = [{"stage": n} for n in range(1, 5)]
                self.manager._finish_stage(task, 5, "codex", response)
                saved = self.manager.get_task(task["task_id"])
                self.assertEqual(saved["status"], "NEEDS_RECONCILIATION")
                self.assertNotEqual(saved["progress_pct"], 100)
                self.assertTrue(Path(saved["artifact_path"]).is_relative_to(Path(self.tmp.name)))
                with patch.object(self.manager, "refresh_providers", return_value={}):
                    result = self.manager.check_and_resume()
                self.assertNotIn(task["task_id"], result["resumed_task_ids"])

    def make_task(self, stage=1, core=None):
        with patch.object(self.manager, "_launch"):
            task=self.manager.dispatch("test", core_review_reason=core)
        self.manager._update(task["task_id"], current_stage=stage)
        return self.manager.get_task(task["task_id"])

    def ready(self):
        return {n:{"auth_ready":True,"status":"AVAILABLE"} for n in ("gemini","deepseek","claude","codex")}

    def test_ordinary_work_never_uses_premium_models(self):
        task=self.make_task()
        with patch.object(self.manager,"refresh_providers",return_value=self.ready()), \
             patch.object(self.manager,"_gemini_call",return_value="facts") as gemini, \
             patch.object(self.manager,"_deepseek",return_value="draft") as deepseek, \
             patch.object(self.manager,"_claude") as claude, patch.object(self.manager,"_codex") as codex:
            for _ in range(5):self.manager._run(task["task_id"])
        self.assertEqual(gemini.call_count,2)
        self.assertEqual(deepseek.call_count,2)
        claude.assert_not_called();codex.assert_not_called()
        self.assertEqual(self.manager.get_task(task["task_id"])["status"],"DRAFT_READY")

    def test_deepseek_runtime_failure_uses_gemini_not_claude(self):
        task=self.make_task(3)
        with patch.object(self.manager,"refresh_providers",return_value=self.ready()), \
             patch.object(self.manager,"_deepseek",side_effect=RuntimeError("402")), \
             patch.object(self.manager,"_gemini_call",return_value="fallback") as gemini, \
             patch.object(self.manager,"_claude") as claude:
            self.manager._run(task["task_id"])
        gemini.assert_called_once();claude.assert_not_called()
        self.assertEqual(self.manager.get_task(task["task_id"])["current_stage"],4)

    def test_core_review_is_reserved_once_per_evidence(self):
        task=self.make_task(4,"security_change")
        self.assertTrue(self.manager._reserve_core_review(task,"claude"))
        self.assertFalse(self.manager._reserve_core_review(task,"claude"))
        other=StrictAGIOrchestrator(self.manager.state_file,999)
        self.assertFalse(other._reserve_core_review(task,"claude"))

    def test_daily_core_cap_survives_restart(self):
        for _ in range(4):self.assertTrue(self.manager._reserve_core_review(self.make_task(4,"security_change"),"claude"))
        other=StrictAGIOrchestrator(self.manager.state_file,999)
        self.assertFalse(other._reserve_core_review(self.make_task(4,"security_change"),"claude"))

    def test_core_failure_is_not_repeated(self):
        task=self.make_task(4,"security_change")
        with patch.object(self.manager,"refresh_providers",return_value=self.ready()), \
             patch.object(self.manager,"_claude",side_effect=RuntimeError("failed")) as claude:
            self.manager._run(task["task_id"]);self.manager._run(task["task_id"])
        claude.assert_called_once()
        self.assertEqual(self.manager.get_task(task["task_id"])["status"],"WAITING_REPAIR")

    def test_review_context_has_bounded_size_and_provenance(self):
        task=self.make_task(4,"security_change")
        task["stage3_output"]="x"*100000
        task["artifacts"]=[{"stage":3,"path":"/evidence","hash":"abc"}]
        context=self.manager._review_context(task)
        self.assertLessEqual(len(context),6000)
        self.assertIn("/evidence",context)

    def test_waiting_goal_does_not_block_other_goal(self):
        first=self.manager.upsert_goal("first",auto_continue=False)
        second=self.manager.upsert_goal("second",auto_continue=False)
        with patch.object(self.manager,"_launch"):
            task=self.manager.dispatch("blocked",goal_id=first["goal_id"])
            self.manager._update(task["task_id"],status="WAITING_QUOTA",next_retry_at="2099-01-01T00:00:00+09:00")
            result=self.manager.set_goal_auto(second["goal_id"],True)
        self.assertEqual(len(result["launched_task_ids"]),1)

    def test_premium_unavailable_does_not_block_ordinary_plan(self):
        task=self.make_task()
        ready={"gemini":{"auth_ready":True,"status":"AVAILABLE"}}
        with patch.object(self.manager,"refresh_providers",return_value=ready), \
             patch.object(self.manager,"_gemini_call",return_value="plan"), \
             patch.object(self.manager,"_claude_plan_fallback") as fallback:
            self.manager._run(task["task_id"])
        fallback.assert_not_called()
        self.assertEqual(self.manager.get_task(task["task_id"])["current_stage"],2)


if __name__ == "__main__":
    unittest.main()
