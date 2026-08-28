# Codex Handoff — Quarterly Resume/Validation (2026-05-27)

## 1) 이번 세션에서 Codex가 직접 수행한 수정

### 1-1. 재실행 오케스트레이션 스크립트 신규 추가
- 파일: `/Applications/stock_dashboard/scripts/ops/resume_quarterly_pipeline_20260527.sh`
- 목적:
  - 중단/토큰 종료 이후에도 **수집 → 검증 → 요약**을 1회 명령으로 재개
  - 실행 전/후 `QUARTERLY_4WAY OPEN`, 전체 `AMBIGUOUS` 수치 자동 기록
  - 결과를 요약 텍스트로 저장
- 동작 순서:
  1. `collect_dart_annual_2016_2018.py` resume
  2. `collect_quarterly_smallcap.py` resume
  3. `collect_quarterly_2019_2021.py` resume
  4. `run_daily_validation.sh` 실행
  5. before/after 지표 및 check_type별 집계 저장

### 1-2. 파이프라인 즉시 실행
- 실행 명령: `bash /Applications/stock_dashboard/scripts/ops/resume_quarterly_pipeline_20260527.sh`
- 실행 로그: `/Applications/stock_dashboard/scratch/resume_quarterly_pipeline_20260527_230650.log`
- 요약 파일: `/Applications/stock_dashboard/scratch/resume_quarterly_pipeline_20260527_230650_summary.txt`

---

## 2) 채워진/반영된 데이터 현황 (검증 완료 기준)

## 2-1. 연간 CFS (2016~2018) 백필 결과
- 소스: `dart_annual_backfill`
- 누적 반영: **3,557건**
- 참고 쿼리:
  - `SELECT data_source, COUNT(*) FROM financial_data WHERE year BETWEEN 2016 AND 2018 AND quarter=0 GROUP BY data_source;`

## 2-2. 이번 재실행에서 추가 삽입 여부
- annual resume: 잔여 없음 → **삽입 0**
- smallcap quarterly resume: 잔여 없음 → **삽입 0**
- 2019~2021 quarterly resume: 잔여 없음 → **삽입/수정 0**
- OFS import 단계: 신규 삽입 대상 399건 처리 (중복 제외 후 반영)

## 2-3. 검증 플래그 최종 상태 (23:07 기준)
- `QUARTERLY_4WAY`: 총 419,725 / OK 237,727 (56.6%) / **OPEN 53,226** / AMBIGUOUS 0
- `ANNUAL_CONSISTENCY`: 총 32,162 / OK 23,693 (73.7%) / OPEN 0 / AMBIGUOUS 0
- `DART_FG_CROSS`: 총 3,422 / OK 1,373 (40.1%) / OPEN 0 / AMBIGUOUS 0
- `DART_NAVER_CROSS`: 총 27,929 / OK 22,343 (80.0%) / OPEN 0 / AMBIGUOUS 0
- `OFS_ANNUAL_CONSISTENCY`: 총 34,713 / OK 16,612 (47.9%) / OPEN 0 / AMBIGUOUS 0

## 2-4. BS 교차검증 상태
- `BS total_assets`: OK 41,985 / 83,945 (**50.0%**)
- `BS total_equity`: OK 43,691 / 83,945 (**52.0%**)

---

## 3) 이번 실행의 핵심 결론

1. **AMBIGUOUS는 전 check_type에서 0 유지**
2. 현재 병목은 `OPEN`보다는 `STRUCTURAL` 비중이 높은 구간(BS/OFS 성격 차이)
3. `QUARTERLY_4WAY OPEN=53,226`는 이번 1회 재실행에서 추가 감소 없음
   - 이유: 이번 회차에서 수집 대상 잔여가 거의 0이었고, 이미 resume 상태가 끝난 케이스

---

## 4) Claude가 바로 이어서 할 작업 (우선순위)

## P0. OPEN 실감축 대상 재탐색 SQL 재생성
- 기존 resume 대상(소형주/2019~2021) 소진 상태이므로,
- `source_count=1` + `2023~2024` + `revenue/op_profit/net_income` 중심으로 **신규 타깃 큐 재산출** 필요.

## P1. BS 50%대 개선 전용 배치
- `field in ('total_assets','total_equity')`의 OPEN/STRUCTURAL만 별도 분리
- `cfs_q4_from_annual` + `dart_quarterly_backfill` + `dart_ofs_backfill` 재매핑 우선순위 재검증

## P2. STRUCTURAL 코드 세분화
- 현재 STRUCTURAL 합계가 커서 실제 개선 가능분과 구조 불가분이 섞여 보임
- `STRUCTURAL_SCOPE_DIFF`, `STRUCTURAL_NI_ATTRIBUTION`, `STRUCTURAL_CUMULATIVE`, `STRUCTURAL_MISSING_SOURCE`로 강제 분리 집계 권장

## P3. 자동 스케줄 적용
- `resume_quarterly_pipeline_20260527.sh`를 일 1회/수동 트리거용으로 등록하면
  - 토큰 중단/세션 중단 시에도 동일한 재개 경로 확보 가능

---

## 5) 재현 명령

```bash
bash /Applications/stock_dashboard/scripts/ops/resume_quarterly_pipeline_20260527.sh
```

실행 후 확인:
- `/Applications/stock_dashboard/scratch/resume_quarterly_pipeline_*_summary.txt`
- `/Applications/stock_dashboard/scratch/resume_quarterly_pipeline_*.log`

---

## 6) 참고 로그/파일
- annual collector log: `/tmp/dart_annual_2016_2018_20260527_121915.log`
- daily validation log: `/tmp/daily_validate_20260527_223935.log`
- latest run summary: `/Applications/stock_dashboard/scratch/resume_quarterly_pipeline_20260527_230650_summary.txt`
- progress files:
  - `/Applications/stock_dashboard/scratch/.dart_annual_2016_2018_progress.json`
  - `/Applications/stock_dashboard/scratch/.smallcap_quarterly_progress.json`
  - `/Applications/stock_dashboard/scratch/.quarterly_2019_2021_progress.json`

