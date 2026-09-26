"""§11 S0 ② / §15 V1 (2026-09-26 개정) — 터널 경유 '수정·민감 조회'에만 인증, Cloudflare Access JWT 인정."""
import base64
import json
import os
import time
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import security_gate as sg
from security_gate import api_token_gate, is_protected

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.9"}
TEAM, AUD = "team.cloudflareaccess.com", "aud-123"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


KEY = rsa.generate_private_key(65537, 2048)
NUM = KEY.public_key().public_numbers()
JWKS = {"k1": {"kty": "RSA", "kid": "k1", "e": _int_b64(NUM.e), "n": _int_b64(NUM.n)}}


def make_jwt(key=KEY, **over):
    payload = {"aud": [AUD], "iss": f"https://{TEAM}", "exp": time.time() + 600, "nbf": time.time() - 5, "email": "owner@example.com"}
    payload.update(over)
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(payload).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


def _client():
    app = FastAPI()
    app.middleware("http")(api_token_gate)

    @app.get("/api/portfolio")
    def pf():
        return {"holdings": 1}

    @app.get("/api/realtime/prices")
    def prices():
        return {"holdings": 47}

    @app.get("/api/market-regime")
    def regime():
        return {"ok": 1}

    @app.get("/api/dart-excel/download/{job_id}")
    def dl(job_id: str):
        return {"file": job_id}

    @app.post("/api/thing")
    def post_thing():
        return {"done": 1}

    return TestClient(app)


class SecurityGateTests(unittest.TestCase):
    def setUp(self):
        for k in ("API_GATE_MODE", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "CF_ACCESS_ALLOWED_EMAILS"):
            os.environ.pop(k, None)

    def test_policy_writes_and_sensitive_reads_only(self):
        for m, p in (("POST", "/api/anything"), ("DELETE", "/api/portfolio/005930"), ("POST", "/hs/run"), ("GET", "/api/portfolio"),
                     ("GET", "/api/kis-trading/paper/status"), ("GET", "/api/commands/status"), ("GET", "/api/research/quantstats"),
                     ("GET", "/api/realtime/prices"), ("GET", "/api/buy-candidates"), ("GET", "/api/trend/holdings"), ("GET", "/api/us-virtual/positions"),
                     ("GET", "/api/dart-excel/download/abc"), ("GET", "/api/x/export/all"), ("GET", "/api/backup/list"), ("GET", "/openapi.json")):
            self.assertTrue(is_protected(m, p), f"{m} {p}")
        for m, p in (("GET", "/api/market-regime"), ("GET", "/api/tenbagger/empirical-scoreboard"), ("GET", "/api/market-indicators/market-summary"),
                     ("GET", "/api/cash-conversion-signals/top"), ("GET", "/"), ("GET", "/assets/index-abc.js"), ("OPTIONS", "/api/portfolio"),
                     ("POST", "/static/x")):
            self.assertFalse(is_protected(m, p), f"{m} {p}")

    def test_strict_mode_protects_every_api_get(self):
        with mock.patch.dict(os.environ, {"API_GATE_MODE": "strict"}):
            self.assertTrue(is_protected("GET", "/api/market-regime"))
            self.assertFalse(is_protected("GET", "/assets/x.js"))

    def test_local_calls_unaffected(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.post("/api/thing").status_code, 200)
            self.assertEqual(c.get("/api/portfolio").status_code, 200)

    def test_tunnel_ordinary_reads_need_no_token(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            self.assertEqual(_client().get("/api/market-regime", headers=TUNNEL).status_code, 200)

    def test_tunnel_writes_and_sensitive_reads_need_token(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            for path in ("/api/portfolio", "/api/realtime/prices", "/api/dart-excel/download/1"):
                self.assertEqual(c.get(path, headers=TUNNEL).status_code, 401, path)
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "wrong"}).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "s3cret"}).status_code, 200)
            self.assertEqual(c.get("/api/portfolio", headers={**TUNNEL, "Authorization": "Bearer s3cret"}).status_code, 200)

    def test_fail_closed_without_any_auth_configured(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_WRITE_TOKEN", None)
            c = _client()
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 503)
            self.assertEqual(c.get("/api/portfolio", headers=TUNNEL).status_code, 503)
            self.assertEqual(c.post("/api/thing").status_code, 200)   # local still fine
            self.assertEqual(c.get("/api/market-regime", headers=TUNNEL).status_code, 200)   # ordinary reads unaffected

    # ── Cloudflare Access JWT ──
    def test_verify_access_jwt(self):
        good = make_jwt()
        self.assertIsNotNone(sg.verify_access_jwt(good, TEAM, AUD, keys=JWKS))
        self.assertIsNone(sg.verify_access_jwt(make_jwt(exp=time.time() - 10), TEAM, AUD, keys=JWKS))            # expired
        self.assertIsNone(sg.verify_access_jwt(make_jwt(aud=["other"]), TEAM, AUD, keys=JWKS))                    # wrong app
        self.assertIsNone(sg.verify_access_jwt(make_jwt(iss="https://evil.cloudflareaccess.com"), TEAM, AUD, keys=JWKS))
        h, b, s = good.split(".")
        tampered = f"{h}.{_b64(json.dumps({'aud': [AUD], 'iss': f'https://{TEAM}', 'exp': time.time() + 999, 'email': 'attacker@x.com'}).encode())}.{s}"
        self.assertIsNone(sg.verify_access_jwt(tampered, TEAM, AUD, keys=JWKS))                                   # payload changed
        other = rsa.generate_private_key(65537, 2048)
        self.assertIsNone(sg.verify_access_jwt(make_jwt(key=other), TEAM, AUD, keys=JWKS))                        # not signed by Cloudflare
        self.assertIsNone(sg.verify_access_jwt("garbage", TEAM, AUD, keys=JWKS))

    def test_access_login_replaces_token(self):
        env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD, "API_WRITE_TOKEN": "s3cret"}
        with mock.patch.dict(os.environ, env), mock.patch.object(sg, "_fetch_jwks", return_value=JWKS):
            c = _client()
            jwt = make_jwt()
            # header form (edge-injected) and cookie form (browser navigation / link download) both work without any API token
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": jwt}).status_code, 200)
            self.assertEqual(c.get("/api/dart-excel/download/9", headers=TUNNEL, cookies={"CF_Authorization": jwt}).status_code, 200)
            # invalid Access JWT falls back to the token requirement
            bad = make_jwt(key=rsa.generate_private_key(65537, 2048))
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": bad}).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": bad, "X-API-Token": "s3cret"}).status_code, 200)

    def test_access_email_allowlist(self):
        env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD, "CF_ACCESS_ALLOWED_EMAILS": "owner@example.com"}
        with mock.patch.dict(os.environ, env), mock.patch.object(sg, "_fetch_jwks", return_value=JWKS):
            c = _client()
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": make_jwt()}).status_code, 200)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": make_jwt(email="x@y.com")}).status_code, 401)

    def test_access_only_configuration_needs_no_api_token(self):
        env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD}
        with mock.patch.dict(os.environ, env), mock.patch.object(sg, "_fetch_jwks", return_value=JWKS):
            os.environ.pop("API_WRITE_TOKEN", None)
            c = _client()
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 401)     # not 503: Access is the configured auth
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": make_jwt()}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
