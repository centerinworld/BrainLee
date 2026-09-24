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
- **financial_data/cash_flow_data 외 다른 테이블 보정은 `data_fix_log`(2026-09-05 신규, 범용)에 table_name/scope/row_count/fix_rule/old_value_summary/new_value_summary/source/run_id 기록** — 대량 UPDATE 하나당 1행(개별 row 단위 아님). 사용자 지시("데이터가 검증오류 고치는 작업을 통해 많이 변경되었는데 해당 사항을 주기적으로 기록") 반영. 2026-09-05 이전 이력은 CLAUDE.md archive/각 backup 테이블에만 있고 이 로그로 소급 이관하지 않음.

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
│   ├── asia_foreign_flow_collector.py # 한국+대만 외국인 순매수(USD환산) → global_macro_data ★신규(2026-09-08)
│   ├── india_fpi_flow_collector.py # 인도 NSDL FPI 주식 순투자 → global_macro_data ★신규(2026-09-08)
│   ├── mof_japan_flow_collector.py # 일본 MOF 공개 주간 CSV 직접수집(대내증권투자 주식 순매수) ★신규(2026-09-08), e-Stat→MOF CSV 전환(2026-09-09)
│   ├── tic_bilateral_flow_collector.py # 미 재무부 TIC 국가별 대미 주식 양자간 흐름(FRED 미러링, 국가/지역 확장 중) ★신규(2026-09-08)
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
└── frontend/src/App.jsx # React SPA 메인 (18,612줄) — 다수 컴포넌트가 frontend/src/views/*.jsx로 분리됨. 섹션 6 참조
```

---

## 2. DB 스키마 및 저장 위치

### DB/저장 경로 강제 규칙 (2026-09-04)

> **운영 기준 DB는 PostgreSQL이다. 모든 운영 조회, 백테스트, 전략 검증, run registry 및
> `signal_experiment_ledger` 기록은 `.env`의 `POSTGRES_DATABASE_URL`을 그대로 사용한다.**

- 기본 연결은 반드시 `config.DATABASE_URL` + `db_compat.connect_primary_db()`를 사용한다. 백테스트 명령에서 `POSTGRES_DATABASE_URL`이나 `DATABASE_URL`을 `sqlite:`로 덮어써 PostgreSQL 라우팅을 우회하지 않는다.
- PostgreSQL 데이터 디렉터리와 프로젝트 데이터·캐시·연구 산출물은 모두 외장 SSD의 `/Volumes/Realtek_NVME/stock_dashboard/` 아래에 둔다.
- `/Volumes/Realtek_NVME/stock_dashboard/runtime/`는 레거시 실행 경로 또는 심볼릭 링크일 뿐이다. DB 경로, 출력 경로, 명령 인자, 코드 상수에 사용하지 않는다. 같은 파일을 가리키더라도 반드시 `/Volumes/Realtek_NVME/stock_dashboard/...` 정규 경로를 사용한다.
- `stock.db`는 레거시/오프라인 호환용 SQLite 스냅샷이다. 운영 성과 판정, 전략 채택·기각, 원장 기록의 근거로 사용하지 않는다. 독립 SQLite side DB가 꼭 필요한 경우에도 저장 위치는 외장 SSD 아래로 제한하고 용도를 명시한다.
- 운영 PostgreSQL 접속 실패 시 SQLite로 자동 또는 수동 폴백해 검증을 계속하지 않는다. 실패 원인을 해결한 뒤 PostgreSQL에서 처음부터 재실행한다.

### 주요 테이블

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
# 운영 주 DB 표준: .env의 PostgreSQL 연결을 유지
from db_compat import connect_primary_db
conn = connect_primary_db(timeout=30)

# SQLAlchemy (ORM 필요 시)
from database import get_db
db: Session = Depends(get_db)
```

`sqlite3.connect("stock.db")` 직접 연결은 독립 SQLite 도구 또는 명시적 레거시 점검 외에는 금지한다.
⚠️ 위 규칙과 별개로, 남아있는 legacy SQLite 도구도 `DB_PATH = "stock.db"`(상대경로) 직접 선언은 금지 — cwd가 어긋나면 빈 stray DB가 그 자리에 생성됨(2026-09-12 사고, 섹션 9 참조). 반드시 `from db_utils import STOCK_DB_PATH as DB_PATH`(절대경로) 사용.

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
- **⛔ 병합조합 가상매매(2026-07-23 신규, 2026-08-23 이후 사실상 중단)**: 전략센터 "전략 조합" 탭에서 `persist_merged_run`으로 등록된 4개 검증조합(605.05%/539.18%/510.12%/473.87%)을 각각 독립 1억원 가상계좌(`combo_605`/`combo_539`/`combo_510`/`combo_474`)로 실행하던 구조. `scheduler.py`의 `_loop_combo_daily`/`_job_combo_daily`는 스레드 이름은 유지한 채 내부 구현이 아래 "전략센터 상위5 가상매매"로 교체됐고("과거 고정 병합조합은 더 이상 스케줄하지 않으며" — 코드 주석), 이 4개 combo_* 계좌는 **2026-08-23 09:37 마지막 실행 이후 한 번도 재실행되지 않아 포지션이 그대로 방치돼 있다**(2026-09-07 확인: 시세만 오늘 기준으로 평가돼 -2.9%~+8.82%로 보이지만 살아있는 검증이 아님). 이 전환이 언제·왜 결정됐는지, combo_* 4개 계좌를 정리(청산/보존)할지는 아직 미정 — `/api/backtest/combinations/list`의 4개 등록조합 자체도 2026-09-07 재검토 결과 605.05%/671.8%/601.0%×2가 동점 타이브레이크 안정성 미검증 상태였음(`research_outputs/merged_account_tiebreak_review_20260907.md` 참조).
- **전략센터 상위5 가상매매(2026-08-26 신규, 위 병합조합 대체)**: 고정 조합 대신 전략센터 매트릭스에서 그날 상위 5(현재는 sc_golden_cross/sc_sector_focus/sc_v2/sc_v5/sc_v8/sc_v10/sc_contract_momentum 중 상황에 따라 선정)를 매일 재선정해 각각 독립 1억원 가상계좌로 실행(`routes/trend.py execute_strategy_center_top_five_now`/`_execute_strategy_center_paper`, `STRATEGY_CENTER_PAPER_ENGINES`). 매일 18:35 `_loop_combo_daily`(스레드명 유지) 자동 실행.
  ⚠️ **2026-09-07 발견·수정: 6개 계좌 전부 설정 이후(8/26~28) 3주 가까이 매수 0건이던 근본 원인 2가지, 모두 리스크게이트 실행계층 버그(전략 신호 로직은 정상이었음)**:
  1. `routes/trend.py _execute_strategy_center_paper`: 고정 매수단위(`STRATEGY_CENTER_PAPER_TICKET_KRW`=1천만원)가 `_gate_volatility_sizing`의 종목당 리스크한도(자본×1.2%÷가정손절20%=자본의 6%, 1억원 계좌 기준 600만원)를 항상 구조적으로 초과 → 게이트 판정이 매번 최선이어도 `SIZE_REDUCED`(전액매수 불가·축소는 가능)인데, 실행 루프가 정확히 `BUY_ALLOWED`만 받아들여 SIZE_REDUCED를 매수거부로 취급하고 있었음. 게이트가 제시한 한도로 수량을 줄여 재검증하도록 수정.
  2. `routes/kis_trading.py _gate_sector_concentration`: KOSPI/KOSDAQ의 절반(2,686/5,379종목)이 `stock_universe.sector_large` NULL → "섹터 판단불가"가 뜨는데, `evaluate_risk_gates(strict_for_execution=True)`는 판단불가 게이트가 하나라도 있으면 BUY_ALLOWED를 WAIT_CONFIRM으로 강등하는 정책이라 이것만으로도 매수가 막혔음. 안전정책은 그대로 두고, 이미 DB에 있는 `stockeasy_sector_membership`(94% 커버)을 폴백으로 추가.
  검증: golden_cross 2026-09-02 신호(8종목) 재실행 시 수정 전 8/8 거부 → 수정 후 4/8 BUY_ALLOWED(나머지 4개는 수급이탈·갭위험 등 진짜 리스크 사유로 정당하게 차단 — 안전정책 우회 아님). `scripts/safe_restart_backend.sh`로 반영 완료.
  ⚠️ **v8 별도 원인(2026-09-07 수정)**: 위 게이트 버그와 무관하게 `hs_trade_lab.db`의 `trade_series_cache`(수출YoY 집계)가 2026-03에서 멈춰있었음 — 원본 `customs_monthly_record`는 실제로 2026-07까지 있었는데 집계 스크립트(`hs_trade_lab/scripts/backfill_trade_series_cache.py`)가 재실행 안 됨. `_date_to_ym`의 2개월 지연 설계와 겹쳐 6월부터 v8 신호가 전부 죽었던 것 — 재실행(멱등, `ON CONFLICT...DO UPDATE`)으로 2026-07까지 갱신, v8 재검증 결과 8월 거래 재개 확인.
  ⚠️ **HS 무역통계 다중매핑 중복계상 버그(2026-09-07 발견, 09-07 중 2차례 수정, 사용자 질문/피드백으로 발견)**: 사용자 지적("유니드는 독점이지만 필러는 성남에 여러 업체") 확인 결과, `hs_code_company_map`은 HS코드 313개 중 193개(62%)가 2개 이상 기업에 매핑돼 있음(최대 33개사 — 반도체 웨이퍼장비 HS '848620'). 기업별 실제 점유율을 담는 `market_share_pct` 컬럼이 존재하지만 941건 전부 미입력 상태였는데도, `backtest_common.py _load_trade_signals()`는 매핑된 각 기업에게 해당 HS코드 수출액 "전액"을 그대로 부여하고 있었음(예: 필러 HS '300190' 5개사 전부에게 같은 총액을 중복 부여) — 실제 수출 규모가 과다계상돼 v8의 수출YoY 신호가 왜곡돼 있었음.
  1차 수정(매핑 기업 수로 균등분할)에 대해 사용자가 "균등 분할도 위험하다 — 33개사가 똑같이 1/33씩 수출하는 게 아니다"고 재지적. 2차 수정: `_load_company_revenue_map()` 신설, HS그룹 내 기업들의 **최근 연간 매출액(`financial_data.revenue`, CFS 우선 최신연도 1건) 비례**로 가중치 산정 — 매핑 대상 398개사 중 384개(96.5%)에서 매출 데이터 확인. 매출 데이터 없는 개별 기업은 같은 그룹 내 매출 확인된 기업들의 평균값으로 대체(imputation)해 그룹 가중치 합이 항상 1이 되도록 하고, 그룹 전체에 매출 데이터가 없는 예외적 경우만 기존 균등분할로 폴백. 기업 "전체" 매출 비중이지 해당 HS 품목만의 매출 비중은 아니므로 여전히 근사치이나, 균등분할보다 기업 규모 차이를 반영하는 훨씬 나은 proxy. market_share_pct가 채워지면 항상 최우선.
  이 작업 중 `financial_data`에서 **CFS/OFS 중복 미제거로 매출액이 실제로 두 배로 잡히는 것**을 재확인(예: 메디톡스 2025년 revenue가 CFS 247,290,289,368 / OFS 222,046,566,421 두 행 다 IN절 조회에 걸림) — se_momentum.py에서 발견된 것과 동일 부류의 버그. `_load_company_revenue_map()`은 `ORDER BY stock_code, year DESC, CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END` + 종목당 1건만 채택하는 방식으로 dedup 처리. 같은 부류 버그가 다른 곳에도 더 있는지 서브에이전트로 전수 감사(아래 "CFS/OFS 미탈리브레이크 감사" 항목 참조) — 6곳 추가 발견·미수정(감사만, 수정은 별도 승인 필요).
  ⚠️ **3차 수정(2026-09-08, 사용자 재지적 — "매출비례도 좋지만 상관계수 등도 고려", "균둥분할 같은 게 다른 곳에도 더 있을것")**: 두 건 추가 발견·수정.
  (a) **조인 불일치로 인한 무응답 버그(더 찾기 어려운 유형 — "틀린 값"이 아니라 에러 없이 조용히 신호가 0으로 비어있는 형태)**: `hs_code_company_map`이 hs_code를 4/6/10자리 뒤섞어 저장하는데 `trade_series_cache`는 관세청 원본대로 10자리로만 존재 — 문자열 완전일치 조인이라 4/6자리 축약 항목(69건)은 절대 매칭 불가, 10자리 항목 중 35건도 세번코드 뒷자리 표기가 실제 관세데이터와 미세하게 달라(예: map '2849201000' vs 실제 '2849200000') 매칭 실패. 매핑 313개 HS코드 중 113개(36%)가 이 문제로 단 한 번도 매칭 안 됐고, 그 결과 S-Oil/SK이노베이션/DB하이텍/LX세미콘/티씨케이 등 398개사 중 **97개사(24%)가 애초에 무역 신호를 영구히 못 받고 있었음**. 확인해보니 113개 전부 앞 6자리(HS 6단위, 국제표준)는 원본 데이터에 존재 — 데이터가 없는 게 아니라 표기 정밀도 문제였음. 조치: ① `hs_trade_lab/scripts/backfill_trade_series_cache.py --all-hs` 재실행으로 원본 관세데이터 전체(10자리 125만행 — 기존엔 hs_code_company_map과 정확히 일치하는 코드만 22,123건 담고 있었고, 그마저 `mapping_status='confirmed'` 필터가 실제로는 한 번도 쓰인 적 없는 값이라 무의미했으며 'provisional' 103건까지 암묵적으로 배제되고 있었음)를 캐시에 반영, ② `_load_trade_signals()` 조인을 "hs_code 완전일치" → "6자리 접두사 일치(4자리 map항목은 4자리 접두사)"로 변경. 검증: 위 97개사 전부 신호 복원 확인.
  (b) **상관계수 필터 추가(재설계)**: 최초엔 상관계수를 매출비례 가중치에 곱하는 "완만한 confidence"로 넣었으나, 사용자가 "수출이 늘어도 실제 매출로 이어지는 기업도 있고 아닌 기업도 있으니 상관계수가 충분히 높은 기업만 적용하고 관련 없는 기업엔 아예 적용하지 말라"고 재지적 — 가중치를 깎는 방식에서 **완전 제외(하드 필터)** 방식으로 재설계. `_load_company_quarterly_revenue_yoy()`(CFS우선 dedup)로 각사 분기매출YoY를 구해 그룹 수출YoY와의 상관계수를 계산(`_MIN_CORR_SAMPLES`=8분기 미만이면 "관련없다는 증거 부족"으로 중립 포함), `_MIN_CORR`=0.2 이하인 기업은 해당 HS그룹 신호에서 완전히 제외하고 생존 기업끼리만 매출비례로 재분배. 실측: 필러그룹(HS '300190') 6개사 중 휴젤(corr 0.47)·바이오플러스(0.17)·069620(0.07, 그룹에 새로 포착된 대형사)만 생존, 메디톡스(-0.08)·휴메딕스(-0.01)·파마리서치(-0.07)는 이 그룹에서 제외(단, 파마리서치는 다른 매핑 HS코드에서는 여전히 신호 보유). 전체 효과: 398개 매핑기업 중 신호를 받는 기업이 193개로 감소(상관계수 낮은 기업은 의도적으로 신호 없음 — 버그 아니라 설계). v8 두 구간(24.06_25.05, 22.11_23.10) 재실행 크래시 없음 확인, 콤보 재검증은 미완료.
  ⚠️ **sector_focus 추가 버그(2026-09-07 수정)**: 위와 별개로 `backtest_strategies/sector.py`의 영업이익 YoY 스코어 컴포넌트(최대 25점)가 `cur_yr`을 거래일의 달력연도로 고정해서, 그 해 사업보고서가 아직 공시 전인 연중 대부분(1~11월) 항상 0점 처리되고 있었음 — 실제 공시된 최신 연도를 찾도록 수정, 6기간 재검증 avg6 23.80%→29.53%(5/6기간 양수)로 개선 확인(sector_focus+v2 콤보 성과도 같이 개선될 전망, 콤보 재검증은 미완료).
  ⚠️ **4차 수정(2026-09-08, 사용자 지적 — "DDR메모리는 삼성전자·하이닉스 2개사가 다인데 어떻게 처리했어?" + "매핑 비율이 너무 낮은데 개선 방법 없나")**: 두 축으로 작업.
  (a) **상관계수 필터의 실제 결함 발견·수정**: DDR메모리(HS '854232')를 실측한 결과, SK하이닉스(corr=0.11)가 상관계수 필터에 걸려 제외되고 군소 팹리스 제주반도체(corr=0.321, 매출 3천억대)가 살아남는 역설을 확인 — 대기업은 전사매출에 다른 사업(낸드/파운드리 등)이 섞여 좁은 HS카테고리 하나와의 상관계수가 오히려 희석되는 구조적 약점이었음. `hs_company_market_share`(애널리스트 실측 점유율 12건, 삼성/하이닉스 등)를 `hs_code_company_map.market_share_pct`에 반영(누락된 쌍 1건은 신규 INSERT)하고, 가중치 로직을 **"그룹 전원이 점유율값을 가져야 적용"(all-or-nothing, 사실상 사문화돼 있었음) → "점유율값이 있는 회사부터 개별 우선 적용, 나머지는 잔여비중(1-알려진점유율합)만 상관계수필터+매출비례로 배분"**으로 재설계 — 검증 결과 삼성(58%)+하이닉스(42%)=100%로 이미 꽉 차 나머지 3개사(제주반도체/해성디에스/한미반도체[장비사])는 이 그룹에서 자동으로 0이 되어 실제 시장구조와 일치하게 됨.
  (b) **커버리지 확장(398→404개사, 7.3%→7.4%)**: `hs_company_market_share`(12건 백필), `regional_company_mapping_evidence`(evidence_score≥0.71 또는 [≥0.65 AND post_count≥2] 기준으로 84쌍 승격 — 명백히 깨진 지역데이터[예: "경기도 기장군"(기장군은 부산 소재), "경상남도 성북구"(성북구는 서울 소재)]는 자동으로 임계값 미달 처리돼 배제됨, 단 이 소스는 기존에 이미 알려진 398개사의 HS코드 커버리지만 넓혔고 신규 기업은 0개), `segment_revenue`(DART 사업부문별 매출, 2,561개사 — 커버리지 확장의 진짜 지렛대가 될 것으로 기대) 세 소스를 검토. segment_revenue는 세그먼트명↔HS설명 키워드 자동매칭을 시도했으나 **한국어 사업분야 용어의 동음이의/과잉일반화로 실측 오탐률 약 50%** 확인(예: KCC의 "실리콘" 사업부문이 반도체용 실리콘 웨이퍼로 오매칭됐지만 KCC는 실리콘[규소]이 아닌 실리콘[폴리머 밀폐제] 화학회사; 파크시스템스의 "산업용 자동화 원자현미경" HS설명에서 "자동화"만 추출돼 POSCO홀딩스/현대엘리베이터/뉴로메카 등 무관 8개사에 오매칭; 한화의 "가성소다(USD/톤)" 세그먼트는 매출이 아니라 톤당 가격 참조행[revenue=4.15]이었음) — 자동화 매칭은 폐기하고 33건 후보를 전부 수동 검토해 11쌍(6개 신규기업: 종근당홀딩스/코아스템켐온/한미사이언스/한미약품/대한유화/이수화학, +송원산업/SNT에너지/DN오토모티브/세방전지/LS의 신규 HS링크)만 provisional로 커밋. 결론: 세그먼트명 기반 완전자동매칭은 정밀도가 낮아 대량 확장에는 부적합 — 유의미한 커버리지 확대는 (i) 수동/반자동 검토를 곁들인 점진적 확장이거나 (ii) 향후 LLM 기반 의미매칭(단순 부분문자열이 아닌 문맥 판단)이 필요, 미해결 과제로 남김. v8 재검증 크래시 없음 확인.
  ⚠️ **5차 수정(2026-09-08, 사용자 지시 — "dart는 후행, hs code는 선행/실시간", "상관계수를 전수조사해", "그 회사의 매출규모가 반드시 고려돼야", "hs 월별실적과 dart 분기실적이 고려된 정보여야")**: 상관계수 필터 자체의 구조적 결함을 전수조사로 확인·재설계.
  (a) **월별-분기 주기 불일치 수정**: 기존 상관계수 계산이 DART 분기매출YoY(3개월 누적)를, HS 수출데이터는 분기말 딱 한 달(3/6/9/12월)의 월별YoY만 뽑아 비교하고 있었음(주기 불일치로 왜곡). `_quarterly_export_yoy_series()` 신설 — HS 월별 수출액을 분기(1~3월=Q1 등, 3개월 전부 있는 완전한 분기만) 합산 후 YoY 계산해 DART와 동일 주기로 정렬. 단 실제 v8 매매신호(`_get_export_yoy`)는 손대지 않음 — 그건 월별 그대로 유지해야 "선행지표"로서의 존재 이유(HS가 DART보다 빠르다는 점)가 유지됨.
  (b) **전수조사로 상관계수 필터의 구조적 편향 발견**: 2개사 이상 매핑된 모든 HS그룹×기업 쌍(722건) 상관계수를 계산한 결과 — corr 분포가 mean=0.107/median=0.085로 애초에 약함에도, 기존 "corr>0.2 이상만 포함"(대칭적 진입장벽) 기준을 쓰면 **80곳 이상의 그룹에서 그 그룹의 명백한 실제 1위 사업자(삼성전자/현대차/LG화학/POSCO홀딩스/삼성바이오로직스/SK이노베이션/현대제철/대한항공/LG전자/롯데칠성 등)가 부당하게 제외되고, 대신 매출 규모가 수백억원대에 불과한 군소기업이 짧은 표본(28~40분기)의 통계적 잡음만으로 살아남는 역전 현상**이 광범위하게 확인됨 — 대기업일수록 전사매출에 여러 사업이 섞여 좁은 HS카테고리 하나와의 상관계수가 구조적으로 희석되는 게 원인. DDR메모리 사례는 이 문제의 극히 일부였을 뿐, 전사적으로 퍼져있던 구조적 결함이었음.
  (c) **재설계**: 상관계수를 "진입하려면 넘어야 하는 대칭적 문턱"에서 **"이미 매핑된 근거를 뒤집을 만큼 뚜렷한 반증(corr<-0.3)이 있을 때만 배제하는 비대칭 필터"**로 전환 — 기본은 포함(매출비례 가중치가 규모를 자연스럽게 반영하도록 맡김). 재검증 결과 대기업 오제외 사례가 80여건→5건(대상홀딩스/서흥/삼성SDI/현대제철/LS ELECTRIC — corr -0.33~-0.48의 뚜렷한 역상관, 정당한 제외로 판단)으로 감소. DDR 검증: 제주반도체/해성디에스/한미반도체가 '854232' 그룹에서는 market_share_pct 우선 로직으로 여전히 0을 받고, 이들이 매핑된 *다른* HS그룹에서 받는 신호를 전부 합산해도 삼성전자 대비 0.01~0.16% 수준(1% 미만) — 사용자 지적(점 6) 그대로 정량 확인됨.
  ⚠️ **점 5(지역정보 교집합) 조사 결과 — 인프라 부재로 보류**: `customs_monthly_record`에 시도(광역단체)별 수출데이터가 있는지 확인한 결과, 실제 시도×품목 교차 데이터를 담은 `sidoitemtrade` 엔드포인트(18,048건)가 존재하긴 하나 hs_code 필드값이 `'0000000001'`류의 알 수 없는 플레이스홀더로 깨져 있고 period_ym도 `'2016'`/`'총계'` 등 비정상값이 섞여 있어 **우리가 추적 중인 313개 HS코드와 교집합이 0건** — 사실상 미가공/방치 상태 데이터로 확인됨. `regional_company_mapping_evidence`(텔레그램 근거 기반 스냅샷, 499건)는 이미 활용 중이나 이건 시계열이 아니라 단발 증거이며, 시군구 단위의 체계적 월별 품목별 무역통계는 관세청 공개데이터 자체에 원래 없음(시도 단위까지만 존재). 결론: 이 아이디어를 제대로 구현하려면 `sidoitemtrade` 원본을 관세청에서 올바른 HS코드 라벨로 재수집하는 별도 작업이 선행돼야 함 — 미해결 과제로 남김.
  ⚠️ **알파벳 섞인 종목코드 시세공백(2026-09-07 재조사·수정)**: 앞선 조사에서 "923개, 어느 수집기도 담당 안 함"으로 잘못 결론지었던 것을 사용자가 정정("최근 상장 종목이다, 무시하면 안 됨"). 재확인 결과 실제 stock_universe 내 알파벳 포함 코드는 81개(예: `0007J0`=인벤테라, `0011A0`=액스비스, `0126Z0`=삼성에피스홀딩스[시총 8.9조] — 신규상장 스팩·일반주 및 우선주), KIS API로 직접 조회 시 오늘(2026-09-07)까지 정상 시세 확인 — 실존하는 활성 상장종목이 맞았음. 원인은 `collect_kis_ohlcv.py`의 전종목 대상 쿼리가 원래 `GLOB '[0-9]*'`(숫자로 시작하는 6자리 — 알파벳코드 포함)였는데, 2026-09-07 진행 중이던 Postgres 마이그레이션 작업 중 `GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'`(순수 숫자 6자리만)로 조용히 강화되어 있었음(미커밋 상태, 그날 밤 정기수집부터 반영될 뻔함) — 원래 필터로 되돌림(3곳: 종목선정 쿼리 + 검증로그 쿼리 2곳). 2026-06-05~09-07 구간 81개 종목 전체 즉시 백필 완료(5,192건, 에러 0). 다만 이 되돌린 필터가 적용되기 전, 즉 6월 5일부터 이 되돌리기 전까지 왜 정지돼 있었는지(그 시점엔 아직 이 강화된 필터가 존재하지 않았음)는 로그 부족으로 완전히 특정 못함 — 2026-09-08 이후 정기수집(18:35)이 계속 정상 작동하는지 확인 필요.
  ⚠️ **옛 병합조합 4계좌(combo_605/539/510/474) — 사용자 결정: 동결보전**(2026-09-07). 청산하지 않고 현재 포지션 그대로 유지, 추가 리밸런싱·정리 작업 불필요.
  ⚠️ **6차: LLM 기반 HS매핑 커버리지 확장 파이프라인(2026-09-08, 사용자 지시 — "애널리스트 리포트나 DART 원문 비교 등 다양한 방법으로, OpenAI 대신 DeepSeek으로")**: 커버리지 398→404(세그먼트명 수동매칭)에서 **478개사(전체 5,459개사의 8.8%)**로 추가 확장.
  - **신규 인프라**(`hs_trade_lab/scripts/`): `llm_hs_matcher.py`(공용 매칭 코어 — `config.get_ai_client()`로 DeepSeek 사용, hs_codes 324개 카테고리 전체를 컨텍스트로 제공해 목록 밖 카테고리 환각 방지), `build_hs_candidates_from_analyst_pdfs.py`(애널리스트 PDF `report_files` 23,727건/1,456개사에서 사업설명 추출), `build_hs_candidates_from_dart_filings.py`(DART Open API document.xml로 사업보고서 "사업의 내용" 원문 추출, segment_revenue 2,561개사 중 미매핑 기업 대상), `promote_llm_hs_candidates.py`(안전기준 통과분만 승격).
  - **파일럿에서 발견·수정한 버그들**(전부 실측 검증): (1) `report_files.stock_name`이 파일명 파싱 잔여물로 깨져있어(예: "오킨스전자［］ Hyundai+Moto") LLM에 그대로 넘기면 자기검열로 매칭이 0건까지 떨어짐 — `stock_universe` 정식 종목명으로 교체. (2) 증권사 발간 리포트의 stock_code 라벨이 실제 분석대상이 아닌 발행 증권사로 잘못 붙는 사례(상상인증권 파일이 실제로는 삼성SDI 분석) 확인. (3) **지주/그룹 대표종목의 자회사 실적 자기귀속** 대량 확인 — 롯데지주/SK스퀘어/한화/SK/GS/효성/에코프로/CJ/대웅 등이 종목명에 "지주"가 없어도(SK스퀘어, 한화, GS, SK, 효성처럼) 사업보고서/리포트 원문이 그룹 전체를 서술하다 보니 자회사(SK하이닉스, 한화에어로스페이스, GS칼텍스, 효성티앤씨 등) 실적이 모회사 종목코드로 매칭됨 — `mentions_subsidiary()`(회사명+추가글자 패턴, "자회사"/"계열사" 키워드) 신설로 검출. (4) LLM이 reason에 "정확히 일치하는 코드가 없어 억지로 끼워맞춤"을 스스로 인정하고도 confidence 0.8~0.9를 부여한 사례(씨엠티엑스: 실리콘 부품을 "실리콘카바이드"로 근사매칭) 및 reason에 "매칭하지 않음"이라고 명시하고도 matches 배열에 포함시킨 자기모순 사례(서울반도체 LED, 오리온 라면류)까지 확인 — `has_hedge_language()` 회피표현 필터 신설.
  - **최종 안전장치 3중**: `is_sector_plausible`(증권/은행/보험/지주/홀딩스 종목명·섹터 배제) + `mentions_subsidiary`(자회사 귀속 의심 배제) + `has_hedge_language`(강제매칭/자기모순 배제) + confidence≥0.8. 800개사 파일럿(analyst_pdf 400 + dart_filing 200 + 초기 테스트 200)에서 최종 127건(74개사)만 승격 통과 — 무작위 15건 재검토 결과 전부 타당, provisional 상태로 반영.
  - **미해결**: 코드베이스에 "지주회사 여부" 플래그가 없어 `mentions_subsidiary`의 이름패턴 방식은 완전 일반화가 안 됨(예: KG케미칼↔KG스틸처럼 형제회사 간 이름이 겹치지 않는 경우는 못 잡음) — 이런 잔여 사례는 provisional 상태이므로 후속 리뷰에서 걸러짐. `analyst_pdf_extracts`(기존 OpenAI/Gemini 기반, 목표주가 추출용, 비용 문제로 비활성)와는 별개 파이프라인.
  ⚠️ **CFS/OFS 미탈리브레이크 전수감사 및 수정 완료(2026-09-08)**: HS매핑 버그 조사 중 발견한 "financial_data에 report_type 타이브레이크 없이 SUM/AVG/positional-index하면 CFS+OFS 이중계상·비결정성" 부류 버그를 서브에이전트로 전수감사, se_momentum/megatrend/peak_easy/`_load_company_revenue_map` 외에 8곳 더 확인 — 사용자 지시("남기지말고 모두 고쳐")로 전부 기존 확립 패턴(`ROW_NUMBER() OVER(...ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END)` 서브쿼리로 종목·연도·분기당 1행만 남긴 뒤 집계)으로 수정 완료, 전 파일 컴파일+실제 쿼리/백테스트 재실행 검증 완료:
  1. `routes/market_radar.py`(3곳: 반도체 밸류스트림 TTM매출 2곳 + financial-detail TTM 1곳) — 종목 000020 TTM매출 445.3B(버그) → 513.2B(수정 후, 검증됨)로 13% 상향.
  2. `routes/tenbagger.py`(`/custom-filter` 재무 JOIN 2곳 + `_quarterly_rows`) — 종목 226340 2026Q1 영업이익이 CFS -2.09B/OFS +2.33B 사이에서 비결정적으로 뒤집히던 것을 CFS -2.09B(적자)로 고정 확인.
  3. `routes/sector_rotation.py`(`_get_sector_earnings_yoy` AVG + top-picks QoQ `q_rows`) — 전력기기 섹터 실측 재확인(earnings_yoy=73.25%, 정상 동작).
  4. `routes/trend.py`(`_rec_is_turnaround`, V-RECOVERY 라이브 가상매매) — 기존엔 OFS+4분기+dart_ofs_backfill 특수케이스만 걸렀는데 일반적인 CFS/OFS 중복도 걸러지도록 일반화(감사에서 "잔여 리스크"로 플래그됐던 부분).
  5. `backtest_strategies/golden_cross.py`(섹터 영업이익YoY SUM), `backtest_strategies/sector.py`(2곳: op_rows_s/op_prev_s 딕셔너리 비결정성, turnaround ni_rows 흑자전환 판정) — 백테스트 전용, golden_cross/sector 재검증 실행(pick_ta_bonus 경로 포함) 크래시 없음 확인.
  전체 콤보/6기간 재검증은 미완료 — 위 수정들이 실전략 성과에 미치는 영향은 다음 정기 재검증 때 반영.
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

