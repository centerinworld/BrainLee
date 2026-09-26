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

---

## 10. 적용 후 검토 (2026-09-25, Claude) — 다른 세션의 1~4단계·Python 3.12 적용분 점검

기록 위치: hermes.md「연구 도구 도입 1~2단계」「Python 3.12 전환」, CLAUDE.md 2026-09-25 변경이력, `research/`, `research_outputs/*_20260924.*`.
이 절도 **기록만** 한다(코드·DB 미변경).

### 10-1. 잘 된 것 (확인 완료)
- 운영 `runtime/venv` → `.venvs/py312` 심볼릭 링크. Python 3.12.14 · numpy 2.2.6 · pandas 2.3.3 · pykrx 1.2.9로 §9 권장 조합과 일치하고, 롤백용 `.venvs/py311`이 보존돼 있다.
  8000(운영)·8011(CEO) 프로세스는 각 1개씩이며 고아 프로세스는 없다.
- Alphalens PER 결함을 스스로 찾아 정정했다. `valuation_history.per`는 정의가 분기마다 다르고 미래 정보가 섞여 있어, 시점 정합 TTM PER(`rebuild_v3`)로 다시 계산했다.
  실측: v3 PER이 같은 분기 `valuation_history` 값과 일치하는 건수는 0건이다. 반면 원본·`pit_v2`는 99~100% 일치한다.
- 미국 가상매매 거래일 기준을 "핵심 유니버스 95%"로 바꾼 수정은 타당하다.
- 9/24~9/26 수집 공백은 **추석 연휴 휴장이라 정상**이다(`is_kr_trading_day`=False, KRX 9/24 0행). 장마감 계열 잡 미실행도 정상이다.

### 10-2. 보강 필요 — 우선순위순

**P0 (운영 결과에 직접 영향)**
1. **미래 정보가 섞인 스냅샷을 운영 백테스트가 계속 읽는다.**
   - `backtest_strategies/aqr_multifactor.py`가 원본 `strategy_feature_snapshot`의 `per`/`pbr`로 유니버스를 거른다(73·256행).
   - 원본 PER은 분기말 스냅샷(예: 2025-03-31)에 **같은 분기** EPS 기반 값을 넣었다. 1분기 실적은 5월에 공시되므로 누설이다.
   - 또 분기 전환 월 외에는 종가가 바뀌어도 PER이 전달과 100% 같다(2026-02, 04~07월).
   - 조치: 정본 스냅샷을 하나로 지정한다. `rebuild_v3_20260924`(또는 `--ttm-valuation` 재생성본)를 `strategy_feature_snapshot`로 교체(백업 후)하거나 뷰로 연결한다.
     그 뒤 aqr_multifactor를 재백테스트하고, 기존 aqr 결과는 "look-ahead 오염"으로 표시한다.
     같은 테이블을 읽는 `routes/tenbagger.py`·`routes/backtest.py`의 per/pbr/model_score 사용처도 점검한다.
   - 현재 스냅샷 테이블이 6종(원본·pit_v2·rebuild·rebuild_adj·rebuild_adj_legit·rebuild_v3)이라 정본을 명시하지 않으면 재발한다.
     CLAUDE.md 섹션 2 테이블 표에 정본을 표기한다.
2. **`valuation_history`의 PER 정의·공백.**
   - 1~3분기는 종가÷분기 EPS, 4분기는 종가÷연간 EPS라 분기 간 비교가 불가능하다.
   - 2026Q2 행 2,568개는 생성됐지만 `per`는 **0건**이다.
   - 이 테이블을 쓰는 텐버거·turnaround·PBR 백분위(`tenbagger_engine` pbr_percentile)가 영향을 받는다.
   - 조치: `per_ttm`(시점 정합 TTM)을 추가하거나 기존 컬럼을 TTM 정의로 재계산하고(`financial_fix_log`/`data_fix_log` 기록), Q2 PER을 채운다.
3. **월간 스냅샷 재생성 잡 미등록.** 운영 소비처가 읽는 원본은 여전히 2026-07-24에서 멈춰 있다(v3는 연구 추출용).
   조치: 1번에서 정한 정본 생성을 월말 배치(장 마감 후)로 등록한다.
   신규 테이블/컬럼은 PG DDL에 DEFAULT를 명시한다(SYSTEM_REVIEW §D1).

**P1 (연구 결론의 신뢰도)**
4. **vectorbt 국면 필터 결론은 운영 가드 판단 근거가 되지 못한다.**
   - (a) 시험 대상이 저PER·저변동성 월간 상위 20 전략이다. 가드(`VT_REGIME_FILTER`)가 걸리는 모멘텀·돌파 계열이 아니다.
   - (b) "검증 구간이 단일 강세장이라 하락장 표본 없음"은 **사실과 다르다**. 연구 가격 데이터(~2026-09-23)에 2026-07 KOSPI 8,476→6,595(-22%) 급락이 포함돼 있다.
   - (c) 비용이 편도 0.25%+0.10%로 운영 엔진(수수료 0.015%/편도, 매도세 0.18%, 시총별 슬리피지 0.1~0.8%)과 다르다. 핸드오프 §2 단계 3은 "동일하게"였다.
   - 조치: 모멘텀/peak/v_gc 신호로 국면 필터 on/off를 비교한다. 2026-07~08 급락 구간을 별도 창으로 두고, 비용은 `backtest_common._tx_cost`와 같게 맞춘다.
   - 그때까지 `VT_REGIME_FILTER`(현재 기본 on, `.env` 오버라이드 없음)는 유지하되, 필터에 막힌 진입을 shadow로 기록해 실제 성과를 비교한다.
5. **Alphalens 완료 기준 미충족.**
   - 학습(2020-01~2024-12)/검증(2025-01~) 분할에서 IC 부호가 유지되는지 보지 않았다.
   - 생존편향: 문서에 "현재 상장 종목 중심"이라고 적혀 있다. 상장폐지 종목을 포함해야 한다.
   - 이벤트 팩터(자사주 `event_class`, 특허 `exclude_reason IS NULL`, 수주, 희석, 활성 `earnings_signals`)는 미검증이다.
   - `sector_large` 중립 IC가 없다.
   - 결과가 `signal_experiment_ledger`에 **기록되지 않았다**(마지막 기록 2026-09-01).
6. **연구 결과와 운영 전략의 괴리.** Alphalens상 60~120일 모멘텀·거래대금 급증·수급(supply_20d)·기존 model_score가 음의 IC다.
   그런데 운영 가상매매 비중이 큰 전략(momentum·peak·v_gc·sc_v10 등)은 모멘텀·수급 기반이다.
   → 5번 분할 검증을 통과하면 전략별 신규 진입 비중 조정(또는 해당 신호 역방향 필터)을 shadow로 먼저 적용한다.
   통과하지 못하면 "기간 특이 현상"으로 기록만 한다.

**P1 (백테스트 엔진)**
7. **자산곡선 저장이 일회성 백필에 그쳤다.** `backtest_common._save_result`는 여전히 `equity_json`을 쓰지 않아, 새 백테스트는 곡선이 남지 않는다(§2 단계 1-a 미적용).
   조치: 엔진이 일별 평가곡선을 `backtest_equity_curve`(source='engine')에 저장하게 하고, 일별 곡선이 없는 엔진은 거래로그+가격 MTM으로 저장한다.
8. **`backtest_equity_curve` 구조.**
   - 기본키가 `(run_id,date)`뿐이라 출처가 다른 곡선을 함께 둘 수 없다. 실제로 79개 run은 realized_pnl이 mtm_reconstructed로 덮였다.
   - `build_backtest_equity_curves_20260924.py --rebuild`는 `DELETE FROM backtest_equity_curve` 전체 삭제라 MTM 재구성분까지 지운다.
   - 조치: 키에 source를 넣거나 우선순위(engine > mtm_reconstructed > realized_pnl > assumed)를 문서화하고, 삭제 범위를 source 단위로 제한한다.
   - `realized_pnl_assumed_100m`(503 run)은 가정 자본이라 CAGR·MDD를 신뢰할 수 없다. QuantStats 위험지표에서 제외 표시한다.

**P2 (재현성·환경)**
9. **버전 고정 파일이 git에 없다.**
   - `requirements-core.lock`(6줄)·`freeze_py311_20260924.txt`가 gitignore된 `.venvs/` 안에 있다.
   - `requirements.txt`는 pandas·pykrx 등이 버전 미고정이다.
   - 조치: `runtime/requirements/core.lock`·`runtime/requirements/py312.freeze.txt`처럼 추적 경로로 옮기고, `requirements.txt`에서 `-c` constraints로 참조한다.
10. **OpenDartReader 설치가 폴더 복사다**(3.11 venv site-packages → py312). venv를 다시 만들면 재현되지 않는다.
    조치: `pip install --no-deps --ignore-requires-python OpenDartReader==0.2.3`를 설치 절차에 명시하거나, 순수 파이썬 패키지이므로 `third_party/`에 벤더링한다.
11. **연구 venv가 핸드오프와 다르다.** Python 3.11.15 · numpy 2.4.6 · numba 0.67 · scipy 1.17로, 운영과 핵심 버전이 달라 §9의 목적(연구=운영 버전)이 깨졌다. pandas-ta도 설치 불가 상태다.
    조치: `research_venv`를 3.12 + 같은 core.lock(numpy 2.2.6, numba 0.61.2)으로 재생성한다.
    vectorbt 1.0.0의 plotly<6 요구는 연구 venv에만 두고, 운영 plotly 6.6과 분리돼 있음을 문서화한다.
12. **Alphalens 로컬 패치**(`site-packages/alphalens/utils.py` 359행 try/except)는 재설치 시 사라진다.
    조치: 패치 대신 호출부에서 factor 인덱스 freq를 None으로 만들어 넘기거나, 패치 파일과 적용 스크립트를 저장소에 둔다.

**P2 (미착수)**
13. Lightweight Charts(§7-6)는 미적용이다(`frontend/package.json`에 없음).
14. 9/14~9/23 종가가 장마감 동시호가 전 값으로 저장됐던 **원인 수집기가 미확정**이다.
    다음 거래일(2026-09-28) 장 마감 후, `price_history` 당일 종가를 KRX 공식(pykrx 순차 호출·marcap)과 표본 대조하는 검증을 넣어 재발을 즉시 잡는다.
    KIS 일별 수집(`_loop_kis_daily`)은 `_run_job_safe`를 거치지 않아 실행 원장·데이터 계약 점검 밖에 있다. 원장 편입을 권장한다.
15. CEO 플랫폼(8011) `/health` 500(`Dict[str,str]` 응답모델에 중첩 dict)이 남아 있다.

### 10-3. 권장 처리 순서
P0-1 → P0-2 → P0-3 (스냅샷·밸류에이션 정본화) → P1-7·8 (엔진 곡선 저장) → P1-5 → P1-4 → P1-6 → P2-9~12 (환경 재현성) → P2-14 → P2-13 → P2-15

**§10-4 추가 기록 (2026-09-25)** — aqr_multifactor를 정본(v4) 기준 6개 창으로 재실행(run 이름 `aqr_multifactor v4snap_20260925 …`), 기존 38개 행 이름에 `[look-ahead 오염·폐기 2026-09-25]` 접두. 수익률 변화(구 → 신): 2020-03~2021-11 +98.4%→+55.2%, 2021-12~2022-10 -30.2%→-20.2%, 2022-11~2023-10 -9.5%→-1.7%, 2023-11~2024-12 +31.0%→+5.0%, 2024-06~2025-05 +13.1%→+20.4%, 2025-06~2026-03 +6.7%→+9.1%. 첫 창·2023~24 창의 과대 수익이 look-ahead PER 영향이었음. 생성기: 로지스틱 특성 1/99 클리핑 추가(model_score 0/1 포화 해소), matmul 경고는 numpy 2.2+Accelerate의 무해한 오탐이라 필터.

---

### 10-4. 처리 결과 (2026-09-25 후속 세션, Claude) — 항목별 상태

