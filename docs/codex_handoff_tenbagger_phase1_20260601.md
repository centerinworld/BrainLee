# Codex 지시서 — 텐버거 발굴 프로젝트 Phase 1
> 작성일: 2026-06-01 | 작성자: Claude
> 프로젝트: /Applications/stock_dashboard

---

## 배경 (반드시 읽을 것)

BigQuery 실증 분석 결과 **1,324종목의 3배+ 상승 패턴** 분석 완료:
- 94.5%가 52주 저점 근처에서 출발
- 89.7%는 기관 순매도 상태에서 출발
- **수주공시가 나오면 이미 늦음** → 공시 전 수주잔고 증가 감지가 핵심

이 지시서는 "공시 전 선행 지표"를 수집하는 3개 수집기를 구현한다.

---

## 공통 규칙 (모든 작업에 적용)

```python
# DB 경로
DB_PATH = "/Applications/stock_dashboard/stock.db"

# DART API 키 (3개, 일일한도 초과 시 순환)
DART_API_KEYS = [
    "70dccf62b9f0eb2ca771ed1758e431bade817ec5",   # DART_API_KEY
    "8936a307b1d1ffe659946cf13f9160f4b6e105d6",   # DART_API_KEY2
    "16a08bd0af1b86532d87d94ba6c6ed33332b106c",   # DART_API_KEY3
]

# DB 연결 패턴 (항상 이 방식 사용)
import sqlite3
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 수집 대상 종목 (stock_universe에서 가져옴)
stocks = conn.execute("""
    SELECT stock_code, stock_name
    FROM stock_universe
    WHERE market IN ('KOSPI','KOSDAQ')
      AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    ORDER BY market_cap DESC NULLS LAST
""").fetchall()

# DART finprdCd (보고서 코드)
# 11011 = 사업보고서(연간)
# 11012 = 반기보고서
# 11013 = 1분기
# 11014 = 3분기

# Rate limit: DART API 호출 간 0.5초 sleep 필수
import time
time.sleep(0.5)
```

---

## Task 1: 수주잔고 수집기 (P1 — 최우선)

### 목적
DART 사업/반기보고서의 "수주잔고" 항목을 파싱하여 저장.
주문이 공식 공시되기 전, 누적 수주잔고 증가 추세로 수주 모멘텀을 선행 감지.

### 저장할 DB 테이블 (신규 생성)

```sql
CREATE TABLE IF NOT EXISTS order_backlog (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT    NOT NULL,
    stock_name      TEXT,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,   -- 1=1Q, 2=반기, 3=3Q, 4=연간
    report_type     TEXT,               -- '사업보고서','반기보고서' 등
    rcept_no        TEXT,               -- DART 접수번호
    backlog_amount  REAL,               -- 수주잔고 금액 (원 단위)
    backlog_unit    TEXT DEFAULT '원',  -- 단위 (원/천원/백만원)
    backlog_normalized REAL,            -- 백만원 단위로 정규화
    new_orders      REAL,               -- 신규수주 (있으면)
    revenue_base    REAL,               -- 같은 기간 매출 (비율 계산용)
    backlog_to_rev  REAL,               -- backlog_normalized / revenue_base
    data_source     TEXT DEFAULT 'dart_backlog',
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, year, quarter)
);

CREATE INDEX IF NOT EXISTS idx_order_backlog_code ON order_backlog(stock_code);
CREATE INDEX IF NOT EXISTS idx_order_backlog_year ON order_backlog(year, quarter);
```

### 구현 파일: `collectors/dart_backlog_collector.py`

