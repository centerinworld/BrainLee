"""Postgres fixture test for guard_historical_price_write() (HANDOFF §16 C-e).

Runs the real trigger function on a TEMP table inside a transaction that is always rolled back,
so the production price_history is never touched. Skipped when Postgres is not the primary DB."""
import unittest

from config import IS_POSTGRES

if IS_POSTGRES:
    from db_compat import connect_primary_db
    from price_integrity import WRITE_GUARD_FUNCTION_SQL


@unittest.skipUnless(IS_POSTGRES, "Postgres only")
class WriteGuardPostgresTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect_primary_db(timeout=60)
        self.raw = self.conn._connection
        self.cur = self.raw.cursor()
        self.cur.execute(WRITE_GUARD_FUNCTION_SQL)
        self.cur.execute("CREATE TEMP TABLE ph_guard_fixture (stock_code text, date text, open double precision, high double precision, "
                         "low double precision, close double precision, volume double precision)")
        self.cur.execute("CREATE TRIGGER g BEFORE INSERT OR UPDATE OF open,high,low,close,volume ON ph_guard_fixture "
                         "FOR EACH ROW EXECUTE FUNCTION guard_historical_price_write()")

    def tearDown(self):
        self.raw.rollback()
        self.conn.close()

    def _insert(self, code, day, o, h, l, c, v=1000, checked=False):
        self.cur.execute("SAVEPOINT s")
        try:
            if checked:
                self.cur.execute("SELECT set_config('app.price_basis_checked','1',true)")
            self.cur.execute("INSERT INTO ph_guard_fixture VALUES (%s,%s,%s,%s,%s,%s,%s)", (code, day, o, h, l, c, v))
            self.cur.execute("RELEASE SAVEPOINT s")
            return True
        except Exception:
            self.cur.execute("ROLLBACK TO SAVEPOINT s")
            return False

    def test_non_positive_close_rejected_even_when_checked(self):
        self.assertFalse(self._insert("005930", "2020-01-02", 1, 1, 1, 0, checked=True))
        self.assertFalse(self._insert("005930", "2020-01-02", 1, 1, 1, -5))

    def test_fractional_krx_prices_rejected_even_when_checked(self):
        self.assertFalse(self._insert("005930", "2020-01-02", 100, 100, 100, 100.5, checked=True))

    def test_unverified_historical_insert_rejected_but_checked_allowed(self):
        self.assertFalse(self._insert("005930", "2020-01-02", 100, 110, 90, 105))
        self.assertTrue(self._insert("005930", "2020-01-02", 100, 110, 90, 105, checked=True))

    def test_today_and_non_kr_codes_pass(self):
        self.cur.execute("SELECT CURRENT_DATE::text")
        today = self.cur.fetchone()[0]
        self.assertTrue(self._insert("005930", today, 100, 110, 90, 105))
        self.assertTrue(self._insert("^KS11", "2020-01-02", 1.5, 1.7, 1.4, 1.6))

    def test_noop_update_allowed_changed_update_rejected(self):
        self.assertTrue(self._insert("005930", "2020-01-02", 100, 110, 90, 105, checked=True))
        self.cur.execute("SELECT set_config('app.price_basis_checked','',true)")  # set_config(...,true) lasts the whole transaction
        self.cur.execute("SAVEPOINT u"); self.cur.execute("UPDATE ph_guard_fixture SET close=105"); self.cur.execute("RELEASE SAVEPOINT u")
        self.cur.execute("SAVEPOINT u2")
        with self.assertRaises(Exception):
            self.cur.execute("UPDATE ph_guard_fixture SET close=106")
        self.cur.execute("ROLLBACK TO SAVEPOINT u2")


if __name__ == "__main__":
    unittest.main()
