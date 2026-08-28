"""
public_data_collector.py — 공공데이터포털 + DART 일괄 수집기

═══════════════════════════════════════════════════════════════
수집 데이터 목록
═══════════════════════════════════════════════════════════════

[공공데이터포털 — 금융위원회]
  1. 주식시세정보       → stock_price_daily     (일별 OHLCV + 시총)
  2. 대차거래정보       → short_sell_daily      (대차잔고, 공매도)
  3. 투자자별매매동향   → investor_trading_daily (개인/기관/외국인)
  4. 외국인보유현황     → foreign_holding_daily  (보유비율, 한도소진)
  5. 상장회사기본정보   → listed_company_info    (업종, 상장주수)

[DART 일괄 다운로드]
  6. 전종목 재무제표   → financial_data         (매출/영업이익/순이익 등)
     - 44회 호출로 11년치 전체 완성 (기존 수만 회 대비 99% 절감)

═══════════════════════════════════════════════════════════════
실행 방법
═══════════════════════════════════════════════════════════════

  cd /Applications/stock_dashboard && source venv/bin/activate

  # 오늘 데이터 전체 수집
  python3 public_data_collector.py

  # 특정 날짜
  python3 public_data_collector.py --date 20260327

  # DART 재무만 (전종목 일괄)
  python3 public_data_collector.py --dart-only

  # 시세만
  python3 public_data_collector.py --price-only

  # crontab 등록 (매일 18:30 자동 실행)
  python3 public_data_collector.py --setup-cron

  # 과거 데이터 백필
  python3 public_data_collector.py --backfill --start 20250101 --end 20260327
"""

import sys, os, sqlite3, time, logging, argparse, requests, json, zipfile, io
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/Applications/stock_dashboard")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/Applications/stock_dashboard/public_data.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

DB_PATH = Path("/Applications/stock_dashboard/stock.db")

# ── API 설정 ──────────────────────────────────────────────────────
BASE_URL    = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService"
KRX_BASE    = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService"
CG_BASE     = "https://apis.data.go.kr/1160100/service/GetCGDiscInfoService"  # 기업지배구조 공시
DART_BASE   = "https://opendart.fss.or.kr/api"
DIVI_BASE   = "https://apis.data.go.kr/1160100/service/GetStocDiviInfoService"       # 주식배당정보
LEND_BASE   = "https://apis.data.go.kr/1160100/service/GetStocLendBorrInfoService"    # 주식대차정보
TRAD_BASE   = "https://apis.data.go.kr/1160100/service/GetStocTradInfoService"        # 주식분포/사고주권
RIGH_BASE   = "https://apis.data.go.kr/1160100/service/GetStocRighScheService"        # 주식권리일정
SEIBRO_BASE = "http://api.seibro.or.kr/openapi/service/CorpSvc"                       # 한국예탁결제원


def _get_keys():
    try:
        import config
        pub_key  = getattr(config, "PUBLIC_DATA_API_KEY", os.getenv("PUBLIC_DATA_API_KEY", ""))
        dart_key = getattr(config, "DART_API_KEY", os.getenv("DART_API_KEY", ""))
        return pub_key, dart_key
    except Exception:
        return os.getenv("PUBLIC_DATA_API_KEY", ""), os.getenv("DART_API_KEY", "")


