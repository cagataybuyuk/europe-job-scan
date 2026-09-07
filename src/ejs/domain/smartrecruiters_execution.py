from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ejs.domain.file_upload import FileUploadResultItem
from ejs.domain.prefill import FieldWriteResult
from ejs.domain.submission import PreSubmitValidationResult


class SmartRecruitersExecutionState(str, Enum):
    PRE_SUBMIT_READY = "pre_submit_ready"
    VALIDATOR_BLOCKED = "validator_blocked"
    SCHEMA_DRIFT = "schema_drift"
    PLAN_BLOCKED = "plan_blocked"
    BOUNDARY = "boundary"
    READBACK_FAILED = "readback_failed"
    ACCESS_BLOCKED = "access_blocked"
    ERROR = "error"


@dataclass(frozen=True)
class SmartRecruitersExecutionResult:
    execution_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    runtime_state: SmartRecruitersExecutionState
    requested_url: str
    final_url: str
    ats_family: str
    observed_form_fingerprint: str
    final_form_fingerprint: str
    required_controls_observed: int
    resolved_required_controls: int
    field_results: tuple[FieldWriteResult, ...]
    file_results: tuple[FileUploadResultItem, ...]
    form_value_write_attempts: int
    file_upload_attempts: int
    submit_attempts: int
    submit_control_observed: bool
    browser_form_valid: bool
    validation: PreSubmitValidationResult | None
    review_gate_reached: bool
    error_code: str = ""
    error_message: str = ""

    @property
    def consequential_boundary_ok(self) -> bool:
        return self.submit_attempts == 0