```python
"""
collectors/dart_backlog_collector.py
DART 사업/반기보고서에서 수주잔고 파싱

DART financeAll API → XML 파싱 → 수주잔고 계정명 매칭

수주잔고 계정명 패턴 (다양한 회사에서 사용):
- "수주잔고", "기말수주잔고", "잔여수주잔고"
- "수주잔액", "수주액", "수주금액"
- "용역수주잔고", "공사수주잔고"
- "order backlog" (영문)
"""

import sqlite3, requests, time, logging, re
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
API_KEYS = [
    "70dccf62b9f0eb2ca771ed1758e431bade817ec5",
    "8936a307b1d1ffe659946cf13f9160f4b6e105d6",
    "16a08bd0af1b86532d87d94ba6c6ed33332b106c",
]
_key_idx = 0

# 수주잔고 계정명 키워드 (우선순위 순)
BACKLOG_KEYWORDS = [
    "수주잔고", "기말수주잔고", "잔여수주잔고",
    "수주잔액", "수주금액", "공사수주잔고",
    "용역수주잔고", "수주총액", "미이행수주",
]
NEW_ORDER_KEYWORDS = ["신규수주", "당기수주", "수주액", "수주계약"]


def _next_key():
    global _key_idx
    k = API_KEYS[_key_idx % len(API_KEYS)]
    _key_idx += 1
    return k


def _get_corp_code(stock_code: str, conn) -> str | None:
    """stock_code → DART corp_code 변환 (corp_code는 별도 API로 조회)"""
    # 1. dart_disclosures 테이블에서 찾기 (이미 있는 경우)
    row = conn.execute(
        "SELECT corp_code FROM dart_disclosures WHERE stock_code=? LIMIT 1",
        (stock_code,)
    ).fetchone()
    if row and row[0]:
        return row[0]
    
    # 2. DART corp_code 검색 API
    try:
        resp = requests.get(
            f"{DART_BASE}/company.json",
            params={"crtfc_key": _next_key(), "stock_code": stock_code},
            timeout=10
        )
        data = resp.json()
        if data.get("status") == "000":
            return data.get("corp_code")
    except Exception as e:
        logger.warning(f"corp_code 조회 실패 {stock_code}: {e}")
    return None


def _parse_amount(value_str: str) -> float | None:
    """금액 문자열 → float (쉼표, 공백 제거)"""
    if not value_str:
        return None
    cleaned = re.sub(r'[,\s원달러억만천]', '', str(value_str))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_to_million(amount: float, unit_str: str) -> float:
    """단위 문자열 기반 백만원 정규화"""
    unit = (unit_str or "원").strip()
    if "백만" in unit or "million" in unit.lower():
        return amount
    elif "억" in unit:
        return amount * 100
    elif "천원" in unit or "천" in unit:
        return amount / 1000
    elif "원" in unit:
        return amount / 1_000_000
    return amount  # 기본값: 이미 백만원


def fetch_backlog_for_stock(corp_code: str, stock_code: str, year: int, quarter: int) -> dict | None:
    """DART financeAll → 수주잔고 추출"""
    rprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    rprt_code = rprt_map.get(quarter, "11011")
    
    # DART 재무제표 전체 조회 (fs_div=CFS 연결, 없으면 OFS 별도)
    for fs_div in ["CFS", "OFS"]:
        try:
            resp = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": _next_key(),
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": rprt_code,
                    "fs_div": fs_div,
                },
                timeout=15
            )
            time.sleep(0.5)
            data = resp.json()
            
            if data.get("status") not in ("000", "013"):
                continue
            
            items = data.get("list", [])
            backlog_amt = None
            new_order_amt = None
            unit_str = "원"
            
            for item in items:
                acnt_nm = item.get("account_nm", "")
                # 단위 추출
                if item.get("currency") and not unit_str:
                    unit_str = item.get("currency", "원")
                
                # 수주잔고 매칭
                for kw in BACKLOG_KEYWORDS:
                    if kw in acnt_nm and "신규" not in acnt_nm and "당기" not in acnt_nm:
                        amt = _parse_amount(item.get("thstrm_amount"))
                        if amt is not None:
                            backlog_amt = amt
                            break
                
                # 신규수주 매칭
                for kw in NEW_ORDER_KEYWORDS:
                    if kw in acnt_nm:
                        amt = _parse_amount(item.get("thstrm_amount"))
                        if amt is not None:
                            new_order_amt = amt
                            break
            
            if backlog_amt is not None:
                return {
                    "backlog_amount": backlog_amt,
                    "new_orders": new_order_amt,
                    "backlog_unit": unit_str,
                    "backlog_normalized": _normalize_to_million(backlog_amt, unit_str),
                }
        except Exception as e:
            logger.warning(f"DART fnlttSinglAcntAll 오류 {stock_code} {year}Q{quarter}: {e}")
    
    return None


def collect_backlog(years: list = None, quarters: list = None,
                    limit: int = None, resume: bool = True):
    """
    전종목 수주잔고 수집 메인 함수
    
    Args:
        years: 수집할 연도 리스트. 기본값: [2021, 2022, 2023, 2024, 2025]
        quarters: 수집할 분기. 기본값: [2, 4] (반기 + 연간)
        limit: 종목수 제한 (테스트용)
        resume: True면 이미 수집된 항목 스킵
    """
    if years is None:
        years = [2021, 2022, 2023, 2024, 2025]
    if quarters is None:
        quarters = [2, 4]  # 반기(2) + 연간(4) 위주
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 테이블 생성
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS order_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL, stock_name TEXT,
            year INTEGER NOT NULL, quarter INTEGER NOT NULL,
            report_type TEXT, rcept_no TEXT,
            backlog_amount REAL, backlog_unit TEXT DEFAULT '원',
            backlog_normalized REAL, new_orders REAL,
            revenue_base REAL, backlog_to_rev REAL,
            data_source TEXT DEFAULT 'dart_backlog',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, year, quarter)
        );
        CREATE INDEX IF NOT EXISTS idx_ob_code ON order_backlog(stock_code);
    """)
    conn.commit()
    
    # 수집 대상 종목
    stocks = conn.execute("""
        SELECT stock_code, stock_name FROM stock_universe
        WHERE market IN ('KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()
    
    if limit:
        stocks = stocks[:limit]
    
    total = len(stocks) * len(years) * len(quarters)
    done = 0
    
    for stock in stocks:
        code = stock["stock_code"]
        name = stock["stock_name"]
        
        corp_code = _get_corp_code(code, conn)
        if not corp_code:
            logger.warning(f"[{code}] corp_code 없음 — 스킵")
            continue
        
        for year in years:
            for quarter in quarters:
                done += 1
                
                # 이미 수집됐으면 스킵
                if resume:
                    exists = conn.execute(
                        "SELECT 1 FROM order_backlog WHERE stock_code=? AND year=? AND quarter=?",
                        (code, year, quarter)
                    ).fetchone()
                    if exists:
                        continue
                
                result = fetch_backlog_for_stock(corp_code, code, year, quarter)
                
                if result and result["backlog_normalized"]:
                    # 같은 기간 매출 조회
                    rev_row = conn.execute(
                        "SELECT revenue FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? LIMIT 1",
                        (code, year, quarter if quarter < 4 else 0, 1 if quarter == 4 else 0)
                    ).fetchone()
                    rev = rev_row[0] if rev_row and rev_row[0] else None
                    
                    b2r = round(result["backlog_normalized"] / (rev / 1_000_000), 2) if rev and rev > 0 else None
                    
                    conn.execute("""
                        INSERT OR REPLACE INTO order_backlog
                        (stock_code, stock_name, year, quarter,
                         backlog_amount, backlog_unit, backlog_normalized,
                         new_orders, revenue_base, backlog_to_rev)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (code, name, year, quarter,
                          result["backlog_amount"], result["backlog_unit"],
                          result["backlog_normalized"], result.get("new_orders"),
                          rev, b2r))
                    conn.commit()
                    logger.info(f"[{code}] {name} {year}Q{quarter} 수주잔고: {result['backlog_normalized']:,.0f}백만원 (b/r={b2r})")
                
                if done % 100 == 0:
                    logger.info(f"진행: {done}/{total}")
    
    conn.close()
    logger.info("=== 수주잔고 수집 완료 ===")


if __name__ == "__main__":
    import argparse, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="테스트용 종목수 제한")
    parser.add_argument("--years", nargs="+", type=int, default=[2021,2022,2023,2024,2025])
    parser.add_argument("--quarters", nargs="+", type=int, default=[2, 4])
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    
    collect_backlog(
        years=args.years,
        quarters=args.quarters,
        limit=args.limit,
        resume=not args.no_resume,
    )
```

