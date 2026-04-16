"""
stock_universe.py — 전종목 정보 DB 모델 + 임포트 + KRX 업데이트

테이블: stock_universe
  - 대한민국 상장 전종목 (KOSPI + KOSDAQ)
  - 엑셀 초기 적재 / KRX OpenAPI 매일 자정 업데이트

사용:
  python3 stock_universe.py --init-excel   # 엑셀 최초 적재
  python3 stock_universe.py --update-krx   # KRX API 업데이트 (1회)
  python3 stock_universe.py --schedule     # 자정 스케줄러 시작
"""

import os
import sys
import time
import logging
import argparse
import requests
import json
from datetime import datetime, date
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime,
    UniqueConstraint, Index, create_engine, text
)
from sqlalchemy.orm import declarative_base, Session
from sqlalchemy.sql import func

# ── 로거 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "stock_universe.log",
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

# ── DB 연결 (기존 프로젝트 database.py 재사용) ─────────────────
_BASE_DIR = Path("/Applications/stock_dashboard")
sys.path.insert(0, str(_BASE_DIR))

try:
    from database import engine, Base, SessionLocal
    logger.info("기존 database.py 사용")
except ImportError:
    # 단독 실행 시 fallback
    _DB_PATH = _BASE_DIR / "stock.db"
    engine = create_engine(
        f"sqlite:///{_DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    Base = declarative_base()
    def SessionLocal():
        from sqlalchemy.orm import sessionmaker
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    logger.info(f"단독 DB 연결: {_DB_PATH}")


# ═══════════════════════════════════════════════════════════════
#  모델 정의
# ═══════════════════════════════════════════════════════════════

class StockUniverse(Base):
    """
    대한민국 전종목 기본정보 + 일별 시세 요약.
    KRX API / 엑셀로 매일 1회 전체 UPSERT.
    """
    __tablename__ = "stock_universe"

    id              = Column(Integer, primary_key=True, index=True)
    stock_code      = Column(String(10), nullable=False, index=True)   # 6자리 종목코드
    stock_name      = Column(String(100), nullable=False)
    market          = Column(String(20))   # 유가증권(KOSPI) / 코스닥(KOSDAQ) / KONEX
    stock_type      = Column(String(20))   # 보통주 / 우선주 / ETF / 리츠 등

    # ── 일별 시세 ─────────────────────────────────────────────
    base_date       = Column(Date, index=True)  # 기준일 (데이터 날짜)
    close           = Column(Float)             # 종가
    open            = Column(Float)             # 시가
    high            = Column(Float)             # 고가
    low             = Column(Float)             # 저가
    change_rate     = Column(Float)             # 등락률 (%)
    volume          = Column(Float)             # 거래량
    trading_value   = Column(Float)             # 거래대금 (백만원)
    market_cap      = Column(Float)             # 시가총액 (억원)

    # ── 종목 기본정보 ─────────────────────────────────────────
    listed_date     = Column(Date)              # 상장일
    settlement_month= Column(Integer)           # 결산월
    face_value      = Column(Float)             # 액면가
    shares_issued   = Column(Float)             # 발행주식수

    # ── 업종 ──────────────────────────────────────────────────
    sector_large    = Column(String(50))        # 업종(대분류)
    sector_mid      = Column(String(50))        # 업종(중분류)
    sector_small    = Column(String(100))       # 업종(소분류)

    # ── 재무 요약 (연간 최근치 — KRX MDCSTAT03901/03501 + 엑셀) ──
    per             = Column(Float)             # PER  (KRX MDCSTAT03901 갱신)
    pbr             = Column(Float)             # PBR  (KRX MDCSTAT03901 신규)
    eps             = Column(Float)             # EPS  (KRX MDCSTAT03501 신규)
    bps             = Column(Float)             # BPS  (KRX MDCSTAT03501 신규)
    total_assets    = Column(Float)             # 자산총계 (억원, 엑셀)
    revenue         = Column(Float)             # 매출액 (억원, KRX 갱신)
    operating_profit= Column(Float)             # 영업이익 (백만원, KRX 갱신)
    revenue_growth  = Column(Float)             # 매출액증가율 (%, 엑셀)
    dps             = Column(Float)             # 보통주DPS (엑셀)
    roa             = Column(Float)             # ROA (%, KRX MDCSTAT03501 갱신)
    roe             = Column(Float)             # ROE (%, KRX MDCSTAT03501 갱신)

    # ── 메타 ──────────────────────────────────────────────────
    source          = Column(String(20), default="excel")   # "excel" | "krx"
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # (종목코드, 기준일) 조합 unique — 일별 이력 저장 가능
        UniqueConstraint("stock_code", "base_date", name="uq_universe_code_date"),
        Index("idx_universe_market",    "market"),
        Index("idx_universe_sector",    "sector_large"),
        Index("idx_universe_mktcap",    "market_cap"),
        Index("idx_universe_date_code", "base_date", "stock_code"),
    )