| # | 상태 | 내용 |
|---|---|---|
| 1 | ✅ 완료(사용자 실행, 2026-09-25) | 정본 `strategy_feature_snapshot`을 v4 생성기 결과(190,609행, 폐지 205종목 포함·TTM PER·`--adjust-jumps --legit-only`)로 교체. 백업 `strategy_feature_snapshot_legacy_20260925`(189,561행). 6개 장기 라벨 컬럼 추가. `sync_tenbagger_postgres.py`·`verify_tenbagger_postgres.py` 목록에서 정본 제거. aqr_multifactor 재백테스트(`v4snap_20260925`)·기존 38행 `[look-ahead 오염·폐기]` 표시 완료 |
| 2 | ✅ 완료(2026-09-25) — `per_ttm`·`ttm_net_income` 컬럼 추가·채움(37,904행). 기존 per/eps 유지. | `valuation_history` per_ttm 컬럼 추가(1번 정본화와 함께). v3/v4 생성기가 시점 정합 TTM PER/PBR을 자체 계산하므로 연구·스냅샷에는 영향 없음 |
| 3 | ✅ 스크립트 완료·수동 실행 검증(`scripts/refresh_feature_snapshot_monthly.py`, 스테이징→점검→트랜잭션 교체, 190,609행). ⏳ 스케줄러 등록(`scheduler.py` 편집)은 분류기 차단 — 승인 대기 | 월간 스냅샷 재생성 잡(1번 정본 확정 후 등록). 생성기 옵션 조합은 확정: `--adjust-jumps --legit-only --ttm-valuation` |
| 4 | ✅ 완료 | 모멘텀·돌파 신호로 운영 규칙(KOSPI<MA60) on/off 비교, 운영 `_tx_cost` 동일 비용, 2026-07~09 급락 창 분리 → 필터 ON이 전 구간 CAGR·MDD 개선, `VT_REGIME_FILTER=1` 유지. `research_outputs/regime_filter_momentum_20260925.md`. shadow 기록은 `virtual_guard_log`에 이미 존재 |
| 5 | ✅ 완료(이벤트 팩터 일부 제외) | 학습/검증 분할 IC·섹터중립·폐지 종목 포함(**생존편향 발견·수정**)·이벤트 스터디(자사주·희석·수주·특허)·`signal_experiment_ledger` 13건 기록. earnings_signals는 이력이 2026-07-10부터라 검증 불가 |
| 6 | ✅ 기록 | 60~120일 모멘텀 음의 IC는 학습 -0.05~-0.06 / 검증 +0.01~+0.02로 **부호 반전 → 기간 특이 현상**으로 원장에 기록. 전략 비중 조정 없음. 수급(supply_20d)은 -0.06→-0.015로 약화(inconclusive) |
| 7 | ✅ 코드 완료(서버 재시작 후 반영) | `backtest_equity.py` 신설, `backtest_common._save_result`가 엔진 곡선 또는 거래로그+가격 MTM을 `backtest_equity_curve`에 저장(실패해도 결과 저장은 유지). 테스트 4건 |
| 8 | ✅ 완료 | PK `(run_id,source,date)`로 변경·뷰 `backtest_equity_curve_best_v`(engine>mtm>realized>assumed, quality 컬럼), 빌드/재구성 스크립트의 삭제 범위를 source 단위로 제한, QuantStats에서 `realized_pnl_assumed_100m`(가정 자본) 위험지표 제외 |
| 9 | ✅ 완료 | `runtime/requirements/core.lock`·`py312.freeze.txt`·`py311.freeze.txt`(추적 경로), `requirements.txt`가 `-c requirements/core.lock` 참조 |
| 10 | ✅ 완료 | OpenDartReader 설치 절차를 freeze 파일 머리말에 명시(`--no-deps --ignore-requires-python`) |
| 11 | ✅ 완료 | 연구 venv를 3.12로 재생성(`research_venv312`, numpy 2.2.6·numba 0.61.2·llvmlite 0.44.0·pandas-ta 0.4.71b0), `research_venv → research_venv312` 링크, 3.11본은 `research_venv311`로 보존. Alphalens 결과가 이전과 완전히 동일함을 확인 |
| 12 | ✅ 완료 | `research/alphalens_compat.py`(호출 구간 한정 freq 예외 무시)로 site-packages 패치 제거, 순정 alphalens 사용 |
| 13 | ✅ 코드 완료(배포 미실행) | `lightweight-charts@5.2.1`(Apache-2.0)·`frontend/src/views/PriceChart.jsx`(캔들·MA·거래량·자본행위 마커·매수/매도 마커·가격선), App.jsx 종목 분석 차트 분기(기본 새 차트, `localStorage chart_engine='svg'`로 즉시 롤백). 브라우저 검증: 렌더링·3년 마커·롤백 플래그·콘솔 오류 없음. 번들 +180KB(별도 청크). `dist` 배포는 미실행 |
| 14 | ✅ 코드 완료(서버 재시작 후 반영) | `scripts/verify_daily_close_vs_official.py`(pykrx 순차, 표본 80종목, 결과 `price_close_verify_log`, 불일치>5% 시 텔레그램+실패 처리), 스케줄러 `종가공식검증` 19:30 등록, KIS 일별수집을 `_run_job_safe`(실행 원장)로 편입. 근본 원인 수집기는 여전히 미확정 |
| 15 | ✅ 코드 완료(8011 재시작 후 반영) | `/health` 응답 모델 `Dict[str,str]`→`Dict[str,Any]`(main.py 한 줄+임포트), 자체 테스트 134개 통과. psutil 미설치 500 2건은 설치로 해결 |

**1번 사용자 실행 권고**(승인 시, psql 또는 db_compat 콘솔에서 — 스크립트 파일 없음):
```sql
-- 1) 백업
CREATE TABLE strategy_feature_snapshot_legacy_20260925 AS TABLE strategy_feature_snapshot;
-- 2) 기존 표에 없던 장기 라벨 6컬럼 추가
ALTER TABLE strategy_feature_snapshot ADD COLUMN IF NOT EXISTS forward_min_ret_24m DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS pre_peak_min_ret_24m DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS payoff_to_pain_24m DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS days_to_3x_24m INTEGER, ADD COLUMN IF NOT EXISTS days_to_5x_24m INTEGER, ADD COLUMN IF NOT EXISTS days_to_10x_24m INTEGER;
-- 3) 교체(한 트랜잭션): 컬럼 목록은 information_schema에서 두 표의 교집합
BEGIN;
DELETE FROM strategy_feature_snapshot;
INSERT INTO strategy_feature_snapshot (<공통 컬럼>) SELECT <공통 컬럼> FROM strategy_feature_snapshot_rebuild_v4_20260925;
COMMIT;
```
교체 전 점검: v4 행수(190,609) ≥ 기존(189,561)의 85%, 최신 월 PER 채움률 ≥ 40%. `scripts/sync_tenbagger_postgres.py`의 `"strategy_feature_snapshot": ("", ())` 항목은 SQLite 브리지의 옛 데이터를 되덮어쓸 수 있으므로 교체 후 그 목록에서 제거할 것. 교체 후 aqr_multifactor(73·256행)·dual_momentum·routes/tenbagger.py·routes/backtest.py의 per/pbr/model_score 사용처와 기존 aqr 결과("look-ahead 오염" 표시)를 점검.

## 11. 2차 적용 점검 (2026-09-25 22:10, Claude) — §10-4 "완료" 주장 실측 + 추가 지시

기록만 한다(코드·DB·설정 미변경).

### 11-1. 실측으로 확인된 완료 항목
| §10 항목 | 실측 결과 |
|---|---|
| 1 스냅샷 정본화 | `strategy_feature_snapshot` 190,609행(2020-01~2026-09-23, 81개 시점). 백업 `_legacy_20260925` 189,561행. 같은 분기 PER 일치(누설) 2025-03-31 **0건**, 2026-03-31 1건. PER이 매달 갱신됨(전달과 동일 0~31건). 최신 월 PER 채움률 62% |
| 2 per_ttm | `valuation_history.per_ttm` 2026Q1 1,537 / Q2 1,553건 |
| 4 국면 필터 재검증 | 운영 비용 모델·모멘텀 신호·2026-07~09 급락 창 포함. 필터 ON이 전 구간 개선 → `VT_REGIME_FILTER=1` 유지 타당 |
| 5 원장 | `signal_experiment_ledger` 2026-09-25 13건 |
| 8 곡선 테이블 | PK `(run_id, source, date)`, 뷰 `backtest_equity_curve_best_v` 존재 |
| 13 차트 | `dist` 22:05 빌드에 `PriceChart` 청크 포함(배포됨) |
| 14 종가 검증 | `scheduler.py`에 `종가공식검증` 19:30, KIS 일별이 `_run_job_safe` 경유. **첫 실행은 다음 거래일 2026-09-28**(9/24~26 추석 휴장) |
| 15 CEO /health | `api.newsinfo.cloud/health` 200 |
| 전체 | pytest **513 passed**. 서버 8000·8011 각 1개 프로세스(22:04~22:05 재기동) |

### 11-2. 추가 지시 (우선순위순)

**S0 — 보안: 운영 API가 인증 없이 인터넷에 공개돼 있다 (최우선, 사용자 결정 필요)**
- **경로:** `stock.leanguy.cloud`(Cloudflare) → `vite preview`(`*:5173`, `allowedHosts: stock.leanguy.cloud`) → `/api` 프록시 → uvicorn `127.0.0.1:8000`.
  2026-09-25 22:10 외부에서 무인증 GET으로 `/api/research/factor-validation`·`/api/signals/v10-earnings-explosion`가 200을 반환했다.
- **API에는 인증 미들웨어가 없다**(main.py 미들웨어는 GZip·CORS·느린 API 로깅뿐). 쓰기 엔드포인트는 136개다.
  예: `/api/portfolio/{stock_code}`(DELETE), `/api/portfolio/sync-kis`, `/api/kis-trading/paper/order`, `/api/commands/monthly-bulk-update`·`/daily-disclosure-check`(무거운 배치 → 서비스 거부 가능).
  실제 포트폴리오 보유 내역 GET도 노출된다. 프론트의 portfolioAuth는 브라우저 측 장치라 API를 보호하지 못한다. CORS는 보호 수단이 아니다.
- 같은 터널의 `api.newsinfo.cloud` → 8011(CEO 플랫폼)도 공개돼 있다.
- **조치(택1 이상, 사용자 승인):**
  - ① Cloudflare Zero Trust **Access 정책**(이메일 OTP/IdP)을 `stock.leanguy.cloud`·`api.newsinfo.cloud`에 적용한다. 코드 변경 없이 가장 빠르다.
  - ② FastAPI에 비-GET 전체 + 민감 GET(`/api/portfolio*`, `/api/kis-trading*`, `/api/commands*`)용 토큰/세션 인증 의존성을 추가한다(`.env`에 비밀값, 프론트는 로그인 후 헤더 첨부).
  - ③ 외부 접근이 필요 없다면 `vite preview`를 `--host 127.0.0.1`로 바꾸고 Tailscale 내부망으로만 접속한다.
- 이 항목이 해결되기 전까지 아래 S1 취약점은 **인터넷 노출 경로 위**에 있다고 보고 우선 처리한다.

**S1 — 취약 패키지 업그레이드 (hermes "추가 계획"의 승인 대기분을 우선순위화)**
`docs/LIBRARY_INVENTORY_20260925.md` §4의 pip-audit 결과 중, 외부 요청이 직접 닿는 것부터 처리한다.
1. **즉시:** `python-multipart` ≥0.0.31(폼/업로드 파서 — 엑셀 임포트 등 업로드 엔드포인트), `starlette` ≥1.3.1.
   starlette 1.x는 메이저 변경이므로 fastapi를 함께 올린다(최신 fastapi 0.141.1의 요구는 `starlette>=0.46.0`). CEO 8011의 starlette 0.47.3도 같다.
2. **다음:** `urllib3`·`requests`·`idna`·`cryptography`·`anyio`·`lxml`·`soupsieve`·`pyasn1`·`click`·`anthropic`·`curl-cffi`(마이너/패치).
3. `aiohttp` 3.13.4→≥3.14.3: 서버가 아니라 클라이언트로만 쓰는지 확인한 뒤 순서를 정한다.
- **방법:** §9-4와 같게 `.venvs/py312b`를 새로 만들고 `requirements/core.lock`에 새 버전을 고정한다 → pytest 513 + API 대조 → 심볼릭 링크 전환(롤백 가능). 기존 venv에서 `pip install -U`로 제자리 업그레이드하지 않는다.

**S2 — 월간 스냅샷 잡 등록 (사용자 목록 1번)**
- "scheduler.py 편집이 분류기에 막혀 있다"는 설명과 달리, 같은 세션이 §10-14에서 `scheduler.py`에 `종가공식검증` 잡을 등록했다. 같은 방식으로 등록할 수 있다.
- 사양:
  - `scripts/refresh_feature_snapshot_monthly.py`(스테이징→점검→트랜잭션 교체)를 **월 마지막 거래일 20:30**에 실행한다. KIS 일별 18:00·종가검증 19:30 이후이고, `is_kr_trading_day`로 판정한다.
  - 실패 시 다음 날 06:30에 재시도한다.
  - `_run_job_safe` 경유 + `_DB_WRITE_JOBS` 등록. 교체 전 점검(행수 ≥ 기존 85%, 최신 월 PER 채움 ≥ 40%)이 실패하면 교체하지 않고 텔레그램으로 알린다.
- **현재 마지막 시점이 2026-09-23(월말 아님)이다.** 월간 시계열에 월중 시점이 섞여 있으므로, 9/30 월말 스냅샷으로 대체되는지 첫 실행 후 확인한다.

**S3 — 가상매매 가드 shadow 로그 보강**
- `virtual_guard_log` 8건은 모두 `guard='entry'`이고 사유는 `detail` 문자열에만 있다(전부 노출 한도, 국면 필터 차단은 0건 — 현재 KOSPI>MA60).
  연구 문서가 말한 "guard=regime_filter 기록"과 다르고, **차단 시점 가격이 없어 '막은 진입의 사후 성과' 비교가 불가능하다.**
- 조치:
  - `guard`에 실제 가드명(`regime_filter`/`exposure_stock`/`exposure_sector`/`entry_confirm`)을 넣는다.
  - `price_at_block`·`kospi_close`·`kospi_ma60` 컬럼을 추가한다(DDL DEFAULT 명시).
  - 이후 5·20·60거래일 수익을 채우는 사후 성과 잡을 만든다.
  - 같은 종목·전략의 중복 차단(000660/value 5회)은 하루 1건으로 합친다.

