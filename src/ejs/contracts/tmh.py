from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

TMH_CONTRACT_VERSION = "TMH-1.0"
CANONICAL_SCHEMA_VERSION = "CANONICAL-APPLICATION-1.0"


def key_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def normalized_label(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9+/# ._-]", "", value)
    return value.strip()


def template_identity(adapter_family: str, employer_scope: str = "global") -> str:
    if not adapter_family.strip():
        raise ValueError("adapter_family is required")
    return f"tmpl:{key_part(adapter_family)}:{key_part(employer_scope or 'global')}"


def mapping_identity(
    *,
    label: str,
    control_type: str,
    context: str,
    canonical_field: str,
) -> str:
    if not all(v.strip() for v in (label, control_type, context, canonical_field)):
        raise ValueError("mapping identity components are required")
    payload = "|".join(
        [normalized_label(label), key_part(control_type), key_part(context), key_part(canonical_field)]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"map:{digest}"


def execution_identity(opportunity_key: str, requisition_id: str, attempt_no: int) -> str:
    if not opportunity_key.strip() or not requisition_id.strip():
        raise ValueError("opportunity_key and requisition_id are required")
    if attempt_no < 1:
        raise ValueError("attempt_no must be >= 1")
    return f"exec:{key_part(opportunity_key)}:{key_part(requisition_id)}:{attempt_no}"


def execution_event_key(execution_id: str, sequence: int, event_type: str) -> str:
    if not execution_id.strip() or not event_type.strip():
        raise ValueError("execution_id and event_type are required")
    if sequence < 1:
        raise ValueError("sequence must be >= 1")
    return f"execevent:{execution_id}:{sequence}:{key_part(event_type)}"


def artifact_key(execution_id: str, artifact_type: str, artifact_version_or_hash: str) -> str:
    if not all(v.strip() for v in (execution_id, artifact_type, artifact_version_or_hash)):
        raise ValueError("artifact key components are required")
    return f"artifact:{execution_id}:{key_part(artifact_type)}:{key_part(artifact_version_or_hash)}"


@dataclass(frozen=True)
class TmhAuthority:
    """TMH-1 only owns metadata/history surfaces.

    It has no browser, submit, Applications, User Fact, legal/consent or outcome authority.
    """

    template_registry_write: bool = True
    field_mapping_registry_write: bool = True
    execution_log_append: bool = True
    artifact_registry_append: bool = True
    canonical_schema_write: bool = True
    applications_write: bool = False
    external_form_write: bool = False
    final_submit: bool = False
    user_fact_write: bool = False

    def validate(self) -> None:
        if self.applications_write:
            raise PermissionError("TMH-1 forbids Applications writes")
        if self.external_form_write:
            raise PermissionError("TMH-1 forbids external-form writes")
        if self.final_submit:
            raise PermissionError("TMH-1 forbids final submit")
        if self.user_fact_write:
            raise PermissionError("TMH-1 forbids User Fact writes")
        if not all(
            (
                self.template_registry_write,
                self.field_mapping_registry_write,
                self.execution_log_append,
                self.artifact_registry_append,
                self.canonical_schema_write,
            )
        ):
            raise PermissionError("TMH-1 requires all metadata/history surfaces")
