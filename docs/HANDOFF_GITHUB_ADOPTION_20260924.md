# HANDOFF — GitHub 추천 20선 도입 검토 (2026-09-24, Claude)

- 입력: `~/Downloads/Stock_Dashboard_GitHub_Recommendations_20.pdf` (5쪽, "pykrx 기반 한국주식 시스템 확장 가이드")
- 이 문서는 **검토·계획만** 담는다. 이 세션은 코드/DB/패키지를 변경하지 않았다. 구현은 아래 순서대로 별도 세션이 진행한다.
- 관련 선행 문서: [docs/SYSTEM_REVIEW_20260924.md](SYSTEM_REVIEW_20260924.md) (오늘 발견된 데이터·전략 결함과 수정 이력)

---

## 0. 결론 요약

PDF의 전제("pykrx 수집 환경 구축 완료, pykrx를 중심 데이터 소스로")는 **우리 시스템에 맞지 않는다.** 우리 시스템은
KIS·키움·DART·marcap·FnGuide 기반 PostgreSQL 운영 DB, 44개 전략 백테스트 엔진, 가상/페이퍼 매매, 리스크게이트까지 이미 갖추고 있다.
따라서 20개 중 **라이브러리 대부분은 이미 있는 기능과 겹치고**, 실제로 빠져 있는 것은 **"검증·평가 계층"**이다.

| 우선순위 | 도입 대상 | 채워주는 공백 | 판정 |
|---|---|---|---|
| 1 | **QuantStats** | Sortino·Calmar·월별 수익·벤치마크(KOSPI) 대비·롤링 지표 표준 리포트 | 도입 |
| 2 | **Alphalens-reloaded** | 팩터 IC·분위수 수익·턴오버 — 44개 전략 신호 중 무엇이 실제 예측력이 있는지 | 도입 (핵심) |
| 3 | **vectorbt** | 청산 규칙·국면 필터 등 파라미터 대량 탐색 + walk-forward 분할 | 도입 (연구 전용) |
| 4 | **PyPortfolioOpt** (필요 시 Riskfolio-Lib) | 종목 선정 후 비중(HRP·리스크패리티·CVaR 한도) — 현재 균등/고정 예산 | 조건부 도입 |
| 5 | FinNLP/FinGPT **아이디어만** | 뉴스·공시·텔레그램 감성/이벤트 점수 — 모델 대신 이미 있는 LLM API 사용 | 참고 |
| 6 | Qlib / ML for Trading **아이디어만** | Alpha158식 피처, purged walk-forward, GBDT | 참고 |
| — | TA-Lib | 지표는 이미 자체 구현 → 검증용 테스트 오라클로만 | 선택 |
| — | pykrx, FinanceDataReader, OpenDartReader | 이미 설치·사용 중 (PDF 5·6단계는 이미 완료 상태) | 추가 작업 없음 |
| — | OpenBB, LEAN, Backtrader, Backtesting.py, bt, Zipline-reloaded, FinRL, skfolio | 기존 엔진과 중복·과중·라이선스·시기상조 | 도입 안 함 |

> **3차 갱신(§8·§9):** 「퀀트 시스템 고도화 전략 보고서」 검토 결과는 다음과 같다.
> - Qlib은 LightGBM 랭킹 위주로 부분 도입한다.
> - Alpha101은 일봉으로 계산 가능한 것만 골라 검증 후 편입한다.
> - 롱숏은 롱 전용으로 한정한다.
> - Riskfolio는 2차 옵션으로 둔다.
>
> 버전은 **Python 3.12 + numpy 2.2.6 + pandas 2.3.x로 운영·연구를 통일**하는 전환을 권장한다(§9). 지금 numpy 1.26에 묶인 원인은 pykrx 1.2.4의 `numpy<2` 고정이며, 코드 영향은 실측상 거의 없다.

---

## 1. 현재 시스템 사실 확인 (2026-09-24 실측)

| 항목 | 실측 결과 | 도입 판단에 미치는 영향 |
|---|---|---|
| pykrx 1.2.4 | 설치됨. **일별 OHLCV만 응답**, 투자자별 순매수·펀더멘털 → 0행, 시가총액·지수 → KeyError | "pykrx 중심" 전제 불가. 교차검증 보조로만 유지 (CLAUDE.md 섹션 9와 일치) |
| FinanceDataReader 0.9.110 | 설치, 18개 파일 사용 | 이미 도입. CLAUDE.md 데이터 우선순위상 검증용 |
| OpenDartReader 0.2.3 | 설치, 24개 파일 사용 | 이미 도입. 본 DART 적재는 자체 수집기+3키 로테이션 |
| Python / numpy / pandas | 3.11.15 / 1.26.4 / 2.3.3 | 신규 라이브러리가 numpy·pandas를 올리면 운영 서버가 깨질 수 있음 → **별도 venv 필수** (§4) |
| scipy / scikit-learn / statsmodels / numba / TA-Lib | **미설치** | QuantStats·Alphalens·PyPortfolioOpt·vectorbt 모두 의존성 추가 필요 |
| 백테스트 엔진 `backtest_common.py` (4,010줄) + `backtest_strategies/` 44개 | 수수료 0.015%·거래세 0.18%·시총별 슬리피지, PIT 재무 공시일, PIT 시총, 기업행위 조정계수, 상장폐지 결과, CAGR/MDD/Sharpe/손익비 | 이미 "운영 기준 엔진". **대체하지 말고 보강**할 대상 |
| `_calc_metrics` | CAGR, MDD, Sharpe(무위험 3%), 승률, 손익비. **Sortino·Calmar·월별 수익·벤치마크·롤링 없음** | QuantStats가 정확히 이 공백을 채움 |
| `backtest_runs` 3,252건 | `equity_json` 컬럼은 **전부 비어 있음**. 결과 JSON(`trades_json`) 안에 `equity_curve`가 있는 건 3,038건 중 926건(약 30%) | QuantStats 전에 **자산곡선 저장 표준화**가 선행 과제 |
| 팩터 검증(IC·분위수) | 코드상 spearman/rank IC는 3개 파일에만 부분 존재, 표준 도구 없음 | Alphalens 도입 가치 큼 |
| 포트폴리오 최적화 | efficient frontier·리스크패리티·HRP·CVaR 구현 **0건** (`portfolio_engine.py`는 현금원장·슬리피지만) | PyPortfolioOpt/Riskfolio 공백 |
| 연구 데이터 `strategy_feature_snapshot` | 189,561행, 79개 월말(2020-01~2026-07), ret_20/60/120d·dist_high_252·vol_ratio·supply_20d·heuristic/model score + forward 라벨 | Alphalens 입력으로 바로 쓸 수 있음. 단 **2026-08 이후 스냅샷 없음**(SQLite 동기화 중단 부류, SYSTEM_REVIEW §B2) |
| ML | `scripts/build_strategy_research_dataset.py`가 numpy로 직접 구현한 로지스틱 회귀 | Qlib 전에 GBDT·walk-forward부터 |
| 연구 거버넌스 | `research_governance.py`(OOS·look-ahead·생존편향 필드 필수), `run_registry.py`, `signal_experiment_ledger` 234건 | 신규 도구 산출물도 이 계약에 맞춰 기록해야 함 |
| 가상매매 성과 | 청산 307건, 승률 24.1%, 평균 -3.36%. KOSPI<MA60 진입 평균 -5.68% vs 위 -0.59% | 도구보다 **검증된 규칙**이 급함 — Alphalens/vectorbt로 근거 확보 후 반영 |

