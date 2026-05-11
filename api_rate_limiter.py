"""
api_rate_limiter.py — 중앙 집중식 외부 API 속도/쿼터 관리

문제 배경:
  KRX, DART, KIS, Naver, Yahoo Finance 등 여러 외부 API를 사용하는데
  각 파일에 sleep만 박혀 있어 일일 쿼터 초과 / IP 차단이 반복 발생.

해결 방법:
  1. API별 초당 요청 수(RPS) 제한 + 랜덤 jitter → 패턴 감지 방지
  2. 일일 요청 횟수 추적 → 쿼터 근접 시 자동 슬로우다운/중단
  3. 429/차단 감지 시 지수 백오프 + 당일 냉각
  4. 상태를 JSON 파일에 저장 → 서버 재시작/AI 세션 재시작 후에도 쿼터 유지

사용법:
    from api_rate_limiter import api_limiter

    # 단순 대기 (KIS 수급 루프 등)
    api_limiter.wait("KIS")

    # requests.get 래핑 (자동 재시도 + 백오프)
    resp = api_limiter.get("DART", url, params=params, timeout=15)

    # 429 / 차단 직접 신고
    api_limiter.report_block("NAVER")
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── 상태 파일 경로 ─────────────────────────────────────────────────────────────
_STATE_FILE = Path(__file__).parent / ".api_quota_state.json"

# ── API별 설정 ─────────────────────────────────────────────────────────────────
#   min_interval : 요청 간 최소 간격(초)
#   jitter       : 랜덤 추가 대기 범위(초) — [0, jitter]
#   daily_limit  : 일일 최대 요청 수 (None = 무제한)
#   warn_at      : 이 비율 초과 시 WARNING 로그 + 간격 2배
#   cooldown     : 차단 감지 후 대기 시간(초)
#   max_retries  : 429/차단 시 재시도 횟수
_API_CONFIG: dict[str, dict] = {
    "KIS": {
        "min_interval": 1.05,   # KIS 공식 제한: 초당 1건
        "jitter":       0.1,
        "daily_limit":  None,   # KIS는 일일 쿼터 미공개
        "warn_at":      0.8,
        "cooldown":     60,
        "max_retries":  3,
    },
    "DART": {
        "min_interval": 0.8,    # DART 실제 한도: 40,000건/일 (2026-05-09 확인)
        "jitter":       0.3,
        "daily_limit":  36000,  # 40,000건 한도에서 10% 여유 (36,000건)
        "warn_at":      0.75,   # 27,000건 초과 시 경고 + 슬로우다운
        "cooldown":     3600,   # 쿼터 소진 후 1시간 냉각
        "max_retries":  2,
    },
    "KRX": {
        "min_interval": 1.0,    # KRX 승인 API: 보수적 1초
        "jitter":       0.5,
        "daily_limit":  500,    # 내부 추정치
        "warn_at":      0.8,
        "cooldown":     1800,
        "max_retries":  2,
    },
    "NAVER": {
        "min_interval": 1.2,    # Naver 스크래핑: 패턴 감지 방지
        "jitter":       0.8,    # 최대 2.0초 사이 랜덤
        "daily_limit":  3000,
        "warn_at":      0.8,
        "cooldown":     1800,
        "max_retries":  1,
    },
    "FNGUIDE": {
        "min_interval": 3.0,    # FnGuide: 공격적 봇 감지, 넉넉하게
        "jitter":       1.5,
        "daily_limit":  500,
        "warn_at":      0.7,
        "cooldown":     3600,
        "max_retries":  1,
    },
    "YAHOO": {
        "min_interval": 1.5,    # Yahoo Finance: 비공개, 보수적
        "jitter":       1.0,
        "daily_limit":  2000,
        "warn_at":      0.8,
        "cooldown":     600,
        "max_retries":  2,
    },
    "PUBLIC_DATA": {
        "min_interval": 0.5,
        "jitter":       0.2,
        "daily_limit":  900,    # 공공데이터포털 1,000건/일
        "warn_at":      0.8,
        "cooldown":     3600,
        "max_retries":  2,
    },
    "STOCKEASY": {
        "min_interval": 2.0,
        "jitter":       1.0,
        "daily_limit":  200,
        "warn_at":      0.8,
        "cooldown":     1800,
        "max_retries":  1,
    },
}


class _ApiState:
    """단일 API의 런타임 상태."""
    __slots__ = ("last_call", "count_today", "today", "blocked_until", "lock")

    def __init__(self):
        self.last_call: float = 0.0
        self.count_today: int = 0
        self.today: str = date.today().isoformat()
        self.blocked_until: float = 0.0
        self.lock = threading.Lock()

    def reset_if_new_day(self):
        today = date.today().isoformat()
        if self.today != today:
            self.today = today
            self.count_today = 0
            self.blocked_until = 0.0


class ApiRateLimiter:
    """
    전체 프로젝트 공유 싱글톤 rate limiter.

    사용 패턴:
        api_limiter.wait("KIS")          # 대기 후 카운트 증가
        api_limiter.get("DART", url)     # requests.get 래핑
        api_limiter.report_block("DART") # 차단 직접 신고
    """

    def __init__(self):
        self._states: dict[str, _ApiState] = {
            name: _ApiState() for name in _API_CONFIG
        }
        self._load_state()

    # ── 상태 영속성 ──────────────────────────────────────────────────────────
    def _load_state(self):
        """서버 시작 / AI 세션 재시작 시 이전 쿼터 복원."""
        try:
            if not _STATE_FILE.exists():
                return
            data = json.loads(_STATE_FILE.read_text())
            today = date.today().isoformat()
            for name, st in data.items():
                if name not in self._states:
                    continue
                if st.get("today") == today:
                    s = self._states[name]
                    s.count_today = st.get("count_today", 0)
                    s.today = today
                    blocked = st.get("blocked_until", 0)
                    if blocked > time.time():
                        s.blocked_until = blocked
                        logger.warning(
                            f"[RateLimiter] {name} 복원: 차단 {int(blocked - time.time())}초 남음, "
                            f"일일 {s.count_today}건"
                        )
                    else:
                        logger.info(f"[RateLimiter] {name} 복원: 일일 {s.count_today}건")
        except Exception as e:
            logger.debug(f"[RateLimiter] 상태 로드 실패 (무시): {e}")

    def _save_state(self):
        """현재 쿼터 상태를 파일에 저장."""
        try:
            data = {}
            for name, s in self._states.items():
                data[name] = {
                    "today":         s.today,
                    "count_today":   s.count_today,
                    "blocked_until": s.blocked_until,
                }
            _STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"[RateLimiter] 상태 저장 실패 (무시): {e}")

    # ── 공개 API ─────────────────────────────────────────────────────────────
    def wait(self, api: str, *, extra_sleep: float = 0.0) -> bool:
        """
        다음 요청 전 필요한 만큼 대기. 쿼터 초과 시 False 반환.

        Returns:
            True  — 요청해도 됨
            False — 일일 쿼터 초과, 오늘은 스킵 권장
        """
        cfg = _API_CONFIG.get(api)
        if cfg is None:
            return True  # 미등록 API는 통과

        s = self._states[api]
        with s.lock:
            s.reset_if_new_day()

            # ① 쿼터 초과 체크
            limit = cfg["daily_limit"]
            if limit and s.count_today >= limit:
                if s.count_today == limit:  # 첫 초과 시 한 번만 로그
                    logger.error(
                        f"[RateLimiter] {api} 일일 쿼터 {limit}건 소진 — "
                        f"오늘 수집 중단 (내일 자정 리셋)"
                    )
                    s.count_today += 1  # 중복 경고 방지
                    self._save_state()
                return False

            # ② 차단 중 체크
            if s.blocked_until > time.time():
                remaining = int(s.blocked_until - time.time())
                logger.warning(f"[RateLimiter] {api} 차단 냉각 중 ({remaining}초 남음) — 스킵")
                return False

            # ③ 쿼터 경고 (warn_at 비율 초과 시 간격 2배)
            if limit:
                ratio = s.count_today / limit
                if ratio >= cfg["warn_at"]:
                    extra = cfg["min_interval"]  # 간격 2배
                    logger.warning(
                        f"[RateLimiter] {api} 쿼터 {ratio:.0%} 소진 ({s.count_today}/{limit}) "
                        f"— 간격 2배 ({cfg['min_interval'] + extra:.1f}초)"
                    )
                else:
                    extra = 0.0
            else:
                extra = 0.0

            # ④ 요청 간격 대기
            min_gap = cfg["min_interval"] + extra + extra_sleep
            jitter  = random.uniform(0, cfg["jitter"])
            elapsed = time.time() - s.last_call
            gap     = min_gap + jitter - elapsed
            if gap > 0:
                time.sleep(gap)

            s.last_call = time.time()
            s.count_today += 1

            if s.count_today % 100 == 0:
                logger.info(f"[RateLimiter] {api} 일일 {s.count_today}건")
                self._save_state()

        return True

    def get(
        self,
        api: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = 20,
        session: requests.Session | None = None,
    ) -> requests.Response | None:
        """
        requests.get 래핑 — 자동 rate limit + 재시도 + 차단 감지.

        Returns:
            Response 객체 (성공) 또는 None (쿼터 초과/차단/재시도 소진)
        """
        cfg = _API_CONFIG.get(api, {})
        max_retries = cfg.get("max_retries", 2)
        cooldown    = cfg.get("cooldown", 600)
        _get = (session.get if session else requests.get)

        for attempt in range(max_retries + 1):
            if not self.wait(api):
                return None  # 쿼터 소진

            try:
                resp = _get(url, params=params, headers=headers, timeout=timeout)

                if resp.status_code == 429:
                    self.report_block(api, cooldown=cooldown)
                    backoff = min(60 * (2 ** attempt), 600)
                    logger.warning(f"[RateLimiter] {api} 429 Too Many Requests → {backoff}초 대기 후 재시도")
                    time.sleep(backoff)
                    continue

                if resp.status_code in (403, 503):
                    logger.warning(f"[RateLimiter] {api} HTTP {resp.status_code} — 차단 의심")
                    self.report_block(api, cooldown=cooldown // 2)
                    return None

                return resp

            except requests.exceptions.Timeout:
                logger.debug(f"[RateLimiter] {api} timeout (시도 {attempt+1}/{max_retries+1})")
                time.sleep(5 * (attempt + 1))
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"[RateLimiter] {api} 연결 오류: {e}")
                time.sleep(10 * (attempt + 1))
            except Exception as e:
                logger.warning(f"[RateLimiter] {api} 예외: {e}")
                break

        return None

    def report_block(self, api: str, *, cooldown: int | None = None):
        """외부에서 차단 / 429 감지 시 직접 신고."""
        cfg = _API_CONFIG.get(api, {})
        cd  = cooldown if cooldown is not None else cfg.get("cooldown", 600)
        s   = self._states.get(api)
        if s:
            with s.lock:
                s.blocked_until = time.time() + cd
            self._save_state()
            logger.error(
                f"[RateLimiter] {api} 차단 신고 → {cd}초({cd//60}분) 냉각 시작 "
                f"(재개: {datetime.fromtimestamp(s.blocked_until).strftime('%H:%M:%S')})"
            )

    def status(self) -> dict:
        """현재 쿼터 상태 반환 (모니터링용)."""
        result = {}
        for name, s in self._states.items():
            cfg = _API_CONFIG[name]
            limit = cfg["daily_limit"]
            result[name] = {
                "count_today":   s.count_today,
                "daily_limit":   limit,
                "usage_pct":     round(s.count_today / limit * 100, 1) if limit else None,
                "blocked":       s.blocked_until > time.time(),
                "blocked_until": datetime.fromtimestamp(s.blocked_until).strftime("%H:%M:%S")
                                 if s.blocked_until > time.time() else None,
            }
        return result

    def reset(self, api: str):
        """테스트/수동 리셋용."""
        s = self._states.get(api)
        if s:
            with s.lock:
                s.count_today = 0
                s.blocked_until = 0.0
            self._save_state()


# ── 전역 싱글톤 ───────────────────────────────────────────────────────────────
api_limiter = ApiRateLimiter()


# ── 편의 함수 (기존 코드 호환) ────────────────────────────────────────────────
def kis_wait():
    """KIS API 호출 전 대기 (기존 time.sleep(1.05) 대체)."""
    return api_limiter.wait("KIS")


def dart_wait():
    """DART API 호출 전 대기."""
    return api_limiter.wait("DART")


def naver_wait():
    """Naver 스크래핑 전 대기."""
    return api_limiter.wait("NAVER")


def yahoo_wait():
    """Yahoo Finance 호출 전 대기."""
    return api_limiter.wait("YAHOO")


def krx_wait():
    """KRX API 호출 전 대기."""
    return api_limiter.wait("KRX")


def fnguide_wait():
    """FnGuide 스크래핑 전 대기."""
    return api_limiter.wait("FNGUIDE")
