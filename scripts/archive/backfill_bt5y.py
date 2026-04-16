"""
backfill_bt5y.py — 5년 백테스트용 데이터 수집 (가격 + 재무)

해결 과제:
  1. [가격] 현재 최초 데이터가 2021-04-05 → MA200 워밍업(300거래일) 위해
           2019-01-01부터 데이터 필요 → yfinance로 7년치 다운로드
  2. [재무] 연간/분기 재무 미수집 종목 → DART API로 보완

실행:
  cd /Applications/stock_dashboard
  source venv/bin/activate

  # 1단계: 가격 데이터 확장 (2019~)
  python3 backfill_bt5y.py --mode price

  # 2단계: 연간 재무 보완 (DART API 필요)
  python3 backfill_bt5y.py --mode finance

  # 전체 한번에
  python3 backfill_bt5y.py --mode all

  # 현황 확인만
  python3 backfill_bt5y.py --stats
"""

import sqlite3, time, json, logging, argparse, sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH       = Path("/Applications/stock_dashboard/stock.db")
LOG_PATH      = Path("/Applications/stock_dashboard/backfill_bt5y.log")
PRICE_START   = "2019-01-01"   # MA200 워밍업 확보 (5년 백테스트 시작 2021 - 15개월 여유)
SLEEP_API     = 0.3            # yfinance 호출 간격(초)
SLEEP_DART    = 0.8            # DART API 간격(초)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════════════════
def get_conn():
    return sqlite3.connect(str(DB_PATH))


def get_stock_list(conn, market_filter=None):
    """가격 데이터가 있는 전체 한국 6자리 종목 코드 반환."""
    rows = conn.execute("""
        SELECT DISTINCT ph.stock_code,
               COALESCE(su.market, '') AS market,
               COALESCE(su.stock_name, sm.stock_name, ph.stock_code) AS name
        FROM price_history ph
        LEFT JOIN stock_universe su USING(stock_code)
        LEFT JOIN stock_meta    sm USING(stock_code)
        WHERE LENGTH(ph.stock_code)=6
          AND ph.stock_code GLOB '[0-9]*'
        ORDER BY ph.stock_code
    """).fetchall()
    return rows


# ══════════════════════════════════════════════════════════════
#  현황 출력
# ══════════════════════════════════════════════════════════════
def print_stats(conn):
    r = conn.execute("""
        SELECT MIN(date), MAX(date), COUNT(DISTINCT stock_code), COUNT(*)
        FROM price_history WHERE close>0
          AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
    """).fetchone()
    log.info("━"*60)
    log.info(f"[가격 데이터]  {r[0]} ~ {r[1]}  |  {r[2]:,}종목  |  {r[3]:,}행")

    # 2019-01-01 이전 데이터가 있는 종목 수
    n2019 = conn.execute("""
        SELECT COUNT(DISTINCT stock_code) FROM price_history
        WHERE date < '2019-06-01' AND close>0
          AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
    """).fetchone()[0]
    log.info(f"[가격] 2019-01 이전 데이터 보유: {n2019:,}종목  (MA200 워밍업 가능)")

    r2 = conn.execute("""
        SELECT COUNT(DISTINCT stock_code), MIN(year), MAX(year)
        FROM financial_data WHERE is_annual=1
    """).fetchone()
    log.info(f"[연간 재무]  {r2[1]}~{r2[2]}년  |  {r2[0]:,}종목")

    r3 = conn.execute("""
        SELECT COUNT(DISTINCT stock_code), MIN(year), MAX(year)
        FROM financial_data WHERE is_annual=0 AND quarter BETWEEN 1 AND 3
    """).fetchone()
    log.info(f"[분기 재무]  {r3[1]}~{r3[2]}년  |  {r3[0]:,}종목")

    # 백테스트 실제 가용 종목
    bt_ready = conn.execute("""
        SELECT COUNT(DISTINCT p.stock_code)
        FROM (
          SELECT stock_code FROM price_history
          WHERE close>0 AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          GROUP BY stock_code HAVING MIN(date)<='2020-01-31' AND COUNT(*)>=400
        ) p
        JOIN (SELECT DISTINCT stock_code FROM financial_data WHERE year>=2020) f
          ON p.stock_code=f.stock_code
    """).fetchone()[0]
    log.info(f"[백테스트 가용]  가격+재무 모두: {bt_ready:,}종목  (5년 백테스트 가능)")
    log.info("━"*60)


