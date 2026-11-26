"""W3.5 research-backed, offline-only country replay.

No network, filesystem, queue or submission integration. Existing live pilot
gate and transport remain unchanged. Research counts are historical evidence,
never a filter or a claim of current completeness.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ejs.services.discovery import (
    AtsClass, BadPayloadError, DiscoveryError, PAGE_SIZE,
    MAX_PAGES_PER_EMPLOYER, parse_smartrecruiters,
)
from ejs.services.http_carrier import MAX_REQUESTS_PER_RUN

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
                or c.error != (_PAGE_ERROR if c.status == "error" else None)
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


def replay_batch(checkpoint: Checkpoint, fixtures: dict[tuple[str, str, int], dict],
                 *, max_pages: int = MAX_REQUESTS_PER_RUN,
                 max_per_employer: int = MAX_PAGES_PER_EMPLOYER) -> ReplayResult:
    """Consume only supplied synthetic pages under the existing 5/60 ceilings.

    Budget-limited cursors remain pending; errors block their tenant until
    separately reviewed. The returned checkpoint is an in-memory value,
    not a trusted production cursor. Every synthetic payload repeats the exact
    tenant and country, including empty pages. Listings are new identities only,
    including across countries and prior batches.
    """
    validate_checkpoint(checkpoint)
    if type(fixtures) is not dict:
        raise ValueError("EXPANSION_FIXTURES_INVALID")
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
    for index, cursor in enumerate(cursors):
        while (cursor.status == "pending" and cursor.tenant not in blocked
               and pages < max_pages
               and counts.get(cursor.tenant, 0) < max_per_employer):
            pages += 1
            counts[cursor.tenant] = counts.get(cursor.tenant, 0) + 1
            key = (cursor.tenant, cursor.country, cursor.offset)
            try:
                next_cursor, page = _page(cursor, fixtures[key])
            except (KeyError, DiscoveryError):
                cursor = replace(cursor, status="error", error=_PAGE_ERROR)
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
        if pages >= max_pages:
            break
    return ReplayResult(Checkpoint(SCOPE_VERSION, tuple(cursors)),
                        tuple(output), pages, duplicates)
