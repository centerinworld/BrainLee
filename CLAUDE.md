# 주식 대시보드 — Claude 필수 참조 문서

---

## ⚠️ CLAUDE 필수 행동 규칙 (모든 세션에서 자동 적용)

> **이 섹션은 Claude가 반드시 따라야 할 행동 규칙입니다. 예외 없이 적용됩니다.**

### 프로젝트 경로·DB (필수)

- **코드 수정·git 작업은 `/Volumes/Realtek_NVME/stock_dashboard/runtime` 한 곳에서만.** 상위 폴더는 공용 데이터 루트(`.git`은 `.git.retired`로 은퇴, 코드 복사본 만들지 않음).
- **운영 DB = PostgreSQL**(`.env` `POSTGRES_DATABASE_URL`, 데이터 `postgresql16/`). 연결은 `db_compat.connect_primary_db()`. `stock.db`(SQLite)는 레거시 스냅샷 — 운영 판단 근거 금지, PG 실패 시 SQLite 폴백 금지(섹션 2).
- `/Applications/stock_dashboard`, `/System/Volumes/Data/Volumes/...` 사용·하드코딩 금지. 경로는 `Path(__file__)` 기준 또는 `/Volumes/Realtek_NVME/stock_dashboard/...`.
- 키움 REST는 등록 IP에서만 인증됨 — 8050 알림이 오면 포털 허용 IP에 등록(섹션 4).

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

**업데이트 위치**: 해당 섹션을 직접 수정 + 섹션 12(변경 이력)에 날짜와 함께 **정말로 1~3문장만** 기록.

> 🪙 **토큰 최적화 규칙 (2026-09-03 재도입, 2번째 재발)**: 이 CLAUDE.md는 `/Volumes/Realtek_NVME/stock_dashboard/runtime`에서 세션이 시작될 때마다
> **전체가 자동으로 컨텍스트에 로드**됩니다(858KB → 2026-09-03 기준 142KB로 축소). 섹션 11이 2026-07-17 archive 분리 후 6주 만에
> 다시 774KB로 재폭증했던 원인은 항목마다 수백~수천자짜리 상세 리포트를 그대로 붙여넣었기 때문입니다. 재발 방지:
> 1. 변경이력 항목은 **한 줄~세 줄 요약만**. 근거 SQL/CSV/실험 결과/장문 분석은 `docs/` 또는 `scratch/`에 날짜 붙인 별도 파일로 만들고 CLAUDE.md에는 파일 경로만 링크.
> 2. 섹션 12는 최근 20~25개 항목만 유지. 초과분은 오래된 것부터 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래로 이동.
> 3. CLAUDE.md에 새 표/코드블록/장문 설명을 추가하기 전, 그 정보가 정말 "매 세션 필요"한지 먼저 판단할 것 — 1회성 조사·검증 결과는 CLAUDE.md가 아니라 `docs/`에 남긴다.

### Codex/Claude 병렬 작업 시 충돌 방지 규칙

> **Codex와 Claude가 동시에 이 프로젝트를 수정함. 충돌 방지 필수.**

