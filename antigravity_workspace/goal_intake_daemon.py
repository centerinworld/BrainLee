"""
Project Antigravity: 텔레그램 목표 수신 데몬 (완전 자동화 1차 단계)

기존 TELEGRAM_BOT_TOKEN은 OpenClaw가 이미 polling 중일 수 있어 건드리지 않는다
(stock_dashboard/agi_session_monitor_daemon.py:307 "OpenClaw handles incoming polling to
prevent 409 conflict" 참고 - 소유자 확인: OpenClaw가 지금도 폴링 중일 가능성이 높음).
같은 봇 토큰을 두 곳에서 동시에 getUpdates polling하면 텔레그램 API가 409 Conflict를
반환하므로, 이 데몬은 소유자가 새로 만든 **별도의** 봇 토큰(GOAL_INTAKE_BOT_TOKEN)으로만
폴링한다. 발신(브리핑/알림)은 기존 봇 그대로 두고, 이 새 봇은 오직 목표 수신용이다.

흐름: 소유자가 이 새 봇에게 목표를 텍스트로 보내면 -> TELEGRAM_CHAT_ID 발신만 인정(다른
chat_id는 무시) -> L1PMOwner.parse_founder_intent()로 태스크 생성 + state_ledger에 기록
-> "접수했습니다" 응답.

실제 실행(execute_handoff)은 이번 단계에서 동기로 연결하지 않는다 - 텔레그램 응답은 몇 초
안에 와야 하는데 실제 파이프라인 실행(주식 리밸런싱/방산 인텔리전스 수집)은 몇 분 걸릴 수
있다. "접수 확인"까지만 여기서 하고, 실행/완료 알림은 별도의 스케줄러(로드맵 문서 참고,
이번 단계에는 포함하지 않음)가 state_ledger의 "goal_intake" 도메인을 폴링해 처리한다.

2026-09-13 검수(handoff/CLAUDE_IMPLEMENTATION_REVIEW_2026-09-13.md) 지적 보완:
1. allowed_chat_id가 비어있으면(환경변수 미설정) 기존엔 "제한 없음"으로 열려버렸다(fail-open).
   이제 비어있으면 명시적으로 fail-closed - 모든 메시지를 거부하고 CRITICAL 로그를 남긴다.
2. offset이 메모리에만 있어 데몬 재시작 시 0으로 초기화되면 텔레그램이 과거 update를
   전부 재전송한다. 이제 StateLedger에 영속시켜 재시작 후에도 이미 처리한 지점부터
   이어받는다.
3. 같은 update_id가 재전송되면(오프셋 유실/재시도 등) 태스크를 중복 생성하지 않도록
   처리한 update_id를 별도 영속 기록해 멱등 처리한다.
4. 사용자 원문(source_text)을 goal_intake/tasks 레코드에 함께 보존한다 - 현재 두 고정
   파이프라인(주식/방산) payload에는 원문이 없어, 추후 범용 실행으로 넘어갈 때 원문/수락
   기준을 잃어버리는 문제(R05)를 이 레코드가 최소한이나마 감사 가능하게 해 둔다.
"""

import os
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from agents.l1_pm_owner import L1PMOwner
from memory.state_ledger import StateLedger

logger = logging.getLogger("goal_intake_daemon")

POLL_TIMEOUT_SECONDS = 25  # 텔레그램 long-poll timeout
OFFSET_STATE_DOMAIN = "goal_intake_daemon_state"
OFFSET_STATE_KEY = "telegram_offset"
PROCESSED_UPDATES_DOMAIN = "goal_intake_processed_updates"


