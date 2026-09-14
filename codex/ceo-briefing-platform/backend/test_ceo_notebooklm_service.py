"""
ceo_notebooklm_service.py 회귀 테스트 (2026-09-14).

소유자 지적: "세계 경제와 자본 흐름"/"AI 기술" 섹션이 실제로는 한국항공우주(KAI) 전용
국내 뉴스를 키워드로 재분류한 것뿐이었다(global_authority_count=0 실측 확인). 영어
키워드 매칭과, 국내 기사에 밀려나지 않는 글로벌 우선 배치를 검증한다. 실제 프로덕션
DB(ceo_briefing.db)는 건드리지 않고 매 테스트마다 임시 DB로 교체한다.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ceo_notebooklm_service as svc

SCHEMA = """
CREATE TABLE feed_items (
    id TEXT PRIMARY KEY, feed_type TEXT, title TEXT, summary TEXT, link TEXT,
    source TEXT, article_publisher TEXT, article_category TEXT,
    article_published_at TEXT, published_at TEXT
)
"""


class ThemeMatchingTests(unittest.TestCase):
    def test_english_economy_headline_matches_economy_theme(self):
        self.assertEqual(svc._theme("Fed holds interest rate steady amid inflation concerns"), "economy")

    def test_english_ai_headline_matches_ai_theme(self):
        self.assertEqual(svc._theme("OpenAI and Anthropic race to build safer AI models"), "ai")

    def test_korean_domestic_stock_headline_still_matches_as_before(self):
        self.assertEqual(svc._theme("코스피 6700선 붕괴…AI 속도조절론에 투매"), "ai")

    def test_unrelated_headline_matches_no_theme(self):
        self.assertIsNone(svc._theme("Local bakery wins pastry award"))


class GlobalPriorityCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        conn = sqlite3.connect(self.tmp.name)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self.db_patch = patch.object(svc, "DB", Path(self.tmp.name))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()

    def _insert(self, conn, id, title, feed_type, category, published_at):
        conn.execute(
            "INSERT INTO feed_items (id,feed_type,title,summary,link,source,article_publisher,article_category,article_published_at,published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (id, feed_type, title, title, f"https://x.test/{id}", "src", "pub.test", category, published_at, published_at),
        )

    def test_global_article_is_not_crowded_out_by_more_recent_domestic_matches(self):
        """국내 기사가 더 최신이라도(list 앞쪽에 옴) dashboard()의 테마당 6칸 상한을
        다 채워 글로벌 기사를 밀어내면 안 된다 - '이름만 거창한 부실 보고서' 문제의
        핵심 원인이었다(실측: world/ai 섹션이 100% 국내 기사로만 채워져 있었음)."""
        conn = sqlite3.connect(self.tmp.name)
        # 국내 기사 7건(테마당 상한 6보다 많게)을 글로벌 기사보다 더 최근 시각으로 심어
        # "최신순 90건" 목록에서 글로벌 기사보다 앞자리를 차지하도록 한다.
        for i in range(7):
            self._insert(conn, f"kr{i}", f"코스피 AI 관련주 급락 {i}", "company", "reference", f"2026-09-14T10:0{i}:00Z")
        self._insert(conn, "global1", "OpenAI unveils new safety framework for AI models", "global_intelligence", "global_intelligence", "2026-09-10T00:00:00Z")
        conn.commit()
        conn.close()

        data = svc.dashboard()
        ai_group = next(g for g in data["groups"] if g["key"] == "ai")
        self.assertEqual(len(ai_group["items"]), 6)
        self.assertTrue(any(x["id"] == "global1" for x in ai_group["items"]), "글로벌 AI 기사가 6칸 안에 없음 - 국내 기사에 밀려남")

    def test_global_items_are_fetched_even_outside_the_recent_limit_window(self):
        """갱신이 뜸한 글로벌 소스가 국내 실시간 뉴스 물량에 밀려 상위 limit 안에도
        못 드는 경우에도, 별도 global_limit 조회로 여전히 확보돼야 한다."""
        conn = sqlite3.connect(self.tmp.name)
        for i in range(5):
            self._insert(conn, f"kr{i}", f"국내 속보 {i}", "company", "reference", f"2026-09-14T12:0{i}:00Z")
        self._insert(conn, "global_old", "IMF warns of global recession risk", "global_intelligence", "global_intelligence", "2020-01-01T00:00:00Z")
        conn.commit()
        conn.close()

        items = svc.collect(limit=3, global_limit=60)  # limit=3이면 global_old는 최신순 3건 안에 못 듦
        self.assertTrue(any(x["id"] == "global_old" for x in items))


if __name__ == "__main__":
    unittest.main()
