# Codex Segment Revenue + Dilution Reinforcement — 2026-07-28

## 요청

- 세그먼트 매출/제품 노출도 보강
- 메자닌/희석 리스크 데이터 보강

## 조치

### 1. DART 희석 스케줄 보강

파일: `scheduler.py`

기존 `DART희석공시`는 CB/BW/EB 수집기만 호출했다. 이제 매일 07:10 작업에서 다음을 함께 실행한다.

- `collect_dilution_events(days=365)`: CB/BW/EB
- `collect_equity_issue_events(since="2020-01-01", missing_only=True, limit=500)`: 유상증자/무상증자/유무상증자

즉, 앞으로 희석 리스크는 전환사채류뿐 아니라 유상/무상증자 이벤트까지 같이 최신화된다.

### 2. DART 세그먼트 스케줄 보강

파일: `scheduler.py`

기존 주간 세그먼트 작업은 `collect_dart_segment_breakdown.py --limit 500`이었다. 이 방식은 fnlttSinglAcntAll 계정 기반이라 과거 감사에서 가짜 세그먼트/단위 혼재 위험이 있었다.

이를 HTML 사업보고서 표 기반 파서로 변경했다.

```bash
scripts/collect_dart_segment_revenue.py --limit 1200 --years 2020..현재연도
```

환경변수:

- `DART_SEGMENT_WEEKLY_LIMIT`: 기본 1200
- `DART_SEGMENT_START_YEAR`: 기본 2020

### 3. 세그먼트 파서 예외 수정

파일: `scripts/collect_dart_segment_revenue.py`

일부 HTML 표에서 앞 3개 행에 `td/th`가 없는 경우 `max()` 빈 시퀀스 예외가 발생했다. 빈 헤더 표는 건너뛰도록 수정했다.

### 4. 커버리지 감사 리포트 추가

파일: `scripts/audit_segment_dilution_coverage.py`

생성 파일:

- `research_outputs/segment_dilution_coverage_20260728.json`
- `research_outputs/segment_dilution_coverage_20260728.md`

이 리포트는 다음을 분리해서 보여준다.

- 연결전체만 있는 종목 vs 실제 사업부문 분해가 있는 종목
- 희석 이벤트별 금액 커버리지
- 희석률/풋옵션일 커버리지
- 세그먼트 미수집 시총 상위 종목
- 금액 결측 희석 이벤트 예시

## 실행 결과

### 세그먼트

수집 전:

- 실제 세그먼트 분해 종목: 284개
- 커버리지: 10.89%
- 명시적 revenue_pct 행: 391개

상위 구간 일부 보강 후:

- 실제 세그먼트 분해 종목: 319개
- 커버리지: 12.23%
- 명시적 revenue_pct 행: 486개

추가 저장 예:

- 삼성물산, NAVER, 한국전력, KT, 에이피알, 에코프로, 이수페타시스, 원익IPS, HLB, 피에스케이, 제주반도체 등

### 희석

실행:

```bash
python -m collectors.dart_equity_issue_collector --since 2020-01-01 --missing-only --limit 500
python -m collectors.dart_dilution_collector --days 2400 --missing-only --limit 500
python scripts/backfill_dilution_issue_amounts.py --since 2020-01-01 --source dart_disclosure_parse --limit 1000
```

결과:

- 유상/무상증자 파서 누락 61건 저장, 오류 0
- CB/BW/EB 파서 버전 누락 5건 저장, 오류 0
- `dart_disclosure_parse` 금액 결측 1,000건 재검토: 업데이트 0건

해석:

- `dart_equity_issue`: 7,461건 중 금액 6,442건, 86.34%
- `dart_dilution`: 6,307건 중 금액 5,652건, 89.61%
- `dart_disclosure_parse`: 4,036건은 금액 0%, 대부분 만기전취득/자기사채/결과/정정류라 금액 보강보다 이벤트 플래그로 취급하는 것이 적절하다.

## 매핑 반영

세그먼트 수집 후 다음을 실행했다.

```bash
python scripts/ops/sync_cafe_stock_indicator_mappings.py
python scripts/build_trigger_discovery_lab.py
python scripts/generate_trigger_discovery_insights.py
```

결과:

- 종목별 지표 매핑 upsert: 1,621건
- trigger discovery events: 108,652
- trigger discovery stock links: 313,596
- trigger discovery forward returns: 878,303

## 남은 과제

1. 세그먼트 분해 커버리지는 아직 12.23%로 낮다. 다만 사업부문을 공시하지 않는 단일사업/금융/지주/바이오 기업도 많으므로 단순 100% 목표는 부적절하다.
2. 시총 상위 미수집 종목 중 SK하이닉스, 기아, 한미반도체 등 투자상 중요한 종목은 수동/개별 파서 보강 후보로 남는다.
3. `dart_disclosure_parse` 금액 0% 묶음은 방어 필터에서 “금액 미상 이벤트”로 감점하되, 발행금액 기반 리스크에는 넣지 않는 것이 안전하다.
4. 주간 스케줄이 1,200종목 범위를 계속 보강하도록 변경했으므로, 다음 일요일 실행 후 커버리지 재감사가 필요하다.
