"""
collect_overseas.py — 해외 주요 종목 시세·펀더멘털 수집기

대상: market_radar.py SECTORS에 정의된 해외 주식 (~35종목)
수집: OHLCV(price_history) + PER/PBR/시총(overseas_fundamentals)
방법: yfinance (무료, 15분 지연) — 1시간 1회 스케줄
실행: python collect_overseas.py  또는  scheduler.py _job_overseas_prices에서 호출

무료 해외 주가 API 비교:
  · yfinance      : 무료, 15분 지연, PER/PBR/시총 포함, 간혹 차단
  · Finnhub       : 60호출/분 무료, 미국 실시간, PER 포함 (API KEY 필요)
  · Alpha Vantage : 500호출/일, 5호출/분, 무료 (API KEY 필요)
  · Twelve Data   : 800크레딧/일 무료, 거의 실시간 (API KEY 필요)
  → 현재 구현: yfinance (API 키 불필요, 1시간 1회 충분)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "stock.db"

# ── 수집 대상 해외 종목 (market_radar.py SECTORS와 동일) ────────
OVERSEAS_STOCKS: list[dict] = [
    # 반도체
    {"symbol": "NVDA",   "name": "엔비디아",       "sector": "반도체"},
    {"symbol": "AMD",    "name": "AMD",            "sector": "반도체"},
    {"symbol": "AVGO",   "name": "브로드컴",        "sector": "반도체"},
    {"symbol": "MU",     "name": "마이크론",        "sector": "반도체"},
    {"symbol": "AMAT",   "name": "AMAT",           "sector": "반도체장비"},
    {"symbol": "LRCX",   "name": "램리서치",        "sector": "반도체장비"},
    {"symbol": "KLAC",   "name": "KLA",            "sector": "반도체장비"},
    {"symbol": "TSM",    "name": "TSMC",           "sector": "반도체"},
    {"symbol": "8035.T", "name": "도쿄일렉트론",    "sector": "반도체장비"},
    # 2차전지
    {"symbol": "TSLA",   "name": "테슬라",          "sector": "2차전지"},
    {"symbol": "ALB",    "name": "알베말",          "sector": "2차전지"},
    {"symbol": "LTHM",   "name": "리튬아메리카스",   "sector": "2차전지"},
    {"symbol": "6981.T", "name": "무라타제작소",     "sector": "2차전지"},
    # 전력인프라
    {"symbol": "ETN",    "name": "이튼",            "sector": "전력장비"},
    {"symbol": "VRT",    "name": "버티브",           "sector": "전력장비"},
    {"symbol": "PWR",    "name": "퀀타서비스",       "sector": "전력장비"},
    {"symbol": "HUBB",   "name": "허블",             "sector": "전력장비"},
    # 제약/바이오
    {"symbol": "LLY",    "name": "일라이릴리",       "sector": "제약"},
    {"symbol": "NVO",    "name": "노보노디스크",      "sector": "제약"},
    {"symbol": "ABBV",   "name": "애브비",           "sector": "제약"},
    {"symbol": "MRNA",   "name": "모더나",           "sector": "바이오"},
    # K방산/우주
    {"symbol": "LMT",    "name": "록히드마틴",       "sector": "방산"},
    {"symbol": "NOC",    "name": "노스롭그루만",      "sector": "방산"},
    {"symbol": "RTX",    "name": "RTX",             "sector": "방산"},
    {"symbol": "HII",    "name": "헌팅턴잉걸스",     "sector": "방산"},
    # 조선/해운
    {"symbol": "ZIM",    "name": "ZIM해운",          "sector": "해운"},
    {"symbol": "STNG",   "name": "스코피오탱커",      "sector": "해운"},
    {"symbol": "SBLK",   "name": "스타벌크",         "sector": "해운"},
    # 에너지/소재
    {"symbol": "XOM",    "name": "엑슨모빌",         "sector": "에너지"},
    {"symbol": "CVX",    "name": "셰브론",           "sector": "에너지"},
    {"symbol": "FCX",    "name": "프리포트맥모란",    "sector": "소재"},
    {"symbol": "RIO",    "name": "리오틴토",         "sector": "소재"},
]

SYMBOLS = [s["symbol"] for s in OVERSEAS_STOCKS]
SYMBOL_META = {s["symbol"]: s for s in OVERSEAS_STOCKS}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_overseas_table(conn: sqlite3.Connection) -> None:
    """overseas_fundamentals 테이블 생성 (없으면)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS overseas_fundamentals (
            symbol        TEXT PRIMARY KEY,
            name          TEXT,
            sector        TEXT,
            market_cap    REAL,
            per           REAL,
            pbr           REAL,
            eps           REAL,
            dividend_yield REAL,
            currency      TEXT,
            updated_at    TEXT
        )
    """)
    conn.commit()


def _safe(v: Any, default=None):
    try:
        if v is None or (isinstance(v, float) and v != v):  # NaN check
            return default
        return v
    except Exception:
        return default


def collect_prices(days_back: int = 5) -> int:
    """yfinance로 OHLCV 수집 → price_history에 upsert.

    Returns: 저장된 행 수
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[Overseas] yfinance 미설치. pip install yfinance")
        return 0

    since = (date.today() - timedelta(days=days_back + 5)).isoformat()
    saved = 0

    try:
        import pandas as _pd
        tickers_str = " ".join(SYMBOLS)
        df = yf.download(tickers_str, start=since, progress=False, auto_adjust=True, group_by="ticker")
        if df is None or df.empty:
            logger.warning("[Overseas] yfinance download returned empty")
            return 0

        conn = _db()
        try:
            for sym in SYMBOLS:
                try:
                    if len(SYMBOLS) == 1:
                        sym_df = df
                    else:
                        # MultiIndex: level 0 = ticker
                        if hasattr(df.columns, "levels"):
                            sym_df = df[sym] if sym in df.columns.get_level_values(0) else None
                        else:
                            sym_df = df
                    if sym_df is None or sym_df.empty:
                        continue

                    for ts, row in sym_df.iterrows():
                        try:
                            row_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                            if row_date.isoformat() < since:
                                continue
                            cl = float(row.get("Close", 0) or 0)
                            if cl <= 0:
                                continue
                            conn.execute("""
                                INSERT INTO price_history
                                    (stock_code, date, open, high, low, close, volume,
                                     inst_net_buy, frn_net_buy, ind_net_buy,
                                     inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt)
                                VALUES (?,?,?,?,?,?,?,0,0,0,0,0,0)
                                ON CONFLICT(stock_code, date) DO UPDATE SET
                                    open=excluded.open, high=excluded.high,
                                    low=excluded.low, close=excluded.close,
                                    volume=excluded.volume
                            """, (
                                sym,
                                row_date.isoformat(),
                                float(row.get("Open",   0) or 0),
                                float(row.get("High",   0) or 0),
                                float(row.get("Low",    0) or 0),
                                cl,
                                float(row.get("Volume", 0) or 0),
                            ))
                            saved += 1
                        except Exception as e:
                            logger.debug(f"[Overseas] {sym} 행 파싱 오류: {e}")
                except Exception as e:
                    logger.warning(f"[Overseas] {sym} 처리 오류: {e}")

            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[Overseas] 가격 수집 실패: {e}")

    logger.info(f"[Overseas] 가격 수집 완료 — {saved}행")
    return saved


