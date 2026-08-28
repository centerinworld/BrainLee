# Claude Handoff Report — Frontend Flicker / Error / Efficiency Review (2026-05-22)

## 목적
- 현재 코드베이스 전반 에러 가능성 점검
- 프론트엔드의 지속 리프레시(깜박임) 원인 후보 식별
- 효율성/안정성 개선 우선순위 제안

## 핵심 결론
- 깜박임의 1순위 원인 후보는 `fetchStockDetail` 내 **응답 body 재사용 버그**로 인한 불필요 재조회 예약입니다.
- 구조적으로 `App.jsx` 단일 파일에 상태가 집중되어 있어, 작은 상태 변경도 광범위 리렌더를 유발합니다.
- 일부 폴링/요약 로직은 의도보다 과도한 재요청을 만들고 있습니다.

---

## Findings (심각도 순)

### [P0] 응답 body 이중 소비로 인한 잘못된 무데이터 판정 + 불필요 재조회
- 파일: [/Applications/stock_dashboard/frontend/src/App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx):228, 316
- 증상:
  - `chartRes.json()`을 1회 소비 후 다시 `chartRes.json()`을 호출.
  - 2번째 호출은 이미 소비된 body라 실패하고 `catch(()=>[])`로 떨어져 `hasNoData=true`로 오판.
  - 결과적으로 10초 뒤 추가 재조회가 불필요하게 예약되어 화면 갱신/깜박임 체감 증가.
- 근거 코드:
  - 228행: `setChartData(await chartRes.json())`
  - 316행: `const hasNoData = !(await chartRes.json().catch(()=>[])).length;`
- 권고:
  - `chartDataFirstLoad`를 변수로 1회만 파싱 후 재사용.
  - 이 케이스는 즉시 수정 권장 (가장 효과 큼).

### [P1] 전역 `loading` 오버레이와 대규모 상태 초기화로 인한 시각적 깜박임
- 파일: [/Applications/stock_dashboard/frontend/src/App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx):183-188, 11949
- 증상:
  - 상세 로딩 시작 시 대량 상태를 비우고(`setChartData([])`, `setFinTable([])` 등) 전역 오버레이 표시.
  - 탭/종목 이동 시 컨텐츠가 순간적으로 사라지고 다시 그려지는 체감이 큼.
- 권고:
  - 전역 `loading` 대신 섹션별 스켈레톤(차트/재무/CF 단위)로 분리.
  - 이전 데이터 유지 + “refreshing 배지” 전략(stale-while-revalidate) 적용.

### [P1] 포트폴리오 요약 재요청이 `portfolio` 변경마다 반복
- 파일: [/Applications/stock_dashboard/frontend/src/App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx):5806-5810
- 증상:
  - `portfolio`가 갱신될 때마다 `/api/realtime/prices`를 다시 호출해 `rtSummary`를 재계산.
  - 이미 같은 effect 내 `applyRealtime()`에서 동일 endpoint를 호출하고 있어 중복 요청 발생.
- 권고:
  - `rtSummary`는 `applyRealtime()` 응답에서 직접 설정하고, `[portfolio]` 의존 fetch 제거.

### [P2] `App.jsx` 단일 초대형 컴포넌트로 인한 리렌더 범위 과다
- 파일: [/Applications/stock_dashboard/frontend/src/App.jsx](/Applications/stock_dashboard/frontend/src/App.jsx):1-11965
- 증상:
  - 탭별로 독립 가능한 상태가 최상위에 몰려 있어, 한 상태 변경이 광범위한 하위 렌더 평가를 유발.
- 권고:
  - 우선 분리 대상:
    - `StockAnalysis` 로딩/데이터 상태
    - `PortfolioView` 실시간 상태
    - `Screener` 상태
  - React Query/SWR 도입으로 fetch/캐시/중복요청 관리 일원화.

### [P3] StrictMode(개발환경)에서 effect 2회 실행 체감 가능
- 파일: [/Applications/stock_dashboard/frontend/src/main.jsx](/Applications/stock_dashboard/frontend/src/main.jsx):7
- 설명:
  - dev에서 effect가 2회 실행되어 폴링/요청 중복처럼 보일 수 있음.
  - 프로덕션 직접 원인은 아니지만 디버깅 시 혼선을 유발.
- 권고:
  - dev flicker 재현 시 StrictMode 영향 분리 검증(임시 off 브랜치) 권장.

---

## Claude 작업 지시(실행 우선순위)

1. **P0 즉시 수정**
   - `fetchStockDetail`에서 `chartRes.json()` 결과를 지역 변수로 1회만 파싱.
   - `hasNoData` 판정은 그 변수 기반으로 변경.

2. **P1 안정화**
   - 전역 `loading` 오버레이 노출 조건 축소(초기 진입/강제 새 종목 로딩만).
   - 기존 데이터 유지 + 섹션별 `isRefreshing` 도입.

3. **P1 네트워크 최적화**
   - `PortfolioView`의 `[portfolio]` 기반 `/api/realtime/prices` 재호출 제거.
   - `applyRealtime()` 응답값으로 `rtSummary` 동시 업데이트.

4. **P2 리팩터링(점진)**
   - `StockAnalysis` / `PortfolioView` / `Screener` 분리.
   - fetch 계층을 React Query 또는 SWR로 통합.

---

## 검증 시나리오

1. 분석 탭 진입 후 60초 관찰:
   - 불필요한 10초 재조회가 없어야 함.
2. 네트워크 탭:
   - `chart/financial/cashflow` 요청 횟수 감소 확인.
3. 포트폴리오 탭:
   - `/api/realtime/prices` 중복 호출 감소 확인.
4. UX:
   - 전체 화면 오버레이 깜박임 빈도 감소 확인.

---

## 추가 메모
- 본 보고서는 “원인 식별 + 즉시 수정 우선순위” 중심.
- 기능 변경보다 안정성/체감 성능 개선이 목표.
