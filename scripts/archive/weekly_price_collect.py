"""
주간 주가/수급 데이터 수집 (매주 토요일 실행)
- 이미 데이터가 있는 종목만 최신 1주일치 업데이트
- 상장폐지 종목 자동 제외
"""
import sqlite3, time, logging, yfinance as yf
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler('/Applications/stock_dashboard/weekly_collect.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
DB_PATH = '/Applications/stock_dashboard/stock.db'

def collect_weekly():
    conn = sqlite3.connect(DB_PATH)
    start = datetime.now()

    # 이미 주가 데이터가 있는 종목만 (60일 이상) + 최근 1주일 데이터 없는 경우
    cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    codes = conn.execute("""
        SELECT s.stock_code, s.stock_name,
               MAX(p.date) as last_date,
               COUNT(*) as cnt
        FROM stock_universe s
        JOIN price_history p ON s.stock_code = p.stock_code
        WHERE LENGTH(s.stock_code)=6
          AND s.stock_code GLOB '[0-9]*'
        GROUP BY s.stock_code
        HAVING cnt >= 60
        ORDER BY s.stock_code
    """).fetchall()

    total = len(codes)
    logger.info(f"업데이트 대상: {total}개 종목")
    success = fail = skip = 0

    for i, (code, name, last_date, cnt) in enumerate(codes):
        # 이미 최신이면 스킵
        if last_date and last_date[:10] >= cutoff:
            skip += 1
            continue

        hist = None
        for suffix in ['.KS', '.KQ']:
            try:
                h = yf.Ticker(f'{code}{suffix}').history(period='1mo', timeout=10)
                if not h.empty:
                    hist = h; break
            except: pass

        if hist is None or hist.empty:
            fail += 1
            time.sleep(0.2)
            continue

        saved = 0
        for dt, row in hist.iterrows():
            try:
                conn.execute('''INSERT OR IGNORE INTO price_history
                    (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy)
                    VALUES (?,?,?,?,?,?,?,0,0)''',
                    (code, dt.strftime('%Y-%m-%d'),
                     float(row.Open or 0), float(row.High or 0),
                     float(row.Low or 0), float(row.Close or 0),
                     int(row.Volume or 0)))
                saved += 1
            except: pass

        conn.commit()
        success += 1

        if i % 200 == 0:
            elapsed = (datetime.now()-start).seconds//60
            logger.info(f"[{i+1}/{total}] {code} {name} | 성공:{success} 실패:{fail} 스킵:{skip} | {elapsed}분 경과")

        time.sleep(0.3)

    elapsed = (datetime.now()-start).seconds//60
    conn.close()
    logger.info(f"완료! 성공:{success} 실패:{fail} 스킵:{skip} | {elapsed}분 소요")

if __name__ == '__main__':
    logger.info(f"===== 주간 주가 수집 시작 {datetime.now().strftime('%Y-%m-%d %H:%M')} =====")
    collect_weekly()
