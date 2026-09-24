import gzip
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ETF_check"))

from issuer_pdf_fallback import (  # noqa: E402
    parse_plus,
    parse_tiger,
    validated_maturity_wind_down_exceptions,
)


class IssuerFallbackTest(unittest.TestCase):
    def test_effective_date_is_preserved(self):
        payload={"totalElements":1,"content":[{"wkdate":"20260826","krJmCd":"US1","jmNm":"X","amount":2,"ratio":3}]}
        effective,rows=parse_plus(payload)
        self.assertEqual(effective,"20260826")
        self.assertEqual(rows[0]["code"],"US1")

    def test_incomplete_pagination_is_rejected(self):
        payload={"totalElements":2,"content":[{"wkdate":"20260826"}]}
        with self.assertRaises(RuntimeError):
            parse_plus(payload)

    def test_tiger_html_requires_all_rows_and_parses_values(self):
        html = """
        <tr data-tot-cnt="2"><td>AAPL US EQUITY</td><td>Apple Inc</td>
        <td>60.4</td><td>25,507,121</td><td>3.70</td><td>-</td></tr>
        <tr data-tot-cnt="2"><td>KRD010010001</td><td>원화예금</td>
        <td>1,000</td><td>1,000</td><td>0.10</td><td>-</td></tr>
        """
        rows = parse_tiger(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["code"], "AAPL US EQUITY")
        self.assertEqual(rows[0]["valuation"], 25507121.0)
        with self.assertRaises(RuntimeError):
            parse_tiger(html.replace('data-tot-cnt="2"', 'data-tot-cnt="3"'))

    def test_maturity_wind_down_requires_history_kis_and_raw_evidence(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE etf_pdf_full_snapshot(
                base_date TEXT,etf_ticker TEXT,etf_name TEXT,status TEXT,
                component_count INTEGER,domestic_stock_count INTEGER,
                raw_path TEXT,raw_sha256 TEXT
            );
            CREATE TABLE etf_scale_daily(
                base_date TEXT,etf_ticker TEXT,expected_component_count INTEGER,
                listed_shares REAL,scale_factor REAL
            );
            """
        )
        history = [
            ("20260907",11),("20260908",7),("20260909",5),
            ("20260910",4),("20260911",1),
        ]
        conn.executemany(
            "INSERT INTO etf_pdf_full_snapshot VALUES(?,?,'마이티 26-09 특수채(AAA)액티브','success',?,0,NULL,NULL)",
            [(day,"465780",count) for day,count in history],
        )
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "465780.json.gz"
            with gzip.open(raw,"wb") as stream:
                stream.write(b"[]")
            digest = hashlib.sha256(b"[]").hexdigest()
            conn.execute(
                "INSERT INTO etf_pdf_full_snapshot VALUES(?,?,?,?,?,?,?,?)",
                ("20260915","465780","마이티 26-09 특수채(AAA)액티브","empty",0,0,str(raw),digest),
            )
            conn.execute(
                "INSERT INTO etf_scale_daily VALUES('20260915','465780',0,485000,485)"
            )
            result = validated_maturity_wind_down_exceptions(conn,"20260915")
            self.assertEqual([item["etf_ticker"] for item in result],["465780"])
            conn.execute(
                "UPDATE etf_scale_daily SET expected_component_count=1 WHERE base_date='20260915'"
            )
            self.assertEqual(validated_maturity_wind_down_exceptions(conn,"20260915"),[])
        conn.close()


if __name__=="__main__":
    unittest.main()
