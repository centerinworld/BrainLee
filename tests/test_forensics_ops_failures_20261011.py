"""Regression tests for the 2026-10-11 ops-failure forensic slice.

Everything here runs against FIXTURES — synthetic sqlite ledger, synthetic integrity
report JSON, and a recording stub connection. No live PostgreSQL, no network, no writes.
The two properties the slice must hold are pinned here:
  * the readers issue SELECT statements only (recorder stub)
  * the ledger is read from a COPY, never the live file
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import forensics_ops_failures_20261011 as fx  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────
LIVE_CASES_I1 = [
    # (stock, year, prev_억, cur_억) — verbatim from the 2026-10-11 report artifact
    ("032680", 2022, 925.0, 733730501.0),
    ("311390", 2022, 299.0, 267029.0),
    ("008060", 2021, 34.0, 13717.0),
    ("066980", 2021, 135.0, 1683.0),
]


def make_ledger(tmp_path: Path) -> Path:
    p = tmp_path / "collection_health.db"
    c = sqlite3.connect(str(p))
    c.execute("""CREATE TABLE collection_job_runs (
        run_id INTEGER PRIMARY KEY, job_name TEXT, attempt INTEGER, status TEXT,
        started_at TEXT, finished_at TEXT, duration_seconds REAL, error TEXT, details_json TEXT)""")
    c.execute("INSERT INTO collection_job_runs VALUES (48179,'재무무결성일일',1,'failed',"
              "'2026-10-11T06:20:00','2026-10-11T06:20:09',9.307,"
              "'daily financial integrity audit failed: 1','{}')")
    c.execute("INSERT INTO collection_job_runs VALUES (48195,'데이터계약점검',1,'failed',"
              "'2026-10-11T07:10:00','2026-10-11T07:10:07',7.481,"
              "'데이터 계약 점검 실패(rc=2): close_verify_fresh fail value=3.0 thr=5.0 "
              "last trade_date 2026-10-08 status mismatch (3d ago)','{}')")
    c.execute("INSERT INTO collection_job_runs VALUES (99999,'스크리너',1,'success',"
              "'2026-10-11T00:02:51','2026-10-11T00:02:55',4.0,NULL,'{}')")
    c.commit()
    c.close()
    return p


def make_report(tmp_path: Path) -> Path:
    """Mirrors the shape (and the exact cases) of integrity_20261011_062009.json."""
    doc = {
        "I1_yoy_spike": {"desc": "YoY 10x+", "pass": False, "count": 4,
                         "cases": [{"stock": s, "year": y, "prev_억": p, "cur_억": c, "chg_pct": 0.0}
                                   for s, y, p, c in LIVE_CASES_I1]},
        "I2_op_gt_rev": {"desc": "op>rev", "pass": False, "count": 10,
                         "cases": [{"stock": "402340", "name": "SK스퀘어", "year": 2025,
                                    "rev_억": 14115.0, "op_억": 87974.0},
                                   {"stock": "383800", "name": "LX홀딩스", "year": 2024,
                                    "rev_억": 407.0, "op_억": 1560.0}]},
        "I6_no_recent_data": {"desc": "no 2023+", "pass": False, "count": 25,
                              "cases": [{"stock": "950260", "name": "인제니아테라퓨릭스(Reg.S)",
                                         "market": "KOSDAQ", "mktcap_조": 1.0}]},
        "I4_quarter_eq_annual": {"desc": "ok", "pass": True, "count": 0, "cases": []},
    }
    p = tmp_path / "integrity_20261011_062009.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


class _Row(dict):
    def keys(self):  # the readers branch on hasattr(row, "keys")
        return super().keys()


class RecordingConn:
    """Records every statement; answers with canned rows. Proves read-only SQL."""

    def __init__(self, one=None, many=None):
        self.statements = []
        self._one = one
        self._many = many or []

    def execute(self, sql, params=()):
        self.statements.append(" ".join(str(sql).split()))
        conn = self

        class Cur:
            def fetchone(self):
                return conn._one

            def fetchall(self):
                return conn._many

            def __iter__(self):
                return iter(conn._many)

        return Cur()

    def close(self):
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Pure classification
# ──────────────────────────────────────────────────────────────────────────────
def test_unit_scale_candidates_live_cases():
    """Each live YoY spike collapses onto its neighbour year at a *single* best power of ten."""
    got = {s: fx.unit_scale_candidates(c, p)[0]["exponent"] for s, _y, p, c in LIVE_CASES_I1}
    assert got == {"032680": 6, "311390": 3, "008060": 3, "066980": 1}
    # a plain 3x growth is NOT within tolerance of any power of ten
    assert fx.unit_scale_candidates(300.0, 100.0) == []


def test_classify_yoy_spike_verdicts():
    assert fx.classify_yoy_spike(733730501.0, 925.0)["verdict"] == "unit_scale_inflated_1e6"
    assert fx.classify_yoy_spike(267029.0, 299.0)["verdict"] == "unit_scale_inflated_1e3"
    assert fx.classify_yoy_spike(13717.0, 34.0)["verdict"] == "unit_scale_inflated_1e3"
    # only a 10x fits -> a genuine 10x growth looks identical: must NOT be called a unit error
    assert fx.classify_yoy_spike(1683.0, 135.0)["verdict"] == "ambiguous_needs_human"
    # a plain 3x growth is not explained by any power of ten
    assert fx.classify_yoy_spike(300.0, 100.0)["verdict"] == "unexplained_needs_human"


def test_classify_yoy_spike_reports_internal_consistency():
    # annual == sum of its own quarters -> the whole year is shifted, not just the annual row
    r = fx.classify_yoy_spike(13717.0, 34.0, annual_억=13717.0, quarterly_sum_억=13717.0)
    assert r["detail"]["internal_consistency"] == "annual_equals_quarters"
    r2 = fx.classify_yoy_spike(733730501.0, 925.0, annual_억=733730501.0, quarterly_sum_억=452.6)
    assert r2["detail"]["internal_consistency"] == "annual_differs_from_quarters"


def test_classify_close_verify_row_splits_the_two_clauses():
    # today's live row: status mismatch, 3 days old, well inside the 5d window
    r = fx.classify_close_verify_row("mismatch", 3)
    assert r["verdict"] == "fail_reference_status"
    assert fx.classify_close_verify_row("ok", 4)["verdict"] == "ok"
    assert fx.classify_close_verify_row("ok", 6)["verdict"] == "fail_stale"
    assert fx.classify_close_verify_row(None, None)["verdict"] == "warn_no_run_recorded"
    # status ok + age 5 is inside the window (<=)
    assert fx.classify_close_verify_row("ok", 5)["verdict"] == "ok"


def test_close_matches_tolerance_boundary():
    assert fx.close_matches(263000.0, 262999.5) is True     # exactly 0.5원 -> inside
    assert fx.close_matches(263000.0, 262999.4) is False
    assert fx.close_matches(None, 100.0) is False


def test_classify_price_disagreement_prefers_the_official_source():
    assert fx.classify_price_disagreement(db_close=5010, ref_close=5100,
                                          krx_close=5010)["verdict"] == "db_agrees_with_official"
    assert fx.classify_price_disagreement(db_close=263000, ref_close=262000,
                                          db_close_nominal=263000)["verdict"] == "reference_revised_after_check"
    assert fx.classify_price_disagreement(db_close=4815, ref_close=4720,
                                          db_close_nominal=4635)["verdict"] == "reference_not_final_at_check"
    assert fx.classify_price_disagreement(db_close=None, ref_close=1.0)["verdict"] == "unresolved_needs_human"


def test_classify_missing_financials_pre_revenue_class():
    assert fx.classify_missing_financials(True, True)["verdict"] == "pre_revenue_no_revenue_field"
    assert fx.classify_missing_financials(False, True)["verdict"] == "revenue_present_elsewhere_needs_human"
    assert fx.classify_missing_financials(False, False)["verdict"] == "no_annual_rows_true_collection_gap"


def test_watchlist_from_ledger_filters_to_the_two_jobs():
    rows = [{"job_name": "재무무결성일일"}, {"job_name": "스크리너"}, {"job_name": "데이터계약점검"}]
    assert [r["job_name"] for r in fx.watchlist_from_ledger(rows)] == ["재무무결성일일", "데이터계약점검"]


# ──────────────────────────────────────────────────────────────────────────────
# Readers — fixture only
# ──────────────────────────────────────────────────────────────────────────────
def test_read_ledger_rows_reads_a_copy_and_leaves_the_source_untouched(tmp_path):
    live = make_ledger(tmp_path)
    before = live.read_bytes()
    rows = fx.read_ledger_rows(live, "2026-10-11")
    assert [r["job_name"] for r in rows] == ["재무무결성일일", "데이터계약점검"]
    assert rows[0]["status"] == "failed" and "audit failed: 1" in rows[0]["error"]
    assert "close_verify_fresh" in rows[1]["error"]
    assert live.read_bytes() == before  # source untouched
    # no copy left behind
    assert not list(tmp_path.glob("ops_forensics_*"))


def test_read_ledger_rows_for_a_day_with_no_failures(tmp_path):
    live = make_ledger(tmp_path)
    assert fx.read_ledger_rows(live, "1999-01-01") == []


def test_read_integrity_report_picks_the_failing_items(tmp_path):
    rep = fx.read_integrity_report(make_report(tmp_path))
    assert rep["items_total"] == 4
    assert rep["items_failed"] == ["I1_yoy_spike", "I2_op_gt_rev", "I6_no_recent_data"]


def test_read_watermarks_issues_selects_only():
    conn = RecordingConn(one=(0, 0), many=[])
    fx.read_watermarks(conn)
    assert conn.statements, "no statement recorded"
    assert all(s.upper().startswith("SELECT") for s in conn.statements)


def test_all_readers_issue_selects_only():
    conn = RecordingConn(one=_Row({"stock_code": "005930", "close": 1.0, "close_price": 1.0}), many=[])
    fx.read_watermarks(conn)
    fx.read_contract_rows(conn, "2026-10-11")
    fx.krx_agreement(conn, "2026-10-08")
    fx.krx_close_map(conn, "2026-10-08")
    fx.read_financial_evidence(conn, "005930", 2025)
    for s in conn.statements:
        head = s.upper().lstrip("(")
        assert head.startswith("SELECT"), s
        assert "INSERT " not in s.upper() and "UPDATE " not in s.upper()
        assert "DELETE " not in s.upper() and "DELETE\n" not in s.upper()


def test_krx_agreement_compares_with_the_same_tolerance_as_the_verifier():
    conn = RecordingConn(many=[("005930", 263000.0, 262999.6), ("005160", 4815.0, 4720.0)])
    ag = fx.krx_agreement(conn, "2026-10-08")
    assert ag["compared"] == 2 and ag["mismatched"] == 1
    assert ag["examples"][0]["code"] == "005160"


def test_financial_case_evidence_reads_the_live_report_shapes():
    rows = [
        {"year": 2026, "is_annual": True, "report_type": "CFS", "revenue": 2.0e13},
        {"year": 2027, "is_annual": True, "report_type": "CFS", "revenue": 8.0e16},
        {"year": 2027, "is_annual": False, "report_type": "CFS", "revenue": 1.0e10},
        {"year": 2027, "is_annual": False, "report_type": "CFS", "revenue": 2.0e10},
    ]
    ev = fx.financial_case_evidence(rows, 2027)
    assert ev["annual_cfs"] == 8.0e16
    assert ev["quarterly_sum_cfs"] == 3.0e10
    assert ev["prev_annual_cfs"] == 2.0e13


# ──────────────────────────────────────────────────────────────────────────────
# Contract guards — the slice must stay read-only and fixture-runnable
# ──────────────────────────────────────────────────────────────────────────────
def test_module_source_contains_no_write_statement():
    src = Path(fx.__file__).read_text(encoding="utf-8").upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "TRUNCATE TABLE", "ALTER TABLE"):
        assert verb not in src, f"read-only slice must not contain {verb}"


def test_module_never_opens_a_writable_primary_connection():
    src = Path(fx.__file__).read_text(encoding="utf-8")
    assert "readonly=False" not in src
    assert "connect_primary_db(readonly=True" in src


def test_cli_accepts_fixture_inputs_without_touching_live_paths(tmp_path):
    """The whole ledger+report half of the slice runs from fixtures."""
    live = make_ledger(tmp_path)
    rep = make_report(tmp_path)
    rows = fx.read_ledger_rows(live, "2026-10-11")
    assert len(rows) == 2
    cls = fx.read_integrity_report(rep)
    for stock, year, prev, cur in LIVE_CASES_I1:
        case = next(c for c in cls["failed"]["I1_yoy_spike"]["cases"] if c["stock"] == stock)
        verdict = fx.classify_yoy_spike(float(case["cur_억"]), float(case["prev_억"]))["verdict"]
        if stock in ("032680", "311390", "008060"):
            assert verdict.startswith("unit_scale_inflated_1e")
        else:
            assert verdict == "ambiguous_needs_human"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
