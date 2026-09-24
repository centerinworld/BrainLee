import unittest
from pathlib import Path

from scripts.audit_backtest_static_contracts import audit


ROOT = Path(__file__).resolve().parents[1]
WEAK_PATTERN = "GLOB '[0-9]*'"


class BacktestStaticContractTests(unittest.TestCase):
    def test_no_weak_numeric_ticker_filters_in_strategies(self):
        result = audit()
        weak = [row for row in result["findings"] if row["code"] == "WEAK_NUMERIC_TICKER_FILTER"]
        self.assertFalse(weak, weak)

    def test_all_strategy_files_are_scanned(self):
        result = audit()
        self.assertGreaterEqual(result["files_checked"], 40)

    def test_no_weak_glob_numeric_contract_in_python_sources(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            if any(part in {"venv", ".venv", "node_modules", ".claude", "scratch"} for part in path.parts):
                continue
            if path.name == "audit_backtest_static_contracts.py":
                continue
            if WEAK_PATTERN in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertFalse(offenders, offenders)


if __name__ == "__main__":
    unittest.main()