# ═══════════════════════════════════════════════════════════════
#  테이블 생성
# ═══════════════════════════════════════════════════════════════

def init_tables():
    """stock_universe 테이블 생성 (없을 때만)."""
    Base.metadata.create_all(bind=engine)
    logger.info("stock_universe 테이블 준비 완료")


def _migrate_add_columns():
    """
    기존 stock_universe 테이블에 신규 컬럼 자동 추가 (마이그레이션).
    SQLite는 ALTER TABLE ADD COLUMN만 지원 — 없는 컬럼만 추가.
    추가 대상: pbr, eps, bps  (KRX 투자지표/재무정보 신규 수집 컬럼)
    """
    new_cols = [
        ("pbr",  "REAL"),
        ("eps",  "REAL"),
        ("bps",  "REAL"),
    ]
    with engine.connect() as conn:
        # 현재 컬럼 목록 조회
        result = conn.execute(text("PRAGMA table_info(stock_universe)"))
        existing = {row[1] for row in result}   # row[1] = 컬럼명

        for col_name, col_type in new_cols:
            if col_name not in existing:
                conn.execute(text(
                    f"ALTER TABLE stock_universe ADD COLUMN {col_name} {col_type}"
                ))
                conn.commit()
                logger.info(f"[마이그레이션] stock_universe.{col_name} 컬럼 추가 완료")
            else:
                logger.debug(f"[마이그레이션] {col_name} 이미 존재 — 스킵")


# ═══════════════════════════════════════════════════════════════
#  엑셀 초기 적재
# ═══════════════════════════════════════════════════════════════

def _parse_listed_date(val) -> date | None:
    """상장일 YYYYMMDD int/str → date 변환."""
    try:
        s = str(int(val))
        if len(s) == 8:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        pass
    return None


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except Exception:
        return None


