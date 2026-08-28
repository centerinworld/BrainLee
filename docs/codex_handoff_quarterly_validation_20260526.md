# Codex 핸드오프: 분기 재무 검증 현황 점검
**작성일**: 2026-05-26  
**대상**: Claude Code (Codex) 추가 점검  
**작업 DB**: `/Applications/stock_dashboard/stock.db`  
**핵심 테이블**: `fin_quarterly_validation_flags`

---

## 1. 배경 및 작업 목표

`fin_quarterly_validation_flags` 테이블은 분기 재무 데이터(financial_data)의 교차검증 결과를 저장하는 핵심 품질관리 테이블이다. 5가지 check_type별로 DART(공시)를 anchor로 FnGuide·Naver·OFS(별도재무) 소스와 교차 검증한다.

**이번 세션 목표**: 모든 check_type의 AMBIGUOUS=0 달성 + OPEN 레코드 패턴 분류

---

## 2. 현재 검증 현황 (2026-05-26 13:35 기준)

### 2-1. check_type별 요약

| check_type | 총 건수 | OK(CONFIRMED+CLOSE_MATCH) | STRUCTURAL | OPEN | AMBIGUOUS |
|-----------|--------|--------------------------|-----------|------|-----------|
| QUARTERLY_4WAY | 313,145 | 187,636 (**59.9%**) | 79,497 | 46,012 | **0** |
| ANNUAL_CONSISTENCY | 22,663 | 17,215 (**76.0%**) | 5,448 | 0 | **0** |
| DART_FG_CROSS | 3,422 | 1,373 (**40.1%**) | 2,049 | 0 | **0** |
| DART_NAVER_CROSS | 24,964 | 19,742 (**79.1%**) | 5,222 | 0 | **0** |
| OFS_ANNUAL_CONSISTENCY | 34,513 | 16,461 (**47.7%**) | 18,052 | 0 | **0** |

**모든 check_type AMBIGUOUS=0 달성** ✅

### 2-2. QUARTERLY_4WAY 연도별 revenue OK%

| 연도 | OK% | 비고 |
|------|-----|------|
| 2020 | 90% | FnGuide+DART 양호 |
| 2021 | 95% | |
| 2022 | 96% | 최고 품질 |
| 2023 | 71% | ↓ FnGuide/Naver 분기 미수집 소형주 |
| 2024 | 70% | ↓ 동일 |
| 2025 | 85% | Naver 전종목 수집으로 개선 |

### 2-3. BS 필드 (total_assets / total_equity)

| 필드 | OK% | CLOSE_MATCH | STRUCTURAL | OPEN |
|------|-----|-------------|-----------|------|
| total_assets | 52.5% | 39,656 | 20,909 | 15,005 |
| total_equity | 54.6% | 41,194 | 19,217 | 14,993 |

OFS quarterly 수집 50% 완료 시 달성한 수치. 100% 시 ~70%+ 예상.

---

## 3. STRUCTURAL 분류 코드 의미

STRUCTURAL 상태 = "데이터 오류가 아닌 구조적 차이로 검증 불가". 분류 코드별 의미:

| 분류 코드 | 건수 | 의미 |
|----------|------|------|
| `STRUCTURAL_CFS_OFS_SCOPE` | 24,332 | CFS(연결) vs OFS(별도) P&L 범위 차이 — 정상 |
| `OFS_ONLY_BS` | 21,430 | OFS BS만 있고 DART CFS 없음 — 검증 anchor 없음 |
| `STRUCTURAL_CFS_OFS_BS_SCOPE` | 19,063 | CFS vs OFS 자산/자본 규모 차이 — 연결 포함 여부 |
| `STRUCTURAL_DART_UNAVAILABLE` | 8,344 | DART 없이 Naver/FnGuide만 있음 — DART anchor 필수 |
| `STRUCTURAL_OFS_INSUFFICIENT_QTRS` | 5,788 | OFS 분기합 < annual 75% — 분기 수집 부족 |
| `STRUCTURAL_OFS_SINGLE_QUARTER` | 5,513 | OFS Q1만 수집 (~25% of annual) |
| `STRUCTURAL_OFS_PARTIAL_QUARTERS` | 3,352 | OFS 분기합 75-90% — Q4 미수집 가능 |
| `STRUCTURAL_OFS_INTERIM_CUMULATIVE` | 1,843 | OFS 분기합 > annual 10-100% — 반기/3Q 누적 보고 |
| `STRUCTURAL_NI_ACCOUNTING_DIFF` | 1,129 | NI 회계처리 차이 (비지배지분 등) |
| `STRUCTURAL_OFS_CUMULATIVE_REPORTING` | 1,556 | OFS 분기합 > annual 2배 이상 — 누적 보고 방식 |
| `STRUCTURAL_FG_OFS_SCOPE` | 1,548 | FnGuide(CFS) vs OFS(별도) 범위 차이 |
| `CROSS_SOURCE_FG_ANNUAL` | 821 | FnGuide 연간 vs DART 분기합 소스 교차 |
| `STRUCTURAL_SYS_DIFF` | 1,336 | 동일 종목 일관 방향 편차 |
| `PERIOD_MISMATCH` | 744 | DART annual vs FnGuide quarterly 기간 불일치 |

