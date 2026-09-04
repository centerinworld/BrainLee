# Codex → Claude 핸드오프: 개선안 5~10

작성일: 2026-07-12

## 5. 시장 국면

- 생성기: `scripts/build_market_regime_history.py`
- `market_regime_daily`: 2015-01-02~2026-07-10, 2,828일
- 분류: bull 962, bear 683, sideways 1,022, high_volatility 161
- 신호는 장 마감 후 계산되므로 `available_at`은 다음 영업일
- `strategy_regime_policy`: momentum, deep value recovery, breakout, mean reversion × 4국면 = 16정책

검증 과제: KOSPI 계열을 외부 지수와 월별 대조하고 전략별 국면 성과를 실제 OOS로 재추정할 것. 현재 정책 점수는 보수적 운영 규칙이다.

## 6. 신호 품질

- `signal_quality.py`
- `signal_quality_scores`
- 입력: 기대수익, 하방위험, 플러스 비율, 표본 수, 국면 적합성, 데이터 품질, 미래정보, 희석, 거래제한
- 출력: quality, confidence, action(buy_candidate/watch/avoid), penalties
- 낙폭과대 표본: confidence 90.0, quality 27.5, action avoid

검증 과제: 점수 구간별 실제 60/120/252일 성과를 추적해 calibration할 것.

## 7. 설명형 종목 신호

- `scripts/build_explainable_stock_signals.py`
- `explainable_stock_signals`: 683건 / 353종목
- high 121, medium 467, low 95
- 지표 변화 × 사업노출 × 매핑 신뢰도로 weighted impact 계산
- 100% 초과 노출 21건은 신뢰도 60% 감점 후 low로 강등

검증 과제: 카페 기반 후보 매핑과 DART 사업부문 매출 근거를 분리 표시하고, 원가 지표는 상승 방향이 악재일 수 있으므로 direction polarity를 지표별로 추가할 것.

## 8. 데이터 계보

- `scripts/build_data_lineage_catalog.py`
- `data_lineage_catalog`: 핵심 지표 8종
- API:
  - `/api/dashboard/data-lineage`
  - `/api/dashboard/data-lineage/{metric_key}`
- 출처, 소스 테이블, 기준일, 가용일 규칙, 계산식, raw/estimate/derived, 품질 규칙, 수집기, 담당 영역 저장

검증 과제: 프론트 숫자 클릭 팝오버에 API를 연결하고 모든 주요 카드의 metric_key를 부여할 것.

## 9. 라이브 신호 사후성과

- `live_signal_tracker.py`
- `scripts/update_live_signal_outcomes.py`
- `live_signal_registry`: 발생 시점 payload와 다음 사용가능 거래일 entry를 불변 저장
- `live_signal_outcomes`: 1/5/20/60/120/252일 수익, 최대상승, 최대하락
- canonical price와 `return_usable=1`만 사용
- 검증 신호 등록→성과 계산→삭제 테스트 통과

검증 과제: 실제 각 신호 생성기에서 `register_signal()` 호출, 중복키 정책, 신호 취소/만료 상태, 벤치마크 초과수익을 추가할 것.

## 10. 보안

- Git 추적 해제:
  - `hs_trade_lab/.env`
  - `__pycache__/config.cpython-314.pyc`
  - `__pycache__/main.cpython-314.pyc`
- 파일은 로컬에 유지
- `.env`, `hs_trade_lab/.env`, `config.py`, `config.py.save`: 권한 600
- `scripts/security_audit.py`: 추적 파일의 민감파일·GitHub PAT·AWS key·private key와 파일권한 검사
- 현재 추적 1,329파일, finding 0
- 결과: `research_outputs/security_audit_20260712.json`

필수 외부 조치:

1. 대화에 노출된 GitHub PAT 폐기·재발급
2. 네이버 계정 비밀번호 변경
3. `hs_trade_lab/.env`가 포함된 과거 Git 커밋의 모든 자격증명 회전
4. 필요 시 별도 승인 후 Git history rewrite

## 자동 운영

매 영업일 KRX 기본정보 갱신 후:

1. 자본행위 보정
2. 가격 급변 감사
3. 네이버 신규 외부 검증
4. 시장 국면
5. 설명형 신호
6. 데이터 계보
7. 라이브 신호 사후성과

주간 DART 배치 후 availability ledger를 재생성한다.

## 아직 남은 통합 작업

- 기존 모든 전략 백테스트를 공통 `CashPortfolio`와 strict next-bar 계약으로 이관
- 시장국면 정책을 실제 전략 주문 허용/비중 축소에 연결
- 설명형 신호와 계보를 개별종목 프론트에 연결
- 모든 실제 신호 생성기에 live registry 호출 연결
- 점수 calibration과 전략별 OOS 재실행

