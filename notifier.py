"""
notifier.py — 텔레그램 알림 공유 모듈

사용법:
    from notifier import send, load_history, purge_old_keys

    load_history()            # 프로세스 시작 시 1회
    send("메시지")            # 즉시 발송
    send("메시지", key="k1") # 중복 방지 (동일 key 재발송 차단)
    purge_old_keys()          # 3일 이상 된 key 정리 (선택)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, timedelta

import requests

import config

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────
_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
_SENT_FILE  = os.path.join(_BASE_DIR, "telegram_sent.json")
_API_URL    = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

# ── 내부 상태 ──────────────────────────────────────────────────
_sent: set[str] = set()
_lock           = threading.Lock()


# ── 공개 API ──────────────────────────────────────────────────

def load_history() -> None:
    """프로세스 시작 시 1회 호출 — 이전 전송 이력 복원."""
    global _sent
    try:
        with open(_SENT_FILE) as f:
            _sent = set(json.load(f))
        logger.info(f"[Notifier] 전송 이력 {len(_sent)}건 로드")
    except FileNotFoundError:
        _sent = set()
    except Exception as e:
        logger.warning(f"[Notifier] 이력 로드 실패: {e}")
        _sent = set()


def send(msg: str, key: str = "") -> bool:
    """
    텔레그램 HTML 메시지 발송.

    key가 지정되면 동일 key의 중복 발송을 차단한다.
    반환: True=발송, False=중복스킵 또는 오류
    """
    tok = config.TELEGRAM_BOT_TOKEN
    cid = config.TELEGRAM_CHAT_ID
    if not tok or not cid:
        logger.warning("[Notifier] BOT_TOKEN / CHAT_ID 미설정")
        return False

    with _lock:
        if key and key in _sent:
            logger.debug(f"[Notifier] 중복 스킵: {key}")
            return False

    try:
        chunks = [msg[i:i + 4000] for i in range(0, len(msg), 4000)] or [""]
        for chunk in chunks:
            r = requests.post(
                _API_URL,
                json={"chat_id": cid, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=10,
            )
            if not r.ok:
                logger.warning(f"[Notifier] 전송 실패 {r.status_code}: {r.text[:80]}")
                return False
    except Exception as e:
        logger.error(f"[Notifier] 오류: {e}")
        return False

    logger.info(f"[Notifier] ✅ {msg[:60].rstrip()}")
    if key:
        with _lock:
            _sent.add(key)
        _persist()
    return True


def purge_old_keys(days: int = 3) -> None:
    """days일 이상 된 dedup key 제거 (파일 무한 증가 방지)."""
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _lock:
        before = len(_sent)
        _sent.difference_update(
            k for k in list(_sent)
            if "_20" in k and k.split("_")[-1] < cutoff
        )
        removed = before - len(_sent)
    if removed:
        _persist()
        logger.info(f"[Notifier] 오래된 key {removed}건 정리")


# ── 내부 헬퍼 ─────────────────────────────────────────────────

def _persist() -> None:
    try:
        with _lock:
            data = list(_sent)
        with open(_SENT_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"[Notifier] 이력 저장 실패: {e}")
