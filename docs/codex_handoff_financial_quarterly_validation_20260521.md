# Codex 핸드오프 — 분기 재무제표(P&L) 검증 및 수정
**작성일**: 2026-05-21  
**작성자**: Claude Sonnet 4.6  
**대상**: Codex (재검증 및 수정 실행)

---

## 1. 지금까지 검증한 것 (완료)

### 1-1. CF 4중 검증 완료 (현금흐름표)
**대상**: `cash_flow_data` 테이블, 2016~2025년 전 종목(2,615종목)  
**검증 소스**: DART × FnGuide × Seibro × AI  
**결과 저장**: `cf_validation_flags` 테이블 (102,133건)

| status | 건수 | 비율 |
|--------|------|------|
| CONFIRMED | 98,792건 | 96.7% |
| CLOSE_MATCH | 3,341건 | 3.3% |
| AMBIGUOUS | **0건** | 0.0% |

**검증한 필드**: `operating_cf`, `investing_cf`, `financing_cf`, `cash_end`, `depreciation`, `capex`

### 1-2. 연간 재무(P&L) 검증 부분 완료
**대상**: `financial_data` 테이블, `is_annual=1` 행  
**검증 소스**: DART × FnGuide (2-way)  
**결과 저장**: `cf_validation_flags` 테이블 (flag_type='FIN_CROSS', 'FIN_NAVER')  
**주요 수정**: 
- revenue 100배+ 오류 74건 NULL 처리
- 2022 DART CFS 파싱오류 19종목 FnGuide 교체 (`data_source='fnguide_revenue_fix'`)
- dart_collector.py revenue 키워드 버그 수정 ("매출원가" 매칭 방지)
- dart_mapping_engine.py operating_profit 매핑 버그 수정

---

## 2. 검증 안 된 것 (이번 Codex 태스크)

### 2-1. 분기 P&L 데이터 품질 — **완전 미검증**

**`financial_data WHERE is_annual=0` (분기 데이터)는 4중 검증 대상에서 제외됨**

현재 발견된 심각한 오류:

#### 문제 A: 분기 revenue < 0 → **1,139건**
```sql
SELECT stock_code, year, quarter, revenue
FROM financial_data
WHERE is_annual=0 AND revenue < 0
ORDER BY stock_code, year, quarter;
-- Q4 음수 665건, Q1~Q3 음수 474건
```

**Q4 음수의 원인**: Q4는 직접 수집하지 않고 `연간 - Q1 - Q2 - Q3`으로 계산하여 저장.  
연간 DART 값이 틀리거나 Q1~Q3 값이 과대 저장되면 Q4가 음수가 됨.

**SK하이닉스(000660) 사례**:
```
연도  | DART연간(저장)  | Q1+Q2+Q3(저장)  | Q4(결과)    | 실제연간
2019  | 8.17조          | 20.06조         | -11.9조 ❌  | 26.99조
2020  | 10.81조         | 23.93조         | -13.1조 ❌  | 31.90조
2021  | 18.95조         | 11.81조         | +7.1조      | 42.72조
2025  | 38.46조(dart)   | 64.32조         | -25.9조 ❌  | 97.15조(fnguide)
```
→ DART 연간 revenue가 실제의 30~50% 수준으로 잘못 저장됨

#### 문제 B: 삼성전자 2017Q3 revenue 오류
```sql
SELECT year, quarter, revenue/1e12 as rev_조, operating_profit/1e12 as op_조
FROM financial_data
WHERE stock_code='005930' AND is_annual=0 AND year=2017 AND quarter=3;
-- 결과: REV=1.05조, OP=0.14조 ← 실제는 REV=62.05조, OP=14.53조
```
→ `data_source=NULL` (구형 수집) 데이터. 16Q4 데이터도 누락.

#### 문제 C: SK하이닉스 연간 DART revenue 심각 오류
```sql
SELECT year, revenue/1e12 as rev_조, data_source
FROM financial_data
WHERE stock_code='000660' AND is_annual=1 AND report_type='CFS'
AND data_source NOT LIKE 'fnguide%' AND data_source NOT LIKE 'dart_ofs%';
-- 2019: 8.17조(저장) vs 실제 26.99조 → 0.30x
-- 2020: 10.81조(저장) vs 실제 31.90조 → 0.34x
-- 2024: 34.36조(저장) vs fnguide 66.19조 → 0.52x
-- 2025: 38.46조(저장) vs fnguide 97.15조 → 0.40x
```

