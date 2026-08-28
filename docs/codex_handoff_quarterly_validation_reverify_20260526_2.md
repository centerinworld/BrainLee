# Codex 재검증 핸드오프 (Quarterly Validation)
작성일: 2026-05-26
대상: Claude 검토용
DB: `/Applications/stock_dashboard/stock.db`
검증 테이블: `fin_quarterly_validation_flags`

## 1) 재검증 범위
- 기존 문서 `docs/codex_handoff_quarterly_validation_20260526.md`의 핵심 주장 재확인
- check_type별 상태 분포, STRUCTURAL 분류 타당성, CLOSE_MATCH 과허용 여부, OPEN 잔여 성격 점검
- P0/P1/P2 의심 구간 실제 상태 조회

## 2) 핵심 결과 요약
- 전체적으로 기존 문서와 **대부분 일치**.
- 단, `QUARTERLY_4WAY OK`가 기존 문서(187,636) 대비 현재 DB는 **187,640(+4)**.
- `AMBIGUOUS=0`는 5개 check_type 모두 유지.
- `source_count>=2 인 OPEN=0` 확인 완료.

## 3) check_type별 현황 (재조회)
```sql
SELECT check_type,
       COUNT(*) total,
       SUM(CASE WHEN status IN ('CONFIRMED','CLOSE_MATCH') THEN 1 ELSE 0 END) ok_cnt,
       SUM(CASE WHEN status='STRUCTURAL' THEN 1 ELSE 0 END) structural_cnt,
       SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_cnt,
       SUM(CASE WHEN status='AMBIGUOUS' THEN 1 ELSE 0 END) ambiguous_cnt
FROM fin_quarterly_validation_flags
GROUP BY check_type
ORDER BY check_type;
```
결과:
- ANNUAL_CONSISTENCY: 22,663 / OK 17,215 / STRUCTURAL 5,448 / OPEN 0 / AMBIGUOUS 0
- DART_FG_CROSS: 3,422 / OK 1,373 / STRUCTURAL 2,049 / OPEN 0 / AMBIGUOUS 0
- DART_NAVER_CROSS: 24,964 / OK 19,742 / STRUCTURAL 5,222 / OPEN 0 / AMBIGUOUS 0
- OFS_ANNUAL_CONSISTENCY: 34,513 / OK 16,461 / STRUCTURAL 18,052 / OPEN 0 / AMBIGUOUS 0
- QUARTERLY_4WAY: 313,145 / OK **187,640** / STRUCTURAL 79,493 / OPEN 46,012 / AMBIGUOUS 0

## 4) HIGH 재점검 결과

### 4-1. STRUCTURAL_CFS_OFS_BS_SCOPE가 BS 전용인지
```sql
SELECT field, COUNT(*)
FROM fin_quarterly_validation_flags
WHERE status='STRUCTURAL' AND notes LIKE '%STRUCTURAL_CFS_OFS_BS_SCOPE%'
GROUP BY field;
```
결과:
- total_assets: 10,628
- total_equity: 8,435

판정: **정상(요청 조건 충족)**

### 4-2. STRUCTURAL_DART_UNAVAILABLE에 dart_value 존재 여부
```sql
SELECT COUNT(*)
FROM fin_quarterly_validation_flags
WHERE status='STRUCTURAL'
  AND notes LIKE '%STRUCTURAL_DART_UNAVAILABLE%'
  AND dart_value IS NOT NULL;
```
결과: 0

판정: **정상(오분류 없음)**

### 4-3. STRUCTURAL_CFS_OFS_SCOPE 중 저차이(<10%) 존재 여부
```sql
SELECT CASE
         WHEN diff < 0.10 THEN '<10%'
         WHEN diff < 0.30 THEN '10-30%'
         ELSE '>=30%'
       END bucket,
       COUNT(*)
FROM (
  SELECT ABS(dart_value-ofs_value)
         / CASE WHEN ABS(dart_value)>ABS(ofs_value)
                THEN ABS(dart_value) ELSE ABS(ofs_value) END AS diff
  FROM fin_quarterly_validation_flags
  WHERE status='STRUCTURAL'
    AND notes LIKE '%STRUCTURAL_CFS_OFS_SCOPE%'
    AND dart_value IS NOT NULL AND ofs_value IS NOT NULL
    AND ABS(dart_value)>0 AND ABS(ofs_value)>0
) t
GROUP BY bucket;
```
결과:
- <10%: 0
- 10-30%: 3,862
- >=30%: 19,293

판정: **현재 규칙상 즉시 재분류 필요성 낮음**

