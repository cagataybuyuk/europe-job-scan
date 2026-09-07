from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FileUploadExecutionState(str, Enum):
    REVIEW_GATE = "review_gate"
    SCHEMA_DRIFT = "schema_drift"
    PLAN_BLOCKED = "plan_blocked"
    READBACK_FAILED = "readback_failed"
    BOUNDARY = "boundary"
    ACCESS_BLOCKED = "access_blocked"
    ERROR = "error"


class FileUploadStatus(str, Enum):
    UPLOADED_VERIFIED = "uploaded_verified"
    ALREADY_ATTACHED = "already_attached"
    BLOCKED = "blocked"
    READBACK_MISMATCH = "readback_mismatch"


@dataclass(frozen=True)
class FileReadback:
    file_name: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class FileUploadResultItem:
    upload_plan_key: str
    upload_key: str
    control_key: str
    canonical_field: str
    artifact_type: str
    artifact_version: str
    content_hash: str
    expected_file_name: str
    expected_size_bytes: int
    expected_mime_type: str
    readback_file_name: str
    readback_size_bytes: int
    readback_mime_type: str
    readback_sha256: str
    status: FileUploadStatus
    upload_executed: bool
    artifact_appended: bool
    artifact_duplicate_suppressed: bool
    reason: str = ""


@dataclass(frozen=True)
class FileUploadExecutionResult:
    execution_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    runtime_state: FileUploadExecutionState
    requested_url: str
    final_url: str
    ats_family: str
    expected_form_fingerprint: str
    observed_form_fingerprint: str
    final_form_fingerprint: str
    item_results: tuple[FileUploadResultItem, ...]
    planned_files: int
    verified_files: int
    already_attached_files: int
    file_upload_attempts: int
    form_value_write_attempts: int
    submit_attempts: int
    exact_readback_all_match: bool
    review_gate_reached: bool
    error_code: str = ""
    error_message: str = ""

    @property
    def consequential_boundary_ok(self) -> bool:
        return self.form_value_write_attempts == 0 and self.submit_attempts == 0
