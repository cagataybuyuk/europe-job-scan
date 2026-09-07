from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OutcomeType(str, Enum):
    APPLICATION_RECEIVED = "Application Received"
    REJECTION = "Rejection"
    SCREENING = "Screening"
    INTERVIEW = "Interview"
    OFFER = "Offer"


class MatchConfidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class MutationDisposition(str, Enum):
    ELIGIBLE = "eligible"
    KILL_SWITCHED = "kill_switched"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    AUDIT_ONLY = "audit_only"
    PRECONDITION_FAILED = "precondition_failed"
    SAME_STATE_NOOP = "same_state_noop"
    CONCURRENT_CHANGE = "concurrent_change_detected"


@dataclass(frozen=True)
class OpportunityState:
    company: str
    role: str
    country: str
    status: str
    applied_date: str = ""
    last_update: str = ""
    verification_status: str = ""
    url_status: str = ""


@dataclass(frozen=True)
class OutcomeSignal:
    message_id: str
    event_type: OutcomeType
    email_date: str
    company: str
    role: str
    confidence: MatchConfidence
    exact_match: bool
    evidence_ref: str
    rejection_reason: str = ""


@dataclass(frozen=True)
class MutationPlan:
    disposition: MutationDisposition
    should_mutate: bool
    reason: str
    outcome_key: str
    pipeline_key: str | None
    opportunity_key: str | None
    from_state: str | None
    to_state: str | None
    state_rule: str | None
    expected_fingerprint: str | None
    target_values: dict[str, str]
