from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AppendOnlyDomain(StrEnum):
    SERVICE_CANARY_AUDIT = "service_canary_audit"
    SOURCE_HEALTH_LOG = "source_health_log"
    DISCOVERY_FUNNEL_LOG = "discovery_funnel_log"
    OUTCOME_EVENT_LOG = "outcome_event_log"


@dataclass(frozen=True)
class AppendOnlyEvent:
    domain: AppendOnlyDomain
    idempotency_key: str
    run_id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class AppendResult:
    idempotency_key: str
    appended: bool
    duplicate_suppressed: bool
    reconciliation_gap: int = 0
