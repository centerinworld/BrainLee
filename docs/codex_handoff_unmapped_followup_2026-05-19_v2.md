# Telegram Unmapped 후속 제안 (Codex v2)

작성일: 2026-05-19 (KST)
원칙: 아래는 확정 결론이 아니라, **"이렇게 보이며 Claude 재검증이 필요"**한 제안입니다.

## 1) 이번 재검증에서 실제 수행한 것

1. `hs_trade_lab/scripts/backfill_telegram_posts.py` 재검토 및 보강
- 상장사 fallback 로드 추가 (`stock.db`의 `stock_universe/stock_meta/stock_price_daily`)
- 회사 alias 보강: `에스테아이→에스티아이`, `SEMES→세메스`, `지앤비에스 에코→지앤비에스에코`
- company-scope 판정 완화: title/품목 근거가 명확하면 회사사전 지연 시에도 mapped 허용
- 반복 상위 제목들에 `TITLE_ALIAS_MAP` 추가 (EV Relay/CCL/리드프레임/PR박리액/CCSS 등)

2. 실행
- `python3 hs_trade_lab/scripts/backfill_telegram_posts.py`
- `python3 hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py`

## 2) 수치 변화

### cache 기준 (telegram_post_cache)
- 이전(Claude 조치 후 보고): unmapped `714`
- 이번 재실행 후: unmapped `657`
- 추가 개선: `-57`

### backfill 단일 실행 summary(최근 fetch window)
- 1차 재실행: `unmapped 116 / mapped 1008`
- 2차 alias 보강 후: `unmapped 103 / mapped 1021`

주의:
- fetch window(최근 페이지) 중심 결과라, 전체 누적 DB와 1:1로 동일하진 않음.

## 3) 이번에 확인된 핵심 병목 (중요)

`backfill`의 `mapping_status`보다, 실제 downstream에서 더 큰 병목은
`rebuild_telegram_flow_mappings.py`의 `missing_hs` 상위 군집으로 보입니다.

상위 예시(반복):
- `씨큐브 : 진주광택안료 ...`
- `상신이디피 : 2차전지 원형/각형 팩 모듈 ...`
- `해성디에스 : Package Substrate + 리드프레임 ...`
- `엘티씨 : 유기혼합용제와 시너 / PR박리액 ...`
- `에스티아이/에스테아이 : CCSS ...`
- `파크시스템스 : 산업용자동화원자현미경 ...`
- `변환기/변압기` 계열
- `NCA`, `OLED 제조용` 계열

즉, **post cache는 mapped여도 flow HS 추론이 비거나 약한 케이스**가 누적되는 것으로 보입니다.

## 4) Claude에게 제안 (재검증 필요)

1. `rebuild_telegram_flow_mappings.py`의 `HS_ALIASES`를 중심으로 보강하는 것이 더 효율적으로 보임
- `진주광택안료`, `CCSS`, `Package Substrate`, `PR박리액`, `유기혼합용제와 시너`, `변환기`, `변압기(전압별)`, `영구자석` 등
- 현재는 `TITLE_ALIAS_MAP`에 의존하는 부분이 커서, flow 단계에서 다시 누락되는 듯 보임

2. `parse_flow_and_product()`에서 제품명 추출 시 회사 prefix 제거 규칙 재점검
- `A/B: 품목` + 복합 괄호/국가 suffix 혼합 케이스
- `core_title`은 backfill에선 효과가 있었으나 rebuild 단계에도 동일한 정규화 체인 공유가 필요해 보임

3. 잔여 unmapped 분류를 3종으로 분리하는 것이 맞아 보임
- `alias-miss` (동의어/표기 변형)
- `hs-miss` (품목은 추출됐으나 hs alias 없음)
- `noise/skip` (품목합/요약성 문구)

## 5) 산출물
- `/Applications/stock_dashboard/scratch/telegram_unmapped_after_patch_2026-05-19_r4.tsv`
- 본 문서: `/Applications/stock_dashboard/docs/codex_handoff_unmapped_followup_2026-05-19_v2.md`

