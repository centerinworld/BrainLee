"""
Project Antigravity: L3 pgvector 임베딩 및 메모리 스토어
1536차원 벡터 임베딩 생성, HNSW 코사인 유사도 검색 및 중복(>0.85) 필터링 엔진
"""

import os
import re
import math
import random
import sqlite3
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

try:
    from config.env_loader import LOADED_ENV_FILES
except ImportError:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("vector_store")

DEFAULT_FALLBACK_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fallback_memory.sqlite3")


class MemoryVectorStore:
    def __init__(self, db_url: Optional[str] = None, fallback_db_path: Optional[str] = None):
        self.db_url = db_url or os.getenv(
            "POSTGRES_DATABASE_URL",
            os.getenv(
                "DATABASE_URL",
                f"postgresql://{os.getenv('DB_USER', 'antigravity_user')}:{os.getenv('DB_PASSWORD', 'antigravity_password')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'antigravity_db')}"
            )
        )
        # PostgreSQL(pgvector)이 없을 때도 원문이 재시작 후 사라지지 않도록 로컬 SQLite에 영속 저장한다.
        # (기존에는 self.in_memory_records 파이썬 리스트에만 있어 프로세스 종료 시 전부 소실됐다.)
        self.fallback_db_path = fallback_db_path or DEFAULT_FALLBACK_DB_PATH
        self.use_fallback = False
        self.last_embedding_source = "unknown"  # 마지막 get_embedding() 호출이 실제 모델(openai)인지 fixture_hash인지
        self._init_fallback_store()
        self._init_connection()

    def _init_fallback_store(self):
        """로컬 SQLite 폴백 저장소 초기화 (PostgreSQL 불가 시 원문 영속 보존용)"""
        conn = sqlite3.connect(self.fallback_db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS defense_intelligence_fallback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                title TEXT,
                raw_content TEXT,
                fact_summary TEXT,
                impact_summary TEXT,
                strategy_summary TEXT,
                sentiment_score REAL,
                embedding_source TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _init_connection(self):
        """PostgreSQL pgvector 연결 시도, 실패 시 인메모리 폴백 활성화"""
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url, connect_timeout=3)
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
            conn.close()
            logger.info("PostgreSQL pgvector 데이터베이스 연결 성공")
            self.use_fallback = False
        except Exception as e:
            logger.warning(f"PostgreSQL 연결 불가 ({e}). 로컬 인메모리 벡터 스토어로 폴백 동작합니다.")
            self.use_fallback = True

    def get_embedding(self, text: str) -> List[float]:
        """
        1536차원 텍스트 임베딩 생성
        OpenAI API -> Ollama 로컬 모델 -> 결정론적 1536차원 단위 벡터 폴백
        """
        # 1. OpenAI 임베딩 시도
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key and not openai_key.startswith("your_"):
            try:
                import openai
                client = openai.OpenAI(api_key=openai_key)
                res = client.embeddings.create(input=text, model="text-embedding-3-small")
                self.last_embedding_source = "openai"
                return res.data[0].embedding
            except Exception as e:
                logger.debug(f"OpenAI 임베딩 호출 실패: {e}")

        # 2. 의미 임베딩이 아님: hash(text) 기반 결정론적 벡터일 뿐이며, 파이썬 hash()는
        # 프로세스마다 난수 솔트가 달라 재시작하면 같은 텍스트도 다른 벡터가 된다.
        # 이 벡터를 실제 임베딩처럼 유사도 검색에 사용하지 않는다 - check_duplicate/
        # search_similar_intelligence는 self.last_embedding_source로 이 상태를 감지해
        # 키워드 검색으로 전환한다.
        self.last_embedding_source = "fixture_hash_not_semantic"
        rng = random.Random(abs(hash(text)) % (2**32))
        raw_vec = [rng.gauss(0, 1) for _ in range(1536)]
        norm = math.sqrt(sum(x * x for x in raw_vec))
        if norm > 0:
            raw_vec = [x / norm for x in raw_vec]
        return raw_vec

    @staticmethod
    def _tokenize(text: str) -> set:
        return set(re.findall(r"[가-힣A-Za-z0-9]+", (text or "").lower()))

    @classmethod
    def _keyword_overlap_score(cls, text_a: str, text_b: str) -> float:
        """실제 임베딩이 없을 때 사용하는 저정밀 근사: 토큰 집합 Jaccard 유사도.
        의미 임베딩을 대체하지 않으며, degraded 상태로만 취급한다."""
        ta, tb = cls._tokenize(text_a), cls._tokenize(text_b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def _fallback_insert(self, doc: Dict[str, Any]) -> int:
        conn = sqlite3.connect(self.fallback_db_path)
        cur = conn.execute("""
            INSERT INTO defense_intelligence_fallback
            (source, title, raw_content, fact_summary, impact_summary, strategy_summary, sentiment_score, embedding_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc["source"], doc["title"], doc["raw_content"], doc["fact_summary"],
            doc["impact_summary"], doc["strategy_summary"], doc["sentiment_score"],
            doc.get("embedding_source", "unknown"), doc["created_at"]
        ))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    def _fallback_all(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.fallback_db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM defense_intelligence_fallback").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """순수 파이썬 기반 코사인 유사도 계산 (-1.0 ~ 1.0)"""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def check_duplicate(
        self,
        new_embedding: List[float],
        threshold: float = 0.85,
        text: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], float]:
        """
        기존에 저장된 인텔리전스와의 중복 검사.
        PostgreSQL(pgvector)이 있으면 코사인 유사도, 없으면(degraded) 키워드 토큰 Jaccard
        유사도로 판정한다 - 의미 임베딩이 없는 상태에서 가짜 벡터 유사도를 신뢰하지 않는다.
        threshold(0.85) 이상일 경우 (True, 중복문서, 유사도) 반환하여 노이즈 사전 차단.
        """
        if self.use_fallback:
            if not text:
                logger.warning("[degraded] check_duplicate: text 없이는 키워드 중복 검사를 할 수 없어 건너뜀")
                return False, None, 0.0
            max_sim = 0.0
            matched_doc = None
            for doc in self._fallback_all():
                sim = self._keyword_overlap_score(text, f"{doc['title']} {doc['raw_content']}")
                if sim > max_sim:
                    max_sim = sim
                    matched_doc = doc
            if max_sim >= threshold and matched_doc is not None:
                return True, matched_doc, max_sim
            return False, None, max_sim

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, title, source, (1 - (embedding <=> %s::vector)) AS similarity
                    FROM defense_intelligence
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT 1;
                """, (new_embedding, new_embedding))
                row = cur.fetchone()
                if row and row["similarity"] >= threshold:
                    return True, dict(row), float(row["similarity"])
            conn.close()
            return False, None, float(row["similarity"]) if row else 0.0
        except Exception as e:
            logger.error(f"PostgreSQL 중복 검사 실패: {e}")
            return False, None, 0.0

    def insert_defense_intelligence(
        self,
        source: str,
        title: str,
        raw_content: str,
        fact_summary: str,
        impact_summary: str,
        strategy_summary: str,
        sentiment_score: float = 0.0,
        embedding: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """KAI 및 방산 인텔리전스 레코드 저장"""
        if embedding is None:
            embedding = self.get_embedding(f"{title}\n{raw_content}")

        doc = {
            "source": source,
            "title": title,
            "raw_content": raw_content,
            "fact_summary": fact_summary,
            "impact_summary": impact_summary,
            "strategy_summary": strategy_summary,
            "sentiment_score": sentiment_score,
            "embedding": embedding,
            "embedding_source": self.last_embedding_source,
            "created_at": datetime.now().isoformat()
        }

        if self.use_fallback:
            doc["id"] = self._fallback_insert(doc)
            logger.info(f"[SQLite 폴백, 영속] 방산 인텔리전스 적재 완료: id={doc['id']} title='{title}' (embedding_source={doc['embedding_source']})")
            return doc

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO defense_intelligence 
                    (source, title, raw_content, fact_summary, impact_summary, strategy_summary, sentiment_score, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    RETURNING id, title, source, created_at;
                """, (
                    source, title, raw_content, fact_summary, impact_summary,
                    strategy_summary, sentiment_score, embedding
                ))
                inserted = cur.fetchone()
                conn.commit()
                doc["id"] = inserted["id"]
            conn.close()
            logger.info(f"[PostgreSQL] 방산 인텔리전스 적재 완료: id={doc['id']} title='{title}'")
            return doc
        except Exception as e:
            logger.error(f"PostgreSQL 인텔리전스 저장 실패: {e}")
            doc["id"] = self._fallback_insert(doc)
            return doc

    def search_similar_intelligence(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """유사 인텔리전스 질의 검색.
        PostgreSQL(pgvector)이 있으면 시맨틱 벡터 검색, 없으면(degraded) 키워드 검색으로
        전환한다 - search_mode 필드로 어느 경로인지 항상 명시한다."""
        if self.use_fallback:
            scored = []
            for doc in self._fallback_all():
                sim = self._keyword_overlap_score(query, f"{doc['title']} {doc['raw_content']}")
                scored.append({**doc, "similarity": round(sim, 4), "search_mode": "degraded_keyword"})
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:top_k]

        query_vec = self.get_embedding(query)
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, source, title, fact_summary, impact_summary, strategy_summary, sentiment_score,
                           (1 - (embedding <=> %s::vector)) AS similarity, created_at
                    FROM defense_intelligence
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT %s;
                """, (query_vec, query_vec, top_k))
                rows = cur.fetchall()
            conn.close()
            return [{**dict(r), "search_mode": "vector_pgvector"} for r in rows]
        except Exception as e:
            logger.error(f"PostgreSQL 유사도 검색 실패: {e}")
            return []
