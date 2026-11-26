"""W3.6 offline-only binding check; no runtime integration or authority grant.

Caller must obtain the expected snapshot SHA-256 independently from the
trusted local review source, never from the manifest or snapshot itself.
This module does not establish trust in that source or read files.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re

from ejs.services.execution_manifest import FACT_KEYS, POLICY_VERSION, load_execution_manifest

ERROR = "FACT_SNAPSHOT_REVIEW_REQUIRED"
_MAX_BYTES = 500_000
_HEX = re.compile(r"[a-f0-9]{64}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)")
_TOP = frozenset({"schema_version", "profile_version", "cv_sha256", "policy",
                  "observed_at", "issued_at", "expires_at", "target", "form_fingerprint", "facts"})
_ROW = frozenset({"key", "canonical_field", "fact_key", "step", "control",
                  "options", "value", "provenance"})


def _require(condition):
    if not condition:
        raise PermissionError(ERROR)


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise PermissionError(ERROR)


def _stamp(value):
    _require(type(value) is str and _UTC.fullmatch(value))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _shape(value, keys):
    _require(type(value) is dict and value.keys() == keys)


def verify_fact_snapshot(manifest_raw: bytes, snapshot_raw: bytes, *,
                         expected_snapshot_sha256: str, now: datetime) -> None:
    """Validate only independently reviewed Verified Fact bindings.

    A successful return is not an execution grant or evidence/policy approval.
    The existing manifest validator remains responsible for non-fact modes.
    No candidate values, supplied digests or source references appear in errors.
    """
    try:
        _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
        _require(type(snapshot_raw) is bytes and 0 < len(snapshot_raw) <= _MAX_BYTES)
        _require(type(expected_snapshot_sha256) is str
                 and _HEX.fullmatch(expected_snapshot_sha256))
        _require(hmac.compare_digest(hashlib.sha256(snapshot_raw).hexdigest(),
                                     expected_snapshot_sha256))
        manifest = load_execution_manifest(manifest_raw, now=now)
        snapshot = json.loads(snapshot_raw.decode("utf-8"),
                              object_pairs_hook=_pairs, parse_constant=_constant)
        _shape(snapshot, _TOP)
        _require(snapshot["schema_version"] == "EJS-TRUSTED-FACT-SNAPSHOT-1")
        _require(snapshot["policy"] == POLICY_VERSION)
        _require(snapshot["profile_version"] == manifest.candidate_profile_version)
        _require(snapshot["cv_sha256"] == manifest.cv_sha256)
        _require(snapshot["form_fingerprint"] == manifest.form_fingerprint)
        target = {"ats_family": manifest.ats_family, "company": manifest.company,
                  "posting_id": manifest.posting_id, "requisition_id": manifest.requisition_id,
                  "opportunity_key": manifest.opportunity_key, "apply_url": manifest.apply_url}
        _require(snapshot["target"] == target)
        _require(snapshot["observed_at"] == manifest.observed_at.isoformat())
        issued, expires = _stamp(snapshot["issued_at"]), _stamp(snapshot["expires_at"])
        _require(manifest.observed_at <= issued <= now < expires
                 and expires - issued <= timedelta(hours=24))
        _require(type(snapshot["facts"]) is list)
        fields = {f.key: f for f in manifest.fields if f.answer.mode == "Verified Fact"}
        _require(bool(fields) and len(snapshot["facts"]) == len(fields))
        for row in snapshot["facts"]:
            _shape(row, _ROW)
            _require(type(row["key"]) is str and row["key"] in fields)
            field = fields.pop(row["key"])
            fact_key = FACT_KEYS[field.canonical_field]
            _require(row["canonical_field"] == field.canonical_field
                     and row["fact_key"] == fact_key
                     and field.answer.source_ref == f"user_fact:{fact_key}"
                     and type(row["step"]) is int and row["step"] == field.step
                     and row["control"] == field.control
                     and row["options"] == (list(field.options) if field.options is not None else None)
                     and type(row["value"]) is type(field.answer.value)
                     and row["value"] == field.answer.value)
            provenance = row["provenance"]
            _require(type(provenance) is str and 0 < len(provenance) <= 512
                     and provenance == provenance.strip()
                     and provenance.isprintable())
        _require(not fields)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        # JSON errors can retain the entire private input in the exception object.
        pass
    else:
        return
    raise PermissionError(ERROR) from None
