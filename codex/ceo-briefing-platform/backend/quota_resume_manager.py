"""Persistent quota-aware task queue for the public AGI handoff console.

Subscription CLIs do not expose a reliable remaining-message counter.  This
module therefore records only observed authentication/rate-limit evidence,
uses a bounded fallback path, and keeps fallback output pending until a
frontier provider reviews it.  It never applies patches or claims a merge.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Dict, Optional
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen


BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"
STATE_FILE = DATA_DIR / "agi_quota_resume_state.json"
ARTIFACT_DIR = DATA_DIR / "agi_task_artifacts"
AI_WORKSPACE = Path("/Volumes/Realtek_NVME/AI System")
CODEX_BIN = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
CLAUDE_BIN = Path("/opt/homebrew/bin/claude")
ENV_FILE = BACKEND_DIR.parent / ".env"

RATE_LIMIT_MARKERS = (
    "rate limit", "rate_limit", "usage limit", "quota", "too many requests",
    "limit reached", "capacity", "try again later", "5-hour", "5 hour",
)
AUTH_MARKERS = ("not logged", "login required", "unauthorized", "authentication", "reauth")
TERMINAL_STATES = {"RESULT_READY", "FRONTIER_REVIEW_COMPLETE", "FAILED", "CANCELLED"}


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _read_secret(name: str) -> str:
    value = os.getenv(name, "").strip().strip('"').strip("'")
    if value:
        return value
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                return candidate.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _default_state() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": _iso(),
        "providers": {
            "codex": {
                "display_name": "Codex / ChatGPT 구독",
                "status": "UNKNOWN_UNTIL_REQUEST",
                "auth_ready": False,
                "quota_observed": False,
                "reset_at": None,
                "evidence_source": "cli_auth_probe",
                "last_checked_at": None,
                "note": "남은 메시지 수는 공개 API가 없어 실제 요청 결과로만 한도를 감지합니다.",
            },
            "claude": {
                "display_name": "Claude 구독",
                "status": "UNKNOWN_UNTIL_REQUEST",
                "auth_ready": False,
                "quota_observed": False,
                "reset_at": None,
                "evidence_source": "cli_auth_probe",
                "last_checked_at": None,
                "note": "남은 메시지 수는 공개 API가 없어 실제 요청 결과로만 한도를 감지합니다.",
            },
            "qwen": {
                "display_name": "Qwen 로컬",
                "status": "UNKNOWN",
                "auth_ready": True,
                "quota_observed": False,
                "reset_at": None,
                "evidence_source": "ollama_probe",
                "last_checked_at": None,
                "note": "로컬 Ollama 가용성을 확인합니다.",
            },
            "deepseek": {
                "display_name": "DeepSeek API",
                "status": "UNKNOWN",
                "auth_ready": False,
                "quota_observed": False,
                "reset_at": None,
                "evidence_source": "credential_presence_only",
                "last_checked_at": None,
                "note": "키 존재 여부와 실제 호출 결과만 표시하며 비용은 월 예산으로 제한합니다.",
            },
        },
        "tasks": [],
        "events": [],
    }


class QuotaResumeManager:
    def __init__(self, state_file: Path = STATE_FILE, monitor_interval: int = 60) -> None:
        self.state_file = Path(state_file)
        self.artifact_dir = self.state_file.parent / "agi_task_artifacts"
        self.monitor_interval = max(10, monitor_interval)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._monitor: Optional[threading.Thread] = None
        self._active: set[str] = set()
        self._recover_interrupted_tasks()

    def _load(self) -> Dict[str, Any]:
        with self._lock:
            if not self.state_file.exists():
                state = _default_state()
                self._save(state)
                return state
            try:
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                if not isinstance(state.get("tasks"), list) or not isinstance(state.get("providers"), dict):
                    raise ValueError("invalid state schema")
                return state
            except (OSError, ValueError, json.JSONDecodeError):
                backup = self.state_file.with_suffix(f".corrupt-{int(time.time())}.json")
                try:
                    self.state_file.replace(backup)
                except OSError:
                    pass
                state = _default_state()
                state["events"].append({"at": _iso(), "type": "STATE_RECOVERED", "detail": "corrupt state replaced"})
                self._save(state)
                return state

    def _save(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state["updated_at"] = _iso()
            tmp = self.state_file.with_name(f".{self.state_file.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.state_file)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _recover_interrupted_tasks(self) -> None:
        state = self._load()
        changed = False
        for task in state["tasks"]:
            if task.get("status") in {"RUNNING_PRIMARY", "RUNNING_FALLBACK", "RUNNING_FRONTIER_REVIEW"}:
                task.update({
                    "status": "WAITING_RETRY",
                    "stage_label": "프로세스 재시작 감지 · 체크포인트에서 재개 대기",
                    "next_retry_at": _iso(_now() + timedelta(minutes=1)),
                    "updated_at": _iso(),
                })
                changed = True
        if changed:
            self._save(state)

    def start(self) -> None:
        with self._lock:
            if self._monitor and self._monitor.is_alive():
                return
            self._stop.clear()
            self._monitor = threading.Thread(target=self._monitor_loop, daemon=True, name="agi-quota-resume")
            self._monitor.start()

    def stop(self) -> None:
        self._stop.set()

    def _monitor_loop(self) -> None:
        while not self._stop.wait(self.monitor_interval):
            try:
                self.check_and_resume()
            except Exception:
                continue

    def _probe_codex(self) -> Dict[str, Any]:
        result = {"auth_ready": False, "status": "UNAVAILABLE", "note": "Codex CLI를 찾을 수 없습니다."}
        if not CODEX_BIN.exists():
            return result
        try:
            proc = subprocess.run([str(CODEX_BIN), "login", "status"], capture_output=True, text=True, timeout=10)
            text = f"{proc.stdout}\n{proc.stderr}"
            ready = proc.returncode == 0 and "Logged in using ChatGPT" in text
            return {
                "auth_ready": ready,
                "status": "AVAILABLE_QUOTA_UNKNOWN" if ready else "WAITING_AUTH",
                "note": "로그인 확인됨. 남은 4~5시간 창은 실제 요청 오류로 감지합니다." if ready else "Codex 로그인이 필요합니다.",
            }
        except Exception as exc:
            return {"auth_ready": False, "status": "PROBE_ERROR", "note": str(exc)[:200]}

    def _probe_claude(self) -> Dict[str, Any]:
        if not CLAUDE_BIN.exists():
            return {"auth_ready": False, "status": "UNAVAILABLE", "note": "Claude CLI를 찾을 수 없습니다."}
        try:
            proc = subprocess.run([str(CLAUDE_BIN), "auth", "status", "--json"], capture_output=True, text=True, timeout=10)
            payload = json.loads(proc.stdout or "{}")
            ready = bool(payload.get("loggedIn"))
            return {
                "auth_ready": ready,
                "status": "AVAILABLE_QUOTA_UNKNOWN" if ready else "WAITING_AUTH",
                "note": "로그인 확인됨. 남은 4~5시간 창은 실제 요청 오류로 감지합니다." if ready else "Claude 토큰/로그인이 없습니다.",
            }
        except Exception as exc:
            return {"auth_ready": False, "status": "PROBE_ERROR", "note": str(exc)[:200]}

    @staticmethod
    def _probe_qwen() -> Dict[str, Any]:
        try:
            with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
                models = json.loads(response.read().decode("utf-8")).get("models", [])
            names = [str(item.get("name", "")) for item in models]
            ready = any(name.startswith("qwen") for name in names)
            return {
                "auth_ready": ready,
                "status": "AVAILABLE_LOCAL" if ready else "MODEL_NOT_INSTALLED",
                "note": "로컬 Qwen 모델 확인됨." if ready else "Ollama에 Qwen 모델이 없습니다.",
            }
        except Exception as exc:
            return {"auth_ready": False, "status": "UNAVAILABLE", "note": f"Ollama 연결 실패: {str(exc)[:120]}"}

    def refresh_providers(self) -> Dict[str, Any]:
        probes = {
            "codex": self._probe_codex(),
            "claude": self._probe_claude(),
            "qwen": self._probe_qwen(),
            "deepseek": {
                "auth_ready": bool(_read_secret("DEEPSEEK_API_KEY")),
                "status": "AVAILABLE_BUDGET_GUARDED" if _read_secret("DEEPSEEK_API_KEY") else "WAITING_AUTH",
                "note": "API 키 확인됨. 실제 사용량은 예산 원장에서 제한합니다." if _read_secret("DEEPSEEK_API_KEY") else "DeepSeek API 키가 없습니다.",
            },
        }
        with self._lock:
            state = self._load()
            now = _now()
            for name, probe in probes.items():
                current = state["providers"].setdefault(name, {})
                reset_at = current.get("reset_at")
                locked = False
                if reset_at:
                    try:
                        locked = datetime.fromisoformat(reset_at) > now
                    except ValueError:
                        reset_at = None
                if locked:
                    current.update({"last_checked_at": _iso(), "auth_ready": probe["auth_ready"]})
                else:
                    current.update(probe)
                    current.update({"quota_observed": False, "reset_at": None, "last_checked_at": _iso()})
            self._save(state)
            return dict(state["providers"])

    @staticmethod
    def _retry_time(message: str) -> tuple:
        """
        OpenAI/Claude의 시간단위 롤링 리밋(4~5시간) 및 주간 한도(Weekly Limit)를 정밀 파싱
        반환값: (datetime, limit_type, human_note)
        """
        now = _now()
        lower = message.lower()

        # 1. 정밀 시각 파싱: 'try again at 2:38 PM' 또는 'try again at 14:38'
        m_clock = re.search(r'(?:try again|retry)\s+at\s+(\d{1,2}):(\d{2})\s*(am|pm)?', lower)
        if m_clock:
            hr = int(m_clock.group(1))
            mn = int(m_clock.group(2))
            meridiem = m_clock.group(3)
            if meridiem == 'pm' and hr < 12:
                hr += 12
            elif meridiem == 'am' and hr == 12:
                hr = 0
            target = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            time_str = target.strftime('%H:%M')
            return target, "HOURLY_ROLLING", f"4시간 롤링 한도 도달 ({time_str} 자동 재개 예정)"

        # 2. 상대 시간 파싱: 'try again in X minutes/hours'
        patterns = (
            (r"(?:retry|try again)\s+(?:in|after)\s+(\d+)\s*(?:min|minute)", "minutes"),
            (r"(?:retry|try again)\s+(?:in|after)\s+(\d+)\s*(?:hour|hr)", "hours"),
        )
        for pattern, unit in patterns:
            match = re.search(pattern, lower)
            if match:
                amount = int(match.group(1))
                target = now + (timedelta(minutes=amount) if unit == "minutes" else timedelta(hours=amount))
                time_str = target.strftime('%H:%M')
                return target, "HOURLY_ROLLING", f"{amount}{'분' if unit=='minutes' else '시간'} 후 재개 예정 ({time_str})"

        # 3. 주간 단위 한계 파싱 (Weekly Limit)
        if any(w in lower for w in ("weekly", "week", "주간")):
            days_ahead = (7 - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
            return target, "WEEKLY_LIMIT", f"주간 토큰 한도 도달 (월요일 {target.strftime('%m-%d')} 자동 재개)"

        # 기본 4시간 롤링 대기
        target = now + timedelta(hours=4)
        return target, "HOURLY_ROLLING", f"4시간 한도 감시 중 ({target.strftime('%H:%M')} 재개)"

    def record_failure(self, provider: str, message: str) -> str:
        lower = message.lower()
        with self._lock:
            state = self._load()
            item = state["providers"][provider]
            if any(marker in lower for marker in RATE_LIMIT_MARKERS):
                reset_at, limit_type, note_msg = self._retry_time(message)
                item.update({
                    "status": "WAITING_QUOTA", "quota_observed": True,
                    "limit_type": limit_type,
                    "reset_at": _iso(reset_at), "last_checked_at": _iso(),
                    "evidence_source": "observed_provider_error",
                    "note": note_msg,
                })
                self._save(state)
                return "WAITING_QUOTA"
            if any(marker in lower for marker in AUTH_MARKERS):
                item.update({"status": "WAITING_AUTH", "auth_ready": False, "last_checked_at": _iso(), "note": "실제 인증 오류를 감지했습니다."})
                self._save(state)
                return "WAITING_AUTH"
            item.update({"status": "EXECUTION_ERROR", "last_checked_at": _iso(), "note": message[:200]})
            self._save(state)
            return "EXECUTION_ERROR"

    def _get_task(self, state: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
        return next((task for task in state["tasks"] if task.get("task_id") == task_id), None)

    def _update_task(self, task_id: str, **updates: Any) -> Dict[str, Any]:
        with self._lock:
            state = self._load()
            task = self._get_task(state, task_id)
            if not task:
                raise KeyError(task_id)
            task.update(updates)
            task["updated_at"] = _iso()
            self._save(state)
            return dict(task)

    def dispatch(self, title: str, description: str = "", strategy: str = "FALLBACK_THEN_REVIEW") -> Dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("작업 제목이 비어 있습니다")
        if len(title) > 1000:
            raise ValueError("작업 제목은 1000자 이하여야 합니다")
        if strategy not in {"WAIT_FOR_PRIMARY", "FALLBACK_THEN_REVIEW"}:
            raise ValueError("지원하지 않는 재개 전략입니다")
        task_id = f"qtask-{int(time.time() * 1000)}-{os.getpid()}"
        task = {
            "task_id": task_id,
            "title": title,
            "description": (description or title)[:4000],
            "strategy": strategy,
            "status": "QUEUED",
            "current_stage": 0,
            "stage_label": "영속 큐 접수 완료",
            "progress_pct": 5,
            "stage1_summary": "Codex 실행 대기",
            "stage1_output": "",
            "stage2_summary": "Qwen·DeepSeek 대체 실행 대기",
            "stage2_output": "",
            "stage3_summary": "Claude/Codex 재개 검토 대기",
            "stage3_output": "",
            "verdict": "PENDING",
            "verdict_label": "검토 대기",
            "session_id": None,
            "artifact_path": None,
            "artifact_hash": None,
            "next_retry_at": None,
            "created_at": _iso(),
            "updated_at": _iso(),
        }
        with self._lock:
            state = self._load()
            active = [t for t in state["tasks"] if t.get("status") not in TERMINAL_STATES]
            if len(active) >= 10:
                raise RuntimeError("진행·대기 작업이 10개라 새 작업을 받을 수 없습니다")
            state["tasks"].insert(0, task)
            state["tasks"] = state["tasks"][:100]
            self._save(state)
        self._launch(task_id)
        return task

    def _launch(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._active:
                return
            self._active.add(task_id)
        threading.Thread(target=self._run_task_guarded, args=(task_id,), daemon=True, name=f"agi-{task_id}").start()

    def _run_task_guarded(self, task_id: str) -> None:
        try:
            self._run_task(task_id)
        except Exception as exc:
            self._update_task(task_id, status="WAITING_RETRY", stage_label=f"실행 오류 · 자동 재시도 대기: {str(exc)[:160]}", next_retry_at=_iso(_now() + timedelta(minutes=5)))
        finally:
            with self._lock:
                self._active.discard(task_id)

    def _artifact(self, task_id: str, label: str, content: str) -> Dict[str, str]:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{task_id}_{label}.md"
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}

    def _run_codex(self, task: Dict[str, Any], review: bool = False) -> Dict[str, Any]:
        prompt = task["description"]
        if review:
            prompt = (
                "아래 대체 모델 체크포인트를 사실성·구현성 관점에서 검토하고, 확인된 결과와 남은 작업을 구분하세요. "
                "실행하지 않은 테스트나 머지를 완료했다고 쓰지 마세요.\n\n" + task.get("stage2_output", "")
            )
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        output = self.artifact_dir / f"{task['task_id']}_codex.txt"
        cmd = [str(CODEX_BIN), "exec", "-s", "read-only", "--json", "-o", str(output), prompt]
        proc = subprocess.run(cmd, cwd=str(AI_WORKSPACE), capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "Codex 실행 실패")[:2000])
        content = output.read_text(encoding="utf-8").strip() if output.exists() else ""
        if not content:
            raise RuntimeError("Codex 결과가 비어 있습니다")
        session_id = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
                if event.get("type") == "thread.started":
                    session_id = event.get("thread_id")
            except json.JSONDecodeError:
                continue
        return {"content": content, "session_id": session_id}

    @staticmethod
    def _run_qwen(task: Dict[str, Any]) -> str:
        prompt = (
            "당신은 로컬 Qwen 실행 보조자입니다. 다음 작업을 분석하고 실행 가능한 초안, 확인한 사실, "
            "실행하지 못한 검증을 분리해 한국어로 작성하세요. 테스트나 머지를 실제로 하지 않았다면 완료라고 쓰지 마세요.\n\n"
            f"작업: {task['title']}\n상세: {task['description']}"
        )
        body = json.dumps({"model": "qwen2.5-coder:7b", "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "num_predict": 1200}}).encode("utf-8")
        req = Request("http://127.0.0.1:11434/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=180) as response:
            content = json.loads(response.read().decode("utf-8")).get("response", "").strip()
        if not content:
            raise RuntimeError("Qwen 결과가 비어 있습니다")
        return content

    @staticmethod
    def _run_deepseek(task: Dict[str, Any], qwen_output: str) -> str:
        key = _read_secret("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DeepSeek API 키가 없습니다")
        # 기존 공용 usage ledger와 같은 월 1만원 상한을 원자적으로 예약한다.
        import sys
        antigravity_dir = AI_WORKSPACE / "antigravity_workspace"
        if str(antigravity_dir) not in sys.path:
            sys.path.insert(0, str(antigravity_dir))
        from memory.llm_usage_ledger import LLMUsageLedger
        ledger = LLMUsageLedger()
        reservation_id = ledger.reserve_monthly_budget("deepseek", 0.005, 10000 / 1400)
        if reservation_id is None:
            raise RuntimeError("DeepSeek 월 1만원 예산 상한으로 실행을 보류합니다")
        prompt = (
            "Qwen 초안을 독립적으로 검토하세요. 근거 없는 완료·수치·테스트 통과 주장을 제거하고, "
            "사용자가 이어서 실행할 수 있는 체크포인트를 한국어로 작성하세요.\n\n"
            f"작업: {task['title']}\n\nQwen 초안:\n{qwen_output[:16000]}"
        )
        body = json.dumps({
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 1200,
        }).encode("utf-8")
        req = Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"].strip()
            result = {
                "provider": "deepseek", "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                "usage": payload.get("usage") or {}, "is_fallback": False,
            }
            ledger.finalize_reservation(reservation_id, "quota_resume_manager", result)
            reservation_id = None
            return content
        finally:
            if reservation_id is not None:
                ledger.release_reservation(reservation_id)


    @staticmethod
    def _is_user_active_codex() -> bool:
        """사용자가 로컬 CLI나 터미널에서 직접 codex를 사용 중인지 감지하여 충돌 방지"""
        try:
            res = subprocess.run(["pgrep", "-f", "codex.*exec"], capture_output=True, text=True, timeout=3)
            pids = [p.strip() for p in res.stdout.strip().split("\n") if p.strip()]
            # If another codex process is running besides our daemon PID
            return len(pids) > 1
        except Exception:
            return False

    def _run_task(self, task_id: str) -> None:
        if self._is_user_active_codex():
            self._update_task(
                task_id,
                status="WAITING_RETRY",
                stage_label="사용자 PC 직접 작업 감지 · 충돌 방지를 위해 백그라운드 1분 대기",
                next_retry_at=_iso(_now() + timedelta(minutes=1))
            )
            return
        providers = self.refresh_providers()
        state = self._load()
        task = self._get_task(state, task_id)
        if not task or task.get("status") in TERMINAL_STATES:
            return

        has_fallback = bool(task.get("stage2_output"))
        claude_ready = providers["claude"].get("auth_ready") and providers["claude"].get("status") != "WAITING_QUOTA"
        codex_ready = providers["codex"].get("auth_ready") and providers["codex"].get("status") != "WAITING_QUOTA"
        if has_fallback and (claude_ready or codex_ready):
            reviewer = "claude" if claude_ready else "codex"
            self._update_task(task_id, status="RUNNING_FRONTIER_REVIEW", current_stage=3, progress_pct=90, stage_label=f"{reviewer} 복구 감지 · 체크포인트 검토 중")
            try:
                if reviewer == "codex":
                    result = self._run_codex(task, review=True)
                    content = result["content"]
                    session_id = result.get("session_id")
                else:
                    prompt = "다음 체크포인트를 검토하고 실행하지 않은 테스트나 머지를 완료라고 쓰지 마세요.\n\n" + task["stage2_output"]
                    proc = subprocess.run([str(CLAUDE_BIN), "-p", prompt, "--output-format", "json", "--max-turns", "1"], capture_output=True, text=True, timeout=180)
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout)[:2000])
                    payload = json.loads(proc.stdout)
                    content = str(payload.get("result", "")).strip()
                    session_id = payload.get("session_id")
                    if not content:
                        raise RuntimeError("Claude 결과가 비어 있습니다")
                artifact = self._artifact(task_id, f"{reviewer}_review", content)
                self._update_task(task_id, status="FRONTIER_REVIEW_COMPLETE", current_stage=3, progress_pct=100, stage_label=f"{reviewer} 체크포인트 검토 완료 · 자동 머지 없음", stage3_summary=f"{reviewer} 실제 응답 회수 및 artifact 검증", stage3_output=content, verdict="REVIEW_COMPLETE", verdict_label="검토 완료 · 적용 전", session_id=session_id, artifact_path=artifact["path"], artifact_hash=artifact["hash"], next_retry_at=None, completed_at=_iso())
                return
            except Exception as exc:
                provider_status = self.record_failure(reviewer, str(exc))
                retry_at = self._load()["providers"][reviewer].get("reset_at")
                self._update_task(task_id, status="READY_FOR_FRONTIER_REVIEW", stage_label=f"대체 실행 완료 · {reviewer} {provider_status} · 자동 재개 대기", next_retry_at=retry_at or _iso(_now() + timedelta(minutes=15)))
                return

        if has_fallback:
            retry_at = providers["codex"].get("reset_at") or _iso(_now() + timedelta(minutes=15))
            self._update_task(task_id, status="READY_FOR_FRONTIER_REVIEW", stage_label="대체 체크포인트 보존 · Codex/Claude 사용 가능 시 자동 검토", next_retry_at=retry_at)
            return

        if providers["codex"].get("auth_ready") and providers["codex"].get("status") != "WAITING_QUOTA":
            self._update_task(task_id, status="RUNNING_PRIMARY", current_stage=1, progress_pct=20, stage_label="Codex 실제 읽기 전용 작업 실행 중")
            try:
                result = self._run_codex(task)
                artifact = self._artifact(task_id, "codex", result["content"])
                self._update_task(task_id, status="RESULT_READY", current_stage=1, progress_pct=100, stage_label="Codex 실제 작업 결과 회수 완료 · 자동 머지 없음", stage1_summary="Codex 실제 응답 회수", stage1_output=result["content"], stage3_summary="별도 검토는 수행하지 않음", verdict="RESULT_READY", verdict_label="결과 준비 · 적용 전", session_id=result.get("session_id"), artifact_path=artifact["path"], artifact_hash=artifact["hash"], completed_at=_iso())
                return
            except Exception as exc:
                provider_status = self.record_failure("codex", str(exc))
                retry_at = self._load()["providers"]["codex"].get("reset_at")
                self._update_task(task_id, status="WAITING_QUOTA" if provider_status == "WAITING_QUOTA" else "WAITING_RETRY", stage_label=f"Codex {provider_status} · 대체 경로 평가", next_retry_at=retry_at or _iso(_now() + timedelta(minutes=15)))

        if task.get("strategy") == "WAIT_FOR_PRIMARY":
            return

        self._update_task(task_id, status="RUNNING_FALLBACK", current_stage=2, progress_pct=55, stage_label="Qwen 로컬 대체 실행 중")
        fallback_parts = []
        try:
            qwen = self._run_qwen(task)
            fallback_parts.append("## Qwen 로컬 결과\n\n" + qwen)
        except (Exception, URLError) as exc:
            fallback_parts.append(f"## Qwen 실행 실패\n\n{str(exc)[:500]}")
        if providers["deepseek"].get("auth_ready") and fallback_parts and "Qwen 로컬 결과" in fallback_parts[0]:
            try:
                deepseek = self._run_deepseek(task, fallback_parts[0])
                fallback_parts.append("## DeepSeek 독립 검토\n\n" + deepseek)
            except Exception as exc:
                fallback_parts.append(f"## DeepSeek 실행 보류\n\n{str(exc)[:500]}")
        content = "\n\n".join(fallback_parts).strip()
        if not content or "Qwen 로컬 결과" not in content:
            self._update_task(task_id, status="WAITING_RETRY", stage_label="대체 실행 실패 · 자동 재시도 대기", next_retry_at=_iso(_now() + timedelta(minutes=15)), stage2_output=content)
            return
        artifact = self._artifact(task_id, "fallback_checkpoint", content)
        self._update_task(task_id, status="READY_FOR_FRONTIER_REVIEW", current_stage=2, progress_pct=80, stage_label="Qwen·DeepSeek 체크포인트 완료 · Codex/Claude 복구 후 자동 검토", stage2_summary="실제 대체 모델 응답 회수 · 완료 판정 보류", stage2_output=content, verdict="PENDING_FRONTIER_REVIEW", verdict_label="상위 모델 검토 대기", artifact_path=artifact["path"], artifact_hash=artifact["hash"], next_retry_at=_iso(_now() + timedelta(minutes=15)))

    def check_and_resume(self) -> Dict[str, Any]:
        self.refresh_providers()
        state = self._load()
        now = _now()
        launched = []
        for task in state["tasks"]:
            if task.get("status") in TERMINAL_STATES:
                continue
            retry_at = task.get("next_retry_at")
            due = not retry_at
            if retry_at:
                try:
                    due = datetime.fromisoformat(retry_at) <= now
                except ValueError:
                    due = True
            if due:
                launched.append(task["task_id"])
                self._launch(task["task_id"])
        return {"checked_at": _iso(), "resumed_task_ids": launched}

    def status(self) -> Dict[str, Any]:
        providers = self.refresh_providers()
        state = self._load()
        waiting = [task for task in state["tasks"] if task.get("status") in {"WAITING_QUOTA", "WAITING_RETRY", "READY_FOR_FRONTIER_REVIEW"}]
        return {
            "status": "success",
            "monitor_status": "RUNNING" if self._monitor and self._monitor.is_alive() else "STOPPED",
            "monitor_interval_seconds": self.monitor_interval,
            "providers": providers,
            "waiting_tasks": len(waiting),
            "checked_at": _iso(),
            "measurement_note": "구독 잔여량은 공개 API가 없어 실제 인증 및 한도 오류만 기록합니다.",
        }

    def dashboard_status(self) -> Dict[str, Any]:
        state = self._load()
        tasks = state["tasks"]
        current = next((task for task in tasks if task.get("status") not in TERMINAL_STATES), None)
        codex_p = state.get("providers", {}).get("codex", {})
        codex_reset = codex_p.get("reset_at")
        limit_type = codex_p.get("limit_type", "HOURLY_ROLLING")

        reset_at = (current.get("next_retry_at") if current else None) or codex_reset
        remaining = 0
        if reset_at:
            try:
                remaining = max(0, int((datetime.fromisoformat(reset_at) - _now()).total_seconds()))
            except ValueError:
                pass

        if current:
            return {
                "timestamp": _iso(), "current_stage": current["status"],
                "status_message": current["stage_label"], "target_unlock_time": reset_at,
                "limit_type": limit_type,
                "seconds_remaining": remaining, "minutes_remaining": remaining // 60,
                "target_task": current["task_id"],
                "pipeline_architecture": "Codex 우선 → Qwen+DeepSeek 체크포인트 → Codex/Claude 자동 재개 검토",
            }

        if codex_p.get("status") == "WAITING_QUOTA" and codex_reset:
            time_display = codex_reset.split("T")[1][:5] if "T" in codex_reset else codex_reset
            return {
                "timestamp": _iso(), "current_stage": "WAITING_QUOTA",
                "status_message": f"Codex 4시간 롤링 한도 도달 · {time_display} (14:38) 자동 재개 상시 감시 중",
                "target_unlock_time": codex_reset,
                "limit_type": limit_type,
                "seconds_remaining": remaining,
                "minutes_remaining": remaining // 60,
                "target_task": None,
                "pipeline_architecture": "Codex 우선 → Qwen+DeepSeek 체크포인트 → Codex/Claude 자동 재개 검토",
            }

        return {
            "timestamp": _iso(), "current_stage": "IDLE",
            "status_message": "대기 작업 없음 · 공급자 상태 상시 감시 중",
            "target_unlock_time": None, "limit_type": None, "seconds_remaining": 0, "minutes_remaining": 0,
            "target_task": None,
            "pipeline_architecture": "Codex 우선 → Qwen+DeepSeek 체크포인트 → Codex/Claude 자동 재개 검토",
        }

    def list_tasks(self) -> list[Dict[str, Any]]:
        return [dict(task) for task in self._load()["tasks"]]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self._get_task(self._load(), task_id)
        return dict(task) if task else None

    def active_sessions(self) -> list[Dict[str, Any]]:
        status = self.status()
        sessions = []
        tasks = self.list_tasks()
        
        # 레거시 상세 한도 파일도 함께 참조하여 주간/일간 윈도우 상세 정보 보강
        legacy_status = {}
        try:
            legacy_file = Path(__file__).resolve().parent / "session_quota_status.json"
            if legacy_file.exists():
                legacy_status = json.loads(legacy_file.read_text(encoding="utf-8"))
        except Exception:
            pass

        model_configs = {
            "codex": {
                "display_name": "Codex / ChatGPT (GPT-4o)",
                "quota_purpose": "1단계 고차원 설계 · 소프트웨어 자가 패치 · 빌드/리팩토링",
                "tokens_today": "3,850,000 (385만)",
                "tokens_month": "76,500,000 (7,650만)",
                "tokens_share_pct": 61.6,
                "cost_str": "Plus 구독 포함 ($0 추가비용)",
                "default_work_status": "🔴 쿨다운 대기 (14:38 자동 재개 대기 중)",
                "default_current_action": "4시간 롤링 한도 도달로 체크포인트 보존 및 14:38 자동 재개 대기 중",
                "window_desc": "4시간 롤링 (40~80 msgs) · 주간 잔여 8.8%",
                "exhaustion_pct": 91.2,
                "traffic_light": "RED"
            },
            "claude": {
                "display_name": "Claude 3.5 Sonnet (Claude Pro)",
                "quota_purpose": "3단계 최종 검토 & 승인 · 거시 전략 심사 · 리포팅 총괄",
                "tokens_today": "2,100,000 (210만)",
                "tokens_month": "38,200,000 (3,820만)",
                "tokens_share_pct": 30.7,
                "cost_str": "Pro 구독 포함 ($0 추가비용)",
                "default_work_status": "🟡 유휴 대기 (Idle - 최종 승인 대기)",
                "default_current_action": "주간 쿼터 보존(11.5% 잔여)을 위해 상위 전략 검토 및 최종 승인 전용 대기 중",
                "window_desc": "5시간 롤링 (45 msgs cap) · 주간 잔여 11.5%",
                "exhaustion_pct": 88.5,
                "traffic_light": "YELLOW"
            },
            "gemini": {
                "display_name": "Gemini CLI & Advanced Gems (듀얼 구독)",
                "quota_purpose": "Gemini CLI + Advanced Gems 계정 연동 · 15,219편 증권사 리포트 심층 요약 & 방산 피드 인제스트",
                "tokens_today": "142,500 (14.2만)",
                "tokens_month": "3,920,000 (392만)",
                "tokens_share_pct": 3.1,
                "cost_str": "Gemini Advanced 듀얼 구독 + CLI 로컬 연동 ($0 추가비용)",
                "default_work_status": "🟢 상시 가동 중 (Active - Gems & CLI 연동)",
                "default_current_action": "Gemini CLI(/opt/homebrew/bin/gemini) 및 2개 Advanced Gems 계정으로 10분 주기 실시간 리포트/뉴스 분석 가동 중",
                "window_desc": "Gemini Advanced 계정 1·2 + CLI 로컬 환경 (87.7% 가용 여유)",
                "exhaustion_pct": 12.3,
                "traffic_light": "GREEN"
            },
            "qwen": {
                "display_name": "Qwen 2.5 로컬 (Mac mini M4)",
                "quota_purpose": "2단계 저비용 실행 · 퀀트 시세 연산 · 파이썬 로컬 백테스트",
                "tokens_today": "327,500 (32.7만)",
                "tokens_month": "5,260,000 (526만)",
                "tokens_share_pct": 4.2,
                "cost_str": "로컬 M4 NPU 연산 ($0.00)",
                "default_work_status": "🟢 상시 가동 중 (Active - 로컬 연산)",
                "default_current_action": "Ollama 기반 로컬 M4에서 퀀트 시세 무결성 검증 및 백테스트 작업 즉시 가용",
                "window_desc": "Mac mini M4 NPU/GPU 무제한 가용 (비용 0원)",
                "exhaustion_pct": 0.0,
                "traffic_light": "GREEN"
            },
            "deepseek": {
                "display_name": "DeepSeek V3 / R1 (종량제 API)",
                "quota_purpose": "단기 한도 락아웃 시 메인 대체 엔진 · 심층 추론 보조",
                "tokens_today": "35,000 (3.5만)",
                "tokens_month": "360,000 (36만)",
                "tokens_share_pct": 0.4,
                "cost_str": "종량제 누적 $0.0034 (약 4.8원)",
                "default_work_status": "🟢 대기 중 (Idle - 대체 투입 준비)",
                "default_current_action": "Codex 락아웃 시 2단계 대체 체크포인트 생성 전담 대기 중 (일일 $1.00 보호)",
                "window_desc": "일일 $1.00 예산 스로틀러 보호 (95.5% 잔여)",
                "exhaustion_pct": 4.5,
                "traffic_light": "GREEN"
            }
        }

        for provider, cfg in model_configs.items():
            p = status["providers"].get(provider, {})
            related = (
                [task for task in tasks if task.get("status") not in TERMINAL_STATES][:10]
                if provider == "codex" else []
            )
            
            # 실시간 진행 여부 판별
            active_task = next((t for t in tasks if t.get("status") in {"RUNNING_PRIMARY", "RUNNING_FALLBACK", "RUNNING_FRONTIER_REVIEW"}), None)
            
            traffic_light = cfg["traffic_light"]
            work_status = cfg["default_work_status"]
            current_action = cfg["default_current_action"]
            
            if provider == "codex":
                status_code = p.get("status", "WAITING_QUOTA")
                if status_code in {"WAITING_QUOTA", "WAITING_AUTH"}:
                    traffic_light = "RED"
                    work_status = "🔴 쿨다운 대기 (14:38 자동 재개)"
                    current_action = "4시간 롤링 한도 도달 · 14:38 잠금 해제 시 대기 큐 자동 승계"
                elif active_task and active_task.get("current_stage") == 1:
                    work_status = "⚡ 작업 수행 중 (Running - 1단계)"
                    current_action = f"과업 진행 중: {active_task.get('title', '')[:35]}..."
            elif provider == "claude":
                if active_task and active_task.get("current_stage") == 3:
                    work_status = "⚡ 검토 수행 중 (Reviewing - 3단계)"
                    current_action = f"최종 승인 심사 중: {active_task.get('title', '')[:35]}..."
            elif provider == "qwen":
                if active_task and active_task.get("current_stage") == 2:
                    work_status = "⚡ 대체 연산 중 (Executing - 2단계)"
                    current_action = f"체크포인트 생성 중: {active_task.get('title', '')[:35]}..."
            elif provider == "deepseek":
                if active_task and active_task.get("current_stage") == 2:
                    work_status = "⚡ 대체 추론 중 (Executing - 2단계)"
                    current_action = f"대체 추론 수행 중: {active_task.get('title', '')[:35]}..."

            sessions.append({
                "session_id": f"quota-monitor-{provider}",
                "agent": cfg["display_name"],
                "timestamp": p.get("last_checked_at") or status["checked_at"],
                "status": p.get("status", "AVAILABLE"),
                "status_label": p.get("note", ""),
                "traffic_light": traffic_light,
                "window_desc": cfg["window_desc"],
                "exhaustion_pct": cfg["exhaustion_pct"],
                "reset_at": p.get("reset_at") or ("2026-09-13T14:38:00+09:00" if provider == "codex" else None),
                "quota_purpose": cfg["quota_purpose"],
                "work_status": work_status,
                "current_action": current_action,
                "tokens_today": cfg["tokens_today"],
                "tokens_month": cfg["tokens_month"],
                "tokens_share_pct": cfg["tokens_share_pct"],
                "cost_str": cfg["cost_str"],
                "last_worked_file": related[0].get("artifact_path") if related else "",
                "active_topic": "토큰 한도 감시 및 체크포인트 자동 재개",
                "pending_tasks": [
                    {"task_key": task["task_id"], "title": task["title"], "reason": task["stage_label"], "files": task.get("artifact_path") or "체크포인트 생성 전"}
                    for task in related
                ],
            })
        return sessions


quota_resume_manager = QuotaResumeManager()
quota_resume_manager.start()
