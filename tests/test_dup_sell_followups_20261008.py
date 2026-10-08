"""§36-3·§39 검토 보완(DB 접속 없이 도는 테스트 — 메모리 SQLite): W5 오버레이·최신 라벨 선택, 중복 매도 정리 후 보유 보정(첫 매도 동기화·애매한 후보 건너뜀)."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import w5_overlay as rb  # noqa: E402 — import 부작용 없는 모듈(routes.backtest는 import 때 DB 연결)
import sync_holdings_after_dup_sell_cleanup_20261008 as sync  # noqa: E402


class TestW5Overlay(unittest.TestCase):
    def _w5(self):
        c = sqlite3.connect(":memory:")
        c.execute("""CREATE TABLE strategy_w5_distribution (w5_label TEXT, strategy TEXT, period_label TEXT,
            median_return_pct REAL, q25_return_pct REAL, min_return_pct REAL, max_return_pct REAL, mdd_median_pct REAL,
            score_return_pct REAL, code_return_pct REAL, n_random INT, order_note TEXT, data_fingerprint TEXT,
            representative_run_id TEXT, created_at TEXT)""")
        return c

    def test_latest_label_by_created_at_not_string_max(self):
        c = self._w5()
        c.execute("INSERT INTO strategy_w5_distribution (w5_label, strategy, period_label, created_at) VALUES ('w5_z_old','a','p','2026-10-01T00:00:00')")
        c.execute("INSERT INTO strategy_w5_distribution (w5_label, strategy, period_label, created_at) VALUES ('w5_a_new','a','p','2026-11-01T00:00:00')")
        self.assertEqual(rb._latest_w5_label(c), "w5_a_new")   # 문자열 MAX라면 w5_z_old

    def test_null_created_at_not_chosen(self):
        c = self._w5()
        c.execute("INSERT INTO strategy_w5_distribution (w5_label, strategy, period_label, created_at) VALUES ('w5_ok','a','p','2026-10-08T00:00:00')")
        c.execute("INSERT INTO strategy_w5_distribution (w5_label, strategy, period_label, created_at) VALUES ('w5_null','a','p',NULL)")
        self.assertEqual(rb._latest_w5_label(c), "w5_ok")      # NULLS LAST

    def test_overlay_replaces_and_preserves(self):
        c = self._w5()
        c.execute("INSERT INTO strategy_w5_distribution VALUES ('L','v8','P1',12.5,5.0,-3.0,30.0,-9.0,10.0,8.0,12,'sensitive','fp','r1','t')")
        ordered = [{"strategy": "v8", "periods": {"P1": {"total_return_pct": 40.0, "mdd": -20.0, "run_id": "old"},
                                                   "P2": {"total_return_pct": 1.0, "mdd": -1.0, "run_id": "x"}}}]
        rb._apply_w5_overlay(ordered, rb._w5_rows(c, "L"), "L")
        p1, p2 = ordered[0]["periods"]["P1"], ordered[0]["periods"]["P2"]
        self.assertEqual((p1["total_return_pct"], p1["mdd"]), (12.5, -9.0))
        self.assertEqual((p1["pre_correction_return_pct"], p1["pre_correction_mdd"], p1["pre_correction_run_id"]), (40.0, -20.0, "old"))
        self.assertEqual(p1["w5"]["n_random"], 12)
        self.assertNotIn("pre_correction_return_pct", p2)       # W5 행이 없는 구간은 그대로
        self.assertEqual(ordered[0]["w5_applied"], "L")


class TestHoldingSync(unittest.TestCase):
    def setUp(self):
        c = sqlite3.connect(":memory:")
        c.execute("""CREATE TABLE peak_holding (id INTEGER PRIMARY KEY, stock_name TEXT, strategy TEXT, quantity REAL,
            is_active INT, sell_price REAL, sold_at TEXT, current_price REAL, profit_pct REAL, sold_price REAL, updated_at TEXT)""")
        c.execute("""CREATE TABLE peak_trade (id INTEGER PRIMARY KEY, holding_id INT, strategy TEXT, stock_name TEXT,
            price REAL, quantity REAL, profit_pct REAL, tx_at TEXT)""")
        # 보유 1: 반복 매도로 마지막 값이 남음(첫 매도 10:13으로 되돌려야 함)
        c.execute("INSERT INTO peak_holding VALUES (1,'A','value',10,0,95,'2026-10-08 14:00:00',95,-5,NULL,NULL)")
        c.execute("INSERT INTO peak_trade VALUES (11,NULL,'value','A',98,10,-2,'2026-10-08 10:13:02')")
        # 보유 2·3: 같은 날·같은 수량 두 개 → 애매 → 건너뜀
        c.execute("INSERT INTO peak_holding VALUES (2,'B','value',5,0,50,'2026-10-08 12:00:00',50,-1,NULL,NULL)")
        c.execute("INSERT INTO peak_holding VALUES (3,'B','value',5,0,51,'2026-10-08 13:00:00',51,-1,NULL,NULL)")
        c.execute("INSERT INTO peak_trade VALUES (12,NULL,'value','B',52,5,0,'2026-10-08 09:00:00')")
        self.c = c

    def test_plan_syncs_unique_and_skips_ambiguous(self):
        items, amb, miss = sync.plan(self.c, keep_ids=[11, 12])
        self.assertEqual([x["holding"]["id"] for x in items if x["needs_update"]], [1])
        self.assertEqual(amb[0]["holding_ids"], [2, 3])
        self.assertEqual(miss, [])


if __name__ == "__main__":
    unittest.main()