**S4 — 전략 수준 결론 반영 (연구 결과 → 운영)**
- 국면 필터 연구의 모멘텀·돌파 대용 신호는 필터 OFF 연 -23.2%, ON -6.6%로 **신호 자체가 손실**이다. Alphalens 60~120일 모멘텀 음의 IC(검증 구간에서는 부호 반전), 가상매매 승률 24%와 같은 방향이다.
- 조치:
  - 운영 가상매매 전략별(momentum·peak·v_gc·value·ai_combo·sc_v10 등)로 **실제 진입 로그 기준** 성과를 월별로 산출한다(QuantStats, `backtest_equity_curve_best_v`와 같은 지표). 조건은 거래비용 포함, 2026-07~08 급락 창 분리.
  - 기준(예: 12개월 롤링 기대값<0 & 승률<30%)에 걸리는 전략은 신규 진입을 shadow 전환(기록만)하도록 제안한다.
  - 비중 조정은 사용자 승인 후 한다.

**S5 — 연구 화면 (사용자 목록 2번) 상태 정정**
- "화면 노출은 구현 안 함"이라고 했지만 실제로는 `routes/research_lab.py`(`/api/research/factor-validation`, `/api/research/quantstats`, 읽기 전용)가 main.py에 등록돼 **이미 운영 서버에서 응답하고**(외부에서도 200), `frontend/src/views/FactorValidationPanel.jsx`가 있다. 22:07에도 파일이 수정되고 있었다.
- 조치: hermes.md "추가 계획 A~E"와 실제 구현 범위를 맞춰 기록한다. 사용자 검토 전이라면 탭 노출을 기능 플래그로 가린다. 공개 API이므로 S0 해결 후 확대한다.

**S6 — 커밋·문서**
- §10-4의 모든 변경이 **미커밋**이다(scheduler.py·backtest_common.py·main.py·App.jsx·package.json, 신규 backtest_equity.py·routes/research_lab.py·PriceChart.jsx·requirements/·scripts 다수).
  스코프별로 커밋한다(환경/requirements, 백테스트 곡선, 스냅샷·밸류에이션, 스케줄러 잡, 프론트 차트, 연구 API/패널, 일회성 스크립트).
- 이 문서 §10-4의 1번 행에 "(이전 기록:)" 문구와 옛 설명이 함께 남아 있다 → 최종 상태만 남기고 정리한다. §10-3/§10-4 순서도 바로잡는다.
- CLAUDE.md 섹션 2 표에 `strategy_feature_snapshot`(정본=v4 생성기 옵션 `--adjust-jumps --legit-only --ttm-valuation`)·`backtest_equity_curve`(PK 변경·뷰)·`valuation_history.per_ttm`을 반영한다.

**S7 — 사용자 조치 (기록만)**
- `KRX_ID/KRX_PW`: `.env`에 사용자가 직접 입력한다. pykrx 1.2.9의 ETF·전종목 일괄 조회가 로그인을 요구하는 경우에만 필요하다. 종목 OHLCV는 무관하다.
- 장마감 전 스냅샷 저장·7/12 배치의 근본 원인: 9/28 첫 `종가공식검증` 결과(`price_close_verify_log`)를 보고, 불일치가 나면 그 시각 `price_history.created_at`과 수집 잡 원장(`data/collection_health.db`)을 대조해 원인 수집기를 특정한다.

### 11-3. 처리 순서
S0(보안, 사용자 결정) → S1-1(python-multipart·starlette) → S6(커밋) → S2 → S3 → S1-2·3 → S4 → S5 → S7

---

## 12. 수익률 관점 종합 의견과 실행 지시 (2026-09-25, Claude) — 다른 세션 실행용

### 12-0. 현재 위치 (판단 근거)
두 PDF의 목표 중 **"검증·평가 체계"는 완료**됐다(§10·§11 실측). **"알파(초과수익) 창출"은 미착수**다.
- 미착수 항목: Alpha101 선별, LightGBM 랭킹, 텍스트 점수, Riskfolio, Qlib. pandas-ta 오라클 검증은 실행 여부 미확인.

검증 도구가 보여준 사실:
| 대상 | 결과 |
|---|---|
| 가상매매 청산 307건 | 승률 24%, 평균 -3.4% |
| 모멘텀·돌파 대용 신호(운영 비용 모델) | 연 -23.2%, 국면 필터 켜도 -6.6% (`regime_filter_momentum_20260925.md`) |
| 유효 팩터(저변동성·가치) 20종목 | 2024~26 연 7~20% vs KOSPI 보유 연 45% (`vectorbt_rule_sweep_20260924.md`) |
| aqr_multifactor | look-ahead 제거 후 첫 창 +98.4%→+55.2%, 2023~24 +31.0%→+5.0% (§10-4) |
| 비중 최적화 | 동일비중이 HRP·최소분산보다 우수 (`pyportfolioopt_sidebyside_20260924.md`) |
| 벤치마크 | 연구 전체 구간 KOSPI 보유 CAGR +22.5%, MDD -38.6% |

**결론:** 지금 시스템은 "좋은 전략을 판별하는 능력"을 갖췄을 뿐, 벤치마크를 이기는 전략은 아직 없다.
앞으로의 목표는 "최고 수익률"이 아니라 **"비용 차감 후 표본 외 구간에서 KOSPI를 이기는 전략만 운영"**으로 둔다.
본 문서는 시스템 설계 의견이며 투자 권유가 아니다.

### 12-1. 선행 조건
§11 S0(운영 API 무인증 공개 차단)을 먼저 끝낸다. 아래 작업은 S0 이후 진행한다.

### 12-2. 실행 지시 (우선순위순)

**R1. 전략 채택 기준을 "벤치마크 대비"로 교체하고 운영 전략을 선별한다** (소요 2~3일)
- **평가 대상:** 운영 가상매매 전략 전부(`peak_holding.strategy` 기준 momentum·peak·v_gc·value·ai_combo·combo_*·sc_v*·v_recovery·v_contract_momentum 등)와 전략센터 등록 전략.
- **평가 데이터:** 실제 진입 로그(가상매매) + 정본 스냅샷 기반 백테스트(`backtest_equity_curve_best_v`).
  비용은 `backtest_common._tx_cost`를 쓴다. 기간은 2026-07~08 급락 창을 분리한다.
- **지표:** CAGR·MDD·Sharpe, KOSPI 대비 초과수익·정보비율·베타(QuantStats, 벤치마크 `^KS11`), 월별 승률, 회전율.
- **다중검정 보정:** `backtest_runs` 3,252건·전략 44개를 탐색한 결과이므로 Deflated Sharpe Ratio(Bailey & López de Prado)와 PBO(CSCV)를 계산한다.
  구현은 연구 venv에서 직접 한다(외부 라이브러리 불필요, 수식 기반).
- **채택 기준:** 모두 충족해야 운영 유지.
  - ① 표본 외(2025-01~) 비용 차감 초과수익 > 0
  - ② DSR > 0.95
  - ③ PBO < 0.5
  - ④ 최근 12개월 롤링 기대값 > 0
- **불합격 전략:** 신규 진입을 shadow(기록만)로 전환하는 제안서를 작성한다(`peak_holding` 보유분 청산은 하지 않음). **전환 실행은 사용자 승인 후.**
- **산출:** `research_outputs/strategy_adoption_review_YYYYMMDD.md`, 결과 `signal_experiment_ledger` 기록.
- **완료 기준:** 전략별 합격/불합격 표 + 불합격 사유.

**R2. 코어-위성 구조 설계안** (R1 결과 이후, 소요 1~2일)
- **배경:** R1 합격 전략이 없거나 소수라면, 지수 추종 코어가 가장 현실적인 개선책이다.
- **구성:** 코어 = KOSPI200 추종(인버스/레버리지 제외), 위성 = R1 합격 전략.
  위성 비중 20/30/50%를 가상 시뮬레이션해 MDD·초과수익을 비교한다.
- **실행 범위:** 가상 원장에서만 한다. `live_orders`·KIS 페이퍼 경로는 건드리지 않는다.
- **산출:** 설계 문서 + 시뮬레이션 결과. **운영 반영은 사용자 결정.**

**R3. 회전율 관리 규칙** (소요 1~2일)
- **배경:** 모멘텀 대용 신호는 연 ~4,700회 진입으로 비용(중소형 슬리피지 0.8%)에 잠식됐다.
- **시험 규칙:** 최소 보유 5/10/20거래일, 신호 강도 상위 N% 문턱, 재진입 쿨다운(`_get_recently_sold_codes` 확장), 시총 하한(슬리피지 구간 0.8% 제외).
- **방법:** vectorbt 격자(연구) → `backtest_common` 재현 → 가상매매 가드 옵션(`virtual_trade_guards.py`, 환경변수 플래그 기본 off)으로 추가.
- **완료 기준:** 비용 차감 CAGR 개선이 학습·검증 두 구간에서 모두 유지되는 규칙만 플래그로 제공.

**R4. 미사용 알파 원천 검증** (소요 1주, R1과 병행 가능)
- 모든 후보는 **Alphalens(학습 2020-01~2024-12 / 검증 2025-01~, 섹터중립, 상장폐지 포함) → 이벤트 스터디 → 원장 기록**을 통과해야 피처로 편입한다.

| 후보 | 데이터(실측 이력) | 비고 |
|---|---|---|
| 목표주가·의견 변화(컨센서스 리비전) | `consensus_targets` 8,665행·840종목, report_date 2024-05~2026-09, prev_target_price 있는 행 3,868 | 이력 2.4년으로 짧다 → 검증 구간 위주로 판단하고, `hankyung_consensus_collector` 과거 백필 가능성부터 확인 |
| 추정실적 변화(EPS 리비전) | `forward_estimate_snapshots` 4,066행, 2025-04~2026-08 | 이력 1.4년 — 수집 확대(`collect_kis_forward_estimates.py` limit 450) 우선 |
| 실적 서프라이즈 | `financial_data`(단일 basis 피벗 `_load_quarterly_pl_pivot`) + 공시일 `_release_date` | 컨센서스 대비가 어려우면 "전년동기 대비 증가율의 과거 분포 대비 z-score"로 대체 |
| 공매도·대차 잔고 변화 | `short_sell_daily` 2020-01~, `kiwoom_credit_balance` 2019-02~ | 잔고 증감률·비율 z-score, 2023-11~2025-03 공매도 금지 기간 분리 |
| 자사주·특허·수주·희석 이벤트 | §10-5 이벤트 스터디 결과(원장 13건) | 통과분만 편입 |
| Alpha101 선별(§8) | `price_history` 일봉(VWAP≈거래대금÷거래량) | 일봉 계산 가능한 30~40개, 비용 차감 후 판단 |

**R5. LightGBM 랭킹 모델 (고도화 보고서 핵심, §8)** (소요 1주, R4 이후)
- 입력은 정본 스냅샷 피처 + R4 통과 피처다. 목표는 다음 20/60거래일 초과수익(KOSPI 대비) 순위다.
- 검증: purged·embargoed walk-forward(월 단위, embargo 1개월), 창별 IC·상위 분위 초과수익·회전율.
- 기존 수동 로지스틱(`build_strategy_research_dataset._fit_logistic`)과 같은 창에서 비교하고, 음의 IC였던 기존 `model_score`는 대체 후보로 둔다.
- 운영 반영은 R1 기준을 똑같이 적용한다. 통과 시 shadow 신호로 3개월 기록한 뒤 결정한다.

**R6. 계좌 단위 위험 차단** (소요 1~2일)
- **배경:** 2026-07~08 급락에서 전략들이 동시에 손실을 냈다(전략별 손절만 존재).
- **규칙 초안:**
  - 가상 원장 총평가액이 60일 고점 대비 -10%이면 신규 진입 50% 축소, -15%이면 신규 진입 중단.
  - 회복 조건은 KOSPI>MA60 + 고점 대비 -5% 이내.
- **구현:** `virtual_trade_guards.py`에 플래그(기본 shadow)로 넣고, 차단 기록은 `virtual_guard_log`(§11 S3 보강 컬럼 사용)에 남긴다.
- **검증:** 가상 원장 과거 일별 평가액으로 on/off 비교한다.

**R7. 실행 괴리(슬리피지) 측정** (소요 1일)
- KIS 페이퍼(`live_orders`·`live_fills`)와 가상매매 체결가를, 백테스트 가정가(다음 거래일 시가/종가 + `_tx_cost`)와 매일 비교해 `execution_slippage_log`에 쌓는다(DDL DEFAULT 명시).
- 월 1회 실측 슬리피지가 `_SLIP_TIERS` 가정보다 크면 가정을 갱신하도록 경고한다.

**R8. 전략 성과 감쇠 감시** (소요 1~2일)
- 운영 전략별 최근 3/6/12개월 실적을 백테스트 기대 분포(같은 길이 창의 분포)와 비교한다. 하위 5% 이탈 시 텔레그램 알림 + 대시보드 표시(StrategyHub, §11 S5의 연구 화면과 통합).
- 스케줄러 등록은 `_run_job_safe` 경유(월 1회, 월초 06:30).

