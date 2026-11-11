"""Fail-closed binding of eligibility evidence to an exact application target.

This classifier has no employer mutation authority.
"""
from __future__ import annotations
from collections.abc import Mapping
from datetime import datetime
from .work_rights_evidence_gate import evaluate_work_rights


def _aware_datetime(value: object) -> bool:
    """A tzinfo object alone does not make a datetime timezone-aware."""
    try:
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    except Exception:
        return False


def _future_expiry(value: object, now: object) -> bool:
    try:
        return _aware_datetime(value) and _aware_datetime(now) and value > now
    except Exception:
        return False


def evaluate_bound_work_rights(evidence: object, target: object, now: datetime) -> dict:
    blocked = {"status": "BLOCKED", "reasons": ["UNBOUND_OR_EXPIRED_EVIDENCE"],
               "write_authorized": False, "upload_authorized": False,
               "submit_authorized": False, "scope": "eligibility_metadata_only"}
    if not isinstance(evidence, Mapping) or not isinstance(target, Mapping):
        return blocked
    if not _aware_datetime(now):
        return blocked
    keys = ("candidate_id", "employer_id", "vacancy_url")
    if any(not isinstance(target.get(k), str) or not target[k].strip()
           or not isinstance(evidence.get(k), str) or evidence[k] != target[k]
           for k in keys):
        return blocked
    expires = evidence.get("expires_at")
    if not _future_expiry(expires, now):
        return blocked
    base = evaluate_work_rights(evidence, target.get("country"))
    if base["status"] != "REVIEW_READY":
        return base
    return base  # REVIEW_READY is not permission to write/upload/submit.