# ══════════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════════
def init_db(conn):
    """필요한 테이블 생성."""

    conn.executescript("""
    -- 1. 일별 주식시세 (공공데이터포털)
    CREATE TABLE IF NOT EXISTS stock_price_daily (
        id          INTEGER PRIMARY KEY,
        bas_dt      TEXT NOT NULL,        -- 기준일자 YYYYMMDD
        stock_code  TEXT NOT NULL,        -- 종목코드
        stock_name  TEXT,                 -- 종목명
        market      TEXT,                 -- 시장구분 (KOSPI/KOSDAQ)
        open_price  REAL,                 -- 시가
        high_price  REAL,                 -- 고가
        low_price   REAL,                 -- 저가
        close_price REAL,                 -- 종가
        vs          REAL,                 -- 대비
        change_pct  REAL,                 -- 등락률
        volume      REAL,                 -- 거래량
        trade_amt   REAL,                 -- 거래대금
        market_cap  REAL,                 -- 시가총액
        shares      REAL,                 -- 상장주수
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_spd_date  ON stock_price_daily(bas_dt);
    CREATE INDEX IF NOT EXISTS idx_spd_code  ON stock_price_daily(stock_code);

    -- 2. 대차거래 / 공매도
    CREATE TABLE IF NOT EXISTS short_sell_daily (
        id              INTEGER PRIMARY KEY,
        bas_dt          TEXT NOT NULL,
        stock_code      TEXT NOT NULL,
        stock_name      TEXT,
        short_qty       REAL,   -- 공매도수량
        short_amt       REAL,   -- 공매도금액
        borrow_bal_qty  REAL,   -- 대차잔고주수
        borrow_bal_amt  REAL,   -- 대차잔고금액
        borrow_bal_pct  REAL,   -- 대차잔고비율
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_ssd_date ON short_sell_daily(bas_dt);
    CREATE INDEX IF NOT EXISTS idx_ssd_code ON short_sell_daily(stock_code);

    -- 3. 투자자별 매매동향
    CREATE TABLE IF NOT EXISTS investor_trading_daily (
        id              INTEGER PRIMARY KEY,
        bas_dt          TEXT NOT NULL,
        stock_code      TEXT NOT NULL,
        stock_name      TEXT,
        indv_buy        REAL,   -- 개인 매수
        indv_sell       REAL,   -- 개인 매도
        indv_net        REAL,   -- 개인 순매수
        inst_buy        REAL,   -- 기관 매수
        inst_sell       REAL,   -- 기관 매도
        inst_net        REAL,   -- 기관 순매수
        frgn_buy        REAL,   -- 외국인 매수
        frgn_sell       REAL,   -- 외국인 매도
        frgn_net        REAL,   -- 외국인 순매수
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_itd_date ON investor_trading_daily(bas_dt);
    CREATE INDEX IF NOT EXISTS idx_itd_code ON investor_trading_daily(stock_code);

    -- 4. 외국인 보유현황
    CREATE TABLE IF NOT EXISTS foreign_holding_daily (
        id              INTEGER PRIMARY KEY,
        bas_dt          TEXT NOT NULL,
        stock_code      TEXT NOT NULL,
        stock_name      TEXT,
        frgn_hold_qty   REAL,   -- 외국인 보유주수
        frgn_hold_pct   REAL,   -- 외국인 보유비율
        frgn_limit_pct  REAL,   -- 외국인 한도소진율
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_fhd_date ON foreign_holding_daily(bas_dt);
    CREATE INDEX IF NOT EXISTS idx_fhd_code ON foreign_holding_daily(stock_code);

    -- 5. 상장회사 기본정보
    CREATE TABLE IF NOT EXISTS listed_company_info (
        id          INTEGER PRIMARY KEY,
        bas_dt      TEXT NOT NULL,
        stock_code  TEXT NOT NULL,
        stock_name  TEXT,
        market      TEXT,       -- 시장구분
        sector      TEXT,       -- 업종
        listing_dt  TEXT,       -- 상장일
        shares      REAL,       -- 상장주수
        face_val    REAL,       -- 액면가
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_lci_code ON listed_company_info(stock_code);

    -- 6. 기업지배구조 공시정보
    CREATE TABLE IF NOT EXISTS corp_governance (
        id          INTEGER PRIMARY KEY,
        biz_year    TEXT NOT NULL,
        stock_code  TEXT,
        corp_name   TEXT,
        data_type   TEXT,
        value1      TEXT,
        value2      TEXT,
        value3      TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(biz_year, stock_code, data_type)
    );
    CREATE INDEX IF NOT EXISTS idx_cg_year  ON corp_governance(biz_year);
    CREATE INDEX IF NOT EXISTS idx_cg_code  ON corp_governance(stock_code);

    -- 7. 주식배당정보
    CREATE TABLE IF NOT EXISTS stock_dividend (
        id              INTEGER PRIMARY KEY,
        bas_dt          TEXT NOT NULL,     -- 기준일자
        stock_code      TEXT NOT NULL,     -- 종목코드
        stock_name      TEXT,              -- 종목명
        dvdn_bas_dt     TEXT,              -- 배당기준일
        cash_dvdn       REAL,              -- 현금배당금
        stk_dvdn_rt     REAL,              -- 주식배당률
        dvdn_yld        REAL,              -- 배당수익률
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_sd_code ON stock_dividend(stock_code);
    CREATE INDEX IF NOT EXISTS idx_sd_date ON stock_dividend(bas_dt);

    -- 8. 주식대차정보
    CREATE TABLE IF NOT EXISTS stock_lending (
        id              INTEGER PRIMARY KEY,
        bas_dt          TEXT NOT NULL,
        stock_code      TEXT NOT NULL,
        stock_name      TEXT,
        lend_vol        REAL,   -- 대차거래량
        lend_amt        REAL,   -- 대차거래금액
        lend_bal_vol    REAL,   -- 대차잔고주수
        lend_bal_amt    REAL,   -- 대차잔고금액
        lend_bal_rt     REAL,   -- 대차잔고비율
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bas_dt, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_sl_date ON stock_lending(bas_dt);
    CREATE INDEX IF NOT EXISTS idx_sl_code ON stock_lending(stock_code);

    -- 9. 주식권리일정
    CREATE TABLE IF NOT EXISTS stock_rights_schedule (
        id              INTEGER PRIMARY KEY,
        righ_std_dt     TEXT NOT NULL,   -- 권리기준일
        stock_code      TEXT,
        stock_name      TEXT,
        corp_name       TEXT,
        righ_exer_reas  TEXT,            -- 권리행사사유
        exer_dt         TEXT,            -- 행사일
        righ_type       TEXT,            -- 권리종류
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(righ_std_dt, stock_code, righ_type)
    );
    CREATE INDEX IF NOT EXISTS idx_srs_date ON stock_rights_schedule(righ_std_dt);
    CREATE INDEX IF NOT EXISTS idx_srs_code ON stock_rights_schedule(stock_code);

    -- 10. 한국예탁결제원 매수청구일정
    CREATE TABLE IF NOT EXISTS buyreq_schedule (
        id              INTEGER PRIMARY KEY,
        rgt_std_dt      TEXT NOT NULL,   -- 권리기준일자
        issueco_custno  TEXT,            -- 발행회사고객번호
        corp_name       TEXT,            -- 회사명
        stock_code      TEXT,            -- 종목코드
        buyreq_start_dt TEXT,            -- 매수청구시작일
        buyreq_end_dt   TEXT,            -- 매수청구종료일
        buyreq_price    REAL,            -- 매수청구가격
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(rgt_std_dt, issueco_custno)
    );
    CREATE INDEX IF NOT EXISTS idx_brs_date ON buyreq_schedule(rgt_std_dt);
    """)
    conn.commit()
    logger.info("DB 테이블 초기화 완료")


