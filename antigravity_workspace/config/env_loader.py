"""
Project Antigravity: 통합 환경 변수 및 API Key 로더
단일 진실 공급원(Single Source of Truth): /Volumes/Realtek_NVME/stock_dashboard/.env
중복 파일 생성 없이 기존 대시보드 및 시스템 설정을 공유 및 활용합니다.
"""

import os
from pathlib import Path

def load_unified_env():
    """
    루트 stock_dashboard/.env 및 관련 기존 시스템 설정을 탐색하여
    환경 변수에 자동 로드합니다.
    """
    candidates = [
        Path("/Volumes/Realtek_NVME/stock_dashboard/.env"),
        Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/.env"),
        Path("/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/.env"),
    ]

    loaded_paths = []
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'").strip('"')
                        # 이미 설정된 환경변수가 아니거나 비어있을 때 우선 주입
                        if k not in os.environ or not os.environ[k]:
                            os.environ[k] = v
            loaded_paths.append(str(p))

    return loaded_paths

# 모듈 로드 시 자동 실행
LOADED_ENV_FILES = load_unified_env()
