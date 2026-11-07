"""Read-only eligibility evidence gate. Never grants employer mutation authority."""
from __future__ import annotations

from collections.abc import Mapping

STATES = frozenset({"VERIFIED", "INELIGIBLE", "UNKNOWN"})


def evaluate_work_rights(evidence: object, target_country: str) -> dict:
    """Require current, country-specific evidence; unknowns fail closed."""
    reasons = []
    if not isinstance(evidence, Mapping) or not isinstance(target_country, str) or not target_country.strip():
        reasons.append("MISSING_EVIDENCE_OR_TARGET")
    else:
        state = evidence.get("state")
        if not isinstance(state, str) or state not in STATES:
            reasons.append("UNKNOWN_STATUS")
        if evidence.get("country") != target_country:
            reasons.append("COUNTRY_MISMATCH")
        if evidence.get("source_type") != "user_explicit":
            reasons.append("USER_CONFIRMATION_REQUIRED")
        if evidence.get("verified") is not True:
            reasons.append("UNVERIFIED")
        if evidence.get("current") is not True:
            reasons.append("STALE_OR_UNKNOWN")
        if not isinstance(evidence.get("source_ref"), str) or not evidence["source_ref"].strip():
            reasons.append("MISSING_SOURCE_REF")
        if isinstance(state, str) and state == "INELIGIBLE":
            reasons.append("INELIGIBLE")
        elif isinstance(state, str) and state == "UNKNOWN":
            reasons.append("UNKNOWN_ELIGIBILITY")
    return {
        "status": "BLOCKED" if reasons else "REVIEW_READY",
        "reasons": reasons,
        "write_authorized": False,
        "upload_authorized": False,
        "submit_authorized": False,
        "scope": "eligibility_metadata_only",
    }
