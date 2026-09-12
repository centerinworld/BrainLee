"""
H01 회귀 테스트: 2026-09-12 핸드오프 문서 A01~A06 수정 사항 검증.
각 테스트는 수정 전 실제로 존재했던 결함을 재현하는 조건에서, 수정 후 코드가
더 이상 그 결함을 일으키지 않음을 확인한다.
"""

import os
import sys
import asyncio
import tempfile
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
from fastapi import HTTPException
import bridge_api
from memory.vector_store import MemoryVectorStore
from memory.state_ledger import StateLedger

# config.env_loader가 import 시점에 stock_dashboard/runtime/.env의 실제 POSTGRES_DATABASE_URL을
# 프로세스 환경에 심어두므로(A10), SQLite 폴백 경로를 테스트할 때는 이 값으로 명시적으로
# postgres_url을 덮어써 실제 운영 DB에 붙지 않도록 격리한다.
UNREACHABLE_PG_URL = "postgresql://nouser:nopass@127.0.0.1:1/doesnotexist"


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
        orchestrator = ContentOrchestrator()
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
        orchestrator1 = ContentOrchestrator(vector_store=make_isolated_vector_store(), ledger=ledger)
        result1 = asyncio.run(orchestrator1.run_defense_intelligence_cycle())
        self.assertGreater(result1["filtered_and_prepared"], 0)

        # "재시작" 시뮬레이션: vector_store는 또 새 것(dedup 이력 없음)이지만 ledger는 공유
        orchestrator2 = ContentOrchestrator(vector_store=make_isolated_vector_store(), ledger=ledger)
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


if __name__ == "__main__":
    unittest.main()