### 실행 방법
```bash
cd /Applications/stock_dashboard
source venv/bin/activate

# 테스트 (상위 50종목, 2024-2025만)
python3 collectors/dart_backlog_collector.py --limit 50 --years 2024 2025

# 전체 수집 (백그라운드)
nohup python3 collectors/dart_backlog_collector.py > /tmp/backlog_collect.log 2>&1 &
echo "PID: $!"
```

---

## Task 2: 매입재료비 비율 수집기 (P1)

### 목적
매입재료비 증가율 변화로 원가 압력 감소 또는 매출 증가 선행 신호 감지.
제조업 텐버거의 핵심 선행 지표.

### 저장 테이블 (신규)

```sql
CREATE TABLE IF NOT EXISTS cost_structure (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT    NOT NULL,
    stock_name      TEXT,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    raw_material_cost    REAL,    -- 원재료비 (원 단위)
    labor_cost           REAL,    -- 노무비
    overhead_cost        REAL,    -- 제조간접비
    total_cogs           REAL,    -- 매출원가 합계
    revenue              REAL,    -- 매출 (financial_data에서)
    raw_material_ratio   REAL,    -- 원재료비/매출 (%)
    cogs_ratio           REAL,    -- 매출원가율 (%)
    yoy_raw_material_chg REAL,    -- 원재료비 YoY 변화율 (%)
    data_source     TEXT DEFAULT 'dart_cost',
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, year, quarter)
);
```

### 구현 파일: `collectors/dart_cost_collector.py`

