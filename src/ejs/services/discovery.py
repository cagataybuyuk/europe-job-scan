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
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, quote, urlparse

Fetch = Callable[[str], "bytes | dict | list"]

_AGGREGATORS = ("linkedin.com", "indeed.com", "glassdoor.com")
_SUPPORTED = ("greenhouse", "lever", "smartrecruiters")
PAGE_SIZE = 100
MAX_PAGES_PER_EMPLOYER = 5
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
API_HOSTS = {
    ("greenhouse", "global"): "boards-api.greenhouse.io",
    ("greenhouse", "eu"): "boards.eu.greenhouse.io",
    ("lever", "global"): "api.lever.co",
    ("lever", "eu"): "api.eu.lever.co",
    ("smartrecruiters", "global"): "api.smartrecruiters.com",
}
_PUBLIC_HOSTS = {
    ("greenhouse", "global"): "job-boards.greenhouse.io",
    ("greenhouse", "eu"): "job-boards.eu.greenhouse.io",
    ("lever", "global"): "jobs.lever.co",
    ("lever", "eu"): "jobs.eu.lever.co",
    ("smartrecruiters", "global"): "jobs.smartrecruiters.com",
}


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
    region: str = "global"


def classify_url(url: str) -> AtsClass:
    try:
        p = urlparse((url or "").strip())
        host = _host(p.hostname)
        if p.username is not None or p.password is not None or p.port not in (None, 443):
            return AtsClass("invalid")
    except ValueError:
        return AtsClass("invalid")
    if p.scheme != "https" or not host:
        return AtsClass("invalid")
    if any(_on(host, a) for a in _AGGREGATORS):
        return AtsClass("blocked_aggregator")
    segs = [s for s in p.path.split("/") if s]
    first = segs[0] if segs else None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"):
        slug = first if first and first != "embed" else (parse_qs(p.query).get("for") or [None])[0]
        return AtsClass("greenhouse", slug, "eu" if ".eu." in host else "global")
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        return AtsClass("lever", first, "eu" if host == "jobs.eu.lever.co" else "global")
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
    if not cls.slug or not _IDENTIFIER.fullmatch(cls.slug):
        raise UnsupportedAtsError(f"{cls.kind}:invalid_slug")
    host = API_HOSTS.get((cls.kind, cls.region))
    if host is None:
        raise UnsupportedAtsError(f"{cls.kind}:invalid_region")
    slug = quote(cls.slug, safe="")
    path = {
        "greenhouse": f"/v1/boards/{slug}/jobs",
        "lever": f"/v0/postings/{slug}?mode=json",
        "smartrecruiters": f"/v1/companies/{slug}/postings",
    }[cls.kind]
    return f"https://{host}{path}"


