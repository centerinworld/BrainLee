import sqlite3
import os
import pandas as pd
import numpy as np
from typing import Optional
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/employment-v2", tags=["employment-v2"])

DIR = os.path.dirname(__file__)
EMP_DB = os.path.join(DIR, "employment.db")
STOCK_DB = "/Applications/stock_dashboard/stock.db"

@router.get("/yearly")
def get_yearly_employment(limit: int = 200, sort_by: str = "count"):
    """고용보험 현황 (근로복지공단 wlb_monthly 기반) — 실제 고용인원 랭킹.

    sort_by: count(인원수순) | workplace(사업장수순) | name(가나다)
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS stock_db")

        # 최신 data_ym 확인
        ym_row = conn.execute("SELECT MAX(data_ym) FROM wlb_monthly").fetchone()
        latest_ym = ym_row[0] if ym_row and ym_row[0] else None

        if latest_ym:
            order_col = "total_workers" if sort_by in ("count", "increase") else "workplace_cnt" if sort_by == "workplace" else "w.stock_name"
            query = f"""
                SELECT
                    w.stock_code, w.stock_name,
                    w.total_workers, w.workplace_cnt,
                    w.data_ym,
                    u.market,
                    u.sector_small as sector
                FROM wlb_monthly w
                LEFT JOIN stock_db.stock_universe u ON w.stock_code = u.stock_code
                WHERE w.data_ym = ?
                ORDER BY {order_col} DESC
                LIMIT ?
            """
            rows = conn.execute(query, (latest_ym, limit)).fetchall()

            # 최신 수집일자
            meta_row = conn.execute("SELECT MAX(fetched_at) FROM wlb_monthly WHERE data_ym=?", (latest_ym,)).fetchone()
            updated_at = meta_row[0][:10] if meta_row and meta_row[0] else latest_ym[:4]+'-'+latest_ym[4:6]+'-01'

            result = []
            for r in rows:
                d = dict(r)
                d['count_26'] = d.get('total_workers')  # 호환성 필드명
                d['count_25'] = None
                d['count_24'] = None
                d['yoy_26'] = None
                d['yoy_25'] = None
                d['yoy_24'] = None
                result.append(d)

            # 총계
            total_row = conn.execute("SELECT SUM(total_workers), SUM(workplace_cnt) FROM wlb_monthly WHERE data_ym=?", (latest_ym,)).fetchone()
        else:
            # WLB 데이터 없음: 빈 결과
            result = []
            updated_at = None
            total_row = (0, 0)
            latest_ym = None

        # 날짜 fallback
        if not updated_at:
            from datetime import date as _date
            updated_at = _date.today().isoformat()

        return {
            "rows": result,
            "date": updated_at,
            "data_ym": latest_ym,
            "total_workers": total_row[0] or 0,
            "total_workplaces": total_row[1] or 0,
            "source": "근로복지공단 고용보험",
        }
    finally:
        conn.close()

# 전역 캐시
_trend_data_cache = None
_trend_data_cache_at = 0

def get_trend_data():
    global _trend_data_cache, _trend_data_cache_at
    import time
    if _trend_data_cache is not None and (time.time() - _trend_data_cache_at) < 3600:
        return _trend_data_cache

    conn = sqlite3.connect(EMP_DB)
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")

    # 1. KOSPI/KOSDAQ 종목 가져오기
    markets = pd.read_sql("SELECT stock_code, stock_name, market, sector_small as sector FROM main_db.stock_universe WHERE market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')", conn)

    # 2. NPS 데이터 가져오기 — nps_workplace_monthly (구) 또는 nps_monthly (신) 시도
    nps_df = pd.DataFrame()
    for _tbl in ("nps_workplace_monthly", "nps_monthly"):
        try:
            _sql = f"SELECT ym, stock_code, nw_acqzr_cnt as new_cnt, lss_jnngp_cnt as lost_cnt, (nw_acqzr_cnt - lss_jnngp_cnt) as net_change FROM {_tbl} ORDER BY ym ASC"
            nps_df = pd.read_sql(_sql, conn)
            if not nps_df.empty:
                break
        except Exception:
            pass

    # 3. WLB 근로복지공단 데이터 가져오기 (모든 수집 월)
    wlb_by_ym = {}   # {data_ym: {stock_code: {total_workers, workplace_cnt}}}
    wlb_map = {}     # stock_code → {total_workers, workplace_cnt, data_ym}  (최신 월)
    try:
        wlb_all_rows = conn.execute(
            "SELECT data_ym, stock_code, total_workers, workplace_cnt FROM wlb_monthly ORDER BY data_ym"
        ).fetchall()
        for r in wlb_all_rows:
            ym = r[0]
            if ym not in wlb_by_ym:
                wlb_by_ym[ym] = {}
            wlb_by_ym[ym][r[1]] = {'total_workers': r[2], 'workplace_cnt': r[3]}
    except Exception:
        pass

    available_wlb_yms = sorted(wlb_by_ym.keys(), reverse=True)
    latest_wlb_ym = available_wlb_yms[0] if available_wlb_yms else None
    if latest_wlb_ym:
        for code, v in wlb_by_ym[latest_wlb_ym].items():
            wlb_map[code] = {**v, 'data_ym': latest_wlb_ym}

    def _subtract_ym(ym: str, months: int) -> str:
        """YYYYMM - N months"""
        if not ym: return None
        y, m = int(ym[:4]), int(ym[4:6])
        m -= months
        while m <= 0:
            m += 12
            y -= 1
        return f"{y}{m:02d}"

    def _find_ym(target: str) -> Optional[str]:
        """Find closest WLB month to target (within ±2 months), excluding latest"""
        if not target or len(available_wlb_yms) <= 1:
            return None
        target_int = int(target)
        for ym in available_wlb_yms[1:]:  # skip latest
            if abs(int(ym) - target_int) <= 2:
                return ym
        return None

    # 1M/3M/6M/1Y 비교 대상 월
    wlb_1m_ym  = _find_ym(_subtract_ym(latest_wlb_ym, 1))
    wlb_3m_ym  = _find_ym(_subtract_ym(latest_wlb_ym, 3))
    wlb_6m_ym  = _find_ym(_subtract_ym(latest_wlb_ym, 6))
    wlb_1y_ym  = _find_ym(_subtract_ym(latest_wlb_ym, 12))

    conn.close()
    
    # NPS Pivot for net_change
    available_months = sorted(nps_df['ym'].unique(), reverse=True)
    nps_pivot = nps_df.pivot_table(index='stock_code', columns='ym', values='net_change', aggfunc='sum') if not nps_df.empty else pd.DataFrame()
    new_pivot = nps_df.pivot_table(index='stock_code', columns='ym', values='new_cnt', aggfunc='sum') if not nps_df.empty else pd.DataFrame()
    lost_pivot = nps_df.pivot_table(index='stock_code', columns='ym', values='lost_cnt', aggfunc='sum') if not nps_df.empty else pd.DataFrame()

    # NPS dictionary for chart history
    history_dict = {}
    for _, row in nps_df.iterrows():
        code = row['stock_code']
        if code not in history_dict:
            history_dict[code] = []
        ym = row['ym']
        history_dict[code].append({
            'month': f"{ym[:4]}-{ym[4:]}",
            'new_cnt': int(row['new_cnt']),
            'lost_cnt': int(row['lost_cnt']),
            'net_change': int(row['net_change'])
        })
    
    # Find specific target months
    target_0m = available_months[0] if len(available_months) > 0 else None
    target_1m = available_months[1] if len(available_months) > 1 else None
    target_3m = available_months[3] if len(available_months) > 3 else None
    target_6m = available_months[6] if len(available_months) > 6 else None
    target_1y = available_months[12] if len(available_months) > 12 else None
    
    def _safe_int(v):
        try:
            return None if v is None or (hasattr(pd, 'isna') and pd.isna(v)) else int(v)
        except Exception:
            return None

    res = []
    for _, row in markets.iterrows():
        code = row['stock_code']

        diff_0m = diff_1m = diff_3m = diff_6m = diff_1y = None
        new_0m = lost_0m = new_1m = lost_1m = None
        history = history_dict.get(code, [])

        if code in nps_pivot.index:
            stock_nps = nps_pivot.loc[code]
            if target_0m: diff_0m = stock_nps.get(target_0m, None)
            if target_1m: diff_1m = stock_nps.get(target_1m, None)
            if target_3m: diff_3m = stock_nps.get(target_3m, None)
            if target_6m: diff_6m = stock_nps.get(target_6m, None)
            if target_1y: diff_1y = stock_nps.get(target_1y, None)

        if code in new_pivot.index:
            s = new_pivot.loc[code]
            if target_0m: new_0m = s.get(target_0m, None)
            if target_1m: new_1m = s.get(target_1m, None)

        if code in lost_pivot.index:
            s = lost_pivot.loc[code]
            if target_0m: lost_0m = s.get(target_0m, None)
            if target_1m: lost_1m = s.get(target_1m, None)

        wlb = wlb_map.get(code, {})
        w_now = wlb.get('total_workers')

        def _wlb_diff(ref_ym):
            """해당 월 대비 현재 피보험자 증감"""
            if ref_ym is None or w_now is None:
                return None
            past = wlb_by_ym.get(ref_ym, {}).get(code, {}).get('total_workers')
            return (w_now - past) if past is not None else None

        res.append({
            'stock_code': code,
            'stock_name': row['stock_name'],
            'market': row['market'],
            'sector': row['sector'],
            'latest_count': None,
            'total_workers':  w_now,                       # WLB 고용보험 피보험자
            'workplace_cnt':  wlb.get('workplace_cnt'),    # WLB 사업장 수
            'wlb_data_ym':    wlb.get('data_ym'),          # WLB 기준 월
            'wlb_diff_1m':    _wlb_diff(wlb_1m_ym),       # 1개월 전 대비
            'wlb_diff_3m':    _wlb_diff(wlb_3m_ym),       # 3개월 전 대비
            'wlb_diff_6m':    _wlb_diff(wlb_6m_ym),       # 6개월 전 대비
            'wlb_diff_1y':    _wlb_diff(wlb_1y_ym),       # 1년 전 대비
            'diff_0m':  _safe_int(diff_0m),
            'diff_1m':  _safe_int(diff_1m),
            'diff_3m':  _safe_int(diff_3m),
            'diff_6m':  _safe_int(diff_6m),
            'diff_1y':  _safe_int(diff_1y),
            'new_0m':   _safe_int(new_0m),
            'lost_0m':  _safe_int(lost_0m),
            'new_1m':   _safe_int(new_1m),
            'lost_1m':  _safe_int(lost_1m),
            'history': history
        })
        
    _trend_data_cache = res
    _trend_data_cache_at = time.time()
    return _trend_data_cache

@router.get("/trend")
def get_nps_trend(sort_by: str = "workers", limit: int = 200):
    data = get_trend_data()

    # WLB 총인원 기준 정렬 (sort_by=workers 또는 NPS diff 데이터가 없을 때 fallback)
    if sort_by == "workers":
        # total_workers 기준 내림차순 — WLB 데이터 있는 항목 우선
        sorted_data = sorted(
            data,
            key=lambda x: (x.get('total_workers') is not None, x.get('total_workers') or 0),
            reverse=True
        )
        result = []
        for d in sorted_data[:limit]:
            item = d.copy()
            item.pop('history', None)
            result.append(item)
    elif sort_by in ('1m', '3m', '6m', '1y'):
        # WLB 기간 대비 증감 정렬
        wlb_key = f'wlb_diff_{sort_by}'
        valid_data = [d for d in data if d.get(wlb_key) is not None]
        valid_data.sort(key=lambda x: x.get(wlb_key) or 0, reverse=True)
        if not valid_data:
            # diff 데이터 없으면 피보험자수 기준 전체 제공
            valid_data = sorted(
                data,
                key=lambda x: (x.get('total_workers') is not None, x.get('total_workers') or 0),
                reverse=True
            )
        result = []
        for d in valid_data[:limit]:
            item = d.copy()
            item.pop('history', None)
            result.append(item)
    else:
        sort_key = f'diff_{sort_by}'

        def _has_val(d):
            v = d.get(sort_key)
            if v is None: return False
            try: return not pd.isna(v)
            except: return True
        valid_data = [d for d in data if _has_val(d)]
        valid_data.sort(key=lambda x: (x[sort_key] is not None, x[sort_key] or 0), reverse=True)

        # NPS 데이터가 없으면 WLB 기준으로 전체 제공
        if not valid_data:
            valid_data = sorted(
                data,
                key=lambda x: (x.get('total_workers') is not None, x.get('total_workers') or 0),
                reverse=True
            )

        result = []
        for d in valid_data[:limit]:
            item = d.copy()
            item.pop('history', None)
            result.append(item)
        
    # 최신 수집일자 + WLB 메타
    conn = sqlite3.connect(EMP_DB)
    try:
        r2 = conn.execute("SELECT MAX(fetched_at), MAX(data_ym) FROM wlb_monthly").fetchone()
        updated_at = r2[0][:10] if r2 and r2[0] else None
        wlb_data_ym = r2[1] if r2 else None
    except Exception:
        updated_at = None
        wlb_data_ym = None
    if not updated_at:
        try:
            r2 = conn.execute("SELECT MAX(fetched_at) FROM nps_workplace_monthly").fetchone()
            updated_at = r2[0][:10] if r2 and r2[0] else None
        except Exception:
            updated_at = None
    conn.close()
    if not updated_at:
        from datetime import date as _date
        updated_at = _date.today().isoformat()

    return {
        "rows": result,
        "date": updated_at,
        "wlb_data_ym": wlb_data_ym,
        "has_nps": any(r.get('diff_0m') is not None for r in result),
        "has_wlb": any(r.get('total_workers') is not None for r in result),
    }

@router.get("/insurance")
def get_insurance_employment(limit: int = 200, sort_by: str = "count"):
    """
    고용보험 상시인원 기준 기업별 인원수 (employment_company where bizr_no IS NOT NULL).
    sort_by: count(인원순) | name(이름순)
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    stock_conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    stock_conn.row_factory = sqlite3.Row
    try:
        # 최신 ym의 고용보험 데이터
        latest_ym_row = conn.execute(
            "SELECT MAX(ym) FROM employment_company WHERE bizr_no IS NOT NULL"
        ).fetchone()
        latest_ym = latest_ym_row[0] if latest_ym_row and latest_ym_row[0] else None
        if not latest_ym:
            return {"rows": [], "ym": None}

        rows = conn.execute("""
            SELECT e.ym, e.stock_code, e.stock_name, e.worker_count, e.yoy_change, e.mom_change
            FROM employment_company e
            WHERE e.bizr_no IS NOT NULL AND e.ym = ?
            ORDER BY e.worker_count DESC NULLS LAST
            LIMIT ?
        """, (latest_ym, limit)).fetchall()

        # stock_universe에서 market/sector 보완
        universe = {r['stock_code']: r for r in stock_conn.execute(
            "SELECT stock_code, market, sector_small as sector FROM stock_universe"
        ).fetchall()}

        result = []
        for r in rows:
            d = dict(r)
            meta = universe.get(d['stock_code'])
            d['market'] = dict(meta)['market'] if meta else None
            d['sector'] = dict(meta)['sector'] if meta else None
            result.append(d)

        return {"rows": result, "ym": latest_ym, "count": len(result)}
    finally:
        conn.close()
        stock_conn.close()


