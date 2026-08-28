# 분기 재무제표 자동 검증/보강 로직 (Q2 이후 공통 적용)

작성일: 2026-05-26
스크립트: `/Applications/stock_dashboard/scripts/ops/sync_quarterly_verified.py`

## 목적
- 26.1Q처럼 분기 데이터가 비어 있거나 불일치할 때, **채우기보다 정확성 우선**으로 검증 후 반영.
- 향후 26.2Q/26.3Q/26.4Q에도 동일 로직 재사용.

## 핵심 규칙
1. DART 키 로테이션: `DART_API_KEY` → `DART_API_KEY2` → `DART_API_KEY3`
2. `finstate_all`에서 CFS 우선, 없으면 OFS 시도
3. 매출/영업이익/순이익 중 2개 이상 유효값일 때만 반영
4. `financial_data`는 UNIQUE 키 기준 업서트
   - `(stock_code, year, quarter, is_annual, report_type)`
5. 변경 이력은 `financial_fix_log`에 기록
   - `fix_rule`: `DART_VERIFIED_QUARTER_INSERT` / `DART_VERIFIED_QUARTER_SYNC`
6. 출처 추적
   - `data_source`: `dart_key1_verified` / `dart_key2_verified` / `dart_key3_verified`

## 실행 예시
### 상세분석 연동 종목 대상 (권장)
```bash
python3 /Applications/stock_dashboard/scripts/ops/sync_quarterly_verified.py --scope detailed --year 2026 --quarter 2
```

### 전 종목 대상
```bash
python3 /Applications/stock_dashboard/scripts/ops/sync_quarterly_verified.py --scope all --year 2026 --quarter 2
```

### 최신 공시 분기 자동 판정
```bash
python3 /Applications/stock_dashboard/scripts/ops/sync_quarterly_verified.py --scope detailed
```

## 이번 실행(2026Q1) 결과
- 실행: `--scope detailed --year 2026 --quarter 1`
- 요약: `target=33 inserted=0 updated=10 unchanged=23 no_dart=0 remaining_missing=0`
- 의미: 상세분석 연동 종목 기준 26.1Q 누락 0건 상태로 정합화.

## 관련 수정
- `/Applications/stock_dashboard/data_collector.py`
  - 저장 시 `report_type`, `data_source` 전달
  - ingest 응답 `raise_for_status()`로 저장 실패 은닉 방지
- `/Applications/stock_dashboard/schemas.py`
  - `FinancialIngest`에 `report_type`, `data_source` 필드 추가