---

## 2. 도입 순서와 작업 지시 (구현 세션용)

### 단계 0 — 선행 조건 (도구 도입 전 반드시)
신규 검증 도구는 입력 데이터가 틀리면 틀린 결론을 더 그럴듯하게 만들 뿐이다. 아래가 끝났는지 먼저 확인한다.
1. SYSTEM_REVIEW §A·§D의 수정이 서버 재시작으로 반영됐는지(V10~V12, CFS/OFS 혼재, 기업행위 조정계수 이중적용, DEFAULT 유실).
2. `price_history` 9/14~9/18 수급 공백 백필 완료 여부(다른 세션 진행 중).
3. 기업행위 조정계수 품질 — TERP 다중 확정(예: 001140) 정리 전에는 **수정주가 기반 팩터 결과를 채택 근거로 쓰지 않는다.**
4. `strategy_feature_snapshot` 2026-08월말 이후 재생성 경로 확보(현재 SQLite→PG 동기화 의존, 스케줄러 미등록).

### 단계 1 — QuantStats: 성과 리포트 표준화 (소요 1~2일)
- **1-a 선행:** 모든 백테스트 엔진이 일별 자산곡선을 `backtest_runs.equity_json`에 저장하도록 `_save_result` 표준화.
  현재 30%만 `trades_json.equity_curve`에 존재하고 turnaround 등은 거래 목록만 있다.
  자산곡선 형식은 `[{date, equity}]` 하나로 통일한다. 가상매매(`peak_holding`/`peak_trade`)와 미국 페이퍼(`us_paper_*`)도 일별 평가액 시계열을 만든다.
- **1-b:** 연구 venv에서 `quantstats.reports.html(returns, benchmark=KOSPI)` 리포트를 생성한다.
  벤치마크는 `price_history`의 `^KS11`·`^KQ11`, 무위험수익률은 기존 3%와 맞춘다.
- 산출물은 `/Volumes/Realtek_NVME/stock_dashboard/reports/quantstats/<run_id>.html`에 두고(runtime 밖), API/프론트는 링크만 건다.
- **1-c:** `_calc_metrics`의 Sharpe와 QuantStats Sharpe를 대조해 차이 원인을 기록한다(모표준편차 vs 표본표준편차, 무위험 차감 방식).
- **완료 기준:** 상위 10개 전략 + 가상매매 전체의 리포트 생성, 지표 차이 기록.

### 단계 2 — Alphalens-reloaded: 팩터 검증 (소요 3~5일, 가장 중요)
- 목적: 44개 전략이 쓰는 개별 신호 중 무엇이 실제로 다음 수익을 예측하는지 확인한다(IC, 분위수 수익, 턴오버, 섹터별 IC).
- **입력 팩터(1차)** — 이미 DB에 있는 것만 쓴다.
  - `strategy_feature_snapshot`: ret_20/60/120d, dist_high_252, vol_ratio_20d, supply_20d_억, heuristic_score, model_score_6m/12m
  - 외국인·기관 5/20/60일 순매수 강도 ÷ 시총: `price_history.*_net_buy_amt`(백만원, ÷100=억원), `kiwoom_investor_daily`
  - 이벤트 점수:
    - 자사주 — `treasury_buyback.event_class` ∈ (취득결정, 취득결과, 신탁체결)
    - 특허·기술이전 — `dart_rd_patent_signals`, `exclude_reason IS NULL`만
    - 수주 — `dart_contracts`(`disclosed_at`은 YYYYMMDD)
    - 희석 — `dilution_events`
    - 실적 — `earnings_signals`(2026-09-24 재판정 후 활성분만)
  - 시장국면: `market_regime_daily` (8/07~9/24 공백은 SYSTEM_REVIEW A6 수정 후 재생성 필요)
- **가격(forward return)** 규칙:
  - 수정주가를 쓴다: `canonical_price_history_v` 또는 `stock_price_daily_adjusted_v`(조정계수 중복 제거 반영본).
  - `forward_max_ret_*`(기간 내 최대수익 라벨)는 낙관 편향이라 Alphalens forward return으로 **쓰지 않는다.**
