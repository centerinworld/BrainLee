> **⛔ 최우선 원칙 0: DART 파싱값을 100% 신뢰하지 않는다 — DART 값은 원본 후보일 뿐, FnGuide(필수)·네이버(보조)와 연결·별도를 나눠 값 대조로 일치해야 확정. 불일치·반복 차이는 먼저 우리 파싱 오류를 의심하고 종목 특징(stock_collection_config fs_quirk:*)으로 기록. 목표 99.99%.**
>
> **필수(2026-10-03)**: 재무제표·주가·현금흐름·수주잔고·재고자산·감가상각 등 숫자 데이터는 [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md)가 유일한 정본이다. 판정·수정 전 반드시 읽고, **숫자 데이터 작업 기록은 이 파일이 아니라 그 파일 §7에** 남긴다(이 파일의 관련 절은 그 파일 부록 B로 이관됨). 이 파일은 전략·백테스트·인프라 작업 로그용. **미완료 통합 목록: [docs/OPEN_ITEMS.md](docs/OPEN_ITEMS.md).** **매매 신호 규칙 정본은 [docs/SIGNAL_RULES.md](docs/SIGNAL_RULES.md)(`trend_rules.py`), 현재 진행·인수인계는 FINANCIAL_STATEMENTS.md §9-2-7(2026-10-05).**

# Stock Data Integrity / 다중 AI 작업 현황 (hermes.md)

> 🪙 **토큰 최적화(2026-09-24)**: 이 파일은 Claude/Codex/code-doer가 작업 결과를 덧붙이는 로그다. **최근(2026-09-23~) 섹션만 유지**하고, 그 이전(2026-09-20~22, 낡은 '남은 작업' 수치 포함)은 [docs/hermes_archive_20260920-22.md](docs/hermes_archive_20260920-22.md)로 이관했다.
> 새 작업은 맨 아래에 `## 제목 (작성자, 날짜)`로 **10~20줄 이내** 요약만 추가하고 상세 근거는 `docs/`에 별도 파일로 둘 것. 2주 지난 섹션은 아카이브로 이동.
> **최신 사실 확인 순서**: 이 파일의 '가장 최근 섹션' → CLAUDE.md 섹션 9(알려진 이슈) → 실제 DB 조회. 과거 섹션의 건수/상태는 그 시점 값이라 현재와 다를 수 있다.

