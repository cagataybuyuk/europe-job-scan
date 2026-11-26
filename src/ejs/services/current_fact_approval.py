"""Offline current-approval gate for existing EJS fact registry inputs.

An authenticated independent review channel must supply the *current* pin,
generation and profile version on every use. This module cannot authenticate
that channel or discover revocations. It never reads files or grants authority.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re

from ejs.services.execution_manifest import MAX_MANIFEST_BYTES, POLICY_VERSION

ERROR = "FACT_SOURCE_REVIEW_REQUIRED"
SCHEMA = "EJS-CURRENT-FACT-APPROVAL-1"
MAX_APPROVAL_BYTES = 16_384
MAX_REGISTRY_BYTES = 1_000_000
MAX_SNAPSHOT_BYTES = 500_000
MAX_APPROVAL_AGE = timedelta(hours=24)
_HEX = re.compile(r"[a-f0-9]{64}")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)")
_KEYS = frozenset({"schema_version", "generation", "status", "profile_version",
                   "policy_version", "issued_at", "expires_at",
                   "template_sha256", "registry_sha256", "snapshot_sha256"})


@dataclass(frozen=True, repr=False)
class FactInputPins:
    """Three exact-byte pins for offline compiler; not an execution capability."""
    template_sha256: str
    registry_sha256: str
    snapshot_sha256: str


def _require(condition):
    if not condition:
        raise PermissionError(ERROR)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_):
    raise PermissionError(ERROR)


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _generation(value):
    return type(value) is int and 1 <= value <= 2**63 - 1


def _stamp(value):
    _require(type(value) is str and _UTC.fullmatch(value))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _pinned(raw, pin, limit):
    _require(type(raw) is bytes and 0 < len(raw) <= limit and _digest(pin))
    _require(hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin))


def resolve_current_fact_inputs(
    approval_raw: bytes, template_raw: bytes, registry_raw: bytes,
    snapshot_raw: bytes, *,
    expected_approval_sha256: str, expected_generation: int,
    expected_profile_version: str, now: datetime,
) -> FactInputPins:
    """Return compiler input pins only after current approval verification.

    Caller obligations: obtain all expected values independently of the
    manifest writer, do not cache them, use a trusted current clock, and
    reject unavailable/unverified current state. A valid older record is
    rejected when checked against the current generation and pin.
    Revoked records are never accepted. All three supplied input byte sequences
    must match the approved digests; no parsing of their private contents
    occurs here. Their schemas and values still require compiler validation.
    """
    try:
        _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
        _require(_generation(expected_generation))
        _require(type(expected_profile_version) is str
                 and _PROFILE.fullmatch(expected_profile_version))
        _pinned(approval_raw, expected_approval_sha256, MAX_APPROVAL_BYTES)
        approval = json.loads(approval_raw.decode("utf-8"),
                              object_pairs_hook=_pairs, parse_constant=_constant)
        _require(type(approval) is dict and approval.keys() == _KEYS)
        _require(approval["schema_version"] == SCHEMA
                 and _generation(approval["generation"])
                 and approval["generation"] == expected_generation
                 and approval["profile_version"] == expected_profile_version
                 and approval["status"] == "active"
                 and approval["policy_version"] == POLICY_VERSION)
        issued = _stamp(approval["issued_at"])
        expires = _stamp(approval["expires_at"])
        _require(issued <= now < expires and expires - issued <= MAX_APPROVAL_AGE)
        template_pin = approval["template_sha256"]
        registry_pin = approval["registry_sha256"]
        snapshot_pin = approval["snapshot_sha256"]
        _pinned(template_raw, template_pin, MAX_MANIFEST_BYTES)
        _pinned(registry_raw, registry_pin, MAX_REGISTRY_BYTES)
        _pinned(snapshot_raw, snapshot_pin, MAX_SNAPSHOT_BYTES)
        result = FactInputPins(template_pin, registry_pin, snapshot_pin)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    else:
        return result
    # Do not retain private payloads or digests in the public error frame.
    del approval_raw, template_raw, registry_raw, snapshot_raw
    del expected_approval_sha256, expected_generation, expected_profile_version, now
    # The parsed record and extracted pins may exist after a late failure.
    approval = template_pin = registry_pin = snapshot_pin = None
    raise PermissionError(ERROR) from None