---

## 3. 현재 데이터 구조 설명

### 3-1. DB 연결
```python
import sqlite3
conn = sqlite3.connect('/Applications/stock_dashboard/stock.db')
conn.row_factory = sqlite3.Row
```

### 3-2. financial_data 테이블 핵심 컬럼
```sql
CREATE TABLE financial_data (
    id INTEGER PRIMARY KEY,
    stock_code TEXT,       -- 종목코드 (6자리, zfill)
    year INTEGER,          -- 사업연도
    quarter INTEGER,       -- 분기 (1~4, 연간=0)
    is_annual INTEGER,     -- 1=연간, 0=분기
    report_type TEXT,      -- 'CFS'=연결, 'OFS'=별도
    revenue REAL,          -- 매출액 (원 단위)
    operating_profit REAL, -- 영업이익 (원 단위)
    net_income REAL,       -- 당기순이익 (원 단위)
    total_assets REAL,     -- 자산총계 (원 단위)
    total_equity REAL,     -- 자본총계 (원 단위)
    eps REAL,              -- 주당순이익
    bps REAL,              -- 주당순자산
    data_source TEXT,      -- 출처 (아래 설명)
    created_at TEXT
);
```

**⚠️ 단위**: 모두 원(KRW) 단위. 억원으로 표시하려면 ÷1e8.

### 3-3. data_source 의미
| data_source | 의미 |
|-------------|------|
| `NULL` | 구형 수집 (출처 불명, DART 기반 추정) — 검증 안 됨 |
| `dart` | DART API 직접 파싱 — 일부 오류 있음 |
| `dart_redownload` | 버그수정 후 재다운로드 — 2022 등 일부 |
| `fnguide` | FnGuide 수집 — 주로 OCF/ICF/FCF만 있음 |
| `fnguide_revenue_fix` | 2022 DART CFS 오류 → FnGuide 교체 |
| `dart_ofs_backfill` | OFS(별도) DART 재수집 |

### 3-4. Q4 계산 방식 (현재 수집 로직)
Q4 분기는 분기별 보고서(11013)에서 직접 오지 않고, 연간(11011)에서 역산:
```python
# 실제 계산 위치: 수집기 (dart_collector.py 또는 별도 저장 루틴)
q4_revenue = annual_revenue - q1_revenue - q2_revenue - q3_revenue
```
→ **연간 값이 틀리면 Q4도 반드시 틀림**

---

## 4. DART API 수집 방법 (재수집에 필요)

### 4-1. 연간 재무제표 수집
```python
import OpenDartReader
dart = OpenDartReader('70dccf...')  # DART_API_KEY

# 연결재무제표(CFS) 연간 사업보고서
df = dart.finstate_all(
    corp_code,       # DART 기업코드 (8자리)
    bsns_year=2024,
    reprt_code='11011',   # 사업보고서
    fs_div='CFS'          # 연결재무제표
)
```

### 4-2. 종목코드 → DART 기업코드 변환
```python
# dart.corp_codes: 전체 기업코드 DataFrame
corp_df = dart.corp_codes
corp_code = corp_df[corp_df['stock_code'] == '000660']['corp_code'].values[0]
# SK하이닉스 corp_code = '00164779'
```

### 4-3. dart_mapping_engine.py — account_id → DB 필드 매핑
```python
# /Applications/stock_dashboard/collectors/dart_mapping_engine.py
from dart_mapping_engine import resolve_field

field = resolve_field(stock_code, account_id, account_nm)
# 예: resolve_field('000660', 'ifrs-full_Revenue', '매출액') → 'revenue'
# 예: resolve_field('000660', 'dart_OperatingIncomeLoss', '영업이익') → 'operating_profit'
```

**핵심 매핑 (DEFAULT_ACCOUNT_ID_MAP)**:
| account_id | DB 필드 |
|-----------|---------|
| `ifrs-full_Revenue` | `revenue` |
| `dart_OperatingIncomeLoss` | `operating_profit` ← 최우선 |
| `ifrs-full_ProfitLossFromOperatingActivities` | `operating_profit` |
| `ifrs-full_GrossProfit` | `operating_profit` ← fallback (매출총이익=영업이익인 특수업종만) |
| `ifrs-full_ProfitLoss` | `net_income` |
| `dart_ProfitLoss` | `net_income` |
| `ifrs-full_Assets` | `total_assets` |
| `ifrs-full_Equity` | `total_equity` |

