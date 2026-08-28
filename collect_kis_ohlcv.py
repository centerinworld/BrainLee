#!/usr/bin/env python3
"""
collect_kis_ohlcv.py — KIS API로 전종목 OHLCV 수집
=====================================================
용도:
  - 오늘 하루치 수집 (KRX 차단 대체)
  - 지정 기간 누락 데이터 백필

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate

  # 오늘 하루 (기본)
  python3 collect_kis_ohlcv.py

  # 특정 기간 백필
  python3 collect_kis_ohlcv.py --start 20260401 --end 20260506

  # 시총 상위 N종목만 (빠른 테스트)
  python3 collect_kis_ohlcv.py --limit 500

KIS 레이트리밋: 초당 20회 (일반 계정 기준)
6,693종목 × 1호출 = 약 6분 (초당 20회 기준)
"""
import sys, os, sqlite3, time, logging, argparse
from datetime import date, datetime, timedelta
from threading import Thread, Semaphore, Lock
import requests
import json

sys.path.insert(0, '/Applications/stock_dashboard')
os.chdir('/Applications/stock_dashboard')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

DB_PATH = '/Applications/stock_dashboard/stock.db'

# ── KIS 설정 ──────────────────────────────────────────
try:
    import config as _cfg
    KIS_APP_KEY    = _cfg.KIS_APP_KEY
    KIS_APP_SECRET = _cfg.KIS_APP_SECRET
    KIS_URL        = getattr(_cfg, 'KIS_URL', 'https://openapi.koreainvestment.com:9443')
except Exception as e:
    log.error(f"config 로드 실패: {e}")
    sys.exit(1)

RATE_LIMIT  = 18        # 초당 최대 요청 수 (안전 마진 포함)
WORKERS     = 6         # 병렬 스레드 수
BATCH_SIZE  = 300       # DB 커밋 단위
CHUNK_DAYS  = 100       # KIS 1회 최대 조회일수

_rate_sem   = Semaphore(RATE_LIMIT)
_db_lock    = Lock()

_KR_HOLIDAYS_2026 = {
    '2026-01-01','2026-01-27','2026-01-28','2026-01-29','2026-01-30',
    '2026-03-01','2026-05-05','2026-06-06','2026-08-15',
    '2026-09-24','2026-09-25','2026-09-26','2026-10-03','2026-10-09','2026-12-25',
}


# ── 토큰 관리 ──────────────────────────────────────────
_token_cache = {'token': None, 'expiry': 0}

def get_token():
    """KIS Access Token 발급/캐시"""
    now = time.time()
    if _token_cache['token'] and now < _token_cache['expiry']:
        return _token_cache['token']

    # 파일 캐시 먼저 확인
    try:
        if os.path.exists('kis_token.json'):
            with open('kis_token.json') as f:
                d = json.load(f)
                if d.get('access_token') and now < d.get('token_expiry', 0):
                    _token_cache['token'] = d['access_token']
                    _token_cache['expiry'] = d['token_expiry']
                    return _token_cache['token']
    except Exception:
        pass

    # 신규 발급
    url = f"{KIS_URL}/oauth2/tokenP"
    payload = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        data = r.json()
        if 'access_token' in data:
            token = data['access_token']
            expiry = int(now + data.get('expires_in', 86400) - 3600)
            _token_cache['token'] = token
            _token_cache['expiry'] = expiry
            with open('kis_token.json', 'w') as f:
                json.dump({'access_token': token, 'token_expiry': expiry}, f)
            log.info("KIS 토큰 신규 발급 완료")
            return token
        else:
            log.error(f"KIS 토큰 발급 실패: {data.get('msg1', data)}")
            sys.exit(1)
    except Exception as e:
        log.error(f"KIS 토큰 발급 오류: {e}")
        sys.exit(1)


# ── KIS OHLCV 조회 ─────────────────────────────────────
_last_calls = []
_call_lock  = Lock()

def _rate_wait():
    """초당 RATE_LIMIT 요청 제한"""
    with _call_lock:
        now = time.time()
        _last_calls[:] = [t for t in _last_calls if now - t < 1.0]
        if len(_last_calls) >= RATE_LIMIT:
            sleep_time = 1.0 - (now - _last_calls[0]) + 0.01
            if sleep_time > 0:
                time.sleep(sleep_time)
        _last_calls.append(time.time())


