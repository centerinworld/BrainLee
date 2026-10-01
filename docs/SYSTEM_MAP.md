# 시스템 지도 (자동 생성 — 수동 편집 금지)

> fingerprint: f04398e0ec45f858 · 2026-09-30 22:56 · 생성: `scripts/ops/gen_system_map_doc.py` (관리자 화면 `/admin/system_map` 과 동일 집계). 코드 수정 후 세션이 끝나면 훅이 재생성.
> 무엇이 어디 있는지 먼저 여기서 찾고, 파일은 필요한 줄만 읽을 것(큰 파일은 아래 표).

## 사이트 구조

| 진입 | 경로 | 내용 | 코드 |
|---|---|---|---|
| Stock Hub(첫 화면) | `stock.leanguy.cloud/` | 4개 메인 선택 + 관리자 | `frontend/src/hub/Landing.jsx` |
| Stock Info | `/info/<탭>` | 시황·종목·섹터·공시 조회 | `App.jsx` + `views/` (탭 구성 `hub/modules.js`) |
| Key Indicator | `newsinfo.cloud` | 별도 사이트(CEO 브리핑 콘솔) | `AI System/codex/ceo-briefing-platform/frontend` (`hubbar.js` 로 허브 바 주입) |
| Stock Lab | `/lab/<탭>` | 전략·백테스트·발굴 | 위와 동일 |
| Stock LLM | `/llm/` → `:8888` | Brian_RAG(하이브리드 RAG). 관리자 로그인 필요 | `/Volumes/Realtek_NVME/Brian_RAG/web_app.py`, 프록시 `routes/llm_proxy.py` |
| 관리자 | `/admin/<탭>` | 비밀번호 로그인(`routes/admin_auth.py`, 쿠키 `sd_admin`) → 개요·시스템 현황·수집 상태·리스크게이트·설정 | `hub/AdminGate.jsx`, `hub/AdminSystemMap.jsx` |

## 규모

| 영역 | 파일 | 줄 |
|---|--:|--:|
| 백엔드 코어 | 141 | 68,827 |
| API 라우터 | 52 | 41,248 |
| 수집기 | 49 | 19,084 |
| 배치·운영 스크립트 | 544 | 109,610 |
| 백테스트 전략 | 44 | 17,382 |
| 테스트 | 95 | 9,289 |
| 프런트엔드 | 49 | 42,383 |

가장 큰 파일: `frontend/src/App.jsx` 18,813, `main.py` 7,535, `scheduler.py` 6,574, `scripts/ops/sync_quant_major_indicators.py` 6,552, `routes/tenbagger.py` 6,442, `signal_engine.py` 6,226, `backtest_common.py` 4,487, `routes/trend.py` 4,109

## API — 483개 (상위 그룹)

`/api/tenbagger` 46, `/api/backtest` 43, `/api/trend` 29, `/api/signals` 24, `/api/dashboard` 22, `/api/us` 19, `/api/market-indicators` 19, `/api/kiwoom` 18, `/api/global-macro` 17, `/api/market-radar` 15, `/api/cafe-signals` 14, `/api/kis-trading` 14, `/api/commands` 13, `/api/portfolio` 11

전체 목록: `docs/API_ENDPOINTS.md` (`scripts/ops/gen_api_doc.py`).

## 스케줄러 — 활성 123 / 전체 125

잡 이름·설명 전체: `docs/SCHEDULER_JOBS.md`. 비활성: V14장중10분, 고용보험배치

## 데이터 계보 (데이터셋 → 테이블 → 쓰는 곳 → 읽는 API → 화면)

