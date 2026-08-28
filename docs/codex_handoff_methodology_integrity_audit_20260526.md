# Codex 방법론/로직/무결성 감사 핸드오프
작성일: 2026-05-26
대상: Claude
범위: 분기 재무 4중 검증 파이프라인(로직 자체 감사)
DB: `/Applications/stock_dashboard/stock.db`

---
## 요약 결론
이번 감사는 "결과 숫자"가 아니라 "검증 방법"을 점검했다. 결론적으로, 현재 파이프라인은 운영 가능하나 **판정 메타 무결성 및 분류 로직에 구조적 결함**이 있다.

- P0: 금융업 예외 로직 버그(실질 미작동)
- P0: self-consistency가 단일소스 근거로 CLOSE_MATCH를 대량 생성(source_count와 상태 의미 불일치)
- P1: source_count와 실제 소스 컬럼 불일치 3,478건
- P1: OFS-only 분기키 누락 가능성(검증 대상 집합 편향)
- P2: 상태/노트 업데이트가 append·덮어쓰기 혼재(추적 가능성 저하)

---
## 1) P0 — 금융업 예외 로직 실질 미작동
### 증거
파일: `scratch/fin_quarterly_4way_validate.py`

```python
# classify()
if is_financial and 'revenue' in str(available):
    return 'STRUCTURAL', ...
```

`available`은 `{'DART':값, 'FnGuide':값...}` 형태이므로 문자열에 `'revenue'`가 포함될 이유가 거의 없다.
즉, 금융업 revenue 구조차이 예외가 의도대로 동작하지 않는다.

### 영향
- 금융업 revenue가 일반 제조업과 동일 규칙으로 판정될 수 있음.
- STRUCTURAL 처리 의도가 누락되어 CONFIRMED/CLOSE/AMBIGUOUS 분포 왜곡 가능.

### 조치 제안
- `classify()`에 `field` 인자를 명시 전달하고 `if is_financial and field=='revenue'`로 수정.
- 수정 후 금융업 샘플군 재검증(전/후 status delta 비교) 필수.

---
## 2) P0 — 단일소스 CLOSE_MATCH 대량 발생 (의미 위배)
### 증거
`QUARTERLY_4WAY`에서 CONFIRMED/CLOSE_MATCH인데 source_count<2:
```sql
SELECT COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='QUARTERLY_4WAY'
  AND status IN ('CONFIRMED','CLOSE_MATCH')
  AND source_count<2;
-- 13,677건
```

구성:
```sql
SELECT source_count, status, COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='QUARTERLY_4WAY'
  AND status IN ('CONFIRMED','CLOSE_MATCH')
GROUP BY source_count, status;
```
- `source_count=1, CLOSE_MATCH = 13,599`
- notes 대부분: `QUARTERLY_SUM_MATCHES_ANNUAL`

원인 파일: `scratch/fin_quarterly_self_consistency.py`
- OPEN → CLOSE_MATCH 전환 시 source_count를 갱신하지 않음
- 연간/분기 자체일치(단일 계열)로 CLOSE_MATCH를 부여함

### 영향
- CLOSE_MATCH의 본래 의미(다중소스 근접일치)가 훼손됨.
- 프론트 "검증강도" 표현이 과대평가될 수 있음.

### 조치 제안
- self-consistency는 status를 `SELF_CONSISTENT`(신규) 또는 `OPEN`+`self_consistent=1`로 분리.
- 최소한 CLOSE_MATCH 유지 시 `validation_basis='self_consistency'` 필드 추가.
- source_count 재계산 배치 도입(아래 4번).

---
## 3) P1 — source_count 무결성 불일치 3,478건
### 증거
```sql
SELECT COUNT(*)
FROM fin_quarterly_validation_flags
WHERE source_count IS NOT NULL
  AND source_count != ((dart_value IS NOT NULL)
                    +  (fnguide_value IS NOT NULL)
                    +  (naver_value IS NOT NULL)
                    +  (ofs_value IS NOT NULL));
-- 3,478건
```

### 영향
- 상태판정 신뢰도(몇 소스로 검증했는지) 왜곡.
- 후속 rule(예: source_count>=2 조건) 오동작 가능.

