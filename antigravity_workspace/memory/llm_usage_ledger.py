"""
Project Antigravity: 실제 LLM 호출 사용량/추정비용 원장.

기존 대시보드의 "Quota Progress Cards"(88.5% 등)와 CEO 백엔드의 subscription_quotas는
하드코딩된 고정 숫자였다(이번 세션 조사로 확인) - 실제 호출 횟수/토큰과 무관했다.
이 모듈은 llm_client.AntigravityLLMClient.chat_completion_with_meta()가 실제로 반환한
usage(토큰수)만 기록한다. 추정비용은 대략적인 공개 가격표 기준이며 정확한 청구액이
아니다 - 실제 사용량(횟수/토큰) 자체는 정확하지만, KRW/USD 환산 비용은 참고용이다.
"""

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_USAGE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_usage.sqlite3")

# 1M 토큰당 USD 추정치 (공개 요금표 기준 근사값, 실제 청구서와 다를 수 있음).
# gemini/groq 무료 티어는 0으로 두되, 유료 한도 초과 시 실제로는 요금이 붙을 수 있음을 주석으로 남긴다.
ESTIMATED_COST_PER_1M_TOKENS_USD = {
    "gemini": {"input": 0.0, "output": 0.0},       # 무료 티어 가정. 유료 승급 시 갱신 필요.
    "grok": {"input": 0.0, "output": 0.0},          # Groq 무료 티어 가정.
    # 2026-09-13 DeepSeek 공식 가격 문서 재확인 결과 갱신 (deepseek-flash, cache-miss 기준
    # 보수적 상한값 사용 - 실제로는 cache-hit/할인시간대에 더 저렴할 수 있음).
    # https://api-docs.deepseek.com/quick_start/pricing/
    "deepseek": {"input": 0.30, "output": 1.20},
    "openai": {"input": 0.15, "output": 0.60},      # gpt-4o-mini 근사치.
    # codex_cli/claude_cli는 종량제 API가 아니라 기존 구독(ChatGPT/Claude Pro)을 쓴다.
    # 구독 토큰에 API 단가를 곱해 청구액처럼 표시하지 않는다(2026-09-12 검토 지적) -
    # 토큰수는 기록하되 추정비용은 0으로 둔다. 실제 구독 한도 소진 여부는 별도 관찰 필요.
    "codex_lean": {"input": 0.0, "output": 0.0},  # ChatGPT 구독(경량 Codex CLI) - 종량제 아님
    "codex_sdk": {"input": 0.0, "output": 0.0},
    "codex_cli": {"input": 0.0, "output": 0.0},
    "claude_cli": {"input": 0.0, "output": 0.0},
    "none": {"input": 0.0, "output": 0.0},
}


