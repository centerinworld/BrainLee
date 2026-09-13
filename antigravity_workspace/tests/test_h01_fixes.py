"""
H01 회귀 테스트: 2026-09-12 핸드오프 문서 A01~A06 수정 사항 검증.
각 테스트는 수정 전 실제로 존재했던 결함을 재현하는 조건에서, 수정 후 코드가
더 이상 그 결함을 일으키지 않음을 확인한다.
"""

import os
import sys
import json
import asyncio
import subprocess
import tempfile
import unittest
import unittest.mock
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
from fastapi import HTTPException
import bridge_api
from memory.vector_store import MemoryVectorStore
from memory.state_ledger import StateLedger
from memory.llm_usage_ledger import LLMUsageLedger
from agents.l2_workers.defense_researcher import DefenseResearcherWorker
from agents.l2_workers.claude_reviewer import ClaudeReviewer

# config.env_loader가 import 시점에 stock_dashboard/runtime/.env의 실제 POSTGRES_DATABASE_URL을
# 프로세스 환경에 심어두므로(A10), SQLite 폴백 경로를 테스트할 때는 이 값으로 명시적으로
# postgres_url을 덮어써 실제 운영 DB에 붙지 않도록 격리한다.
UNREACHABLE_PG_URL = "postgresql://nouser:nopass@127.0.0.1:1/doesnotexist"


class _StubLLMClient:
    """실제 네트워크 호출 없이 항상 폴백 응답을 반환하는 테스트용 스텁 (llm_client 실사용 회귀
    테스트는 TestLLMWiring에서 별도로 mock 응답을 통해 다룬다)."""
    def chat_completion_with_meta(self, *args, **kwargs):
        return {"content": "", "provider": "none", "model": None, "usage": None, "is_fallback": True}


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
        """A02: 주석 스캐폴드뿐인 패치는 리뷰 승인과 무관하게 자동 머지되면 안 된다.
        LLM은 mock(is_fallback)해 실제 네트워크 호출 없이 스캐폴드 저하 경로를 검증한다."""
        stub_builder = CodexBuilder(llm_client=_StubLLMClient())
        dev = DevOrchestrator(is_mock=True, codex_builder=stub_builder)
        try:
            raise ValueError("테스트용 에러")
        except ValueError as e:
            record = dev.self_healing_loop(e)
        self.assertFalse(record["is_auto_merged"])
        self.assertIn("merge_blocked_reason", record)

    def test_generate_patch_reports_no_code_change(self):
        """LLM이 전부 실패(mock)하면 has_code_change=False로 스캐폴드 저하."""
        builder = CodexBuilder(llm_client=_StubLLMClient())
        patch = builder.generate_patch({"error_type": "ValueError", "error_message": "x", "target_file": "f.py"})
        self.assertFalse(patch["has_code_change"])

    def test_generate_patch_accepts_real_unified_diff(self):
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+# fix\n x = 1\n",
            "provider": "deepseek", "model": "deepseek-chat",
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            "is_fallback": False
        }
        builder = CodexBuilder(llm_client=stub)
        patch = builder.generate_patch({"error_type": "ValueError", "error_message": "x", "target_file": "f.py"})
        self.assertTrue(patch["has_code_change"])
        self.assertEqual(patch["status"], "DRAFT_DIFF_GENERATED")

    def test_generate_patch_rejects_non_diff_response(self):
        """형식이 unified diff가 아니면(설명문만 등) has_code_change=False로 저하."""
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "이 에러는 null 체크를 추가하면 해결됩니다.",
            "provider": "deepseek", "model": "deepseek-chat",
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            "is_fallback": False
        }
        builder = CodexBuilder(llm_client=stub)
        patch = builder.generate_patch({"error_type": "ValueError", "error_message": "x", "target_file": "f.py"})
        self.assertFalse(patch["has_code_change"])
        self.assertEqual(patch["status"], "DRAFT_ONLY")
        self.assertEqual(patch["status"], "DRAFT_ONLY")


