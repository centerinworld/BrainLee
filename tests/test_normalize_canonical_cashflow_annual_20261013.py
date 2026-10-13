import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("norm_cf", ROOT / "scripts" / "review" / "normalize_canonical_cashflow_annual_20261013.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_rounding_pair_prefers_the_precise_value():
    assert mod.rounding_pair(55351403770.0, 55351000000.0) == (55351403770.0, 55351000000.0)
    assert mod.rounding_pair(55351000000.0, 55351403770.0) == (55351403770.0, 55351000000.0)
    # a material difference is not a rounding pair
    assert mod.rounding_pair(14057664000000.0, 13833106000000.0) is None
    # two exact values that merely differ are not a rounding pair either
    assert mod.rounding_pair(1234567891.0, 1234567999.0) is None


def test_refine_fills_null_and_replaces_a_rounded_representative():
    rep = {"operating_cf": None, "cash_end": 43968000000.0, "capex": 100.0, "depreciation": 5e9}
    other = {"operating_cf": 7.5e9, "cash_end": 43967634212.0, "capex": 100.0, "depreciation": 5e9}
    upd, conf = mod.refine(rep, [other])
    assert upd == {"operating_cf": 7.5e9, "cash_end": 43967634212.0} and conf == []


def test_refine_never_picks_a_side_in_a_real_conflict():
    rep = {"depreciation": 14057664000000.0}
    other = {"depreciation": 13833106000000.0}
    upd, conf = mod.refine(rep, [other])
    assert upd == {} and conf == [("depreciation", 14057664000000.0, 13833106000000.0)]


def test_refine_keeps_a_precise_representative():
    rep = {"cash_end": 43967634212.0}
    other = {"cash_end": 43968000000.0}
    assert mod.refine(rep, [other]) == ({}, [])
