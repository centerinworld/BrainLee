# Codex Dashboard Site Review Handoff (2026-06-06)

## 1. 이번 턴에서 Codex가 직접 수정한 범위

### A. 상세분석 API/프론트 성능 및 정확도

수정 파일:
- `/Applications/stock_dashboard/routes/detailed_analysis.py`
- `/Applications/stock_dashboard/frontend/src/App.jsx`

핵심 변경:
1. `/api/detailed-analysis/posts`를 **서버 페이지네이션**으로 전환
   - 기존: 전체 목록 반환 후 프론트에서 `slice`
   - 변경: `q`, `page`, `page_size`를 받아 `{items, total, page, page_size, total_pages}` 반환
   - 종목당 중복 게시글은 `ROW_NUMBER() OVER (...)`로 최신글 1건만 노출

2. 상세분석 프론트에서 **클라이언트 페이지네이션 제거**
   - 목록 10건 단위는 서버 응답 기준으로 표시
   - 검색어 변경 시 `page=1`로 자동 복귀
   - 현재 페이지에 선택된 글이 없으면 첫 글 자동 선택

3. `127.0.0.1:8000` 절대 fallback 제거
   - 기존 `API_ABS()` 사용 제거
   - 프록시 상대경로 `/api`만 사용
   - 로컬 맥 외 다른 기기에서 접속 시 fallback 실패하던 문제 예방

4. 텔레그램 메시지/첨부파일 매칭 강화
   - 기존: `LIKE '%종목명%'` 기반으로 넓게 연결
   - 변경: 종목명/종목코드/정규화 텍스트 기반 후처리 필터 추가
   - 함수:
     - `_report_file_matches_stock`
     - `_telegram_message_matches_stock`
     - `_tokenize_stocks_field`
   - 목적: 유사 종목명/짧은 종목명으로 인한 오매칭 축소

### B. peak_monitor 루프 블로킹 완화

수정 파일:
- `/Applications/stock_dashboard/peak_monitor.py`

핵심 변경:
1. `/api/trend/update`를 동기 POST에서 **백그라운드 비동기 전송**으로 변경
2. 업데이트성 API는 timeout을 10초 → 2초로 단축
3. 대기 작업 수 상한(`API_UPDATE_MAX_PENDING=6`) 추가
4. 메인 모니터 루프가 `/api/trend/update` 지연 때문에 60초 이상 멈추는 현상 완화

적용 함수:
- `api_post_background()`
- `run_once()` 내부 `api_post(...)` → `api_post_background(...)`

## 2. 실제 검증 결과

### A. 코드 상태 검증

실행:
- `python3 -m py_compile peak_monitor.py routes/detailed_analysis.py main.py routes/*.py collectors/*.py`
- `cd frontend && npm run build`

결과:
- 문법 오류 없음
- 프론트 빌드 성공
- 참고: `collectors/hankyung_consensus_collector.py:19`에 기존 invalid escape warning 1건 존재(이번 수정 범위 아님)

### B. API 검증

서버 재기동 후 확인:
- `launchctl kickstart -k gui/$(id -u)/com.stock-dashboard.local`

확인 결과:
- backend: `http://127.0.0.1:8000/docs` 응답 OK
- frontend: `http://127.0.0.1:5173/` 응답 OK

상세분석 API 응답 확인:
- `/api/detailed-analysis/posts?page=1&page_size=2&q=`
  - 응답 형식: `['items', 'page', 'page_size', 'total', 'total_pages']`
  - 실제 샘플: `total=33`, `page=1`, `page_size=2`, `total_pages=17`
- OpenAPI 파라미터:
  - `q`, `page`, `page_size` 노출 확인
- 상세조회 샘플:
  - 첫 게시글 `화신`
  - `files=5`, `telegram_files=5`, `telegram_messages=18`

### C. 실행 프로세스 검증

재기동 후 활성 프로세스:
- `serve_foreground.sh`
- `uvicorn main:app`
- `npm run preview`
- `peak_monitor.py`

## 3. 이번 작업 중 확인한 원인/사실

### A. “코드는 수정됐는데 API 응답이 옛 형식으로 보이던” 이유

원인:
- 파일은 새 코드였지만 **실행 중인 launchd 프로세스가 구 프로세스를 계속 물고 있던 상태**였음

