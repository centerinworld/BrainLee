from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import models, schemas, crud, processor, screener, ai_analyzer
from database import get_db, engine
import logging
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from hs_trade_lab.app.main import app as hs_trade_lab_app
from hs_trade_lab.semiconductor_value_lab.fastapi_app import app as semiconductor_value_lab_app

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 데이터베이스 테이블 생성 (상시 동기화)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="주식 분석 백엔드 (프로젝트 안티그래비티)")
app.mount("/hs", hs_trade_lab_app)
app.mount("/semiconductor-lab", semiconductor_value_lab_app)

# ═══════════════════════════════════════════════════════
#  전역 상태 — startup_event 이전에 반드시 정의
# ═══════════════════════════════════════════════════════
import threading as _th
import time as _tm

_collecting: dict      = {}   # stock_code -> "running"|"done"
_valuation_cache: dict = {}   # stock_code -> {per,pbr,cached_at,...}
_market_info_cache: dict = {} # stock_code -> {market,mktcap,mktcap_rank,cached_at}
_signal_cache: dict = {}      # 'market' or stock_code -> {results, cached_at}

# ── 통합 스케줄러 (startup/shutdown에서 제어) ─────────────────
from scheduler import CollectionScheduler
from database import SessionLocal as _SessionLocal
_scheduler = CollectionScheduler(db_factory=_SessionLocal)

# ── 분리된 라우터 등록 ────────────────────────────────────────
from routes.trend          import router as _trend_router
from routes.signals        import router as _signals_router
from routes.backtest       import router as _backtest_router
from routes.telegram       import router as _telegram_router
from routes.buy_candidates import router as _buy_router
from routes.reports        import router as _reports_router
from routes.ingest         import router as _ingest_router
from routes.portfolio          import router as _portfolio_router
from routes.market_indicators  import router as _market_indicators_router
from routes.extra_signals      import router as _extra_signals_router
from routes.stock_analysis_rs  import router as _stock_analysis_rs_router
from routes.market_radar       import router as _market_radar_router
from routes.sector_define      import router as _sector_define_router
from routes.kis_trading        import router as _kis_trading_router
from routes.dart_contracts     import router as _dart_contracts_router
from routes.kiwoom             import router as _kiwoom_router
import sys as _sys
_sys.path.insert(0, "/Applications/stock_dashboard/ETF_check")
from routes_etf                import router as _etf_check_router
_sys.path.insert(0, "/Applications/stock_dashboard/employment_monitor")
from routes_employment_v2      import router as _employment_v2_router

app.include_router(_trend_router,              prefix="/api/trend",              tags=["trend"])
app.include_router(_signals_router,            prefix="/api/signals",            tags=["signals"])
app.include_router(_backtest_router,           prefix="/api/backtest",           tags=["backtest"])
app.include_router(_telegram_router,           prefix="/api/telegram",           tags=["telegram"])
app.include_router(_buy_router,                prefix="/api/buy-candidates",     tags=["buy-candidates"])
app.include_router(_reports_router,            prefix="/api/reports",            tags=["reports"])
app.include_router(_ingest_router,             prefix="/api/ingest",             tags=["ingest"])
app.include_router(_portfolio_router,          prefix="/api/portfolio",          tags=["portfolio"])
app.include_router(_market_indicators_router,  prefix="/api/market-indicators",  tags=["market-indicators"])
app.include_router(_extra_signals_router,      prefix="/api/extra-signals",      tags=["extra-signals"])
app.include_router(_stock_analysis_rs_router,  prefix="/api/stock-analysis-rs",  tags=["stock-analysis-rs"])
app.include_router(_etf_check_router)  # prefix: /api/etf-check (router 내부 정의)
app.include_router(_market_radar_router,  prefix="/api/market-radar",   tags=["market-radar"])
app.include_router(_sector_define_router, prefix="/api/sector-define",  tags=["sector-define"])
app.include_router(_employment_v2_router)  # prefix: /api/employment-v2 (router 내부 정의)
app.include_router(_kis_trading_router)
app.include_router(_dart_contracts_router,  prefix="/api/dart-contracts", tags=["dart-contracts"])
app.include_router(_kiwoom_router,          prefix="/api/kiwoom",         tags=["kiwoom"])


def _send_telegram(msg: str, dedup_key: str = ""):
    """텔레그램 메시지 발송 (중복 방지). notifier 모듈에 위임."""
    from notifier import send as _notify
    _notify(msg, key=dedup_key)



def _get_cached_valuation(stock_code: str) -> dict:
    entry = _valuation_cache.get(stock_code)
    if entry and (_tm.time() - entry.get("cached_at", 0)) < 86400:
        return entry
    return {}


def _scrape_naver(stock_code: str) -> dict:
    """네이버 금융 PER/PBR 스크래핑. URL: .naver (구 .nhn 아님), EUC-KR."""
    empty = {"per": None, "pbr": None, "trailing_eps": None,
             "forward_per": None, "source": None}
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        return empty
    try:
        import requests as _r
        from bs4 import BeautifulSoup
        res = _r.get(
            f"https://finance.naver.com/item/main.naver?code={stock_code}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                     "Referer": "https://finance.naver.com/"},
            timeout=8,
        )
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        def _n(sel):
            t = soup.select_one(sel)
            if not t: return None
            s = t.text.strip().replace(",","").replace("N/A","").strip()
            try: return float(s) if s else None
            except: return None
        per, pbr, eps = _n("em#_per"), _n("em#_pbr"), _n("em#_eps")
        if per is None and pbr is None and eps is None:
            logger.warning(f"[Naver] {stock_code}: 파싱 없음")
            return empty
        logger.info(f"[Naver] {stock_code}: PER={per} PBR={pbr}")
        return {"per": per, "pbr": pbr, "trailing_eps": eps, "forward_per": None, "source": "네이버금융"}
    except Exception as e:
        logger.warning(f"[Naver] {stock_code}: {e}")
        return empty


def _get_latest_disclosed_quarter():
    """현재 시점에 DART에 실제 공시된 최신 분기 반환 (year, quarter).
    
    공시 일정 (법정 제출 기한):
      Q1 (1분기보고서): 5월 15일
      Q2 (반기보고서):  8월 14일
      Q3 (3분기보고서): 11월 14일
      Q4 (사업보고서):  3월 31일 (단, 12월 결산법인 기준)
    
    → 3월부터는 전년도 Q4가 공시되기 시작하므로 Q4 포함
    """
    from datetime import date
    m = date.today().month
    d = date.today().day
    y = date.today().year
    if m >= 11:              return (y, 3)
    elif m >= 8:             return (y, 2)
    elif m >= 5:             return (y, 1)
    elif m >= 3:             return (y - 1, 4)   # 3월부터 전년 Q4 공시 시작
    elif m >= 4:             return (y - 1, 4)   # 4월: 전년 Q4 확정
    else:                    return (y - 1, 3)


def _has_any_financial(stock_code: str, db) -> bool:
    """DB에 해당 종목 재무 데이터가 1건이라도 있는지 직접 확인."""
    return db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code
    ).first() is not None


