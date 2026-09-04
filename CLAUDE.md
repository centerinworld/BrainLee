# 주식 대시보드 — Claude 필수 참조 문서

---

## ⚠️ CLAUDE 필수 행동 규칙 (모든 세션에서 자동 적용)

> **이 섹션은 Claude가 반드시 따라야 할 행동 규칙입니다. 예외 없이 적용됩니다.**

### 서버 재시작 (필수 — 코드 수정 후 반드시 이 방법으로만)

> **직접 uvicorn kill 절대 금지.** launchd `KeepAlive:true` 때문에 kill 후 launchd가 자동 재시작 → 이어서 수동으로 uvicorn 시작하면 두 프로세스가 공존함.
>
> **2026-08-23/24 재발 확인**: `launchctl kickstart -k`만으로도 구 프로세스가 무거운 연산 중이면 SIGTERM을 못 받아 고아 프로세스로 남는 사고가 2회 발생(포트 없이 CPU만 계속 점유, 전체 서버 체감속도 저하의 원인이었음). **반드시 아래 안전 스크립트를 사용할 것** — 재시작 전후 PID를 비교해 고아를 자동 탐지·정리한다.

```bash
# ✅ 권장(2026-08-25 신규) — 고아 프로세스 자동 탐지·정리까지 포함
bash /Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/safe_restart_backend.sh

# ✅ 기존 방법(고아 프로세스 재발 가능 — 재시작 후 반드시 `ps aux`+`lsof -i :8000`으로 직접 확인할 것)
launchctl kickstart -k "gui/$(id -u)/com.stock-dashboard.local"

# ✅ 완전 정지 후 시작
/Volumes/Realtek_NVME/stock_dashboard/runtime/stop.sh
/Volumes/Realtek_NVME/stock_dashboard/runtime/start.sh

# ❌ 금지: kill <pid> 후 nohup uvicorn ... &  → 서버 2개 생김
```

### 세션 시작 시
- **이 파일을 먼저 읽는다.** 파일 내용으로 프로젝트 구조를 파악하고, 불필요한 파일 열람을 최소화한다.
- 작업 전 필요한 정보가 이 파일에 있으면 파일을 새로 열지 않는다.
- **ETF/ETN 정보는 장중(09:00~15:30) 수집 금지.** ETF/ETN 수집은 장 마감 후 배치(야간/새벽 백필 포함)로만 수행한다.

### 작업 완료 시 (필수 — 자동으로 수행)
다음 중 하나라도 해당하면 **이 파일(CLAUDE.md)을 반드시 업데이트**한다:
- [ ] 새 파일 생성 (routes/, collectors/ 등)
- [ ] API 엔드포인트 추가/변경/삭제
- [ ] DB 테이블/컬럼 추가 또는 스키마 변경
- [ ] 프론트엔드 컴포넌트 추가/이동 (줄번호 포함)
- [ ] 스케줄러 잡 추가/변경
- [ ] 버그 수정 (재발 방지를 위해 "알려진 이슈" 섹션에 기록)
- [ ] 환경변수/설정 추가
- [ ] 기존 동작 방식 변경 (단위, 포맷, 로직)

**업데이트 위치**: 해당 섹션을 직접 수정 + 섹션 11(변경 이력)에 날짜와 함께 **정말로 1~3문장만** 기록.

> 🪙 **토큰 최적화 규칙 (2026-09-03 재도입, 2번째 재발)**: 이 CLAUDE.md는 `/Volumes/Realtek_NVME/stock_dashboard/runtime`에서 세션이 시작될 때마다
> **전체가 자동으로 컨텍스트에 로드**됩니다(858KB → 2026-09-03 기준 142KB로 축소). 섹션 11이 2026-07-17 archive 분리 후 6주 만에
> 다시 774KB로 재폭증했던 원인은 항목마다 수백~수천자짜리 상세 리포트를 그대로 붙여넣었기 때문입니다. 재발 방지:
> 1. 변경이력 항목은 **한 줄~세 줄 요약만**. 근거 SQL/CSV/실험 결과/장문 분석은 `docs/` 또는 `scratch/`에 날짜 붙인 별도 파일로 만들고 CLAUDE.md에는 파일 경로만 링크.
> 2. 섹션 11은 최근 20~25개 항목만 유지. 초과분은 오래된 것부터 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래로 이동.
> 3. CLAUDE.md에 새 표/코드블록/장문 설명을 추가하기 전, 그 정보가 정말 "매 세션 필요"한지 먼저 판단할 것 — 1회성 조사·검증 결과는 CLAUDE.md가 아니라 `docs/`에 남긴다.

### Codex/Claude 병렬 작업 시 충돌 방지 규칙

> **Codex와 Claude가 동시에 이 프로젝트를 수정함. 충돌 방지 필수.**

