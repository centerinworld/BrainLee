#!/usr/bin/env python3
"""
collect_naver_ohlcv_today.py — Naver Finance로 오늘 전종목 OHLCV 수집
=====================================================================
용도: KRX API 차단 환경에서 당일 종가 수집 (장 마감 후 실행)
실행: cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate
      python3 collect_naver_ohlcv_today.py

Naver Finance fchart API 사용:
  https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=3&requestType=0
  XML 응답: <item data="YYYYMMDD|open|high|low|close|volume" />
"""
import sys, os, sqlite3, time, logging, re
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

DB_PATH = '/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'
NAVER_URL = 'https://fchart.stock.naver.com/sise.nhn'
SLEEP_PER_REQ = 0.05   # 요청 간격 (초) — 너무 빠르면 차단
MAX_WORKERS = 8         # 병렬 스레드 수
BATCH_SIZE  = 200       # DB 배치 커밋 단위

_KR_HOLIDAYS_2026 = {
    '2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
    '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
    '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09','2026-12-25',
}

import requests


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    })
    return s


def get_naver_ohlcv(session, code, count=3):
    """Naver Finance fchart에서 최근 count일 OHLCV 반환.
    반환: [(date_str, open, high, low, close, volume), ...]
    """
    try:
        r = session.get(NAVER_URL, params={
            'symbol': code, 'timeframe': 'day',
            'count': count, 'requestType': '0'
        }, timeout=10)
        if r.status_code != 200:
            return []
        # XML 파싱: <item data="20260506|3200|3250|3180|3210|12345" />
        rows = []
        for m in re.finditer(r'data="([^"]+)"', r.text):
            parts = m.group(1).split('|')
            if len(parts) < 6:
                continue
            d_str, o, h, l, c, v = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
            if len(d_str) != 8:
                continue
            date_iso = f'{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}'
            try:
                rows.append((date_iso, float(o), float(h), float(l), float(c), float(v)))
            except ValueError:
                continue
        return rows
    except Exception as e:
        return []


def get_all_codes():
    """stock_universe에서 코스피/코스닥 6자리 종목코드 목록"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        rows = conn.execute("""
            SELECT stock_code FROM stock_universe
            WHERE (market = '유가증권' OR market = '코스닥')
              AND length(stock_code) = 6
              AND stock_code GLOB '[0-9]*'
            ORDER BY market_cap DESC NULLS LAST
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def collect_worker(codes_chunk, today_str, result_list):
    """스레드별 수집 워커"""
    session = make_session()
    local = []
    for code in codes_chunk:
        rows = get_naver_ohlcv(session, code, count=3)
        for date_iso, o, h, l, c, v in rows:
            if date_iso == today_str and c > 0:
                local.append((code, date_iso, o, h, l, c, v))
                break
        time.sleep(SLEEP_PER_REQ)
    result_list.extend(local)


def save_to_db(records):
    """price_history에 UPSERT"""
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    try:
        ins = upd = 0
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i+BATCH_SIZE]
            for (code, date_iso, o, h, l, c, v) in batch:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (stock_code, date, open, high, low, close, volume)
                    VALUES (?,?,?,?,?,?,?)
                """, (code, date_iso, o, h, l, c, v))
                if cur.rowcount > 0:
                    ins += 1
                else:
                    conn.execute("""
                        UPDATE price_history
                        SET open=?, high=?, low=?, close=?, volume=?
                        WHERE stock_code=? AND date=?
                          AND close = 0
                    """, (o, h, l, c, v, code, date_iso))
                    # close>0인 기존 레코드는 덮어쓰지 않음 (KRX 확정값 우선)
                    upd += 1
            conn.commit()
            log.info(f"  배치 {i//BATCH_SIZE+1}: {len(batch)}건 처리 (누적 신규+{ins} 수정{upd})")
        return ins, upd
    finally:
        conn.close()


def main():
    today = date.today()
    today_str = today.isoformat()

    # 오늘이 거래일인지 확인
    if today.weekday() >= 5:
        log.warning(f"오늘은 주말({today_str}) — 수집 불필요")
        return
    if today_str in _KR_HOLIDAYS_2026:
        log.warning(f"오늘은 공휴일({today_str}) — 수집 불필요")
        return

    print("=" * 56)
    print(f"  Naver Finance OHLCV 수집  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  대상 날짜: {today_str}")
    print("=" * 56)

    # 이미 오늘 데이터가 충분히 있으면 스킵
    conn = sqlite3.connect(DB_PATH, timeout=30)
    existing = conn.execute(
        f"SELECT COUNT(*) FROM price_history WHERE date='{today_str}' "
        f"AND close>0 AND length(stock_code)=6 AND stock_code GLOB '[0-9]*'"
    ).fetchone()[0]
    conn.close()
    log.info(f"기존 오늘 데이터: {existing}건")
    if existing >= 2000:
        log.info("오늘 데이터 이미 충분함 — 스킵")
        return

    # 전종목 코드 로드
    codes = get_all_codes()
    log.info(f"수집 대상: {len(codes)}개 종목")

    # 멀티스레드 수집
    chunk_size = max(1, len(codes) // MAX_WORKERS)
    chunks = [codes[i:i+chunk_size] for i in range(0, len(codes), chunk_size)]

    from threading import Thread
    result_list = []
    threads = []
    for chunk in chunks:
        t = Thread(target=collect_worker, args=(chunk, today_str, result_list))
        t.start()
        threads.append(t)

    start = time.time()
    # 진행상황 표시
    total = len(codes)
    while any(t.is_alive() for t in threads):
        elapsed = time.time() - start
        collected = len(result_list)
        pct = collected / max(1, total) * 100
        log.info(f"  수집 중... {collected}/{total}건 ({pct:.0f}%) — {elapsed:.0f}초 경과")
        time.sleep(15)
    for t in threads:
        t.join()

    elapsed = time.time() - start
    log.info(f"수집 완료: {len(result_list)}건 ({elapsed:.0f}초)")

    if not result_list:
        log.warning("수집된 데이터 없음 — 장이 아직 마감되지 않았거나 Naver API 오류")
        return

    # DB 저장
    log.info("DB 저장 중...")
    ins, upd = save_to_db(result_list)

    print("\n" + "=" * 56)
    print("  수집 결과")
    print("=" * 56)
    print(f"  오늘({today_str}) 수집된 종목: {len(result_list)}개")
    print(f"  신규 삽입: {ins}건 / 기존 수정: {upd}건")

    # 검증
    conn = sqlite3.connect(DB_PATH, timeout=30)
    final = conn.execute(
        f"SELECT COUNT(*) FROM price_history WHERE date='{today_str}' "
        f"AND close>0 AND length(stock_code)=6 AND stock_code GLOB '[0-9]*'"
    ).fetchone()[0]
    conn.close()
    print(f"  최종 오늘 종목 수: {final}개")
    if final >= 2000:
        print("  ✅ 전종목 수집 완료")
    elif final >= 500:
        print("  ⚠️  일부 수집됨 (Naver 일시 응답 지연)")
    else:
        print("  ❌ 수집 부족 — 장이 마감 전이거나 Naver API 오류")
    print("=" * 56)
    print("  브라우저를 새로고침하세요!")
    print("=" * 56)


if __name__ == '__main__':
    main()
