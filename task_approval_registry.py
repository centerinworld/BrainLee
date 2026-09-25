"""작업 승인 대기 큐 + 사람 승인 감사 원장 (2026-09-24 신규).

목적
  - 에이전트가 스스로 결정하지 못하고 **사용자 명시 클릭**으로만 결재해야 하는
    "미승인 작업"을 한 곳에 모아 보여준다.
  - 승인/철회에는 actor(승인자)·시각·사유(audit_note)를 남겨 감사 가능하게 한다(read-back).

절대 제약 (설계 불변식, 테스트가 강제한다)
  1. 이 모듈은 LIVE 주문 경로와 **어떤 연결도 갖지 않는다**.
     `live_orders` / `live_order_events` / `live_strategy_approvals` / `risk_gate_decisions`
     / `kis_*` 주문 테이블에 쓰지도, 읽어서 승인 상태로 승격하지도 않는다.
     (`live_strategy_approvals` 는 개수만 참고 표시하며, 이 탭에서 변경 불가.)
  2. 승인 기록은 **사람 신원 + confirm=True + 최소 길이 사유**를 모두 만족할 때만 생성된다.
     에이전트 신원(code-doer/planner/checker/hermes/…, *bot, *gpt, agent*)은 거부한다.
  3. 저장은 라이브 프라이머리(PostgreSQL)로 라우팅되는 `db_compat.connect_primary_db` 를
     쓰되, 스키마/행 생성은 **첫 사람 승인 시점에만** 일어난다(lazy DDL).
     아무도 클릭하지 않으면 DB 쓰기는 0건이다.

SQL 방언
  소스에는 SQLite 방언으로 쓴다(하우스 컨벤션). PostgreSQL 에서는
  `db_compat.translate_sqlite_sql` 이 `INTEGER PRIMARY KEY AUTOINCREMENT` 등을 번역한다.
  → 같은 SQL이 임시 SQLite(단위테스트)와 라이브 PG(운영) 양쪽에서 유효하다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")

# 미승인(결재 대기)으로 볼 상태값. 그 외(APPROVED/COMPLETED/REJECTED/…)는 대기 목록에서 제외.
PENDING_STATES = {
    "QUEUED_FOR_FRONTIER",
    "QUEUED_FOR_APPROVAL",
    "QUEUED",
    "PENDING",
    "WAITING_APPROVAL",
}

QUEUE_FILE = ROOT / "frontier_handoff_queue.json"
TASK_STORE_FILE = ROOT / "agi_tasks_store.json"

# 사람 승인 최소 사유 길이(문자). 근거 없는 클릭을 막는다.
MIN_AUDIT_NOTE = 10
MAX_AUDIT_NOTE = 4000

ACTIONS = {"approve": "approved", "revoke": "revoked"}

# 자동승인 차단: 정규화(소문자, 영숫자만) 후 아래 집합과 일치하거나
# 접두/접미 패턴에 걸리면 사람이 아닌 것으로 본다.
_AGENT_IDENTITIES = {
    "ai", "agent", "assistant", "auto", "autogpt", "bot", "checker", "claude",
    "codex", "codedoer", "deepseek", "gemini", "gpt", "hermes", "llm",
    "openai", "planner", "plannerbot", "qwen", "robot", "system",
}
_AGENT_SUFFIX = ("bot", "agent", "ai")
_AGENT_PREFIX = ("agent", "auto", "bot")


class ApprovalRejected(Exception):
    """사람 승인 조건을 만족하지 못한 요청. status 는 HTTP 코드로 매핑된다."""

    def __init__(self, reason: str, status: int = 400, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.status = status
        self.detail = detail or reason


# ── 공통 유틸 ─────────────────────────────────────────────
def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _open():
    """라이브 프라이머리 연결(PostgreSQL 라우팅). 테스트는 이 함수를 패치한다."""
    from db_compat import connect_primary_db

    return connect_primary_db(timeout=30, row_factory=sqlite3.Row)


def normalize_actor(actor: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (actor or "").lower())


def is_agent_identity(actor: str) -> bool:
    """사람이 아닌(=자동/에이전트) 신원인가."""
    norm = normalize_actor(actor)
    if not norm:
        return True
    if norm in _AGENT_IDENTITIES:
        return True
    if norm.endswith(_AGENT_SUFFIX) or norm.startswith(_AGENT_PREFIX):
        return True
    return False


def validate_human_approval(approved_by: str, audit_note: str, confirm: Any) -> tuple[str, str]:
    """사람 승인 3요건(신원·확인 플래그·사유)을 검사하고 정규화된 값을 돌려준다."""
    actor = (approved_by or "").strip()
    note = (audit_note or "").strip()
    if confirm is not True:
        raise ApprovalRejected(
            "confirm_required", 400,
            "confirm=true 를 명시해야 합니다. 자동 호출/오클릭으로는 승인되지 않습니다.",
        )
    if not actor:
        raise ApprovalRejected("actor_required", 400, "approved_by(승인자)가 필요합니다.")
    if len(actor) > 60:
        raise ApprovalRejected("actor_too_long", 400, "approved_by 는 60자 이내여야 합니다.")
    if is_agent_identity(actor):
        raise ApprovalRejected(
            "actor_is_automation", 403,
            f"'{actor}' 는 사람 신원으로 인정되지 않습니다 — 자동승인 금지(에이전트는 자기 결과를 승인할 수 없습니다).",
        )
    if len(note) < MIN_AUDIT_NOTE:
        raise ApprovalRejected(
            "audit_note_too_short", 400,
            f"audit_note 는 {MIN_AUDIT_NOTE}자 이상이어야 합니다(승인 근거 기록).",
        )
    if len(note) > MAX_AUDIT_NOTE:
        raise ApprovalRejected("audit_note_too_long", 400, "audit_note 가 너무 깁니다.")
    return actor, note


def request_hash(task_key: str, action: str, approved_by: str, audit_note: str) -> str:
    """같은 결재 요청(대상·행위·승인자·사유)은 같은 해시 → 재클릭 멱등성의 기준값."""
    payload = "\x1f".join([task_key, action, approved_by.strip(), audit_note.strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── DDL ──────────────────────────────────────────────────
DDL = (
    """CREATE TABLE IF NOT EXISTS task_approvals (
        task_key TEXT PRIMARY KEY,
        task_title TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        status TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        approved_at TEXT NOT NULL,
        audit_note TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        live_order_linked INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS task_approval_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotency_key TEXT NOT NULL,
        task_key TEXT NOT NULL,
        action TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        approved_at TEXT NOT NULL,
        audit_note TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        live_order_linked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_task_approval_events_task ON task_approval_events(task_key, id)",
)