def _collect_dart_to_db(stock_code: str, db, latest_only: bool = False) -> int:
    """
    DART에서 재무제표를 수집해 DB에 직접 저장 (동기).
    httpx 자기참조 없이 crud.upsert_financial_data 직접 호출.
    반환: 저장 건수
    """
    try:
        import config as _cfg, pandas as pd
        import OpenDartReader
        dart = OpenDartReader(_cfg.DART_API_KEY)
    except Exception as e:
        logger.error(f"[DART] 초기화 실패: {e}")
        return 0

    latest_y, latest_q = _get_latest_disclosed_quarter()
    # ★ 사업보고서(11011)를 먼저 처리해야 Q4 분기 자동계산이 올바르게 됨
    qmap = {"11011": 4, "11013": 1, "11012": 2, "11014": 3}
    # latest_only=True: 최근 2년 / False: 최근 10년
    years = list(range(latest_y, latest_y - (2 if latest_only else 10), -1))

    saved = 0
    for year in years:
        for rcode, qnum in qmap.items():
            # 아직 공시 안 된 분기 스킵
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue
            # 이미 DB에 있으면 스킵 (연간/분기 구분해서 체크)
            is_annual_flag = (rcode == "11011")
            exists = db.query(models.FinancialData).filter(
                models.FinancialData.stock_code == stock_code,
                models.FinancialData.year       == year,
                models.FinancialData.quarter    == qnum,
                models.FinancialData.is_annual  == is_annual_flag,
                models.FinancialData.data_source == "dart",
            ).first() is not None
            if exists:
                continue

            fn_data = None
            for fs in ["CFS", "OFS"]:
                try:
                    df = dart.finstate_all(stock_code, year, rcode, fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df; break
                except Exception:
                    pass
            if fn_data is None or fn_data.empty:
                try:
                    fn_data = dart.finstate(stock_code, year, rcode)
                except Exception:
                    pass
            if fn_data is None or fn_data.empty:
                continue

            m = {k: 0.0 for k in [
                "revenue","operating_profit","net_income","total_assets",
                "total_liabilities","total_equity","capital_stock",
                "eps","bps","dps","cash","total_shares","depreciation_amortization"]}
            for _, row in fn_data.iterrows():
                acc = str(row.get("account_nm","")).replace(" ","")
                vc  = "thstrm_amount"
                if not acc or vc not in row or pd.isna(row[vc]): continue
                try: val = float(str(row[vc]).replace(",",""))
                except: continue
                if   "매출액" in acc or "영업수익" in acc:                             m["revenue"] = val
                elif "영업이익" in acc:                                                m["operating_profit"] = val
                elif ("당기순이익" in acc or "분기순이익" in acc or "반기순이익" in acc) and "주당" not in acc and "지배" not in acc: m["net_income"] = val
                elif "자산총계" in acc:                                                m["total_assets"] = val
                elif "부채총계" in acc:                                                m["total_liabilities"] = val
                elif "자본총계" in acc:                                                m["total_equity"] = val
                elif "자본금" in acc:                                                  m["capital_stock"] = val
                elif any(k in acc for k in ["기본주당순이익","주당순이익","기본EPS"]): m["eps"] = val
                elif any(k in acc for k in ["주당순자산","1주당순자산가액"]):           m["bps"] = val
                elif any(k in acc for k in ["주당배당금","주당현금배당금"]):            m["dps"] = val
                elif any(k in acc for k in ["현금및현금성자산","현금성자산"]):          m["cash"] = val
            try:
                fin = schemas.FinancialIngest(
                    stock_code=stock_code, year=year, quarter=qnum,
                    is_annual=(rcode == "11011"), **m)
                crud.upsert_financial_data(db, fin)
                saved += 1
                logger.info(f"[DART] {stock_code} {year}Q{qnum} 저장")

                # ── 사업보고서(Q4 연간) 저장 후 → 4Q 분기값 자동 계산 ──
                # 4Q 분기 = 연간합계 - (Q1 + Q2 + Q3)
                if rcode == "11011":
                    try:
                        q1 = db.query(models.FinancialData).filter(
                            models.FinancialData.stock_code == stock_code,
                            models.FinancialData.year    == year,
                            models.FinancialData.quarter == 1,
                            models.FinancialData.is_annual.is_(False),
                        ).first()
                        q2 = db.query(models.FinancialData).filter(
                            models.FinancialData.stock_code == stock_code,
                            models.FinancialData.year    == year,
                            models.FinancialData.quarter == 2,
                            models.FinancialData.is_annual.is_(False),
                        ).first()
                        q3 = db.query(models.FinancialData).filter(
                            models.FinancialData.stock_code == stock_code,
                            models.FinancialData.year    == year,
                            models.FinancialData.quarter == 3,
                            models.FinancialData.is_annual.is_(False),
                        ).first()
                        # Q4 분기 이미 있으면 스킵
                        q4_exists = db.query(models.FinancialData).filter(
                            models.FinancialData.stock_code == stock_code,
                            models.FinancialData.year    == year,
                            models.FinancialData.quarter == 4,
                            models.FinancialData.is_annual.is_(False),
                        ).first()
                        if q1 and q2 and q3 and not q4_exists:
                            def _sub(a, b, c, annual):
                                """annual - q1 - q2 - q3, None 안전 처리"""
                                if annual is None: return None
                                return (annual or 0) - (a or 0) - (b or 0) - (c or 0)
                            q4_data = schemas.FinancialIngest(
                                stock_code     = stock_code,
                                year           = year,
                                quarter        = 4,
                                is_annual      = False,
                                revenue        = _sub(q1.revenue,           q2.revenue,           q3.revenue,           m["revenue"]),
                                operating_profit= _sub(q1.operating_profit,  q2.operating_profit,  q3.operating_profit,  m["operating_profit"]),
                                net_income     = _sub(q1.net_income,         q2.net_income,         q3.net_income,         m["net_income"]),
                                total_assets   = m["total_assets"],    # 기말 잔액 그대로
                                total_liabilities= m["total_liabilities"],
                                total_equity   = m["total_equity"],
                                capital_stock  = m["capital_stock"],
                                eps            = m["eps"],
                                bps            = m["bps"],
                                dps            = m["dps"],
                                cash           = m["cash"],
                            )
                            crud.upsert_financial_data(db, q4_data)
                            saved += 1
                            logger.info(f"[DART] {stock_code} {year}Q4 분기값 자동 계산 저장")
                        elif q4_exists:
                            logger.debug(f"[DART] {stock_code} {year}Q4 분기 이미 존재")
                        else:
                            logger.info(f"[DART] {stock_code} {year}Q4 계산 불가 (Q1~Q3 미수집)")
                    except Exception as e_q4:
                        logger.warning(f"[DART] {stock_code} {year}Q4 계산 오류: {e_q4}")

            except Exception as e:
                logger.warning(f"[DART] {stock_code} {year}Q{qnum} 저장실패: {e}")

        if latest_only and saved >= 4:
            break  # 최신 모드: 최근 4건이면 충분

    logger.info(f"[DART] {stock_code} 완료: {saved}건")
    return saved


def _collect_dart_cashflow(stock_code: str, db, latest_only: bool = False) -> int:
    """
    DART 현금흐름표(CF) 수집 → cash_flow_data 테이블에 저장.
    finstate_all 에서 CF 계정 행만 추출하여 저장.
    반환: 저장 건수
    """
    try:
        import config as _cfg, pandas as pd
        import OpenDartReader
        dart = OpenDartReader(_cfg.DART_API_KEY)
    except Exception as e:
        logger.error(f"[DART-CF] 초기화 실패: {e}")
        return 0

    latest_y, latest_q = _get_latest_disclosed_quarter()
    qmap = {"11011": 4, "11013": 1, "11012": 2, "11014": 3}
    years = list(range(latest_y, latest_y - (2 if latest_only else 10), -1))

    saved = 0
    for year in years:
        for rcode, qnum in qmap.items():
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue
            is_annual_flag = (rcode == "11011")
            exists = db.query(models.CashFlowData).filter(
                models.CashFlowData.stock_code == stock_code,
                models.CashFlowData.year       == year,
                models.CashFlowData.quarter    == qnum,
                models.CashFlowData.is_annual  == is_annual_flag,
            ).first() is not None
            if exists:
                continue

            # CFS·OFS 각각 독립 수집 (연결/별도 모두 저장)
            for fs in ["CFS", "OFS"]:
                fn_data = None
                try:
                    df = dart.finstate_all(stock_code, year, rcode, fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df
                except Exception:
                    pass
                if fn_data is None or fn_data.empty:
                    continue

                m = {k: None for k in ["operating_cf","investing_cf","financing_cf",
                                        "capex","cash_end","depreciation"]}
                for _, row in fn_data.iterrows():
                    acc = str(row.get("account_nm","")).replace(" ","")
                    vc  = "thstrm_amount"
                    if not acc or vc not in row or pd.isna(row[vc]): continue
                    try: val = float(str(row[vc]).replace(",",""))
                    except: continue
                    if   acc == "영업활동현금흐름" or acc == "영업활동으로인한현금흐름":
                        m["operating_cf"] = val
                    elif acc == "투자활동현금흐름" or acc == "투자활동으로인한현금흐름":
                        m["investing_cf"] = val
                    elif acc == "재무활동현금흐름" or acc == "재무활동으로인한현금흐름":
                        m["financing_cf"] = val
                    elif any(k in acc for k in ["유형자산의취득","유형자산취득"]):
                        m["capex"] = abs(val) if val is not None else None
                    elif any(k in acc for k in ["현금및현금성자산의기말잔액","기말의현금및현금성자산"]):
                        m["cash_end"] = val
                    elif any(k in acc for k in ["감가상각비","유형자산상각비"]):
                        m["depreciation"] = val

                if all(v is None for v in m.values()):
                    continue

                try:
                    cf = schemas.CashFlowIngest(
                        stock_code=stock_code, year=year, quarter=qnum,
                        is_annual=is_annual_flag, report_type=fs, **m)
                    _upsert_cashflow(db, cf)
                    saved += 1
                    logger.info(f"[DART-CF] {stock_code} {year}Q{qnum}/{fs} 저장")
                except Exception as e:
                    logger.warning(f"[DART-CF] {stock_code} {year}Q{qnum}/{fs} 저장실패: {e}")

        if latest_only and saved >= 4:
            break

    logger.info(f"[DART-CF] {stock_code} 완료: {saved}건")
    return saved


def _upsert_cashflow(db, cf: schemas.CashFlowIngest):
    """현금흐름표 upsert. report_type(CFS/OFS)별로 별도 행 유지."""
    rt = getattr(cf, "report_type", "CFS") or "CFS"
    row = db.query(models.CashFlowData).filter(
        models.CashFlowData.stock_code  == cf.stock_code,
        models.CashFlowData.year        == cf.year,
        models.CashFlowData.quarter     == cf.quarter,
        models.CashFlowData.is_annual   == cf.is_annual,
        models.CashFlowData.report_type == rt,
    ).first()
    if row is None:
        row = models.CashFlowData(
            stock_code=cf.stock_code, year=cf.year,
            quarter=cf.quarter, is_annual=cf.is_annual,
            report_type=rt)
        db.add(row)
    for field in ["operating_cf","investing_cf","financing_cf","capex","cash_end","depreciation"]:
        v = getattr(cf, field)
        if v is not None:
            setattr(row, field, v)
    db.commit()
    db.refresh(row)
    return row


def _get_kis_price(stock_code: str):
    """KIS API 현재가. 실패 시 None."""
    try:
        from kis_client import kis_client
        return kis_client.get_current_price(stock_code)
    except Exception as e:
        logger.warning(f"[KIS] {stock_code}: {e}")
        return None


def _bg_ondemand(stock_code: str):
    """백그라운드 온디맨드: 주가 + 재무 전체."""
    _collecting[stock_code] = "running"
    try:
        from data_collector import DataCollector
        import config as _cfg
        col = DataCollector(dart_api_key=_cfg.DART_API_KEY)
        col.run_ondemand(stock_code)
        logger.info(f"[BG] {stock_code} 완료")
    except Exception as e:
        logger.error(f"[BG] {stock_code}: {e}")
    finally:
        _collecting.pop(stock_code, None)  # 완료 즉시 제거 — 누적 방지


# ── 유틸리티 함수 ──────────────────────────────────────────────────

def _is_market_hours() -> bool:
    """한국 주식시장 운영 시간 여부 (평일 09:00~15:35)."""
    from datetime import datetime as _dt
    now = _dt.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1535


_MACRO_SYMBOLS = {
    "^KS11":    "KOSPI",
    "^KQ11":    "KOSDAQ",
    "^IXIC":    "NASDAQ",
    "^GSPC":    "S&P500",
    "^VIX":     "VIX",
    "2YY=F":    "US2Y",
    "^UST2Y":   "US2Y_ALT",
    "^TNX":     "US10Y",
    "10Y=F":    "US10Y_ALT",
    "^TYX":     "US30Y",
    "30Y=F":    "US30Y_ALT",
    "DX-Y.NYB": "DXY",
    "GC=F":     "GOLD",
    "CL=F":     "OIL",
    "USDKRW=X": "USD/KRW",
}


def _realtime_fetch_price(stock_code: str, db) -> float | None:
    """KIS API로 현재가를 즉시 조회하고 DB에 upsert 후 종가 반환."""
    try:
        from kis_client import kis_client
        data = kis_client.get_current_price(stock_code)
        if not data:
            return None
        crud.bulk_insert_price_history(db, schemas.PriceIngest(
            stock_code=stock_code,
            prices=[schemas.PriceRecord(
                date=data["date"], open=data["open"], high=data["high"],
                low=data["low"], close=data["close"], volume=data["volume"],
                inst_net_buy=0.0, frn_net_buy=0.0,
            )],
        ))
        return data["close"]
    except Exception as e:
        logger.warning(f"[RT-Price] {stock_code}: {e}")
        return None


def _realtime_fetch_macro(db) -> None:
    """Yahoo Finance로 매크로 지수 최신값 조회 후 DB upsert."""
    from datetime import datetime as _dt, date as _date
    _now = _dt.now()
    if _now.weekday() >= 5:
        return
    _today = _now.date().isoformat()
    try:
        _existing = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == "^KS11",
            models.PriceHistory.date >= _today,
        ).first()
        if _existing and _existing.close and _existing.close > 0:
            return
    except Exception:
        pass
    import yfinance as yf
    def _spike_threshold(sym: str) -> float:
        if sym in ("^IXIC", "^GSPC", "^KS11", "^KQ11", "^KS200", "^KQ150"):
            return 12.0
        if sym in ("^TNX", "^TYX", "2YY=F", "10Y=F", "30Y=F", "^UST2Y"):
            return 6.0
        if sym == "^VIX":
            return 25.0
        return 15.0

    def _prev_close(sym: str):
        row = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == sym,
            models.PriceHistory.close > 0,
        ).order_by(models.PriceHistory.date.desc()).first()
        return float(row.close) if row and row.close else None

    def _download_prices(sym: str, period: str = "5d"):
        df = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        if hasattr(df.columns, 'get_level_values'):
            try:
                df.columns = df.columns.get_level_values(0)
            except Exception:
                pass
        out = []
        for ts, row in df.iterrows():
            try:
                row_date = ts.date() if hasattr(ts, 'date') else _date.fromisoformat(str(ts)[:10])
                def _gv(col):
                    v = row.get(col, 0)
                    if hasattr(v, 'iloc'):
                        v = v.iloc[0]
                    try:
                        return float(v) if v is not None else 0.0
                    except Exception:
                        return 0.0
                out.append(schemas.PriceRecord(
                    date=_dt.combine(row_date, _dt.min.time()),
                    open=_gv("Open"), high=_gv("High"),
                    low=_gv("Low"), close=_gv("Close"),
                    volume=_gv("Volume"),
                    inst_net_buy=0.0, frn_net_buy=0.0,
                ))
            except Exception:
                pass
        return out

    today = _date.today()
    for symbol, name in _MACRO_SYMBOLS.items():
        try:
            prices = _download_prices(symbol, "5d")
            if not prices:
                continue
            if not prices:
                continue

            # 급변값 재검증: 직전 저장값 대비 과도 변동 시 재파싱 1회
            latest = max(prices, key=lambda p: p.date)
            prev_c = _prev_close(symbol)
            if prev_c and latest.close and latest.close > 0:
                diff_pct = abs((latest.close - prev_c) / prev_c * 100.0)
                th = _spike_threshold(symbol)
                if diff_pct >= th:
                    logger.warning(
                        f"[RT-MacroGuard] {symbol}: 급변 {diff_pct:.2f}% (prev={prev_c:.4f}, new={latest.close:.4f}) 재파싱"
                    )
                    retry_prices = _download_prices(symbol, "10d")
                    if retry_prices:
                        retry_latest = max(retry_prices, key=lambda p: p.date)
                        retry_diff = abs((retry_latest.close - prev_c) / prev_c * 100.0) if prev_c else 0.0
                        if retry_diff < diff_pct:
                            prices = retry_prices
                            latest = retry_latest
                            diff_pct = retry_diff
                    if diff_pct >= th:
                        logger.error(
                            f"[RT-MacroGuard] {symbol}: 급변 재검증 실패(diff={diff_pct:.2f}%, th={th}%) 저장 스킵"
                        )
                        continue
            # 데이터 무결성: 미거래일(today) 합성행 생성 금지
            # (미국 지수/금리의 날짜 오염 및 왜곡 방지)
            crud.bulk_insert_price_history(db, schemas.PriceIngest(stock_code=symbol, prices=prices))
            logger.info(f"[RT-Macro] {symbol}({name}) {len(prices)}건 저장")
        except Exception as e:
            logger.warning(f"[RT-Macro] {symbol}: {e}")


def _monthly_bulk_update() -> None:
    """전종목 메타(시총·섹터) 갱신 — CollectionScheduler 로직 위임."""
    try:
        import stock_universe
        stock_universe.update_universe()
    except Exception as e:
        logger.error(f"[월간업데이트] {e}")


def _daily_disclosure_check() -> None:
    """DART 공시 기반 재무 재수집 — CollectionScheduler 로직 위임."""
    _scheduler._job_disclosure_check()


# ── AI 스크리너 30분 사전계산 + AI 적극검토 자동매매 ─────────────
_screener_lock = _th.Lock()

