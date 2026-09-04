import sqlite3
import os
import time
from fastapi import APIRouter, Query

from employment_monitor.data_quality import audit_employment_data

router = APIRouter(prefix="/api/employment-v2", tags=["employment-v2"])

DIR = os.path.dirname(__file__)
EMP_DB = os.path.join(DIR, "employment.db")
STOCK_DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"


@router.get("/quality")
def get_employment_quality():
    """수집 최신성, 키 중복, 산식 및 종목 커버리지 점검 결과."""
    return audit_employment_data(EMP_DB, STOCK_DB)


def _is_preferred_like_name(name: str | None) -> bool:
    """우선주/종류주로 보이는 종목명 판별 (우성 같은 일반 종목 오탐 최소화)."""
    if not name:
        return False
    n = str(name).strip()
    # 일반적으로 접미사 형태로 등장
    suffixes = (
        "우", "우B", "1우", "2우", "2우B", "3우", "3우B", "전환우", "우선주"
    )
    return any(n.endswith(s) for s in suffixes)


def _employment_scope_profile(stock_name: str | None, sector: str | None) -> dict:
    """Describe how insured-worker counts should be interpreted, without invalidating them."""
    name_text = str(stock_name or "").replace(" ", "")
    sector_text = str(sector or "").replace(" ", "")
    text = f"{name_text}{sector_text}"
    if any(token in text for token in ("보험", "손해보험", "생명보험", "재보험")):
        return {
            "type": "insurance_sales_network",
            "label": "보험 영업인력 포함 가능",
            "note": "보험설계사·컨설턴트 등 정규직 외 영업인력이 사업장·피보험자 범위에 포함될 수 있습니다. 값은 유효한 고용 기반 정보이며 직접고용 정규직 수와 동일하게 해석하지 않습니다.",
        }
    if "건설" in text or "토목" in sector_text or sector_text in ("건축", "종합건설"):
        return {
            "type": "project_worksites",
            "label": "현장·프로젝트 인력 포함 가능",
            "note": "현장 개설·종료와 프로젝트 인력 이동이 사업장 및 피보험자 수에 반영될 수 있습니다. 값은 유지하되 수주·현장 변화와 함께 해석합니다.",
        }
    if any(token in text for token in ("유통", "백화점", "마트", "편의점", "물류")):
        return {
            "type": "distributed_worksites",
            "label": "다사업장 운영 특성",
            "note": "점포·물류 거점의 개폐와 운영 인력 범위가 사업장 및 피보험자 수에 반영될 수 있습니다. 값은 유지하되 사업장 수 변화와 함께 해석합니다.",
        }
    return {
        "type": "general",
        "label": "사업장·피보험자 기준",
        "note": "고용보험·국민연금 수치는 사업장 및 피보험자 기준이며 사업보고서의 직접고용 인원과 집계 범위가 다를 수 있습니다.",
    }