# idempotency_key 는 '논리 지문'이며 UNIQUE 제약이 아니다:
# 승인→철회→재승인 사이클은 같은 지문을 정당하게 재사용한다(별개 사건). 중복 저장은
# ①같은 상태+같은 request_hash 조기 반환(재클릭 멱등성) ②task_approvals 의 PRIMARY KEY
# ③그 PK 충돌 시 rollback 후 read-back 재시도 로 막는다.


def ensure_schema(conn, *, commit: bool = True) -> None:
    """스키마 생성(지연 DDL). commit=False 는 검증용 — 트랜잭션 안에서만 만들고 롤백한다."""
    for stmt in DDL:
        conn.execute(stmt)
    if commit:
        conn.commit()


# ── 결재 대상(미승인 작업) 수집 — 전부 읽기전용 ────────────
def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "file_missing"
    except Exception as exc:  # noqa: BLE001 - 손상 파일도 화면에 사유로 노출
        return None, f"read_error: {exc}"


def _queue_tasks(queue_path: Path) -> tuple[list[dict], str | None]:
    raw, err = _read_json(queue_path)
    if err:
        return [], err
    items = raw if isinstance(raw, list) else list((raw or {}).get("items", []))
    tasks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("task_key") or item.get("id") or "").strip()
        if not key:
            continue
        status = str(item.get("status") or "").upper()
        tasks.append({
            "task_key": key,
            "title": item.get("title") or key,
            "source_type": "frontier_handoff_queue",
            "source_ref": str(queue_path.name),
            "status": status,
            "created_at": item.get("created_at"),
            "target_model": item.get("target_model"),
            "pipeline": item.get("pipeline"),
            "prompt_preview": (item.get("prompt_preview") or "")[:1200],
            "approvable": status in PENDING_STATES,
            "not_approvable_reason": None if status in PENDING_STATES else f"이미 종료된 상태({status})",
            "target_files": _target_files(item.get("prompt_preview") or ""),
        })
    return tasks, None


_TARGET_RE = re.compile(r"([A-Za-z0-9_./\-]+\.(?:py|jsx|js|json|md|sql))")


def _target_files(text: str) -> list[str]:
    seen: list[str] = []
    for m in _TARGET_RE.finditer(text or ""):
        value = m.group(1)
        if value not in seen:
            seen.append(value)
    return seen[:12]


def _count_table(conn, table: str) -> tuple[int | None, str | None]:
    """참고 테이블 개수. 스키마/테이블이 없으면 (None, 사유). 절대 쓰지 않는다."""
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0]), None
    except Exception as exc:  # noqa: BLE001
        return None, f"unavailable: {type(exc).__name__}"


