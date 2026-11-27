"""FUNCTION-05: vacancy detail verification for a bounded, owner-approved shortlist.

Reuses, unchanged: the Function 04 report/evidence/scoring (``suitability``, ``suitability_evidence``), the W2
detail observation (``application_bridge.observe_posting_detail``: identity, requisition, availability) and the
read-only ``http_carrier`` (HTTPS, GET only, no redirects, no retries, >= 1 s interval, request caps).

What this module adds:

* ``build_allowlist``: picks ``preliminary_high`` rows in Function 04's explicit ``priority_rank`` order (never a
  re-sort by score), binds each to ATS, tenant, posting id and canonical URL, and emits a reviewable allowlist.
  The allowlist has a digest; a live run needs the owner to quote that digest.
* ``verify_allowlist``: recomputes the allowlist from the Function 04 report and refuses any difference.
* ``request_details``: one GET per allowlisted URL through the injected carrier, nothing else.
* ``verify_details``: identity/availability/requisition checks, section extraction (verbatim only), Function 04
  re-evaluation with the same approved criteria, and a verdict per candidate:
  ``candidate_for_review`` | ``incomplete_evidence`` | ``confirmed_incompatible``. Nothing is ever "suitable":
  mandatory qualifications are not checked against verified candidate facts here (a Function 06 gate).
* ``reserve_budget``: durable single-use request budget keyed by allowlist digest and by each detail URL.
* ``build_handoff``: prioritized Function 06 hand-off. Non-executable: no authority of any kind.

No candidate qualification and no employer requirement is inferred. Unknown stays Unknown.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ejs.services.application_bridge import observe_posting_detail
from ejs.services.criteria_loader import Criteria
from ejs.services.discovery import DiscoveryError
from ejs.services.execution_manifest import validate_apply_url
from ejs.services.http_carrier import HttpCarrier
from ejs.services.suitability import (AUTHORITY as F04_AUTHORITY, ROW_FIELDS, SCHEMA as F04_SCHEMA, evaluate_report,
                                      canon_country, canonical_sha256)
from ejs.services import language_policy as lp
from ejs.services.suitability_evidence import (VacancyDetail, detail_from_smartrecruiters, extract_detail_evidence,
                                               html_to_text)

SCHEMA_ALLOWLIST = "EJS-DETAIL-ALLOWLIST-1"
SCHEMA_REPORT = "EJS-DETAIL-VERIFICATION-1"
SCHEMA_HANDOFF = "EJS-F06-HANDOFF-1"
POLICY_VERSION = "DETAIL-VERIFICATION-1.0"
MAX_PILOT_REQUESTS = 5
SELECT_CLASSIFICATION = "preliminary_high"
MEDIUM_CLASSIFICATION = "preliminary_medium"
HIGH_SELECTION_RULE = "preliminary_high_in_f04_priority_rank_order"
MEDIUM_SELECTION_RULE = "preliminary_medium_in_f04_priority_rank_order"
DETAIL_HOST = "api.smartrecruiters.com"
APPROVED_CRITERIA_STATUS = "approved_digest_and_rule_version_match"
MAX_SECTION_LINES = 40
MAX_LINE = 240

# Nothing in Function 05 may execute, submit, open a browser, upload a CV or start Function 06.
AUTHORITY = {"execute": False, "submit": False, "form_access": False, "browser": False, "cv_upload": False,
             "function06_started": False, "label_is_not_authority": True}
HANDOFF_PREREQUISITES = (
    "owner_review_of_this_handoff", "mandatory_qualifications_checked_against_verified_candidate_facts", "fresh_employer_and_mandatory_eligibility_review",
    "verified_candidate_facts_and_supported_answer_provenance", "observed_application_form",
    "execution_manifest_validation_and_independent_review", "separate_function_06_authorization")

_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
_MANDATORY = re.compile(r"\b(?:must|required|requires?|mandatory|essential)\b", re.I)
_OPTIONAL = re.compile(r"\b(?:plus|advantage|preferred|preferably|ideally|nice to have|desirable|bonus|optional|"
                       r"not required|not mandatory|beneficial|an asset)\b", re.I)
WATCHLIST_STATUS = lp.WATCHLIST
_STATUS_ORDER = {"candidate_for_review": 0, "incomplete_evidence": 1, "confirmed_incompatible": 2,
                 "policy_excluded": 3, WATCHLIST_STATUS: 4}
_BASE_STATUSES = ("candidate_for_review", "incomplete_evidence", "confirmed_incompatible")
LANGUAGE_BLOCKER = "confirmed:blocker:mandatory-language-unmet"
_CONFIRMED_CLASSES = frozenset({"verified_blocked", "verified_below_threshold"})
_ALLOWLIST_KEYS = frozenset({"schema", "mode", "executable", "submit_authorized", "authority", "request_policy",
                             "source", "selection", "targets", "approval"})


_HEX64 = re.compile(r"[0-9a-f]{64}")
LIVE_ORIGIN, REPLAY_ORIGIN = "live_employer_response", "offline_replay_synthetic"
DERIVED_ORIGIN = "derived_offline_analysis_of_live_evidence"


class DetailVerificationError(ValueError):
    """Fail-closed refusal: nothing is requested or evaluated beyond this point."""


def detail_url(tenant: str, posting_id: str) -> str:
    """The one endpoint Function 05 may read (same URL the W2 bridge observes)."""
    return f"https://{DETAIL_HOST}/v1/companies/{tenant}/postings/{posting_id}"


def allowlist_digest(doc: Mapping[str, Any]) -> str:
    """Digest the owner approves: the whole allowlist except its own ``approval`` block."""
    return canonical_sha256({k: v for k, v in doc.items() if k != "approval"})


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------------------- selection
def validate_f04_report(report: Any) -> list[dict[str, Any]]:
    """Only a Function 04 report with approved criteria and an unbroken priority order is accepted."""
    if not isinstance(report, dict) or report.get("schema") != F04_SCHEMA:
        raise DetailVerificationError("F04_REPORT_SCHEMA")
    if (report.get("executable") is not False or report.get("submit_authorized") is not False
            or report.get("authority") != F04_AUTHORITY):
        raise DetailVerificationError("F04_REPORT_AUTHORITY")
    criteria = report.get("criteria")
    if (not isinstance(criteria, dict) or criteria.get("approval") != APPROVED_CRITERIA_STATUS
            or criteria.get("approved_by_owner") is not True or report.get("handoff_ready") is not True):
        raise DetailVerificationError("F04_CRITERIA_NOT_APPROVED")
    rows = report.get("vacancies")
    if not isinstance(rows, list) or report.get("listings_kept") != len(rows):
        raise DetailVerificationError("F04_REPORT_COUNT")
    for rank, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ROW_FIELDS)
                or row.get("posting_id") is not None and not isinstance(row["posting_id"], str)
                or not isinstance(row.get("suitability"), dict)
                or not isinstance(row["suitability"].get("classification"), str)):
            raise DetailVerificationError("F04_REPORT_ROW")
        if row.get("priority_rank") != rank:
            raise DetailVerificationError("F04_PRIORITY_ORDER_BROKEN")
    return rows


def _bind(row: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    """Exact ATS, region, tenant, posting id and canonical URL, or a refusal reason."""
    tenant, posting, url = row["tenant"], row.get("posting_id") or "", row["url"]
    if row["ats_kind"] != "smartrecruiters":
        raise DetailVerificationError("unsupported_ats")
    if row["region"] != "global":
        raise DetailVerificationError("unsupported_region")
    if not _TENANT.fullmatch(tenant) or not posting.isdecimal() or not posting.isascii():
        raise DetailVerificationError("identity_incomplete")
    try:
        validate_apply_url(url, "smartrecruiters", posting)
    except (ValueError, PermissionError):
        raise DetailVerificationError("canonical_url_invalid") from None
    if urlsplit(url).path.split("/")[1] != tenant:
        raise DetailVerificationError("canonical_url_tenant_mismatch")
    return {"sequence": sequence, "f04_priority_rank": row["priority_rank"], "ats": "smartrecruiters",
            "region": "global", "tenant": tenant, "posting_id": posting, "canonical_url": url,
            "detail_url": detail_url(tenant, posting), "company": row["company"], "role": row["role"],
            "location": row["location"]}


def _lineage_problem(lineage: Any, f04_report: Mapping[str, Any]) -> bool:
    return (not isinstance(lineage, dict) or set(lineage) != {"f02_source_file_sha256", "f02_projection_sha256"}
            or any(not isinstance(v, str) or not _HEX64.fullmatch(v) for v in lineage.values())
            or lineage["f02_projection_sha256"] != f04_report.get("source_report_sha256"))


def build_allowlist(f04_report: Any, *, f04_report_sha256: str, lineage: Mapping[str, str],
                    limit: int = MAX_PILOT_REQUESTS, medium_offset: int | None = None,
                    pilot_countries: list[str] | None = None) -> dict[str, Any]:
    """Explicit, reviewable detail-request allowlist. Pure; no network.

    Selection follows Function 04's ``priority_rank`` (the explicit priority), not a score sort: the first
    ``limit`` ``preliminary_high`` rows by default, or an explicit bounded window of ``preliminary_medium``
    rows when medium_offset is supplied. An unbindable row is reported in ``selection.unbound`` and is never
    replaced by a row outside the chosen window. The legacy high selection document remains unchanged. ``lineage`` pins the verified original
    Function 02 data the Function 04 report was produced from (see ``check_lineage``).
    """
    rows = validate_f04_report(f04_report)
    if _lineage_problem(lineage, f04_report):
        raise DetailVerificationError("LINEAGE_NOT_BOUND_TO_F04_REPORT")
    if type(limit) is not int or not 0 < limit <= MAX_PILOT_REQUESTS:
        raise DetailVerificationError("LIMIT_OUT_OF_RANGE")
    if not isinstance(f04_report_sha256, str) or not _HEX64.fullmatch(f04_report_sha256):
        raise DetailVerificationError("F04_REPORT_SHA256")
    if medium_offset is not None and (type(medium_offset) is not int or medium_offset < 0 or medium_offset > len(rows)):
        raise DetailVerificationError("MEDIUM_OFFSET_OUT_OF_RANGE")
    classification = SELECT_CLASSIFICATION if medium_offset is None else MEDIUM_CLASSIFICATION
    eligible = [r for r in rows if r["suitability"]["classification"] == classification]
    # Explicit pilot scope is digest-bound; unknown or mixed provenance
    # outside it skips this pilot, not the listing itself.
    total_classified = len(eligible)
    if pilot_countries is not None:
        if (not isinstance(pilot_countries, list) or not pilot_countries
                or any(not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code)
                       or canon_country(code) != code for code in pilot_countries)
                or len(set(pilot_countries)) != len(pilot_countries)):
            raise DetailVerificationError("PILOT_COUNTRIES_INVALID")
        pilot_countries = sorted(pilot_countries)
        allowed = set(pilot_countries)
        scoped = []
        for row in eligible:
            dims = row["suitability"].get("dimensions")
            geo = dims.get("geography") if isinstance(dims, dict) else None
            countries = geo.get("countries") if isinstance(geo, dict) else None
            evidence = row.get("discovery_evidence")
            raw = evidence.get("countries") if isinstance(evidence, dict) else None
            if (f04_report.get("source_projection") != "verified_live_country_feed"
                    or not isinstance(evidence, dict) or evidence.get("observation") != "live_country_feed"
                    or not isinstance(raw, list) or not raw
                    or any(type(code) is not str or code not in allowed for code in raw)
                    or len(set(raw)) != len(raw)
                    or not isinstance(countries, list) or countries != sorted(raw)
                    or not isinstance(geo, dict) or geo.get("status") not in ("tier_1", "other")
                    or not _TENANT.fullmatch(row["tenant"])):
                continue
            expected_feed = (f"https://{DETAIL_HOST}/v1/companies/{row['tenant']}/postings"
                             f"?country={raw[0].lower()}&offset=0&limit=100")
            if evidence.get("feed_url") == expected_feed:
                scoped.append(row)
        eligible = scoped
    if medium_offset is not None and medium_offset >= len(eligible):
        raise DetailVerificationError("MEDIUM_OFFSET_OUT_OF_RANGE")
    start = 0 if medium_offset is None else medium_offset
    targets: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    seen_identity: set[tuple] = set()
    for row in eligible[start:start + limit]:
        try:
            target = _bind(row, len(targets) + 1)
        except DetailVerificationError as exc:
            unbound.append({"f04_priority_rank": row["priority_rank"], "tenant": row["tenant"],
                            "posting_id": row.get("posting_id"), "reason": str(exc)})
            continue
        identity = (target["ats"], target["region"], target["tenant"], target["posting_id"])
        if identity in seen_identity:
            raise DetailVerificationError("DUPLICATE_TARGET")
        seen_identity.add(identity)
        targets.append(target)
    doc = {
        "schema": SCHEMA_ALLOWLIST, "mode": "detail_read_only_allowlist", "executable": False,
        "submit_authorized": False, "authority": dict(AUTHORITY),
        "request_policy": {"method": "GET", "scheme": "https", "host": DETAIL_HOST, "max_requests": len(targets),
                           "min_interval_s": 1.0, "redirects": "refused", "retries": 0, "authentication": "none",
                           "browser": False, "form_access": False, "each_url_at_most_once": True,
                           "single_use_budget": True},
        "source": {"f04_schema": F04_SCHEMA, "f04_report_sha256": f04_report_sha256,
                   "rule_version": f04_report.get("rule_version"),
                   "policy_version": f04_report.get("policy_version"),
                   "criteria_sha256": f04_report["criteria"].get("sha256"), "lineage": dict(lineage)},
        "selection": {"rule": HIGH_SELECTION_RULE if medium_offset is None else MEDIUM_SELECTION_RULE, "limit": limit,
                      **({"high_rows_in_report": len(eligible)} if medium_offset is None else
                         {"medium_rows_in_report": len(eligible), "medium_offset": medium_offset}),
                      **({"pilot_countries": pilot_countries, "scope_excluded": total_classified - len(eligible)}
                         if pilot_countries is not None else {}),
                      "selected": len(targets), "unbound": unbound},
        "targets": targets}
    doc["approval"] = {"status": "awaiting_owner_approval", "allowlist_sha256": allowlist_digest(doc),
                       "note": "Quote allowlist_sha256 to approve exactly these requests. Nothing is approved yet."}
    return doc


def verify_allowlist(doc: Any, f04_report: Any, *, f04_report_sha256: str, lineage: Mapping[str, str]) -> dict[str, Any]:
    """The allowlist must equal what Function 04's report yields. Extra, altered or reordered targets are refused."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_ALLOWLIST or set(doc) != _ALLOWLIST_KEYS:
        raise DetailVerificationError("ALLOWLIST_SHAPE")
    targets = doc.get("targets")
    selection = doc.get("selection")
    if (not isinstance(targets, list) or not isinstance(selection, dict) or len(targets) > MAX_PILOT_REQUESTS
            or type(selection.get("limit")) is not int):
        raise DetailVerificationError("ALLOWLIST_SHAPE")
    rule = selection.get("rule")
    if rule == HIGH_SELECTION_RULE:
        medium_offset = None
    elif rule == MEDIUM_SELECTION_RULE and type(selection.get("medium_offset")) is int:
        medium_offset = selection["medium_offset"]
    else:
        raise DetailVerificationError("ALLOWLIST_SELECTION_RULE")
    expected = build_allowlist(f04_report, f04_report_sha256=f04_report_sha256, lineage=lineage,
                               limit=selection["limit"], medium_offset=medium_offset,
                               pilot_countries=selection.get("pilot_countries"))
    if allowlist_digest(doc) != allowlist_digest(expected):
        raise DetailVerificationError("ALLOWLIST_NOT_REPRODUCED_FROM_F04_REPORT")
    return expected


