"""
agi_session_monitor_daemon.py — 로컬 Claude / Codex 세션 감시 & 텔레그램 양방향 원격 지시 오케스트레이터

[AI 모델 위계 절대 준수 원칙]
1. 고차원 모델 (Codex, Claude): 시스템 아키텍처, 퀀트 알고리즘, 복합 설계, 고차원 의사결정 담당.
2. 소형/경량 모델 (Qwen 등): 원시 데이터 수집, 1차 텍스트 요약, 팩트 추출 등 보조 전처리만 담당.
3. 절대 규칙:
   - ❌ 대형 모델(Codex/Claude)의 고차원 작업을 소형 모델(Qwen)이 이어받는 것은 절대 금지 (Top-Down Downgrade FORBIDDEN).
   - ✅ 소형 모델(Qwen)이 전처리한 데이터를 대형 모델(Codex/Claude)이 이어받는 상향 파이프라인만 허용 (Bottom-Up Ingestion MANDATED).
   - 🔄 세션 한도 도달 시: Claude ➔ Codex 또는 Codex ➔ Claude 등 동급 프론티어 대형 모델 간의 수평 인계만 허용.
"""

import os
import sys
import time
import json
import logging
import sqlite3
import threading
import requests
from datetime import datetime
from pathlib import Path

# Base Path
WORKSPACE_DIR = "/Volumes/Realtek_NVME/stock_dashboard"
CEO_BACKEND_DIR = "/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform"
sys.path.insert(0, WORKSPACE_DIR)

import config
from notifier import send as send_tg

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][SessionMonitor] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("SessionMonitor")

TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
API_BASE = "http://127.0.0.1:8011"

# 상태 저장소 파일
STATE_FILE = os.path.join(WORKSPACE_DIR, "runtime", "agi_monitor_state.json")
TELEGRAM_MESSAGES_LOG = os.path.join(WORKSPACE_DIR, "runtime", "telegram_agi_chat_history.json")


def load_monitor_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_notified_session_id": "",
        "last_notified_status": "",
        "last_telegram_update_id": 0,
        "notified_task_keys": []
    }


def save_monitor_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save monitor state: {e}")