```python
"""
collectors/dart_cost_collector.py
DART 원가명세서에서 원재료비/노무비/제조간접비 수집

원가명세서 계정명 키워드:
- "원재료비", "재료비", "주요재료비", "원자재비"
- "노무비", "급여", "인건비"
- "제조경비", "제조간접비", "경비"
- "매출원가"

주의: 원가명세서는 제조업에만 있음. 서비스/금융업 제외.
"""

import sqlite3, requests, time, logging, re

logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
API_KEYS = [
    "70dccf62b9f0eb2ca771ed1758e431bade817ec5",
    "8936a307b1d1ffe659946cf13f9160f4b6e105d6",
    "16a08bd0af1b86532d87d94ba6c6ed33332b106c",
]
_key_idx = 0

MATERIAL_KW  = ["원재료비", "주요재료비", "재료비", "원자재비", "원재료"]
LABOR_KW     = ["노무비", "인건비", "급여와임금"]
OVERHEAD_KW  = ["제조경비", "제조간접비", "경비합계"]
COGS_KW      = ["매출원가", "제조원가", "용역원가", "공사원가"]

# 원가명세서가 있을 가능성 높은 섹터 (금융/부동산 제외)
MANUFACTURING_SECTORS = [
    "IT", "산업재", "소재", "에너지", "경기소비재", "필수소비재", "의료"
]


def _next_key():
    global _key_idx
    k = API_KEYS[_key_idx % len(API_KEYS)]
    _key_idx += 1
    return k


def _parse_amount(v) -> float | None:
    if v is None:
        return None
    cleaned = re.sub(r'[,\s]', '', str(v))
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_cost_structure(corp_code: str, stock_code: str, year: int, quarter: int) -> dict | None:
    """DART financeAll → 원가명세서 추출"""
    rprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    
    for fs_div in ["CFS", "OFS"]:
        try:
            resp = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": _next_key(),
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": rprt_map[quarter],
                    "fs_div": fs_div,
                },
                timeout=15
            )
            time.sleep(0.5)
            data = resp.json()
            if data.get("status") not in ("000", "013"):
                continue
            
            items = data.get("list", [])
            result = {"material": None, "labor": None, "overhead": None, "cogs": None}
            
            for item in items:
                nm = item.get("account_nm", "")
                amt = _parse_amount(item.get("thstrm_amount"))
                if amt is None:
                    continue
                
                # 우선순위 매칭
                if result["material"] is None:
                    for kw in MATERIAL_KW:
                        if kw in nm and "감소" not in nm and "증가" not in nm:
                            result["material"] = amt
                            break
                
                if result["labor"] is None:
                    for kw in LABOR_KW:
                        if kw in nm:
                            result["labor"] = amt
                            break
                
                if result["cogs"] is None:
                    for kw in COGS_KW:
                        if kw in nm and "증감" not in nm:
                            result["cogs"] = amt
                            break
            
            # 최소 COGS나 재료비 중 하나라도 있으면 반환
            if any(v is not None for v in result.values()):
                return result
        
        except Exception as e:
            logger.warning(f"원가명세서 오류 {stock_code} {year}Q{quarter}: {e}")
    
    return None


def collect_cost_structure(years=None, quarters=None, limit=None, resume=True):
    if years is None:
        years = [2021, 2022, 2023, 2024, 2025]
    if quarters is None:
        quarters = [2, 4]  # 반기, 연간
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 테이블 생성
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cost_structure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL, stock_name TEXT,
            year INTEGER NOT NULL, quarter INTEGER NOT NULL,
            raw_material_cost REAL, labor_cost REAL,
            overhead_cost REAL, total_cogs REAL, revenue REAL,
            raw_material_ratio REAL, cogs_ratio REAL,
            yoy_raw_material_chg REAL,
            data_source TEXT DEFAULT 'dart_cost',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, year, quarter)
        );
        CREATE INDEX IF NOT EXISTS idx_cs_code ON cost_structure(stock_code);
    """)
    conn.commit()
    
    # 제조업 종목만 대상 (효율을 위해)
    stocks = conn.execute("""
        SELECT su.stock_code, su.stock_name
        FROM stock_universe su
        WHERE su.market IN ('KOSPI','KOSDAQ')
          AND su.sector_large NOT IN ('금융','부동산')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY su.market_cap DESC NULLS LAST
    """).fetchall()
    
    if limit:
        stocks = stocks[:limit]
    
    logger.info(f"수집 대상: {len(stocks)}종목 × {len(years)}년 × {len(quarters)}분기")
    
    from collectors.dart_backlog_collector import _get_corp_code  # corp_code 재사용
    
    done = 0
    for stock in stocks:
        code = stock["stock_code"]
        name = stock["stock_name"]
        corp_code = _get_corp_code(code, conn)
        if not corp_code:
            continue
        
        for year in years:
            for quarter in quarters:
                done += 1
                
                if resume:
                    exists = conn.execute(
                        "SELECT 1 FROM cost_structure WHERE stock_code=? AND year=? AND quarter=?",
                        (code, year, quarter)
                    ).fetchone()
                    if exists:
                        continue
                
                result = fetch_cost_structure(corp_code, code, year, quarter)
                if not result:
                    continue
                
                # 매출은 financial_data에서
                rev_row = conn.execute(
                    "SELECT revenue FROM financial_data WHERE stock_code=? AND year=? AND is_annual=? LIMIT 1",
                    (code, year, 1 if quarter == 4 else 0)
                ).fetchone()
                rev = rev_row[0] if rev_row and rev_row[0] else None
                
                mat = result.get("material")
                cogs = result.get("cogs")
                mat_ratio = round(mat / rev * 100, 2) if mat and rev and rev > 0 else None
                cogs_ratio = round(cogs / rev * 100, 2) if cogs and rev and rev > 0 else None
                
                # YoY 변화율
                prev_row = conn.execute(
                    "SELECT raw_material_cost FROM cost_structure WHERE stock_code=? AND year=? AND quarter=?",
                    (code, year - 1, quarter)
                ).fetchone()
                yoy_chg = None
                if prev_row and prev_row[0] and mat:
                    yoy_chg = round((mat - prev_row[0]) / abs(prev_row[0]) * 100, 1)
                
                conn.execute("""
                    INSERT OR REPLACE INTO cost_structure
                    (stock_code, stock_name, year, quarter,
                     raw_material_cost, labor_cost, overhead_cost, total_cogs,
                     revenue, raw_material_ratio, cogs_ratio, yoy_raw_material_chg)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (code, name, year, quarter,
                      mat, result.get("labor"), result.get("overhead"), cogs,
                      rev, mat_ratio, cogs_ratio, yoy_chg))
                conn.commit()
                
                if done % 50 == 0:
                    logger.info(f"진행: {done} | {code} {name} {year}Q{quarter} 원재료비율={mat_ratio}")
    
    conn.close()
    logger.info("=== 원가구조 수집 완료 ===")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--years", nargs="+", type=int, default=[2021,2022,2023,2024,2025])
    parser.add_argument("--quarters", nargs="+", type=int, default=[2, 4])
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    collect_cost_structure(args.years, args.quarters, args.limit, not args.no_resume)
```

