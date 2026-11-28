"""Offline admission composition under the W36-02 standing batch binding.

Recompiles the manifest from envelope-pinned inputs and requires byte equality
with the supplied manifest. It also recomputes W36-03 eligibility from the
pinned inventory. It reuses the existing offline compiler and verifiers and
does not use the 24-hour current-fact approval record. No I/O, and no
production authority: runtime wiring and cap reservation are a later slice.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json

from ejs.services.execution_manifest import (
    MAX_MANIFEST_BYTES, _TARGET_KEYS, _utc, load_execution_manifest,
)
from ejs.services.inventory_eligibility import bind_f05_vacancy, evaluate_inventory_eligibility
from ejs.services.reviewed_manifest_compiler import compile_reviewed_manifest
from ejs.services.standing_envelope import (
    _ENVELOPE_KEYS, _INDEX_KEYS, ENVELOPE_SCHEMA, INDEX_SCHEMA, BatchApprovalSources,
    BatchBinding, BatchInputs, _record, validate_standing_batch,
)

ERROR = "STANDING_ADMISSION_REVIEW_REQUIRED"


@dataclass(frozen=True, repr=False)
class AdmissionEvidence:
    """Bytes pinned by the indexed eligibility package, not by the envelope."""
    snapshot: bytes
    handoff: bytes
    country_attestation: bytes


@dataclass(frozen=True, repr=False)
class AdmissionDecision:
    """Offline verdict for one manifest; not a reusable token or execution grant."""
    binding: BatchBinding
    execution_id: str
    manifest_sha256: str


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _admit(sources: BatchApprovalSources, inputs: BatchInputs, evidence: AdmissionEvidence,
           now: datetime) -> AdmissionDecision:
    _require(isinstance(evidence, AdmissionEvidence))
    binding = validate_standing_batch(sources, inputs, now=now)
    # S1 has pinned these bytes to the trusted pointer; reparse for the values.
    envelope = _record(sources.envelope, ENVELOPE_SCHEMA, _ENVELOPE_KEYS)
    index = _record(sources.eligibility_index, INDEX_SCHEMA, _INDEX_KEYS)
    manifest = load_execution_manifest(inputs.manifest, now=now)
    eligibility = evaluate_inventory_eligibility(
        inputs.eligibility, inputs.inventory, evidence.handoff, evidence.country_attestation,
        inputs.manifest,
        expected_package_sha256=index["entries"][manifest.execution_id],
        expected_inventory_sha256=envelope["inventory_sha256"],
        envelope_id=binding.envelope_id, generation=binding.generation,
        country=envelope["country"], matching_rule_version=envelope["matching_rule_version"],
        now=now,
    )
    template_sha256 = hashlib.sha256(inputs.template).hexdigest()
    # S1 already binds the input country to the envelope; restate it here because
    # the attested country was just checked against the envelope.
    _require(inputs.country == envelope["country"]
             and template_sha256 in envelope["template_sha256s"]
             and eligibility.execution_id == manifest.execution_id)
    compiled = compile_reviewed_manifest(
        inputs.template, inputs.registry, evidence.snapshot,
        expected_template_sha256=template_sha256,
        expected_registry_sha256=envelope["registry_sha256"],
        expected_snapshot_sha256=eligibility.snapshot_sha256, now=now,
    )
    _require(type(compiled) is bytes and len(compiled) <= MAX_MANIFEST_BYTES
             and hmac.compare_digest(compiled, inputs.manifest))
    # Enforce authorization chronology independently: envelope issuance cannot
    # precede any evidence, attestation, snapshot or preparation timestamp it authorizes.
    issued = _utc(envelope["issued_at"])
    package = json.loads(inputs.eligibility.decode("utf-8"))
    target = {key: getattr(manifest, key) for key in _TARGET_KEYS}
    vacancy = bind_f05_vacancy(evidence.handoff, target,
                               expected_handoff_sha256=package["handoff_sha256"], now=now)
    attested_at = _utc(json.loads(evidence.country_attestation.decode("utf-8"))["attested_at"])
    snapshot_issued_at = _utc(json.loads(evidence.snapshot.decode("utf-8"))["issued_at"])
    _require(max(vacancy.observed_at, attested_at, snapshot_issued_at,
                 manifest.observed_at, manifest.prepared_at) <= issued)
    return AdmissionDecision(binding, manifest.execution_id, manifest.sha256)


def admit_standing_batch(sources: BatchApprovalSources, inputs: BatchInputs,
                         evidence: AdmissionEvidence, *, now: datetime) -> AdmissionDecision:
    """Admit one manifest only when the binding, eligibility and recompilation agree.

    The caller supplies independently read current sources and a trusted UTC
    clock and must call this again at every point of use; nothing is cached.
    """
    try:
        return _admit(sources, inputs, evidence, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del sources, inputs, evidence, now
    raise PermissionError(ERROR) from None
