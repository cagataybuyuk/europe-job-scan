"""W3.5 research-backed country replay and the FUNCTION-02 live-feed adapter.

The replay itself has no network, filesystem, queue or submission integration.
``live_replay_batch`` reads pages through an injected read-only carrier and only
for pairs in the explicitly selected owner-approved pilot scope (default ``LIVE_APPROVED_PAIRS``). The existing four-employer
pilot gate and transport are unchanged. Research counts are historical
evidence, never a filter or a claim of current completeness.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import re
from urllib.parse import quote

from ejs.services.discovery import (
    AtsClass, BadPayloadError, DiscoveryError, PAGE_SIZE,
    MAX_PAGES_PER_EMPLOYER, parse_smartrecruiters,
)
from ejs.services.http_carrier import MAX_REQUESTS_PER_RUN, PageLimitError, RequestBudgetError

COUNTRIES = ("NL", "IE", "DE", "BE", "SE", "DK", "ES", "PT", "IT")
SCOPE_VERSION = "W3.4-2026-11-26-v1"
# Employer, exact tenant, research tier, reported counts in COUNTRIES order.
RESEARCH = (
    ("KPMG Nederland", "KPMGNederland", 1, (197, 0, 0, 0, 0, 0, 0, 0, 0)),
    ("Devoteam", "Devoteam", 1, (19, 0, 79, 34, 15, 13, 74, 233, 15)),
    ("Eurofins", "Eurofins", 1, (169, 41, 200, 41, 8, 20, 45, 11, 36)),
    ("Bosch Group", "BoschGroup", 1, (21, 0, 789, 18, 29, 2, 69, 229, 21)),
    ("Sia", "Sia", 1, (89, 4, 1, 88, 0, 0, 0, 0, 17)),
    ("ServiceNow", "ServiceNow", 1, (16, 22, 45, 4, 12, 2, 3, 0, 4)),
    ("Version 1", "Version1", 1, (0, 44, 0, 0, 0, 0, 1, 0, 0)),
    ("JACOBS DOUWE EGBERTS", "JACOBSDOUWEEGBERTS", 1, (36, 0, 20, 7, 0, 2, 0, 0, 0)),
    ("Implement Consulting Group", "ImplementConsultingGroup", 1, (0, 0, 40, 0, 4, 15, 0, 0, 0)),
    ("Delivery Hero", "DeliveryHero", 1, (0, 0, 20, 0, 14, 0, 63, 4, 28)),
    ("Colliers International EMEA", "ColliersInternationalEMEA", 1, (12, 0, 19, 3, 0, 0, 11, 1, 4)),
    ("PA Consulting", "PAConsulting", 1, (9, 4, 0, 0, 14, 10, 0, 0, 0)),
    ("SGS", "SGS", 2, (82, 18, 68, 50, 6, 0, 160, 16, 31)),
    ("H&M Group", "HMGroup", 2, (61, 3, 64, 46, 51, 5, 54, 12, 12)),
    ("Talan", "Talan", 2, (0, 0, 0, 10, 0, 0, 25, 4, 1)),
    ("SOCOTEC", "SOCOTEC", 2, (50, 0, 0, 3, 0, 0, 0, 0, 0)),
    ("Angelini Pharma Spa", "AngeliniPharmaSpa", 2, (0, 0, 4, 0, 0, 0, 3, 0, 48)),
    ("Natixis in Portugal", "NatixisInPortugal", 2, (0, 0, 0, 0, 0, 0, 0, 48, 0)),
    ("MET Group", "METGroup", 2, (0, 0, 3, 0, 0, 0, 15, 0, 5)),
    ("Brainlab", "Brainlab", 2, (0, 0, 30, 0, 1, 0, 3, 0, 1)),
    ("InPost", "InPost", 2, (12, 0, 0, 6, 0, 0, 2, 0, 0)),
    ("NielsenIQ", "NielsenIQ", 2, (3, 0, 16, 2, 2, 0, 8, 9, 0)),
    ("Strukton Nederland", "StruktonNederland", 2, (81, 0, 0, 11, 0, 0, 0, 0, 0)),
    ("Varrlyn", "Varrlyn", 3, (4, 0, 0, 0, 0, 0, 0, 0, 0)),
    ("The Nielsen Company", "TheNielsenCompany", 3, (2, 0, 2, 0, 0, 0, 3, 0, 0)),
    ("Aramark", "Aramark", 3, (0, 193, 0, 0, 0, 0, 1, 0, 0)),
    ("Evolution", "Evolution", 3, (4, 0, 1, 2, 14, 4, 9, 0, 4)),
)
_SCOPE = tuple((tenant, country) for _, tenant, _, _ in RESEARCH for country in COUNTRIES)
_NAMES = {tenant: name for name, tenant, _, _ in RESEARCH}
_ERROR = "EXPANSION_CHECKPOINT_REVIEW_REQUIRED"
_PAGE_ERROR = "page_review_required"
# Fixed vocabulary only: never persist arbitrary exception strings or provider values.
_PAGE_DETAILS = frozenset({
    "invalid_json", "invalid_page", "fixture_scope_mismatch", "invalid_pagination",
    "invalid_posting", "country_mismatch", "tenant_mismatch", "invalid_posting_id",
    "invalid_posting_text", "invalid_or_overlapping_postings", "missing_content",
    "no_valid_entries",
})
_LIVE_PAGE_ERRORS = frozenset(f"{_PAGE_ERROR}:{code}" for code in _PAGE_DETAILS) | {_PAGE_ERROR}


def _safe_page_error(exc: Exception) -> str:
    if isinstance(exc, BadPayloadError) and type(exc.detail) is str and exc.detail in _PAGE_DETAILS:
        return f"{_PAGE_ERROR}:{exc.detail}"
    return _PAGE_ERROR
# Exact (tenant, country) pairs approved for live read-only collection. Owner decision of 26 Nov 2026:
# one pilot pair. Widening this set needs a new owner approval in a reviewed change.
LIVE_APPROVED_PAIRS: frozenset[tuple[str, str]] = frozenset({("KPMGNederland", "NL")})
# Second owner-approved pilot, separate bundle and cumulative budget; never union with the default.
VERSION1_APPROVED_PAIRS: frozenset[tuple[str, str]] = frozenset({("Version1", "NL"), ("Version1", "IE")})
LIVE_FEED_HOST = "api.smartrecruiters.com"
# Total GET attempts (failures included) one live invocation may make; the owner-approved pilot ceiling.
LIVE_MAX_REQUESTS = 5
# Cumulative GET attempts one private bundle (one day's snapshot) may spend across all its invocations.
# Owner-approved pilot total; widening it is an owner decision.
LIVE_BUNDLE_MAX_REQUESTS = LIVE_MAX_REQUESTS
EVIDENCE_VERSION = 1
_POSTING_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class Cursor:
    tenant: str
    country: str
    offset: int = 0
    total: int | None = None
    seen: tuple[str, ...] = ()
    status: str = "pending"
    error: str | None = None


@dataclass(frozen=True)
class Checkpoint:
    scope_version: str
    cursors: tuple[Cursor, ...]


@dataclass(frozen=True)
class ReplayResult:
    checkpoint: Checkpoint
    listings: tuple[dict[str, str], ...]
    pages: int
    duplicates: int


def initial_checkpoint() -> Checkpoint:
    """Explicit expansion scope: all 27 × 9 pairs, including historical zeros."""
    return Checkpoint(SCOPE_VERSION, tuple(Cursor(*pair) for pair in _SCOPE))


def validate_checkpoint(checkpoint: Checkpoint) -> None:
    """Reject cursors that could not result from replaying this scope."""
    if (type(checkpoint) is not Checkpoint or checkpoint.scope_version != SCOPE_VERSION
            or type(checkpoint.cursors) is not tuple
            or len(checkpoint.cursors) != len(_SCOPE)):
        raise ValueError(_ERROR)
    incomplete_tenants: set[str] = set()
    for c, pair in zip(checkpoint.cursors, _SCOPE):
        if (type(c) is not Cursor or (c.tenant, c.country) != pair
                or type(c.offset) is not int or c.offset < 0
                or type(c.seen) is not tuple or len(c.seen) != c.offset
                or any(type(x) is not str or not _POSTING_ID.fullmatch(x) for x in c.seen)
                or len(set(c.seen)) != len(c.seen)
                or (c.total is not None and
                    (type(c.total) is not int or c.total < c.offset))
                or (c.total is None and c.offset != 0)
                or c.status not in ("pending", "complete", "error")
                or (c.status == "complete" and c.total != c.offset)
                or (c.status != "complete" and c.total is not None
                    and not 0 < c.offset < c.total)
                or (c.status == "error" and (type(c.error) is not str or c.error not in _LIVE_PAGE_ERRORS))
                or (c.status != "error" and c.error is not None)
                or (c.tenant in incomplete_tenants and c != Cursor(*pair))):
            raise ValueError(_ERROR)
        if c.status != "complete":
            incomplete_tenants.add(c.tenant)


def _page(cursor: Cursor, payload: object) -> tuple[Cursor, list[dict[str, str]]]:
    """Validate one synthetic country page atomically, never advance on drift."""
    if type(payload) is not dict:
        raise BadPayloadError("invalid_page")
    if payload.get("tenant") != cursor.tenant or payload.get("country") != cursor.country:
        raise BadPayloadError("fixture_scope_mismatch")
    total, offset, limit = (payload.get(k) for k in ("totalFound", "offset", "limit"))
    items = payload.get("content")
    if (not all(type(n) is int and n >= 0 for n in (total, offset, limit))
            or offset != cursor.offset or not 0 < limit <= PAGE_SIZE
            or type(items) is not list or len(items) > limit
            or offset + len(items) > total
            or (cursor.total is not None and cursor.total != total)
            or (not items and offset < total)):
        raise BadPayloadError("invalid_pagination")
    for item in items:
        if type(item) is not dict:
            raise BadPayloadError("invalid_posting")
        location, publisher = item.get("location"), item.get("company")
        if type(location) is not dict or location.get("country") != cursor.country:
            raise BadPayloadError("country_mismatch")
        if type(publisher) is not dict or publisher.get("identifier") != cursor.tenant:
            raise BadPayloadError("tenant_mismatch")
        posting_id = item.get("id")
        if type(posting_id) is not str or not _POSTING_ID.fullmatch(posting_id):
            raise BadPayloadError("invalid_posting_id")
        title, city = item.get("name"), location.get("city", "")
        if (type(title) is not str or not title.strip() or not title.isprintable()
                or type(city) is not str or (city and not city.isprintable())):
            raise BadPayloadError("invalid_posting_text")
    identity = AtsClass("smartrecruiters", cursor.tenant)
    page = parse_smartrecruiters(payload, _NAMES[cursor.tenant], cursor.tenant,
                                  identity=identity)
    ids = tuple(item["posting_id"] for item in page)
    if (len(page) != len(items) or len(set(ids)) != len(ids)
            or set(ids).intersection(cursor.seen)):
        raise BadPayloadError("invalid_or_overlapping_postings")
    new_offset = offset + len(items)
    return replace(cursor, offset=new_offset, total=total, seen=cursor.seen + ids,
                   status="complete" if new_offset == total else "pending"), page


@dataclass(frozen=True)
class LiveObservation:
    """What one live invocation observed: attempted requests and first-page rechecks."""
    requests: tuple[dict, ...] = ()
    rechecks: tuple[dict, ...] = ()


_RECHECK_STATUSES = ("stable", "drift", "unreadable", "not_checked", "not_applicable")


def empty_evidence() -> dict:
    """Durable live evidence kept in the same atomic private bundle as the checkpoint."""
    return {"version": EVIDENCE_VERSION, "budget": LIVE_BUNDLE_MAX_REQUESTS, "invocations": [],
            "rechecks": [], "in_flight": None}


def _natural(value: object, limit: int | None = None) -> bool:
    return type(value) is int and value >= 0 and (limit is None or value <= limit)


def validate_evidence(evidence: object) -> None:
    """Reject evidence that no sequence of recorded invocations could have produced."""
    if (type(evidence) is not dict
            or evidence.keys() != {"version", "budget", "invocations", "rechecks", "in_flight"}
            or evidence["version"] != EVIDENCE_VERSION or not _natural(evidence["budget"], LIVE_BUNDLE_MAX_REQUESTS)
            or type(evidence["invocations"]) is not list or type(evidence["rechecks"]) is not list):
        raise ValueError(_ERROR)
    for position, call in enumerate(evidence["invocations"], 1):
        if (type(call) is not dict or call.keys() != {"seq", "status", "reserved", "requests"}
                or call["seq"] != position or call["status"] not in ("completed", "interrupted_unverified")
                or not _natural(call["reserved"], LIVE_MAX_REQUESTS) or type(call["requests"]) is not list
                or len(call["requests"]) > call["reserved"]
                or (call["status"] == "interrupted_unverified" and call["requests"])):
            raise ValueError(_ERROR)
        for request in call["requests"]:
            if (type(request) is not dict or request.keys() != {"url", "status", "bytes"}
                    or type(request["url"]) is not str or not request["url"].isprintable()
                    or len(request["url"]) > 300 or type(request["status"]) not in (int, str)
                    or not _natural(request["bytes"])):
                raise ValueError(_ERROR)
    in_flight = evidence["in_flight"]
    if in_flight is not None and (
            type(in_flight) is not dict or in_flight.keys() != {"seq", "reserved"}
            or in_flight["seq"] != len(evidence["invocations"]) + 1
            or not 0 < in_flight["reserved"] <= LIVE_MAX_REQUESTS):
        raise ValueError(_ERROR)
    for check in evidence["rechecks"]:
        if (type(check) is not dict or check.keys() != {"seq", "tenant", "country", "status", "reason"}
                or (check["tenant"], check["country"]) not in _SCOPE
                or check["status"] not in _RECHECK_STATUSES or type(check["reason"]) is not str
                or not _natural(check["seq"]) or not 0 < check["seq"] <= len(evidence["invocations"])
                or evidence["invocations"][check["seq"] - 1]["status"] != "completed"):
            raise ValueError(_ERROR)
    if spent_requests(evidence) > evidence["budget"]:
        raise ValueError(_ERROR)


def spent_requests(evidence: dict) -> int:
    """Attempts spent so far. An interrupted or in-flight invocation is charged its full reservation."""
    spent = sum(len(c["requests"]) if c["status"] == "completed" else c["reserved"]
                for c in evidence["invocations"])
    return spent + (evidence["in_flight"]["reserved"] if evidence["in_flight"] else 0)


def remaining_requests(evidence: dict) -> int:
    return max(0, min(evidence["budget"], LIVE_BUNDLE_MAX_REQUESTS) - spent_requests(evidence))


def effective_rechecks(evidence: dict) -> dict[tuple[str, str], dict[str, str]]:
    """Worst recorded recheck per pair. Drift and unreadable are sticky: a later stable result never hides them."""
    order = {"drift": 0, "unreadable": 1, "stable": 2, "not_checked": 3, "not_applicable": 4}
    best: dict[tuple[str, str], dict[str, str]] = {}
    for check in evidence["rechecks"]:
        pair = (check["tenant"], check["country"])
        if pair not in best or order[check["status"]] < order[best[pair]["status"]]:
            best[pair] = {"status": check["status"], "reason": check["reason"]}
    return best


def settle_in_flight(evidence: dict) -> dict:
    """Recover from a process that died mid-invocation: its whole reservation counts as spent."""
    if evidence["in_flight"] is None:
        return evidence
    call = {"seq": evidence["in_flight"]["seq"], "status": "interrupted_unverified",
            "reserved": evidence["in_flight"]["reserved"], "requests": []}
    return {**evidence, "invocations": [*evidence["invocations"], call], "in_flight": None}


def begin_invocation(evidence: dict, reserved: int) -> dict:
    """Evidence to publish durably before any request of the invocation is sent."""
    if evidence["in_flight"] is not None or not 0 < reserved <= remaining_requests(evidence):
        raise ValueError(_ERROR)
    return {**evidence, "in_flight": {"seq": len(evidence["invocations"]) + 1, "reserved": reserved}}


def record_invocation(evidence: dict, observation: LiveObservation) -> dict:
    """Close the in-flight invocation with what it actually did; prior rechecks are only ever appended to."""
    in_flight = evidence["in_flight"]
    seq = in_flight["seq"]
    call = {"seq": seq, "status": "completed", "reserved": in_flight["reserved"],
            "requests": [{k: r[k] for k in ("url", "status", "bytes")} for r in observation.requests]}
    checks = [{"seq": seq, "tenant": c["tenant"], "country": c["country"],
               "status": c["status"], "reason": c["reason"]} for c in observation.rechecks]
    return {**evidence, "invocations": [*evidence["invocations"], call],
            "rechecks": [*evidence["rechecks"], *checks], "in_flight": None}


def live_page_url(tenant: str, country: str, offset: int) -> str:
    """Public postings feed URL; SmartRecruiters documents ``country`` as a lowercase ISO 3166-1 alpha-2 filter."""
    if (tenant, country) not in _SCOPE or type(offset) is not int or offset < 0:
        raise ValueError(_ERROR)
    return (f"https://{LIVE_FEED_HOST}/v1/companies/{quote(tenant, safe='')}/postings"
            f"?country={country.lower()}&offset={offset}&limit={PAGE_SIZE}")


def live_page(tenant: str, country: str, body: bytes) -> dict:
    """Turn a real feed response into the replay page shape without trusting it.

    The feed carries no tenant/country metadata, so those come from the request; offset/limit/total stay as echoed. The provider reports
    lowercase country codes; a posting is relabelled only when it equals the requested country ignoring case.
    Anything else stays as received and is rejected by ``_page``.
    """
    try:
        payload = json.loads(bytes(body).decode("utf-8"))
    except (ValueError, RecursionError):
        raise BadPayloadError("invalid_json") from None
    if type(payload) is not dict or type(payload.get("content")) is not list:
        raise BadPayloadError("invalid_page")
    items = []
    for item in payload["content"]:
        if type(item) is dict:
            item = dict(item)
            location, publisher = item.get("location"), item.get("company")
            if type(location) is dict and type(location.get("country")) is str \
                    and location["country"].casefold() == country.casefold():
                item["location"] = {**location, "country": country}
            if type(publisher) is dict and type(publisher.get("identifier")) is str \
                    and publisher["identifier"].casefold() == tenant.casefold():
                item["company"] = {**publisher, "identifier": tenant}
        items.append(item)
    # offset stays as the provider echoed it: _page compares it with the cursor.
    return {**payload, "content": items, "tenant": tenant, "country": country}


def replay_batch(checkpoint: Checkpoint, fixtures: dict[tuple[str, str, int], object],
                 *, max_pages: int = MAX_REQUESTS_PER_RUN,
                 max_per_employer: int = MAX_PAGES_PER_EMPLOYER) -> ReplayResult:
    """Consume only supplied synthetic pages under the existing 5/60 ceilings.

    Budget-limited cursors remain pending; errors block their tenant until
    separately reviewed. The returned checkpoint is an in-memory value,
    not a trusted production cursor. Every synthetic payload repeats the exact
    tenant and country, including empty pages. Listings are new identities only,
    including across countries and prior batches.
    """
    if type(fixtures) is not dict:
        raise ValueError("EXPANSION_FIXTURES_INVALID")
    return _replay(checkpoint, fixtures.__getitem__, max_pages, max_per_employer, None)


def live_replay_batch(checkpoint: Checkpoint, fetch: Callable[[str], bytes],
                      begin_employer: Callable[[str], None], *,
                      approved: frozenset[tuple[str, str]] | None = None,
                      max_pages: int = MAX_REQUESTS_PER_RUN,
                      max_per_employer: int = MAX_PAGES_PER_EMPLOYER) -> ReplayResult:
    """Read live country pages through an injected carrier (normally ``HttpCarrier``).

    Same validation, 5/60 ceilings, tenant isolation and checkpoint semantics as ``replay_batch``. Pairs outside
    ``approved`` (default ``LIVE_APPROVED_PAIRS``; per tenant a prefix of ``COUNTRIES``) are never requested and stay pending. A carrier that is
    tighter than the batch ceilings stops the batch without marking a pair failed.
    """
    approved = LIVE_APPROVED_PAIRS if approved is None else approved
    if type(approved) is not frozenset or not approved <= set(_SCOPE) or any(
            {c for t, c in approved if t == tenant} != set(COUNTRIES[:sum(1 for t, _ in approved if t == tenant)])
            for tenant in {t for t, _ in approved}):
        # A tenant's countries advance in COUNTRIES order (see validate_checkpoint), so its approved set is a prefix.
        raise ValueError("EXPANSION_LIVE_SCOPE_INVALID")

    def read(key: tuple[str, str, int]) -> object:
        begin_employer(key[0])
        return live_page(key[0], key[1], fetch(live_page_url(*key)))

    return _replay(checkpoint, read, max_pages, max_per_employer, approved)


def recheck_first_page(cursor: Cursor, fetch: Callable[[str], bytes],
                       begin_employer: Callable[[str], None]) -> dict[str, str]:
    """Re-read offset 0 of a completed pair once and report observable drift.

    A changed total or changed first-page identities shows the feed moved while it was being read. A stable
    result proves nothing about unobserved pages, so no caller may claim an atomic snapshot from it.
    """
    pair = {"tenant": cursor.tenant, "country": cursor.country}
    if cursor.status != "complete" or not cursor.total:
        return {**pair, "status": "not_applicable", "reason": "empty_or_incomplete"}
    try:
        begin_employer(cursor.tenant)
        again, page = _page(Cursor(cursor.tenant, cursor.country),
                            live_page(cursor.tenant, cursor.country,
                                      fetch(live_page_url(cursor.tenant, cursor.country, 0))))
    except (RequestBudgetError, PageLimitError):
        return {**pair, "status": "not_checked", "reason": "request_budget"}
    except (KeyError, DiscoveryError) as exc:
        code = _safe_page_error(exc)
        return {**pair, "status": "unreadable", "reason": "recheck_page_rejected" + code[len(_PAGE_ERROR):]}
    if again.total != cursor.total:
        return {**pair, "status": "drift", "reason": "total_changed"}
    if again.seen != cursor.seen[:len(again.seen)]:
        return {**pair, "status": "drift", "reason": "first_page_identities_changed"}
    return {**pair, "status": "stable", "reason": "first_page_and_total_unchanged"}


def _replay(checkpoint: Checkpoint, read: Callable[[tuple[str, str, int]], object],
            max_pages: int, max_per_employer: int,
            approved: frozenset[tuple[str, str]] | None) -> ReplayResult:
    validate_checkpoint(checkpoint)
    if (type(max_pages) is not int or not 0 < max_pages <= MAX_REQUESTS_PER_RUN
            or type(max_per_employer) is not int
            or not 0 < max_per_employer <= MAX_PAGES_PER_EMPLOYER):
        raise ValueError("EXPANSION_BUDGET_INVALID")
    cursors = list(checkpoint.cursors)
    blocked = {c.tenant for c in cursors if c.status == "error"}
    seen = {(c.tenant, x) for c in cursors for x in c.seen}
    counts: dict[str, int] = {}
    output = []
    pages = duplicates = 0
    exhausted = False
    for index, cursor in enumerate(cursors):
        while (cursor.status == "pending" and cursor.tenant not in blocked
               and (approved is None or (cursor.tenant, cursor.country) in approved)
               and pages < max_pages
               and counts.get(cursor.tenant, 0) < max_per_employer):
            pages += 1
            counts[cursor.tenant] = counts.get(cursor.tenant, 0) + 1
            key = (cursor.tenant, cursor.country, cursor.offset)
            try:
                next_cursor, page = _page(cursor, read(key))
            except (RequestBudgetError, PageLimitError):
                pages -= 1
                counts[cursor.tenant] -= 1
                exhausted = True
                break
            except (KeyError, DiscoveryError) as exc:
                cursor = replace(cursor, status="error", error=_safe_page_error(exc) if approved is not None else _PAGE_ERROR)
                blocked.add(cursor.tenant)
                break
            cursor = next_cursor
            for item in page:
                identity = (cursor.tenant, item["posting_id"])
                if identity in seen:
                    duplicates += 1
                else:
                    seen.add(identity)
                    output.append(item)
        cursors[index] = cursor
        if exhausted or pages >= max_pages:
            break
    return ReplayResult(Checkpoint(SCOPE_VERSION, tuple(cursors)),
                        tuple(output), pages, duplicates)
