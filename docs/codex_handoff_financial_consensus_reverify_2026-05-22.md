# Codex → Claude Handoff
## 재무/현금흐름 입력 규칙 재정의 + 재검증 지시 (2026-05-22)

### 0) 사용자 기준(최우선, 확정)
- `DART/FnGuide/Naver/Seibro`는 **입력 우선순위**가 아님.
- 1/2/3차 검증은 단계 우선순위가 아니라 **4개 소스 비교 결과의 합의(일치 수) 계산**임.
- 최종 입력값은 **4소스 중 가장 일치도가 높은 값(다수결/합의 점수 최대)**으로 채택.
- 동률일 경우에만 tie-break를 사용(규칙 명시 필요).

---

### 1) 이번 점검에서 확인된 현황 (DB 실측)

#### 1-1. `financial_data` 출처 누락이 과다
```sql
SELECT COUNT(*) total,
       SUM(CASE WHEN data_source IS NULL OR TRIM(data_source)='' THEN 1 ELSE 0 END) null_source
FROM financial_data;
```
- total: **107,837**
- null_source: **70,598 (65.5%)**

의미:
- 현재 상태로는 “4소스 합의 기반 입력”을 구조적으로 보장할 수 없음.
- 소스 lineage가 없는 레코드는 합의 점수 계산에서 제외 또는 재평가 큐로 보내야 함.

#### 1-2. 분기 데이터 검증 공백
```sql
SELECT COUNT(*) FROM financial_data WHERE is_annual=0;          -- 79,951
SELECT COUNT(*) FROM financial_data WHERE is_annual=0 AND revenue<0;  -- 1,149
SELECT COUNT(*) FROM financial_data WHERE is_annual=0 AND operating_profit<0; -- 25,429
```
- 분기 매출 음수: **1,149건**
  - `data_source NULL` 961건, `fnguide` 49건
  - 분기별: Q1 300 / Q2 93 / Q3 91 / Q4 526

주의:
- 영업이익 음수는 실제 가능(적자)이므로 오류 단정 불가.
- 하지만 매출 음수는 일반적으로 비정상이며, Q4 역산 로직 영향 가능성 큼.

#### 1-3. 매핑 프로파일 커버리지 부족
```sql
-- stock_universe 6자리 상장코드
SELECT COUNT(DISTINCT stock_code)
FROM stock_universe
WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';
-- 3,881

-- company_mapping_profile 활성 매핑 종목
SELECT COUNT(DISTINCT stock_code)
FROM company_mapping_profile
WHERE is_active=1
  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';
-- 351
```
- 전종목 대비 매핑 프로파일 적용 종목이 **351/3,881 (9.0%)** 수준.
- 종목별 DART id/account 매핑 “완벽” 기준과 큰 괴리.

#### 1-4. `financial_source_snapshot` 검증 상태
```sql
SELECT verification_status, COUNT(*)
FROM financial_source_snapshot
GROUP BY verification_status;
```
- unverified: **33,201**
- match: 9 / partial: 7 / mismatch: 4

의미:
- snapshot 저장은 되어 있으나 검증 상태 활용이 거의 안 되고 있음.

#### 1-5. 코드 상 충돌되는 정책 문구
- `scheduler.py`에 `FnGuide를 단일 권위 소스로 재무 수집·보정` 주석/설명 존재.
- `main.py` 다수 쿼리에서 `data_source='dart' THEN 0, 'fnguide' THEN 1` 우선 정렬 존재.
- `crud.py`에 `fnguide` 보호 로직 존재(특정 흐름에서 덮어쓰기 방지).

의미:
- 현재는 “합의 기반”이 아니라 “소스 우선 정렬 + 부분 보호”가 혼재.
- 사용자 기준과 불일치.

---

### 2) 틀린 부분(사용자 기준 대비)
1. **단일 소스 우선순위 설계 자체**
   - DART 우선/FnGuide 우선 어느 쪽이든 사용자 기준 위배.
2. **합의 점수 저장 구조 부재**
   - 레코드 단위로 source별 값/일치수/최종선택근거가 일관 저장되지 않음.