class TestClaudeReviewerDiffHandling(unittest.TestCase):
    """2026-09-12 외부 검토에서 지적된 실제 버그: codex_builder가 진짜 unified diff를
    반환하게 된 뒤, claude_reviewer가 그걸 그대로 ast.parse()해 항상 SyntaxError로
    REJECTED 처리하고 있었다(diff의 @@ 헤더는 유효한 파이썬 구문이 아님)."""

    def test_real_diff_is_not_misclassified_as_syntax_error(self):
        reviewer = ClaudeReviewer()
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+x = 1\n+y = 2\n"
        result = reviewer.review_code_quality(diff, "f.py")
        self.assertNotEqual(result["status"], "REJECTED")
        self.assertEqual(result["status"], "PARTIAL_REVIEW_DIFF")
        self.assertFalse(result["is_full_syntax_validated"])

    def test_diff_with_hardcoded_secret_in_added_lines_is_flagged(self):
        reviewer = ClaudeReviewer()
        diff = '--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+api_key = "sk-abcdefghijklmnopqrstuvwx"\n'
        result = reviewer.review_code_quality(diff, "f.py")
        self.assertFalse(result["approved"])

    def test_diff_with_eval_in_added_lines_is_flagged(self):
        reviewer = ClaudeReviewer()
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+eval(user_input)\n"
        result = reviewer.review_code_quality(diff, "f.py")
        self.assertFalse(result["approved"])

    def test_removed_lines_are_not_scanned_as_added_code(self):
        """제거된(-) 줄에 있는 패턴은 '추가된' 걸로 오탐하면 안 된다."""
        reviewer = ClaudeReviewer()
        diff = '--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-api_key = "sk-abcdefghijklmnopqrstuvwx"\n+api_key = os.getenv("API_KEY")\n'
        result = reviewer.review_code_quality(diff, "f.py")
        self.assertTrue(result["approved"])

    def test_plain_python_code_still_uses_full_ast_check(self):
        """diff가 아닌 일반 코드(주석 스캐폴드 등)는 기존처럼 전체 ast.parse() 검사를 받는다."""
        reviewer = ClaudeReviewer()
        result = reviewer.review_code_quality("# just a comment scaffold\n", "f.py")
        self.assertIn(result["status"], ("APPROVED", "REJECTED"))
        self.assertNotEqual(result["status"], "PARTIAL_REVIEW_DIFF")

    def test_self_healing_loop_with_real_diff_does_not_falsely_reject_as_syntax_error(self):
        """전체 경로 회귀: self_healing_loop가 진짜 diff를 만들었을 때 review_findings에
        가짜 SyntaxError가 나오면 안 된다(머지는 여전히 A02 게이트로 항상 차단됨)."""
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+x = 1\n",
            "provider": "deepseek", "model": "deepseek-chat",
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            "is_fallback": False
        }
        builder = CodexBuilder(llm_client=stub)
        dev = DevOrchestrator(is_mock=True, codex_builder=builder)
        try:
            raise ValueError("테스트용 에러")
        except ValueError as e:
            record = dev.self_healing_loop(e)
        self.assertNotIn("SyntaxError", " ".join(record["review_findings"]))
        self.assertFalse(record["is_auto_merged"])  # A02 게이트는 여전히 유효


class TestA03QuantTrader(unittest.TestCase):
    def test_missing_db_returns_empty_not_fixture_by_default(self):
        """A03: DB가 없을 때 기본 동작은 빈 목록(degraded)이며, 가짜 실데이터를 반환하지 않는다.
        postgres_url을 명시적으로 차단해 config.env_loader가 import 시점에 프로세스 환경에
        심어둔 실제 STOCK_POSTGRES_URL/POSTGRES_DATABASE_URL의 영향을 받지 않게 한다(A10 경로 격리)."""
        worker = QuantTraderWorker(is_mock=True, stock_db_path="/no/such/path.db", postgres_url=UNREACHABLE_PG_URL)
        universe = worker.get_real_universe(limit=5)
        self.assertEqual(universe, [])

    def test_missing_db_fixture_requires_explicit_opt_in(self):
        worker = QuantTraderWorker(is_mock=True, stock_db_path="/no/such/path.db", postgres_url=UNREACHABLE_PG_URL)
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
        orchestrator = ContentOrchestrator(llm_client=_StubLLMClient())
        result = asyncio.run(orchestrator.run_defense_intelligence_cycle())
        for item in result["reports"]:
            if item.get("delivery_status") == "PREPARED_NOT_SENT":
                self.assertFalse(item["is_notified_slack"])
                self.assertFalse(item["is_published_notion"])


