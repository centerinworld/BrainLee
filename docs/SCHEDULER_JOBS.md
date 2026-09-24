# 스케줄러 잡 목록 (자동 생성)

> `scripts/ops/gen_scheduler_doc.py` 가 `scheduler.py` 루프 등록부에서 생성 — 2026-09-24 기준 116개. **수동 편집 금지**(잡 추가/변경 후 스크립트 재실행).
> 시각/주기 설명은 등록 라인의 주석에서 가져오므로 주석이 없는 잡은 설명이 비어 있다. 정확한 실행 조건은 `scheduler.py`의 `_loop_*`/`_job_*` 참조.

| 잡 | 상태 | 설명 |
|----|------|------|
| `야간배치` (`_loop_nightly`) | 활성 |  |
| `월간업데이트` (`_loop_monthly`) | 활성 |  |
| `공시확인` (`_loop_disclosure`) | 활성 |  |
| `공시최근증분` (`_loop_disclosure_recent_refresh`) | 활성 |  |
| `장중1분가격` (`_loop_intraday_price`) | 활성 |  |
| `장중5분수급` (`_loop_intraday_investor`) | 활성 |  |
| `장마감` (`_loop_closing`) | 활성 |  |
| `스크리너사전계산` (`_loop_screener`) | 활성 |  |
| `공공데이터` (`_loop_public_data`) | 활성 |  |
| `KIS일별수집` (`_loop_kis_daily`) | 활성 | KIS API 전종목 OHLCV (KRX 차단 대체) |
| `KIS추정실적` (`_loop_kis_forward_estimates`) | 활성 | KIS Forward EPS/PER 등 순환 갱신 |
| `전종목수급17시` (`_loop_supply_daily`) | 활성 | 17:30 KIS 전종목 수급 |
| `전종목수급21시` (`_loop_supply_evening`) | 활성 | 21:00 재갱신 |
| `레이더해외가격` (`_loop_radar_price_update`) | 활성 | 1시간마다 해외 yfinance |
| `네이버밸류에이션` (`_loop_naver_fundamentals`) | 활성 | 네이버 PBR/PER/EPS |
| `현금흐름배치` (`_loop_cashflow_batch`) | 활성 | DART 현금흐름표 월간 |
| `텐버거오전` (`_loop_tenbagger_morning`) | 활성 | 09:00 텐버거 발굴 |
| `텐버거정오` (`_loop_tenbagger_noon`) | 활성 | 12:00 텐버거 발굴 |
| `텐버거오후` (`_loop_tenbagger_afternoon`) | 활성 | 15:00 텐버거 발굴 |
| `NPS고용업데이트` (`_loop_nps_daily`) | 활성 | 매일 06:00 공개 최신월 감지 + 최근 3개월 보완 |
| `미국지수수집` (`_loop_us_indices`) | 활성 | 매일 06:30 나스닥/S&P500 수집 |
| `글로벌매크로수집` (`_loop_global_macro_daily`) | 활성 | 매일 06:45 글로벌 거시/원자재/이벤트 지표 수집 |
| `거시가격품질감사` (`_loop_macro_price_quality`) | 활성 | 매일 07:05 심볼 혼입·범위 이탈 탐지 |
| `미국일별시세팩터수집` (`_loop_us_daily_quotes_and_factors`) | 활성 | 매일 06:30 미국 전종목 OHLCV+팩터 stale-only |
| `미국바이오파이프라인` (`_loop_us_biotech_pipeline`) | 활성 | 매일 06:50 SEC 10-K/10-Q 기반 바이오 후보물질 보강 |
| `소스인텔리전스주간` (`_loop_source_intelligence_weekly`) | 활성 | 매주 일요일 10:00 트릴리온 등 등록 소스 티커 의견 재검토 |
| `미국13F거물공시` (`_loop_us_13f_refresh`) | 활성 | 매일 07:12 SEC 13F + House PTR 갱신 |
| `시장시그널브리핑` (`_loop_market_signal_briefing`) | 활성 | 매일 07:00 시장 5단계 국면 + AI 브리핑 |
| `HOT섹터블로그` (`_loop_sector_blog`) | 활성 | 매일 07:00 블로그 신규 포스트 파싱 |
| `섹터지수보완` (`_loop_sector_index_rebuild`) | 활성 | 매일 18:40 가격히스토리 기반 섹터지수 보완 |
| `섹터로테이션캐시` (`_loop_sector_rotation_cache`) | 활성 | 장중 1시간 + 장마감 기준 주도섹터 캐시 |
| `AI주도섹터` (`_loop_ai_leading_sector`) | 활성 | 매일 07:20 미국 증시 기반 주도 섹터 판독 |
| `섹터오전텔레그램` (`_loop_sector_morning_tg`) | 활성 | 매일 08:30 섹터 AI 리포트 텔레그램 |
| `섹터점심텔레그램` (`_loop_sector_lunch_tg`) | 활성 | 매일 12:30 오전장 섹터 분석 텔레그램 |
| `스탁이지분석` (`_loop_stockeasy_analysis`) | 활성 | 매일 16:30 스탁이지 전략 분석 |
| `스탁이지주간` (`_loop_stockeasy_weekly`) | 활성 | 매주 일요일 09:00 주간 요약 |
| `스탁이지30분동기화` (`_loop_stockeasy_30m_sync`) | 활성 | 매 30분 모멘텀 동기화 + 실주문(옵션) |
| `보유종목매도알림` (`_loop_portfolio_sell_alerts`) | 활성 | 매일 15:00 보유종목 매도검토 텔레그램 요약 |
| `V14장중10분` (`_loop_v14_10m`) | ⛔ 비활성 | 2026-07-23 비활성: GPT V18 가상매매 승률27%(-8.6M 누적손실)로 저효율 확인되어 삭제, 조합 가상매매로 대체 |
| `V12골든크로스` (`_loop_gc_20m`) | 활성 | 장중 20분 V12 골든크로스 가상매매 |
| `V-RECOVERY` (`_loop_rec_20m`) | 활성 | 장중 20분 V-RECOVERY 낙폭반등 가상매매 |
| `V-CONTRACT` (`_loop_cm_20m`) | 활성 | 장중 20분 V-CONTRACT-MOMENTUM 해외수주 모멘텀 가상매매(2026-08-09) |
| `전방검증체크` (`_loop_forward_validation_check`) | 활성 | 매일 06:10 라이브 가상매매 실측으로 forward_validation 아티팩트 재평가(2026-08-13) |
| `전략센터상위5가상매매` (`_loop_combo_daily`) | 활성 | 매일 18:35 전략센터 현재 상위 5개를 재선정해 가상매매 |
| `키움연결체크` (`_loop_kiwoom_health`) | 활성 | 키움 REST 연결 상태 점검(장중 10분) |
| `키움IP감시` (`_loop_kiwoom_ip_watch`) | 활성 | 24시간 10분마다 공인 IP 변경 감시 → 변경 시 키움 인증 재확인+텔레그램 알림 |
| `키움실시간스냅샷` (`_loop_kiwoom_realtime`) | 활성 | 장중 1분 키움 실시간 스냅샷 수집 |
| `나무체결강도` (`_loop_namu_execution_strength`) | 활성 | 관심/보유 종목 체결강도 이력 |
| `키움조건검색` (`_loop_kiwoom_condition_snapshot`) | 활성 | 장중 조건식 현재 편입 + 편입/편출 이력 |
| `고용보험배치` (`_loop_insurance_monthly`) | ⛔ 비활성 | 비활성: 연간 총인원 수집 — 월별차이 없어 의미 없음 (사용자 요청) |
| `DART수주공시` (`_loop_dart_contracts`) | 활성 | 매일 08:00/13:00/17:00 DART 수주공시 |
| `DART수주계약` (`_loop_order_contracts`) | 활성 | 매일 19:00 수주잔고 급증 proxy 테이블 적재 |
| `DART희석공시` (`_loop_dart_dilution`) | 활성 | 매일 07:10 CB/BW/EB 희석 공시 수집 |
| `DART희석공시마감` (`_loop_dart_dilution_close`) | 활성 | 평일 17:20 당일 CB/BW/EB·증자 공시 반영 |
| `키움신용잔고` (`_loop_kiwoom_margin`) | 활성 | 평일 18:45 종목별 신용/대주 잔고 (코스피+코스닥 80%) |
| `키움업종수급` (`_loop_kiwoom_sector_flow`) | 활성 | 평일 19:00 ka10051 업종별투자자순매수(2026-09-05 신규, 12종 세부기관분류) |
| `키움업종수급검증` (`_loop_kiwoom_sector_flow_validation`) | 활성 | 매주 월요일 08:15 수급→다음날수익률 상관관계 누적검증(2026-09-06 신규) |
| `키움외국인지분율` (`_loop_kiwoom_foreign_hold`) | 활성 | 평일 19:15 외국인 지분율 수집 (코스피+코스닥 80%) |
| `DART임원매매` (`_loop_dart_insider`) | 활성 | 매주 일요일 02:30 임원매매 전종목 + 매일 공시 incremental |
| `DART수주잔고` (`_loop_dart_backlog`) | 활성 | 매주 일요일 01:20 수주잔고 분기 수집(5년) |
| `DART원가재고` (`_loop_dart_cost`) | 활성 | 매주 일요일 01:50 매입재료비/재고/감가상각 수집(5년) |
| `DART매입재료비` (`_loop_dart_material_purchase`) | 활성 | 매주 일요일 02:20 원재료 매입액 전용 수집 |
| `DART직원수` (`_loop_dart_employee_count`) | 활성 | 매주 일요일 02:55 dart_employee_count 전용 수집 |
| `DART임직원CH` (`_loop_dart_ch_extra`) | 활성 | 매주 일요일 03:10 직원현황/판관비/매출채권 보강 |
| `DART세그먼트` (`_loop_dart_segment`) | 활성 | 매주 일요일 03:30 사업부문별 매출 수집(시총상위500) |
| `근로복지공단` (`_loop_wlb_monthly`) | 활성 | 매일 20:30 변화감지 → 수집 |
| `BigQuery동기화` (`_loop_bigquery_sync`) | 활성 | 매일 23:30 BigQuery 전체 운영테이블 동기화 → 텐버거 BQ 분석 |
| `BQ아침알림` (`_loop_bq_morning_alert`) | 활성 | 매일 07:30 3배 패턴 아침 알림 |
| `컨센서스수집` (`_loop_consensus`) | 활성 | 매일 04:00 한경 컨센서스 증분 수집 |
| `재무무결성일일` (`_loop_financial_integrity_daily`) | 활성 | 매일 06:20 재무 이상값 수리 + 무결성 리포트 |
| `재무무결점월간` (`_loop_financial_integrity_monthly`) | 활성 | 매월 1일 05:00 재무 무결점 검사 |
| `재무무결점분기` (`_loop_financial_integrity_quarterly`) | 활성 | 분기 공시마감 1주 후 자동 보완 |
| `KRX종목기본정보` (`_loop_krx_base_info`) | 활성 | 매일 18:35 KRX 종목기본정보 + 변동 감지 |
| `FnGuide재무월간` (`_loop_fnguide_financial_monthly`) | 활성 | 매월 3일 05:00 연결/별도 재무제표 전종목 |
| `수출입가집계` (`_loop_trade_provisional`) | 활성 | 매주 월요일 06:00 수출입 10일 가집계 수집 |
| `공시DB배치` (`_loop_disclosure_db_batch`) | 활성 | 매주 일요일 02:00 DART 전종목 공시 DB 저장 |
| `KRX투자자수급` (`_loop_krx_investor_playwright`) | 활성 | 매일 18:10 KRX 전종목 기관/외국인 순매수(Playwright) |
| `KRX프로그램매매` (`_loop_krx_program_trading`) | 활성 | 매일 18:20 KRX 프로그램매매(차익/비차익) Playwright |
| `종목프로그램매매` (`_loop_broker_program_stock_trading`) | 활성 | 매일 18:50 Kiwoom 종목별 프로그램 매수/매도 |
| `RS사전계산` (`_loop_rs_precompute`) | 활성 | 매일 18:30 RS/52주 캐시 사전계산 |
| `CF3중검증` (`_loop_cf_triple_validate`) | 활성 | 매일 05:30 신규 CF 3중 검증 (DART·FnGuide·Seibro) |
| `주간4중검증` (`_loop_weekly_revalidation`) | 활성 | 매주 일요일 03:00 전종목 4중 검증 Phase A+B+C+E+F |
| `DB유지보수` (`_loop_db_maintenance`) | 활성 | 매주 일요일 04:00 VACUUM/ANALYZE/WAL checkpoint |
| `PostgreSQL주간백업` (`_loop_postgres_weekly_backup`) | 활성 | 매주 일요일 05:10 전체 스냅샷 백업+검증 |
| `PostgreSQL백업상태` (`_loop_postgres_backup_health`) | 활성 | 매일 05:50 해시·카탈로그·신선도 검증 |
| `PostgreSQL커트오버검증` (`_loop_postgres_cutover_verify`) | 활성 | 매일 06:10 테이블별 드리프트/매크로오염 감시+자동복구 |
| `WAL일별체크` (`_loop_wal_daily_check`) | 활성 | 매일 04:30 WAL 크기 감시 + 100MB 초과 시 checkpoint |
| `실적신호스캔` (`_loop_earnings_signal_scan`) | 활성 | 매일 06:00 + 분기실적 시즌 추가 스캔 |
| `키움투자자수급` (`_loop_kiwoom_investor_daily`) | 활성 | 매일 19:00 키움 ka10059 종목별 투자자 순매수 수집 |
| `키움종목기본정보` (`_loop_kiwoom_stock_universe`) | 활성 | 매주 월요일 06:30 키움 ka10001 PER/PBR/ROE/유동주식수 갱신 |
| `키움대량체결` (`_loop_kiwoom_large_trade_rank`) | 활성 | 장중 10분 키움 ka00190 대량체결 원본 순위 |
| `카페시그널주간` (`_loop_cafe_signal_weekly`) | 활성 | 매주 월요일 07:10 지표상회 카페 종목/섹터 시그널 구조화 |
| `카페시그널월간` (`_loop_cafe_signal_monthly`) | 활성 | 매월 1일 07:15 지표상회 카페 월간 시그널 구조화 |
| `체리형부최신채널` (`_loop_cherry_latest_channel`) | 활성 | 매일 08:45 @Brianlee4 최신 체리형부 문서 증분 수집 |
| `체리형부패밀리학습` (`_loop_cherry_family_learning`) | 활성 | 매일 09:05 체리형부 family 등록상태 확인 + 재학습 로그 저장 |
| `퀀트지표트리거` (`_loop_quant_indicator_signal`) | 활성 | 매일 07:40 지표 이상치 → 관련 종목 매수 후보 텔레그램 |
| `거시지표백테스트` (`_loop_macro_indicator_backtest`) | 활성 | 매주 월요일 07:50 거시지표×섹터 후보 검증 |
| `퀀트주요지표일일` (`_loop_quant_major_indicators_daily`) | 활성 | 매일 19:35 퀀트 주요지표 daily(시장폭/대차/기준금리/거시브릿지/카지노) — 2026-09-22 crontab 부재 복구 |
| `데이터무결성후속검증` (`_loop_data_integrity_followup`) | 활성 | 매일 00:05 2026-08-22 세션 발견 잔여 이상치(revenue_extreme_yoy/dilution) DART 원문대조 재검증 |
| `기업행위조정계수후속확정` (`_loop_corporate_action_confirmation_followup`) | 활성 | 매일 00:10 유상증자 TERP 조정계수 매칭 재시도 (turnaround/regime_adaptive 등 백테스트 검증 병목 해소용, DART 미사용) |
| `가격점프감사재빌드` (`_loop_price_jump_audit_rebuild`) | 활성 | 매일 00:15 price_jump_audit 재빌드(2026-08-24 세션: 2주 이상 스테일 방치로 허위오탐 발생 확인, 재발방지) |
| `가격외부소스재대조` (`_loop_naver_price_verify`) | 활성 | 매일 00:20 신규 가격점프 이벤트만 Naver와 교차대조(--only-new, DART 미사용) |
| `가격커버리지백필` (`_loop_naver_coverage_backfill`) | 활성 | 매일 19:15 KIS일별수집 직후 그날 빠진 종목을 Naver로 즉시 백필 |
| `다중소스재무교차검증` (`_loop_multi_source_financial_crosscheck`) | 활성 | 매일 00:25 손익/현금흐름/매입재료비를 DART(anchor) vs FnGuide/Naver/Yahoo 다중소스로 교차검증(2026-08-26 세션, DART API 미사용 — FnGuide스크레이핑+naver_financial테이블+yfinance) |
| `전략센터주간재검증` (`_loop_weekly_strategy_reverify`) | 활성 | 매주 일요일 01:30 등록전략 전량 최신데이터로 재실행(2026-08-24: V8 승격/V10·V12 정직한 하향 확인된 바로 그 배치를 정기화) |
| `DART재무재수집` (`_loop_dart_financial_recollect`) | 활성 | 매일 00:30 DART 재무제표 재수집 (ETF/ETN/상폐 제외) |
| `DART_CF위험군재수집` (`_loop_dart_cf_risk_recollect`) | 활성 | 매일 01:00 CF MISSING_Q123/NULL_Q123_DEPR/MIXED_SOURCE 재수집 |
| `텐버거위클리` (`_loop_tenbagger_weekly`) | 활성 | 매주 월요일 07:30 위클리 리포트 + 텔레그램 |
| `텐버거역사검증` (`_loop_tenbagger_historical_validation`) | 활성 | 매주 월요일 08:10 지속형 텐버거 로직 재검증 |
| `텐버거트리거` (`_loop_tenbagger_trigger`) | 활성 | 평일 18:00 복합 트리거 알림 |
| `페이지데이터감사` (`_loop_page_data_audit`) | 활성 | 매일 06:40 전체 페이지 데이터 신선도 감사 |
| `턴어라운드워치사전계산` (`_loop_turnaround_watch_precompute`) | 활성 | 매일 04:40 무거운 턴어라운드 발굴 스캔 사전계산(CPU 유휴시간대) |
| `체리형부스크리너사전계산` (`_loop_cherry_screener_precompute`) | 활성 | 매일 04:45 체리형부식 3대 스크리닝 전종목 스캔 사전계산 |
| `투자의사결정RAG` (`_loop_investment_decision_rag`) | 활성 | 평일 저가 구간에만 대기 중인 문서 RAG 처리 |
| `FnGuideDART전종목검증` (`_loop_fnguide_dart_verify_sweep`) | 활성 | 매일 03:15 FNGUIDE 일일한도 내에서 전종목 순차 교차검증 |
| `미검증스냅샷백필` (`_loop_unverified_snapshot_backfill`) | 활성 | 매일 03:45 financial_source_snapshot unverified 백로그 정리(2026-08-28 신설, DART만 소비, FnGuide 재수집 불필요) |
