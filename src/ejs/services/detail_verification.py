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
  ``suitable`` | ``incomplete_evidence`` | ``confirmed_incompatible``.
* ``build_handoff``: prioritized Function 06 hand-off. Non-executable: no authority of any kind.

No candidate qualification and no employer requirement is inferred. Unknown stays Unknown.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any
from urllib.parse import urlsplit

from ejs.services.application_bridge import observe_posting_detail
from ejs.services.criteria_loader import Criteria
from ejs.services.discovery import DiscoveryError
from ejs.services.execution_manifest import validate_apply_url
from ejs.services.http_carrier import HttpCarrier
from ejs.services.suitability import (AUTHORITY as F04_AUTHORITY, ROW_FIELDS, SCHEMA as F04_SCHEMA, evaluate_report,
                                      canonical_sha256)
from ejs.services.suitability_evidence import VacancyDetail, detail_from_smartrecruiters, html_to_text

SCHEMA_ALLOWLIST = "EJS-DETAIL-ALLOWLIST-1"
SCHEMA_REPORT = "EJS-DETAIL-VERIFICATION-1"
SCHEMA_HANDOFF = "EJS-F06-HANDOFF-1"
POLICY_VERSION = "DETAIL-VERIFICATION-1.0"
MAX_PILOT_REQUESTS = 5
SELECT_CLASSIFICATION = "preliminary_high"
DETAIL_HOST = "api.smartrecruiters.com"
APPROVED_CRITERIA_STATUS = "approved_digest_and_rule_version_match"
MAX_SECTION_LINES = 40
MAX_LINE = 240

# Nothing in Function 05 may execute, submit, open a browser, upload a CV or start Function 06.
AUTHORITY = {"execute": False, "submit": False, "form_access": False, "browser": False, "cv_upload": False,
             "function06_started": False, "label_is_not_authority": True}
HANDOFF_PREREQUISITES = (
    "owner_review_of_this_handoff", "fresh_employer_and_mandatory_eligibility_review",
    "verified_candidate_facts_and_supported_answer_provenance", "observed_application_form",
    "execution_manifest_validation_and_independent_review", "separate_function_06_authorization")

_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
_MANDATORY = re.compile(r"\b(?:must|required|requires?|mandatory|essential)\b", re.I)
_OPTIONAL = re.compile(r"\b(?:plus|advantage|preferred|preferably|ideally|nice to have|desirable|bonus|optional|"
                       r"not required|not mandatory|beneficial|an asset)\b", re.I)
_STATUS_ORDER = {"suitable": 0, "incomplete_evidence": 1, "confirmed_incompatible": 2}
_CONFIRMED_CLASSES = frozenset({"verified_blocked", "verified_below_threshold"})
_ALLOWLIST_KEYS = frozenset({"schema", "mode", "executable", "submit_authorized", "authority", "request_policy",
                             "source", "selection", "targets", "approval"})


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


def build_allowlist(f04_report: Any, *, f04_report_sha256: str, limit: int = MAX_PILOT_REQUESTS) -> dict[str, Any]:
    """Explicit, reviewable detail-request allowlist. Pure; no network.

    Selection follows Function 04's ``priority_rank`` (the explicit priority), not a score sort: the first
    ``limit`` ``preliminary_high`` rows. A high row that cannot be bound exactly is reported in
    ``selection.unbound`` and is never replaced by a lower-priority row.
    """
    rows = validate_f04_report(f04_report)
    if type(limit) is not int or not 0 < limit <= MAX_PILOT_REQUESTS:
        raise DetailVerificationError("LIMIT_OUT_OF_RANGE")
    if not isinstance(f04_report_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", f04_report_sha256):
        raise DetailVerificationError("F04_REPORT_SHA256")
    high = [r for r in rows if r["suitability"]["classification"] == SELECT_CLASSIFICATION]
    targets: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    seen_identity: set[tuple] = set()
    for row in high[:limit]:
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
                           "browser": False, "form_access": False, "each_url_at_most_once": True},
        "source": {"f04_schema": F04_SCHEMA, "f04_report_sha256": f04_report_sha256,
                   "rule_version": f04_report.get("rule_version"),
                   "policy_version": f04_report.get("policy_version"),
                   "criteria_sha256": f04_report["criteria"].get("sha256")},
        "selection": {"rule": "preliminary_high_in_f04_priority_rank_order", "limit": limit,
                      "high_rows_in_report": len(high), "selected": len(targets), "unbound": unbound},
        "targets": targets}
    doc["approval"] = {"status": "awaiting_owner_approval", "allowlist_sha256": allowlist_digest(doc),
                       "note": "Quote allowlist_sha256 to approve exactly these requests. Nothing is approved yet."}
    return doc