def _cross_store_notes(store_path: Path, queue_tasks: Iterable[dict]) -> dict[str, str]:
    """다른 스토어(agi_tasks_store.json)와 상태가 어긋나는 키를 근거로 노출한다."""
    raw, err = _read_json(store_path)
    if err:
        return {}
    rows = raw if isinstance(raw, list) else list((raw or {}).get("tasks", []))
    by_key: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("task_key"):
            by_key[str(row["task_key"])] = str(row.get("status") or "")
    notes = {}
    for task in queue_tasks:
        other = by_key.get(task["task_key"])
        if other and other.upper() not in PENDING_STATES and task["approvable"]:
            notes[task["task_key"]] = (
                f"{store_path.name} 에서는 '{other}' 로 기록돼 있습니다 — "
                "두 스토어가 불일치합니다. 승인 전 실제 수행 여부를 확인하세요."
            )
    return notes


def pending_tasks(queue_path: Path | str = QUEUE_FILE,
                  store_path: Path | str = TASK_STORE_FILE,
                  conn=None) -> dict:
    """미승인 작업 + 참고 테이블 현황 + 이미 기록된 승인 상태."""
    queue_path = Path(queue_path)
    store_path = Path(store_path)
    tasks, queue_error = _queue_tasks(queue_path)
    notes = _cross_store_notes(store_path, tasks)

    owns = conn is None
    if owns:
        conn = _open()
    try:
        approvals = {row["task_key"]: dict(row) for row in _select_approvals(conn)}
    finally:
        if owns and conn is not None:
            conn.close()

    for task in tasks:
        record = approvals.get(task["task_key"])
        task["approval"] = record
        task["cross_store_note"] = notes.get(task["task_key"])

    reference = _reference_counts()

    return {
        "read_at": _now(),
        "store": "postgresql",
        "live_order_linked": False,
        "queue": {"path": str(queue_path), "error": queue_error,
                  "pending_total": sum(1 for t in tasks if t["approvable"])},
        "tasks": tasks,
        "approvals": sorted(approvals.values(), key=lambda r: r["updated_at"], reverse=True),
        "reference": reference,
        "policy": policy(),
    }


def _reference_counts() -> dict[str, Any]:
    """참고용 테이블 개수(절대 변경하지 않음). 별도 연결로 조회한다."""
    out: dict[str, Any] = {}
    conn = None
    try:
        conn = _open()
        count, err = _count_table(conn, "investment_decision_tasks")
        out["investment_decision_tasks"] = {
            "count": count, "error": err, "approvable": False,
            "note": "LLM 검토 작업 큐입니다. 이 탭에서 승인/변경하지 않습니다.",
            "table": "investment_decision_tasks",
        }
        count, err = _count_table(conn, "live_strategy_approvals")
        out["live_strategy_approvals"] = {
            "count": count, "error": err, "approvable": False,
            "note": "실주문 승인 테이블입니다. 이 기능은 어떤 경우에도 여기에 쓰지 않습니다(읽기 전용 참고).",
            "table": "live_strategy_approvals",
        }
    except Exception as exc:  # noqa: BLE001
        for name in ("investment_decision_tasks", "live_strategy_approvals"):
            out.setdefault(name, {"count": None, "error": f"connect_failed: {exc}",
                                  "approvable": False, "table": name, "note": ""})
    finally:
        if conn is not None:
            conn.close()
    return out


def _select_approvals(conn) -> list:
    try:
        rows = conn.execute("SELECT * FROM task_approvals ORDER BY updated_at DESC").fetchall()
    except Exception:  # noqa: BLE001 - 아직 테이블이 없음(승인 0건)
        return []
    return rows


def list_approvals(conn=None) -> list[dict]:
    owns = conn is None
    if owns:
        conn = _open()
    try:
        return [dict(r) for r in _select_approvals(conn)]
    finally:
        if owns:
            conn.close()


