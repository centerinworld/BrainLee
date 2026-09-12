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
"""

import os
import time
import logging
from typing import Any, Dict, List, Optional

import requests

from agents.l1_pm_owner import L1PMOwner
from memory.state_ledger import StateLedger

logger = logging.getLogger("goal_intake_daemon")

POLL_TIMEOUT_SECONDS = 25  # 텔레그램 long-poll timeout


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
        self._offset = 0

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
        """단일 update를 처리한다. 접수된 태스크 목록(0개 이상)을 반환하거나, 무시했으면 None."""
        self._offset = max(self._offset, update.get("update_id", 0) + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = message.get("text", "")

        if not text:
            return None

        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            logger.warning(f"허용되지 않은 chat_id({chat_id})의 메시지 무시: '{text[:40]}'")
            return None

        tasks = self.pm_owner.parse_founder_intent(text)
        if not tasks:
            self.send_ack(chat_id, "요청을 이해하지 못했습니다 (주식/방산 도메인으로 분류되지 않음). 다시 말씀해주세요.")
            return []

        for t in tasks:
            self.ledger.upsert(
                "goal_intake", t["task_id"],
                {**t, "received_via": "telegram_goal_intake_bot", "chat_id": chat_id}
            )

        task_titles = "\n".join(f"- {t['title']}" for t in tasks)
        self.send_ack(
            chat_id,
            f"목표 접수 완료 ({len(tasks)}개 작업):\n{task_titles}\n\n실행 결과는 추후 별도로 안내드립니다."
        )
        return tasks

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