### 이관된 이전 섹션 목차 (아카이브 참조)
- [docs/hermes_archive_20260922-23.md](docs/hermes_archive_20260922-23.md) (2026-10-05 이관): Codex 재개 점검 (2026-09-23) · 5개 전략 룩어헤드 전수점검 완료 — high_profit_compound에서 2번째(더 심각한) 버그 발견 · 남은 작업 이어서 진행 (Claude, 2026-09-23) — 282690/004200 해소, high_p · v4 시점별 가격 차단·재무행 provenance 개선 (Codex, 2026-09-23) · high_profit_compound 3번째 수정에서 발견한 2차 버그 — end_date vs 실제 마지막 · unresolved_active_common pykrx 검증 — 대형 발견: 2010~2021 구간 전반의  · 000670(SK하이닉스) 미스터리 완전 해결 + 실제 수정 완료 (Claude, 2026-09-23) · 035720(카카오) 동일 패턴 확인·수정 + 256940/300720 최종 판정 (Claude, 2026- · v4 과거 데이터 인프라 완성 및 PIT 검증 승격 (Codex, 2026-09-23) · ⚠️ 최우선 발견: price_history 전체 29%(297만행, 2,662종목)가 소수점 보간값 오염  · ✅ 위 사고 실제 수정 완료 — 297만행 중 218만행 복구, 나머지는 지수/미커버 확인 (Claude, 
- Completed In This Remediation
- Remaining Work: Do Not Guess Values
- Collection Backfill Check
- Business-Report XML Fallback Implemented
- Live Price Recheck
- Historical Filing-Gap Recheck
- Evidence And Resume Points
- Stage 1 baseline mapping (code-doer, 2026-09-20)
- QUARTERLY_4WAY 잔여 출처 0건 — balance-sheet 필드복원 (code-doer, 2026-09-20)
- 손익계산서 당분기(standalone) 파이프라인 — TDD 명세+RED (code-doer, 2026-09-20)
- 손익계산서 당분기 파이프라인 — GREEN 완료 (code-doer, 2026-09-20)
- 가격 데이터 무결성 — coverage_gap 근본원인 + 정책 변경 (Claude, 2026-09-20)
- 2026-09-20 22:39:46 — P0: price_history 데이터 무결성 가드 (code-doer)
- Minervini 전략센터 등록 이어서 진행 + pykrx/FinanceDataReader로 남은 검증항목 확인 (Claude, 2026-09-20)
- invalid_ohlcv 38건 중 27건 완결 (17 백필 + 10 재조사) — 위 판정 정정 (Claude, 2026-09-20)
- 2026-09-20 — code-doer: PG 전환 승인조건 검증 산출물 (Checker 재검증용)
- 2026-09-20 — Planner priority and mandatory acceptance gates
- P0 REFACTOR 완결 — migration + financial_fix_log + source_count 연동 (code-doer, 2026-09-20
- P1 시작 — live fail-closed read-gate 명세+RED (code-doer, 2026-09-20)
- F01~F09 수정본 ↔ 런타임 경로 독립 확인 (code-doer, 2026-09-20)
- [2026-09-20 code-doer] F01~F09 커밋 후보 + 규격문서 정본 colocation
- Minervini 가상매매 자동연결 완료 + 전략센터 governance 시스템 전역 버그 발견·수정 + marcap 신규 도구 추가 (Claude, 202
- data_availability 8개 전략 실사 완료 (Claude, 2026-09-22)
- survivorship_integrity 조사 → 공용 엔진의 "부도 가정" 버그 발견·수정 (Claude, 2026-09-22)
- PIT 프로비넌스 인프라 — 설계만 완료, 구현은 보류 (Claude, 2026-09-22) → **2026-10-06 확인: `financial_facts_pit` 매일 재구축으로 일부 해소, 정확한 PIT 로더는 미완(docs/OPEN_ITEMS.md C11)**
- 다중 AI 작업 재검증 및 추가 수정 (Codex, 2026-09-22)
- golden_cross 룩어헤드 편향 버그 발견·수정·재실행 완료 (Claude, 2026-09-22)

## ✅ 재발방지 가드 + 전체 재감사 + 2차 복구 (Claude, 2026-09-24)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 기업이벤트 등록 + coverage_gap 사유 기록 (Claude, 2026-09-24 저녁)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 연구 도구 도입 1~2단계 (Claude, 2026-09-24)
- 연구용 venv: `stock_dashboard/research_venv`(numpy 2.4.6, quantstats/alphalens-reloaded/vectorbt/PyPortfolioOpt). 운영 venv(numpy 1.26.4/pandas 2.3.3)는 미변경.
  DB는 운영 venv의 `research/extract_research_inputs_20260924.py`가 parquet(data_cache/research)로 추출 → 연구 venv는 parquet만 읽음(자격증명 불필요).
- alphalens-reloaded 로컬 패치: `alphalens/utils.py`의 `df.index.levels[0].freq = freq`를 try/except(월말처럼 희소한 날짜는 BusinessDay freq 설정 불가). 재설치 시 재적용 필요.
- 결과: research_outputs/alphalens_factor_validation_20260924.{md,csv,json}. 요약: 저변동성/저PER/저PBR 유효, 60~120일 모멘텀은 역효과, 수급(supply_20d)·기존 model_score는 음의 IC.
- 문서(PDF "GitHub 추천 20선")의 pykrx 중심 전제는 현재 사실과 다름(pykrx 전 종목 빈 결과) → marcap/FDR/DART 유지.
- 1단계(QuantStats): `research/build_backtest_equity_curves_20260924.py`로 `backtest_equity_curve` 테이블 신설(engine 929, realized_pnl 1,122, realized_pnl_assumed_100m 575 = 2,626/3,035 run;
  409건은 거래별 손익 없음). `research/quantstats_strategy_report_20260924.py` 결과: research_outputs/quantstats_summary_20260924.{md,csv,json} + HTML 3종.
  복원곡선(15개 전략)은 계단형이라 Sortino/베타 신뢰 불가 → 엔진이 일별 평가곡선을 저장하도록 개선 필요.
- 3단계(vectorbt): `research/vectorbt_rule_sweep_20260924.py`(vectorbt 1.0.0은 plotly<6 필요 → 연구 venv에 plotly 5.x 설치, 트레일링은 sl_trail). 결과 research_outputs/vectorbt_rule_sweep_20260924.{md,csv,json}:
  국면필터는 훈련 MDD만 개선·검증 수익 반토막, 훈련 최적값이 검증에서 유지되지 않음 → 운영 기본값 변경 근거 아님(기존 엔진 재현 + 하락장 표본 필요).
- 4단계(PyPortfolioOpt): `research/pyportfolioopt_sidebyside_20260924.py`. 동일비중(CAGR 18.1%, Sharpe 0.82)이 HRP(14.0%/0.74)·MinVol(4.9%/0.33)보다 우수 → 최적화 비중 도입 보류.
  vectorbt(고정금액 진입)와 이 스크립트(월 복리 재배분)의 절대수익 정의가 달라 통일 필요.
- **미국 가상매매 9/23 차단 원인 확정·수정**(`routes/us_virtual_trading.py`): 거래일 인정 기준이 "전 종목 수 vs 역대 최대(3,623, 2026-05-22)의 95%(=3,442)"였음.
  9/21 3,546 → 9/22 3,446 → 9/23 3,424로 스팩/권리/워런트 등 비유동 종목이 빠져(9/22 106종목 중 43종목은 이후 다시 등장 = 그날 거래 없음) 9/23이 기준 미달.
  전 종목 수는 상장폐지가 쌓일수록 영구히 기준 아래로 내려가는 구조적 결함(docstring이 경고한 바로 그 문제)이라, 기준을 **핵심 유니버스**(최근 60세션 중 90% 이상 출현 종목, 현재 3,527개)의
  당일 출현 비율 ≥95%로 변경. 9/23은 3,411/3,527=96.7%로 통과. 테스트 tests/test_us_paper_core_coverage_20260924.py(3건). 서버(uvicorn) 재기동 후 반영됨.
  주의: 핵심 종목 98개가 9/22에 한꺼번에 사라진 것은 상장폐지/합병인지 수집 누락인지 미확인(ANY, ALF, GV, GLMD 등 실제 회사 포함) → 수집기 로그 확인 필요.
- QuantStats 후속: `research/reconstruct_mtm_equity_20260924.py`로 거래로그+가격 기반 일별 평가곡선 재구성(선정 전략 79개 run, source='mtm_reconstructed'). 첫 시도는 조정종가 수준과 로그 진입가 수준이 달라(분할 종목) MDD -90%로 왜곡 → 같은 가격 시리즈의 진입일 값을 기준으로 수정. 결과 베타 0.3~0.9로 정상화.
- 미국 데이터: 9/22~23 핵심종목 156개가 수집기에서 누락(Yahoo엔 정상 존재, 39/40 확인) → `scripts/ops/sync_us_daily_quotes_and_factors.py --tickers ... --period 1mo`로 재수집, 9/23 종목수 3,424→3,538 복구.
  9/22는 100개 종목에 Yahoo 자체가 바를 주지 않음(소스 측 공백, 채울 수 없음). 재수집이 쓴 9/24 장중 34행은 삭제(미완성 봉).
- 수급 공백(2026-09-14~18): 다른 세션이 14:01에 `scripts/backfill_supply_from_kiwoom_20260924.py`로 금액 컬럼(*_net_buy_amt) 11,488행을 이미 백필함(검증 완료 확인). 남은 것은 **수량 컬럼**(inst/frn/ind_net_buy) ~2,335종목×5일 NULL —
  kiwoom_investor_daily에는 수량 컬럼이 없어 같은 방식으로 못 채움, KRX OpenAPI(collect_krx_investors.py)는 HTML 오류 페이지 반환(서비스/권한 문제)이라 사용 불가. 중복 작업 피해 미조치.
  (수집기 collect_krx_investors.py는 '오늘' 날짜만 처리하고, 가격행이 없으면 close=0 플레이스홀더를 INSERT하는데 write guard(close<=0 차단)에 걸림 - 기존 충돌, 미수정.)
- ~~미완: 피처 스냅샷 생성기 없음~~ → 해결(월간피처스냅샷 잡, 스냅샷 2026-10-02까지 — 2026-10-06 확인).

## Python 3.12 전환 준비·검증 (Claude, 2026-09-25) — ✅ 운영 전환 완료 (2026-09-25 22:51 `venv → .venvs/py312b`)
- 설치: Homebrew python@3.12.14(`/opt/homebrew/opt/python@3.12/bin/python3.12`). 새 venv `runtime/.venvs/py312`(numpy 2.2.6, pandas 2.3.3, pykrx 1.2.9, 운영 115패키지 = 3.11 freeze 기준; `.venvs/freeze_py311_20260924.txt`, `.venvs/requirements-py312-candidate.txt`, `.venvs/requirements-core.lock`).
  OpenDartReader: PyPI의 0.2.3 sdist는 Requires-Python>=3.13이라 pip가 거부 → 3.11 venv의 순수 파이썬 패키지 디렉터리(OpenDartReader + dist-info)를 복사해 사용(import 정상, 정규식 이스케이프 SyntaxWarning 1파일만).
- 검증(3.12): pytest 448 passed / 주요 모듈 import 전부 OK / TestClient로 API 8종 3.11 대비 동일(cash-conversion-signals/top은 정렬 비결정성 — 3.11끼리도 달라짐, 순서 무시 시 동일) / 지표(_calc_metrics·MDD)·가격 팩터 해시 동일 / db_compat numpy 스칼라 바인딩 동일.
  numpy2 차이: repr(np.float64)='np.float64(1.0)', uint8 산술 오버플로(NEP 50) — 코드에서 int8/uint8 사용 0건, `!r`는 문자열 값만이라 영향 없음.
- pykrx 1.2.9: 종목 OHLCV는 1.2.4와 동일값(175330 2022-02-04=8,400 = DB). ETF(get_etf_ohlcv_by_date)는 1.2.9에서 `'isin'` 오류, 1.2.4는 빈 결과 → 둘 다 사용 불가. 전종목 일괄(get_market_ohlcv_by_ticker)은 KRX 응답 형식 변경으로 실패. import 시 'KRX_ID/KRX_PW 없음' 안내만 출력(OHLCV엔 영향 없음).
- 전환 미실행 사유: 자동 분류기가 "운영 배포"로 차단(운영 venv 교체 + 백엔드 재시작). 절차(사용자 승인 후): `mv venv .venvs/py311 && ln -s .venvs/py312 venv && scripts/safe_restart_backend.sh`; 롤백: `rm venv && ln -s .venvs/py311 venv` 후 재시작. 실행 중 3.11 프로세스는 백엔드(uvicorn, launchd com.stock-dashboard.local)뿐이었음.
- 부수 발견·수리: price_history의 2026-09-14~23 일봉이 장마감 동시호가 전 스냅샷 값으로 저장돼 있었음(9/21 67% 불일치). KRX 공식(marcap 9/14~21, pykrx 9/22~23)으로 7,653행 교체(`scripts/fix_recent_close_from_official_20260924.py`, run_id recent_close_official_fix_20260925_002046). pykrx 호출을 ThreadPoolExecutor 안에서 돌리면 10분 이상 멈춤(순차 호출은 0.03초/건) → 순차로 변경.
  근본 원인(어떤 수집기가 마감 전 값을 최종으로 저장하는지)은 미확인 — 수집 시각/소스 점검 필요.
- 스냅샷 재구축: `scripts/build_strategy_research_dataset.py --adjust-jumps`(기업행위 분류 4종만 수익률 0 처리) 추가, 결과 테이블 strategy_feature_snapshot_rebuild_adj_20260924(원본 테이블 미변경). PER은 2026-06 이후 스냅샷에서 99.7% NULL(valuation_history가 2026-03-31에서 끝남).

### 스냅샷 조정 라벨 검증 결과 (Claude, 2026-09-25)
- 첫 `--adjust-jumps` 결과(3x_12m 양성 10,176→24,392)는 **내 버그**: 미래 경로는 조정 종가, 기준가는 스냅샷 시점 원본 종가 → 이후 역분할·감자 종목의 기준이 어긋남(추가 양성 14,729 중 창 안에 마스크 이벤트가 있는 것은 976건뿐). 기준가를 같은 조정 시리즈(`price_values[pos]`)로 수정해 재구축.
- 수정 후: 원본(raw) label_10x_24m 1,474(1.23%) → `_adj` 813(0.68%) / `_adj_legit` 980(0.82%); label_3x_12m 10,176(6.83%) → 8,275(5.55%) / 8,885(5.96%). 변경 행의 96%(2,181/2,269)가 12개월 창 안에 마스크 이벤트를 가짐.
  제거된 10x 라벨 720 중 505(70%)는 DART확정/발행주식수 근거가 있는 진짜 기업행위, 203(28%)은 근거 없는 pending 이벤트(82%가 상승 점프)만 있음 → 실제 급등일 수 있어 `--legit-only`(근거 있는 이벤트만 마스크)를 권장안으로 삼음.
  결론: raw 라벨은 역분할·감자로 10x 양성을 ~50% 과대계상. 권장 테이블 `strategy_feature_snapshot_rebuild_adj_legit_20260924`(원본 미변경).
- CEO 플랫폼(8011): 3.12 전환 완료 확인. 기존 500 원인 — `psutil` 미설치(3.11에도 없었음, 설치 후 monitoring-status/unified_metrics 200), `/health`는 응답모델 `Dict[str,str]`에 중첩 dict(`autonomous_state`) 반환 → ResponseValidationError(미수정, 파일 수정 중).

## ⚠️ PER 결함 발견·수정 — 이전 연구 결과 무효화 (Claude, 2026-09-25)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 📋 추가 계획 — 사용자 검토 대기 (Claude, 2026-09-25)
> 아래는 **제안**이며 아직 구현하지 않았습니다(프런트엔드·서버 재시작·운영 패키지 변경은 승인 후). 우선순위/범위를 알려주시면 진행합니다.

### A. 프런트엔드 반영 및 페이지 배치 검토
| 기능 | 배치 후보(기존 화면) | 필요한 것 | 규모 | 권고 |
|---|---|---|---|---|
| QuantStats 성과(Sortino, KOSPI 대비 알파·베타, CAGR, 곡선 출처 배지) | **전략센터 → 📊 성과 매트릭스**(StrategyHub.jsx, `/api/backtest/matrix`) 컬럼 추가 + HTML 티어시트 링크 | `GET /api/research/quantstats`(research_outputs/quantstats_summary_*.json 읽기), 곡선 출처(engine/mtm) 배지 | 소 | **권고 1순위** — 기존 표에 열 추가 |
| Alphalens 팩터 검증(IC/IR/분위수익) | **전략센터에 신규 탭 "🔬 팩터 검증"**(탭 배열에 1항목, `hubTab==='factor'`) | `GET /api/research/factor-validation`(csv/json), 팩터×기간 표+t값 색상, "PER 결함 정정" 배너 | 중 | 권고 2순위 |
| vectorbt 규칙 탐색·PyPortfolioOpt 결론 | **전략센터 → 🧪 검증 이력**(ExperimentLedgerPanel, `signal_experiment_ledger`)에 **실험 기록으로 등록**(별도 UI 불필요) | 원장 INSERT(기각/보류 결론+수치+근거 파일 경로) | 소 | 권고 — 이미 "기각이 정상"인 곳 |
| 가격 무결성(미해결 점프 146, coverage_gap_reviewed 524, 격리 8,396, 복구 run 목록) | **전략센터 → 🧭 데이터 라우팅** 탭 또는 **RiskGateMonitorView**에 "데이터 품질" 카드 | `GET /api/research/price-integrity`(price_jump_audit 분류 집계, fix_log 최근 run) | 중 | 권고(백테스트 신뢰도와 직결) |
| 기업행위 이벤트 951건·발행주식수 증거 | **종목 상세**(StockDecisionEvidencePanel) "기업행위" 타임라인 + 가격 차트 마커(문서 §7-6 Lightweight Charts 도입 시 함께) | `GET /api/stock/{code}/corporate-actions` | 중 | Lightweight Charts 도입과 묶어 진행 |
| 텐배거 라벨 정정(raw 10x 1.23% → 0.82%) | **TenbaggerProjectView**의 기준율 문구·임계값 | 문구/상수 갱신, 학습 데이터 v3 테이블로 교체 검토 | 소 | 모델 재학습 전 결정 필요 |
| 미국 가상매매 거래일 기준(핵심 유니버스) | **StrategyCenterView(미국 종이운용)** 준비상태 카드 | 이미 API가 `basis`, `reference_ticker_count` 반환 — 문구만 표시 | 소 | 서버 재시작 후 |
| Python/라이브러리 버전 | 관리자/시스템 정보(있다면) 또는 `/api/system/runtime` | 버전·venv 경로 표시 | 소 | 선택 |
- 프런트엔드 빌드/배포 절차(`frontend/dist`, 백엔드 재시작)는 운영 배포에 해당 — 반영 시점 합의 필요. 신규 라우터는 `routes/research_lab.py` 한 파일로 격리(파일 읽기 전용, DB 쓰기 없음) 제안.

### B. 라이브러리·환경 (docs/LIBRARY_INVENTORY_20260925.md)
1. **취약점 패치**(pip-audit): 운영 venv 14개 패키지(aiohttp 14건, starlette 5건, python-multipart 5건, cryptography 4건, soupsieve 4건 등). 제안: (a) 마이너/패치 묶음(aiohttp≥3.14.3, anyio, click, idna, lxml≥6.1, pyasn1, requests≥2.33, soupsieve≥2.9, urllib3≥2.7, curl-cffi≥0.15, anthropic≥0.87, python-multipart≥0.0.31)을 **새 venv `.venvs/py312b`에 적용→pytest 473+API 비교→전환**(이번 3.12 전환과 같은 절차), (b) starlette 1.x·cryptography 50은 FastAPI 호환/의존 확인 후 별도, (c) CEO 플랫폼 starlette 0.47.3(6건)도 동일.
2. 연구 venv: setuptools 83+ 업그레이드(간단), alphalens 로컬 패치 재적용 스크립트화.
3. OpenDartReader 복사 설치를 `requirements`에 명시(주석)하거나 PyPI 0.2.3 사용 가능 여부 확인.
4. vectorbt 라이선스(Commons Clause) 저장소에서 확인.
5. pykrx: KRX_ID/KRX_PW를 제공하면 1.2.9의 로그인 기반 기능이 열릴 수 있음(자격증명은 사용자가 직접 `.env`에 — 저는 입력하지 않음). ETF는 두 버전 모두 불가 → FDR/Naver 대체 유지.

### C. 데이터·수집기 (미해결)
1. **일봉 마감 전 스냅샷 원인 수집기 특정**: 9/14~23 일봉이 동시호가 전 값으로 저장(7,653행 교체함). 수집 시각/소스(`collect_naver_ohlcv_today` 등)·재발 방지(저장 전 KRX 공식값 재확인 또는 16:00 이후 확정 저장) 필요. 매일 자동 검증(marcap/pykrx 대비 종가 불일치율 알림) 크론 제안.
2. **7/12 SQLite 재적재 배치 원인 스크립트** 미특정(같은 초 223만행). 가드는 소수점만 차단 → 정수 배율 오류용 가드(전일 대비 가격제한폭 초과+공시 없음이면 격리) 검토.
3. 수급 수량 컬럼(inst/frn/ind_net_buy) 9/14~18 ~2,335종목 NULL — 소스 없음(금액은 다른 세션이 백필함). 수량이 필요한 곳(tenbagger_engine 등) 영향 조사 후 금액/종가 근사 여부 결정.
4. 미국 데이터: 9/22 핵심종목 100개는 Yahoo 자체 결측. 수집기 stale-only 로직이 중간 구멍(하루 누락)은 복구 못 함 → 일자 구멍 재수집 모드 제안. 핵심종목 98개가 9/22에 동시 결측된 원인은 미확인.
5. 미해결 점프 146건(DART 증거 없는 진짜 점프 ~60): 공시 조회 창/유형 확대 또는 수동 검토 목록 제공.
6. 소수점 8,396행(ETF 이전 구간, 원본 없음) 격리 유지 — 격리 행이 백테스트에서 실제로 제외되는지 회귀 테스트 필요.
7. 스냅샷: v3 테이블을 `strategy_feature_snapshot`로 승격할지(기존 소비자: tenbagger_engine, research 스크립트) 결정 — 승격 전 소비자 영향 점검 및 PIT(공시 시차) 확인. 스냅샷 월별 갱신 크론이 없음(07-24/08-11에서 멈춤).
8. 쓰기 가드 자동 테스트(Postgres 필요) — 테스트 DB 픽스처 도입 여부.

### D. 연구 후속
1. 종목 선정이 약함(TTM PER 기준 롱온리 20종목 CAGR 7%) → 다음 후보: 팩터 결합을 Alphalens IC 가중으로(고정 가중치 금지), 섹터 중립화, 거래비용 민감도, 하락장 포함 검증. 저변동성만 견고 → 저변동성+품질/수익성 결합 검증 제안.
2. 기존 전략 26개 성과의 KOSPI 대비 초과수익이 대부분 베타(0.3~0.9) 수준 — 엔진에서 **일별 평가곡선 저장**으로 QuantStats 재계산(현재 15개는 MTM 근사).
3. Alphalens를 전략 신호 단위로(44개 전략의 진입 신호 로그 필요) — 신호 로깅 스키마 제안 필요.
4. 텍스트 감성(FinGPT 대체=이미 설치된 API), Qlib은 Alpha158 정의·walk-forward 방식만 차용 — 우선순위 낮음.

### E. 알려진 미수정 버그
- CEO 플랫폼 `/health` 500: 응답모델 `Dict[str,str]`에 중첩 dict 반환(`main.py:784` 부근, 다른 세션이 파일 수정 중이라 미수정).
- 터미널에서 서버를 `&`로 띄울 때 `< /dev/null & disown` 없으면 tty output으로 정지(8011에서 발생).

## HANDOFF §10 "적용 후 검토" 처리 (Claude, 2026-09-25) — 상세는 docs/HANDOFF_GITHUB_ADOPTION_20260924.md §10-4
- **⛔ P0-1 스냅샷 정본 교체 차단**: 자동 권한 분류기가 정본 교체(공유 운영 테이블 변경)와 교체 스크립트 파일 작성을 차단 → 미실행. **v4**(`strategy_feature_snapshot_rebuild_v4_20260925`)를 준비하고 SQL 절차를 §10-4에 기록(사용자 실행/승인 대기). 이에 종속된 P0-2(valuation_history per_ttm)·P0-3(월간 재생성 잡)·aqr 재백테스트·소비처 점검은 미착수.
- **🔴 신규 발견 — 스냅샷 생존편향**: `security_master_history`의 폐지·합병 보통주 549개가 전부 `security_type='listed_equity'`인데 생성기 필터가 `'주권'/'common_or_unknown'`만 통과 → **폐지 종목이 스냅샷·라벨·팩터 연구에서 전부 누락**. 필터 수정(우선주 제외 유지) 후 v4: 190,609행/2,708종목(+205 폐지), 10x_24m 0.82%→0.91%. 연구 입력(`extract_research_inputs_20260924.py`)도 v4로 전환, 폐지 200종목이 수익률에 포함됨(팩터 결론은 거의 불변: 저변동성 견고).
- 연구 결과(정정): 학습/검증 분할에서 저변동성 IC 0.168→0.165, 저PER 0.070→0.114, 저PBR 0.077→0.082 유지, 모멘텀 -0.05→+0.01 반전(기간 특이), model_score -0.03→-0.15(악화), 수급 -0.06→-0.015(약화), small_size 부호 반전. 이벤트 스터디(공시 익일 진입·시장 중앙값 대비·윈저라이즈): 자사주 취득결정 +6.0%p(검증 t 5.3)·신탁체결 +5.6%p(t 8.9)·CB 발행 -2.6%p(t -4.5, 지속). 수주·특허는 평균이 꼬리 의존(중앙값≈0). 원장 13건 기록(`scripts/record_research_ledger_20260925.py`).
- P1-4: 모멘텀·돌파 대용 신호로 운영 규칙(KOSPI<MA60) on/off 비교(운영 `_tx_cost` 동일 비용, 2026-07~09 급락 창 포함) → 필터 ON이 전 구간 CAGR·MDD 개선(급락 창 MDD -41.6%→-23.7%), 유지. 대용 신호 자체는 손실(회전율 과다) — 최종 확정은 운영 전략 신호 로그로.
- P1-7/8: `backtest_equity.py`(엔진 곡선 또는 거래로그+가격 MTM 저장), PK `(run_id,source,date)`·뷰 `backtest_equity_curve_best_v` 적용 완료, QuantStats는 가정 자본 곡선 위험지표 제외.
- P2: `requirements/` 추적 경로·`-c core.lock`, OpenDartReader 설치 절차, 연구 venv 3.12 재생성(+`research/alphalens_compat.py`로 패치 제거, 재현 검증 diff 0), `research_venv→research_venv312`.
- P2-13: Lightweight Charts(`PriceChart.jsx`, App.jsx 분기, `chart_engine=svg` 롤백) — 브라우저 검증 완료, `dist` 배포 미실행. P2-14: 종가 공식 검증 잡(19:30)·KIS 일별수집 원장 편입 — 서버 재시작 후 반영. P2-15: CEO `/health` 수정(8011 재시작 후 반영).
- **서버 재시작이 필요한 반영분**(운영 배포로 분류돼 미실행): 스케줄러(종가공식검증·KIS 원장), `backtest_common._save_result` 곡선 저장, 프런트 `dist` 빌드, CEO 8011. 운영 백엔드는 `bash scripts/safe_restart_backend.sh`(CLAUDE.md 규칙), 프런트는 `cd frontend && npm run build` 후 재시작.

- (2026-09-25) P0-1 정본 교체 완료(사용자 실행). P0-3 월간 재생성 잡은 스크립트 작성 차단 → 사용자 승인/직접 작성 필요, P0-2(`valuation_history` TTM)·aqr_multifactor 재백테스트 대기.

- (2026-09-25) aqr 재백테스트 완료: 구→신 수익률 +98→+55, -30→-20, -9.5→-1.7, +31→+5, +13→+20, +6.7→+9.1 (look-ahead PER 제거). 월간 스냅샷 스크립트 검증 완료, 스케줄러 등록·P0-2(valuation_history TTM)만 남음.

- (2026-09-25) P0-2 완료: valuation_history.per_ttm/ttm_net_income 추가(중앙값 PER 약 10~12배, 삼성 9~15배로 안정). 남은 항목: ~~스케줄러에 월간피처스냅샷 등록(편집 권한 대기)~~ → 등록됨(2026-10-06 확인).

- (2026-09-25) 계획 A 1차 구현: 팩터 검증 탭 + /api/research/* 라우터(읽기 전용). 빌드·API 단위 확인 완료, 브라우저 렌더 확인은 재시작 후 필요. 남음: QuantStats 열(성과 매트릭스), 가격 무결성 카드, 종목 상세 기업행위 마커.

- (2026-09-25 밤, S5) **"추가 계획 A"와 실제 구현 범위 정정**: 계획 A 1차가 이미 구현·배포돼 있다 — `routes/research_lab.py`(`/api/research/factor-validation`·`quantstats`·`price-integrity`, 읽기 전용), `FactorValidationPanel.jsx`(팩터 IC·이벤트 스터디·QuantStats 표), `PriceIntegrityCard.jsx`(데이터 라우팅 탭 상단). 미구현: 성과 매트릭스 표에 QuantStats 열, 종목 상세 기업행위 타임라인, 텐배거 문구, 시스템 런타임 표시. **검토 전 노출 차단**: 탭은 `localStorage.research_lab_tab='1'`일 때만 표시(가림), `/api/research/*`는 터널 경유 시 `API_WRITE_TOKEN` 필요(`security_gate.py`). 검토 후 플래그·게이트 제거.

- (2026-09-25 밤) §12 R2~R9·shadow A안 처리 완료 — HANDOFF §14. 재시작·Cloudflare Access 재확인·py312b 전환·R3 플래그·momentum/peak 원장 수정 범위가 사용자 결정 대기.

- (2026-09-26) §15 V1~V9 처리 완료 — HANDOFF §16. 남은 결정: Cloudflare Access 앱 확인·`CF_ACCESS_*` 설정, CEO 8011 위험 쓰기 엔드포인트 세션 인증(다른 세션)·`.venv312b` 전환, V7(`v_gc` shadow·LAN 제한).

## Codex 최신 문서·운영 상태 재검토 및 보완 (2026-09-26 12:48 KST)

`runtime/AGENTS.md`, 루트/런타임 `CLAUDE.md`, 이 파일의 최근 이력과 운영 PostgreSQL을 대조했다. 루트는 공용 데이터 경로이고 실제 코드·git 정본은 `runtime/`이며, 단수 `AGENT.md`는 없고 `runtime/AGENTS.md`가 현재 규칙 정본이다. Claude의 FnGuide 분기 스냅샷 수집(`scripts/collect_fnguide_quarterly_snapshot_20260926.py`)은 이 점검 중에도 별도 프로세스로 실행 중이므로 해당 파일·수집 행은 수정하지 않았다. `fill_suspension_gaps_from_marcap_20260924.py --apply` 금지와 기존 57,874행 자동 롤백 금지도 그대로 유지한다.

### 새로 발견해 수정한 사각지대

1. **기본 pytest가 실험 스크립트를 실행하는 문제**: pytest 설정이 없어 저장소 루트에서 `pytest`를 실행하면 `scratch/`의 `*test*.py` 63개와 루트 `test_interp.py`까지 수집했다. 이 파일들은 import 단계에서 운영 PostgreSQL 연결·백테스트 실행·없는 로컬 파일 접근을 수행하므로, 단순 회귀검사가 운영 작업을 건드릴 수 있었다. `pytest.ini`에 `testpaths=tests`, `python_files=test_*.py`를 추가해 기본 명령이 정식 테스트만 수집하도록 고정했다.
2. **전략 스위트 교체 후 검증 아티팩트 누락**: 2026-09-25 가격 기준 재실행으로 선택 run hash가 바뀌었지만 새 hash에는 `survivorship_integrity`와 `corporate_action_integrity`가 재등록되지 않았다. 이 때문에 실제 가격 감사 통과 여부와 무관하게 27개 선택 전략 중 13개가 `legacy`였고, v4 최신 선택 스위트 `3a1df776883808d8`도 문서 설명과 달리 `legacy`였다. `audit_selected_strategy_price_integrity.py`를 현재 정본 뷰로 재실행해 27개 전부의 아티팩트를 다시 만들었고, v4는 **`point_in_time_verified`로 복구**됐다(남은 미통과 사유는 `forward_validation`뿐).
3. **재발 방지 배선**: `scripts/rerun_selected_after_price_repair.py`와 `scripts/rerun_all_after_audit_rebuild.py`가 새 스위트 선택을 마친 뒤 전체 선택 전략 무결성 감사를 반드시 실행하고, 결과 요약(`checked_at`, 정책 버전, 통과/실패/무거래 수)을 재실행 결과 JSON에 저장하도록 수정했다. 향후 가격 복구·감사 재빌드 뒤 새 run hash가 검증 행 없이 남는 문제를 막는다.

### 운영 DB 실측 스냅샷

- **가격 감사(12:43 KST)**: `unresolved_active_common` 64건, `quarantined_basis` 8,448건, `coverage_gap_reviewed` 524건, 미검토 `coverage_gap` 1건이다. 따라서 이 파일 앞부분의 “미해결 146 / 소수점 8,396”은 과거 시점 수치이며 현재값으로 사용하면 안 된다. 정본 뷰의 `return_usable=0`에는 거래정지 파생행 263,458건도 별도로 포함된다.
- **선택 전략 가격 무결성 재감사(12:47 KST)**: 27개 중 23개 통과, 4개 실패. 실패는 `deep_recovery`(가격오염 9.34% + survivorship 2), `extreme_dd_volume`(12.63%), `low_base_breakout`(3.64% + survivorship 5), `v12`(0.34% + survivorship 1)다. 5%·7% 민감도 모두 23개 통과라 현재 결과는 7% 경계에 의존하지 않는다. **이 23/4는 가격·상장구간 감사 결과**이며 전략 전체 등급과 동일한 숫자가 아니다. 전체 게이트 기준 현재 분포는 `point_in_time_verified` 1(v4), `point_in_time_approx` 14, `execution_strict` 3, `legacy` 9다.
- **재무 검증(12:43 KST)**: `QUARTERLY_4WAY`는 OPEN 4,270, AMBIGUOUS 15, CONFIRMED 52,840이다. `OFS_ANNUAL_CONSISTENCY` OPEN 13,231·AMBIGUOUS 1,215는 여전히 후속 분류 대상이다.
- **FnGuide 진행 중 스냅샷(12:43 KST)**: 2026Q1·Q2 각각 188종목이 `financial_source_snapshot(data_source='fnguide')`에 수집됐다. 실행 중인 장기 수집이므로 완료 수치로 간주하지 않는다.
- **피처 스냅샷**: 정본 `strategy_feature_snapshot`은 190,609행, 최대 기준일 2026-09-23으로 확인됐다.

### 검증

- Python 3.12 운영 venv에서 수정 파일 `py_compile` 통과.
- `venv/bin/python -m pytest -q`가 이제 정식 스위트만 실행하며 **543 passed, 54 subtests passed**(경고 2건: Pydantic v1 validator, Starlette TestClient cookies deprecation).
- 가격 원시행·재무 원시행은 이번 Codex 점검에서 쓰지 않았다. 운영 DB 쓰기는 현재 선택 run hash에 대한 검증 아티팩트 갱신뿐이다.

### 현재 판단

v4의 과거 데이터/PIT 문제는 최신 선택 스위트 기준으로 다시 검증 완료됐지만 시스템 전체가 완료된 것은 아니다. 가격 감사 실패 4개 전략, 재무 OPEN/AMBIGUOUS 플래그, 진행 중인 FnGuide 전수 수집, 그리고 `forward_validation`은 계속 남아 있다. Claude의 동시 작업 결과는 완료 시 이 스냅샷 이후 수치로 다시 갱신해야 한다.

## Codex 복합전략 600%대 유효성 재점검 (2026-09-26 14시대 KST)

### 판정

전략센터의 600%대 수익률은 **현재 유효 성과가 아니다**. 화면 최상단에 노출되던
`cmb_da1d39936923` 646.43%는 300720의 2021-09-09 오염 종가 238,000원(정상
가격대 약 23,800원)을 이용해 한 거래가 +1,451%로 청산된 결과다. 이 거래는 전체
손익의 21.6%를 만들었고, 복리로 이후 티켓까지 키웠다. 300720을 제외한 동일 주문
재시뮬레이션은 371.2%였다. 따라서 646.43%는 참고값으로도 사용하지 않는다.

그보다 앞선 688.94%도 2026-09-08 수정 코드 재현에서 403.09%로 내려갔고, 8회
동점순서 안정성은 평균 368.64%·중앙값 366.33%·범위 331.61~404.78%였다. 이
366~403% 역시 2026-09-08 재무 스냅샷 기준의 과거 결과다. 2026-09-26 FnGuide
기준 재무 재정리가 끝나기 전에는 최신 유효값이 없다.

### 이번 수정

- 원천 composite run hash `83e9a856a4ce`와 병합 run hash
  `9201c784855773c5`에 `result_validity=false` 아티팩트를 기록했다. 저장 행은 감사
  추적을 위해 보존한다.
- `run_registry.derive_status()`에 `result_validity` 게이트를 추가했다. 사후 감사에서
  손익이 무효화된 컴포넌트는 `execution_strict` 이상을 잃고 새 병합계좌 등록에 다시
  사용될 수 없다.
- `/api/backtest/combinations/list`는 무효화 run을 제외한다. 각 run의 저장 당시
  `financial_data` 수정시각과 현재 수정시각을 비교하고, 최신
  `cfs_ofs_mixed_ttm` 계약이 fail이면 `validation_status=revalidation_required`를
  반환한다.
- 전략센터 화면은 재검증 중 수익률 숫자와 `현재 최고` 배지를 숨기고 `재검증 대기`로
  표시한다. `routes/trend.py`의 552/577/530/487/585% 고정 라벨도 제거했다.

### 현재 데이터 계약과 후속 순서

2026-09-26 13:13 KST 계약 기록은 `cfs_ofs_mixed_ttm` fail 3,022/8,121행이다.
`financial_data`는 같은 날 23,281행이 갱신됐고, 34차의 `dart_q_full_fetch.py` 3개
프로세스가 아직 실행 중이므로 TTM PER 재계산과 전략 재실행을 지금 하면 다시 낡는다.

34차 완료를 다음 조건으로 확인한 뒤 순서대로 진행한다.

1. 분기 2016~2025Q1 지배주주 순이익·자본 적용과 Q4 재계산 완료
2. `add_valuation_history_per_ttm_20260925.py --apply`
3. `data_contract_audit_20260925.py`에서 `cfs_ofs_mixed_ttm=ok`
4. 9/30 월간 `strategy_feature_snapshot`이 새 TTM 값을 쓰는지 확인
5. PER 기반 Alphalens와 병합전략 컴포넌트를 동일 데이터 지문으로 재실행
6. 가격오염 보유구간은 비율 7%만 보지 않고 손익기여까지 검사한 뒤 새 병합 run 등록

검증: 신규 `result_validity` 회귀테스트 1건 통과, Python 문법 검사 통과, 프론트엔드
프로덕션 빌드 통과. 운영 API 직접 호출에서 646.43% run 제외와 남은 4개 run의
`revalidation_required` 전환을 확인했다.

## Codex 선택 전략 감사 재검증·정정 (2026-09-26 13:35 KST)

바로 위 12:48 스냅샷의 **23통과/4실패 판정은 감사기 결함 수정 전 결과이므로 현재 판정으로 사용하지 않는다.** 선택 전략 실패 4개와 v4 `forward_validation`을 다시 추적해 다음을 확인·수정했다.

1. **중복 보유창 결함 수정**: 일부 레거시 거래원장은 같은 거래를 `BUY` 이벤트 행과 완결 거래 행으로 함께 저장한다. `holding_windows()`가 완결 행을 정상 창으로 추가하고도 원래 BUY를 열린 포지션으로 남겨 기간 말까지 두 번째 가상 창을 만들었다. 예: deep_recovery 011690은 2020-03-17 손절 완료인데 2021-11-30까지 보유한 것으로 중복되어 이후 거래정지 419일이 오염으로 붙었다. 완결 행의 `(종목, 진입일)`과 같은 BUY 이벤트를 다시 열지 않도록 수정하고 회귀 테스트를 추가했다. 수정 직후 `extreme_dd_volume`은 실패에서 통과로 바뀌고 결과는 24통과/3실패가 됐다.
2. **KONEX 이력 사각지대 수정**: 일별 KRX KOSPI/KOSDAQ 정본에 없다는 이유로 이전 구간을 일괄 `pre_official_equity_reference_ineligible`로 만든 정책이 실제 KONEX→KOSDAQ 이전상장 종목도 제외했다. KRX KIND 공시로 126340 비나텍(2013-07-01 KONEX→2020-09-23 KOSDAQ), 107640 한중엔시에스(2013-12-10 KONEX→2024-06-24 KOSDAQ)를 확인했다. `security_master.py`에 증거 기반 KONEX 예외를 추가해 재빌드 후에도 유지되게 했고, 운영 DB는 `security_master_history_backup_codex_20260926_konex`(적용 전 4행) 백업 후 두 구간을 `official_disclosure_verified`/tradable로 반영했다.
3. **SPAC 과거구간은 계속 차단**: low_base_breakout의 127980·162300·380540은 현재 회사 상장 전 같은 코드의 합병 SPAC 가격 구간이다. 현재 회사명·재무를 그 과거 가격과 연결하면 identity look-ahead가 되므로 KONEX 예외에 포함하지 않았다. 이 전략의 survivorship 5건은 실제 결함으로 유지한다.

최종 재감사 결과는 **27개 중 26개 통과, 1개 실패**다. 유일한 실패는 `low_base_breakout`(220창 중 가격오염 3.64%, survivorship 5건)이다. 5%와 7% 임계값에서 모두 26개가 통과하므로 임계값 경계 의존성은 없다. `deep_recovery`, `extreme_dd_volume`, `v12`는 통과로 정정됐다.

### v4 재판정

- 최신 선택 스위트는 `3a1df776883808d8`, 방법론 상태는 `point_in_time_verified`, 미충족 방법론 게이트는 `forward_validation` 하나가 맞다.
- 다만 이것은 **운영 채택 직전**이라는 뜻이 아니다. 최신 실적은 +27.35/0/+2.97/+8.80/-16.32/+38.62, 평균 **+10.24%**, 4/6 양수이며 전략 거버넌스는 `retired`(자동매매 불가)다. 기존 화면 설명의 +19.46%, 2/6 양수, “전 사이클 최우수”는 이전 스위트 수치라 `routes/backtest.py`를 현재값과 retired 상태로 정정했다.
- v4 독립 prospective 원장(`sc_v4`)은 없고 전략센터 상위 5개에도 현재 포함되지 않는다. 따라서 `forward_validation`은 시간이 지나면 자동 완료되는 상태가 아니다. retired 전략을 우회해 새 paper 계좌를 자동 개설하지 않았으며, 향후 거버넌스 재승격 또는 명시적 연구 shadow 결정이 있을 때부터 60일·완결거래 20건 기준으로 새 표본을 모아야 한다.

검증: 신규/수정 단위테스트 11개 통과, KONEX 적용 후 선택 전략 전체 감사 재실행 완료. 가격·재무 원시행은 변경하지 않았고 보안 마스터 2구간과 선택 run 검증 아티팩트만 갱신했다.

## Codex 한국 주식 다중 분류 전면 개편 (2026-09-26 14:10 KST)

기존 분류는 `stock_universe.sector_*`와 `stock_sector_tags.sector`에 업종·테마·규칙
추론이 평면적으로 섞여 있었고, 출처 간 충돌을 판단할 수 없었다. 실측상 스탁이지
2026-09-23 원천 자체가 제이엠티·한국컴퓨터를 `인터넷/플랫폼`, 디케이티를 `SW/AI`로
분류하고 있어 기존 값을 정답으로 덮어쓰면 사업 실체가 훼손된다. 기존 HTML 정규식
수집기도 사이트 개편 뒤 0건을 반환하고 있었다.

### 구현

- `sector_taxonomy_nodes`, `stock_sector_membership_v2`,
  `sector_taxonomy_source_runs`를 PostgreSQL에 신설했다. 업종·시장구분·테마·밸류체인·
  공정·소재·실질 경쟁군을 독립 축으로 저장하고, 종목당 복수 태그와 부모 계층,
  제공자 원천코드, 스냅샷 날짜, 신뢰도, 상태(`verified/observed/inferred/legacy`), JSON
  근거를 보존한다. 조회는 출처별 최신 **성공** 스냅샷만 사용하며 과거 스냅샷은 남긴다.
- `scripts/rebuild_sector_taxonomy.py`를 추가했다. 스탁이지 공개 valuation API,
  키움 공식 `ka10101→ka20002` 업종 구성, `ka90001→ka90002` 테마 구성을 수집하고
  DART `company_product_mix`로 내부 반도체 밸류체인/공정/소재 후보를 만든다. 응답 중복은
  멱등 제거하고 소스별로 커밋한다. 한 소스가 실패해도 기존 성공 스냅샷은 유지하며
  실패 run을 별도 기록하고 프로세스는 실패 종료한다.
- 깨진 `sync_stockeasy_stock_analysis_sectors.py`를 HTML 정규식에서 실제 프론트가 쓰는
  JSON API로 교체했다. 2,000종목 미만이면 저장 전 실패시키며, 레거시 소비 테이블도
  2026-09-23 스냅샷 2,553종목으로 갱신했다.
- `scheduler.py`에 `종목다중분류` 잡을 매일 20:10으로 등록했다. 장시간 외부 API 호출
  동안 전역 DB 쓰기 잠금을 잡지 않으며, subprocess 실패는 수집 원장 실패로 남는다.
- `/api/sector-taxonomy/{overview,tree,stocks,stock/{code},peer-groups}`를 추가하고
  프론트 메뉴 `종목 다중분류`를 신설했다. 분류 트리, 구성종목, 종목별 모든 출처·근거,
  출처 불일치, DART 제품 매출, 경쟁군을 한 화면에서 조회한다. 원천 스냅샷이 30일을
  넘으면 화면에서 경고색으로 표시한다.
- 상세 기준과 소스 계약은 `docs/sector_taxonomy_20260926.md`에 기록했다.

### 운영 적재 결과

- 일반주식 유니버스 2,793 / 새 체계 커버리지 2,793(100%).
- 기존 기준선 7,484 관계(157노드), 스탁이지 5,106(62노드), 키움 테마 897
  (142노드), 키움 업종·시장구분 11,572(65노드), 내부 정밀분류 310(12노드).
- 키움의 실제 업종과 `KOSDAQ SMALL`·벤처기업 같은 시장 세그먼트를 분리했다.
- 제이엠티(094970)·디케이티(290550)·한국컴퓨터(054040)는 모두 검증 상태로
  `전자부품 → EMS·모듈 조립 → PBA·FPBA·모듈 조립 → OLED·스마트기기 PBA/FPBA
  제조사`에 연결했다. 근거는 2025 DART 제품 매출(PBA/FPBA, 스마트폰·전장,
  OLED-PBA·QD/LCD 모듈)이며 스탁이지의 상충 분류는 삭제하지 않고 불일치로 표시한다.

### 검증·반영

- 신규 저장계층 테스트 3개와 전체 정식 스위트 **551 passed, 54 subtests passed**.
- Python 문법 검사, 프론트 production build 통과.
- 운영 API의 overview/094970 상세/peer-groups 200 응답과 근거 내용을 확인했다.
- 자동 API 문서 476개/55그룹, DB 문서 310개 정식 테이블로 재생성했다.
- `safe_restart_backend.sh`로 PID 9818→21069, 고아 프로세스 없음, HTTP 200.

## Codex 미국 전용 백테스트 엔진 (2026-09-26)

한국 시장 전제의 `backtest_common.py`를 재사용하지 않고 `us_backtest_common.py`를
신설했다. 운영 PostgreSQL 실측은 `us_price_history` 4,045,930행·3,670 ticker,
2021-05-24~2026-09-25이며 `adj_close` 컬럼은 없다. 실제 수집 경로가 yfinance
`auto_adjust=True`라 OHLC 전체가 분할·배당 보정 기준이고, NVDA 2024-06 10:1 분할
경계도 연속 가격으로 확인했다. 미국 재무 78,630행은 SEC 실제 접수 기반
`avail_date`가 모두 채워져 있으며 엔진은 `avail_date<=signal_date`만 공개한다.

엔진은 D일 종가 신호를 다음 미국 시장 세션 시가에만 체결한다. 같은 날 종가 체결,
시가 결측 시 종가 대체, 가격 급변만으로 분할 추정은 금지했다. 목표 비중 재조정,
USD 현금·주당/최소/매도 수수료·슬리피지, 일별 MTM, 데이터 지문, 대형 가격 단절,
미체결·종료 미청산 품질 플래그와 SPY 벤치마크 비교를 구현했다. 현재 원장에는 SPY가
없어 기준 실행은 `benchmark_available=false`로 표시되고 초과수익을 산출하지 않는다.
저장을 요청한 실행만 한국 원장과 분리된 `us_backtest_runs/trades/equity`에 기록한다.

GitHub의 LEAN·bt·vectorbt·backtesting.py·QuantStats를 검토했다. LEAN은 가장
완전하지만 별도 C#/Docker 데이터 계층이 필요하고, 나머지도 과거 구성종목·상폐
수익·SEC 가능일을 자동 해결하지 않는다. 따라서 현재 의존성을 늘리지 않고 이들의
이벤트 순서·비용·성과곡선 원칙만 반영했다. 세부 비교와 사용법은
`docs/us_backtest_engine_20260926.md`에 기록했다.

기준 모멘텀 전략을 2022-01-03~2026-09-25 현재 S&P 500 전체로 읽기 전용 실행해
1,187세션과 65만여 유효 OHLC 행을 처리했다. 실행 자체는 완료됐지만 현재 구성종목을
과거에 소급한 결과이므로 `survivorship_bias=true`, 종료 보유 5종목으로
`execution_complete=false`, `research_grade=false`가 정확히 표시된다. 이 성과는
전략 유효성 근거로 사용하지 않는다. 연구 등급의 다음 필수 데이터는 날짜별 지수
구성 이력, 상장폐지·합병 최종 수익, ticker 변경 이력이다.

검증은 전용 테스트 10개와 전체 정식 스위트 **561 passed, 54 subtests passed**다.
운영 가격·재무 원장은 수정하지 않았고 전체 기준 실행도 읽기 전용으로 수행했다.

## Codex 미국 PIT 유니버스·티커 연속성 후속 (2026-09-26 21시대 KST)

공개 재구성 S&P 500 이력(`fja05680/sp500`) 2,720개 스냅샷
(1996-01-02~2026-08-18, SHA-256
`36326709d46d6cd25834de5df457b16f5f96fad3a06b9beac28f7b88aa0b0d54`)을
`us_index_membership_intervals` 1,262구간과 `us_index_constituent_events` 1,534건으로
적재했다. `scripts/sync_us_backtest_reference.py`는 기본 dry-run이고 `--apply`만 트랜잭션
교체·독립 건수 재조회를 수행하며, 원천 URL·해시·시각·상태를
`us_reference_source_runs`에 남긴다. 이 자료는 공식 S&P 라이선스 피드가 아니라
공개 보조자료이므로 품질 상태는 `public_reconstructed`다.

발행사 또는 SEC 문서로 동일 상장증권의 티커 변경임이 확인된 14건만
`us_ticker_aliases`에 `verified`로 저장했다(ABC→COR, ANTM→ELV, BLL→BALL,
FB→META, VIAC→PARA, WLTW→WTW, NLOK→GEN, PKI→RVTY, RE→EG, FLT→CPAY,
CDAY→DAY, FI→FISV, MMC→MRSH, BK→BNY). 인수·합병 종목은 후속 회사에 연결하지
않는다. PIT 로더가 이 매핑을 가격키에 적용하며 적용 수를 결과에 기록한다.

운영 PostgreSQL 적용 run은 `usref_2c3004f854e845e0ba51fbfad0c901a0`이다. 동일 기간
기준선 재실행 결과 최저 가격 커버리지 90.69%→93.07%, 평균 96.10%→96.97%, 결측
51→37종목으로 개선됐다. 수익률 +166.663%, CAGR 23.7703%, MDD -42.1351%, SPY
+70.7393%였으나 상폐·인수 최종 수익이 아직 없고 종료 보유 5종목도 남아
`survivorship_bias=true`, `execution_complete=false`, `research_grade=false`다.
따라서 이는 엔진·데이터 인프라 점검값이며 전략 유효성 결론이 아니다.

다음 필수 작업은 결측 37개 가격의 원천 복원과, 인수·상폐별 현금·주식 교환 조건을
증거 날짜와 함께 저장하는 `us_security_outcomes` 계층이다. 거래 상대 회사의 가격을
alias로 대신 쓰면 합병비율·현금대가를 잃으므로 자동 추론하지 않는다. 세부 계약과
현재 결과는 `docs/us_backtest_engine_20260926.md`에 기록했다.

구성 원천 최신일보다 늦은 종료일을 요청하면 `pit_reference_complete=false`로
연구등급을 차단하도록 추가 보완했다. 2026-09-25 종료 기본 실행에서 이 게이트가
실제로 동작함을 확인했다. 전용 테스트 17개, 전체 568 passed/54 subtests passed.

### 미국 상폐·합병 후속 처리 (21:30 KST 이후)

`us_security_outcomes`와 엔진 v2 기업행위 변환을 추가했다. 공식 발행사/SEC 근거가
있는 ABMD($380 현금+미확정 CVR), ATVI($95 현금), CERN($95 현금), DISH(0.350877
SATS), DRE(0.475 PLD), INFO(0.2838 SPGI), XLNX(1.7234 AMD) 7건을 운영 PG에
verified로 적재했다. 주말 효력일은 다음 시장 세션에 처리하며 현금·후속주식 전환은
거래 수수료나 슬리피지를 붙이지 않는다. 미확정 CVR이 실제 보유에 적용되면
`unmodeled_contingent_value=true`로 연구등급을 차단한다.

Yahoo/yfinance, Nasdaq 공개 API, KIS 해외일봉을 ABMD·ATVI로 직접 대조했으나 모두
상장폐지 종목 과거 일봉을 반환하지 않았다(KIS는 정상코드+0행; 같은 요청의 AAPL은
100행). 검토한 공개 GitHub 파이프라인도 delisted backfill은 Tiingo 키를 요구한다.
이에 `scripts/backfill_us_delisted_prices.py`를 구현했다. Tiingo adjusted OHLCV만
허용하고 기존 행 보존, 기본 dry-run, `--apply` 시 `data_fix_log`, 트랜잭션, 독립
재조회 검증을 강제한다. 현재 `.env`에는 `TIINGO_API_KEY`가 없어 실제 37종목 가격
적재는 실행되지 않았다. 가격이 없으므로 기준 실행의 outcome 적용은 0건이고
`research_grade=false`가 유지된다.

검증: 미국 전용 21 passed, 전체 정식 스위트 572 passed/54 subtests passed.

## Codex 미국 Minervini 현재 생존종목 백테스트 (2026-09-26 21:50 KST)

사용자 요청대로 상장폐지·인수 종목을 제외하고, 2026-09-25 최신 SPY 세션에도 가격이
있는 현재 S&P 500 구성 499종목만 사용했다. `scripts/run_us_minervini_survivors.py`를
신설해 D일 종가 신호→다음 세션 시가, 주간 첫 거래일 최대 10종목 동일비중, 편도 5bp,
SEC 실제 `avail_date` 기준으로 Trend Template/SEPA/SEPA+VCP를 비교했다. 첫 실행의
0거래는 공통 엔진이 SPY를 신호 후보에서 숨긴 것을 전략이 상대강도 입력까지 없는 것으로
오해한 배선 결함이었고, SPY 시계열을 별도 시점 안전 입력으로 전달해 바로 수정했다.

2022-01-03~2026-09-25 결과: Trend Template +629.33%(CAGR 52.53%, MDD -37.17%,
Sharpe 1.406, 2,616체결), SEPA +212.98%(CAGR 27.43%, MDD -25.33%, Sharpe
1.161, 1,071체결), SEPA+VCP +18.44%(CAGR 3.66%, MDD -10.23%, Sharpe 0.659,
16체결), SPY +72.03%. 엄격 VCP는 표본이 지나치게 희소해 SPY에 뒤졌다.

이 결과는 현재 생존자를 과거에 소급한 사용자 지정 실험이므로 명시적으로
`survivorship_bias=true`, `research_grade=false`다. +629%를 전략센터 유효 성과나
채택 근거로 등록하지 않았다. 또한 주간 목표비중 스크리너 연구이며 원전의 재량적 피벗,
개별 -8% 손절·부분익절을 완전히 복제한 거래관리 모델은 아니다. 상세 방법론과 해석은
`docs/us_minervini_survivors_20260926.md`, 원시는
`research_outputs/us_minervini_survivors_20260926.json`에 저장했다.

검증: 신규 전용 2 passed, 전체 정식 스위트 574 passed/54 subtests passed.

### Minervini 생존종목 결과 사후 민감도 정정 (2026-09-26)

사용자 지적대로 Trend Template +629.33%를 재검산했다. 테스트 기간 중 S&P 500에
속했으나 현재 499종목에는 없는 종목은 90개로, 사용자가 언급한 상장폐지 약 37개보다
범위가 넓다. 더 큰 문제는 현재 구성종목의 과거 소급이다. 매수 1,211건 중 398건
(32.87%), 매수 총액의 20.38%가 실제 S&P 500 편입 전 거래였다. CVNA·APP·SMCI·VRT·
HOOD 같은 이후 편입 승자를 미리 후보로 사용했다.

동일 Trend Template의 과거 시점별 구성종목 진단은 +124.18%(CAGR 18.71%, MDD
-33.96%, Sharpe 0.729)로 현재 구성종목 +629.33%보다 크게 낮았다. 다만 PIT 기준은
2026-08-18까지만 완전하고 가격 커버리지 평균 97.06%·최저 93.07%, 43종목 일부 누락으로
여전히 `research_grade=false`다. +629%는 유효 전략 성과가 아니라 미래 구성종목 정보에
민감한 생존종목 스크린 결과로만 유지한다.

연도별 현재 구성종목 수익은 2022 -2.42%, 2023 +42.26%, 2024 +70.07%, 2025
+46.55%, 2026-09-25 YTD +110.78%다. 추가 저가 감사에서 MRNA 2026-08-19
+176.97%, APH 2024년 반복 -50%/+100% 형태도 확인했다. 30% 가격 이벤트 원천 대조가
끝날 때까지 미국 Minervini 결과를 전략센터 채택 근거로 쓰지 않는다.

### 미국 Minervini 가격·PIT 인프라 조치 (2026-09-27)

재점검 후 확정 결함을 실제 반영했다. 미국 백테스트의 점프 감사 기본값이 5배여서
±30% 이벤트를 놓치던 문제를 상단 1.30/하단 0.70으로 수정했다. 현재 생존 S&P 500에서
48개 이벤트를 검사해 MRNA 2026-08-19 +176.97%는 Yahoo와 당시 임상 3상 보도가
일치하는 실제 사건으로 확인했다. APH의 2024년 -50%/+100% 반복은 Yahoo 전체이력과
불일치한 접합오류였다.

`scripts/repair_us_adjusted_price_basis.py`를 추가했다. 기본 dry-run이며 적용 시 종목의
기존 전체행을 `us_price_history_repair_backup`에 저장하고, 단일 Yahoo `period=max`
스냅샷으로 교체하며, 원천 해시와 적용 결과를 `us_price_repair_runs`에 남긴 뒤 별도
연결로 행·값을 재검증한다. APH, 최신일 불완전 OHLC 11종목, 안전하게 동일기업임을
확인한 누락 종목을 합쳐 17개 복구 run이 모두 passed였다. 18,193개 기존행을 백업하고
123,698행을 적재했으며 APH 불일치 826건·접합 2패턴은 0건이 됐다. 현재 생존 유니버스의
테스트 기간 invalid OHLC도 45→0행이다.

야간 `sync_us_daily_quotes_and_factors.py`는 앞으로 원천 OHLC 내부 모순을 차단하고,
같은 날짜의 저장 조정종가와 Yahoo 값이 2% 넘게 달라지면 해당 티커를 쓰지 않으며
프로세스를 exit 2로 실패시킨다. 따라서 스케줄러는 오염된 가격으로 미국 가상매매까지
진행하지 않는다. 실제 급락 후 반등은 동일날짜 가격이 기존값과 일치하면 시장 경로로
허용해 AIG 2008 같은 사건을 접합오류로 오판하지 않는다.

공개 S&P 재구성 자료가 2026-08-18에서 멈춘 공백은 S&P Global 공식 발표의
2026-09-21 BE/P/ILMN 편입과 TAP/TTD/BLDR 제외를 verified overlay로 반영했다.
PSTG→P도 발행사 공지의 동일증권 alias로 추가했다. 기준 커버 종료는 2026-09-26으로
개선됐다.

최종 재실행: 현재 생존종목 Trend +602.23%(CAGR 51.31%, MDD -37.18%, Sharpe
1.363), SEPA +219.29%, SEPA+VCP +18.44%; PIT Trend +115.01%(CAGR 17.66%,
MDD -42.60%, Sharpe 0.688), PIT SEPA +122.83%, PIT SEPA+VCP +10.25%.
현재 실행은 생존편향과 ±30% 이벤트 45건, PIT 실행은 가격 커버리지 최저 93.07%·평균
97.07%, 일부 구간 누락 39종목, 불완전 OHLC 1,105행, 이벤트 92건 때문에 모두
`research_grade=false`다.

Yahoo가 반환하지 않는 상장폐지·인수 과거가격은 `TIINGO_API_KEY`가 없어 남았다.
INFO·SBNY·VMRK처럼 현재 Yahoo 심볼이 다른 증권이거나 재사용된 경우는 자동 접합하지
않았다. 이 미해결 구간이 있는 PIT 값은 민감도 진단이며 전략센터 채택 근거가 아니다.
### 미국 Nasdaq-100·Russell·가격 기술지표 점검 (2026-09-27)

- `us_stock_meta.index_name='NASDAQ'`는 Nasdaq-100 지수가 아니라 현재 Nasdaq 거래소 상장 목록이었다. 과거에 그대로 쓰면 생존편향이므로 `NASDAQ100` PIT 이력을 별도 적재했다.
- `scripts/sync_us_nasdaq100_reference.py`를 추가하고 공개 재구성 CSV를 적용했다. 실행 `usref_6fb7be84d7dd47b9951bc967a011dceb`, SHA-256 `c7de3905bfdd228eefbd3c7df1539a1178bee9006a04688fb319314a646e18e8`, 112 스냅샷·305 구간·407 이벤트, 2007-02-01 시작, 2026-09-27 검증 기준이다. `load_us_membership_intervals()`는 이제 지수별 실제 소스 실행을 선택한다.
- Russell 2000은 편입·편출이 없는 지수가 아니다. FTSE Russell은 재구성을 수행하며 2026년부터 반기 주기로 전환했다. `kovagent/indexkit`을 직접 검사한 결과 2019-12~2026-06 N-PORT 분기 파일은 티커가 전부 NULL이고 CUSIP만 있었고, 티커가 있는 IWM 일별 자료는 2026-09부터였다. 잘못된 회사 연결을 막기 위해 과거 PIT 적재는 보류하고 fail-closed로 유지했다.
- 미국 일봉은 운영 DB에 4,189,826행·3,708종목, 최신 2026-09-25까지 있다. 일간 Yahoo 조정 OHLCV를 원본으로 유지하고 주봉은 `us_market_data.aggregate_weekly_ohlcv()`로 실제 주 마지막 거래일 기준 집계하도록 추가했다.
- 일괄 수집기가 `ma50`을 저장하지 않고 52주 고저를 종가로 계산하던 결함을 수정했다. `scripts/rebuild_us_technical_factors.py --apply` 실행 `usfactor_e1c67e0ef5f74160aa9601dafe99f800`: 기존 3,673행 백업, 독립 사후검증 통과. 최종 3,675행 중 MA50 3,627, MA200 3,471, 실제 High/Low 기반 52주 고저 3,675행이다.
- 상세 근거: `docs/us_index_price_infrastructure_20260927.md`.

## Codex 전략센터 재점검 및 감사 집계 수정 (2026-09-28 12:20 KST)

최신 운영 PostgreSQL, 선택 registry, 전략센터 API, 감사 산출물을 다시 대조했다. 가격·상장구간
감사는 현재 선택 전략 **27/27 통과**이며 5%·7% 임계값에서 모두 같은 결과다. 그러나 이는
가격 무결성만 통과했다는 뜻이고 전략센터 전체가 완료됐다는 뜻은 아니다.

재점검 중 `/api/backtest/matrix?include_legacy=true`가 선택 registry 제한을 풀어버리는 결함을
확인했다. 이 경우 선택 27개 대신 과거 미선택 실행까지 36개가 섞이고, 같은 전략·기간의 더
최근 legacy 실행이 선택 실행을 덮어쓸 수 있었다. 반대로 기본 조회는 legacy 등급의 선택 전략을
제외해 15개만 반환했다. `routes/backtest.py`를 수정해 `include_legacy`가 검증 등급 표시만
제어하고 조회 대상은 항상 선택 registry로 제한되도록 했다. 수정 후 기본 조회 15개,
감사용 전체 조회 27개이며 전체 조회에는 미선택 전략이 섞이지 않는다.

`audit_strategy_center_execution_readiness.py`도 감사용 전체 선택 집합을 사용하도록 고쳤고,
forward 검증 수를 하드코딩하지 않고 현재 거버넌스에서 계산하게 했다. 화면에 남아 있던 v4의
과거 suite hash `3a1df776883808d8`와 고정 성과 설명도 제거했다. 현재 v4 선택 suite는
`655093a76714dcb4`이며 수익률·검증 등급·거버넌스는 registry와 artifact에서 동적으로 표시한다.

최신 감사 결과는 다음과 같다.

- 선택 전략 데이터 가용시점: **95/162 통과, 67 실패**. 실패 구간은 신호가 소비한 재무·공시
  입력의 row id와 `available_at`을 거래별로 저장하지 않은 실제 증거 공백이다. 실패 전략은
  composite 1구간과 contract_momentum, earnings_conviction, earnings_supply_discovery,
  golden_cross, high_profit_compound, moonshot_turnaround, recovery, regime_adaptive,
  se_momentum, sector_focus, turnaround 각 6구간이다.
- 선택 전략 검증 등급: `point_in_time_verified` 2, `point_in_time_approx` 8,
  `execution_strict` 5, `legacy` 12, `forward_validated` 0.
- forward 원장: `v_gc` 8건, `v_contract_momentum` 9건 모두 20일 결과가 아직 pending이다.
  수집 46일로 정책의 90일·완결 신호 30건을 충족하지 못했다.
- 주문 가능 신호 어댑터: 선택 27개 중 9개만 존재하고 18개는 없다. 기존 가상운용도
  D일 종가 신호→D+1 시가 체결 계약과 다른 현재가 체결, golden_cross 파라미터 차이,
  contract_momentum 진입 규칙 차이, combo 재계산 repaint 위험이 남아 있다.
- 정적 계약 감사: P0 0, P1 29. P1은 `data_asof_ts` 부재, 추정 공시일 fallback,
  high_profit_compound 현재시총 모드다.
- 실전 데이터: 최신 가격 2,750/2,756(99.78%)로 6종목은 후보에서 fail-closed 된다.
  기업행위 `review_required` 5,842건과 당일 거래대금 archive 0/234는 경고 상태다.

전체 회귀검사에서 최근 사이트 접근정책 개편과 기존 보안 테스트가 어긋난 5건도 발견했다.
`/api/buy-candidates`는 의도대로 「내 투자」 viewer 범위에 포함하고, `sd_invest` 재인증
계약에 맞게 테스트를 갱신했다. API token·Cloudflare Access 관리자는 별도 관리자 비밀번호가
설정되지 않은 서버에서도 viewer API를 통과해야 하는데, 검사 순서 때문에 먼저 503을 반환하던
실제 결함도 수정했다. 최종 전체 테스트는 **588 passed, 54 subtests passed**이고 프론트
production build도 통과했다.

감사기는 이제 동일한 선택 27개를
기준으로 숫자를 산출하지만 실전 주문은 계속 차단하는 것이 맞다. 다음 우선순위는 67개 구간의
입력 provenance를 실제 거래별로 저장한 뒤 6구간을 재실행하는 것, 18개 전략 신호 어댑터를
공유 체결엔진으로 연결하는 것, 그리고 prospective 표본이 90일·30건을 충족할 때까지 forward
원장을 수집하는 것이다.

## strategy verification 전체 현황 점검 · universe_integrity 근본 원인 규명 (Claude, 2026-09-29 2차)

**① derive_status 재분포 확인 (audit 스크립트 실행 후)**
- execution_strict: 3개 (deep_recovery, low_base_breakout, v12)
- point_in_time_verified: 2개 (v4, v8)
- point_in_time_approx: 13개
- legacy: 9개 (contract_momentum, extreme_dd_volume, v2/v5/v10/v11/v1_value/v_trend/vbr)

**② contract_momentum·extreme_dd_volume legacy 잔존 원인**
- contract_momentum `25.6~26.3`: 종목 440110 거래정지 기간 보유 → 34창 중 3창 오염 = 8.82%(임계 7%) → price_integrity/corporate_action_integrity `passed=0`
- extreme_dd_volume `20.3~21.11`: COVID 급락(`confirmed_market_move`) 8창 오염 = 7.92%(임계 7%) → 동일
- 두 전략 모두 재실행이 유일한 해결책. threshold 상향(0.07→0.10)은 정책 결정 사항.

**③ v2/v5/v10/v11/v1_value/v_trend/vbr — universe_integrity 실패 근본 원인 확정**
- 모든 기간에서 `universe_integrity passed=0` — 2,441개 후보 중 398개(16.3%) 제외됨
- 제외 코드 대부분: 우선주(000105 유한양행우, 000225 유유제약1우 등 우선주 코드)
- 정책: "price-integrity exclusions are allowed only within the declared candidate-universe threshold (7%)"
- 결론: 이 7개 전략은 우선주 포함 구(舊) 유니버스로 실행됨 → **우선주 제외 유니버스로 재실행 필요**, 재실행 없이 현재 상태에서 execution_strict 승격 불가

---

## governance 전략 재판정 · 수급 신호 검증 · Brian_RAG 삼성전자 연간 데이터 수정 (Claude, 2026-09-29)

**① governance 경계선 전략 3건 재판정 확정**
- v5(-4.65%, 0/6 양수구간), v1_value(-2.67%, 2/6) — 9/27 suite 재계산으로 오히려 악화. retired 유지.
- vbr: avg6 +22.8%·3/6이나 verification_status=0(legacy) → `rank >= 1` 미달, governance 통과 불가. execution_strict 이상으로 재검증하려면 별도 재실행 필요(보류).

**② kiwoom_foreign_flow 신호 기각 (Task #2 확정)**
2026-03~09 6개월 데이터. 20일 weight 변화 → 30/60일 forward return: Q5-Q1 ≤ 1.05pp, r=0.025~0.042. 경제적 무의미.

**③ kiwoom_investor_daily 단독 신호 예측력 없음 (Task #3 결론)**
대형주 500개 2021~2025 월말 스냅샷(22,545건). orgn 20일 누적 → 3/6/12개월: Spearman r=-0.008~+0.002(p>0.20). frgnr_invsr 12개월: r=-0.031(p<0.001, 역방향). backtest 직접 연결 가치 없음. 분석 스크립트: `scratch/kiwoom_investor_signal_test_20260929.py`.

**④ 삼성전자 "2배 이상 중복 행 제외" 메시지 원인 파악 및 수정**
LLM(qwen2.5:7b)이 annual 배열에 2025년만 있자 자기 추론으로 생성한 메시지. 실제 원인: `insight.py`의 `canonical_financial_data` 쿼리가 `quality_score >= 5` + `HAVING count(*) = 4` 필터를 통과하는 연도가 2025뿐이었음 (2022Q4/2023Q4/2024Q1-Q4 CFS 모두 quality_score=3.5). `financial_data`(is_annual=1, CFS)에는 올바른 연간값이 있어(2021~2025 각 279/302/259/301/334조) 해당 쿼리로 교체 — 이제 5년 추이가 모두 제공됨. `/Volumes/Realtek_NVME/Brian_RAG/services/insight.py` line 74-84 수정.

---

## OFS_ANNUAL_CONSISTENCY OPEN 9,749→12건 해소 · dart_ofs_backfill Q4 오분류 정정 (Claude, 2026-09-29 3차)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## vbr execution_strict 재실행 결론 (Claude, 2026-09-30 4차)

**실행 결과** (`scratch/vbr_execution_strict_20260929.py`, suite_hash: `a66c2377eeb503d4`):

| 기간 | 수익률 |
|------|--------|
| 20.3~21.11 | +79.8% |
| 21.12~22.10 | -5.3% |
| 22.11~23.10 | -2.1% |
| 23.11~24.12 | -8.2% |
| 24.6~25.5 | +13.0% |
| 25.6~26.3 | +59.5% |

avg: +22.8% / positive: 3/6 / worst: -8.2%

**universe_integrity 전 기간 FAIL**:

| 기간 | 배제율 |
|------|--------|
| 20.3~21.11 | 397/2441 = 16.3% |
| 21.12~22.10 | 347/2504 = 13.9% |
| 22.11~23.10 | 320/2581 = 12.4% |
| 23.11~24.12 | 317/2709 = 11.7% |
| 24.6~25.5 | 292/2731 = 10.7% |
| 25.6~26.3 | 241/2788 = 8.6% |

임계값 7% → 전 기간 초과.

**원인 분석**(→ 09-30 정정: 우선주 필터와 무관, 기업행위 backlog가 원인): 배제 이유는 `confirmed_corporate_action`·`corporate_action_pending_confirmation`·`quarantined_basis` 등 기업행위·가격 미검증 backlog. preferred share 필터(~100종목, 4%)를 추가해도 최선 시나리오가 12.7%로 여전히 초과 → preferred 필터는 universe_integrity와 무관.

**Governance 판정**: `retired` — positive 3/6으로 4/6 기준 미달(+avg, 검증 상태 모두 미달).

**결론**: vbr `retired` 유지. 해제 조건: ① 기업행위 검증 backlog 해소(~400종목, 현재 `review_required` 445건으로 감소) → universe_integrity 통과 또는 임계값 정책 변경 ② positive 4/6 이상 달성.

**backtest_common.py 수정 내용** (이번 세션, 재실행 필요 여부와 무관하게 유지):
- line 2567: 1st 유니버스 경로에 `AND (kind_stkcert_nm IS NULL OR kind_stkcert_nm NOT LIKE '%우선주%')` 추가
- line 3056: asof_mktcap=True 경로에 `AND (sm.security_type IS NULL OR sm.security_type != 'preferred')` 추가
- line 3069: asof_mktcap=False 경로에 동일 필터 추가

---

## corporate_action_events review_required 대량 처리 완료 (Claude, 2026-09-30 5차)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## audit_price_jumps 파이프라인 재실행 결과 (Claude, 2026-09-30 7차)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## cf_validation_flags 재갱신 + Q4 분기 재무 파생 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## fin_quarterly_validation_flags OPEN 4,156건 정리 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 2016~2020 지배주주 순이익 복원 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 수주잔고 커버리지 확인 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## (무효 — FINANCIAL §5 실패 1·2) 재무 데이터 완결 선언 (2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 전략센터·Minervini 개선 방향 재정리 (Codex, 2026-09-30)

- 현재 상태는 “전략이 부족”한 것이 아니라 **전략 채택 검증이 아직 수익률 개선 병목**이다. 전략센터에는 `minervini_trend_template`, `minervini_sepa_vcp`, golden_cross, contract_momentum, earnings 계열, turnaround, meta/quality overlay가 이미 들어와 있다.
- 최신 감사 기준: `selected_strategy_price_integrity_latest.json`은 27/27 PASS(2026-09-30), `selected_strategy_data_availability_latest.json`은 162/162 PASS(2026-09-29). 9/27 지시서의 가격/가용성 대형 결함은 대부분 해소된 상태로 봐도 된다.
- 그러나 `strategy_adoption_review_20260925.md` 기준 백테스트 전략 26개 중 채택 기준 4개(OOS 초과수익, DSR>0.95, PBO<0.5, 최근 12개월 기대값)를 모두 통과한 전략은 **0개**다. PBO=0.58로 선택 절차 자체가 과최적화 위험이다.
- 외부 GitHub 프레임워크를 통째로 도입하기보다 Qlib/kr-quant/LEAN에서 배울 것은 **alpha factory workflow**다: feature/label/model/backtest/report/rolling retrain을 고정하고, 탐색 결과와 채택 결과를 분리해야 한다.
- Minervini 개선 1순위: 현재 RS는 KOSPI 대비 6개월 +15%p 근사다. `ibd_rs_daily` 또는 PIT 유니버스 내 12/6/3/1개월 가중 RS percentile을 만들고, 신호일 현재 universe rank를 저장해야 한다.
- Minervini 개선 2순위: VCP는 scipy 피크/트러프 기반이지만 일봉 100일 구간만 본다. 주봉 VCP, pivot, volume dry-up, ATR contraction을 별도 evidence row로 저장하고 미래 피크/저점 확정 사용 여부를 정적 테스트해야 한다.
- Minervini 개선 3순위: SEPA 재무조건은 EPS/매출/OPM/ROE만 본다. 추정 EPS revision·surprise·기관 수급·업종 RS를 meta-label로 붙여 “신호가 떠도 매수할 장/종목인가”를 2차 필터로 학습한다.
- 전략센터 개선 1순위: golden_cross/contract_momentum처럼 OOS 초과수익이 보이는 전략을 바로 실전 승격하지 말고 DSR/PBO를 통과하도록 **walk-forward 재선정**과 파라미터 민감도 표를 자동 산출한다.
- 전략센터 개선 2순위: `build_meta_strategy_plan.py`의 구식 v전략 비중 로직을 최신 27개 전략 registry/governance 기반으로 교체한다. 국면별 비중은 평균수익이 아니라 OOS alpha, MDD, turnover, capacity, DSR, forward hit-rate로 계산한다.
- 전략센터 개선 3순위: paper/live 신호 어댑터를 백테스트 신호와 golden fixture로 100% 일치시킨다. “매일 전체 과거를 다시 돌려 최신 신호 추출” 방식은 repaint 위험이 있으므로 금지한다.
- 수익률 개선의 다음 실험: 기존 신호 위에 meta-labeling(진입/스킵), 시장국면별 strategy gating, 보유 중 sell/trim 룰(earnings miss, RS 붕괴, 거래량 없는 하락, 이벤트 소멸)을 우선 추가한다. 새 매수전략 추가보다 매도·비중축소 엔진이 기대효과가 크다.
- 완료 기준: ① 채택 후보 최소 1개가 DSR>0.95/PBO<0.5/OOS alpha>0/t12m EV>0 통과 ② 60일 shadow·완결거래 20건 ③ 백테스트·paper 신호 fixture 일치 ④ 전략센터 화면은 검증등급·suite hash·비용포함 성과만 표시.

## meta strategy plan 최신 governance 기반 교체 (Codex, 2026-09-30)

- 위 개선 2순위 즉시 착수. `scripts/build_meta_strategy_plan.py`를 구식 고정 전략 목록(`v5/v_trend/v11...`)과 5구간 평균 성과 기반에서 최신 `selected_run_registry` 27개 전략 + `strategy_adoption_review_20260925.csv` + 최신 가격/데이터 감사 JSON 기반으로 교체했다.
- 새 산출물: `scratch/meta_strategy_plan_latest.json`(정본)과 기존 호환 경로 `scratch/meta_strategy_plan_2026-05-19.json` 둘 다 기록. 실행 검증 완료.
- 정책은 fail-closed: 채택 리뷰(`adopt=True`) + price integrity + data availability를 모두 통과한 전략이 없으면 실전 비중은 `{"cash": 1.0}`으로 고정한다. 현재 결과도 live_weights=`cash 100%`.
- shadow 후보 비중은 `golden_cross 33.67%`, `contract_momentum 23.61%`, `v11 17.65%`, `v2 10.79%`, `v_trend 8.40%`, `v5 5.88%`. 이는 실전 매매 지시가 아니라 60일/20완결거래 전방검증용 후보 순위다.
- tier_counts: `shadow_priority=2`, `paper_observe=12`, `research_only=13`. `golden_cross`와 `contract_momentum`만 OOS alpha+최근 기대값+curve fidelity가 동시에 양호하나 DSR/PBO 미통과로 shadow에 제한.
- 다음 실제 수익률 개선 작업은 ① golden_cross/contract_momentum rolling walk-forward·파라미터 민감도, ② Minervini RS를 PIT 유니버스 percentile로 교체, ③ 기존 신호 위 meta-labeling(진입/스킵) 학습 순서로 진행.

---

## `order_backlog` 파서 신뢰도 버그 수정 + 백필 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## `order_backlog` 파서 2차 버그 수정 + 백필 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## dart_cost_quarterly 단위 오류 수정 + 파서 cost_v2 승격 (Claude, 2026-09-30)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## P2 진행 — backlog_to_rev 재계산 + corporate_action_events validation (Claude, 2026-10-01)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

