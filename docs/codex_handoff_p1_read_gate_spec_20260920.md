# P1 — live 신호경로 fail-closed read-gate 구현 명세 (RED-first)

> 상태: **구현 명세 + failing test** (값 쓰기 금지 — 코드 미구현, RED 먼저 제시)
> 근거: `research_outputs/stage1_pipeline_mapping_20260920.md` (Stage-1: live 전략엔진이
> canonical/quarantine/point-in-time 게이트를 우회) + P0 승인 후 Planner 지시.

---

## 0. 배경 (Stage-1 실측 재확인)

- 라이브 PG에 canonical/quarantine 레이어는 **존재·충족**:
  view `canonical_price_history_v`(`return_usable`)·`canonical_price_returns_v`(`safe_daily_return`),
  table `canonical_financial_data`(93,734행)·`canonical_cashflow_data`(80,592행),
  `price_jump_audit`(25,098)·`price_integrity_quarantine`(1,855,059)·`price_ingestion_quarantine`(27,334).
- 그러나 live 전략엔진은 raw를 직접 읽는다: `signal_engine.py` **price_history 48곳 + financial_data 17곳**,
  `screener.py` price_history 5 + financial_data 4. `tenbagger_engine.py`도 동일 패턴.
- 결과: `hermes.md`에 기록된 격리(price-jump 6,904·mixed-basis 626·invalid-ohlcv 38 등)와
  재무 이상(BS identity, negative-revenue)이 **live 신호 경로를 차단하지 못함** — integrity는 advisory.

---

## 1. Planner 요구 → 계약 매핑

| Planner 요구 | 본 명세의 계약 |
|---|---|
| raw `price_history`/`financial_data` 직접조회를 최소 교체 | §2 — 공용 helper `signal_data_gate.py`로 read 경로 단일화, 호출부는 swap만 |
| canonical/quarantine gate를 **fail-closed** 강제 | §3 — 격리/불량 행은 구조적으로 반환 금지(반환 목록에 애초에 없음) |
| point-in-time availability | §4 — 가용일(`feature_available_at`) 이후 fact만 채택 |
| 결측·격리 행 제외 사유+카운트를 결과/백테스트 아티팩트에 기록 | §5 — `excluded`(reason→count) 반환, 호출부가 로그/run_spec에 남김 |
| 새 데이터 쓰기 금지 | §6 — read-only (INSERT/UPDATE/DELETE 없음) |

---

## 2. 공개 API (failing test가 import할 계약)

```python
# signal_data_gate.py (미구현 — 본 명세가 계약 정의)

from dataclasses import dataclass, field

@dataclass
class PriceGateResult:
    rows: list                # usable 행(date,open,high,low,close,volume,...) — return_usable=1만
    total_in_window: int      # 해당 구간 raw price_history 행 수 (usable+excluded)
    usable: int               # len(rows)
    excluded: dict            # reason -> count  (비어있으면 {})
    excluded_rows: list       # [(date, reason), ...] 감사 로깅용 (상한 50)

def read_prices_failclosed(conn, stock_code, start, end, *,
                           fields=("date","open","high","low","close","volume"),
                           allow_suspended=False) -> PriceGateResult:
    """canonical_price_history_v 기준 fail-closed 일별 가격 조회.

    return_usable=0(격리/불량) 행은 절대 반환하지 않고, reason+count를 excluded에
    담아 호출부가 조용히 빼지 않도록 강제한다(무사일ent exclusion 금지).
    """

def read_financials_failclosed(conn, stock_code, *, year=None, quarter=None,
                               is_annual=False, report_type="CFS",
                               fields=("revenue","operating_profit","net_income",
                                       "total_assets","total_equity")) -> list:
    """canonical_financial_data(gated)만 조회. raw financial_data는 안 읽는다.

    write-gate를 통과한 canonical 행이 없으면 빈 리스트(구조적 부재) — BS identity
    위반 등으로 gate에 막힌 raw 행은 애초에 노출되지 않는다(fail-closed).
    """
```

