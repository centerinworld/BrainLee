from sqlalchemy.orm import Session
import models
from datetime import datetime, timedelta
import json
import urllib.parse
import xml.etree.ElementTree as ET
import config
from services.gemini_openai_compat import OpenAI

_VALID_YEAR_MIN = 2000
_VALID_YEAR_MAX = 2030
_VALID_QUARTERS = {1, 2, 3, 4}
_QUARTER_TO_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
_MONTH_TO_Q_LABEL = {'03': '1Q', '06': '2Q', '09': '3Q', '12': '4Q'}
_TREASURY_BRIEF_CACHE = {"date": "", "text": ""}
_BROAD_INDEX_SYMBOLS = {"^IXIC", "^GSPC", "^KS11", "^KQ11", "^KS200", "^KQ150"}


def _is_broad_index_outlier(symbol: str, close: float | None, prev_close: float | None) -> bool:
    if symbol not in _BROAD_INDEX_SYMBOLS or close is None or prev_close is None or prev_close <= 0:
        return False
    try:
        return abs((float(close) / float(prev_close) - 1.0) * 100.0) >= 20.0
    except Exception:
        return False


def _filter_broad_index_history(symbol: str, items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if symbol not in _BROAD_INDEX_SYMBOLS:
        return items
    filtered: list[tuple[str, float]] = []
    prev_close: float | None = None
    for date_str, close in sorted(items):
        if close is None or close <= 0:
            continue
        if _is_broad_index_outlier(symbol, close, prev_close):
            continue
        filtered.append((date_str, close))
        prev_close = float(close)
    return filtered


def _fmt_q_period(raw_period: str) -> str:
    """'2025.03' → '25년1Q', '2025.12' → '25년4Q'"""
    if len(raw_period) < 7:
        return raw_period
    yr = raw_period[:4]
    mo = raw_period[5:7]
    return f"{yr[2:]}년{_MONTH_TO_Q_LABEL.get(mo, mo)}"


def _fmt_annual_period(raw_period: str) -> str:
    """'2025.12' → '2025년'"""
    return f"{raw_period[:4]}년"


def _sanitize_balance_sheet(assets, liabilities, equity):
    """
    재무상태표 이상값 보정.
    DART 수집 시 계정명 매핑 오류로 부채·자본 항목에 자산 값이 그대로 저장되는 경우 수정.
    - 부채 ≈ 자산 : 부채 = 자산 - 자본 (자본이 정상일 때)
    - 자본 ≈ 자산 : 자본 = 자산 - 부채 (부채가 정상일 때)
    - 자본 < 자산의 1% : 자본금이 잘못 매핑된 것으로 판단 → 자산 - 부채로 재계산
    반환: (assets, liabilities, equity)
    """
    if not assets or assets <= 0:
        return assets, liabilities, equity

    tol = assets * 0.001  # 0.1% 허용 오차

    liab_eq_asset  = liabilities is not None and abs(liabilities - assets) < tol
    eq_eq_asset    = equity     is not None and abs(equity     - assets) < tol
    eq_too_small   = equity     is not None and 0 < equity < assets * 0.01

    # 자본 = 자산 (잘못 저장) → 자산 - 부채로 보정
    if eq_eq_asset:
        if liabilities is not None and liabilities > 0 and not liab_eq_asset:
            equity = assets - liabilities
        else:
            equity = None

    # 부채 = 자산 (잘못 저장) → 자산 - 자본으로 보정
    if liab_eq_asset:
        if equity is not None and equity > 0 and not eq_eq_asset:
            liabilities = assets - equity
        else:
            liabilities = None

    # 자본이 너무 작으면 (자본금이 잘못 매핑된 케이스) → 자산 - 부채
    if eq_too_small and liabilities is not None and liabilities > 0:
        computed = assets - liabilities
        if computed > assets * 0.01:  # 계산 결과가 합리적일 때만
            equity = computed

    return assets, liabilities, equity


def _to_uk(val) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val) / 1e8, 0)
    except (TypeError, ValueError):
        return None


def _is_valid_record(d) -> bool:
    if d.year is None or d.quarter is None:
        return False
    if not (_VALID_YEAR_MIN <= d.year <= _VALID_YEAR_MAX):
        return False
    if d.is_annual:
        return d.quarter in {0, 4}
    return d.quarter in _VALID_QUARTERS


