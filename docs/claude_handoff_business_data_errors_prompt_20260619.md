# Claude 전달 프롬프트: 매입재료비/수주잔고/섹터별 매출 오류 수정 요청 (2026-06-19)

Claude, 아래 파일들을 먼저 읽고 수정 작업을 이어가세요.

## 반드시 읽을 파일

1. `/Applications/stock_dashboard/docs/codex_handoff_claude_business_data_audit_20260619.md`
2. `/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/summary.json`
3. `/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/*.csv`

## 현재 판정

Codex가 읽기 전용 전수 감사를 수행했습니다. DB는 수정하지 않았고, 오류 목록만 CSV/JSON으로 export했습니다.

핵심 결론:

- `dart_material_purchase` 연간 매입재료비 원천값은 sanity filter 후 사용 가능.
- `cost_structure.raw_material_ratio`는 753건이 `raw_material_cost / revenue`와 불일치합니다. 168건은 음수 또는 300% 초과입니다.
- `dart_cost_quarterly.material_cost_krw`는 678건의 소액/저신뢰 값과 490건의 연도/기수/퍼센트 컨텍스트 오파싱 의심값이 있습니다. 현재 전략 사용 금지입니다.
- `dart_backlog_quarterly`는 951건의 수주잔고 오파싱 의심값이 있습니다. 날짜, "해당사항 없음", "수주잔고는 의미가 없음", 소액값이 섞여 있습니다.
- `order_backlog.backlog_normalized`는 1,062건의 단위 불일치가 있습니다. 기존 값을 신뢰하지 말고 `backlog_amount / 1,000,000` 또는 명확한 새 기준으로 재계산해야 합니다.
- `segment_revenue`는 현재 DB 기준 `연결전체` 외 breakdown row가 0건입니다. 즉 기업별 매출 내 섹터/부문 매출 분리 작업은 완료되었다고 볼 수 없습니다.
- `segment_revenue`의 `연결전체` 행도 `financial_data.revenue`와 대조 불일치가 5,083건 있습니다. 이 값은 곧바로 삭제하지 말고 단위/중복/재무 데이터 자체 오류를 같이 점검하세요.

## 생성된 오류 파일

오류 디렉터리:

```text
/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/
```

주요 파일:

- `cost_structure_ratio_mismatch.csv`
- `cost_structure_ratio_out_of_range.csv`
- `dart_cost_quarterly_bad_values.csv`
- `dart_cost_quarterly_bad_context.csv`
- `dart_backlog_quarterly_bad_parse.csv`
- `order_backlog_unit_mismatch.csv`
- `order_backlog_bad_values.csv`
- `segment_revenue_consolidated_mismatch.csv`
- `raw_table_usage_in_code.csv`

## 수정 우선순위

1. `cost_structure` ratio repair dry-run 스크립트를 작성하세요.
   - `raw_material_ratio = raw_material_cost / revenue`
   - ratio가 `<0` 또는 `>3`이면 NULL 처리
   - `raw_material_yoy` 재계산
   - 적용 전 반드시 CSV diff 생성

2. `dart_cost_quarterly.material_cost_krw` 파서를 고치세요.
   - 연도/기수/퍼센트/날짜 숫자를 금액으로 저장하지 않도록 reject rule 추가
   - 테이블 헤더와 단위 검증 없이 정규식 숫자만 저장하지 않도록 수정
   - `confidence < 0.75` 또는 `material_cost_krw < 10,000,000`은 전략 제외

3. 수주잔고 파서를 고치세요.
   - "해당사항 없음", "의미가 없습니다", "수주잔고는 의미가 없음" 문구를 no_metric으로 분리
   - 날짜처럼 보이는 `2026.04`, `2026.01` 등을 금액으로 저장하지 않도록 reject
   - `backlog_normalized`를 전량 재계산하거나 legacy 컬럼으로 격리

4. `segment_revenue`를 재설계하세요.
   - 현재 테이블은 실제 sector/business segment revenue로 사용하지 마세요.
   - `fnlttSinglAcntAll` 손익계산서 계정 기반 수집을 부문 매출 파서로 사용하지 마세요.
   - DART 사업보고서 주석의 `영업부문`, `사업부문`, `제품과 용역`, `지역별 매출` 표에서 새로 추출하세요.
   - `source_table_title`, `raw_cell`, `unit_detected`, `unit_multiplier`, `amount_krw`, `segment_type`을 저장하세요.

5. 전략/백테스트가 dirty raw table을 직접 읽는 경로를 차단하세요.
   - raw table 대신 clean view만 사용하게 바꾸세요.
   - 후보 view 이름:
     - `v_material_purchase_clean`
     - `v_order_backlog_clean`
     - `v_segment_revenue_clean`

## 주의

이번 Codex 작업은 DB 수정 없이 오류 탐지와 인계 파일 생성만 했습니다. 수정 스크립트는 반드시 dry-run, backup, diff 검증 후 적용하세요. 이 데이터는 텐버거/시장 2배 전략 후보 신호로 쓰일 예정이므로, 잘못된 숫자를 "좋은 신호"로 학습하지 않게 raw table 직접 사용을 막는 것이 가장 중요합니다.
