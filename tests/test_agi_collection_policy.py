"""AGI-03 수집 정책 계층 테스트 — robots.txt 준수 · 도메인별 예의 지연 · 전체 시간 상한.

범위: **외부 접속 0건**. 통합 테스트는 `127.0.0.1` 의 일회용 `http.server` 만 쓰고, 그 서버가
`/robots.txt` 를 직접 제공한다(404/503/드롭/크기초과 포함). 공인 주소 검사는 resolver 주입으로만
검증하며, 실제 DNS/외부 HTTP 호출은 일어나지 않는다.

고정하는 계약
-------------
* robots: 4xx = 제약 없음(허용) / 5xx·429·드롭·크기초과 = **차단(fail-closed)** / 금지 경로 = 차단
* robots 그룹은 **가장 구체적인 하나만** 적용(RFC 9309) — 우리 UA 그룹이 있으면 `*` 규칙은 가산되지 않음
* 경로 매칭: 최장 일치 우선, 동률이면 Allow, `*` 와 끝 `$` 지원, 빈 `Disallow:` = 제약 없음
* 도메인별 지연: 호스트 단위, `max(min_delay, Crawl-delay)`, `max_domain_delay_s` 상한,
  robots 요청도 같은 간격을 지킨다
* `total_timeout_s` 는 **실제로 강제**된다: robots 조회·지연 대기·리다이렉트 홉·본문 수신이 한 예산을
  나눠 쓰고, 예산을 넘길 대기는 **자지 않고** 타임아웃으로 끝난다(요청을 보내지 않는다)

실행:
    ./venv/bin/python -m pytest tests/test_agi_collection_policy.py -v
"""

from __future__ import annotations

import http.server
import threading
import time
import unittest
from collections import Counter
from urllib.parse import urlsplit

from agi.adapters.blog import FetchResult, ListingEntry, ListingPage, PostDetail, BlogAdapter
from agi.adapters.blog_fetch import FetchPolicy, HttpBlogFetcher
from agi.adapters.collection_policy import (
    ON_UNAVAILABLE_ALLOW,
    ON_UNAVAILABLE_DENY,
    ROBOTS_ABSENT,
    ROBOTS_OK,
    ROBOTS_UNAVAILABLE,
    Deadline,
    DomainRateLimiter,
    RobotsDocument,
    RobotsGate,
    parse_robots,
)
from agi.adapters.errors import (
    CODE_ROBOTS_DISALLOWED,
    CODE_ROBOTS_UNAVAILABLE,
    CODE_TIMEOUT,
    FetchPolicyError,
)
from agi.repository import INTERNAL_VISIBILITY, ResearchRepository, make_document_id
from tests.agi_test_support import isolated_schema

TOKEN = "stock-dashboard-agi-blog-test"
OTHER_TOKEN = "unrelated-bot"

# 그룹 2개: `*` 와 우리 토큰. **우리 토큰 그룹이 선택되면 `*` 규칙은 적용되지 않는다**(RFC 9309).
ROBOTS_BODY = """\
# 픽스처 robots.txt  (주석/빈 줄 포함)
User-agent: *
Disallow: /star-blocked
Disallow: /wild/*
Allow: /wild/ok
Crawl-delay: 0.5

User-agent: stock-dashboard-agi-blog-test
Disallow: /token-blocked
Disallow: /deep/*
Allow: /deep/allowed
Crawl-delay: 0.5
"""


