# 손익계산서 당분기(Standalone) 복원 — 구현 명세 (TDD)

> 상태: **구현 명세** (값 쓰기 금지 — 파이프라인 미구현, failing test 먼저)
> 상위 설계: `docs/codex_handoff_is_quarterly_standalone_design_20260920.md`
> 참조 구현(DB 무접근, self-test 7/7): `scratch/is_standalone_reference.py`
> 대상: `scripts/is_standalone_pipeline.py` (미구현 — 본 명세가 계약을 정의)

---

## 0. Planner 요구 → 계약 매핑

| Planner 요구 | 본 명세의 계약 |
|---|---|
| CFS/OFS 분리 | §2 — 연결 fact는 CFS 행만, 별도 fact는 OFS 행만. 교차 기입 금지 |
| Q2~Q4 `당분기 = 누적 − 직전누적` | §3 폴백 경로 (1순위는 직접 당분기 fact) |
| 최초분기·정정공시·결측 시 산출 거부/플래그 | §4 — Q1 누적=당분기 특별취급, 재작성(PFY 불일치)·직전 누적 결측은 SKIP |
| 원문/계산값/근거 분리 저장 | §5 — `is_standalone_derivation` 테이블에 3계층 분리 |

> **Planner 가정 수정(이미 실측됨)**: "누적→당분기 차감"이 아니라, 보고서 XML에 당분기
> (3개월 단독) fact가 **직접 태깅**되어 있다(ACONTEXT `...d{PERIOD}Q`). 따라서 1순위는
> 직접 추출, 2순위가 누적 차감 폴백이다. 차감은 직접 fact가 없는 발행사에서만 사용.

---

## 1. 공개 API (failing test가 import할 계약)

```python
# scripts/is_standalone_pipeline.py

from dataclasses import dataclass

@dataclass
class Derivation:
    mode: str                       # 'DIRECT' | 'SUBTRACT' | 'SKIP'
    raw_value: float | None         # 원문: DIRECT=당분기 fact / SUBTRACT=현재 누적 fact
    prior_raw_value: float | None   # 원문: SUBTRACT의 직전 누적 fact (그 외 None)
    computed_value: float | None    # 계산값: 최종 당분기 (financial_data에 기입될 유일한 값)
    acode: str | None               # 근거: 채택된 ACODE
    adecimal: int | None            # 근거: 스케일 (ADECIMAL)
    consolidated: bool              # 근거: 연결 여부
    period_token: str | None        # 근거: FQ/HY/TQ/FY
    suffix: str | None              # 근거: Q(당분기)/A(누적)
    skip_reason: str | None         # SKIP 사유

def derive_standalone(xml_text: str, field: str, year: int, quarter: int,
                      prior_cumulative: float | None = None) -> Derivation:
    """XML 원문 → 당분기 파생값. 순수 함수(DB 무접근)."""

def record_standalone(conn, stock_code, year, quarter, field,
                      deriv: Derivation, rcept_no: str, run_id: str) -> bool:
    """계산값만 financial_data에, 원문/계산값/근거를 is_standalone_derivation에 분리 저장."""
```

---

## 2. CFS/OFS 분리 규칙

- `..._ifrs-full_ConsolidatedMember` → `consolidated=True` → **CFS 행만** 기입.
- `..._ifrs-full_SeparateMember` → `consolidated=False` → **OFS 행만** 기입.
- CFS/OFS 교차 기입은 구조적으로 금지(각 파생값에 `consolidated` 플래그가 붙고,
  `record_standalone`은 `report_type = 'CFS' if deriv.consolidated else 'OFS'`로 고정).
- **세부 내역 제외**: 축 뒤에 추가 axis(`_SegmentsAxis_` 등)가 있으면 `SKIP`
  (사유 `extra_axis`). 총액 fact만 채택.

## 3. 추출 알고리즘 (mode 결정)

1. ACODE 매칭 TE 수집 (`revenue`: `ifrs-full_Revenue` +
   `ifrs-full_RevenueFromContractsWithCustomers`; `operating_profit`:
   `dart_OperatingIncomeLoss` → `ifrs-full_OperatingIncomeLoss`; `net_income`:
   `ifrs-full_ProfitLoss`).
