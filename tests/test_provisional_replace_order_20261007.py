"""당일 날짜로 KRX 교체해도 잠정 표시가 남지 않는다(REVIEW_PLAN §21-2 1번).

price_history_mark_provisional 트리거는 당일 행 UPDATE마다 표시를 다시 만든다. check_price_vs_krx_daily.repair_provisional 이
'UPDATE → 표시 DELETE' 순서를 지켜야 같은 날 교체(수동 실행 등) 뒤 표시가 0건이 된다. 실제 운영 테이블을 쓰되
가짜 종목코드(999990)로 트랜잭션 안에서만 실행하고 항상 롤백한다. PostgreSQL이 아니면 건너뜀."""
import sys
import unittest
from datetime import date
from pathlib import Path

from config import IS_POSTGRES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

if IS_POSTGRES:
    import check_price_vs_krx_daily as m
    import price_integrity
    from db_compat import connect_primary_db

CODE = "999990"


@unittest.skipUnless(IS_POSTGRES, "Postgres only")
class ProvisionalReplaceOrderTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect_primary_db(timeout=60)
        self.conn.commit = lambda: None  # repair_provisional 의 commit 무력화 — 끝에서 롤백
        price_integrity.install_provisional_marker(self.conn)
        self.iso = date.today().isoformat()
        self.bas = self.iso.replace("-", "")

    def tearDown(self):
        self.conn._connection.rollback()
        self.conn.close()

    def test_same_day_replace_leaves_no_marker(self):
        c = self.conn
        c.execute("SELECT set_config('app.price_basis_checked','1', true)")
        c.execute("INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                  (CODE, self.iso, 1000, 1100, 990, 1050, 500))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM price_provisional_rows WHERE stock_code=? AND date=?", (CODE, self.iso)).fetchone()[0], 1)
        c.execute("""INSERT INTO stock_price_daily(bas_dt,stock_code,stock_name,market,open_price,high_price,low_price,close_price,volume)
                     VALUES (?,?,?,?,?,?,?,?,?)""", (self.bas, CODE, "테스트", "TEST", 1000, 1100, 990, 1040, 480))
        out = m.repair_provisional(c, [self.bas])
        self.assertGreaterEqual(out[self.iso][0], 1)
        row = tuple(c.execute("SELECT close, volume FROM price_history WHERE stock_code=? AND date=?", (CODE, self.iso)).fetchone())
        self.assertEqual(row, (1040.0, 480.0))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM price_provisional_rows WHERE stock_code=? AND date=?", (CODE, self.iso)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
