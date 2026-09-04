# 프로젝트 안티그래비티: 최종 시스템 작동 가이드

본 워크스루는 동적 와치리스트, 프론트엔드 검색 UI, 그리고 대화형 Chat API가 통합된 최종 시스템의 작동 방식을 설명합니다.

## 1. 하드코딩 박멸 및 동적 수집 (Phase 11)
이제 [data_collector.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/data_collector.py)는 내부 코드가 아닌 DB의 [watchlist](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/crud.py#76-86) 테이블을 기준으로 작동합니다.

- **확인 방법**:
  ```bash
  curl http://127.0.0.1:8000/api/commands/watchlist
  ```
- **결과**: 현재 추적 중인 모든 종목 리스트가 JSON으로 반환됩니다.

---

## 2. 프론트엔드 검색 및 자동 등록 (Phase 12)
대시보드 상단 사이드바에 추가된 검색창을 통해 종목을 동적으로 추가할 수 있습니다.

- **작동 방식**:
  1. 검색창에 '카카오' 입력 후 엔터.
  2. 백엔드가 종목코드를 찾아 [watchlist](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/crud.py#76-86)에 등록.
  3. 수집기가 즉시 가동되어 5분 주기 추적 대상에 포함됨.
  4. 대시보드 '자동 추적 리스트'에 실시간 반영.

---

## 3. 유니버설 Chat API 및 질문하기 (Phase 13)
오픈클로(텔레그램)나 외부 앱에서 자연어로 질문할 수 있습니다.

- **테스트 명령**:
  ```bash
  curl -X POST "http://localhost:8000/api/commands/chat" \
       -H "Content-Type: application/json" \
       -d '{"query": "삼성전자 영업이익 어때?"}'
  ```
- **응답 예시**: `{"answer": "삼성전자 최신 영업이익은 약 2,781억원입니다."}`

---

## 4. 한국투자증권(KIS) API 연동 (Phase 15)
국내 주식에 대해 가장 정확하고 빠른 KIS 실전투자 API를 연동했습니다.

- **작동 원리**:
  1. 국내 주식(숫자 6자리) 수집 시 KIS API 우선 사용.
  2. 유저 요청에 따라 **1초당 1회** 속도로 스로틀링 적용.
  3. 토큰 발급 및 24시간 자동 갱신 로직 포함.
- **검증**: [kis_client.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py) 테스트를 통해 삼성전자(005930)의 현재 시세를 초단위로 정확히 수신함을 확인했습니다.

- **최적화**: 수집 주기를 기존 5분에서 **10초** 내외로 대폭 단축하여 진정한 실시간 대시보드를 구현했습니다.

---

## 5. [Phase 19] 프론트엔드 최적화 및 하드코딩 완전 제거
- **데이터 시각화 복구**: 백엔드([processor.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/processor.py))와 프론트엔드([App.jsx](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx)) 간의 데이터 키 불일치(price vs close 등)를 해결하여 모든 그래프가 정상적으로 출력됩니다.
- **다이내믹 상세 테이블**: 기존에 하드코딩되어 있던 '상세 종목 데이터' 테이블을 백엔드 API(`/api/dashboard/fundamentals/{code}`)와 연동하여 실제 재무 수치가 표시되도록 개선했습니다.
- **고밀도 레이아웃(High-Density CSS)**: 화면이 한눈에 들어오도록 전체적인 스케일을 조정하고 여백을 최적화했습니다. 이제 큰 모니터는 물론 작은 화면에서도 주요 지표를 한눈에 확인할 수 있습니다.
- **스마트 검색 고도화**: "하이닉식"과 같은 오타가 발생해도 `difflib` 기반의 지능형 추천 로직을 통해 가장 적절한 종목을 자동으로 선택하여 분석을 수행합니다.

---

## 6. [Phase 21] 전문 SPA 네비게이션 및 종합 매크로 대시보드 (FINAL)

사용자 편의성과 정보 밀도를 극대화하기 위해 시스템 아키텍처를 전문적인 SPA(Single Page Application) 형태로 전면 개편했습니다.

- **글로벌 사이드바 네비게이션**:
  - '종합 대시보드', '개별 종목 분석', '시스템 상태' 등 주요 기능을 단일 페이지 내에서 즉시 전환 가능.
- **종합 매크로 보드 (Macro Overview)**:
  - 코스피, 코스닥 지수 뿐만 아니라 국제 금 시세, WTI 유가, USD/KRW 환율을 한눈에 모니터링.
  - 전일 대비 변동률을 시각화하여 시장 분위기를 즉각 파악 가능.
- **데이터 투명성 (Database Status)**:
  - 시스템 내 실제 [stock.db](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db) 위치 및 실시간 적재 데이터 건수(주가/재무)를 투명하게 공개.
- **기능 최적화**:
  - 프론트엔드 포트를 **5174**로 고정하여 포트 충돌 방지 및 안정적인 접속 환경 구축.

## ✅ 최종 시스템 통합 검증 결과 (Phase 21 기준)

1. **데이터**: 지수, 금, 유가 및 삼성전자(005930)의 고도화된 재무 데이터가 DB에 정상 적재됨을 확인.
2. **UI/UX**: 사이드바를 통한 부드러운 화면 전환과 고해상도 차트 출력을 확인.
3. **안정성**: 백엔드(8000)와 프론트엔드(5174) 간의 CORS 통신 및 데이터 파이프라인 무결성 검증 완료.

이제 프로젝트 안티그래비티는 단순한 수집기를 넘어, **전문 투자자용 종합 분석 플랫폼**으로 진화했습니다.

---

## 7. [2026-03-21] MD 문서 vs 코드 비교 분석 및 버그 수정 (BF-01~05)

### 📋 분석 개요
`보고서.md`, `계획서.md`, `룰.md` 3개 문서와 실제 구현 코드를 전수 비교하여 5개의 버그를 발견하고 즉시 수정했습니다.

### 🐛 수정된 버그 목록

| # | 파일 | 수정 내용 | 영향 |
| :--- | :--- | :--- | :--- |
| **BF-01** | [main.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/main.py) | `from datetime import datetime` 임포트 추가 | `/api/dashboard/stats` 호출 시 NameError 런타임 오류 해결 |
| **BF-02** | [main.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/main.py) | [get_ready_reports()](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/main.py#137-167)에서 `stock["code"]` → `stock["stock_code"]` 키 수정 | `/api/reports/ready` 호출 시 KeyError 오류 해결 |
| **BF-03** | [processor.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/processor.py) | [get_sector_performance()](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/processor.py#67-87)에 `return performance` 추가 | `/api/dashboard/sectors`가 항상 `null` 반환하던 문제 해결 |
| **BF-04** | [kis_client.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py) | 미완성 중복 [get_current_price()](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py#138-183) 첫 번째 정의 제거 | KIS 현재가 조회 메서드 중복으로 인한 불안정성 제거 |
| **BF-05** | [kis_client.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py) | [get_investor_trends()](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py#93-137)에 실제 HTTP 요청 코드 완성 | 기관/외국인 수급 데이터 수집 불가 → 정상 수집 가능 |

---

## 8. [2026-03-21] 관심종목 메뉴 추가 (Watchlist Tab)

### 🆕 구현 내용

**백엔드 ([main.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/main.py))**
- `DELETE /api/commands/watchlist/{stock_code}` 신규 엔드포인트 추가
  - 와치리스트에서 특정 종목을 제거, 없는 종목 요청 시 404 응답

**프론트엔드 ([App.jsx](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx))**
- 사이드바에 ⭐ **관심종목** 탭 추가
- [WatchlistView](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx#138-255) 컴포넌트 신규 구현:
  - DB 와치리스트 전체 종목을 **카드 그리드** 형태로 표시
  - 각 카드: 종목코드 배지 + 종목명 + 선택중 표시
  - 👁️ 버튼: 해당 종목 개별 분석 탭으로 즉시 이동
  - 🗑️ 버튼: 관심종목에서 즉시 제거 (DELETE API 연동, UI 즉시 반영)
  - ➕ 추가 폼: 종목명 입력 → analyze API 호출 → 와치리스트 자동 등록
  - 등록 종목 없을 경우 빈 상태 안내 UI 표시

### ✅ 검증 결과
5종목(삼성전자, 카카오, SK하이닉스, BYC, 하나30호스팩) 카드 정상 표시, 선택 종목 배지·추가/삭제/분석이동 액션 버튼 정상 동작 확인.

---

## 9. [2026-07-26] 퀄리티 팩터 입체 진단 및 5대 전략 백테스트 고도화

### 🆕 구현 및 분석 내용

**1. 5대 전략 백테스트 수행 및 성능 분석 완료**
- [backtest_quality_overlay_monthly.py](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/backtest_quality_overlay_monthly.py) 에 5가지 핵심 시나리오(`model`, `advance`, `order`, `no_risk`, `overlay`)를 투입하여 2020~2026년 기간 및 2024H2~2026 테스트 구간에 대한 rebalance 시뮬레이션을 수행했습니다.
- **결과 레포트**: [quality_overlay_monthly_backtest_20260726.md](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/quality_overlay_monthly_backtest_20260726.md)
  - `model` (기준 모델): 2024H2~2026 테스트 구간에서 +173.85% (MDD -13.37%)
  - `order` (모델 + 최근 수주 보너스): total_return_pct = **+264.7%** (MDD -18.52%)로 압도적인 초과수익률 달성.
  - `no_risk` (모델 - 리스크 감점): total_return_pct = +96.24%

**2. 퀄리티 & 촉매 팩터 입체 진단 UI 적용**
- [App.jsx](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx#12766-12820)의 텐버거 발굴 종목 상세 카드 내부에 `Quality & Catalyst Explanation Layer` 를 구현했습니다.
  - **[🔥 긍정적 촉매]**: 선수성부채 및 신규 수주 확인 안내.
  - **[⚠️ 리스크 유의]**: 영업현금흐름 음수 및 악성재고 가능성 경고.
  - **[ℹ️ 참고 사항]**: 일반 현금흐름 양호 수준의 보조적 지표 가이드.

**3. 섹터 및 마켓레짐별 팩터 효용성 분석 리포트 작성**
- [analyze_by_regime_and_sector.py](file:///Users/brainlee/.gemini/antigravity-ide/brain/6c3b27d1-75a2-4200-944e-aff94aa7abfb/scratch/analyze_by_regime_and_sector.py) 스크립트를 작성하여 KOSPI 200일선 기준의 상승장(Bull)/하락장(Bear) 국면 및 업종별로 12M forward max return 성과를 분석했습니다.
  - **결과 레포트**: [quality_factor_regime_sector_analysis_20260726.md](file:///Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/quality_factor_regime_sector_analysis_20260726.md)
  - `Advance Good` (선수금) 팩터는 **산업재** (+157.62%p) 및 **소재** (+81.20%p) 업종에서 시장지수(KOSPI) 국면과 무관하게 독보적인 알파 창출 효용성이 있음을 증명했습니다.

