#!/usr/bin/env python3
"""관리자 비밀번호 설정 — .env의 ADMIN_PASSWORD_HASH를 (재)생성한다 (2026-09-27).

  cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python scripts/ops/set_admin_password.py

비밀번호는 화면에 표시되지 않는 프롬프트로 두 번 입력한다(명령행 인자·로그에 남기지 않음). 평문은 저장하지 않고 PBKDF2-SHA256 해시만 기록하며,
기존 ADMIN_PASSWORD(평문) 줄은 제거한다. 비밀번호를 바꾸면 기존 관리자 세션은 모두 무효가 된다. 적용: scripts/safe_restart_backend.sh
"""
import getpass
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from security_gate import hash_admin_password  # noqa: E402

MIN_LEN = 10


def main() -> int:
    pw = getpass.getpass("새 관리자 비밀번호(10자 이상): ")
    if len(pw) < MIN_LEN:
        print(f"너무 짧습니다(최소 {MIN_LEN}자).")
        return 1
    if getpass.getpass("한 번 더 입력: ") != pw:
        print("두 입력이 다릅니다.")
        return 1
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [ln for ln in lines if not re.match(r"\s*(ADMIN_PASSWORD_HASH|ADMIN_PASSWORD)\s*=", ln)]
    lines.append(f"ADMIN_PASSWORD_HASH={hash_admin_password(pw)}")
    tmp = env.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, env.stat().st_mode & 0o777 if env.exists() else 0o600)
    tmp.replace(env)
    print("저장했습니다. 적용하려면: bash scripts/safe_restart_backend.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
