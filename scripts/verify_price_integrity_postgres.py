"""Transactional PostgreSQL integration test; isolated schema is rolled back."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from db_utils import connect_stock_db
from price_integrity import ensure_schema,rebuild_views,install_write_guard,assert_research_prices,PriceIntegrityError
from scripts.audit_price_jumps_and_build_canonical import DDL
from price_integrity import native_script
c=connect_stock_db()
try:
    c.execute('CREATE SCHEMA codex_price_integrity_test')
    c.execute('SET LOCAL search_path TO codex_price_integrity_test')
    native_script(c,'''CREATE TABLE price_history(id bigserial PRIMARY KEY,stock_code text,date text,
        open double precision,high double precision,low double precision,close double precision,volume double precision);
        CREATE TABLE price_jump_audit(stock_code text,event_date text,previous_date text,
        previous_close double precision,event_close double precision,classification text,return_usable text);
        CREATE TABLE price_trading_calendar(date text PRIMARY KEY);
        CREATE TABLE price_integrity_quarantine(stock_code text,event_date text,reason text,evidence text,created_at text,
        PRIMARY KEY(stock_code,event_date,reason));''')
    ensure_schema(c);rebuild_views(c);install_write_guard(c)
    c.execute("SELECT set_config('app.price_basis_checked','1',true)")
    c.execute("INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES('005930','2026-01-02',100,100,100,100,10),('005930','2026-01-05',150,150,150,150,10),('005930','2026-01-06',155,155,155,155,10)")
    c.execute("INSERT INTO price_trading_calendar VALUES('2026-01-02'),('2026-01-05'),('2026-01-06')")
    assert all(r[0] is None for r in c.execute('SELECT safe_daily_return FROM canonical_price_returns_v'))
    try:assert_research_prices(c,['005930'],'2026-01-02','2026-01-06')
    except PriceIntegrityError:pass
    else:raise AssertionError('backtest guard failed')
    c.execute("SELECT set_config('app.price_basis_checked','0',true)")
    with c._connection.cursor() as cur:
        cur.execute('SAVEPOINT rejected_legacy_write')
        try:
            cur.execute("UPDATE price_history SET close=99 WHERE date='2026-01-02'")
        except Exception as exc:
            assert 'price integrity guard blocked' in str(exc)
            cur.execute('ROLLBACK TO SAVEPOINT rejected_legacy_write')
            cur.execute('RELEASE SAVEPOINT rejected_legacy_write')
        else:
            raise AssertionError('legacy historical write was not blocked')
    assert c.execute("SELECT close FROM price_history WHERE date='2026-01-02'").fetchone()[0]==100
    c.execute("INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES('^KS11','2026-01-02',100,100,100,100,10)")
    print('PASS: native views, blocked returns, backtest fail-closed, legacy historical write rejected; transaction rolled back')
finally:
    c.rollback();c.close()