def _run_screener_precompute():
    """AI 스크리너 3종 + 시장시그널 결과를 미리 계산하고 캐시에 저장."""
    import sqlite3 as _sl
    import time as _t
    from signal_engine import (
        calc_trend_candidates, calc_value_candidates,
        calc_top20_candidates, calc_market_signals,
    )

    try:
        with _screener_lock:
            conn = _sl.connect("stock.db")

            # 0. 시장 시그널 (메인 화면 즉시 표시용 — 가장 먼저 캐시)
            try:
                market_data = calc_market_signals(conn)
                _signal_cache['market'] = {'data': market_data, 'at': _t.time()}
                logger.info(f"[사전계산] 시장시그널 {len(market_data)}개 캐시 완료")
            except Exception as _e:
                logger.warning(f"[사전계산] 시장시그널 오류: {_e}")

            # 1. 추세추종 계산
            trend_data = calc_trend_candidates(conn)
            _signal_cache['trend_candidates'] = {'data': trend_data, 'at': _t.time()}

            # 2. 가치매수 계산
            value_data = calc_value_candidates(conn)
            _signal_cache['value_candidates'] = {'data': value_data, 'at': _t.time()}

            # 3-a. 진입트리거 TOP20 — 3-트랙 종합 선별
            top20_data = calc_top20_candidates(conn)
            _signal_cache['top20_candidates'] = {'data': top20_data, 'at': _t.time()}

            # 3. 재무스크리너 계산
            import screener as _screener
            fin_data = _screener.advanced_screening()
            _signal_cache['fin_screener'] = {'data': fin_data, 'at': _t.time()}

            conn.close()

            # 4. AI 적극검토 교집합 계산 — 강화된 필터 (v4)
            from signal_logic import (
                COMBO_TREND_SCORE_MIN, COMBO_VALUE_SCORE_MIN, COMBO_FIN_SCORE_MIN
            )

            # ★ KOSPI 추세 필터: KOSPI > MA60 인지 확인 (하락장 차단)
            _kospi_rows = conn.execute("""
                SELECT close FROM price_history
                WHERE stock_code='^KS11' AND close>0
                ORDER BY date DESC LIMIT 65
            """).fetchall()
            _kospi_bullish = True   # 데이터 없으면 허용
            if len(_kospi_rows) >= 60:
                _kospi_now = _kospi_rows[0][0]
                _kospi_ma60 = sum(r[0] for r in _kospi_rows[:60]) / 60
                _kospi_bullish = _kospi_now > _kospi_ma60

            trend_map = {s['stock_code']: s for s in trend_data}
            value_map = {s['stock_code']: s for s in value_data}
            fin_map   = {s['stock_code']: s for s in fin_data}
            all_codes = set(trend_map) | set(value_map) | set(fin_map)

            combo = []
            for code in all_codes:
                in_t = code in trend_map
                in_v = code in value_map
                in_f = code in fin_map

                # 각 스크리너 점수 가져오기
                t_score = trend_map[code].get('score', 0) if in_t else 0
                v_score = value_map[code].get('score', 0) if in_v else 0
                f_score = fin_map[code].get('total_score', fin_map[code].get('score', 0)) if in_f else 0

                # ── 점수 미달 스크리너는 플래그 해제 ──────────────────────
                # COMBO_TREND_SCORE_MIN: 8→10 (signal_logic.py v4 강화)
                if in_t and t_score < COMBO_TREND_SCORE_MIN:
                    in_t = False
                if in_v and v_score < COMBO_VALUE_SCORE_MIN:
                    in_v = False
                if in_f and f_score < COMBO_FIN_SCORE_MIN:
                    in_f = False

                cnt = (1 if in_t else 0) + (1 if in_v else 0) + (1 if in_f else 0)
                if cnt < 2:
                    continue  # 유효 스크리너 2개 미만이면 제외

                # ── 추세 없이 가치+재무만은 제외 (모멘텀 필수) ────────────
                if not in_t and cnt == 2:
                    continue

                # ★ 하락장(KOSPI < MA60)에서는 신규 콤보 편입 차단 ──────────
                if not _kospi_bullish:
                    continue

                base = (trend_map.get(code) or value_map.get(code) or fin_map.get(code))
                if not base:
                    continue

                # 합산 점수 (정규화)
                combo_score = (t_score/14 + v_score/9 + f_score/30) * 10

                combo.append({**base, 'match_count': cnt,
                              'in_trend': in_t, 'in_value': in_v, 'in_fin': in_f,
                              'trend_score': t_score, 'value_score': v_score, 'fin_score': f_score,
                              'combo_score': round(combo_score, 1)})

            combo.sort(key=lambda x: (x['match_count'], x.get('combo_score', 0)), reverse=True)
            _signal_cache['combo_candidates'] = {'data': combo, 'at': _t.time()}

            logger.info(f"[스크리너사전계산] 추세={len(trend_data)} 가치={len(value_data)} 재무={len(fin_data)} 적극검토={len(combo)}")

            # Logic-#2 수급 주도 모멘텀 사전계산
            try:
                from signal_engine import calc_combo_v2
                import sqlite3 as _sl2
                conn_v2 = _sl2.connect("stock.db")
                combo_v2 = calc_combo_v2(conn_v2)
                conn_v2.close()
                _signal_cache['combo_v2'] = {'data': combo_v2, 'at': _t.time()}
                logger.info(f"[스크리너사전계산] Logic-#2 수급모멘텀={len(combo_v2)}")
            except Exception as _e2:
                logger.error(f"[스크리너사전계산] Logic-#2 오류: {_e2}")

            # 5. AI 적극검토 자동매매 처리 (장중 여부 무관하게 실행 — 종가 기반 가상매매)
            if combo:
                _process_ai_combo_autotrade(combo)

            # 6. 시장지표(investor-top) 사전 계산 — 탭 진입 즉시 로딩되도록
            try:
                from routes.market_indicators import precompute_indicator_cache
                precompute_indicator_cache()
            except Exception as _e3:
                logger.error(f"[스크리너사전계산] 시장지표캐시 오류: {_e3}")

    except Exception as e:
        logger.error(f"[스크리너사전계산] {e}", exc_info=True)


