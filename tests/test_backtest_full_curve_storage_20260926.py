"""_save_result must hand the FULL daily curve to backtest_equity while the stored/returned result keeps the 252-point tail."""
import json
import unittest
from unittest import mock

import backtest_common


class FullCurveStorageTest(unittest.TestCase):
    def test_full_curve_goes_to_equity_table_only(self):
        full = [{"date": f"2024-01-{i % 28 + 1:02d}", "equity": 100 + i} for i in range(600)]
        result = {"equity_curve": full[-252:], "_equity_full": full, "total_return_pct": 1.0}
        conn = mock.MagicMock()
        conn.execute.return_value.fetchone.return_value = ("2024-01-01", "2024-12-31", 1e7, 10)
        with mock.patch.object(backtest_common, "connect_primary_db", return_value=conn), \
                mock.patch("backtest_equity.save_run_curve") as save:
            backtest_common._save_result("r1", result)
        self.assertNotIn("_equity_full", result)
        stored_json = json.loads(conn.execute.call_args_list[0].args[1][6])
        self.assertEqual(len(stored_json["equity_curve"]), 252)
        self.assertNotIn("_equity_full", stored_json)
        self.assertEqual(len(save.call_args.args[2]["equity_curve"]), 600)

    def test_without_full_curve_behaviour_unchanged(self):
        result = {"equity_curve": [{"date": "2024-01-02", "equity": 1.0}]}
        conn = mock.MagicMock()
        conn.execute.return_value.fetchone.return_value = ("2024-01-01", "2024-12-31", 1e7, 10)
        with mock.patch.object(backtest_common, "connect_primary_db", return_value=conn), \
                mock.patch("backtest_equity.save_run_curve") as save:
            backtest_common._save_result("r2", result)
        self.assertEqual(len(save.call_args.args[2]["equity_curve"]), 1)


if __name__ == "__main__":
    unittest.main()
