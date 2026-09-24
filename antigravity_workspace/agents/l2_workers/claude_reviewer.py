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

    def review_code_quality(self, code: str, filename: str = "patch.py") -> Dict[str, Any]:
        """
        AST 및 정규식 기반 정적 분석 및 보안/무결성 교차 검증
        """
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
