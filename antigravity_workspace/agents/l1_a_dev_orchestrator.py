"""
Project Antigravity: L1-A Dev Orchestrator (개발 및 트레이딩 오케스트레이터)
주식 퀀트 파이프라인 관리, 런타임 에러 감지 및 자가 패치(Self-Healing) 루프 총괄
"""

import os
import sys
import logging
import asyncio
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime

from agents.l2_workers.codex_builder import CodexBuilder
from agents.l2_workers.claude_reviewer import ClaudeReviewer
from agents.l2_workers.quant_trader import QuantTraderWorker

logger = logging.getLogger("l1_a_dev_orchestrator")

class DevOrchestrator:
    def __init__(self, is_mock: bool = True, codex_builder: Optional[CodexBuilder] = None):
        # codex_builder 주입 경로 - 테스트에서 실제 LLM 네트워크 호출 없이 대체할 수 있게 한다.
        self.codex_builder = codex_builder or CodexBuilder()
        self.claude_reviewer = ClaudeReviewer()
        self.quant_trader = QuantTraderWorker(is_mock=is_mock)
        self.self_healing_logs: List[Dict[str, Any]] = []

    async def run_trading_pipeline(self, universe: List[Dict[str, str]], execute_orders: bool = False) -> Dict[str, Any]:
        """
        주식보드 정량 수집 -> 팩터 리밸런싱 -> (execute_orders=True인 경우에만) 모의 주문 체결 파이프라인.
        분석 전용 요청(execute_orders=False)에서는 실행 없이 리밸런싱 비중만 산출한다.
        """
        logger.info(f"[L1-A] 퀀트 파이프라인 개시 (유니버스 종목 수: {len(universe)}, 주문 실행: {execute_orders})")

        # 1. 거시 지표 수집
        macro = await self.quant_trader.fetch_ecos_macro_rate()

        # 2. 종목별 컨센서스 및 리밸런싱 비중 산출
        codes = [item["code"] for item in universe]
        weights = await self.quant_trader.calculate_rebalancing_weights(codes)

        # 3. 주문 실행 (요청된 경우에만, 예산 부족 시 0주 - 강제 최소 1주 없음)
        BUDGET_PER_REBALANCE_KRW = 10_000_000
        executed_orders = []
        if execute_orders:
            for item in universe:
                code = item["code"]
                name = item["name"]
                price = item.get("price", 0.0)
                if price <= 0:
                    executed_orders.append({
                        "stock_code": code, "stock_name": name, "status": "SKIPPED_INVALID_PRICE"
                    })
                    continue

                qty = int(BUDGET_PER_REBALANCE_KRW * weights.get(code, 0.0) / price)
                if qty <= 0:
                    executed_orders.append({
                        "stock_code": code, "stock_name": name, "status": "SKIPPED_INSUFFICIENT_BUDGET", "quantity": qty
                    })
                    continue

                order = await self.quant_trader.execute_order(
                    stock_code=code,
                    stock_name=name,
                    order_type="BUY",
                    price=price,
                    quantity=qty,
                    strategy_name="Quant_L1A_Rebalancer"
                )
                executed_orders.append(order)

        return {
            "status": "SUCCESS",
            "macro_indicator": macro,
            "rebalance_weights": weights,
            "orders_executed": execute_orders,
            "executed_orders": executed_orders,
            "timestamp": datetime.now().isoformat()
        }

    def self_healing_loop(self, error: Exception, context_info: Optional[str] = None) -> Dict[str, Any]:
        """
        [자가 고도화 (Self-Improvement) 및 버그 자동 패치 루프]
        1. Runtime Error 감지
        2. 스택 트레이스 및 해당 소스코드 추출
        3. codexbuilder 에이전트가 패치 코드(Git fix/*) 작성
        4. claudereviewer 에이전트가 품질, 무한 루프, 보안 취약점 교차 검증
        5. 유닛 테스트 및 무결성 검증
        6. 통과 시 main 브랜치 자동 머지 / 핫 리로드 완료
        """
        stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        logger.warning(f"[L1-A Self-Healing] 런타임 예외 포착: {error}")
        
        # 1 & 2. 스택 분석
        diagnosis = self.codex_builder.analyze_stack_trace(stack_trace)
        
        # 3. codex_builder 패치 작성
        patch = self.codex_builder.generate_patch(diagnosis, context_code=context_info)
        
        # 4. claude_reviewer 교차 검증
        review = self.claude_reviewer.validate_patch(patch)

        # 5 & 6. 테스트 및 머지 판단
        # is_auto_merged는 실제 코드 diff + 테스트 실행 + git 커밋/머지가 모두 확인된 경우에만
        # True가 되어야 한다. 현재 codex_builder는 주석 스캐폴드만 생성하고(has_code_change=False),
        # 이 오케스트레이터에는 테스트 실행/git 머지 절차가 구현되어 있지 않으므로 항상 False다.
        is_auto_merged = False
        if not patch.get("has_code_change"):
            merge_blocked_reason = "생성된 패치가 주석 스캐폴드뿐이며 실행 가능한 diff가 없어 자동 머지 불가"
            logger.warning(f"[L1-A Self-Healing] {merge_blocked_reason}: {patch['branch_name']}")
        elif not review["approved"]:
            merge_blocked_reason = f"패치 반려됨: {review['findings']}"
            logger.error(f"[L1-A Self-Healing] {merge_blocked_reason}")
        else:
            merge_blocked_reason = "테스트 실행 및 git 커밋/머지 절차가 구현되지 않아 자동 머지 미수행 - 수동 검토 필요"
            logger.warning(f"[L1-A Self-Healing] {merge_blocked_reason}: {patch['branch_name']}")

        healing_record = {
            "id": len(self.self_healing_logs) + 1,
            "error_type": diagnosis["error_type"],
            "error_message": diagnosis["error_message"],
            "target_file": diagnosis["target_file"],
            "patch_branch": patch["branch_name"],
            "suggested_fix": patch["suggested_fix"],
            "review_status": review["status"],
            "review_score": review["score"],
            "review_findings": review["findings"],
            "is_auto_merged": is_auto_merged,
            "merge_blocked_reason": merge_blocked_reason,
            "status": "PROPOSED_PATCH_PENDING_HUMAN_REVIEW",
            "timestamp": datetime.now().isoformat()
        }
        self.self_healing_logs.append(healing_record)
        return healing_record
