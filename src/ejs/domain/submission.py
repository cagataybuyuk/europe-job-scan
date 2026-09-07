from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ejs.contracts.submit import RolloutStage, SUBMIT_POLICY_VERSION
from ejs.domain.template_history import DriftState, MappingConfidence


class ResolutionMode(str, Enum):
    VERIFIED_FACT = "Verified Fact"
    APPROVED_POLICY = "Approved Policy"
    GROUNDED_GENERATED = "Grounded Generated"
    DEFAULT_NO = "Default No"
    BLANK_OPTIONAL = "Blank Optional"
    NEUTRAL_CHOICE = "Approved Neutral Choice"
    ARTIFACT = "Approved Artifact"
    UNRESOLVED = "Unresolved"


class ValidationDecision(str, Enum):
    ELIGIBLE = "Eligible"
    RUNTIME_DISABLED = "Eligible / Runtime Disabled"
    BLOCKED = "Blocked"
    DUPLICATE_SUPPRESSED = "Duplicate Suppressed"


class SubmissionAttemptStatus(str, Enum):
    REQUESTED = "Requested"
    VALIDATED = "Validated"
    SUBMIT_CLICKED = "Submit Clicked"
    SUBMITTED_UNCONFIRMED = "Submitted / Unconfirmed"
    CONFIRMED = "Confirmed"
    BLOCKED = "Blocked"
    FAILED = "Failed"
    DUPLICATE_SUPPRESSED = "Duplicate Suppressed"


class ConfirmationType(str, Enum):
    CONFIRMATION_PAGE = "Confirmation Page"
    EMPLOYER_EMAIL = "Employer Email"
    ATS_ACKNOWLEDGEMENT = "ATS Acknowledgement"


@dataclass(frozen=True)
class ControlResolution:
    control_key: str
    label: str
    required: bool
    canonical_field: str = ""
    mapping_confidence: MappingConfidence = MappingConfidence.LOW
    resolution_mode: ResolutionMode = ResolutionMode.UNRESOLVED
    provenance_ref: str = ""
    policy_ref: str = ""
    artifact_ref: str = ""
    grounded: bool = False
    explicit_user_fact: bool = False
    option_available: bool = True
    notes: str = ""

    @property
    def mapped(self) -> bool:
        return bool(self.canonical_field.strip())

    @property
    def has_provenance(self) -> bool:
        return bool(self.provenance_ref.strip() or self.policy_ref.strip() or self.artifact_ref.strip())


@dataclass(frozen=True)
class PreSubmitValidationRequest:
    execution_id: str
    opportunity_key: str
    requisition_id: str
    candidate_profile_version: str
    apply_url: str
    ats_family: str
    form_fingerprint: str
    drift_state: DriftState
    runtime_inspection_current: bool
    tracker_fingerprint_current: bool
    controls: tuple[ControlResolution, ...]
    existing_submit_keys: frozenset[str] = field(default_factory=frozenset)
    rollout_stage: RolloutStage = RolloutStage.R0_SHADOW
    submissions_today: int = 0
    technical_challenge: str = ""
    policy_version: str = SUBMIT_POLICY_VERSION


@dataclass(frozen=True)
class PreSubmitValidationResult:
    validation_key: str
    submit_key: str
    decision: ValidationDecision
    policy_eligible: bool
    execution_allowed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    required_controls: int
    resolved_required_controls: int
    provenance_complete: bool
    artifact_ready: bool
    duplicate_state: str
    daily_cap_state: str


@dataclass(frozen=True)
class SubmissionAttempt:
    submit_key: str
    execution_id: str
    opportunity_key: str
    requisition_id: str
    candidate_profile_version: str
    policy_version: str
    validation_key: str
    requested_at: str
    status: SubmissionAttemptStatus
    ats_family: str = ""
    form_fingerprint: str = ""
    evidence_ref: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ConfirmationEvidence:
    submit_key: str
    evidence_type: ConfirmationType
    evidence_ref: str
    observed_at: str
    exact_identity_match: bool
    execution_id: str = ""
    requisition_id: str = ""
    notes: str = ""
