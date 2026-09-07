from __future__ import annotations

from dataclasses import dataclass


DERIVED_READINESS_COLUMNS = frozenset({
    "readiness_rule_version",
    "blocking_review",
    "unresolved_required",
    "readiness_count_confidence",
    "derived_fill_pack_status",
    "readiness_last_evaluated",
})


@dataclass(frozen=True)
class Mw4aAuthority:
    enabled: bool = True
    production_derived_writes_enabled: bool = False
    business_state_writes_enabled: bool = False
    external_form_writes_enabled: bool = False

    def validate(self) -> None:
        if self.production_derived_writes_enabled:
            raise PermissionError("MW-4A is shadow/parity only; derived production writes belong to MW-4B")
        if self.business_state_writes_enabled:
            raise PermissionError("MW-4A cannot mutate Applications or pipeline business state")
        if self.external_form_writes_enabled:
            raise PermissionError("MW-4A cannot write external forms")


@dataclass(frozen=True)
class Mw4bAuthority:
    enabled: bool = True
    production_derived_writes_enabled: bool = True
    allowed_fields: frozenset[str] = DERIVED_READINESS_COLUMNS
    business_state_writes_enabled: bool = False
    user_fact_writes_enabled: bool = False
    external_form_writes_enabled: bool = False

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.production_derived_writes_enabled:
            raise PermissionError("MW-4B canary requires derived-write flag to be enabled")
        if self.business_state_writes_enabled:
            raise PermissionError("MW-4B cannot mutate Applications or pipeline business state")
        if self.user_fact_writes_enabled:
            raise PermissionError("MW-4B cannot mutate user-owned facts")
        if self.external_form_writes_enabled:
            raise PermissionError("MW-4B cannot write external application forms")
        unsupported = set(self.allowed_fields) - DERIVED_READINESS_COLUMNS
        if unsupported:
            raise PermissionError(f"MW-4B unsupported derived fields: {sorted(unsupported)}")


def readiness_write_idempotency_key(run_id: str, company: str, role: str) -> str:
    if not run_id or not company or not role:
        raise ValueError("run_id, company and role are required")
    normalize = lambda value: "-".join(value.strip().lower().split())
    return f"mw4b:readiness:{run_id}:{normalize(company)}:{normalize(role)}"
