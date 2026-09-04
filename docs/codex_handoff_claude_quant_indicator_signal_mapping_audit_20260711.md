# Codex → Claude Handoff: Quant Indicator Signal Mapping Audit

Date: 2026-07-11 KST
Workspace: `/Applications/stock_dashboard`

## User Intent

The user wants the quant-indicator system to generate actionable buy signals:

1. Detect when a collected indicator improves sharply.
2. Identify related stocks.
3. Avoid false signals when the indicator is only a tiny or indirect part of the company.
4. Prefer showing the indicator's share of revenue/profit when available.

This means the system must be built around:

`indicator anomaly -> exposure-weighted related stocks -> Telegram/front-end signal`

not merely:

`Naver Cafe text mention -> related stock`

## Recent Codex Changes

Files touched for this indicator-signal workflow:

- `scripts/ops/sync_cafe_stock_indicator_mappings.py`
  - Builds `cafe_stock_indicator_mappings`.
  - Uses Naver Cafe body proximity matching: stock name and product/indicator terms within 120 chars.
  - Uses coarse sector allow-lists per indicator.
  - Adds exposure fields:
    - `revenue_exposure_pct`
    - `profit_exposure_pct`
    - `cost_exposure_pct`
    - `exposure_basis`
    - `importance_level`

- `scripts/ops/quant_indicator_signal_engine.py`
  - Reads `quant_major_indicator_series`.
  - Calculates `MoM`, `YoY`, `z_score`.
  - Inserts anomaly events into `quant_indicator_signal_events`.
  - Ranks related stocks using `importance_level`, mention count, and exposure fields.
  - Sends Telegram only when invoked with `--send-telegram`.

- `routes/cafe_signals.py`
  - Adds API:
    - `/api/cafe-signals/stock-indicator-mappings`
    - `/api/cafe-signals/quant-indicator-signals`

- `frontend/src/views/CafeSignalsView.jsx`
  - Adds "지표 급변 매수 후보" table.
  - Shows stock exposure text in signal badges.

- `frontend/src/App.jsx`
  - Adds individual-stock "카페 기반 연관 지표" exposure and importance display.

- `scheduler.py`
  - Adds daily `퀀트지표트리거` loop at 07:40.
  - Also runs signal engine after cafe signal refresh.

## Current DB State

Audited with:

```bash
sqlite3 stock.db "
select 'stock_indicator_mappings', count(*) from cafe_stock_indicator_mappings;
select importance_level, count(*) from cafe_stock_indicator_mappings group by importance_level order by count(*) desc;
select 'signal_events', count(*) from quant_indicator_signal_events;
select signal_type, count(*) from quant_indicator_signal_events group by signal_type;
"
```

Observed:

- `cafe_stock_indicator_mappings`: 236 rows
- `importance_level`:
  - `unknown`: 161
  - `unknown_core_candidate`: 55
  - `unknown_cost_sensitive`: 20
- `quant_indicator_signal_events`: 15 rows
  - `spike_up`: 14
  - `spike_down`: 1

Exposure coverage:

```sql
select
  count(*) total,
  sum(revenue_exposure_pct is not null) rev_known,
  sum(profit_exposure_pct is not null) profit_known,
  sum(cost_exposure_pct is not null) cost_known
from cafe_stock_indicator_mappings;
```

Observed:

- total: 236
- revenue exposure known: 0
- profit exposure known: 0
- cost exposure known: 226

This is the most important QA finding.

## Critical Findings

### 1. Revenue/profit exposure is currently not actually known

The added fields exist, but current `segment_revenue` data does not provide product-level segment matching for the mapped indicators.

Example: `014830 유니드`

```sql
select stock_code, stock_name, indicator_name, mention_count,
       revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
       importance_level, exposure_basis
from cafe_stock_indicator_mappings
where stock_code='014830'
order by mention_count desc;
```

Current result summary:

- `칼륨 화학제품 수출입`
  - revenue/profit exposure: null
  - cost exposure: 82.1
  - importance: `unknown_core_candidate`
  - basis: `원가구조: 2025Q4 매출원가/원재료 비중`
