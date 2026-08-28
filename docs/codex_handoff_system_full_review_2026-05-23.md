# Codex Handoff — 시스템 전반 정밀 점검 (2026-05-23)

## 0) 목적/범위
- 목적: 프론트엔드/백엔드/DB/운영 스케줄/LLM 토큰 사용까지 전체 성능·안정성·확장성 점검
- 범위: `/Applications/stock_dashboard` 전체 코드와 `stock.db`, `hs_trade_lab.db`
- 주의: 2026-05-22~23에 이미 검토/수정된 일부 재무 정합성 항목은 재검증 제외(단, 참조로 문서 말미에 기록)

## 1) 현재 시스템 스냅샷 (팩트)
- 대형 단일 파일
  - `main.py`: 2,692 lines
  - `frontend/src/App.jsx`: 12,269 lines
  - `hs_trade_lab/app/main.py`: 1,643 lines
  - `scheduler.py`: 2,582 lines
- DB 크기
  - `stock.db`: 3.3G
  - `stock.db-wal`: 256M
  - `hs_trade_lab.db`: 1.5G
- 핵심 row 수 (`stock.db`)
  - `price_history`: 5,855,001
  - `stock_universe`: 6,715
  - `financial_data`: 107,790
  - `cash_flow_data`: 111,868
  - `kiwoom_tick_history`: 0
  - `kiwoom_realtime_quote`: 0
  - `kiwoom_minute_snapshot`: 0
- 운영 상태
  - 미국 지수 최신일: `^IXIC`, `^GSPC` 모두 `2026-05-22`
  - `stock_universe` 최신일: `2026-05-22`
  - `stockeasy_analysis` 최신 분석시각: `2026-05-22 22:57:51`
- SQLite 설정 (`stock.db`)
  - `journal_mode=wal`, `synchronous=1(NORMAL)`, `wal_autocheckpoint=1000`
  - `freelist_count=23,039` (빈 페이지 존재)
- SQLite 설정 (`hs_trade_lab.db`)
  - `freelist_count=175,548` (재정리 필요 수준)

## 2) 우선순위별 핵심 이슈

### P0 (즉시 대응)
1. 프론트엔드 리프레시/깜박임 구조적 원인
- 파일: `/Applications/stock_dashboard/frontend/src/App.jsx`
- 현상: 다중 `useEffect` + 다중 polling(`setInterval`) + 탭/종목 변경 시 중첩 fetch
- 영향: 초기 진입 지연, 화면 깜박임, API burst, 상태 경합(race)
- 근거: App 단일 파일에서 수십 개의 `useEffect`, interval, fetch 분산
- 조치:
  - `React Query`(또는 SWR)로 서버 상태 통합: dedupe, stale-time, background refresh 표준화
  - `analysis`, `screener`, `trend`, `portfolio`, `market_indicators`를 페이지 단위 코드 분할
  - 동일 엔드포인트 중복 호출을 단일 hook로 합치고 탭 비활성 시 polling 중단

2. 키움 실시간 데이터 테이블이 비어 있음
- 테이블: `kiwoom_tick_history`, `kiwoom_realtime_quote`, `kiwoom_minute_snapshot` row=0
- 영향: V18/가상매매에서 “틱/체결강도 기반 실시간성”을 가정하면 현재는 데이터 공백
- 조치:
  - 점검일 제외 시간대 수집 health-check 자동 기록(성공/실패/최종 수신시각)
  - 실패 시 fallback 우선순위 명시(KIS 분봉/직전 스냅샷/신호 보류)

3. 시장점유율 기반 vs 균등분할 표시는 구현됐지만, API 응답에 basis 필드 강제 보장 필요
- 파일: `/Applications/stock_dashboard/hs_trade_lab/app/main.py`
- 영향: 프론트/텔레그램 렌더러가 basis 누락 시 오해 가능
- 조치:
  - 관련 엔드포인트 응답 스키마에 `allocation_basis: 'market_share'|'equal_split'` 강제
  - 미존재 시 기본값 금지(명시적 `unknown`으로 경고 노출)

