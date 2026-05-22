#!/usr/bin/env python3
"""
refresh_today.py — 오늘 데이터 즉시 갱신 스크립트
=====================================================
실행: python3 /Applications/stock_dashboard/refresh_today.py

작업:
  1. 오늘 price_history에 있는 May4 복사본 데이터 삭제
  2. Yahoo Finance로 지수/환율 최신값 갱신 (KOSPI, NASDAQ 등)
  3. 갱신 결과 출력
"""
import sys, os, sqlite3, time
from datetime import date, datetime, timedelta

sys.path.insert(0, '/Applications/stock_dashboard')
os.chdir('/Applications/stock_dashboard')

DB_PATH = '/Applications/stock_dashboard/stock.db'

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn

def step1_cleanup_bad_data():
    """오늘 날짜에 전일 복사본 데이터 삭제"""
    today = date.today().isoformat()
    yesterday_biz = None  # 직전 영업일 계산

    # 직전 영업일 찾기 (공휴일 제외)
    _KR_HOLIDAYS_2026 = {
        '2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
        '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
        '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09',
        '2026-12-25',
    }
    for i in range(1, 8):
        d = (date.today() - timedelta(days=i))
        if d.weekday() < 5 and d.isoformat() not in _KR_HOLIDAYS_2026:
            yesterday_biz = d.isoformat()
            break

    print(f"[1/3] 오늘({today}) 잘못된 데이터 정리...")
    print(f"  직전 영업일: {yesterday_biz}")

    if not yesterday_biz:
        print("  직전 영업일을 찾을 수 없음. 스킵.")
        return

    conn = get_conn()
    try:
        # 오늘 데이터 중 직전 영업일과 open/close가 동일한 것 삭제 (복사본)
        deleted = conn.execute(f"""
            DELETE FROM price_history
            WHERE date = '{today}'
              AND stock_code NOT LIKE '%^%'
              AND stock_code NOT LIKE 'GC%'
              AND stock_code NOT LIKE 'CL%'
              AND stock_code NOT LIKE '%=%'
              AND stock_code IN (
                SELECT p6.stock_code
                FROM price_history p6
                JOIN price_history p_prev
                  ON p6.stock_code = p_prev.stock_code
                 AND p_prev.date = '{yesterday_biz}'
                WHERE p6.date = '{today}'
                  AND p6.open   = p_prev.open
                  AND p6.close  = p_prev.close
                  AND ABS(COALESCE(p6.volume,0) - COALESCE(p_prev.volume,0))
                      / (COALESCE(p_prev.volume,0) + 1.0) < 0.02
              )
        """).rowcount
        conn.commit()
        print(f"  ✅ 복사본 데이터 {deleted}건 삭제 완료")

        # 남은 오늘 데이터 확인
        remain = conn.execute(f"SELECT COUNT(*) FROM price_history WHERE date='{today}'").fetchone()[0]
        print(f"  남은 오늘 데이터: {remain}건 (실제 오늘 수집된 것)")
    finally:
        conn.close()

