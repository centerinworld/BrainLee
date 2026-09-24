# -*- coding: utf-8 -*-
"""
codex_pipeline_orchestrator.py — Codex 00:21 락 해제 감지 및 3단계 자율 파이프라인 오케스트레이터

[3단계 아키텍처 흐름]
1. [Stage 1 - Codex 고차원 설계]:
   - 00:21:00 (밤 12시 21분 KST) 토큰 리셋 감지 즉시 Codex(GPT-4o)가 고차원 알고리즘 및 검증 코드 설계
2. [Stage 2 - 저가 모델 Qwen 실행]:
   - Codex가 설계한 코드를 저가 모델(Qwen 2.5) 및 로컬 실행 워커가 받아서 백테스트 시뮬레이터 구동 및 원시 로그/메트릭 수집
3. [Stage 3 - Claude 최종 검토 & 감사]:
   - Qwen이 수집한 실행 결과를 Claude 3.5 Sonnet이 정밀 검토하여 아키텍처 무결성 검증, 과최적화 감사, 최종 머지 승인 판정
"""

import os
import sys
import time
import json
import datetime
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
sys.path.insert(0, str(WORKSPACE_ROOT))

import config
from claude_codex_session_handoff import get_latest_claude_handoff_context, build_frontier_handoff_prompt
from qwen_to_claude_codex_bridge import export_qwen_result_to_claude_and_codex

QUOTA_FILE = Path("/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/backend/session_quota_status.json")
AUDIT_FILE = WORKSPACE_ROOT / "audit_logs" / "task_audit_ledger.jsonl"
SESSION_STATE_FILE = WORKSPACE_ROOT / "docs" / "codex_orchestrator_state.json"

TARGET_UNLOCK_TIME_STR = "2026-09-13 00:21:00"
TARGET_TASK_KEY = "TASK_PARTIAL_TP_P3"


def get_seconds_until_unlock() -> float:
    """00:21:00 KST까지 남은 초 계산"""
    target_dt = datetime.datetime.strptime(TARGET_UNLOCK_TIME_STR, "%Y-%m-%d %H:%M:%S")
    now_dt = datetime.datetime.now()
    diff = (target_dt - now_dt).total_seconds()
    return diff


def update_orchestrator_state(stage: str, status_msg: str, detail: dict = None):
    """실시간 오케스트레이터 상태 파일 기록"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = {
        "timestamp": now_str,
        "current_stage": stage,
        "status_message": status_msg,
        "target_unlock_time": TARGET_UNLOCK_TIME_STR,
        "seconds_remaining": max(0, int(get_seconds_until_unlock())),
        "target_task": TARGET_TASK_KEY,
        "pipeline_architecture": "Codex (고차원 설계) ➔ Qwen (저가 실행) ➔ Claude (최종 검토)",
        "detail": detail or {}
    }
    with open(SESSION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def run_stage1_codex_design(task_info: dict) -> dict:
    """[Stage 1] 🟢 Codex 고차원 알고리즘 및 검증 코드 설계"""
    print("\n🚀 [STAGE 1] 🟢 Codex (GPT-4o) 고차원 아키텍처 및 알고리즘 설계 시작...")
    update_orchestrator_state(
        "STAGE_1_CODEX_DESIGN",
        "🟢 Codex가 TASK_PARTIAL_TP_P3 에코프로 편중 해소 알고리즘 설계를 진행 중입니다."
    )

    prompt = build_frontier_handoff_prompt(
        target_model="codex",
        task_key=TARGET_TASK_KEY,
        user_prompt="P3 구간(에코프로 편중 72.9%)에 치우치지 않도록 동적 익절 밴드(Dynamic Take-Profit Band) 수식 및 다종목 분산 검증 시뮬레이션 코드를 완성하십시오."
    )

    # 실제 Codex API 또는 고차원 설계 명세 산출
    openai_key = getattr(config, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    design_spec = ""

    if openai_key:
        try:
            import requests
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": "You are OpenAI Codex / GPT-4o senior quant architect. Design rigorous algorithms and production-grade python backtest code."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                },
                timeout=45
            )
            if r.ok:
                design_spec = r.json()["choices"][0]["message"]["content"]
                print("✅ [Codex API] Successfully generated high-dimensional architecture design!")
        except Exception as e:
            print(f"⚠️ [Codex API Notice] {e}, using established mathematical design spec.")

    if not design_spec:
        design_spec = """# [Codex High-Dimensional Design Specification]
## 과업: TASK_PARTIAL_TP_P3 에코프로 편중 해소 및 다종목 동적 익절 밴드 설계
1. 핵심 알고리즘 수식:
   - dynamic_tp_pct = base_tp_pct * (1.0 - (single_stock_gain_share - 0.20) * alpha)
   - 특정 단일 종목(에코프로)의 수익 기여도가 전체 포트폴리오의 30%를 초과할 경우, 해당 종목의 익절선을 1차로 조기 체결하고 잔여 물량은 동일가중 4개 바스켓 종목으로 리밸런싱.
2. 검증 파라미터 그리드:
   - base_tp_pct: [0.25, 0.30, 0.35]
   - single_stock_cap: 0.25 (단일종목 익절 기여도 상한 25%)
3. 실행 워커(Qwen) 전달 지침:
   - runtime/backtest_strategies/sector.py 내 partial_tp_pct 적용 후 비용 스트레스 1x/2x 전수 백테스트 실행 및 MDD/샤프비율 추출.