def import_from_excel(excel_path: str, base_date_override: date | None = None) -> int:
    """
    엑셀 파일 → stock_universe UPSERT.

    Parameters
    ----------
    excel_path        : 엑셀 파일 경로
    base_date_override: 기준일 강제 지정 (None이면 오늘 날짜)

    Returns
    -------
    int : 처리된 행 수
    """
    init_tables()
    _migrate_add_columns()   # pbr/eps/bps 컬럼 없으면 자동 추가
    base_date = base_date_override or date.today()

    logger.info(f"엑셀 적재 시작: {excel_path}  기준일={base_date}")
    df = pd.read_excel(excel_path, dtype={"종목코드": str})
    logger.info(f"엑셀 행 수: {len(df)}")

    db: Session = SessionLocal()
    upserted = 0

    try:
        for _, row in df.iterrows():
            code = str(row.get("종목코드", "")).strip().zfill(6)
            name = str(row.get("종목명", "")).strip()
            if not code or not name:
                continue

            # 기존 레코드 조회 (종목코드 + 기준일)
            obj = db.query(StockUniverse).filter(
                StockUniverse.stock_code == code,
                StockUniverse.base_date  == base_date,
            ).first()

            if obj is None:
                obj = StockUniverse(stock_code=code, base_date=base_date)
                db.add(obj)

            obj.stock_name      = name
            obj.market          = str(row.get("시장구분", "") or "")
            obj.stock_type      = str(row.get("주식종류", "") or "보통주")
            obj.close           = _safe_float(row.get("종가"))
            obj.open            = _safe_float(row.get("시가"))
            obj.high            = _safe_float(row.get("고가"))
            obj.low             = _safe_float(row.get("저가"))
            obj.change_rate     = _safe_float(row.get("등락률"))
            obj.volume          = _safe_float(row.get("거래량"))
            obj.trading_value   = _safe_float(row.get("거래대금(백만)"))
            obj.market_cap      = _safe_float(row.get("시가총액(억)"))
            obj.settlement_month= int(row["결산월"]) if _safe_float(row.get("결산월")) else None
            obj.face_value      = _safe_float(row.get("액면가"))
            obj.shares_issued   = _safe_float(row.get("발행주식수"))
            obj.sector_large    = str(row.get("업종(대분류)", "") or "")
            obj.sector_mid      = str(row.get("업종(중분류)", "") or "")
            obj.sector_small    = str(row.get("업종(소분류)", "") or "")
            obj.per             = _safe_float(row.get("PER"))
            obj.total_assets    = _safe_float(row.get("자산총계(억)"))
            obj.revenue         = _safe_float(row.get("매출액(억)"))
            obj.operating_profit= _safe_float(row.get("영업이익(백만)"))
            obj.revenue_growth  = _safe_float(row.get("매출액증가율"))
            obj.dps             = _safe_float(row.get("보통주DPS"))
            obj.roa             = _safe_float(row.get("ROA"))
            obj.roe             = _safe_float(row.get("ROE"))
            obj.listed_date     = _parse_listed_date(row.get("상장일"))
            obj.source          = "excel"

            upserted += 1

        db.commit()
        logger.info(f"엑셀 적재 완료: {upserted}건 (기준일={base_date})")

    except Exception as e:
        db.rollback()
        logger.error(f"엑셀 적재 실패: {e}")
        raise
    finally:
        db.close()

    return upserted


# ═══════════════════════════════════════════════════════════════
#  KRX OpenAPI 업데이트
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  데이터 수집 — 네이버 금융 스크래핑 (KRX/pykrx/FDR 차단 대응)
# ═══════════════════════════════════════════════════════════════
#
#  KRX data.krx.co.kr 점검/차단 시 → 네이버 금융 전종목 시세 스크래핑
#  URL: https://finance.naver.com/sise/sise_market_sum.naver
#  제공: 종목명, 현재가, 등락률, 거래량, 시가총액, PER, ROE, 거래대금
#
#  우선순위:
#    1순위: FinanceDataReader (KRX 정상 시)
#    2순위: 네이버 금융 스크래핑 (KRX 점검/차단 시 자동 fallback)


import requests as _req
from bs4 import BeautifulSoup as _BS

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Encoding": "gzip, deflate",
}


def _find_latest_trading_day() -> str:
    """가장 최근 거래일 (YYYYMMDD). 자정 기준 전날, 주말 조정."""
    from datetime import timedelta
    today = date.today()
    now   = datetime.now()
    if now.hour < 16:
        today -= timedelta(days=1)
    wd = today.weekday()
    if wd == 6:   today -= timedelta(days=2)
    elif wd == 5: today -= timedelta(days=1)
    return today.strftime("%Y%m%d")


def _fval(v) -> float | None:
    """문자열/숫자 → float. 변환 불가 시 None."""
    if v is None: return None
    try:
        import math
        f = float(str(v).replace(",", "").replace("%", "").replace("+", "").strip())
        return None if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return None


