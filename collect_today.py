#!/usr/bin/env python3
"""
collect_today.py — 오늘 가격 데이터 즉시 수집 (KRX + Yahoo Finance)
=====================================================================
실행: cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate
      python3 collect_today.py

용도: 서버 다운으로 KRX 18:00 잡이 누락됐을 때 수동 실행
"""
import sys, os, sqlite3, time, logging
from datetime import date, datetime, timedelta

sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

DB_PATH = '/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'

def wait_db(max_wait=30):
    """DB 락 해제 대기 (최대 max_wait초)"""
    for attempt in range(max_wait):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=3)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            conn.close()
            if attempt > 0:
                log.info(f"DB 락 해제 ({attempt}초 대기)")
            return True
        except sqlite3.OperationalError:
            if attempt == 0:
                log.info("DB 락 대기 중...")
            time.sleep(1)
    log.warning(f"DB 락 {max_wait}초 초과 — 강제 진행")
    return False

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    return conn

def collect_krx_today():
    """KRX 승인 API로 오늘 전종목 OHLCV 수집"""
    try:
        import requests
        import config as cfg
    except ImportError as e:
        log.error(f"임포트 실패: {e}")
        return 0, 0

    api_key = getattr(cfg, 'KRX_API_KEY', '')
    if not api_key:
        log.warning("KRX_API_KEY 없음")
        return 0, 0

    today = date.today()
    bas_dd = today.strftime('%Y%m%d')
    today_str = today.strftime('%Y-%m-%d')
    base_url = 'https://data-dbg.krx.co.kr/svc/apis'
    headers = {'AUTH_KEY': api_key}

    def _fetch(path):
        try:
            r = requests.get(f"{base_url}/{path}",
                             params={'basDd': bas_dd},
                             headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json().get('OutBlock_1', [])
        except Exception as e:
            log.warning(f"KRX {path}: {e}")
        return []

    def _n(row, key):
        v = row.get(key, '')
        try:
            return float(str(v).replace(',', '')) if v not in ('', '-', None) else 0.0
        except:
            return 0.0

    wait_db()
    conn = get_conn()
    ins = upd = idx_saved = 0

    try:
        # 잘못된 오늘 데이터 먼저 삭제 (May4 복사본)
        prev_biz = None
        _HOLIDAYS = {'2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
                     '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
                     '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09','2026-12-25'}
        for i in range(1, 8):
            d = today - timedelta(days=i)
            if d.weekday() < 5 and d.isoformat() not in _HOLIDAYS:
                prev_biz = d.isoformat()
                break

        if prev_biz:
            del_cnt = conn.execute(f"""
                DELETE FROM price_history
                WHERE date = '{today_str}'
                  AND length(stock_code) = 6
                  AND stock_code GLOB '[0-9]*'
                  AND stock_code IN (
                    SELECT p6.stock_code
                    FROM price_history p6
                    JOIN price_history p_prev
                      ON p6.stock_code = p_prev.stock_code
                     AND p_prev.date = '{prev_biz}'
                    WHERE p6.date = '{today_str}'
                      AND p6.open  = p_prev.open
                      AND p6.close = p_prev.close
                      AND ABS(COALESCE(p6.volume,0)-COALESCE(p_prev.volume,0))
                          / (COALESCE(p_prev.volume,0) + 1.0) < 0.02
                  )
            """).rowcount
            conn.commit()
            log.info(f"직전일 복사본 {del_cnt}건 삭제 (직전영업일={prev_biz})")

        # 유가증권 + 코스닥 OHLCV
        for path in ('sto/stk_bydd_trd', 'sto/ksq_bydd_trd'):
            rows = _fetch(path)
            log.info(f"KRX {path}: {len(rows)}건 수신")
            for r in rows:
                code = str(r.get('ISU_CD', '')).strip()
                if not code or len(code) != 6 or not code.isdigit():
                    continue
                close = _n(r, 'TDD_CLSPRC')
                open_ = _n(r, 'TDD_OPNPRC')
                high  = _n(r, 'TDD_HGPRC')
                low   = _n(r, 'TDD_LWPRC')
                vol   = _n(r, 'ACC_TRDVOL')
                if close <= 0:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?,?,?,?,?,?,?)
                """, (code, today_str, open_, high, low, close, vol))
                if cur.rowcount > 0:
                    ins += 1
                else:
                    conn.execute("""
                        UPDATE price_history
                        SET open=?, high=?, low=?, close=?, volume=?
                        WHERE stock_code=? AND date=?
                    """, (open_, high, low, close, vol, code, today_str))
                    upd += 1

        # KOSPI/KOSDAQ 지수
        _idx_map = {'코스피': '^KS11', 'KOSPI': '^KS11',
                    '코스닥': '^KQ11', 'KOSDAQ': '^KQ11',
                    '코스피 200': '^KS200', '코스닥 150': '^KQ150'}
        for path in ('idx/kospi_dd_trd', 'idx/kosdaq_dd_trd'):
            for r in _fetch(path):
                idx_nm = str(r.get('IDX_NM', '')).strip()
                code = _idx_map.get(idx_nm)
                if not code:
                    continue
                close = _n(r, 'CLSPRC_IDX')
                open_ = _n(r, 'OPNPRC_IDX')
                high  = _n(r, 'HGPRC_IDX')
                low   = _n(r, 'LWPRC_IDX')
                vol   = _n(r, 'ACC_TRDVOL')
                if close <= 0:
                    continue
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?,?,?,?,?,?,?)
                """, (code, today_str, open_, high, low, close, vol))
                if cur.rowcount > 0:
                    idx_saved += 1
                else:
                    conn.execute("""
                        UPDATE price_history
                        SET open=?, high=?, low=?, close=?, volume=?
                        WHERE stock_code=? AND date=?
                    """, (open_, high, low, close, vol, code, today_str))
        conn.commit()
        log.info(f"KRX 저장: 종목 신규+{ins} 수정{upd}, 지수+{idx_saved}")
    except Exception as e:
        log.error(f"KRX 수집 오류: {e}")
        conn.rollback()
    finally:
        conn.close()

    return ins + upd, idx_saved

