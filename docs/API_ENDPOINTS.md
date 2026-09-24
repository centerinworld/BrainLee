# API 엔드포인트 목록 (자동 생성)

> `scripts/ops/gen_api_doc.py` 생성 — 2026-09-24 기준 459개 / 그룹 51개. **수동 편집 금지**(서버 기동 후 재실행). 파라미터는 `http://127.0.0.1:8000/docs`.

## `/` (1)
`GET /`

## `/api/antigravity` (1)
`GET /monitoring-status`

## `/api/backtest` (43)
`GET /combinations/list`, `POST /combinations/simulate`, `GET /continuous-returns`, `GET /experiment-ledger`, `GET /hardening-plan`, `GET /list`, `GET /matrix`, `GET /monthly-picks`, `GET /registry`, `GET /registry/candidates`, `POST /registry/select`, `POST /registry/suites`, `POST /run`, `POST /run-all-matrix`, `POST /run-composite`, `POST /run-deep-recovery`, `POST /run-golden-cross`, `POST /run-high-profit-compound`, `POST /run-low-base-breakout`, `POST /run-minervini`, `POST /run-recovery`, `POST /run-regime-adaptive`, `POST /run-sector`, `POST /run-turnaround`, `POST /run-v1`, `POST /run-v1-dart`, `POST /run-v1-value`, `POST /run-v10`, `POST /run-v10-hs`, `POST /run-v11`, `POST /run-v11-hs`, `POST /run-v12`, `POST /run-v2`, `POST /run-v5`, `POST /run-v8`, `POST /run-vbr`, `GET /security-master/{stock_code}`, `GET /strategies`, `POST /strategy-research/rebuild`, `GET /strategy-research/summary`, `GET /verification/{run_hash}`, `GET /{run_id}`, `DELETE /{run_id}`

## `/api/buy-candidates` (6)
`GET /`, `POST /`, `GET /auto-board`, `GET /short-sell/{stock_code}`, `PATCH /{stock_code}`, `DELETE /{stock_code}`

## `/api/cafe-signals` (14)
`POST /collect`, `GET /indicator-traffic-lights`, `GET /leadership`, `GET /macro-signal-backtests`, `GET /mentions`, `GET /posts`, `GET /quant-indicator-signals`, `GET /quant-mappings`, `GET /runs`, `GET /sector-traffic-lights`, `GET /stock-indicator-mappings`, `GET /stock-trade-signal-performance`, `GET /stock-trade-signals`, `GET /summary`

## `/api/cash-conversion-signals` (3)
`POST /rebuild`, `GET /stock/{stock_code}`, `GET /top`

## `/api/cherry-screener` (4)
`GET /`, `GET /analysis/{stock_code}`, `GET /detail/{stock_code}`, `POST /precompute`

## `/api/commands` (13)
`POST /analyze/{stock_name}`, `POST /batch-float-shares`, `GET /batch-float-shares/status`, `GET /collect-status/{stock_code}`, `POST /daily-disclosure-check`, `POST /monthly-bulk-update`, `GET /monthly-bulk-update/status`, `POST /rebuild-shareholder-profiles`, `POST /refresh-annual/{stock_code}`, `POST /refresh-cashflow/{stock_code}`, `POST /screener-refresh`, `GET /watchlist`, `DELETE /watchlist/{stock_code}`

## `/api/company-intelligence` (4)
`GET /cherry-family/status`, `GET /company/{stock_code}`, `GET /compare`, `GET /portfolio/compare`

## `/api/consensus` (5)
`POST /backfill`, `GET /recent`, `POST /refresh`, `GET /{stock_code}`, `GET /{stock_code}/summary`

## `/api/contract-advance-signals` (3)
`POST /rebuild`, `GET /stock/{stock_code}`, `GET /top`

## `/api/dart-contracts` (6)
`POST /backfill`, `GET /list`, `POST /refresh`, `GET /signals`, `GET /stats`, `GET /{rcept_no}`

## `/api/dart-excel` (5)
`POST /build`, `GET /ch-data/{code}`, `GET /download/{job_id}`, `GET /status/{job_id}`, `GET /verify/{stock_code}`