### 4-4. _parse_fin_df() — DART DataFrame → dict 변환
```python
# /Applications/stock_dashboard/collectors/dart_collector.py
# _parse_fin_df(df, stock_code) → dict with keys: revenue, operating_profit, net_income, total_assets, total_equity

# 핵심 로직:
# 1. account_id 기반 매핑 (resolve_field) 우선
# 2. 키워드 스캔 fallback (단 기존값 있으면 skip)
# 3. revenue 키워드 스캔 시 "원가","총이익","차감","비용","손실" 포함 계정 제외

# DART DataFrame 컬럼: account_id, account_nm, thstrm_amount (당기), frmtrm_amount (전기)
```

### 4-5. FnGuide revenue 조회
```python
# 비교용 FnGuide revenue는 financial_data에서
fg_row = conn.execute("""
    SELECT revenue, operating_profit, net_income
    FROM financial_data
    WHERE stock_code=? AND year=? AND is_annual=1 AND report_type='CFS'
    AND data_source LIKE 'fnguide%'
    ORDER BY rowid DESC LIMIT 1
""", (stock_code, year)).fetchone()
```

---

## 5. Codex 실행 태스크 목록

### Task 1: 연간 DART revenue 오류 탐지 (전 종목)
**목표**: 연간 DART revenue가 FnGuide의 50% 이하인 종목 전체 탐지

```sql
-- 탐지 쿼리
SELECT 
    d.stock_code, su.stock_name, d.year,
    d.revenue/1e12 as dart_조, f.revenue/1e12 as fg_조,
    d.revenue/f.revenue as ratio,
    d.data_source
FROM financial_data d
JOIN financial_data f ON d.stock_code=f.stock_code AND d.year=f.year
    AND d.is_annual=1 AND d.report_type='CFS'
    AND f.data_source LIKE 'fnguide%' AND f.is_annual=1 AND f.report_type='CFS'
LEFT JOIN stock_universe su ON d.stock_code=su.stock_code
WHERE d.data_source NOT LIKE 'fnguide%' 
  AND d.data_source NOT LIKE 'dart_ofs%'
  AND d.is_annual=1 AND d.report_type='CFS'
  AND f.revenue > 0 AND d.revenue > 0
  AND d.revenue/f.revenue < 0.60  -- 60% 미만
ORDER BY ratio ASC;
```

**처리**: 
- ratio < 0.60이고 FnGuide 값이 있으면 → `financial_data` 해당 row의 `revenue = fnguide.revenue`, `data_source = 'fnguide_revenue_fix'`
- cf_validation_flags에 해당 pair 있으면 → `status='CONFIRMED'`, `ai_verdict='DART_CFS_PARTIAL_USE_FNGUIDE'`

---

### Task 2: Q4 revenue 음수 재계산
**목표**: 연간 revenue 수정 후 Q4를 올바르게 재계산

```sql
-- 음수 Q4 탐지
SELECT stock_code, year, revenue, operating_profit
FROM financial_data
WHERE is_annual=0 AND quarter=4 AND (revenue < 0 OR operating_profit < 0)
ORDER BY stock_code, year;
-- 665건 예상
```

**재계산 로직**:
```python
for each (stock_code, year) with negative Q4:
    annual = conn.execute("""
        SELECT revenue, operating_profit, net_income FROM financial_data
        WHERE stock_code=? AND year=? AND is_annual=1 AND report_type='CFS'
        AND data_source NOT LIKE 'fnguide%' AND data_source NOT LIKE 'dart_ofs%'
        ORDER BY CASE 
            WHEN data_source='fnguide_revenue_fix' THEN 1
            WHEN data_source='dart_redownload' THEN 2
            WHEN data_source='dart' THEN 3
            ELSE 4 END
        LIMIT 1
    """, (stock_code, year)).fetchone()
    
    q1_q2_q3 = conn.execute("""
        SELECT SUM(revenue), SUM(operating_profit), SUM(net_income) FROM financial_data
        WHERE stock_code=? AND year=? AND is_annual=0 AND quarter IN (1,2,3)
        AND report_type='CFS'
    """, (stock_code, year)).fetchone()
    
    if annual and q1_q2_q3:
        q4_rev = annual_rev - sum_q1q2q3_rev
        # q4_rev >= 0 인 경우만 업데이트
        # q4_rev < 0이면 연간값이 아직 틀린 것 → 연간 먼저 수정 필요 (Task 1)
        
        conn.execute("""
            UPDATE financial_data SET revenue=?, operating_profit=?, net_income=?
            WHERE stock_code=? AND year=? AND is_annual=0 AND quarter=4 AND report_type='CFS'
        """, (q4_rev, q4_op, q4_ni, stock_code, year))
```

