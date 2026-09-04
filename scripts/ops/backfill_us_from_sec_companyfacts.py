#!/usr/bin/env python3
"""
US 종목 재무/현금흐름 데이터 SEC EDGAR 전수 백필 스크립트.

- Rate Limit: SEC API 초당 10요청 허용. 요청 간격 0.12s(~8.3/s)로 여유 있게 유지.
- 429/503 시 지수 백오프 재시도(최대 5회).
- 파생 지표 자동 계산: gross_profit, opm, ebitda, eps, bps, pbr, per.
- 50종목마다 commit + 진행 상황 출력.
- --resume 플래그: 최근 7일 이내 업데이트된 종목 스킵(중단 후 재개 가능).
"""
from __future__ import annotations

import json
import sqlite3
import time
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

import requests

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
UA = 'StockDashboard AdminContact admin@example.com'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'application/json,text/plain,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.sec.gov/',
}

# SEC API Rate Limit: 10 req/s. 0.12s 간격 = 8.3req/s (안전 마진 확보)
SEC_INTERVAL = 0.12
MAX_RETRY = 5
COMMIT_EVERY = 50

TAG_CAND = {
    'revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet'],
    'cogs': ['CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold'],
    'gross_profit': ['GrossProfit'],
    'operating_expense': ['OperatingExpenses', 'CostsAndExpenses'],
    'sga': ['SellingGeneralAndAdministrativeExpense', 'GeneralAndAdministrativeExpense'],
    'rnd': ['ResearchAndDevelopmentExpense', 'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost'],
    'ebitda': ['EarningsBeforeInterestTaxesDepreciationAmortization'],
    'operating_income': ['OperatingIncomeLoss'],
    'interest_expense': ['InterestExpenseNonOperating', 'InterestExpense'],
    'pretax_income': [
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'
    ],
    'tax_expense': ['IncomeTaxExpenseBenefit'],
    'net_income': ['NetIncomeLoss', 'ProfitLoss'],
    'assets': ['Assets'],
    'liabilities': ['Liabilities', 'LiabilitiesCurrent'],
    'equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'eps': ['EarningsPerShareDiluted', 'EarningsPerShareBasic'],
    'shares': [
        'WeightedAverageNumberOfDilutedSharesOutstanding',
        'WeightedAverageNumberOfSharesOutstandingBasic',
        'CommonStockSharesOutstanding'
    ],
    'operating_cf': [
        'NetCashProvidedByUsedInOperatingActivities',
        'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'
    ],
    'investing_cf': [
        'NetCashProvidedByUsedInInvestingActivities',
        'NetCashProvidedByUsedInInvestingActivitiesContinuingOperations'
    ],
    'financing_cf': [
        'NetCashProvidedByUsedInFinancingActivities',
        'NetCashProvidedByUsedInFinancingActivitiesContinuingOperations'
    ],
    'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'CapitalExpendituresIncurredButNotYetPaid'],
    'depreciation': ['DepreciationDepletionAndAmortization', 'DepreciationAmortizationAndAccretionNet'],
}


# ─── Rate Limiter ────────────────────────────────────────────────────────────
class RateLimiter:
    """토큰 버킷 기반 rate limiter. 초당 max_per_sec 요청 초과 방지."""
    def __init__(self, max_per_sec: float = 8.3):
        self.interval = 1.0 / max_per_sec
        self._last = 0.0

    def wait(self):
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.monotonic()


_limiter = RateLimiter(max_per_sec=8.3)


def sec_get(url: str, timeout: int = 30) -> requests.Response | None:
    """SEC API 요청. 429/503 시 지수 백오프로 최대 MAX_RETRY회 재시도."""
    for attempt in range(MAX_RETRY):
        _limiter.wait()
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            elif r.status_code in (429, 503):
                wait_sec = min(2 ** attempt * 2.0, 60.0)
                print(f'  [WARN] HTTP {r.status_code} → {wait_sec:.0f}s 대기 후 재시도 (attempt {attempt+1}/{MAX_RETRY})', flush=True)
                time.sleep(wait_sec)
            elif r.status_code == 404:
                return None  # 해당 CIK 없음
            else:
                print(f'  [WARN] HTTP {r.status_code} for {url}', flush=True)
                return None
        except (requests.Timeout, requests.ConnectionError) as e:
            wait_sec = min(2 ** attempt * 1.5, 30.0)
            print(f'  [WARN] 연결 오류 ({e}) → {wait_sec:.0f}s 대기 후 재시도', flush=True)
            time.sleep(wait_sec)
    return None


