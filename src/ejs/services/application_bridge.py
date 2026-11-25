"""Offline preparation from W1 listing identities and optional posting detail bytes."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from ejs.services.execution_manifest import (
    ATS_HOSTS, ExecutionManifest, load_execution_manifest, validate_apply_url,
)
from ejs.services.shadow_run import SCHEMA as DISCOVERY_SCHEMA

SCHEMA = "EJS-APPLICATION-PREPARATION-1"
MAX_DETAIL_REQUESTS = 200
MAX_DETAIL_BYTES = 1_000_000
DetailTransport = Callable[[str], bytes]

# Narrow English matches. Preserve the employer's wording for human review.
_CONDITIONS = {
    "explicit_no_sponsorship": re.compile(
        r"(?:we (?:do not|cannot|will not) (?:offer|provide) visa sponsorship|"
        r"visa sponsorship is not available)", re.I),
    "existing_work_rights_required": re.compile(
        r"(?:you must|applicants must|candidates must) (?:already )?have "
        r"(?:the )?(?:legal )?right to work(?: in [\w -]+)?", re.I),
}
_MANIFEST_REQUIREMENTS = (
    "fresh_employer_and_mandatory_eligibility_review",
    "verified_candidate_facts_and_supported_answer_provenance",
    "approved_pdf_with_matching_name_digest_and_size",
    "observed_form_steps_controls_options_upload_and_fingerprint",
    "canonical_opportunity_and_duplicate_ledger_binding",
    "versioned_policy_profile_run_and_freshness_metadata",
    "execution_manifest_validation_and_independent_review",
)


class _PostingText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"} and self.hidden is None:
            self.hidden = tag
        if self.hidden is None and tag in {"p", "li", "br", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
        if self.hidden is None and tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden is None:
            self.parts.append(re.sub(r"\s+", " ", data))


def _detail(raw: bytes, tenant: str, posting_id: str) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DETAIL_BYTES:
        raise ValueError("detail_size")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("id") != posting_id:
        raise ValueError("detail_identity")
    company = data.get("company")
    if not isinstance(company, dict) or company.get("identifier") != tenant:
        raise ValueError("detail_identity")

    ref = data.get("refNumber")
    requisition = ref if isinstance(ref, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ref) else None
    active = data.get("active")
    ad = data.get("jobAd")
    sections = ad.get("sections") if isinstance(ad, dict) else None
    conditions = []
    if isinstance(sections, dict):
        for name in ("jobDescription", "qualifications", "additionalInformation"):
            section = sections.get(name)
            html = section.get("text") if isinstance(section, dict) else None
            if not isinstance(html, str):
                continue
            parser = _PostingText()
            parser.feed(html)
            parser.close()
            for sentence in re.split(r"\n+|(?<=[.!?])\s+", "".join(parser.parts)):
                quote = " ".join(sentence.split()).rstrip(".!?")
                for condition, pattern in _CONDITIONS.items():
                    if len(conditions) < 20 and pattern.fullmatch(quote):
                        conditions.append(dict(condition=condition, section=name, quote=quote))
    return dict(detail_sha256=sha256(raw).hexdigest(), requisition_id=requisition,
                application_availability="active_posting" if active is True else
                "closed" if active is False else "unknown",
                eligibility_conditions=conditions)


def _order(row: dict) -> tuple:
    criteria = row.get("criteria_evaluation")
    score = criteria.get("score") if isinstance(criteria, dict) else None
    priority = -score if type(score) in (int, float) and -float("inf") < score < float("inf") else 0
    return (row["ats_kind"] not in ATS_HOSTS, priority, row["ats_kind"],
            row["region"], row["tenant"], row["posting_id"] or "", row["url"],
            row["company"], row["role"], row["location"])


def prepare_application_report(report: dict, *, transport: DetailTransport | None = None,
                               detail_budget: int = 0) -> dict:
    if not isinstance(report, dict) or report.get("schema") != DISCOVERY_SCHEMA:
        raise ValueError("discovery_schema")
    rows = report.get("vacancies")
    if not isinstance(rows, list) or report.get("listings_kept") != len(rows):
        raise ValueError("discovery_count")
    if type(detail_budget) is not int or not 0 <= detail_budget <= MAX_DETAIL_REQUESTS:
        raise ValueError("detail_budget")
    if detail_budget and transport is None:
        raise ValueError("detail_transport_required")

    fields = ("ats_kind", "region", "tenant", "url", "company", "role", "location")
    for row in rows:
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in fields)
                or row.get("posting_id") is not None and not isinstance(row["posting_id"], str)):
            raise ValueError("discovery_row")

    candidates = []
    seen = {}
    conflicting = set()
    for row in sorted(rows, key=_order):
        identity = tuple(row[k] for k in ("ats_kind", "region", "tenant")) + (row.get("posting_id"),)
        if all(identity) and identity in seen:
            first = seen[identity]
            first["source_rows"] += 1
            if first["url"] != row["url"]:
                conflicting.add(identity)
            continue
        candidate = {k: row[k] for k in fields}
        candidate.update(posting_id=identity[-1], source_rows=1, requisition_id=None,
                         application_availability="unknown", eligibility_conditions=[])
        if all(identity):
            seen[identity] = candidate
        candidates.append((identity, candidate))

    attempts = 0
    for identity, candidate in candidates:
        family, region, tenant, posting = identity
        status = "missing_evidence"
        if family not in ATS_HOSTS:
            status, reason = "unsupported_ats", "no_preparation_adapter"
        elif identity in conflicting:
            reason = "conflicting_discovery_urls"
        elif not all(identity):
            reason = "missing_discovery_identity"
        elif not _compatible_url(candidate["url"], family, region, tenant, posting):
            reason = "incompatible_posting_identity_or_url"
        elif attempts >= detail_budget:
            reason = "detail_budget_exhausted" if transport is not None else "detail_not_observed"
        else:
            attempts += 1
            detail_url = f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings/{posting}"
            candidate["detail_source_url"] = detail_url
            try:
                raw = transport(detail_url)
            except (LookupError, OSError):
                reason = "detail_unavailable"
            else:
                try:
                    candidate.update(_detail(raw, tenant, posting))
                except (ValueError, RecursionError):
                    reason = "detail_invalid_or_unbound"
                else:
                    status, reason = _classify_detail(candidate)
        candidate.update(readiness=status, reason=reason)

    output = [candidate for _, candidate in candidates]
    return dict(schema=SCHEMA, source_schema=DISCOVERY_SCHEMA, executable=False,
                submit_authorized=False, input_rows=len(rows), candidate_count=len(output),
                duplicate_rows=len(rows) - len(output), detail_attempts=attempts,
                readiness_counts=dict(Counter(c["readiness"] for c in output)),
                manifest_requirements=list(_MANIFEST_REQUIREMENTS), candidates=output)


def _compatible_url(url: str, family: str, region: str, tenant: str, posting: str) -> bool:
    try:
        validate_apply_url(url, family, posting)
    except (ValueError, PermissionError):
        return False
    path = urlsplit(url).path
    return (region == "global" and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", tenant))
            and path.split("/")[1] == tenant and not path.endswith("/"))


def _classify_detail(candidate: dict) -> tuple[str, str]:
    availability = candidate["application_availability"]
    if availability == "closed":
        return "hard_blocker", "employer_posting_inactive"
    if availability != "active_posting":
        return "missing_evidence", "application_availability_unverified"
    if not candidate["requisition_id"]:
        return "missing_evidence", "requisition_id_unverified"
    if candidate["eligibility_conditions"]:
        return "missing_evidence", "eligibility_condition_requires_candidate_review"
    return "ready_for_form_observation", "active_posting_with_bound_requisition"


def validate_candidate_manifest(candidate: dict, raw: bytes, *, now: datetime,
                                expected_sha256: str | None = None) -> ExecutionManifest:
    """Validate separately prepared bytes and bind them to one preparation target."""
    manifest = load_execution_manifest(raw, now=now, expected_sha256=expected_sha256)
    if (candidate["readiness"] != "ready_for_form_observation"
            or (manifest.ats_family, manifest.company, manifest.posting_id,
                manifest.requisition_id, manifest.apply_url) !=
            (candidate["ats_kind"], candidate["tenant"], candidate["posting_id"],
             candidate["requisition_id"], candidate["url"])):
        raise PermissionError("PREPARATION_MANIFEST_TARGET_MISMATCH")
    return manifest