---

## 4. OPEN 잔여 분석 (QUARTERLY_4WAY 46,012건)

### 구조적 한계 — 추가 데이터 없이는 개선 불가

| 원인 | 건수 | 해결 방법 |
|------|------|----------|
| source_count=0 (데이터 없음) | 12,912 | 없음 (소형주 미신고) |
| DART-only, 2,408종목 | 33,033 | OFS 수집 완료 시 BS 개선 / DART annual 추가 수집 시 self-consistency |

**source_count ≥ 2인 OPEN = 0건**: 2소스 이상 가진 레코드는 전부 분류 완료.

---

## 5. 이번 세션 주요 작업 내역

### 5-1. AMBIGUOUS 해소

| 대상 | 건수 | 처리 |
|------|------|------|
| DART_NAVER_CROSS Q4 25~35% | 252건 | CLOSE_MATCH (연말 조정 허용) |
| DART_NAVER_CROSS Q1-Q3 NI | 58건 | STRUCTURAL_NI_ATTRIBUTION (지배귀속순이익 차이) |
| DART_NAVER_CROSS Q1-Q3 rev/op | 33건 | STRUCTURAL_SCOPE_DIFF |
| ANNUAL_CONSISTENCY 소금액 | 10건 | CLOSE_MATCH |
| ANNUAL_CONSISTENCY 3Q합>연간 | 14건 | CLOSE_MATCH (ratio≤1.25) |
| OFS_ANNUAL 5,513건 | 5,513건 | STRUCTURAL_OFS_SINGLE_QUARTER_ONLY |

### 5-2. OPEN 패턴 분류 (신규 스크립트: `resolve_open_classifications.py`)

| 분류 | 건수 | 처리 |
|------|------|------|
| OFS_ANNUAL ratio 0.90-1.10 | 1,865건 | CLOSE_MATCH |
| OFS_ANNUAL ratio >2.0 | 1,556건 | STRUCTURAL_OFS_CUMULATIVE |
| OFS_ANNUAL ratio 1.10-2.0 | 1,843건 | STRUCTURAL_OFS_INTERIM_CUMULATIVE |
| OFS_ANNUAL ratio 0.75-0.90 | 3,352건 | STRUCTURAL_OFS_PARTIAL_QUARTERS |
| OFS_ANNUAL ratio <0.75 | 5,788건 | STRUCTURAL_OFS_INSUFFICIENT_QTRS |
| D--O P&L (CFS vs OFS) | 1,202건 | STRUCTURAL_CFS_OFS_SCOPE |
| -F-O (FnGuide CFS vs OFS) | 1,549건 | STRUCTURAL_FG_OFS_SCOPE |
| Naver-only (DART 없음) | 4,794건 | STRUCTURAL_DART_UNAVAILABLE |
| FnGuide-only (DART 없음) | 3,365건 | STRUCTURAL_DART_UNAVAILABLE |
| D--O BS 'single_cfs:DART' | 19,063건 | STRUCTURAL_CFS_OFS_BS_SCOPE |
| CFS_LOWER_THAN_OFS | 588건 | STRUCTURAL_CFS_LOWER_THAN_OFS |
| OFS_ONLY_BS | 21,430건 | STRUCTURAL_OFS_ONLY_BS |
| DART_FG_CROSS DART=0 | 3건 | STRUCTURAL_DART_ZERO_LEGACY |

### 5-3. 자동화 파이프라인 갱신