### routes/sector_rotation.py → /api/sector-rotation ★신규(2026-06-27), 문서 소급기재(2026-09-08)
```
GET  /scores                      # 섹터별 활성도 스코어(0~100, 수급+수출+실적+거래량+RS)
GET  /leadership                  # 주도섹터·주도주·진입단계(ENTRY_NOW/EARLY_WATCH/HOLD_LEADER/WAIT/AVOID) 통합
GET  /history/{sector_key}        # 섹터별 월별 RS 히스토리
GET  /rotation-map                # 4주/12주 RS 4분면 맵
GET  /top-picks/{sector_key}      # 섹터 내 급등 후보 종목
GET  /dashboard-summary           # ★신규(2026-09-08) 메인페이지(macro 탭) 카드용 — 전략1(추세추종 집중/탈출)+전략2(낙폭과대 반등) 통합
POST /refresh-cache               # 캐시 수동 재계산
GET  /flow-signal-validation      # ka10051 업종별투자자순매수 신호 검증 현황
```
- `SECTOR_GROUPS`(10개 커스텀 섹터: 전력기기/원자력/화장품뷰티/의료기기미용/반도체/기판패키지/2차전지/방산/조선/바이오)는 파일 상단에 하드코딩. 캐시는 `sector_rotation_cache` 테이블(장중 1시간/장마감 기준 자동 갱신).
- `/dashboard-summary`: 전략1은 기존 `_entry_stage` ENTRY_NOW 중 최고점 1개를 `primary_focus`(집중)로 스포트라이트, 나머지는 `secondary`. 12주RS 양호했다가 4주RS가 막 꺾인 섹터는 `exit_alerts`(추세이탈 경보)로 별도 표시. 전략2(`_score_bottom_reversal`)는 낙폭과대(30)+반전조짐(30, 단기RS가 장기RS보다 개선됐는지)+수급전환(25, 최근30일 대 이전60일)+밸류트로프(15)로 점수화, `BOTTOM_ENTRY`는 반전조짐 컴포넌트가 실제로 점수를 받았을 때만 승격(낙폭+수급만으로는 승격 안 함 — 전략1의 "가격 확인 없이는 BUY 승격 금지" 원칙과 동일 취지). 전략1 ENTRY_NOW인 섹터는 전략2 목록에서 중복 제외.