# ─── SEC EDGAR 파싱 유틸 ──────────────────────────────────────────────────────
def load_map() -> dict[str, str]:
    """SEC ticker → CIK 매핑 로드."""
    url = 'https://www.sec.gov/files/company_tickers.json'
    r = sec_get(url, timeout=25)
    if not r:
        raise RuntimeError('SEC company_tickers.json 로드 실패')
    obj = r.json()
    out = {}
    if isinstance(obj, dict):
        for v in obj.values():
            t = str(v.get('ticker', '')).upper().strip()
            c = str(v.get('cik_str', '')).strip()
            if t and c:
                out[t] = c.zfill(10)
    return out


def get_units(facts: dict, tag_list: list[str], unit='USD') -> list:
    gaap = ((facts.get('facts') or {}).get('us-gaap') or {})
    target_units = [unit] if isinstance(unit, str) else list(unit or ['USD'])
    for tag in tag_list:
        t = gaap.get(tag)
        if not t:
            continue
        units_map = t.get('units') or {}
        for u in target_units:
            items = units_map.get(u)
            if items:
                return items
    return []


def _duration_days(it: dict) -> int | None:
    try:
        s = datetime.fromisoformat(str(it.get('start') or '')[:10])
        e = datetime.fromisoformat(str(it.get('end') or '')[:10])
        return (e - s).days
    except Exception:
        return None


def pick_latest_value(items: list[dict], period_end: str, prefer_fp: str | None,
                      period_type: str, is_flow: bool = True) -> float | None:
    cand = []
    for it in items:
        if str(it.get('end') or '') != period_end:
            continue
        fp = str(it.get('fp') or '')
        if prefer_fp and fp != prefer_fp:
            continue
        val = it.get('val')
        try:
            val = float(val)
        except Exception:
            continue
        filed = str(it.get('filed') or '')
        frame = str(it.get('frame') or '')
        dur = _duration_days(it)

        if is_flow and period_type == 'quarter':
            q_ok = prefer_fp in ('Q1', 'Q2', 'Q3', 'Q4') and frame.endswith(prefer_fp)
            dur_ok = dur is not None and 20 <= dur <= 120
            if not q_ok and not dur_ok:
                continue
            score = 2 if q_ok else 1
        elif is_flow and period_type == 'annual':
            dur_ok = dur is None or dur >= 300
            if not dur_ok:
                continue
            score = 1
        else:
            score = 1

        cand.append((score, filed, val))
    if not cand:
        return None
    cand.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return cand[0][2]


def build_periods(facts: dict):
    rev_items = get_units(facts, TAG_CAND['revenue'], unit='USD')
    ast_items = get_units(facts, TAG_CAND['assets'], unit='USD')
    periods = {}
    for it in rev_items + ast_items:
        end = str(it.get('end') or '')
        fp = str(it.get('fp') or '')
        form = str(it.get('form') or '')
        if not end:
            continue
        if fp == 'FY' and form.startswith('10-K'):
            periods[(end, 'annual')] = 'FY'
        elif fp in ('Q1', 'Q2', 'Q3', 'Q4') and (form.startswith('10-Q') or form.startswith('10-K')):
            periods[(end, 'quarter')] = fp
    ann = sorted([k for k in periods if k[1] == 'annual'], key=lambda x: x[0], reverse=True)
    qtr = sorted([k for k in periods if k[1] == 'quarter'], key=lambda x: x[0], reverse=True)
    return ann[:6], qtr[:16], periods