- `비료 수출입`
  - revenue/profit exposure: null
  - cost exposure: 82.1
  - importance: `unknown_core_candidate`
- `석유화학 합성수지 수출입`
  - revenue/profit exposure: null
  - cost exposure: 82.1
  - importance: `unknown_cost_sensitive`
- `정유 석유제품 수출입`
  - revenue/profit exposure: null
  - cost exposure: 82.1
  - importance: `unknown`

Interpretation:

Cost exposure is not the same as product revenue exposure. It should be treated as a sensitivity hint only, not as a direct buy-signal weight.

### 2. Mapping still has likely false positives

Even after proximity and sector filters, many rows are only weakly related because generic words like `스프레드`, `PC`, `요소`, `항공`, `수주잔고` can match broad discussion text.

Examples needing Claude review:

- `석유화학 합성수지 수출입`
  - `KG스틸`, `대한제강`, `금강철강`, `세아제강`, `포스코스틸리온`, etc.
  - These may be false positives caused by generic `스프레드` language.

- `항공/방산 수출입`
  - `현대건설`, `GS건설`, `대우건설`, `두산밥캣`, etc.
  - Likely caused by generic `항공` mentions rather than direct defense/aerospace revenue exposure.

- `비료 수출입`
  - Some non-fertilizer 소재 names matched via `요소`.
  - Needs business/product validation.

- `조선 상선 수출입`
  - Strong direct matches exist: `HD현대`, `HD현대중공업`, `HD한국조선해양`, `삼성중공업`, `한화오션`.
  - But peripheral industrial names still appear due to `수주잔고`/`조선` nearby.

### 3. Current signal events should be treated as "candidate alerts", not production buy signals

Top current events:

- `조선 상선 수출단가` 2026-05 spike_up
  - MoM +66.0%, YoY +100.0%, z +3.23
  - related: HD현대, HD현대중공업, HD한국조선해양...

- `화학 비료 수출단가` 2026-05 spike_up
  - MoM +45.4%, YoY +77.1%, z +5.05
  - related: 유니드...

- `화학 칼륨 수입액` 2026-05 spike_up
  - MoM -11.3%, YoY +463.1%, z +4.17
  - related: 유니드...

- `정유 석유제품 수입단가` 2026-05 spike_up
  - MoM +10.9%, YoY +78.1%, z +5.79
  - related: 롯데케미칼...

Note: Some events are based on import amount/unit price. Directionality may differ by company:

- Product selling price/export unit price up: usually positive for exporters/producers.
- Raw material import cost up: can be negative for consumers and positive for producers.
- Import amount up: ambiguous without knowing whether company produces or consumes the item.

Claude should add direction semantics per indicator/company role.

## What Claude Should Re-Check

### A. Build a real exposure model

Current exposure model is too weak. Claude should create or improve a table such as:

`stock_indicator_exposure`

Suggested columns:

- `stock_code`
- `indicator_key`
- `exposure_type`: `revenue`, `profit`, `cost`, `raw_material`, `selling_price`, `macro_proxy`
- `exposure_pct`
- `direction`: `positive_when_up`, `negative_when_up`, `mixed`, `unknown`
- `source`: `segment_revenue`, `dart_business_text`, `cafe_post`, `manual_override`, `analyst_report`
- `basis_text`
- `confidence`
- `review_status`

Do not rely on `cost_exposure_pct` alone as a buy-signal strength.

### B. Add manual product mapping for high-value indicators

Start with the eight current product indicator families:

- `public:23:7` 조선 상선 수출입
- `public:23:14` 정유 석유제품 수출입
- `public:23:17` 석유화학 합성수지 수출입
- `public:23:28` 비료 수출입
- `public:23:36` 전력기기 수출입
- `public:23:37` 항공/방산 수출입
- `public:23:41` 칼륨 화학제품 수출입
- `public:23:42` 에폭시/NB라텍스 화학소재 수출입

For each, define:

- direct producers
- consumers
- ambiguous/peripheral companies
- expected signal direction when export price/import price/export amount/import amount rises

### C. Tighten weak keyword matches

Problematic generic terms:

- `스프레드`
- `PC`
- `항공`
- `수주잔고`
- `요소`