## `/api/dashboard` (22)
`GET /cashflow/{stock_code}`, `GET /chart/{stock_code}`, `GET /collection-runs`, `GET /corporate-actions/{stock_code}`, `GET /data-lineage`, `GET /data-lineage/{metric_key}`, `GET /data-quality/{stock_code}`, `GET /disclosures/{stock_code}`, `GET /estimated-performance/{stock_code}`, `GET /explainable-signals/{stock_code}`, `GET /financial-table/{stock_code}`, `GET /fundamentals/{stock_code}`, `GET /hypothesis-reports`, `GET /live-signal-outcomes/{stock_code}`, `GET /macro`, `GET /market-info/{stock_code}`, `GET /market-regime/latest`, `GET /screening/logic`, `GET /screening/triple`, `GET /sectors`, `GET /shareholder-profiles`, `GET /stats`

## `/api/detailed-analysis` (8)
`POST /bootstrap`, `GET /files/{file_id}/download`, `POST /normalize`, `GET /posts`, `POST /posts`, `GET /posts/{post_id}`, `GET /report-files/{report_file_id}/download`, `GET /telegram/channel-link`

## `/api/earnings-signals` (4)
`GET /latest`, `POST /scan`, `GET /stats`, `GET /stock/{code}`

## `/api/employment-v2` (8)
`GET /annual-top`, `GET /annual-trend`, `GET /chart`, `GET /insurance`, `GET /insurance/chart`, `GET /quality`, `GET /trend`, `GET /yearly`

## `/api/etf-check` (8)
`GET /etf-list/{stock_code}`, `GET /search`, `GET /source-control`, `GET /status`, `GET /tab1`, `GET /tab2`, `GET /tab3`, `GET /tab4`

## `/api/extra-signals` (2)
`GET /chart/{code}`, `GET /extra-signals/{code}`

## `/api/global-foreign-flow` (3)
`GET /history`, `GET /summary`, `GET /us-inbound-history`

## `/api/global-macro` (17)
`GET /categories`, `POST /collect`, `GET /collection-log`, `GET /commodities`, `GET /commodities/correlations`, `GET /dashboard`, `GET /events`, `POST /events`, `GET /events/reactions`, `GET /events/surprises`, `GET /insights`, `GET /insights/lead-lag`, `GET /insights/regime`, `GET /latest`, `GET /roadmap`, `GET /stats`, `GET /timeseries/{code}`

## `/api/ingest` (4)
`POST /fundamentals`, `POST /investor-trends`, `POST /market-price`, `POST /sectors`

## `/api/insider` (4)
`GET /ceo-changes`, `GET /holdings/{code}`, `GET /major/{code}`, `GET /recent-significant`

## `/api/inventory-sales-signals` (3)
`POST /rebuild`, `GET /stock/{stock_code}`, `GET /top`

## `/api/investment-decisions` (2)
`POST /tasks/{stock_code}`, `GET /tasks/{stock_code}/latest`

## `/api/kis-trading` (14)
`GET /account/summary`, `GET /cash-ledger`, `GET /lifecycle/reconciliation`, `GET /live/data-preflight`, `POST /live/order`, `GET /orders/lifecycle`, `GET /orders/{order_id}`, `POST /paper/order`, `GET /paper/orders`, `GET /paper/pnl`, `GET /paper/positions`, `GET /risk-gates/check`, `GET /risk-gates/recent`, `GET /status`

## `/api/kiwoom` (18)
`POST /conditions/events`, `POST /conditions/snapshot`, `GET /conditions/status`, `GET /data-status`, `POST /foreign-flow`, `POST /investor/collect`, `GET /investor/status`, `POST /rankings/large-trades`, `GET /rankings/large-trades/confirmation`, `GET /rankings/large-trades/latest`, `POST /realtime/snapshot`, `GET /status`, `POST /stock-info/update`, `POST /stock-universe/bulk-update`, `GET /summary/{code}`, `POST /token/refresh`, `POST /us/realtime/snapshot`, `GET /us/realtime/{ticker}`

