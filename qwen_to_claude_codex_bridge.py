# -*- coding: utf-8 -*-
"""
qwen_to_claude_codex_bridge.py
Bottom-Up Ingestion Pipeline: Small Model (Qwen/Workers) ➔ Frontier Models (Codex & Claude)

[AI 모델 위계 절대 규칙 준수]
1. 소형 모델(Qwen 2.5, 로컬 워커)은 원시 데이터 스크래핑, 1차 텍스트 요약, 팩트 추출 등 '사전 전처리(Preprocessing)'만 담당한다.
2. 대형 모델(Codex, Claude)이 소형 모델의 전처리 산출물을 상향 인계(Bottom-Up Ingestion)받아 고차원 아키텍처 수립 및 최종 코딩을 완수한다.
3. 대형 모델의 고차원 작업을 소형 모델이 이어받아서 종결하는 것은 절대 불가(FORBIDDEN)하며, 반드시 대형 모델의 검토와 머지를 거쳐야 한다.
"""

import os
import sys
import json
import datetime
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
DOCS_DIR = WORKSPACE_ROOT / "docs"
AUDIT_DIR = WORKSPACE_ROOT / "audit_logs"
CLAUDE_DIR = WORKSPACE_ROOT / ".claude"

DOCS_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

LATEST_HANDOFF_MD = DOCS_DIR / "qwen_handoff_to_claude_and_codex_latest.md"
AUDIT_LEDGER_FILE = AUDIT_DIR / "task_audit_ledger.jsonl"
SESSION_LOG_FILE = CLAUDE_DIR / "session.log"

def export_qwen_result_to_claude_and_codex(task: dict):
    """
    소형 모델(Qwen)의 전처리 및 1차 분석 결과를 대형 모델(Codex & Claude)이 상향 인계받을 수 있도록
    정식 상향 인계(Bottom-Up Ingestion Manifest) 마크다운 및 감사 장부 기록
    """
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y%m%d_%H%M%S")
    task_id = task.get("id", "tsk-unknown")
    title = task.get("title", "데이터 전처리 결과")
    engine_used = task.get("engine_used", "Qwen 2.5 보조 전처리 파이프라인")
    tokens_saved = task.get("claude_tokens_saved", 0)
    elapsed = task.get("elapsed_sec", 0)
    full_output = task.get("full_output", "산출물 내용 없음")
    task_key = task.get("task_key", "PREPROCESS_TASK")

    # 1. Claude 및 Codex 공용 정본 상향 인계 마크다운 작성
    handoff_markdown = f"""# [상향 인계 보고서] 소형 모델 전처리 ➔ 대형 모델(Codex/Claude) 인계

- **작성 일시:** {now_str}
- **작업 ID:** `{task_id}`
- **전처리 주체:** `{engine_used}` (소형 보조 모델)
- **수신 대형 모델:** **OpenAI Codex (GPT-4o) & Anthropic Claude 3.5 Sonnet**
- **로컬 대형 모델 토큰 방어량:** **{tokens_saved:,} Tokens Pre-filtered**
- **처리 소요 시간:** {elapsed}초
- **위계 원칙:** 본 문서는 소형 모델의 1차 데이터 전처리 산출물이며, 최종 아키텍처 결정 및 고차원 코딩은 대형 모델(Codex/Claude)이 수행함.
- **과업 식별 키:** `{task_key}`

---

## 1. 전처리 산출물 요약 (Executive Preprocessing Extract)
{full_output}

---

## 2. 대형 모델(Codex / Claude) 후속 조치 가이드
1. 위 소형 모델의 전처리 팩트와 수치를 검증하십시오.
2. 고차원 아키텍처 결함 및 신호/실행 분리 알고리즘을 프론티어 모델 수준에서 직접 구현 및 머지하십시오.
3. 소형 모델 단독으로 본 과업을 종결할 수 없으므로, 대형 모델 회귀 테스트(pytest)를 반드시 통과시키십시오.
"""

    archive_file = DOCS_DIR / f"qwen_handoff_to_claude_and_codex_{date_str}_{task_id}.md"

    try:
        with open(LATEST_HANDOFF_MD, "w", encoding="utf-8") as f:
            f.write(handoff_markdown)
        with open(archive_file, "w", encoding="utf-8") as f:
            f.write(handoff_markdown)
        print(f"✅ [Bottom-Up Bridge] Saved latest handoff for Codex/Claude: {LATEST_HANDOFF_MD}")
    except Exception as e:
        print(f"⚠️ [Bottom-Up Bridge Error] Failed writing handoff markdown: {e}")

    # 2. task_audit_ledger.jsonl 에 영구 감사 기록 추가
    ledger_entry = {
        "timestamp": now_str,
        "event_type": "BOTTOM_UP_PREPROCESSING_HANDOFF",
        "task_id": task_id,
        "task_title": title,
        "engine_used": engine_used,
        "target_frontier_models": ["Codex (GPT-4o)", "Claude 3.5 Sonnet"],
        "claude_tokens_saved": tokens_saved,
        "handoff_doc_path": str(LATEST_HANDOFF_MD),
        "archive_doc_path": str(archive_file)
    }

    try:
        with open(AUDIT_LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")
        print(f"📋 [Audit Ledger] Appended handoff event to {AUDIT_LEDGER_FILE}")
    except Exception as e:
        print(f"⚠️ [Audit Ledger Error] Failed logging handoff event: {e}")

    # 3. .claude/session.log 에 로컬 세션 동기화
    session_line = f"[{now_str}] [BOTTOM_UP_INGESTION] Task {task_id} preprocessed by {engine_used}. Manifest: {LATEST_HANDOFF_MD.name}\n"
    try:
        with open(SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(session_line)
        print(f"📝 [Claude Session Log] Synced to {SESSION_LOG_FILE}")
    except Exception as e:
        print(f"⚠️ [Session Log Error] Failed writing to session log: {e}")

    return {
        "status": "success",
        "latest_handoff_file": str(LATEST_HANDOFF_MD),
        "archive_file": str(archive_file)
    }

if __name__ == "__main__":
    sample_task = {
        "id": "tsk-test-hierarchy",
        "title": "KAI 수주 및 DAPA 10,041건 팩트 데이터 전처리",
        "engine_used": "Qwen 2.5 Preprocessor",
        "claude_tokens_saved": 48200,
        "elapsed_sec": 4.2,
        "full_output": "DAPA 10,041건 데이터 중 KAI KF-21 및 FA-50 2026 핵심 수주 파이프라인 1차 추출 완료.",
        "task_key": "TASK_PREPROCESS_KAI"
    }
    res = export_qwen_result_to_claude_and_codex(sample_task)
    print("Bridge Test Result:", res)