- **시점 규칙(PIT):** 재무 파생 팩터는 `backtest_common._release_date` 기준 공시일 이후에만 사용하고, 이벤트는 `rcept_dt` 다음 거래일부터 반영한다.
- **유니버스:** `stock_universe` 최신 1행 기준(다른 세션이 history 분리 작업 중), 지수·ETF·선물은 CLAUDE.md 제외 필터로 뺀다. 상장폐지 종목을 포함해 생존편향을 통제한다.
- **산출:** 팩터별 IC 평균·IR·분위수 스프레드·턴오버를 `signal_experiment_ledger`에 기록한다(`research_governance.validate_research_record` 필드 충족: is_out_of_sample, lookahead_violations 등).
- **PDF의 Multi-Factor 가중치(0.20/0.15/…)는 쓰지 않는다.** 가중치는 이 단계의 IC/IR 결과(학습구간)로 정하고 검증구간에서 확인한다.
- **완료 기준:** 2020-01~2024-12 학습 / 2025-01~ 검증 분할에서 IC 부호가 유지되는 팩터 목록과, 유지되지 않는 기존 전략 신호 목록.

### 단계 3 — vectorbt: 규칙·파라미터 탐색 (소요 3~5일)
- **대상 (오늘 진단에서 나온 가설):**
  - KOSPI MA60 국면 필터
  - 진입 확인(당일 +3% 이상·거래량 동반)
  - +10~15% 도달 후 본전 스톱
  - GC 트레일 폭(-25%/-30%)
  - 종목당 동시 보유 한도
  - 이 규칙들은 다른 세션이 `virtual_trade_guards.py`에 환경변수 플래그로 넣는 중이므로, 그 **기본값의 근거를 만드는 작업**이다.
- **방식:** 연구 venv에서 PG 가격 패널(수정주가)을 읽어 `vbt.Portfolio.from_signals` 격자 탐색을 하고, walk-forward(롤링 학습/검증 창)로 과적합을 점검한다.
  비용은 `backtest_common`의 FEE_PER_LEG·SELL_TAX·시총별 슬리피지와 **동일하게** 넣는다.
- **채택 규칙:** vectorbt에서 좋은 조합은 반드시 **`backtest_common` 엔진으로 재현**된 뒤에만 운영에 반영한다.
  운영 기준 엔진은 하나로 유지한다(PIT·기업행위·상장폐지 처리가 거기에만 있다).
- 주의(2026-09-24 PyPI 조회로 정정):
  - vectorbt는 1.0·1.1이 새로 나와 활발히 배포 중이다(첫 판에 쓴 "유지보수 모드"는 틀린 설명).
  - 최신 1.1.0은 numpy≥2.4.6·pandas≥3.0.3·numba≥0.66을 요구한다. pykrx·Alphalens가 pandas<3이라 함께 쓸 수 없으므로 **1.0.0(pandas<3.0, numba≥0.60)을 쓴다**(§9).
  - PyPI에 라이선스 표기가 비어 있다. 과거 Apache-2.0+Commons Clause였으므로 도입 전 저장소 LICENSE를 확인한다.

### 단계 4 — PyPortfolioOpt (→ 필요 시 Riskfolio-Lib): 비중 결정 (소요 2~3일, 단계 2·3 이후)
- **현재:** 가상매매·KIS 페이퍼는 종목당 고정 예산이고, 리스크게이트가 변동성 기반 수량·섹터 35% 한도만 조정한다.
- **1차:** HRP·역변동성 비중을 `routes/kis_trading.py` 리스크게이트의 `volatility_sizing` 대안으로 **shadow 계산**(로그만)하고 기존 방식과 성과를 비교한다.
- **2차:** MDD가 핵심 문제라면 Riskfolio-Lib의 CVaR/CDaR 제약을 검토한다(cvxpy 의존).
- skfolio는 scikit-learn 1.6+·cvxpy 요구가 겹치고 기능이 중복되므로 보류한다.

### 단계 5 — 뉴스·공시 텍스트 점수 (소요 1주, 선택)
- FinGPT/FinNLP **모델은 도입하지 않는다**(GPU·용량 부담, 한국어 공시 적합성 낮음). 이미 venv에 있는 `anthropic`/`openai` SDK로 FinNLP식 프롬프트(감성·이벤트 유형·확신도)를 적용한다.
- 원천: `dart_disclosures` 공시명·본문, 텔레그램 수집(`telegram_collector` — 현재 크론 중단 상태, 선행 복구 필요), `detailed_analysis`.
- **PIT 필수:** 점수에 공시/메시지 타임스탬프를 붙이고 Alphalens(단계 2)로 예측력을 검증한 뒤에만 스코어에 편입한다.

### 단계 6 — ML 고도화 (단계 2 결과 이후)
- Qlib 전체 도입은 하지 않는다(자체 데이터 포맷·파이프라인이 우리 PG 구조와 중복).
- 대신 다음을 한다.
  - Alpha158의 피처 정의를 참고해 `strategy_feature_snapshot` 컬럼 확장을 검토한다.
  - 자체 로지스틱을 GBDT(LightGBM)로 비교한다.
  - "Machine Learning for Trading" 저장소의 purged/embargoed walk-forward CV를 적용한다.
- FinRL(강화학습)은 단계 1~6이 안정된 뒤 재검토한다(PDF도 마지막 단계로 둠).

---

## 3. 도입하지 않는 항목과 이유

