from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json

from ejs.contracts.tmh import normalized_label


class MappingConfidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class PromotionState(str, Enum):
    RUNTIME_ONLY = "Runtime Only"
    CANDIDATE = "Promotion Candidate"
    PROMOTED = "Promoted"
    REVIEWED = "Reviewed / Promoted"
    SUPERSEDED = "Superseded"


class DriftState(str, Enum):
    NO_BASELINE = "No Baseline"
    MATCH = "Match"
    DRIFT = "Drift"


@dataclass(frozen=True)
class CanonicalFieldDefinition:
    field_key: str
    category: str
    data_type: str
    ownership_mode: str
    source_of_truth: str
    auto_fill_allowed: bool
    auto_submit_eligible: bool
    review_requirement: str
    reuse_scope: str
    null_behavior: str
    schema_version: str
    status: str = "Active"
    notes: str = ""


@dataclass(frozen=True)
class TemplateVersionRecord:
    template_id: str
    adapter_family: str
    employer_scope: str
    template_type: str
    version: str
    status: str
    detection_pattern: str = ""
    navigation_pattern: str = ""
    file_upload_pattern: str = ""
    submit_pattern: str = ""
    known_custom_fields: tuple[str, ...] = field(default_factory=tuple)
    evidence_count: int = 0
    first_observed: str = ""
    last_validated: str = ""
    supersedes: str = ""
    baseline_fingerprint: str = ""
    notes: str = ""

    @property
    def version_key(self) -> str:
        return f"{self.template_id}@{self.version}"


@dataclass(frozen=True)
class FieldMappingRecord:
    mapping_id: str
    normalized_label_value: str
    control_type: str
    context: str
    employer_scope: str
    canonical_field: str
    mapping_version: str
    confidence: MappingConfidence
    status: str
    provenance: str
    evidence_count: int
    first_observed: str
    last_observed: str
    promotion_state: PromotionState
    supersedes: str = ""
    notes: str = ""

    @property
    def version_key(self) -> str:
        return f"{self.mapping_id}@{self.mapping_version}"


@dataclass(frozen=True)
class ObservedControl:
    label: str
    control_type: str
    required: bool
    step: int = 1
    options: tuple[str, ...] = field(default_factory=tuple)
    semantic_hint: str = ""

    def canonical_payload(self) -> dict[str, object]:
        return {
            "label": normalized_label(self.label),
            "control_type": self.control_type.strip().lower(),
            "required": bool(self.required),
            "step": int(self.step),
            "options": [normalized_label(x) for x in self.options],
            "semantic_hint": normalized_label(self.semantic_hint),
        }


@dataclass(frozen=True)
class RuntimeFormObservation:
    opportunity_key: str
    requisition_id: str
    apply_url: str
    ats_family: str
    observed_at: str
    controls: tuple[ObservedControl, ...]
    step_count: int = 1

    @property
    def fingerprint(self) -> str:
        payload = {
            "ats_family": self.ats_family.strip().lower(),
            "step_count": self.step_count,
            "controls": [c.canonical_payload() for c in self.controls],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ExecutionEvent:
    execution_id: str
    sequence: int
    occurred_at: str
    opportunity_key: str
    requisition_id: str
    attempt_no: int
    event_type: str
    discovery_source: str = ""
    apply_url: str = ""
    ats_family: str = ""
    adapter_version: str = ""
    template_id: str = ""
    template_version: str = ""
    employer_override: str = ""
    form_fingerprint: str = ""
    mapping_snapshot_ref: str = ""
    answer_snapshot_ref: str = ""
    policy_version: str = ""
    submit_key: str = ""
    result: str = ""
    drift_state: DriftState = DriftState.NO_BASELINE
    reconciliation_state: str = ""
    evidence_ref: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SubmissionArtifact:
    execution_id: str
    artifact_type: str
    artifact_version: str
    content_hash: str
    source_ref: str
    asset_ref: str = ""
    policy_version: str = ""
    template_snapshot_ref: str = ""
    mapping_snapshot_ref: str = ""
    answer_snapshot_ref: str = ""
    confirmation_evidence_ref: str = ""
    captured_at: str = ""
    notes: str = ""


def drift_state(template: TemplateVersionRecord | None, observation: RuntimeFormObservation) -> DriftState:
    if template is None or not template.baseline_fingerprint:
        return DriftState.NO_BASELINE
    if template.baseline_fingerprint == observation.fingerprint:
        return DriftState.MATCH
    return DriftState.DRIFT


def mapping_promotion_state(*, evidence_count: int, explicitly_reviewed: bool) -> PromotionState:
    if explicitly_reviewed:
        return PromotionState.REVIEWED
    if evidence_count >= 3:
        return PromotionState.PROMOTED
    if evidence_count >= 2:
        return PromotionState.CANDIDATE
    return PromotionState.RUNTIME_ONLY