**R9. 데이터 신뢰도 자동 점검 확대** (상시)
- 이번에 반복 발견된 결함 유형을 일일/주간 계약 점검으로 고정한다(`collection_health.evaluate_job_outputs` 또는 별도 감사 잡).
  - PG 컬럼 DEFAULT 누락(신규 테이블)
  - 분기·연간 CFS/OFS 혼재 사용처
  - 스냅샷 look-ahead(같은 분기 PER 일치율)
  - 시총·금액 단위(억/원) 역전
  - 날짜 형식 불일치(YYYYMMDD vs YYYY-MM-DD) 비교
  - 종가 공식 대조(§10-14, 9/28 첫 실행)
- 각 점검은 실패 시 텔레그램 + `data_fix_log` 연계.

### 12-3. 운영 원칙 (모든 신규 전략에 적용)
1. **순서 고정:** 표본 외 검증 통과(R1 기준) → 가상/페이퍼 shadow 3개월 → 소액 실전 → 증액. 단계 건너뛰기 금지.
2. 벤치마크(KOSPI, 비용 없음)를 이기지 못하는 전략은 "수익이 나도" 채택하지 않는다.
3. 전략 추가보다 **제거**를 우선한다. 운영 전략 수 상한을 둔다(예: 5개).
4. 실거래 주문 경로(`live_orders`·KIS)를 바꾸는 작업은 모두 사용자 승인 사항이다.

### 12-4. 처리 순서
§11 S0 → R1 → (R3·R4 병행) → R5 → R2 → R6 → R7 → R8 → R9(상시)
각 단계 완료 시 이 절에 상태 표(§10-4 형식)를 추가하고, CLAUDE.md 변경 이력에 1~3줄을 남긴다.

### 11-4. §11 처리 결과 (2026-09-25 밤, Claude)
| 항목 | 상태 |
|---|---|
| S0 보안 | 사용자 결정: **① Cloudflare Access(사용자가 대시보드에서 설정) + ② 쓰기 API 토큰**. ② 구현 완료: `security_gate.py`(터널 경유 = `Cf-Connecting-Ip`/`X-Forwarded-For` 헤더가 있는 요청만 검사, 로컬 호출은 무영향), 보호 = /api·/hs·/semiconductor-lab의 POST/PUT/PATCH/DELETE + 민감 GET(`/api/portfolio`·`/api/kis-trading`·`/api/commands`·`/api/live-orders`), 토큰 미설정 시 fail-closed(503), 프런트 `apiToken.js`(localStorage `api_write_token`, 401 시 1회 입력창). 테스트 5건. **적용은 서버 재시작 + `.env`의 `API_WRITE_TOKEN` 설정 + 프런트 배포 후.** 8011(CEO)·Access 정책은 사용자 |
| S1-1 | `.venvs/py312b`(python-multipart 0.0.32·starlette 1.7.0·aiohttp 3.14.3·anthropic 1.8.0 등) 구축, pytest 통과, GET 242개 대조 차이 0, pip-audit 86→19건(남은 것: cryptography 48.0.1은 설치로 해소 예정, curl-cffi는 yfinance가 <0.14 요구로 보류). **전환(심볼릭 링크)은 사용자 승인 후** |
| S2·S3·S4·S5 | 진행 중 — 아래 상태표 갱신 |

### 12-5. §12 처리 결과 (2026-09-25 밤, Claude)
| 단계 | 상태 |
|---|---|
| S0(§12-1 선행) | ② 코드 방어선 **적용·실측 검증 완료**(2026-09-25 22:45, 토큰 `.env` 설정·백엔드 재시작·프런트 빌드 후 외부 실측: `/api/portfolio`·`/api/research/quantstats`·무토큰 POST 모두 401, 로컬 직접 호출 200). ⏳ ① Cloudflare Access 정책은 사용자 설정 필요 |
| R1 전략 채택 평가 | ✅ 평가 완료. `research/strategy_adoption_review_20260925.py`(DSR·PBO CSCV 직접 구현), 결과 `research_outputs/strategy_adoption_review_20260925.md`. **백테스트 26개 중 4기준 통과 0개**(① 통과 5개는 전부 근사 곡선이라 유보, ② DSR>0.95 0개, ③ PBO 0.58). 가상매매 momentum·peak는 비용 차감 후 KOSPI 대비 유의하게 열등. 원장 3건 기록. **shadow 전환 제안서 `research_outputs/strategy_shadow_proposal_20260925.md` — 사용자 승인 대기(권고 A안)** |
| R2~R9 | ✅ 처리 완료(§14) — 결론: 채택 기준을 통과한 전략·모델 없음, 낙폭 완화·감시 인프라 구축 |

---

## 13. 수정·보강 내역 (2026-09-25 후속 세션, Claude) — §10-4 형식

기준 시점 2026-09-25 22:45. 모든 코드는 `stock_dashboard/runtime`에 반영했고, 커밋은 13개(아래 §13-5)다. 푸시는 하지 않았다.

### 13-1. 코드·설정 변경
| # | 대상 | 변경 | 검증 | 롤백 |
|---|---|---|---|---|
| 1 | `security_gate.py`(신규), `main.py`(미들웨어 등록 3줄) | **§11 S0 ②** 터널 경유(`Cf-Connecting-Ip`/`X-Forwarded-For`/`Cf-Ray` 헤더) 요청 중 (a) /api·/hs·/semiconductor-lab의 POST/PUT/PATCH/DELETE, (b) 민감 GET(`/api/portfolio`·`/api/kis-trading`·`/api/commands`·`/api/live-orders`·`/api/research`)에 `API_WRITE_TOKEN`(`X-API-Token` 또는 `Authorization: Bearer`, 상수시간 비교) 요구. 토큰 미설정 시 fail-closed(503). 로컬 호출(스케줄러·스크립트)은 헤더가 없어 무영향 | 테스트 5건. **외부 실측**: 401×3(portfolio·research·무토큰 POST), 로컬 200, 가짜 터널 헤더 401 | `main.py`의 미들웨어 2줄 제거 후 재시작 |
| 2 | `frontend/src/apiToken.js`(신규), `main.jsx` | `fetch` 래퍼: /api·/hs·/semiconductor-lab 요청에 localStorage `api_write_token`을 `X-API-Token`으로 첨부, 401(`api_token_required`)이면 1회 입력창 후 재시도. 토큰은 코드·저장소에 없음 | 빌드 통과, `dist` 배포됨(22:41) | `main.jsx`의 `installApiTokenFetch()` 2줄 제거 |
| 3 | `routes/research_lab.py`(신규), `main.py` | 읽기 전용 `/api/research/factor-validation`·`/quantstats`·`/price-integrity`(파일 + DB SELECT만) | 실제 파일로 호출: 팩터 56행·이벤트 24행·QuantStats 26개·무결성 분류 13종 | main.py 등록 2줄 제거 |
| 4 | `FactorValidationPanel.jsx`(신규, `QuantStatsPanel` 포함), `PriceIntegrityCard.jsx`(신규), `StrategyHub.jsx` | 전략센터 "🔬 팩터 검증" 탭(IC 학습/검증 분할·이벤트 스터디·QuantStats 표) + "🧭 데이터 라우팅" 상단 가격 무결성 카드. **탭은 `localStorage.research_lab_tab='1'`일 때만 표시(검토 전 노출 차단)** | 빌드 통과. 브라우저 렌더 확인은 미실시 | 플래그 미설정 시 이미 숨김 |
| 5 | `scheduler.py` | 잡 3개 등록: `종가공식검증`(19:30, §10-14), **`월간피처스냅샷`**(월 마지막 거래일 20:30, 실패 시 익일 06:30 재시도, `_DB_WRITE_JOBS` 포함, `is_kr_trading_day` 기반 — 2026-09-30·10-30·12-30 판정 확인), **`가드사후성과`**(영업일 20:50) | 컴파일·pytest·판정 함수 검증. 백엔드 재시작(22:41) 후 대기 상태(로그는 실행 시각에 생성) | 등록 줄 3개 + 함수 삭제 |
| 6 | `scripts/refresh_feature_snapshot_monthly.py`(신규) | 정본을 직접 지우지 않는다: 스테이징 `strategy_feature_snapshot_stage`에 v4 옵션(`--adjust-jumps --legit-only --ttm-valuation`)으로 빌드 → 점검(행수 ≥ 기존 85%, 최신 월 PER 채움 ≥ 40%) → 한 트랜잭션 교체. 실패 시 롤백·텔레그램 | **사용자 수동 실행 성공**(190,609→190,609행, PER 62.3%) | 백업 `strategy_feature_snapshot_legacy_20260925` |
| 7 | `scripts/build_strategy_research_dataset.py` | (a) 로지스틱 특성 1/99% 클리핑(`model_score`가 0/1로 포화되던 것 해소: 범위 0.07~0.95, 기존과 상관 0.92), (b) numpy 2.2+macOS Accelerate의 무해한 `matmul` 경고 필터(정상 유한 데이터에서도 재현 확인) | 새 테이블에 빌드해 비교 후 삭제(`..._v5test_...` 드롭) | git revert. **현재 정본의 `model_score`는 클리핑 이전 값 — 다음 월간 재생성에 반영** |
| 8 | `virtual_trade_guards.py` | **§11 S3** 판정 로직은 불변, 기록만 보강: 사유마다 실제 가드명(`regime_filter`/`exposure_stock`/`exposure_sector`/`entry_confirm`)으로 행 분리, `price_at_block`·`kospi_close`·`kospi_ma60` 기록, 같은 (종목·전략·가드·판정) 하루 1건으로 합침 | 기존 가드 테스트 통과 + 신규 1건(사유→가드명 분리, SQLite) + **PG 실측**: 3회 호출→1행, 사유별 2행, 삭제 후 정리 확인 | git revert(컬럼은 남아도 무해) |
| 9 | `scripts/fill_guard_log_outcomes_20260925.py`(신규) | `virtual_guard_log`의 차단·shadow 진입에 5/20/60거래일 사후 수익(종목·KOSPI)을 채움, 미경과 지평은 NULL 유지, 멱등 | 테스트 2건(사후 수익 계산·잘못된 입력), 드라이런(대상 0건 — 기존 8건은 가격 미기록) | 스케줄러 잡 제거 |
| 10 | `scripts/add_valuation_history_per_ttm_20260925.py`(신규), `routes/tenbagger.py` | **§10 P0-2** `valuation_history`에 `per_ttm`·`ttm_net_income` 추가·채움. 기존 `per`/`eps` 불변. valuation-history 응답에 두 필드 추가 | 삼성전자 `per`(60/144/52/15.7) vs `per_ttm`(9~15배), 전 종목 중앙값 9~12.6배, Q2/Q1 순이익 중앙값 1.05~1.26배로 누적값 아님 확인 | `ALTER TABLE valuation_history DROP COLUMN per_ttm, DROP COLUMN ttm_net_income` |
| 11 | `scripts/sync_tenbagger_postgres.py`, `verify_tenbagger_postgres.py` | 동기화·검증 목록에서 `strategy_feature_snapshot` 제거(SQLite 옛 값이 정본을 덮어쓰는 것 방지) | pytest | git revert |
| 12 | `.venvs/py312b`(신규 venv), `requirements/py312b.freeze.txt` | **§11 S1 1·2·3**: python-multipart 0.0.32, starlette 1.7.0, aiohttp 3.14.3, anyio 4.14.2, urllib3 2.8.0, requests 2.34.2, idna 3.20, lxml 6.1.3, soupsieve 2.10, pyasn1 0.6.4, click 8.5.0, anthropic 1.8.0, cryptography 48.0.1(설치 후 미재감사). `curl_cffi`는 0.13.0 유지(yfinance 1.2.0이 <0.14 요구). OpenDartReader는 운영 venv에서 폴더 복사 | pip check 통과, pytest 513(당시), 앱 로드 라우트 473 동일, **GET 242개 상태코드 운영 venv와 차이 0**(200×225, 422×11, 500×6 — 500은 양쪽 동일한 기존 문제), pip-audit 86→19건(재감사 전 cryptography 3건 포함) | **전환 전**이라 운영 무영향 |
| 13 | `research/extract_adoption_inputs_20260925.py`, `strategy_adoption_review_20260925.py`(신규), `scripts/record_adoption_review_ledger_20260925.py` | **§12 R1** 채택 평가(DSR·PBO CSCV 수식 직접 구현) + 원장 기록 | §12-5 참조 | 파일 삭제, 원장 3행은 experiment_name으로 삭제 가능 |

### 13-2. DB 변경 (모두 추가·표시 방식, 삭제 없음)
| 대상 | 변경 |
|---|---|
| `strategy_feature_snapshot` | 정본 교체(사용자 실행, 190,609행 v4), 6개 라벨 컬럼 추가. 백업 `strategy_feature_snapshot_legacy_20260925` |
| `valuation_history` | `per_ttm`, `ttm_net_income` 컬럼 추가·채움(37,904행) |
| `virtual_guard_log` | 9개 컬럼 추가(`price_at_block`, `kospi_close`, `kospi_ma60`, `ret_5/20/60d`, `kospi_ret_5/20/60d`, 전부 DEFAULT NULL) — 코드가 `ALTER … ADD COLUMN IF NOT EXISTS`로 자동 추가 |
| `backtest_runs` | aqr_multifactor 기존 38행 이름에 `[look-ahead 오염·폐기 2026-09-25]` 접두, 새 실행 6건 `aqr_multifactor v4snap_20260925 …` |
| `signal_experiment_ledger` | +3행(`adoption_review_*_20260925`) — 이전 13행과 합쳐 §10-5 |
| 임시 테이블 | `strategy_feature_snapshot_v5test_20260925` 생성 후 삭제, 스테이징 테이블은 월간 잡이 교체 후 삭제 |