## `/api/market-indicators` (18)
`GET /attention-confirmation`, `POST /attention-events`, `POST /attention-events/materialize-channel`, `GET /available-dates`, `GET /index-investor`, `GET /investor-top`, `GET /investor-trend`, `GET /market-cash`, `GET /market-summary`, `GET /rank-events`, `GET /short-dates`, `GET /short-foreign`, `GET /short-history`, `GET /short-monthly`, `GET /short-rank`, `GET /turnover-breakout-live`, `GET /turnover-breakout-signals`, `GET /turnover-top`

## `/api/market-radar` (15)
`GET /all`, `GET /export-csv`, `POST /import-csv`, `POST /init-semiconductor`, `POST /refresh-cache`, `GET /sector-us-overnight-signals`, `GET /sector/{sector}/detail`, `GET /semiconductor/financial-detail`, `GET /semiconductor/financial-history`, `GET /semiconductor/financials`, `GET /semiconductor/megatrend`, `GET /semiconductor/summary`, `GET /semiconductor/us-overnight-signal`, `GET /semiconductor/valuestream`, `POST /semiconductor/valuestream/refresh`

## `/api/market-regime` (1)
`GET /`

## `/api/namu` (1)
`GET /execution/{stock_code}`

## `/api/notices` (3)
`GET /recent`, `GET /stock/{code}`, `GET /today`

## `/api/order-contracts` (7)
`GET /backlog/{stock_code}`, `POST /collect/today`, `POST /collect/{stock_code}`, `GET /screener/surge`, `GET /stock/{stock_code}`, `DELETE /{contract_id}`, `PATCH /{contract_id}/verify`

## `/api/peer-compare` (1)
`GET /compare`

## `/api/portfolio` (11)
`GET /`, `GET /export/excel`, `POST /import/excel`, `POST /kakao-parse`, `POST /recalculate-avg`, `POST /sync-kis`, `POST /transaction`, `GET /transactions`, `PUT /{stock_code}`, `DELETE /{stock_code}`, `PATCH /{stock_code}/bought-at`

## `/api/quant-major-indicators` (6)
`GET /catalog`, `GET /cross-context/{indicator_key}`, `GET /hs-sector-context/{sector_key}`, `GET /series/{indicator_key}`, `GET /stock-context/{stock_code}`, `GET /summary`

## `/api/realtime` (2)
`GET /macro`, `GET /prices`

## `/api/reports` (9)
`GET /download/{report_id}`, `POST /extract/{report_id}`, `GET /extracts/{stock_code}`, `POST /generate/{stock_code}`, `GET /latest/{stock_code}`, `GET /ready`, `GET /sector/{sector}`, `GET /sectors`, `GET /stock/{stock_code}`

## `/api/search` (1)
`GET /`

## `/api/sector-define` (7)
`POST /init`, `POST /parse`, `POST /post`, `GET /post/{post_id}`, `DELETE /post/{post_id}`, `GET /posts`, `GET /special-filter`

## `/api/sector-rotation` (8)
`GET /dashboard-summary`, `GET /flow-signal-validation`, `GET /history/{sector_key}`, `GET /leadership`, `POST /refresh-cache`, `GET /rotation-map`, `GET /scores`, `GET /top-picks/{sector_key}`

## `/api/signals` (24)
`GET /combo-candidates`, `GET /combo-v2`, `GET /config`, `POST /config`, `PUT /config/{config_id}`, `DELETE /config/{config_id}`, `GET /consensus-revisions`, `GET /fin-screener`, `GET /high-profit-candidates`, `GET /kiwoom-conditions`, `POST /manual/{config_id}`, `GET /market`, `GET /market-regime`, `POST /market-regime/briefing`, `GET /market-regime/qa`, `GET /meta`, `GET /overheat-risk`, `GET /stock/{stock_code}`, `GET /trend-candidates`, `GET /trigger-ranking`, `GET /v10-earnings-explosion`, `GET /v11-turnaround`, `GET /v12-sector-megatrend`, `GET /value-candidates`

## `/api/source-intelligence` (1)
`GET /opinions`

