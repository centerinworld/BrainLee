import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arb", ROOT / "scripts" / "review" / "arbitrate_annual_conflicts_20261013.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _write(tmp, code, y, fs, rows):
    d = tmp / code
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{y}_0_{fs}.json.gz").write_bytes(gzip.compress(json.dumps(rows).encode()))


def test_cash_end_reads_the_cash_flow_end_of_period_row(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.fn, "RAW_DART", tmp_path)
    rows = [{"sj_div": "BS", "account_id": "ifrs-full_CashAndCashEquivalents", "account_nm": "현금및현금성자산", "thstrm_amount": "1", "frmtrm_amount": "1"},
            {"sj_div": "CF", "account_id": "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "account_nm": "기말현금및현금성자산", "thstrm_amount": "69,080,893,000", "frmtrm_amount": "49,680,710,000"}]
    _write(tmp_path, "000001", 2023, "CFS", rows)
    _write(tmp_path, "000001", 2024, "CFS", [{**rows[1], "frmtrm_amount": "69,080,893,000", "thstrm_amount": "70,000,000,000"}])
    assert mod.raw_cands("000001", 2023, "CFS", "cash_end") == [69080893000.0, 69080893000.0]
    assert mod._cash_end("000001", 2022, "CFS", "thstrm_amount") is None
