"""MVP-05: pure, fail-closed answer provenance gate.

This module does not interact with ATS sites or authorize writes.
"""
from __future__ import annotations

from collections.abc import Mapping

LEGAL_FIELDS = frozenset({
    "work_authorization", "visa_sponsorship", "citizenship",
    "residency", "salary_expectation", "criminal_history",
    "consent", "disability_status",
})
ALLOWED_SOURCE_TYPES = frozenset({"candidate_verified", "approved_document", "user_explicit"})


def evaluate_answer_manifest(manifest: object) -> dict:
    """Reject unverified, ambiguous or unsupported field answers.

    A provenance reference alone never proves the answer is correct.
    The source must be explicitly approved and independently verified.
    """
    errors: list[str] = []
    if not isinstance(manifest, Mapping):
        return {"status": "BLOCKED", "reasons": ["INVALID_MANIFEST"], "write_authorized": False}
    fields = manifest.get("fields")
    if not isinstance(fields, list) or not fields:
        return {"status": "BLOCKED", "reasons": ["MISSING_FIELDS"], "write_authorized": False}
    seen: set[str] = set()
    for index, field in enumerate(fields):
        prefix = f"field_{index}"
        if not isinstance(field, Mapping):
            errors.append(f"{prefix}:INVALID_FIELD")
            continue
        key = field.get("key")
        if not isinstance(key, str) or not key.strip() or key in seen:
            errors.append(f"{prefix}:INVALID_OR_DUPLICATE_KEY")
            continue
        seen.add(key)
        value = field.get("value")
        if not isinstance(value, (str, bool, int, float)) or (
            isinstance(value, str) and not value.strip()
        ):
            errors.append(f"{key}:MISSING_VALUE")
        provenance = field.get("provenance")
        if not isinstance(provenance, Mapping):
            errors.append(f"{key}:MISSING_PROVENANCE")
            continue
        if provenance.get("source_type") not in ALLOWED_SOURCE_TYPES:
            errors.append(f"{key}:UNAPPROVED_SOURCE_TYPE")
        if not isinstance(provenance.get("source_ref"), str) or not provenance["source_ref"].strip():
            errors.append(f"{key}:MISSING_SOURCE_REF")
        if provenance.get("verified") is not True or provenance.get("approved") is not True:
            errors.append(f"{key}:UNVERIFIED_SOURCE")
        if key.casefold() in LEGAL_FIELDS and provenance.get("source_type") != "user_explicit":
            errors.append(f"{key}:USER_CONFIRMATION_REQUIRED")
    return {
        "status": "BLOCKED" if errors else "REVIEW_READY",
        "reasons": errors,
        "field_count": len(fields),
        "write_authorized": False,
        "upload_authorized": False,
        "submit_authorized": False,
    }
