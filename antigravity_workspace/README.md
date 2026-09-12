# Project Antigravity V2: 계층형 멀티 에이전트 시스템

`Antigravity 시스템 운영 지침서 V2.pdf` 사양에 따라 맥미니(M4) 단일 인프라에서 주식 자동매매, KAI/항공방산 인텔리전스 및 AI 자가 고도화(Self-Improvement)를 완벽히 자동화하도록 구축된 시스템입니다.

---

## 1. 계층형 아키텍처 (Multi-Agent Architecture)

- **L1. PM / OS Owner (`agents/l1_pm_owner.py`)**: 제라드 던 페르소나, 자연어 의도 파싱(DAG 분해), Handoff 및 최종 QA.
- **L1-A. Dev Orchestrator (`agents/l1_a_dev_orchestrator.py`)**: 주식 퀀트 리밸런싱, 런타임 에러 감지 및 자가 패치 루프 관리.
- **L1-B. Content Orchestrator (`agents/l1_b_content_orchestrator.py`)**: KAI/방산 뉴스 수집, 중복 필터링, 3줄 요약 보고서 발행(Slack/Notion).
- **L2 Workers (`agents/l2_workers/`)**:
  - `codex_builder.py`: 스택 트레이스 분석 및 Git fix/* 브랜치 패치 생성.
  - `claude_reviewer.py`: 무한 루프, 보안 및 무결성 교차 검증기.
  - `quant_trader.py`: 비동기 퀀트 데이터 수집 및 증권사 주문 봇.
  - `defense_researcher.py`: DAPA/KAI 기사 수집, 0.85 코사인 유사도 노이즈 필터링, 3줄 전략 요약문 생성.
- **L3. Data & Memory Infrastructure (`memory/`)**:
  - PostgreSQL 16 + pgvector (1536차원 임베딩 HNSW 인덱스), Redis, Ollama Hermes-3.
- **UI 대시보드 (`dashboard/app.py`)**: Streamlit 기반 포트 8501 통합 관제 센터.

---

## 2. 빠른 실행 가이드 (Quick Start)

### 1) L3 인프라 컨테이너 기동
```bash
cd /Volumes/Realtek_NVME/stock_dashboard/antigravity_workspace
docker-compose up -d
```

### 2) L1 마스터 오케스트레이터 자율 가동
```bash
python -m agents.l1_pm_owner --mode full-autonomous --auto-heal True
```

### 3) 통합 관제 대시보드 (Streamlit) 실행
```bash
streamlit run dashboard/app.py --server.port 8501
```

### 4) 전체 유닛 및 자가 패치 테스트 실행
```bash
pytest tests/ -v
```