---

## 3. 가격 fail-closed 규칙 (§1-1/§1-2 재사용)

`read_prices_failclosed`는 `canonical_price_history_v`의 `return_usable=1` 행만 반환.
`return_usable=0`이 되는 조건(이미 `price_integrity.py`에 구현·검증됨):
- `quality_status NOT IN ('normal','insufficient_history')` — invalid_ohlcv /
  quarantined_basis / invalid_previous_price / coverage_gap / unexplained_jump / suspended.
- `price_jump_audit`에 event_date가 존재(stale_audit 포함).

`excluded`는 같은 구간의 `canonical_quality`(또는 `quality_status`)를 집계해 reason→count로
반환. `allow_suspended=True`면 `suspended`(거래정지) 행은 excluded 집계에서만 제외(반환은 안 함).

> `canonical_price_history_v`의 LAG는 격리 촛대를 건너뛰지 않고 전 행을 유지하므로,
> fail-closed로 특정 날을 빼도 시계열 압축(skip-over)이 없다(설계상 보장).

---

## 4. 재무 fail-closed + point-in-time (§FIX-B/C)

- `read_financials_failclosed`는 `canonical_financial_data`만 읽는다. `gate_financial_row`
  (EMPTY_FINANCIAL_ROW / NEGATIVE_REVENUE / OP>REV / BS_A=L+E 위반)를 통과한 행만 존재하므로,
  불량 raw 행은 구조적으로 노출 불가.
- point-in-time: `canonical_financial_data.updated_at`(게이트 시각)을 `feature_available_at`로
  간주. as-of 날짜가 주어지면 `updated_at <= as_of`인 행만 채택(1차 게이트). 이번 P1에서는
  helper가 `as_of` 인자를 받아 이 필터를 적용(기본 `as_of=None`=전체).

---

## 5. 제외 사유·카운트 기록 (무사일ent 금지)

`PriceGateResult.excluded`(reason→count)와 `excluded_rows`를 호출부가 반드시
로그/`backtest_runs.summary_text` 또는 run_spec에 남긴다. swap 시 각 호출부에
`excluded`를 남기는 최소 코드를 함께 넣어 "조용한 제외"를 구조적으로 차단.

---

## 6. 범위·비범위

- **범위**: `signal_data_gate.py`(신규) + `signal_engine.py`/`screener.py`의 price/financial
  read 경로를 helper로 최소 swap. 쓰기 금지(DDL/DML 없음).
- **비범위(별도)**: `tenbagger_engine.py`(동일 패턴이나 별도 검토), Minervini 가상매매
  allowlist/governance(Planner가 P1 검증 후 별도 변경으로 지시), point-in-time 전체 도입은
  1차 가용일 게이트까지만.

---

## 7. TDD 순서

1. **RED**: `tests/test_signal_data_gate.py` 작성(G1/G2 계약 assert) → import 실패로 RED.
2. **GREEN**: `signal_data_gate.py` 구현(§3/§4), in-memory SQLite + `rebuild_views`로 검증.
3. **REFACTOR**: `signal_engine.py`/`screener.py` read 경로 swap + `excluded` 로깅.
   Checker가 live 재실행·회귀·무라이브쓰기·무추정 원칙을 독립 검증.

---

## 8. 미확정(구현 시 실측 확정)

- `canonical_financial_data`가 raw `financial_data`의 CFS valuation 행을 얼마나 커버하는지
  (커버리지 갭 시 signal_engine의 PER/PBR/ROE 경로에서 표출 감소 — fail-closed로는 정상이나
  로그로 보고).
- `signal_engine`의 17개 financial_data 조회 중 어느 것이 valuation(canonical 대체)인지,
  어느 것이 시계열 growth(원시 직렬 필요 — canonical에 시계열 전부 존재하는지)인지 분류.
