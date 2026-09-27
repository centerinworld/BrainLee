import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from role_gate import RoleGate
import role_gate as rg
from session_auth import SessionStore

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7"}


def build():
    store = SessionStore(secret="s", ttl_seconds=3600)
    admin = store.issue("boss", "admin", "Boss")["token"]
    staff = store.issue("kim", "staff", "Kim")["token"]
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": 1}

    @app.post("/app-login")
    def login():
        return {"token": "x"}

    @app.get("/feeds")
    def feeds(role: str):
        return {"role": role}

    @app.put("/telegram-settings")
    def tg(role: str):
        if role != "admin":
            from fastapi import HTTPException
            raise HTTPException(403, "Admin access required.")
        return {"changed": True}

    app.add_middleware(RoleGate, store=store)
    return TestClient(app), admin, staff


class RoleGateTests(unittest.TestCase):
    def setUp(self):
        rg._ATTEMPTS.clear()

    def test_public_paths_open_without_login(self):
        c, _, _ = build()
        self.assertEqual(c.get("/health", headers=TUNNEL).status_code, 200)
        self.assertEqual(c.post("/app-login", headers=TUNNEL, json={}).status_code, 200)

    def test_everything_else_needs_a_session_from_the_internet(self):
        c, _, _ = build()
        r = c.get("/feeds?role=admin", headers=TUNNEL)
        self.assertEqual((r.status_code, r.json()["detail"]), (401, "login_required"))
        self.assertEqual(c.put("/telegram-settings?role=admin", headers=TUNNEL).status_code, 401)   # the original exploit
        self.assertEqual(c.get("/feeds?role=admin", headers={**TUNNEL, "Authorization": "Bearer nope"}).status_code, 401)

    def test_client_supplied_role_is_overridden_by_the_session(self):
        c, admin, staff = build()
        h = lambda t: {**TUNNEL, "Authorization": f"Bearer {t}"}
        self.assertEqual(c.get("/feeds?role=admin", headers=h(staff)).json(), {"role": "staff"})    # staff cannot claim admin
        self.assertEqual(c.get("/feeds?role=staff", headers=h(admin)).json(), {"role": "admin"})
        self.assertEqual(c.get("/feeds", headers=h(staff)).json(), {"role": "staff"})               # role param not even needed
        self.assertEqual(c.put("/telegram-settings?role=admin", headers=h(staff)).status_code, 403)  # server-verified role gate
        self.assertEqual(c.put("/telegram-settings?role=admin", headers=h(admin)).status_code, 200)

    def test_local_and_internal_calls_are_unchanged(self):
        c, _, _ = build()
        self.assertEqual(c.get("/feeds?role=admin").json(), {"role": "admin"})                      # no tunnel header: legacy behaviour
        self.assertEqual(c.options("/feeds", headers=TUNNEL).status_code in (200, 405), True)

    def test_login_brute_force_is_limited(self):
        c, _, _ = build()
        codes = [c.post("/app-login", headers=TUNNEL, json={}).status_code for _ in range(12)]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10:], [429, 429])


if __name__ == "__main__":
    unittest.main()
