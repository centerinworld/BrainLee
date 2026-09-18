"""
config/env_loader.py 회귀 테스트.

2026-09-18 실사용 중 재현: approved_code_jobs.py의 테스트 샌드박스는 모든 .env*
경로 읽기를 OS 수준에서 거부한다(비밀 유출 방지). Path.exists()는 그 거부를
PermissionError로 올려보내는데(Python 3.8+부터 의도된 동작 - 조용히 False로
취급하지 않음), load_unified_env()가 이를 처리하지 않아 이 모듈을 import하는
모든 코드가 실제 code-job 샌드박스 안에서 무조건 크래시했다.
"""

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

import config.env_loader as env_loader


class LoadUnifiedEnvTests(unittest.TestCase):
    def test_permission_denied_candidate_is_skipped_not_fatal(self):
        """실제 샌드박스가 .env 경로 읽기를 거부하면 PermissionError가 나는데,
        이전엔 이게 그대로 올라가 import 자체가 죽었다."""
        with patch.object(Path, "exists", side_effect=PermissionError("denied")):
            result = env_loader.load_unified_env()
        self.assertEqual(result, [])

    def test_permission_denied_on_open_is_also_skipped(self):
        """exists()는 통과해도 실제 open()에서 거부될 수 있는 경합 상황도 방어한다."""
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", side_effect=PermissionError("denied")):
            result = env_loader.load_unified_env()
        self.assertEqual(result, [])

    def test_module_import_does_not_crash_under_denied_permissions(self):
        """실제 재현 조건: import 시점(모듈 최상단에서 자동 실행되는
        LOADED_ENV_FILES = load_unified_env())에 크래시하지 않아야 한다."""
        with patch.object(Path, "exists", side_effect=PermissionError("denied")):
            importlib.reload(env_loader)
        self.assertEqual(env_loader.LOADED_ENV_FILES, [])
        importlib.reload(env_loader)  # 정상 상태로 복구


if __name__ == "__main__":
    unittest.main()
