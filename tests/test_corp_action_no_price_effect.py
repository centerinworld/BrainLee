"""권리락 없는 유상증자(corporate_action_no_price_effect 등재)는 조정계수 로더에서 제외된다."""
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest_common import _load_corp_action_factors


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE corporate_action_events (id INTEGER PRIMARY KEY, stock_code TEXT, event_date TEXT, backward_price_factor REAL, "
              "source TEXT, confidence REAL, adjustment_status TEXT, updated_at TEXT)")
    rows = [(1, "005930", "2024-01-10", 0.5, "DART_equity_issue", 0.9, "factor_confirmed", "2026-01-01"),      # 액면분할 계수 → 유지
            (2, "005930", "2024-06-10", 0.9, "DART_equity_issue", 0.9, "factor_confirmed", "2026-01-01"),      # 제3자배정 → 제외
            (3, "005930", "2024-09-10", 0.8, "DART_equity_issue", 0.9, "factor_confirmed", "2026-01-01")]      # 주주배정 → 유지
    c.executemany("INSERT INTO corporate_action_events VALUES (?,?,?,?,?,?,?,?)", rows)
    return c


def test_third_party_allotment_excluded_but_others_kept():
    c = _db()
    c.execute("CREATE TABLE corporate_action_no_price_effect (event_id BIGINT PRIMARY KEY, stock_code TEXT, event_date TEXT, method TEXT, reason TEXT, evidence_rcept_no TEXT, classified_at TEXT)")
    c.execute("INSERT INTO corporate_action_no_price_effect (event_id, stock_code, method) VALUES (2, '005930', '제3자배정')")
    out = _load_corp_action_factors(c, ["005930"])
    assert [round(f, 3) for _, f in out["005930"]] == [0.5, 0.8]


def test_loader_creates_table_when_missing_and_keeps_all():
    out = _load_corp_action_factors(_db(), ["005930"])
    assert [round(f, 3) for _, f in out["005930"]] == [0.5, 0.9, 0.8]
