"""AGI-03 수집 정책 계층 — robots.txt 준수 · 도메인별 예의 지연 · 전체 시간 상한.

`blog_fetch.HttpBlogFetcher` 의 **보안 계층**(스킴·호스트·포트·주소등급·크기)과 분리한다.
fetcher 는 "이 URL 을 가져와도 되는가"를 판정하고, 이 계층은 "이 사이트를 **어떻게** 가져올
것인가"(허용 경로·요청 간격·호출 1건의 전체 예산)를 담당한다. 분리하는 이유는 관심사가
다르기 때문이다 — robots 를 못 받았다고 해서 URL 검증까지 무너지면 안 되고, URL 검증이
통과했다고 해서 사이트가 허용한 경로라는 뜻은 아니다.

robots.txt 판정 (RFC 9309 준수, 실패는 전부 fail-closed)
-------------------------------------------------------
1. **4xx(404·410 등)** → robots 없음 = 제약 없음 → 허용 (RFC 9309 §2.3.1.3).
2. **5xx·429·타임아웃·전송오류·크기초과** → "unavailable" → **전부 차단**
   (RFC 9309 §2.3.1.4 는 unavailable 을 complete disallow 로 처리하도록 규정).
3. **200 + 경로 금지 규칙** → 금지된 경로는 차단(`robots_disallowed`).
4. 그룹 선택은 **가장 구체적인 그룹 하나**만 적용한다(RFC 9309 §2.2.1) — 우리 UA 그룹이
   있으면 `*` 그룹은 **가산되지 않는다**.

경로 매칭은 `*`(임의열)·`$`(끝 앵커)를 지원하고, **가장 긴 일치 규칙**이 이기며 동률이면
`Allow` 가 이긴다. 빈 `Disallow:` 는 제약 없음을 뜻하므로 규칙으로 등록하지 않는다.

도메인별 예의 지연
------------------
지연 키는 **호스트**(포트 무시)다. 유효 간격 = `max(설정 최소간격, robots Crawl-delay)` 를
`max_domain_delay_s` 로 상한한다 — 악의적·오설정 `Crawl-delay: 86400` 하나가 수집을 영구
정지시키지 못하게 하되, 상한을 넘겨 **몰래 빨리 긁지는 않는다**(상한을 넘는 값은 상한값으로
기다린 뒤 진행하고 그 사실을 결정에 남긴다).

전체 시간 상한(`total_timeout_s`)
---------------------------------
호출 1건의 예산이다. robots 조회 · 지연 대기 · 리다이렉트 홉 · 본문 수신이 **같은 예산**을
나눠 쓴다. 예산을 넘길 지연은 **자지 않고** 타임아웃으로 끝낸다 — 예의 지연을 어기면서
요청을 보내는 것보다 보내지 않는 것이 낫고, "예산 초과 = 차단"이 정책 위반을 조용히
삼키지 않는다.
"""

from __future__ import annotations

import re
import threading
import time as _time
from dataclasses import dataclass
from typing import Callable

from agi.adapters.errors import CODE_TIMEOUT, FetchPolicyError

# robots 문서 상태 — "없음(허용)" 과 "못 받음(차단)" 을 절대 같은 값으로 뭉개지 않는다.
ROBOTS_OK = "ok"
ROBOTS_ABSENT = "absent"
ROBOTS_UNAVAILABLE = "unavailable"

ON_UNAVAILABLE_DENY = "deny"
ON_UNAVAILABLE_ALLOW = "allow"