### routes/global_foreign_flow.py → /api/global-foreign-flow ★신규(2026-09-08), TIC 대미 흐름 확장(2026-09-08 2차)
```
GET  /summary                     # ①국가별 자국시장 외국인 순매수(한국/대만/일본/중국/인도) + ②TIC 국가별 대미 주식 순매수(16개국) + observations(상관관계 관찰) + DXY/VIX/UST10Y
GET  /history?days=180            # ① 국가별 자국시장 시계열(라인차트용)
GET  /us-inbound-history?months=24 # ② TIC 전세계/아시아/유럽/국가별 월별 시계열(라인차트용)
```
- 데이터는 `routes/global_macro.py`의 `global_macro_data`(범용 시계열 저장소, indicator_code+date)를 그대로 재사용 — 신규 테이블 없음.
- **① 자국시장 외국인 순매수** 신뢰도(2026-09-09 기준): **한국**(`KR_FOREIGN_FLOW_USD`, price_history 집계) HIGH / **인도**(`IN_FPI_FLOW_USD`, NSDL `fpi.nsdl.co.in/Reports/Latest.aspx` HTML 파싱) HIGH / **일본**(`JP_FOREIGN_FLOW_USD`, MOF `week.csv` 직접 CSV) HIGH — e-Stat 대신 재무성 공개 CSV로 전환(섹션 9 참조) / **대만**(`TW_FOREIGN_FLOW_USD`) **BLOCKED**(TWSE WAF가 `/rwd/`·`/exchangeReport/` 경로를 307로 차단, Playwright도 동일) / **중국 북향자금**(`CN_NORTHBOUND_FLOW_USD`) PENDING(HKEX net-flow 엔드포인트 미확정).
- **② TIC(미 재무부) 국가별 대미 주식 양자간 흐름** ★신규(2026-09-08 2차, `collectors/tic_bilateral_flow_collector.py`) — 사용자가 "유럽/미주 등 시총 상위 국가 전체로 확장"을 요청해, ①과는 반대 방향("그 나라 투자자가 미국 주식을 얼마나 순매수했는가")을 측정하는 FRED 미러링 TIC 데이터(`FORLTEQTYNET*` 시리즈)를 추가 — 20개 국가/지역 시리즈(전세계·아시아합계·유럽합계·유로존 + 중국/일본/홍콩/대만/한국/인도/싱가포르(아시아), 영국/독일/프랑스/이탈리아/스위스(유럽), 캐나다/호주/사우디/브라질) 전부 실측 확인됨(월별, USD 백만, 공식발표상 약 2~3개월 지연). 기존 KRX 스타일 개별 거래소 스크래핑과 달리 **차단 리스크 없이 한 번의 API로 시총 상위 대부분 국가를 커버** — TWSE/HKEX처럼 개별 국가 거래소를 일일이 뚫는 것보다 훨씬 안정적임을 확인, 향후 유사 요청은 이 방식을 우선 검토할 것.
- `observations` 필드: ①이 마이너스(자국 이탈)이면서 ②아시아합계가 플러스(미국 유입)일 때 "방향 일치" 관찰 문구를 자동 생성 — 인과관계 단정은 하지 않고 상관관계 관찰로만 표현(문구에 명시).
- 프론트: `frontend/src/views/GlobalForeignFlowView.jsx`(`global_foreign_flow` 탭) — 섹션①(자국시장)+섹션②(대미 TIC, 국가별 랭킹바+지역합계+월별추이) 2단 구성. 데이터 없는 나라는 "0"이 아니라 "데이터 없음"으로 표시.
- 수집: 별도 스케줄러 잡 없음 — 기존 `_loop_global_macro_daily`(매일 06:45)가 실행하는 `scripts/ops/collect_global_macro_daily.py`에 `asia_foreign_flow`/`india_fpi_flow`/`jp_foreign_flow`/`tic_bilateral_flow` 4단계 추가(TIC는 FRED와 같은 API키 재사용, `fred` 스텝 바로 다음에 실행).

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
GET  /etf-list/{code}   # 종목 편입 ETF 목록 (KRX PDF + KIS CU 직접 수집)
```
- etf_amount=0인 날(수집실패)은 get_available_dates에서 자동 제외
- 2026-09-19부터 ETF Check 외부 사이트는 운영 수집/조회 경로에서 사용하지 않는다. `ENABLE_ETFCHECK_VALIDATION=1`은 수동 회귀 비교가 꼭 필요할 때만 명시적으로 사용한다.

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
| `_job_combo_daily` | 매일 18:35 (평일) | (스레드명 유지, 구현은 2026-08-26 교체) 전략센터 상위5 가상매매 — `execute_strategy_center_top_five_now()`가 그날 매트릭스 상위 5개를 재선정해 각 계좌 매도→매수 체결. 옛 병합조합 4종(combo_605/539/510/474)은 2026-08-23 이후 재실행 안 됨(위 "병합조합 가상매매" 항목 참조). 2026-09-07 "6개 계좌 매수 0건" 문제는 같은 날 리스크게이트 버그 수정으로 해결 완료(2026-09-19 재확인: sc_sector_focus 09-17, ai_combo 09-18 실제 매수 체결 확인) |

### ETF 수집 스케줄 (crontab — ETF_check/scheduler.py)
| 시간 | 실행 모드 | 설명 |
|------|-----------|------|
| 20:30 평일 | `--once` | 메인 수집 (장 마감 후) |
| 23:30 평일 | `--retry` | 실패 종목 재수집 |
| 02:30 화~토 | `--backfill` | 전날 최종 백필 (재수집 실패 시 보완) |

현재 운영 원본은 KRX PDF 전수 + KIS CU 배율이다. ETF Check 표본 수집 단계와 `retry_etfcheck_k_sample.sh` 크론은 2026-09-19 중단했으며, KRX PDF/운용사 예외/KIS 배율 재시도만 유지한다.

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

**App.jsx = 18,612줄**. ★2026-09-03 대규모 분리: `Screener`/`PeakView`(→`StrategyCenterView.jsx`)/`BacktestView`/`StrategyHub` 등 8개 컴포넌트가 App.jsx에서 `frontend/src/views/*.jsx` 개별 파일로 이동하고 `React.lazy()`로 로드되도록 변경(토큰 최적화, 섹션 11 참조). 아래 표는 2026-09-04 기준 재검증된 최신 줄번호. `const App = () => {`는 12896줄에서 시작.

⚠️ **분리 시 주의**: App.jsx 모듈 스코프에 있던 헬퍼 함수(`fmtKrw`, `fmtPctUs` 등)는 별도 파일(별도 ES 모듈)로는 자동으로 따라가지 않는다 — 분리 시 반드시 `frontend/src/utils.js`에 있는지 확인 후 명시적으로 import할 것. 2026-09-04에 이 누락으로 인한 `ReferenceError` 크래시 2건이 실제로 발생(섹션 9 참조).

### 별도 파일로 분리된 컴포넌트 (views/)
| 파일 | 탭 키 / 용도 |
|------|-------|
| `frontend/src/views/MarketIndicatorsView.jsx` | market_indicators |
| `frontend/src/views/MarketRadarView.jsx` | market_radar |
| `frontend/src/views/SemiconductorView.jsx` | semiconductor_sector (탭 렌더가 실제 사용하는 파일) |
| `frontend/src/views/SectorFollowupView.jsx` | sector_followup (MarketRadar 내부) / hot_sector |
| `frontend/src/views/SectorRotationView.jsx` | sector_rotation |
| `frontend/src/views/QuantMajorIndicatorsView.jsx` | quant_indicators (내부에서 `CafeSignalsView.jsx`의 `QuantCafeSignalsPanel` import) |
| `frontend/src/views/CafeSignalsView.jsx` | QuantMajorIndicatorsView 내부 서브패널(단독 탭 아님) |
| `frontend/src/views/StockAnalysisRsView.jsx` | stock_rs |
| `frontend/src/views/TenbaggerProjectView.jsx` | tenbagger_proj (nav 라벨 "조건 필터") |
| `frontend/src/views/RiskGateMonitorView.jsx` | risk_gate — routes/kis_trading.py 리스크게이트/주문생애주기/현금원장 모니터 |
| `frontend/src/views/SignalImpactView.jsx` | signal_impact |
| `frontend/src/views/DartExcelView.jsx` | dart_excel |
| `frontend/src/views/StrategyCenterView.jsx` | trend (구 `PeakView`) ★2026-09-03 App.jsx에서 분리 |
| `frontend/src/views/Screener.jsx` | screener / 전략센터(StrategyHub) `hubTab==='stocks'` 내부 embed ★2026-09-03 분리 |
| `frontend/src/views/StrategyHub.jsx` | strategy_hub (내부에 Screener embed, `데이터 라우팅` 탭 포함) ★2026-09-03 분리 |
| `frontend/src/views/BacktestView.jsx` | backtest ★2026-09-03 분리 |
| `frontend/src/views/StockDecisionEvidencePanel.jsx` | 분석(`analysis`) 메인 화면 내부, 즉시 로드(lazy 아님) |
| `frontend/src/views/InvestmentDecisionTaskPanel.jsx` | 분석(`analysis`) 메인 화면 내부, 즉시 로드(lazy 아님) |
| `frontend/src/views/SectorSignalSummary.jsx` | macro(매크로 탭 최상단, `MacroDashboard`의 `<SignalBoard>` 바로 다음) 내부, 즉시 로드(lazy 아님) ★신규(2026-09-08) — `/api/sector-rotation/dashboard-summary` 카드 |
| `frontend/src/views/GlobalForeignFlowView.jsx` | global_foreign_flow(`React.lazy`) ★신규(2026-09-08) — 아시아 5개국 외국인 자금흐름, `/api/global-foreign-flow/*` |
| `frontend/src/EtfCheckView.jsx` | etf_check (views/ 밖, src 바로 아래) |
| `frontend/src/utils.js` | API, isKRMarketOpen, isUSMarketOpen, fmtKrw, fmtPctUs 등 공유 유틸 ★2026-09-04 fmtKrw/fmtPctUs 추가 |

### App.jsx 내 컴포넌트 → 탭 키 → 시작 줄번호 → 위치(2026-09-04 재검증)
| 컴포넌트 | 탭 키 | 줄번호 | 위치 |
|---------|-------|--------|------|
| `SignalBoard` | (헤더 상시 노출) | 104 | module-level |
| `SignalSettings` | (settings 내부) | 826 | module-level |
| `SettingsView` | settings | 1007 | module-level |
| `EmploymentView` | employment | 1383 | module-level |
| `USInsightView` | (미국 인사이트) | 1544 | module-level |
| `TradeAnalysis2` | hs_trade2 | 1737 | module-level |
| `DartContractView` | dart_contracts | 3636 | module-level |
| `DetailedAnalysisView` | detailed_analysis | 5507 | module-level (isMobile prop) |
| `USStocksView` | us_stocks | 6028 | module-level (isMobile prop, 미국주식/스크리너/인사이트/바이오/13F 거물 동향 탭) |
| `BuyCandidateView` | buy_candidates | 7627 | module-level (changeStock/changeTab prop) |
| `PortfolioView` | portfolio | 8278 | module-level |
| `SectorReports` | reports | 9576 | module-level |
| `TenbaggerView` | tenbagger | 9688 | module-level |
| `MegatrendView` | megatrend | 10682 | module-level |
| `TelegramMentions` | telegram | 10882 | module-level |
| `ExportHealthView` | export_health | 11140 | module-level |
| `HardeningPlanPanel`/`TurnaroundWatchPanel`/`ConsensusRevisionPanel`/`CherryScreenerPanel` | exp_roadmap 내부 서브패널(pageTab: hardening/turnaround/cherry/consensus) | 11429/11544/12326/12416 | module-level |
| `ExperimentRoadmapView` | exp_roadmap (nav 라벨 "실험 로드맵") | 12631 | module-level |
| `WatchlistView` | watchlist | 13401 | App 내부 (closure) |
| `MacroDashboard` | macro | 13497 | App 내부 (`React.useState(() => function ...)` 패턴으로 안정화, closure) |
| `AIInsight` | insight | 18096 | App 내부 (closure) |
| `SystemStatus` | system | 18173 | App 내부 (closure) |

### 렌더 스위치 탭 (App.jsx ~18500줄대) — 2026-09-04 기준 확인된 키
```
macro / market_indicators / market_radar / analysis / us_stocks / quant_indicators
stock_rs / semiconductor_sector / detailed_analysis / buy_candidates / watchlist
portfolio / screener / strategy_hub / tenbagger / tenbagger_proj / sector_rotation
dart_excel / dart_contracts / megatrend / trend / reports / insight / system
export_health / telegram / settings / backtest / hs_trade2 / employment / etf_check
hot_sector / risk_gate / signal_impact / exp_roadmap
```
※ `global_econ` 탭은 2026-07-02부로 `ceo-briefing-platform` 프론트엔드로 이관되어 stock_dashboard 메뉴에서 제거됨.
※ 이 목록은 App.jsx 렌더 스위치의 `activeTab===` 조건을 grep한 결과이며, 전수 확인은 아님 — 새 탭 추가/삭제 시 이 섹션을 갱신할 것(섹션 상단 필수 행동 규칙 참조).

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
ESTAT_APP_ID=                          # 일본 e-Stat API 앱ID(무료 가입 즉시 발급) — collectors/mof_japan_flow_collector.py, 미설정 시 스킵
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
from db_compat import connect_primary_db
conn = connect_primary_db(timeout=30)
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
from db_compat import connect_primary_db
conn = connect_primary_db(timeout=30)
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
| 모바일에서 계좌현황(포트폴리오) 진입 불가 | ✅ 2차 수정(2026-09-18) | 1차(09-17): `window.prompt()`가 모바일 인앱 브라우저/웹뷰에서 미지원(`prompt() is not supported`)이라 클릭이 무시되던 문제를 React 모달로 교체. 2차(09-18): 사용자가 "고쳤는데도 안 보인다"고 재보고 — 실제 증상은 "메뉴 자체를 못 찾겠다"였음. `NAV_ITEMS`(App.jsx)에서 `portfolio`가 전체 ~30개 항목 중 28번째(하단 섹션)에 있어 모바일 사이드바에서 3번 넘게 스크롤해야 도달 가능했던 게 근본원인. `매수후보`+`계좌현황`을 배열 맨 앞으로 이동해 메뉴를 열자마자 스크롤 없이 바로 보이도록 수정, 모바일 뷰포트로 재검증 완료. **재발방지**: `window.prompt`/`alert`/`confirm` 등 브라우저 네이티브 다이얼로그는 모바일 인앱 브라우저 호환성이 보장되지 않으므로 신규 UI에 사용 금지 — 항상 앱 내부 모달 컴포넌트 사용(기존 `window.confirm` 사용처 다수 잔존, 동일 부류 위험 후속 검토 필요). 또한 NAV_ITEMS가 기능 추가로 계속 길어지고 있으므로, 자주 쓰는 개인화 메뉴(계좌/매수후보 등)는 새 항목 추가 시에도 상단 고정 위치를 유지할 것. |
| stock.db 상대경로 open으로 인한 stray 빈 DB 생성 | ✅ 수정(2026-09-12) | legacy SQLite 도구 7개 파일이 `DB_PATH = "stock.db"`(상대경로)를 직접 선언 — cwd가 어긋나면 그 자리에 빈 DB 생성. `/Applications/stock_dashboard_orphaned_*`(내장 디스크) 2건 + `runtime/data`, `runtime/frontend` 빈 stock.db 2건을 이 세션에서 발견·삭제(전부 0바이트, 데이터 손실 없음). 7개 파일(대부분 이미 죽은 코드였음, `routes/consensus.py`만 실사용) 전부 `db_utils.STOCK_DB_PATH`(절대경로) 참조로 통일 후 `safe_restart_backend.sh`로 재시작 완료. |
| KRX 신규 영숫자 종목코드(예: 액스비스=0011A0) TTM/공시/현금흐름 누락 | ✅ 수정(2026-09-06) | `main.py` 전역의 `stock_code.isdigit() and len==6` 국내종목 판정이 KRX 2026년 신규 6자리 영숫자 코드(숫자+대문자 1글자)를 전부 해외/미상장으로 오판 — TTM(`_calc_ttm_fundamentals`)·PER/PBR·시장정보·공시·전략분석 background 수집이 전부 스킵됨(실사용 73종목 확인). `_is_kr_code()`(정규식 `^[0-9A-Z]{6}$`) 헬퍼로 main.py 10곳 + `App.jsx` `isKrStockCode()`로 프론트 24곳 통일. 부수 발견: 현금흐름표(`get_cashflow_table`)는 데이터가 없어도 백그라운드 재수집을 트리거한 적이 없는 dead code(`_bg_collect_cashflow` 미호출) — 프론트는 이미 15초×8회 폴링 로직이 있었는데 서버가 트리거를 안 해 영원히 빈 테이블로 남던 상태였음. `raw` 없을 때 자동 트리거 추가. |
| 개별종목 심층 인사이트 vs 대주주 섹션 중복 | ✅ 정리(2026-09-06) | "대주주·임원 지분변동" 섹션과 "심층 인사이트"가 동일 테이블(`dart_insider_holdings`, `/api/insider/holdings` = tenbagger stock-insight `insider_trading`)을 각각 별도 fetch해 임원매매 이력을 화면에 두 번 표시하던 중복 제거 — 대주주 섹션은 "현재 주요주주"만 남기고 임원매매는 심층 인사이트에서만 표시. 심층 인사이트 섹션을 페이지 최하단에서 대주주 섹션 바로 아래(재무제표/현금흐름표보다 위)로 재배치. |
| KRX 승인API (data-dbg.krx.co.kr) | ✅ 정상 | OHLCV·지수 정상 수집. PER/PBR은 제공 안 함 → DB 직접 계산 |
| KRX 웹API (data.krx.co.kr) | ✅ Playwright로 정상 | requests 방식 CSV 다운로드 실패(보안강화). Playwright(실 브라우저) → 로그인+OTP+CSV 모두 성공. 매일 18:10 스케줄링됨 |
| K-mydata | ❌ 인증실패 | KRX_API_KEY가 K-mydata용 아님 |
| pykrx | ❌ Empty | KRX 서버 차단으로 빈 DataFrame |
| TWSE(대만) 외국인 순매수 수집 | ❌ 접속 차단(2026-09-08 확인) | `/rwd/`·`/exchangeReport/` 등 데이터 경로가 이 Mac 네트워크에서 WAF 307("FOR SECURITY REASONS")로 전부 차단됨(루트 도메인은 200으로 정상 — 데이터 경로만 선별 차단). Referer/User-Agent 조정, Playwright 풀브라우저 모두 동일하게 막힘 — IP/지역 기반 차단으로 추정. 대체 소스 없이는 `TW_FOREIGN_FLOW_USD` 수집 불가(`collectors/asia_foreign_flow_collector.py`의 `collect_tw_foreign_flow`는 코드는 있으나 상시 0건). 우회 시도(프록시/스푸핑)는 정책상 하지 않음. |
| 중국 북향자금(HKEX Stock Connect) 순매수 수집 | ⚠️ 미구현 | 사이트 자체는 접근 가능하나(hkex.com.hk 200) Historical-Daily 통계표가 JS로 동적 렌더링됨 — Playwright로 네트워크 캡처해 찾은 `/eng/csm/DailyStat/data_tab_daily_YYYYMMDDe.js`는 Turnover(거래대금)만 있고 실제 순매수(Net Buy/Sell) 필드가 없어 사용 불가. 실제 net flow가 나오는 엔드포인트는 날짜검색 인터랙션 뒤에 있는 것으로 추정되나 미확인 — 후속 조사 필요, `CN_NORTHBOUND_FLOW_USD` 현재 0건. |
| 일본 외국인 증권매매 수집 | ✅ 수집 중(2026-09-09) | `collectors/mof_japan_flow_collector.py` — 사용자가 `ESTAT_APP_ID` 발급 후 e-Stat으로 실제 조회해보니 이 통계("対外及び対内証券売買契約等の状況")는 e-Stat 포털에 없었음(검색 0건, API 자체는 정상 — "人口" 등 다른 키워드는 25,022건 조회됨). 대신 재무성이 인증 없이 직접 공개하는 CSV(`mof.go.jp/.../week.csv`, 2005년~현재, 주간, 지연 약1~2주)를 발견해 그쪽으로 전환 — e-Stat보다 오히려 더 간단하고 안정적. `ESTAT_APP_ID`는 `.env`에 보관만 하고(향후 JP_CPI/JP_GDP 등 다른 e-Stat 확장에 재사용 가능) 이 수집기는 사용 안 함. |
| 공공데이터포털 투자자API | ❌ 404 | getStocInvtTrdnInfo 서비스 폐지 |
| investor_trading_daily | ✅ 조치 불필요(2026-09-19 재확인) — deprecated, 죽은 테이블 | 원천 API(공공데이터포털 getStocInvtTrdnInfo)가 서비스 폐지되어 2026-07-10 이후 신규 데이터 없음(0건만 반환) — `collectors/public_data.py`가 2026-08-24에 이미 이 죽은 API 호출 자체를 제거함(코드 주석 확인). buy-only 오염(inst_net 450만행 중 음수 0건, 물리적으로 불가능한 패턴)은 여전히 남아있으나 **더 이상 갱신되지 않는 과거 스냅샷일 뿐이고, kiwoom_investor_daily가 이미 이 데이터를 대체(2026-07-21 trde_tp='0' 수정 완료, 최근 30~90일 실사용 구간 전부 KIS와 검증됨)** — 재수집으로 고칠 대상 자체가 없음(API가 없음). 테이블/기존 행은 과거조회 코드 영향 없도록 그대로 보존. |
| foreign_holding_daily | ℹ️ 정상 적재 중 | 문서상 "0행"으로 남아있었으나 실측 107,764행 확인(Kiwoom ka10008 경유로 이미 채워지고 있음, 문서만 stale이었음) |
| `stock_universe` 월간 배치 장기 정지 | ✅ 수정(2026-09-06) | `update_universe()`(존재하지 않는 함수) 호출로 매월 1일 배치가 조용히 실패 → `update_from_krx()`로 수정 + Naver fallback 정규식/PRAGMA/base_date 타입 동반 수정. 상세는 섹션 11 최상단 참조. |
| 분기 현금흐름표 investing/financing_cf 음수 전부 None | ✅ 수정(2026-09-06) | `main.py get_cashflow_table`의 `_q_or_diff()`가 capex 전용 "음수=오류" 필터를 operating/investing/financing_cf에도 적용해 정상적인 음수 흐름값(예: capex 지출로 인한 investing_cf 음수)을 전부 숨김. 삼성전자 등 대다수 종목의 분기 현금흐름표에 영향. `field=='capex'`일 때만 필터 적용하도록 수정. |
| Screener/StrategyCenterView `fmtKrw`/`fmtPctUs` 스코프 버그 | ✅ 수정(2026-09-04) | 2026-09-03 App.jsx→views 분리 시 각 파일이 참조하던 포맷 함수(`fmtKrw`, `fmtPctUs`)가 다른 ES 모듈로는 안 넘어가 `ReferenceError`로 렌더가 크래시(Screener embed는 전체 화면 블랙아웃까지 발생). `frontend/src/utils.js`에 두 함수를 공용 헬퍼로 추가하고 양쪽에서 import하도록 수정. **재발방지**: 컴포넌트를 별도 파일로 분리할 때는 반드시 참조하는 모든 헬퍼가 import돼 있는지 확인(섹션 6 상단 경고 참조). |
| 백테스트 4개 전략(turnaround/regime_adaptive/value/v2) 결과 비결정성 | ✅ 수정(2026-09-04) | 근본원인은 `financial_data` CFS/OFS 중복행에 대한 정렬 tiebreak 부재(지배적) + corp_action/financial_data 실시간 재검증 잡과의 타이밍 경쟁(2차) — 섹션 11 참조. `report_type` 필터+ORDER BY tiebreak는 기본 적용, `data_asof_ts` 옵션 파라미터로 회귀검증 재현성 확보. v8도 동일 취약점 확인·수정. |
| composite 시장필터 dead code | ✅ 발견+opt-in 수정(2026-09-04) | `run_backtest_composite`가 KOSPI MA120 `market_bullish`를 계산만 하고 매수 게이트에 배선한 적이 없어 하락장 방어가 전혀 작동하지 않던 상태(2026-07 폭락에서 KOSPI와 거의 동행한 -20.95%의 원인). `use_market_filter=True`(opt-in)로 배선 가능하나, walk-forward 검증 결과 상승장 기회비용이 더 커 기본값 False 유지. |
| 텐버거 가치함정(value trap) 추천 | ✅ 수정(2026-09-06) | 미원화학처럼 유동성 낮고 대주주 지분 집중된 흑자·저PBR 종목이 텐버거 후보로 추천되던 문제 — `tenbagger_engine.py`에 유동성(60일 평균거래대금)+지분집중(`dart_insider_holdings`) 가드레일 신규, composite.py엔 `value_trap_gate`(opt-in)로 이식. 상세는 섹션 11(4차) 및 [docs/CLAUDE_CHANGELOG_20260906_composite_tuning.md](docs/CLAUDE_CHANGELOG_20260906_composite_tuning.md). |

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
| 만기형 채권 ETF 빈 PDF 오판 | ✅ 수정됨 | `465780`이 2026-09 만기 청산 과정에서 구성 수 `11→7→5→4→1→0`으로 정상 감소했지만 빈 PDF를 수집 실패로 계산해 직접 게시가 9/11에 멈췄다. 현재는 종목명 만기월, 과거 5일 단조감소, 국내주식 0개, KIS 예상 구성 수 0개, raw SHA-256 일치를 모두 만족할 때만 `KRX_MATURITY_WINDDOWN` 예외로 인정한다. 일반 빈 응답은 계속 실패 처리한다. |
| 섹터 트렌드 오분류 | ✅ 수정됨 | `sector_mid`(e.g. "상업서비스")가 업종과 맞지 않는 종목 多 → `sector_large` 기준으로 전체 변경. **재발방지**: 섹터 분류는 반드시 `sector_large` 기준으로. `sector_mid`는 신뢰도 낮음 |
| 수출공동 표시 이종업종 혼입 | ✅ 수정됨 | HS코드가 넓어 전혀 다른 업종(HD건설기계 등)이 공동 표시 → 동일 sector_large 종목만 필터링. **재발방지**: HS코드 기반 공동 매핑 시 반드시 sector_large 교집합 필터 필수 |
| **shares_issued 우선주 포함** | ✅ 해소됨(문서 낡음, 2026-09-19 재확인) | 이 표에 기록된 삼성전자(1.43배)·SK하이닉스(2.08배)·현대차(1.49배)·LG화학(1.27배) 배율을 2026-09-19 재검증한 결과 전부 0.95~1.03배로 정상 범위(코스피 시총 상위 30종목 전수 확인, 10% 초과 이탈 0건) — 언제 어떤 작업으로 수정됐는지는 특정 못했으나(문서 업데이트 누락으로 추정), 현재 데이터는 정상. `EPS 저장값 vs 계산값 괴리`·`TTM EPS 계산 부정확` 항목(아래)은 shares_issued와 별개의 구조적 이슈(지배주주 귀속 순이익 vs 전체 NI)라 이번 재확인 대상 아님, 여전히 유효 |
| **EPS 저장값 vs 계산값 괴리** | ⚠️ 구조적 | FnGuide 저장 EPS = 지배주주 귀속 순이익 ÷ 보통주 수. 우리 계산 EPS = 전체 NI ÷ shares_issued(우선주 포함). 두 효과가 역방향으로 작용해 일치 불가. **올바른 계산**: 지배주주 순이익(별도 미저장) ÷ 보통주 수(미분리). FnGuide SVD_Main.asp에서 직접 수집한 EPS/BPS를 1순위로 사용할 것 |
| **TTM EPS 계산 부정확** | ⚠️ 주의 | TTM EPS = 최근 4Q net_income ÷ shares_issued 방식은 shares_issued 우선주 포함으로 EPS 과소 계산됨. 단, 분기 데이터 자체(TTM NI 합산 = annual NI)는 정확히 검증됨. 현재 stock_universe에 TTM PER 반영됨 — 외부 사이트 대비 PER 높게 나올 수 있음 |
| **PER/PBR 외부사이트 일치율** | ℹ️ 현황 | Codex 검증(500종목): revenue/op_profit 100%, net_income 97.48%, CF 98-99%. EPS(FnGuide) 67.79%, BPS(FnGuide) 82.53%. PER(Naver) 29.81%, PBR(Naver) 49.49% — 기준 연도·주식수 차이에 기인. 네이버 기준일 vs FnGuide TTM 기준일 다름 |
| **4중검증 L3 수정 후 B/S NULL 증가** | ℹ️ 구조적 | fnguide/legacy B/S 파싱오류 ~10,440건을 NULL처리함. B/S NULL 행은 P&L 표시에는 영향 없음. 향후 DART 재수집 시 자동 채워짐. data_source: fnguide_bs_null_fix, legacy_bs_null_fix, quarterly_recalc_bs_null |
| **dart_recollect 분기 NI 파싱실패** | ⚠️ 여전히 미해결(2026-09-19 재확인: 7,613건으로 오히려 증가) | DART 분기 보고서에서 당기순이익 XBRL 태그 매핑 실패. "다음 DART 재수집부터 점진 해소 예정"이라 기록돼 있었으나 실제로는 5,228→7,613건으로 늘어나 자연 해소되지 않음을 확인 — dart_collector.py의 XBRL 태그 확장만으로는 부족한 것으로 보임, 별도 파싱 로직 점검 필요(이번 세션에서는 카운트 재확인만 하고 실제 수정은 미착수) |
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

> 📦 **2026-08-30(2차) 이전 변경이력은 전량 `docs/CLAUDE_CHANGELOG_ARCHIVE.md`에 있습니다** (2026-09-04 재정리 — 이전엔 archive로 옮긴다면서 CLAUDE.md 원본을 안 지워 20건이 그대로 중복 남아있었음, 이번에 제거). 아래는 최근 5개 항목만 유지합니다.

### 2026-09-20 price_history 무결성 가드(P0) — close<=0 거부·수급 6필드 보존·비KR종목 공통필터
- `price_integrity.py`: `WRITE_GUARD_FUNCTION_SQL` 분리 + 트리거 함수에 `NEW.close<=0 OR NULL` fail-closed 거부 추가(검증 플래그 우회보다 먼저 실행). 라이브 반영은 `scripts/apply_price_write_guard.py` 재실행 필요(price_history에 AccessShareLock 잡은 idle-in-transaction 세션이 있어 DROP/CREATE TRIGGER는 쓰기 한가한 시간대 권장).
- `crud.py`: `merge_supply_fields()` 신규 — 장중 1분 갱신 시 기존 수급 6필드(inst/frn/ind + 금액3)를 0·NULL로 덮어쓰지 않도록 보존(기존엔 inst/frn 2필드만 보존).
- `security_master.py`: `is_kr_equity_code()` 신규(6자리 `[0-9A-Z]`만 허용, `^KS11`/`GC=F`/`USDKRW=X` 등 제외) → `routes/buy_candidates.py` POST 등록·자동보드 후보에 적용.
- 테스트 `tests/test_price_history_guard.py` 6건(트리거 롤백온리 검증 포함) 통과, 기존 46건 회귀 무손상.

### 2026-09-19 StockEasy 로직 일치율 재검토 — 모멘텀 Easy 0% 근본버그 수정 + 매수로직 개선
`stockeasy_logic_validator.py`의 `replay_entry_day_inclusion()`이 "오늘 실시간 보유 0개"(전략이 일시적으로 전량현금 상태)면 1년치 과거 편입 이벤트 재현검증 전체를 건너뛰고 무조건 0%를 반환하던 버그 발견·수정 — 모멘텀 Easy가 8월 27종목→9월 0종목으로 서서히 전량현금 전환되며 4일 연속 "0%"로 잘못 표시되던 것을 "1년치 이력에 편입이벤트가 있는지"로 판단기준 변경, 즉시 96.4%로 정상화. 스탁이지 사이트(stockeasy.intellio.kr) 직접 접속으로 3개 전략 매핑(momentum/peak/value = 모멘텀 Easy/피크 Easy/밸류 Easy)이 정확함도 확인. 추가로 momentum 미스 2건(에이피알 ret20 임계 근소미달, 셀바스AI 소형주 거래량무관 급등패턴)과 value 미스 2건(LS/지엔씨에너지 — 거래량 동반 없는 극단 낙폭과대 저점매수)을 실측 근거로 반영해 momentum 96.4%→97.8%, value 85.7%→100%. 매도(편출) F1은 세 전략 모두 19~40%로 낮으나, 실제 ground-truth 이탈사건 자체가 90일간 4건뿐인 극소표본(재현 검증 시도한 안트로젠/코오롱티슈진 등 대손실 케이스는 강제로 임계값을 낮추면 실제 편출일 10일+ 이전부터 조기오탐만 늘어 정밀도만 훼손됨을 실측 확인) — 무리한 피팅 대신 표본 부족으로 결론, 보류.
- **Peak/모멘텀 매도 추가분석(2026-09-19 후속)**: 실제 이탈이 83건/143건으로 표본이 충분해 패턴 확인 — F1 Peak 40.0%/모멘텀 34.4%. 허위경보 1위가 LG이노텍(Peak 12회/모멘텀 10회, +180~300% 메가수익주)인데 기존 `_mega_mult`(손익100%↑ dd20 임계 1.6배 완화)가 공통 보조신호에만 적용되고 Peak 1순위 `dd20_severe`·모멘텀 dd20 단독신호엔 누락된 버그 발견 → `_mega_mult_primary`로 두 곳에 적용(LG이노텍 12회 중 06-02/17/18 3건 해소). 나머지 오탐은 `parabolic_tp`(ret5 31~77% 초급등, 05-27~06-01 4건)·dd20 -23~-28%(1.6배로도 미해소 4건)·trend_break(06-12 1건)로 단일종목(초극단 아웃라이어) 특수패턴이라 과적합 위험으로 추가 패치 보류. 모멘텀 반복미스(NAVER/파마리서치 등)는 전부 보유 0~8일·손익 -3~+11%의 "신규 후보에 순위 밀려 슬롯 로테이션" 유형이라 가격/기술지표로 예측 불가한 구조적 한계로 확인.

### 2026-09-19(2차) StockEasy 매도 일치율 개선 — 채점 정상화 + 섹터 바스켓 전파
매도 F1이 낮았던 근본원인: ①스탁이지 이탈은 섹터 묶음편출이 다수(모멘텀 이탈의 71%가 하루 4종목+ 동시편출일, 8/19 하루 20종목) ②자동튜너가 정한 일일 예측 상한(momentum 3)이 대량편출일 재현율을 21.6%로 제약 ③"이탈 당일"만 정답이라 조기경고가 오탐으로 집계 ④순위밀림 이탈은 예측불가. 조치(`stockeasy_logic_validator.py`): (C)`backtest_sell()`을 "이탈 5영업일 전~당일 예측 시 적중"(window=5) 기준으로 정상화(기존 당일 기준은 exact_* 키/트래커 "매도F1(당일)" 열로 병기), (A)`_get_our_sell_candidates()`에 섹터 전파 추가(같은 섹터 보유 2종목+ 중 30%+가 매도신호면 나머지도 후보, peak/momentum은 일일상한 제거) — 60/40 시간분할 검증에서 훈련·검증 모두 개선(모멘텀 검증 F1 72.1→80.4, Peak 46.2→57.1). (B)매수조건 이탈=매도 규칙은 Peak 검증구간 +1p·모멘텀 재현율 절반·밸류 무의미로 미채택. 최종 5일창 F1: Peak 40.0→55.8, 모멘텀 34.4→80.5, 밸류 19.0→45.2(밸류는 이탈 4건뿐이라 참고용). 부수: 모멘텀 섹터 사전계산이 미사용 죽은코드인데 호출당 수십초 소모 → `_MOM_SECTOR_ROTATION_ENABLED=False`로 차단, 전체 재계산 3,800초→14초. `_entry_signal_ok()`를 모듈 수준으로 분리(연구용). 연구 스크립트: `scripts/research_stockeasy_sell_cache.py`, `scripts/research_stockeasy_sell_eval.py`. 위 09-19 항목의 매도 F1 수치(19~40%)는 이 항목으로 대체.

### 2026-09-18 모바일 계좌현황 진입불가 2차 수정 — 메뉴 위치 문제
09-17 `window.prompt()` 수정 후에도 사용자가 "안 보인다"고 재보고 — 실제로는 `portfolio` nav 항목이 `NAV_ITEMS`(App.jsx) 하단(전체 30개 중 28번째)에 있어 모바일에서 스크롤을 3번 넘게 해야 도달 가능했던 게 원인이었음. 매수후보+계좌현황을 배열 최상단으로 이동해 스크롤 없이 즉시 보이도록 수정, 모바일 뷰포트 재검증 완료. 섹션 9 참조.

### 2026-09-17 모바일 계좌현황 진입불가 1차 수정 — window.prompt 미지원
사용자 리포트("모바일에서 내 보유계좌가 안보임")로 확인한 결과, 계좌현황 탭 비밀번호 확인이 `window.prompt()`를 써서 이를 지원하지 않는 모바일 인앱 브라우저/웹뷰에서 클릭이 조용히 무시되고 있었음(모바일 뷰포트 재현, 콘솔에 `prompt() is not supported` 확인). `App.jsx`에 React 모달로 교체, 모바일 뷰에서 계좌현황 진입→보유종목 렌더링까지 재검증 완료. 섹션 9에 재발방지 규칙 기록(`window.confirm` 등 다른 네이티브 다이얼로그 잔존 — 후속 검토 필요).

### 2026-09-13 전략센터 "현재 최고 155%" 표시 버그 정정 + 3종 병합계좌 646.43% 정식 등록 + 손익집중도 노출
- **원인**: `/api/backtest/combinations/list`가 `end_date == MAX(end_date)` 정확일치만 반환해, 하루 늦게 등록된 약한 조합(golden_cross+recovery 155%)이 이미 등록된 sector_focus+v2(403.09%)·sector_focus(547.36%)를 화면에서 통째로 숨겼음 → 최신 end_date 기준 14일 이내로 완화(`routes/backtest.py`). 680%대 옛 기록은 HD현대일렉트릭 1종목이 손익의 34%를 차지한 것으로 액면분할 버그는 아니었음(실제 전력기기 랠리).
- `merged_simulator.pnl_concentration()` 신규 + `StrategyHub.jsx` "상위1종목(코드) N% 기여" 캡션. `backtest_common._run_portfolio`에 `corp_action_factors` 신규(v4/v5가 기업행위 조정계수를 안 쓰던 것 — `base.py` 연결). 결측일 1,739개 KRX 재수집(159,054행 갱신, coverage_gap 7,633→2,697).
- **sector_focus+v2+composite 3종 병합계좌 `cmb_da1d39936923` 등록**: 646.43%/MDD -46.11%(2종 조합 366.82%/-58.25% 대비 개선). composite 런(`83e9a856a4ce`)의 price/corporate_action 아티팩트를 7% 임계값(2.76%<7%)으로 수동 재등록해 `execution_strict` 획득 — **7% 정책은 `run_registry`/`persist_merged_run` 코드에 아직 배선되지 않았고 이 1건만 수동 처리**. sector_focus/v2는 `point_in_time_approx` 등급, 상위1종목(300720) 21.6%, 가격데이터 갱신으로 재시뮬레이션마다 수치가 움직임(동일 컴포넌트인데 403→366.8%). 상세: [research_outputs/strategy_return_research_20260912/claude_wonderful_swirles_contribution_20260913.md](research_outputs/strategy_return_research_20260912/claude_wonderful_swirles_contribution_20260913.md)
- **⚠️ 09-19 정정 — 646.43%는 오염된 1건에 의존**: 300720의 composite 거래 1건(2021-08-23~09-09, `익절40%`, +1,451%, 손익 1.45억=총손익의 21.6%)이 `price_history`의 오염 봉(2021-09-08~10 종가 238,000·거래량 0 — 직전 23,800의 10배 자릿수 오류, 09-13 21,700으로 복귀; `price_jump_audit`=`unresolved_active_common`)에서 청산된 가짜 수익이었다. 이 종목을 제외하고 재시뮬레이션하면 3종 조합 **371.2%**(2종 370.6%와 사실상 동일), 상위2종목(300720+086520) 제외 시 3종 359.2%/MDD -48.4% vs 2종 333.1%/-53.9%. 즉 "composite 추가로 2배"는 이 1건 때문이고, 오염 제외 후 실제 개선은 소폭(+26%p, MDD 5%p 개선). 7% 오염률 임계값은 **오염 윈도우의 손익 크기를 보지 않아** 이런 케이스를 통과시킴 — 임계값 배선 전에 손익기여 상한 등 보완 필요. `cmb_da1d39936923`은 참고용으로만 취급할 것.

### 2026-09-09 composite 추가신호 탐색 6건 전부 기각 + 구조적 개선(conviction_sizing_gate) 시도도 기각
> "다른건 추가로 검토할게 없을까" 연장선 — 신용잔고(kiwoom_credit_balance)/대량보유자 지분변동(dart_major_holders) 2건을 마저 독립검증했으나 리프트가 0에 가깝거나(신용잔고 +8.1% vs 베이스라인 +8.8%) 오히려 위험(대량보유자: 큰 변동 버킷 함정률 28~29% vs 보합 12.7%) — 이걸로 이번 세션 신규 단일신호 탐색(PBR백분위/고정비레버리지/CAPEX/재고급증/신용잔고/대량보유자, 총 6건) 전부 약하거나 역효과로 마무리, 신규 단일신호 탐색 중단.
- 방향을 "새 신호 찾기"에서 "이미 있는 score를 다르게 쓰기"로 전환 — `conviction_sizing_gate` 신규(opt-in): 매수 여부뿐 아니라 포지션 사이즈도 score(60~69/70~79/80+)에 비례시킴(총 투입자본 한도는 불변, 재배분만). 5개 강도 조합 7구간 walk-forward 전부 기각(23.21%→8~16%대) — bull_covid 구간에서 문턱만 겨우 넘긴(60~69점) 종목들이 오히려 그 구간 최대 승자였음이 밝혀져, "고득점=고수익"이라는 전제 자체가 이 전략엔 안 맞음. 기본값 False 유지, 상세는 `composite.py` docstring 참조.

### 2026-09-08(5차) composite의 material_backlog_bonus를 turnaround/v2/value에도 이식 — 전부 기각(무변화 또는 악화)
> 지난 세션(09-06)의 composite 튜닝 후속 — 사용자가 "백테스트 관련 다른 전략"에도 적용해보라고 요청.
- turnaround(복합점수에 직접 가산)/v2·value(entry_bonus_fn 진입우선순위, composite와 달리 진입조건 자체는 불변)에 opt-in `use_material_backlog_bonus` 신규, 공용 헬퍼 `_make_material_backlog_bonus_fn()`을 backtest_common.py에 추가. 7구간 walk-forward 결과 **전부 기각**: turnaround avg 20.92%→20.48%(거의 무변화 — 기존 점수스케일 100점이 이벤트보너스 3점을 압도), v2 avg 15.36%→13.91%(악화, 구간별 들쭉날쭉), value avg 14.78%→11.64%(일관 악화). composite에서만 성공했던 이유는 "이미 신호를 통과한 후보의 순서만 바꾸는" v2/value 구조와 달리 composite는 이 보너스가 threshold 게이트 통과 여부 자체에 영향을 주는 스코어 성분이기 때문으로 추정. 3개 전략 전부 기본값 False 유지, 상세 수치는 각 파일 docstring 참조. regime_adaptive는 애초에 스코어링/우선순위 메커니즘 자체가 없어(첫 매치 종목 순서로 매수) 이식하지 않음.

### 2026-09-08(4차) "세계 최고 수익 로직" 요청에 대한 신규가설 검증 — 2건 기각(라이브 노출 없음 확인)
- 사용자가 "모든 지식을 동원해 새로운 가치를 창출하는 로직"을 요청 — 감이 아니라 오늘 HS작업에서 쓴 것과 동일한 실측검증 방법론으로 신규 가설 2건을 테스트하고 **둘 다 기각**.
- **가설1(내부자매수 추종) 기각**: `dart_insider_holdings`에서 CEO/등기임원 순매수 공시 이벤트(2021~2025, 전체 14,072건/CEO만 4,372건) 이후 20/60/120일 KOSPI 대비 초과수익을 이벤트스터디로 계산 — 전 구간·전 필터링에서 **초과수익 전부 마이너스(-1.4%~-8.9%), 승률 37~42%(50% 미만)**. 원시수익률만 보면 플러스(시장 베타에 편승)라 착시 위험 있음 — 반드시 벤치마크 대비로 봐야 함. "임원 매수=매수신호"는 이 시장 데이터에서 성립하지 않음.
- **가설2(patent_catalyst 단독전략) 재확인 결과 미흡**: 과거 세션에서 라벨레벨 통계적 lift(적자기업 12개월 내 50%+ 달성률 학습42.3%/검증41.9% vs 대조35.9%/34.8%, lift 1.18~1.20x)는 검증돼 있었으나, **이번에 처음으로 실제 백테스트(2024.06~2025.05)를 돌려보니 총수익률 -11.22%, 승률 26.1%**로 실전 수익성은 없음 확인 — 통계적 lift가 있어도 그대로 단독 전략 수익으로 이어지지 않는다는 걸 재확인(과거 기록에 "실전 수익성 미검증"이라 이미 적혀 있었던 그대로).
- **라이브 노출 여부 확인 — 둘 다 없음**: `routes/trend.py STRATEGY_CENTER_PAPER_ENGINES`(실제 가상매매 7개 엔진: golden_cross/sector_focus/v5/v10/v8/v2/contract_momentum)에도, `routes/backtest.py ALL_STRATEGIES`(백테스트 거버넌스 매트릭스)에도 `patent_catalyst`/`dual_conviction`이 등록돼 있지 않음 — `backtest.py` import shim에만 있고 UI/API로 선택할 경로 자체가 없는 고립 코드, 애초에 라이브 노출된 적 없음. `composite`(V11)의 임원매수 활용은 단독신호가 아니라 흑자전환/추세/수급/가치와 묶인 opt-in 보너스이며 자체 walk-forward 검증(avg6 +9.47%→+10.95%) 기록이 있어 오늘 발견과 직접 충돌하지 않음 — 미변경. `high_profit_compound`(V13, 임원매수180일 필터 사용)는 이미 "legacy — 실전용 아님"으로 표시돼 `include_legacy=false` 기본 API에서 제외된 상태 — 추가 조치 불필요.
- **후속 검증 완료(2026-09-08 같은날 진행)**: 수주잔고 급증(QoQ+30%↑, DART rcept_no 기반 as-of 공시일 사용, 1,426건)과 기관/외국인 순매수 가속도(5일 순매수가 직전 페이스의 3배 이상, 900개 표본종목 무작위추출, 89,592건 중 20,000건 다운샘플) 둘 다 **기각**. 수주잔고: 20/60/120일 초과수익 평균+4.4%/0.0%/-1.0%이나 **중앙값은 전부 마이너스(-2.4%/-7.9%/-11.0%), 승률 35~41%** — 평균이 플러스로 보이는 20일 구간도 소수 대박종목이 끌어올린 착시(로또형 분포)일 뿐 일관된 엣지 아님. 순매수가속도: 10/20/60일 평균초과수익 전부 마이너스(-0.25%/-0.38%/-2.21%), 중앙값도 마이너스, 승률 38~43% — 표본이 약 2만건으로 커서 통계적으로 견고한 기각.
  **종합**: 이번 세션에서 테스트한 4개 단독가설(임원매수/특허촉매 단독전략/수주잔고급증/수급가속도) **전부 기각** — "이미 공개된 이벤트에 사후 반응"하는 방식은 이 시장에서 단독 엣지가 안 나옴이 일관되게 확인됨. composite.py(V11)가 이런 신호들을 단독이 아니라 흑자전환+추세+수급+가치와 AND로 결합했을 때만 보너스로 쓰는 설계가 왜 옳은 접근인지 방증하는 결과. 추가로 테스트할 만한 단독가설이 새로 나오면 이 항목 갱신할 것.
- ⚠️ **"600%대 병합계좌" 재검증 — 헤드라인 688.9%는 버그로 부풀려진 숫자였음, 실제 기대값 366~403%**: 사용자 질문("600%에 만족해도 되나")에 답하기 위해 상위 헤드라인 `cmb_c8f841b9708d`(688.9%, sector_focus+v2, 2020-01-01~2026-08-20 연속 단일 시뮬레이션, max_positions=20, ticket_budget=1000만원, dynamic_tickets=True, 가중치 없음)의 정확한 설정을 `backtest_run_specs.parameter_json`에서 복원해 **오늘까지의 모든 버그수정(sector.py op_yoy·CFS/OFS dedup 등)을 반영한 코드로 완전히 동일 설정 재현**(`scripts/research_combo_20260908_continuous_repro.py`, 2020-01-01~2026-09-08). 결과: **총수익률 403.09%**(원본 대비 -286%p), `tiebreak_stability`(8회 무작위 타이브레이크) 평균 368.6%/중앙값 366.3%/범위 331.6~404.8%/CV 5.2%/base_above_max=False — 이번엔 안정적(운 아님)으로 검증됨. MDD -41.8%.
  **결론**: 688.9%는 오늘 고친 버그들 때문에 부풀려져 있던 숫자였고, 지금 코드 기준 진짜 재현 가능한 기대값은 **366~403%(6.5년 누적, MDD -41.8%)**. 참고로 avg6(6개 표준구간 독립 재시작·비복리) 방식으로는 sector_focus 단독/60:40조합 모두 구간당 평균 24~27%(연속복리 방식과는 다른 지표, 직접 비교 불가 — 방법론 차이 주의). combo_* 4계좌(동결보전 결정된 옛 계좌들)도 이 부풀려진 시절 숫자를 근거로 등록됐던 것이므로, 향후 재평가 시 이 재현값을 기준으로 삼을 것.
  ⚠️ **정식 등록 완료(2026-09-08, 사용자 지시 "제대로 다 돌려줘")**: `persist_merged_run()`의 execution_strict 게이트(price_integrity/survivorship_integrity/corporate_action_integrity 3종 필요)에 처음엔 막힘 — **원본 688.9%의 컴포넌트 런(80092cf054aa/c1925e1b53af)도 이 3종이 전부 미등록 상태였다는 걸 확인**(이 게이트가 원본 등록 이후 추가됐거나 다른 경로로 우회됐던 것으로 추정). 대충 통과 처리하지 않고, `scripts/audit_selected_strategy_price_integrity.py`와 동일한 실제 검증 로직(as-of 거래가능구간·상장폐지구간·price_jump_audit 오염구간 체크)을 재실행분 run_hash 2개(`24ab3fdb0a28`/`5d1dbed8b823`)에 직접 적용(`scripts/audit_combo_20260908_refresh_integrity.py`) — 실측 결과 sector_focus 175개/v2 673개 보유구간 전부 오염 0건, 3종 전부 정직하게 PASS. 이후 두 런의 상태가 `point_in_time_approx`(rank 2)로 올라 게이트 통과, `persist_merged_run()`으로 **정식 등록 완료: run_id `cmb_90d92c104f0a`, 403.09%, tiebreak_stability 기록됨(평균368.6%/중앙값366.3%/범위331.6~404.8%/CV5.2%/base_above_max=False)** — `/api/backtest/combinations/list`에 노출 확인. 참고: 동일 스크립트를 실수로 두 번 실행해 `cmb_04252615749f`(동일 403.09%)도 중복 등록됨 — 값은 둘 다 정확하고 무해하나 목록에 중복 항목으로 남아있음, 필요시 정리 검토.
  ⚠️ **2026-09-09 전 복합전략 재검증(사용자 지시 "복합전략 모두에 대해서 필요하다면 백테스트 후 % 수정")**: 등록된 병합계좌 36건 전수 조사 — 대부분(`sector`/`sector_fresh`/`earn`/`moon30`/`v10_bull`/`v10_latest`/`v10_current_control` 컴포넌트 사용)이 현재 `routes/backtest.py ALL_STRATEGIES`에 없는 죽은 별칭이라 현재 코드로 재현 자체가 불가능함(별도 조치 불필요 — 애초에 `/api/backtest/combinations/list`가 "가장 최근 측정일 기준 상위 5개"만 보여주므로 이 죽은 조합들의 부정확한 수치가 사용자 화면에 노출된 적도 없음). 실제 로직이 바뀐 컴포넌트(recovery=equity-curve $0버그, se_momentum=CFS/OFS 비결정성, golden_cross=CFS/OFS SUM중복)를 포함하는 재현 가능한 조합 5개를 sector_focus+v2와 동일 절차(연속 2020-01-01~2026-09-09 시뮬레이션 → 무결성감사 → tiebreak_stability → persist_merged_run)로 재실행(`scripts/refresh_all_combos_20260909.py`):
  - **sector_focus 단독**: `cmb_14d19e97767b`, **547.36%** 정식 등록(무결성 통과, tiebreak 안정).
  - **golden_cross+recovery**: `cmb_849d7435701d`, **155.47%** 정식 등록(무결성 통과, tiebreak 안정).
  - **earnings_conviction+golden_cross+recovery**: 등록 **거부됨**(정상 동작) — 단일경로 244.9%가 8회 무작위 타이브레이크 최댓값 215.0%(평균 146.4%, CV 31.4%)를 초과해 "타이브레이크 운"으로 판정, `allow_path_luck=True`로 억지 통과시키지 않음 — 이 조합의 정직한 기대값은 244.9%가 아니라 ~146%대.
  - **earnings_conviction+se_momentum, earnings_conviction+recovery+se_momentum**: 최초 등록 시도 **실패** — 두 가지 문제 겹침. (1) se_momentum의 trades_json이 라운드트립 완결거래(1,752건)와 **그것과 code+매수일까지 100% 중복되는** action='buy' 이벤트로그(1,752건)를 같이 담고 있는데, 재실행 스크립트의 주문 파서가 action 키만 보고 이벤트로그로 오인 처리해 이중집계·매도주문누락(내 파싱 버그) — 라운드트립 필드가 있으면 무조건 그쪽을 우선하도록 수정. (2) **더 근본적인 문제 — se_momentum.py 자체의 미조정 가격 버그**: se_momentum이 쓰는 가격 시계열이 원시(미수정) 종가라, 보유기간이 확정된 기업행위(감자 등)를 가로지르면 그 점프가 그대로 pnl에 섞였음. 실측: 097780이 2026-06-16 감자(주식수 50,732,723→20,293,089, 2.5배)로 664원→1,348원 하루 +103% — 진짜 거래이익 아님. 기존 "1일 등락률 0.45~2.2배 밖이면 종목 전체 제외" 필터가 있었지만 097780(2.03배)·084010(2022-02-21, +117%→3일뒤 원복, 2.17배)처럼 문턱 바로 아래인 경우는 조정 안 된 채 그대로 거래에 쓰였음. turnaround/regime_adaptive/composite는 2026-08-23에 이미 `_load_corp_action_factors`/`_corp_action_adjusted_entry`로 이 문제를 막아뒀는데 se_momentum엔 그 보호가 없었음(제자리에서 발견한 원인).
    **수정 완료(`backtest_strategies/se_momentum.py`)**: (a) 확정된 기업행위(`corporate_action_events.adjustment_status='factor_confirmed'`)가 있는 종목은 이벤트 이전 구간의 가격을 backward_price_factor로 누적조정해 시계열을 연속화(수정주가 방식) — 종목을 통째로 버리지 않고 정상적으로 거래에 포함. (b) 확정 근거가 없는 미해소 이상급등락(`price_jump_audit.return_usable=0`이면서 확정 이벤트로 설명 안 되는 날짜)이 하나라도 있는 종목은 여전히 통째로 제외(기존 보수적 동작 유지, 084010 등 3종목 계속 제외됨). 재검증: se_momentum 재실행(run_id 244ad1b9) 후 무결성 재감사 결과 **오염 20건이 전부 confirmed_corporate_action(이제 정상 조정됨)뿐, unresolved/missing_asof/held_through_listing_end 0건** — price_integrity PASS, status `point_in_time_approx`(rank 2)로 승격.
    **최종 등록**: earnings_conviction+se_momentum → `cmb_9037383a2bfc`, **135.93%** 정식 등록(무결성·안정성 통과). earnings_conviction+recovery+se_momentum은 tiebreak_stability에서 **정당하게 거부**됨(단일경로 249.6% vs 8회 무작위 최댓값 229.1%/평균193.4%/CV11.9% — 타이브레이크 운으로 판정, allow_path_luck 강제통과 안 함 — 이 조합의 정직한 기대값은 193%대). 짧은 회귀 백테스트(2024.06~2025.05)로 se_momentum 수정본 크래시 없음 재확인.

- ⚠️ **2026-09-09 국내 주가 ±30% 상하한가 전수조사(사용자 지시 "상장일 제외 30% 상한가 규정을 벗어나는 게 없는지 전체 조사, 백테스트 매수/매도 겹치면 전수조사")** — 세션 최대 규모의 데이터무결성 발견.
  **[1] 기존 감사 시스템 자체의 사각지대 확인**: `price_jump_audit`(`scripts/audit_price_jumps_and_build_canonical.py`)의 탐지 문턱이 `ratio>1.8 또는 <0.55`였음 — 실제 규정(±30%, ratio 1.30/0.70)보다 훨씬 느슨해 **+30%~+80%/-30%~-45% 구간 전체가 감사망에서 빠져 있었음**.
  **[2] 실제 규정(0.70~1.30 밖, 상장일=종목별 최초관측일 제외)으로 전수조사**: 원시 발견 5,039건(지수/매크로 제외) → 기존 감사 테이블에 이미 있음 2,766건 → 확정 기업행위인데 감사 테이블 미등재 17건 → **완전 사각지대 2,256건**. 이 중 429건은 갭 10일 초과(수년치 데이터공백 후 재개 — 진짜 하루 위반 아님, 데이터 커버리지 문제로 재분류)이고 나머지 1,827건이 진짜 검토대상.
  **[3] 두 가지 서로 다른 근본원인을 실측으로 확정**:
  (a) **배치 이음매 가격기준 불일치**: `price_history.created_at`을 대조한 결과, 예를 들어 000700/003490 등 2018-12-24~2019-01-02 경계, 207940(삼성바이오로직스) 2022-01-03 등에서 **경계 앞뒤 날짜의 created_at이 서로 다른 배치 실행 시각**임을 확인 — 한쪽은 정수 원시종가, 다른 쪽은 소수점 정밀도의 수정주가(예: 207940 2022-01-03만 911,000 정수값, 앞뒤 며칠은 1,373,812~1,350,991 소수점값)로 서로 다른 가격기준을 쓰는 별개 백필 작업이 이어붙여지며 생긴 가짜 점프. 2018-12-24(72종목)/2019-01-02(128종목)/2022년 1~5월 여러 날짜(각 13~58종목) 클러스터가 전부 이 패턴과 일치 — 같은 날 수십~수백 개 무관 종목이 동시에 규정위반 수준으로 움직인 것 자체가 실제 시장현상일 수 없다는 논리로 최초 의심, created_at 대조로 확정. **어느 쪽 값이 맞는지는 이 세션에서 확정 못함 — 외부 검증 필요, 미해결**.
  (b) **보통주/우선주 데이터 혼입(확정·수정 완료)**: 001720(신영증권) 2022년 1~3월 급등락 7건 중 6건이 001725(신영증권우, 우선주)의 **같은 날짜 가격대와 거의 일치**함을 확인(예: 001720 2022-02-21=63,000원 vs 001725 같은날=63,300원, 반면 001720의 정상 추세는 45,000~48,000원대) — 우선주 가격이 보통주 행에 잘못 섞여들어간 명백한 데이터 오염. **6개 오염 행을 price_history에서 삭제**(조작값으로 채우지 않고 결측 처리 — 기존 세션 전체의 "빈 값은 직전가로 폴백" 관례와 일치).
  **[4] 수정 효과가 실측으로 컸음**: `golden_cross` 재백테스트 결과 **001720 매매 자체가 통째로 사라짐**(오염된 가격이 골든크로스 진입신호를 가짜로 만들어냈던 것 — profit_amt 552,777원짜리 있지도 않았을 거래) — 전체 avg return 102.36%→**85.33%**(연속 2020~2026), 이미 등록했던 `golden_cross_recovery` 조합도 재검증해 155.47%→**107.60%**(`cmb_4ccab6fc6785`)로 재등록(-48%p, 상대 -31%). 종목 1개의 6개 행 수정만으로 이 정도 낙폭 — 이 세션에서 발견한 다른 어떤 로직버그보다 조합 수익률에 미친 영향이 컸음.
  **[5] 나머지 사례**: 등록된 6개 전략 거래와 겹치는 17건 중 001720(6건)은 위에서 수정. 나머지(003000/207940/079160/073570/067170/476830/099320/042660/006740)는 대부분 ratio가 1.30~1.32 근처에 몰려있어 **진짜 상한가(정상)일 가능성**과 (a)의 배치이음매 아티팩트일 가능성이 공존 — 외부 검증 없이는 어느 쪽인지 확정 못해 손대지 않음. 오늘(2026-09-09) 발생한 3건(273060/001290/121850)은 전부 거래정지 재개·정리매매(코이즈는 상장폐지 사유로 정리매매 중, 규정상 가격제한 없음)로 확인된 정당한 케이스.
  **미해결 과제로 남김**: (i) 배치이음매 근본원인(어느 배치가 언제 어떤 가격기준으로 재적재했는지 데이터 파이프라인 이력 추적), (ii) 나머지 ~1,800건(1.30~1.32 근접 다수 포함)의 개별 검증, (iii) 001720류 보통주/우선주 혼입이 다른 종목쌍에도 있는지 전종목 스캔.

### 2026-09-08 섹터 로테이션 2대 전략(추세추종 집중/탈출 + 낙폭과대 반등) + 메인페이지(macro 탭) 신호 카드 신규
- 사용자 요청으로 `routes/sector_rotation.py`에 전략1 보강(ENTRY_NOW 중 최고점 1개를 `primary_focus`로 스포트라이트, 12주RS 양호→4주RS 급락 시 `exit_alerts`)과 전략2 신규(`_score_bottom_reversal` — 낙폭과대+반전조짐+수급전환+밸류트로프 4요소, `BOTTOM_ENTRY`는 반전조짐이 실제 점수를 받았을 때만 승격)를 추가, `GET /api/sector-rotation/dashboard-summary`로 통합(캐시 키 추가, 상세는 섹션 3 참조). Postgres 라이브 데이터로 검증: 바이오는 낙폭은 크지만(-44%) 4주RS가 여전히 자유낙하(-21.5%p)+수급도 순매도 전환이라 `NONE`(반등 신호 아님)으로 정확히 걸러짐, 화장품/뷰티가 전략1 `primary_focus`로 정상 포착됨.
- `frontend/src/views/SectorSignalSummary.jsx` 신규, `MacroDashboard`(macro 탭, App.jsx) 최상단 `<SignalBoard>` 바로 다음에 삽입 — "지금 집중할 섹터" 또는 "관망(현금 보유) 시기" 카드. Browser 도구로 실제 macro 탭 렌더 확인 완료.

### 2026-09-08(2차) 아시아 외국인 자금흐름 신규(`global_foreign_flow` 탭) — 실측 조사 결과 국가별 접근성 크게 갈림
- 사용자 요청("외국인 자금이 아시아에서 빠지고 있다는데 확인할 화면")으로 한국/대만/일본/중국/인도 5개국 외국인 순매수를 `global_macro_data`(기존 범용 시계열 테이블) 재사용해 통합. **실측 결과 예상과 다르게 갈림**: 사전 WebFetch 조사에선 대만 TWSE가 "즉시 JSON 반환"으로 확인됐으나 실제 이 Mac 네트워크로는 WAF 307 전면 차단(도구별 네트워크 경로가 달라 검증 결과가 달랐음), 반대로 WebFetch에서 ECONNRESET 났던 인도 NSDL은 이 Mac에서 정상 200 + USD 백만달러 값을 이미 계산해서 제공(가장 쉬운 소스였음). 최종: 한국(price_history 집계)·인도(NSDL HTML 파싱) HIGH신뢰도 실가동, 대만 BLOCKED, 일본(e-Stat, ESTAT_APP_ID 필요) 및 중국(HKEX 북향자금, net-flow 엔드포인트 미확정) PENDING — 상세는 섹션 3 `routes/global_foreign_flow.py`, 섹션 9 알려진이슈 참조.
- `routes/global_foreign_flow.py`(`/summary`,`/history`) 신규, `frontend/src/views/GlobalForeignFlowView.jsx` 신규 탭 등록. 수집은 기존 `_loop_global_macro_daily`(매일 06:45)의 `scripts/ops/collect_global_macro_daily.py`에 3단계 추가(신규 스케줄러 잡 없음). 대만/일본/중국은 화면에 "데이터 없음 + 사유"로 정직하게 표시(가짜 값 없음).

### 2026-09-08(3차) TIC(미 재무부) 국가별 대미 주식 양자간 흐름 추가 — "아시아→미국 이동" 가설을 직접 검증하는 데이터로 확장
- 사용자가 "유럽/미주 등 시총 상위 국가 전체로 확장" 요청 — FRED가 미러링하는 TIC `FORLTEQTYNET*` 시리즈(그 나라 투자자의 미국 주식 순매수)가 20개국/지역 전부 실측됨을 확인(전세계·아시아합계·유럽합계 + 아시아 7개국·유럽 5개국·아메리카/MEA 4개국). 기존 KRX 스타일 개별 거래소 스크래핑과 달리 차단 리스크 없이 한 번의 API로 커버 — 향후 유사 확장은 이 방식 우선 검토.
- `collectors/tic_bilateral_flow_collector.py` 신규, `routes/global_foreign_flow.py`에 `us_inbound` 섹션+`/us-inbound-history`+`observations`(①자국유출·②아시아TIC유입 방향 일치 시 자동 관찰문, 인과 단정 안 함) 추가. 실측(2026-06 3개월 합): 한국 +$25.1B·홍콩 +$10.3B·대만 +$5.9B 미국행인 반면 **일본은 -$8.0B로 순매도** — 아시아 내에서도 국가별 방향이 다름을 확인.

### 2026-09-09 일본 자국시장 외국인 순매수 수집 완성 — e-Stat 대신 재무성 공개 CSV로 전환
- 사용자가 e-Stat 앱ID를 발급해 실제 조회해보니 "対外及び対内証券売買契約等の状況" 통계가 e-Stat 포털에 없음을 확인(검색 0건, API 자체는 정상 동작 — "人口" 등 다른 키워드는 25,022건). 대신 재무성이 인증 없이 직접 공개하는 CSV(`mof.go.jp/.../week.csv`, 2005년~현재 주간, 지연 약1~2주)를 발견해 `collectors/mof_japan_flow_collector.py`를 이쪽으로 전면 재작성 — e-Stat보다 더 간단하고 안정적(대내증권투자 "주식·투자펀드지분" 순매수 컬럼만 사용). `ESTAT_APP_ID`는 `.env`에 보관(향후 JP_CPI/JP_GDP 등 다른 e-Stat 확장용, 이 수집기는 미사용).
- `JP_USD_JPY` 등 FX 환산용 시계열을 기존 120일치→약 4년치로 1회성 확장 백필(`collect_yahoo_macro(lookback_days=1500)`)해 MOF 주간 히스토리와 정합. 자국시장 외국인 순매수 3/5개국(한국·일본·인도) HIGH 신뢰도로 실가동 확인.


### 2026-09-09(2차) 한일 선행지표 매칭 — 구축 직후 백테스트에서 전부 기각, 기능 삭제
- 일본 기계수주(내각부, e-Stat) 업종별 발주액을 삼성전자/SK하이닉스/POSCO홀딩스/현대모비스/HD한국조선해양과 엮는 7개 페어(`jp_kr_indicators` 탭)를 만들었으나, 사용자 지시("백테스트 해보고 의미가 없다면 삭제해")로 2005~2026 월간 데이터 전체를 Pearson 상관+정규근사 p-value+부호적중률로 검증(narrow/누적수익률, YoY/MoM 총 4가지 변형으로 관대하게 재검증 포함).
- **결과: 7페어 전부 기각.** 12/14 조합은 |r|<0.15·p>0.09(사실상 무관), 나머지 2개(전기기계→삼성전자·SK하이닉스, YoY)는 p<0.01로 유의했으나 **부호가 정반대**(r=-0.21, 적중률 43~44%=동전던지기보다 나쁨) — "일본 반도체장비 공급망 의존→선행지표" 스토리가 실제 데이터로 뒷받침되지 않음. `collectors/jp_machinery_orders_collector.py`/`routes/jp_kr_indicator_pairs.py`/`frontend/src/views/JpKrIndicatorsView.jsx` 및 관련 배선(main.py, App.jsx, collect_global_macro_daily.py, global_macro_categories) 전부 삭제, `global_macro_data`의 `JP_MACHORDER_*` 데이터도 정리.
- **교훈**: 그럴듯한 산업 스토리(공급망 의존, 국제적으로 통용되는 매크로 지표)만으로 페어를 확정하지 말 것 — 배포 전 반드시 보유 히스토리로 상관관계·부호·유의성을 검증한다. 후속으로 유사한 매칭 아이디어가 나오면 이 백테스트 방법론(월간 리샘플+Pearson+정규근사 p-value+부호적중률)을 재사용할 것.

### 2026-09-09(3차) blindspot_audit_20260909 후속 — 가격 무결성 게이트 4개 우선조치 수정 + 운영 PostgreSQL 적용
- 읽기전용 감사(`research_outputs/blindspot_audit_20260909/findings.md`)가 지적한 것 중 4개를 코드 수정 후 실제 운영 DB에 적용: ①확정 기업행위가 외부(Naver) 일치만으로 `return_usable=1`로 뒤집히던 오버라이드 차단(`verify_price_history_with_naver.py` PROTECTED_FROM_OVERRIDE) + 미확정 기업행위가 raw-source 일치로 새는 구멍 신규 분류 `corporate_action_pending_confirmation` 추가(`audit_price_jumps_and_build_canonical.py`). ②낡은 외부 검증 재사용 차단 — `input_fingerprint`(SHA256, classification 제외) 도입해 비교기간/가격이 바뀌면 자동 재검증(036220 사례 확인·수정). ③`price_integrity.py`(미커밋 신규 모듈, `native_script()`로 Postgres DDL 스킵 우회)를 실제 실행해 운영 PostgreSQL에 `canonical_price_history_v`/`canonical_price_returns_v`/`price_trading_calendar`/`price_integrity_quarantine` 등을 처음으로 생성(`scripts/apply_price_integrity_schema.py` 신규) — 이전엔 전부 부재. ④`scripts/audit_selected_strategy_price_integrity.py`(선택전략 26종 게이트)가 좁은 `price_jump_audit` 대신 이 canonical view를 보도록 수정.
- **중요 부작용**: 위 ④ 재실행 결과 선택된 26개 전략 전부에서 최소 1개 이상의 오염된 보유기간이 새로 발견되어(비율은 낮음, 0.8~16%) `price_integrity` 아티팩트가 전부 fail로 전환됨 → `routes/backtest.py` 스위트 상태가 `legacy`로 강등. 전부-아니면-전무 통과기준을 유지할지 임계값을 둘지는 정책 결정 필요(미결정, 임의 변경 안 함).
- 대한항공(003490)/005440의 2018-12-24 접합 오류는 가격을 직접 고치지 않고(`price_series_registry`의 "naver_price_history_backfill로 price_history 덮어쓰기 금지" 정책 준수) ±30% 문턱 자동차단(unexplained_jump)으로만 해결 — 802건대 ±30% 미만 접합 후보·2022년 클러스터는 미해결. 상세·재현 명령·남은 과제는 [docs/CLAUDE_CHANGELOG_20260909_price_integrity_gate_fix.md](docs/CLAUDE_CHANGELOG_20260909_price_integrity_gate_fix.md).

### 2026-09-11 접합(splice) 오류 363종목·1,435행 복구 + 802건 후보 대부분 정상판정 + 2022년 클러스터 근본원인 확인
`scripts/apply_20181224_splice_repair.py` 신규 — 003490/005440과 동일 패턴(naver 대비 수년 정확일치 후 좁은 구간만 안정적 비율로 이탈)을 보수적으로 선별해 340종목/1,343행 복구(`run_id=splice_repair_naver_backfill_20260911_202212`). naver_price_history_backfill을 2019-02-28까지 확장 수집한 뒤 재검증해 극단비율(0.3~3.0배 밖) 125건 중 재수렴 확인된 22건 추가 복구(`apply_20181224_splice_repair_extreme.py`) + 2019-01-02 경계 802건 후보 정밀 재검토 — 대부분 정상 시장변동(연말효과)으로 확인되고 신규 1건(227100)만 동일 패턴 확인, 총 363종목/1,435행. composite 오염비율 3.36%→3.07% 개선.
⚠️ **부작용**: `audit_price_jumps_and_build_canonical.py`가 설치하는 PostgreSQL 트리거(`price_history_basis_write_guard`)가 다른 세션의 동시 KRX 결측일 백필 쓰기를 대량(160만건+) `unverified_historical_write`로 격리 — 데이터 손상 아니라 설계대로 동작하는 것이나, canonical view `return_usable` 해석에 영향. 검증/승격 절차는 미결정, 사용자 지시로 보류.
**2022년 클러스터(09-09 findings.md 미확정 항목) 근본원인 확인**: 2022-01-03 등 다수 날짜에서 이웃 날짜와 다른/NULL `created_at`으로 배치접합 확정(2018-12-24와 동일 부류, 최소 2개 하위패턴). 검증 인프라(naver 확장수집) 미비 + 동시 세션 부하로 이번 세션에서 수정은 보류, read-only 조사만 완료. 상세는 [docs/CLAUDE_CHANGELOG_20260909_price_integrity_gate_fix.md](docs/CLAUDE_CHANGELOG_20260909_price_integrity_gate_fix.md) 5번, [docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md](docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md). 26개 전략 zero-tolerance 정책결정은 여전히 미해결.

### 2026-09-12 Codex 가격무결성 재점검 교차검증 + 레거시 writer 6곳 write-guard 호환 수정
Codex가 09-12 아침 write-guard를 AFTER(관찰)→BEFORE(차단) 트리거로 강화하고, 096640/2022-01-03 오포함 1행 원복, 전체 4,669종목 스냅샷 드라이런(2,749종목 ready/287만 행 후보, 미적용) 등을 완료(`research_outputs/price_integrity_recheck_20260912.md`). Claude가 운영 DB에서 직접 재검증해 전부 일치 확인(불일치 0건).
전체 코드베이스에서 `price_history` OHLCV를 쓰는 모든 경로(~40개 파일)를 훑어, 새 BEFORE 트리거를 통과 못하는 미검증 레거시 writer 6곳을 발견·수정: `collect_krx_history.py`(gap-fill 전용 신규 `price_integrity.gate_gap_fill_row()` 연결), `collect_krx_investors.py`/`collectors/krx_investor_collector.py`(수급전용 0-OHLCV 플레이스홀더), `scripts/backfill_delisted_naver_prices.py`, `scripts/audit_and_repair_postgres_data.py`, `scripts/ops/audit_and_repair_core_data_quality_20260624.py`(검증조건이 SQL WHERE에 내장된 배치 repair 2건). 스케줄러 정기잡(`_job_krx_daily`/`_job_supply_daily`/지수수집)과 `collect_kis_ohlcv.py` 등은 이미 안전함을 확인(오늘 날짜만 쓰거나, 비6자리 지수/해외종목이거나, OHLCV 비관련 컬럼만 갱신). 신규 단위테스트 5개 추가(`tests/test_price_integrity.py`, 총 31개 통과), 운영 DB 트랜잭션 롤백 테스트로 정상/비정상/대조군 케이스 전부 검증. 하드코딩된 2026-04 1회성 패치 스크립트 6개(`patch_data*.py` 등)는 의도적으로 손대지 않음(이미 실행 완료된 과거 기록으로 판단). 상세는 [docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md](docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md).

### 2026-09-12(2차) "2022년 클러스터"가 2018~2026년 전체에 걸친 반복 문제였음을 확인·복구 + 26개 전략 게이트 정책 결정(임계값 도입)
Codex가 캐시해둔 전종목 완전 naver 이력(`research_outputs/price_snapshot_repair/20260911T200447/`)으로 재조사한 결과, "2022년 클러스터"는 실제로는 2018~2026년에 걸쳐 개별 종목이 무관한 날짜에 반복적으로 산발적 접합오류를 내는 더 큰 문제였음. 최초 스캔 로그는 471건이지만 복구 후 같은 파일을 덮어써 원본 후보 집합은 현재 재현 불가. 확정 매니페스트는 154종목·246건·312행, 실제 적용은 유효성 검사 통과 310행이다. **Codex 재검증에서 확정 로직 결함 발견**: 공급자간 가격비율을 일일 가격제한폭과 비교한 91건은 그 자체로 오류 증명이 아니며, 전일 종가 동일 155건에도 거래정지/무거래가 섞였다(거래량 0인 행 117개, OHLC 0인 행 57개). 기업행위 ±3일 제외도 실제 코드는 당일만 검사해 4개 에피소드가 통과했고 000520에는 분할 공시가 있었다. 스캐너/적용기에 실제 ±3일+DART 공시 veto, 매니페스트 해시 고정, 원천행 재검증을 추가했으며 기존 적용행은 백업을 유지한 채 충돌 행을 재검토 대상으로 분류했다. 2026-07-20은 휴장일이 아니라 2,784개 가격행·2,663개 거래량 양수 행·핵심지수 4개가 있는 거래일이었다. 그날 `coverage_gap` 2,500건은 07-17 수집 공백의 파급이다.
**26개 전략 zero-tolerance 정책 결정**(사용자 지시로 Claude가 3가지 옵션 중 선택): 오염비율 5% 임계값 도입(`PRICE_JUMP_CONTAMINATION_THRESHOLD`, `scripts/audit_selected_strategy_price_integrity.py`) — 단, survivorship 결함(상장기간 밖 매매)은 전략 로직 결함이라 계속 전부-아니면-전무 유지. 최초 구현은 distinct 보유윈도우 분자를 원시 윈도우 분모로 나눠 3개 전략의 비율을 낮췄다. 분모도 distinct로 수정했고 결과는 26개 중 19개 통과, 실패 7개로 동일하다(`deep_recovery` 9.47→9.86%, `extreme_dd_volume` 12.80→13.21%; `se_momentum`은 분모 보정 2.61→2.64%, 의심행 격리 반영 후 2.78%). 실패 4개는 오염초과, 3개(`regime_adaptive`/`v_trend`/`v4`)는 survivorship 결함이다. 상세는 [docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md](docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md), [docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md](docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md).
**Claude 교차재현(2026-09-12(3차))**: 위 Codex 재검증 전체(38테스트/격리30건/매니페스트 312행 분류/000520 공시/07-17=189행 부분수집·07-20=2,689행 정상거래/전략 19통과)를 운영 DB에서 직접 재현, 불일치 0건. 6개 미완료 항목을 핸드오프 문서 6번 항목에 정리.

### 2026-09-12(4차) 미완료 6개 항목 중 3개 실제 해결 (사용자 지시)
①**07-17 재조사·수정**: KRX 승인API·Naver 둘 다 07-17 데이터가 전혀 없음을 재확인(진짜 휴장일 — 이전 세션의 "휴장 아님" 철회를 다시 뒤집음). 원인은 DB의 191개 국내종목 행이 07-16과 종가·거래량까지 완전 동일한 stale 복제였던 것 — 진짜 거래 흔적이 전혀 아니었다. `scripts/remove_stale_20260717_duplicate_rows.py`로 백업 후 삭제(지수/해외 20행은 정상이라 제외), 재구축 결과 07-20 coverage_gap이 종목마다 들쭉날쭉하던 것에서 전종목 일관되게 2,689건으로 정정됨. ②**26개 전략 임계값 재보정**: 오염비율을 정렬해보니 5.91%↔9.86% 사이에 3.95%p라는 압도적 단절이 있어(원래 5%는 이 단절이 아니라 통과권 안에 우연히 걸림) `PRICE_JUMP_CONTAMINATION_THRESHOLD` 0.05→0.07로 재보정 — 26개 중 22개 통과(golden_cross/low_base_breakout 추가 통과). ③**regime_adaptive survivorship 근본수정**: 11건 전부 00088K/00104K/00279K 등 영숫자 우선주 코드였고 `security_master.py`가 순수 6자리 숫자만 매칭하는 GLOB 패턴을 써서 `security_master_history`에 아예 누락돼 있었음(main.py `_is_kr_code` 버그와 동일 부류) — `~ '^[0-9A-Z]{6}$'`로 4곳 교체 후 테이블 재구축(82종목 추가, 4,366→4,873행), regime_adaptive 통과 전환. ④**v4 survivorship 원인 확정(미수정)**: 282690 사례에서 청산가 13,500원이 상장폐지 80일 전 마지막 관측종가(거래량0으로 정지된 채 반복)와 정확히 일치 — 공유 백테스트 엔진이 상장폐지 후 시세부재를 감지 못하고 마지막가를 forward-fill해 "정상청산"처럼 보이게 만드는 것으로 확인. 파급범위가 다른 전략과 공유될 가능성이 높아 이번엔 수정하지 않고 증거만 확정(v_trend 2건은 이미 시세부재 전액손실로 정확히 모델링 중이라 버그 아님). 나머지 3개(기업행위veto 확장·225건 착수·287만행 적용)는 새 데이터수집/공유엔진 변경이 필요해 미착수. 상세는 [docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md](docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md).

### 2026-09-13(2차) 자료 추가수집으로 "추가검토필요" 216건 중 183건 확정·복구(225→33건으로 축소)
사용자 지시로 DART 공시에서 거래정지/관리종목/투자유의/상장적격성/불성실공시 관련 데이터(1,581종목·8,225건)를 발견 — 새 수집기 없이 기존 `dart_disclosures`로 항목1 veto 확장 가능함을 확인했으나 216건 중 2건만 설명(halt 무관 원인 다수). 대신 "naver 대비 1.0±2%"가 아니라 **종목별 고유 기준비율**(직전5일 1%이내 안정+이후5일 2%이내 재수렴+±3일 기업행위·DART무충돌)로 216건 전체 재검증 — **183건(85%) 확정·복구**: 2022-01-03 추가 22건(created_at=2026-04-15 배치서명 확인, 005380/005930과 동일원인), **2025-09-18/19 신규 클러스터 65/66건**(이번 세션 최초 발견, created_at=2026-04-25 배치서명 — 2022년과 다른 별도 사고), 2022-05-09 23건 전체, 나머지 산발 69/71건(2020-03-09 COVID급락일 15건 중 11건 포함 — 재수렴 기준으로 실제변동과 안전하게 구분). 33건은 재수렴 실패/기준비율 불안정으로 정직하게 미해결 유지. 총 5개 스크립트로 183행 적용(백업+data_fix_log 동일패턴), 재구축 후 표본 재분류 전부 `normal` 확인, 43개 테스트 유지, 26개 전략 재감사는 통과22/실패4로 불변(오늘 고친 날짜가 우연히 겹치지 않았을 뿐).

### 2026-09-13 미완료항목 추가해결 — 2022-01-03 4종목 기준비율 접합오류 복구 + v4 재실행 시도로 assert_research_prices 차단 재확인
①**000300/001080/005380/005930의 2022-01-03 오류 복구**: 기존 반복접합 스캐너는 "naver 대비 비율이 1.0±2%"만 정상으로 인정했는데, 이 4종목은 naver와 원래부터 1.0이 아닌 자기 고유 기준비율(각각 0.3828/1.0304/0.8297/0.9119배, 아마 두 소스 간 과거 조정기준 차이)로 거래돼 한 번도 후보로 안 잡혔음. 01-03 하루만 그 기준비율에서 이탈했다가 바로 복귀하는 명백한 접합오류였음을 직접 확인(전후구간 0.5% 이내 안정, ±3일 기업행위/DART 없음) — `scripts/apply_2022_baseline_offset_repair_20260913.py`로 naver값×기준비율 4행 복구(백업+`data_fix_log`). ②**v4 재실행 시도 — Codex(6차)의 `assert_research_prices` 차단 발견을 독립 재현**: v4의 `_final_liquidation_quote` 청산버그는 Codex가 이미 코드 수정(6차 항목 참조)했으나, 재등록 시도 시 v4가 시총1000억+ 전체 유니버스(~2,000종목)를 검사해 잔여 오염(coverage_gap 17,041건 등)과 반드시 충돌함을 직접 재현 확인. 같은 기간으로 `composite`(좁은 유니버스)는 정상 성공 — "유니버스가 넓을수록 재실행이 막힌다"는 구조적 패턴을 교차검증. 전체 43개(+기존 스위트 총 195개) 테스트 통과. 상세는 [docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md](docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md).

### 2026-09-12(6차) Codex 후속 검증 — v4 청산 결함 수정, 7% 정책을 잠정화
- 운영 DB에서 07-17 국내종목 0행, 삭제 백업 `remove_stale_20260717_20260912_151404` 191행(모두 new 값 NULL), `00088K` tradable master 반영을 재확인했다.
- `backtest_common._run_portfolio`의 기간종료 청산이 최종 시장일의 종목별 시세가 없을 때 과거 마지막 종가를 쓰던 결함을 수정했다. 이제 0원 회수와 `기간종료(시세부재 전액손실)`을 기록하고 마지막 equity도 거래원장과 맞춘다. 직접 호출처는 `backtest_strategies/base.py` 한 곳이며 이 경로의 영향 전략은 v4다(v5는 `_run_generic_backtest` 사용). 합성 통합테스트로 282690 유형을 재현해 검증했다.
- 당시 운영 v4 동일기간 재실행은 `assert_research_prices`가 미해소 가격오염을 엄격 차단해 시작 전에 중단됐다. 이 차단을 우회하면 선택편향이 생기므로 기존 v4 성과 수치는 갱신하지 않았다. 후속 상태는 `hermes.md`의 2026-09-22 재검증 기록을 따른다.
- `security_master.py`의 PostgreSQL 정규식 수정은 custom SQLite DB rebuild 경로에서 문법 오류가 나는 회귀가 있었다. 백엔드별 exact 6자 영숫자 predicate로 분리하고 SQLite 테스트를 추가했다.
- 7%는 현재 26개 전략 분포로 맞춘 값이라 `price-window-v2-provisional-7pct`로 명시했다. 5~7%는 review warning이며 현재 golden_cross 5.28%, low_base_breakout 5.91% 두 전략이다. 감사 산출물에 0/3/5/7/10% 민감도(1/16/20/22/23개 통과)를 기록한다.
- 관련 단위/계약 테스트 47건 통과(가격무결성 43 + 백테스트 정적계약 4), py_compile 통과.

### 2026-09-11 ETF KRX 공식 PDF 파이프라인(`full_pdf_collector.py`) — 9/10 ISIN 코드 회귀 근본수정 + 1,168개 전량 재정규화 (Codex 작업 이어받아 완료, 아직 미커밋)
> 이 파이프라인은 `routes_etf.py`/프론트(`etf_inclusion_daily` 기반 tab1~4)와 **아직 연결되지 않은 별도 검증용 파이프라인**이라 사용자 화면에는 영향 없었음(2026-08-30 기록 그대로 유효).
- **근본원인**: KRX가 2026-09-10부터 일부 ETF 구성종목의 `COMPST_ISU_CD`를 6자리 종목코드 대신 ISIN(`KR7...`)으로 내려주기 시작(시장구분/평가금액도 일부 공란) → 기존 파서가 이를 해외자산으로 오인해 국내 종목 편입개수가 실제보다 적게 계산됨(예: 삼성전자 -5개, SK하이닉스 -7개). 80종목 표본 검증에서 ETF-Check 대비 개수 일치율이 73.75%(기준 80%)로 미달되며 발견.
- **수정**(`ETF_check/full_pdf_collector.py`): ①`resolve_isin_codes`/`resolve_all_isin_codes` — 과거(직전일 이전) 스냅샷의 `raw_json.COMPST_ISU_CD2`(ISIN) ↔ 6자리코드가 1:1로 유일했던 이력만으로 ISIN→코드 역매핑 캐시 생성(모호한 다대다 매핑은 제외), `collect()`는 실행당 1회 전체 캐시. ②`response_quality_issue` — 전일 대비 구성종목 수가 절반 이하로 급감 **and** 비중합계<80%인 "절단된 PDF" 응답을 빈 응답과 동일하게 실패 처리(정상 리밸런싱과 구분하기 위해 두 조건 AND로 보수적으로 설정). ③빈/저품질 응답도 재시도(최대 3회, 기존엔 즉시 종료) + `save_failure`가 실패 시 해당 (날짜,티커)의 기존 구성종목 행을 삭제하고 `assess_and_publish`가 불완전한 날짜의 과거 `etf_pdf_full_publication` 행도 취소하도록 수정(부분 실패가 "완료"로 잘못 발행되는 것 방지).
- **복구**(`ETF_check/repair_full_pdf_snapshot.py` 신규): 보관된 raw gzip 원문을 재판독해 새 로직으로 전량 재정규화하는 감사 가능한 스크립트. 실행 전 `ETF_check/backups/etf_check.pre_pdf_repair_20260911.db`로 SQLite 온라인 백업 확보. 최초 버전은 ETF마다 과거 전체 이력을 재스캔해 O(n²)로 30초+ 소요 → 날짜 단위 ISIN 캐시 1회 계산으로 변경 후 4초로 개선. 결과: 2026-09-10 1,168종목 중 **1,156개 정상 복구**, KRX 원문 자체가 절단된 12개는 실패 상태로 정확히 격리(자동 확정 금지 원칙 준수) → 재수집으로 11개 추가 회복.
- **잔존 미해결 1건**: `435420`(TIGER 미국나스닥100채권혼합50) — KRX가 3회 재시도 모두 109개 중 52개만 반환(원문 자체 절단, 파서 문제 아님). 미래에셋 TIGER 공식 사이트(`investments.miraeasset.com/tigeretf/.../pdfListAjax.ajax`)로 보완 가능한지 확인했으나 **해당 사이트는 총 51개 구성종목만 반환**(KRX 109개와 이유 불명 불일치, 만기/평가일 정의 차이 추정) — 원인 미규명 상태에서 이 수치로 강제 보정하면 새로운 오류를 만들 위험이 커서 **보류**. `ETF_check/issuer_pdf_fallback.py`(기존, PLUS운용 489010만 지원)에 미래에셋 어댑터를 추가하는 방향이 유력하나, 결과는 항상 별도 `etf_pdf_issuer_fallback`/`etf_pdf_issuer_component` 테이블에만 저장하고(원본 `etf_pdf_full_snapshot`은 절대 덮어쓰지 않음) `effective_date`를 있는 그대로 기록하는 기존 설계를 유지할 것. 이 파이프라인이 크론/스케줄러에 아직 연결되지 않아 자동 재시도가 없으므로, 다음에 수동 재수집(`python3 full_pdf_collector.py --date 20260910 --stock 435420`) 시도 시 KRX가 정상 응답하는지부터 먼저 확인할 것.
- **검증**: 국내종목 기준 동등성 재검증(80종목 표본) — ETF 개수 일치율 100%, 편입종목 교집합 100%, 편입금액 상관계수 0.9983, 총액 차이 0.16%. `tests/test_etf_full_pdf_collector.py` 7건 전체 통과(unittest, `venv/bin/python3.11 -m unittest tests.test_etf_full_pdf_collector`).
- **상태**: 코드 3개 파일(`full_pdf_collector.py`, `repair_full_pdf_snapshot.py`, `tests/test_etf_full_pdf_collector.py`) 및 DB 복구 모두 `runtime/` 워크트리(`claude/sqlite-migration-completion-x0h891` 브랜치)에 반영됨, **아직 git commit 안 됨** — 커밋 여부는 사용자 확인 후 진행.

### 2026-09-12 ETF 435420 재검증 및 화면 게시 완료 (Codex)
- KRX의 52개 응답을 TIGER 공식 `pdfListAjax.ajax`와 재대조했다. 공식 자료는 51개, 비중합 100.02%, 국내 6자리 종목 0개이며 KRX는 공식 자산에 설정현금액 한 행이 추가된 구조였다. 기존 절단 판정은 정상 대규모 리밸런싱을 오탐한 것으로 결론냈다.
- `issuer_pdf_fallback.py`에 TIGER 공식 어댑터와 원문/해시/총건수/국내종목 검증을 추가했다. KRX 실패 상태는 유지하고 국내 편입 계산에만 검증된 해외전용 ETF 예외를 허용했다.
- 20260911 결과: 1,167 KRX 정상 + TIGER 예외 1 = 국내 범위 1,168/1,168, ETF Check 표본 개수/편입 100% 일치, 금액상관 0.9976, 화면용 2,693종목 게시 완료. 20260907~11 최근 5영업일 연속 통과로 `krx_primary` 자동 전환 완료.
- 전환 후 `etf_primary_service.direct_summary()`가 운용사 예외일을 제외하고 20260909로 후퇴하는 문제를 발견해, 최신 `etf_direct_stock_publication`을 우선 선택하도록 수정했다.
- 실패 KRX 응답도 raw gzip과 SHA-256을 남기도록 보강했고 ETF 테스트 50건 및 SQLite integrity check를 통과했다. 실제 `/api/etf-check/etf-list/005930` 응답도 `KRX_KIS_DIRECT`, 기준일 20260911로 확인했다. 상세/복원 절차: `docs/codex_handoff_etf_tiger_fallback_20260912.md`.

### 2026-09-19 ETF Check 외부 수집 중단 및 만기 ETF 품질 게이트 수정 (Codex)
- `465780`의 정상 만기 청산을 실패로 오판하던 문제를 다중 증거 기반 `KRX_MATURITY_WINDDOWN` 예외로 수정했다. 20260915~18을 재평가해 전수 커버리지 100%, 표본 편입개수/교집합 100%, 금액상관 0.9973~0.9976을 확인했고 20260911 포함 최근 5거래일 연속 통과로 `krx_primary`를 복구했다.
- 최신 20260918 데이터를 2,693종목(가격/시총 커버리지 100%)으로 직접 게시했다. 일일 파이프라인의 ETF Check 표본/동등성 단계는 기본 비활성화하고, API의 ETF Check 네트워크 폴백과 `retry_etfcheck_k_sample.sh` 크론을 제거했다. 복원용 DB/크론 백업은 `ETF_check/backups/etf_check.pre_maturity_exception_20260919.db`, `ETF_check/backups/crontab.pre_etfcheck_stop_20260919.txt`이다.

### 2026-09-12(5차) stock.db 상대경로 stray 파일 조사·수정 (source+runtime 양쪽)
사용자 요청("stock.db가 외장 SSD에 정상 관리되는지 확인")으로 전수조사 — 핵심 데이터(stock.db 13.98GB, PostgreSQL 데이터 21GB) 자체는 외장 SSD에 정상 위치했으나, `DB_PATH = "stock.db"` 상대경로 관례로 인한 빈 stray 파일 4건(`/Applications` 2건 내장 디스크 위반 + `runtime/data`, `runtime/frontend` 2건) 발견·삭제(전부 0바이트). source 브랜치 9개 + runtime(이 워크트리) 동일 버그 7개 파일을 `db_utils.STOCK_DB_PATH`(절대경로)로 통일 — runtime 7개 중 6개는 이미 `connect_primary_db()`(Postgres)로 이전 완료된 뒤 방치된 dead code라 삭제, `routes/consensus.py` 1개만 실사용이라 절대경로로 교체. `safe_restart_backend.sh`로 재시작, 무고아 프로세스·HTTP 200 확인. 상세는 섹션 9 참조.

### 2026-09-14(3차) 핫섹터 파서 — 단순 시황/동향 글은 텔레그램 발송 생략
사용자 지시("기업 상세분석이 아닌 단순 동향 정보면 텔레그램 발송을 삭제") 반영. `Sector_define/blog_parser.py`: AI(Gemini) 파싱 프롬프트에 `is_company_analysis`(true/false) 필드 추가 — 실적/밸류에이션/매수매도 근거 등 구체적 기업분석이 있을 때만 true. 로컬 매칭 폴백은 애초에 이런 판단이 불가능한 단순 substring 검색이므로 항상 `is_company_analysis: False` 고정(바로 위 항목의 알트/아스트 오탐 같은 사례가 텔레그램으로 안 나가게 됨). `run_parser()`가 이 플래그를 확인해 `true`일 때만 `send_telegram()` 호출 + `telegram_sent=1` 기록, `false`면 DB 저장(`sector_posts`/`sector_stocks`)은 유지하되 발송만 생략. 서버 재시작 완료(HTTP 200 확인).

### 2026-09-14(2차) 핫섹터 로컬매칭 오탐 발견·수정 — "알트"/"아스트" 오매칭
사용자 리포트("[신규 섹터 분석] 현재 AI 상황 요약"에서 알트/아스트 추천 근거 문의)로 원문 블로그(`https://m.blog.naver.com/going_tothe_moon/224410153730`)를 직접 확인 — 실제로는 각각 **"샘알트먼"(Sam Altman)**과 **"아스트라"(AI 제품명 Astra)**라는 무관한 단어/인명에 포함된 부분문자열이 종목명으로 오매칭된 것(종목 459550 알트, 067390 아스트와 전혀 무관). 근본원인: `Sector_define/blog_parser.py`의 OpenAI 파싱 실패시 폴백인 `parse_blog_post_local()`이 문맥 검증 없이 `stock_universe` 전체 종목명을 본문에 대해 단순 부분문자열 매칭만 하는 구조적 결함 — 짧은 종목명(2~3자)이 우연히 다른 단어 안에 포함되면 걸러내지 못함. `_FALSE_POSITIVE_PHRASES` 블랙리스트(샘알트먼/알트먼/아스트라/아스트라제네카)를 매칭 전 사전제거하는 방식으로 수정, 동일 포스트 재파싱시 "매칭된 종목 없음"으로 정상화 확인. **재발방지**: 짧은 종목명이 포함된 흔한 인명/제품명이 추가로 발견되면 이 블랙리스트에 계속 추가할 것 — 근본적으로는 형태소 분석 없는 substring 매칭의 한계이므로 완전한 해결은 아님.

### 2026-09-14 남은 가격무결성 32건 외부자료(DART 공시/애널리스트 리포트) 개별 검토 완료
사용자 지시("남은 33건도 다양한 외부소스 검토")로 `remaining_batch_confirmed_20260913.json`의 `"ok":false` 32건 전부를 `dart_disclosures` 전체 공시(용어제한 없이 ±10일) + `report_files`/`analyst_pdf_extracts`(±15일)로 개별 조회. **18건은 실제 공시(실적서프라이즈/신규계약/합병완료/유상증자/2020-03 코로나 급락)로 설명 확인**, 4건은 정황상 개연성만, 13건은 여전히 근거 없음 — **price_history는 이번 조사에서 전혀 수정하지 않음**(가격이 옳다는 근거이지 틀렸다는 근거가 아니므로). 부수 발견: 310840·276920 등 SPAC 5개 에피소드는 애초에 데이터 오류가 아니라 이미 `quality_status='suspended'`로 정확히 격리된 정지마커 행을 `scan_recurring_splice_glitches_20260912.py`가 naver 비율계산에 포함시켜 생긴 **스캐너 자체 오탐** — 해당 스크립트에 정지마커(open=high=low=0,volume=0) 제외 필터 추가(코드만 수정, DB 미변경). 상세 분류표는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 9.

### 2026-09-17~18 제3의 가격 배치오류 클러스터 발견·복구 — 2020-01~2021-02, 112종목 29,998행
사용자 질문("문제 있는 데이터가 더 이상 없는지")을 계기로 `price_jump_audit`를 전수 재점검하다가 216→32건 풀과는 별개인 대형 클러스터 발견. Naver+독립 원시 KRX 테이블(`stock_price_daily`) 2개 외부소스가 서로 일치하면서 price_history와만 불일치하는 `externally_confirmed_internal_corruption` 1,090건 중 481건 우선 복구(run_id `ext_confirmed_corruption_repair_20260917_211837`) 후, 연속된 날짜가 전부 동일 배수로 틀려있어 "전일대비 등락률" 탐지를 통과해버리는 **다일(多日) 구간 오류**임을 확인 — 237종목 전체 2020-01-02~2021-02-17 재대조 결과 62,957 종목-일 불일치. 이 중 실제 분할비율(2x/3x/5x/10x 등)·기업행위 근접으로 설명되는 114종목은 손대지 않고, 2021-02-17 이후 비율이 즉시 1.0으로 복귀하는(=고립된 배치오류 신호) **112종목 29,998행만** `stock_price_daily`로 교체(run_id `window_2020_2021_corruption_repair_20260918_192933`, 사용자가 터미널에서 직접 `--apply` 실행 — 대규모 프로덕션 쓰기라 자동 권한 분류기가 Claude의 직접 실행을 차단함). 창 종료 후에도 0.90~0.95 비율이 지속되는 11종목은 별개의 조정기준 차이로 판단해 제외(원인 미규명, 후속 필요). `data_fix_log` INSERT 9-placeholder/8-value 버그가 새 스크립트에서 또 재발(세션 통산 4회째) — 사용자가 에러 로그를 그대로 공유해줘서 즉시 수정 후 재실행 성공. 적용 후 뷰+감사테이블 전체 재빌드로 정상 전환 확인. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 10 — **미해결로 남은 것**: 제외 11종목 원인규명, 잔여 `mixed_basis_or_price_corruption` 551건 정체 확인, `unresolved_active_common` 7,175건(이번 세션이 다룬 건 이 중 일부에 불과), 26개 전략 게이트 재검증.

### 2026-09-18(2차) phase 2 — 같은 배치오류 811종목 220,822행 추가 발견·복구
바로 위 항목(237종목/112종목)의 "잔여 551건 정체 확인" 후속조사 중, 오염이 237종목 패밀리에 국한된 게 아님을 발견 — `stock_price_daily` 비교 가능한 전체 2,416종목(634,365행)을 동일 3단계 방법론(분할비율 제외+기업행위 제외+창 이후 스냅백 확인)으로 전수 재검사한 결과 **811종목(기존 237종목과 겹침 0)·약 220,822행** 추가 확정. `scripts/apply_2020_2021_window_corruption_repair_phase2_20260918.py`(대상목록: `research_outputs/price_integrity_remediation_20260909/window_2020_2021_phase2_targets_20260918.json`)로 사용자가 직접 `--apply` 실행, run_id `window_2020_2021_phase2_repair_20260918_194551` 정상 반영 확인. `data_fix_log` 9-placeholder 버그가 작성 직후 또 재발(세션 통산 5회째) — 이번엔 사용자 실행 전에 Python으로 튜플 길이를 직접 검증해 사전에 잡아 수정, 실제 실행은 1회에 성공. `rebuild_views()` 재실행 완료 — 이번 3개 배치 합계(481+29,998+220,822=**251,301행**) 중 99.9%(251,024건)가 `quality_status='normal'`로 정상 전환 확인(나머지는 window 첫날 경계효과/신규상장 등 구조적, 추가 오류 아님). **누적**: 2020-01~2021-02 구간 1,048종목 251,301행의 "제3의 배치오류"를 사실상 전량 정리. `price_jump_audit` 전체 재빌드는 이번엔 권한 분류기가 차단해 미완(선택사항, price_history 자체는 이미 정상), phase 2 811종목의 Naver 재검증도 미실행. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 11 — 이번에도 `unresolved_active_common` 등 전체 미해결 풀의 대부분은 여전히 개별 조사 전.

### 2026-09-18(3차) 감사테이블 재빌드+phase2 Naver재검증 완료 — 1,090건 풀 완전 정리 확인
`scripts/audit_price_jumps_and_build_canonical.py` 전체 재빌드 완료 — `externally_confirmed_internal_corruption`(최초 발견 1,090건) 분류가 **0건으로 완전히 사라짐** 확인. 잔여 `mixed_basis_or_price_corruption`(482건) 표본 확인 결과 상위 종목(088980·024110·000480) 전부 실제 유상증자/깨끗한 분할비율로 **의도적으로 제외했던 케이스**였음을 재확인(방법론 정확성 검증). phase 2(811종목) Naver 재검증 완료 — 143건 재분류, `naver_confirms_public_raw`(오염 신호) 단 1건만 남았고 그마저 정상 등락률. 최종 `price_jump_audit` 분포: coverage_gap 17,138 / **unresolved_active_common 7,167(여전히 최대 미해결 풀)** / inactive_or_noncommon_review 2,085 / invalid_ohlcv 1,546 / mixed_basis_or_price_corruption 482 / 기타. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 11.

### 2026-09-18(4차) invalid_ohlcv NULL O/H/L 마커 정규화 1,382행 완료 + unresolved_active_common 6,657행 건별검증(실행대기)
사용자 지시("미해결 종목 계속 해결해")로 두 풀 동시 착수. ①`invalid_ohlcv`(1,546건) 중 1,382건(206종목, 2026-06-15~07-02 집중)이 open/high/low **NULL**(close/volume은 정상)로 저장된 표현버그 발견 — Naver 대조 결과 가격오류 아니라 무거래일 마커가 NULL로 잘못 저장된 것(946건은 close도 일치, 나머지 436건은 close 자체가 조정기준 차이로 판단돼 **손대지 않음**) — open/high/low만 기존 관례대로 0/0/0 정규화. `scripts/apply_null_ohl_marker_fix_20260918.py`, **1,382행 전량 Claude가 직접 적용 성공**(대규모 가격변경이 아니라 표현정규화라 권한분류기 통과), run_id `null_ohl_marker_fix_20260918_202322`. ②`unresolved_active_common`(7,167건)은 클러스터링만으로 판단하지 않고 전건을 Naver와 개별 대조(event_date·previous_date 양쪽) → 분할비율/기업행위 제외 후 **6,657행 확정**(`scripts/apply_unresolved_active_common_naver_repair_20260918.py`) — 대량쓰기라 이번엔 권한분류기가 Claude 직접실행을 계속 차단, **사용자 실행 대기 중**. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 12.

### 2026-09-18(5차) 전체종목 전수스캔 — 대부분 "배치오류 아닌 구조적 미기록 조정기준차"로 판명, 294행만 안전복구
6,657행 반영 후에도 "개별 flagged 날짜만 고치면 연속구간 나머지는 안 고쳐짐" 문제가 재발해, 4,751개 전종목×Naver 전체 히스토리 전수비교 시도. 2% 문턱은 노이즈 폭증(1,000종목만에 112만건)으로 중단, 15%로 올려도 133만건 — 분석 결과 관련종목 대부분(1,320/1,431)이 100일+ 만성적으로 어긋남(`corporate_action_events` 기록 없는 종목도 다수 포함, 즉 이 테이블 자체가 불완전) — **이는 배치오류가 아니라 실제 분할 등을 겪었으나 미기록된 종목들의 구조적 조정기준 차이로 판단, Naver로 치환하면 오히려 위험해 대부분 복구 시도하지 않음**(제대로 하려면 corporate_action_events 보강이 필요한 별도 프로젝트). 비만성 17종목만 추려 스냅백 검증(고립구간 종료후 5% 이내 재수렴 확인) 통과한 **12종목 294행만 최종 복구**, Claude 직접 적용 성공(run_id `final_isolated_naver_repair_20260918_203543`). `data_fix_log` 9-placeholder 버그 세션 통산 7번째 재발 — 이번엔 SQL 문자열 정규식 검증으로도 못 잡혀서(SQL은 맞았으나 실제 Python 튜플이 8개), AST로 `execute()` 호출의 실제 튜플 원소개수까지 파싱하는 검증방식으로 전환 후에야 사전차단. 감사테이블 재빌드는 권한분류기 차단으로 사용자 실행 대기 중. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 13.

### 2026-09-19 감사테이블 재빌드 완료 + invalid_ohlcv 163→38건(97.5% 해결)
뷰+감사테이블 재빌드 재시도 성공(재시도로 권한통과). `invalid_ohlcv` 잔여 163건을 Naver와 개별대조해 2가지로 분리: REPLACE(66건, naver가 실제 다른 유효캔들 보유) / CLAMP(97건, naver가 현재값과 거의 일치 = 두 소스가 같은 내부비일관성에 동의하는 원천데이터 특이값이라 open/close/volume은 안 건드리고 high/low만 내부정합 맞춤). `scripts/apply_invalid_ohlcv_final_fix_20260918.py`로 125건 복구(run_id `invalid_ohlcv_final_fix_20260918_090245`), 잔여 38건(2011~2014년, naver 스냅샷 자체에 데이터 없음)은 검증소스 부재로 보류. `data_fix_log` 9-placeholder 버그 세션 통산 8번째 재발했으나 AST기반 사전검증으로 실행 전 차단 성공(효과 재확인). 최종: `invalid_ohlcv` 163→38(97.5%), `unresolved_active_common` 6,917건으로 여전히 최대 미해결 풀. 상세는 `docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md` 섹션 14.

### 2026-09-19(2차) 가격 외 미해결 항목 처리 — financial_anomalies revenue_zero 29/30건, data_quality_issues ANCHOR_MISMATCH 3/4건 해결
사용자 지시("unresolved_active_common은 넘기고 다른 미해결 문제도 처리")로 가격 무결성 외 영역 착수. ①`financial_anomalies` `revenue_zero`(30건 미해결) 전수 확인 — **26건이 SPAC**(이름에 "~호스팩" 명시 또는 DART corp_name이 "~기업인수목적"), 2건이 상장 전 임상단계 바이오텍(372320 큐로셀, 388870 파로스아이바이오, 매출0/근사값이 정상)으로 확인돼 **29건을 "정상상황(오탐)"으로 is_resolved=1 처리**. 나머지 014950(삼익제약, 실제 매출 있는 정상기업)만 DART에 2022~2023년 사업보고서/분기보고서 자체가 미수집(감사보고서만 존재)된 진짜 공백으로 확인 — 단순대입 불가, 재수집 필요 노트만 추가. ②`data_quality_issues` `ANCHOR_MISMATCH`(4건, HIGH, 5월부터 미해결) — 057050 현대홈쇼핑·119850 지엔씨에너지·178320 서진시스템 3건 모두 **CFS/OFS 혼용**으로 확인(FnGuide 앵커=CFS, 현재값=OFS, 배율 1.1~8.4배) → `stock_collection_config.preferred_report_type='CFS'` 등록(178320은 기존에 "지주사"로 잘못 분류돼 OFS 선호로 설정돼있던 오류도 함께 정정), `financial_fix_log` 기록, is_resolved=1. 180640 한진칼은 **지주회사라 OFS 선호 관례(002020 선례) 적용대상**이라 CFS로 강제전환하지 않음 — 대신 `dart_raw_accounts` 원문(rcept_no 20230315001278) 확인 결과 CFS 매출액 2,003억원이 FnGuide 앵커와 정확히 일치함을 확인, 데이터 자체는 정상이고 표시로직 확인이 필요하다는 진단만 기록(is_resolved=0 유지, 데이터 미변경).

### 2026-09-19(3차) investor_trading_daily/kiwoom_investor_daily 재점검 — 컬렉터는 이미 수정됨, shares_issued 우선주 이슈는 이미 해소(문서만 낡음)
①`kiwoom_investor_daily` 매수전용(buy-only) 버그는 **2026-07-21에 이미 컬렉터 코드 수정 완료**(`trde_tp="0"`)되어 있었음을 확인 — 005930 2026-07-20/2026-09-11 양쪽 다 KIS(price_history) `_amt` 컬럼과 거의 정확히 일치(기관/개인 완전일치, 외국인 99.5%). 재수집 범위(2025-01~현재, `scratch/backfill_kiwoom_investor_netbuy_20260721.py`)도 실사용처(signal_engine 최근30일, sector_rotation 90일) 전부 커버함을 코드로 직접 확인 — 2018~2024 과거값이 여전히 버그값인 채 남아있으나 **아무 소비자도 참조하지 않는 죽은 데이터**로 재확인, 추가 조치 불필요(기존 "우선순위 낮음" 판단이 옳았음). ②`shares_issued 우선주 포함` 문제(삼성전자 1.43배 등, 오랫동안 "미수정"으로 기록됨)를 재검증한 결과 **이미 해소됨**(전부 0.95~1.03배 정상, 코스피 시총상위 30종목 전수 확인) — 문서가 낡아있던 것으로 확인, 섹션 9 표 정정 완료. `EPS 저장값 괴리`/`TTM EPS 부정확`은 별개의 구조적 이슈라 여전히 유효.

### 2026-09-19(4차) 알려진 이슈 표 전체 재점검 — 해결된 문서 4건 정정/삭제, 미해결 1건 수치 정정
사용자 지시("클로드점엠디에 저장된 미완료 항도 재점검해서 완료된거면 삭제하거나 완료 표시")로 섹션 9 전체를 훑어 재검증:
- **`investor_trading_daily`**: 여전히 buy-only 오염 자체는 사실이나(inst_net 450만행 중 음수 0건), 원천 API(공공데이터포털 getStocInvtTrdnInfo)가 서비스 폐지되어 2026-07-10 이후 죽은 테이블이고 `collectors/public_data.py`가 2026-08-24에 이미 호출 자체를 제거했음을 코드로 확인 — "미수정 잔존"에서 "조치 불필요(deprecated)"로 정정.
- **`frontend/src/views/SemiconductorSectorView.jsx`**: 어디서도 import 안 되는 757줄 고아 파일 재확인 → **파일 삭제**(git 이력 보존, 복구 가능), 표에서 해당 행 제거.
- **`_job_combo_daily`의 "6개 계좌 매수 0건"(2026-09-07)**: 같은 날 다른 항목에 기록된 리스크게이트 수정으로 이미 해결됐음을 실거래 확인(sc_sector_focus 09-17, ai_combo 09-18 매수 체결)으로 재검증, 스테일 경고문 제거.
- **`dart_recollect 분기 NI 파싱실패`**: "점진 해소 예정"이라 적혀있었으나 실측 결과 5,228→**7,613건으로 오히려 증가** — 자연 해소되지 않았음을 확인, 수치 정정(실제 수정 작업은 미착수, 별도 파싱 로직 점검 필요로 남김).
- 이 외 항목(TWSE/HKEX/PMI 등 외부 API 차단류, HS매핑/전략 백테스트류)은 외부 요인 또는 이번 세션 범위(가격·재무 데이터 무결성) 밖이라 재검증 보류.

### 2026-09-19(5차) dart_recollect NI 파싱실패 근본원인 확정·수정 — 야간 스케줄러가 매일 크래시하고 있었음
바로 위 항목("5,228→7,613건으로 증가")을 파고든 결과, "점진 해소 예정"이 실현 안 된 진짜 이유를 확정: 매일 00:30 `_job_dart_financial_recollect`가 실행하는 `scratch/legacy_dart_recollect.py`가 **금지경로 4곳**(`DB_PATH`·`CKPT_PATH`·`OUT_DIR`·DART API 키를 읽는 `.env` 경로, 전부 `/Applications/stock_dashboard/...`)을 하드코딩하고 있었는데, 이 경로 자체가 이 환경에 존재하지 않아 **`_load_dart_keys()`가 모듈 임포트 시점에 `FileNotFoundError`로 즉시 크래시** — 즉 이 스크립트는 실행될 때마다 한 줄도 처리 못 하고 매일 밤 실패해왔던 것으로 확인(정확히 언제부터인지는 로그 부족으로 미특정). 별도로 `_NET_KW`(순이익 키워드 목록)도 `collectors/dart_collector.py`보다 좁아서(분기순이익/반기순이익/연간순이익 등 누락) 설령 크래시가 없었어도 일부는 계속 놓쳤을 것으로 확인.

**수정**: ①경로 4곳을 `Path(__file__).resolve().parents[1]`(런타임 루트) 기준 상대경로로 전환, 미사용 `DB_PATH` 상수는 제거(`connect_primary_db()`만 실사용). ②`_NET_KW`에 "분기순이익"·"반기순이익"·"당기순이익(손실)"·"당기순손익(이익)"·"연간순이익" 추가. **샘플검증(CLAUDE.md 규칙 준수, 8종목)**: 025980·051370·302440·048430·126560·103590·041460 **7/8건이 실제 DART 재호출로 net_income 정상 추출 확인**(수정 전 전부 NULL), 000650만 해당 분기 DART 응답 자체가 빈 값(별도 사유, 파싱 문제 아님). 스크립트 자체를 재실행하지는 않음 — **경로 수정으로 오늘 밤(00:30)부터 스케줄러가 정상 동작해 7,613건을 점진적으로 자동 해소할 것으로 예상**, 며칠 뒤 카운트 재확인 필요. 이 외 `/Applications/stock_dashboard` 참조는 전부 `scripts/archive/`(미사용 보관본)뿐임을 전수 검색으로 확인, 추가 조치 불필요.

### 2026-09-19(6차) 재무 이상치/검증 테이블 스테일 플래그 재검증 — 사용자 지시("숫자 데이터 완결성 계속")
`financial_anomalies`·`fin_quarterly_validation_flags` 모두 2026-05-25~31 무렵 일괄 감지된 후 재검증 없이 방치된 스테일 플래그가 대량 존재함을 확인·정리:
- **`partial_coverage`(1,800건 미해결)**: 전부 "결손 연도: [2020, 2021]" 패턴 — 실제 financial_data를 재조회한 결과 **1,639건(91%)이 이미 데이터가 채워져 있음**(감지 이후 백필로 자연 해소, 재검증만 안 됨) → is_resolved=1 처리. 159건은 일부 연도만 남아 결손연도 목록 축소 갱신. 진짜 전부 미수집인 건 **014950(삼익제약, 이미 별도 확인된 종목)·101970 단 2건**뿐.
- **`persistent_loss`(660건 미해결)**: 무작위 표본 검증 결과 헬릭스미스·롯데하이마트·현대바이오·큐라티스 등 **실제로 잘 알려진 적자기업들**로 확인 — 이건 데이터 오류 지표가 아니라 "검증 필요" 의도적 리뷰 큐(recommended_action=verify_net_income_carefully)로 설계된 것이라 손대지 않음(CLAUDE.md 자동확정 금지 원칙 준수).
- **`fin_quarterly_validation_flags` status=OPEN(14,232건, check_type=QUARTERLY_4WAY)**: notes가 대부분 `no_data`/`no_data_bs` — 당시엔 데이터가 없어 검증 자체를 못 한 것으로 확인. 원래 4-way(DART+FnGuide+Seibro+Naver) 검증을 채운 파이프라인 스크립트는 git 이력에서 못 찾았고 Seibro/Naver는 재수집 인프라 자체가 없어 원본과 동일한 4-way 재현은 불가 — 대신 **재현 가능한 DART/FnGuide 2-way 대조로 실제 재검증 수행**(사용자 지시 "오픈하지말고 완결해"): financial_data를 stock_code+year+quarter+field로 재조회해 8,923건(63%)이 이제 값을 보유함을 확인 → 그 중 46건은 DART·FnGuide 둘 다 있어 실제 비율 비교(±2%=CONFIRMED 28건, ±10%=CLOSE_MATCH 3건, 그 이상=STRUCTURAL 15건), 나머지 8,877건은 한쪽 소스만 있어 교차검증은 불가하나 그 자체로는 정합적인 기존 상태값 **SELF_CONSISTENT**로 정직하게 반영(허위로 CONFIRMED 처리하지 않음). dart_value/fnguide_value/ratio/notes 실제 갱신, status는 OPEN→위 4개 상태로 전환. **OPEN 14,232→5,309로 감소**(63% 실질 해결) — 남은 5,309건은 DART·FnGuide 둘 다 진짜 데이터가 없는 건(91.6%가 2023~2026년 최근분기, 이 중 상당수는 아직 보고시점도 안 지남) — 조작 없이 OPEN 유지, 오늘 밤 수정된 DART 재수집 스케줄러(위 5차 항목)가 정상 가동되면 자연히 줄어들 것으로 예상.

### 2026-09-19 종합 정리 — 완료 vs 지속점검 명확화 (사용자 지시)
오늘 하루 처리한 항목을 완료/지속점검으로 명확히 구분:

**✅ 완료(추가 조치 불필요)**
- `invalid_ohlcv` 1,546→38건(97.5%, 잔여는 검증소스 없음)
- `externally_confirmed_internal_corruption` 1,090건 → 0건(2020-01~2021-02 배치오류 1,048종목 251,301행 복구)
- `unresolved_active_common` 중 Naver 대조 가능분 6,657행 복구
- `financial_anomalies revenue_zero` 30→1건(29건은 SPAC/임상바이오텍 확인, 정상상황)
- `data_quality_issues ANCHOR_MISMATCH` 4→1건(3건 CFS/OFS 설정 정정)
- `shares_issued 우선주 포함` — 이미 해소 확인(문서 정정)
- `kiwoom_investor_daily` buy-only 버그 — 이미 수정 확인(2026-07-21)
- `SemiconductorSectorView.jsx` 고아파일 삭제
- `_job_combo_daily` "매수0건" 스테일 경고 제거
- `dart_recollect NI 파싱실패` **근본원인(스케줄러 크래시) 수정 완료** — 단, 실제 해소는 며칠 뒤 재확인 필요(아래 지속점검)
- `financial_anomalies partial_coverage` 1,800→161건(91%)
- `fin_quarterly_validation_flags OPEN` 14,232→5,309건(63%)

**🔄 지속점검 필요(완료 아님, 앞으로도 계속 확인할 것)**
- `investor_trading_daily` — API 폐지로 조치불필요 처리했으나, 혹시 대체 API가 생기면 재검토
- `dart_recollect NI 파싱실패` 잔여 — 오늘 밤(00:30)부터 스케줄러가 정상 가동될 것으로 "예상"만 한 상태, **며칠 뒤 실제로 카운트가 줄어드는지 재확인 필요**
- `fin_quarterly_validation_flags` 잔여 OPEN 5,309건 — 신규수집 대기 중, 스케줄러 정상화 이후 자연 감소 여부 추적
- `financial_anomalies persistent_loss` 660건 — 의도된 리뷰 큐, 계속 살아있는 상태가 정상(완료될 항목 아님, 지속 모니터링 대상)
- `financial_anomalies partial_coverage` 잔여 161건(159 부분결손+2 완전결손: 014950·101970)
- `unresolved_active_common` 나머지(6,657행 제외분) — 이번엔 스킵했으나 여전히 존재
- `mixed_basis_or_price_corruption`(617건 안팎, 유동적) — 정체 미확인 잔여분
- TWSE/HKEX/글로벌 PMI 등 외부 데이터소스 — 사용자 지시로 미결 업무로 보류 유지

### 2026-09-19(7차) financial_data 음수매출 78건 전량 정정 + cf_validation_flags AMBIGUOUS 재검증 실행 중
①`financial_data.revenue < 0` 78건 전수 확인·정정. **25건**(`derived_annual_minus_quarters`, 대부분 Q4)은 연간-분기누적 역산 결과가 음수 — CLAUDE.md 규칙("소스 불일치시 Q4 강제산출 금지") 위반이라 NULL 처리. **53건**(`dart_ofs_backfill`/`dart_q2_verified`/`dart` 등, 004310 현대약품 24건·950170 JTC 9건 등)은 DART 실시간 재조회로 **완전히 다른 값**이 나옴을 확인(예: 004310 2023 Q2 저장값 -30.9억 vs 실제 DART 매출액 +488.5억) — 진짜 오염 확정, 재조회값으로 교체. 중 28건은 같은 (종목,연도,분기) 키에 이미 정상값을 가진 "형제 행"(legacy_collected/dart_recollect 등)이 별도로 존재하는 **중복행 구조**였음을 발견 — `id` 기준 정밀 타겟팅으로 정상 형제행은 건드리지 않고 오염된 행만 수정. `financial_fix_log`에 전건 기록(run_id 3종). **최종: 음수매출 78→0건**.
②`cf_validation_flags` status=AMBIGUOUS(1,424건, 2026-08-02 배치, ai_verdict 전부 NULL) — 이미 존재하던 검증 파이프라인(`collectors/cf_triple_validator.py validate_recent()`, DART+FnGuide+Seibro 3중대조+자동보정 로직 포함)을 찾아 재실행 시작. 대상이 예상(864쌍)보다 넓어져 **5,577건**(관련 627종목의 전체 연도) 처리 중 — Seibro 실시간 조회 방식이라 시간 소요(추정 1시간+), **백그라운드 실행 중, 완료 시 결과 반영 예정**.
③영업이익이 매출의 5배 이상인 1,991건 스팟체크 — 절반 이상이 "의료"(임상단계 바이오텍) 업종으로 확인, 이미 이번 세션에서 여러 번 확인된 "매출 미미+R&D손실 막대"의 정상적인 바이오텍 특성과 일치 — 노이즈가 커서 이번엔 깊이 파지 않음, 후속 필요시 참고용으로 기록만.

### 2026-09-19(8차) cf_validation_flags AMBIGUOUS 1,424→485건 — 3중검증 실행 + FIN_CROSS 917건 사업보고서 재조회
①`collectors/cf_triple_validator.validate_recent(days=9999, codes=627종목)` 실행 완료(5,577건, 오류 0, CONFIRMED 10,617/AMBIGUOUS 588 이벤트, `dart_fg_corrected` 보정 10건). 단 이 검증기는 현금흐름 3필드(영업CF/투자CF/기말현금)만 다루므로 AMBIGUOUS 테이블 감소는 66건에 그침. ②남은 AMBIGUOUS 중 `flag_type='FIN_CROSS'`(재무제표 본체 DART↔FnGuide 불일치: 매출373·순이익166·영업이익156·총자산113·총자본109, 917건)는 DART 연간 사업보고서(11011, CFS→OFS)를 실시간 재조회(`scratch/fin_cross_recheck_20260919.py`, DB 쓰기 없음)해 판별: **873건은 현재 financial_data 값이 공시 원문과 이미 일치하는 스테일 플래그**(플래그 저장값이 2026-08-02 시점 낡은 값) → 411건 CONFIRMED(DB=공시=FnGuide), 462건 STRUCTURAL(DB=공시, FnGuide는 기준차/오파싱, DART 앵커 우선) 처리, `ai_verdict`/`resolved_value`/`resolved_at` 기록. **financial_data는 한 행도 수정하지 않음**(모두 이미 정상이었음).
③**미해결 유지(근거 불충분, 데이터 미변경)**: FIN_CROSS 44건(DB가 공시와도 불일치 19건 + 공시 조회 불가 25건 — 대부분 소형/외화보고(900·950번대) 종목으로 통화·단위 문제이거나 FnGuide 1억 단위 반올림값, 개별 확인 필요) + 현금흐름 필드 AMBIGUOUS 약 415건(투자CF 166·영업CF 141·기말현금 108, 검증기가 3자 불일치로 판정한 진짜 미해결). 최종 cf_validation_flags: CONFIRMED 102,831 / CLOSE_MATCH 3,441 / STRUCTURAL 479 / AMBIGUOUS 485. **지속점검**: 이 485건은 계속 추적 대상.

### 2026-09-20 — FIN_CROSS 결과 재적용 및 현재 DB 기준 정정
2026-09-19 기록의 `AMBIGUOUS=485`는 PostgreSQL 현재 상태와 일치하지 않았다. `/tmp/fin_cross_recheck.json`의 DART 재조회값을 현재 `financial_data`와 다시 대조한 결과, 917 FIN_CROSS 중 **409건**만 현재 저장값이 DART CFS/OFS 원문과 일치했다. 이 409건은 값 변경 없이 `STRUCTURAL` 및 `DART_LIVE_RECHECK_MATCH_FNGUIDE_DIFF`로 확정했다. 나머지 **508건**은 현재 저장값도 재조회 DART 값과 일치하지 않거나 대상 행이 없어 계속 미결이다. 현재 `cf_validation_flags`의 `AMBIGUOUS`는 **1,066건**이며, 근거 파일은 `research_outputs/fin_cross_reconciliation_20260920.json`이다. 향후 FIN_CROSS 수치는 이 현재 DB 기준을 사용하며, 508건은 종목·필드별 DART 원문 및 단위/연결·별도 기준을 검토한 후에만 `financial_data`를 수정한다.

FIN_CROSS 매출 338건도 같은 기준으로 세분화했다. **218건**은 현재 다른 DART 연간 행이 live DART와 동일한 CFS/OFS 기준으로 일치해 값 변경 없이 `STRUCTURAL`로 확정했다. 매출 미결은 **120건**, 전체 `AMBIGUOUS`는 **848건**이다. 근거: `research_outputs/fin_cross_revenue_reconciliation_20260920.json`.

매출 미결 120건 중 현재 DART 연간 행이 live DART 원문과 동일 CFS/OFS 기준에서 직접 불일치한 **89건**은 `financial_data.revenue`를 원문값으로 정정했다. 나머지 **31건**은 대상 행 또는 같은 기준 원문값이 없어 보류했다. 현재 전체 `AMBIGUOUS`는 **759건**이다. 근거: `research_outputs/fin_cross_revenue_dart_corrections_20260920.json`.

FIN_CROSS 총자산·총자본 104건 중 **83건**은 현재 다른 DART 연간 행이 live DART 원문과 동일한 CFS/OFS 기준으로 일치해 값 변경 없이 `STRUCTURAL`로 확정했다. 자산·자본 미결은 **21건**, 전체 `AMBIGUOUS`는 **676건**이다. 근거: `research_outputs/fin_cross_balance_sheet_reconciliation_20260920.json`.

FIN_CROSS 영업이익·순이익 66건 중 **46건**은 현재 다른 DART 연간 행이 live DART 원문과 동일한 CFS/OFS 기준으로 일치해 값 변경 없이 `STRUCTURAL`로 확정했다. 직접 불일치 후보에는 외화보고·단위 차이 또는 재수집 파싱 이상 가능성이 있어 `financial_data`를 수정하지 않았다. 손익 미결은 **20건**, 현재 전체 `AMBIGUOUS`는 **630건**이다. 근거: `research_outputs/fin_cross_profit_reconciliation_20260920.json`.

현금흐름 3중검증기의 DART 값 선택이 `id` 순서에 의존해 OFS가 먼저 저장된 경우 별도 값을 읽을 수 있던 결함을 수정했다. `get_cf_values()`는 이제 CFS 행을 우선 선택한다. 기존 현금흐름 `AMBIGUOUS` 558건을 저장된 FnGuide·Seibro 스냅샷과 현재 CFS 값으로 재판정해 **224건을 `CONFIRMED`, 3건을 `CLOSE_MATCH`**로 바꿨다. `cash_flow_data` 원본값은 변경하지 않았고, 상태가 여전히 `AMBIGUOUS`인 18건은 DART 비교값만 CFS 기준으로 갱신했다. 현재 전체 `AMBIGUOUS`는 **403건**이며, 현금흐름 미결은 영업CF 118건·투자CF 140건·기말현금 73건(합계 331건)이다. 근거: `research_outputs/cf_basis_reclassification_20260920.json`.

남은 현금흐름 331건의 DART 연간 공시 원문을 재조회하는 읽기 전용 도구 `scratch/recheck_cf_ambiguous_dart.py`를 추가했다. 2026-09-20 실행에서는 등록된 DART API 키 3개가 모두 첫 호출부터 한도 초과(`020`)를 반환해 원문 증적 파일을 만들지 못했다. DB 변경은 없으며, 중단 시에도 결과를 매 25건 저장하고 `--resume`으로 이어 받을 수 있게 보강했다. DART 키 한도 회복 전에는 이 331건과 FIN_CROSS 72건을 확정하거나 데이터값을 수정하지 않는다.

standalone 검토 과정에서 발생한 의도치 않은 쓰기를 재발 방지했다. `collectors/fred_collector.py`는 수동 실행 시 기본적으로 도움말만 출력하고 `--collect`를 명시해야 수집·DB 적재를 수행한다. API/스케줄러의 `collect_fred()` 직접 호출은 유지된다. `scripts/verify_tenbagger_postgres.py`는 argparse를 추가해 `--help`에서 검증과 `tenbagger_verification_latest.json` 갱신 없이 종료한다. 두 도움말 호출의 종료 코드 0과 tenbagger 결과 파일 수정 시간 불변을 확인했다.

PostgreSQL cutover의 missing 3개 테이블은 SQLite 원본에서 생성·복제했고, `cafe_stock_indicator_mappings`는 `(stock_code, indicator_key)` 자연키 upsert로 보강했다. `dart_insider_holdings`는 SQLite에만 있는 surrogate `id` 16,563개가 PostgreSQL의 기존 자연키와 충돌하는 구조라 강제 삽입하면 중복 오염을 재발시킨다. 따라서 강제 행수 맞춤은 하지 않았고, `verify_postgres_cutover.py`는 아직 이 테이블을 behind로 판정한다. 서버는 LaunchAgent 재기동 후 `/openapi.json`과 `/api/dashboard/stats`가 정상이고 dashboard 응답의 `db_path=postgresql`을 확인했지만, 기존 `/api/semiconductor/financials`는 404라 전체 cold-path 완료 판정은 보류한다.

반도체 재무 cold-path의 실제 500은 `routes/market_radar.py`가 PostgreSQL boolean `is_annual`에 SQLite식 0/1 파라미터를 비교하고, PostgreSQL numeric(`Decimal`)을 float 상수 `1e8`로 나누는 데서 발생했다. 분기/연간 조건을 `IS FALSE`/`IS TRUE`로 바꾸고 나눗셈 단위를 정수 `100_000_000`으로 수정했다. LaunchAgent 재기동 후 종목 재무표와 `/api/market-radar/semiconductor/financials?type=quarterly`가 모두 정상 응답했다. **10번 cold-path 검증은 완료다.**

PostgreSQL cutover 최종 재검증: `dart_insider_holdings`의 SQLite 물리 행 78,581개와 PostgreSQL 물리 행 62,059개의 차이는 SQLite의 쉼표·과학적 표기 숫자와 surrogate `id` 중복에서 비롯된 것이었다. 보유 수량 필드를 `Decimal` 의미값으로 정규화한 natural key 기준 SQLite 62,044개가 PostgreSQL 62,059개에 모두 포함됨(`missing_keys=0`)을 확인했다. 이에 reconciliation과 verifier를 같은 의미값 비교 방식으로 보완했고, `scripts/verify_postgres_cutover.py`가 missing table·behind table·failure 없이 `ok=true`를 반환했다. **8번 missing/behind 실제 reconciliation 완료.**

2026-09-20 재점검: PostgreSQL cutover는 완료지만 전체 주식 데이터의 무결점 상태는 아니다. 현재 미결은 `cf_validation_flags` 403건(CF 331건, FIN_CROSS 72건), `QUARTERLY_4WAY` OPEN 5,309건(5,303건이 모든 비교 소스 NULL인 실제 수집 공백), `price_jump_audit.unresolved_active_common` 6,904건 및 `mixed_basis_or_price_corruption` 626건이다. DART API 단건 읽기 요청도 `020`(일일 한도 초과)이어서 원문 근거 없는 값 수정은 하지 않았다. 대신 일일 `data_integrity_followup.py`의 실제 결함을 보정했다. 희석률 보정은 이미 일치하는 26건을 매일 UPDATE하면서 `data_source`에 `+dart_followup_repair`를 중복 누적하고 있었으므로, 값이 실질적으로 달라질 때만 UPDATE하도록 변경했다. 기존 반복 표기 31건도 값 변경 없이 출처 메타데이터만 한 번으로 정규화했고 재검증 결과 반복 표기 0건이다. 같은 수집기의 매출 파서는 `손익계산서` 명칭만 받아 `포괄손익계산서` 원문을 누락할 수 있어 손익계산서 계열 전체를 인식하도록 수정했다. 문법·동일값 차단·포괄손익계산서 표본 검증을 통과했다.

2026-09-20 DART CF 원문 재대조 후속: 세 API 키 중 KEY3가 사용 가능함을 확인했으나 재수집기 키 순환이 KEY1→KEY2까지만 시도하고 중단하는 결함이 있어 KEY3까지 순차 전환하도록 수정했다. 읽기 전용 재조회로 CF 미결 331건 중 CFS 원문 43건·OFS 원문 61건을 확보했다. CFS 원문과 현재값이 같은 38건은 값 변경 없이 `CONFIRMED`로 처리했다. 나머지 5건(018500·021820·099750·206650·258610의 기말현금)은 DART CFS 원문과 직접 불일치했고, DART 수집 출처가 명시된 연간 CFS 행 6개만 원문값으로 정정했다. `cashflow_fix_log`에 전건을 남겼고 해당 5개 플래그도 `CONFIRMED`가 됐다. 총 `AMBIGUOUS`는 **403→360건**, CF 미결은 영업CF 118·투자CF 140·기말현금 30건(288건)이며 FIN_CROSS 72건은 그대로다. 근거: `research_outputs/cf_ambiguous_dart_recheck_20260920.json`, `research_outputs/cf_live_dart_resolution_20260920.json`.

2026-09-22 미결 항목 재점검 중 `price_jump_audit` 분류기 오탐 근본원인 발견·수정. 09-20에 언급된 `mixed_basis_or_price_corruption` 626건이 이후 nightly `verify_price_history_with_naver.py`에 의해 `externally_confirmed_internal_corruption`(price_history가 틀렸다는 강한 주장)으로 승격돼 있었음. 626건 전체를 `corporate_action_events.backward_price_factor`(감자 등 확정된 액면비율)와 대조한 결과 대부분(587건)은 실제 근거 불명확한 미결로 남지만, **7건은 명백한 오탐**이었음: `audit_price_jumps_and_build_canonical.py`가 corporate_action_events를 event_date **±3일**로만 매칭하는데, 감자 등 자본감소는 실제 거래정지·재개일(가격 점프가 찍히는 날)보다 DART 신주상장(등록완료) 기준 event_date가 **8~11일 늦게** 기록되는 경우가 있어 매칭에 실패 → `unresolved_active_common`으로 떨어졌다가 Naver 비교에서 오탐 승격됨(Naver는 자체적으로 분할·감자 구간을 소급조정해서 보여주므로 원시 점프가 안 보여 "불일치"로 잘못 해석됨). 049630·010280·054300·097800·104040·256630·002630 7건은 ±25일 창으로 넓혀 backward_price_factor가 관측 ratio를 5% 이내로 재현함을 확인 후 `confirmed_corporate_action`으로 재분류(price_history 자체는 수정 안 함 — 원래 맞는 값이었음). 근본 수정: `audit_price_jumps_and_build_canonical.py`의 corporate_action_events 매칭에 ±3일 실패 시 ±25일 창 + ratio 5% 이내 매칭 폴백 추가(스케줄러가 매일 재실행하는 표준 파이프라인이라 서버 재시작 불필요, 다음 야간 재감사부터 자동 반영). 나머지 619건(608건은 2020-2021 chronic 구간, 11건은 2026-04/05 신규지만 근거 불충분)은 여전히 미결로 남김 — 근거 없이 성급히 되돌리지 않음. `data_fix_log` run_id=`reclassify_confirmed_corp_action_wide_window_20260922_222600`.

2026-09-23 미결 업무 계속 점검. (1) `data_quality_issues` ANCHOR_MISMATCH 180640(한진칼) 최종 확인·해결: `processor.get_financial_summary`/App.jsx 실측 결과 메인 재무테이블은 항상 `report_type=CFS`로 조회하므로 기본 화면은 FnGuide 앵커와 일치하는 CFS 매출(200,336,082,220원)을 정상 표시하고, 사용자가 별도(OFS) 탭을 열 때만 보이는 revenue=NULL은 지주사 별도재무제표가 매출액을 다른 계정으로 인식하는 구조적 특성일 뿐 오류가 아님 — `is_resolved=1` 처리. (2) `financial_anomalies` partial_coverage(161건, "결손 연도" 대부분 2020년 단일)를 전수 DART 실측 재조회로 3분류: `no_filing`(66건, 그 해 사업보고서/분기보고서/반기보고서 자체가 DART에 없음 — 회사가 정기공시 대상이 아니었거나 미제출, 014950 삼익제약처럼 2020~2021년 감사보고서만 있고 정기보고서가 전혀 없는 사례 포함), `filing_no_structured_data`(57건, 보고서는 있으나 `fnlttSinglAcntAll` 구조화 API·document.xml XBRL 태그 둘 다 비어있음 — 101970 우양에이치씨로 실증, 원문 HTML 표를 직접 파싱해야 하는 별도 작업), `fixable`(40건, DART에 구조화 데이터가 실제로 존재하는데 한 번도 수집된 적이 없던 순수 컬렉션 공백). fixable 40건은 `fnlttSinglAcntAll`(CFS/OFS)로 직접 재조회해 `financial_data`에 신규 연간 행 51개(매출/영업이익/순이익, `data_source='dart_document_triage_20260923'`)를 채우고 `financial_anomalies.is_resolved=1` 처리, 모든 값은 `financial_fix_log`에 필드별로 기록(run_id=`partial_coverage_triage_20260923_*`, 스크립트는 `/tmp/partial_coverage_triage_20260923.json`에 분류 원본 보관). 나머지 121건(66+57에서 두 해 결손 종목 중복 조정)은 description에 확인된 근거를 남기고 미결 유지 — "이유 불명"에서 "구체적으로 왜 안 되는지 확인된 미결"로 전환. partial_coverage: 161건 미결 → 121건 미결(확정된 구조적 사유 포함)+40건 해결.

2026-09-20 잔여 재무 무결성 handoff: 추가 원문 대조·기준 재분류·합의 보정으로 `AMBIGUOUS`를 **299건**까지 줄였다. 남은 전건은 `research_outputs/remaining_financial_integrity_20260920.json`에 증적 상태와 함께 기록했다. 구성은 DART 현금흐름 응답 자체가 없는 268건, DART 연간 응답 자체가 없는 FIN_CROSS 25건, 그리고 원문은 있으나 외화 단위 또는 계정 해석을 종목별로 검토해야 하는 FIN_CROSS 6건이다. 남은 299건은 값이 틀렸다는 증거가 아니라 현재 자동 확정에 필요한 원문 근거가 없거나 불충분한 큐다. DART 재조회에서 새 원문이 생기면 같은 파일의 id를 입력으로 재개하고, 그 전에는 원본값을 변경하지 않는다.

2026-09-24 가격 소수점 오염 잔여분 복구. 다른 세션(hermes.md 2026-09-23 기록)이 2026-03-31~04-07 배치 사고(`scripts/archive/collect_all_prices.py`가 yfinance `auto_adjust=True` 소수점 종가를 `INSERT OR IGNORE`로 빈 날짜에만 채움, 현재 비활성·미스케줄)로 오염된 `price_history` 297만 행 중 218만 행을 marcap 원가로 복구(`bulk_marcap_interpolation_repair_*`, `data_fix_log` 합 2,181,029)하고 `price_jump_audit` stale 3,169건을 재분류함(`unresolved_active_common` 6,352→4,961, 이 값이 09-22의 내 ±25일 corporate action 매칭 수정과 겹쳐 반영됨). 잔여 주식 소수점 종가 73.9만 행/754종목 중 종목별로 네이버 스냅샷 종가가 기존 정수 행과 99% 이상 일치(같은 가격 기준 검증)하는 465종목의 45.2만 행을 네이버 정수 OHLCV로 교체(`scripts/apply_remaining_decimal_naver_repair_20260924.py`, run_id `remaining_decimal_naver_repair_20260924_102347`, 백업 `price_history_fix_backup` 451,560행, `data_fix_log` 기록). 미수정 잔여 약 28.7만 행/359종목은 ETF(네이버와 기준 불일치)·네이버 미보유·지수 등 독립 소스가 없어 값을 만들지 않고 유지. 다음 야간 재감사부터 audit 테이블에 반영됨.

2026-09-24(2차) ETF 소수점 종가 잔여분 복구. 네이버 스냅샷에 없거나 기준이 달라 앞선 복구에서 빠진 ETF 소수점 종가(yfinance auto_adjust 사고 잔재)를 pykrx `get_market_ohlcv_by_date` 정수 OHLCV로 교체(`scripts/apply_etf_decimal_pykrx_repair_20260924.py`). 종목별로 pykrx 종가/기존 소수점 종가 비율이 겹치는 모든 행에서 0.985~1.10 이내(최소 20행)인 143 ETF·155,931행만 적용, 나머지 114종목은 분할조정 등 기준 불일치 가능성으로 미수정. 백업 `price_history_fix_backup`·`data_fix_log`(run_id `etf_decimal_pykrx_repair_20260924_*`) 기록. 주의: pykrx는 `adjusted=False`가 빈 결과를 반환하므로 기본값을 써야 함.

2026-09-24(3차) partial_coverage `filing_no_structured_data` 57건 중 8종목 원문 파싱 해결. 원인 확인: DART `document.xml` ZIP의 XML이 헤더(utf-8)와 달리 **cp949 인코딩**이라 기존 `collectors/dart_document_financials.py`(utf-8 디코딩)가 한글·표를 못 읽었음(XBRL 태그가 없는 보고서는 HTML 표 파싱이 필요). cp949로 디코딩해 손익계산서 표(Ⅰ.매출액/영업이익/당기순이익)를 추출하고 문서 내 백만원 요약표로 교차검증, 세 항목 모두 있고 매출≥10억·모든 값 ≥1천만원(백만원 단위 오독 배제)인 일반기업만 적용: 027830·089860·099430·148780·176750·200350·203400·101970(2020·2021) `financial_data` 9행(`data_source='dart_document_html_20260924'`, `financial_fix_log` run_id `html_pl_extract_20260924_*`). 금융사·지주사는 매출 정의/연결·별도 구분 자동판별이 불확실해(메리츠금융지주 연결 매출이 OFS로 분류되는 등) 제외. report_type은 자동판별이 불확실해 CFS로 표기(기록에 명시). partial_coverage 미결 121→113건. 남은 미결의 추출기 개선은 스크립트 `/tmp` 산출물이 아니라 필요 시 `scratchpad/html_pl_extract.py` 로직 참고. 주의: 이 값들은 독립 외부소스(FnGuide 2020)가 없어 DART 원문 파싱 결과 단독임.

2026-09-24(4차) `externally_confirmed_internal_corruption` 204건 중 marcap 대조 복구. 독립 원가 소스 marcap(비조정 OHLCV)을 대조해 167건은 marcap은 매끄럽고 `price_history`만 튀는 진짜 오염임을 확인. 앞뒤 경계가 모두 marcap과 5% 이내로 일치하는 "고립 오염 구간(스냅백)"만 marcap 정수 OHLCV로 교체(`scripts/apply_marcap_snapback_repair_20260924.py`): 118구간·208행, 백업 `price_history_fix_backup` 208행·`data_fix_log` 208행(run_id `marcap_snapback_repair_20260924_104044`). 나머지 약 150건은 종목 전 구간에서 `price_history`와 marcap이 계속 벌어진 **가격 기준 불일치(분할조정 vs 원가)** 유형(persist_before/after, both_sides_off 등)이라 어느 쪽이 맞는지 종목별 corporate action 확정 없이는 marcap으로 덮어쓰면 기준이 섞임 → 미수정, corporate_action_events 보강 프로젝트 대상. 연도 경계(2020-01-02 등)를 위해 인접 연도 marcap parquet을 함께 로드해야 함.

2026-09-24(5차) 원문 HTML 파싱 추출기 보완: 손익계산서 행의 **주석 번호 칸("4, 25")이 숫자로 읽혀 첫 값이 작다고 버려지던 버그** 수정 후 044990·114840 2020 연간(매출/영업이익/순이익) 2행 추가(`data_source='dart_document_html_20260924'`, report_type CFS 표기, `financial_fix_log` run_id `html_pl_extract2_20260924_*`). partial_coverage 미결 113→111건. 나머지 파싱 실패분은 금융사(은행·보험·증권·투자사: "매출액" 개념 부재, 연결/별도 자동판별 불확실)와 계정 표기가 다른 대기업(LG씨엔에스·에스디바이오센서·HK이노엔 등, 영업이익/순이익 라벨 변형)이라 원문 개별 확인 필요.

2026-09-24(6차) `externally_confirmed_internal_corruption` 잔여 기준 불일치 구간 원가 통일. 96종목의 `price_history`가 긴 구간에서 소급조정(불일치 행의 58%는 네이버 소급조정 종가와 일치, 나머지는 서로 다른 조정 시점 혼합; 5.9만 행은 `corporate_action_events`의 확정 배율로 설명되지 않음)돼 marcap 원가와 일정 배율(5x·10x·25x·42x 등)로 벌어져 있었음. hermes.md의 000670/035720 splice 수정과 같은 원칙(price_history=KRX 원가, 조정은 corporate_action 엔진이 별도로 적용)으로 종가가 marcap 대비 5% 초과 벌어진 행을 marcap 정수 OHLCV로 교체(`scripts/apply_marcap_raw_basis_restore_20260924.py`): 96종목·63,930행, `price_history_fix_backup` 63,930행·`data_fix_log` 63,930행 일치(run_id `marcap_raw_basis_restore_20260924_105159`, 무효 OHLCV 1행 제외). 되돌리려면 백업의 old_* 값을 복원. **영향**: 미기록 실제 분할/감자가 있던 종목은 원가 복원 후 그 날짜에 실제 점프가 드러나므로 다음 야간 `price_jump_audit` 재감사에서 `unresolved_active_common`/`corporate_action_pending_confirmation` 등으로 새로 분류될 수 있음(정직한 상태) — 이는 corporate_action_events 보강 대상. 전략 백테스트가 이 96종목을 쓰면 결과가 바뀔 수 있어 `audit_selected_strategy_price_integrity.py` 재실행 권장.

2026-09-24(7차) 원가 통일 후 재감사·후속. (1) `scripts/reclassify_stale_price_jump_audit_20260923.py --apply`로 stale 감사 9,782건 재분류(885 삭제, 8,897 갱신): `externally_confirmed_internal_corruption` 204→**0**, `unresolved_active_common` 4,961→4,365. (2) **회귀 수정**: 6차 원가 통일을 2018-01-01부터만 적용해 2018-01-02에 새 이음매 점프가 생김 → 같은 96종목의 2018년 이전 구간도 marcap 2010~2017로 통일(`apply_marcap_raw_basis_restore_20260924.py --pre2018`, 83종목·55,884행, run_id `marcap_raw_basis_restore_pre2018_20260924_*`). 교훈: 원가 복원은 항상 종목 전 구간(marcap 커버 범위 전체)에 적용할 것. (3) 통일로 드러난 실제 원가 점프 113건을 marcap 종가·발행주식수로 검증해 `scripts/record_raw_confirmed_jumps_20260924.py`로 기록: 발행주식수 변화가 배율과 일치하는 36건은 `corporate_action_events`에 `marcap_shares_daily`/`review_required` 이벤트 36개 추가+`corporate_action_pending_confirmation`, 나머지 76건은 `raw_source_confirmed_jump_review`(원가에서도 발생, 무상/유상증자 시점 등 원인 미확정). DART 공시 확정 시 `factor_confirmed`로 승격 가능. 다음 과제: 이 36개 review_required 이벤트의 DART 확정.

2026-09-24(8차) marcap 검출 corporate_action_events 36건 DART 확정. `record_raw_confirmed_jumps_20260924.py`가 만든 review_required 이벤트 36건 중 로컬 `dart_disclosures`(2건) + OpenDART `list.json` 직접 조회(29건, 로컬 공시 테이블 누락분)로 분할/병합/감자 공시 유형과 발행주식수 비율이 일치하는 **31건을 `factor_confirmed`로 승격**(`backward_price_factor=old_shares/new_shares`, source `marcap_shares_daily+DART`, 신뢰도 0.9; `scripts/confirm_marcap_events_with_dart_20260924.py` + 인라인 라이브 조회, `data_fix_log` 기록). 대응 `price_jump_audit` 행은 `confirmed_corporate_action`으로 갱신. 미확정 5건: 065060·071950·101400·284620은 유상증자/권리락 공시뿐이라 유형 불일치(TERP+발행가 필요), 207940은 공시 없음 → review_required 유지. 참고: 로컬 `dart_disclosures`가 감자·분할 공시를 상당수 누락하고 있으므로(34건 중 33건이 라이브 조회에서만 발견) 이 테이블을 근거로 "공시 없음" 판정하지 말 것.

2026-09-24(9차) 전체 재감사 결과 급증 원인 확인(진행 중). 11:05 `audit_price_jumps_and_build_canonical.py` 전체 재구성 결과 `unresolved_active_common` 20,231·`mixed_basis_or_price_corruption` 4,431로 급증(직전 stale 재분류 후 4,365/29). 원인: **소수점이 아닌 정수 값으로 섞여 있던 분할조정(yfinance/FDR 소급조정) 행**이 원가(marcap 교체분)와 교차해 246960 같은 종목에서 2~4배 점프가 수백 번 반복(예: 246960 2019-02-22 ph 4,100 vs marcap 16,400=4:1 분할 조정값). 이 시간대에 **다른 세션이 같은 문제를 동시에 수정 중**(`residual_fractional_repair_marcap/fdr_20260924`, `fdr_basis_conflict_fix_20260924_111039` 11,043행 등, run_id는 `data_fix_log` 참조) — 동일 종목·행을 중복 수정하지 않도록 이 세션은 `price_history` 추가 수정을 중단하고, 해당 세션 작업 종료 후 `reclassify_stale_price_jump_audit_20260923.py`/전체 재감사로 재확인할 것. 246960은 수정 후에도 marcap 대비 170행 잔존(2019~) → 마무리 확인 필요.

### 2026-09-24 소스/운영 저장소 통합 1~2단계 (runtime을 단일 기준으로)
- 발견: `/Volumes/Realtek_NVME/stock_dashboard`(소스, 63커밋)와 `runtime/`(운영, 별도 git 저장소)은 **히스토리가 무관한 두 저장소**(같은 GitHub 원격). 코드는 runtime이 훨씬 앞서 있음(main.py 7,366 vs 5,549줄). 앞으로 코드 수정은 **runtime 한 곳**에서만 한다.
- 1단계: runtime 미커밋 623건을 5개 논리 커밋으로 보호(`3c3a893`~`b05d613`, push 안 함, `data/`·`data_cache/`·`.verification/`·`hs_trade_lab/data/` 및 런타임 상태 json은 제외).
- 2단계: 소스에만 있던 tracked 파일 80개를 덮어쓰기 없이 이식(`73965d5`) — `routes/peer_compare.py`, `collectors/dart_product_mix_collector.py`, `frontend/src/views/PeerCompareView.jsx`, `antigravity_workspace/`, agi/gemini 도구 등. peer_compare는 같은 날 등록 완료(`aedc284`): `main.py`에 `/api/peer-compare` 라우터, `App.jsx`에 `peer_compare` 탭(NAV '동종기업 비교', 섹터 로테이션 아래), 라우트는 `connect_primary_db` 사용(PostgreSQL `company_product_mix` 14,482행), 프론트 빌드+`launchctl kickstart -k` 재시작 후 3종목 응답 확인.
- 소스 폴더 의존 잔존: crontab `gemini_gems_worker.py`(매일 06:00, 소스 경로에서 실행), `ai.hermes.gateway.plist` PATH의 `antigravity_workspace/venv`. 이 둘을 옮기기 전에는 소스 폴더를 폐기하지 않는다.
- 2026-09-24 소스 저장소 은퇴: `/Volumes/Realtek_NVME/stock_dashboard/.git`(133GB, 원격에 없던 14커밋 포함)을 `.git.retired`로 이름 변경(삭제 아님, 되돌리려면 `mv .git.retired .git`). 원격 미푸시 14커밋은 `runtime/backups/source_repo_unpushed_14commits_20260924.bundle`(15MB)에 보존. 상위 폴더는 공용 데이터 루트(`postgresql16/`·`stock.db`·`reports/`·`logs/`·`browser_profiles/`)이므로 삭제 금지. gemini_gems_worker 크론은 runtime 경로·PostgreSQL로 이전 완료. 이후 git 작업은 `runtime/`에서만.