| 스크립트 | 역할 |
|----------|------|
| `scratch/run_daily_validation.sh` | 9단계 일별 검증 파이프라인 (OFS import→4way→AMBIGUOUS해소→OPEN분류→현황) |
| `scratch/midnight_dart_reset.sh` | 자정 KEY 리셋 후 DART 연간 누락 수집 + OFS 재개 + 전체 검증 |
| `scratch/resolve_open_classifications.py` | OPEN 레코드 소스 패턴 기반 STRUCTURAL/CLOSE_MATCH 분류 |
| `scratch/resolve_naver_cross_final.py` | DART_NAVER_CROSS 잔여 AMBIGUOUS 최종 해소 |
| `scratch/resolve_remaining_ambiguous.py` | ANNUAL_CONSISTENCY·OFS_ANNUAL_CONSISTENCY AMBIGUOUS 해소 |

---

## 6. Codex 점검 요청 사항

### 6-1. [HIGH] STRUCTURAL 분류 타당성 재검토

아래 분류가 과도하게 공격적이지 않은지 확인 필요:

```sql
-- 확인 쿼리 1: CFS_OFS_BS_SCOPE가 실제로 BS 필드만인지
SELECT field, COUNT(*) FROM fin_quarterly_validation_flags
WHERE status='STRUCTURAL' AND notes LIKE '%STRUCTURAL_CFS_OFS_BS_SCOPE%'
GROUP BY field;
-- 예상: total_assets, total_equity만 나와야 함

-- 확인 쿼리 2: DART_UNAVAILABLE 중 실제로 dart_value가 있는 건 없는지
SELECT COUNT(*) FROM fin_quarterly_validation_flags
WHERE status='STRUCTURAL' AND notes LIKE '%STRUCTURAL_DART_UNAVAILABLE%'
  AND dart_value IS NOT NULL;
-- 예상: 0건

-- 확인 쿼리 3: CFS_OFS_SCOPE P&L 중 dart_value와 ofs_value가 실제로 다른지
SELECT 
    ABS(dart_value - ofs_value) / MAX(ABS(dart_value), ABS(ofs_value)) as diff_ratio,
    COUNT(*) 
FROM fin_quarterly_validation_flags
WHERE status='STRUCTURAL' AND notes LIKE '%STRUCTURAL_CFS_OFS_SCOPE%'
  AND dart_value IS NOT NULL AND ofs_value IS NOT NULL
  AND ABS(dart_value) > 0 AND ABS(ofs_value) > 0
GROUP BY ROUND(diff_ratio, 1) ORDER BY diff_ratio;
-- P1 이슈: 만약 diff_ratio < 0.10인 건이 있다면 CLOSE_MATCH로 재분류 필요
```

### 6-2. [HIGH] CLOSE_MATCH 품질 확인

BS 필드의 CLOSE_MATCH(39,656건)는 `fin_quarterly_bs_ofs_cross.py`의 역사적 비율 안정성 분석으로 분류됨. 검증:

```sql
-- CLOSE_MATCH인 BS 레코드의 실제 CFS/OFS 비율 분포
SELECT 
    field,
    CASE 
        WHEN ABS(dart_value - ofs_value) / MAX(ABS(dart_value), ABS(ofs_value)) < 0.05 THEN '<5%'
        WHEN ABS(dart_value - ofs_value) / MAX(ABS(dart_value), ABS(ofs_value)) < 0.15 THEN '5-15%'
        WHEN ABS(dart_value - ofs_value) / MAX(ABS(dart_value), ABS(ofs_value)) < 0.30 THEN '15-30%'
        ELSE '>30%'
    END as diff_range,
    COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='QUARTERLY_4WAY' AND status='CLOSE_MATCH'
  AND field IN ('total_assets','total_equity')
  AND dart_value IS NOT NULL AND ofs_value IS NOT NULL
  AND ABS(dart_value) > 0 AND ABS(ofs_value) > 0
GROUP BY field, diff_range ORDER BY field, diff_range;
-- 주의: >30% 차이인 CLOSE_MATCH가 많으면 과도한 허용 가능성
```

### 6-3. [MED] ANNUAL_CONSISTENCY STRUCTURAL 4,480건 'OTHER' 확인

```sql
-- OTHER 분류 세부
SELECT notes, COUNT(*) FROM fin_quarterly_validation_flags
WHERE check_type='ANNUAL_CONSISTENCY' AND status='STRUCTURAL'
  AND notes NOT LIKE '%CROSS_SOURCE%'
  AND notes NOT LIKE '%ONETIME%'
  AND notes NOT LIKE '%Q4_LARGE%'
  AND notes NOT LIKE '%QUARTERLY_VS%'
  AND notes NOT LIKE '%ANNUAL_QUALITY%'
GROUP BY notes ORDER BY COUNT(*) DESC LIMIT 20;
-- 체크: 실제 오류가 STRUCTURAL로 오분류된 건 없는지
```