def check_lineage(f04_report: Mapping[str, Any], projection: Any, criteria: Criteria, *, criteria_sha256: str,
                  expected_criteria_sha256: str, expected_rule_version: str, f02_source_sha256: str) -> dict[str, str]:
    """Anchor the Function 04 report to the verified ORIGINAL Function 02 data, not to its own rows.

    ``projection`` is the Function 02 report the owner pinned by digest (``f02_source_sha256`` is the digest of
    its exact bytes, checked by the caller against the independently recorded value). Re-running Function 04 on it
    with the approved criteria must reproduce the given report exactly, including ``source_report_sha256``.
    """
    again = evaluate_report(projection, criteria, criteria_sha256=criteria_sha256,
                            expected_criteria_sha256=expected_criteria_sha256,
                            expected_rule_version=expected_rule_version)
    if again["criteria"]["approval"] != APPROVED_CRITERIA_STATUS:
        raise DetailVerificationError("CRITERIA_NOT_APPROVED")
    if not isinstance(f02_source_sha256, str) or not _HEX64.fullmatch(f02_source_sha256):
        raise DetailVerificationError("F02_SOURCE_SHA256")
    if f04_report.get("source_report_sha256") != again["source_report_sha256"]:
        raise DetailVerificationError("F04_REPORT_NOT_FROM_PINNED_F02_DATA")
    if json.dumps(f04_report, sort_keys=True) != json.dumps(again, sort_keys=True):
        raise DetailVerificationError("F04_REPORT_NOT_REPRODUCIBLE")
    return {"f02_source_file_sha256": f02_source_sha256, "f02_projection_sha256": again["source_report_sha256"]}


