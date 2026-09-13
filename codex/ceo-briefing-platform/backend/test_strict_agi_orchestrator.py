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
