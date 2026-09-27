# -*- coding: utf-8 -*-
"""
qwen_to_claude_codex_bridge.py
Bidirectional Collaboration Bridge: Qwen/Pipeline -> Claude & Codex
Writes execution reports, generated code, and resolution statuses to:
1. docs/qwen_handoff_to_claude_and_codex_latest.md (Primary Manifest)
2. docs/qwen_handoff_to_claude_and_codex_{date}_{task_id}.md (Archive)
3. audit_logs/task_audit_ledger.jsonl (Immutable Codex Ledger)
4. .claude/session.log (Claude local session trace)
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
    Qwen이 수행한 작업 결과를 로컬 Claude와 Codex가 즉시 읽고 이어서 작업할 수 있도록
    정식 인계 마크다운 문서 및 감사 장부로 역전달(Reverse Handoff)
    """
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y%m%d_%H%M%S")
    task_id = task.get("id", "tsk-unknown")
    title = task.get("title", "작업 결과")
    engine_used = task.get("engine_used", "Qwen 2.5 Coder & 당사 3단계 파이프라인")
    tokens_saved = task.get("claude_tokens_saved", 0)
    elapsed = task.get("elapsed_sec", 0)
    full_output = task.get("full_output", "산출물 내용 없음")
    is_handoff = task.get("is_handoff", False)
    task_key = task.get("task_key", "CUSTOM_TASK")

    # 1. Claude 및 Codex 공용 정본 인계 마크다운 작성
    handoff_markdown = f"""# Qwen ➔ Claude & Codex 작업 결과 인계 보고서 (정본)

- **작성 일시:** {now_str}
- **작업 ID:** `{task_id}`
- **수행 주체:** `{engine_used}` (M4 Mac Mini 로컬 인프라)
- **로컬 Claude 토큰 절감 효과:** **{tokens_saved:,} Tokens Saved (비용 $0 소모)**
- **처리 소요 시간:** {elapsed}초
- **세션 인계 여부:** {'✅ Claude/Codex 미완료 과업 승계' if is_handoff else '사용자 신규 지시 과업'}
- **과업 식별 키:** `{task_key}`

---

## 1. 지시 과업 개요
> **과업명:** {title}

## 2. Qwen 자율 수행 결과 및 핵심 산출물 전문
아래 내용은 로컬 Claude의 토큰을 보존하기 위해 Qwen 2.5(로컬/Groq) 및 3단계 파이프라인이 완수한 작업 결과입니다.
Claude 및 Codex는 본 문서를 읽고 즉시 다음 단계(코드 머지, 회귀테스트, 추가 배포)를 매끄럽게 이어서 진행할 수 있습니다.

```markdown
{full_output}
```

---

## 3. Claude & Codex를 위한 후속 조치 가이드 (Next Steps for Claude/Codex)
1. **코드 반영 확인:** Qwen이 제안하거나 수정한 로직이 관련 소스코드에 부합하는지 최종 승인(Verify)하십시오.
2. **회귀테스트 실행:** `runtime/venv/bin/python3 -m unittest`로 기존 46개 회귀테스트와 충돌이 없는지 확인하십시오.
3. **토큰 절감 확인:** 본 작업을 Qwen이 완수함에 따라 로컬 Claude의 세션 쿼터 **{tokens_saved:,} 토큰**이 완벽히 보존되었습니다.
4. **연속 작업 지시:** 본 결과를 바탕으로 후속 작업이 필요할 경우, 본 파일(`docs/qwen_handoff_to_claude_and_codex_latest.md`)을 그대로 참조(Reference)하여 대화를 이어가시면 됩니다.

---
*Generated automatically by Project AGI - Qwen-to-Claude Bi-directional Collaboration Bridge*
"""

    # 1) Latest 정본 파일 저장
    with open(LATEST_HANDOFF_MD, "w", encoding="utf-8") as f:
        f.write(handoff_markdown)
        
    # 2) Timestamped 아카이브 파일 저장
    archive_file = DOCS_DIR / f"qwen_handoff_to_claude_and_codex_{date_str}_{task_id}.md"
    with open(archive_file, "w", encoding="utf-8") as f:
        f.write(handoff_markdown)

    # 3) Codex 영구 불변 감사장부 (task_audit_ledger.jsonl) 기록
    audit_record = {
        "timestamp": now_str,
        "event_type": "QWEN_TO_CLAUDE_HANDOFF",
        "task_id": task_id,
        "task_title": title,
        "engine_used": engine_label if 'engine_label' in locals() else engine_used,
        "claude_tokens_saved": tokens_saved,
        "handoff_doc_path": str(LATEST_HANDOFF_MD),
        "archive_doc_path": str(archive_file)
    }
    with open(AUDIT_LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_record, ensure_ascii=False) + "\n")

    # 4) Claude 로컬 세션 로그 (.claude/session.log)에 이벤트 주입
    try:
        with open(SESSION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')} — [Qwen Handoff] task={task_id} tokens_saved={tokens_saved:,} doc={LATEST_HANDOFF_MD.name}\n")
    except Exception as e:
        print(f"Failed writing to session.log: {e}")

    return {
        "status": "success",
        "message": "Qwen 수행 결과가 로컬 Claude 및 Codex 인계 문서(docs/qwen_handoff_...md)에 완벽히 전달되었습니다.",
        "latest_file": str(LATEST_HANDOFF_MD),
        "archive_file": str(archive_file),
        "tokens_saved": tokens_saved,
        "prompt_snippet": f"Qwen이 수행한 작업 결과 정본이 docs/qwen_handoff_to_claude_and_codex_latest.md 에 생성되었습니다. 해당 문서를 참조하여 작업을 이어가세요."
    }

def get_latest_handoff_status():
    """가장 최근에 Claude/Codex로 전달된 인계 파일 정보 반환"""
    if not LATEST_HANDOFF_MD.exists():
        return {"exists": False, "message": "아직 역전달된 인계 파일이 없습니다."}
        
    mtime = os.path.getmtime(LATEST_HANDOFF_MD)
    dt_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    
    with open(LATEST_HANDOFF_MD, "r", encoding="utf-8") as f:
        first_lines = [f.readline() for _ in range(12)]
        
    return {
        "exists": True,
        "file_path": str(LATEST_HANDOFF_MD),
        "updated_at": dt_str,
        "preview": "".join(first_lines)
    }

if __name__ == "__main__":
    sample_task = {
        "id": "tsk-test-999",
        "title": "단독 partial_tp_pct=0.3 (부분익절) P3 에코프로 편중성 해소 및 다종목 분산 검증",
        "engine_used": "🔄 3단계 자동 파이프라인 (Qwen ➔ Gemini ➔ Vault)",
        "claude_tokens_saved": 56736,
        "elapsed_sec": 3.8,
        "full_output": "에코프로 1종목 편중 완화를 위한 동적 이익 실현 밴드 적용 완료.",
        "is_handoff": True,
        "task_key": "TASK_PARTIAL_TP_P3"
    }
    res = export_qwen_result_to_claude_and_codex(sample_task)
    print("Export result:", res)
    status = get_latest_handoff_status()
    print("Latest status:", status)
