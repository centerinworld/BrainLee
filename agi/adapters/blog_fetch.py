"""AGI-03 블로그 HTTP fetcher — **실행은 하지 않는다**(구현·테스트만).

무엇을 하는가
-------------
`agi.adapters.blog` 의 `fetch(external_id) -> FetchResult` 계약을 실제 HTTP 로 구현한다.
어댑터는 이 객체를 주입받기만 하므로 수집 파이프라인과 네트워크가 분리된다.

보안 설계 (전부 fail-closed)
---------------------------
1. **스킴 allowlist** — 기본은 `https` 만. `http` 는 테스트 픽스처 전용 플래그로만 열린다.
2. **호스트 allowlist(정확 일치)** — 접미사 매칭을 쓰지 않는다. `example.com` 을 허용해도
   `evil-example.com`·`example.com.evil.net` 은 통과하지 못한다. 포트까지 비교한다
   (항목에 포트가 없으면 스킴 기본 포트만 허용) → `https://host:9999` 같은 우회 차단.
3. **userinfo 금지** — `https://user@host/` 는 호스트 판정을 흐리므로 거부한다.
4. **주소 등급 검사 + IP 고정(pinning)** — 호스트를 해석해 **모든** 주소가 공인 주소인지
   확인하고, 실제 연결은 **검증한 IP 로 직접** 한다(호스트 이름으로 다시 해석하지 않는다).
   이렇게 해야 "검사 시점엔 공인 IP, 연결 시점엔 127.0.0.1" 인 DNS 리바인딩이 성립하지
   않는다. TLS 는 원래 호스트명으로 SNI/검증한다(`server_hostname`).
5. **리다이렉트 차단** — 기본은 따라가지 않는다(3xx 는 정책 차단). 따라가는 설정에서도
   `Location` 을 **같은 정책으로 다시 검증**한 뒤에만 이동한다.
6. **크기 제한** — `Content-Length` 사전 검사 + 스트리밍 중 초과 시 즉시 중단.
7. **Content-Type 제한** — HTML/XHTML/plain 만. 그 외는 본문을 읽지 않는다.
8. **타임아웃** — 소켓 타임아웃 + 전체 경과 상한.

수집 정책 계층 (`collection_policy`, fetcher 와 분리)
----------------------------------------------------
9. **robots.txt 준수** — 우리 UA 그룹 기준으로 경로를 판정한다. 4xx(없음)는 제약 없음,
   5xx·429·타임아웃은 **차단**(fail-closed), 금지 경로는 `robots_disallowed`. 그룹은 가장
   구체적인 **하나**만 적용하고(RFC 9309), `*`·`$` 와 최장 일치/Allow 우선 규칙을 따른다.
10. **도메인별 예의 지연** — 호스트 단위 최소 간격(`min_domain_delay_s`)과 robots
    `Crawl-delay` 중 큰 값을 쓰되 `max_domain_delay_s` 로 상한한다. robots 요청도 같은
    간격을 지킨다(robots.txt 자체를 연타하지 않는다).
11. **전체 예산(`total_timeout_s`)은 조회 1건에 실제로 강제된다** — robots 조회·지연 대기·
    리다이렉트 홉·본문 수신이 같은 예산을 나눠 쓰고, 소켓 타임아웃도 남은 예산으로 잘린다.
    예산을 넘길 지연은 **자지 않고** 타임아웃으로 끝난다(예의를 어기고 보내지 않는다).

정직한 잔여 한계
----------------
* DNS 해석(`resolve`)은 OS 리졸버에 위임하므로 자체 타임아웃이 없다 — 해석이 매달리면
  예산은 해석 이후 첫 검사에서 소진된 것으로 판정된다(해석 중 강제 중단은 불가).
* robots.txt 의 비표준 확장(Sitemap·Request-rate 등)은 해석하지 않고, 경로 규칙·Crawl-delay
  만 소비한다. robots 를 `200 text/html` 로 주는 사이트도 텍스트로 읽는다(관대한 쪽).
* 리다이렉트를 따라가는 설정에서 홉마다 지연이 적용되므로, 예산이 작으면 홉 대기에서
  타임아웃으로 끝날 수 있다(그것이 정책 위반보다 낫다).
* 인증이 필요한 페이지(로그인 벽)는 다루지 않는다 — `restricted` 로 남는 것이 정상이다.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import ssl
import time as _time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from agi.adapters.blog import FetchResult, PostDetail
from agi.adapters.collection_policy import (
    ON_UNAVAILABLE_DENY,
    ROBOTS_ABSENT,
    ROBOTS_OK,
    ROBOTS_UNAVAILABLE,
    Deadline,
    DomainRateLimiter,
    RobotsDecision,
    RobotsDocument,
    RobotsGate,
)
from agi.adapters.errors import (
    CODE_ADDRESS_NOT_ALLOWED,
    CODE_CONTENT_TYPE_NOT_ALLOWED,
    CODE_HOST_NOT_ALLOWED,
    CODE_PORT_NOT_ALLOWED,
    CODE_REDIRECT_BLOCKED,
    CODE_REDIRECT_TARGET_NOT_ALLOWED,
    CODE_RESOLVE_FAILED,
    CODE_RESPONSE_TOO_LARGE,
    CODE_ROBOTS_DISALLOWED,
    CODE_ROBOTS_UNAVAILABLE,
    CODE_SCHEME_NOT_ALLOWED,
    CODE_TIMEOUT,
    CODE_TRANSPORT_ERROR,
    CODE_UNEXPECTED_STATUS,
    CODE_URL_MALFORMED,
    CODE_URL_TOO_LONG,
    CODE_USERINFO_NOT_ALLOWED,
    FetchPolicyError,
)

HTTPS_PORT = 443
HTTP_PORT = 80

# 어댑터(`agi.adapters.blog.classify_fetch_status`)가 해석할 수 있는 상태만 그대로 돌려준다.
# 그 밖의 상태는 **추측하지 않고** 정책 오류로 멈춘다 — 모르는 상태를 200 이나 404 로 뭉개면
# 조용한 오분류가 되고, 그대로 넘기면 어댑터가 ValueError 로 죽는다.
PASS_THROUGH_STATUSES = (401, 403, 404, 410, 429, 500, 501, 502, 503, 504)
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

DEFAULT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)


@dataclass(frozen=True)
class FetchPolicy:
    """HTTP 가져오기 정책. 기본값은 **운영 기준(가장 좁은 허용)** 이다.

    `allow_http` 와 `allow_private_addresses` 는 **로컬 HTTP 픽스처 통합테스트 전용**이다.
    운영 코드에서 True 로 두면 스킴·주소 등급 방어가 사라지므로, 기본값을 바꾸지 않는다.
    """

    allowed_hosts: tuple[str, ...]
    allow_http: bool = False
    allow_private_addresses: bool = False
    follow_redirects: bool = False
    max_redirects: int = 3
    timeout_s: float = 10.0
    total_timeout_s: float = 20.0
    max_bytes: int = 2_000_000
    allowed_content_types: tuple[str, ...] = DEFAULT_CONTENT_TYPES
    max_url_length: int = 2048
    user_agent: str = "stock-dashboard-agi-blog/1 (research; contact owner)"
    accept_language: str = "ko-KR,ko;q=0.9,en;q=0.8"
    # --- 수집 정책 (robots · 예의 지연 · robots 크기) ---
    respect_robots: bool = True
    robots_user_agent_token: str = "stock-dashboard-agi-blog"
    robots_cache_ttl_s: float = 3600.0
    robots_max_bytes: int = 512_000
    robots_on_unavailable: str = ON_UNAVAILABLE_DENY
    min_domain_delay_s: float = 1.0
    max_domain_delay_s: float = 30.0

    @property
    def allowed_schemes(self) -> tuple[str, ...]:
        return ("https", "http") if self.allow_http else ("https",)


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    scheme: str
    host: str  # idna-encoded, lowercase
    port: int
    path_with_query: str
    addresses: tuple[str, ...]


# --------------------------------------------------------------------- 크기/HTML 파싱
class _HtmlExtractor(HTMLParser):
    """최소 HTML 추출기 — 제목·본문 텍스트·미디어 참조.

    완전한 렌더러가 아니다(스크립트/스타일 제외, 블록 경계에서 개행). 목적은 **원문 텍스트와
    미디어 참조를 잃지 않고 보관**하는 것이며, 정밀 파싱/OCR 은 AGI-04 범위다.
    """

    _SKIP_TAGS = ("script", "style", "noscript", "template", "svg")
    _BLOCK_TAGS = (
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "section", "article", "figure", "figcaption",
    )
    _MEDIA_ATTRS = {
        "img": ("src", "data-src"),
        "source": ("src",),
        "video": ("src", "poster"),
        "audio": ("src",),
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.media: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_body = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "body":
            self._in_body = True
        attributes = {key.lower(): (value or "") for key, value in attrs}
        for attr in self._MEDIA_ATTRS.get(tag, ()):
            value = attributes.get(attr, "").strip()
            if value:
                self.media.append(value)
        if tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag == "body":
            self._in_body = False
        if tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._in_body or not self._in_title:
            self.text_parts.append(data)


def extract_post_detail(
    body: bytes,
    *,
    charset: str = "utf-8",
    base_url: str | None = None,
    content_type: str = "text/html",
) -> PostDetail:
    """응답 본문 → `PostDetail`(본문 텍스트 + 절대 URL 로 정규화한 미디어 참조)."""
    text = body.decode(charset, errors="replace")
    media: list[str] = []
    title_parts: list[str] = []
    if "html" in content_type or content_type.endswith("xhtml+xml"):
        parser = _HtmlExtractor()
        parser.feed(text)
        parser.close()
        # 인라인 태그로 쪼개진 조각을 공백으로 잇고, 블록 경계의 빈 줄은 보존한다.
        joined = "".join(parser.text_parts)
        lines = [" ".join(line.split()) for line in joined.split("\n")]
        body_text = "\n".join(line for line in lines if line).strip()
        title_parts = parser.title_parts
        media = parser.media
    else:
        body_text = text.strip()

    refs: list[str] = []
    for raw in media:
        absolute = urljoin(base_url, raw) if base_url else raw
        scheme = urlsplit(absolute).scheme.lower()
        if scheme in ("http", "https"):
            refs.append(absolute)
    title = " ".join(" ".join(title_parts).split()) or None
    return PostDetail(body_text=body_text, media_refs=tuple(refs), title=title)


# --------------------------------------------------------------------- 고정 연결
class _PinnedHTTPConnection(http.client.HTTPConnection):
    """호스트 이름 대신 **검증된 IP** 로 연결한다(DNS 리바인딩 차단)."""

    def __init__(self, host: str, port: int, *, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        # 호스트 이름이 아니라 **검증된 IP** 로 붙는다 → 검사-연결 사이의 DNS 재해석이 없다.
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """검증된 IP 로 연결하되 TLS 는 **원래 호스트명** 으로 SNI/인증서 검증한다."""

    def __init__(self, host: str, port: int, *, ip: str, timeout: float, context) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip
        # `http.client` 의 비공개 속성에 기대지 않도록 넘겨받은 컨텍스트를 직접 보관한다.
        self._tls_context = context

    def connect(self) -> None:
        # 검증된 IP 로 붙되 TLS 는 원래 호스트명으로 SNI·인증서 검증한다(SNI 를 IP 로 보내면
        # 인증서 검증이 무의미해진다).
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._tls_context.wrap_socket(sock, server_hostname=self.host)


# --------------------------------------------------------------------- fetcher
class HttpBlogFetcher:
    """`FetchPolicy` 를 강제하는 HTTP fetcher. `url_for` 로 external_id → URL 을 만든다."""

    def __init__(
        self,
        policy: FetchPolicy,
        *,
        url_for: Callable[[str], str],
        resolver: Callable[[str, int], list[str]] | None = None,
        connect=None,
        ssl_context=None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        rate_limiter: DomainRateLimiter | None = None,
        robots_gate: RobotsGate | None = None,
    ) -> None:
        self.policy = policy
        self.url_for = url_for
        self._resolver = resolver or _default_resolver
        self._connect = connect  # 테스트 주입(실제 소켓 없이 정책 경로만 검증)
        self._ssl_context = ssl_context
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        # 같은 수집 실행 안에서 지연 상태를 공유하려면 두 객체를 **밖에서** 주입한다.
        self.rate_limiter = rate_limiter or DomainRateLimiter(
            min_interval_s=policy.min_domain_delay_s,
            max_interval_s=policy.max_domain_delay_s,
            clock=self._clock,
            sleep=self._sleep,
        )
        self.robots_gate = robots_gate
        if self.robots_gate is None and policy.respect_robots:
            self.robots_gate = RobotsGate(
                self.fetch_robots_document,
                product_token=policy.robots_user_agent_token,
                ttl_s=policy.robots_cache_ttl_s,
                on_unavailable=policy.robots_on_unavailable,
                max_domain_delay_s=policy.max_domain_delay_s,
                clock=self._clock,
            )

    # ------------------------------------------------------------- 공개 API
    def __call__(self, external_id: str) -> FetchResult:
        return self.fetch_url(self.url_for(external_id))

    def fetch_url(self, url: str, *, deadline: Deadline | None = None) -> FetchResult:
        """정책을 통과한 URL 만 가져온다. 정책 위반은 `FetchPolicyError`(값이 아니라 결정).

        `deadline` 을 주면 그 예산을 쓰고, 없으면 `policy.total_timeout_s` 로 새로 만든다 —
        robots 조회·지연 대기·리다이렉트·본문 수신이 **한 예산**을 나눠 쓴다.
        """
        budget = deadline if deadline is not None else Deadline(self.policy.total_timeout_s, clock=self._clock)
        target = self.resolve(url)
        budget.check(url=url, phase="resolve 이후")
        return self._fetch_target(target, redirects_left=self.policy.max_redirects, deadline=budget)

    # ------------------------------------------------------------- URL 검증
    def resolve(self, url: str) -> ResolvedTarget:
        """URL 을 정책에 비추어 검증하고 **연결할 IP 까지 확정**한다(네트워크 I/O 는 DNS 만)."""
        policy = self.policy
        if not isinstance(url, str) or not url:
            raise FetchPolicyError(CODE_URL_MALFORMED, "빈 URL", url=str(url))
        if len(url) > policy.max_url_length:
            raise FetchPolicyError(CODE_URL_TOO_LONG, f"{len(url)}자", url=url[:200])
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
            raise FetchPolicyError(CODE_URL_MALFORMED, "제어문자 포함", url=url[:200])

        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in policy.allowed_schemes:
            raise FetchPolicyError(
                CODE_SCHEME_NOT_ALLOWED, f"스킴 {scheme!r} 미허용", url=url
            )
        if parts.username or parts.password:
            raise FetchPolicyError(CODE_USERINFO_NOT_ALLOWED, "userinfo 포함 URL", url=url)

        raw_host = (parts.hostname or "").strip()
        if not raw_host:
            raise FetchPolicyError(CODE_URL_MALFORMED, "호스트 없음", url=url)
        try:
            host = raw_host.rstrip(".").lower().encode("idna").decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise FetchPolicyError(CODE_URL_MALFORMED, f"호스트 인코딩 실패: {exc}", url=url) from None

        default_port = HTTPS_PORT if scheme == "https" else HTTP_PORT
        try:
            port = parts.port or default_port
        except ValueError:
            raise FetchPolicyError(CODE_PORT_NOT_ALLOWED, "포트 파싱 실패", url=url) from None

        if not self._host_allowed(host):
            raise FetchPolicyError(
                CODE_HOST_NOT_ALLOWED, f"허용목록에 없음: {host}:{port}", url=url
            )
        if not self._port_allowed(host, port, scheme):
            raise FetchPolicyError(
                CODE_PORT_NOT_ALLOWED,
                f"포트 미허용: {host}:{port} (스킴 {scheme} 기본 포트 또는 명시 포트만)",
                url=url,
            )

        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        addresses = tuple(self._resolver(host, port))
        if not addresses:
            raise FetchPolicyError(CODE_RESOLVE_FAILED, f"주소 없음: {host}", url=url)
        for address in addresses:
            self._check_address(address, url)
        # 비기본 포트는 canonical URL 에 **반드시 남긴다** — 빼면 상대 경로(base_url)와
        # 리다이렉트 Location 이 기본 포트로 재해석돼 엉뚱한 대상(80/443)을 가리킨다.
        netloc = host if port == default_port else f"{host}:{port}"
        return ResolvedTarget(
            url=urlunsplit((scheme, netloc, path, "", "")),
            scheme=scheme,
            host=host,
            port=port,
            path_with_query=path,
            addresses=addresses,
        )

    def _host_allowed(self, host: str) -> bool:
        """호스트 **정확 일치** 판정 — 접미사 매칭을 하지 않는다(서브도메인·유사도메인 우회 차단)."""
        for entry in self.policy.allowed_hosts:
            entry_host = str(entry).partition(":")[0].strip().rstrip(".").lower()
            if entry_host == host:
                return True
        return False

    def _port_allowed(self, host: str, port: int, scheme: str) -> bool:
        """포트 판정 — 항목에 포트가 있으면 그 포트만, 없으면 스킴 기본 포트만 허용한다."""
        default_port = HTTPS_PORT if scheme == "https" else HTTP_PORT
        for entry in self.policy.allowed_hosts:
            entry_host, _, entry_port = str(entry).partition(":")
            if entry_host.strip().rstrip(".").lower() != host:
                continue
            if entry_port:
                if int(entry_port) == port:
                    return True
            elif port == default_port:
                return True
        return False

    def _check_address(self, address: str, url: str) -> None:
        """주소 등급 검사 — 사설·루프백·링크로컬·멀티캐스트·예약·미지정을 거부한다."""
        if self.policy.allow_private_addresses:
            return
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise FetchPolicyError(CODE_ADDRESS_NOT_ALLOWED, f"주소 해석 실패: {address}", url=url) from None
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise FetchPolicyError(
                CODE_ADDRESS_NOT_ALLOWED, f"공인 주소가 아님: {address}", url=url
            )
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) 로 등급 검사를 우회하지 못하게 한다.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            raise FetchPolicyError(
                CODE_ADDRESS_NOT_ALLOWED, f"IPv4-mapped IPv6 거부: {address}", url=url
            )

    # ------------------------------------------------------------- 수집 정책
    def _headers(self, target: ResolvedTarget, *, accept: str | None = None) -> dict[str, str]:
        return {
            "Host": target.host,
            "User-Agent": self.policy.user_agent,
            "Accept": accept or (", ".join(self.policy.allowed_content_types) + ", */*;q=0.1"),
            "Accept-Language": self.policy.accept_language,
            "Connection": "close",
        }

    def check_robots(self, target: ResolvedTarget, *, deadline: Deadline | None = None) -> RobotsDecision:
        """robots 판정. `respect_robots=False` 면 조회 자체를 하지 않는다(명시적 탈출구)."""
        if not self.policy.respect_robots or self.robots_gate is None:
            return RobotsDecision(allowed=True, crawl_delay_s=0.0, source="disabled", detail="robots 미적용(명시 설정)")
        return self.robots_gate.decision(target, deadline=deadline)

    def fetch_robots_document(self, target: ResolvedTarget, *, deadline: Deadline | None = None) -> RobotsDocument:
        """`/robots.txt` 를 **같은 정책·같은 지연 규칙·같은 예산**으로 조회한다.

        실패는 예외로 올리지 않고 상태(`absent`/`unavailable`)로 돌려준다 — 최종 판정은
        게이트가 fail-closed 로 내린다. 예외는 **예산 소진(`timeout`)** 하나뿐이다: 그것은
        사이트에 대한 판정이 아니라 우리 호출 자체의 실패이므로 조용히 뭉개지 않는다.
        """
        parts = urlsplit(target.url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            robots_target = self.resolve(robots_url)
            if deadline is not None:
                deadline.check(url=robots_url, phase="robots")
            # robots.txt 자체도 같은 간격 규칙을 지킨다(연타 금지).
            self.rate_limiter.wait(robots_target.host, interval_s=0.0, deadline=deadline, url=robots_url)
            connection = self._open(robots_target, deadline=deadline)
            try:
                connection.request(
                    "GET", "/robots.txt", headers=self._headers(robots_target, accept="text/plain,*/*;q=0.1")
                )
                response = connection.getresponse()
                status = int(response.status)
                if status == 200:
                    body, too_large = self._read_body(
                        response, self.policy.robots_max_bytes, deadline=deadline, url=robots_url
                    )
                    if too_large:
                        return RobotsDocument(ROBOTS_UNAVAILABLE, status=200, reason="robots_too_large")
                    return RobotsDocument(ROBOTS_OK, text=body.decode("utf-8", errors="replace"), status=200)
                # 4xx(429 제외) = robots 없음 → 제약 없음(RFC 9309 §2.3.1.3)
                if 400 <= status < 500 and status != 429:
                    return RobotsDocument(ROBOTS_ABSENT, status=status, reason=f"http_{status}")
                # 5xx·429 = "unavailable" → 게이트가 fail-closed 로 차단한다(RFC §2.3.1.4)
                return RobotsDocument(ROBOTS_UNAVAILABLE, status=status, reason=f"http_{status}")
            finally:
                with contextlib.suppress(Exception):
                    connection.close()
        except FetchPolicyError as exc:
            if exc.code == CODE_TIMEOUT:
                raise
            return RobotsDocument(ROBOTS_UNAVAILABLE, reason=exc.code)
        except (OSError, http.client.HTTPException) as exc:
            return RobotsDocument(ROBOTS_UNAVAILABLE, reason=f"{CODE_TRANSPORT_ERROR}: {exc}")

    # ------------------------------------------------------------- 요청
    def _fetch_target(
        self, target: ResolvedTarget, *, redirects_left: int, deadline: Deadline | None = None
    ) -> FetchResult:
        if deadline is not None:
            deadline.check(url=target.url, phase="요청 전")

        decision = self.check_robots(target, deadline=deadline)
        if not decision.allowed:
            # "규칙이 금지" 와 "robots 를 못 받아 차단" 은 운영자가 다르게 대응해야 하는 사건이다
            # (전자는 사이트 정책, 후자는 우리 쪽/상대 인프라 문제) → 사유 코드를 분리한다.
            code = CODE_ROBOTS_UNAVAILABLE if decision.source == ROBOTS_UNAVAILABLE else CODE_ROBOTS_DISALLOWED
            raise FetchPolicyError(
                code,
                f"robots 판정 차단: {target.path_with_query} ({decision.detail})",
                url=target.url,
            )
        # 방금 알게 된 Crawl-delay 는 **다음 슬롯**에 반영된다. robots 요청이 그 지식을 알기 전에
        # 잡아 둔 슬롯을 뒤로 미뤄야 "선언된 지연을 무시한 첫 요청" 이 생기지 않는다.
        self.rate_limiter.defer(target.host, decision.crawl_delay_s)
        self.rate_limiter.wait(
            target.host, interval_s=decision.crawl_delay_s, deadline=deadline, url=target.url
        )
        if deadline is not None:
            deadline.check(url=target.url, phase="지연 대기 후")

        connection = self._open(target, deadline=deadline)
        try:
            headers = self._headers(target)
            try:
                connection.request("GET", target.path_with_query, headers=headers)
                response = connection.getresponse()
            except socket.timeout:
                raise FetchPolicyError(CODE_TIMEOUT, f"{self.policy.timeout_s}s 초과", url=target.url) from None
            except (OSError, http.client.HTTPException) as exc:
                raise FetchPolicyError(CODE_TRANSPORT_ERROR, str(exc), url=target.url) from None

            # 본문 수신도 **남은 예산**으로 잘라 기다린다(홉마다 타임아웃을 새로 주지 않는다).
            if deadline is not None and getattr(connection, "sock", None) is not None:
                with contextlib.suppress(OSError, AttributeError):
                    connection.sock.settimeout(deadline.clamp_timeout(self.policy.timeout_s))

            status = int(response.status)
            if status in REDIRECT_STATUSES:
                return self._handle_redirect(
                    target, response, redirects_left=redirects_left, deadline=deadline
                )
            if status != 200:
                if status in PASS_THROUGH_STATUSES:
                    # 상태 코드는 서버가 준 사실이다 — 정책 오류로 바꾸지 않는다.
                    return FetchResult(http_status=status)
                raise FetchPolicyError(
                    CODE_UNEXPECTED_STATUS,
                    f"해석할 수 없는 상태 {status}",
                    url=target.url,
                    status=status,
                )
            return self._read_success(target, response, deadline=deadline)
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _handle_redirect(self, target, response, *, redirects_left: int, deadline: Deadline | None = None) -> FetchResult:
        location = response.getheader("Location") or ""
        if not self.policy.follow_redirects or redirects_left <= 0:
            raise FetchPolicyError(
                CODE_REDIRECT_BLOCKED,
                f"{response.status} → {location or '(Location 없음)'} (리다이렉트 미추적)",
                url=target.url,
                status=int(response.status),
            )
        next_url = urljoin(target.url, location)
        try:
            next_target = self.resolve(next_url)
        except FetchPolicyError as exc:
            if exc.code == CODE_HOST_NOT_ALLOWED:
                raise FetchPolicyError(
                    CODE_REDIRECT_TARGET_NOT_ALLOWED, exc.args[0], url=next_url
                ) from None
            raise
        return self._fetch_target(next_target, redirects_left=redirects_left - 1, deadline=deadline)

    def _read_success(self, target: ResolvedTarget, response, *, deadline: Deadline | None = None) -> FetchResult:
        policy = self.policy
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if not any(content_type == allowed for allowed in policy.allowed_content_types):
            raise FetchPolicyError(
                CODE_CONTENT_TYPE_NOT_ALLOWED, f"Content-Type {content_type!r}", url=target.url
            )
        charset = "utf-8"
        raw_header = response.getheader("Content-Type") or ""
        for chunk in raw_header.split(";")[1:]:
            key, _, value = chunk.partition("=")
            if key.strip().lower() == "charset" and value.strip():
                charset = value.strip().strip('"\'') or "utf-8"

        declared = response.getheader("Content-Length")
        if declared and declared.isdigit() and int(declared) > policy.max_bytes:
            raise FetchPolicyError(
                CODE_RESPONSE_TOO_LARGE,
                f"Content-Length {declared} > {policy.max_bytes}",
                url=target.url,
            )

        body, too_large = self._read_body(response, policy.max_bytes, deadline=deadline, url=target.url)
        if too_large:
            raise FetchPolicyError(
                CODE_RESPONSE_TOO_LARGE, f"{policy.max_bytes}바이트 초과", url=target.url
            )
        return FetchResult(
            http_status=200,
            detail=extract_post_detail(
                body, charset=charset, base_url=target.url, content_type=content_type
            ),
        )

    def _read_body(
        self, response, limit: int, *, deadline: Deadline | None = None, url: str | None = None
    ) -> tuple[bytes, bool]:
        """본문을 `limit` 까지만 읽는다. `(본문, 초과여부)` — 초과는 조용히 잘라내지 않는다."""
        chunks: list[bytes] = []
        total = 0
        while True:
            if deadline is not None:
                deadline.check(url=url, phase="본문 수신")
            remaining = limit - total
            try:
                chunk = response.read(min(65536, remaining + 1))
            except socket.timeout:
                raise FetchPolicyError(CODE_TIMEOUT, "본문 수신 타임아웃", url=url) from None
            except (OSError, http.client.HTTPException) as exc:
                raise FetchPolicyError(CODE_TRANSPORT_ERROR, str(exc), url=url) from None
            if not chunk:
                return b"".join(chunks), False
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                return b"".join(chunks), True

    def _open(self, target: ResolvedTarget, *, deadline: Deadline | None = None):
        if self._connect is not None:  # 테스트 주입
            return self._connect(target)
        timeout = self.policy.timeout_s if deadline is None else deadline.clamp_timeout(self.policy.timeout_s)
        address = target.addresses[0]
        if target.scheme == "https":
            context = self._ssl_context or ssl.create_default_context()
            return _PinnedHTTPSConnection(
                target.host, target.port, ip=address, timeout=timeout, context=context
            )
        return _PinnedHTTPConnection(
            target.host, target.port, ip=address, timeout=timeout
        )


def _default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({str(info[4][0]) for info in infos})
