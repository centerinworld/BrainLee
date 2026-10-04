# 데이터 신선도 전체 점검 — 2026-10-02 (자동 생성: scripts/ops/audit_data_freshness.py)

- 대상 테이블 235개(제외 183: 백업·로그·일회성 / 날짜 컬럼 없음 65 / 조회 오류 1). 기준: 오늘(2026-10-02) 대비 **7일 이상** 뒤처진 것.
- 주의: 월·분기·연간 데이터, 이벤트 구동(조건 충족 시만 적재), 일회성 연구 테이블은 오래돼도 정상일 수 있다 — 아래는 후보 목록이며 원인 분류는 `원인` 열/본문 참조.

## 뒤처진 테이블 78개

| 테이블 | 기준 컬럼 | 최신 | 뒤처진 일수 | 행수 |
|---|---|---|--:|--:|
| `selected_price_repair_inserted` | date | 2018-12-28 | 2835 | 539 |
| `ai_analysis_reports` | report_date | 2026-03-22 13:13:37 | 194 | 16 |
| `telegram_stock_mentions` | created_at | 2026-03-29 18:34:19 | 187 | 21 |
| `signal_config` | created_at | 2026-04-04 12:14:19 | 181 | 26 |
| `futures_daily` | date | 2026-04-17 | 168 | 90 |
| `stock_bizno_map` | updated_at | 2026-04-19 11:53:58 | 166 | 29 |
| `futures_contract_daily` | date | 2026-04-20 | 165 | 423,852 |
| `radar_semiconductor_override` | updated_at | 2026-05-03 13:38:24 | 152 | 168 |
| `sector_insights` | updated_at | 2026-05-03 13:38:24 | 152 | 15 |
| `semiconductor_valuestream` | created_at | 2026-05-03 13:59:25 | 152 | 151 |
| `dart_disclosure_cache` | updated_at | 2026-05-10 21:43:28 | 145 | 20 |
| `buy_candidates` | updated_at | 2026-05-13 08:13:21 | 142 | 31 |
| `dart_raw_accounts` | fetched_at | 2026-05-16T17:25:43 | 139 | 112 |
| `data_lock` | created_at | 2026-05-16 08:26:03 | 139 | 6,840 |
| `stock_meta` | updated_at | 2026-05-24 22:02:26 | 131 | 2,784 |
| `naver_financial` | collected_at | 2026-05-27 03:46:16 | 128 | 22,768 |
| `dart_item_mapping_catalog` | updated_at | 2026-05-31 02:48:40 | 124 | 12 |
| `ifrs_field_rules` | updated_at | 2026-05-31 02:48:40 | 124 | 5 |
| `ifrs_mapping_coverage_snapshot` | created_at | 2026-05-31 02:48:40 | 124 | 220 |
| `source_field_mapping_rules` | updated_at | 2026-05-31 02:48:40 | 124 | 15 |
| `forward_strategy_indicators` | updated_at | 2026-06-06 13:04:29 | 118 | 93 |
| `forward_strategy_industry_categories` | updated_at | 2026-06-06 13:04:29 | 118 | 43 |
| `forward_strategy_raw_responses` | fetched_at | 2026-06-06 13:02:09 | 118 | 97 |
| `forward_strategy_related_companies` | updated_at | 2026-06-06 13:04:29 | 118 | 711 |
| `forward_strategy_sources` | updated_at | 2026-06-06 13:04:29 | 118 | 1 |
| `cost_breakdown` | collected_at | 2026-06-09 16:42:23 | 115 | 23,678 |
| `foreign_flow_quarterly` | updated_at | 2026-06-11 08:57:09 | 113 | 87,474 |
| `investor_flow_quarterly` | updated_at | 2026-06-11 09:05:04 | 113 | 90,150 |
| `radar_price_cache` | trade_date | 2026-06-19 | 105 | 79,082 |
| `dart_report_item_mapping_qa` | created_at | 2026-06-22 12:00:54 | 102 | 921 |
| `cafe_signal_post_bodies` | fetched_at | 2026-07-10 22:52:23 | 84 | 7,600 |
| `investor_trading_daily` | bas_dt | 2026-07-10 | 84 | 4,519,957 |
| `dart_material_purchase` | collected_at | 2026-07-11 01:50:15 | 83 | 3,801 |
| `epic_indicator_replacement_plan` | updated_at | 2026-07-11 05:10:48 | 83 | 80 |
| `hypothesis_research_registry` | updated_at | 2026-07-12T16:49:16 | 82 | 1 |
| `hypothesis_research_runs` | created_at | 2026-07-12T16:49:16 | 82 | 1 |
| `signal_quality_scores` | signal_date | 2026-07-12 | 82 | 1 |
| `company_mapping_profile` | created_at | 2026-07-21 12:09:56 | 73 | 17,347 |
| `stockeasy_autotrade_manual_hold` | created_at | 2026-07-21 14:10:17 | 73 | 1 |
| `stockeasy_sector_rs_daily` | dt | 2026-07-22 | 72 | 47 |
| `contract_advance_signals` | updated_at | 2026-07-26T19:20:25 | 68 | 3,983 |
| `dart_bs_items` | created_at | 2026-07-26 10:20:21 | 68 | 67,705 |
| `dart_report_items_quarterly` | updated_at | 2026-07-26 10:20:21 | 68 | 111,194 |
| `portfolio_tx` | created_at | 2026-07-26 13:00:48 | 68 | 86 |
| `autotrade_guard_state` | updated_at | 2026-07-27 15:27:07 | 67 | 2 |
| `tenbagger_daily_alerts` | created_at | 2026-07-27 22:30:00 | 67 | 613 |
| `triple_pattern_daily` | updated_at | 2026-07-27 14:59:18 | 67 | 354 |
| `segment_revenue` | updated_at | 2026-08-01 20:29:28 | 62 | 25,624 |
| `data_availability_ledger` | updated_at | 2026-08-02T03:04:07 | 61 | 240,364 |
| `naver_price_history_backfill` | date | 2026-08-07 | 56 | 2,547,912 |
| `telegram_messages` | date | 2026-08-08 12:17:06 | 55 | 13,361 |
| `telegram_channels` | created_at | 2026-08-11 09:16:24 | 52 | 14 |
| `trading_restrictions` | as_of | 2026-08-13T23:13:59 | 50 | 26 |
| `selected_price_repair_batches` | created_at | 2026-08-14T22:34:08 | 49 | 8 |
| `quant_market_regime_signal` | trade_date | 2026-08-21 | 42 | 1,919 |
| `postgres_data_repair_batches` | created_at | 2026-08-23 08:37:58 | 40 | 6 |
| `cafe_signal_posts` | collected_at | 2026-08-24 07:10:06 | 39 | 8,225 |
| `kiwoom_us_realtime_quote` | updated_at | 2026-08-25 22:43:30 | 38 | 1 |
| `system_hardening_plan` | updated_at | 2026-08-25 22:47:32 | 38 | 16 |
| `inventory_sales_signals` | updated_at | 2026-09-03T21:04:56 | 29 | 54,237 |
| `canonical_cashflow_data` | updated_at | 2026-09-04T12:31:38 | 28 | 80,592 |
| `radar_market_cache` | updated_at | 2026-09-04 12:54:07 | 28 | 266 |
| `nps_workplace_monthly` | fetched_at | 2026-09-06 00:40:31 | 26 | 34,007 |
| `company_product_mix` | updated_at | 2026-09-07 05:50:44 | 25 | 14,482 |
| `gems_analyst_insights` | created_at | 2026-09-12 07:56:31 | 20 | 2 |
| `price_snapshot_repair_runs` | updated_at | 2026-09-12T09:49:10 | 20 | 4,456 |
| `global_macro_event_reactions` | base_date | 2026-09-16 | 16 | 430 |
| `cf_validation_flags` | created_at | 2026-09-19 15:05:31 | 13 | 107,236 |
| `stock_collection_config` | updated_at | 2026-09-19T09:28:05 | 13 | 310 |
| `cash_conversion_signals` | updated_at | 2026-09-20T03:37:27 | 12 | 61,782 |
| `cost_structure` | collected_at | 2026-09-20 04:25:53 | 12 | 52,968 |
| `us_paper_positions` | updated_at | 2026-09-22 06:48:24 | 10 | 5 |
| `delisting_outcomes` | created_at | 2026-09-23T17:19:14 | 9 | 2 |
| `foreign_holding_daily` | bas_dt | 20260923 | 9 | 304,260 |
| `stock_universe_history` | base_date | 2026-09-23 | 9 | 5,216 |
| `quant_indicator_signal_events` | period | 2026-09-24 | 8 | 132 |
| `valuation_history` | updated_at | 2026-09-24 18:36:24 | 8 | 66,080 |
| `us_frontend_snapshot` | as_of_date | 2026-09-25 | 7 | 3,676 |

## 행은 있으나 날짜를 해석하지 못한 테이블 9개

`cafe_monthly_generated_reports`, `cafe_monthly_hs_leadership`, `cafe_monthly_sector_leadership`, `forward_estimates`, `global_macro_data`, `quant_major_indicator_series`, `report_files`, `security_share_history`, `trigger_discovery_events`

## 비어 있는 테이블 12개

`broker_order_reconciliation`, `forward_strategy_indicator_series`, `investment_decision_rag_cache`, `investment_decision_reviews`, `investment_decision_tasks`, `kis_paper_positions`, `listed_company_info`, `live_strategy_approvals`, `paper_order_queue`, `price_snapshot_repair_insertions`, `seibro_financial_snapshot`, `signal_data_provenance`
