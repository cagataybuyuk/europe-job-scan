"""Offline verification of the owner-pinned SmartRecruiters batch pilot."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path

from ejs.services.current_fact_approval import MAX_APPROVAL_BYTES, MAX_REGISTRY_BYTES
from ejs.services.execution_manifest import (
    MAX_CV_BYTES, MAX_MANIFEST_BYTES, POLICY_VERSION, ROLLOUT_CAPS, _HEX_64, _SEGMENT,
    _TARGET_KEYS,
    _exact_object, _opaque, _reject_constant, _strict_int, _unique_pairs, _utc,
    _validate_target, load_execution_manifest,
)
from ejs.services.private_source_files import _read_sources
from ejs.services.suitability import _COUNTRY_NAMES

ERROR = "STANDING_ENVELOPE_REVIEW_REQUIRED"
ENVELOPE_SCHEMA = "EJS-STANDING-ENVELOPE-1"
POINTER_SCHEMA = "EJS-STANDING-ENVELOPE-POINTER-1"
INDEX_SCHEMA = "EJS-BATCH-ELIGIBILITY-INDEX-1"
MAX_STANDING_TARGETS = 5

_APPROVAL_ROOT = Path("/Library/Application Support/EJS/approval")
_SOURCES = (
    ("current.json", MAX_APPROVAL_BYTES),
    ("envelope.json", MAX_MANIFEST_BYTES),
    ("eligibility-index.json", MAX_MANIFEST_BYTES),
)
_ATS = "smartrecruiters"
_DAY_TIMEZONE = "Europe/Istanbul"  # calendar day used by the existing policy ledger
_BOUNDED_STAGES = frozenset(stage for stage, cap in ROLLOUT_CAPS.items() if cap is not None)
_IDENTITY_KEYS = frozenset({"schema_version", "envelope_id", "generation"})
_POINTER_KEYS = _IDENTITY_KEYS | {"envelope_sha256", "eligibility_index_sha256"}
_INDEX_KEYS = _IDENTITY_KEYS | {"entries"}
_PINS = {
    "policy": MAX_MANIFEST_BYTES,
    "profile": MAX_MANIFEST_BYTES,
    "cv": MAX_CV_BYTES,
    "registry": MAX_REGISTRY_BYTES,
    "inventory": MAX_REGISTRY_BYTES,
}
_ENVELOPE_KEYS = _IDENTITY_KEYS | {
    "status", "issued_at", "expires_at", "policy_version", "candidate_profile_version",
    "ats_family", "country", "targets", "rollout_stages", "daily_cap", "daily_timezone",
    "matching_rule_version", "template_sha256s",
} | {f"{asset}_sha256" for asset in _PINS}


@dataclass(frozen=True, repr=False)
class BatchApprovalSources:
    pointer: bytes
    envelope: bytes
    eligibility_index: bytes


@dataclass(frozen=True, repr=False)
class BatchInputs:
    manifest: bytes
    policy: bytes
    profile: bytes
    template: bytes
    cv: bytes
    registry: bytes
    inventory: bytes
    eligibility: bytes
    country: str
    matching_rule_version: str


@dataclass(frozen=True, repr=False)
class BatchBinding:
    envelope_id: str
    generation: int
    envelope_sha256: str
    eligibility_index_sha256: str


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def read_trusted_batch_sources() -> BatchApprovalSources:
    """Read the three fixed production paths with the root-owned file policy."""
    try:
        paths = tuple((_APPROVAL_ROOT / name, limit) for name, limit in _SOURCES)
        return BatchApprovalSources(*_read_sources(paths, root_owned=True))
    except (OSError, ValueError, TypeError, AttributeError, NotImplementedError):
        pass
    raise PermissionError(ERROR) from None


def _sha256(raw: bytes, limit: int) -> str:
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> bool:
    return type(value) is str and bool(_HEX_64.fullmatch(value))


def _check_pin(pin: object, actual: str) -> None:
    _require(_digest(pin) and hmac.compare_digest(pin, actual))


def _record(raw: bytes, schema: str, keys: frozenset[str]) -> dict:
    # These schemas have no floating-point fields.
    doc = _exact_object(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                                   parse_constant=_reject_constant, parse_float=_reject_constant), keys)
    _require(doc["schema_version"] == schema and _opaque(doc["envelope_id"]))
    _strict_int(doc["generation"], 1, 2**63 - 1)
    return doc


def canonical_posting_identity(target: dict[str, str]) -> tuple[str, str, str]:
    """Return the canonical ATS tenant and posting identity for duplicate checks."""
    return target["ats_family"], target["company"].casefold(), target["posting_id"]


def _check_scope(envelope: dict, inputs: BatchInputs, now: datetime) -> str:
    _require(envelope["status"] == "active" and envelope["policy_version"] == POLICY_VERSION
             and envelope["ats_family"] == _ATS and envelope["daily_timezone"] == _DAY_TIMEZONE)
    country = envelope["country"]
    _require(type(country) is str and country in _COUNTRY_NAMES
             and type(inputs.country) is str and inputs.country == country)
    rule = envelope["matching_rule_version"]
    _require(type(rule) is str and _SEGMENT.fullmatch(rule)
             and type(inputs.matching_rule_version) is str and rule == inputs.matching_rule_version)
    cap = _strict_int(envelope["daily_cap"], 1, 1)
    stages = envelope["rollout_stages"]
    _require(type(stages) is list and stages and all(
        type(stage) is str and stage in _BOUNDED_STAGES for stage in stages
    ) and len(set(stages)) == len(stages))
    issued, expires = _utc(envelope["issued_at"]), _utc(envelope["expires_at"])
    _require(issued <= now < expires and expires - issued <= timedelta(hours=24))

    locations = envelope["targets"]
    _require(type(locations) is list and 0 < len(locations) <= MAX_STANDING_TARGETS)
    targets = set()
    postings = set()
    for row in locations:
        target = _validate_target(row)
        identity = tuple(sorted(target.items()))
        posting = canonical_posting_identity(target)
        _require(target["ats_family"] == _ATS and identity not in targets and posting not in postings)
        targets.add(identity)
        postings.add(posting)
    manifest = load_execution_manifest(inputs.manifest, now=now)
    _require(manifest.observed_at <= issued and manifest.prepared_at <= issued)
    _require(manifest.pass_kind == "submit" and manifest.ats_family == _ATS
             and manifest.rollout_stage in stages and manifest.daily_cap is not None
             and manifest.daily_cap <= cap and manifest.expires_at <= expires
             and manifest.candidate_profile_version == envelope["candidate_profile_version"])
    identity = tuple(sorted((key, getattr(manifest, key)) for key in _TARGET_KEYS))
    _require(identity in targets)
    _check_pin(manifest.cv_sha256, _sha256(inputs.cv, MAX_CV_BYTES))
    return manifest.execution_id


def _validate(sources: BatchApprovalSources, inputs: BatchInputs, now: datetime) -> BatchBinding:
    _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
    _sha256(sources.pointer, _SOURCES[0][1])
    pointer = _record(sources.pointer, POINTER_SCHEMA, _POINTER_KEYS)
    envelope_sha = _sha256(sources.envelope, _SOURCES[1][1])
    index_sha = _sha256(sources.eligibility_index, _SOURCES[2][1])
    _check_pin(pointer["envelope_sha256"], envelope_sha)
    _check_pin(pointer["eligibility_index_sha256"], index_sha)
    envelope = _record(sources.envelope, ENVELOPE_SCHEMA, _ENVELOPE_KEYS)
    index = _record(sources.eligibility_index, INDEX_SCHEMA, _INDEX_KEYS)
    for key in ("envelope_id", "generation"):
        _require(pointer[key] == envelope[key] == index[key])

    execution_id = _check_scope(envelope, inputs, now)
    for asset, limit in _PINS.items():
        actual = _sha256(getattr(inputs, asset), limit)
        _check_pin(envelope[f"{asset}_sha256"], actual)
    templates = envelope["template_sha256s"]
    _require(type(templates) is list and templates and all(map(_digest, templates))
             and len(set(templates)) == len(templates))
    _require(_sha256(inputs.template, MAX_MANIFEST_BYTES) in templates)
    entries = index["entries"]
    _require(type(entries) is dict and entries and all(
        _opaque(key) and _digest(pin) for key, pin in entries.items()
    ))
    _check_pin(entries.get(execution_id), _sha256(inputs.eligibility, MAX_REGISTRY_BYTES))
    return BatchBinding(pointer["envelope_id"], pointer["generation"], envelope_sha, index_sha)


def validate_standing_batch(sources: BatchApprovalSources, inputs: BatchInputs, *,
                            now: datetime) -> BatchBinding:
    """Check exact-byte bindings and the manifest's scope without I/O.

    The caller supplies independently read current sources and a trusted UTC
    clock. The country must come from verified vacancy evidence. Admission
    must still verify that evidence and recompile the manifest from its pins.
    """
    try:
        return _validate(sources, inputs, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del sources, inputs, now
    raise PermissionError(ERROR) from None