class TestA05BridgeAuth(unittest.TestCase):
    def test_trigger_blocked_when_no_server_key_configured(self):
        """A05: 서버에 API 키가 설정되지 않으면 트리거는 무조건 차단(fail-closed)돼야 한다."""
        with patch.object(bridge_api, "_BRIDGE_API_KEY", None):
            with self.assertRaises(HTTPException) as ctx:
                bridge_api._verify_bridge_api_key("무슨키든")
            self.assertEqual(ctx.exception.status_code, 503)

    def test_trigger_blocked_with_wrong_key(self):
        """A05: 잘못된 API 키는 거부된다."""
        with patch.object(bridge_api, "_BRIDGE_API_KEY", "correct-key"):
            with self.assertRaises(HTTPException) as ctx:
                bridge_api._verify_bridge_api_key("wrong-key")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_trigger_allowed_with_correct_key(self):
        """A05: 올바른 API 키는 통과해야 한다."""
        with patch.object(bridge_api, "_BRIDGE_API_KEY", "correct-key"):
            bridge_api._verify_bridge_api_key("correct-key")  # 예외 없이 통과

    def test_cors_no_longer_wildcard(self):
        """A05: CORS allow_origins가 더 이상 '*' 전체 허용이 아니다."""
        self.assertNotIn("*", bridge_api._allowed_origins)
        self.assertIn("https://newsinfo.cloud", bridge_api._allowed_origins)


class TestA07VectorStoreDegraded(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_db.close()

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _make_store(self) -> MemoryVectorStore:
        # 존재하지 않는 PostgreSQL DSN을 줘서 항상 degraded(use_fallback=True) 경로를 타게 한다.
        return MemoryVectorStore(db_url="postgresql://nouser:nopass@127.0.0.1:1/doesnotexist", fallback_db_path=self.tmp_db.name)

    def test_content_persists_across_restart(self):
        """A07: PostgreSQL이 없어도 원문이 프로세스 재시작(새 인스턴스) 후에도 남아있어야 한다."""
        store1 = MemoryVectorStore(db_url="postgresql://nouser:nopass@127.0.0.1:1/doesnotexist", fallback_db_path=self.tmp_db.name)
        emb = store1.get_embedding("KF-21 양산 계약")
        doc = store1.insert_defense_intelligence(
            source="DAPA", title="KF-21 양산 계약", raw_content="본문 내용",
            fact_summary="f", impact_summary="i", strategy_summary="s", embedding=emb
        )
        self.assertTrue(store1.use_fallback)
        self.assertIsNotNone(doc["id"])

        # "재시작" 시뮬레이션: 같은 파일을 가리키는 새 인스턴스
        store2 = MemoryVectorStore(db_url="postgresql://nouser:nopass@127.0.0.1:1/doesnotexist", fallback_db_path=self.tmp_db.name)
        results = store2.search_similar_intelligence("KF-21 양산", top_k=5)
        self.assertTrue(any(r["title"] == "KF-21 양산 계약" for r in results))

    def test_search_marks_degraded_mode(self):
        store = self._make_store()
        results = store.search_similar_intelligence("아무 질문")
        self.assertEqual(results, [])  # 빈 저장소
        store.insert_defense_intelligence(
            source="X", title="테스트 제목 KF-21", raw_content="테스트 본문",
            fact_summary="f", impact_summary="i", strategy_summary="s",
            embedding=store.get_embedding("테스트")
        )
        results = store.search_similar_intelligence("KF-21")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["search_mode"], "degraded_keyword")

    def test_check_duplicate_without_text_does_not_false_positive(self):
        """A07: text 없이는(가짜 벡터로) 중복 판정을 내리지 않는다."""
        store = self._make_store()
        emb = store.get_embedding("아무 텍스트")
        is_dup, matched, sim = store.check_duplicate(emb, threshold=0.85)
        self.assertFalse(is_dup)

    def test_check_duplicate_with_text_uses_keyword_overlap(self):
        store = self._make_store()
        store.insert_defense_intelligence(
            source="DAPA", title="KF-21 블록1 양산 계약 체결", raw_content="방위사업청 KAI 계약",
            fact_summary="f", impact_summary="i", strategy_summary="s",
            embedding=store.get_embedding("KF-21 블록1 양산 계약 체결")
        )
        emb2 = store.get_embedding("KF-21 블록1 양산 계약 체결 관련 후속 보도")
        is_dup, matched, sim = store.check_duplicate(
            emb2, threshold=0.5, text="KF-21 블록1 양산 계약 체결 관련 후속 보도"
        )
        self.assertTrue(is_dup)