# ══════════════════════════════════════════════════════════════════
# 공공데이터포털 API 공통 호출
# ══════════════════════════════════════════════════════════════════
def _api_call(url: str, params: dict, max_rows: int = 5000) -> list:
    """공공데이터포털 API 호출 (페이징 자동 처리)."""
    pub_key, _ = _get_keys()
    all_items = []
    page = 1

    while True:
        p = {
            "serviceKey": pub_key,
            "numOfRows":  max_rows,
            "pageNo":     page,
            "resultType": "json",
            **params,
        }
        try:
            r = requests.get(url, params=p, timeout=30)
            r.raise_for_status()
            data = r.json()

            # 응답 구조 파싱
            body = data.get("response", {}).get("body", {})
            items = body.get("items", {})
            if not items:
                break

            item_list = items.get("item", [])
            if isinstance(item_list, dict):
                item_list = [item_list]
            if not item_list:
                break

            all_items.extend(item_list)

            total = int(body.get("totalCount", 0))
            if len(all_items) >= total or len(item_list) < max_rows:
                break
            page += 1

        except Exception as e:
            logger.warning(f"API 호출 오류: {url} - {e}")
            break

    return all_items


def _fval(v, default=None):
    """문자열 → float 변환."""
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s and s != "-" else default
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════
# 1. 주식시세정보
# ══════════════════════════════════════════════════════════════════
def collect_stock_price(conn, bas_dt: str) -> int:
    """일별 전종목 주식시세 수집."""
    logger.info(f"[시세] {bas_dt} 수집 시작")
    items = _api_call(
        f"{BASE_URL}/getStockPriceInfo",
        {"basDt": bas_dt, "mrktCls": ""},  # 전체 시장
    )
    if not items:
        logger.warning(f"[시세] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "")).strip().zfill(6)
        if not code or not code.isdigit():
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO stock_price_daily
                (bas_dt, stock_code, stock_name, market,
                 open_price, high_price, low_price, close_price,
                 vs, change_pct, volume, trade_amt, market_cap, shares)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                it.get("mrktCtg", ""),
                _fval(it.get("mkp")),      # 시가
                _fval(it.get("hipr")),     # 고가
                _fval(it.get("lopr")),     # 저가
                _fval(it.get("clpr")),     # 종가
                _fval(it.get("vs")),       # 대비
                _fval(it.get("fltRt")),    # 등락률
                _fval(it.get("trqu")),     # 거래량
                _fval(it.get("trPrc")),    # 거래대금
                _fval(it.get("mktTotAmt")),# 시가총액
                _fval(it.get("lstgStCnt")),# 상장주수
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[시세] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[시세] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 2. 대차거래 / 공매도
# ══════════════════════════════════════════════════════════════════
def collect_short_sell(conn, bas_dt: str) -> int:
    """일별 대차거래/공매도 수집."""
    logger.info(f"[대차] {bas_dt} 수집 시작")
    items = _api_call(
        f"{BASE_URL}/getItemSrtSellInfo",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[대차] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("isinCd", "") or it.get("srtnCd", "")).strip()
        if len(code) > 6:
            code = code[-6:]
        if not code or not code.isdigit():
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO short_sell_daily
                (bas_dt, stock_code, stock_name,
                 short_qty, short_amt, borrow_bal_qty, borrow_bal_amt, borrow_bal_pct)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                _fval(it.get("srtSelVol")),      # 공매도수량
                _fval(it.get("srtSelAmt")),      # 공매도금액
                _fval(it.get("stlnRmndrVol")),   # 대차잔고주수
                _fval(it.get("stlnRmndrAmt")),   # 대차잔고금액
                _fval(it.get("stlnRmndrRate")),  # 대차잔고비율
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[대차] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[대차] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 3. 투자자별 매매동향
# ══════════════════════════════════════════════════════════════════
def collect_investor_trading(conn, bas_dt: str) -> int:
    """일별 투자자별 매매동향 수집."""
    logger.info(f"[투자자] {bas_dt} 수집 시작")
    items = _api_call(
        f"{BASE_URL}/getInvstByTrdrStkInfo",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[투자자] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "")).strip().zfill(6)
        if not code or not code.isdigit():
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO investor_trading_daily
                (bas_dt, stock_code, stock_name,
                 indv_buy, indv_sell, indv_net,
                 inst_buy, inst_sell, inst_net,
                 frgn_buy, frgn_sell, frgn_net)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                _fval(it.get("indvBuyVol")),   # 개인 매수
                _fval(it.get("indvSellVol")),  # 개인 매도
                _fval(it.get("indvNetVol")),   # 개인 순매수
                _fval(it.get("orgBuyVol")),    # 기관 매수
                _fval(it.get("orgSellVol")),   # 기관 매도
                _fval(it.get("orgNetVol")),    # 기관 순매수
                _fval(it.get("frgnBuyVol")),   # 외국인 매수
                _fval(it.get("frgnSellVol")),  # 외국인 매도
                _fval(it.get("frgnNetVol")),   # 외국인 순매수
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[투자자] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[투자자] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 4. 외국인 보유현황
# ══════════════════════════════════════════════════════════════════
def collect_foreign_holding(conn, bas_dt: str) -> int:
    """일별 외국인 보유현황 수집."""
    logger.info(f"[외국인] {bas_dt} 수집 시작")
    items = _api_call(
        f"{BASE_URL}/getFrgnIsttTrdrStkInfo",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[외국인] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "")).strip().zfill(6)
        if not code or not code.isdigit():
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO foreign_holding_daily
                (bas_dt, stock_code, stock_name,
                 frgn_hold_qty, frgn_hold_pct, frgn_limit_pct)
                VALUES (?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                _fval(it.get("frgnHoldQty")),    # 외국인 보유주수
                _fval(it.get("frgnHoldRt")),     # 외국인 보유비율
                _fval(it.get("frgnLmtExhstRt")), # 한도소진율
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[외국인] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[외국인] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 5. 상장회사 기본정보
# ══════════════════════════════════════════════════════════════════
def collect_listed_company(conn, bas_dt: str) -> int:
    """상장회사 기본정보 수집 (업종, 상장주수 등)."""
    logger.info(f"[기업정보] {bas_dt} 수집 시작")
    items = _api_call(
        f"{KRX_BASE}/getItemInfo",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[기업정보] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "")).strip().zfill(6)
        if not code or not code.isdigit():
            continue
        try:
            sector = it.get("induty", "") or it.get("bizr", "")
            conn.execute("""
                INSERT OR REPLACE INTO listed_company_info
                (bas_dt, stock_code, stock_name, market, sector, listing_dt, shares, face_val)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                it.get("mrktCtg", ""),
                sector,
                it.get("lstgDt", ""),
                _fval(it.get("lstgStCnt")),
                _fval(it.get("parval")),
            ))
            # portfolio 테이블 업종도 업데이트
            if sector:
                conn.execute("""
                    UPDATE portfolio SET sector=? WHERE stock_code=?
                    AND (sector IS NULL OR sector='기타' OR sector='')
                """, (sector, code))
            saved += 1
        except Exception as e:
            logger.debug(f"[기업정보] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[기업정보] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 6. DART 재무제표 일괄 다운로드
# ══════════════════════════════════════════════════════════════════
def collect_dart_bulk(conn, year: int, reprt_code: str) -> int:
    """
    DART 전종목 재무제표 일괄 다운로드.
    fnlttSinglAcntAll: 단일회사가 아닌 전종목 일괄
    """
    import pandas as pd

    reprt_names = {"11011": "사업보고서", "11012": "반기보고서",
                   "11013": "1분기", "11014": "3분기"}
    name = reprt_names.get(reprt_code, reprt_code)
    logger.info(f"[DART] {year}년 {name} 일괄 다운로드 시작")

    _, dart_key = _get_keys()
    if not dart_key:
        logger.error("[DART] API 키 없음")
        return 0

    # CFS(연결) 먼저, 없으면 OFS(개별)
    fn_data = None
    for fs_div in ["CFS", "OFS"]:
        try:
            url = f"{DART_BASE}/fnlttSinglAcntAll.json"
            params = {
                "crtfc_key": dart_key,
                "bsns_year":  str(year),
                "reprt_code": reprt_code,
                "fs_div":     fs_div,
            }
            r = requests.get(url, params=params, timeout=60)
            data = r.json()

            if data.get("status") == "000" and data.get("list"):
                fn_data = pd.DataFrame(data["list"])
                logger.info(f"[DART] {year} {name} {fs_div}: {len(fn_data)}행")
                break
            elif data.get("status") == "020":
                logger.warning(f"[DART] API 한도 초과")
                return -1  # 한도 초과 신호
        except Exception as e:
            logger.warning(f"[DART] {year} {name} {fs_div} 오류: {e}")

    if fn_data is None or fn_data.empty:
        logger.warning(f"[DART] {year} {name}: 데이터 없음")
        return 0

    # 재무 항목 매핑
    ACCOUNT_MAP = {
        "매출액": "revenue", "영업수익": "revenue",
        "영업이익": "operating_profit",
        "당기순이익": "net_income",
        "자산총계": "total_assets",
        "부채총계": "total_liabilities",
        "자본총계": "total_equity",
        "자본금": "capital_stock",
        "기본주당순이익": "eps", "주당순이익": "eps",
        "주당순자산": "bps",
        "주당배당금": "dps",
        "현금및현금성자산": "cash",
    }

    # stock_code별 그룹핑
    quarter_map = {"11011": 4, "11012": 2, "11013": 1, "11014": 3}
    qnum = quarter_map.get(reprt_code, 0)
    is_annual = (reprt_code == "11011")

    # 종목별 집계
    grouped = fn_data.groupby("stock_code")
    saved = 0

    for stock_code, grp in grouped:
        if not str(stock_code).strip().isdigit():
            continue

        m = {k: None for k in ["revenue","operating_profit","net_income",
             "total_assets","total_liabilities","total_equity","capital_stock",
             "eps","bps","dps","cash","total_shares","depreciation_amortization"]}

        for _, row in grp.iterrows():
            acc = str(row.get("account_nm", "")).replace(" ", "")
            val_str = row.get("thstrm_amount", "")
            if not acc or not val_str:
                continue
            try:
                val = float(str(val_str).replace(",", ""))
            except Exception:
                continue

            for key, field in ACCOUNT_MAP.items():
                if key in acc and "주당" not in acc or (key in ("기본주당순이익","주당순이익","주당순자산","주당배당금") and key in acc):
                    if "주당" not in key or "주당" in key:
                        m[field] = val
                        break

        try:
            conn.execute("""
                INSERT OR REPLACE INTO financial_data (
                    stock_code, year, quarter, is_annual,
                    revenue, operating_profit, net_income,
                    total_assets, total_liabilities, total_equity, capital_stock,
                    eps, bps, dps, cash, total_shares, depreciation_amortization,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """, (
                str(stock_code).zfill(6), year, qnum, int(is_annual),
                m["revenue"], m["operating_profit"], m["net_income"],
                m["total_assets"], m["total_liabilities"], m["total_equity"],
                m["capital_stock"], m["eps"], m["bps"], m["dps"],
                m["cash"], m["total_shares"], m["depreciation_amortization"],
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[DART] {stock_code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[DART] {year} {name}: {saved}종목 저장")
    return saved


def collect_dart_all_years(conn, start_year: int = 2015) -> dict:
    """
    DART 전년도~현재까지 전체 재무 일괄 수집.
    공시된 분기만 수집 (미래 분기 스킵).
    """
    from datetime import date as _date
    today = _date.today()
    current_year = today.year
    month = today.month

    # 공시 완료된 분기 목록
    available = []
    for y in range(start_year, current_year + 1):
        for rprt, avail_month in [("11013", 5), ("11012", 8), ("11014", 11), ("11011", 4)]:
            # 해당 연도/분기가 공시됐는지 확인
            if y < current_year:
                available.append((y, rprt))
            elif y == current_year and month >= avail_month:
                available.append((y, rprt))

    logger.info(f"[DART] 전체 수집 대상: {len(available)}건 (연도/분기 조합)")
    results = {}
    for y, rprt in available:
        # 이미 있는지 확인
        qmap = {"11011": 4, "11012": 2, "11013": 1, "11014": 3}
        cnt = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE year=? AND quarter=? AND is_annual=?",
            (y, qmap[rprt], int(rprt == "11011"))
        ).fetchone()[0]

        if cnt > 100:  # 100종목 이상 있으면 이미 수집됨
            logger.info(f"[DART] {y}년 {rprt} 이미 {cnt}종목 → 스킵")
            results[f"{y}_{rprt}"] = cnt
            continue

        n = collect_dart_bulk(conn, y, rprt)
        results[f"{y}_{rprt}"] = n
        if n == -1:  # 한도 초과
            logger.warning("[DART] API 한도 초과 — 내일 재시도")
            break
        time.sleep(1.0)

    return results




# ══════════════════════════════════════════════════════════════════
# 6. 기업지배구조 공시정보
# ══════════════════════════════════════════════════════════════════
def collect_corp_governance(conn, biz_year: str) -> dict:
    """기업지배구조 공시정보 수집 (연간)."""
    logger.info(f"[지배구조] {biz_year}년 수집 시작")
    results = {}

    # 소액주주현황
    items = _api_call(f"{CG_BASE}/getCGSmamInfo", {"bizYear": biz_year})
    if items:
        saved = 0
        for it in items:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO corp_governance
                    (biz_year, stock_code, corp_name, data_type, value1, value2, value3, created_at)
                    VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                """, (
                    biz_year,
                    it.get("stockCode", ""),
                    it.get("corpNm", ""),
                    "소액주주현황",
                    it.get("smamStkStkqty", ""),  # 소액주주 주식수
                    it.get("smamStkrt", ""),        # 소액주주 지분율
                    it.get("smamCnt", ""),           # 소액주주 수
                ))
                saved += 1
            except Exception as e:
                logger.debug(f"[지배구조] 저장 오류: {e}")
        conn.commit()
        results["소액주주"] = saved
        logger.info(f"[지배구조] 소액주주현황: {saved}건")

    return results



# ══════════════════════════════════════════════════════════════════
# 7. 주식배당정보
# ══════════════════════════════════════════════════════════════════
def collect_dividend(conn, bas_dt: str) -> int:
    """주식배당정보 수집."""
    logger.info(f"[배당] {bas_dt} 수집 시작")
    items = _api_call(
        f"{DIVI_BASE}/getDiviInfo",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[배당] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "") or it.get("isinCd", "")).strip().zfill(6)
        if not code or not code.isdigit():
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO stock_dividend
                (bas_dt, stock_code, stock_name, dvdn_bas_dt,
                 cash_dvdn, stk_dvdn_rt, dvdn_yld)
                VALUES (?,?,?,?,?,?,?)
            """, (
                bas_dt, code,
                it.get("itmsNm", ""),
                it.get("dvdnBasDt", ""),
                _fval(it.get("cashDvdn")),      # 현금배당금
                _fval(it.get("stkDvdnRt")),     # 주식배당률
                _fval(it.get("dvdnYld")),        # 배당수익률
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[배당] {code} 저장 오류: {e}")

    conn.commit()
    logger.info(f"[배당] {bas_dt} → {saved}건 저장")
    return saved



# ══════════════════════════════════════════════════════════════════
# 8. 주식대차정보
# ══════════════════════════════════════════════════════════════════
def collect_lending(conn, bas_dt: str) -> int:
    """주식대차 종목별 현황 수집."""
    logger.info(f"[대차] {bas_dt} 수집 시작")
    items = _api_call(
        f"{LEND_BASE}/getStItemLendAndBorrStatu",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[대차] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        code = str(it.get("srtnCd", "") or it.get("isinCd", "")).strip()
        if len(code) > 6: code = code[-6:]
        if not code or not code.isdigit(): continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO stock_lending
                (bas_dt, stock_code, stock_name,
                 lend_vol, lend_amt, lend_bal_vol, lend_bal_amt, lend_bal_rt)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                bas_dt, code.zfill(6),
                it.get("itmsNm", ""),
                _fval(it.get("lendVol")),       # 대차거래량
                _fval(it.get("lendAmt")),        # 대차거래금액
                _fval(it.get("lendBalVol")),     # 대차잔고주수
                _fval(it.get("lendBalAmt")),     # 대차잔고금액
                _fval(it.get("lendBalRt")),      # 대차잔고비율
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[대차] {code} 오류: {e}")

    conn.commit()
    logger.info(f"[대차] {bas_dt} → {saved}건 저장")
    return saved


# ══════════════════════════════════════════════════════════════════
# 9. 주식권리일정정보
# ══════════════════════════════════════════════════════════════════
def collect_rights_schedule(conn, bas_dt: str) -> int:
    """주식 권리행사 일정 수집."""
    logger.info(f"[권리일정] {bas_dt} 수집 시작")
    items = _api_call(
        f"{RIGH_BASE}/getRighExerReasSche",
        {"basDt": bas_dt},
    )
    if not items:
        logger.warning(f"[권리일정] {bas_dt} 데이터 없음")
        return 0

    saved = 0
    for it in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO stock_rights_schedule
                (righ_std_dt, stock_code, stock_name, corp_name,
                 righ_exer_reas, exer_dt, righ_type)
                VALUES (?,?,?,?,?,?,?)
            """, (
                bas_dt,
                str(it.get("srtnCd", "")).strip().zfill(6),
                it.get("itmsNm", ""),
                it.get("corpNm", ""),
                it.get("righExerReas", ""),   # 권리행사사유
                it.get("exerDt", ""),          # 행사일
                it.get("righType", ""),        # 권리종류
            ))
            saved += 1
        except Exception as e:
            logger.debug(f"[권리일정] 오류: {e}")

    conn.commit()
    logger.info(f"[권리일정] {bas_dt} → {saved}건 저장")
    return saved



