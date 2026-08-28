import sqlite3, time, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
    handlers=[logging.FileHandler('/Applications/stock_dashboard/collect_prices.log'),
              logging.StreamHandler()])
logger = logging.getLogger(__name__)

DB_PATH = '/Applications/stock_dashboard/stock.db'

def collect_all():
    import yfinance as yf
    conn = sqlite3.connect(DB_PATH)
    codes = conn.execute("""
        SELECT s.stock_code, s.stock_name, COALESCE(p.cnt,0) as cnt
        FROM stock_universe s
        LEFT JOIN (
            SELECT stock_code, COUNT(*) as cnt FROM price_history
            WHERE date >= '2024-01-01' GROUP BY stock_code
        ) p ON s.stock_code = p.stock_code
        WHERE COALESCE(p.cnt,0) < 60
          AND LENGTH(s.stock_code)=6 AND s.stock_code GLOB '[0-9]*'
        ORDER BY s.stock_code
    """).fetchall()
    total = len(codes)
    logger.info(f"수집 대상: {total}개")
    success = fail = 0
    for i, (code, name, days) in enumerate(codes):
        hist = None
        for suffix in ['.KS', '.KQ']:
            try:
                h = yf.Ticker(f'{code}{suffix}').history(period='2y', timeout=10)
                if not h.empty:
                    hist = h; break
            except: pass
        if hist is None or hist.empty:
            fail += 1
            time.sleep(0.2)
            continue
        for dt, row in hist.iterrows():
            try:
                conn.execute('''INSERT OR IGNORE INTO price_history
                    (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy)
                    VALUES (?,?,?,?,?,?,?,0,0)''',
                    (code, dt.strftime('%Y-%m-%d'),
                     float(row.Open or 0), float(row.High or 0),
                     float(row.Low or 0), float(row.Close or 0), int(row.Volume or 0)))
            except: pass
        conn.commit()
        success += 1
        if i % 100 == 0:
            logger.info(f"[{i+1}/{total}] {code} {name} (성공:{success} 실패:{fail})")
        time.sleep(0.4)
    conn.close()
    logger.info(f"완료! 성공:{success} 실패:{fail}")

if __name__ == '__main__':
    collect_all()
