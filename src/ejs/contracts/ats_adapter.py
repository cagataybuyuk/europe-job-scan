from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

ADAPTER_CONTRACT_VERSION = "ATS-ADAPTER-1.0"


class AtsFamily(str, Enum):
    LINKEDIN_EASY_APPLY = "linkedin_easy_apply"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ADP = "adp"
    UNSUPPORTED = "unsupported"


LAUNCH_ATS_FAMILIES = (
    AtsFamily.LINKEDIN_EASY_APPLY,
    AtsFamily.SMARTRECRUITERS,
    AtsFamily.WORKDAY,
    AtsFamily.GREENHOUSE,
    AtsFamily.LEVER,
    AtsFamily.ADP,
)


class AdapterImplementationState(str, Enum):
    PLANNED = "planned"
    INSPECTION = "inspection"
    PREPARE = "prepare"
    LIVE_CANARY = "live_canary"
    PRODUCTION_READY = "production_ready"


class SessionRequirement(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class DispatchState(str, Enum):
    DISPATCH = "dispatch"
    HUMAN_REVIEW = "human_review"
    UNSUPPORTED = "unsupported"


class HumanReviewStage(str, Enum):
    DISCOVERY = "discovery"
    ROUTING = "routing"
    INSPECTION = "inspection"
    PREPARATION = "preparation"
    SUBMIT = "submit"
    CONFIRMATION = "confirmation"


class HumanReviewReason(str, Enum):
    ROUTE_UNRESOLVED = "route_unresolved"
    UNSUPPORTED_ATS = "unsupported_ats"
    ADAPTER_NOT_READY = "adapter_not_ready"
    AUTH_REQUIRED = "auth_required"
    SESSION_REQUIRED = "session_required"
    MFA_REQUIRED = "mfa_required"
    CAPTCHA_OR_CHALLENGE = "captcha_or_challenge"
    ATS_DRIFT = "ats_drift"
    UNKNOWN_REQUIRED_CONTROL = "unknown_required_control"
    REQUIRED_ANSWER_UNRESOLVED = "required_answer_unresolved"
    POLICY_BLOCKED = "policy_blocked"
    SUBMIT_CONFIRMATION_MISSING = "submit_confirmation_missing"


@dataclass(frozen=True)
class AdapterCapabilityManifest:
    family: AtsFamily
    adapter_key: str
    adapter_version: str
    implementation_state: AdapterImplementationState
    inspection_supported: bool
    safe_fill_supported: bool
    document_upload_supported: bool
    multi_step_supported: bool
    conditional_submit_supported: bool
    confirmation_supported: bool
    session_requirement: SessionRequirement = SessionRequirement.NONE
    challenge_boundaries: tuple[str, ...] = field(
        default_factory=lambda: ("captcha", "mfa", "security_verification")
    )
    contract_version: str = ADAPTER_CONTRACT_VERSION

    def validate(self) -> None:
        if self.family is AtsFamily.UNSUPPORTED:
            raise ValueError("unsupported family cannot have an adapter manifest")
        if self.contract_version != ADAPTER_CONTRACT_VERSION:
            raise ValueError("unexpected adapter contract version")
        if self.adapter_key != f"ats:{self.family.value}":
            raise ValueError("adapter_key must match ATS family")
        if not self.adapter_version.strip():
            raise ValueError("adapter_version is required")
        if self.implementation_state is AdapterImplementationState.PLANNED:
            implemented = (
                self.inspection_supported
                or self.safe_fill_supported
                or self.document_upload_supported
                or self.multi_step_supported
                or self.conditional_submit_supported
                or self.confirmation_supported
            )
            if implemented:
                raise ValueError("planned adapter cannot advertise implemented capabilities")
        if self.safe_fill_supported and not self.inspection_supported:
            raise ValueError("safe-fill requires inspection capability")
        if self.document_upload_supported and not self.inspection_supported:
            raise ValueError("document upload requires inspection capability")
        if self.conditional_submit_supported and not self.safe_fill_supported:
            raise ValueError("conditional submit requires safe-fill capability")
        if self.confirmation_supported and not self.conditional_submit_supported:
            raise ValueError("confirmation requires conditional-submit capability")
        if not self.challenge_boundaries:
            raise ValueError("challenge boundaries must not be empty")


@dataclass(frozen=True)
class AdapterDispatchDecision:
    state: DispatchState
    family: AtsFamily
    reason_code: str
    adapter_key: str = ""
    adapter_version: str = ""
    implementation_state: AdapterImplementationState = AdapterImplementationState.PLANNED
    mutation_authorized: bool = False
    submit_authorized: bool = False

    def validate(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("reason_code is required")
        if self.mutation_authorized or self.submit_authorized:
            raise PermissionError(
                "L0 dispatch metadata cannot grant mutation or submit authority"
            )
        if self.state is DispatchState.DISPATCH:
            if self.family is AtsFamily.UNSUPPORTED:
                raise ValueError("unsupported ATS cannot be dispatched")
            if not self.adapter_key or not self.adapter_version:
                raise ValueError("dispatch requires adapter identity")
        if self.state is DispatchState.UNSUPPORTED and self.family is not AtsFamily.UNSUPPORTED:
            raise ValueError("unsupported dispatch state requires unsupported ATS family")


@dataclass(frozen=True)
class HumanReviewItem:
    execution_id: str
    opportunity_key: str
    stage: HumanReviewStage
    reason: HumanReviewReason
    ats_family: AtsFamily
    detail_code: str
    evidence_ref: str = ""
    retryable: bool = False
    user_action_required: bool = True
    contract_version: str = ADAPTER_CONTRACT_VERSION

    def validate(self) -> None:
        if self.contract_version != ADAPTER_CONTRACT_VERSION:
            raise ValueError("unexpected human-review contract version")
        if not self.execution_id.strip() or not self.opportunity_key.strip():
            raise ValueError("execution_id and opportunity_key are required")
        if not self.detail_code.strip():
            raise ValueError("detail_code is required")
        if not self.user_action_required:
            raise ValueError("human review item must require human action")
