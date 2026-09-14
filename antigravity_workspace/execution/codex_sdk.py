"""공식 Codex Python SDK용 영속 로컬 실행 adapter.

SDK의 thread/turn ID와 결과 artifact를 StateLedger의 같은 run_id에 연결한다.
여기서 SUCCEEDED는 SDK 실행과 artifact 검증이 끝났다는 뜻이며, 상위 목표가
완료됐다는 뜻은 아니다.
"""

from datetime import datetime
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, Optional

from memory.state_ledger import StateLedger
from .contracts import ExecutionRequest

try:
    import openai_codex
    from openai_codex import Codex, Sandbox
except ImportError:  # pragma: no cover - capability()와 실패 결과에서 처리
    openai_codex = None
    Codex = None
    Sandbox = None


TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "WAITING_AUTH", "CANCELLED"}


class CodexSDKAdapter:
    DOMAIN = "codex_sdk_run"

    def __init__(
        self,
        ledger: Optional[StateLedger] = None,
        artifact_dir: Optional[str] = None,
        codex_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.ledger = ledger or StateLedger()
        self.artifact_dir = Path(
            artifact_dir
            or os.getenv(
                "CODEX_SDK_ARTIFACT_DIR",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "artifacts", "codex_sdk"),
            )
        )
        self.codex_factory = codex_factory or Codex

    def capabilities(self) -> Dict[str, Any]:
        auth_mode = "unknown"
        auth_ready = False
        cli_path = os.getenv("CODEX_CLI_PATH", "/Applications/ChatGPT.app/Contents/Resources/codex")
        if os.path.exists(cli_path):
            try:
                result = subprocess.run(
                    [cli_path, "login", "status"], capture_output=True, text=True, timeout=10
                )
                combined = f"{result.stdout}\n{result.stderr}"
                auth_ready = result.returncode == 0 and "Logged in using ChatGPT" in combined
                auth_mode = "chatgpt_subscription" if auth_ready else "unavailable"
            except Exception:
                auth_mode = "unknown"
        return {
            "provider": "codex_sdk",
            "available": openai_codex is not None and self.codex_factory is not None,
            "sdk_version": getattr(openai_codex, "__version__", None),
            "auth_mode": auth_mode,
            "auth_ready": auth_ready,
            "supports_resume": True,
            "sandboxes": ["read-only", "workspace-write"],
        }

    @staticmethod
    def _usage_dict(usage: Any) -> Optional[Dict[str, int]]:
        if usage is None:
            return None
        total = getattr(usage, "total", usage)
        prompt = int(getattr(total, "input_tokens", 0) or 0)
        completion = int(getattr(total, "output_tokens", 0) or 0)
        total_tokens = int(getattr(total, "total_tokens", prompt + completion) or 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total_tokens,
        }

    def _record(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload)
        payload["updated_at"] = datetime.now().isoformat()
        return self.ledger.upsert(self.DOMAIN, run_id, payload)

    def observe(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.get(self.DOMAIN, run_id)

    def submit(self, request: ExecutionRequest) -> Dict[str, Any]:
        existing = self.observe(request.run_id)
        if existing and existing.get("status") in TERMINAL_STATUSES:
            return existing

        base = {
            "task_id": request.task_id,
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "role": request.role,
            "transport": "codex_sdk",
            "workspace": request.workspace,
            "spec_hash": request.spec_hash,
            "input_hash": request.input_hash,
            "session_id": request.session_id,
            "requested_model": request.model,
            # 2026-09-14 진단(D01): openai_codex SDK의 TurnResult/Turn에는 실제 사용된
            # 모델을 알려주는 필드가 없다(SDK 0.154.0 스키마 확인) - requested_model이
            # 실제로 적용됐는지는 이 원장만으로 확정할 수 없다. None(미확인처럼 보임)
            # 대신 이유를 남긴 명시적 상태로 "모른다"는 사실 자체를 숨기지 않는다.
            "resolved_model": "UNKNOWN_SDK_DOES_NOT_REPORT_MODEL",
            "auth_mode": "chatgpt_subscription",
            "sandbox": request.sandbox,
            "status": "QUEUED",
            "started_at": datetime.now().isoformat(),
            "heartbeat_at": datetime.now().isoformat(),
        }
        self._record(request.run_id, base)

        if self.codex_factory is None or Sandbox is None:
            return self._record(request.run_id, {
                "status": "FAILED", "error_type": "SDK_NOT_INSTALLED",
                "error": "openai-codex 패키지를 불러올 수 없습니다",
            })

        self._record(request.run_id, {"status": "LEASED"})
        sandbox = Sandbox.read_only if request.sandbox == "read-only" else Sandbox.workspace_write
        try:
            self._record(request.run_id, {"status": "RUNNING"})
            with self.codex_factory() as codex:
                if request.session_id:
                    thread = codex.thread_resume(
                        request.session_id, cwd=request.workspace, model=request.model, sandbox=sandbox
                    )
                else:
                    thread = codex.thread_start(
                        cwd=request.workspace, model=request.model, sandbox=sandbox
                    )
                # 2026-09-14 진단(D01, AGENTIC_RUNTIME_DIAGNOSIS_SOL_ONLY): thread_start()에만
                # model을 넘기고 있었다 - Thread.run()도 per-turn model 인자를 별도로 받으므로
                # (openai_codex.api.Thread.run signature 확인), 세션 기본값에만 기대지 않고
                # 매 턴에도 명시적으로 재전달해 사용자 전역 config.toml 기본값(gpt-6-astra 등)이
                # 끼어들 여지를 줄인다.
                result = thread.run(request.prompt, model=request.model, sandbox=sandbox)

            content = (getattr(result, "final_response", None) or "").strip()
            if not content:
                return self._record(request.run_id, {
                    "status": "FAILED", "session_id": thread.id,
                    "turn_id": getattr(result, "id", None),
                    "error_type": "EMPTY_RESULT", "error": "SDK 최종 응답이 비어 있습니다",
                })

            self._record(request.run_id, {
                "status": "RESULT_RECEIVED", "session_id": thread.id,
                "turn_id": getattr(result, "id", None),
            })
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = self.artifact_dir / f"{request.run_id}.txt"
            artifact_path.write_text(content, encoding="utf-8")
            artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            usage = self._usage_dict(getattr(result, "usage", None))
            return self._record(request.run_id, {
                "status": "SUCCEEDED",
                "session_id": thread.id,
                "turn_id": getattr(result, "id", None),
                "artifact_path": str(artifact_path),
                "artifact_hash": artifact_hash,
                "usage": usage,
                "completed_at": datetime.now().isoformat(),
            })
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            waiting_auth = any(token in lowered for token in ("reauthentication", "not logged", "login required", "unauthorized"))
            return self._record(request.run_id, {
                "status": "WAITING_AUTH" if waiting_auth else "FAILED",
                "error_type": type(exc).__name__,
                "error": message[:1000],
                "completed_at": datetime.now().isoformat(),
            })

    def resume(self, previous_run_id: str, prompt: str, *, run_id: Optional[str] = None) -> Dict[str, Any]:
        previous = self.observe(previous_run_id)
        if not previous or not previous.get("session_id"):
            raise ValueError(f"재개할 session_id가 없습니다: {previous_run_id}")
        request = ExecutionRequest(
            prompt=prompt,
            workspace=previous["workspace"],
            role=previous.get("role", "worker"),
            run_id=run_id or f"{previous_run_id}_resume",
            task_id=previous["task_id"],
            session_id=previous["session_id"],
            model=previous.get("requested_model"),
            sandbox=previous.get("sandbox", "read-only"),
        )
        return self.submit(request)

    def reconcile(self, run_id: str) -> Dict[str, Any]:
        record = self.observe(run_id)
        if not record:
            raise ValueError(f"알 수 없는 run_id: {run_id}")
        if record.get("status") == "SUCCEEDED":
            path = record.get("artifact_path")
            if not path or not os.path.exists(path):
                return self._record(run_id, {"status": "NEEDS_RECONCILIATION", "error": "artifact 누락"})
            content = Path(path).read_bytes()
            actual = hashlib.sha256(content).hexdigest()
            if actual != record.get("artifact_hash"):
                return self._record(run_id, {"status": "NEEDS_RECONCILIATION", "error": "artifact hash 불일치"})
        elif record.get("status") in {"LEASED", "RUNNING", "RESULT_RECEIVED"}:
            return self._record(run_id, {
                "status": "NEEDS_RECONCILIATION",
                "error": "프로세스 중단 뒤 provider 상태 확인 필요",
            })
        return self.observe(run_id)
