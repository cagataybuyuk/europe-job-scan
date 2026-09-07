from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

GOOGLE_NATIVE_CONTRACT_VERSION = "EJS-GN-1.0"


class ExecutionEnvironment(str, Enum):
    TEST = "TEST"


class ExecutionOperation(str, Enum):
    ECHO = "echo"
    INSPECT_FIXTURE = "inspect_fixture"
    INSPECT_READ_ONLY = "inspect_read_only"


@dataclass(frozen=True)
class ExecutionAuthority:
    browser_read: bool = True
    form_write: bool = False
    file_upload: bool = False
    credential_entry: bool = False
    consent_action: bool = False
    captcha_bypass: bool = False
    final_submit: bool = False

    def validate(self) -> None:
        if not self.browser_read:
            raise PermissionError("GD-002 requires browser_read authority")
        forbidden = {
            "form_write": self.form_write,
            "file_upload": self.file_upload,
            "credential_entry": self.credential_entry,
            "consent_action": self.consent_action,
            "captcha_bypass": self.captcha_bypass,
            "final_submit": self.final_submit,
        }
        enabled = [k for k, v in forbidden.items() if v]
        if enabled:
            raise PermissionError(f"GD-002 forbidden authorities enabled: {', '.join(enabled)}")


_SENSITIVE_TRACE_KEYS = {
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "cookie", "cookies", "authorization", "private_key", "api_key",
}


@dataclass(frozen=True)
class GoogleNativeExecutionRequest:
    request_id: str
    execution_id: str
    operation: ExecutionOperation
    environment: ExecutionEnvironment = ExecutionEnvironment.TEST
    contract_version: str = GOOGLE_NATIVE_CONTRACT_VERSION
    target_url: str = ""
    opportunity_key: str = ""
    requisition_id: str = ""
    expected_tracker_fingerprint: str = ""
    authority: ExecutionAuthority = field(default_factory=ExecutionAuthority)
    trace: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.contract_version != GOOGLE_NATIVE_CONTRACT_VERSION:
            raise ValueError("unsupported google-native execution contract version")
        if self.environment is not ExecutionEnvironment.TEST:
            raise PermissionError("GD-002 only permits TEST environment")
        if not self.request_id.strip() or not self.execution_id.strip():
            raise ValueError("request_id and execution_id are required")
        self.authority.validate()
        if self.operation is ExecutionOperation.INSPECT_READ_ONLY and not self.target_url.strip():
            raise ValueError("inspect_read_only requires target_url")
        if self.operation is ExecutionOperation.INSPECT_FIXTURE and self.target_url.strip():
            raise ValueError("inspect_fixture does not accept caller-controlled target_url")
        normalized = {str(k).strip().lower() for k in self.trace.keys()}
        forbidden = sorted(normalized.intersection(_SENSITIVE_TRACE_KEYS))
        if forbidden:
            raise ValueError(f"sensitive trace keys are forbidden: {', '.join(forbidden)}")


@dataclass(frozen=True)
class GoogleNativeExecutionResult:
    request_id: str
    execution_id: str
    operation: str
    contract_version: str
    runtime_state: str
    observed_at: str
    final_url: str = ""
    ats_family: str = ""
    browser_engine: str = ""
    browser_version: str = ""
    page_fingerprint: str = ""
    form_fingerprint: str = ""
    controls: int = 0
    required_controls: int = 0
    action_controls: int = 0
    captcha_state: str = ""
    auth_boundary_type: str = ""
    error_code: str = ""
    error_message: str = ""
    mutation_attempts: int = 0
    file_upload_attempts: int = 0
    submit_attempts: int = 0
    read_only_invariant_ok: bool = True
