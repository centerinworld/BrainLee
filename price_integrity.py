"""Price-basis safety policy shared by audit, ingestion and backtests.

A price-limit breach is a review candidate, never a legal conclusion. A source
agreement does not establish economic return across a capital action.
"""
from __future__ import annotations
import hashlib
import json
import logging
import math
from datetime import date, datetime, timedelta

POLICY_VERSION = '2026-09-11.2'


def price_band(day):
    return (0.85, 1.15) if str(day)[:10] < '2015-06-15' else (0.70, 1.30)


def outside_band(previous, current, day):
    if not previous or not current or not math.isfinite(previous + current):
        return True
    lo, hi = price_band(day)
    # Numerical tolerance only, not a relaxation of the price limit.
    return current / previous < lo - 1e-9 or current / previous > hi + 1e-9


def invalid_ohlcv(o, h, l, c, v):
    if any(x is None or not math.isfinite(float(x)) for x in (o, h, l, c, v)):
        return True
    if c <= 0 or v < 0 or min(o, h, l) < 0:
        return True
    if v == 0 and o == h == l == 0:
        return False  # supplied suspension marker, never a tradable candle
    # Integer adjusted KRW series can truncate each OHLC independently by 1 won.
    tol = 1.0 if all(float(x).is_integer() for x in (o,h,l,c)) else 1e-8
    return min(o, h, l) <= 0 or h + tol < max(o, l, c) or l - tol > min(o, h, c)


def verification_fingerprint(row):
    """Fingerprint the audit row's underlying facts, not its derived classification.

    'classification' is excluded on purpose: an external verifier writes it back
    onto price_jump_audit as its own output, so including it would make a
    verifier's own write look like a changed input on the very next run and
    force endless re-verification of rows nothing actually changed about.
    """
    fields = ('stock_code','event_date','previous_date','previous_close','event_close',
              'price_ratio','public_previous_close','public_event_close','public_price_ratio',
              'matched_event_type','matched_report_name')
    return hashlib.sha256(json.dumps([POLICY_VERSION] + [row[k] for k in fields],
                                    default=str,ensure_ascii=False).encode()).hexdigest()


def manifest_repair_status(conn, code, day, expected_old, expected_new):
    """Revalidate a reviewed repair manifest against both live and source rows."""
    def same(left, right):
        return len(left) == len(right) and all(
            a is not None and b is not None and abs(float(a)-float(b)) <= 1e-6
            for a, b in zip(left, right)
        )
    current = conn.execute(
        'SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?',
        (code, day),
    ).fetchone()
    if current and same(tuple(current), expected_new):
        return 'already_applied'
    if not current:
        return 'missing_live_row'
    if not same(tuple(current), expected_old):
        return 'live_row_changed_since_review'
    source = conn.execute(
        'SELECT open,high,low,close,volume FROM naver_price_history_backfill WHERE stock_code=? AND date=?',
        (code, day),
    ).fetchone()
    if not source or not same(tuple(source), expected_new):
        return 'source_row_changed_since_review'
    if invalid_ohlcv(*expected_new):
        return 'invalid_replacement_ohlcv'
    return 'ready'


def manifest_gap_fill_status(conn, code, day, expected_new):
    """Require the reviewed row to remain a real interior market-data gap."""
    if conn.execute(
        'SELECT 1 FROM price_history WHERE stock_code=? AND substr(date,1,10)=?', (code, day)
    ).fetchone():
        return 'already_present'
    source = conn.execute(
        'SELECT open,high,low,close,volume FROM naver_price_history_backfill WHERE stock_code=? AND date=?',
        (code, day),
    ).fetchone()
    if not source or any(abs(float(a)-float(b)) > 1e-6 for a,b in zip(tuple(source), expected_new)):
        return 'source_row_changed_since_review'
    if invalid_ohlcv(*expected_new):
        return 'invalid_replacement_ohlcv'
    if not conn.execute('SELECT 1 FROM price_trading_calendar WHERE date=?',(day,)).fetchone():
        return 'not_in_market_calendar'
    bounds = conn.execute(
        'SELECT MIN(date) FILTER(WHERE date<?),MAX(date) FILTER(WHERE date>?) FROM price_history WHERE stock_code=?',
        (day,day,code),
    ).fetchone()
    if not bounds or not bounds[0] or not bounds[1]:
        return 'not_an_interior_gap'
    return 'ready'