# --------------------------------------------------------------------- 전체 시간 예산
class Deadline:
    """호출 1건의 단조(monotonic) 예산. 네트워크 단계마다 같은 예산을 나눠 쓴다."""

    __slots__ = ("total_s", "_clock", "_expires_at")

    def __init__(self, total_s: float, *, clock: Callable[[], float] | None = None) -> None:
        self.total_s = float(total_s)
        self._clock = clock or _time.monotonic
        self._expires_at = self._clock() + self.total_s

    def remaining(self) -> float:
        return max(0.0, self._expires_at - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def check(self, *, url: str | None = None, phase: str = "") -> None:
        """예산 소진 시 즉시 타임아웃 — 다음 단계로 넘어가기 **전에** 확인한다."""
        if self.remaining() <= 0.0:
            raise FetchPolicyError(
                CODE_TIMEOUT, f"전체 시간 상한 {self.total_s:g}s 초과{(' (' + phase + ')') if phase else ''}", url=url
            )

    def clamp_timeout(self, timeout_s: float) -> float:
        """소켓 타임아웃을 남은 예산 이하로 잘라 준다(예산이 있어도 홉마다 무한정 기다리지 않게)."""
        return max(0.001, min(float(timeout_s), self.remaining()))


# --------------------------------------------------------------------- robots.txt
class RobotsRule:
    """robots 경로 규칙 1개. `*` = 임의열, 끝 `$` = 앵커."""

    __slots__ = ("allow", "pattern", "_regex")

    def __init__(self, allow: bool, pattern: str) -> None:
        self.allow = bool(allow)
        self.pattern = pattern
        self._regex = _compile_pattern(pattern)

    def matches(self, path: str) -> bool:
        return bool(self._regex.match(path))

    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return f"RobotsRule(allow={self.allow}, pattern={self.pattern!r})"


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = re.escape(body).replace(r"\*", ".*")
    return re.compile("^" + regex + ("$" if anchored else ""))


class RobotsGroup:
    """User-agent 연속 줄들 + 그 그룹의 규칙·Crawl-delay."""

    __slots__ = ("agents", "rules", "crawl_delay_s")

    def __init__(self) -> None:
        self.agents: list[str] = []
        self.rules: list[RobotsRule] = []
        self.crawl_delay_s: float | None = None

    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return f"RobotsGroup(agents={self.agents}, rules={len(self.rules)}, crawl_delay={self.crawl_delay_s})"


@dataclass(frozen=True)
class RobotsRules:
    """파싱된 robots.txt. 빈 그룹 목록 = "규칙 없음 = 전부 허용"."""

    groups: tuple[RobotsGroup, ...] = ()

    def group_for(self, product_token: str) -> RobotsGroup | None:
        """우리 UA 에 적용할 그룹 **하나**를 고른다(가장 긴 일치 토큰 우선, `*` 는 대체)."""
        token = (product_token or "").strip().lower()
        fallback: RobotsGroup | None = None
        best: RobotsGroup | None = None
        best_length = -1
        for group in self.groups:
            for agent in group.agents:
                if agent == "*":
                    if fallback is None:
                        fallback = group
                    continue
                if token and token.startswith(agent) and len(agent) > best_length:
                    best, best_length = group, len(agent)
        return best if best is not None else fallback

    def allows(self, path: str, *, product_token: str) -> bool:
        """경로 허용 여부 — 가장 긴 일치 규칙이 이기고, 동률이면 Allow 가 이긴다."""
        group = self.group_for(product_token)
        if group is None or not group.rules:
            return True
        winner: RobotsRule | None = None
        for rule in group.rules:
            if not rule.matches(path):
                continue
            if winner is None or len(rule.pattern) > len(winner.pattern):
                winner = rule
            elif len(rule.pattern) == len(winner.pattern) and rule.allow and not winner.allow:
                winner = rule
        return True if winner is None else winner.allow

    def crawl_delay_s(self, *, product_token: str) -> float | None:
        group = self.group_for(product_token)
        return None if group is None else group.crawl_delay_s


def parse_robots(text: str | None) -> RobotsRules:
    """robots.txt 본문 → `RobotsRules`. 본문 없음/빈 문자열 = 규칙 없음(허용)."""
    groups: list[RobotsGroup] = []
    current: RobotsGroup | None = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name == "user-agent":
            # 연속 UA 줄은 한 그룹을 공유하고, 규칙이 시작된 뒤의 UA 줄은 새 그룹을 연다.
            if current is None or current.rules or current.crawl_delay_s is not None:
                current = RobotsGroup()
                groups.append(current)
            current.agents.append(value.lower())
            continue
        if current is None:  # UA 줄 이전의 지시자는 무시한다(소속 그룹이 없다)
            continue
        if name == "disallow":
            if value:  # 빈 Disallow = 제약 없음 → 규칙 아님
                current.rules.append(RobotsRule(False, value))
        elif name == "allow":
            if value:
                current.rules.append(RobotsRule(True, value))
        elif name in ("crawl-delay", "crawldelay"):
            try:
                parsed = float(value)
            except ValueError:
                continue
            if parsed >= 0:
                current.crawl_delay_s = parsed
    return RobotsRules(groups=tuple(groups))


@dataclass(frozen=True)
class RobotsDocument:
    """robots.txt 조회 결과. `state` 가 판정을 결정한다(메시지 문자열로 판정하지 않는다)."""

    state: str
    text: str = ""
    status: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    crawl_delay_s: float = 0.0
    source: str = ROBOTS_OK
    detail: str = ""
    path: str = ""


class RobotsGate:
    """robots.txt 조회·캐시·판정. 조회 자체는 fetcher 콜백에 위임한다(네트워크는 한 곳)."""

    def __init__(
        self,
        fetch_robots: Callable[..., RobotsDocument],
        *,
        product_token: str,
        ttl_s: float = 3600.0,
        on_unavailable: str = ON_UNAVAILABLE_DENY,
        max_domain_delay_s: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if on_unavailable not in (ON_UNAVAILABLE_DENY, ON_UNAVAILABLE_ALLOW):
            raise ValueError(f"on_unavailable 은 {ON_UNAVAILABLE_DENY}|{ON_UNAVAILABLE_ALLOW} 만 허용: {on_unavailable!r}")
        self._fetch = fetch_robots
        self.product_token = product_token
        self.ttl_s = float(ttl_s)
        self.on_unavailable = on_unavailable
        self.max_domain_delay_s = float(max_domain_delay_s)
        self._clock = clock or _time.monotonic
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, RobotsRules | None, RobotsDecision]] = {}
        self.fetch_count = 0

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)

    def decision(self, target, *, deadline: Deadline | None = None) -> RobotsDecision:
        """대상 URL 의 허용 여부 + 유효 Crawl-delay. 캐시는 호스트(키) 단위."""
        key = f"{target.scheme}://{target.host}:{target.port}"
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
        if cached and cached[0] > now:
            return self._finalize(cached[1], cached[2], target.path_with_query)

        document = self._fetch(target, deadline=deadline)
        self.fetch_count += 1
        rules = parse_robots(document.text) if document.state == ROBOTS_OK else None
        base = self._base_decision(document)
        with self._lock:
            # ttl_s 이 0 이면 다음 호출은 재조회한다(테스트가 robots 변경을 즉시 반영할 수 있게).
            self._cache[key] = (now + self.ttl_s, rules, base)
        return self._finalize(rules, base, target.path_with_query)

    def _base_decision(self, document: RobotsDocument) -> RobotsDecision:
        """robots 문서 상태 → 경로와 무관한 기저 결정(허용여부·유효 지연)."""
        if document.state == ROBOTS_UNAVAILABLE:
            allowed = self.on_unavailable == ON_UNAVAILABLE_ALLOW
            return RobotsDecision(
                allowed=allowed,
                crawl_delay_s=self.max_domain_delay_s if allowed else 0.0,
                source=ROBOTS_UNAVAILABLE,
                detail=(
                    f"robots 조회 불가({document.reason or document.status}) → "
                    f"{'허용(명시 설정)' if allowed else '차단(fail-closed)'}"
                ),
            )
        if document.state == ROBOTS_ABSENT:
            return RobotsDecision(
                allowed=True, crawl_delay_s=0.0, source=ROBOTS_ABSENT, detail="robots.txt 없음(4xx) → 제약 없음"
            )
        delay = parse_robots(document.text).crawl_delay_s(product_token=self.product_token) or 0.0
        capped = min(delay, self.max_domain_delay_s)
        note = "" if capped >= delay else f" (Crawl-delay {delay:g}s → 상한 {capped:g}s)"
        return RobotsDecision(allowed=True, crawl_delay_s=capped, source=ROBOTS_OK, detail=f"robots.txt 200{note}")

    def _finalize(self, rules: RobotsRules | None, base: RobotsDecision, path: str) -> RobotsDecision:
        """경로별 판정 — `ok` 문서일 때만 규칙을 적용한다(없음/불가는 기저 결정 그대로)."""
        if rules is None:
            return base
        return RobotsDecision(
            allowed=rules.allows(path, product_token=self.product_token),
            crawl_delay_s=base.crawl_delay_s,
            source=base.source,
            detail=base.detail,
            path=path,
        )