| 프로젝트 | 이유 |
|---|---|
| pykrx(중심 소스로서) | 실측상 OHLCV 외 엔드포인트 실패. 운영 원천은 KIS·키움·DART·marcap. 교차검증 보조 유지 |
| FinanceDataReader / OpenDartReader | 이미 설치·사용 중. 추가 도입 작업 없음(데이터 우선순위상 검증용 원칙 유지) |
| OpenBB | 매우 무거움, 거시/해외 데이터는 `global_macro`·FRED 수집기와 중복, AGPL 계열 라이선스(도입 시 재확인) |
| QuantConnect LEAN | C# 엔진·자체 데이터 포맷. 주문 생애주기는 `live_orders`/`live_order_events`/리스크게이트로 이미 구현 — 주문 모델 설계 참고만 |
| Backtrader / Backtesting.py / bt / Zipline-reloaded | 운영 엔진과 기능 중복 → 결과 기준이 둘로 갈라짐. Backtrader는 장기간 정체, Backtesting.py는 AGPL(도입 시 재확인). Zipline의 거래일 캘린더/이벤트 개념만 참고 |
| TA-Lib | RSI·MACD·ATR·볼린저가 `signal_logic.py`·`backtest_common.py`에 자체 구현. C 라이브러리(brew) 필요. **선택 사항:** 연구 venv에서 자체 지표 값의 테스트 오라클로만 사용 |
| FinRL | 데이터·검증 체계가 먼저. 과적합 위험 최대 |
| skfolio | PyPortfolioOpt/Riskfolio와 중복, 의존성 무거움 |
| FinGPT (모델) | 로컬 LLM 파인튜닝 부담. API LLM으로 대체 |

라이선스 표기는 기억 기반이다. **실제 도입 전 각 저장소의 LICENSE를 확인**할 것(특히 AGPL/Commons Clause 여부).

---

## 4. 설치·운영 원칙 (모든 단계 공통)

1. **버전 정책은 §9를 따른다(2026-09-24 3차 갱신).** 운영·연구 모두 **Python 3.12 + numpy 2.2.6 + pandas 2.3.x**로 통일한다.
   venv는 두 개로 나누되(운영 경량 / 연구 무거운 라이브러리) 핵심 버전은 같은 constraints 파일로 고정한다.
   그러면 "연구 결과를 운영 코드로 옮길 때 버전 차이로 동작이 달라지는" 문제가 없어진다.
   업그레이드 완료 전까지는 기존 원칙대로 운영 venv(3.11·numpy 1.26.4)에 신규 라이브러리를 설치하지 않는다.
2. DB는 PostgreSQL만 쓴다(`POSTGRES_DATABASE_URL`). SQLite `stock.db` 폴백 금지(CLAUDE.md 섹션 2).
   가능하면 읽기 전용 계정으로 연결하고, 결과는 `signal_experiment_ledger`/`run_registry`에만 기록한다.
3. 대용량 산출물(HTML 리포트, 패널 캐시)은 `/Volumes/Realtek_NVME/stock_dashboard/reports/`·`data_cache/` 아래에 둔다.
4. 연구 스크립트는 **서버 프로세스 밖에서** 실행한다. 스케줄러 잡으로 넣을 경우 `_DB_WRITE_JOBS` 배타락 규칙과 장중(09:00~15:30) 부하를 고려한다.
   오늘 `price_history` 락 적체로 API timeout이 관측됐다(SYSTEM_REVIEW §D6).
5. 새 도구로 얻은 규칙은 **운영 엔진(`backtest_common`) 재현 → shadow 운용 → 채택** 순서를 지킨다.
6. 단위 규칙: `market_cap`은 억원, `*_net_buy_amt`는 백만원. 오늘만 단위 오류가 V10~V12·BigQuery·PSR에서 반복 발견됐다.

---

## 5. 대시보드 메뉴 반영 (PDF §7 대비)

새 메뉴를 늘리기보다 기존 탭에 붙인다(NAV_ITEMS 과밀 문제, CLAUDE.md 섹션 9).

| PDF 제안 메뉴 | 기존 위치 | 추가 내용 |
|---|---|---|
| 시장 현황 | `macro` / `market_regime` API | 국면 카드(재생성 후) |
| 전략 연구소 | `strategy_hub`·`backtest` | vectorbt 탐색 결과 요약, walk-forward 표 |
| Factor Lab | `signal_impact` 탭 확장 또는 신규 1개 | IC·분위수·턴오버·섹터별 IC (Alphalens 산출물 조회) |
| 포트폴리오 | `risk_gate`·`portfolio` | HRP/역변동성 shadow 비중 비교 |
| 성과/리스크 | `backtest` 결과 상세 | QuantStats HTML 링크 + Sortino/Calmar/월별 수익 |

---

## 6. 다음 세션 체크리스트

- [ ] 단계 0 선행 조건 4개 확인 결과 기록
- [ ] `research_venv` 생성, 설치 버전 고정(`requirements-research.txt`), 운영 venv 불변 확인
- [ ] 단계 1-a 자산곡선 저장 표준화 → CLAUDE.md 변경 이력 1~3줄
- [ ] 단계 1-b/1-c QuantStats 리포트 + Sharpe 대조
- [ ] 단계 2 Alphalens 학습/검증 분할 결과 → `signal_experiment_ledger`
- [ ] 단계 3 vectorbt 결과 → `backtest_common` 재현 → `virtual_trade_guards` 기본값 근거로 연결
- [ ] 단계 4 이후는 단계 2·3 결과를 보고 진행 여부 결정

---

## 7. 추가 제안 검토 (2026-09-24 2차 입력: OpenDartReader · FDR · VectorBT · Pandas-TA · Lightweight Charts)

### 7-1. 판정 요약

