"""2026-10-08: 이미 매도한(sold_at) 같은 편입일 보유를 /api/trend/buy가 다시 활성화하지 않는다(같은 날 반복 매도 결함).
운영 DB에 시험용 전략 키 행을 잠시 만들고 지운다. PostgreSQL에 접속할 수 없으면 건너뛴다."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10); _c.execute("SELECT 1 FROM peak_holding LIMIT 1"); _c.close()
    _HAS_DB = True
except Exception:  # pragma: no cover
    _HAS_DB = False

KEY = "zz_test_reactivate_20261008"


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestNoReactivateSold(unittest.TestCase):
    def setUp(self):
        c = connect_primary_db(timeout=30)
        c.execute("DELETE FROM peak_holding WHERE strategy=?", (KEY,))
        c.execute("""INSERT INTO peak_holding (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,
                     profit_pct,is_active,strategy,detected_at,updated_at,sold_at)
                     VALUES ('999999','시험종목','',1000,900,10,'2026-03-25',0,-10,0,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""", (KEY,))
        c.commit(); c.close()

    def tearDown(self):
        c = connect_primary_db(timeout=30)
        c.execute("DELETE FROM peak_holding WHERE strategy=?", (KEY,)); c.commit(); c.close()

    def test_sold_holding_not_reactivated(self):
        from fastapi import HTTPException
        from routes import trend
        with self.assertRaises(HTTPException) as cm:
            trend.trend_buy({"stock_name": "시험종목", "stock_code": "999999", "buy_price": 950, "quantity": 10,
                             "entry_date": "2026-03-25", "strategy": KEY})
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("already_sold_same_entry", cm.exception.detail["reasons"])
        c = connect_primary_db(timeout=30)
        self.assertEqual(c.execute("SELECT is_active FROM peak_holding WHERE strategy=?", (KEY,)).fetchone()[0], 0)
        c.close()


if __name__ == "__main__":
    unittest.main()
