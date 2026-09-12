"""
Project Antigravity: Remote Bridge API Server (FastAPI)
https://newsinfo.cloud/kai/ 및 외부 클라우드에서 Antigravity OS로 원격 지시 및 상태 제어
"""

import os
import sys
import hmac
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("bridge_api")

# Workspace path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from agents.l1_pm_owner import L1PMOwner
from agents.l2_workers.quant_trader import QuantTraderWorker
from agents.l2_workers.defense_researcher import DefenseResearcherWorker

app = FastAPI(
    title="Antigravity Remote Bridge API",
    description="Bridge connecting newsinfo.cloud/kai and Streamlit localhost:8501",
    version="2.0.0"
)

# CORS 설정: 기본은 newsinfo.cloud만 허용. ANTIGRAVITY_ALLOWED_ORIGINS(쉼표구분)로 재정의 가능.
_allowed_origins = [
    o.strip() for o in os.getenv(
        "ANTIGRAVITY_ALLOWED_ORIGINS",
        "https://newsinfo.cloud,http://localhost:5500,http://localhost:8501"
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 원격 자율 실행 트리거용 API 키. 미설정 시 트리거 엔드포인트를 fail-closed로 차단한다
# (환경변수 없이 열어두지 않는다 - CORS 설정은 인증을 대신하지 않는다).
_BRIDGE_API_KEY = os.getenv("ANTIGRAVITY_BRIDGE_API_KEY")
if not _BRIDGE_API_KEY:
    logger.warning(
        "[보안 경고] ANTIGRAVITY_BRIDGE_API_KEY 미설정 - "
        "/api/antigravity/trigger 는 모든 요청을 차단합니다."
    )


def _verify_bridge_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """원격 트리거 요청의 API 키 검증. 서버에 키가 설정되지 않으면 무조건 차단(fail-closed)."""
    if not _BRIDGE_API_KEY:
        raise HTTPException(status_code=503, detail="서버에 ANTIGRAVITY_BRIDGE_API_KEY가 설정되지 않아 트리거를 사용할 수 없습니다.")
    if not x_api_key or not hmac.compare_digest(x_api_key, _BRIDGE_API_KEY):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키")

pm_owner = L1PMOwner(auto_heal=True)
quant_worker = QuantTraderWorker(is_mock=True)
defense_worker = DefenseResearcherWorker()

class CommandRequest(BaseModel):
    command: str = "삼성전자 퀀트 리밸런싱 및 KAI 방산 동향 수집"
    auto_heal: bool = True

@app.get("/")
def root():
    return {
        "service": "Antigravity Remote Bridge API",
        "status": "ONLINE",
        "endpoints": {
            "trigger": "POST /api/antigravity/trigger",
            "status": "GET /api/antigravity/status",
            "universe": "GET /api/antigravity/universe",
            "intel": "GET /api/antigravity/intel"
        }
    }

@app.get("/api/antigravity/status")
def get_status():
    """Antigravity OS 및 대시보드 상태 조회"""
    return {
        "status": "ONLINE",
        "dashboard_url": "http://localhost:8501",
        "cloud_url": "https://newsinfo.cloud/kai/",
        "version": "2.0.0-PROD",
        "os_owner": "제라드 던 (Jared Dunn)",
        "self_healing": True,
        "tasks_count": len(pm_owner.tasks),
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/antigravity/trigger")
async def trigger_command(payload: CommandRequest, x_api_key: Optional[str] = Header(default=None)):
    """https://newsinfo.cloud/kai/ 등에서 명령 수신 및 DAG 자율 실행.
    X-Api-Key 헤더가 ANTIGRAVITY_BRIDGE_API_KEY와 일치해야 한다 (미설정 시 항상 차단)."""
    _verify_bridge_api_key(x_api_key)
    try:
        tasks = await pm_owner.run_autonomous_loop(payload.command)
        return {
            "status": "SUCCESS",
            "command": payload.command,
            "tasks": tasks,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/antigravity/universe")
def get_stock_universe(limit: int = 10):
    """stock_dashboard 실시간 주식 유니버스 반환"""
    return quant_worker.get_real_universe(limit=limit)

@app.get("/api/antigravity/intel")
async def get_defense_intel(limit: int = 10):
    """codex ceo_briefing.db 실시간 방산 인텔리전스 및 3줄 요약 반환"""
    raw_feeds = await defense_worker.fetch_defense_sources(limit=limit)
    processed = await defense_worker.process_and_filter_intel(raw_feeds)
    return processed

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8502)
