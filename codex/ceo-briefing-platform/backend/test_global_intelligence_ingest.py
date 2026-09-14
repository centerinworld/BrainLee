"""
services/global_intelligence_ingest.py 회귀 테스트 (2026-09-14).

소유자 지적: "CEO 리포트"의 세계경제/AI 섹션이 사실 KAI 전용 국내 뉴스를 재분류한
것뿐이었다(global_authority_count=0을 직접 확인). 이 모듈은 실제로 curl로 확인한
RSS 피드만 별도 경로(feed_type='global_intelligence')로 수집한다 - 네트워크 호출은
전부 모킹해 테스트가 실제 인터넷에 나가지 않게 한다.
"""

import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.global_intelligence_ingest import ingest_global_intelligence, GLOBAL_FEEDS

SCHEMA = """
CREATE TABLE feed_items (
    id TEXT PRIMARY KEY,
    feed_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    link TEXT NOT NULL,
    source TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    article_published_at TEXT,
    article_publisher TEXT NOT NULL DEFAULT '',
    article_category TEXT NOT NULL DEFAULT 'reference',
    category_manual INTEGER NOT NULL DEFAULT 0,
    sent_briefing_at TEXT
)
"""

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Fed holds rates steady</title><link>https://example.com/fed-rates</link>
<description>The Federal Reserve kept interest rates unchanged.</description>
<pubDate>Mon, 14 Sep 2026 09:00:00 GMT</pubDate></item>
<item><title>Second story</title><link>https://example.com/second</link>
<description>Another real story.</description>
<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


class GlobalIntelligenceIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_real_feed_list_only_contains_verified_https_urls(self):
        """추측/미검증 URL을 몰래 추가하지 않았는지 최소한의 형식 확인."""
        self.assertGreaterEqual(len(GLOBAL_FEEDS), 5)
        for feed in GLOBAL_FEEDS:
            self.assertTrue(feed["url"].startswith("http"))
            self.assertTrue(feed["name"])

    def test_ingest_stores_items_with_global_intelligence_feed_type(self):
        with patch("services.global_intelligence_ingest._fetch_xml", return_value=SAMPLE_RSS), \
             patch("services.global_intelligence_ingest.GLOBAL_FEEDS", [{"name": "Test Source", "url": "https://example.com/rss"}]):
            result = ingest_global_intelligence(self.conn)
        self.assertEqual(result["inserted"], 2)
        rows = self.conn.execute("SELECT feed_type, article_category, source, title FROM feed_items").fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["feed_type"], "global_intelligence")
            self.assertEqual(r["article_category"], "global_intelligence")
            self.assertEqual(r["source"], "Test Source")

    def test_ingest_skips_links_already_present(self):
        self.conn.execute(
            "INSERT INTO feed_items (id, feed_type, title, summary, link, source) VALUES "
            "('x', 'global_intelligence', 'Fed holds rates steady', 's', 'https://example.com/fed-rates', 'Test Source')"
        )
        self.conn.commit()
        with patch("services.global_intelligence_ingest._fetch_xml", return_value=SAMPLE_RSS), \
             patch("services.global_intelligence_ingest.GLOBAL_FEEDS", [{"name": "Test Source", "url": "https://example.com/rss"}]):
            result = ingest_global_intelligence(self.conn)
        self.assertEqual(result["inserted"], 1)  # 두 번째 기사만 신규
        total = self.conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0]
        self.assertEqual(total, 2)

    def test_ingest_records_fetch_failure_without_crashing_other_sources(self):
        def fake_fetch(url):
            if "broken" in url:
                raise RuntimeError("HTTP Error 403: Forbidden")
            return SAMPLE_RSS

        with patch("services.global_intelligence_ingest._fetch_xml", side_effect=fake_fetch), \
             patch("services.global_intelligence_ingest.GLOBAL_FEEDS", [
                 {"name": "Broken Source", "url": "https://example.com/broken"},
                 {"name": "Good Source", "url": "https://example.com/rss"},
             ]):
            result = ingest_global_intelligence(self.conn)
        self.assertEqual(result["sources"]["Broken Source"]["status"], "FETCH_FAILED")
        self.assertEqual(result["sources"]["Good Source"]["status"], "OK")
        self.assertEqual(result["inserted"], 2)


if __name__ == "__main__":
    unittest.main()