def get_chart_data(db: Session, stock_code: str, days: int = 30):
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    prices = (
        db.query(models.PriceHistory)
        .filter(
            models.PriceHistory.stock_code == stock_code,
            models.PriceHistory.date >= start_date,
        )
        .order_by(models.PriceHistory.date.asc())
        .all()
    )
    seen = {}
    for p in prices:
        date_str = p.date.strftime("%Y-%m-%d") if hasattr(p.date,"strftime") else str(p.date)[:10]
        try:
            if datetime.strptime(date_str, "%Y-%m-%d").weekday() >= 5:
                continue  # 토/일 제외
        except Exception:
            pass
        seen[date_str] = p
    if stock_code in _BROAD_INDEX_SYMBOLS:
        allowed = {k for k, _ in _filter_broad_index_history(stock_code, [(k, seen[k].close) for k in seen])}
        seen = {k: v for k, v in seen.items() if k in allowed}
    return [
        {
            "date":              k,
            "open":              seen[k].open  or seen[k].close,
            "high":              seen[k].high  or seen[k].close,
            "low":               seen[k].low   or seen[k].close,
            "close":             seen[k].close,
            "volume":            seen[k].volume or 0,
            "inst_net_buy":      seen[k].inst_net_buy     or 0.0,
            "frn_net_buy":       seen[k].frn_net_buy      or 0.0,
            "ind_net_buy":       getattr(seen[k], 'ind_net_buy', 0) or 0.0,
            "inst_net_buy_amt":  getattr(seen[k], 'inst_net_buy_amt', 0) or 0.0,
            "frn_net_buy_amt":   getattr(seen[k], 'frn_net_buy_amt', 0) or 0.0,
            "ind_net_buy_amt":   getattr(seen[k], 'ind_net_buy_amt', 0) or 0.0,
        }
        for k in sorted(seen.keys())
    ]


def _calc_eps(eps, net_income, shares_issued):
    """EPS가 0.0이거나 None인데 net_income이 실제값이면 계산으로 보완."""
    if eps is not None and eps != 0.0:
        return eps
    if net_income is not None and net_income != 0 and shares_issued and shares_issued > 0:
        return round(net_income / shares_issued, 2)
    return eps


