# 주식 대시보드 — Claude 필수 참조 문서

---

## ⚠️ CLAUDE 필수 행동 규칙 (모든 세션에서 자동 적용)

> **이 섹션은 Claude가 반드시 따라야 할 행동 규칙입니다. 예외 없이 적용됩니다.**

### 프로젝트 경로·DB (필수)

- **코드 수정·git 작업은 `/Volumes/Realtek_NVME/stock_dashboard/runtime` 한 곳에서만.** 상위 폴더는 공용 데이터 루트(`.git`은 `.git.retired`로 은퇴, 코드 복사본 만들지 않음).
- **운영 DB = PostgreSQL**(`.env` `POSTGRES_DATABASE_URL`, 데이터 `postgresql16/`). 연결은 `db_compat.connect_primary_db()`. `stock.db`(SQLite)는 레거시 스냅샷 — 운영 판단 근거 금지, PG 실패 시 SQLite 폴백 금지(섹션 2).
- `/Applications/stock_dashboard`, `/System/Volumes/Data/Volumes/...` 사용·하드코딩 금지. 경로는 `Path(__file__)` 기준 또는 `/Volumes/Realtek_NVME/stock_dashboard/...`.
- 키움 REST는 등록 IP에서만 인증됨 — 8050 알림이 오면 포털 허용 IP에 등록(섹션 4).

### 숫자 데이터 규칙 (필수 — 2026-10-03, 모든 AI 공통)
> **⛔ 최우선 원칙 0: DART 파싱값을 100% 신뢰하지 않는다 — DART 값은 원본 후보일 뿐, FnGuide(필수)·네이버(보조)와 연결·별도를 나눠 값 대조로 일치해야 확정. 불일치·반복 차이는 먼저 우리 파싱 오류를 의심하고 종목 특징(stock_collection_config fs_quirk:*)으로 기록. 목표 99.99%.**

