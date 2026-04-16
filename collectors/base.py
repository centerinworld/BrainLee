"""
collectors/base.py — 공통 기반 클래스

모든 수집기가 상속하는 BaseCollector:
  · RateLimiter   : 소스별 초당 호출 제한 (asyncio 기반)
  · CircuitBreaker: 연속 실패 시 자동 차단 → 복구 대기
  · _get / _post  : retry + circuit-breaker가 적용된 httpx async 래퍼
  · _sync_get     : 동기 요청 래퍼 (OpenDartReader 등 동기 라이브러리 전용)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# RateLimiter
# ══════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    asyncio 기반 레이트 리밋.
    acquire() 호출 사이에 min_interval_secs 이상을 보장.
    """

    def __init__(self, min_interval_secs: float):
        self._min_interval = min_interval_secs
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now     = asyncio.get_event_loop().time()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = asyncio.get_event_loop().time()

    def acquire_sync(self) -> None:
        """동기 환경(스레드)에서 사용하는 레이트 리밋."""
        now     = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


# ══════════════════════════════════════════════════════════════════
# CircuitBreaker
# ══════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    3-상태 서킷브레이커: CLOSED → OPEN → HALF-OPEN → CLOSED
      CLOSED   : 정상 동작
      OPEN     : failure_threshold 연속 실패 → recovery_secs 동안 호출 차단
      HALF-OPEN: 복구 대기 후 다음 1회 허용, 성공 시 CLOSED 복귀
    """

    _CLOSED    = "CLOSED"
    _OPEN      = "OPEN"
    _HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        failure_threshold: int = None,
        recovery_secs: int = None,
        name: str = "unnamed",
    ):
        self._threshold = failure_threshold or config.CB_FAILURE_THRESHOLD
        self._recovery  = recovery_secs    or config.CB_RECOVERY_SECS
        self._name      = name
        self._state     = self._CLOSED
        self._failures  = 0
        self._opened_at: float = 0.0

    def is_open(self) -> bool:
        """True → 호출 거부해야 함."""
        if self._state == self._OPEN:
            if time.monotonic() - self._opened_at >= self._recovery:
                self._state = self._HALF_OPEN
                logger.info(f"[CB:{self._name}] HALF-OPEN — 테스트 호출 허용")
                return False
            return True
        return False

    def record_success(self) -> None:
        if self._state in (self._OPEN, self._HALF_OPEN):
            logger.info(f"[CB:{self._name}] 복구 완료 → CLOSED")
        self._state    = self._CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == self._HALF_OPEN or self._failures >= self._threshold:
            self._state     = self._OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                f"[CB:{self._name}] OPEN — {self._failures}회 실패, "
                f"{self._recovery}초 차단"
            )


# ══════════════════════════════════════════════════════════════════
# BaseCollector
# ══════════════════════════════════════════════════════════════════

class BaseCollector:
    """
    모든 수집기의 기반 클래스.

    서브클래스에서 초기화 예시:
        super().__init__(
            rate_limit_secs = config.KIS_RATE_LIMIT_SECS,
            name            = "KIS",
        )
    """

    def __init__(
        self,
        rate_limit_secs: float = 0.0,
        name: str = "collector",
    ):
        self.rate_limiter    = RateLimiter(rate_limit_secs) if rate_limit_secs > 0 else None
        self.circuit_breaker = CircuitBreaker(name=name)
        self._name           = name

        # 공유 async 클라이언트 (서브클래스가 직접 접근하지 말고 _get/_post 사용)
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout   = httpx.Timeout(10.0, connect=5.0),
                limits    = httpx.Limits(max_connections=20, max_keepalive_connections=10),
                follow_redirects = True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── async GET ────────────────────────────────────────────────

    async def _get(
        self,
        url: str,
        params:  dict | None = None,
        headers: dict | None = None,
        timeout: int = 10,
    ) -> dict | None:
        return await self._request("GET", url, params=params, headers=headers, timeout=timeout)

    # ── async POST ───────────────────────────────────────────────

    async def _post(
        self,
        url: str,
        data:    dict | None = None,
        json:    dict | None = None,
        headers: dict | None = None,
        timeout: int = 10,
    ) -> dict | None:
        return await self._request(
            "POST", url, data=data, json=json, headers=headers, timeout=timeout
        )

    # ── 핵심 요청 메서드 (retry + circuit-breaker) ───────────────

    async def _request(
        self,
        method:  str,
        url:     str,
        params:  dict | None = None,
        data:    dict | None = None,
        json:    Any         = None,
        headers: dict | None = None,
        timeout: int = 10,
    ) -> dict | None:
        if self.circuit_breaker.is_open():
            logger.debug(f"[{self._name}] CB OPEN — 요청 거부: {url}")
            return None

        max_attempts = config.RETRY_MAX_ATTEMPTS
        backoff_base = config.RETRY_BACKOFF_BASE

        for attempt in range(1, max_attempts + 1):
            if self.rate_limiter:
                await self.rate_limiter.acquire()

            try:
                client = await self._ensure_client()
                resp   = await client.request(
                    method,
                    url,
                    params  = params,
                    data    = data,
                    json    = json,
                    headers = headers,
                    timeout = timeout,
                )

                # 서버 오류 (5xx) → 재시도
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )

                # 레이트리밋 (429) → 백오프 후 재시도
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff_base ** attempt))
                    logger.warning(f"[{self._name}] 429 Too Many Requests — {retry_after:.1f}초 대기")
                    await asyncio.sleep(retry_after)
                    continue

                self.circuit_breaker.record_success()
                try:
                    return resp.json()
                except Exception:
                    return {"_raw": resp.text, "_status": resp.status_code}

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning(f"[{self._name}] 네트워크 오류 (시도 {attempt}/{max_attempts}): {exc}")
            except httpx.HTTPStatusError as exc:
                logger.warning(f"[{self._name}] HTTP {exc.response.status_code} (시도 {attempt}/{max_attempts}): {url}")
            except Exception as exc:
                logger.error(f"[{self._name}] 예외 (시도 {attempt}/{max_attempts}): {exc}")

            if attempt < max_attempts:
                wait = backoff_base ** attempt + random.uniform(0, 0.5)
                logger.debug(f"[{self._name}] {wait:.2f}초 후 재시도")
                await asyncio.sleep(wait)

        self.circuit_breaker.record_failure()
        return None

    # ── 동기 GET (OpenDartReader 등 동기 라이브러리 전용) ─────────

    def _sync_get(
        self,
        url:     str,
        params:  dict | None = None,
        headers: dict | None = None,
        timeout: int = 10,
    ) -> dict | None:
        """동기 HTTP GET — 동기 컨텍스트(스레드)에서만 사용."""
        if self.circuit_breaker.is_open():
            return None
        if self.rate_limiter:
            self.rate_limiter.acquire_sync()

        import requests as _req
        max_attempts = config.RETRY_MAX_ATTEMPTS
        backoff_base = config.RETRY_BACKOFF_BASE

        for attempt in range(1, max_attempts + 1):
            try:
                resp = _req.get(url, params=params, headers=headers, timeout=timeout)
                if resp.status_code >= 500:
                    raise _req.exceptions.HTTPError(f"HTTP {resp.status_code}")
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", backoff_base ** attempt))
                    time.sleep(wait)
                    continue
                self.circuit_breaker.record_success()
                return resp.json()
            except Exception as exc:
                logger.warning(f"[{self._name}] sync 오류 (시도 {attempt}/{max_attempts}): {exc}")
                if attempt < max_attempts:
                    time.sleep(backoff_base ** attempt + random.uniform(0, 0.5))

        self.circuit_breaker.record_failure()
        return None