# ══════════════════════════════════════════════════════════════════
# 10. 한국예탁결제원 매수청구일정 (seibro.or.kr)
# ══════════════════════════════════════════════════════════════════
def collect_buyreq_schedule(conn, bas_dt: str) -> int:
    """매수청구일정 수집 (한국예탁결제원)."""
    logger.info(f"[매수청구] {bas_dt} 수집 시작")
    pub_key, _ = _get_keys()

    # seibro는 날짜 범위 조회 방식 (rgtStdDt 단일 날짜)
    try:
        url = f"{SEIBRO_BASE}/getBuyreqSkeduInfoSvc"
        params = {
            "serviceKey": pub_key,
            "rgtStdDt":   bas_dt,
            "pageNo":     1,
            "numOfRows":  1000,
            "resultType": "json",
        }
        r = requests.get(url, params=params, timeout=30)

        # seibro는 XML 응답일 수 있음
        if "xml" in r.headers.get("Content-Type", "").lower() or r.text.strip().startswith("<"):
            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
        else:
            data = r.json()
            body = data.get("response", {}).get("body", {})
            raw = body.get("items", {}).get("item", [])
            items = raw if isinstance(raw, list) else ([raw] if raw else [])

        saved = 0
        for it in (items or []):
            # XML ElementTree의 경우 딕셔너리 변환
            if hasattr(it, 'findtext'):
                d = {child.tag: child.text for child in it}
            else:
                d = it

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO buyreq_schedule
                    (rgt_std_dt, issueco_custno, corp_name, stock_code,
                     buyreq_start_dt, buyreq_end_dt, buyreq_price)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    bas_dt,
                    d.get("issucoCustno", ""),
                    d.get("issucNm", "") or d.get("corpNm", ""),
                    d.get("srtnCd", "") or d.get("stockCode", ""),
                    d.get("buyreqStartDt", "") or d.get("aplyBgnDt", ""),
                    d.get("buyreqEndDt", "") or d.get("aplyEndDt", ""),
                    _fval(d.get("buyreqPrice") or d.get("apprPrice")),
                ))
                saved += 1
            except Exception as e:
                logger.debug(f"[매수청구] 저장 오류: {e}")

        conn.commit()
        if saved > 0:
            logger.info(f"[매수청구] {bas_dt} → {saved}건 저장")
        else:
            logger.debug(f"[매수청구] {bas_dt} 데이터 없음")
        return saved

    except Exception as e:
        logger.warning(f"[매수청구] {bas_dt} 오류: {e}")
        return 0

