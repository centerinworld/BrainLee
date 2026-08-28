from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import case
import models, schemas, crud, processor, screener, ai_analyzer
from database import get_db, engine
import logging
import re
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from hs_trade_lab.app.main import app as hs_trade_lab_app
from hs_trade_lab.semiconductor_value_lab.fastapi_app import app as semiconductor_value_lab_app
from db_utils import connect_stock_db
from db_compat import install_sqlite_primary_router, primary_database_label
from config import IS_POSTGRES

install_sqlite_primary_router()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from config import DATABASE_URL, IS_POSTGRES
logger.info("=== DB CONFIG CHECK ===")
logger.info(f"  DATABASE_URL: {DATABASE_URL}")
logger.info(f"  IS_POSTGRES: {IS_POSTGRES}")
logger.info(f"  Primary Database Label: {primary_database_label()}")

# 데이터베이스 테이블 생성 (상시 동기화)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="주식 분석 백엔드 (프로젝트 안티그래비티)")
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.mount("/hs", hs_trade_lab_app)
app.mount("/semiconductor-lab", semiconductor_value_lab_app)


@app.middleware("http")
async def record_slow_api_requests(request: Request, call_next):
    started = _tm.perf_counter()
    response = await call_next(request)
    elapsed_ms = (_tm.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if request.url.path.startswith("/api/") and elapsed_ms >= 1000:
        logger.warning(
            "[느린 API] %s %s %.0fms",
            request.method,
            request.url.path,
            elapsed_ms,
        )
    return response

# ═══════════════════════════════════════════════════════
#  전역 상태 — startup_event 이전에 반드시 정의
# ═══════════════════════════════════════════════════════
import threading as _th
import time as _tm
import json as _json
import requests as _requests
from data_write_gate import (
    ensure_canonical_schema as _wg_ensure_schema,
    gate_cashflow_row as _wg_gate_cashflow_row,
    upsert_canonical_cashflow as _wg_upsert_canonical_cashflow,
)

_collecting: dict      = {}   # stock_code -> "running"|"done"
_valuation_cache: dict = {}   # stock_code -> {per,pbr,cached_at,...}
_us_biotech_refresh_state: dict = {
    "status": "idle", "started_at": None, "finished_at": None,
    "min_market_cap": None, "limit": None, "result": None, "error": None,
}
_market_info_cache: dict = {} # stock_code -> {market,mktcap,mktcap_rank,cached_at}
_signal_cache: dict = {}      # 'market' or stock_code -> {results, cached_at}
_macro_rt_running = False
_macro_rt_last_ts = 0.0
_MACRO_RT_MIN_INTERVAL = 180.0  # 초: 실시간 매크로 강제 갱신 최소 간격
_us_sp500_cache: dict = {"symbols": set(), "ts": 0.0}
_us_stock_detail_cache: dict = {}
_us_stock_base_items_cache: dict = {"ts": 0.0, "items": []}


def _load_shareholder_profile(stock_code: str) -> dict:
    """Consolidated float-share and major-holder profile from local stock.db."""
    try:
        conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
        row = conn.execute(
            """
            SELECT stock_code, stock_name, market, base_date,
                   shares_issued, shares_outstanding, float_shares,
                   treasury_shares_est, free_float_ratio,
                   major_holder_name, major_holder_shares, major_holder_ratio,
                   major_holder_report_date, major_holder_report_no,
                   major_holder_report_type, major_holder_count,
                   data_quality, quality_note, source, updated_at
            FROM stock_shareholder_profile
            WHERE stock_code=?
            """,
            (stock_code,),
        ).fetchone()
        conn.close()
        if not row:
            return {}
        profile = dict(row)
        issued = float(profile.get("shares_issued") or 0)
        float_shares = float(profile.get("float_shares") or 0)
        ratio = profile.get("free_float_ratio")
        impossible = bool(issued and float_shares and float_shares > issued) or bool(ratio is not None and float(ratio) > 100.0)
        if impossible:
            profile["float_shares"] = None
            profile["free_float_ratio"] = None
            profile["data_quality"] = "review"
            note = str(profile.get("quality_note") or "").strip()
            warning = "유통주식수가 발행주식수를 초과해 표시 제외"
            profile["quality_note"] = f"{note} / {warning}" if note else warning
        return profile
    except Exception:
        return {}
_us_sec_map_cache: dict = {"map": {}, "ts": 0.0}

# ── 통합 스케줄러 (startup/shutdown에서 제어) ─────────────────
from scheduler import CollectionScheduler
from collection_health import DATASET_CONTRACTS, evaluate_all_contracts, latest_collection_runs
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
from routes.quant_major_indicators import router as _quant_major_indicators_router
from routes.extra_signals      import router as _extra_signals_router
from routes.stock_analysis_rs  import router as _stock_analysis_rs_router
from routes.market_radar       import router as _market_radar_router
from routes.sector_define      import router as _sector_define_router
from routes.kis_trading        import router as _kis_trading_router
from routes.dart_contracts     import router as _dart_contracts_router
from routes.order_contracts    import router as _order_contracts_router
from routes.contract_advance_signals import router as _contract_advance_signals_router
from routes.inventory_sales_signals import router as _inventory_sales_signals_router
from routes.cash_conversion_signals import router as _cash_conversion_signals_router
from routes.dart_excel         import router as _dart_excel_router
from routes.earnings_signals   import router as _earnings_signals_router
from routes.kiwoom             import router as _kiwoom_router
from routes.consensus          import router as _consensus_router
from routes.tenbagger          import router as _tenbagger_router
from routes.cherry_screener    import router as _cherry_screener_router
from routes.sector_rotation    import router as _sector_rotation_router
from routes.detailed_analysis  import router as _detailed_analysis_router
from routes.global_macro       import router as _global_macro_router
from routes.cafe_signals       import router as _cafe_signals_router
from routes.us_virtual_trading import router as _us_virtual_trading_router
from routes.company_intelligence import router as _company_intelligence_router
from routes.insider            import router as _insider_router
from routes.notices            import router as _notices_router
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
app.include_router(_quant_major_indicators_router, prefix="/api/quant-major-indicators", tags=["quant-major-indicators"])
app.include_router(_extra_signals_router,      prefix="/api/extra-signals",      tags=["extra-signals"])
app.include_router(_stock_analysis_rs_router,  prefix="/api/stock-analysis-rs",  tags=["stock-analysis-rs"])
app.include_router(_etf_check_router)  # prefix: /api/etf-check (router 내부 정의)
app.include_router(_market_radar_router,  prefix="/api/market-radar",   tags=["market-radar"])
app.include_router(_sector_define_router, prefix="/api/sector-define",  tags=["sector-define"])
app.include_router(_employment_v2_router)  # prefix: /api/employment-v2 (router 내부 정의)
app.include_router(_kis_trading_router)
app.include_router(_dart_contracts_router,    prefix="/api/dart-contracts",    tags=["dart-contracts"])
app.include_router(_order_contracts_router,   prefix="/api/order-contracts",   tags=["order-contracts"])
app.include_router(_contract_advance_signals_router, prefix="/api/contract-advance-signals", tags=["contract-advance-signals"])
app.include_router(_inventory_sales_signals_router, prefix="/api/inventory-sales-signals", tags=["inventory-sales-signals"])
app.include_router(_cash_conversion_signals_router, prefix="/api/cash-conversion-signals", tags=["cash-conversion-signals"])
app.include_router(_dart_excel_router,       prefix="/api/dart-excel",        tags=["dart-excel"])
app.include_router(_earnings_signals_router, prefix="/api/earnings-signals",  tags=["earnings-signals"])
app.include_router(_kiwoom_router,          prefix="/api/kiwoom",         tags=["kiwoom"])
app.include_router(_cherry_screener_router, prefix="/api/cherry-screener", tags=["cherry-screener"])
app.include_router(_consensus_router,       prefix="/api/consensus",      tags=["consensus"])
app.include_router(_tenbagger_router,       prefix="/api/tenbagger",      tags=["tenbagger"])
app.include_router(_sector_rotation_router, prefix="/api/sector-rotation", tags=["sector-rotation"])
app.include_router(_detailed_analysis_router, prefix="/api/detailed-analysis", tags=["detailed-analysis"])
app.include_router(_global_macro_router,      prefix="/api/global-macro",      tags=["global-macro"])
app.include_router(_cafe_signals_router,      prefix="/api/cafe-signals",      tags=["cafe-signals"])
app.include_router(_us_virtual_trading_router)
app.include_router(_company_intelligence_router, prefix="/api/company-intelligence", tags=["company-intelligence"])
# 2026-08-26: routes/insider.py·routes/notices.py는 완성되어 있었으나 여기 등록이 빠져
# 개별종목 페이지의 임원·대주주 지분변동/공지사항 패널이 항상 404였음 — 등록 누락 수정.
app.include_router(_insider_router,  prefix="/api/insider",  tags=["insider"])
app.include_router(_notices_router,  prefix="/api/notices",  tags=["notices"])


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
        import pandas as pd
        from dart_key_manager import RotatingOpenDartReader
        dart = RotatingOpenDartReader()
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
                # 계정 오매핑 방지: "부채와자본총계" 같은 결합 계정은 개별 BS 계정으로 사용 금지
                if "부채와자본총계" in acc or "부채및자본총계" in acc:
                    continue
                if   "매출액" in acc or "영업수익" in acc or acc == "수익":               m["revenue"] = val
                elif "영업이익" in acc or acc in ("영업손익", "영업손익(손실)"):         m["operating_profit"] = val
                elif ("당기순이익" in acc or "분기순이익" in acc or "반기순이익" in acc) and "주당" not in acc and "지배" not in acc: m["net_income"] = val
                elif acc in ("자산총계",):                                            m["total_assets"] = val
                elif acc in ("부채총계",):                                            m["total_liabilities"] = val
                elif acc in ("자본총계", "자본총계(지배)"):                             m["total_equity"] = val
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
        import pandas as pd
        from dart_key_manager import RotatingOpenDartReader
        dart = RotatingOpenDartReader()
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

    # write-gate: Q1 q필드 등 보정
    try:
        import sqlite3 as _sl_wg
        _c = _sl_wg.connect("/Applications/stock_dashboard/stock.db")
        _wg_ensure_schema(_c)
        ok, fixed, _ = _wg_gate_cashflow_row(_c, {
            "stock_code": row.stock_code,
            "year": row.year,
            "quarter": row.quarter,
            "is_annual": row.is_annual,
            "report_type": row.report_type or "CFS",
            "operating_cf": row.operating_cf,
            "investing_cf": row.investing_cf,
            "financing_cf": row.financing_cf,
            "capex": row.capex,
            "cash_end": row.cash_end,
            "depreciation": row.depreciation,
            "operating_cf_q": row.operating_cf_q,
            "investing_cf_q": row.investing_cf_q,
            "financing_cf_q": row.financing_cf_q,
            "capex_q": row.capex_q,
            "value_type": row.value_type,
            "data_source": row.data_source,
        })
        if ok:
            for f in ["operating_cf_q", "investing_cf_q", "financing_cf_q", "capex_q", "value_type"]:
                if f in fixed:
                    setattr(row, f, fixed.get(f))
        _c.commit()
        _c.close()
    except Exception:
        pass

    db.commit()
    db.refresh(row)

    # canonical sync
    try:
        import sqlite3 as _sl_wg
        _c = _sl_wg.connect("/Applications/stock_dashboard/stock.db")
        _wg_ensure_schema(_c)
        _wg_upsert_canonical_cashflow(_c, {
            "stock_code": row.stock_code,
            "year": row.year,
            "quarter": row.quarter,
            "is_annual": row.is_annual,
            "report_type": row.report_type or "CFS",
            "operating_cf": row.operating_cf,
            "investing_cf": row.investing_cf,
            "financing_cf": row.financing_cf,
            "capex": row.capex,
            "cash_end": row.cash_end,
            "depreciation": row.depreciation,
            "operating_cf_q": row.operating_cf_q,
            "investing_cf_q": row.investing_cf_q,
            "financing_cf_q": row.financing_cf_q,
            "capex_q": row.capex_q,
            "value_type": row.value_type,
            "data_source": row.data_source,
        }, source_row_id=getattr(row, "id", None), decision_reason="_upsert_cashflow")
        _c.commit()
        _c.close()
    except Exception:
        pass

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


def _is_us_market_hours() -> bool:
    """미국 정규장 운영 시간 여부 (미 동부시간, 평일 09:30~16:00)."""
    from datetime import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        now = _dt.now(ZoneInfo("America/New_York"))
    except Exception:
        # timezone DB 이슈 시 안전하게 미장 오픈 판단은 false 처리
        return False
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 930 <= t <= 1600


def _us_macro_stale(db) -> bool:
    """미국지수 최신일자가 기대일자보다 뒤처졌는지 판단."""
    from datetime import datetime as _dt, timedelta as _td
    try:
        from zoneinfo import ZoneInfo
        now_ny = _dt.now(ZoneInfo("America/New_York"))
    except Exception:
        now_ny = _dt.now()

    # 미국장 마감(16:00 ET) 전에는 이전 영업일 데이터가 정상
    expected = now_ny.date()
    if now_ny.hour < 16:
        expected = expected - _td(days=1)
    while expected.weekday() >= 5:
        expected = expected - _td(days=1)

    for sym in ("^IXIC", "^GSPC"):
        row = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == sym
        ).order_by(models.PriceHistory.date.desc()).first()
        if not row or not hasattr(row.date, "date"):
            return True
        if row.date.date() < expected:
            return True
    return False