@router.get("/insurance/chart")
def get_insurance_chart(code: str = Query(..., description="Stock code")):
    """특정 종목 고용보험 상시인원 월별 추이."""
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT ym, worker_count, yoy_change, mom_change
            FROM employment_company
            WHERE stock_code = ? AND worker_count IS NOT NULL
            ORDER BY ym ASC
        """, (code,)).fetchall()
        if not rows:
            return {"history": [], "notFound": True}
        history = [dict(r) for r in rows]
        return {"stock_code": code, "history": history}
    finally:
        conn.close()


@router.get("/chart")
def get_nps_chart(query: str = Query(..., description="Stock code or name")):
    data = get_trend_data()
    for d in data:
        if d['stock_code'] == query or d['stock_name'] == query:
            return {"company": d['stock_name'], "history": d.get('history', []), "notFound": not d.get('history')}
    for d in data:
        if query in d['stock_name']:
            return {"company": d['stock_name'], "history": d.get('history', []), "notFound": not d.get('history')}
    return {"company": None, "history": [], "notFound": True}


@router.get("/annual-trend")
def get_annual_trend(q: str = Query(..., description="종목명 또는 종목코드 (부분 일치)")):
    """기업별 연간 고용인원 추이 (employment_company, 사업보고서 기준).

    2023-12 / 2024-12 / 2025-12 데이터 포함.
    2026-05(NPS API 기반)는 소스가 달라 별도 표시.
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        # 정확한 코드 일치 우선
        rows = conn.execute("""
            SELECT ym, stock_code, stock_name, worker_count
            FROM employment_company
            WHERE (stock_code = ? OR stock_name LIKE ?)
              AND worker_count IS NOT NULL
              AND ym != '2026-05'
            ORDER BY stock_code, ym ASC
        """, (q, f'%{q}%')).fetchall()

        if not rows:
            return {"results": [], "notFound": True}

        # 종목별로 그룹화
        companies: dict = {}
        for r in rows:
            code = r['stock_code']
            if code not in companies:
                companies[code] = {'stock_code': code, 'stock_name': r['stock_name'], 'history': []}
            companies[code]['history'].append({
                'ym': r['ym'],
                'worker_count': r['worker_count'],
            })

        # 최대 10개 결과
        results = list(companies.values())[:10]
        return {"results": results, "notFound": False}
    finally:
        conn.close()