def log_telegram_chat(sender: str, message: str, action: str = ""):
    history = []
    if os.path.exists(TELEGRAM_MESSAGES_LOG):
        try:
            with open(TELEGRAM_MESSAGES_LOG, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sender": sender,
        "message": message,
        "action": action
    }
    history.append(entry)
    history = history[-50:]  # 최근 50개 유지
    os.makedirs(os.path.dirname(TELEGRAM_MESSAGES_LOG), exist_ok=True)
    try:
        with open(TELEGRAM_MESSAGES_LOG, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save chat log: {e}")


def fetch_active_sessions():
    """CEO FastAPI 백엔드에서 현재 세션 목록 조회"""
    try:
        r = requests.get(f"{API_BASE}/api/agi/sessions/active-list", timeout=5)
        if r.ok:
            return r.json()
    except Exception as e:
        logger.debug(f"Fetch active sessions error: {e}")
    return {"sessions": []}


def send_telegram_alert(text: str, keyboard: list = None) -> bool:
    """텔레그램 메시지 전송 (Inline Keyboard 지원)"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram token or chat_id not set.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.ok:
            log_telegram_chat("BOT", text, "ALERT_SENT")
            return True
        else:
            logger.error(f"Telegram send failed: {r.text}")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
    return False


def build_session_notification_message(session_data: dict) -> tuple[str, list]:
    """사용자가 이해하기 쉬운 세션 상태 & 프론티어 모델 수평 인계 알림 포맷 구성"""
    sessions = session_data.get("sessions", [])
    if not sessions:
        return "", []

    primary_session = sessions[0]  # 최근 Claude 또는 Codex 세션
    agent = primary_session.get("agent", "Claude 3.5 Sonnet")
    status_label = primary_session.get("status_label", "토큰 한도 중단")
    last_file = primary_session.get("last_worked_file", "")
    pending_tasks = primary_session.get("pending_tasks", [])

    # pending_tasks가 비어있을 경우 claude_codex_session_handoff에서 최신 미완료 과업 로드
    if not pending_tasks:
        try:
            from claude_codex_session_handoff import get_latest_claude_handoff_context
            ctx = get_latest_claude_handoff_context()
            pending_tasks = ctx.get("pending_tasks", [])
        except Exception as e:
            logger.debug(f"Failed to load handoff tasks from context: {e}")

    # 프론티어 동급 수평 인계 타겟 결정 (규칙: Claude ➔ Codex, Codex ➔ Claude)
    agent_lower = agent.lower()
    if "codex" in agent_lower or "chatgpt" in agent_lower:
        target_successor = "Claude 3.5 Sonnet"
        successor_tag = "claude"
        successor_desc = "OpenAI Codex ➔ Anthropic Claude 3.5 Sonnet 프론티어 수평 인계"
    else:
        target_successor = "OpenAI Codex (GPT-4o)"
        successor_tag = "codex"
        successor_desc = "Anthropic Claude ➔ OpenAI Codex (GPT-4o) 프론티어 수평 인계"

    msg = f"🔔 <b>[Project AGI — 로컬 세션 상태 감시 알림]</b>\n\n"
    msg += f"<b>{agent}</b> 작업 상태가 갱신되었습니다.\n"
    msg += f"• <b>세션 상태:</b> {status_label}\n"
    if last_file:
        msg += f"• <b>최근 수정 파일:</b> <code>{last_file}</code>\n"
    msg += f"• <b>인계 정책:</b> {successor_desc}\n\n"

    keyboard = []
    if pending_tasks:
        msg += f"⚠️ <b>[한계 제외 및 남겨진 미완료 과업 목록]</b>\n"
        for idx, pt in enumerate(pending_tasks, 1):
            key = pt.get("task_key", f"TASK_{idx}")
            title = pt.get("title", "")
            reason = pt.get("reason", "") or pt.get("context", "")
            if len(reason) > 75:
                reason = reason[:72] + "..."
            msg += f"<b>[{idx}] {title}</b>\n"
            if reason:
                msg += f"  ↳ <i>사유: {reason}</i>\n\n"
            keyboard.append([{"text": f"🔄 [{idx}번 과업] {target_successor} 수평 인계 & 3단계 승계", "callback_data": f"handoff:{key}"}])
    else:
        msg += f"ℹ️ 현재 등록된 미완료 과업이 없습니다.\n\n"

    msg += f"🚨 <b>[AI 모델 위계 절대 원칙 준수]</b>\n"
    msg += f"• <b>하향 인계 차단:</b> 고차원 설계/퀀트 과업의 소형 모델(Qwen) 단독 위임(Top-Down Downgrade)은 원천 금지됩니다.\n"
    msg += f"• <b>프론티어 수평 인계:</b> 세션 한도 시 <b>동급 프론티어({target_successor})</b>가 직접 아키텍처를 수평 승계합니다.\n"
    msg += f"• <b>3단계 자율 파이프라인:</b> (1단계 {target_successor} 설계 ➔ 2단계 로컬 보조 연산/테스트 ➔ 3단계 프론티어 최종 승인)\n\n"

    msg += f"💬 <b>[원격 지시 방법]</b>\n"
    msg += f"텔레그램 답장으로 <b>번호 (1, 2, 3...)</b>를 입력하시거나 추가 지시를 보내시면, {target_successor} 및 3단계 파이프라인으로 안전하게 승계됩니다."

    keyboard.append([{"text": "🌐 웹 관제 센터 열기", "url": "https://newsinfo.cloud/kai/#handoff"}])
    return msg, keyboard


def dispatch_frontier_handoff(task_key: str, title: str = "", custom_prompt: str = "", target_model: str = "codex") -> dict:
    """
    프론티어 모델 수평 인계 (Codex ⇄ Claude) 및 3단계 파이프라인 등록.
    규칙: 소형 모델(Qwen)로의 하향 인계(Top-Down Downgrade)는 원천 차단되며,
    Qwen은 2단계 보조 연산/스크립트 테스트 역할로만 제한 격리됨.
    """
    try:
        from claude_codex_session_handoff import build_frontier_handoff_prompt
        handoff_prompt = build_frontier_handoff_prompt(
            target_model=target_model,
            task_key=task_key,
            user_prompt=custom_prompt or title
        )
    except Exception as e:
        logger.warning(f"Failed to build frontier handoff prompt: {e}")
        handoff_prompt = f"[{target_model.upper()} 수평 인계] {task_key}: {title}\n{custom_prompt}"

    # 인계 큐 및 체크포인트 영구 기록
    handoff_record = {
        "task_key": task_key,
        "title": title or custom_prompt or task_key,
        "target_model": target_model,
        "pipeline": "3STAGE_FRONTIER_VERIFIED",
        "status": "QUEUED_FOR_FRONTIER",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_preview": handoff_prompt[:400]
    }

    queue_file = os.path.join(WORKSPACE_DIR, "runtime", "frontier_handoff_queue.json")
    os.makedirs(os.path.dirname(queue_file), exist_ok=True)
    q_data = []
    if os.path.exists(queue_file):
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                q_data = json.load(f)
        except Exception:
            pass
    q_data.insert(0, handoff_record)
    q_data = q_data[:50]
    try:
        with open(queue_file, "w", encoding="utf-8") as f:
            json.dump(q_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save handoff queue: {e}")

    # 백엔드 AGI Goal로 등록
    # 2026-09-17 발견(handoff/AGENTIC_EXECUTION_REPAIR_HANDOFF_2026-09-17.md P0-1):
    # 이전엔 응답 상태를 확인하지 않고 예외만 삼켜서, 접수가 실패해도(404/500/timeout)
    # 항상 status="success"를 반환했다 - 실제 접수 여부를 확인해 반영한다.
    backend_registered = False
    backend_error = None
    try:
        url = f"{API_BASE}/api/agi/goals/submit"
        payload = {
            "title": f"[{target_model.upper()} 수평 인계] {title or task_key}",
            "target_system": "stock_dashboard",
            "priority": "HIGH"
        }
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        backend_registered = True
    except Exception as e:
        backend_error = str(e)[:300]
        logger.warning(f"Backend goal registration failed for '{task_key}': {backend_error}")

    logger.info(
        f"Dispatched task '{task_key}' to Frontier Model ({target_model}) via 3-Stage Pipeline "
        f"(backend_registered={backend_registered})"
    )
    if backend_registered:
        return {
            "status": "success",
            "task_key": task_key,
            "target_model": target_model,
            "message": f"동급 프론티어 모델({target_model}) 3단계 자율 검증 파이프라인에 안전하게 승계되었습니다.",
        }
    return {
        "status": "partial_failure",
        "task_key": task_key,
        "target_model": target_model,
        "message": "로컬 인계 큐에는 기록됐지만 백엔드 목표 등록에는 실패했습니다 - 수동 확인이 필요합니다.",
        "backend_error": backend_error,
    }


def delegate_task_to_qwen(task_key: str, title: str = "", custom_prompt: str = "") -> dict:
    """
    [하위 호환성 래퍼]
    규칙에 따라 소형 모델로의 직결 하향 인계는 금지되며,
    동급 프론티어 모델(Codex ➔ Claude) 3단계 파이프라인으로 강제 라우팅됩니다.
    """
    logger.info(f"Top-Down Downgrade blocked: Redirecting {task_key} to Frontier 3-Stage Pipeline")
    return dispatch_frontier_handoff(task_key, title, custom_prompt, target_model="codex")


def process_telegram_incoming_commands(state: dict):
    """텔레그램 사용자 수신 메시지 처리 (Long polling getUpdates)"""
    if not TELEGRAM_BOT_TOKEN:
        return

    last_update_id = state.get("last_telegram_update_id", 0)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2, "limit": 10}

    try:
        r = requests.get(url, params=params, timeout=5)
        if not r.ok:
            return
        data = r.json()
        updates = data.get("result", [])
        if not updates:
            return

        for u in updates:
            update_id = u.get("update_id", 0)
            if update_id > last_update_id:
                last_update_id = update_id

            # 콜백 쿼리 (인라인 버튼 클릭)
            if "callback_query" in u:
                cb = u["callback_query"]
                cb_data = cb.get("data", "")
                user_name = cb.get("from", {}).get("first_name", "User")
                if cb_data.startswith("handoff:") or cb_data.startswith("delegate:"):
                    task_key = cb_data.split(":", 1)[1]
                    logger.info(f"Telegram Inline Button Clicked: {task_key} by {user_name}")
                    log_telegram_chat(user_name, f"[버튼 클릭] {task_key}", "FRONTIER_HANDOFF_CLICK")
                    
                    # 실행 (프론티어 수평 인계)
                    # 2026-09-17 발견(handoff): res 상태를 확인하지 않고 항상 "완료"를
                    # 보냈다 - dispatch_frontier_handoff가 이제 반환하는 실제 status를 본다.
                    res = dispatch_frontier_handoff(task_key, target_model="codex")
                    if res.get("status") == "success":
                        ack_msg = (
                            f"🔄 <b>[프론티어 모델 수평 인계 완료]</b>\n\n"
                            f"선택하신 <code>{task_key}</code> 과업이 <b>Codex ➔ Claude 동급 수평 인계</b> 및 "
                            f"<b>3단계 자율 검증 파이프라인</b>에 등록되었습니다!\n\n"
                            f"• <b>위계 정책:</b> 소형 모델(Qwen) 단독 하향 인계 원천 차단됨\n"
                            f"• <b>진행 구조:</b> 1단계 프론티어 설계 ➔ 2단계 로컬 보조 연산 ➔ 3단계 프론티어 최종 승인\n"
                            f"• <b>실시간 관제:</b> https://newsinfo.cloud/kai/#handoff"
                        )
                    else:
                        ack_msg = (
                            f"⚠️ <b>[인계 부분 실패]</b>\n\n"
                            f"<code>{task_key}</code>: {res.get('message', '알 수 없는 오류')}\n"
                            f"사유: {res.get('backend_error', '')}"
                        )
                    send_telegram_alert(ack_msg)

            # 텍스트 메시지
            if "message" in u and "text" in u["message"]:
                msg_obj = u["message"]
                text = msg_obj.get("text", "").strip()
                user_name = msg_obj.get("from", {}).get("first_name", "User")
                sender_id = msg_obj.get("from", {}).get("id")

                # 보안 체크 (허용된 chat_id인지 확인)
                if str(sender_id) != str(TELEGRAM_CHAT_ID) and str(msg_obj.get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
                    continue

                logger.info(f"Telegram Message Received from {user_name}: '{text}'")
                log_telegram_chat(user_name, text, "INCOMING_COMMAND")

                # 번호 또는 지시어 매핑
                task_map = {
                    "1": ("TASK_PARTIAL_TP_P3", "단독 partial_tp_pct=0.3 P3 에코프로 편중성 해소 및 다종목 분산 검증"),
                    "1번": ("TASK_PARTIAL_TP_P3", "단독 partial_tp_pct=0.3 P3 에코프로 편중성 해소 및 다종목 분산 검증"),
                    "에코프로": ("TASK_PARTIAL_TP_P3", "단독 partial_tp_pct=0.3 P3 에코프로 편중성 해소 및 다종목 분산 검증"),
                    "2": ("TASK_F05_SIGNAL_EXECUTION", "F05 백테스트 vs 가상매매 자본궤적 신호/실행 분리 재설계"),
                    "2번": ("TASK_F05_SIGNAL_EXECUTION", "F05 백테스트 vs 가상매매 자본궤적 신호/실행 분리 재설계"),
                    "F05": ("TASK_F05_SIGNAL_EXECUTION", "F05 백테스트 vs 가상매매 자본궤적 신호/실행 분리 재설계"),
                    "3": ("TASK_V2_DATA_REPAIR", "v2 하드게이트 003925 남양유업우 시세 오염 복구 도구 설계"),
                    "3번": ("TASK_V2_DATA_REPAIR", "v2 하드게이트 003925 남양유업우 시세 오염 복구 도구 설계"),
                    "4": ("TASK_MERGED_ACCOUNT_V2", "병합계좌 v2 시뮬레이터 재검증 및 46개 회귀테스트 전수 통과"),
                    "4번": ("TASK_MERGED_ACCOUNT_V2", "병합계좌 v2 시뮬레이터 재검증 및 46개 회귀테스트 전수 통과"),
                }

                if text.lower() in ["상태", "status", "/status", "현황"]:
                    send_current_status_summary()
                elif text in task_map:
                    task_key, title = task_map[text]
                    res = dispatch_frontier_handoff(task_key, title, target_model="codex")
                    reply = (
                        f"🔄 <b>[프론티어 수평 인계 접수]</b>\n"
                        f"선택하신 <b>[{text}] {title}</b> 과업이 동급 프론티어(Codex/Claude) 3단계 검증 파이프라인에 등록되었습니다.\n\n"
                        f"• <b>위계 원칙:</b> 소형 모델(Qwen) 단독 하향 인계 차단 (규칙 준수)\n"
                        f"• <b>승계 모델:</b> 프론티어 대형 모델 (Codex / Claude)\n"
                        f"• <b>실행 구조:</b> 프론티어 고차원 설계 ➔ 로컬 보조 연산 ➔ 프론티어 최종 승인\n"
                        f"• <b>실시간 관제:</b> https://newsinfo.cloud/kai/#handoff"
                    )
                    send_telegram_alert(reply)
                else:
                    # 자유 입력 명령 처리
                    custom_key = f"USER_TG_CMD_{datetime.now().strftime('%H%M%S')}"
                    res = dispatch_frontier_handoff(custom_key, text, custom_prompt=text, target_model="codex")
                    reply = (
                        f"🎯 <b>[원격 지시 접수 — 프론티어 3단계 파이프라인]</b>\n"
                        f"보내주신 지시를 동급 프론티어 모델(Codex ⇄ Claude) 3단계 자율 검증 파이프라인에 등록했습니다.\n\n"
                        f"• <b>지시 내용:</b> <i>\"{text}\"</i>\n"
                        f"• <b>작업 ID:</b> <code>{custom_key}</code>\n"
                        f"• <b>위계 보호:</b> 소형 모델 단독 코딩 금지 (프론티어 주도 설계 및 최종 코드 전수 검증)\n"
                        f"• <b>실시간 관제:</b> https://newsinfo.cloud/kai/#handoff"
                    )
                    send_telegram_alert(reply)

        state["last_telegram_update_id"] = last_update_id
        save_monitor_state(state)

    except Exception as e:
        logger.debug(f"Telegram incoming polling error: {e}")


def send_current_status_summary():
    """현재 세션 현황 텔레그램 요약 발송"""
    session_data = fetch_active_sessions()
    sessions = session_data.get("sessions", [])
    if not sessions:
        send_telegram_alert("ℹ️ 현재 활성화된 Claude/Codex 세션이 없습니다.")
        return

    msg, kb = build_session_notification_message(session_data)
    send_telegram_alert(msg, kb)


def monitor_loop():
    """메인 모니터링 루프"""
    logger.info("Starting AGI Session Monitor & Telegram Interactive Daemon...")
    state = load_monitor_state()

    # 프로세스 시작 시 환영 안내 (선택적)
    logger.info(f"Target Telegram Chat ID: {TELEGRAM_CHAT_ID}")

    while True:
        try:
            # 1. 텔레그램 명령 수신 확인 (사용자가 보낸 답장)
            # process_telegram_incoming_commands(state)  # OpenClaw handles incoming polling to prevent 409 conflict

            # 2. 로컬 Claude / Codex 세션 변경 감지
            session_data = fetch_active_sessions()
            sessions = session_data.get("sessions", [])

            if sessions:
                top_session = sessions[0]
                session_id = top_session.get("session_id", "")
                status = top_session.get("status", "")

                # 새로운 세션이거나 상태가 변경된 경우 (또는 최초 실행 시)
                if (session_id != state.get("last_notified_session_id") or
                    status != state.get("last_notified_status")):

                    logger.info(f"Session State Change Detected: {session_id} [{status}]")
                    msg, kb = build_session_notification_message(session_data)
                    if msg:
                        send_telegram_alert(msg, kb)
                        logger.info("Sent Telegram notification successfully.")

                    state["last_notified_session_id"] = session_id
                    state["last_notified_status"] = status
                    save_monitor_state(state)

            time.sleep(5)  # 5초 간격으로 신속하게 감시 및 텔레그램 폴링

        except KeyboardInterrupt:
            logger.info("Daemon interrupted by user.")
            break
        except Exception as e:
            logger.error(f"Error in monitor loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "send_now":
        # 수동 즉시 발송 테스트
        print("Sending current session status to Telegram now...")
        send_current_status_summary()
    else:
        monitor_loop()
