from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
ROOT_DIR = PROJECT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
STATIC_DIR = PROJECT_DIR / "static"
LOCAL_ENV = PROJECT_DIR / ".env"
ROOT_ENV = ROOT_DIR / ".env"
ROOT_STOCK_DB = ROOT_DIR / "stock.db"
LOCAL_DB = DATA_DIR / "hs_trade_lab.db"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


for env_path in (ROOT_ENV, LOCAL_ENV):
    _load_env_file(env_path)

