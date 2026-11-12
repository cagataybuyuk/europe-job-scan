"""Externally pinned single-target operation grant, distinct from TEST metadata.

The caller authenticates the user decision and reviewer outside these bytes.
Absent an independent exact decision reference, this validator always stops.
It performs no file, database, browser, network or application operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from weakref import WeakKeyDictionary

from ejs.services.oneclick_target import OneClickTarget, native_entry_policy_enabled


ERROR = "APPLICATION_EXECUTION_SCOPE_REVIEW_REQUIRED"
EXPECTED_FUTURE_DECISION_REF = "USER-DEVOTEAM-REF00063-LIVE-20261112"
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_HEX40 = re.compile(r"[a-f0-9]{40}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}\Z")
_TARGET_KEYS = frozenset({"company", "company_id", "posting_id", "publication_id",
                          "requisition_id", "posting_url"})
_SNAPSHOT_KEYS = frozenset({"status", "submit_key_digest", "execution_digest",
    "requisition_digest", "validation_digest", "requested_at", "started_at",
    "confirmed_at", "evidence_digest"})
_GRANT_KEYS = frozenset({"schema_version", "scope_id", "user_decision_ref", "operations",
    "source_sha", "manifest_sha256", "facts_sha256", "target", "candidate_profile_version",
    "execution_id", "cv_sha256", "reviewer_ref", "reviewed_at", "expires_at",
    "legacy_snapshot_sha256"})
_OPERATIONS = {"form_write": True, "cv_upload": True, "next": True,
               "final_submit": True, "canonical_scope_migration": True}
_ISSUED: WeakKeyDictionary = WeakKeyDictionary()


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _utc(value) -> datetime:
    if type(value) is str:
        _require(len(value) <= 40)
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(isinstance(value, datetime) and value.tzinfo is not None
             and value.utcoffset() == timedelta(0))
    return value.astimezone(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(items):
    data = {}
    for key, value in items:
        _require(key not in data)
        data[key] = value
    return data


def _invalid(_value):
    raise PermissionError(ERROR)


def _pinned(payload, pin):
    _require(type(payload) is bytes and 0 < len(payload) <= 1_000_000
             and type(pin) is str and _HEX64.fullmatch(pin) is not None
             and hmac.compare_digest(hashlib.sha256(payload).hexdigest(), pin))
    result = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_invalid)
    _require(type(result) is dict)
    return result


def legacy_snapshot_fingerprint(snapshot: dict) -> str:
    """Bind every public field of the unchanged Confirmed first-ledger snapshot."""
    try:
        _require(type(snapshot) is dict and set(snapshot) == _SNAPSHOT_KEYS
                 and snapshot["status"] == "Confirmed")
        for key in ("submit_key_digest", "execution_digest", "requisition_digest",
                    "validation_digest", "evidence_digest"):
            _require(type(snapshot[key]) is str and _HEX64.fullmatch(snapshot[key]) is not None)
        _require(snapshot["requisition_digest"] == _hash("REF7089X"))
        _require(_utc(snapshot["requested_at"]) <= _utc(snapshot["started_at"])
                 <= _utc(snapshot["confirmed_at"]))
        return _hash(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False))
    except Exception:
        raise PermissionError(ERROR) from None


@dataclass(frozen=True, eq=False)
class ReviewedExecutionScope:
    """In-memory reviewed grant; an ordinary constructed object is not issued."""

    scope_id: str
    grant_sha256: str
    user_decision_ref: str
    source_sha: str
    manifest_sha256: str
    facts_sha256: str
    target: OneClickTarget
    candidate_profile_version: str
    execution_id: str
    cv_sha256: str
    reviewer_ref: str
    reviewed_at: datetime
    expires_at: datetime
    legacy_snapshot_sha256: str


def _seal(scope: ReviewedExecutionScope) -> tuple:
    # Snapshot primitive target values, not the caller's mutable object identity.
    # Even a frozen dataclass can be changed through object.__setattr__.
    return tuple(tuple(getattr(scope.target, key) for key in sorted(_TARGET_KEYS))
                 if name == "target" else getattr(scope, name)
                 for name in scope.__dataclass_fields__)


def require_issued_execution_scope(scope: ReviewedExecutionScope, *, now: datetime | None = None,
                                    require_current: bool = True) -> None:
    """Check the exact issued in-memory object, never a boolean claiming approval."""
    try:
        _require(type(scope) is ReviewedExecutionScope and _ISSUED.get(scope) == _seal(scope))
        if require_current:
            _require(now is not None and scope.reviewed_at <= _utc(now) < scope.expires_at)
    except Exception:
        raise PermissionError(ERROR) from None


def review_execution_scope(grant_payload: bytes, manifest_payload: bytes,
                            facts_payload: bytes, *, grant_sha256: str,
                            manifest_sha256: str, facts_sha256: str, source_sha: str,
                            expected_decision_ref: str | None = None,
                            expected_target: OneClickTarget,
                            legacy_snapshot: dict, now: datetime) -> ReviewedExecutionScope:
    """Review ONE future exact Devoteam grant; no default grant exists.

    expected_decision_ref and expected_target must come from independent trusted
    user/endpoint review, not these files, model output or vacancy content. The
    backend company ID remains an independent prerequisite. Existing private
    input compilation, CV bytes, suitability and endpoint review still apply.
    """
    try:
        now = _utc(now)
        _require(type(expected_decision_ref) is str
                 and expected_decision_ref == EXPECTED_FUTURE_DECISION_REF
                 and type(expected_target) is OneClickTarget)
        # This engineering implementation cannot open another target/scope.
        _require((expected_target.company, expected_target.posting_id,
                  expected_target.publication_id, expected_target.requisition_id,
                  expected_target.posting_url) == (
            "Devoteam", "744000156831399", "3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e", "REF00063",
            "https://jobs.smartrecruiters.com/Devoteam/744000156831399-digital-impulse-business-analyst-"))
        _require(type(source_sha) is str and _HEX40.fullmatch(source_sha) is not None)
        grant = _pinned(grant_payload, grant_sha256)
        manifest = _pinned(manifest_payload, manifest_sha256)
        facts = _pinned(facts_payload, facts_sha256)
        _require(set(grant) == _GRANT_KEYS
                 and grant["schema_version"] == "EJS-REVIEWED-EXECUTION-SCOPE-1"
                 and grant["user_decision_ref"] == expected_decision_ref
                 and type(grant["operations"]) is dict and set(grant["operations"]) == set(_OPERATIONS)
                 and all(grant["operations"][key] is True for key in _OPERATIONS)
                 and type(grant["scope_id"]) is str and _OPAQUE.fullmatch(grant["scope_id"]) is not None
                 and grant["source_sha"] == source_sha == manifest.get("source_sha")
                 and grant["manifest_sha256"] == manifest_sha256
                 and grant["facts_sha256"] == facts_sha256
                 and manifest.get("schema_version") == "EJS-SINGLE-APPLICATION-1"
                 and facts.get("schema_version") == "EJS-VERIFIED-FACTS-1"
                 and native_entry_policy_enabled(manifest))
        _require(type(grant["target"]) is dict and set(grant["target"]) == _TARGET_KEYS
                 and grant["target"] == manifest.get("target")
                 and OneClickTarget(**grant["target"]) == expected_target)
        profile, execution, reviewer = (grant["candidate_profile_version"], grant["execution_id"], grant["reviewer_ref"])
        _require(all(type(v) is str and _OPAQUE.fullmatch(v) is not None for v in (profile, execution, reviewer))
                 and profile == manifest.get("candidate_profile_version") == facts.get("candidate_profile_version")
                 and execution == manifest.get("execution_id") and reviewer == manifest.get("reviewer_ref"))
        cv = manifest.get("cv")
        _require(type(cv) is dict and type(grant["cv_sha256"]) is str
                 and _HEX64.fullmatch(grant["cv_sha256"]) is not None
                 and grant["cv_sha256"] == cv.get("sha256") and cv.get("user_approved") is True)
        reviewed, expires = _utc(grant["reviewed_at"]), _utc(grant["expires_at"])
        manifest_reviewed = _utc(manifest.get("reviewed_at"))
        _require(manifest_reviewed <= reviewed <= now < expires
                 and now - manifest_reviewed <= timedelta(hours=24)
                 and timedelta(0) < expires - reviewed <= timedelta(hours=24))
        fingerprint = legacy_snapshot_fingerprint(legacy_snapshot)
        _require(type(grant["legacy_snapshot_sha256"]) is str
                 and hmac.compare_digest(grant["legacy_snapshot_sha256"], fingerprint))
        scope = ReviewedExecutionScope(scope_id=grant["scope_id"], grant_sha256=grant_sha256,
            user_decision_ref=expected_decision_ref, source_sha=source_sha,
            manifest_sha256=manifest_sha256, facts_sha256=facts_sha256, target=OneClickTarget(**grant["target"]),
            candidate_profile_version=profile, execution_id=execution, cv_sha256=grant["cv_sha256"],
            reviewer_ref=reviewer, reviewed_at=reviewed, expires_at=expires,
            legacy_snapshot_sha256=fingerprint)
        _ISSUED[scope] = _seal(scope)
        return scope
    except Exception:
        raise PermissionError(ERROR) from None
