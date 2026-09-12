"""
Project Antigravity: L2 Worker - codex_builder (Codex CLI 래퍼 및 자동 패치 생성기)
스택 트레이스 및 에러 로그 분석, 자동 패치(Git fix/*) 브랜치 생성 및 코드 생성 전담
"""

import os
import re
import logging
import subprocess
from typing import Dict, Any, Optional

logger = logging.getLogger("codex_builder")

class CodexBuilder:
    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    def generate_patch(self, diagnosis: Dict[str, Any], context_code: Optional[str] = None) -> Dict[str, Any]:
        """
        진단 결과를 바탕으로 패치 브랜치명, 패치 코드(diff) 및 수정 방안 생성
        """
        error_type = diagnosis.get("error_type", "RuntimeError")
        target_file = diagnosis.get("target_file", "unknown.py")
        error_msg = diagnosis.get("error_message", "")
        
        branch_name = f"fix/{error_type.lower()}-{int(abs(hash(error_msg)) % 10000)}"
        
        # 패치 템플릿 생성
        patch_description = f"Fix {error_type} in {os.path.basename(str(target_file))}: {error_msg}"
        suggested_fix = f"""
# [Auto-Generated Patch by codex_builder]
# Issue: {error_type} - {error_msg}
# File: {target_file} (Line: {diagnosis.get('line_number')})
# Resolution: Added boundary checks, null guards, and safe fallback logic.
"""
        return {
            "branch_name": branch_name,
            "patch_description": patch_description,
            "suggested_fix": suggested_fix,
            "target_file": target_file,
            # suggested_fix는 주석 스캐폴드일 뿐 실행 가능한 diff가 아니므로, 이 값으로
            # "패치 완료"를 표시하지 않는다. 실제 코드 diff 생성 전까지는 항상 False.
            "has_code_change": False,
            "status": "DRAFT_ONLY"
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
