from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from ejs.contracts.prefill import MappingConfidence

FILE_UPLOAD_VERSION = "FILE-UPLOAD-0.1"
FILE1_RUNTIME_VERSION = "FILE-1.0"
MAX_FILE_UPLOAD_BYTES = 10 * 1024 * 1024


class ArtifactType(str, Enum):
    CV = "cv"
    COVER_LETTER = "cover_letter"


ALLOWED_DOCUMENT_FIELDS = frozenset({"document.cv", "document.cover_letter"})
ALLOWED_MIME_TYPES = frozenset({"application/pdf"})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_part(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())
    return value.strip("-") or "unknown"


def upload_key(execution_id: str, control_key: str, content_hash: str) -> str:
    if not all(str(v).strip() for v in (execution_id, control_key, content_hash)):
        raise ValueError("upload key components are required")
    return f"upload:{key_part(execution_id)}:{key_part(control_key)}:{content_hash.lower()}"


@dataclass(frozen=True)
class FileUploadAuthority:
    """FILE-1 grants only bounded approved-document attachment authority."""

    file_upload: bool = True
    form_value_write: bool = False
    credential_entry: bool = False
    consent_action: bool = False
    protected_attribute_action: bool = False
    captcha_bypass: bool = False
    final_submit: bool = False

    def validate(self) -> None:
        if not self.file_upload:
            raise PermissionError("FILE-1 requires file_upload authority")
        forbidden = {
            "form_value_write": self.form_value_write,
            "credential_entry": self.credential_entry,
            "consent_action": self.consent_action,
            "protected_attribute_action": self.protected_attribute_action,
            "captcha_bypass": self.captcha_bypass,
            "final_submit": self.final_submit,
        }
        enabled = [name for name, value in forbidden.items() if value]
        if enabled:
            raise PermissionError(f"FILE-1 forbidden authorities enabled: {', '.join(enabled)}")


@dataclass(frozen=True)
class ApprovedFileAsset:
    artifact_type: ArtifactType
    artifact_version: str
    source_ref: str
    asset_ref: str
    local_path: str
    expected_file_name: str
    expected_mime_type: str
    expected_sha256: str
    expected_size_bytes: int
    approved: bool = True

    def validate(self) -> None:
        required = {
            "artifact_version": self.artifact_version,
            "source_ref": self.source_ref,
            "asset_ref": self.asset_ref,
            "local_path": self.local_path,
            "expected_file_name": self.expected_file_name,
            "expected_mime_type": self.expected_mime_type,
            "expected_sha256": self.expected_sha256,
        }
        missing = [k for k, v in required.items() if not str(v).strip()]
        if missing:
            raise ValueError(f"missing asset attributes: {', '.join(missing)}")
        if not self.approved:
            raise PermissionError("FILE-1 may upload only explicitly approved assets")
        if self.expected_mime_type not in ALLOWED_MIME_TYPES:
            raise PermissionError(f"unsupported FILE-1 MIME type: {self.expected_mime_type}")
        if self.expected_size_bytes <= 0 or self.expected_size_bytes > MAX_FILE_UPLOAD_BYTES:
            raise PermissionError("asset size is outside FILE-1 bounds")
        digest = self.expected_sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("expected_sha256 must be a 64-character lowercase/uppercase hex digest")


@dataclass(frozen=True)
class FileUploadPlan:
    upload_plan_key: str
    control_key: str
    canonical_field: str
    asset: ApprovedFileAsset
    provenance_ref: str
    mapping_confidence: MappingConfidence = MappingConfidence.HIGH
    required: bool = True

    def validate(self) -> None:
        if not all(str(v).strip() for v in (self.upload_plan_key, self.control_key, self.canonical_field, self.provenance_ref)):
            raise ValueError("upload plan identity/provenance attributes are required")
        if self.canonical_field not in ALLOWED_DOCUMENT_FIELDS:
            raise PermissionError(f"FILE-1 may upload only approved document fields: {self.canonical_field}")
        if self.mapping_confidence is not MappingConfidence.HIGH:
            raise PermissionError("FILE-1 requires High-confidence file-control mapping")
        expected_type = ArtifactType.CV if self.canonical_field == "document.cv" else ArtifactType.COVER_LETTER
        if self.asset.artifact_type is not expected_type:
            raise ValueError("asset artifact_type does not match canonical document field")
        self.asset.validate()


@dataclass(frozen=True)
class FileUploadExecutionRequest:
    execution_id: str
    bridge_request_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    application_url: str
    adapter_key: str
    observed_at: str
    expected_form_fingerprint: str
    application_status: str
    readiness_confidence: str
    unresolved_required: int
    upload_plan: tuple[FileUploadPlan, ...]
    policy_version: str = "SUBMIT-1.0"
    max_navigation_steps: int = 6

    def validate(self) -> None:
        required = {
            "execution_id": self.execution_id,
            "bridge_request_id": self.bridge_request_id,
            "route_key": self.route_key,
            "opportunity_key": self.opportunity_key,
            "requisition_id": self.requisition_id,
            "application_url": self.application_url,
            "adapter_key": self.adapter_key,
            "observed_at": self.observed_at,
            "expected_form_fingerprint": self.expected_form_fingerprint,
            "policy_version": self.policy_version,
        }
        missing = [k for k, v in required.items() if not str(v).strip()]
        if missing:
            raise ValueError(f"missing file-upload request attributes: {', '.join(missing)}")
        if self.application_status != "To Apply":
            raise PermissionError("FILE-1 only executes on To Apply opportunities")
        if self.readiness_confidence != "High":
            raise PermissionError("FILE-1 requires High readiness confidence")
        if self.unresolved_required != 0:
            raise PermissionError("FILE-1 requires zero unresolved required fields")
        if not self.upload_plan:
            raise ValueError("upload_plan must not be empty")
        if self.max_navigation_steps < 1 or self.max_navigation_steps > 20:
            raise ValueError("max_navigation_steps must be between 1 and 20")
        if not (
            self.application_url.startswith("https://")
            or self.application_url.startswith("http://127.0.0.1")
            or self.application_url.startswith("http://localhost")
            or self.application_url.startswith("file://")
        ):
            raise ValueError("application_url must be HTTPS or explicit local fixture URL")
        plan_keys = [p.upload_plan_key for p in self.upload_plan]
        control_keys = [p.control_key for p in self.upload_plan]
        if len(plan_keys) != len(set(plan_keys)):
            raise ValueError("upload_plan_key values must be unique")
        if len(control_keys) != len(set(control_keys)):
            raise ValueError("one FILE-1 execution may target each file control at most once")
        for plan in self.upload_plan:
            plan.validate()
