from __future__ import annotations

import unittest

import backtest
from routes.backtest import ALL_STRATEGIES_EX, STRATEGY_RUN_FUNCS


class BacktestStrategyCoverageTest(unittest.TestCase):
    def test_all_strategy_center_strategies_have_rerun_functions(self):
        self.assertEqual(set(ALL_STRATEGIES_EX), set(STRATEGY_RUN_FUNCS))
        self.assertEqual(len(ALL_STRATEGIES_EX), 26)
        for function_name in STRATEGY_RUN_FUNCS.values():
            self.assertTrue(callable(getattr(backtest, function_name, None)), function_name)


if __name__ == "__main__":
    unittest.main()
