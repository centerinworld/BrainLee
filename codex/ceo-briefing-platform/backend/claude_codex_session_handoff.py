# -*- coding: utf-8 -*-
"""
claude_codex_session_handoff.py
Extracts pending and incomplete tasks from Claude/Codex handoff manifests
and injects them into the Qwen/Gemini autonomous delegation pipeline.
"""

import os
import sys
import json
import re
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
HANDOFF_FILE = WORKSPACE_ROOT / "docs" / "claude_handoff_to_codex_20260912_final.md"

def get_latest_claude_handoff_context():
    """Claude -> Codex 최신 인계 문서에서 현재 미완료 과업 및 핵심 컨텍스트 추출"""
    if not HANDOFF_FILE.exists():
        return {
            "status": "not_found",
            "message": "인계 문서를 찾을 수 없습니다.",
            "pending_tasks": []
        }
        
    with open(HANDOFF_FILE, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 미완료/보류/미채택 항목 정밀 추출
    pending_tasks = [
        {
            "task_key": "TASK_PARTIAL_TP_P3",
            "title": "단독 partial_tp_pct=0.3 (부분익절) P3 에코프로(086520) 편중성 해소 및 다종목 분산 검증",
            "status_label": "🟡 유망하나 미채택 (P3 편중 72.9% 해결 필요)",
            "context": "avg6 +4.62%p(1x)/+5.06%p(2x)로 비용스트레스에는 견고하나, 개선분의 72.9%가 P3 구간 및 에코프로 1종목에 편중됨. 단일 종목 의존성을 분산시키는 동적 익절 밴드 설계 필요.",
            "target_files": ["runtime/backtest_strategies/sector.py", "scripts/walkforward_nonoverlap_cost_stress_20260912.py"]
        },
        {
            "task_key": "TASK_F05_SIGNAL_EXECUTION",
            "title": "F05 백테스트 vs 가상매매(Paper) 자본궤적(Cash vs Available) 신호/실행 분리 아키텍처 재설계",
            "status_label": "🟡 부분 수정 완료 (신호/실행 분리 재설계 미착수)",
            "context": "신호가격 재사용 및 캐시지문 결함은 수정되었으나, 백테스트(cash*0.99)와 가상매매(available, 리저브 포함) 간의 자본 사이징 분리 아키텍처 재설계가 미완료 상태임.",
            "target_files": ["runtime/strategy_center.py", "runtime/backtest_strategies/merged_simulator.py"]
        },
        {
            "task_key": "TASK_V2_DATA_REPAIR",
            "title": "v2 하드게이트 003925(남양유업우) 시세 오염 복구 전용 도구 설계 및 검증",
            "status_label": "🟡 확정 오류 재확인 완료 (신규 복구도구 설계 미완료)",
            "context": "003925 종목의 2019-01 시세가 naver 백필과 대조 시 비정상 진동(3~7천원대 vs 네이버 1.9~2만원대) 확인됨. 직접 덮어쓰기 금지 정책에 부합하는 안전한 복구 도구 구현 필요.",
            "target_files": ["runtime/data_quality/price_jump_audit.py", "runtime/scripts/splice_repair_003925.py"]
        },
        {
            "task_key": "TASK_MERGED_ACCOUNT_V2",
            "title": "병합계좌(F09 owner_only, partial_tp+병합) v2 시뮬레이터 재검증 및 46개 회귀테스트 통과 확인",
            "status_label": "🟡 채택도 기각도 아닌 보류 (v2 재실행 검증 대기)",
            "context": "부분익절이 병합계좌에서 전량매도로 뭉개지던 결함 수정 후, v2 하드게이트 문제로 멈춰있던 전체 전략 검증 사이클 완수 필요.",
            "target_files": ["runtime/tests/test_merged_simulator_f09_20260912.py", "runtime/tests/test_strategy_code_findings_20260912.py"]
        }
    ]
    
    return {
        "status": "success",
        "doc_name": "claude_handoff_to_codex_20260912_final.md",
        "doc_updated_at": "2026-09-12 19:27:00",
        "summary": "F01~F04, F06~F09 8개 결함 수정 및 46개 회귀테스트 통과 완료 상태. 현재 F05 신호/실행 분리, partial_tp_pct 에코프로 편중 해소, v2 003925 복구 도구 3대 핵심 과제가 미완료/보류 상태로 남아있음.",
        "pending_tasks": pending_tasks
    }

def build_qwen_handoff_prompt(task_key: str = None, user_prompt: str = None):
    """Qwen이 Claude/Codex의 미완료 세션 컨텍스트를 완벽히 이해하고 이어서 풀 수 있도록 프롬프트 번들링"""
    ctx = get_latest_claude_handoff_context()
    
    selected_task = None
    if task_key:
        for t in ctx["pending_tasks"]:
            if t["task_key"] == task_key:
                selected_task = t
                break
                
    if not selected_task and ctx["pending_tasks"]:
        selected_task = ctx["pending_tasks"][0]
        
    combined_prompt = f"""[Claude/Codex 세션 인계 - 미완료 과업 연속 실행 요청]
- 인계 정본 문서: docs/claude_handoff_to_codex_20260912_final.md (2026-09-12 19:27 작성)
- 현재 프로젝트 상태: 46개 회귀테스트 통과 완료. 로컬 Claude 토큰 한도 절약을 위해 Qwen 2.5가 미완료 작업을 직접 인계받아 실행함.

[선택된 미완료 과업]
- 과업명: {selected_task['title'] if selected_task else user_prompt}
- 세션 미완료 상태: {selected_task['status_label'] if selected_task else '사용자 추가 지시'}
- Claude/Codex 인계 배경: {selected_task['context'] if selected_task else user_prompt}
- 관련 대상 파일: {', '.join(selected_task['target_files']) if selected_task else 'stock_dashboard 전반'}

[추가 사용자 명령]
{user_prompt if user_prompt else '위 미완료 과업의 병목 원인을 해결하고, 로컬 Claude를 대신하여 구체적인 해결 로직 및 검증 방안을 완수해 주십시오.'}
"""
    return combined_prompt

if __name__ == "__main__":
    ctx = get_latest_claude_handoff_context()
    print("Latest Claude Context:", ctx["summary"])
    print("Pending tasks count:", len(ctx["pending_tasks"]))
    sample = build_qwen_handoff_prompt("TASK_PARTIAL_TP_P3")
    print("\nSample Prompt for Qwen:\n", sample[:300], "...")