class TestA08StatePersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_ledger = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_ledger.close()

    def tearDown(self):
        os.unlink(self.tmp_ledger.name)

    def test_ledger_persists_across_instances(self):
        """A08: 원장에 쓴 레코드는 새 StateLedger 인스턴스(재시작 시뮬레이션)에서도 읽힌다."""
        ledger1 = StateLedger(db_path=self.tmp_ledger.name)
        ledger1.upsert("orders", "ORD_TEST_1", {"status": "SIMULATED_FILL", "stock_code": "005930"})

        ledger2 = StateLedger(db_path=self.tmp_ledger.name)
        record = ledger2.get("orders", "ORD_TEST_1")
        self.assertIsNotNone(record)
        self.assertEqual(record["stock_code"], "005930")

    def test_order_idempotency_key_prevents_duplicate_execution(self):
        """A08: 같은 idempotency_key로 두 번 호출하면 두 번째는 재실행 없이 기존 주문을 반환한다."""
        ledger = StateLedger(db_path=self.tmp_ledger.name)
        worker = QuantTraderWorker(is_mock=True, ledger=ledger)

        order1 = asyncio.run(worker.execute_order(
            "005930", "삼성전자", "BUY", 70000.0, 10, idempotency_key="RUN_1_005930"
        ))
        order2 = asyncio.run(worker.execute_order(
            "005930", "삼성전자", "BUY", 70000.0, 10, idempotency_key="RUN_1_005930"
        ))
        self.assertEqual(order1["order_id"], order2["order_id"])
        # 두 번째 호출에서 self.orders 리스트에 새 레코드가 추가되지 않아야 한다.
        self.assertEqual(len(worker.orders), 1)

    def test_content_orchestrator_does_not_reprepare_same_item_after_restart(self):
        """A08: 발행 원장(ledger)이 공유되면, 콘텐츠 dedup 이력(vector_store)이 없는
        새 프로세스에서도 같은 (source, title) 항목을 다시 '발행 준비'로 집계하지 않는다."""
        ledger = StateLedger(db_path=self.tmp_ledger.name)

        def make_isolated_vector_store():
            tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
            tmp.close()
            self.addCleanup(os.unlink, tmp.name)
            return MemoryVectorStore(db_url="postgresql://nouser:nopass@127.0.0.1:1/doesnotexist", fallback_db_path=tmp.name)

        # 1회차: 콘텐츠 dedup 이력이 없는 새 vector_store
        orchestrator1 = ContentOrchestrator(vector_store=make_isolated_vector_store(), ledger=ledger, llm_client=_StubLLMClient())
        result1 = asyncio.run(orchestrator1.run_defense_intelligence_cycle())
        self.assertGreater(result1["filtered_and_prepared"], 0)

        # "재시작" 시뮬레이션: vector_store는 또 새 것(dedup 이력 없음)이지만 ledger는 공유
        orchestrator2 = ContentOrchestrator(vector_store=make_isolated_vector_store(), ledger=ledger, llm_client=_StubLLMClient())
        result2 = asyncio.run(orchestrator2.run_defense_intelligence_cycle())
        self.assertEqual(result2["filtered_and_prepared"], 0)
        skipped = [r for r in result2["reports"] if r.get("delivery_status") == "ALREADY_PREPARED_SKIPPED"]
        self.assertGreater(len(skipped), 0)


class TestA10PostgresVsLegacySqlite(unittest.TestCase):
    def test_postgres_unreachable_falls_back_to_labeled_legacy_sqlite(self):
        """A10: PostgreSQL이 없으면 레거시 SQLite로 저하하되, data_source로 명확히 구분해야 한다."""
        worker = QuantTraderWorker(is_mock=True, postgres_url=UNREACHABLE_PG_URL)
        universe = worker.get_real_universe(limit=3)
        if universe:  # 실제 stock.db가 이 환경에 있을 때만 검증 가능
            self.assertTrue(all(row["data_source"] == "legacy_sqlite_snapshot" for row in universe))

    def test_postgres_result_is_labeled_operational_not_legacy(self):
        """A10: PostgreSQL 조회 결과는 legacy_sqlite_snapshot으로 표시되면 안 된다."""
        with patch.object(QuantTraderWorker, "_get_universe_from_postgres", return_value=[
            {"code": "005930", "name": "삼성전자", "market": "KOSPI", "sector": "IT",
             "price": 1.0, "market_cap": 1.0, "per": 1.0, "roe": 1.0,
             "snapshot_date": "2026-09-04", "updated_at": None,
             "is_fixture": False, "data_source": "postgres_operational"}
        ]):
            worker = QuantTraderWorker(is_mock=True)
            universe = worker.get_real_universe(limit=3)
        self.assertEqual(universe[0]["data_source"], "postgres_operational")

    def test_postgres_query_pins_latest_base_date(self):
        """A10: stock_universe가 base_date별 시계열이므로, 쿼리가 최신 날짜로 고정되어 있어야
        한다(누락 시 같은 종목이 오래된 날짜 행과 중복으로 섞여 나온다 - 실제로 재현됐던 문제)."""
        import inspect
        src = inspect.getsource(QuantTraderWorker._get_universe_from_postgres)
        self.assertIn("MAX(base_date)", src)