def collect_fundamentals() -> int:
    """yfinance ticker.info로 PER/PBR/시총 수집 → overseas_fundamentals에 upsert.

    Returns: 저장된 종목 수
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[Overseas] yfinance 미설치")
        return 0

    conn = _db()
    try:
        _ensure_overseas_table(conn)
        saved = 0
        for s in OVERSEAS_STOCKS:
            sym = s["symbol"]
            try:
                t = yf.Ticker(sym)
                info = t.info or {}
                market_cap     = _safe(info.get("marketCap"))
                per            = _safe(info.get("trailingPE"))
                pbr            = _safe(info.get("priceToBook"))
                eps            = _safe(info.get("trailingEps"))
                div_yield      = _safe(info.get("dividendYield"))
                currency       = _safe(info.get("currency", "USD"))
                conn.execute("""
                    INSERT INTO overseas_fundamentals
                        (symbol, name, sector, market_cap, per, pbr, eps, dividend_yield, currency, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        name=excluded.name, sector=excluded.sector,
                        market_cap=excluded.market_cap, per=excluded.per,
                        pbr=excluded.pbr, eps=excluded.eps,
                        dividend_yield=excluded.dividend_yield,
                        currency=excluded.currency,
                        updated_at=excluded.updated_at
                """, (
                    sym, s["name"], s["sector"],
                    market_cap, per, pbr, eps, div_yield, currency,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))
                saved += 1
                time.sleep(0.3)   # rate limit
            except Exception as e:
                logger.warning(f"[Overseas] {sym} fundamentals 오류: {e}")
        conn.commit()
        logger.info(f"[Overseas] 펀더멘털 수집 완료 — {saved}종목")
        return saved
    finally:
        conn.close()


def run_all() -> None:
    """가격 + 펀더멘털 일괄 수집 (스케줄러에서 호출)."""
    logger.info("[Overseas] 수집 시작")
    collect_prices(days_back=5)
    collect_fundamentals()
    logger.info("[Overseas] 수집 완료")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_all()
