"""
Project Antigravity: 목표(Goals) 레지스트리 + 이중 AI 검증 완료 게이트.

소유자가 제시한 핵심 목표를 영속적으로 기록한다. "핵심 목표"(is_key_goal=True, 한 번의
사건으로 달성 여부를 판정할 수 있는 목표)는 서로 다른 AI 검증자 최소 2명이 독립적으로
"100% 완료"라고 판단해야만 COMPLETED로 종결된다 - 이 세션 내내 지켜온 원칙과 같은 이유:
한 모델(자기 자신 포함)의 판단만으로 "완료"를 자칭하지 않는다.

지속형 목표(is_continuous=True, 예: 시장 인텔리전스 제공처럼 끝이 없는 역량)는 단일
완료 사건이 없으므로 이 게이트를 적용하지 않고 ONGOING 상태와 진행 로그로만 추적한다.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_GOALS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goals_registry.sqlite3")

VERDICT_COMPLETE = "COMPLETE_100"
VERDICT_INCOMPLETE = "NOT_COMPLETE"


class GoalsRegistry:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_GOALS_DB_PATH
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                goal_id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                category TEXT,
                is_key_goal INTEGER,
                is_continuous INTEGER,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goal_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT,
                verifier TEXT,
                verdict TEXT,
                confidence REAL,
                evidence TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goal_progress_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT,
                note TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def add_goal(
        self, goal_id: str, title: str, description: str, category: str,
        is_key_goal: bool, is_continuous: bool, status: Optional[str] = None
    ) -> None:
        """멱등 - 이미 등록된 goal_id는 다시 만들지 않는다(재시작 시 중복 생성 방지)."""
        if self.get_goal(goal_id):
            return
        conn = sqlite3.connect(self.db_path)
        now = datetime.now().isoformat()
        conn.execute("""
            INSERT INTO goals (goal_id, title, description, category, is_key_goal, is_continuous, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            goal_id, title, description, category, int(is_key_goal), int(is_continuous),
            status or ("ONGOING" if is_continuous else "ACTIVE"), now, now
        ))
        conn.commit()
        conn.close()

    def list_goals(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM goals ORDER BY created_at ASC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def log_progress(self, goal_id: str, note: str) -> None:
        conn = sqlite3.connect(self.db_path)
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO goal_progress_log (goal_id, note, created_at) VALUES (?, ?, ?)",
            (goal_id, note, now)
        )
        conn.execute("UPDATE goals SET updated_at=? WHERE goal_id=?", (now, goal_id))
        conn.commit()
        conn.close()

    def get_progress_log(self, goal_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM goal_progress_log WHERE goal_id=? ORDER BY created_at DESC LIMIT ?",
            (goal_id, limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def record_verification(
        self, goal_id: str, verifier: str, verdict: str,
        confidence: Optional[float] = None, evidence: str = ""
    ) -> Dict[str, Any]:
        """검증자 한 명의 판정을 기록한다. 이 호출로 완료 조건(핵심 목표: 서로 다른
        검증자 2명 이상이 최신 판정에서 모두 COMPLETE_100)이 충족되면 자동으로
        goals.status를 COMPLETED로 갱신한다."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO goal_verifications (goal_id, verifier, verdict, confidence, evidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (goal_id, verifier, verdict, confidence, evidence, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return self._maybe_complete(goal_id)

    def get_verifications(self, goal_id: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM goal_verifications WHERE goal_id=? ORDER BY created_at DESC", (goal_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _maybe_complete(self, goal_id: str) -> Dict[str, Any]:
        goal = self.get_goal(goal_id)
        if not goal or goal["status"] == "COMPLETED" or goal["is_continuous"]:
            return {"completed": False, "reason": "지속형 목표이거나 이미 종결됨" if goal else "존재하지 않는 목표"}

        verifications = self.get_verifications(goal_id)  # 최신순(DESC)
        latest_by_verifier: Dict[str, str] = {}
        for v in verifications:
            if v["verifier"] not in latest_by_verifier:
                latest_by_verifier[v["verifier"]] = v["verdict"]

        distinct_complete = [name for name, verdict in latest_by_verifier.items() if verdict == VERDICT_COMPLETE]
        if len(distinct_complete) >= 2:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE goals SET status='COMPLETED', updated_at=? WHERE goal_id=?",
                (datetime.now().isoformat(), goal_id)
            )
            conn.commit()
            conn.close()
            self.log_progress(goal_id, f"이중 AI 검증 통과({', '.join(distinct_complete)}) - COMPLETED로 종결")
            return {"completed": True, "verifiers": distinct_complete}
        return {"completed": False, "verifiers_agreeing": distinct_complete, "needed": 2}


default_registry = GoalsRegistry()


SEED_GOALS: List[Dict[str, Any]] = [
    {
        "goal_id": "goal_1_zero_defect_data",
        "title": "stock_dashboard 수집 데이터 무결점",
        "description": (
            "stock_dashboard에 수집된 모든 데이터(시세, 재무, 배당/분할, 캘린더 등)에 "
            "결측·중복·단위오류·미래참조가 없어야 한다."
        ),
        "category": "data_integrity",
        "is_key_goal": True,
        "is_continuous": False,
    },
    {
        "goal_id": "goal_2_800pct_strategy",
        "title": "800% 이상 수익 전략 발굴",
        "description": (
            "비용 차감 후 800% 이상 누적수익을 내는 매수/매도 전략을 walk-forward(비중복 구간) "
            "검증까지 통과한 상태로 찾는다. 단일 연속실행 1회 결과만으로는 인정하지 않는다."
        ),
        "category": "strategy_research",
        "is_key_goal": True,
        "is_continuous": False,
    },
    {
        "goal_id": "goal_3_autotrading_ready",
        "title": "자동매매 가능 수준의 시스템 구성",
        "description": (
            "데이터·전략 신호·리스크 헷지(예산/MDD/종목당 한도)·주문 체결 원장까지 실전 연동 "
            "직전 수준으로 구성한다. 실제 자금 연동 자체는 소유자의 별도 승인 대상(2026-09-12 확인: 보류)."
        ),
        "category": "autotrading_infra",
        "is_key_goal": True,
        "is_continuous": False,
    },
    {
        "goal_id": "goal_4_market_intelligence",
        "title": "시장 인텔리전스 제공",
        "description": "주요 지표의 변동을 감지하고 핵심 요약을 지속적으로 제공한다.",
        "category": "market_intelligence",
        "is_key_goal": False,
        "is_continuous": True,
    },
    {
        "goal_id": "goal_5_self_diagnosis",
        "title": "사람 개입 없는 자가진단/개선",
        "description": (
            "시스템이 스스로 문제를 판단하고 개선안을 만든다. 자동 병합/승격은 여전히 "
            "검증 게이트(A02 self_healing_loop, 이 레지스트리의 이중검증 등)를 통과해야 한다."
        ),
        "category": "self_improvement",
        "is_key_goal": False,
        "is_continuous": True,
    },
    {
        "goal_id": "goal_6_full_automation",
        "title": "시스템 개선/확장의 완전 자동화",
        "description": "사람이 목표만 주면 시스템이 지속적으로 개선을 진행한다.",
        "category": "automation",
        "is_key_goal": False,
        "is_continuous": True,
    },
    {
        "goal_id": "goal_7_executive_briefing",
        "title": "대통령급 지식 브리핑 비서",
        "description": (
            "세계 경제·주식시장·방산산업·국제정세의 변화와 시사점을 정기적으로 보고서 형태로 제공한다."
        ),
        "category": "executive_briefing",
        "is_key_goal": False,
        "is_continuous": True,
    },
]


def seed_default_goals(registry: Optional[GoalsRegistry] = None) -> None:
    registry = registry or default_registry
    for g in SEED_GOALS:
        registry.add_goal(**g)