---

## Task 3: CB/BW 발행 이력 파서 (P2 — 악재 필터)

### 목적
전환사채(CB), 신주인수권부사채(BW), 유상증자(RO) 이력을 구조화.
이 이벤트가 있으면 주식 희석 리스크 → 텐버거 스크리너에서 **감점 또는 필터** 처리.

### 저장 테이블 (신규)

```sql
CREATE TABLE IF NOT EXISTS dilution_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT    NOT NULL,
    stock_name      TEXT,
    event_type      TEXT    NOT NULL,  -- 'CB','BW','RO','DR' (무상증자=DR)
    disclosed_at    TEXT,              -- 공시일 (YYYY-MM-DD)
    rcept_no        TEXT,
    issue_amount    REAL,              -- 발행 금액 (원)
    conversion_price REAL,            -- 전환가액 (CB/BW)
    shares_to_issue REAL,             -- 발행 예정 주식수
    current_shares  REAL,             -- 현재 발행주식수 (희석률 계산용)
    dilution_pct    REAL,             -- 희석률 (%)
    report_nm       TEXT,
    data_source     TEXT DEFAULT 'dart_dilution',
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_de_code ON dilution_events(stock_code, disclosed_at);
```

### 구현 파일: `collectors/dart_dilution_collector.py`

