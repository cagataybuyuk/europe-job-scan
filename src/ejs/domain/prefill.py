from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PrefillExecutionState(str, Enum):
    REVIEW_GATE = "review_gate"
    SCHEMA_DRIFT = "schema_drift"
    BOUNDARY = "boundary"
    PLAN_BLOCKED = "plan_blocked"
    READBACK_FAILED = "readback_failed"
    ACCESS_BLOCKED = "access_blocked"
    ERROR = "error"


class FieldWriteStatus(str, Enum):
    WRITTEN_VERIFIED = "written_verified"
    ALREADY_MATCHED = "already_matched"
    BLOCKED = "blocked"
    READBACK_MISMATCH = "readback_mismatch"


@dataclass(frozen=True)
class FieldWriteResult:
    field_plan_key: str
    control_key: str
    canonical_field: str
    control_type: str
    value_hash: str
    readback_hash: str
    status: FieldWriteStatus
    mutation_executed: bool
    reason: str = ""


@dataclass(frozen=True)
class PrefillExecutionResult:
    execution_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    runtime_state: PrefillExecutionState
    requested_url: str
    final_url: str
    ats_family: str
    expected_form_fingerprint: str
    observed_form_fingerprint: str
    final_form_fingerprint: str
    field_results: tuple[FieldWriteResult, ...]
    planned_fields: int
    verified_fields: int
    already_matched_fields: int
    form_value_write_attempts: int
    file_upload_attempts: int
    submit_attempts: int
    readback_all_match: bool
    review_gate_reached: bool
    error_code: str = ""
    error_message: str = ""

    @property
    def consequential_boundary_ok(self) -> bool:
        return self.file_upload_attempts == 0 and self.submit_attempts == 0