## 5) CLOSE_MATCH 품질(특히 BS) 재점검
```sql
SELECT field,
       CASE
         WHEN diff < 0.05 THEN '<5%'
         WHEN diff < 0.15 THEN '5-15%'
         WHEN diff < 0.30 THEN '15-30%'
         ELSE '>30%'
       END AS diff_range,
       COUNT(*)
FROM (
  SELECT field,
         ABS(dart_value-ofs_value)
         / CASE WHEN ABS(dart_value)>ABS(ofs_value)
                THEN ABS(dart_value) ELSE ABS(ofs_value) END AS diff
  FROM fin_quarterly_validation_flags
  WHERE check_type='QUARTERLY_4WAY'
    AND status='CLOSE_MATCH'
    AND field IN ('total_assets','total_equity')
    AND dart_value IS NOT NULL AND ofs_value IS NOT NULL
    AND ABS(dart_value)>0 AND ABS(ofs_value)>0
) x
GROUP BY field, diff_range
ORDER BY field, diff_range;
```
요약:
- total_assets: <5%(22,095), 5-15%(8,161), 15-30%(3,258), >30%(4,643)
- total_equity: <5%(25,435), 5-15%(8,074), 15-30%(2,659), >30%(3,369)

판정:
- `>30%`가 적지 않음(자산 4,643 / 자본 3,369).
- 현재 CLOSE_MATCH가 “절대차이”가 아닌 “역사적 비율 안정성” 기반인 것으로 보이며,
  **회계구조 안정 종목에는 허용 가능**하나 프론트 신뢰표시(완전검증)에는 보수적 등급 반영 권장.

## 6) MED 재점검

### 6-1. ANNUAL_CONSISTENCY STRUCTURAL 'OTHER' 성격
```sql
SELECT notes, COUNT(*) cnt
FROM fin_quarterly_validation_flags
WHERE check_type='ANNUAL_CONSISTENCY'
  AND status='STRUCTURAL'
  AND notes NOT LIKE '%CROSS_SOURCE%'
  AND notes NOT LIKE '%ONETIME%'
  AND notes NOT LIKE '%Q4_LARGE%'
  AND notes NOT LIKE '%QUARTERLY_VS%'
  AND notes NOT LIKE '%ANNUAL_QUALITY%'
GROUP BY notes
ORDER BY cnt DESC
LIMIT 20;
```
핵심:
- `3분기합 비율 낮음: 1.000` 436건 등, notes 표준화 미흡 패턴 다수.

권고:
- notes 프리텍스트를 코드형(`STRUCTURAL_*`)으로 정규화 필요.

### 6-2. OFS_ANNUAL_CONSISTENCY PARTIAL_QUARTERS 연도 분포
- 2019~2022에 집중, 2023 이후 급감.
- OFS 분기 수집 커버리지 확대 시 CLOSE_MATCH/CONFIRMED 전환 여지 큼.

### 6-3. DART_FG_CROSS STRUCTURAL 사유
```sql
SELECT CASE
         WHEN notes LIKE '%STRUCTURAL_PERIOD_MISMATCH%' THEN 'PERIOD_MISMATCH'
         WHEN notes LIKE '%STRUCTURAL_REVENUE_DIFF%' THEN 'REVENUE_DIFF(금융업)'
         WHEN notes LIKE '%STRUCTURAL_OFS_CFS%' THEN 'OFS_CFS'
         WHEN notes LIKE '%STRUCTURAL_CURRENCY%' THEN 'CURRENCY'
         WHEN notes LIKE '%STRUCTURAL_SCOPE%' THEN 'SCOPE'
         WHEN notes LIKE '%DART_CFS_PARTIAL%' THEN 'DART_CFS_PARTIAL'
         WHEN notes LIKE '%AUTO_DART_PRIORITY%' THEN 'AUTO_DART_PRIORITY'
         ELSE 'OTHER'
       END reason,
       COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='DART_FG_CROSS' AND status='STRUCTURAL'
GROUP BY reason
ORDER BY COUNT(*) DESC;
```
결과:
- OTHER 1,305
- PERIOD_MISMATCH 744

판정:
- 분류코드 미정규화(OTHER)가 큼. 코드화 필요.

## 7) P0/P1/P2 관찰
- P0(008700/308080 CFS=OFS 완전일치 오분류) 문구는 재점검 필요.
  - 008700 샘플 조회 시 다수는 CLOSE_MATCH/STRUCTURAL/OPEN 혼재이며,
    “완전일치 4건” 단정은 현재 DB 단면에서 즉시 재현되지 않음.
- P1(BS CLOSE_MATCH >30% 8,012건) 취지는 타당.
  - 재조회 합계(>30%): 4,643 + 3,369 = 8,012로 일치.
- P2(DART_FG_CROSS OTHER / ANNUAL_CONSISTENCY OTHER) 추가 분석 필요성 유효.

## 8) Claude 후속 액션(우선순위)
1. **P0 재현 쿼리 명확화**: 008700/308080의 “완전일치인데 STRUCTURAL” 판정 기준 SQL 명시 및 재현.
2. **CLOSE_MATCH 등급 분리**: BS `>30%`는 `CLOSE_MATCH_LOOSE` 같은 별도 상태/등급으로 분리 검토.
3. **notes 정규화**: ANNUAL_CONSISTENCY / DART_FG_CROSS의 OTHER를 코드형으로 치환.
4. **프론트 표기 보수화**: "완전 검증" 뱃지에 BS high-diff 케이스 감점 반영.

## 9) 참고
- 기존 문서: `docs/codex_handoff_quarterly_validation_20260526.md`
- 본 재검증 문서: `docs/codex_handoff_quarterly_validation_reverify_20260526_2.md`