def verify_allowlist(doc: Any, f04_report: Any, *, f04_report_sha256: str) -> dict[str, Any]:
    """The allowlist must equal what Function 04's report yields. Extra, altered or reordered targets are refused."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_ALLOWLIST or set(doc) != _ALLOWLIST_KEYS:
        raise DetailVerificationError("ALLOWLIST_SHAPE")
    targets = doc.get("targets")
    selection = doc.get("selection")
    if (not isinstance(targets, list) or not isinstance(selection, dict) or len(targets) > MAX_PILOT_REQUESTS
            or type(selection.get("limit")) is not int):
        raise DetailVerificationError("ALLOWLIST_SHAPE")
    expected = build_allowlist(f04_report, f04_report_sha256=f04_report_sha256, limit=selection["limit"])
    if allowlist_digest(doc) != allowlist_digest(expected):
        raise DetailVerificationError("ALLOWLIST_NOT_REPRODUCED_FROM_F04_REPORT")
    return expected


def report_to_discovery(f04_report: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the Function 02 projection shape from a Function 04 report (all rows, so similarity groups hold)."""
    from ejs.services.shadow_run import SCHEMA as DISCOVERY_SCHEMA
    rows = [{**{k: r[k] for k in ROW_FIELDS}, "posting_id": r.get("posting_id"),
             "similarity_key": r.get("similarity_key"), "discovery_evidence": r.get("discovery_evidence")}
            for r in f04_report["vacancies"]]
    return {"schema": DISCOVERY_SCHEMA, "mode": "shadow_no_submit", "projection": "f04_report_rebuilt",
            "listings_kept": len(rows), "vacancies": rows}


def check_f04_reproducible(f04_report: Mapping[str, Any], criteria: Criteria, *, criteria_sha256: str,
                           expected_criteria_sha256: str, expected_rule_version: str) -> None:
    """The F04 report must be what these exact approved criteria produce from its own rows (no hand-edited ranks)."""
    again = evaluate_report(report_to_discovery(f04_report), criteria, criteria_sha256=criteria_sha256,
                            expected_criteria_sha256=expected_criteria_sha256,
                            expected_rule_version=expected_rule_version)
    if again["criteria"]["approval"] != APPROVED_CRITERIA_STATUS:
        raise DetailVerificationError("CRITERIA_NOT_APPROVED")
    old = [(r["priority_rank"], r["tenant"], r.get("posting_id"), r["suitability"]) for r in f04_report["vacancies"]]
    new = [(r["priority_rank"], r["tenant"], r.get("posting_id"), r["suitability"]) for r in again["vacancies"]]
    if json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True):
        raise DetailVerificationError("F04_REPORT_NOT_REPRODUCIBLE")