---

### Task 3: 삼성전자(005930) 2016~2017 분기 재수집
**목표**: 2017Q3 REV=1.05조(실제 62조) 등 구형 오류 수정

현재 상태:
```sql
SELECT year, quarter, revenue/1e12, operating_profit/1e12, data_source
FROM financial_data
WHERE stock_code='005930' AND is_annual=0 AND year BETWEEN 2016 AND 2017;
-- 2017Q3: REV=1.05조(오류), OP=0.14조(오류) data_source=NULL
-- 2016Q4: 데이터 없음 (누락)
```

**방법**: DART 분기 보고서(reprt_code='11014' = 3Q 보고서)에서 직접 수집
```python
import OpenDartReader
dart = OpenDartReader('70dccf...')

# 삼성전자 corp_code = '00126380'
# 2017년 3분기 보고서 (11014=반기, 11012=1분기, 11013=분기보고서, 11011=사업보고서)
df_q3 = dart.finstate_all('00126380', bsns_year=2017, reprt_code='11014', fs_div='CFS')
# thstrm_amount = 당기 (2017 3분기 누적), frmtrm_amount = 전기 (2016 3분기 누적)
# Q3 단독 = 3분기누적 - 2분기누적
```

**분기 단독값 계산 방법**:
- Q1 = 1분기보고서(11012) thstrm_amount
- Q2 = 반기보고서(11013) thstrm_amount - Q1
- Q3 = 분기보고서(11014) thstrm_amount - Q1 - Q2
- Q4 = 사업보고서(11011) thstrm_amount - Q1 - Q2 - Q3

---

### Task 4: Q1~Q3 음수 474건 원인 분석 및 수정
**목표**: Q4 외 분기에서 음수인 474건 처리

```sql
-- Q1~Q3 음수 탐지
SELECT fd.stock_code, su.stock_name, fd.year, fd.quarter, 
       fd.revenue/1e12 as rev_조, fd.operating_profit/1e12 as op_조,
       fd.data_source
FROM financial_data fd
LEFT JOIN stock_universe su ON fd.stock_code=su.stock_code
WHERE fd.is_annual=0 AND fd.quarter IN (1,2,3) AND fd.revenue < 0
ORDER BY fd.revenue ASC;
```

**예상 원인별 처리**:
1. **단위오류** (값이 -수억 수준): 원 단위인데 억원으로 수집된 경우 → ×1e8 또는 DART 재수집
2. **진짜 음수** (소기업 특수항목, 손상차손 등): 정상값 → 유지
3. **DART 파싱오류** (이상하게 큰 음수): DART 재수집 or FnGuide 교체

**분류 기준**:
```python
if abs(revenue) < 1e9:   # 10억 미만 절대값
    # 소기업 정상 가능성 높음
    pass
elif revenue < -1e13:    # -10조 이상 음수
    # 명백한 오류 → DART 재수집 필요
    redownload = True
```

---

### Task 5: 전체 분기 데이터 일관성 검증
**목표**: Q1+Q2+Q3+Q4 ≈ 연간 (±10% 허용)