### P1 (이번 주 권장)
4. HS 전체 기업 목록 쿼리가 temp B-tree 과다 사용
- 파일: `/Applications/stock_dashboard/hs_trade_lab/app/main.py` (`/api/analysis2/companies`)
- 플랜: `USE TEMP B-TREE FOR GROUP BY`, `GROUP_CONCAT(DISTINCT)`, `ORDER BY`
- 영향: 데이터 증가 시 응답 지연 급증
- 조치:
  - `analysis2_company_latest_cache` 물리 캐시 테이블 신설
  - 야간 배치로 집계 선계산, API는 단순 `SELECT ... ORDER BY total_export DESC LIMIT ...`
  - `GROUP_CONCAT(DISTINCT ...)`를 API 실시간에서 제거

5. DB 파일 팽창 및 WAL/프리리스트 관리 부재
- 영향: I/O 증가, cold start 지연, 백업/복구 시간 증가
- 조치:
  - 매주 1회 유지보수 윈도우에 `PRAGMA wal_checkpoint(TRUNCATE); VACUUM; ANALYZE;`
  - 대형 로그/히스토리 테이블 보관 정책(예: tick raw 30~90일, 집계 장기 보존)

6. 공휴일 하드코딩 관리 위험
- 파일: `/Applications/stock_dashboard/routes/market_indicators.py`
- 영향: 연도 변경 시 장 상태 계산 오류 가능
- 조치:
  - 거래일 캘린더 단일 모듈로 통합(`trading_calendar.py`)하고 모든 라우트에서 재사용

7. 스케줄러 잡 수 과다 + 단일 프로세스 결합
- 파일: `/Applications/stock_dashboard/scheduler.py`
- 영향: 장애 전파, 디버깅 난이도, 중복 수집 위험
- 조치:
  - 수집/분석/알림 잡을 3개 큐로 논리 분리
  - 잡별 실행 이력 테이블 표준화(`job_name`, `started_at`, `ended_at`, `status`, `error`, `rows`)

### P2 (리팩토링)
8. 모놀리식 코드 구조
- App.jsx 12k 라인, main.py/scheduler.py 2k+ 라인
- 영향: 변경 리스크/회귀 테스트 비용 상승
- 조치:
  - 도메인 단위 모듈화(예: `analysis`, `signals`, `portfolio`, `export_import`, `market_indicators`)
  - API 계약(OpenAPI schema) 우선 고정 후 내부 분리

9. 예외 삼킴(`except: pass`) 다수
- 영향: 조용한 데이터 손실/갱신 실패
- 조치:
  - 수집·계산 핵심 경로는 fail-fast + structured logging
  - `pass` 대신 경고 로그 + 카운터 적재

## 3) 프론트엔드 개선안 (깜박임/지연/바인딩)
1. 서버 상태 관리 통합
- 권장: `@tanstack/react-query`
- 효과: 중복 fetch 제거, 캐시/재시도/동시성 제어 일원화

2. 탭 전환 시 데이터 유지
- 정책: `keepPreviousData=true`, 로딩 오버레이 최소화
- 즉시 깜박임 감소

3. polling 중앙화
- 현재: 각 탭 내부 interval 분산
- 개선: 전역 scheduler hook 1개에서 시장시간/활성탭 기준으로 polling orchestration

4. 렌더링 최적화
- 대형 테이블(수출입/재무/랭킹)은 virtualization 적용
- 차트 데이터는 memoized selector로 변환 비용 절감

5. 네트워크 취소/경합
- 모든 fetch 경로 `AbortController` 적용 범위 확대
- 종목 전환 시 이전 요청 일괄 cancel

## 4) 백엔드/API 개선안
1. 읽기/쓰기 분리 경로
- 현재 SQLite 단일 파일에 수집+조회 혼합
- 권장:
  - 단기: 수집 배치 시간대와 사용자 조회 고부하 시간대 분리
  - 중기: `read replica`(SQLite 복사본) 또는 DuckDB/ClickHouse 병행(분석 조회용)

2. 분석 API 캐시 계층
- `/api/analysis2/*`, `/api/stock-analysis-rs/*`, `/api/trend/*`에 TTL 캐시 표준화
- 키: `endpoint + params + latest_data_version`

3. 쿼리 표준화
- `SELECT *` 축소 (명시 컬럼만)
- 문자열 날짜 연산(`date('now',...)`)은 사전 계산한 boundary 파라미터로 대체

4. 관측성(Observability)
- 응답시간 p50/p95/p99, 캐시 hit ratio, DB lock wait, 에러율 대시보드화

