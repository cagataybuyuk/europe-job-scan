"""Assemble a reviewable pilot in memory from one pinned F05 handoff."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json

from ejs.services.execution_manifest import POLICY_VERSION, _TARGET_KEYS, _utc
from ejs.services.inventory_eligibility import (
    MATCHING_RULE, _attested_country, _inventory, _pinned, draft_eligibility_package, recompute,
)
from ejs.services.standing_admission import AdmissionEvidence, admit_standing_batch
from ejs.services.standing_envelope import (
    ENVELOPE_SCHEMA, INDEX_SCHEMA, BatchApprovalSources, BatchInputs,
)
from ejs.services.standing_envelope_draft import draft_records
from ejs.services.standing_manifest_preparation import (
    ReviewedBytes, StandingManifestReview, prepare_standing_manifest,
)
from ejs.services.standing_pilot_pool import RankedEligibleTarget, select_pilot_pool

ERROR = "STANDING_PILOT_PREPARATION_REVIEW_REQUIRED"


@dataclass(frozen=True, repr=False)
class PilotCandidateReview:
    manifest: StandingManifestReview
    country_attestation: ReviewedBytes


@dataclass(frozen=True, repr=False)
class PilotReview:
    envelope_id: str
    generation: int
    issued_at: datetime
    expires_at: datetime
    policy: ReviewedBytes
    profile: ReviewedBytes
    inventory: ReviewedBytes
    candidates: tuple[PilotCandidateReview, ...]


@dataclass(frozen=True, repr=False)
class PreparedPackage:
    execution_id: str
    ranked_target: RankedEligibleTarget
    inputs: BatchInputs
    evidence: AdmissionEvidence
    rollout_stage: str


@dataclass(frozen=True, repr=False)
class PilotDraft:
    approval_draft: BatchApprovalSources
    packages: tuple[PreparedPackage, ...]


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _is_utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _prepare_packages(review: PilotReview, now: datetime) -> tuple[PreparedPackage, ...]:
    first = review.candidates[0].manifest
    _pinned(review.policy.raw, review.policy.sha256)
    _pinned(review.profile.raw, review.profile.sha256)
    _pinned(review.inventory.raw, review.inventory.sha256)
    seen_execution_ids = set()
    packages = []
    for candidate in review.candidates:
        source = candidate.manifest
        attestation = candidate.country_attestation
        _require(source.handoff.sha256 == first.handoff.sha256
                 and source.registry.sha256 == first.registry.sha256 and source.cv == first.cv)
        prepared = prepare_standing_manifest(source, now=now)
        manifest, vacancy = prepared.manifest, prepared.vacancy
        _require(manifest.execution_id not in seen_execution_ids)
        seen_execution_ids.add(manifest.execution_id)
        target = {key: getattr(manifest, key) for key in _TARGET_KEYS}
        _pinned(attestation.raw, attestation.sha256)
        country = _attested_country(attestation.raw, manifest, source.handoff.sha256,
                                    vacancy.observed_at, now)
        attested_at = _utc(json.loads(attestation.raw.decode("utf-8"))["attested_at"])
        snapshot_issued_at = _utc(json.loads(source.snapshot.raw.decode("utf-8"))["issued_at"])
        _require(max(vacancy.observed_at, attested_at, snapshot_issued_at,
                     manifest.observed_at, manifest.prepared_at) <= review.issued_at)
        inventory, _ = _inventory(review.inventory.raw, manifest.candidate_profile_version, now)
        if any(recompute(line, inventory)[0] != "met" for line in vacancy.mandatory_lines):
            continue
        eligibility = draft_eligibility_package(
            review.inventory.raw, source.handoff.raw, attestation.raw, prepared.raw,
            source.snapshot.raw, expected_inventory_sha256=review.inventory.sha256,
            envelope_id=review.envelope_id, generation=review.generation, now=now,
        )
        inputs = BatchInputs(prepared.raw, review.policy.raw, review.profile.raw, source.template.raw,
                             source.cv, source.registry.raw, review.inventory.raw, eligibility,
                             country, MATCHING_RULE)
        evidence = AdmissionEvidence(source.snapshot.raw, source.handoff.raw, attestation.raw)
        packages.append(PreparedPackage(manifest.execution_id,
                                        RankedEligibleTarget(vacancy.handoff_rank, country, target),
                                        inputs, evidence, manifest.rollout_stage))
    return tuple(packages)


def _prepare(review: PilotReview, now: datetime) -> PilotDraft | None:
    _require(_is_utc(now) and _is_utc(review.issued_at) and _is_utc(review.expires_at))
    _require(review.issued_at <= now < review.expires_at
             and review.expires_at - review.issued_at <= timedelta(hours=24))
    if not review.candidates:
        return None
    packages = _prepare_packages(review, now)
    pool = select_pilot_pool(tuple(package.ranked_target for package in packages))
    if pool is None:
        return None
    by_posting = {(item.ranked_target.target["company"], item.ranked_target.target["posting_id"]): item
                  for item in packages
                  if item.ranked_target.attested_country == pool.country}
    selected = tuple(by_posting[target["company"], target["posting_id"]] for target in pool.targets)
    first = selected[0].inputs
    profile_version = json.loads(first.manifest)["candidate_profile_version"]
    identity = dict(envelope_id=review.envelope_id, generation=review.generation)
    envelope = dict(schema_version=ENVELOPE_SCHEMA, **identity, status="active",
                    issued_at=review.issued_at.isoformat(), expires_at=review.expires_at.isoformat(),
                    policy_version=POLICY_VERSION, candidate_profile_version=profile_version,
                    ats_family="smartrecruiters", country=pool.country, targets=pool.targets,
                    rollout_stages=sorted({item.rollout_stage for item in selected}), daily_cap=1,
                    daily_timezone="Europe/Istanbul", matching_rule_version=MATCHING_RULE,
                    template_sha256s=sorted({_digest(item.inputs.template) for item in selected}),
                    policy_sha256=review.policy.sha256, profile_sha256=review.profile.sha256,
                    cv_sha256=_digest(first.cv), registry_sha256=_digest(first.registry),
                    inventory_sha256=review.inventory.sha256)
    index = dict(schema_version=INDEX_SCHEMA, **identity,
                 entries={item.execution_id: _digest(item.inputs.eligibility) for item in selected})
    envelope_raw, index_raw, pointer_raw = draft_records(_encode(envelope), _encode(index))
    approval = BatchApprovalSources(pointer_raw, envelope_raw, index_raw)
    for item in selected:
        admit_standing_batch(approval, item.inputs, item.evidence, now=now)
    return PilotDraft(approval, selected)


def prepare_pilot_draft(review: PilotReview, *, now: datetime) -> PilotDraft | None:
    """Prepare all draft bytes or refuse. A verified non-met vacancy is excluded.

    Each input is reviewed independently. The result has no publication or queue
    capability. The owner reviews the exact employer evidence, country and final
    draft bytes before any separately authorized publication.
    """
    try:
        return _prepare(review, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del review, now
    raise PermissionError(ERROR) from None