def _load(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        if not payload.strip():
            raise BadPayloadError("empty")
        try:
            return json.loads(bytes(payload).decode("utf-8"))
        except (ValueError, RecursionError) as e:
            raise BadPayloadError("invalid_json") from e
    if isinstance(payload, (dict, list)):
        return payload
    raise BadPayloadError("unsupported_type")


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _row(company: str, role: str, location: str, url: str, kind: str) -> dict[str, str]:
    return {"company": company, "role": role, "location": location, "url": url,
            "ats_kind": kind, "source": f"company_watchlist:{kind}"}


def _with_identity(row: dict[str, str], raw_id: Any, cls: AtsClass | None) -> dict[str, str]:
    if cls is None:
        return row
    posting_id = str(raw_id) if type(raw_id) is int and raw_id > 0 else _s(raw_id)
    if not _IDENTIFIER.fullmatch(posting_id) or (cls.kind == "greenhouse" and not posting_id.isdecimal()):
        posting_id = ""
    # Use the regional provider URL template. Never persist opaque paths, query strings or fragments.
    host = _PUBLIC_HOSTS[cls.kind, cls.region]
    suffix = f"jobs/{posting_id}" if cls.kind == "greenhouse" else posting_id
    row.update(
        tenant=cls.slug or "",
        region=cls.region,
        posting_id=posting_id,
        url=f"https://{host}/{cls.slug}/{suffix}" if posting_id else "",
    )
    return row


def _items(data: Any, key: str | None) -> list:
    items = data.get(key) if key and isinstance(data, dict) else (data if key is None else None)
    if not isinstance(items, list):
        raise BadPayloadError(f"missing_{key or 'list'}")
    return items


def parse_greenhouse(payload: Any, company: str, *, identity: AtsClass | None = None) -> list[dict[str, str]]:
    items = _items(_load(payload), "jobs")
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        loc = j.get("location")
        title, url = _s(j.get("title")), _s(j.get("absolute_url"))
        if title and (url or identity is not None):
            row = _row(company, title, _s(loc.get("name")) if isinstance(loc, dict) else _s(loc), url, "greenhouse")
            out.append(_with_identity(row, j.get("id"), identity))
    if items and not out:
        raise BadPayloadError("no_valid_entries")
    return out


def parse_lever(payload: Any, company: str, *, identity: AtsClass | None = None) -> list[dict[str, str]]:
    items = _items(_load(payload), None)
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories")
        title, url = _s(j.get("text")), _s(j.get("hostedUrl"))
        if title and (url or identity is not None):
            row = _row(company, title, _s(cats.get("location")) if isinstance(cats, dict) else "", url, "lever")
            out.append(_with_identity(row, j.get("id"), identity))
    if items and not out:
        raise BadPayloadError("no_valid_entries")
    return out


def parse_smartrecruiters(payload: Any, company: str, slug: str, *, identity: AtsClass | None = None) -> list[dict[str, str]]:
    items = _items(_load(payload), "content")
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        title, jid = _s(j.get("name")), _s(j.get("id"))
        publisher = j.get("company")
        if identity and isinstance(publisher, dict):
            identifier = _s(publisher.get("identifier"))
            if identifier and identifier.casefold() != slug.casefold():
                raise BadPayloadError("tenant_mismatch")
        if title and (jid or identity is not None):
            place = ", ".join(x for x in (_s(loc.get("city")), _s(loc.get("country")).upper()) if x)
            url = f"https://jobs.smartrecruiters.com/{quote(slug, safe='')}/{quote(jid, safe='')}" if jid else ""
            out.append(_with_identity(_row(company, title, place, url, "smartrecruiters"), jid, identity))
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
    return _parse_page(_fetch(fetch, api), row, cls)


def _fetch(fetch: Fetch, url: str) -> Any:
    try:
        return fetch(url)
    except DiscoveryError:
        raise
    except Exception as e:
        raise FetchFailedError(type(e).__name__) from e


def _parse_page(payload: Any, row: WatchlistRow, cls: AtsClass, *, identified: bool = False) -> list[dict[str, str]]:
    identity = cls if identified else None
    if cls.kind == "greenhouse":
        return parse_greenhouse(payload, row.company, identity=identity)
    if cls.kind == "lever":
        return parse_lever(payload, row.company, identity=identity)
    return parse_smartrecruiters(payload, row.company, cls.slug or "", identity=identity)


def _natural_number(value: Any) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class EmployerDiscovery:
    listings: list[dict[str, str]]
    status: str
    error_kind: str | None = None


def discover_pages(row: WatchlistRow, fetch: Fetch | None) -> EmployerDiscovery:
    """Read a bounded public feed, retaining normalized records from incomplete results."""
    cls = classify_url(row.careers_url)
    listings: list[dict[str, str]] = []
    seen_posting_ids: set[str] = set()
    offset = 0
    expected_total = None
    error = None
    try:
        base = list_api_url(cls)
        if fetch is None:
            raise NoCarrierError(row.company)
        for _ in range(MAX_PAGES_PER_EMPLOYER):
            url = base
            if cls.kind == "lever":
                url += f"&skip={offset}&limit={PAGE_SIZE}"
            elif cls.kind == "smartrecruiters":
                url += f"?offset={offset}&limit={PAGE_SIZE}"
            data = _load(_fetch(fetch, url))
            item_key = {"greenhouse": "jobs", "lever": None, "smartrecruiters": "content"}[cls.kind]
            size = len(_items(data, item_key))
            if cls.kind == "smartrecruiters":
                total, start, limit = (data.get(key) for key in ("totalFound", "offset", "limit"))
                if (not all(_natural_number(n) for n in (total, start, limit))
                        or start != offset or not 0 < limit <= PAGE_SIZE or size > limit
                        or offset + size > total
                        or (expected_total is not None and total != expected_total)):
                    error = "invalid_pagination"
                    break
                expected_total = total
                if size == 0 and offset < total:
                    error = "pagination_no_progress"
                    break
            elif cls.kind == "lever" and size > PAGE_SIZE:
                error = "invalid_pagination"
                break

            page = _parse_page(data, row, cls, identified=True)
            valid_page = []
            for item in page:
                try:
                    for value in item.values():
                        value.encode("utf-8", errors="strict")
                except UnicodeEncodeError:
                    continue
                valid_page.append(item)
            if len(valid_page) != len(page):
                error = "invalid_vacancy_unicode"
            page = valid_page
            page_posting_ids = [item["posting_id"] for item in page if item["posting_id"]]
            posting_ids = set(page_posting_ids)
            for item in page:
                item["discovered_via"] = url
            listings.extend(page)
            if error:
                break
            if len(posting_ids) != len(page_posting_ids):
                error = "duplicate_id_in_page"
                break
            if seen_posting_ids.intersection(posting_ids):
                error = "overlapping_page"
                break
            seen_posting_ids.update(posting_ids)
            if size != len(page):
                error = "invalid_posting"
                break
            if cls.kind == "greenhouse":
                meta = data.get("meta")
                total = meta.get("total") if isinstance(meta, dict) else None
                if not _natural_number(total) or total != size:
                    error = "greenhouse_total_mismatch"
                    break
            if (cls.kind == "greenhouse" or (cls.kind == "lever" and size < PAGE_SIZE)
                    or (cls.kind == "smartrecruiters" and offset + size == expected_total)):
                return EmployerDiscovery(listings, "complete" if listings else "empty")
            offset += size
    except DiscoveryError as exc:
        error = exc.kind
    return EmployerDiscovery(listings, "partial" if listings else "error", error or "page_limit_exceeded")


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