- 작업 시작 전 `git pull --rebase` 로 최신 코드 동기화
- **같은 파일을 동시에 편집하지 않는다** — 작업 파일을 CLAUDE.md 상단에 미리 명시
- 코드 수정 후: 서버 재시작 필수 — `scripts/safe_restart_backend.sh` 권장(위 규칙 참조). 프론트 수정 시 `cd frontend && npm run build` 먼저
- Python 코드 수정 = 서버 재시작 없이는 변경 미반영 (uvicorn은 모듈 캐시)
- **routes/*.py, ETF_check/routes_etf.py 수정 시**: 서버 재시작 필수
- DB 스키마 변경 시: 다른 AI가 같은 테이블을 수정 중인지 반드시 확인

### 토큰 절약 규칙
- 파일 전체를 읽기 전에 이 문서에서 줄 번호를 확인하고 해당 범위만 읽는다.
- DB 스키마 확인 → 섹션 2 참조 (init_db.py 열지 않음)
- API 엔드포인트 확인 → 섹션 3 참조 (routes/*.py 열지 않음)
- 컴포넌트 위치 확인 → 섹션 6 참조 (App.jsx 전체 스캔 안 함)
- 전체 API/스케줄러/테이블 목록 → `docs/API_ENDPOINTS.md` · `docs/SCHEDULER_JOBS.md` · `docs/DB_TABLES_PG.md` (자동 생성, 수동 편집 금지 — 해당 코드를 바꾼 뒤 `scripts/ops/gen_*_doc.py` 재실행)

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
/Volumes/Realtek_NVME/stock_dashboard/            # 공용 데이터 루트 (git 아님, .git.retired)
├── postgresql16/  stock.db(레거시 SQLite)  reports/  logs/  browser_profiles/  antigravity_workspace/venv/   # 삭제 금지
└── runtime/                                       # ★ 운영본 = 유일한 git 저장소 (LaunchAgent com.stock-dashboard.local)
    ├── main.py            # FastAPI 앱 + 라우터 등록 (~7,370줄) — 서버 진입점(:8000)
    ├── scheduler.py       # CollectionScheduler, 루프 116개 (~6,080줄) → docs/SCHEDULER_JOBS.md
    ├── signal_engine.py   # 시그널/스크리너 계산 (~6,200줄)
    ├── tenbagger_engine.py / peak_monitor.py / kis_client.py / notifier.py(텔레그램)
    ├── config.py          # .env 로드 (DATABASE_URL, IS_POSTGRES)
    ├── db_compat.py       # ★ sqlite3 스타일 API를 PostgreSQL로 라우팅 — connect_primary_db()
    ├── db_utils.py / database.py / models.py
    ├── runtime_pg_bootstrap/  # sitecustomize.py: 스크립트의 sqlite3.connect를 PG로 리다이렉트(PYTHONPATH로 주입)
    ├── routes/            # FastAPI 라우터 41개 → 목록 docs/API_ENDPOINTS.md
    ├── collectors/        # 외부 수집기 47개 (kis/krx/dart/kiwoom/yahoo/fnguide/…)
    ├── backtest_strategies/  backtest.py(래퍼)  # 전략 43개 파일, 백테스트/전략센터
    ├── ETF_check/         # ETF 수집·검증 (crontab, 별도 routes_etf)
    ├── employment_monitor/  Sector_define/  hs_trade_lab/   # 고용정보 / 섹터정의 / 수출입 HS
    ├── scripts/           # 배치·백필·운영 스크립트 ~300개 (ops/ 하위: gen_*_doc.py, quant_indicators_cron.py 등)
    ├── tests/  verification/  scratch/(일회성 분석)  research_outputs/  docs/  backups/
    ├── frontend/src/App.jsx  + views/*.jsx            # React SPA (~18,670줄, 섹션 6)
    ├── data/  data_cache/                             # 런타임 데이터(미추적)
    ├── launchd/  run/                                 # plist 사본
    └── .claude/hooks/     # session_start.sh(매 프롬프트 CLAUDE.md 지시 주입), session_stop.sh
```

- 문서: `CLAUDE.md`(이 파일, 매 세션 로드) · `hermes.md`/`hermes_change.md`(Hermes 에이전트 작업 현황/변경 로그) · `docs/*.md`(자동 생성 정본: SCHEDULER_JOBS / API_ENDPOINTS / DB_TABLES_PG, 아카이브: CLAUDE_CHANGELOG_ARCHIVE / CLAUDE_KNOWN_ISSUES_RESOLVED) · `PROJECT_MASTER.md`(폐기 안내 — 정본은 이 파일) · `docs/legacy/`(3~5월 SQLite 시대 문서, 참고용).
- 스크립트를 직접 실행할 땐 PG 라우팅을 위해: `cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 <script>`.

---

## 2. DB 스키마 및 저장 위치

### DB/저장 경로 강제 규칙 (2026-09-04)

> **운영 기준 DB는 PostgreSQL이다. 모든 운영 조회, 백테스트, 전략 검증, run registry 및
> `signal_experiment_ledger` 기록은 `.env`의 `POSTGRES_DATABASE_URL`을 그대로 사용한다.**

- 기본 연결은 반드시 `config.DATABASE_URL` + `db_compat.connect_primary_db()`를 사용한다. 백테스트 명령에서 `POSTGRES_DATABASE_URL`이나 `DATABASE_URL`을 `sqlite:`로 덮어써 PostgreSQL 라우팅을 우회하지 않는다.
- PostgreSQL 데이터 디렉터리와 프로젝트 데이터·캐시·연구 산출물은 모두 외장 SSD의 `/Volumes/Realtek_NVME/stock_dashboard/` 아래에 둔다.
- `/Volumes/Realtek_NVME/stock_dashboard/runtime/`은 **LaunchAgent 운영본이자 유일한 git 저장소**다(상위 폴더는 공용 데이터 루트 — `postgresql16/`·`stock.db`·`reports/`·`logs/`). 코드는 runtime에서 수정하고, **대용량 데이터·산출물·DB 경로는 상위 정규 경로 `/Volumes/Realtek_NVME/stock_dashboard/...`를 쓴다**(runtime 하위에 새 대용량 데이터 생성 금지).
- `stock.db`는 레거시/오프라인 호환용 SQLite 스냅샷이다. 운영 성과 판정, 전략 채택·기각, 원장 기록의 근거로 사용하지 않는다. 독립 SQLite side DB가 꼭 필요한 경우에도 저장 위치는 외장 SSD 아래로 제한하고 용도를 명시한다.
- 운영 PostgreSQL 접속 실패 시 SQLite로 자동 또는 수동 폴백해 검증을 계속하지 않는다. 실패 원인을 해결한 뒤 PostgreSQL에서 처음부터 재실행한다.

### 주요 테이블

| 테이블 | 행수 | 핵심 컬럼 | 용도 |
|--------|------|-----------|------|
| `price_history` | 1023.7만 | stock_code, date, open/high/low/close, volume, inst_net_buy, frn_net_buy, ind_net_buy, **inst_net_buy_amt**, **frn_net_buy_amt**, **ind_net_buy_amt** | 일별 OHLCV + 투자자수급 |
| `stock_universe` | 5,459 | stock_code, stock_name, market, sector_large, shares_issued, market_cap, per, pbr, roe, roa | 전 종목 마스터 |
| `financial_data` | 20.1만 | stock_code, year, quarter, revenue, operating_profit, net_income, total_assets, total_equity, eps, bps, is_annual | 재무제표 |
| `peak_holding` | 366 | stock_code, stock_name, buy_price, current_price, quantity, entry_date, is_active, strategy, profit_pct | 가상매매 보유 |
| `peak_trade` | 818 | stock_name, tx_type(buy/sell), price, quantity, profit, strategy | 가상매매 거래내역 |
| `portfolio` | 53 | stock_code, quantity, avg_price, bought_at | 실제 포트폴리오 |
| `portfolio_snapshot` | 4,482 | snapshot_date, stock_code, close_price, quantity, eval_amount, profit_pct | 일별 스냅샷 |
| `portfolio_tx` | 86 | stock_code, tx_type, quantity, price, tx_date | 거래내역 |
| `signal_config` | 26 | scope, name, label, logic_type, params, is_active | 시그널 설정 |
| `signal_result` | 1.4만 | config_id, stock_code, signal(green/yellow/red), score | 시그널 결과 |
| `stock_meta` | 2,784 | stock_code, float_shares, shares_outstanding | 유동주식수 |
| `short_sell_daily` | 393.4만 | bas_dt, stock_code, short_qty, borrow_bal_qty, borrow_bal_pct | 대차잔고/공매도 |
| `buy_candidates` | 31 | stock_code, target_price, memo | 매수 후보 |
| `watchlist` | 191 | stock_code | 관심종목 |
| `telegram_channels` | 14 | channel_id, channel_name | 텔레그램 채널 |
| `report_files` | 2.6만 | stock_code, sector, report_date, file_path | 섹터 보고서 |
| `backtest_runs` | 3,209 | run_id, status, total_return_pct, trades_json | 백테스트 결과 |
| `strategy_feature_snapshot` | 19.0만 | snapshot_date, stock_code, close_price, market_cap_억, per, pbr, ret_20d/60d/120d, dist_high_252, vol_ratio_20d, supply_20d_억, label_2x/3x_6m/12m, **forward_max_ret_24m/36m, label_3x/5x/10x_24m, label_5x/10x_36m**, heuristic_score, model_score_6m/12m | 전략 연구용 월말 피처 스냅샷 + forward 라벨 + 휴리스틱/ML 점수. `scripts/build_strategy_research_dataset.py`가 생성/전량 재구축. ★신규(2026-07-05) / **2026-08-08 24·36개월 라벨 7컬럼 추가** — 실제 10배 종목은 중위 609일(1.7년) 소요라 기존 12개월 창으로는 86.9%가 관측 불가였음. 라벨 유효구간: 24m는 스냅샷 ≤2024-08-07(126,879행), 36m는 ≤2023-08-08(97,188행). 기준율 label_10x_24m 1.50% / label_10x_36m 2.37%. **모든 라벨은 비율 스케일(1.0=+100%) — 3배=2.0, 5배=4.0, 10배=9.0** |
| `investor_trading_daily` | 452.0만 | bas_dt, stock_code, indv_net, inst_net, frgn_net | ⚠️ deprecated — 원천 API 폐지, 매수전용 오염값. 사용 금지(대체: `kiwoom_investor_daily`, 섹션 9 참조) |
| `foreign_holding_daily` | 10.8만 | bas_dt, stock_code, frgn_hold_pct | ✅ Kiwoom ka10008 경유 적재 중 |
| `kiwoom_investor_daily` | 472.8만 | stock_code, dt, ind_invsr, frgnr_invsr, orgn + 세부기관분류 | ✅ 키움 ka10059 (개인/외국인/기관 + 10개 기관세부) |
| `financial_source_snapshot` | 15.8만 | stock_code, year, is_annual, report_type, data_source('fnguide'), revenue, op_profit, net_income, verification_status | FnGuide 원본 스냅샷 (마스터) |
| `financial_anomalies` | 5,339 | stock_code, anomaly_type, severity, is_resolved | 재무 이상 분류 (unit_error/cfs_ofs/large_discrepancy 등) |
| `stock_collection_config` | 310 | stock_code, config_key, config_value | 종목별 수집 특성 (report_type/unit_verified 등) |
| `company_mapping_profile` | 1.7만 | stock_code('*'=공통), standard_key, source_system, account_id(XBRL), account_label_raw, confidence_score, valid_from/to, verified_by, is_active | 기업별 DART 계정 매핑 프로파일. 2026-07-21 확장: DART fnlttSinglAcntAll 실계정id를 financial_data와 대조검증해 revenue/operating_profit/net_income/total_assets/total_equity 5개 키 기준 2,282종목 확정(is_active=1) + 검증대기 다수(is_active=0, review 큐) |
| `dart_raw_accounts` | 112 | stock_code, year, quarter, report_code, fs_div, account_id, account_nm, thstrm_amount, rcept_no | DART 원문 계정 저장. anchor_mismatch 4종목 2022년 CFS 원문(DART account_id는 API 미제공) ★신규(2026-05-16) |
| `data_quality_issues` | 79 | stock_code, year, quarter, table_name, field_name, reason_code(SOURCE_MISSING/ANCHOR_MISMATCH 등), severity, is_resolved | Null Sentinel — ANCHOR_MISMATCH 4건(HIGH)+SOURCE_MISSING 75건(금융업 DART미제공) ★신규(2026-05-16) |
| `data_lock` | 6,840 | stock_code, year, table_name, is_locked, lock_basis('dart_verified'), lock_hash(md5) | Freeze 정책 — 2019~2022 DART 검증 완료 전량 잠금. financial 1,282건+cashflow 5,558건 ★신규(2026-05-16) |
| `fin_quarterly_validation_flags` | 51.8만 | stock_code, year, quarter, field, check_type(ANNUAL_CONSISTENCY/DART_FG_CROSS), dart_value, fnguide_value, annual_value, quarterly_sum, ratio, status(CONFIRMED/AMBIGUOUS/STRUCTURAL/OPEN), ai_verdict, notes | 분기 재무 3중 검증 (DART+FnGuide+AI). ★신규(2026-05-23) |
| `tenbagger_results` | 3,961 | stock_code, stock_name, total_score, axis1~6, reasons, run_time | 텐버거 발굴 엔진 결과 (6축 스코어링). ★신규(2026-06) |
| `tenbagger_daily_alerts` | 613 | alert_date, stock_code, stock_name, total_score, reasons, is_new(신규=1), best_reason(왜 최고 종목인지 분석), created_at. UNIQUE(alert_date, stock_code) | 텐버거 아침 알림 이력. tenbagger_morning_alert.py 실행시 저장. ★신규(2026-06-13) |
| `tenbagger_ai_analysis` | 8 | stock_code, analysis_text, created_at | DeepSeek 심층 분석 캐시(24h). ★신규(2026-06) |
| `dart_backlog_quarterly` | 1.8만 | stock_code, fiscal_year, fiscal_quarter, report_type, backlog_amount_krw, source_rcept_no | 수주잔고 분기별 추이. order_backlog와 병렬 저장. ★신규(2026-06) |
| `dart_cost_quarterly` | 6.7만 | stock_code, fiscal_year, fiscal_quarter, cogs, sg_a, gross_margin_pct | 원가 구조 분기별. cost_structure와 병렬 저장. ★신규(2026-06) |
| `dart_tenbagger_triggers_quarterly` | 10.4만 | stock_code, fiscal_year, fiscal_quarter, metric_name, metric_value, yoy_pct, trigger_level | 텐버거 트리거 지표 (BACKLOG_SURGE 등). ★신규(2026-06) |
| `kiwoom_credit_balance` | 408.9만 | stock_code, dt, credit_balance_qty, credit_balance_amt, credit_ratio, new_credit_qty, repay_credit_qty | Kiwoom ka10013 신용거래잔고(일별). tenbagger_engine credit_trend 우선 소스. 5년치 수집 진행중(max_pages=13). ★신규(2026-06) |
| `kiwoom_foreign_flow` | 30.9만 | stock_code, date, weight(외국인지분율%), frg_hold_qty | Kiwoom ka10008 외국인 지분율 추이. ★신규(2026-06) |
| `investor_flow_quarterly` | 9.0만 | stock_code, year, quarter, ind_net_sum, frgnr_net_sum, orgn_net_sum, trading_days, source | 투자자 분기별 순매수 집계(price_history 기반, 2018~2026, 3962종목). ★신규(2026-06-11) |
| `foreign_flow_quarterly` | 8.7만 | stock_code, year, quarter, frn_net_buy_amt_sum, frn_net_buy_qty_sum, trading_days, weight_end, source | 외국인 분기별 순매수 집계(price_history 기반, 2019~2026, 3890종목). ★신규(2026-06-11) |
| `dart_insider_holdings` | 6.2만 | stock_code, corp_code, officer_name, trade_type(취득/처분), shares, report_date, is_ceo | DART 임원 매매 공시. tenbagger_engine insider_signal 소스. ★신규(2026-06) |
| `order_backlog` | 2.1만 | stock_code, year, quarter, backlog_amount, backlog_normalized(백만원), data_source | 수주잔고 (건설/조선 등). ★신규(2026-06) |
| `cost_structure` | 5.3만 | stock_code, year, quarter, cogs_pct, sg_a_pct, gross_margin_pct | 원가율 구조. ★신규(2026-06) |
| `cost_breakdown` | 2.4만 | stock_code, year, quarter, material_cost, labor_cost, overhead | 원가 세부 분해. ★신규(2026-06) |
| `dilution_events` | 2.1만 | stock_code, rcept_no, event_type(CB/BW/EB/RIGHTS/BONUS), issue_amount, dilution_pct, conversion_price, put_option_date | 희석 이벤트. 건수 기반 리스크는 사용 가능하나, issue_amount는 12,015행(67.80%) / 1,238종목으로 **금액 기반 리스크는 부분완료**. DART 과거 문서 cp949/euc-kr 디코딩 보강 후 2020년 74.6%, 2021년 58.6%, 2022년 74.0%까지 복구. 목표 커버리지 80%+. `dart_disclosure_parse` 잔여는 대부분 만기전취득/자기전환사채/종속회사/권리락/가격확정 등 금액 필드로 해석하면 안 되는 레거시 행. ★신규(2026-06, 2026-07-21 보강) |
| `triple_pattern_daily` | 354 | stock_code, dt, triple_score, tenbagger_score, supply_signal | BigQuery 3배주 복합 신호 일별. ★신규(2026-06) |
| `valuation_history` | 6.4만 | stock_code, year, quarter, period_end, close_price, eps, bps, per, pbr, market_cap_억 | 분기별 역사적 PBR/PER 밸류에이션 이력. financial_data+price_history 기반 계산. ★신규(2026-06-11) |
| `segment_revenue` | 2.6만 | stock_code, corp_code, year, quarter, segment_name, revenue(백만원), operating_profit(백만원), assets(백만원), report_type | DART 사업부문/세그먼트 매출. **⚠️ "95% 커버" 표기 주의**: 2,561종목(95.10%)은 `segment_name`이 `연결전체`(총계 1행)만 있어도 카운트된 값 — 실제 제품/사업부/지역별 세부 breakdown이 있는 종목은 **319종목(12.23%)뿐**(2026-07-29 재감사, `scripts/audit_segment_dilution_coverage.py`). 제품노출도 기반 신호에는 반드시 breakdown coverage(12.23%) 기준으로 판단할 것 — 95%는 "데이터 존재 여부"이지 "세그먼트 분해 가능 여부"가 아님. ★신규(2026-06-11, 2026-07-21 현황 정정, 2026-07-29 breakdown 커버리지 분리) |
| `program_trading_daily` | 3,484 | dt, market(KOSPI/KOSDAQ), prog_net_buy_amt(억원), arb_net_buy_amt(차익,억원), non_arb_net_buy_amt(비차익,억원), source | KRX 프로그램매매 일별. KRX MDCSTAT05301(KOSPI)/05401(KOSDAQ) Playwright 수집. 스케줄러 18:20 KRX프로그램매매 잡 등록완료, KRX 로그인 정상화 시 자동수집. ★신규(2026-06-13) |
| `dart_rd_patent_signals` | 2,501 | stock_code, rcept_no, rcept_dt, report_nm, signal_type(patent/tech_transfer/rd_contract/license), amount_krw, notes. UNIQUE(rcept_no, signal_type) | DART 특허/기술이전/R&D/라이선스 공시. dart_disclosures 파싱. 텐버거 엔진 연동(1년내 기술이전+3점/특허+2점/R&D+1점). ★신규(2026-06-15) |
| `analyst_pdf_extracts` | 461 | report_id(UNIQUE), stock_code, target_price, opinion, fwd_eps_1y, fwd_rev_1y, fwd_per, extracted_at, raw_text | PDF 보고서에서 gpt-4o-mini로 추출한 컨센서스 지표 캐시. routes/reports.py 자동 생성. ★신규(2026-07-05) |
| `earnings_signals` | 2,724 | stock_code, signal_type(turnaround/revenue_surge/profit_accel), ttm_eps, qoq_streak | TTM 실적 신호 자동 탐지. ★신규(2026-06-01) |
| `quant_major_indicator_catalog` | 301 | indicator_key(epic:N:M), epic_indicator_name, status, source_system, frequency, base_unit | EPIC 대체지표 카탈로그. ★신규(2026-06) |
| `quant_major_indicator_series` | 21.8만 | indicator_key, period_str(YYYY-MM), value, unit, source | 퀀트 주요지표 시계열. ★신규(2026-06) |
| `margin_balance_daily` | 9.3만 | stock_code, dt, credit_balance, collected_at | 신용잔고 일별 (kiwoom_credit_balance fallback용). ★신규(2026-06) |
| `live_orders` | 48 | order_id, parent_order_id, mode, strategy_key, stock_code, side, order_type, qty, limit_price, status, filled_qty, avg_fill_price, decision_reason | 실전형 주문 생애주기 마스터. `kis_paper_orders`(구)와 병행 기록. ★신규(2026-07-23, Codex A1 제안) |
| `live_order_events` | 96 | order_id, event_ts, event_type(SUBMITTED/FILLED/...), qty_delta, price, detail | 주문별 이벤트 로그. ★신규(2026-07-23) |
| `live_fills` | 48 | order_id, fill_ts, fill_qty, fill_price, cumulative_qty | 개별 체결 기록(현재는 단일체결만, 부분체결 확장 여지). ★신규(2026-07-23) |
| `live_cash_ledger` | 1 | ts, mode, delta_krw, balance_after, reason, ref_order_id | 페이퍼 현금원장(seed 1억원 기본, `KIS_PAPER_INITIAL_CASH`로 조정). ★신규(2026-07-23) |
| `risk_gate_decisions` | 1,982 | ts, stock_code, side, strategy_key, decision, reasons, gate_snapshot, order_id | A2 리스크게이트 판정 이력(전량 기록, 차단/통과 모두). ★신규(2026-07-23, Codex A2 제안) |

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
> 행수는 2026-09-24 PostgreSQL 추정치. 전체 테이블(407개)·컬럼은 [docs/DB_TABLES_PG.md](docs/DB_TABLES_PG.md)(자동 생성: `python3 scripts/ops/gen_db_doc.py`).


## 3. API 엔드포인트

> 전체 459개 엔드포인트 — **정본은 [docs/API_ENDPOINTS.md](docs/API_ENDPOINTS.md)**(자동 생성: `python3 scripts/ops/gen_api_doc.py`)와 `http://127.0.0.1:8000/docs`. 라우터 파일은 `routes/*.py`, 등록은 `main.py`의 `include_router`. 새 라우트 추가 후 서버 재시작 → gen_api_doc 재실행.
> 예전 수기 목록(파라미터·캐시키 설명 포함)은 [docs/CLAUDE_API_SECTION_ARCHIVE_20260924.md](docs/CLAUDE_API_SECTION_ARCHIVE_20260924.md).

| 그룹(prefix) | 개수 | 그룹 | 개수 |
|---|--:|---|--:|
| `/api/tenbagger` | 46 | `/api/quant-major-indicators` | 6 |
| `/api/backtest` | 43 | `/api/consensus` | 5 |
| `/api/trend` | 28 | `/api/dart-excel` | 5 |
| `/api/signals` | 24 | `/api/cherry-screener` | 4 |
| `/api/dashboard` | 22 | `/api/company-intelligence` | 4 |
| `/api/us` | 19 | `/api/earnings-signals` | 4 |
| `/api/kiwoom` | 18 | `/api/ingest` | 4 |
| `/api/market-indicators` | 18 | `/api/insider` | 4 |
| `/api/global-macro` | 17 | `/api/cash-conversion-signals` | 3 |
| `/api/market-radar` | 15 | `/api/contract-advance-signals` | 3 |
| `/api/cafe-signals` | 14 | `/api/global-foreign-flow` | 3 |
| `/api/kis-trading` | 14 | `/api/inventory-sales-signals` | 3 |
| `/api/commands` | 13 | `/api/notices` | 3 |
| `/api/portfolio` | 11 | `/api/extra-signals` | 2 |
| `/api/reports` | 9 | `/api/investment-decisions` | 2 |
| `/api/detailed-analysis` | 8 | `/api/realtime` | 2 |
| `/api/employment-v2` | 8 | `/api/us-13f` | 2 |
| `/api/etf-check` | 8 | `/` | 1 |
| `/api/sector-rotation` | 8 | `/api/antigravity` | 1 |
| `/api/stock-analysis-rs` | 8 | `/api/market-regime` | 1 |
| `/api/us-virtual` | 8 | `/api/namu` | 1 |
| `/api/order-contracts` | 7 | `/api/peer-compare` | 1 |
| `/api/sector-define` | 7 | `/api/search` | 1 |
| `/api/telegram` | 7 | `/api/source-intelligence` | 1 |
| `/api/buy-candidates` | 6 | `/api/strategy-data-lab` | 1 |
| `/api/dart-contracts` | 6 |  |  |

핵심 규칙: 시그널 캐시키/TTL은 섹션 5, DB 연결은 섹션 2·8, 단위는 섹션 2 '중요 단위 규칙'.

## 4. 스케줄러 (scheduler.py)

> `CollectionScheduler`(scheduler.py)가 FastAPI 프로세스 안에서 스레드 루프 116개를 돌린다. **전체 잡 목록·시각은 [docs/SCHEDULER_JOBS.md](docs/SCHEDULER_JOBS.md)**(자동 생성: `python3 scripts/ops/gen_scheduler_doc.py`). 잡 추가/변경 시 등록부(`self._loops` 리스트 주석에 시각·설명 기재) 수정 후 재생성.
> DB 쓰기 잡은 `_DB_WRITE_JOBS`에 등록해 배타 락을 건다(읽기 전용/로깅 잡은 제외). 실패 잡은 자동 재시도하지 않는 것이 많으므로 결측일은 스크립트로 백필(`scripts/`).
> 키움 REST는 **등록 IP에서만 인증**된다. `키움IP감시`(24h/10분)가 공인 IP 변경을 감지해 8050이면 텔레그램 알림 — 알림의 IP를 키움 포털 허용 IP에 등록(2026-09-24).

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

**App.jsx = 18,670줄**. ★2026-09-03 대규모 분리: `Screener`/`PeakView`(→`StrategyCenterView.jsx`)/`BacktestView`/`StrategyHub` 등 8개 컴포넌트가 App.jsx에서 `frontend/src/views/*.jsx` 개별 파일로 이동하고 `React.lazy()`로 로드되도록 변경(토큰 최적화, 섹션 11 참조). 아래 표는 2026-09-04 기준 재검증된 최신 줄번호. `const App = () => {`는 12896줄에서 시작.

⚠️ **분리 시 주의**: App.jsx 모듈 스코프에 있던 헬퍼 함수(`fmtKrw`, `fmtPctUs` 등)는 별도 파일(별도 ES 모듈)로는 자동으로 따라가지 않는다 — 분리 시 반드시 `frontend/src/utils.js`에 있는지 확인 후 명시적으로 import할 것. 2026-09-04에 이 누락으로 인한 `ReferenceError` 크래시 2건이 실제로 발생(섹션 9 참조).

### 별도 파일로 분리된 컴포넌트 (views/)
| 파일 | 탭 키 / 용도 |
|------|-------|
| `frontend/src/views/MarketIndicatorsView.jsx` | market_indicators |
| `frontend/src/views/MarketRadarView.jsx` | market_radar |
| `frontend/src/views/SemiconductorView.jsx` | semiconductor_sector (탭 렌더가 실제 사용하는 파일) |
| `frontend/src/views/SectorFollowupView.jsx` | sector_followup (MarketRadar 내부) / hot_sector |
| `frontend/src/views/SectorRotationView.jsx` | sector_rotation |
| `frontend/src/views/PeerCompareView.jsx` | peer_compare (NAV '동종기업 비교') ★신규(2026-09-24 운영 등록) — `/api/peer-compare/compare`, PostgreSQL `company_product_mix` 매출구성 |
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

### App.jsx 내 컴포넌트 → 탭 키 → 시작 줄번호 → 위치 (2026-09-24 재생성, 줄번호는 편집 시 밀리므로 근사치 — 정확한 위치는 `grep -n 'const 이름 =' frontend/src/App.jsx`)
| 컴포넌트 | 탭 키 | 줄번호 | 위치 |
|---------|-------|--------|------|
| `SignalBoard` | (헤더 상시 노출) | 112 | module-level |
| `SignalSettings` | (settings 내부) | 835 | module-level |
| `SettingsView` | settings | 1016 | module-level |
| `EmploymentView` | employment | 1392 | module-level |
| `USInsightView` | (미국 인사이트) | 1553 | module-level |
| `TradeAnalysis2` | hs_trade2 | 1746 | module-level |
| `DartContractView` | dart_contracts | 3645 | module-level |
| `DetailedAnalysisView` | detailed_analysis | 5526 | module-level (isMobile prop) |
| `USStocksView` | us_stocks | 6047 | module-level (isMobile prop, 미국주식/스크리너/인사이트/바이오/13F 거물 동향 탭) |
| `BuyCandidateView` | buy_candidates | 7646 | module-level (changeStock/changeTab prop) |
| `PortfolioView` | portfolio | 8297 | module-level |
| `SectorReports` | reports | 9595 | module-level |
| `TenbaggerView` | tenbagger | 9707 | module-level |
| `MegatrendView` | megatrend | 10701 | module-level |
| `TelegramMentions` | telegram | 10901 | module-level |
| `ExportHealthView` | export_health | 11159 | module-level |
| `HardeningPlanPanel`/`TurnaroundWatchPanel`/`ConsensusRevisionPanel`/`CherryScreenerPanel` | exp_roadmap 내부 서브패널(pageTab: hardening/turnaround/cherry/consensus) | 11429/11544/12326/12416 | module-level |
| `ExperimentRoadmapView` | exp_roadmap (nav 라벨 "실험 로드맵") | 12649 | module-level |
| `WatchlistView` | watchlist | 13426 | App 내부 (closure) |
| `MacroDashboard` | macro | 13522 | App 내부 (`React.useState(() => function ...)` 패턴으로 안정화, closure) |
| `AIInsight` | insight | 18102 | App 내부 (closure) |
| `SystemStatus` | system | 18179 | App 내부 (closure) |

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
KIWOOM_ENABLED=true                   # 키움 REST는 호출 IP 등록 필수(미등록 시 8050 오류)
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
| K-mydata | ❌ 인증실패 | KRX_API_KEY가 K-mydata용 아님 |
| pykrx | ❌ Empty | KRX 서버 차단으로 빈 DataFrame |
| TWSE(대만) 외국인 순매수 수집 | ❌ 접속 차단(2026-09-08 확인) | `/rwd/`·`/exchangeReport/` 등 데이터 경로가 이 Mac 네트워크에서 WAF 307("FOR SECURITY REASONS")로 전부 차단됨(루트 도메인은 200으로 정상 — 데이터 경로만 선별 차단). Referer/User-Agent 조정, Playwright 풀브라우저 모두 동일하게 막힘 — IP/지역 기반 차단으로 추정. 대체 소스 없이는 `TW_FOREIGN_FLOW_USD` 수집 불가(`collectors/asia_foreign_flow_collector.py`의 `collect_tw_foreign_flow`는 코드는 있으나 상시 0건). 우회 시도(프록시/스푸핑)는 정책상 하지 않음. |
| 중국 북향자금(HKEX Stock Connect) 순매수 수집 | ⚠️ 미구현 | 사이트 자체는 접근 가능하나(hkex.com.hk 200) Historical-Daily 통계표가 JS로 동적 렌더링됨 — Playwright로 네트워크 캡처해 찾은 `/eng/csm/DailyStat/data_tab_daily_YYYYMMDDe.js`는 Turnover(거래대금)만 있고 실제 순매수(Net Buy/Sell) 필드가 없어 사용 불가. 실제 net flow가 나오는 엔드포인트는 날짜검색 인터랙션 뒤에 있는 것으로 추정되나 미확인 — 후속 조사 필요, `CN_NORTHBOUND_FLOW_USD` 현재 0건. |
| 공공데이터포털 투자자API | ❌ 404 | getStocInvtTrdnInfo 서비스 폐지 |
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
| `ka10059` | **종목별투자자일별순매수** (개인/외국인/기관+10개 세부기관, 100행/page) | stk_cd, amt_qty_tp, trde_tp, dt, unit_tp | ✅ 수집기 정상(2026-07-21 수정, 2026-09-24 재실측): `trde_tp='0'`=순매수, `'1'`=매수, `'2'`=매도, `amt_qty_tp='1'`=금액(백만원)/`'2'`=수량. 005930 2026-09-23 순매수 = `price_history` 순매수와 1% 이내 일치. |
| `ka10095` | 관심종목 현재 시세 (복수 종목 동시 조회) | stk_cd |
| `ka10100` | 종목 상장기본정보 (상장일, 감사의견, 업종, 대형/중형/소형주) | stk_cd |

URI: /api/dostk/frgnistt
| `ka10008` | 외국인종목별매매동향 (외국인 보유주식수/지분율 추이) | stk_cd |
| `ka10009` | 외국인+기관 복합 (orgn_daly_nettrde+frgnr_daly_nettrde) | stk_cd |
| 글로벌 인텔리전스 PMI/중국 수출 | ⚠️ 미연결 | 안정적인 공식 무료 API를 아직 붙이지 못해 `CN_EXPORT`, `CN_PMI_MFG`, `EU_PMI_MFG`, `US_ISM_MFG`는 2026-07-17 기준 0건. FRED/World Bank 확장으로 정책금리·스프레드·세계무역량은 보강 완료. |
| 재무제표 Q4 대규모 손실 | ℹ️ 정상 | 잔존 14건(삼성SDI2016/현대건설2024/대한항공 등)은 실제 이벤트 손실로 수학적 정확값 |
| **EPS 저장값 vs 계산값 괴리** | ⚠️ 구조적 | FnGuide 저장 EPS = 지배주주 귀속 순이익 ÷ 보통주 수. 우리 계산 EPS = 전체 NI ÷ shares_issued(우선주 포함). 두 효과가 역방향으로 작용해 일치 불가. **올바른 계산**: 지배주주 순이익(별도 미저장) ÷ 보통주 수(미분리). FnGuide SVD_Main.asp에서 직접 수집한 EPS/BPS를 1순위로 사용할 것 |
| **TTM EPS 계산 부정확** | ⚠️ 주의 | TTM EPS = 최근 4Q net_income ÷ shares_issued 방식은 shares_issued 우선주 포함으로 EPS 과소 계산됨. 단, 분기 데이터 자체(TTM NI 합산 = annual NI)는 정확히 검증됨. 현재 stock_universe에 TTM PER 반영됨 — 외부 사이트 대비 PER 높게 나올 수 있음 |
| **PER/PBR 외부사이트 일치율** | ℹ️ 현황 | Codex 검증(500종목): revenue/op_profit 100%, net_income 97.48%, CF 98-99%. EPS(FnGuide) 67.79%, BPS(FnGuide) 82.53%. PER(Naver) 29.81%, PBR(Naver) 49.49% — 기준 연도·주식수 차이에 기인. 네이버 기준일 vs FnGuide TTM 기준일 다름 |
| **4중검증 L3 수정 후 B/S NULL 증가** | ℹ️ 구조적 | fnguide/legacy B/S 파싱오류 ~10,440건을 NULL처리함. B/S NULL 행은 P&L 표시에는 영향 없음. 향후 DART 재수집 시 자동 채워짐. data_source: fnguide_bs_null_fix, legacy_bs_null_fix, quarterly_recalc_bs_null |
| **dart_recollect 분기 NI 파싱실패** | ⚠️ 부분 해소(2026-09-24: 7,613→6,024건, 분기 NI NULL 전체 8,768건) | DART 분기 보고서 당기순이익 XBRL 태그 매핑 실패. 2026-09-19 `scratch/legacy_dart_recollect.py` 경로 크래시 수정 후 매일 00:30 재수집으로 점진 감소 중 — 감소 정체 시 별도 파싱 로직 점검 필요 |

---


### 해결된 이슈 — 재발방지 규칙 요약 (전문: [docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md](docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md))
- **모바일에서 계좌현황(포트폴리오) 진입 불가**: `window.prompt`/`alert`/`confirm` 등 브라우저 네이티브 다이얼로그는 모바일 인앱 브라우저 호환성이 보장되지 않으므로 신규 UI에 사용 금지 — 항상 앱 내부 모달 컴포넌트 사용(기존 `window.confirm` 사용처 다수 잔존, 동일 부류 위험 후속 검토 필요). 또한 NAV_ITEMS가 기능 추가로 계속 길어지고 있으므로, 자주 쓰는 개인화 메뉴(계좌/매수후보 등)는 새 항목 
- **Screener/StrategyCenterView `fmtKrw`/`fmtPctUs` 스코프 버그**: 컴포넌트를 별도 파일로 분리할 때는 반드시 참조하는 모든 헬퍼가 import돼 있는지 확인(섹션 6 상단 경고 참조).
- **백테스트 market_cap 단위 오류 반복**: 500억+=500, 1000억+=1000, 5조+=50000 (억원 그대로)
- **ETF 수집실패일 오표시**: ETF 관련 쿼리 시 반드시 `WHERE etf_amount > 0` 또는 valid_rows 필터 적용
- **섹터 트렌드 오분류**: 섹터 분류는 반드시 `sector_large` 기준으로. `sector_mid`는 신뢰도 낮음
- **수출공동 표시 이종업종 혼입**: HS코드 기반 공동 매핑 시 반드시 sector_large 교집합 필터 필수

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

## 12. 변경 이력

> 🪙 **토큰 최적화**: 이 섹션은 매 세션 자동 로드된다. **항목은 1~3문장만**, 근거/SQL/장문 분석은 `docs/`에 날짜 파일로 두고 링크만. 최근 1~2주(약 15개)만 유지하고 초과분은 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래로 이동.

### 2026-09-20 price_history 무결성 가드(P0) — close<=0 거부·수급 6필드 보존·비KR종목 공통필터
- `price_integrity.py`: `WRITE_GUARD_FUNCTION_SQL` 분리 + 트리거 함수에 `NEW.close<=0 OR NULL` fail-closed 거부 추가(검증 플래그 우회보다 먼저 실행). 라이브 반영은 `scripts/apply_price_write_guard.py` 재실행 필요(price_history에 AccessShareLock 잡은 idle-in-transaction 세션이 있어 DROP/CREATE TRIGGER는 쓰기 한가한 시간대 권장).
- `crud.py`: `merge_supply_fields()` 신규 — 장중 1분 갱신 시 기존 수급 6필드(inst/frn/ind + 금액3)를 0·NULL로 덮어쓰지 않도록 보존(기존엔 inst/frn 2필드만 보존).
- `security_master.py`: `is_kr_equity_code()` 신규(6자리 `[0-9A-Z]`만 허용, `^KS11`/`GC=F`/`USDKRW=X` 등 제외) → `routes/buy_candidates.py` POST 등록·자동보드 후보에 적용.
- 테스트 `tests/test_price_history_guard.py` 6건(트리거 롤백온리 검증 포함) 통과, 기존 46건 회귀 무손상.

### 2026-09-19 StockEasy 로직 일치율 재검토 — 모멘텀 Easy 0% 근본버그 수정 + 매수로직 개선
`stockeasy_logic_validator.py`의 `replay_entry_day_inclusion()`이 "오늘 실시간 보유 0개"(전략이 일시적으로 전량현금 상태)면 1년치 과거 편입 이벤트 재현검증 전체를 건너뛰고 무조건 0%를 반환하던 버그 발견·수정 — 모멘텀 Easy가 8월 27종목→9월 0종목으로 서서히 전량현금 전환되며 4일 연속 "0%"로 잘못 표시되던 것을 "1년치 이력에 편입이벤트가 있는지"로 판단기준 변경, 즉시 96.4%로 정상화. 스탁이지 사이트(stockeasy.intellio.kr) 직접 접속으로 3개 전략 매핑(momentum/peak/value = 모멘텀 Easy/피크 Easy/밸류 Easy)이 정확함도 확인. 추가로 momentum 미스 2건(에이피알 ret20 임계 근소미달, 셀바스AI 소형주 거래량… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-19 StockEasy 로직 일치율 재검토 — 모멘')

### 2026-09-19(2차) StockEasy 매도 일치율 개선 — 채점 정상화 + 섹터 바스켓 전파
매도 F1이 낮았던 근본원인: ①스탁이지 이탈은 섹터 묶음편출이 다수(모멘텀 이탈의 71%가 하루 4종목+ 동시편출일, 8/19 하루 20종목) ②자동튜너가 정한 일일 예측 상한(momentum 3)이 대량편출일 재현율을 21.6%로 제약 ③"이탈 당일"만 정답이라 조기경고가 오탐으로 집계 ④순위밀림 이탈은 예측불가. 조치(`stockeasy_logic_validator.py`): (C)`backtest_sell()`을 "이탈 5영업일 전~당일 예측 시 적중"(window=5) 기준으로 정상화(기존 당일 기준은 exact_* 키/트래커 "매도F1(당일)" 열로 병기), (A)`_get_our_sell_candidates()`에 섹터 전파 추가(같은 섹터 보유 2종목+ 중 30%+가 매도신호면 나머지도 후보, peak/momentum은 일일상한 제거) — 60/40 시간분할 검증에서 훈련·검증 모두 개선(모멘텀 검증 F1 72.1→80.4, Peak 46.2→57.1). (B)매수조건 이탈=매도 규칙은 Peak 검증구간 +1p·모멘텀 재현율 절반·밸류 무의미로 미채택. 최종 5일창 F1: Peak 40.0→55.8, 모멘텀 34.4→80.5, 밸류 19.0→45.2(밸류는 이탈 4건뿐이라 참고용). 부수: 모멘텀 섹터 사전계산이 미사용 죽은코드인데 호출당 수십초 소모 → `_MOM_SECTOR_ROTATION_ENABLED=False`로 차단, 전체 재계산 3,800초→14초. `_entry_signal_ok()`를 모듈 수준으로 분리(연구용). 연구 스크립트: `scripts/research_stockeasy_sell_cache.py`, `scripts/research_stockeasy_sell_eval.py`. 위 09-19 항목의 매도 F1 수치(19~40%)는 이 항목으로 대체.

### 2026-09-19 ETF Check 외부 수집 중단 및 만기 ETF 품질 게이트 수정 (Codex)
- `465780`의 정상 만기 청산을 실패로 오판하던 문제를 다중 증거 기반 `KRX_MATURITY_WINDDOWN` 예외로 수정했다. 20260915~18을 재평가해 전수 커버리지 100%, 표본 편입개수/교집합 100%, 금액상관 0.9973~0.9976을 확인했고 20260911 포함 최근 5거래일 연속 통과로 `krx_primary`를 복구했다.
- 최신 20260918 데이터를 2,693종목(가격/시총 커버리지 100%)으로 직접 게시했다. 일일 파이프라인의 ETF Check 표본/동등성 단계는 기본 비활성화하고, API의 ETF Check 네트워크 폴백과 `retry_etfcheck_k_sample.sh` 크론을 제거했다. 복원용 DB/크론 백업은 `ETF_check/backups/etf_check.pre_maturity_exception_20260919.db`, `ETF_check/backups/crontab.pre_etfcheck_stop_20260919.txt`이다.

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
`financial_anomalies`·`fin_quarterly_validation_flags` 모두 2026-05-25~31 무렵 일괄 감지된 후 재검증 없이 방치된 스테일 플래그가 대량 존재함을 확인·정리: - **`partial_coverage`(1,800건 미해결)**: 전부 "결손 연도: [2020, 2021]" 패턴 — 실제 financial_data를 재조회한 결과 **1,639건(91%)이 이미 데이터가 채워져 있음**(감지 이후 백필로 자연 해소, 재검증만 안 됨) → is_resolved=1 처리. 159건은 일부 연도만 남아 결손연도 목록 축소 갱신. 진짜 전부 미수집인 건 **014950(삼익제약, 이미 별도 확인된 종목)·101970 단 2건**뿐. - **`persistent_loss`(660건 미해결)**: 무작위… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-19(6차) 재무 이상치/검증 테이블 스테일 플래그')

### 2026-09-19 종합 정리 — 완료 vs 지속점검 명확화 (사용자 지시)
오늘 하루 처리한 항목을 완료/지속점검으로 명확히 구분: **✅ 완료(추가 조치 불필요)** - `invalid_ohlcv` 1,546→38건(97.5%, 잔여는 검증소스 없음) - `externally_confirmed_internal_corruption` 1,090건 → 0건(2020-01~2021-02 배치오류 1,048종목 251,301행 복구) - `unresolved_active_common` 중 Naver 대조 가능분 6,657행 복구 - `financial_anomalies revenue_zero` 30→1건(29건은 SPAC/임상바이오텍 확인, 정상상황) - `data_quality_issues ANCHOR_MISMATCH` 4→1건(3건 CFS/OFS 설정 정정) - `shares_issued 우선주 포함` — 이미 해소 확… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-19 종합 정리 — 완료 vs 지속점검 명확화 (사')

### 2026-09-19(7차) financial_data 음수매출 78건 전량 정정 + cf_validation_flags AMBIGUOUS 재검증 실행 중
①`financial_data.revenue < 0` 78건 전수 확인·정정. **25건**(`derived_annual_minus_quarters`, 대부분 Q4)은 연간-분기누적 역산 결과가 음수 — CLAUDE.md 규칙("소스 불일치시 Q4 강제산출 금지") 위반이라 NULL 처리. **53건**(`dart_ofs_backfill`/`dart_q2_verified`/`dart` 등, 004310 현대약품 24건·950170 JTC 9건 등)은 DART 실시간 재조회로 **완전히 다른 값**이 나옴을 확인(예: 004310 2023 Q2 저장값 -30.9억 vs 실제 DART 매출액 +488.5억) — 진짜 오염 확정, 재조회값으로 교체. 중 28건은 같은 (종목,연도,분기) 키에 이미 정상값을 가진 "형제 행"(legacy_collected/dart_recollect 등)이 별도로 존재하는 **중복행 구조**였음을 발견 — `id` 기준 정밀 타겟팅으로 정상 형제행은 건드리지 않고 오염된 행만 수정. `financial_fix_log`에 전건 기록(run_id 3종). **최종: 음수매출 78→0건**.
②`cf_validation_flags` status=AMBIGUOUS(1,424건, 2026-08-02 배치, ai_verdict 전부 NULL) — 이미 존재하던 검증 파이프라인(`collectors/cf_triple_validator.py validate_recent()`, DART+FnGuide+Seibro 3중대조+자동보정 로직 포함)을 찾아 재실행 시작. 대상이 예상(864쌍)보다 넓어져 **5,577건**(관련 627종목의 전체 연도) 처리 중 — Seibro 실시간 조회 방식이라 시간 소요(추정 1시간+), **백그라운드 실행 중, 완료 시 결과 반영 예정**.
③영업이익이 매출의 5배 이상인 1,991건 스팟체크 — 절반 이상이 "의료"(임상단계 바이오텍) 업종으로 확인, 이미 이번 세션에서 여러 번 확인된 "매출 미미+R&D손실 막대"의 정상적인 바이오텍 특성과 일치 — 노이즈가 커서 이번엔 깊이 파지 않음, 후속 필요시 참고용으로 기록만.

### 2026-09-19(8차) cf_validation_flags AMBIGUOUS 1,424→485건 — 3중검증 실행 + FIN_CROSS 917건 사업보고서 재조회
①`collectors/cf_triple_validator.validate_recent(days=9999, codes=627종목)` 실행 완료(5,577건, 오류 0, CONFIRMED 10,617/AMBIGUOUS 588 이벤트, `dart_fg_corrected` 보정 10건). 단 이 검증기는 현금흐름 3필드(영업CF/투자CF/기말현금)만 다루므로 AMBIGUOUS 테이블 감소는 66건에 그침. ②남은 AMBIGUOUS 중 `flag_type='FIN_CROSS'`(재무제표 본체 DART↔FnGuide 불일치: 매출373·순이익166·영업이익156·총자산113·총자본109, 917건)는 DART 연간 사업보고서(11011, CFS→OFS)를 실시간 재조회(`scratch/fin_cross_recheck_20260919.py`, DB 쓰기 없음)해 판별: **873건은 현재 financial_data 값이 공시 원문과 이미 일치하는 스테일 플래그**(플래그 저장값이 2026-08-02 시점 낡은 값) → 411건 CONFIRMED(DB=공시=FnGuide), 462건 STRUCTURAL(DB=공시, FnGuide는 기준차/오파싱, DART 앵커 우선) 처리, `ai_verdict`/`resolved_value`/`resolved_at` 기록. **financial_data는 한 행도 수정하지 않음**(모두 이미 정상이었음).
③**미해결 유지(근거 불충분, 데이터 미변경)**: FIN_CROSS 44건(DB가 공시와도 불일치 19건 + 공시 조회 불가 25건 — 대부분 소형/외화보고(900·950번대) 종목으로 통화·단위 문제이거나 FnGuide 1억 단위 반올림값, 개별 확인 필요) + 현금흐름 필드 AMBIGUOUS 약 415건(투자CF 166·영업CF 141·기말현금 108, 검증기가 3자 불일치로 판정한 진짜 미해결). 최종 cf_validation_flags: CONFIRMED 102,831 / CLOSE_MATCH 3,441 / STRUCTURAL 479 / AMBIGUOUS 485. **지속점검**: 이 485건은 계속 추적 대상.

### 2026-09-20 — FIN_CROSS 결과 재적용 및 현재 DB 기준 정정
2026-09-19 기록의 `AMBIGUOUS=485`는 PostgreSQL 현재 상태와 일치하지 않았다. `/tmp/fin_cross_recheck.json`의 DART 재조회값을 현재 `financial_data`와 다시 대조한 결과, 917 FIN_CROSS 중 **409건**만 현재 저장값이 DART CFS/OFS 원문과 일치했다. 이 409건은 값 변경 없이 `STRUCTURAL` 및 `DART_LIVE_RECHECK_MATCH_FNGUIDE_DIFF`로 확정했다. 나머지 **508건**은 현재 저장값도 재조회 DART 값과 일치하지 않거나 대상 행이 없어 계속 미결이다. 현재 `cf_validation_flags`의 `AMBIGUOUS`는 **1,066건**이며, 근거 파일은 `research_outputs/fin_cross_rec… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-20 — FIN_CROSS 결과 재적용 및 현재 D')

### 2026-09-24 소스/운영 저장소 통합 1~2단계 (runtime을 단일 기준으로)
- 발견: `/Volumes/Realtek_NVME/stock_dashboard`(소스, 63커밋)와 `runtime/`(운영, 별도 git 저장소)은 **히스토리가 무관한 두 저장소**(같은 GitHub 원격). 코드는 runtime이 훨씬 앞서 있음(main.py 7,366 vs 5,549줄). 앞으로 코드 수정은 **runtime 한 곳**에서만 한다. - 1단계: runtime 미커밋 623건을 5개 논리 커밋으로 보호(`3c3a893`~`b05d613`, push 안 함, `data/`·`data_cache/`·`.verification/`·`hs_trade_lab/data/` 및 런타임 상태 json은 제외). - 2단계: 소스에만 있던 tracked 파일 80개를 덮어쓰기 없이 이식(`73965d5`) — `routes/peer_… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-24 소스/운영 저장소 통합 1~2단계 (runti')

### 2026-09-24 문서 전면 최신화·최적화 (토큰 절약)
- CLAUDE.md 243KB→~85KB: 섹션 1(구조)·2(테이블 행수 407개 기준)·6(줄번호) 재생성, 섹션 3(API)·4(스케줄러)는 자동 생성 정본(`docs/API_ENDPOINTS.md`/`SCHEDULER_JOBS.md`/`DB_TABLES_PG.md`, `scripts/ops/gen_*_doc.py`)으로 대체, 해결된 이슈 54건·이전 변경이력은 `docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md`/`CLAUDE_CHANGELOG_ARCHIVE.md`로 이관(원본 백업 `backups/CLAUDE.runtime_before_optimize_20260924.md`).
- hermes.md 138→63KB, hermes_change.md 50→12KB(9/23 이전 → `docs/hermes_*archive*`). PROJECT_MASTER 등 3~5월 SQLite 시대 문서 7개 → `docs/legacy/`, 루트 중복 md 13개 → `backups/root_md_duplicates_20260924/`.
- 정정된 낡은 서술: runtime "레거시/심볼릭 링크" 문구, 키움 ka10059 "buy-only 버그"(2026-07-21 수정 완료), dart_recollect NI 결측 7,613→6,024건(감소 중), 존재하지 않는 `financial_data_backup_20260412` 행 삭제.