```python
"""
collectors/dart_dilution_collector.py
DART 공시에서 CB/BW/유상증자 이력 파싱

dart_disclosures 테이블에서 키워드 필터링 후 상세 파싱
- report_nm LIKE '%전환사채%' → CB
- report_nm LIKE '%신주인수권%' → BW
- report_nm LIKE '%유상증자%' → RO
"""

import sqlite3, requests, time, logging, re
from datetime import datetime

logger = logging.getLogger(__name__)
DB_PATH = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
API_KEYS = [
    "70dccf62b9f0eb2ca771ed1758e431bade817ec5",
    "8936a307b1d1ffe659946cf13f9160f4b6e105d6",
    "16a08bd0af1b86532d87d94ba6c6ed33332b106c",
]
_key_idx = 0

EVENT_PATTERNS = {
    "CB":  ["전환사채", "CB발행", "전환사채권"],
    "BW":  ["신주인수권부사채", "BW발행"],
    "RO":  ["유상증자", "주주배정", "제3자배정"],
    "MBO": ["경영권", "대주주변경"],
}


def _next_key():
    global _key_idx
    k = API_KEYS[_key_idx % len(API_KEYS)]
    _key_idx += 1
    return k


def _detect_event_type(report_nm: str) -> str | None:
    for etype, keywords in EVENT_PATTERNS.items():
        for kw in keywords:
            if kw in report_nm:
                return etype
    return None


def _parse_amount(v) -> float | None:
    if not v:
        return None
    cleaned = re.sub(r'[,\s억만원원달러]', '', str(v))
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_dilution_detail(rcept_no: str) -> dict:
    """DART 공시 상세에서 발행금액/전환가액 추출"""
    try:
        resp = requests.get(
            f"{DART_BASE}/document.xml",
            params={"crtfc_key": _next_key(), "rcept_no": rcept_no},
            timeout=15
        )
        time.sleep(0.3)
        text = resp.text
        
        result = {}
        
        # 발행금액 패턴
        m = re.search(r'발행금액.*?([0-9,]+)', text)
        if m:
            result["issue_amount"] = _parse_amount(m.group(1))
        
        # 전환가액 패턴
        m = re.search(r'전환가액.*?([0-9,]+)\s*원', text)
        if m:
            result["conversion_price"] = _parse_amount(m.group(1))
        
        # 발행 주식수
        m = re.search(r'발행주식수.*?([0-9,]+)\s*주', text)
        if m:
            result["shares_to_issue"] = _parse_amount(m.group(1))
        
        return result
    except Exception as e:
        logger.warning(f"공시 상세 파싱 오류 {rcept_no}: {e}")
        return {}


def collect_dilution_events(years_back: int = 5, limit: int = None):
    """dart_disclosures에서 CB/BW/유상증자 이력 수집"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dilution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL, stock_name TEXT,
            event_type TEXT NOT NULL, disclosed_at TEXT,
            rcept_no TEXT UNIQUE, issue_amount REAL,
            conversion_price REAL, shares_to_issue REAL,
            current_shares REAL, dilution_pct REAL,
            report_nm TEXT, data_source TEXT DEFAULT 'dart_dilution',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_de_code ON dilution_events(stock_code);
        CREATE INDEX IF NOT EXISTS idx_de_date ON dilution_events(disclosed_at);
    """)
    conn.commit()
    
    # dart_disclosures에서 키워드 필터링
    keywords_sql = " OR ".join([
        "report_nm LIKE '%전환사채%'",
        "report_nm LIKE '%신주인수권%'",
        "report_nm LIKE '%유상증자%'",
    ])
    
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")
    
    rows = conn.execute(f"""
        SELECT d.rcept_no, d.stock_code, d.corp_name as stock_name,
               d.report_nm, d.rcept_dt
        FROM dart_disclosures d
        WHERE ({keywords_sql})
          AND d.rcept_dt >= ?
          AND d.stock_code IS NOT NULL
          AND d.stock_code != ''
        ORDER BY d.rcept_dt DESC
    """, (cutoff,)).fetchall()
    
    if limit:
        rows = rows[:limit]
    
    logger.info(f"파싱 대상 공시: {len(rows)}건")
    
    done = 0
    for row in rows:
        rcept_no = row["rcept_no"]
        
        # 이미 파싱됐으면 스킵
        exists = conn.execute("SELECT 1 FROM dilution_events WHERE rcept_no=?", (rcept_no,)).fetchone()
        if exists:
            continue
        
        event_type = _detect_event_type(row["report_nm"] or "")
        if not event_type:
            continue
        
        # 공시 상세 파싱
        detail = fetch_dilution_detail(rcept_no)
        
        # 현재 발행주식수 (희석률 계산)
        shares_row = conn.execute(
            "SELECT shares_issued FROM stock_universe WHERE stock_code=?",
            (row["stock_code"],)
        ).fetchone()
        current_shares = shares_row[0] if shares_row and shares_row[0] else None
        
        dil_pct = None
        if current_shares and detail.get("shares_to_issue"):
            dil_pct = round(detail["shares_to_issue"] / current_shares * 100, 2)
        
        conn.execute("""
            INSERT OR IGNORE INTO dilution_events
            (stock_code, stock_name, event_type, disclosed_at, rcept_no,
             issue_amount, conversion_price, shares_to_issue,
             current_shares, dilution_pct, report_nm)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row["stock_code"], row["stock_name"], event_type,
            row["rcept_dt"], rcept_no,
            detail.get("issue_amount"), detail.get("conversion_price"),
            detail.get("shares_to_issue"), current_shares, dil_pct,
            row["report_nm"]
        ))
        conn.commit()
        done += 1
        
        if done % 50 == 0:
            logger.info(f"진행: {done}/{len(rows)} | {row['stock_code']} {event_type}")
    
    conn.close()
    logger.info(f"=== CB/BW/유상증자 이력 수집 완료: {done}건 저장 ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    collect_dilution_events(years_back=5)
```

---

## Task 4: BigQuery 파이프라인 스케줄러 등록 (P2)

### 목적
`bigquery_triple_pipeline.py`와 `bigquery_triple_morning_alert.py`를
스케줄러에 등록하여 매일 자동 실행.

### 수정 파일: `scheduler.py`

아래 두 가지를 추가한다.

#### 4-1. 잡 이름 목록에 추가 (77번 줄 근처, `}` 앞)

```python
    "BQ3배파이프라인",    # 추가
    "BQ아침알림",         # 추가
```

#### 4-2. 루프 등록 (~194번 줄 근처)

```python
("BQ3배파이프라인",  self._loop_bq_triple_pipeline),  # ★ 매일 18:30 3배패턴 후보 계산
("BQ아침알림",       self._loop_bq_morning_alert),     # ★ 매일 07:30 텔레그램 아침 알림
```

#### 4-3. 함수 구현 추가 (scheduler.py 말미, `_job_wal_daily_check` 아래)