class TestLLMWiring(unittest.TestCase):
    """defense_researcher가 실제 LLM 호출 경로를 쓰도록 바뀐 것(완전 자동화 1차 단계) 검증.
    실제 네트워크는 절대 호출하지 않고 llm_client를 항상 mock한다."""

    def setUp(self):
        self.tmp_usage_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_usage_db.close()
        self.usage_ledger = LLMUsageLedger(db_path=self.tmp_usage_db.name)

    def tearDown(self):
        os.unlink(self.tmp_usage_db.name)

    def _make_worker(self, llm_client) -> DefenseResearcherWorker:
        return DefenseResearcherWorker(llm_client=llm_client, usage_ledger=self.usage_ledger)

    def test_well_formed_llm_response_is_used_and_labeled(self):
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "[주요 팩트] 테스트 팩트\n[경쟁 환경 및 산업 영향] 테스트 영향\n[전사 사업 전략 관점의 시사점] 테스트 시사점",
            "provider": "gemini", "model": "gemini-3.6-flash",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "is_fallback": False
        }
        worker = self._make_worker(stub)
        summary = worker.generate_three_line_strategy_summary("KF-21 테스트 제목", "본문", "DAPA")
        self.assertEqual(summary["data_source"], "llm_generated:gemini")
        self.assertIn("[주요 팩트] 테스트 팩트", summary["fact_summary"])

    def test_malformed_llm_response_falls_back_to_template(self):
        """A03/A07과 같은 패턴: 형식이 안 맞는 응답을 진짜 요약처럼 쓰지 않는다."""
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "형식이 하나도 안 맞는 응답입니다.",
            "provider": "gemini", "model": "gemini-3.6-flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "is_fallback": False
        }
        worker = self._make_worker(stub)
        summary = worker.generate_three_line_strategy_summary("KF-21 테스트", "본문", "DAPA")
        self.assertEqual(summary["data_source"], "template_fallback")
        self.assertIn("[주요 팩트]", summary["fact_summary"])

    def test_all_providers_failed_falls_back_to_template(self):
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "", "provider": "none", "model": None, "usage": None, "is_fallback": True
        }
        worker = self._make_worker(stub)
        summary = worker.generate_three_line_strategy_summary("드론 테스트", "본문", "DAPA")
        self.assertEqual(summary["data_source"], "template_fallback")

    def test_usage_is_recorded_to_ledger(self):
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.return_value = {
            "content": "[주요 팩트] a\n[경쟁 환경 및 산업 영향] b\n[전사 사업 전략 관점의 시사점] c",
            "provider": "deepseek", "model": "deepseek-chat",
            "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            "is_fallback": False
        }
        worker = self._make_worker(stub)
        worker.generate_three_line_strategy_summary("제목", "본문", "DAPA")
        summary = self.usage_ledger.summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertIn("deepseek", summary["by_provider"])
        self.assertEqual(summary["by_provider"]["deepseek"]["total_tokens"], 300)

    def test_no_llm_client_construction_hits_network_at_import_time(self):
        """llm_client 모듈 자체는 import만으로 API를 호출하지 않아야 한다(생성자는 env만 읽음)."""
        import llm_client
        client = llm_client.AntigravityLLMClient()
        self.assertEqual(client.last_provider_used, "NONE")

    def test_provider_targeting_does_not_fall_through_to_other_providers(self):
        """목표 이중검증처럼 '정말 다른 두 모델'이 필요한 호출: provider를 지정하면 그
        provider만 시도하고, 실패해도 다른 provider로 자동전환하지 않고 즉시 폴백해야 한다."""
        import llm_client
        client = llm_client.AntigravityLLMClient()
        with patch.object(client, "_try_gemini", return_value=None) as mock_gemini, \
             patch.object(client, "_try_grok") as mock_grok, \
             patch.object(client, "_try_deepseek") as mock_deepseek, \
             patch.object(client, "_try_openai") as mock_openai:
            result = client.chat_completion_with_meta([{"role": "user", "content": "x"}], provider="gemini")
        mock_gemini.assert_called_once()
        mock_grok.assert_not_called()
        mock_deepseek.assert_not_called()
        mock_openai.assert_not_called()
        self.assertTrue(result["is_fallback"])

    def test_provider_targeting_returns_that_providers_result(self):
        import llm_client
        client = llm_client.AntigravityLLMClient()
        with patch.object(client, "_try_deepseek", return_value={
            "content": "ok", "provider": "deepseek", "model": "deepseek-chat", "usage": None, "is_fallback": False
        }) as mock_deepseek, patch.object(client, "_try_gemini") as mock_gemini:
            result = client.chat_completion_with_meta([{"role": "user", "content": "x"}], provider="deepseek")
        mock_gemini.assert_not_called()
        mock_deepseek.assert_called_once()
        self.assertEqual(result["provider"], "deepseek")

    def test_min_tier_verified_skips_draft_tier_providers_in_cascade(self):
        """2026-09-12 Qwen(저사양) 사고 재발 방지: min_tier='verified'를 주면 draft
        등급 provider(gemini/grok/deepseek)는 캐스케이드에서도 아예 시도되지 않아야 한다."""
        import llm_client
        client = llm_client.AntigravityLLMClient()
        with patch.object(client, "_try_gemini") as mock_gemini, \
             patch.object(client, "_try_grok") as mock_grok, \
             patch.object(client, "_try_deepseek") as mock_deepseek, \
             patch.object(client, "_try_openai", return_value=None) as mock_openai:
            result = client.chat_completion_with_meta([{"role": "user", "content": "x"}], min_tier="verified")
        mock_gemini.assert_not_called()
        mock_grok.assert_not_called()
        mock_deepseek.assert_not_called()
        mock_openai.assert_called_once()
        self.assertTrue(result["is_fallback"])  # openai도 실패했으니 정직하게 폴백

    def test_min_tier_verified_rejects_explicit_draft_provider(self):
        """min_tier와 provider를 함께 줬는데 그 provider가 등급 미달이면, 그 provider가
        실제로 응답할 수 있어도 아예 시도하지 않고 폴백해야 한다(저사양 모델이 고신뢰
        판단을 조용히 대행하지 못하게)."""
        import llm_client
        client = llm_client.AntigravityLLMClient()
        with patch.object(client, "_try_gemini", return_value={
            "content": "ok", "provider": "gemini", "model": "x", "usage": None, "is_fallback": False
        }) as mock_gemini:
            result = client.chat_completion_with_meta([{"role": "user", "content": "x"}], provider="gemini", min_tier="verified")
        mock_gemini.assert_not_called()
        self.assertTrue(result["is_fallback"])

    def test_min_tier_verified_allows_openai(self):
        import llm_client
        client = llm_client.AntigravityLLMClient()
        with patch.object(client, "_try_openai", return_value={
            "content": "ok", "provider": "openai", "model": "gpt-4o-mini", "usage": None, "is_fallback": False
        }) as mock_openai:
            result = client.chat_completion_with_meta([{"role": "user", "content": "x"}], min_tier="verified")
        mock_openai.assert_called_once()
        self.assertFalse(result["is_fallback"])