def fetch_ohlcv(code, start_yyyymmdd, end_yyyymmdd, token):
    """
    KIS FHKST01010400: 기간별 일봉 OHLCV
    반환: [(date_str, open, high, low, close, volume), ...]
    """
    url = f"{KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010400",
        "custtype": "P",
    }

    start_dt = datetime.strptime(start_yyyymmdd, "%Y%m%d")
    end_dt   = datetime.strptime(end_yyyymmdd,   "%Y%m%d")
    result   = []
    cursor_end = end_dt

    while cursor_end >= start_dt:
        cursor_start = max(start_dt, cursor_end - timedelta(days=CHUNK_DAYS - 1))
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         code,
            "FID_INPUT_DATE_1":       cursor_start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2":       cursor_end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE":    "D",
            "FID_ORG_ADJ_PRC":        "1",   # 수정주가
        }

        _rate_wait()
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            data = r.json()
        except Exception as e:
            log.debug(f"[KIS] {code} 요청 오류: {e}")
            return result

        if data.get("rt_cd") != "0":
            msg = data.get("msg1", "")
            if "초당" in msg or "Rate" in msg.lower():
                time.sleep(1)
            return result

        rows = data.get("output2") or data.get("output") or []
        for r in rows:
            d = r.get("stck_bsop_date", "")
            if not d or len(d) != 8:
                continue
            date_iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            try:
                o = float(r.get("stck_oprc", 0) or 0)
                h = float(r.get("stck_hgpr", 0) or 0)
                l = float(r.get("stck_lwpr", 0) or 0)
                c = float(r.get("stck_clpr", 0) or 0)
                v = float(r.get("acml_vol",  0) or 0)
                if c > 0:
                    result.append((date_iso, o, h, l, c, v))
            except (ValueError, TypeError):
                continue

        if not rows or len(rows) < CHUNK_DAYS:
            break
        cursor_end = cursor_start - timedelta(days=1)

    return result


