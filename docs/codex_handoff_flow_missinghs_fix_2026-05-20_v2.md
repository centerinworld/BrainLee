# Telegram Flow 매핑 추가개선 핸드오프 v2 (for Claude)

작성일: 2026-05-20 (KST)
작성자: Codex

## 요약
이번 v2는 단순 전달이 아니라, **추가 분석 → 코드 수정 → 재실행 검증**까지 직접 진행한 결과입니다.
핵심 병목이던 `rebuild_telegram_flow_mappings.py`의 `missing_hs` 상위군을 추가로 흡수했습니다.

---

## 1) 추가로 실제 수정한 파일

- `/Applications/stock_dashboard/hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py`

### A. 회사 alias 추가 보강
- `에스테아이 -> 에스티아이`
- `SEMES -> 세메스`
- `지앤비에스 에코 -> 지앤비에스에코`
- `바우와우코리아 -> 오에스피`
- `삼성디플레이 -> 삼성디스플레이`

### B. HS alias 추가 보강 (상위 미매핑 제품군)
추가 키워드 예:
- `전자집적회로`
- `12인치 레이저마커 / 레이저 그루빙`
- `FPCB`, `부직포`, `NCF`
- `유도무기`, `로켓 발사기`, `레이더`
- 이전 단계에서 넣은 `CCSS`, `PR박리액`, `유기혼합용제와 시너`, `NCA`, `리드프레임`, `Package Substrate`, `Cap Assembly` 등과 결합

### C. 정규화 규칙(신규) 추가
- `PRODUCT_CANON_RULES` 도입
- `hs_entries_for_post()`에서 문자열 부분일치 기반 canonical label 확장
- 목적: 같은 의미의 문장 변형(예: 레이저마커/그루빙, NCF 표기 차이)을 HS lookup으로 연결

---

## 2) 재실행 결과 (v2)

실행:
```bash
python3 /Applications/stock_dashboard/hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py
```

산출:
- `/Applications/stock_dashboard/scratch/rebuild_telegram_flow_mappings_20260520_after_patch_v2.json`

핵심 수치:
- `flow_posts_with_hs_and_company`: **14,425**
- `evidence_rows`: **33,653**
- `inserted`: 23
- `updated`: 33,630

v1 대비 추가 개선:
- `flow_posts_with_hs_and_company`: `14,242 -> 14,425` (**+183**)
- `evidence_rows`: `33,002 -> 33,653` (**+651**)

초기(어제 기준 14,081) 대비 누적:
- `flow_posts_with_hs_and_company`: **+344**

---

## 3) 남은 불확실/추가개선 후보

현재 `missing_hs` 상위 잔존 항목:
1. `코미코(미코세라믹스)`
2. `양극활물질 ... 월별 수출 데이터 (전국)`
3. `반도체 조립용 인캡슐레이션(몰딩 장비)`
4. `수입`
5. `아이디피 : 카드프린터(+소모품)` 계열
6. `한화에어로스페이스 / 현대로템` (복수회사 요약형)

판단(확정 아님):
- 상위 잔존은 **요약형/컴파일형 문장** 비중이 커서, alias 무한확장보다
  - summary/noise 분류 규칙
  - 제품명 추출 강화
  가 더 안전할 수 있음.

---

## 4) Claude 빠른 검증 체크리스트

1. 아래 값 재확인
- `flow_posts_with_hs_and_company`
- `evidence_rows`

2. `missing_hs` 상위 20개를
- alias 확장 대상
- noise/summary 제외 대상
으로 분류

3. 필요 시 후속 개선
- `parse_flow_and_product()`에서 요약형 문구 필터링
- `hs_entries_for_post()`에서 멀티품목 결합문장 처리 강화

---

## 5) 참고 수치 (cache)

현재 확인값:
- `telegram_post_cache`: `mapped 15,176`, `unmapped 453`

주의:
- 이 값은 데이터 갱신/재실행 시점에 따라 변동 가능.
- 본 작업의 1차 KPI는 `rebuild` 지표 개선(`flow_posts_with_hs_and_company`, `evidence_rows`)로 판단 권장.

