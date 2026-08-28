from __future__ import annotations

import json
import time
from typing import Any

from env_utils import BASE_DIR

_PROFILE_PATH = BASE_DIR / "config" / "financial_profiles.json"
_MISMATCH_PATH = BASE_DIR / "config" / "financial_profiles_pending.json"

# mtime 기반 캐싱 — 파일 변경 시 자동 무효화 (배치에서 매번 디스크 I/O 방지)
_cache: dict[str, Any] = {"data": None, "mtime": 0.0}


def load_profiles() -> dict[str, dict[str, Any]]:
    """JSON에서 프로파일 로드 (mtime 기반 캐시)."""
    if not _PROFILE_PATH.exists():
        return {}
    try:
        mtime = _PROFILE_PATH.stat().st_mtime
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        with _PROFILE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _cache["data"] = data
            _cache["mtime"] = mtime
            return data
    except Exception:
        pass
    return {}


def get_profile(stock_code: str) -> dict[str, Any]:
    return load_profiles().get(stock_code, {})


def append_pending_mismatch(stock_code: str, reason: str, details: dict[str, Any]) -> None:
    """
    검증에서 불일치가 발견된 종목을 _pending.json에 자동 누적.
    사람이 검토 후 financial_profiles.json으로 이관.

    같은 (stock_code, reason)은 카운트만 증가 + last_seen 갱신.
    """
    pending: dict[str, Any] = {}
    if _MISMATCH_PATH.exists():
        try:
            with _MISMATCH_PATH.open("r", encoding="utf-8") as f:
                pending = json.load(f)
        except Exception:
            pending = {}

    entry = pending.setdefault(stock_code, {})
    prev = entry.get(reason, {})
    entry[reason] = {
        "details":    details,
        "first_seen": prev.get("first_seen") or time.strftime("%Y-%m-%d"),
        "last_seen":  time.strftime("%Y-%m-%d"),
        "count":      (prev.get("count") or 0) + 1,
    }

    _MISMATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _MISMATCH_PATH.open("w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2, sort_keys=True)


def list_pending_mismatches() -> dict[str, Any]:
    """검토 대기 중인 불일치 종목 전체."""
    if not _MISMATCH_PATH.exists():
        return {}
    try:
        with _MISMATCH_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