def list_events(conn=None, task_key: str | None = None, limit: int = 100) -> list[dict]:
    owns = conn is None
    if owns:
        conn = _open()
    try:
        limit = max(1, min(int(limit or 100), 500))
        try:
            if task_key:
                rows = conn.execute(
                    "SELECT * FROM task_approval_events WHERE task_key=? ORDER BY id DESC LIMIT ?",
                    (task_key, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM task_approval_events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        return [dict(r) for r in rows]
    finally:
        if owns:
            conn.close()


# ── 결재 기록(승인/철회) ─────────────────────────────────
def record_decision(*, task_key: str, action: str, approved_by: str,
                    audit_note: str, confirm: Any, conn=None) -> dict:
    """사람 승인/철회를 기록한다. 재클릭(같은 요청)은 아무것도 쓰지 않고 기존 기록을 돌려준다."""
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        raise ApprovalRejected("unknown_action", 400, f"action 은 {sorted(ACTIONS)} 중 하나여야 합니다.")
    status = ACTIONS[action]
    actor, note = validate_human_approval(approved_by, audit_note, confirm)

    task_key = (task_key or "").strip()
    if not task_key:
        raise ApprovalRejected("task_key_required", 400, "task_key 가 필요합니다.")

    tasks, queue_error = _queue_tasks(QUEUE_FILE)
    target = next((t for t in tasks if t["task_key"] == task_key), None)
    if target is None:
        raise ApprovalRejected(
            "unknown_task", 404,
            f"'{task_key}' 는 승인 대기 목록에 없습니다(큐 조회 오류: {queue_error or 'none'}).",
        )
    if not target["approvable"]:
        raise ApprovalRejected("task_not_approvable", 409, target["not_approvable_reason"] or "승인 대상이 아닙니다.")

    digest = request_hash(task_key, action, actor, note)
    owns = conn is None
    if owns:
        conn = _open()
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM task_approvals WHERE task_key=?", (task_key,)).fetchone()
        existing = dict(row) if row else None

        if existing and existing["status"] == status:
            if existing["request_hash"] == digest:
                return {
                    "result": "idempotent_replay", "writes": 0, "action": action,
                    "task_key": task_key, "approval": existing,
                    "notice": "동일한 결재 요청이 이미 기록돼 있어 아무것도 변경하지 않았습니다.",
                }
            raise ApprovalRejected(
                "basis_changed", 409,
                f"이미 '{status}' 상태이고 근거(승인자/사유)가 다릅니다 — 먼저 철회(revoke) 후 다시 승인하세요.",
            )

        now = _now()
        snapshot = json.dumps({
            "task_key": task_key, "action": action, "status": status, "actor": actor,
            "at": now, "note": note, "previous": existing,
            "task": {k: target[k] for k in ("title", "source_type", "source_ref", "status", "target_files")},
        }, ensure_ascii=False)

        try:
            if existing:
                conn.execute(
                    "UPDATE task_approvals SET status=?, approved_by=?, approved_at=?, audit_note=?, "
                    "request_hash=?, live_order_linked=0, updated_at=? WHERE task_key=?",
                    (status, actor, now, note, digest, now, task_key),
                )
            else:
                conn.execute(
                    "INSERT INTO task_approvals(task_key, task_title, source_type, source_ref, status, "
                    "approved_by, approved_at, audit_note, request_hash, live_order_linked, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                    (task_key, target["title"], target["source_type"], target["source_ref"],
                     status, actor, now, note, digest, now),
                )
            conn.execute(
                "INSERT INTO task_approval_events(idempotency_key, task_key, action, approved_by, approved_at, "
                "audit_note, request_hash, snapshot_json, live_order_linked, created_at) VALUES(?,?,?,?,?,?,?,?,0,?)",
                (hashlib.sha256(f"{digest}|{status}|{existing['status'] if existing else 'none'}".encode()).hexdigest(),
                 task_key, action, actor, now, note, digest, snapshot, now),
            )
            conn.commit()
        except Exception:
            # 동시 중복 클릭(두 요청이 같은 순간 INSERT)은 PK 충돌로 나타난다 —
            # 진 쪽은 통째로 롤백하고 이긴 쪽 기록을 그대로 돌려준다(멱등).
            conn.rollback()
            row = conn.execute("SELECT * FROM task_approvals WHERE task_key=?", (task_key,)).fetchone()
            current = dict(row) if row else None
            if current and current["status"] == status and current["request_hash"] == digest:
                return {
                    "result": "idempotent_replay", "writes": 0, "action": action,
                    "task_key": task_key, "approval": current,
                    "notice": "동시 중복 요청 — 이미 기록된 결재를 그대로 반환했습니다(추가 저장 없음).",
                }
            raise

        saved = conn.execute("SELECT * FROM task_approvals WHERE task_key=?", (task_key,)).fetchone()
        return {
            "result": status, "writes": 2, "action": action, "task_key": task_key,
            "approval": dict(saved) if saved else None,
            "notice": "기록했습니다. 이 승인은 작업 착수 결재 기록일 뿐이며 LIVE 주문과 연결되지 않습니다.",
        }
    finally:
        if owns and conn is not None:
            conn.close()


def policy() -> dict:
    return {
        "requires_human_click": True,
        "confirm_flag_required": True,
        "min_audit_note": MIN_AUDIT_NOTE,
        "automation_identities_blocked": sorted(_AGENT_IDENTITIES),
        "live_order_linked": False,
        "writable_tables": ["task_approvals", "task_approval_events"],
        "never_written": ["live_orders", "live_order_events", "live_strategy_approvals", "risk_gate_decisions"],
    }
