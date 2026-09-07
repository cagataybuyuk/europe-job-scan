from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ejs.contracts.file_upload import FileUploadPlan
from ejs.contracts.prefill import PrefillFieldPlan
from ejs.contracts.submit import RolloutStage
from ejs.domain.submission import ControlResolution

SMARTRECRUITERS_EXEC_VERSION = "SMARTRECRUITERS-EXEC-0.1"


class SmartRecruitersExecutionMode(str, Enum):
    PREPARE_ONLY = "prepare_only"


@dataclass(frozen=True)
class SmartRecruitersExecutionAuthority:
    navigation_read: bool = True
    dom_read: bool = True
    safe_form_value_write: bool = True
    approved_file_upload: bool = True
    consent_action: bool = False
    credential_entry: bool = False
    protected_attribute_action: bool = False
    captcha_bypass: bool = False
    final_submit: bool = False

    def validate(self) -> None:
        if not (self.navigation_read and self.dom_read and self.safe_form_value_write and self.approved_file_upload):
            raise PermissionError("SmartRecruiters prepare-only execution requires read + safe-write + approved-upload authority")
        forbidden = {
            "consent_action": self.consent_action,
            "credential_entry": self.credential_entry,
            "protected_attribute_action": self.protected_attribute_action,
            "captcha_bypass": self.captcha_bypass,
            "final_submit": self.final_submit,
        }
        enabled = [k for k, v in forbidden.items() if v]
        if enabled:
            raise PermissionError(f"SmartRecruiters prepare-only forbidden authorities enabled: {', '.join(enabled)}")


@dataclass(frozen=True)
class SmartRecruitersExecutionRequest:
    execution_id: str
    bridge_request_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    application_url: str
    observed_at: str
    expected_form_fingerprint: str
    candidate_profile_version: str
    application_status: str
    readiness_confidence: str
    unresolved_required: int
    field_plan: tuple[PrefillFieldPlan, ...]
    upload_plan: tuple[FileUploadPlan, ...]
    control_resolutions: tuple[ControlResolution, ...]
    tracker_fingerprint_current: bool = True
    existing_submit_keys: frozenset[str] = field(default_factory=frozenset)
    rollout_stage: RolloutStage = RolloutStage.R1_PREFILL
    submissions_today: int = 0
    adapter_key: str = "ats:smartrecruiters"
    adapter_version: str = "SMARTRECRUITERS-EXEC-0.1"
    policy_version: str = "SUBMIT-1.0"
    mode: SmartRecruitersExecutionMode = SmartRecruitersExecutionMode.PREPARE_ONLY
    max_navigation_steps: int = 6

    def validate(self) -> None:
        required = {
            "execution_id": self.execution_id,
            "bridge_request_id": self.bridge_request_id,
            "route_key": self.route_key,
            "opportunity_key": self.opportunity_key,
            "requisition_id": self.requisition_id,
            "application_url": self.application_url,
            "observed_at": self.observed_at,
            "expected_form_fingerprint": self.expected_form_fingerprint,
            "candidate_profile_version": self.candidate_profile_version,
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "policy_version": self.policy_version,
        }
        missing = [k for k, v in required.items() if not str(v).strip()]
        if missing:
            raise ValueError(f"missing SmartRecruiters execution attributes: {', '.join(missing)}")
        if self.adapter_key != "ats:smartrecruiters":
            raise PermissionError("SmartRecruiters executor accepts only ats:smartrecruiters")
        if self.application_status != "To Apply":
            raise PermissionError("SmartRecruiters prepare execution requires To Apply")
        if self.readiness_confidence != "High":
            raise PermissionError("SmartRecruiters prepare execution requires High readiness confidence")
        if self.unresolved_required != 0:
            raise PermissionError("SmartRecruiters prepare execution requires zero unresolved required fields")
        if not self.field_plan:
            raise ValueError("field_plan must not be empty")
        if not self.upload_plan:
            raise ValueError("upload_plan must not be empty")
        if not self.control_resolutions:
            raise ValueError("control_resolutions must not be empty")
        if self.mode is not SmartRecruitersExecutionMode.PREPARE_ONLY:
            raise PermissionError("only prepare_only mode is supported")
        if self.max_navigation_steps < 1 or self.max_navigation_steps > 20:
            raise ValueError("max_navigation_steps must be between 1 and 20")
        for plan in self.field_plan:
            plan.validate()
        for plan in self.upload_plan:
            plan.validate()