def step2_refresh_indices():
    """Yahoo Finance로 지수/환율 최신값 다운로드"""
    print("\n[2/3] Yahoo Finance 지수/환율 갱신...")

    try:
        import yfinance as yf
    except ImportError:
        print("  ⚠️  yfinance 미설치. pip install yfinance --break-system-packages")
        return

    # 오늘/어제 날짜 (Yahoo Finance 다운로드용)
    today = date.today()
    tomorrow = today + timedelta(days=1)
    start_dt = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    end_dt   = tomorrow.strftime('%Y-%m-%d')

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

    _KR_HOLIDAYS_2026 = {
        '2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
        '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
        '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09',
        '2026-12-25',
    }
    _KR_SYMS = {'^KS11', '^KQ11', '^KS200', '^KQ150'}

    conn = get_conn()
    try:
        saved_total = 0
        for symbol, name in SYMBOLS.items():
            try:
                df = yf.download(symbol, start=start_dt, end=end_dt,
                                 interval='1d', progress=False, auto_adjust=True)
                if df is None or df.empty:
                    print(f"  {symbol}({name}): 데이터 없음")
                    continue

                # MultiIndex 컬럼 처리
                if hasattr(df.columns, 'get_level_values'):
                    try:
                        df.columns = df.columns.get_level_values(0)
                    except Exception:
                        pass

                rows_saved = 0
                for ts, row in df.iterrows():
                    try:
                        row_date = ts.date() if hasattr(ts, 'date') else date.fromisoformat(str(ts)[:10])

                        # 한국 공휴일/주말에 한국 지수 저장 금지
                        if symbol in _KR_SYMS:
                            if row_date.weekday() >= 5:
                                continue
                            if row_date.isoformat() in _KR_HOLIDAYS_2026:
                                continue

                        def _gv(col):
                            v = row.get(col, 0)
                            if hasattr(v, 'iloc'): v = v.iloc[0]
                            try: return float(v) if v is not None else 0.0
                            except: return 0.0

                        close_v = _gv('Close')
                        if close_v <= 0:
                            continue

                        date_str = row_date.isoformat()
                        # UPSERT
                        conn.execute("""
                            INSERT INTO price_history
                              (stock_code, date, open, high, low, close, volume,
                               inst_net_buy, frn_net_buy)
                            VALUES (?,?,?,?,?,?,?,0,0)
                            ON CONFLICT(stock_code, date) DO UPDATE SET
                              open  = excluded.open,
                              high  = excluded.high,
                              low   = excluded.low,
                              close = excluded.close,
                              volume= excluded.volume
                        """, (
                            symbol, date_str,
                            _gv('Open'), _gv('High'), _gv('Low'),
                            close_v, _gv('Volume')
                        ))
                        rows_saved += 1
                    except Exception as e:
                        pass

                conn.commit()
                # 최신값 조회
                latest = conn.execute(
                    "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                if latest:
                    print(f"  ✅ {symbol}({name}): {latest['date']} close={latest['close']:.2f} ({rows_saved}건 저장)")
                saved_total += rows_saved
                time.sleep(0.3)

            except Exception as e:
                print(f"  ⚠️  {symbol}({name}): {e}")

        print(f"  총 {saved_total}건 저장 완료")
    finally:
        conn.close()

def step3_summary():
    """갱신 후 현황 출력"""
    print("\n[3/3] 갱신 결과 확인...")
    today = date.today().isoformat()

    conn = get_conn()
    try:
        for sym, name in [('^KS11','KOSPI'), ('^KQ11','KOSDAQ'),
                          ('^IXIC','NASDAQ'), ('^GSPC','S&P500'),
                          ('USDKRW=X','USD/KRW'), ('^VIX','VIX')]:
            r = conn.execute(
                "SELECT date, close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (sym,)
            ).fetchone()
            if r:
                is_today = '✅' if r['date'] == today else '⏳'
                print(f"  {is_today} {name}({sym}): {r['date']} = {r['close']:,.2f}")
            else:
                print(f"  ❌ {name}({sym}): 데이터 없음")

        # 오늘 한국 종목 수
        kr_today = conn.execute(
            f"SELECT COUNT(*) FROM price_history WHERE date='{today}' AND length(stock_code)=6 AND stock_code GLOB '[0-9]*'"
        ).fetchone()[0]
        print(f"\n  오늘({today}) 한국 종목 OHLCV: {kr_today}개")
        if kr_today < 100:
            print("  ℹ️  KRX 확정 데이터는 오늘 18:00 이후 자동 수집됩니다.")

    finally:
        conn.close()

    print("\n======================================================")
    print("  완료! 서버를 재시작하면 변경사항이 반영됩니다:")
    print("  bash /Applications/stock_dashboard/fix_db_and_restart.sh")
    print("======================================================")

if __name__ == '__main__':
    print("=" * 56)
    print("  오늘 데이터 즉시 갱신")
    print(f"  실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 56)

    step1_cleanup_bad_data()
    step2_refresh_indices()
    step3_summary()