| 제안 | 판정 | 핵심 이유 |
|---|---|---|
| OpenDartReader(재무 → SQLite → 턴어라운드 스크리닝) | **추가 도입 없음 / SQLite 부분은 금지** | 이미 설치·24개 파일 사용. 운영 DB는 PostgreSQL이며 SQLite 적재는 CLAUDE.md 섹션 2 위반. 턴어라운드 스크리닝은 이미 있음(V11 `calc_turnaround_momentum`, `backtest_strategies/turnaround.py`, `earnings_signals` TTM_OP_INFLECT) |
| FinanceDataReader(글로벌 매크로·미국주식) | **추가 도입 없음(폴백 후보로만)** | 매크로는 `fred_collector`·`ecos_collector`·`yahoo_macro_collector` → `global_macro_data`(약 7.3만 행), 미국 주가는 `us_price_history`(약 400만 행, 9/23까지 갱신)로 이미 수집 중. FDR은 대부분 같은 원천(Yahoo·FRED 등)을 재포장한 것이라 새 정보가 없다 |
| VectorBT | **도입(§2 단계 3과 동일)** + "FastAPI로 대시보드에 쏘기"는 제한 | 격자 탐색·walk-forward에 적합. 버전은 **1.0.0**(pandas<3 지원; 1.1.0은 pandas≥3 요구라 제외, §9). 설치 가능 여부와 별개로 **API 서버 프로세스에서 요청마다 실행하지 않는다**(CPU·메모리) → 배치로 돌려 결과만 PG에 적재하고 FastAPI는 읽기만 |
| Pandas-TA | **§9 업그레이드 후 설치 가능(검증용 선택)** | 최신 0.4.71b0은 Python≥3.12·numpy≥2.2.6·pandas≥2.3.2·numba==0.61.2 요구 → 현재 운영 venv(3.11/numpy 1.26)에는 불가, §9 조합(3.12/numpy 2.2.6/pandas 2.3)에서는 가능. 지표는 이미 자체 구현이 있어 검증 오라클 용도 |
| Lightweight Charts(TradingView) | **도입 권장 — 이번 제안 중 운영 체감 효과가 가장 큼** | 현재 종목 차트는 App.jsx(약 15,760행 부근)에서 SVG로 직접 그림(10년=약 2,500캔들, 확대·이동·십자선 없음). 백테스트 매매는 표로만 표시. 5.2.1 Apache-2.0, 의존성 1개(fancy-canvas) |

### 7-2. OpenDartReader — 할 것 / 하지 말 것
- **하지 말 것:** 재무를 SQLite에 적재하는 것. `financial_data`·`cash_flow_data`의 유일한 write 경로는 자체 DART 수집기이고, 검증·`financial_fix_log`·run_id 규칙을 따라야 한다(CLAUDE.md "FnGuide급 신뢰도" 규칙).
- **할 수 있는 것:** 기존 수집기의 보조 도구로 쓴다(공시 원문·지분공시 조회 등). 이때 3키 로테이션(`dart_key_manager`)과 status=020(일일한도) 규칙을 우회하지 않는다.
- 턴어라운드 스크리닝에서 진짜 문제는 라이브러리가 아니라 **데이터 기준 혼재**였다. 오늘 고친 것: CFS/OFS 분기 혼재, 흑자전환을 2년 전과 비교하던 버그(SYSTEM_REVIEW §D2·§D3). 새 스크리닝 로직을 만들 때도 `signal_engine._load_quarterly_pl_pivot`(단일 report_type·연속 5분기)을 재사용한다.

### 7-3. FinanceDataReader — 폴백으로만
- 쓰는 곳: Yahoo·FRED 수집이 실패한 지표의 **대체 원천**, 또는 KRX 휴장·상장 정보 교차검증(`krx_security_reference_collector.py`가 이미 사용).
- 데이터 우선순위(CLAUDE.md 섹션 8)상 FDR/Naver 값은 검증용이며 본 테이블 write의 1차 원천으로 쓰지 않는다.
- 주의: FDR 한국 주가는 수정주가 기준이다. 오늘 다른 세션이 `fill_coverage_gaps_20260924.py`에서 "Naver 분할조정 기준"을 걸러내는 가드를 따로 둔 이유와 같다. 원시가격 테이블과 섞지 않는다.

### 7-4. VectorBT — 대시보드 연동 방식
- 흐름: `research_venv` 배치 → 결과(파라미터 격자별 CAGR/MDD/Sharpe·walk-forward 창별 성과)를 PG 연구 테이블에 적재 → 기존 `routes/backtest.py` 또는 `strategy_hub`에서 조회.
  신규 테이블을 만들면 PG DDL에 DEFAULT를 명시한다(SYSTEM_REVIEW §D1 재발 방지).
- 금지: 요청마다 서버에서 vectorbt 시뮬레이션 실행(수천 종목×격자는 CPU·메모리를 크게 써 오늘 관측된 DB/서버 지연을 키운다).
- 채택 규칙은 §2 단계 3과 같다(운영 엔진 `backtest_common` 재현 후 반영).

### 7-5. Pandas-TA — 쓰임새 한정
- §9 조합의 venv에서 **자체 지표의 테스트 오라클**로만 쓴다: `backtest_common._rsi`·`_chart_rsi14`, `signal_logic.py`의 MACD/ATR/볼린저가 같은 입력에서 같은 값을 내는지 대조한다.
- TA-Lib(§3)과 역할이 같으므로 **둘 중 하나만** 고른다. macOS 빌드 부담이 없는 Pandas-TA가 낫다.
- 신규 지표가 필요하면 운영 코드에는 자체 구현을 추가하고, 오라클로 검증한다.

### 7-6. Lightweight Charts — 도입 계획 (프론트엔드, 소요 2~4일)
**대상 화면 (우선순위 순)**
1. 종목 분석 가격 차트 — App.jsx `{/* candle chart */}`(약 15,760행) SVG 캔들을 교체한다.
   - MA5/20/60 라인, 거래량 히스토그램(별도 pane), 자본행위 ◆ 마커(`corporateActions`, `/api/.../corporate-actions`)를 옮긴다.
   - 기간 버튼(30일~10년)은 그대로 두고 확대·이동·십자선을 추가한다.
