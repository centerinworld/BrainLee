# StockEasy 3전략 역추론 통합 운영 문서 (단일 기준, 2026-05-14)

## 목적
- 단순 Precision/Recall 보고에서 끝내지 않고, **당일 편입/이탈 변화**를 근거로
  로직 임계값을 계속 미세 조정한다.
- 리포트 문구 설명보다 **3개 전략 로직(Peak/Momentum/Value) 근접 재현**을 최우선으로 한다.
- 특히 Momentum/Value의 0% 구간을 빠르게 탈출하고 교집합을 지속적으로 늘린다.
- 텔레그램에는 반드시 아래 4가지를 함께 보고한다.
1. 이전 로직값
2. 오늘 조정된 로직값
3. 조정 사유(신규편입 누락/과추출/재현율 저조 등)
4. StockEasy 대비 현재 차이(누락/과추출/당일 편출)

## 오늘 기준 추론 절차
1. `stockeasy_analysis` 최신/직전 2개 스냅샷을 비교해 전략별 `added/removed/exits_today` 계산
2. 우리 후보와 StockEasy 보유를 비교해 `intersect/only_se/only_ours` 산출
3. StockEasy 리포트 본문(`research.summary.content_list`)을 전략별로 전량 읽어 핵심 키워드 추출
4. 전략별 파라미터(`config/stockeasy_logic_params.json`) 자동 조정
   - `min_score`, `max_candidates`, `min_mktcap_억`
   - 리포트 키워드(AI/HBM/서버/반도체/수주 등) 반영
5. 변경 이력(`config/stockeasy_logic_tuning_history.json`) 적재
6. 트래커(`stockeasy_logic_tracker.md`)와 텔레그램에 조정 전/후 + 차이 보고

## 데이터 제약 (전제)
- 사용 가능한 입력은 아래로 제한한다.
1. 전략별 편입/편출 종목
2. 해당 날짜의 가격/거래량/수급/재무/밸류 지표
3. StockEasy 리포트 본문(`research.summary.content_list`)
- 리포트에 없는 항목(RSI/MACD/돌파기준/목표가/손절가/편출사유)은 내부 지표를 통해 역추론한다.

## 자동 조정 규칙(현재)
- 신규편입 누락이 감지되면: `min_score` 완화, `max_candidates` 확대
- 과추출이 크고 정밀도가 낮으면: `min_score` 강화, `max_candidates` 축소
- 재현율이 매우 낮으면: `min_score` 완화 시도
- 리포트가 대형주/AI 중심이면: `min_mktcap_억`를 상향해 잡음 후보를 억제

## 고정 원칙 (2026-05-14 반영)
- 추세추종 3전략(Peak/Momentum/Value)은 **섹터를 제한하지 않는다**.
- 섹터 정보는 설명/분석용 메타데이터로만 사용하며, 편입/편출 점수 가중치 또는 필터로 사용하지 않는다.

## 실행 원칙
1. 매일 편입/편출 변화를 먼저 분석한다.
2. Peak는 추세중심, Momentum은 추세+실적가속, Value는 재평가(추세+가치)로 가설을 분리한다.
3. `min_score/max_candidates/min_mktcap_억`를 자동 조정한다.
4. 조정 전/후와 사유, StockEasy 대비 차이를 텔레그램에 반드시 보고한다.
5. 목표치:
   - 1차: Momentum/Value Recall 30% 이상
   - 2차: F1 25% 이상
   - 3차: 전략별 교집합 안정적 확대

## 편출 데이터 처리
- 편출 종목은 스냅샷 기준으로 자동 감지하고 편입일 vs 편출시점(당일) 지표 변화를 함께 저장/비교한다.
- 편출 사유는 별도 제공을 기다리지 않고 지표 변화로 추론한다.

## 사례 학습 메모 (2026-05-14, Peak Easy)
- 편입 확인: `제주반도체(080220)`
  - 리포트 요지: 온디바이스 AI/LPDDR 수요 증가 + 실적 모멘텀(매출/이익 성장) 중심.
  - 구현 반영: 섹터 제한 없이 추세/실적/상대강도 점수로 편입 유지.
- 편출 확인: `에스비비테크(389500)` (최종수익률 -8.07%, 2일 보유 후 이탈)
  - 관측 패턴(편입 시점): `mktcap 3,191억`, `RSI 84.6`, `vol_ratio 6.13`, `resistance_gap +44.27%`.
  - 로직 강화: Peak 후보 산식에 **소형주 단기 과열 추격 제외** 필터 추가.
    - 조건: `mktcap < 5,000억` AND `resistance_gap >= +30%` AND (`RSI >= 82` OR `vol_ratio >= 4.5` OR `stoch 과열`)
  - 목적: 급등 말단 추격 편입 후 단기 되돌림(-6~-10%) 재발 억제.

## 스케줄 연동
- `scheduler._job_stockeasy_analysis()`에서
  `run_daily_analysis()` 직후 `run_validation(["peak","momentum","value"], send_tg=True)` 실행
- 즉, 매일 분석 직후 적응 검증/조정/보고가 자동 수행된다.
