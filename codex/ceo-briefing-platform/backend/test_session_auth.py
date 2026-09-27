"""
session_auth.py / session_deps.py 회귀 테스트.
AGI 작업 생성 엔드포인트가 이 세션을 관리자 게이트로 사용한다.
"""

import time
import unittest

from session_auth import SessionStore


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore(secret="test-secret", ttl_seconds=3600)

    def test_issue_and_resolve(self):
        result = self.store.issue("alice", "admin", "Alice")
        session = self.store.resolve(result["token"])
        self.assertIsNotNone(session)
        self.assertEqual(session["role"], "admin")
        self.assertEqual(session["username"], "alice")

    def test_unknown_token_resolves_to_none(self):
        self.assertIsNone(self.store.resolve("no-such-token"))

    def test_none_token_resolves_to_none(self):
        self.assertIsNone(self.store.resolve(None))

    def test_expired_session_resolves_to_none(self):
        store = SessionStore(secret="test-secret", ttl_seconds=0)
        result = store.issue("bob", "staff", "Bob")
        time.sleep(0.01)
        self.assertIsNone(store.resolve(result["token"]))

    def test_revoke_removes_session(self):
        result = self.store.issue("carol", "ceo", "Carol")
        self.assertTrue(self.store.revoke(result["token"]))
        self.assertIsNone(self.store.resolve(result["token"]))

    def test_revoke_all_for_user(self):
        t1 = self.store.issue("dave", "staff", "Dave")["token"]
        t2 = self.store.issue("dave", "staff", "Dave")["token"]
        other = self.store.issue("erin", "staff", "Erin")["token"]
        removed = self.store.revoke_all_for_user("dave")
        self.assertEqual(removed, 2)
        self.assertIsNone(self.store.resolve(t1))
        self.assertIsNone(self.store.resolve(t2))
        self.assertIsNotNone(self.store.resolve(other))

    def test_fails_closed_without_secret(self):
        """A05: 운영자가 CEO_SESSION_SECRET을 설정하지 않으면 세션 기능 자체를 막는다
        (설정 안 한 상태를 '열려있음'으로 취급하지 않는다)."""
        store = SessionStore(secret="")
        with self.assertRaises(RuntimeError):
            store.issue("alice", "admin", "Alice")

    def test_two_users_get_independent_tokens_and_roles(self):
        """A05 핵심: role은 발급 시점에 서버가 기록한 값만 유효하고, 클라이언트가
        나중에 role을 바꿔 보내도(여기서는 애초에 role을 받는 파라미터가 없음)
        세션이 가진 값이 바뀌지 않는다."""
        admin_token = self.store.issue("admin_user", "admin", "Admin").get("token")
        staff_token = self.store.issue("staff_user", "staff", "Staff").get("token")
        self.assertEqual(self.store.resolve(admin_token)["role"], "admin")
        self.assertEqual(self.store.resolve(staff_token)["role"], "staff")


class TestSessionDeps(unittest.TestCase):
    def test_require_admin_session_rejects_non_admin(self):
        import session_auth
        import session_deps
        from fastapi import HTTPException

        original_store = session_deps.default_store
        try:
            session_deps.default_store = SessionStore(secret="test-secret", ttl_seconds=3600)
            token = session_deps.default_store.issue("staff_user", "staff", "Staff")["token"]
            session = session_deps.require_any_session(authorization=f"Bearer {token}")
            with self.assertRaises(HTTPException) as ctx:
                session_deps.require_admin_session(session=session)
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            session_deps.default_store = original_store

    def test_require_any_session_rejects_missing_header(self):
        import session_deps
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            session_deps.require_any_session(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_require_admin_session_accepts_admin(self):
        import session_deps

        original_store = session_deps.default_store
        try:
            session_deps.default_store = SessionStore(secret="test-secret", ttl_seconds=3600)
            token = session_deps.default_store.issue("admin_user", "admin", "Admin")["token"]
            session = session_deps.require_any_session(authorization=f"Bearer {token}")
            result = session_deps.require_admin_session(session=session)
            self.assertEqual(result["role"], "admin")
        finally:
            session_deps.default_store = original_store


if __name__ == "__main__":
    unittest.main()
