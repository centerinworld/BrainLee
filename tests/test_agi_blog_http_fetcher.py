"""AGI-03 블로그 HTTP fetcher 테스트 — 정책 경계 + 로컬 HTTP 픽스처 통합.

범위: **실제 블로그에 접속하지 않는다**. 통합 테스트는 `127.0.0.1` 의 일회용
`http.server` 만 쓰고, 그 서버는 프로세스가 끝나면 사라진다(수집·소스 등록 0).

고정하는 계약
-------------
* 스킴 allowlist(기본 https 만)·호스트 정확 일치·포트 일치·userinfo 금지
* 주소 등급 검사(사설/루프백/링크로컬/멀티캐스트/예약) + **IP 고정** 연결
* 리다이렉트 미추적(기본)·추적 시 대상 재검증
* 크기 제한(사전 Content-Length + 스트리밍 중단)·Content-Type 제한·타임아웃
* 정책 차단은 `FetchPolicyError`(값이 아니라 결정) — 어댑터가 `unavailable` 로 기록

실행:
    ./venv/bin/python -m pytest tests/test_agi_blog_http_fetcher.py -v
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
import unittest
from urllib.parse import urlsplit

from agi.adapters.blog import FetchResult, ListingPage, ListingEntry, PostDetail
from agi.adapters.blog import BlogAdapter
from agi.adapters.blog_fetch import (
    DEFAULT_CONTENT_TYPES,
    HttpBlogFetcher,
    FetchPolicy,
    extract_post_detail,
)
from agi.adapters.errors import (
    CODE_ADDRESS_NOT_ALLOWED,
    CODE_CONTENT_TYPE_NOT_ALLOWED,
    CODE_HOST_NOT_ALLOWED,
    CODE_PORT_NOT_ALLOWED,
    CODE_REDIRECT_BLOCKED,
    CODE_REDIRECT_TARGET_NOT_ALLOWED,
    CODE_RESPONSE_TOO_LARGE,
    CODE_SCHEME_NOT_ALLOWED,
    CODE_TIMEOUT,
    CODE_UNEXPECTED_STATUS,
    CODE_URL_MALFORMED,
    CODE_URL_TOO_LONG,
    CODE_USERINFO_NOT_ALLOWED,
    FetchPolicyError,
)
from agi.repository import INTERNAL_VISIBILITY, ResearchRepository, make_document_id
from tests.agi_test_support import isolated_schema

PUBLIC_IP = "93.184.216.34"
PRODUCTION_HOST = "blog.example.com"
API_HOST = "api.example.com"

PAGE_HTML = (
    "<html><head><title>데모 글</title>"
    "<script>var x = 1; // SCRIPT_TEXT_SHOULD_NOT_APPEAR</script>"
    "<style>.a{color:red} /* STYLE_TEXT_SHOULD_NOT_APPEAR */</style></head>"
    "<body><h1>제목</h1><p>본문 첫 문단</p><img src=\"/img/a.png\">"
    "<img src=\"https://cdn.example.net/b.jpg\"><img src=\"javascript:alert(1)\"></body></html>"
).encode("utf-8")


def _policy(**overrides) -> FetchPolicy:
    base = dict(
        allowed_hosts=(PRODUCTION_HOST, API_HOST),
        allow_http=False,
        allow_private_addresses=False,
        follow_redirects=False,
        timeout_s=2.0,
        max_bytes=4096,
        # 픽스처 전용 탈출구: 예의 지연은 **새 정책 테스트**가 실측으로 검증한다
        # (`respect_robots` 는 기본값 True 를 그대로 쓴다 — 404 응답은 "robots 없음 = 허용").
        min_domain_delay_s=0.0,
    )
    base.update(overrides)
    return FetchPolicy(**base)


def _fetcher(policy: FetchPolicy, resolver=None) -> HttpBlogFetcher:
    return HttpBlogFetcher(
        policy,
        url_for=lambda external_id: f"https://{PRODUCTION_HOST}/{external_id}",
        resolver=resolver or (lambda host, port: [PUBLIC_IP]),
    )


def _expect_code(test: unittest.TestCase, code: str, fn) -> FetchPolicyError:
    with test.assertRaises(FetchPolicyError) as ctx:
        fn()
    test.assertEqual(ctx.exception.code, code, f"기대 {code}, 실제 {ctx.exception.code}: {ctx.exception}")
    return ctx.exception


# --------------------------------------------------------------------------- 픽스처 서버
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:  # 테스트 출력 오염 방지
        pass

    def _headers(self, status: int, content_type: str | None, extra=None, length=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _body(self, status=200, body=b"", content_type="text/html; charset=utf-8", extra=None):
        self._headers(status, content_type, extra, length=len(body))
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 규약
        path = urlsplit(self.path).path
        try:
            if path == "/ok":
                self._body(body=PAGE_HTML)
            elif path == "/plain":
                self._body(body="plain body".encode("utf-8"), content_type="text/plain; charset=utf-8")
            elif path == "/forbidden":
                self._body(status=403, body=b"no")
            elif path == "/missing":
                self._body(status=404, body=b"gone")
            elif path == "/gone":
                self._body(status=410, body=b"gone")
            elif path == "/busy":
                self._body(status=503, body=b"busy")
            elif path == "/teapot":
                self._body(status=418, body=b"tea")
            elif path == "/binary":
                self._body(body=b"\x00\x01", content_type="application/octet-stream")
            elif path == "/redirect":
                self._headers(302, None, {"Location": "/ok"}, length=0)
            elif path == "/redirect-external":
                self._headers(302, None, {"Location": "http://evil.example.net/x"}, length=0)
            elif path == "/redirect-loop":
                self._headers(302, None, {"Location": "/redirect"}, length=0)
            elif path == "/big":
                self._body(body=b"x" * (16 * 1024))
            elif path == "/big-stream":
                self._headers(200, "text/html; charset=utf-8", length=None)
                for _ in range(8):
                    self.wfile.write(b"y" * 4096)
                    self.wfile.flush()
            elif path == "/slow":
                time.sleep(3.0)
                self._body(body=b"late")
            else:
                self._body(status=404, body=b"unknown")
        except (BrokenPipeError, ConnectionResetError):
            pass


class _LocalServer:
    def __init__(self) -> None:
        self.server = None
        self.thread = None
        self.port = 0

    def __enter__(self) -> "_LocalServer":
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
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
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def policy(self, **overrides) -> FetchPolicy:
        base = dict(
            allowed_hosts=(f"127.0.0.1:{self.port}",),
            allow_http=True,
            allow_private_addresses=True,
            follow_redirects=False,
            timeout_s=1.5,
            max_bytes=4096,
            # 픽스처 전용: 실제 대기를 0 으로 (지연 자체는 test_agi_collection_policy.py 가 검증)
            min_domain_delay_s=0.0,
        )
        base.update(overrides)
        return FetchPolicy(**base)

    def fetcher(self, **overrides) -> HttpBlogFetcher:
        return HttpBlogFetcher(self.policy(**overrides), url_for=lambda item: item)


# --------------------------------------------------------------------------- 정책(순수)
class UrlPolicyTests(unittest.TestCase):
    def test_defaults_are_the_narrow_production_values(self) -> None:
        policy = FetchPolicy(allowed_hosts=(PRODUCTION_HOST,))
        self.assertEqual(policy.allowed_schemes, ("https",))
        self.assertFalse(policy.allow_http)
        self.assertFalse(policy.allow_private_addresses)
        self.assertFalse(policy.follow_redirects)
        self.assertEqual(policy.max_redirects, 3)
        self.assertGreater(policy.max_bytes, 0)
        self.assertEqual(policy.allowed_content_types, DEFAULT_CONTENT_TYPES)

    def test_http_is_rejected_by_default(self) -> None:
        _expect_code(self, CODE_SCHEME_NOT_ALLOWED, lambda: _fetcher(_policy()).resolve("http://blog.example.com/"))

    def test_non_http_schemes_are_rejected(self) -> None:
        for url in ("file:///etc/passwd", "ftp://blog.example.com/x", "gopher://blog.example.com/x"):
            _expect_code(self, CODE_SCHEME_NOT_ALLOWED, lambda u=url: _fetcher(_policy()).resolve(u))

    def test_host_must_be_exactly_listed(self) -> None:
        for url in (
            "https://evil.example.com/x",
            "https://example.com/x",
            "https://blog.example.com.evil.net/x",
            "https://evil-blog.example.com/x",
            "https://sub.blog.example.com/x",
        ):
            _expect_code(self, CODE_HOST_NOT_ALLOWED, lambda u=url: _fetcher(_policy()).resolve(u))

    def test_allowlisted_host_and_default_port_resolve(self) -> None:
        target = _fetcher(_policy()).resolve("https://blog.example.com/a?b=1")
        self.assertEqual((target.host, target.port, target.scheme), (PRODUCTION_HOST, 443, "https"))
        self.assertEqual(target.path_with_query, "/a?b=1")
        self.assertEqual(target.addresses, (PUBLIC_IP,))

    def test_non_default_port_is_rejected(self) -> None:
        _expect_code(
            self, CODE_PORT_NOT_ALLOWED, lambda: _fetcher(_policy()).resolve("https://blog.example.com:8443/x")
        )

    def test_explicit_port_entry_allows_only_that_port(self) -> None:
        fetcher = _fetcher(_policy(allowed_hosts=("blog.example.com:8443",)))
        self.assertEqual(fetcher.resolve("https://blog.example.com:8443/x").port, 8443)
        _expect_code(self, CODE_PORT_NOT_ALLOWED, lambda: fetcher.resolve("https://blog.example.com/x"))

    def test_userinfo_is_rejected(self) -> None:
        _expect_code(
            self, CODE_USERINFO_NOT_ALLOWED, lambda: _fetcher(_policy()).resolve("https://u:p@blog.example.com/x")
        )

    def test_control_characters_and_empty_url_are_rejected(self) -> None:
        _expect_code(self, CODE_URL_MALFORMED, lambda: _fetcher(_policy()).resolve(""))
        _expect_code(
            self, CODE_URL_MALFORMED, lambda: _fetcher(_policy()).resolve("https://blog.example.com/a\r\nHost: x")
        )

    def test_over_long_url_is_rejected(self) -> None:
        long_url = "https://blog.example.com/" + "a" * 3000
        _expect_code(self, CODE_URL_TOO_LONG, lambda: _fetcher(_policy()).resolve(long_url))

    def test_uppercase_and_trailing_dot_hosts_normalize(self) -> None:
        target = _fetcher(_policy()).resolve("https://BLOG.Example.COM./x")
        self.assertEqual(target.host, PRODUCTION_HOST)

    def test_host_allowlist_has_no_wildcard_semantics(self) -> None:
        """`*.example.com` 같은 문자열을 넣어도 **리터럴 호스트로만** 취급된다(의도치 않은 전체 허용 금지)."""
        fetcher = _fetcher(_policy(allowed_hosts=("*.example.com",)))
        _expect_code(self, CODE_HOST_NOT_ALLOWED, lambda: fetcher.resolve("https://blog.example.com/x"))
        self.assertEqual(fetcher.resolve("https://*.example.com/x").host, "*.example.com")


class AddressPolicyTests(unittest.TestCase):
    """주소 등급 검사 — allowlist 를 통과한 호스트도 사설 주소로 해석되면 거부한다."""

    def _resolve_with(self, address: str):
        fetcher = _fetcher(_policy(), resolver=lambda host, port: [address])
        return fetcher.resolve(f"https://{PRODUCTION_HOST}/x")

    def test_private_and_special_addresses_are_rejected(self) -> None:
        for address in (
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.10",
            "172.16.0.9",
            "169.254.169.254",  # 클라우드 메타데이터
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:127.0.0.1",  # IPv4-mapped 로 등급검사 우회 시도
        ):
            _expect_code(
                self, CODE_ADDRESS_NOT_ALLOWED, lambda a=address: self._resolve_with(a)
            )

    def test_any_private_address_in_the_answer_set_rejects_the_whole_host(self) -> None:
        """여러 주소 중 하나라도 사설이면 거부한다(부분 통과 금지)."""
        fetcher = _fetcher(_policy(), resolver=lambda host, port: [PUBLIC_IP, "127.0.0.1"])
        _expect_code(
            self,
            CODE_ADDRESS_NOT_ALLOWED,
            lambda: fetcher.resolve(f"https://{PRODUCTION_HOST}/x"),
        )

    def test_public_addresses_pass(self) -> None:
        target = self._resolve_with("8.8.8.8")
        self.assertEqual(target.addresses, ("8.8.8.8",))

    def test_missing_dns_answer_is_reported_as_resolve_failed(self) -> None:
        fetcher = _fetcher(_policy(), resolver=lambda host, port: [])
        with self.assertRaises(FetchPolicyError) as ctx:
            fetcher.resolve(f"https://{PRODUCTION_HOST}/x")
        self.assertEqual(ctx.exception.code, "resolve_failed")

    def test_private_flag_exists_but_is_off_by_default(self) -> None:
        """테스트 픽스처용 탈출구는 **명시적 플래그**로만 열린다."""
        fetcher = _fetcher(_policy(allow_private_addresses=True), resolver=lambda h, p: ["127.0.0.1"])
        self.assertEqual(fetcher.resolve(f"https://{PRODUCTION_HOST}/x").addresses, ("127.0.0.1",))


class HtmlExtractionTests(unittest.TestCase):
    def test_script_and_style_text_is_not_stored(self) -> None:
        detail = extract_post_detail(PAGE_HTML, base_url="https://blog.example.com/post")
        self.assertIn("본문 첫 문단", detail.body_text)
        self.assertNotIn("SCRIPT_TEXT_SHOULD_NOT_APPEAR", detail.body_text)
        self.assertNotIn("STYLE_TEXT_SHOULD_NOT_APPEAR", detail.body_text)
        self.assertEqual(detail.title, "데모 글")

    def test_media_refs_are_absolute_and_scheme_filtered(self) -> None:
        detail = extract_post_detail(PAGE_HTML, base_url="https://blog.example.com/post")
        self.assertIn("https://blog.example.com/img/a.png", detail.media_list)
        self.assertIn("https://cdn.example.net/b.jpg", detail.media_list)
        self.assertEqual(len(detail.media_list), 2, f"javascript: 참조가 남았다: {detail.media_list}")

    def test_plain_text_content_is_passed_through(self) -> None:
        detail = extract_post_detail(b"  hello\nworld  ", content_type="text/plain")
        self.assertEqual(detail.body_text, "hello\nworld")


# --------------------------------------------------------------------------- 로컬 HTTP 통합
class LocalHttpFetcherTests(unittest.TestCase):
    def test_200_html_becomes_a_post_detail(self) -> None:
        with _LocalServer() as server:
            result = server.fetcher().fetch_url(server.url("/ok"))
            self.assertEqual(result.http_status, 200)
            self.assertIn("본문 첫 문단", result.detail.body_text)
            self.assertIn(server.url("/img/a.png"), result.detail.media_list)

    def test_error_statuses_pass_through_as_facts(self) -> None:
        with _LocalServer() as server:
            fetcher = server.fetcher()
            for path, expected in (("/forbidden", 403), ("/missing", 404), ("/gone", 410), ("/busy", 503)):
                result = fetcher.fetch_url(server.url(path))
                self.assertIsInstance(result, FetchResult)
                self.assertEqual(result.http_status, expected)
                self.assertIsNone(result.detail, "오류 응답에 본문이 실렸다")

    def test_unmapped_status_is_not_guessed(self) -> None:
        with _LocalServer() as server:
            _expect_code(self, CODE_UNEXPECTED_STATUS, lambda: server.fetcher().fetch_url(server.url("/teapot")))

    def test_redirect_is_blocked_by_default(self) -> None:
        with _LocalServer() as server:
            exc = _expect_code(self, CODE_REDIRECT_BLOCKED, lambda: server.fetcher().fetch_url(server.url("/redirect")))
            self.assertIn("/ok", str(exc))
            # 차단된 URL 임을 식별할 수 있게 원 URL 을 들고 있어야 한다.
            self.assertEqual(exc.url, server.url("/redirect"))

    def test_redirect_is_followed_only_when_enabled_and_target_is_allowlisted(self) -> None:
        with _LocalServer() as server:
            fetcher = server.fetcher(follow_redirects=True)
            result = fetcher.fetch_url(server.url("/redirect"))
            self.assertEqual(result.http_status, 200)
            self.assertIn("본문 첫 문단", result.detail.body_text)

    def test_redirect_hops_are_capped(self) -> None:
        with _LocalServer() as server:
            fetcher = server.fetcher(follow_redirects=True, max_redirects=1)
            _expect_code(self, CODE_REDIRECT_BLOCKED, lambda: fetcher.fetch_url(server.url("/redirect-loop")))

    def test_redirect_target_outside_the_allowlist_is_refused(self) -> None:
        with _LocalServer() as server:
            fetcher = server.fetcher(follow_redirects=True)
            _expect_code(
                self,
                CODE_REDIRECT_TARGET_NOT_ALLOWED,
                lambda: fetcher.fetch_url(server.url("/redirect-external")),
            )

    def test_content_type_outside_the_allowlist_is_refused(self) -> None:
        with _LocalServer() as server:
            _expect_code(
                self,
                CODE_CONTENT_TYPE_NOT_ALLOWED,
                lambda: server.fetcher().fetch_url(server.url("/binary")),
            )

    def test_declared_content_length_over_the_limit_is_refused_before_reading(self) -> None:
        with _LocalServer() as server:
            _expect_code(self, CODE_RESPONSE_TOO_LARGE, lambda: server.fetcher().fetch_url(server.url("/big")))

    def test_streamed_body_is_cut_off_when_it_exceeds_the_limit(self) -> None:
        """Content-Length 를 주지 않는 응답도 스트리밍 중 즉시 중단한다."""
        with _LocalServer() as server:
            _expect_code(
                self, CODE_RESPONSE_TOO_LARGE, lambda: server.fetcher().fetch_url(server.url("/big-stream"))
            )

    def test_timeout_is_reported_as_a_policy_decision(self) -> None:
        with _LocalServer() as server:
            started = time.monotonic()
            _expect_code(self, CODE_TIMEOUT, lambda: server.fetcher().fetch_url(server.url("/slow")))
            self.assertLess(time.monotonic() - started, 3.0, "타임아웃이 적용되지 않았다")

    def test_path_policy_applies_to_direct_urls_too(self) -> None:
        with _LocalServer() as server:
            fetcher = server.fetcher()
            _expect_code(self, CODE_SCHEME_NOT_ALLOWED, lambda: fetcher.fetch_url("ftp://127.0.0.1/ok"))
            _expect_code(self, CODE_HOST_NOT_ALLOWED, lambda: fetcher.fetch_url("http://localhost/ok"))
            # 허용 호스트라도 포트가 다르면 거부된다(여기서는 기본 포트 80).
            _expect_code(
                self, CODE_PORT_NOT_ALLOWED, lambda: fetcher.fetch_url("http://127.0.0.1/ok")
            )

    def test_resolved_url_keeps_a_non_default_port(self) -> None:
        """canonical URL 에 포트가 빠지면 상대경로·리다이렉트가 기본 포트로 오해석된다."""
        with _LocalServer() as server:
            target = server.fetcher().resolve(server.url("/ok"))
            self.assertIn(f":{server.port}/", target.url, target.url)


# --------------------------------------------------------------------------- 어댑터 결합
class AdapterWithHttpFetcherTests(unittest.TestCase):
    def _listing(self, ids):
        return ListingPage(
            entries=tuple(
                ListingEntry(external_id=str(i), url=f"http://blog.test/{i}", published_at="2026-10-09 08:30:00")
                for i in ids
            ),
            complete=True,
        )

    def test_policy_blocked_urls_are_recorded_as_unavailable_and_do_not_stop_the_run(self) -> None:
        """정책 차단은 URL 단위 결정이다 — 한 건의 오설정이 나머지 글 수집을 막지 않는다."""
        with isolated_schema() as (conn, schema):
            adapter = BlogAdapter(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = adapter.ensure_source(conn, blog_key="demo-blog")
            conn.commit()

            def fetch(external_id: str) -> FetchResult:
                if external_id == "2":
                    raise FetchPolicyError(
                        CODE_HOST_NOT_ALLOWED, "허용목록에 없음: blocked.test", url="http://blocked.test/2"
                    )
                return FetchResult(http_status=200, detail=PostDetail(body_text=f"본문 {external_id}"))

            result = adapter.ingest_page(conn, source_id, self._listing([1, 2, 3]), fetch)
            conn.commit()

            self.assertEqual(result["stored_count"], 2)
            self.assertEqual(result["skipped_unavailable"], 1)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["policy_blocked_counts"], {CODE_HOST_NOT_ALLOWED: 1})
            self.assertIn(f"policy_blocked:{CODE_HOST_NOT_ALLOWED}", result["limitations"])

            blocked = repo.get_document(
                make_document_id(source_id, "2"), visibility=INTERNAL_VISIBILITY
            )
            self.assertEqual(blocked["access_state"], "unavailable")
            self.assertEqual(blocked["tombstone_reason"], f"policy_blocked:{CODE_HOST_NOT_ALLOWED}")
            self.assertEqual(
                len(repo.list_revisions(make_document_id(source_id, "2"))),
                0,
                "가져오지 않은 글에 revision 이 생겼다",
            )
            for external_id in ("1", "3"):
                document_id = make_document_id(source_id, external_id)
                self.assertEqual(
                    repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)["access_state"], "ok"
                )
                self.assertEqual(len(repo.list_revisions(document_id)), 1)

    def test_http_fetcher_satisfies_the_adapter_contract_against_a_local_server(self) -> None:
        with _LocalServer() as server, isolated_schema() as (conn, schema):
            adapter = BlogAdapter(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = adapter.ensure_source(conn, blog_key="local-fixture")
            conn.execute(
                f'UPDATE "{schema}".research_sources SET store_original = %s WHERE source_id = %s',
                ("yes", source_id),
            )
            conn.commit()

            # 실제 배선과 같은 모양: external_id → URL 매핑을 url_for 로 준다.
            urls = {
                "ok": server.url("/ok"),
                "missing": server.url("/missing"),
                "redirect": server.url("/redirect"),
            }
            fetcher = HttpBlogFetcher(server.policy(), url_for=lambda external_id: urls[external_id])
            listing = ListingPage(
                entries=(
                    ListingEntry(external_id="ok", url=server.url("/ok"), published_at="2026-10-09 08:30:00"),
                    ListingEntry(external_id="missing", url=server.url("/missing")),
                    ListingEntry(external_id="redirect", url=server.url("/redirect")),
                ),
                complete=True,
            )
            result = adapter.ingest_page(conn, source_id, listing, fetcher)
            conn.commit()

            ok_id = make_document_id(source_id, "ok")
            self.assertEqual(result["stored_count"], 1)
            self.assertEqual(len(repo.list_revisions(ok_id)), 1)
            self.assertIn(
                "본문 첫 문단", repo.list_revisions(ok_id)[0]["body_text"]
            )
            self.assertEqual(
                repo.get_document(ok_id, visibility=INTERNAL_VISIBILITY)["access_state"], "ok"
            )
            self.assertEqual(
                repo.get_document(make_document_id(source_id, "missing"), visibility=INTERNAL_VISIBILITY)[
                    "access_state"
                ],
                "deleted",
            )
            self.assertEqual(
                repo.get_document(make_document_id(source_id, "redirect"), visibility=INTERNAL_VISIBILITY)[
                    "access_state"
                ],
                "unavailable",
            )
            self.assertEqual(result["tombstone_sweep_performed"], True)
            self.assertEqual(result["skipped_deleted"], 1)
            self.assertEqual(result["skipped_unavailable"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