3. **출처 미기록 레코드 과다**
   - `financial_data` null/blank source가 다수라 lineage 불명.
4. **분기(Q) 레이어에서 이상치 관리 미흡**
   - revenue 음수 대량 잔존.
5. **종목 매핑 커버리지 부족**
   - `company_mapping_profile` 기준 전종목 매핑 미달.

---

### 3) Claude 재검증/수정 방향성 (필수)

#### Phase A. 합의 엔진 테이블 도입 (신규)
`financial_value_consensus` (제안)
- key: `stock_code, year, quarter, report_type, metric`
- columns:
  - `dart_value, fnguide_value, naver_value, seibro_value`
  - `dart_norm, fnguide_norm, naver_norm, seibro_norm` (단위 정규화)
  - `match_count` (최대 일치수)
  - `winner_source`
  - `winner_value`
  - `tie_flag`, `tie_break_rule`
  - `quality_grade` (A/B/C/D)
  - `computed_at`

#### Phase B. 최종 입력 규칙 변경
- `financial_data` 반영은 **winner_value만 입력**.
- data_source는 winner_source로 기록.
- 동률 처리 규칙(예시):
  1) 최근 공시일 소스 우선
  2) 과거 same-metric 일관성 높은 소스 우선
  3) 여전히 동률이면 `manual_review` 큐

#### Phase C. 기존 데이터 정리
1) `data_source NULL/blank` 전수 재평가:
- consensus 계산 가능한 행은 winner로 채우기.
- 불가한 행은 `needs_review=1` 플래그.
2) 분기 매출 음수 재검증:
- Q4 역산행은 annual/Q1~Q3 다시 계산 후 음수 유지 시 격리.

#### Phase D. 매핑 확장
- 목표: `company_mapping_profile` 활성 종목 351 → 최소 3,000+
- 우선순위:
  - KOSPI200/KOSDAQ150
  - 현재 보유/관심/시그널 대상
  - 나머지 전종목

---

### 4) 즉시 실행 SQL 점검셋 (Claude 재현용)

```sql
-- A) 출처 누락
SELECT COUNT(*) total,
       SUM(CASE WHEN data_source IS NULL OR TRIM(data_source)='' THEN 1 ELSE 0 END) null_source
FROM financial_data;

-- B) 분기 매출 음수 by source
SELECT COALESCE(NULLIF(TRIM(data_source),''),'(null)') src, COUNT(*) cnt
FROM financial_data
WHERE is_annual=0 AND revenue<0
GROUP BY src
ORDER BY cnt DESC;

-- C) 분기 매출 음수 by quarter
SELECT quarter, COUNT(*) cnt
FROM financial_data
WHERE is_annual=0 AND revenue<0
GROUP BY quarter
ORDER BY quarter;

-- D) 매핑 커버리지
SELECT COUNT(DISTINCT stock_code) total6
FROM stock_universe
WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';

SELECT COUNT(DISTINCT stock_code) mapped6
FROM company_mapping_profile
WHERE is_active=1
  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]';

-- E) snapshot 검증 상태
SELECT verification_status, COUNT(*) cnt
FROM financial_source_snapshot
GROUP BY verification_status
ORDER BY cnt DESC;
```

---

### 5) 제안 결과물(Claude가 남겨야 할 산출물)
1. `docs/claude_reverify_consensus_progress_YYYYMMDD.md`
   - 일별 진행률, 오류 케이스 샘플 30개
2. `scratch/consensus_mismatch_top500.csv`
   - metric별 불일치 상위 500
3. `scratch/mapping_coverage_gap.csv`
   - 미매핑 종목 리스트 + 예상 원인
4. `sql/migrations/2026xxxx_add_financial_value_consensus.sql`
5. `scripts/rebuild_financial_consensus.py`

---

### 6) 결론
- 현재 상태는 “완료” 아님.
- 사용자 기준(4소스 합의 기반 입력)으로 가려면,
  - 단일소스 우선 로직 제거,
  - 합의 테이블/점수화 도입,
  - source 누락 레코드 정리,
  - 종목 매핑 커버리지 확장
  이 4축이 필수.