### 13-3. 수치 변화 (aqr_multifactor, look-ahead PER 제거 후 재백테스트)
2020-03~2021-11 +98.4→+55.2% / 2021-12~2022-10 -30.2→-20.2% / 2022-11~2023-10 -9.5→-1.7% / 2023-11~2024-12 +31.0→+5.0% / 2024-06~2025-05 +13.1→+20.4% / 2025-06~2026-03 +6.7→+9.1%.

### 13-4. 사용자 조치 상태
| 조치 | 상태 |
|---|---|
| `.env`에 `API_WRITE_TOKEN` 추가 | ✅ 완료(2026-09-25 22:4x, 값은 확인·기록하지 않음) |
| 백엔드 재시작(`safe_restart_backend.sh`) | ✅ 완료(PID 92448→32902, 22:41) |
| 프런트 `dist` 빌드 | ✅ 완료(`StrategyHub` 61.84 kB, `index` 853.38 kB) |
| Cloudflare Access 정책(① S0) | ⏳ 미설정 — 설정 전까지 ②만 방어선(공개 GET 중 비민감 엔드포인트는 여전히 무인증) |
| venv 전환(`ln -sfn .venvs/py312b venv` + 재시작) | ⏳ 승인·실행 대기(롤백 `ln -sfn .venvs/py312 venv`). CEO 8011의 starlette 0.47.3은 별도 |
| shadow 전환(R1 제안서 A안) | ⏳ 승인 대기(`research_outputs/strategy_shadow_proposal_20260925.md`) |
| 외부에서 쓰기 기능 사용 시 | 브라우저 입력창에 `.env`의 `API_WRITE_TOKEN` 입력(1회, localStorage 저장) |
| 근사 곡선 전략 5개 엔진 재실행 | ⏳ 승인 후 |

### 13-5. 커밋(브랜치 `claude/sqlite-migration-completion-x0h891`, 푸시 안 함)
`5b475c9` env/requirements · `83e572c` 백테스트 곡선 · `f20bf9a` 스냅샷·밸류에이션 · `ea80b45` 종가검증 잡 · `e5c3fd7` 가격 차트 · `b39b578` 보안 게이트+연구 API/패널 · `cb6afd1` 연구 스크립트 · `3809b5e` 문서 · `692783f` 월간 스냅샷 잡 · `446d404` 가드 로그·사후 성과 · `fa76f7d` 연구 탭 플래그·/api/research 게이트 · `fd5459a` R1 평가 · `f37b777` py312b freeze. 제외(다른 세션 파일): `ETF_check/*`, `scripts/*etf*`, `tests/test_etf_universe_sync_v3.py`, `scripts/fix_cfs_old_quarterly_rows_20260925.py`·`fix_ofs_quarterly_op_20260924.py`·`fix_q4_derived_rows_20260925.py`. `research_outputs/*.md·csv`는 .gitignore 대상이라 로컬 보관.

### 13-6. 알려진 한계
- 게이트는 `Cf-Connecting-Ip`/`X-Forwarded-For`/`Cf-Ray` 헤더가 없는 요청을 "로컬"로 본다. 같은 LAN에서 vite preview(5173, `host:true`)로 직접 접속하면 헤더가 없어 토큰 없이 통과한다(LAN 신뢰 가정). 터널 우회 경로가 필요 없으면 preview 바인딩 제한(S0 ③)을 병행할 것.
- `cloudflared tunnel run --token …`이 프로세스 인자에 토큰을 노출한다(`ps`로 같은 계정에서 조회 가능). 토큰 파일/환경변수 방식으로 바꾸는 것을 권고(미수정, 서비스 정의는 사용자 영역).
- 가드 로그의 기존 8건은 `guard='entry'`·가격 없음(소급 불가). 새 기록부터 사후 성과가 채워진다.
- R1의 표본 외 구간은 1.3년, 가상매매 표본은 5개월이다(통계력 한계는 R1 문서에 명시).

---

## 14. §12 R2~R9·shadow 전환 처리 (2026-09-25 밤, Claude) — §13 형식

기준 시점 2026-09-25 23:30. 사용자 승인: shadow 전환 A안(momentum·peak), 남은 항목 진행.

### 14-1. 사용자 확인 사항 (실측)
| 항목 | 결과 |
|---|---|
| Cloudflare Access(① S0) | **사용자는 설정 완료라고 했으나 실측상 미적용**: `https://stock.leanguy.cloud/`가 로그인(302) 없이 200, `/api/portfolio`는 원본 서버의 401(`api_token_required`, 우리 게이트), `https://api.newsinfo.cloud/health` 200. 설정한 애플리케이션의 호스트명·정책(Allow/Bypass)·배포 여부 재확인 필요. ②(토큰 게이트)만 유효 |
| shadow A안 | 코드+`.env` 반영 완료, **백엔드 재시작 후 적용** |

### 14-2. 코드·설정 변경
| # | 대상 | 변경 | 검증 | 롤백 |
|---|---|---|---|---|
| 1 | `virtual_trade_guards.py`, `stockeasy_autotrade.py`, `.env`(`VT_SHADOW_STRATEGIES=momentum,peak`) | **shadow A안**: 목록 전략의 신규 진입은 진입하지 않고 `virtual_guard_log`에 `guard='shadow_strategy'`·`shadow_would_block`·가격·KOSPI 기록. 두 진입 경로 모두 적용 — API `trend_buy`(`_paper_buy_gate`→`check_entry`)와 **스케줄러 StockEasy 동기화(`_upsert_trend_holding`, 기존엔 가드를 거치지 않던 경로)**. 기존 보유분 청산·실주문 경로 불변 | 테스트, PG 실측(2회 호출→1행), 목록 밖 전략 무영향 | `.env`의 줄 삭제 후 재시작 |
| 2 | `virtual_trade_guards.py` | **R3** 기본 꺼짐 플래그 `VT_MIN_MCAP_EOK`(권고 1000)·`VT_REENTRY_COOLDOWN_DAYS`(권고 28) | 테스트 | 미설정=꺼짐 |
| 3 | `virtual_trade_guards.py`, `scripts/compute_virtual_account_equity_20260925.py` | **R6** 가상 계좌 낙폭 규칙(-10% 격번 축소, -15% 중단, KOSPI>MA60 & -5% 이내에서만 해제), `VT_ACCOUNT_DD_GUARD=shadow`(기본, 기록만)/enforce/off | 테스트, 평가액 재구성(83영업일, 최대 낙폭 -3.4% → 발동 0회) | `off` |
| 4 | `scripts/measure_execution_gap_20260925.py` | **R7** 신호-체결 시점 괴리 측정 → `execution_slippage_log`, 진입(매수)만 판정·가정 슬리피지 대비 +0.2%p 초과·n≥20이면 텔레그램 | 실행: 54건, **진입 평균 역방향 괴리 +0.29% vs 가정 0.13%**(n=19, 경고 미발동) | 잡 제거 |
| 5 | `scripts/strategy_decay_monitor_20260925.py`, `routes/research_lab.py` | **R8** 월 1회 감쇠 감시 + `/api/research/strategy-decay` | 실행: `v_gc` 3M -25.1% < 하위 5% -17.7% **경고 1건(텔레그램 발송됨)** | 잡 제거 |
| 6 | `scripts/data_contract_audit_20260925.py` | **R9** 일일 계약 점검 6종 → `data_contract_check_log`, 실패 시 텔레그램+`data_fix_log` | 첫 실행: default_missing ok, **cfs_ofs_mixed_ttm 15.25%(warn, 426/2,793종목)**, snapshot_lookahead 1.99%(임계 warn 2·fail 4), unit_inversion 0.54%(15/2,765), date_format 0, close_verify ok | 잡 제거 |
| 7 | `scheduler.py` | 잡 추가: `가드사후성과`(20:50)에 **가상계좌평가·실행괴리 측정** 포함, `전략감쇠감시`(매월 첫 영업일 06:30), `데이터계약점검`(07:10) | 컴파일·pytest 525 | 등록 줄·함수 삭제 |
| 8 | `research/*_20260925.py` | R2 `core_satellite`, R3 `turnover_rules`, R4 `extract_alpha_sources`+`alpha_sources`, R5 `lightgbm_ranking`, R6 `account_dd_rule` | 아래 14-3 | 파일 삭제 |
| 9 | 연구 venv(`research_venv312`) | `lightgbm 4.7.0`(MIT) 설치 — 운영 venv에는 미설치 | 학습 확인 | pip uninstall |

### 14-3. 연구 결과 (모두 시스템 검증 결과이며 투자 권유가 아님)
| 단계 | 결론 |
|---|---|
| R1 보강 | 재구성 곡선을 엔진 MDD로 검증: golden_cross·contract_momentum 6구간 평균 절대 차이 0.8%p(최대 3.7%p) → 신뢰. 그래도 DSR 0.096/0.331로 **여전히 채택 0개**. earnings_conviction·se_momentum은 검증 불가, turnaround는 계단 곡선이라 유보. 엔진 재실행은 이 엔진들이 일별 곡선을 산출하지 않아(거래 목록만 반환) 엔진 수정이 필요해 보류 |
| R3 회전율 | 시총을 **시점 정합(신호일 직전 스냅샷)**으로 쓰자 결과가 뒤집힘 — 최신 시총을 쓰면 생존·성장 편향으로 시총 하한이 과대평가됐음(시행착오). 108개 규칙 중 학습·검증 CAGR 개선+Sharpe 유지+급락 창 악화 없음을 모두 충족한 것 23개, 모두 시총 ≥1000억 포함. 단독 규칙은 모두 미채택(학습 구간 개선 없음). 가드 플래그 2개는 기본 꺼짐 |
| R4 알파 원천 | 직교화(시총·수익률·52주고점·거래량 제거) 후 통과: **sue_op(영업이익 서프라이즈 대용) IC 0.026~0.032, 20D·60D 모두**, sue_ni(20D), borrow_pct(음의 신호), short_ratio_20d(해석 불확실 — 금지 기간 시장조성 헤지 흐름 가능성). credit_chg는 검증 구간 약화, credit_ratio는 직교화 후 소멸. 컨센서스 이력 27개월·EPS 리비전 2.7개월(`forward_estimate_snapshots`의 snapshot_date는 2026-07~09 201종목뿐)로 판정 불가. 원장 8건 |
| R5 LightGBM | walk-forward(43개월) IC 0.159 > 3팩터 합성 0.127 > 기존 model_score -0.079(음수). 그러나 상위 20% 동일가중 롱온리는 KOSPI 대비 월 -1.6%p(비용 차감 -2.0%p) — R1 ① 미충족, 합성과 차이 작아 **미채택**. 저변동성이 중요도 29% |
| R2 코어-위성 | 위성 비중 20/30/50%: 일관된 효과는 **MDD 완화**뿐(v11 -34.6→-23.5%@50%, 합성 -22.2→-9.0%). 엔진 전략 위성의 초과수익은 정보비율 0.02~0.14로 무의미, 합성은 음. **위성 0%(또는 실험 소액 가상) 유지 권고** |
| R6 계좌 낙폭 | 가상 원장(83일)은 임계 미도달로 검증 불가 → 대용 포트폴리오로 대체: R3 규칙과 함께 쓰면 검증 CAGR +2.8→+19.1%이나 **전체 MDD -40.8→-44.5%로 악화·급락 창 악화**(반등 놓침) → shadow만, enforce는 사용자 결정 |
| R7 | live_orders 55건은 전부 PAPER(모의)라 실제 브로커 슬리피지는 측정 불가. 대신 신호-체결 시점 괴리 측정 |

### 14-4. 새로 발견한 결함·주의
- **`momentum`·`peak` 가상 현금 계좌가 오염됨**: 매도 기록만 있고 매수 기록이 없어 잔고가 429.9M·178.5M으로 부풀려짐(계좌가 2026-08-18에 생성되기 전 미러링된 포지션이 청산되며 대금만 입금). 총평가액 계산에서 제외했고(`excluded` 컬럼), 원인 수정(미러링 경로의 원장 기록)은 미실시 — shadow 전환으로 신규 발생은 멈춤.
- 가상 원장의 일부 매수(v_gc 등)는 임시 음수 `holding_id`와 빈 종목코드로 기록돼 이후 매도(실제 ID)와 연결되지 않음 → 수량 일치 매칭으로 복원(오탐 방지).
- 문서 §12-2 R4의 "forward_estimate_snapshots 2025-04~"는 `estimate_date` 기준이며 스냅샷 이력은 2026-07~09.
- 스케줄러가 시작하는 새 잡(월간 스냅샷·가드사후성과·전략감쇠·데이터계약)은 **백엔드 재시작 후** 동작. 재시작 전까지 수동 실행 가능.