# ------------------------------------------------------------------------------------------- requests
def request_details(allowlist: Mapping[str, Any], carrier: HttpCarrier, *,
                    now: Callable[[], datetime]) -> list[dict[str, Any]]:
    """One GET per allowlisted URL, in allowlist order, through the carrier. Never anything else.

    A failing employer stops (carrier rule); remaining targets of that employer are recorded as not requested.
    """
    targets = allowlist["targets"]
    if len(targets) > MAX_PILOT_REQUESTS:
        raise DetailVerificationError("TOO_MANY_TARGETS")
    approved = {t["detail_url"] for t in targets}
    if len(approved) != len(targets):
        raise DetailVerificationError("DUPLICATE_TARGET")
    requested: set[str] = set()
    observations: list[dict[str, Any]] = []
    for target in targets:
        box: dict[str, bytes] = {}

        def guarded(url: str, target=target, box=box) -> bytes:
            if url != target["detail_url"] or url not in approved or url in requested:
                raise DetailVerificationError("REQUEST_NOT_ALLOWLISTED_OR_DUPLICATE")
            requested.add(url)
            box["raw"] = carrier(url)
            return box["raw"]

        carrier.begin_employer(target["tenant"])
        before = carrier.total
        w2: dict[str, Any] = {"tenant": target["tenant"], "posting_id": target["posting_id"]}
        outcome, status = "ok", None
        try:
            observe_posting_detail(w2, guarded)
        except DetailVerificationError:
            raise
        except DiscoveryError as exc:
            outcome, status = exc.kind, getattr(exc, "status", None)
        except Exception as exc:  # noqa: BLE001 - recorded, never retried
            outcome = f"error_{type(exc).__name__}"
        observations.append({"target": target, "outcome": outcome, "http_status": status,
                             "attempted": carrier.total - before, "observed_at": _iso(now()),
                             "raw": box.get("raw"), "w2": w2})
    logged = [e["url"] for e in carrier.log if e["url"]]
    if len(set(logged)) != len(logged) or not set(logged) <= approved:
        raise DetailVerificationError("UNAPPROVED_OR_DUPLICATE_REQUEST_LOGGED")
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
def _verdict(observation: Mapping[str, Any], facts: Mapping[str, Any], entry: Mapping[str, Any] | None) -> dict:
    reasons: list[str] = []
    if observation["raw"] is None:
        out = observation["outcome"]
        return {"status": "incomplete_evidence", "reasons": [f"detail_not_obtained:{out}"]}
    if facts["identity"] != "bound" or entry is None:
        return {"status": "incomplete_evidence", "reasons": [f"identity_{facts['identity']}:{r}"
                                                             for r in facts["identity_reasons"]] or ["identity_unbound"]}
    s = entry["suitability"]
    confirmed = list(s["confirmed_incompatibilities"])
    if confirmed or s["classification"] in _CONFIRMED_CLASSES:
        return {"status": "confirmed_incompatible", "reasons": confirmed or [s["classification"]]}
    if facts["availability"] != "active":
        reasons.append("availability_unverified")
    if not facts["requisition_id"]:
        reasons.append("requisition_id_unverified")
    if facts["w2_eligibility_conditions"]:
        reasons.append("w2_eligibility_condition_requires_review")
    if s["classification"] != "verified_apply_candidate":
        reasons.append(f"f04_{s['classification']}")
    reasons += [f"review_flag:{f}" for f in s["review_flags"]]
    if reasons:
        return {"status": "incomplete_evidence", "reasons": reasons}
    return {"status": "suitable", "reasons": ["f04_verified_apply_candidate_with_bound_active_requisition"]}


def verify_details(f04_report: Mapping[str, Any], allowlist: Mapping[str, Any],
                   observations: list[dict[str, Any]], criteria: Criteria, *, criteria_sha256: str,
                   expected_criteria_sha256: str, expected_rule_version: str) -> dict[str, Any]:
    """Re-evaluate with the approved Function 04 criteria and the identity-bound details; no new evidence invented."""
    facts = [analyze(o) for o in observations]
    details: dict[tuple[str, str], VacancyDetail] = {
        (o["target"]["tenant"], o["target"]["posting_id"]): f["vacancy_detail"]
        for o, f in zip(observations, facts) if f["vacancy_detail"] is not None}
    result = evaluate_report(report_to_discovery(f04_report), criteria, details=details,
                             criteria_sha256=criteria_sha256, expected_criteria_sha256=expected_criteria_sha256,
                             expected_rule_version=expected_rule_version)
    if result["criteria"]["approval"] != APPROVED_CRITERIA_STATUS or result["details_rejected"]:
        raise DetailVerificationError("REEVALUATION_REFUSED")
    by_identity = {(r["tenant"], r.get("posting_id")): r for r in result["vacancies"]}
    candidates = []
    for obs, fact in zip(observations, facts):
        target = obs["target"]
        entry = by_identity.get((target["tenant"], target["posting_id"]))
        verdict = _verdict(obs, fact, entry if fact["identity"] == "bound" else None)
        suit = entry["suitability"] if entry is not None and fact["identity"] == "bound" else None
        candidates.append({
            "sequence": target["sequence"], "f04_priority_rank": target["f04_priority_rank"],
            "reevaluated_priority_rank": entry["priority_rank"] if suit else None,
            "ats": target["ats"], "region": target["region"], "tenant": target["tenant"],
            "posting_id": target["posting_id"], "canonical_url": target["canonical_url"],
            "detail_url": target["detail_url"], "company": target["company"], "role": target["role"],
            "location": target["location"],
            "request": {"outcome": obs["outcome"], "http_status": obs["http_status"],
                        "attempted": obs["attempted"], "observed_at": obs["observed_at"]},
            "evidence": {"raw_sha256": suit["detail_sha256"] if suit else None,
                         "raw_bytes": len(obs["raw"]) if obs["raw"] is not None else None},
            "identity": {"status": fact["identity"], "reasons": fact["identity_reasons"]},
            "availability": fact["availability"], "requisition_id": fact["requisition_id"],
            "w2_eligibility_conditions": fact["w2_eligibility_conditions"],
            "sections": fact["sections"], "reevaluation": _compact(suit), "verdict": verdict})
    counts = Counter(c["verdict"]["status"] for c in candidates)
    return {"candidates": candidates, "verdict_counts": {k: counts.get(k, 0) for k in _STATUS_ORDER},
            "reevaluation_summary": {"rows_evaluated": result["listings_kept"], "details_applied": result["details_applied"],
                                     "classification_distribution": result["classification_distribution"]},
            "rule_version": result["rule_version"], "policy_version": result["policy_version"]}