```sql
-- 연간 vs 분기합계 불일치 탐지
SELECT 
    ann.stock_code, ann.year,
    ann.revenue/1e12 as ann_rev,
    (q.q1+q.q2+q.q3+q.q4)/1e12 as sum_rev,
    ABS(ann.revenue - (q.q1+q.q2+q.q3+q.q4)) / ann.revenue as diff_pct
FROM financial_data ann
JOIN (
    SELECT stock_code, year,
        SUM(CASE WHEN quarter=1 THEN revenue ELSE 0 END) as q1,
        SUM(CASE WHEN quarter=2 THEN revenue ELSE 0 END) as q2,
        SUM(CASE WHEN quarter=3 THEN revenue ELSE 0 END) as q3,
        SUM(CASE WHEN quarter=4 THEN revenue ELSE 0 END) as q4
    FROM financial_data
    WHERE is_annual=0 AND report_type='CFS' AND revenue IS NOT NULL
    GROUP BY stock_code, year
    HAVING COUNT(DISTINCT quarter) = 4
) q ON ann.stock_code=q.stock_code AND ann.year=q.year
WHERE ann.is_annual=1 AND ann.report_type='CFS'
  AND ann.revenue IS NOT NULL AND ann.revenue > 0
  AND ABS(ann.revenue - (q.q1+q.q2+q.q3+q.q4)) / ann.revenue > 0.10  -- 10% 초과 불일치
ORDER BY diff_pct DESC
LIMIT 100;
```

---

## 6. 검증 완료 후 cf_validation_flags 업데이트 방법

분기 데이터는 `cf_validation_flags`에 별도 flag_type이 없음. 수정 후 아래처럼 기록:

```python
# 분기 수정 기록 방법 (cf_validation_flags에 새 row 추가)
conn.execute("""
    INSERT OR REPLACE INTO cf_validation_flags
    (stock_code, year, field, flag_type, dart_value, fnguide_value, seibro_value,
     ai_verdict, ai_reasoning, resolved_value, status)
    VALUES (?, ?, ?, 'QUARTERLY_FIX', ?, ?, NULL, ?, ?, ?, 'CONFIRMED')
""", (stock_code, year, 'revenue', old_dart_val, fg_val, 
      'QUARTERLY_Q4_RECALC', reason, new_val))
```

---

## 7. 우선순위

| 우선도 | 태스크 | 종목수 | 영향 |
|--------|--------|--------|------|
| 🔴 P0 | Task 1: 연간 DART revenue 0.6x 이하 수정 | ~50종목 | Q4 재계산의 선결 조건 |
| 🔴 P0 | Task 2: Q4 음수 재계산 | 665건 | 차트 마이너스 즉시 해소 |
| 🟡 P1 | Task 3: 삼성전자 2017Q3 재수집 | 1종목 | 17.3Q 차트 표시 |
| 🟡 P1 | Task 4: Q1~Q3 음수 474건 분석 | ~200종목 | 데이터 신뢰성 |
| 🟢 P2 | Task 5: 전체 일관성 검증 | 전 종목 | QC |

---

## 8. 주요 종목 우선 처리 목록

```
SK하이닉스 (000660): 연간 2019/2020/2025 DART revenue 크게 틀림 → Task 1+2 우선
삼성전자 (005930): 2017Q3 오류, 2016Q4 누락 → Task 3
삼성바이오로직스, 카카오, 네이버 등 대형주도 유사 점검 권장
```

---

## 9. 파일 위치

| 파일 | 경로 | 용도 |
|------|------|------|
| DART 수집기 | `/Applications/stock_dashboard/collectors/dart_collector.py` | _parse_fin_df() 함수 |
| 매핑 엔진 | `/Applications/stock_dashboard/collectors/dart_mapping_engine.py` | account_id → field 매핑 |
| DB | `/Applications/stock_dashboard/stock.db` | financial_data, cf_validation_flags |
| FIN 재검증 스크립트 (참고) | `/Applications/stock_dashboard/scratch/revalidate_fin_only.py` | 연간 재수집 방법 참고 |
| CF 최종 해소 스크립트 (참고) | `/Applications/stock_dashboard/scratch/resolve_final_ambiguous.py` | 구조적 해소 패턴 참고 |

---

## 10. 주의사항

1. **연간 수정 전에 Q4 수정 절대 금지** — Task 1 완료 후 Task 2 실행
2. **data_source 반드시 업데이트** — 수정된 행은 `data_source='fnguide_revenue_fix'` 또는 `'dart_quarterly_fix'` 등으로 명시
3. **Q4가 음수 나오면 연간값 먼저 수정** — 연간이 틀린 것이 근본 원인
4. **CFS/OFS 구분 필수** — `report_type='CFS'` 행과 `'OFS'` 행을 혼용하지 말 것
5. **백업 필수** — `CREATE TABLE financial_data_backup_20260521 AS SELECT * FROM financial_data;`
