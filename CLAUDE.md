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
| foreign_holding_daily | ⚠️ 2026-06-08 이후 정지 | 107,764행 존재하나 MAX(bas_dt)=20260608(2026-09-24 실측). 외국인 지분율 최신값은 `kiwoom_foreign_flow`(9/23까지 정상) 사용 |
| **crontab 잡 전체** | ❌ 2026-08-29경~ 중단 | macOS TCC가 cron의 외장 볼륨 쓰기를 거부(`/var/mail/brainlee`: `Operation not permitted`) → quant_indicators·HS daily_refresh·telegram·ETF 재시도·cron_3am 등 미실행. 사용자 조치: 전체 디스크 접근 권한에 `/usr/sbin/cron` 추가 또는 LaunchAgent 이전. 상세 [docs/SYSTEM_REVIEW_20260924.md](docs/SYSTEM_REVIEW_20260924.md) |
| **stock_universe 스냅샷 중복** | ⚠️ 구조적 | (stock_code, base_date) 스냅샷이라 종목당 2~3행. `WHERE stock_code=?`/JOIN 시 중복·임의 행. 신규 코드는 `signal_engine._load_latest_universe_rows()` 패턴(최신 base_date + 섹터 상속) 사용. `market_cap`(억원)은 3월 값 유지로 stale |

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
| **crontab 전 잡 실행 불가(macOS TCC)** | ✅ 해결(2026-09-24, 사용자가 `/usr/sbin/cron`에 전체 디스크 접근 권한 부여) | 2026-08-28~09-24 모든 cron 잡이 `Operation not permitted`(외장볼륨 쓰기 거부, `/var/mail/brainlee`)로 실행 안 됨. 권한 부여 후 테스트 잡으로 쓰기 성공 확인, 퀀트지표 weekly/monthly는 수동 캐치업. **재발 시 점검**: `/var/mail/brainlee` 의 `Operation not permitted`, 로그 파일 mtime. macOS 업데이트 후 권한이 초기화될 수 있음 |
| **가상매매 공통 하드스탑 미실행(2026-07-23~09-24)** | ✅ 수정(2026-09-24) | `_auto_hardstop_all_strategies`(전 전략 -10% 손절)가 V18 추천 빌드 안에서만 호출됐는데 V14/V18 장중 루프가 7/23 삭제되며 스케줄러에서 한 번도 안 돌았음(value -24~-45% 방치·HDC -10.5% 미청산 확인). 전용 루프 `가상매매공통손절`(장중 5분) 신설 + 본전스톱 추가. **재발방지**: 안전장치 함수는 다른 기능의 부산물 호출에 얹지 말고 독립 스케줄 잡으로 둘 것 |
| **서버 재시작 직후 catch-up 잡이 `price_history` AccessExclusiveLock 장시간 점유** | ⚠️ 주의(2026-09-24 확인) | 재시작 시 `audit_price_jumps_and_build_canonical.py`(가격점프감사재빌드)가 `price_history_quality_v` 뷰 재빌드 트랜잭션으로 수 분간 price_history를 잠가 API·백테스트가 statement timeout. 재시작 직후 몇 분간 타임아웃/느림은 이것. 근본 개선안: DDL(뷰 재생성)을 별도 짧은 트랜잭션으로 분리하거나 야간(00:15)에만 실행 |
| **corporate_action_events 정정·분류 후속(무상증자 날짜/액면분할/유상증자 가격조정)** | ✅ 해결(2026-09-25, 일부 review_required 잔존) | ① 무상증자 138건 event_date를 배정기준일→권리락일로 정정(DART 대조, 백업 `corporate_action_events_backup_20260924`), 수집기도 권리락일 저장. ② company_split 153건 분해: 단순 액면분할 71건(56+15)을 stock_split/stock_merge_or_reduction으로 확정(백업 `..._backup_20260924_split`), CR홀딩스·삼양홀딩스·만호제강 계수 확정. ③ **권리락 없는 유상증자(제3자배정 614·일반공모 23 = 637건)는 가격 조정에서 제외**: 테이블 `corporate_action_no_price_effect`(이벤트 행 불변, 비우면 원복)를 백테스트 로더(`backtest_common._load_corp_action_factors`)·조정가 뷰(`stock_price_daily_adjusted_v`, build 스크립트)가 참조, 수집기가 신규 유상증자를 자동 분류·등재. 근거: 이벤트 ±1일 가격단절 9.4% ≈ 무작위 7.4%, 계수 평균 0.839로 진입가 보정 왜곡이 컸음 — 제외 후 turnaround 6기간 평균 +25.8%→+4.1%(최신 +101.6%→-3.2%). **미해결(review_required)**: 진짜 회사분할 66건, 주식수 불변·날짜 어긋난 가짜 이벤트 10건, 가격불일치 액면분할 4건, 확정 이벤트 중복쌍 88건(읽기 시점 병합에 의존), 유상증자 분류 미상 ~150건(조정 유지). 상세 `docs/handoff_corp_action_event_date_offset_20260924.md` |
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