def native_script(conn, script):
    """Do not route PostgreSQL schema migrations through SQLite DDL skipping."""
    if hasattr(conn, '_connection'):
        with conn._connection.cursor() as cur:
            cur.execute(script)
    else:
        conn.executescript(script)


TABLES = """
CREATE TABLE IF NOT EXISTS price_trading_calendar (date TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS price_integrity_quarantine (
 stock_code TEXT NOT NULL, event_date TEXT NOT NULL, reason TEXT NOT NULL,
 evidence TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 PRIMARY KEY(stock_code,event_date,reason)
);
CREATE TABLE IF NOT EXISTS price_verification_state (
 stock_code TEXT NOT NULL,event_date TEXT NOT NULL,external_source TEXT NOT NULL,
 input_fingerprint TEXT NOT NULL,verified_at TEXT NOT NULL,
 PRIMARY KEY(stock_code,event_date,external_source)
);
CREATE TABLE IF NOT EXISTS price_coverage_gap_reviewed (
 stock_code TEXT NOT NULL, event_date TEXT NOT NULL, previous_date TEXT NOT NULL,
 reason TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL,
 PRIMARY KEY(stock_code,event_date,previous_date)
);
CREATE TABLE IF NOT EXISTS price_ingestion_quarantine (
 batch_id TEXT PRIMARY KEY, stock_code TEXT NOT NULL, source TEXT NOT NULL,
 reason TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def ensure_schema(conn):
    native_script(conn, TABLES)


def refresh_calendar(conn, full=False):
    # An observed market calendar avoids confusing weekends/long holidays with
    # missing observations - a real trading day is one where a broad swath of
    # equities actually printed.
    #
    # 2026-09-20 found live: the old version also accepted a date on ^KS11's
    # presence alone (`OR SUM(...^KS11...)>0`), no equity count required. On
    # 2026-07-17 (a real KR market holiday - confirmed against pykrx/KRX
    # official: zero individual stocks traded, only ^KS11/^KQ11/FX/overseas
    # index rows exist for that date) the index collector still wrote a
    # closing print for ^KS11 with nonzero close and volume - not a stale
    # zero-volume carry-forward, a fully-formed-looking row - so the ^KS11
    # branch alone made this table wrongly treat that holiday as a trading
    # day. Every KOSPI/KOSDAQ stock then looked like it had a "coverage_gap"
    # on the next real trading day after it (2,689 rows just from this one
    # date, ~90% of this table's entire "coverage_gap" pool at the time).
    # COUNT(DISTINCT stock_code)>=100 alone is a robust signal on its own
    # (any real trading day clears this trivially) and isn't fooled by one
    # symbol's collector behaving oddly, so the ^KS11-alone branch is
    # dropped rather than tightened with another special case.
    #
    # 2026-09-24: default is now incremental (only re-scan dates within 45 days of
    # the calendar's latest entry). The old unconditional full-table GROUP BY over
    # 10M+ rows hit the Postgres statement timeout on every audit run, so the
    # whole price_jump_audit rebuild never completed. The INSERT is idempotent
    # (ON CONFLICT DO NOTHING), so the overlap window is harmless; pass
    # full=True after a historical backfill that could have added old trading days.
    lower_bound = None
    if not full:
        latest = conn.execute("SELECT MAX(date) FROM price_trading_calendar").fetchone()
        if latest and latest[0]:
            lower_bound = (datetime.strptime(str(latest[0])[:10], '%Y-%m-%d') - timedelta(days=45)).strftime('%Y-%m-%d')
    where = "close>0" + (" AND date>=?" if lower_bound else "")
    conn.execute(f"""INSERT INTO price_trading_calendar(date)
        SELECT substr(date,1,10) FROM price_history
        WHERE {where} GROUP BY substr(date,1,10)
        HAVING COUNT(DISTINCT stock_code)>=100
        ON CONFLICT(date) DO NOTHING""", (lower_bound,) if lower_bound else ())


def rebuild_views(conn):
    numeric = "length(p.stock_code)=6 AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
    if hasattr(conn,'_connection'):
        numeric = "p.stock_code ~ '^[0-9]{6}$'"
    tolerance = "CASE WHEN p.open=ROUND(p.open) AND p.high=ROUND(p.high) AND p.low=ROUND(p.low) AND p.close=ROUND(p.close) THEN 1.0 ELSE 0.00000001 END"
    # Keep all observations in LAG, including invalid/zero prices. No compressed
    # time series and no skip-over of a quarantined candle.
    sql = f"""
