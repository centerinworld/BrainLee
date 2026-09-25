#!/usr/bin/env python
"""라이브 PostgreSQL 경로 검증 — 작업 승인 원장 (2026-09-24 신규).

무엇을 증명하는가
  1. `task_approval_registry.DDL` 이 **라이브 PG에서 실제로 실행된다**(db_compat 번역 경유).
  2. `record_decision()` 의 실제 코드 경로가 PG에서 승인 + 감사 이벤트를 쓰고 read-back 된다.
  3. 커넥션의 `commit()` 을 무력화한 뒤 마지막에 ROLLBACK 하므로 라이브 DB 에는
     테이블도 행도 남지 않는다(실행 전후 카운트 대조로 입증).

실행 (runtime venv, cwd=runtime):
    ./venv/bin/python scripts/verify_task_approval_pg_path.py

종료코드: 0 = 계약 통과, 2 = 실패(사유 출력)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402

from db_compat import connect_primary_db  # noqa: E402
import task_approval_registry as registry  # noqa: E402

TABLES = ("task_approvals", "task_approval_events")


def _counts(conn) -> dict:
    """테이블 미생성 상태에서도 예외로 트랜잭션을 망가뜨리지 않도록 개별 롤백한다."""
    out = {}
    for table in TABLES:
        try:
            out[table] = int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
        except Exception as exc:  # noqa: BLE001 - 테이블 없음 = 0건
            out[table] = f"absent({type(exc).__name__})"
            conn.rollback()
    return out


def main() -> int:
    failures: list[str] = []
    conn = connect_primary_db(timeout=30, row_factory=sqlite3.Row)
    suppressed = {"n": 0}
    real_commit = conn.commit

    def _no_commit() -> None:  # 라이브에 남기지 않기 위해 commit 을 삼킨다
        suppressed["n"] += 1

    conn.commit = _no_commit  # type: ignore[method-assign]
    try:
        before = _counts(conn)
        print(f"[1] 라이브 PG(compat 경유) 접속 — 실행 전 행수: {before}")

        registry.ensure_schema(conn, commit=False)
        print("[2] ensure_schema(commit=False) — 번역된 DDL 을 트랜잭션 안에서 실행: OK")

        tasks, queue_err = registry._queue_tasks(registry.QUEUE_FILE)
        pending = [t for t in tasks if t["approvable"]]
        print(f"[3] 라이브 큐 미승인 작업 {len(pending)}건 "
              f"(queue_error={queue_err}) → {[t['task_key'] for t in pending]}")
        if not pending:
            failures.append("라이브 큐에 승인 대기 작업이 없음")
        task = pending[0] if pending else None

        note = "PG 경로 검증(롤백 예정) — 승인 기록·재클릭 멱등·read-back 계약 확인용"
        if task:
            result = registry.record_decision(
                task_key=task["task_key"], action="approve", approved_by="pgpath-probe",
                audit_note=note, confirm=True, conn=conn,
            )
            replay = registry.record_decision(
                task_key=task["task_key"], action="approve", approved_by="pgpath-probe",
                audit_note=note, confirm=True, conn=conn,
            )
            events = conn.execute(
                "SELECT task_key, action, approved_by, audit_note, live_order_linked "
                "FROM task_approval_events ORDER BY id DESC LIMIT 5").fetchall()
            stored = conn.execute(
                "SELECT task_key, status, approved_by, audit_note, live_order_linked, "
                "task_title, source_ref FROM task_approvals WHERE task_key=?",
                (task["task_key"],)).fetchone()
            print(f"[4] record_decision: result={result['result']} writes={result['writes']}")
            print(f"    재클릭:          result={replay['result']} writes={replay['writes']}")
            print(f"[5] read-back 승인행: {dict(stored) if stored else None}")
            print(f"    read-back 원장 {len(events)}행: "
                  f"{[dict(e) for e in events]}")

            if result["result"] != "approved" or result["writes"] != 2:
                failures.append(f"최초 승인 결과 비정상: {result['result']}/{result['writes']}")
            if replay["result"] != "idempotent_replay" or replay["writes"] != 0:
                failures.append(f"재클릭이 멱등이 아님: {replay['result']}/{replay['writes']}")
            if len(events) != 1:
                failures.append(f"원장 행수가 1이 아님: {len(events)}")
            if stored is None:
                failures.append("승인 행 read-back 실패")
            else:
                if stored["live_order_linked"] != 0:
                    failures.append("live_order_linked 가 0이 아님")
                if stored["audit_note"] != note:
                    failures.append("audit_note read-back 불일치")
                if stored["approved_by"] != "pgpath-probe":
                    failures.append("approved_by read-back 불일치")
                if stored["task_title"] != task["title"]:
                    failures.append("task_title read-back 불일치")
        print(f"    commit 억제 횟수(무시됨): {suppressed['n']}")

        conn.rollback()
        print("[6] ROLLBACK 실행")
        after = _counts(conn)
        print(f"    실행 후 행수: {after}")
        if after != before:
            failures.append(f"롤백 후 상태가 달라짐: {before} → {after}")
        else:
            print("    ✅ 라이브 DB 무변경(테이블·행 모두 남지 않음)")
    finally:
        conn.commit = real_commit  # type: ignore[method-assign]
        try:
            conn.rollback()
        finally:
            conn.close()

    if failures:
        print("\n❌ 실패:")
        for item in failures:
            print(f"   - {item}")
        return 2
    print("\n✅ PG 런타임 경로 계약 통과 (라이브 무기록)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
