"""Pinned metadata for a different, reviewed TEST target; no execution grant.

The trusted caller supplies current canonical history. This validator does not
authenticate a reviewer/history source, lock history, open files or create a
ledger. Existing input/compiler, vacancy/CV and runtime gates remain required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re

from ejs.services.oneclick_target import OneClickTarget, native_entry_policy_enabled


_ERROR = "APPLICATION_NEXT_TEST_SCOPE_REVIEW_REQUIRED"
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_HEX40 = re.compile(r"[a-f0-9]{40}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}\Z")
_TARGET_KEYS = frozenset({"company", "company_id", "posting_id", "publication_id",
                          "requisition_id", "posting_url"})
_SCOPE_KEYS = frozenset({"schema_version", "scope_id", "decision_ref", "mode",
                         "source_sha", "manifest_sha256", "facts_sha256",
                         "history_sha256", "target", "candidate_profile_version",
                         "cv_sha256", "reviewer_ref", "reviewed_at", "expires_at"})
_CONSUMED = frozenset({"Validated", "Submitted / Unconfirmed", "Confirmed"})
_LEGACY_TARGET = {
    "company": "Version1", "company_id": "593fe2a3e4b0ac9d00fb37f8",
    "posting_id": "744000159638089",
    "publication_id": "aaf6587c-8336-4454-adb1-e09661d9bdfd",
    "requisition_id": "REF7089X",
    "posting_url": "https://jobs.smartrecruiters.com/Version1/744000159638089--senior-business-analyst",
}


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError(_ERROR)


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise PermissionError(_ERROR)


def _pinned(payload: bytes, pin: str) -> dict:
    _require(type(payload) is bytes and 0 < len(payload) <= 1_000_000
             and type(pin) is str and _HEX64.fullmatch(pin) is not None)
    _require(hmac.compare_digest(hashlib.sha256(payload).hexdigest(), pin))
    value = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant)
    _require(type(value) is dict)
    return value


def _utc(value) -> datetime:
    _require(type(value) is str and len(value) <= 40)
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0))
    return stamp.astimezone(timezone.utc)


def _target(value) -> OneClickTarget:
    _require(type(value) is dict and set(value) == _TARGET_KEYS)
    return OneClickTarget(**value)


def _same_vacancy(left: OneClickTarget, right: OneClickTarget) -> bool:
    # A renamed slug/profile/execution cannot reopen the same vacancy. Database
    # company identity is used for requisitions even if the public slug changes.
    return (left.posting_id == right.posting_id
            or left.publication_id == right.publication_id
            or (left.company_id == right.company_id
                and left.requisition_id.casefold() == right.requisition_id.casefold()))


def history_fingerprint(history: list[dict], legacy_snapshot: dict) -> str:
    """Canonical metadata digest, not authentication or a concurrency lock."""
    try:
        _require(type(history) is list and 1 <= len(history) <= 10_000
                 and type(legacy_snapshot) is dict)
        raw = json.dumps({"history": history, "legacy_snapshot": legacy_snapshot},
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        _require(len(raw) <= 1_000_000)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        raise PermissionError(_ERROR) from None


@dataclass(frozen=True)
class ReviewedNextTestScope:
    """Accepted inspection metadata; none of these fields is a runtime grant."""

    scope_id: str
    target: OneClickTarget
    source_sha: str
    manifest_sha256: str
    facts_sha256: str
    history_sha256: str
    reviewed_at: datetime
    expires_at: datetime
    browser_action_authorized: bool = field(default=False, init=False)
    form_write_authorized: bool = field(default=False, init=False)
    cv_upload_authorized: bool = field(default=False, init=False)
    next_authorized: bool = field(default=False, init=False)
    final_submit_authorized: bool = field(default=False, init=False)
    ledger_create_authorized: bool = field(default=False, init=False)


def review_next_test_scope(scope_payload: bytes, manifest_payload: bytes,
                           facts_payload: bytes, *, scope_sha256: str,
                           manifest_sha256: str, facts_sha256: str, source_sha: str,
                           history: list[dict], legacy_snapshot: dict,
                           now: datetime) -> ReviewedNextTestScope:
    """Validate a pinned different-target TEST record without granting actions.

    Pins come from the independent trusted reviewer. Before any later operation,
    the caller must separately load/validate private application inputs, verify
    current canonical history and obtain that operation's explicit authority.
    This acceptance cannot be used as an authorization to create a new ledger.
    """
    try:
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() == timedelta(0)
                 and type(source_sha) is str and _HEX40.fullmatch(source_sha) is not None)
        scope = _pinned(scope_payload, scope_sha256)
        manifest = _pinned(manifest_payload, manifest_sha256)
        facts = _pinned(facts_payload, facts_sha256)
        _require(set(scope) == _SCOPE_KEYS
                 and scope["schema_version"] == "EJS-REVIEWED-NEXT-TEST-SCOPE-1"
                 and scope["decision_ref"] == "USER-MVP-TEST-TARGET-20261112"
                 and scope["mode"] == "review_only"
                 and type(scope["scope_id"]) is str and _OPAQUE.fullmatch(scope["scope_id"]) is not None
                 and scope["source_sha"] == source_sha == manifest.get("source_sha")
                 and scope["manifest_sha256"] == manifest_sha256
                 and scope["facts_sha256"] == facts_sha256
                 and manifest.get("schema_version") == "EJS-SINGLE-APPLICATION-1"
                 and facts.get("schema_version") == "EJS-VERIFIED-FACTS-1")
        _require(native_entry_policy_enabled(manifest))
        target = _target(scope["target"])
        _require(target == _target(manifest.get("target")))
        profile = scope["candidate_profile_version"]
        _require(type(profile) is str and _OPAQUE.fullmatch(profile) is not None
                 and profile == manifest.get("candidate_profile_version") == facts.get("candidate_profile_version"))
        cv = manifest.get("cv")
        _require(type(cv) is dict and type(scope["cv_sha256"]) is str
                 and _HEX64.fullmatch(scope["cv_sha256"]) is not None
                 and scope["cv_sha256"] == cv.get("sha256") and cv.get("user_approved") is True)
        reviewer = scope["reviewer_ref"]
        _require(type(reviewer) is str and _OPAQUE.fullmatch(reviewer) is not None
                 and reviewer == manifest.get("reviewer_ref"))
        reviewed, expires = _utc(scope["reviewed_at"]), _utc(scope["expires_at"])
        manifest_reviewed = _utc(manifest.get("reviewed_at"))
        _require(manifest_reviewed <= reviewed <= now < expires
                 and now - manifest_reviewed <= timedelta(hours=24)
                 and timedelta(0) < expires - reviewed <= timedelta(hours=24))
        fingerprint = history_fingerprint(history, legacy_snapshot)
        _require(type(scope["history_sha256"]) is str and _HEX64.fullmatch(scope["history_sha256"]) is not None
                 and hmac.compare_digest(scope["history_sha256"], fingerprint))
        _require(legacy_snapshot.get("status") == "Confirmed"
                 and legacy_snapshot.get("requisition_digest") == hashlib.sha256(b"REF7089X").hexdigest())
        legacy = _target(_LEGACY_TARGET)
        legacy_seen = False
        for row in history:
            _require(type(row) is dict and set(row) == {"target", "submission_state"}
                     and row["submission_state"] in _CONSUMED)
            previous = _target(row["target"])
            if previous == legacy:
                _require(row["submission_state"] == "Confirmed" and not legacy_seen)
                legacy_seen = True
            _require(not _same_vacancy(target, previous))
        _require(legacy_seen)
        return ReviewedNextTestScope(scope_id=scope["scope_id"], target=target,
            source_sha=source_sha, manifest_sha256=manifest_sha256,
            facts_sha256=facts_sha256, history_sha256=fingerprint,
            reviewed_at=reviewed, expires_at=expires)
    except Exception:
        raise PermissionError(_ERROR) from None