def get_financial_summary(db: Session, stock_code: str, data_type: str = "annual", report_type: str = "CFS"):
    # 주식수 + stock_collection_config 로드
    import sqlite3 as _sl3
    import sqlalchemy as _sa
    _shares_issued = None
    _preferred_report_type = None
    try:
        _conn = _sl3.connect("stock.db")
        _su = _conn.execute(
            "SELECT shares_issued FROM stock_universe WHERE stock_code=?", (stock_code,)
        ).fetchone()
        _shares_issued = _su[0] if _su and _su[0] else None
        _cfg = _conn.execute(
            "SELECT config_value FROM stock_collection_config WHERE stock_code=? AND config_key='preferred_report_type'",
            (stock_code,)
        ).fetchone()
        _preferred_report_type = _cfg[0] if _cfg else None
        _conn.close()
    except Exception:
        pass

    # report_type 필터 조건 (API 파라미터 우선)
    if report_type == "OFS":
        _rt_filter = models.FinancialData.report_type == "OFS"
    else:  # CFS 또는 기본값
        _rt_filter = _sa.or_(
            models.FinancialData.report_type == "CFS",
            models.FinancialData.report_type.is_(None),
        )

    if data_type == "quarter":
        data = (
            db.query(models.FinancialData)
            .filter(
                models.FinancialData.stock_code == stock_code,
                models.FinancialData.is_annual.is_(False),
                _rt_filter,
            )
            .order_by(models.FinancialData.year.desc(), models.FinancialData.quarter.desc())
            .all()
        )
        result = []
        for d in data:
            if not _is_valid_record(d):
                continue
            q_label = f"{d.year}.{_QUARTER_TO_MONTH.get(d.quarter, 0):02d}"
            if ".00" in q_label:
                continue
            opm = (d.operating_profit / d.revenue * 100) \
                if (d.revenue and d.revenue != 0 and d.operating_profit is not None) else 0.0
            q_assets, q_liab, q_equity = _sanitize_balance_sheet(
                d.total_assets, d.total_liabilities, d.total_equity)
            result.append({
                "period":      q_label,
                "revenue":     _to_uk(d.revenue),
                "op_profit":   _to_uk(d.operating_profit),
                "net_income":  _to_uk(d.net_income),
                "opm":         round(opm, 1),
                "assets":      _to_uk(q_assets),
                "liabilities": _to_uk(q_liab),
                "equity":      _to_uk(q_equity),
                "capital":     _to_uk(d.capital_stock),
                "eps":         _calc_eps(d.eps, d.net_income, _shares_issued),
                "bps": d.bps, "dps": d.dps,
            })
        result.reverse()

        # ── Q4 자동 보완: DART는 Q4를 사업보고서(연간)에만 포함하므로
        #    Q1+Q2+Q3가 있는데 Q4가 없는 연도는 연간 - (Q1+Q2+Q3)로 계산 ──
        existing = {}  # {year: set(month_ints)}
        for r in result:
            y = int(r['period'][:4])
            m = int(r['period'][5:7])
            existing.setdefault(y, set()).add(m)

        years_missing_q4 = [
            y for y, months in existing.items()
            if {3, 6, 9}.issubset(months) and 12 not in months
        ]

        for year in sorted(years_missing_q4):
            annual_rec = (
                db.query(models.FinancialData)
                .filter(
                    models.FinancialData.stock_code == stock_code,
                    models.FinancialData.year == year,
                    models.FinancialData.is_annual.is_(True),
                    _rt_filter,
                )
                .order_by(models.FinancialData.quarter.desc())
                .first()
            )
            if not annual_rec:
                continue

            q123 = [r for r in result
                    if int(r['period'][:4]) == year and r['period'][5:7] in ('03', '06', '09')]

            def _saf(key):
                vals = [r[key] for r in q123 if r.get(key) is not None]
                return sum(vals) if vals else None

            rev_a  = _to_uk(annual_rec.revenue)
            opf_a  = _to_uk(annual_rec.operating_profit)
            ni_a   = _to_uk(annual_rec.net_income)
            rev123 = _saf('revenue');  opf123 = _saf('op_profit');  ni123 = _saf('net_income')

            # 억원 단위로 계산 후 원 단위로 역산 (DB 저장용)
            _M = 1e8  # 억원 → 원
            q4_rev = (rev_a - rev123) if (rev_a is not None and rev123 is not None) else rev_a
            q4_opf = (opf_a - opf123) if (opf_a is not None and opf123 is not None) else opf_a
            q4_ni  = (ni_a  - ni123)  if (ni_a  is not None and ni123  is not None) else ni_a

            opm = (q4_opf / q4_rev * 100) if (q4_rev and q4_rev != 0 and q4_opf is not None) else 0.0
            ann_a, ann_l, ann_e = _sanitize_balance_sheet(
                annual_rec.total_assets, annual_rec.total_liabilities, annual_rec.total_equity)

            # Q4는 표시 전용 계산값. 조회 API에서 DB 쓰기 금지(락 경합 방지).
            # Q4 영속화가 필요하면 scripts/batch_compute_q4.py 별도 실행.

            result.append({
                "period":      f"{year}.12",
                "revenue":     q4_rev,
                "op_profit":   q4_opf,
                "net_income":  q4_ni,
                "opm":         round(opm, 1),
                "assets":      _to_uk(ann_a),
                "liabilities": _to_uk(ann_l),
                "equity":      _to_uk(ann_e),
                "capital":     _to_uk(annual_rec.capital_stock),
                "eps":         _calc_eps(annual_rec.eps, annual_rec.net_income, _shares_issued),
                "bps":         annual_rec.bps,
                "dps":         annual_rec.dps,
            })

        result.sort(key=lambda x: x['period'])
        for r in result:
            r['period'] = _fmt_q_period(r['period'])
        return result

    # ── 연간 데이터 ──────────────────────────────────────────────
    _DS_PRIORITY = {'dart': 0, 'fnguide': 1}  # DART 최우선 (EPS 정확성)
    annual_data_raw = (
        db.query(models.FinancialData)
        .filter(
            models.FinancialData.stock_code == stock_code,
            models.FinancialData.is_annual.is_(True),
            _rt_filter,
        )
        .order_by(models.FinancialData.year.desc(), models.FinancialData.revenue.desc())
        .all()
    )
    # DART 우선 정렬 (EPS 정확성, FnGuide EPS 단위 오류 회피)
    annual_data_raw = sorted(
        annual_data_raw,
        key=lambda x: (
            -x.year,
            0 if _preferred_report_type and x.report_type == _preferred_report_type else 1,
            _DS_PRIORITY.get(x.data_source or 'other', 2),
            -(x.revenue or 0)
        )
    )
    # 같은 연도 첫 번째 레코드 선택 (DART 우선, 이후 NULL 필드 보완)
    _year_recs: dict = {}
    _year_all: dict = {}  # 연도별 모든 행 보관 (revenue 이상치 보정용)
    for _d in annual_data_raw:
        _year_all.setdefault(_d.year, []).append(_d)
        if _d.year not in _year_recs:
            _year_recs[_d.year] = _d
        else:
            # NULL 필드는 다른 row에서 보완 — FnGuide가 가진 D&A 등 추가 필드
            _base = _year_recs[_d.year]
            for _col in ('total_liabilities', 'capital_stock', 'eps', 'bps', 'dps',
                         'total_assets', 'total_equity', 'roe', 'operating_profit', 'net_income',
                         'depreciation_amortization', 'revenue'):
                if getattr(_base, _col, None) is None and getattr(_d, _col, None) is not None:
                    setattr(_base, _col, getattr(_d, _col))

    # DART revenue 이상치 보정: FnGuide 대비 100배+ 차이 시 FnGuide 값 사용
    for _yr, _base in _year_recs.items():
        _base_rev = getattr(_base, 'revenue', None)
        if not _base_rev or _base_rev < 0:
            continue
        for _alt in _year_all.get(_yr, []):
            if _alt is _base:
                continue
            _alt_rev = getattr(_alt, 'revenue', None)
            if not _alt_rev or _alt_rev <= 0:
                continue
            _ratio = max(_base_rev, _alt_rev) / min(_base_rev, _alt_rev)
            if _ratio >= 50:  # 50배 이상 차이 → FnGuide 쪽 신뢰
                _base.revenue = _alt_rev
                break

    annual_data = [_year_recs[y] for y in sorted(_year_recs.keys(), reverse=True)]

    # is_annual=True 레코드가 없으면 분기 데이터로 대체 (OFS 명시 요청 시는 빈 배열 반환)
    if not annual_data:
        if report_type == "OFS":
            return []  # 별도재무제표 없음
        annual_data = (
            db.query(models.FinancialData)
            .filter(models.FinancialData.stock_code == stock_code,
                    models.FinancialData.is_annual.is_(False))
            .order_by(models.FinancialData.year.desc(), models.FinancialData.quarter.desc())
            .limit(8).all()
        )
        result = []
        for d in annual_data:
            if not _is_valid_record(d):
                continue
            q_label = f"{d.year}.{_QUARTER_TO_MONTH.get(d.quarter, 0):02d}"
            if ".00" in q_label:
                continue
            opm = (d.operating_profit / d.revenue * 100) \
                if (d.revenue and d.revenue != 0 and d.operating_profit is not None) else 0.0
            fb_assets, fb_liab, fb_equity = _sanitize_balance_sheet(
                d.total_assets, d.total_liabilities, d.total_equity)
            result.append({
                "period":      q_label,
                "revenue":     _to_uk(d.revenue),
                "op_profit":   _to_uk(d.operating_profit),
                "net_income":  _to_uk(d.net_income),
                "opm":         round(opm, 1),
                "assets":      _to_uk(fb_assets),
                "liabilities": _to_uk(fb_liab),
                "equity":      _to_uk(fb_equity),
                "capital":     _to_uk(d.capital_stock),
                "eps":         _calc_eps(d.eps, d.net_income, _shares_issued),
                "bps": d.bps, "dps": d.dps,
            })
        result.reverse()
        for r in result:
            r['period'] = _fmt_q_period(r['period'])
        return result

    # ── 연간 레코드의 정합성 검증: 연간값이 단일 분기 수준이면 분기합으로 보정 ──
    # DART 수집 버그 등으로 is_annual=True에 분기값이 저장되는 경우 대응
    years_to_fix = set()
    for d in annual_data:
        if not d.revenue or d.revenue <= 0:
            years_to_fix.add(d.year)
            continue
        # 같은 연도 분기 레코드의 최대 revenue 확인
        max_q_rev = db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == stock_code,
            models.FinancialData.year == d.year,
            models.FinancialData.is_annual.is_(False),
            models.FinancialData.revenue > 0,
        ).order_by(models.FinancialData.revenue.desc()).first()
        if max_q_rev and d.revenue <= max_q_rev.revenue * 1.05:
            # 연간값이 분기 최대값과 거의 같으면(5% 이내)만 오류로 판단
            # 1.8배 기준은 삼천리·한국전력 등 계절성 기업을 오판함
            years_to_fix.add(d.year)

    # 보정이 필요한 연도의 분기 데이터를 한 번에 로드
    q_by_year: dict[int, list] = {}
    if years_to_fix:
        q_rows = db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == stock_code,
            models.FinancialData.year.in_(years_to_fix),
            models.FinancialData.is_annual.is_(False),
        ).all()
        for q in q_rows:
            q_by_year.setdefault(q.year, []).append(q)

    result = []
    for d in annual_data:
        if not _is_valid_record(d):
            continue

        if d.year in years_to_fix and d.year in q_by_year:
            # 분기합으로 손익계산서 항목 보정 (누계)
            qs = q_by_year[d.year]
            rev  = sum((q.revenue           or 0) for q in qs)
            opf  = sum((q.operating_profit   or 0) for q in qs)
            ni   = sum((q.net_income         or 0) for q in qs)
            # 재무상태표는 기말 잔액 (연간 레코드 우선, 없으면 분기 Q4→Q3 순으로 fallback)
            q_sorted = sorted(qs, key=lambda x: x.quarter, reverse=True)
            latest_q = q_sorted[0] if q_sorted else None
            assets = d.total_assets      or (latest_q.total_assets      if latest_q else None)
            liab   = d.total_liabilities or (latest_q.total_liabilities if latest_q else None)
            equity = d.total_equity      or (latest_q.total_equity      if latest_q else None)
            cap    = d.capital_stock     or (latest_q.capital_stock     if latest_q else None)
            eps    = d.eps or (latest_q.eps if latest_q else None)
            bps    = d.bps or (latest_q.bps if latest_q else None)
            dps    = d.dps or (latest_q.dps if latest_q else None)
            use_rev, use_opf, use_ni = rev or None, opf or None, ni or None
        else:
            use_rev  = d.revenue
            use_opf  = d.operating_profit
            use_ni   = d.net_income
            assets   = d.total_assets
            liab     = d.total_liabilities
            equity   = d.total_equity
            cap      = d.capital_stock
            eps, bps, dps = d.eps, d.bps, d.dps

        # 재무상태표 이상값 보정 (부채/자본이 자산과 같은 경우 등)
        assets, liab, equity = _sanitize_balance_sheet(assets, liab, equity)

        opm = (use_opf / use_rev * 100) \
            if (use_rev and use_rev != 0 and use_opf is not None) else 0.0
        result.append({
            "period":      f"{d.year}.12",
            "revenue":     _to_uk(use_rev),
            "op_profit":   _to_uk(use_opf),
            "net_income":  _to_uk(use_ni),
            "opm":         round(opm, 1),
            "assets":      _to_uk(assets),
            "liabilities": _to_uk(liab),
            "equity":      _to_uk(equity),
            "capital":     _to_uk(cap),
            "eps":         _calc_eps(eps, use_ni, _shares_issued),
            "bps": bps, "dps": dps,
        })
    result.reverse()
    for r in result:
        r['period'] = _fmt_annual_period(r['period'])
    return result