Recommendation:

- Require a product-specific co-term for generic terms.
  - Example: `스프레드` alone should not map to petrochemical unless near `나프타`, `에틸렌`, `PP`, `ABS`, `합성수지`.
  - `항공` alone should not map to defense/aerospace unless near `방산`, `전투기`, `항공기`, `KAI`, `엔진`, etc.
  - `수주잔고` alone should not map to shipbuilding unless near `조선`, `선박`, `LNG선`, etc.

### D. Validate current events before Telegram production

Before enabling strong Telegram buy alerts, Claude should run:

```bash
venv/bin/python scripts/ops/sync_cafe_stock_indicator_mappings.py
venv/bin/python scripts/ops/quant_indicator_signal_engine.py --limit-events 50
sqlite3 stock.db "
select indicator_name, series_name, period, signal_type, signal_strength, related_stocks
from quant_indicator_signal_events
order by signal_strength desc
limit 20;
"
```

Then manually inspect top 20 related stocks.

### E. Check DB lock/server restart issue

During Codex verification, server restart was blocked by SQLite locks from other processes:

- `collectors/dart_dilution_collector.py --days 1825`
- `collectors/dart_material_purchase_collector` sampling process
- multiple uvicorn leftovers

Do not kill long-running collectors unless user approves. Instead:

```bash
lsof /Applications/stock_dashboard/stock.db /Applications/stock_dashboard/stock.db-wal /Applications/stock_dashboard/stock.db-shm
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Wait for collectors to finish before restarting the backend.

## Suggested Acceptance Criteria

The system should be considered production-ready only when:

1. Each Telegram buy candidate shows:
   - indicator change
   - stock relationship
   - exposure type
   - exposure percentage or explicit `미공시`
   - signal direction

2. At least the top 50 mappings are reviewed as:
   - `confirmed`
   - `candidate`
   - `rejected`

3. `unknown` mappings are excluded from high-priority buy Telegram messages.

4. Import price signals do not automatically create buy alerts unless the company is a producer/seller or pass-through beneficiary.

5. The front-end clearly distinguishes:
   - `매출 비중 확인`
   - `이익 비중 확인`
   - `원가 민감`
   - `비중 미공시`
   - `검토 필요`

## Quick Current Verdict

The pipeline is structurally in place, but the mapping quality is not yet good enough for fully automatic buy decisions.

Safe wording for current UI/Telegram:

- "지표 급변 후보"
- "관련 종목 후보"
- "비중 미공시/검토 필요"

Avoid wording:

- "매수 확정"
- "핵심 수혜주 확정"
- "매출 비중 확인" when `revenue_exposure_pct` is null

## Codex Follow-up Update (2026-07-11 10:30 KST)

- Ready theme indicators increased from 38 to 42.
- Added official-series bridges for:
  - `cafe:11:2650` IPTV annual subscribers
  - `cafe:11:2805` automobile country/company monthly totals
  - `cafe:11:3475` KOSIS construction orders/output
  - `cafe:34:7690` department store/duty-free/online/tourism series
- Traffic-light API now returns 223 component series across all 42 ready themes.
- Stock mappings were rebuilt with sector constraints and phrase-level terms:
  - 709 mappings
  - 354 stocks
  - 41 indicators with at least one evidence-backed stock relationship
- DART segment HTML was recollected for all 340 mapped stocks for 2024/2025:
  - 2,986 segment rows saved
  - collector now persists `revenue_pct`
- Exposure calculation now rejects negative revenue, account-name rows, implausible
  segment/consolidated coverage, and invalid published percentage totals.
- Confirmed revenue exposure remains sparse: 4 mappings (0.6%). Do not infer a
  percentage for the remaining mappings.
- Corrected observed false positives:
  - airline companies no longer map from generic `항공` to defense/aerospace
  - `비타이어부문` no longer matches the tire indicator
  - short Latin terms such as `PC` require token boundaries
- Still missing dedicated series collectors:
  - advertising/media replacement source (KOBACO KAI ended after the 2026-01 outlook)
  - airline passenger/fare series (KAC API currently returns 403 for the configured key)

### Additional follow-up completed

- Korean housing starts are now exact official monthly data:
  - MOLIT form `5386`
  - nationwide monthly starts backfilled from 2021-06
  - provisional 2025/2026 values retain an explicit quality label
  - missing consecutive months no longer produce a false period-over-period rate
- Airline theme now has Brent/WTI monthly fuel-cost proxies. They are marked as
  `proxy_monthly`, interpreted as higher-is-bad, and are not treated as passenger demand.
- Game theme now collects daily Steam current-player snapshots for PUBG, Black
  Desert, Lies of P, and Stellar Blade. These are marked as PC-platform partial
  indicators and directly mapped only to their verified listed developer/publisher.
- Telegram eligibility was tightened:
  - traffic light must be green
  - at least one related stock must have `high` or `medium` importance
  - revenue or profit exposure percentage must be populated
  - cost spikes and unknown-exposure candidates remain front-end review items only

### Exposure and mapping precision update

- Confirmed revenue/profit exposure mappings increased from 4 to 15.
- Company-specific segment proxies were added only for explicit combinations,
  including UNID/potassium chemicals, Rainbow Robotics/industrial robots,
  Namhae Chemical/fertilizer, Hyundai Rotem/defense, Taekwang/plant fittings,
  DN Automotive/auto parts, Asia Cement/housing-construction demand, and
  HanaTour/tourism consumption.
- Segment validation now falls back to the latest older valid disclosure when
  the newest year's HTML extraction is unusable.
- Whitespace-normalized totals and accounting rows such as `합 계`, `전사 매출`,
  `영업손익`, and domestic/export subtotal rows are excluded from denominators.
- Weak standalone keywords (`PC`, `PP`, `스프레드`, `수주잔고`, `요소`, `소다`)
  were removed or replaced by product-specific phrases. Mapping count fell from
  719 to 667 without losing confirmed exposure mappings.
- Mapping state is now explicit:
  - `confirmed_exposure`: 15
  - `confirmed_relationship`: 4
  - `candidate_context`: 648
- Front-end labels candidate-context relationships as `문맥후보·비중미공시`
  rather than presenting them as confirmed beneficiaries.

### Sector and stock signal integration

- Added `/api/cafe-signals/sector-traffic-lights` for all 11 mapped sectors.
- Multiple component series are first collapsed to one indicator score so themes
  with many sub-series do not dominate a sector merely by row count.
- Sector output includes positive/negative/caution indicator counts, strongest
  indicator drivers, and linked stocks ordered by confirmed exposure,
  confirmed relationship, then context candidate.
- The Cafe Signals front end now shows the sector traffic-light table above the
  detailed indicator table.
- The domestic individual-stock page now receives a live aggregate traffic
  status for each mapped indicator and shows its light, label, source series,
  period, exposure state, and evidence.
- Confirmed stock relationships are ordered before context-only candidates on
  the individual-stock page.

### Candidate expansion and trade-signal audit

- Cafe indicator coverage is now 49 mappings across 12 sectors. New partial
  bridges include SK Hynix/memory, telecom subscribers/ARPU, and pork/hog data.
- Stock mappings now cover 350 stocks and 44 indicators with 15
  `confirmed_exposure`, 8 `confirmed_relationship`, and 660 `candidate_context`
  relationships. Context candidates remain excluded from trade scoring.
- Added `/api/cafe-signals/stock-trade-signals` and a front-end candidate table.
  The score uses fresh indicator direction, relationship strength, disclosed
  revenue/profit exposure, source quality, and sector alignment.
- Correlated indicators from the same sector are decayed (`1.0`, `0.45`, then
  `0.25`) so repeated component series do not inflate a stock's score.
- Stale daily/monthly/yearly series are visible as reference data but excluded
  from both sector and stock trade scoring.
- Added daily persistence in `quant_stock_trade_signal_snapshots`, invoked after
  the scheduled indicator refresh. Initial 2026-07-11 snapshot: 5 buy candidates,
  2 sell/risk candidates, and 2 watch candidates.
- These labels are research candidates, not an execution strategy. Require
  forward-return validation, transaction-cost/slippage assumptions, and
  walk-forward threshold calibration before any automatic trade instruction.
