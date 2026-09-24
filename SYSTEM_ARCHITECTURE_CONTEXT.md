# Project AGI Development — Core System Architecture & AI Agent Playbook

## 1. 4대 AI 엔진별 특화 역할 분담 (Role Specialization)

| AI 엔진 | 투입 형태 | 과금 정책 | 핵심 전담 역할 |
| :--- | :--- | :--- | :--- |
| **OpenAI GPT-4o / Astra** | 구독형 (ChatGPT Plus) | 고정비 ($0 추가) | **상위 총괄 설계자 (Chief Architect)**<br>• 시스템 취약점 상시 진단 및 개선점 발굴<br>• Intelligence 확장 및 전략적 DAG 계획 수립 |
| **Claude 3.5 Sonnet** | 구독형 (Claude Pro M4 Local) | 고정비 ($0 추가) | **실행 & 검토 총괄 (Lead Reviewer & Deployer)**<br>• Qwen 2.5 Coder 작성 초안 코드 전수 심층 검토<br>• M4 NVME 파일 직접 패치 & 문법/성능 최적화<br>• 100점 무결성 검증 및 Zero-Downtime 무인 배포 |
| **Qwen 2.5 Coder 32B on Groq LPU** | Groq LPU 초광속 가속 (API) | **Groq 무료 쿼터 ($0) + 월 1만원 한도** | **초광속 기초 초안 코더 (Ultra-Fast Coder)**<br>• 초당 300+ 토큰으로 0.8초 만에 고정밀 파이썬/SQL 초안 생성<br>• 작성 즉시 Claude에게 심층 검토 의뢰 |
| **Google Gemini 3.6 Flash & Grok** | 완전 무료 티어 (AI Studio) | **$0 (100% 무료)** | **대용량 뉴스 & 데이터 전처리 (Free Data Worker)**<br>• 10,041건 뉴스 3줄 요약, 61개 RSS 크롤링<br>• 883만 행 시세 DB 이상치 감시 24시간 풀가동 |

---

## 2. 3단계 자율 개발 워크플로우
1. **[Step 1 - GPT Astra]**: 전체 시스템 아키텍처 및 DAG 구현 계획 수립
2. **[Step 2 - Qwen 2.5 Coder on Groq]**: 0.8초 초광속 고정밀 파이썬/SQL 초안 코드 작성
3. **[Step 3 - Claude Local M4]**: M4 로컬에서 초안 심층 검토, 에러 수정, 자가 컴파일 검증 및 배포