def get_sector_performance(db: Session):
    sectors = db.query(models.SectorInfo).all()
    sector_map: dict[str, list] = {}
    for s in sectors:
        sector_map.setdefault(s.sector_name, []).append(s.stock_code)
    return [{"sector": sn, "avg_profit": 1.2, "count": len(c)} for sn, c in sector_map.items()]


# ─────────────────────────────────────────────────────────────
# 매크로 헬퍼
# ─────────────────────────────────────────────────────────────

def _query_latest(db: Session, symbol: str):
    """날짜(일) 중복 제거 후 최신 2일치 반환."""
    rows = (
        db.query(models.PriceHistory)
        .filter(
            models.PriceHistory.stock_code == symbol,
            models.PriceHistory.close > 0,
        )
        .order_by(models.PriceHistory.date.desc())
        .limit(20)
        .all()
    )
    seen = {}
    for r in rows:
        k = r.date.strftime("%Y-%m-%d") if hasattr(r.date, "strftime") else str(r.date)[:10]
        if k not in seen:
            seen[k] = r
        if len(seen) == 2:
            break
    vals = list(seen.values())
    return (vals[0] if vals else None), (vals[1] if len(vals) > 1 else None)


def _query_latest_any(db: Session, symbols: list[str]):
    """심볼 후보군 중 데이터가 있는 첫 번째 최신 2일치를 반환."""
    for s in symbols:
        latest, prev = _query_latest(db, s)
        if latest:
            return s, latest, prev
    return None, None, None