# ─── DB Upsert ────────────────────────────────────────────────────────────────
def upsert_fin(conn: sqlite3.Connection, tk: str, pend: str, ptype: str, fp: str, facts: dict):
    vals = {}
    flow_keys = [
        'revenue', 'cogs', 'gross_profit', 'operating_expense', 'sga', 'rnd', 'ebitda',
        'operating_income', 'interest_expense', 'pretax_income', 'tax_expense', 'net_income'
    ]
    stock_keys = ['assets', 'liabilities', 'equity']

    for key in flow_keys:
        items = get_units(facts, TAG_CAND[key], unit='USD')
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=True)
    for key in stock_keys:
        items = get_units(facts, TAG_CAND[key], unit='USD')
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=False)

    # EPS: USD/shares 단위
    eps_items = get_units(facts, TAG_CAND['eps'], unit=['USD/shares', 'USD/share', 'pure'])
    vals['eps'] = pick_latest_value(eps_items, pend, fp, ptype, is_flow=True)

    # 발행주식수: shares 단위
    share_items = get_units(facts, TAG_CAND['shares'], unit=['shares', 'pure'])
    vals['shares'] = pick_latest_value(share_items, pend, fp, ptype, is_flow=False)

    rev = vals.get('revenue')
    gp = vals.get('gross_profit')
    cogs = vals.get('cogs')
    opi = vals.get('operating_income')
    ni = vals.get('net_income')
    eq = vals.get('equity')
    sh = vals.get('shares')

    # 파생 계산
    if cogs is None and rev is not None and gp is not None:
        vals['cogs'] = rev - gp
    if gp is None and rev is not None:
        vals['gross_profit'] = rev - (cogs or 0.0)
        gp = vals['gross_profit']
    if vals.get('operating_expense') is None and gp is not None and opi is not None:
        vals['operating_expense'] = gp - opi

    opm = (opi / rev * 100.0) if (opi is not None and rev not in (None, 0)) else None

    eps = vals.get('eps')
    if eps is None and ni is not None and sh is not None and sh > 0:
        eps = ni / sh

    bps = None
    if eq is not None and sh is not None and sh > 0:
        bps = eq / sh

    conn.execute(
        """
        INSERT INTO us_financial_data
        (ticker, period_end, period_type, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
         operating_income, interest_expense, pretax_income, tax_expense, net_income,
         assets, liabilities, equity, eps, bps, opm, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker, period_end, period_type)
        DO UPDATE SET
          revenue=COALESCE(excluded.revenue, us_financial_data.revenue),
          cogs=COALESCE(excluded.cogs, us_financial_data.cogs),
          gross_profit=COALESCE(excluded.gross_profit, us_financial_data.gross_profit),
          operating_expense=COALESCE(excluded.operating_expense, us_financial_data.operating_expense),
          sga=COALESCE(excluded.sga, us_financial_data.sga),
          rnd=COALESCE(excluded.rnd, us_financial_data.rnd),
          ebitda=COALESCE(excluded.ebitda, us_financial_data.ebitda),
          operating_income=COALESCE(excluded.operating_income, us_financial_data.operating_income),
          interest_expense=COALESCE(excluded.interest_expense, us_financial_data.interest_expense),
          pretax_income=COALESCE(excluded.pretax_income, us_financial_data.pretax_income),
          tax_expense=COALESCE(excluded.tax_expense, us_financial_data.tax_expense),
          net_income=COALESCE(excluded.net_income, us_financial_data.net_income),
          assets=COALESCE(excluded.assets, us_financial_data.assets),
          liabilities=COALESCE(excluded.liabilities, us_financial_data.liabilities),
          equity=COALESCE(excluded.equity, us_financial_data.equity),
          eps=COALESCE(excluded.eps, us_financial_data.eps),
          bps=COALESCE(excluded.bps, us_financial_data.bps),
          opm=COALESCE(excluded.opm, us_financial_data.opm),
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            tk, pend, ptype,
            vals.get('revenue'), vals.get('cogs'), vals.get('gross_profit'), vals.get('operating_expense'),
            vals.get('sga'), vals.get('rnd'), vals.get('ebitda'), vals.get('operating_income'),
            vals.get('interest_expense'), vals.get('pretax_income'), vals.get('tax_expense'), vals.get('net_income'),
            vals.get('assets'), vals.get('liabilities'), vals.get('equity'), eps, bps, opm,
        ),
    )


def upsert_cf(conn: sqlite3.Connection, tk: str, pend: str, ptype: str, fp: str, facts: dict):
    vals = {}
    for key in ['operating_cf', 'investing_cf', 'financing_cf', 'capex', 'depreciation']:
        items = get_units(facts, TAG_CAND[key], unit='USD')
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=True)

    ocf = vals.get('operating_cf')
    cap = vals.get('capex')
    free_cf = (ocf - abs(cap)) if (ocf is not None and cap is not None) else None

    conn.execute(
        """
        INSERT INTO us_cashflow_data
        (ticker, period_end, period_type, operating_cf, investing_cf, financing_cf,
         capex, depreciation, free_cf, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker, period_end, period_type)
        DO UPDATE SET
          operating_cf=COALESCE(excluded.operating_cf, us_cashflow_data.operating_cf),
          investing_cf=COALESCE(excluded.investing_cf, us_cashflow_data.investing_cf),
          financing_cf=COALESCE(excluded.financing_cf, us_cashflow_data.financing_cf),
          capex=COALESCE(excluded.capex, us_cashflow_data.capex),
          depreciation=COALESCE(excluded.depreciation, us_cashflow_data.depreciation),
          free_cf=COALESCE(excluded.free_cf, us_cashflow_data.free_cf),
          updated_at=CURRENT_TIMESTAMP
        """,
        (tk, pend, ptype,
         vals.get('operating_cf'), vals.get('investing_cf'), vals.get('financing_cf'),
         vals.get('capex'), vals.get('depreciation'), free_cf),
    )


def apply_derived_metrics(conn: sqlite3.Connection, tk: str):
    """수집 후 종목별 파생 지표(ebitda, pbr, per 등) 보강."""
    # EBITDA = operating_income + depreciation (cashflow에서 가져옴)
    conn.execute("""
        UPDATE us_financial_data
        SET ebitda = operating_income + COALESCE((
            SELECT c.depreciation FROM us_cashflow_data c
            WHERE c.ticker = us_financial_data.ticker
              AND c.period_end = us_financial_data.period_end
              AND c.period_type = us_financial_data.period_type
        ), 0)
        WHERE ticker = ?
          AND ebitda IS NULL
          AND operating_income IS NOT NULL
    """, (tk,))

    # gross_profit 보강
    conn.execute("""
        UPDATE us_financial_data
        SET gross_profit = COALESCE(gross_profit, revenue - COALESCE(cogs, 0))
        WHERE ticker = ? AND revenue IS NOT NULL
    """, (tk,))

    # OPM 재계산
    conn.execute("""
        UPDATE us_financial_data
        SET opm = (operating_income / revenue) * 100.0
        WHERE ticker = ?
          AND (opm IS NULL OR opm = 0)
          AND operating_income IS NOT NULL
          AND revenue IS NOT NULL AND revenue != 0
    """, (tk,))

    # PBR = close / bps (가격 이력에서)
    conn.execute("""
        UPDATE us_financial_data
        SET pbr = COALESCE(pbr, (
            SELECT p.close / us_financial_data.bps
            FROM us_price_history p
            WHERE p.ticker = us_financial_data.ticker
              AND p.date <= us_financial_data.period_end
              AND us_financial_data.bps > 0
            ORDER BY p.date DESC LIMIT 1
        ))
        WHERE ticker = ? AND (pbr IS NULL OR pbr = 0) AND bps IS NOT NULL AND bps > 0
    """, (tk,))

    # PER = close / eps
    conn.execute("""
        UPDATE us_financial_data
        SET per = COALESCE(per, (
            SELECT p.close / us_financial_data.eps
            FROM us_price_history p
            WHERE p.ticker = us_financial_data.ticker
              AND p.date <= us_financial_data.period_end
              AND us_financial_data.eps != 0
            ORDER BY p.date DESC LIMIT 1
        ))
        WHERE ticker = ? AND (per IS NULL OR per = 0) AND eps IS NOT NULL AND eps != 0
    """, (tk,))


def print_stats(conn: sqlite3.Connection):
    """현재 채움률 출력."""
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM us_financial_data").fetchone()[0]
    print(f'\n=== US_FINANCIAL_DATA 채움률 (전체 {total:,}행) ===')
    for col in ['revenue', 'gross_profit', 'operating_income', 'net_income', 'assets',
                'equity', 'eps', 'bps', 'pbr', 'per', 'opm', 'ebitda']:
        null = cur.execute(f"SELECT COUNT(*) FROM us_financial_data WHERE {col} IS NULL").fetchone()[0]
        pct = (total - null) / total * 100 if total else 0
        print(f'  {col:20s}: {total-null:6,}/{total:6,} 채움 ({pct:5.1f}%)')


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='US 종목 SEC EDGAR 전수 백필 (Rate-Limited)')
    ap.add_argument('--tickers', default='', help='특정 ticker만 처리 (쉼표 구분). 미입력 시 전체.')
    ap.add_argument('--limit', type=int, default=0, help='시총 상위 N개만 처리.')
    ap.add_argument('--resume', action='store_true',
                    help='최근 7일 이내 업데이트된 종목 스킵 (중단 후 재개용)')
    ap.add_argument('--stats', action='store_true', help='처리 전후 채움률 출력')
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB), timeout=300)
    conn.execute('PRAGMA busy_timeout=300000')
    conn.execute('PRAGMA journal_mode=WAL')

    try:
        if args.stats:
            print_stats(conn)

        # 처리 대상 ticker 목록
        requested = [x.strip().upper() for x in (args.tickers or '').split(',') if x.strip()]
        if requested:
            tks = requested
        elif args.limit > 0:
            tks = [r[0] for r in conn.execute(
                """SELECT DISTINCT ticker FROM us_stock_meta
                   WHERE ticker IS NOT NULL AND ticker != ''
                   ORDER BY COALESCE(market_cap, 0) DESC LIMIT ?""",
                (args.limit,)
            ).fetchall()]
        else:
            tks = [r[0] for r in conn.execute(
                """SELECT DISTINCT ticker FROM us_stock_meta
                   WHERE ticker IS NOT NULL AND ticker != ''
                   ORDER BY COALESCE(market_cap, 0) DESC"""
            ).fetchall()]

        # resume 모드: 최근 7일 이내 수집된 종목 제외
        if args.resume:
            cutoff = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
            already = {r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM us_financial_data WHERE updated_at >= ?", (cutoff,)
            ).fetchall()}
            original_len = len(tks)
            tks = [t for t in tks if t not in already]
            print(f'[Resume] {original_len}개 중 {len(already)}개 스킵 → {len(tks)}개 처리 예정', flush=True)

        # SEC ticker → CIK 매핑 로드
        print('SEC CIK 매핑 로드 중...', flush=True)
        mp = load_map()
        print(f'CIK 매핑 로드 완료: {len(mp):,}개', flush=True)

        ok = 0
        skip = 0
        err = 0
        total = len(tks)
        start_time = time.time()

        for idx, tk in enumerate(tks, 1):
            cik = mp.get(tk)
            if not cik:
                skip += 1
                continue

            url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
            r = sec_get(url)
            if not r:
                err += 1
                continue

            try:
                facts = r.json()
            except Exception:
                err += 1
                continue

            ann, qtr, periods = build_periods(facts)
            if not ann and not qtr:
                skip += 1
                continue

            for pend, ptype in ann + qtr:
                fp = periods.get((pend, ptype))
                upsert_fin(conn, tk, pend, ptype, fp, facts)
                upsert_cf(conn, tk, pend, ptype, fp, facts)

            # 파생 지표 보강
            apply_derived_metrics(conn, tk)

            ok += 1

            # 50종목마다 commit + 진행 상황 출력
            if ok % COMMIT_EVERY == 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = ok / elapsed if elapsed > 0 else 0
                eta_sec = (total - idx) / rate if rate > 0 else 0
                eta_str = str(timedelta(seconds=int(eta_sec)))
                pct = idx / total * 100
                print(
                    f'[{idx:5d}/{total}] {pct:5.1f}% | 완료:{ok} 스킵:{skip} 오류:{err} | '
                    f'{rate:.1f}종목/s | 예상 완료까지: {eta_str}',
                    flush=True
                )

        conn.commit()
        elapsed = time.time() - start_time
        print(f'\n=== 완료 ===', flush=True)
        print(f'처리: {ok}개 | 스킵: {skip}개 | 오류: {err}개 | 소요: {timedelta(seconds=int(elapsed))}', flush=True)

        if args.stats:
            print_stats(conn)

        print(json.dumps({'tickers': total, 'processed': ok, 'skip': skip, 'error': err},
                         ensure_ascii=False))

    finally:
        conn.close()


if __name__ == '__main__':
    main()