def _scrape_naver_sise(sosok: int) -> list[dict]:
    """
    네이버 금융 전종목 시세 스크래핑.
    sosok=0: KOSPI, sosok=1: KOSDAQ

    반환: [{code, name, close, change_rate, volume, market_cap,
             per, roe, trading_value}, ...]
    """
    base_url = (
        f"https://finance.naver.com/sise/sise_market_sum.naver"
        f"?sosok={sosok}&page="
    )
    market_name = "KOSPI" if sosok == 0 else "KOSDAQ"
    rows = []

    # 1페이지로 총 페이지 수 확인
    try:
        resp = _req.get(base_url + "1", headers=_NAVER_HEADERS, timeout=15)
        resp.encoding = "euc-kr"
        soup = _BS(resp.text, "html.parser")

        # 총 페이지 수
        pager = soup.select("td.pgRR a")
        last_page = 1
        if pager:
            import re
            m = re.search(r"page=(\d+)", pager[0].get("href", ""))
            if m:
                last_page = int(m.group(1))

        logger.info(f"[Naver] {market_name}: 총 {last_page}페이지")

    except Exception as e:
        logger.error(f"[Naver] {market_name} 1페이지 접근 실패: {e}")
        return []

    # 전체 페이지 순회
    for page in range(1, last_page + 1):
        try:
            resp = _req.get(base_url + str(page), headers=_NAVER_HEADERS, timeout=15)
            resp.encoding = "euc-kr"
            soup = _BS(resp.text, "html.parser")

            # 종목 테이블 파싱
            table = soup.select_one("table.type_2")
            if not table:
                continue

            for tr in table.select("tr"):
                tds = tr.select("td")
                if len(tds) < 10:
                    continue

                # 종목코드: 링크에서 추출
                a_tag = tds[1].select_one("a")
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                import re
                code_m = re.search(r"code=(\d{6})", href)
                if not code_m:
                    continue
                code = code_m.group(1)
                name = a_tag.get_text(strip=True)

                def td_text(i):
                    if i >= len(tds): return ""
                    return tds[i].get_text(strip=True).replace(",", "").replace("%", "").replace("+", "")

                # 네이버 sise_market_sum 실제 컬럼 (삼성전자 실측 확정):
                # td[0]=순위  td[1]=종목명  td[2]=현재가  td[3]=전일비
                # td[4]=등락률  td[5]=액면가  td[6]=거래량
                # td[7]=거래대금(백만원)  td[8]=외국인비율(%)
                # td[9]=외국인보유주식수(백주)  td[10]=PER  td[11]=ROE(%)
                # ※ 시가총액은 이 페이지에 없음 → 엑셀 적재값 유지
                rows.append({
                    "code":          code,
                    "name":          name,
                    "close":         _fval(td_text(2)),   # 현재가(원)
                    "change_rate":   _fval(td_text(4)),   # 등락률(%)
                    "volume":        _fval(td_text(6)),   # 거래량(주)
                    "trading_value": _fval(td_text(7)),   # 거래대금(백만원)
                    "per":           _fval(td_text(10)),  # PER
                    "roe":           _fval(td_text(11)),  # ROE(%)
                })

            time.sleep(0.12)   # 네이버 요청 간격

        except Exception as e:
            logger.warning(f"[Naver] {market_name} {page}페이지 실패: {e}")
            continue

    logger.info(f"[Naver] {market_name}: {len(rows)}종목 수집 완료")
    return rows


def _fetch_fdr() -> pd.DataFrame:
    """FinanceDataReader 시도 (KRX 정상 시 1순위)."""
    try:
        import FinanceDataReader as fdr
        parts = []
        for mkt, label in [("KOSPI","유가증권"), ("KOSDAQ","코스닥")]:
            df = fdr.StockListing(mkt)
            if df is None or df.empty:
                return pd.DataFrame()
            # 종목코드
            for c in ("Code","Symbol","종목코드"):
                if c in df.columns:
                    df = df.rename(columns={c:"_code"}); break
            df["_code"] = df["_code"].astype(str).str.zfill(6)
            for c in ("Name","종목명"):
                if c in df.columns:
                    df = df.rename(columns={c:"_name"}); break
            df["_market"] = label
            col_map = {
                "Close":"close","Open":"open","High":"high","Low":"low",
                "Volume":"volume","Amount":"trading_value","Marcap":"market_cap",
                "Stocks":"shares_issued","ChagesRatio":"change_rate",
                "PER":"per","PBR":"pbr","EPS":"eps","BPS":"bps",
                "DPS":"dps","ROE":"roe","ROA":"roa",
            }
            df = df.rename(columns={k:v for k,v in col_map.items() if k in df.columns})
            if "market_cap"    in df.columns: df["market_cap"]    = pd.to_numeric(df["market_cap"],    errors="coerce").div(1e8).round(1)
            if "trading_value" in df.columns: df["trading_value"] = pd.to_numeric(df["trading_value"], errors="coerce").div(1e6).round(1)
            parts.append(df)
            time.sleep(0.3)
        if parts:
            result = pd.concat(parts, ignore_index=True)
            logger.info(f"[FDR] 수집 완료: {len(result)}종목")
            return result
    except Exception as e:
        logger.warning(f"[FDR] 실패 (KRX 점검 중?): {e}")
    return pd.DataFrame()


