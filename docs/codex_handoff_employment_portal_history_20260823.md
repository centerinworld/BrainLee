# 고용 장기 이력 확장 핸드오프 (2026-08-23)

## 결론

- 기존 NPS API는 임의의 오래된 `dataCrtYm` 조회를 지원하지 않고 최근 약 12개월 스냅샷만 반환한다.
- 공공데이터포털의 국민연금 가입 사업장 월별 원본 127개를 이용하면 실데이터 기준 2015-11부터 확장할 수 있다.
- 근로복지공단 고용·산재보험 가입 현황 연간 원본은 2015~2025 연속 자료와 2014 스냅샷을 제공한다. 포털의 2007 묶음 파일에는 실제 2001~2007년 자료가 포함돼 있다.
- 월별 API와 공식 파일은 집계 범위가 다르므로 기존 테이블을 덮어쓰지 않는다. 공식 장기 이력은 별도 테이블과 별도 API 응답 필드로 유지한다.

## 구현

- `employment_monitor/backfill_nps_portal_history.py`
  - 공공데이터포털 과거 파일 자동 발견·다운로드·재개
  - 영어/한글 컬럼 스키마 자동 인식
  - 사업자번호 앞 6자리와 정규화 회사명을 함께 사용한 보수적 매칭
  - `nps_portal_monthly`, `nps_portal_imports`에 원천 추적 정보 저장
- `employment_monitor/backfill_wlb_portal_history.py`
  - 연간 파일 자동 발견·다운로드·재개
  - ZIP 분할 CSV 스트리밍 처리
  - 파일별 UTF-8/CP949 및 다년 묶음 파일의 연도 자동 판별
  - 10자리 사업자등록번호 정확 일치
  - 사업자번호가 빠진 예외 연도는 직전 검증 원본의 `사업장명+주소`가 단일 종목으로 연결될 때만 적재
  - `wlb_portal_annual`, `wlb_portal_imports`에 원천 추적 정보 저장
- `/api/employment-v2/chart`
  - 기존 `history`는 유지하고 공식 장기 월별 자료를 `portal_history`로 분리 반환
  - 사업장 수가 전월 대비 2배 이상 변하면 `scope_break=true`; 원값은 유지하고 집계 범위 변화로 표시
- `/api/employment-v2/annual-trend`
  - 공식 연간 자료를 `official_history`로 분리 반환
  - 보험·건설·다사업장 업종의 고용 범위 특성을 `employment_scope`로 반환
- `/api/employment-v2/quality`
  - 장기 이력의 행·기간·종목 커버리지와 음수/순증감 무결성 검사 포함

## 검증 근거

- NPS 15개월 겹침 구간(2025-04~2026-06): 공통 29,048종목-월, 신규취득 정확 일치 87.6%, 상실 정확 일치 87.7%.
- 차이의 주원인은 기존 API가 대표 사업장 1개를 추적하고 공식 파일은 동일 기업의 복수 사업장을 합산하기 때문이다.
- 2024 WLB 원본: 2,545,386행 중 7,005개 사업장을 2,506개 상장사에 10자리 사업자번호로 정확 연결.
- 2024 연간 원본과 2026-05 월별 API의 종목별 상시근로자 상관계수는 0.932. 기준시점과 산정 정의가 달라 값 자체는 동일하지 않다.
- 2020 묶음 원본에서 2016~2019 중복 멤버를 제외한 2020년 2,230,559행을 처리했다. 2019년 사업자번호 검증 교차표 5,274개로 4,763행·1,925종목을 연결했다.
- 2001~2007 묶음 원본 6,684,960행은 검증 교차표로 최종 22,595행·1,918종목을 연결했다.
- 최종 NPS 장기 테이블은 232,461행, 2015-11~2026-06 128개월 무공백, 2,115종목이다.
- 최종 WLB 장기 테이블은 52,513행, 2001~2025 25년 무공백, 2,508종목이다.
- 회귀 테스트: `python -m unittest tests.test_employment_data_quality tests.test_nps_portal_history tests.test_wlb_portal_history`

## 원본 및 복구

- 국민연금 원본: `/Volumes/Realtek_NVME/stock_dashboard/employment_history/nps_raw`
- 근로복지공단 원본: `/Volumes/Realtek_NVME/stock_dashboard/employment_history/wlb_raw`
- 사전 백업: `employment_monitor/employment.pre_portal_history_20260823.db`
- 최종 복구 백업: `/Volumes/Realtek_NVME/stock_dashboard/employment_history/employment.final_20260823.db`
- 원본 체크섬: `/Volumes/Realtek_NVME/stock_dashboard/employment_history/SHA256SUMS_20260823.txt` (141개 파일)
- 재실행은 이미 적재된 `source_detail_pk`를 건너뛰므로 중단 후 동일 명령으로 안전하게 재개된다.

```bash
/Applications/stock_dashboard/venv/bin/python employment_monitor/backfill_nps_portal_history.py \
  --from-label 201512 --to-label 202504 \
  --archive-dir /Volumes/Realtek_NVME/stock_dashboard/employment_history/nps_raw

/Applications/stock_dashboard/venv/bin/python employment_monitor/backfill_wlb_portal_history.py \
  --backfill --from-year 2015 --to-year 2025 \
  --archive-dir /Volumes/Realtek_NVME/stock_dashboard/employment_history/wlb_raw
```

## 사용 제한

- `portal_history`와 기존 `history`를 한 계열로 단순 연결하지 않는다.
- `scope_break=true`인 월과 대규모 취득·상실도 삭제하거나 무효 처리하지 않는다. 원값을 표시하고 집계 범위·사업모델 변화 표식을 함께 제공한다.
- 보험업의 피보험자 수에는 보험설계사·컨설턴트 등 정규직 외 영업인력이 포함될 수 있다. 이는 유효한 영업·고용 기반 정보이며 사업보고서 직접고용 인원과 동일한 지표로 해석하지 않는다.
- 직접고용 대비 큰 괴리는 `scope_difference`로 표시하되 추정값과 비교값을 유지한다. 업종 특성과 사업장 수 변화 없이 숫자만으로 이상치 판정을 내리지 않는다.
- WLB는 2001~2025 연속이지만 2001~2010·2020은 사업자번호가 없는 원본을 검증 사업장 교차표로 연결했으므로 `match_quality=identity_crosswalk` 구간의 장기 성장률은 보조 지표로만 취급한다.
- 현재 상장사 사업자번호를 기준으로 연결하므로 상장폐지 종목이 빠지는 생존편향이 있다. 과거 전략 백테스트의 종목선정 피처로 사용하지 않는다.