해결:
- `start.sh`만으로는 교체가 불안정할 수 있어
- 최종적으로 `launchctl kickstart -k gui/$(id -u)/com.stock-dashboard.local`로 강제 재시작 후 정상 반영 확인

### B. peak_monitor 타임아웃 문제의 실제 영향

기존 로그:
- `/Applications/stock_dashboard/logs/peak_monitor.launchd.log`
- `/api/trend/update: timed out`가 연속 발생하며 한 루프가 약 60초 걸린 이력 존재

변경 후 기대효과:
- trend/update 지연이 있어도 `peak_monitor` 핵심 루프는 블로킹되지 않음
- 다만 DB lock 자체가 많으면 “스킵 로그”는 계속 남을 수 있음

## 4. 아직 남아 있는 리스크(클로드 검토 필요)

### 4.1 RT-Macro / trend/update DB lock

로그:
- `/Applications/stock_dashboard/logs/backend.launchd.log`

반복 확인된 경고:
- `[RT-Macro] ... database is locked`
- `[trend/update] DB locked; skipped non-critical update ...`

의미:
- 이번 수정으로 `peak_monitor` 루프 정체는 줄였지만
- **DB writer 경합 자체**는 아직 남아 있음

클로드 검토 포인트:
1. `price_history` 쓰기 작업과 `trend/update` 쓰기 경합 분리 가능 여부
2. WAL/transaction 경계 재설계
3. RT-Macro 쓰기를 큐 기반 또는 배치형으로 바꿀 수 있는지

### 4.2 public_data 수집기의 구식/중복 경로 가능성

관찰:
- `public_data_collector.py` 계열은 예전 테이블명과 현재 운영 테이블이 혼재한 흔적이 있음
- 로그상 같은 성격의 수집이 중복 실행되는 패턴이 보임

클로드 검토 포인트:
1. `public_data_collector.py`가 현재 운영 테이블 기준으로 맞는지
2. 중복 수집/중복 스케줄 여부
3. 더 이상 쓰지 않는 레거시 경로 정리 가능 여부

### 4.3 프론트 번들 크기

빌드 경고:
- `dist/assets/index-*.js` 약 800KB

즉시 장애는 아니지만:
- 페이지 첫 로딩 체감에 영향 가능

클로드 검토 포인트:
1. `DetailedAnalysisView`, 미국주식, 데이터품질 패널 등 대형 섹션 lazy load 분리
2. 코드 스플리팅 도입 가능 여부

## 5. 클로드가 우선 재검토해야 할 체크리스트

1. 상세분석 페이지
   - 검색/페이지 이동 시 10건 단위로 정상 변경되는지
   - 종목 중복 게시글이 최신 1건만 나오는지
   - 다른 종목 첨부파일/메시지가 섞이지 않는지

2. LAN 접속
   - `192.168.x.x:5173` 형태로 접속 시 상세분석 호출이 정상인지
   - 이제 더 이상 `127.0.0.1:8000` fallback에 의존하지 않는지

3. peak_monitor
   - 타임아웃이 발생해도 루프 주기가 비정상적으로 늘어나지 않는지
   - `trend/update` 지연이 매수/매도 감시 루프를 막지 않는지

4. DB lock
   - 현재 남아 있는 `database is locked` 경고가 어떤 writer 조합에서 주로 나는지
   - 비핵심 갱신 스킵이 화면/전략에 실제로 어떤 영향을 주는지

## 6. 참고 파일

- 수정 파일
  - `/Applications/stock_dashboard/routes/detailed_analysis.py`
  - `/Applications/stock_dashboard/frontend/src/App.jsx`
  - `/Applications/stock_dashboard/peak_monitor.py`

- 참고 로그
  - `/Applications/stock_dashboard/logs/backend.launchd.log`
  - `/Applications/stock_dashboard/logs/frontend.launchd.log`
  - `/Applications/stock_dashboard/logs/peak_monitor.launchd.log`

## 7. 한 줄 결론

이번 턴에서 **상세분석 페이지의 구조적 성능 문제와 오매칭 위험, peak_monitor의 API 타임아웃 블로킹 문제는 직접 수정 완료**했다.  
다만 **DB lock 경합과 일부 레거시 수집 경로 정리**는 아직 후속 점검이 필요하므로, 클로드는 그 부분을 우선 검토하면 된다.