# ══════════════════════════════════════════════════════════════
#  [1] 가격 데이터 확장 — yfinance 7년치
# ══════════════════════════════════════════════════════════════
def backfill_price(conn, limit=None, start_from=None):
    """
    PRICE_START(2019-01-01)부터 현재까지 전종목 가격 다운로드.
    이미 충분한 데이터가 있는 종목은 건너뜀.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance 설치 필요: pip install yfinance")
        return

    stocks = get_stock_list(conn)
    if limit:
        stocks = stocks[:limit]

    # 각 종목의 현재 최솟값 확인
    existing_min = {
        r[0]: r[1] for r in conn.execute("""
            SELECT stock_code, MIN(date) FROM price_history
            WHERE close>0 AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code
        """).fetchall()
    }

    total     = len(stocks)
    updated   = 0
    skipped   = 0
    failed    = 0
    inserted  = 0

    log.info(f"[가격 백필] 대상: {total:,}종목  목표 시작일: {PRICE_START}")

    for idx, (code, market, name) in enumerate(stocks, 1):
        # 이미 2019-06-01 이전 데이터가 있으면 스킵
        cur_min = existing_min.get(code, "9999-99-99")
        if cur_min <= "2019-06-01":
            skipped += 1
            if idx % 500 == 0:
                log.info(f"  진행: {idx}/{total}  업데이트:{updated} 스킵:{skipped} 실패:{failed}")
            continue

        if start_from and code < start_from:
            skipped += 1
            continue

        # yfinance 티커 결정
        mkt = market.upper()
        if '코스닥' in mkt or 'KOSDAQ' in mkt or 'KQ' in mkt:
            tickers = [f"{code}.KQ", f"{code}.KS"]
        else:
            tickers = [f"{code}.KS", f"{code}.KQ"]

        df = None
        for tk in tickers:
            try:
                import yfinance as yf
                _df = yf.download(
                    tk, start=PRICE_START,
                    end=datetime.today().strftime("%Y-%m-%d"),
                    interval="1d", progress=False, auto_adjust=True,
                )
                if _df is not None and not _df.empty:
                    if hasattr(_df.columns, 'levels'):
                        _df.columns = [c[0] if isinstance(c, tuple) else c for c in _df.columns]
                    df = _df
                    break
            except Exception:
                pass
            time.sleep(SLEEP_API)

        if df is None or df.empty:
            failed += 1
            if idx % 100 == 0:
                log.info(f"  진행: {idx}/{total}  업데이트:{updated} 스킵:{skipped} 실패:{failed}")
            continue

        # DB에 없는 날짜만 삽입
        rows_inserted = 0
        for dt_idx, row in df.iterrows():
            try:
                dt_str = dt_idx.strftime("%Y-%m-%d") if hasattr(dt_idx, 'strftime') else str(dt_idx)[:10]
                close  = float(row.get('Close', 0) or 0)
                if close <= 0:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO price_history
                      (stock_code, date, open, high, low, close, volume)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    code, dt_str,
                    float(row.get('Open',  0) or 0),
                    float(row.get('High',  0) or 0),
                    float(row.get('Low',   0) or 0),
                    close,
                    float(row.get('Volume',0) or 0),
                ))
                rows_inserted += 1
            except Exception:
                continue

        conn.commit()
        inserted += rows_inserted
        updated  += 1

        if idx % 100 == 0 or idx == total:
            log.info(f"  진행: {idx}/{total} ({idx/total*100:.1f}%)  "
                     f"업데이트:{updated}  스킵:{skipped}  실패:{failed}  삽입:{inserted:,}행")

    log.info(f"[가격 백필 완료]  업데이트:{updated}  스킵:{skipped}  실패:{failed}  총삽입:{inserted:,}행")


# ══════════════════════════════════════════════════════════════
#  [2] 연간/분기 재무 보완 — DART API
# ══════════════════════════════════════════════════════════════
def _dart_client():
    """OpenDartReader 클라이언트 초기화."""
    try:
        import OpenDartReader
        import os
        key = os.environ.get("DART_API_KEY", "")
        if not key:
            # config.py에서 키 로드
            sys.path.insert(0, "/Applications/stock_dashboard")
            try:
                from config import DART_API_KEY
                key = DART_API_KEY
            except Exception:
                pass
        if not key:
            log.error("DART_API_KEY 환경변수 또는 config.py 설정 필요")
            return None
        return OpenDartReader.OpenDartReader(key)
    except ImportError:
        log.error("OpenDartReader 설치 필요: pip install opendartreader")
        return None


def _fin_exists(conn, code, year, quarter):
    return conn.execute(
        "SELECT 1 FROM financial_data WHERE stock_code=? AND year=? AND quarter=? LIMIT 1",
        (code, year, quarter)
    ).fetchone() is not None


def _save_fin(conn, code, year, qnum, is_annual, m):
    conn.execute("""
        INSERT OR REPLACE INTO financial_data
          (stock_code,year,quarter,is_annual,revenue,operating_profit,net_income,
           total_assets,total_liabilities,total_equity,capital_stock,
           eps,bps,dps,cash,total_shares,depreciation_amortization)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        code, year, qnum, int(is_annual),
        m.get("revenue"), m.get("operating_profit"), m.get("net_income"),
        m.get("total_assets"), m.get("total_liabilities"), m.get("total_equity"),
        m.get("capital_stock"), m.get("eps"), m.get("bps"), m.get("dps"),
        m.get("cash"), m.get("total_shares"), m.get("depreciation_amortization"),
    ))


