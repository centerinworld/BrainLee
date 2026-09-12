"""
Project Antigravity: L2 Worker - claude_reviewer (코드 및 전략 교차 검증기)
코드 품질, 무한 루프, 보안 취약점, 하드코딩 방지 및 데이터 무결성 엄격 심사 전담
"""

import ast
import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger("claude_reviewer")

class ClaudeReviewer:
    def __init__(self):
        self.rules = [
            "하드코딩 금지 (API 키, URL 등 환경변수 활용)",
            "무한 루프 방지 및 안전 탈출 조건(Guard clause) 구비",
            "예외 처리 및 로깅 완비",
            "시계열/재무 미래 참조(Look-ahead bias) 방지"
        ]

    @staticmethod
    def _looks_like_unified_diff(code: str) -> bool:
        return bool(code) and ("--- " in code) and ("+++ " in code) and ("@@" in code)

    @staticmethod
    def _extract_added_lines(diff_text: str) -> str:
        """unified diff에서 새로 추가된(+) 줄만 뽑는다. diff는 완결된 파이썬 파일이
        아니므로(예: `@@ -1,1 +1,2 @@` 헤더는 그 자체로 유효한 파이썬 구문이 아님)
        여기서 ast.parse()로 전체 문법 검증을 할 수 없다 - 실제 대상 파일에 적용한
        뒤 검사하는 것이 정답이지만(후속 과제), 그 전까지는 추가된 줄만 정규식 기반으로
        점검해 최소한의 신호라도 준다."""
        return "\n".join(
            line[1:] for line in diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    def review_code_quality(self, code: str, filename: str = "patch.py") -> Dict[str, Any]:
        """
        AST 및 정규식 기반 정적 분석 및 보안/무결성 교차 검증

        2026-09-12 검토에서 지적된 실제 버그 수정: codex_builder가 실제 unified diff를
        반환하게 된 뒤(A02/LLM 연동), 이 함수가 그 diff 텍스트를 그대로 ast.parse()에
        넘겨 `@@ -1,1 +1,2 @@` 같은 hunk 헤더 때문에 거의 항상 SyntaxError로
        REJECTED 처리되고 있었다 - 정상적인 diff까지 부당하게 반려됐다. diff 형식이면
        전체 문법 검사 대신 추가된 줄만 정규식으로 점검하고, 결과를 APPROVED/REJECTED
        이분법이 아니라 "PARTIAL_REVIEW_DIFF"로 명확히 구분해 표시한다.
        """
        if self._looks_like_unified_diff(code):
            added_code = self._extract_added_lines(code)
            findings: List[str] = []
            is_approved = True
            score = 100
            hardcoded_secrets = re.findall(r'(api_key|secret|password|token)\s*=\s*["\'][a-zA-Z0-9_\-]{16,}["\']', added_code, re.IGNORECASE)
            if hardcoded_secrets:
                findings.append("보안 경고: 추가된 줄에서 하드코딩된 Secret/API Key 패턴이 발견되었습니다.")
                score -= 30
                is_approved = False
            risky_calls = sorted(set(re.findall(r'\b(eval|exec)\s*\(', added_code)))
            if risky_calls:
                findings.append(f"보안 위험: 추가된 줄에서 금지된 동적 실행 함수({', '.join(risky_calls)}) 호출 패턴이 발견되었습니다.")
                score -= 40
                is_approved = False
            if not findings:
                findings.append("부분 검토 통과(diff의 추가된 줄만 정규식 기반 점검) - 전체 파일 문법 검증이 아닙니다.")
            return {
                "status": "PARTIAL_REVIEW_DIFF",
                "approved": is_approved,
                "score": max(0, score),
                "findings": findings,
                "filename": filename,
                "is_full_syntax_validated": False
            }

        findings: List[str] = []
        is_approved = True
        score = 100

        # 1. 문법 검사 (AST 파싱)
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {
                "status": "REJECTED",
                "score": 0,
                "findings": [f"파이썬 구문 오류(SyntaxError): {e}"],
                "approved": False
            }

        # 2. 하드코딩된 API 키/비밀번호 패턴 검출
        hardcoded_secrets = re.findall(r'(api_key|secret|password|token)\s*=\s*["\'][a-zA-Z0-9_\-]{16,}["\']', code, re.IGNORECASE)
        if hardcoded_secrets:
            findings.append("보안 경고: 하드코딩된 Secret/API Key가 발견되었습니다. 환경 변수로 분리하세요.")
            score -= 30
            is_approved = False

        # 3. 위험한 함수 호출 검출 (eval, exec 등)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ["eval", "exec"]:
                    findings.append(f"보안 위험: 금지된 동적 실행 함수 `{node.func.id}()` 호출이 감지되었습니다.")
                    score -= 40
                    is_approved = False

        # 4. 무한 루프 위험 검출 (`while True:` without break)
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                has_break = any(isinstance(sub, ast.Break) for sub in ast.walk(node))
                if isinstance(node.test, ast.Constant) and node.test.value is True and not has_break:
                    findings.append("치명적 결함: 탈출 조건(break)이 없는 `while True:` 무한 루프가 감지되었습니다.")
                    score -= 50
                    is_approved = False

        # 5. 빈 except 블록 검출 (`except: pass`)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if not node.body or (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                    findings.append("코드 품질 경고: 에러를 무시하는 `except: pass` 패턴이 발견되었습니다.")
                    score -= 10

        status = "APPROVED" if (is_approved and score >= 70) else "REJECTED"
        if not findings:
            findings.append("모든 무결성, 보안, 코드 스타일 검증 통과 (Clean Code).")

        return {
            "status": status,
            "approved": status == "APPROVED",
            "score": max(0, score),
            "findings": findings,
            "filename": filename
        }

    def validate_patch(self, patch_data: Dict[str, Any]) -> Dict[str, Any]:
        """패치 제안서 심사"""
        fix_code = patch_data.get("suggested_fix", "")
        res = self.review_code_quality(fix_code, patch_data.get("target_file", "patch.py"))
        logger.info(f"패치 교차 검증 완료: 상태={res['status']}, 점수={res['score']}")
        return res