class _FakeClock:
    """가짜 시계 — 실제로 자지 않고 '얼마나 기다렸는지'를 기록한다."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


# --------------------------------------------------------------------------- 픽스처 서버
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:  # 테스트 출력 오염 방지
        pass

    def _body(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 규약
        path = urlsplit(self.path).path
        self.server.hits[path] += 1  # 서버가 실제로 받은 요청만 센다
        try:
            if path == "/robots.txt":
                status = self.server.robots_status
                if status == 0:  # 응답 없이 끊는다(전송 오류)
                    self.close_connection = True
                    return
                body = (self.server.robots_body or "").encode("utf-8") if status == 200 else b"robots error"
                self._body(status, body, "text/plain; charset=utf-8")
            elif path == "/slow":
                time.sleep(3.0)
                self._body(200, b"<html><body>late</body></html>")
            elif path == "/redirect-slow":
                self._body(302, b"", extra={"Location": "/slow"})
            elif path == "/big":
                self._body(200, b"x" * (16 * 1024))
            else:
                self._body(200, b"<html><body>page ok</body></html>")
        except (BrokenPipeError, ConnectionResetError):
            pass


class _RobotsServer:
    """`/robots.txt` 를 마음대로 바꿀 수 있는 일회용 loopback 서버."""

    def __init__(self, *, robots_body: str = ROBOTS_BODY, robots_status: int = 200) -> None:
        self.robots_body = robots_body
        self.robots_status = robots_status
        self.server = None
        self.thread = None
        self.port = 0

    def __enter__(self) -> "_RobotsServer":
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.robots_body = self.robots_body
        self.server.robots_status = self.robots_status
        self.server.hits = Counter()
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    @property
    def hits(self) -> Counter:
        return self.server.hits

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def policy(self, **overrides) -> FetchPolicy:
        base = dict(
            allowed_hosts=(f"127.0.0.1:{self.port}",),
            allow_http=True,
            allow_private_addresses=True,
            follow_redirects=False,
            timeout_s=1.5,
            max_bytes=4096,
            robots_user_agent_token=TOKEN,
            min_domain_delay_s=0.0,
        )
        base.update(overrides)
        return FetchPolicy(**base)

    def fetcher(self, **overrides) -> HttpBlogFetcher:
        fetcher_kwargs = {}
        for key in ("clock", "sleep"):
            if key in overrides:
                fetcher_kwargs[key] = overrides.pop(key)
        return HttpBlogFetcher(self.policy(**overrides), url_for=lambda item: item, **fetcher_kwargs)


def _expect_code(test: unittest.TestCase, code: str, fn) -> FetchPolicyError:
    with test.assertRaises(FetchPolicyError) as ctx:
        fn()
    test.assertEqual(ctx.exception.code, code, f"기대 {code}, 실제 {ctx.exception.code}: {ctx.exception}")
    return ctx.exception


def _doc(state: str, text: str = "", status: int | None = None, reason: str = "") -> RobotsDocument:
    return RobotsDocument(state=state, text=text, status=status, reason=reason)


class _Target:
    """게이트/리미터 단위테스트용 최소 타겟(`decision()` 이 읽는 필드만)."""

    def __init__(self, path: str = "/", host: str = "blog.example.com", port: int = 443, scheme: str = "https") -> None:
        self.scheme = scheme
        self.host = host
        self.port = port
        self.path_with_query = path
        self.url = f"{scheme}://{host}{path}"


# --------------------------------------------------------------------------- robots 파싱(순수)
class RobotsParserTests(unittest.TestCase):
    def test_absent_source_means_no_rules_and_everything_allowed(self) -> None:
        rules = parse_robots(None)
        self.assertEqual(rules.groups, ())
        self.assertIsNone(rules.group_for(TOKEN))
        self.assertTrue(rules.allows("/anything", product_token=TOKEN))

    def test_empty_disallow_means_no_restriction(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow:\n")
        self.assertTrue(rules.allows("/x", product_token=TOKEN), "빈 Disallow 는 제약이 아니다")

    def test_longest_match_wins_and_allow_wins_ties(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /wild/*\nAllow: /wild/ok\nDisallow: /deep\nAllow: /deep\n")
        self.assertFalse(rules.allows("/wild/x", product_token=TOKEN))
        self.assertTrue(rules.allows("/wild/ok", product_token=TOKEN), "더 긴 Allow 가 이겨야 한다")
        self.assertTrue(rules.allows("/deep", product_token=TOKEN), "동률이면 Allow 우선")
        self.assertTrue(rules.allows("/wild", product_token=TOKEN), "규칙 자체가 안 맞으면 허용")

    def test_wildcard_and_dollar_anchor(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /tmp/*/secret\n")
        self.assertFalse(rules.allows("/a/b.pdf", product_token=TOKEN))
        self.assertTrue(rules.allows("/a/b.pdf?x=1", product_token=TOKEN), "$ 앵커는 쿼리까지 포함한 끝을 본다")
        self.assertFalse(rules.allows("/tmp/1/secret", product_token=TOKEN))
        self.assertFalse(
            rules.allows("/tmp/1/secret/x", product_token=TOKEN),
            "robots 경로 규칙은 **접두** 일치다 — 하위 경로도 막힌다",
        )
        self.assertTrue(rules.allows("/tmp/secret", product_token=TOKEN), "`*` 자리에 세그먼트가 없으면 불일치")

    def test_comments_blank_lines_and_case_are_ignored(self) -> None:
        rules = parse_robots("# c\n\n  USER-AGENT:  *  \n  DISALLOW:  /x  # tail\n")
        self.assertFalse(rules.allows("/x", product_token=TOKEN))

    def test_consecutive_user_agent_lines_share_one_group(self) -> None:
        rules = parse_robots("User-agent: a-bot\nUser-agent: b-bot\nDisallow: /no\n")
        self.assertEqual(len(rules.groups), 1)
        self.assertFalse(rules.allows("/no", product_token="a-bot-v2"))
        self.assertFalse(rules.allows("/no", product_token="b-bot-v2"))

    def test_new_rules_start_a_new_group(self) -> None:
        rules = parse_robots("User-agent: a-bot\nDisallow: /no\nUser-agent: b-bot\nDisallow: /no2\n")
        self.assertEqual(len(rules.groups), 2)
        self.assertTrue(rules.allows("/other", product_token="a-bot-v2"), "a-bot 그룹에 b-bot 규칙이 섞였다")
        self.assertFalse(rules.allows("/no2", product_token="b-bot-v9"))

    def test_specific_group_replaces_the_star_group(self) -> None:
        """RFC 9309 §2.2.1: 적용되는 그룹은 **하나** — 우리 그룹이 있으면 `*` 는 가산되지 않는다."""
        rules = parse_robots(ROBOTS_BODY)
        self.assertFalse(rules.allows("/token-blocked", product_token=TOKEN))
        self.assertTrue(rules.allows("/star-blocked", product_token=TOKEN), "우리 그룹이 있으면 * 규칙은 적용되지 않는다")
        self.assertFalse(rules.allows("/star-blocked", product_token=OTHER_TOKEN), "* 그룹만 맞는 UA 에는 적용된다")
        self.assertTrue(rules.allows("/token-blocked", product_token=OTHER_TOKEN))

    def test_longest_agent_token_wins(self) -> None:
        rules = parse_robots("User-agent: stock\nDisallow: /a\nUser-agent: stock-dashboard\nDisallow: /b\n")
        self.assertFalse(rules.allows("/b", product_token="stock-dashboard-agi-blog"))
        self.assertTrue(rules.allows("/a", product_token="stock-dashboard-agi-blog"), "더 구체적인 그룹만 적용")

    def test_unknown_agent_without_star_group_is_unconstrained(self) -> None:
        rules = parse_robots("User-agent: someone-else\nDisallow: /\n")
        self.assertTrue(rules.allows("/x", product_token=TOKEN))

    def test_crawl_delay_is_parsed_and_invalid_values_are_ignored(self) -> None:
        self.assertEqual(parse_robots("User-agent: *\nCrawl-delay: 2.5\n").crawl_delay_s(product_token=TOKEN), 2.5)
        self.assertIsNone(parse_robots("User-agent: *\nCrawl-delay: soon\n").crawl_delay_s(product_token=TOKEN))
        self.assertIsNone(parse_robots("User-agent: *\nCrawl-delay: -1\n").crawl_delay_s(product_token=TOKEN))
        self.assertEqual(parse_robots("User-agent: *\ncrawl-delay: 3\n").crawl_delay_s(product_token=TOKEN), 3.0)

    def test_directives_before_any_user_agent_are_ignored(self) -> None:
        rules = parse_robots("Disallow: /x\nUser-agent: *\nDisallow: /y\n")
        self.assertTrue(rules.allows("/x", product_token=TOKEN), "그룹 없는 지시자가 규칙으로 등록됐다")
        self.assertFalse(rules.allows("/y", product_token=TOKEN))


# --------------------------------------------------------------------------- 게이트(네트워크 없음)
class RobotsGateTests(unittest.TestCase):
    def _gate(self, document: RobotsDocument, **kwargs) -> RobotsGate:
        calls = {"n": 0}

        def fetch(target, deadline=None):
            calls["n"] += 1
            return document

        gate = RobotsGate(
            fetch,
            product_token=kwargs.pop("product_token", TOKEN),
            ttl_s=kwargs.pop("ttl_s", 3600.0),
            clock=kwargs.pop("clock", None) or time.monotonic,
            **kwargs,
        )
        gate.fetch_calls = calls  # 테스트가 재조회 여부를 본다
        return gate

    def test_absent_document_allows(self) -> None:
        decision = self._gate(_doc(ROBOTS_ABSENT, status=404)).decision(_Target("/x"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, ROBOTS_ABSENT)
        self.assertEqual(decision.crawl_delay_s, 0.0)

    def test_unavailable_document_denies_by_default(self) -> None:
        gate = self._gate(_doc(ROBOTS_UNAVAILABLE, status=503, reason="http_503"))
        decision = gate.decision(_Target("/x"))
        self.assertFalse(decision.allowed, "robots 를 못 받으면 fail-closed 로 차단해야 한다")
        self.assertIn("fail-closed", decision.detail)

    def test_explicit_allow_flag_is_the_only_way_to_crawl_a_site_without_robots(self) -> None:
        gate = self._gate(
            _doc(ROBOTS_UNAVAILABLE, reason="transport"),
            on_unavailable=ON_UNAVAILABLE_ALLOW,
            max_domain_delay_s=2.0,
        )
        decision = gate.decision(_Target("/x"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.crawl_delay_s, 2.0, "판정 불가 상태에서는 최대 간격을 쓴다")

    def test_path_is_evaluated_for_ok_documents(self) -> None:
        gate = self._gate(_doc(ROBOTS_OK, text=ROBOTS_BODY, status=200))
        self.assertFalse(gate.decision(_Target("/token-blocked")).allowed)
        self.assertTrue(gate.decision(_Target("/deep/allowed")).allowed)
        self.assertEqual(gate.decision(_Target("/deep/allowed")).crawl_delay_s, 0.5)

    def test_cache_prevents_refetching_robots(self) -> None:
        gate = self._gate(_doc(ROBOTS_OK, text=ROBOTS_BODY, status=200))
        for path in ("/a", "/b", "/c"):
            gate.decision(_Target(path))
        self.assertEqual(gate.fetch_calls["n"], 1, "robots.txt 를 매 요청마다 다시 받았다")
        self.assertEqual(gate.fetch_count, 1)

    def test_ttl_zero_refetches(self) -> None:
        gate = self._gate(_doc(ROBOTS_OK, text=ROBOTS_BODY, status=200), ttl_s=0.0)
        gate.decision(_Target("/a"))
        gate.decision(_Target("/b"))
        self.assertEqual(gate.fetch_calls["n"], 2)

    def test_ttl_is_per_host(self) -> None:
        docs = {"a.test": _doc(ROBOTS_ABSENT, status=404), "b.test": _doc(ROBOTS_ABSENT, status=404)}

        def fetch(target, deadline=None):
            return docs[target.host]

        gate = RobotsGate(fetch, product_token=TOKEN, ttl_s=3600.0)
        gate.decision(_Target("/x", host="a.test"))
        gate.decision(_Target("/x", host="b.test"))
        gate.decision(_Target("/y", host="a.test"))
        self.assertEqual(gate.fetch_count, 2, "호스트별 캐시가 공유됐거나 재조회가 과했다")

    def test_crawl_delay_is_capped(self) -> None:
        gate = self._gate(
            _doc(ROBOTS_OK, text="User-agent: *\nCrawl-delay: 9999\n", status=200), max_domain_delay_s=5.0
        )
        decision = gate.decision(_Target("/x"))
        self.assertEqual(decision.crawl_delay_s, 5.0)
        self.assertIn("상한", decision.detail)

    def test_invalid_on_unavailable_is_refused_loudly(self) -> None:
        with self.assertRaises(ValueError):
            RobotsGate(lambda t, deadline=None: None, product_token=TOKEN, on_unavailable="whatever")


# --------------------------------------------------------------------------- 예의 지연
class DomainRateLimiterTests(unittest.TestCase):
    def _limiter(self, **kwargs):
        clock = kwargs.pop("clock")
        return DomainRateLimiter(clock=clock.now, sleep=clock.sleep, **kwargs)

    def test_first_request_does_not_wait(self) -> None:
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=1.0)
        self.assertEqual(limiter.wait("a.test"), 0.0)
        self.assertEqual(clock.sleeps, [])

    def test_second_request_to_same_host_waits_the_interval(self) -> None:
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=1.0)
        limiter.wait("a.test")
        self.assertEqual(limiter.wait("a.test"), 1.0)
        self.assertEqual(clock.sleeps, [1.0])

    def test_another_host_does_not_wait(self) -> None:
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=5.0)
        limiter.wait("a.test")
        self.assertEqual(limiter.wait("b.test"), 0.0)
        self.assertEqual(clock.sleeps, [])

    def test_larger_requested_interval_wins_over_the_configured_minimum(self) -> None:
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=0.5)
        self.assertEqual(limiter.wait("a.test"), 0.0)
        self.assertEqual(limiter.wait("a.test", interval_s=2.0), 0.5, "직전 간격으로 대기")
        self.assertEqual(limiter.wait("a.test", interval_s=2.0), 2.0, "Crawl-delay 2.0 이 최소 0.5 를 이긴다")
        self.assertEqual(limiter.effective_interval(2.0), 2.0)

    def test_interval_is_capped(self) -> None:
        """간격은 **다음 슬롯 예약**에 쓰인다 — 대기 시간은 직전 간격으로 결정된다."""
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=1.0, max_interval_s=3.0)
        self.assertEqual(limiter.wait("a.test"), 0.0)
        self.assertEqual(limiter.effective_interval(9999), 3.0, "상한이 적용되지 않았다")
        self.assertEqual(limiter.wait("a.test", interval_s=9999), 1.0, "직전 간격(1.0)으로 대기")
        self.assertEqual(limiter.wait("a.test", interval_s=9999), 3.0, "상한 3.0 을 넘겨 기다리지 않는다")

    def test_wait_that_would_exceed_the_budget_does_not_sleep(self) -> None:
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=10.0)
        self.assertEqual(limiter.wait("a.test"), 0.0)
        deadline = Deadline(0.5, clock=clock.now)
        with self.assertRaises(FetchPolicyError) as ctx:
            limiter.wait("a.test", deadline=deadline)
        self.assertEqual(ctx.exception.code, CODE_TIMEOUT)
        self.assertEqual(clock.sleeps, [], "예산을 넘길 대기를 실제로 자버렸다")

    def test_reservation_makes_concurrent_callers_serialize(self) -> None:
        """자리 예약을 먼저 하므로 두 번째 호출이 '0초'를 받지 않는다(동시 호출 안전)."""
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=2.0)
        self.assertEqual(limiter.reserve("a.test"), 0.0)
        self.assertEqual(limiter.reserve("a.test"), 2.0)
        self.assertEqual(limiter.reserve("a.test"), 4.0)

    def test_defer_pushes_the_next_slot_back_but_never_pulls_it_forward(self) -> None:
        """robots 에서 알게 된 Crawl-delay 를 **이미 잡힌 슬롯 뒤로** 반영한다."""
        clock = _FakeClock()
        limiter = self._limiter(clock=clock, min_interval_s=0.0)
        limiter.reserve("a.test")  # 슬롯 없음(대기 0)
        self.assertEqual(limiter.defer("a.test", 3.0), 3.0)
        limiter.reserve("a.test")
        self.assertEqual(limiter.defer("a.test", 0.5), 3.0, "앞당기면 안 된다(남은 대기가 그대로여야 한다)")


class DeadlineTests(unittest.TestCase):
    def test_remaining_counts_down_and_check_raises_with_phase(self) -> None:
        clock = _FakeClock()
        deadline = Deadline(2.0, clock=clock.now)
        self.assertAlmostEqual(deadline.remaining(), 2.0)
        clock.sleep(1.5)
        self.assertAlmostEqual(deadline.remaining(), 0.5)
        self.assertFalse(deadline.expired())
        clock.sleep(0.6)
        self.assertTrue(deadline.expired())
        with self.assertRaises(FetchPolicyError) as ctx:
            deadline.check(url="https://x/y", phase="본문 수신")
        self.assertEqual(ctx.exception.code, CODE_TIMEOUT)
        self.assertIn("본문 수신", str(ctx.exception))
        self.assertEqual(ctx.exception.url, "https://x/y")

    def test_clamp_timeout_never_exceeds_the_budget(self) -> None:
        clock = _FakeClock()
        deadline = Deadline(1.0, clock=clock.now)
        self.assertAlmostEqual(deadline.clamp_timeout(10.0), 1.0)
        self.assertAlmostEqual(deadline.clamp_timeout(0.25), 0.25)
        clock.sleep(1.0)
        self.assertGreater(deadline.clamp_timeout(10.0), 0.0, "0 이면 소켓이 즉시 죽는다 — 최소값을 준다")

    def test_zero_budget_is_immediately_expired(self) -> None:
        self.assertTrue(Deadline(0.0).expired())


# --------------------------------------------------------------------------- 로컬 HTTP 통합
class RobotsOverHttpTests(unittest.TestCase):
    def test_production_defaults_are_the_narrow_ones(self) -> None:
        policy = FetchPolicy(allowed_hosts=("blog.example.com",))
        self.assertTrue(policy.respect_robots)
        self.assertEqual(policy.robots_on_unavailable, ON_UNAVAILABLE_DENY)
        self.assertEqual(policy.robots_user_agent_token, "stock-dashboard-agi-blog")
        self.assertGreater(policy.min_domain_delay_s, 0.0, "운영 기본 예의 지연은 0 이면 안 된다")
        self.assertGreater(policy.max_domain_delay_s, 0.0)
        self.assertGreater(policy.robots_max_bytes, 0)
        self.assertFalse(policy.allow_http)
        self.assertFalse(policy.allow_private_addresses)

    def test_disallowed_path_is_blocked_without_sending_the_request(self) -> None:
        with _RobotsServer() as server:
            fetcher = server.fetcher()
            exc = _expect_code(self, CODE_ROBOTS_DISALLOWED, lambda: fetcher.fetch_url(server.url("/token-blocked")))
            self.assertEqual(exc.url, server.url("/token-blocked"))
            self.assertEqual(server.hits["/token-blocked"], 0, "robots 가 금지한 경로로 요청을 실제로 보냈다")

    def test_allowed_path_under_a_disallowed_prefix_is_fetched(self) -> None:
        """`Allow` 가 `Disallow` 보다 구체적이면 그 경로는 가져와야 한다(양성 대조)."""
        with _RobotsServer() as server:
            result = server.fetcher().fetch_url(server.url("/deep/allowed"))
            self.assertEqual(result.http_status, 200)
            self.assertIn("page ok", result.detail.body_text)
            self.assertEqual(server.hits["/deep/allowed"], 1)

    def test_star_group_rules_apply_when_our_token_has_no_group(self) -> None:
        with _RobotsServer() as server:
            fetcher = server.fetcher(robots_user_agent_token=OTHER_TOKEN)
            _expect_code(self, CODE_ROBOTS_DISALLOWED, lambda: fetcher.fetch_url(server.url("/star-blocked")))
            self.assertEqual(server.hits["/star-blocked"], 0)

    def test_missing_robots_allows_and_is_not_an_error(self) -> None:
        with _RobotsServer(robots_status=404) as server:
            result = server.fetcher().fetch_url(server.url("/token-blocked"))
            self.assertEqual(result.http_status, 200)
            self.assertEqual(server.hits["/robots.txt"], 1)

    def test_robots_5xx_is_fail_closed(self) -> None:
        with _RobotsServer(robots_status=503) as server:
            fetcher = server.fetcher()
            exc = _expect_code(self, CODE_ROBOTS_UNAVAILABLE, lambda: fetcher.fetch_url(server.url("/ok")))
            self.assertIn("503", str(exc))
            self.assertEqual(server.hits["/ok"], 0, "robots 를 못 받았는데 본문을 가져갔다")

    def test_robots_429_is_fail_closed_too(self) -> None:
        with _RobotsServer(robots_status=429) as server:
            _expect_code(self, CODE_ROBOTS_UNAVAILABLE, lambda: server.fetcher().fetch_url(server.url("/ok")))

    def test_dropped_robots_connection_is_fail_closed(self) -> None:
        with _RobotsServer(robots_status=0) as server:
            fetcher = server.fetcher()
            _expect_code(self, CODE_ROBOTS_UNAVAILABLE, lambda: fetcher.fetch_url(server.url("/ok")))
            self.assertEqual(server.hits["/ok"], 0)

    def test_oversized_robots_is_fail_closed(self) -> None:
        with _RobotsServer(robots_body="User-agent: *\n" + ("#" * 5000) + "\nDisallow: /\n") as server:
            fetcher = server.fetcher(robots_max_bytes=512)
            _expect_code(self, CODE_ROBOTS_UNAVAILABLE, lambda: fetcher.fetch_url(server.url("/ok")))

    def test_robots_is_fetched_once_per_host_and_cached(self) -> None:
        with _RobotsServer() as server:
            fetcher = server.fetcher()
            for path in ("/deep/allowed", "/deep/allowed", "/a", "/b"):
                fetcher.fetch_url(server.url(path))
            self.assertEqual(server.hits["/robots.txt"], 1, "robots.txt 를 요청마다 다시 받았다")

    def test_robots_request_and_page_request_respect_the_same_delay(self) -> None:
        """robots 요청도 같은 간격 규칙을 지킨다 — 지연은 호스트 단위다."""
        with _RobotsServer() as server:
            clock = _FakeClock()
            fetcher = server.fetcher(clock=clock.now, sleep=clock.sleep)
            fetcher.fetch_url(server.url("/deep/allowed"))
            self.assertEqual([round(s, 6) for s in clock.sleeps], [0.5], "Crawl-delay 0.5 가 페이지 요청에 적용되지 않았다")

    def test_configured_min_delay_applies_even_without_crawl_delay(self) -> None:
        with _RobotsServer(robots_status=404) as server:
            clock = _FakeClock()
            fetcher = server.fetcher(clock=clock.now, sleep=clock.sleep, min_domain_delay_s=0.4)
            fetcher.fetch_url(server.url("/ok"))
            self.assertEqual([round(s, 6) for s in clock.sleeps], [0.4])

    def test_respect_robots_false_skips_the_lookup_entirely(self) -> None:
        """명시적 탈출구 — 켜면 robots 를 아예 요청하지 않는다(운영 기본값은 True)."""
        with _RobotsServer() as server:
            fetcher = server.fetcher(respect_robots=False)
            result = fetcher.fetch_url(server.url("/token-blocked"))
            self.assertEqual(result.http_status, 200)
            self.assertEqual(server.hits["/robots.txt"], 0)

    def test_budget_that_cannot_cover_the_politeness_wait_sends_nothing(self) -> None:
        with _RobotsServer(robots_status=404) as server:
            clock = _FakeClock()
            fetcher = server.fetcher(
                clock=clock.now, sleep=clock.sleep, min_domain_delay_s=1.0, total_timeout_s=5.0
            )
            fetcher.fetch_url(server.url("/ok"))  # 첫 호출은 예산 안에서 진행(로봇+페이지 = 1.0s 대기)
            sleeps_before = list(clock.sleeps)
            self.assertEqual([round(s, 6) for s in sleeps_before], [1.0])

            budget = Deadline(0.5, clock=clock.now)
            with self.assertRaises(FetchPolicyError) as ctx:
                fetcher.fetch_url(server.url("/second"), deadline=budget)
            self.assertEqual(ctx.exception.code, CODE_TIMEOUT)
            self.assertEqual(ctx.exception.reason(), "policy_blocked:timeout")
            self.assertEqual(server.hits["/second"], 0, "예의 지연을 어기고 요청을 보냈다")
            self.assertEqual(clock.sleeps, sleeps_before, "예산을 넘길 대기를 실제로 자버렸다")


class TotalBudgetOverHttpTests(unittest.TestCase):
    def test_total_budget_bounds_a_slow_body_read(self) -> None:
        """소켓 타임아웃(5s)보다 전체 예산(0.5s)이 짧으면 예산이 이긴다."""
        with _RobotsServer() as server:
            fetcher = server.fetcher(timeout_s=5.0, total_timeout_s=0.5)
            started = time.monotonic()
            _expect_code(self, CODE_TIMEOUT, lambda: fetcher.fetch_url(server.url("/slow")))
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.0, f"전체 예산이 강제되지 않았다 (elapsed={elapsed:.2f}s)")

    def test_total_budget_covers_redirect_hops(self) -> None:
        with _RobotsServer() as server:
            fetcher = server.fetcher(follow_redirects=True, timeout_s=5.0, total_timeout_s=1.2)
            started = time.monotonic()
            _expect_code(self, CODE_TIMEOUT, lambda: fetcher.fetch_url(server.url("/redirect-slow")))
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertEqual(server.hits["/slow"], 1, "리다이렉트 홉은 나가고, 본문 수신이 예산에서 잘려야 한다")

    def test_body_read_check_inside_the_budget_still_succeeds(self) -> None:
        with _RobotsServer() as server:
            result = server.fetcher(total_timeout_s=10.0).fetch_url(server.url("/ok"))
            self.assertEqual(result.http_status, 200)

    def test_request_timeout_code_is_the_same_as_a_policy_decision(self) -> None:
        """예산 초과도 `timeout` 코드 하나로 모인다(어댑터가 정책 차단으로 기록)."""
        with _RobotsServer() as server:
            exc = _expect_code(
                self, CODE_TIMEOUT, lambda: server.fetcher(total_timeout_s=0.01).fetch_url(server.url("/slow"))
            )
            self.assertEqual(exc.reason(), "policy_blocked:timeout")


# --------------------------------------------------------------------------- 어댑터 결합
class AdapterRobotsIntegrationTests(unittest.TestCase):
    def test_robots_blocked_url_becomes_unavailable_without_revision(self) -> None:
        """정책 차단은 URL 단위 결정 — 문서는 `unavailable` 로 남고 실행은 계속된다."""
        with _RobotsServer() as server, isolated_schema() as (conn, schema):
            adapter = BlogAdapter(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = adapter.ensure_source(conn, blog_key="robots-fixture")
            conn.commit()

            urls = {"blocked": server.url("/token-blocked"), "open": server.url("/deep/allowed")}
            fetcher = HttpBlogFetcher(server.policy(), url_for=lambda external_id: urls[external_id])
            listing = ListingPage(
                entries=(
                    ListingEntry(external_id="blocked", url=urls["blocked"], published_at="2026-10-09 08:30:00"),
                    ListingEntry(external_id="open", url=urls["open"], published_at="2026-10-09 08:35:00"),
                ),
                complete=True,
            )
            result = adapter.ingest_page(conn, source_id, listing, fetcher)
            conn.commit()

            self.assertEqual(result["stored_count"], 1)
            self.assertEqual(result["skipped_unavailable"], 1)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["policy_blocked_counts"], {CODE_ROBOTS_DISALLOWED: 1})
            self.assertIn(f"policy_blocked:{CODE_ROBOTS_DISALLOWED}", result["limitations"])

            blocked_id = make_document_id(source_id, "blocked")
            blocked = repo.get_document(blocked_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(blocked["access_state"], "unavailable")
            self.assertEqual(blocked["tombstone_reason"], f"policy_blocked:{CODE_ROBOTS_DISALLOWED}")
            self.assertEqual(len(repo.list_revisions(blocked_id)), 0, "가져오지 않은 글에 revision 이 생겼다")

            open_id = make_document_id(source_id, "open")
            self.assertEqual(repo.get_document(open_id, visibility=INTERNAL_VISIBILITY)["access_state"], "ok")
            self.assertEqual(len(repo.list_revisions(open_id)), 1)
            self.assertEqual(server.hits["/token-blocked"], 0)

    def test_robots_unavailable_document_is_also_policy_blocked(self) -> None:
        with _RobotsServer(robots_status=503) as server, isolated_schema() as (conn, schema):
            adapter = BlogAdapter(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = adapter.ensure_source(conn, blog_key="robots-503-fixture")
            conn.commit()
            fetcher = HttpBlogFetcher(
                server.policy(), url_for=lambda external_id: server.url(f"/{external_id}")
            )
            listing = ListingPage(
                entries=(ListingEntry(external_id="ok", url=server.url("/ok"), published_at="2026-10-09 08:30:00"),),
                complete=True,
            )
            result = adapter.ingest_page(conn, source_id, listing, fetcher)
            conn.commit()
            self.assertEqual(result["policy_blocked_counts"], {CODE_ROBOTS_UNAVAILABLE: 1})
            doc = repo.get_document(make_document_id(source_id, "ok"), visibility=INTERNAL_VISIBILITY)
            self.assertEqual(doc["access_state"], "unavailable")
            self.assertEqual(doc["tombstone_reason"], f"policy_blocked:{CODE_ROBOTS_UNAVAILABLE}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