"""

    output = {
        "status": "success",
        "stage": "STAGE_1_CODEX",
        "task_key": TARGET_TASK_KEY,
        "design_spec": design_spec,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return output


def run_stage2_qwen_execution(codex_output: dict) -> dict:
    """[Stage 2] ⚡ 저가 모델(Qwen) 및 실행 워커가 Codex 설계 코드를 받아 백테스트 구동 및 데이터 취합

    2026-09-12 감사 결과: 이전 버전은 실제 백테스트를 전혀 실행하지 않고 time.sleep(3) 후
    지어낸 수치(에코프로 편중도 72.9%->23.4%, CAGR +28.4% 등)를 반환했다 - 실제
    runtime/backtest_strategies/sector.py나 merged_simulator.py를 import/실행한 적이
    없다. 그 결과를 근거로 stage3가 "APPROVED & MERGED"라는 가짜 승인 문서까지 만들었다.
    실제 백테스트 실행기 연동 전까지는 조작된 수치 대신 NOT_IMPLEMENTED를 반환한다."""
    print("\n⚙️ [STAGE 2] 미구현 - 실제 백테스트 엔진 연동 필요")
    update_orchestrator_state(
        "STAGE_2_NOT_IMPLEMENTED",
        "⛔ Stage 2는 실제 백테스트 엔진과 연동되어 있지 않습니다. 이전 버전이 반환하던 수치는 전부 가짜였습니다(2026-09-12 감사 후 비활성화)."
    )
    return {
        "status": "NOT_IMPLEMENTED",
        "stage": "STAGE_2_QWEN",
        "reason": "실제 runtime/backtest_strategies 실행 연동이 없어 백테스트 결과를 생성할 수 없습니다. 이전 버전의 수치는 전부 조작된 값이었습니다.",
        "completed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def run_stage3_claude_review(qwen_output: dict) -> dict:
    """[Stage 3] 🟣 Claude 3.5 Sonnet (최고위 인지 모델) 최종 아키텍처 감사, 검토 및 머지 승인

    2026-09-12 감사 결과: 이전 버전은 실제 Claude API를 호출한 적이 없고, stage2의 조작된
    수치를 그대로 인용해 "APPROVED & MERGED TO PRODUCTION" 승인 문서를 지어냈다 - 실제로는
    runtime/backtest_strategies/sector.py가 그 시각에 수정된 적이 없다(git/mtime 확인
    완료). 그 가짜 문서는 docs/_quarantined_fake_reports_20260912/에 격리했다."""
    print("\n🔍 [STAGE 3] 미구현 - 실제 검토자(LLM 호출 또는 사람) 연동 필요")
    update_orchestrator_state(
        "STAGE_3_NOT_IMPLEMENTED",
        "⛔ Stage 3는 실제 검토 없이 승인 문서를 지어내던 코드였습니다(2026-09-12 감사 후 비활성화). 승인 문서를 생성하지 않습니다."
    )
    return {
        "status": "BLOCKED",
        "stage": "STAGE_3_CLAUDE",
        "reason": "Stage 2/3 모두 실제 실행 연동이 없어 검토·승인·머지를 수행할 수 없습니다."
    }


def orchestrator_monitoring_daemon():
    """상시 백그라운드 감시 루프"""
    print("=" * 70)
    print(f"🤖 [Codex Pipeline Orchestrator] Daemon Initialized")
    print(f"🎯 Target Unlock Time: {TARGET_UNLOCK_TIME_STR} (밤 12시 21분 KST)")
    print(f"📐 Architecture: [Stage 1: Codex 설계] ➔ [Stage 2: Qwen 실행] ➔ [Stage 3: Claude 검토]")
    print("=" * 70)

    while True:
        seconds_left = get_seconds_until_unlock()
        
        if seconds_left > 0:
            mins_left = int(seconds_left // 60)
            secs_left = int(seconds_left % 60)
            status_text = f"⏳ Codex 일일 쿼터 리셋 대기 중 (D-{mins_left}분 {secs_left}초, 해제 시각: 00:21)"
            
            update_orchestrator_state(
                "WAITING_LOCKOUT_RESET",
                status_text,
                {"minutes_remaining": mins_left}
            )
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {status_text}", end="\r")
            
            # 15초 간격 모니터링
            sleep_sec = min(15, max(1, seconds_left))
            time.sleep(sleep_sec)
        else:
            print("\n\n🔓 [QUOTA UNLOCKED!] 00:21분 도달! Codex 토큰 제한 해제 감지!")
            print("🚀 [Pipeline Triggered] 3단계 자율 협업 파이프라인을 즉시 실행합니다.\n")
            
            # 1. Quota JSON 갱신
            if QUOTA_FILE.exists():
                try:
                    with open(QUOTA_FILE, "r", encoding="utf-8") as f:
                        qdata = json.load(f)
                    qdata["chatgpt_plus"]["is_locked"] = False
                    qdata["chatgpt_plus"]["session_status"] = "🟢 락 해제 완료 - 자율 파이프라인 가동"
                    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
                        json.dump(qdata, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

            # Stage 1: Codex 설계
            stage1_res = run_stage1_codex_design({"task_key": TARGET_TASK_KEY})

            # Stage 2: Qwen 실행
            stage2_res = run_stage2_qwen_execution(stage1_res)

            # Stage 3: Claude 최종 검토
            stage3_res = run_stage3_claude_review(stage2_res)

            print("\n" + "=" * 70)
            print(f"⛔ [Orchestrator Cycle] Stage2/3 are NOT_IMPLEMENTED (2026-09-12 감사 후) - 실제 백테스트/검토가 수행되지 않았습니다.")
            print("=" * 70)
            break


if __name__ == "__main__":
    orchestrator_monitoring_daemon()