### 14-5. DB 변경 (추가 방식, 삭제 없음)
신규 테이블(전부 DEFAULT 명시): `virtual_account_equity_daily`(83행), `virtual_strategy_equity_daily`(17전략×83일), `execution_slippage_log`(54행), `strategy_decay_check`, `data_contract_check_log`. 기존 `virtual_guard_log`는 §13의 9컬럼에 더해 사용. `signal_experiment_ledger` +8행(R4 7 + R5 1), R1 행 1건 보강(UPDATE). `data_fix_log`에는 감사 실패 시에만 기록.

### 14-6. 사용자 조치 상태
| 조치 | 상태 |
|---|---|
| shadow A안 적용 | ✅ 반영 완료(23:52·09-26 09:37 재시작, `VT_SHADOW_STRATEGIES=momentum,peak` 로드 확인). 실제 shadow 기록은 신규 진입 신호가 나와야 확인 가능 |
| Cloudflare Access 재확인 | ⏳ 실측상 미적용 |
| 새 스케줄러 잡 적용 | ✅ 반영 완료(재시작됨). 추석 연휴로 첫 실행은 09-28(월간 스냅샷은 09-30) |
| py312b 전환 | ✅ 완료(사용자 실행: `venv`→`.venvs/py312b` 링크 22:51, 23:46 재시작 프로세스가 py312b 로드 확인). 이후 `cryptography` 48.0.1→50.0.0 추가 적용(임시 환경 시험 후, pytest 525·라우트 474·pip check 통과) — 23:52 재시작으로 반영 완료. 남은 감사 1건: curl_cffi 0.13.0(yfinance <0.14 요구로 유지) |
| R3 플래그 켤지 | ⏳ 사용자 결정(기본 꺼짐) |
| R6 enforce 여부 | ⏳ 사용자 결정(기본 shadow, 근거 약함) |
| momentum·peak 원장 오염 수정 | ✅ 완료(§16 V6) |

### 14-7. 커밋
`620e35d` shadow · `d4c938d` R3 · `f866be9` R4 · `4fc05ae` R5 · `b21f1e8` R2 · `ab00377` R6 · `41845b1` R7 · `572ee57` R8 · `01aa928` R9 · `043f6ef` R1 곡선 신뢰 기준. (푸시 안 함, `research_outputs/*.md·csv`는 .gitignore 대상)


---

## 15. 3차 적용 점검 (2026-09-25 23:55, Claude) — §13·§14 실측 + 추가 지시

Cloudflare Access 설정은 사용자 영역이라 제외했다. 이 절도 기록만 한다(코드·DB·설정 미변경).

### 15-1. 실측 확인
| 항목 | 결과 |
|---|---|
| 커밋 | §13·§14 변경이 23개 커밋으로 정리됨. 미커밋은 다른 세션의 ETF 파일·일회성 스크립트 3개뿐 |
| 서버·환경 | `venv → .venvs/py312b`, 운영 8000 프로세스 **23:52 재시작**, py312b 로드, cryptography 50.0.0. → **§14-6의 "재시작 필요" 3건(shadow A안·새 잡·cryptography)은 이미 반영됨**(문서 갱신 필요) |
| shadow A안 | `.env` `VT_SHADOW_STRATEGIES=momentum,peak`, 재시작 후 적용 |
| 스케줄러 | `종가공식검증`·`월간피처스냅샷`(`_DB_WRITE_JOBS` 포함)·`가드사후성과`·`전략감쇠감시`·`데이터계약점검` 등록 확인. 원장상 아직 미실행(추석 휴장) |
| 보안 게이트 | 외부 무토큰: `/api/portfolio`·`/api/research/*` 401, POST 401 → 동작함 |
| R1~R9 연구 결론 | 문서 기재와 결과 파일 일치. "채택 0개", "LightGBM 미채택", "위성 0% 권고"는 보수적이고 타당 |

### 15-2. 추가 지시 (우선순위순)

**V1 (P0 보안·코드 측) — 게이트 민감 목록 누락으로 실제 보유 종목이 공개 중**
- 2026-09-25 23:55 외부 무토큰 GET 결과:
  - `https://stock.leanguy.cloud/api/realtime/prices` **200** — `holdings`에 **실보유 47종목**(국내 43·미국 4) + `summary`.
  - `/api/buy-candidates` 200(개인 매수 후보), `/api/trend/holdings` 200(가상 보유 258KB), `/api/us-virtual/positions` 200.
- 원인: `security_gate.SENSITIVE_GET_PREFIXES`가 5개 경로(portfolio·kis-trading·commands·live-orders·research)뿐이다.
- 조치(권장): **터널 경유 `/api/*`·`/hs`·`/semiconductor-lab` 요청은 GET까지 전부 토큰을 요구**한다.
  프론트 `apiToken.js`가 이미 모든 /api 요청에 토큰을 붙이므로 사용자 불편은 없다. 공개가 필요한 GET이 있으면 **허용 목록(allowlist)**으로 예외를 둔다(차단 목록 방식은 신규 엔드포인트마다 누락이 반복된다).
  테스트에 "터널 헤더 + 무토큰 → /api/realtime/prices 401"을 추가한다.
- **CEO 플랫폼(`api.newsinfo.cloud` → 8011)은 게이트가 없다.** 외부에서 `/docs` 200, OpenAPI 123개 경로 중 쓰기 54개가 공개돼 있다. 같은 게이트를 적용하고, starlette 0.47.3도 업그레이드한다(§11 S1).
- LAN 우회(§13-6): `vite preview`의 `host:true`(`*:5173`)를 `127.0.0.1`로 제한한다. cloudflared는 같은 Mac에서 로컬로 접속하므로 영향이 없다. 적용 여부는 사용자 결정.

**V2 (P0 데이터) — TTM PER이 CFS/OFS를 분기별로 섞는다**
- `scripts/add_valuation_history_per_ttm_20260925.py`와 `build_strategy_research_dataset.py`(스냅샷 TTM)가 **분기마다 독립적으로 "CFS 우선"을 골라** 4개 분기를 더한다.
  데이터계약점검이 잡은 `cfs_ofs_mixed_ttm` 426/2,793종목(15.25%)은 한 TTM 안에 연결·별도 순이익이 섞인다(CLAUDE.md "CFS/OFS 혼합 역산 금지" 위반). 시총(연결)÷별도 순이익이 되어 PER이 왜곡된다.
- 조치:
  - TTM 창(4분기) 단위로 basis를 고정한다: 4개 모두 CFS면 CFS, 아니면 4개 모두 OFS면 OFS, 둘 다 불가하면 NULL.
  - `per_ttm`·`ttm_net_income`을 재계산(`data_fix_log` 기록)하고 정본 스냅샷을 재생성(월간 잡 또는 수동)한다.
  - 이후 Alphalens earn_yield를 재실행해 원장을 갱신한다.
  - `cfs_ofs_mixed_ttm` 점검은 "TTM 소비처가 혼재 창을 쓰는지"(값이 채워진 혼재 창 수)를 기준으로 fail 판정하도록 바꾼다.

**V3 (P1) — `snapshot_lookahead` 점검이 누설이 아니라 정체를 잰다**
- 현재 지표는 "PER이 전달과 같은 비율"(31/1,558)이다. 이 방식으로는 오늘 발견한 누설을 잡지 못한다.
- 교체: ① 스냅샷 PER과 **같은 분기** `valuation_history.per` 일치율(누설 지표, 2026-09-25 기준 원본 99%·v4 0%), ② 스냅샷 각 재무 피처의 원천 공시일(`_release_date`) ≤ snapshot_date 위반 건수. ①이 1% 초과면 fail.

**V4 (P1) — 단위 역전 15종목 후속 미처리**
- `unit_inversion`(15/2,765종목이 5배 이상 어긋남)은 ok 판정이지만 목록·원인이 기록되지 않았다.
- 조치: 목록을 `data_contract_check_log.detail`에 남기고, 원인(시총 억/원, 수급 백만원, 재무 원/천원)을 분류해 정정한다(`data_fix_log`).

**V5 (P1 연구 해석) — 벤치마크가 대형 반도체 2종목에 지배된다**
- R1·R2·R5가 "KOSPI 대비 열위"로 결론 냈다. 그런데 2024~26 KOSPI(시총가중)는 삼성전자·SK하이닉스 비중이 매우 커서, 중소형·저변동성 편향 포트폴리오는 신호와 무관하게 불리하다.
- R5 LightGBM은 IC 0.159(양호)인데 롱온리 상위 20%는 월 -1.6%p다. 이는 신호 부재보다 **베타·규모·섹터 편향**일 가능성을 먼저 배제해야 한다.
- 조치: 모든 채택 평가에 보조 벤치마크를 추가한다.
  - ① 유니버스 동일가중
  - ② KOSPI200 동일가중 또는 상위 2종목 제외
  - ③ 회귀 기반 알파(젠센 알파, 베타 조정)와 섹터(`sector_large`)·규모 중립 포트폴리오 성과
- 판정 규칙은 유지한다(KOSPI 미달 = 운영 미채택). 다만 "신호 무효"와 "벤치마크 구성 차이"를 구분해 원장에 기록한다.

**V6 (P1 데이터) — 가상 원장 오염의 원인 수정**
- §14-4: `momentum`·`peak` 현금 계좌가 매수 없이 매도 대금만 입금돼 429.9M·178.5M으로 부풀려졌다. `v_gc` 등의 매수가 음수 임시 `holding_id`·빈 종목코드로 기록된다.
- 조치:
  - 미러링 경로(StockEasy 동기화 `_upsert_trend_holding`)와 GC 매수 경로(`routes/trend.py`)에서 원장 매수 기록을 실제 `holding_id`·`stock_code`로 남기게 한다.
  - 오염 계좌는 거래 로그로 재구성한 값으로 정정한다(백업·`data_fix_log`).
  - 수정 전까지 `virtual_account_equity_daily`의 `excluded` 처리를 유지한다.

**V7 (사용자 결정) — `v_gc` 감쇠 경고**
- 전략감쇠감시가 `v_gc` 3개월 -25.1%(백테스트 분포 하위 5% = -17.7%)로 경고를 보냈다. R1에서도 불합격이다(표본 n<30이라 A안에서 제외).
- 선택지:
  - ① `VT_SHADOW_STRATEGIES`에 `v_gc` 추가(신규 진입만 중단, 보유분 유지)
  - ② 3개월 추가 관찰
- 결정은 사용자가 한다.

**V8 (확인) — 첫 거래일(2026-09-28)·월말(09-30) 실행 검증 체크리스트**
- 09-28 19:30 `종가공식검증`: `price_close_verify_log` 불일치율. 불일치가 있으면 해당 시각 `price_history.created_at`과 수집 원장을 대조해 **원인 수집기를 특정**한다(§11 S7).
- 09-28 18:00 `KIS일별수집`이 `collection_job_runs`에 success로 남는지 확인한다.
- 09-28 20:50 `가드사후성과`: momentum·peak 진입이 `guard='shadow_strategy'`로 기록되는지(가격·KOSPI 포함), 실제 `peak_holding` 신규 진입이 0인지 확인한다.
- 매일 07:10 `데이터계약점검`: V2·V3 수정 전에는 warn이 계속 나올 수 있다.
- 09-30 20:30 `월간피처스냅샷`:
  - 2026-09-23 월중 시점이 09-30 월말로 대체되는지 확인한다.
  - `model_score`가 클리핑 버전(0.07~0.95)으로 바뀌는지 확인한다.
  - 점검(행수 85%, PER 40%)이 통과하는지 확인한다.
- 10-01 06:30 `전략감쇠감시` 두 번째 실행.
- 실행괴리(R7): 진입 역방향 괴리 +0.29%(가정 0.13%, n=19)가 n≥20에서도 유지되면 `_SLIP_TIERS` 상향을 검토한다.

**V9 (문서) — 상태 갱신**
- §14-6 표를 갱신한다: shadow A안·새 잡·cryptography 50은 23:52 재시작으로 반영 완료.
- §10-3/§10-4 순서를 정리하고, §10-4 1번 행의 "(이전 기록:)" 옛 설명을 삭제한다.

### 15-3. 처리 순서
V1(보안 코드) → V2 → V6 → V3 → V4 → V5 → V8(09-28·09-30 확인) → V9 / V7은 사용자 결정

---

## 16. §15 지시(V1~V9) 처리 결과 (2026-09-26, Claude) — §13 형식

