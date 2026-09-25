"""§11 S0 ② — 터널 경유 쓰기·민감 API 토큰 게이트."""
import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from security_gate import api_token_gate, is_protected

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.9"}


def _client():
    app = FastAPI()
    app.middleware("http")(api_token_gate)

    @app.get("/api/portfolio")
    def pf():
        return {"holdings": 1}

    @app.get("/api/research/quantstats")
    def qs():
        return {"ok": 1}

    @app.get("/api/market-regime")
    def regime():
        return {"ok": 1}

    @app.post("/api/thing")
    def post_thing():
        return {"done": 1}

    return TestClient(app)


class SecurityGateTests(unittest.TestCase):
    def test_protection_rules(self):
        self.assertTrue(is_protected("POST", "/api/anything"))
        self.assertTrue(is_protected("DELETE", "/api/portfolio/005930"))
        self.assertTrue(is_protected("POST", "/hs/run"))
        self.assertTrue(is_protected("GET", "/api/portfolio"))
        self.assertTrue(is_protected("GET", "/api/kis-trading/paper/status"))
        self.assertTrue(is_protected("GET", "/api/commands/status"))
        self.assertTrue(is_protected("GET", "/api/research/quantstats"))
        self.assertFalse(is_protected("GET", "/api/market-regime"))
        self.assertFalse(is_protected("OPTIONS", "/api/portfolio"))
        self.assertFalse(is_protected("POST", "/static/x"))

    def test_local_calls_unaffected(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.post("/api/thing").status_code, 200)
            self.assertEqual(c.get("/api/portfolio").status_code, 200)

    def test_tunnel_requires_token(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.get("/api/portfolio", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "wrong"}).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "s3cret"}).status_code, 200)
            self.assertEqual(c.get("/api/portfolio", headers={**TUNNEL, "Authorization": "Bearer s3cret"}).status_code, 200)

    def test_tunnel_public_reads_still_open(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.get("/api/market-regime", headers=TUNNEL).status_code, 200)
            self.assertEqual(c.get("/api/research/quantstats", headers=TUNNEL).status_code, 401)   # 연구 API는 토큰 필요

    def test_fail_closed_without_configured_token(self):
        env = {k: v for k, v in os.environ.items() if k != "API_WRITE_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            c = _client()
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 503)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": ""}).status_code, 503)
            self.assertEqual(c.post("/api/thing").status_code, 200)   # local still fine


if __name__ == "__main__":
    unittest.main()
