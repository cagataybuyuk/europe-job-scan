from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DerivedFillPackStatus(StrEnum):
    READY_FOR_USER_SUBMIT = "Ready for User Submit"
    READY_USER_INPUT = "Ready - User Input Required"
    NEEDS_FORM_ACCESS = "Needs Form Access"
    NEEDS_USER_CLARIFICATION = "Needs User Clarification"
    ON_HOLD = "On Hold"
    SUBMITTED_OUTCOME = "Submitted / Outcome"


@dataclass(frozen=True)
class ReadinessProjectionInput:
    company: str
    role: str
    application_status: str
    form_access: str
    total_required: int | None
    ready_ai: int | None
    user_required_factual: int | None
    user_only_legal: int | None
    technical_captcha: int | None
    blocking_review: int | None
    unresolved_required: int | None
    readiness_confidence: str | None
    cv_asset_ready: bool
    current_fill_pack_status: str


@dataclass(frozen=True)
class ReadinessProjection:
    company: str
    role: str
    derived_status: DerivedFillPackStatus
    current_status: str
    status_parity: bool
    write_eligible: bool
    confidence: str
    critical_divergence: bool
    reason: str


@dataclass(frozen=True)
class UserActionProjection:
    company: str
    role: str
    include: bool
    category: str | None
    priority: str | None
    can_submit_now: str | None
    batch_group: str | None