CREATE OR REPLACE VIEW price_history_quality_v AS
WITH p AS (
 SELECT ph.*, LAG(close) OVER(PARTITION BY stock_code ORDER BY date) prev_close,
 LAG(date) OVER(PARTITION BY stock_code ORDER BY date) previous_date
 FROM price_history ph
)
SELECT p.*,
 CASE WHEN prev_close>0 THEN close/prev_close END daily_price_ratio,
 CASE
 WHEN close IS NULL OR close<=0 OR close>1e100 OR volume IS NULL OR volume<0
   OR open IS NULL OR high IS NULL OR low IS NULL
   OR open<0 OR high<0 OR low<0 OR open>1e100 OR high>1e100 OR low>1e100 OR volume>1e100
   OR (NOT(volume=0 AND open=0 AND high=0 AND low=0) AND
       (open<=0 OR high<=0 OR low<=0 OR high+({tolerance})<open OR high+({tolerance})<close OR high+({tolerance})<low OR low-({tolerance})>open OR low-({tolerance})>close))
 THEN 'invalid_ohlcv'
 WHEN EXISTS(SELECT 1 FROM price_integrity_quarantine q
      WHERE q.stock_code=p.stock_code AND q.event_date=substr(p.date,1,10)
        AND q.reason<>'unverified_historical_write') THEN 'quarantined_basis'
 WHEN previous_date IS NOT NULL AND (prev_close IS NULL OR prev_close<=0) THEN 'invalid_previous_price'
 WHEN ({numeric}) AND previous_date IS NOT NULL AND EXISTS(
      SELECT 1 FROM price_trading_calendar t WHERE t.date>substr(p.previous_date,1,10)
          AND t.date<substr(p.date,1,10)) THEN 'coverage_gap'
 WHEN ({numeric}) AND prev_close>0 AND
   (close/prev_close < CASE WHEN substr(p.date,1,10)<'2015-06-15' THEN 0.85 ELSE 0.70 END - 0.000000001
    OR close/prev_close > CASE WHEN substr(p.date,1,10)<'2015-06-15' THEN 1.15 ELSE 1.30 END + 0.000000001)
 THEN 'unexplained_jump'
 WHEN volume=0 AND open=0 AND high=0 AND low=0 THEN 'suspended'
 WHEN previous_date IS NULL THEN 'insufficient_history'
 ELSE 'normal' END quality_status
FROM p;
CREATE OR REPLACE VIEW canonical_price_history_v AS
SELECT q.*,
 CASE WHEN q.quality_status NOT IN ('normal','insufficient_history','unexplained_jump') THEN q.quality_status
      WHEN a.event_date IS NOT NULL AND (a.event_close<>q.close OR a.previous_close<>q.prev_close
           OR a.previous_date<>substr(q.previous_date,1,10)) THEN 'stale_audit'
      ELSE COALESCE(a.classification,q.quality_status) END canonical_quality,
 CASE WHEN q.quality_status NOT IN ('normal','insufficient_history') THEN 0
      WHEN a.event_date IS NOT NULL THEN 0
      ELSE 1 END return_usable,
 'price_history' selected_series,'adjusted_intended_mixed_risk' price_basis
FROM price_history_quality_v q
LEFT JOIN price_jump_audit a ON a.stock_code=q.stock_code AND a.event_date=substr(q.date,1,10);
CREATE OR REPLACE VIEW canonical_price_returns_v AS
WITH x AS (
 SELECT c.*,LAG(return_usable) OVER(PARTITION BY stock_code ORDER BY date) previous_return_usable
 FROM canonical_price_history_v c
)
SELECT x.*,prev_close canonical_prev_close,
 CASE WHEN return_usable=1 AND previous_return_usable=1 AND prev_close>0
 THEN close/prev_close-1 END safe_daily_return FROM x;
