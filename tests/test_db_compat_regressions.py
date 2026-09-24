from __future__ import annotations

import unittest

from db_compat import translate_sqlite_sql


class SqliteCompatibilityRegressionTests(unittest.TestCase):
    def test_parameter_date_offsets_support_both_directions(self) -> None:
        translated = translate_sqlite_sql("SELECT DATE(?, '-10 days'), DATE(?, '+30 days')")
        self.assertIn("%s::date - INTERVAL '10 days'", translated)
        self.assertIn("%s::date + INTERVAL '30 days'", translated)

    def test_nullable_is_annual_preserves_sqlite_truth_tables(self) -> None:
        translated = translate_sqlite_sql(
            "SELECT * FROM financial_data WHERE is_annual IS FALSE OR is_annual IS NOT FALSE"
        )
        self.assertIn("is_annual::text IN ('0', 'false', 'f')) IS TRUE", translated)
        self.assertIn("is_annual::text IN ('0', 'false', 'f')) IS NOT TRUE", translated)
        self.assertNotIn("COALESCE(is_annual", translated)

    def test_json_extract_casts_text_and_uses_jsonpath(self) -> None:
        translated = translate_sqlite_sql("SELECT json_extract(manifest_json, '$.members.x.status')")
        self.assertIn("manifest_json)::jsonb", translated)
        self.assertIn("'$.members.x.status')::jsonpath", translated)
        self.assertIn("jsonb_path_query_first", translated)

    def test_json_extract_supports_dynamic_jsonpath(self) -> None:
        translated = translate_sqlite_sql(
            "SELECT json_extract(rs.manifest_json, '$.members.' || m.period_label || '.status')"
        )
        self.assertIn("m.period_label", translated)
        self.assertIn("::jsonpath", translated)


if __name__ == "__main__":
    unittest.main()
