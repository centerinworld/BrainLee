import tempfile, time, unittest
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

    def _advance_to_stage3(self, ready):
        with patch.object(self.manager, "refresh_providers", return_value=ready), \
             patch.object(self.manager, "_codex", return_value="plan"), \
             patch.object(self.manager, "_gemini", return_value="evidence"):
            task = self.manager.dispatch("reach stage 3")
            self.wait_idle()
            self.manager._launch(task["task_id"])
            self.wait_idle()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 3)
        return task

    def test_stage3_falls_back_to_gemini_when_deepseek_unavailable(self):
        """소유자 지시(2026-09-14): "DeepSeek는 중요한 AI가 아니니 Gemini가 대체하도록
        해" - DeepSeek 계정/한도 문제로 3단계가 막히면 Gemini가 그 점검을 대신해야 한다."""
        ready = {n: {"auth_ready": True, "status": "AVAILABLE", "reset_at": None} for n in ("codex", "gemini", "qwen", "claude")}
        task = self._advance_to_stage3(ready)

        deepseek_down = dict(ready, deepseek={"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"})
        with patch.object(self.manager, "refresh_providers", return_value=deepseek_down), \
             patch.object(self.manager, "_qwen", return_value="qwen out"), \
             patch.object(self.manager, "_gemini_qc", return_value="gemini qc out") as mock_gemini_qc, \
             patch.object(self.manager, "_deepseek") as mock_deepseek:
            self.manager._launch(task["task_id"])
            self.wait_idle()
        mock_gemini_qc.assert_called_once_with("qwen out")
        mock_deepseek.assert_not_called()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 4)
        self.assertIn("Gemini 전처리 점검(DeepSeek 대체)", saved["stage3_output"])
        self.assertEqual(saved["artifacts"][-1]["provider"], "qwen+gemini_qc_fallback")

    def test_stage3_still_blocks_when_both_deepseek_and_gemini_unavailable(self):
        ready = {n: {"auth_ready": True, "status": "AVAILABLE", "reset_at": None} for n in ("codex", "gemini", "qwen", "claude")}
        task = self._advance_to_stage3(ready)

        both_down = dict(ready,
                          deepseek={"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"},
                          gemini={"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"})
        with patch.object(self.manager, "refresh_providers", return_value=both_down), \
             patch.object(self.manager, "_gemini_qc") as mock_gemini_qc:
            self.manager._launch(task["task_id"])
            self.wait_idle()
        mock_gemini_qc.assert_not_called()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 3)
        self.assertFalse(saved["stage3_output"])

    def test_stage1_falls_back_to_claude_when_codex_quota_exhausted(self):
        """소유자 지시(2026-09-14): GPT(Codex/Astra) 한도가 자주 소진돼도 자율 루프가
        멈추지 않도록, 1단계(계획)만 Claude로 대체 가능해야 한다."""
        ready = {
            "codex": {"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"},
            "claude": {"auth_ready": True, "status": "AVAILABLE_QUOTA_UNKNOWN", "reset_at": None},
        }
        with patch.object(self.manager, "refresh_providers", return_value=ready), \
             patch.object(self.manager, "_claude_plan_fallback", return_value="claude plan") as mock_fallback, \
             patch.object(self.manager, "_codex") as mock_codex:
            task = self.manager.dispatch("fallback test")
            self.wait_idle()
        mock_fallback.assert_called_once()
        mock_codex.assert_not_called()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 2)
        self.assertEqual(saved["stage1_output"], "claude plan")
        self.assertEqual(saved["artifacts"][0]["provider"], "claude_plan_fallback")
        self.assertNotEqual(saved["status"], "WAITING_QUOTA")

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

    def test_stage5_never_falls_back_to_claude_even_if_codex_is_down(self):
        """완료 판정(5단계)은 자기 인증 방지 원칙상 Claude로 대체하면 안 된다 -
        Codex가 막혀 있으면 Claude가 대신 계획을 세웠더라도 최종검수는 그대로 정지해야 한다.

        _run()이 끝에서 부르는 self._launch(tid)는 같은 스레드 안에서는 self.active에
        아직 tid가 남아 있어 no-op이다(설계상 "한 번에 과업 하나만 실행" 재진입 방지) -
        실제로는 monitor 루프(check_and_resume)가 밖에서 반복 호출해 다음 단계로 넘긴다.
        그래서 이 테스트도 단계마다 _launch를 직접 호출해 한 단계씩 진행시킨다."""
        ready_for_plan = {n: {"auth_ready": True, "status": "AVAILABLE", "reset_at": None} for n in ("codex", "gemini", "qwen", "deepseek", "claude")}
        with patch.object(self.manager, "refresh_providers", return_value=ready_for_plan), \
             patch.object(self.manager, "_codex", return_value="plan"), \
             patch.object(self.manager, "_gemini", return_value="evidence"), \
             patch.object(self.manager, "_qwen", return_value="qwen out"), \
             patch.object(self.manager, "_deepseek", return_value="deepseek out"), \
             patch.object(self.manager, "_claude", return_value="claude review"):
            task = self.manager.dispatch("reach stage 5")
            self.wait_idle()
            for _ in range(3):  # 2,3,4단계를 하나씩 밖에서 진행시켜 5단계 직전까지 이동
                self.manager._launch(task["task_id"])
                self.wait_idle()
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["current_stage"], 5)

        codex_down = {
            "codex": {"auth_ready": False, "status": "WAITING_QUOTA", "reset_at": "2099-01-01T00:00:00+09:00"},
            "claude": {"auth_ready": True, "status": "AVAILABLE_QUOTA_UNKNOWN", "reset_at": None},
        }
        with patch.object(self.manager, "refresh_providers", return_value=codex_down), \
             patch.object(self.manager, "_claude_plan_fallback") as mock_fallback, \
             patch.object(self.manager, "_codex") as mock_codex:
            self.manager._launch(task["task_id"])
            self.wait_idle()
        mock_fallback.assert_not_called()  # 5단계는 절대 Claude로 대체되지 않는다
        mock_codex.assert_not_called()  # 게이트에서 막혀 아예 호출되지 않아야 함
        saved = self.manager.get_task(task["task_id"])
        self.assertEqual(saved["status"], "WAITING_QUOTA")
        self.assertEqual(saved["current_stage"], 5)

    def test_each_artifact_is_required_before_next_stage(self):
        ready={n:{"auth_ready":True,"status":"AVAILABLE","reset_at":None} for n in ("codex","gemini","qwen","deepseek","claude")}
        with patch.object(self.manager,"refresh_providers",return_value=ready), patch.object(self.manager,"_codex",return_value="plan"):
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


if __name__ == "__main__":
    unittest.main()
