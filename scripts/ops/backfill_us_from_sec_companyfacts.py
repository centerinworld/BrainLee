#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import time
import argparse
from datetime import datetime
from pathlib import Path

import requests

DB = Path('/Applications/stock_dashboard/stock.db')
UA = 'StockDashboard AdminContact admin@example.com'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'application/json,text/plain,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.sec.gov/',
}

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
    'pretax_income': ['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest', 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
    'tax_expense': ['IncomeTaxExpenseBenefit'],
    'net_income': ['NetIncomeLoss', 'ProfitLoss'],
    'assets': ['Assets'],
    'liabilities': ['Liabilities', 'LiabilitiesCurrent'],
    'equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'operating_cf': ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
    'investing_cf': ['NetCashProvidedByUsedInInvestingActivities', 'NetCashProvidedByUsedInInvestingActivitiesContinuingOperations'],
    'financing_cf': ['NetCashProvidedByUsedInFinancingActivities', 'NetCashProvidedByUsedInFinancingActivitiesContinuingOperations'],
    'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'CapitalExpendituresIncurredButNotYetPaid'],
    'depreciation': ['DepreciationDepletionAndAmortization', 'DepreciationAmortizationAndAccretionNet'],
}


def load_map() -> dict[str, str]:
    url = 'https://www.sec.gov/files/company_tickers.json'
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    obj = r.json()
    out = {}
    if isinstance(obj, dict):
        for v in obj.values():
            t = str(v.get('ticker', '')).upper().strip()
            c = str(v.get('cik_str', '')).strip()
            if t and c:
                out[t] = c.zfill(10)
    return out


def get_units(facts: dict, tag_list: list[str], unit='USD'):
    gaap = ((facts.get('facts') or {}).get('us-gaap') or {})
    for tag in tag_list:
        t = gaap.get(tag)
        if not t:
            continue
        units = (t.get('units') or {}).get(unit)
        if units:
            return units
    return []


def _duration_days(it: dict) -> int | None:
    try:
        s = datetime.fromisoformat(str(it.get('start') or '')[:10])
        e = datetime.fromisoformat(str(it.get('end') or '')[:10])
        return (e - s).days
    except Exception:
        return None


def pick_latest_value(items: list[dict], period_end: str, prefer_fp: str | None, period_type: str, is_flow: bool = True):
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
            # SEC CompanyFacts often includes both YTD and single-quarter facts for Q2/Q3.
            # Prefer explicit CYxxxxQn frame or one-quarter duration; never backfill YTD
            # as a quarterly value when no single-quarter fact is available.
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
    # 기준 태그: revenue + assets 를 합쳐 period set 생성
    rev_items = get_units(facts, TAG_CAND['revenue'])
    ast_items = get_units(facts, TAG_CAND['assets'])
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
    # 최신순 정렬 후 annual 6, quarter 16
    ann = [k for k in periods.keys() if k[1] == 'annual']
    qtr = [k for k in periods.keys() if k[1] == 'quarter']
    ann.sort(key=lambda x: x[0], reverse=True)
    qtr.sort(key=lambda x: x[0], reverse=True)
    return ann[:6], qtr[:16], periods


def upsert_fin(conn, tk: str, pend: str, ptype: str, fp: str, facts: dict):
    vals = {}
    flow_keys = [
        'revenue','cogs','gross_profit','operating_expense','sga','rnd','ebitda',
        'operating_income','interest_expense','pretax_income','tax_expense','net_income'
    ]
    stock_keys = ['assets','liabilities','equity']
    for key in flow_keys:
        items = get_units(facts, TAG_CAND[key])
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=True)
    for key in stock_keys:
        items = get_units(facts, TAG_CAND[key])
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=False)

    rev = vals.get('revenue')
    gp = vals.get('gross_profit')
    cogs = vals.get('cogs')
    opi = vals.get('operating_income')
    if cogs is None and rev is not None and gp is not None:
        vals['cogs'] = rev - gp
    if gp is None and rev is not None and cogs is not None:
        vals['gross_profit'] = rev - cogs
        gp = vals['gross_profit']
    if vals.get('operating_expense') is None and gp is not None and opi is not None:
        vals['operating_expense'] = gp - opi
    opm = (opi / rev * 100.0) if (opi is not None and rev not in (None, 0)) else None

    conn.execute(
        """
        INSERT INTO us_financial_data
        (ticker, period_end, period_type, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
         operating_income, interest_expense, pretax_income, tax_expense, net_income, assets, liabilities, equity, opm, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
          opm=COALESCE(excluded.opm, us_financial_data.opm),
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            tk, pend, ptype,
            vals.get('revenue'), vals.get('cogs'), vals.get('gross_profit'), vals.get('operating_expense'),
            vals.get('sga'), vals.get('rnd'), vals.get('ebitda'), vals.get('operating_income'),
            vals.get('interest_expense'), vals.get('pretax_income'), vals.get('tax_expense'), vals.get('net_income'),
            vals.get('assets'), vals.get('liabilities'), vals.get('equity'), opm,
        ),
    )


def upsert_cf(conn, tk: str, pend: str, ptype: str, fp: str, facts: dict):
    vals = {}
    for key in ['operating_cf','investing_cf','financing_cf','capex','depreciation']:
        items = get_units(facts, TAG_CAND[key])
        vals[key] = pick_latest_value(items, pend, fp, ptype, is_flow=True)

    ocf = vals.get('operating_cf')
    cap = vals.get('capex')
    free_cf = (ocf - abs(cap)) if (ocf is not None and cap is not None) else None

    conn.execute(
        """
        INSERT INTO us_cashflow_data
        (ticker, period_end, period_type, operating_cf, investing_cf, financing_cf, capex, depreciation, free_cf, created_at, updated_at)
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
        (tk, pend, ptype, vals.get('operating_cf'), vals.get('investing_cf'), vals.get('financing_cf'), vals.get('capex'), vals.get('depreciation'), free_cf),
    )


def main():
    ap = argparse.ArgumentParser(description='Backfill US financial/cashflow data from SEC companyfacts.')
    ap.add_argument('--tickers', default='', help='Comma-separated tickers to process first/only, e.g. NVDA,AAPL,MSFT')
    ap.add_argument('--limit', type=int, default=0, help='Process top N US stocks by market cap. 0 means all.')
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB), timeout=120)
    conn.execute('PRAGMA busy_timeout=120000')
    try:
        requested = [x.strip().upper() for x in (args.tickers or '').split(',') if x.strip()]
        if requested:
            tks = requested
        elif args.limit and args.limit > 0:
            tks = [r[0] for r in conn.execute(
                """
                SELECT DISTINCT ticker
                FROM us_stock_meta
                WHERE ticker IS NOT NULL AND ticker!=''
                ORDER BY COALESCE(market_cap, 0) DESC
                LIMIT ?
                """,
                (args.limit,),
            ).fetchall()]
        else:
            tks = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM us_stock_meta WHERE ticker IS NOT NULL AND ticker!='' ORDER BY ticker").fetchall()]
        mp = load_map()
        ok = 0
        for tk in tks:
            cik = mp.get(tk)
            if not cik:
                continue
            url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.status_code != 200:
                    continue
                facts = r.json()
            except Exception:
                continue

            ann, qtr, periods = build_periods(facts)
            for pend, ptype in ann + qtr:
                fp = periods.get((pend, ptype))
                upsert_fin(conn, tk, pend, ptype, fp, facts)
                upsert_cf(conn, tk, pend, ptype, fp, facts)
            ok += 1
            time.sleep(0.15)

        conn.commit()
        print(json.dumps({'tickers': len(tks), 'processed': ok}, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
