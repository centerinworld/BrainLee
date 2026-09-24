# PostgreSQL 테이블 목록 (자동 생성)

> `scripts/ops/gen_db_doc.py` 생성 — 2026-09-24 기준 전체 407개(백업/임시 111개 제외 296개). 행수는 추정치. **수동 편집 금지**.
> 컬럼 의미·단위·주의사항은 CLAUDE.md 섹션 2 표(핵심 테이블)와 섹션 8을 본다. 이 문서는 '테이블이 있는지/컬럼명이 뭔지' 확인용.

| 테이블 | 행수 | 컬럼 |
|---|---:|---|
| `ai_analysis_reports` | 16 | id, stock_code, report_date, content, created_at |
| `analyst_pdf_extracts` | 461 | id, report_id, stock_code, target_price, opinion, fwd_eps_1y, fwd_rev_1y, fwd_per, extracted_at, raw_text |
| `autotrade_guard_state` | 2 | k, v, updated_at |
| `backlog_recover_progress` | 6K | stock_code, fiscal_year, fiscal_quarter, report_type, result, checked_at |
| `backtest_run_set_members` | 1K | suite_hash, period_label, run_hash |
| `backtest_run_sets` | 168 | suite_hash, strategy, report_type, manifest_json, created_at |
| `backtest_run_specs` | 3K | run_id, strategy, engine_version, git_commit, signal_timing, execution_timing, market_cap_mode, universe_version, allocation_rule, fee_model, parameter_json, run_hash, supersedes_run_id, created_at |
| `backtest_runs` | 3K | id, run_id, name, start_date, end_date, per_stock, max_pos, status, total_return_pct, ann_return_pct, win_rate, total_trades, profit_trades, max_drawdown_pct …+5 |
| `backtest_signal_data_provenance` | 3K | run_id, run_hash, strategy, stock_code, decision_date, entry_date, dataset, source_row_id, available_at, source_key, created_at |
| `broker_order_reconciliation` | 0 | id, local_order_id, broker_order_no, trade_date, stock_code, side, requested_qty, filled_qty, avg_fill_price, broker_status, reconciliation_status, mismatch_reason, source_payload, reconciled_at |
| `broker_program_market_daily` | 4K | source, dt, market, prog_net_buy_amt, arb_net_buy_amt, non_arb_net_buy_amt, raw_json, updated_at |
| `broker_program_stock_daily` | 1.8M | source, stock_code, dt, close_price, change_rate, trade_volume, sell_qty, buy_qty, net_buy_qty, sell_amt_krw, buy_amt_krw, net_buy_amt_krw, market_channel, raw_json …+1 |
| `bs_items_verify_progress` | 2K | stock_code, year, report_type, result, checked_at |
| `buy_candidates` | 31 | id, stock_code, stock_name, mktcap, target_price, ref_date1, ref_price1, ref_date2, ref_price2, memo, created_at, updated_at |
| `cafe_monthly_generated_reports` | 1 | period, title, summary_text, sector_count, hs_count, generated_at |
| `cafe_monthly_hs_leadership` | 54 | id, period, hs_code, hs_name, sector_name, export_value_usd, export_yoy_pct, export_mom_pct, export_weight_kg, export_unit_price, unit_price_yoy_pct, related_companies, matched_indicator_key, momentum_score …+2 |
| `cafe_monthly_sector_leadership` | 20 | id, period, indicator_key, sector_name, export_value_musd, export_yoy_pct, export_mom_pct, unit_price_yoy_pct, trade_balance_musd, momentum_score, rank_no, source_detail, generated_at |
| `cafe_quant_indicator_mappings` | 202 | id, sector_name, mention_count, indicator_key, indicator_name, status, source_system, confidence, mapping_note, updated_at |
| `cafe_signal_mentions` | 25K | id, cafe_post_id, mention_type, mention_key, mention_name, stock_code, stock_name, sector_name, indicator_name, signal_direction, confidence, evidence, created_at |
| `cafe_signal_post_bodies` | 8K | id, cafe_post_id, cafe_id, article_id, content_text, content_hash, fetched_at |
| `cafe_signal_posts` | 8K | id, cafe_id, board_key, board_name, article_id, title, url, author, published_at, excerpt, content_hash, collected_at, updated_at |
| `cafe_signal_runs` | 9 | id, run_type, period_key, source_board_keys, posts_count, stocks_count, sectors_count, indicators_count, summary_json, generated_at |
| `cafe_stock_indicator_mappings` | 1K | id, stock_code, stock_name, sector_name, indicator_key, indicator_name, mention_count, evidence_terms, example_posts, latest_collected_at, confidence, mapping_note, updated_at, revenue_exposure_pct …+5 |
| `canonical_cashflow_data` | 81K | id, stock_code, year, quarter, is_annual, report_type, operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation, operating_cf_q, investing_cf_q …+9 |
| `canonical_financial_data` | 94K | id, stock_code, year, quarter, is_annual, report_type, revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity, capital_stock, eps …+9 |
| `cash_conversion_signal_runs` | 9 | run_id, started_at, finished_at, rows_written, stocks, notes |
| `cash_conversion_signals` | 62K | stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div, revenue, operating_profit, net_income, operating_cf, capex, free_cf, trade_receivable …+21 |
| `cash_flow_data` | 150K | id, stock_code, year, quarter, is_annual, operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation, created_at, operating_cf_q, investing_cf_q …+7 |
| `cashflow_fix_log` | 82K | id, fixed_at, row_id, stock_code, year, quarter, is_annual, report_type, field_name, old_value, new_value, fix_rule, source, run_id |
| `cf_full_verify_progress` | 13K | stock_code, year, report_type, result, checked_at |
| `cf_survivor_verify_progress` | 17K | stock_code, year, report_type, result, checked_at |
| `cf_validation_flags` | 107K | id, stock_code, year, field, cf_data_id, dart_value, fnguide_value, seibro_value, dart_vs_fnguide_ratio, dart_vs_seibro_ratio, fnguide_vs_seibro_ratio, flag_type, flag_severity, ai_verdict …+7 |
| `cherry_family_learning_runs` | 38 | id, run_type, status, started_at, completed_at, channels_total, channels_active, telegram_message_count, report_file_count, screener_universe_scanned, three_screen_count, two_screen_count, portfolio_profile_count, auto_candidate_count …+1 |
| `company_analysis_profiles` | 394 | stock_code, stock_name, source_family, sector_large, sector_mid, sector_small, value_chain_position, business_model, cycle_type, main_products_json, differentiation_json, bull_points_json, bear_points_json, comparison_summary …+2 |
| `company_mapping_profile` | 17K | id, stock_code, standard_key, source_system, account_id, account_label_raw, mapping_rule, confidence_score, valid_from, valid_to, version, verified_by, verified_at, is_active …+1 |
| `company_peer_relations` | 2K | id, stock_code, peer_code, peer_name, relation_type, rationale, updated_at |
| `company_product_mix` | 14K | id, stock_code, year, category, product_name, revenue_krw, revenue_pct, rcept_no, source, updated_at |
| `consensus_targets` | 9K | id, report_idx, stock_code, stock_name, report_date, securities_firm, analyst, opinion, target_price, prev_target_price, skin_type, report_title, collected_at |
| `contract_advance_signal_runs` | 9 | run_id, started_at, finished_at, rows_written, stocks, notes |
| `contract_advance_signals` | 4K | stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div, contract_liabilities, advances_received, contract_assets, gross_customer_funding, net_customer_funding, revenue, gross_to_revenue_pct …+10 |
| `contracts_verify_progress` | 10K | rcept_no, result, checked_at |
| `corporate_action_events` | 11K | id, stock_code, event_date, event_type, old_shares, new_shares, share_ratio, backward_price_factor, evidence_report_name, evidence_rcept_no, evidence_url, source, confidence, adjustment_status …+3 |
| `cost_breakdown` | 24K | id, stock_code, year, quarter, raw_material_cost, purchased_goods_cost, labor_cost, depreciation_cost, overhead_cost, total_cogs, revenue, material_ratio, labor_ratio, fixed_cost_ratio …+3 |
| `cost_structure` | 53K | id, stock_code, stock_name, year, quarter, raw_material_cost, labor_cost, overhead_cost, total_cogs, revenue, raw_material_ratio, cogs_ratio, yoy_raw_material_chg, data_source …+1 |
| `dart_backlog_quarterly` | 18K | stock_code, fiscal_year, fiscal_quarter, report_type, backlog_amount, backlog_unit, backlog_amount_krw, backlog_confidence, source_excerpt, source_rcept_no, source_report_nm, source_rcept_dt, source_text_hash, parser_version …+2 |
| `dart_bs_items` | 68K | stock_code, year, quarter, item_key, value, report_type, created_at |
| `dart_bs_items_fix_log` | 2K | id, fixed_at, stock_code, year, quarter, report_type, item_key, old_value, new_value, fix_rule, run_id |
| `dart_contracts` | 11K | id, rcept_no, stock_code, stock_name, disclosed_at, report_nm, contract_amount, contract_unit, contract_amount_krw, revenue_base, contract_ratio_pct, counterparty, counterparty_country, is_overseas …+14 |
| `dart_contracts_fix_log` | 3K | fixed_at, rcept_no, field_name, old_value, new_value, run_id |
| `dart_cost_quarterly` | 67K | stock_code, fiscal_year, fiscal_quarter, report_type, material_cost_krw, inventory_assets_krw, depreciation_krw, confidence, source_excerpt, source_rcept_no, source_report_nm, source_rcept_dt, source_text_hash, parser_version …+2 |
| `dart_dilution_events` | 8K | rcept_no, stock_code, corp_name, rcept_dt, report_nm, instrument_type, issue_amount_krw, conversion_price, exercise_price, maturity_date, potential_shares, dilution_ratio_pct, confidence, source_excerpt …+5 |
| `dart_dilution_fix_log` | 5 | fixed_at, rcept_no, field_name, old_value, new_value, run_id |
| `dart_disclosure_cache` | 20 | stock_code, payload_json, updated_at |
| `dart_disclosures` | 817K | stock_code, rcept_no, rcept_dt, report_nm, flr_nm, corp_name, dart_url, fetched_at |
| `dart_employee_count` | 5K | stock_code, year, reprt_code, total_emp, male_emp, female_emp, regular_emp, contract_emp, avg_tenure_years, annual_salary_m, acmtn_dscd |
| `dart_equity_issue_events` | 9K | rcept_no, stock_code, corp_name, rcept_dt, report_nm, event_type, issue_method, issue_amount_krw, issue_price, new_shares, old_shares, dilution_ratio_pct, record_date, listing_date …+6 |
| `dart_equity_issue_fix_log` | 7K | fixed_at, rcept_no, field_name, old_value, new_value, run_id |
| `dart_insider_holdings` | 62K | id, rcept_no, rcept_dt, stock_code, corp_code, corp_name, repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr, sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate, is_ceo …+4 |
| `dart_insider_holdings_fix_log` | 14K | fixed_at, stock_code, rcept_no, repror, field_name, old_value, new_value, run_id |
| `dart_item_mapping_catalog` | 12 | id, canonical_field, account_id, account_nm, sj_nm, fs_div, match_rule, sample_count, first_year, last_year, confidence, active, updated_at |
| `dart_major_holders` | 68K | id, rcept_no, rcept_dt, stock_code, corp_code, corp_name, repror, stkqy, stkrt, ctr_stkqy, ctr_stkrt, report_tp, stk_diff, rt_diff …+2 |
| `dart_major_holders_fix_log` | 0 | fixed_at, stock_code, rcept_no, repror, field_name, old_value, new_value, run_id |
| `dart_material_purchase` | 4K | stock_code, year, period_type, report_type, material_purchase_krw, unit_label, rcept_no, collected_at |
| `dart_material_purchase_fix_log` | 63 | fixed_at, rcept_no, field_name, old_value, new_value, run_id |
| `dart_raw_accounts` | 112 | id, stock_code, year, quarter, report_code, fs_div, sj_nm, account_id, account_nm, thstrm_amount, frmtrm_amount, rcept_no, source_url, fetched_at |
| `dart_rd_patent_signals` | 3K | id, stock_code, rcept_no, rcept_dt, report_nm, signal_type, amount_krw, notes, created_at |
| `dart_report_item_collection_log` | 13K | stock_code, fiscal_year, fiscal_quarter, reprt_code, fs_div, corp_code, status, rows_saved, collected_at |
| `dart_report_item_mapping_qa` | 921 | id, stock_code, fiscal_year, fiscal_quarter, fs_div, metric_name, account_id, account_nm, value, issue_type, severity, detail, reviewed, created_at |
| `dart_report_items_quarterly` | 111K | stock_code, corp_code, fiscal_year, fiscal_quarter, reprt_code, fs_div, metric_name, account_id, account_nm, sj_div, sj_nm, value, rcept_no, updated_at |
| `dart_sga_annual` | 52 | stock_code, year, reprt_code, sga_total, report_type |
| `dart_sga_fix_log` | 2 | fixed_at, stock_code, year, old_value, new_value, run_id |
| `dart_tenbagger_triggers_quarterly` | 104K | stock_code, fiscal_year, fiscal_quarter, report_type, metric_name, metric_value, yoy_pct, qoq_pct, trigger_level, source_table, updated_at |
| `data_availability_ledger` | 240K | dataset, entity_key, period_key, available_at, availability_quality, source_reference, rule_version, updated_at |
| `data_fix_log` | 27 | id, fixed_at, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id |
| `data_lineage_catalog` | 8 | metric_key, display_name, source_name, source_table, period_field, available_at_rule, formula, value_type, quality_rule, collector, owner, updated_at |
| `data_lock` | 7K | id, stock_code, year, table_name, is_locked, locked_at, lock_basis, lock_hash, unlock_reason, unlocked_at, created_at |
| `data_quality_issues` | 79 | id, stock_code, year, quarter, table_name, field_name, reason_code, detail, severity, is_resolved, resolved_at, detected_at |
| `data_quality_repair_log` | 197 | id, run_id, table_name, repair_name, affected_rows, backup_table, created_at |
| `delisting_outcomes` | 0 | stock_code, event_date, outcome_type, successor_stock_code, exchange_ratio, successor_reference_date, cash_per_share, evidence_report_name, evidence_rcept_no, source, confidence, status, note, created_at |
| `depr_q_rebuild_cause` | 45K | id, run_ts, stock_code, year, report_type, cause_code, note |
| `depreciation_q_fix_log` | 38K | id, fixed_at, stock_code, year, quarter, report_type, row_id, old_depreciation_q, new_depreciation_q, cause_code, note |
| `detailed_analysis_files` | 4K | id, post_id, file_name, file_path, file_type, created_at |
| `detailed_analysis_posts` | 55 | id, stock_code, stock_name, title, content_md, source, created_at, updated_at |
| `dilution_events` | 21K | id, stock_code, stock_name, event_type, disclosed_at, rcept_no, issue_amount, conversion_price, shares_to_issue, current_shares, dilution_pct, report_nm, data_source, collected_at …+5 |
| `dilution_reverify_progress` | 2K | rcept_no, result, checked_at |
| `earnings_signals` | 3K | id, stock_code, stock_name, year, quarter, signal_type, detail, ttm_op_cur, ttm_op_prev_q, ttm_op_yoy_base, ttm_rev_cur, ttm_rev_yoy_base, ttm_rev_yoy_pct, ttm_op_accel_pct …+5 |
| `epic_indicator_replacement_plan` | 80 | id, category_code, category_name, sub_code, indicator_name, frequency, unit, latest_date, since_date, status, replacement_family, source_system, collector_path, exactness …+3 |
| `equity_issue_reverify_progress` | 9K | rcept_no, result, checked_at |
| `explainable_stock_signals` | 2K | stock_code, indicator_key, period, indicator_name, latest_value, previous_value, change_pct, direction, revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct, exposure_basis, mapping_confidence, weighted_impact_score …+5 |
| `external_price_verification` | 47K | stock_code, event_date, external_source, external_previous_date, external_previous_close, external_event_close, external_price_ratio, agreement_class, confidence, evidence, verified_at, input_fingerprint |
| `field_verification_status` | 257K | stock_code, year, table_name, field_name, status, sources_checked, note, checked_at |
| `fin_disclosure_dates` | 42K | stock_code, year, quarter, is_annual, disclosure_date, avail_date |
| `fin_full_verify_progress` | 27K | stock_code, year, report_type, result, checked_at |
| `fin_quarterly_validation_flags` | 518K | id, stock_code, year, quarter, field, check_type, dart_value, fnguide_value, annual_value, quarterly_sum, ratio, status, ai_verdict, notes …+8 |
| `financial_anomalies` | 5K | id, stock_code, anomaly_type, severity, description, affected_fields, cfs_ofs_ratio, sample_year, recommended_action, auto_fixable, is_resolved, first_detected, last_checked |
| `financial_data` | 201K | id, stock_code, year, quarter, revenue, operating_profit, net_income, total_assets, total_liabilities, total_equity, capital_stock, eps, bps, dps …+9 |
| `financial_data_verify_progress` | 73K | stock_code, year, report_type, table_name, last_verified_at, last_result |
| `financial_fix_log` | 161K | id, fixed_at, row_id, stock_code, year, quarter, is_annual, report_type, field_name, old_value, new_value, fix_rule, source, run_id |
| `financial_source_snapshot` | 158K | id, stock_code, year, quarter, is_annual, report_type, data_source, source_url, fetched_at, revenue, operating_profit, net_income, eps, bps …+14 |
| `fnguide_dart_mismatch_log` | 2K | id, stock_code, year, field, note, found_at |
| `foreign_flow_quarterly` | 87K | stock_code, year, quarter, frn_net_buy_amt_sum, frn_net_buy_qty_sum, trading_days, weight_end, source, updated_at |
| `foreign_holding_daily` | 108K | id, bas_dt, stock_code, stock_name, frgn_hold_qty, frgn_hold_pct, frgn_limit_pct, created_at |
| `forward_estimate_collection_status` | 3K | stock_code, status, message, last_checked_at |
| `forward_estimate_snapshots` | 4K | stock_code, stock_name, period, is_estimate, revenue_억원, revenue_growth_pct, operating_profit_억원, operating_profit_growth_pct, net_income_억원, net_income_growth_pct, ebitda_십억원, eps_원, eps_growth_pct, per …+10 |
| `forward_estimates` | 1K | id, stock_code, stock_name, period, is_estimate, revenue_억원, revenue_growth_pct, operating_profit_억원, operating_profit_growth_pct, net_income_억원, net_income_growth_pct, ebitda_십억원, eps_원, eps_growth_pct …+11 |
| `forward_strategy_indicator_series` | 0 | id, source_id, category_code, sub_code, data_code, series_type, period, value, raw_value, updated_at |
| `forward_strategy_indicators` | 93 | id, source_id, tab, category_code, category_name, group_id, group_name, sub_code, indicator_name, indicator_type, update_date, data_code, data_name, latest_date …+8 |
| `forward_strategy_industry_categories` | 43 | id, source_id, tab, category_code, category_name, group_id, group_name, description, update_date, raw_json, updated_at |
| `forward_strategy_raw_responses` | 97 | id, source_id, endpoint, params_json, response_hash, response_json, fetched_at |
| `forward_strategy_related_companies` | 711 | id, source_id, category_code, sub_code, stock_code, stock_name, raw_json, updated_at |
| `forward_strategy_sources` | 1 | source_id, source_name, base_url, description, created_at, updated_at |
| `futures_contract_daily` | 424K | id, date, session, market_name, product_name, isu_cd, isu_name, expiry_ym, close, change_, open_, high, low, settle …+6 |
| `futures_daily` | 90 | id, date, product, session, expiry, close, change_, open_, high, low, volume, open_interest |
| `gems_analyst_insights` | 0 | id, target_type, target_name, account_used, growth_outlook_2026_2027, bull_case, bear_case, consensus_summary, raw_response, source_reports_count, created_at |
| `gems_daily_learned_logs` | 0 | id, learned_date, target_type, target_name, source_file_name, account_used, key_insights_summary, growth_2026_2027, bull_vs_bear, tokens_context_size, learned_at, token_quota_snapshot |
| `global_macro_categories` | 177 | id, code, name, name_en, category, subcategory, unit, source, source_code, frequency, description, importance, is_active |
| `global_macro_collection_log` | 463 | id, source, status, records, message, run_at |
| `global_macro_data` | 73K | id, indicator_code, date, value, prev_value, change_pct, created_at |
| `global_macro_event_reactions` | 424 | id, event_id, event_date, country, indicator_code, event_name, surprise_value, surprise_pct, asset_code, asset_name, asset_group, window, base_date, end_date …+6 |
| `global_macro_events` | 30 | id, event_date, event_time, country, indicator_code, event_name, importance, forecast, previous, actual, unit, created_at, surprise_value, surprise_pct …+4 |
| `hypothesis_research_registry` | 1 | research_id, title, hypothesis, verdict, status, latest_run_id, validation_json, updated_at |
| `hypothesis_research_runs` | 1 | run_id, research_id, record_json, publishable, created_at |
| `ifrs_field_rules` | 5 | field, statement_type, ifrs_basis, quarter_transform_rule, stock_flow_type, notes, updated_at |
| `ifrs_mapping_coverage_snapshot` | 220 | id, run_id, source_name, field, year, total_rows, non_null_rows, coverage_pct, created_at |
| `indicator_sector_direction_rules` | 171 | indicator_key, sector_name, direction_mode, note, confidence, updated_at |
| `insider_holdings_verify_progress` | 3K | stock_code, result, checked_at |
| `inventory_sales_signal_runs` | 5 | run_id, started_at, finished_at, rows_written, stocks, notes |
| `inventory_sales_signals` | 54K | stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div, inventory_krw, revenue, order_backlog_krw, order_contracts_krw, inventory_to_revenue_pct, inventory_qoq_pct, inventory_yoy_pct …+11 |
| `investment_decision_rag_cache` | 0 | packet_hash, model_name, rag_json, created_at |
| `investment_decision_reviews` | 0 | id, task_id, provider, model_name, status, result_json, error_text, created_at |
| `investment_decision_tasks` | 0 | id, stock_code, stock_name, status, packet_json, rag_json, created_at, completed_at, error_text |
| `investor_flow_quarterly` | 90K | stock_code, year, quarter, ind_net_sum, frgnr_net_sum, orgn_net_sum, trading_days, source, updated_at |
| `investor_trading_daily` | 4.5M | id, bas_dt, stock_code, stock_name, indv_buy, indv_sell, indv_net, inst_buy, inst_sell, inst_net, frgn_buy, frgn_sell, frgn_net, created_at |
| `kis_paper_orders` | 0 | id, ts, stock_code, side, qty, req_price, fill_price, status, reason, order_krw, mode |
| `kis_paper_positions` | 0 | stock_code, qty, avg_price, updated_at |
| `kis_paper_realized` | 0 | id, ts, stock_code, qty, entry_price, exit_price, pnl |
| `kiwoom_condition_current` | 668 | stock_code, condition_id, condition_name, detected_at, source |
| `kiwoom_condition_definition` | 1 | condition_id, condition_name, first_seen_at, last_seen_at, active |
| `kiwoom_condition_membership` | 59K | id, stock_code, condition_id, condition_name, event_type, event_at, source, captured_at, raw_json |
| `kiwoom_credit_balance` | 4.1M | stock_code, dt, credit_balance_qty, credit_balance_amt, credit_ratio, new_credit_qty, repay_credit_qty, raw_json, updated_at |
| `kiwoom_foreign_flow` | 309K | stock_code, dt, close_price, change_qty, poss_stock_cnt, weight, limit_exhaust_rate, raw_json, updated_at |
| `kiwoom_investor_daily` | 4.7M | stock_code, dt, close_pric, acc_trde_qty, acc_trde_prica, ind_invsr, frgnr_invsr, orgn, fnnc_invt, insrnc, invtrt, etc_fnnc, bank, penfnd_etc …+5 |
| `kiwoom_large_trade_rank` | 0 | snapshot_at, rank_type, market_type, rank_no, stock_code, stock_name, raw_json, created_at |
| `kiwoom_margin_daily` | 93K | stock_code, base_date, credit_balance, credit_buy_balance, credit_sell_balance, loan_balance, short_balance, source_api_id, raw_json, updated_at, credit_ratio |
| `kiwoom_minute_snapshot` | 164K | stock_code, minute_ts, open_price, high_price, low_price, close_price, sum_volume, max_strength, min_strength, avg_strength, sample_count, best_bid1, best_ask1, spread_close …+1 |
| `kiwoom_realtime_quote` | 3K | stock_code, last_price, change_price, change_rate, trade_volume, trade_strength, bid1, ask1, bid_qty1, ask_qty1, source_type, raw_json, updated_at |
| `kiwoom_sector_investor_net_buy` | 628 | snapshot_at, market_type, row_no, sector_code, sector_name, raw_json, created_at |
| `kiwoom_tick_history` | 896K | id, stock_code, source_type, event_ts, last_price, change_price, change_rate, trade_volume, trade_strength, bid1, ask1, bid_qty1, ask_qty1, spread …+2 |
| `kiwoom_us_realtime_quote` | 1 | ticker, exchange_code, source_types, raw_json, updated_at |
| `krx_security_reference` | 7K | stock_code, effective_from, effective_to, stock_name, market, security_type, is_etf_etn, is_equity, quality, source, source_note, collected_at |
| `krx_security_share_snapshot` | 7.0M | stock_code, snapshot_date, shares_issued, stock_name, market, quality, source, collected_at |
| `listed_company_info` | 0 | id, bas_dt, stock_code, stock_name, market, sector, listing_dt, shares, face_val, created_at |
| `live_cash_ledger` | 1 | id, ts, mode, delta_krw, balance_after, reason, ref_order_id |
| `live_fills` | 48 | id, order_id, fill_ts, fill_qty, fill_price, cumulative_qty |
| `live_order_events` | 96 | id, order_id, event_ts, event_type, qty_delta, price, detail |
| `live_orders` | 48 | order_id, parent_order_id, mode, strategy_key, stock_code, side, order_type, qty, limit_price, status, filled_qty, avg_fill_price, reject_reason, decision_reason …+2 |
| `live_signal_outcomes` | 222 | signal_id, horizon_days, outcome_date, outcome_price, return_pct, max_gain_pct, max_loss_pct, status, updated_at |
| `live_signal_registry` | 37 | signal_id, stock_code, signal_type, strategy_id, signal_date, available_at, entry_date, entry_price, price_basis, quality_score, confidence_score, action, signal_payload_json, created_at |
| `live_strategy_approvals` | 0 | strategy_key, approval_status, verification_status, approved_at, expires_at, approved_by, evidence_uri, note, updated_at |
| `macro_signal_backtest_results` | 46 | id, run_id, indicator_key, indicator_name, sector_name, direction_mode, event_count, observation_count, stock_count, avg_ret_20d, median_ret_20d, hit_rate_20d, avg_ret_60d, median_ret_60d …+10 |
| `macro_signal_backtest_trades` | 3K | id, run_id, indicator_key, sector_name, stock_code, stock_name, signal_period, available_date, entry_date, entry_close, ret_20d, ret_60d, ret_120d, mdd_60d …+3 |
| `major_holders_verify_progress` | 2K | stock_code, result, checked_at |
| `margin_balance_daily` | 93K | id, stock_code, dt, credit_balance, credit_amount, credit_ratio, short_balance, data_source, collected_at |
| `market_attention_event` | 0 | id, source, source_url, source_event_id, observed_at, event_type, stock_code, stock_name, sector_name, rank, previous_rank, mention_count, sentiment_score, headline …+1 |
| `market_regime_daily` | 3K | trade_date, index_code, close, ma60, ma200, return_20d, return_60d, volatility_60d, drawdown_252d, trend_regime, volatility_regime, market_regime, regime_score, available_at …+2 |
| `market_signal_briefing` | 238 | id, briefing_date, market, stage, score, buy_allowed, defense_needed, forced_level, summary, factors_json, model, created_at |
| `market_signal_qa_log` | 868 | id, qa_date, market, trade_date, check_name, level, expected_value, actual_value, diff_value, message, payload_json, created_at |
| `material_purchase_reverify_progress` | 1K | rcept_no, result, checked_at |
| `multi_source_financial_mismatch_log` | 9K | id, stock_code, year, field, category, dart_value, fnguide_value, naver_value, yahoo_value, status, note, checked_at |
| `namu_execution_strength_snapshots` | 9K | stock_code, trade_date, observed_at, execution_strength, buy_rate, sell_rate, price, buy_volume, sell_volume, broker_time, source |
| `naver_financial` | 23K | id, stock_code, year, quarter, is_annual, revenue, operating_profit, net_income, total_assets, total_equity, collected_at |
| `naver_price_history_backfill` | 2.5M | stock_code, date, open, high, low, close, volume, source_url, fetched_at |
| `nps_workplace_monthly` | 34K | id, ym, stock_code, stock_name, seq, wkpl_nm, bzowr_rgst_no, wkpl_jnng_stcd, wkpl_styl_dvcd, wkpl_road_addr, nw_acqzr_cnt, lss_jnngp_cnt, match_score, candidate_count …+3 |
| `order_backlog` | 21K | id, stock_code, stock_name, year, quarter, report_type, rcept_no, backlog_amount, backlog_unit, backlog_normalized, new_orders, revenue_base, backlog_to_rev, data_source …+3 |
| `order_contracts` | 11K | id, stock_code, stock_name, rcept_no, rcept_dt, report_nm, is_termination, contract_amount, revenue_ratio_pct, recent_revenue, counterpart, contract_date, contract_start, contract_end …+9 |
| `orderbook_snapshots` | 5 | id, stock_code, observed_at, bid1, ask1, bid_qty1, ask_qty1, total_bid_qty, total_ask_qty, source, raw_json |
| `peak_holding` | 366 | id, stock_name, sector, sector_score, buy_price, current_price, sell_price, quantity, entry_date, hold_days, profit_pct, detected_at, updated_at, sold_at …+8 |
| `peak_trade` | 818 | id, holding_id, stock_name, sector, tx_type, price, quantity, total_amount, profit, profit_pct, tx_at, created_at, strategy, amount …+2 |
| `portfolio` | 53 | id, stock_code, stock_name, sector, quantity, avg_price, created_at, updated_at, broker, owner, source, bought_at |
| `portfolio_sell_signal_alerts` | 447 | id, scan_ts, stock_code, stock_name, severity, score, current_price, avg_price, return_pct, peak_drawdown_pct, reasons_json, sent_telegram |
| `portfolio_snapshot` | 4K | id, snapshot_date, stock_code, stock_name, close_price, quantity, total_value, profit, created_at, avg_price, eval_amount, profit_amt, profit_pct |
| `portfolio_tx` | 86 | id, stock_code, stock_name, tx_type, quantity, price, tx_date, memo, created_at |
| `postgres_data_repair_batches` | 6 | batch_id, created_at, status, repair_counts, restored_at |
| `price_history` | 10.2M | id, stock_code, date, open, high, low, close, volume, inst_net_buy, frn_net_buy, created_at, ind_net_buy, inst_net_buy_amt, frn_net_buy_amt …+2 |
| `price_ingestion_quarantine` | 35K | batch_id, stock_code, source, reason, payload, created_at |
| `price_integrity_quarantine` | 1.9M | stock_code, event_date, reason, evidence, created_at |
| `price_jump_audit` | 21K | stock_code, event_date, previous_date, previous_close, event_close, price_ratio, public_previous_close, public_event_close, public_price_ratio, classification, return_usable, matched_event_type, matched_report_name, evidence …+1 |
| `price_series_registry` | 3 | series_name, price_basis, intended_use, source_detail, mixed_basis_risk, policy_note, updated_at |
| `price_snapshot_provenance` | 1 | stock_code, source, batch_id, first_date, last_date, verified_at |
| `price_snapshot_repair_insertions` | 0 | batch_id, stock_code, date |
| `price_snapshot_repair_runs` | 4K | batch_id, stock_code, status, details, updated_at |
| `price_trading_calendar` | 4K | date |
| `price_verification_state` | 0 | stock_code, event_date, external_source, input_fingerprint, verified_at |
| `program_trading_daily` | 3K | id, dt, market, prog_net_buy_amt, arb_net_buy_amt, non_arb_net_buy_amt, source, updated_at |
| `quant_indicator_signal_events` | 109 | id, indicator_key, indicator_name, series_name, period, value, prev_value, mom_pct, yoy_pct, z_score, signal_type, signal_strength, related_stocks, message …+2 |
| `quant_major_indicator_catalog` | 301 | indicator_key, epic_category_code, epic_sub_code, epic_indicator_name, frequency, base_unit, status, replacement_family, source_system, collector_path, exactness, priority, notes, enabled …+2 |
| `quant_major_indicator_series` | 218K | id, indicator_key, period, series_name, value, unit, source_name, source_detail, quality, updated_at |
| `quant_market_regime_signal` | 2K | trade_date, credit_health_pct, flow_health_pct, regime_score, created_at |
| `quant_stock_trade_signal_snapshots` | 303 | id, signal_date, stock_code, stock_name, action, score, positive_drivers, negative_drivers, drivers_json, policy, generated_at |
| `radar_market_cache` | 266 | ticker, country, market_cap, pbr, per, latest_date, latest_close, updated_at |
| `radar_price_cache` | 79K | ticker, rn, close, trade_date |
| `radar_sector_override` | 574 | id, sort_order, lv0, lv1, lv2, company_name, country_raw, ticker, level2_signal_5d, level2_signal_10d, level2_signal_30d, country_flag, updated_at, lv0_industry_overview …+2 |
| `radar_semiconductor_override` | 168 | id, sort_order, lv0, lv1, lv2, company_name, country_raw, ticker, level2_signal_5d, level2_signal_10d, level2_signal_30d, country_flag, updated_at, lv2_investment_view …+2 |
| `report_files` | 26K | id, channel_id, message_id, stock_code, stock_name, report_date, file_name, saved_name, file_path, file_size, mime_type, caption, created_at, sector …+2 |
| `risk_gate_decisions` | 2K | id, ts, stock_code, side, strategy_key, decision, reasons, gate_snapshot, order_id, decision_source |
| `run_verification_artifacts` | 14K | run_hash, artifact_type, passed, details_json, artifact_hash, created_at |
| `sector_index_daily` | 35K | id, date, market, sector, close, change_, change_rate, open_, high, low, volume |
| `sector_info` | 0 | id, sector_name, stock_code |
| `sector_insights` | 15 | id, lv0, lv1, lv2, insight_type, description, updated_at |
| `sector_posts` | 129 | id, title, blog_url, post_date, ai_summary, telegram_sent, created_at |
| `sector_rotation_cache` | 4 | cache_key, payload_json, as_of, market_status, computed_at, updated_at |
| `sector_stocks` | 697 | id, post_id, category, stock_name, stock_code, ref_price, memo |
| `security_master_history` | 5K | stock_code, effective_from, effective_to, stock_name, market, security_type, is_etf_etn, is_tradable, interval_quality, source, source_note, updated_at |
| `security_share_history` | 112K | stock_code, effective_from, effective_to, shares_issued, quality, source, confidence, updated_at |
| `segment_revenue` | 26K | id, stock_code, corp_code, year, quarter, segment_name, revenue, operating_profit, assets, revenue_pct, report_type, rcept_no, updated_at |
| `seibro_financial_snapshot` | 0 | id, stock_code, year, quarter, is_annual, report_type, revenue, operating_profit, net_income, source_action, source_account_map, fetched_at |
| `selected_price_repair_batches` | 8 | batch_id, status, source, target_codes_json, eligible_codes_json, created_at, applied_at, restored_at |
| `selected_price_repair_inserted` | 539 | batch_id, stock_code, date |
| `selected_run_registry` | 26 | strategy, report_type, run_hash, selected_at, selected_by, note |
| `semiconductor_valuestream` | 151 | id, sort_order, stock_code, company_name, lv1, lv2, customers, main_business, ticker_raw, etf_flag, created_at |
| `short_foreign_balance` | 224 | id, bas_dt, ntiv_brw_bal, forg_brw_bal, brw_bal_forg_rto, ntiv_lndn_bal, forg_lndn_bal, lndn_bal_forg_rto, created_at |
| `short_foreign_trade` | 5K | id, bas_dt, forg_lnb_ccl_stck_cnt, forg_lnb_ccl_amt, ntiv_lnb_ccl_stck_cnt, ntiv_lnb_ccl_amt, sum_lnb_ccl_stck_cnt, sum_lnb_ccl_amt, created_at |
| `short_monthly_stat` | 272 | id, bas_dt, lnb_expr_itms_cnt, lnb_ccl_stck_cnt, lnb_ccl_amt, lnb_rdpt_stck_cnt, lnb_rdpt_amt, lnb_rman_stck_cnt, lnb_bal, created_at |
| `short_rank_daily` | 4.0M | id, bas_dt, isin_cd, stock_code, stock_name, lnb_scrt_dcd, lnb_ccl_stck_cnt, rcal_rdpt_stck_cnt, rdpt_stck_cnt, lnb_rman_stck_cnt, lnb_bal, created_at |
| `short_sale_daily` | 5K | trade_date, stock_code, short_qty, short_amt, short_volume_ratio, short_amount_ratio, trade_volume, trade_amount, source, collected_at |
| `short_sector_daily` | 4.0M | id, bas_dt, isin_cd, stock_code, stock_name, sic_cd, sic_nm, stck_lndn_bal, stck_lndn_rto, stck_brw_bal, stck_brw_rto, created_at |
| `short_sell_daily` | 3.9M | id, bas_dt, stock_code, stock_name, short_qty, short_amt, borrow_bal_qty, borrow_bal_amt, borrow_bal_pct, created_at, short_rdpt_qty |
| `signal_config` | 26 | id, scope, name, label, description, logic_type, params, weight, is_active, sort_order, created_at |
| `signal_data_provenance` | 0 | run_hash, stock_code, decision_date, year, quarter, is_annual, avail_date_used, created_at |
| `signal_experiment_ledger` | 234 | id, strategy_key, experiment_name, hypothesis, baseline_avg6, baseline_pos, treatment_avg6, treatment_pos, verdict, detail, tested_at |
| `signal_quality_scores` | 1 | signal_id, stock_code, signal_date, signal_type, strategy_family, market_regime, quality_score, confidence_score, action, expected_return_pct, downside_pct, positive_rate_pct, sample_count, data_quality …+4 |
| `signal_result` | 14K | id, config_id, stock_code, signal, score, value, description, calc_date, created_at |
| `source_field_mapping_rules` | 15 | id, source_name, source_field, canonical_field, report_type_scope, transform_rule, source_priority, verification_rule, active, updated_at |
| `source_intelligence_mentions` | 781 | id, source_key, source_type, source_name, message_id, published_at, ticker, company_name, asset_class, detection, stance, signal_level, evidence_type, summary_text …+3 |
| `source_intelligence_sources` | 1 | source_key, source_name, source_type, enabled, refresh_frequency, last_refreshed_at, created_at, updated_at |
| `stock_base_info_changes` | 5K | id, stock_code, change_date, change_type, old_value, new_value, description, created_at, source, confidence, evidence_report_name |
| `stock_base_info_history` | 81K | id, stock_code, snapshot_date, market_cap, shares_issued, sector_type, secugrp_nm, stock_name |
| `stock_bizno_map` | 29 | stock_code, stock_name, biz_no_6, source, updated_at |
| `stock_collection_config` | 310 | stock_code, config_key, config_value, reason, source, created_at, updated_at |
| `stock_dart_data_quality` | 3K | stock_code, status, note, ok_years, fail_years, checked_at, source |
| `stock_meta` | 3K | id, stock_code, stock_name, market, sector, created_at, updated_at, float_shares, shares_outstanding, float_updated_at |
| `stock_price_daily` | 909K | id, bas_dt, stock_code, stock_name, market, open_price, high_price, low_price, close_price, vs, change_pct, volume, trade_amt, market_cap …+2 |
| `stock_sector_tags` | 7K | id, stock_code, stock_name, sector, source, confidence, is_primary, evidence, strategy, observed_at, updated_at |
| `stock_shareholder_profile` | 3K | stock_code, stock_name, market, base_date, shares_issued, shares_outstanding, float_shares, treasury_shares_est, free_float_ratio, major_holder_name, major_holder_shares, major_holder_ratio, major_holder_report_date, major_holder_report_no …+6 |
| `stock_universe` | 5K | id, stock_code, stock_name, market, stock_type, base_date, close, open, high, low, change_rate, volume, trading_value, market_cap …+28 |
| `stockeasy_analysis` | 284 | id, strategy, analyzed_at, holdings_cnt, exits_cnt, analysis_text, holdings_json, exits_json, created_at |
| `stockeasy_autotrade_manual_hold` | 1 | strategy, stock_code, reason, created_at |
| `stockeasy_sector_membership` | 10K | id, stock_code, stock_name, sector_name, sector_level, source_snapshot_date, source_url, observed_at |
| `stockeasy_sector_rs_daily` | 47 | dt, sector_name, rs_score, source_portfolio_id, collected_at |
| `stockeasy_sync_state` | 148 | strategy, stock_code, stock_name, is_active, first_seen_at, last_seen_at, updated_at, id |
| `strategy_feature_snapshot` | 190K | snapshot_date, stock_code, stock_name, market, sector_large, close_price, market_cap_억, market_cap_log, per, pbr, ret_20d, ret_60d, ret_120d, dist_high_252 …+21 |
| `strategy_feature_snapshot_pit_v2` | 188K | snapshot_date, stock_code, stock_name, market, sector_large, close_price, market_cap_억, market_cap_log, per, pbr, ret_20d, ret_60d, ret_120d, dist_high_252 …+27 |
| `strategy_regime_policy` | 16 | strategy_family, market_regime, suitability_score, action, rationale, updated_at |
| `strict_backtest_runs` | 0 | run_id, strategy_id, started_at, completed_at, signal_date_min, signal_date_max, decision_time, execution_price_type, price_basis, transaction_cost_bps, slippage_bps, lookahead_violations, availability_fallback_rows, status …+1 |
| `system_hardening_plan` | 16 | id, category, phase, title, description, priority, status, owner, evidence, target_note, created_at, updated_at |
| `telegram_channels` | 14 | id, channel_id, channel_name, is_active, last_sync, created_at, entity_hint |
| `telegram_messages` | 13K | id, channel, message_id, text, date, summary, stocks, collected_at |
| `telegram_stock_alert_state` | 371 | alert_namespace, stock_code, stock_name, first_sent_at, last_seen_at, sent_count, last_payload |
| `telegram_stock_mentions` | 21 | id, stock_name, market, mention_count, period_start, period_end, created_at |
| `tenbagger_ai_analysis` | 8 | stock_code, generated_at, score, reasons, ai_analysis, model |
| `tenbagger_daily_alerts` | 613 | id, alert_date, stock_code, stock_name, total_score, axis_breakdown, reasons, is_new, best_reason, created_at |
| `tenbagger_results` | 4K | id, run_time, run_type, stock_code, stock_name, total_score, score_detail, reasons, ai_analysis, current_price, market_cap, per, pbr, roe …+7 |
| `tg_daily_mentions` | 916 | id, mention_date, stock_name, market, mention_count |
| `theme_membership_snapshot` | 97K | snapshot_date, stock_code, stock_name, theme, taxonomy, source, captured_at |
| `trading_restrictions` | 26 | stock_code, as_of, is_tradable, is_halted, is_management, warning_level, is_short_overheat, source, raw_json, updated_at |
| `treasury_buyback` | 13K | id, stock_code, corp_name, rcept_no, rcept_dt, event_type, report_nm, updated_at |
| `trigger_discovery_events` | 109K | event_id, source, trigger_key, trigger_name, event_date, available_date, period, entity_type, entity_key, sector_name, stock_code, value, z_score, mom_pct …+5 |
| `trigger_discovery_forward_returns` | 878K | event_id, stock_code, horizon_days, entry_date, entry_close, exit_date, exit_close, return_pct, max_drawdown_pct, fill_gap_days, created_at |
| `trigger_discovery_stock_links` | 314K | event_id, stock_code, stock_name, sector_name, link_source, confidence, revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct, created_at |
| `triple_pattern_daily` | 354 | id, run_date, stock_code, stock_name, sector, market, mktcap_100m, per, pbr, roe, op_margin_pct, net_margin_pct, avg_inst_60d_100m, avg_frn_60d_100m …+5 |
| `turnover_breakout_live_log` | 0 | id, run_ts, trade_date, market, stock_code, stock_name, score, last_price, turnover_pct, body_pct, vol_ratio_20d, inst_net_buy_amt_억, frn_net_buy_amt_억, flow_sum_억 …+3 |
| `us_biotech_clinical_trials` | 3K | ticker, nct_id, title, status, phase, conditions_json, interventions_json, start_date, primary_completion_date, completion_date, last_update_date, sponsor_name, source_url, updated_at …+2 |
| `us_biotech_consensus_snapshot` | 154 | ticker, target_mean_price, target_high_price, target_low_price, recommendation_key, recommendation_mean, analyst_count, source, updated_at |
| `us_biotech_fda_labels` | 625 | ticker, product_key, brand_name, generic_name, manufacturer_name, indications, boxed_warning, effective_time, source_url, updated_at |
| `us_biotech_news` | 2K | ticker, news_id, published_at, title, publisher, url, summary, source, updated_at |
| `us_biotech_pipeline_snapshot` | 154 | ticker, company_name, filing_date, form, accession_no, source_url, pipeline_json, source_excerpt, extraction_status, source_text_hash, parser_version, last_error, updated_at |
| `us_cashflow_data` | 79K | ticker, period_end, period_type, operating_cf, investing_cf, financing_cf, capex, depreciation, created_at, updated_at, change_working_capital, stock_based_compensation, dividends_paid, cash_begin …+2 |
| `us_data_integrity_audit` | 700 | id, ticker, period_end, period_type, check_type, field, db_value, ref_value, ratio, severity, note, run_at |
| `us_disclosures` | 162K | ticker, filing_date, form, title, url, accession_no, created_at |
| `us_factor_snapshot` | 4K | ticker, as_of_date, price, market_cap, sector, industry, return_1m, return_3m, return_6m, return_1y, ma50, ma200, above_200ma, high_52w …+25 |
| `us_financial_data` | 79K | ticker, period_end, period_type, revenue, operating_income, net_income, eps, bps, roe, roa, per, pbr, opm, created_at …+14 |
| `us_frontend_snapshot` | 4K | ticker, as_of_date, price, change_pct, market_cap, annual_revenue, annual_operating_income, annual_net_income, opm, fifty_two_week_high, fifty_two_week_low, per, pbr, eps …+8 |
| `us_paper_cash_ledger` | 21 | id, ts, delta_usd, balance_after, reason, ref_order_id |
| `us_paper_orders` | 20 | id, ts, ticker, side, qty, req_price, fill_price, status, reason, amount_usd, strategy_key, signal_snapshot, price_as_of, mode |
| `us_paper_positions` | 6 | ticker, qty, avg_price, updated_at, strategy_key |
| `us_paper_realized` | 7 | id, ts, ticker, qty, entry_price, exit_price, pnl_usd, strategy_key |
| `us_paper_run_log` | 6 | market_date, started_at, completed_at, status, result_json |
| `us_price_history` | 4.0M | ticker, date, close, volume, created_at, open, high, low |
| `us_stock_meta` | 4K | ticker, company_name, exchange, index_name, sector, industry, market_cap, country, currency, updated_at |
| `valuation_history` | 64K | id, stock_code, year, quarter, period_end, close_price, eps, bps, per, pbr, market_cap_억, shares_issued, data_source, updated_at |
| `virtual_cash_accounts` | 18 | strategy, initial_cash, balance_krw, total_fees, total_taxes, total_slippage, realized_pnl_gross, realized_pnl_net, created_at, updated_at |
| `virtual_cash_ledger` | 180 | id, strategy, event_type, stock_code, stock_name, holding_id, quantity, price, gross_amount, fee, tax, slippage_cost, cash_delta, balance_after …+5 |
| `virtual_position_costs` | 86 | holding_id, strategy, stock_code, stock_name, buy_fee, buy_slippage, gross_cost, opened_at, closed_at |
| `virtual_strategy_runs` | 80 | id, strategy, run_at, status, sold_count, bought_count, message |
| `watchlist` | 191 | id, stock_code, added_at |
| `write_gate_log` | 43K | id, gate_ts, table_name, stock_code, year, quarter, is_annual, report_type, level, reason_code, message, payload_json |

