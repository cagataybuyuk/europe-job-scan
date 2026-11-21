"""Company-first discovery (ROADMAP Faz 1.2): watchlist row, URL->ATS classifier, ATS list adapters.

Offline by construction: the transport is INJECTED (``fetch(url) -> bytes | dict``). There is no default
transport; calling without one raises ``NoCarrierError``. Nothing here imports a network library.
Aggregators (LinkedIn/Indeed/Glassdoor) are never bulk-fetched (K4): they classify as ``blocked_aggregator``.
Unknown hosts classify as ``custom`` (no guessing). Output rows match ``dedupe.dedupe_listings`` input.

The public JSON shapes (Greenhouse boards-api, Lever postings, SmartRecruiters postings) come from
knowledge, NOT verified against the live services.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, quote, urlparse

Fetch = Callable[[str], "bytes | dict | list"]

_AGGREGATORS = ("linkedin.com", "indeed.com", "glassdoor.com")
_SUPPORTED = ("greenhouse", "lever", "smartrecruiters")


class DiscoveryError(Exception):
    """Base error; ``kind`` is a stable machine-readable class."""
    kind = "discovery_error"

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.kind}:{detail}" if detail else self.kind)
        self.detail = detail


class NoCarrierError(DiscoveryError):
    kind = "no_carrier"


class WatchlistError(DiscoveryError):
    kind = "invalid_watchlist_row"


class UnsupportedAtsError(DiscoveryError):
    kind = "unsupported_ats"


class BadPayloadError(DiscoveryError):
    kind = "bad_payload"


class FetchFailedError(DiscoveryError):
    kind = "fetch_failed"


@dataclass(frozen=True)
class WatchlistRow:
    company: str
    careers_url: str


def parse_watchlist(rows: Iterable[Mapping[str, Any]]) -> tuple[WatchlistRow, ...]:
    """Only ``company`` and ``careers_url`` are read; any other key is ignored (no personal data kept)."""
    out = []
    for i, r in enumerate(rows):
        company = r.get("company") if isinstance(r, Mapping) else None
        url = r.get("careers_url") if isinstance(r, Mapping) else None
        if not isinstance(company, str) or not company.strip() or not isinstance(url, str) or not url.strip():
            raise WatchlistError(f"row {i}")
        try:
            parsed = urlparse(url.strip())
            host = parsed.hostname
        except ValueError:
            raise WatchlistError(f"row {i} malformed url") from None
        if parsed.scheme != "https" or not host:
            raise WatchlistError(f"row {i} not https or no host")
        out.append(WatchlistRow(company.strip(), url.strip()))
    return tuple(out)


def _host(h: str | None) -> str:
    return (h or "").lower().rstrip(".")


def _on(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


@dataclass(frozen=True)
class AtsClass:
    kind: str            # greenhouse|lever|smartrecruiters|workday|workable|custom|blocked_aggregator|invalid
    slug: str | None = None


def classify_url(url: str) -> AtsClass:
    try:
        p = urlparse((url or "").strip())
        host = _host(p.hostname)
    except ValueError:
        return AtsClass("invalid")
    if p.scheme != "https" or not host:
        return AtsClass("invalid")
    if any(_on(host, a) for a in _AGGREGATORS):
        return AtsClass("blocked_aggregator")
    segs = [s for s in p.path.split("/") if s]
    first = segs[0] if segs else None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"):
        slug = first if first and first != "embed" else (parse_qs(p.query).get("for") or [None])[0]
        return AtsClass("greenhouse", slug)
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        return AtsClass("lever", first)
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        return AtsClass("smartrecruiters", first)
    if _on(host, "myworkdayjobs.com"):
        return AtsClass("workday", host.split(".")[0])
    if host == "apply.workable.com":
        return AtsClass("workable", first)
    return AtsClass("custom")


def list_api_url(cls: AtsClass) -> str:
    if cls.kind not in _SUPPORTED:
        raise UnsupportedAtsError(cls.kind)
    if not cls.slug:
        raise UnsupportedAtsError(f"{cls.kind}:no_slug")
    s = quote(cls.slug, safe="")
    return {"greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
            "lever": f"https://api.lever.co/v0/postings/{s}?mode=json",
            "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{s}/postings"}[cls.kind]


def _load(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        if not payload.strip():
            raise BadPayloadError("empty")
        try:
            return json.loads(bytes(payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise BadPayloadError("invalid_json") from e
    if isinstance(payload, (dict, list)):
        return payload
    raise BadPayloadError("unsupported_type")


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _row(company: str, role: str, location: str, url: str, kind: str) -> dict[str, str]:
    return {"company": company, "role": role, "location": location, "url": url,
            "ats_kind": kind, "source": f"company_watchlist:{kind}"}


def _items(data: Any, key: str | None) -> list:
    items = data.get(key) if key and isinstance(data, dict) else (data if key is None else None)
    if not isinstance(items, list):
        raise BadPayloadError(f"missing_{key or 'list'}")
    return items


def parse_greenhouse(payload: Any, company: str) -> list[dict[str, str]]:
    items = _items(_load(payload), "jobs")
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        loc = j.get("location")
        title, url = _s(j.get("title")), _s(j.get("absolute_url"))
        if title and url:
            out.append(_row(company, title, _s(loc.get("name")) if isinstance(loc, dict) else _s(loc), url, "greenhouse"))
    if items and not out:
        raise BadPayloadError("no_valid_entries")
    return out


def parse_lever(payload: Any, company: str) -> list[dict[str, str]]:
    items = _items(_load(payload), None)
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories")
        title, url = _s(j.get("text")), _s(j.get("hostedUrl"))
        if title and url:
            out.append(_row(company, title, _s(cats.get("location")) if isinstance(cats, dict) else "", url, "lever"))
    if items and not out:
        raise BadPayloadError("no_valid_entries")
    return out


def parse_smartrecruiters(payload: Any, company: str, slug: str) -> list[dict[str, str]]:
    items = _items(_load(payload), "content")
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        title, jid = _s(j.get("name")), _s(j.get("id"))
        if title and jid:
            place = ", ".join(x for x in (_s(loc.get("city")), _s(loc.get("country")).upper()) if x)
            out.append(_row(company, title, place, f"https://jobs.smartrecruiters.com/{quote(slug, safe='')}/{quote(jid, safe='')}", "smartrecruiters"))
    if items and not out:
        raise BadPayloadError("no_valid_entries")
    return out


class RateLimiter:
    """Minimum interval between requests, with injected clock and sleep (no real time in tests)."""

    def __init__(self, min_interval_s: float, clock: Callable[[], float], sleep: Callable[[float], None]):
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be >= 0")
        self._min, self._clock, self._sleep, self._last = min_interval_s, clock, sleep, None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self._min - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now


def discover_company(row: WatchlistRow, fetch: Fetch | None, limiter: RateLimiter | None = None) -> list[dict[str, str]]:
    cls = classify_url(row.careers_url)
    api = list_api_url(cls)           # raises UnsupportedAtsError for custom/workday/workable/aggregator/invalid
    if fetch is None:
        raise NoCarrierError(row.company)
    if limiter is not None:
        limiter.wait()
    try:
        payload = fetch(api)
    except DiscoveryError:
        raise
    except Exception as e:
        raise FetchFailedError(type(e).__name__) from e
    if cls.kind == "greenhouse":
        return parse_greenhouse(payload, row.company)
    if cls.kind == "lever":
        return parse_lever(payload, row.company)
    return parse_smartrecruiters(payload, row.company, cls.slug or "")


@dataclass(frozen=True)
class DiscoveryReport:
    listings: tuple[dict[str, str], ...]
    errors: tuple[tuple[str, str], ...]   # (company, error kind) - every failure is reported, none swallowed


def discover_all(rows: Iterable[WatchlistRow], fetch: Fetch | None, limiter: RateLimiter | None = None) -> DiscoveryReport:
    listings: list[dict[str, str]] = []
    errors: list[tuple[str, str]] = []
    for row in rows:
        try:
            listings.extend(discover_company(row, fetch, limiter))
        except DiscoveryError as e:
            errors.append((row.company, e.kind))
    return DiscoveryReport(tuple(listings), tuple(errors))
