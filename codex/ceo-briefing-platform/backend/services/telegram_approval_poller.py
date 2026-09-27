# -*- coding: utf-8 -*-
"""
텔레그램에서 "최종 승인" 버튼을 누르면 실제로 code-job이 적용되게 하는 폴러.

소유자 지시(2026-09-14): "텔레그램에서 최종 승인 누르면 나오도록 해줘." 지금까지는
텔레그램에 알림만 가고 실제 승인은 웹(/agentic-code)에서만 가능했다.

2026-09-17 보강 (handoff/AGENTIC_EXECUTION_REPAIR_HANDOFF_2026-09-17.md P0-1/P0-2
지적 반영, 독립 코드 검토로 실제 코드에서 재현·확인된 결함):
1. **콜백 데이터 64바이트 초과(실측 108바이트)** — `apply:{job_id}:{diff_hash}`는
   Telegram의 공식 InlineKeyboardButton.callback_data 상한(1-64바이트)을 넘는다.
   Telegram이 이런 버튼을 아예 안 보여주거나 전송을 거부할 수 있어, 애초에 버튼이
   눌리지 않았을 가능성이 있다. → job_id/diff_hash를 직접 담지 않고, 서버 쪽에
   미리 발급한 **짧은 불투명 토큰**(16 hex = "apply:"+16 = 22바이트)만 담는다.
2. **거짓 성공 표시** — `apply_to_workspace()`는 실패해도 예외를 던지지 않고
   `{"status": "APPLY_FAILED", ...}`를 정상 반환한다. 이전 코드는 예외 발생
   여부만으로 성공/실패를 판정해 **실패 응답도 "적용 완료"로 오인**했다. → 반환된
   `status` 필드를 직접 확인한다.
3. **allowed_chat_id 미설정 시 그냥 통과** — `TELEGRAM_CHAT_ID`가 비어 있으면
   `if self.allowed_chat_id and ...` 조건 자체가 거짓이 돼 아무나 승인 가능했다.
   → 설정이 없으면 명시적으로 거부(fail-closed)한다.
4. **재시작 시 과거 콜백 재처리** — `_offset`이 메모리에만 있어 프로세스 재시작
   때마다 Telegram이 아직 갖고 있는 예전 업데이트를 처음부터 다시 받아, 과거에
   이미 처리된 승인 버튼이 재실행될 수 있었다. → offset과 토큰 소비 여부를
   같은 SQLite 파일에 영속화한다(토큰은 1회용 - 재전송돼도 이미 소비됐으면 거부).

설계 원칙(기존대로 유지):
- 자유 텍스트 답장을 파싱해 자동 승인하지 않는다 - 텔레그램 inline keyboard 버튼
  (callback_query)만 처리한다.
- 오직 "최종 승인"(READY_FOR_REVIEW → 실제 저장소 적용) 한 가지만 텔레그램에서
  가능하다. 1차 승인/거부/롤백은 여전히 웹에서만.
- 이 봇(GOAL_INTAKE_BOT_TOKEN)을 폴링하는 다른 프로세스가 없는지 확인 후 단일
  스레드로만 동작한다(409 충돌 방지 - 기존 결정과 일관).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)
_ENV_PATH = Path("/Volumes/Realtek_NVME/AI System/antigravity_workspace/.env")
_STATE_DIR = Path(__file__).resolve().parents[1] / "data"
_STATE_DB = _STATE_DIR / "telegram_approval_tokens.sqlite3"
# 토큰은 "apply:" + 16자리 hex = 22바이트 - Telegram callback_data 64바이트 상한에
# 여유 있게 들어간다. 옛 형식(job_id:diff_hash 직접 노출)은 더 이상 발급하지 않지만,
# 이미 전송된 옛 메시지의 버튼을 누르면 명확한 오류로 안내는 해야 하므로 패턴만 남겨둔다.
_CALLBACK_RE = re.compile(r"^apply:([0-9a-f]{16,64})$")
_TOKEN_TTL_SECONDS = 24 * 3600


def _read_env(key: str) -> str:
    try:
        for line in _ENV_PATH.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _db() -> sqlite3.Connection:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_STATE_DB, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS approval_tokens("
        "token TEXT PRIMARY KEY, job_id TEXT NOT NULL, diff_hash TEXT NOT NULL,"
        "created_at REAL NOT NULL, expires_at REAL NOT NULL, consumed_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS poller_offset(id INTEGER PRIMARY KEY CHECK(id=1), next_offset INTEGER NOT NULL)"
    )
    return conn


def mint_approval_token(job_id: str, diff_hash: str) -> str:
    """READY_FOR_REVIEW 알림을 보낼 때 job_id/diff_hash를 직접 노출하지 않는 짧은
    1회용 토큰을 발급한다. 콜백에서 이 토큰만 오면 서버가 실제 job_id/diff_hash로
    역매핑한다."""
    token = secrets.token_hex(8)
    now = time.time()
    with _db() as conn:
        conn.execute(
            "INSERT INTO approval_tokens(token, job_id, diff_hash, created_at, expires_at) VALUES(?,?,?,?,?)",
            (token, job_id, diff_hash, now, now + _TOKEN_TTL_SECONDS),
        )
    return token


def _resolve_and_consume_token(token: str) -> tuple[Optional[str], Optional[str], str]:
    """토큰을 1회 소비하며 (job_id, diff_hash, 이유)를 반환한다. job_id가 None이면
    실패 - 이유 문자열에 사람이 읽을 사유가 담긴다. 소비는 원자적(같은 트랜잭션 내
    UPDATE ... WHERE consumed_at IS NULL)이라 같은 토큰이 동시에 두 번 들어와도
    한쪽만 통과한다."""
    now = time.time()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_id, diff_hash, expires_at, consumed_at FROM approval_tokens WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None, None, "알 수 없거나 만료된 버튼입니다"
        job_id, diff_hash, expires_at, consumed_at = row
        if consumed_at is not None:
            return None, None, "이미 처리된 승인입니다(중복 클릭 또는 재시작 후 재전송)"
        if now > expires_at:
            return None, None, "승인 유효기간이 지났습니다 - 웹 대시보드에서 다시 확인해주세요"
        updated = conn.execute(
            "UPDATE approval_tokens SET consumed_at=? WHERE token=? AND consumed_at IS NULL", (now, token)
        ).rowcount
        if not updated:
            return None, None, "이미 처리된 승인입니다(동시 클릭)"
        return job_id, diff_hash, ""


class TelegramApprovalPoller:
    def __init__(self, code_jobs=None, poll_interval: float = 5.0):
        from services.approved_code_jobs import CodeJobs
        self.code_jobs = code_jobs or CodeJobs()
        self.token = _read_env("GOAL_INTAKE_BOT_TOKEN")
        self.allowed_chat_id = _read_env("TELEGRAM_CHAT_ID")
        self.poll_interval = poll_interval
        self._offset = self._load_offset()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _load_offset(self) -> int:
        try:
            with _db() as conn:
                row = conn.execute("SELECT next_offset FROM poller_offset WHERE id=1").fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def _save_offset(self, offset: int) -> None:
        with _db() as conn:
            conn.execute(
                "INSERT INTO poller_offset(id, next_offset) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET next_offset=excluded.next_offset",
                (offset,),
            )

    def start(self) -> None:
        if not self.token:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram-approval-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _api(self, method: str, payload: dict) -> dict:
        body = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", data=body)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._poll_once()
            except Exception:
                pass  # 폴링 실패가 서비스를 죽이면 안 된다 - 다음 주기에 재시도

    def _poll_once(self) -> None:
        params = urllib.parse.urlencode({
            "offset": self._offset, "timeout": 0,
            "allowed_updates": json.dumps(["callback_query"]),
        })
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/getUpdates?{params}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return  # 다른 프로세스가 같은 봇을 폴링 중 - 조용히 이번 주기만 건너뜀
            raise
        for update in data.get("result", []):
            self._offset = max(self._offset, update.get("update_id", 0) + 1)
            self._save_offset(self._offset)  # 재시작 시 같은 콜백을 다시 받지 않도록 매 건 영속화
            cq = update.get("callback_query")
            if cq:
                self._handle_callback(cq)

    def _handle_callback(self, cq: dict) -> None:
        callback_id = cq.get("id", "")
        chat_id = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
        if not self.allowed_chat_id:
            # 설정 누락을 "검사 생략"으로 취급하면 안 된다 - 실패로 닫는다.
            self._answer(callback_id, "승인 채널 설정 누락 - 관리자에게 문의하세요")
            return
        if chat_id != self.allowed_chat_id:
            self._answer(callback_id, "허용되지 않은 사용자입니다")
            return
        match = _CALLBACK_RE.match(cq.get("data") or "")
        if not match:
            self._answer(callback_id, "알 수 없는 버튼입니다")
            return
        token = match.group(1)
        job_id, diff_hash, reason = _resolve_and_consume_token(token)
        if not job_id:
            self._answer(callback_id, reason)
            return
        try:
            result = self.code_jobs.apply_to_workspace(job_id, diff_hash, approved_by="owner_via_telegram")
            status = result.get("status")
        except Exception as exc:
            status, result = "EXCEPTION", {"error": str(exc)[:300]}
        if status == "APPLIED_TO_WORKSPACE":
            self._answer(callback_id, "✅ 실제 적용 완료")
            self._mark_message_done(cq, f"✅ 최종 승인됨 (job {job_id}) - 실제 저장소에 적용되었습니다.")
        else:
            # apply_to_workspace는 실패해도 예외 없이 status만 바꿔 반환한다 - 여기서
            # status를 직접 확인하지 않으면 실패가 성공으로 보고된다(2026-09-17 발견).
            detail = str(result.get("error") or status or "알 수 없는 오류")[:300]
            self._answer(callback_id, f"실패({status}): {detail[:150]}")
            self._mark_message_done(cq, f"❌ 적용 실패 (job {job_id}, status={status}): {detail}\n웹 대시보드에서 확인해주세요.")

    def _answer(self, callback_id: str, text: str) -> None:
        try:
            self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
        except Exception:
            pass

    def _mark_message_done(self, cq: dict, new_text: str) -> None:
        # 버튼 재입력(중복 적용 시도) 방지를 위해 원문 메시지를 결과로 덮어쓴다.
        # (토큰이 1회용이라 실제로는 재입력 자체가 거부되지만, 메시지도 눈에 보이게 바꿔둔다.)
        message = cq.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        if chat_id is None or message_id is None:
            return
        try:
            self._api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": new_text})
        except Exception:
            pass


def send_final_approval_prompt(job_id: str, diff_hash: str, summary: str) -> None:
    """READY_FOR_REVIEW 알림에 "최종 승인" 인라인 버튼을 붙여 보낸다. job_id/diff_hash를
    콜백에 직접 담지 않고 짧은 1회용 토큰으로 대체한다(64바이트 제한 + 재전송 방지)."""
    bot_token = _read_env("GOAL_INTAKE_BOT_TOKEN")
    chat_id = _read_env("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    approval_token = mint_approval_token(job_id, diff_hash)
    callback_data = f"apply:{approval_token}"
    assert len(callback_data.encode("utf-8")) <= 64, "Telegram callback_data 64바이트 상한 초과"
    keyboard = {"inline_keyboard": [[{"text": "✅ 최종 승인 (실제 적용)", "callback_data": callback_data}]]}
    try:
        body = urllib.parse.urlencode({
            "chat_id": chat_id, "text": summary,
            "reply_markup": json.dumps(keyboard),
        }).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=body)
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        # 2026-09-18 실사용 중 재현: 여기서 조용히 삼키던 예외 때문에, 1차 승인은
        # 서버에 정상 기록됐는데(토큰도 발급됨) 소유자 폰에는 텔레그램이 실제로는
        # 하나도 안 온 사고가 났고, 원인을 알 방법이 없었다(로그에 아무 흔적도 없음).
        # 알림 실패가 승인 절차 자체를 막으면 안 되므로 여전히 예외는 삼키되, 최소한
        # 로그에는 남겨 다음엔 바로 원인을 볼 수 있게 한다.
        _LOG.exception("Telegram 최종 승인 알림 전송 실패 (job_id=%s)", job_id)