def _process_ai_combo_autotrade(combo_stocks: list):
    """AI 적극검토 종목 자동매매: 신규 편입 시 매수, 추세이탈 시 매도."""
    import sqlite3 as _sl
    from datetime import datetime as _dt

    HARD_STOP_LOSS_PCT = -20.0  # AI 추천 탭 하드 손절선: -20%

    conn = _sl.connect("stock.db")
    try:
        # 현재 AI_COMBO 전략으로 보유 중인 종목
        active = {r[0]: r for r in conn.execute(
            "SELECT stock_name, buy_price, quantity, id, COALESCE(stock_code,'') FROM peak_holding WHERE strategy='ai_combo' AND is_active=1"
        ).fetchall()}

        # 신규 편입: 3개 충족 우선, 그 다음 2개 충족 (최대 10종목)
        sorted_combo = sorted(combo_stocks, key=lambda x: (x['match_count'], x.get('combo_score', 0)), reverse=True)

        for s in sorted_combo[:10]:  # 최대 10종목
            name = s.get('stock_name') or s.get('stock_code')
            code = s.get('stock_code', '')
            if name in active:
                continue

            # 현재가 조회
            price_row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            if not price_row:
                continue
            price = price_row[0]
            if price <= 0:
                continue

            MAX_BUDGET = 10_000_000
            qty = max(1, int(MAX_BUDGET / price))
            amount = price * qty

            entry_date = _dt.now().strftime("%Y-%m-%d")
            sector = s.get('sector', '')
            match_cnt = s.get('match_count', 2)

            # 중복 방지
            dup = conn.execute(
                "SELECT id FROM peak_holding WHERE stock_name=? AND strategy='ai_combo' AND entry_date=?",
                (name, entry_date)
            ).fetchone()
            if dup:
                continue

            # DB 기록
            conn.execute("""
                INSERT INTO peak_holding
                (stock_code, stock_name, sector, buy_price, current_price, quantity, entry_date,
                 hold_days, profit_pct, is_active, strategy, detected_at, updated_at)
                VALUES (?,?,?,?,?,?,?,0,0.0,1,'ai_combo',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """, (code, name, sector, price, price, qty, entry_date))
            conn.execute("""
                INSERT INTO peak_trade (stock_name, tx_type, price, quantity, amount, profit_pct, entry_date, tx_date, strategy)
                VALUES (?,?,?,?,?,0.0,?,?,'ai_combo')
            """, (name, 'buy', price, qty, round(amount), entry_date, entry_date))
            conn.commit()

            # 텔레그램 알림
            tags_str = ' | '.join((s.get('reasons') or s.get('tags') or [])[:4])
            cats = []
            if s.get('in_trend'): cats.append('추세')
            if s.get('in_value'): cats.append('가치')
            if s.get('in_fin'):   cats.append('재무')

            msg = (
                f"[AI 적극검토 매수신호]\n"
                f"종목: {name} ({code})\n"
                f"카테고리: {' + '.join(cats)} ({match_cnt}개 동시충족)\n"
                f"매수가: {price:,.0f}원  수량: {qty}주  금액: {amount/10000:.0f}만원\n"
                f"섹터: {sector or '-'}\n"
                f"근거: {tags_str}\n"
                f"전략: 시장가 매수 -> 추세이탈 시 매도\n"
                f"실제 주문 아님 - 검토 후 수동 집행"
            )
            _send_telegram(msg, dedup_key=f"ai_combo_buy_{code}_{entry_date}")
            logger.info(f"[AI자동매매] 매수신호: {name}({code}) {price:,.0f}원 x {qty}주")

        # 추세이탈 체크: 보유 중인 AI_COMBO 종목 중 정배열 깨진 것
        for name, (sname, buy_price, qty, hold_id, hold_code) in active.items():
            # stock_code는 보유행 우선 사용 (이름 매칭 실패로 매도 누락 방지)
            code = str(hold_code or "").strip()
            if not code:
                code_row = conn.execute(
                    "SELECT su.stock_code FROM stock_universe su WHERE su.stock_name=? LIMIT 1",
                    (name,)
                ).fetchone()
                if not code_row:
                    continue
                code = code_row[0]

            closes = [r[0] for r in conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 65",
                (code,)
            ).fetchall()]
            if len(closes) < 20:
                continue

            curr  = closes[0]
            prev  = closes[1] if len(closes) > 1 else curr
            ma20  = sum(closes[:20]) / 20
            ma60  = sum(closes[:60]) / 60 if len(closes) >= 60 else ma20
            ma20_prev = sum(closes[1:21]) / 20 if len(closes) >= 21 else ma20

            profit_pct = round((curr - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0

            # 추세이탈 조건:
            # ① MA20 2일 연속 하단 이탈 (노이즈 제거)
            ma20_break_2d = (curr < ma20 * 0.99) and (prev < ma20_prev * 0.99)
            # ② 하드 손절 (손실 제한)
            hard_stop_loss = profit_pct <= HARD_STOP_LOSS_PCT
            # ③ MA60 하단 이탈 (장기 추세 붕괴)
            ma60_break = curr < ma60 * 0.98

            trend_broken = ma20_break_2d or hard_stop_loss or ma60_break

            if trend_broken:
                profit_amt = round((curr - buy_price) * qty)

                conn.execute("""
                    UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=CURRENT_TIMESTAMP,
                    current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (curr, curr, profit_pct, hold_id))
                conn.execute("""
                    INSERT INTO peak_trade (stock_name, tx_type, price, quantity, amount, profit_pct, tx_date, strategy)
                    VALUES (?,?,?,?,?,?,CURRENT_DATE,'ai_combo')
                """, (name, 'sell', curr, qty, round(curr*qty), profit_pct))
                conn.commit()

                exit_reason = ('하드손절(-20%)' if hard_stop_loss else
                               'MA20 2일 연속 이탈' if ma20_break_2d else
                               'MA60 장기추세 붕괴')
                msg = (
                    f"[AI 적극검토 추세이탈 매도]\n"
                    f"종목: {name} ({code})\n"
                    f"매수가: {buy_price:,.0f}원 → 현재가: {curr:,.0f}원\n"
                    f"수익률: {profit_pct:+.2f}%  손익: {profit_amt:+,}원\n"
                    f"이탈사유: {exit_reason} (MA20={ma20:,.0f} MA60={ma60:,.0f})\n"
                    f"실제 주문 아님 - 검토 후 수동 집행"
                )
                _send_telegram(msg, dedup_key=f"ai_combo_sell_{code}_{_dt.now().strftime('%Y-%m-%d')}")
                logger.info(f"[AI자동매매] 매도신호(추세이탈): {name}({code}) {curr:,.0f}원 {profit_pct:+.1f}%")
    finally:
        conn.close()




@app.on_event("startup")
async def startup_event():
    from notifier import load_history as _notifier_load
    _notifier_load()
    _scheduler.start()

    # 서버 시작 직후 시그널 캐시 즉시 워밍업 (프론트 첫 로딩 시 10초 대기 방지)
    def _warm_cache():
        _tm.sleep(3)  # 서버 완전 기동 대기
        logger.info("[캐시워밍] 시작 — 시장시그널·스크리너 사전계산")
        _run_screener_precompute()
        logger.info("[캐시워밍] 완료")

    _th.Thread(target=_warm_cache, daemon=True, name="StartupCacheWarm").start()
    logger.info("서버 시작 완료")


@app.on_event("shutdown")
async def shutdown_event():
    _scheduler.stop()
    logger.info("서버 종료 — 스케줄러 중지")

# [CORS] 로컬 개발 + Cloudflare Tunnel 외부 접속 허용.
# Duck DNS / Cloudflare Tunnel 도메인은 .env의 ALLOWED_ORIGINS에 추가하세요.
# 예: ALLOWED_ORIGINS=https://yourname.duckdns.org,https://xxxx.trycloudflare.com
import os as _os
_extra_origins = [o.strip() for o in _os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
] + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/realtime/prices")
def get_realtime_prices(db: Session = Depends(get_db)):
    """
    포트폴리오 전 종목의 최신 현재가 및 등락률을 반환합니다.
    프론트엔드 1분 폴링 전용 경량 API.
    반환: { stock_code: { current_price, change_pct, profit, profit_pct, total_value } }
    """
    holdings = db.query(models.Portfolio).filter(models.Portfolio.quantity > 0).all()
    result = {}
    total_buy_sum   = 0.0
    total_value_sum = 0.0
    total_profit_sum = 0.0

    for h in holdings:
        # 최신 거래일 vs 직전 거래일 기준 (휴장일 포함, 같은 날짜 중복행 비교 방지)
        rows = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == h.stock_code,
            models.PriceHistory.close > 0,
        ).order_by(models.PriceHistory.date.desc()).limit(20).all()

        current_price = h.avg_price
        prev_price = h.avg_price
        price_date = ""
        if rows:
            current_price = rows[0].close
            if hasattr(rows[0].date, "strftime"):
                price_date = rows[0].date.strftime("%Y-%m-%d %H:%M")
            else:
                price_date = str(rows[0].date)
            latest_day = str(rows[0].date)[:10]
            prev_candidate = None
            for rr in rows[1:]:
                if str(rr.date)[:10] < latest_day:
                    prev_candidate = rr
                    break
            prev_price = prev_candidate.close if prev_candidate else current_price

        change_pct  = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
        profit      = round((current_price - h.avg_price) * h.quantity)
        profit_pct  = round((current_price - h.avg_price) / h.avg_price * 100, 2) if h.avg_price else 0.0
        total_value = round(current_price * h.quantity)
        buy_total   = round(h.avg_price   * h.quantity)

        result[h.stock_code] = {
            "current_price": current_price,
            "change_pct":    change_pct,
            "profit":        profit,
            "profit_pct":    profit_pct,
            "total_value":   total_value,
            "buy_total":     buy_total,
            "price_date":    price_date,
        }
        total_buy_sum    += buy_total
        total_value_sum  += total_value
        total_profit_sum += profit

    total_profit_pct = round(total_profit_sum / total_buy_sum * 100, 2) if total_buy_sum else 0.0
    return {
        "holdings":         result,
        "summary": {
            "total_buy":        round(total_buy_sum),
            "total_value":      round(total_value_sum),
            "total_profit":     round(total_profit_sum),
            "total_profit_pct": total_profit_pct,
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_open": _is_market_hours(),
    }


@app.get("/api/realtime/macro")
def get_realtime_macro(db: Session = Depends(get_db)):
    """
    매크로 지표 최신값 반환 (300초 폴링용).
    - 장 시간: Yahoo Finance 즉시 갱신 후 DB 최신값 반환
    - 장 외: DB 저장된 최신값 바로 반환
    반환: { index:{KOSPI,KOSDAQ}, vix:{...}, commodities:{...} }
    """
    if _is_market_hours():
        try:
            _realtime_fetch_macro(db)
        except Exception as e:
            logger.warning(f"[realtime/macro] Yahoo 갱신 실패 (DB값 반환): {e}")
    result = processor.get_macro_status(db)
    result.setdefault("index",       {})
    result.setdefault("vix",         {"value": 0, "change": 0, "date": "-", "history": []})
    result.setdefault("commodities", {})
    return result





@app.get("/")
def read_root():
    return {"message": "주식 분석 백엔드 서버가 작동 중입니다."}


@app.get("/api/dashboard/market-info/{stock_code}")
def get_market_info(stock_code: str, refresh: bool = False, db: Session = Depends(get_db)):
    """
    종목의 시장정보 반환.
    refresh=true 이면 캐시 무시하고 재조회.
    """
    if not (stock_code.isdigit() and len(stock_code) == 6):
        return {"market": None, "mktcap": None, "mktcap_rank": None}

    # ── 캐시 확인 (1시간 유효) ──────────────────────────────────
    cached = _market_info_cache.get(stock_code, {})
    cache_age = _tm.time() - cached.get("cached_at", 0)
    if cached and cache_age < 3600 and not refresh:
        return cached

    result = {"market": None, "mktcap": None, "mktcap_rank": None, "stock_name": None}

    # ── 시장구분: DB 먼저 확인 ──────────────────────────────────
    meta = db.query(models.StockMeta).filter(
        models.StockMeta.stock_code == stock_code
    ).first()
    if meta:
        result["market"]     = meta.market
        result["stock_name"] = meta.stock_name

    # ── 네이버 금융 스크래핑 (시총·순위·시장구분) ───────────────
    try:
        import requests as _req, re as _re_mi
        from bs4 import BeautifulSoup as _BS
        res = _req.get(
            f"https://finance.naver.com/item/main.naver?code={stock_code}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
                     "Referer": "https://finance.naver.com/"},
            timeout=8,
        )
        res.encoding = res.apparent_encoding  # ★ UTF-8 자동 감지
        soup = _BS(res.text, "html.parser")

        # 시장구분 + 시총순위 — "코스피1위" 형식의 td에서 한번에 파싱
        for tag in soup.find_all("td"):
            txt = tag.get_text(strip=True)
            if "코스피" in txt and len(txt) < 20:
                result["market"] = "KOSPI"
                rank_txt = txt.replace("코스피","").replace("위","").replace(",","").strip()
                try: result["mktcap_rank"] = int(rank_txt)
                except: pass
                break
            elif "코스닥" in txt and len(txt) < 20:
                result["market"] = "KOSDAQ"
                rank_txt = txt.replace("코스닥","").replace("위","").replace(",","").strip()
                try: result["mktcap_rank"] = int(rank_txt)
                except: pass
                break

        # 시장구분 fallback — 타이틀에서 파싱
        if not result["market"]:
            title = soup.find("title")
            title_txt = title.text if title else ""
            if "코스피" in title_txt:   result["market"] = "KOSPI"
            elif "코스닥" in title_txt: result["market"] = "KOSDAQ"

        # 종목명
        for sel in ["div.wrap_company h2 a", "h2.h_company a", "h2.h_company", "title"]:
            t = soup.select_one(sel)
            if t:
                nm = t.get_text(strip=True)
                if ":" in nm: nm = nm.split(":")[0].strip()
                if nm and len(nm) < 30:
                    result["stock_name"] = nm
                    break

        # 시가총액 — table.tb_type1 에서 "시가총액(억)" 행
        for tr in soup.select("table.tb_type1 tr"):
            th = tr.select_one("th"); td = tr.select_one("td")
            if not (th and td): continue
            th_txt = th.get_text(strip=True)
            td_txt = td.get_text(strip=True).replace(",","")
            if "시가총액" in th_txt:
                try:
                    result["mktcap"] = int(td_txt.replace("억","").strip())
                    break
                except: pass

    except Exception as e:
        logger.warning(f"[MarketInfo] {stock_code} 네이버 스크래핑 오류: {e}")

    # ── 시장구분 DB 저장 (최초 1회) ─────────────────────────────
    if result["market"] and not meta:
        try:
            db.add(models.StockMeta(
                stock_code=stock_code,
                stock_name=result.get("stock_name"),
                market=result["market"],
            ))
            db.commit()
            logger.info(f"[MarketInfo] {stock_code} 시장구분 저장: {result['market']}")
        except Exception as e:
            db.rollback()
            logger.warning(f"[MarketInfo] DB 저장 오류: {e}")

    # 캐시 저장
    result["cached_at"] = _tm.time()
    _market_info_cache[stock_code] = result
    return result

# --- 대시보드 및 결과 조회 API ---

@app.get("/api/dashboard/chart/{stock_code}")
def get_stock_chart(stock_code: str, days: int = 30, db: Session = Depends(get_db)):
    """
    특정 종목의 차트 시계열 데이터를 반환합니다.
    """
    return processor.get_chart_data(db, stock_code, days)

@app.get("/api/dashboard/sectors")
def get_sectors(db: Session = Depends(get_db)):
    """
    섹터별 등락 현황을 반환합니다.
    """
    return processor.get_sector_performance(db)

@app.get("/api/dashboard/screening/triple")
def get_triple_screening(db: Session = Depends(get_db)):
    """소외 턴어라운드 + 성장 기울기 스크리너 (캐시 우선)."""
    import time as _t
    cached = _signal_cache.get('fin_screener', {})
    ttl = 1800 if _is_market_hours() else 14400
    if cached and (_t.time() - cached.get('at', 0)) < ttl:
        return cached['data']
    result = screener.advanced_screening()
    _signal_cache['fin_screener'] = {'data': result, 'at': _t.time()}
    return result


@app.get("/api/dashboard/screening/logic")
def get_screening_logic():
    """스크리너 로직/원리 설명 반환."""
    return {"doc": screener.SCREENER_LOGIC_DOC}

# --- AI 분석 리포트 API ---

@app.post("/api/reports/generate/{stock_code}")
def generate_report(stock_code: str, db: Session = Depends(get_db)):
    """
    특정 종목의 AI 분석 리포트를 생성합니다.
    """
    analyzer = ai_analyzer.AIAnalyzer(db)
    report = analyzer.generate_stock_report(stock_code)
    return report

@app.get("/api/reports/latest/{stock_code}")
def get_latest_report(stock_code: str, db: Session = Depends(get_db)):
    """
    최근 생성된 AI 리포트 정보를 조회합니다.
    리포트가 없으면 404 대신 null을 반환하여 프론트 fetch가 실패하지 않도록 합니다.
    """
    analyzer = ai_analyzer.AIAnalyzer(db)
    report = analyzer.get_latest_report(stock_code)
    if not report:
        return None  # 404 대신 200 + null → fetchStockDetail 전체가 중단되지 않음
    return report

@app.get("/api/dashboard/financial-table/{stock_code}")
def get_financial_table(stock_code: str, type: str = "annual", report_type: str = "CFS", db: Session = Depends(get_db)):
    """재무제표 반환. ?type=annual(기본,연간5년) 또는 ?type=quarter(분기8개) &report_type=CFS|OFS"""
    return processor.get_financial_summary(db, stock_code, data_type=type, report_type=report_type)


_cf_collecting: set = set()   # 현금흐름 백그라운드 수집 중인 종목코드

def _bg_collect_cashflow(stock_code: str):
    """현금흐름표 백그라운드 수집 (별도 DB 세션)."""
    if stock_code in _cf_collecting:
        return
    _cf_collecting.add(stock_code)
    try:
        from database import SessionLocal as _SL
        _db = _SL()
        try:
            _collect_dart_cashflow(stock_code, _db, latest_only=False)
        finally:
            _db.close()
    except Exception as e:
        logger.warning(f"[CF-BG] {stock_code}: {e}")
    finally:
        _cf_collecting.discard(stock_code)


@app.get("/api/dashboard/cashflow/{stock_code}")
def get_cashflow_table(stock_code: str, type: str = "annual", report_type: str = "CFS", db: Session = Depends(get_db)):
    """
    현금흐름표 반환.
    ?type=annual  → 연간(최근 5개 연도, is_annual=True) — 연도당 1행 선택(capex 우선)
    ?type=quarter → 분기(최근 8분기, is_annual=False)
    ?report_type=CFS(기본,연결) | OFS(별도)
    DB 캐시 반환; 없으면 빈 배열 반환.
    """
    import sqlite3 as _sl
    _conn = _sl.connect("stock.db")
    _conn.row_factory = _sl.Row

    is_annual = (type != "quarter")
    # report_type 필터: CFS → CFS + NULL, OFS → OFS만
    if report_type == "OFS":
        _rt_sql = "AND (report_type = 'OFS')"
    else:
        _rt_sql = "AND (report_type = 'CFS' OR report_type IS NULL)"

    if is_annual:
        # 연간: DART 최우선 → FnGuide(연간CF 정확, capex 없음) → NULL Q4증분(capex 있으나 CF 오류)
        # 우선순위: ① data_source='dart' ② data_source='fnguide' ③ NULL(Q4증분) → 그 안에서 capex 있는 행
        raw = _conn.execute(f"""
            WITH ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY year
                        ORDER BY
                            -- OCF/ICF/FCF 모두 0이면 수집 오류 가능성 → 최우선 후순위
                            CASE WHEN (operating_cf = 0 AND investing_cf = 0 AND financing_cf = 0) THEN 1 ELSE 0 END,
                            CASE WHEN data_source='dart' THEN 0 WHEN data_source='fnguide' THEN 1 ELSE 2 END,
                            CASE WHEN capex IS NOT NULL THEN 0 ELSE 1 END,
                            (CASE WHEN operating_cf IS NULL THEN 1 ELSE 0 END +
                             CASE WHEN investing_cf IS NULL THEN 1 ELSE 0 END +
                             CASE WHEN financing_cf IS NULL THEN 1 ELSE 0 END),
                            id DESC
                    ) rn
                FROM cash_flow_data
                WHERE stock_code = ? AND is_annual = 1 {_rt_sql}
            )
            SELECT * FROM ranked WHERE rn = 1
            ORDER BY year DESC
        """, (stock_code,)).fetchall()
        # 필드별 최우선 값 맵: ranked CTE는 OCF 기준 최선 행 1개만 선택하므로
        # capex/cash_end/depr이 다른 행에 있을 경우 별도로 수집
        _annual_depr = {}
        _annual_capex_map = {}
        _annual_cash_end = {}
        for ar in _conn.execute(f"""
            SELECT year, data_source, depreciation, capex, cash_end
            FROM cash_flow_data
            WHERE stock_code=? AND is_annual=1 {_rt_sql}
            ORDER BY year,
                CASE WHEN data_source='dart' THEN 0 WHEN data_source='fnguide' THEN 1 ELSE 2 END,
                id DESC
        """, (stock_code,)).fetchall():
            yr = ar['year']
            if ar['depreciation'] is not None and yr not in _annual_depr:
                _annual_depr[yr] = ar['depreciation']
            if ar['capex'] is not None and yr not in _annual_capex_map:
                _annual_capex_map[yr] = ar['capex']
            # cash_end=0은 미수집(DART 파싱실패)일 가능성이 높으므로 비어있는 것으로 취급
            if ar['cash_end'] is not None and ar['cash_end'] != 0 and yr not in _annual_cash_end:
                _annual_cash_end[yr] = ar['cash_end']
    else:
        raw = _conn.execute(f"""
            SELECT * FROM cash_flow_data
            WHERE stock_code = ? AND is_annual = 0 {_rt_sql}
            ORDER BY year DESC, quarter DESC
        """, (stock_code,)).fetchall()

        # Q4 추론용: 필드별로 non-null 값이 있는 최우선 행에서 수집
        # capex/depr/cash_end은 DART > NULL(Q4증분) > FnGuide 순 (FnGuide annual=0 행은 이 필드 없음)
        _annual_depr = {}
        _annual_capex_map = {}
        _annual_cash_end = {}   # Q4 기말현금: stock 변수이므로 annual = Q4 기말값

        for ar in _conn.execute(f"""
            SELECT year, data_source, quarter, depreciation, capex, cash_end
            FROM cash_flow_data
            WHERE stock_code=? AND is_annual=1 {_rt_sql}
            ORDER BY year,
                CASE WHEN data_source='dart' THEN 0 WHEN data_source='fnguide' THEN 1 ELSE 2 END,
                id DESC
        """, (stock_code,)).fetchall():
            yr = ar['year']
            if ar['depreciation'] is not None and yr not in _annual_depr:
                _annual_depr[yr] = ar['depreciation']
            if ar['capex'] is not None and yr not in _annual_capex_map:
                _annual_capex_map[yr] = ar['capex']
            if ar['cash_end'] is not None and ar['cash_end'] != 0 and yr not in _annual_cash_end:
                _annual_cash_end[yr] = ar['cash_end']

        # Q3 누적값 캐싱 (Q4 추론 = annual - Q3_cumulative)
        _q3_depr = {}
        _q3_capex = {}
        _q3_cash_end = {}   # Q4 기말현금 역산: annual_cash_end - Q3_cash_end
        for qr in raw:
            if qr['quarter'] == 3:
                if qr['depreciation'] is not None:
                    _q3_depr[qr['year']] = qr['depreciation']
                if qr['capex'] is not None:
                    _q3_capex[qr['year']] = qr['capex']
                if qr['cash_end'] is not None:
                    _q3_cash_end[qr['year']] = qr['cash_end']

    _conn.close()

    if not raw:
        return []

    def _uk(v):
        if v is None: return None
        try: return round(float(v) / 1e8, 0)
        except: return None

    def _col_annual(r, key):
        """연간: base 컬럼 → None이면 _q fallback."""
        v = r[key] if key in r.keys() else None
        if v is None:
            qkey = f"{key}_q"
            v = r[qkey] if qkey in r.keys() else None
        return v

    def _col_quarter(r, key):
        """분기: _q 컬럼(분기값) 우선 → None이면 base 컬럼(누적값) fallback.
        _q 컬럼이 있을 때 base 컬럼은 YTD 누적값이므로 절대 우선 사용하면 안 됨."""
        qkey = f"{key}_q"
        if qkey in r.keys() and r[qkey] is not None:
            return r[qkey]
        return r[key] if key in r.keys() else None

    # 분기 감가상각비: _q 컬럼 없으므로 누적→분기 변환 필요 (prev_quarter 누적 차감)
    # raw는 year DESC, quarter DESC 정렬 → reversed 후 순차 처리로 prev 추적
    _prev_cumul = {}  # (year, field) → 직전 분기 누적값

    result = []
    for r in reversed(raw):
        if is_annual:
            period = f"{r['year']}년"
        elif r['quarter'] and r['quarter'] > 0:
            period = f"{str(r['year'])[2:]}년{r['quarter']}Q"
        else:
            period = f"{r['year']}Q?"

        if is_annual:
            ocf     = _col_annual(r, 'operating_cf')
            icf     = _col_annual(r, 'investing_cf')
            fcf_cf  = _col_annual(r, 'financing_cf')
            capex_v = _col_annual(r, 'capex')
            depr_v  = _col_annual(r, 'depreciation')
            # 연간 fallback: ranked 행에 없으면 필드별 최우선 행에서 보완
            yr = r['year']
            if capex_v is None and yr in _annual_capex_map:
                capex_v = _annual_capex_map[yr]
            if depr_v is None and yr in _annual_depr:
                depr_v = _annual_depr[yr]
        else:
            # 흐름 항목: _q 컬럼 우선 (분기 파생값)
            ocf     = _col_quarter(r, 'operating_cf')
            icf     = _col_quarter(r, 'investing_cf')
            fcf_cf  = _col_quarter(r, 'financing_cf')
            capex_v = _col_quarter(r, 'capex')

            # 감가상각비: _q 컬럼 없음 → 누적에서 전분기 누적 차감
            yr, qtr = r['year'], r['quarter']
            depr_cum = r['depreciation'] if 'depreciation' in r.keys() else None
            if depr_cum is not None and qtr and qtr > 1:
                prev_cum = _prev_cumul.get((yr, 'depreciation'))
                depr_v = (depr_cum - prev_cum) if prev_cum is not None else depr_cum
            else:
                depr_v = depr_cum  # Q1은 누적=분기
            # 이번 분기 누적값 저장 (다음 분기 차감용)
            if depr_cum is not None and qtr:
                _prev_cumul[(yr, 'depreciation')] = depr_cum

            # Q4 추론: Annual - Q3_cumulative
            if r['quarter'] == 4:
                if depr_v is None and yr in _annual_depr and yr in _q3_depr:
                    depr_v = _annual_depr[yr] - _q3_depr[yr]
                if capex_v is None and yr in _annual_capex_map and yr in _q3_capex:
                    capex_v = _annual_capex_map[yr] - _q3_capex[yr]

        cash_v = r['cash_end'] if 'cash_end' in r.keys() else None
        # cash_end=0은 미수집으로 취급해 fallback 허용
        if cash_v is None or cash_v == 0:
            yr = r['year']
            if yr in _annual_cash_end:
                # 연간: ranked 행에 cash_end 없으면 필드별 최우선 행에서 보완
                # 분기 Q4: 기말현금은 stock 변수이므로 연간 기말값 = Q4 기말값 (역산 아님)
                cash_v = _annual_cash_end[yr]
        free_cf = None
        if ocf is not None and capex_v is not None:
            free_cf = ocf - capex_v

        result.append({
            "period":       period,
            "operating_cf": _uk(ocf),
            "investing_cf": _uk(icf),
            "financing_cf": _uk(fcf_cf),
            "capex":        _uk(capex_v),
            "free_cf":      _uk(free_cf),
            "cash_end":     _uk(cash_v),
            "depreciation": _uk(depr_v),
        })
    return result


@app.post("/api/commands/refresh-cashflow/{stock_code}")
def refresh_cashflow(stock_code: str, db: Session = Depends(get_db)):
    """현금흐름표 강제 재수집."""
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        raise HTTPException(status_code=400, detail="국내 종목코드(6자리)만 지원합니다.")
    db.query(models.CashFlowData).filter(
        models.CashFlowData.stock_code == stock_code
    ).delete(synchronize_session=False)
    db.commit()
    saved = _collect_dart_cashflow(stock_code, db, latest_only=False)
    return {"status": "ok", "saved": saved}


# ── 전체 종목 유통주식수 배치 수집 ──────────────────────────────────
_float_batch_status: dict = {"running": False, "done": 0, "total": 0, "failed": 0, "started_at": None}

@app.get("/api/commands/batch-float-shares/status")
def batch_float_shares_status():
    """배치 수집 진행 상황."""
    return dict(_float_batch_status)


@app.post("/api/commands/batch-float-shares")
def start_batch_float_shares(force: bool = False):
    """
    전체 종목 유통주식수·발행주식수 배치 수집.
    - price_history에 있는 모든 6자리 종목코드 대상
    - yfinance 병렬 조회 (10 workers)
    - stock_meta에 upsert
    - force=true: 이미 수집된 종목도 재수집
    """
    global _float_batch_status
    if _float_batch_status["running"]:
        return {"status": "already_running", **_float_batch_status}

    import sqlite3 as _sl, concurrent.futures as _cf, yfinance as _yf
    from datetime import datetime as _dtnow, timedelta as _td

    def _run():
        global _float_batch_status
        conn = _sl.connect("stock.db")
        # 대상 종목: price_history 최근 90일 활성 종목
        rows = conn.execute("""
            SELECT DISTINCT p.stock_code
            FROM price_history p
            WHERE p.date >= date('now','-90 days')
              AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            ORDER BY p.stock_code
        """).fetchall()
        codes = [r[0] for r in rows]

        if not force:
            # 이미 수집된 종목 제외 (30일 이내)
            cutoff = (_dtnow.now() - _td(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            done_set = set(
                r[0] for r in conn.execute(
                    "SELECT stock_code FROM stock_meta WHERE float_shares IS NOT NULL AND float_updated_at > ?",
                    (cutoff,)
                ).fetchall()
            )
            codes = [c for c in codes if c not in done_set]

        _float_batch_status.update(running=True, done=0, failed=0,
                                   total=len(codes),
                                   started_at=_dtnow.now().isoformat())
        logger.info(f"[BatchFloat] 시작: {len(codes)}종목")

        import time as _time

        def _fetch_one(code):
            """KS → KQ 순으로 시도, floatShares 또는 sharesOutstanding 중 하나라도 있으면 저장."""
            fs = so = None
            for suffix in [".KS", ".KQ"]:
                try:
                    info = _yf.Ticker(f"{code}{suffix}").info
                    fs = info.get("floatShares") or fs
                    so = info.get("sharesOutstanding") or so
                    if fs or so:
                        break
                except Exception:
                    pass
                _time.sleep(0.1)   # suffix간 간격

            if fs or so:
                try:
                    c2 = _sl.connect("stock.db")
                    existing = c2.execute(
                        "SELECT id FROM stock_meta WHERE stock_code=?", (code,)
                    ).fetchone()
                    now_s = _dtnow.now().strftime("%Y-%m-%d %H:%M:%S")
                    if existing:
                        c2.execute(
                            "UPDATE stock_meta SET float_shares=?, shares_outstanding=?, float_updated_at=? WHERE stock_code=?",
                            (fs, so, now_s, code)
                        )
                    else:
                        c2.execute(
                            "INSERT INTO stock_meta (stock_code, float_shares, shares_outstanding, float_updated_at) VALUES (?,?,?,?)",
                            (code, fs, so, now_s)
                        )
                    c2.commit(); c2.close()
                    return True
                except Exception as e:
                    logger.debug(f"[BatchFloat] {code} DB저장실패: {e}")
            return False

        # 5 workers + 0.3s per-worker delay → ~60종목/분, rate limit 회피
        with _cf.ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_fetch_one, code): code for code in codes}
            for fut in _cf.as_completed(futures):
                ok = fut.result()
                if ok:
                    _float_batch_status["done"] += 1
                else:
                    _float_batch_status["failed"] += 1
                _time.sleep(0.05)   # 메인 스레드 스로틀

        conn.close()
        _float_batch_status["running"] = False
        logger.info(f"[BatchFloat] 완료: {_float_batch_status['done']}성공 / {_float_batch_status['failed']}실패")

    _th.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": f"백그라운드 수집 시작. /api/commands/batch-float-shares/status 로 진행 확인"}


@app.post("/api/commands/refresh-annual/{stock_code}")
def refresh_annual_financials(stock_code: str, db: Session = Depends(get_db)):
    """
    연간 재무 데이터(is_annual=True) 강제 재수집.
    - 기존 is_annual 레코드 삭제 후 DART에서 재수집
    - 분기값이 잘못 저장된 경우 수동으로 정정할 때 사용
    """
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        raise HTTPException(status_code=400, detail="국내 종목코드(6자리)만 지원합니다.")

    # 기존 is_annual=True 레코드 삭제
    deleted = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code,
        models.FinancialData.is_annual.is_(True),
    ).delete(synchronize_session=False)
    db.commit()
    logger.info(f"[연간재수집] {stock_code}: 기존 연간 레코드 {deleted}건 삭제")

    # DART에서 재수집
    saved = _collect_dart_to_db(stock_code, db, latest_only=False)
    db.expire_all()

    return {
        "status":  "ok",
        "deleted": deleted,
        "saved":   saved,
        "message": f"{deleted}건 삭제 후 {saved}건 재수집 완료",
    }


# ── 수동 트리거: 월간 배치 / 공시체크 ───────────────────────────────────

_bulk_update_running: bool = False

@app.post("/api/commands/monthly-bulk-update")
def trigger_monthly_bulk_update():
    """월간 전체 종목 재무/CF/유통주식수 배치 수집 수동 트리거."""
    global _bulk_update_running
    if _bulk_update_running:
        return {"status": "already_running"}
    def _run():
        global _bulk_update_running
        _bulk_update_running = True
        try:
            _monthly_bulk_update()
        finally:
            _bulk_update_running = False
    _th.Thread(target=_run, daemon=True, name="ManualMonthlyBulk").start()
    return {"status": "started", "message": "월간 배치 수집 시작. 완료까지 수 시간 소요될 수 있습니다."}


@app.get("/api/commands/monthly-bulk-update/status")
def monthly_bulk_update_status():
    """월간 배치 실행 여부 확인."""
    return {"running": _bulk_update_running}


@app.post("/api/commands/daily-disclosure-check")
def trigger_disclosure_check():
    """DART 공시 기반 당일 재무 업데이트 수동 트리거."""
    def _run():
        _daily_disclosure_check()
    _th.Thread(target=_run, daemon=True, name="ManualDisclosureCheck").start()
    return {"status": "started", "message": "공시 체크 시작"}


@app.post("/api/commands/screener-refresh")
def trigger_screener_refresh():
    """AI 스크리너 캐시 즉시 재계산 수동 트리거 (진입트리거 TOP20 포함)."""
    import threading as _thr
    _thr.Thread(target=_run_screener_precompute, daemon=True, name="ManualScreenerRefresh").start()
    return {"status": "started", "message": "스크리너 재계산 시작 — 약 60~120초 후 새로고침 하세요."}


# ── 데이터 품질 등급 엔드포인트 ─────────────────────────────────────────
@app.get("/api/dashboard/data-quality/{stock_code}")
def get_data_quality(stock_code: str):
    """
    종목별 데이터 신뢰도 등급 반환.

    등급:
      A  완전검증   — 4중 검증 CONFIRMED, 핵심필드 NULL 없음
      B  핵심검증   — OCF/ICF CONFIRMED, D&A·CapEx 일부 누락
      C  부분검증   — AMBIGUOUS 존재 또는 핵심필드 일부 NULL
      D  검증미완   — 4중 검증 데이터 없음

    필드별 상태:
      confirmed  — 4중 검증 통과
      close_match— 오차범위 내 일치
      ambiguous  — 불일치 (재확인 필요)
      ok         — null 없음 (검증 미실시)
      missing    — null 또는 미수집
      na         — 구조적 한계 (DART 미제공)
    """
    import sqlite3 as _sl
    conn = _sl.connect("stock.db")
    conn.row_factory = _sl.Row

    # ── 1. 4중 검증 플래그 (전 연도 — 등급 판정은 2022+ 기준) ──────────
    # 등급 판정용: 최근 데이터(2022+) 기준
    flags = conn.execute("""
        SELECT flag_type, field, status, COUNT(*) AS cnt
        FROM cf_validation_flags
        WHERE stock_code=? AND year>=2022
        GROUP BY flag_type, field, status
    """, (stock_code,)).fetchall()

    # CF 플래그 (flag_type='MATCH') → CF 검증용
    flag_map = {}   # field -> {confirmed, ambiguous, close_match, total}
    # P&L 플래그 (flag_type='FIN_CROSS': DART×FnGuide, 'FIN_NAVER': DART×FnGuide×Naver)
    fin_cross_map = {}   # field -> {confirmed, ambiguous, close_match, total}
    fin_naver_map = {}   # field -> {confirmed, ambiguous, close_match, total}

    for f in flags:
        fd   = f["field"]
        ftyp = f["flag_type"] or "MATCH"
        st   = f["status"].lower()
        if ftyp == "MATCH":
            if fd not in flag_map:
                flag_map[fd] = {"confirmed": 0, "ambiguous": 0, "close_match": 0, "total": 0}
            flag_map[fd][st] = f["cnt"]
            flag_map[fd]["total"] += f["cnt"]
        elif ftyp == "FIN_CROSS":
            if fd not in fin_cross_map:
                fin_cross_map[fd] = {"confirmed": 0, "ambiguous": 0, "close_match": 0, "total": 0}
            fin_cross_map[fd][st] = f["cnt"]
            fin_cross_map[fd]["total"] += f["cnt"]
        elif ftyp == "FIN_NAVER":
            if fd not in fin_naver_map:
                fin_naver_map[fd] = {"confirmed": 0, "ambiguous": 0, "close_match": 0, "total": 0}
            fin_naver_map[fd][st] = f["cnt"]
            fin_naver_map[fd]["total"] += f["cnt"]

    has_validation = bool(flag_map) or bool(fin_cross_map) or bool(fin_naver_map)

    # 실제 검증된 연도 범위 (전 연도 조회 — 표시용)
    val_years_row = conn.execute("""
        SELECT MIN(year) AS min_yr, MAX(year) AS max_yr,
               GROUP_CONCAT(DISTINCT year ORDER BY year) AS years_csv
        FROM cf_validation_flags
        WHERE stock_code=?
    """, (stock_code,)).fetchone()
    if val_years_row and val_years_row["min_yr"]:
        _min_yr = val_years_row["min_yr"]
        _max_yr = val_years_row["max_yr"]
        _val_years = [int(y) for y in (val_years_row["years_csv"] or "").split(",") if y]
    else:
        _min_yr = _max_yr = None
        _val_years = []

    # 필드별 검증 연도 범위 (표시용)
    field_years_rows = conn.execute("""
        SELECT field, MIN(year) AS min_yr, MAX(year) AS max_yr, COUNT(DISTINCT year) AS yr_cnt
        FROM cf_validation_flags
        WHERE stock_code=?
        GROUP BY field
    """, (stock_code,)).fetchall()
    _field_yr_map = {r["field"]: (r["min_yr"], r["max_yr"], r["yr_cnt"]) for r in field_years_rows}

    def _field_status(fd):
        if fd not in flag_map:
            return "ok"
        m = flag_map[fd]
        if m["ambiguous"] > 0:
            return "ambiguous"
        if m["confirmed"] > 0 or m["close_match"] > 0:
            return "confirmed"
        return "ok"

    # ── 2. CF NULL 현황 (최근 3년 연간) ─────────────────────────────
    cf_rows = conn.execute("""
        SELECT year, depreciation, capex, operating_cf, investing_cf, financing_cf, cash_end
        FROM cash_flow_data
        WHERE stock_code=? AND is_annual=1 AND year>=2022
          AND (report_type='CFS' OR report_type IS NULL)
        ORDER BY year DESC LIMIT 3
    """, (stock_code,)).fetchall()

    def _null_rate(rows, col):
        if not rows: return 1.0
        return sum(1 for r in rows if r[col] is None) / len(rows)

    dep_null   = _null_rate(cf_rows, "depreciation")
    capex_null = _null_rate(cf_rows, "capex")
    ocf_null   = _null_rate(cf_rows, "operating_cf")
    icf_null   = _null_rate(cf_rows, "investing_cf")

    # ── 3. 재무제표 NULL 현황 (최근 3년 연간) ─────────────────────
    fin_rows = conn.execute("""
        SELECT year, revenue, operating_profit, net_income, total_assets, total_equity
        FROM financial_data
        WHERE stock_code=? AND is_annual=1 AND year>=2022
          AND (report_type='CFS' OR report_type IS NULL)
        ORDER BY year DESC LIMIT 3
    """, (stock_code,)).fetchall()

    rev_null  = _null_rate(fin_rows, "revenue")
    op_null   = _null_rate(fin_rows, "operating_profit")
    has_fin   = bool(fin_rows)

    # OFS 수집 여부
    ofs_cf  = conn.execute("SELECT COUNT(*) FROM cash_flow_data WHERE stock_code=? AND report_type='OFS' AND is_annual=1", (stock_code,)).fetchone()[0]
    ofs_fin = conn.execute("SELECT COUNT(*) FROM financial_data WHERE stock_code=? AND report_type='OFS' AND is_annual=1", (stock_code,)).fetchone()[0]

    # FIN_NAVER 검증 연도 범위 (표시용) — conn.close() 전에 조회
    naver_yr_rows = conn.execute("""
        SELECT MIN(year) AS mn, MAX(year) AS mx, COUNT(DISTINCT year) AS cnt
        FROM cf_validation_flags
        WHERE stock_code=? AND flag_type='FIN_NAVER'
    """, (stock_code,)).fetchone()

    # BS 검증 플래그 (FIN_CROSS: total_assets, total_equity)
    bs_flags = conn.execute("""
        SELECT field, status, ai_verdict, COUNT(*) AS cnt
        FROM cf_validation_flags
        WHERE stock_code=? AND flag_type='FIN_CROSS' AND year>=2022
          AND field IN ('total_assets','total_equity')
        GROUP BY field, status, ai_verdict
    """, (stock_code,)).fetchall()
    bs_map = {}  # field -> {confirmed, ambiguous, auto_fixed}
    for f in bs_flags:
        fd = f["field"]
        if fd not in bs_map:
            bs_map[fd] = {"confirmed": 0, "ambiguous": 0, "auto_fixed": 0}
        st = f["status"].lower()
        # AUTO_DART_TRUST = FnGuide 음수오류 → 자동해소
        if st == "confirmed" and f["ai_verdict"] and "AUTO" in (f["ai_verdict"] or ""):
            bs_map[fd]["auto_fixed"] += f["cnt"]
        if st == "confirmed":
            bs_map[fd]["confirmed"] += f["cnt"]
        elif st == "ambiguous":
            bs_map[fd]["ambiguous"] += f["cnt"]

    # revenue STRUCTURAL_DIFF 비율 계산
    rev_structural = conn.execute("""
        SELECT COUNT(*) FROM cf_validation_flags
        WHERE stock_code=? AND flag_type='FIN_NAVER' AND field='revenue' AND year>=2022
          AND ai_verdict='STRUCTURAL_DIFF_FINANCIAL_SECTOR'
    """, (stock_code,)).fetchone()[0]
    rev_total_naver = conn.execute("""
        SELECT COUNT(*) FROM cf_validation_flags
        WHERE stock_code=? AND flag_type='FIN_NAVER' AND field='revenue' AND year>=2022
    """, (stock_code,)).fetchone()[0]

    conn.close()

    # ── 4. 항목별 상태 계산 ──────────────────────────────────────────
    def _yr_suffix(field_key):
        """필드별 실제 검증 연도 범위 문자열 반환."""
        info = _field_yr_map.get(field_key)
        if not info:
            return ""
        min_yr, max_yr, cnt = info
        if min_yr == max_yr:
            return f" ({min_yr}년)"
        return f" ({min_yr}~{max_yr}년, {cnt}개년)"

    def _cf_item_status(field_key, null_rate, label_k):
        st = _field_status(field_key)
        yr = _yr_suffix(field_key)
        if null_rate >= 0.5:
            return {"field": label_k, "status": "missing",
                    "label": "❌ 미수집", "detail": f"최근 3년 중 {round(null_rate*100)}% null",
                    "sources": "—"}
        if st == "ambiguous":
            return {"field": label_k, "status": "ambiguous",
                    "label": "⚠️ 재확인 필요", "detail": f"소스 간 수치 불일치 (DART vs FnGuide/Seibro){yr}",
                    "sources": "DART+FnGuide+Seibro(3중)"}
        if st == "confirmed":
            return {"field": label_k, "status": "confirmed",
                    "label": "✅ 4중 검증 완료", "detail": f"DART·FnGuide·Seibro 교차검증 통과{yr}",
                    "sources": "DART+FnGuide+Seibro(3중)"}
        if has_validation:
            return {"field": label_k, "status": "ok",
                    "label": "✔ 검증됨", "detail": "",
                    "sources": "DART"}
        return {"field": label_k, "status": "ok",
                "label": "✔ 데이터 있음", "detail": "교차검증 미실시",
                "sources": "DART"}

    ocf_item  = _cf_item_status("operating_cf",  ocf_null,  "영업현금흐름 (OCF)")
    icf_item  = _cf_item_status("investing_cf",  icf_null,  "투자현금흐름 (ICF)")
    cash_item = _cf_item_status("cash_end",       0.0,      "기말현금")

    # D&A — 구조적 한계 명시
    if dep_null >= 0.8:
        dep_item = {"field": "감가상각비 (D&A)", "status": "na",
                    "label": "〰 구조적 미수집", "detail": "DART 간접법 묶음 표시 → 개별 추출 불가",
                    "sources": "DART"}
    elif dep_null >= 0.34:
        dep_item = {"field": "감가상각비 (D&A)", "status": "partial",
                    "label": "△ 부분 수집", "detail": f"최근 3년 중 {round(dep_null*100)}% null{_yr_suffix('depreciation')}",
                    "sources": "DART"}
    else:
        st = _field_status("depreciation")
        dep_item = {"field": "감가상각비 (D&A)",
                    "status": st if st != "ok" else "ok",
                    "label": "✅ 4중 검증 완료" if st == "confirmed" else "✔ 수집됨",
                    "detail": f"DART·FnGuide·Seibro 교차검증 통과{_yr_suffix('depreciation')}" if st == "confirmed" else "",
                    "sources": "DART+FnGuide+Seibro(3중)" if st == "confirmed" else "DART"}

    # CapEx
    if capex_null >= 0.5:
        capex_item = {"field": "설비투자 (CapEx)", "status": "missing",
                      "label": "❌ 미수집", "detail": f"최근 3년 중 {round(capex_null*100)}% null",
                      "sources": "—"}
    else:
        st = _field_status("capex")
        capex_item = {"field": "설비투자 (CapEx)",
                      "status": st if st != "ok" else "ok",
                      "label": "✅ 4중 검증 완료" if st == "confirmed" else "✔ 수집됨",
                      "detail": f"DART·FnGuide·Seibro 교차검증 통과{_yr_suffix('capex')}" if st == "confirmed" else "",
                      "sources": "DART+FnGuide+Seibro(3중)" if st == "confirmed" else "DART"}

    # 재무제표 (P&L/BS — FIN_CROSS: DART×FnGuide, FIN_NAVER: DART×FnGuide×Naver)
    def _fin_field_status(fd):
        """
        P&L 필드 상태: FIN_NAVER(3중) > FIN_CROSS(2중) > 없음 순으로 우선 판정.
        FIN_NAVER CONFIRMED가 있으면 FIN_CROSS AMBIGUOUS를 무시 (FIN_NAVER이 더 포괄적).
        FIN_NAVER AMBIGUOUS가 있으면 최종 AMBIGUOUS.
        FIN_NAVER 없고 FIN_CROSS AMBIGUOUS이면 AMBIGUOUS.
        """
        nm = fin_naver_map.get(fd, {})
        nc = fin_cross_map.get(fd, {})
        naver_confirmed = nm.get("confirmed", 0) > 0
        naver_ambiguous = nm.get("ambiguous", 0) > 0
        cross_ambiguous = nc.get("ambiguous", 0) > 0

        # FIN_NAVER AMBIGUOUS는 최우선 경고
        if naver_ambiguous:
            return "ambiguous"
        # FIN_NAVER CONFIRMED: 3중 검증 통과 → FIN_CROSS AMBIGUOUS 덮어씀
        if naver_confirmed:
            return "naver_confirmed"
        # FIN_NAVER 없거나 데이터 없음: FIN_CROSS 결과 사용
        if cross_ambiguous:
            return "ambiguous"
        if nc.get("confirmed", 0) > 0 or nc.get("close_match", 0) > 0:
            return "cross_confirmed"   # 2중 검증
        if nm.get("close_match", 0) > 0:
            return "naver_confirmed"   # close_match도 충분히 신뢰
        return "ok"

    fin_op_st  = _fin_field_status("operating_profit")
    fin_rev_st = _fin_field_status("revenue")
    fin_ni_st  = _fin_field_status("net_income")
    fin_yr     = _yr_suffix("operating_profit") or _yr_suffix("revenue")

    # FIN_NAVER 연도 표시용 (conn.close() 전에 조회한 naver_yr_rows 재사용)
    if naver_yr_rows and naver_yr_rows["mn"]:
        _nyr_min, _nyr_max, _nyr_cnt = naver_yr_rows["mn"], naver_yr_rows["mx"], naver_yr_rows["cnt"]
        _naver_yr_suffix = f" ({_nyr_min}~{_nyr_max}년, {_nyr_cnt}개년)" if _nyr_min != _nyr_max else f" ({_nyr_min}년)"
    else:
        _naver_yr_suffix = ""

    if not has_fin:
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "missing",
                    "label": "❌ 미수집", "detail": "DART 미등록 또는 미수집",
                    "sources": "—"}
    elif rev_null >= 0.5 or op_null >= 0.5:
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "partial",
                    "label": "△ 부분 수집", "detail": f"매출 {round(rev_null*100)}% null · 영업이익 {round(op_null*100)}% null",
                    "sources": "DART"}
    elif fin_op_st == "ambiguous" or fin_rev_st == "ambiguous":
        ambig_field = "영업이익" if fin_op_st == "ambiguous" else "매출"
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "ambiguous",
                    "label": f"⚠️ {ambig_field} 재확인 필요",
                    "detail": f"DART·FnGuide·Naver 수치 불일치{fin_yr}",
                    "sources": "DART+FnGuide+Naver"}
    elif fin_op_st == "naver_confirmed":
        # 영업이익 3중(DART·FnGuide·Naver) 검증 완료
        if rev_structural > 0 and rev_total_naver > 0:
            rev_note = f" · 매출 {rev_structural}/{rev_total_naver}건 구조적차이(금융업/지주사 정상)"
        elif fin_rev_st == "naver_confirmed":
            rev_note = ""
        elif fin_rev_st == "cross_confirmed":
            rev_note = " · 매출 2중 검증"
        else:
            rev_note = " · 매출 파싱 확인 필요"
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "confirmed",
                    "label": "✅ DART·FnGuide·Naver 3중 검증",
                    "detail": f"영업이익·순이익 Naver 교차검증 완료{_naver_yr_suffix}{rev_note}",
                    "sources": "DART+FnGuide+Naver(3중)"}
    elif fin_op_st == "cross_confirmed":
        rev_note = "" if fin_rev_st in ("naver_confirmed", "cross_confirmed") else " · 매출 파싱 오류 가능성"
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "confirmed",
                    "label": "✅ DART·FnGuide 2중 검증",
                    "detail": f"영업이익 교차검증 통과{fin_yr}{rev_note}",
                    "sources": "DART+FnGuide(2중)"}
    else:
        fin_item = {"field": "재무제표 (매출·영업이익)", "status": "ok",
                    "label": "✔ 수집됨 (미검증)", "detail": "DART 수집 완료 · FnGuide 데이터 없어 교차검증 불가",
                    "sources": "DART"}

    # BS 검증 항목 (total_assets, total_equity)
    def _bs_item():
        ta = bs_map.get("total_assets", {})
        te = bs_map.get("total_equity", {})
        if not ta and not te:
            return None
        ta_ambig = ta.get("ambiguous", 0)
        te_ambig = te.get("ambiguous", 0)
        ta_ok = ta.get("confirmed", 0) > 0
        te_ok = te.get("confirmed", 0) > 0
        if ta_ambig > 0 or te_ambig > 0:
            problems = []
            if ta_ambig > 0: problems.append(f"총자산 {ta_ambig}건")
            if te_ambig > 0: problems.append(f"총자본 {te_ambig}건")
            return {"field": "재무상태표 (총자산·총자본)", "status": "ambiguous",
                    "label": "⚠️ BS 재확인 필요",
                    "detail": f"DART·FnGuide 불일치: {', '.join(problems)}{fin_yr}",
                    "sources": "DART+FnGuide(2중)"}
        if ta_ok or te_ok:
            auto_cnt = ta.get("auto_fixed", 0) + te.get("auto_fixed", 0)
            note = f" (FnGuide 오류 {auto_cnt}건 자동보정)" if auto_cnt > 0 else ""
            return {"field": "재무상태표 (총자산·총자본)", "status": "confirmed",
                    "label": "✅ DART·FnGuide 2중 검증",
                    "detail": f"총자산·총자본 교차검증 통과{fin_yr}{note}",
                    "sources": "DART+FnGuide(2중)"}
        return None
    bs_item = _bs_item()

    # OFS (별도재무제표)
    if ofs_cf > 0 and ofs_fin > 0:
        ofs_item = {"field": "별도재무제표 (OFS)", "status": "ok",
                    "label": "✔ CF·재무 수집됨 (미검증)", "detail": f"CF {ofs_cf}건 · 재무 {ofs_fin}건 — 교차검증 미실시"}
    elif ofs_cf > 0:
        ofs_item = {"field": "별도재무제표 (OFS)", "status": "partial",
                    "label": "△ CF만 수집됨", "detail": f"현금흐름 {ofs_cf}건 수집 · 재무제표(P&L) 미수집 — DART 별도 손익계산서 없음"}
    elif ofs_fin > 0:
        ofs_item = {"field": "별도재무제표 (OFS)", "status": "partial",
                    "label": "△ 재무만 수집됨", "detail": f"재무제표 {ofs_fin}건 수집 · 현금흐름 미수집"}
    else:
        ofs_item = {"field": "별도재무제표 (OFS)", "status": "na",
                    "label": "〰 미제출", "detail": "DART 별도재무제표 없음 (지주사·금융사 등 해당 없음)"}

    items = [ocf_item, icf_item, cash_item, dep_item, capex_item, fin_item]
    if bs_item:
        items.append(bs_item)
    items.append(ofs_item)

    # ── 5. 종합 등급 산정 ────────────────────────────────────────────
    # 핵심 항목(CF + 재무제표)만 ambiguous_cnt에 포함 — BS는 보조항목으로 등급 강등 없음
    core_items    = [ocf_item, icf_item, cash_item, dep_item, capex_item, fin_item]
    ambiguous_cnt = sum(1 for it in core_items if it["status"] == "ambiguous")
    missing_cnt   = sum(1 for it in [ocf_item, icf_item, fin_item] if it["status"] == "missing")
    confirmed_cnt = sum(1 for it in [ocf_item, icf_item] if it["status"] == "confirmed")
    fin_confirmed = fin_item["status"] == "confirmed"
    bs_ambig_cnt  = 1 if bs_item and bs_item["status"] == "ambiguous" else 0

    if not has_validation and not has_fin:
        grade, grade_label, grade_color = "D", "검증 미완", "#6b7280"
        grade_desc = "4중 교차검증 데이터 없음 — 수집 이전 종목"
    elif missing_cnt >= 2:
        grade, grade_label, grade_color = "D", "검증 미완", "#6b7280"
        grade_desc = "핵심 현금흐름 데이터 미수집"
    elif ambiguous_cnt >= 2:
        grade, grade_label, grade_color = "C", "부분 검증", "#f59e0b"
        grade_desc = f"소스 간 불일치 {ambiguous_cnt}개 항목 — 직접 확인 권장"
    elif ambiguous_cnt == 1:
        grade, grade_label, grade_color = "C", "부분 검증", "#f59e0b"
        grade_desc = "1개 항목 소스 불일치 — 참고용으로만 사용"
    elif (confirmed_cnt == 2 and fin_confirmed
          and dep_item["status"] in ("confirmed", "ok", "na", "partial")
          and capex_item["status"] in ("confirmed", "ok")):
        has_naver_fin = bool(fin_naver_map)
        grade, grade_label, grade_color = "A", "완전 검증", "#10b981"
        bs_note = f" · BS {bs_ambig_cnt}건 미해소" if bs_ambig_cnt > 0 else ""
        grade_desc = ("현금흐름 4중 검증 · 재무제표 DART·FnGuide·Naver 3중 검증 완료" + bs_note
                      if has_naver_fin else
                      "현금흐름 4중 검증 · 재무제표 DART·FnGuide 교차검증 완료" + bs_note)
    elif confirmed_cnt == 2 and dep_item["status"] in ("confirmed", "ok", "na", "partial") and capex_item["status"] in ("confirmed", "ok"):
        grade, grade_label, grade_color = "B+", "핵심 검증", "#22c55e"
        grade_desc = "현금흐름 4중 검증 완료 · 재무제표 FnGuide 미보유"
    elif confirmed_cnt >= 1 or has_validation:
        grade, grade_label, grade_color = "B", "핵심 검증", "#3b82f6"
        grade_desc = "OCF·ICF 검증 완료 · 일부 항목 구조적 한계"
    else:
        grade, grade_label, grade_color = "B", "핵심 검증", "#3b82f6"
        grade_desc = "데이터 수집 완료 · 교차검증 미실시"

    return {
        "grade": grade,
        "grade_label": grade_label,
        "grade_color": grade_color,
        "grade_desc": grade_desc,
        "items": items,
        "has_validation": has_validation,
        "val_year_min": _min_yr,
        "val_year_max": _max_yr,
        "val_years": _val_years,            # 실제 검증된 연도 목록
        "checked_years": [r["year"] for r in cf_rows],  # CF null체크 연도 (하위호환)
    }


# ── DART 공시 조회 캐시 (stock_code → {items, cached_at}) ─────────────
_disclosure_cache: dict = {}
_DISCLOSURE_CACHE_TTL = 300  # 5분

def _load_disclosures_from_db(stock_code: str, limit: int = 100):
    """로컬 DB(dart_disclosures / dart_disclosure_cache)에서 공시 목록 로드."""
    import sqlite3 as _sl
    import json as _json
    items = []
    conn = None
    try:
        conn = _sl.connect("stock.db", timeout=10)
        conn.row_factory = _sl.Row

        # 1) 정규 저장 테이블 우선
        rows = conn.execute(
            """
            SELECT rcept_no, rcept_dt, report_nm, flr_nm, corp_name, dart_url
            FROM dart_disclosures
            WHERE stock_code = ?
            ORDER BY REPLACE(rcept_dt, '.', '') DESC, rcept_no DESC
            LIMIT ?
            """,
            (stock_code, limit),
        ).fetchall()

        if rows:
            for r in rows:
                items.append({
                    "rcept_no":  str(r["rcept_no"] or ""),
                    "rcept_dt":  str(r["rcept_dt"] or ""),
                    "report_nm": str(r["report_nm"] or ""),
                    "flr_nm":    str(r["flr_nm"] or ""),
                    "corp_name": str(r["corp_name"] or ""),
                    "dart_url":  str(r["dart_url"] or ""),
                })
            return items

        # 2) 캐시 JSON 테이블 fallback
        row = conn.execute(
            "SELECT payload_json FROM dart_disclosure_cache WHERE stock_code=?",
            (stock_code,),
        ).fetchone()
        if row and row["payload_json"]:
            parsed = _json.loads(row["payload_json"])
            if isinstance(parsed, list):
                return parsed[:limit]
    except Exception as _e:
        logger.debug(f"[공시][DB] {stock_code} fallback 로드 실패: {_e}")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return items


@app.get("/api/dashboard/disclosures/{stock_code}")
def get_disclosures(stock_code: str):
    """
    DART 최신 공시 목록 반환.
    - 국내 종목 6자리 코드만 지원 (해외 종목은 빈 리스트)
    - 최근 1년 공시, 최대 100건
    - 5분 캐시 적용
    """
    # 국내 종목 코드 검증 (6자리 숫자)
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        return []

    # 캐시 확인 (메모리)
    cached = _disclosure_cache.get(stock_code)
    if cached and (_tm.time() - cached.get("cached_at", 0)) < _DISCLOSURE_CACHE_TTL:
        return cached["items"]

    # 1) DB 우선 (빠르고 안정적)
    db_items = _load_disclosures_from_db(stock_code, limit=100)
    if db_items:
        _disclosure_cache[stock_code] = {"items": db_items, "cached_at": _tm.time()}
        # DB값 즉시 반환 (실시간 API 불안정 대비)
        return db_items

    try:
        import config as _cfg
        import OpenDartReader
        from datetime import timedelta

        dart = OpenDartReader(_cfg.DART_API_KEY)

        # 최근 1년 조회
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=365)
        start_str = start_dt.strftime("%Y%m%d")
        end_str   = end_dt.strftime("%Y%m%d")

        # final=False → 정정 포함 전체 공시 조회
        df = dart.list(stock_code, start=start_str, end=end_str, final=False)

        if df is None or df.empty:
            # 실시간 결과가 비어도 DB fallback 재시도
            fallback = _load_disclosures_from_db(stock_code, limit=100)
            _disclosure_cache[stock_code] = {"items": fallback, "cached_at": _tm.time()}
            return fallback

        # 최신순 정렬 후 최대 100건
        if "rcept_dt" in df.columns:
            df = df.sort_values("rcept_dt", ascending=False)

        result = []
        for _, row in df.head(100).iterrows():
            rcept_no  = str(row.get("rcept_no",  ""))
            rcept_dt  = str(row.get("rcept_dt",  ""))
            report_nm = str(row.get("report_nm", ""))
            flr_nm    = str(row.get("flr_nm",    ""))  # 공시 제출인명
            corp_name = str(row.get("corp_name", ""))

            # 날짜 포맷: 20250101 → 2025.01.01
            if len(rcept_dt) == 8:
                rcept_dt = f"{rcept_dt[:4]}.{rcept_dt[4:6]}.{rcept_dt[6:]}"

            # DART 원문 링크
            dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""

            result.append({
                "rcept_no":  rcept_no,
                "rcept_dt":  rcept_dt,
                "report_nm": report_nm,
                "flr_nm":    flr_nm,
                "corp_name": corp_name,
                "dart_url":  dart_url,
            })

        _disclosure_cache[stock_code] = {"items": result, "cached_at": _tm.time()}
        logger.info(f"[공시] {stock_code} 조회 완료: {len(result)}건")
        return result

    except Exception as e:
        logger.warning(f"[공시] {stock_code} 조회 실패, DB fallback 사용: {e}")
        fallback = _load_disclosures_from_db(stock_code, limit=100)
        _disclosure_cache[stock_code] = {"items": fallback, "cached_at": _tm.time()}
        return fallback

@app.get("/api/dashboard/fundamentals/{stock_code}")
def get_stock_fundamentals(stock_code: str, db: Session = Depends(get_db)):
    """
    종목 핵심 지표.
    1. 재무 DB 없으면 → DART 동기 수집 (crud 직접 저장)
    2. 주가 DB 없으면 → KIS 즉시 저장 + Yahoo 백그라운드
    3. PBR/PER → 캐시 우선, 없으면 네이버금융 동기 스크래핑
    """
    is_kr  = stock_code.isdigit() and len(stock_code) == 6
    is_col = _collecting.get(stock_code) == "running"

    # ── 재무 DB 확인 → 없으면 동기 수집 ─────────────────────────
    data = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code,
        models.FinancialData.is_annual.is_(True),
    ).order_by(models.FinancialData.year.desc()).first()

    # 재무 데이터 없으면 그대로 진행 (월간 배치 또는 수동 refresh로만 수집)

    # ── 주가 DB 확인 → 없으면 KIS 즉시 + Yahoo 백그라운드 ─────────
    if is_kr:
        no_price = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == stock_code
        ).first() is None
        if no_price:
            kis = _get_kis_price(stock_code)
            if kis:
                try:
                    crud.bulk_insert_price_history(db, schemas.PriceIngest(
                        stock_code=stock_code,
                        prices=[schemas.PriceData(
                            date=kis["date"], open=kis["open"], high=kis["high"],
                            low=kis["low"], close=kis["close"], volume=kis["volume"],
                            inst_net_buy=0.0, frn_net_buy=0.0)]))
                    logger.info(f"[Fund] KIS 현재가 저장: {stock_code}")
                except Exception as e:
                    logger.warning(f"[Fund] KIS 저장실패: {e}")
            if not is_col:
                _th.Thread(target=_bg_ondemand, args=(stock_code,), daemon=True).start()
                is_col = True

    # ── PBR/PER: DB에서 직접 계산 (Naver 스크래핑 불필요) ─────────────
    # 우선순위: ①현재가×EPS/BPS 직접계산 → ②stock_universe 월간배치값 → ③캐시(구Naver)
    val = _get_cached_valuation(stock_code)
    if not val and is_kr:
        import sqlite3 as _sl3
        try:
            _conn_v = _sl3.connect("stock.db")
            _conn_v.row_factory = _sl3.Row
            # 현재주가
            _pr = _conn_v.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (stock_code,)
            ).fetchone()
            _cur_price = _pr["close"] if _pr else None
            # 최근 연간 EPS/BPS (FnGuide 수집분 우선, 그 다음 DART)
            _fin = _conn_v.execute(
                """SELECT eps, bps FROM financial_data
                   WHERE stock_code=? AND is_annual=1 AND year >= 2020
                   ORDER BY year DESC, (CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END) ASC
                   LIMIT 1""",
                (stock_code,)
            ).fetchone()
            # stock_universe fallback (월간배치 계산값)
            _su = _conn_v.execute(
                "SELECT per, pbr, eps, bps, roe, roa FROM stock_universe WHERE stock_code=?",
                (stock_code,)
            ).fetchone()
            _conn_v.close()

            per = pbr = trailing_eps = None
            src = None

            if _cur_price and _fin:
                _eps = _fin["eps"] if _fin["eps"] and _fin["eps"] != 0 else None
                _bps = _fin["bps"] if _fin["bps"] and _fin["bps"] != 0 else None
                if _eps and _eps > 0:
                    per = round(_cur_price / _eps, 1)
                if _bps and _bps > 0:
                    pbr = round(_cur_price / _bps, 2)
                trailing_eps = _eps
                if per or pbr:
                    src = "DB(price×EPS/BPS)"

            # fallback: stock_universe 월간배치값
            if (per is None or pbr is None) and _su:
                if per is None and _su["per"]:
                    per = _su["per"]
                if pbr is None and _su["pbr"]:
                    pbr = _su["pbr"]
                if trailing_eps is None and _su["eps"]:
                    trailing_eps = _su["eps"]
                src = src or "DB(stock_universe)"

            # BPS: financial_data 우선, 없으면 stock_universe
            _bps_val = None
            if _fin and _fin["bps"] and _fin["bps"] != 0:
                _bps_val = _fin["bps"]
            elif _su and _su["bps"]:
                _bps_val = _su["bps"]

            # ROE/ROA: stock_universe (배치 계산값)
            _roe_val = _su["roe"] if _su and _su["roe"] is not None else None
            _roa_val = _su["roa"] if _su and _su["roa"] is not None else None

            val = {"per": per, "pbr": pbr, "trailing_eps": trailing_eps,
                   "forward_per": None, "source": src,
                   "bps": _bps_val, "roe": _roe_val, "roa": _roa_val}
        except Exception:
            val = {}

    # ── 52주 최고/최저가 (price_history 최근 252 거래일) ─────────────
    high52 = low52 = None
    try:
        ph_rows = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == stock_code,
            models.PriceHistory.close > 0,
        ).order_by(models.PriceHistory.date.desc()).limit(252).all()
        if ph_rows:
            closes = [r.close for r in ph_rows]
            high52 = max(closes)
            low52  = min(closes)
    except Exception:
        pass

    # ── 유통주식수: stock_meta 캐시 우선, 없으면 백그라운드 수집 ──────
    float_shares = shares_outstanding = None
    if is_kr:
        _meta = db.query(models.StockMeta).filter(
            models.StockMeta.stock_code == stock_code
        ).first()
        if _meta and _meta.float_shares:
            float_shares       = _meta.float_shares
            shares_outstanding = _meta.shares_outstanding
        else:
            pass  # 캐시 미스 → 월간 배치에서 수집됨

    if not data:
        return {
            "revenue": None, "operating_profit": None, "net_income": None,
            "opm": None,
            "roe": val.get("roe"), "roa": val.get("roa"),
            "bps": val.get("bps"),
            "pbr": val.get("pbr"), "per": val.get("per"),
            "forward_per": None, "trailing_eps": val.get("trailing_eps"),
            "source": val.get("source"), "collecting": is_col,
            "high52": high52, "low52": low52,
            "float_shares": float_shares, "shares_outstanding": shares_outstanding,
        }

    opm = (
        (data.operating_profit / data.revenue * 100)
        if (data.revenue and data.revenue != 0 and data.operating_profit is not None)
        else 0.0
    )
    # ROE: stock_universe(배치계산) 우선 → financial_data.roe fallback
    _roe_fd = getattr(data, "roe", None)
    _roe_su = val.get("roe")
    _roe = _roe_su if _roe_su is not None else (_roe_fd if _roe_fd else None)
    return {
        "revenue":           data.revenue,
        "operating_profit":  data.operating_profit,
        "net_income":        data.net_income,
        "opm":               round(opm, 1),
        "roe":               round(_roe, 2) if _roe is not None else None,
        "roa":               val.get("roa"),
        "bps":               val.get("bps"),
        "pbr":               val.get("pbr"),
        "per":               val.get("per"),
        "forward_per":       val.get("forward_per"),
        "trailing_eps":      val.get("trailing_eps"),
        "source":            val.get("source"),
        "collecting":        is_col,
        "high52":            high52,
        "low52":             low52,
        "float_shares":      float_shares,
        "shares_outstanding": shares_outstanding,
    }

@app.get("/api/reports/ready")
def get_ready_reports(db: Session = Depends(get_db)):
    """
    OpenClaw 전송용(텔레그램 등)으로 스크리닝 통과 종목 및 AI 분석 결과를 통합 반환합니다.
    """
    try:
        passed_stocks = screener.apply_triple_screening(db)

        results = []
        analyzer = ai_analyzer.AIAnalyzer(db)

        for stock in passed_stocks:
            report = analyzer.get_latest_report(stock["stock_code"])

            # [버그 1 수정] report는 ORM 객체 또는 None.
            # report.content 속성 접근 전에 None 체크 후 안전하게 추출.
            if report is None:
                ai_report_text = "분석 리포트가 아직 생성되지 않았습니다."
            elif isinstance(report, dict):
                ai_report_text = report.get("content", "분석 리포트가 아직 생성되지 않았습니다.")
            else:
                ai_report_text = getattr(report, "content", "분석 리포트가 아직 생성되지 않았습니다.")

            results.append({
                "stock_code": stock["stock_code"],
                "stock_name": stock["stock_name"],
                "financial_summary": {
                    "revenue": stock["latest_revenue"],
                    "profit": stock["latest_profit"],
                },
                "ai_report": ai_report_text,
            })

        return results
    except Exception as e:
        logger.error(f"최종 리포트 준비 중 오류 발생: {e}")
        raise HTTPException(status_code=500, detail="리포트 생성 중 오류가 발생했습니다.")

# --- 대화형 명령 API (OpenClaw Interaction) ---

@app.get("/api/search")
def search_stocks(q: str):
    """
    프론트엔드 드롭다운용 주식 자동완성(Fuzzy) 검색 API
    """
    from ticker_utils import ticker_mapper
    if not q or len(q.strip()) == 0:
        return []
    return ticker_mapper.search(q.strip())


@app.post("/api/commands/analyze/{stock_name}")
def command_analyze_stock(stock_name: str, db: Session = Depends(get_db)):
    """
    종목 조회 진입점 (검색 즉시 전체 수집).
    1. 종목코드 조회 (ticker_mapper)
    2. watchlist 등록
    3. 주가 히스토리 없으면 KIS 현재가 즉시 저장 → Yahoo 1년치 백그라운드
    4. 재무 없거나 최신분기 없거나 재무관련 공시 있으면 → DART 수집 백그라운드
    5. 즉시 응답 (collecting=True 이면 프론트가 폴링)
    """
    from ticker_utils import ticker_mapper
    # 종목코드로 직접 조회도 허용 (6자리 숫자)
    if stock_name.isdigit() and len(stock_name) == 6:
        stock_code = stock_name
        resolved_name = ticker_mapper.get_name(stock_code) or stock_name
    else:
        stock_code = ticker_mapper.get_code(stock_name)
        resolved_name = stock_name
        if not stock_code:
            # fuzzy search fallback
            results = ticker_mapper.search(stock_name)
            if results:
                stock_code = results[0].get("code")
                resolved_name = results[0].get("name", stock_name)
    if not stock_code:
        raise HTTPException(status_code=404, detail=f"'{stock_name}' 종목을 찾을 수 없습니다.")

    # watchlist 등록
    crud.add_to_watchlist(db, stock_code)
    try: db.commit()
    except: db.rollback()

    is_kr  = stock_code.isdigit() and len(stock_code) == 6
    is_col = _collecting.get(stock_code) == "running"

    if is_kr and not is_col:
        # ── 주가: 장중에만 KIS 현재가 즉시 저장 ──
        if _is_market_hours():
            kis = _get_kis_price(stock_code)
            if kis:
                try:
                    crud.bulk_insert_price_history(db, schemas.PriceIngest(
                        stock_code=stock_code,
                        prices=[schemas.PriceData(
                            date=kis["date"], open=kis["open"], high=kis["high"],
                            low=kis["low"], close=kis["close"], volume=kis["volume"],
                            inst_net_buy=0.0, frn_net_buy=0.0)]))
                    logger.info(f"[Analyze] KIS 현재가 즉시 저장: {stock_code} {kis['close']}원")
                except Exception as e:
                    logger.warning(f"[Analyze] KIS 저장실패: {e}")

        # ── 히스토리 + 재무 백그라운드 전체 수집 ──────────────────
        # has_history: 30일치 차트 데이터 1건이라도 있는지 확인
        has_history = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == stock_code
        ).count() >= 20  # 20건 미만이면 히스토리 부족으로 간주

        has_financial = db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == stock_code
        ).first() is not None

        # 수집 필요 여부 판단
        # 장중이거나 데이터 부족 시에만 수집
        need_collect = (
            not has_history or          # 주가 데이터 없음
            not has_financial or        # 재무 데이터 없음
            _is_market_hours()          # 장중: 오늘 최신 데이터 갱신 필요
        )
        if need_collect:
            _th.Thread(target=_bg_ondemand, args=(stock_code,), daemon=True).start()
            is_col = True
            logger.info(f"[Analyze] {stock_code} 수집 시작 (히스토리:{has_history}, 재무:{has_financial}, 장중:{_is_market_hours()})")
        else:
            logger.debug(f"[Analyze] {stock_code} 데이터 충분+장외 → 수집 스킵")

        # ── 수급 데이터 온디맨드 업데이트 (KIS에서 최근 수급 갱신) ──
        def _update_supply(code: str):
            import sqlite3 as _sl2
            from datetime import date as _date

            def _do_update(c2):
                try:
                    from kis_client import kis_client
                    today_str = _date.today().isoformat()
                    row = c2.execute(
                        "SELECT frn_net_buy, inst_net_buy FROM price_history WHERE stock_code=? AND date=?",
                        (code, today_str)
                    ).fetchone()
                    # 오늘 수급이 없거나 0이면 KIS에서 갱신
                    if row is None or ((row[0] or 0) == 0.0 and (row[1] or 0) == 0.0):
                        trends = kis_client.get_investor_trends_bulk(code)
                        if trends:
                            updated_cnt = 0
                            for t in trends:
                                existing = c2.execute(
                                    "SELECT id FROM price_history WHERE stock_code=? AND date=?",
                                    (code, t['date'])
                                ).fetchone()
                                if existing:
                                    c2.execute("""
                                        UPDATE price_history SET
                                            inst_net_buy=?, frn_net_buy=?, ind_net_buy=?,
                                            inst_net_buy_amt=?, frn_net_buy_amt=?, ind_net_buy_amt=?
                                        WHERE stock_code=? AND date=?
                                    """, (
                                        t['inst_net_buy'], t['frn_net_buy'], t.get('ind_net_buy', 0),
                                        t.get('inst_net_buy_amt', 0), t.get('frn_net_buy_amt', 0),
                                        t.get('ind_net_buy_amt', 0), code, t['date']
                                    ))
                                    updated_cnt += 1
                            c2.commit()
                            logger.info(f"[Analyze] {code} 수급 업데이트: {updated_cnt}/{len(trends)}건")
                except Exception as e2:
                    logger.warning(f"[Analyze] {code} 수급 업데이트 오류: {e2}")

            try:
                c2 = _sl2.connect("stock.db")
                _do_update(c2)     # 즉시 1회
                # _bg_collect 완료 대기 후 재시도 (race-condition 방지)
                _tm.sleep(20)
                _do_update(c2)
                c2.close()
            except Exception as e2:
                logger.warning(f"[Analyze] {code} 수급 스레드 오류: {e2}")

        _th.Thread(target=_update_supply, args=(stock_code,), daemon=True).start()

    is_col = _collecting.get(stock_code) == "running"
    return {
        "status":     "collecting" if is_col else "ready",
        "stock_name": resolved_name,
        "stock_code": stock_code,
        "collecting": is_col,
    }

@app.get("/api/commands/collect-status/{stock_code}")
def get_collect_status(stock_code: str):
    """수집 진행 상태 조회"""
    return {"stock_code": stock_code, "status": _collecting.get(stock_code, "done")}

@app.get("/api/commands/watchlist")
def get_current_watchlist(db: Session = Depends(get_db)):
    """현재 자동 추적 중인 종목 리스트를 반환합니다."""
    return crud.get_watchlist(db)

@app.delete("/api/commands/watchlist/{stock_code}")
def remove_from_watchlist(stock_code: str, db: Session = Depends(get_db)):
    """관심종목에서 특정 종목을 제거합니다."""
    deleted = db.query(models.Watchlist).filter(
        models.Watchlist.stock_code == stock_code
    ).first()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"'{stock_code}' 종목이 관심종목에 없습니다.")
    db.delete(deleted)
    db.commit()
    return {"status": "success", "removed": stock_code}

@app.get("/api/dashboard/macro")
def get_macro_dashboard(db: Session = Depends(get_db)):
    """지수, 환율, 원자재 등 매크로 지표 현황을 반환합니다."""
    result = processor.get_macro_status(db)
    result.setdefault("index",       {})
    result.setdefault("vix",         {"value": 0, "change": 0, "date": "-", "history": []})
    result.setdefault("commodities", {})
    return result

@app.get("/api/dashboard/stats")
def get_db_stats(db: Session = Depends(get_db)):
    """데이터베이스 적재 현황 통계를 반환합니다."""
    return {
        "stock_count": db.query(models.FinancialData.stock_code).distinct().count(),
        "price_records": db.query(models.PriceHistory).count(),
        "db_path": "Applications/stock_dashboard/stock.db",
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