def fetch_krx_universe(trade_date: str | None = None) -> pd.DataFrame:
    """
    전종목 데이터 수집.

    1순위: FinanceDataReader (KRX 정상 시)
    2순위: 네이버 금융 스크래핑 (KRX 점검/차단 시 자동 fallback)
    """
    logger.info("━━━ 전종목 수집 시작 (FDR → Naver fallback) ━━━")

    # 1순위: FDR 시도
    df = _fetch_fdr()
    if not df.empty:
        return df

    # 2순위: 네이버 금융 스크래핑
    logger.info("[Naver] FDR 실패 → 네이버 금융으로 fallback")
    parts = []
    for sosok, label in [(0, "유가증권"), (1, "코스닥")]:
        rows = _scrape_naver_sise(sosok)
        if rows:
            ndf = pd.DataFrame(rows)
            ndf = ndf.rename(columns={"code":"_code","name":"_name"})
            ndf["_market"] = label
            # 네이버는 시가총액=억원, 거래대금=백만원 단위 (이미 올바름)
            parts.append(ndf)

    if not parts:
        logger.error("네이버 스크래핑도 실패")
        return pd.DataFrame()

    result = pd.concat(parts, ignore_index=True)
    logger.info(f"━━━ Naver 수집 완료: {len(result)}종목 ━━━")
    return result


def update_from_krx(trade_date: str | None = None) -> int:
    """
    전종목 데이터 → stock_universe UPSERT.
    (FDR 또는 네이버 자동 선택)
    """
    init_tables()
    _migrate_add_columns()

    td_str  = trade_date or _find_latest_trading_day()
    td_date = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))

    merged = fetch_krx_universe(td_str)
    if merged.empty:
        logger.error("데이터 없음 — 업데이트 중단")
        return 0

    db: Session = SessionLocal()
    upserted = 0

    try:
        for _, row in merged.iterrows():
            code = str(row.get("_code", "")).strip().zfill(6)
            if not code or code == "000000":
                continue

            obj = db.query(StockUniverse).filter(
                StockUniverse.stock_code == code,
                StockUniverse.base_date  == td_date,
            ).first()
            if obj is None:
                obj = StockUniverse(stock_code=code, base_date=td_date)
                db.add(obj)

            name = str(row.get("_name","") or "").strip()
            if name: obj.stock_name = name
            mkt = str(row.get("_market","") or "").strip()
            if mkt: obj.market = mkt

            # 시세 (None이면 기존값 유지 — 네이버는 시총/시가/고가/저가 없음)
            def _set(attr, key):
                v = _fval(row.get(key))
                if v is not None:
                    setattr(obj, attr, v)

            _set("close",         "close")
            _set("open",          "open")
            _set("high",          "high")
            _set("low",           "low")
            _set("volume",        "volume")
            _set("trading_value", "trading_value")
            _set("market_cap",    "market_cap")    # FDR만 제공, 네이버는 None → 기존값 유지
            _set("shares_issued", "shares_issued")
            _set("change_rate",   "change_rate")

            # 투자지표 + 재무 (None이면 기존값 유지)
            for field in ("per","pbr","eps","bps","dps","roe","roa"):
                v = _fval(row.get(field))
                if v is not None:
                    setattr(obj, field, v)

            # 소스 표시
            obj.source = "fdr" if row.get("pbr") is not None else "naver"
            upserted += 1

        db.commit()
        logger.info(f"업데이트 완료: {upserted}건 (기준일={td_date})")

    except Exception as e:
        db.rollback()
        logger.error(f"업데이트 실패: {e}")
        raise
    finally:
        db.close()

    return upserted


# ═══════════════════════════════════════════════════════════════
#  자정 스케줄러
# ═══════════════════════════════════════════════════════════════

def _seconds_until_midnight() -> float:
    """다음 자정(00:00)까지 남은 초."""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    next_midnight = midnight + timedelta(days=1)
    return (next_midnight - now).total_seconds()