@router.get("/annual-top")
def get_annual_top(limit: int = 200, sort_by: str = "latest"):
    """고용인원 연간 스냅샷 랭킹 (employment_company 사업보고서 기준).

    sort_by: latest(최신인원순) | growth(증감순) | name(이름순)
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    stock_conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    stock_conn.row_factory = sqlite3.Row
    try:
        # 2025-12과 2024-12 데이터 join하여 증감 계산
        rows = conn.execute("""
            SELECT
                a.stock_code, a.stock_name,
                a.worker_count  AS cnt_2025,
                b.worker_count  AS cnt_2024,
                c.worker_count  AS cnt_2023,
                (a.worker_count - COALESCE(b.worker_count, a.worker_count)) AS diff_1y,
                (a.worker_count - COALESCE(c.worker_count, a.worker_count)) AS diff_2y
            FROM employment_company a
            LEFT JOIN employment_company b
                ON a.stock_code = b.stock_code AND b.ym = '2024-12'
            LEFT JOIN employment_company c
                ON a.stock_code = c.stock_code AND c.ym = '2023-12'
            WHERE a.ym = '2025-12' AND a.worker_count IS NOT NULL
            ORDER BY a.worker_count DESC
            LIMIT ?
        """, (limit,)).fetchall()

        universe = {r['stock_code']: dict(r) for r in stock_conn.execute(
            "SELECT stock_code, market, sector_small as sector FROM stock_universe"
        ).fetchall()}

        result = []
        for r in rows:
            d = dict(r)
            meta = universe.get(d['stock_code'], {})
            d['market'] = meta.get('market')
            d['sector'] = meta.get('sector')
            result.append(d)

        if sort_by == 'growth':
            result.sort(key=lambda x: x.get('diff_1y') or 0, reverse=True)
        elif sort_by == 'name':
            result.sort(key=lambda x: x.get('stock_name') or '')

        return {"rows": result, "count": len(result), "base_ym": "2025-12", "compare_ym": "2024-12"}
    finally:
        conn.close()
        stock_conn.close()