```python
# ── BigQuery 3배 패턴 파이프라인 (매일 18:30) ────────────────────────────
def _loop_bq_triple_pipeline(self) -> None:
    """매일 18:30 — BigQuery v_3x_candidate_screen → triple_pattern_daily 적재"""
    logger.info("[BQ3배파이프라인] 루프 시작")
    self._wait_secs(60)
    while not self._stop_event.is_set():
        self._stop_event.wait(timeout=_seconds_until(18, 30, skip_weekend=True))
        if self._stop_event.is_set():
            break
        _run_job_safe("BQ3배파이프라인", self._job_bq_triple_pipeline)

def _job_bq_triple_pipeline(self) -> None:
    import subprocess, sys
    try:
        result = subprocess.run(
            [sys.executable,
             "/Applications/stock_dashboard/scripts/bigquery_triple_pipeline.py"],
            capture_output=True, text=True, timeout=600,
            cwd="/Applications/stock_dashboard"
        )
        if result.returncode == 0:
            logger.info(f"[BQ3배파이프라인] ✅ {result.stdout.strip()}")
        else:
            logger.error(f"[BQ3배파이프라인] ❌ {result.stderr[-500:]}")
    except Exception as e:
        logger.error(f"[BQ3배파이프라인] 실행 오류: {e}")

# ── BigQuery 아침 알림 (매일 07:30) ─────────────────────────────────────
def _loop_bq_morning_alert(self) -> None:
    """매일 07:30 — BigQuery 3배 패턴 후보 텔레그램 발송"""
    logger.info("[BQ아침알림] 루프 시작")
    self._wait_secs(60)
    while not self._stop_event.is_set():
        self._stop_event.wait(timeout=_seconds_until(7, 30))
        if self._stop_event.is_set():
            break
        _run_job_safe("BQ아침알림", self._job_bq_morning_alert)

def _job_bq_morning_alert(self) -> None:
    import subprocess, sys
    try:
        result = subprocess.run(
            [sys.executable,
             "/Applications/stock_dashboard/scripts/bigquery_triple_morning_alert.py"],
            capture_output=True, text=True, timeout=120,
            cwd="/Applications/stock_dashboard"
        )
        if result.returncode == 0:
            logger.info(f"[BQ아침알림] ✅ {result.stdout.strip()}")
        else:
            logger.error(f"[BQ아침알림] ❌ {result.stderr[-500:]}")
    except Exception as e:
        logger.error(f"[BQ아침알림] 실행 오류: {e}")
```

---

## Task 5: 신용잔고 수집기 (P2)

### 목적
신용잔고 급감 → 강제 반대매매 종료 → 반등 시작 선행 신호.

### 저장 테이블 (신규)

```sql
CREATE TABLE IF NOT EXISTS margin_balance_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT    NOT NULL,
    dt              TEXT    NOT NULL,   -- YYYYMMDD
    credit_balance  REAL,               -- 신용잔고 (주)
    credit_amount   REAL,               -- 신용잔고금액 (원)
    credit_ratio    REAL,               -- 신용비율 (%)
    short_balance   REAL,               -- 대차잔고 (주, 기존 short_sell_daily에도 있음)
    data_source     TEXT DEFAULT 'kiwoom_ka10013',
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, dt)
);
```

### 구현 파일: `collectors/kiwoom_margin_collector.py`

```python
"""
collectors/kiwoom_margin_collector.py
키움 ka10013 — 신용거래동향 (신용잔고 추이)

API URI: /api/dostk/stkinfo
API-ID: ka10013

Request:
  stk_cd: 종목코드
  dt: 기준일 (YYYYMMDD, 공백이면 당일)
  qry_tp: 0=일별, 1=주별, 2=월별

Response: 일별 신용잔고/잔고금액/신용비율
"""

import sqlite3, requests, time, logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "/Applications/stock_dashboard/stock.db"


def _get_kiwoom_token() -> str | None:
    """kiwoom_realtime_quote 또는 환경변수에서 토큰 가져오기"""
    import os
    # 환경변수 우선
    token = os.getenv("KIWOOM_ACCESS_TOKEN")
    if token:
        return token
    # DB에서 최신 토큰
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT access_token FROM kiwoom_realtime_quote ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def fetch_margin_balance(stock_code: str, dt: str = "", token: str = None) -> list[dict]:
    """
    키움 ka10013으로 신용잔고 조회
    Returns: [{"dt": "20260601", "credit_balance": 123456, ...}, ...]
    """
    if not token:
        token = _get_kiwoom_token()
    if not token:
        logger.warning("키움 토큰 없음")
        return []
    
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Authorization": f"Bearer {token}",
        "api-id": "ka10013",
    }
    body = {
        "stk_cd": stock_code,
        "dt": dt,
        "qry_tp": "0",  # 일별
    }
    
    try:
        resp = requests.post(
            "https://api.kiwoom.com/api/dostk/stkinfo",
            headers=headers, json=body, timeout=10
        )
        time.sleep(0.3)
        data = resp.json()
        
        results = []
        for item in data.get("output", []):
            results.append({
                "dt": item.get("dt", "").replace("-", ""),
                "credit_balance": _safe_float(item.get("crdt_blnc")),
                "credit_amount": _safe_float(item.get("crdt_amt")),
                "credit_ratio": _safe_float(item.get("crdt_rt")),
            })
        return results
    except Exception as e:
        logger.warning(f"신용잔고 조회 오류 {stock_code}: {e}")
        return []


def _safe_float(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def collect_margin_balance(days_back: int = 60, limit: int = None):
    """전종목 신용잔고 수집"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS margin_balance_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL, dt TEXT NOT NULL,
            credit_balance REAL, credit_amount REAL, credit_ratio REAL,
            data_source TEXT DEFAULT 'kiwoom_ka10013',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, dt)
        );
        CREATE INDEX IF NOT EXISTS idx_mbd_code ON margin_balance_daily(stock_code, dt);
    """)
    conn.commit()
    
    token = _get_kiwoom_token()
    if not token:
        logger.error("키움 토큰 없음 — 수집 불가")
        conn.close()
        return
    
    stocks = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()
    
    if limit:
        stocks = stocks[:limit]
    
    for i, stock in enumerate(stocks):
        code = stock["stock_code"]
        rows = fetch_margin_balance(code, token=token)
        
        inserted = 0
        for row in rows:
            if not row["dt"]:
                continue
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO margin_balance_daily
                    (stock_code, dt, credit_balance, credit_amount, credit_ratio)
                    VALUES (?,?,?,?,?)
                """, (code, row["dt"], row["credit_balance"],
                      row["credit_amount"], row["credit_ratio"]))
                inserted += 1
            except Exception:
                pass
        
        conn.commit()
        if i % 100 == 0:
            logger.info(f"[{i}/{len(stocks)}] {code} +{inserted}행")
    
    conn.close()
    logger.info("=== 신용잔고 수집 완료 ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    collect_margin_balance(days_back=60)
```

