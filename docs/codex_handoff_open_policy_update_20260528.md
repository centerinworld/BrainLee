# Codex Handoff — OPEN 정책 업데이트 및 후속 (2026-05-28)

## 이번 세션 반영 사항

## 1) OPEN 판정 정책 반영
- 파일: `/Applications/stock_dashboard/scratch/resolve_open_classifications.py`
- 변경:
  - DB 경로 절대경로로 고정 (`/Applications/stock_dashboard/stock.db`)
  - 신규 함수 추가: `resolve_open_dart_one_external_match(conn)`
    - 정책: `DART + 외부 1소스 일치`면 OPEN이 아닌 `CLOSE_MATCH`
    - P&L 허용치: 15%
    - BS 허용치: 30%
  - `main()` 실행 순서에 위 함수를 `resolve_ofs_annual_consistency()` 다음에 삽입

## 2) BS 외부소스 보강 시도
- 신규 스크립트: `/Applications/stock_dashboard/scratch/collect_naver_bs_open.py`
- 목적: QUARTERLY_4WAY OPEN(BS) 대상 종목의 Naver BS(`total_assets`, `total_equity`) 보강
- 결과: Naver `item/main` 페이지에는 BS 항목이 없어 실효 데이터 생성 0건

---

## 현재 상태(실측)
- `QUARTERLY_4WAY`: total 419,725 / OK 60.3% / OPEN 53,226 / AMBIG 0
- OPEN 구성:
  - `open_dart_only`: 41,968
  - `open_source0`: 11,253
- 즉, OPEN의 대부분은 **외부소스 자체가 없음**

---

## 왜 정책 적용 후 즉시 수치가 안 올랐는가
- 신규 정책은 맞게 반영되었으나, 승격 조건(`DART + 외부1`)에 해당하는 OPEN row가 거의 0건
- 확인 쿼리 결과:
  - `pl_dart_fg=0`, `pl_dart_naver=0`, `bs_dart_fg=0`, `bs_dart_naver=0`

---

## Claude 후속 작업 (실행 우선순위)

## P0. 외부소스 실데이터 확보 배치 (필수)
- 대상: `open_dart_only` 41,968건에 해당하는 `(stock_code, year, quarter)`
- 권장: `fnguide_fill_financial()`를 소배치(예: 50종목) 반복 실행 후 commit
  - 현 상태는 종목당 호출이 느려 장시간 필요
  - 타임아웃/속도제한 고려해 `nohup + 진행률 로그` 구성 필요

## P1. FnGuide 파서 안전장치
- 파싱 오류 우려 대응:
  - 수집 직후 `dart_value`와 편차 검사(예: 300% 이상 값 reject)
  - 값 범위/단위 sanity check (억/원 변환 검증)
  - 실패 row는 `financial_fix_log`에 기록

## P2. OPEN 소스0(11,253) 분리 대응
- source0는 어떤 소스에도 값이 없는 상태
- 수집 큐를 별도로 분리하여 DART 재수집 우선, 이후 외부소스 보강

## P3. 프론트 표기
- "OPEN(검증미완료)"를 아래로 분리 표기:
  - `OPEN_DART_ONLY` (값은 있음, 외부소스 없음)
  - `OPEN_SOURCE0` (값 자체 없음)

---

## 검증
- `python3 -m py_compile` 통과:
  - `resolve_open_classifications.py`
  - `collect_naver_bs_open.py`
  - `ifrs_unified_mapping_revalidate.py`