2. 백테스트 결과(`views/BacktestView.jsx`) — 거래 표(`entry_date`/`exit_date`)를 **차트 위 매수▲/매도▼ 마커**로 표시한다. 종목 선택 시 해당 종목 캔들 + 마커.
3. 가상매매·리스크게이트(`views/StrategyCenterView.jsx`, `RiskGateMonitorView.jsx`) — 보유 종목 진입가·손절선(price line)을 표시한다.

**구현 지침**
- 다른 차트(막대·파이·지표 추이)는 recharts(현재 12개 파일)를 유지하고, **캔들/시계열 가격 차트만** Lightweight Charts로 바꾼다. 두 라이브러리 혼용은 문제없다.
- 섹션 6 규칙에 따라 새 컴포넌트는 `frontend/src/views/PriceChart.jsx` 같은 별도 파일로 만들고 `React.lazy`로 로드한다. App.jsx에 직접 크게 추가하지 않는다. 헬퍼(fmtKrw 등)는 `utils.js`에서 import한다(2026-09-04 ReferenceError 사고).
- 데이터 기준:
  - 표시 가격은 `/api/.../price-history?basis=` 중 무엇을 쓸지 명시한다(`execution_raw` vs `confirmed_actions_adjusted`).
  - 오늘 `stock_price_daily_adjusted_v` 재생성·조정계수 중복 제거를 고쳤으므로 **수정주가 차트는 서버 재시작·야간 잡 이후 값으로 확인**한다.
  - 분할일 불연속이 보이면 데이터 문제이지 차트 문제가 아니다.
