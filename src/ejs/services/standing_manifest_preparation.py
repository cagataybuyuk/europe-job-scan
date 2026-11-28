"""Tie a reviewed submit template to the exact vacancy, observed form and CV bytes."""

from dataclasses import dataclass
from datetime import datetime
import hashlib

from ejs.services.execution_manifest import (
    MAX_MANIFEST_BYTES, ExecutionManifest, _TARGET_KEYS, _exact_object, _step, _utc,
    form_fingerprint, load_execution_manifest,
)
from ejs.services.inventory_eligibility import BoundVacancy, bind_f05_vacancy
from ejs.services.reviewed_manifest_compiler import _decode, compile_reviewed_manifest

ERROR = "STANDING_MANIFEST_PREPARATION_REVIEW_REQUIRED"
OBSERVATION_SCHEMA = "EJS-REVIEWED-FORM-OBSERVATION-1"
_OBSERVATION_KEYS = frozenset({"schema_version", "target", "source_detail_sha256", "observed_at",
                              "final_page_observed", "fields", "cv_upload"})
_CONTROL_KEYS = frozenset({"key", "label", "control", "required", "options"})
_UPLOAD_KEYS = frozenset({"key", "label", "required"})
_OPTIONAL_STEP = frozenset({"step"})


@dataclass(frozen=True, repr=False)
class ReviewedBytes:
    raw: bytes
    sha256: str


@dataclass(frozen=True, repr=False)
class StandingManifestReview:
    handoff: ReviewedBytes
    observation: ReviewedBytes
    template: ReviewedBytes
    registry: ReviewedBytes
    snapshot: ReviewedBytes
    cv: bytes


@dataclass(frozen=True, repr=False)
class PreparedStandingManifest:
    raw: bytes
    manifest: ExecutionManifest
    vacancy: BoundVacancy


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _prepare(review: StandingManifestReview, now: datetime) -> PreparedStandingManifest:
    raw = compile_reviewed_manifest(
        review.template.raw, review.registry.raw, review.snapshot.raw,
        expected_template_sha256=review.template.sha256,
        expected_registry_sha256=review.registry.sha256,
        expected_snapshot_sha256=review.snapshot.sha256, now=now,
    )
    manifest = load_execution_manifest(raw, now=now)
    _require(manifest.pass_kind == "submit")
    target = {key: getattr(manifest, key) for key in _TARGET_KEYS}
    vacancy = bind_f05_vacancy(review.handoff.raw, target,
                               expected_handoff_sha256=review.handoff.sha256, now=now)
    obs = _exact_object(_decode(review.observation.raw, MAX_MANIFEST_BYTES, review.observation.sha256),
                        _OBSERVATION_KEYS)
    _require(obs["schema_version"] == OBSERVATION_SCHEMA and obs["final_page_observed"] is True
             and obs["target"] == target and obs["source_detail_sha256"] == vacancy.detail_sha256
             and vacancy.observed_at <= _utc(obs["observed_at"]) == manifest.observed_at <= now)
    fields = obs["fields"]
    _require(type(fields) is list and len(fields) == len(manifest.fields))
    for field in fields:
        _step(_exact_object(field, _CONTROL_KEYS, _OPTIONAL_STEP))
    upload = _exact_object(obs["cv_upload"], _UPLOAD_KEYS, _OPTIONAL_STEP)
    _step(upload)
    _require(form_fingerprint(fields, upload) == manifest.form_fingerprint)
    cv = review.cv
    _require(type(cv) is bytes and len(cv) == manifest.cv_size_bytes and cv.startswith(b"%PDF-")
             and hashlib.sha256(cv).hexdigest() == manifest.cv_sha256)
    return PreparedStandingManifest(raw, manifest, vacancy)


def prepare_standing_manifest(review: StandingManifestReview, *, now: datetime) -> PreparedStandingManifest:
    """Compile reviewed inputs and compare an independently pinned, complete form observation.

    The observation records structure only. Its producer or reviewer verifies that
    all pages and the single required CV control were observed for this exact target.
    This offline function does not read files, observe a browser, or grant authority.
    """
    try:
        return _prepare(review, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del review, now
    raise PermissionError(ERROR) from None