| 데이터셋 | 출처 · 주기 | 테이블 | 수집 잡/쓰는 파일 | 읽는 API 라우트 | 화면 |
|---|---|---|---|---|---|
| 국내 주가 | KIS/KRX · 영업일 장중·장마감 | `price_history` (0행) | collect_kis_ohlcv.py, collect_kis_supply_history.py | backtest, buy_candidates, cafe_signals | App.jsx, BacktestView.jsx, CafeSignalsView.jsx |
| 프로그램 매매(시장) | KIS/Kiwoom · 영업일 18:20 | `broker_program_market_daily` (0행) | KRX프로그램매매, collect_broker_program_trading.py | tenbagger | App.jsx, SignalImpactView.jsx, TenbaggerProjectView.jsx |
| 프로그램 매매(종목별) | Kiwoom · 영업일 18:50 | `broker_program_stock_daily` (0행) | 종목프로그램매매, collect_broker_program_trading.py | kiwoom, tenbagger | App.jsx, SignalImpactView.jsx, TenbaggerProjectView.jsx |
| 투자자 수급 | Kiwoom · 영업일 19:00 | `kiwoom_investor_daily` (0행) | 키움투자자수급, kiwoom_collector.py, fix_remaining_data_errors_20260626.py | kiwoom, sector_rotation, tenbagger | App.jsx, SectorRotationView.jsx, SectorSignalSummary.jsx |
| 외국인 지분 | Kiwoom · 영업일 19:15 | `kiwoom_foreign_flow` (0행) | kiwoom_collector.py | kiwoom, tenbagger | App.jsx, SignalImpactView.jsx, TenbaggerProjectView.jsx |
| 대차·공매도 | 공공데이터/KRX · 영업일 | `short_sell_daily` (0행) | collect_short_5years.py, public_data.py | buy_candidates, kiwoom, market_indicators | App.jsx, MarketIndicatorsView.jsx, Screener.jsx |
| 섹터 지수 | KRX · 영업일 | `sector_index_daily` (0행) | 섹터지수보완, rebuild_sector_index_from_price_history.py | extra_signals, global_macro | App.jsx |
| 퀀트 종목 시그널 | 퀀트 엔진 · 매일 07:40 (이벤트 구동 — 조건 미충족 시 0행이 정상) | `quant_stock_trade_signal_snapshots` (0행) | 퀀트지표트리거, snapshot_quant_stock_trade_signals.py | cafe_signals | CafeSignalsView.jsx |
| 글로벌 거시 fast 지표 | Yahoo/FRED/EIA/ECOS · 매일 06:45 | `global_macro_data` (0행) | 글로벌매크로수집, asia_foreign_flow_collector.py, dram_spot_collector.py | global_foreign_flow, global_macro, us_virtual_trading | App.jsx, GlobalForeignFlowView.jsx, StrategyCenterView.jsx |
| 퀀트 거시지표 브릿지 | global_macro_data → quant_major_indicator_series · 매일 19:35 | `quant_major_indicator_series` (0행) | 퀀트주요지표일일, dram_spot_collector.py, collect_molit_housing_starts.py | cafe_signals, quant_major_indicators, sector_rotation | App.jsx, CafeSignalsView.jsx, QuantMajorIndicatorsView.jsx |
| 미국 주가 | yfinance · 미국장 마감 후 06:30 KST | `us_price_history` (0행) | 미국일별시세팩터수집, backfill_us_delisted_prices.py, backfill_us_stale_prices_20260928.py | main, market_radar, trend | App.jsx, MarketRadarView.jsx, Screener.jsx |
| 미국 팩터 | yfinance+US 재무 · 미국장 마감 후 06:30 KST | `us_factor_snapshot` (0행) | 미국일별시세팩터수집, backfill_us_factor_snapshot.py, sync_us_daily_quotes_and_factors.py | main, us_virtual_trading | App.jsx, StrategyCenterView.jsx |
| 텐버거 결과 | 텐버거 엔진 · 영업일 09·12·15시 | `tenbagger_results` (0행) | tenbagger_engine.py | sector_define, tenbagger | App.jsx, SectorFollowup.jsx, SectorFollowupView.jsx |
| 컨센서스 | 한경 컨센서스 · 매일 04:00 | `consensus_targets` (0행) | hankyung_consensus_collector.py, cleanup_consensus_duplicates.py | company_intelligence, consensus, signals | App.jsx, Screener.jsx, StrategyHub.jsx |
| 수주 공시 | DART · 매일 3회 | `dart_contracts` (0행) | dart_contract_collector.py | backtest, cherry_screener, dart_contracts | App.jsx, BacktestView.jsx, Screener.jsx |
| 텔레그램 채널 | Telegram API · 매일 | `telegram_channels` (0행) | report_store.py, telegram_collector.py | company_intelligence, telegram | App.jsx |
| ETF 구성 | ETF CHECK · 영업일 20:30 | `etf_inclusion_daily` (0행) | ETF수집점검, collector.py, publish_direct_stock_daily.py | extra_signals, market_radar | App.jsx, MarketRadarView.jsx, SemiconductorView.jsx |
| 고용보험 | 근로복지공단 · 매일 변화감지 | `wlb_monthly` (0행) | – | extra_signals | App.jsx |
| HS 월간 확정 | 관세청 · 월간 | `customs_monthly_record` (0행) | – | sector_rotation | SectorRotationView.jsx, SectorSignalSummary.jsx |
| 상장회사 기본정보(공공데이터) | 공공데이터포털 · 매주 월요일 07:00 | `listed_company_info` (0행) | public_data_collector.py | reports, trend | App.jsx, Screener.jsx, StrategyCenterView.jsx |
| 키움 실시간(장중 분봉) | Kiwoom WS · 영업일 09:00~15:30 1분 주기 | `kiwoom_minute_snapshot` (0행) | 키움실시간스냅샷, kiwoom_collector.py | kiwoom, market_indicators | App.jsx, MarketIndicatorsView.jsx |
| 키움 실시간(최신 스냅샷) | Kiwoom WS · 영업일 09:00~15:30 1분 주기 | `kiwoom_realtime_quote` (0행) | 키움실시간스냅샷, kiwoom_collector.py, repair_kiwoom_realtime_quote_from_raw.py | kiwoom, market_indicators, market_radar | App.jsx, MarketIndicatorsView.jsx, MarketRadarView.jsx |
| 키움 실시간(틱 원본) | Kiwoom WS · 영업일 09:00~15:30 1분 주기 | `kiwoom_tick_history` (0행) | 키움실시간스냅샷, kiwoom_collector.py | kiwoom, market_indicators | App.jsx, MarketIndicatorsView.jsx |
| 키움 대량체결 순위(원본) | Kiwoom ka00190 · 영업일 09:00~15:30 10분 주기 | `kiwoom_large_trade_rank` (0행) | 키움대량체결, kiwoom_collector.py | kiwoom | App.jsx |
| 종목 마스터 | KRX/네이버 · 일별 KRX 잡(시총·주식수) | `stock_universe` (0행) | clean_stock_universe.py, collect_krx_history.py | buy_candidates, cherry_screener, company_intelligence | App.jsx, DartExcelView.jsx, MarketIndicatorsView.jsx |
| 재무제표 | DART/FnGuide · 월간·공시 후 증분 | `financial_data` (0행) | check_financial_integrity.py, collect_dart_financial_batch.py | backtest, cherry_screener, company_intelligence | App.jsx, BacktestView.jsx, DartExcelView.jsx |
| 현금흐름표 | DART · 월간 현금흐름배치 | `cash_flow_data` (0행) | check_financial_integrity.py, collect_dart_cashflow_batch.py | dart_excel, detailed_analysis, main | App.jsx, DartExcelView.jsx, MarketRadarView.jsx |
| 밸류에이션 이력 | 재무+가격 계산 · 재무 갱신 후 | `valuation_history` (0행) | add_valuation_history_per_ttm_20260925.py, build_valuation_history_2026q2_20260924.py | research_lab, tenbagger | App.jsx, FactorValidationPanel.jsx, PriceIntegrityCard.jsx |
| 신용잔고 | Kiwoom ka10013 · 영업일 | `kiwoom_credit_balance` (0행) | kiwoom_collector.py, kiwoom_margin_collector.py | kis_trading, kiwoom, tenbagger | App.jsx, RiskGateMonitorView.jsx, SignalImpactView.jsx |
| 시그널 결과 | signal_engine · 장마감 후 | `signal_result` (0행) | signal_engine.py | signals | App.jsx, Screener.jsx |
| 전략 피처 스냅샷 | build_strategy_research_dataset · 월간 재생성 | `strategy_feature_snapshot` (0행) | – | backtest, signals, tenbagger | App.jsx, BacktestView.jsx, Screener.jsx |
| 백테스트 결과 | backtest_strategies · 수동·연구 실행 | `backtest_runs` (0행) | backtest_common.py, backtest_equity.py | backtest, trend | App.jsx, BacktestView.jsx, Screener.jsx |
| 내 포트폴리오 | 수동 입력/KIS · 수시 | `portfolio` (0행) | public_data_collector.py, replace_portfolio_20260929.py | company_intelligence, main, portfolio | App.jsx, StrategyCenterView.jsx, TenbaggerProjectView.jsx |
| 가상매매 보유 | peak_monitor · 장중 | `peak_holding` (0행) | paper_execution.py, stockeasy_autotrade.py | main, trend, us_virtual_trading | App.jsx, Screener.jsx, StrategyCenterView.jsx |
| 섹터 보고서 | 텔레그램/PDF 수집 · 매일 | `report_files` (0행) | report_store.py, cleanup_report_files.py | cherry_screener, company_intelligence, detailed_analysis | App.jsx |

## 데이터 신선도 (수집 계약 24개)

주의: 국내 주가=partial, 프로그램 매매(시장)=stale, 프로그램 매매(종목별)=stale, 투자자 수급=stale, 외국인 지분=stale, 섹터 지수=stale, ETF 구성=stale, 상장회사 기본정보(공공데이터)=missing, 키움 실시간(장중 분봉)=missing, 키움 실시간(최신 스냅샷)=stale, 키움 실시간(틱 원본)=missing, 키움 대량체결 순위(원본)=missing

## DB

PostgreSQL 0 MB · 테이블 None개. 상위:  — 스키마 전체 `docs/DB_TABLES_PG.md`.

## 서비스

백엔드 API (FastAPI) :8000 ●, 프런트 서버 (vite preview) :5173 ○(중지), PostgreSQL :5432 ●, Stock LLM (Brian_RAG) :8888 ●, Key Indicator 정적 서버 :5500 ●, Key Indicator API :8011 ●, Cloudflare 터널 프로세스 ●