### 16-1. 사용자 요청으로 정책이 바뀐 부분 (V1)
| 항목 | 내용 |
|---|---|
| 게이트 정책 | §15 V1은 "터널 경유 `/api` GET까지 전부 토큰"을 권고했고 이를 먼저 적용했으나(`30a5c5f`), 사용자가 "접속할 때마다 토큰을 계속 입력하라고 한다. **수정하거나 데이터를 빼갈 때만** 요구해야 한다"고 정정 → **수정(POST/PUT/PATCH/DELETE) + 민감 GET(보유·계좌·주문·후보·연구·다운로드·내보내기·백업·관리·설정)에만 인증**으로 재개정. 일반 조회는 무토큰. 엄격 모드는 `API_GATE_MODE=strict`로 되돌릴 수 있음(§15 V1 권고안). 트레이드오프: 민감 목록은 명시적 관리라 **새 민감 엔드포인트를 목록에 넣지 않으면 공개**된다(테스트 `test_policy_writes_and_sensitive_reads_only`가 알려진 경로 고정) |
| Cloudflare Access 로그인 인정 | `CF_ACCESS_TEAM_DOMAIN`·`CF_ACCESS_AUD`가 설정되면 서버가 Access JWT(`Cf-Access-Jwt-Assertion` 헤더 또는 `CF_Authorization` 쿠키)의 RS256 서명·만료·aud·iss를 직접 검증해 토큰 없이 통과(링크 다운로드 포함). `CF_ACCESS_ALLOWED_EMAILS`로 이메일 제한 가능. 검증 테스트 10건(위조·만료·다른 앱·변조 거부). **Access 앱 생성 후 `.env`에 두 값을 넣어야 활성화** |
| 프런트 토큰 입력 | 원인 = 옛 프런트의 동시 요청마다 입력창, 잘못 저장된 토큰 반복 거부, 저장소 차단. 개선: 입력창 1개 공유, 붙여넣기 정리(`API_WRITE_TOKEN=`·따옴표·공백 제거, 8유형 시험), 거부 사유 안내, 취소 시 10분 무질문, 저장소 차단 시 메모리 유지. 09-26 09:37 재시작·`dist` 재빌드로 반영, 외부 실측(일반 GET 200, 민감 GET·POST 401, 올바른 토큰 200) |
| CEO 플랫폼(8011) | **코드 미변경**(다른 세션이 `main.py`·세션 인증을 활발히 수정 중). 정적 분석: 쓰기 54개 중 **세션 인증 없는 것 38개** — 위험(`/app-users` 생성·수정·삭제, `/telegram-settings`, `/openai-settings`, `/naver-news-settings`, `/api/agi/*` 제출·트리거, `/api/gemini/register-second-key`)과 공개 의도(`/app-login`, `/requests` 생성)가 섞여 있어 토큰 게이트를 씌우면 로그인 화면이 깨질 수 있음 → **그 세션이 `require_admin_session`을 위험 엔드포인트에 적용**할 것. 임시 방어는 Cloudflare Access(`api.newsinfo.cloud` 별도 앱). starlette 취약점(0.47.3): 새 환경 `codex/ceo-briefing-platform/backend/.venv312b`(fastapi 0.141.1·starlette 1.7.0)를 만들어 자체 테스트 통과·OpenAPI 135작업 동일·pip-audit 0건 확인 — **전환은 `.venv` 링크 변경 + 8011 재시작(사용자)** |

### 16-2. 처리 표
| # | 상태 | 내용 |
|---|---|---|
| V2 | ✅ | TTM PER의 CFS/OFS 혼재 수정: 4분기 창 단위 단일 기준(CFS 4개→CFS, 아니면 OFS 4개→OFS, 아니면 NULL). `valuation_history.ttm_basis` 추가, `per_ttm` 재계산(채움 37,904→35,053; CFS 48,926·OFS 7,036·기준 없음 10,118 창), `data_fix_log` 109. 스냅샷 생성기(`build_strategy_research_dataset.py`)도 동일 규칙, 정본 스냅샷 재생성(190,609행, PER 채움 62.28%). 연구 입력 추출이 옛 v4 스테이징 테이블을 하드코딩해 읽고 있던 결함도 정본 테이블로 수정. Alphalens 재검증: earn_yield 60D IC 0.080→0.076(t 8.1→7.6, 학습 0.065·검증 0.112), 결론 불변, R4·R5·R2 재실행 결론 유지 |
| V3 | ✅ | `snapshot_lookahead` 점검을 실제 누설 지표로 교체: (a) 스냅샷 PER과 같은 분기 `valuation_history.per` 일치율(옛 스냅샷 100%·새 0.1%로 판별력 확인, 1% 초과 fail), (b) 표본 400행의 공시 시차 위반(0건). `cfs_ofs_mixed_ttm`은 저장된 `per_ttm`이 단일 기준 4분기 합과 일치하는지로 판정(불일치 0, 참고로 최근 4분기 혼재 종목 426/2,793) |
| V4 | ✅ | 단위 역전 15종목의 **근본 원인 확정**: KRX 일별 잡이 `stock_universe`의 시총·주식수·소속부만 UPDATE하고 종가·기준일은 갱신하지 않아 close/base_date가 09-04에 굳음(2,765행 중 2,497행이 최신 가격과 0.5% 초과 차이). 병합 후 5.0·10.0배(12종목: 진흥기업·SDN·신라섬유 등), 정리매매 급락 0.01~0.03배(카프로 43원·코스나인 1원·코다코 140원). 조치: `scheduler.py` 일별 잡이 close/open/high/low/volume/base_date도 갱신, 일회성 정정 `refresh_stock_universe_price_fields_20260926.py`(2,768행, 백업 `stock_universe_backup_v4_20260926`, `data_fix_log`), 점검이 원인별 분류(stale universe row vs 실제 단위 역전) + `universe_fresh` 신설. 정정 후 14종목 해소, **`196490` 디에이테크놀로지 1종목은 감자(30:1)·유상증자·거래정지(5~9월) 후 재개가(202원 vs 직전 6,090원)의 기준 불일치로 개별 검토 대상(warn)** |
| V5 | ✅ | 벤치마크 분해(R5·R1): 월평균 KOSPI 2.74% vs 삼성·하이닉스 제외 1.49% vs 동일가중 0.42%. LightGBM 상위 20%(1.10%)의 KOSPI 격차 -1.64%p 중 **76%가 삼성·하이닉스 편중**으로 설명(제외 KOSPI 대비 -0.39%p, t -0.5), 동일가중 대비 +0.68%p(t 1.8), 섹터·규모 중립 선택 알파 +0.66%p(t 2.2), 젠센 알파 +0.64%p(t 1.0, 베타 0.17) → 신호 무효 근거 없음·초과성과도 미확정. R1: 표본 외 CAGR KOSPI 78.4%·제외 KOSPI 42.1%·동일가중 11.5%, 초과수익>0 전략 5개(KOSPI)→11개→18개. **판정 규칙(KOSPI 기준)·결론(채택 0개)은 유지**. 원장 `benchmark_decomposition_20260926` 신규, R1·R5 행 보강 |
| V6 | ✅ | 원장 오염: 원인 확정 — StockEasy 동기화가 원장 매수·매도를 전혀 기록하지 않는데 청산 API만 매도를 기록해 매수 없는 매도 대금만 입금됨(momentum 429.9M·peak 178.5M). 옛 음수 `holding_id`·빈 종목코드 매수 73건은 `scripts/backfill_virtual_cash_ledger.py`(07월 일회성 백필)의 산물이고 08-14 이후 신규 발생 0건(GC 등 현행 경로는 정상). 조치: (1) `record_trade`가 대응 매수 없는 매도를 거부(`allow_unmatched_sell=True`는 정정 전용), 테스트 2건, (2) StockEasy 동기화가 매수·매도를 원장에 기록(원장 오류가 동기화를 멈추지 않음), (3) 정정 `repair_virtual_ledger_20260926.py`: 옛 매수 73건 종목코드·실제 holding_id 복원, momentum 36건·peak 8건 보정 매수(ref_key `repair_buy:<id>`) 추가 → **momentum 429.9M→72.8M, peak 178.5M→99.0M**(검산: 1억 − 실현손실 1,724만원 − 보유 1,000만원). 백업 `virtual_cash_ledger_backup_v6_20260926`·`virtual_cash_accounts_backup_v6_20260926`, `data_fix_log`. 계좌 평가액 재구성도 holding_id 우선·수량 차선 매칭으로 재작성(19개 계좌 모두 포함, 총평가액 18.83억 vs 초기 19억) |
| V7 | ⏳ 사용자 결정 | `v_gc` 감쇠 경고(3개월 -25.1%): 신규 진입 shadow 추가(`VT_SHADOW_STRATEGIES`에 `v_gc`) vs 3개월 관찰. LAN 접속 제한(`vite preview`를 127.0.0.1로): 사용자 결정 |
| V8 | ⏳ 시각 도래 후 | 아래 16-3 체크리스트 |
| V9 | ✅ | §10-3/§10-4 순서 정정, §14-6 상태 갱신(shadow·새 잡·cryptography 반영 완료, 원장 수정 완료), 본 절 추가 |

### 16-3. 첫 거래일(09-28)·월말(09-30) 실행 검증 체크리스트 (V8)
- 09-28 18:00 `KIS일별수집`이 `collection_job_runs`에 success, **19:30 `종가공식검증`** → `price_close_verify_log` 불일치율(불일치 시 그 시각 `price_history.created_at`과 수집 원장으로 원인 수집기 특정, §11 S7).
- 09-28 20:50 `가드사후성과`(가상계좌평가·실행괴리 포함): momentum·peak 신규 진입이 `guard='shadow_strategy'`로 기록(가격·KOSPI)되고 `peak_holding` 신규 진입 0인지, `virtual_account_equity_daily`·`execution_slippage_log` 갱신.
- 매일 07:10 `데이터계약점검`: 현재 기대 상태 = default_missing ok · cfs_ofs ok · snapshot_lookahead ok · unit_inversion warn(196490 1건) · universe_fresh ok(다음 KRX 일별 잡부터 자동 유지) · date_format ok · close_verify ok.
- 09-30 20:30 `월간피처스냅샷`: 09-23 월중 시점이 09-30 월말로 대체되는지, `model_score`가 클리핑 버전(0.07~0.95)인지, 점검(85%·PER 40%) 통과, **정본 PER 채움률이 62% 안팎으로 유지**되는지.
- 10-01 06:30 `전략감쇠감시` 두 번째 실행, 실행괴리 n≥20 도달 시 `_SLIP_TIERS` 상향 검토(현재 진입 역방향 괴리 +0.29% vs 가정 0.13%, n=19).

### 16-4. 사용자 조치 상태
| 조치 | 상태 |
|---|---|
| Cloudflare Access | ✅ 적용·실측 완료(09-26 10:09). **원인**: 앱 `stock`에 붙은 정책 `Stock_dashboard`(Service Auth, Include `Country=Korea, South`, 00:01 생성)가 Access 평가 순서상 가장 먼저 실행되어 한국발 요청 전부를 로그인 없이 통과시키고 익명 앱 토큰(`type=app`, 이메일 없음)을 발급. 조치(사용자): 앱에서 해당 정책 제거 + `newsinfo.cloud` 앱에 `api` 호스트명 추가(기존엔 `api.newsinfo.cloud` 미보호였음). 실측: `stock.leanguy.cloud` 전 경로·`api.newsinfo.cloud`가 로그인(302)으로 리다이렉트, 익명 토큰 발급 0건. 서버: `.env`에 `CF_ACCESS_TEAM_DOMAIN=macmini-stock.cloudflareaccess.com`·`CF_ACCESS_AUD`(공개 값, 앱 로그인 URL의 `kid`)·`CF_ACCESS_ALLOWED_EMAILS` 추가, 재시작 후 로드·JWKS 조회 확인, 위조 JWT·무토큰 401. 로그인한 본인 이메일 JWT는 API 토큰 없이 통과(실제 브라우저 로그인 확인은 사용자) |
| CEO 플랫폼: 위험 쓰기 엔드포인트에 세션 인증, `.venv312b` 전환·8011 재시작 | ⏳ 사용자/다른 세션(Access로 외부 접근은 이미 보호됨 — 남은 것은 다층 방어) |
| V7 결정(v_gc shadow, LAN 제한) | ⏳ 사용자 |
| R3 플래그·R6 enforce | ⏳ 사용자(기본 꺼짐/기록 전용) |

