from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib

PREFILL_EXEC_VERSION = "PREFILL-EXEC-0.2"
BE2_WRITER_VERSION = "BE-2.0"


class PrefillMode(str, Enum):
    PREFILL_NON_SENSITIVE = "prefill_non_sensitive"


class FieldOwnership(str, Enum):
    AUTO_SAFE = "AUTO_SAFE"
    DYNAMIC_AI = "DYNAMIC_AI"
    USER_FACT = "USER_FACT"
    USER_ONLY = "USER_ONLY"
    REVIEW_GATED = "REVIEW_GATED"


class ResolverStatus(str, Enum):
    RESOLVED = "Resolved"
    OPTIONAL_RESOLVED = "Optional Resolved"
    RESOLVED_REVIEW = "Resolved-Review"
    PENDING_USER = "Pending User"
    USER_ONLY = "USER-only"
    UNRESOLVED = "Unresolved"


class MappingConfidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    UNKNOWN = "Unknown"


SAFE_VERIFIED_FIELDS = frozenset(
    {
        "candidate.first_name",
        "candidate.last_name",
        "candidate.email",
        "candidate.phone",
        "candidate.linkedin_url",
        "employment.notice_period",
        "application.referral",
    }
)

DYNAMIC_AI_FIELDS = frozenset(
    {
        "application.motivation_text",
    }
)

HARD_FORBIDDEN_PREFIXES = (
    "work.",
    "compensation.",
    "consent.",
    "demographic.",
    "document.",
    "technical.",
)

HARD_FORBIDDEN_FIELDS = frozenset(
    {
        "application.final_submit",
        "experience.relevant_years",
        "employment.earliest_start_date",
    }
)

SAFE_CONTROL_TYPES = frozenset({"text", "email", "tel", "url", "textarea", "select"})


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class SafeFieldWriterAuthority:
    """BE-2 grants only bounded non-sensitive value-write authority.

    Upload, credentials, consent, CAPTCHA and final-submit remain structurally disabled.
    """

    mode: PrefillMode = PrefillMode.PREFILL_NON_SENSITIVE
    form_value_write: bool = True
    file_upload: bool = False
    credential_entry: bool = False
    consent_action: bool = False
    protected_attribute_action: bool = False
    captcha_bypass: bool = False
    final_submit: bool = False

    def validate(self) -> None:
        if self.mode is not PrefillMode.PREFILL_NON_SENSITIVE:
            raise PermissionError("BE-2 only supports prefill_non_sensitive")
        if not self.form_value_write:
            raise PermissionError("BE-2 requires bounded form_value_write authority")
        forbidden = {
            "file_upload": self.file_upload,
            "credential_entry": self.credential_entry,
            "consent_action": self.consent_action,
            "protected_attribute_action": self.protected_attribute_action,
            "captcha_bypass": self.captcha_bypass,
            "final_submit": self.final_submit,
        }
        enabled = [name for name, value in forbidden.items() if value]
        if enabled:
            raise PermissionError(f"BE-2 forbidden authorities enabled: {', '.join(enabled)}")


@dataclass(frozen=True)
class PrefillFieldPlan:
    field_plan_key: str
    control_key: str
    canonical_field: str
    control_type: str
    value: str
    provenance_ref: str
    ownership: FieldOwnership
    resolver_status: ResolverStatus
    mapping_confidence: MappingConfidence
    review_required: bool = False
    required: bool = False

    @property
    def value_hash(self) -> str:
        return value_hash(self.value)

    def validate(self) -> None:
        missing = [
            name
            for name, val in {
                "field_plan_key": self.field_plan_key,
                "control_key": self.control_key,
                "canonical_field": self.canonical_field,
                "control_type": self.control_type,
                "value": self.value,
                "provenance_ref": self.provenance_ref,
            }.items()
            if not str(val).strip()
        ]
        if missing:
            raise ValueError(f"missing field plan attributes: {', '.join(missing)}")


@dataclass(frozen=True)
class PrefillExecutionRequest:
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
    field_plan: tuple[PrefillFieldPlan, ...]
    mode: PrefillMode = PrefillMode.PREFILL_NON_SENSITIVE
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
        }
        missing = [k for k, v in required.items() if not str(v).strip()]
        if missing:
            raise ValueError(f"missing prefill request attributes: {', '.join(missing)}")
        if self.mode is not PrefillMode.PREFILL_NON_SENSITIVE:
            raise ValueError("BE-2 request mode must be prefill_non_sensitive")
        if self.application_status != "To Apply":
            raise PermissionError("BE-2 only executes on To Apply opportunities")
        if self.readiness_confidence != "High":
            raise PermissionError("BE-2 requires High readiness confidence")
        if self.unresolved_required != 0:
            raise PermissionError("BE-2 requires zero unresolved required fields")
        if not self.field_plan:
            raise ValueError("field_plan must not be empty")
        if self.max_navigation_steps < 1 or self.max_navigation_steps > 20:
            raise ValueError("max_navigation_steps must be between 1 and 20")
        if not (
            self.application_url.startswith("https://")
            or self.application_url.startswith("http://127.0.0.1")
            or self.application_url.startswith("http://localhost")
            or self.application_url.startswith("file://")
        ):
            raise ValueError("application_url must be HTTPS or explicit local fixture URL")
        keys = [p.field_plan_key for p in self.field_plan]
        controls = [p.control_key for p in self.field_plan]
        if len(keys) != len(set(keys)):
            raise ValueError("field_plan_key values must be unique")
        if len(controls) != len(set(controls)):
            raise ValueError("one BE-2 execution may target each control at most once")
        for plan in self.field_plan:
            plan.validate()