- 작업 시작 전 `git pull --rebase` 로 최신 코드 동기화
- **같은 파일을 동시에 편집하지 않는다** — 작업 파일을 CLAUDE.md 상단에 미리 명시
- 코드 수정 후: 반드시 `launchctl kickstart -k` 로 서버 재시작 (위 규칙 참조)
- Python 코드 수정 = 서버 재시작 없이는 변경 미반영 (uvicorn은 모듈 캐시)
- **routes/*.py, ETF_check/routes_etf.py 수정 시**: 서버 재시작 필수
- DB 스키마 변경 시: 다른 AI가 같은 테이블을 수정 중인지 반드시 확인

### 토큰 절약 규칙
- 파일 전체를 읽기 전에 이 문서에서 줄 번호를 확인하고 해당 범위만 읽는다.
- DB 스키마 확인 → 섹션 2 참조 (init_db.py 열지 않음)
- API 엔드포인트 확인 → 섹션 3 참조 (routes/*.py 열지 않음)
- 컴포넌트 위치 확인 → 섹션 6 참조 (App.jsx 전체 스캔 안 함)

### 재무/현금흐름 무결성 작업 선행 규칙 (필수)

> **Claude는 재무/현금흐름 관련 수정 전에 반드시 아래 파일을 먼저 읽고 작업한다.**

1. `/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/claude_handoff_external_reverify_20260516_1510.md`
2. `/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/claude_handoff_capex_depr_material_20260516.md`
3. `/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/company_profile_22_25_top500_1to1_20260516.csv`
4. `/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/company_profile_22_25_top500_1to1_20260516.json`

작업 지침:
- 재무 원천 적재는 DART/KRX 기반으로 수행하고, 웹 파싱값은 검증 레이어로만 사용.
- `standard_key` 중심 매핑(예: capex, depreciation)과 기업별 오버라이드 매핑을 분리.
- 값 저장 전 1:1 대조 실패(`ok_* = False`) 항목은 자동확정 금지, review 큐로 분리.
- 2022~2025 데이터 잠금은 조건 충족 시에만 허용(검증 통과율 근거 필수).

### FnGuide급 신뢰도 목표 운영 규칙 (상시 고정, 2026-05-31 추가)

> 목표: 사용자 화면 재무제표/현금흐름표/CapEx/감가상각비를 **FnGuide 수준 신뢰도**로 유지.
> 원칙: **DART 원천 보존 + IFRS 표준화 + FnGuide 표시변환 검증**.

필수 원칙:
- DART 원천(raw)은 절대 덮어쓰지 않는다. (원천 보존)
- AI는 자동확정 주체가 아니라 **후보 매핑 제안자**로만 사용한다.
- DB 반영은 반드시 규칙엔진 검증 통과 시에만 수행한다. (등식/범위/전후분기 일관성)
- `account_nm` 키워드 단독 매핑으로 자동반영 금지. `account_id + sj_nm + fs_div` 우선.
- CFS/OFS 혼합 저장/혼합 역산 금지. report_type 단위로 분리 검증.
- Q4 단일분기 파생은 규칙 고정:
  - 누적형이면 `Q4 = Annual - Q1 - Q2 - Q3`
  - 소스 불일치(혼합)면 Q4 강제 산출 금지(NULL 유지 + review 큐)
- OPEN은 오류 확정이 아닌 “검증 미완”이므로 자동 임의보정 금지.
- STRUCTURAL은 데이터 결함이 아니라 기준차 가능성이 있으므로 “변환검증 후 재분류” 우선.

실행 금지 조건:
- DART API `status=020`(일일한도 초과) 상태에서 대량 재수집/일괄보정 실행 금지.
- 샘플 검증(최소 10종목) 없이 전종목 대량 UPDATE 금지.

필수 검증 로그:
- 모든 자동보정은 `financial_fix_log` 또는 `cashflow_fix_log`에 사유/전후값/run_id 기록.
- run_id 없는 UPDATE 금지.

---

## 1. 프로젝트 구조

```
/Volumes/Realtek_NVME/stock_dashboard/runtime/
├── main.py              # FastAPI 앱 + 라우터 등록 (1792줄)
├── scheduler.py         # 수집 스케줄러 CollectionScheduler (400줄)
├── signal_engine.py     # 시그널 계산 엔진 (2507줄)
├── peak_monitor.py      # 가상매매 모니터 + Telegram 알림 (663줄)
├── config.py            # 환경변수 로드
├── database.py          # SQLAlchemy SessionLocal + get_db
├── models.py            # SQLAlchemy ORM 모델
├── kis_client.py        # KIS API 토큰 관리
│
├── routes/              # FastAPI 라우터 (main.py 38~56줄에 등록)
│   ├── signals.py       → /api/signals/*
│   ├── trend.py         → /api/trend/*  (가상매매)
│   ├── portfolio.py     → /api/portfolio/*
│   ├── buy_candidates.py→ /api/buy-candidates/*
│   ├── dart_contracts.py→ /api/dart-contracts/*
│   ├── dart_excel.py    → /api/dart-excel/*  ★신규(2026-06-13)
│   ├── market_indicators.py → /api/market-indicators/*  ★2026-04 신규
│   ├── kiwoom.py       → /api/kiwoom/*  ★2026-05 신규
│   ├── reports.py       → /api/reports/*
│   ├── telegram.py      → /api/telegram/*
│   ├── backtest.py      → /api/backtest/*
│   ├── strategy_data_lab.py → /api/strategy-data-lab/*  ★2026-08 신규
│   ├── us_13f.py        → /api/us-13f/*  ★2026-08-29 신규
│   └── ingest.py        → /api/ingest/*
│
├── collectors/          # 외부 데이터 수집기
│   ├── kis_collector.py # KIS API (주가·수급·실시간)
│   ├── krx_collector.py # KRX / K-mydata (현재 접근 불가)
│   ├── public_data.py   # 공공데이터포털
│   ├── dart_collector.py# DART 공시
│   ├── yahoo_collector.py # Yahoo Finance (해외지수)
│   ├── imf_weo_collector.py # IMF WEO 성장률 전망치
│   ├── global_financial_conditions_collector.py # FRED 기반 글로벌 금융여건/정책금리 확장
│   ├── dram_spot_collector.py # TrendForce/DRAMeXchange 실제 D램 현물가
│   ├── market_quant_bridge_collector.py # 기존 주요 퀀트 지표를 글로벌 인텔리전스로 브릿지
│   ├── kiwoom_collector.py # 키움 REST 연결/인증 상태 점검
│   └── base.py          # BaseCollector (rate limit, async)
│
├── scripts/
│   └── build_strategy_research_dataset.py # 전략 연구용 월말 스냅샷/3배 라벨/ML 점수 생성
│   └── research_strategy_barbell_combo.py # 전략센터 상위 5전략 병합 재배치 탐색 + combined run 저장 ★신규(2026-07-29)
├── research_outputs/
│   └── strategy_research_summary.json     # 전략 연구 요약 JSON (전략 센터 패널 소스)
│
├── .claude/
│   ├── settings.json    # hooks 설정 (UserPromptSubmit, Stop)
│   └── hooks/
│       ├── session_start.sh  # 매 프롬프트: CLAUDE.md 지시사항 주입
│       └── session_stop.sh   # 세션 종료: 로그 기록
│
└── frontend/src/App.jsx # 단일 파일 React SPA (~7600줄)
```

---

## 2. DB 스키마 (stock.db)

| 테이블 | 행수 | 핵심 컬럼 | 용도 |
|--------|------|-----------|------|
| `price_history` | 516만 | stock_code, date, open/high/low/close, volume, inst_net_buy, frn_net_buy, ind_net_buy, **inst_net_buy_amt**, **frn_net_buy_amt**, **ind_net_buy_amt** | 일별 OHLCV + 투자자수급 |
| `stock_universe` | 6693 | stock_code, stock_name, market, sector_large, shares_issued, market_cap, per, pbr, roe, roa | 전 종목 마스터 |
| `financial_data` | 9.2만 | stock_code, year, quarter, revenue, operating_profit, net_income, total_assets, total_equity, eps, bps, is_annual | 재무제표 |
| `peak_holding` | 31 | stock_code, stock_name, buy_price, current_price, quantity, entry_date, is_active, strategy, profit_pct | 가상매매 보유 |
| `peak_trade` | 31 | stock_name, tx_type(buy/sell), price, quantity, profit, strategy | 가상매매 거래내역 |
| `portfolio` | 29 | stock_code, quantity, avg_price, bought_at | 실제 포트폴리오 |
| `portfolio_snapshot` | 305 | snapshot_date, stock_code, close_price, quantity, eval_amount, profit_pct | 일별 스냅샷 |
| `portfolio_tx` | 37 | stock_code, tx_type, quantity, price, tx_date | 거래내역 |
| `signal_config` | 26 | scope, name, label, logic_type, params, is_active | 시그널 설정 |
| `signal_result` | 936 | config_id, stock_code, signal(green/yellow/red), score | 시그널 결과 |
| `stock_meta` | 1097 | stock_code, float_shares, shares_outstanding | 유동주식수 |
| `short_sell_daily` | 7만 | bas_dt, stock_code, short_qty, borrow_bal_qty, borrow_bal_pct | 대차잔고/공매도 |
| `buy_candidates` | 28 | stock_code, target_price, memo | 매수 후보 |
| `watchlist` | 61 | stock_code | 관심종목 |
| `telegram_channels` | 9 | channel_id, channel_name | 텔레그램 채널 |
| `report_files` | 2691 | stock_code, sector, report_date, file_path | 섹터 보고서 |
| `backtest_runs` | 2 | run_id, status, total_return_pct, trades_json | 백테스트 결과 |
| `strategy_feature_snapshot` | 189,561 | snapshot_date, stock_code, close_price, market_cap_억, per, pbr, ret_20d/60d/120d, dist_high_252, vol_ratio_20d, supply_20d_억, label_2x/3x_6m/12m, **forward_max_ret_24m/36m, label_3x/5x/10x_24m, label_5x/10x_36m**, heuristic_score, model_score_6m/12m | 전략 연구용 월말 피처 스냅샷 + forward 라벨 + 휴리스틱/ML 점수. `scripts/build_strategy_research_dataset.py`가 생성/전량 재구축. ★신규(2026-07-05) / **2026-08-08 24·36개월 라벨 7컬럼 추가** — 실제 10배 종목은 중위 609일(1.7년) 소요라 기존 12개월 창으로는 86.9%가 관측 불가였음. 라벨 유효구간: 24m는 스냅샷 ≤2024-08-07(126,879행), 36m는 ≤2023-08-08(97,188행). 기준율 label_10x_24m 1.50% / label_10x_36m 2.37%. **모든 라벨은 비율 스케일(1.0=+100%) — 3배=2.0, 5배=4.0, 10배=9.0** |
| `investor_trading_daily` | ~수집중 | bas_dt, stock_code, indv_net, inst_net, frgn_net | ✅ 키움 ka10059로 수집 중 (DART recollect 완료 후) |
| `foreign_holding_daily` | 0 | bas_dt, stock_code, frgn_hold_pct | ⚠️ 미수집 |
| `kiwoom_investor_daily` | ~수집중 | stock_code, dt, ind_invsr, frgnr_invsr, orgn + 세부기관분류 | ✅ 키움 ka10059 (개인/외국인/기관 + 10개 기관세부) |
| `financial_source_snapshot` | ~25만 | stock_code, year, is_annual, report_type, data_source('fnguide'), revenue, op_profit, net_income, verification_status | FnGuide 원본 스냅샷 (마스터) |
| `financial_anomalies` | 3181 | stock_code, anomaly_type, severity, is_resolved | 재무 이상 분류 (unit_error/cfs_ofs/large_discrepancy 등) |
| `stock_collection_config` | 248 | stock_code, config_key, config_value | 종목별 수집 특성 (report_type/unit_verified 등) |
| `company_mapping_profile` | 17,258 | stock_code('*'=공통), standard_key, source_system, account_id(XBRL), account_label_raw, confidence_score, valid_from/to, verified_by, is_active | 기업별 DART 계정 매핑 프로파일. 2026-07-21 확장: DART fnlttSinglAcntAll 실계정id를 financial_data와 대조검증해 revenue/operating_profit/net_income/total_assets/total_equity 5개 키 기준 2,282종목 확정(is_active=1) + 검증대기 다수(is_active=0, review 큐) |
| `dart_raw_accounts` | 112 | stock_code, year, quarter, report_code, fs_div, account_id, account_nm, thstrm_amount, rcept_no | DART 원문 계정 저장. anchor_mismatch 4종목 2022년 CFS 원문(DART account_id는 API 미제공) ★신규(2026-05-16) |
| `data_quality_issues` | 79 | stock_code, year, quarter, table_name, field_name, reason_code(SOURCE_MISSING/ANCHOR_MISMATCH 등), severity, is_resolved | Null Sentinel — ANCHOR_MISMATCH 4건(HIGH)+SOURCE_MISSING 75건(금융업 DART미제공) ★신규(2026-05-16) |
| `data_lock` | 6840 | stock_code, year, table_name, is_locked, lock_basis('dart_verified'), lock_hash(md5) | Freeze 정책 — 2019~2022 DART 검증 완료 전량 잠금. financial 1,282건+cashflow 5,558건 ★신규(2026-05-16) |
| `fin_quarterly_validation_flags` | ~829 | stock_code, year, quarter, field, check_type(ANNUAL_CONSISTENCY/DART_FG_CROSS), dart_value, fnguide_value, annual_value, quarterly_sum, ratio, status(CONFIRMED/AMBIGUOUS/STRUCTURAL/OPEN), ai_verdict, notes | 분기 재무 3중 검증 (DART+FnGuide+AI). ★신규(2026-05-23) |
| `tenbagger_results` | ~1800 | stock_code, stock_name, total_score, axis1~6, reasons, run_time | 텐버거 발굴 엔진 결과 (6축 스코어링). ★신규(2026-06) |
| `tenbagger_daily_alerts` | 증가중 | alert_date, stock_code, stock_name, total_score, reasons, is_new(신규=1), best_reason(왜 최고 종목인지 분석), created_at. UNIQUE(alert_date, stock_code) | 텐버거 아침 알림 이력. tenbagger_morning_alert.py 실행시 저장. ★신규(2026-06-13) |
| `tenbagger_ai_analysis` | ~50 | stock_code, analysis_text, created_at | DeepSeek 심층 분석 캐시(24h). ★신규(2026-06) |
| `dart_backlog_quarterly` | ~5000 | stock_code, fiscal_year, fiscal_quarter, report_type, backlog_amount_krw, source_rcept_no | 수주잔고 분기별 추이. order_backlog와 병렬 저장. ★신규(2026-06) |
| `dart_cost_quarterly` | ~수집중 | stock_code, fiscal_year, fiscal_quarter, cogs, sg_a, gross_margin_pct | 원가 구조 분기별. cost_structure와 병렬 저장. ★신규(2026-06) |
| `dart_tenbagger_triggers_quarterly` | ~수집중 | stock_code, fiscal_year, fiscal_quarter, metric_name, metric_value, yoy_pct, trigger_level | 텐버거 트리거 지표 (BACKLOG_SURGE 등). ★신규(2026-06) |
| `kiwoom_credit_balance` | ~2198종목 | stock_code, dt, credit_balance_qty, credit_balance_amt, credit_ratio, new_credit_qty, repay_credit_qty | Kiwoom ka10013 신용거래잔고(일별). tenbagger_engine credit_trend 우선 소스. 5년치 수집 진행중(max_pages=13). ★신규(2026-06) |
| `kiwoom_foreign_flow` | ~2198종목 | stock_code, date, weight(외국인지분율%), frg_hold_qty | Kiwoom ka10008 외국인 지분율 추이. ★신규(2026-06) |
| `investor_flow_quarterly` | ~90,150행 | stock_code, year, quarter, ind_net_sum, frgnr_net_sum, orgn_net_sum, trading_days, source | 투자자 분기별 순매수 집계(price_history 기반, 2018~2026, 3962종목). ★신규(2026-06-11) |
| `foreign_flow_quarterly` | ~87,474행 | stock_code, year, quarter, frn_net_buy_amt_sum, frn_net_buy_qty_sum, trading_days, weight_end, source | 외국인 분기별 순매수 집계(price_history 기반, 2019~2026, 3890종목). ★신규(2026-06-11) |
| `dart_insider_holdings` | ~1797종목 | stock_code, corp_code, officer_name, trade_type(취득/처분), shares, report_date, is_ceo | DART 임원 매매 공시. tenbagger_engine insider_signal 소스. ★신규(2026-06) |
| `order_backlog` | ~5000 | stock_code, year, quarter, backlog_amount, backlog_normalized(백만원), data_source | 수주잔고 (건설/조선 등). ★신규(2026-06) |
| `cost_structure` | ~수집중 | stock_code, year, quarter, cogs_pct, sg_a_pct, gross_margin_pct | 원가율 구조. ★신규(2026-06) |
| `cost_breakdown` | ~수집중 | stock_code, year, quarter, material_cost, labor_cost, overhead | 원가 세부 분해. ★신규(2026-06) |
| `dilution_events` | 17,722행 / 1,448종목 | stock_code, rcept_no, event_type(CB/BW/EB/RIGHTS/BONUS), issue_amount, dilution_pct, conversion_price, put_option_date | 희석 이벤트. 건수 기반 리스크는 사용 가능하나, issue_amount는 12,015행(67.80%) / 1,238종목으로 **금액 기반 리스크는 부분완료**. DART 과거 문서 cp949/euc-kr 디코딩 보강 후 2020년 74.6%, 2021년 58.6%, 2022년 74.0%까지 복구. 목표 커버리지 80%+. `dart_disclosure_parse` 잔여는 대부분 만기전취득/자기전환사채/종속회사/권리락/가격확정 등 금액 필드로 해석하면 안 되는 레거시 행. ★신규(2026-06, 2026-07-21 보강) |
| `triple_pattern_daily` | ~수집중 | stock_code, dt, triple_score, tenbagger_score, supply_signal | BigQuery 3배주 복합 신호 일별. ★신규(2026-06) |
| `valuation_history` | 63,451 | stock_code, year, quarter, period_end, close_price, eps, bps, per, pbr, market_cap_억 | 분기별 역사적 PBR/PER 밸류에이션 이력. financial_data+price_history 기반 계산. ★신규(2026-06-11) |
| `segment_revenue` | 18,365행 / 2,561종목 | stock_code, corp_code, year, quarter, segment_name, revenue(백만원), operating_profit(백만원), assets(백만원), report_type | DART 사업부문/세그먼트 매출. **⚠️ "95% 커버" 표기 주의**: 2,561종목(95.10%)은 `segment_name`이 `연결전체`(총계 1행)만 있어도 카운트된 값 — 실제 제품/사업부/지역별 세부 breakdown이 있는 종목은 **319종목(12.23%)뿐**(2026-07-29 재감사, `scripts/audit_segment_dilution_coverage.py`). 제품노출도 기반 신호에는 반드시 breakdown coverage(12.23%) 기준으로 판단할 것 — 95%는 "데이터 존재 여부"이지 "세그먼트 분해 가능 여부"가 아님. ★신규(2026-06-11, 2026-07-21 현황 정정, 2026-07-29 breakdown 커버리지 분리) |
| `program_trading_daily` | 0 | dt, market(KOSPI/KOSDAQ), prog_net_buy_amt(억원), arb_net_buy_amt(차익,억원), non_arb_net_buy_amt(비차익,억원), source | KRX 프로그램매매 일별. KRX MDCSTAT05301(KOSPI)/05401(KOSDAQ) Playwright 수집. 스케줄러 18:20 KRX프로그램매매 잡 등록완료, KRX 로그인 정상화 시 자동수집. ★신규(2026-06-13) |
| `dart_rd_patent_signals` | 2,209 | stock_code, rcept_no, rcept_dt, report_nm, signal_type(patent/tech_transfer/rd_contract/license), amount_krw, notes. UNIQUE(rcept_no, signal_type) | DART 특허/기술이전/R&D/라이선스 공시. dart_disclosures 파싱. 텐버거 엔진 연동(1년내 기술이전+3점/특허+2점/R&D+1점). ★신규(2026-06-15) |
| `analyst_pdf_extracts` | 증가중 | report_id(UNIQUE), stock_code, target_price, opinion, fwd_eps_1y, fwd_rev_1y, fwd_per, extracted_at, raw_text | PDF 보고서에서 gpt-4o-mini로 추출한 컨센서스 지표 캐시. routes/reports.py 자동 생성. ★신규(2026-07-05) |
| `earnings_signals` | ~344 | stock_code, signal_type(turnaround/revenue_surge/profit_accel), ttm_eps, qoq_streak | TTM 실적 신호 자동 탐지. ★신규(2026-06-01) |
| `quant_major_indicator_catalog` | ~80 | indicator_key(epic:N:M), epic_indicator_name, status, source_system, frequency, base_unit | EPIC 대체지표 카탈로그. ★신규(2026-06) |
| `quant_major_indicator_series` | ~5000+ | indicator_key, period_str(YYYY-MM), value, unit, source | 퀀트 주요지표 시계열. ★신규(2026-06) |
| `margin_balance_daily` | ~758종목 | stock_code, dt, credit_balance, collected_at | 신용잔고 일별 (kiwoom_credit_balance fallback용). ★신규(2026-06) |
| `live_orders` | 0(신규) | order_id, parent_order_id, mode, strategy_key, stock_code, side, order_type, qty, limit_price, status, filled_qty, avg_fill_price, decision_reason | 실전형 주문 생애주기 마스터. `kis_paper_orders`(구)와 병행 기록. ★신규(2026-07-23, Codex A1 제안) |
| `live_order_events` | 0(신규) | order_id, event_ts, event_type(SUBMITTED/FILLED/...), qty_delta, price, detail | 주문별 이벤트 로그. ★신규(2026-07-23) |
| `live_fills` | 0(신규) | order_id, fill_ts, fill_qty, fill_price, cumulative_qty | 개별 체결 기록(현재는 단일체결만, 부분체결 확장 여지). ★신규(2026-07-23) |
| `live_cash_ledger` | 0(신규) | ts, mode, delta_krw, balance_after, reason, ref_order_id | 페이퍼 현금원장(seed 1억원 기본, `KIS_PAPER_INITIAL_CASH`로 조정). ★신규(2026-07-23) |
| `risk_gate_decisions` | 0(신규) | ts, stock_code, side, strategy_key, decision, reasons, gate_snapshot, order_id | A2 리스크게이트 판정 이력(전량 기록, 차단/통과 모두). ★신규(2026-07-23, Codex A2 제안) |

### 중요 단위 규칙
```
stock_universe.market_cap → 억원 단위 ★ (LX홀딩스=5,927억원 실증, 2026-05-30 두산=257,968억원 확인)
  SQL 필터: 500억+=500, 1000억+=1000, 5조+=50000 (모두 억원 그대로)
  ⚠️ 과거 오류: "백만원 단위(50000=500억원)"로 잘못 기록된 변경이력 존재 → 무시

inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt → 백만원 단위 (÷100 = 억원)
inst_net_buy, frn_net_buy → 수량(주)
예외: ^KS11, ^KQ11 지수 레코드의 inst_net_buy → 억원 직접 저장
```

### DB 연결 패턴
```python
# routes/ 파일 표준 (sqlite3 직접)
import sqlite3 as _sl
DB_PATH = "stock.db"
conn = _sl.connect(DB_PATH)
conn.row_factory = _sl.Row   # dict처럼 r["col_name"] 접근

# SQLAlchemy (ORM 필요 시)
from database import get_db
db: Session = Depends(get_db)
```

### 지수/ETF 제외 필터 (price_history 조회 시 항상 적용)
```sql
WHERE stock_code NOT LIKE '%^%'   -- ^KS11, ^KQ11, ^IXIC 등
  AND stock_code NOT LIKE 'GC%'   -- 금 선물
  AND stock_code NOT LIKE 'CL%'   -- 원유 선물
  AND stock_code NOT LIKE '%-F'   -- 선물
  AND stock_code NOT LIKE '%=%'   -- 통화 (USDKRW=X 등)
  AND stock_code NOT LIKE 'NQ%'   -- 나스닥 선물
  AND stock_code NOT LIKE 'ES%'   -- S&P 선물
```

---

## 3. API 엔드포인트 전체 목록

### main.py 직접 정의 엔드포인트
```
GET  /api/realtime/prices                  # KIS 실시간 주가 캐시
GET  /api/realtime/macro                   # 거시지표 실시간
GET  /api/dashboard/market-info/{code}     # 종목 시장정보 (sector, mktcap, 순위)
GET  /api/dashboard/chart/{code}           # 주가 차트 데이터
GET  /api/dashboard/sectors                # 섹터 목록
GET  /api/dashboard/screening/triple       # 3단계 스크리닝
GET  /api/dashboard/screening/logic        # 로직 스크리닝
GET  /api/dashboard/financial-table/{code} # 재무제표 테이블
GET  /api/dashboard/cashflow/{code}        # 현금흐름
GET  /api/dashboard/disclosures/{code}     # 공시 목록
GET  /api/dashboard/fundamentals/{code}    # PER/PBR (Naver 스크래핑 포함, 비동기 캐시)
GET  /api/dashboard/macro                  # 거시지표 캐시
GET  /api/dashboard/stats                  # 시스템 통계
GET  /api/search                           # 종목 검색
POST /api/reports/generate/{code}          # AI 리포트 생성
GET  /api/reports/latest/{code}            # 최신 AI 리포트
GET  /api/reports/ready                    # 리포트 준비 상태
POST /api/commands/refresh-cashflow/{code}
POST /api/commands/refresh-annual/{code}
POST /api/commands/monthly-bulk-update
POST /api/commands/daily-disclosure-check
POST /api/commands/screener-refresh
POST /api/commands/analyze/{stock_name}
GET  /api/commands/collect-status/{code}
GET  /api/commands/watchlist
DELETE /api/commands/watchlist/{code}
POST /api/commands/batch-float-shares
GET  /api/commands/batch-float-shares/status
GET  /api/kiwoom/status
POST /api/kiwoom/token/refresh
POST /api/kiwoom/realtime/snapshot
POST /api/kiwoom/foreign-flow
POST /api/kiwoom/investor/collect   # ka10059: 종목별 투자자 일별 수급
POST /api/kiwoom/stock-info/update  # ka10001: 종목 PER/PBR/ROE/유동주식수
POST /api/kiwoom/stock-universe/bulk-update  # ka10001 배치: 전종목 갱신
GET  /api/kiwoom/investor/status    # kiwoom_investor_daily 적재 현황
GET  /api/kiwoom/data-status
```

### routes/signals.py → /api/signals
```
GET  /market             # 시장 시그널 (캐시키: 'market', TTL 1800초)
GET  /market-regime      # 5단계 시장국면 점수 + 강제하향 + AI 브리핑
POST /market-regime/briefing # 시장국면 AI 브리핑 수동 생성
GET  /stock/{code}       # 종목 시그널 (캐시키: 'stock_{code}')
GET  /trend-candidates   # 추세 후보 (캐시키: 'trend')
GET  /value-candidates   # 가치 후보 (캐시키: 'value')
GET  /combo-candidates   # AI 콤보 후보 (캐시키: 'combo_candidates')
GET  /fin-screener       # 재무 스크리너
GET  /trigger-ranking    # 트리거 20 (캐시키: 'trigger')
GET  /kiwoom-conditions  # 키움조건식 5가지 퀀트 전략 (params: strategy=all|value_blue|supply_momentum|growth_garp|high52_break|contrarian, 캐시키: 'kiwoom_cond_{strategy}', TTL 1h)
GET  /meta               # 스크리너 메타정보
GET  /config             # 시그널 설정
PUT  /config/{id}        # 설정 수정
POST /config             # 설정 추가
DELETE /config/{id}      # 설정 삭제
POST /manual/{id}        # 수동 실행
GET  /overheat-risk      # 60일수익률+100%초과 과열종목 (캐시 30분) ★2026-07 신규(문서 누락 소급기재)
GET  /consensus-revisions # 컨센서스 목표주가 상향조정 종목 (params: days=60, limit=60) ★신규(2026-08-23)
```

### routes/trend.py → /api/trend (가상매매)
```
GET    /holdings              # 보유종목 (현재가: price_history 최신 close)
POST   /buy                   # 매수
POST   /sell                  # 매도
POST   /update                # 현재가/수익률 업데이트
GET    /trades                # 거래내역
GET    /summary               # 요약 (승률, 수익)
DELETE /trades/all            # 전체 삭제
GET    /gc/recommendations    # V12 골든크로스 가상매매 추천 (strategy='v_gc') ★신규(2026-07-08)
POST   /gc/execute            # V12 골든크로스 즉시 실행 ★신규(2026-07-08)
GET    /rec/recommendations   # V-RECOVERY 낙폭반등 가상매매 추천 (strategy='v_recovery')
POST   /rec/execute           # V-RECOVERY 즉시 실행
GET    /combo/{key}/status    # 병합조합 가상매매 현황(구성전략/매수후보/매도후보) ★신규(2026-07-23)
POST   /combo/{key}/execute   # 병합조합 가상매매 즉시 실행(매도→매수) ★신규(2026-07-23)
```
- **⛔ 2026-07-23 삭제(저효율 확인)**: `ai-combo/execute`(strategy='ai_combo', 승률23%·누적-20.7M)/`v18/recommendations`·`v18/execute`(strategy='gpt_v18', 승률27%·누적-8.6M)/`turnover/*`(strategy='turnover_100m'·'turnover_auto_100m', 1건뿐 또는 0건) — 엔드포인트 코드는 남아있으나 스케줄러 루프(`_loop_v14_10m`) 비활성화, 프론트 STRATEGIES 버튼 제거. 오픈포지션 1건(gpt_v18, 안국약품)은 +7.22%에 청산 후 종료. 대체: 아래 병합조합 4종.
- **V12 골든크로스**: strategy='v_gc', MA20↑MA60(15일내)+거래량1.2x+RS6M>-20%+시총2000억+, Trail-25%/손절-12%/300일, 1억원 예산/종목당1000만원/최대8종목. 20분 주기 장중 자동실행. avg6=+47.6%, 6/6기간 양수.
- **병합조합 가상매매(2026-07-23 신규)**: 전략센터 "전략 조합" 탭에서 `persist_merged_run`으로 등록된 4개 검증조합(605.05%/539.18%/510.12%/473.87%, `/api/backtest/combinations/list` 참조)을 각각 독립 1억원 가상계좌로 실행. `combo_605`/`combo_539`/`combo_510`/`combo_474` 4개 strategy 키(peak_holding/peak_trade 재사용). 구성 컴포넌트(v4/v2/sector_focus/v10/recovery/earnings_conviction/moonshot_turnaround)를 등록 당시와 동일 파라미터로 2020-03-01~최신거래일까지 매번 재실행(`routes/trend.py COMBO_COMPONENTS`)해 "최신거래일 당일" 발생분만 오늘의 매수/매도 시그널로 추출, 콤보 우선순위(등록된 priority)로 랭킹 후 고정티켓(1,000만원)/20%현금보유 방식(v_gc/v_recovery와 동일 패턴)으로 체결. 컴포넌트 1개당 1~20초 소요(총 7개 최초 1회 약 60~90초, 프로세스 내 캐시로 하루 1회만 계산·여러 콤보가 공유). **매도 판단 이중화**: (a) 원천 컴포넌트가 오늘 자신의 매도신호를 냈으면 반영 (b) 컴포넌트별 stop_loss를 안전망으로 상시 병행 평가(콤보 자신의 진입가/일자가 컴포넌트 연속시뮬레이션과 다를 수 있어 (a)만으로는 누락 위험). ⚠️ **재발방지 버그(발견·수정 완료)**: 백테스트 엔진이 end_date(=오늘)에 아직 보유 중인 포지션을 회계상 강제청산할 때 붙이는 사유(`기간종료`/`기간종료(시세부재 전액손실)`/`종료청산`/`final`/`end`, 엔진마다 문자열 다름)를 걸러내지 않으면 "오늘 보유 중인 모든 포지션"이 매번 매도신호로 오탐됨 — `_COMBO_PERIOD_END_MARKERS`로 필터링. 매일 18:35(평일, KRX일별수집 이후) `_loop_combo_daily` 자동 실행.
- 메타시뮬레이터 기반: backtest_runs DB의 AUTO 런 목록 블렌딩 → 상위 종목 추출

### routes/portfolio.py → /api/portfolio
```
GET    /                 # 포트폴리오 + 현재가 + 수익
POST   /sync-kis         # KIS 체결 동기화
PATCH  /{code}/bought-at # 매수일 수정
GET    /transactions     # 거래내역
POST   /transaction      # 거래 추가
POST   /kakao-parse      # 카카오뱅크 문자 파싱
PUT    /{code}           # 종목 수정
DELETE /{code}           # 종목 삭제
GET    /export/excel     # 엑셀 내보내기
POST   /import/excel     # 엑셀 가져오기
```

### routes/buy_candidates.py → /api/buy-candidates
```
GET    /                 # 매수 후보 + 현재가
POST   /                 # 추가
PATCH  /{code}           # 메모/목표가 수정
DELETE /{code}           # 삭제
GET    /short-sell/{code}# 대차잔고 조회
```

### routes/market_indicators.py → /api/market-indicators ★신규(2026-04)
```
GET  /investor-top       # 투자자별 순매수 상위 (params: date, limit=20)
GET  /turnover-top       # 회전율 상위 (params: date, market=ALL, limit=20)
GET  /investor-trend     # 수급 추이 차트 (params: market=kospi, days=60)
GET  /market-summary     # KOSPI/KOSDAQ 요약 + 오늘 수급
GET  /index-investor     # 지수 투자자 일별 (params: days=20)
GET  /available-dates    # 수급 데이터 있는 영업일 목록
```

### routes/reports.py → /api/reports
```
GET  /stock/{code}       # 종목 리포트 목록
GET  /download/{id}      # 파일 다운로드
GET  /sectors            # 섹터 목록
GET  /sector/{sector}    # 섹터별 리포트
POST /extract/{id}       # PDF → gpt-4o-mini 컨센서스 추출 (캐싱) ★신규(2026-07-05)
GET  /extracts/{code}    # 종목별 추출 결과 목록 ★신규(2026-07-05)
```

### routes/telegram.py → /api/telegram
```
GET    /channels         # 채널 목록
POST   /channels         # 채널 추가
DELETE /channels/{id}    # 채널 삭제
POST   /collect          # 즉시 수집
GET    /mentions/daily   # 일별 언급
GET    /mentions/weekly  # 주별 언급
GET    /mentions/monthly # 월별 언급
```

### routes/backtest.py → /api/backtest
```
POST   /run              # 백테스트 실행
GET    /list             # 결과 목록
GET    /{run_id}         # 결과 상세
DELETE /{run_id}         # 삭제
GET    /monthly-picks    # 월별 추천 종목 백테스트 리포트
GET    /strategies       # 전략 카탈로그
GET    /strategy-research/summary  # 전략 연구 요약 + 현재 장세 기준 전략 우선순위 ★신규(2026-07-05)
POST   /strategy-research/rebuild  # 전략 연구 데이터셋/요약 JSON 재생성 ★신규(2026-07-05)
```

### routes/strategy_data_lab.py → /api/strategy-data-lab ★신규(2026-08-29)
```
GET    /overview  # 전략센터 데이터 역할(진입/확인/촉매/위험제거)·신선도·다중확인 연구후보
```
- `V-CATALYST`/`V-REVISION`/`V-QUALITY-ROUTE`는 검증 전 연구 후보로만 반환한다. 기존 실행 검증에서 품질·수주 지표를 매수랭킹에 가산하면 악화됐으므로, 성과 매트릭스·자동매매에는 포함하지 않는다.

### routes/us_13f.py → /api/us-13f ★신규(2026-08-29)
```
GET    /summary          # SEC 13F-HR 최신/직전 보고서 비교 (force=true: 12시간 캐시 무시)
GET    /buffett-cash     # Berkshire 10-Q/10-K 현금·단기투자자산 시계열
```
- 20개 13F 운용사와 Nancy Pelosi의 House PTR을 출처·기준일을 분리해 표시한다. PTR은 보유포트폴리오가 아닌 거래 신고이며, 13F는 분기 지연 롱 포지션 공시로 옵션·공매도·공시 후 거래를 포함하지 않는다. 자동주문 또는 단독 매수/매도 근거로 사용하지 않는다.

### routes/kis_trading.py → /api/kis-trading (2026-07-23 최초 문서화 — main.py에는 등록돼 있었으나 CLAUDE.md 누락)
```
GET  /status                    # 거래모드(PAPER/LIVE)·리스크한도 조회
GET  /account/summary           # KIS 실계좌 스냅샷(보유/잔고/당일체결) — LIVE 조회 전용, 매매 아님
POST /paper/order                # 페이퍼 주문 실행 (A2 리스크게이트 통과 필요)
GET  /paper/orders               # 페이퍼 주문 이력(구 스키마, 하위호환 유지)
GET  /paper/positions            # 페이퍼 보유 포지션 + 평가손익
GET  /paper/pnl                  # 페이퍼 당일/누적 실현손익
POST /live/order                 # 항상 403 차단(실전주문 미승인 상태, 명시적 승인 절차 전까지 유지)
GET  /risk-gates/check           # ★신규(2026-07-23) 주문 없이 리스크게이트만 사전점검
GET  /risk-gates/recent          # ★신규(2026-07-23) 최근 게이트 판정 이력(BUY_ALLOWED/BLOCKED_* 등)
GET  /orders/lifecycle           # ★신규(2026-07-23) live_orders 기반 주문 목록(신규 스키마)
GET  /orders/{order_id}          # ★신규(2026-07-23) 주문+이벤트+체결 상세
GET  /cash-ledger                # ★신규(2026-07-23) 페이퍼 현금원장(잔고 이력)
```
- **PAPER 주문 흐름(2026-07-23 이후, 2026-07-23(2차) 3개 게이트 추가)**: `place_paper_order()`가 먼저 `evaluate_risk_gates()`로 **9개 게이트**(데이터신선도/갭리스크/유동성/희석위험/수급역풍/장세위험/**신용잔고급증/변동성기반사이징/섹터집중한도**)를 평가 → `BLOCKED_STALE_DATA`/`BLOCKED_RISK`(희석·수급역풍·장세위험·신용급증·섹터집중 위반)는 400 거부, `WAIT_CONFIRM`(갭+7%↑)은 `override_wait_confirm=true` 재요청 전까지 409 거부, `SIZE_REDUCED`(유동성 3%↑ 또는 종목당 리스크한도 초과)는 두 한도 중 더 보수적인 쪽까지 수량 자동 축소 후 진행. 통과 시 **기존 `kis_paper_orders/positions/realized`(하위호환) + 신규 `live_orders/live_order_events/live_fills/live_cash_ledger`(생애주기 상세) 양쪽에 병행 기록**.
- 매도(side=sell)는 데이터신선도만 확인하고 나머지 게이트는 통과시킴 — 리스크 축소 행위인 매도를 막으면 오히려 위험하다는 원칙.
- **변동성기반 사이징 가정**: 종목당 손실한도=계좌자본×1.2%, 가정손절폭=-20%(이 세션에서 가장 흔히 쓰인 기본값) — 전략별 실제 손절폭(-8%~-35%)과 다를 수 있어 "최소한 이 이상은 넘지 말자"는 보수적 하한으로만 기능. 섹터집중한도(35%)는 `kis_paper_positions`+`stock_universe.sector_large` 기준 계산, 한도 초과 시 부분축소가 아니라 전체 차단(단순화, 정직하게 명시).
- **신용잔고급증**: `kiwoom_credit_balance.credit_ratio` 기준 8%↑ & 20일전 대비 50%↑ 급등 시에만 경고(V-SMARTFLOW의 "신용잔고<3%가 좋은 신호" 임계와는 별개 — 여기서는 "급격한 증가" 자체를 위험신호로 봄). 데이터 45일↑ 오래되면 판단보류.

전체 검증 세부는 섹션 11 변경이력 참조.

### routes/ingest.py → /api/ingest
```
POST /fundamentals       # 재무 데이터 저장
POST /market-price       # 시장가 저장 (장중만)
POST /sectors            # 섹터 저장
POST /investor-trends    # 투자자 동향 저장
```

### routes/market_radar.py → /api/market-radar ★등록(2026-05)
```
GET  /all                         # 전체 섹터 RS 데이터
GET  /sector/{sector}/detail      # 섹터 상세 (섹터 지표 페이지)
POST /init-semiconductor          # 반도체 초기화
POST /refresh-cache               # 캐시 강제 갱신
GET  /export-csv                  # CSV 내보내기
POST /import-csv                  # CSV 가져오기
GET  /semiconductor/valuestream   # 반도체 밸류체인 (SemiconductorView)
POST /semiconductor/valuestream/refresh
GET  /semiconductor/megatrend     # 메가트렌드 탐지 스크리너 ★신규(2026-07-20)
```

### routes/sector_define.py → /api/sector-define ★등록(2026-05)
```
GET  /posts                       # Hot 섹터 포스트 목록
GET  /post/{id}                   # 포스트 상세
POST /parse                       # 포스트 파싱
```

### routes/extra_signals.py → /api/extra-signals ★신규(2026-05)
```
GET  /extra-signals/{code}  # 추가 시그널 (고용/수출/섹터트렌드/수급/ETF편입/ETF비중)
```
응답 구조: `{employment, exports, sector_trend, supply, etf_ratio, etf_inclusion}`
- sector_trend: sector_large 기준 분류 (sector_mid 아님)
- etf_inclusion/etf_ratio: etf_count=0 & etf_amount=0 인 날(수집실패)은 건너뜀

### ETF_check/routes_etf.py → /api/etf-check ★신규(2026-05)
```
GET  /tab1              # ETF 편입액 기준 (KOSPI/KOSDAQ 상위)
GET  /tab2              # ETF 편입액 증감 (1일/5일 전 대비)
GET  /tab3              # 시총대비 증감%
GET  /tab4              # 시총대비 비중%
GET  /search            # 종목명/코드 검색 (유효 날짜만 사용)
GET  /etf-list/{code}   # 종목 편입 ETF 목록 (etfcheck.co.kr 스크래핑)
```
- etf_amount=0인 날(수집실패)은 get_available_dates에서 자동 제외

### routes/stock_analysis_rs.py → /api/stock-analysis-rs ★성능개선(2026-05)
```
GET  /dashboard-data    # 요약만 반환: benchmarks, sector_rs, metadata (rs_list 없음)
GET  /dashboard-rows    # RS 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&cap_min=&market=&sector_mode=
GET  /high52-data       # 52주 메타데이터만 반환
GET  /high52-rows       # 52주 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&high_filter=
POST /precompute        # 캐시 강제 재계산 (스케줄러 18:30 호출)
```
- 초기 전송량: 2.3MB → 수십KB (rs_list/high52_list 제거)
- 캐시: scratch/stock_analysis_rs_cache.json (장중 10분, 장외 24시간 TTL)
- 동시 요청 시 double-check locking (compute는 락 밖에서 수행)

---

## 4. 스케줄러 (scheduler.py)

| 잡 | 시간 | 설명 |
|----|------|------|
| `_job_nightly_batch` | 00:10 daily | Yahoo/KIS/공공데이터 수집 |
| `_job_monthly_bulk` | 매월 1일 03:00 | stock_universe 전체 갱신 |
| `_job_disclosure_check` | 03:30 daily | DART 공시 확인 |
| `_job_intraday_prices` | 매 1분 (장중) | KIS 현재가 수집 → price_history |
| `_job_intraday_investor` | 매 5분 (장중) | KIS 수급 수집 → price_history |
| `_job_market_signal_briefing` | 07:00 daily | 5단계 시장국면 점수 계산 + OpenAI 5줄 브리핑 저장 |
| `_job_closing` | 15:40 daily | 종가 확정 + portfolio_snapshot |
| `_job_screener_precompute` | 매 30분 | 시그널 캐시 갱신 |
| `_job_krx_daily` | 18:00 daily (영업일) | KRX 승인API 전종목 OHLCV + 지수 수집 (data-dbg.krx.co.kr) |
| `_job_supply_daily` | 17:30 daily (영업일) | KIS 전종목 최근 30일 수급 누락분 보완 |
| `_job_krx_investor_playwright` | 18:10 daily (영업일) | KRX 전종목 기관/외국인 순매수 금액 수집 (Playwright, data.krx.co.kr 로그인) |
| `_job_kiwoom_health` | 매 10분 (평일 장중) | 키움 REST 인증/연결 상태 점검 |
| `_job_kiwoom_investor_daily` | 19:00 daily (영업일) | 키움 ka10059 시가총액 상위 1000종목 투자자 일별 순매수 수집 |
| `_job_kiwoom_stock_universe` | 매주 월요일 06:30 | 키움 ka10001 전종목 PER/PBR/ROE/유동주식수 갱신 |
| `_job_dart_financial_recollect` | 00:30 daily | DART finstate_all 재무제표 재수집 (ETF/ETN/상폐 제외, legacy_dart_recollect.py --resume, 최대 4시간) |
| `_job_dart_segment` | 매주 일요일 03:30 | DART fnlttSinglAcntAll IS계정 기반 사업부문별 매출 수집 (시총상위 500, scripts/collect_dart_segment_breakdown.py) ★신규(2026-06-14) |
| `_job_combo_daily` | 매일 18:35 (평일) | 전략센터 병합조합 4종(605/539/510/473%) 가상매매 실행 — 구성 컴포넌트 7개 today-signal 재계산(최초 1회 약 60~90초) 후 매도→매수 체결 ★신규(2026-07-23) |

### ETF 수집 스케줄 (crontab — ETF_check/scheduler.py)
| 시간 | 실행 모드 | 설명 |
|------|-----------|------|
| 20:30 평일 | `--once` | 메인 수집 (장 마감 후) |
| 23:30 평일 | `--retry` | 실패 종목 재수집 |
| 02:30 화~토 | `--backfill` | 전날 최종 백필 (재수집 실패 시 보완) |

### 퀀트 주요지표 자동 수집 (crontab — scripts/ops/quant_indicators_cron.py) ★신규(2026-06-13)
| 시간 | 모드 | 설명 | 소요 |
|------|------|------|------|
| 19:30 평일 | `daily` | 시장폭/대차잔고/기준금리/카지노공시 | ~37초 |
| 08:00 월요일 | `weekly` | K-Line BDI/BCI/BPI/BSI, SteelBenchmarker 중국 | ~5분 |
| 05:00 매월 12일 | `monthly` | KAMA/KOSIS/KTO/KPX/지하철/철도/관세청/ECOS 등 전체 | ~40분 |
| 05:00 매년 1월 20일 | `annual` | HIRA 의료통계, ITSTAT IPTV 가입자 | ~10분 |

**리스크 회피 설계**: FastAPI 서버와 완전히 분리된 별도 프로세스로 실행 (scheduler.py 내부 X, crontab으로만). PID 파일로 중복 실행 방지. 각 수집기 try/except 감싸 하나 실패해도 나머지 계속 진행. DB busy_timeout=300000(5분). 수동 실행: `python3 scripts/ops/quant_indicators_cron.py --mode all`

---

## 5. 공유 캐시 (_signal_cache, main.py)

```python
_signal_cache = {}
# 키 목록: 'market', 'trend', 'value', 'combo_candidates', 'combo_v2', 'trigger',
#          'stock_{code}', 'prices', 'macro'
# 값 구조: {'data': [...], 'at': time.time()}
# TTL: 시그널 1800초, 주가 300초

# routes/signals.py에서 접근 방법:
def _cache():
    import main as _m
    return _m._signal_cache
```

---

## 6. 프론트엔드 컴포넌트 (App.jsx)

**App.jsx = 17,395줄**. 다수 컴포넌트가 별도 파일로 분리됨. 2026-07-09(2차) 대규모 리팩터링으로 23개 컴포넌트가 `App` 내부 중첩 정의 → **module-level(App 함수 밖)** 로 이동됨(섹션 11 변경이력 참조). 아래 표의 "위치"는 이제 대부분 App보다 앞쪽(module-level)이다 — `const App = () => {`는 6135줄에서 시작.

### 별도 파일로 분리된 컴포넌트 (views/)
| 파일 | 탭 키 |
|------|-------|
| `frontend/src/views/MarketIndicatorsView.jsx` | market_indicators |
| `frontend/src/views/SemiconductorView.jsx` | semiconductor (MarketRadar 내부) |
| `frontend/src/views/SectorFollowupView.jsx` | sector_followup (MarketRadar 내부) / hot_sector |
| `frontend/src/views/MarketRadarView.jsx` | market_radar |
| `frontend/src/views/QuantMajorIndicatorsView.jsx` | quant_indicators ★신규(2026-06-07) |
| `frontend/src/views/StockAnalysisRsView.jsx` | stock_rs ★신규(2026-05-16) |
| `frontend/src/views/SemiconductorSectorView.jsx` | semiconductor_sector ★신규(2026-05) |
| `frontend/src/views/TenbaggerProjectView.jsx` | tenbagger_proj ★신규(2026-06) |
| `frontend/src/views/SectorRotationView.jsx` | sector_rotation ★신규(2026-06-27) |
| `frontend/src/views/RiskGateMonitorView.jsx` | risk_gate ★신규(2026-07-23) — routes/kis_trading.py 리스크게이트/주문생애주기/현금원장 모니터 (개요·사전점검·판정이력·주문생애주기·현금원장 5탭) |
| `frontend/src/EtfCheckView.jsx` | etf_check |
| `frontend/src/utils.js` | API, isKRMarketOpen, isUSMarketOpen 등 공유 유틸 |

### App.jsx 내 컴포넌트 → 탭 키 → 시작 줄번호 → 위치(2026-07-09 갱신)
| 컴포넌트 | 탭 키 | 줄번호 | 위치 |
|---------|-------|--------|------|
| `SignalBoard` | (헤더 상시 노출) | 83 | module-level |
| `BacktestView` | backtest | 984 | module-level |
| `SignalSettings` | (settings 내부) | 805 | module-level |
| `StrategyHub` | strategy_hub | 2054 | module-level (`데이터 라우팅` 탭: 2934) |
| `SettingsView` | settings | 2943 | module-level |
| `EmploymentView` | employment | 3245 | module-level |
| `TradeAnalysis2` | hs_trade2 | 3599 | module-level |
| `DartContractView` | dart_contracts | 5001 | module-level |
| `DetailedAnalysisView` | detailed_analysis | 6137 | module-level (isMobile prop) |
| `USStocksView` | us_stocks | 6550 | module-level (isMobile prop, 미국주식/스크리너/인사이트/바이오/13F 거물 동향 탭) |
| `BuyCandidateView` | buy_candidates | 7170 | module-level (changeStock/changeTab prop) |
| `Screener` | screener/전략센터 내부 | 7509 | module-level (changeStock/changeTab prop) |
| `PeakView` | trend | 9534 | module-level (changeStock/changeTab prop) |
| `PortfolioView` | portfolio | 10152 | module-level (changeStock/changeTab/collecting/fetchWatchlist prop) |
| `SectorReports` | reports | 11168 | module-level (changeStock/setActiveTab prop) |
| `TenbaggerView` | tenbagger | 11280 | module-level (changeStock/changeTab prop) |
| `MegatrendView` | megatrend | 12205 | module-level (setActiveTab prop) |
| `TelegramMentions` | telegram | 12405 | module-level (changeStock/changeTab prop) |
| `ExportHealthView` | export_health | 12663 | module-level (changeStock/changeTab prop) |
| `WatchlistView` | watchlist | 13432 | App 내부 (closure: selectedStock/watchlist 등) |
| `MacroDashboard` | macro | 13528 | App 내부 (closure: macroData — 이관 보류, 섹션11 참조) |
| `AIInsight` | insight | 16904 | App 내부 (closure: aiReport/watchlist) |
| `SystemStatus` | system | 16981 | App 내부 (closure: sysStats) |
| `DartExcelView` | dart_excel | (views/DartExcelView.jsx) | 별도 파일 |

### 렌더 스위치 탭 전체 목록 (App.jsx ~14518줄)
```
macro / market_indicators / market_radar / analysis / us_stocks / quant_indicators
stock_rs / semiconductor_sector / detailed_analysis / buy_candidates / watchlist
portfolio / screener / tenbagger / tenbagger_proj / dart_excel / dart_contracts / megatrend
trend / reports / insight / system / export_health / telegram / settings / backtest
hs_trade2 / employment / etf_check / hot_sector
```
※ `global_econ` 탭은 2026-07-02부로 `ceo-briefing-platform` 프론트엔드로 이관되어 stock_dashboard 메뉴에서 제거됨.

### 네비게이션 구조
```
NAV_ITEMS 정의: ~14348줄
렌더 스위치:    ~14517줄

순서: macro → market_indicators → quant_indicators → stock_rs → market_radar
    → analysis → us_stocks → detailed_analysis → screener
    → tenbagger → tenbagger_proj → megatrend → trend
    → reports → telegram → backtest → hs_trade2
    ── (구분선) ──
    buy_candidates → watchlist → portfolio
    ── (구분선) ──
    settings → system
```

### 전역 상태 (App 최상위)
```javascript
const [activeTab, setActiveTab]          // 현재 탭
const [stockCode, setStockCode]          // 분석 중인 종목코드
const [portfolioAuth, setPortfolioAuth]  // 포트폴리오 인증
const API = (path) => path               // vite proxy → :8000
```

---

## 7. 환경변수 (.env)

```
KIS_APP_KEY / KIS_APP_SECRET          # KIS API (주가, 수급, 체결)
KIS_ACCOUNT_NO=63109821 / KIS_ACCOUNT_PROD=01
KRX_API_KEY=115C0F...                 # KRX (현재 data.krx.co.kr 접근 불가)
PUBLIC_DATA_API_KEY=93b5be...         # 공공데이터포털 (주가 OK, 투자자API 404)
DART_API_KEY / DART_API_KEY2 / DART_API_KEY3  # DART 3-key 로테이션 필수
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE
KIWOOM_ENABLED=false
KIWOOM_APP_KEY / KIWOOM_SECRET_KEY
KIWOOM_BASE_URL=https://api.kiwoom.com
KIWOOM_WS_URL=
```

---

## 8. 핵심 코딩 패턴

### 현재가 조회 (항상 DB 사용, Yahoo/KIS 직접 호출 X)
```python
row = conn.execute(
    "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
    (stock_code,)
).fetchone()
current_price = row[0] if row else fallback_price
```

### Telegram 야간 알림 억제 (peak_monitor.py)
```python
_cur_h = datetime.now().hour
if not (8 <= _cur_h < 22):
    sent = False  # 22:00~08:00 알림 보류 (모멘텀 easy 등 오발송 방지)
```

### 시그널 stale-while-revalidate 패턴 (routes/signals.py)
```python
# TTL 내: 캐시 즉시 반환
# TTL 초과: 캐시 즉시 반환 + 백그라운드 갱신 시작 (_bg_compute)
```

### 수급 금액 단위 변환
```python
# price_history._net_buy_amt는 백만원 → 억원 표시 시 ÷100
inst_억 = round(inst_net_buy_amt / 100.0)
# ^KS11/^KQ11은 여러 row가 날짜별로 분리되므로 GROUP BY + SUM 필요
```

### 라우터 등록 위치 (main.py 38~56줄)
```python
from routes.market_indicators import router as _market_indicators_router
app.include_router(_market_indicators_router, prefix="/api/market-indicators", tags=["market-indicators"])
```

### ETF 수집실패일 필터 패턴 (etf_inclusion_daily 조회 시 항상 적용)
```python
# etf_count=0 AND etf_amount=0 → 수집 실패일. 반드시 유효 데이터만 사용
valid_rows = [r for r in rows if (r["etf_count"] or 0) > 0 or (r["etf_amount"] or 0) > 0]
# get_available_dates에서도:
WHERE e.etf_amount > 0   -- 수집실패일 제외 필수
```

### 섹터 분류 기준 (sector_large만 신뢰)
```python
# sector_mid는 분류 오류 多 (e.g. 한국항공우주 → '상업서비스') — 사용 금지
# 섹터 분류·그룹핑은 반드시 sector_large 기준으로
sector = conn.execute("SELECT sector_large FROM stock_universe WHERE stock_code=?", (code,)).fetchone()["sector_large"]
```

### HS코드 공동 매핑 시 섹터 교집합 필터 (재발방지)
```python
# HS코드 단독 매칭은 이종업종 혼입 위험. 반드시 sector_large 교집합 필터 적용
# 예: extra_signals.py _get_hs_export_info() 참조
same_sector_codes = {r["stock_code"] for r in mc.execute(
    f"SELECT stock_code FROM stock_universe WHERE stock_code IN ({placeholders}) AND sector_large=?",
    all_codes + [own_sector]
).fetchall()}
```

### stock_collection_config 패턴 (종목별 수집 특성 등록)
```python
# 수집기에서 이 테이블을 먼저 읽어 종목별 특성 반영
import sqlite3
conn = sqlite3.connect("stock.db")
cfg = {r["config_key"]: r["config_value"] for r in conn.execute(
    "SELECT config_key, config_value FROM stock_collection_config WHERE stock_code=?", (code,)
)}
# 키:
#   preferred_report_type → "CFS" | "OFS"  (교정된 연결/별도 구분)
#   unit_verified         → "true"  (단위오류 수정 완료 종목)
report_type = cfg.get("preferred_report_type", "CFS")
```

### FnGuide 무결성 동기화 (수동 실행)
```bash
python3 scripts/fnguide_integrity_sync.py            # critical 수정 (unit_error+cfs_ofs)
python3 scripts/fnguide_integrity_sync.py --all      # large_discrepancy 640건 포함 전체
python3 scripts/fnguide_integrity_sync.py --dry-run  # 변경 없이 리포트만
```

### 데이터 소스 우선순위 (엄수) — Codex 지시서 20260516 기준
```
1순위: DART API (OpenDART)    → financial_data, cash_flow_data 확정 저장 (유일한 write 경로)
2순위: KRX API / Playwright   → price_history OHLCV, 지수
3순위: KIS API                → price_history 주가·수급 (장중)
4순위: 내부 계산              → PER/PBR/ROE/ROA (DART 데이터 기반)
검증용: FnGuide, Naver        → financial_source_snapshot 전용, 본 테이블 write 절대 금지

⚠️ FnGuide/Naver 스크래핑 → financial_data/cash_flow_data write 금지 (검증/비교 전용)
⚠️ FnGuide data_source='fnguide' 행이 본 테이블에 있으면 DART 행 우선 노출
⚠️ Naver PER/PBR/EPS 직접 DB 쓰기 금지 — 내부 계산값 사용

### DART 불일치 처리 원칙 (사용자 확정 지시, 2026-05-25)

아래 원칙은 연간/분기 재무데이터(`financial_data`, `financial_source_snapshot`, `naver_financial`) 전 구간에 강제 적용한다.

1. DART는 정부 공식 원천이므로 **항상 기준축(anchor)** 으로 사용한다.
2. FnGuide/Naver 2개가 일치하더라도, DART와 불일치하면 자동확정 금지.
3. DART·FnGuide·Naver 3소스 일치: `highest_confidence`로 확정 가능.
4. DART + (FnGuide 또는 Naver) 2소스 일치: `provisional_ok`로 채택 가능하나 검증로그 필수.
5. DART 단독 불일치(외부 2소스와 모두 불일치): **명백한 이상치로 분류하고 원인분석 의무화**.
6. 원인분석 없이 화면 카드값(매출/영업이익/순이익) 자동 대체 금지.
7. 원인분석 결과는 `financial_fix_log` 또는 별도 리포트에 종목코드/연도/분기/계정/괴리율/판정근거를 남긴다.
8. 화면 표시는 `값 + source_badge + confidence_badge`를 함께 제공해 출처/신뢰도를 사용자에게 명시한다.
9. 재무 검증 배치는 연간/분기 전체를 재검사하며, 결과를 재현 가능한 스크립트 산출물(CSV/MD)로 보관한다.
10. "외부 2소스 일치"만으로 DART를 무시한 확정은 중대 오류로 간주한다.

판정 우선순위:
- `match_3way` (DART=FnGuide=Naver)
- `match_2way_with_dart` (DART=FnGuide 또는 DART=Naver)
- `dart_mismatch_all` (DART가 외부 2소스와 모두 불일치, 즉시 원인분석 큐)
⚠️ 수학적 계산(net_income/shares)은 display 전용 — DB 직접 쓰기 금지

### PER/PBR 계산 방식 (CLAUDE.md 규칙) — TTM 기준

**분기 EPS 직접 합산 = FnGuide TTM 방식 완전 재현**

> ⚠️ net_income ÷ shares_issued 방식은 금지. shares_issued에 우선주 포함으로 EPS 과소됨.
> FnGuide가 분기보고서에서 이미 "지배주주NI ÷ 보통주수"로 계산한 분기 EPS를 합산하면 동일 결과.

```
EPS_TTM = SUM(financial_data.eps, is_annual=0, 최근 4분기)   ← FnGuide 분기EPS 직접 합산
BPS_TTM = financial_data.bps (is_annual=0, 최근 1분기)       ← FnGuide 분기BPS 직접 사용
PER_TTM = 최신 종가 ÷ EPS_TTM
PBR_TTM = 최신 종가 ÷ BPS_TTM
ROE     = 최신 연도 net_income / total_equity × 100  (annual 기준)
ROA     = 최신 연도 net_income / total_assets × 100  (annual 기준)
```

**왜 TTM인가**: FnGuide는 분기 실적 공시 즉시 집계 반영. Annual EPS(is_annual=1)는 수집 시점에 따라 Q4 미반영 가능. TTM은 최근 4분기를 직접 합산하므로 항상 최신.

- 네이버 PER: 직전 사업보고서(연간 공시) EPS 기준 — TTM보다 1년 늦을 수 있음
- FnGuide PER: TTM 또는 최신 연도 — 우리 계산과 근접
- main.py get_stock_fundamentals()에서 stock_universe.per/pbr 즉시 반환

### EPS/BPS 수집 방법 (FnGuide 파이프라인)
- 정기 수집: collectors/fnguide_financial_collector.py (run() 내 fetch_fnguide_eps_bps 자동 호출, SVD_Main.asp)
- 수동 TTM 일괄 재계산:
```python
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('stock.db')
stocks = conn.execute("""
    SELECT su.stock_code, su.shares_issued, ph.close AS price
    FROM stock_universe su
    JOIN (SELECT stock_code, close FROM price_history p1
          WHERE date=(SELECT MAX(date) FROM price_history p2 WHERE p2.stock_code=p1.stock_code AND p2.close>0)
    ) ph ON ph.stock_code=su.stock_code
    WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
      AND su.shares_issued>0 AND ph.close>0
      AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
""").fetchall()
updated = 0
for code, shares, price in stocks:
    qs = conn.execute("SELECT net_income FROM financial_data WHERE stock_code=? AND is_annual=0 AND net_income IS NOT NULL ORDER BY year DESC, quarter DESC LIMIT 4", (code,)).fetchall()
    eq = conn.execute("SELECT total_equity FROM financial_data WHERE stock_code=? AND is_annual=0 AND total_equity>0 ORDER BY year DESC, quarter DESC LIMIT 1", (code,)).fetchone()
    u = {}
    if len(qs) >= 2:
        ttm_eps = sum(r[0] for r in qs) / shares
        if ttm_eps > 0:
            p = round(price/ttm_eps, 2)
            if 0 < p < 9999: u['per'] = p
    if eq and eq[0]:
        ttm_bps = eq[0] / shares
        if ttm_bps > 0:
            p = round(price/ttm_bps, 4)
            if 0 < p < 999: u['pbr'] = p
    if u:
        conn.execute('UPDATE stock_universe SET '+','.join(f'{k}=?' for k in u)+' WHERE stock_code=?', list(u.values())+[code])
        updated += 1
# ROE/ROA
conn.execute("""
    UPDATE stock_universe SET
        roe=(SELECT ROUND(fd.net_income*100.0/fd.total_equity,2) FROM financial_data fd WHERE fd.stock_code=stock_universe.stock_code AND fd.is_annual=1 AND fd.net_income IS NOT NULL AND fd.total_equity>0 ORDER BY fd.year DESC LIMIT 1),
        roa=(SELECT ROUND(fd.net_income*100.0/fd.total_assets,2) FROM financial_data fd WHERE fd.stock_code=stock_universe.stock_code AND fd.is_annual=1 AND fd.net_income IS NOT NULL AND fd.total_assets>0 ORDER BY fd.year DESC LIMIT 1)
    WHERE stock_code IN (SELECT DISTINCT stock_code FROM financial_data WHERE is_annual=1 AND net_income IS NOT NULL)
""")
conn.commit(); conn.close(); print(f'PER/PBR: {updated}개, ROE/ROA 업데이트 완료')
EOF
```

---

## 9. 알려진 이슈 & 제한사항

| 항목 | 상태 | 내용 |
|------|------|------|
| KRX 승인API (data-dbg.krx.co.kr) | ✅ 정상 | OHLCV·지수 정상 수집. PER/PBR은 제공 안 함 → DB 직접 계산 |
| KRX 웹API (data.krx.co.kr) | ✅ Playwright로 정상 | requests 방식 CSV 다운로드 실패(보안강화). Playwright(실 브라우저) → 로그인+OTP+CSV 모두 성공. 매일 18:10 스케줄링됨 |
| K-mydata | ❌ 인증실패 | KRX_API_KEY가 K-mydata용 아님 |
| pykrx | ❌ Empty | KRX 서버 차단으로 빈 DataFrame |
| 공공데이터포털 투자자API | ❌ 404 | getStocInvtTrdnInfo 서비스 폐지 |
| investor_trading_daily | ⚠️ 미수정 잔존 | 여전히 매수금액(buy-only) 오염 상태 — kiwoom_investor_daily는 2026-07-21 trde_tp='0' 수정으로 해결됐으나 이 테이블(동일 로직 복사본)은 미반영. |
| foreign_holding_daily | ℹ️ 정상 적재 중 | 문서상 "0행"으로 남아있었으나 실측 107,764행 확인(Kiwoom ka10008 경유로 이미 채워지고 있음, 문서만 stale이었음) |

### 키움 REST API 확인된 엔드포인트 (URI: /api/dostk/stkinfo, Bearer 토큰)
| API-ID | 설명 | 필수 파라미터 |
|--------|------|--------------|
| `ka10001` | 종목기본정보 (PER/PBR/ROE/EPS/BPS/유동주식수/외국인지분율/시가총액/매출/영업이익/순이익) | stk_cd |
| `ka10002` | 증권사별매매 (당일 상위 브로커 매수/매도) | stk_cd |
| `ka10003` | 체결정보 (틱 체결 목록) | stk_cd |
| `ka10013` | 신용거래동향 (신용잔고 추이) | stk_cd, dt, qry_tp |
| `ka10015` | 일별거래상세 (거래량·투자자 수급 포함) | stk_cd, strt_dt, end_dt |
| `ka10058` | 투자자별매매상위종목 (invsr_tp별 순매수상위) | trde_tp, mrkt_tp, strt_dt, end_dt, invsr_tp, stex_tp |
| `ka10059` | **종목별투자자일별순매수** (개인/외국인/기관+10개 세부기관, 100행/page) | stk_cd, amt_qty_tp, trde_tp, dt, unit_tp | ⚠️ 수집기 파라미터 버그: `trde_tp='1'`이 순매수가 아닌 **매수(buy-only)** 반환, `amt_qty_tp='1'`이 수량 아닌 **금액(백만원)** 반환. 검증: `ind+frgn+orgn+natfor+etc_corp=acc_trde_prica(총거래대금)`. 기존 4.5M행은 **매수금액** 저장 상태. 순매수로 해석/사용 금지. |
| `ka10095` | 관심종목 현재 시세 (복수 종목 동시 조회) | stk_cd |
| `ka10100` | 종목 상장기본정보 (상장일, 감사의견, 업종, 대형/중형/소형주) | stk_cd |

URI: /api/dostk/frgnistt
| `ka10008` | 외국인종목별매매동향 (외국인 보유주식수/지분율 추이) | stk_cd |
| `ka10009` | 외국인+기관 복합 (orgn_daly_nettrde+frgnr_daly_nettrde) | stk_cd |
| **kiwoom_investor_daily 파라미터 버그** | ✅ 수정+재수집 완료 (2026-07-21) | `collectors/kiwoom_collector.py` — `trde_tp='0'`이 순매수, `amt_qty_tp='1'`이 금액(백만원)임을 005930 2026-07-20 KIS 실측값(price_history *_amt) 대조로 확정. 전종목 재수집(2,693종목, 105.6만행, 최근 ~1.5년치) 완료, 재검증 결과 KIS와 정확히 일치. `investor_trading_daily`는 동일 로직 복사본이라 여전히 미반영 상태(별도 재수집 필요). |
| **백테스트 market_cap 단위 오류 반복** | ✅ 2차 수정 | stock_universe.market_cap = **억원** 단위. 2026-06-25 "백만원" 오해로 100x 과대 설정(50000=5조, 100000=10조). 2026-06-26 재수정 완료. **재발방지**: 500억+=500, 1000억+=1000, 5조+=50000 (억원 그대로) |
| **backtest.py 하락장 손절 미작동** | ✅ 수정 | `_run_portfolio`·`_run_generic_backtest` 시장필터(`continue`)가 Phase D(손절) 전에 실행되어 하락장 동안 추적손절/손절선이 완전히 무시됨. Phase D를 시장필터 앞으로 이동 완료. |
| **V-GC 거래비용 미계산** | ✅ 수정 | golden_cross 매매에 `_net_profit()` 미호출 → 수수료·세금·슬리피지 미반영. `mkt_cap_억` 저장 + `_net_profit()` 호출 추가 완료. |
| **signal_engine 스크리너 0종목** | ✅ 수정 | 추세·가치·콤보 스크리너 SQL에서 `TREND_MKTCAP_MIN(50억원원)`을 억원 단위 market_cap과 비교하여 0 종목 반환. `/1e8` 변환 추가. 수정 후 추세후보 2종목 정상 반환. |
| StockAnalysis 수급/프로그램 패널 정렬 | ✅ 수정 | `frontend/src/App.jsx` StockAnalysis 헤더 우측 패널을 flex+세로구분선 구조에서 카드형 grid로 재구성해 수급/프로그램/대차잔고 위치 어긋남을 수정. 수급 기준일·프로그램 기준일·대차 기준일 표기도 `fmtPanelDate`로 `YYYY-MM-DD` 형식으로 통일했고, 프로그램 순매수 금액은 `fmtSignedKrw`에 `만원` 구간을 추가해 `-2,000,000원` 같은 raw 숫자가 아니라 `-200만원`처럼 읽히는 형식으로 보정. |
| 글로벌 매크로 수집 데이터 미표시 | ✅ 수정 | `global_macro_data`에는 수집됐지만 `global_macro_categories`에 없는 코드(EU_DAX/EU_FTSE/EU_EUR_USD/JP_NIKKEI/US_10Y_YIELD_YH 등)가 `/api/global-macro/dashboard` 조인에서 빠지던 문제 수정. 미등록 코드 fallback 메타 + 시계열 fallback 추가. |
| ECOS 거시지표 0건 수집 | ✅ 수정 | `collectors/ecos_collector.py`가 ECOS 주기값을 `MM/QQ/YY`로 호출해 `ERROR-100`이 발생. 현 API 형식 `M/Q/A/D`로 수정했고, 2026-07-05에 `161Y008/BBGA00`(M2), `301Y017/SA000·SA100·SA110·SA120`(경상수지/무역수지/수출/수입)까지 확장해 2주차 ECOS 6종을 모두 적재 완료. 단위도 `십억원→조원`, `백만달러→억달러`로 정규화. |
| 글로벌 인텔리전스 프론트 메뉴 위치 | ✅ 이관 | `stock_dashboard`의 `global_econ` 메뉴는 제거하고 `ceo-briefing-platform`의 KAI 프론트 메뉴(`⚔️ 글로벌 인텔리전스`)로 이관. stock_dashboard에서는 `/api/global-macro/*` API만 유지. |
| 글로벌 인텔리전스 2주차 진행률 | ✅ 개선 | `/api/global-macro/roadmap`, `/stats`, `/dashboard`, `/timeseries/{code}`가 한국 2주차 상태를 실제 적재 데이터 기준으로 계산하도록 수정. 한국 핵심지표 포커스 묶음과 `change_basis`, `mom_change_pct`, `yoy_change_pct` 필드를 추가해 프론트에서 전월/전년 비교를 바로 표시할 수 있음. |
| 글로벌 인텔리전스 3주차 진행률 | ✅ 개선 | `/api/global-macro/roadmap`, `/stats`, `/dashboard`에 미국 3주차 상태(`week3_progress`)를 추가. 미국 핵심지표 포커스 묶음(`__focus.us`)과 수익률 곡선/장단기 금리차 신호(`__signals.us`)를 내려 프론트에서 미국 전용 카드와 2Y-10Y 신호를 렌더링할 수 있음. |
| 글로벌 인텔리전스 4주차 진행률 | ✅ 개선 | `/api/global-macro/roadmap`, `/stats`, `/dashboard`에 4주차 상태(`week4_progress`)를 추가. 2026-07-05부터 OECD CLI 3종과 IMF WEO 4종을 실제 수집 상태로 반영하며, 유럽·중국·일본 핵심 지표 포커스(`__focus.eu/cn/jp`)와 지역별 연결 현황(`__signals.week4_regions`)을 함께 내려 프론트에서 4주차 글로벌 확장 패널을 렌더링할 수 있음. |
| 전략 연구 요약 API 500 오류 | ✅ 수정 | `strategy_research_summary.json`에 `NaN` 값이 포함되면 FastAPI가 표준 JSON 직렬화에 실패해 `/api/backtest/strategy-research/summary`가 500을 내던 문제 수정. `scripts/build_strategy_research_dataset.py`와 `routes/backtest.py`에 `_json_safe()` 정규화 추가, `allow_nan=False`로 재발 방지. |
| V-TURNAROUND 과거 PBR 오염 | ✅ 수정+재검증 | `run_backtest_turnaround()`가 과거 백테스트에서도 현재 `stock_universe.pbr`를 참조하던 문제 수정. 2026-07-05부터 `valuation_history.period_end` 기준 역사적 PBR을 우선 사용하고, 누락 시에만 `stock_universe` fallback 사용. 재검증 결과(2026-07-06): avg5=+11.6% [+66.6/-20.1/+9.8/+10.2/-8.4] — AI랠리 기간이 +32.9%→+10.2%로 조정(과거 저PBR 오적용 제거). |
| KOSIS API 키 포맷 | ✅ 수정 | `collectors/kosis_collector.py`가 `.env`의 `KOSIS_API_KEY`를 base64 저장 포맷까지 자동 decode 하도록 수정. 기존에는 인코딩된 값을 그대로 보내 인증 실패가 날 수 있었음. |
| FRED 미국지표 적재 | ✅ 개선 | `.env`에 `FRED_API_KEY` 등록 후 `collectors/fred_collector.py` 실행으로 1,467건 적재 완료. `US_FED_RATE`, `US_CPI`, `US_GDP_GROWTH`, `US_UNEMPLOYMENT`, `US_RETAIL_SALES`, `US_HOUSING_START`, `US_10Y_YIELD`, `US_2Y_YIELD`가 채워져 3주차 핵심 8개 지표가 모두 연결됨. |
| 글로벌 금융여건 지표 확장 | ✅ 연결 | `collectors/global_financial_conditions_collector.py` 신규 추가. FRED 공식 API 기반으로 `EU_ECB_RATE`, `JP_BOJ_RATE`, `US_HY_SPREAD`, `US_BAA_SPREAD`, `US_NFCI`, `US_10Y_BREAKEVEN`, `US_30Y_YIELD`, `US_3M_YIELD` 5,065건 적재 완료. `/api/global-macro/collect?source=global_financial` 및 `source=all`에 연결. |
| D램 실제 현물가 수집 | ✅ 연결 | `collectors/dram_spot_collector.py` 신규 추가. TrendForce/DRAMeXchange 공개 DRAM Spot Price 표에서 Session Average를 수집한다. `MQ_DRAM_PROXY`는 수출단가 대리지표라 실제 현물가로 해석 금지. 실제 spot은 `MQ_DRAM_SPOT_DDR4_8GB_3200` 등 `DRAM_SPOT` 하위 카테고리와 `market:dram:spot:*` quant key에 저장. |
| 글로벌 인텔리전스 시장형 퀀트 지표 | ✅ 연결 | `collectors/market_quant_bridge_collector.py` 신규 추가. 외부 신규 수집보다 기존 `quant_major_indicator_catalog/series`를 우선 사용하며, 글로벌 인텔리전스에 없는 지표만 `MARKET_QUANT`로 브릿지한다. D램 actual spot/proxy, 반도체·이차전지·조선·전력기기·항공/방산, BDI/BCI/BPI/BSI, 철광석, 열연강판 proxy, 유연탄, SMP, 미국 리그 수, KOSPI/KOSDAQ 시장폭·52주 신고/신저·거래량 확산·거래대금, 예탁금·신용·수급·공매도·대차·프로그램매매 등 49개 지표 31,338건 연결. |
| 글로벌 인텔리전스 PMI/중국 수출 | ⚠️ 미연결 | 안정적인 공식 무료 API를 아직 붙이지 못해 `CN_EXPORT`, `CN_PMI_MFG`, `EU_PMI_MFG`, `US_ISM_MFG`는 2026-07-17 기준 0건. FRED/World Bank 확장으로 정책금리·스프레드·세계무역량은 보강 완료. |
| 한국 주택가격지수 수집 | ✅ 우회 완료 | KOSIS 주택 테이블은 현재 키에서 `유효하지 않은 인증KEY`가 반환되므로 직접 사용하지 않음. `collectors/reb_housing_collector.py`가 한국부동산원 R-ONE 공개 통계(`A_2024_00045`)를 수집해 `KR_HOUSING_PRICE` 2021-01~2026-06 66건 적재 완료. |
| OECD/IMF 글로벌 전망치 | ✅ 연결 | `collectors/oecd_cli_collector.py`, `collectors/imf_weo_collector.py`를 `/api/global-macro/collect`와 `source=all`에 연결. 2026-07-05 기준 `US/CN/JP_CLI_OECD`, `US/EU/CN/JP_GDP_GROWTH_WEO` 적재 완료로 4주차 로드맵이 `done` 상태까지 올라감. |
| price_history 수급 | ✅ 57일치 | KIS 매 5분 정상 수집 중, 주말 필터링 적용 |
| 시장 지표 기본날짜 | ✅ 수정됨 | 데이터 부족한 날 대신 수급 20건 이상인 영업일 자동 선택 |
| 주말 데이터 노출 | ✅ 수정됨 | 토/일요일은 기준일 목록 및 자동 선택에서 제외 |
| Trigger 20 | ✅ 수정됨 | URL /api/signals/trigger-ranking 로 수정 |
| 대차잔고 URL | ✅ 수정됨 | /api/buy-candidates/short-sell/${code} |
| 모멘텀 야간 알림 | ✅ 수정됨 | 22:00~08:00 억제 로직 추가 |
| price_history close=0 | ✅ 수정됨 | routes/ingest.py /investor-trends에서 가격 없는 날짜에 close=0 행 생성 버그 → else:continue로 수정. 매 KRX일별 잡에서 자동 정리 추가 |
| 가상매매 현재가 | ✅ 수정됨 | Yahoo Finance 제거, price_history 사용 |
| 개별종목 PBR/PER 지연 | ✅ 완전수정 | stock_universe DB 즉시 반환(0ms) + 백그라운드 Naver 갱신. 5초 재시도 로직 제거 불필요 |
| 시그널 계산 10초 지연 | ✅ 개선 | 서버 시작 시 warm-up + stale-while-revalidate |
| 재무제표 단위 오류 | ✅ 완전수정 | op_profit 597건·net_income 20건·equity 5건 억원→원 변환, Q4 254건 재계산, CFS/OFS 혼용 36건 재수집, 지주사 Q4 NULL 10건 처리, 수집오류 삭제 2건 |
| financial_data 백업 | ℹ️ 보관 | `financial_data_backup_20260412` 테이블로 수정 전 원본 보관 |
| 재무제표 Q4 대규모 손실 | ℹ️ 정상 | 잔존 14건(삼성SDI2016/현대건설2024/대한항공 등)은 실제 이벤트 손실로 수학적 정확값 |
| ETF 수집실패일 오표시 | ✅ 수정됨 | `etf_inclusion_daily`에서 etf_count=0 && etf_amount=0인 날은 수집 실패일로 간주 → extra_signals.py + routes_etf.py `get_available_dates`에서 자동 건너뜀. **재발방지**: ETF 관련 쿼리 시 반드시 `WHERE etf_amount > 0` 또는 valid_rows 필터 적용 |
| 섹터 트렌드 오분류 | ✅ 수정됨 | `sector_mid`(e.g. "상업서비스")가 업종과 맞지 않는 종목 多 → `sector_large` 기준으로 전체 변경. **재발방지**: 섹터 분류는 반드시 `sector_large` 기준으로. `sector_mid`는 신뢰도 낮음 |
| 수출공동 표시 이종업종 혼입 | ✅ 수정됨 | HS코드가 넓어 전혀 다른 업종(HD건설기계 등)이 공동 표시 → 동일 sector_large 종목만 필터링. **재발방지**: HS코드 기반 공동 매핑 시 반드시 sector_large 교집합 필터 필수 |
| **shares_issued 우선주 포함** | ⚠️ 미수정 | 삼성전자(1.43배), SK하이닉스(2.08배), 현대차(1.49배), LG화학(1.27배) 등 대형주에서 shares_issued가 보통주+우선주 합산으로 저장됨 → EPS/BPS 계산시 분모 과대 → 계산값 과소. **재발방지**: `주식수(보통주) = market_cap ÷ 종가`로 역산 가능. 수집기는 보통주 발행주식수를 별도 필드로 저장해야 함 |
| **EPS 저장값 vs 계산값 괴리** | ⚠️ 구조적 | FnGuide 저장 EPS = 지배주주 귀속 순이익 ÷ 보통주 수. 우리 계산 EPS = 전체 NI ÷ shares_issued(우선주 포함). 두 효과가 역방향으로 작용해 일치 불가. **올바른 계산**: 지배주주 순이익(별도 미저장) ÷ 보통주 수(미분리). FnGuide SVD_Main.asp에서 직접 수집한 EPS/BPS를 1순위로 사용할 것 |
| **TTM EPS 계산 부정확** | ⚠️ 주의 | TTM EPS = 최근 4Q net_income ÷ shares_issued 방식은 shares_issued 우선주 포함으로 EPS 과소 계산됨. 단, 분기 데이터 자체(TTM NI 합산 = annual NI)는 정확히 검증됨. 현재 stock_universe에 TTM PER 반영됨 — 외부 사이트 대비 PER 높게 나올 수 있음 |
| **PER/PBR 외부사이트 일치율** | ℹ️ 현황 | Codex 검증(500종목): revenue/op_profit 100%, net_income 97.48%, CF 98-99%. EPS(FnGuide) 67.79%, BPS(FnGuide) 82.53%. PER(Naver) 29.81%, PBR(Naver) 49.49% — 기준 연도·주식수 차이에 기인. 네이버 기준일 vs FnGuide TTM 기준일 다름 |
| **4중검증 L3 수정 후 B/S NULL 증가** | ℹ️ 구조적 | fnguide/legacy B/S 파싱오류 ~10,440건을 NULL처리함. B/S NULL 행은 P&L 표시에는 영향 없음. 향후 DART 재수집 시 자동 채워짐. data_source: fnguide_bs_null_fix, legacy_bs_null_fix, quarterly_recalc_bs_null |
| **dart_recollect 분기 NI 파싱실패** | ⚠️ 5,228건 | DART 분기 보고서에서 당기순이익 XBRL 태그 매핑 실패. dart_collector.py NI 키워드+XBRL 태그 확장 완료(dart_NetIncome·dart_ProfitLossForThePeriod 추가). 다음 DART 재수집(00:30 자동)부터 점진 해소 예정 |
| **dart L3 assets=liabilities 자본잠식 파싱버그** | ✅ 수정됨 | 자본잠식 기업에서 total_liabilities가 total_assets와 동일하게 파싱되는 버그 22건 수정(liabilities=assets-equity로 복원). 잔존 dart_recollect 104건(1~5%)은 NCI 차이로 정상 |
| **dart_ofs_backfill B/S 파싱오류** | ✅ 수정됨 | assets=liab 1,602건·assets=equity 1,913건 NULL처리 완료. 해당 행은 OFS(별도) P&L 데이터는 정상이나 B/S가 오파싱됨 |
| StockEasy 일치율 검증 편향 | ✅ 수정됨 | 기존 `stockeasy_logic_validator.py`가 보유(매수) 정합성(F1) 중심으로만 검증하고 매도(편출) 적중률을 측정하지 않던 문제 수정. 매도 정답(removed+exits_today) vs 우리 매도후보 비교(P/R/F1) 추가, 리포트/튜닝로그 동시 기록. |

---

## 9-1. 데이터 검증 규칙 (외부사이트 교차검증 가이드)

### EPS/BPS 신뢰도 계층 (Codex 지시서 20260516 기준)
```
1순위: DART financial_data.eps (is_annual=0, 최근 4분기 합산) — DART 공시 기반
       → Q4 EPS = annual_eps - Q3_cumulative_eps (Annual + Q3 둘 다 있을 때만 추론)
2순위: 분기 net_income 4Q 합산 ÷ (market_cap ÷ 종가) 역산 보통주수
       → shares_issued 직접 사용 금지 (우선주 포함 과대)
3순위: stock_universe 배치계산값 — 정확도 낮음

FnGuide EPS/BPS → financial_source_snapshot 검증 전용. financial_data write 금지.
```

### shares_issued 오류 패턴 (외부 검증 기준)
```python
# 보통주 발행수 역산 (시총 ÷ 종가 = 보통주 기준 주식수)
real_common_shares = market_cap ÷ current_price

# 비율 > 1.1이면 우선주 포함 의심
ratio = shares_issued / real_common_shares

# 주요 우선주 보유 종목: 삼성전자, SK하이닉스, 현대차, 기아, LG화학 등
# → EPS/BPS 계산 시 이 종목들은 FnGuide 수집값 우선 사용
```

### 외부사이트 비교 기준 정리
| 지표 | 우리 DB 기준 | 네이버 기준 | FnGuide 기준 | 차이 원인 |
|------|------------|-----------|-------------|---------|
| EPS | 전체NI÷전체주식수 (부정확) | 최신 사업보고서 EPS | 지배주주NI÷보통주수 TTM | 주식수·귀속NI 기준 다름 |
| BPS | 전체자본÷전체주식수 | 최신 사업보고서 BPS | 지배주주자본÷보통주수 | 비지배주주지분 포함 여부 |
| PER | 종가÷TTM_EPS | 종가÷사업보고서EPS | 종가÷FnGuide_EPS | 기준 연도+주식수 모두 다름 |
| PBR | 종가÷TTM_BPS | 종가÷사업보고서BPS | 종가÷FnGuide_BPS | 상동 |
| ROE | annual NI÷annual equity | 사업보고서 ROE | 지배주주 기준 ROE | 귀속 기준 다름 |

### 향후 수집기 개선 지침
```
1. shares_issued_common (보통주만) 필드 별도 추가 → stock_universe 스키마 확장 필요
2. controlling_interest_ni (지배주주 순이익) 별도 수집 → financial_data 확장
3. FnGuide SVD_Main.asp 연 2회 재수집 (Q2/Q4 실적 공시 후: 8월, 3월)
4. EPS/BPS 검증: 수집 후 net_income÷shares vs EPS 차이 >30% → 재수집 플래그
5. market_cap ÷ 종가 역산값 vs shares_issued 비율 >1.15 종목 → 우선주 보정 필요 플래그
```

---

## 10. 고용정보 페이지 — 데이터 수집·로직 필수 참조

> **이 섹션을 먼저 읽지 않고 고용정보 관련 코드를 수정하지 말 것.**

### 파일 구조
```
employment_monitor/
├── routes_employment_v2.py   # FastAPI 라우터 (/api/employment-v2/*)
├── collect_labor_welfare.py  # WLB 수집기 (근로복지공단 고용보험)
├── collect_nps_monthly.py    # NPS 수집기 (국민연금 월별 신규/상실)
└── employment.db             # 고용정보 전용 DB (stock.db 아님)
```

### 두 데이터 소스의 본질적 차이 (반드시 이해할 것)

| 항목 | WLB (고용보험) | NPS (국민연금) |
|------|--------------|--------------|
| 테이블 | `wlb_monthly` | `nps_monthly` |
| 데이터 성격 | **스톡(Stock)** — 특정 시점 피보험자 총 수 | **플로우(Flow)** — 월별 신규취득/상실 건수 |
| 현재 보유 월 | `202505`(2026-05-04 수집), `202605`(2026-05-09~15 수집) | `202504`~`202603` 완전(2111~2151개사), `202604`~이후 대부분 미수집 |
| API 특성 | **날짜 파라미터 없음** → 실행 시점의 현재 데이터만 반환 | 월별 과거 조회 가능 |
| 용도 | 현재 피보험자 수 표시 | 기간별 순증감 계산 |

### ⚠️ WLB API 핵심 제약 (절대 잊지 말 것)
```
WLB API는 날짜 파라미터가 없다.
--month YYYYMM 은 DB 저장 레이블일 뿐, 과거 데이터를 가져오지 않는다.
즉, 언제 실행해도 항상 "실행 시점의 현재 데이터"만 수집된다.

현재 DB 상태:
- data_ym='202505': 2026-05-04에 수집 → 2026년 5월 초 시점의 피보험자 수
- data_ym='202605': 2026-05-09~15에 수집 → 2026년 5월 중순 시점의 피보험자 수
- data_ym='202504', '202506': 잘못된 레이블로 수집 → 이미 삭제됨

⚠️ 202505도 2026-05-04에 수집된 것. "2025년 5월" 데이터가 아님. 레이블 주의.
```

### NPS 데이터 해석 규칙
```
nps_monthly.data_ym = '202603'
  → 2026년 3월 한 달 동안 국민연금에 신규 가입한 인원(new_hires)과 상실한 인원(terminations)
  → net_change = new_hires - terminations (그 달의 순증감)

기간별 순증감 = 각 종목의 최신 data_ym 기준으로 N개월 net_change 누적합
(전체 통일 ref_ym 고정 방식 사용 금지 — 최신 데이터 있는 종목을 무시하게 됨)
```

### 이상값 필터에 대하여
```
⚠️ Claude가 임의로 이상값 필터(2000명 절대값, 평균의 5배 등)를 추가/변경하지 말 것.
합병·분사·법인 전환은 실제 사업 이벤트이며, 필터 기준은 사용자가 결정한다.
현재 코드에 필터가 있다면 사용자 확인 후에만 수정할 것.
```

### 피보험자 수 표시
```
1순위: WLB 202605 실측값
2순위: 미수집 종목은 NPS 누적 추정 또는 미표시 (사용자 결정)
⚠️ 202605 없다고 202505 자동 fallback 금지 — WLB 레이블 신뢰도 문제 있음
```

### API 엔드포인트 (routes_employment_v2.py)
```
GET /api/employment-v2/trend           # 종목별 피보험자+기간별순증감 (메인 테이블)
  ?sort_by=workers|1m|3m|6m|1y
  ?limit=200
GET /api/employment-v2/insurance/chart # 기업별 그래프
  ?code=047810
GET /api/employment-v2/insurance       # 고용보험 상시인원 순위
GET /api/employment-v2/annual-top      # 사업보고서 기준 연간 인원 순위
```

### 알려진 데이터 한계 및 미결 사항
- NPS 202604는 정상 기준월(2,084종목)로 수집됨. 202605는 취득/상실 API가 대표 종목에서도 `totalCount=0`을 반환해 아직 화면 기준월에서 제외(`nps_ref_ym=202604` 유지).
- WLB 202605 미수집 종목 275개 (에코프로 086520 포함) — 재수집 필요
- WLB data_ym 레이블이 실제 수집 시점과 다를 수 있음 — 레이블 정책 재정의 필요
- NPS는 국민연금 기준이므로 고용보험 미가입 프리랜서·특수고용직 제외

---

## 11. 자주 수정하는 작업별 파일 가이드

| 작업 | 파일 | 참고 위치 |
|------|------|-----------|
| 새 API 엔드포인트 추가 | `routes/new.py` 생성 → `main.py` 등록 | main.py 38~56줄 |
| 시그널 로직 수정 | `signal_engine.py` | 섹션 1 함수목록 참조 |
| 스케줄 시간 수정 | `scheduler.py` | `_seconds_until()` 호출부 |
| 가상매매 로직 | `routes/trend.py` + `peak_monitor.py` | — |
| 포트폴리오 로직 | `routes/portfolio.py` | — |
| 프론트 탭 추가 | `App.jsx`: 컴포넌트 + NAV_ITEMS + 렌더스위치 | 줄: 7459, 7585 |
| Telegram 알림 | `peak_monitor.py` + `notifier.py` | — |
| DB 스키마 변경 | `init_db.py` + `migrate_db.py` | — |
| KIS 수집 로직 | `collectors/kis_collector.py` | — |
| 환경변수 추가 | `.env` + `config.py` | — |

---

## 11. 변경 이력

> 🪙 **토큰 최적화 규칙 (2026-09-03, 2차 재발)**: 이 섹션(변경 이력)은 CLAUDE.md 전체와 함께 **매 세션 자동 로드**됩니다.
> 2026-07-17에 한 번 archive 분리를 했음에도 6주 만에 다시 774KB로 재폭증했습니다 — 원인은 항목당 '한 줄 기록' 규칙을 어기고
> 수백~수천자짜리 기술 리포트를 통째로 붙여넣은 것입니다. **재발 방지**:
> 1. 항목은 정말로 1~3문장 요약만 기록하고, 상세 분석/근거/SQL/CSV는 `docs/` 또는 `scratch/`의 별도 날짜별 문서로 분리해 링크만 남기세요.
> 2. 최근 20~25개 항목만 유지하고, 그 이전은 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래에 이어붙이세요.
> 3. 2026-07-01~2026-08-30 전체 이력(276건)은 이번에 전량 archive로 이관되었습니다. 과거 버그 재발/근거 추적 시에만 Read/grep 하세요.

> 📦 **2026-08-30 이전 변경이력은 `docs/CLAUDE_CHANGELOG_ARCHIVE.md`로 이관되었습니다** (2026-09-03). 아래는 2026-08-26 이후 최근 20개 항목만 표시합니다.

### 2026-08-30 사용자 질문 2건 — DART 3키 실사용여부 재확인 중 로테이션 자체가 무력화돼있던 버그 발견·수정 + KRX/공공데이터포털 API 라이브 재검증
> 사용자: "1. 12시가 넘어가면 dart 초기화되니까 시도해, 그리고 id가 3개나 있는데 모두 사용한거야? / 2. 첨부한 이미지와 같이 krx openapi를 사용하고 있는것으로 알고 있는데, codex가 안된다고 하고 있음. 점검해봐"
- **①DART 3키 재점검 — "모두 사용한거야?"에 대한 답: 아니오, 사실상 1개만 쓰이고 있었음(치명적 버그 발견)**. `dart_key_manager.RotatingOpenDartReader`는 쿼터소진 감지를 `is_quota_error(result)`로 하는데, `OpenDartReader.finstate_all()`은 DART가 status≠'000'(쿼터소진 포함)이어도 예외를 던지거나 에러값을 리턴하지 않고 **그냥 `print(jo)` 후 빈 DataFrame을 반환**함(`venv/.../OpenDartReader/dart_finstate.py:64-69`) — `is_quota_error()`는 DataFrame을 통째로 무조건 `False` 처리하도록 짜여 있어서, 쿼터소진이 "빈 데이터(정상적인 조회결과없음)"와 절대 구분되지 않고 로테이션이 **단 한 번도 발동하지 않고 있었음**(실측: KEY1 쿼터소진 상태에서 계속 KEY1만 재시도). **수정**: `_call()`에서 `contextlib.redirect_stdout`으로 라이브러리가 인쇄하는 원본 에러 JSON을 캡처해 그 텍스트에서 쿼터 마커를 찾아내는 방식으로 우회 탐지 추가. 수정 후 재현 테스트: KEY1→KEY2 순서로 정확히 로테이션되며 KEY3에서 정상 데이터(176행) 수신 확인. **즉 어제(8/29) financial_data/cash_flow_data 전건검증에서 "쿼터소진"으로 보였던 것은 실제로는 3키 합산 용량이 아니라 어느 한 키의 용량에 근접했을 가능성이 있음** — 다만 verify_all 스크립트들은 처음부터 quota_hit=False로 완주했으므로 실질적 손해는 크지 않았고, 문제는 `_fetch_dart_annual()`(교차검증 전용, 이 함수만 별도로 KEY1 고정 사용) 경로에 집중돼 있었음. `collectors/fnguide_financial_collector.py`의 `_get_dart_client()`도 `RotatingOpenDartReader`로 교체(기존엔 KEY1 하나만 썼음). 수정 후 financial_source_snapshot 백필 재실행 결과 KEY1/KEY2 자동 스킵 후 KEY3로 정상 처리 확인.
- **②KRX 주가 API(공공데이터포털/data.krx.co.kr) 자체는 라이브 정상** 확인(price API 직접 호출 성공). `collect_krx_history.py`는 scheduler에 미등록된 수동 스크립트.

### 2026-08-30(2차) Codex의 ETF Check 대체 파이프라인(PDF 역산) — KRX 로그인 버그 발견·수정
> 배경: etfcheck.co.kr 스크래핑의 저작권 리스크 회피 위해 Codex가 KRX 공식 PDF(포트폴리오예탁파일) 기반 파이프라인(`ETF_check/full_pdf_collector.py`)을 작성 중이었으나 4연속 실패("KRX authentication did not issue a client session").
- **원인**: KRX 로그인 시 "이미 로그인된 계정입니다" 확인 다이얼로그가 `<button>`이 아닌 다른 태그로 렌더링돼 기존 `button:has-text('확인')` 셀렉터가 매칭 실패 → 다이얼로그가 안 닫혀 로그인 미완료. Playwright로 직접 재현해 원인 특정.
- **수정**: 셀렉터를 `a`/`button`/`input[value='확인']` 전체로 확장(`full_pdf_collector.py` `_login()`). 단독 재현 테스트 1회 성공(세션쿠키 확인) — 단 반복 재현 시 "이미 로그인된 계정" 재발(내 반복 테스트 자체가 세션을 계속 점유한 것으로 추정, KRX측 동시세션 제한 가능성). 실제 스케줄 실행(21:15/02:15)에서 안정적으로 통과하는지 다음 실행 로그 확인 필요.
- **Codex 코드에 대한 그 외 개선 포인트**(서브에이전트 감사, 코드 수정은 안 함): (1) KIS기반 임시 파이프라인이 실제로는 상위 30종목만 반환하는 추정치인데 테이블명이 `etf_pdf_daily`라 "공식 PDF값"으로 오인될 위험 — 개명 권장. (2) `full_pdf_*`/legacy etfcheck.co.kr 스크래퍼/KIS추정 3개 파이프라인이 아직 어느 것도 routes_etf.py·프론트에 연결 안 됨. (3) KRX 로그인 실패가 4회 연속돼도 사람에게 알림 없이 조용히 실패 기록만 함(legacy 스크래퍼는 텔레그램 알림 있음).


> 사용자: "전략조합으로 600%가 넘는 실적을 얻었는데? 이 로직으로 가상매매가 진행되고 있어? 그리고 20개가 넘는 전략센터 내 전략중에서 진짜 몇개만 남은것으로 보이는데, 키움에서 여러 데이터가 추가된 만큼 추가할 전략이나 기존 전략을 개선할 여지는 없는지 검토해" — 1번 질문(가상매매 미작동)은 바로 위 2026-08-26 항목에서 이미 처리(v8/v2 어댑터 추가로 sc_* 5개 페이퍼계좌 정상화). 이 항목은 2번 질문(전략 확장/개선 여지)에 대한 조사.
- **governance tier 현황 재확인**: 25개 전략 중 `retired` 18개, `validation_queue` 4개(sector_focus/v2/earnings_conviction/v11), `offensive_satellite` 1개(golden_cross), `paper_core` 2개(contract_momentum/v8), `live_eligible` 0개 — 실제 종이운용 이상 등급은 7개뿐이라는 사용자 관찰이 정확했음을 재확인.
- **⚠️ governance 경계선 硬直性 발견**: `strategy_governance.py`의 `validation_queue` 기준은 `positive_periods>=4`(6구간 중 4구간 이상 플러스)를 요구하는데, **v5(V4수급모멘텀, avg=21.95%)·vbr(V8 52W돌파, avg=19.63%)·v1_value(V2가치매수, avg=18.95%)가 딱 3구간 플러스라 1구간 차이로 retired**로 밀려나 있음 — 특히 v1_value는 worst=-3.87%(거의 손실 없음, 3승3패지만 패배폭이 -2.45~-3.87%로 극소)인데도 기계적 임계값 미달로 퇴역 분류. 전략 품질 문제라기보다 판정기준의 이산적 경계 문제로 판단, 재조정은 신중한 별도 검토 필요(이번 세션에서 기준 자체는 변경하지 않음).
- **Kiwoom 데이터 활용 현황 매핑**: `kiwoom_credit_balance`(신용잔고비율, 407만행/2,680종목, 최신 2026-08-25 — 2026-08-23 근본수정 이후 완전히 정상화 확인)는 `run_backtest_recovery`의 `market_regime_gate_min`·`run_backtest_megatrend`의 `smart_money_min_score`에만 실험적으로 연결(둘 다 기본값 미적용, 과거 세션에서 각각 기각 이력). **`kiwoom_investor_daily`(기관 세부매매 10종 분류, 465만행, 최신 2026-08-26)와 `kiwoom_foreign_flow`(외국인 보유비중, 최신 2026-08-26)는 `backtest.py` 어디에도 전혀 사용되지 않는 완전 미개척 데이터**임을 확인.
- **신규 검증 ①기관 세부유형별 20일 순매수(kiwoom_investor_daily) — 기각**: `strategy_feature_snapshot`(mktcap≥300억, n_train=72,474/n_test=67,113, 학습≤2022-12/검증2023-01+) 기준 walk-forward. 연기금(penfnd_etc)/보험(insrnc)/사모펀드(samo_fund)/투신(invtrt)/은행(bank)/금융투자(fnnc_invt)/기타법인(etc_corp) 7개 유형 전부 forward_max_ret_12m에 대해 lift 0.81~1.14x(무판별력~약한 역효과), 대부분 학습·검증 방향 불일치 또는 일관된 역효과 — 신용잔고+수급을 "결합"했던 기존 채택 신호(2026-07-22(9차), lift 2.39x)와 달리 단일 기관유형 買い越し만으로는 무효.
- **신규 검증 ②외국인 보유비중 20일 변화(kiwoom_foreign_flow) — 판정불가(데이터 부족)**: 테이블 실측 결과 종목별 이력이 **2026-04월부터만 존재**(211,938행이 사실상 최근 수개월치뿐) — `strategy_feature_snapshot`(2020-01~2026-07)과 asof merge 시 매칭 0건, walk-forward 검증 자체가 현재로선 불가능. 기각이 아니라 데이터 축적 후 재검증 대상으로 보류.
- **신규 검증 ③신용잔고<1% 제외필터를 V-RECOVERY에 실전 구현 — case-control은 유망했으나 실행백테스트에서 기각**: 낙폭과대 population(52주고점대비-30%↓, 시총300+)에서 신용잔고비율 3분위 사전검증 결과 <1%(매우낮음) lift 1.21x(학습)/1.11x(검증) — 단조·방향일치로 유망해 보여, `run_backtest_recovery`에 `max_credit_ratio` 파라미터 신규 구현(kiwoom_credit_balance asof 조회, bisect 기반, 30일 이내 데이터 없으면 판단불가로 통과). **6기간 전수 실행 백테스트 결과 avg6 0.90%(3/6)→-5.03%(3/6)로 오히려 악화** — 단일기간(25.6~26.3)만 보면 -3.41%→+17.34%(+20.75pp)로 극적 개선처럼 보였으나 22.11~23.10(-26.86pp)·23.11~24.12(-32.18pp) 2개 구간에서 대폭 악화, 거래건수도 감소(정상 반등후보 배제). 이번 세션 ①과 동일 패턴(case-control 사전검증 lift가 실행가능 전략에 전이되지 않음) 재확인. `max_credit_ratio` 파라미터는 코드 보존(기본 None=미적용), signal_experiment_ledger 기록.
- **⚠️ V-RECOVERY 성과 하락 원인 후속 진단 — 코드 버그 아님, price_jump_audit로 인한 데이터 변화로 추정**: 위 baseline(avg6=0.90%, 3/6, 보너스 포함)이 2026-07-20 기록(+29.5%)과 크게 괴리된 것을 확인한 뒤, **보너스를 전부 비활성화(turnaround_bonus=None, flow_bonus=None)한 순수 베이스라인을 6기간 재실행**해 2026-07-12 기록(보너스 도입 전 avg6=+25.8%)과 대조 — 결과 **avg6=-4.05%(3/6)로 약 30pt 하락**, 보너스 있는 버전(-29pt 하락)과 거의 동일한 낙폭. 보너스 유무와 무관하게 같은 폭으로 떨어졌다는 것은 `turnaround_bonus`/`flow_bonus` 로직 자체의 결함이 아니라 그 전제인 기초 가격데이터(MA60낙폭/52주저점거리/거래량반등 판정에 쓰이는 `price_history` 시계열)의 값 자체가 코드 변경 없이 바뀌었음을 시사 — 시점상 2026-08-22~24 price_jump_audit 재구축(가격 이상치·펌프덤프 아티팩트 대량 정정)과 일치. **낙폭과대반등 전략은 정의상 급락 패턴에 의존하므로, 과거 고수익 기록의 일부가 실제로는 가격 데이터 오류(가짜 급락→가짜 급반등)에서 나온 착시였을 가능성**이 있음(원인 확정은 아니며 정확한 인과관계 미확인). 즉 현재의 낮은 성과(governance상 retired)는 버그가 아니라 더 정확해진 데이터를 반영한 결과일 개연성이 높음 — 코드 변경 없이 진단만 완료, signal_experiment_ledger에 기록(`recovery/no_bonus_baseline_vs_historical_record_20260827`).
- **결론(사용자 질문에 대한 답)**: 20+개 전략 중 7개만 稼働 중인 것은 governance 경계선의 이산적 판정 기준(4/6구간 요구) 영향이 일부 있으나 대체로 정당한 필터링. Kiwoom 신규데이터(신용잔고/외국인지분율/기관세부매매)를 활용한 신규 시그널 3건을 이번 세션에서 직접 탐색·구현·검증했으나 **전부 기각**(1건은 데이터 부족으로 판정 보류) — Kiwoom 데이터가 아직 검증 가능한 형태의 알파를 제공하지 못하고 있음을 정직하게 확인. signal_experiment_ledger에 전부 기록(`discovery_tools/kiwoom_institutional_subtype`, `discovery_tools/kiwoom_foreign_weight_change`, `recovery/max_credit_ratio_1pct_exclude_filter_20260827`).

### 2026-08-29 미국종목 13F 거물 매매 분석 추가
- `routes/us_13f.py`와 `GET /api/us-13f/summary`를 추가했다. SEC EDGAR 원문의 최신·직전 `13F-HR` 정보표를 비교해 신규 편입, 추가 매수, 비중 축소, 전량 매도를 CUSIP 기준으로 반환하며 12시간 캐시와 원문 링크를 제공한다.
- `frontend/src/App.jsx` `USStocksView`에 `13F 거물 동향` 탭을 추가했다. 운용사 필터와 매수/매도 변동 필터, 여러 운용사 동시 변동, 운용사별 변동표를 표시한다. 13F의 분기 지연·롱 포지션 한계는 화면과 API 문서에 명시했으며 자동 추격 주문에는 연결하지 않는다.

### 2026-08-29 미국종목 13F 거물 동향 확장
- 13F 분석 대상을 20개 운용사로 확대하고 Nancy Pelosi의 하원 공식 PTR을 별도 출처로 추가했다. 인물 선택 시 현재 13F 보유 포트폴리오와 이번 분기 매수/매도·축소를 분리해 표시하며, 여러 운용사의 중복 변동은 별도 동시 변동표에서 강조한다.
- `scheduler.py`에 `미국13F거물공시` 일일 07:12(KST) 점검을 추가했다. SEC/House 원문을 재확인해 13F 캐시를 갱신한다. Buffett 선택 시 SEC companyfacts의 Berkshire 현금·단기투자자산 시계열 차트를 표시한다.

### 2026-08-29 13F 매매판단 정보구조 보강
- 13F 행에 보고 기준일·제출일·실제 매매일 공개 여부를 분리하고, 수량 변화와 보고가치로 계산한 `change_notional_usd`(추정 변동 규모) 및 현재 보고 포트폴리오 비중을 추가했다. 13F는 실제 체결일을 공개하지 않으므로 UI에 `13F 비공개`로 명시한다. House PTR만 실제 거래일과 신고 금액 범위를 표시한다.
- 동시 변동표는 같은 방향으로 움직인 인원수와 매수/매도 합산 추정 변동 규모를 표시한다. 집계는 각 운용사의 보고가치 기준 상위 100개 변동 안에서 수행되므로 전체 비공개 거래의 공통 보유 현황을 뜻하지 않는다.

### 2026-08-29 13F 투자 스타일 프로필 추가
- Ken Fisher(Fisher Asset Management)와 Cathie Wood(ARK Investment Management)를 13F 대상에 추가해 총 22개 운용사를 조회한다. 모든 대상에 가치·성장·퀀트·매크로·이벤트드리븐 등 스타일과 투자 초점 메타데이터를 부여하고, 미국종목 선택 UI에 함께 표시한다. 스타일은 과거·공개 운용 철학의 요약으로 개별 공시 종목의 매수 근거나 향후 성과를 보장하지 않는다.

### 2026-08-29 13F 유명 펀드매니저 2차 확장
- Viking(Andreas Halvorsen), Coatue(Philippe Laffont), Tudor(Paul Tudor Jones), Paulson(John Paulson), Farallon(Thomas Steyer), Balyasny(Dmitry Balyasny), Millennium(Izzy Englander), Point72(Steve Cohen)를 SEC CIK 검증 후 추가했다. 13F 분석 대상은 총 30개 운용사이며, 각 운용사의 성장·테크·매크로·이벤트드리븐·멀티전략 특성을 함께 표시한다.

### 2026-08-29 13F 바이오 전문 운용사 추가
- Baker Bros.(Julian·Felix Baker), Perceptive(Joseph Edelman), RA Capital(Peter Kolchinsky), OrbiMed(Sven Borho), Deerfield(James Flynn)를 SEC 13F 원문 확인 후 추가해 총 35개 운용사를 조회한다. `바이오 전문` 프로필은 임상·규제·상업화 촉매에 민감하므로 일반 성장주 공통매수와 같은 의미로 해석하지 않도록 스타일·초점에 명시한다.

### 2026-08-29(3차) price_history H1 2022 KRX 오염 — 전수 자동탐지로 5개 클러스터 일괄 정정(총 358건/90종목) + 소규모 22건은 증거불충분으로 보류
> 사용자: "게속해"(2차 발견 이후 계속) — 개별 날짜를 하나씩 찾는 대신, 전체기간·전종목 대상 "전일대비 급변일" 자동 스캔(LAG 윈도우함수, 7.5초)으로 일괄 탐지.
- **전수스캔 결과**: 급변종목 15개+인 날짜 18건 발견 — 그중 2022년 구간에 **2/21(44)·2/22(24)·2/24(65)·3/10(30)·3/14(23)·3/15(36)·3/18(29)** 신규 클러스터 다수 확인. 000670(SK하이닉스)로 정밀추적한 결과 **한 종목이 Feb~Mar 2022 사이 3차례(2/21-23, 3/10-11, 3/15-17) 반복적으로 오염-복귀를 겪는 패턴**을 발견 — 단발성 사고가 아니라 이 시기 KRX 파이프라인이 상습적으로 결함을 일으켰음을 시사. 지수(^KS11/^KQ11)는 전 구간 정상.
- **일반화된 자동탐지 알고리즘 신규 구현**(`scratch/tccbridge/detect_all_2022h1_bursts.py`): 종목별 시계열을 순회하며 "전일대비 ±40%+ 급변 시작 → 직전 안정가(baseline) 대비 ±15%이내로 복귀할 때까지(최대 4거래일) 사이의 모든 날"을 오염행으로 표시하는 범용 로직 — 2021-12~2022-07 구간(45.7만행)에서 **387건/82종목** 탐지(2.2초 소요).
- **표본검증으로 오탐 분리(중요)**: 주요 클러스터(2/21·22·23, 3/10·11·14·15·16·17, 1/3·1/4·5/9) 외의 소규모(33건) 표본을 전수 확인한 결과 **명확한 오탐 다수 발견**(089850/192410 등은 전후값이 사실상 비슷한 범위라 정상 변동성, 203690/221610은 복귀 없이 지속되거나 점진하락하는 패턴이라 진짜 재평가/하락일 가능성) — **이 소규모 22건(139050 11건 제외)은 증거 불충분으로 정정하지 않고 그대로 보류**. 반면 006740·024850은 동일 종목 내 baseline 대비 명확한 burst-then-revert 재현이 확인돼 주요 클러스터와 함께 정정 대상에 포함.
- **정정 실행(3단계, 139050 비상장 매번 자동 제외)**: ①주요 클러스터(2/21·22·23 + 3/10·11·14·15·16·17 + 1/3·1/4·5/9 잔여) 346건/78종목 — `price_history_h1_2022_burst_backup_20260829` 백업 후 삭제. ②재스캔 결과 2022-01-03에만 13건(139050 제외 12건) 잔존 확인 → `price_history_jan03_residual_backup_20260829` 백업 후 추가 정정. **누계: 358건/90종목**(2026-08-29 세션 전체 합산: Feb8-9 134건 + 이번 358건 = 492건).
- **최종 재검증**: 동일 스캔쿼리로 재확인 결과 H1 2022 구간에서 10건+ 몰린 날짜가 **완전히 소멸**(0건), 표본종목(000670/021045/001080) 전부 자연스러운 연속 흐름으로 복원(오염일은 결측으로 남음, KRX 재조회는 2026-08-23(3차) 교훈에 따라 시도하지 않음).
- **잔여**: 소규모 22건(039230/052790 등 개별 종목, 증거 불충분) 미해결 — 다음에 볼 때는 3~4일이 아닌 더 넓은 윈도우(예 10일)로 복귀여부를 재확인하거나 개별 DART 공시 대조가 필요. 2015-01-02(845)·2018-12-24(458)·2019-01-02(370)·2010-05(3개 날짜, 각 15~19건) 구간은 규모가 훨씬 크고 원인 성격이 다를 가능성이 높아(오래된 구간, 데이터 신뢰도 자체가 다른 시기) 이번 세션에서 다루지 않음 — 별도의 큰 조사 필요, 다음 세션 후보 1순위로 남김. 2024-04-01(4종목, 실거래량 확인)과 2026-04-30/05-18(각 17/15종목)은 조사 결과 복귀 없이 지속되는 패턴(실제 액면병합/재평가로 추정)이라 오염이 아닌 것으로 판정, 정정하지 않음.
- 재현: `scratch/tccbridge/full_scan_cluster_dates.py`(전수스캔), `detect_all_2022h1_bursts.py`(자동탐지), `spotcheck_singleton_bursts.py`(오탐분리), `remediate_h1_2022_bursts.py`, `finish_jan03_residual.py`.

### 2026-08-29(2차) price_history 4번째 KRX 오염클러스터(2022-02-08~09) 신규 발견·정정 — `price_jump_audit`의 미해결 잔여(unresolved_active_common) 후속 조사
> 사용자: "다른거 더 점검할게 있어?" — 기존 `price_jump_audit`(2026-08-24 재빌드) 미해결 큐 중 가장 큰 카테고리인 `unresolved_active_common`(2,349건, 2010~2026 전 구간)을 처음으로 직접 파고들어 발견.
- **발견 경위**: `unresolved_active_common` 연도별 분포를 보다가 2022년(441건) 내 특정 날짜에 이상 집중된 걸 확인 → 2022-02-08 하루에만 69/70건이 이 분류로 몰려있음을 발견. KOSPI/KOSDAQ 지수(^KS11/^KQ11)는 이날 완전히 정상(2767.76→2746.47, 900.7→895.27, 거래량도 평시 수준)이라 시장 전체 이벤트가 아님을 먼저 확인.
- **패턴 확정**: 021045(2/7 14,250원→**2/8 1,395원→2/9 1,370원**→2/10 13,750원 정상복귀), 001080(2/7 2,246원→**2/8 22,850원→2/9 22,950원**→2/10 2,241원 정상복귀) 등 — 기존에 이미 확정한 2022-01-03(254종목)·2022-05-09(232종목) 오염과 정확히 동일한 시그니처(단, 이번엔 **이틀 연속**(2/8+2/9) 오염이 지속된 뒤 3일째(2/10)에 원상복귀 — 이게 원인). **왜 이전 세션의 전수조사(2026-08-23)에서 놓쳤는가**: 당시 판정기준이 "전일대비 급변 & **익일** 15%이내 원상복귀"(1일 지연 가정)였는데, 이 클러스터는 복귀가 2일 뒤(2/10)에 일어나 그 필터를 통과하지 못하고 `unresolved_active_common`으로 남아있었음 — **오염 지속기간이 항상 하루라는 가정 자체가 놓친 사각지대**였다는 재발방지 교훈.
- **스코프 확정(재현가능 SQL: `d7→d8·d9 40%이상급변 & d10 15%이내복귀`)**: 68종목 매칭, 그중 139050은 `stock_universe` 비상장(사용자 지시 "비상장 주식은 필요 없고" 범위 밖) — **67개 코스피/코스닥 상장종목**만 대상 확정(상장여부 67/67 재확인).
- **정정 실행**: `price_history_corruption_backup_20260829`(134행=67종목×2일) 백업 후 원본에서 2022-02-08·2022-02-09 두 날짜 전량 삭제 → 재검증 결과 잔존 0건, 021045/001080 등 표본 재확인상 결측(gap)으로 정상 복원. **재발취(KRX 재조회) 시도는 하지 않음** — 2026-08-23(3차)에서 이미 "재조회하면 동일 오염값이 그대로 다시 채워지는" 것을 직접 겪고 "삭제 후 결측으로 남기는 것이 유일하게 안전하고 durable한 정정"이라고 결론낸 교훈을 그대로 적용.
- **잔여 상태**: `unresolved_active_common` 2,349건 중 이번 정정(134건)을 제외한 나머지는 대부분 ①2015-01-02/2010-2011년 구간(842건, 최초일자 아티팩트 가설은 기각됨 — 진짜 원인 미상, 오래된 구간이라 우선순위 낮음) ②`stock_price_daily`(비조정) 기준과의 조정기준 불일치(2026-08-23(3차)에서 이미 정책적으로 "비교 불가"로 확정된 것과 동일 클래스) — 남은 건 중 2022년 이후(최근, 라이브 전략에 영향 가능성 있는 구간)만 701건, 이 중 `corporate_action_events` 기록이 전혀 없는 순수 미상 종목은 16개(020180/054940/101680/101970/127980/204210/354320/356890/377220/383220/417200/417310/430690/445680/450520/452430) — 이번에 발견한 것과 같은 4번째 미지의 클러스터가 섞여있을 가능성이 있으나 미조사, 다음 세션 우선 후보로 남김.
- 재현: `scratch/tccbridge/check_unresolved_active_common.py`, `check_recent_unresolved.py`, `check_20220208_cluster.py`, `scope_feb0809_cluster.py`, `check_139050_feb.py`, `remediate_feb0809.py`.

### 2026-08-29 전략센터 데이터 라우팅 레이어 추가
- `routes/strategy_data_lab.py` 신규, `GET /api/strategy-data-lab/overview`로 실적변곡·계약선행·현금전환·재고/매출·수주공시·컨센서스의 커버리지/신선도와 역할(진입·확인·촉매·위험제거)을 반환한다. 다중확인 후보는 `V-CATALYST`/`V-REVISION`/`V-QUALITY-ROUTE` 연구 전용으로만 노출하며 자동매매와 성과 매트릭스에는 연결하지 않는다.
- `frontend/src/App.jsx` `StrategyHub`에 `데이터 라우팅` 탭을 추가해 소스별 상태, 설계 중인 전략 역할, V-CATALYST 다중확인 후보를 표시한다. 과거 실행 검증에서 품질/수주 지표를 매수랭킹에 가산할 때 성과가 악화된 결론을 유지해, 이 데이터는 설명·교차확인·위험표시 역할로 제한한다.

### 2026-08-29 전략센터 고수익 조합 가상계좌 추가 시도 철회
- `sc_return_core` 전방 가상계좌를 추가하려다 철회했다. 최신 등록 병합 run `cmb_c8f841b9708d`의 +688.9%는 단순 `v2 + sector_focus`가 아니라 **V-SECTOR 피라미딩과 최소보유 60일**까지 포함한 명세인데, 최초 연결안은 이 조건을 빠뜨려 동일 전략이 아니었다.
- 재발 방지: 전략 로직·가상운용 어댑터·파라미터를 새로 만들거나 바꿀 때는 배포 전에 반드시 **동일 주문 흐름의 병합 백테스트**, 동점순서 안정성, MDD를 실행한다. 기존 등록 run의 수익률만 인용해 새 실행 경로를 배포하지 않는다.

### 2026-08-29 전략센터 최신구간 전수 백테스트 상태 점검
- 가격 데이터는 2026-08-29까지 있으나, 전략센터 표준 여섯 번째 구간은 여전히 `2025-06-01~2026-03-31(25.6~26.3)`이다. 따라서 이 시점의 전략센터 매트릭스를 "오늘 기준 전수 백테스트"라고 부르면 안 된다.
- `25.6~26.8`로 26개 전략을 일괄 연장하는 실행을 시도했으나, `high_profit_compound`의 대규모 가격 이력 집계가 장시간 진행되고 일부 전략만 새 기간으로 등록되는 부분 상태가 발생했다. 실행을 중지하고, 새로 선택된 5개 전략은 이전 suite로 복구했다. 현재 프런트/레지스트리의 표준 기간은 다시 `25.6~26.3`으로 일관된다.
- 후속 전수검증은 먼저 `high_profit_compound`의 최신기간 쿼리 성능을 개선하고, 전략별 checkpoint·원자적 선택 전환(26개 모두 성공한 뒤에만 registry 교체)을 갖춘 별도 러너로 실행한다. 중간 산출물이나 일부 최신 결과를 전략센터 순위에 노출하지 않는다.

### 2026-08-29 사용자 4개 후속지시 처리 — 분기데이터 재검토(클린 확인) + DART조회불가 종목 화면표시 신설 + 타 재무테이블 감사·부분정리 + financial_source_snapshot 백필 재시도(DART 자체장애 발견)
> 사용자: "1. 분기 데이터 다시 봐주세요 / 2. Dart 원문 조회자체가 안되는 종목들에는 국내종목 페이지 내에 꼭 표시를 해서 데이터에 문제가 있다고 표시 할 것 / 3. 다른 재무 테이블도 보세요 / 4. Financial Source 남은 872건, 미검증 136441건도 검증하세요"
- **①분기(is_annual=0) 데이터 재검토 — 클린 확인**: financial_data/cash_flow_data 둘 다 분기 데이터는 `(stock_code,year,quarter,report_type)` 정확일치 중복그룹 **0건**. 연간행과 달리 quarter 값이 항상 1~4 실값이라(모든 writer가 `{"11011":4,"11012":2,"11013":1,"11014":3}` 류의 동일 report_code→quarter 매핑을 씀) 애초에 관례 충돌 여지가 없었음. 분기 데이터는 이번 세션 버그 클래스의 영향 밖으로 확인.
- **②DART 원문 조회불가 종목 화면표시 신설**: [dart_data_quality.py](dart_data_quality.py) 신규 — `stock_dart_data_quality` 테이블(종목별 DART 재조회 성공/실패 연도 누적, status: ok/partial/no_dart_data)을 만들고, dedup/백필 스크립트들이 DART 재조회를 시도할 때마다 `record_dart_result()`로 결과를 누적 기록하도록 연동(`dedup_financial_data_annual.py`/`dedup_cash_flow_data_annual.py`/`scripts/backfill_unverified_snapshot.py`). [main.py](main.py) `/api/dashboard/fundamentals/{stock_code}` 응답에 `dart_data_quality`/`dart_data_quality_note` 필드 추가(기존 `shareholder_data_quality` 패턴과 동일 방식). [App.jsx](frontend/src/App.jsx) 종목 상세 페이지에 빨간색 "⚠ DART 원문 조회불가"/"⚠ DART 일부연도 조회불가" 경고 배지 추가(기존 유통주식수 배지 바로 아래, 동일 스타일). `npx vite build`로 빌드 검증 완료. **데이터는 스크립트들이 실제로 각 종목을 재조회할 때마다 점진적으로 채워짐 — 세션 종료 시점 기준 아직 대부분 종목이 미채움 상태(초기 0건, no_dart_data 케이스가 아직 실측되지 않음), 신규 기능 자체는 배포 완료.**
- **③다른 재무테이블 감사 — 서브에이전트로 23개 테이블 전수조사**(1차 세션한도 초과로 중단 → 재개 완료). 확정된 실제 버그:
  - **`canonical_financial_data`(8,687그룹)/`canonical_cashflow_data`(9,681그룹)**: 이전 세션이 만든 "최선의 1행 선정" 통합테이블이 정확히 같은 버그(quarter=0/4 리터럴로 먼저 GROUP BY 하는 `scripts/ops/rebuild_canonical_2022_2025.py:45-57` 근본원인)를 그대로 재현하고 있었음. 라이브 라우트는 안 씀(위험 낮음)이나 **`scripts/audit_all_page_data_quality.py`가 이 테이블을 "중복 grain이 이미 해소된 표준테이블"로 잘못 신뢰해 실제로는 안 풀린 이슈를 "해결됨"으로 오탐지 중**이었고, `bigquery_sync.py`가 매일 이 오염된 데이터를 BigQuery로 내보내고 있었음. **미수정(다음 세션 과제)** — financial_data/cash_flow_data 중복정리가 끝난 뒤 그 클린 소스로 canonical을 재빌드하는 것이 정공법으로 판단.
  - **`dart_insider_holdings`(16,544그룹)/`dart_major_holders`(11,098그룹)/`dart_employee_count`(1,582그룹)**: 실제 자연키(`rcept_no`+`repror` 등)에 대한 유효 UNIQUE 제약이 없거나(surrogate PK만 존재, 인덱스가 아예 `_nonunique`로 명명됨) NULL이 무력화해 재수집 스크립트 재실행마다 동일 공시가 그대로 중복 삽입되던 문제. **DART 재조회 불필요(이미 저장된 값들끼리 비교로 충분)** — `scratch/dedup_no_unique_tables.py` 신규, 그룹 내 값이 완전 동일하면 최신 1건만 남기고 삭제, 실제로 값이 다르면 자동삭제 안 하고 플래그만 남김. 결과: major_holders 11,098그룹 **100% 정리**(42,985행 삭제), employee_count 1,582그룹 중 1,573그룹 정리(9그룹은 진짜 값 차이로 보류), insider_holdings 16,544그룹 중 5,114그룹만 완전동일이라 정리(11,430그룹은 **같은 rcept_no+repror인데 값 자체가 다름** — 원인 미상, 자동삭제 안 하고 보류, 후속 조사 필요). 백업: `{table}_backup_dedup_20260829`.
  - **`us_financial_data`**: PK는 유효하나 `period_end`가 SEC XBRL 원본을 그대로 써서 동일 분기에 재수집마다 값이 미세하게 달라짐(1,566+412그룹). 미국주식 데이터라 "국내종목" 우선순위 밖으로 이번 세션 미수정.
  - 나머지 15개 테이블(`dart_backlog_quarterly`/`dart_cost_quarterly`/`dart_bs_items`/`naver_financial`/`order_backlog`/`dart_material_purchase` 등)은 quarter값이 항상 1~4 실값이거나 자연키가 rcept_no 기반이라 **클린** 확인, `dart_raw_accounts`(112행, dead)/`seibro_financial_snapshot`(0행, dead)은 낮은 우선순위로 기록만.
- **④financial_source_snapshot 872건/136,441건 백필 재시도 — DART 자체 장애로 중단**: 대량배치(`--limit 140000`) 실행 중 전건이 "status=800 시스템점검"으로 실패. 조사 결과 (a) `_fetch_dart_annual()`이 호출마다 매번 새 OpenDartReader를 생성하고 있었는데 이 라이브러리는 회사고유번호목록을 **날짜별 pickle 캐시**로 관리 — 자정(8/28→8/29) 직후라 오늘자 캐시가 없어 매 호출이 원격 재다운로드를 시도하던 비효율 발견·수정(클라이언트를 프로세스당 1회만 생성해 재사용하도록 `_get_dart_client()` 신설, [fnguide_financial_collector.py](collectors/fnguide_financial_collector.py)). (b) 수정 후에도 실패 지속 → 단독 재현 테스트로 **DART 서버 자체가 그 시각(새벽 1시경) corp_codes 다운로드 엔드포인트에 대해 실제로 점검 중**임을 확인(financial_data dedup은 자정 이전에 이미 연결을 맺어놓은 별도 프로세스라 영향 없이 계속 정상 진행됨). 외부 서비스 장애라 이번 세션에서 완료 못 함 — 코드수정은 반영 완료, DART 정상화 후 재시도 필요.
- **최종 상태(세션 종료 시점)**: financial_data 연간 중복 12,113→3,411그룹(계속 진행 중, 배경 프로세스), cash_flow_data(20,392그룹) 미착수, financial_source_snapshot 872+136,441건 DART 장애로 보류, canonical_* 2테이블 미수정, us_financial_data 미수정, dart_insider_holdings 11,430그룹(값 자체가 다른 원인불명 케이스) 미해결.
- **financial_data 연간 중복정리 최종 완료**: 12,113/12,113그룹 전량 정리(no_dart_data 0건, quota_hit 없이 완주).

### 2026-08-29(2차) 우선순위 전환 — "중복행보다 데이터 오류 자체가 더 큰 문제" (사용자 지시)
> 사용자: "중복행도 문제이지만 데이터 오류가 가장 문제야 / 오류/잘못 들어온 데이터 검증을 우선시 하고 중복은 오류 검증중에 확인하도록해 / Dart는 완료되었을 가능성이 높으니 재점검 해봐"
- **DART 재점검 결과**: 여전히 점검중(09:57 KST 확인, 01:08부터 9시간 가까이 지속 — 통상적 야간정기점검치고 이례적으로 김). 사용자 기대와 달리 아직 복구 안 됨을 정직하게 보고.
- **접근법 전환**: 기존 `dedup_*_annual.py`는 "이미 중복행이 있는 그룹만" 대상으로 했음 — 이는 단일행(중복 아님)인데 값 자체가 틀린 케이스를 전혀 검증하지 못하는 구조적 사각지대였음(사용자가 정확히 지적한 문제). **`verify_all_financial_data_annual.py`/`verify_all_cash_flow_data_annual.py`(scratchpad) 신규 — 중복 여부와 무관하게 모든 (stock_code,year,report_type) 연간 그룹을 DART 원문과 대조**, 값이 틀리면 수정(financial_fix_log/cashflow_fix_log 기록), 중복이 남아있으면 그 김에 정리(승자선정+삭제) — "중복정리는 오류검증의 부산물"로 재구성.
- **진행상황 추적 신설**: 라이브 `financial_data`/`cash_flow_data` 스키마는 건드리지 않고(ALTER TABLE이 락 경합으로 멈춰 킬하고 우회 — 운영 중인 uvicorn 프로세스와 충돌 가능성 때문에 라이브 테이블 스키마변경 자체를 회피) 별도 테이블 `financial_data_verify_progress`(table_name,stock_code,year,report_type,last_verified_at,last_result) 신설 — 한번도 검증 안 된 그룹 최우선(NULLS FIRST), 그 다음 가장 오래전에 검증된 순으로 처리해 상시 롤링 감사가 되도록 설계.
- **우선순위 체인 실행**: `scratch/wait_dart_and_run_priority_chain.sh` — DART 정상화 대기(2분 간격) → 복구되면 ①financial_data 연간 전건검증 → ②cash_flow_data 연간 전건검증 → ③financial_source_snapshot 백필 순서로 자동 순차실행(사용자 지시대로 데이터오류 검증 우선, 감사전용 테이블은 최후순위).
- **⚠️ 버그 발견·수정(1차 실행 중)**: `verify_all_*.py`의 while루프가 "그룹이 남아있으면(=거의 항상) 절대 빈 배치를 반환 안 함" 특성 때문에 한 바퀴를 다 돌고도 종료 못 하고 무한히 재순회 — financial_data만 30분+ 붙잡고 cash_flow_data/snapshot으로 못 넘어가던 것 발견. 시작시점 총 그룹수를 못박아 `processed >= TOTAL_GROUPS`가 되면 반드시 종료하도록 수정. (참고: 이 버그 상태에서도 실질적 피해는 없었음 — financial_data 31,306그룹 중 13,278건(42%)의 실제 값 오류를 이미 발견·수정한 뒤였고, 단지 다음 단계로 못 넘어갔을 뿐.)
- **2026-08-29(3차) 사용자 추가지시 반영**: "10년치의 데이터를 목표로해서 진행해줘" — `verify_all_financial_data_annual.py`/`verify_all_cash_flow_data_annual.py`에 `MIN_YEAR = 올해-9`(2017년~) 범위 필터 추가 + 같은 우선순위(미검증 우선) 내에서 `year DESC` 2차 정렬 추가(중단되더라도 최근 연도가 먼저 끝나도록). financial_data 2017년~ 대상 29,975그룹/cash_flow_data 38,572그룹으로 재정의 후 처음부터 재실행.
- **✅ 최종 완료(2017~2026, 10개년)**:
  - **financial_data**: 30,000건 처리, **수정 0건**(이미 앞선 세션의 fix가 durable하게 유지됨을 재확인), 정상 29,039건, DART데이터없음 961건. 잔여 중복그룹 **0개**.
  - **cash_flow_data**: 38,600건 처리, **수정 9,437건(24.4%!)** — 이번에 처음으로 "중복 아닌 단일행"까지 전수검증한 결과, 중복행 문제보다 오히려 큰 규모의 실제 값 오류가 발견됨(사용자가 정확히 예견한 그대로). 정상 22,479건, DART데이터없음 6,684건(17.3%). 잔여 중복그룹 20,392→**4개**(사실상 완전 해소).
  - **financial_source_snapshot 백필(③)은 DART 일일 실사용한도(status=020, 앞의 두 단계에서 68,600여 회 호출)를 소진해 오늘은 착수 못 함** — 쿼터 소진은 정상적인 상한 도달이지 버그 아님. 이미 등록된 매일 03:45 스케줄러 잡(`_loop_unverified_snapshot_backfill`)이 쿼터 리셋 후 자동으로 하루 1,000건씩 이어서 처리.
  - **체인 스크립트 사소한 실행오류**: step3 진입 시점에 일시적으로 `venv/bin/python3` 경로를 못 찾는 오류(원인 미상 — venv 자체는 정상 확인됨, 아마 장시간 실행 중 외부요인) 발생 → step1/2는 이미 정상 완료된 상태였으므로 step3만 단독 재실행(재시도 결과 DART 쿼터 소진으로 확인, 위 참고).
  - **DART없음(no_dart_data) 비율이 예상보다 높음(961+6,684=7,645건, 약 11%)** — `stock_dart_data_quality` 테이블에 누적 기록됨, 다음 세션에서 종목상세 페이지 배지로 실제로 얼마나 뜨는지 확인 필요.

### 2026-08-28 multi_source_financial_mismatch_log 잔여 145건 전수 재검토 완료 (income_statement/cash_flow/material_purchase_internal)
> 사용자: "그래서 완료된거야? 뭐야?" → "재검토 진행해. 너가 완료를 안하면 미완료 상태로 계속 남아. 멈추지 말고 무조건 수정을 완료해 / 해당 사항에 대해서는 기록해서 모든 ai가 적용하도록해" — DART 원문 재검증(anchor) 원칙에 따라 세 카테고리(income_statement 49건, cash_flow 28건, material_purchase_internal 68건, 총 145건) 잔여 mismatch를 전부 DART 원문 재조회로 재검증. cross-check 플래그를 무조건 정답으로도, 재추출 결과를 무조건 정답으로도 취급하지 않고 매 건 근거를 남김.
- **방법론 ①(income_statement/cash_flow)**: `dart_key_manager.RotatingOpenDartReader.finstate_all(stock_code, year, '11011', fs_div='CFS')`로 원문 재조회(빈 응답/status=013 시 `fs_div='OFS'`로 폴백) → `dart_collector.py`의 기존 계정매칭 파서 `_parse_fin_df`/`_parse_cf_df` 그대로 재사용해 재파싱 → 저장값과 2% 이내 오차면 `dart_confirmed`(원본 데이터 정상, 외부소스 쪽 기준차이), 다르면 재파싱값으로 `corrected`. 스크립트: `scratch/verify_income_cf_mismatches.py`, `scratch/apply_income_cf_corrections.py`(`RUN_ID=verify_remaining_mismatch_20260828`).
- **🔑 근본원인 발견 — income_statement/cash_flow "corrected" 건 상당수는 실제로는 파서버그가 아니라 중복행 문제**: 동일 (stock_code, year, report_type)에 대해 `financial_data`/`cash_flow_data`에 `created_at`이 동일한 중복 행이 존재하고(이미 정정된 최신값 행 + 정정 전 stale값 행 공존), crosscheck 스크립트의 `created_at DESC` 정렬이 동률 시 비결정적으로 stale 행을 읽고 있었던 것. 재현 불가한 크로스체크 랭킹 로직을 다시 만드는 대신, 로그에 남은 stale dart_value와 **정확히 일치하는 행**(실패 시 ±0.5% 근사)만 정밀 타겟팅해 수정 — 원인 자체(중복행 생성 경로)는 이번 세션에서 미조사, 후속 필요.
- **방법론 ②(material_purchase_internal)**: `dart_material_purchase_collector.download_and_extract(rcept_no, key, year)`로 원문 document.xml 재다운로드+재추출(XBRL 태그 → 키워드/IFRS주석 → 태그 스캔 3단계 폴백) 후 저장값과 비교. 재추출값=저장값(2%이내) → 매입재료비 자체는 정확, cost_structure 프록시(COGS 비교) 쪽의 구조적 한계로 판단해 `no_external_source`로 재분류(`mismatch` 아님). 재추출값≠저장값 → **`financial_data.revenue` 대비 비율로 타당성 재검증 후에만** `dart_material_purchase.material_purchase_krw` 수정. 스크립트: `scratch/verify_material_purchase.py`(RUN_ID=verify_material_purchase_20260828, `run_in_background`+`python3 -u`로 백그라운드 실행), `scratch/apply_mp_corrections.py`.
- **⚠️ 자동재추출 결과 중 명백한 오류 2건을 타당성 필터로 사전 차단(적용 안 함)**: `041520`(이엘씨) 재추출값이 매출대비 4.44배(기존 3.68배보다 오히려 악화), `064090`(인크레더블버즈) 재추출값이 매출대비 574배(파서가 무관한 큰 수치를 오매칭한 것으로 판단) — 둘 다 "재추출값도 신뢰 불가"로 판단해 **저장값 유지, `mismatch` 상태 그대로 note만 추가**(수동 원문 확인 필요). CLAUDE.md 상단 재무 무결성 규칙("DART 재검증 없이 자동보정 금지")과 "OPEN은 임의 확정 금지" 원칙을 이 필터로 실제 적용한 사례.
- **최종 결과**(`SELECT category,status,COUNT(*) FROM multi_source_financial_mismatch_log GROUP BY category,status`): income_statement `mismatch→0`(35 corrected + 14 dart_confirmed, 49/49 완료). cash_flow `mismatch 28→3`(22 corrected + 3 dart_confirmed; 잔여 3건은 001000/001720 depreciation, 001770 capex — DART 원문 자체가 해당 계정을 별도 항목으로 안 싣는 직접법 현금흐름표로 판단, 데이터 결손 아님, 정직하게 OPEN 유지). material_purchase_internal `mismatch 68→49`(14 corrected + 5 no_external_source 재분류; 나머지 49건은 재추출 실패 45건+rcept_no 없음 2건+타당성필터 차단 2건, 전부 정직하게 `mismatch` 유지, 임의 확정 안 함).
- **백업**: `financial_data_backup_mismatch_verify_20260828`, `cash_flow_data_backup_mismatch_verify_20260828`, `dart_material_purchase_backup_mismatch_verify_20260828`(각 UPDATE 전 대상 범위만 스코프 백업).
- **후속 미착수 항목(다음 세션 인계)**: cash_flow 3건(계정 자체 미기재 추정, 재도전해도 결과 동일할 가능성 높음), material_purchase_internal 45건 재추출실패(문서 형식이 현재 파서 3단계 전부와 안 맞는 케이스 — `download_and_extract` 자체의 4번째 폴백 패턴 추가가 필요할 수 있음), 041520/064090 2건(수동 원문 확인 필요), income_statement/cash_flow 중복행 생성 근본원인(파이프라인 어디서 중복 insert가 발생하는지 미조사) — **바로 아래 항목에서 근본원인 규명·부분수정함**.

### 2026-08-28(2차) ⚠️ financial_data/cash_flow_data 연간행 대량중복(annual quarter 값 3중 불일치) 근본원인 발견 + 수집코드 수정 + financial_source_snapshot 1,045건 중 안전한 범위 마무리
> 사용자: "데이터가 무결점이 아니라면 계속 검증 계획을 세우고 진행해줘" — 위 항목(1차)에서 다룬 `multi_source_financial_mismatch_log`(145건)와 별개로, FnGuide↔DART 교차검증 전용 테이블 `financial_source_snapshot`(150,261행)을 발견. 상태분포: `unverified` 136,441(91%!) / `verified` 10,823 / `mismatch` 1,045 / `reconstructed` 1,828 / `structural_diff` 108. 매일 수집분의 90%가 검증조차 안 되고 있었음.
- **1단계 원인**: `cross_validate_annual()`이 `api_limiter.wait("DART")` 실패(쿼터체크) 시 재시도/백필 없이 조용히 `unverified` 반환 — `unverified`의 `fetched_at`이 2026-05-10~08-22까지 매일 계속 쌓이고 있어(정적 잔재가 아니라 현재진행형 구조적 결함) 코드 원인은 확인했으나 이번 세션에서 수정하지 않음(범위상 다음 세션 과제로 명시 보류).
- **2단계 원인(더 심각, 오늘의 핵심 발견) — annual quarter 값 3중 불일치로 UNIQUE(stock_code,year,quarter,is_annual,report_type) 제약이 사실상 무력화됨**: `financial_data`/`cash_flow_data` 둘 다 연간(is_annual=1) 행에 quarter=`0`(FnGuide계열), `4`(DART계열, `collect_dart_financial_batch.py`/`collect_dart_cashflow_batch.py`가 의도적으로 사용 — `validate_eps_bps()`가 이 둘을 JOIN해 EPS/BPS 교차검증하는 **의도된 설계**), `NULL`(`scratch/legacy_dart_recollect.py`의 `update_annual_pl`/`update_cf_annual`이 신규 insert 시 사용 — **이것만 의도되지 않은 세 번째 관례**) 세 가지가 혼재. PostgreSQL UNIQUE 인덱스는 NULL을 서로 다른 값으로 취급하고, quarter=0/4/NULL은 애초에 다른 키라 제약 자체가 중복을 막지 못함. 실측: **financial_data 연간 중복그룹 12,113개, cash_flow_data 연간 중복그룹 20,392개**. `routes/*.py`의 financial_data 조회 대부분이 quarter/data_source를 구분하지 않고 조회해 어느 사본이 화면에 뜨는지 사실상 비결정적.
- **수정(근본원인 중 안전하게 확정 가능한 부분만)**: `scratch/legacy_dart_recollect.py`의 `update_annual_pl()`/`update_cf_annual()` 신규 INSERT 시 `quarter=NULL` → `quarter=4`로 변경(기존 행 탐색은 이미 quarter 무관 조회라 안전). **quarter=0(FnGuide) vs quarter=4(DART) 이원구조 자체는 `validate_eps_bps()`가 의도적으로 사용 중이라 건드리지 않음** — 이걸 섣불리 "하나로 통일"했으면 그 교차검증 기능이 깨졌을 것(사용자에게 재확인 후 축소 진행). 기존에 이미 쌓인 12,113+20,392개 중복행 자체는 오늘 정리하지 않음 — 대형 별도 프로젝트로 이관(다음 세션 과제).
- **financial_source_snapshot 1,045건 mismatch 처리**: DART 원문 재파싱(`dart_collector._parse_fin_df`)으로 재검증한 결과 `live_data_bug`(라이브 financial_data가 실제로 틀림) 880건 중 **865건(98%)이 위 중복행 문제와 얽혀있어** 임의 수정하면 잘못된 사본을 고칠 위험이 있음을 발견하고 적용 중단. 최종적으로 **중복 없이 안전하게 확정 가능한 15건만 수정**(43개 필드, `financial_fix_log` 기록, run_id=`verify_snapshot_mismatch_20260828`), `fnguide_only_diff`(라이브 정상, FnGuide만 다름) 158건은 상태만 `dart_reverified_ok`로 갱신(데이터 변경 없음), 나머지 865건(중복 얽힘)+7건(비교대상 없음)은 정직하게 note만 남기고 보류.
- **최종 financial_source_snapshot 상태**(GROUP BY): mismatch 1045→**872**, dart_reverified_ok 신규 158, confirmed 신규 15, unverified 136,441(변화없음, 다음 세션 과제).
- **다음 세션 인계 항목(우선순위 순)**: ① 12,113+20,392개 기존 중복행 정리(DART 원문 재검증 기반, 규모상 며칠간 DART 쿼터 분할 소요 예상 — 사용자가 "근본원인 코드수정 먼저, 기존 중복정리는 별도 대형작업으로" 명시적으로 선택함), ② `routes/*.py` 전수 감사 — financial_data 조회 시 quarter=4(DART)/report_type 명시 없이 그냥 SELECT하는 곳이 몇 곳인지, 실제 화면에 어느 사본이 노출되는지 확인, ③ `cross_validate_annual()`의 쿼터소진시 무재시도 문제(1단계 원인) 백필 잡 추가, ④ financial_source_snapshot `unverified` 136,441건 잔여, `mismatch` 872건 잔여. **①③은 아래 2026-08-28(3차)에서 진행/부분완료, ②는 감사 완료 후 실제 수정까지 완료.**

### 2026-08-28(4차) 가상매매 탭 정리(옛 병합조합 5개 숨김) + 전략센터상위5 500오류 해소 확인 + golden_cross "잔존" 마커 누락 잠재버그 발견·수정
> 사용자: "옛 탭은 삭제하고 실제 가동중인것만 표시해" + "내가 가상매매를 진행하라고 했는데 진행 안하는거야?" — 가상매매 탭 확인 중 3건 처리.
- **① `frontend/src/App.jsx` PeakView STRATEGIES에서 combo_605/539/510/474/546("기존 조합①~⑤") 5개 항목 제거**: 2026-07-31 이후 `scheduler.py _job_combo_daily`가 이 계좌들을 더 이상 건드리지 않고(전략센터 상위5로 완전 교체됨, 함수 이름만 옛 이름 유지) 갱신이 끊긴 옛 스냅샷을 계속 노출해 혼란을 주고 있었음 — DB의 `peak_holding`/`peak_trade` 기록은 그대로 보존, 화면 탭만 제거. `npm run build`(vite preview 정적서빙이라 재빌드 필수, HMR 무효) + 브라우저 실검증 완료, 콘솔 에러 0.
- **② `/api/trend/strategy-center/top-five` 500 오류는 이미 해소돼 있었음을 재확인**: 2026-08-26 다른 세션이 `STRATEGY_CENTER_PAPER_ENGINES`에 v2/v8을 추가해 실행가능 전략 3→5개(golden_cross/sector_focus/v2/contract_momentum/v8)로 복구 완료된 상태였음 — 실API 호출로 200 정상 확인.
- **🔴 ③ 신규 발견·수정 — `run_backtest_golden_cross`(backtest.py:6502)만 아직 보유 중인 포지션의 회계상 마감 사유로 `"잔존"`이라는 문자열을 쓰는데, `routes/trend.py`의 `_COMBO_PERIOD_END_MARKERS`(2026-07-23 도입, v10/v4/v2/earnings/moonshot/recovery의 각기 다른 마감사유 문자열을 걸러 "오늘 보유중인 모든 포지션이 매도신호로 오인"되는 걸 막는 필터)에는 `"잔존"`이 빠져 있었음**: golden_cross가 새로 STRATEGY_CENTER_PAPER_ENGINES에 편입되며 처음으로 이 파서를 타게 됐는데, sc_golden_cross 계좌가 아직 한 번도 매수를 못해(2026-08-24 이후 신규 진입 자체가 없음, 정상적인 "신호 없음" 상태) 지금까지는 실제 오매도로 이어지지 않았을 뿐 — 첫 매수가 체결되는 즉시 다음날 재실행에서 방금 산 종목 전부가 "매도신호"로 오인되어 하루 만에 되팔릴 잠재 버그였음(사전 발견). `_COMBO_PERIOD_END_MARKERS`에 `"잔존"` 추가. v2/v8(`_run_generic_backtest` 공용, `기간종료`류)·contract_momentum(`final`)·sector_focus(event-stream `FINAL`/`SECTOR_EXIT` action, `_combo_parse_trades`가 애초에 BUY/SELL만 인식해 무해하게 무시됨) 나머지 4개 엔진은 전수 확인 결과 이미 안전. `scripts/safe_restart_backend.sh`로 안전 재시작 후 golden_cross 최신 backtest_runs(`bc2398fe`)를 실제 파서에 통과시켜 검증(수정 전 오탐 매도 10건 → 수정 후 0건).

### 2026-08-28(3차) "모두 다 진행해" — 연간행 중복정리 백그라운드 실행 + routes/*.py 화면단 버그 5건 수정 + 미검증백로그 백필 자동화
> 사용자: "모두 다 진행해.. 재무 데이터라 매우 중요해" — (2차)에서 발견한 4개 후속 항목을 순서대로 진행.
- **③ financial_source_snapshot `unverified` 백필 자동화**: `scripts/backfill_unverified_snapshot.py` 신규 — FnGuide 원본은 이미 스냅샷에 저장돼 있으므로 재수집 없이 DART 쪽만 재시도(`cross_validate_annual` 재사용). 스케줄러에 `_loop_unverified_snapshot_backfill`/`_job_unverified_snapshot_backfill` 등록(매일 03:45, 03:15 FnGuideDART전종목검증 직후, limit=1000/day). 3건 실측 테스트로 정상 작동 확인(verified 1/mismatch 2). 근본원인(쿼터체크 실패시 무재시도) 자체는 미수정 — 이 백필이 사후 보완.
- **① financial_data/cash_flow_data 연간행 중복정리 실행**: `scratch/dedup_financial_data_annual.py`/`dedup_cash_flow_data_annual.py` 신규(DART 원문 재파싱 앵커, 그룹별 승자 선정 후 나머지 삭제+`financial_fix_log`/`cashflow_fix_log` 기록, 삭제행은 `financial_data_backup_dedup_20260828`/`cash_flow_data_backup_dedup_20260828`에 전체 백업 보존). 실행 중 **UNIQUE 제약 충돌 버그 발견·즉시 수정**(패자 행이 이미 quarter=4일 때 승자를 quarter=4로 먼저 바꾸면 충돌 — 패자 삭제를 승자 quarter 갱신보다 먼저 하도록 순서 수정, 000020/2020/CFS 실측으로 발견). financial_data 쪽 백그라운드 실행 중(세션 종료 시점 기준 12,113→10,642+ 그룹 진행 중, 완료까지 수 시간 예상, 쿼터 소진 시 자동 안전정지 후 재실행하면 이어서 진행되는 멱등 설계). cash_flow_data(20,392그룹)는 financial_data 완료 후 순차 착수 예정(같은 DART 쿼터를 두 프로세스가 동시에 나눠쓰는 것보다 순차가 안전).
- **② routes/*.py 감사 + 실제 수정**: Explore 서브에이전트로 `financial_data`/`cash_flow_data`를 quarter/report_type 구분 없이 읽는 지점 전수 조사(36개 routes 파일 + main.py). **실제 화면에 영향 주는 버그 5건 확정 수정**(모두 기존에 이미 검증된 안전패턴 — `main.py:6292` CASE 기반 tiebreak, `main.py:4525` ROW_NUMBER() OVER — 을 재사용):
  - [main.py](main.py) EPS/BPS(PER/PBR 카드, `/api/dashboard/fundamentals`): tiebreak 없어 동일연도 중복 시 PER/PBR이 요청마다 바뀔 수 있었음 → quarter=4/id DESC tiebreak 추가.
  - [routes/sector_rotation.py](routes/sector_rotation.py) 영업이익 YoY(섹터로테이션 리더십/톱픽 스크리너, 4곳): `ORDER BY` 자체가 아예 없어 완전 임의값이었음 → CFS/quarter=4/dart 우선 tiebreak 추가.
  - [routes/tenbagger.py](routes/tenbagger.py) 회복후보 스크리너(`/api/tenbagger/recovery-candidates`): 단순 `MAX(CASE...)` 집계라 중복행 중 **숫자가 더 큰 쪽이 항상 이기는 구조적 왜곡**(무작위 아님, 매번 같은 방향으로 편향) → `ROW_NUMBER() OVER (PARTITION BY stock_code,year ORDER BY ...)`로 그룹당 대표행 1개 선정 후 집계하도록 재작성.
  - [routes/order_contracts.py](routes/order_contracts.py) `_latest_annual_revenue`(수주잔고 스크리너/상세 3곳): report_type 구분조차 없었음 → tiebreak 추가.
  - [routes/market_radar.py](routes/market_radar.py) 반도체 재무이력(`/semiconductor/financial-history`): "연도당 첫 행 채택" dedup 로직이 있었으나 정렬 자체가 비결정적이라 그 "첫 행"이 매번 바뀔 수 있었음 → tiebreak 추가(financial_data/cash_flow_data 양쪽, 폴백 쿼리 포함 총 4곳).
  - 감사에서 이미 안전하다고 확인된 패턴도 문서화: `main.py:4525`(ROW_NUMBER 기반, 가장 견고), `main.py:6292`, `routes/dart_excel.py`(2곳) — 참고용, 수정 안 함.
  - 모든 수정된 쿼리는 실제 라이브 DB(005930/000660/035420 등)로 실행 검증 완료.
- **진행 중 요약**: 세션 종료 시점 기준 financial_data 중복정리 진행 중(완료 후 cash_flow_data 착수), routes 수정 5건 완료·검증, unverified 백필 자동화 완료(내일 새벽부터 매일 가동). 사용자에게는 매 단계 "이걸로 데이터가 완벽해지는 건 아니다"(분기데이터 미검토, DART재파싱 실패건 미해결, 다른 재무테이블 미감사, DART파서 자체의 잠재 버그 가능성, 살아있는 데이터라 상시관리 필요)를 명시적으로 반복 고지함 — 과장 보고 금지 원칙 준수.

### 2026-08-26 가상매매 페이지 진입 시 종목이 안 보이던 버그 — 원인 규명 및 프론트 수정
> 사용자: "가상매매 탭에서 매수/매도 종목이 보이지 않지?" — 실제로는 데이터 자체는 정상인데 페이지 첫 진입 시 어떤 탭도 자동 선택되지 않아 빈 화면처럼 보이던 프론트엔드 버그.
- **재현·원인**: 브라우저에서 "가상 매매" 탭 진입 직후 스크린샷은 "투입원금 0원 / 보유 종목이 없습니다"로 비어 보였으나, 네트워크 로그 확인 결과 `/api/trend/holdings`(319건, 오늘자 갱신분 포함)는 정상 200 응답 — 문제는 별도로 호출되는 `/api/trend/strategy-center/top-five`가 500 에러를 내고 있었던 것. `PeakView`의 `loadPeak()`가 이 호출이 성공(`scRes.ok`)할 때만 `setStrategy(selected[0]?.key || '')`로 기본 선택 탭을 정하는 구조라, 이 호출이 실패하면 `strategy` state가 초기값 `''`(빈 문자열)에 영원히 머물러 — 어떤 탭도 `strategy===key`를 만족 못해 `curHoldings`/`curExits` 필터가 항상 빈 배열이 되고, "기존 조합①"(combo_605) 같은 탭을 **직접 클릭**하면 즉시 실제 보유종목(삼성전자 +26.3%/SK하이닉스 +27.7%/롯데렌탈 +18.9% 등)이 정상 표시됨을 확인 — 데이터·필터 로직 자체는 멀쩡했다.
- **500의 근본원인(참고, 이번 세션 중 다른 세션이 해결)**: `_select_strategy_center_top_five()`(routes/trend.py)가 `STRATEGY_CENTER_PAPER_ENGINES`에 등록된 5개 전략(golden_cross/sector_focus/v5/v10/contract_momentum) 중 "퇴역(retired)"이 아닌 것이 정확히 5개여야 하도록 의도적으로 fail-closed 설계돼 있는데, 최근 재검증 세션에서 v5·v10이 둘 다 '퇴역' 등급으로 강등되며 후보가 3개로 줄어 매번 `RuntimeError: selected=3`로 500이 발생 중이었음. 조사 도중 다른 세션이 `STRATEGY_CENTER_PAPER_ENGINES`에 v2·v8을 추가해(현재 파일에 반영됨) 유효 후보가 5개(golden_cross/sector_focus/v2/contract_momentum/v8)로 복구되어 엔드포인트가 다시 200을 반환하는 것을 확인 — 이 백엔드 수정은 이번 세션이 직접 작성한 것이 아니며 검토도 하지 않았음(routes/trend.py는 이번 조사 중 2,400줄 넘게 동시수정 중이라 별도 세션의 진행중 작업으로 판단, 커밋 대상에서 제외).
- **프론트 수정(직접 반영)**: `frontend/src/App.jsx`의 `PeakView` — `const [strategy, setStrategy] = React.useState('')` → `React.useState('peak')`로 변경. 이제 top-five 호출이 다시 실패하더라도(예: 앞으로 또 어떤 이유로든 5개 미만이 되는 경우) 최소한 하드코딩된 첫 탭(Peak Easy)이 기본 선택되어 화면이 비어 보이는 일이 재발하지 않음 — top-five가 정상일 때는 기존처럼 그 결과가 기본 탭을 덮어씀(동작 변화 없음).
- **검증**: `npm run build` 성공 후 frontend preview(vite preview, 5173) 재기동 — 재기동이 필요했던 이유는 이 프로젝트의 프론트가 `vite dev`가 아니라 `npm run build && npm run preview`(정적 프리빌드) 방식으로 서빙되어 소스 수정이 HMR로 즉시 반영되지 않기 때문(재발방지: 프론트 소스 수정 후에는 반드시 재빌드+preview 재기동 필요, `start.sh`의 62~71번째 줄과 동일 절차). 브라우저 재검증: 새로고침 직후 "1. V12골든크로스 BT+33.6%" 탭이 자동 선택되어 정상 표시(top-five가 이미 복구된 상태라 sc_ 전략이 기본값이 됨), "기존 조합①" 클릭 시 여전히 실제 보유종목 3건 정상 표시 확인.

