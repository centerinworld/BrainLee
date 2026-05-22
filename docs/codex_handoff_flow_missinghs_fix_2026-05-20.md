# Telegram Flow Missing_HS 보강 조치 핸드오프 (for Claude)

작성일: 2026-05-20 (KST)
작성자: Codex
목적: Claude가 최소 시간으로 검증할 수 있도록, 제가 직접 반영한 수정사항/근거/검증절차를 전달

---

## 1) 이번에 Codex가 직접 수행한 수정

수정 파일:
- `/Applications/stock_dashboard/hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py`

### A. `COMPANY_ALIASES` 보강
추가:
- `에스테아이 -> 에스티아이`
- `SEMES -> 세메스`
- `지앤비에스 에코 -> 지앤비에스에코`
- `바우와우코리아 -> 오에스피`
- `삼성디플레이 -> 삼성디스플레이`

### B. `HS_ALIASES` 보강 (missing_hs 상위군 집중)
추가한 핵심 alias:
- `유기발광다이오드 OLED 제조용 -> OLED 모듈(8524911000)`
- `CCSS -> 848620`
- `PR박리액 -> 848620`
- `유기혼합용제와 시너 -> 3814000000`
- `3차원 검사장비 -> 9031809091`
- `산업용자동화원자현미경 -> 9012101000`
- `소형 변압기 / 중대형 변압기 / 변환기`
- `NCA / 진주광택안료 / 수산화리튬 / 영구자석`
- `리드프레임 / Package Substrate / CCL / Cap Assembly`

의도:
- 이전 병목이던 `rebuild_telegram_flow_mappings.py`의 `missing_hs` 상위 반복군을 직접 흡수

---

## 2) 실행 및 결과

실행:
```bash
python3 /Applications/stock_dashboard/hs_trade_lab/scripts/rebuild_telegram_flow_mappings.py
```

결과 파일:
- `/Applications/stock_dashboard/scratch/rebuild_telegram_flow_mappings_20260520_after_patch.json`

핵심 수치 (이번 실행):
- `flow_posts_scanned`: 15,598
- `flow_posts_with_hs_and_company`: **14,242**
- `evidence_rows`: **33,002**
- `inserted`: 24
- `updated`: 32,978

직전 기준 대비(어제 기준치):
- `flow_posts_with_hs_and_company`: 14,081 -> **14,242** (`+161`)
- `evidence_rows`: 31,583 -> **33,002** (`+1,419`)

해석:
- 포스트 cache의 mapped/unmapped보다, 실제 flow 증거행 생성이 중요한 목표였고, 이 지표는 개선됨.

---

## 3) 현재 남은 상위 missing_hs (재검증 우선)

여전히 상위에 남는 항목:
- `코미코(미코세라믹스)`
- `양극활물질 ... 월별 수출 데이터`
- `부분품 ... 리드프레임 (전국)`
- `12인치 레이저마커 / 레이저 그루빙 (전국)`
- `반도체 조립용 인캡슐레이션(몰딩 장비)`

코멘트(확정 아님):
- 일부는 제품명이 아닌 요약/컴파일형 텍스트라 alias만으로 과매핑 리스크 있음.
- 다음 단계는 단순 alias 확장보다 `noise/summary` 분류 규칙 분리가 더 안전해 보임.

---

## 4) 참고: unmapped 수치 관련

제가 이번 턴에서 `telegram_post_cache` 집계도 확인했을 때:
- mapped: 14,970
- unmapped: 659

주의:
- 이 수치는 크롤링 시점/페이지 윈도우에 영향받아 소폭 변동 가능.
- 이번 조치의 핵심 KPI는 `rebuild`의 `flow_posts_with_hs_and_company`, `evidence_rows` 개선으로 판단하는 것이 타당.

---

## 5) Claude 검증 요청 사항 (빠른 체크리스트)

1. `rebuild_telegram_flow_mappings.py` 재실행 후 아래 2개만 우선 확인
- `flow_posts_with_hs_and_company`
- `evidence_rows`

2. `missing_hs` 상위 20개 중
- alias로 풀어야 할 항목
- noise/summary로 제외해야 할 항목
을 분리 판단

3. 필요 시 후속 패치 방향
- `parse_flow_and_product()`에서 요약형 문장 필터 강화
- `hs_entries_for_post()`에서 summary post guard(라벨 폭주/형식 매칭) 추가

---

## 6) 변경 이력 요약

- 수정 파일: `rebuild_telegram_flow_mappings.py` 1개
- 변경 목적: missing_hs 상위군 흡수 + flow evidence 생성량 증가
- 결과: flow evidence 지표 개선 확인