### 조치 제안
- source_count를 저장값 의존하지 말고 조회시 계산 또는 nightly 보정:
```sql
UPDATE fin_quarterly_validation_flags
SET source_count = ((dart_value IS NOT NULL)
                 +  (fnguide_value IS NOT NULL)
                 +  (naver_value IS NOT NULL)
                 +  (ofs_value IS NOT NULL)),
    updated_at = datetime('now')
WHERE source_count IS NULL
   OR source_count != ((dart_value IS NOT NULL)
                    +  (fnguide_value IS NOT NULL)
                    +  (naver_value IS NOT NULL)
                    +  (ofs_value IS NOT NULL));
```

---
## 4) P1 — 검증 대상 집합 편향 (OFS-only 키 누락)
### 증거
파일: `scratch/fin_quarterly_4way_validate.py`
```python
all_keys = set(dart_data) | set(fg_data) | set(naver_data)
# ofs_data key는 union에 포함되지 않음
```

### 영향
- OFS만 존재하는 분기레코드가 검증 대상에서 누락될 수 있음.
- OPEN/STRUCTURAL 분포가 "실제 분포"보다 낙관적으로 보일 위험.

### 조치 제안
- `all_keys`에 `set(ofs_data)` 포함.
- 단, OFS-only는 곧바로 확정하지 말고 `OPEN(single_source:OFS)` 또는 `STRUCTURAL_OFS_ONLY_*` 정책 분리.

---
## 5) P2 — 노트/상태 추적 가능성 저하
### 관찰
- 일부 스크립트는 `notes=COALESCE(notes,'') || ' TAG'` append,
- 일부는 `notes='...'` 덮어쓰기.
- 전체 파이프라인이 다단 UPDATE 중심이며 `financial_fix_log` 연결키가 약함.

### 영향
- 어떤 규칙이 최종 status를 만들었는지 재현성 저하.
- 동일 레코드에 대해 날짜별 rule provenance 추적이 어려움.

### 조치 제안
- `validation_transition_log` 신설:
  - key: stock_code/year/quarter/field/check_type
  - old_status/new_status/rule_id/script_name/run_id/reason/ts
- notes는 표시용 요약, 진짜 근거는 transition_log로 관리.

---
## 6) 즉시 실행 가능한 개선 우선순위
1. `classify(field, ...)` 버그 수정 (금융업 revenue 예외 정상화)
2. self-consistency 결과를 CLOSE_MATCH에서 분리(`SELF_CONSISTENT`)
3. source_count 재계산 배치 1회 실행 + 파이프라인 후단 자동화
4. 4way key union에 OFS 포함
5. transition log 테이블 도입

---
## 7) Claude 검증 체크리스트
- [ ] 금융업 revenue status 재분포 전/후 비교
- [ ] source_count 불일치 3,478건 0건으로 감소 확인
- [ ] CLOSE_MATCH 중 source_count<2 건수 급감 여부
- [ ] OFS-only key 포함 후 OPEN/STRUCTURAL 분포 변화 측정
- [ ] transition_log 기반 재현성 테스트(샘플 20종목)

---
## 8) 참고 쿼리
```sql
-- A. CLOSE_MATCH/CONFIRMED 중 source_count<2
SELECT status, source_count, COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='QUARTERLY_4WAY'
  AND status IN ('CONFIRMED','CLOSE_MATCH')
GROUP BY status, source_count
ORDER BY source_count;

-- B. source_count 무결성
SELECT COUNT(*)
FROM fin_quarterly_validation_flags
WHERE source_count IS NOT NULL
  AND source_count != ((dart_value IS NOT NULL)
                    +  (fnguide_value IS NOT NULL)
                    +  (naver_value IS NOT NULL)
                    +  (ofs_value IS NOT NULL));

-- C. self-consistency로 생성된 CLOSE_MATCH 규모
SELECT notes, COUNT(*)
FROM fin_quarterly_validation_flags
WHERE check_type='QUARTERLY_4WAY'
  AND status='CLOSE_MATCH'
  AND notes='QUARTERLY_SUM_MATCHES_ANNUAL'
GROUP BY notes;
```
