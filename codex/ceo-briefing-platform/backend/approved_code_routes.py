"""Authenticated approval UI/API. No code job starts from scheduler prose alone."""
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from session_deps import require_admin_session
from services.approved_code_jobs import CodeJobs
from services.telegram_approval_poller import TelegramApprovalPoller, send_final_approval_prompt
from services.code_job_worker import CodeJobWorker

_LOG = logging.getLogger(__name__)
router = APIRouter()
code_jobs = CodeJobs()
approval_poller = TelegramApprovalPoller(code_jobs=code_jobs)
# 2026-09-17 handoff P1-2: approve() 직후의 BackgroundTasks 실행은 서버 재시작에
# 유실될 수 있다 - 이 워커가 주기적으로 놓친 작업을 다시 집어 durable하게 만든다.
code_job_worker = CodeJobWorker(code_jobs=code_jobs)

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
        # 알림 실패가 승인 절차 자체를 막으면 안 되므로 여전히 예외는 삼킨다 - 다만
        # 2026-09-18 실사용 중, 원인 추적 불가능한 무음 전송 실패를 실제로 겪은 뒤
        # 최소한 로그에는 남기도록 고쳤다(telegram_approval_poller.py와 동일 조치).
        _LOG.exception("Telegram 알림 전송 실패: %s", text[:120])


def _execute_and_notify(job_id: str) -> None:
    code_jobs.execute(job_id)
    try:
        job = code_jobs.get(job_id)
    except Exception:
        return
    if job.get("status") == "READY_FOR_REVIEW":
        # 2026-09-18 소유자 지적(1차): "어떤 내용을 수정/개선할건지 보내야 승인을 할 거
        # 같아" - 이전엔 "테스트: PASS"와 버튼만 보내서 실제로 뭘 바꾸는지 텔레그램만
        # 으로는 전혀 알 수 없었다. 지시문과 diff 미리보기를 추가했었다.
        # 2026-09-18 소유자 지적(2차, 실제 첫 실사용 후): "너가 보낸 메세지를 내가
        # 이해 못하겠는데? 뭘 수정하는지에 대한 내용을 적어 줘야지" - 원본 지시문
        # (개발자 용어)과 raw unified diff(+/-, @@ hunk header)만으로는 여전히 못
        # 읽겠다고 하셨다. execute()가 모델에게 diff와 별개로 요청해 받은 평이한
        # 한국어 요약(job['summary'])을 맨 앞에 크게 두고, 원본 지시/diff는 참고용
        # 상세로 뒤에 둔다.
        diff = job.get("diff", "")
        summary = job.get("summary", "").strip() or "(모델이 요약을 만들지 않음 - 아래 상세 참고)"
        header = (
            f"🔍 코드 변경 검토 대기 (job {job_id})\n"
            f"파일: {', '.join(job.get('files_changed', []))}\n\n"
            f"무엇이 바뀌나요:\n{summary}\n\n"
            f"원래 요청: {job['spec']['instruction'][:200]}\n"
            f"테스트: {'PASS' if job.get('tests', {}).get('passed') else 'FAIL'}\n\n"
            f"--- 상세 변경 내용(개발자용 diff) ---\n"
        )
        footer = "\n\n위 내용을 확인하고, 아래 버튼으로 바로 최종 승인(실제 적용)하거나 웹 대시보드(/agentic-code)에서 더 보고 결정하세요."
        # Telegram sendMessage text는 4096 "바이트"가 아니라 UTF-16 코드 유닛 기준
        # 4096자 상한이지만, 한글이 섞이면 실제 표시/제한 동작이 라이브러리마다
        # 갈릴 수 있어 보수적으로 UTF-8 바이트 기준 예산을 잡는다(한글 1자=3바이트라
        # 문자 수로만 자르면 초과할 수 있다 - diff는 거의 코드/영문이라 바이트≈문자
        # 수지만 안전하게 바이트 단위로 직접 자른다).
        budget = 3800 - len(header.encode("utf-8")) - len(footer.encode("utf-8"))
        diff_bytes = diff.encode("utf-8")
        truncated = len(diff_bytes) > max(budget, 0)
        diff_preview = diff_bytes[:max(budget, 0)].decode("utf-8", errors="ignore")
        diff_note = "\n…(전체 diff는 웹 대시보드에서 확인)" if truncated else ""
        send_final_approval_prompt(
            job_id, job.get("diff_hash", ""),
            f"{header}{diff_preview}{diff_note}{footer}"
        )
    elif job.get("status") in ("TEST_FAILED", "FAILED"):
        notify_telegram(f"❌ 코드 생성/테스트 실패 (job {job_id})\n사유: {str(job.get('error',''))[:300]}")

class JobRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    provider: Literal['claude','codex','gemini','qwen','deepseek']
    paths: list[str] = Field(min_length=1, max_length=20)
    test_module: str
    # 2026-09-17 handoff P1-1: 등록된 저장소 중에서만 고를 수 있다(services.
    # agentic_apply_worker.REPO_REGISTRY) - 임의 경로를 받지 않는다. 기본값은 기존
    # 동작과 동일한 AI System 저장소.
    repo_id: Literal['ai-system','stock-dashboard'] = 'ai-system'

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
    # 2026-09-17 발견(handoff): rollback()도 apply_to_workspace()처럼 실패해도 예외 없이
    # status만 ROLLBACK_FAILED로 바꿔 반환한다 - 상태 확인 없이 무조건 "롤백됨"을 보내면
    # 실패를 성공으로 알리게 된다.
    if result.get('status') == 'ROLLED_BACK':
        background.add_task(notify_telegram, f"↩️ 코드 변경 롤백됨 (job {job_id})")
    else:
        background.add_task(notify_telegram, f"❌ 롤백 실패 (job {job_id}, status={result.get('status')}): {str(result.get('error',''))[:300]}")
    return result