_MACRO_SYMBOLS = {
    "^KS11":    "KOSPI",
    "^KQ11":    "KOSDAQ",
    "^IXIC":    "NASDAQ",
    "^GSPC":    "S&P500",
    "^VIX":     "VIX",
    "2YY=F":    "US2Y",
    # "^UST2Y" 제거 — Yahoo Finance 상폐(delisted). 2YY=F로 대체됨
    "^TNX":     "US10Y",
    "10Y=F":    "US10Y_ALT",
    "^TYX":     "US30Y",
    # "30Y=F" 제거 — Yahoo Finance 상폐. ^TYX로 대체됨
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
            prices=[schemas.PriceData(
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
    from db_utils import stock_db_write_lock
    from sqlalchemy.exc import OperationalError as _OperationalError
    import yfinance as yf
    def _spike_threshold(sym: str) -> float:
        if sym in ("^IXIC", "^GSPC", "^KS11", "^KQ11", "^KS200", "^KQ150"):
            return 12.0
        if sym in ("^TNX", "^TYX", "2YY=F", "10Y=F"):
            return 6.0
        if sym == "^VIX":
            return 60.0  # VIX는 하루 30-50% 급등이 정상 — 25%는 너무 낮아 실제 급등 데이터 스킵됨
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
                out.append(schemas.PriceData(
                    date=_dt.combine(row_date, _dt.min.time()),
                    open=_gv("Open"), high=_gv("High"),
                    low=_gv("Low"), close=_gv("Close"),
                    volume=_gv("Volume"),
                    inst_net_buy=0.0, frn_net_buy=0.0,
                ))
            except Exception:
                pass
        return out

    def _filter_broad_index_prices(sym: str, prices: list) -> list:
        if sym not in ("^IXIC", "^GSPC", "^KS11", "^KQ11", "^KS200", "^KQ150"):
            return prices
        out = []
        prev_c = _prev_close(sym)
        for p in sorted(prices, key=lambda row: row.date):
            close = float(p.close or 0)
            if close <= 0:
                continue
            if prev_c and prev_c > 0:
                diff_pct = abs((close - prev_c) / prev_c * 100.0)
                if diff_pct >= 20.0:
                    logger.warning(
                        f"[RT-MacroGuard] {sym}: broad-index outlier skipped "
                        f"{p.date} diff={diff_pct:.2f}% prev={prev_c:.4f} close={close:.4f}"
                    )
                    continue
            out.append(p)
            prev_c = close
        return out

    def _save_prices(sym: str, prices: list) -> bool:
        for delay in (0, 0.5, 1.5, 3.0):
            if delay:
                _tm.sleep(delay)
            # End the read transaction before waiting for the shared writer lock.
            db.rollback()
            with stock_db_write_lock(f"realtime-macro:{sym}", timeout=15) as acquired:
                if not acquired:
                    continue
                try:
                    crud.bulk_insert_price_history(
                        db, schemas.PriceIngest(stock_code=sym, prices=prices)
                    )
                    return True
                except _OperationalError as exc:
                    db.rollback()
                    if "database is locked" not in str(exc).lower():
                        raise
        return False

    today = _date.today()
    for symbol, name in _MACRO_SYMBOLS.items():
        try:
            # 종목별 최신 일자가 오늘이면 스킵 (전체 스킵 금지)
            try:
                _latest = db.query(models.PriceHistory).filter(
                    models.PriceHistory.stock_code == symbol
                ).order_by(models.PriceHistory.date.desc()).first()
                if _latest and hasattr(_latest.date, "date") and _latest.date.date() >= today:
                    continue
            except Exception:
                pass

            prices = _download_prices(symbol, "5d")
            if not prices:
                continue
            prices = _filter_broad_index_prices(symbol, prices)
            from macro_data_quality import filter_plausible_price_rows
            prices, rejected = filter_plausible_price_rows(symbol, prices)
            if rejected:
                logger.warning(f"[RT-MacroGuard] {symbol}: 범위 이탈 {rejected}건 차단")
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
            if _save_prices(symbol, prices):
                logger.info(f"[RT-Macro] {symbol}({name}) {len(prices)}건 저장")
            else:
                logger.warning(f"[RT-Macro] {symbol}: DB 쓰기 지연으로 다음 갱신 주기 재시도")
        except Exception as e:
            logger.warning(f"[RT-Macro] {symbol}: {e}")


def _trigger_macro_refresh_async() -> None:
    """매크로 실시간 갱신을 백그라운드에서 1회 실행 (중복 실행/과도 호출 방지)."""
    global _macro_rt_running, _macro_rt_last_ts
    now = _tm.time()
    if _macro_rt_running:
        return
    if now - _macro_rt_last_ts < _MACRO_RT_MIN_INTERVAL:
        return
    _macro_rt_running = True
    _macro_rt_last_ts = now

    def _job():
        global _macro_rt_running
        try:
            from database import SessionLocal as _SL
            _db = _SL()
            try:
                _realtime_fetch_macro(_db)
            finally:
                _db.close()
        except Exception as _e:
            logger.warning(f"[RT-Macro] async refresh failed: {_e}")
        finally:
            _macro_rt_running = False

    _th.Thread(target=_job, daemon=True).start()


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

    _signal_cache['_computing_fin_screener'] = True
    _signal_cache['_computing_combo_v2'] = True
    try:
        with _screener_lock:
            conn = _sl.connect("stock.db")
            try:

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

                # 키움조건식 5가지 사전계산
                try:
                    from signal_engine import calc_kiwoom_conditions
                    import sqlite3 as _sl_kc
                    _conn_kc = _sl_kc.connect("stock.db")
                    _conn_kc.row_factory = _sl_kc.Row
                    _kc_result = calc_kiwoom_conditions(_conn_kc, "all")
                    _conn_kc.close()
                    _signal_cache['kiwoom_cond_all'] = {'data': _kc_result, 'at': _t.time()}
                    _total_kc = sum(len(v.get('stocks', [])) for v in _kc_result.values())
                    logger.info(f"[스크리너사전계산] 키움조건식 5종={_total_kc}종목")
                except Exception as _ekc:
                    logger.error(f"[스크리너사전계산] 키움조건식 오류: {_ekc}")

                # 6. 시장지표(investor-top) 사전 계산 — 탭 진입 즉시 로딩되도록
                try:
                    from routes.market_indicators import precompute_indicator_cache
                    precompute_indicator_cache()
                except Exception as _e3:
                    logger.error(f"[스크리너사전계산] 시장지표캐시 오류: {_e3}")

            finally:
                conn.close()

    except Exception as e:
        logger.error(f"[스크리너사전계산] {e}", exc_info=True)
    finally:
        _signal_cache.pop('_computing_fin_screener', None)
        _signal_cache.pop('_computing_combo_v2', None)


def _process_ai_combo_autotrade(combo_stocks: list):
    """AI 적극검토 종목 자동매매: 신규 편입 시 매수, 추세이탈 시 매도."""
    import sqlite3 as _sl
    from datetime import datetime as _dt
    from telegram_stock_dedup import filter_new as _filter_new_alerts, mark_sent as _mark_alert_sent

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
            alert_item = {"stock_code": code, "stock_name": name, "payload": tags_str}
            if _filter_new_alerts([alert_item], "ai_combo_buy_signal"):
                _send_telegram(msg, dedup_key=f"ai_combo_buy_{code}")
                _mark_alert_sent("ai_combo_buy_signal", [alert_item], payload_key="payload")
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
                alert_item = {"stock_code": code, "stock_name": name, "payload": exit_reason}
                if _filter_new_alerts([alert_item], f"ai_combo_sell_signal_{exit_reason}"):
                    _send_telegram(msg, dedup_key=f"ai_combo_sell_{code}_{exit_reason}")
                    _mark_alert_sent(f"ai_combo_sell_signal_{exit_reason}", [alert_item], payload_key="payload")
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
    # 무거운 Yahoo 갱신은 백그라운드 비동기 처리하고, API는 즉시 DB 최신값을 반환.
    if _is_market_hours() or _is_us_market_hours() or _us_macro_stale(db):
        _trigger_macro_refresh_async()
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
                except Exception: pass  # HTML 파싱 실패 시 순위 생략
                break
            elif "코스닥" in txt and len(txt) < 20:
                result["market"] = "KOSDAQ"
                rank_txt = txt.replace("코스닥","").replace("위","").replace(",","").strip()
                try: result["mktcap_rank"] = int(rank_txt)
                except Exception: pass  # HTML 파싱 실패 시 순위 생략
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
                except Exception: pass  # HTML 파싱 실패 시 시총 생략

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
def get_stock_chart(stock_code: str, days: int = 30, basis: str = "research_adjusted", db: Session = Depends(get_db)):
    """
    특정 종목의 차트 시계열 데이터를 반환합니다.
    """
    if basis == "research_adjusted":
        rows = processor.get_chart_data(db, stock_code, days)
        for row in rows:
            row["price_basis"] = "adjusted_intended_mixed_risk"
        return rows
    if basis == "canonical_research":
        start_iso = (datetime.now() - timedelta(days=max(30, min(days, 3650)))).strftime("%Y-%m-%d")
        conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
        try:
            rows = conn.execute(
                """SELECT date,open,high,low,close,volume,inst_net_buy,frn_net_buy,ind_net_buy,
                          canonical_quality,return_usable
                   FROM canonical_price_history_v WHERE stock_code=? AND date>=? ORDER BY date""",
                (stock_code, start_iso),
            ).fetchall()
            return [{**dict(r), "price_basis": "canonical_research"} for r in rows]
        finally:
            conn.close()
    if basis not in {"execution_raw", "confirmed_actions_adjusted"}:
        raise HTTPException(status_code=400, detail="basis must be research_adjusted, canonical_research, execution_raw, or confirmed_actions_adjusted")
    start = (datetime.now() - timedelta(days=max(30, min(days, 3650)))).strftime("%Y%m%d")
    table = "stock_price_daily" if basis == "execution_raw" else "stock_price_daily_adjusted_v"
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        rows = conn.execute(
            f"""SELECT bas_dt, open_price, high_price, low_price, close_price, volume
                FROM {table} WHERE stock_code=? AND bas_dt>=? ORDER BY bas_dt""",
            (stock_code, start),
        ).fetchall()
        return [{
            "date": f"{r['bas_dt'][:4]}-{r['bas_dt'][4:6]}-{r['bas_dt'][6:8]}",
            "open": r["open_price"], "high": r["high_price"], "low": r["low_price"],
            "close": r["close_price"], "volume": r["volume"] or 0,
            "inst_net_buy": 0, "frn_net_buy": 0, "ind_net_buy": 0,
            "price_basis": basis,
        } for r in rows]
    finally:
        conn.close()


@app.get("/api/dashboard/corporate-actions/{stock_code}")
def get_stock_corporate_actions(stock_code: str, days: int = 365):
    """Return capital/share events that should be annotated on the price chart."""
    if not (stock_code.isdigit() and len(stock_code) == 6):
        raise HTTPException(status_code=400, detail="stock_code must be a 6 digit code")
    days = max(30, min(int(days), 3650))
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        rows = conn.execute(
            """
            SELECT rcept_dt, report_nm, dart_url, rcept_no
            FROM dart_disclosures
            WHERE stock_code=? AND rcept_dt>=?
              AND (
                report_nm LIKE '%주식분할%' OR report_nm LIKE '%액면분할%'
                OR report_nm LIKE '%주식병합%' OR report_nm LIKE '%액면병합%'
                OR report_nm LIKE '%무상증자%' OR report_nm LIKE '%유무상증자%'
                OR report_nm LIKE '%유상증자%'
              )
              AND report_nm NOT LIKE '%종속회사%'
            ORDER BY rcept_dt, rcept_no
            """,
            (stock_code, start_date),
        ).fetchall()

        events: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def add_event(event_date: str, event_type: str, label: str, title: str,
                      source: str, url: str | None = None) -> None:
            event_date = str(event_date or "").replace("-", "")[:8]
            if len(event_date) != 8:
                return
            iso_date = f"{event_date[:4]}-{event_date[4:6]}-{event_date[6:]}"
            key = (iso_date, event_type)
            if key in seen:
                return
            seen.add(key)
            events.append({
                "date": iso_date, "event_type": event_type, "label": label,
                "title": title, "source": source, "url": url,
            })

        for row in rows:
            name = str(row["report_nm"] or "")
            # 정정·청약·발행결과를 모두 그리면 한 번의 증자가 여러 마커로 보인다.
            # 결정 공시와 실제 가격 조정일에 가까운 권리락만 차트 이벤트로 사용한다.
            if name.startswith("["):
                continue
            compact_name = "".join(name.split())
            is_rights_drop = "권리락" in compact_name
            is_decision = (
                ("주요사항보고서" in compact_name and "결정" in compact_name)
                or compact_name.startswith(("유상증자결정", "무상증자결정", "유무상증자결정", "주식분할결정", "주식병합결정"))
            )
            if not (is_rights_drop or is_decision):
                continue
            # 유무상증자는 두 자본행위를 모두 표시한다.
            if "유무상증자" in name:
                add_event(row["rcept_dt"], "rights_issue", "유상", name, "DART", row["dart_url"])
                add_event(row["rcept_dt"], "bonus_issue", "무상", name, "DART", row["dart_url"])
            elif "무상증자" in name:
                add_event(row["rcept_dt"], "bonus_issue", "무상", name, "DART", row["dart_url"])
            elif "유상증자" in name:
                add_event(row["rcept_dt"], "rights_issue", "유상", name, "DART", row["dart_url"])
            if "주식병합" in name or "액면병합" in name:
                add_event(row["rcept_dt"], "stock_merge", "병합", name, "DART", row["dart_url"])
            elif "주식분할" in name or "액면분할" in name:
                add_event(row["rcept_dt"], "stock_split", "분할", name, "DART", row["dart_url"])

        # 분할/병합 효력일 자동 감지 (2026-07-12 Claude 개선):
        # rcept_dt는 결정공시일이라 실제 가격 단절일과 다르다 (예: 101930 공시 3/26 vs 효력 4/30 ratio 0.26).
        # 결정공시 후 120일 내 가격 불연속(전일比 <0.6 또는 >1.6)을 찾아 효력일 마커를 추가한다.
        split_decisions = [e for e in events if e["event_type"] in ("stock_split", "stock_merge")]
        if split_decisions:
            px = conn.execute(
                "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date",
                (stock_code,),
            ).fetchall()
            for ev in split_decisions:
                d0 = ev["date"]
                d1 = (datetime.strptime(d0, "%Y-%m-%d") + timedelta(days=120)).strftime("%Y-%m-%d")
                prev_close = None
                for r in px:
                    if r["date"] <= d0:
                        prev_close = float(r["close"])
                        continue
                    if r["date"] > d1:
                        break
                    c = float(r["close"])
                    if prev_close and prev_close > 0:
                        ratio = c / prev_close
                        if ev["event_type"] == "stock_split" and ratio < 0.6:
                            add_event(r["date"], "stock_split_effective", "분할효력",
                                      f"주식분할 효력 추정일 (전일比 {ratio:.2f}, 비수정주가 단절)", "가격감지")
                            break
                        if ev["event_type"] == "stock_merge" and ratio > 1.6:
                            add_event(r["date"], "stock_merge_effective", "병합효력",
                                      f"주식병합 효력 추정일 (전일比 {ratio:.2f}, 비수정주가 단절)", "가격감지")
                            break
                    prev_close = c

        # KRX 상장주식수 변경일은 공시 결정일과 별개인 실제 변경 단서다.
        # ⚠️ 2026-07-12 검증: stock_base_info_changes 현재 0행 — 수집 파이프라인 미가동 상태(마커 미발생, 코드는 유지).
        krx_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        krx_rows = conn.execute(
            """
            SELECT change_date, old_value, new_value, description
            FROM stock_base_info_changes
            WHERE stock_code=? AND change_type='shares_issued' AND change_date>=?
            ORDER BY change_date
            """,
            (stock_code, krx_start),
        ).fetchall()
        for row in krx_rows:
            title = row["description"] or f"상장주식수 {row['old_value']} → {row['new_value']}"
            add_event(row["change_date"], "shares_change", "주식수", title, "KRX")
        normalized_rows = conn.execute(
            """
            SELECT event_date, event_type, old_shares, new_shares, share_ratio,
                   adjustment_status, evidence_report_name, evidence_url
            FROM corporate_action_events
            WHERE stock_code=? AND event_date>=?
            ORDER BY event_date
            """,
            (stock_code, krx_start),
        ).fetchall()
        normalized_labels = {
            "stock_split": ("stock_split", "분할"),
            "stock_merge_or_reduction": ("stock_merge", "병합/감자"),
            "bonus_issue": ("bonus_issue", "무상"),
            "rights_issue": ("rights_issue", "유상"),
        }
        for row in normalized_rows:
            chart_type, label = normalized_labels.get(row["event_type"], ("shares_change", "주식수"))
            title = row["evidence_report_name"] or (
                f"상장주식수 {int(row['old_shares'] or 0):,}주 → {int(row['new_shares'] or 0):,}주 "
                f"({float(row['share_ratio'] or 0):.3f}배)"
            )
            status = "보정계수 확정" if row["adjustment_status"] == "factor_confirmed" else "유형 검토 필요"
            add_event(row["event_date"], chart_type, label, f"{title} · {status}", "정규화 자본행위", row["evidence_url"])
        return {"stock_code": stock_code, "count": len(events), "events": sorted(events, key=lambda e: e["date"])}
    finally:
        conn.close()


@app.get("/api/dashboard/hypothesis-reports")
def get_hypothesis_reports():
    """Research-report registry for the signal-direction analysis page."""
    report_dir = Path("research_outputs/deep_drawdown_recovery_5y")
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        return {"reports": [], "notice": "연구 결과가 아직 생성되지 않았습니다."}
    try:
        summary = _json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"연구 결과 로드 실패: {exc}") from exc
    return {
        "reports": [{
            "id": "deep_drawdown_recovery_5y",
            "title": "낙폭과대·52주 신저가 회복",
            "short_title": "낙폭과대 회복",
            "hypothesis": "고점 대비 60~70% 이상 하락하거나 52주 최저가를 기록한 종목은 하방이 제한되어 좋은 매수 기회가 된다.",
            "verdict": "rejected",
            "verdict_label": "가설 기각",
            "updated_at": datetime.fromtimestamp(summary_path.stat().st_mtime).isoformat(timespec="seconds"),
            "summary": summary,
            "methodology": {
                "event_period": summary.get("period"),
                "trigger": "52주 고점 대비 -60% 이하 또는 52주 저가 2% 이내",
                "recovery": "저점 형성 10거래일 후 +30%, 종가>MA20, MA20 상승",
                "outcome": "진입 및 반등확인 후 60·120·252거래일 수익률",
                "bias_controls": ["비활성·상장폐지 가능 종목 포함", "종목당 첫 사건 민감도", "액면분할·병합 등 비연속 가격점프 제외"],
            },
            "report_path": str(report_dir / "report.md"),
        }]
    }


@app.get("/api/dashboard/data-lineage")
def get_data_lineage_catalog():
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM data_lineage_catalog ORDER BY metric_key")]
    finally: conn.close()


@app.get("/api/dashboard/data-lineage/{metric_key:path}")
def get_data_lineage(metric_key: str):
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        row = conn.execute("SELECT * FROM data_lineage_catalog WHERE metric_key=?", (metric_key,)).fetchone()
        if not row: raise HTTPException(status_code=404, detail="metric lineage not found")
        return dict(row)
    finally: conn.close()


@app.get("/api/dashboard/market-regime/latest")
def get_latest_market_regime():
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        row = conn.execute("SELECT * FROM market_regime_daily ORDER BY trade_date DESC LIMIT 1").fetchone()
        return dict(row) if row else {}
    finally: conn.close()


@app.get("/api/dashboard/explainable-signals/{stock_code}")
def get_explainable_stock_signals(stock_code: str, limit: int = 30):
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        rows = conn.execute(
            """SELECT * FROM explainable_stock_signals WHERE stock_code=?
               ORDER BY CASE signal_strength WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        ABS(weighted_impact_score) DESC LIMIT ?""", (stock_code, max(1,min(limit,100)))
        ).fetchall()
        return {"stock_code":stock_code,"count":len(rows),"signals":[dict(r) for r in rows]}
    finally: conn.close()


@app.get("/api/dashboard/live-signal-outcomes/{stock_code}")
def get_live_signal_outcomes(stock_code: str, limit: int = 100):
    conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
    try:
        rows = conn.execute(
            """SELECT s.signal_id,s.signal_type,s.strategy_id,s.signal_date,s.entry_date,s.entry_price,
                      s.quality_score,s.confidence_score,s.action,o.horizon_days,o.outcome_date,
                      o.return_pct,o.max_gain_pct,o.max_loss_pct,o.status
               FROM live_signal_registry s LEFT JOIN live_signal_outcomes o USING(signal_id)
               WHERE s.stock_code=? ORDER BY s.signal_date DESC,o.horizon_days LIMIT ?""",
            (stock_code,max(1,min(limit,500)))
        ).fetchall()
        return {"stock_code":stock_code,"count":len(rows),"outcomes":[dict(r) for r in rows]}
    finally: conn.close()


def _load_sp500_symbols() -> set[str]:
    """S&P500 구성 종목 심볼 집합(간이 캐시)."""
    now = _tm.time()
    if (now - float(_us_sp500_cache.get("ts") or 0)) < 86400 and _us_sp500_cache.get("symbols"):
        return set(_us_sp500_cache["symbols"])
    symbols: set[str] = set()
    try:
        # 공개 CSV(위키 기반) - 실패 시 빈 집합 반환
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
        res = _requests.get(url, timeout=8)
        if res.status_code == 200:
            lines = res.text.splitlines()
            for line in lines[1:]:
                parts = line.split(",")
                if not parts:
                    continue
                s = parts[0].strip().upper().replace(".", "-")
                if s:
                    symbols.add(s)
    except Exception:
        pass
    _us_sp500_cache["symbols"] = symbols
    _us_sp500_cache["ts"] = now
    return symbols


def _load_sec_symbol_cik_map() -> dict[str, str]:
    """SEC ticker->CIK 매핑 캐시."""
    now = _tm.time()
    if (now - float(_us_sec_map_cache.get("ts") or 0)) < 86400 and _us_sec_map_cache.get("map"):
        return dict(_us_sec_map_cache["map"])
    out: dict[str, str] = {}
    try:
        headers = {
            "User-Agent": "StockDashboard AdminContact admin@example.com",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.sec.gov/",
        }
        url = "https://www.sec.gov/files/company_tickers.json"
        res = _requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            payload = res.json()
            for _, row in payload.items():
                t = str(row.get("ticker", "")).upper()
                cik = str(row.get("cik_str", "")).strip()
                if t and cik.isdigit():
                    out[t] = cik.zfill(10)
    except Exception:
        pass
    _us_sec_map_cache["map"] = out
    _us_sec_map_cache["ts"] = now
    return out


def _build_us_stock_base_items() -> list:
    """전종목 기준 데이터(시총/가격/팩터) 조립 — market/q 필터 적용 전 원본.
    us_price_history(390만행) GROUP BY MAX(date) 자기조인이 핵심 비용이라
    market/q/limit 조합이 바뀔 때마다 매번 다시 계산할 필요가 없음."""
    import sqlite3 as _sl3
    _ensure_us_tables()
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    rows = conn.execute(
        """
        WITH latest_price AS (
            SELECT p.ticker, p.date AS latest_date, p.close AS latest_close
            FROM us_price_history p
            JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM us_price_history
                GROUP BY ticker
            ) mx ON mx.ticker = p.ticker AND mx.max_date = p.date
        )
        SELECT m.ticker,
               m.country,
               COALESCE(s.market_cap, m.market_cap, r.market_cap, 0) AS market_cap,
               COALESCE(s.pbr, r.pbr, f.pbr) AS pbr,
               COALESCE(s.per, r.per, f.per) AS per,
               COALESCE(lp.latest_date, s.as_of_date, r.latest_date) AS latest_date,
               COALESCE(lp.latest_close, s.price, r.latest_close, 0) AS latest_close,
               COALESCE(m.company_name, m.ticker) AS company_name,
               COALESCE(m.index_name, '') AS index_name,
               COALESCE(m.sector, f.sector, '') AS sector,
               COALESCE(m.industry, f.industry, '') AS industry,
               f.system_action,
               f.total_score,
               f.rs_score,
               f.return_3m,
               f.high_52w,
               f.low_52w,
               f.atr_stop_loss,
               f.atr_risk_pct
        FROM us_stock_meta m
        LEFT JOIN us_frontend_snapshot s ON s.ticker = m.ticker
        LEFT JOIN radar_market_cache r ON r.ticker = m.ticker
        LEFT JOIN us_factor_snapshot f ON f.ticker = m.ticker
        LEFT JOIN latest_price lp ON lp.ticker = m.ticker
        WHERE UPPER(COALESCE(m.country,''))='US'
          AND m.ticker IS NOT NULL AND m.ticker <> ''
        ORDER BY market_cap DESC, m.ticker ASC
        """
    ).fetchall()
    conn.close()
    sp500 = set()
    items = []
    for idx, r in enumerate(rows, start=1):
        ticker = str(r["ticker"] or "").upper()
        if not ticker:
            continue
        index_name = str(r["index_name"] or "").upper()
        market_bucket = "S&P500" if ("S&P" in index_name or "SP500" in index_name or ticker in sp500) else "NASDAQ"
        name = str(r["company_name"] or ticker)
        items.append({
            "ticker": ticker,
            "name": name,
            "market": market_bucket,
            "index_name": r["index_name"] or market_bucket,
            "sector": r["sector"] or "",
            "industry": r["industry"] or "",
            "market_cap": float(r["market_cap"] or 0),
            "market_cap_rank": idx,
            "price": float(r["latest_close"] or 0),
            "pbr": float(r["pbr"] or 0) if r["pbr"] is not None else None,
            "per": float(r["per"] or 0) if r["per"] is not None else None,
            "as_of": r["latest_date"],
            "system_action": r["system_action"] or None,
            "total_score": float(r["total_score"]) if r["total_score"] is not None else None,
            "rs_score": float(r["rs_score"]) if r["rs_score"] is not None else None,
            "return_3m": float(r["return_3m"]) if r["return_3m"] is not None else None,
            "high_52w": float(r["high_52w"]) if r["high_52w"] is not None else None,
            "low_52w": float(r["low_52w"]) if r["low_52w"] is not None else None,
            "atr_stop_loss": float(r["atr_stop_loss"]) if r["atr_stop_loss"] is not None else None,
            "atr_risk_pct": float(r["atr_risk_pct"]) if r["atr_risk_pct"] is not None else None,
        })
    return items


def _get_us_stock_base_items_cached() -> list:
    now = _tm.time()
    if now - _us_stock_base_items_cache["ts"] < 300:
        return _us_stock_base_items_cache["items"]
    items = _build_us_stock_base_items()
    _us_stock_base_items_cache["ts"] = now
    _us_stock_base_items_cache["items"] = items
    return items


@app.get("/api/us/stocks/list")
def get_us_stocks_list(q: str = "", market: str = "all", limit: int = 300):
    """미국 종목 목록(시총/순위 포함). source: us_stock_meta 중심.
    기초 데이터는 5분 캐시(장중 갱신 빈도 대비 US 종목은 일배치라 충분히 안전) —
    market/q/limit 조합이 바뀌어도 비싼 DB 조회를 다시 하지 않음."""
    base_items = _get_us_stock_base_items_cached()
    qn = (q or "").strip().upper()
    items = []
    for it in base_items:
        if market != "all" and it["market"] != market:
            continue
        if qn and (qn not in it["ticker"] and qn not in it["name"].upper()):
            continue
        items.append(it)
    return items[:max(20, min(limit, 10000))]


@app.get("/api/us/indices")
def get_us_indices():
    """나스닥/S&P500 최신 지수 테이블."""
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    out = []
    for code, name in (("^IXIC", "NASDAQ"), ("^GSPC", "S&P500")):
        rows = conn.execute(
            """
            SELECT date, close
            FROM price_history
            WHERE stock_code = ?
            ORDER BY date DESC
            LIMIT 2
            """,
            (code,),
        ).fetchall()
        if not rows:
            out.append({"symbol": code, "name": name, "close": None, "change_pct": None, "as_of": None})
            continue
        latest = float(rows[0]["close"] or 0)
        prev = float(rows[1]["close"] or 0) if len(rows) > 1 else 0.0
        chg = None if prev == 0 else ((latest - prev) / prev) * 100.0
        out.append({
            "symbol": code,
            "name": name,
            "close": latest,
            "change_pct": round(chg, 3) if chg is not None else None,
            "as_of": rows[0]["date"],
        })
    conn.close()
    return out


def _ensure_us_tables() -> None:
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db", timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_financial_data (
            ticker TEXT NOT NULL,
            period_end TEXT NOT NULL,
            period_type TEXT NOT NULL, -- annual|quarter
            revenue REAL,
            cogs REAL,
            gross_profit REAL,
            operating_expense REAL,
            sga REAL,
            rnd REAL,
            ebitda REAL,
            operating_income REAL,
            interest_expense REAL,
            pretax_income REAL,
            tax_expense REAL,
            net_income REAL,
            assets REAL,
            liabilities REAL,
            equity REAL,
            capital REAL,
            eps REAL,
            bps REAL,
            roe REAL,
            roa REAL,
            per REAL,
            pbr REAL,
            opm REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, period_end, period_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_cashflow_data (
            ticker TEXT NOT NULL,
            period_end TEXT NOT NULL,
            period_type TEXT NOT NULL, -- annual|quarter
            operating_cf REAL,
            change_working_capital REAL,
            stock_based_compensation REAL,
            investing_cf REAL,
            financing_cf REAL,
            capex REAL,
            dividends_paid REAL,
            cash_begin REAL,
            cash_end REAL,
            free_cf REAL,
            depreciation REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, period_end, period_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_price_history (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL,
            volume REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_fin_ticker_type ON us_financial_data(ticker, period_type, period_end DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_cf_ticker_type ON us_cashflow_data(ticker, period_type, period_end DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_price_ticker_date ON us_price_history(ticker, date DESC)")
    # Legacy DB 호환: 컬럼이 없는 경우에만 추가
    fin_cols = {
        "cogs": "REAL", "gross_profit": "REAL", "operating_expense": "REAL", "sga": "REAL", "rnd": "REAL", "ebitda": "REAL",
        "interest_expense": "REAL", "pretax_income": "REAL", "tax_expense": "REAL",
        "assets": "REAL", "liabilities": "REAL", "equity": "REAL", "capital": "REAL",
    }
    cf_cols = {
        "change_working_capital": "REAL", "stock_based_compensation": "REAL", "dividends_paid": "REAL",
        "cash_begin": "REAL", "cash_end": "REAL", "free_cf": "REAL",
    }
    existing_fin = {r[1] for r in conn.execute("PRAGMA table_info(us_financial_data)").fetchall()}
    for col, typ in fin_cols.items():
        if col not in existing_fin:
            conn.execute(f"ALTER TABLE us_financial_data ADD COLUMN {col} {typ}")
    existing_cf = {r[1] for r in conn.execute("PRAGMA table_info(us_cashflow_data)").fetchall()}
    for col, typ in cf_cols.items():
        if col not in existing_cf:
            conn.execute(f"ALTER TABLE us_cashflow_data ADD COLUMN {col} {typ}")
    # us_price_history OHLC 컬럼 migration
    price_ohlc_cols = {"open": "REAL", "high": "REAL", "low": "REAL"}
    existing_price = {r[1] for r in conn.execute("PRAGMA table_info(us_price_history)").fetchall()}
    for col, typ in price_ohlc_cols.items():
        if col not in existing_price:
            try:
                conn.execute(f"ALTER TABLE us_price_history ADD COLUMN {col} {typ}")
            except Exception:
                pass  # DB locked 시 무시 (다음 재시작 시 재시도)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_stock_meta (
            ticker TEXT PRIMARY KEY,
            company_name TEXT,
            exchange TEXT,
            index_name TEXT,
            sector TEXT,
            industry TEXT,
            market_cap REAL,
            country TEXT DEFAULT 'US',
            currency TEXT DEFAULT 'USD',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_factor_snapshot (
            ticker TEXT PRIMARY KEY,
            as_of_date TEXT,
            price REAL,
            market_cap REAL,
            sector TEXT,
            industry TEXT,
            return_1m REAL,
            return_3m REAL,
            return_6m REAL,
            return_1y REAL,
            ma50 REAL,
            ma200 REAL,
            above_200ma INTEGER,
            high_52w REAL,
            low_52w REAL,
            revenue_growth_yoy REAL,
            op_income_growth_yoy REAL,
            net_income_growth_yoy REAL,
            op_margin REAL,
            roe REAL,
            roa REAL,
            per REAL,
            pbr REAL,
            eps REAL,
            bps REAL,
            debt_to_equity REAL,
            fcf_yield REAL,
            total_score REAL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # us_factor_snapshot 마이그레이션
    factor_extra_cols = {
        "graham_intrinsic": "REAL", "graham_discount": "REAL",
        "atr14": "REAL", "atr_stop_loss": "REAL", "atr_risk_pct": "REAL",
        "rs_score": "REAL", "ma5": "REAL", "ma20": "REAL", "ma60": "REAL",
        "system_action": "TEXT"
    }
    existing_snap = {r[1] for r in conn.execute("PRAGMA table_info(us_factor_snapshot)").fetchall()}
    for col, typ in factor_extra_cols.items():
        if col not in existing_snap:
            try:
                conn.execute(f"ALTER TABLE us_factor_snapshot ADD COLUMN {col} {typ}")
            except Exception:
                pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_data_integrity_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ticker TEXT,
            yahoo_price REAL,
            external_price REAL,
            source TEXT,
            abs_diff REAL,
            pct_diff REAL,
            passed INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_disclosures (
            ticker TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            form TEXT,
            title TEXT,
            url TEXT,
            accession_no TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, filing_date, form, accession_no)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_frontend_snapshot (
            ticker TEXT PRIMARY KEY,
            as_of_date TEXT,
            price REAL,
            change_pct REAL,
            market_cap REAL,
            annual_revenue REAL,
            annual_operating_income REAL,
            annual_net_income REAL,
            opm REAL,
            fifty_two_week_high REAL,
            fifty_two_week_low REAL,
            per REAL,
            pbr REAL,
            eps REAL,
            bps REAL,
            roe REAL,
            roa REAL,
            financial_annual_json TEXT,
            financial_quarter_json TEXT,
            cashflow_annual_json TEXT,
            cashflow_quarter_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_factor_sector ON us_factor_snapshot(sector, total_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_integrity_runat ON us_data_integrity_audit(run_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_disclosures_ticker_date ON us_disclosures(ticker, filing_date DESC)")
    conn.commit()
    conn.close()


def _us_to_float(v):
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


def _refresh_us_stock_data(ticker: str) -> dict:
    import sqlite3 as _sl3
    import yfinance as _yf
    tk = (ticker or "").upper().strip()
    _ensure_us_tables()

    yf_t = _yf.Ticker(tk)
    info = {}
    try:
        fi = yf_t.fast_info or {}
        if isinstance(fi, dict):
            info.update(fi)
    except Exception:
        pass
    try:
        i2 = yf_t.info or {}
        if isinstance(i2, dict):
            info.update(i2)
    except Exception:
        pass

    conn = _sl3.connect("stock.db", timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = _sl3.Row

    # 1) 가격 5년 적재 (OHLCV)
    try:
        h = yf_t.history(period="5y", interval="1d", auto_adjust=True)
        if h is not None and not h.empty:
            for d, row in h.iterrows():
                dstr = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO us_price_history (ticker, date, open, high, low, close, volume, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        tk, dstr,
                        _us_to_float(row.get("Open")),
                        _us_to_float(row.get("High")),
                        _us_to_float(row.get("Low")),
                        _us_to_float(row.get("Close")),
                        _us_to_float(row.get("Volume")),
                    ),
                )
    except Exception:
        pass


    def _pick(df, col, names):
        if df is None or df.empty:
            return None
        for n in names:
            if n in df.index:
                return _us_to_float(df.at[n, col])
        return None

    # 2) 재무제표(연간 4년+, 분기 8개)
    def _upsert_fin(df, period_type: str):
        if df is None or df.empty:
            return 0
        c = 0
        for col in list(df.columns)[: (8 if period_type == "quarter" else 6)]:
            p = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
            revenue = _pick(df, col, ["Total Revenue", "Revenue"])
            cogs = _pick(df, col, ["Cost Of Revenue", "Cost Of Goods Sold"])
            gross_profit = _pick(df, col, ["Gross Profit"])
            opx = _pick(df, col, ["Operating Expense", "Operating Expenses"])
            sga = _pick(df, col, ["Selling General And Administration", "Selling General Administration"])
            rnd = _pick(df, col, ["Research And Development", "Research Development"])
            ebitda = _pick(df, col, ["EBITDA"])
            opi = _pick(df, col, ["Operating Income", "Operating Income Or Loss"])
            ie = _pick(df, col, ["Interest Expense", "Interest Expense Non Operating"])
            ptx = _pick(df, col, ["Pretax Income", "Income Before Tax"])
            tax = _pick(df, col, ["Tax Provision", "Income Tax Expense"])
            ni = _pick(df, col, ["Net Income", "Net Income Common Stockholders"])
            eps = _pick(df, col, ["Diluted EPS", "Basic EPS"])
            opm = (opi / revenue * 100.0) if (opi is not None and revenue not in (None, 0)) else None
            conn.execute(
                """
                INSERT OR REPLACE INTO us_financial_data
                (ticker, period_end, period_type, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
                 operating_income, interest_expense, pretax_income, tax_expense, net_income,
                 assets, liabilities, equity, capital, eps, bps, roe, roa, per, pbr, opm, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL, NULL, NULL, ?, CURRENT_TIMESTAMP)
                """,
                (tk, p, period_type, revenue, cogs, gross_profit, opx, sga, rnd, ebitda, opi, ie, ptx, tax, ni, eps, opm),
            )
            c += 1
        return c

    try:
        _upsert_fin(yf_t.financials, "annual")
    except Exception:
        pass
    try:
        _upsert_fin(yf_t.quarterly_financials, "quarter")
    except Exception:
        pass

    # 2-1) 재무상태표(자산/부채/자본/자본금) 보강
    def _upsert_bs(df, period_type: str):
        if df is None or df.empty:
            return 0
        c = 0
        for col in list(df.columns)[: (8 if period_type == "quarter" else 6)]:
            p = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
            assets = _pick(df, col, ["Total Assets"])
            liabilities = _pick(df, col, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
            equity = _pick(df, col, ["Stockholders Equity", "Total Equity Gross Minority Interest", "Total Stockholder Equity"])
            capital = _pick(df, col, ["Ordinary Shares Number", "Share Issued", "Common Stock Equity"])
            conn.execute(
                """
                UPDATE us_financial_data
                   SET assets=COALESCE(?, assets),
                       liabilities=COALESCE(?, liabilities),
                       equity=COALESCE(?, equity),
                       capital=COALESCE(?, capital),
                       updated_at=CURRENT_TIMESTAMP
                 WHERE ticker=? AND period_type=? AND period_end=?
                """,
                (assets, liabilities, equity, capital, tk, period_type, p),
            )
            c += 1
        return c

    try:
        _upsert_bs(yf_t.balance_sheet, "annual")
    except Exception:
        pass
    try:
        _upsert_bs(yf_t.quarterly_balance_sheet, "quarter")
    except Exception:
        pass

    # 3) 현금흐름(연간 4년+, 분기 8개)
    def _upsert_cf(df, period_type: str):
        if df is None or df.empty:
            return 0
        c = 0
        for col in list(df.columns)[: (8 if period_type == "quarter" else 6)]:
            p = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
            ocf = _pick(df, col, ["Operating Cash Flow"])
            wc = _pick(df, col, ["Change In Working Capital", "Changes In Working Capital"])
            sbc = _pick(df, col, ["Stock Based Compensation"])
            icf = _pick(df, col, ["Investing Cash Flow"])
            fcf = _pick(df, col, ["Financing Cash Flow"])
            capex = _pick(df, col, ["Capital Expenditure"])
            div = _pick(df, col, ["Cash Dividends Paid", "Dividends Paid"])
            cbegin = _pick(df, col, ["Beginning Cash Position"])
            cend = _pick(df, col, ["End Cash Position"])
            free_cf = (ocf - abs(capex)) if (ocf is not None and capex is not None) else None
            dep = _pick(df, col, ["Depreciation And Amortization", "Depreciation Amortization Depletion"])
            conn.execute(
                """
                INSERT OR REPLACE INTO us_cashflow_data
                (ticker, period_end, period_type, operating_cf, change_working_capital, stock_based_compensation,
                 investing_cf, financing_cf, capex, dividends_paid, cash_begin, cash_end, free_cf, depreciation, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (tk, p, period_type, ocf, wc, sbc, icf, fcf, capex, div, cbegin, cend, free_cf, dep),
            )
            c += 1
        return c

    try:
        _upsert_cf(yf_t.cashflow, "annual")
    except Exception:
        pass
    try:
        _upsert_cf(yf_t.quarterly_cashflow, "quarter")
    except Exception:
        pass

    # 4) valuation 보강 (최신 + 과거는 기간가 기준 계산)
    eps = _us_to_float(info.get("trailingEps"))
    bps = _us_to_float(info.get("bookValue"))
    roe = _us_to_float(info.get("returnOnEquity"))
    roa = _us_to_float(info.get("returnOnAssets"))
    per = _us_to_float(info.get("trailingPE"))
    pbr = _us_to_float(info.get("priceToBook"))
    conn.execute(
        """
        UPDATE us_financial_data
           SET eps=COALESCE(?, eps),
               bps=COALESCE(?, bps),
               roe=COALESCE(?, roe),
               roa=COALESCE(?, roa),
               per=COALESCE(?, per),
               pbr=COALESCE(?, pbr),
               updated_at=CURRENT_TIMESTAMP
         WHERE ticker=?
           AND period_type='annual'
           AND period_end=(SELECT MAX(period_end) FROM us_financial_data WHERE ticker=? AND period_type='annual')
        """,
        (eps, bps, (roe * 100.0 if roe is not None and roe < 2 else roe), (roa * 100.0 if roa is not None and roa < 2 else roa), per, pbr, tk, tk),
    )
    # 과거 기간은 해당 기간 종가 + EPS/BPS로 계산 보강 및 gross_profit/opm/ebitda 파생
    conn.execute(
        """
        UPDATE us_financial_data
           SET per = CASE
                        WHEN (per IS NULL OR per=0) AND eps IS NOT NULL AND eps != 0 THEN (
                          SELECT close/eps FROM us_price_history p
                           WHERE p.ticker=us_financial_data.ticker AND p.date<=us_financial_data.period_end
                           ORDER BY p.date DESC LIMIT 1
                        )
                        ELSE per
                     END,
               pbr = CASE
                        WHEN (pbr IS NULL OR pbr=0) AND bps IS NOT NULL AND bps != 0 THEN (
                          SELECT close/bps FROM us_price_history p
                           WHERE p.ticker=us_financial_data.ticker AND p.date<=us_financial_data.period_end
                           ORDER BY p.date DESC LIMIT 1
                        )
                        ELSE pbr
                     END,
               gross_profit = COALESCE(gross_profit, revenue - COALESCE(cogs, 0)),
               opm = CASE WHEN (opm IS NULL OR opm = 0) AND operating_income IS NOT NULL AND revenue IS NOT NULL AND revenue != 0 THEN (operating_income / revenue) * 100.0 ELSE opm END,
               ebitda = COALESCE(ebitda, operating_income + COALESCE((
                   SELECT c.depreciation FROM us_cashflow_data c
                    WHERE c.ticker = us_financial_data.ticker AND c.period_end = us_financial_data.period_end AND c.period_type = us_financial_data.period_type
               ), 0))
         WHERE ticker=?
        """,
        (tk,),
    )

    # 5) 메타 저장
    company_name = str(info.get("shortName") or info.get("longName") or tk)
    exchange = str(info.get("exchange") or info.get("fullExchangeName") or "")
    sector = str(info.get("sector") or "")
    industry = str(info.get("industry") or "")
    market_cap = _us_to_float(info.get("marketCap"))
    sp500 = _load_sp500_symbols()
    idx_name = "S&P500" if tk in sp500 else "NASDAQ"
    conn.execute(
        """
        INSERT OR REPLACE INTO us_stock_meta
        (ticker, company_name, exchange, index_name, sector, industry, market_cap, country, currency, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'US', 'USD', CURRENT_TIMESTAMP)
        """,
        (tk, company_name, exchange, idx_name, sector, industry, market_cap),
    )

    # 6) 팩터 스냅샷 계산 및 저장
    px = [dict(r) for r in conn.execute(
        "SELECT date, close FROM us_price_history WHERE ticker=? ORDER BY date DESC LIMIT 300",
        (tk,),
    ).fetchall()]
    px_desc = [float(r["close"] or 0) for r in px if r["close"] is not None]
    as_of_date = px[0]["date"] if px else None
    p0 = px_desc[0] if px_desc else None
    def _ret(n):
        if p0 is None or len(px_desc) <= n or px_desc[n] == 0:
            return None
        return ((p0 - px_desc[n]) / px_desc[n]) * 100.0
    r1m = _ret(21)
    r3m = _ret(63)
    r6m = _ret(126)
    r1y = _ret(252)
    ma50 = (sum(px_desc[:50]) / 50.0) if len(px_desc) >= 50 else None
    ma200 = (sum(px_desc[:200]) / 200.0) if len(px_desc) >= 200 else None
    above_200 = 1 if (p0 is not None and ma200 is not None and p0 >= ma200) else 0
    high_52w = max(px_desc[:252]) if len(px_desc) >= 252 else (max(px_desc) if px_desc else None)
    low_52w = min(px_desc[:252]) if len(px_desc) >= 252 else (min(px_desc) if px_desc else None)

    fin_rows = [dict(r) for r in conn.execute(
        """
        SELECT period_end, revenue, operating_income, net_income, opm, roe, roa, per, pbr, eps, bps
        FROM us_financial_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 2
        """, (tk,)
    ).fetchall()]
    cf_rows = [dict(r) for r in conn.execute(
        """
        SELECT period_end, operating_cf, capex
        FROM us_cashflow_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 2
        """, (tk,)
    ).fetchall()]
    rev_yoy = opi_yoy = ni_yoy = None
    if len(fin_rows) >= 2:
        c, p = fin_rows[0], fin_rows[1]
        if p.get("revenue") not in (None, 0):
            rev_yoy = ((float(c.get("revenue") or 0) - float(p.get("revenue") or 0)) / float(p["revenue"])) * 100.0
        if p.get("operating_income") not in (None, 0):
            opi_yoy = ((float(c.get("operating_income") or 0) - float(p.get("operating_income") or 0)) / float(p["operating_income"])) * 100.0
        if p.get("net_income") not in (None, 0):
            ni_yoy = ((float(c.get("net_income") or 0) - float(p.get("net_income") or 0)) / float(p["net_income"])) * 100.0
    latest_fin = fin_rows[0] if fin_rows else {}
    fcf_yield = None
    if cf_rows and p0 and market_cap and market_cap > 0:
        ocf = float(cf_rows[0].get("operating_cf") or 0)
        capex = float(cf_rows[0].get("capex") or 0)
        fcf = ocf - abs(capex)
        fcf_yield = (fcf / market_cap) * 100.0

    # 간이 총점(문서 5개 축 반영)
    score = 50.0
    for v, w in ((rev_yoy, 6), (opi_yoy, 5), (ni_yoy, 4), (r6m, 6), (r1y, 6), (latest_fin.get("opm"), 5), (latest_fin.get("roe"), 5), (fcf_yield, 5)):
        if v is None:
            continue
        if v > 0:
            score += min(10.0, float(v) / 5.0) * (w / 10.0)
        else:
            score += max(-10.0, float(v) / 5.0) * (w / 10.0)
    if above_200:
        score += 3.0
    score = max(0.0, min(100.0, score))

    conn.execute(
        """
        INSERT OR REPLACE INTO us_factor_snapshot
        (ticker, as_of_date, price, market_cap, sector, industry, return_1m, return_3m, return_6m, return_1y,
         ma50, ma200, above_200ma, high_52w, low_52w, revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy,
         op_margin, roe, roa, per, pbr, eps, bps, debt_to_equity, fcf_yield, total_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            tk, as_of_date, p0, market_cap, sector, industry, r1m, r3m, r6m, r1y,
            ma50, ma200, above_200, high_52w, low_52w, rev_yoy, opi_yoy, ni_yoy,
            latest_fin.get("opm"), latest_fin.get("roe"), latest_fin.get("roa"), latest_fin.get("per"), latest_fin.get("pbr"),
            latest_fin.get("eps"), latest_fin.get("bps"), _us_to_float(info.get("debtToEquity")), fcf_yield, score
        ),
    )

    # 7) 프론트엔드 즉시표시용 스냅샷 적재 (외부 호출 없이 화면 구성 가능)
    fin_annual_full = [dict(r) for r in conn.execute(
        """
        SELECT period_end AS period, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
               operating_income, interest_expense, pretax_income, tax_expense, net_income,
               assets, liabilities, equity, capital, eps, bps, roe, roa, per, pbr, opm
        FROM us_financial_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 16
        """,
        (tk,),
    ).fetchall()]
    fin_quarter_full = [dict(r) for r in conn.execute(
        """
        SELECT period_end AS period, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
               operating_income, interest_expense, pretax_income, tax_expense, net_income,
               assets, liabilities, equity, capital, eps, bps, roe, roa, per, pbr, opm
        FROM us_financial_data WHERE ticker=? AND period_type='quarter'
        ORDER BY period_end DESC LIMIT 24
        """,
        (tk,),
    ).fetchall()]
    cf_annual_full = [dict(r) for r in conn.execute(
        """
        SELECT period_end AS period, operating_cf, change_working_capital, stock_based_compensation,
               investing_cf, financing_cf, capex, dividends_paid, cash_begin, cash_end, free_cf, depreciation
        FROM us_cashflow_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 16
        """,
        (tk,),
    ).fetchall()]
    cf_quarter_full = [dict(r) for r in conn.execute(
        """
        SELECT period_end AS period, operating_cf, change_working_capital, stock_based_compensation,
               investing_cf, financing_cf, capex, dividends_paid, cash_begin, cash_end, free_cf, depreciation
        FROM us_cashflow_data WHERE ticker=? AND period_type='quarter'
        ORDER BY period_end DESC LIMIT 24
        """,
        (tk,),
    ).fetchall()]
    change_pct = _us_to_float(info.get("regularMarketChangePercent"))
    conn.execute(
        """
        INSERT OR REPLACE INTO us_frontend_snapshot
        (ticker, as_of_date, price, change_pct, market_cap, annual_revenue, annual_operating_income, annual_net_income, opm,
         fifty_two_week_high, fifty_two_week_low, per, pbr, eps, bps, roe, roa,
         financial_annual_json, financial_quarter_json, cashflow_annual_json, cashflow_quarter_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            tk, as_of_date, p0, change_pct, market_cap,
            latest_fin.get("revenue"), latest_fin.get("operating_income"), latest_fin.get("net_income"), latest_fin.get("opm"),
            high_52w, low_52w, latest_fin.get("per"), latest_fin.get("pbr"), latest_fin.get("eps"), latest_fin.get("bps"),
            latest_fin.get("roe"), latest_fin.get("roa"),
            _json.dumps(fin_annual_full, ensure_ascii=False),
            _json.dumps(fin_quarter_full, ensure_ascii=False),
            _json.dumps(cf_annual_full, ensure_ascii=False),
            _json.dumps(cf_quarter_full, ensure_ascii=False),
        ),
    )

    # 8) SEC 공시도 DB에 적재 (프론트 즉시 사용)
    try:
        sec_map = _load_sec_symbol_cik_map()
        cik = sec_map.get(tk)
        if cik:
            headers = {
                "User-Agent": "StockDashboard AdminContact admin@example.com",
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.sec.gov/",
            }
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            res = _requests.get(url, headers=headers, timeout=12)
            if res.status_code == 200:
                payload = res.json()
                recent = payload.get("filings", {}).get("recent", {})
                forms = recent.get("form", []) or []
                filed = recent.get("filingDate", []) or []
                acc_no = recent.get("accessionNumber", []) or []
                primary_doc = recent.get("primaryDocument", []) or []
                n = min(len(forms), len(filed), len(acc_no), len(primary_doc), 80)
                for i in range(n):
                    an = str(acc_no[i] or "")
                    an_no_dash = an.replace("-", "")
                    doc = str(primary_doc[i] or "")
                    sec_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{an_no_dash}/{doc}" if an_no_dash and doc else ""
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO us_disclosures
                        (ticker, filing_date, form, title, url, accession_no, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (tk, str(filed[i] or ""), str(forms[i] or ""), f"{forms[i]} Filing", sec_url, an),
                    )
    except Exception:
        pass

    conn.commit()
    conn.close()
    return {"ok": True, "ticker": tk}


@app.get("/api/us/screener/presets")
def get_us_screener_presets(
    preset: str = "trend_leaders",
    limit: int = 50,
    min_market_cap: float = 5_000_000_000,
    min_price: float = 5.0,
):
    """미국 종목 프리셋 스크리너 (주도주, 그레이엄 가치주, 고성장주, 스마트머니 유입)."""
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    _ensure_us_tables()

    p = (preset or "trend_leaders").lower().strip()
    limit = max(10, min(limit, 200))
    min_market_cap = max(0.0, float(min_market_cap or 0))
    min_price = max(0.0, float(min_price or 0))

    if p == "graham_bargains":
        rows = conn.execute(
            """
            SELECT f.ticker, m.company_name AS name, f.as_of_date, f.price, f.market_cap, f.per, f.pbr, f.op_margin AS opm,
                   f.eps, f.bps, (f.op_income_growth_yoy) AS growth
            FROM us_factor_snapshot f
            LEFT JOIN us_stock_meta m ON m.ticker = f.ticker
            WHERE f.eps > 0 AND f.bps > 0 AND f.price > 0
              AND COALESCE(f.market_cap,0) >= ?
              AND COALESCE(f.price,0) >= ?
              AND (SQRT(22.5 * f.eps * f.bps) - f.price) / SQRT(22.5 * f.eps * f.bps) >= 0.15
            ORDER BY ((SQRT(22.5 * f.eps * f.bps) - f.price) / SQRT(22.5 * f.eps * f.bps)) DESC
            LIMIT ?
            """,
            (min_market_cap, min_price, limit),
        ).fetchall()
    elif p == "high_growth":
        rows = conn.execute(
            """
            SELECT f.ticker, m.company_name AS name, f.as_of_date, f.price, f.market_cap, f.per, f.pbr, f.op_margin AS opm,
                   f.revenue_growth_yoy AS growth
            FROM us_factor_snapshot f
            LEFT JOIN us_stock_meta m ON m.ticker = f.ticker
            WHERE f.revenue_growth_yoy IS NOT NULL AND f.revenue_growth_yoy >= 15.0
              AND f.op_margin IS NOT NULL AND f.op_margin >= 10.0
              AND COALESCE(f.market_cap,0) >= ?
              AND COALESCE(f.price,0) >= ?
            ORDER BY f.revenue_growth_yoy DESC
            LIMIT ?
            """,
            (min_market_cap, min_price, limit),
        ).fetchall()
    else:  # trend_leaders (default)
        rows = conn.execute(
            """
            SELECT f.ticker, m.company_name AS name, f.as_of_date, f.price, f.market_cap, f.per, f.pbr, f.op_margin AS opm,
                   f.return_3m AS growth, f.above_200ma
            FROM us_factor_snapshot f
            LEFT JOIN us_stock_meta m ON m.ticker = f.ticker
            WHERE f.above_200ma = 1 AND f.return_3m IS NOT NULL
              AND COALESCE(f.market_cap,0) >= ?
              AND COALESCE(f.price,0) >= ?
              AND COALESCE(f.op_margin,-999) > 0
            ORDER BY f.return_3m DESC
            LIMIT ?
            """,
            (min_market_cap, min_price, limit),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/us/stocks/chart/{ticker}")
def get_us_stock_chart(ticker: str, days: int = 180):
    """미국 종목 차트. source: us_price_history 우선, 없으면 radar_price_cache."""
    import sqlite3 as _sl3
    t = (ticker or "").upper().strip()
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    _ensure_us_tables()
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume
        FROM us_price_history
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (t, max(30, min(days, 4000))),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT trade_date AS date, NULL AS open, NULL AS high, NULL AS low, close, NULL AS volume
            FROM radar_price_cache
            WHERE ticker = ?
            ORDER BY rn DESC
            LIMIT ?
            """,
            (t, max(30, min(days, 300))),
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        o = {"date": (r["date"] or "")}
        for k in ("open", "high", "low", "close", "volume"):
            v = r[k] if k in r.keys() else None
            o[k] = float(v) if v is not None else None
        out.append(o)
    # 오래된 -> 최신 순서로 정렬
    out.reverse()

    # ── 이동평균선 (MA5/20/60/200) 연산 ────────────────────────────
    closes = [r["close"] for r in out]
    for i in range(len(out)):
        out[i]["ma5"] = float(sum(closes[max(0, i-4):i+1]) / min(5, i+1)) if i >= 4 and closes[i] is not None else None
        out[i]["ma20"] = float(sum(closes[max(0, i-19):i+1]) / min(20, i+1)) if i >= 19 and closes[i] is not None else None
        out[i]["ma60"] = float(sum(closes[max(0, i-59):i+1]) / min(60, i+1)) if i >= 59 and closes[i] is not None else None
        out[i]["ma200"] = float(sum(closes[max(0, i-199):i+1]) / min(200, i+1)) if i >= 199 and closes[i] is not None else None

    return out


@app.get("/api/us/stocks/detail/{ticker}")
def get_us_stock_detail(ticker: str):
    """미국 종목 상세(테이블2 + 재무/현금흐름). DB 우선 + 부족시 Yahoo 수집."""
    import sqlite3 as _sl3
    import yfinance as _yf
    tk = (ticker or "").upper().strip()
    cache_key = f"us_detail_{tk}"
    c = _us_stock_detail_cache.get(cache_key, {})
    if c and (_tm.time() - c.get("ts", 0)) < 3600:
        return c["data"]

    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    # 목록 API와 상세 API가 서로 다른 가격 기준일을 표시하지 않도록
    # 최신 us_price_history를 최우선으로 사용하고, snapshot/radar는 보조 소스로만 쓴다.
    base = conn.execute(
        """
        WITH latest_price AS (
            SELECT p.ticker, p.date AS latest_date, p.close AS latest_close
            FROM us_price_history p
            WHERE p.ticker = ?
            ORDER BY p.date DESC
            LIMIT 1
        )
        SELECT
            m.ticker AS ticker,
            COALESCE(m.country, 'US') AS country,
            COALESCE(s.market_cap, m.market_cap, r.market_cap, 0) AS market_cap,
            COALESCE(s.pbr, r.pbr) AS pbr,
            COALESCE(s.per, r.per) AS per,
            COALESCE(lp.latest_date, s.as_of_date, r.latest_date) AS latest_date,
            COALESCE(lp.latest_close, s.price, r.latest_close, 0) AS latest_close
        FROM us_stock_meta m
        LEFT JOIN us_frontend_snapshot s ON s.ticker = m.ticker
        LEFT JOIN radar_market_cache r ON r.ticker = m.ticker
        LEFT JOIN latest_price lp ON lp.ticker = m.ticker
        WHERE m.ticker = ?
          AND UPPER(COALESCE(m.country, 'US')) = 'US'
        LIMIT 1
        """,
        (tk, tk),
    ).fetchone()
    conn.close()
    if not base:
        raise HTTPException(status_code=404, detail="미국 종목 데이터가 없습니다.")

    _ensure_us_tables()
    # DB 데이터 부족 시 1회 수집
    conn2 = _sl3.connect("stock.db")
    fin_cnt = conn2.execute("SELECT COUNT(*) FROM us_financial_data WHERE ticker=? AND period_type='annual'", (tk,)).fetchone()[0]
    cf_cnt = conn2.execute("SELECT COUNT(*) FROM us_cashflow_data WHERE ticker=? AND period_type='annual'", (tk,)).fetchone()[0]
    px_cnt = conn2.execute("SELECT COUNT(*) FROM us_price_history WHERE ticker=?", (tk,)).fetchone()[0]
    conn2.close()
    if fin_cnt < 4 or cf_cnt < 4 or px_cnt < 120:
        try:
            _refresh_us_stock_data(tk)
        except Exception as e:
            logger.warning(f"[US-REFRESH] {tk}: {e}")

    yf_t = _yf.Ticker(tk)

    # ── 추가 yfinance 데이터 (배당/어닝/애널리스트/투자의견) ──────────────
    def _safe_df_to_list(df, max_rows: int = 12) -> list[dict]:
        """DataFrame → list[dict], NaN 제거, epoch → ISO 날짜 변환."""
        import math as _math
        if df is None:
            return []
        try:
            rows = []
            for idx, row in df.tail(max_rows).iterrows():
                d = {}
                idx_str = str(idx)[:10] if hasattr(idx, '__str__') else str(idx)
                d['date'] = idx_str
                for col in df.columns:
                    v = row[col]
                    if v is None or (isinstance(v, float) and _math.isnan(v)):
                        d[col] = None
                    elif hasattr(v, 'item'):
                        d[col] = v.item()
                    else:
                        d[col] = v
                rows.append(d)
            return rows
        except Exception:
            return []

    # 7개 필드가 전부 독립적인 yfinance(Yahoo Finance) 속성 접근이라
    # 순차 실행 시 각각의 네트워크 왕복이 그대로 누적됨(실측 2.35초/티커,
    # 캐시 만료 시마다 반복). 서로 의존관계가 없어 병렬화 — 동시요청 수는
    # 늘지 않고(총 API 호출 횟수는 동일) 대기시간만 겹쳐서 단축.
    def _fetch_info():
        info = {}
        try:
            info = yf_t.fast_info or {}
        except Exception:
            info = {}
        try:
            info2 = yf_t.info or {}
            if isinstance(info2, dict):
                info.update(info2)
        except Exception:
            pass
        return info

    def _fetch_dividends():
        out: list[dict] = []
        try:
            _div = yf_t.dividends
            if _div is not None and len(_div) > 0:
                import math as _math
                for idx, val in _div.tail(16).items():
                    d_str = str(idx)[:10] if hasattr(idx, '__str__') else str(idx)
                    out.append({'date': d_str, 'amount': float(val) if not _math.isnan(float(val)) else None})
        except Exception:
            pass
        return out

    def _fetch_earnings_history():
        try:
            _eh = yf_t.earnings_history
            if _eh is not None and len(_eh) > 0:
                return _safe_df_to_list(_eh, max_rows=8)
        except Exception:
            pass
        return []

    def _fetch_earnings_estimate():
        try:
            _ee = yf_t.earnings_estimate
            if _ee is not None and len(_ee) > 0:
                return _safe_df_to_list(_ee, max_rows=10)
        except Exception:
            pass
        return []

    def _fetch_revenue_estimate():
        try:
            _re = yf_t.revenue_estimate
            if _re is not None and len(_re) > 0:
                return _safe_df_to_list(_re, max_rows=10)
        except Exception:
            pass
        return []

    def _fetch_recommendations_summary():
        try:
            _rs = yf_t.recommendations_summary
            if _rs is not None and len(_rs) > 0:
                return _safe_df_to_list(_rs, max_rows=4)
        except Exception:
            pass
        return []

    def _fetch_next_earnings():
        try:
            _dates = yf_t.earnings_dates
            if _dates is not None and not _dates.empty:
                from datetime import datetime as _dt
                now_dt = _dt.now()
                future_dates = []
                for idx in _dates.index:
                    try:
                        d_obj = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else _dt.fromisoformat(str(idx)[:10])
                        if d_obj.tzinfo is not None:
                            d_obj = d_obj.replace(tzinfo=None)
                        if d_obj >= now_dt:
                            future_dates.append(d_obj)
                    except Exception:
                        pass
                if future_dates:
                    future_dates.sort()
                    next_dt = future_dates[0]
                    return next_dt.strftime('%Y-%m-%d'), (next_dt.date() - now_dt.date()).days
        except Exception:
            pass
        return None, None

    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=4) as _pool:
        _f_info = _pool.submit(_fetch_info)
        _f_div = _pool.submit(_fetch_dividends)
        _f_eh = _pool.submit(_fetch_earnings_history)
        _f_ee = _pool.submit(_fetch_earnings_estimate)
        _f_re = _pool.submit(_fetch_revenue_estimate)
        _f_rs = _pool.submit(_fetch_recommendations_summary)
        _f_ned = _pool.submit(_fetch_next_earnings)
        info = _f_info.result()
        _dividends_list = _f_div.result()
        _earnings_history = _f_eh.result()
        _earnings_estimate = _f_ee.result()
        _revenue_estimate = _f_re.result()
        _recommendations_summary = _f_rs.result()
        next_earnings_date, earnings_d_day = _f_ned.result()

    db = _sl3.connect("stock.db")
    db.row_factory = _sl3.Row
    fin_annual = [dict(r) for r in db.execute(
        """
        SELECT period_end AS period, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
               operating_income, interest_expense, pretax_income, tax_expense, net_income,
               assets, liabilities, equity, capital, eps, bps, roe, roa, per, pbr, opm
        FROM us_financial_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 8
        """,
        (tk,),
    ).fetchall()]
    fin_quarter = [dict(r) for r in db.execute(
        """
        SELECT period_end AS period, revenue, cogs, gross_profit, operating_expense, sga, rnd, ebitda,
               operating_income, interest_expense, pretax_income, tax_expense, net_income,
               assets, liabilities, equity, capital, eps, bps, roe, roa, per, pbr, opm
        FROM us_financial_data WHERE ticker=? AND period_type='quarter'
        ORDER BY period_end DESC LIMIT 16
        """,
        (tk,),
    ).fetchall()]
    cf_annual = [dict(r) for r in db.execute(
        """
        SELECT period_end AS period, operating_cf, change_working_capital, stock_based_compensation,
               investing_cf, financing_cf, capex, dividends_paid, cash_begin, cash_end, free_cf, depreciation
        FROM us_cashflow_data WHERE ticker=? AND period_type='annual'
        ORDER BY period_end DESC LIMIT 8
        """,
        (tk,),
    ).fetchall()]
    cf_quarter = [dict(r) for r in db.execute(
        """
        SELECT period_end AS period, operating_cf, change_working_capital, stock_based_compensation,
               investing_cf, financing_cf, capex, dividends_paid, cash_begin, cash_end, free_cf, depreciation
        FROM us_cashflow_data WHERE ticker=? AND period_type='quarter'
        ORDER BY period_end DESC LIMIT 16
        """,
        (tk,),
    ).fetchall()]

    price_rows = [dict(r) for r in db.execute(
        """
        SELECT date, close
        FROM us_price_history
        WHERE ticker=? AND close IS NOT NULL AND close > 0
        ORDER BY date DESC
        LIMIT 370
        """,
        (tk,),
    ).fetchall()]
    db.close()

    def _row_score(row: dict, keys: list[str]) -> int:
        s = 0
        for k in keys:
            v = row.get(k)
            if v is None:
                continue
            try:
                if float(v) == 0:
                    continue
            except Exception:
                pass
            s += 1
        return s

    def _has_metric_value(v) -> bool:
        if v is None:
            return False
        try:
            x = float(v)
            return x == x
        except Exception:
            return True

    def _derive_us_financial_fields(rows: list[dict]) -> list[dict]:
        """Fill safe derived US statement fields from accounting identities."""
        for row in rows or []:
            rev = row.get("revenue")
            cogs = row.get("cogs")
            gp = row.get("gross_profit")
            opi = row.get("operating_income")

            if not _has_metric_value(cogs) and _has_metric_value(rev) and _has_metric_value(gp):
                row["cogs"] = float(rev) - float(gp)
                cogs = row["cogs"]
            if not _has_metric_value(gp) and _has_metric_value(rev) and _has_metric_value(cogs):
                row["gross_profit"] = float(rev) - float(cogs)
                gp = row["gross_profit"]
            if not _has_metric_value(row.get("operating_expense")) and _has_metric_value(gp) and _has_metric_value(opi):
                row["operating_expense"] = float(gp) - float(opi)
            if not _has_metric_value(row.get("opm")) and _has_metric_value(rev) and float(rev) != 0 and _has_metric_value(opi):
                row["opm"] = float(opi) / float(rev) * 100.0
        return rows

    def _merge_nearby_period_rows(rows: list[dict], metric_keys: list[str]) -> list[dict]:
        """근접 날짜(±7일) 중복 행 병합: 값이 많은 행을 기준으로 필드별 non-null 값을 보존."""
        from datetime import datetime as _dt
        if not rows:
            return rows
        kept: list[dict] = []
        for row in rows:
            p = str(row.get("period") or "")
            try:
                d = _dt.strptime(p[:10], "%Y-%m-%d").date()
            except Exception:
                kept.append(row)
                continue
            matched_idx = None
            for i, k in enumerate(kept):
                kp = str(k.get("period") or "")
                try:
                    kd = _dt.strptime(kp[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                if abs((d - kd).days) <= 7:
                    matched_idx = i
                    break
            if matched_idx is None:
                kept.append(row)
            else:
                old = kept[matched_idx]
                sc_new = _row_score(row, metric_keys)
                sc_old = _row_score(old, metric_keys)
                if (sc_new > sc_old) or (sc_new == sc_old and p > str(old.get("period") or "")):
                    base, extra = dict(row), old
                    for k in metric_keys:
                        if not _has_metric_value(base.get(k)) and _has_metric_value(extra.get(k)):
                            base[k] = extra.get(k)
                    kept[matched_idx] = base
                else:
                    base = dict(old)
                    for k in metric_keys:
                        if not _has_metric_value(base.get(k)) and _has_metric_value(row.get(k)):
                            base[k] = row.get(k)
                    kept[matched_idx] = base
        kept.sort(key=lambda x: str(x.get("period") or ""), reverse=True)
        return kept

    fin_keys = [
        "revenue","cogs","gross_profit","operating_expense","sga","rnd","ebitda",
        "operating_income","interest_expense","pretax_income","tax_expense","net_income",
        "assets","liabilities","equity","capital","eps","bps","roe","roa","per","pbr","opm"
    ]
    cf_keys = [
        "operating_cf","change_working_capital","stock_based_compensation","investing_cf",
        "financing_cf","capex","dividends_paid","cash_begin","cash_end","free_cf","depreciation"
    ]
    fin_annual = _derive_us_financial_fields(_merge_nearby_period_rows(fin_annual, fin_keys))
    fin_quarter = _derive_us_financial_fields(_merge_nearby_period_rows(fin_quarter, fin_keys))
    cf_annual = _merge_nearby_period_rows(cf_annual, cf_keys)
    cf_quarter = _merge_nearby_period_rows(cf_quarter, cf_keys)

    latest_fin = fin_annual[0] if fin_annual else {}
    latest_px = float(price_rows[0]["close"]) if price_rows else None
    latest_px_date = price_rows[0]["date"] if price_rows else None
    prev_px = float(price_rows[1]["close"]) if len(price_rows) > 1 else None
    px_change_pct = ((latest_px - prev_px) / prev_px * 100.0) if latest_px and prev_px else None
    px_values = [float(r["close"]) for r in price_rows if r.get("close") is not None and float(r["close"]) > 0]
    high_52w = max(px_values) if px_values else None
    low_52w = min(px_values) if px_values else None

    _mconn = _sl3.connect("stock.db")
    meta_row = _mconn.execute(
        "SELECT sector, industry, company_name, index_name FROM us_stock_meta WHERE ticker=?",
        (tk,),
    ).fetchone()

    # ── 매매 판단 시그널 연산 ───────────────────────────────────────
    # 1. 이동평균선 및 ATR 14일 연산
    px_closes = [float(r["close"]) for r in price_rows if r.get("close") is not None and float(r["close"]) > 0]
    # oldest to newest
    px_closes_asc = list(reversed(px_closes))

    ma5 = sum(px_closes_asc[-5:]) / 5.0 if len(px_closes_asc) >= 5 else None
    ma20 = sum(px_closes_asc[-20:]) / 20.0 if len(px_closes_asc) >= 20 else None
    ma60 = sum(px_closes_asc[-60:]) / 60.0 if len(px_closes_asc) >= 60 else None
    ma200 = sum(px_closes_asc[-200:]) / 200.0 if len(px_closes_asc) >= 200 else None

    is_ma_aligned = bool(ma5 and ma20 and ma60 and (ma5 > ma20 > ma60))
    ma_status_label = "완전 정배열 (강한 추세)" if (ma5 and ma20 and ma60 and ma200 and ma5 > ma20 > ma60 > ma200) \
        else ("정배열" if is_ma_aligned else ("역배열" if (ma5 and ma20 and ma60 and ma5 < ma20 < ma60) else "혼조세"))

    # ATR(14) 계산: High/Low가 없는 경우 종가 변동폭(Absolute Change) 기반 추정
    atr14 = None
    if len(px_closes_asc) >= 15:
        tr_list = []
        for i in range(len(px_closes_asc) - 14, len(px_closes_asc)):
            c_curr = px_closes_asc[i]
            c_prev = px_closes_asc[i - 1]
            tr = abs(c_curr - c_prev)
            tr_list.append(tr)
        atr14 = sum(tr_list) / len(tr_list) if tr_list else None

    curr_p = float(latest_px or base["latest_close"] or info.get("lastPrice") or 0)
    atr_stop_loss = (curr_p - (2.0 * atr14)) if (curr_p > 0 and atr14 and atr14 > 0) else None
    atr_risk_pct = ((curr_p - atr_stop_loss) / curr_p * 100.0) if (curr_p > 0 and atr_stop_loss) else None

    # 2. S&P500 대비 3개월 초과수익률 (RS Score)
    rs_score = None
    if len(px_closes_asc) >= 63:
        stock_3m_ret = (px_closes_asc[-1] - px_closes_asc[-63]) / px_closes_asc[-63] * 100.0
        # S&P500 3M return (us_price_history ^GSPC or default baseline ~3.5%)
        sp500_row = _mconn.execute(
            "SELECT close FROM us_price_history WHERE ticker IN ('^GSPC', 'SPY') ORDER BY date DESC LIMIT 63"
        ).fetchall()
        sp500_3m_ret = 3.5
        if len(sp500_row) >= 63:
            try:
                sp_latest = float(sp500_row[0][0])
                sp_old = float(sp500_row[-1][0])
                if sp_old > 0:
                    sp500_3m_ret = (sp_latest - sp_old) / sp_old * 100.0
            except Exception:
                pass
        rs_score = stock_3m_ret - sp500_3m_ret

    # ── us_factor_snapshot에서 추가 팩터 로드 ─────────────────────────
    _snap_conn = _sl3.connect("stock.db")
    _snap_conn.row_factory = _sl3.Row
    _snap = _snap_conn.execute("""
        SELECT total_score, system_action,
               return_1m, return_3m, return_6m, return_1y,
               high_52w, low_52w,
               rs_score AS snap_rs,
               atr14 AS snap_atr14,
               atr_stop_loss AS snap_atr_stop,
               atr_risk_pct AS snap_atr_risk,
               graham_intrinsic AS snap_graham,
               graham_discount AS snap_gdiscount,
               op_margin, roe AS snap_roe, roa AS snap_roa,
               fcf_yield, debt_to_equity,
               revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy,
               above_200ma
        FROM us_factor_snapshot WHERE ticker=?
    """, (tk,)).fetchone()
    _snap_conn.close()
    snap_data = dict(_snap) if _snap else {}

    # rs_score: 실시간 계산 우선, 없으면 snap 사용
    if rs_score is None and snap_data.get("snap_rs") is not None:
        rs_score = snap_data["snap_rs"]
    # atr: 실시간 우선, 없으면 snap
    if atr14 is None and snap_data.get("snap_atr14") is not None:
        atr14 = snap_data["snap_atr14"]
        atr_stop_loss = snap_data.get("snap_atr_stop")
        atr_risk_pct  = snap_data.get("snap_atr_risk")
    _mconn.close()

    # 3. 그레이엄 내재가치 = sqrt(22.5 * EPS * BPS)
    eps_val = float((latest_fin.get("eps") if latest_fin.get("eps") is not None else info.get("trailingEps")) or 0)
    bps_val = float((latest_fin.get("bps") if latest_fin.get("bps") is not None else info.get("bookValue")) or 0)
    graham_intrinsic = None
    graham_discount = None
    if eps_val > 0 and bps_val > 0:
        import math as _m
        graham_intrinsic = _m.sqrt(22.5 * eps_val * bps_val)
        if curr_p > 0 and graham_intrinsic > 0:
            graham_discount = ((graham_intrinsic - curr_p) / graham_intrinsic) * 100.0

    def _safe_float(v, mult: float = 1.0):
        """None/NaN-safe float 변환. mult로 단위 조정 가능."""
        if v is None:
            return None
        try:
            import math as _m
            f = float(v) * mult
            return None if _m.isnan(f) or _m.isinf(f) else f
        except Exception:
            return None

    # 4. 종합 매매 판정 (System Judgment)
    # Track 1 (Trend): MA 정배열 + RS > 0
    # Track 2 (Value): Graham Discount >= 25% OR (PER < 20 AND OPM >= 15%)
    trend_buy = bool(is_ma_aligned and (rs_score is None or rs_score > 0))
    opm_val = latest_fin.get("opm") if latest_fin.get("opm") is not None else (_safe_float(info.get("operatingMargins"), 100.0) or 0)
    per_val = float((latest_fin.get("per") if latest_fin.get("per") is not None else base["per"]) or info.get("trailingPE") or 0)

    value_buy = bool((graham_discount and graham_discount >= 25.0) or (0 < per_val < 20.0 and opm_val >= 15.0))

    if trend_buy and value_buy:
        sys_action = "BUY_STRONG"
        sys_label = "🟢 강력 매수 (추세+가치 충족)"
        sys_desc = "이평선 정배열 추세와 밸류에이션 안전마진을 모두 확보한 유망 매수 구간입니다."
    elif trend_buy:
        sys_action = "BUY_TREND"
        sys_label = "🟢 추세 매수 (주도주 추세)"
        sys_desc = "S&P500 대비 우수한 상대강도와 이평선 정배열 추세를 유지하는 매수 구간입니다."
    elif value_buy:
        sys_action = "BUY_VALUE"
        sys_label = "🟢 가치 매수 (저평가 구간)"
        sys_desc = "내재가치 대비 할인율 또는 수익성 대비 저평가 상태의 가치 매수 구간입니다."
    elif is_ma_aligned or (rs_score and rs_score > -5.0):
        sys_action = "WATCH"
        sys_label = "🟡 관망 (트리거 대기)"
        sys_desc = "추세 또는 가치 조건이 일부 형성 중입니다. 확실한 진입 시그널을 확인하세요."
    else:
        sys_action = "AVOID"
        sys_label = "🔴 매수 보류 (위험 관망)"
        sys_desc = "역배열 또는 상대적 약세 구간입니다. 신규 매수를 지양하고 기계적 손절가를 준수하세요."

    def _ts_to_date(v) -> str | None:
        """Unix timestamp → 'YYYY-MM-DD' 문자열."""
        if not v:
            return None
        try:
            from datetime import datetime as _dt
            return _dt.utcfromtimestamp(int(v)).strftime('%Y-%m-%d')
        except Exception:
            return None

    data = {
        # ── 기본 정보 ──────────────────────────────────────────────────
        "ticker": tk,
        "name": str((meta_row[2] if meta_row else None) or info.get("shortName") or info.get("longName") or tk),
        "exchange": str(info.get("exchange") or info.get("fullExchangeName") or ""),
        "index_name": (meta_row[3] if meta_row else None) or "",
        "sector": (meta_row[0] if meta_row else None) or str(info.get("sector") or ""),
        "industry": (meta_row[1] if meta_row else None) or str(info.get("industry") or ""),
        "market_cap": float(base["market_cap"] or info.get("marketCap") or 0),
        "price": float(latest_px or base["latest_close"] or info.get("lastPrice") or 0),
        "change_pct": float(px_change_pct if px_change_pct is not None else (info.get("regularMarketChangePercent") or 0)),
        "as_of": latest_px_date or base["latest_date"],
        # ── 퀀트 종합 점수 & 팩터 ─────────────────────────────────────
        "total_score": snap_data.get("total_score"),
        "return_1m": snap_data.get("return_1m"),
        "return_3m": snap_data.get("return_3m"),
        "return_6m": snap_data.get("return_6m"),
        "return_1y": snap_data.get("return_1y"),
        "high_52w": snap_data.get("high_52w"),
        "low_52w": snap_data.get("low_52w"),
        "fcf_yield": snap_data.get("fcf_yield"),
        "debt_to_equity": snap_data.get("debt_to_equity"),
        "revenue_growth_yoy": snap_data.get("revenue_growth_yoy"),
        "op_income_growth_yoy": snap_data.get("op_income_growth_yoy"),
        "net_income_growth_yoy": snap_data.get("net_income_growth_yoy"),
        # ── 매매 판단 시그널 ───────────────────────────────────────────
        "system_action": sys_action,
        "system_label": sys_label,
        "system_desc": sys_desc,
        "ma_status_label": ma_status_label,
        "is_ma_aligned": is_ma_aligned,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma200": ma200,
        "atr14": atr14,
        "atr_stop_loss": atr_stop_loss,
        "atr_risk_pct": atr_risk_pct,
        "rs_score": rs_score,
        "graham_intrinsic": graham_intrinsic or snap_data.get("snap_graham"),
        "graham_discount": graham_discount or snap_data.get("snap_gdiscount"),
        "next_earnings_date": next_earnings_date,
        "earnings_d_day": earnings_d_day,
        # ── 기업 개요 ──────────────────────────────────────────────────
        "business_summary": info.get("longBusinessSummary") or "",
        "website": info.get("website") or info.get("irWebsite") or "",
        "full_time_employees": info.get("fullTimeEmployees"),
        "country": info.get("country") or "US",
        # ── 주가 지표 ──────────────────────────────────────────────────
        "annual_revenue": float(latest_fin.get("revenue") or info.get("totalRevenue") or 0),
        "annual_operating_income": float(latest_fin.get("operating_income") or info.get("operatingIncome") or 0),
        "annual_net_income": float(latest_fin.get("net_income") or info.get("netIncomeToCommon") or 0),
        "opm": latest_fin.get("opm") if latest_fin.get("opm") is not None else (
            snap_data.get("op_margin") or _safe_float(info.get("operatingMargins"), 100.0)
        ),
        "fifty_two_week_high": float(high_52w or snap_data.get("high_52w") or info.get("fiftyTwoWeekHigh") or 0),
        "fifty_two_week_low": float(low_52w or snap_data.get("low_52w") or info.get("fiftyTwoWeekLow") or 0),
        # ── 밸류에이션 ─────────────────────────────────────────────────
        "pbr": float((latest_fin.get("pbr") if latest_fin.get("pbr") is not None else base["pbr"]) or info.get("priceToBook") or 0),
        "per": float((latest_fin.get("per") if latest_fin.get("per") is not None else base["per"]) or info.get("trailingPE") or 0),
        "eps": float((latest_fin.get("eps") if latest_fin.get("eps") is not None else info.get("trailingEps")) or 0),
        "bps": float((latest_fin.get("bps") if latest_fin.get("bps") is not None else info.get("bookValue")) or 0),
        "roe": (
            latest_fin.get("roe") if latest_fin.get("roe") is not None else
            (snap_data.get("snap_roe") or _safe_float(info.get("returnOnEquity"), 100.0))
        ),
        "roa": (
            latest_fin.get("roa") if latest_fin.get("roa") is not None else
            (snap_data.get("snap_roa") or _safe_float(info.get("returnOnAssets"), 100.0))
        ),
        "forward_pe": _safe_float(info.get("forwardPE")),
        "forward_eps": _safe_float(info.get("forwardEps")),
        "peg_ratio": _safe_float(info.get("pegRatio")),
        "price_to_sales": _safe_float(info.get("priceToSalesTrailing12Months")),
        "beta": _safe_float(info.get("beta")),
        # ── 성장률 (yfinance 우선, 없으면 YoY 계산값) ───────────────────
        "revenue_growth": (
            _safe_float(info.get("revenueGrowth"), 100.0) or
            snap_data.get("revenue_growth_yoy")
        ),
        "earnings_growth": _safe_float(info.get("earningsGrowth"), 100.0),
        "earnings_quarterly_growth": _safe_float(info.get("earningsQuarterlyGrowth"), 100.0),
        # ── 배당 ────────────────────────────────────────────────────────
        "dividend_rate": _safe_float(info.get("dividendRate")),
        "dividend_yield": _safe_float(info.get("dividendYield"), 100.0),  # % 단위
        "payout_ratio": _safe_float(info.get("payoutRatio"), 100.0),
        "ex_dividend_date": _ts_to_date(info.get("exDividendDate")),
        "dividend_date": _ts_to_date(info.get("dividendDate")),
        "five_year_avg_dividend_yield": _safe_float(info.get("fiveYearAvgDividendYield")),
        "dividends_history": _dividends_list,  # 최근 16회 배당 이력
        # ── 수급 / 주주 구조 ───────────────────────────────────────────
        "held_pct_institutions": _safe_float(info.get("heldPercentInstitutions"), 100.0),
        "held_pct_insiders": _safe_float(info.get("heldPercentInsiders"), 100.0),
        "short_ratio": _safe_float(info.get("shortRatio")),
        "short_pct_float": _safe_float(info.get("shortPercentOfFloat"), 100.0),
        "total_cash": _safe_float(info.get("totalCash")),
        "total_debt": _safe_float(info.get("totalDebt")),
        "shares_outstanding": info.get("sharesOutstanding"),
        "float_shares": info.get("floatShares"),
        # ── 애널리스트 컨센서스 ─────────────────────────────────────────
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "target_high_price": _safe_float(info.get("targetHighPrice")),
        "target_low_price": _safe_float(info.get("targetLowPrice")),
        "target_median_price": _safe_float(info.get("targetMedianPrice")),
        "recommendation_key": info.get("recommendationKey") or "",
        "recommendation_mean": _safe_float(info.get("recommendationMean")),
        "number_of_analyst_opinions": info.get("numberOfAnalystOpinions"),
        "analyst_rating": info.get("averageAnalystRating") or "",
        "recommendations_summary": _recommendations_summary,  # 기간별 Buy/Hold/Sell
        # ── 어닝 서프라이즈 & 추정치 ───────────────────────────────────
        "earnings_history": _earnings_history,     # 실적 vs 추정치 이력
        "earnings_estimate": _earnings_estimate,   # 분기/연간 EPS 추정
        "revenue_estimate": _revenue_estimate,     # 분기/연간 매출 추정
        # ── 재무 시계열 ─────────────────────────────────────────────────
        "financial_annual": fin_annual,
        "financial_quarter": fin_quarter,
        "cashflow_annual": cf_annual,
        "cashflow_quarter": cf_quarter,
    }
    _us_stock_detail_cache[cache_key] = {"ts": _tm.time(), "data": data}
    return data


@app.post("/api/us/stocks/refresh/{ticker}")
def refresh_us_stock(ticker: str):
    """미국 종목 4년+ 재무/현금흐름 + 5년 가격 DB 적재."""
    try:
        data = _refresh_us_stock_data(ticker)
        _us_stock_detail_cache.pop(f"us_detail_{(ticker or '').upper().strip()}", None)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"US data refresh failed: {e}")


def _stooq_last_close(ticker: str):
    try:
        t = (ticker or "").lower()
        url = f"https://stooq.com/q/d/l/?s={t}.us&i=d"
        res = _requests.get(url, timeout=10)
        if res.status_code != 200:
            return None
        lines = [ln.strip() for ln in res.text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        # Date,Open,High,Low,Close,Volume
        parts = lines[-1].split(",")
        if len(parts) < 5:
            return None
        return float(parts[4])
    except Exception:
        return None


@app.post("/api/us/integrity/run")
def run_us_integrity_check(limit: int = 20):
    """Yahoo 수집 데이터 vs 외부(Stooq) 무결성 점검."""
    import sqlite3 as _sl3
    _ensure_us_tables()
    conn = _sl3.connect("stock.db", timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = _sl3.Row
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT ticker FROM us_factor_snapshot ORDER BY market_cap DESC, total_score DESC LIMIT ?",
        (max(5, min(limit, 100)),)
    ).fetchall()]
    if not tickers:
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM radar_market_cache WHERE UPPER(COALESCE(country,''))='US' ORDER BY market_cap DESC LIMIT ?",
            (max(5, min(limit, 100)),)
        ).fetchall()]
    rows = []
    for tk in tickers:
        y = conn.execute("SELECT close FROM us_price_history WHERE ticker=? ORDER BY date DESC LIMIT 1", (tk,)).fetchone()
        yv = float(y["close"] or 0) if y else None
        ev = _stooq_last_close(tk)
        if yv is None or ev is None or ev == 0:
            continue
        abs_diff = abs(yv - ev)
        pct_diff = (abs_diff / abs(ev)) * 100.0
        passed = 1 if pct_diff <= 2.0 else 0
        conn.execute(
            """
            INSERT INTO us_data_integrity_audit
            (ticker, yahoo_price, external_price, source, abs_diff, pct_diff, passed)
            VALUES (?, ?, ?, 'stooq', ?, ?, ?)
            """,
            (tk, yv, ev, abs_diff, pct_diff, passed),
        )
        rows.append({"ticker": tk, "yahoo_price": yv, "external_price": ev, "pct_diff": pct_diff, "passed": bool(passed)})
    conn.commit()
    summary = {
        "checked": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "failed": sum(1 for r in rows if not r["passed"]),
    }
    conn.close()
    return {"summary": summary, "rows": rows[:50]}


@app.get("/api/us/integrity/latest")
def get_latest_us_integrity(limit: int = 100):
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    rows = [dict(r) for r in conn.execute(
        """
        SELECT * FROM us_data_integrity_audit
        ORDER BY id DESC
        LIMIT ?
        """, (max(10, min(limit, 500)),)
    ).fetchall()]
    conn.close()
    return rows


@app.get("/api/us/screener")
def get_us_screener(index_name: str = "all", sector: str = "", min_score: float = 0, limit: int = 200):
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    where = ["1=1"]
    params = []
    if sector:
        where.append("LOWER(COALESCE(sector,'')) = LOWER(?)")
        params.append(sector)
    if min_score > 0:
        where.append("COALESCE(total_score,0) >= ?")
        params.append(min_score)
    if index_name in ("S&P500", "NASDAQ"):
        where.append("COALESCE(index_name,'') = ?")
        params.append(index_name)
    rows = [dict(r) for r in conn.execute(
        f"""
        SELECT ticker, sector, industry, market_cap, price, return_1m, return_3m, return_6m, return_1y,
               revenue_growth_yoy, op_margin, roe, per, pbr, fcf_yield, debt_to_equity, above_200ma, total_score
        FROM us_factor_snapshot
        WHERE {' AND '.join(where)}
        ORDER BY total_score DESC, market_cap DESC
        LIMIT ?
        """,
        (*params, max(20, min(limit, 1000))),
    ).fetchall()]
    conn.close()
    return rows


@app.get("/api/us/sectors")
def get_us_sector_dashboard():
    import sqlite3 as _sl3
    import math as _math
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    rows = [dict(r) for r in conn.execute(
        """
        SELECT COALESCE(sector,'Unknown') AS sector,
               COUNT(*) AS count,
               AVG(return_1m) AS ret_1m,
               AVG(return_3m) AS ret_3m,
               AVG(return_6m) AS ret_6m,
               AVG(return_1y) AS ret_1y,
               AVG(per) AS avg_per,
               AVG(revenue_growth_yoy) AS avg_revenue_growth,
               AVG(op_margin) AS avg_op_margin,
               AVG(above_200ma) * 100.0 AS above_200ma_ratio,
               AVG(total_score) AS sector_score
        FROM us_factor_snapshot
        GROUP BY COALESCE(sector,'Unknown')
        ORDER BY sector_score DESC
        """
    ).fetchall()]
    conn.close()
    def _safe(v):
        if v is None:
            return None
        try:
            x = float(v)
            return x if _math.isfinite(x) else None
        except Exception:
            return None
    for row in rows:
        for key in ("ret_1m", "ret_3m", "ret_6m", "ret_1y", "avg_per",
                    "avg_revenue_growth", "avg_op_margin", "above_200ma_ratio",
                    "sector_score"):
            row[key] = _safe(row.get(key))
    return rows


@app.get("/api/us/long-term-picks")
def get_us_long_term_picks(limit: int = 100):
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    rows = [dict(r) for r in conn.execute(
        """
        SELECT ticker, sector, market_cap, total_score, revenue_growth_yoy, roe, op_margin, fcf_yield, debt_to_equity, return_1y, above_200ma,
               CASE
                 WHEN COALESCE(revenue_growth_yoy,0) >= 10 AND COALESCE(op_margin,0) >= 15 AND COALESCE(roe,0) >= 12 THEN 'Quality Growth'
                 WHEN COALESCE(roe,0) >= 15 AND COALESCE(fcf_yield,0) >= 1 THEN 'Compounder'
                 WHEN COALESCE(per,999) <= 25 AND COALESCE(fcf_yield,0) >= 2 THEN 'Value Candidate'
                 ELSE 'Long-term Watch'
               END AS style
        FROM us_factor_snapshot
        WHERE COALESCE(total_score,0) >= 60
          AND COALESCE(above_200ma,0) = 1
        ORDER BY total_score DESC, market_cap DESC
        LIMIT ?
        """, (max(20, min(limit, 500)),)
    ).fetchall()]
    conn.close()
    return rows


@app.get("/api/us/new-opportunities")
def get_us_new_opportunities(limit: int = 150):
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    rows = [dict(r) for r in conn.execute(
        """
        SELECT ticker, sector, industry, return_3m, return_6m, revenue_growth_yoy, op_margin, fcf_yield, total_score,
               CASE
                 WHEN COALESCE(revenue_growth_yoy,0) > 8 AND COALESCE(op_margin,0) > 10 THEN 'Earnings Improvement'
                 WHEN COALESCE(return_3m,0) > 15 AND COALESCE(return_6m,0) > 25 AND COALESCE(above_200ma,0)=1 THEN 'Momentum Breakout'
                 WHEN COALESCE(per,999) < 25 AND COALESCE(fcf_yield,0) > 2 THEN 'Value Recovery'
                 ELSE 'Watch'
               END AS opportunity_type
        FROM us_factor_snapshot
        WHERE COALESCE(total_score,0) >= 55
        ORDER BY total_score DESC, return_3m DESC
        LIMIT ?
        """, (max(20, min(limit, 500)),)
    ).fetchall()]
    conn.close()
    return rows


@app.get("/api/us/stocks/disclosures/{ticker}")
def get_us_stock_disclosures(ticker: str):
    """미국 종목 최근 공시(SEC submissions)."""
    import sqlite3 as _sl3
    tk = (ticker or "").upper().strip()
    _ensure_us_tables()
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    try:
        cached = [dict(r) for r in conn.execute(
            """
            SELECT filing_date AS date, form, title, url
            FROM us_disclosures
            WHERE ticker=?
            ORDER BY filing_date DESC
            LIMIT 30
            """,
            (tk,),
        ).fetchall()]
        if cached:
            return cached
    finally:
        conn.close()

    sec_map = _load_sec_symbol_cik_map()
    cik = sec_map.get(tk)
    if not cik:
        return []
    try:
        headers = {
            "User-Agent": "StockDashboard AdminContact admin@example.com",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.sec.gov/",
        }
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        res = _requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return []
        payload = res.json()
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", []) or []
        filed = recent.get("filingDate", []) or []
        acc_no = recent.get("accessionNumber", []) or []
        primary_doc = recent.get("primaryDocument", []) or []
        out = []
        n = min(len(forms), len(filed), len(acc_no), 30)
        conn2 = _sl3.connect("stock.db")
        for i in range(n):
            an = str(acc_no[i] or "").replace("-", "")
            doc = str(primary_doc[i] or "")
            sec_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{an}/{doc}" if an and doc else ""
            form = str(forms[i] or "")
            fdate = str(filed[i] or "")
            title = f"{form} Filing"
            conn2.execute(
                """
                INSERT OR REPLACE INTO us_disclosures
                (ticker, filing_date, form, title, url, accession_no, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (tk, fdate, form, title, sec_url, str(acc_no[i] or "")),
            )
            out.append({
                "date": fdate,
                "form": form,
                "title": title,
                "url": sec_url,
            })
        conn2.commit()
        conn2.close()
        return out
    except Exception:
        return []


@app.get("/api/us/biotech/analysis")
def get_us_biotech_analysis(min_market_cap: float = 300_000_000, limit: int = 300):
    """시총 기준을 통과한 미국 바이오/의약품 종목과 SEC 파이프라인 수집 상태."""
    from collectors.us_biotech_pipeline_collector import PIPELINE_PARSER_VERSION, ensure_us_biotech_tables, is_biotech_company

    _ensure_us_tables()
    ensure_us_biotech_tables()
    min_market_cap = max(0.0, min(float(min_market_cap or 0), 10_000_000_000_000.0))
    limit = max(20, min(int(limit or 300), 1000))
    base = _get_us_stock_base_items_cached()
    eligible = [
        item for item in base
        if float(item.get("market_cap") or 0) >= min_market_cap
        and is_biotech_company(item.get("sector"), item.get("industry"))
    ]
    import sqlite3 as _sl3
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    try:
        snapshot_rows = conn.execute(
            """
            SELECT ticker, filing_date, form, source_url, extraction_status, updated_at, parser_version,
                   pipeline_json, last_error
            FROM us_biotech_pipeline_snapshot
            """
        ).fetchall()
    finally:
        conn.close()
    snapshots = {str(row["ticker"]): dict(row) for row in snapshot_rows}
    trial_rows = conn = None
    conn = _sl3.connect("stock.db")
    conn.row_factory = _sl3.Row
    try:
        trial_rows = conn.execute(
            "SELECT ticker, conditions_json, title FROM us_biotech_clinical_trials"
        ).fetchall()
    finally:
        conn.close()
    trial_text = {}
    for row in trial_rows or []:
        try:
            conditions = " ".join(_json.loads(row["conditions_json"] or "[]"))
        except (TypeError, ValueError):
            conditions = ""
        trial_text[row["ticker"]] = f"{trial_text.get(row['ticker'], '')} {conditions} {row['title'] or ''}".lower()

    category_rules = (
        ("oncology", "암 치료", ("cancer", "oncology", "tumor", "carcinoma", "leukemia", "lymphoma", "myeloma")),
        ("neuro", "신경·치매", ("alzheimer", "dementia", "parkinson", "neurolog", "cns", "brain")),
        ("metabolic", "비만·대사", ("obesity", "weight loss", "diabetes", "metabolic", "incretin", "glp-1", "glycemic")),
        ("immunology", "면역·염증", ("immun", "inflamm", "autoimmune", "arthritis", "dermatitis")),
        ("rare", "희귀질환", ("rare disease", "orphan", "genetic disorder", "ultra-rare")),
        ("infectious", "감염병", ("infect", "antiviral", "vaccine", "bacteria", "covid")),
    )
    import math as _math
    def _api_safe(value):
        if isinstance(value, float) and not _math.isfinite(value):
            return None
        return value

    items = []
    structured = 0
    for item in eligible:
        snapshot = snapshots.get(item["ticker"], {})
        parser_current = snapshot.get("parser_version") == PIPELINE_PARSER_VERSION
        try:
            pipeline_count = len(_json.loads(snapshot.get("pipeline_json") or "[]")) if parser_current else 0
        except (TypeError, ValueError):
            pipeline_count = 0
        status = (snapshot.get("extraction_status") or "pending") if parser_current else "reparse_required"
        try:
            pipeline_text = " ".join(
                f"{asset.get('indication') or ''} {asset.get('source_excerpt') or ''}"
                for asset in _json.loads(snapshot.get("pipeline_json") or "[]")
            ).lower() if parser_current else ""
        except (TypeError, ValueError):
            pipeline_text = ""
        disease_text = f"{pipeline_text} {trial_text.get(item['ticker'], '')}"
        categories = [code for code, _, terms in category_rules if any(term in disease_text for term in terms)] or ["other"]
        structured += int(status == "structured")
        items.append({
            **{key: _api_safe(value) for key, value in item.items()},
            "pipeline_status": status,
            "pipeline_count": pipeline_count,
            "pipeline_filing_date": snapshot.get("filing_date"),
            "pipeline_form": snapshot.get("form"),
            "pipeline_source_url": snapshot.get("source_url"),
            "pipeline_updated_at": snapshot.get("updated_at"),
            "pipeline_error": snapshot.get("last_error") if status == "error" else None,
            "therapy_categories": categories,
        })
    return {
        "min_market_cap": min_market_cap,
        "eligible_count": len(eligible),
        "structured_count": structured,
        "items": items[:limit],
        "coverage_note": "SEC 10-K/10-Q 원문에서 추출한 정보만 표시합니다. pending/source_review_needed는 후보물질 부재가 아니라 아직 구조화되지 않았음을 뜻합니다.",
    }


@app.get("/api/us/biotech/pipeline/{ticker}")
def get_us_biotech_pipeline(ticker: str):
    from collectors.us_biotech_pipeline_collector import get_biotech_pipeline
    return get_biotech_pipeline(ticker)


@app.get("/api/us/biotech/profile/{ticker}")
def get_us_biotech_profile(ticker: str):
    """바이오 종목 비교용 표준 프로필: 가격·컨센서스·재무·파이프라인·임상 일정."""
    detail = get_us_stock_detail(ticker)
    from collectors.us_biotech_pipeline_collector import PIPELINE_PARSER_VERSION, get_biotech_pipeline
    pipeline = get_biotech_pipeline(ticker)
    parser_current = pipeline.get("parser_version") == PIPELINE_PARSER_VERSION
    # 회사명 검색만으로 수집한 오래된 시험이 파이프라인에 섞이지 않도록,
    # 후보물질명·적응증·중재약물의 구체적인 공통어가 있을 때만 연결한다.
    relation_stop_words = {
        "cancer", "clinical", "study", "trial", "treatment", "patients", "disease",
        "phase", "therapy", "drug", "with", "from", "for", "and", "the", "in",
        "advanced", "metastatic", "solid", "tumor", "tumours", "disorder",
    }
    def relation_terms(value):
        return {
            word for word in re.findall(r"[a-z0-9]{4,}", str(value or "").lower())
            if word not in relation_stop_words
        }
    raw_pipeline = (pipeline.get("pipeline") or []) if parser_current else []
    raw_trials = pipeline.get("clinical_trials") or []
    linked_trial_ids = set()
    linked_pipeline = []
    for asset in raw_pipeline:
        asset_terms = relation_terms(" ".join([
            str(asset.get("asset_name") or ""), str(asset.get("indication") or ""),
        ]))
        linked_trials = []
        for trial in raw_trials:
            trial_text = " ".join([
                str(trial.get("title") or ""),
                " ".join(trial.get("conditions") or []),
                " ".join(trial.get("interventions") or []),
            ])
            trial_terms = relation_terms(trial_text)
            shared_terms = asset_terms & trial_terms
            asset_name = str(asset.get("asset_name") or "").lower().strip()
            direct_asset_match = len(asset_name) >= 4 and asset_name in trial_text.lower()
            # 적응증의 일반어 하나(cancer 등)만 겹치는 경우는 연결하지 않는다.
            if direct_asset_match or len(shared_terms) >= 2:
                linked_trial = {**trial, "match_basis": "후보물질명 일치" if direct_asset_match else f"적응증·중재 공통어: {', '.join(sorted(shared_terms)[:4])}"}
                linked_trials.append(linked_trial)
                linked_trial_ids.add(trial.get("nct_id"))
        linked_pipeline.append({**asset, "linked_trials": linked_trials})
    unlinked_trials = [trial for trial in raw_trials if trial.get("nct_id") not in linked_trial_ids]
    saved_consensus = pipeline.get("consensus") or {}
    target = detail.get("target_mean_price") or saved_consensus.get("target_mean_price")
    price = detail.get("price")
    latest_quarter_cf = (detail.get("cashflow_quarter") or [None])[0] or {}
    cash_end = latest_quarter_cf.get("cash_end")
    operating_cf = latest_quarter_cf.get("operating_cf")
    free_cf = latest_quarter_cf.get("free_cf")
    runway_months = None
    try:
        quarterly_burn = min(float(operating_cf or 0), 0)
        if cash_end is not None and quarterly_burn < 0:
            runway_months = float(cash_end) / abs(quarterly_burn) * 3
    except (TypeError, ValueError):
        pass
    total_cash = detail.get("total_cash")
    total_debt = detail.get("total_debt")
    net_cash = None
    try:
        if total_cash is not None and total_debt is not None:
            net_cash = float(total_cash) - float(total_debt)
    except (TypeError, ValueError):
        pass
    today = date.today()
    linked_trials = [trial for asset in linked_pipeline for trial in asset.get("linked_trials") or []]
    pipeline_source_types = {str(asset.get("source_type") or "") for asset in linked_pipeline}
    sec_pipeline_verified = "SEC filing" in pipeline_source_types
    catalysts = [
        trial for trial in linked_trials
        if trial.get("primary_completion_date") and str(trial.get("primary_completion_date")) >= today.isoformat()
        and str(trial.get("primary_completion_date")) <= (today + timedelta(days=180)).isoformat()
    ]
    phase_counts = {}
    for asset in linked_pipeline:
        phase = asset.get("phase") or "미분류"
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
    readiness_score = 0
    for phase, count in phase_counts.items():
        label = str(phase).lower()
        readiness_score = max(readiness_score, 90 if "nda" in label or "bla" in label or "approved" in label else 75 if "phase 3" in label or "phase iii" in label else 50 if "phase 2" in label or "phase ii" in label else 30 if "phase 1" in label or "phase i" in label else 10)
    score = readiness_score * 0.40
    score += 25 if runway_months is not None and runway_months >= 24 else 15 if runway_months is not None and runway_months >= 12 else 5 if runway_months is not None else 8
    score += min(15, len(catalysts) * 3)
    score += 10 if any(trial.get("has_results") for trial in linked_trials) else 0
    score += 10 if net_cash is not None and net_cash > 0 else 0
    score -= 10 if (detail.get("short_pct_float") or 0) >= 20 else 0
    score = max(0, min(100, round(score)))
    grade = "최상" if score >= 75 else "상" if score >= 60 else "우수" if score >= 45 else "보통"
    coverage_missing = []
    if target is None:
        coverage_missing.append("컨센서스 목표가")
    if not pipeline.get("pipeline"):
        coverage_missing.append("SEC 파이프라인")
    if total_cash is None and cash_end is None:
        coverage_missing.append("현금")
    if coverage_missing:
        grade = "정보 보완 중"
    upside = None
    try:
        if target is not None and price not in (None, 0):
            upside = (float(target) / float(price) - 1.0) * 100.0
    except (TypeError, ValueError):
        pass
    def age_days(value):
        try:
            return max(0, (today - date.fromisoformat(str(value)[:10])).days)
        except (TypeError, ValueError):
            return None
    market_age = age_days(detail.get("as_of"))
    filing_age = age_days(pipeline.get("filing_date"))
    news_dates = [str(item.get("published_at") or "")[:10] for item in (pipeline.get("news") or [])]
    news_age = min((days for days in (age_days(value) for value in news_dates) if days is not None), default=None)
    decision_checks = [
        {"key": "market", "label": "주가·시가총액", "status": "pass" if price and market_age is not None and market_age <= 7 else "missing", "current": f"기준일 {detail.get('as_of') or '-'}", "meaning": "현재 가격 기준이 오래되면 목표가 괴리와 가치평가가 왜곡됩니다."},
        {"key": "consensus", "label": "애널리스트 컨센서스", "status": "pass" if target is not None and (detail.get("number_of_analyst_opinions") or saved_consensus.get("analyst_count")) else "missing", "current": f"목표가 {target if target is not None else '-'} · 참여 {(detail.get('number_of_analyst_opinions') or saved_consensus.get('analyst_count') or '-')}명", "meaning": "목표가는 참고 범위이며 임상 실패 위험을 대신하지 않습니다."},
        {"key": "liquidity", "label": "현금·현금소진", "status": "pass" if cash_end is not None and latest_quarter_cf.get("period") else "missing", "current": f"최근 분기 {latest_quarter_cf.get('period') or '-'} · 런웨이 {f'{round(runway_months, 1)}개월' if runway_months is not None else '흑자 또는 계산 불가'}", "meaning": "적자 바이오는 다음 자금조달 전까지 버틸 수 있는 기간이 핵심입니다."},
        {"key": "pipeline", "label": "후보물질 파이프라인", "status": "pass" if parser_current and linked_pipeline and sec_pipeline_verified else "warn" if parser_current and linked_pipeline else "missing", "current": f"SEC 원문 검증 {len(linked_pipeline)}건" if sec_pipeline_verified else f"ClinicalTrials.gov 보완 {len(linked_pipeline)}건 · 자산 소유권 미검증" if parser_current and linked_pipeline else "SEC·임상등록 자동 확정 실패 · 수동 검토 필요" if parser_current else "구버전 자료 재분석 필요", "meaning": "SEC 확정값을 우선하며, 임상등록 중재약물은 병용약·대조약일 수 있어 회사 소유 자산으로 단정하지 않습니다."},
        {"key": "clinical", "label": "임상시험 연결", "status": "pass" if linked_trials else "warn", "current": f"직접 연결 {len(linked_trials)}건 · 제외 {len(unlinked_trials)}건", "meaning": "회사명이 아니라 후보물질·적응증이 일치하는 임상만 촉매로 사용합니다."},
        {"key": "catalyst", "label": "6개월 촉매 일정", "status": "pass" if catalysts else "warn", "current": f"확인 {len(catalysts)}건", "meaning": "주요 완료일은 결과 발표일이나 FDA 결정일과 다를 수 있어 별도 확인이 필요합니다."},
        {"key": "dilution", "label": "증자·희석 위험", "status": "missing", "current": "발행주식수 시계열·ATM/증자 일정 미연결", "meaning": "현금 부족 기업은 증자로 주당 가치가 희석될 수 있습니다."},
        {"key": "efficacy", "label": "효능·안전성 경쟁 비교", "status": "missing", "current": "임상 결과 수치의 경쟁약 대비 표준화 미완료", "meaning": "반응률·생존기간·부작용을 같은 적응증과 시험 조건에서 비교해야 합니다."},
        {"key": "news", "label": "뉴스·공시 최신성", "status": "pass" if news_age is not None and news_age <= 30 and (filing_age is None or filing_age <= 550) else "warn", "current": f"최근 뉴스 {news_age if news_age is not None else '-'}일 전 · SEC {filing_age if filing_age is not None else '-'}일 전", "meaning": "최근 임상 중단·안전성·규제 변경이 빠지지 않았는지 확인합니다."},
    ]
    readiness_weights = {"market": 10, "consensus": 10, "liquidity": 15, "pipeline": 20, "clinical": 15, "catalyst": 10, "dilution": 10, "efficacy": 5, "news": 5}
    readiness_score = round(sum(readiness_weights[row["key"]] * (1 if row["status"] == "pass" else 0.4 if row["status"] == "warn" else 0) for row in decision_checks))
    critical_missing = [row["label"] for row in decision_checks if row["key"] in {"market", "liquidity", "pipeline"} and row["status"] == "missing"]
    readiness_label = "판단 보류" if critical_missing else "근거 검토 가능" if readiness_score >= 80 else "추가 검증 필요" if readiness_score >= 60 else "자료 보완 필요"
    return {
        "ticker": detail.get("ticker") or (ticker or "").upper(),
        "company": {key: detail.get(key) for key in ("name", "sector", "industry", "market_cap", "price", "change_pct", "as_of")},
        "consensus": {
            "target_mean_price": target,
            "target_high_price": detail.get("target_high_price") or saved_consensus.get("target_high_price"),
            "target_low_price": detail.get("target_low_price") or saved_consensus.get("target_low_price"),
            "upside_pct": upside,
            "recommendation_key": detail.get("recommendation_key") or saved_consensus.get("recommendation_key"),
            "recommendation_mean": detail.get("recommendation_mean") or saved_consensus.get("recommendation_mean"),
            "analyst_count": detail.get("number_of_analyst_opinions") or saved_consensus.get("analyst_count"),
            "recommendations_summary": detail.get("recommendations_summary") or [],
        },
        "financials": {key: detail.get(key) for key in ("annual_revenue", "annual_operating_income", "annual_net_income", "opm", "cashflow_annual", "financial_annual")},
        "liquidity": {
            "period": latest_quarter_cf.get("period"), "cash_end": cash_end,
            "operating_cf": operating_cf, "free_cf": free_cf,
            "estimated_runway_months": runway_months,
            "total_cash": total_cash, "total_debt": total_debt, "net_cash": net_cash,
            "note": "runway는 최근 분기 영업현금흐름이 음수일 때만 단순 연환산한 참고치입니다.",
        },
        "pipeline": linked_pipeline,
        "clinical_trials": linked_trials,
        "unlinked_clinical_trials": unlinked_trials,
        "news": pipeline.get("news") or [],
        "fda_labels": pipeline.get("fda_labels") or [],
        "biotech_checklist": {
            "grade": grade, "score": score,
            "coverage_missing": coverage_missing,
            "regulatory_readiness_score": readiness_score,
            "regulatory_note": "임상 단계·규제 신청 상태 기반 준비도이며 FDA 승인 확률 또는 투자 권고가 아닙니다.",
            "pipeline_by_phase": phase_counts,
            "near_term_clinical_catalysts": catalysts[:20],
            "institutional_ownership_pct": detail.get("held_pct_institutions"),
            "insider_ownership_pct": detail.get("held_pct_insiders"),
            "short_pct_float": detail.get("short_pct_float"),
            "short_ratio_days": detail.get("short_ratio"),
            "shares_outstanding": detail.get("shares_outstanding"),
            "data_updated_at": pipeline.get("updated_at"),
        },
        "investment_readiness": {
            "score": readiness_score, "label": readiness_label,
            "use_level": "후보 탐색용" if readiness_score < 80 or critical_missing else "외부 원문 교차검증 후 활용",
            "critical_missing": critical_missing, "checks": decision_checks,
            "note": "이 점수는 데이터 완성도 점수이며 매수·매도 신호나 수익률 예측이 아닙니다.",
        },
        "source": {
            "sec_form": pipeline.get("form"), "sec_filing_date": pipeline.get("filing_date"),
            "sec_url": pipeline.get("source_url"), "pipeline_status": (pipeline.get("extraction_status") or "pending") if parser_current else "reparse_required",
            "parser_version": pipeline.get("parser_version"), "required_parser_version": PIPELINE_PARSER_VERSION,
            "pipeline_updated_at": pipeline.get("updated_at"),
            "clinical_source": "ClinicalTrials.gov API v2",
        },
    }


@app.post("/api/us/biotech/pipeline/{ticker}/refresh")
def refresh_us_biotech_pipeline(ticker: str):
    """선택 종목의 최신 SEC 10-K/10-Q를 읽어 파이프라인을 갱신한다."""
    from collectors.us_biotech_pipeline_collector import refresh_biotech_pipeline
    return refresh_biotech_pipeline(ticker)


@app.post("/api/us/biotech/refresh-pending")
def refresh_pending_us_biotech(background_tasks: BackgroundTasks, min_market_cap: float = 300_000_000, limit: int = 100):
    """초기 적재·복구용: 아직 수집되지 않은 바이오 종목을 즉시 순차 처리한다."""
    cap = max(0.0, float(min_market_cap))
    batch = max(1, min(int(limit), 100))
    if _us_biotech_refresh_state.get("status") == "running":
        return {"queued": False, "reason": "already_running", **_us_biotech_refresh_state}
    _us_biotech_refresh_state.update({
        "status": "queued", "started_at": None, "finished_at": None,
        "min_market_cap": cap, "limit": batch, "result": None, "error": None,
    })
    background_tasks.add_task(_run_us_biotech_refresh, cap, batch)
    return {"queued": True, "min_market_cap": cap, "limit": batch}


def _run_us_biotech_refresh(min_market_cap: float, limit: int) -> None:
    from collectors.us_biotech_pipeline_collector import collect_biotech_pipelines
    _us_biotech_refresh_state.update({"status": "running", "started_at": datetime.now().isoformat(), "error": None})
    logger.info("[미국바이오] 재수집 시작: 시총=%s, limit=%s", min_market_cap, limit)
    try:
        result = collect_biotech_pipelines(min_market_cap=min_market_cap, limit=limit)
        _us_biotech_refresh_state.update({
            "status": "completed", "finished_at": datetime.now().isoformat(), "result": result,
        })
        logger.info("[미국바이오] 재수집 완료: %s", result)
    except Exception as exc:
        _us_biotech_refresh_state.update({
            "status": "error", "finished_at": datetime.now().isoformat(), "error": str(exc)[:1000],
        })
        logger.exception("[미국바이오] 재수집 실패")


@app.get("/api/us/biotech/refresh-status")
def get_us_biotech_refresh_status():
    return dict(_us_biotech_refresh_state)

@app.get("/api/dashboard/sectors")
def get_sectors(db: Session = Depends(get_db)):
    """
    섹터별 등락 현황을 반환합니다.
    """
    return processor.get_sector_performance(db)

@app.get("/api/dashboard/screening/triple")
def get_triple_screening(db: Session = Depends(get_db)):
    """소외 턴어라운드 + 성장 기울기 스크리너 (캐시 우선)."""
    # 두 화면이 같은 고비용 계산을 각각 실행하지 않도록 단일 캐시 경로를 사용한다.
    from routes.signals import get_fin_screener
    return get_fin_screener()


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

def _kis_est_num(v):
    try:
        if v in (None, "", "-"):
            return None
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _kis_est_pick(rows, idx, key):
    if not rows or idx >= len(rows):
        return None
    row = rows[idx] or {}
    return _kis_est_num(row.get(key))


def _ensure_forward_estimates_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            period TEXT NOT NULL,
            is_estimate INTEGER NOT NULL DEFAULT 0,
            revenue_억원 REAL,
            revenue_growth_pct REAL,
            operating_profit_억원 REAL,
            operating_profit_growth_pct REAL,
            net_income_억원 REAL,
            net_income_growth_pct REAL,
            ebitda_십억원 REAL,
            eps_원 REAL,
            eps_growth_pct REAL,
            per REAL,
            ev_ebitda REAL,
            roe_pct REAL,
            debt_ratio_pct REAL,
            interest_coverage REAL,
            analyst TEXT,
            estimate_date TEXT,
            opinion TEXT,
            source TEXT NOT NULL DEFAULT 'KIS 국내주식 종목추정실적',
            raw_message TEXT,
            collected_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(stock_code, period, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forward_estimates_code_est ON forward_estimates(stock_code, is_estimate, period)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forward_estimates_collected ON forward_estimates(collected_at)")


def _upsert_forward_estimates(payload: dict):
    rows = []
    income_by_period = {
        r.get("period"): r for r in (payload.get("income_statement") or [])
        if r and r.get("period")
    }
    for ind in payload.get("investment_indicators") or []:
        period = ind.get("period")
        if not period:
            continue
        inc = income_by_period.get(period, {})
        rows.append((
            payload.get("stock_code"),
            payload.get("stock_name"),
            period,
            1 if ind.get("is_estimate") else 0,
            inc.get("revenue_억원"),
            inc.get("revenue_growth_pct"),
            inc.get("operating_profit_억원"),
            inc.get("operating_profit_growth_pct"),
            inc.get("net_income_억원"),
            inc.get("net_income_growth_pct"),
            ind.get("ebitda_십억원"),
            ind.get("eps_원"),
            ind.get("eps_growth_pct"),
            ind.get("per"),
            ind.get("ev_ebitda"),
            ind.get("roe_pct"),
            ind.get("debt_ratio_pct"),
            ind.get("interest_coverage"),
            payload.get("analyst"),
            payload.get("estimate_date"),
            payload.get("opinion"),
            payload.get("source") or "KIS 국내주식 종목추정실적",
            payload.get("raw_message"),
        ))
    if not rows:
        return 0

    from db_utils import stock_db_write_lock
    with stock_db_write_lock("forward-estimates", timeout=30) as acquired:
        if not acquired:
            raise RuntimeError("stock.db writer lock timeout")
        conn = connect_stock_db(timeout=60, wal=True)
        try:
            _ensure_forward_estimates_table(conn)
            conn.executemany("""
            INSERT INTO forward_estimates (
                stock_code, stock_name, period, is_estimate,
                revenue_억원, revenue_growth_pct,
                operating_profit_억원, operating_profit_growth_pct,
                net_income_억원, net_income_growth_pct,
                ebitda_십억원, eps_원, eps_growth_pct, per, ev_ebitda,
                roe_pct, debt_ratio_pct, interest_coverage,
                analyst, estimate_date, opinion, source, raw_message
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_code, period, source) DO UPDATE SET
                stock_name=excluded.stock_name,
                is_estimate=excluded.is_estimate,
                revenue_억원=excluded.revenue_억원,
                revenue_growth_pct=excluded.revenue_growth_pct,
                operating_profit_억원=excluded.operating_profit_억원,
                operating_profit_growth_pct=excluded.operating_profit_growth_pct,
                net_income_억원=excluded.net_income_억원,
                net_income_growth_pct=excluded.net_income_growth_pct,
                ebitda_십억원=excluded.ebitda_십억원,
                eps_원=excluded.eps_원,
                eps_growth_pct=excluded.eps_growth_pct,
                per=excluded.per,
                ev_ebitda=excluded.ev_ebitda,
                roe_pct=excluded.roe_pct,
                debt_ratio_pct=excluded.debt_ratio_pct,
                interest_coverage=excluded.interest_coverage,
                analyst=excluded.analyst,
                estimate_date=excluded.estimate_date,
                opinion=excluded.opinion,
                raw_message=excluded.raw_message,
                collected_at=datetime('now','localtime')
            """, rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()


def _load_latest_forward_estimate(stock_code: str) -> dict:
    empty = {"forward_per": None, "forward_eps": None, "forward_period": None, "forward_source": None}
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        return empty
    try:
        conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
        _ensure_forward_estimates_table(conn)
        row = conn.execute("""
            SELECT period, eps_원, per, source, collected_at
            FROM forward_estimates
            WHERE stock_code=?
              AND is_estimate=1
              AND (eps_원 IS NOT NULL OR per IS NOT NULL)
            ORDER BY
              CASE WHEN per IS NOT NULL THEN 0 ELSE 1 END,
              period ASC,
              collected_at DESC
            LIMIT 1
        """, (stock_code,)).fetchone()
        conn.close()
        if not row:
            return empty
        return {
            "forward_per": row["per"],
            "forward_eps": row["eps_원"],
            "forward_period": row["period"],
            "forward_source": row["source"],
        }
    except Exception as exc:
        logger.warning("[ForwardEstimate] %s 조회 실패: %s", stock_code, exc)
        return empty


@app.get("/api/dashboard/estimated-performance/{stock_code}")
def get_estimated_performance(stock_code: str):
    """
    KIS 국내주식 종목추정실적.
    실제 공시 재무제표가 아니라 리서치본부 추정치이므로 프론트에서도
    반드시 '추정실적'으로 분리 표시한다.
    """
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        raise HTTPException(status_code=400, detail="국내 6자리 종목코드만 조회할 수 있습니다.")

    try:
        import config as _cfg
        from kis_client import kis_client as _kis_client

        token = _kis_client.get_token()
        if not token:
            return {
                "available": False,
                "stock_code": stock_code,
                "source": "KIS 종목추정실적",
                "message": "KIS 토큰을 발급하지 못했습니다.",
                "income_statement": [],
                "investment_indicators": [],
            }

        url = f"{_cfg.KIS_URL}/uapi/domestic-stock/v1/quotations/estimate-perform"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": _cfg.KIS_APP_KEY,
            "appsecret": _cfg.KIS_APP_SECRET,
            "tr_id": "HHKST668300C0",
            "custtype": "P",
        }
        res = _requests.get(url, headers=headers, params={"SHT_CD": stock_code}, timeout=12)
        data = res.json() if res.content else {}
        if res.status_code >= 400 or data.get("rt_cd") != "0":
            return {
                "available": False,
                "stock_code": stock_code,
                "source": "KIS 종목추정실적",
                "message": data.get("msg1") or f"KIS HTTP {res.status_code}",
                "income_statement": [],
                "investment_indicators": [],
            }

        meta = data.get("output1") or {}
        periods = [str(r.get("dt") or "").strip() for r in (data.get("output4") or []) if r]
        out2 = data.get("output2") or []
        out3 = data.get("output3") or []

        income_rows = []
        indicator_rows = []
        for i, period in enumerate(periods[:5], start=1):
            key = f"data{i}"
            income_rows.append({
                "period": period,
                "is_estimate": "E" in period.upper(),
                "revenue_억원": _kis_est_pick(out2, 0, key),
                "revenue_growth_pct": (_kis_est_pick(out2, 1, key) / 10.0) if _kis_est_pick(out2, 1, key) is not None else None,
                "operating_profit_억원": _kis_est_pick(out2, 2, key),
                "operating_profit_growth_pct": (_kis_est_pick(out2, 3, key) / 10.0) if _kis_est_pick(out2, 3, key) is not None else None,
                "net_income_억원": _kis_est_pick(out2, 4, key),
                "net_income_growth_pct": (_kis_est_pick(out2, 5, key) / 10.0) if _kis_est_pick(out2, 5, key) is not None else None,
            })
            indicator_rows.append({
                "period": period,
                "is_estimate": "E" in period.upper(),
                "ebitda_십억원": (_kis_est_pick(out3, 0, key) / 10.0) if _kis_est_pick(out3, 0, key) is not None else None,
                "eps_원": (_kis_est_pick(out3, 1, key) / 10.0) if _kis_est_pick(out3, 1, key) is not None else None,
                "eps_growth_pct": (_kis_est_pick(out3, 2, key) / 10.0) if _kis_est_pick(out3, 2, key) is not None else None,
                "per": (_kis_est_pick(out3, 3, key) / 10.0) if _kis_est_pick(out3, 3, key) is not None else None,
                "ev_ebitda": (_kis_est_pick(out3, 4, key) / 10.0) if _kis_est_pick(out3, 4, key) is not None else None,
                "roe_pct": (_kis_est_pick(out3, 5, key) / 10.0) if _kis_est_pick(out3, 5, key) is not None else None,
                "debt_ratio_pct": (_kis_est_pick(out3, 6, key) / 10.0) if _kis_est_pick(out3, 6, key) is not None else None,
                "interest_coverage": (_kis_est_pick(out3, 7, key) / 10.0) if _kis_est_pick(out3, 7, key) is not None else None,
            })

        has_rows = any(any(v is not None for k, v in r.items() if k not in ("period", "is_estimate")) for r in income_rows + indicator_rows)
        payload = {
            "available": bool(has_rows),
            "stock_code": stock_code,
            "stock_name": meta.get("item_kor_nm"),
            "analyst": meta.get("name1"),
            "estimate_date": meta.get("estdate"),
            "opinion": meta.get("rcmd_name"),
            "source": "KIS 국내주식 종목추정실적",
            "notice": "추정실적입니다. 공시 재무제표가 아니며 KIS 리서치본부 월초 추정치 기준입니다.",
            "coverage_note": "KIS 문서 기준 거래소/코스닥 약 160개 기업만 제공됩니다.",
            "income_statement": income_rows,
            "investment_indicators": indicator_rows,
            "raw_message": data.get("msg1"),
        }
        if has_rows:
            try:
                payload["saved_rows"] = _upsert_forward_estimates(payload)
            except Exception as save_exc:
                logger.warning("[KIS EstimatedPerformance] %s 저장 실패: %s", stock_code, save_exc)
                payload["save_error"] = str(save_exc)
        return payload
    except Exception as e:
        logger.warning("[KIS EstimatedPerformance] %s: %s", stock_code, e)
        return {
            "available": False,
            "stock_code": stock_code,
            "source": "KIS 종목추정실적",
            "message": str(e),
            "income_statement": [],
            "investment_indicators": [],
        }


_cf_collecting: set = set()   # 현금흐름 백그라운드 수집 중인 종목코드

def _calc_ttm_fundamentals(stock_code: str, report_type: str = "CFS") -> dict:
    """최근 4개 분기 공시 재무로 TTM 매출/이익/EPS/PER 계산."""
    empty = {
        "available": False,
        "periods": [],
        "period_start": None,
        "period_end": None,
        "revenue": None,
        "operating_profit": None,
        "net_income": None,
        "opm": None,
        "eps": None,
        "per": None,
        "source": "financial_data quarterly",
        "message": "최근 4개 분기 재무 데이터가 부족합니다.",
    }
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        return empty

    try:
        conn = connect_stock_db(timeout=30, row_factory=sqlite3.Row)
        if report_type == "OFS":
            rt_sql = "report_type = 'OFS'"
        else:
            rt_sql = "(report_type = 'CFS' OR report_type IS NULL)"

        rows = conn.execute(f"""
            WITH ranked AS (
                SELECT year, quarter, revenue, operating_profit, net_income, eps,
                       report_type, data_source, id,
                       ROW_NUMBER() OVER (
                           PARTITION BY year, quarter
                           ORDER BY
                             CASE WHEN report_type = ? THEN 0 WHEN report_type IS NULL THEN 1 ELSE 2 END,
                             CASE WHEN data_source = 'fnguide' THEN 0
                                  WHEN data_source LIKE '%q4%' THEN 1
                                  WHEN data_source = 'dart' THEN 2
                                  ELSE 3 END,
                             id DESC
                       ) rn
                FROM financial_data
                WHERE stock_code = ?
                  AND is_annual = 0
                  AND quarter BETWEEN 1 AND 4
                  AND {rt_sql}
            )
            SELECT * FROM ranked
            WHERE rn = 1
            ORDER BY year DESC, quarter DESC
            LIMIT 8
        """, (report_type, stock_code)).fetchall()

        if len(rows) < 4:
            conn.close()
            return empty

        latest4 = rows[:4]
        periods = [f"{int(r['year'])}Q{int(r['quarter'])}" for r in reversed(latest4)]

        def metric_sum(key: str):
            vals = [r[key] for r in latest4]
            if any(v is None for v in vals):
                return None
            return float(sum(vals))

        revenue = metric_sum("revenue")
        op = metric_sum("operating_profit")
        ni = metric_sum("net_income")

        su = conn.execute(
            "SELECT shares_issued FROM stock_universe WHERE stock_code=?",
            (stock_code,),
        ).fetchone()
        shares = float(su["shares_issued"] or 0) if su else 0

        # ⚠️ 2026-08-23: 일부 분기(주로 fnguide 소스)가 eps를 0.0으로
        # 저장한 채 net_income은 실제값을 갖는 "플레이스홀더" 케이스가
        # 있음(예: 172670 2026Q1 CFS eps=0.0 vs net_income=-24억). 그대로
        # 합산하면 TTM EPS/PER이 크게 왜곡되므로, eps==0.0인데 net_income이
        # 0이 아닌 분기는 net_income/shares_issued로 보정한다.
        eps_vals = []
        eps_valid = True
        for r in latest4:
            q_eps, q_ni = r["eps"], r["net_income"]
            if (q_eps is None or q_eps == 0.0) and q_ni is not None and q_ni != 0 and shares > 0:
                q_eps = q_ni / shares
            if q_eps is None:
                eps_valid = False
                break
            eps_vals.append(q_eps)
        eps = float(sum(eps_vals)) if eps_valid and eps_vals else None

        if eps is None and ni is not None and shares > 0:
            eps = ni / shares

        price_row = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
            (stock_code,),
        ).fetchone()
        conn.close()
        price = float(price_row["close"]) if price_row and price_row["close"] else None
        per = round(price / eps, 2) if price and eps and eps > 0 else None
        opm = round(op / revenue * 100, 1) if revenue and op is not None else None

        return {
            "available": any(v is not None for v in (revenue, op, ni)),
            "periods": periods,
            "period_start": periods[0] if periods else None,
            "period_end": periods[-1] if periods else None,
            "revenue": revenue,
            "operating_profit": op,
            "net_income": ni,
            "opm": opm,
            "eps": round(eps, 2) if eps is not None else None,
            "per": per,
            "source": "financial_data quarterly",
            "message": None,
        }
    except Exception as exc:
        logger.warning("[TTM] %s 계산 실패: %s", stock_code, exc)
        return empty

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
                            -- 연간은 DART 사업보고서 표준 quarter=4 우선, FnGuide quarter=0 차순위
                            CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END,
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
                CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END,
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

        # 연간 dep 추가 fallback: 분기 dep_q 합산으로 역산 (연간 NULL인 연도 보완)
        for yr_dep, dep_sum, cnt in _conn.execute(f"""
            SELECT year, SUM(depreciation_q) s, COUNT(*) cnt
            FROM cash_flow_data
            WHERE stock_code=? AND is_annual=0 {_rt_sql}
              AND depreciation_q IS NOT NULL AND depreciation_q > 0
              AND quarter IN (1,2,3,4)
            GROUP BY year HAVING COUNT(*) >= 3 AND SUM(depreciation_q) > 0
        """, (stock_code,)).fetchall():
            if yr_dep not in _annual_depr:
                _annual_depr[yr_dep] = dep_sum
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
                CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END,
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
        _q3_cash_end = {}
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

    # 분기 감가상각비: depreciation_q 신뢰도 검증 후 우선 사용.
    # dep_q 합이 연간 dep의 5% 미만 → dep_q 오류(단위 혼재 등) → 역산 fallback
    _dep_q_trusted_years = set()
    if not is_annual:
        # 연도별 dep_q 합 vs 연간 dep 비교
        from collections import defaultdict
        _dep_q_sums = defaultdict(float)
        for rr in raw:
            if rr['depreciation_q'] is not None:
                _dep_q_sums[rr['year']] += rr['depreciation_q']
        for yr, dq_sum in _dep_q_sums.items():
            ann_dep = _annual_depr.get(yr)
            if ann_dep and ann_dep > 0:
                ratio = dq_sum / ann_dep
                if ratio >= 0.05:  # dep_q 합이 연간의 5%+ → 신뢰
                    _dep_q_trusted_years.add(yr)
            else:
                _dep_q_trusted_years.add(yr)  # 연간 없으면 dep_q 그대로 사용

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
            # 흐름 항목: _q 컬럼 우선 → NULL이면 누적차감으로 분기값 계산
            yr_q, qtr_q = r['year'], r['quarter']

            def _q_or_diff(field):
                """_q 컬럼 우선 → NULL이면 누적차감, 음수면 None"""
                qkey = f"{field}_q"
                q_val = r[qkey] if qkey in r.keys() else None
                if q_val is not None:
                    # 이번 누적 저장
                    cum = r[field] if field in r.keys() else None
                    if cum is not None and qtr_q:
                        _prev_cumul[(yr_q, field)] = cum
                    return q_val if q_val >= 0 else None
                # _q NULL → 누적에서 역산
                cum = r[field] if field in r.keys() else None
                if cum is None or not qtr_q:
                    return None
                if qtr_q == 1:
                    _prev_cumul[(yr_q, field)] = cum
                    return cum
                prev = _prev_cumul.get((yr_q, field))
                diff = (cum - prev) if prev is not None else cum
                _prev_cumul[(yr_q, field)] = cum
                # 음수 역산은 원본 누적이 왜곡된 경우 → None
                return diff if (diff is None or diff >= -1e7) else None

            ocf     = _q_or_diff('operating_cf')
            icf     = _q_or_diff('investing_cf')
            fcf_cf  = _q_or_diff('financing_cf')
            capex_v = _q_or_diff('capex')
            if capex_v is not None and capex_v < 0:
                capex_v = None

            # 감가상각비: dep_q 신뢰 연도는 dep_q 우선, 아니면 누적차감/역산
            yr, qtr = r['year'], r['quarter']
            depr_q = r['depreciation_q'] if 'depreciation_q' in r.keys() else None
            depr_cum = r['depreciation'] if 'depreciation' in r.keys() else None
            dep_q_ok = (depr_q is not None) and (depr_q >= 0) and (yr in _dep_q_trusted_years)
            if dep_q_ok:
                depr_v = depr_q
                # _prev_cumul도 업데이트 — 같은 해 이후 분기에서 dep_q=None 폴백 시 사용
                if depr_cum is not None and qtr:
                    _prev_cumul[(yr, 'depreciation')] = depr_cum
            else:
                if depr_cum is not None and qtr and qtr > 1:
                    prev_cum = _prev_cumul.get((yr, 'depreciation'))
                    diff = (depr_cum - prev_cum) if prev_cum is not None else depr_cum
                    # 역산 결과가 음수(단위혼재/비단조 오염) → NULL 처리
                    depr_v = diff if (diff is None or diff >= 0) else None
                else:
                    # Q1: 누적=분기. 단, dep_cum 자체가 음수면 NULL
                    depr_v = depr_cum if (depr_cum is None or depr_cum >= 0) else None
                # 이번 분기 누적값 저장 (다음 분기 차감용)
                if depr_cum is not None and qtr:
                    _prev_cumul[(yr, 'depreciation')] = depr_cum

            # Q4 추론: Annual - Q3_cumulative
            # 단, source 혼합일 가능성이 높으면 무리한 역산을 피하기 위해 depreciation_q 우선/없으면 NULL 유지
            if r['quarter'] == 4:
                if depr_v is None and yr in _annual_depr and yr in _q3_depr:
                    _inferred = _annual_depr[yr] - _q3_depr[yr]
                    depr_v = _inferred if _inferred >= 0 else None  # 음수 역산 차단
                if capex_v is None and yr in _annual_capex_map and yr in _q3_capex:
                    _inferred_cap = _annual_capex_map[yr] - _q3_capex[yr]
                    capex_v = _inferred_cap if _inferred_cap >= 0 else None

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

    # ── 0. 종목 섹터/업종 (STRUCTURAL_DIFF_FINANCIAL_SECTOR 오분류 검증용) ──
    # ⚠️ 2026-08-23: ai_verdict='STRUCTURAL_DIFF_FINANCIAL_SECTOR'가
    # "금융업/지주사라서 DART 총영업수익 vs FnGuide 순영업수익 차이가
    # 정상"이라는 근거로 CONFIRMED 처리된 케이스가 실제로는 전체
    # 1,009종목 중 953종목(94.5%)이 금융/지주사가 아니었음(2026-05-21
    # 일괄 AI검증 배치의 오분류로 추정, 예: 삼성전자·에이엘티 등).
    # 실제로 금융/지주사가 아닌데 이 근거로 confirmed 처리된 항목은
    # ambiguous로 재분류해 "A-완전검증"이 실제와 다르게 표시되지 않게 한다.
    su_row = conn.execute(
        "SELECT stock_name, sector_large FROM stock_universe WHERE stock_code=?",
        (stock_code,),
    ).fetchone()
    _su_name = (su_row["stock_name"] if su_row else "") or ""
    _su_sector = (su_row["sector_large"] if su_row else "") or ""
    is_financial_like = (
        _su_sector == "금융"
        or any(k in _su_name for k in ("지주", "홀딩스", "금융", "증권", "보험", "캐피탈"))
    )

    # ── 1. 4중 검증 플래그 (전 연도 — 등급 판정은 2022+ 기준) ──────────
    # 등급 판정용: 최근 데이터(2022+) 기준
    flags = conn.execute("""
        SELECT flag_type, field, status, COUNT(*) AS cnt
        FROM cf_validation_flags
        WHERE stock_code=? AND year>=2022
        GROUP BY flag_type, field, status
    """, (stock_code,)).fetchall()

    # 비금융/비지주사인데 STRUCTURAL_DIFF_FINANCIAL_SECTOR 근거로
    # CONFIRMED된 (flag_type, field)별 오분류 건수
    mislabeled_map = {}
    if not is_financial_like:
        mislabeled_rows = conn.execute("""
            SELECT flag_type, field, COUNT(*) AS cnt
            FROM cf_validation_flags
            WHERE stock_code=? AND year>=2022 AND status='CONFIRMED'
              AND ai_verdict='STRUCTURAL_DIFF_FINANCIAL_SECTOR'
            GROUP BY flag_type, field
        """, (stock_code,)).fetchall()
        for m in mislabeled_rows:
            mislabeled_map[(m["flag_type"] or "MATCH", m["field"])] = m["cnt"]

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

    # 비금융/비지주사에 잘못 적용된 STRUCTURAL_DIFF_FINANCIAL_SECTOR
    # confirmed 건을 ambiguous로 재분류 (건수는 total 불변, confirmed→ambiguous 이동)
    for (m_ftyp, m_fd), m_cnt in mislabeled_map.items():
        target = fin_cross_map if m_ftyp == "FIN_CROSS" else (fin_naver_map if m_ftyp == "FIN_NAVER" else None)
        if target is None or m_fd not in target:
            continue
        moved = min(m_cnt, target[m_fd]["confirmed"])
        if moved > 0:
            target[m_fd]["confirmed"] -= moved
            target[m_fd]["ambiguous"] += moved

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

    # ── 4-b. 분기 데이터 검증 현황 (fin_quarterly_validation_flags) ──
    import sqlite3 as _sl2
    conn2 = _sl2.connect("stock.db", timeout=10)
    conn2.row_factory = _sl2.Row

    # ── 투자 신뢰등급 (OPEN tier) ─────────────────────────────────────
    mktcap_row = conn2.execute(
        "SELECT MAX(market_cap) FROM stock_universe WHERE stock_code=?", (stock_code,)
    ).fetchone()
    _mktcap = (mktcap_row[0] or 0) if mktcap_row else 0
    if   _mktcap >= 1_000_000: _open_tier, _open_tier_color = "A", "#10b981"
    elif _mktcap >= 100_000:   _open_tier, _open_tier_color = "B", "#22c55e"
    elif _mktcap >= 10_000:    _open_tier, _open_tier_color = "C", "#f59e0b"
    else:                       _open_tier, _open_tier_color = "D", "#ef4444"

    # QUARTERLY_4WAY 전체 요약 (이 종목)
    q4way_summary = conn2.execute("""
        SELECT status, COUNT(*) cnt FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='QUARTERLY_4WAY'
        GROUP BY status
    """, (stock_code,)).fetchall()
    _q4s = {r[0]: r[1] for r in q4way_summary}
    _q4_ok  = sum(_q4s.get(s, 0) for s in ("CONFIRMED","CLOSE_MATCH","SELF_CONSISTENT"))
    _q4_open = _q4s.get("OPEN", 0)
    _q4_str  = _q4s.get("STRUCTURAL", 0)
    _q4_amb  = _q4s.get("AMBIGUOUS", 0)
    _q4_total = sum(_q4s.values())
    _q4_ok_pct = round(100 * _q4_ok / max(_q4_total, 1))

    # BS (총자산·총자본) QUARTERLY_4WAY 현황
    bs_q4way_rows = conn2.execute("""
        SELECT field, status, COUNT(*) cnt, MAX(year) max_year
        FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='QUARTERLY_4WAY'
          AND field IN ('total_assets','total_equity')
        GROUP BY field, status
    """, (stock_code,)).fetchall()
    _bsq = {}
    for r in bs_q4way_rows:
        fd = r[0]; st = r[1]
        if fd not in _bsq: _bsq[fd] = {}
        _bsq[fd][st] = r[2]

    # ANNUAL_CONSISTENCY: 분기합 vs 연간 일치
    ac_rows = conn2.execute("""
        SELECT status, field, year, ratio
        FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='ANNUAL_CONSISTENCY'
        ORDER BY year DESC
    """, (stock_code,)).fetchall()

    # QUARTERLY_4WAY: 다중 소스 교차 검증
    q4way_rows = conn2.execute("""
        SELECT status, field, year, quarter, source_count, ratio, notes
        FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='QUARTERLY_4WAY'
        ORDER BY year DESC, quarter DESC
    """, (stock_code,)).fetchall()

    # DART_FG_CROSS: DART vs FnGuide 분기 교차
    fg_rows = conn2.execute("""
        SELECT status, field, year, quarter, dart_value, fnguide_value, ratio
        FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='DART_FG_CROSS'
        ORDER BY year DESC
    """, (stock_code,)).fetchall()

    # DART_NAVER_CROSS: DART vs Naver 분기 교차 (신규)
    naver_cross_rows = conn2.execute("""
        SELECT status, field, year, quarter, dart_value, fnguide_value as naver_value, ratio
        FROM fin_quarterly_validation_flags
        WHERE stock_code=? AND check_type='DART_NAVER_CROSS'
        ORDER BY year DESC
    """, (stock_code,)).fetchall()

    # 2025년 기준 QUARTERLY_4WAY P&L
    q4way_2025 = [r for r in q4way_rows if r["year"] == 2025 and r["field"] in ("revenue","operating_profit","net_income")]

    # 연간별 financial_data 소스 (연간만)
    annual_sources = conn2.execute("""
        SELECT year, data_source, report_type,
               revenue, operating_profit, net_income,
               total_assets, total_liabilities, total_equity
        FROM financial_data
        WHERE stock_code=? AND is_annual=1
          AND year BETWEEN 2016 AND 2025
        ORDER BY year
    """, (stock_code,)).fetchall()

    # 분기별 financial_data (검증 대상 분기)
    quarterly_rows = conn2.execute("""
        SELECT year, quarter, data_source, revenue
        FROM financial_data
        WHERE stock_code=? AND is_annual=0
          AND year BETWEEN 2019 AND 2025
        ORDER BY year, quarter
    """, (stock_code,)).fetchall()

    # naver_financial 수집 여부
    # ⚠️ naver_financial.is_annual은 numeric 컬럼(financial_data와 달리 boolean 아님) —
    # db_compat.py의 blanket 'is_annual=1 -> IS TRUE' 정규식이 테이블 구분 없이 적용돼
    # DatatypeMismatch 500을 유발했음. 비교연산자(>=1/<1)로 정규식 매칭을 우회.
    naver_fin_cnt = conn2.execute("""
        SELECT COUNT(*) FROM naver_financial WHERE stock_code=? AND is_annual>=1
    """, (stock_code,)).fetchone()[0]
    naver_qtr_cnt = conn2.execute("""
        SELECT COUNT(*) FROM naver_financial WHERE stock_code=? AND is_annual<1
    """, (stock_code,)).fetchone()[0]

    conn2.close()

    # ── 종목별 연도/항목 검증 요약 (UI 표시용) ──────────────────────────
    annual_by_year = {}
    for r in annual_sources:
        y = int(r["year"])
        annual_by_year.setdefault(y, []).append(r)

    year_item_summary = []
    for y in sorted(annual_by_year.keys(), reverse=True):
        rows_y = annual_by_year[y]
        # CFS 우선
        row = next((rr for rr in rows_y if (rr["report_type"] or "CFS") == "CFS"), rows_y[0])
        src = (row["data_source"] or "").lower()
        pl_ok = all(row[k] is not None for k in ("revenue", "operating_profit", "net_income"))
        bs_has = all(row[k] is not None for k in ("total_assets", "total_liabilities", "total_equity"))
        bs_identity_ok = False
        if bs_has:
            try:
                ta, tl, te = float(row["total_assets"]), float(row["total_liabilities"]), float(row["total_equity"])
                bs_identity_ok = abs((ta - tl) - te) <= max(abs(ta) * 0.01, 5e8)
            except Exception:
                bs_identity_ok = False
        source_label = (
            "DART+FnGuide+Naver" if ("naver" in src and "fnguide" in src) else
            "DART+FnGuide" if "fnguide" in src else
            "DART"
        )
        check_basis = "교차검증"
        if source_label == "DART":
            check_basis = "DART+수식검증"
        year_item_summary.append({
            "year": y,
            "source": source_label,
            "pl_ok": pl_ok,
            "bs_ok": bs_identity_ok,
            "check_basis": check_basis,
            "note": "" if (pl_ok and (bs_identity_ok or not bs_has)) else "일부 항목 추가 점검 필요",
        })

    # 최근 gate 보정 로그 (종목별)
    conn3 = _sl2.connect("stock.db", timeout=10)
    conn3.row_factory = _sl2.Row
    gate_logs = conn3.execute("""
        SELECT gate_ts, table_name, year, quarter, is_annual, report_type, reason_code
        FROM write_gate_log
        WHERE stock_code=?
        ORDER BY id DESC
        LIMIT 12
    """, (stock_code,)).fetchall()

    fin_fix_logs = conn3.execute("""
        SELECT fixed_at, year, quarter, is_annual, report_type, field_name
        FROM financial_fix_log
        WHERE stock_code=?
        ORDER BY id DESC
        LIMIT 12
    """, (stock_code,)).fetchall()

    cf_fix_logs = conn3.execute("""
        SELECT fixed_at, year, quarter, is_annual, report_type, field_name
        FROM cashflow_fix_log
        WHERE stock_code=?
        ORDER BY id DESC
        LIMIT 12
    """, (stock_code,)).fetchall()
    conn3.close()

    recent_fixes = []
    for gl in gate_logs:
        y = gl["year"]
        q = gl["quarter"]
        yq = f"{y}Q{q}" if gl["is_annual"] == 0 and y and q else (f"{y}Y" if y else "-")
        recent_fixes.append({
            "ts": gl["gate_ts"],
            "target": f"{gl['table_name']} {yq} {gl['report_type'] or 'CFS'}",
            "reason": gl["reason_code"],
        })
    for fl in fin_fix_logs:
        y = fl["year"]
        q = fl["quarter"]
        yq = f"{y}Q{q}" if fl["is_annual"] == 0 and y and q else (f"{y}Y" if y else "-")
        recent_fixes.append({
            "ts": fl["fixed_at"],
            "target": f"financial_data {yq} {fl['report_type'] or 'CFS'}",
            "reason": f"MANUAL_FIX:{fl['field_name']}",
        })
    for cl in cf_fix_logs:
        y = cl["year"]
        q = cl["quarter"]
        yq = f"{y}Q{q}" if cl["is_annual"] == 0 and y and q else (f"{y}Y" if y else "-")
        recent_fixes.append({
            "ts": cl["fixed_at"],
            "target": f"cash_flow_data {yq} {cl['report_type'] or 'CFS'}",
            "reason": f"MANUAL_FIX:{cl['field_name']}",
        })
    recent_fixes.sort(key=lambda x: x.get("ts") or "", reverse=True)

    # 요약문(삼성전자 예시 형태)
    annual_verified_years = [str(x["year"]) for x in year_item_summary if x["pl_ok"] and x["bs_ok"]]
    dart_formula_years = [str(x["year"]) for x in year_item_summary if x["source"] == "DART" and x["pl_ok"]]
    verification_summary_lines = []
    if annual_verified_years:
        verification_summary_lines.append(f"연간 완전검증: {', '.join(annual_verified_years)}")
    if dart_formula_years:
        verification_summary_lines.append(f"DART+수식검증: {', '.join(dart_formula_years)}")
    if not verification_summary_lines:
        verification_summary_lines.append("연간 검증: 추가 수집/검증 진행 필요")

    # ANNUAL_CONSISTENCY 집계
    ac_confirmed  = sum(1 for r in ac_rows if r["status"] in ("CONFIRMED","CLOSE_MATCH"))
    ac_ambiguous  = sum(1 for r in ac_rows if r["status"] == "AMBIGUOUS")
    ac_structural = sum(1 for r in ac_rows if r["status"] == "STRUCTURAL")
    ac_total      = len(ac_rows)

    # 연간 소스별 집계
    src_by_year = {r["year"]: r["data_source"] for r in annual_sources}
    dart_only_years = [yr for yr, src in src_by_year.items()
                       if yr <= 2021 and "dart" in (src or "").lower()
                       and "fnguide" not in (src or "").lower()]
    multi_src_years = [yr for yr, src in src_by_year.items()
                       if yr >= 2022 and "fnguide" in (src or "").lower()]

    # 분기 데이터 소스 현황
    qtr_legacy = sum(1 for r in quarterly_rows if r["data_source"] == "legacy_collected")
    qtr_dart   = sum(1 for r in quarterly_rows if "dart" in (r["data_source"] or "").lower() or "quarterly" in (r["data_source"] or "").lower())
    qtr_fg     = sum(1 for r in quarterly_rows if "fnguide" in (r["data_source"] or "").lower())
    qtr_total  = len(quarterly_rows)

    # 분기 합계 일치 항목 (ANNUAL_CONSISTENCY)
    if ac_total == 0:
        qval_item = {"field": "분기합 vs 연간 일치", "status": "missing",
                     "label": "❌ 검증 미실시", "detail": "dart_recollect 분기 데이터 없음",
                     "sources": "DART 내부"}
    elif ac_ambiguous > 0:
        pct = round(100 * ac_confirmed / ac_total) if ac_total > 0 else 0
        qval_item = {"field": "분기합 vs 연간 일치", "status": "ambiguous",
                     "label": f"⚠️ 불일치 {ac_ambiguous}건",
                     "detail": f"Q1+Q2+Q3+Q4 ≠ 연간 {ac_ambiguous}건 · 일치 {ac_confirmed}건 ({pct}%) · 구조적 {ac_structural}건",
                     "sources": "DART 내부"}
    elif ac_confirmed > 0:
        pct = round(100 * ac_confirmed / ac_total) if ac_total > 0 else 0
        qval_item = {"field": "분기합 vs 연간 일치", "status": "confirmed",
                     "label": f"✅ 일치 {pct}%",
                     "detail": f"Q1+Q2+Q3+Q4 ≈ 연간 CONFIRMED {ac_confirmed}건 · 구조적예외 {ac_structural}건",
                     "sources": "DART 내부"}
    else:
        qval_item = {"field": "분기합 vs 연간 일치", "status": "ok",
                     "label": "〰 구조적 예외만",
                     "detail": f"구조적예외 {ac_structural}건 (금융/지주 등)",
                     "sources": "DART 내부"}

    # 분기 소스 품질 항목
    if qtr_legacy > 0:
        qsrc_pct = round(100*qtr_legacy/max(qtr_total,1))
        qsrc_item = {"field": "분기 소스 품질", "status": "partial",
                     "label": f"△ 미검증 {qsrc_pct}%",
                     "detail": f"legacy 미검증 {qtr_legacy}건 / DART수집 {qtr_dart}건 / FnGuide {qtr_fg}건 (총 {qtr_total}건)",
                     "sources": "DART+FnGuide"}
    else:
        qsrc_item = {"field": "분기 소스 품질", "status": "ok",
                     "label": f"✔ DART {qtr_dart}건",
                     "detail": f"FnGuide {qtr_fg}건 포함 · 총 {qtr_total}건 수집",
                     "sources": "DART+FnGuide"}

    # DART vs FnGuide 분기 교차 항목
    if fg_rows:
        fg_confirmed  = sum(1 for r in fg_rows if r["status"] in ("CONFIRMED","CLOSE_MATCH"))
        fg_ambiguous  = sum(1 for r in fg_rows if r["status"] == "AMBIGUOUS")
        fg_structural = sum(1 for r in fg_rows if r["status"] == "STRUCTURAL")
        fg_total      = len(fg_rows)
        fg_pct = round(100 * fg_confirmed / fg_total) if fg_total > 0 else 0
        if fg_ambiguous == 0:
            fg_item = {"field": "DART·FnGuide 분기 교차", "status": "confirmed",
                       "label": f"✅ 일치 {fg_pct}% (불일치 0건)",
                       "detail": f"CONFIRMED {fg_confirmed}건 · 구조적차이 {fg_structural}건 · AMBIGUOUS 0건",
                       "sources": "DART+FnGuide"}
        else:
            fg_item = {"field": "DART·FnGuide 분기 교차", "status": "ambiguous",
                       "label": f"⚠️ 불일치 {fg_ambiguous}건",
                       "detail": f"CONFIRMED {fg_confirmed}건 ({fg_pct}%) · AMBIGUOUS {fg_ambiguous}건 · 구조적 {fg_structural}건",
                       "sources": "DART+FnGuide"}
    else:
        fg_item = None

    # DART vs Naver 분기 교차 항목 (신규)
    if naver_cross_rows:
        nc_confirmed  = sum(1 for r in naver_cross_rows if r["status"] in ("CONFIRMED","CLOSE_MATCH"))
        nc_ambiguous  = sum(1 for r in naver_cross_rows if r["status"] == "AMBIGUOUS")
        nc_structural = sum(1 for r in naver_cross_rows if r["status"] == "STRUCTURAL")
        nc_total      = len(naver_cross_rows)
        nc_pct = round(100 * nc_confirmed / nc_total) if nc_total > 0 else 0
        # 최근 연도 범위
        nc_years = sorted({r["year"] for r in naver_cross_rows})
        nc_yr_range = f"{min(nc_years)}~{max(nc_years)}" if len(nc_years) > 1 else str(nc_years[0]) if nc_years else ""
        if nc_ambiguous == 0:
            nc_item = {"field": "DART·Naver 분기 교차", "status": "confirmed",
                       "label": f"✅ 일치 {nc_pct}%",
                       "detail": f"CONFIRMED {nc_confirmed}건 ({nc_yr_range}년) · 구조적차이 {nc_structural}건",
                       "sources": "DART+Naver"}
        elif nc_ambiguous <= nc_total * 0.15:
            nc_item = {"field": "DART·Naver 분기 교차", "status": "ok",
                       "label": f"✔ 일치 {nc_pct}% (경미불일치 {nc_ambiguous}건)",
                       "detail": f"CONFIRMED {nc_confirmed}건 · AMBIGUOUS {nc_ambiguous}건(15-35%차이) · 구조적 {nc_structural}건 ({nc_yr_range}년)",
                       "sources": "DART+Naver"}
        else:
            nc_item = {"field": "DART·Naver 분기 교차", "status": "ambiguous",
                       "label": f"⚠️ 불일치 {nc_ambiguous}건 ({100-nc_pct}%)",
                       "detail": f"CONFIRMED {nc_confirmed}건 ({nc_pct}%) · AMBIGUOUS {nc_ambiguous}건 · 구조적 {nc_structural}건 ({nc_yr_range}년)",
                       "sources": "DART+Naver"}
    else:
        nc_item = None

    # 2019~2021 한계 안내
    if dart_only_years:
        limit_item = {"field": f"구기 데이터 ({min(dart_only_years)}~{max(dart_only_years)}년)",
                      "status": "na",
                      "label": "〰 DART 단독 (교차검증 불가)",
                      "detail": f"{min(dart_only_years)}~{max(dart_only_years)}년 FnGuide·Naver 미제공 — DART만 가능. ID매핑 정확도 ~95%",
                      "sources": "DART단독"}
    else:
        limit_item = None

    # 네이버 수집 현황 항목
    if naver_fin_cnt > 0 or naver_qtr_cnt > 0:
        naver_item = {"field": f"Naver 수집 현황",
                      "status": "ok",
                      "label": f"✔ 연간 {naver_fin_cnt}건 / 분기 {naver_qtr_cnt}건",
                      "detail": f"Naver 금융 P&L 수집 완료 · DART·FnGuide 교차검증 소스로 활용",
                      "sources": "DART+FnGuide+Naver"}
    else:
        naver_item = {"field": "Naver 수집 현황", "status": "na",
                      "label": "〰 미수집",
                      "detail": "네이버 금융 P&L 수집 예정 (2023~2025)",
                      "sources": "—"}

    # 분기 4중 검증 항목 (QUARTERLY_4WAY)
    if q4way_2025:
        q4_confirmed = sum(1 for r in q4way_2025 if r["status"] in ("CONFIRMED","CLOSE_MATCH"))
        q4_ambiguous = sum(1 for r in q4way_2025 if r["status"] == "AMBIGUOUS")
        q4_open      = sum(1 for r in q4way_2025 if r["status"] == "OPEN")
        q4_total     = len(q4way_2025)
        q4_pct       = round(100*q4_confirmed/q4_total) if q4_total > 0 else 0
        # 소스 수 최대값
        max_src = max((r["source_count"] or 0) for r in q4way_2025)
        src_label = {1:"DART단독", 2:"DART+1소스", 3:"DART+FnGuide+Naver", 4:"4중검증"}.get(max_src, f"{max_src}소스")
        if q4_ambiguous > 0 and q4_ambiguous > q4_total * 0.15:
            q4way_item = {"field": "분기 4중 검증 (2025, P&L)",
                          "status": "ambiguous",
                          "label": f"⚠️ 불일치 {q4_ambiguous}건 ({round(100*q4_ambiguous/q4_total)}%)",
                          "detail": f"CONFIRMED {q4_confirmed}/{q4_total} ({q4_pct}%) · AMBIGUOUS {q4_ambiguous}건 · {src_label}",
                          "sources": src_label}
        elif q4_confirmed >= q4_total * 0.7:
            q4way_item = {"field": "분기 4중 검증 (2025, P&L)",
                          "status": "confirmed",
                          "label": f"✅ 교차확인 {q4_pct}%",
                          "detail": f"CONFIRMED {q4_confirmed}/{q4_total} ({q4_pct}%) · {src_label}",
                          "sources": src_label}
        elif q4_open == q4_total:
            q4way_item = {"field": "분기 4중 검증 (2025, P&L)",
                          "status": "missing",
                          "label": "❌ 교차소스 없음",
                          "detail": "FnGuide·Naver 분기 데이터 미제공",
                          "sources": "DART단독"}
        else:
            q4way_item = {"field": "분기 4중 검증 (2025, P&L)",
                          "status": "partial",
                          "label": f"△ 부분확인 {q4_pct}%",
                          "detail": f"CONFIRMED {q4_confirmed}/{q4_total} ({q4_pct}%) · {src_label}",
                          "sources": src_label}
    else:
        # 전체 연도 기준
        q4_all_confirmed = sum(1 for r in q4way_rows if r["status"] in ("CONFIRMED","CLOSE_MATCH"))
        if q4_all_confirmed > 0:
            q4way_item = {"field": "분기 4중 검증",
                          "status": "partial",
                          "label": f"△ {q4_all_confirmed}건 확인됨 (2025 분기 미수집)",
                          "detail": "2025년 분기 데이터 없음 (DART·FnGuide·Naver 미매칭)",
                          "sources": "DART+FnGuide+Naver"}
        else:
            q4way_item = {"field": "분기 4중 검증",
                          "status": "missing",
                          "label": "❌ 4중 검증 미실시",
                          "detail": "분기 데이터 없거나 교차소스 미제공",
                          "sources": "—"}

    # ── 투자 신뢰등급 배지 항목 ──────────────────────────────────────
    _tier_labels = {
        "A": "투자사용 가능 — 대형주, 공시감사 엄격",
        "B": "투자사용 가능 — 중형주, 신뢰충분",
        "C": "주의 — 소형주, DART단독 확인권고",
        "D": "주의 — 초소형주, 개별확인 필수",
    }
    _tier_status = {"A": "confirmed", "B": "confirmed", "C": "partial", "D": "warning"}
    open_tier_item = {
        "field": f"OPEN 데이터 투자신뢰등급",
        "status": _tier_status[_open_tier],
        "label": f"{'✅' if _open_tier in ('A','B') else '⚠️'} {_open_tier}급 — {_tier_labels[_open_tier]}",
        "detail": (f"시총 {round(_mktcap/10000)}조" if _mktcap >= 10_000_000
                   else f"시총 {round(_mktcap/1000)}억" if _mktcap >= 1_000
                   else "시총 100억 미만"),
        "sources": f"시총기준",
        "tier": _open_tier,
        "tier_color": _open_tier_color,
    }

    # ── QUARTERLY_4WAY 전체 요약 항목 ────────────────────────────────
    if _q4_total > 0:
        q4_summary_item = {
            "field": "4중 교차검증 종합 (분기 전체)",
            "status": "confirmed" if _q4_ok_pct >= 70 else "partial" if _q4_ok_pct >= 40 else "missing",
            "label": f"{'✅' if _q4_ok_pct>=70 else '△'} OK {_q4_ok_pct}% ({_q4_ok}/{_q4_total}건)",
            "detail": (f"CONFIRMED·CLOSE_MATCH·SELF_CONSISTENT {_q4_ok}건 · "
                       f"STRUCTURAL {_q4_str}건 · OPEN {_q4_open}건 · AMBIGUOUS {_q4_amb}건"),
            "sources": "DART+FnGuide+Naver+OFS(4중)",
        }
    else:
        q4_summary_item = {
            "field": "4중 교차검증 종합",
            "status": "missing",
            "label": "❌ 검증 데이터 없음",
            "detail": "분기 교차검증 미실시",
            "sources": "—",
        }

    # ── BS QUARTERLY_4WAY 항목 ──────────────────────────────────────
    _ta = _bsq.get("total_assets", {}); _te = _bsq.get("total_equity", {})
    _bs_ok  = sum(_ta.get(s,0) + _te.get(s,0) for s in ("CONFIRMED","CLOSE_MATCH"))
    _bs_open = _ta.get("OPEN",0) + _te.get("OPEN",0)
    _bs_tot  = sum(_ta.values()) + sum(_te.values())
    if _bs_tot > 0:
        _bs_pct = round(100*_bs_ok / _bs_tot)
        bs_q4way_item = {
            "field": "BS 교차검증 (총자산·총자본)",
            "status": "confirmed" if _bs_pct >= 60 else "partial" if _bs_pct >= 30 else "missing",
            "label": f"{'✅' if _bs_pct>=60 else '△'} {_bs_pct}% 확인",
            "detail": (f"CLOSE_MATCH {_bs_ok//2}건 · OPEN {_bs_open//2}건 — "
                       f"{'CFS/OFS 교차 또는 FnGuide 연간 간접검증' if _bs_ok>0 else 'Q1/Q3 BS는 의무공시 아님'}"),
            "sources": "DART CFS+OFS+FnGuide",
        }
    else:
        bs_q4way_item = None

    # 구분선 + 분기 검증 섹션 헤더
    items.append({"field": "──분기 데이터 검증──", "status": "section_header",
                  "label": "", "detail": "", "sources": ""})
    items.append(open_tier_item)
    items.append(q4_summary_item)
    items.append(qval_item)
    items.append(qsrc_item)
    items.append(q4way_item)
    if bs_q4way_item:
        items.append(bs_q4way_item)
    if fg_item:
        items.append(fg_item)
    if nc_item:
        items.append(nc_item)
    if limit_item:
        items.append(limit_item)
    items.append(naver_item)

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

    # ── 6. 연도×항목 매트릭스 (새 UI용) ─────────────────────────────────
    # 표시 연도: 최근 7년 (2019~2025)
    _matrix_years = list(range(2019, 2026))

    # (a) 연도별 financial_data 상태
    _fin_annual = {}  # year -> {rev, op, ni, assets, equity, source, report_type}
    _cf_annual  = {}  # year -> {ocf, icf, fcf, capex, dep, source, report_type}

    import sqlite3 as _sl3
    _mc = _sl3.connect("stock.db", timeout=10)
    _mc.row_factory = _sl3.Row

    # 연간 재무 (CFS 우선)
    for r in _mc.execute("""
        SELECT year, data_source, report_type,
               revenue, operating_profit, net_income, total_assets, total_equity
        FROM financial_data
        WHERE stock_code=? AND is_annual=1 AND year BETWEEN 2019 AND 2025
        ORDER BY year,
          CASE WHEN (report_type IS NULL OR report_type='CFS') THEN 0 ELSE 1 END,
          CASE WHEN data_source LIKE '%fnguide%' THEN 0
               WHEN data_source LIKE '%dart%' THEN 1 ELSE 2 END
    """, (stock_code,)).fetchall():
        y = int(r["year"])
        if y not in _fin_annual:
            _fin_annual[y] = {
                "revenue": r["revenue"],
                "operating_profit": r["operating_profit"],
                "net_income": r["net_income"],
                "total_assets": r["total_assets"],
                "total_equity": r["total_equity"],
                "source": r["data_source"] or "",
                "rt": r["report_type"] or "CFS",
            }

    # 연간 CF (CFS 우선)
    for r in _mc.execute("""
        SELECT year, data_source, report_type,
               operating_cf, investing_cf, financing_cf, capex, depreciation
        FROM cash_flow_data
        WHERE stock_code=? AND is_annual=1 AND year BETWEEN 2019 AND 2025
        ORDER BY year,
          CASE WHEN (report_type IS NULL OR report_type='CFS') THEN 0 ELSE 1 END,
          CASE WHEN data_source LIKE '%fnguide%' THEN 0
               WHEN data_source LIKE '%dart%' THEN 1 ELSE 2 END
    """, (stock_code,)).fetchall():
        y = int(r["year"])
        if y not in _cf_annual:
            _cf_annual[y] = {
                "operating_cf": r["operating_cf"],
                "investing_cf": r["investing_cf"],
                "financing_cf": r["financing_cf"],
                "capex": r["capex"],
                "depreciation": r["depreciation"],
                "source": r["data_source"] or "",
                "rt": r["report_type"] or "CFS",
            }

    # (b) 연도별 검증 플래그 (cf_validation_flags)
    _vflags = {}  # (year, field) -> {status, flag_type}
    for r in _mc.execute("""
        SELECT year, field, flag_type, status, COUNT(*) cnt
        FROM cf_validation_flags WHERE stock_code=?
          AND year BETWEEN 2019 AND 2025
        GROUP BY year, field, flag_type, status
        ORDER BY year, field,
          CASE flag_type WHEN 'FIN_NAVER' THEN 0 WHEN 'FIN_CROSS' THEN 1 WHEN 'MATCH' THEN 2 ELSE 3 END
    """, (stock_code,)).fetchall():
        k = (int(r["year"]), r["field"])
        if k not in _vflags:
            _vflags[k] = {"status": r["status"], "flag_type": r["flag_type"]}
        # 우선순위: AMBIGUOUS > CONFIRMED > CLOSE_MATCH
        existing = _vflags[k]
        if r["status"] == "AMBIGUOUS":
            _vflags[k] = {"status": "AMBIGUOUS", "flag_type": r["flag_type"]}

    # (c) data_lock 상태
    # 2026-08-14: PostgreSQL에 data_lock 테이블 생성 + SQLite 6,840행 이관 완료
    # (기존엔 테이블 자체가 없어 조용히 "잠금 없음"으로만 폴백되고 있었음).
    # try/except 폴백은 향후 재발방지용으로 유지.
    _locked_years = set()
    try:
        for r in _mc.execute("""
            SELECT DISTINCT year FROM data_lock
            WHERE stock_code=? AND is_locked=1 AND table_name='financial_data'
              AND year BETWEEN 2019 AND 2025
        """, (stock_code,)).fetchall():
            _locked_years.add(int(r["year"]))
    except Exception:
        pass

    _mc.close()

    def _cell_status(year, field_key, has_data):
        """
        셀 상태 반환.
        반환: {"s": status_code, "src": source_label, "locked": bool}
        status_code:
          "V3" = 3중 이상 검증 (DART+FnGuide+Naver)
          "V2" = 2중 검증 (DART+FnGuide 또는 DART+Seibro)
          "V1" = DART 단독 수집 (검증 없음)
          "CALC" = 계산값 (Q4 역산 등)
          "MISS" = 미수집 / NULL
          "WARN" = 불일치 경고
        """
        locked = year in _locked_years
        if not has_data:
            return {"s": "MISS", "src": "—", "locked": locked}

        vf = _vflags.get((year, field_key))
        if vf:
            st = vf["status"]
            ft = vf["flag_type"]
            if st == "AMBIGUOUS":
                return {"s": "WARN", "src": ft, "locked": locked}
            if st in ("CONFIRMED", "CLOSE_MATCH"):
                if ft == "FIN_NAVER":
                    return {"s": "V3", "src": "D+F+N", "locked": locked}
                if ft in ("MATCH",):  # Seibro 검증
                    return {"s": "V3", "src": "D+F+S", "locked": locked}
                if ft == "FIN_CROSS":
                    return {"s": "V2", "src": "D+F", "locked": locked}
                return {"s": "V2", "src": ft, "locked": locked}

        # 소스 기반 판단
        # fin 연도는 _fin_annual에서 source 확인
        src = ""
        if field_key in ("revenue", "operating_profit", "net_income", "total_assets", "total_equity"):
            src = (_fin_annual.get(year) or {}).get("source") or ""
        else:
            src = (_cf_annual.get(year) or {}).get("source") or ""
        src_l = src.lower()

        if "fnguide" in src_l and ("dart" in src_l or "naver" in src_l):
            return {"s": "V2", "src": "D+F", "locked": locked}
        if "fnguide" in src_l:
            return {"s": "V2", "src": "FnG", "locked": locked}
        if "dart" in src_l or "quarterly_recalc" in src_l:
            if "q4" in src_l or "recalc" in src_l:
                return {"s": "CALC", "src": "Q4역산", "locked": locked}
            return {"s": "V1", "src": "DART", "locked": locked}
        if "legacy" in src_l:
            return {"s": "V1", "src": "구버전", "locked": locked}
        return {"s": "V1", "src": "수집", "locked": locked}

    # 매트릭스 행 정의 (field_key, label, data_type)
    _matrix_rows = [
        # 재무제표
        {"key": "revenue",           "label": "매출",       "group": "재무제표"},
        {"key": "operating_profit",  "label": "영업이익",    "group": "재무제표"},
        {"key": "net_income",        "label": "순이익",      "group": "재무제표"},
        {"key": "total_assets",      "label": "총자산",      "group": "재무상태표"},
        {"key": "total_equity",      "label": "총자본",      "group": "재무상태표"},
        # 현금흐름
        {"key": "operating_cf",      "label": "OCF",        "group": "현금흐름"},
        {"key": "investing_cf",      "label": "ICF",        "group": "현금흐름"},
        {"key": "capex",             "label": "CapEx",      "group": "현금흐름"},
        {"key": "depreciation",      "label": "D&A",        "group": "현금흐름"},
    ]

    _cf_field_map = {"operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"}
    _fin_field_map = {"revenue", "operating_profit", "net_income", "total_assets", "total_equity"}

    matrix_data = []
    for row_def in _matrix_rows:
        fk = row_def["key"]
        cells = {}
        for yr in _matrix_years:
            if fk in _cf_field_map:
                cf_yr = _cf_annual.get(yr)
                has_d = cf_yr is not None and cf_yr.get(fk) is not None
            else:
                fin_yr = _fin_annual.get(yr)
                has_d = fin_yr is not None and fin_yr.get(fk) is not None
            cells[yr] = _cell_status(yr, fk, has_d)
        matrix_data.append({
            "key": fk,
            "label": row_def["label"],
            "group": row_def["group"],
            "cells": cells,
        })

    # CFS/OFS 수집 여부 요약
    _cfs_years = sorted(_fin_annual.keys())
    _ofs_fin = _mc_count = 0
    _tmp_mc = _sl3.connect("stock.db", timeout=10)
    try:
        _ofs_fin = _tmp_mc.execute(
            "SELECT COUNT(DISTINCT year) FROM financial_data WHERE stock_code=? AND report_type='OFS' AND is_annual=1",
            (stock_code,)
        ).fetchone()[0]
    except Exception:
        pass
    finally:
        _tmp_mc.close()

    return {
        "grade": grade,
        "grade_label": grade_label,
        "grade_color": grade_color,
        "grade_desc": grade_desc,
        "items": items,
        "has_validation": has_validation,
        "val_year_min": _min_yr,
        "val_year_max": _max_yr,
        "val_years": _val_years,
        "checked_years": [r["year"] for r in cf_rows],
        # 투자 신뢰등급
        "open_tier": _open_tier,
        "open_tier_color": _open_tier_color,
        # 4중 검증 요약
        "q4way_ok_pct": _q4_ok_pct,
        "q4way_open": _q4_open,
        "q4way_total": _q4_total,
        # 종목별 연도/항목 검증 요약
        "verification_summary_lines": verification_summary_lines,
        "year_item_summary": year_item_summary,
        "recent_fixes": recent_fixes,
        # ★ 새 UI: 연도×항목 매트릭스
        "matrix_years": _matrix_years,
        "matrix_data": matrix_data,
        "cfs_years": _cfs_years,
        "ofs_fin_years": _ofs_fin,
        "locked_years": sorted(_locked_years),
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
        from datetime import timedelta
        from dart_key_manager import RotatingOpenDartReader

        dart = RotatingOpenDartReader()

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
    # 핵심 카드(매출/영업이익/순이익)는 연간 연결(CFS) 기준으로 고정
    # 동일 연도에 CFS/OFS가 공존할 때 OFS가 섞여 노출되는 문제를 방지한다.
    data = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code,
        models.FinancialData.is_annual.is_(True),
    ).order_by(
        case((models.FinancialData.report_type == 'CFS', 0), else_=1),
        models.FinancialData.year.desc(),
        case((models.FinancialData.quarter == 4, 0), (models.FinancialData.quarter == 0, 1), else_=2),
        case((models.FinancialData.data_source == 'dart', 0), (models.FinancialData.data_source == 'fnguide', 1), else_=2),
        models.FinancialData.id.desc(),
    ).first()

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
    latest_volume = None
    avg_volume_20d = None
    volume_ratio_20d = None
    try:
        ph_rows = db.query(models.PriceHistory).filter(
            models.PriceHistory.stock_code == stock_code,
            models.PriceHistory.close > 0,
        ).order_by(models.PriceHistory.date.desc()).limit(252).all()
        if ph_rows:
            closes = [r.close for r in ph_rows]
            highs = [r.high for r in ph_rows if r.high and r.high > 0]
            lows  = [r.low  for r in ph_rows if r.low  and r.low  > 0]
            high52 = max(highs) if highs else max(closes)
            low52  = min(lows)  if lows  else min(closes)
            # 거래량은 일부 소스에서 0/null 행이 섞일 수 있어
            # "최근 유효 거래량(>0)" 기준으로 최신/평균을 계산한다.
            vols = [float(getattr(r, "volume", 0) or 0) for r in ph_rows]
            valid_vols = [v for v in vols if v > 0]
            if valid_vols:
                latest_volume = valid_vols[0]
                v20 = valid_vols[:20]
                if v20:
                    avg_volume_20d = sum(v20) / len(v20)
                    if avg_volume_20d > 0:
                        volume_ratio_20d = round(latest_volume / avg_volume_20d, 2)
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
    shareholder_profile = _load_shareholder_profile(stock_code) if is_kr else {}
    if shareholder_profile:
        if "float_shares" in shareholder_profile:
            float_shares = shareholder_profile.get("float_shares")
        shares_outstanding = shareholder_profile.get("shares_outstanding") or shares_outstanding

    ttm = _calc_ttm_fundamentals(stock_code, report_type="CFS") if is_kr else {
        "available": False,
        "periods": [],
        "period_start": None,
        "period_end": None,
        "revenue": None,
        "operating_profit": None,
        "net_income": None,
        "opm": None,
        "eps": None,
        "per": None,
        "source": None,
        "message": None,
    }
    forward_est = _load_latest_forward_estimate(stock_code) if is_kr else {
        "forward_per": None,
        "forward_eps": None,
        "forward_period": None,
        "forward_source": None,
    }

    if not data:
        return {
            "revenue": None, "operating_profit": None, "net_income": None,
            "opm": None,
            "roe": val.get("roe"), "roa": val.get("roa"),
            "bps": val.get("bps"),
            "pbr": val.get("pbr"), "per": val.get("per"),
            "forward_per": forward_est.get("forward_per"), "forward_eps": forward_est.get("forward_eps"),
            "forward_period": forward_est.get("forward_period"), "forward_source": forward_est.get("forward_source"),
            "trailing_eps": val.get("trailing_eps"),
            "ttm": ttm,
            "source": val.get("source"), "collecting": is_col,
            "high52": high52, "low52": low52,
            "latest_volume": latest_volume, "avg_volume_20d": avg_volume_20d, "volume_ratio_20d": volume_ratio_20d,
            "float_shares": float_shares, "shares_outstanding": shares_outstanding,
            "shares_issued": shareholder_profile.get("shares_issued"),
            "free_float_ratio": shareholder_profile.get("free_float_ratio"),
            "major_holder_name": shareholder_profile.get("major_holder_name"),
            "major_holder_ratio": shareholder_profile.get("major_holder_ratio"),
            "major_holder_shares": shareholder_profile.get("major_holder_shares"),
            "major_holder_report_date": shareholder_profile.get("major_holder_report_date"),
            "shareholder_data_quality": shareholder_profile.get("data_quality"),
            "shareholder_quality_note": shareholder_profile.get("quality_note"),
            "shareholder_profile": shareholder_profile or None,
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
        "forward_per":       forward_est.get("forward_per") if forward_est.get("forward_per") is not None else val.get("forward_per"),
        "forward_eps":       forward_est.get("forward_eps"),
        "forward_period":    forward_est.get("forward_period"),
        "forward_source":    forward_est.get("forward_source"),
        "trailing_eps":      val.get("trailing_eps"),
        "ttm":               ttm,
        "source":            val.get("source"),
        "collecting":        is_col,
        "high52":            high52,
        "low52":             low52,
        "latest_volume":     latest_volume,
        "avg_volume_20d":    avg_volume_20d,
        "volume_ratio_20d":  volume_ratio_20d,
        "float_shares":      float_shares,
        "shares_outstanding": shares_outstanding,
        "shares_issued":     shareholder_profile.get("shares_issued"),
        "free_float_ratio":  shareholder_profile.get("free_float_ratio"),
        "major_holder_name": shareholder_profile.get("major_holder_name"),
        "major_holder_ratio": shareholder_profile.get("major_holder_ratio"),
        "major_holder_shares": shareholder_profile.get("major_holder_shares"),
        "major_holder_report_date": shareholder_profile.get("major_holder_report_date"),
        "shareholder_data_quality": shareholder_profile.get("data_quality"),
        "shareholder_quality_note": shareholder_profile.get("quality_note"),
        "shareholder_profile": shareholder_profile or None,
    }


@app.get("/api/dashboard/shareholder-profiles")
def list_shareholder_profiles(
    q: str = "",
    market: str = "",
    quality: str = "",
    limit: int = 300,
):
    """국내 종목 유통주식수·주요주주 통합 프로필 목록."""
    import sqlite3 as _sl
    limit = max(1, min(int(limit or 300), 3000))
    conn = _sl.connect("stock.db")
    conn.row_factory = _sl.Row
    try:
        params: list = []
        where = ["1=1"]
        if q:
            where.append("(stock_code LIKE ? OR stock_name LIKE ? OR major_holder_name LIKE ?)")
            like = f"%{q.strip()}%"
            params.extend([like, like, like])
        if market:
            where.append("market = ?")
            params.append(market)
        if quality:
            where.append("data_quality = ?")
            params.append(quality)
        sql = f"""
            SELECT *
            FROM stock_shareholder_profile
            WHERE {' AND '.join(where)}
            ORDER BY market, stock_name
            LIMIT ?
        """
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for profile in rows:
            issued = float(profile.get("shares_issued") or 0)
            float_shares = float(profile.get("float_shares") or 0)
            ratio = profile.get("free_float_ratio")
            impossible = bool(issued and float_shares and float_shares > issued) or bool(
                ratio is not None and float(ratio) > 100.0
            )
            if impossible:
                profile["float_shares"] = None
                profile["free_float_ratio"] = None
                profile["data_quality"] = "review"
                note = str(profile.get("quality_note") or "").strip()
                warning = "유통주식수가 발행주식수를 초과해 표시 제외"
                profile["quality_note"] = f"{note} / {warning}" if note else warning
        summary = dict(conn.execute(
            """
            SELECT data_quality, COUNT(*)
            FROM stock_shareholder_profile
            GROUP BY data_quality
            """
        ).fetchall())
        return {"count": len(rows), "summary": summary, "items": rows}
    finally:
        conn.close()


@app.post("/api/commands/rebuild-shareholder-profiles")
def rebuild_shareholder_profiles(limit: int = 0):
    """stock_shareholder_profile 재생성."""
    try:
        from scripts.build_shareholder_profile import rebuild
        return {"status": "ok", **rebuild(limit=limit)}
    except Exception as e:
        logger.error("[shareholder_profile] rebuild failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

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
            from db_utils import stock_db_write_lock as _stock_db_write_lock

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
                            c2.rollback()
                            with _stock_db_write_lock(f"analyze-supply:{code}", timeout=30) as acquired:
                                if not acquired:
                                    logger.warning(f"[Analyze] {code} 수급 업데이트 지연 — 다음 조회에서 재시도")
                                    return
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
                c2 = _sl2.connect("stock.db", timeout=30)
                c2.execute("PRAGMA busy_timeout=30000")
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

@app.get("/api/market-regime")
def get_market_regime():
    """현재 KOSPI 시장 국면 (BULL/BEAR/NEUTRAL) 반환.
    BULL: KOSPI > MA120 * 1.01 (1% 위)
    BEAR: KOSPI < MA120 * 0.99 (1% 아래)
    NEUTRAL: MA120 ±1% 이내 (횡보)
    """
    import sqlite3 as _sl
    conn = _sl.connect("stock.db"); conn.row_factory = _sl.Row
    try:
        rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0
            ORDER BY date DESC LIMIT 130
        """).fetchall()
        if not rows:
            return {"regime": "UNKNOWN", "kospi": None, "ma120": None, "today": None}
        rows = list(reversed(rows))
        prices = [float(r["close"]) for r in rows]
        today_date = rows[-1]["date"]
        kospi = prices[-1]
        ma120 = sum(prices[-120:]) / min(120, len(prices))
        buf = 0.01
        if kospi > ma120 * (1 + buf):
            regime = "BULL"
        elif kospi < ma120 * (1 - buf):
            regime = "BEAR"
        else:
            regime = "NEUTRAL"

        # 최근 60일 KOSPI 추이 (프론트 미니차트용)
        recent = [{"date": r["date"], "close": float(r["close"])} for r in rows[-60:]]

        return {
            "regime": regime,
            "kospi": round(kospi, 2),
            "ma120": round(ma120, 2),
            "diff_pct": round((kospi - ma120) / ma120 * 100, 2),
            "today": today_date,
            "recent": recent,
        }
    finally:
        conn.close()


@app.get("/api/dashboard/stats")
def get_db_stats(db: Session = Depends(get_db)):
    """Return page-facing collection health derived from dataset contracts."""
    dataset_health = evaluate_all_contracts()
    health_by_key = {item["key"]: item for item in dataset_health}
    freshness = {
        "kr_price_latest": health_by_key.get("kr_price", {}).get("source_as_of"),
        "program_market_latest": health_by_key.get("program_market", {}).get("source_as_of"),
        "program_stock_latest": health_by_key.get("program_stock", {}).get("source_as_of"),
        "program_market_age_days": health_by_key.get("program_market", {}).get("lag"),
        "program_stock_age_days": health_by_key.get("program_stock", {}).get("lag"),
    }
    stale_items = [
        {
            "key": item["key"],
            "label": item["label"],
            "latest": item["source_as_of"],
            "collected_at": item["collected_at"],
            "expected": item["expected_as_of"],
            "age_days": item["lag"],
            "limit_days": next(
                (contract.allowed_lag for contract in DATASET_CONTRACTS if contract.key == item["key"]),
                None,
            ),
            "status": item["status"],
            "coverage": item["latest_coverage"],
            "minimum_coverage": item["minimum_coverage"],
            "issues": item["issues"],
        }
        for item in dataset_health
        if item["status"] != "healthy"
    ]

    if IS_POSTGRES:
        from routes.tenbagger import get_data_status
        table_status = get_data_status()
        stock_count = table_status.get("financial_data", {}).get("stocks", 0)
        price_records = table_status.get("price_history", {}).get("rows", 0)
    else:
        stock_count = db.query(models.FinancialData.stock_code).distinct().count()
        price_records = db.query(models.PriceHistory).count()

    return {
        "stock_count": stock_count,
        "price_records": price_records,
        "db_path": primary_database_label(),
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_freshness": freshness,
        "dataset_health": dataset_health,
        "collection_runs": latest_collection_runs(limit=30),
        "stale_collection_items": stale_items,
    }


@app.get("/api/dashboard/collection-runs")
def get_collection_runs(limit: int = 100):
    """Latest scheduler execution result per collection job."""
    return {"runs": latest_collection_runs(limit=limit)}
