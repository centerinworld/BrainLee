"""
DART API key rotation helpers.

Keep secrets out of source code. Keys are read from config/.env only:
    DART_API_KEY, DART_API_KEY2, DART_API_KEY3
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

_DART_QUOTA_MARKERS = ("020", "사용한도", "한도", "quota", "limit")


def get_dart_api_keys() -> list[str]:
    """Return configured unique DART keys in priority order."""
    keys: list[str] = []
    for key in (config.DART_API_KEY, config.DART_API_KEY2, config.DART_API_KEY3):
        if key and key not in keys:
            keys.append(key)
    return keys


def masked_key_label(index: int, key: str) -> str:
    """Non-secret key label for logs."""
    suffix = key[-4:] if key else "none"
    return f"KEY{index + 1}(...{suffix})"


def is_quota_error(value: Any) -> bool:
    """Best-effort quota detection across DART JSON, text, exceptions, and DataFrames."""
    if value is None:
        return False
    if value.__class__.__name__ == "DataFrame":
        return False
    if isinstance(value, dict):
        status = str(value.get("status", ""))
        msg = str(value.get("message", ""))
        return status == "020" or any(marker.lower() in msg.lower() for marker in _DART_QUOTA_MARKERS)
    if not isinstance(value, (str, bytes, Exception)):
        return False
    text = str(value)
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in _DART_QUOTA_MARKERS)


class RotatingOpenDartReader:
    """Small proxy around OpenDartReader that retries the next key on quota errors."""

    def __init__(self, keys: list[str] | None = None):
        self.keys = keys or get_dart_api_keys()
        if not self.keys:
            raise RuntimeError("DART API keys not configured")
        import OpenDartReader

        self._readers = [OpenDartReader.OpenDartReader(k) if hasattr(OpenDartReader, "OpenDartReader") else OpenDartReader(k) for k in self.keys]
        self._idx = 0
        self._exhausted: set[int] = set()

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        last_err: Exception | None = None
        for _ in range(len(self._readers)):
            idx = self._idx % len(self._readers)
            self._idx += 1
            if idx in self._exhausted:
                continue
            reader = self._readers[idx]
            try:
                # 2026-08-30 발견: OpenDartReader.finstate_all()은 DART가 status!='000'을
                # 반환해도(쿼터소진 포함) 예외를 던지거나 에러값을 리턴하지 않고 그냥
                # `print(jo)` 후 빈 DataFrame을 반환한다(venv/.../OpenDartReader/
                # dart_finstate.py:64-69) — 그 결과 아래 is_quota_error(result)가 빈
                # DataFrame을 보고도 False를 반환해(DataFrame은 무조건 False 처리) 로테이션이
                # 전혀 발동하지 않고 있었다(실측: KEY1 쿼터소진 상태에서 계속 KEY1만 재시도).
                # stdout으로 인쇄되는 원본 에러 JSON을 캡처해 그 텍스트에서 쿼터 마커를
                # 찾아내는 방식으로 우회 탐지한다.
                import io, contextlib
                _buf = io.StringIO()
                with contextlib.redirect_stdout(_buf):
                    result = getattr(reader, method_name)(*args, **kwargs)
                _captured = _buf.getvalue()
                if _captured and is_quota_error(_captured):
                    self._exhausted.add(idx)
                    logger.warning("[DART] %s quota exhausted (stdout capture); trying next key", masked_key_label(idx, self.keys[idx]))
                    continue
                if is_quota_error(result):
                    self._exhausted.add(idx)
                    logger.warning("[DART] %s quota exhausted; trying next key", masked_key_label(idx, self.keys[idx]))
                    continue
                return result
            except Exception as exc:
                last_err = exc
                if is_quota_error(exc):
                    self._exhausted.add(idx)
                    logger.warning("[DART] %s quota exhausted; trying next key", masked_key_label(idx, self.keys[idx]))
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("All configured DART API keys are exhausted")

    def _get_property(self, property_name: str) -> Any:
        last_err: Exception | None = None
        for _ in range(len(self._readers)):
            idx = self._idx % len(self._readers)
            self._idx += 1
            if idx in self._exhausted:
                continue
            reader = self._readers[idx]
            try:
                result = getattr(reader, property_name)
                if is_quota_error(result):
                    self._exhausted.add(idx)
                    logger.warning("[DART] %s quota exhausted; trying next key", masked_key_label(idx, self.keys[idx]))
                    continue
                return result
            except Exception as exc:
                last_err = exc
                if is_quota_error(exc):
                    self._exhausted.add(idx)
                    logger.warning("[DART] %s quota exhausted; trying next key", masked_key_label(idx, self.keys[idx]))
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("All configured DART API keys are exhausted")

    def __getattr__(self, method_name: str) -> Callable[..., Any]:
        probe = getattr(self._readers[self._idx % len(self._readers)], method_name)
        if not callable(probe):
            return self._get_property(method_name)

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            return self._call(method_name, *args, **kwargs)

        return _wrapped


class DartKeyRotator:
    """Rotator for direct requests-based DART collectors."""

    def __init__(self, keys: list[str] | None = None):
        self.keys = keys or get_dart_api_keys()
        if not self.keys:
            raise RuntimeError("DART API keys not configured")
        self.idx = 0
        self.exhausted: set[int] = set()

    def next_key(self) -> str | None:
        for _ in range(len(self.keys)):
            idx = self.idx % len(self.keys)
            self.idx += 1
            if idx not in self.exhausted:
                return self.keys[idx]
        return None

    def mark_exhausted(self, key: str) -> None:
        try:
            idx = self.keys.index(key)
        except ValueError:
            return
        self.exhausted.add(idx)
        logger.warning("[DART] %s quota exhausted", masked_key_label(idx, key))

    def sleep(self, seconds: float = 0.8) -> None:
        time.sleep(seconds)