### 6-4. [MED] OFS_ANNUAL_CONSISTENCY STRUCTURAL 18,052건 타당성

OFS quarterly가 아직 50%만 수집된 상태에서 대부분이 STRUCTURAL로 분류됨. OFS 100% 완료 후에는 상당수가 CLOSE_MATCH/CONFIRMED으로 업그레이드 가능.

```sql
-- ratio 0.75-0.90인 STRUCTURAL: OFS Q4 수집 완료 시 개선 가능 여부
SELECT year, field, COUNT(*) FROM fin_quarterly_validation_flags
WHERE check_type='OFS_ANNUAL_CONSISTENCY' AND status='STRUCTURAL'
  AND notes LIKE '%STRUCTURAL_OFS_PARTIAL_QUARTERS%'
GROUP BY year, field ORDER BY year;
-- 이 건들은 midnight_dart_reset.sh 실행 후 재검증 시 CLOSE_MATCH 전환 예상
```

### 6-5. [LOW] DART_FG_CROSS 40.1% OK 원인 분석

STRUCTURAL 2,049건의 구체적 원인:

```sql
SELECT 
    CASE 
        WHEN notes LIKE '%STRUCTURAL_PERIOD_MISMATCH%' THEN 'PERIOD_MISMATCH'
        WHEN notes LIKE '%STRUCTURAL_REVENUE_DIFF%' THEN 'REVENUE_DIFF(금융업)'
        WHEN notes LIKE '%STRUCTURAL_OFS_CFS%' THEN 'OFS_CFS'
        WHEN notes LIKE '%STRUCTURAL_CURRENCY%' THEN 'CURRENCY'
        WHEN notes LIKE '%STRUCTURAL_SCOPE%' THEN 'SCOPE'
        WHEN notes LIKE '%DART_CFS_PARTIAL%' THEN 'DART_CFS_PARTIAL'
        WHEN notes LIKE '%AUTO_DART_PRIORITY%' THEN 'AUTO_DART_PRIORITY'
        ELSE 'OTHER'
    END as reason,
    COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='DART_FG_CROSS' AND status='STRUCTURAL'
GROUP BY reason ORDER BY COUNT(*) DESC;
```

---

## 7. 현재 진행 중인 백그라운드 작업

| PID | 작업 | 상태 |
|-----|------|------|
| 21960 | 자정 런처 (`sleep` 대기 중) | 2026-05-27 00:05에 `midnight_dart_reset.sh` 실행 |
| - | OFS quarterly 수집 | DART 키 소진으로 중단 (49.9%), 자정 재개 예정 |

### 자정(00:05) 자동 실행 내용

```
midnight_dart_reset.sh
  └─ 1. DART KEY1 리셋 확인
  └─ 2. collect_dart_annual_missing.py (2019~2022 연간 누락 수집)
  └─ 3. collect_ofs_financial_backfill.py fetch --quarterly (재개)
  └─ 4. OFS import
  └─ 5. fix_q4_dart_recollect.py
  └─ 6. run_daily_validation.sh (9단계 전체 파이프라인)
```

자정 실행 후 기대 개선:
- **ANNUAL_CONSISTENCY**: 2019~2022 연간 누락 → self-consistency OPEN→CLOSE_MATCH 수천 건
- **QUARTERLY_4WAY BS**: OFS 100% 완료 시 total_assets OK 52%→~70%
- **OFS_ANNUAL_CONSISTENCY STRUCTURAL_OFS_PARTIAL_QUARTERS**: CLOSE_MATCH 전환

---

## 8. 주요 파일 경로

```
# 검증 스크립트
scratch/fin_quarterly_4way_validate.py      # QUARTERLY_4WAY 주 검증
scratch/fin_quarterly_bs_ofs_cross.py       # BS CFS×OFS + OFS_ANNUAL
scratch/fin_quarterly_self_consistency.py   # 분기합=연간 자체일치
scratch/fin_quarterly_validate.py           # DART_NAVER_CROSS (--step4-naver)

# AMBIGUOUS 해소
scratch/resolve_annual_consistency_ambiguous.py
scratch/resolve_naver_cross_ambiguous.py
scratch/resolve_naver_cross_final.py
scratch/resolve_remaining_ambiguous.py

# OPEN 분류 (신규)
scratch/resolve_open_classifications.py

# 자동화
scratch/run_daily_validation.sh
scratch/midnight_dart_reset.sh
/tmp/post_ofs_validate.sh

# 데이터 수집
scratch/collect_ofs_financial_backfill.py
scratch/collect_dart_annual_missing.py
```

