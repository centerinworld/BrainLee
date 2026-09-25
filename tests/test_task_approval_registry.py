"""작업 승인 대기 큐 / 사람 승인 감사 원장 계약 테스트 (2026-09-24 신규).

고정하는 것
  1. 사람 승인 3요건 — 신원(사람)·confirm(엄격 boolean)·사유(>=10자) 미충족 시 거부 + 무기록.
  2. 자동승인 차단 — 에이전트 신원(code-doer/planner-bot/hermes/GPT…)은 403.
  3. 재클릭 멱등성 — 같은 결재 요청 재전송은 writes=0, 두 테이블 행수 불변.
  4. 근거 변경은 409 — 승인 후 다른 사유로 재승인 불가(철회 먼저).
  5. 감사 read-back — approved_by/approved_at/audit_note 가 원장에서 그대로 읽힌다.
  6. LIVE 주문 경로 무연결 — `record_decision(` 호출처는 registry + 라우터 뿐이고,
     금지 테이블(live_orders/live_strategy_approvals/risk_gate_decisions…)에 쓰지 않는다.
  7. SQLite 방언 DDL 이 PostgreSQL 로 번역 가능(AUTOINCREMENT → IDENTITY).

라이브 DB 무기록: 모든 테스트는 임시 SQLite 파일을 registry._open 으로 주입한다.
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import task_approval_registry as registry
from db_compat import translate_sqlite_sql
from routes.task_approvals import router as approval_router

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TABLES = (
    "live_orders",
    "live_order_events",
    "live_strategy_approvals",
    "risk_gate_decisions",
)


def _temp_conn_factory(tmpdir: str):
    path = str(Path(tmpdir) / "approval_test.db")

    def _open():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    return _open, path


class ApprovalTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._open, self._path = _temp_conn_factory(self._tmp.name)
        self._patch = patch.object(registry, "_open", self._open)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)
        self.queue_file = Path(self._tmp.name) / "frontier_handoff_queue.json"
        self.queue_file.write_text(json.dumps([
            {
                "task_key": "TASK_TEST_APPROVAL",
                "title": "테스트 작업",
                "target_model": "codex",
                "pipeline": "3STAGE_FRONTIER_VERIFIED",
                "status": "QUEUED_FOR_FRONTIER",
                "created_at": "2026-09-13 15:02:58",
                "prompt_preview": "대상 파일: runtime/scripts/foo.py, runtime/backtest_strategies/sector.py",
            },
            {"task_key": "TASK_ALREADY_DONE", "title": "끝난 작업", "status": "COMPLETED"},
        ], ensure_ascii=False), encoding="utf-8")
        self._qpatch = patch.object(registry, "QUEUE_FILE", self.queue_file)
        self._qpatch.start()
        self.addCleanup(self._qpatch.stop)

    # ── helpers ────────────────────────────────────────────
    def _counts(self) -> tuple[int, int]:
        conn = self._open()
        try:
            out = []
            for table in ("task_approvals", "task_approval_events"):
                try:
                    out.append(int(conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]))
                except sqlite3.OperationalError:
                    out.append(0)
            return tuple(out)
        finally:
            conn.close()

    def _approve(self, **kw):
        payload = {
            "task_key": "TASK_TEST_APPROVAL",
            "action": "approve",
            "approved_by": "brainlee",
            "audit_note": "백테스트 편중 리스크 72.9% 해소 확인 후 승인합니다.",
            "confirm": True,
        }
        payload.update(kw)
        return registry.record_decision(**payload)

    # ── 1~4. 사람 승인 3요건 / 자동승인 차단 ───────────────
    def test_confirm_flag_must_be_a_real_boolean(self):
        for bad in (None, "true", 1, False):
            with self.assertRaises(registry.ApprovalRejected) as ctx:
                self._approve(confirm=bad)
            self.assertEqual(ctx.exception.reason, "confirm_required")
        self.assertEqual(self._counts(), (0, 0))

    def test_agent_identity_can_never_approve(self):
        for actor in ("code-doer", "planner-bot", "checker", "hermes", "GPT", "deepseek", "auto", "수행봇"):
            with self.assertRaises(registry.ApprovalRejected) as ctx:
                self._approve(approved_by=actor)
            self.assertEqual(ctx.exception.status, 403, actor)
            self.assertEqual(ctx.exception.reason, "actor_is_automation", actor)
        self.assertEqual(self._counts(), (0, 0))

    def test_blank_actor_and_short_note_are_rejected(self):
        for actor in ("", "   "):
            with self.assertRaises(registry.ApprovalRejected) as ctx:
                self._approve(approved_by=actor)
            self.assertEqual(ctx.exception.reason, "actor_required")
        for note in ("", "짧음", "123456789"):
            with self.assertRaises(registry.ApprovalRejected) as ctx:
                self._approve(audit_note=note)
            self.assertEqual(ctx.exception.reason, "audit_note_too_short")
        self.assertEqual(self._counts(), (0, 0))

    def test_unregistered_or_closed_task_cannot_be_approved(self):
        with self.assertRaises(registry.ApprovalRejected) as ctx:
            self._approve(task_key="TASK_NOT_IN_QUEUE")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(registry.ApprovalRejected) as ctx:
            self._approve(task_key="TASK_ALREADY_DONE")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self._counts(), (0, 0))

    # ── 5. 정상 승인 + read-back ───────────────────────────
    def test_human_click_records_audit_and_reads_back(self):
        result = self._approve()
        self.assertEqual(result["result"], "approved")
        self.assertEqual(result["writes"], 2)
        self.assertEqual(result["approval"]["live_order_linked"], 0)
        self.assertEqual(result["approval"]["approved_by"], "brainlee")
        self.assertTrue(result["approval"]["approved_at"])

        self.assertEqual(self._counts(), (1, 1))
        approvals = registry.list_approvals()
        events = registry.list_events()
        self.assertEqual(len(approvals), 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["task_key"], "TASK_TEST_APPROVAL")
        self.assertEqual(events[0]["audit_note"], "백테스트 편중 리스크 72.9% 해소 확인 후 승인합니다.")
        self.assertEqual(events[0]["approved_by"], "brainlee")
        self.assertEqual(events[0]["live_order_linked"], 0)
        snapshot = json.loads(events[0]["snapshot_json"])
        self.assertEqual(snapshot["task"]["source_ref"], "frontier_handoff_queue.json")
        self.assertEqual(snapshot["task"]["status"], "QUEUED_FOR_FRONTIER")

    def test_reclick_same_request_is_idempotent(self):
        self._approve()
        before = self._counts()
        replay = self._approve()
        self.assertEqual(replay["result"], "idempotent_replay")
        self.assertEqual(replay["writes"], 0)
        self.assertEqual(self._counts(), before)
        # 다른 승인자가 사유를 바꿔 재승인하는 것도 조용히 덮어쓰지 않는다(철회 먼저).
        with self.assertRaises(registry.ApprovalRejected) as ctx:
            self._approve(approved_by="brainlee2")
        self.assertEqual(ctx.exception.reason, "basis_changed")
        self.assertEqual(self._counts(), before)
        # 승인자만 다르고 사유가 같으면? 승인자도 지문에 포함되므로 여전히 근거 변경이다.
        with self.assertRaises(registry.ApprovalRejected):
            self._approve(approved_by="brainlee3")

    def test_changed_basis_requires_revoke_first(self):
        self._approve()
        with self.assertRaises(registry.ApprovalRejected) as ctx:
            self._approve(audit_note="사유를 슬그머니 바꿔 재승인 시도합니다.")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "basis_changed")
        self.assertEqual(self._counts(), (1, 1))

        revoke = registry.record_decision(
            task_key="TASK_TEST_APPROVAL", action="revoke", approved_by="brainlee",
            audit_note="근거 재확인 필요 — 우선 철회합니다.", confirm=True,
        )
        self.assertEqual(revoke["result"], "revoked")
        self.assertEqual(self._counts(), (1, 2))
        again = self._approve()
        self.assertEqual(again["result"], "approved")
        self.assertEqual(self._counts(), (1, 3))
        self.assertEqual(registry.list_approvals()[0]["status"], "approved")

    # ── 6. LIVE 주문 무연결 (구조 불변식) ──────────────────
    def test_module_never_touches_live_order_tables(self):
        import ast

        registry_path = ROOT / "task_approval_registry.py"
        route_path = ROOT / "routes" / "task_approvals.py"
        sources = {
            "registry": registry_path.read_text(encoding="utf-8"),
            "route": route_path.read_text(encoding="utf-8"),
        }
        for name, src in sources.items():
            upper = src.upper().replace("IF NOT EXISTS ", "")
            for table in FORBIDDEN_TABLES:
                for stmt in ("INSERT INTO", "UPDATE", "DELETE FROM", "CREATE TABLE"):
                    self.assertNotIn(f"{stmt} {table.upper()}", upper,
                                     f"{name} 이 금지 테이블 {table} 을 {stmt} 합니다")
        # 라우터·레지스트리는 LIVE 주문 모듈을 import 하지 않는다(AST 기준 — docstring 언급은 무관).
        for label, path in (("registry", registry_path), ("route", route_path)):
            modules = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module)
            for module in modules:
                self.assertNotIn("kis_trading", module, f"{label}: {module} import 금지")
                self.assertNotIn("live_trading_data", module, f"{label}: {module} import 금지")
        # 승인 기록 함수의 호출처는 registry(정의) + 라우터뿐이어야 한다(자동승인 경로 차단).
        # 테스트 코드는 대상이 아니다 — '프로덕션 경로에서 자동승인 호출이 가능한가'가 불변식이다.
        call_pattern = re.compile(r"(?:\.|\b)record_decision\s*\(")
        callers = []
        for path in ROOT.rglob("*.py"):
            if any(part in {".git", "venv", "node_modules", "__pycache__", "worktrees",
                            ".claude", "tests", "archives", "scratch"}
                   for part in path.relative_to(ROOT).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if call_pattern.search(text):
                callers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(callers), [
            "routes/task_approvals.py",
            "scripts/verify_task_approval_pg_path.py",
            "task_approval_registry.py",
        ])
        # 예외는 검증 스크립트 1개뿐이고, 그 스크립트는 커밋을 무력화하고 롤백해야 한다(라이브 무기록).
        probe = (ROOT / "scripts" / "verify_task_approval_pg_path.py").read_text(encoding="utf-8")
        self.assertIn("_no_commit", probe)
        self.assertIn("conn.rollback()", probe)

    def test_ddl_is_postgres_translatable(self):
        approvals_ddl = translate_sqlite_sql(registry.DDL[0])
        events_ddl = translate_sqlite_sql(registry.DDL[1])
        self.assertNotIn("AUTOINCREMENT", events_ddl.upper())
        self.assertIn("IDENTITY", events_ddl.upper())
        self.assertIn("PRIMARY KEY", approvals_ddl.upper())
        self.assertIn("task_approval_events", events_ddl)
        # idempotency_key 는 논리 지문일 뿐 UNIQUE 제약이 아니다:
        # 승인→철회→재승인 사이클은 같은 지문을 정당하게 재사용하므로 UNIQUE 면 정상 동작이 죽는다.
        self.assertNotIn("UNIQUE", events_ddl.upper())
        self.assertIn("CREATE INDEX", translate_sqlite_sql(registry.DDL[2]).upper())

    # ── 큐 수집 ────────────────────────────────────────────
    def test_pending_queue_flags_pending_task_and_ignores_closed_one(self):
        payload = registry.pending_tasks(queue_path=self.queue_file,
                                          store_path=Path(self._tmp.name) / "absent.json")
        by_key = {t["task_key"]: t for t in payload["tasks"]}
        self.assertTrue(by_key["TASK_TEST_APPROVAL"]["approvable"])
        self.assertFalse(by_key["TASK_ALREADY_DONE"]["approvable"])
        self.assertIn("runtime/scripts/foo.py", by_key["TASK_TEST_APPROVAL"]["target_files"])
        self.assertEqual(payload["queue"]["pending_total"], 1)
        self.assertFalse(payload["live_order_linked"])
        # 참고 테이블은 임시 DB에 없으므로 '없음'으로 보고되고, 승인 가능으로 표시되지 않는다.
        for ref in payload["reference"].values():
            self.assertFalse(ref["approvable"])
            self.assertIsNone(ref["count"])
        self.assertEqual(self._counts(), (0, 0), "GET 경로가 스키마/행을 만들면 안 된다")

    def test_cross_store_disagreement_is_surfaced(self):
        store = Path(self._tmp.name) / "agi_tasks_store.json"
        store.write_text(json.dumps([
            {"task_key": "TASK_TEST_APPROVAL", "status": "COMPLETED"},
        ], ensure_ascii=False), encoding="utf-8")
        payload = registry.pending_tasks(queue_path=self.queue_file, store_path=store)
        note = {t["task_key"]: t["cross_store_note"] for t in payload["tasks"]}["TASK_TEST_APPROVAL"]
        self.assertIn("COMPLETED", note)

    # ── HTTP 계층 ──────────────────────────────────────────
    def _client(self):
        app = FastAPI()
        app.include_router(approval_router, prefix="/api/task-approvals")
        return TestClient(app)

    def test_http_pending_reads_without_writing(self):
        with self._client() as client:
            r = client.get("/api/task-approvals/pending")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["queue"]["pending_total"], 1)
            self.assertEqual(body["approvals"], [])
        self.assertEqual(self._counts(), (0, 0))

    def test_http_rejects_automation_and_weak_confirmation(self):
        with self._client() as client:
            base = {"task_key": "TASK_TEST_APPROVAL",
                    "audit_note": "충분히 긴 승인 사유입니다 확인.", "confirm": True}
            agent = client.post("/api/task-approvals/approve", json={**base, "approved_by": "code-doer"})
            self.assertEqual(agent.status_code, 403)
            self.assertEqual(agent.json()["detail"]["reason"], "actor_is_automation")
            missing = client.post("/api/task-approvals/approve", json={**base, "approved_by": "brainlee", "confirm": None})
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(missing.json()["detail"]["reason"], "confirm_required")
            stringy = client.post("/api/task-approvals/approve",
                                  json={**base, "approved_by": "brainlee", "confirm": "true"})
            self.assertEqual(stringy.status_code, 422, "문자열 confirm 은 스키마에서 거부돼야 한다")
        self.assertEqual(self._counts(), (0, 0))

    def test_http_approve_replay_and_ledger_readback(self):
        with self._client() as client:
            body = {"task_key": "TASK_TEST_APPROVAL", "approved_by": "brainlee",
                    "audit_note": "사람이 화면에서 클릭한 승인입니다.", "confirm": True}
            first = client.post("/api/task-approvals/approve", json=body)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["result"], "approved")
            second = client.post("/api/task-approvals/approve", json=body)
            self.assertEqual(second.json()["result"], "idempotent_replay")
            ledger = client.get("/api/task-approvals/ledger").json()
            self.assertEqual(len(ledger["events"]), 1)
            self.assertEqual(ledger["events"][0]["approved_by"], "brainlee")
            self.assertEqual(ledger["events"][0]["audit_note"], "사람이 화면에서 클릭한 승인입니다.")
            self.assertFalse(ledger["live_order_linked"])
            policy = client.get("/api/task-approvals/policy").json()
            self.assertFalse(policy["live_order_linked"])
            self.assertIn("live_strategy_approvals", policy["never_written"])
            self.assertIn("task_approvals", policy["writable_tables"])
        self.assertEqual(self._counts(), (1, 1))

    def test_live_pending_registry_flags_current_unapproved_task(self):
        """라이브 큐(frontier_handoff_queue.json)를 실제로 읽어 미승인 작업을 노출하는지."""
        real = json.loads((ROOT / "frontier_handoff_queue.json").read_text(encoding="utf-8"))
        expected = {str(x["task_key"]) for x in real
                    if str(x.get("status", "")).upper() in registry.PENDING_STATES}
        payload = registry.pending_tasks(conn=self._open())
        got = {t["task_key"] for t in payload["tasks"] if t["approvable"]}
        self.assertEqual(got, expected)
        for task in payload["tasks"]:
            if task["approvable"]:
                self.assertTrue(task["title"])
                self.assertEqual(task["source_ref"], "frontier_handoff_queue.json")


if __name__ == "__main__":
    unittest.main()
