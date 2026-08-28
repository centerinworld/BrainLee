# Codex Handoff - Quant Major Indicators Factor Extension Plan (2026-06-14)

## Goal
EPIC 대체 지표 메뉴가 대부분 연결된 상태에서, 전문 퀀트 투자자가 사용하는 핵심 팩터를 `퀀트 주요지표` 페이지 안의 별도 탭으로 제시했다. 새 페이지를 만들지 않고 기존 페이지 내부 탭으로만 확장했다.

## Changed File
- `frontend/src/views/QuantMajorIndicatorsView.jsx`

## UI Change
- 기존 퀀트 주요지표 화면에 내부 탭 2개 추가
  - `현재 지표`: 기존 카탈로그/차트/상세 테이블 유지
  - `확장계획`: 추가할 퀀트 팩터 후보와 구현 우선순위 표시
- `확장계획` 탭은 5개 그룹, 25개 후보 지표를 표시한다.
- 각 지표는 `계산/정의`, `원천 후보`, `준비상태`, `순위(P1/P2/P3)`를 함께 보여준다.

## Factor Groups Added
1. 스타일 팩터 스코어
   - Value composite
   - Quality composite
   - Momentum composite
   - Low volatility / downside risk
   - Size / liquidity

2. 실적 가속·턴어라운드
   - Earnings acceleration
   - Margin inflection
   - Cash conversion quality
   - CapEx expansion signal
   - Inventory / backlog cycle

3. 수급·시장 미세구조
   - Volume breakout quality
   - Investor flow persistence
   - Short balance squeeze
   - ETF / passive flow
   - Intraday execution health

4. 이벤트·공시 팩터
   - Order backlog surprise
   - Dilution / overhang risk
   - Buyback / insider alignment
   - Analyst revision / consensus surprise
   - Disclosure sentiment

5. 섹터/매크로 로테이션
   - HS export momentum
   - Sector breadth
   - Macro regime overlay
   - Commodity input spread
   - Global peer confirmation

## Design Principles Shown In UI
- 검증 우선: 투자 로직에 쓰는 지표는 `source_name`, `source_detail`, `quality`를 남기고 partial을 exact처럼 사용하지 않는다.
- 팩터 합성: 단일 지표가 아니라 가치·퀄리티·모멘텀·수급·이벤트를 z-score로 합산한다.
- 수집 주기 분리: 재무/공시, 장중 수급/틱, 월간 관세청/매크로 데이터를 각기 다른 주기로 운영한다.

## Rationale / External Reference Framework
- Fama-French 계열: size, value, profitability, investment, momentum/reversal 포트폴리오를 주요 연구 팩터로 제공한다.
- MSCI/BlackRock 계열: value, quality, momentum, size, minimum/low volatility 등 스타일 팩터를 장기 초과수익/위험 설명 축으로 사용한다.
- AQR Quality Minus Junk 계열: profitability, growth, safety, payout 등 quality 복합 팩터가 투자 판단에 쓰인다.

## Build / Verification
- `npm run build` 성공.
- Browser 검증 완료:
  - `http://localhost:5173` 접속
  - `퀀트 주요지표` 메뉴 진입
  - `확장계획` 탭 클릭
  - `퀀트 지표 확장계획`, `Value composite`, `Volume breakout quality`, `Macro regime overlay` 렌더링 확인

## Important Scope Note
이번 작업은 DB 대량 적재나 자동매매 로직 변경이 아니다. 현재는 투자 전문가형 지표 후보와 구현 우선순위를 화면 탭으로 정리한 작업이다. 실제 점수화/백테스트/자동매매 반영은 다음 단계에서 별도 테이블과 검증 게이트를 만들어야 한다.

## Recommended Next Work
1. `quant_factor_scores` 테이블 설계
   - stock_code, trade_date, factor_group, factor_name, raw_value, z_score, percentile, source_quality, calculated_at
2. P1 중 기존 DB로 계산 가능한 항목부터 배치 구현
   - value, quality, momentum, low volatility, size/liquidity, earnings acceleration, volume breakout, investor flow
3. 팩터별 validation table 추가
   - raw source freshness
   - missing ratio
   - outlier count
   - source_quality distribution
4. 백테스트 연결
   - 단일 팩터 수익률
   - 복합 팩터 수익률
   - KOSPI/KOSDAQ 대비 초과수익
   - MDD, turnover, hit ratio
5. 장중 자동매매에는 검증완료 P1 지표만 사용
   - intraday tick/minute 기반 지표는 Kiwoom/KIS 수집 안정성 확인 전까지 추천/분석용으로만 표시

## Claude Review Checklist
- `확장계획` 탭이 기존 `현재 지표` API 흐름을 방해하지 않는지 확인
- 모바일/좁은 화면에서 확장계획 테이블 overflow가 정상인지 확인
- P1/P2/P3 우선순위가 실제 보유 DB와 맞는지 확인
- partial 지표가 exact처럼 투자 로직에 들어가지 않도록 다음 단계 테이블 설계 검토
- 향후 `quant_factor_scores` 저장 시 source lineage가 누락되지 않도록 수집기/배치 설계 검토