# ══════════════════════════════════════════════════════════════════
# 날짜별 전체 수집
# ══════════════════════════════════════════════════════════════════
def collect_all_for_date(conn, bas_dt: str, skip_existing: bool = True) -> dict:
    """특정 날짜 모든 데이터 수집."""
    results = {}

    # 이미 수집됐는지 확인
    if skip_existing:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM stock_price_daily WHERE bas_dt=?", (bas_dt,)
        ).fetchone()[0]
        if cnt > 100:
            logger.info(f"[{bas_dt}] 시세 이미 {cnt}건 → 스킵")
            results["price"] = cnt
        else:
            results["price"] = collect_stock_price(conn, bas_dt)
    else:
        results["price"] = collect_stock_price(conn, bas_dt)

    if results.get("price", 0) == 0:
        logger.warning(f"[{bas_dt}] 시세 없음 — 휴장일이거나 데이터 미제공")
        return results

    # 나머지 수집
    time.sleep(0.5)
    results["short"]    = collect_short_sell(conn, bas_dt)
    time.sleep(0.5)
    results["investor"] = collect_investor_trading(conn, bas_dt)
    time.sleep(0.5)
    results["foreign"]  = collect_foreign_holding(conn, bas_dt)
    time.sleep(0.5)
    results["dividend"] = collect_dividend(conn, bas_dt)
    time.sleep(0.5)
    results["lending"]  = collect_lending(conn, bas_dt)
    time.sleep(0.5)
    results["rights"]   = collect_rights_schedule(conn, bas_dt)
    time.sleep(0.5)
    results["buyreq"]   = collect_buyreq_schedule(conn, bas_dt)
    time.sleep(0.5)

    return results


