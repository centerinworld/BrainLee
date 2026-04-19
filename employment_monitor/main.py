"""
employment_monitor/main.py — 고용보험 모니터 독립 FastAPI 앱

실행: uvicorn employment_monitor.main:app --port 8001 --reload
     또는 python -m employment_monitor.main
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (독립 실행 시)
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="고용/산재보험 모니터",
    description="data.go.kr 고용보험·산재보험 업종별 현황 API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/employment", tags=["employment"])


@app.get("/")
def root():
    return {"status": "ok", "service": "employment-monitor"}


@app.get("/health")
def health():
    from .db import connect
    try:
        conn = connect()
        cnt = conn.execute("SELECT COUNT(*) FROM employment_monthly").fetchone()[0]
        conn.close()
        return {"status": "ok", "rows": cnt}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("employment_monitor.main:app", host="0.0.0.0", port=8001, reload=True)
