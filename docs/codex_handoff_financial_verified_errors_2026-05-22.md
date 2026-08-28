# Codex → Claude 최종 핸드오프 (전수 검증 완료본)
작성일: 2026-05-22 (Asia/Seoul)

## 0) 사용자 기준 재확인 (절대 기준)
- DART/FnGuide/Naver/Seibro는 **입력 우선순위가 아님**.
- 1/2/3차 검증은 **4소스 일치 항목 수(합의점수)** 계산 단계.
- 최종 입력값은 **합의점수 최대값**을 채택해야 함.

---

## 1) 이번에 Codex가 실제로 끝낸 검증 범위
- DB 전수 점검: `financial_data`, `cash_flow_data`, `cf_validation_flags`, `financial_source_snapshot`, `company_mapping_profile`, `stock_universe`
- 주간 리포트 재생성:
  - `python3 scratch/weekly_revalidation.py --phase F`
  - 결과: `scratch/revalidation_report_20260522_212622.json`
- 오류군 상세 CSV 생성(아래 2장)

---

## 2) "틀린 부분" 상세 추출물 (Claude 바로 사용)
생성 폴더:
- `/Applications/stock_dashboard/scratch/full_reverify_20260522/`

### 2.1 분기 매출 음수 (강한 오류 후보)
- 파일: `quarterly_negative_revenue_details.csv`
- 건수: **1,015건**
- 특징:
  - Q4 집중(잔여 다수)
  - `data_source=(null)` 비중 높음

### 2.2 연간 DART vs FnGuide 매출 괴리 (구조/파싱/단위 의심)
- 파일: `annual_dart_vs_fnguide_revenue_outliers.csv`
- 건수: **634건** (ratio < 0.6 또는 > 1.8)
- 극단치 파일: `annual_revenue_extreme_under_0p1.csv`
  - ratio < 0.1 케이스 별도 분리

### 2.3 FIN_NAVER CLOSE_MATCH 상세
- 파일: `fin_naver_close_match_details.csv`
- 건수: **2,902건**
- 의미:
  - 확정 불일치는 아니지만 합의 불충분 구간
  - field별 후속 룰 필요(특히 revenue)

### 2.4 FIN_CROSS CLOSE_MATCH 상세
- 파일: `fin_cross_close_match_details.csv`
- 건수: **122건**

### 2.5 source lineage 결함 (최근년도)
- 파일: `financial_data_null_source_recent.csv`
- 건수: **24,727건** (2022+)
- 의미: 합의 엔진으로 판정 불가한 레코드 풀

### 2.6 연간 source null 상세
- 파일: `annual_null_source_details.csv`
- 건수: (파일 참조, annual 우선 복구 대상)

### 2.7 report_type 결함
- 파일: `financial_data_null_report_type_details.csv`
- 건수:
  - 전체 null report_type: **2,076건**
  - 분기 null report_type: **2,067건**

### 2.8 매핑 커버리지 결함
- 파일: `company_mapping_coverage_gaps.csv`
- 미매핑 종목: **3,530건**
  - (6자리 상장코드 기준 전수 미매핑 리스트)

### 2.9 매핑 품질 결함 (필수 key 미보유)
- 파일: `mapped_stocks_missing_required_keys.csv`
- 건수: **351건**
- 공통 패턴: `operating_cf, investing_cf, financing_cf, capex, depreciation` 미보유

### 2.10 요약 파일
- 파일: `summary.json`

---

## 3) DB 스냅샷 핵심 수치 (검증 시점)
- `financial_data` source null/blank: **70,598 / 107,837 (65.5%)**
- 분기 revenue<0: **~1,010~1,015건** (실행 시점 동적)
- 분기 Q4 revenue<0: **526건**
- 매핑 커버리지: **351 / 3,881 (9.0%)**
- `financial_source_snapshot.verification_status='unverified'`: 대량(기존 유지)

참고:
- 동시 작업/배치로 인해 분기 음수 건수는 실행 시점에 소폭 변동 가능.

---

## 4) Claude 수정 우선순위 (실행 순서 권장)

### P1. 데이터 구조 결함 복구
1. `report_type` null 2,076건 우선 정리
2. `source null` 연간→분기 순으로 lineage 복구

### P2. 매출 오류군 정리
1. `annual_revenue_extreme_under_0p1.csv` 우선 처리
2. `quarterly_negative_revenue_details.csv`에서 Q4 음수 우선 처리
3. revenue close-match 2,902건에서 금융업/지주사 구조 차이 룰 분리

### P3. 매핑 확장
1. `company_mapping_coverage_gaps.csv` 기반 매핑 확대
2. `mapped_stocks_missing_required_keys.csv` 필수키 채우기

---

## 5) 구현 원칙 (이번 기준에서 필수)
1. 최종값 선정은 source priority 금지
2. 합의점수(max agreement) 기반으로만 winner 확정
3. 동률이면 tie-break 규칙 문서화 + 기록
4. winner/value/source/evidence를 별도 테이블에 남길 것

---

## 6) 즉시 재현 명령
```bash
cd /Applications/stock_dashboard
python3 scratch/weekly_revalidation.py --phase F
python3 - <<'PY'
import json
from pathlib import Path
p=Path('scratch/full_reverify_20260522/summary.json')
print(json.loads(p.read_text()))
PY
```

---

## 7) Codex 최종 판단
- 이번 요청(“당신이 다 검증하고 틀린 것을 상세히 확인 후 넘겨라”) 기준으로,
  - **검증 완료**
  - **오류군 상세 파일 생성 완료**
  - **Claude가 바로 수정 가능한 입력자료 준비 완료**
- 다만 시스템 자체는 아직 “합의기반 입력체계 완성” 상태가 아니며, 위 P1~P3 수정이 필요함.

