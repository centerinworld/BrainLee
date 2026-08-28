# Codex Handoff — 전체 코드/로직/프론트/DB 종합 점검 (2026-05-23)

## 점검 목적
- 클로드 교차검증 전에, 현재 코드베이스 전체(백엔드/프론트/DB/운영성능)에서 잠재 리스크를 선제 식별.
- 특히 사용자 첨부 이슈 유형(단위 불일치, 조용한 실패, 화면/로직 불일치)을 중심으로 재점검.

## 점검 범위
- 백엔드: `main.py`, `routes/*`, `hs_trade_lab/app/main.py`, `stockeasy_logic_validator.py`
- 프론트: `frontend/src/App.jsx`, `frontend/src/views/*`
- DB: `stock.db`, `hs_trade_lab/data/hs_trade_lab.db`
- 정적/정량 점검: 예외처리 패턴, 단위 변환 패턴, 인덱스/쿼리 플랜, 최신성 지표

---

## A. 이번 점검 핵심 결론

### A1) [P0] 시가총액(`market_cap`) 단위 혼재 리스크가 실제 존재
- `stock_universe` 최신행 기준 3,891종목 중:
  - `market_cap < 1e7`: 3,878건 (대체로 억원 단위)
  - `market_cap between 1e7 and 1e8`: 2건 (경계)
  - `market_cap >= 1e8`: 11건 (원 단위 잔존 가능성 매우 높음)
- 잔존 11건은 `base_date=2026-03-26~2026-03-27` 구간에 집중.
- 영향: 대형주 필터, 시총 대비 수급비율, 점수 보정 로직 왜곡 가능.

재현 SQL:
```sql
WITH latest AS (
  SELECT su.*
  FROM stock_universe su
  JOIN (SELECT stock_code, MAX(base_date) md FROM stock_universe GROUP BY stock_code) x
    ON x.stock_code=su.stock_code AND x.md=su.base_date
  WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
)
SELECT
  COUNT(*) total,
  SUM(CASE WHEN market_cap>=1e8 THEN 1 ELSE 0 END) ge1e8,
  SUM(CASE WHEN market_cap BETWEEN 1e7 AND 1e8 THEN 1 ELSE 0 END) ge1e7_lt1e8,
  SUM(CASE WHEN market_cap<1e7 THEN 1 ELSE 0 END) lt1e7
FROM latest;
```

### A2) [P0] `stockeasy_logic_validator.py` 내부 시총 단위 가정이 서로 충돌
- 동일 파일에서 `억원 기준`과 `원 기준` 임계값이 혼재.
- 예시:
  - 억원 가정: `is_large = mktcap >= 3000` ([stockeasy_logic_validator.py](/Applications/stock_dashboard/stockeasy_logic_validator.py:889))
  - 원 가정 임계값 다수: `5e12`, `1e12`, `100000000000` 등 ([stockeasy_logic_validator.py](/Applications/stock_dashboard/stockeasy_logic_validator.py:230), [stockeasy_logic_validator.py](/Applications/stock_dashboard/stockeasy_logic_validator.py:388), [stockeasy_logic_validator.py](/Applications/stock_dashboard/stockeasy_logic_validator.py:446))
- 영향: 전략 후보 선정, 대형주 보너스, 매도 로직의 강도 판단 왜곡.

### A3) [P0] `_extract_sell_features`의 단위 휴리스틱이 대형주에서 오판 가능
- 현재:
  - `mktcap_억 = mktcap if mktcap < 1e7 else mktcap / 1e8` ([stockeasy_logic_validator.py](/Applications/stock_dashboard/stockeasy_logic_validator.py:1553))
- 삼성전자/하이닉스처럼 `억원 단위`인데 값이 `1e7`을 넘는 종목은 원 단위로 오인되어 `mktcap_억`가 과소 추정됨.
- 영향: `flow_pct_of_mktcap` 왜곡, 매도 신호 품질 하락.

### A4) [P1] 수출입분석(HS) 기업탭은 개선됐지만, 여전히 에러 노출/경합 처리 약함
- `try/catch`에서 사용자 경고 없이 `console.error`만 남기는 호출 다수.
- 빠른 탭/종목 전환 시 이전 응답이 늦게 도착해 상태를 덮는 race 가능.
- 관련 구간: [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:7012), [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:7090)

### A5) [P1] HS `all_companies` 쿼리는 full scan + temp B-tree 다중 사용
- `EXPLAIN QUERY PLAN` 결과:
  - `SCAN analysis2_company_monthly_cache`
  - `USE TEMP B-TREE FOR GROUP BY / DISTINCT / ORDER BY`
- 위치: `/api/analysis2/companies` 쿼리 ([hs_trade_lab/app/main.py](/Applications/stock_dashboard/hs_trade_lab/app/main.py:631))
- 영향: 데이터 증가 시 페이지 최초 진입 지연.

---

## B. 프론트엔드 점검 결과

### B1) [P1] 다중 polling/refresh 구조로 체감 깜빡임 유발 가능
- 메인 App에 `setInterval`/`setTimeout`/다수 `useEffect`가 고밀도 존재.
- 일부 탭은 배경 폴링과 수동 새로고침이 중첩.
- 대표 구간:
  - [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:276)
  - [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:398)
  - [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:1009)

