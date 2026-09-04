# Claude → Codex Handoff: macro_signal_backtest_results 재검증 결과 (2026-07-28)

## 배경
`docs/codex_handoff_macro_quant_scheduler_strategy_20260728.md`에서 43개 매크로지표×섹터 조합을
백테스트해 21개를 `promoted`로 표시함(예: `KR_TRADE_BALANCE`×전력기기 PF10.7,
`COMM_COPPER`×전력기기 PF26.8). Codex가 문서 말미에 "Review possible look-ahead in monthly
macro availability"를 Claude에게 요청해 직접 원시 데이터(`macro_signal_backtest_trades`)를
학습(~2022)/검증(2023~) 분리로 재검증함.

## 발견 1 — 데이터 중복 버그
`macro_signal_backtest_trades`에서 `KR_TRADE_BALANCE`×전력기기를 조회하면 216행이 나오지만
`DISTINCT (entry_date, stock_code, ret_60d)` 기준으로는 72행뿐 — 동일 이벤트가 정확히 3배로
중복 저장돼 있음(재실행 시 idempotent upsert 없이 append된 것으로 추정). `macro_signal_backtest_results`
집계 테이블의 `observation_count`는 다행히 72로 정확했으나(중복제거된 값 사용 추정), 원본 trades
테이블 자체는 정리가 필요함 — 스크립트 재실행 전 `DELETE FROM macro_signal_backtest_trades
WHERE run_id=?` 또는 `UNIQUE(run_id, indicator_key, sector_name, stock_code, entry_date)` 제약
추가를 권장.

## 발견 2 — promoted 21개 중 최소 7개가 학습기간 관측치 0건
`entry_date < '2023-01-01'` 기준으로 나눈 결과, 아래 조합은 **학습기간 데이터가 아예 없음**
(train_n=0) — 즉 2023년 이전엔 이 조합의 이벤트 자체가 없거나 데이터가 없어서, "검증"이 아니라
단일 구간(대부분 2024~2026)을 관측한 것에 불과함:

| indicator × sector | train_n | test_n | test avg60d | 비고 |
|---|---:|---:|---:|---|
| `CN_CLI_OECD`×반도체 | 0 | 45 | +27.0% | |
| `COMM_COPPER`×전력기기 | 0 | 224(중복포함) | +27.3% | **2025-06~2026-01 단일 7개월 구간뿐, 28개 고유일자** |
| `COMM_COPPER`×철강/비철 | 0 | 84 | +11.8% | |
| `COMM_NATURAL_GAS`×정유화학 | 0 | 50 | +19.2% | |
| `US_BAA_SPREAD`×금융 | 0 | 522 | +14.5% | |
| `US_HY_SPREAD`×바이오 | 0 | 117 | +16.3% | |
| `US_NFCI`×바이오 | 0 | 54 | +21.9% | |

이 중 `COMM_COPPER`×전력기기가 가장 뚜렷한 사례 — 264건(중복제거 전) 전부 2025-06-27~2026-01-13
사이에서만 발생. 구리는 일별 시계열이라 "이벤트"가 거의 매일 생기는데, 하필 관측 가능한 전체
기간이 전력기기 슈퍼사이클 랠리 구간과 겹쳐서 사실상 "이 7개월간 전력기기가 많이 올랐다"를
"구리가격이 전력기기 수익을 예측한다"로 재포장한 결과로 판단됨. **PF 26.8/hit rate 86.4%는
반복가능한 매크로 타이밍 신호가 아니라 단일 레짐 관측치.**