### 2026-09-24 소스/운영 저장소 통합 1~2단계 (runtime을 단일 기준으로)
- 발견: `/Volumes/Realtek_NVME/stock_dashboard`(소스, 63커밋)와 `runtime/`(운영, 별도 git 저장소)은 **히스토리가 무관한 두 저장소**(같은 GitHub 원격). 코드는 runtime이 훨씬 앞서 있음(main.py 7,366 vs 5,549줄). 앞으로 코드 수정은 **runtime 한 곳**에서만 한다. - 1단계: runtime 미커밋 623건을 5개 논리 커밋으로 보호(`3c3a893`~`b05d613`, push 안 함, `data/`·`data_cache/`·`.verification/`·`hs_trade_lab/data/` 및 런타임 상태 json은 제외). - 2단계: 소스에만 있던 tracked 파일 80개를 덮어쓰기 없이 이식(`73965d5`) — `routes/peer_… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md '2026-09-24 소스/운영 저장소 통합 1~2단계 (runti')

### 2026-09-24 문서 전면 최신화·최적화 (토큰 절약)
- CLAUDE.md 243KB→~85KB: 섹션 1(구조)·2(테이블 행수 407개 기준)·6(줄번호) 재생성, 섹션 3(API)·4(스케줄러)는 자동 생성 정본(`docs/API_ENDPOINTS.md`/`SCHEDULER_JOBS.md`/`DB_TABLES_PG.md`, `scripts/ops/gen_*_doc.py`)으로 대체, 해결된 이슈 54건·이전 변경이력은 `docs/CLAUDE_KNOWN_ISSUES_RESOLVED.md`/`CLAUDE_CHANGELOG_ARCHIVE.md`로 이관(원본 백업 `backups/CLAUDE.runtime_before_optimize_20260924.md`). - hermes.md 138→63KB, hermes_change.md 50→12KB(9/23 이전 → `docs/hermes_*archive*`). PROJECT_MASTER 등 3~5월 SQ… (전문: docs/CLAUDE_CHANGELOG_ARCHIVE.md)


### 2026-09-24 2차 점검 (Claude) — 상세 docs/SYSTEM_REVIEW_20260924.md §D
- **PG 이관 시 컬럼 DEFAULT 366개(172테이블) 유실** → 생략 INSERT가 NULL 저장(실적신호 8월 이후 1,629건이 화면·알림에서 전부 누락 등). 360개 `SET DEFAULT` 복구(price_history 수급 숫자 컬럼은 NULL=결측 유지로 제외), 플래그 NULL 백필(`restore_null_defaults_20260924_175758`). 신규 테이블/컬럼은 PG DDL에 DEFAULT를 직접 명시할 것.
- 실적 트리거 TTM(CFS+OFS 이중합산·흑자전환 2년전 비교) 수정 후 기존 신호 1,029건 비활성. tenbagger/screener/signal_engine/routes/tenbagger의 CFS/OFS·연간(quarter 0/4) 혼재 QoQ/YoY 수정, D&A는 `depreciation_q`. `treasury_buyback` 7/10 정지 → `scripts/ops/sync_treasury_buyback_from_dart.py`(공시 갱신 시 자동) + 정규 분류 `event_class`. 실적신호 API 10s→0.08s.


> 📦 2026-09-27: 이 아래 최근 12개 항목만 유지 — 이전 항목(45개 + 관련 소제목/색인)은 [docs/CLAUDE_CHANGELOG_ARCHIVE.md](docs/CLAUDE_CHANGELOG_ARCHIVE.md) 맨 아래 「CLAUDE.md에서 이동」 절. (`scripts/ops/trim_claude_changelog.py`)

2026-09-26(Claude) 게이트를 다중 사용자 사이트에 맞게 재설계: public(일반 조회·검색 분석) / viewer(계좌현황 — 서버가 검증하는 비밀번호 쿠키 `pf_view`, `POST /api/portfolio-access/login`, 비밀번호는 `.env` `PORTFOLIO_VIEW_PASSWORD`로 이동) / owner(쓰기·다운로드·내보내기·설정 — API 토큰 또는 관리자 이메일 Access JWT). IP당 분당 300회 조회 제한. 일반 사용자 쓰기 허용 목록 `API_PUBLIC_WRITE_PATTERNS`. Cloudflare Access 앱은 관리자 이메일만 허용해 친구 접근을 막으므로 제거/조정 필요(사용자 결정). 상세 HANDOFF §16-5.

2026-09-26(Claude) `v_gc` 신규 진입 shadow 추가(`VT_SHADOW_STRATEGIES=momentum,peak,v_gc`, 사용자 승인). CEO 플랫폼(api.newsinfo.cloud)의 관리자 확인이 쿼리 `role=admin`(클라이언트 입력)뿐임을 발견 — Access 재설정 필요, 근본 수정은 CEO 백엔드 세션 과제(HANDOFF §16-6).

- **2026-09-26 (32차)**: 2026 분기 미결 플래그 원문 대조. OpenDART 재조회(P&L 2,903·BS 2,898키, /tmp/fq_pl_dart.jsonl·fq_open_dart.jsonl) 후 `reconfirm_open_flags_from_live_20260926.py --apply`로 현재 값이 원문과 0.3% 이내인 플래그를 CONFIRMED 전환 → QUARTERLY_4WAY OPEN 17,010→4,270, CONFIRMED 52,840, AMBIGUOUS 15. 2026Q1 P&L 31건 정정(run_id `cfs_old_quarterly_fix_20260925_104333`) 중 금융업종 7건과, 앞선 1,706건 정정(`..._092348`) 중 금융업종(은행·보험·증권·금융지주) 30건은 원문 매출/영업이익 계정이 업종 정의와 달라 **되돌림**(financial_fix_log `..._revert` run_id). 2026Q2는 원문 3개월/누적 표기가 회사별로 달라(예: 138040 원문 2.5조 vs DB 1.1조) 기존 `dart_q2_verified` 행과 충돌하는 91건을 적용하지 않음. 자본총계 불일치 71건(총자본 vs 지배귀속 등 정의 차이 추정)도 미적용. 자산총계는 불일치 0.

- **2026-09-26 (33차)**: 사용자 결정 "FnGuide 기준으로 진행, DART와 다르면 표시"로 2026 1·2분기 정리. (1) **FnGuide 2026Q2가 수집된 적 없던 원인**: `collectors/fnguide_financial_collector.py`가 종목마다 3초 간격 HTTP 대기 동안 PG 트랜잭션을 열어둬 idle-in-transaction 타임아웃으로 연결이 끊김("the connection is closed", 저장 0건) → HTTP 호출 직전 `conn.commit()` 추가로 수정(스케줄러 월간 잡도 동일 경로). FnGuide 쿼터(`api_rate_limiter` FNGUIDE 일 1,500건, 종목당 CFS 6회)는 그대로 두었고, 분기 P&L만 종목당 4회로 받는 재개형 수집기 `scripts/collect_fnguide_quarterly_snapshot_20260926.py`(financial_source_snapshot에만 저장, financial_data 미변경)를 띄움 — 하루 약 250~300종목이라 전 종목은 수일 소요. FnGuide wcomp는 분기 재무상태표를 주지 않음(P&L만). (2) **네이버 재무(FnGuide 계열, `NAVER/FINSTATE-Q`, 억원)로 즉시 대조**: `scripts/apply_fnguide_basis_from_naver_20260926.py [1|2] --apply` — 값이 반올림 오차(1억 또는 0.5%) 안에서 같으면 DB(정밀한 DART 값) 유지, 다르면 FnGuide 값(순이익·자본은 지배 기준)으로 덮어쓰고 이전 값은 `financial_fix_log`(run_id `fnguide_basis_naver_2026q{1,2}_*`)와 `fnguide_dart_mismatch_log`('FnGuide기준 채택 … DART/기존=… FnG=…')에 표시. 네이버가 0으로 준 항목은 미공시로 간주해 제외. Q2: 일치 12,062·덮어쓰기 369·NULL 채움 82·행 신규 125, Q1: 일치 9,759·덮어쓰기 219·NULL 채움 2,668(주로 자산·자본)·행 신규 104. 백업 `financial_data_backup_2026q_fnguide_q2_20260926`(2025+ 전체). (3) 2Q 행이 없던 종목 중 DART 반기보고서가 있는 10종목은 DART로 채움(`data_source='dart_q2_missing_fill_20260926'`, run_id `q2_missing_dart_fill_20260926`). 남은 무행 약 25종목은 스팩·상장폐지·거래정지(금양 등)·신규상장·해외법인으로 DART에도 반기 자료 없음. (4) 결과: 2026Q2 주요 행 결측(CFS 2,182행 기준) 매출 4·영업이익 2·순이익 2·자산 3·자본 3. (5) 이번 세션 앞선 원문 대조 정정 중 금융업종 37건은 되돌렸으나(32차), FnGuide 기준 채택은 금융업종의 영업수익 정의를 그대로 따름.

- **2026-09-26 (34차, 진행 중)**: 사용자 결정 "FnGuide 기준 적용 + 모든 데이터를 같은 기준으로 정리". **기준 정의**: 매출·영업이익(발표기준)·총자산 = FnGuide 값, **순이익·자본총계 = 지배주주 귀속**(전체 아님), 현금흐름 = FnGuide 영업/투자/재무활동·CAPEX(양수). 값이 반올림(1억 또는 0.5%) 안에서 같으면 DB의 정밀값 유지, 다르면 FnGuide 값으로 교체하고 이전 값은 `financial_fix_log`(+`fnguide_dart_mismatch_log`)에 표시. (1) 분기 2025Q2~2026Q2: `scripts/apply_fnguide_basis_from_naver_20260926.py all --apply`(네이버 `NAVER/FINSTATE-Q`, Q4는 NULL 채움만 — Q4=연간−Q1−Q2−Q3 규칙 유지). 순이익·자본은 지배 기준으로 전환(약 4,900필드). (2) 연간 2021~2025: `scripts/apply_fnguide_basis_annual_20260926.py --apply`(네이버 `NAVER/FINSTATE`, 12월 결산만; 덮어쓰기 18,003·NULL 채움 3,888·행 신규 507, data_lock 해제→갱신→재잠금+해시 재계산). 2021년 연간 결측이 매출 512→22, 자산 807→32로 감소. 백업 `financial_data_backup_annual_fnguide_basis_20260926`(연간 2016~2025), `financial_data_backup_2026q_fnguide_q2_20260926`(2025+). (3) 현금흐름 연간 2021~2025: `scripts/apply_fnguide_basis_cashflow_annual_20260926.py --apply`(덮어쓰기 4,987[capex 3,840]·NULL 채움 780·행 신규 906). 백업 `cash_flow_data_backup_fnguide_basis_20260926`. (4) 연간 2016~2020(네이버 미제공)은 OpenDART의 지배귀속 계정(`ProfitLossAttributableToOwnersOfParent`, `EquityAttributableToOwnersOfParent`)으로 전환 예정: 수집 `dart_a_full_fetch.py`(→/tmp/dart_a_full.jsonl), 적용 `scripts/apply_dart_parent_basis_annual_20260926.py`·`apply_fnguide_basis_cashflow_annual_20260926.py --dart`. 분기 2016~2025Q1의 지배 순이익·자본은 `dart_q_full_fetch.py`(→/tmp/dart_q_full.jsonl, DART 일일 쿼터 소진 시 다음 날 재개)로 수집 후 적용, 그 뒤 Q4 재계산 필요.
  - 34차 결과(13:30 기준): 연간 2016~2020 OpenDART 지배귀속 전환(NI·자본 6,311필드, NULL 채움 391, run_id `dart_parent_basis_annual_20260926_132915`)·현금흐름 DART 전환(1,452필드). 2021~2025는 네이버(FnGuide)가 최종 기준(2021은 DART 전환 후 네이버 재적용으로 되돌아감 — 두 출처의 지배 값이 0.5% 넘게 다른 종목 약 330필드). 미완료: 분기 2016~2025Q1의 지배 순이익·자본·자산 결측(분기 자산·자본 2023Q3~2025Q1 각 600~900건, 순이익 100~450건)은 `/tmp/dart_q_full.jsonl`(키 55,577, 백그라운드 `dart_q_full_fetch.py`, 재개형)로 수집 중 → 적용 스크립트 작성·실행 → Q4 재계산 → 검증기(`fin_quarterly_4way_validate.py` 등) 재실행 순서. 분기 현금흐름(3개월/누적)은 미착수.

- **2026-09-26 (Codex 선택 전략 재감사)**: `holding_windows()`가 BUY 이벤트+완결 행 혼합 원장을 이중 보유로 계산하던 결함을 수정(기간말 phantom 창 제거). KRX KIND 증거로 126340·107640의 KONEX 이전상장 구간을 `security_master_history`에 복구하고 재빌드 지속 예외를 추가(백업 `security_master_history_backup_codex_20260926_konex`); SPAC 전신인 127980·162300·380540 과거구간은 차단 유지. 선택 전략 감사 **23/4→26/1**, 유일 실패 `low_base_breakout`(SPAC identity survivorship 5). v4는 최신 suite `3a1df776883808d8` 기준 PIT verified이나 성과 avg6 +10.24%·4/6 양수, governance `retired`, 독립 forward 원장 없음 — 화면의 낡은 +19.46% 설명 정정. 상세 `hermes.md` 13:35 절.

- **2026-09-26 (Codex 복합전략 600%대 무효화·재검증 게이트)**: 전략센터 646.43% `cmb_da1d39936923`은 300720 오염가격 청산 1건(+1,451%, 총손익 21.6%)에 의존해 무효이며 해당 종목 제외 재시뮬레이션은 371.2%였다. 원천 composite/병합 run에 `result_validity=false`를 기록하고 이 아티팩트를 `derive_status()`·신규 병합 등록 게이트에 연결했다. 조합 API는 무효 run을 제외하고 재무 스냅샷 이후 `financial_data` 변경 또는 `cfs_ofs_mixed_ttm` 실패 시 `revalidation_required`를 반환한다. 화면과 라이브 콤보 라벨의 552~585% 고정 주장을 제거해 재검증 완료 전 숫자를 숨긴다. 현재 34차 DART 분기 수집 3프로세스 실행 중, 계약 fail 3,022/8,121이므로 TTM PER·Alphalens·병합전략 재실행은 34차 적용/Q4 재계산 완료 뒤 수행한다. 상세 `hermes.md` 14시대 절.

- **2026-09-26 (Codex 한국 주식 다중 분류 개편)**: 평면 `sector` 분류를 대체할 출처·스냅샷·근거 보존형 `sector_taxonomy_nodes`/`stock_sector_membership_v2`/`sector_taxonomy_source_runs`를 신설했다. StockEasy 최신 공개 API(2,553종목), Kiwoom 공식 업종·테마 구성 API, 기존 기준선과 DART 제품 매출 기반 내부 밸류체인/공정/소재/경쟁군을 분리 적재해 일반주식 2,793종목 100% 커버. 제이엠티·디케이티·한국컴퓨터는 `전자부품→EMS·모듈 조립→PBA·FPBA·모듈 조립→OLED·스마트기기 PBA/FPBA 제조사` 검증 경쟁군으로 연결했다. `/api/sector-taxonomy/*`, 프론트 `종목 다중분류`, 매일 20:10 `종목다중분류` 잡 추가. 깨진 StockEasy HTML 파서는 현재 JSON API+2,000종목 fail-closed로 교체. 전체 551 passed/54 subtests, 프론트 build, 운영 API 200, PID 9818→21069. 기준·소스·운영 규칙은 `docs/sector_taxonomy_20260926.md`, 상세 결과는 `hermes.md` 14:10 절.
  - 34차 완료(15:20): DART 분기 원문 55,414키 수집(`dart_q_full*.jsonl`, 3샤드 병렬, 한도 미도달) → `apply_dart_parent_basis_quarterly_20260926.py --apply`(2016~2025Q1: 순이익·자본 지배주주 기준 전환 34,243필드, NULL 채움 10,993; 금융업종은 매출·영업이익 채움 제외, run_id `dart_parent_basis_quarterly_20260926_145929`) → `rederive_q4_fnguide_basis_20260926.py --apply`(Q4=연간−Q1−Q2−Q3, 재무상태표는 연간값; 19,714 종목-연도, 흐름 22,452·BS 8,524필드, run_id `q4_fnguide_basis_20260926_150000`). 단 Q4 매출이 음수가 된 257건(연간<Q1~Q3 합: 입력 불일치)은 이전 값으로 되돌림(`..._revert`) — 연간/분기 입력 출처 검토 필요 목록. → 4분기 검증(QUARTERLY_4WAY) 재실행: CONFIRMED 54,697·CLOSE_MATCH 221,011·STRUCTURAL 75,775·SELF_CONSISTENT 80,903·OPEN 4,043·AMBIGUOUS 71. → 분기 현금흐름 `apply_cf_quarterly_dart_20260926.py --apply`(YTD=DART, *_q=YTD 차분, Q4=연간; 변경 29,767·채움 10,167, run_id `cf_quarterly_dart_20260926_151905`). 이상 행: `cash_flow_data`에 미래 기간 행 2개(001080 2026Q3, 2026 Q4 연간; 8/9 dart_api_unified) — 삭제 여부 사용자 확인 대기. 기준 잔여 결측(상장 보통주, 최선 행 기준): 순이익 분기 2016~2024 연 25~130건대, Q4 2022~2024 매출·영업이익·순이익 120~380건, 2024Q1~Q3·2025Q1 자산·자본 77~257건.

- **2026-09-26 (35차, 진행 중)**: 잔여 결측 원인 분류(상장 보통주, 2016~2025): ① DART에 값이 있는데 미적용(주로 CFS 행이 비어 있고 DART는 OFS만 존재) ② `ifrs-full_ProfitLoss` 등 계정 ID 부재(계정명 폴백 필요) ③ 조회 범위 밖(2022 연간·2025Q2~Q3) ④ DART 자료 없음(no_corp/no_data) ⑤ Q4 산출 입력(연간·Q1~Q3) 누락. 대응: `dart_null_fill_fetch.py`(계정 ID+계정명 폴백, 3,660키 → `/tmp/dart_null_fill.jsonl`, KEY1·2 소진·KEY3만 사용, 한도 소진 시 중단 후 재개) → `scripts/apply_dart_null_fill_20260926.py --apply`(NULL만 채움, 순이익·자본은 지배 기준, OFS만 존재하는 종목의 CFS 라벨 행도 채움) → Q4 재계산(`rederive_q4_fnguide_basis_20260926.py`, 음수 매출 산출은 건너뜀) → 검증기 재실행이 `/tmp/chain3_0926.sh`로 자동 연결됨(결과 `/tmp/chain3_0926.log`). 2026년 4분기 값은 생성된 적 없음(Q4 재계산은 2025년까지). 이상 행: `financial_data` 2026 연간 12행(전 필드 NULL, FnGuide 추정 컬럼에서 생성), `cash_flow_data` 2026Q3 1행·2026 연간 1행 — 삭제 여부 사용자 확인 대기.

- **2026-09-26 (Claude Minervini SEPA+VCP 백테스트 + US avail_date)**: Minervini SEPA+VCP 전략 백테스트 5종 비교(2021~2025): ① Trend Template 기준선 +57.52%/MDD -42.5% ② SEPA 엄격(EPS≥20%) +9.34%/MDD -8.9% ③ SEPA+VCP 엄격 -1.08%(신호 4건) ④ SEPA 완화(EPS≥15%) -10.15%(역효과) ⑤ SEPA완화+VCP완화 -1.54%(신호 7건). 결론: SEPA 기준 완화 역효과, VCP 한국 시장 희소. 미국 DB(`us_financial_data`) avail_date 컬럼 추가: quarter=period_end+45일, annual=period_end+90일(SEC 10-Q/10-K 기한), 78,630행 전체 계산, 인덱스 `idx_us_fin_ticker_avail` 생성. 전략 파일 `backtest_strategies/minervini_sepa_vcp.py` 신규. — (개선 16:13~16:49) `scripts/fetch_sec_filing_dates_20260926.py` 정교화: acceptanceDateTime(SEC 수리 시각) 활용해 장 마감(16:00 ET/20:00 UTC) 이후 공시 → 다음 거래일 반영, 매핑 허용 오차 ±45일→±60일. 3,383개 ticker 전체 실행·완료: **55,855행** avail_date SEC 실제값으로 갱신(0행 NULL), 평균 지연 이전 추정 +45일 → **실제 42일(중앙값)** (AAPL +31~34일, AMZN +27~38일). VCP 파라미터 catchitearly/vcp 방법론으로 업그레이드: 거래량 건조 5d/45d×60%→10d/50d×70%, ATR 수축 신규 추가(10d ATR < 50d ATR × 65%).

- **2026-09-26 (36차)**: (1) **비금융 복원**(사용자 결정: FnGuide 기준은 금융업만, DART와 다르면 표시): `scripts/restore_nonfinancial_fnguide_basis_20260926.py --apply` — 로그(`fnguide_basis_*`·`dart_parent_basis_*`·`q4_fnguide_basis_*`·`fnguide_basis_cf_annual_*`)의 최초 old_value로 비금융 98,065필드 복원(순이익 43,002·자본 38,564·영업이익 4,382·매출 4,070·자산 1,980·현금흐름 6,067; 충돌 0), run_id `restore_nonfinancial_20260926_153520`. 빈 값 채움 23,614은 유지. `fnguide_dart_mismatch_log` 비금융 항목은 '[복원: 비금융 원복]' 표기. 금융업 137종목은 FnGuide 기준 유지. 근거: 이전 값 vs FnGuide 값을 DART 원문과 대조한 표본(원문 있는 442건)에서 비금융은 이전 값이 맞은 경우 354·FnGuide가 맞은 경우 64. (2) **밤사이 덮어쓰기 차단**: 스케줄러 `_job_fnguide_financial_monthly`(매일 05:00 백로그 모드, `--override` + `scripts/fnguide_integrity_sync.py --all`)가 FnGuide 값으로 전 종목을 덮어쓰는 구조 — 이번에 수집기의 트랜잭션 버그를 고쳐서 실제로 저장되기 시작하므로, `collectors/fnguide_financial_collector.py`(override·Q4 재계산은 금융업만)와 `scripts/fnguide_integrity_sync.py`(덮어쓰기 루프에서 비금융 건너뜀)를 수정. (3) **DART 다중회사 API(`fnlttMultiAcnt`) 검증**: 100개 회사/호출, CFS·OFS·당기/전기/전전기 반환, 호출 513회로 전 종목 주요계정(매출·영업이익·당기순이익(전체)·자산·자본) 수집 완료(`/tmp/dart_multi.jsonl`, 스크래치 `dart_multi_fetch.py`). 현금흐름·지배귀속 순이익·자본은 제공하지 않음. 읽기 전용 감사 `scripts/audit_db_vs_dart_multi_20260926.py`(→`/tmp/audit_multi_mismatch.json`): 비금융 대조 가능 셀 약 619k 중 일치 576,465(93.1%)·불일치 40,279·DB 빈칸인데 DART 값 있음 2,216·DART 값 없음 179,435(분기 BS 격년 등 커버리지 밖 포함); 불일치 필드: 순이익 20,679(대부분 지배 vs 전체 정의 추정)·자본 6,619·영업이익 7,018·매출 5,194·자산 2,240. 매출·영업이익·자산 불일치 유형: 5~30% 차이 약 8,700, 그 외 큰 차이 약 4,900, 누적/단위 의심 약 450, 출처는 dart 계열 9,600·fnguide 3,700. 아직 정정하지 않음(분류 후 적용 예정).
  - 36차 정정 실행(16:12): `scripts/apply_dart_multi_truth_20260926.py --apply` — 비금융 종목의 매출·영업이익·순이익(전체)·자산·자본(전체)을 OpenDART 다중회사 API 값으로 정정(같은 재무제표 종류끼리, 연간·Q1~Q3, 2016~2025; 모호한 키[서로 다른 값·Q2/Q3 누적만 공시] 제외): 정정 40,121·빈 값 채움 2,208(run_id `dart_multi_truth_20260926_155241`, 백업 `financial_data_backup_dart_multi_truth_20260926`). 재감사: 비금융 일치 618,794(불일치 158·DB빈칸 8·DART값 없음 179,435). 이어서 Q4 재계산(`q4_fnguide_basis_20260926_155313`: 손익 21,288·BS 3,188필드, 음수 매출 산출 370건 건너뜀) 및 검증기 재실행: CONFIRMED 55,191·CLOSE_MATCH 221,894·STRUCTURAL 74,944·SELF_CONSISTENT 80,506·OPEN 4,156·AMBIGUOUS 39. 잔여 결측(상장 보통주 종목-기간 104,013행 기준): 매출 1,741·영업이익 1,201·순이익 2,006·자산 1,012·자본 1,068(Q4 행 20,927 중 매출 1,061·영업이익 1,006·순이익 1,164). 금융업 137종목은 FnGuide 기준 유지(DART와 다른 값은 mismatch 로그 표시; 감사에서 금융업 불일치 1,471은 정의 차이로 미정정). 미해결: 현금흐름은 다중회사 API 미제공이라 위 대조 대상 아님.
- **2026-09-26 (37차)**: (1) 미래 기간 이상 행 삭제(사용자 승인 "불필요하면 삭제"): `cash_flow_data` 2행(001080 2026Q3, 001720 2026 연간; 8/9 dart_api_unified)과 `financial_data` 2026 연간 12행(핵심 5필드 전부 NULL, FnGuide 추정 eps/bps만 있던 행, 소비처 확인 못 함)을 삭제. 백업 `cash_flow_data_deleted_future_rows_20260926`·`financial_data_deleted_future_rows_20260926`. (2) **현금흐름 검증 기록 확인**: 이미 Codex/Claude 세션이 상당 부분 검토함 — `cf_validation_flags` 107,236건(CONFIRMED 102,751·CLOSE_MATCH 3,566·STRUCTURAL 885·AMBIGUOUS 32·OPEN 2), `cashflow_fix_log` 82,404건(2026-05-31~09-20), 핸드오프 `scratch/HANDOFF_CF_VALIDATION_FOR_CODEX_20260518.md`·`docs/codex_handoff_financial_cashflow_4layer_2026-05-24.md`·`scratch/REPORT_CF_PHASE2_20260518.md`, 검증기 `scratch/cf_4way_validator.py`·`cf_3way_validate_and_fix.py`·`collectors/cf_triple_validator.py`. 원칙(소유자 지시): **현금흐름의 유일한 쓰기 경로는 DART API, FnGuide/네이버는 참고 전용 — FnGuide는 잘못 파싱할 수 있음**(`FG_FALLBACK` 자동 적용 금지). 이번 세션의 FnGuide/네이버→현금흐름 쓰기는 이 원칙에 어긋났음(비금융은 복원, 금융업 172필드는 FnGuide 값이 남아 있어 처리 결정 대기). 또한 이번 세션은 CLAUDE.md의 '재무/현금흐름 무결성 선행 규칙'(선행 파일 읽기, 샘플 10종목 검증, Q4 소스 혼합 시 강제 산출 금지 등)을 사전에 확인하지 않고 작업을 시작함. 오늘 DART 기준으로 바꾼 현금흐름(연간 2016~2021 1,827필드 재적용 + 분기 YTD 29,767정정·10,167채움, `cf_quarterly_dart_20260926_151905`)은 옛 검증 플래그(dart_value가 억원 단위 등 단위 불일치 사례 포함)와 재대조·`cf_validation_flags` 갱신이 필요.
  - 37차 추가(16:45): 소유자 원칙(현금흐름 쓰기는 DART 전용)에 따라 이번 세션의 FnGuide/네이버→`cash_flow_data` 쓰기를 전부 되돌림: `scripts/revert_cf_naver_writes_20260926.py --apply` — 값 복원 105(금융업 덮어쓰기)·NULL 복원 780(채움분)·삽입 행 906 삭제(백업 `cash_flow_data_deleted_fnguide_naver_20260926`), run_id `revert_cf_naver_20260926_164449`. DART 기반 현금흐름 정정(연간 2016~2021·분기 YTD)은 유지. 남은 재확인: 오늘 DART로 바꾼 현금흐름과 `cf_validation_flags` 재대조·갱신, 내일 05:00 스케줄러 이후 복원 유지 여부, `financial_data`의 `data_source='fnguide_naver'` 삽입 행 처리 결정.

2026-09-27(Claude 사이트 전면 개편) Stock Hub 허브(`/`)+`/info`·`/lab`·`/admin` 라우팅, 관리자 비밀번호 로그인(`sd_admin`), Stock LLM `/llm` 프록시(관리자 전용), 전 화면 라이트 테마(코드모드 pass1~4), 관리자 시스템 현황(`system_map.py`, 자동 갱신)·`docs/SYSTEM_MAP.md`, CLAUDE.md 147→~97KB. 배포: 백엔드 재시작 + `frontend` 빌드 교체. 상세 §0.
2026-09-27(Claude) newsinfo 서버(:5500 정적/:8011 API)를 launchd(`com.ceo-briefing.frontend`/`backend`, KeepAlive)로 등록 — 재부팅 후 502 재발 방지. 관리자 비밀번호 브라우저 초기 설정 추가.
2026-09-27(Claude) 구분선 가시성 강화(사용자 요청): `--line` #cdd4e1·`--line-strong` #aab4c8, 표 행 1px/머리글 2px !important(`index.css` 맨 아래), 앱바·사이드바·헤더 2px, 인라인 border 알파 하한 0.2(코드모드 pass5).
2026-09-27(Claude) UI 개선: 사이드바 섹션 접이식(+/−, 현재 섹션 자동 펼침, localStorage `sd_nav_open_<모듈>`), 표 색상(머리글 #dbe6f7·첫 열 #eaf0fa·짝수행 #f1f4fa·호버 #e3ecfb, `index.css` 맨 아래 `#main-scroll table` 규칙), 글자 진하게(`--text` #0b1220·`--text-muted` #334155), 남은 어두운 배경 정리(코드모드 pass6·7).
2026-09-27(Claude 2차 개편) ① Key Indicator(newsinfo.cloud) 첫 화면을 **공개**(로그인 없음) 주요 경제지표·뉴스정보로 교체(`frontend/index.html`+`keyindicator.js/css`, 백엔드 `role_gate.PUBLIC_READ` GET 화이트리스트만 role=staff로 통과). 기존 관리 콘솔은 `console.html`로 이동해 stock 관리자 › `Key Indicator 관리`(iframe, `hub/AdminKeyIndicator.jsx`)에서 연다. ② 아이디·PIN 계정 삭제(DB 백업 `backups/ceo_briefing.db.before_pin_removal_20260927`, 하드코딩 기본 PIN 제거) — newsinfo 로그인은 `backend/admin_password.py`가 stock `.env`의 ADMIN_PASSWORD_HASH로 검증(비밀번호 1개 공용). ③ 「내 투자」(매수후보·계좌현황)는 메뉴 진입부터 관리자 비밀번호(`LOCKED_TABS`), 사이드바 맨 아래로 이동, 별도 `PORTFOLIO_VIEW_PASSWORD`·`pf_view` 폐지(security_gate viewer=관리자 전용, `/api/buy-candidates` 포함). ④ 배포 시 옛 해시 파일 보존(`scripts/deploy_frontend.sh`)+오래된 탭 자동 새로고침(`main.jsx`) — 배포 직후 lazy 화면 오류 방지.
2026-09-27(Claude) 「내 투자」(매수후보+계좌현황) 잠금 재설계: 관리자 로그인과 **별개로** 비밀번호를 다시 입력해 발급하는 `sd_invest` 쿠키(30분)가 있어야 열림(`POST /api/admin-auth/invest-unlock`·`invest-status`·`invest-lock`, security_gate viewer 단계는 관리자 쿠키를 인정하지 않음, API 토큰·Access 관리자만 예외). 화면은 잠금 시 하위 메뉴 숨김·🔒/🔓 표시·「지금 잠그기」.
2026-09-27(Claude 데이터 정지 원인 3건) ① 섹터 로테이션 캐시가 9/18부터 `_leader_picks_for_sector`의 영업이익 NULL(`float(None)`)로 실패하고 `_job_sector_rotation_cache`가 예외를 삼켜 원장엔 success로 남았음 → NULL 제외 + 예외 재발생. ② 섹터 지표 상세 `updated_date`가 폐기 캐시 `radar_price_cache`(6/19)를 봄 → us_market.db `us_price_history`로 교체. ③ 주요 지표(미국10년물·환율·VIX 등)가 9/8~9/11에서 정지: 저장 마지막일이 5일 조회창보다 오래되면 겹침이 없어 `gate_price_batch`가 `historical_batch_without_overlap`으로 영구 격리 + 저장분 스냅샷 오염으로 `overlap_basis_mismatch` + `^VIX` 9/8=8.73 오염값이 급변 가드를 영구 발동 → `main._realtime_fetch_macro`: 지연 시 1개월 창, 급변 기준을 새 시계열의 직전일로, 게이트 격리 시 화이트리스트 심볼만 `macro_window_repair.replace_macro_window`(편차 한도·`price_history_fix_backup`·`data_fix_log` 기록)로 교체, 예외 후 `db.rollback()`(InFailedSqlTransaction 연쇄 차단). **공통 결함**: `db_compat.PostgresCompatConnection.close()`가 readonly 연결의 read_only 상태를 풀에 되돌려 놓아 이후 쓰기 세션이 `ReadOnlySqlTransaction`으로 간헐 실패 → 반납 전 복구. `data_fix_log_id_seq`가 max(id)보다 뒤처져 setval(122)로 보정.
2026-09-27(Claude) 「오늘의 섹터 신호」(`views/SectorSignalSummary.jsx`)를 전체 섹터 한 표(섹터·신호등·핵심 사유 한 줄, 행 클릭=섹터 로테이션 상세)로 개편. Info 메뉴 재편: 시황(주요 지표·수급 현황·종합 RS·자금 흐름·섹터 지표·섹터 로테이션·ETF 자금)/종목/퀀트지표/공시·리포트/내 투자, 라벨 이모지 자동 제거(`stripEmoji`).
2026-09-27(Claude 데이터 신선도 점검) 결과·원인·조치는 [docs/DATA_FRESHNESS_REVIEW_20260927.md](docs/DATA_FRESHNESS_REVIEW_20260927.md), 전체 목록은 `scripts/ops/audit_data_freshness.py` 산출물. **공통 결함 2건**: ① `db_compat`는 실패한 문장마다 트랜잭션 전체를 롤백 → 수집기의 `ALTER TABLE ADD COLUMN`(try/except로 무시)이 PG에서 앞서 넣은 데이터를 조용히 지움 → `translate_sqlite_sql`이 `ADD COLUMN IF NOT EXISTS`로 자동 변환. ② 수집 잡이 "N건 저장"만 로그하고 커밋 실패는 모르는 패턴 — 신규 수집기는 저장 후 DB 건수를 검증할 것. 키움 8050(IP 미등록)으로 장중 피드 정지, 텔레그램 모니터링은 비용 절감으로 의도 비활성.
2026-09-27(Claude RS 점검) RS 계산이 5가지 — [docs/RS_METHODS_REVIEW_20260927.md](docs/RS_METHODS_REVIEW_20260927.md). 종합 RS 섹터 칩의 "100"은 화면이 최저~최고로 늘린 값이라 제거(반도체 실제 12M 평균 RS 74.9).
2026-09-27(Claude) 키움 8050 재확인: 등록 IP(49.170.20.30)는 안 바뀌었고(`logs/kiwoom_ip_state.json`) 지금 직접 발급 테스트·최신 백엔드 세션의 `키움외국인지분율`(133,823행)도 정상 — 오늘 낮 실패 9,444건은 재시작 이전 구간의 일시 장애(429 폭주 직후 8050 다발)로 추정, 현재는 정상. 텔레그램 관심도 수집은 사용자 지시로 재개 보류(대안 검토 중, `telegram_monitor.py` 비활성 유지).
2026-09-27(Claude) 종합 RS 페이지에 5가지 RS 방식 병기: 서버 `routes/stock_analysis_rs.py`가 종목별 `rs_methods`{percentile, sector_rotation_4w/12w, signal_light, track_r, ibd_rs(ibd_rs_daily 조인)}를 계산해 함께 반환, 화면은 "5가지 RS 방식 비교" 체크박스로 5개 열 표시(각 열 헤더에 산식 툴팁). 산식 비교는 §RS_METHODS_REVIEW.
2026-09-27(Claude UI 마무리) ① 모바일 사이드바 높이가 하단 고정 메뉴(60px)를 빼지 않아 맨 아래 섹션(「내 투자」)이 그 뒤에 가려져 눌러도 하단 메뉴 버튼이 대신 눌리던 문제 수정(App.jsx aside height calc). ② 「섹터 지표」를 「섹터 분류」로 개명해 반도체 섹터 바로 아래로 이동(퀀트지표 섹션), 내부의 주도섹터 진입신호 표는 섹터 로테이션과 중복이라 제거(MarketRadarView.jsx) — 섹터 로테이션이 정본. ③ 국내 종목 상세 재무 지표 타일(7→8칸)에 RS(종합) 추가, 신규 경량 엔드포인트 `GET /api/stock-analysis-rs/stock/{code}`(대시보드 캐시 재사용, 재계산 없음) — 최초 구현이 StockAnalysis의 안정화 클로저(_saState.current 패턴) 밖에서 상태를 직접 참조해 절대 갱신되지 않던 버그를 `_saState.current.stockRs`로 수정. ④ Stock Lab 내부 가로 탭(전략 센터 6개·실험 로드맵 5개)을 Stock Info처럼 왼쪽 메뉴로 재배열 — StrategyHub/ExperimentRoadmapView에 initialHubTab/initialPageTab prop 추가(MarketRadarView initialSector와 동일 패턴), 탭 키는 `strategy_hub_*`/`exp_roadmap_*`. ⑤ Stock LLM을 정식 모듈로 승격(`/stock-llm`, 키도 경로와 동일하게 `stock-llm` — 라우터가 첫 URL 세그먼트를 그대로 MODULES 키로 조회하므로 `llm`처럼 다르면 허브로 튕김) — 다른 모듈과 동일한 AppBar+왼쪽 사이드바 안에서 Brian_RAG를 iframe으로 연다(`hub/StockLlmView.jsx`). iframe 내부 UI가 예상(밝은 테마)과 다른 다른 구성(어두운 테마, 검색/분류체계/주제 탭)으로 떠 있음 — 다른 세션이 Brian_RAG 프런트를 별도로 교체한 것으로 보임, 내부 테마는 이 세션 책임범위 밖이라 손대지 않음.
