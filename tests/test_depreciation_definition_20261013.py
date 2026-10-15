import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "review" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


al = _load("align_dep", "align_depreciation_annual_20261013.py")
doc = _load("doc_dep", "parse_cf_adj_dep_from_doc_20261013.py")


def _cf(nm, v):
    return {"sj_div": "CF", "account_nm": nm, "thstrm_amount": v, "frmtrm_amount": v}


def test_dep_sum_adds_depreciation_rows_but_not_amortisation_or_bad_debt():
    rows = [_cf("감가상각비", "1,000"), _cf("사용권자산감가상각비", "300"), _cf("투자부동산감가상각비", "20"),
            _cf("무형자산상각비", "999"), _cf("대손상각비", "888"), _cf("상각후원가측정금융자산의취득", "777"),
            {"sj_div": "BS", "account_nm": "감가상각누계액", "thstrm_amount": "5,000"}]
    assert al.dep_sum(rows, "thstrm_amount") == 1320.0


def test_dep_sum_is_none_without_any_depreciation_row():
    assert al.dep_sum([_cf("무형자산상각비", "5")], "thstrm_amount") is None
    assert al.dep_sum(None, "thstrm_amount") is None


def test_classify_labels_the_known_wrong_definitions():
    assert al.classify(19.39, 1938612344.0, (None, None, None)) == "단위 오류"
    assert al.classify(2.0e10 + 4.0e7, 2.0e10, (None, None, 4.0e7)) == "조정감가+무형상각(D&A) 변형"


def test_doc_table_picks_consolidated_first_and_separate_last():
    rows = [(5.0, 4.0), (3.0, 2.0)]
    assert doc.pick(rows, "CFS", "CFS") == (5.0, 4.0)
    assert doc.pick(rows, "OFS", "OFS") == (3.0, 2.0)
    # a separate-statement cell with only one table found must not borrow the consolidated note
    assert doc.pick([(5.0, 4.0)], "OFS", "OFS") is None


def test_doc_dep_rows_ignore_income_statement_by_nature_tables():
    html = ("<TABLE><TR><TD>(단위 : 원)</TD></TR>"
            "<TR><TD>매출액</TD><TD>100</TD></TR><TR><TD>급여</TD><TD>1</TD></TR><TR><TD>감가상각비</TD><TD>7</TD></TR><TR><TD>법인세비용</TD><TD>2</TD></TR><TR><TD>이자비용</TD><TD>1</TD></TR></TABLE>")
    assert doc.dep_rows(html) == []
