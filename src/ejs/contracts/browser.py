from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

BROWSER_WORKER_VERSION = "BE-1.0"
BROWSER_BRIDGE_VERSION = "BROWSER-BRIDGE-0.1"


class BrowserMode(str, Enum):
    INSPECT_READ_ONLY = "inspect_read_only"


class RuntimeState(str, Enum):
    RENDERED = "rendered"
    AUTH_BOUNDARY = "auth_boundary"
    CAPTCHA_BOUNDARY = "captcha_boundary"
    ACCESS_BLOCKED = "access_blocked"
    EXPIRED = "expired"
    NAVIGATION_ERROR = "navigation_error"
    UNSUPPORTED = "unsupported"


class DomSnapshotState(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CaptchaState(str, Enum):
    NONE_OBSERVED = "none_observed"
    PRESENT = "present"
    UNKNOWN = "unknown"


class AuthBoundaryType(str, Enum):
    NONE = "none"
    SIGN_IN = "sign_in"
    CREATE_ACCOUNT = "create_account"
    SESSION_REQUIRED = "session_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrowserWorkerAuthority:
    """BE-1 is structurally incapable of candidate form mutation.

    Write capabilities intentionally remain false and are validated before the browser launches.
    Later BE waves must introduce a new authority contract instead of mutating this one.
    """

    mode: BrowserMode = BrowserMode.INSPECT_READ_ONLY
    navigation_read: bool = True
    dom_read: bool = True
    form_value_write: bool = False
    file_upload: bool = False
    credential_entry: bool = False
    consent_action: bool = False
    captcha_bypass: bool = False
    final_submit: bool = False

    def validate(self) -> None:
        if self.mode is not BrowserMode.INSPECT_READ_ONLY:
            raise PermissionError("BE-1 only supports inspect_read_only")
        if not self.navigation_read or not self.dom_read:
            raise PermissionError("BE-1 requires navigation and DOM-read authority")
        forbidden = {
            "form_value_write": self.form_value_write,
            "file_upload": self.file_upload,
            "credential_entry": self.credential_entry,
            "consent_action": self.consent_action,
            "captcha_bypass": self.captcha_bypass,
            "final_submit": self.final_submit,
        }
        enabled = [name for name, value in forbidden.items() if value]
        if enabled:
            raise PermissionError(f"BE-1 forbidden browser authorities enabled: {', '.join(enabled)}")


@dataclass(frozen=True)
class BrowserInspectionRequest:
    bridge_request_id: str
    route_key: str
    opportunity_key: str
    requisition_id: str
    application_url: str
    adapter_key: str
    observed_at: str
    mode: BrowserMode = BrowserMode.INSPECT_READ_ONLY
    session_policy: str = "no_session"
    max_navigation_steps: int = 6

    def validate(self) -> None:
        required = {
            "bridge_request_id": self.bridge_request_id,
            "route_key": self.route_key,
            "opportunity_key": self.opportunity_key,
            "requisition_id": self.requisition_id,
            "application_url": self.application_url,
            "adapter_key": self.adapter_key,
            "observed_at": self.observed_at,
        }
        missing = [k for k, v in required.items() if not str(v).strip()]
        if missing:
            raise ValueError(f"missing browser request fields: {', '.join(missing)}")
        if self.mode is not BrowserMode.INSPECT_READ_ONLY:
            raise ValueError("BE-1 request mode must be inspect_read_only")
        if self.max_navigation_steps < 1 or self.max_navigation_steps > 20:
            raise ValueError("max_navigation_steps must be between 1 and 20")
        if not (
            self.application_url.startswith("https://")
            or self.application_url.startswith("http://127.0.0.1")
            or self.application_url.startswith("http://localhost")
            or self.application_url.startswith("file://")
        ):
            raise ValueError("application_url must be HTTPS or an explicit local BE-1 fixture URL")
