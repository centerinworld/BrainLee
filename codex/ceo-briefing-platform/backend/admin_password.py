"""admin_password.py — 관리자 비밀번호 검증 (2026-09-27, 사이트 전면 개편)

Key Indicator(newsinfo) 관리 기능은 별도 아이디·PIN 대신 **stock 사이트와 같은 관리자 비밀번호(10자 이상)** 하나로 연다.
해시는 stock_dashboard/runtime/.env 의 ADMIN_PASSWORD_HASH(`pbkdf2_sha256$반복$salt$hash`)를 로그인 때마다 읽어 비교한다 —
비밀번호를 바꾸면 여기에도 바로 반영되고, 이 프로젝트에는 비밀번호나 해시를 복사해 두지 않는다. 설정이 없으면 항상 False(fail-closed).
"""
import hashlib
import hmac
import os
from pathlib import Path

STOCK_ENV = Path(os.getenv("STOCK_ENV_FILE", "/Volumes/Realtek_NVME/stock_dashboard/runtime/.env"))


def _stored_hash() -> str:
    v = os.getenv("ADMIN_PASSWORD_HASH", "")
    if v:
        return v
    try:
        for raw in STOCK_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("ADMIN_PASSWORD_HASH="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def configured() -> bool:
    return bool(_stored_hash())


def verify(password: str) -> bool:
    stored = _stored_hash()
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001 - 깨진 해시 = 인증 실패
        return False