# ── DB 저장 ────────────────────────────────────────────
def save_batch(records, conn):
    """records: [(code, date, o, h, l, c, v)]"""
    ins = upd = 0
    for (code, date_iso, o, h, l, c, v) in records:
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
            """, (o, h, l, c, v, code, date_iso))
            upd += 1
    conn.commit()
    return ins, upd


# ── 수집 워커 ──────────────────────────────────────────
def worker(codes_chunk, start_str, end_str, token, result_queue, progress_counter, counter_lock):
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")

    batch = []
    local_ins = local_upd = 0

    for code in codes_chunk:
        rows = fetch_ohlcv(code, start_str, end_str, token)
        for (date_iso, o, h, l, c, v) in rows:
            batch.append((code, date_iso, o, h, l, c, v))

        with counter_lock:
            progress_counter[0] += 1

        if len(batch) >= BATCH_SIZE:
            i, u = save_batch(batch, conn)
            local_ins += i
            local_upd += u
            batch = []

    if batch:
        i, u = save_batch(batch, conn)
        local_ins += i
        local_upd += u

    conn.close()
    result_queue.append((local_ins, local_upd))


# ── 메인 ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='KIS API로 전종목 OHLCV 수집')
    parser.add_argument('--start', default=None,  help='시작일 YYYYMMDD (기본: 오늘)')
    parser.add_argument('--end',   default=None,  help='종료일 YYYYMMDD (기본: 오늘)')
    parser.add_argument('--limit', type=int, default=0, help='종목 수 제한 (0=전체)')
    parser.add_argument('--days',  type=int, default=0, help='최근 N일 (start/end 대신)')
    args = parser.parse_args()

    today = date.today()
    today_str = today.strftime("%Y%m%d")

    # 날짜 결정
    if args.days:
        end_str   = today_str
        start_str = (today - timedelta(days=args.days)).strftime("%Y%m%d")
    elif args.start:
        start_str = args.start
        end_str   = args.end or today_str
    else:
        start_str = today_str
        end_str   = today_str

    # 오늘이 거래일인지
    if start_str == end_str == today_str:
        if today.weekday() >= 5:
            log.warning("오늘은 주말 — 수집 불필요")
            return
        if today.isoformat() in _KR_HOLIDAYS_2026:
            log.warning("오늘은 공휴일 — 수집 불필요")
            return

    print("=" * 60)
    print(f"  KIS OHLCV 수집  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  수집 기간: {start_str} ~ {end_str}")
    print("=" * 60)

    # 토큰 발급
    token = get_token()
    log.info("KIS 토큰 준비 완료")

    # 종목 목록
    conn = sqlite3.connect(DB_PATH, timeout=30)
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('유가증권', '코스닥', 'KOSPI', 'KOSDAQ')
          AND length(stock_code) = 6
          AND stock_code GLOB '[0-9]*'
        ORDER BY CAST(COALESCE(market_cap, 0) AS REAL) DESC
    """).fetchall()
    conn.close()

    codes = [r[0] for r in rows]
    if args.limit:
        codes = codes[:args.limit]

    total = len(codes)
    log.info(f"수집 대상: {total}개 종목")

    # 멀티스레드 분할
    chunk_size  = max(1, total // WORKERS)
    chunks      = [codes[i:i+chunk_size] for i in range(0, total, chunk_size)]
    result_queue    = []
    progress_counter = [0]
    counter_lock    = Lock()

    threads = []
    for chunk in chunks:
        t = Thread(target=worker, args=(
            chunk, start_str, end_str, token,
            result_queue, progress_counter, counter_lock
        ))
        t.start()
        threads.append(t)

    # 진행 표시
    t_start = time.time()
    while any(t.is_alive() for t in threads):
        done = progress_counter[0]
        pct  = done / total * 100
        elapsed = time.time() - t_start
        eta = (elapsed / max(done, 1)) * (total - done)
        log.info(f"  진행: {done}/{total} ({pct:.0f}%) | 경과 {elapsed:.0f}s | 예상 남은 {eta:.0f}s")
        time.sleep(20)

    for t in threads:
        t.join()

    total_ins = sum(r[0] for r in result_queue)
    total_upd = sum(r[1] for r in result_queue)
    elapsed   = time.time() - t_start

    # 결과 요약
    print("\n" + "=" * 60)
    print(f"  수집 완료  ({elapsed:.0f}초)")
    print("=" * 60)
    print(f"  신규 삽입: {total_ins:,}건")
    print(f"  갱신:      {total_upd:,}건")

    # 오늘 한국 종목 수 확인
    conn = sqlite3.connect(DB_PATH, timeout=30)
    verify_day = datetime.strptime(end_str, "%Y%m%d").date()
    today_iso = verify_day.isoformat()
    kr_today = conn.execute(f"""
        SELECT COUNT(*) FROM price_history
        WHERE date='{today_iso}' AND close>0
          AND length(stock_code)=6 AND stock_code GLOB '[0-9]*'
    """).fetchone()[0]

    # 샘플 확인
    samples = conn.execute(f"""
        SELECT p.stock_code, u.stock_name, p.close
        FROM price_history p
        LEFT JOIN stock_universe u ON p.stock_code = u.stock_code
        WHERE p.date='{today_iso}' AND p.close>0
          AND length(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
        ORDER BY CAST(COALESCE(u.market_cap,0) AS REAL) DESC
        LIMIT 5
    """).fetchall()
    conn.close()

    print(f"\n  검증일({today_iso}) 한국 종목: {kr_today:,}개")
    if samples:
        print("  시총 상위 5종목 확인:")
        for code, name, close in samples:
            print(f"    {code} {name}: {close:,.0f}원")

    if kr_today >= 2000:
        print("\n  ✅ 전종목 수집 완료! 브라우저를 새로고침하세요.")
    elif kr_today >= 500:
        print(f"\n  ⚠️  {kr_today}종목 수집됨. 일부 누락 — 재실행 권장")
    else:
        print(f"\n  ❌ {kr_today}종목만 수집. KIS API 오류 확인 필요")
    print("=" * 60)


if __name__ == '__main__':
    main()
