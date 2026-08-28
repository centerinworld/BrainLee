# 전체 페이지 데이터 품질 감사 — 2026-07-02

- DB: `/Applications/stock_dashboard/stock.db`
- HS DB: `/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db`
- 생성: `2026-07-02T06:41:13`
- 요약: OK 27 / 수집필요 2 / 검토필요 2 / 누락 0

|페이지|데이터셋|테이블|상태|행수|기간|이슈|수집/보강|메모|
|---|---|---:|---|---:|---|---|---|---|
|메인 대시보드/차트|국내 OHLCV|`price_history`|ok|5881661|2010-01-04 ~ 2026-07-02|-|KIS collect_kis_ohlcv.py|-|
|메인 대시보드/종목검색|상장종목|`stock_universe`|ok|2693|2026-06-28 21:30:00 ~ 2026-06-28 21:53:17|-|KRX/Kiwoom stock info|-|
|시장지표|공공 일별 시세|`stock_price_daily`|ok_with_fallback|814220|20200102 ~ 20260629|covered_by:price_history+stock_universe<br>stock_price_daily의 market_cap 결측은 stock_universe로 보완 가능|public_data_collector.py / KIS fallback|페이지/전략의 주 시세 소스는 price_history이며 market_cap은 stock_universe fallback 가능|
|시장지표|투자자별 매매|`investor_trading_daily`|ok|4512954|2018-04-23 ~ 2026-07-01|-|public_data_collector.py / KIS|-|
|시장지표|Kiwoom 투자자 수급|`kiwoom_investor_daily`|ok|4512954|2018-04-23 ~ 2026-07-01|-|Kiwoom ka10059|-|
|시장지표|외국인 보유|`foreign_holding_daily`|ok_with_fallback|107764|20260312 ~ 20260608|covered_by:kiwoom_foreign_flow<br>공공 외국인 보유 API 미신청/지연 구간은 Kiwoom ka10008로 보완|public_data_collector.py / Kiwoom|-|
|시장지표|Kiwoom 외국인 지분|`kiwoom_foreign_flow`|ok|152996|20260312 ~ 20260701|-|Kiwoom ka10008|-|
|시장지표|대차잔고 종목|`short_sell_daily`|ok_with_fallback|3781725|20200102 ~ 20260630|covered_by:short_rank_daily.lnb_bal<br>종목별 API가 잔고금액을 제공하지 않는 구간은 대차순위 금액 필드로 보완|collect_short_5years.py / public_data_collector.py|-|
|시장지표|대차순위|`short_rank_daily`|ok|3298149|20210104 ~ 20260630|-|collect_short_5years.py|-|
|시장지표|외국인 대차잔고|`short_foreign_balance`|ok|222|20080101 ~ 20260601|-|collect_short_5years.py|월/시장 단위 보조지표라 종목 일별 테이블과 같은 행수 기준을 적용하지 않음|
|시장지표|월별 대차|`short_monthly_stat`|ok|234|20080131 ~ 20260629|-|collect_short_5years.py|-|
|시장지표/텐버거|프로그램 시장|`broker_program_market_daily`|ok|4022|2020-01-01 ~ 2026-06-26|-|KIS/Kiwoom program collector|-|
|시장지표/텐버거|프로그램 종목|`broker_program_stock_daily`|unstable_or_needs_review|1605349|2020-12-02 ~ 2026-06-30|latest_day_low_coverage:1<2000|Kiwoom program collector|-|
|재무/텐버거/DART Excel|연결 재무|`financial_data`|ok_with_fallback|191903|2015 ~ 2026|covered_by:canonical_financial_data<br>raw financial_data 중복 grain은 표준 테이블에서 해소|DART/FnGuide batch|-|
|재무/텐버거/DART Excel|표준 재무|`canonical_financial_data`|ok|90850|2015 ~ 2026|-|canonical rebuild|-|
|재무/텐버거/DART Excel|현금흐름|`cash_flow_data`|ok_with_fallback|128337|2016 ~ 2026|covered_by:canonical_cashflow_data<br>raw cash_flow_data 중복 grain은 표준 테이블에서 해소|DART cashflow batch|-|
|재무/텐버거/DART Excel|표준 현금흐름|`canonical_cashflow_data`|ok|78322|2022 ~ 2025|-|canonical cashflow rebuild|-|
|재무/텐버거|매입재료비|`dart_material_purchase`|needs_collection|2655|2021 ~ 2025|low_volume:2655<3000|DART material collector|-|
|재무/텐버거|수주잔고|`order_backlog`|ok|6817|2016 ~ 2026|-|DART backlog collector|-|
|재무/텐버거|세그먼트 매출|`segment_revenue`|ok|16903|2018 ~ 2026|-|DART segment collector|-|
|고용 페이지|NPS 월별|`nps_workplace_monthly`|ok|29704|202504 ~ 202605|-|employment_monitor.collect_nps_workplace|-|
|고용/재무|DART 임직원|`dart_employee_count`|needs_collection|1330|2020 ~ 2025|low_volume:1330<5000|DART employee collector|-|
|컨센서스/종목|컨센서스|`consensus_targets`|unstable_or_needs_review|11248|2024-05-08 ~ 2026-07-01|duplicate_grain:12|collect_consensus|report_idx가 없는 한경 리포트는 자연키로 중복 판정|
|텐버거|텐버거 결과|`tenbagger_results`|ok|900|2026-06-06 10:20:15 ~ 2026-07-01 15:00:00|-|routes/tenbagger run|-|
|텐버거|실적 시그널|`earnings_signals`|ok|909|2018 ~ 2026|-|earnings signal scan|-|
|퀀트 주요지표|주요지표 시계열|`quant_major_indicator_series`|ok|136652|1960-01 ~ 202606|-|scripts/ops/quant_indicators_cron.py|-|
|마켓 레이더|섹터 가격 캐시|`radar_price_cache`|ok|79082|2025-02-24 ~ 2026-06-19|-|market_radar refresh-cache|-|
|HS/시그널 영향성|HS 월간 확정 수출입|`customs_monthly_record`|ok|1332214|2016-01 ~ 2026-05|-|hs_trade_lab/scripts/daily_refresh.py|-|
|HS/시그널 영향성|분석2 섹터-HS 캐시|`analysis2_sector_hs_monthly_cache`|ok|26066|2016-01 ~ 2026-05|-|rebuild_analysis2_cache.py|-|
|HS/시그널 영향성|분석2 기업-HS 캐시|`analysis2_company_hs_monthly_cache`|ok|88835|2016-01 ~ 2026-05|-|rebuild_analysis2_cache.py|-|
|HS/시그널 영향성|10일 잠정 수출입|`customs_provisional_10day_record`|ok|1485|2025-04 ~ 2026-06|-|collect_provisional_10day.py|-|