# ══════════════════════════════════════════════════════════════════
# crontab 등록
# ══════════════════════════════════════════════════════════════════
def setup_cron():
    """매일 18:30 자동 실행 cron 등록."""
    import subprocess
    py  = "/Applications/stock_dashboard/venv/bin/python3"
    scr = "/Applications/stock_dashboard/public_data_collector.py"
    log = "/Applications/stock_dashboard/public_data.log"

    # 기업정보는 주 1회(월요일 07:00)
    lines = [
        f"30 18 * * 1-5 cd /Applications/stock_dashboard && {py} {scr} >> {log} 2>&1",
        f"0 7 * * 1 cd /Applications/stock_dashboard && {py} {scr} --company-only >> {log} 2>&1",
    ]

    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = res.stdout if res.returncode == 0 else ""

    if "public_data_collector" in existing:
        print("✅ cron 이미 등록됨")
        return

    new_cron = existing.rstrip()
    for line in lines:
        new_cron += "\n" + line
    new_cron += "\n"

    proc = subprocess.run(["crontab", "-"], input=new_cron, text=True)
    if proc.returncode == 0:
        print("✅ cron 등록 완료!")
        for line in lines:
            print(f"   {line}")
    else:
        print("❌ cron 등록 실패. 수동으로 등록하세요:")
        for line in lines:
            print(f"   {line}")


