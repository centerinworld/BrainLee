"""cf_triple_validator가 FnGuide 값을 원장에 직접 복사하지 않는지(2026-10-09, 원칙 0)."""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "collectors" / "cf_triple_validator.py"


def test_apply_fg_correction_raises():
    from collectors.cf_triple_validator import _apply_fg_correction
    with pytest.raises(RuntimeError):
        _apply_fg_correction(None, "005930", 2025, "operating_cf", 1.0)


def test_no_call_and_no_overwrite_sql():
    tree = ast.parse(SRC.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_apply_fg_correction"]
    assert not calls, "_apply_fg_correction 호출이 남아 있음"
    assert "UPDATE cash_flow_data" not in SRC.read_text(), "cash_flow_data를 직접 고치는 SQL이 남아 있음"
