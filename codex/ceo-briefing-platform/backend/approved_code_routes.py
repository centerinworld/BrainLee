"""Authenticated approval UI/API. No code job starts from scheduler prose alone."""
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from session_deps import require_admin_session
from services.approved_code_jobs import CodeJobs

router = APIRouter()
code_jobs = CodeJobs()

# 2026-09-14 소유자 지시: "텔레그램으로 수정 사항을 보내서 나에게 승인 받으면 되잖아."
# 두 승인 지점(1차: 이 지시를 실행해도 되는지 / 2차: 실제로 생성·테스트된 diff를 실제
# 저장소에 적용해도 되는지)에서 알림만 보낸다 - 승인/거부 자체는 여전히 /agentic-code
# 웹 화면에서만 이뤄진다(텔레그램 답장을 파싱해 자동 승인하지 않음 - 이 봇의 getUpdates를
# 다른 프로세스가 이미 폴링 중일 수 있어 새 폴링 루프를 만들지 않기로 한 이전 결정과 동일한
# 이유). "KAI Info Room"(뉴스 전용)과는 다른 별도 봇(GOAL_INTAKE_BOT_TOKEN)을 쓴다.
_ENV_PATH = Path("/Volumes/Realtek_NVME/AI System/antigravity_workspace/.env")


def _read_env(key: str) -> str:
    try:
        for line in _ENV_PATH.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def notify_telegram(text: str) -> None:
    token = _read_env("GOAL_INTAKE_BOT_TOKEN")
    chat_id = _read_env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # 알림 실패가 승인 절차 자체를 막으면 안 된다 - 웹 화면은 항상 별도로 확인 가능


def _execute_and_notify(job_id: str) -> None:
    code_jobs.execute(job_id)
    try:
        job = code_jobs.get(job_id)
    except Exception:
        return
    if job.get("status") == "READY_FOR_REVIEW":
        notify_telegram(
            f"🔍 코드 변경 검토 대기 (job {job_id})\n"
            f"제공자: {job['spec']['provider']} | 파일: {', '.join(job.get('files_changed', []))}\n"
            f"테스트: {'PASS' if job.get('tests', {}).get('passed') else 'FAIL'}\n"
            "웹 대시보드(/agentic-code)에서 diff를 확인하고 실제 적용 여부를 승인해주세요."
        )
    elif job.get("status") in ("TEST_FAILED", "FAILED"):
        notify_telegram(f"❌ 코드 생성/테스트 실패 (job {job_id})\n사유: {str(job.get('error',''))[:300]}")

class JobRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    provider: Literal['claude','codex','gemini','qwen','deepseek']
    paths: list[str] = Field(min_length=1, max_length=20)
    test_module: str

class Approval(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)

def perform(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, RuntimeError, FileNotFoundError, BlockingIOError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get('/agentic-code')
def page():
    return FileResponse(Path(__file__).parent.parent/'frontend/agentic-code.html')

@router.get('/api/agi/code-jobs')
def listing(session=Depends(require_admin_session)):
    return code_jobs.list()

@router.post('/api/agi/code-jobs')
def create(req: JobRequest, background: BackgroundTasks, session=Depends(require_admin_session)):
    job = perform(code_jobs.create, **req.model_dump(), requested_by=session['username'])
    background.add_task(
        notify_telegram,
        f"🆕 코드 수정 요청 등록 (job {job['id']})\n제공자: {req.provider} | 파일: {', '.join(req.paths)}\n"
        f"지시: {req.instruction[:200]}\n웹 대시보드(/agentic-code)에서 1차 승인해주세요.",
    )
    return job

@router.get('/api/agi/code-jobs/{job_id}')
def detail(job_id: str, session=Depends(require_admin_session)):
    return perform(code_jobs.get, job_id)

@router.post('/api/agi/code-jobs/{job_id}/approve')
def approve(job_id: str, req: Approval, background: BackgroundTasks, session=Depends(require_admin_session)):
    result = perform(code_jobs.approve, job_id, req.fingerprint, approved_by=session['username'])
    background.add_task(_execute_and_notify, job_id)
    return result

@router.post('/api/agi/code-jobs/{job_id}/reject')
def reject(job_id: str, req: Approval, session=Depends(require_admin_session)):
    return perform(code_jobs.reject, job_id, req.fingerprint, rejected_by=session['username'])

@router.post('/api/agi/code-jobs/{job_id}/apply')
def apply(job_id: str, req: Approval, background: BackgroundTasks, session=Depends(require_admin_session)):
    result = perform(code_jobs.apply_to_workspace, job_id, req.fingerprint, approved_by=session['username'])
    if result.get('status') == 'APPLIED_TO_WORKSPACE':
        background.add_task(notify_telegram, f"✅ 코드 변경 실제 적용됨 (job {job_id}) - 아직 git commit은 안 됨, 수동 확인 필요")
    return result

@router.post('/api/agi/code-jobs/{job_id}/rollback')
def rollback(job_id: str, req: Approval, background: BackgroundTasks, session=Depends(require_admin_session)):
    result = perform(code_jobs.rollback, job_id, req.fingerprint, approved_by=session['username'])
    background.add_task(notify_telegram, f"↩️ 코드 변경 롤백됨 (job {job_id})")
    return result