def _fetch_latest_yahoo_close(symbol: str):
    """DB 미존재 시 Yahoo에서 최근 2개 종가를 직접 조회."""
    try:
        import yfinance as yf
        df = yf.download(symbol, period="10d", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None, None
        close_col = df.get("Close")
        if close_col is None:
            return None, None
        # yfinance가 단일 종목에서도 DataFrame(MultiIndex)로 반환하는 경우 대응
        if hasattr(close_col, "iloc") and hasattr(close_col, "columns"):
            if close_col.empty:
                return None, None
            close_series = close_col.iloc[:, 0].dropna()
        else:
            close_series = close_col.dropna()
        closes = close_series.tolist()
        if not closes:
            return None, None
        latest = float(closes[-1])
        prev = float(closes[-2]) if len(closes) > 1 else None
        return latest, prev
    except Exception:
        return None, None


def _normalize_treasury_yield(symbol: str, raw: float | None) -> float | None:
    """Yahoo 심볼별 금리 퍼센트 정규화."""
    if raw is None:
        return None
    # ^TNX/^TYX는 환경에 따라 4.6 또는 46.0 스케일이 혼재할 수 있어 동적 보정
    if symbol in ("^TNX", "^TYX"):
        return round(raw / 10.0, 3) if raw >= 20 else round(raw, 3)
    return round(raw, 3)


def _pct_change(latest, prev) -> float:
    if not latest or not prev or not prev.close or prev.close == 0:
        return 0.0
    return round((latest.close - prev.close) / prev.close * 100, 2)


def _history(db: Session, symbol: str, days: int = 30) -> list:
    """최근 N일 종가 배열 반환 (날짜 중복 제거, 오름차순)."""
    cutoff = (datetime.now() - timedelta(days=days + 10)).strftime("%Y-%m-%d")  # 여유 있게
    rows = (
        db.query(models.PriceHistory)
        .filter(
            models.PriceHistory.stock_code == symbol,
            models.PriceHistory.date >= cutoff,
        )
        .order_by(models.PriceHistory.date.asc())
        .all()
    )
    seen = {}
    for r in rows:
        if not r.close or r.close <= 0:  # 이상값 제거
            continue
        k = r.date.strftime("%Y-%m-%d") if hasattr(r.date, "strftime") else str(r.date)[:10]
        seen[k] = r.close
    # 최근 days일치만 슬라이싱
    all_items = _filter_broad_index_history(symbol, sorted(seen.items()))
    return [{"date": k, "close": v} for k, v in all_items[-days:]]


def _fetch_rss_titles(url: str, limit: int = 4) -> list[str]:
    try:
        import requests
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out = []
        for it in root.findall(".//item"):
            t = it.findtext("title")
            if t:
                out.append(t.strip())
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _build_treasury_ai_brief(y2, y10, y30, spread_10_2, spread_30_10, risk_level: str) -> str:
    """외부 뉴스 + 금리 데이터 기반 3줄 브리핑(일일 캐시)."""
    today = datetime.now().strftime("%Y-%m-%d")
    if _TREASURY_BRIEF_CACHE.get("date") == today and _TREASURY_BRIEF_CACHE.get("text"):
        return _TREASURY_BRIEF_CACHE["text"]

    q = urllib.parse.urlencode({"q": "US CPI Federal Reserve Treasury yield DXY when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    feeds = [
        "https://finance.yahoo.com/news/rssindex",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://www.marketwatch.com/rss/topstories",
        f"https://news.google.com/rss/search?{q}",
    ]
    titles = []
    for u in feeds:
        titles.extend(_fetch_rss_titles(u, limit=3))
    dedup = []
    seen = set()
    for t in titles:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(t)
    titles = dedup[:10]

    try:
        if config.GEMINI_API_KEY:
            client = OpenAI()
            payload = {
                "date": today,
                "yields_pct": {"2Y": y2, "10Y": y10, "30Y": y30},
                "spreads_pctp": {"10Y-2Y": spread_10_2, "30Y-10Y": spread_30_10},
                "risk_level": risk_level,
                "news_titles": titles,
            }
            prompt = (
                "입력 JSON만 보고 한국어 3줄 이내로 브리핑하라. "
                "1줄=핵심, 2줄=위험신호, 3줄=의의(투자 시사점). "
                "사실 기반으로 쓰고 추정이면 '추정' 명시. 마크다운 금지.\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=170,
            )
            txt = (res.choices[0].message.content or "").strip()
            lines = [x.strip() for x in txt.splitlines() if x.strip()]
            if len(lines) <= 1:
                # 모델이 한 줄로 답한 경우 문장 기준으로 3줄 이하 분할
                parts = [p.strip() for p in txt.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|") if p.strip()]
                lines = parts
            txt = "\n".join(lines[:3]) if lines else ""
            if txt:
                _TREASURY_BRIEF_CACHE["date"] = today
                _TREASURY_BRIEF_CACHE["text"] = txt
                return txt
    except Exception:
        pass

    # fallback
    l1 = f"핵심: 10Y {y10:.2f}%·30Y {y30:.2f}%·2Y {y2:.2f}%로 금리 레벨은 {risk_level} 구간입니다." if None not in (y2, y10, y30) else "핵심: 미국채 핵심 금리 데이터 수집 중입니다."
    l2 = f"위험신호: 10Y-2Y {spread_10_2:.2f}%p, 30Y-10Y {spread_30_10:.2f}%p를 함께 보며 역전 여부를 점검해야 합니다." if None not in (spread_10_2, spread_30_10) else "위험신호: 스프레드 데이터 수집 중입니다."
    l3 = "의의: CPI/연준/달러 뉴스와 결합해 '좋은 상승(성장)'과 '나쁜 상승(재정·물가 우려)'을 구분해야 합니다."
    txt = "\n".join([l1, l2, l3])
    _TREASURY_BRIEF_CACHE["date"] = today
    _TREASURY_BRIEF_CACHE["text"] = txt
    return txt


def get_macro_status(db: Session) -> dict:
    """
    종합 매크로 현황 반환.
    반환 구조:
      index:       { KOSPI, KOSDAQ }          — 지수 + 수급 + 30일 히스토리
      vix:         { value, change, date, history(30일) }
      us_treasury: { US2Y, US10Y, US30Y, spreads, risk, ai_summary }
      commodities: { USD/KRW, GOLD, OIL }     — 가격 + 30일 히스토리
    """
    # ── KOSPI / KOSDAQ / 나스닥 / S&P500 ─────────────────────
    index_result = {}
    for symbol, name in [("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ"),
                         ("^KS200", "KOSPI200"), ("^KQ150", "KOSDAQ150"),
                         ("^IXIC", "NASDAQ"), ("^GSPC", "S&P500")]:
        latest, prev = _query_latest(db, symbol)
        change = _pct_change(latest, prev)
        # 나스닥/S&P500은 수급 없음
        # KOSPI/KOSDAQ: 가장 최근 비-제로 수급 행 사용 (당일 미수집 대비)
        inst = frn = ind = 0
        inst_5d = frn_5d = ind_5d = 0
        supply_date = None
        if name in ("KOSPI", "KOSDAQ"):
            sup_rows = (
                db.query(models.PriceHistory)
                .filter(
                    models.PriceHistory.stock_code == symbol,
                    (models.PriceHistory.inst_net_buy != 0) |
                    (models.PriceHistory.frn_net_buy  != 0),
                )
                .order_by(models.PriceHistory.date.desc())
                .limit(5)
                .all()
            )
            if sup_rows:
                sr = sup_rows[0]
                inst = round(sr.inst_net_buy or 0)
                frn  = round(sr.frn_net_buy  or 0)
                ind  = round(getattr(sr, 'ind_net_buy', 0) or 0)
                supply_date = (sr.date.strftime("%Y-%m-%d") if hasattr(sr.date,"strftime")
                               else str(sr.date)[:10])
                inst_5d = round(sum(r.inst_net_buy or 0 for r in sup_rows))
                frn_5d  = round(sum(r.frn_net_buy  or 0 for r in sup_rows))
                ind_5d  = round(sum(getattr(r, 'ind_net_buy', 0) or 0 for r in sup_rows))
        # 시각이 00:00이면 날짜만 표시, 아니면 날짜+시각
        def _fmt_date(dt):
            if not dt: return "-"
            if not hasattr(dt, 'hour'): return str(dt)[:10]
            if dt.hour == 0 and dt.minute == 0:
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M")

        index_result[name] = {
            "symbol":       symbol,
            "value":        round(latest.close, 2) if latest else None,
            "change":       change,
            "date":         _fmt_date(latest.date) if latest else "-",
            "inst_net_buy": inst,
            "frn_net_buy":  frn,
            "ind_net_buy":  ind if ind else -(inst + frn),
            "inst_5d": inst_5d,
            "frn_5d":  frn_5d,
            "ind_5d":  ind_5d if ind_5d else -(inst_5d + frn_5d),
            "supply_date":  supply_date,
            # 3가지 기간 히스토리 (탭 전환용)
            "history_90":   _history(db, symbol, 90),
            "history_365":  _history(db, symbol, 365),
            "history_1095": _history(db, symbol, 1095),
        }

    # ── VIX ──────────────────────────────────────────────────
    vl, vp = _query_latest(db, "^VIX")
    vix_result = {
        "value":   round(vl.close, 2) if vl else None,
        "change":  _pct_change(vl, vp),
        "date":    (str(vl.date)[:10] if not hasattr(vl.date,"hour") else (vl.date.strftime("%Y-%m-%d") if vl.date.hour==0 and vl.date.minute==0 else vl.date.strftime("%Y-%m-%d %H:%M"))) if vl else "-",
        "history": _history(db, "^VIX", 30),
    }

    # ── 미국 국채(2Y/10Y/30Y) ────────────────────────────────
    us_treasury = {}
    treasury_sources = {
        "US2Y": ["2YY=F"],
        "US10Y": ["^TNX", "10Y=F"],
        "US30Y": ["^TYX"],
    }
    for key, candidates in treasury_sources.items():
        used_symbol, latest, prev = _query_latest_any(db, candidates)
        val = _normalize_treasury_yield(used_symbol, float(latest.close) if latest and latest.close is not None else None)
        prv = _normalize_treasury_yield(used_symbol, float(prev.close) if prev and prev.close is not None else None)

        # DB에 없으면 Yahoo 실시간 폴백
        used_date = (str(latest.date)[:10] if latest else "-")
        if val is None:
            for s in candidates:
                y_latest, y_prev = _fetch_latest_yahoo_close(s)
                if y_latest is None:
                    continue
                used_symbol = s
                val = _normalize_treasury_yield(s, y_latest)
                prv = _normalize_treasury_yield(s, y_prev)
                used_date = datetime.now().strftime("%Y-%m-%d")
                break
        chg = None
        if val is not None and prv not in (None, 0):
            chg = round((val - prv) / abs(prv) * 100.0, 2)
        history_symbol = used_symbol
        if key == "US2Y" and (used_symbol == "2YY=F"):
            # 과거 저장이 US2Y 코드로 된 데이터와 호환
            history_symbol = "US2Y"
        us_treasury[key] = {
            "symbol": used_symbol,
            "value": val,
            "change": chg,
            "date": used_date,
            "history": _history(db, history_symbol, 365) if history_symbol else [],
        }

    y2 = us_treasury["US2Y"]["value"]
    y10 = us_treasury["US10Y"]["value"]
    y30 = us_treasury["US30Y"]["value"]
    spread_10_2 = (round(y10 - y2, 3) if y10 is not None and y2 is not None else None)
    spread_30_10 = (round(y30 - y10, 3) if y30 is not None and y10 is not None else None)

    risk_flags = []
    if y10 is not None and y10 >= 4.5:
        risk_flags.append("10Y_HIGH")
    if y10 is not None and y10 >= 5.0:
        risk_flags.append("10Y_CRITICAL")
    if y30 is not None and y30 >= 5.0:
        risk_flags.append("30Y_HIGH")
    if y30 is not None and y30 >= 5.5:
        risk_flags.append("30Y_CRITICAL")
    if spread_10_2 is not None and spread_10_2 < 0:
        risk_flags.append("INVERSION_10_2")
    if spread_30_10 is not None and spread_30_10 < 0:
        risk_flags.append("INVERSION_30_10")

    risk_level = "정상"
    if any(x in risk_flags for x in ("10Y_CRITICAL", "30Y_CRITICAL", "INVERSION_30_10")):
        risk_level = "위험"
    elif any(x in risk_flags for x in ("10Y_HIGH", "30Y_HIGH", "INVERSION_10_2")):
        risk_level = "주의"

    ai_lines = []
    if y10 is not None:
        if y10 >= 5.0:
            ai_lines.append(f"10년물 {y10:.2f}%: 금융여건 급격 긴축 구간입니다.")
        elif y10 >= 4.5:
            ai_lines.append(f"10년물 {y10:.2f}%: 위험자산 밸류에이션 압박 구간입니다.")
        elif y10 >= 3.5:
            ai_lines.append(f"10년물 {y10:.2f}%: 중립~긴축 경계 구간입니다.")
        else:
            ai_lines.append(f"10년물 {y10:.2f}%: 비교적 안정 구간입니다.")
    if y30 is not None:
        if y30 >= 5.5:
            ai_lines.append(f"30년물 {y30:.2f}%: 장기 재정신뢰 우려가 큰 위험 구간입니다.")
        elif y30 >= 5.0:
            ai_lines.append(f"30년물 {y30:.2f}%: 장기채 변동성 확대 주의 구간입니다.")
        elif y30 >= 4.0:
            ai_lines.append(f"30년물 {y30:.2f}%: 재정 우려가 서서히 반영되는 구간입니다.")
        else:
            ai_lines.append(f"30년물 {y30:.2f}%: 장기채 금리는 정상 범위입니다.")
    if spread_10_2 is not None:
        if spread_10_2 < 0:
            ai_lines.append(f"10Y-2Y 스프레드 {spread_10_2:.2f}%p: 장단기 금리 역전 신호입니다.")
        else:
            ai_lines.append(f"10Y-2Y 스프레드 {spread_10_2:.2f}%p: 역전은 아닙니다.")
    if spread_30_10 is not None and spread_30_10 < 0:
        ai_lines.append(f"30Y-10Y 스프레드 {spread_30_10:.2f}%p: 매우 드문 위험 신호입니다.")

    # ── 원자재 · 환율 (각각 30일 히스토리 포함) ──────────────
    commodity_result = {}
    for symbol, name in [("USDKRW=X", "USD/KRW"), ("GC=F", "GOLD"), ("CL=F", "OIL")]:
        latest, prev = _query_latest(db, symbol)
        commodity_result[name] = {
            "symbol":  symbol,
            "value":   round(latest.close, 2) if latest else None,
            "change":  _pct_change(latest, prev),
            "date":    (str(latest.date)[:10] if not hasattr(latest.date,"hour") else (latest.date.strftime("%Y-%m-%d") if latest.date.hour==0 and latest.date.minute==0 else latest.date.strftime("%Y-%m-%d %H:%M"))) if latest else "-",
            "history": _history(db, symbol, 30),
        }

    return {
        "index":       index_result,
        "vix":         vix_result,
        "us_treasury": {
            **us_treasury,
            "spreads": {
                "10Y_minus_2Y": spread_10_2,
                "30Y_minus_10Y": spread_30_10,
            },
            "risk": {
                "level": risk_level,
                "flags": risk_flags,
            },
            "ai_summary": _build_treasury_ai_brief(y2, y10, y30, spread_10_2, spread_30_10, risk_level),
            "updated_at": us_treasury["US10Y"]["date"] if us_treasury.get("US10Y") else "-",
        },
        "commodities": commodity_result,
    }