def collect_yahoo_indices():
    """Yahoo Finance로 KOSPI/NASDAQ/환율 등 갱신"""
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance 미설치")
        return

    today = date.today()
    start = (today - timedelta(days=5)).strftime('%Y-%m-%d')
    end   = (today + timedelta(days=1)).strftime('%Y-%m-%d')

    _HOLIDAYS = {'2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
                 '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
                 '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09','2026-12-25'}
    _KR_SYMS = {'^KS11', '^KQ11', '^KS200'}

    SYMBOLS = {
        '^KS11':    'KOSPI',
        '^KQ11':    'KOSDAQ',
        '^IXIC':    'NASDAQ',
        '^GSPC':    'S&P500',
        '^VIX':     'VIX',
        'USDKRW=X': 'USD/KRW',
        'GC=F':     'GOLD',
        'CL=F':     'OIL',
    }

    wait_db()
    conn = get_conn()
    total = 0
    try:
        for symbol, name in SYMBOLS.items():
            try:
                df = yf.download(symbol, start=start, end=end,
                                 interval='1d', progress=False, auto_adjust=True)
                if df is None or df.empty:
                    log.info(f"{symbol}: 데이터 없음")
                    continue
                if hasattr(df.columns, 'get_level_values'):
                    try:
                        df.columns = df.columns.get_level_values(0)
                    except:
                        pass
                saved = 0
                for ts, row in df.iterrows():
                    try:
                        row_date = ts.date() if hasattr(ts, 'date') else date.fromisoformat(str(ts)[:10])
                        # 한국 지수: 공휴일/주말 저장 금지
                        if symbol in _KR_SYMS:
                            if row_date.weekday() >= 5:
                                continue
                            if row_date.isoformat() in _HOLIDAYS:
                                continue
                        def _gv(col):
                            v = row.get(col, 0)
                            if hasattr(v, 'iloc'): v = v.iloc[0]
                            try: return float(v) if v is not None else 0.0
                            except: return 0.0
                        close = _gv('Close')
                        if close <= 0:
                            continue
                        conn.execute("""
                            INSERT INTO price_history
                              (stock_code, date, open, high, low, close, volume,
                               inst_net_buy, frn_net_buy)
                            VALUES (?,?,?,?,?,?,?,0,0)
                            ON CONFLICT(stock_code, date) DO UPDATE SET
                              open=excluded.open, high=excluded.high,
                              low=excluded.low,   close=excluded.close,
                              volume=excluded.volume
                        """, (symbol, row_date.isoformat(),
                              _gv('Open'), _gv('High'), _gv('Low'),
                              close, _gv('Volume')))
                        saved += 1
                    except:
                        pass
                conn.commit()
                latest = conn.execute(
                    "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                if latest:
                    log.info(f"  ✅ {symbol}({name}): {latest[0]} = {latest[1]:,.2f} ({saved}건)")
                total += saved
                time.sleep(0.5)
            except Exception as e:
                log.warning(f"  {symbol}: {e}")
    finally:
        conn.close()

    log.info(f"Yahoo Finance 총 {total}건 저장")

def print_summary():
    """최종 결과 요약"""
    today = date.today().isoformat()
    conn = get_conn()
    try:
        print("\n" + "="*54)
        print("  수집 결과")
        print("="*54)
        for sym, name in [('^KS11','KOSPI'),('^KQ11','KOSDAQ'),
                          ('^IXIC','NASDAQ'),('^GSPC','S&P500'),
                          ('USDKRW=X','USD/KRW'),('^VIX','VIX')]:
            r = conn.execute(
                "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (sym,)
            ).fetchone()
            mark = '✅' if r and r[0] == today else '⏳'
            val  = f"{r[0]} = {r[1]:,.2f}" if r else "없음"
            print(f"  {mark} {name:<10} {val}")

        kr = conn.execute(
            f"SELECT COUNT(*) FROM price_history WHERE date='{today}' AND length(stock_code)=6 AND stock_code GLOB '[0-9]*'"
        ).fetchone()[0]
        print(f"\n  한국종목 오늘({today}): {kr:,}개")
        if kr >= 1000:
            print("  ✅ KRX 전종목 수집 완료")
        elif kr > 0:
            print("  ⚠️  일부만 수집됨 (KRX API 부분 응답)")
        else:
            print("  ❌ KRX 수집 실패 (Yahoo Finance만 적용됨)")
        print("="*54)
        print("  브라우저를 새로고침하세요!")
        print("="*54)
    finally:
        conn.close()

if __name__ == '__main__':
    print("="*54)
    print(f"  오늘 데이터 수동 수집  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("="*54)

    # 1. KRX API로 전종목 수집
    log.info("=== KRX API 수집 시작 ===")
    stk_cnt, idx_cnt = collect_krx_today()

    # 2. Yahoo Finance로 지수/환율 보완
    log.info("=== Yahoo Finance 보완 수집 ===")
    collect_yahoo_indices()

    # 3. 결과 출력
    print_summary()
