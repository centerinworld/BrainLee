"""
knowledge_rag_engine.py 회귀 테스트 (2026-09-13 검수, R09).

get_vault_statistics()가 실제 수집 파이프라인이 전혀 없는데도
status="🟢 상시 백그라운드 수집 및 인덱싱 가동 중"이라는 거짓 상태를 반환하던 결함과,
seed_comprehensive_intelligence()가 적재하는 문서(코드에 하드코딩된 고정 예시 텍스트)가
실제 수집 문서와 구분 없이 반환되던 결함을 검증한다. 실제 vault DB 경로를 건드리지
않도록 매 테스트마다 임시 디렉터리로 VAULT_DIR/DB_PATH를 격리한다.
"""

import importlib
import tempfile
import unittest
from pathlib import Path

import knowledge_rag_engine as rag


class KnowledgeVaultHonestyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_vault_dir = rag.VAULT_DIR
        self._orig_db_path = rag.DB_PATH
        rag.VAULT_DIR = Path(self.tmp.name)
        rag.DB_PATH = rag.VAULT_DIR / "knowledge_vault.db"

    def tearDown(self):
        rag.VAULT_DIR = self._orig_vault_dir
        rag.DB_PATH = self._orig_db_path
        self.tmp.cleanup()

    def test_statistics_do_not_claim_live_collection_when_only_seed_exists(self):
        rag.init_vault_db()
        rag.seed_comprehensive_intelligence()
        stats = rag.get_vault_statistics()
        self.assertFalse(stats["is_live_collection"])
        self.assertEqual(stats["real_collected_document_count"], 0)
        self.assertGreater(stats["seed_document_count"], 0)
        self.assertEqual(stats["seed_document_count"], stats["total_documents"])
        self.assertNotIn("상시 백그라운드 수집 및 인덱싱 가동 중", stats["status"])
        self.assertIn("정적 시드 데이터", stats["status"])

    def test_search_results_are_flagged_as_seed_data(self):
        rag.init_vault_db()
        rag.seed_comprehensive_intelligence()
        results = rag.search_knowledge_vault("한국항공우주", limit=10)
        self.assertGreater(len(results), 0)
        self.assertTrue(all(r["is_seed_data"] is True for r in results))

    def test_statistics_reflect_real_documents_once_added(self):
        rag.init_vault_db()
        rag.seed_comprehensive_intelligence()
        import sqlite3
        conn = sqlite3.connect(str(rag.DB_PATH))
        conn.execute("""
            INSERT INTO vault_documents
            (doc_id, category, source_name, title, author_or_broker, published_date,
             target_stock_code, target_stock_name, file_path, url, content_length, collected_at, is_seed)
            VALUES ('REAL_DOC_1','실제수집','테스트 소스','실제 문서','','2026-09-13','047810','한국항공우주','','',0,'2026-09-13T00:00:00',0)
        """)
        conn.commit()
        conn.close()
        stats = rag.get_vault_statistics()
        self.assertEqual(stats["real_collected_document_count"], 1)
        self.assertTrue(stats["status"].startswith("🟢"))

    def test_vault_documents_table_gains_is_seed_column_on_existing_db(self):
        """이미 존재하는(구버전 스키마) vault DB를 열어도 is_seed 컬럼이 없으면 추가돼야 한다."""
        import sqlite3
        rag.VAULT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(rag.DB_PATH))
        conn.execute("""
            CREATE TABLE vault_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE, category TEXT,
                source_name TEXT, title TEXT, author_or_broker TEXT, published_date TEXT,
                target_stock_code TEXT, target_stock_name TEXT, file_path TEXT, url TEXT,
                content_length INTEGER, collected_at TEXT
            )
        """)
        conn.commit()
        conn.close()
        rag.init_vault_db()  # 구버전 스키마 위에서도 예외 없이 마이그레이션돼야 함
        conn = sqlite3.connect(str(rag.DB_PATH))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(vault_documents)")}
        conn.close()
        self.assertIn("is_seed", cols)


if __name__ == "__main__":
    unittest.main()
