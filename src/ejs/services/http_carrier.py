"""Read-only HTTP carrier for company-first discovery (EJS-W1). OFF by default.

GET only, no cookies/Authorization/identity headers, fixed plain User-Agent, allow-listed public ATS hosts,
global >=1 s between requests, at most 5 requests per employer and 60 per run.
429/403/5xx (and any other non-2xx) stops that employer: NO retry. The request log keeps URL, time, status and
size only. Response bodies are returned to the parser in memory.
Clock, sleep and transport are injected; tests must never reach a real network. ``urllib_transport`` (lazy
import, no redirects) exists for the separately gated live phase and is not used by any offline test.
"""
from __future__ import annotations

import math
from typing import Any, Callable
from urllib.parse import urlparse

from ejs.services.discovery import API_HOSTS, MAX_PAGES_PER_EMPLOYER, DiscoveryError, NoCarrierError, RateLimiter

ALLOWED_HOSTS = frozenset(API_HOSTS.values())
USER_AGENT = "ejs-shadow-discovery/1.0 (read-only public job listings; no login)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
MIN_INTERVAL_S = 1.0
MAX_REQUESTS_PER_RUN = 60
MAX_BODY_BYTES = 5_000_000

Transport = Callable[[str, dict, float], "tuple[int, bytes]"]


class HostNotAllowedError(DiscoveryError):
    kind = "host_not_allowed"


class RequestBudgetError(DiscoveryError):
    kind = "request_budget_exceeded"


class PageLimitError(DiscoveryError):
    kind = "page_limit_exceeded"


class EmployerStoppedError(DiscoveryError):
    kind = "employer_stopped"


class HttpStatusError(DiscoveryError):
    kind = "http_error"

    def __init__(self, status: int):
        super().__init__(str(status))
        self.status = status
        self.kind = f"http_{status}"


class BodyTooLargeError(DiscoveryError):
    kind = "body_too_large"


class HttpCarrier:
    """``carrier(url) -> bytes`` matches ``discovery.Fetch``. Call ``begin_employer`` before each employer.

    The 1 s / 5 pages / 60 requests limits are mandatory: parameters may only tighten them (a looser value raises
    ``ValueError``). Offline replay uses ``ReplayCarrier`` with the CLI's file-backed transport.
    """

    _MIN_INTERVAL_FLOOR = MIN_INTERVAL_S

    def __init__(self, *, enabled: bool = False, transport: Transport | None = None,
                 clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None,
                 max_requests: int = MAX_REQUESTS_PER_RUN, max_pages: int = MAX_PAGES_PER_EMPLOYER,
                 min_interval_s: float = MIN_INTERVAL_S, timeout_s: float = 20.0,
                 allowed_hosts: frozenset[str] = ALLOWED_HOSTS):
        if (type(max_requests) is not int or not 0 < max_requests <= MAX_REQUESTS_PER_RUN
                or type(max_pages) is not int or not 0 < max_pages <= MAX_PAGES_PER_EMPLOYER):
            raise ValueError("request/page limits may only be tightened")
        if not math.isfinite(min_interval_s) or min_interval_s < self._MIN_INTERVAL_FLOOR:
            raise ValueError("min_interval_s below the mandatory floor")
        if not allowed_hosts <= ALLOWED_HOSTS:
            raise ValueError("allowed_hosts may only be a subset of ALLOWED_HOSTS")
        self._enabled, self._transport = enabled, transport
        self._clock, self._max_req, self._max_pages = clock, max_requests, max_pages
        self._timeout, self._hosts = timeout_s, frozenset(allowed_hosts)
        self._limiter = RateLimiter(min_interval_s, clock, sleep) if clock and sleep else None
        self._employer: str | None = None
        self._stopped: set[str] = set()
        self.per_employer: dict[str, int] = {}
        self.log: list[dict[str, Any]] = []
        self.total = 0

    def begin_employer(self, name: str) -> None:
        self._employer = name
        self.per_employer.setdefault(name, 0)

    def _log(self, url: str, status: int | str, size: int, started: float) -> None:
        self.log.append({"employer": self._employer, "url": url, "t": started, "status": status, "bytes": size})

    def __call__(self, url: str) -> bytes:
        if not self._enabled or self._transport is None or self._clock is None or self._limiter is None:
            raise NoCarrierError("carrier_disabled")
        emp = self._employer or ""
        if emp in self._stopped:
            raise EmployerStoppedError(emp)
        if _public_api_host(url, self._hosts) is None:
            self._log("", "blocked", 0, self._clock())
            raise HostNotAllowedError()
        if self.total >= self._max_req:
            raise RequestBudgetError(str(self._max_req))
        if self.per_employer.get(emp, 0) >= self._max_pages:
            raise PageLimitError(emp)
        self._limiter.wait()
        self.total += 1
        self.per_employer[emp] = self.per_employer.get(emp, 0) + 1
        started = self._clock()
        try:
            status, body = self._transport(url, dict(REQUEST_HEADERS), self._timeout)
        except DiscoveryError as exc:
            self._stopped.add(emp)
            self._log(url, exc.kind, 0, started)
            raise
        except Exception as e:
            self._stopped.add(emp)
            self._log(url, "error", 0, started)
            raise DiscoveryError(type(e).__name__) from e
        self._log(url, status, len(body), started)
        if not 200 <= status < 300:
            self._stopped.add(emp)
            raise HttpStatusError(status)
        if len(body) > MAX_BODY_BYTES:
            self._stopped.add(emp)
            raise BodyTooLargeError(str(len(body)))
        return body


class ReplayCarrier(HttpCarrier):
    """Offline fixture replay: no delay floor, never records, still enforces hosts and 5/60 caps."""

    _MIN_INTERVAL_FLOOR = 0.0

    def __init__(self, *, transport: Transport, clock: Callable[[], float], **kw: Any):
        if getattr(transport, "__name__", "") == "urllib_transport":
            raise ValueError("ReplayCarrier cannot use the live transport")
        kw.setdefault("min_interval_s", 0.0)
        super().__init__(enabled=True, transport=transport, clock=clock, sleep=lambda s: None, **kw)


def _public_api_host(url: str, hosts: frozenset[str]) -> str | None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if (parsed.scheme == "https" and host in hosts and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None and not parsed.fragment):
            return host
    except ValueError:
        pass
    return None


def urllib_transport(url: str, headers: dict, timeout: float) -> tuple[int, bytes]:
    """Live-phase transport: GET only, no redirects, no cookie jar. NOT used offline."""
    if _public_api_host(url, ALLOWED_HOSTS) is None:
        raise HostNotAllowedError()
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: D401 - refuse every redirect
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, headers={name: headers[name] for name in REQUEST_HEADERS if name in headers}, method="GET")
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, b""
