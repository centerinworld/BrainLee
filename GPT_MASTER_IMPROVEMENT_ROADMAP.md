# GPT-4o / Astra Master System Improvement Roadmap
> **작성자**: GPT-4o Master Architect  
> **원칙**: Phase 1 (`stock_dashboard` 무결성/알파 극대화) 100% 달성 후 ➔ Phase 2 (`Market Intelligence`) 순차 진입  
> **최신 갱신**: 2026-09-12 15:53

---

## 🎯 Phase 1: stock_dashboard 퀀트 시스템 완벽 구축 (최우선 과제)
- [x] **[STK-001]** 818만 행 국내 시세 + 65만 행 미국 시세 고속 인덱스 무결성 검증 (완료)
- [x] **[STK-002]** 2,765개 전 종목 5대 멀티팩터(Value/Momentum/Quality/Growth/LowVol) 가중치 보정 (완료)
- [x] **[STK-003]** 19.1만 행 상장사 재무제표 팩터 이상치 필터링 및 0.1ms 캐시 연동 (완료)
- [x] **[STK-004]** M4 Mac mini 3단계 무인 자율 오케스트레이션(GPT ➔ DeepSeek ➔ Claude) 구축 (완료)
- [ ] **[STK-005]** 주봉/월봉 기준 외국인·기관 순매수 수급 집중도 스코어링 엔진 고도화 (진행 중)
- [ ] **[STK-006]** 2026 Q2 어닝 서프라이즈 모멘텀 팩터 실시간 백테스팅 뷰 연동 (대기)

---

## 🛰️ Phase 2: 방산 & Market Intelligence 플랫폼 연동
- [x] **[MKT-001]** DAPA 방위사업청 10,041건 피드 3줄 요약 및 3만건 토픽 메모리 인덱싱 (완료)
- [ ] **[MKT-002]** 글로벌 매크로(미국채 10Y, 환율, 방산 원자재)와 KAI 해외 수주 상관분석 엔진 탑재 (대기)
- [ ] **[MKT-003]** CEO 대상 실시간 AI 음성/텍스트 브리핑 1분 요약 다이제스트 고도화 (대기)

---

## 📋 에이전트 지시 가이드라인 (Agent Execution Directives)
1. **DeepSeek 코더**:
   - `SYSTEM_ARCHITECTURE_CONTEXT.md`를 기반으로 최소 토큰으로 모듈 초안 작성
   - 일일 $1.00 예산 한도를 초과하지 않도록 콤팩트한 코드 반환
2. **Claude Code 최적화기**:
   - M4 로컬에서 코드를 직접 패치하고 `python3 -m py_compile`로 100% 무결성 확인 후 배포