## `/api/stock-analysis-rs` (8)
`GET /dashboard-data`, `GET /dashboard-rows`, `GET /high52-data`, `GET /high52-rows`, `POST /precompute`, `GET /theme-composition`, `POST /theme-composition/capture`, `GET /theme-composition/tags`

## `/api/strategy-data-lab` (1)
`GET /overview`

## `/api/telegram` (7)
`GET /channels`, `POST /channels`, `DELETE /channels/{channel_id}`, `POST /collect`, `GET /mentions/daily`, `GET /mentions/monthly`, `GET /mentions/weekly`

## `/api/tenbagger` (46)
`GET /action-signals`, `POST /ai-analysis-batch`, `GET /ai-analysis-list`, `GET /ai-analysis/{stock_code}`, `GET /bq-composite`, `GET /bq-sector`, `GET /collection-coverage`, `GET /custom-filter`, `GET /daily-alerts`, `GET /data-status`, `GET /empirical-scoreboard`, `GET /fp-fn-analysis`, `GET /historical-causes`, `GET /historical-scoreboard-v2`, `GET /historical-signal-discovery`, `GET /historical-tenbagger-audit`, `GET /historical-tenbaggers`, `GET /history`, `GET /liquidity-risk-scan`, `GET /quant-context/{stock_code}`, `GET /rd-patent/{stock_code}`, `GET /recovery-candidates`, `GET /results`, `POST /run`, `GET /run-history`, `GET /score-performance`, `GET /screener-v2`, `GET /screener-v3`, `GET /sector-ai-leaders`, `GET /segments/{stock_code}`, `POST /sell-check`, `GET /status`, `GET /stock-extra/{stock_code}`, `GET /stock-insight/{stock_code}`, `GET /stock-quality-signals/{stock_code}`, `GET /treasury-buyback-top`, `GET /treasury-buyback/{stock_code}`, `GET /triple-trigger-analysis`, `GET /triple-winners-by-year`, `GET /turnaround-filter`, `GET /turnaround-watch`, `GET /turnaround-watch/detail/{stock_code}`, `POST /turnaround-watch/precompute`, `GET /undervalued-filter`, `GET /valuation-history/{stock_code}`, `GET /valuation-summary`

## `/api/trend` (28)
`POST /ai-combo/execute`, `GET /ai-holdings`, `POST /buy`, `POST /cm/execute`, `GET /cm/recommendations`, `POST /combo/{combo_key}/execute`, `GET /combo/{combo_key}/status`, `POST /gc/execute`, `GET /gc/recommendations`, `GET /holdings`, `PATCH /holdings/{holding_id}/buy-price`, `GET /ledger/{strategy}`, `GET /performance`, `POST /rec/execute`, `GET /rec/recommendations`, `POST /sell`, `GET /strategy-center/top-five`, `GET /summary`, `GET /trades`, `DELETE /trades/all`, `POST /turnover/auto/start`, `GET /turnover/auto/status`, `POST /turnover/auto/stop`, `POST /turnover/execute`, `GET /turnover/recommendations`, `POST /update`, `POST /v18/execute`, `GET /v18/recommendations`

## `/api/us` (19)
`GET /biotech/analysis`, `GET /biotech/pipeline/{ticker}`, `POST /biotech/pipeline/{ticker}/refresh`, `GET /biotech/profile/{ticker}`, `POST /biotech/refresh-pending`, `GET /biotech/refresh-status`, `GET /indices`, `GET /integrity/latest`, `POST /integrity/run`, `GET /long-term-picks`, `GET /new-opportunities`, `GET /screener`, `GET /screener/presets`, `GET /sectors`, `GET /stocks/chart/{ticker}`, `GET /stocks/detail/{ticker}`, `GET /stocks/disclosures/{ticker}`, `GET /stocks/list`, `POST /stocks/refresh/{ticker}`

## `/api/us-13f` (2)
`GET /buffett-cash`, `GET /summary`

## `/api/us-virtual` (8)
`GET /candidates`, `GET /combined-summary`, `POST /execute-candidates`, `POST /order`, `GET /orders`, `GET /positions`, `POST /run-daily-rebalance`, `GET /status`
