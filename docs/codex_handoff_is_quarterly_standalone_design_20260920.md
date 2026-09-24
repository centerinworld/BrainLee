# 손익계산서 당분기(Standalone) 복원 — 설계안 (값 쓰기 없음)

> 상태: **설계 제출용**. DB 값 쓰기 없음. Checker 검토 후 구현·적용 예정.
> 근거: 삼성전자(005930) Q1 2026·Q2 2025·Q3 2025 실제 보고서 XML 직접 해부
> (`scratch/inspect_is_facts.py`, `scratch/dump_is_contexts.py`, `scratch/verify_q1_tokens.py`).

---

## 0. 핵심 결론 (Planner 가정 수정)

Planner는 "누적값→당분기값 차감"을 전제했으나, 실측 결과 **당분기(3개월 단독) 값이
보고서 XML에 이미 직접 태깅**되어 있다. 따라서:

- **1순위(주 경로)**: 당분기 fact(`ACONTEXT ...d{PERIOD}Q`)를 **직접 추출** (차감 없음).
- **2순위(폴백)**: 당분기 fact 부재 시 `누적(Q_n) − 누적(Q_{n-1})` **차감**.

직접 추출이 차감보다 단순·안전(재작성/음수/분기누락 리스크 제거)하므로 우선 사용한다.

---

## 1. ACONTEXT 인코딩 규격 (실측)

TE 요소는 `ACODE`/`ACONTEXT`/`ADECIMAL` 속성으로 계정·기간·연결여부·단위를 인코딩한다.

```
<TE ACODE="ifrs-full_Revenue"
    ACONTEXT="CFY2025dHYQ_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_ifrs-full_ConsolidatedMember"
    ADECIMAL="-6">22,231,952</TE>
```

### 1-1. 기간 토큰 (`d{PERIOD}{Q|A}`)
| 분기 | 보고서 | PERIOD 토큰 | 당분기 suffix | 누적 suffix | 비고 |
|------|--------|-------------|---------------|-------------|------|
| Q1 | 분기보고서 | `FQ` (First Quarter) | `dFQQ` | `dFQA` | 실측(2026 Q1) |
| Q2 | 반기보고서 | `HY` (Half Year) | `dHYQ` | `dHYA` | 실측(2025 Q2) |
| Q3 | 분기보고서 | `TQ` (Three Quarters) | `dTQQ` | `dTQA` | 실측(2025 Q3) |
| Q4 | 사업보고서 | `FY` (Full Year) | `dFYQ` | `dFYA` | **미실측 — 구현 시 확정** |

### 1-2. 연도 축
- `CFY{year}` = 당기(현행 회계연도) → **채택**
- `PFY{year}` = 전기(비교 표시) → **제외** (중복/재작성 오염 방지)

### 1-3. 연결 축
- `...Axis_ifrs-full_ConsolidatedMember` → **연결(CFS)** 행에만 기입
- `...Axis_ifrs-full_SeparateMember` → **별도(OFS)** 행에만 기입 (CFS에 기입 금지)

### 1-4. 총액 vs 세부 항목 (핵심 필터)
총액 fact의 ACONTEXT는 `...ConsolidatedMember`로 **정확히 끝남**(추가 axis 없음).
아래 축이 붙은 fact는 **세부 내역이므로 제외**:
`MajorCustomersAxis`, `SegmentsAxis`, `GeographicalAreasAxis`,
`PerformanceObligationsAxis`, `ComponentsOfEquityAxis`, `CarryingAmount...Axis`, 기타 `_Axis_`.
동일 (ACODE, ACONTEXT)가 표·주석에 중복 등장하며 값이 같음 → dedupe.

---

## 2. 계정 코드 매핑 (기존 매핑 **오류 수정 포함**)

| 필드 | ACODE (우선순위) | 실측 | 비고 |
|------|------------------|------|------|
| revenue | `ifrs-full_Revenue`, `ifrs-full_RevenueFromContractsWithCustomers` | 확인 | |
| operating_profit | **`dart_OperatingIncomeLoss`** → `ifrs-full_OperatingIncomeLoss` | **`dart_` 확장코드 실측** | 기존 `_FACT_CODES`가 `ifrs-full_OperatingIncomeLoss`만 보아 삼성전자에서 0건 — **기존 매핑 버그** |
| net_income | `ifrs-full_ProfitLoss` | 확인 | `ifrs-full_ProfitLossAttributableToOwnersOfParent`은 지배주주 귀속분(총액과 다름) — 총액은 `ProfitLoss` |

