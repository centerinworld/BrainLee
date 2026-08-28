from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def load_project_env(env_file: Path | None = None) -> None:
    """Load project .env values without triggering config.py required-key checks."""
    path = env_file or BASE_DIR / ".env"
    if not path.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass

    with open(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_env(key: str, default: str = "") -> str:
    load_project_env()
    return os.getenv(key, default)


def require_env(key: str) -> str:
    value = get_env(key)
    if not value:
        raise EnvironmentError(
            f"필수 환경변수 '{key}'가 없습니다. {BASE_DIR / '.env'} 또는 시스템 환경변수에 설정하세요."
        )
    return value