def run_scheduler():
    """
    매일 자정 KRX 3개 API 전종목 업데이트.
      - MDCSTAT01501: 시세 (종가/거래량/시총 등)
      - MDCSTAT03901: 투자지표 (PER/PBR)
      - MDCSTAT03501: 재무정보 (ROE/ROA/매출/영업이익/EPS/BPS)
    실행: python3 stock_universe.py --schedule
    """
    from datetime import timedelta
    init_tables()
    _migrate_add_columns()   # 신규 컬럼 자동 추가
    logger.info("★ 자정 스케줄러 시작 (Ctrl+C로 종료)")
    logger.info("  수집 항목: 시세 + PER/PBR + ROE/ROA/매출/영업이익/EPS/BPS")
    while True:
        wait = _seconds_until_midnight()
        next_run = datetime.now() + timedelta(seconds=wait)
        logger.info(
            f"다음 실행: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
            f"  (대기 {wait/3600:.1f}시간)"
        )
        time.sleep(wait + 5)   # 자정 +5초 여유

        logger.info("━━━ 자정 KRX 전종목 업데이트 시작 (3개 API) ━━━")
        t0 = time.time()
        try:
            n = update_from_krx()
            elapsed = time.time() - t0
            logger.info(
                f"━━━ 자정 업데이트 완료: {n}건  "
                f"소요 {elapsed:.0f}초 ━━━"
            )
        except Exception as e:
            logger.error(f"자정 업데이트 실패: {e}")


# ═══════════════════════════════════════════════════════════════
#  FastAPI 라우터 (main.py에서 include_router로 등록)
# ═══════════════════════════════════════════════════════════════