def _parse_dart_df(fn):
    """DART DataFrame → 재무 dict 파싱."""
    import pandas as pd
    m = {k: None for k in [
        "revenue","operating_profit","net_income","total_assets",
        "total_liabilities","total_equity","capital_stock",
        "eps","bps","dps","cash","total_shares","depreciation_amortization"
    ]}
    for _, row in fn.iterrows():
        acc = str(row.get("account_nm", "")).replace(" ", "")
        vc  = "thstrm_amount"
        if not acc or vc not in row or pd.isna(row[vc]):
            continue
        try:
            val = float(str(row[vc]).replace(",", ""))
        except Exception:
            continue
        if   "매출액" in acc or "영업수익" in acc:              m["revenue"]               = val
        elif "영업이익" in acc:                                  m["operating_profit"]       = val
        elif "당기순이익" in acc and "주당" not in acc:         m["net_income"]             = val
        elif "자산총계" in acc:                                  m["total_assets"]           = val
        elif "부채총계" in acc:                                  m["total_liabilities"]      = val
        elif "자본총계" in acc:                                  m["total_equity"]           = val
        elif "자본금" in acc:                                    m["capital_stock"]          = val
        elif any(k in acc for k in ["기본주당순이익","주당순이익","기본EPS"]): m["eps"] = val
        elif any(k in acc for k in ["주당순자산","1주당순자산가액"]):          m["bps"] = val
        elif any(k in acc for k in ["주당배당금","주당현금배당금"]):           m["dps"] = val
        elif any(k in acc for k in ["현금및현금성자산","현금성자산"]):         m["cash"]= val
        elif any(k in acc for k in ["발행주식수","유통주식수"]):               m["total_shares"] = val
        elif "감가상각비" in acc:                                m["depreciation_amortization"] = val
    return m