class GoalIntakeDaemon:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        allowed_chat_id: Optional[str] = None,
        pm_owner: Optional[L1PMOwner] = None,
        ledger: Optional[StateLedger] = None,
    ):
        self.bot_token = bot_token or os.getenv("GOAL_INTAKE_BOT_TOKEN")
        self.allowed_chat_id = str(allowed_chat_id if allowed_chat_id is not None else os.getenv("TELEGRAM_CHAT_ID") or "")
        self.pm_owner = pm_owner or L1PMOwner(auto_heal=True)
        self.ledger = ledger or StateLedger()
        self._offset = self._load_persisted_offset()

    def _load_persisted_offset(self) -> int:
        state = self.ledger.get(OFFSET_STATE_DOMAIN, OFFSET_STATE_KEY)
        return int(state["value"]) if state else 0

    def _persist_offset(self, offset: int) -> None:
        self.ledger.upsert(OFFSET_STATE_DOMAIN, OFFSET_STATE_KEY, {"value": offset})

    def _already_processed(self, update_id: Any) -> bool:
        return self.ledger.exists(PROCESSED_UPDATES_DOMAIN, str(update_id))

    def _mark_processed(self, update_id: Any) -> None:
        self.ledger.upsert(PROCESSED_UPDATES_DOMAIN, str(update_id), {"processed_at": datetime.now().isoformat()})

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def send_ack(self, chat_id: str, text: str) -> bool:
        if not self.bot_token:
            logger.error("GOAL_INTAKE_BOT_TOKEN 미설정 - 응답을 보낼 수 없음")
            return False
        try:
            res = requests.post(
                self._api_url("sendMessage"),
                json={"chat_id": chat_id, "text": text},
                timeout=10
            )
            if not res.ok:
                logger.warning(f"텔레그램 응답 전송 실패 {res.status_code}: {res.text[:100]}")
            return res.ok
        except Exception as e:
            logger.error(f"텔레그램 응답 전송 오류: {e}")
            return False

    def fetch_updates(self) -> List[Dict[str, Any]]:
        if not self.bot_token:
            raise RuntimeError("GOAL_INTAKE_BOT_TOKEN이 설정되지 않아 폴링할 수 없습니다.")
        res = requests.get(
            self._api_url("getUpdates"),
            params={"offset": self._offset, "timeout": POLL_TIMEOUT_SECONDS},
            timeout=POLL_TIMEOUT_SECONDS + 10
        )
        res.raise_for_status()
        return res.json().get("result", [])

    def handle_update(self, update: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """단일 update를 처리한다. 접수된 태스크 목록(0개 이상)을 반환하거나, 무시했으면 None.

        offset은 이 update에 대한 처리 방침(중복/미인증/무텍스트/정상)이 전부 결정된
        뒤에만 전진·영속시킨다 - 처리 도중 예외가 나면 offset이 전진하지 않아, 재시작 시
        텔레그램이 이 update를 다시 보내고 재처리를 시도할 수 있다(멱등 체크가 중복
        생성은 막아준다)."""
        update_id = update.get("update_id")

        def _advance_and_return(result):
            if update_id is not None:
                self._mark_processed(update_id)
                self._offset = max(self._offset, update_id + 1)
                self._persist_offset(self._offset)
            return result

        if update_id is not None and self._already_processed(update_id):
            logger.info(f"이미 처리한 update_id({update_id}) 재수신 - 중복 생성 방지를 위해 무시")
            return _advance_and_return(None)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = message.get("text", "")

        if not text:
            return _advance_and_return(None)

        if not self.allowed_chat_id:
            logger.critical(
                "TELEGRAM_CHAT_ID(allowed_chat_id)가 설정되지 않아 모든 발신자를 거부합니다(fail-closed). "
                f"수신 메시지 무시: chat_id={chat_id}"
            )
            return _advance_and_return(None)

        if chat_id != self.allowed_chat_id:
            logger.warning(f"허용되지 않은 chat_id({chat_id})의 메시지 무시: '{text[:40]}'")
            return _advance_and_return(None)

        tasks = self.pm_owner.parse_founder_intent(text)
        if not tasks:
            self.send_ack(chat_id, "요청을 이해하지 못했습니다 (주식/방산 도메인으로 분류되지 않음). 다시 말씀해주세요.")
            return _advance_and_return([])

        for t in tasks:
            enriched = {
                **t,
                "received_via": "telegram_goal_intake_bot",
                "chat_id": chat_id,
                "source_text": text,
                "source_update_id": update_id,
            }
            self.ledger.upsert("goal_intake", t["task_id"], enriched)
            # parse_founder_intent가 이미 "tasks" 도메인에 원문 없이 기록해 두었으므로,
            # 여기서 source_text/source_update_id만 병합해 원문을 함께 남긴다(R05 감사용).
            self.ledger.upsert("tasks", t["task_id"], {
                "source_text": text, "source_update_id": update_id
            })

        task_titles = "\n".join(f"- {t['title']}" for t in tasks)
        self.send_ack(
            chat_id,
            f"목표 접수 완료 ({len(tasks)}개 작업):\n{task_titles}\n\n실행 결과는 추후 별도로 안내드립니다."
        )
        return _advance_and_return(tasks)

    def poll_once(self) -> int:
        """한 번의 getUpdates 호출과 처리. 처리한 update 개수를 반환한다."""
        updates = self.fetch_updates()
        for update in updates:
            self.handle_update(update)
        return len(updates)

    def run_forever(self):
        if not self.bot_token:
            logger.critical("GOAL_INTAKE_BOT_TOKEN이 설정되지 않아 데몬을 시작할 수 없습니다.")
            return
        logger.info("텔레그램 목표 수신 데몬 시작 (전용 봇 - 기존 TELEGRAM_BOT_TOKEN/OpenClaw와 별개)")
        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"폴링 오류: {e}")
                time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    GoalIntakeDaemon().run_forever()
