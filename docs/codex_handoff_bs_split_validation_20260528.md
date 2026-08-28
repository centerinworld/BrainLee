# Codex Handoff — BS 분리 검증 적용 (2026-05-28)

## 적용 목적
- 기존 `QUARTERLY_4WAY`에서 P&L(매출/영업이익/순이익)과 BS(자산/자본)를 동일 규칙으로 판정해
  최근연도 BS가 과도하게 낮게 보이는 문제를 완화.
- DART 우선 원칙은 유지하되, BS는 범위/시점 차이를 반영한 완화 규칙 분리 적용.

## 코드 변경
- 파일: `/Applications/stock_dashboard/scratch/fin_quarterly_4way_validate.py`
- 변경 내용:
  1. `BS_FIELDS = {'total_assets','total_equity'}` 추가
  2. BS 전용 허용치 추가
     - `TOL_BS_CLOSE = 0.25`
     - `TOL_BS_WIDE  = 0.60`
  3. `classify()`에 BS 전용 분기 로직 추가
     - DART anchor + peer( FnGuide/Naver/OFS ) 최소 diff 기준 판정
     - 근접 시 `CLOSE_MATCH`, 큰 차이는 `STRUCTURAL`, 단일 소스는 `OPEN`

## 실행
- 명령: `bash /Applications/stock_dashboard/scratch/run_daily_validation.sh`
- 로그: `/tmp/daily_validate_20260528_200958.log`

## 결과 (적용 전 → 적용 후)
- `QUARTERLY_4WAY OK`: **56.6% → 60.3%** (+3.7%p)
- `QUARTERLY_4WAY OPEN`: **53,226 → 53,226** (동일)
- `QUARTERLY_4WAY STRUCTURAL`: **128,772 → 113,276** (감소)
- `QUARTERLY_4WAY CLOSE_MATCH`: **151,305 → 166,801** (증가)

### BS 개선 효과
- `BS total_assets OK`: **50.0% → 60.4%**
- `BS total_equity OK`: **52.0% → 60.1%**

## 해석
- 데이터가 새로 생긴 것이 아니라, BS 특성(연결/별도, 범위 차이, 시점 차이)을 반영한 판정으로
  과도한 STRUCTURAL 비중이 CLOSE_MATCH로 재분류됨.
- DART 없는 데이터를 DART로 덮어쓴 것은 없음.

## Claude 재검토 요청 포인트
1. BS 전용 허용치(25%/60%) 민감도 테스트
2. 업종별(금융/비금융) BS 허용치 분기 필요성
3. 프론트 표기 분리
   - P&L 신뢰도
   - BS 신뢰도
   - 소스 가용성 부족(OPEN) 별도 배지