## 발견 3 — 학습기간이 있는 경우도 종목선정 자체가 이미 알려진 슈퍼사이클과 100% 겹침
`KR_TRADE_BALANCE`×전력기기는 train_n=18(avg+10.0%/hit61%), test_n=54(avg+39.2%/hit85%)로
방향은 일치하지만, 매핑된 3종목(LS ELECTRIC 010120 / HD현대일렉트릭 267260 / 효성중공업 298040)이
이 프로젝트가 이미 2026-07-20~21 세션에서 V-MEGATREND로 독립 발굴한 바로 그 전력기기 슈퍼사이클
종목들임. 검증기 수치 급증이 "무역수지 개선이 전력기기 주가를 예측"해서가 아니라 "이미 아는
슈퍼사이클 종목이 그 구간에 크게 올랐다"의 동어반복일 가능성이 큼 — 이 신호를 무역수지 신호로
일반화해 다른 국면/다른 종목에도 적용 가능하다고 보기엔 근거가 약함.

## 종목 매핑 소스에 대한 우려
`cafe_stock_indicator_mappings`(네이버 카페 게시글 추출) 기반이라 섹터당 3~8종목의 극소표본.
`passes()`의 `min_stocks=2`도 매우 낮은 문턱 — 종목 2개만 있어도 "검증 통과"로 잡힘.

## 발견 4 — 같은 종류의 체결시점 버그가 이 스크립트에도 잠재함(별도 세션에서 fill-timing 버그 확인 후 교차점검)
`docs/codex_handoff_fill_timing_artifact_recheck_20260728.md`에서 `research_strategy_overlay_expansion.py`의
`next_open()`이 신호일 이후 첫 가격행을 **상한 없이** 가져오다 2020년 신호가 몇 년 뒤 가격에
체결되는 버그가 있었음을 확인했음. 동일 클래스의 패턴이 `backtest_macro_indicator_candidates.py`의
`price_path()`에도 존재함을 확인:
```python
def price_path(conn, stock_code, available_date, max_horizon):
    return conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code=? AND date>=? AND close IS NOT NULL AND close>0 "
        "ORDER BY date LIMIT ?", (stock_code, available_date, max_horizon + 1),
    ).fetchall()
```
`date>=?` 조건에 상한이 없어 `available_date` 이후 거래정지/데이터공백이 있으면 `path[0]`
(entry_date/entry_close 기준점)이 조용히 훨씬 뒤 시점으로 밀릴 수 있음. 이번에 검토한
`KR_TRADE_BALANCE`/`COMM_COPPER` 등은 유동성 높은 대형주(LS ELECTRIC 등)라 실제로는 gap이
1~3일로 문제가 드러나지 않았지만, 다른 지표×섹터 조합이나 향후 재실행 시 잠재적으로 같은
아티팩트가 재발할 수 있음. **제안**: Codex가 이미 만든 `next_open(..., before_or_equal=...)`
패턴을 공용 헬퍼(`next_tradable_open(code, signal_date, max_gap_days=10)`)로 승격해
`backtest_macro_indicator_candidates.py`의 `price_path()`에도 동일하게 적용 권장(Codex의
fill-timing 문서 5번 항목과 정확히 같은 제안).

## 결론 및 제안
- 21개 promoted 후보를 Strategy Center나 `quant_indicator_signal_engine.py`의 실제 신호로
  연결하지 않음(현재 미연결 상태가 맞음, Codex의 "Follow-up checks for Claude" 질문에 대한 답:
  **아직 어디에도 노출하지 않는 것을 권장**).
- 재발방지 제안: `passes()`에 "train_n(2023년 이전 관측치) >= 최소 N" 조건을 추가해 단일 레짐
  관측만으로 promoted되는 것을 원천 차단. 이 세션(stock_dashboard) 전체에서 반복 확인된 원칙 —
  "학습/검증 분리 후 방향 일치"가 promoted 여부의 필수조건이지, 전체기간 통계량(PF, hit rate)
  단독으로는 판단 근거가 안 됨.
- `macro_signal_backtest_trades` 중복 저장 버그 수정 권장(idempotent upsert 또는 재실행 전
  기존 run_id 데이터 삭제).

이 문서는 signal_experiment_ledger(strategy_key='codex_review',
experiment_name='macro_quant_scheduler_candidates_review_20260728')에도 동일 내용 기록됨.
