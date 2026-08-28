# QUARTERLY_4WAY OPEN 개선 실행안 (2026-05-27)

## 현재 상태(실 DB 재집계)
- check_type `QUARTERLY_4WAY`: 325,535건
  - CLOSE_MATCH 118,756
  - CONFIRMED 28,940
  - SELF_CONSISTENT 41,635
  - STRUCTURAL 92,313
  - OPEN 43,891
- OPEN source_count 분포
  - source_count=0 : 11,945건
  - source_count=1 : 31,946건

## 핵심 해석
1. `source_count=1` (31,946건): **즉시 개선 가능 구간**
- DART 분기(CFS/OFS) 추가 수집으로 2소스 이상으로 승격 가능
- OPEN 상당수를 CONFIRMED/CLOSE_MATCH로 전환 가능

2. `source_count=0` (11,945건): **구조 한계 구간**
- 어떤 소스에도 분기값 자체가 없음
- DART/FG/Naver/Seibro 중 신규 소스 확보 전에는 OPEN 유지가 정상

## 사용자 질문에 대한 명확 답변
### Q1. “네이버/에프앤가이드가 정상 파싱되면 DART ID 매핑값으로 넣을 수 있나?”
- 가능. 단, 자동 삽입 조건 필요:
  - 동일 stock_code/year/quarter/report_type 키
  - DART 원문 우선
  - DART 부재 시 FG+Naver 일치(허용오차)일 때만 보조 삽입

### Q2. “해당 방법으로 못 넣는 값(=NULL/0) 확인 필요?”
- 필수.
- `NULL/0`는 채우는 대상이 아니라 **원인 분류 대상**:
  - 실제 미공시
  - 파싱 불가
  - 단위/계정 매핑 불일치

### Q3. “2025년 값이 50%대인데 왜 개선이 안 되나?”
- 2025 OPEN 6,519건 중 `source_count=0`가 4,056건으로 큼.
- 즉 데이터가 없는 구간 비중이 높아 단순 파이프라인 재실행으로는 개선 한계.

### Q4. “소형주 수집 키 3개로 계속 진행”
- 타당. KEY1/2/3 로테이션으로 `source_count=1` 소형주부터 우선 축소 가능.

## 바로 실행할 개선 절차
1. 우선순위 큐 생성
- 대상: `QUARTERLY_4WAY` + `OPEN` + `source_count=1`
- 필드 우선순위: revenue/op/net_income -> total_assets/total_equity

2. DART 분기 재수집 (KEY 로테이션)
- CFS -> OFS 순
- 검증 통과 시만 업서트
- `financial_fix_log`에 run_id/fix_rule/source 기록

3. 자동 판정 규칙
- DART 존재 + 2개 지표 이상 유효 -> CONFIRMED
- DART 부재 + FG/Naver 일치 -> CLOSE_MATCH 후보
- 둘 다 부재 -> STRUCTURAL(OPEN 유지)

4. 일일 운영
- 장후 1회: OPEN 감소 배치
- 다음날 장전: 재검증 집계 리포트

## 운영 KPI
- 1차 목표: source_count=1 OPEN 31,946건 중 30~50% 축소
- 2차 목표: 2025 OPEN 6,519건 중 20~35% 축소
- 구조 한계 구간(source_count=0)은 별도 표기하여 “미개선=오류” 오해 방지