def _compact(s: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if s is None:
        return None
    keys = ("evidence_level", "classification", "decision_label", "reason_code", "reason_codes", "score",
            "score_exact", "band", "ratings", "unknown_components", "dimensions", "confirmed_incompatibilities",
            "preliminary_signals", "missing_evidence", "review_flags", "detail_quotes", "rule_version",
            "policy_version")
    return {k: s[k] for k in keys}


def build_report(allowlist: Mapping[str, Any], verification: Mapping[str, Any], carrier: HttpCarrier, *,
                 criteria_sha256: str, allowlist_sha256: str, mode: str, started_at: str) -> dict[str, Any]:
    log = [{k: e[k] for k in ("url", "status", "bytes")} for e in carrier.log]
    return {"schema": SCHEMA_REPORT, "mode": "detail_verification_no_submit", "transport_mode": mode,
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
                "A suitable verdict is a label for Function 06 review, not execution or submission authority.",
                "scope and career_upside stay Unknown without a reviewer rating that cites a reference."]}


def build_handoff(report: Mapping[str, Any]) -> dict[str, Any]:
    """Prioritized, non-executable Function 06 hand-off. Confirmed-incompatible candidates are listed apart."""
    def order(c: Mapping[str, Any]) -> tuple:
        s = c["reevaluation"]
        return (_STATUS_ORDER[c["verdict"]["status"]], c["reevaluated_priority_rank"] or 10 ** 9,
                -Fraction(s["score_exact"]) if s else 0, c["f04_priority_rank"])

    ranked = sorted(report["candidates"], key=order)
    keep = [c for c in ranked if c["verdict"]["status"] != "confirmed_incompatible"]
    out = []
    for position, c in enumerate(keep, 1):
        s = c["reevaluation"]
        out.append({"handoff_rank": position, "status": c["verdict"]["status"], "status_reasons": c["verdict"]["reasons"],
                    "ats": c["ats"], "region": c["region"], "tenant": c["tenant"], "posting_id": c["posting_id"],
                    "canonical_url": c["canonical_url"], "requisition_id": c["requisition_id"],
                    "company": c["company"], "role": c["role"], "location": c["location"],
                    "detail_sha256": c["evidence"]["raw_sha256"], "observed_at": c["request"]["observed_at"],
                    "classification": s["classification"] if s else None,
                    "decision_label": s["decision_label"] if s else None, "score": s["score"] if s else None,
                    "missing_evidence": s["missing_evidence"] if s else [],
                    "review_flags": s["review_flags"] if s else [],
                    "mandatory_qualification_lines": (c["sections"] or {}).get("mandatory_qualification_lines", []),
                    "candidate_qualification_match": "not_performed"})
    first = next((c for c in out if c["status"] == "suitable"), None)
    return {"schema": SCHEMA_HANDOFF, "mode": "function06_handoff_non_executable", "executable": False,
            "submit_authorized": False, "authority": dict(AUTHORITY), "source_report_schema": report["schema"],
            "source": report["source"], "first_suitable_candidate": first,
            "next_step": ("owner_review_then_separate_function_06_authorization" if first else
                          "no_suitable_candidate_yet:resolve_evidence_gaps_or_widen_shortlist_with_owner_approval"),
            "prerequisites_before_function_06": list(HANDOFF_PREREQUISITES), "candidates": out,
            "confirmed_incompatible": [{"tenant": c["tenant"], "posting_id": c["posting_id"],
                                       "canonical_url": c["canonical_url"], "reasons": c["verdict"]["reasons"]}
                                      for c in ranked if c["verdict"]["status"] == "confirmed_incompatible"]}
