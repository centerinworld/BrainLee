# 🛰️ Project Antigravity & CEO Briefing Platform — External AI System

> **외장 SSD 전용 통합 저장소**: `/Volumes/Realtek_NVME/AI System`  
> 모든 멀티 에이전트 코드, 백엔드 API, 프론트엔드 콘솔, DB 및 Streamlit 관제 보드가 이 폴더에서 단독으로 관리되고 작동합니다.

---

## 📁 디렉토리 구조 (Directory Structure)

```
/Volumes/Realtek_NVME/AI System/
├── codex/
│   └── ceo-briefing-platform/
│       ├── backend/                # FastAPI 백엔드 (Port 8011, api.newsinfo.cloud)
│       │   ├── main.py             # 메인 API 및 AI 관제 엔드포인트
│       │   ├── services/rss_ingest.py # 3-Tier Multi-LLM Waterfall 엔진
│       │   └── db_access.py        # SQLite DB 인터페이스
│       ├── frontend/               # 프론트엔드 웹 콘솔 (Port 5500, newsinfo.cloud)
│       │   ├── kai/                # 🛰️ AI 통합 관제 센터 (/kai/)
│       │   ├── index.html          # 📊 관리자 콘솔 메인
│       │   ├── kai.css / styles.css# 엔터프라이즈 라이트 디자인 시스템
│       │   └── kai.js              # 관제 대시보드 및 지표 렌더링
│       ├── data/
│       │   └── ceo_briefing.db     # 10,027건 방산 뉴스 & RSS 피드 데이터베이스
│       └── .env                    # 통합 AI API 키 및 환경 변수
│
├── antigravity_workspace/          # 🤖 자율 멀티 에이전트 오케스트레이션 시스템
│   ├── agents/                     # L1 PM, Dev/Content Orchestrator, L2 Workers
│   ├── dashboard/app.py            # Streamlit HUD 관제 보드 (Port 8501)
│   ├── llm_client.py               # 1차 Gemini ➔ 2차 Groq ➔ 3차 DeepSeek 폴백 클라이언트
│   ├── tests/                      # pytest 6/6 자동 검증 슈트
│   └── venv/                       # 통합 Python 3.11 가상환경
│
├── start_all_services.sh           # 🚀 전체 서비스 원클릭 실행 스크립트
├── backend_8011.log                # 백엔드 로그
├── frontend_5500.log               # 프론트엔드 로그
└── streamlit_8501.log              # Streamlit 로그
```

---

## ⚡ 주요 서비스 포트 및 도메인 매핑

| 서비스 명칭 | 로컬 포트 | 운영 도메인 | 작업 디렉토리 (CWD) |
| :--- | :---: | :--- | :--- |
| **KAI AI 관제 센터** | `5500` | [https://newsinfo.cloud/kai/](https://newsinfo.cloud/kai/) | `.../codex/ceo-briefing-platform/frontend` |
| **관리자 콘솔** | `5500` | [https://newsinfo.cloud/](https://newsinfo.cloud/) | `.../codex/ceo-briefing-platform/frontend` |
| **FastAPI 백엔드** | `8011` | [https://api.newsinfo.cloud/](https://api.newsinfo.cloud/) | `.../codex/ceo-briefing-platform/backend` |
| **Streamlit HUD** | `8501` | [http://localhost:8501/](http://localhost:8501/) | `.../antigravity_workspace` |

---

## 🌊 3-Tier Multi-LLM 라우팅 체인
1. **1차**: Google Gemini (`gemini-3.6-flash`) — 무료 쿼터 최우선 소진 (응답 ~0.4s)
2. **2차**: Groq / Grok (`qwen/qwen3.8-27b`) — LPU 초고속 추론 (응답 ~0.28s)
3. **3차**: DeepSeek API (`deepseek-chat`) — 초저비용 고성능 LLM
4. **4차 (안전망)**: OpenAI (`gpt-4o-mini`)
