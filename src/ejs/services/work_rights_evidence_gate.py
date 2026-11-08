"""Read-only eligibility evidence gate. Never grants employer mutation authority."""
from __future__ import annotations

from collections.abc import Mapping
import unicodedata

def _ambiguous_text(value: str) -> bool:
    """Reject invisible/format characters and Unicode separators in legal evidence."""
    return any(unicodedata.category(ch).startswith(("C", "Z")) for ch in value)


STATES = frozenset({"VERIFIED", "INELIGIBLE", "UNKNOWN"})


def evaluate_work_rights(evidence: object, target_country: str) -> dict:
    """Require current, country-specific evidence; unknowns fail closed."""
    reasons = []
    if not isinstance(evidence, Mapping) or not isinstance(target_country, str) or not target_country.strip():
        reasons.append("MISSING_EVIDENCE_OR_TARGET")
    else:
        legal_keys = frozenset({"state", "country", "source_type", "verified", "current", "source_ref"})
        if any(isinstance(key, str) and key not in legal_keys
               and unicodedata.normalize("NFKC", "".join(ch for ch in key if not ch.isspace() and unicodedata.category(ch) not in ("Cf", "Cc", "Zs", "Zl", "Zp"))) in legal_keys for key in evidence):
            reasons.append("AMBIGUOUS_LEGAL_FIELD_KEY")
        state = evidence.get("state")
        if not isinstance(state, str) or state not in STATES:
            reasons.append("UNKNOWN_STATUS")
        if (not isinstance(evidence.get("country"), str) or not evidence["country"].strip()
                or evidence["country"] != evidence["country"].strip()
                or target_country != target_country.strip()
                or _ambiguous_text(evidence["country"] + target_country)
                or evidence["country"] != target_country):
            reasons.append("COUNTRY_MISMATCH")
        if evidence.get("source_type") != "user_explicit":
            reasons.append("USER_CONFIRMATION_REQUIRED")
        if evidence.get("verified") is not True:
            reasons.append("UNVERIFIED")
        if evidence.get("current") is not True:
            reasons.append("STALE_OR_UNKNOWN")
        if (not isinstance(evidence.get("source_ref"), str) or not evidence["source_ref"].strip()
                or evidence["source_ref"] != evidence["source_ref"].strip()
                or _ambiguous_text(evidence["source_ref"])):
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
