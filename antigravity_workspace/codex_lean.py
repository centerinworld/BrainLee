"""ChatGPT 구독(Codex CLI)으로 GPT를 부르는 경량 호출 + 일일 상한.

종량제 OpenAI API 키 없이, 이미 로그인된 ChatGPT 구독 한도(`codex login status` ->
"Logged in using ChatGPT")로 GPT를 쓴다. stdlib만 쓰므로 백엔드/워커 어느 venv에서도
import 된다.

기본 `codex exec`는 사용자 플러그인/스킬/AGENTS.md까지 불러와 호출 한 번에 입력 토큰이
1.7만 안팎이라 뉴스 분류 같은 단순 작업에 낭비가 크다. 여기서는 세션 저장 없음
(--ephemeral), 사용자 설정·규칙 무시(--ignore-user-config/--ignore-rules), 빈 임시
디렉터리, 낮은 추론 강도, 소형 모델(gpt-5.6-luna)로 호출해 이 낭비를 줄인다(실측 약 1.2만,
그중 대부분 캐시).

구독 한도는 사용자가 Codex를 직접 쓸 때와 공유되므로 하루 호출 상한과, 한도 오류가
나면 일정 시간 호출을 멈추는 쿨다운을 둔다 - 자동 폴백이 구독을 소진해 정작 사용자의
Codex 작업을 막는 일을 피하기 위함이다.
"""

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

CODEX_CLI_PATH = os.getenv("CODEX_CLI_PATH", "/Applications/ChatGPT.app/Contents/Resources/codex")
DEFAULT_MODEL = os.getenv("CODEX_LEAN_MODEL", "gpt-5.6-luna")
DEFAULT_EFFORT = os.getenv("CODEX_LEAN_EFFORT", "low")
DAILY_CAP = int(os.getenv("CODEX_LEAN_DAILY_CAP", "40"))
COOLDOWN_SECONDS = int(os.getenv("CODEX_LEAN_COOLDOWN_SECONDS", "1800"))
DB_PATH = os.getenv("CODEX_LEAN_DB_PATH") or str(
    Path(__file__).resolve().parent / "memory" / "codex_lean_usage.sqlite3"
)

_LIMIT_HINTS = ("usage limit", "rate limit", "quota", "too many requests", "limit reached", "429")
_TEXT_ONLY = "도구 호출이나 명령 실행 없이 텍스트로만 답하라.\n\n"


class CodexLeanUnavailable(RuntimeError):
    """CLI 없음/비활성/일일 상한/쿨다운 - 호출 자체를 시도하지 않았다."""


def enabled() -> bool:
    return os.getenv("CODEX_LEAN_ENABLED", "1") != "0"


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY CHECK (id = 1), until REAL NOT NULL)")
    return conn


def status() -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT calls FROM daily WHERE day=?", (datetime.now().strftime("%Y-%m-%d"),)).fetchone()
        cd = conn.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
    finally:
        conn.close()
    return {
        "enabled": enabled(),
        "cli_present": os.path.exists(CODEX_CLI_PATH),
        "calls_today": int(row[0]) if row else 0,
        "daily_cap": DAILY_CAP,
        "cooldown_until": cd[0] if cd and cd[0] > time.time() else None,
    }


def _reserve() -> None:
    """상한/쿨다운을 확인하고 오늘 호출 수를 원자적으로 1 올린다."""
    day = datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cd = conn.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
        if cd and cd[0] > time.time():
            conn.rollback()
            raise CodexLeanUnavailable(f"ChatGPT 구독 한도 쿨다운 중({int(cd[0] - time.time())}초 남음)")
        row = conn.execute("SELECT calls FROM daily WHERE day=?", (day,)).fetchone()
        calls = int(row[0]) if row else 0
        if calls >= DAILY_CAP:
            conn.rollback()
            raise CodexLeanUnavailable(f"Codex 경량 호출 일일 상한 도달({calls}/{DAILY_CAP})")
        conn.execute(
            "INSERT INTO daily(day, calls) VALUES(?, 1) ON CONFLICT(day) DO UPDATE SET calls = calls + 1", (day,)
        )
        conn.commit()
    finally:
        conn.close()


def _start_cooldown() -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO cooldown(id, until) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET until = excluded.until",
            (time.time() + COOLDOWN_SECONDS,),
        )
        conn.commit()
    finally:
        conn.close()


def complete(
    prompt: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """프롬프트를 ChatGPT 구독으로 보내 텍스트 답을 받는다.

    반환: {"content", "usage"(prompt/completion/total_tokens 또는 None), "model"}
    CodexLeanUnavailable: 호출을 시도하지 않음. RuntimeError: 호출했지만 실패/빈 응답.
    """
    if not enabled():
        raise CodexLeanUnavailable("CODEX_LEAN_ENABLED=0")
    if not os.path.exists(CODEX_CLI_PATH):
        raise CodexLeanUnavailable(f"Codex CLI 없음: {CODEX_CLI_PATH}")
    _reserve()

    target_model = model or DEFAULT_MODEL
    with tempfile.TemporaryDirectory(prefix="codex_lean_") as workdir:
        out_path = os.path.join(workdir, "last_message.txt")
        cmd = [
            CODEX_CLI_PATH, "exec",
            "-m", target_model,
            "-s", "read-only",
            "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "-c", f"model_reasoning_effort={effort or DEFAULT_EFFORT}",
            "-C", workdir,
            "--json", "-o", out_path,
            "-",
        ]
        try:
            res = subprocess.run(
                cmd, input=_TEXT_ONLY + prompt, capture_output=True, text=True, timeout=timeout, cwd=workdir
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex_lean 시간 초과({timeout}초)")

        combined = f"{res.stdout}\n{res.stderr}".lower()
        if res.returncode != 0:
            if any(hint in combined for hint in _LIMIT_HINTS):
                _start_cooldown()
            raise RuntimeError(f"codex_lean 실행 실패(exit={res.returncode}): {(res.stderr or res.stdout)[:300]}")

        usage = None
        message_text = ""
        for line in res.stdout.splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "turn.completed":
                u = event.get("usage") or {}
                prompt_tokens = u.get("input_tokens") or 0
                completion_tokens = u.get("output_tokens") or 0
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                message_text = item.get("text") or message_text

        content = ""
        if os.path.exists(out_path):
            with open(out_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        content = content or message_text.strip()
        if not content:
            raise RuntimeError("codex_lean 응답이 비어있음")
        return {"content": content, "usage": usage, "model": target_model}