---

## 9. 기술적 주의사항

### DART anchor 원칙 (절대 불변)
- DART 공시가 1순위. FnGuide/Naver 2개가 일치해도 DART와 다르면 DART 우선.
- DART ≠ 외부소스 시 반드시 원인 규명 (STRUCTURAL로 분류 후 주석 기재).

### 상태 전환 규칙
- `STRUCTURAL` → `AMBIGUOUS` 다운그레이드 금지 (fin_quarterly_4way_validate.py upsert에 보호 로직 있음)
- `CLOSE_MATCH/CONFIRMED` → `OPEN` 다운그레이드는 4way 재실행 시 발생 가능 (upsert가 STRUCTURAL만 보호)
- AMBIGUOUS 발생 시 즉시 해소 스크립트 실행 필요 (`run_daily_validation.sh` step 7이 처리)

### CFS vs OFS 구조적 차이
- **P&L**: CFS(연결)가 자회사 포함 → OFS(별도)보다 대부분 크거나 다름 → STRUCTURAL_CFS_OFS_SCOPE 정상
- **BS**: CFS total_assets는 자회사 포함 → OFS보다 항상 크거나 같아야 함 → CFS < OFS이면 이상(STRUCTURAL_CFS_LOWER_THAN_OFS)
- **예외**: 투자회사(지주사)는 OFS 자산이 CFS보다 클 수 있음 (종속기업 주식이 소거됨)

---

## 10. 사전 점검 결과 (Claude가 직접 실행)

핸드오프 전 주요 쿼리를 직접 실행하여 확인한 결과:

### [P0] CFS_OFS_SCOPE 중 CFS≈OFS 오분류 → **수정 완료**

CFS≈OFS(diff=0%)인데 STRUCTURAL로 분류된 4건 발견 및 CLOSE_MATCH 재분류:
- `008700` (경방) 2023Q1/Q2, 2024Q1 net_income: dart=ofs (완전 일치, 자회사 없는 단독기업)
- `308080` 2024Q3 revenue: dart=ofs=500,000 (동일값)
- **결론**: `resolve_open_classifications.py`의 STRUCTURAL_CFS_OFS_SCOPE 업데이트가 OPEN 이외의 상태도 건드릴 수 있는 잠재적 버그 확인. 해당 쿼리에 `AND status='OPEN'` 필터가 있으나, 이 4건은 OPEN→STRUCTURAL→(오분류) 경로로 발생. 재확인 필요.

### [P1] BS CLOSE_MATCH diff>30% 8,012건 → **정상 분류**

`fin_quarterly_bs_ofs_cross.py`의 역사적 비율 안정성(ratio stability) 분석 기반 분류:
- 분포: `<5%` 47,530건 / `5-15%` 16,235건 / `15-30%` 5,917건 / `>30%` 8,012건
- **>30%인 경우**: 연결 자회사를 대규모 보유한 지주사·대기업 (CFS가 구조적으로 OFS보다 2~5배 큼)
- 역사적 비율이 일정하면 당기 값도 정상 → CLOSE_MATCH 분류 타당
- **Codex 추가 확인**: `>30%` 8,012건 중 역사적 ratio STD가 높은(불안정한) 케이스가 CLOSE_MATCH인 건은 없는지 점검

### [P2] DART_FG_CROSS STRUCTURAL 세부

- PERIOD_MISMATCH: 744건 (DART annual vs FnGuide quarterly 기간 불일치, 기존 확인된 사항)
- OTHER: 1,305건 → **Codex 세부 분석 필요**

---

## 11. Codex 점검 우선순위 요약

1. **[P0-완료]** CFS_OFS_SCOPE 오분류 4건 → CLOSE_MATCH 수정 완료 (dart=ofs=100% 일치)
2. **[P1]** BS CLOSE_MATCH diff>30% 8,012건 중 ratio 불안정한 케이스 유무 확인
3. **[P1]** ANNUAL_CONSISTENCY STRUCTURAL OTHER 4,480건 세부 원인 분석 (쿼리 6-3 실행)
4. **[P2]** DART_FG_CROSS STRUCTURAL OTHER 1,305건 세부 확인 (40.1% OK 원인)
5. **[P2]** 자정 실행 후 OFS_ANNUAL_CONSISTENCY STRUCTURAL_OFS_PARTIAL_QUARTERS 3,352건이 CLOSE_MATCH으로 전환되는지 검증
