# 데이터 신선도 전체 점검 — 2026-09-27 (자동 생성: scripts/ops/audit_data_freshness.py)

- 대상 테이블 224개(제외 167: 백업·로그·일회성 / 날짜 컬럼 없음 64 / 조회 오류 2). 기준: 오늘(2026-09-27) 대비 **5일 이상** 뒤처진 것.
- 주의: 월·분기·연간 데이터, 이벤트 구동(조건 충족 시만 적재), 일회성 연구 테이블은 오래돼도 정상일 수 있다 — 아래는 후보 목록이며 원인 분류는 `원인` 열/본문 참조.

## 뒤처진 테이블 110개

| 테이블 | 기준 컬럼 | 최신 | 뒤처진 일수 | 행수 |
|---|---|---|--:|--:|
| `selected_price_repair_inserted` | date | 2018-12-28 | 2830 | 539 |
| `ai_analysis_reports` | report_date | 2026-03-22 13:13:37 | 189 | 16 |
| `telegram_stock_mentions` | created_at | 2026-03-29 18:34:19 | 182 | 21 |
| `signal_config` | created_at | 2026-04-04 12:14:19 | 176 | 26 |
| `futures_daily` | date | 2026-04-17 | 163 | 90 |
| `stock_bizno_map` | updated_at | 2026-04-19 11:53:58 | 161 | 29 |
| `futures_contract_daily` | date | 2026-04-20 | 160 | 423,852 |
| `radar_sector_override` | updated_at | 2026-05-03 13:38:24 | 147 | 574 |
| `radar_semiconductor_override` | updated_at | 2026-05-03 13:38:24 | 147 | 168 |
| `sector_insights` | updated_at | 2026-05-03 13:38:24 | 147 | 15 |
| `semiconductor_valuestream` | created_at | 2026-05-03 13:59:25 | 147 | 151 |
| `dart_disclosure_cache` | updated_at | 2026-05-10 21:43:28 | 140 | 20 |
| `buy_candidates` | updated_at | 2026-05-13 08:13:21 | 137 | 31 |
| `dart_raw_accounts` | fetched_at | 2026-05-16T17:25:43 | 134 | 112 |
| `data_lock` | created_at | 2026-05-16 08:26:03 | 134 | 6,840 |
| `stock_meta` | updated_at | 2026-05-24 22:02:26 | 126 | 2,784 |
| `naver_financial` | collected_at | 2026-05-27 03:46:16 | 123 | 22,768 |
| `dart_item_mapping_catalog` | updated_at | 2026-05-31 02:48:40 | 119 | 12 |
| `ifrs_field_rules` | updated_at | 2026-05-31 02:48:40 | 119 | 5 |
| `ifrs_mapping_coverage_snapshot` | created_at | 2026-05-31 02:48:40 | 119 | 220 |
| `source_field_mapping_rules` | updated_at | 2026-05-31 02:48:40 | 119 | 15 |
| `forward_strategy_indicators` | updated_at | 2026-06-06 13:04:29 | 113 | 93 |
| `forward_strategy_industry_categories` | updated_at | 2026-06-06 13:04:29 | 113 | 43 |
| `forward_strategy_raw_responses` | fetched_at | 2026-06-06 13:02:09 | 113 | 97 |
| `forward_strategy_related_companies` | updated_at | 2026-06-06 13:04:29 | 113 | 711 |
| `forward_strategy_sources` | updated_at | 2026-06-06 13:04:29 | 113 | 1 |
| `cost_breakdown` | collected_at | 2026-06-09 16:42:23 | 110 | 23,678 |
| `foreign_flow_quarterly` | updated_at | 2026-06-11 08:57:09 | 108 | 87,474 |
| `investor_flow_quarterly` | updated_at | 2026-06-11 09:05:04 | 108 | 90,150 |
| `radar_price_cache` | trade_date | 2026-06-19 | 100 | 79,082 |
| `dart_report_item_mapping_qa` | created_at | 2026-06-22 12:00:54 | 97 | 921 |
| `cafe_signal_post_bodies` | fetched_at | 2026-07-10 22:52:23 | 79 | 7,600 |
| `investor_trading_daily` | bas_dt | 2026-07-10 | 79 | 4,519,957 |
| `dart_material_purchase` | collected_at | 2026-07-11 01:50:15 | 78 | 3,801 |
| `epic_indicator_replacement_plan` | updated_at | 2026-07-11 05:10:48 | 78 | 80 |
| `hypothesis_research_registry` | updated_at | 2026-07-12T16:49:16 | 77 | 1 |
| `hypothesis_research_runs` | created_at | 2026-07-12T16:49:16 | 77 | 1 |
| `signal_quality_scores` | signal_date | 2026-07-12 | 77 | 1 |
| `stock_base_info_changes` | created_at | 2026-07-12 07:23:24 | 77 | 4,611 |
| `company_mapping_profile` | created_at | 2026-07-21 12:09:56 | 68 | 17,347 |
| `stockeasy_autotrade_manual_hold` | created_at | 2026-07-21 14:10:17 | 68 | 1 |
| `stockeasy_sector_rs_daily` | dt | 2026-07-22 | 67 | 47 |
| `contract_advance_signals` | updated_at | 2026-07-26T19:20:25 | 63 | 3,983 |
| `dart_bs_items` | created_at | 2026-07-26 10:20:21 | 63 | 67,705 |
| `dart_report_items_quarterly` | updated_at | 2026-07-26 10:20:21 | 63 | 111,194 |
| `portfolio` | updated_at | 2026-07-26 13:00:48 | 63 | 53 |
| `portfolio_tx` | created_at | 2026-07-26 13:00:48 | 63 | 86 |
| `autotrade_guard_state` | updated_at | 2026-07-27 15:27:07 | 62 | 2 |
| `tenbagger_daily_alerts` | created_at | 2026-07-27 22:30:00 | 62 | 613 |
| `triple_pattern_daily` | updated_at | 2026-07-27 14:59:18 | 62 | 354 |
| `trigger_discovery_forward_returns` | created_at | 2026-07-29 09:17:16 | 60 | 878,303 |
| `trigger_discovery_stock_links` | created_at | 2026-07-29 09:15:43 | 60 | 313,596 |
| `segment_revenue` | updated_at | 2026-08-01 20:29:28 | 57 | 25,624 |
| `data_availability_ledger` | updated_at | 2026-08-02T03:04:07 | 56 | 240,364 |
| `data_lineage_catalog` | updated_at | 2026-08-07T18:37:44 | 51 | 8 |
| `explainable_stock_signals` | period | 2026-08-07 | 51 | 1,618 |
| `market_regime_daily` | trade_date | 2026-08-07 | 51 | 2,848 |
| `naver_price_history_backfill` | date | 2026-08-07 | 51 | 2,547,912 |
| `price_series_registry` | updated_at | 2026-08-07T18:37:05 | 51 | 3 |
| `stock_universe_history` | base_date | 2026-08-07 | 51 | 2,666 |
| `stockeasy_analysis` | created_at | 2026-08-07 07:30:23 | 51 | 293 |
| `strategy_regime_policy` | updated_at | 2026-08-07T18:37:36 | 51 | 16 |
| `telegram_messages` | date | 2026-08-08 12:17:06 | 50 | 13,361 |
| `market_signal_briefing` | created_at | 2026-08-09 22:00:30 | 49 | 244 |
| `sector_posts` | created_at | 2026-08-09 22:00:05 | 49 | 130 |
| `telegram_channels` | created_at | 2026-08-11 09:16:24 | 47 | 14 |
| `trading_restrictions` | as_of | 2026-08-13T23:13:59 | 45 | 26 |
| `live_signal_outcomes` | updated_at | 2026-08-14T20:44:37 | 44 | 222 |
| `live_signal_registry` | signal_date | 2026-08-14 | 44 | 37 |
| `selected_price_repair_batches` | created_at | 2026-08-14T22:34:08 | 44 | 8 |
| `quant_market_regime_signal` | trade_date | 2026-08-21 | 37 | 1,919 |
| `quant_stock_trade_signal_snapshots` | signal_date | 2026-08-21 | 37 | 303 |
| `postgres_data_repair_batches` | created_at | 2026-08-23 08:37:58 | 35 | 6 |
| `cafe_signal_posts` | collected_at | 2026-08-24 07:10:06 | 34 | 8,225 |
| `kiwoom_us_realtime_quote` | updated_at | 2026-08-25 22:43:30 | 33 | 1 |
| `system_hardening_plan` | updated_at | 2026-08-25 22:47:32 | 33 | 16 |
| `short_foreign_balance` | bas_dt | 20260901 | 26 | 224 |
| `inventory_sales_signals` | updated_at | 2026-09-03T21:04:56 | 24 | 54,237 |
| `short_rank_daily` | bas_dt | 20260903 | 24 | 3,955,138 |
| `canonical_cashflow_data` | updated_at | 2026-09-04T12:31:38 | 23 | 80,592 |
| `radar_market_cache` | updated_at | 2026-09-04 12:54:07 | 23 | 266 |
| `nps_workplace_monthly` | fetched_at | 2026-09-06 00:40:31 | 21 | 34,007 |
| `company_product_mix` | updated_at | 2026-09-07 05:50:44 | 20 | 14,482 |
| `gems_analyst_insights` | created_at | 2026-09-12 07:56:31 | 15 | 2 |
| `price_snapshot_repair_runs` | updated_at | 2026-09-12T09:49:10 | 15 | 4,456 |
| `global_macro_event_reactions` | base_date | 2026-09-16 | 11 | 424 |
| `cf_validation_flags` | created_at | 2026-09-19 15:05:31 | 8 | 107,236 |
| `stock_collection_config` | updated_at | 2026-09-19T09:28:05 | 8 | 310 |
| `cash_conversion_signals` | updated_at | 2026-09-20T03:37:27 | 7 | 61,782 |
| `cost_structure` | collected_at | 2026-09-20 04:25:53 | 7 | 52,968 |
| `dart_backlog_quarterly` | updated_at | 2026-09-20 04:26:28 | 7 | 18,402 |
| `dart_cost_quarterly` | updated_at | 2026-09-20 04:25:53 | 7 | 66,542 |
| `dart_tenbagger_triggers_quarterly` | updated_at | 2026-09-20 04:26:28 | 7 | 104,369 |
| `order_backlog` | collected_at | 2026-09-20 04:26:28 | 7 | 20,807 |
| `cafe_quant_indicator_mappings` | updated_at | 2026-09-21 07:10:42 | 6 | 202 |
| `cafe_signal_mentions` | created_at | 2026-09-21 07:10:07 | 6 | 24,623 |
| `indicator_sector_direction_rules` | updated_at | 2026-09-21 07:10:42 | 6 | 171 |
| `kiwoom_minute_snapshot` | updated_at | 2026-09-21 15:30:31 | 6 | 164,429 |
| `kiwoom_realtime_quote` | updated_at | 2026-09-21 15:30:31 | 6 | 2,639 |
| `kiwoom_sector_investor_net_buy` | created_at | 2026-09-21 19:00:00 | 6 | 688 |
| `macro_signal_backtest_results` | created_at | 2026-09-21 07:50:00 | 6 | 46 |
| `macro_signal_backtest_trades` | created_at | 2026-09-21 07:50:00 | 6 | 3,411 |
| `sector_index_daily` | date | 2026-09-22 | 5 | 35,316 |
| `short_foreign_trade` | bas_dt | 20260922 | 5 | 4,579 |
| `short_monthly_stat` | bas_dt | 20260922 | 5 | 275 |
| `short_sector_daily` | bas_dt | 20260922 | 5 | 3,975,794 |
| `short_sell_daily` | bas_dt | 20260922 | 5 | 3,939,927 |
| `stock_base_info_history` | snapshot_date | 2026-09-22 | 5 | 80,557 |
| `stock_price_daily` | bas_dt | 20260922 | 5 | 917,540 |
| `us_paper_positions` | updated_at | 2026-09-22 06:48:24 | 5 | 5 |

## 행은 있으나 날짜를 해석하지 못한 테이블 9개

`cafe_monthly_generated_reports`, `cafe_monthly_hs_leadership`, `cafe_monthly_sector_leadership`, `forward_estimates`, `global_macro_data`, `quant_major_indicator_series`, `report_files`, `security_share_history`, `trigger_discovery_events`

## 비어 있는 테이블 12개

`broker_order_reconciliation`, `forward_strategy_indicator_series`, `investment_decision_rag_cache`, `investment_decision_reviews`, `investment_decision_tasks`, `kis_paper_positions`, `kiwoom_large_trade_rank`, `listed_company_info`, `live_strategy_approvals`, `price_snapshot_repair_insertions`, `seibro_financial_snapshot`, `signal_data_provenance`
