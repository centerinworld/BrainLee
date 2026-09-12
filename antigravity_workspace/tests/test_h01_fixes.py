"""
H01 회귀 테스트: 2026-09-12 핸드오프 문서 A01~A06 수정 사항 검증.
각 테스트는 수정 전 실제로 존재했던 결함을 재현하는 조건에서, 수정 후 코드가
더 이상 그 결함을 일으키지 않음을 확인한다.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import patch

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

import process_watchdog
from agents.l2_workers.codex_builder import CodexBuilder
from agents.l2_workers.quant_trader import QuantTraderWorker
from agents.l1_a_dev_orchestrator import DevOrchestrator
from agents.l1_pm_owner import L1PMOwner
from agents.l1_b_content_orchestrator import ContentOrchestrator


class TestA01ProcessWatchdog(unittest.TestCase):
    def test_status_query_never_kills_processes(self):
        """A01: 상태 조회(get_system_status)는 kill을 유발해서는 안 된다."""
        with patch("process_watchdog.subprocess.run") as mock_run:
            process_watchdog.get_system_status()
            kill_calls = [c for c in mock_run.call_args_list if "kill" in str(c)]
            self.assertEqual(kill_calls, [])

    def test_cleanup_skips_unowned_process(self):
        """A01: 소유권(워크스페이스 경로) 확인 안 되는 PID는 dry_run=False에서도 kill하지 않는다."""
        with patch("process_watchdog.get_pid_by_port", return_value=[99999]), \
             patch("process_watchdog._is_owned_by_workspace", return_value=False), \
             patch("process_watchdog.subprocess.run") as mock_run:
            actions = process_watchdog.clean_redundant_ports(dry_run=False)
            mock_run.assert_not_called()
            self.assertTrue(all(not a["killed"] for a in actions))

    def test_bridge_port_not_in_redundant_list(self):
        """A01: bridge_api.py의 실제 운영 포트(8502)는 정리 대상에서 제외되어야 한다."""
        with patch("process_watchdog.get_pid_by_port", return_value=[]) as mock_get_pid:
            process_watchdog.clean_redundant_ports(dry_run=True)
        checked_ports = [call.args[0] for call in mock_get_pid.call_args_list]
        self.assertNotIn(8502, checked_ports)


class TestA02SelfHealing(unittest.TestCase):
    def test_comment_only_patch_is_not_auto_merged(self):
        """A02: 주석 스캐폴드뿐인 패치는 리뷰 승인과 무관하게 자동 머지되면 안 된다."""
        dev = DevOrchestrator(is_mock=True)
        try:
            raise ValueError("테스트용 에러")
        except ValueError as e:
            record = dev.self_healing_loop(e)
        self.assertFalse(record["is_auto_merged"])
        self.assertIn("merge_blocked_reason", record)

    def test_generate_patch_reports_no_code_change(self):
        builder = CodexBuilder()
        patch = builder.generate_patch({"error_type": "ValueError", "error_message": "x", "target_file": "f.py"})
        self.assertFalse(patch["has_code_change"])
        self.assertEqual(patch["status"], "DRAFT_ONLY")


class TestA03QuantTrader(unittest.TestCase):
    def test_missing_db_returns_empty_not_fixture_by_default(self):
        """A03: DB가 없을 때 기본 동작은 빈 목록(degraded)이며, 가짜 실데이터를 반환하지 않는다."""
        worker = QuantTraderWorker(is_mock=True, stock_db_path="/no/such/path.db")
        universe = worker.get_real_universe(limit=5)
        self.assertEqual(universe, [])

    def test_missing_db_fixture_requires_explicit_opt_in(self):
        worker = QuantTraderWorker(is_mock=True, stock_db_path="/no/such/path.db")
        universe = worker.get_real_universe(limit=5, allow_fixture_fallback=True)
        self.assertTrue(len(universe) > 0)
        self.assertTrue(all(row.get("is_fixture") for row in universe))

    def test_real_order_without_broker_integration_is_blocked(self):
        """A03: is_mock=False만으로 실전 체결(FILLED)을 표시하면 안 된다."""
        worker = QuantTraderWorker(is_mock=False)
        order = asyncio.run(worker.execute_order("005930", "삼성전자", "BUY", 70000.0, 10))
        self.assertEqual(order["status"], "BLOCKED_NO_BROKER_INTEGRATION")
        self.assertNotEqual(order["status"], "FILLED")

    def test_mock_order_is_labeled_simulated_not_filled(self):
        worker = QuantTraderWorker(is_mock=True)
        order = asyncio.run(worker.execute_order("005930", "삼성전자", "BUY", 70000.0, 10))
        self.assertEqual(order["status"], "SIMULATED_FILL")
        self.assertNotEqual(order["status"], "FILLED")


class TestA04IntentAndBudget(unittest.TestCase):
    def test_neutral_prompt_creates_no_task(self):
        """A04: 주식/방산 도메인과 무관한 일반 질문은 태스크를 생성하지 않아야 한다."""
        pm = L1PMOwner(auto_heal=True)
        tasks = pm.parse_founder_intent("오늘 날씨 어때?")
        self.assertEqual(tasks, [])

    def test_analysis_only_prompt_does_not_execute_orders(self):
        """A04: 주문 의도 키워드가 없는 주식 질문은 분석만 하고 주문을 실행하지 않는다."""
        pm = L1PMOwner(auto_heal=True)
        tasks = pm.parse_founder_intent("삼성전자 퀀트 팩터 분석해줘")
        dev_tasks = [t for t in tasks if t["domain"] == "dev_orchestrator"]
        self.assertEqual(len(dev_tasks), 1)
        self.assertFalse(dev_tasks[0]["payload"]["execute_orders"])

    def test_insufficient_budget_skips_order_without_forcing_minimum_share(self):
        """A04: 예산 부족(비중 0)이면 강제 1주 매수 없이 0주로 건너뛴다."""
        dev = DevOrchestrator(is_mock=True)
        universe = [{"code": "999999", "name": "테스트종목", "price": 999_999_999.0}]
        result = asyncio.run(dev.run_trading_pipeline(universe, execute_orders=True))
        self.assertEqual(len(result["executed_orders"]), 1)
        self.assertEqual(result["executed_orders"][0]["status"], "SKIPPED_INSUFFICIENT_BUDGET")


class TestA06ContentDelivery(unittest.TestCase):
    def test_prepared_reports_are_not_marked_as_sent(self):
        """A06: 실제 Slack/Notion 전송 없이 is_notified_slack/is_published_notion을 True로 표시하지 않는다."""
        orchestrator = ContentOrchestrator()
        result = asyncio.run(orchestrator.run_defense_intelligence_cycle())
        for item in result["reports"]:
            if item.get("delivery_status") == "PREPARED_NOT_SENT":
                self.assertFalse(item["is_notified_slack"])
                self.assertFalse(item["is_published_notion"])


if __name__ == "__main__":
    unittest.main()