"""
    if not hasattr(conn, '_connection'):
        # SQLite has no CREATE OR REPLACE VIEW.  Keep the in-memory/test path
        # equivalent to PostgreSQL by dropping dependants first and rebuilding
        # the same three views with ordinary CREATE VIEW statements.
        sql = (
            "DROP VIEW IF EXISTS canonical_price_returns_v;\n"
            "DROP VIEW IF EXISTS canonical_price_history_v;\n"
            "DROP VIEW IF EXISTS price_history_quality_v;\n"
            + sql.replace("CREATE OR REPLACE VIEW", "CREATE VIEW")
        )
    native_script(conn, sql)


class PriceIntegrityError(ValueError):
    pass


def research_price_issues(
    conn, codes, start, end, *,
    allow_confirmed_corporate_actions=False,
):
    """Return persisted row-level issues so callers can apply temporal masks."""
    if not codes:
        return []
    bad = []
    code_list = list(codes)
    for offset in range(0, len(code_list), 100):
        batch = code_list[offset:offset + 100]
        marks = ','.join('?' for _ in batch)
        if hasattr(conn, '_connection'):
            ca_clause = ""
            if allow_confirmed_corporate_actions:
                ca_clause = """AND NOT (
                    a.classification='confirmed_corporate_action' AND EXISTS (
                      SELECT 1 FROM corporate_action_events c
                      WHERE c.stock_code=a.stock_code
                        AND substr(c.event_date,1,10)=substr(a.event_date,1,10)
                        AND c.adjustment_status='factor_confirmed'
                        AND c.backward_price_factor IS NOT NULL))"""
            bad.extend(conn.execute(f"""SELECT a.stock_code,a.event_date,a.classification
                FROM price_jump_audit a
                WHERE a.stock_code IN ({marks})
                  AND a.event_date>=? AND a.event_date<=?
                  AND a.return_usable=0 AND a.classification<>'suspended'
                  {ca_clause}
                ORDER BY a.stock_code,a.event_date""", (*batch, start, end)).fetchall())
        else:
            bad.extend(conn.execute(f"""SELECT stock_code,date,canonical_quality FROM canonical_price_history_v
                WHERE stock_code IN ({marks}) AND date>=? AND date<=?
                  AND return_usable=0 AND canonical_quality<>'suspended'
                ORDER BY stock_code,date""", (*batch, start, end)).fetchall())
    return bad


def assert_research_prices(
    conn, codes, start, end, *, exclude=False,
    allow_confirmed_corporate_actions=False,
):
    """Refuse to silently use unverified prices in a return/backtest calculation.

    2026-09-20 소유자 지시로 정책 변경: 기존엔 후보 유니버스 중 단 한 종목·하루라도
    문제 있으면 전체 백테스트를 막았다("dropping troubled symbols would create
    selection bias"라는 원래 취지 자체는 맞다 - 조용히 종목을 빼면 생존편향이
    생긴다). 그런데 실측 결과 시총 500억+ KOSPI/KOSDAQ 전체·11개월 구간처럼
    현실적인 유니버스는 확정된 기업행위(confirmed_corporate_action)나 조사 중인
    건(unresolved_active_common) 몇 건만 있어도 사실상 항상 막혀, "유니버스가
    넓은 전략은 재실행이 불가능하다"는 구조적 문제가 됐다(2026-09-12 핸드오프
    문서에 v4 사례로 이미 기록됨).

    exclude=False(기본값)면 예전과 동일하게 예외를 던진다(기존 호출부와 완전히
    같은 동작 유지). exclude=True를 넘기면 대신 문제 있는 종목 집합을 반환하고,
    호출부가 그 종목들만 유니버스에서 빼고 계속하게 한다 - 조용한 생존편향을
    피하려고, 뺀 종목과 사유를 호출부가 반드시 로그/run_spec에 남기게
    (backtest_common.py/base.py의 실제 호출부 참고) 강제하지는 않지만 그 용도로
    쓰라고 반환값 자체를 상세하게(사유 포함) 준다.
    """
    bad = research_price_issues(
        conn, codes, start, end,
        allow_confirmed_corporate_actions=allow_confirmed_corporate_actions,
    )
    if not bad:
        return set() if exclude else None
    if not exclude:
        raise PriceIntegrityError('Price integrity blocked (including indicator warmup): '+
                                  '; '.join(f'{r[0]} {r[1]} {r[2]}' for r in bad[:10]))
    excluded = {r[0] for r in bad}
    reasons = sorted({f'{r[0]} {r[1]} {r[2]}' for r in bad})[:20]
    logging.getLogger(__name__).warning(
        'assert_research_prices: excluding %d/%d candidate stocks with unverified prices '
        'in [%s, %s] rather than blocking the whole run - %s%s',
        len(excluded), len(codes), start, end, '; '.join(reasons),
        f' (+{len(bad)-20} more)' if len(bad) > 20 else '',
    )
    return excluded


def gate_price_batch(conn, code, rows, source, *, today=None, provisional_days=0):
    """Rows: date, open, high, low, close, volume. Stage incompatible batches.

    Historical overlap must agree before a partial batch can be spliced into a
    stored series. A full basis replacement belongs to the repair workflow.

    provisional_days (official end-of-day sources only): stored rows dated within
    this many calendar days may be intraday/pre-open placeholders written by the
    live price ingest. A difference there that stays inside the daily price-limit
    band is a correction, not a basis change, so it does not block the batch.
    2026-10-02: without this, one placeholder close made every later official batch
    look like a basis mismatch and the wrong close carried forward day after day
    (~1,000 stocks/day quarantined, e.g. 172670 10-01 14,110 vs official 14,210).
    """
    today = today or date.today().isoformat()
    provisional_from = (date.fromisoformat(today) - timedelta(days=provisional_days)).isoformat() if provisional_days else None
    rows = sorted(rows, key=lambda r: str(r[0])[:10])
    if not rows:
        return True
    reason = None
    if len({str(r[0])[:10] for r in rows}) != len(rows):
        reason = 'duplicate_input_dates'
    if any(str(r[0])[:10] > today or invalid_ohlcv(*r[1:6]) for r in rows):
        reason = 'invalid_ohlcv_or_future_date'
    existing = {str(r[0])[:10]: float(r[1]) if r[1] is not None else None for r in conn.execute(
        'SELECT date,close FROM price_history WHERE stock_code=? AND date>=? AND date<=?',
        (code,str(rows[0][0])[:10],str(rows[-1][0])[:10]))}
    historical = [r for r in rows if str(r[0])[:10] < today]
    overlap = [r for r in historical if str(r[0])[:10] in existing and existing[str(r[0])[:10]]]
    def _basis_mismatch(r):
        d = str(r[0])[:10]
        stored = existing[d]
        if abs(float(r[4])/stored-1) <= 0.005:
            return False
        return not (provisional_from and d >= provisional_from and not outside_band(stored, float(r[4]), d))
    if any(_basis_mismatch(r) for r in overlap):
        reason = 'historical_overlap_basis_mismatch'
    bounds = conn.execute('SELECT MIN(date),MAX(date) FROM price_history WHERE stock_code=?',(code,)).fetchone()
    if bounds and bounds[0] and historical and not overlap:
        reason = reason or 'historical_batch_without_overlap'
    # Test both seams. Agreement on the internal overlap alone is insufficient.
    prev = conn.execute('SELECT date,close FROM price_history WHERE stock_code=? AND date<? ORDER BY date DESC LIMIT 1',
                        (code,str(rows[0][0])[:10])).fetchone()
    nxt = conn.execute('SELECT date,close FROM price_history WHERE stock_code=? AND date>? ORDER BY date LIMIT 1',
                       (code,str(rows[-1][0])[:10])).fetchone()
    if code.isdigit() and len(code)==6:
        if prev and outside_band(prev[1],rows[0][4],rows[0][0]):
            reason = reason or 'left_boundary_review'
        if nxt and outside_band(rows[-1][4],nxt[1],nxt[0]):
            reason = reason or 'right_boundary_review'
    if reason:
        payload = json.dumps(rows,default=str,ensure_ascii=False)
        batch = hashlib.sha256((code+source+payload).encode()).hexdigest()
        conn.execute('''INSERT INTO price_ingestion_quarantine
            (batch_id,stock_code,source,reason,payload,created_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(batch_id) DO NOTHING''',
            (batch,code,source,reason,payload,datetime.now().isoformat(timespec='seconds')))
        return False
    if hasattr(conn, '_connection'):
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    return True


def gate_gap_fill_row(conn, code, day, row, source):
    """Validate one isolated missing/placeholder trading day against its neighbors.

    gate_price_batch() requires the batch to overlap an existing, already-valid
    close on at least one of its own dates - that is the right bar for a
    multi-day reload, but it makes every genuine single-day gap fill (the date
    has no valid close yet, by definition) fail with
    'historical_batch_without_overlap' even when the value is fine. This is the
    gap-fill-specific counterpart: no overlap is required, but the two
    surrounding trading days (whichever already have a valid close) must both
    stay inside the price-limit band relative to this new value. day: 'YYYY-MM-DD'.
    row: open, high, low, close, volume.
    """
    today = date.today().isoformat()
    reason = None
    if day > today or invalid_ohlcv(*row):
        reason = 'invalid_ohlcv_or_future_date'
    existing = conn.execute(
        'SELECT close,volume FROM price_history WHERE stock_code=? AND date=?', (code, day)
    ).fetchone()
    if existing and existing[0] and existing[0] > 0 and existing[1]:
        # Already has a real observation - overwriting it is a basis repair, not a gap fill.
        reason = reason or 'not_a_gap_use_repair_workflow'
    if not reason and code.isdigit() and len(code) == 6:
        prev = conn.execute(
            'SELECT date,close FROM price_history WHERE stock_code=? AND date<? AND close>0 '
            'ORDER BY date DESC LIMIT 1', (code, day)
        ).fetchone()
        nxt = conn.execute(
            'SELECT date,close FROM price_history WHERE stock_code=? AND date>? AND close>0 '
            'ORDER BY date LIMIT 1', (code, day)
        ).fetchone()
        if prev and outside_band(prev[1], row[3], day):
            reason = 'left_boundary_review'
        if nxt and outside_band(row[3], nxt[1], nxt[0]):
            reason = reason or 'right_boundary_review'
    if reason:
        payload = json.dumps({'date': day, 'row': list(row)}, default=str, ensure_ascii=False)
        batch = hashlib.sha256((code + source + day + payload).encode()).hexdigest()
        conn.execute('''INSERT INTO price_ingestion_quarantine
            (batch_id,stock_code,source,reason,payload,created_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(batch_id) DO NOTHING''',
            (batch, code, source, reason, payload, datetime.now().isoformat(timespec='seconds')))
        return False
    if hasattr(conn, '_connection'):
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    return True


WRITE_GUARD_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION guard_historical_price_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.close IS NULL OR NEW.close <= 0 THEN
   RAISE EXCEPTION USING
     ERRCODE='P0001',
     MESSAGE=format('price integrity guard blocked non-positive close (%s) for %s on %s',
                    COALESCE(NEW.close::text,'NULL'), NEW.stock_code, substr(NEW.date,1,10)),
     HINT='A close<=0 row is never a valid observation; do not insert it.';
 END IF;
 IF NEW.stock_code ~ '^[0-9]{6}$' AND (
      NEW.close <> ROUND(NEW.close) OR NEW.open <> ROUND(NEW.open)
      OR NEW.high <> ROUND(NEW.high) OR NEW.low <> ROUND(NEW.low)) THEN
   RAISE EXCEPTION USING
     ERRCODE='P0001',
     MESSAGE=format('price integrity guard blocked non-integer OHLC (o=%s h=%s l=%s c=%s) for %s on %s',
                    NEW.open::text, NEW.high::text, NEW.low::text, NEW.close::text,
                    NEW.stock_code, substr(NEW.date,1,10)),
     HINT='KRX prices are whole won; fractional values are adjusted/interpolated series (2026-03-31~04-07 yfinance auto_adjust incident). Applies even when app.price_basis_checked=1.';
 END IF;
 IF COALESCE(current_setting('app.price_basis_checked',true),'')='1' THEN RETURN NEW; END IF;
 IF NEW.stock_code !~ '^[0-9]{6}$' THEN RETURN NEW; END IF;
 IF substr(NEW.date,1,10) = CURRENT_DATE::text THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN
   IF ROW(NEW.open,NEW.high,NEW.low,NEW.close,NEW.volume) IS NOT DISTINCT FROM
      ROW(OLD.open,OLD.high,OLD.low,OLD.close,OLD.volume) THEN RETURN NEW; END IF;
 END IF;
 RAISE EXCEPTION USING
   ERRCODE='P0001',
   MESSAGE=format('price integrity guard blocked unverified %s for %s on %s',
                  TG_OP,NEW.stock_code,substr(NEW.date,1,10)),
   HINT='Validate the batch with gate_price_batch or use the snapshot repair transaction.';
END $$;
"""


def install_write_guard(conn):
    """Block legacy historical writers that bypass the validated ingestion path.

    A logging-only AFTER trigger allowed the corrupting statement to commit.  A
    BEFORE trigger is deliberately fail-closed.  PostgreSQL cannot both retain a
    quarantine row and reject the surrounding statement atomically, so rejected
    attempts surface as an exception in the writer's own logs/monitoring.
    """
    if not hasattr(conn, '_connection'):
        return
    native_script(conn, WRITE_GUARD_FUNCTION_SQL + """
DROP TRIGGER IF EXISTS price_history_basis_write_guard ON price_history;
CREATE TRIGGER price_history_basis_write_guard BEFORE INSERT OR UPDATE OF open,high,low,close,volume
 ON price_history FOR EACH ROW EXECUTE FUNCTION guard_historical_price_write();
""")
