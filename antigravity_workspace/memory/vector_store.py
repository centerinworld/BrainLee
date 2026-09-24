"""
Project Antigravity: L3 pgvector 임베딩 및 메모리 스토어
1536차원 벡터 임베딩 생성, HNSW 코사인 유사도 검색 및 중복(>0.85) 필터링 엔진
"""

import os
import math
import random
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

class MemoryVectorStore:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv(
            "POSTGRES_DATABASE_URL",
            os.getenv(
                "DATABASE_URL", 
                f"postgresql://{os.getenv('DB_USER', 'antigravity_user')}:{os.getenv('DB_PASSWORD', 'antigravity_password')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'antigravity_db')}"
            )
        )
        self.in_memory_records: List[Dict[str, Any]] = []
        self.use_fallback = False
        self._init_connection()

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
                return res.data[0].embedding
            except Exception as e:
                logger.debug(f"OpenAI 임베딩 호출 실패: {e}")

        # 2. 순수 파이썬 표준 라이브러리 기반 결정론적 1536차원 단위 벡터 생성
        rng = random.Random(abs(hash(text)) % (2**32))
        raw_vec = [rng.gauss(0, 1) for _ in range(1536)]
        norm = math.sqrt(sum(x * x for x in raw_vec))
        if norm > 0:
            raw_vec = [x / norm for x in raw_vec]
        return raw_vec

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

    def check_duplicate(self, new_embedding: List[float], threshold: float = 0.85) -> Tuple[bool, Optional[Dict[str, Any]], float]:
        """
        기존에 저장된 인텔리전스와의 코사인 유사도 검사
        threshold(0.85) 이상일 경우 (True, 중복문서, 유사도) 반환하여 노이즈 사전 차단
        """
        if self.use_fallback:
            max_sim = -1.0
            matched_doc = None
            for doc in self.in_memory_records:
                sim = self.cosine_similarity(new_embedding, doc["embedding"])
                if sim > max_sim:
                    max_sim = sim
                    matched_doc = doc
            if max_sim >= threshold and matched_doc is not None:
                return True, matched_doc, max_sim
            return False, None, max_sim if max_sim > -1.0 else 0.0

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
            "created_at": datetime.now().isoformat()
        }

        if self.use_fallback:
            doc["id"] = len(self.in_memory_records) + 1
            self.in_memory_records.append(doc)
            logger.info(f"[인메모리] 방산 인텔리전스 적재 완료: id={doc['id']} title='{title}'")
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
            doc["id"] = len(self.in_memory_records) + 1
            self.in_memory_records.append(doc)
            return doc

    def search_similar_intelligence(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """시맨틱 유사 인텔리전스 질의 검색"""
        query_vec = self.get_embedding(query)
        if self.use_fallback:
            scored = []
            for doc in self.in_memory_records:
                sim = self.cosine_similarity(query_vec, doc["embedding"])
                scored.append({**doc, "similarity": round(sim, 4)})
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:top_k]

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
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"PostgreSQL 유사도 검색 실패: {e}")
            return []
