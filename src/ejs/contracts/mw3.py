from __future__ import annotations

from dataclasses import dataclass

from ejs.domain.append_only import AppendOnlyDomain


@dataclass(frozen=True)
class Mw3Authority:
    enabled: bool
    allowed_domains: frozenset[AppendOnlyDomain]
    business_state_writes_enabled: bool = False
    external_form_writes_enabled: bool = False

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.business_state_writes_enabled:
            raise PermissionError("MW-3 cannot enable production business-state writes")
        if self.external_form_writes_enabled:
            raise PermissionError("MW-3 cannot enable external form writes")
        forbidden = {
            domain
            for domain in self.allowed_domains
            if domain not in {
                AppendOnlyDomain.SERVICE_CANARY_AUDIT,
                AppendOnlyDomain.SOURCE_HEALTH_LOG,
                AppendOnlyDomain.DISCOVERY_FUNNEL_LOG,
                AppendOnlyDomain.OUTCOME_EVENT_LOG,
            }
        }
        if forbidden:
            raise PermissionError(f"Unsupported MW-3 append-only domains: {sorted(forbidden)}")


def source_run_idempotency_key(run_id: str, source_key: str) -> str:
    if not run_id or not source_key:
        raise ValueError("run_id and source_key are required")
    return f"run:{run_id}:{source_key}"


def service_canary_idempotency_key(run_id: str, domain: str) -> str:
    if not run_id or not domain:
        raise ValueError("run_id and domain are required")
    return f"canary:MW-3:{run_id}:{domain}"
