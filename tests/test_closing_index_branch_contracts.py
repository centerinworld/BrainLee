"""`장마감` 지수일봉 단계 — 결함 B(죽은 호출) 회귀 방지.

결함 B (2026-09-25 확정, planner 우선순위 P2):
  `scheduler._job_closing()` 이 `main._save_index_history_today()` 를 호출했지만
  그 함수는 런타임에 **존재하지 않는다**(정의는 옛 모놀리식 `frontend/main.py:610` 에만
  있고 `runtime/main.py` 에는 0건 — 서버는 `runtime/main.py` 를 로드한다). 호출은 매
  거래일 `AttributeError` → `logger.warning` 으로 삼켜져 로그에
  `[장마감] 지수일봉: module 'main' has no attribute '_save_index_history_today'` 를
  12거래일 연속 남겼고, 잡 자체는 원장에 success 로 기록됐다.
  (지수 일봉 `^KS11/^KQ11/^KS200/^KQ150` 은 `KIS일별수집`(18:00)과
  `_startup_catchup` ① 이 실제로 채우고 있어 테이블은 신선했다 = 중복 죽은 경로.)
"""
from __future__ import annotations

import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
SCHEDULER_SRC = (RUNTIME / "scheduler.py").read_text(encoding="utf-8")
MAIN_SRC = (RUNTIME / "main.py").read_text(encoding="utf-8")


class ClosingIndexBranchContractTests(unittest.TestCase):
    def test_dead_call_removed(self) -> None:
        self.assertNotIn("_main._save_index_history_today", SCHEDULER_SRC)
        self.assertNotIn("_save_index_history_today", MAIN_SRC)
        # `import main as _main` 자체는 다른 잡(_job_screener_precompute →
        # main._run_screener_precompute, 런타임에 실재)에서 정당하게 쓰인다.

    def test_replacement_branch_does_not_write(self) -> None:
        """장마감 지수 단계는 조회만 한다(중복 적재 경로 제거)."""
        start = SCHEDULER_SRC.index("def _job_closing")
        end = SCHEDULER_SRC.index("def ", start + 10)
        body = SCHEDULER_SRC[start:end]
        head = body[: body.index("from database import SessionLocal")]
        self.assertIn("SELECT COUNT(*) FROM price_history", head)
        for verb in ("INSERT", "UPDATE", "DELETE"):
            self.assertNotIn(verb, head, f"장마감 지수 단계가 {verb} 를 한다")

    def test_branch_names_the_real_owner_of_index_daily_bars(self) -> None:
        self.assertIn("KIS일별수집(18:00)", SCHEDULER_SRC)

    def test_legacy_monolith_still_has_the_function(self) -> None:
        """삭제한 것이 아니라 '런타임에 없는 함수'였음을 고정한다."""
        legacy = (RUNTIME / "frontend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("def _save_index_history_today", legacy)


if __name__ == "__main__":
    unittest.main()
