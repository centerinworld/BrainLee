# -*- coding: utf-8 -*-
"""
승인된 code-job을 durable하게(=프로세스 재시작에도 유실 없이) 실행하는 워커.

2026-09-17 handoff(AGENTIC_EXECUTION_REPAIR_HANDOFF_2026-09-17.md) P1-2 지적:
"작업 전달 - approve API가 FastAPI BackgroundTasks로 실행을 예약한다 - 승인 저장 후
서버가 종료되면 실행이 유실될 수 있다." 실제로 그렇다: BackgroundTasks는 같은
프로세스가 재시작 없이 살아 있을 때만 실행되고, 그 사이 서버가 재시작되면
"승인은 됐지만 아무도 실행을 시도하지 않은" 작업이 조용히 방치된다(상태 자체는
SQLite에 APPROVED로 남아있어서 데이터가 사라지는 건 아니지만, 아무도 다시 보지
않으면 영원히 그 상태에 머문다).

이 워커는 `approved_code_routes.py`의 `background.add_task(_execute_and_notify,
job_id)`(빠른 경로 - 정상적인 경우 승인 직후 바로 실행됨)를 대체하지 않고, 그 위에
얹는 안전망이다: 주기적으로 (a) 아무도 안 집어간(또는 리스가 만료된) APPROVED
작업을 하나 집어 실행하고, (b) 워커가 죽어 리스가 만료된 RUNNING 작업을
NEEDS_RECONCILIATION으로 표시한다. strict_agi_orchestrator.py의 기존 모니터
스레드 패턴(threading.Thread + poll_interval)을 그대로 따른다.
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional


class CodeJobWorker:
    def __init__(self, code_jobs, poll_interval: float = 10.0, lease_seconds: int = 300):
        self.code_jobs = code_jobs
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="code-job-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._tick()
            except Exception:
                pass  # 워커 자체가 죽으면 안전망 전체가 사라진다 - 한 틱 실패는 무시하고 계속

    def _tick(self) -> None:
        self.code_jobs.reconcile_stale_running(stale_after_seconds=self.lease_seconds)
        job = self.code_jobs.claim_next_approved(self.worker_id, lease_seconds=self.lease_seconds)
        if job:
            self.code_jobs.execute(job["id"])
