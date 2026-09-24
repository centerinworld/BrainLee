
<!-- AI MODEL HIERARCHY RULES -->
## 🚨 AI 모델 위계 및 작업 인계 절대 원칙
1. **고차원 모델 (Codex, Claude)**: 시스템 아키텍처, 퀀트 알고리즘, 복합 설계, 고차원 의사결정을 담당.
2. **소형/경량 모델 (Qwen 등)**: 원시 데이터 수집, 1차 텍스트 요약, 팩트 추출 등 보조 전처리만 담당.
3. **절대 규칙**:
   - ❌ **대형 모델(Codex/Claude)의 고차원 작업을 소형 모델(Qwen)이 이어받는 것은 절대 금지 (Top-Down Downgrade FORBIDDEN)**.
   - ✅ **소형 모델(Qwen)이 전처리한 데이터를 대형 모델(Codex/Claude)이 이어받는 상향 파이프라인만 허용 (Bottom-Up Ingestion MANDATED)**.
   - 🔄 **세션 한도 도달 시**: Claude ➔ Codex 또는 Codex ➔ Claude 등 동급 프론티어 대형 모델 간의 수평 인계만 허용.