# ══════════════════════════════════════════════════════════════════
# 통계 출력
# ══════════════════════════════════════════════════════════════════
def print_stats(conn):
    tables = [
        ("stock_price_daily",    "일별 시세"),
        ("short_sell_daily",     "대차거래"),
        ("investor_trading_daily","투자자매매"),
        ("foreign_holding_daily", "외국인보유"),
        ("listed_company_info",   "기업정보"),
        ("financial_data",        "재무제표"),
        ("corp_governance",        "기업지배구조"),
        ("stock_dividend",          "주식배당"),
        ("stock_lending",           "주식대차"),
        ("stock_rights_schedule",   "권리일정"),
        ("buyreq_schedule",         "매수청구일정"),
    ]
    print()
    print("━" * 55)
    print("📊 공공데이터 수집 현황")
    print("━" * 55)
    for tbl, name in tables:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            latest = conn.execute(
                f"SELECT MAX(bas_dt) FROM {tbl}" if tbl != "financial_data"
                else "SELECT MAX(year)||'Q'||MAX(quarter) FROM financial_data"
            ).fetchone()[0]
            print(f"  {name:12s}: {cnt:>8,}건  최신={latest or '-'}")
        except Exception:
            print(f"  {name:12s}: 테이블 없음")
    print("━" * 55)
    print()


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="공공데이터포털 + DART 일괄 수집")
    parser.add_argument("--date",         default="",       help="기준일자 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--dart-only",    action="store_true", help="DART 재무만 수집")
    parser.add_argument("--price-only",   action="store_true", help="시세만 수집")
    parser.add_argument("--company-only", action="store_true", help="기업정보만 수집")
    parser.add_argument("--setup-cron",   action="store_true", help="crontab 등록")
    parser.add_argument("--stats",        action="store_true", help="현황 출력만")
    parser.add_argument("--backfill",     action="store_true", help="과거 데이터 백필")
    parser.add_argument("--start",        default="",       help="백필 시작일 YYYYMMDD")
    parser.add_argument("--end",          default="",       help="백필 종료일 YYYYMMDD")
    parser.add_argument("--dart-years",   action="store_true", help="DART 전년도 일괄 수집")
    args = parser.parse_args()

    if args.setup_cron:
        setup_cron()
        return

    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    if args.stats:
        print_stats(conn)
        conn.close()
        return

    # 날짜 결정
    if args.date:
        bas_dt = args.date
    else:
        bas_dt = date.today().strftime("%Y%m%d")

    pub_key, dart_key = _get_keys()
    if not pub_key and not args.dart_only:
        logger.error("PUBLIC_DATA_API_KEY 없음 — .env 파일 확인")
        conn.close()
        return
    if not dart_key and args.dart_only:
        logger.error("DART_API_KEY 없음 — .env 파일 확인")
        conn.close()
        return

    t_start = time.time()
    logger.info(f"=== 공공데이터 수집 시작 ({bas_dt}) ===")

    # DART 전년도 일괄
    if args.dart_years or args.dart_only:
        logger.info("[DART] 전종목 재무 일괄 수집 시작")
        results = collect_dart_all_years(conn)
        total = sum(v for v in results.values() if isinstance(v, int) and v > 0)
        logger.info(f"[DART] 완료: {total}건 저장")

    if args.dart_only:
        print_stats(conn)
        conn.close()
        return

    # 기업정보만
    if args.company_only:
        collect_listed_company(conn, bas_dt)
        print_stats(conn)
        conn.close()
        return

    # 백필 모드
    if args.backfill:
        start_dt = args.start or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        end_dt   = args.end   or date.today().strftime("%Y%m%d")
        logger.info(f"[백필] {start_dt} ~ {end_dt}")

        cur = datetime.strptime(start_dt, "%Y%m%d").date()
        end = datetime.strptime(end_dt,   "%Y%m%d").date()
        total_days = sum(1 for d in range((end-cur).days+1)
                         if (cur + timedelta(days=d)).weekday() < 5)
        done = 0

        while cur <= end:
            if cur.weekday() < 5:  # 평일만
                d_str = cur.strftime("%Y%m%d")
                done += 1
                elapsed = time.time() - t_start
                per = elapsed / done if done > 1 else 0
                eta = per * (total_days - done)
                eta_str = f"{int(eta//60)}분{int(eta%60)}초" if eta > 0 else "-"
                print(f"[{done:3d}/{total_days}] {d_str}  ETA {eta_str}", flush=True)
                collect_all_for_date(conn, d_str, skip_existing=True)
                time.sleep(1.5)
            cur += timedelta(days=1)

        print_stats(conn)
        conn.close()
        return

    # 일반 모드: 오늘 전체
    if args.price_only:
        collect_stock_price(conn, bas_dt)
    else:
        results = collect_all_for_date(conn, bas_dt, skip_existing=True)
        # 기업정보 (주 1회 월요일만)
        if date.today().weekday() == 0:
            collect_listed_company(conn, bas_dt)

    elapsed = time.time() - t_start
    logger.info(f"=== 완료 ({elapsed:.1f}초) ===")
    print_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