# ------------------------------------------------------------------------------- durable single-use budget
def _create_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def reserve_budget(budget_dir: Path, allowlist: Mapping[str, Any], *, now: Callable[[], datetime],
                   out_dir: Path) -> Path:
    """Publish the single-use request reservation BEFORE any request. Never released automatically.

    One file per allowlist digest and one per detail URL, each created exclusively (atomic across processes and
    output directories). If any already exists the budget is consumed (by a finished, interrupted or uncertain run)
    and nothing is requested. Files this call created are removed only on that refusal, before any request.
    A later crash or interruption leaves them in place: the attempt counts as consumed and is never retried
    automatically. Only the owner may clear them, after reading the attempt journal.
    """
    digest = allowlist_digest(allowlist)
    names = [f"allowlist-{digest}.json"] + [f"url-{sha256(t['detail_url'].encode()).hexdigest()}.json"
                                            for t in allowlist["targets"]]
    record = (json.dumps({"schema": "EJS-DETAIL-BUDGET-1", "allowlist_sha256": digest,
                          "requests_reserved": len(allowlist["targets"]), "reserved_at": _iso(now()),
                          "pid": os.getpid(), "out_dir": str(out_dir),
                          "state": "reserved_single_use_not_released_automatically"},
                         sort_keys=True, indent=1) + "\n").encode()
    created: list[Path] = []
    try:
        for name in names:
            _create_exclusive(budget_dir / name, record)
            created.append(budget_dir / name)
    except FileExistsError:
        for path in created:
            path.unlink()
        raise DetailVerificationError("BUDGET_ALREADY_CONSUMED") from None
    fd = os.open(budget_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return budget_dir / names[0]


# ------------------------------------------------------------------------------------------------ requests
def request_details(allowlist: Mapping[str, Any], carrier: HttpCarrier, *, now: Callable[[], datetime],
                    on_start: Callable[[dict], None] | None = None,
                    on_finish: Callable[[dict, bytes | None], None] | None = None) -> list[dict[str, Any]]:
    """One GET per allowlisted URL, in allowlist order, through the carrier. Never anything else.

    ``on_start`` runs (and must succeed) before each request, ``on_finish`` right after the response arrives and
    before it is analyzed, so an interruption leaves a started-but-unfinished record (an uncertain attempt).
    A failing employer stops (carrier rule). A response that is unreadable, oversized, or not bound to the
    allowlisted identity also stops that employer at once; its remaining targets are recorded as not attempted.
    """
    targets = allowlist["targets"]
    if len(targets) > MAX_PILOT_REQUESTS:
        raise DetailVerificationError("TOO_MANY_TARGETS")
    approved = {t["detail_url"] for t in targets}
    if len(approved) != len(targets):
        raise DetailVerificationError("DUPLICATE_TARGET")
    requested: set[str] = set()
    halted: set[str] = set()
    observations: list[dict[str, Any]] = []
    for target in targets:
        box: dict[str, bytes] = {}

        def guarded(url: str, target=target, box=box) -> bytes:
            if url != target["detail_url"] or url not in approved or url in requested:
                raise DetailVerificationError("REQUEST_NOT_ALLOWLISTED_OR_DUPLICATE")
            requested.add(url)
            box["raw"] = carrier(url)
            return box["raw"]

        w2: dict[str, Any] = {"tenant": target["tenant"], "posting_id": target["posting_id"]}
        obs: dict[str, Any] = {"target": target, "outcome": "ok", "http_status": None, "attempted": 0,
                               "requested_at": None, "observed_at": None, "duration_ms": None,
                               "response_bytes": None, "response_sha256": None, "raw": None, "w2": w2}
        if target["tenant"] in halted:
            obs["outcome"] = "employer_stopped_invalid_response"
            obs["observed_at"] = _iso(now())
            obs["facts"] = analyze(obs)
            observations.append(obs)
            continue
        carrier.begin_employer(target["tenant"])
        before_total, before_log = carrier.total, len(carrier.log)
        started = now()
        obs["requested_at"] = _iso(started)
        if on_start:
            on_start({"event": "attempt_started", "sequence": target["sequence"], "detail_url": target["detail_url"],
                      "requested_at": obs["requested_at"]})
        try:
            observe_posting_detail(w2, guarded)
        except DetailVerificationError:
            raise
        except DiscoveryError as exc:
            obs["outcome"] = exc.kind
        except Exception as exc:  # noqa: BLE001 - recorded, never retried
            obs["outcome"] = f"error_{type(exc).__name__}"
        finished = now()
        obs["attempted"] = carrier.total - before_total
        if obs["attempted"] and len(carrier.log) > before_log:
            entry = carrier.log[-1]
            obs["http_status"], obs["response_bytes"] = entry["status"], entry["bytes"]
        obs["raw"] = box.get("raw")
        if obs["raw"] is not None:
            obs["response_sha256"] = sha256(obs["raw"]).hexdigest()
        obs["observed_at"] = _iso(finished)
        obs["duration_ms"] = max(0, int((finished - started).total_seconds() * 1000))
        if on_finish:
            on_finish({"event": "attempt_finished", "sequence": target["sequence"],
                       "detail_url": target["detail_url"], "outcome": obs["outcome"],
                       "http_status": obs["http_status"], "response_bytes": obs["response_bytes"],
                       "response_sha256": obs["response_sha256"], "requested_at": obs["requested_at"],
                       "completed_at": obs["observed_at"], "duration_ms": obs["duration_ms"]}, obs["raw"])
        obs["facts"] = analyze(obs)
        if obs["raw"] is not None and obs["facts"]["identity"] != "bound":
            halted.add(target["tenant"])                    # invalid, unreadable or unbound: no further requests
        observations.append(obs)
    logged = [e["url"] for e in carrier.log if e["url"]]
    if len(set(logged)) != len(logged) or not set(logged) <= approved:
        raise DetailVerificationError("UNAPPROVED_OR_DUPLICATE_REQUEST_LOGGED")
    return observations


def observations_from_saved(allowlist: Mapping[str, Any], journal: list[Mapping[str, Any]],
                            read_raw: Callable[[str], bytes]) -> list[dict[str, Any]]:
    """Rebuild observations from previously collected live evidence. NO request: the saved body is replayed to the
    W2 observation through a closure and its SHA-256 must equal the one the fsynced journal recorded at receipt."""
    finished = {e["detail_url"]: e for e in journal if e.get("event") == "attempt_finished"}
    observations = []
    for target in allowlist["targets"]:
        e = finished.get(target["detail_url"])
        if e is None:
            raise DetailVerificationError("SAVED_ATTEMPT_MISSING")
        raw = read_raw(e["stored_as"]) if e.get("stored_as") else None
        if raw is not None and sha256(raw).hexdigest() != e["response_sha256"]:
            raise DetailVerificationError("SAVED_EVIDENCE_HASH_MISMATCH")
        w2: dict[str, Any] = {"tenant": target["tenant"], "posting_id": target["posting_id"]}
        if raw is not None:
            def replay(url: str, raw=raw, target=target) -> bytes:
                if url != target["detail_url"]:
                    raise DetailVerificationError("REQUEST_NOT_ALLOWLISTED_OR_DUPLICATE")
                return raw
            observe_posting_detail(w2, replay)
        obs = {"target": target, "outcome": e["outcome"], "http_status": e["http_status"], "attempted": 1,
               "requested_at": e["requested_at"], "observed_at": e["completed_at"], "duration_ms": e["duration_ms"],
               "response_bytes": e["response_bytes"], "response_sha256": e["response_sha256"], "raw": raw, "w2": w2}
        obs["facts"] = analyze(obs)
        observations.append(obs)
    return observations


# ----------------------------------------------------------------------------------------- extraction
def extract_sections(data: Mapping[str, Any]) -> dict[str, Any]:
    """Employer wording only. ``jobDescription`` and ``qualifications`` lines verbatim; mandatory lines are those
    with an explicit must/required/mandatory cue and no optional wording. Nothing is paraphrased or inferred."""
    ad = data.get("jobAd")
    sections = ad.get("sections") if isinstance(ad, dict) else None
    out: dict[str, Any] = {"job_description_lines": [], "qualification_lines": [],
                           "mandatory_qualification_lines": [], "truncated": False}
    if not isinstance(sections, dict):
        return out
    for name, key in (("jobDescription", "job_description_lines"), ("qualifications", "qualification_lines")):
        section = sections.get(name)
        html = section.get("text") if isinstance(section, dict) else None
        if not isinstance(html, str):
            continue
        lines = [" ".join(line.split()) for line in html_to_text(html).split("\n") if line.strip()]
        if len(lines) > MAX_SECTION_LINES or any(len(line) > MAX_LINE for line in lines):
            out["truncated"] = True
        out[key] = [line[:MAX_LINE] for line in lines[:MAX_SECTION_LINES]]
    out["mandatory_qualification_lines"] = [line for line in out["qualification_lines"]
                                            if _MANDATORY.search(line) and not _OPTIONAL.search(line)]
    return out


def _posting_url_problem(data: Mapping[str, Any], tenant: str, posting_id: str) -> str | None:
    """``postingUrl`` is optional; when present it must be the same tenant and posting."""
    value = data.get("postingUrl")
    if value is None:
        return None
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        segments = parts.path.split("/") if parts else []
    except ValueError:
        return "posting_url_mismatch"
    if (not parts or parts.scheme != "https" or parts.hostname != "jobs.smartrecruiters.com" or len(segments) < 3
            or segments[1] != tenant or segments[2].split("-", 1)[0] != posting_id):
        return "posting_url_mismatch"
    return None


def analyze(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Identity, availability, requisition and sections for one observation."""
    target, raw, w2 = observation["target"], observation["raw"], observation["w2"]
    result: dict[str, Any] = {"identity": "not_observed", "identity_reasons": [], "availability": "unknown",
                              "requisition_id": None, "sections": None, "vacancy_detail": None,
                              "w2_eligibility_conditions": []}
    if raw is None:
        return result
    try:
        vacancy = detail_from_smartrecruiters(raw, target["tenant"], target["posting_id"])
        data = json.loads(raw)
    except ValueError as exc:
        reason = str(exc) if str(exc) in ("detail_size", "detail_identity") else "detail_unreadable"
        result.update(identity="mismatch" if reason == "detail_identity" else "unreadable",
                      identity_reasons=[reason])
        return result
    except RecursionError:
        result.update(identity="unreadable", identity_reasons=["detail_unreadable"])
        return result
    problem = _posting_url_problem(data, target["tenant"], target["posting_id"])
    if problem:
        result.update(identity="mismatch", identity_reasons=[problem])
        return result
    result.update(identity="bound", vacancy_detail=vacancy, sections=extract_sections(data),
                  availability={"active_posting": "active", "closed": "inactive"}.get(
                      w2.get("application_availability"), "unknown"),
                  requisition_id=w2.get("requisition_id"),
                  w2_eligibility_conditions=list(w2.get("eligibility_conditions") or []))
    return result


# ------------------------------------------------------------------------------------------- verdict
def _verdict(observation: Mapping[str, Any], facts: Mapping[str, Any], entry: Mapping[str, Any] | None,
             policy: Mapping[str, str] | None = None) -> dict:
    reasons: list[str] = []
    if observation["raw"] is None:
        out = observation["outcome"]
        return {"status": "incomplete_evidence", "reasons": [f"detail_not_obtained:{out}"]}
    if facts["identity"] != "bound" or entry is None:
        return {"status": "incomplete_evidence", "reasons": [f"identity_{facts['identity']}:{r}"
                                                             for r in facts["identity_reasons"]] or ["identity_unbound"]}
    s = entry["suitability"]
    # Exclusions come only from approved K2 hard blockers stated in the posting, a closed posting, or the approved
    # engine's own verified_blocked / verified_below_threshold classes. Compensation below target is scored by the
    # engine, not a blocker. Contradictory employer statements never become a confirmed exclusion.
    contradictory = "contradiction" in s["reason_codes"] or s["reason_code"] == "contradiction"
    confirmed = [c for c in s["confirmed_incompatibilities"]
                 if c != "confirmed:compensation_below_target" and not (contradictory and c.startswith("confirmed:blocker:"))]
    action = policy["action"] if policy else "evaluate"
    if action == "watchlist":
        # Sponsored foreign-language posting: kept apart from the application queue. A language blocker does not
        # exclude it (the working languages are recorded); any other confirmed exclusion still does.
        rest = [c for c in confirmed if c != LANGUAGE_BLOCKER]
        if rest:
            return {"status": "confirmed_incompatible", "reasons": rest}
        return {"status": WATCHLIST_STATUS, "reasons": [f"language_policy:{policy['reason_code']}"],
                "policy": dict(policy)}
    if confirmed or (not contradictory and s["classification"] in _CONFIRMED_CLASSES):
        out = {"status": "confirmed_incompatible", "reasons": confirmed or [s["classification"]]}
        if action == "policy_exclusion":
            out["policy_reasons"] = [f"language_policy:{policy['reason_code']}"]     # both apply; kept distinguishable
        return out
    if action == "policy_exclusion":
        return {"status": "policy_excluded", "reasons": [f"language_policy:{policy['reason_code']}"],
                "policy": dict(policy)}
    if action == "review":
        reasons.append(f"language_policy:{policy['reason_code']}")
    if contradictory:
        reasons.append("contradictory_evidence_requires_review")
    if "confirmed:compensation_below_target" in s["confirmed_incompatibilities"]:
        reasons.append("compensation_below_target_scored_not_a_blocker")
    if facts["availability"] != "active":
        reasons.append("availability_unverified")
    if not facts["requisition_id"]:
        reasons.append("requisition_id_unverified")
    if facts["w2_eligibility_conditions"]:
        reasons.append("w2_eligibility_condition_requires_review")
    if s["classification"] != "verified_apply_candidate":
        reasons.append(f"f04_{s['classification']}")
    reasons += [f"review_flag:{f}" for f in s["review_flags"]]
    notes = [r for r in reasons if r == "compensation_below_target_scored_not_a_blocker"]
    reasons = [r for r in reasons if r not in notes]
    if reasons:
        return {"status": "incomplete_evidence", "reasons": reasons + notes}
    return {"status": "candidate_for_review", "notes": notes,
            "reasons": ["f04_verified_apply_candidate_with_bound_active_requisition",
                        "mandatory_qualifications_not_checked_against_verified_candidate_facts"]}


def verify_details(projection: Any, observations: list[dict[str, Any]], criteria: Criteria, *, criteria_sha256: str,
                   expected_criteria_sha256: str, expected_rule_version: str) -> dict[str, Any]:
    """Re-evaluate the verified Function 02 projection with the approved criteria and the identity-bound details."""
    facts = [o["facts"] for o in observations]
    details: dict[tuple[str, str], VacancyDetail] = {
        (o["target"]["tenant"], o["target"]["posting_id"]): f["vacancy_detail"]
        for o, f in zip(observations, facts) if f["vacancy_detail"] is not None}
    result = evaluate_report(projection, criteria, details=details,
                             criteria_sha256=criteria_sha256, expected_criteria_sha256=expected_criteria_sha256,
                             expected_rule_version=expected_rule_version)
    if result["criteria"]["approval"] != APPROVED_CRITERIA_STATUS or result["details_rejected"]:
        raise DetailVerificationError("REEVALUATION_REFUSED")
    by_identity = {(r["tenant"], r.get("posting_id")): r for r in result["vacancies"]}
    countries = {(r["tenant"], r.get("posting_id")): sorted({c for c in ((r.get("discovery_evidence") or {}).get("countries") or [])
                                                             if isinstance(c, str)})
                 for r in projection["vacancies"]}
    candidates, watchlist, history = [], [], []
    for obs, fact in zip(observations, facts):
        target = obs["target"]
        entry = by_identity.get((target["tenant"], target["posting_id"]))
        bound = fact["identity"] == "bound"
        evidence = extract_detail_evidence(fact["vacancy_detail"]) if bound else None
        posting = _posting_evidence(evidence, fact["vacancy_detail"]) if bound else None
        policy = lp.decide(posting["advertisement_language"]["status"], posting["sponsorship"]["status"]) if bound else None
        verdict = _verdict(obs, fact, entry if bound else None, policy)
        suit = entry["suitability"] if entry is not None and bound else None
        identity = lp.canonical_identity(target["ats"], target["region"], target["tenant"], target["posting_id"])
        base = {"identity": identity, "employer": target["company"], "position": target["role"],
                "source_url": target["canonical_url"], "observed_at": obs["observed_at"],
                "evidence_sha256": obs["response_sha256"], "policy_version": lp.POLICY_VERSION}
        if verdict["status"] == lp.WATCHLIST:
            watchlist.append({**base, "country": countries.get((target["tenant"], target["posting_id"]), []),
                              "advertisement_language": posting["advertisement_language"],
                              "mandatory_working_languages": posting["mandatory_working_languages"],
                              "sponsorship_evidence": posting["sponsorship"]})
        ad_status = posting["advertisement_language"]["status"] if posting else None
        if verdict["status"] == "confirmed_incompatible":
            history.append({**base, "kind": "confirmed_incompatibility", "reasons": list(verdict["reasons"]),
                            "advertisement_language_status": ad_status})
        if verdict["status"] == "policy_excluded" or verdict.get("policy_reasons"):
            history.append({**base, "kind": "policy_exclusion",       # never merged into candidate incompatibilities
                            "reasons": list(verdict["reasons"]) if verdict["status"] == "policy_excluded"
                            else list(verdict["policy_reasons"]), "advertisement_language_status": ad_status})
        candidates.append({
            "sequence": target["sequence"], "f04_priority_rank": target["f04_priority_rank"],
            "reevaluated_priority_rank": entry["priority_rank"] if suit else None,
            "ats": target["ats"], "region": target["region"], "tenant": target["tenant"],
            "posting_id": target["posting_id"], "canonical_url": target["canonical_url"],
            "detail_url": target["detail_url"], "company": target["company"], "role": target["role"],
            "location": target["location"],
            "request": {"outcome": obs["outcome"], "http_status": obs["http_status"], "attempted": obs["attempted"],
                        "requested_at": obs["requested_at"], "observed_at": obs["observed_at"],
                        "duration_ms": obs["duration_ms"], "response_bytes": obs["response_bytes"],
                        "response_sha256": obs["response_sha256"]},
            "evidence": {"raw_sha256": obs["response_sha256"],
                         "raw_bytes": len(obs["raw"]) if obs["raw"] is not None else None},
            "identity": {"status": fact["identity"], "reasons": fact["identity_reasons"]},
            "availability": fact["availability"], "requisition_id": fact["requisition_id"],
            "w2_eligibility_conditions": fact["w2_eligibility_conditions"],
            "sections": fact["sections"], "posting_evidence": posting, "language_policy": policy,
            "reevaluation": _compact(suit), "verdict": verdict})
    counts = Counter(c["verdict"]["status"] for c in candidates)
    return {"candidates": candidates, "watchlist_records": watchlist, "history_records": history,
            "verdict_counts": {k: counts.get(k, 0) for k in _STATUS_ORDER if k in _BASE_STATUSES or counts.get(k)},
            "reevaluation_summary": {"rows_evaluated": result["listings_kept"], "details_applied": result["details_applied"],
                                     "classification_distribution": result["classification_distribution"]},
            "rule_version": result["rule_version"], "policy_version": result["policy_version"]}


def _posting_evidence(evidence: Any, detail: VacancyDetail) -> dict[str, Any]:
    """What the employer's wording says, kept apart: advertisement language vs mandatory working languages."""
    q = evidence.quotes
    sponsorship = lp.sponsorship_status(evidence)
    return {
        "advertisement_language": lp.detect_ad_language(detail.text),
        "mandatory_working_languages": list(evidence.mandatory_languages),
        "language_levels": dict(evidence.language_levels),
        "language_quotes": {k: v for k, v in q.items() if k.startswith("mandatory_language:")},
        "sponsorship": {"status": sponsorship, "quote": q.get("sponsorship_no") if sponsorship == "unavailable"
                        else q.get("sponsorship_yes") if sponsorship == "offered" else None,
                        "quotes_if_contradictory": ([q.get("sponsorship_no"), q.get("sponsorship_yes")]
                                                    if sponsorship == "contradictory" else None)},
        "compensation": evidence.compensation,
        "experience_requirements": list(evidence.experience_requirements),
        "education_requirements": list(evidence.education_requirements)}


def _compact(s: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if s is None:
        return None
    keys = ("evidence_level", "classification", "decision_label", "reason_code", "reason_codes", "score",
            "score_exact", "band", "ratings", "unknown_components", "dimensions", "confirmed_incompatibilities",
            "preliminary_signals", "missing_evidence", "review_flags", "detail_quotes", "rule_version",
            "policy_version")
    return {k: s[k] for k in keys}


class NoRequests:
    """Stand-in carrier for offline reassessment: no request is possible or recorded."""
    total = 0
    log: list = []


_ORIGINS = {"live_allowlisted_get": LIVE_ORIGIN, "derived_offline_reassessment": DERIVED_ORIGIN}


def build_report(allowlist: Mapping[str, Any], verification: Mapping[str, Any], carrier: HttpCarrier | NoRequests, *,
                 criteria_sha256: str, allowlist_sha256: str, mode: str, started_at: str,
                 watchlist_store: Mapping[str, Any] | None = None,
                 derived_from: Mapping[str, Any] | None = None) -> dict[str, Any]:
    log = [{k: e[k] for k in ("url", "status", "bytes")} for e in carrier.log]
    origin = _ORIGINS.get(mode, REPLAY_ORIGIN)
    real = origin != REPLAY_ORIGIN
    store = watchlist_store if watchlist_store is not None else lp.merge_watchlist(None, verification["watchlist_records"])
    return {"schema": SCHEMA_REPORT, "mode": "detail_verification_no_submit", "transport_mode": mode,
            "evidence_origin": origin, "real_evidence": real,
            "function06_eligible": real, "derived_from": dict(derived_from) if derived_from else None,
            "language_policy": {"version": lp.POLICY_VERSION, "approval": lp.POLICY_APPROVAL,
                                "matrix": {f"{a}|{b}": v[0] for (a, b), v in lp.MATRIX.items()}},
            **lp.reporting_section(store),
            "policy_exclusions": [{"identity": h["identity"], "reasons": h["reasons"]}
                                  for h in verification["history_records"] if h["kind"] == "policy_exclusion"],
            "executable": False, "submit_authorized": False, "authority": dict(AUTHORITY),
            "policy_version": POLICY_VERSION, "started_at": started_at,
            "source": {**allowlist["source"], "allowlist_sha256": allowlist_sha256,
                       "criteria_sha256_verified": criteria_sha256},
            "requests": {"approved": len(allowlist["targets"]), "attempted": carrier.total, "log": log,
                         "methods": ["GET"], "redirects_followed": 0, "retries": 0},
            "verdict_counts": verification["verdict_counts"], "reevaluation": verification["reevaluation_summary"],
            "candidates": verification["candidates"],
            "not_verified": [
                "No candidate qualification or employer requirement is inferred; only explicit employer wording counts.",
                "candidate_for_review is provisional: mandatory qualifications are not checked against verified candidate facts, and the label is not execution or submission authority.",
                "scope and career_upside stay Unknown without a reviewer rating that cites a reference."]}


def build_handoff(report: Mapping[str, Any]) -> dict[str, Any]:
    """Prioritized, non-executable Function 06 hand-off. Confirmed-incompatible candidates are listed apart."""
    def order(c: Mapping[str, Any]) -> tuple:
        s = c["reevaluation"]
        return (_STATUS_ORDER[c["verdict"]["status"]], c["reevaluated_priority_rank"] or 10 ** 9,
                -Fraction(s["score_exact"]) if s else 0, c["f04_priority_rank"])

    ranked = sorted(report["candidates"], key=order)
    keep = [c for c in ranked if c["verdict"]["status"] in _BASE_STATUSES[:2]]
    out = []
    for position, c in enumerate(keep, 1):
        s = c["reevaluation"]
        out.append({"handoff_rank": position, "status": c["verdict"]["status"], "status_reasons": c["verdict"]["reasons"],
                    "ats": c["ats"], "region": c["region"], "tenant": c["tenant"], "posting_id": c["posting_id"],
                    "canonical_url": c["canonical_url"], "requisition_id": c["requisition_id"],
                    "company": c["company"], "role": c["role"], "location": c["location"],
                    "detail_sha256": c["evidence"]["raw_sha256"], "observed_at": c["request"]["observed_at"], "evidence_origin": report["evidence_origin"],
                    "classification": s["classification"] if s else None,
                    "decision_label": s["decision_label"] if s else None, "score": s["score"] if s else None,
                    "missing_evidence": s["missing_evidence"] if s else [],
                    "review_flags": s["review_flags"] if s else [],
                    "mandatory_qualification_lines": (c["sections"] or {}).get("mandatory_qualification_lines", []),
                    "candidate_qualification_match": "not_performed_requires_verified_candidate_facts"})
    first = next((c for c in out if c["status"] == "candidate_for_review"), None)
    watch = {k: v for k, v in report.items() if k == lp.WATCHLIST}               # present only when non-empty
    return {"schema": SCHEMA_HANDOFF, **watch, "mode": "function06_handoff_non_executable", "executable": False,
            "submit_authorized": False, "authority": dict(AUTHORITY), "source_report_schema": report["schema"],
            "source": report["source"], "evidence_origin": report["evidence_origin"],
            "real_evidence": report["real_evidence"], "function06_eligible": report["function06_eligible"],
            "first_candidate_for_review": first,
            "next_step": ("synthetic_replay_output_is_not_real_evidence:do_not_use_for_function_06"
                          if not report["real_evidence"] else
                          "owner_review_then_qualification_check_against_verified_candidate_facts" if first else
                          "no_candidate_for_review_yet:resolve_evidence_gaps_or_widen_shortlist_with_owner_approval"),
            "prerequisites_before_function_06": list(HANDOFF_PREREQUISITES), "candidates": out,
            "confirmed_incompatible": [{"tenant": c["tenant"], "posting_id": c["posting_id"],
                                       "canonical_url": c["canonical_url"], "reasons": c["verdict"]["reasons"]}
                                      for c in ranked if c["verdict"]["status"] == "confirmed_incompatible"],
            "policy_excluded": [{"tenant": c["tenant"], "posting_id": c["posting_id"],
                                 "canonical_url": c["canonical_url"], "reasons": c["verdict"]["reasons"]}
                                for c in ranked if c["verdict"]["status"] == "policy_excluded"]}
