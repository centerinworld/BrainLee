# Codex Handoff — 퀀트 주요지표 UI / 데이터 연결 현황 (2026-06-07)

## 1. 이번 턴에서 반영한 것

### 프론트
- 신규 화면:
  - `/Applications/stock_dashboard/frontend/src/views/QuantMajorIndicatorsView.jsx`
- 기존 앱 연결:
  - `/Applications/stock_dashboard/frontend/src/App.jsx`

### UI 방향
- **화면 스타일은 기존 stock_dashboard 스타일**
  - `glass-panel`, 카드형 요약, Recharts 차트, 하단 시계열 테이블
- **메뉴 구조는 EPIC 스타일**
  - 상단 우선순위 탭(`p1/p2/p3`)
  - 카테고리별 수평 메뉴
  - 카테고리 안 지표 리스트
- 데이터가 없는 지표는 무조건:
  - `수집대기중`
  - 필요 소스
  - 연결 방식
  - notes
  를 노출

## 2. 현재 연결된 API

### 백엔드
- `/api/quant-major-indicators/catalog`
- `/api/quant-major-indicators/series/{indicator_key}`
- `/api/quant-major-indicators/summary`

### 현재 실제 시계열 적재 완료 지표
1. `epic:20:1` 한국은행 기준금리
2. `epic:20:22` 국내 주식시장 대차잔고
3. `epic:20:99` 국내 주식시장 투자자 예탁금 / 신용공여

## 3. 다른 AI가 이어서 물어볼 핵심 연결 과제

아래는 **실제 신규 수집기 개발이 필요한 항목**이다.

### P1 우선 연결
1. 자동차 판매 / 점유율
   - EPIC key:
     - `epic:0:1` 글로벌 자동차 판매: 국가별 (월)
     - `epic:0:2` 한국 자동차 판매: 회사별 (월)
     - `epic:0:4` 한국 자동차 시장 점유율: 회사별 (월)
   - 예상 소스:
     - KAMA
     - KAIDA
     - 현대차/기아/KGM/르노/한국GM 월간 판매 공시/IR
   - 비고:
     - `0:4`는 판매량 수집 완료 후 계산 가능

2. 모델별 자동차 판매
   - EPIC key 예:
     - `epic:0:14`
     - `epic:0:17`
     - `epic:0:112`
     - `epic:0:113`
   - 예상 소스:
     - OEM 월간 판매 공지 / IR PDF / HTML 표

3. 후판가격
   - EPIC key:
     - `epic:17:17` 한국 후판가격 (주)
   - 예상 소스:
     - 철강 시황 사이트
     - 협회 / 유료 리포트 대체 가능한 공개 발표 자료

### P2 / 부분 대체 가능
4. 베트남 의류/IT 수출 지표
   - EPIC key:
     - `epic:12:10`
     - `epic:15:11`
   - 예상 소스:
     - customs / HS 코드 집계
   - 비고:
     - EPIC와 1:1 exact인지 검증 필요

### P2 다수 신규 필요
5. 카드 결제 추정치
6. 카지노 드롭액
7. 여행/송출객
8. IPTV 가입자
9. 대중교통 이용량
10. 운임지수 / 원자재 가격 일부

## 4. 다른 AI에게 바로 물어보면 좋은 질문

1. 자동차 판매:
   - KAMA/KAIDA 월간 판매 데이터를 자동 파싱 가능한 공개 페이지가 있는지
   - OEM IR PDF/HTML에서 월별 모델별 판매량을 안정적으로 뽑는 구조가 있는지

2. 후판가격:
   - 한국 후판가격을 주간 단위로 공개적으로 제공하는 사이트/문서가 있는지
   - 조선/철강 업계에서 반복적으로 접근 가능한 대체 공개 소스가 있는지

3. 베트남 지표:
   - HS 코드 바스켓을 EPIC 정의에 가깝게 복원할 수 있는지
   - customs 기반 프록시를 exact/close/approx 중 어느 등급으로 둘지

4. 카드소비/관광:
   - 공개 API/크롤링 가능한 원천이 있는지
   - 월별 누적/단월 데이터 구분이 명확한지

## 5. 검증 상태

- `npm run build` 성공
- 현재 로컬 서비스 정상:
  - backend `127.0.0.1:8000`
  - frontend `127.0.0.1:5173`

## 6. 주의사항

1. 이 페이지는 **지표 카탈로그와 연결 상태를 먼저 보여주는 용도**다.
2. 아직 연결되지 않은 지표를 빈값처럼 보이게 하면 안 되고, 반드시 `수집대기중`으로 표시해야 한다.
3. 새 수집기를 만들면:
   - `quant_major_indicator_catalog.status`
   - `collector_path`
   - `quant_major_indicator_series`
   를 함께 업데이트해야 한다.