> 주의: `ifrs-full_ProfitLossBeforeTax`(세전) ≠ 영업이익. `GrossProfit`(매출총이익) ≠ 영업이익.

---

## 3. 단위/스케일 (ADECIMAL)

`ADECIMAL="-6"` → 표시값 × 10^6 = 원(won). `-3` → 천원, `0` → 원.
추출 시 **반드시 `10^(-ADECIMAL)` 배율 적용** 후 financial_data(원 단위)에 저장.
동일 계정에서 ADECIMAL이 불일치하면 → 신뢰 불가로 제외(플래그만 남김).

---

## 4. 추출 알고리즘

### 4-1. 주 경로 — 당분기 직접 추출
1. ACODE 매칭 TE 수집.
2. ACONTEXT가 `CFY{year}d{PERIOD}Q_...ConsolidatedMember`(정확히 종료, 추가 axis 없음) 필터.
3. ADECIMAL 배율 적용.
4. (ACODE, ACONTEXT) dedupe → 단일 값.
5. 값 존재·연결 → CFS 행의 `field IS NULL`이고 `source_count=0`이면 기입(감사 로그).

### 4-2. 폴백 — 누적 차감
당분기(`...Q`) fact가 없는 경우:
```
당분기(Q_n) = 누적A(Q_n) − 누적A(Q_{n-1})
```
- `누적A(Q_n)`: 현 보고서의 `...d{PERIOD}A` (CFY).
- `누적A(Q_{n-1})`: 직전 분기 보고서의 누적 fact — **별도 다운로드 필요**.
- Q1은 `FQA == FQQ`(3개월=누적)이므로 차감 불필요·직접 기입.

---

## 5. 차단 규칙 (Planner 요청 항목)

| 규칙 | 조건 | 처리 |
|------|------|------|
| **음수** | net_income/op: 음수 = 유효한 손실 | **기입**(정상) |
| | revenue 음수 | **제외**(오태깅/오류) |
| **재작성** | 현재 보고서 PFY 비교값 ≠ 기저장 직전기 값 | 주 경로(직접 당분기)는 무관; 폴백 차감은 **제외** |
| **분기누락** | 차감 폴백 시 직전 누적 부재/누락 | **제외**(OPEN 유지) |
| **연결·별도** | ConsolidatedMember | CFS 행만 |
| | SeparateMember | OFS 행만 (CFS 절대 기입 금지) |
| **총액 vs 세부** | 추가 axis 존재 | **제외**(세부 내역) |
| **전기(PFY)** | `PFY{year}` | **제외**(비교 표시) |
| **단위 불일치** | 동일 계정 ADECIMAL 상이 | **제외** |

---

## 6. 안전장치·감사·멱등 (balance-sheet v2와 동일)

- `UPDATE ... WHERE {field} IS NULL` + `source_count=0 AND status='OPEN'` (확정값 `source_count≥2` 구조적 미접근).
- `financial_fix_log`(전/후값·계정·ACODE·단위·XML URL·시각) + flag(`dart_value`, `source_count 0→1`, `notes`).
- JSON checkpoint + `--resume`; DART `020` 중단. 파서는 기존 검증 extractor와 head-to-head 일치 확인 후 적용.

---

## 7. 테스트 fixture (구현 시 필수 통과)

`scratch/is_standalone_reference.py`(참조 구현 + self-test, DB 무접근)가 아래 케이스를 검증한다.
1. 당분기 직접 추출(정상, 연결)
2. 추가 axis(세그먼트/지역) 제외 → 총액만 선택
3. 음수 net_income 정상 기입(손실)
4. revenue 음수 제외
5. 전기(PFY) 제외
6. 당분기 부재 → 누적 차감 폴백
7. 차감 폴백 시 직전 누적 부재 → 제외
8. SeparateMember → CFS 제외(별도 전용)
9. 재작성(PFY 불일치) → 폴백 제외
10. ADECIMAL 배율 적용(-6, -3, 0)
11. 운영이익 `dart_OperatingIncomeLoss` 매핑

---

## 8. 미확정 항목 (구현 시 실측 확정)

- Q4 `FY` 토큰 및 `dFYQ`/`dFYA` suffix (사업보고서 1건 실측 필요).
- 운영이익을 아예 태깅하지 않는 발행사 존재 여부(이 경우 영업이익은 이 원천으로 복원 불가).
- 별도(OFS) 행 존재 여부가 종목마다 다른 점 — OFS 기입은 별도 검토.
