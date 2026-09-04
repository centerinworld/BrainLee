# Codex 핸드오프 — 데이터 수집·정비 작업 (2026-07-12)

> 발주자: Claude (전략 실험 담당). 아래 작업은 **4주 실험 로드맵**(대시보드 🧪 실험 로드맵 탭)의
> 데이터 선행 작업입니다. 전략 로직/backtest.py/routes/trend.py는 수정하지 마세요 (Claude 담당 영역).
> 완료 시 이 문서의 체크박스를 채우고 CLAUDE.md 변경이력에 한 줄 기록해 주세요.

## 우선순위 1 — Week 1~2 실험에 직접 필요

### 1. 프로그램매매 데이터 백필 (2020-03 ~ 2020-11)
- [ ] `broker_program_stock_daily`가 2020-12-02부터 시작 → 상승장 백테스트 기간(2020-03~) 앞 9개월 공백.
- 키움 REST(`collectors/kiwoom_collector.py` 참고) 또는 KIS의 프로그램매매 과거 일별 조회 API로 백필.
- 과거 조회 불가 시: "불가" 판정 근거(API 응답)만 이 문서에 기록.
- 스키마 유지: `(source, stock_code, dt YYYY-MM-DD, net_buy_qty, buy_amt_krw, sell_amt_krw)`, UNIQUE 충돌 시 skip.

### 2. 수출 무역통계 최신화 + 매핑 확장
- [ ] `hs_trade_lab/data/hs_trade_lab.db` `trade_series_cache`가 2026-03까지 → 2026-06까지 갱신.
- [ ] `hs_code_company_map` 현재 1,119종목 → 시총 2,000억+ 중 미매핑 종목의 HS코드 매핑 추가.
  - 기존 매핑 방법론: 관세청 HS코드 ↔ 종목 (CLAUDE.md 수출입분석 참고). 확신 없는 매핑은 넣지 말 것(오매핑 > 미매핑).

### 3. 산업지표 ↔ 섹터 매핑 테이블 (Week 2 준비)
- [ ] 신규 테이블 `quant_indicator_sector_map (indicator_key TEXT, sector_large TEXT, direction TEXT, note TEXT)` 생성.
- `quant_major_indicator_catalog`의 `customs_sector_trade` 44종 + `steel_price`/`shipping_index`/`autos_sales` 등을
  `stock_universe.sector_large` 값에 매핑 (예: 철강가격→철강금속 direction='+', BDI→운수창고 '+').
- 애매한 지표는 비워두고 note에 사유 기록. **월별 지표 발표시차(대략 익월 15일)를 note에 명시.**

## 우선순위 2 — 데이터 품질 정비

### 4. short_sell_daily.borrow_bal_pct 백필
- [ ] 컬럼이 전량 NULL. `borrow_bal_qty / stock_universe.shares_issued * 100`으로 일괄 계산 UPDATE.
- shares_issued 없는 종목은 NULL 유지. 수집기에도 동일 계산 추가(재발 방지).

### 5. treasury_buyback event_type 정규화
- [ ] 한/영 혼재: '취득결정'/'acquisition'/'trust'/'처분결정'/'disposal' 등.
- 신규 컬럼 `event_norm TEXT` 추가 후 표준값(acquire_decision/trust_sign/trust_cancel/dispose_decision/retire/result)으로 채우기.
- `rcept_dt`도 'YYYY.MM.DD'/'YYYY-MM-DD' 혼재 → 'YYYY-MM-DD'로 통일 UPDATE.
- ⚠️ 기존 값을 지우지 말고 컬럼 추가 방식으로 (Claude 실험 코드가 원본 값 필터를 사용 중).

### 6. 2016~2019 수급 데이터 커버리지 점검 (Week 3 홀드아웃 준비)
- [ ] `price_history`의 2016-01~2019-12 구간에서 `inst_net_buy`/`frn_net_buy` NOT NULL 비율을 연도별로 산출해 보고.
- 커버리지 30% 미만 연도는 네이버 금융 스크래핑(`collect_naver_investor.py` 재활용) 백필 견적(종목수×기간) 산출만 — 실행은 보고 후.

## 우선순위 3 — 조사만 (구현 금지)

### 7. 컨센서스 목표가 과거 데이터 소스 조사
- [ ] `consensus_targets`가 2024-05 이후만 존재. 2020~2024 과거 목표가/투자의견을 구할 수 있는 소스(FnGuide, 에프앤가이드 컨센서스 히스토리, 네이버 리서치 아카이브 등) 조사 + 수집 가능성/비용 보고.

## 작업 규칙
- DB 쓰기 전 해당 테이블 백업본 생성 (`{table}_backup_YYYYMMDD` 관행 유지).
- stock.db 장중(09:00~15:40) 대량 쓰기 금지 — 수집 스케줄러와 lock 경합.
- 완료/불가 판정은 이 문서 체크박스 + CLAUDE.md 변경이력 한 줄.

## 추가 (2026-07-12 오후, Claude)

### 8. 2015~2018 일별 OHLCV 백필 (홀드아웃 검증 차단 요인)
- [ ] `price_history`의 2015~2017이 사실상 비어 있음(종목 2개), 2018도 희소(종목당 ~4일).
  2026-04-15에 `collect_krx_history.py`(KRX 승인 API, 2010~) 백필을 실행했다는 기록이 있으나 현재 DB에 데이터 없음.
- KRX API로 2015-01~2018-12 전종목 OHLCV 백필 재실행. 완료 시 Claude가 전략 홀드아웃(아웃오브샘플) 검증에 즉시 사용.
- `stock_price_daily`도 동기간 0행 — 원인(삭제/다른 DB 파일) 파악해 기록.

### 9. stock_base_info_changes 수집 가동 (자본행위 차트마커 소스)
- [ ] `stock_base_info_changes` 0행 — main.py corporate-actions API의 shares_change 마커가 발생 불가.
  KRX 상장주식수 변경 수집 파이프라인을 가동하거나, 불가하면 이 문서에 사유 기록 (API 코드는 유지되어 있음).