def backfill_finance(conn, limit=None, start_from=None, years_back=7):
    """
    DART API로 연간(사업보고서) + 분기(Q1/Q2/Q3) 재무 수집.
    - 연간: reprt_code=11011 (사업보고서) → is_annual=True, quarter=4
    - Q1:   reprt_code=11013
    - Q2:   reprt_code=11012
    - Q3:   reprt_code=11014
    """
    import pandas as pd

    dart = _dart_client()
    if dart is None:
        return

    stocks = get_stock_list(conn)
    if limit:
        stocks = stocks[:limit]

    # ETF/리츠/스팩 패턴 필터 — 재무제표 없는 종목 스킵
    skip_patterns = ["ETF","ETN","TIGER","KODEX","KINDEX","KOSEF","ARIRANG",
                     "KBSTAR","HANARO","ACE","SOL","RISE","SMART","파생",
                     "스팩","목적"]

    today    = datetime.today()
    cur_year = today.year
    cur_month= today.month
    # 현재 공시 가능한 최신 분기
    if   cur_month >= 11: cur_q = 3
    elif cur_month >= 8:  cur_q = 2
    elif cur_month >= 5:  cur_q = 1
    elif cur_month >= 3:  cur_q = 4   # 전년 연간
    else:                 cur_q = 3   # 전전년 Q3

    start_year = cur_year - years_back

    # reprt_code → quarter
    qmap = {
        "11011": 4,   # 사업보고서 (연간, is_annual=True)
        "11013": 1,   # 1분기
        "11012": 2,   # 2분기 (반기)
        "11014": 3,   # 3분기
    }

    total   = len(stocks)
    updated = 0
    skipped = 0
    api_cnt = 0
    API_DAILY_LIMIT = 7500

    log.info(f"[재무 백필] 대상: {total:,}종목  기간: {start_year}~{cur_year}  "
             f"(연간+Q1~Q3)")

    for idx, (code, market, name) in enumerate(stocks, 1):
        # ETF 등 스킵
        if any(p in name for p in skip_patterns):
            skipped += 1
            continue

        if start_from and code < start_from:
            skipped += 1
            continue

        if api_cnt >= API_DAILY_LIMIT:
            log.warning(f"[재무] DART 일일 한도({API_DAILY_LIMIT}) 도달 — 중단 (재실행 시 이어서)")
            break

        stock_new = 0
        for year in range(cur_year, start_year - 1, -1):
            for rprt, qnum in qmap.items():
                # 미래 분기 스킵
                if year > cur_year:
                    continue
                if year == cur_year:
                    if rprt == "11011" and cur_month < 4:   # 사업보고서 미발표
                        continue
                    if rprt == "11013" and cur_month < 5:   # Q1 미발표
                        continue
                    if rprt == "11012" and cur_month < 8:   # Q2 미발표
                        continue
                    if rprt == "11014" and cur_month < 11:  # Q3 미발표
                        continue

                is_ann = (rprt == "11011")
                # 이미 있는 분기는 스킵 (단 연간은 별도 체크)
                existing = conn.execute(
                    "SELECT 1 FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? LIMIT 1",
                    (code, year, qnum, int(is_ann))
                ).fetchone()
                if existing:
                    continue

                if api_cnt >= API_DAILY_LIMIT:
                    break

                fn = None
                for fs_div in ["CFS", "OFS"]:
                    try:
                        df = dart.finstate_all(code, year, rprt, fs_div=fs_div)
                        api_cnt += 1
                        if df is not None and not df.empty:
                            fn = df
                            break
                    except Exception:
                        api_cnt += 1
                    time.sleep(SLEEP_DART)

                if fn is None or fn.empty:
                    try:
                        fn = dart.finstate(code, year, rprt)
                        api_cnt += 1
                    except Exception:
                        api_cnt += 1
                    time.sleep(SLEEP_DART)

                if fn is None or fn.empty:
                    continue

                m = _parse_dart_df(fn)
                try:
                    _save_fin(conn, code, year, qnum, is_ann, m)
                    stock_new += 1
                except Exception as e:
                    log.debug(f"[저장오류] {code} {year}Q{qnum}: {e}")

            if api_cnt >= API_DAILY_LIMIT:
                break

        if stock_new > 0:
            conn.commit()
            updated += 1

        if idx % 50 == 0 or idx == total:
            log.info(f"  진행: {idx}/{total} ({idx/total*100:.1f}%)  "
                     f"업데이트:{updated}  스킵:{skipped}  API호출:{api_cnt}")

    conn.commit()
    log.info(f"[재무 백필 완료]  업데이트:{updated}  스킵:{skipped}  총API호출:{api_cnt}")


# ══════════════════════════════════════════════════════════════
#  ROE 계산 — 연간/분기 재무에서 ROE 없는 레코드 채우기
# ══════════════════════════════════════════════════════════════
def fill_roe(conn):
    """
    ROE = net_income / total_equity * 100
    roe 컬럼이 NULL인 레코드를 업데이트.
    """
    updated = conn.execute("""
        UPDATE financial_data
        SET roe = ROUND(net_income * 100.0 / total_equity, 2)
        WHERE roe IS NULL
          AND net_income IS NOT NULL AND total_equity IS NOT NULL
          AND total_equity > 0
    """).rowcount
    conn.commit()
    log.info(f"[ROE 계산]  {updated:,}건 업데이트")


# ══════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="5년 백테스트 데이터 수집")
    parser.add_argument("--mode",       choices=["price","finance","roe","all","stats"],
                        default="stats", help="실행 모드")
    parser.add_argument("--limit",      type=int,  default=None, help="종목 수 제한 (테스트)")
    parser.add_argument("--start-from", type=str,  default=None, help="이 코드부터 재개")
    parser.add_argument("--years-back", type=int,  default=7,    help="재무 수집 연도 수 (기본 7)")
    parser.add_argument("--stats",      action="store_true",     help="현황만 출력")
    args = parser.parse_args()

    if args.stats:
        args.mode = "stats"

    conn = get_conn()

    if args.mode in ("stats", "all"):
        print_stats(conn)

    if args.mode in ("price", "all"):
        log.info("=" * 60)
        log.info("[1단계] 가격 데이터 확장 (2019-01-01 ~)")
        log.info("=" * 60)
        backfill_price(conn, limit=args.limit, start_from=args.start_from)
        fill_roe(conn)
        print_stats(conn)

    if args.mode in ("finance", "all"):
        log.info("=" * 60)
        log.info("[2단계] 재무 데이터 보완 (DART API)")
        log.info("=" * 60)
        backfill_finance(conn, limit=args.limit,
                         start_from=args.start_from,
                         years_back=args.years_back)
        fill_roe(conn)
        print_stats(conn)

    if args.mode == "roe":
        fill_roe(conn)

    conn.close()


if __name__ == "__main__":
    main()
