from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

SUBMIT_POLICY_VERSION = "SUBMIT-1.0"


def _key_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def submit_identity(opportunity_key: str, requisition_id: str, candidate_profile_version: str) -> str:
    """Deterministic cross-retry submit identity.

    One opportunity/requisition/profile-version combination must never be submitted twice.
    """
    if not all(v.strip() for v in (opportunity_key, requisition_id, candidate_profile_version)):
        raise ValueError("opportunity_key, requisition_id and candidate_profile_version are required")
    return (
        f"submit:{_key_part(opportunity_key)}:{_key_part(requisition_id)}:"
        f"{_key_part(candidate_profile_version)}"
    )


def validation_identity(execution_id: str, form_fingerprint: str, policy_version: str = SUBMIT_POLICY_VERSION) -> str:
    if not all(v.strip() for v in (execution_id, form_fingerprint, policy_version)):
        raise ValueError("execution_id, form_fingerprint and policy_version are required")
    return f"submitval:{_key_part(execution_id)}:{_key_part(form_fingerprint)}:{_key_part(policy_version)}"


class RolloutStage(str, Enum):
    R0_SHADOW = "R0 — Shadow"
    R1_PREFILL = "R1 — Prefill"
    R2_SINGLE_CANARY = "R2 — Single Submit Canary"
    R3_BOUNDED_ATS = "R3 — Bounded ATS Submit"
    R4_MULTI_ATS = "R4 — Multi-ATS Autonomous"
    R5_BROAD = "R5 — Broad Autonomous"


ROLLOUT_DAILY_CAPS: dict[RolloutStage, int] = {
    RolloutStage.R0_SHADOW: 0,
    RolloutStage.R1_PREFILL: 0,
    RolloutStage.R2_SINGLE_CANARY: 1,
    RolloutStage.R3_BOUNDED_ATS: 5,
    RolloutStage.R4_MULTI_ATS: 5,
    RolloutStage.R5_BROAD: 5,
}


@dataclass(frozen=True)
class ApprovedSubmitPolicy:
    """User-approved v1.1 roadmap decisions A1-A10.

    This grants policy authority only. Runtime click authority is still separately gated by
    rollout stage, live submit flag, browser capability, validation, and kill switch.
    """

    version: str = SUBMIT_POLICY_VERSION
    auto_submit_when_all_gates_pass: bool = True  # A1
    work_right_from_verified_facts_only: bool = True  # A2
    salary_requires_preapproved_policy: bool = True  # A3
    required_privacy_ack_allowed_when_policy_covered: bool = True  # A4
    optional_marketing_default_no: bool = True  # A5
    optional_demographic_prefer_blank: bool = True  # A6
    mandatory_demographic_neutral_choice_only: bool = True  # A6
    grounded_ai_narrative_allowed: bool = True  # A7
    captcha_mfa_bypass_forbidden: bool = True  # A8
    rollout_starts_single_canary_then_five_per_day: bool = True  # A9
    mw5_parallel_nonblocking: bool = True  # A10
    approved_at: str = "2026-08-21"
    approval_source: str = "Roadmap v1.1 approved by user"

    def validate(self) -> None:
        if self.version != SUBMIT_POLICY_VERSION:
            raise ValueError("unexpected SUBMIT policy version")
        if not self.auto_submit_when_all_gates_pass:
            raise PermissionError("SUBMIT-1 baseline requires approved conditional auto-submit authority")
        if not self.work_right_from_verified_facts_only:
            raise PermissionError("work-right/sponsorship facts may not be inferred or optimized")
        if not self.salary_requires_preapproved_policy:
            raise PermissionError("salary auto-answer requires a pre-approved policy")
        if not self.required_privacy_ack_allowed_when_policy_covered:
            raise PermissionError("required privacy acknowledgement policy decision missing")
        if not self.optional_marketing_default_no:
            raise PermissionError("optional marketing consent must default to No")
        if not self.optional_demographic_prefer_blank:
            raise PermissionError("optional demographic questions must prefer blank")
        if not self.mandatory_demographic_neutral_choice_only:
            raise PermissionError("mandatory demographic questions require approved neutral-choice handling")
        if not self.grounded_ai_narrative_allowed:
            raise PermissionError("grounded narrative policy decision missing")
        if not self.captcha_mfa_bypass_forbidden:
            raise PermissionError("CAPTCHA/MFA bypass must remain forbidden")
        if not self.rollout_starts_single_canary_then_five_per_day:
            raise PermissionError("bounded rollout decision missing")
        if not self.mw5_parallel_nonblocking:
            raise PermissionError("MW-5 must remain a parallel safety track")


@dataclass(frozen=True)
class SubmitAuthority:
    """Runtime authority boundary for consequential submission action."""

    policy_approved: bool = True
    browser_external_form_write: bool = False
    final_submit_runtime_flag: bool = False
    kill_switch_engaged: bool = True
    user_fact_write: bool = False
    applications_write: bool = False
    captcha_bypass: bool = False

    def validate_policy_boundary(self) -> None:
        if not self.policy_approved:
            raise PermissionError("conditional auto-submit policy is not approved")
        if self.user_fact_write:
            raise PermissionError("submit runtime may not create or change User Facts")
        if self.applications_write:
            raise PermissionError("SUBMIT-1 validator does not directly mutate Applications")
        if self.captcha_bypass:
            raise PermissionError("CAPTCHA bypass is forbidden")

    @property
    def submit_click_allowed(self) -> bool:
        self.validate_policy_boundary()
        return (
            self.browser_external_form_write
            and self.final_submit_runtime_flag
            and not self.kill_switch_engaged
        )