class LLMUsageLedger:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_USAGE_DB_PATH
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller TEXT,
                provider TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                estimated_cost_usd REAL,
                is_fallback INTEGER,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                month_key TEXT NOT NULL,
                reserved_cost_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def record(self, caller: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """llm_client.chat_completion_with_meta()가 반환한 dict를 그대로 넘기면 기록한다."""
        provider = result.get("provider") or "none"
        usage = result.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)

        rates = ESTIMATED_COST_PER_1M_TOKENS_USD.get(provider, {"input": 0.0, "output": 0.0})
        estimated_cost = (
            (prompt_tokens / 1_000_000) * rates["input"]
            + (completion_tokens / 1_000_000) * rates["output"]
        )

        # 예산 예약을 원자적으로 정산한 호출은 이미 같은 usage가 저장됐다. 상위 호출자가
        # 기존 방식대로 record()를 다시 불러도 중복 과금 기록을 만들지 않는다.
        if result.get("_usage_recorded"):
            return {
                "provider": provider, "total_tokens": total_tokens,
                "estimated_cost_usd": estimated_cost, "is_fallback": bool(result.get("is_fallback"))
            }

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO llm_usage_log
            (caller, provider, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, is_fallback, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            caller, provider, result.get("model"), prompt_tokens, completion_tokens, total_tokens,
            estimated_cost, 1 if result.get("is_fallback") else 0, datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
        return {
            "provider": provider, "total_tokens": total_tokens,
            "estimated_cost_usd": estimated_cost, "is_fallback": bool(result.get("is_fallback"))
        }

    def cost_this_month_usd(self, provider: str, now: Optional[datetime] = None) -> float:
        """이번 달(자연월) 해당 provider의 누적 추정비용(USD)을 반환한다.
        DeepSeek 월 예산 한도 확인 등 사전 지출 통제에 쓴다(2026-09-13 소유자 지시)."""
        now = now or datetime.now()
        month_prefix = now.strftime("%Y-%m")
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT SUM(estimated_cost_usd) FROM llm_usage_log WHERE provider=? AND substr(created_at, 1, 7)=?",
                (provider, month_prefix)
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
        finally:
            conn.close()

    def reserve_monthly_budget(
        self, provider: str, reserved_cost_usd: float, limit_usd: float,
        now: Optional[datetime] = None
    ) -> Optional[str]:
        """동시 호출도 월 상한을 함께 통과하지 못하도록 BEGIN IMMEDIATE로 비용을 예약한다."""
        now = now or datetime.now()
        month_key = now.strftime("%Y-%m")
        reservation_id = f"res_{uuid.uuid4().hex}"
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            spent_row = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM llm_usage_log "
                "WHERE provider=? AND substr(created_at, 1, 7)=?",
                (provider, month_key),
            ).fetchone()
            reserved_row = conn.execute(
                "SELECT COALESCE(SUM(reserved_cost_usd), 0) FROM llm_budget_reservations "
                "WHERE provider=? AND month_key=?",
                (provider, month_key),
            ).fetchone()
            committed = float(spent_row[0] or 0.0) + float(reserved_row[0] or 0.0)
            if committed + max(0.0, reserved_cost_usd) > limit_usd:
                conn.rollback()
                return None
            conn.execute(
                "INSERT INTO llm_budget_reservations "
                "(reservation_id, provider, month_key, reserved_cost_usd, created_at) VALUES (?,?,?,?,?)",
                (reservation_id, provider, month_key, max(0.0, reserved_cost_usd), now.isoformat()),
            )
            conn.commit()
            return reservation_id
        finally:
            conn.close()

    def release_reservation(self, reservation_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM llm_budget_reservations WHERE reservation_id=?", (reservation_id,))
            conn.commit()
        finally:
            conn.close()

    def finalize_reservation(
        self, reservation_id: str, caller: str, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """실제 usage 기록과 예약 해제를 한 트랜잭션으로 처리한다."""
        provider = result.get("provider") or "none"
        usage = result.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        rates = ESTIMATED_COST_PER_1M_TOKENS_USD.get(provider, {"input": 0.0, "output": 0.0})
        estimated_cost = (
            (prompt_tokens / 1_000_000) * rates["input"]
            + (completion_tokens / 1_000_000) * rates["output"]
        )
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT provider FROM llm_budget_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if not reservation:
                raise ValueError(f"알 수 없는 reservation_id: {reservation_id}")
            if reservation[0] != provider:
                raise ValueError("예약 provider와 결과 provider가 다릅니다")
            conn.execute("""
                INSERT INTO llm_usage_log
                (caller, provider, model, prompt_tokens, completion_tokens, total_tokens,
                 estimated_cost_usd, is_fallback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                caller, provider, result.get("model"), prompt_tokens, completion_tokens,
                total_tokens, estimated_cost, 1 if result.get("is_fallback") else 0,
                datetime.now().isoformat(),
            ))
            conn.execute("DELETE FROM llm_budget_reservations WHERE reservation_id=?", (reservation_id,))
            conn.commit()
        finally:
            conn.close()
        result["_usage_recorded"] = True
        return {
            "provider": provider, "total_tokens": total_tokens,
            "estimated_cost_usd": estimated_cost, "is_fallback": bool(result.get("is_fallback"))
        }

    def summary(self) -> Dict[str, Any]:
        """실제 누적 사용량 - 대시보드가 하드코딩된 숫자 대신 이걸 조회하도록 다음 단계에서 연결한다."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT provider,
                   COUNT(*) AS call_count,
                   SUM(total_tokens) AS total_tokens,
                   SUM(estimated_cost_usd) AS estimated_cost_usd,
                   SUM(is_fallback) AS fallback_count
            FROM llm_usage_log
            GROUP BY provider
        """).fetchall()
        conn.close()
        by_provider = {r["provider"]: dict(r) for r in rows}
        return {
            "by_provider": by_provider,
            "total_calls": sum(r["call_count"] for r in by_provider.values()),
            "total_estimated_cost_usd": sum(r["estimated_cost_usd"] or 0.0 for r in by_provider.values()),
        }


default_ledger = LLMUsageLedger()