- 색상 규칙: 현재와 같이 양봉 빨강(#ef4444)·음봉 파랑(#3b82f6)(한국식)을 유지한다.
- 모바일: 컨테이너 폭 기준 `autoSize`와 터치 스크롤 옵션을 확인한다(모바일 진입 불가 사고 이력, CLAUDE.md 섹션 9).
- 라이선스: Apache-2.0이지만 저장소 NOTICE의 **TradingView 출처 표기 요구**를 확인해 차트 옵션 또는 화면에 반영한다.
- 빌드: `cd frontend && npm install lightweight-charts@^5 && npm run build`. 번들 크기 변화를 기록한다(현재 dist 2.2MB).

### 7-7. 갱신된 전체 도입 순서 (§2 + §7 통합)

| 순서 | 항목 | 영역 | 비고 |
|---|---|---|---|
| 0 | 선행 데이터 정리 | 데이터 | §2 단계 0 |
| 1 | 백테스트 자산곡선 저장 표준화 → QuantStats | 백엔드·연구 | §2 단계 1 |
| 2 | **Lightweight Charts — 종목 차트 교체 + 백테스트 매매 마커** | 프론트 | §7-6. 연구 venv와 무관하게 병행 가능 |
| 3 | Alphalens 팩터 검증 | 연구 | §2 단계 2 |
| 4 | VectorBT 규칙 탐색(배치) → 결과 테이블 → 전략 화면 | 연구·백엔드 | §2 단계 3, §7-4 |
| 5 | Pandas-TA 오라클로 자체 지표 검증 | 연구 | §7-5, 선택 |
| 6 | PyPortfolioOpt 비중 shadow | 백엔드 | §2 단계 4 |
| 7 | 텍스트 점수 / ML 고도화 | 연구 | §2 단계 5·6 |
| — | OpenDartReader·FDR | — | 추가 작업 없음(§7-2·7-3) |

---

## 8. 3차 입력 검토: 「퀀트 시스템 고도화 전략 보고서」(3쪽)

앞 두 입력과 겹치는 항목(OpenDartReader·FDR·VectorBT·Pandas-TA·Lightweight Charts)은 §7 판정을 그대로 따른다. 새로 나온 항목만 검토한다.
보고서도 "pykrx 기반"과 "SQLite/DB 적재"를 전제로 하는데, 두 가지 모두 우리 시스템에는 맞지 않는다(§1, CLAUDE.md 섹션 2).

| 항목 | 보고서 주장 | 판정 | 근거·조건 |
|---|---|---|---|
| Microsoft Qlib (LightGBM·ALSTM·Transformer, Top-K 랭킹) | 핵심 알파 엔진 | **부분 도입 — LightGBM 랭킹만, Qlib 프레임워크는 선택** | ① Qlib은 macOS wheel이 **Python 3.12까지만** 있다(0.9.7, 2025-08 이후 릴리스 없음). ② 자체 데이터 포맷(`.bin`)으로 변환해야 해서 PG 파이프라인과 이중화된다. ③ 가치의 대부분은 "피처 → GBDT 랭킹 → Top-K"이고, 이는 `lightgbm`(3.12~3.14 지원)만으로 `strategy_feature_snapshot`에서 바로 구현할 수 있다. Transformer/ALSTM은 데이터가 월말 79개 시점이라 과적합 위험이 커서 보류한다. Qlib은 Alpha158 피처 정의와 벤치마크 비교용으로 연구 venv(§9, 3.12)에 둘 수 있다 |
| Long-Short 포트폴리오 | 하락 예상 종목 공매도/헤지 | **롱 전용으로 한정** | 개인의 국내 개별주 공매도는 대차 제약으로 사실상 어렵고 운영 규정 부담이 크다. 하위 분위수는 **매수 제외 필터**로만 쓰고, 헤지는 인버스 ETF·현금 비중으로 검토한다. Alphalens 결과를 볼 때도 롱-숏 스프레드가 아니라 **상위 분위수의 절대·초과수익**으로 판단한다 |
| WorldQuant Alpha101 | 101개 수식 신호 | **선별 도입(연구 단계)** | 라이브러리가 아니라 공개 수식이라 의존성이 없다. 다만 다음 제약이 있다. ① 상당수가 VWAP·분봉·산업중립화를 전제로 하는데 우리 일봉(`price_history`)엔 VWAP가 없어 거래대금÷거래량으로 근사해야 한다. ② 대부분 단기(1~5일) 평균회귀 신호라 거래비용(편도 0.015%+매도세 0.18%+시총별 슬리피지 0.1~0.8%)에 약하다. 따라서 **일봉으로 계산 가능한 30~40개만 구현 → Alphalens로 비용 차감 후 IC 검증 → 통과분만 피처로 편입**한다. "수급에 선제 반응" 주장은 검증 전까지 채택 근거로 쓰지 않는다. 산업중립화는 `sector_large` 기준으로 한다(sector_mid 금지) |
| Riskfolio-Lib (MDD 최소화·평균분산) | 동적 자산배분 엔진 | **§2 단계 4의 2차 옵션 유지** | 7.3.0은 Python 3.10~3.14·scipy≥1.16.1·cvxpy 요구 → §9 조합에서 설치 가능. 평균분산은 기대수익 추정 오차에 민감하므로 **HRP·역변동성(PyPortfolioOpt) shadow → CDaR/MDD 제약(Riskfolio)** 순서로 간다. 효율적 전선을 대시보드에 "추천 포트폴리오"로 직접 노출하는 것은 투자 권유 오해 소지가 있으므로 연구 화면 표기로 둔다 |
| 목표 아키텍처(Ingestion→Feature→AI→Optimization→UI) | 5단계 파이프라인 | **방향은 동의, 구성요소는 교체** | Ingestion=KIS·키움·DART·marcap→PG(기존). Feature=`strategy_feature_snapshot` 확장 + 선별 Alpha101 + 자체 지표. AI=LightGBM 랭킹(Qlib 선택). Optimization=PyPortfolioOpt→Riskfolio. UI=Lightweight Charts(§7-6). 각 단계 사이에 **Alphalens/QuantStats 검증 게이트**를 둔다 — 보고서에 없는 부분이며 가장 중요하다 |

**§7-7 순서표에 추가:** 3단계(Alphalens) 직후 "3-b. Alpha101 선별 구현 + LightGBM 랭킹(학습 2020-01~2024-12 / 검증 2025-01~, purged walk-forward)"을 넣는다.
Qlib 프레임워크 자체는 7단계(ML 고도화)에서 선택 사항으로 둔다.

---

## 9. Python·핵심 라이브러리 버전 업그레이드 검토 ("파이썬 버전 올리면 되는 것 아닌가")

**결론: 맞다. 올리는 게 낫다.** 다만 올릴 대상은 파이썬 하나가 아니라 **"Python + numpy + pandas" 조합**이다. 최적 조합은 **Python 3.12 + numpy 2.2.6 + pandas 2.3.x**이며, 운영·연구를 이 버전으로 통일할 수 있다.

### 9-1. 왜 지금 3.11 / numpy 1.26에 묶여 있나 (실측)
- 파이썬 버전 때문이 아니라 **pykrx 1.2.4가 `numpy<2.0`, `pandas<3.0`을 고정**하기 때문이다(설치된 패키지 의존성 조회).
- pykrx 최신 **1.2.9는 `numpy>=2.0`, `pandas<3.0`**으로 바뀌었다 → pykrx를 올리면 numpy 2로 갈 수 있고 오히려 **numpy 2가 강제**된다.
- 그 밖의 운영 패키지 중 numpy 상한이 있는 것은 없다. pandas 상한은 pykrx·Alphalens(<3.0)가 있다(db-dtypes 최신 1.7.1은 <4.0으로 풀림).
- Python 3.11은 보안 지원이 2027-10에 끝난다. 3.12는 2028-10, 3.13은 2029-10까지다.

### 9-2. 버전별 호환성 (PyPI 메타데이터·macOS arm64 wheel 기준, 2026-09-24 조회)

| 구성요소 | 3.11 (현재) | **3.12 (권장)** | 3.13 | 3.14 (설치돼 있음) |
|---|---|---|---|---|
| 운영 의존성 115개(psycopg·pydantic-core·pyarrow·grpcio·lxml·greenlet·playwright·telethon 등) | ○ | ○ | ○ | ○ (cryptography·curl-cffi는 abi3/cp314 wheel) |
| numpy 2.2.6 (pandas-ta·numba 0.61.2가 요구하는 교집합) | ○ | ○ | ○ | **✕ (cp314 wheel 없음)** |
| numba 0.61.2 (pandas-ta가 `==`로 고정) | ○ | ○ | ○ | **✕** |
| pandas-ta 0.4.71b0 | ✕ (≥3.12) | ○ | ○ | ✕ (numba 때문) |
| vectorbt 1.0.0 (pandas<3) | ○ | ○ | ○ | ○ |
| alphalens-reloaded 0.4.6 · quantstats 0.0.81 · PyPortfolioOpt 1.6.0 | ○ | ○ | ○ | ○ |
| riskfolio-lib 7.3.0 · cvxpy 1.9 · scipy 1.16.x | ○ | ○ | ○ | ○ |
| lightgbm 4.7 · scikit-learn 1.9 · statsmodels 0.15 · torch 2.14 | ○ | ○ | ○ | ○ |
| **pyqlib 0.9.7** | ○ | **○ (최상위)** | ✕ | ✕ |
| OpenDartReader 최신 0.3.3 | 0.2.3 유지 | 0.2.3 유지 | ○(≥3.13) | ○ |
| TA-Lib 0.8.1 (이제 arm64 wheel 제공) | ○ | ○ | ○ | ○ |

→ 3.12가 **모든 후보 라이브러리가 동시에 설치되는 가장 높은 버전**이다.
- 3.13은 Qlib만 빠진다. Qlib을 쓰지 않기로 하면 3.13도 가능하고, OpenDartReader 0.3.x까지 쓸 수 있다.
- 3.14는 pandas-ta/numba 0.61.2가 막혀서 제외한다.

**권장 고정 조합** (`requirements-core.lock`으로 운영·연구 venv가 공유):

| 패키지 | 버전 |
|---|---|
| Python | 3.12.x |
| numpy | 2.2.6 |
| pandas | 2.3.x (<3.0) |
| numba / llvmlite | 0.61.2 / 0.44.0 |
| scipy | 1.15.3 또는 1.16.x |
| pyarrow | 24~25 |
| pykrx | 1.2.9 |

그 외 운영 패키지는 현재 버전을 유지하거나 같은 메이저 안에서 올린다.

### 9-3. 코드 영향 (실측)
- Python 3.14로 운영 코드 1,976개 파일을 메모리 내 컴파일: **문법 오류 0건**, 경고 1건(잘못된 이스케이프 `"\("` 1개 파일).
- numpy 2에서 제거된 API(`np.NaN`·`np.float_`·`np.product`·`np.in1d`·`np.row_stack`·`np.trapz` 등, 별칭 포함) 사용: **0건**.
- Python 3.12/3.13에서 삭제된 표준 모듈(distutils·imp·asyncore·cgi·telnetlib·imghdr 등)·`pkg_resources` 사용: **0건**.
- `datetime.utcnow()` 13개 파일: 3.12부터 DeprecationWarning만 나고 동작은 같다(후속 정리 대상).
- pandas는 2.3에 그대로 두므로 pandas 3의 Copy-on-Write·문자열 dtype 변경 영향은 없다.
- 그래도 확인할 곳:
  - numpy 2 스칼라 표현(`repr`이 `np.float64(1.0)`로 바뀜)이 문자열·JSON 직렬화 경로에 섞이는지.
  - numpy 2의 정수 승격 규칙 변경(NEP 50)이 int8/uint 연산에서 결과를 바꾸는지.
  - `db_compat`의 파라미터 바인딩(numpy 스칼라 → psycopg 어댑터)이 새 버전에서 그대로 동작하는지. 테스트 `test_db_compat_*`가 기준이다.

### 9-4. 전환 절차 (구현 세션용, 무중단·즉시 롤백 가능하게)
1. **Python 3.12 설치:** Homebrew에는 현재 3.11·3.14만 있다. `brew install python@3.12`(또는 pyenv) 후 설치 경로를 기록한다.
2. **새 venv를 옆에 만든다:** `runtime/.venvs/py312`에 `requirements.txt` + `requirements-core.lock`으로 설치한다.
   pykrx는 1.2.9로 올린다(numpy 2 요구). `playwright install chromium`을 다시 실행한다.
   기존 `runtime/venv`(3.11)는 그대로 둔다.
3. **검증 (새 venv로, 운영 서버와 별개 포트/프로세스):**
   - `pytest tests/` 전체(현재 3.11에서 445 passed가 기준).
   - `python -W error::DeprecationWarning`으로 import 스모크: `main`·`scheduler`·`signal_engine`·`tenbagger_engine`·`backtest_common`·collectors 전부.
   - 대표 API 응답 비교: `/api/signals/v10~v12`·`/api/earnings-signals/stats`·백테스트 1건을 3.11 결과와 **숫자 동일성** 대조.
   - pykrx 1.2.9를 쓰는 9개 파일(`data_collector.py`·`kis_client.py`·`collectors/krx_collector.py` 등)의 호출이 같은 결과를 내는지 확인.
   - Telethon 세션 파일·BigQuery(비활성) import 확인.
4. **전환:** 장 마감 후, `runtime/venv`를 심볼릭 링크로 바꿔 `.venvs/py312`를 가리키게 한다.
   - 89개 파일·크론 11줄·launchd 15개 plist가 `venv/bin/python` 경로를 참조하므로 **경로 자체는 바꾸지 않는다**.
   - 기존 3.11 venv는 `.venvs/py311`로 보존한다.
   - 서버는 `scripts/safe_restart_backend.sh`로 재시작하고, launchd 잡들은 다음 실행 주기에 자동으로 새 환경을 쓴다.
5. **롤백:** 심볼릭 링크를 `.venvs/py311`로 되돌리고 재시작한다(1분 이내).
6. **연구 venv:** 같은 lock 파일로 `/Volumes/Realtek_NVME/stock_dashboard/research_venv`(3.12)를 만들고 연구용 패키지를 추가한다.
   연구용: vectorbt 1.0.0, pandas-ta 0.4.71b0, alphalens-reloaded, quantstats, PyPortfolioOpt, riskfolio-lib, lightgbm, scikit-learn, statsmodels, (선택) pyqlib 0.9.7·torch.
   - 운영 venv에 연구 패키지를 넣지 않는 이유는 버전 불일치가 아니라(같은 lock) **설치 크기·import 시간·서버 안정성** 때문이다.
   - 운영에 필요한 것(예: QuantStats 리포트 생성)만 선별해 운영 venv에 추가한다.
7. 완료 후 CLAUDE.md 섹션 1(구조)·7(환경)과 변경 이력에 버전·경로를 기록한다.

### 9-5. 하지 말 것
- pandas 3.x로 올리지 않는다. pykrx·Alphalens가 `pandas<3`이고, pandas 3의 Copy-on-Write·기본 문자열 dtype 변경은 1,976개 파일 전수 점검이 필요한 별도 프로젝트다.
- Python 3.14로 운영을 올리지 않는다(numba 0.61.2/pandas-ta 불가). 3.13으로 가려면 Qlib 포기를 먼저 결정한다.
- 기존 venv에서 `pip install -U` 방식으로 제자리 업그레이드하지 않는다(롤백 불가, 컴파일된 확장 불일치).