## 5) 데이터베이스 적절성 평가 (대량 데이터 관점)

### 현재 SQLite 유지 가능 범위
- 일봉/재무/요약형 데이터 중심이면 당분간 유지 가능
- 단, 실시간 틱/체결강도 대량 적치까지 SQLite 단일 파일로 장기 운영은 비권장

### 확장 전략
1. 단기(즉시)
- 파티셔닝 유사 전략: 월/분기 단위 아카이브 테이블 분리
- 인덱스 재점검 + 주기적 VACUUM/ANALYZE

2. 중기(1~2개월)
- 시계열/대용량 조회 분리
  - 옵션 A: PostgreSQL + TimescaleDB
  - 옵션 B: ClickHouse (분석/랭킹 매우 빠름)
- 거래/주문 관련 core 상태는 RDB(정합성 우선), 대량 시계열은 컬럼형/TSDB

3. 장기
- OLTP(실시간 서비스)와 OLAP(특징 탐색/백테스트) 완전 분리
- BigQuery는 배치 분석 목적에 적합, 실시간 주문 신호 직접 소스는 비권장

## 6) 토큰(LLM) 사용량 절감/효율화
1. 호출 트리거 축소
- 이벤트 기반 호출만 유지(데이터 변화 임계치 초과 시)
- 주기 호출 금지(특히 장중 반복)

2. 프롬프트 경량화
- 원본 JSON 전체 첨부 대신 핵심 피처만 전달
- 동일 종목/동일 기간 요청은 hash 캐시

3. 모델 계층화
- 1차 규칙 기반 필터(로컬 계산) → 통과 건만 LLM
- 요약형은 `gpt-5-mini` 유지, 고비용 모델 사용 금지

4. 출력 구조 표준화
- JSON 스키마 엄격화로 파싱 재시도/재호출 감소

5. 예산 가드
- 일/주/월 토큰 상한 + 상한 근접 시 자동 degrade(요약 길이 축소/호출 연기)

## 7) 안정성/백업/롤백
1. 배포 전 백업
- 코드: 태그 + 브랜치 스냅샷
- DB: `stock.db`, `hs_trade_lab.db` 스냅샷 + WAL 포함

2. 장애 시 백업 플랜
- 데이터 수집 실패: 직전 확정 데이터로 신호 보수적 유지
- 실시간 수집 실패: 주문 신호 `HOLD` 강등 + 재검증 호출 강제

3. 무결성 점검 자동화
- 일일: row 증가량/최신일/NULL 비율/중복키
- 주간: 재무·현금흐름 샘플링 교차검증(DART/FnGuide/내부)

## 8) 실행 우선순위 (Claude 작업 지시용)
1. P0-1: App.jsx polling/fetch 중복 제거 + React Query 도입
2. P0-2: 키움 수집 health endpoint + fallback 규칙 구현
3. P1-4: `analysis2_all_companies` 사전집계 캐시 테이블 도입
4. P1-5: DB maintenance job(VACUUM/ANALYZE/checkpoint) 스케줄 추가
5. P1-6: 공휴일/거래일 판정 단일 모듈 통합
6. P2-8: 대형 파일 모듈 분리(백엔드→프론트 순)

## 9) 재검토 제외 항목(어제 검토분, 참조만)
- 재무 Q4/CF 음수 분류 정정, source NULL 정리, CAPEX 부호 정리 관련
- 참조 문서:
  - `/Applications/stock_dashboard/docs/codex_handoff_q4_negative_fix_20260522.md`
  - `/Applications/stock_dashboard/docs/codex_handoff_cross_validation_2026-05-23.md`
  - `/Applications/stock_dashboard/docs/codex_handoff_telegram_export_import_check_2026-05-23.md`

## 10) 결론
- 현재 구조는 기능 폭은 넓지만, 프론트 다중 polling/대형 모놀리식/SQLite 파일 팽창으로 인해
  “지연 + 깜박임 + 운영 리스크”가 반복될 수 있는 상태입니다.
- 단기적으로는 캐시/폴링 정리와 집계 선계산만으로 체감 성능을 크게 개선할 수 있고,
  중기적으로 저장소를 OLTP/OLAP로 분리하면 데이터가 더 늘어도 안정적으로 확장 가능합니다.
