import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from full_pdf_collector_v4 import remove_sample_publication  # noqa: E402
from full_pdf_collector import connect  # noqa: E402


class SamplePublicationTest(unittest.TestCase):
    def test_sample_publication_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.db"
            conn = connect(path)
            run_id = conn.execute(
                """
                INSERT INTO etf_pdf_full_run(
                    base_date,status,universe_count,is_complete,started_at
                ) VALUES('20260828','complete',1,1,'now')
                """
            ).lastrowid
            conn.execute(
                """
                INSERT INTO etf_pdf_full_publication
                VALUES('20260828',1,1,202,'now','test')
                """
            )
            conn.commit()
            conn.close()
            remove_sample_publication(path, "20260828", run_id)
            conn = connect(path)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM etf_pdf_full_publication").fetchone()[0],
                0,
            )
            status = conn.execute(
                "SELECT status,is_complete FROM etf_pdf_full_run WHERE run_id=?", (run_id,)
            ).fetchone()
            self.assertEqual(tuple(status), ("sample_complete", 0))
            conn.close()


if __name__ == "__main__":
    unittest.main()