---

## 실행 순서 (권장)

```bash
cd /Applications/stock_dashboard
source venv/bin/activate

# 1. Task 4 먼저 — 스케줄러 등록 (코드 수정 후 서버 재시작)
# scheduler.py 수정 후:
launchctl kickstart -k "gui/$(id -u)/com.stock-dashboard.local"

# 2. Task 3 (빠름 — DB에서 파싱, API 적게 씀)
python3 collectors/dart_dilution_collector.py
# 예상 소요: 30분~1시간

# 3. Task 5 (키움 토큰 필요 — 장중/장후 실행 권장)
python3 collectors/kiwoom_margin_collector.py --limit 500
# 예상 소요: 30분

# 4. Task 1 (DART API 다량 호출 — 야간 실행 권장)
nohup python3 collectors/dart_backlog_collector.py \
  --years 2022 2023 2024 2025 \
  > /tmp/backlog.log 2>&1 &
echo "PID: $!"
# 예상 소요: 4~6시간 (DART API 일일한도 주의)

# 5. Task 2 (Task 1 완료 후 실행)
nohup python3 collectors/dart_cost_collector.py \
  --years 2022 2023 2024 2025 \
  > /tmp/cost.log 2>&1 &
echo "PID: $!"
# 예상 소요: 3~4시간
```

---

## 검증 쿼리 (수집 완료 후 확인)

```sql
-- 수주잔고 수집 현황
SELECT year, quarter, COUNT(*) cnt, 
       AVG(backlog_normalized) avg_backlog_million
FROM order_backlog GROUP BY year, quarter ORDER BY year, quarter;

-- 수주잔고 TOP 10 (2024년)
SELECT stock_name, year, quarter, 
       backlog_normalized/100 as backlog_억원,
       backlog_to_rev
FROM order_backlog 
WHERE year=2024 AND quarter=4 
ORDER BY backlog_normalized DESC LIMIT 10;

-- CB/BW 발행 현황
SELECT event_type, COUNT(*) cnt, 
       AVG(dilution_pct) avg_dilution
FROM dilution_events GROUP BY event_type;

-- 신용잔고 급감 종목 (최근 30일)
SELECT stock_code, 
       MAX(credit_balance) max_30d,
       MIN(credit_balance) min_30d,
       (MIN(credit_balance)/MAX(credit_balance)-1)*100 chg_pct
FROM margin_balance_daily 
WHERE dt >= strftime('%Y%m%d', date('now','-30 days'))
GROUP BY stock_code
HAVING chg_pct < -30
ORDER BY chg_pct LIMIT 20;
```

---

## CLAUDE.md 업데이트 사항 (Codex가 작업 완료 후 추가할 것)

### 신규 테이블 섹션에 추가:

```
| `order_backlog` | 수집 후 확인 | stock_code, year, quarter, backlog_normalized(백만원), backlog_to_rev | 수주잔고 — DART 반기/연간 보고서 파싱 |
| `cost_structure` | 수집 후 확인 | stock_code, year, quarter, raw_material_ratio(%), cogs_ratio | 원가구조 — DART 원가명세서 파싱 |
| `dilution_events` | 수집 후 확인 | stock_code, event_type(CB/BW/RO), disclosed_at, dilution_pct | CB/BW/유상증자 이력 — 악재 필터용 |
| `margin_balance_daily` | 수집 후 확인 | stock_code, dt, credit_balance, credit_ratio | 신용잔고 일별 — Kiwoom ka10013 |
```

---

**완료 기준:**
- [ ] `order_backlog` 테이블 생성 + 2022~2025 수집
- [ ] `cost_structure` 테이블 생성 + 2022~2025 수집
- [ ] `dilution_events` 테이블 생성 + 최근 5년 수집
- [ ] `margin_balance_daily` 테이블 생성 + 최근 60일 수집
- [ ] scheduler.py에 BQ파이프라인 + 아침알림 잡 등록
- [ ] 서버 재시작 확인
- [ ] CLAUDE.md 업데이트
