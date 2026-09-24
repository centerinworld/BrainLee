"""
Project Antigravity: Remote Bridge API Server (FastAPI)
https://newsinfo.cloud/kai/ 및 외부 클라우드에서 Antigravity OS로 원격 지시 및 상태 제어
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

# CORS 설정 (newsinfo.cloud 및 로컬 호스트 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
async def trigger_command(payload: CommandRequest):
    """https://newsinfo.cloud/kai/ 등에서 명령 수신 및 DAG 자율 실행"""
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