# --------------------------------------------------------------------- 도메인별 지연
class DomainRateLimiter:
    """호스트별 최소 요청 간격. 시계·슬립을 주입할 수 있어 테스트가 실제로 자지 않는다."""

    def __init__(
        self,
        *,
        min_interval_s: float = 0.0,
        max_interval_s: float = 30.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.max_interval_s = max(0.0, float(max_interval_s))
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._lock = threading.Lock()
        self._next_at: dict[str, float] = {}
        self.waits: list[tuple[str, float]] = []

    def effective_interval(self, interval_s: float = 0.0) -> float:
        return min(max(self.min_interval_s, float(interval_s or 0.0)), self.max_interval_s)

    def reserve(self, host: str, *, interval_s: float = 0.0) -> float:
        """다음 슬롯을 예약하고 **대기 시간**을 돌려준다 — 동시 호출이 둘 다 '0초'를 받지 않게."""
        interval = self.effective_interval(interval_s)
        with self._lock:
            now = self._clock()
            scheduled = max(now, self._next_at.get(host, now))
            wait = max(0.0, scheduled - now)
            self._next_at[host] = scheduled + interval
        return wait

    def defer(self, host: str, interval_s: float) -> float:
        """이미 잡아 둔 다음 슬롯을 **뒤로만** 미룬다(앞당기지 않는다).

        robots.txt 에서 `Crawl-delay` 를 처음 알게 된 순간, 그 지식이 도착하기 전에 잡아 둔
        슬롯(robots 요청 자신의 슬롯)에는 지연이 반영되지 않는다. 그 상태로 다음 요청을 바로
        보내면 "선언된 지연을 무시한 첫 요청" 이 되므로, 지연을 알게 된 즉시 슬롯을 미룬다.
        """
        interval = self.effective_interval(interval_s)
        if interval <= 0.0:
            return 0.0
        with self._lock:
            now = self._clock()
            current = self._next_at.get(host, now)
            target = now + interval
            if target > current:
                self._next_at[host] = target
            return max(0.0, self._next_at[host] - now)

    def wait(self, host: str, *, interval_s: float = 0.0, deadline: Deadline | None = None, url: str | None = None) -> float:
        """간격을 지킨다. 예산을 넘길 대기는 **하지 않고** 타임아웃으로 끝낸다."""
        wait = self.reserve(host, interval_s=interval_s)
        if wait <= 0.0:
            return 0.0
        if deadline is not None:
            remaining = deadline.remaining()
            if remaining <= wait:
                raise FetchPolicyError(
                    CODE_TIMEOUT,
                    f"예의 지연 대기 {wait:.2f}s 가 남은 예산 {remaining:.2f}s 를 초과 — 요청을 보내지 않음",
                    url=url,
                )
        self.waits.append((host, wait))
        self._sleep(wait)
        return wait
