# Codex 변경사항만 전달 (Claude 재검토용)

작성시각: 2026-05-24 10:44 KST

## 1) 프론트 수정
대상 파일: `/Applications/stock_dashboard/frontend/src/App.jsx`

### 반영 내용
1. 연간 재무제표 스크롤 강제 제거
- 기존: `overflowX:'scroll'` + `width:'max-content'`
- 변경: 연간 재무표는 `width:'100%'`, `tableLayout:'fixed'`로 렌더
- 목적: 연간표에 불필요한 가로 스크롤바 상시 노출 제거

2. 스크롤 초기 위치를 “최근값(우측)”으로 유지할 대상 한정
- 분기 재무표 / 분기 현금흐름표만 우측 끝으로 자동 이동
- 연간 재무표 / 연간 현금흐름표는 자동 우측 이동 제거

3. 우측 이동 안정화
- `requestAnimationFrame` + `setTimeout(40ms)` 2회 적용
- 목적: 첫 렌더 타이밍에서 우측 이동 누락 방지

### 빌드 확인
- 실행: `cd /Applications/stock_dashboard/frontend && npm run build`
- 결과: 성공

---

## 2) 데이터 보정 (삼성 포함)

### 실행 SQL 개요
- 연간(CFS) `capital_stock` NULL을 같은 연도 분기(CFS) 최신값으로 보정
- 모든 변경은 `financial_fix_log`에 기록
- run_id: `20260524_fill_annual_from_quarter`

### 결과
- 로그 적재: **4,364건**
- 삼성전자(005930):
  - 2016/2017 연간 `capital_stock` 채움 완료 (7,780.47억)

---

## 3) 삼성전자 아직 미채움인 이유 (근거)

- 2016/2017 CFS 연간/4분기의 `total_assets/total_liabilities/total_equity`는 DB 원천값 자체가 NULL
- 오늘(2026-05-24) DART 재조회 시도했으나 `status=020 사용한도 초과`로 실패
- 따라서 현재 시점에는 “안전한 추정 없이” 해당 3개 항목을 임의 채움할 수 없음

### 오늘 재조회 시도 결과
- 호출: `_collect_dart_to_db('005930', latest_only=False)`, `_collect_dart_cashflow('005930', latest_only=False)`
- 결과: `saved_fin=0`, `saved_cf=0`, DART 한도 초과

---

## 4) Claude가 이어서 볼 포인트 (수정사항만)

1. UI 렌더 확인
- 연간 재무표: 스크롤바 사라졌는지
- 분기 재무/분기 현금흐름: 진입 시 최신분기(우측)부터 보이는지

2. 삼성 미채움 잔여
- 대상: 2016/2017 CFS의 `assets/liabilities/equity`
- DART 한도 복구 후 재수집 우선
- 수집 성공 시 기존 4중 검증 스크립트 재실행

3. 로그 추적
- `financial_fix_log`에서 run_id `20260524_fill_annual_from_quarter` 검증

