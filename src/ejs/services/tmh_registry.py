from __future__ import annotations

from dataclasses import replace

from ejs.contracts.tmh import TmhAuthority
from ejs.domain.template_history import (
    DriftState,
    FieldMappingRecord,
    MappingConfidence,
    PromotionState,
    RuntimeFormObservation,
    TemplateVersionRecord,
    drift_state,
    mapping_promotion_state,
)
from ejs.persistence.tmh_memory import InMemoryTmhRepository


MIN_DYNAMIC_MAPPING_CONFIDENCE = MappingConfidence.HIGH


def register_template(
    repo: InMemoryTmhRepository,
    record: TemplateVersionRecord,
    authority: TmhAuthority,
) -> bool:
    authority.validate()
    if record.evidence_count < 0:
        raise ValueError("evidence_count cannot be negative")
    if not record.template_id or not record.version:
        raise ValueError("template_id and version are required")
    return repo.append_template(record)


def register_mapping(
    repo: InMemoryTmhRepository,
    record: FieldMappingRecord,
    authority: TmhAuthority,
) -> bool:
    authority.validate()
    if record.evidence_count < 1:
        raise ValueError("mapping requires at least one evidence observation")
    if record.confidence == MappingConfidence.LOW and record.promotion_state in {
        PromotionState.PROMOTED,
        PromotionState.REVIEWED,
    }:
        raise ValueError("low-confidence mapping cannot be promoted")
    return repo.append_mapping(record)


def promotion_candidate(record: FieldMappingRecord, *, explicitly_reviewed: bool = False) -> FieldMappingRecord:
    state = mapping_promotion_state(
        evidence_count=record.evidence_count,
        explicitly_reviewed=explicitly_reviewed,
    )
    if record.confidence != MappingConfidence.HIGH and state == PromotionState.PROMOTED:
        state = PromotionState.CANDIDATE
    return replace(record, promotion_state=state)


def runtime_drift_gate(
    *,
    template: TemplateVersionRecord | None,
    observation: RuntimeFormObservation,
) -> tuple[DriftState, bool, str]:
    state = drift_state(template, observation)
    if state == DriftState.DRIFT:
        return state, False, "live form fingerprint differs from stored template; re-inspection required"
    if state == DriftState.NO_BASELINE:
        return state, True, "no template baseline; live runtime observation remains authoritative"
    return state, True, "live form matches current template fingerprint"


def dynamic_mapping_allowed(confidence: MappingConfidence, required: bool) -> bool:
    if confidence == MappingConfidence.HIGH:
        return True
    if required:
        return False
    return confidence == MappingConfidence.MEDIUM
