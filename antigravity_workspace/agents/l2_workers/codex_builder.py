"""
Project Antigravity: L2 Worker - codex_builder (Codex CLI 래퍼 및 자동 패치 생성기)
스택 트레이스 및 에러 로그 분석, 자동 패치(Git fix/*) 브랜치 생성 및 코드 생성 전담
"""

import os
import re
import logging
import subprocess
from typing import Dict, Any, Optional

from llm_client import AntigravityLLMClient
from memory.llm_usage_ledger import LLMUsageLedger

logger = logging.getLogger("codex_builder")

class CodexBuilder:
    def __init__(
        self,
        workspace_root: Optional[str] = None,
        llm_client: Optional[AntigravityLLMClient] = None,
        usage_ledger: Optional[LLMUsageLedger] = None
    ):
        self.workspace_root = workspace_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # generate_patch()가 실제 LLM에게 진짜 diff 초안을 요청하도록 - 이전에는 항상
        # 주석 스캐폴드만 만들었다(A02에서 이미 이 스캐폴드로 자동머지되지 않게는 고쳤으나,
        # 그 밑바탕 자체는 여전히 가짜 diff였다).
        self.llm_client = llm_client or AntigravityLLMClient()
        self.usage_ledger = usage_ledger or LLMUsageLedger()

    def analyze_stack_trace(self, stack_trace: str) -> Dict[str, Any]:
        """스택 트레이스에서 원인 파일, 라인 번호, 에러 유형 추출"""
        lines = stack_trace.strip().split("\n")
        error_line = lines[-1] if lines else "UnknownError"
        
        # 파일 경로 및 라인 번호 추출 (Python traceback 포맷)
        file_matches = re.findall(r'File "([^"]+)", line (\d+), in (.+)', stack_trace)
        
        target_file = None
        line_no = None
        func_name = None
        if file_matches:
            target_file, line_no, func_name = file_matches[-1]
            line_no = int(line_no)

        error_type_match = re.match(r'([A-Za-z0-9_]+Error|[A-Za-z0-9_]+Exception): (.+)', error_line)
        error_type = error_type_match.group(1) if error_type_match else "RuntimeError"
        error_msg = error_type_match.group(2) if error_type_match else error_line

        return {
            "error_type": error_type,
            "error_message": error_msg,
            "target_file": target_file,
            "line_number": line_no,
            "function_name": func_name,
            "raw_stack": stack_trace
        }

    @staticmethod
    def _looks_like_unified_diff(text: str) -> bool:
        """LLM 응답이 실제 unified diff 형식인지 최소 검증 (--- / +++ / @@ 마커).
        이 검사를 통과해야만 has_code_change=True가 될 수 있다 - LLM의 자기 보고를
        신뢰하지 않는다."""
        return bool(text) and ("--- " in text) and ("+++ " in text) and ("@@" in text)

    def generate_patch(self, diagnosis: Dict[str, Any], context_code: Optional[str] = None) -> Dict[str, Any]:
        """
        진단 결과를 바탕으로 실제 LLM에게 unified diff 초안을 요청한다.
        응답이 diff 형식이 아니거나(LLM이 설명문만 반환 등) 모든 provider가 실패하면
        기존 주석 스캐폴드로 저하한다 - 두 경우 모두 has_code_change=False로 명시하고,
        A02에서 만든 "diff 없으면 자동머지 금지" 게이트는 그대로 유지된다(변경 없음).
        """
        error_type = diagnosis.get("error_type", "RuntimeError")
        target_file = diagnosis.get("target_file", "unknown.py")
        error_msg = diagnosis.get("error_message", "")
        line_number = diagnosis.get("line_number")

        branch_name = f"fix/{error_type.lower()}-{int(abs(hash(error_msg)) % 10000)}"
        patch_description = f"Fix {error_type} in {os.path.basename(str(target_file))}: {error_msg}"

        file_snippet = context_code
        if not file_snippet and target_file and os.path.isfile(target_file):
            try:
                with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                    file_snippet = f.read()[:4000]
            except Exception as e:
                logger.debug(f"대상 파일 읽기 실패({target_file}): {e}")

        prompt = (
            "다음 파이썬 런타임 에러를 고치는 패치를 unified diff 형식으로만 작성해줘. "
            "diff 밖에 설명 문장을 쓰지 말고, --- / +++ / @@ 헤더를 포함한 표준 unified diff만 출력해줘.\n\n"
            f"에러 유형: {error_type}\n에러 메시지: {error_msg}\n"
            f"대상 파일: {target_file} (줄 {line_number})\n\n"
            f"현재 코드:\n{file_snippet or '(코드 컨텍스트 없음 - 최소한의 방어 코드 diff를 제안해줘)'}"
        )
        result = self.llm_client.chat_completion_with_meta(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800
        )
        self.usage_ledger.record("codex_builder.generate_patch", result)

        raw_content = result.get("content", "")
        has_code_change = (not result.get("is_fallback")) and self._looks_like_unified_diff(raw_content)

        if has_code_change:
            suggested_fix = raw_content
            status = "DRAFT_DIFF_GENERATED"
        else:
            if result.get("is_fallback"):
                logger.warning(f"모든 LLM provider 실패 - 주석 스캐폴드로 저하: {target_file}")
            else:
                logger.warning(f"[{result.get('provider')}] 응답이 unified diff 형식이 아님 - 주석 스캐폴드로 저하: {target_file}")
            suggested_fix = f"""
# [Auto-Generated Patch by codex_builder - DRAFT ONLY, NOT A REAL DIFF]
# Issue: {error_type} - {error_msg}
# File: {target_file} (Line: {line_number})
# LLM이 실행 가능한 diff를 만들지 못해(전 provider 실패 또는 형식 불일치) 이 스캐폴드로 저하됐다.
"""
            status = "DRAFT_ONLY"

        return {
            "branch_name": branch_name,
            "patch_description": patch_description,
            "suggested_fix": suggested_fix,
            "target_file": target_file,
            "has_code_change": has_code_change,
            "status": status,
            "llm_provider": result.get("provider")
        }

    def execute_cli_command(self, cmd: str) -> Dict[str, Any]:
        """안전한 로컬 CLI 실행 래퍼"""
        try:
            res = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "success": res.returncode == 0,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "returncode": res.returncode
            }
        except Exception as e:
            logger.error(f"CLI 실행 실패: {e}")
            return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}