@router.get("/yearly")
def get_yearly_employment(limit: int = 9999, sort_by: str = "count"):
    """고용보험 현황 — stock_universe 전체 KOSPI/KOSDAQ 기업 + WLB 데이터 LEFT JOIN.

    sort_by: count(인원수순) | workplace(사업장수순) | name(가나다)
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS stock_db")

        ym_row = conn.execute("SELECT MAX(data_ym) FROM wlb_monthly").fetchone()
        latest_ym = ym_row[0] if ym_row and ym_row[0] else None

        order_col = ("w.total_workers" if sort_by in ("count", "increase")
                     else "w.workplace_cnt" if sort_by == "workplace"
                     else "u.stock_name")

        # stock_universe 전체 보통주 기준 LEFT JOIN → 데이터 없는 종목도 포함
        rows = conn.execute(f"""
            SELECT
                u.stock_code, u.stock_name,
                w.total_workers, w.workplace_cnt,
                w.data_ym,
                u.market,
                u.sector_small AS sector
            FROM stock_db.stock_universe u
            LEFT JOIN wlb_monthly w
                ON u.stock_code = w.stock_code AND w.data_ym = ?
            WHERE u.secugrp_nm = '주권'
            ORDER BY {order_col} DESC NULLS LAST
        """, (latest_ym,)).fetchall()

        meta_row = (conn.execute("SELECT MAX(fetched_at) FROM wlb_monthly WHERE data_ym=?", (latest_ym,)).fetchone()
                    if latest_ym else None)
        updated_at = (meta_row[0][:10] if meta_row and meta_row[0]
                      else (latest_ym[:4] + '-' + latest_ym[4:6] + '-01' if latest_ym else None))

        rep_rows = conn.execute("""
            SELECT stock_code, ym, worker_count
            FROM (
              SELECT stock_code, ym, worker_count,
                     ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY ym DESC) AS rn
              FROM employment_company
              WHERE source='report_annual' AND ym LIKE '%-12' AND worker_count IS NOT NULL
            ) t
            WHERE rn=1
        """).fetchall()
        rep_map = {r["stock_code"]: {"ym": r["ym"], "workers": r["worker_count"]} for r in rep_rows}

        result = []
        for r in rows:
            d = dict(r)
            if _is_preferred_like_name(d.get("stock_name")):
                continue
            rep = rep_map.get(d["stock_code"], {})
            d["report_ym"] = rep.get("ym")
            d["report_workers"] = rep.get("workers")
            d["employment_scope"] = _employment_scope_profile(d.get("stock_name"), d.get("sector"))
            result.append(d)

        total_row = (conn.execute("SELECT SUM(total_workers), SUM(workplace_cnt) FROM wlb_monthly WHERE data_ym=?", (latest_ym,)).fetchone()
                     if latest_ym else (0, 0))

        if not updated_at:
            from datetime import date as _date
            updated_at = _date.today().isoformat()

        returned = result[:max(1, min(int(limit), 9999))]
        return {
            "rows": returned,
            "row_count": len(result),
            "returned_count": len(returned),
            "date": updated_at,
            "data_ym": latest_ym,
            "total_workers": (total_row[0] or 0) if total_row else 0,
            "total_workplaces": (total_row[1] or 0) if total_row else 0,
            "source": "근로복지공단 고용보험",
        }
    finally:
        conn.close()

# 전역 캐시
_trend_data_cache = None
_trend_data_cache_at = 0

def get_trend_data():
    global _trend_data_cache, _trend_data_cache_at
    if _trend_data_cache is not None and (time.time() - _trend_data_cache_at) < 3600:
        return _trend_data_cache

    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
    # 1) universe
    markets = conn.execute(
        "SELECT stock_code, stock_name, market, sector_small as sector "
        "FROM main_db.stock_universe WHERE secugrp_nm='주권'"
    ).fetchall()

    # 2) NPS rows. 모든 종목을 동일한 공개 기준월로 비교한다.
    latest_nps_row = conn.execute("SELECT MAX(data_ym) FROM nps_monthly").fetchone()
    latest_nps_ym = str(latest_nps_row[0]) if latest_nps_row and latest_nps_row[0] else None
    try:
        nps_rows = conn.execute(
            "SELECT data_ym AS ym, stock_code, new_hires AS new_cnt, "
            "terminations AS lost_cnt, net_change "
            "FROM nps_monthly ORDER BY stock_code ASC, data_ym ASC"
        ).fetchall()
    except Exception:
        nps_rows = []

    # 2-1) 사업보고서 기준 인원(연말 12월) 베이스
    report_base_map: dict[str, dict] = {}
    try:
        base_rows = conn.execute(
            "SELECT stock_code, ym, worker_count FROM employment_company "
            "WHERE worker_count IS NOT NULL AND ym LIKE '%-12' "
            "AND (source='report_annual' OR (source IS NULL AND (bizr_no IS NULL OR bizr_no=''))) "
            "ORDER BY stock_code ASC, ym DESC"
        ).fetchall()
        for r in base_rows:
            code = r["stock_code"]
            if code not in report_base_map:
                report_base_map[code] = {"base_ym": r["ym"], "base_workers": int(r["worker_count"])}
    except Exception:
        pass

    # 2-2) 사업자번호 보유 종목 set (한 번만 로딩)
    bizno_code_set: set[str] = set()
    try:
        for r in conn.execute(
            "SELECT DISTINCT stock_code FROM stock_bizr_no_map WHERE bizr_no IS NOT NULL AND LENGTH(bizr_no)=10"
        ).fetchall():
            bizno_code_set.add(r["stock_code"])
    except Exception:
        pass
    try:
        for r in conn.execute(
            "SELECT DISTINCT stock_code FROM stock_bizno_map WHERE biz_no_6 IS NOT NULL AND LENGTH(biz_no_6)=6"
        ).fetchall():
            bizno_code_set.add(r["stock_code"])
    except Exception:
        pass

    # 3. WLB 고용보험 피보험자 (최신 월 스냅샷)
    wlb_latest_map = {}
    wlb_map = {}
    try:
        yms = [
            r[0] for r in conn.execute(
                "SELECT data_ym FROM wlb_monthly GROUP BY data_ym ORDER BY data_ym DESC LIMIT 2"
            ).fetchall()
        ]
        latest_wlb_ym = yms[0] if yms else None
        prev_wlb_ym   = yms[1] if len(yms) > 1 else None

        if latest_wlb_ym:
            for r in conn.execute(
                "SELECT stock_code, total_workers, workplace_cnt FROM wlb_monthly WHERE data_ym=?",
                (latest_wlb_ym,)
            ).fetchall():
                wlb_latest_map[r[0]] = {'total_workers': r[1], 'workplace_cnt': r[2], 'data_ym': latest_wlb_ym}
                wlb_map[r[0]] = wlb_latest_map[r[0]]
    except Exception:
        pass

    def _safe_int(v):
        try:
            return None if v is None else int(v)
        except Exception:
            return None

    def _period_months(end_ym: str, n: int) -> set[str]:
        out = []
        y, m = int(end_ym[:4]), int(end_ym[4:6])
        for _ in range(n):
            out.append(f"{y}{m:02d}")
            m -= 1
            if m == 0:
                y -= 1
                m = 12
        return set(out)

    # 종목별 NPS 버킷 구성 (history는 여기서 만들지 않음)
    nps_by_code: dict[str, list[dict]] = {}
    scope_event_count: dict[str, int] = {}
    code_max_ym: dict[str, str] = {}
    for r in nps_rows:
        code = r["stock_code"]
        scope_event = max(int(r["new_cnt"] or 0), int(r["lost_cnt"] or 0)) >= 2000
        row = {
            "ym": str(r["ym"]),
            "new_cnt": int(r["new_cnt"] or 0),
            "lost_cnt": int(r["lost_cnt"] or 0),
            "net_change": int(r["net_change"] or 0),
            "scope_event": scope_event,
        }
        nps_by_code.setdefault(code, []).append(row)
        if scope_event:
            scope_event_count[code] = scope_event_count.get(code, 0) + 1
        code_max_ym[code] = row["ym"]

    cum_1m: dict[str, int] = {}
    cum_3m: dict[str, int] = {}
    cum_6m: dict[str, int] = {}
    cum_1y: dict[str, int] = {}
    cur_new_map: dict[str, int] = {}
    cur_lost_map: dict[str, int] = {}

    for code, rows in nps_by_code.items():
        row_yms = {row["ym"] for row in rows}
        if not latest_nps_ym or latest_nps_ym not in row_yms:
            continue
        max_ym = latest_nps_ym
        m1 = _period_months(max_ym, 1)
        m3 = _period_months(max_ym, 3)
        m6 = _period_months(max_ym, 6)
        m12 = _period_months(max_ym, 12)
        s1 = s3 = s6 = s12 = 0
        cur = None
        for rr in rows:
            ym = rr["ym"]
            net = rr["net_change"]
            if ym in m1: s1 += net
            if ym in m3: s3 += net
            if ym in m6: s6 += net
            if ym in m12: s12 += net
            if ym == max_ym:
                cur = rr
        # 중간 월이 빠지면 합계를 0으로 간주하지 않고 결측으로 유지한다.
        if m1.issubset(row_yms): cum_1m[code] = s1
        if m3.issubset(row_yms): cum_3m[code] = s3
        if m6.issubset(row_yms): cum_6m[code] = s6
        if m12.issubset(row_yms): cum_1y[code] = s12
        if cur:
            cur_new_map[code] = cur["new_cnt"]
            cur_lost_map[code] = cur["lost_cnt"]

    def _ym_dash_to_yyyymm(v: str | None) -> str | None:
        if not v:
            return None
        s = str(v).strip()
        if len(s) == 7 and s[4] == "-":
            return s[:4] + s[5:7]
        return s if len(s) == 6 else None

    res = []
    for row in markets:
        code = row['stock_code']
        stock_name = row['stock_name']
        # 우선주/종류주는 제외하고 모회사(보통주)만 사용
        if _is_preferred_like_name(stock_name):
            continue

        wlb  = wlb_map.get(code, {})
        base = report_base_map.get(code, {})
        base_ym_dash = base.get("base_ym")
        base_ym = _ym_dash_to_yyyymm(base_ym_dash)
        base_workers = base.get("base_workers")
        ref_ym = latest_nps_ym if code in cum_1m else None

        estimated_workers = None
        estimate_valid = True
        estimate_invalid_reason = None
        comparison_status = "comparable"
        if base_workers is not None and ref_ym and code in nps_by_code:
            est = int(base_workers)
            for rr in nps_by_code[code]:
                ym = rr["ym"]
                if base_ym and ym > base_ym and ym <= ref_ym:
                    est += int(rr["net_change"] or 0)
            estimated_workers = est

        wlb_vs_est_pct = None
        if estimated_workers not in (None, 0) and wlb.get("total_workers") not in (None, 0):
            ratio = float(wlb.get("total_workers")) / float(estimated_workers)
            pct_gap = abs((float(wlb.get("total_workers")) - float(estimated_workers)) / float(estimated_workers)) * 100.0
            wlb_vs_est_pct = round(((wlb.get("total_workers") - estimated_workers) / estimated_workers) * 100.0, 2)
            # 큰 괴리는 오류가 아니라 집계 범위·사업모델 차이일 수 있으므로 값을 유지한다.
            if ratio >= 2.0 or ratio <= 0.5 or pct_gap >= 80.0:
                comparison_status = "scope_difference"

        # 사업자번호가 전혀 없거나, 사업자번호는 있는데 최신 WLB가 없는 종목은 제외
        has_any_bizno = (code in bizno_code_set) or (code in report_base_map)

        if not has_any_bizno:
            continue
        if wlb.get("total_workers") is None:
            continue

        res.append({
            'stock_code':      code,
            'stock_name':      stock_name,
            'market':          row['market'],
            'sector':          row['sector'],
            'total_workers':   wlb.get('total_workers'),
            'workplace_cnt':   wlb.get('workplace_cnt'),
            'wlb_data_ym':     wlb.get('data_ym'),
            'base_report_ym':  base_ym_dash,
            'base_report_workers': base_workers,
            'estimated_workers': estimated_workers,
            'wlb_vs_est_pct':  wlb_vs_est_pct,
            'extra_vs_report': (wlb.get("total_workers") - base_workers) if (wlb.get("total_workers") is not None and base_workers is not None) else None,
            'estimate_valid': estimate_valid,
            'estimate_invalid_reason': estimate_invalid_reason,
            'comparison_status': comparison_status,
            'data_valid': True,
            'employment_scope': _employment_scope_profile(stock_name, row['sector']),
            'scope_event_count': scope_event_count.get(code, 0),
            'display_diff_1m': _safe_int(cum_1m.get(code)),
            'display_diff_3m': _safe_int(cum_3m.get(code)),
            'display_diff_6m': _safe_int(cum_6m.get(code)),
            'display_diff_1y': _safe_int(cum_1y.get(code)),
            'new_0m':  _safe_int(cur_new_map.get(code)),
            'lost_0m': _safe_int(cur_lost_map.get(code)),
            'diff_0m': _safe_int(
                (cur_new_map.get(code) or 0) - (cur_lost_map.get(code) or 0)
                if cur_new_map.get(code) is not None else None
            ),
            'nps_ref_ym': ref_ym
        })

    conn.close()
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
        # WLB 기간 대비 증감 기준 정렬 — 증가(양수) 먼저↓, 감소(음수) 다음↑, NULL 마지막
        sort_key = f'display_diff_{sort_by}'
        valid_count = sum(1 for d in data if d.get(sort_key) is not None)
        # 50개 이상 기업에 유효 데이터가 있을 때만 해당 기간 정렬 의미 있음
        if valid_count < 50:
            # 데이터 부족 → total_workers 내림차순으로 fallback
            sorted_data = sorted(
                data,
                key=lambda x: (x.get('total_workers') is not None, x.get('total_workers') or 0),
                reverse=True
            )
        else:
            def _sort_key_period(x):
                v = x.get(sort_key)
                if v is None:
                    return (2, 0)      # null → 마지막
                if v >= 0:
                    return (0, -v)     # 증가 → 앞 (큰 값 먼저)
                return (1, -v)         # 감소 → 중간 (절댓값 작은 것 먼저)
            sorted_data = sorted(data, key=_sort_key_period)
        result = []
        for d in sorted_data[:limit]:
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
        
    # 메타 정보
    conn = sqlite3.connect(EMP_DB)
    try:
        r2 = conn.execute(
            "SELECT data_ym, MAX(fetched_at) FROM wlb_monthly GROUP BY data_ym ORDER BY data_ym DESC LIMIT 1"
        ).fetchone()
        updated_at = r2[1][:10] if r2 and r2[1] else None
        wlb_data_ym = r2[0] if r2 else None
        nps_data_ym = conn.execute("SELECT MAX(data_ym) FROM nps_monthly").fetchone()[0]
    except Exception:
        updated_at = wlb_data_ym = nps_data_ym = None
    conn.close()
    if not updated_at:
        from datetime import date as _date
        updated_at = _date.today().isoformat()

    # nps_ref_ym: 기간 탭 기준 월 (result 첫 번째 항목에서 추출)
    nps_ref_ym = nps_data_ym if any(r.get('display_diff_1m') is not None for r in result) else None

    return {
        "rows": result,
        "date": updated_at,
        "wlb_data_ym": wlb_data_ym,
        "nps_data_ym": nps_data_ym,
        "nps_ref_ym": nps_ref_ym,
        "has_nps": any(r.get('display_diff_1m') is not None for r in result),
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
            "SELECT MAX(ym) FROM employment_company "
            "WHERE (source='insurance_api' OR bizr_no IS NOT NULL)"
        ).fetchone()
        latest_ym = latest_ym_row[0] if latest_ym_row and latest_ym_row[0] else None
        if not latest_ym:
            return {"rows": [], "ym": None}

        rows = conn.execute("""
            SELECT e.ym, e.stock_code, e.stock_name, e.worker_count, e.yoy_change, e.mom_change
            FROM employment_company e
            WHERE (e.source='insurance_api' OR e.bizr_no IS NOT NULL) AND e.ym = ?
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
    """
    기업별 피보험자 월별 추이.
    WLB 실측(202505, 202605) + NPS 월별 net_change 누적으로 중간 월 추정.
    큰 취득·상실도 원값을 유지하고 집계 범위 변화 가능성으로 표시한다.
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        # 1. WLB 스냅샷 (실측 기준점: 최신 월)
        wlb_rows = conn.execute("""
            SELECT data_ym AS ym, total_workers AS worker_count
            FROM wlb_monthly
            WHERE stock_code = ? AND total_workers IS NOT NULL AND total_workers > 0
            ORDER BY data_ym DESC
        """, (code,)).fetchall()
        wlb_map = {r['ym']: r['worker_count'] for r in wlb_rows}

        # 2. NPS 월별 신규/상실 (국민연금)
        nps_rows = conn.execute("""
            SELECT data_ym AS ym, new_hires, terminations, net_change
            FROM nps_monthly
            WHERE stock_code = ?
            ORDER BY data_ym ASC
        """, (code,)).fetchall()

        # 3. 26-05(최신 WLB) 기준으로 역추정: 25-04까지 back-cast
        def _prev_ym(ym: str) -> str:
            y, m = int(ym[:4]), int(ym[4:6])
            m -= 1
            if m == 0:
                y -= 1
                m = 12
            return f"{y}{m:02d}"

        def _ym_range(start_ym: str, end_ym: str) -> list[str]:
            out = []
            cur = start_ym
            while cur <= end_ym:
                out.append(cur)
                y, m = int(cur[:4]), int(cur[4:6])
                m += 1
                if m == 13:
                    y += 1
                    m = 1
                cur = f"{y}{m:02d}"
            return out

        history = []
        anchor_ym = max(wlb_map.keys()) if wlb_map else None
        anchor_workers = wlb_map.get(anchor_ym) if anchor_ym else None

        if anchor_ym and anchor_workers:
            nps_map = {r['ym']: {'new_hires': r['new_hires'], 'terminations': r['terminations'], 'net_change': r['net_change']} for r in nps_rows}
            start_ym = "202504"
            months = _ym_range(start_ym, anchor_ym)
            workers_map = {anchor_ym: int(anchor_workers)}
            assumed_zero = []

            cur = anchor_ym
            while cur > start_ym:
                prev = _prev_ym(cur)
                net = nps_map.get(cur, {}).get('net_change')
                if net is None:
                    net = 0
                    assumed_zero.append(cur)
                workers_map[prev] = int(workers_map[cur] - net)
                cur = prev

            prev_workers = None
            for ym in months:
                w = workers_map.get(ym)
                nps = nps_map.get(ym, {})
                mom = round((w - prev_workers) / prev_workers * 100, 1) if (prev_workers not in (None, 0) and w is not None) else None
                is_actual = (ym == anchor_ym)
                history.append({
                    'ym': ym,
                    'worker_count': w,
                    'new_hires': nps.get('new_hires'),
                    'terminations': nps.get('terminations'),
                    'net_change': nps.get('net_change'),
                    'mom_change': mom,
                    'is_actual': is_actual,
                    'assumed_zero': ym in assumed_zero,
                    'scope_event': bool(
                        anchor_workers
                        and max(int(nps.get('new_hires') or 0), int(nps.get('terminations') or 0)) > anchor_workers * 0.5
                    ),
                })
                prev_workers = w
        elif nps_rows:
            # WLB 없으면 employment_company fallback
            ec_rows = conn.execute("""
                SELECT ym, worker_count, mom_change FROM employment_company
                WHERE stock_code = ? AND worker_count IS NOT NULL ORDER BY ym ASC
            """, (code,)).fetchall()
            history = [dict(r) for r in ec_rows]

        if not history:
            return {"history": [], "notFound": True}

        # 종목명 조회
        name_row = conn.execute(
            "SELECT stock_name FROM wlb_monthly WHERE stock_code = ? LIMIT 1", (code,)
        ).fetchone()
        stock_name = name_row['stock_name'] if name_row else code
        stock_conn = sqlite3.connect(STOCK_DB)
        stock_conn.row_factory = sqlite3.Row
        stock_meta = stock_conn.execute(
            "SELECT stock_name, sector_small AS sector FROM stock_universe WHERE stock_code=? LIMIT 1",
            (code,),
        ).fetchone()
        stock_conn.close()
        if stock_meta:
            stock_name = stock_meta['stock_name'] or stock_name

        report_row = conn.execute(
            "SELECT ym, worker_count FROM employment_company "
            "WHERE stock_code=? AND source='report_annual' AND ym LIKE '%-12' "
            "ORDER BY ym DESC LIMIT 1",
            (code,),
        ).fetchone()

        return {
            "stock_code": code,
            "stock_name": stock_name,
            "history": history,
            "source": "wlb+nps",
            "base_ym": anchor_ym,
            "base_workers": anchor_workers,
            "report_ym": report_row["ym"] if report_row else None,
            "report_workers": report_row["worker_count"] if report_row else None,
            "employment_scope": _employment_scope_profile(
                stock_name, stock_meta['sector'] if stock_meta else None
            ),
        }
    finally:
        conn.close()


@router.get("/chart")
def get_nps_chart(query: str = Query(..., description="Stock code or name")):
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS stock_db")
        row = conn.execute(
            "SELECT stock_code, stock_name, sector_small AS sector FROM stock_db.stock_universe "
            "WHERE stock_code=? OR stock_name=? LIMIT 1",
            (query, query),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT stock_code, stock_name, sector_small AS sector FROM stock_db.stock_universe "
                "WHERE stock_name LIKE ? ORDER BY stock_name LIMIT 1",
                (f"%{query}%",),
            ).fetchone()
        if not row:
            return {"company": None, "history": [], "notFound": True}

        code = row["stock_code"]
        hist_rows = conn.execute(
            "SELECT data_ym, new_hires, terminations, net_change "
            "FROM nps_monthly WHERE stock_code=? ORDER BY data_ym ASC",
            (code,),
        ).fetchall()
        history = [
            {
                "month": f"{str(r['data_ym'])[:4]}-{str(r['data_ym'])[4:]}",
                "new_cnt": int(r["new_hires"] or 0),
                "lost_cnt": int(r["terminations"] or 0),
                "net_change": int(r["net_change"] or 0),
            }
            for r in hist_rows
        ]
        portal_history = []
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nps_portal_monthly'"
        ).fetchone():
            portal_rows = conn.execute(
                "SELECT data_ym,new_hires,terminations,net_change,subscriber_count,workplace_count "
                "FROM nps_portal_monthly WHERE stock_code=? ORDER BY data_ym ASC",
                (code,),
            ).fetchall()
            previous_workplaces = None
            for r in portal_rows:
                workplace_count = int(r["workplace_count"] or 0)
                scope_break = bool(
                    previous_workplaces
                    and workplace_count
                    and (workplace_count > previous_workplaces * 2 or previous_workplaces > workplace_count * 2)
                )
                portal_history.append({
                    "month": f"{str(r['data_ym'])[:4]}-{str(r['data_ym'])[4:]}",
                    "new_cnt": int(r["new_hires"] or 0),
                    "lost_cnt": int(r["terminations"] or 0),
                    "net_change": int(r["net_change"] or 0),
                    "subscriber_count": int(r["subscriber_count"] or 0),
                    "workplace_count": workplace_count,
                    "scope_break": scope_break,
                    "scope_status": "aggregation_scope_changed" if scope_break else "stable",
                    "data_valid": True,
                })
                previous_workplaces = workplace_count or previous_workplaces
        return {
            "company": row["stock_name"],
            "history": history,
            "portal_history": portal_history,
            "portal_source": "국민연금공단 월별 사업장 원본(동일 기업 사업장 합산)",
            "portal_usage": "모든 값을 표시합니다. scope_break는 무효가 아니라 집계 범위 또는 사업모델 변화 표식이며 업종 특성과 함께 해석합니다.",
            "employment_scope": _employment_scope_profile(row["stock_name"], row["sector"]),
            "notFound": not history and not portal_history,
        }
    finally:
        conn.close()


@router.get("/annual-trend")
def get_annual_trend(q: str = Query(..., description="종목명 또는 종목코드 (부분 일치)")):
    """기업별 연간 고용인원 추이 — stock_universe에서 검색 후 employment_company JOIN."""
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS stock_db")

        # 1. stock_universe에서 전체 보통주 검색 (데이터 없는 종목도 포함)
        companies = conn.execute("""
            SELECT stock_code, stock_name, sector_small AS sector FROM stock_db.stock_universe
            WHERE secugrp_nm = '주권'
              AND (stock_code = ? OR stock_name LIKE ?)
            LIMIT 10
        """, (q, f'%{q}%')).fetchall()

        if not companies:
            return {"results": [], "notFound": True}

        results = []
        for c in companies:
            code, name = c['stock_code'], c['stock_name']
            emp_rows = conn.execute("""
                SELECT ym, worker_count FROM employment_company
                WHERE stock_code = ? AND worker_count IS NOT NULL AND ym != '2026-05'
                ORDER BY ym ASC
            """, (code,)).fetchall()
            history = [{'ym': r['ym'], 'worker_count': r['worker_count']} for r in emp_rows]
            official_history = []
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wlb_portal_annual'"
            ).fetchone():
                portal_rows = conn.execute(
                    "SELECT data_year,total_workers,workplace_count,source_rows,identity_matched_rows "
                    "FROM wlb_portal_annual WHERE stock_code=? ORDER BY data_year ASC",
                    (code,),
                ).fetchall()
                official_history = [
                    {
                        'year': r['data_year'],
                        'worker_count': r['total_workers'],
                        'workplace_count': r['workplace_count'],
                        'match_quality': (
                            'exact_biz_no' if not r['identity_matched_rows']
                            else 'identity_crosswalk' if r['identity_matched_rows'] == r['source_rows']
                            else 'mixed'
                        ),
                        'data_valid': True,
                    }
                    for r in portal_rows
                ]
            results.append({
                'stock_code': code,
                'stock_name': name,
                'history': history,
                'official_history': official_history,
                'official_source': '근로복지공단 연간 고용보험 가입 사업장 원본',
                'employment_scope': _employment_scope_profile(name, c['sector']),
                'matching_note': 'match_quality는 원천 연결 방식이며 값의 유효/무효 판정이 아닙니다.',
            })

        return {"results": results, "notFound": False}
    finally:
        conn.close()


@router.get("/annual-top")
def get_annual_top(limit: int = 9999, sort_by: str = "latest"):
    """고용인원 연간 스냅샷 랭킹 — stock_universe 전체 보통주 LEFT JOIN.

    sort_by: latest(최신인원순) | growth(증감순) | name(이름순)
    """
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS stock_db")

        rows = conn.execute("""
            SELECT
                u.stock_code, u.stock_name, u.market, u.sector_small AS sector,
                a.worker_count AS cnt_2025,
                b.worker_count AS cnt_2024,
                c.worker_count AS cnt_2023,
                (a.worker_count - COALESCE(b.worker_count, a.worker_count)) AS diff_1y,
                (a.worker_count - COALESCE(c.worker_count, a.worker_count)) AS diff_2y
            FROM stock_db.stock_universe u
            LEFT JOIN employment_company a ON u.stock_code = a.stock_code AND a.ym = '2025-12'
            LEFT JOIN employment_company b ON u.stock_code = b.stock_code AND b.ym = '2024-12'
            LEFT JOIN employment_company c ON u.stock_code = c.stock_code AND c.ym = '2023-12'
            WHERE u.secugrp_nm = '주권'
            ORDER BY a.worker_count DESC NULLS LAST
        """).fetchall()

        result = [dict(r) for r in rows]

        if sort_by == 'growth':
            result.sort(key=lambda x: x.get('diff_1y') or 0, reverse=True)
        elif sort_by == 'name':
            result.sort(key=lambda x: x.get('stock_name') or '')

        return {"rows": result, "count": len(result), "base_ym": "2025-12", "compare_ym": "2024-12"}
    finally:
        conn.close()
