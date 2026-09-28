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
    from routes.portfolio_access import router as access_router
    from routes.admin_auth import router as admin_router
    app = FastAPI()
    app.middleware("http")(api_token_gate)
    app.include_router(access_router, prefix="/api/portfolio-access")
    app.include_router(admin_router, prefix="/api/admin-auth")

    @app.get("/api/portfolio")
    def pf():
        return {"holdings": 1}

    @app.get("/api/realtime/prices")
    def prices():
        return {"holdings": 47}

    @app.get("/api/market-regime")
    def regime():
        return {"ok": 1}

    @app.get("/api/buy-candidates")
    def cands():
        return {"c": 1}

    @app.get("/api/dart-excel/download/{job_id}")
    def dl(job_id: str):
        return {"file": job_id}

    @app.post("/api/thing")
    def post_thing():
        return {"done": 1}

    return TestClient(app, base_url="https://testserver")   # tunnel traffic is HTTPS; the Secure view cookie is only sent over HTTPS


class SecurityGateTests(unittest.TestCase):
    def setUp(self):
        sg._HITS.clear()
        for k in ("API_GATE_MODE", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "CF_ACCESS_ALLOWED_EMAILS", "API_RATE_LIMIT_PER_MIN", "API_PUBLIC_WRITE_PATTERNS", "API_PUBLIC_WRITE_LIMIT_PER_MIN", "PORTFOLIO_VIEW_PASSWORD", "VIEW_COOKIE_SECRET", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "ADMIN_COOKIE_SECRET"):
            os.environ.pop(k, None)

    def test_access_levels(self):
        for m, p in (("POST", "/api/anything"), ("DELETE", "/api/portfolio/005930"), ("POST", "/hs/run"), ("GET", "/api/dart-excel/download/abc"),
                     ("GET", "/api/x/export/all"), ("GET", "/api/backup/list"), ("GET", "/api/x/settings"), ("GET", "/openapi.json")):
            self.assertEqual(sg.access_level(m, p), "owner", f"{m} {p}")
        for p in ("/api/portfolio", "/api/portfolio/transactions", "/api/buy-candidates", "/api/realtime/prices", "/api/kis-trading/account/summary", "/api/kis-trading/cash-ledger"):
            self.assertEqual(sg.access_level("GET", p), "viewer", p)
        # everything else a friend can browse stays public: watchlist/collect status, paper (virtual) trading, risk gates
        for p in ("/api/commands/watchlist", "/api/commands/collect-status/005930", "/api/kis-trading/paper/positions", "/api/kis-trading/paper/pnl",
                  "/api/kis-trading/paper/orders", "/api/kis-trading/risk-gates/recent", "/api/kis-trading/status", "/api/kis-trading/orders/lifecycle"):
            self.assertEqual(sg.access_level("GET", p), "public", p)
        self.assertEqual(sg.access_level("GET", "/api/portfolio/export/excel"), "owner")       # 내보내기는 비밀번호로 열리지 않는다
        for m, p in (("GET", "/api/market-regime"), ("GET", "/api/trend/holdings"), ("GET", "/api/research/quantstats"),
                     ("GET", "/api/tenbagger/empirical-scoreboard"), ("GET", "/api/cash-conversion-signals/top"), ("GET", "/"), ("GET", "/assets/index-abc.js"),
                     ("OPTIONS", "/api/portfolio"), ("POST", "/static/x"), ("POST", "/api/portfolio-access/login"), ("GET", "/api/portfolio-access/status"),
                     ("POST", "/api/admin-auth/login"), ("GET", "/api/admin-auth/status")):
            self.assertEqual(sg.access_level(m, p), "public", f"{m} {p}")

    def test_public_interactive_writes_are_allowlisted_and_limited(self):
        self.assertEqual(sg.access_level("POST", "/api/commands/analyze/005930"), "public")
        self.assertEqual(sg.access_level("POST", "/api/sector-define/parse"), "public")
        for p in ("/api/commands/screener-refresh", "/api/portfolio/transaction", "/api/reports/generate/005930", "/api/tenbagger/run",
                  "/api/commands/analyze/005930/extra"):
            self.assertEqual(sg.access_level("POST", p), "owner", p)
        self.assertEqual(sg.access_level("DELETE", "/api/commands/watchlist/005930"), "owner")
        with mock.patch.dict(os.environ, {"API_PUBLIC_WRITE_PATTERNS": ""}):
            self.assertEqual(sg.access_level("POST", "/api/commands/analyze/005930"), "owner")

    def test_public_write_rate_limit(self):
        from fastapi import FastAPI as _F
        app = _F()
        app.middleware("http")(api_token_gate)

        @app.post("/api/commands/analyze/{code}")
        def analyze(code: str):
            return {"ok": code}
        c = TestClient(app, base_url="https://testserver")
        sg._HITS.clear()
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret", "API_PUBLIC_WRITE_LIMIT_PER_MIN": "3"}):
            codes = [c.post("/api/commands/analyze/005930", headers=TUNNEL).status_code for _ in range(5)]
            self.assertEqual(codes, [200, 200, 200, 429, 429])
        sg._HITS.clear()

    def test_strict_mode_makes_every_api_get_owner_only(self):
        with mock.patch.dict(os.environ, {"API_GATE_MODE": "strict"}):
            self.assertEqual(sg.access_level("GET", "/api/market-regime"), "owner")
            self.assertEqual(sg.access_level("GET", "/assets/x.js"), "public")

    def test_local_calls_unaffected(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.post("/api/thing").status_code, 200)
            self.assertEqual(c.get("/api/portfolio").status_code, 200)

    def test_public_reads_need_nothing(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            for path in ("/api/market-regime",):
                self.assertEqual(c.get(path, headers=TUNNEL).status_code, 200, path)

    def test_owner_actions_need_admin_token(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            c = _client()
            self.assertEqual(c.get("/api/dart-excel/download/1", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "wrong"}).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "X-API-Token": "s3cret"}).status_code, 200)
            self.assertEqual(c.get("/api/dart-excel/download/1", headers={**TUNNEL, "Authorization": "Bearer s3cret"}).status_code, 200)

    def test_account_pages_need_server_verified_password_cookie(self):
        env = {"API_WRITE_TOKEN": "s3cret", "ADMIN_PASSWORD": "pw-1234"}
        with mock.patch.dict(os.environ, env):
            c = _client()
            r = c.get("/api/portfolio", headers=TUNNEL)
            self.assertEqual((r.status_code, r.json()["detail"]), (401, "invest_unlock_required"))
            self.assertEqual(c.get("/api/buy-candidates", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.get("/api/realtime/prices", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.post("/api/admin-auth/invest-unlock", json={"password": "nope"}, headers=TUNNEL).status_code, 401)
            ok = c.post("/api/admin-auth/invest-unlock", json={"password": "pw-1234"}, headers=TUNNEL)
            self.assertEqual(ok.status_code, 200)
            self.assertIn("sd_invest=", ok.headers["set-cookie"])
            self.assertIn("HttpOnly", ok.headers["set-cookie"])
            # the client keeps the cookie: viewer pages open, but owner actions stay closed
            self.assertEqual(c.get("/api/portfolio", headers=TUNNEL).status_code, 200)
            self.assertEqual(c.get("/api/buy-candidates", headers=TUNNEL).status_code, 200)
            self.assertEqual(c.get("/api/realtime/prices", headers=TUNNEL).status_code, 200)
            self.assertEqual(c.get("/api/admin-auth/invest-status", headers=TUNNEL).json(), {"unlocked": True})
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 401)
            self.assertEqual(c.get("/api/dart-excel/download/1", headers=TUNNEL).status_code, 401)

    def test_view_cookie_forgery_and_expiry(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            good = sg.make_view_cookie()
            self.assertTrue(sg.valid_view_cookie(good))
            exp, sig = good.split(".")
            self.assertFalse(sg.valid_view_cookie(f"{int(exp) + 999}.{sig}"))                 # extended expiry
            self.assertFalse(sg.valid_view_cookie(f"{exp}.{'0' * len(sig)}"))
            self.assertFalse(sg.valid_view_cookie(""))
            self.assertFalse(sg.valid_view_cookie(sg.make_view_cookie(now=time.time() - 13 * 3600)))   # expired
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "other"}):
            self.assertFalse(sg.valid_view_cookie(good))                                       # signed with a different secret

    def test_admin_token_opens_account_pages_without_password(self):
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret"}):
            self.assertEqual(_client().get("/api/portfolio", headers={**TUNNEL, "X-API-Token": "s3cret"}).status_code, 200)

    def test_login_brute_force_is_limited(self):
        import routes.portfolio_access as pa
        pa._ATTEMPTS.clear()
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret", "PORTFOLIO_VIEW_PASSWORD": "pw"}):
            c = _client()
            codes = [c.post("/api/portfolio-access/login", json={"password": "x"}, headers=TUNNEL).status_code for _ in range(10)]
            self.assertEqual(codes[:8], [401] * 8)
            self.assertEqual(codes[8:], [429, 429])
        pa._ATTEMPTS.clear()

    def test_rate_limit_for_bulk_scraping(self):
        sg._HITS.clear()
        with mock.patch.dict(os.environ, {"API_WRITE_TOKEN": "s3cret", "API_RATE_LIMIT_PER_MIN": "5"}):
            c = _client()
            codes = [c.get("/api/market-regime", headers=TUNNEL).status_code for _ in range(7)]
            self.assertEqual(codes, [200] * 5 + [429, 429])
            self.assertEqual(c.get("/api/market-regime").status_code, 200)                     # local calls are not limited
        sg._HITS.clear()

    def test_fail_closed_without_any_auth_configured(self):
        env = {k: v for k, v in os.environ.items() if k not in ("API_WRITE_TOKEN", "PORTFOLIO_VIEW_PASSWORD", "VIEW_COOKIE_SECRET", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "ADMIN_COOKIE_SECRET")}
        with mock.patch.dict(os.environ, env, clear=True):
            c = _client()
            self.assertEqual(c.post("/api/thing", headers=TUNNEL).status_code, 503)
            self.assertEqual(c.get("/api/portfolio", headers=TUNNEL).status_code, 503)
            self.assertEqual(c.post("/api/thing").status_code, 200)                             # local still fine
            self.assertEqual(c.get("/api/market-regime", headers=TUNNEL).status_code, 200)     # public reads unaffected

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
        # 익명 앱 토큰(Bypass 정책에서 무인증 방문자에게 발급됨): 서명·aud·iss·exp가 모두 유효해도 이메일이 없으면 거부
        anon = make_jwt(type="app", sub="")
        anon_payload = json.loads(base64.urlsafe_b64decode(anon.split(".")[1] + "=="))
        anon_payload.pop("email", None)
        head = anon.split(".")[0]
        body = _b64(json.dumps(anon_payload).encode())
        sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        self.assertIsNone(sg.verify_access_jwt(f"{head}.{body}.{_b64(sig)}", TEAM, AUD, keys=JWKS))
        self.assertIsNone(sg.verify_access_jwt(make_jwt(email=""), TEAM, AUD, keys=JWKS))
        self.assertIsNone(sg.verify_access_jwt(make_jwt(type="app"), TEAM, AUD, keys=JWKS))

    def test_access_login_of_owner_email_replaces_token(self):
        env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD, "CF_ACCESS_ALLOWED_EMAILS": "owner@example.com", "API_WRITE_TOKEN": "s3cret"}
        with mock.patch.dict(os.environ, env), mock.patch.object(sg, "_fetch_jwks", return_value=JWKS):
            c = _client()
            jwt = make_jwt()
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": jwt}).status_code, 200)
            self.assertEqual(c.get("/api/dart-excel/download/9", headers=TUNNEL, cookies={"CF_Authorization": jwt}).status_code, 200)
            self.assertEqual(c.get("/api/portfolio", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": jwt}).status_code, 200)
            # a friend who also passes Access is NOT an owner
            friend = make_jwt(email="friend@example.com")
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": friend}).status_code, 401)
            self.assertEqual(c.get("/api/dart-excel/download/9", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": friend}).status_code, 401)
            # invalid signature falls back to the token requirement
            bad = make_jwt(key=rsa.generate_private_key(65537, 2048))
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": bad}).status_code, 401)
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": bad, "X-API-Token": "s3cret"}).status_code, 200)

    def test_access_without_owner_list_grants_nothing(self):
        env = {"CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD}
        with mock.patch.dict(os.environ, env), mock.patch.object(sg, "_fetch_jwks", return_value=JWKS):
            os.environ.pop("CF_ACCESS_ALLOWED_EMAILS", None)
            os.environ.pop("API_WRITE_TOKEN", None)
            os.environ.pop("ADMIN_PASSWORD", None)
            os.environ.pop("ADMIN_PASSWORD_HASH", None)
            os.environ.pop("ADMIN_COOKIE_SECRET", None)
            c = _client()
            self.assertEqual(c.post("/api/thing", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": make_jwt()}).status_code, 503)


if __name__ == "__main__":
    unittest.main()
