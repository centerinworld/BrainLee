"""작업 승인 대기 API (2026-09-24 신규).

prefix: /api/task-approvals  (main.py 에서 등록)

  GET  /api/task-approvals/pending          미승인 작업 + 참고 테이블 현황 + 기록된 승인
  GET  /api/task-approvals/ledger?task_key= 감사 원장(read-back)
  GET  /api/task-approvals/policy           승인 정책(자동승인 차단 규칙) 노출
  POST /api/task-approvals/approve          사람 승인 기록 (confirm=true 필수)
  POST /api/task-approvals/revoke           사람 철회 기록 (confirm=true 필수)

⚠️ 이 라우터는 LIVE 주문 경로(routes/kis_trading.py, live_trading_data.py)를 import 하지 않는다.
   승인 기록은 `task_approvals` / `task_approval_events` 두 테이블에만 쓰이며,
   `live_strategy_approvals`·`live_orders`·`risk_gate_decisions` 는 읽기 전용 참고다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool

import task_approval_registry as registry

router = APIRouter()


class DecisionRequest(BaseModel):
    task_key: str = ""
    approved_by: str = ""
    audit_note: str = ""
    # 반드시 JSON boolean true 여야 한다(문자열 "true"/1 도 거부) → 자동 호출/오클릭 차단.
    confirm: StrictBool | None = None


@router.get("/pending")
def get_pending():
    return registry.pending_tasks()


@router.get("/policy")
def get_policy():
    return registry.policy()


@router.get("/ledger")
def get_ledger(task_key: str | None = None, limit: int = 100):
    return {
        "read_at": registry._now(),
        "events": registry.list_events(task_key=task_key, limit=limit),
        "approvals": registry.list_approvals(),
        "live_order_linked": False,
    }


def _decide(action: str, body: DecisionRequest) -> dict:
    try:
        return registry.record_decision(
            task_key=body.task_key,
            action=action,
            approved_by=body.approved_by,
            audit_note=body.audit_note,
            confirm=body.confirm,
        )
    except registry.ApprovalRejected as exc:
        raise HTTPException(status_code=exc.status, detail={"reason": exc.reason, "message": exc.detail})


@router.post("/approve")
def post_approve(body: DecisionRequest):
    return _decide("approve", body)


@router.post("/revoke")
def post_revoke(body: DecisionRequest):
    return _decide("revoke", body)