class TestLocalCliProviders(unittest.TestCase):
    """2026-09-13: 소유자 지시로 추가된 codex_cli/claude_cli provider.
    실제 CLI를 호출하면 진짜 구독 쿼터를 쓰므로(codex exec 1회에 수천~수만 토큰 확인됨,
    2026-09-13 이 환경에서 직접 테스트), subprocess.run을 전부 mock해 로직만 검증한다."""

    def setUp(self):
        import llm_client
        self.client = llm_client.AntigravityLLMClient()

    def test_codex_cli_not_registered_in_default_cascade(self):
        """codex_cli/claude_cli는 명시 지정 없이는 절대 자동으로 안 불려야 한다
        (뉴스 요약 같은 흔한 draft 호출마다 구독 쿼터를 쓰면 안 됨)."""
        import llm_client
        self.assertNotIn("codex_cli", llm_client.AntigravityLLMClient._PROVIDER_TRIERS)
        self.assertNotIn("claude_cli", llm_client.AntigravityLLMClient._PROVIDER_TRIERS)

    def test_codex_cli_and_claude_cli_are_verified_tier(self):
        import llm_client
        self.assertEqual(llm_client.AntigravityLLMClient.PROVIDER_TIER["codex_cli"], "verified")
        self.assertEqual(llm_client.AntigravityLLMClient.PROVIDER_TIER["claude_cli"], "verified")

    def test_codex_cli_parses_output_last_message_file_and_usage(self):
        """codex exec --json 이벤트 스트림에서 usage를, -o 파일에서 최종 텍스트를 뽑는지 확인.
        2026-09-13 실제 CLI로 검증한 실제 출력 형태를 그대로 흉내낸다."""
        fake_jsonl = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":16880,"cached_input_tokens":12928,'
            '"cache_write_input_tokens":0,"output_tokens":28,"reasoning_output_tokens":0}}\n'
        )

        def fake_run(cmd, **kwargs):
            # -o 뒤 인자가 output-last-message 파일 경로
            out_path = cmd[cmd.index("-o") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write('{"verdict": "COMPLETE_100", "confidence": 0.9, "reason": "test"}')
            return unittest.mock.Mock(returncode=0, stdout=fake_jsonl, stderr="")

        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", side_effect=fake_run):
            result = self.client._try_codex_cli([{"role": "user", "content": "x"}], None, 0.0, 300)

        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "codex_cli")
        self.assertFalse(result["is_fallback"])
        self.assertIn("COMPLETE_100", result["content"])
        self.assertEqual(result["usage"]["prompt_tokens"], 16880)
        self.assertEqual(result["usage"]["completion_tokens"], 28)

    def test_codex_cli_returns_none_on_nonzero_exit(self):
        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", return_value=unittest.mock.Mock(returncode=1, stdout="", stderr="auth error")):
            result = self.client._try_codex_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIsNone(result)

    def test_codex_cli_returns_none_on_timeout(self):
        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=120)):
            result = self.client._try_codex_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIsNone(result)

    def test_codex_cli_returns_none_when_binary_missing(self):
        with patch("llm_client.os.path.exists", return_value=False):
            result = self.client._try_codex_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIsNone(result)

    def test_claude_cli_parses_json_result_field(self):
        fake_stdout = json.dumps({
            "result": '{"verdict": "NOT_COMPLETE", "confidence": 0.3, "reason": "부족"}',
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "output_tokens": 40}
        })
        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", return_value=unittest.mock.Mock(returncode=0, stdout=fake_stdout, stderr="")):
            result = self.client._try_claude_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "claude_cli")
        self.assertIn("NOT_COMPLETE", result["content"])

    def test_claude_cli_returns_none_on_nested_session_failure(self):
        """실제로 이 세션 안에서 claude CLI를 부르면 '중첩 세션' 에러로 실패한다
        (2026-09-13 직접 확인) - 이 경우 크래시가 아니라 None(is_fallback 처리)이어야 한다."""
        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", return_value=unittest.mock.Mock(
                 returncode=1, stdout="",
                 stderr="Error: Claude Code cannot be launched inside another Claude Code session."
             )):
            result = self.client._try_claude_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIsNone(result)

    def test_claude_cli_disallows_mutating_tools(self):
        """호출 커맨드에 파일/셸 수정 도구가 비허용 목록에 포함되는지 확인 - 검증
        호출이 실수로 파일을 바꾸면 안 된다."""
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return unittest.mock.Mock(returncode=0, stdout=json.dumps({"result": "ok"}), stderr="")

        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", side_effect=fake_run):
            self.client._try_claude_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        disallowed_idx = captured_cmd.index("--disallowedTools")
        disallowed_value = captured_cmd[disallowed_idx + 1]
        for tool in ("Bash", "Edit", "Write"):
            self.assertIn(tool, disallowed_value)

    def test_codex_cli_uses_read_only_sandbox(self):
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            out_path = cmd[cmd.index("-o") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("ok")
            return unittest.mock.Mock(returncode=0, stdout="", stderr="")

        with patch("llm_client.os.path.exists", return_value=True), \
             patch("llm_client.subprocess.run", side_effect=fake_run):
            self.client._try_codex_cli([{"role": "user", "content": "x"}], None, 0.0, 300)
        self.assertIn("read-only", captured_cmd)


class TestEnvLoaderIncludesWorkspaceEnv(unittest.TestCase):
    def test_workspace_env_file_is_actually_loaded(self):
        """이전에는 antigravity_workspace/.env 자체가 후보 목록에 없어서, 이 워크스페이스에
        새로 추가한 환경변수(ANTIGRAVITY_BRIDGE_API_KEY 등)가 실제로는 로드된 적이 없었다."""
        import config.env_loader as env_loader
        loaded_paths = env_loader.load_unified_env()
        self.assertTrue(any(p.endswith("antigravity_workspace/.env") for p in loaded_paths))


class TestGoalIntakeDaemon(unittest.TestCase):
    def setUp(self):
        self.tmp_ledger = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_ledger.close()
        self.ledger = StateLedger(db_path=self.tmp_ledger.name)

    def tearDown(self):
        os.unlink(self.tmp_ledger.name)

    def _make_daemon(self, pm_owner=None):
        import goal_intake_daemon
        return goal_intake_daemon.GoalIntakeDaemon(
            bot_token="test-goal-bot-token",
            allowed_chat_id="12345",
            pm_owner=pm_owner or L1PMOwner(auto_heal=True),
            ledger=self.ledger,
        )

    def test_message_from_disallowed_chat_id_is_ignored(self):
        """소유자 확인: TELEGRAM_CHAT_ID 일치 발신만 명령으로 인정, 다른 chat_id는 무시."""
        daemon = self._make_daemon()
        with patch("goal_intake_daemon.requests.post") as mock_post:
            result = daemon.handle_update({
                "update_id": 1,
                "message": {"chat": {"id": 99999}, "text": "삼성전자 퀀트 리밸런싱해줘"}
            })
        self.assertIsNone(result)
        mock_post.assert_not_called()  # 무시된 메시지는 응답도 보내지 않는다
        self.assertEqual(self.ledger.list_domain("goal_intake"), [])

    def test_message_from_allowed_chat_id_creates_task_and_acks(self):
        daemon = self._make_daemon()
        with patch("goal_intake_daemon.requests.post") as mock_post:
            mock_post.return_value.ok = True
            tasks = daemon.handle_update({
                "update_id": 2,
                "message": {"chat": {"id": 12345}, "text": "삼성전자 퀀트 리밸런싱해줘"}
            })
        self.assertGreaterEqual(len(tasks), 1)
        mock_post.assert_called_once()
        sent_text = mock_post.call_args.kwargs["json"]["text"]
        self.assertIn("접수 완료", sent_text)
        ledger_tasks = self.ledger.list_domain("goal_intake")
        self.assertEqual(len(ledger_tasks), len(tasks))
        self.assertEqual(ledger_tasks[0]["chat_id"], "12345")

    def test_unclassified_request_acks_without_creating_task(self):
        daemon = self._make_daemon()
        with patch("goal_intake_daemon.requests.post") as mock_post:
            mock_post.return_value.ok = True
            tasks = daemon.handle_update({
                "update_id": 3,
                "message": {"chat": {"id": 12345}, "text": "오늘 날씨 어때?"}
            })
        self.assertEqual(tasks, [])
        mock_post.assert_called_once()
        self.assertIn("이해하지 못했습니다", mock_post.call_args.kwargs["json"]["text"])
        self.assertEqual(self.ledger.list_domain("goal_intake"), [])

    def test_no_bot_token_refuses_to_run(self):
        """bot_token=""은 falsy라 daemon이 os.getenv(GOAL_INTAKE_BOT_TOKEN)으로 폴백한다
        (다른 생성자들의 or 패턴과 동일) - 실제 .env에 값이 들어있을 수 있으므로 그 값도
        비운 상태로 격리해서 검증한다."""
        import goal_intake_daemon
        with patch.dict(os.environ, {"GOAL_INTAKE_BOT_TOKEN": ""}):
            daemon = goal_intake_daemon.GoalIntakeDaemon(bot_token="", allowed_chat_id="12345", ledger=self.ledger)
            with self.assertRaises(RuntimeError):
                daemon.fetch_updates()

    def test_offset_advances_past_processed_update(self):
        """같은 update를 다시 받지 않도록 offset이 update_id+1로 전진해야 한다."""
        daemon = self._make_daemon()
        with patch("goal_intake_daemon.requests.post") as mock_post:
            mock_post.return_value.ok = True
            daemon.handle_update({"update_id": 41, "message": {"chat": {"id": 12345}, "text": "오늘 날씨 어때?"}})
        self.assertEqual(daemon._offset, 42)


if __name__ == "__main__":
    unittest.main()
