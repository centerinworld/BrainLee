from sqlalchemy.orm import Session
import models
from datetime import datetime, timedelta

_VALID_YEAR_MIN = 2000
_VALID_YEAR_MAX = 2030
_VALID_QUARTERS = {1, 2, 3, 4}
_QUARTER_TO_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
_MONTH_TO_Q_LABEL = {'03': '1Q', '06': '2Q', '09': '3Q', '12': '4Q'}


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
    start_date = datetime.now() - timedelta(days=days)
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
        seen[p.date.strftime("%Y-%m-%d") if hasattr(p.date,"strftime") else str(p.date)[:10]] = p
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


def get_financial_summary(db: Session, stock_code: str, data_type: str = "annual"):
    if data_type == "quarter":
        data = (
            db.query(models.FinancialData)
            .filter(
                models.FinancialData.stock_code == stock_code,
                models.FinancialData.is_annual.is_(False),
            )
            .order_by(models.FinancialData.year.desc(), models.FinancialData.quarter.desc())
            .limit(8).all()
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
                "eps":         d.eps, "bps": d.bps, "dps": d.dps,
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

            q4_rev = (rev_a - rev123) if (rev_a is not None and rev123 is not None) else rev_a
            q4_opf = (opf_a - opf123) if (opf_a is not None and opf123 is not None) else opf_a
            q4_ni  = (ni_a  - ni123)  if (ni_a  is not None and ni123  is not None) else ni_a

            opm = (q4_opf / q4_rev * 100) if (q4_rev and q4_rev != 0 and q4_opf is not None) else 0.0
            ann_a, ann_l, ann_e = _sanitize_balance_sheet(
                annual_rec.total_assets, annual_rec.total_liabilities, annual_rec.total_equity)

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
                "eps":         annual_rec.eps,
                "bps":         annual_rec.bps,
                "dps":         annual_rec.dps,
            })

        result.sort(key=lambda x: x['period'])
        # 최근 8분기만 반환
        result = result[-8:] if len(result) > 8 else result
        for r in result:
            r['period'] = _fmt_q_period(r['period'])
        return result

    # ── 연간 데이터 ──────────────────────────────────────────────
    annual_data_raw = (
        db.query(models.FinancialData)
        .filter(
            models.FinancialData.stock_code == stock_code,
            models.FinancialData.is_annual.is_(True),
        )
        .order_by(models.FinancialData.year.desc(), models.FinancialData.revenue.desc())
        .limit(10).all()
    )
    # 같은 연도 중복 레코드 제거 — revenue 내림차순이므로 첫 번째가 가장 완전한 데이터
    _seen_years: set = set()
    annual_data = []
    for _d in annual_data_raw:
        if _d.year not in _seen_years:
            _seen_years.add(_d.year)
            annual_data.append(_d)
        if len(annual_data) >= 5:
            break

    # is_annual=True 레코드가 없으면 분기 데이터로 대체
    if not annual_data:
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
                "eps":         d.eps, "bps": d.bps, "dps": d.dps,
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
        if max_q_rev and d.revenue < max_q_rev.revenue * 1.8:
            # 연간값이 가장 큰 분기의 1.8배 미만 → 잘못 저장된 것으로 판단
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
            "eps":         eps, "bps": bps, "dps": dps,
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


def _pct_change(latest, prev) -> float:
    if not latest or not prev or not prev.close or prev.close == 0:
        return 0.0
    return round((latest.close - prev.close) / prev.close * 100, 2)


def _history(db: Session, symbol: str, days: int = 30) -> list:
    """최근 N일 종가 배열 반환 (날짜 중복 제거, 오름차순)."""
    cutoff = datetime.now() - timedelta(days=days + 10)  # 여유 있게
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
    all_items = sorted(seen.items())
    return [{"date": k, "close": v} for k, v in all_items[-days:]]


def get_macro_status(db: Session) -> dict:
    """
    종합 매크로 현황 반환.
    반환 구조:
      index:       { KOSPI, KOSDAQ }          — 지수 + 수급 + 30일 히스토리
      vix:         { value, change, date, history(30일) }
      commodities: { USD/KRW, GOLD, OIL }     — 가격 + 30일 히스토리
    """
    # ── KOSPI / KOSDAQ / 나스닥 / S&P500 ─────────────────────
    index_result = {}
    for symbol, name in [("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ"),
                         ("^IXIC", "NASDAQ"), ("^GSPC", "S&P500")]:
        latest, prev = _query_latest(db, symbol)
        change = _pct_change(latest, prev)
        # 나스닥/S&P500은 수급 없음
        # KOSPI/KOSDAQ: 가장 최근 비-제로 수급 행 사용 (당일 미수집 대비)
        inst = frn = ind = 0
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
                .limit(1)
                .all()
            )
            if sup_rows:
                sr = sup_rows[0]
                inst = round(sr.inst_net_buy or 0)
                frn  = round(sr.frn_net_buy  or 0)
                ind  = round(getattr(sr, 'ind_net_buy', 0) or 0)
                supply_date = (sr.date.strftime("%Y-%m-%d") if hasattr(sr.date,"strftime")
                               else str(sr.date)[:10])
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
        "commodities": commodity_result,
    }