## 백업/임시/일자별 스냅샷 테이블 (111개, 조회 대상 아님)

`canonical_financial_data_backup_20260906_empty_rows`(133), `cash_flow_data_backup_20260905_dedup`(17K), `cash_flow_data_backup_annual_gap_fix_20260830`(18K), `cash_flow_data_backup_dedup_20260828`(141K), `cash_flow_data_backup_mismatch_verify_20260828`(550), `cash_flow_data_full_backup_20260822`(141K), `corporate_action_events_backup2_20260823`(6K), `corporate_action_events_backup_20260823`(7K), `cost_breakdown_backup_20260822`(24K), `dart_employee_count_backup_20260906_dedup`(3K), `dart_employee_count_backup_dedup_20260829`(5K), `dart_insider_holdings_backup_20260822`(1), `dart_insider_holdings_backup_20260904_dup`(11K), `dart_insider_holdings_backup_20260919`(62K), `dart_insider_holdings_backup_dedup_20260829`(79K), `dart_insider_holdings_null_bridge_backup_20260920`(28K), `dart_major_holders_backup_20260906_dedup`(25K), `dart_major_holders_backup_dedup_20260829`(66K), `dart_material_purchase_backup_20260822`(5), `dart_material_purchase_backup_mismatch_verify_20260828`(4K), `dart_report_items_quarterly_backup_20260906_dedup`(6K), `data_quality_backup_i10_equity_spike_20260910_062000`(0), `data_quality_backup_i10_equity_spike_20260911_062000`(0), `data_quality_backup_i10_equity_spike_20260912_062000`(0), `data_quality_backup_i10_equity_spike_20260913_062000`(0), `data_quality_backup_i10_equity_spike_20260914_062001`(0), `data_quality_backup_i10_equity_spike_20260915_062000`(0), `data_quality_backup_i10_equity_spike_20260916_062000`(0), `data_quality_backup_i10_equity_spike_20260917_062000`(0), `data_quality_backup_i10_equity_spike_20260918_062000`(0), `data_quality_backup_i10_equity_spike_20260919_062001`(0), `data_quality_backup_i10_equity_spike_20260920_062000`(0), `data_quality_backup_i10_equity_spike_20260921_062000`(0), `data_quality_backup_i10_equity_spike_20260922_062000`(0), `data_quality_backup_i10_equity_spike_20260923_062001`(0), `data_quality_backup_i10_equity_spike_20260924_062000`(0), `data_quality_backup_i4_quarter_revenue_20260910_062000`(0), `data_quality_backup_i4_quarter_revenue_20260911_062000`(0), `data_quality_backup_i4_quarter_revenue_20260912_062000`(0), `data_quality_backup_i4_quarter_revenue_20260913_062000`(0), `data_quality_backup_i4_quarter_revenue_20260914_062001`(0), `data_quality_backup_i4_quarter_revenue_20260915_062000`(0), `data_quality_backup_i4_quarter_revenue_20260916_062000`(0), `data_quality_backup_i4_quarter_revenue_20260917_062000`(0), `data_quality_backup_i4_quarter_revenue_20260918_062000`(0), `data_quality_backup_i4_quarter_revenue_20260919_062001`(0), `data_quality_backup_i4_quarter_revenue_20260920_062000`(0), `data_quality_backup_i4_quarter_revenue_20260921_062000`(0), `data_quality_backup_i4_quarter_revenue_20260922_062000`(0), `data_quality_backup_i4_quarter_revenue_20260923_062001`(0), `data_quality_backup_i4_quarter_revenue_20260924_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260910_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260911_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260912_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260913_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260914_062001`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260915_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260916_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260917_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260918_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260919_062001`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260920_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260921_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260922_062000`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260923_062001`(0), `data_quality_backup_i5_i9_cashflow_extreme_20260924_062000`(0), `dilution_events_anomaly_backup_20260822`(11), `dilution_events_backup2_20260825`(90), `financial_data_backup_20260902_ofs_isannual`(5K), `financial_data_backup_20260902_ofs_q4_dup_delete`(5K), `financial_data_backup_annual_gap_fix_20260830`(737), `financial_data_backup_dedup_20260828`(195K), `financial_data_backup_fnguide_sync_20260917`(178K), `financial_data_backup_fnguide_sync_20260918`(178K), `financial_data_backup_fnguide_sync_20260919`(178K), `financial_data_backup_fnguide_sync_20260920`(178K), `financial_data_backup_fnguide_sync_20260921`(201K), `financial_data_backup_fnguide_sync_20260922`(201K), `financial_data_backup_fnguide_sync_20260923`(201K), `financial_data_backup_fnguide_sync_20260924`(201K), `financial_data_backup_manual_pref_fix_20260830`(14), `financial_data_backup_mismatch_verify_20260828`(547), `financial_data_backup_naver_derive_20260830`(492), `financial_data_backup_preferred_naver_fix_20260830`(377), `financial_data_backup_q2anomaly2_20260822`(5), `financial_data_backup_q2anomaly_20260822`(25), `financial_data_full_backup_20260822`(195K), `order_backlog_anomaly_backup_20260822`(15), `order_backlog_backup2_20260825`(1), `postgres_data_repair_backup`(41K), `price_history_backup_003200_20260823`(2), `price_history_backup_ohl0_20260822`(1K), `price_history_corruption_backup_20260823`(1K), `price_history_corruption_backup_20260823b`(507), `price_history_corruption_backup_20260829`(134), `price_history_fix_backup`(3.3M), `price_history_h1_2022_burst_backup_20260829`(346), `price_history_holiday_phantom_backup_20260824`(90), `price_history_jan03_residual_backup_20260829`(12), `price_snapshot_repair_backup`(2K), `security_master_history_backup_codex_20260908`(4K), `security_master_history_backup_codex_20260923`(5K), `security_share_history_backup_codex_20260908`(95K), `security_share_history_backup_codex_20260923`(97K), `segment_revenue_backup_20260822`(142), `selected_price_repair_backup`(152K), `selected_price_repair_stage`(208K), `short_sell_pct_anomaly_backup_20260822`(782), `stock_meta_backup_20260822`(89), `us_cashflow_data_backup_20260906_period_end_drift`(3K), `us_financial_data_backup_20260904_period_end_drift`(3K)
