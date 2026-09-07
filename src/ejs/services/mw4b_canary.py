from __future__ import annotations

from dataclasses import dataclass

from ejs.contracts.mw4 import Mw4bAuthority, readiness_write_idempotency_key
from ejs.domain.derived_state import ReadinessProjection


@dataclass(frozen=True)
class DerivedWriteSnapshot:
    company: str
    role: str
    application_status: str
    rule_version: str
    blocking_review: int
    unresolved_required: int
    confidence: str
    derived_status: str
    last_evaluated: str


@dataclass(frozen=True)
class DerivedWritePlan:
    idempotency_key: str
    should_write: bool
    reason: str
    values: dict[str, object]


def plan_readiness_write(
    *,
    run_id: str,
    projection: ReadinessProjection,
    current: DerivedWriteSnapshot,
    blocking_review: int,
    unresolved_required: int,
    evaluated_date: str,
    authority: Mw4bAuthority,
) -> DerivedWritePlan:
    authority.validate()
    key = readiness_write_idempotency_key(run_id, projection.company, projection.role)

    if current.application_status != "To Apply":
        return DerivedWritePlan(key, False, "application state is no longer To Apply", {})
    if projection.critical_divergence:
        return DerivedWritePlan(key, False, "critical parity divergence", {})
    if not projection.write_eligible or projection.confidence != "High":
        return DerivedWritePlan(key, False, "projection is not High-confidence write eligible", {})

    target = {
        "readiness_rule_version": "READINESS-1.0",
        "blocking_review": blocking_review,
        "unresolved_required": unresolved_required,
        "readiness_count_confidence": "High",
        "derived_fill_pack_status": projection.derived_status.value,
        "readiness_last_evaluated": evaluated_date,
    }
    current_values = {
        "readiness_rule_version": current.rule_version,
        "blocking_review": current.blocking_review,
        "unresolved_required": current.unresolved_required,
        "readiness_count_confidence": current.confidence,
        "derived_fill_pack_status": current.derived_status,
        "readiness_last_evaluated": current.last_evaluated,
    }
    if current_values == target:
        return DerivedWritePlan(key, False, "duplicate_suppressed: target projection already materialized", target)
    return DerivedWritePlan(key, True, "bounded High-confidence derived-state canary write", target)
