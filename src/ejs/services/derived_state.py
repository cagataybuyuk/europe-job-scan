from __future__ import annotations

from ejs.domain.derived_state import (
    DerivedFillPackStatus,
    ReadinessProjection,
    ReadinessProjectionInput,
    UserActionProjection,
)


FORM_ACCESS_BLOCKERS = {
    "Not Inspected",
    "Recovered - Inspect Required",
    "Canonical URL Missing",
    "Direct Detail Unresolved",
    "Inaccessible",
    "Partial",
}


def derive_readiness(item: ReadinessProjectionInput) -> ReadinessProjection:
    if item.application_status != "To Apply":
        derived = (
            DerivedFillPackStatus.SUBMITTED_OUTCOME
            if item.application_status in {
                "Applied", "Recruiter Contacted", "Screening", "Interview 1",
                "Interview 2", "Final Interview", "Offer", "Rejected"
            }
            else DerivedFillPackStatus.ON_HOLD
        )
        return _projection(item, derived, True, "High", "non-active application state")

    if item.form_access in FORM_ACCESS_BLOCKERS:
        return _projection(
            item,
            DerivedFillPackStatus.NEEDS_FORM_ACCESS,
            False,
            "Structural",
            "shadow parity only: form-access projection is not in initial MW-4B write scope",
        )

    confidence = item.readiness_confidence or "Unknown"
    unresolved = item.unresolved_required or 0
    factual = item.user_required_factual or 0
    review = item.blocking_review or 0

    if unresolved > 0:
        return _projection(
            item,
            DerivedFillPackStatus.NEEDS_USER_CLARIFICATION,
            False,
            confidence,
            "one or more required fields remain unresolved",
        )

    if factual > 0 or review > 0:
        derived = DerivedFillPackStatus.READY_USER_INPUT
    else:
        if not item.cv_asset_ready:
            derived = DerivedFillPackStatus.READY_USER_INPUT
        else:
            derived = DerivedFillPackStatus.READY_FOR_USER_SUBMIT

    write_eligible = confidence == "High"
    reason = "high-confidence readiness derivation" if write_eligible else "audit-only: readiness confidence below High"
    return _projection(item, derived, write_eligible, confidence, reason)


def derive_user_action(readiness: ReadinessProjection) -> UserActionProjection:
    if readiness.derived_status == DerivedFillPackStatus.READY_FOR_USER_SUBMIT:
        return UserActionProjection(
            readiness.company,
            readiness.role,
            True,
            "Submit",
            "P1",
            "Yes — user actions only",
            "Submit Now",
        )
    if readiness.derived_status == DerivedFillPackStatus.READY_USER_INPUT:
        return UserActionProjection(
            readiness.company,
            readiness.role,
            True,
            "Input + Submit",
            "P2",
            "After input",
            "Answer Once + Submit",
        )
    return UserActionProjection(readiness.company, readiness.role, False, None, None, None, None)


def _projection(
    item: ReadinessProjectionInput,
    derived: DerivedFillPackStatus,
    write_eligible: bool,
    confidence: str,
    reason: str,
) -> ReadinessProjection:
    parity = derived.value == item.current_fill_pack_status
    return ReadinessProjection(
        company=item.company,
        role=item.role,
        derived_status=derived,
        current_status=item.current_fill_pack_status,
        status_parity=parity,
        write_eligible=write_eligible,
        confidence=confidence,
        critical_divergence=not parity,
        reason=reason,
    )