### B2) [P1] 실패 표준화 부족
- 네트워크 실패 시 UX 레벨에서 `로딩 해제 + 배너/리트라이`가 통일되어 있지 않음.
- 일부는 무음 실패(`catch {}`), 일부는 콘솔만 출력.
- 대표 구간: [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:7024), [App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx:7067)

### B3) [P2] 단위 포맷 함수 다중화
- `fmtMkt`, `fmtUkWon`, 화면별 개별 포맷 함수가 분산되어 있어 단위 버그 재발 위험.
- 공통 유틸 단일화 권장.

---

## C. 백엔드/로직 점검 결과

### C1) [P0] 전략 엔진 단위 불일치 (재강조)
- `stockeasy_logic_validator.py`는 즉시 단위 단일화 필요.
- 권장: 내부 계산은 `억원` 단일화 + 로딩 시 정규화 함수를 강제 적용.

### C2) [P1] 예외 삼키기(`except: pass`) 광범위
- 탐지 파일 다수(`main.py`, `routes/*`, `data_collector.py` 등).
- 장애 시 원인 추적 난이도 상승, 조용한 데이터 결손 가능.

### C3) [P1] 신선도/지연 감지 지표 표준 부족
- 예: `financial_data`, `cash_flow_data`는 `updated_at` 컬럼이 없어 최신성 점검 일관성이 떨어짐.
- 현재는 `year/quarter` 기반 간접 점검만 가능.

---

## D. DB 점검 결과

### D1) `stock.db` 현황
- `price_history`: 5,855,000 rows
- `financial_data`: 107,591 rows
- `cash_flow_data`: 109,480 rows
- `stockeasy_analysis`: 50 rows

### D2) `hs_trade_lab.db` 현황
- `analysis2_sector_monthly_cache`: 868 rows
- `analysis2_company_monthly_cache`: 34,788 rows
- `analysis2_company_hs_monthly_cache`: 59,841 rows
- `telegram_post_cache`: 15,974 rows
- 텔레그램 매핑률: `97.58% (15587/15974)`
- 최신 월: company/sector 모두 `2026-04`
- 최신 텔레그램 post: `2026-05-22T04:19:48+00:00`

### D3) 인덱스/플랜
- `stock.db`는 주요 테이블 인덱스 비교적 양호.
- `hs_trade_lab.db`는 분석용 대집계 쿼리에 비해 인덱스가 제한적.
- `/api/analysis2/companies`는 스캔/임시 B-tree 사용이 많아 개선 여지 큼.

---

## E. 효율성/안정성 개선 제안 (실행 순서)

### E1) Priority 0 (즉시)
1. `market_cap` 단위 정규화 배치 + 최신행 재정렬
2. `stockeasy_logic_validator.py` 단위 통일 (억원 기준)
3. `_extract_sell_features` 휴리스틱 제거, 정규화 함수 강제
4. 회귀 테스트 추가:
- 삼성전자/하이닉스 `is_large=True`
- `flow_pct_of_mktcap` 극단치 방지

### E2) Priority 1
1. HS API 에러 표준화 (`r.ok` 검사 + 에러 payload)
2. 프론트 요청 경합 차단(AbortController/requestId 통일)
3. `/api/analysis2/companies` 사전 집계 테이블 또는 materialized cache 도입
4. 공통 단위 포맷 유틸 모듈화

### E3) Priority 2
1. `except: pass` 전수 제거 및 구조화 로깅
2. `financial_data/cash_flow_data` 최신성 메타 컬럼 보강
3. 모니터링 대시보드에 “데이터 신선도/지연 원인” 패널 추가

---

## F. 어제 점검분(재검토 제외, 문서 이관)
- 아래 항목은 이번 턴에서 **중복 재검토 제외**하고 참조만 유지.
- 참조 문서:
  - [codex_handoff_cross_validation_2026-05-23.md](/Applications/stock_dashboard/docs/codex_handoff_cross_validation_2026-05-23.md)
  - [codex_handoff_telegram_export_import_check_2026-05-23.md](/Applications/stock_dashboard/docs/codex_handoff_telegram_export_import_check_2026-05-23.md)
- 제외 항목 요약:
  - HS 기업탭 `period_ym` 반영/수출입 분리 렌더링 1차 수정
  - 텔레그램-수출입 동기화 상태 1차 점검
  - 시장점유율 기반/균등분할 표시 필요성 확인

---

## G. 클로드 작업 체크리스트
1. 단위 정규화 스크립트 작성/실행 (`stock_universe.market_cap`)
2. `stockeasy_logic_validator.py` 단위 통일 PR
3. 회귀테스트 4종 추가
- 대형주 분류
- 매도 피처 비율
- 전략 후보 수 변화(전/후)
- HS 페이지 기준월 전환 스냅샷
4. HS `all_companies` 성능 개선(쿼리/인덱스/캐시)
5. 프론트 에러 처리 표준화 및 깜빡임 완화

---

## H. 참고: 이번 점검에서 직접 수정된 최근 항목
- HS 페이지의 일부 로직/표시 보완은 이미 적용된 상태.
- 단, 본 문서의 P0(전략 단위 통일)은 아직 미수정이며 클로드가 우선 처리 필요.