### 16-5. 게이트 재설계 — 다중 사용자 사이트 반영 (2026-09-26, 사용자 지적 3건)
| 지적 | 조치 |
|---|---|
| 계좌현황에 들어가면 비밀번호 뒤에 API 토큰을 또 묻는다 | 계좌현황 GET은 토큰 대신 **서버가 검증한 계좌현황 비밀번호 쿠키**(`pf_view`, 12시간, HMAC 서명, HttpOnly)로 보호. 401 사유가 `portfolio_password_required`라 프런트 토큰 입력창이 뜨지 않음. 프런트 로그인 모달이 `POST /api/portfolio-access/login`을 호출(비밀번호는 `.env` `PORTFOLIO_VIEW_PASSWORD`로 이동, **브라우저 코드·배포 번들에서 제거**, 실측 0개 파일) |
| 친구·외부인이 로그인하는 사이트인데 계속 물어보면 곤란 | 접근 3단계로 재설계(`security_gate.py`): **public**(일반 조회·전략 화면·검색 분석 요청 — 인증 없음), **viewer**(`/api/portfolio`·`/api/realtime/prices`·`/api/kis-trading`·`/api/live-orders`·`/api/commands` GET — 비밀번호 쿠키), **owner**(허용 목록 밖 쓰기 전부·다운로드·내보내기·백업·관리·설정·API 문서 — API 토큰 또는 관리자 이메일의 Access JWT). 친구는 토큰을 절대 요구받지 않음 |
| 기본은 읽기 전용, 수정·반출만 차단 | 쓰기 49종을 프런트에서 추출·분류: 기본 owner, 일반 기능인 `/api/commands/analyze/{code}`(검색·분석)·`/api/sector-define/parse`만 `API_PUBLIC_WRITE_PATTERNS` 허용 목록으로 공개(IP당 분당 30회). 대량 수집 방지로 터널 경유 GET에 IP당 분당 300회 제한(`API_RATE_LIMIT_PER_MIN`, 0=끔), 로그인 시도 IP당 10분 8회 |
검증: 테스트 16건(3단계·쿠키 위조·만료·다른 비밀로 서명·무차별 대입·요청 제한·Access 관리자 이메일 조건·친구 이메일은 관리자 아님), 로컬 시나리오 실측(일반 200 / 계좌현황 비밀번호 전 401·후 200 / 쿠키 있어도 쓰기·다운로드 401 / 위조 쿠키 401 / 관리자 토큰 통과). **주의**: 쓰기 중 일반 사용자에게 필요한 기능(예: 보고서 생성)이 있으면 `API_PUBLIC_WRITE_PATTERNS`에 추가해야 함(현재는 관리자 전용). 계좌현황 비밀번호는 4자리 숫자라 강도가 낮음 — 길게 바꾸려면 `.env` 값만 변경(친구에게 공지). **Cloudflare Access 앱 `stock`은 관리자 이메일만 허용이라 친구가 들어오지 못한다 — 다중 사용자 사이트에서는 앱을 제거하거나 친구 이메일을 허용해야 함(사용자 결정)**
**범위 정정(2026-09-26 후속, 사용자: "계좌현황만 제외하고 친구는 모두 볼 수 있어야 함")**: 열람(비밀번호) 단계를 계좌 정보로만 축소 — `/api/portfolio`(보유)·`/api/portfolio/transactions`·`/api/realtime/prices`(보유 종목 시세)·`/api/kis-trading/account/*`·`/api/kis-trading/cash-ledger`. 관심종목·수집 상태(`/api/commands/*`)·가상매매 paper(주문·손익·포지션)·리스크게이트·상태 조회는 공개로 전환(일반 화면이 사용). 실측: 공개 8개 경로 200, 계좌 5개 경로 비밀번호 전 401·후 200, 내보내기(`/api/portfolio/export/excel`)·쓰기는 비밀번호 쿠키로도 401. **Cloudflare Access 앱은 서버 코드로 우회할 수 없는 엣지 설정이라 사용자가 앱을 삭제해야 친구가 바로 접속 가능**.

### 16-6. 잔여 항목 처리 (2026-09-26, 사용자 결정 반영)
| 항목 | 처리 |
|---|---|
| V7 `v_gc` | ✅ **shadow 추가**(사용자 승인): `VT_SHADOW_STRATEGIES=momentum,peak,v_gc`, 재시작·로드 확인. 신규 진입만 기록 전용, 보유분 청산·실주문 경로 불변. 롤백=`.env`에서 `v_gc` 제거 후 재시작 |
| LAN 제한(`vite preview` 127.0.0.1) | 사용자 결정: **현행 유지**(같은 LAN에서 5173 직접 접속은 터널 헤더가 없어 게이트를 거치지 않음 — 감수) |
| `196490` 디에이테크놀로지 | ✅ KRX 공식(pykrx) 9/23 시가 450·고가 473·저가 202·종가 202·거래량 3,277,513이 `price_history`와 정확히 일치 → 가격은 정확, `stock_universe.market_cap` 691억(=정지 중 기준가 6,090원×주식수)만 stale → 22.9억으로 정정(`data_fix_log`), 단위 역전 점검 0건 |
| **CEO 플랫폼 노출(신규 발견, 심각)** | `api.newsinfo.cloud`가 Access 앱 삭제 후 **무인증 공개(200)**. 관리자 확인이 `role: Literal["admin","ceo","staff"]` **쿼리 파라미터**(요청자가 적는 값, 서버 세션 검증 아님)라 `?role=admin`만으로 `PUT /telegram-settings`(봇 토큰 교체)·`POST/PUT/DELETE /app-users`(관리자 계정)·`/openai-settings`·피드 삭제 등이 가능한 구조(코드 확인, 악용 시험은 하지 않음). 사용자 결정: **Cloudflare Access 재설정**(본인·직원 이메일만 허용, Service Auth·Bypass·국가 조건 금지). 근본 수정(role을 로그인 세션에서 서버 검증)은 CEO 백엔드를 편집 중인 다른 세션의 과제로 남김 |

### 16-7. 잔여 항목 추가 처리 (2026-09-26 오후)
| 항목 | 처리 |
|---|---|
| CEO 백엔드 환경 전환 | ✅ `codex/ceo-briefing-platform/backend/.venv`→`.venv312b`(fastapi 0.141.1·starlette 1.7.0) 링크 전환, 8011 재기동(PID 86404→85381, 사용자 셸에서 띄운 프로세스라 `nohup … uvicorn` 방식으로 동일 재기동), 로컬·공개 /health 200, OpenAPI 작업 135개 전환 전과 동일, `/agentic-code` 200, pip-audit 0건. 롤백=`ln -sfn .venv312 .venv` 후 재기동. **프런트(5500 포트, `newsinfo.cloud` 원본)는 꺼져 있어 502 — Access 재설정 전에는 켜지 않음**(취약한 백엔드 사용 유도 방지) |
| 연구 venv | ✅ setuptools 84.0.0. 라이선스 확인: vectorbt 1.0.0 = Apache-2.0 + **Commons Clause**(제3자에게 이 소프트웨어 기능이 가치의 대부분인 상품·서비스를 유료 제공(호스팅 포함)하는 것 금지 → 내부 연구용은 무방, 유료 서비스에 탑재 금지·현재 연구 venv에만 존재), quantstats·alphalens-reloaded Apache-2.0, PyPortfolioOpt·LightGBM MIT |
| 수급 수량 NULL(09-14~18) | 조사 완료 — 수량 컬럼(`inst/frn/ind_net_buy`) 약 2,420종목 결측(정상 ~250), 금액 컬럼은 정상. 사용처: 스크리너 최근 5거래일 수량 합만 영향(창에서 빠지는 09-29에 자동 해소), 텐배거 엔진은 금액 사용. **추정값 채우기 안 함**(amt/close 근사는 오해 소지) |
| 격리 가격행의 백테스트 노출 | 측정 완료(`scripts/report_quarantine_backtest_exposure_20260926.py`): `backtest_common`이 `price_history`를 직접 12곳에서 읽고 `return_usable`을 거르지 않음(사실). 그러나 선정 실행 청산 거래 14,595건 중 격리 날짜를 지나는 거래는 **33건(0.23%)**, 전략별 최대 low_base_breakout 2.27%·extreme_dd_volume 1.86% → **12곳 조회 수정·전 결과 재기준화는 불필요**, 권고=격리 종목·구간을 신호 후보에서 제외하는 가드(별도 결정) |
| 종목 상세 기업행위 마커 | 이미 구현·연결됨(차트 `actions` prop ← 기존 이벤트 조회). 추가 작업 없음 |


### 16-8. 2026-09-26 후속: CEO 로그인 · KRX 별칭 · R3/R6 판단 · 점프 재분류

1. **CEO 플랫폼(newsinfo.cloud) 로그인**: `backend/role_gate.py`(순수 ASGI RoleGate — 터널 경유 요청은 세션 필수, `?role=` 은 서버 세션 role 로 덮어씀, `/app-login` 10회/10분 제한), `frontend/ceo-auth.js`(ID/PIN 모달·Bearer 자동 첨부). 8011(uvicorn)·5500(정적)은 nohup 으로 재기동됨(launchd 아님 → 재부팅 시 자동 복구 안 됨). **주의**: 기본 계정 admin/ceo/staff 의 PIN 이 4자리 평문 저장 → 강한 값으로 교체 + 해시 저장 권장(미구현). CEO 저장소 변경은 커밋하지 않음(다른 세션 작업과 혼재).
2. **KRX 인증**: `.env` 는 `KRX_DATA_ID/PASS` 이고 pykrx 는 `KRX_ID/PW` 를 읽음 → `config.py` 에서 별칭 연결. `import config` 후 pykrx 로그인 성공(ETF 1,175 / 전체 2,870). KRX 는 반복 로그인 시 일시 차단(JSON 파싱 오류)될 수 있음 → 스크립트는 pykrx 를 지연 import.
3. **R3(회전 규칙)/R6(계좌 낙폭 규칙) 판단(위임받음)**: R3 플래그(`VT_MIN_MCAP_EOK`, `VT_REENTRY_COOLDOWN_DAYS`)는 **기본 off 유지**(학습·검증·급락 3구간 모두 개선한 단일 규칙 없음, 다중검정), R6 는 **shadow 유지**(전 구간 MDD 악화). 실거래 경로 변경 없음.
4. **`unresolved_active_common` 126건 KRX 교차검증**(`scripts/resolve_unresolved_jumps_krx_20260926.py`, 결과 `research_outputs/unresolved_jumps_krx_20260926.csv`): KRX 원시·수정 종가 모두 점프 43건 → `raw_source_confirmed_jump_review`, 원시만 점프·수정계열 매끈 19건 → `corporate_action_pending_confirmation`(KRX 계수 증거), **우리 값이 KRX 원시와 0.5% 이상 다른 54건과 KRX 무자료 10건은 수동 검토로 남김**(2010–2014 집중, 자동 교정 안 함 — 수정계열 조회 불가로 원인 확정 불가). 잔여 `unresolved_active_common` 64건. `data_fix_log` 기록됨, 가격 데이터·return_usable 변경 없음.
5. **미처리(다음)**: 일일 미설명 점프 점검을 data_contract_audit 에 추가, 종가 이전 봉 수집 원인 확인(9/28 검증 결과 필요), US 결측 재수집 모드, 컨센서스 이력 백필 타당성, 텐배거 기준선 문구(v4 수치) 정정, 09-30 월간 스냅샷 교체·10-01 감쇠 감시 확인.

### 16-9. 2026-09-26 C 기술과제 후속 처리

| 항목 | 결과 |
|---|---|
| 일일 미설명 점프 점검 | `data_contract_audit_20260925.py`에 `unexplained_jump` 추가(최근 10일, ±31% 이상이면서 `price_jump_audit` 미분류; 정수배 비율=fail, 그 외 warn — 7/12 SQLite 배치 같은 혼합기준 오염의 전형). 첫 실행: 196490 2026-09-23 6,090→202(거래정지 후 재개, 다음 점프감사에서 분류 예정) 1건 warn |
| 격리 종목 후보 제외 | 격리(`quarantined_basis`) 118종목 중 2026년 이후 이벤트 0 → 현재 후보엔 영향 없음. 대신 `ingestion_quarantine_recent`(최근 2일 KR 수집 거부 종목, 중복일자 배치 제외, 50종목 초과 시 warn) 추가. 첫 실행 1,170종목 warn = 9/26 09:30 KIS itemchart 1회성 배치(1,168종목 `historical_overlap_basis_mismatch`)를 게이트가 정상 거부한 것 — 일일 반복 아님, 2일 뒤 자연 해소 |
| US 결측 재수집 | 3,670종목·1,342 거래일 점검: 5일 초과 결측 56종목(대부분 권리·워런트 등 저유동 심볼, 합계 3,829 — 실제 수집 실패 아님), 정체(9/20 이전 종료) 115종목은 상장폐지 추정. **재수집 모드 불필요**로 판단(별도 구현 안 함). 9/25 3,421종목은 수집 진행 중 스냅샷 |
| 컨센서스/EPS 이력 백필 | `consensus_targets` 8,665행(2024-05-08~, 목표가·직전목표가 포함) — 목표가 수정 이력은 약 2.3년치. `forward_estimates`(EPS 등) 1,011행·스냅샷 4,066행은 2025-04~로 **현재 시점 추정치만 제공하는 원천**이라 과거 EPS 수정 이력은 소급 불가. 결론: EPS 수정 팩터는 앞으로 스냅샷을 쌓아야 검증 가능(현재 워크포워드에 쓰기엔 표본 부족), 목표가 수정은 약 2년 창으로 참고용만 |
| Postgres 가드 테스트 | `tests/test_price_write_guard_postgres_20260926.py`(5건): 실제 트리거 함수를 TEMP 테이블+롤백 트랜잭션에서 검증(close<=0, 소수 OHLC는 checked여도 거부, 미검증 과거 INSERT 거부/checked 허용, 당일·비KR 통과, 무변경 UPDATE 허용/변경 UPDATE 거부). 운영 `price_history` 무접촉 |
| QuantStats 열 | 전략센터 성과 매트릭스에 `QuantStats(Sharpe·MDD)` 열 추가 — `/api/research/quantstats` 사용, 근사 곡선(거래로그 재구성)은 "근사" 배지, 툴팁에 CAGR·KOSPI·알파. `frontend/dist` 재빌드 |
| 텐배거 기저율 문구 | CLAUDE.md의 0.82%/1.23%는 옛 18.1만행(v2/v3) 기준. 운영 v4(190,609행) 실측은 10x/24m 1,171건(0.61%), 3x/12m 9,432건(4.95%)로 정정 |
| 대기(날짜 의존) | 종가 이전 봉 수집 원인 특정은 9/28 `종가공식검증` 결과 필요. 근사 곡선 전략(엔진이 일간 곡선을 안 내는 전략)은 엔진 개조가 필요해 미착수. curl_cffi는 yfinance<0.14 제약으로 보류 |