2. ACONTEXT 파싱 → `(fy, year, period, suffix, member, extra_axis)`.
3. `CFY{year}`만 채택(PFY는 비교 표시로 제외). period 토큰 일치 필수.
4. **1순위 DIRECT**: `suffix='Q'` + `consolidated` + 총액 → `mode=DIRECT`,
   `raw=computed=fact × 10^(-adec)`. 같은 키 dedupe 후 단일값.
5. **2순위 SUBTRACT**: 당분기 fact 부재 시 `suffix='A'`(누적) fact + `prior_cumulative`가
   모두 존재 → `mode=SUBTRACT`, `raw=현재 누적`, `prior_raw=직전 누적`,
   `computed = raw − prior_raw`.
6. 둘 다 불가 → `SKIP`(사유별: `no_fact` / `missing_prior_cumulative` / `separate_only`
   / `extra_axis` / `restatement_conflict` / `negative_revenue` / `unit_mismatch`).

## 4. 거부/플래그 규칙

| 조건 | 처리 |
|---|---|
| Q1(최초분기) | 누적=당분기 → `suffix='A'`도 직접값으로 취급, 차감 없이 DIRECT 기입 |
| 정정공시(재작성) | 차감 폴백에서 PFY 비교값이 기저장 직전기 값과 불일치 → `SKIP(restatement_conflict)` |
| 직전 누적 결측 | `prior_cumulative is None` → `SKIP(missing_prior_cumulative)`, OPEN 유지 |
| revenue 음수 | 오태깅/오류 → `SKIP(negative_revenue)` |
| net_income/op 음수 | 유효한 손실 → 기입(정상) |
| 동일 계정 ADECIMAL 불일치 | 신뢰 불가 → `SKIP(unit_mismatch)` |

## 5. 원문/계산값/근거 분리 저장

`financial_data.{field}`에는 **계산값(computed_value)만** 기록. 파생 증적은 아래 전용
테이블에 3계층 분리 저장(기존 `financial_fix_log`와 별도).

```sql
CREATE TABLE IF NOT EXISTS is_standalone_derivation (
  id               INTEGER PRIMARY KEY,
  stock_code       TEXT, year INTEGER, quarter INTEGER, field TEXT,
  mode             TEXT NOT NULL,          -- DIRECT / SUBTRACT / SKIP
  raw_value        REAL,                   -- 원문 (당분기 fact 또는 현재 누적 fact)
  prior_raw_value  REAL,                   -- 원문 (직전 누적 fact, SUBTRACT 전용)
  computed_value   REAL,                   -- 계산값 (최종 당분기)
  acode            TEXT, adecimal INTEGER, consolidated INTEGER,
  period_token     TEXT, suffix TEXT,
  skip_reason      TEXT,
  rcept_no         TEXT, run_id TEXT, created_at TEXT
);
```

`record_standalone` 계약:
- `mode in ('DIRECT','SUBTRACT')` → `UPDATE financial_data SET {field}=computed_value`
  (조건: `is_annual IS FALSE AND report_type=<CFS|OFS> AND {field} IS NULL`), 그 후
  `is_standalone_derivation`에 raw/prior_raw/computed/근거 전부 INSERT.
- `mode='SKIP'` → `financial_data` **변경 없음**. `is_standalone_derivation`에
  `skip_reason`과 raw(가능 시)만 기록(감사 추적).

---

## 6. TDD 순서

1. **RED**: `tests/test_is_standalone_pipeline.py` 작성(본 명세 계약을 assert) → import 실패로 RED.
2. **GREEN**: `scripts/is_standalone_pipeline.py` 구현(참조 구현 로직 재사용 + §5 저장 분리).
3. **REFACTOR**: DB 통합(flag `source_count 0→1`, `financial_fix_log` 연동) 후
   Checker walk-forward/point-in-time 검증.

## 7. 미확정(구현 시 실측 확정)

- Q4 `FY` 토큰 / `dFYQ`·`dFYA` suffix(사업보고서 1건 실측 필요).
- `fin_quarterly_validation_flags.field` 명칭이 `operating_profit`(financial_data)인지
  `op_profit`(flag 주석)인지 — 구현 전 flag DDL/실데이터로 확정(기입 대상 조회 쿼리 영향).
