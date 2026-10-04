import json
from pathlib import Path

import pandas as pd

from scripts.review.third_party_crosscheck_20261003 import compare_earnings, compare_prices


def test_compare_earnings_classifies_mismatch_and_missing(tmp_path: Path):
    paths = {"aik_earnings.json": tmp_path / "aik_earnings.json"}
    paths["aik_earnings.json"].write_text(
        json.dumps(
            {
                "count": 4,
                "items": [
                    {
                        "parse_status": "ok",
                        "period": "2026.06",
                        "code": "005930",
                        "name": "Samsung",
                        "rcept_dt": "20260814",
                        "rcept_no": "A",
                        "fin": {
                            "누적": True,
                            "기준": "연결",
                            "revenue": {"current": 300_000_000},
                            "operating_income": {"current": 30_000_000},
                            "net_income": {"current": 10_000_000},
                        },
                    },
                    {
                        "parse_status": "ok",
                        "period": "2026.06",
                        "code": "123456",
                        "name": "SeparateCo",
                        "rcept_dt": "20260814",
                        "rcept_no": "B",
                        "fin": {
                            "누적": True,
                            "기준": "별도",
                            "revenue": {"current": 1_000_000_000},
                            "operating_income": {"current": 100_000_000},
                            "net_income": {"current": 50_000_000},
                        },
                    },
                    {
                        "parse_status": "ok",
                        "period": "2026.06",
                        "code": "900100",
                        "name": "ForeignCo",
                        "rcept_dt": "20260814",
                        "rcept_no": "C",
                        "fin": {
                            "누적": True,
                            "기준": "연결",
                            "revenue": {"current": 1_000_000},
                            "operating_income": {"current": 1_000_000},
                            "net_income": {"current": 1_000_000},
                        },
                    },
                    {
                        "parse_status": "ok",
                        "period": "2026.06",
                        "code": "777777",
                        "name": "MissingCo",
                        "rcept_dt": "20260814",
                        "rcept_no": "D",
                        "fin": {
                            "누적": True,
                            "기준": "연결",
                            "revenue": {"current": 10_000_000},
                            "operating_income": {"current": 10_000_000},
                            "net_income": {"current": 10_000_000},
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pg_fin = pd.DataFrame(
        [
            {"stock_code": "005930", "report_type": "CFS", "revenue": 100_000_000, "operating_profit": 10_000_000, "net_income": 4_000_000, "data_source": "q1"},
            {"stock_code": "005930", "report_type": "CFS", "revenue": 200_000_000, "operating_profit": 20_000_000, "net_income": 6_000_000, "data_source": "q2"},
            {"stock_code": "123456", "report_type": "OFS", "revenue": 1_200_000_000, "operating_profit": 100_000_000, "net_income": 50_000_000, "data_source": "q1q2"},
            {"stock_code": "900100", "report_type": "CFS", "revenue": 2_000_000_000, "operating_profit": 2_000_000_000, "net_income": 2_000_000_000, "data_source": "foreign"},
        ]
    )
    meta = pd.DataFrame(
        [
            {"stock_code": "005930", "stock_name": "Samsung"},
            {"stock_code": "123456", "stock_name": "SeparateCo"},
            {"stock_code": "900100", "stock_name": "ForeignCo"},
        ]
    )
    quirks = pd.DataFrame(
        [{"stock_code": "900100", "config_key": "fs_quirk:reporting_currency", "config_value": "USD"}]
    )

    summary = compare_earnings(paths, pg_fin, meta, quirks, tmp_path)

    assert summary["compared_fields_total"] == 12
    assert summary["ok_fields"] == 5
    classes = {row["classification"]: row["fields"] for row in summary["mismatch_class_summary"]}
    assert classes["ofs_mapping_or_source_basis_review"] == 1
    assert classes["currency_or_foreign_issuer_priority"] == 3
    missing = {row["classification"]: row["fields"] for row in summary["missing_class_summary"]}
    assert missing["pg_financial_missing_or_basis_gap"] == 3


def test_compare_prices_flags_stale_rows(tmp_path: Path):
    paths = {"aik_quotes_min.json": tmp_path / "aik_quotes_min.json"}
    paths["aik_quotes_min.json"].write_text(
        json.dumps(
            {
                "generated_kst": "2026-10-02 18:11",
                "as_of_iso": "2026-10-01",
                "columns": ["c", "clpr", "fltRt", "mrktTotAmt"],
                "rows": [["111111", 1000, 0, 1000000], ["222222", 2000, 0, 2000000]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pg_price = pd.DataFrame(
        [{"stock_code": "111111", "date": "2026-10-01", "close": 1000, "volume": 10}]
    )
    pg_latest = pd.DataFrame(
        [
            {"stock_code": "111111", "date": "2018-12-28", "close": 900, "volume": 1},
            {"stock_code": "222222", "date": "2026-09-30", "close": 2000, "volume": 0},
        ]
    )
    meta = pd.DataFrame(
        [
            {"stock_code": "111111", "stock_name": "Old"},
            {"stock_code": "222222", "stock_name": "Suspended"},
        ]
    )

    summary = compare_prices(paths, pg_price, pg_latest, meta, tmp_path)

    assert summary["matched_on_20261001"] == 1
    stale = {row["classification"]: row["rows"] for row in summary["stale_summary"]}
    assert stale["very_stale_possible_delisted_or_ticker_identity"] == 1
    assert stale["recent_no_volume_or_suspended_review"] == 1