def get_universe_router():
    """
    /api/universe/* 라우터 반환.
    main.py에서: app.include_router(get_universe_router())
    """
    from fastapi import APIRouter, Query
    from sqlalchemy.orm import Session as _Session
    try:
        from database import get_db
    except ImportError:
        def get_db():
            db = SessionLocal()
            try: yield db
            finally: db.close()
    from fastapi import Depends

    router = APIRouter(prefix="/api/universe", tags=["universe"])

    @router.get("")
    def list_universe(
        market:  str | None = Query(None, description="유가증권 | 코스닥"),
        sector:  str | None = Query(None, description="업종(대분류)"),
        min_cap: float | None = Query(None, description="최소 시가총액(억)"),
        max_cap: float | None = Query(None, description="최대 시가총액(억)"),
        limit:   int = Query(200, le=2000),
        offset:  int = Query(0),
        db: _Session = Depends(get_db),
    ):
        """전종목 목록 조회 (최신 기준일 기준)."""
        # 최신 기준일 찾기
        latest = db.query(StockUniverse.base_date)\
            .order_by(StockUniverse.base_date.desc()).first()
        if not latest:
            return {"total": 0, "items": [], "base_date": None}
        base_date = latest[0]

        q = db.query(StockUniverse).filter(StockUniverse.base_date == base_date)
        if market:  q = q.filter(StockUniverse.market == market)
        if sector:  q = q.filter(StockUniverse.sector_large == sector)
        if min_cap: q = q.filter(StockUniverse.market_cap >= min_cap)
        if max_cap: q = q.filter(StockUniverse.market_cap <= max_cap)

        total = q.count()
        items = q.order_by(StockUniverse.market_cap.desc().nullslast())\
                 .offset(offset).limit(limit).all()

        return {
            "total":     total,
            "base_date": str(base_date),
            "items": [_serialize(i) for i in items],
        }

    @router.get("/search")
    def search_universe(
        q:  str = Query(..., min_length=1, description="종목명 또는 종목코드"),
        db: _Session = Depends(get_db),
    ):
        """종목명/코드 검색."""
        latest = db.query(StockUniverse.base_date)\
            .order_by(StockUniverse.base_date.desc()).first()
        if not latest:
            return []
        base_date = latest[0]

        items = db.query(StockUniverse).filter(
            StockUniverse.base_date == base_date,
            (StockUniverse.stock_name.contains(q)) |
            (StockUniverse.stock_code.contains(q))
        ).order_by(StockUniverse.market_cap.desc().nullslast()).limit(50).all()
        return [_serialize(i) for i in items]

    @router.get("/sectors")
    def list_sectors(db: _Session = Depends(get_db)):
        """업종 목록 (대분류 기준)."""
        latest = db.query(StockUniverse.base_date)\
            .order_by(StockUniverse.base_date.desc()).first()
        if not latest:
            return []
        base_date = latest[0]

        from sqlalchemy import func as sqlfunc
        rows = db.query(
            StockUniverse.sector_large,
            sqlfunc.count(StockUniverse.id).label("count"),
            sqlfunc.sum(StockUniverse.market_cap).label("total_cap"),
        ).filter(
            StockUniverse.base_date == base_date,
            StockUniverse.sector_large != "",
            StockUniverse.sector_large.isnot(None),
        ).group_by(StockUniverse.sector_large)\
         .order_by(sqlfunc.sum(StockUniverse.market_cap).desc().nullslast()).all()

        return [{"sector": r[0], "count": r[1], "total_cap": round(r[2] or 0, 1)} for r in rows]

    @router.get("/stats")
    def universe_stats(db: _Session = Depends(get_db)):
        """전체 통계 (종목수, 시총 합계, 최신 기준일)."""
        from sqlalchemy import func as sqlfunc
        latest = db.query(StockUniverse.base_date)\
            .order_by(StockUniverse.base_date.desc()).first()
        if not latest:
            return {}
        base_date = latest[0]

        total  = db.query(StockUniverse).filter(StockUniverse.base_date == base_date).count()
        kospi  = db.query(StockUniverse).filter(
            StockUniverse.base_date == base_date, StockUniverse.market == "유가증권"
        ).count()
        kosdaq = db.query(StockUniverse).filter(
            StockUniverse.base_date == base_date, StockUniverse.market == "코스닥"
        ).count()
        total_cap = db.query(sqlfunc.sum(StockUniverse.market_cap)).filter(
            StockUniverse.base_date == base_date
        ).scalar()
        history_days = db.query(StockUniverse.base_date)\
            .distinct().count()

        return {
            "base_date":    str(base_date),
            "total":        total,
            "kospi":        kospi,
            "kosdaq":       kosdaq,
            "total_cap_억": round(float(total_cap or 0), 0),
            "history_days": history_days,
        }

    @router.post("/update/krx")
    def trigger_krx_update(trade_date: str | None = None):
        """KRX 업데이트 수동 트리거 (관리자용)."""
        import threading
        def _do():
            try:
                update_from_krx(trade_date)
            except Exception as e:
                logger.error(f"수동 KRX 업데이트 실패: {e}")
        threading.Thread(target=_do, daemon=True).start()
        return {"status": "triggered", "trade_date": trade_date or "auto"}

    return router


def _serialize(obj: StockUniverse) -> dict:
    return {
        "stock_code":       obj.stock_code,
        "stock_name":       obj.stock_name,
        "market":           obj.market,
        "stock_type":       obj.stock_type,
        "base_date":        str(obj.base_date) if obj.base_date else None,
        "close":            obj.close,
        "open":             obj.open,
        "high":             obj.high,
        "low":              obj.low,
        "change_rate":      obj.change_rate,
        "volume":           obj.volume,
        "trading_value":    obj.trading_value,
        "market_cap":       obj.market_cap,
        "listed_date":      str(obj.listed_date) if obj.listed_date else None,
        "settlement_month": obj.settlement_month,
        "face_value":       obj.face_value,
        "shares_issued":    obj.shares_issued,
        "sector_large":     obj.sector_large,
        "sector_mid":       obj.sector_mid,
        "sector_small":     obj.sector_small,
        "per":              obj.per,
        "pbr":              obj.pbr,
        "eps":              obj.eps,
        "bps":              obj.bps,
        "total_assets":     obj.total_assets,
        "revenue":          obj.revenue,
        "operating_profit": obj.operating_profit,
        "revenue_growth":   obj.revenue_growth,
        "dps":              obj.dps,
        "roa":              obj.roa,
        "roe":              obj.roe,
        "source":           obj.source,
    }


