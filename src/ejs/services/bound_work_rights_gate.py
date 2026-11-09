"""Fail-closed binding of eligibility evidence to an exact application target.

This classifier has no employer mutation authority.
"""
from __future__ import annotations
from collections.abc import Mapping
from datetime import datetime, timezone
from .work_rights_evidence_gate import evaluate_work_rights


def evaluate_bound_work_rights(evidence: object, target: object, now: datetime) -> dict:
    blocked = {"status": "BLOCKED", "reasons": ["UNBOUND_OR_EXPIRED_EVIDENCE"],
               "write_authorized": False, "upload_authorized": False,
               "submit_authorized": False, "scope": "eligibility_metadata_only"}
    if not isinstance(evidence, Mapping) or not isinstance(target, Mapping):
        return blocked
    if not isinstance(now, datetime) or now.tzinfo is None:
        return blocked
    keys = ("candidate_id", "employer_id", "vacancy_url")
    if any(not isinstance(target.get(k), str) or not target[k].strip()
           or not isinstance(evidence.get(k), str) or evidence[k] != target[k]
           for k in keys):
        return blocked
    expires = evidence.get("expires_at")
    if not isinstance(expires, datetime) or expires.tzinfo is None or expires <= now:
        return blocked
    base = evaluate_work_rights(evidence, target.get("country"))
    if base["status"] != "REVIEW_READY":
        return base
    return base  # REVIEW_READY is not permission to write/upload/submit.