- **재무제표·주가·현금흐름·수주잔고·재고자산·감가상각 등 숫자 데이터를 판정·수집·수정하기 전에 반드시 [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 를 먼저 읽고 그 기준대로만 작업한다.** 정답 소스·정의·판정 규칙·필수 절차·실패 사례·현재 상태·한계가 모두 그 파일에 있다(이 CLAUDE.md의 관련 규칙·이력은 2026-10-03 그 파일 부록 A로 이관).
- 기준(FnGuide vs DART, 지배 vs 전체 등)은 사용자 승인 없이 바꾸지 않는다. 숫자 데이터 작업 기록은 그 파일 §7에 남긴다.
- **미완료 항목 통합 목록(사용자 결정 필요·남은 작업·해결된 낡은 항목): [docs/OPEN_ITEMS.md](docs/OPEN_ITEMS.md)** — 작업을 끝내면 원문과 이 목록을 함께 고친다.
- **매매 신호(계좌현황 추세추종·매도시그널, 차트시그널, 매수후보 진입)는 [docs/SIGNAL_RULES.md](docs/SIGNAL_RULES.md)가 정본** — 코드는 `trend_rules.py` 한 곳. 다른 AI가 이어받을 현황은 docs/FINANCIAL_STATEMENTS.md §9-2-7.

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

> 📦 **재무/현금흐름 무결성 선행 규칙 · FnGuide급 신뢰도 운영 규칙** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

## 0. 사이트 구조(2026-09-27 전면 개편) — 먼저 [docs/SYSTEM_MAP.md](docs/SYSTEM_MAP.md)(자동 생성 압축 지도)를 읽고 필요한 파일만 열 것

- 진입: `stock.leanguy.cloud/` **Stock Hub**(4개 선택 + 관리자) → `/info/<탭>` Stock Info · `/lab/<탭>` Stock Lab · `newsinfo.cloud` Key Indicator(별도 사이트, 코드 `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/frontend`, `hubbar.js`로 허브 바 주입) · `/llm/` Stock LLM(Brian_RAG :8888, **폴더명은 `Brian_RAG`**, 서버 프록시 `routes/llm_proxy.py`, 관리자 로그인 필요) · `/admin/<탭>` 관리자.
- 프런트: `frontend/src/main.jsx → hub/Root.jsx`(History 라우터 `hub/router.js`). **탭 구성의 정본 = `hub/modules.js`**(모듈별 섹션·탭 키). 화면 렌더는 여전히 `App.jsx`의 `activeTab` 스위치 — `App({module, tab})`이 URL을 받고 `setActiveTab`은 URL 이동(모듈 간 이동 포함). 새 탭: modules.js 섹션에 키 추가 → App.jsx `NAV_ITEMS`/`NAV_DEFS`에 아이콘·라벨 → 렌더 스위치. 아래 섹션 6의 App.jsx 줄번호는 이 개편으로 약 +110줄 밀렸다(근사치, `grep -n 'const 이름 ='`).
- **디자인**: 클린 라이트(newsinfo와 동일 팔레트: 파랑 `#1a73e8`, 배경 `#f4f6fb`, 선 `#e2e6ef`). 토큰은 `index.css :root`(레거시 변수명 `--bg-dark`·`--accent-mint`·`--text-secondary`는 이름만 유지하고 값이 라이트), 허브·앱바·관리자 CSS는 `hub/hub.css`. 기존 화면의 인라인 다크 색은 `scripts/ops/light_theme_codemod.py`(pass1~4, 파일 첫 줄 마커로 재실행 방지)로 일괄 변환 — **새 코드는 인라인 hex 대신 `var(--surface/--line/--text/--primary…)`를 쓸 것**. 잔여 개별 화면 디테일은 페이지별 후속 작업.
- **관리자 인증**: `POST /api/admin-auth/login`(routes/admin_auth.py) → 서명 쿠키 `sd_admin`(8h, HttpOnly). `security_gate._owner_ok`가 이 쿠키를 API 토큰·Cloudflare Access와 같은 owner 자격으로 인정. 비밀번호는 `.env` `ADMIN_PASSWORD_HASH` — **최초 설정은 이 PC에서 `http://localhost:5173/admin` 접속 시 뜨는 설정 화면**(`POST /api/admin-auth/setup`, 미설정+터널 아닌 요청만 허용, 인터넷 경유는 403) 또는 `scripts/ops/set_admin_password.py`. 미설정 시 로그인 503 fail-closed. `/api/sysmap/*`는 관리자 전용(OWNER_GET_PREFIXES).
- **시스템 현황**(`/admin/system_map`): `system_map.py`가 코드·API·스케줄러·데이터 계보(테이블→쓰는 파일→읽는 API→화면)를 소스에서 자동 집계, 소스 fingerprint가 바뀌면 정적 부분 재계산·화면 15초 자동 갱신. 데이터 신선도·수집 실행은 기존 `collection_health.DATASET_CONTRACTS`를 재사용 — **새 수집 데이터는 그 계약(또는 `system_map.EXTRA_DATASETS`)에 등록하면 지도에 나타난다.**
- 신규 백엔드: `routes/admin_auth.py`(/api/admin-auth) · `routes/hub.py`(/api/hub/status, 허브 모듈 가동 표시) · `routes/llm_proxy.py`(/llm) · `routes/system_map.py`(/api/sysmap). vite preview 프록시에 `/llm` 추가(`frontend/vite.config.js`, launchd 재시작 필요).
- 토큰 최적화: 이 파일 변경 이력은 최근 12개만 유지(`scripts/ops/trim_claude_changelog.py`, 초과분은 `docs/CLAUDE_CHANGELOG_ARCHIVE.md` 맨 아래로 이동). 코드 탐색은 SYSTEM_MAP → grep → 필요한 줄만.

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
- **Python 환경(2026-09-25 전환)**: 운영 `runtime/venv` = 심볼릭 링크 → `.venvs/py312`(**Python 3.12.14**, numpy 2.2.6, pandas 2.3.3, pykrx 1.2.9). 롤백용 `.venvs/py311`(3.11.15, numpy 1.26.4, pykrx 1.2.4)과 패키지 목록 `.venvs/freeze_py311_20260924.txt`·`requirements-py312-candidate.txt`·`requirements-core.lock` 보존. 롤백: `rm venv && ln -s .venvs/py311 venv && scripts/safe_restart_backend.sh`. 절차·금지사항(pandas 3·Python 3.14·제자리 업그레이드 금지): `docs/HANDOFF_GITHUB_ADOPTION_20260924.md` §9. OpenDartReader는 PyPI 0.2.3이 ≥3.13을 요구해 3.11 venv의 순수 파이썬 패키지를 복사해 사용. 연구용 별도 venv: `/Volumes/Realtek_NVME/stock_dashboard/research_venv`(Python 3.11, numpy 2.4.6, vectorbt·alphalens-reloaded·quantstats·PyPortfolioOpt, DB 직접 접속 없이 `data_cache/research/*.parquet`만 읽음; alphalens `utils.py`에 월말 희소 날짜용 로컬 패치가 있어 재설치 시 재적용).

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
| `portfolio` | 10 | stock_code, quantity, avg_price, bought_at | 실제 포트폴리오(PG 정본 — SQLite 자동복구 제외, 섹션 9) |
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
| `backtest_equity_curve` | 33.6만 | run_id, date, equity, source(engine/realized_pnl/realized_pnl_assumed_100m/mtm_reconstructed) | **PK `(run_id,source,date)`, 조회는 뷰 `backtest_equity_curve_best_v`(engine>mtm>realized>assumed, `quality` 컬럼), `backtest_common._save_result`가 자동 저장(`backtest_equity.py`)** ★2026-09-24 신설 — 백테스트 자산곡선 저장 통일(`equity_json`은 전부 비어 있음). `research/build_backtest_equity_curves_20260924.py`·`reconstruct_mtm_equity_20260924.py` |
| `price_coverage_gap_reviewed` | 524 | stock_code, event_date, previous_date, reason, evidence | ★2026-09-24 신설 — 원본이 없어 못 채우는 coverage_gap의 사유·근거(감사가 `coverage_gap_reviewed`로 분류, 재검토 방지) |
| `price_history_fix_backup` | 449.7만 | run_id, stock_code, date, old_*/new_* OHLCV, reason | 모든 가격 교체/삽입의 백업(삽입은 old NULL) — 롤백용. `data_fix_log`와 run_id로 연결 |
| `strategy_feature_snapshot_rebuild[_adj[_legit]|_v3]_20260924` | 18.1만 | strategy_feature_snapshot과 동일 컬럼 | 생성기 재실행 결과(원본 미변경). `_adj`=기업행위 4분류 전부 수익률 0, **`_adj_legit`=DART확정+발행주식수 근거 있는 것만**, **`_v3`=adj_legit+시점 정합 TTM PER/PBR(`--ttm-valuation`, 권장; 기존 `valuation_history.per`는 분기별 정의가 달라 삼성 PER 60/144/52처럼 무효)**. 검증: 원본(raw) 라벨은 역분할·감자의 가짜 상승을 양성으로 셈 — label_10x_24m 양성 1,474(1.23%)→adj_legit 980(0.82%)/adj 813, label_3x_12m 10,176(6.83%)→8,885(5.96%). 변경 행의 96%가 창 안에 마스크 이벤트 보유. **⚠ 기저율 문구 정정(2026-09-26)**: 위 0.82%/1.23%는 옛 18.1만 행 스냅샷(v2/v3) 기준이며, 현재 운영 `strategy_feature_snapshot`(v4, 190,609행)의 실측 기저율은 label_10x_24m 1,171건(0.61%)·label_3x_12m 9,432건(4.95%) — 인용 시 v4 값을 쓸 것 |
| `strategy_feature_snapshot` | 19.1만 | **정본(2026-09-25)=v4 생성기 결과: `scripts/build_strategy_research_dataset.py --adjust-jumps --legit-only --ttm-valuation`(폐지 보통주 포함·시점 정합 TTM PER·기업행위만 수익률 0). 월간 재생성=`scripts/refresh_feature_snapshot_monthly.py`(스테이징→점검→교체). 백업 `strategy_feature_snapshot_legacy_20260925`.**  snapshot_date, stock_code, close_price, market_cap_억, per, pbr, ret_20d/60d/120d, dist_high_252, vol_ratio_20d, supply_20d_억, label_2x/3x_6m/12m, **forward_max_ret_24m/36m, label_3x/5x/10x_24m, label_5x/10x_36m**, heuristic_score, model_score_6m/12m | 전략 연구용 월말 피처 스냅샷 + forward 라벨 + 휴리스틱/ML 점수. `scripts/build_strategy_research_dataset.py`가 생성/전량 재구축. ★신규(2026-07-05) / **2026-08-08 24·36개월 라벨 7컬럼 추가** — 실제 10배 종목은 중위 609일(1.7년) 소요라 기존 12개월 창으로는 86.9%가 관측 불가였음. 라벨 유효구간: 24m는 스냅샷 ≤2024-08-07(126,879행), 36m는 ≤2023-08-08(97,188행). 기준율 label_10x_24m 1.50% / label_10x_36m 2.37%. **모든 라벨은 비율 스케일(1.0=+100%) — 3배=2.0, 5배=4.0, 10배=9.0** |
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
| `valuation_history` | 6.6만 | stock_code, year, quarter, period_end, close_price, eps, bps, per, pbr, market_cap_억, **per_ttm, ttm_net_income(2026-09-25, 회계기간 TTM — `per`는 분기별 정의 상이라 비교 불가)** | 분기별 역사적 PBR/PER 밸류에이션 이력. financial_data+price_history 기반 계산. ★신규(2026-06-11) |
| `price_close_verify_log` | - | trade_date, sampled, compared, close_mismatch, mismatch_pct, status | ★2026-09-25 `scripts/verify_daily_close_vs_official.py`(스케줄러 `종가공식검증` 19:30) — 종가 표본 80종목을 pykrx 공식값과 대조, 불일치>5%면 텔레그램+실패 |
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

> 📦 **중요 단위 규칙(시가총액 억원·수급 백만원)** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

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
> **`stock_universe`는 종목당 1행**(2026-09-24~): 과거 base_date 스냅샷은 `stock_universe_history`로 이관(`stock_universe_history.py`, `update_from_krx` 종료 시 자동 실행, 백업 `stock_universe_backup_20260924`). 조회 시 base_date 조건 불필요.
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


**Python/라이브러리**: 위 섹션 1의 Python 3.12 환경 참조. pykrx 1.2.9는 import 시 `KRX_ID/KRX_PW` 미설정 안내를 출력하지만 종목 OHLCV는 동작(1.2.4와 동일값); ETF(`get_etf_ohlcv_by_date`)·전종목 일괄(`get_market_ohlcv_by_ticker`)은 두 버전 모두 불가. pykrx 호출을 `ThreadPoolExecutor` 안에서 돌리면 멈추므로 순차 호출할 것(0.03초/건).
---

- 가상매매 가드(`virtual_trade_guards.py`, 기본 적용, `.env`로 개별 해제): `VT_REGIME_FILTER`(KOSPI<MA60이면 모멘텀/돌파 신규진입 차단, v_recovery·value 제외) · `VT_EXPOSURE_LIMIT`(종목당 `VT_MAX_POS_PER_STOCK`=2, 섹터 `VT_SECTOR_CAP_PCT`=35%) · `VT_BREAKEVEN_STOP`(+`VT_BREAKEVEN_ARM_PCT`=10% 도달 후 본전 이탈 청산) · `VT_ENTRY_CONFIRM`=shadow(당일 등락<+3% 진입은 차단 없이 `virtual_guard_log` 기록, `enforce`로 전환 가능)

## 8. 핵심 코딩 패턴

> 📦 **현재가 조회 패턴** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

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

> 📦 **수급 금액 단위 변환** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

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

> 📦 **종목별 수집 특성·FnGuide 동기화·데이터 소스 우선순위·DART 불일치 원칙·PER/PBR·EPS/BPS 계산** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

## 9. 알려진 이슈 & 제한사항

| 항목 | 상태 | 내용 |
|------|------|------|
| K-mydata | ❌ 인증실패 | KRX_API_KEY가 K-mydata용 아님 |
| pykrx | ❌ Empty | KRX 서버 차단으로 빈 DataFrame |
| TWSE(대만) 외국인 순매수 수집 | ❌ 접속 차단(2026-09-08 확인) | `/rwd/`·`/exchangeReport/` 등 데이터 경로가 이 Mac 네트워크에서 WAF 307("FOR SECURITY REASONS")로 전부 차단됨(루트 도메인은 200으로 정상 — 데이터 경로만 선별 차단). Referer/User-Agent 조정, Playwright 풀브라우저 모두 동일하게 막힘 — IP/지역 기반 차단으로 추정. 대체 소스 없이는 `TW_FOREIGN_FLOW_USD` 수집 불가(`collectors/asia_foreign_flow_collector.py`의 `collect_tw_foreign_flow`는 코드는 있으나 상시 0건). 우회 시도(프록시/스푸핑)는 정책상 하지 않음. |
| 중국 북향자금(HKEX Stock Connect) 순매수 수집 | ⚠️ 미구현 | 사이트 자체는 접근 가능하나(hkex.com.hk 200) Historical-Daily 통계표가 JS로 동적 렌더링됨 — Playwright로 네트워크 캡처해 찾은 `/eng/csm/DailyStat/data_tab_daily_YYYYMMDDe.js`는 Turnover(거래대금)만 있고 실제 순매수(Net Buy/Sell) 필드가 없어 사용 불가. 실제 net flow가 나오는 엔드포인트는 날짜검색 인터랙션 뒤에 있는 것으로 추정되나 미확인 — 후속 조사 필요, `CN_NORTHBOUND_FLOW_USD` 현재 0건. |
| 공공데이터포털 투자자API | ❌ 404 | getStocInvtTrdnInfo 서비스 폐지 |
| foreign_holding_daily | ⚠️ 2026-09-23 이후 정지(2026-10-06 실측) | 이전 기록의 '06-08 정지'는 낡음. 외국인 지분율 최신값은 `kiwoom_foreign_flow`(9/23까지 정상) 사용 |
| ~~**crontab 잡 전체**~~ | ✅ 해결(2026-09-24 — 아래 해결 행) | macOS TCC가 cron의 외장 볼륨 쓰기를 거부(`/var/mail/brainlee`: `Operation not permitted`) → quant_indicators·HS daily_refresh·telegram·ETF 재시도·cron_3am 등 미실행. 사용자 조치: 전체 디스크 접근 권한에 `/usr/sbin/cron` 추가 또는 LaunchAgent 이전. 상세 [docs/SYSTEM_REVIEW_20260924.md](docs/SYSTEM_REVIEW_20260924.md) |

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
| **crontab 전 잡 실행 불가(macOS TCC)** | ✅ 해결(2026-09-24, 사용자가 `/usr/sbin/cron`에 전체 디스크 접근 권한 부여) | 2026-08-28~09-24 모든 cron 잡이 `Operation not permitted`(외장볼륨 쓰기 거부, `/var/mail/brainlee`)로 실행 안 됨. 권한 부여 후 테스트 잡으로 쓰기 성공 확인, 퀀트지표 weekly/monthly는 수동 캐치업. **재발 시 점검**: `/var/mail/brainlee` 의 `Operation not permitted`, 로그 파일 mtime. macOS 업데이트 후 권한이 초기화될 수 있음 |
| **가상매매 공통 하드스탑 미실행(2026-07-23~09-24)** | ✅ 수정(2026-09-24) | `_auto_hardstop_all_strategies`(전 전략 -10% 손절)가 V18 추천 빌드 안에서만 호출됐는데 V14/V18 장중 루프가 7/23 삭제되며 스케줄러에서 한 번도 안 돌았음(value -24~-45% 방치·HDC -10.5% 미청산 확인). 전용 루프 `가상매매공통손절`(장중 5분) 신설 + 본전스톱 추가. **재발방지**: 안전장치 함수는 다른 기능의 부산물 호출에 얹지 말고 독립 스케줄 잡으로 둘 것 |
| **삭제한 포트폴리오 행이 SQLite에서 부활** | ✅ 수정(2026-10-02) | 06:10 `PostgreSQL커트오버검증`이 PG 행수<SQLite 행수를 "뒤처짐"으로 보고 `sync_sqlite_bridge_delta.py`로 SQLite 스냅샷을 upsert → 09-29 사용자가 지운 49행이 원래 id로 되살아나 보유수량 이중 합산(172670 95,182주). `verify_postgres_cutover.POSTGRES_USER_MANAGED_TABLES`(portfolio/portfolio_tx/watchlist/buy_candidates)는 판정 제외 + 브리지도 거부. **사용자 관리 테이블을 새로 만들면 이 집합에 추가할 것.** |
| **백테스트 가격 = 원주가, 기업행위 보정은 전략별(공용 엔진은 없음)** | ⚠️ 알려진 한계(2026-10-06) | 신호는 끊긴 원주가로 계산되고, 보정(진입가 보정·보유 재기준)은 14개 전략에만 있다. 공용 일반 엔진 9개 등은 무상증자·분할 가짜 손실 가능. 수정 계획: docs/REVIEW_PLAN_20261006.md §1·§4 W1~W3, docs/Stock_Strategy.md S28 |
---

> 📦 숫자 데이터 관련 이슈 12행 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

### 해결된 이슈 — 재발방지 규칙 요약 (전문: [docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md](docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md))
- **모바일에서 계좌현황(포트폴리오) 진입 불가**: `window.prompt`/`alert`/`confirm` 등 브라우저 네이티브 다이얼로그는 모바일 인앱 브라우저 호환성이 보장되지 않으므로 신규 UI에 사용 금지 — 항상 앱 내부 모달 컴포넌트 사용(기존 `window.confirm` 사용처 다수 잔존, 동일 부류 위험 후속 검토 필요). 또한 NAV_ITEMS가 기능 추가로 계속 길어지고 있으므로, 자주 쓰는 개인화 메뉴(계좌/매수후보 등)는 새 항목 
- **Screener/StrategyCenterView `fmtKrw`/`fmtPctUs` 스코프 버그**: 컴포넌트를 별도 파일로 분리할 때는 반드시 참조하는 모든 헬퍼가 import돼 있는지 확인(섹션 6 상단 경고 참조).
- **백테스트 market_cap 단위 오류 반복**: 500억+=500, 1000억+=1000, 5조+=50000 (억원 그대로)
- **ETF 수집실패일 오표시**: ETF 관련 쿼리 시 반드시 `WHERE etf_amount > 0` 또는 valid_rows 필터 적용
- **섹터 트렌드 오분류**: 섹터 분류는 반드시 `sector_large` 기준으로. `sector_mid`는 신뢰도 낮음
- **수출공동 표시 이종업종 혼입**: HS코드 기반 공동 매핑 시 반드시 sector_large 교집합 필터 필수

> 📦 **9-1 데이터 검증 규칙(EPS/BPS 신뢰 계층·shares_issued·외부 비교)** → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A

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

> 📦 숫자 데이터(재무·가격·현금흐름·수주잔고·원가·감가상각·검증 플래그) 변경 이력 34건 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 A. 새 숫자 데이터 작업 기록은 그 파일 §7에.


> 🪙 **토큰 최적화**: 이 섹션은 매 세션 자동 로드된다. **항목은 1~3문장만**, 근거/SQL/장문 분석은 `docs/`에 날짜 파일로 두고 링크만. 최근 1~2주(약 15개)만 유지하고 초과분은 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래로 이동.

### 2026-09-24 소스/운영 저장소 통합 1~2단계 (runtime을 단일 기준으로)
- 발견: `/Volumes/Realtek_NVME/stock_dashboard`(소스, 63커밋)와 `runtime/`(운영, 별도 git 저장소)은 **히스토리가 무관한 두 저장소**(같은 GitHub 원격). 코드는 runtime이 훨씬 앞서 있음(main.py 7,366 vs 5,549줄). 앞으로 코드 수정은 **runtime 한 곳**에서만 한다. - 1단계: runtime 미커밋 623건을 5개 논리 커밋으로 보호(`3c3a893`~`b05d613`, push 안 함, `data/`·`data_cache/`·`.verification/`·`hs_trade_lab/data/` 및 런타임 상태 json은 제외). - 2단계: 소스에만 있던 tracked 파일 80개를 덮어쓰기 없이 이식(`73965d5`) — `routes/peer_… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-24 소스/운영 저장소 통합 1~2단계 (runti')

### 2026-09-24 문서 전면 최신화·최적화 (토큰 절약)
- CLAUDE.md 243KB→~85KB: 섹션 1(구조)·2(테이블 행수 407개 기준)·6(줄번호) 재생성, 섹션 3(API)·4(스케줄러)는 자동 생성 정본(`docs/API_ENDPOINTS.md`/`SCHEDULER_JOBS.md`/`DB_TABLES_PG.md`, `scripts/ops/gen_*_doc.py`)으로 대체, 해결된 이슈 54건·이전 변경이력은 `docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md`/`CLAUDE_CHANGELOG_ARCHIVE.md`로 이관(원본 백업 `backups/CLAUDE.runtime_before_optimize_20260924.md`). - hermes.md 138→63KB, hermes_change.md 50→12KB(9/23 이전 → `docs/hermes_*archive*`). PROJECT_MASTER 등 3~5월 SQ… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md)


### 2026-09-24 2차 점검 (Claude) — 상세 docs/SYSTEM_REVIEW_20260924.md §D
- **PG 이관 시 컬럼 DEFAULT 366개(172테이블) 유실** → 생략 INSERT가 NULL 저장(실적신호 8월 이후 1,629건이 화면·알림에서 전부 누락 등). 360개 `SET DEFAULT` 복구(price_history 수급 숫자 컬럼은 NULL=결측 유지로 제외), 플래그 NULL 백필(`restore_null_defaults_20260924_175758`). 신규 테이블/컬럼은 PG DDL에 DEFAULT를 직접 명시할 것.
- 실적 트리거 TTM(CFS+OFS 이중합산·흑자전환 2년전 비교) 수정 후 기존 신호 1,029건 비활성. tenbagger/screener/signal_engine/routes/tenbagger의 CFS/OFS·연간(quarter 0/4) 혼재 QoQ/YoY 수정, D&A는 `depreciation_q`. `treasury_buyback` 7/10 정지 → `scripts/ops/sync_treasury_buyback_from_dart.py`(공시 갱신 시 자동) + 정규 분류 `event_class`. 실적신호 API 10s→0.08s.


> 📦 2026-09-27: 이 아래 최근 12개 항목만 유지 — 이전 항목(45개 + 관련 소제목/색인)은 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래 「CLAUDE.md에서 이동」 절. (`scripts/ops/trim_claude_changelog.py`)

2026-09-29(Codex Stock LLM 상세분석 품질 개선): `routes/detailed_analysis.py`와 `/Volumes/Realtek_NVME/Brian_RAG`의 `services/insight.py`/`services/market_data.py`/`services/gemini_extractor.py`/`rag/hybrid_engine.py`를 함께 수정. 기업 기본정보를 stock_universe에서 보강하고, 분기 재무/현금흐름은 `(year, quarter)`별 CFS 우선 대표행으로 dedupe해 2026Q2 같은 최신 분기가 2025년 연간보다 먼저 나오게 함. “어떤 기업이야”류 질의가 `기업` 단어 때문에 SQL_ONLY로 오분류되지 않게 HYBRID 라우팅하고, 회사가 명시된 단독 질문에서는 taxonomy 옆길 결과를 제외. 프롬프트는 “어떤 기업인가→돈 버는 구조→최신 실적→투자 판단” 형식으로 강제. 회사 질의 검색 후보·LLM tokens/context를 줄여 응답 체감 속도 개선.

2026-09-29(Codex Stock LLM DB 신호 확장): Brian_RAG `services/market_data.py`/`services/insight.py`에 텔레그램 관심도·원문 단서(`telegram_stock_mentions`/`tg_daily_mentions`/`telegram_messages`), DART 최근 공시, 공급계약, R&D·특허, 수주잔고, 컨센서스/목표가, KIS 추정실적, 원가·재고·감가상각, 임직원, 내부자/대량보유 이벤트를 기업별 정량 신호로 추가. 질문이 매번 달라져도 기업 기본 설명뿐 아니라 긍정 요인과 리스크 요인을 함께 분리해 답하도록 프롬프트 규칙 보강. 텔레그램은 확정 사실이 아닌 시장 관심/소문성 단서로 낮은 신뢰도를 명시하게 함.

2026-09-29(Codex Stock LLM 종합 보고서화): Brian_RAG 기업 질문 답변을 짧은 Q&A가 아니라 최소 1,800자~3,000자 종합 보고서 형식으로 변경. 목차는 요약 투자 의견/기업 정체성/핵심 데이터 테이블/실적·사업부문/여러 소스 종합/긍정 요인/리스크/확인 지표/최종 판단으로 고정하고, Open LLM 출력 여유를 company mode `max_tokens=2400`, `num_ctx=14000`으로 확대. 회사 질문 검색은 사용자 원문을 보존한 확장 쿼리로 변경하고 후보 64→재랭킹 32→최종 20개 근거까지 사용해 애널리스트 보고서·텔레그램·공시·기초 DB를 더 넓게 통합하도록 조정.

2026-09-29(Codex Stock LLM 대상기업 오인 방지): 삼성전자 질의에서 협력사/장비사 리포트를 대상 기업 자체로 오인하는 문제 확인. Brian_RAG `rag/hybrid_engine.py`에서 회사 단독 질문은 `company_name = 대상기업` 리포트를 우선 검색하고, 자체 리포트가 부족할 때만 `mentioned_companies` 간접 언급 리포트를 보조로 사용하도록 수정. `services/insight.py` 프롬프트에 대상 기업 고정 규칙 추가: 다른 회사 리포트는 고객·공급망·섹터 맥락으로만 쓰고 대상 기업 사업모델로 오인하지 말 것.

2026-09-29(Codex Stock LLM 핵심 숫자 검증 블록): 사용자 지적(최신 분기가 Q1로 나오고 연간/분기 이익이 뒤섞이는 문제) 반영. Brian_RAG `services/insight.py`에 서버 생성 `검증된 핵심 데이터` 블록 추가: 최신 분기/최근 연간/사업부문/목표가/컨센서스/추정실적/텔레그램 관심도를 DB에서 직접 삽입하고 최종 답변 맨 앞에 붙임. LLM은 핵심 데이터 테이블 숫자를 이 블록에서만 복사하도록 강제. 최신 분기 영업이익이 최근 연간보다 큰 경우 누적값/정의 차이 가능성을 표시하고 단일분기 실적으로 해석 금지. KIS 추정실적이 최근 연간 이익 대비 4배 초과하면 “단위/정의 검증 필요” 표시. 하드웨어 확인: Apple M4 10코어, 16GB RAM, 설치 모델 `qwen2.5:7b-instruct` 1개 — 14B 상시 운영은 메모리 여유상 신중.

2026-09-26(Claude) 게이트를 다중 사용자 사이트에 맞게 재설계: public(일반 조회·검색 분석) / viewer(계좌현황 — 서버가 검증하는 비밀번호 쿠키 `pf_view`, `POST /api/portfolio-access/login`, 비밀번호는 `.env` `PORTFOLIO_VIEW_PASSWORD`로 이동) / owner(쓰기·다운로드·내보내기·설정 — API 토큰 또는 관리자 이메일 Access JWT). IP당 분당 300회 조회 제한. 일반 사용자 쓰기 허용 목록 `API_PUBLIC_WRITE_PATTERNS`. Cloudflare Access 앱은 관리자 이메일만 허용해 친구 접근을 막으므로 제거/조정 필요(사용자 결정). 상세 HANDOFF §16-5.

2026-09-26(Claude) `v_gc` 신규 진입 shadow 추가(`VT_SHADOW_STRATEGIES=momentum,peak,v_gc`, 사용자 승인). CEO 플랫폼(api.newsinfo.cloud)의 관리자 확인이 쿼리 `role=admin`(클라이언트 입력)뿐임을 발견 — Access 재설정 필요, 근본 수정은 CEO 백엔드 세션 과제(HANDOFF §16-6).

- **2026-09-26 (Codex 선택 전략 재감사)**: `holding_windows()`가 BUY 이벤트+완결 행 혼합 원장을 이중 보유로 계산하던 결함을 수정(기간말 phantom 창 제거). KRX KIND 증거로 126340·107640의 KONEX 이전상장 구간을 `security_master_history`에 복구하고 재빌드 지속 예외를 추가(백업 `security_master_history_backup_codex_20260926_konex`); SPAC 전신인 127980·162300·380540 과거구간은 차단 유지. 선택 전략 감사 **23/4→26/1**, 유일 실패 `low_base_breakout`(SPAC identity survivorship 5). v4는 최신 suite `3a1df776883808d8` 기준 PIT verified이나 성과 avg6 +10.24%·4/6 양수, governance `retired`, 독립 forward 원장 없음 — 화면의 낡은 +19.46% 설명 정정. 상세 `hermes.md` 13:35 절.

- **2026-09-26 (Codex 한국 주식 다중 분류 개편)**: 평면 `sector` 분류를 대체할 출처·스냅샷·근거 보존형 `sector_taxonomy_nodes`/`stock_sector_membership_v2`/`sector_taxonomy_source_runs`를 신설했다. StockEasy 최신 공개 API(2,553종목), Kiwoom 공식 업종·테마 구성 API, 기존 기준선과 DART 제품 매출 기반 내부 밸류체인/공정/소재/경쟁군을 분리 적재해 일반주식 2,793종목 100% 커버. 제이엠티·디케이티·한국컴퓨터는 `전자부품→EMS·모듈 조립→PBA·FPBA·모듈 조립→OLED·스마트기기 PBA/FPBA 제조사` 검증 경쟁군으로 연결했다. `/api/sector-taxonomy/*`, 프론트 `종목 다중분류`, 매일 20:10 `종목다중분류` 잡 추가. 깨진 StockEasy HTML 파서는 현재 JSON API+2,000종목 fail-closed로 교체. 전체 551 passed/54 subtests, 프론트 build, 운영 API 200, PID 9818→21069. 기준·소스·운영 규칙은 `docs/sector_taxonomy_20260926.md`, 상세 결과는 `hermes.md` 14:10 절.
  - 34차 완료(15:20): DART 분기 원문 55,414키 수집(`dart_q_full*.jsonl`, 3샤드 병렬, 한도 미도달) → `apply_dart_parent_basis_quarterly_20260926.py --apply`(2016~2025Q1: 순이익·자본 지배주주 기준 전환 34,243필드, NULL 채움 10,993; 금융업종은 매출·영업이익 채움 제외, run_id `dart_parent_basis_quarterly_20260926_145929`) → `rederive_q4_fnguide_basis_20260926.py --apply`(Q4=연간−Q1−Q2−Q3, 재무상태표는 연간값; 19,714 종목-연도, 흐름 22,452·BS 8,524필드, run_id `q4_fnguide_basis_20260926_150000`). 단 Q4 매출이 음수가 된 257건(연간<Q1~Q3 합: 입력 불일치)은 이전 값으로 되돌림(`..._revert`) — 연간/분기 입력 출처 검토 필요 목록. → 4분기 검증(QUARTERLY_4WAY) 재실행: CONFIRMED 54,697·CLOSE_MATCH 221,011·STRUCTURAL 75,775·SELF_CONSISTENT 80,903·OPEN 4,043·AMBIGUOUS 71. → 분기 현금흐름 `apply_cf_quarterly_dart_20260926.py --apply`(YTD=DART, *_q=YTD 차분, Q4=연간; 변경 29,767·채움 10,167, run_id `cf_quarterly_dart_20260926_151905`). 이상 행: `cash_flow_data`에 미래 기간 행 2개(001080 2026Q3, 2026 Q4 연간; 8/9 dart_api_unified) — 삭제 여부 사용자 확인 대기. 기준 잔여 결측(상장 보통주, 최선 행 기준): 순이익 분기 2016~2024 연 25~130건대, Q4 2022~2024 매출·영업이익·순이익 120~380건, 2024Q1~Q3·2025Q1 자산·자본 77~257건.

2026-09-27(Claude 사이트 전면 개편) Stock Hub 허브(`/`)+`/info`·`/lab`·`/admin` 라우팅, 관리자 비밀번호 로그인(`sd_admin`), Stock LLM `/llm` 프록시(관리자 전용), 전 화면 라이트 테마(코드모드 pass1~4), 관리자 시스템 현황(`system_map.py`, 자동 갱신)·`docs/SYSTEM_MAP.md`, CLAUDE.md 147→~97KB. 배포: 백엔드 재시작 + `frontend` 빌드 교체. 상세 §0.
2026-09-27(Claude) newsinfo 서버(:5500 정적/:8011 API)를 launchd(`com.ceo-briefing.frontend`/`backend`, KeepAlive)로 등록 — 재부팅 후 502 재발 방지. 관리자 비밀번호 브라우저 초기 설정 추가.
2026-09-27(Claude) 구분선 가시성 강화(사용자 요청): `--line` #cdd4e1·`--line-strong` #aab4c8, 표 행 1px/머리글 2px !important(`index.css` 맨 아래), 앱바·사이드바·헤더 2px, 인라인 border 알파 하한 0.2(코드모드 pass5).
2026-09-27(Claude) UI 개선: 사이드바 섹션 접이식(+/−, 현재 섹션 자동 펼침, localStorage `sd_nav_open_<모듈>`), 표 색상(머리글 #dbe6f7·첫 열 #eaf0fa·짝수행 #f1f4fa·호버 #e3ecfb, `index.css` 맨 아래 `#main-scroll table` 규칙), 글자 진하게(`--text` #0b1220·`--text-muted` #334155), 남은 어두운 배경 정리(코드모드 pass6·7).
2026-09-27(Claude 2차 개편) ① Key Indicator(newsinfo.cloud) 첫 화면을 **공개**(로그인 없음) 주요 경제지표·뉴스정보로 교체(`frontend/index.html`+`keyindicator.js/css`, 백엔드 `role_gate.PUBLIC_READ` GET 화이트리스트만 role=staff로 통과). 기존 관리 콘솔은 `console.html`로 이동해 stock 관리자 › `Key Indicator 관리`(iframe, `hub/AdminKeyIndicator.jsx`)에서 연다. ② 아이디·PIN 계정 삭제(DB 백업 `backups/ceo_briefing.db.before_pin_removal_20260927`, 하드코딩 기본 PIN 제거) — newsinfo 로그인은 `backend/admin_password.py`가 stock `.env`의 ADMIN_PASSWORD_HASH로 검증(비밀번호 1개 공용). ③ 「내 투자」(매수후보·계좌현황)는 메뉴 진입부터 관리자 비밀번호(`LOCKED_TABS`), 사이드바 맨 아래로 이동, 별도 `PORTFOLIO_VIEW_PASSWORD`·`pf_view` 폐지(security_gate viewer=관리자 전용, `/api/buy-candidates` 포함). ④ 배포 시 옛 해시 파일 보존(`scripts/deploy_frontend.sh`)+오래된 탭 자동 새로고침(`main.jsx`) — 배포 직후 lazy 화면 오류 방지.
2026-09-27(Claude) 「내 투자」(매수후보+계좌현황) 잠금 재설계: 관리자 로그인과 **별개로** 비밀번호를 다시 입력해 발급하는 `sd_invest` 쿠키(30분)가 있어야 열림(`POST /api/admin-auth/invest-unlock`·`invest-status`·`invest-lock`, security_gate viewer 단계는 관리자 쿠키를 인정하지 않음, API 토큰·Access 관리자만 예외). 화면은 잠금 시 하위 메뉴 숨김·🔒/🔓 표시·「지금 잠그기」.
2026-09-27(Claude) 「오늘의 섹터 신호」(`views/SectorSignalSummary.jsx`)를 전체 섹터 한 표(섹터·신호등·핵심 사유 한 줄, 행 클릭=섹터 로테이션 상세)로 개편. Info 메뉴 재편: 시황(주요 지표·수급 현황·종합 RS·자금 흐름·섹터 지표·섹터 로테이션·ETF 자금)/종목/퀀트지표/공시·리포트/내 투자, 라벨 이모지 자동 제거(`stripEmoji`).
2026-09-27(Claude RS 점검) RS 계산이 5가지 — [docs/RS_METHODS_REVIEW_20260927.md](docs/RS_METHODS_REVIEW_20260927.md). 종합 RS 섹터 칩의 "100"은 화면이 최저~최고로 늘린 값이라 제거(반도체 실제 12M 평균 RS 74.9).
2026-09-27(Claude) 키움 8050 재확인: 등록 IP(49.170.20.30)는 안 바뀌었고(`logs/kiwoom_ip_state.json`) 지금 직접 발급 테스트·최신 백엔드 세션의 `키움외국인지분율`(133,823행)도 정상 — 오늘 낮 실패 9,444건은 재시작 이전 구간의 일시 장애(429 폭주 직후 8050 다발)로 추정, 현재는 정상. 텔레그램 관심도 수집은 사용자 지시로 재개 보류(대안 검토 중, `telegram_monitor.py` 비활성 유지).
2026-09-27(Claude UI 마무리) ① 모바일 사이드바 높이가 하단 고정 메뉴(60px)를 빼지 않아 맨 아래 섹션(「내 투자」)이 그 뒤에 가려져 눌러도 하단 메뉴 버튼이 대신 눌리던 문제 수정(App.jsx aside height calc). ② 「섹터 지표」를 「섹터 분류」로 개명해 반도체 섹터 바로 아래로 이동(퀀트지표 섹션), 내부의 주도섹터 진입신호 표는 섹터 로테이션과 중복이라 제거(MarketRadarView.jsx) — 섹터 로테이션이 정본. ③ 국내 종목 상세 재무 지표 타일(7→8칸)에 RS(종합) 추가, 신규 경량 엔드포인트 `GET /api/stock-analysis-rs/stock/{code}`(대시보드 캐시 재사용, 재계산 없음) — 최초 구현이 StockAnalysis의 안정화 클로저(_saState.current 패턴) 밖에서 상태를 직접 참조해 절대 갱신되지 않던 버그를 `_saState.current.stockRs`로 수정. ④ Stock Lab 내부 가로 탭(전략 센터 6개·실험 로드맵 5개)을 Stock Info처럼 왼쪽 메뉴로 재배열 — StrategyHub/ExperimentRoadmapView에 initialHubTab/initialPageTab prop 추가(MarketRadarView initialSector와 동일 패턴), 탭 키는 `strategy_hub_*`/`exp_roadmap_*`. ⑤ Stock LLM을 정식 모듈로 승격(`/stock-llm`, 키도 경로와 동일하게 `stock-llm` — 라우터가 첫 URL 세그먼트를 그대로 MODULES 키로 조회하므로 `llm`처럼 다르면 허브로 튕김) — 다른 모듈과 동일한 AppBar+왼쪽 사이드바 안에서 Brian_RAG를 iframe으로 연다(`hub/StockLlmView.jsx`). iframe 내부 UI가 예상(밝은 테마)과 다른 다른 구성(어두운 테마, 검색/분류체계/주제 탭)으로 떠 있음 — 다른 세션이 Brian_RAG 프런트를 별도로 교체한 것으로 보임, 내부 테마는 이 세션 책임범위 밖이라 손대지 않음.
2026-09-27(Claude) Stock LLM 내부 테마 라이트 전환: `/Volumes/Realtek_NVME/Brian_RAG/ui/dashboard.html`(다른 세션이 오늘 저녁 새로 만든 정식 프런트 — 기존 web_app.py 인라인 HTML은 폐기됨, `web_app.py.bak_20260927`에 구버전 보존)의 `:root` 다크 토큰 + 태그/배너/인사이트 텍스트 리터럴 색을 라이트로 교체(백업 `runtime/backups/brian_rag_dashboard.html.before_light_20260927`). Brian_RAG는 별도 비-git 폴더라 이 커밋 범위 밖 — 파일 직접 수정만 적용, 재시작 불필요(정적 파일 즉시 반영 확인).
2026-09-27(Claude) 섹터 분류(구 섹터 지표) 재정리: 메뉴 순서를 반도체 섹터 앞으로, 가로 스크롤 탭(오른쪽 섹터가 안 보이던 문제) → 15개 섹터 전체를 감싸는 히트 그리드로 교체 — 박스 하나에 위 미국(전일 대표주 평균, 워크포워드 검증 6섹터만)/아래 한국(당일 평균등락) %, 붉음=상승·푸름=하락 배경 강도, 한국 등락 내림차순 정렬로 핫한 섹터가 상단에 오도록. 미국 신호 키 체계(auto_ev/healthcare/financials/materials/industrials)가 섹터 탭 키와 달라 매핑(US_KEY_TO_RADAR_KEY) 추가. 새 API 호출: `/api/market-radar/all`(기존에 있었으나 이 화면에서 미사용이던 15섹터 요약).
2026-09-27(Claude 미국 종목 페이지 정보 격차 보강 — 수급 제외) 사용자가 국내 종목 페이지 수준 정보를 미국 페이지에도 요청, 수급(기관/외국인 순매수)만 범위에서 제외하고 진행. **먼저 정정**: 직전 조사에서 "컨센서스/RS 없음"이라 답했으나 오답이었음 — `/api/us/stocks/detail/{ticker}`(main.py get_us_stock_detail)는 이미 `target_mean_price/recommendations_summary/earnings_estimate/revenue_estimate`(yfinance) 등 컨센서스 전체와 `rs_score`(S&P500 대비 3개월 초과수익) 를 반환하고 프론트([App.jsx:7528](frontend/src/App.jsx:7528) 부근, `rs_score`는 7034/7159)도 이미 렌더링 중 — 국내 전용으로 막힌 건 별개 라우트 `/api/consensus`뿐. 실제 빠져 있던 건 두 가지: ① **주봉/월봉**: `us_price_history`가 일봉만 저장하고 `us_market_data.aggregate_weekly_ohlcv`가 있어도 API/화면과 미연결이었음 → `us_market_data.py`에 `aggregate_monthly_ohlcv` 신규(공통 로직은 `_aggregate_by_bucket`로 통합) + `/api/us/stocks/chart/{ticker}`에 `period=daily|weekly|monthly` 파라미터 추가(원본 일봉을 그대로 집계) + 프론트에 일봉/주봉/월봉 토글과 10년 구간 버튼 추가(`chartPeriodUs` state, App.jsx:6083/6282/7234). 브라우저로 AAPL 일/주/월봉 전환 확인 완료. ② **재무제표/현금흐름 커버리지 감사 부재**: 국내는 DART 다층검증이 있는데 미국은 온디맨드 수집뿐이라 커버리지 통계가 없었음 → `scripts/audit_us_financial_coverage.py` 신설(연간/분기별 종목 커버리지 %, 핵심 필드 NULL율, 상세페이지 최소기준 미달 표본) → `docs/us_financial_coverage_audit_latest.md`. 결과: 유니버스 3,676개 중 연간 재무 90.78%·현금흐름 90.72% 커버, 미달 종목은 대부분 SPAC(블랭크체크 회사, 재무가 원래 얇음). `tests/test_us_market_data.py`에 월봉 테스트 추가, 전체 통과.
2026-09-27(Claude 섹터 로테이션 ↔ 섹터 분류 통합) 사용자 지시("두 화면 섹터가 안 맞는다, 세분화가 좋겠다, 미국 주식 없으면 대표종목 추가")로 두 화면을 18개 섹터로 일치시킴. ① `sector_rotation.py`(10개)에 `market_radar.py`의 radar_sector_override 큐레이션을 재사용해 8개 신규(자동차/산업재·건설/해운/금융·지주/소재·화학/철강·비철금속/통신·플랫폼/IT·하드웨어) 추가, "전력기기" label→"전력산업"으로 표시명만 통일(dict 키는 backtest_common.py 등 다른 참조 보존을 위해 유지). ② `market_radar.py`(15개)에 반대로 로테이션에만 있던 3개(기판/패키지·화장품/뷰티·의료기기/미용) 신규 추가 — 국내 종목은 로테이션 큐레이션 재사용, 해외 대표주는 신규 선정(`scripts/ops/seed_new_radar_sectors_20260927.py`, radar_sector_override에 35건 적재). "K방산"→"방산" 표시명 통일. ③ 미국 오버나잇 신호: 기존 6개 검증 섹터(_SECTOR_LEADLAG_DEFS, backtested_hit_rate 있음)는 유지하고, 나머지 12개는 워크포워드 검증 없는 대표종목 바스켓 단순평균을 `_SECTOR_REP_BASKETS`로 신규 추가해 `validated:false`로 구분 반환(과장 방지) — 화면은 "미국·참고" 라벨과 옅은 명도로 구분 표시. 새 종목의 미국 시세는 PG `us_price_history`(신호 계산용)와 `us_market.db`(섹터 상세 화면용) 두 곳 모두에 yfinance로 적재 필요함을 이번에 재확인(두 DB가 별도 경로 — 각 55개/58개 티커 2년치 적재). 재정렬 후 `/api/sector-rotation/refresh-cache`(DB 캐시, `sector_rotation_cache` 테이블) 수동 실행 필요 — 다음 스케줄러 실행부터는 자동 반영.
2026-09-27(Claude 미국 주도섹터·주도주 판정 신규) 사용자 지시("미국 나스닥/S&P500도 주도섹터·주도주 판정해줘, 섹터로테이션/섹터분류 중 어디가 맞을지 판단, 전체 구성도 정리")로 ① 역할 분리 결정: **섹터 로테이션=점수·4분면·리더종목을 산출하는 판정 엔진**(국내 담당 함수가 이미 그 역할), **섹터 분류=시세 비교/탐색 카탈로그**(점수 로직 없음) — 신규 기능은 섹터 로테이션에 추가하고 섹터 분류 헤더에 안내 문구만 추가. ② 신규 `routes/us_sector_rotation.py`: 11개 GICS 유사 섹터(SPDR 섹터 ETF 11종 XLK/XLF/XLE/XLI/XLP/XLY/XLV/XLB/XLU/XLRE/XLC를 섹터지표로, us_stock_meta.sector와 매핑) × `?universe=sp500|nasdaq`(us_stock_meta.index_name 필터, 벤치마크 SPY/QQQ). 국내와 달리 수급·실적 데이터가 없어 가격·거래량 팩터(RS 초과수익 4주/12주 + 거래량비 + 섹터폭[52주고점권 비율])만으로 점수·Leading/Improving/Weakening/Lagging 4분면·진입단계 산출(임계값은 국내와 동일 BUY65+/WATCH40+ 유지). 리더종목(주도주)은 **52주 고점권 모멘텀 기준**(IBD/오닐 스타일)으로 국내(52주 저점 근처 저평가 반전 후보)와 의도적으로 반대 방향 — 시장 성격 차이(미국=추세 추종, 국내=수급 선행) 반영. API 4종(leadership/scores/rotation-map/top-picks), 모듈 내 캐시 TTL 15분. SPY/QQQ/11개 섹터ETF+미도입 티커(원자력·해운·소재 등 대표주) 총 68종을 yfinance로 PG `us_price_history`에 2년치 신규 적재. ③ `SectorRotationView.jsx`에 국내/S&P500/나스닥 상단 토글 추가 — 기존 4탭(주도섹터·진입/섹터스코어/4분면맵/RS히스토리) 중 앞 3개를 국내·미국 공용으로 재사용(같은 컴포넌트가 API만 갈아끼움), RS히스토리는 미국 월별 데이터 미제공이라 미국 모드에서 숨김. 국내 전용 컬럼(외국인·기관수급/수출·영업이익YoY)은 미국 모드에서 RS·거래량비·섹터폭·기간수익률로 교체. S&P500 11섹터 계산 ~11초, 나스닥(3,600종목) ~23초 — 캐시 히트 시 즉시 응답.
2026-09-27(Claude 국내/미국 리더종목 기준 차이 강조 + 미국→국내 선행신호) 사용자 지시 2건("설계 차이를 명확히 표시" · "국내는 미국의 후행이니 미국 결과를 국내에 적용 가능하게") 반영. ① **기준 차이 배너**: SectorRotationView.jsx 상단에 국내(🇰🇷 52주 저점권 저평가 반전)/미국(🇺🇸 52주 고점권 모멘텀 추종) 2단 카드를 탭·토글과 무관하게 항상 노출, 현재 선택된 시장 쪽을 굵은 테두리로 강조 — 이전엔 subtitle 한 줄이라 놓치기 쉬웠음. ② **미국 선행→국내 후행 신호 신규**: `scripts/ops/analyze_us_kr_sector_leadlag_20260927.py`로 미국 섹터 ETF 당일등락 vs "다음 국내 거래일" KR 섹터 바스켓 등락의 방향일치율을 실측(2024-09-30~2026-09-23, 483쌍, 학습60%/검증40%, market_radar.py의 기존 6개 검증 신호와 같은 정렬 방식) — 18개 KR 섹터 중 16개가 미국 GICS 섹터에 매핑되고 검증 적중률 49.0~66.0%(원자력·2차전지는 대응 섹터 없음, 매핑 제외). 결과를 `routes/sector_rotation.py`의 `US_TO_KR_LEADLAG`(정적 표, tier: strong≥58%/moderate 54~58%/weak<54%)에 반영, 신규 `GET /api/sector-rotation/us-leadlag?universe=sp500`가 이 표 + `us_sector_rotation._score_sector_us()`로 얻은 미국 섹터의 "지금" 국면을 결합해 반환. 프런트(국내 모드 전용)는 ① 상단 요약 배너(18개 섹터 방향 ▲▼ pill, tier 색점) ② 주도섹터 진입 테이블의 섹터명 아래 인라인 배지(`🇺🇸▲기술` 등)로 표시 — 신뢰도 낮은 신호(weak)까지 굳이 강조하지 않되 투명하게 함께 보여줌. 결과 원본은 `docs/us_kr_sector_leadlag_backtest_20260927.json`(재현 가능).
2026-09-29(Claude 계좌현황 포트폴리오 전면 교체) 사용자가 HTS 캡처(10종목, 2개 소계로 검산: 572,194,343/102,306,727 + 6,907,500/-745,350)를 주고 "이게 전체 보유종목"이라고 확정 — 반영 범위를 먼저 AskUserQuestion으로 확인 후(기존 DB에 이미지에 없는 종목 30여개가 더 있어 삭제 여부가 갈림) 사용자가 "전체 교체, 나머지 삭제"를 명시적으로 선택. `scripts/ops/replace_portfolio_20260929.py`로 기존 `portfolio` 53행 전량을 `backups/portfolio_backup_20260929.csv`에 백업한 뒤 삭제하고, 캡처 10종목(에이엘티·아이엠티·에스티아이·티에프이·월덱스·에스지헬스케어·큐리오시스·리브스메드·LS에코에너지·AJ네트웍스)만 정확한 수량·매입가로 재삽입(매입총액 역산으로 검산 완료, 큐리오시스는 기존 DB에 없어 stock_universe에서 코드 494120 신규 조회). `GET /api/portfolio`로 10종목 정확히 반영 확인.
2026-09-29(Claude 미결 3건 검토 — governance 전략 재판정·수급 신호 유효성 검증) ① **v5/vbr/v1_value 재활성화 불가 확정**: v5(-4.65%, +0/6), v1_value(-2.67%, +2/6)는 9/27 재계산으로 더 악화. vbr은 avg6 +22.8%·+3/6이나 rank=legacy(verification_status=0)라 governance 기준 `rank >= 1` 미달 — 재실행해도 동일 결과. 세 전략 모두 retired 유지. ② **kiwoom_foreign_flow(외국인지분율) 신호 기각**: 2026-03~09 6개월 데이터, 20일 weight 변화 → 30/60일 forward return, Q5-Q1 spread ≤ +1.05pp, Spearman r=0.025~0.042 → 경제적 무의미. ③ **kiwoom_investor_daily 수급 신호 예측력 없음**: 대형주 500개 2021~2025 월말 스냅샷(22,545개). orgn 20일 누적 → 3/6/12개월: Spearman r=-0.008~+0.002(p>0.20, 무의미). frgnr_invsr 12개월: r=-0.031(p<0.001, **역방향** — 외국인이 판 종목이 오히려 12개월 수익률이 높음, Q5-Q1=-5.42pp). **결론: backtest에 직접 연결 가치 없음**, price_history 기존 inst_net_buy/frn_net_buy로 충분. 분석 스크립트: `scratch/kiwoom_investor_signal_test_20260929.py`. ④ **삼성전자 가격 오류 확인**: price_history 2022~2024 기간 2배 이상 급변일 = 0건, price_jump_audit 2022+ 행 없음. 이전 세션 "2배 이상 중복 행" 메시지는 재무 데이터(CFS/OFS 혼재, is_annual=True 연간합계 vs False 분기증분 4.3배 차이)에서 유래한 것으로 주가 오류 아님.
2026-09-29(Claude Key Indicator 공개 화면 — 상세 이력 복원 + 관리 콘솔 링크 제거) 사용자 지시 2건: ① "예전엔 주요 경제지표 페이지가 상세 내역을 보여줬는데 지금은 안 보인다" ② "Key Indicator 페이지에 관리자 콘솔 링크가 그대로 살아있다, 삭제해". 코드는 이 저장소 밖 `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/frontend`(newsinfo.cloud, 정적 `python -m http.server 5500`로 서빙). 조사 결과: 9/27 공개화면 단순화 당시 옛 상세뷰(10년 이력 차트+표, `kai.js`의 `showDetail()`)가 통째로 빠졌는데, 정작 **백엔드 API(`/api/eco/indicators/{code}/history`, `/api/global-macro/timeseries/{code}`)는 이미 `role_gate.py` PUBLIC_READ에 등록돼 있어 프런트만 안 쓰고 있던 상태**(백엔드 변경 불필요). 또한 현재 관리 콘솔(`console.html`+`admin-console-runtime-v2.js`)에는 애초 경제지표 페이지 자체가 없어(구식 `kai.js`에만 존재, 배선 안 됨) "복원할 관리자판"도 실질적으로 없었음 — 그래서 공개 화면(`keyindicator.js`) 안에 직접 상세이력 모달을 새로 구현: 국내·글로벌 지표 표의 행 클릭 → 모달에서 의존성 없는 인라인 SVG 스파크라인 차트 + 최근 30건 표(날짜·값·증감·증감률) 표시(`index.html`+`keyindicator.js`+`keyindicator.css`, 캐시버스터 `?v=20260929`). `index.html` 헤더의 `<a class="ki-admin" href=".../admin/admin_ki">관리 콘솔(관리자)</a>` 링크는 완전 삭제(공개 페이지에 관리자 진입점을 노출하지 않음 — 실제 관리자 접근은 stock.leanguy.cloud 관리자 메뉴로만). 브라우저에서 국내(실업률, 125건 10년치)·글로벌(WTI 원유, 1,047건) 둘 다 정상 동작 확인.
2026-09-29(Claude 전체 점검 8건 — 키움 IP 재등록 필요/체인 잡 6주 정지 수정/포트폴리오 다크색 잔재/성능) 사용자가 한 번에 8가지 문제 제기, 전수 조사·수정. **⚠️ 사용자 조치 필요**: 공인 IP가 9/24 이후 `49.170.20.30→49.170.20.33`으로 또 바뀌었는데 키움 포털에 미등록(8050) — `logs/kiwoom_ip_state.json` 확인, 새 IP를 키움 REST API 포털 허용 IP에 등록해야 함. 이게 ③④번(대차/공매도·프로그램매매 정지)의 직접 원인. ① **로딩 속도**: `analysis`(국내종목) 페이지가 종목 하나 열 때 API를 **55개 동시 호출**하고 그중 `/api/company-intelligence/company/{code}`가 18초·`/api/extra-signals/extra-signals/{code}` 14초·`/api/kiwoom/summary` 12.9초 등 개별 응답 자체가 매우 느림 — company-intelligence는 캐시가 전혀 없이 텍스트근거 스캔+동료비교+**매 GET마다 DB 재기록(`_persist_profile`)**까지 하고 있어 10분 TTL 캐시(`_COMPANY_INTEL_CACHE`, `?refresh=true`로 우회 가능)를 추가— 이 한 엔드포인트만 조치, 나머지 다수 느린 엔드포인트는 이번엔 손 못 댐(후속 필요, 목록은 이 항목 참고). ② **계좌현황 색상 불일치**: `App.jsx` PortfolioView의 외인/기관 수급 타일이 라이트테마 전환 코드모드가 못 잡은 하드코딩 다크네이비 배경(`rgba(20,30,50,0.75)`)에 다크텍스트를 얹고 있어 거의 안 보였음 — 이미 계산돼 있던 `light().bg`(연한 초록/빨강/회색 틴트)를 쓰도록 수정. 같은 패턴을 전체 프런트에서 재검색해 `EtfCheckView.jsx`의 sticky 표 헤더(`rgba(10,18,50,0.98)`, 98% 불투명 다크네이비)도 동일 문제로 발견·수정(다른 페이지와 통일된 `#dbe6f7`/`#14315f`). `rgba(0,0,0,0.6x)` 3건은 모달 배경 딤 처리라 정상— 오탐 아님. ③④ **대차/공매도·프로그램매매 정지**: `program_trading_daily`·`broker_program_market_daily` 둘 다 9/23에 멈춤 — 원인은 키움 8050(위 IP 재등록 필요 항목). ⑤ **바닥/반등 신호**: `routes/extra_signals.py`가 매 요청마다 price_history 최신값으로 즉시 재계산하는 구조라 스케줄러 없이도 매일 자동 반영됨(정상 동작, 응답이 느린 건 ①과 같은 원인). ⑥ **투자 근거·검증 8/13 정지 — 6주간 조용히 막혀있던 잡 체인 발견·수정**: `_job_krx_base_info`(매일 18:35) 안에서 자본행위보정→가격급변감사→외부가격검증→시장국면→설명형신호→**전략센터전진신호(`live_signal_registry` 적재)**→신호사후성과→전진검증감사가 순차 `if returncode==0` 중첩 조건으로 체인돼 있는데, 첫 단계 `build_corporate_action_adjustment_engine.py`의 `stock_price_daily` 전체 LAG 윈도우함수 스캔(격리실행 시 ~18~20초)이 스케줄러 동시부하 시 기본 statement_timeout(30s)을 매일 초과해 `QueryCanceled`로 실패 → 이후 7단계 전부 한 번도 안 돌아 `live_signal_registry` 8/13 이후 신규 신호 0건(live_signal_outcomes는 기존 신호 사후추적만 계속 업데이트돼 "부분적으로는 최신"이라 더 못 알아챘음). 스크립트 세션 한정 `SET statement_timeout='120s'`로 수정(전역 30s는 유지, `scripts/audit_and_repair_postgres_data.py` 등 기존 선례와 동일 패턴) — 수동 실행으로 1단계 확인 완료(성공, 4,637건 처리), 2~8단계는 이 커밋 시점 백그라운드로 체인 실행 중(가격급변감사가 몇 분 걸릴 수 있음 — CLAUDE.md 알려진 이슈의 "서버 재시작 직후 price_history AccessExclusiveLock" 항목과 같은 스크립트). ⑦ Key Indicator 복원 — 바로 위 항목 참고(같은 세션에서 처리, 실제로는 반영돼 있었고 브라우저 캐시 문제로 추정 — 사용자에게 강력 새로고침 안내). ⑧ **git 충돌 없음** — `git status`에 충돌 마커 無, merge/rebase 진행 상태 無, 현재 브랜치는 원격보다 2커밋 앞선 정상 상태.
2026-09-29(Claude ETF 자금 페이지 경고 배너 근본 수정 — 상장폐지 ETF 자동 감지·제외) 사용자 지시("이티에프는 계속 폐기 생성이 되는데 적절한 조치를해") — 바로 위 항목 ③에서 원인만 규명하고 미뤄둔 건을 마무리. **근본원인**: KIS 마스터파일(ETF 유니버스 동기화 소스)이 KRX 상장폐지를 즉시 반영 안 해, 상폐된 종목(454180/464240/488200/488210)이 유니버스에 계속 남아 매일 빈 PDF를 내고, 파이프라인 안에 독립적으로 존재하던 **all-or-nothing "전종목 성공" 판정 게이트 4곳**(`full_pdf_audit.health()`/`full_pdf_collector.assess_and_publish()`/`verify_daily_pipeline.verify()`/`publish_direct_stock_daily._quality_gate()`)이 전부 이 4개 때문에 9/23 이후 영구적으로 "미완료"로 막혀 있었음(1,171/1,175=99.7% 정상 수집되고도 화면엔 "6일째 갱신 안 됨"으로 표시). **조치**: 신규 `ETF_check/etf_delisting_watch.py`(`etf_delisting_exclusion` 테이블 신설) — 최근 PDF 스냅샷이 threshold일(기본 2일) 연속 empty/error인 종목을 자동 감지해 제외 등록, 이후 성공하면 자동 복구(self-healing)하는 상시 감시 스테이지. 4개 게이트 전부에 `_excluded_delisted_tickers()` 필터를 추가해 PDF 관련 커버리지 체크(스냅샷·성공건수)만 제외 반영하고, 시가총액(`etf_scale_daily`)은 상폐 후에도 값이 남아있어 원래 전체 유니버스 기준 그대로 유지(직접 쿼리로 확인 후 반영). `scripts/run_etf_daily_pipeline.sh`에 `delisting_watch` 스테이지를 `full_pdf` 직후 비차단(파이프라인 전체를 막지 않음)으로 추가. 검증: 이미 수집된 9/29 스냅샷으로 4개 게이트 전부 재실행해 통과 확인(`verify_daily_pipeline` ok:true, `latest_publishable_date()`가 9/23→9/29로 복구) → `run_etf_direct_publish.sh` 실행해 `published:2693` 반영 → 브라우저에서 `/info/etf_check` 경고 배너 사라지고 "최근 수집일: 2026-09-29" 정상 표시 확인. 향후 새 ETF가 상폐돼도 사람 개입 없이 2일 내 자동 격리된다.
2026-09-29(Claude 후속 — 전 페이지·탭 전수 점검 + 8건 마무리) 사용자 지시("페이지마다 들어가서 에러 확인, 탭도 눌러봐" · "나머지 항목도 완벽하게 마무리"). ① **잡 체인 재검증 완료**: 백그라운드로 돌리던 2~8단계(가격급변감사→...→전진검증감사) 전부 exit=0 완료 확인. 다만 6단계(전략센터전진신호)가 `captured:0, skipped_existing_episode:8`을 반환 — `live_signal_registry`가 여전히 8/14·37건에 멈춰 있는 이유는 현재 추적 중인 두 전략(v_contract_momentum·v_gc)의 "에피소드"가 6주 정지 동안 한 번도 안 닫혀 여전히 "열려있는" 것으로 간주돼 신규 등록을 스킵하기 때문(중복 방지 로직 자체는 정상 동작) — 두 전략 모두 `elapsed_calendar_days:47`(정책 horizon 20일을 훌쩍 넘음), `status:collecting`으로 조만간 결과가 확정되며 앞으로 매일 정상 실행되므로 며칠 내 자연 해소 예상, 강제 조치 안 함. ② **전 페이지 콘솔 에러 전수 점검**: Stock Info 21개 탭 + Stock Lab 16개 탭 + 허브 랜딩, 총 38개 화면을 새 브라우저 탭(과거 세션 잔여 에러 섞임 방지)으로 전부 열어 콘솔 에러 확인 — **전부 에러 0건**. 잠금 화면(내 투자·관리자·Stock LLM)도 정상적으로 잠금 UI만 표시(크래시 없음). 섹터 로테이션(국내→S&P500 전환, 4분면 맵), 섹터 분류(기판/패키지 타일 클릭) 등 페이지 내부 탭·클릭도 직접 눌러 확인 — 전부 정상. ③ **신규 발견: ETF 자금 페이지 자체 경고 배너 — 원인 규명**: `etf_check` 탭 상단에 "ETF 데이터가 최근 6일 갱신 안 됨(9/23 기준)" 경고가 떠 있어 조사 — 일별 파이프라인(`run_etf_daily_pipeline.sh`)은 매일 정상 실행 중이고 1175개 중 1171개(99.7%)는 매일 새로 수집되고 있으나, **`etf_pdf_full_audit`의 "healthy" 판정이 all-or-nothing이라 단 1개 티커라도 빈 PDF면 그날 전체가 "미완료"로 처리**되고, 정확히 4개 티커(454180/464240/488200/488210)가 9/28부터 계속 빈 PDF(`raw_pdf/*/454180.json.gz`가 9/23 1750바이트→9/28부터 46바이트=빈 배열 `[]`)를 반환해 그 뒤로 "healthy" 날짜가 하나도 안 쌓임. 웹 검색으로 488210(KIWOOM K-반도체북미공급망)이 **9/22 KRX 상장폐지 공시**, 9/23부터 거래량 0임을 확인 — 나머지 3개도 같은 시기 상장폐지로 추정(개별 확인은 못 함). `stock_universe`에도 4개 다 없음(교차 확인). **코드 수정은 보류**: ETF 유니버스가 KIS 마스터파일(`KIS_MASTER_ALNUM_V2`) 동기화에 의존하는데 그 파일이 아직 이 4개를 안 뺀 상태로 보이고, 운영 중인 금융데이터 파이프라인의 판정 로직을 이 세션에서 검증 없이 건드리는 위험을 피하기 위해 원인 규명까지만 하고 실제 제외 처리는 다음 세션/사용자 확인 후로 넘김(운용사 상장폐지 공시 근거: 키움자산운용 488210, 상세 조사 시 KIND 공시사이트 acptNo 기반 검색 가능).
2026-10-02(Claude SQLite 제거 마무리 — live_signal_tracker + capture_signals + routes 핵심 파일): ① `live_signal_tracker.py` `update_outcomes` 함수 PG 전환 완료: `conn.row_factory=sqlite3.Row` 제거, `signal["key"]` dict 접근 → `cur.execute()+cols` 방식, `?`→`%s`, `connect_stock_db`→`db_compat.connect_primary_db`. ② `scripts/capture_strategy_center_forward_signals.py` PG 전환: `connect_stock_db`→`db_compat.connect_primary_db`, `conn.execute()`→`cur.execute()`, `?`→`%s`. ③ routes/ 핵심 파일 `connect_stock_db`→`db_compat.connect_primary_db` 명시 교체: `trend.py`(3곳, `STOCK_DB_PATH`+`DB_PATH` 제거), `signals.py`(1곳), `contract_advance_signals.py`(1곳+`DB_PATH` 제거), `company_intelligence.py`(4곳), `cherry_screener.py`(2곳+마이그레이션 주석 제거). ④ 확인: `db_utils.connect_stock_db`는 `IS_POSTGRES=True` 시 이미 `PostgresCompatConnection`을 반환 → `scheduler.py` 등 30개+ 잔여 파일은 동작 정상이므로 긴급 교체 불필요. ⑤ `db_compat.PostgresCompatCursor`가 SQL `?`→`%s` 자동 변환 확인 — 기존 SQLite 쿼리 호환.
2026-10-02(Claude 프론트엔드 최적화 4건 + hidden 탭 5개 복원 + Key Indicator 개선):
① extra_signals 3분 캐시(`_EXTRA_SIGNALS_CACHE`, TTL 180s), kiwoom summary 5분 캐시(`_KIWOOM_SUMMARY_CACHE`, TTL 300s) — 14초·12.9초 응답 캐시 처리.
② Stock LLM 접속 시 왼쪽 메뉴 2개 생기는 버그 수정: `App.jsx` aside에 `display: module === 'stock-llm' ? 'none' : 'flex'` + 모바일 오버레이도 stock-llm 제외.
③ 모바일 API 토큰 팝업 `window.prompt` → DOM 인라인 모달(`apiToken.js` 전면 재작성, 취소 후 10분 쿨다운 유지).
④ `modules.js` hidden 탭 5개 → 각 섹션으로 복원(info: watchlist·insight → 종목 섹션, lab: screener·megatrend → 종목발굴, backtest → 전략센터); `App.jsx` NAV_ITEMS에 5개 아이콘·라벨 추가(커밋 40d22ef).
⑤ newsinfo.cloud `index.html` 탭 바에 「🛰️ 시장분석 · AI 인텔리전스 ↗」(/kai/) 바로가기 추가 — 9/27 공개화면 교체로 사라진 kai.js 기능 접근 복원. 이력 모달(스파크라인 차트·상세 이력 30건) 및 CSS도 함께 커밋(ceo-briefing-platform 1d0dba2).

2026-09-30(Claude Stock LLM LLM 보강 정체 개선): 사용자가 `/stock-llm/llm_console`의 "LLM 보강"이 2711 근처에서 정체된다고 지적. Brian_RAG Postgres 확인 결과 실제 enriched는 2,714건까지 올라갔고 `com.brian-rag.enrich` launchd job도 running 상태였으나, 한 건당 20~50초 걸리는 7B Ollama 추론 + `EOF while parsing` JSON 잘림 실패 + hourly StartInterval 때문에 중간 yield 후 최대 1시간 쉬는 구조가 체감 정체의 원인. `/Volumes/Realtek_NVME/Brian_RAG/scripts/run_enrichment.py`에 compact JSON 규칙과 truncation retry(CompanyFacts 1800→2800 tokens, ReportFacts 1200→2200 tokens)를 추가. `scripts/run_enrich_job.py`는 사용자 interactive insight가 없고 ingest가 끝난 상태에서 최대 55분 동안 idle loop를 돌며 pending reports를 계속 처리하도록 변경하고, idle turbo governor 기본값(duty 0.95, max_cpu 90)을 적용. `scripts/service/install_launchd.sh`의 enrich StartInterval은 3600→600초로 축소해 프로세스가 끝나도 10분 내 재시도하게 함. 문법 확인은 `ast.parse`로 통과(`py_compile`은 외부 Brian_RAG `__pycache__` 쓰기 sandbox 제한으로 사용 불가).

2026-10-02(Claude 포트폴리오 행 부활 정리 — 사용자 지시 "원인 확인 후 정리해") 원인: 커트오버 자동복구(SQLite→PG 브리지)가 09-29 사용자 삭제분 49행을 되살림(로그 `portfolio: scanned=49 sqlite=50 postgres=59`). 49행 백업(`portfolio_backup_revived_rows_20261002`) 후 삭제 → 10종목(run_id `portfolio_revived_cleanup_20261002`), 사용자 관리 테이블 4종을 자동복구 판정·브리지에서 제외.
2026-10-02(Claude 파일·잡 정리 — 사용자 지시 "필요없는 잡·파이썬·md 정리", 방식 "보관 폴더로 이동" 선택) 운영 코드·launchd·스케줄러·정본 문서(CLAUDE.md/SYSTEM_MAP/hermes) 어디에서도 참조되지 않는 Python 289개(루트 23·scripts 266)·md 396개, scratch 14일 초과 1,348개(157→18MB), 완료된 launchd `us_delisted_backfill`을 `/Volumes/Realtek_NVME/stock_dashboard/archive_20261002/`로 이동(목록 `MANIFEST_*.txt`, 복원은 같은 상대 경로로 mv). `.claude/` 설정은 제외. 이동 후 전체 테스트 619 통과.
2026-10-03(Claude 숫자 데이터 자동 재수집) launchd `com.stock-dashboard.numeric-recollect-dart`(00:20, DART 키1·3, 한도 소진 시 종료)·`-fnguide`(04:00, FnGuide wcomp 원문 저장 1,000건+원문 대조) 신설(`scripts/review/daily_numeric_recollect.sh`). 스케줄러 `FnGuideDART전종목검증` 한도 450→150종목. 외국기업 16종목 재무 원화 환산(`fx_krw_20261003_131733`). 상세 [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) §6·§7.
2026-10-03(Claude Codex 의견 반영) 신규 테이블 `financial_field_verification`(현행 기준 필드 확정 상태)·`financial_facts_pit`(최초 공시값/재작성값·available_at), 매일 자동 재구축. `/api/dashboard/data-quality` 등급을 현행 기준으로 fail-closed(대조 없음=U, 원인 조사=C, 전부 일치=A), 응답에 `current_verification` 추가. 상세 docs/FINANCIAL_STATEMENTS.md §9-1.
2026-10-03(Claude 감가상각·CapEx 구성요소) 신규 테이블 `financial_dep_capex_components`(매일 재구축), `/api/dashboard/cashflow` 연간 응답에 dep_ppe·dep_rou·amort_intangible·capex_intangible·dep_cf_line_basis 추가, 종목 현금흐름표에 내역 행(App.jsx cfRows, 데이터 있을 때만). 정의는 docs/FINANCIAL_STATEMENTS.md §2-1.
2026-10-03(Codex 가격 99.99% 재확인/Claude 적용 큐) PostgreSQL `price_history` 2,801종목·8,239,506행 읽기 전용 감사 결과, 결정적 내부 게이트 통과율 상한 99.949427%로 목표 미달. 별도 핸드오프 문서는 만들지 않고, 99.99% 달성 방법·미점검 항목·Claude 적용 조건을 [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) §9-2-2에 통합.
2026-10-03(Claude .env 통합) 기준 파일 `runtime/.env` 하나로 통합(루트·antigravity·worktree는 링크, ceo-briefing은 자동 생성), launchd `com.stock-dashboard.env-sync`. DART 4번 키 추가·키 4개 순차 사용. 상세 [docs/ENV_MANAGEMENT.md](docs/ENV_MANAGEMENT.md).
2026-10-05(Claude 비12월 결산·수주잔고·Codex 검증 검토) 비12월 결산 기간 키를 회계연도(결산월 연도)+회계분기로 통일(`fiscal_period.py`, 저장 경로 전부 적용, 재배치 1,222행). 수주잔고는 원문 항등식(수주총액−기납품=잔고)으로 판정 가능 7,408행 100%(정정 773행·order_backlog 동기화, 매일 측정). Codex 제3자 검증 항목별 검토는 docs/FINANCIAL_STATEMENTS.md §9-2-6, 기준은 §2-7·§2-4-1.
2026-10-05(Claude 신호·신규상장·매출구성) 추세추종 공통 판정 `trend_rules.py`(모멘텀Easy MA5<MA20×0.96·피크Easy MA20<MA60·손절 -8%·추적 -20%, 무상증자 원주가 급변 보정)로 계좌현황 추세추종/매도시그널(`routes/portfolio.py`)과 차트시그널 종합 판정(`routes/extra_signals.py`) 통일 — 추세 유지 중 밸류만으로 매도 신호 금지. 신규 상장 매일 편입·상장 초기 가격 KIS 채움(`scripts/ops/sync_new_listings.py`, 예전엔 월 1회). 국내/해외 매출 `revenue_geography`(XBRL 지역 주석)+제품별 매출을 종목 페이지에 표시(`/api/dashboard/revenue-mix/{code}`, 묶음 `revenue_mix`).
2026-10-05(Claude 매도 기준가·사업보고서 지표 2021~) 계좌현황 매도시그널을 보유/반등 관찰/매도 + 매도 기준가(`trend_rules.exit_signal`)로 재설계. 사업보고서 본문 원문 수집(`fetch_dart_business_docs_20261005.py`)·파서(`parse_business_docs_20261005.py`: 연구개발비·가동률·원재료 가격·내수/수출·비용 성격·제품별 2021~22)·추가 계정(`build_extra_accounts_20261005.py`: 매출채권·차입금·사채·금융원가·이자)·배당 수집기 대상 전 종목화, 종목 페이지 '사업보고서 지표' 카드. 검증 기준 docs/FINANCIAL_STATEMENTS.md §8-0-0.
2026-10-05(Claude Stock Lab 전략 센터 중복 제거) "3배 라벨 연구 요약"·"휴리스틱 vs ML 상위 추천 품질" 등 연구 카드가 전략 센터 모든 페이지에 반복되던 문제 수정 — 전용 메뉴 `strategy_hub_research`(3배 후보 연구)로 분리. 시장 국면·검증 배너는 성과 매트릭스 전용, 선택 전략 요약은 추천 종목 화면 전용, 왼쪽 메뉴와 중복이던 페이지 내부 탭 버튼과 국면 배너와 중복이던 "현재 국면 전략 우선순위" 카드 삭제, 백테스트 메뉴는 실행 목록 기본 화면. V-SECTOR 설명 패널의 미정의 변수(`strategyAudit`) 크래시도 수정.
2026-10-05 오후(Claude 외부 검토 반영) 매도시그널 추세 이탈을 현재가 비교 → 원 전략대로 5일선 비교, 피크Easy 진입 252일 이력 조건, SIGNAL_RULES.md에 '원 전략과 다른 점'(이 조합은 백테스트 안 함) 명시. 인수인계 §9-2-7에 §9-2-8 결함(1,000배 오류·06:20 반복 루프·리츠·외화) 우선 배치, KRX 주가 백필 완료 반영. 루트 CLAUDE.md·AGENTS.md(신설)·세션 훅에 진입 경로 추가(루트 훅의 옛 App.jsx 경로 수정).
2026-10-05(Claude Stock Lab 전략 재검토 문서) 여러 AI 공동 검토용 [docs/Stock_Strategy.md](docs/Stock_Strategy.md) 신설 — Lab 구조 지도, 발견 15건(S01~S15: 후보 풀 무제한/백테스트 선택 규칙 불일치, 가치 스크리너 OR 조건 850종목, 어댑터 없는 전략 13개 등), Phase 0~5 개선 순서, 전략 인벤토리, 사용자 결정 대기 D1~D6. 코드 변경 없음.
2026-10-05 밤(Claude 2차 검토 반영) 06:20 `재무무결성일일` 보고 전용(값 변경 안 함, 사용자 승인), 단위 오류 복원·재적용 1,000배 거부, 리츠 키 원상 복구·`fiscal_period.is_reit`, 외화 2026Q2 원통화 정리·반기 백필 외화 제외, 매일 이상값 감시 `scripts/ops/check_financial_anomalies_daily.py`(→`data_anomaly_daily`), 매수후보 무상증자 보정, `tests/test_trend_rules.py`, 백테스트 `se_momentum(exit_mode=)` 옵션, 가격 감사 독립 소스·OHLC. 상세 docs/FINANCIAL_STATEMENTS.md §7·§9-2-7.
2026-10-05(Claude Stock_Strategy P0-5) 4분기 단독 재무의 공개일 폴백을 익년 2/15 → 연간 실제 공시일(없으면 익년 3/31)로 변경(`backtest_common._release_date`, 전략 SQL 14곳, `routes/tenbagger.py`·`cherry_screener.py`) — 미래 참조(S16) 제거, 4분기 35,808행 중 35,806행의 공개일이 늦어짐. 백테스트 재실행 전이라 성과 영향 미측정, 회귀 테스트 `tests/test_q4_release_date_20261005.py`. 상세 docs/Stock_Strategy.md.
2026-10-05(Claude Stock_Strategy P0-6/P0-7) 백테스트 run 해시에 공시일 표·정정 로그·PIT 표 버전(`_data_revision_extras`) 추가. 코드·데이터 지문은 이미 있었음(S19 정정). PIT 재무 로더(P0-6)는 PIT 표에 EPS/BPS/ROE·분기 단독값이 없고 순이익·자본 기준 일치율이 60~76%라 구현 보류 — 선행 조건은 docs/Stock_Strategy.md.
2026-10-05(Claude Stock_Strategy P0-3) Screener "Logic v1 — 전체 577종목"은 서버 캐시가 비었을 때 브라우저가 점수 하한·추세 필수·하락장 차단 없이 가치/추세/재무를 2개 이상 겹치게 한 대체 계산이었음(같은 데이터로 577 재현). 대체 계산 삭제, `get_combo_candidates`가 캐시 없을 때 사전계산 시작, `GET /api/signals/combo-status`(단계별 개수) 추가. 서버 정식 콤보는 0종목이며 재무 1,210·가치 850 통과(변별력 문제)·추세 4는 별도 과제(docs/Stock_Strategy.md S25·S26).
2026-10-05(Claude Stock_Strategy P0-2) 매트릭스 거래 0건 구간은 v12·v8·v4의 21.12~22.10으로, KOSPI>MA120 시장 필터가 그 구간 225일 중 0일 통과한 정상 동작(결함 아님). 필터 유무가 전략 평균 수익률 비교에 섞이므로 공통 지표에 현금 보유 비율 필요(docs/Stock_Strategy.md S08·S09).
2026-10-05(Claude Stock_Strategy P0-4 부분) docs/Stock_Strategy.md §6-2에 전략 27개 설명·손절/익절·시총·월한도를 코드에서 추출해 정리. 발견: 백테스트 가치 조건(Graham 25%+ OR PBR<0.7&PER<10)이 화면 가치 스크리너(15%+ OR PBR<1&PER<15)보다 훨씬 엄격 — 같은 이름의 두 규칙.
2026-10-05(Claude Stock_Strategy 잔여 처리) P0-5·P0-7 커밋(e3d76b7), D11: 공개일 게이팅 재무 로더 13곳이 `fs_quirk:dart_unit_error` 기간 34행을 입력에서 제외(fe897d5, DB 값 불변). 추세 스크리너가 적은 이유는 계산 오류가 아니라 '당일 거래량 2배' 단일 조건(월말 34시점 중앙값 RSI 통과 50→최종 10, S26). `scripts/rerun_post_fix_compare_20261005.py`로 전 전략 비교 재실행(선택 안 함, 결과 research_outputs/post_fix_20261005_rerun.json, 이전 매트릭스 matrix_pre_data_fix_20261004.json). P0-9 Codex 수락 검증 의뢰서는 docs/Stock_Strategy.md §9.
2026-10-06(Claude Stock_Strategy P0-8a) 전 전략 6구간 비교 재실행 완료(선택·화면 교체 없음). 가격·재무 무관 전략 4개는 동일, v12(paper_core)는 평균 23.3→16.2·최악 −1.5→−22.7, 평균 상승분 상당수는 데이터 정정 효과(4개 전략 분리). 8개 전략은 실행 직후 가격 무결성 게이트 실패(원인 미규명). 상세 docs/Stock_Strategy.md §9.
2026-10-06(Claude Stock_Strategy) 가격 무결성 게이트 실패 8개 전략은 실제 보유구간 오염 이벤트 때문. v12 22.11~23.10 −47pp는 077500 분할(2023-10-23) 가격 미조정 −64% 가짜 손실로 추정(D12, docs/Stock_Strategy.md §9).
2026-10-06(Claude Stock_Strategy S27) v12 22.11~23.10 −47pp는 데이터·코드가 아니라 선착순 매수 선택이 `ORDER BY` 없는 `stock_universe` 조회(물리 순서) 순서를 따라 결과가 바뀐 것 — 결정 D13 대기. run 지문의 `data_fix_log`에서 지수·환율 보정 등 무관 기록 제외, 비교 재실행 스크립트가 이전 결과를 지우지 않게 수정·v12 결과 복구. 상세 docs/Stock_Strategy.md §9.
2026-10-06(Claude Stock_Strategy D13 점수 순, 사용자 결정) v12 매수 후보를 조건 통과 종목 전체에서 개별 RS(종목 3개월−섹터 평균) 순·동점 종목코드로 선택, 종목 조회 ORDER BY 추가(`selection_order`, 기본 score). 2회 실행 동일 확인, 선택 순서만으로 구간 수익률 최대 24.9pp 차이 — 결과·원장 research_outputs/v12_selection_order_20261006.json·signal_experiment_ledger. 상세 docs/Stock_Strategy.md §9.
2026-10-06(Claude) 야간 작업 확인·후속: 사업보고서 파서 idle-in-transaction 실패 수정·재실행(원문 12,024건), 배당 무배당 재조회 건너뛰기, 4분기 재계산 5칸. 흩어진 미완료를 [docs/OPEN_ITEMS.md](docs/OPEN_ITEMS.md)로 통합(사용자 결정 필요 A·남은 작업 C·해결됐는데 남아 있던 E), CLAUDE.md·hermes.md 낡은 항목 표시.
2026-10-06(Claude Stock_Strategy v12 재점검) 매수 기회일의 62~86%에서 조건 통과 종목(중앙 4~7)이 빈 자리(중앙 1~2)보다 많고, 점수(RS) 순은 무작위 순서 12개 대비 8~67 백분위로 선별력 없음. v12에 확정 기업행위 포지션 재기준 추가(무상증자 가짜 손실 제거, S28 — 일반 엔진 등 13개 전략은 미적용). 개선 가설 H1~H6·평가 기준은 docs/Stock_Strategy.md §9.
2026-10-06 오후(Claude) OPEN_ITEMS C 처리: 시가·고가·저가 정정, 제품별 매출 2021~25 재파싱(`scripts/review/reparse_product_mix_20261006.py`), 미분류 필드 3자 분류(`classify_unclassified_fields_20261006.py`), 외부 확인 재작성값 매일 자동 반영, 퀀트지표 외국인 보유 키움 대체, 조합 백테스트 6기간(우위 없음 — docs/SIGNAL_RULES.md §5).
2026-10-06(Claude) docs/REVIEW_PLAN_20261006.md §3 문서 정리 반영(정정 표시·상태 통일·OPEN_ITEMS 링크·§9 가격 한계 1행). W1~W8 실행은 D12 등 사용자 결정 대기.
2026-10-06(Claude Stock_Strategy W1, D12 ② 승인) 조정 가격 공용 로더 `backtest_common.load_adjusted_prices` 신설(확정 계수로 후진 조정, 계수 미확정 단절은 breaks·excluded_ranges로 반환). 아직 어떤 엔진도 사용하지 않음(W2~W3 대기). 테스트 tests/test_adjusted_prices_20261006.py.
2026-10-06(Claude Stock_Strategy W1 보강) 조정 가격 로더 단절 기준을 가격제한폭 초과(거래정지 재개 제외)로 바꾸고 quarantined_basis 제외, 청산용 last_day_before_break·run 지문(계수·단절 감사 버전) 추가. 2015년 이후 단절 종목 841·제외 종목-연 1,524. W2는 아직.
2026-10-07(Claude Stock_Strategy W2) 공용 일반 엔진(_run_generic_backtest)에 조정 가격 옵션(adjusted_prices, 기본 꺼짐) 통합 — 일반 엔진 7개 전략 6구간에서 기준선과 거래 목록 동일(영향 0), 단절 전 청산·제외 구간 로직은 자체 루프 전략(W3)에서 실제 시험 필요. 공백≥60일 단절·내용 해시 지문(§7-2 A·B). 상세 docs/Stock_Strategy.md §9.
2026-10-07(Claude Stock_Strategy W2 최종) 일반 엔진 8개 전략 6구간 기존 vs 조정 비교 완료(미래 정보 편향 제거+기업행위 조정 효과, 우열 판단 금지, 생존편향 별도). 48쌍 중 27구간에서 거래 목록이 달라짐(무효 쌍 9 포함), 구간별 최대 ±수십pp. 상세 docs/Stock_Strategy.md §9.
2026-10-07(Claude) REVIEW_PLAN_20261006 §13 결정 적용·§14 잔여 처리: Stock_Strategy §8-1·OPEN_ITEMS A에 결정 기록, A5 10/2 KRX 값 감사 제외, A6 규칙(FINANCIAL §2-8, 재표기는 CFS 전용 조회 77곳 선행), D2 퇴역 배지·D3 위험 지표 열(`strategy_governance` metrics max_drawdown_pct·return_to_mdd), D11 단위 의심 표시(`/api/dashboard/revenue-mix` unit_suspect), C8 원인 분리(코드 차이). 프런트 배포는 다른 세션 미커밋 정리 뒤(A9), N1은 Lab 세션 W2.5.
2026-10-07(Claude Stock_Strategy W3-1) v12 조정 가격 이관 비교(6구간×{기준선,조정}×{점수 순,무작위 12}): 077500 인적분할 −64% 가짜 손실→공시 후 단절 전 청산 +6.4%, 196170 무상증자 보유는 재기준과 동일 결과, 052710은 이동평균 변화로 청산 시점 달라짐(원인 미검증). 마지막 두 구간은 데이터 지문 불일치로 비교 무효에 가까움.
2026-10-07(Claude 원주가 잠정 처리) 당일 KIS 값은 장 마감 뒤에도 바뀌는 잠정값(KRX 시간외 단일가 반영 추정) — 신규 `price_provisional_rows`(당일 행 표시: 게이트 + DB 트리거 `price_history_mark_provisional`), `scripts/ops/check_price_vs_krx_daily.py`가 KRX 공식값 수신 시 교체·표시 해제·오래 남은 표시 알림. 원인 관측 launchd `com.stock-dashboard.kis-close-probe`(10-07~08만, 이후 해제). 상세 docs/FINANCIAL_STATEMENTS.md §5 실패 27, REVIEW_PLAN_20261006 §17~19.
2026-10-07(Claude Stock_Strategy W3-2) v12 마지막 두 구간 재실행(유효 짝 11/13·8/13)·golden_cross 조정 이관 6구간 비교(유효 5/6, 보유 중 단절 0건, 인공물 필터 제거 효과 포함). 우열 판단 금지, 196170 2020 거래 사라진 원인 미조사. docs/Stock_Strategy.md §9.