# ═══════════════════════════════════════════════════════════════
#  CLI 진입점
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="stock_universe 관리 CLI")
    parser.add_argument("--init-excel",  metavar="EXCEL_PATH",
                        help="엑셀 파일 최초 적재  예) --init-excel ./주식종목전체검색.xlsx")
    parser.add_argument("--update-krx",  action="store_true",
                        help="KRX API 1회 업데이트 (시세+PER/PBR+ROE/ROA)")
    parser.add_argument("--trade-date",  metavar="YYYYMMDD",
                        help="KRX 기준일 지정 (기본: 최근 거래일)")
    parser.add_argument("--schedule",    action="store_true",
                        help="자정 스케줄러 시작")
    parser.add_argument("--stats",       action="store_true",
                        help="DB 통계 출력")
    parser.add_argument("--debug-krx",   action="store_true",
                        help="KRX API 원문 응답 저장 (krx_debug_*.csv)")
    args = parser.parse_args()

    if args.debug_krx:
        print("\n=== 데이터 소스 테스트 ===")
        td = args.trade_date or _find_latest_trading_day()

        # 1. FDR 테스트
        print("\n[1/2] FinanceDataReader 테스트...")
        try:
            import FinanceDataReader as fdr
            df = fdr.StockListing("KOSPI")
            if df is not None and not df.empty:
                print(f"  ✅ FDR 성공: {len(df)}종목  컬럼={list(df.columns)}")
                print(df.head(3).to_string())
            else:
                print("  ❌ FDR: 데이터 없음 (KRX 점검 중)")
        except Exception as e:
            print(f"  ❌ FDR 실패: {e}")

        # 2. 네이버 테스트
        print("\n[2/2] 네이버 금융 스크래핑 테스트 (1페이지만)...")
        try:
            import requests, re
            from bs4 import BeautifulSoup
            resp = requests.get(
                "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1",
                headers=_NAVER_HEADERS, timeout=15
            )
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.type_2 tr")
            data_rows = [r for r in rows if r.select("td a[href*='code=']")]
            print(f"  {'✅' if data_rows else '❌'} 네이버: 1페이지 {len(data_rows)}종목 파싱")
            if data_rows:
                for tr in data_rows[:3]:
                    tds = tr.select("td")
                    a = tds[1].select_one("a")
                    code_m = re.search(r"code=(\d{6})", a.get("href",""))
                    if code_m:
                        print(f"    {code_m.group(1)} {a.get_text(strip=True)} | {[td.get_text(strip=True) for td in tds[2:8]]}")
            # 총 페이지
            pg = soup.select("td.pgRR a")
            if pg:
                pm = re.search(r"page=(\d+)", pg[0].get("href",""))
                if pm: print(f"  총 페이지: {pm.group(1)}페이지")
        except Exception as e:
            import traceback; traceback.print_exc()


    elif args.init_excel:
        n = import_from_excel(args.init_excel)
        print(f"✅ 엑셀 적재 완료: {n}건")

    elif args.update_krx:
        n = update_from_krx(args.trade_date)
        print(f"✅ KRX 업데이트 완료: {n}건")

    elif args.schedule:
        run_scheduler()

    elif args.stats:
        init_tables()
        db = SessionLocal()
        try:
            from sqlalchemy import func as sqlfunc
            latest = db.query(StockUniverse.base_date)\
                .order_by(StockUniverse.base_date.desc()).first()
            if not latest:
                print("데이터 없음")
            else:
                bd    = latest[0]
                total = db.query(StockUniverse).filter(StockUniverse.base_date == bd).count()
                kospi = db.query(StockUniverse).filter(
                    StockUniverse.base_date == bd, StockUniverse.market == "유가증권"
                ).count()
                kosdaq = total - kospi
                cap   = db.query(sqlfunc.sum(StockUniverse.market_cap)).filter(
                    StockUniverse.base_date == bd
                ).scalar()
                hist  = db.query(StockUniverse.base_date).distinct().count()
                print(f"최신 기준일 : {bd}")
                print(f"전체 종목수 : {total}개 (KOSPI {kospi} / KOSDAQ {kosdaq})")
                print(f"전체 시총   : {cap/10000:.0f}조원" if cap else "전체 시총: -")
                print(f"이력 보유일수: {hist}일")
        finally:
            db.close()

    else:
        parser.print_help()
