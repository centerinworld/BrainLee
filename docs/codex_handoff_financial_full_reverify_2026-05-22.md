# Codex Full Re-Verification Handoff (2026-05-22)

## 1) 전제 (사용자 확정 기준)
- DART/FnGuide/Naver/Seibro는 **우선순위 소스가 아님**.
- 1/2/3차 검증은 단계 우선순위가 아니라, **4개 소스 비교 후 일치 항목이 가장 많은 값(합의값)**을 채택하기 위한 프로세스임.
- 즉 최종 입력값은 source priority가 아니라 **consensus score max** 기반이어야 함.

---

## 2) 이번 전수 검증 범위
- DB: `/Applications/stock_dashboard/stock.db`
- 검증 스크립트/리포트:
  - `scratch/weekly_revalidation.py --phase F`
  - 생성 리포트: `scratch/revalidation_report_20260522_212622.json`
- 교차 확인 SQL: financial_data / cash_flow_data / cf_validation_flags / financial_source_snapshot / company_mapping_profile / stock_universe

검증 시각: **2026-05-22 21:26 (Asia/Seoul)**

---

## 3) 핵심 결과 (실측)

### 3.1 재무/CF 검증 상태
- CF 4중 검증(`cf_validation_flags`):
  - CONFIRMED: **98,792**
  - CLOSE_MATCH: **3,341**
  - AMBIGUOUS: **0**

- FIN_NAVER(재무 3중) 요약:
  - `operating_profit` CONFIRMED: **6,098 / 6,307 (96.7%)**
  - `net_income` CONFIRMED: **6,017 / 6,307 (95.4%)**
  - `revenue` CONFIRMED: **3,902 / 6,305 (61.9%)**
  - revenue는 CLOSE_MATCH 비중이 높아 구조적/매핑 이슈가 여전함.

### 3.2 분기 P&L 잔여 오류
- 분기 행 수(`is_annual=0`): **79,951**
- 분기 매출 음수(`revenue < 0`): **1,010건**
  - Q4 음수: **526건**
  - source 분포: NULL 961건, fnguide 49건

해석:
- Q4 음수 다발은 여전히 역산/원천 불일치 이슈가 남아 있음.
- 분기 매출 음수는 데이터 품질 이슈로 계속 분류 필요.

### 3.3 source lineage 품질
- `financial_data` 전체 107,837건 중 `data_source` NULL/blank: **70,598건 (65.5%)**

해석:
- 합의 기반 엔진을 운영하려면 source lineage가 필수인데, 현재 NULL 비율이 매우 높아 자동합의 신뢰성이 낮음.

### 3.4 매핑 커버리지
- 6자리 상장코드 수(`stock_universe`): **3,881**
- `company_mapping_profile` 활성 매핑 종목 수: **351**
- 커버리지: **9.0%**

해석:
- 종목별 DART id/account 매핑 “완성” 상태 아님.
- 현재는 공통/핵심 종목 중심 매핑 수준.

### 3.5 snapshot 검증 상태
- `financial_source_snapshot.verification_status='unverified'`: **33,201건**

해석:
- snapshot은 쌓였으나 검증 상태를 최종 입력 의사결정에 충분히 활용하지 못함.

---

## 4) 코드/정책 불일치 (중요)
다음 구간에서 사용자 기준과 충돌 가능성이 확인됨.

1. `scheduler.py`
- "FnGuide를 단일 권위 소스로 재무 수집·보정" 설명/흐름 존재

2. `main.py`
- 일부 조회 SQL에서 `CASE WHEN data_source='dart' THEN 0 WHEN data_source='fnguide' THEN 1 ...` 식의 priority 정렬 존재

3. `crud.py`
- `data_source='fnguide'` 보호 갱신 로직 존재

의미:
- 현재 시스템은 합의 엔진 단일체계가 아니라 "우선순위 + 보호 로직 + 검증 플래그"가 혼재.

---

## 5) Claude 수정 방향 (필수)

### A. 합의 엔진 단일화
- 신규 테이블(예시): `financial_value_consensus`
  - key: stock_code, year, quarter, report_type, metric
  - values: dart/fnguide/naver/seibro 원천값 + 정규화값
  - score: match_count, confidence, tie_flag, tie_break_rule
  - output: winner_source, winner_value

### B. financial_data 입력 규칙 변경
- `financial_data`에는 winner_value만 반영
- `data_source`는 winner_source 기록
- tie 발생 시에만 tie-break 룰 사용 (룰 문서화 필수)

### C. NULL source 정리
- NULL/blank source 전수 재평가
- 합의 계산 가능한 행은 source/값 재기입
- 불가능한 행은 review queue 분리

### D. 분기 매출 음수 정리
- Q4 역산 로직 재검증
- annual/Q1~Q3 source 일관성 검증 후 재계산
- 여전히 음수면 anomaly로 격리 + 수동검증 큐

### E. 매핑 커버리지 확장
- 최소 목표: 351 → 3,000+
- 우선순위:
  1) 보유/매매대상
  2) KOSPI200/KOSDAQ150
  3) 전체 확장

---

## 6) 즉시 재현 SQL
```sql
-- source 누락
SELECT COUNT(*) total,
       SUM(CASE WHEN data_source IS NULL OR TRIM(data_source)='' THEN 1 ELSE 0 END) null_source
FROM financial_data;

-- 분기 매출 음수
SELECT COUNT(*) FROM financial_data WHERE is_annual=0 AND revenue<0;
SELECT quarter, COUNT(*) FROM financial_data WHERE is_annual=0 AND revenue<0 GROUP BY quarter;
SELECT COALESCE(NULLIF(TRIM(data_source),''),'(null)') src, COUNT(*)
FROM financial_data WHERE is_annual=0 AND revenue<0 GROUP BY src;

-- 매핑 커버리지
SELECT COUNT(DISTINCT stock_code)
FROM stock_universe
WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';

SELECT COUNT(DISTINCT stock_code)
FROM company_mapping_profile
WHERE is_active=1
  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';

-- CF 검증 상태
SELECT status, COUNT(*) FROM cf_validation_flags GROUP BY status;
```

---

## 7) Codex 최종 판단
- 전수 검증은 완료.
- 하지만 사용자 기준(합의값 기반 입력)으로는 **완료 상태가 아님**.
- 남은 핵심 과제는 “소스 우선순위 제거 + 합의 엔진 단일화 + source lineage 복원 + 분기 음수/매핑 커버리지 해소”.

